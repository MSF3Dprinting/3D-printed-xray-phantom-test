"""Undo, redo and reset for the measuring points.

The promise: a slip while adjusting an ROI never costs more than one click, and
never costs a re-upload. That means the history has to live on the server —
the browser resyncs from it whenever a request fails or the analysis is
reopened, so a stack kept in the page would vanish exactly when it was needed.
"""

from __future__ import annotations

import hashlib
import io
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from phantom_qa.store import GEOMETRY_HISTORY_DEPTH, Store
from test_authorization import ADMIN_PW, _build_app, _login
from test_store_labels import fake_scan, results_for


def _png(seed: int = 0) -> bytes:
    import numpy as np
    from PIL import Image
    arr = np.full((24, 24), 100 + (seed % 50), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _geom(x: float) -> dict:
    return {"uniformity": {"squares": [
        {"id": "C", "roi": {"id": "uniformity/C", "type": "rect",
                            "center_mm": [x, 0.0], "size_mm": [30.0, 30.0],
                            "angle_deg": 0.0}}]}}


def _centre(rec_or_geom) -> float:
    g = rec_or_geom.get("geometry", rec_or_geom)
    return g["uniformity"]["squares"][0]["roi"]["center_mm"][0]


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path))


@pytest.fixture()
def aid(store):
    payload = _png()
    a = store.new_analysis(fake_scan(sha=hashlib.sha256(payload).hexdigest()),
                           payload, "sig", "1.0.0", "1.0",
                           labels={"phantom": "MSF-01"})
    store.set_geometry_baseline(a, _geom(0.0))
    return a


# ------------------------------------------------------------- the stack

def test_a_fresh_proposal_has_nothing_to_undo(store, aid):
    st = store.geometry_state(aid)
    assert st == {"seq": 0, "undo_depth": 0, "redo_depth": 0,
                  "has_auto_proposal": True}


def test_an_edit_can_be_undone_and_redone(store, aid):
    store.mutate_geometry(
        aid, lambda g: g["uniformity"]["squares"][0]["roi"].update(
            {"center_mm": [10.0, 0.0]}), action="roi")
    assert _centre(store.get(aid)) == 10.0
    assert store.geometry_state(aid)["undo_depth"] == 1

    back = store.undo_geometry(aid)
    assert _centre(back) == 0.0
    assert _centre(store.get(aid)) == 0.0
    assert back["redo_depth"] == 1

    fwd = store.redo_geometry(aid)
    assert _centre(fwd) == 10.0
    assert fwd["redo_depth"] == 0


def test_several_edits_unwind_in_order(store, aid):
    for x in (1.0, 2.0, 3.0):
        store.mutate_geometry(
            aid, lambda g, x=x: g["uniformity"]["squares"][0]["roi"].update(
                {"center_mm": [x, 0.0]}), action="roi")
    assert store.geometry_state(aid)["undo_depth"] == 3
    assert _centre(store.undo_geometry(aid)) == 2.0
    assert _centre(store.undo_geometry(aid)) == 1.0
    assert _centre(store.undo_geometry(aid)) == 0.0
    with pytest.raises(LookupError):
        store.undo_geometry(aid)


def test_a_new_edit_after_an_undo_discards_the_redo_tail(store, aid):
    """The standard rule — otherwise redo would resurrect a branch the
    operator has already replaced."""
    store.mutate_geometry(
        aid, lambda g: g["uniformity"]["squares"][0]["roi"].update(
            {"center_mm": [10.0, 0.0]}), action="roi")
    store.undo_geometry(aid)
    assert store.geometry_state(aid)["redo_depth"] == 1
    store.mutate_geometry(
        aid, lambda g: g["uniformity"]["squares"][0]["roi"].update(
            {"center_mm": [99.0, 0.0]}), action="roi")
    st = store.geometry_state(aid)
    assert st["redo_depth"] == 0
    assert _centre(store.get(aid)) == 99.0
    with pytest.raises(LookupError):
        store.redo_geometry(aid)


def test_redo_at_the_top_is_refused(store, aid):
    with pytest.raises(LookupError):
        store.redo_geometry(aid)


def test_the_baseline_is_never_trimmed_away(store, aid):
    """Reset-to-auto has to keep working however long the session ran."""
    for i in range(GEOMETRY_HISTORY_DEPTH + 12):
        store.mutate_geometry(
            aid, lambda g, i=i: g["uniformity"]["squares"][0]["roi"].update(
                {"center_mm": [float(i), 0.0]}), action="roi")
    base = store.geometry_at(aid, 0)
    assert base is not None and _centre(base) == 0.0
    con = sqlite3.connect(store.db_path)
    rows = con.execute(
        "SELECT COUNT(*) FROM geometry_history WHERE analysis_id=?",
        (aid,)).fetchone()[0]
    con.close()
    assert rows <= GEOMETRY_HISTORY_DEPTH + 1, "the stack grew without bound"


def test_undo_can_still_reach_the_baseline_across_the_trim_gap(store, aid):
    """The trim removes the oldest edits but never seq 0, so a long session
    leaves states 0, then 35, 36, … Stepping by exactly one used to stall at
    the low end of the surviving run with the Undo button still enabled, and
    the pinned automatic proposal could never be reached by undo at all."""
    n = GEOMETRY_HISTORY_DEPTH + 5
    for i in range(n):
        store.mutate_geometry(
            aid, lambda g, i=i: g["uniformity"]["squares"][0]["roi"].update(
                {"center_mm": [float(i + 1), 0.0]}), action="roi")

    con = sqlite3.connect(store.db_path)
    seqs = [r[0] for r in con.execute(
        "SELECT seq FROM geometry_history WHERE analysis_id=? ORDER BY seq",
        (aid,)).fetchall()]
    con.close()
    assert seqs[0] == 0 and seqs[1] > 1, (
        "this test is meaningless unless the trim actually left a gap")

    steps = 0
    while store.geometry_state(aid)["undo_depth"]:
        before = store.geometry_state(aid)
        store.undo_geometry(aid)          # must not raise while depth > 0
        steps += 1
        assert steps <= len(seqs), "undo walked further than there are states"
        assert store.geometry_state(aid)["seq"] < before["seq"]
    assert _centre(store.get(aid)) == 0.0, (
        "undo could not walk back to the pinned automatic proposal")


def test_the_reported_depths_are_exactly_the_steps_available(store, aid):
    """A random walk over edit / undo / redo / reset. The buttons are driven
    by these counts, so a count that over-reports is a button that refuses."""
    import random
    rng = random.Random(7)
    n = 0
    for step in range(400):
        st = store.geometry_state(aid)
        con = sqlite3.connect(store.db_path)
        seqs = [r[0] for r in con.execute(
            "SELECT seq FROM geometry_history WHERE analysis_id=? ORDER BY seq",
            (aid,)).fetchall()]
        con.close()
        assert st["seq"] in seqs, (
            f"step {step}: cursor {st['seq']} points at no stored state")
        assert st["undo_depth"] == len([s for s in seqs if s < st["seq"]])
        assert st["redo_depth"] == len([s for s in seqs if s > st["seq"]])
        # and the counts must be honoured, not merely arithmetic
        if st["undo_depth"] == 0:
            with pytest.raises(LookupError):
                store.undo_geometry(aid)
        if st["redo_depth"] == 0:
            with pytest.raises(LookupError):
                store.redo_geometry(aid)

        op = rng.choices(["edit", "undo", "redo", "reset"],
                         weights=[6, 3, 2, 1])[0]
        try:
            if op == "edit":
                n += 1
                store.mutate_geometry(
                    aid,
                    lambda g, n=n: g["uniformity"]["squares"][0]["roi"].update(
                        {"center_mm": [float(n), 0.0]}), action="roi")
            elif op == "undo":
                store.undo_geometry(aid)
            elif op == "redo":
                store.redo_geometry(aid)
            else:
                store.replace_geometry(aid, store.geometry_at(aid, 0),
                                       action="reset_auto")
        except LookupError:
            pass


def test_a_record_written_before_the_feature_gains_a_baseline(store):
    """Old analyses must not need a data migration to become undoable."""
    payload = _png(3)
    a = store.new_analysis(fake_scan(sha=hashlib.sha256(payload).hexdigest()),
                           payload, "sig", "1.0.0", "1.0", labels={})
    store.update(a, geometry=_geom(5.0))          # no history rows at all
    assert store.geometry_state(a)["has_auto_proposal"] is False
    store.mutate_geometry(
        a, lambda g: g["uniformity"]["squares"][0]["roi"].update(
            {"center_mm": [6.0, 0.0]}), action="roi")
    assert store.geometry_state(a)["undo_depth"] == 1
    assert _centre(store.undo_geometry(a)) == 5.0


def test_a_reset_is_itself_undoable(store, aid):
    """Pressing Reset by mistake must not destroy an afternoon's work."""
    store.mutate_geometry(
        aid, lambda g: g["uniformity"]["squares"][0]["roi"].update(
            {"center_mm": [40.0, 0.0]}), action="roi")
    store.replace_geometry(aid, store.geometry_at(aid, 0), action="reset_auto")
    assert _centre(store.get(aid)) == 0.0
    assert _centre(store.undo_geometry(aid)) == 40.0


def test_reregistration_discards_the_stack(store, aid):
    """Snapshots hold pixel coordinates from a transform that is now gone."""
    store.mutate_geometry(
        aid, lambda g: g["uniformity"]["squares"][0]["roi"].update(
            {"center_mm": [3.0, 0.0]}), action="roi")
    store.clear_geometry_history(aid)
    store.update(aid, geometry_seq=0)
    st = store.geometry_state(aid)
    assert st["undo_depth"] == 0 and st["redo_depth"] == 0


def test_an_edit_that_raises_leaves_no_history_row(store, aid):
    def boom(geom):
        geom["uniformity"]["squares"][0]["roi"]["center_mm"] = [7.0, 0.0]
        raise RuntimeError("rejected")

    with pytest.raises(RuntimeError):
        store.mutate_geometry(aid, boom, action="roi")
    assert _centre(store.get(aid)) == 0.0
    assert store.geometry_state(aid)["undo_depth"] == 0


def test_a_geometry_edit_invalidates_stored_results(store, aid):
    """Otherwise the report draws the new ROIs and prints the old numbers."""
    store.update(aid, results=results_for(), status="pass")
    store.mutate_geometry(
        aid, lambda g: g["uniformity"]["squares"][0]["roi"].update(
            {"center_mm": [1.0, 0.0]}), action="roi")
    rec = store.get(aid)
    assert rec["results"] is None
    assert rec["status"] == "draft"


def test_concurrent_edits_each_leave_exactly_one_history_row(store, aid):
    """The undo write shares the transaction with the geometry write, so it
    cannot reintroduce the lost-update class mutate_json exists to prevent."""
    store.set_geometry_baseline(aid, {"rois": {f"r{i}": {"v": 0} for i in range(8)}})
    gate = threading.Barrier(8)
    errors: list[Exception] = []

    def move(i):
        try:
            gate.wait(timeout=30)
            store.mutate_geometry(
                aid, lambda g: g["rois"][f"r{i}"].__setitem__("v", i + 1),
                action="roi")
        except Exception as exc:            # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=move, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive(), "mutate_geometry deadlocked"
    assert not errors, errors

    geom = store.get(aid)["geometry"]
    lost = [i for i in range(8) if geom["rois"][f"r{i}"]["v"] != i + 1]
    assert not lost, f"updates lost for {lost}"
    st = store.geometry_state(aid)
    assert st["seq"] == 8 and st["undo_depth"] == 8


# ------------------------------------------------------------- over HTTP

@pytest.fixture()
def mod(tmp_path, monkeypatch):
    return _build_app(tmp_path, monkeypatch)


@pytest.fixture()
def client(mod):
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    return c


@pytest.fixture()
def http_aid(mod):
    payload = _png(5)
    a = mod.store.new_analysis(
        fake_scan(sha=hashlib.sha256(payload).hexdigest()), payload,
        "sig", "1.0.0", "1.0", labels={"phantom": "MSF-01"})
    mod.store.set_geometry_baseline(a, _geom(0.0))
    mod.store.mutate_geometry(
        a, lambda g: g["uniformity"]["squares"][0]["roi"].update(
            {"center_mm": [22.0, 0.0]}), action="roi")
    return a


def test_undo_and_redo_over_http(client, http_aid):
    r = client.post(f"/api/analyses/{http_aid}/geometry/undo", json={})
    assert r.status_code == 200, r.text
    assert _centre(r.json()["geometry"]) == 0.0
    assert r.json()["history"]["redo_depth"] == 1

    r = client.post(f"/api/analyses/{http_aid}/geometry/redo", json={})
    assert r.status_code == 200
    assert _centre(r.json()["geometry"]) == 22.0


def test_nothing_to_undo_is_a_409_not_a_500(client, http_aid):
    client.post(f"/api/analyses/{http_aid}/geometry/undo", json={})
    r = client.post(f"/api/analyses/{http_aid}/geometry/undo", json={})
    assert r.status_code == 409
    assert isinstance(r.json()["detail"], str)


def test_reset_to_auto_over_http(client, http_aid):
    r = client.post(f"/api/analyses/{http_aid}/geometry/reset", json={"to": "auto"})
    assert r.status_code == 200, r.text
    assert _centre(r.json()["geometry"]) == 0.0
    assert r.json()["layout_source"] == "auto"
    # and the reset is undoable
    back = client.post(f"/api/analyses/{http_aid}/geometry/undo", json={})
    assert _centre(back.json()["geometry"]) == 22.0


def test_reset_to_a_layout_that_does_not_exist_is_refused(client, http_aid):
    r = client.post(f"/api/analyses/{http_aid}/geometry/reset",
                    json={"to": "profile"})
    assert r.status_code == 404
    assert "layout" in r.json()["detail"].lower()


def test_an_unknown_reset_target_is_refused(client, http_aid):
    r = client.post(f"/api/analyses/{http_aid}/geometry/reset",
                    json={"to": "something-else"})
    assert r.status_code == 400


def test_the_record_carries_the_history_depth_for_the_buttons(client, http_aid):
    rec = client.get(f"/api/analyses/{http_aid}").json()
    assert rec["history"]["undo_depth"] == 1
    assert rec["history"]["redo_depth"] == 0


def test_every_editing_response_reports_the_history(client, mod, http_aid):
    """The Undo / Redo buttons must never need a second round trip."""
    r = client.post(f"/api/analyses/{http_aid}/geometry/undo", json={})
    assert set(r.json()["history"]) >= {"seq", "undo_depth", "redo_depth"}
