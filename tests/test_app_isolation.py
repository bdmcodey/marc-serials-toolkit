"""
Guards the import scheme that lets all three Flask apps live in one interpreter.

Every other API test depends on this working. If two apps ever start sharing a
module object, or one of them silently claims the bare name `app`, the failures
elsewhere would be baffling -- so they are caught here instead.
"""

from __future__ import annotations

import sys

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
    "/api/preview-record", "/api/preview-records", "/api/convert-record",
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
