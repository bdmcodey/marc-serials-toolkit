"""
CHANGELOG.md is generated, and must stay current.

about.json is the source of truth: the application reads it for the version
badge and the "What has changed" panel, so it is the copy that gets kept up to
date. CHANGELOG.md exists for people reading the repository rather than running
the tool -- and a repository seeded fresh has no commit history to tell the
story, so it is the only place the story is told.

Two copies of one list drift. This is the test that stops them.
"""

from __future__ import annotations

import json
import subprocess
import sys

from conftest import REPO_ROOT

ABOUT = REPO_ROOT / "marc_serials" / "shared" / "about.json"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
BUILDER = REPO_ROOT / "scripts" / "build_changelog.py"


def test_the_changelog_is_current():
    """
    Regenerate it with:

        python scripts/build_changelog.py
    """
    done = subprocess.run([sys.executable, str(BUILDER), "--check"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr or done.stdout


def test_every_released_version_appears():
    """
    The check above compares whole files, which would also pass if the builder
    and the changelog were wrong in the same way. This asserts the thing that
    actually matters, from the data rather than from the renderer.
    """
    about = json.loads(ABOUT.read_text(encoding="utf-8"))
    text = CHANGELOG.read_text(encoding="utf-8")

    versions = [entry["version"] for entry in about["changelog"]]
    assert versions, "about.json carries no changelog"
    missing = [v for v in versions if f"## {v} — " not in text]
    assert not missing, f"not in CHANGELOG.md: {missing}"


def test_the_current_version_is_the_newest_entry():
    """A release that bumps the version and forgets the entry is the usual slip."""
    about = json.loads(ABOUT.read_text(encoding="utf-8"))
    assert about["changelog"][0]["version"] == about["version"]
