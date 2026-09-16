"""
Shared fixtures for the MARC Serials Toolkit test suite.

There is one application and one package. Everything is reached by ordinary
import, and no fixture puts a directory on sys.path or loads a module from a
file path.

That was not always true. Until September 2026 the repository held three Flask
applications, each defining a module called ``app``, so a plain ``import app``
cached whichever loaded first and handed it to whoever asked second; one of them
lived in a directory with a hyphen in its name and could not be imported at all.
This file was mostly machinery for working around that.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Repository geometry
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"

EXAMPLE_MRC = DATA_DIR / "example_holdings.mrc"
MESSY_MRC = DATA_DIR / "messy_holdings.mrc"

import sys
# The repository root, so `import marc_serials` resolves when the suite is run
# against a clone that has not been pip-installed. Stated rather than relied on:
# pytest's own rootdir insertion would cover it today, but that is a property of
# how the suite happens to be invoked, not something the tests should assume.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The store is imported before the application so MARC_UPLOAD_DIR is read from
# the environment the session fixture below has already set.
import marc_serials.budget as regex_budget                                # noqa: E402
import marc_serials.store as store                                       # noqa: E402


@pytest.fixture(autouse=True)
def _short_match_budget(monkeypatch):
    """
    Shorten the regex budget for the whole suite.

    Several tests deliberately run an expression that never finishes, and each
    one costs its whole budget in wall clock. What is under test is the
    mechanism -- that a runaway expression is stopped and reported -- not the
    number, so the number is made small. The tests send a handful of statements,
    and an empty round trip through the child costs about 37 ms, so half a
    second is still more than ten times what any of them needs.

    Every caller resolves the budget from this module attribute at call time,
    which is why patching it here reaches the app routes as well.
    """
    monkeypatch.setattr(regex_budget, "MATCH_BUDGET_SECONDS", 0.5)


@pytest.fixture(scope="session")
def _upload_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Redirect the store *before* marc_serials.store is imported.

    store.py reads MARC_UPLOAD_DIR into a module-level global at import and
    immediately os.makedirs it, so setting the variable afterwards would be too
    late for that mkdir. Per-test isolation is handled separately in `client`,
    because every path is resolved from the global at call time and can
    therefore be monkeypatched.
    """
    root = tmp_path_factory.mktemp("marc_upload_root")
    os.environ["MARC_UPLOAD_DIR"] = str(root)
    return root


@pytest.fixture(scope="session")
def marc_app(_upload_root: Path):
    """The application module."""
    import marc_serials.webapp as webapp
    return webapp


# ---------------------------------------------------------------------------
# Flask test clients
# ---------------------------------------------------------------------------

def _make_client(marc_app, upload_dir: Path, monkeypatch):
    """
    A test client with a store of its own.

    Two layers of isolation, and both are needed. Each test client carries its
    own cookie jar, so it gets its own Flask session and therefore its own
    {uuid}.mrc -- state cannot leak between tests through the session. But the
    store is a process-wide global shared by every client, so repointing it per
    test stops the sweep from walking another test's working set and lets a test
    assert on directory contents without seeing its neighbours'.
    """
    monkeypatch.setattr(store, "UPLOAD_DIR", str(upload_dir))
    marc_app.app.config.update(TESTING=True, SECRET_KEY="test-secret-key")
    # A plain client, not `with app.test_client() as client`. The context-manager
    # form preserves each request's context until the fixture ends, and two
    # preserved contexts pop out of order the moment a test uses two clients.
    # Nothing here needs the preserved context; the cookie jar, and so the Flask
    # session, lives on the client either way.
    return marc_app.app.test_client()


@pytest.fixture
def client(marc_app, tmp_path: Path, monkeypatch):
    """The application's test client."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    return _make_client(marc_app, upload_dir, monkeypatch)


@pytest.fixture
def second_client(marc_app, client):
    """
    A second client against the same application, with its own cookie jar.

    For the tests that check one cataloguer's upload cannot be reached from
    another's session. It shares the store deliberately: separate sessions on
    one running server is exactly the situation being tested.
    """
    return marc_app.app.test_client()


# ---------------------------------------------------------------------------
# MARC corpora
# ---------------------------------------------------------------------------

def upload_marc(client, data: bytes, filename: str = "corpus.mrc"):
    """POST bytes to /api/upload-marc as a multipart file part."""
    return client.post(
        "/api/upload-marc",
        data={"file": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


@pytest.fixture(scope="session")
def example_marc_bytes() -> bytes:
    """
    The committed synthetic corpus, data/example_holdings.mrc.

    .gitignore excludes *.mrc deliberately, with named exceptions, so that real
    library holdings never reach the repository. Both committed corpora are
    generated by scripts/ and contain only invented titles.
    """
    return EXAMPLE_MRC.read_bytes()


@pytest.fixture(scope="session")
def messy_marc_bytes() -> bytes:
    """The synthetic 'unkempt' corpus, data/messy_holdings.mrc."""
    if not MESSY_MRC.exists():       # pragma: no cover - regenerate and re-run
        pytest.fail(
            f"{MESSY_MRC} is missing. Regenerate it with:\n"
            f"    python scripts/create_messy_mrc.py"
        )
    return MESSY_MRC.read_bytes()


# ---------------------------------------------------------------------------
# The private corpus
#
# The historical verification numbers in test_calibration.py were measured
# against two files of real library holdings that are not, and must not be, in
# the repository.
# They are reached through an environment variable naming the directory they
# sit in, so the suite is fully green on a clean clone and *additionally* pins
# exact counts on a machine that has them:
#
#     export MARC_TEST_DATA_DIR=/path/to/holdings
#
# An unset variable and a directory that is not there produce the same clean
# skip.
# ---------------------------------------------------------------------------

WELLFORMED_NAME = "test_extract_10per.mrc"
UNKEMPT_NAME = "TEST_50records_0615_853-1.mrc"


def private_marc(filename: str) -> Optional[Path]:
    """
    Resolve one private corpus file, or None when it is not reachable.

    Returns None both when MARC_TEST_DATA_DIR is unset and when it points
    somewhere the file is not -- an unmounted share and an unset variable should
    produce the same clean skip, not a confusing FileNotFoundError.
    """
    raw = os.environ.get("MARC_TEST_DATA_DIR", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser() / filename
    return path if path.is_file() else None


def available_corpora() -> list:
    """
    Every MARC corpus this machine can reach, as parametrize entries.

    Always includes the two committed synthetic corpora, so the invariant tests
    have real work to do on a clean clone. The private files join the list only
    when they resolve, which is why the invariant tests never need a skip: they
    run against whatever exists.
    """
    params = [
        pytest.param(EXAMPLE_MRC, id="example"),
        pytest.param(MESSY_MRC, id="messy"),
    ]
    for name, label in ((WELLFORMED_NAME, "wellformed"), (UNKEMPT_NAME, "unkempt")):
        path = private_marc(name)
        if path is not None:
            params.append(pytest.param(path, id=label))
    return params


@pytest.fixture(params=available_corpora())
def any_corpus(request) -> bytes:
    """MARC bytes for one available corpus; invariant tests fan out over all."""
    path: Path = request.param
    if not path.exists():            # pragma: no cover - regenerate and re-run
        pytest.fail(f"{path} is missing; run python scripts/create_messy_mrc.py")
    return path.read_bytes()
