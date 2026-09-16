"""
What the one application must expose, and what its page must carry.

Until September 2026 this file guarded an import scheme that let three Flask
applications share one interpreter without shadowing each other. There is one
now, so the interesting question changed: not "are these three apps distinct"
but "does the one that remains still do everything the three did".

The route sets below are the three applications' routes as they stood the day
before they were merged. Nothing may quietly fall out of them.
"""

from __future__ import annotations

import re

import pytest

from conftest import REPO_ROOT

TEMPLATES = REPO_ROOT / "marc_serials" / "templates"

# The standalone converter's routes, the day it was retired.
CONVERTER_ROUTES = {
    "/", "/ui.css", "/static/<path:filename>",
    "/api/parse-text", "/api/upload-marc", "/api/convert-record",
    "/api/preview-record", "/api/batch-convert", "/api/download-converted",
}

# The standalone pattern detector's routes, the day it was retired.
DETECTOR_ROUTES = {
    "/", "/ui.css", "/static/<path:filename>",
    "/api/detect", "/api/upload-marc", "/api/test-regex",
}

# What only ever existed here: confirming what a pattern means, and the library
# of those confirmations.
JOINED_ROUTES = {
    "/api/pattern-preview", "/api/patterns",
    "/api/patterns/export", "/api/patterns/import",
    "/api/preview-records", "/api/review-index",
}


def _rules(flask_app) -> set[str]:
    return {r.rule for r in flask_app.url_map.iter_rules()}


def test_every_route_the_converter_had_still_exists(marc_app):
    """Retiring its URL must not retire what it could do."""
    assert CONVERTER_ROUTES <= _rules(marc_app.app)


def test_every_route_the_detector_had_still_exists(marc_app):
    assert DETECTOR_ROUTES <= _rules(marc_app.app)


def test_the_joined_routes_are_here_too(marc_app):
    assert JOINED_ROUTES <= _rules(marc_app.app)


def test_there_is_nothing_else(marc_app):
    """
    The route list is the application's whole surface, so it is asserted
    exactly: a route appearing without a test naming it is a route nobody
    decided on.
    """
    expected = CONVERTER_ROUTES | DETECTOR_ROUTES | JOINED_ROUTES
    assert _rules(marc_app.app) == expected


def test_pymarc_was_found(marc_app):
    """Without it the file routes degrade to 500s with a clear message."""
    assert marc_app.HAS_PYMARC is True


def test_the_page_renders(client):
    assert client.get("/").status_code == 200


def test_the_stylesheet_is_served(client):
    response = client.get("/ui.css")
    assert response.status_code == 200
    assert response.data


def test_the_page_carries_its_fold_controls(client):
    """
    The step-2 fold and the skip controls are client-side, so nothing else in
    this suite would notice them going missing -- and a template that renders
    without them renders fine, just missing the controls. Pin the handful of
    hooks the script drives.
    """
    page = client.get("/").get_data(as_text=True)
    for anchor in ('id="btn-toggle-patterns"', 'id="patterns-body"',
                   'id="patterns-summary"', 'aria-controls="patterns-body"',
                   'class="rec-skip"', 'pc-skip', 'data-filter="skipped"',
                   'jump-to-pattern', 'id="review-notice"'):
        assert anchor in page, anchor


# A callback passed to .map() by name: the shape of the bug this catches.
_MAP_CALLBACK_RE = re.compile(r"\.map\(\s*([A-Za-z_$][\w$]*)\s*\)")


def _declared_names(script: str) -> set:
    """How the templates declare a function, either form."""
    return (set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", script))
            | set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", script)))


@pytest.mark.parametrize("template", sorted(TEMPLATES.glob("*.html")))
def test_every_named_map_callback_in_the_template_exists(template):
    """
    The one bug the Python suite could not see.

    renderPreviewPair() was deleted when record-scope preview arrived, and the
    line calling it was left behind. Every pasted statement -- the path a
    cataloguer takes when trying the tool without a .mrc file -- reached
    `data.previews.map(renderPreviewPair)` and threw "renderPreviewPair is not
    defined", showing an error where the generated fields should be. The server
    was fine throughout, so no route test could have noticed.

    Nothing here type-checks the template. It catches exactly the shape that
    went wrong: a callback passed to .map() by a name that nothing defines.
    """
    script = template.read_text(encoding="utf-8")
    declared = _declared_names(script)
    used = set(_MAP_CALLBACK_RE.findall(script))
    # Built-ins are legitimate callbacks and are not declared anywhere.
    missing = sorted(used - declared - {"Number", "String", "Boolean", "parseInt"})
    assert not missing, f"{template.name} maps over undefined: {missing}"


def test_the_summary_knows_which_sources_are_not_patterns():
    """
    The batch summary splits statements into "read by your patterns" and
    everything else, which is the figure a cataloguer checks and the one that
    makes a library the server has lost obvious. The split is a hard-coded set
    of source ids in the template, and the ids live in marc_serials.bridge --
    two copies of one list, so this pins them together.
    """
    from marc_serials.bridge import PARSER_SOURCE, SKIPPED_SOURCE, UNMATCHED_SOURCE

    script = (TEMPLATES / "tool.html").read_text(encoding="utf-8")
    match = re.search(r"const NOT_A_PATTERN = new Set\(\[([^\]]*)\]\)", script)
    assert match, "the summary no longer names the non-pattern sources"

    in_template = set(re.findall(r"'([^']+)'", match.group(1)))
    assert in_template == {PARSER_SOURCE, UNMATCHED_SOURCE, SKIPPED_SOURCE}


def test_the_page_explains_what_confirming_decides(client):
    """
    The note that tells a cataloguer whether their answer changes the reading,
    a caption, or nothing at all. It is built client-side from group.decides,
    so no route test would notice it going missing.
    """
    page = client.get("/").get_data(as_text=True)
    assert "function decidesNote(" in page
    assert "decides-note" in page
    for verdict in ("reading:", "caption:", "nothing:"):
        assert verdict in page, verdict


def test_patterns_the_parser_reads_are_not_presented_as_work(client):
    """
    partitionGroups must route a group whose answer changes nothing away from
    the `open` list -- that list is the work, and a question with no consequence
    is not work.
    """
    page = client.get("/").get_data(as_text=True)
    assert "group.decides === 'nothing'" in page
    assert "parts.readable" in page


def test_a_pattern_deciding_a_reading_is_never_confirmed_unasked(client):
    """
    Auto-confirmation must skip a pattern whose answer decides the reading.

    The parser refuses those statements on purpose -- it read part of the
    wording and could not account for the rest -- so confirming one unasked
    writes holdings on an inference nobody checked. "v. 19 no. 2 Suppl. (1998)"
    is the shape that showed it up: the pattern reads it confidently as volume
    19, issue 2, and the supplement, which is what the library actually holds,
    disappears into an ordinary 863.
    """
    page = client.get("/").get_data(as_text=True)
    assert "g.decides !== 'reading'" in page, \
        "auto-confirm no longer excludes patterns that decide a reading"


def test_the_summary_says_what_the_outstanding_patterns_decide(client):
    """
    "Say what a value means" described every card until 0.10.0. Most of them now
    supply only a caption, which is a different amount of care, so the count
    distinguishes them.
    """
    page = client.get("/").get_data(as_text=True)
    assert "function openBreakdown(" in page
    assert "how a statement is read" in page
    assert "supply' : 'supplies'" in page or "supplies' : 'supply'" in page \
        or "supplies" in page
