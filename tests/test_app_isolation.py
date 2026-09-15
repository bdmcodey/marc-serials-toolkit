"""
Guards the import scheme that lets all three Flask apps live in one interpreter.

Every other API test depends on this working. If two apps ever start sharing a
module object, or one of them silently claims the bare name `app`, the failures
elsewhere would be baffling -- so they are caught here instead.
"""

from __future__ import annotations

import re
import sys

import pytest

from conftest import REPO_ROOT

CONVERTER_ROUTES = {
    "/", "/ui.css", "/static/<path:filename>",
    "/api/parse-text", "/api/upload-marc", "/api/convert-record",
    "/api/preview-record", "/api/batch-convert", "/api/download-converted",
}

DETECTOR_ROUTES = {
    "/", "/ui.css", "/static/<path:filename>",
    "/api/detect", "/api/upload-marc", "/api/test-regex",
}

# The workbench is the union of the two, plus the endpoints that only exist
# because they are joined: confirming what a pattern means, and the library of
# those confirmations.
WORKBENCH_ROUTES = {
    "/", "/ui.css", "/static/<path:filename>",
    "/api/upload-marc", "/api/detect", "/api/test-regex",
    "/api/pattern-preview", "/api/patterns",
    "/api/patterns/export", "/api/patterns/import",
    "/api/preview-record", "/api/preview-records", "/api/review-index",
    "/api/convert-record",
    "/api/batch-convert", "/api/download-converted",
}


def _rules(flask_app) -> set[str]:
    return {r.rule for r in flask_app.url_map.iter_rules()}


def test_apps_are_distinct_objects(converter_app, detector_app, workbench_app):
    """The whole point: three Flask apps, not one shadowing the others."""
    apps = [converter_app.app, detector_app.app, workbench_app.app]
    assert len({id(a) for a in apps}) == 3


def test_no_bare_app_module_is_registered(converter_app, detector_app, workbench_app):
    """
    A plain `import app` anywhere in the suite would bind whichever app loaded
    first and hand it to every later importer. Nothing should own that name.
    """
    assert "app" not in sys.modules
    assert "converter_app" in sys.modules
    assert "detector_app" in sys.modules
    assert "workbench_app" in sys.modules


def test_converter_routes(converter_app):
    """Asserted as a set so an added or renamed route fails informatively."""
    assert _rules(converter_app.app) == CONVERTER_ROUTES


def test_detector_routes(detector_app):
    assert _rules(detector_app.app) == DETECTOR_ROUTES


def test_workbench_routes(workbench_app):
    assert _rules(workbench_app.app) == WORKBENCH_ROUTES


def test_all_apps_found_pymarc(converter_app, detector_app, workbench_app):
    """
    Without pymarc, half of each API turns into 500s. Asserting it here turns
    a wall of confusing failures into one clear one.
    """
    assert converter_app.HAS_PYMARC is True
    assert detector_app.HAS_PYMARC is True
    assert workbench_app.HAS_PYMARC is True


def test_each_app_resolves_its_own_templates(converter_app, detector_app,
                                            workbench_app):
    """
    Both apps set template_folder relative to their own file location, which is
    what makes `run each app from its own directory` work. spec_from_file_location
    sets __file__, so this survives the aliased import -- but only if the shim
    keeps doing so.
    """
    assert converter_app.app.root_path.endswith("converter")
    assert detector_app.app.root_path.endswith("pattern-detector")
    assert workbench_app.app.root_path.endswith("workbench")


def test_all_apps_serve_the_same_shared_stylesheet(converter_client, detector_client,
                                                  workbench_client):
    """shared/ui.css is served by all three apps and must be the identical file."""
    responses = [converter_client.get("/ui.css"), detector_client.get("/ui.css"),
                 workbench_client.get("/ui.css")]
    assert all(r.status_code == 200 for r in responses)
    assert len({r.data for r in responses}) == 1


def test_all_apps_report_the_same_version(converter_app, detector_app, workbench_app):
    """
    shared/about.json is the single source for the version badge. Every app reads
    it per request, so a mismatch means one of them is resolving a different file.
    """
    versions = {m._load_about().get("version")
                for m in (converter_app, detector_app, workbench_app)}
    assert versions and None not in versions
    assert len(versions) == 1


def test_the_workbench_page_carries_its_fold_controls(workbench_client):
    """
    The step-2 fold and the skip controls are client-side, so nothing else in
    this suite would notice them going missing -- and a template that renders
    without them renders fine, just missing the controls. Pin the handful of
    hooks the script drives.
    """
    page = workbench_client.get("/").get_data(as_text=True)
    for anchor in ('id="btn-toggle-patterns"', 'id="patterns-body"',
                   'id="patterns-summary"', 'aria-controls="patterns-body"',
                   'class="rec-skip"', 'pc-skip', 'data-filter="skipped"',
                   'jump-to-pattern', 'id="review-notice"'):
        assert anchor in page, anchor


# A callback passed to .map() by name: the shape of the bug this catches.
_MAP_CALLBACK_RE = re.compile(r"\.map\(\s*([A-Za-z_$][\w$]*)\s*\)")

# How the templates declare a function, either form.
def _declared_names(script: str) -> set:
    return (set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", script))
            | set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", script)))


@pytest.mark.parametrize("template", sorted(
    p for p in (REPO_ROOT / "workbench" / "templates").glob("*.html")))
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
    of source ids in the template, and the ids live in pattern_bridge -- two
    copies of one list, so this pins them together.
    """
    from pattern_bridge import PARSER_SOURCE, SKIPPED_SOURCE, UNMATCHED_SOURCE

    script = (REPO_ROOT / "workbench" / "templates" / "tool.html").read_text(
        encoding="utf-8")
    match = re.search(r"const NOT_A_PATTERN = new Set\(\[([^\]]*)\]\)", script)
    assert match, "the summary no longer names the non-pattern sources"

    in_template = set(re.findall(r"'([^']+)'", match.group(1)))
    assert in_template == {PARSER_SOURCE, UNMATCHED_SOURCE, SKIPPED_SOURCE}


def test_index_pages_render(converter_client, detector_client, workbench_client):
    assert converter_client.get("/").status_code == 200
    assert detector_client.get("/").status_code == 200
    assert workbench_client.get("/").status_code == 200


def test_workbench_reuses_the_other_apps_engines(workbench_app, converter_app,
                                                 detector_app):
    """
    The workbench must *import* the engines, not carry copies of them.

    A second copy of the parser or the pattern detector would drift from the
    original the first time either was fixed, and the two tools would start
    disagreeing about the same statement.
    """
    assert workbench_app.parse_866 is converter_app.parse_866
    assert workbench_app.convert_record is converter_app.convert_record
    assert workbench_app.detect_patterns is detector_app.detect_patterns
