"""
Shared fixtures for the MARC Serials Toolkit test suite.

Every engine lives in the ``marc_serials`` package and is reached by ordinary
import, here and in the applications alike. Nothing in this file puts an
application directory on ``sys.path``, and no test needs it to.

One wrinkle remains. The three Flask applications each define a module called
``app``, so a plain ``import app`` would cache the first one in ``sys.modules``
and silently hand it to whoever asked second. They are therefore loaded from
their paths under distinct aliases by ``_load_app_module``.

Do NOT write ``import app`` in a test module -- it would bind whichever app
happened to load first. Use the ``converter_app`` / ``detector_app`` /
``workbench_app`` fixtures; tests/test_app_isolation.py guards this.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Repository geometry
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
CONVERTER_DIR = REPO_ROOT / "converter"
DETECTOR_DIR = REPO_ROOT / "pattern-detector"
WORKBENCH_DIR = REPO_ROOT / "workbench"
DATA_DIR = REPO_ROOT / "data"

EXAMPLE_MRC = DATA_DIR / "example_holdings.mrc"
MESSY_MRC = DATA_DIR / "messy_holdings.mrc"

# The repository root, so `import marc_serials` resolves when the suite is run
# against a clone that has not been pip-installed. Stated rather than relied on:
# pytest's own rootdir insertion would cover it today, but that is a property of
# how the suite happens to be invoked, not something the tests should assume.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Imported under its old bare name because the fixture below patches this one
# module object, and every caller resolves the budget from it at call time.
import marc_serials.budget as regex_budget                                    # noqa: E402
import marc_serials.store as store                                            # noqa: E402


# ---------------------------------------------------------------------------
# Loading the two colliding app modules
# ---------------------------------------------------------------------------

def _load_app_module(alias: str, path: Path) -> ModuleType:
    """
    Execute `path` as a top-level module registered under `alias`.

    Registering in sys.modules *before* exec_module is what the import system
    itself does, and it keeps dataclasses defined in the module correctly
    __module__-tagged. Loading is cached because converter/app.py builds its
    Flask object and calls os.makedirs at import; doing that once per session
    keeps route registration and the upload directory stable.
    """
    cached = sys.modules.get(alias)
    if cached is not None:
        return cached

    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:      # pragma: no cover - env bug
        raise ImportError(f"Could not build an import spec for {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # Do not leave a half-executed module behind for the next test to find.
        sys.modules.pop(alias, None)
        raise
    return module


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
    Redirect the converter's upload directory *before* its app.py is imported.

    converter/app.py reads MARC_UPLOAD_DIR into a module-level global at import
    time and immediately os.makedirs it, so setting the variable afterwards
    would be too late for that mkdir. Per-test isolation is handled separately
    in `converter_client`, because _file_path() re-reads the global on every
    call and can therefore be monkeypatched.
    """
    root = tmp_path_factory.mktemp("marc_upload_root")
    os.environ["MARC_UPLOAD_DIR"] = str(root)
    return root


@pytest.fixture(scope="session")
def converter_app(_upload_root: Path) -> ModuleType:
    """The converter's app.py, loaded under the alias `converter_app`."""
    return _load_app_module("converter_app", CONVERTER_DIR / "app.py")


@pytest.fixture(scope="session")
def workbench_app(_upload_root: Path) -> ModuleType:
    """
    The workbench's app.py, loaded under the alias `workbench_app`.

    Depends on `_upload_root` for the same reason the converter does: it reads
    MARC_UPLOAD_DIR into a module-level global at import and os.makedirs it
    immediately, so the redirection has to happen first.
    """
    return _load_app_module("workbench_app", WORKBENCH_DIR / "app.py")


@pytest.fixture(scope="session")
def detector_app() -> ModuleType:
    """
    The pattern detector's app.py, loaded under the alias `detector_app`.

    Stateless: no session, no tempfiles, no import-time environment reads, so it
    needs none of the upload plumbing the converter does.
    """
    return _load_app_module("detector_app", DETECTOR_DIR / "app.py")


# ---------------------------------------------------------------------------
# Flask test clients
# ---------------------------------------------------------------------------

@pytest.fixture
def converter_client(converter_app, tmp_path: Path, monkeypatch):
    """
    A converter test client with an upload directory of its own.

    Two layers of isolation, and both are needed. Each test client carries its
    own cookie jar, so it gets its own Flask session and therefore its own
    {uuid}.mrc -- state cannot leak between tests through the session. But the
    store is a process-wide global shared by every client, so repointing it per
    test stops the sweep from walking another test's working set and lets a test
    assert on directory contents without seeing its neighbours'.

    Patched on marc_serials.store, which is where the directory now lives. The
    applications used to declare one each, and defaulting both to the same path
    is what let the converter's sweep delete the workbench's pattern libraries.
    """
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(store, "UPLOAD_DIR", str(upload_dir))

    # A plain client, not `with app.test_client() as client`. The context-manager
    # form preserves each request's context until the fixture ends, and two
    # preserved contexts from different apps pop out of order the moment a test
    # uses both clients -- which the workbench/converter equivalence tests do.
    # Nothing here needs the preserved context; the cookie jar, and so the Flask
    # session, lives on the client either way.
    converter_app.app.config.update(TESTING=True, SECRET_KEY="test-secret-key")
    return converter_app.app.test_client()


@pytest.fixture
def workbench_client(workbench_app, tmp_path: Path, monkeypatch):
    """
    A workbench test client with an upload directory of its own.

    Isolated the same way the converter's client is, and for the same reasons --
    but the workbench also stores its pattern library there, so without the
    redirect one test's confirmed patterns would convert another test's
    holdings.
    """
    # A directory of its own per test. It no longer has to be named apart from
    # the converter's: there is one sweep now, and it ages a library by the
    # library limit whichever application happens to run it.
    upload_dir = tmp_path / "workbench-uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(store, "UPLOAD_DIR", str(upload_dir))

    workbench_app.app.config.update(TESTING=True, SECRET_KEY="test-secret-key")
    return workbench_app.app.test_client()      # plain: see converter_client


@pytest.fixture
def detector_client(detector_app):
    """A pattern-detector test client. The app holds no state to isolate."""
    detector_app.app.config.update(TESTING=True)
    return detector_app.app.test_client()       # plain: see converter_client


# ---------------------------------------------------------------------------
# MARC corpora
# ---------------------------------------------------------------------------

def upload_marc(client, data: bytes, filename: str = "corpus.mrc"):
    """POST bytes to an app's /api/upload-marc as a multipart file part."""
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
# against two files on a USC SMB share that are not, and must not be, in the
# repository.
# They are reached through an environment variable naming the mounted
# directory, so the suite is fully green on a clean clone and *additionally*
# pins exact counts on a machine where the share is mounted:
#
#     export MARC_TEST_DATA_DIR=/Volumes/rfolders/codey/.../serials-enhancement
#
# An unset variable and an unmounted share produce the same clean skip.
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
