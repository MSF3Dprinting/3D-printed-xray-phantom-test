import atexit
import os
import shutil
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# --------------------------------------------------------- runtime isolation
# phantom_qa.webapp.main builds its Store at import time, so merely importing it
# — which several tests do, directly or through importlib.reload — used to
# create and migrate data/phantom_qa.sqlite3 inside the checkout. Harmless here,
# not harmless on a server, where the checkout holds the live database. Pointing
# the data root at a throwaway directory for the whole session closes that off
# no matter which test imports the app first.
#: What the checkout already held before any test ran. Running the app from the
#: checkout — the ordinary way to try it out locally — puts a database, uploads
#: and logs there, legitimately. The guard in test_runtime_files has to tell
#: "the tests created it" from "it was already there", or trying the app once
#: makes the whole suite fail. Taken here, before anything imports the app.
CHECKOUT_RUNTIME_AT_START = {
    rel: os.path.exists(os.path.join(ROOT, rel))
    for rel in (os.path.join("data", "phantom_qa.sqlite3"),
                os.path.join("data", "uploads"), "logs")
}

_DATA_ROOT = tempfile.mkdtemp(prefix="phantomqa-tests-")
os.environ["PHANTOMQA_DATA_ROOT"] = _DATA_ROOT
# The log directory is opened at import time too, so a bare `import
# phantom_qa.webapp.main` (a couple of tests do exactly that for a constant)
# used to leave a logs/ tree in the checkout. Tests that care about log
# contents set PHANTOMQA_LOG_DIR themselves; this is only the fallback.
os.environ.setdefault("PHANTOMQA_LOG_DIR", os.path.join(_DATA_ROOT, "logs"))
atexit.register(shutil.rmtree, _DATA_ROOT, True)

# ---------------------------------------------------------------- sample scans
# The two reference DICOMs the sample-based tests measure against. They are not
# in git (large, and they carry site-identifying tags), so every test that needs
# them is skipped when they are absent — which is correct on a build machine and
# a silent hole anywhere else. Two things make that hole visible:
#
#   * the folder is looked up in several places instead of one hard-coded path,
#     so moving the scan drops does not quietly disable a third of the suite
#     (it did: they moved under "HQ testing/" and 35 tests skipped unnoticed);
#   * whatever the outcome, the end of the run says so (see the summary hook).
#
# PHANTOMQA_SAMPLE_DIR overrides the search and must point at the directory that
# holds the two numbered sub-folders (…/10000000/10000001).
_SAMPLE_MEMBERS = (("10000002", "10000003"), ("10000004", "10000005"))
_EXPORT_TAIL = os.path.join(
    "MSF", "Export_2026-07-27_10-00-05_1", "10000000", "10000001")
_CANDIDATES = [
    # current location
    os.path.join(ROOT, "HQ testing", "20260727 MSF", _EXPORT_TAIL),
    # where the drop lived before 2026-09-20; kept so an older checkout works
    os.path.join(ROOT, "20260727 MSF", _EXPORT_TAIL),
]


def _samples_in(directory):
    if not directory:
        return None
    paths = [os.path.join(directory, a, b) for a, b in _SAMPLE_MEMBERS]
    return paths if all(os.path.exists(p) for p in paths) else None


_ENV_DIR = os.environ.get("PHANTOMQA_SAMPLE_DIR", "").strip()
SAMPLE_DIR = _ENV_DIR or _CANDIDATES[0]
SAMPLES = _samples_in(_ENV_DIR)
if SAMPLES is None and not _ENV_DIR:
    for _cand in _CANDIDATES:
        SAMPLES = _samples_in(_cand)
        if SAMPLES:
            SAMPLE_DIR = _cand
            break
HAVE_SAMPLES = SAMPLES is not None
if SAMPLES is None:                      # keep the names usable for messages
    SAMPLES = [os.path.join(SAMPLE_DIR, a, b) for a, b in _SAMPLE_MEMBERS]

SKIP_REASON = "sample DICOM scans not present"
needs_samples = pytest.mark.skipif(not HAVE_SAMPLES, reason=SKIP_REASON)

#: Set PHANTOMQA_REQUIRE_SAMPLES=1 for a release run: missing scans then stop
#: the session instead of skipping a third of the suite.
_REQUIRE = os.environ.get("PHANTOMQA_REQUIRE_SAMPLES", "").strip().lower() \
    in ("1", "true", "yes", "on")


def _searched():
    if _ENV_DIR:
        return f"  PHANTOMQA_SAMPLE_DIR = {_ENV_DIR}"
    return "\n".join(f"  {c}" for c in _CANDIDATES)


def pytest_configure(config):
    if _REQUIRE and not HAVE_SAMPLES:
        raise pytest.UsageError(
            "PHANTOMQA_REQUIRE_SAMPLES is set but the reference scans were not "
            "found. Looked in:\n" + _searched()
            + "\nPoint PHANTOMQA_SAMPLE_DIR at the folder holding "
              "10000002/10000003 and 10000004/10000005.")


def pytest_terminal_summary(terminalreporter):
    """Say out loud whether the sample-based tests actually ran.

    A skipped test is reported in the same muted colour as a passing one, so a
    suite that lost its scans still reads as green. This prints one unmissable
    line at the very end instead."""
    if HAVE_SAMPLES:
        return
    skipped = terminalreporter.stats.get("skipped", [])
    n = 0
    for rep in skipped:
        longrepr = getattr(rep, "longrepr", None)
        reason = longrepr[2] if isinstance(longrepr, tuple) and len(longrepr) > 2 \
            else str(longrepr)
        if SKIP_REASON in str(reason):
            n += 1
    if not n:
        return
    terminalreporter.write_sep("=", f"{n} SAMPLE-BASED TESTS SKIPPED", red=True)
    terminalreporter.write_line(
        f"The reference scans were not found, so {n} tests that measure real "
        "images did not run.")
    terminalreporter.write_line("Looked in:")
    terminalreporter.write_line(_searched())
    terminalreporter.write_line(
        "Set PHANTOMQA_SAMPLE_DIR to the folder holding 10000002/10000003, or "
        "PHANTOMQA_REQUIRE_SAMPLES=1 to make this an error.")


@pytest.fixture(scope="session")
def rng():
    return np.random.default_rng(42)


@pytest.fixture(scope="session")
def sample_scans():
    from phantom_qa import ingest
    return [ingest.load_path(p)[0] for p in SAMPLES]


@pytest.fixture(scope="session")
def pdef():
    from phantom_qa.phantom_def import load_default
    return load_default()
