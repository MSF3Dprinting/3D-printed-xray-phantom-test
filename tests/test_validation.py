"""Administrator validation sign-off: states, approver, comment, propagation."""

import sqlite3

import pytest

from phantom_qa.comparison_report import build_comparison_report
from phantom_qa.report import build_report
from phantom_qa.store import (VALIDATION_LABELS, VALIDATION_STATES, Store,
                              csv_export, wide_csv_export)

from test_store_labels import fake_scan, results_for


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path))


def add(store, site="Goma", phantom="MSF-01", sha="a" * 64, results=True):
    aid = store.new_analysis(fake_scan(sha=sha), b"payload", "sig", "1.0.0",
                             "1.0", labels={"site": site, "phantom": phantom,
                                            "operator": "ST"})
    if results:
        store.update(aid, results=results_for(), status="pass")
    return aid


# ------------------------------------------------------------------- states

def test_new_analysis_is_pending(store):
    rec = store.get(add(store))
    assert rec["validation_status"] == ""
    assert rec["validated_by"] == ""
    assert rec["validated_at"] == ""
    assert VALIDATION_LABELS[""] == "pending review"


@pytest.mark.parametrize("state", VALIDATION_STATES)
def test_each_state_can_be_recorded(store, state):
    aid = add(store)
    out = store.set_validation(aid, state, "Dr Approver", "looks fine")
    assert out["validation_status"] == state
    rec = store.get(aid)
    assert rec["validation_status"] == state
    assert rec["validated_by"] == "Dr Approver"
    assert rec["validation_comment"] == "looks fine"
    assert rec["validated_at"], "a timestamp must be recorded"


def test_three_states_are_exactly_what_was_asked_for():
    assert set(VALIDATION_STATES) == {"validated", "conditionally_validated",
                                      "not_validated"}


def test_unknown_state_is_rejected(store):
    aid = add(store)
    with pytest.raises(ValueError):
        store.set_validation(aid, "approved-ish", "Someone")
    assert store.get(aid)["validation_status"] == ""


def test_approver_name_is_required(store):
    aid = add(store)
    with pytest.raises(ValueError, match="approver"):
        store.set_validation(aid, "validated", "   ")
    assert store.get(aid)["validation_status"] == "", "must not be half-applied"


def test_comment_is_optional(store):
    aid = add(store)
    store.set_validation(aid, "validated", "A. Person")
    assert store.get(aid)["validation_comment"] == ""


def test_ruling_can_be_withdrawn(store):
    aid = add(store)
    store.set_validation(aid, "validated", "A. Person", "ok")
    store.set_validation(aid, "", "", "")
    rec = store.get(aid)
    assert rec["validation_status"] == ""
    assert rec["validated_at"] == "", "the old timestamp must not linger"


def test_ruling_can_be_changed(store):
    aid = add(store)
    store.set_validation(aid, "validated", "First Person", "fine")
    store.set_validation(aid, "not_validated", "Second Person", "on review, no")
    rec = store.get(aid)
    assert rec["validation_status"] == "not_validated"
    assert rec["validated_by"] == "Second Person"
    assert rec["validation_comment"] == "on review, no"


def test_names_and_comments_are_trimmed(store):
    aid = add(store)
    store.set_validation(aid, "validated", "  S. Tkac  ", "  spaced  ")
    rec = store.get(aid)
    assert rec["validated_by"] == "S. Tkac"
    assert rec["validation_comment"] == "spaced"


# ---------------------------------------------------------------- filtering

def test_filter_by_validation_state(store):
    a = add(store, sha="a" * 64)
    b = add(store, sha="b" * 64)
    c = add(store, sha="c" * 64)
    store.set_validation(a, "validated", "P1")
    store.set_validation(b, "not_validated", "P2")
    assert [r["id"] for r in store.list_all(validation="validated")] == [a]
    assert [r["id"] for r in store.list_all(validation="not_validated")] == [b]
    assert [r["id"] for r in store.list_all(validation="pending")] == [c]
    assert len(store.list_all()) == 3


def test_listing_exposes_validation_fields(store):
    aid = add(store)
    store.set_validation(aid, "conditionally_validated", "S. Tkac", "watch SNR")
    row = store.list_all()[0]
    assert row["validation_status"] == "conditionally_validated"
    assert row["validated_by"] == "S. Tkac"
    assert row["validation_comment"] == "watch SNR"


# ------------------------------------------------------------------ reports

def test_single_report_shows_the_ruling(store):
    aid = add(store)
    store.set_validation(aid, "conditionally_validated", "S. Tkac",
                         "uniformity drifting, recheck next month")
    out = build_report(store.get(aid))
    assert "CONDITIONALLY VALIDATED" in out
    assert "S. Tkac" in out
    assert "uniformity drifting" in out
    assert "Approved by" in out


def test_single_report_says_pending_when_unruled(store):
    out = build_report(store.get(add(store)))
    assert "PENDING REVIEW" in out
    assert "have not been signed off" in out


def test_not_validated_is_unmissable(store):
    aid = add(store)
    store.set_validation(aid, "not_validated", "S. Tkac", "field alignment fails")
    out = build_report(store.get(aid))
    assert "NOT VALIDATED" in out
    assert "#cf3f3f" in out, "rejection should be coloured as a failure"


def test_comparison_report_shows_validation_per_entry(store):
    a = add(store, phantom="MSF-01", sha="a" * 64)
    b = add(store, phantom="MSF-02", sha="b" * 64)
    store.set_validation(a, "validated", "Alice")
    store.set_validation(b, "not_validated", "Bob", "rejected")
    recs = [store.get(a), store.get(b)]
    out = build_comparison_report(recs)
    assert "validation" in out
    assert "Alice" in out and "Bob" in out
    assert "NOT validated" in out


# ------------------------------------------------------------------ exports

def test_long_csv_carries_the_ruling(store):
    aid = add(store)
    store.set_validation(aid, "validated", "S. Tkac", "ok")
    out = csv_export([store.get(aid)])
    header = out.splitlines()[0]
    for col in ("validation", "validated_by", "validated_at"):
        assert col in header
    assert "S. Tkac" in out
    assert "validated" in out


def test_wide_csv_carries_the_ruling(store):
    aid = add(store)
    store.set_validation(aid, "conditionally_validated", "S. Tkac")
    out = wide_csv_export([store.get(aid)])
    assert "# validation_status" in out
    assert "conditionally_validated" in out
    assert "# validated_by" in out and "S. Tkac" in out


# ---------------------------------------------------------------- migration

def test_migration_adds_validation_columns(tmp_path):
    """An existing database from before this feature must gain the columns."""
    db = tmp_path / "data" / "phantom_qa.sqlite3"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE analyses (
          id TEXT PRIMARY KEY, created_at TEXT, source_name TEXT, sha256 TEXT,
          kind TEXT, reduced_precision INTEGER, signature TEXT, meta_json TEXT,
          stage TEXT, reg_json TEXT, geometry_json TEXT, results_json TEXT,
          audit_json TEXT, sid_mm REAL, is_baseline INTEGER DEFAULT 0,
          algo_version TEXT, pdef_version TEXT, status TEXT,
          site TEXT, phantom TEXT, operator TEXT, notes TEXT,
          acquired_at TEXT);
    """)
    con.execute("INSERT INTO analyses (id, created_at, source_name, sha256, site)"
                " VALUES ('old1','2026-01-01 00:00:00','legacy.dcm','f','Goma')")
    con.commit()
    con.close()

    s = Store(str(tmp_path))
    rec = s.get("old1")
    assert rec is not None and rec["site"] == "Goma"
    assert rec["validation_status"] == ""
    s.set_validation("old1", "validated", "Late Approver", "signed off later")
    assert s.get("old1")["validated_by"] == "Late Approver"
    assert [r["id"] for r in s.list_all(validation="validated")] == ["old1"]
