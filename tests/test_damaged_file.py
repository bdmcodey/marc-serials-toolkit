"""
A MARC file carrying a record pymarc cannot decode.

Real library exports contain them — a bad length in the leader, a truncated
directory — and pymarc's reader yields None for each one rather than raising.
Reading straight past that raised AttributeError on the next attribute access,
so a single damaged record made the whole file fail to upload with a 500 and
nothing to say which record was at fault.

The file is now refused, naming the positions. Converting it is not an option:
a record that could not be decoded cannot be written back out either, so the
download would return the cataloguer's file with that record silently missing.
That is the same rule the converter already applies to a statement it can only
partly read.
"""

from __future__ import annotations

import io

import pytest
from pymarc import Field, MARCWriter, Record, Subfield

import marc_serials.store as store
from conftest import upload_marc
from marc_serials.records import (read_marc_file, records_from_bytes,
                                  refuse_unreadable, unreadable_positions)


def _record(title: str, holdings: str) -> Record:
    rec = Record()
    rec.add_field(Field(tag="245", indicators=["0", "0"],
                        subfields=[Subfield(code="a", value=title)]))
    rec.add_field(Field(tag="866", indicators=["4", "1"],
                        subfields=[Subfield(code="a", value=holdings)]))
    return rec


def _marc_bytes(*records: Record) -> bytes:
    buf = io.BytesIO()
    writer = MARCWriter(buf)
    for rec in records:
        writer.write(rec)
    writer.close(close_fh=False)
    return buf.getvalue()


@pytest.fixture
def damaged_marc() -> bytes:
    """One undecodable record, then one good one."""
    good = _marc_bytes(_record("A journal", "v.1(1990)-v.5(1994)"))
    # Corrupt the record length in the leader.
    return good[:12] + b"9" + good[13:] + good


@pytest.fixture
def clean_marc() -> bytes:
    return _marc_bytes(_record("A journal", "v.1(1990)-v.5(1994)"),
                       _record("Another", "v.2(1991)"))


# ---------------------------------------------------------------------------
# The reader itself
# ---------------------------------------------------------------------------

def test_reading_a_damaged_file_does_not_raise(damaged_marc):
    """It used to raise AttributeError on the first record."""
    records = read_marc_file(io.BytesIO(damaged_marc))
    assert len(records) == 2


def test_the_damaged_record_keeps_its_position(damaged_marc):
    """
    Positions are what record_index is counted against, so a record that could
    not be read holds its place rather than shifting every record after it.
    """
    records = read_marc_file(io.BytesIO(damaged_marc))
    assert records[0]["unreadable"] is True
    assert records[1]["unreadable"] is False
    assert records[1]["title"] == "A journal"
    assert unreadable_positions(records) == [1]
    # records_from_bytes keeps the same positions, so the two agree.
    assert len(records_from_bytes(damaged_marc)) == len(records)


def test_a_clean_file_is_not_refused(clean_marc):
    records = read_marc_file(io.BytesIO(clean_marc))
    assert unreadable_positions(records) == []
    assert refuse_unreadable(records) is None


def test_the_refusal_names_the_position(damaged_marc):
    message = refuse_unreadable(read_marc_file(io.BytesIO(damaged_marc)))
    assert message is not None
    assert "position 1" in message
    assert "1 record that could not be read" in message


# ---------------------------------------------------------------------------
# Both applications refuse it, and store nothing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("client_name", ["client", "client"])
def test_the_upload_is_refused_rather_than_failing(request, client_name,
                                                   damaged_marc):
    client = request.getfixturevalue(client_name)
    response = upload_marc(client, damaged_marc)
    assert response.status_code == 400, "a damaged file used to give a 500"
    assert "could not be read" in response.get_json()["error"]


@pytest.mark.parametrize("client_name", ["client", "client"])
def test_a_refused_upload_stores_nothing(request, client_name, damaged_marc,
                                         clean_marc):
    """
    The cataloguer's working file must survive a refused upload. Storing first
    and reading afterwards would have replaced a good file with a bad one.
    """
    import os

    client = request.getfixturevalue(client_name)
    assert upload_marc(client, clean_marc).status_code == 200
    before = sorted(os.listdir(store.UPLOAD_DIR))

    assert upload_marc(client, damaged_marc).status_code == 400
    assert sorted(os.listdir(store.UPLOAD_DIR)) == before

    # And the good file is still the one that is loaded.
    body = client.post("/api/batch-convert", json={}).get_json()
    assert "error" not in body, body
