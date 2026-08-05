"""
app.py
------
Flask web application for converting MARC 866 textual holdings to
structured 853 + 863 holdings fields.

Run:
    python app.py

Then open http://localhost:5000 in your browser.

Requirements:
    pip install flask pymarc
"""

from __future__ import annotations

import io
import os
import tempfile
import time
import uuid
from typing import Optional

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_file,
    send_from_directory,
    session,
)

try:
    import pymarc
    from pymarc import MARCReader, Record, Field, Subfield, MARCWriter
    HAS_PYMARC = True
except ImportError:
    HAS_PYMARC = False

from holdings_parser import parse_866
from marc_converter import (convert_holdings, convert_record, ConversionResult,
                            FREQUENCY_CODES, CONVENTION_STANDARD, CONVENTION_HOUSE,
                            CONVENTION_LEVELS, convention_presets,
                            resolve_convention)


def _add_853(record, field_data) -> None:
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


def _display_marc_field(fld) -> str:
    """
    Render a pymarc field the way FieldData.display() renders a generated one,
    so an existing 853 and a generated one look identical in the UI.
    """
    ind = f"{fld.indicator1}{fld.indicator2}".replace(" ", "#")
    # Values in real records often carry padding; strip it so an existing field
    # and a generated one render identically rather than with doubled spaces.
    sfs = " ".join(f"${sf.code} {(sf.value or '').strip()}" for sf in fld.subfields)
    return f"{fld.tag} {ind} {sfs}"


def _previews_from(rc, rejections=(), existing_853s=()) -> list:
    """
    One preview entry per statement, in 866 field order.

    `link` lets the client group statements that share an 853 without parsing
    $8 back out of the display string.  When a statement conforms to an 853
    already on the record, field_853 is None -- substitute that field's text so
    the group still has a heading to show.
    """
    by_link = {}
    for fld in existing_853s or ():
        by_link[(fld.get("8") or "").strip()] = _display_marc_field(fld)

    out = []
    for c in rc.results:
        link = str(c.linking_number)
        display = c.field_853.display() if c.field_853 else by_link.get(link)
        out.append({
            "field_853": display,
            "fields_863": [f.display() for f in c.fields_863],
            "warnings": c.warnings + list(rejections),
            "conformed": c.conformed,
            "needs_review": c.needs_review,
            "link": link,
            "existing": bool(c.conformed and display),
        })
    return out


def _apply_record_conversion(record, rc) -> None:
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
        _add_853(record, f853)          # replaces any 853 sharing its $8
    for f863 in rc.fields_863:
        record.add_field(f863.to_pymarc())


def _convention_opts(data: dict) -> tuple:
    """
    Build a caption-convention spec from a request body.

    The named preset ('standard' follows MARC 21; 'house' reproduces the local
    practice of year in $a with chronology as text) is only a starting point --
    per-level subfields, indicators and the chronology format may all be
    overridden.  Returns (kwargs for convert_holdings, rejection messages).
    """
    conv = (data.get("convention") or CONVENTION_STANDARD).strip().lower()

    subfields = data.get("subfields")
    if not isinstance(subfields, dict):
        subfields = None

    indicators = data.get("indicators")
    if not (isinstance(indicators, (list, tuple)) and len(indicators) == 2):
        indicators = None

    chron = data.get("chronology")
    chron_as_text = None
    if isinstance(chron, str) and chron.strip().lower() in ("text", "code"):
        chron_as_text = chron.strip().lower() == "text"

    spec, rejections = resolve_convention(
        conv, subfields=subfields, indicators=indicators, chron_as_text=chron_as_text
    )
    return {"convention_spec": spec}, rejections

# ---------------------------------------------------------------------------
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__,
            template_folder=os.path.join(_BASE_DIR, "templates"),
            static_folder=os.path.join(_BASE_DIR, "static"))
app.secret_key = os.environ.get("SECRET_KEY", "marc-holdings-dev-key")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB upload limit

# ---------------------------------------------------------------------------
# Server-side file storage
# Binary MARC files are stored on disk; only a small UUID is kept in the
# session cookie, staying well under Flask's 4 KB cookie limit.
# ---------------------------------------------------------------------------

UPLOAD_DIR = os.environ.get(
    "MARC_UPLOAD_DIR", os.path.join(tempfile.gettempdir(), "marc_uploads")
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Uploaded files are purged after this many seconds to bound disk usage.
UPLOAD_TTL_SECONDS = int(os.environ.get("MARC_UPLOAD_TTL", 6 * 3600))


def _purge_old_uploads() -> None:
    """Delete uploaded files older than UPLOAD_TTL_SECONDS."""
    now = time.time()
    try:
        for fname in os.listdir(UPLOAD_DIR):
            fpath = os.path.join(UPLOAD_DIR, fname)
            try:
                if now - os.path.getmtime(fpath) > UPLOAD_TTL_SECONDS:
                    os.remove(fpath)
            except OSError:
                pass
    except OSError:
        pass


def _file_path(file_id: str) -> str:
    """Absolute path for a stored file given its ID."""
    return os.path.join(UPLOAD_DIR, f"{file_id}.mrc")


def _save_file(session_key: str, data: bytes) -> None:
    """Write data to disk and record the file ID in the session."""
    _purge_old_uploads()
    file_id = session.get(session_key)
    if not isinstance(file_id, str) or len(file_id) != 32:
        file_id = uuid.uuid4().hex
    session[session_key] = file_id
    with open(_file_path(file_id), "wb") as fh:
        fh.write(data)


def _load_file(session_key: str) -> Optional[bytes]:
    """Read data from disk using the file ID stored in the session."""
    file_id = session.get(session_key)
    if not file_id:
        return None
    path = _file_path(file_id)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# MARC helpers
# ---------------------------------------------------------------------------

def _read_marc_file(fileobj) -> list[dict]:
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


def _records_to_bytes(records: list) -> bytes:
    """Serialise a list of pymarc Records to MARC binary."""
    buf = io.BytesIO()
    writer = MARCWriter(buf)
    for rec in records:
        writer.write(rec)
    # close_fh defaults to True, which closes the BytesIO and makes the
    # getvalue() below raise "I/O operation on closed file".
    writer.close(close_fh=False)
    return buf.getvalue()


def _load_all_records() -> Optional[list]:
    """Load the uploaded MARC file and return all pymarc Record objects."""
    marc_bytes = _load_file("marc_file")
    if not marc_bytes:
        return None
    all_records = []
    reader = MARCReader(
        io.BytesIO(marc_bytes), to_unicode=True,
        force_utf8=True, utf8_handling="replace"
    )
    for rec in reader:
        all_records.append(rec)
    return all_records


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/ui.css")
def ui_css():
    """
    Serve the stylesheet shared with the pattern detector.

    Templates link to it relatively, so it resolves both locally and behind
    nginx, whose trailing-slash proxy_pass strips the /converter prefix.
    """
    return send_from_directory(os.path.join(_BASE_DIR, os.pardir, "shared"),
                               "ui.css", mimetype="text/css")


@app.route("/")
def index():
    return render_template("index.html",
                           has_pymarc=HAS_PYMARC,
                           frequency_codes=FREQUENCY_CODES,
                           convention_levels=CONVENTION_LEVELS,
                           convention_presets=convention_presets())


@app.route("/api/parse-text", methods=["POST"])
def api_parse_text():
    """
    Parse a single 866 $a text string and return structured data + preview.

    This previews one statement in isolation, so its $8 is always 1.1.  Applied
    numbering is decided per record by convert_record(), which shares an 853
    across statements with the same publication pattern.
    """
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text provided"}), 400

    captions = data.get("captions") or {}
    frequency = data.get("frequency", "")
    continuity = data.get("numbering_continuity", "r")
    linking = int(data.get("linking_number", 1))

    conv_opts, rejections = _convention_opts(data)
    parse_result = parse_866(text)
    conversion = convert_holdings(
        parse_result,
        linking_number=linking,
        captions=captions or None,
        frequency=frequency,
        numbering_continuity=continuity,
        **conv_opts,
    )
    conversion.warnings.extend(rejections)

    return jsonify({
        "parse": {
            "success": parse_result.success,
            "needs_review": parse_result.needs_review,
            "warnings": parse_result.warnings,
            "ranges": [
                {
                    "raw": r.raw,
                    "open_ended": r.open_ended,
                    "start": {
                        "vol": r.start.vol,
                        "issue": r.start.issue,
                        "part": r.start.part,
                        "year": r.start.year,
                        "month": r.start.month,
                    },
                    "end": {
                        "vol": r.end.vol if r.end else None,
                        "issue": r.end.issue if r.end else None,
                        "part": r.end.part if r.end else None,
                        "year": r.end.year if r.end else None,
                        "month": r.end.month if r.end else None,
                    } if r.end else None,
                }
                for r in parse_result.ranges
            ],
        },
        "conversion": conversion.to_dict(),
        "preview": {
            "field_853": conversion.field_853.display() if conversion.field_853 else None,
            "fields_863": [f.display() for f in conversion.fields_863],
        },
    })


@app.route("/api/upload-marc", methods=["POST"])
def api_upload_marc():
    if not HAS_PYMARC:
        return jsonify({"error": "pymarc is not installed on the server."}), 500

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400

    f = request.files["file"]
    if f.filename == "":
        return jsonify({"error": "Empty filename."}), 400

    # Scrub any stale/corrupt session values before touching the session
    for key in ("marc_file", "marc_file_converted"):
        val = session.get(key)
        if val is not None and not (isinstance(val, str) and len(val) == 32):
            session.pop(key, None)

    try:
        file_bytes = f.read()
        # Store on disk; only a UUID goes into the session cookie
        _save_file("marc_file", file_bytes)
        # Clear any previous converted version when a new file is uploaded
        session.pop("marc_file_converted", None)

        records = _read_marc_file(io.BytesIO(file_bytes))
        return jsonify({"records": records, "total": len(records)})
    except Exception as exc:
        app.logger.exception("Request failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/convert-record", methods=["POST"])
def api_convert_record():
    """
    Convert 866 fields in a specific record to 853/863.

    POST JSON: {
        "record_index": 0,
        "conversions": [
            {
                "text": "v.1:no.1(1990:Jan.)-v.5:no.4(1994:Dec.)",
                "captions": {...},
                "frequency": "q",
                "numbering_continuity": "r",
                "linking_number": 1,
                "remove_866": true
            }
        ],
        "clear_existing_853_863": false
    }
    """
    if not HAS_PYMARC:
        return jsonify({"error": "pymarc is not installed on the server."}), 500

    data = request.get_json(force=True)
    all_records = _load_all_records()
    if all_records is None:
        return jsonify({"error": "No MARC file found. Please upload a file first."}), 400

    record_index = int(data.get("record_index", 0))
    conversions_input = data.get("conversions", [])

    try:
        if record_index >= len(all_records):
            return jsonify({"error": "Record index out of range."}), 400

        target = all_records[record_index]

        # Capture the record's own 853s before any clearing: when one already
        # describes the data we conform to it instead of adding a second.
        # Clearing is an explicit request to start over, so drop them then.
        existing_853s = list(target.get_fields("853"))

        if data.get("clear_existing_853_863"):
            target.remove_fields("853", "863")
            existing_853s = []

        remove_866 = any(c.get("remove_866", True) for c in conversions_input)
        if remove_866:
            target.remove_fields("866")

        conv_opts, rejections = _convention_opts(data)
        specs = [c for c in conversions_input if c.get("text")]

        # Linking numbers are a record-level decision, so the whole record is
        # converted at once; any per-conversion linking_number is advisory.
        first = specs[0] if specs else {}
        rc = convert_record(
            [parse_866(c["text"]) for c in specs],
            existing_853s=existing_853s,
            captions=first.get("captions") or None,
            frequency=first.get("frequency", ""),
            numbering_continuity=first.get("numbering_continuity", "r"),
            **conv_opts,
        )
        _apply_record_conversion(target, rc)

        previews = _previews_from(rc, rejections)

        # Save the full updated file back to disk
        updated_bytes = _records_to_bytes(all_records)
        _save_file("marc_file_converted", updated_bytes)

        return jsonify({"success": True, "previews": previews})

    except Exception as exc:
        app.logger.exception("Request failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/preview-record", methods=["POST"])
def api_preview_record():
    """
    Preview the conversion of one whole record without writing anything.

    Linking numbers are a record-level property, so a per-statement preview
    cannot show them correctly -- it has no idea which siblings share its
    publication pattern.  This converts the entire record and returns one
    preview per 866, in field order, so the caller can render any single block
    with the $8 it would actually receive.

    POST JSON: {"record_index": 0, "convention": ..., "captions": {...}, ...}
    """
    if not HAS_PYMARC:
        return jsonify({"error": "pymarc is not installed on the server."}), 500

    data = request.get_json(force=True)
    all_records = _load_all_records()
    if all_records is None:
        return jsonify({"error": "No MARC file found. Please upload a file first."}), 400

    record_index = int(data.get("record_index", 0))
    if record_index >= len(all_records):
        return jsonify({"error": "Record index out of range."}), 400

    try:
        record = all_records[record_index]
        conv_opts, rejections = _convention_opts(data)

        existing_853s = list(record.get_fields("853"))
        texts = [(f["a"] or "") for f in record.get_fields("866")]
        statements = [t for t in texts if t]
        rc = convert_record(
            [parse_866(t) for t in statements],
            existing_853s=existing_853s,
            captions=data.get("captions") or None,
            frequency=data.get("frequency", ""),
            numbering_continuity=data.get("numbering_continuity", "r"),
            **conv_opts,
        )
        # Deliberately no _apply_record_conversion and no _save_file: preview
        # must leave the uploaded file untouched.
        previews = _previews_from(rc, rejections, existing_853s)
        # Pair each preview with the 866 it came from so the UI can show them
        # side by side without re-deriving the order.
        for pv, text in zip(previews, statements):
            pv["source_866"] = text

        return jsonify({
            "success": True,
            "record_index": record_index,
            "previews": previews,
        })

    except Exception as exc:
        app.logger.exception("Request failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/batch-convert", methods=["POST"])
def api_batch_convert():
    """Convert ALL records' 866 fields using supplied default settings."""
    if not HAS_PYMARC:
        return jsonify({"error": "pymarc is not installed."}), 500

    all_records = _load_all_records()
    if all_records is None:
        return jsonify({"error": "No MARC file found. Please upload a file first."}), 400

    data = request.get_json(force=True)
    frequency = data.get("frequency", "")
    continuity = data.get("numbering_continuity", "r")
    remove_866 = data.get("remove_866", True)
    clear_existing = data.get("clear_existing_853_863", False)

    conv_opts, rejections = _convention_opts(data)
    captions = data.get("captions") or None

    try:
        summary = []
        review_total = 0
        for rec_idx, record in enumerate(all_records):
            # Read the record's own 853s before clearing (see api_convert_record).
            existing_853s = list(record.get_fields("853"))
            if clear_existing:
                record.remove_fields("853", "863")
                existing_853s = []

            fields_866 = record.get_fields("866")
            if not fields_866:
                continue

            texts = [f["a"] or "" for f in fields_866]
            rc = convert_record(
                [parse_866(t) for t in texts if t],
                existing_853s=existing_853s,
                captions=captions,
                frequency=frequency,
                numbering_continuity=continuity,
                **conv_opts,
            )
            _apply_record_conversion(record, rc)

            rec_warnings = rc.warnings
            converted, conformed, review = rc.converted, rc.conformed, rc.needs_review

            # Only strip the source 866s that were actually converted; a
            # statement held back for review must keep its original data.
            if remove_866 and review == 0:
                record.remove_fields("866")

            review_total += review
            summary.append({
                "index": rec_idx,
                "converted_fields": converted,
                "conformed_fields": conformed,
                "needs_review": review,
                "warnings": rec_warnings,
            })

        updated_bytes = _records_to_bytes(all_records)
        _save_file("marc_file_converted", updated_bytes)

        return jsonify({
            "success": True,
            "records_processed": len(summary),
            "needs_review": review_total,
            "rejections": rejections,
            "summary": summary,
        })

    except Exception as exc:
        app.logger.exception("Request failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/download-converted", methods=["GET"])
def api_download_converted():
    """Download the converted MARC binary file."""
    marc_bytes = _load_file("marc_file_converted") or _load_file("marc_file")
    if not marc_bytes:
        return "No converted file available.", 404

    return send_file(
        io.BytesIO(marc_bytes),
        mimetype="application/marc",
        as_attachment=True,
        download_name="holdings_converted.mrc",
    )


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=5000)
