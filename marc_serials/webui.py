"""
Web-layer pieces every application shares: the version badge, the stylesheet,
and reading a caption convention off a request body.

Both were declared three times, once per app, differing only in how each
computed its way back to shared/. One copy's docstring still explained how the
path survived nginx's trailing-slash proxy_pass, for a deployment the repository
no longer describes.
"""

from __future__ import annotations

import json
import logging
import os

from flask import send_from_directory

from marc_serials.converter import CONVENTION_STANDARD, resolve_convention

# shared/ sits beside the package, at the repository root.
SHARED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shared"
)

_log = logging.getLogger(__name__)


def load_about() -> dict:
    """
    Version and changelog, shared by all three applications.

    Read per request rather than cached at import, so editing the file and
    reloading the page is enough to see the change. Never fatal: a missing or
    malformed file degrades to no badge rather than a broken page.
    """
    path = os.path.join(SHARED_DIR, "about.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        _log.warning("Could not read shared/about.json", exc_info=True)
        return {}


def register_shared_routes(app) -> None:
    """Give `app` the shared stylesheet at /ui.css."""

    @app.route("/ui.css")
    def ui_css():
        """Serve the stylesheet shared by all three applications."""
        return send_from_directory(SHARED_DIR, "ui.css", mimetype="text/css")


def convention_opts(data: dict) -> tuple:
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
        conv, subfields=subfields, indicators=indicators,
        chron_as_text=chron_as_text
    )
    return {"convention_spec": spec}, rejections
