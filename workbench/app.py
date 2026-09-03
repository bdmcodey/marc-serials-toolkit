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
                            enum_level_fields,
                            FREQUENCY_CODES, convention_presets,
                            convert_holdings, convert_record, resolve_convention)
from pattern_detector import detect_patterns, split_multi_range

import pattern_library as plib
from pattern_bridge import (CAPTION_CHOICES, ENCODABLE_KINDS, KIND_IGNORE,
                            KIND_LABELS, KIND_UNRESOLVED,
                            PARSER_SOURCE, SKIPPED_SOURCE, UNMATCHED_SOURCE,
                            apply_patterns, build_parse_result, infer_roles)

# ---------------------------------------------------------------------------

app = Flask(__name__,
            template_folder=os.path.join(_BASE_DIR, "templates"),
            static_folder=os.path.join(_BASE_DIR, "static"))
app.secret_key = os.environ.get("SECRET_KEY", "marc-workbench-dev-key")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024   # 25 MB

# Flask names its session cookie "session" at path / by default, and the three
# apps are served from one hostname -- so with the stock name the workbench and
# the converter overwrite each other's cookie. Neither can then read what it
# wrote, and the cataloguer is told their uploaded file has gone. The workbench
# is the newcomer, so it is the one that yields.
app.config["SESSION_COOKIE_NAME"] = "workbench_session"

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

# How many of a group's examples the confirmation screen can step through.
# Bounded because a group can hold thousands and each one costs a match.
EXAMPLE_LIMIT = 25

# How many records one review page previews. Previewing a record costs well
# under a millisecond, so the ceiling is response size, not time.
PREVIEW_PAGE = 50
PREVIEW_PAGE_MAX = 200

# The pattern being confirmed, as it appears to the preview: ahead of everything
# already in the library, and never written to it.
CANDIDATE_ID = "__candidate__"
CANDIDATE_LABEL = "This pattern"
CANDIDATE_PRIORITY = 10 ** 6


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


def _parser_fallback(data: dict) -> bool:
    """Whether an unmatched statement falls to the standard parser. Default yes."""
    value = data.get("parser_fallback", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no")
    return bool(value)


def _keep_separate(data: dict) -> set:
    """
    Records the cataloguer has told not to merge patterns on.

    Whether two statements recording different amounts of detail are one
    publication is a judgement about the serial, so it is theirs to make, per
    record.
    """
    raw = data.get("keep_separate")
    if not isinstance(raw, (list, tuple)):
        return set()
    out = set()
    for value in raw:
        try:
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _skipped_records(data: dict) -> set:
    """
    Records the cataloguer has told the tool not to touch.

    Skipping is stronger than every other switch on the screen: the record is
    not converted, its 866s are not removed, and its existing 853/863 are left
    alone even when "clear existing" is set. It comes out of a run byte for
    byte as it went in, which is the whole point -- these are the ones going to
    be catalogued by hand.
    """
    raw = data.get("skip_records")
    if not isinstance(raw, (list, tuple)):
        return set()
    out = set()
    for value in raw:
        try:
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return out


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


def _record_title(record) -> str:
    """The 245 $a$b of a record, trimmed of ISBD punctuation."""
    field = record.get("245")
    if not field:
        return ""
    return " ".join(field.get_subfields("a", "b")).strip().rstrip(" /:")


def _read_marc_file(fileobj) -> list[dict]:
    """Read a MARC file into the record summaries the record list renders."""
    records_out = []
    reader = MARCReader(fileobj, to_unicode=True, force_utf8=True,
                        utf8_handling="replace")
    for rec_idx, record in enumerate(reader):
        if record is None:
            continue
        title = _record_title(record)

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

def _parse_all(texts, patterns, fallback: bool = True) -> tuple[list, list]:
    """
    Parse every statement, preferring a confirmed pattern.

    `fallback` decides what becomes of a statement no pattern matches: the
    standard parser reads it, or nothing is written and its 866 is left alone.
    Returns (parse_results, sources) in step with `texts`.
    """
    parsed, sources = [], []
    for text in texts:
        result, source = apply_patterns(text, patterns, fallback)
        parsed.append(result)
        sources.append(source)
    return parsed, sources


def _source_labels(patterns) -> dict:
    labels = {p.id: p.label for p in patterns}
    labels[PARSER_SOURCE] = "Standard parser"
    labels[UNMATCHED_SOURCE] = "No pattern matched — left as it is"
    labels[SKIPPED_SOURCE] = "Skipped — left as it is"
    return labels


def _requested_indices(data: dict, total: int, offset: int, limit: int) -> list:
    """
    Which records a review request wants: an explicit list, or a page.

    An explicit list exists because the review screen pages through whatever
    the *filter* is showing, not through the file in order -- with a filter on,
    records 1-50 of the file are not the first fifty a cataloguer is looking at.
    """
    raw = data.get("indices")
    if isinstance(raw, (list, tuple)):
        wanted = []
        for value in raw[:PREVIEW_PAGE_MAX]:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= index < total and index not in wanted:
                wanted.append(index)
        return wanted
    return list(range(offset, min(offset + limit, total)))


def _review_row(record, index, *, patterns, fallback, conv_opts, captions,
                frequency, continuity, rejections, merge_patterns,
                skipped: bool, with_previews: bool) -> dict:
    """
    One record as the review screen sees it: what it would produce, and what
    read it.

    `with_previews` is the difference between the two callers.  The filters and
    the row status need only the counts, for every record in the file; the
    fields themselves are wanted only for the handful on screen, and carrying
    them for a 400-record file would be most of a megabyte spent on rows nobody
    has opened.  Both come from here, so a filter can never disagree with the
    preview it filtered on.
    """
    row = {
        "index": index,
        "title": _record_title(record) or f"Record {index + 1}",
        "converted": 0,
        "held": 0,
        "flagged": 0,
        "sources": [],
        "has_866": False,
        "skipped": skipped,
    }
    if with_previews:
        row["previews"] = []

    statements = [t for t in ((f["a"] or "") for f in record.get_fields("866")) if t]
    row["has_866"] = bool(statements)

    # A preview that showed fields a skipped record will never get would be
    # showing something that is not going to happen.
    if skipped or not statements:
        return row

    parsed, sources = _parse_all(statements, patterns, fallback)
    rc = convert_record(
        parsed, existing_853s=list(record.get_fields("853")), captions=captions,
        frequency=frequency, numbering_continuity=continuity,
        merge_patterns=merge_patterns, **conv_opts,
    )
    previews = _previews_from(rc, rejections, list(record.get_fields("853")),
                              sources, patterns)
    for preview, text in zip(previews, statements):
        preview["source_866"] = text

    row["converted"] = sum(1 for p in previews if p["fields_863"])
    row["held"] = sum(1 for p in previews if not p["fields_863"])
    # Converted, and the tool cannot vouch for it. Counted apart from "held"
    # because these records *do* have fields -- what they need is a look, not
    # a pattern.
    row["flagged"] = sum(1 for p in previews if p.get("flagged"))
    row["sources"] = sorted({p["source"] for p in previews})
    if with_previews:
        row["previews"] = previews
    return row


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
    merged = set(rc.merged_links)
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
            "flagged": c.flagged,
            "link": link,
            "existing": bool(c.conformed and display),
            "source": source,
            "source_label": labels.get(source, "Standard parser"),
            "from_pattern": source != PARSER_SOURCE,
            "merged_run": link in merged,
        })
    return out


# ---------------------------------------------------------------------------
# Pattern annotation for the confirmation screen
# ---------------------------------------------------------------------------

def _example_values(regex: str, statements, roles) -> list:
    """
    What each capture group catches, one entry per example statement.

    Deliberately *not* pooled across examples.  A cataloguer checking a pattern
    is checking one statement at a time -- this 866, these captured values, that
    853 -- and a column mixing values from several statements cannot be lined up
    against any of them.

    Only a full match contributes values, for the same reason pattern_bridge
    only converts on one: values pulled out of a substring look like a working
    pattern on this screen, and are exactly what the conversion will refuse to
    use.  A partial match shows empty, which is what the pattern will do.
    """
    try:
        compiled = re.compile(regex, re.IGNORECASE)
    except re.error:
        return []

    out = []
    for statement in list(statements)[:EXAMPLE_LIMIT]:
        s = (statement or "").strip()[:MAX_STATEMENT_CHARS]
        m = compiled.fullmatch(s)
        caught = m.groupdict() if m else {}
        out.append({r.group: (caught.get(r.group) or "") for r in roles})
    return out


def _statement_origins(do_split: bool) -> dict:
    """
    Map each statement back to the 866 field it came from.

    Splitting means a statement need not equal any $a -- "v.1(1990)-v.3(1992) /
    v.5(1994)-v.8(1997)" becomes two -- so the client cannot match an example to
    its record by comparing text, and the association has to be made here, while
    the statements are being taken apart.

    First occurrence wins: the same holdings string can appear on two records,
    and either one previews the numbering equally well.
    """
    stored = _load_file("marc_file")
    if not stored:
        return {}

    origins: dict = {}
    for record in _read_marc_file(io.BytesIO(stored)):
        for field_index, fld in enumerate(record["fields_866"]):
            text = (fld["a"] or "").strip()
            if not text:
                continue
            pieces = split_multi_range(text) if do_split else [text]
            for piece in pieces:
                key = piece.strip()[:MAX_STATEMENT_CHARS]
                if key and key not in origins:
                    origins[key] = {
                        "record_index": record["index"],
                        "field_index": field_index,
                        "source_866": text,
                    }
    return origins


def _annotate_group(group_dict: dict, origins: Optional[dict] = None) -> dict:
    """Add the roles to offer, and per-example values and provenance."""
    named = group_dict.get("named_groups") or []
    roles = infer_roles(named)
    examples = group_dict.get("examples") or []
    shown = examples[:EXAMPLE_LIMIT]
    origins = origins or {}

    group_dict["suggested_roles"] = [r.to_dict() for r in roles]
    group_dict["example_values"] = _example_values(
        group_dict.get("regex") or "", shown, roles
    )
    group_dict["example_sources"] = [
        origins.get((e or "").strip()[:MAX_STATEMENT_CHARS]) for e in shown
    ]
    group_dict["examples_shown"] = len(shown)
    group_dict["needs_decision"] = any(r.needs_a_decision for r in roles)
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
        enum_levels=enum_level_fields(),
        convention_presets=convention_presets(),
        kind_choices=[(k, KIND_LABELS[k]) for k in ENCODABLE_KINDS],
        caption_choices=list(CAPTION_CHOICES),
        ignore_kind=KIND_IGNORE,
        ignore_label=KIND_LABELS[KIND_IGNORE],
        unresolved_kind=KIND_UNRESOLVED,
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
        # Built from the stored file rather than from `raw`, so an example
        # resolves to its record whether the client resent the statements or
        # left the server to read them.
        origins = _statement_origins(do_split)
        groups = [_annotate_group(g.to_dict(), origins)
                  for g in detect_patterns(statements)]
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

    # "matched" means the pattern spans the whole statement, because that is
    # what the Workbench will convert on -- see pattern_bridge. A partial hit is
    # still reported, with the span it covers, so the cataloguer can see how
    # close the expression came and what it missed; it just does not count.
    results = []
    for s in statements:
        s = s.strip()
        fm = compiled.fullmatch(s)
        partial = None if fm else compiled.search(s)
        m = fm or partial
        results.append({
            "statement": s,
            "matched": fm is not None,
            "full_match": fm is not None,
            "partial_match": partial is not None,
            "groups": fm.groupdict() if fm else {},
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
        # Aligned with the statements sent, so the card can keep showing the
        # example it was already on after the expression is edited.
        "example_values": _example_values(regex_str, statements, roles),
        "needs_decision": any(r.needs_a_decision for r in roles),
    })


@app.route("/api/pattern-preview", methods=["POST"])
def api_pattern_preview():
    """
    Show what a pattern would produce, with the linking numbers it would get.

    This is the confirmation step, so it has to show the real thing.  $8 is a
    record-level decision -- convert_record() shares one 853 across statements
    expressing the same publication pattern and numbers them 1.1, 1.2, then 2.1
    when the pattern changes -- so previewing a statement on its own always read
    "1.1" and quietly misrepresented what conversion would write.

    Given the record an example came from, the whole record is converted with
    the candidate pattern in front of the confirmed library, exactly as
    /api/preview-record converts it, and the example's own row is marked.

    Statements pasted rather than uploaded have no record to sit in; those fall
    back to previewing the statement alone, beside the standard parser.
    """
    data = request.get_json(force=True) or {}
    regex_str = data.get("regex") or ""
    do_split = bool(data.get("split", True))

    if not regex_str:
        return jsonify({"error": "No regex provided."}), 400
    if len(regex_str) > plib.MAX_REGEX_CHARS:
        return jsonify({
            "error": f"Regex exceeds the {plib.MAX_REGEX_CHARS:,}-character limit.",
        }), 400

    try:
        compiled = re.compile(regex_str, re.IGNORECASE)
    except re.error as exc:
        return jsonify({"error": f"Invalid regex: {exc}"}), 400

    roles = plib.assign_levels([plib.GroupRole.from_dict(r)
                                for r in (data.get("roles") or [])
                                if isinstance(r, dict)])
    unresolved = [r.group for r in roles if r.kind == KIND_UNRESOLVED]

    conv_opts, rejections = _convention_opts(data)
    captions = data.get("captions") or None
    frequency = data.get("frequency", "")
    continuity = data.get("numbering_continuity", "r")

    if unresolved:
        # Nothing to show until every value has a meaning; the client renders
        # the list rather than a preview.
        return jsonify({"scope": "unresolved", "previews": [],
                        "unresolved": unresolved, "rejections": rejections})

    record_index = data.get("record_index")
    field_index = data.get("field_index")

    # ── Record scope: the numbering conversion would actually write ──────────
    if HAS_PYMARC and isinstance(record_index, int):
        all_records = _load_all_records()
        if all_records and 0 <= record_index < len(all_records):
            candidate, errors = plib.validate_pattern({
                "id": CANDIDATE_ID,
                "label": CANDIDATE_LABEL,
                "regex": regex_str,
                "roles": [r.to_dict() for r in roles],
                "split": do_split,
                # Ahead of everything confirmed: the cataloguer is looking at
                # this pattern, so it must be the one that reads its own shape.
                "priority": CANDIDATE_PRIORITY,
            })
            if candidate is None:
                return jsonify({"error": "; ".join(errors)}), 400

            record = all_records[record_index]
            existing_853s = list(record.get_fields("853"))

            texts, field_indexes = [], []
            for idx, fld in enumerate(record.get_fields("866")):
                text = fld["a"] or ""
                if text:
                    texts.append(text)
                    field_indexes.append(idx)

            patterns = [candidate] + _load_library()
            parsed, sources = _parse_all(texts, patterns,
                                         _parser_fallback(data))
            rc = convert_record(
                parsed,
                existing_853s=existing_853s,
                captions=captions,
                frequency=frequency,
                numbering_continuity=continuity,
                merge_patterns=record_index not in _keep_separate(data),
                **conv_opts,
            )
            previews = _previews_from(rc, rejections, existing_853s,
                                      sources, patterns)
            for preview, text, idx in zip(previews, texts, field_indexes):
                preview["source_866"] = text
                preview["is_example"] = (idx == field_index)

            return jsonify({
                "scope": "record",
                "record": {
                    "index": record_index,
                    "title": _record_title(record) or f"Record {record_index + 1}",
                },
                "previews": previews,
                "unresolved": [],
                "rejections": rejections,
            })

    # ── Statement scope: no record to sit in ────────────────────────────────
    statements = [str(s)[:MAX_STATEMENT_CHARS]
                  for s in (data.get("statements") or [])[:EXAMPLE_LIMIT]]
    if not statements:
        return jsonify({"error": "No statements to preview."}), 400

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
        pattern_result = build_parse_result(statement, compiled, roles, do_split,
                                            _parser_fallback(data))
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
        "scope": "statement",
        "record": None,
        "previews": previews,
        "unresolved": [],
        "rejections": rejections,
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
        parsed, sources = _parse_all(statements, patterns,
                                     _parser_fallback(data))

        rc = convert_record(
            parsed,
            existing_853s=existing_853s,
            captions=data.get("captions") or None,
            frequency=data.get("frequency", ""),
            numbering_continuity=data.get("numbering_continuity", "r"),
            merge_patterns=record_index not in _keep_separate(data),
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


@app.route("/api/preview-records", methods=["POST"])
def api_preview_records():
    """
    Preview a page of records in one request, for reviewing a file record by
    record rather than pattern by pattern.

    Previewing one record costs well under a millisecond; what makes reviewing a
    whole file impractical is a round trip per record.  This returns a page of
    them, each with the counts a reviewer filters on, so the work is "show me
    everything still held for review" rather than "open all 400 and look".

    Nothing is written: this is the read-only twin of /api/batch-convert.

    POST JSON: {"offset": 0, "limit": 50, ...conversion settings}
    """
    if not HAS_PYMARC:
        return jsonify({"error": "pymarc is not installed on the server."}), 500

    data = request.get_json(force=True) or {}
    all_records = _load_all_records()
    if all_records is None:
        return jsonify({"error": "No MARC file found. Please upload a file first."}), 400

    try:
        offset = max(0, int(data.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = int(data.get("limit", PREVIEW_PAGE))
    except (TypeError, ValueError):
        limit = PREVIEW_PAGE
    limit = max(1, min(limit, PREVIEW_PAGE_MAX))

    conv_opts, rejections = _convention_opts(data)
    captions = data.get("captions") or None
    frequency = data.get("frequency", "")
    continuity = data.get("numbering_continuity", "r")
    patterns = _load_library()
    fallback = _parser_fallback(data)
    keep_separate = _keep_separate(data)
    skip_records = _skipped_records(data)

    try:
        wanted = _requested_indices(data, len(all_records), offset, limit)
        out = [
            _review_row(all_records[index], index,
                        patterns=patterns, fallback=fallback,
                        conv_opts=conv_opts, captions=captions,
                        frequency=frequency, continuity=continuity,
                        rejections=rejections,
                        merge_patterns=index not in keep_separate,
                        skipped=index in skip_records,
                        with_previews=True)
            for index in wanted
        ]

        return jsonify({
            "records": out,
            "total": len(all_records),
            "offset": offset,
            "limit": limit,
            "rejections": rejections,
        })
    except Exception as exc:
        app.logger.exception("Request failed")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/review-index", methods=["POST"])
def api_review_index():
    """
    What every record in the file would produce, in counts rather than fields.

    The review screen filters and pages over the whole file, so it needs an
    answer for every record -- not just the page whose previews are loaded.
    Without this the filters silently lied: a record with no preview data
    passed every test, so "needs attention" showed the whole file from the
    second page on.

    It also answers "which records did this pattern read", which is what makes
    editing a pattern able to put the records it touched back into the review
    queue instead of leaving a stale tick beside them.

    Deliberately no field data: see _review_row.

    POST JSON: {...conversion settings}
    """
    if not HAS_PYMARC:
        return jsonify({"error": "pymarc is not installed on the server."}), 500

    data = request.get_json(force=True) or {}
    all_records = _load_all_records()
    if all_records is None:
        return jsonify({"error": "No MARC file found. Please upload a file first."}), 400

    conv_opts, rejections = _convention_opts(data)
    captions = data.get("captions") or None
    frequency = data.get("frequency", "")
    continuity = data.get("numbering_continuity", "r")
    patterns = _load_library()
    fallback = _parser_fallback(data)
    keep_separate = _keep_separate(data)
    skip_records = _skipped_records(data)

    try:
        rows = [
            _review_row(record, index,
                        patterns=patterns, fallback=fallback,
                        conv_opts=conv_opts, captions=captions,
                        frequency=frequency, continuity=continuity,
                        rejections=rejections,
                        merge_patterns=index not in keep_separate,
                        skipped=index in skip_records,
                        with_previews=False)
            for index, record in enumerate(all_records)
        ]
        return jsonify({"records": rows, "total": len(all_records)})
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

        remove_866 = any(c.get("remove_866", False) for c in conversions_input)
        conv_opts, rejections = _convention_opts(data)
        specs = [c for c in conversions_input if c.get("text")]
        texts = [c["text"] for c in specs]

        # The text arrives from the client and may have been edited, so a spec
        # matching no field leaves every 866 alone: never delete a field we
        # cannot account for.
        sources_866 = _match_866_sources(target, texts)

        patterns = _load_library()
        parsed, sources = _parse_all(texts, patterns, _parser_fallback(data))

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
    # Defaults to keeping them: an ILS that regenerates 866s from 853/863 makes
    # the originals redundant rather than wrong, and keeping them means the file
    # can be run through again with different settings.
    remove_866 = data.get("remove_866", False)
    clear_existing = data.get("clear_existing_853_863", False)

    conv_opts, rejections = _convention_opts(data)
    captions = data.get("captions") or None
    patterns = _load_library()
    fallback = _parser_fallback(data)
    keep_separate = _keep_separate(data)
    skip_records = _skipped_records(data)

    try:
        summary = []
        review_total = 0
        skipped_total = 0
        by_source: dict = {}

        for rec_idx, record in enumerate(all_records):
            # Before anything else, including clearing existing fields: a
            # skipped record is one the run does not touch at all.
            if rec_idx in skip_records:
                skipped_total += 1
                summary.append({
                    "index": rec_idx,
                    "converted_fields": 0,
                    "conformed_fields": 0,
                    "needs_review": 0,
                    "skipped": True,
                    "warnings": ["Skipped: this record was left exactly as it was."],
                })
                continue

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

            parsed, sources = _parse_all(statements, patterns, fallback)
            for src in sources:
                by_source[src] = by_source.get(src, 0) + 1

            rc = convert_record(
                parsed,
                existing_853s=existing_853s,
                captions=captions,
                frequency=frequency,
                numbering_continuity=continuity,
                merge_patterns=rec_idx not in keep_separate,
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
            "skipped_records": skipped_total,
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
