"""
pattern_app.py
--------------
Standalone Flask web application for detecting structural patterns in
MARC 866 textual holdings statements and generating named-group regexes.

Completely independent of app.py — run separately on port 5001.

Run:
    python pattern_app.py

Then open http://localhost:5001
"""

from __future__ import annotations

import io
import os
import re

from flask import Flask, render_template, request, jsonify

from pattern_detector import detect_patterns, split_multi_range

try:
    from pymarc import MARCReader
    HAS_PYMARC = True
except ImportError:
    HAS_PYMARC = False

# ---------------------------------------------------------------------------
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(_BASE_DIR, "templates"),
    static_folder=os.path.join(_BASE_DIR, "static"),
)
app.secret_key = os.environ.get("SECRET_KEY", "pattern-tool-dev-key")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024   # 25 MB


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("tool.html", has_pymarc=HAS_PYMARC)


# ---------------------------------------------------------------------------

@app.route("/api/detect", methods=["POST"])
def api_detect():
    """
    Detect structural patterns in a list of holdings strings and return
    one or more pattern groups with generated regexes.

    POST JSON
    ---------
    {
        "statements": ["v.1:no.1(1990:Jan.)-...", ...],
        "split_multi_range": true
    }

    Response JSON
    -------------
    {
        "total_statements": 120,
        "total_patterns":   3,
        "groups": [ { PatternGroup.to_dict() }, ... ]
    }
    """
    data = request.get_json(force=True)
    raw: list[str] = data.get("statements", [])
    do_split: bool = bool(data.get("split_multi_range", True))

    if not raw:
        return jsonify({"error": "No statements provided."}), 400

    statements: list[str] = []
    for s in raw:
        if do_split:
            statements.extend(split_multi_range(s))
        else:
            if s.strip():
                statements.append(s.strip())

    statements = [s[:500] for s in statements if s.strip()]
    if not statements:
        return jsonify({"error": "All statements were empty after processing."}), 400
    if len(statements) > 5000:
        statements = statements[:5000]

    try:
        groups = detect_patterns(statements)
        return jsonify({
            "total_statements": len(statements),
            "total_patterns":   len(groups),
            "groups":           [g.to_dict() for g in groups],
        })
    except Exception as exc:
        app.logger.exception("Request failed")
        return jsonify({
            "error": str(exc),
        }), 500


# ---------------------------------------------------------------------------

@app.route("/api/upload-marc", methods=["POST"])
def api_upload_marc():
    """
    Upload a MARC binary file and extract all 866 $a values.
    Returns the raw statement list so the UI can feed them into /api/detect.

    Response JSON
    -------------
    {
        "records":    45,
        "statements": ["v.1:no.1(1990)...", ...],
        "count":      45
    }
    """
    if not HAS_PYMARC:
        return jsonify({"error": "pymarc is not installed on this server."}), 500

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename."}), 400

    try:
        file_bytes = f.read()
        reader = MARCReader(
            io.BytesIO(file_bytes),
            to_unicode=True,
            force_utf8=True,
            utf8_handling="replace",
        )
        statements: list[str] = []
        record_count = 0

        for record in reader:
            record_count += 1
            for fld in record.get_fields("866"):
                val = fld.get("a") or ""
                if val.strip():
                    statements.append(val.strip())

        return jsonify({
            "records":    record_count,
            "statements": statements,
            "count":      len(statements),
        })
    except Exception as exc:
        app.logger.exception("Request failed")
        return jsonify({
            "error": str(exc),
        }), 500


# ---------------------------------------------------------------------------

@app.route("/api/test-regex", methods=["POST"])
def api_test_regex():
    """
    Test a user-supplied (possibly edited) regex against a list of statements.

    POST JSON
    ---------
    {
        "regex":      "(?P<start_vol>\\d+)...",
        "statements": ["v.1(1990)-v.5(1994)", ...]
    }

    Response JSON
    -------------
    {
        "matched":    42,
        "failed":     3,
        "match_rate": 0.933,
        "results": [
            {
                "statement": "v.1(1990)-v.5(1994)",
                "matched":   true,
                "full_match": true,
                "groups":    {"start_vol": "1", "start_year": "1990", ...},
                "span":      [0, 18]
            },
            ...
        ]
    }
    """
    data        = request.get_json(force=True)
    regex_str   = data.get("regex", "")
    statements  = data.get("statements", [])

    if not regex_str:
        return jsonify({"error": "No regex provided."}), 400
    if len(regex_str) > 2000:
        return jsonify({
            "error": "Regex exceeds the 2,000-character test limit.",
        }), 400
    # User-supplied regex runs against user-supplied text, so bound both to
    # limit catastrophic-backtracking (ReDoS) exposure on this public endpoint.
    statements = [str(s)[:500] for s in statements[:2000]]

    try:
        compiled = re.compile(regex_str, re.IGNORECASE)
    except re.error as exc:
        return jsonify({"error": f"Invalid regex: {exc}"}), 400

    results = []
    for s in statements:
        s = s.strip()
        fm = compiled.fullmatch(s)
        m  = fm or compiled.search(s)
        if m:
            results.append({
                "statement":  s,
                "matched":    True,
                "full_match": fm is not None,
                "groups":     m.groupdict(),
                "span":       list(m.span()),
            })
        else:
            results.append({
                "statement":  s,
                "matched":    False,
                "full_match": False,
                "groups":     {},
                "span":       None,
            })

    matched_n = sum(1 for r in results if r["matched"])
    return jsonify({
        "results":    results,
        "matched":    matched_n,
        "failed":     len(results) - matched_n,
        "match_rate": matched_n / len(results) if results else 1.0,
    })


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=5001)
