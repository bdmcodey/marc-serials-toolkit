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
    session,
)

try:
    import pymarc
    from pymarc import MARCReader, Record, Field, Subfield, MARCWriter
    HAS_PYMARC = True
except ImportError:
    HAS_PYMARC = False

from holdings_parser import parse_866
from marc_converter import (convert_holdings, ConversionResult, FREQUENCY_CODES,
                            CONVENTION_STANDARD, CONVENTION_HOUSE)


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


def _convention_opts(data: dict) -> dict:
    """
    Read the caption-convention choice from a request body.

    'house' reproduces the local practice in existing records (year in $a,
    chronology as text); 'standard' follows MARC 21.
    """
    conv = (data.get("convention") or CONVENTION_STANDARD).strip().lower()
    if conv not in (CONVENTION_STANDARD, CONVENTION_HOUSE):
        conv = CONVENTION_STANDARD
    return {"convention": conv, "chron_as_text": conv == CONVENTION_HOUSE}

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

@app.route("/")
def index():
    return render_template("index.html",
                           has_pymarc=HAS_PYMARC,
                           frequency_codes=FREQUENCY_CODES)


@app.route("/api/parse-text", methods=["POST"])
def api_parse_text():
    """Parse a single 866 $a text string and return structured data + preview."""
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text provided"}), 400

    captions = data.get("captions") or {}
    frequency = data.get("frequency", "")
    continuity = data.get("numbering_continuity", "r")
    linking = int(data.get("linking_number", 1))

    parse_result = parse_866(text)
    conversion = convert_holdings(
        parse_result,
        linking_number=linking,
        captions=captions or None,
        frequency=frequency,
        numbering_continuity=continuity,
        **_convention_opts(data),
    )

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

        # Capture the record's own 853 before any clearing: when it already
        # describes the data we conform to it instead of adding a second one.
        # Clearing is an explicit request to start over, so drop it in that case.
        existing_853 = next(iter(target.get_fields("853")), None)

        if data.get("clear_existing_853_863"):
            target.remove_fields("853", "863")
            existing_853 = None

        remove_866 = any(c.get("remove_866", True) for c in conversions_input)
        if remove_866:
            target.remove_fields("866")

        conv_opts = _convention_opts(data)
        previews = []
        for conv_spec in conversions_input:
            text = conv_spec.get("text", "")
            if not text:
                continue
            parse_result = parse_866(text)
            conversion = convert_holdings(
                parse_result,
                linking_number=int(conv_spec.get("linking_number", 1)),
                captions=conv_spec.get("captions") or None,
                frequency=conv_spec.get("frequency", ""),
                numbering_continuity=conv_spec.get("numbering_continuity", "r"),
                existing_853=existing_853,
                **conv_opts,
            )

            if conversion.field_853:
                _add_853(target, conversion.field_853)
            for f863 in conversion.fields_863:
                target.add_field(f863.to_pymarc())

            previews.append({
                "field_853": conversion.field_853.display() if conversion.field_853 else None,
                "fields_863": [f.display() for f in conversion.fields_863],
                "warnings": conversion.warnings,
                "conformed": conversion.conformed,
                "needs_review": conversion.needs_review,
            })

        # Save the full updated file back to disk
        updated_bytes = _records_to_bytes(all_records)
        _save_file("marc_file_converted", updated_bytes)

        return jsonify({"success": True, "previews": previews})

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

    conv_opts = _convention_opts(data)

    try:
        summary = []
        review_total = 0
        for rec_idx, record in enumerate(all_records):
            # Read the record's own 853 before clearing (see api_convert_record).
            existing_853 = next(iter(record.get_fields("853")), None)
            if clear_existing:
                record.remove_fields("853", "863")
                existing_853 = None

            fields_866 = record.get_fields("866")
            if not fields_866:
                continue

            rec_warnings = []
            converted = conformed = review = 0
            for link_num, f866 in enumerate(fields_866, start=1):
                text = f866["a"] or ""
                if not text:
                    continue
                parse_result = parse_866(text)
                conversion = convert_holdings(
                    parse_result,
                    linking_number=link_num,
                    frequency=frequency,
                    numbering_continuity=continuity,
                    existing_853=existing_853,
                    **conv_opts,
                )
                if conversion.needs_review or not conversion.fields_863:
                    review += 1
                    rec_warnings.extend(conversion.warnings)
                    continue
                if conversion.field_853:
                    _add_853(record, conversion.field_853)
                for f863 in conversion.fields_863:
                    record.add_field(f863.to_pymarc())
                converted += 1
                conformed += 1 if conversion.conformed else 0
                rec_warnings.extend(conversion.warnings)

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
