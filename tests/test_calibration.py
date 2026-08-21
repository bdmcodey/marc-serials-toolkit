"""
Exact counts measured against the private USC corpus. Opt-in.

These are *characterization* values: they record what the tools produced on two
known files at a known version, not what MARC 21 requires. A deliberate
improvement to the parser will move them, and the table should be updated with
the reason in the commit message. An accidental change is what the table exists
to catch -- output has already shifted materially between versions, and this is
the only check that would notice.

The files are real library holdings and are deliberately absent from the
repository. Point the suite at a mounted share to run these:

    MARC_TEST_DATA_DIR=/Volumes/rfolders/.../serials-enhancement \\
        python -m pytest -m calibration

Without that variable every test here skips, which is the normal case on any
machine but the maintainer's. The skips are left visible rather than deselected
by default: a "SKIPPED" line in the summary is documentation, whereas a silent
deselection is a trap.
"""

from __future__ import annotations

import io

import pytest
from pymarc import MARCReader

from conftest import upload_marc, private_marc, WELLFORMED_NAME, UNKEMPT_NAME
from holdings_parser import parse_866
from pattern_detector import detect_patterns

pytestmark = pytest.mark.calibration


# Recorded in HANDOFF.md, measured at version 0.5.0.
EXPECTED = {
    "wellformed": {
        "convention": "standard",
        "fields_853": 38,
        "fields_863": 114,
        "statements": 116,
        "parsed": 114,
    },
    "unkempt": {
        "convention": "house",
        "fields_853": 52,
        "fields_863": 166,
        "statements": 51,
        "parsed": 48,
    },
}

FILENAMES = {"wellformed": WELLFORMED_NAME, "unkempt": UNKEMPT_NAME}


@pytest.fixture(params=["wellformed", "unkempt"])
def corpus(request):
    """One private corpus, or a clean skip when the share is not mounted."""
    name = request.param
    path = private_marc(FILENAMES[name])
    if path is None:
        pytest.skip(f"set MARC_TEST_DATA_DIR to a directory containing "
                    f"{FILENAMES[name]} to run the calibration checks")
    return name, path.read_bytes()


def _field_totals(converter_client, data: bytes, convention: str) -> tuple[int, int]:
    upload_marc(converter_client, data)
    converter_client.post("/api/batch-convert", json={"convention": convention})
    converted = converter_client.get("/api/download-converted").data

    total_853 = total_863 = 0
    for record in MARCReader(io.BytesIO(converted)):
        if record is None:
            continue
        total_853 += len(record.get_fields("853"))
        total_863 += len(record.get_fields("863"))
    return total_853, total_863


def test_generated_field_counts(converter_client, corpus):
    name, data = corpus
    expected = EXPECTED[name]

    total_853, total_863 = _field_totals(converter_client, data, expected["convention"])
    assert (total_853, total_863) == (expected["fields_853"], expected["fields_863"])


def test_parse_rate(detector_client, corpus):
    """
    How many 866 statements the parser accepts. 0.3.0 took one file from 6% to
    94%, so this number moving is the single clearest signal that parsing
    behaviour changed.
    """
    name, data = corpus
    expected = EXPECTED[name]

    statements = upload_marc(detector_client, data).get_json()["statements"]
    assert len(statements) == expected["statements"]

    parsed = sum(1 for s in statements if parse_866(s).ranges)
    assert parsed == expected["parsed"]


def test_detector_matches_every_cluster_it_describes(detector_client, corpus):
    _, data = corpus
    statements = upload_marc(detector_client, data).get_json()["statements"]

    for group in detect_patterns(statements):
        if group.too_complex:
            continue
        assert group.match_rate == 1.0, group.human_label


def test_detector_regexes_stay_testable(detector_client, corpus):
    """
    The observation MAX_PATTERN_TOKENS was calibrated from: at 40 tokens the
    longest regex these files produced was 1,506 characters, inside the
    2,000-character limit /api/test-regex enforces.
    """
    _, data = corpus
    statements = upload_marc(detector_client, data).get_json()["statements"]

    longest = max((len(g.regex) for g in detect_patterns(statements)), default=0)
    assert longest <= 2000
