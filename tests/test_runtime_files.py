"""Runtime data files: created on demand, never tracked, safely backed up.

The database, its WAL companions and the uploads directory are all runtime
artefacts. A fresh clone contains none of them and must still start.
"""

import os
import subprocess
import sys

import pytest

from phantom_qa.store import Store

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_store_creates_everything_it_needs(tmp_path):
    """A clone with an empty data/ must come up without manual setup."""
    assert not (tmp_path / "data").exists()
    s = Store(str(tmp_path))
    assert (tmp_path / "data" / "uploads").is_dir()
    assert os.path.exists(s.db_path)
    assert s.list_all() == []


def test_wal_mode_is_enabled(tmp_path):
    """WAL is what lets gunicorn's workers read while one writes."""
    s = Store(str(tmp_path))
    with s._conn() as c:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_wal_companions_exist_only_while_connected(tmp_path):
    """-wal and -shm live beside the database while a connection is open and
    are cleaned up by SQLite when the last one closes. They are pure runtime
    state: deleting them between runs loses nothing."""
    from test_store_labels import fake_scan
    s = Store(str(tmp_path))
    s.new_analysis(fake_scan(), b"x", "sig", "1", "1", labels={})

    c = s._connect()                       # hold one open, as a worker does
    try:
        c.execute("SELECT 1").fetchone()
        assert os.path.exists(s.db_path + "-wal")
        assert os.path.exists(s.db_path + "-shm")
    finally:
        c.close()

    # with nothing connected the data lives entirely in the main file
    for suffix in ("-wal", "-shm"):
        p = s.db_path + suffix
        if os.path.exists(p):
            os.remove(p)
    s2 = Store(str(tmp_path))
    assert len(s2.list_all()) == 1, "no data was lost with the companions gone"


def test_connections_are_closed_not_just_committed(tmp_path):
    """`with sqlite3.connect(...)` commits but does NOT close, which would leak
    one file handle per request in a long-running gunicorn worker."""
    import gc
    s = Store(str(tmp_path))
    for _ in range(50):
        s.list_all()
    gc.collect()
    open_conns = [o for o in gc.get_objects()
                  if isinstance(o, __import__("sqlite3").Connection)]
    assert len(open_conns) < 10, (
        f"{len(open_conns)} sqlite connections still alive after 50 queries — "
        f"connections are not being closed")


def test_throttle_connections_are_closed(tmp_path):
    import gc
    from phantom_qa.security import SharedThrottle
    t = SharedThrottle(str(tmp_path / "t.sqlite3"), "login", 5, 15)
    for i in range(50):
        t.locked_for(f"client-{i}")
    gc.collect()
    open_conns = [o for o in gc.get_objects()
                  if isinstance(o, __import__("sqlite3").Connection)]
    assert len(open_conns) < 10, f"{len(open_conns)} throttle connections leaked"


def test_busy_timeout_is_set(tmp_path):
    """Without it a second worker gets 'database is locked' instead of waiting."""
    s = Store(str(tmp_path))
    with s._conn() as c:
        assert int(c.execute("PRAGMA busy_timeout").fetchone()[0]) >= 5000


def test_checkpoint_makes_the_db_self_contained(tmp_path):
    from test_store_labels import fake_scan, results_for
    s = Store(str(tmp_path))
    aid = s.new_analysis(fake_scan(), b"x", "sig", "1", "1", labels={})
    s.update(aid, results=results_for())
    s.checkpoint()
    wal = s.db_path + "-wal"
    assert not os.path.exists(wal) or os.path.getsize(wal) == 0


def test_backup_copy_is_complete_without_the_wal(tmp_path):
    """The documented backup must not depend on the -wal file."""
    import sqlite3
    from test_store_labels import fake_scan, results_for
    s = Store(str(tmp_path))
    aid = s.new_analysis(fake_scan(), b"payload", "sig", "1", "1",
                         labels={"site": "Goma", "phantom": "P1"})
    s.update(aid, results=results_for(), status="pass")

    dest = str(tmp_path / "backup" / "copy.sqlite3")
    s.backup_to(dest)
    assert os.path.exists(dest)
    # no companions needed to read it
    assert not os.path.exists(dest + "-wal")

    con = sqlite3.connect(dest)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id, site, phantom FROM analyses").fetchall()
    con.close()
    assert len(rows) == 1
    assert rows[0]["site"] == "Goma" and rows[0]["phantom"] == "P1"


def test_backup_taken_while_writing_is_consistent(tmp_path):
    """Backup uses SQLite's online backup API, so it is safe with the app up."""
    import sqlite3
    from test_store_labels import fake_scan
    s = Store(str(tmp_path))
    for i in range(5):
        s.new_analysis(fake_scan(sha=f"{i:064d}"), b"x", "sig", "1", "1",
                       labels={"site": f"S{i}"})
    dest = str(tmp_path / "b.sqlite3")
    s.backup_to(dest)
    s.new_analysis(fake_scan(sha="f" * 64), b"x", "sig", "1", "1", labels={})

    con = sqlite3.connect(dest)
    n = con.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
    con.close()
    assert n == 5, "the snapshot must reflect the moment it was taken"
    assert len(s.list_all()) == 6


# ------------------------------------------------------------------- gitignore

def _gitignore() -> str:
    with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("pattern", [
    "/data/*.sqlite3", "/data/*.sqlite3-wal", "/data/*.sqlite3-shm",
    "/data/uploads/", "/data/backup/", "/data/thumbs/", "logs/", ".env",
])
def test_runtime_paths_are_git_ignored(pattern):
    assert pattern in _gitignore(), f"{pattern} must be git-ignored"


def test_phantom_definition_is_not_ignored():
    """The calibrated geometry is source, and must stay in version control."""
    text = _gitignore()
    assert "/data/phantom_definitions" not in text
    assert os.path.exists(os.path.join(REPO, "data", "phantom_definitions",
                                       "msf_v1.json"))


def test_the_test_session_does_not_write_into_the_checkout():
    """Running the suite must leave the working tree's data/ untouched.

    It did not: the web module builds its Store at import time, so importing it
    created data/phantom_qa.sqlite3 and data/uploads/ in whatever checkout the
    tests ran from — and on a server that is the live database. conftest points
    PHANTOMQA_DATA_ROOT at a temp directory for the whole session; this asserts
    it is actually in force."""
    assert os.environ.get("PHANTOMQA_DATA_ROOT"), \
        "conftest must set PHANTOMQA_DATA_ROOT so imports cannot touch the repo"
    assert os.path.abspath(os.environ["PHANTOMQA_DATA_ROOT"]) != os.path.abspath(REPO)

    import phantom_qa.webapp.main as main           # the import under suspicion
    data = os.path.join(REPO, "data")
    assert os.path.dirname(main.store.db_path) != data, \
        f"the app store is inside the checkout: {main.store.db_path}"
    assert not os.path.exists(os.path.join(data, "phantom_qa.sqlite3")), \
        "importing the app created a database inside the checkout"
    assert not os.path.exists(os.path.join(data, "uploads")), \
        "importing the app created data/uploads inside the checkout"
    assert not os.path.exists(os.path.join(REPO, "logs")), \
        "importing the app created a logs/ tree inside the checkout"
    assert os.path.isdir(os.path.join(data, "phantom_definitions")), \
        "the tracked phantom definition must still be there"


def test_resolve_root_defaults_to_the_application_directory(monkeypatch):
    """Unset means today's behaviour, so no deployment changes."""
    from phantom_qa.store import DATA_ROOT_ENV, resolve_root
    monkeypatch.delenv(DATA_ROOT_ENV, raising=False)
    assert resolve_root("/srv/phantomqa") == "/srv/phantomqa"
    monkeypatch.setenv(DATA_ROOT_ENV, "/var/lib/phantomqa")
    assert resolve_root("/srv/phantomqa") == os.path.abspath("/var/lib/phantomqa")
    monkeypatch.setenv(DATA_ROOT_ENV, "   ")          # blank is not a path
    assert resolve_root("/srv/phantomqa") == "/srv/phantomqa"


@pytest.mark.skipif(not os.path.exists(os.path.join(REPO, ".git")),
                    reason="not a git repository")
def test_git_does_not_track_runtime_files():
    """Belt and braces: ask git itself what it is tracking."""
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                         text=True).stdout.splitlines()
    for path in out:
        assert not path.endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm")), \
            f"git is tracking a runtime database file: {path}"
        assert not path.startswith("data/uploads/"), \
            f"git is tracking an uploaded scan: {path}"
        assert not path.startswith("logs/"), f"git is tracking a log: {path}"
        assert path != ".env", "git is tracking the secrets file"
