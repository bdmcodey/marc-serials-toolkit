"""
Things about the source that a different Python version would notice first.

This one was reported from a Windows machine running Python 3.12, where
`SyntaxWarning: invalid escape sequence '\\s'` prints on every start. The
development container runs 3.11, where the same problem is a DeprecationWarning
and silent by default, so nothing here caught it: a docstring in
marc_serials/detector.py quoted a regex without being a raw string.

Escape sequences that Python does not recognise have been deprecated for years
and are slated to become errors, so this is a real defect and not a style
preference. Asserted for every file, at the strictest setting, so the suite
fails wherever it is run rather than only where the warning happens to be loud.
"""

from __future__ import annotations

import pathlib
import warnings

import pytest

from conftest import REPO_ROOT


def _python_files() -> list[pathlib.Path]:
    return sorted(p for p in REPO_ROOT.rglob("*.py")
                  if "__pycache__" not in p.parts
                  and ".git" not in p.parts
                  and ".venv" not in p.parts)


def test_there_are_files_to_check():
    """A glob that silently matches nothing would make the test below vacuous."""
    assert len(_python_files()) >= 15


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_no_invalid_escape_sequences(path: pathlib.Path):
    """
    A string that means to hold a backslash must say so.

    Either a raw string (r"\\s*") or a doubled backslash. The usual way this
    slips in is a docstring quoting a regex, which is exactly what happened.
    """
    source = path.read_text(encoding="utf-8")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        compile(source, str(path), "exec")
    problems = [f"line {w.lineno}: {w.message}" for w in caught
                if issubclass(w.category, (SyntaxWarning, DeprecationWarning))
                and "escape sequence" in str(w.message)]
    assert not problems, f"{path.relative_to(REPO_ROOT)} -> {problems}"
