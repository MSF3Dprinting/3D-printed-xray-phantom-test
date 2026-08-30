"""An upgrade must never cost a deployed installation its data.

This release adds two tables and two columns. The database it will meet in the
field was created by an earlier release, is several megabytes of analyses, and
cannot be recreated — the scans behind it were taken months ago in places the
software will not be run again. So the migration is exercised here against
hand-built databases in each of the shapes previous releases actually produced.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from phantom_qa.store import Store

#: The `analyses` table exactly as the first release created it: before the
#: site / phantom / operator / notes / acquired_at columns, before validation,
#: and before this release's geometry_seq / layout_source.
_ORIGINAL_TABLE = """
CREATE TABLE analyses (
  id TEXT PRIMARY KEY, created_at TEXT, source_name TEXT, sha256 TEXT,
  kind TEXT, reduced_precision INTEGER, signature TEXT, meta_json TEXT,
  stage TEXT, reg_json TEXT, geometry_json TEXT, results_json TEXT,
  audit_json TEXT, sid_mm REAL, is_baseline INTEGER DEFAULT 0,
  algo_version TEXT, pdef_version TEXT, status TEXT
);
"""

#: The shape after the labels release but before validation.
_LABELS_TABLE = _ORIGINAL_TABLE.rstrip().rstrip(";").rstrip().rstrip(")") + """,
  site TEXT DEFAULT '', phantom TEXT DEFAULT '', operator TEXT DEFAULT '',
  notes TEXT DEFAULT '', acquired_at TEXT DEFAULT ''
);
"""


def _legacy_db(root: str, table_sql: str, rows: list[dict]) -> str:
    os.makedirs(os.path.join(root, "data", "uploads"), exist_ok=True)
    path = os.path.join(root, "data", "phantom_qa.sqlite3")
    con = sqlite3.connect(path)
    con.executescript(table_sql)
    for r in rows:
        cols = ", ".join(r)
        marks = ", ".join("?" for _ in r)
        con.execute(f"INSERT INTO analyses ({cols}) VALUES ({marks})",
                    list(r.values()))
    con.commit()
    con.close()
    return path


def _columns(db_path: str, table: str) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def _tables(db_path: str) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


@pytest.mark.parametrize("table_sql,label", [(_ORIGINAL_TABLE, "first release"),
                                             (_LABELS_TABLE, "labels release")])
def test_an_existing_database_gains_everything_new(tmp_path, table_sql, label):
    row = {"id": "old1", "created_at": "2026-01-02 03:04:05",
           "source_name": "scan.dcm", "sha256": "a" * 64, "kind": "dicom",
           "reduced_precision": 0, "signature": "sig", "stage": "F",
           "status": "pass", "algo_version": "0.9", "pdef_version": "1.0",
           "results_json": json.dumps({"uniformity": {"status": "pass"}}),
           "geometry_json": json.dumps({"uniformity": {"squares": []}})}
    db = _legacy_db(str(tmp_path), table_sql, [row])

    store = Store(str(tmp_path))          # the upgrade happens here

    assert {"analyses", "geometry_history", "phantom_profiles"} <= _tables(db)
    cols = _columns(db, "analyses")
    assert {"site", "phantom", "operator", "notes", "acquired_at",
            "validation_status", "validated_by", "validation_comment",
            "validated_at", "geometry_seq", "layout_source"} <= cols, label

    rec = store.get("old1")
    assert rec is not None, f"the {label} row did not survive the upgrade"
    assert rec["source_name"] == "scan.dcm"
    assert rec["results"]["uniformity"]["status"] == "pass"
    assert rec["geometry"]["uniformity"]["squares"] == []


def test_the_upgrade_is_idempotent(tmp_path):
    db = _legacy_db(str(tmp_path), _ORIGINAL_TABLE,
                    [{"id": "old1", "created_at": "2026-01-02 03:04:05",
                      "source_name": "s", "sha256": "b" * 64}])
    for _ in range(3):
        Store(str(tmp_path))
    assert Store(str(tmp_path)).get("old1") is not None
    assert {"analyses", "geometry_history", "phantom_profiles"} <= _tables(db)


def test_a_legacy_row_becomes_undoable_without_a_data_migration(tmp_path):
    """No history rows exist for rows written by an earlier release. The first
    edit must seed a baseline from whatever geometry they hold, so the operator
    can undo it — rather than the edit being unrecoverable."""
    _legacy_db(str(tmp_path), _LABELS_TABLE,
               [{"id": "old1", "created_at": "2026-01-02 03:04:05",
                 "source_name": "s", "sha256": "c" * 64, "phantom": "MSF-01",
                 "geometry_json": json.dumps({"u": {"c": 1.0}})}])
    store = Store(str(tmp_path))
    assert store.geometry_state("old1") == {
        "seq": 0, "undo_depth": 0, "redo_depth": 0, "has_auto_proposal": False}

    store.mutate_geometry("old1", lambda g: g["u"].__setitem__("c", 2.0),
                          action="roi")
    assert store.geometry_state("old1")["undo_depth"] == 1
    back = store.undo_geometry("old1")
    assert back["geometry"]["u"]["c"] == 1.0


def test_a_legacy_row_can_carry_a_layout_immediately(tmp_path):
    _legacy_db(str(tmp_path), _LABELS_TABLE,
               [{"id": "old1", "created_at": "2026-01-02 03:04:05",
                 "source_name": "s", "sha256": "d" * 64, "phantom": "MSF-01"}])
    store = Store(str(tmp_path))
    store.save_phantom_profile("MSF-01", {"rois": {"uniformity/C": {}}})
    assert store.get_phantom_profile("MSF-01")["layout"]["rois"]
    assert store.list_phantom_profiles()[0]["n_analyses"] == 1
    # and the cascade still applies to a row that predates the feature
    assert store.delete("old1")["profile_deleted"] is True


def test_a_backup_taken_after_the_upgrade_carries_the_new_tables(tmp_path):
    """docs/MAINTENANCE.md promises the backup copy is complete on its own."""
    _legacy_db(str(tmp_path), _LABELS_TABLE,
               [{"id": "old1", "created_at": "2026-01-02 03:04:05",
                 "source_name": "s", "sha256": "e" * 64, "phantom": "MSF-01"}])
    store = Store(str(tmp_path))
    store.save_phantom_profile("MSF-01", {"rois": {"a": {}}})
    store.set_geometry_baseline("old1", {"u": {"c": 1.0}})

    dest = str(tmp_path / "backup" / "copy.sqlite3")
    store.backup_to(dest)

    assert {"analyses", "geometry_history", "phantom_profiles"} <= _tables(dest)
    con = sqlite3.connect(dest)
    try:
        assert con.execute("SELECT COUNT(*) FROM phantom_profiles").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM geometry_history").fetchone()[0] == 1
    finally:
        con.close()


def test_the_old_database_is_not_rewritten_wholesale(tmp_path):
    """The migration is ALTER TABLE and CREATE TABLE IF NOT EXISTS only: an
    upgrade must not rebuild a multi-megabyte table, and must not need free
    space for a second copy of it."""
    db = _legacy_db(str(tmp_path), _LABELS_TABLE,
                    [{"id": f"old{i}", "created_at": "2026-01-02 03:04:05",
                      "source_name": "s", "sha256": str(i) * 64}
                     for i in range(50)])
    con = sqlite3.connect(db)
    before = con.execute("SELECT rootpage FROM sqlite_master "
                         "WHERE name='analyses'").fetchone()[0]
    con.close()

    Store(str(tmp_path))

    con = sqlite3.connect(db)
    after = con.execute("SELECT rootpage FROM sqlite_master "
                        "WHERE name='analyses'").fetchone()[0]
    n = con.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
    con.close()
    assert after == before, "the analyses table was recreated, not altered"
    assert n == 50
