"""
MARC record handling shared by every application.

Reading a file into the shape the screens want, writing a conversion back onto
a record, and lining a converted statement up with the 866 it came from. None of
it depends on Flask, so it is testable on its own.

Each of these existed twice, once in converter/app.py and once in
workbench/app.py, and the copies had drifted -- not in what they did, but in
what they said. The workbench's copy of remove_converted_866s() carried a
one-line docstring where the converter's carried the account of the 0.5.2 data
loss and the warning never to build `sources` from get_fields("866"). Losing the
reasoning is how the reasoning stops being followed.
"""

from __future__ import annotations

import io
from typing import Optional

from pymarc import MARCReader, MARCWriter


def read_marc_file(fileobj) -> list[dict]:
    """
    Read a MARC file and extract records with their 866 fields.

    Returns a list of record dicts for the UI.
    """
    records_out = []
    reader = MARCReader(fileobj, to_unicode=True, force_utf8=True,
                        utf8_handling="replace")
    for rec_idx, record in enumerate(reader):
        title_field = record.get("245")
        title = ""
        if title_field:
            title = title_field.get_subfields("a", "b")
            title = " ".join(title).strip().rstrip(" /:")

        issn_field = record.get("022")
        issn = issn_field["a"] if issn_field and issn_field["a"] else ""

        holdings_loc = record.get("852")
        location = ""
        if holdings_loc:
            loc_parts = holdings_loc.get_subfields("b", "c")
            location = " > ".join(loc_parts)

        fields_866 = []
        for f in record.get_fields("866"):
            subfield_a = f.get("a") or ""
            subfield_z = f.get("z") or ""
            fields_866.append({
                "ind1": f.indicator1,
                "ind2": f.indicator2,
                "a": subfield_a,
                "z": subfield_z,
                "display": f"866 {f.indicator1}{f.indicator2} $a {subfield_a}"
                           + (f" $z {subfield_z}" if subfield_z else ""),
            })

        records_out.append({
            "index": rec_idx,
            "title": title or f"Record {rec_idx + 1}",
            "issn": issn,
            "location": location,
            "fields_866": fields_866,
            "has_853": bool(record.get_fields("853")),
            "has_863": bool(record.get_fields("863")),
        })

    return records_out


def records_from_bytes(data: bytes) -> list:
    """Every pymarc Record in `data`, in file order."""
    reader = MARCReader(io.BytesIO(data), to_unicode=True,
                        force_utf8=True, utf8_handling="replace")
    return list(reader)


def records_to_bytes(records: list) -> bytes:
    """Serialise a list of pymarc Records to MARC binary."""
    buf = io.BytesIO()
    writer = MARCWriter(buf)
    for rec in records:
        writer.write(rec)
    # close_fh defaults to True, which closes the BytesIO and makes the
    # getvalue() below raise "I/O operation on closed file".
    writer.close(close_fh=False)
    return buf.getvalue()


def add_853(record, field_data) -> None:
    """
    Add a regenerated 853, replacing any existing one with the same $8.

    A regenerated 853 supersedes the field it was built from — leaving both in
    place would give the record two patterns sharing one linking number, so the
    863s would be ambiguous.
    """
    link = next((sf.value for sf in field_data.subfields if sf.code == "8"), None)
    if link is not None:
        for old in list(record.get_fields("853")):
            if (old.get("8") or "").strip() == str(link).strip():
                record.remove_field(old)
    record.add_field(field_data.to_pymarc())


def apply_record_conversion(record, rc) -> None:
    """
    Write a RecordConversion onto a pymarc record.

    863s already sitting under a link number we are about to write are dropped
    first: they describe the same holdings from an earlier run, so replacing
    them keeps re-conversion idempotent instead of accumulating duplicates.
    """
    links = set(rc.links_written)
    for old in list(record.get_fields("863")):
        if (old.get("8") or "").split(".")[0].strip() in links:
            record.remove_field(old)
    for f853 in rc.fields_853:
        add_853(record, f853)           # replaces any 853 sharing its $8
    for f863 in rc.fields_863:
        record.add_field(f863.to_pymarc())


def match_866_sources(record, texts) -> list:
    """
    Line each statement up with the 866 field it came from.

    Returns a list the same length as `texts`, holding either the matching field
    or None.  Each field is claimed at most once, so a record carrying the same
    statement twice maps to two distinct fields rather than the first one twice.

    Used by the single-statement route, where the text arrives from the client
    and may have been edited in the UI.  An edited statement matches nothing and
    yields None, which remove_converted_866s() then leaves alone: a field we
    cannot account for is never deleted.
    """
    claimed: list = []
    matched: list = []
    for text in texts:
        wanted = (text or "").strip()
        found = None
        for field in record.get_fields("866"):
            if any(field is c for c in claimed):
                continue
            if (field["a"] or "").strip() == wanted:
                found = field
                claimed.append(field)
                break
        matched.append(found)
    return matched


def remove_converted_866s(record, sources, rc) -> None:
    """
    Drop only those 866s whose statement actually produced 863s.

    Stripping used to be decided for the whole record, so a statement the parser
    could not read had its 866 removed alongside its converted neighbours and
    left nothing behind -- the holdings were simply gone, and the response still
    reported success.  Each field is now judged on its own result.

    `sources` is the 866 fields that were handed to convert_record(), in the
    same order as rc.results, and may contain None for a statement with no field
    to match.  Callers must build it themselves rather than reusing
    get_fields("866"): statements with an empty $a are filtered out before
    conversion, so the two lists are not otherwise aligned.  An 866 with nothing
    in $a is consequently never stripped, which is right -- it carried nothing
    to convert.
    """
    for field, result in zip(sources, rc.results):
        if field is not None and result.fields_863:
            record.remove_field(field)


def display_marc_field(fld) -> str:
    """
    Render a pymarc field the way FieldData.display() renders a generated one,
    so an existing 853 and a generated one look identical in the UI.
    """
    ind = f"{fld.indicator1}{fld.indicator2}".replace(" ", "#")
    # Values in real records often carry padding; strip it so an existing field
    # and a generated one render identically rather than with doubled spaces.
    sfs = " ".join(f"${sf.code} {(sf.value or '').strip()}" for sf in fld.subfields)
    return f"{fld.tag} {ind} {sfs}"
