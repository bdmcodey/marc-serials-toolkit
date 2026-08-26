"""
app.py
------
Holdings Workbench — the pattern detector and the converter as one tool.

Upload a MARC file once, detect the patterns in its 866 statements, confirm what
each captured value means, and convert with those patterns applied.  A statement
no confirmed pattern matches is converted by holdings_parser.parse_866() exactly
as the standalone converter converts it, so nothing the converter can do today
is lost here.

Run:
    WORKBENCH_PORT=5003 python app.py

The two standalone apps are untouched and keep running on their own ports; this
one imports their engines rather than copying them.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import time
import uuid
from typing import Optional

# The engines live in the two standalone apps' directories and import each other
# by bare name ("from holdings_parser import parse_866"), so those directories
# have to be importable before anything below can load.  tests/conftest.py does
# the same thing for the same reason.  Prepended so a same-named module
# elsewhere on the path cannot shadow ours.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_BASE_DIR)
for _engine_dir in ("converter", "pattern-detector"):
    _path = os.path.join(_REPO_ROOT, _engine_dir)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from flask import (Flask, jsonify, render_template, request, send_file,
                   send_from_directory, session)

try:
    from pymarc import MARCReader, MARCWriter
    HAS_PYMARC = True
except ImportError:
    HAS_PYMARC = False

from holdings_parser import parse_866
from marc_converter import (CONVENTION_LEVELS, CONVENTION_STANDARD,
                            FREQUENCY_CODES, convention_presets,
                            convert_holdings, convert_record, resolve_convention)
from pattern_detector import detect_patterns, split_multi_range

import pattern_library as plib
from pattern_bridge import (ENCODABLE_LEVELS, LEVEL_IGNORE, LEVEL_LABELS,
                            LEVEL_UNRESOLVED, PARSER_SOURCE, apply_patterns,
                            build_parse_result, infer_roles)

# ---------------------------------------------------------------------------

app = Flask(__name__,
            template_folder=os.path.join(_BASE_DIR, "templates"),
            static_folder=os.path.join(_BASE_DIR, "static"))
app.secret_key = os.environ.get("SECRET_KEY", "marc-workbench-dev-key")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024   # 25 MB

UPLOAD_DIR = os.environ.get(
    "MARC_UPLOAD_DIR", os.path.join(tempfile.gettempdir(), "marc_uploads")
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

UPLOAD_TTL_SECONDS = int(os.environ.get("MARC_UPLOAD_TTL", 6 * 3600))

# Bounds on user-supplied text, matching the pattern detector's: a regex the
# cataloguer edited runs against statements the cataloguer uploaded, so both
# sides are capped to limit catastrophic-backtracking exposure.
MAX_STATEMENT_CHARS = 500
MAX_STATEMENTS = 5000
MAX_TEST_STATEMENTS = 2000
SAMPLE_LIMIT = 8


# ---------------------------------------------------------------------------
# Server-side storage.  Binary MARC and the pattern library are held on disk;
# only a UUID per kind goes into the session cookie, which Flask caps at 4 KB --
# a single generated regex can run to a quarter of that.
# ---------------------------------------------------------------------------

def _purge_old_uploads() -> None:
    """Delete stored files older than UPLOAD_TTL_SECONDS."""
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


def _file_path(file_id: str, ext: str = ".mrc") -> str:
    return os.path.join(UPLOAD_DIR, f"{file_id}{ext}")


def _save_file(session_key: str, data: bytes, ext: str = ".mrc") -> None:
    _purge_old_uploads()
    file_id = session.get(session_key)
    if not isinstance(file_id, str) or len(file_id) != 32:
        file_id = uuid.uuid4().hex
    session[session_key] = file_id
    with open(_file_path(file_id, ext), "wb") as fh:
        fh.write(data)


def _load_file(session_key: str, ext: str = ".mrc") -> Optional[bytes]:
    file_id = session.get(session_key)
    if not file_id:
        return None
    path = _file_path(file_id, ext)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# The pattern library, for this session
# ---------------------------------------------------------------------------

def _load_library() -> list:
    """The confirmed patterns for this session, in the order they are tried."""
    raw = _load_file("pattern_library", ".json")
    if not raw:
        return []
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        app.logger.warning("Stored pattern library was unreadable; ignoring it.")
        return []
    patterns, _ = plib.from_export(document)
    return patterns


def _save_library(patterns) -> None:
    payload = json.dumps(plib.to_export(patterns), indent=2).encode("utf-8")
    _save_file("pattern_library", payload, ".json")


# ---------------------------------------------------------------------------
# MARC helpers.  These mirror converter/app.py; the standalone converter is
# deliberately left untouched, so the glue is repeated rather than imported --
# importing its app.py would execute a second Flask application at import time.
# ---------------------------------------------------------------------------

def _add_853(record, field_data) -> None:
    """Add a regenerated 853, replacing any existing one with the same $8."""
    link = next((sf.value for sf in field_data.subfields if sf.code == "8"), None)
    if link is not None:
        for old in list(record.get_fields("853")):
            if (old.get("8") or "").strip() == str(link).strip():
                record.remove_field(old)
    record.add_field(field_data.to_pymarc())


def _display_marc_field(fld) -> str:
    """Render an existing field the way a generated one renders."""
    ind = f"{fld.indicator1}{fld.indicator2}".replace(" ", "#")
    sfs = " ".join(f"${sf.code} {(sf.value or '').strip()}" for sf in fld.subfields)
    return f"{fld.tag} {ind} {sfs}"


def _apply_record_conversion(record, rc) -> None:
    """Write a RecordConversion onto a record, replacing superseded 863s."""
    links = set(rc.links_written)
    for old in list(record.get_fields("863")):
        if (old.get("8") or "").split(".")[0].strip() in links:
            record.remove_field(old)
    for f853 in rc.fields_853:
        _add_853(record, f853)
    for f863 in rc.fields_863:
        record.add_field(f863.to_pymarc())


def _match_866_sources(record, texts) -> list:
    """Line each statement up with the 866 field it came from, claiming each once."""
    claimed, matched = [], []
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


def _remove_converted_866s(record, sources, rc) -> None:
    """Drop only those 866s whose statement actually produced 863s."""
    for field, result in zip(sources, rc.results):
        if field is not None and result.fields_863:
            record.remove_field(field)


def _convention_opts(data: dict) -> tuple:
    """Build a caption-convention spec from a request body."""
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


def _read_marc_file(fileobj) -> list[dict]:
    """Read a MARC file into the record summaries the record list renders."""
    records_out = []
    reader = MARCReader(fileobj, to_unicode=True, force_utf8=True,
                        utf8_handling="replace")
    for rec_idx, record in enumerate(reader):
        if record is None:
            continue
        title_field = record.get("245")
        title = ""
        if title_field:
            title = " ".join(title_field.get_subfields("a", "b")).strip().rstrip(" /:")

        issn_field = record.get("022")
        issn = issn_field["a"] if issn_field and issn_field["a"] else ""

        holdings_loc = record.get("852")
        location = " > ".join(holdings_loc.get_subfields("b", "c")) if holdings_loc else ""

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
    buf = io.BytesIO()
    writer = MARCWriter(buf)
    for rec in records:
        writer.write(rec)
    writer.close(close_fh=False)
    return buf.getvalue()


def _load_all_records() -> Optional[list]:
    marc_bytes = _load_file("marc_file")
    if not marc_bytes:
        return None
    reader = MARCReader(io.BytesIO(marc_bytes), to_unicode=True,
                        force_utf8=True, utf8_handling="replace")
    return [rec for rec in reader if rec is not None]


# ---------------------------------------------------------------------------
# Conversion, with the library applied
# ---------------------------------------------------------------------------

def _parse_all(texts, patterns) -> tuple[list, list]:
    """
    Parse every statement, preferring a confirmed pattern and falling back to
    the standard parser.  Returns (parse_results, sources) in step with `texts`.
    """
    parsed, sources = [], []
    for text in texts:
        result, source = apply_patterns(text, patterns)
        parsed.append(result)
        sources.append(source)
    return parsed, sources


def _source_labels(patterns) -> dict:
    labels = {p.id: p.label for p in patterns}
    labels[PARSER_SOURCE] = "Standard parser"
    return labels


def _previews_from(rc, rejections=(), existing_853s=(), sources=(),
                   patterns=()) -> list:
    """
    One preview entry per statement, in 866 field order, annotated with whatever
    read it -- a confirmed pattern by name, or the standard parser.
    """
    by_link = {}
    for fld in existing_853s or ():
        by_link[(fld.get("8") or "").strip()] = _display_marc_field(fld)

    labels = _source_labels(patterns)
    out = []
    for idx, c in enumerate(rc.results):
        link = str(c.linking_number)
        display = c.field_853.display() if c.field_853 else by_link.get(link)
        source = sources[idx] if idx < len(sources) else PARSER_SOURCE
        out.append({
            "field_853": display,
            "fields_863": [f.display() for f in c.fields_863],
            "warnings": c.warnings + list(rejections),
            "conformed": c.conformed,
            "needs_review": c.needs_review,
            "link": link,
            "existing": bool(c.conformed and display),
            "source": source,
            "source_label": labels.get(source, "Standard parser"),
            "from_pattern": source != PARSER_SOURCE,
        })
    return out


# ---------------------------------------------------------------------------
# Pattern annotation for the confirmation screen
# ---------------------------------------------------------------------------

def _sample_values(regex: str, statements, roles) -> dict:
    """
    What each capture group actually catches, across a few example statements.

    The confirmation screen is only meaningful if the cataloguer can see the
    values a role is about to be assigned to -- "1990" and "1994" under two
    groups both called end_year is the whole reason the screen exists.
    """
    try:
        compiled = re.compile(regex, re.IGNORECASE)
    except re.error:
        return {}

    samples: dict = {r.group: [] for r in roles}
    for statement in list(statements)[:SAMPLE_LIMIT]:
        s = (statement or "").strip()[:MAX_STATEMENT_CHARS]
        m = compiled.fullmatch(s) or compiled.search(s)
        if not m:
            continue
        for name, value in m.groupdict().items():
            if name in samples and value and value not in samples[name]:
                samples[name].append(value)
    return samples


def _annotate_group(group_dict: dict) -> dict:
    """Add suggested roles and sample values to a detected pattern group."""
    named = group_dict.get("named_groups") or []
    roles = infer_roles(named)
    group_dict["suggested_roles"] = [r.to_dict() for r in roles]
    group_dict["sample_values"] = _sample_values(
        group_dict.get("regex") or "", group_dict.get("examples") or [], roles
    )
    group_dict["needs_decision"] = any(r.level == LEVEL_UNRESOLVED for r in roles)
    return group_dict


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _load_about() -> dict:
    """Version and changelog, shared with the two standalone tools."""
    path = os.path.join(_REPO_ROOT, "shared", "about.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        app.logger.warning("Could not read shared/about.json", exc_info=True)
        return {}


@app.route("/ui.css")
def ui_css():
    """Serve the stylesheet shared with the two standalone tools."""
    return send_from_directory(os.path.join(_REPO_ROOT, "shared"),
                               "ui.css", mimetype="text/css")


@app.route("/")
def index():
    return render_template(
        "tool.html",
        has_pymarc=HAS_PYMARC,
        frequency_codes=FREQUENCY_CODES,
        convention_levels=CONVENTION_LEVELS,
        convention_presets=convention_presets(),
        level_labels={lvl: LEVEL_LABELS[lvl] for lvl in ENCODABLE_LEVELS},
        ignore_level=LEVEL_IGNORE,
        ignore_label=LEVEL_LABELS[LEVEL_IGNORE],
        unresolved_level=LEVEL_UNRESOLVED,
        about=_load_about(),
    )


@app.route("/api/upload-marc", methods=["POST"])
def api_upload_marc():
    """
    Take the MARC file once and serve both halves of the tool from it.

    Returns the record list the converter side needs *and* the 866 statements
    the detector side needs, so the cataloguer never uploads the same file to
    two tools again.
    """
    if not HAS_PYMARC:
        return jsonify({"error": "pymarc is not installed on the server."}), 500
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename."}), 400

    for key in ("marc_file", "marc_file_converted"):
        val = session.get(key)
        if val is not None and not (isinstance(val, str) and len(val) == 32):
            session.pop(key, None)

    try:
        file_bytes = f.read()
        _save_file("marc_file", file_bytes)
        session.pop("marc_file_converted", None)

        records = _read_marc_file(io.BytesIO(file_bytes))
        statements = [
            fld["a"].strip()
            for rec in records for fld in rec["fields_866"] if (fld["a"] or "").strip()
        ]
        return jsonify({
            "records": records,
            "total": len(records),
            "statements": statements,
            "count": len(statements),
        })
    except Exception as exc:
        app.logger.exception("Request failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/detect", methods=["POST"])
def api_detect():
    """
    Cluster statements by structure and return a regex per cluster, each with
    the roles to offer the cataloguer and the values those roles would take.

    Statements come from the request, or from the uploaded file when none are
    given -- the file is already on the server, so the client need not resend it.
    """
    data = request.get_json(force=True) or {}
    raw = data.get("statements")
    do_split = bool(data.get("split_multi_range", True))

    if not raw:
        stored = _load_file("marc_file")
        records = _read_marc_file(io.BytesIO(stored)) if stored else []
        raw = [fld["a"].strip() for rec in records
               for fld in rec["fields_866"] if (fld["a"] or "").strip()]

    if not raw:
        return jsonify({"error": "No statements provided."}), 400

    statements: list[str] = []
    for s in raw:
        if do_split:
            statements.extend(split_multi_range(s))
        elif s.strip():
            statements.append(s.strip())

    statements = [s[:MAX_STATEMENT_CHARS] for s in statements if s.strip()]
    if not statements:
        return jsonify({"error": "All statements were empty after processing."}), 400
    statements = statements[:MAX_STATEMENTS]

    try:
        groups = [_annotate_group(g.to_dict()) for g in detect_patterns(statements)]
        return jsonify({
            "total_statements": len(statements),
            "total_patterns": len(groups),
            "split_multi_range": do_split,
            "groups": groups,
        })
    except Exception as exc:
        app.logger.exception("Request failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/test-regex", methods=["POST"])
def api_test_regex():
    """
    Test a possibly-edited regex against statements, and re-offer roles for it.

    Editing the expression can add or remove capture groups, so the roles come
    back too -- decisions already made are kept, anything new needs deciding.
    """
    data = request.get_json(force=True) or {}
    regex_str = data.get("regex", "")
    statements = data.get("statements", [])

    if not regex_str:
        return jsonify({"error": "No regex provided."}), 400
    if len(regex_str) > plib.MAX_REGEX_CHARS:
        return jsonify({
            "error": f"Regex exceeds the {plib.MAX_REGEX_CHARS:,}-character test limit.",
        }), 400

    statements = [str(s)[:MAX_STATEMENT_CHARS] for s in statements[:MAX_TEST_STATEMENTS]]

    try:
        compiled = re.compile(regex_str, re.IGNORECASE)
    except re.error as exc:
        return jsonify({"error": f"Invalid regex: {exc}"}), 400

    results = []
    for s in statements:
        s = s.strip()
        fm = compiled.fullmatch(s)
        m = fm or compiled.search(s)
        results.append({
            "statement": s,
            "matched": m is not None,
            "full_match": fm is not None,
            "groups": m.groupdict() if m else {},
            "span": list(m.span()) if m else None,
        })

    matched_n = sum(1 for r in results if r["matched"])
    names = sorted(compiled.groupindex, key=lambda n: compiled.groupindex[n])
    prior = [plib.GroupRole.from_dict(r) for r in (data.get("roles") or [])
             if isinstance(r, dict)]
    roles = plib.merge_roles(names, prior) if prior else infer_roles(names)

    return jsonify({
        "results": results,
        "matched": matched_n,
        "failed": len(results) - matched_n,
        "match_rate": matched_n / len(results) if results else 1.0,
        "roles": [r.to_dict() for r in roles],
        "sample_values": _sample_values(regex_str, statements, roles),
        "needs_decision": any(r.level == LEVEL_UNRESOLVED for r in roles),
    })


@app.route("/api/pattern-preview", methods=["POST"])
def api_pattern_preview():
    """
    Show what a pattern would produce, beside what the standard parser produces.

    This is the confirmation step.  A cataloguer approving a role assignment is
    approving MARC output, so they are shown the MARC output -- and shown where
    it differs from what they would have got without the pattern, since that
    difference is the entire effect of confirming it.
    """
    data = request.get_json(force=True) or {}
    regex_str = data.get("regex") or ""
    statements = [str(s)[:MAX_STATEMENT_CHARS]
                  for s in (data.get("statements") or [])[:SAMPLE_LIMIT]]
    do_split = bool(data.get("split", True))

    if not regex_str:
        return jsonify({"error": "No regex provided."}), 400
    if not statements:
        return jsonify({"error": "No statements to preview."}), 400
    if len(regex_str) > plib.MAX_REGEX_CHARS:
        return jsonify({
            "error": f"Regex exceeds the {plib.MAX_REGEX_CHARS:,}-character limit.",
        }), 400

    try:
        compiled = re.compile(regex_str, re.IGNORECASE)
    except re.error as exc:
        return jsonify({"error": f"Invalid regex: {exc}"}), 400

    roles = [plib.GroupRole.from_dict(r) for r in (data.get("roles") or [])
             if isinstance(r, dict)]
    conv_opts, rejections = _convention_opts(data)
    captions = data.get("captions") or None
    frequency = data.get("frequency", "")
    continuity = data.get("numbering_continuity", "r")

    def _fields(parse_result):
        conversion = convert_holdings(
            parse_result, linking_number=1, captions=captions,
            frequency=frequency, numbering_continuity=continuity, **conv_opts,
        )
        return {
            "field_853": conversion.field_853.display() if conversion.field_853 else None,
            "fields_863": [f.display() for f in conversion.fields_863],
            "warnings": conversion.warnings,
            "needs_review": conversion.needs_review,
        }

    previews = []
    for statement in statements:
        pattern_result = build_parse_result(statement, compiled, roles, do_split)
        pattern_side = _fields(pattern_result) if pattern_result else None
        parser_side = _fields(parse_866(statement))
        differs = (
            pattern_side is None
            or pattern_side["field_853"] != parser_side["field_853"]
            or pattern_side["fields_863"] != parser_side["fields_863"]
        )
        previews.append({
            "statement": statement,
            "matched": pattern_result is not None,
            "pattern": pattern_side,
            "parser": parser_side,
            "differs": differs,
        })

    return jsonify({
        "previews": previews,
        "rejections": rejections,
        "unresolved": [r.group for r in roles if r.level == LEVEL_UNRESOLVED],
    })


@app.route("/api/patterns", methods=["GET", "PUT"])
def api_patterns():
    """
    Read or replace this session's confirmed patterns.

    PUT replaces the whole library, so confirming, reordering and removing are
    the same operation from the client's side.  Nothing invalid is stored: a
    rejected pattern comes back with the reason instead.
    """
    if request.method == "GET":
        patterns = _load_library()
        return jsonify({
            "patterns": [p.to_dict() for p in patterns],
            "count": len(patterns),
        })

    data = request.get_json(force=True) or {}
    patterns, errors = plib.load_patterns(data.get("patterns"))
    _save_library(patterns)
    return jsonify({
        "patterns": [p.to_dict() for p in patterns],
        "count": len(patterns),
        "rejected": errors,
    })


@app.route("/api/patterns/export", methods=["GET"])
def api_patterns_export():
    """Download the library so it can be reloaded, or shared with a colleague."""
    patterns = _load_library()
    payload = json.dumps(plib.to_export(patterns), indent=2).encode("utf-8")
    return send_file(
        io.BytesIO(payload),
        mimetype="application/json",
        as_attachment=True,
        download_name="holdings_patterns.json",
    )


@app.route("/api/patterns/import", methods=["POST"])
def api_patterns_import():
    """
    Load a previously exported library, replacing or adding to this session's.

    Accepts the file as an upload or the document as a JSON body.
    """
    if "file" in request.files:
        try:
            document = request.files["file"].read().decode("utf-8")
        except UnicodeDecodeError:
            return jsonify({"error": "That file is not a readable text file."}), 400
        merge = request.form.get("merge") not in (None, "", "0", "false", "False")
    else:
        body = request.get_json(silent=True) or {}
        document = body.get("library", body)
        merge = bool(body.get("merge"))

    incoming, errors = plib.from_export(document)
    if not incoming and errors:
        return jsonify({"error": "; ".join(errors), "rejected": errors}), 400

    existing = _load_library() if merge else []
    combined, more_errors = plib.load_patterns(
        [p.to_dict() for p in existing] + [p.to_dict() for p in incoming]
    )
    _save_library(combined)

    return jsonify({
        "patterns": [p.to_dict() for p in combined],
        "count": len(combined),
        "imported": len(incoming),
        "rejected": errors + more_errors,
    })


@app.route("/api/preview-record", methods=["POST"])
def api_preview_record():
    """
    Preview one whole record without writing anything.

    Linking numbers are a record-level property, so the whole record is
    converted and one preview returned per 866, in field order.
    """
    if not HAS_PYMARC:
        return jsonify({"error": "pymarc is not installed on the server."}), 500

    data = request.get_json(force=True) or {}
    all_records = _load_all_records()
    if all_records is None:
        return jsonify({"error": "No MARC file found. Please upload a file first."}), 400

    record_index = int(data.get("record_index", 0))
    if record_index >= len(all_records):
        return jsonify({"error": "Record index out of range."}), 400

    try:
        record = all_records[record_index]
        conv_opts, rejections = _convention_opts(data)
        patterns = _load_library()

        existing_853s = list(record.get_fields("853"))
        statements = [t for t in ((f["a"] or "") for f in record.get_fields("866")) if t]
        parsed, sources = _parse_all(statements, patterns)

        rc = convert_record(
            parsed,
            existing_853s=existing_853s,
            captions=data.get("captions") or None,
            frequency=data.get("frequency", ""),
            numbering_continuity=data.get("numbering_continuity", "r"),
            **conv_opts,
        )
        # Deliberately no write and no save: preview leaves the file untouched.
        previews = _previews_from(rc, rejections, existing_853s, sources, patterns)
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


@app.route("/api/convert-record", methods=["POST"])
def api_convert_record():
    """Convert every 866 on one record and save the result back to the file."""
    if not HAS_PYMARC:
        return jsonify({"error": "pymarc is not installed on the server."}), 500

    data = request.get_json(force=True) or {}
    all_records = _load_all_records()
    if all_records is None:
        return jsonify({"error": "No MARC file found. Please upload a file first."}), 400

    record_index = int(data.get("record_index", 0))
    conversions_input = data.get("conversions", [])

    try:
        if record_index >= len(all_records):
            return jsonify({"error": "Record index out of range."}), 400

        target = all_records[record_index]
        existing_853s = list(target.get_fields("853"))
        if data.get("clear_existing_853_863"):
            target.remove_fields("853", "863")
            existing_853s = []

        remove_866 = any(c.get("remove_866", True) for c in conversions_input)
        conv_opts, rejections = _convention_opts(data)
        specs = [c for c in conversions_input if c.get("text")]
        texts = [c["text"] for c in specs]

        # The text arrives from the client and may have been edited, so a spec
        # matching no field leaves every 866 alone: never delete a field we
        # cannot account for.
        sources_866 = _match_866_sources(target, texts)

        patterns = _load_library()
        parsed, sources = _parse_all(texts, patterns)

        first = specs[0] if specs else {}
        rc = convert_record(
            parsed,
            existing_853s=existing_853s,
            captions=first.get("captions") or None,
            frequency=first.get("frequency", ""),
            numbering_continuity=first.get("numbering_continuity", "r"),
            **conv_opts,
        )
        _apply_record_conversion(target, rc)

        if remove_866:
            _remove_converted_866s(target, sources_866, rc)

        previews = _previews_from(rc, rejections, (), sources, patterns)
        _save_file("marc_file_converted", _records_to_bytes(all_records))

        return jsonify({"success": True, "previews": previews})
    except Exception as exc:
        app.logger.exception("Request failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/batch-convert", methods=["POST"])
def api_batch_convert():
    """Convert every record in the file, applying the confirmed patterns."""
    if not HAS_PYMARC:
        return jsonify({"error": "pymarc is not installed."}), 500

    all_records = _load_all_records()
    if all_records is None:
        return jsonify({"error": "No MARC file found. Please upload a file first."}), 400

    data = request.get_json(force=True) or {}
    frequency = data.get("frequency", "")
    continuity = data.get("numbering_continuity", "r")
    remove_866 = data.get("remove_866", True)
    clear_existing = data.get("clear_existing_853_863", False)

    conv_opts, rejections = _convention_opts(data)
    captions = data.get("captions") or None
    patterns = _load_library()

    try:
        summary = []
        review_total = 0
        by_source: dict = {}

        for rec_idx, record in enumerate(all_records):
            existing_853s = list(record.get_fields("853"))
            if clear_existing:
                record.remove_fields("853", "863")
                existing_853s = []

            fields_866 = record.get_fields("866")
            if not fields_866:
                continue

            texts = [f["a"] or "" for f in fields_866]
            sources_866 = [f for f, t in zip(fields_866, texts) if t]
            statements = [t for t in texts if t]

            parsed, sources = _parse_all(statements, patterns)
            for src in sources:
                by_source[src] = by_source.get(src, 0) + 1

            rc = convert_record(
                parsed,
                existing_853s=existing_853s,
                captions=captions,
                frequency=frequency,
                numbering_continuity=continuity,
                **conv_opts,
            )
            _apply_record_conversion(record, rc)

            if remove_866:
                _remove_converted_866s(record, sources_866, rc)

            review_total += rc.needs_review
            summary.append({
                "index": rec_idx,
                "converted_fields": rc.converted,
                "conformed_fields": rc.conformed,
                "needs_review": rc.needs_review,
                "warnings": rc.warnings,
            })

        _save_file("marc_file_converted", _records_to_bytes(all_records))

        labels = _source_labels(patterns)
        return jsonify({
            "success": True,
            "records_processed": len(summary),
            "needs_review": review_total,
            "rejections": rejections,
            "by_source": [
                {"source": src, "label": labels.get(src, src), "count": n}
                for src, n in sorted(by_source.items(), key=lambda kv: -kv[1])
            ],
            "summary": summary,
        })
    except Exception as exc:
        app.logger.exception("Request failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/download-converted", methods=["GET"])
def api_download_converted():
    """Download the converted MARC binary."""
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
    # Named separately from the two standalone apps' ports so all three can be
    # exported at once; see the note in converter/app.py about port 5000.
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1",
            port=int(os.environ.get("WORKBENCH_PORT", 5003)))
