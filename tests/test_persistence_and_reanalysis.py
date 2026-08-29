"""Data survives code updates, and stale results can be recomputed in place.

The promise being tested: **a code update never costs you your analyses, and
you never have to re-upload a scan.** The source file is kept, so when the
algorithm or phantom definition changes the stored results can be brought up to
date from disk.
"""

import os
import sqlite3

import pytest

from phantom_qa import ALGO_VERSION
from phantom_qa.phantom_def import load_default
from phantom_qa.reanalyze import (find_outdated, is_outdated, reanalyze_all,
                                  reanalyze_one)
from phantom_qa.store import Store

from test_store_labels import fake_scan, results_for


@pytest.fixture()
def pdef():
    return load_default()


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path))


def _tiny_png(seed: int = 0) -> bytes:
    """A real, decodable image — re-analysis loads the stored file for real."""
    import io
    import numpy as np
    from PIL import Image
    arr = np.full((16, 16), 100 + (seed % 50), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def add(store, pdef, sha=None, site="Goma", phantom="P1",
        algo=ALGO_VERSION, results=True, geometry=True, reg=True,
        payload=None):
    # The recorded hash must be the real hash of the stored bytes, otherwise
    # integrity verification fails and re-analysis correctly refuses to run.
    import hashlib
    payload = payload if payload is not None else _tiny_png(len(sha or "x"))
    aid = store.new_analysis(
        fake_scan(sha=hashlib.sha256(payload).hexdigest()), payload, "sig",
        algo, pdef.version,
        labels={"site": site, "phantom": phantom, "operator": "ST"})
    fields = {}
    if results:
        fields.update(results=results_for(), status="pass")
    if geometry:
        fields["geometry"] = {"geometry": {}}
    if reg:
        fields["reg"] = {"transform": {"A": [[7.0, 0.0], [0.0, -7.0]],
                                       "t": [100.0, 100.0]},
                         "corners_px": [[0, 0], [1, 0], [1, 1], [0, 1]]}
    if fields:
        store.update(aid, **fields)
    return aid


# ------------------------------------------------- survival across updates

def test_analyses_survive_a_schema_migration(tmp_path, pdef):
    """The migration that runs on every start must not disturb stored rows."""
    s = Store(str(tmp_path))
    aid = add(s, pdef)
    s.set_validation(aid, "validated", "Dr A", "accepted")
    s.set_baseline(aid, True)

    # a later release adds a column
    con = sqlite3.connect(s.db_path)
    con.execute("ALTER TABLE analyses ADD COLUMN future_field TEXT DEFAULT ''")
    con.commit()
    con.close()

    s2 = Store(str(tmp_path))                    # restart
    rec = s2.get(aid)
    assert rec is not None
    assert rec["site"] == "Goma" and rec["phantom"] == "P1"
    assert rec["validation_status"] == "validated"
    assert rec["validated_by"] == "Dr A"
    assert rec["validation_comment"] == "accepted"
    assert bool(rec["is_baseline"])
    assert rec["results"] is not None
    assert rec["geometry"] is not None
    assert os.path.exists(s2.upload_path(aid))
    assert s2.verify_integrity(aid)["status"] == "ok"


def test_reopening_the_store_never_drops_data(tmp_path, pdef):
    s = Store(str(tmp_path))
    ids = [add(s, pdef, sha=f"{i:064d}") for i in range(5)]
    for _ in range(3):                            # simulate restarts
        s = Store(str(tmp_path))
    assert {r["id"] for r in s.list_all()} == set(ids)


def test_source_files_are_kept_for_every_analysis(store, pdef):
    aid = add(store, pdef)
    assert os.path.exists(store.upload_path(aid)), \
        "the original file must be kept so re-analysis needs no re-upload"


# ------------------------------------------------------ outdated detection

def test_current_version_is_not_outdated(store, pdef):
    add(store, pdef)
    assert find_outdated(store, pdef) == []


def test_older_algorithm_is_flagged(store, pdef):
    aid = add(store, pdef, algo="0.9.0")
    stale, why = is_outdated(store.get(aid), pdef)
    assert stale and "algorithm" in why
    assert [r["id"] for r in find_outdated(store, pdef)] == [aid]


def test_older_phantom_definition_is_flagged(store, pdef):
    aid = add(store, pdef)
    store.update(aid, pdef_version="0.1")
    stale, why = is_outdated(store.get(aid), pdef)
    assert stale and "phantom definition" in why


def test_drafts_without_results_are_not_flagged(store, pdef):
    add(store, pdef, algo="0.9.0", results=False)
    assert find_outdated(store, pdef) == []


# ----------------------------------------------------------- re-analysis

def test_reanalysis_updates_the_version_stamp(store, pdef):
    aid = add(store, pdef, algo="0.9.0")
    out = reanalyze_one(store, pdef, aid, mode="results")
    assert out["status"] == "updated"
    rec = store.get(aid)
    assert rec["algo_version"] == ALGO_VERSION
    assert rec["pdef_version"] == pdef.version


def test_reanalysis_preserves_everything_the_user_entered(store, pdef):
    aid = add(store, pdef, algo="0.9.0")
    store.set_validation(aid, "conditionally_validated", "Dr B", "watch SNR")
    store.set_baseline(aid, True)
    reanalyze_one(store, pdef, aid, mode="results")
    rec = store.get(aid)
    assert rec["site"] == "Goma" and rec["phantom"] == "P1"
    assert rec["operator"] == "ST"
    assert rec["validation_status"] == "conditionally_validated"
    assert rec["validated_by"] == "Dr B"
    assert rec["validation_comment"] == "watch SNR"
    assert bool(rec["is_baseline"])


def test_results_mode_keeps_the_confirmed_geometry(store, pdef):
    """Manual ROI adjustments must survive an algorithm-driven recompute."""
    aid = add(store, pdef, algo="0.9.0")
    marked = {"geometry": {}, "_user_marker": "manually adjusted"}
    store.update(aid, geometry=marked)
    reanalyze_one(store, pdef, aid, mode="results")
    assert store.get(aid)["geometry"].get("_user_marker") == "manually adjusted"


def _full_mode(monkeypatch, geometry=None):
    """Run a full re-analysis without needing a real phantom in the image.

    Full mode re-detects everything, which a 16x16 placeholder PNG cannot
    support; the point being tested is what the store does with the results
    afterwards, not the detection itself."""
    from phantom_qa import pipeline
    from phantom_qa.registration import Transform
    import numpy as np
    from test_store_labels import results_for

    reg = pipeline.registration_from_dict(
        {"transform": {"A": [[7.0, 0.0], [0.0, -7.0]], "t": [8.0, 8.0]},
         "corners_px": [[0, 0], [1, 0], [1, 1], [0, 1]]})
    monkeypatch.setattr(pipeline, "run_stage_a", lambda *a, **k: reg)
    monkeypatch.setattr(pipeline, "propose_all",
                        lambda ctx: geometry or {"geometry": {"redetected": 1}})
    monkeypatch.setattr(pipeline, "compute_all", lambda ctx, g: results_for())
    monkeypatch.setattr(pipeline, "overall_status", lambda r: "pass")


def test_full_mode_keeps_the_results_it_just_computed(store, pdef, monkeypatch):
    """A full re-analysis computes results FROM the geometry it then installs,
    so the two already agree — invalidating them destroys exactly the work the
    command was run to do, while the CLI still prints 'pass -> pass'."""
    aid = add(store, pdef, algo="0.9.0")
    _full_mode(monkeypatch)

    out = reanalyze_one(store, pdef, aid, mode="full")

    assert out["status"] == "updated"
    rec = store.get(aid)
    assert rec["results"] is not None, (
        "the full re-analysis blanked the results it had just computed")
    assert rec["status"] == out["new_overall"]
    assert rec["geometry"] == {"geometry": {"redetected": 1}}


def test_full_mode_still_discards_the_measuring_point_history(store, pdef,
                                                              monkeypatch):
    """Re-detection replaces the transform, so every stored undo state holds
    pixel coordinates from a registration that no longer exists."""
    aid = add(store, pdef, algo="0.9.0")
    store.set_geometry_baseline(aid, {"geometry": {"old": 1}})
    store.mutate_geometry(aid, lambda g: g.__setitem__("edited", 1),
                          action="roi")
    assert store.geometry_state(aid)["undo_depth"] == 1

    _full_mode(monkeypatch)
    reanalyze_one(store, pdef, aid, mode="full")

    st = store.geometry_state(aid)
    assert st == {"seq": 0, "undo_depth": 0, "redo_depth": 0,
                  "has_baseline": True}
    assert store.geometry_at(aid, 0) == {"geometry": {"redetected": 1}}


def test_full_mode_leaves_the_record_in_every_export(store, pdef, monkeypatch):
    """The symptom a user would actually notice: a blanked record silently
    drops out of trends, both CSV exports and the comparison report, all of
    which select on completed results."""
    aid = add(store, pdef, algo="0.9.0")
    _full_mode(monkeypatch)
    reanalyze_one(store, pdef, aid, mode="full")
    assert [r["id"] for r in store.list_all(completed_only=True)] == [aid]


def test_dry_run_writes_nothing(store, pdef):
    aid = add(store, pdef, algo="0.9.0")
    out = reanalyze_one(store, pdef, aid, mode="results", dry_run=True)
    assert out["status"] == "would_change"
    assert store.get(aid)["algo_version"] == "0.9.0", "dry run must not write"


def test_signed_off_analyses_are_skipped_by_default(store, pdef):
    aid = add(store, pdef, algo="0.9.0")
    store.set_validation(aid, "validated", "Dr C")
    out = reanalyze_all(store, pdef)
    assert out[0]["status"] == "skipped_validated"
    assert store.get(aid)["algo_version"] == "0.9.0"


def test_signed_off_can_be_included_explicitly(store, pdef):
    aid = add(store, pdef, algo="0.9.0")
    store.set_validation(aid, "validated", "Dr C")
    out = reanalyze_all(store, pdef, include_validated=True)
    assert out[0]["status"] == "updated"
    assert store.get(aid)["algo_version"] == ALGO_VERSION
    assert store.get(aid)["validation_status"] == "validated", \
        "recomputing must not silently clear the sign-off"


def test_reanalysis_refuses_when_integrity_fails(store, pdef):
    """New numbers must not be computed from a file that no longer matches."""
    aid = add(store, pdef, algo="0.9.0")
    with open(store.upload_path(aid), "wb") as f:
        f.write(b"tampered")
    out = reanalyze_one(store, pdef, aid, mode="results")
    assert out["status"] == "integrity_failed"
    assert store.get(aid)["algo_version"] == "0.9.0", "must not have been updated"


def test_results_mode_needs_stored_geometry_and_registration(store, pdef):
    aid = add(store, pdef, algo="0.9.0", geometry=False, reg=False)
    out = reanalyze_one(store, pdef, aid, mode="results")
    assert out["status"] == "skipped"
    assert "--full" in out["message"]


def test_reanalysis_is_recorded_in_the_audit_trail(store, pdef):
    aid = add(store, pdef, algo="0.9.0")
    reanalyze_one(store, pdef, aid, mode="results")
    actions = [a["action"] for a in store.get(aid)["audit"]]
    assert "re-analysed" in actions


def test_only_outdated_are_touched_by_default(store, pdef):
    old = add(store, pdef, sha="a" * 64, algo="0.9.0")
    cur = add(store, pdef, sha="b" * 64)
    touched = {r["id"] for r in reanalyze_all(store, pdef)}
    assert touched == {old}, "up-to-date analyses should be left alone"


def test_all_flag_includes_current_versions(store, pdef):
    add(store, pdef, sha="a" * 64)
    out = reanalyze_all(store, pdef, only_outdated=False)
    assert out and out[0]["status"] == "updated"


def test_unknown_id_reports_cleanly(store, pdef):
    assert reanalyze_one(store, pdef, "nope")["status"] == "not_found"


def test_unreadable_file_does_not_abort_the_batch(store, pdef):
    """One corrupt file must not stop the rest of a bulk re-analysis."""
    import hashlib
    bad = add(store, pdef, sha="bad", algo="0.9.0", payload=b"not-an-image")
    # record the true hash of the junk so integrity passes and decoding is
    # what fails
    store.update(bad, sha256=hashlib.sha256(b"not-an-image").hexdigest())
    good = add(store, pdef, sha="good", algo="0.9.0")

    out = {r["id"]: r["status"] for r in reanalyze_all(store, pdef)}
    assert out[bad] == "unreadable"
    assert out[good] == "updated", "a bad neighbour must not block this one"


def test_missing_source_file_reports_cleanly(store, pdef):
    aid = add(store, pdef, algo="0.9.0")
    os.remove(store.upload_path(aid))
    out = reanalyze_one(store, pdef, aid, mode="results")
    assert out["status"] in ("source_missing", "integrity_failed")
    assert store.get(aid)["algo_version"] == "0.9.0"
