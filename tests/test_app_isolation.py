"""
Guards the import scheme that lets both Flask apps live in one interpreter.

Every other API test depends on this working. If the two apps ever start
sharing a module object, or one of them silently claims the bare name `app`,
the failures elsewhere would be baffling -- so they are caught here instead.
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


def _rules(flask_app) -> set[str]:
    return {r.rule for r in flask_app.url_map.iter_rules()}


def test_apps_are_distinct_objects(converter_app, detector_app):
    """The whole point: two Flask apps, not one shadowing the other."""
    assert converter_app.app is not detector_app.app


def test_no_bare_app_module_is_registered(converter_app, detector_app):
    """
    A plain `import app` anywhere in the suite would bind whichever app loaded
    first and hand it to every later importer. Nothing should own that name.
    """
    assert "app" not in sys.modules
    assert "converter_app" in sys.modules
    assert "detector_app" in sys.modules


def test_converter_routes(converter_app):
    """Asserted as a set so an added or renamed route fails informatively."""
    assert _rules(converter_app.app) == CONVERTER_ROUTES


def test_detector_routes(detector_app):
    assert _rules(detector_app.app) == DETECTOR_ROUTES


def test_both_apps_found_pymarc(converter_app, detector_app):
    """
    Without pymarc, half of each API turns into 500s. Asserting it here turns
    a wall of confusing failures into one clear one.
    """
    assert converter_app.HAS_PYMARC is True
    assert detector_app.HAS_PYMARC is True


def test_each_app_resolves_its_own_templates(converter_app, detector_app):
    """
    Both apps set template_folder relative to their own file location, which is
    what makes `run each app from its own directory` work. spec_from_file_location
    sets __file__, so this survives the aliased import -- but only if the shim
    keeps doing so.
    """
    assert converter_app.app.root_path.endswith("converter")
    assert detector_app.app.root_path.endswith("pattern-detector")


def test_both_apps_serve_the_same_shared_stylesheet(converter_client, detector_client):
    """shared/ui.css is served by both apps and must be the identical file."""
    a = converter_client.get("/ui.css")
    b = detector_client.get("/ui.css")
    assert a.status_code == b.status_code == 200
    assert a.data == b.data


def test_both_apps_report_the_same_version(converter_app, detector_app):
    """
    shared/about.json is the single source for the version badge. Both apps read
    it per request, so a mismatch means one of them is resolving a different file.
    """
    conv = converter_app._load_about()
    det = detector_app._load_about()
    assert conv.get("version")
    assert conv["version"] == det["version"]


def test_index_pages_render(converter_client, detector_client):
    assert converter_client.get("/").status_code == 200
    assert detector_client.get("/").status_code == 200
