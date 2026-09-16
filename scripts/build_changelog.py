#!/usr/bin/env python3
"""
Write CHANGELOG.md from marc_serials/shared/about.json.

about.json is the source of truth: the application reads it for the version
badge and the "What has changed" panel, so it is the copy that is kept current.
A hand-written changelog beside it would be a second copy of one list, and this
project has spent enough time fixing those.

    python scripts/build_changelog.py            # write CHANGELOG.md
    python scripts/build_changelog.py --check     # exit 1 if it is out of date

tests/test_changelog.py runs the check, so a release that edits about.json and
forgets the changelog fails the suite rather than shipping a stale file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ABOUT = REPO_ROOT / "marc_serials" / "shared" / "about.json"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

PREAMBLE = """<!--
  Generated from marc_serials/shared/about.json by scripts/build_changelog.py.
  Edit that file, not this one, and run the script to regenerate.
-->

# Changelog

Every released version, newest first. The wording is written for cataloguing
staff rather than developers: each entry says what changed about the output or
the screen, not how the code changed.

A note on the version numbers. They stood still for a week in August 2026 while
the three separate tools were being built out, so the 0.6.0 entry covers rather
more than one release; numbering resumed with 0.6.1. Reasoning behind the
parser and converter decisions, including the defects a real corpus exposed and
what was done about each, is in [CORPUS-FINDINGS.md](CORPUS-FINDINGS.md).
"""


def render(about: dict) -> str:
    out = [PREAMBLE]
    for entry in about.get("changelog", []):
        version = entry.get("version", "?")
        date = entry.get("date", "")
        out.append(f"\n## {version} — {date}\n\n")
        summary = (entry.get("summary") or "").strip()
        if summary:
            out.append(f"{summary}\n\n")
        for change in entry.get("changes", []):
            tool = (change.get("tool") or "").strip()
            text = " ".join((change.get("text") or "").split())
            out.append(f"- **{tool}** — {text}\n" if tool else f"- {text}\n")
    return "".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if CHANGELOG.md is out of date")
    args = parser.parse_args()

    about = json.loads(ABOUT.read_text(encoding="utf-8"))
    wanted = render(about)

    if args.check:
        current = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else ""
        if current == wanted:
            print(f"CHANGELOG.md is current ({len(about.get('changelog', []))} versions).")
            return 0
        print("CHANGELOG.md is out of date. Regenerate it with:\n"
              "    python scripts/build_changelog.py", file=sys.stderr)
        return 1

    CHANGELOG.write_text(wanted, encoding="utf-8")
    print(f"Wrote {CHANGELOG.relative_to(REPO_ROOT)} "
          f"({len(about.get('changelog', []))} versions).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
