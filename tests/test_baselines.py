"""The reference scan: one per phantom per protocol, and removable.

Two things were wrong. A baseline was scoped to the protocol alone, so a site
running two phantoms on one machine could only ever have a reference for
whichever was marked last — and phantoms can differ by design and both be
valid. And nothing could clear the flag once set, so a reference chosen from a
scan that later turned out to be poor was permanent.
"""

from __future__ import annotations

import hashlib
import io

import pytest
from fastapi.testclient import TestClient

from phantom_qa.store import Store
from test_authorization import _build_app, _login
from test_store_labels import fake_scan, results_for


def _png(seed: int = 0) -> bytes:
    import numpy as np
    from PIL import Image
    arr = np.full((24, 24), 100 + (seed % 50), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path))


def _add(store, phantom="MSF-01", signature="sig-A", seed=0, results=True,
         reduced=False):
    payload = _png(seed)
    scan = fake_scan(sha=hashlib.sha256(payload).hexdigest())
    scan.reduced_precision = reduced
    aid = store.new_analysis(scan, payload, signature, "1.0.0", "1.0",
                             labels={"site": "Goma", "phantom": phantom})
    if results:
        store.update(aid, results=results_for(), status="pass")
    return aid


# ------------------------------------------------------------- the scope

def test_two_phantoms_on_one_machine_each_keep_a_reference(store):
    """The reported problem. Same protocol, two phantoms, both valid."""
    a = _add(store, phantom="MSF-01", seed=1)
    b = _add(store, phantom="MSF-02", seed=2)
    store.set_baseline(a, True)
    store.set_baseline(b, True)

    assert store.get(a)["is_baseline"] == 1, (
        "marking a second phantom's reference demoted the first phantom's")
    assert store.get(b)["is_baseline"] == 1
    assert store.baseline_for("sig-A", "MSF-01")["id"] == a
    assert store.baseline_for("sig-A", "MSF-02")["id"] == b


def test_one_phantom_has_only_one_reference(store):
    older = _add(store, phantom="MSF-01", seed=1)
    newer = _add(store, phantom="MSF-01", seed=2)
    store.set_baseline(older, True)
    out = store.set_baseline(newer, True)

    assert out["replaced"] == [older]
    assert store.get(older)["is_baseline"] == 0
    assert store.baseline_for("sig-A", "MSF-01")["id"] == newer


def test_the_protocol_still_separates_references(store):
    """Pixel values are not proportional to dose, so comparing a scan against a
    reference taken at another kV would be meaningless whatever the phantom."""
    a = _add(store, phantom="MSF-01", signature="sig-A", seed=1)
    b = _add(store, phantom="MSF-01", signature="sig-B", seed=2)
    store.set_baseline(a, True)
    store.set_baseline(b, True)
    assert store.get(a)["is_baseline"] == 1
    assert store.baseline_for("sig-A", "MSF-01")["id"] == a
    assert store.baseline_for("sig-B", "MSF-01")["id"] == b


def test_a_reference_is_not_offered_to_a_different_phantom(store):
    a = _add(store, phantom="MSF-01", seed=1)
    store.set_baseline(a, True)
    assert store.baseline_for("sig-A", "MSF-02") is None


def test_unlabelled_scans_share_one_reference(store):
    """'' is a legal phantom value; it must behave as one bucket, not as a
    wildcard that matches every phantom."""
    a = _add(store, phantom="", seed=1)
    store.set_baseline(a, True)
    assert store.baseline_for("sig-A", "")["id"] == a
    assert store.baseline_for("sig-A", "MSF-01") is None


def test_the_lookup_ignores_surrounding_space(store):
    a = _add(store, phantom="MSF-01", seed=1)
    store.set_baseline(a, True)
    assert store.baseline_for("sig-A", "  MSF-01  ")["id"] == a


# ------------------------------------------------------------- removability

def test_a_reference_can_be_removed(store):
    a = _add(store, phantom="MSF-01", seed=1)
    store.set_baseline(a, True)
    out = store.set_baseline(a, False)
    assert out["is_baseline"] is False
    assert store.get(a)["is_baseline"] == 0
    assert store.baseline_for("sig-A", "MSF-01") is None, (
        "the phantom should be left with no reference at all")


def test_removing_one_leaves_other_phantoms_alone(store):
    a = _add(store, phantom="MSF-01", seed=1)
    b = _add(store, phantom="MSF-02", seed=2)
    store.set_baseline(a, True)
    store.set_baseline(b, True)
    store.set_baseline(a, False)
    assert store.baseline_for("sig-A", "MSF-02")["id"] == b


def test_setting_a_baseline_on_a_missing_analysis_raises(store):
    with pytest.raises(KeyError):
        store.set_baseline("does-not-exist", True)


def test_every_reference_can_be_listed(store):
    a = _add(store, phantom="MSF-01", seed=1)
    b = _add(store, phantom="MSF-02", seed=2)
    store.set_baseline(a, True)
    store.set_baseline(b, True)
    rows = store.baselines()
    assert {r["phantom"] for r in rows} == {"MSF-01", "MSF-02"}
    assert {r["id"] for r in rows} == {a, b}


def test_references_marked_under_the_old_rule_keep_working(store):
    """A deployed database has baselines chosen when the scope was the protocol
    alone. Narrowing the scope must not orphan them or create duplicates.

    It cannot: the old rule allowed at most one per signature, and every such
    row is now simply the reference for its own phantom on that signature. The
    only change is that OTHER phantoms can now have one too."""
    import sqlite3
    legacy = _add(store, phantom="MSF-01", signature="sig-A", seed=1)
    other = _add(store, phantom="MSF-02", signature="sig-A", seed=2)
    # written the way the previous release did it, bypassing the new scoping
    con = sqlite3.connect(store.db_path)
    con.execute("UPDATE analyses SET is_baseline=1 WHERE id=?", (legacy,))
    con.commit()
    con.close()

    assert store.baseline_for("sig-A", "MSF-01")["id"] == legacy
    assert store.baseline_for("sig-A", "MSF-02") is None, (
        "the other phantom must start with no reference, not inherit one")

    # and it can now have its own without disturbing the legacy one
    store.set_baseline(other, True)
    assert store.get(legacy)["is_baseline"] == 1
    assert len(store.baselines()) == 2


def test_a_phantom_never_ends_up_with_two_references(store):
    a = _add(store, phantom="MSF-01", seed=1)
    b = _add(store, phantom="MSF-01", seed=2)
    c = _add(store, phantom="MSF-01", seed=3)
    for aid in (a, b, c):
        store.set_baseline(aid, True)
    marked = [r for r in store.baselines() if r["phantom"] == "MSF-01"]
    assert len(marked) == 1 and marked[0]["id"] == c


# ------------------------------------------------------- renaming a phantom

def test_renaming_a_reference_into_an_occupied_phantom_stands_it_down(store):
    """Otherwise the phantom would have two references and the lookup would
    return whichever the database reached first."""
    keeper = _add(store, phantom="MSF-02", seed=1)
    mover = _add(store, phantom="MSF-01", seed=2)
    store.set_baseline(keeper, True)
    store.set_baseline(mover, True)

    out = store.set_labels(mover, {"phantom": "MSF-02"})

    assert out["baseline_demoted"] is True
    assert store.get(mover)["is_baseline"] == 0
    assert store.baseline_for("sig-A", "MSF-02")["id"] == keeper


def test_renaming_a_reference_into_a_free_phantom_keeps_it(store):
    mover = _add(store, phantom="MSF-01", seed=1)
    store.set_baseline(mover, True)
    out = store.set_labels(mover, {"phantom": "MSF-09"})
    assert out["baseline_demoted"] is False
    assert store.baseline_for("sig-A", "MSF-09")["id"] == mover
    assert store.baseline_for("sig-A", "MSF-01") is None


# ------------------------------------------------------------------- HTTP

@pytest.fixture()
def mod(tmp_path, monkeypatch):
    return _build_app(tmp_path, monkeypatch)


@pytest.fixture()
def client(mod):
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    return c


def test_the_endpoint_sets_and_clears(client, mod):
    aid = _add(mod.store, phantom="MSF-01")
    r = client.post(f"/api/analyses/{aid}/baseline", json={"baseline": True})
    assert r.status_code == 200, r.text
    assert r.json()["is_baseline"] is True
    assert r.json()["phantom"] == "MSF-01"

    r = client.post(f"/api/analyses/{aid}/baseline", json={"baseline": False})
    assert r.status_code == 200, r.text
    assert r.json()["is_baseline"] is False
    assert mod.store.get(aid)["is_baseline"] == 0


def test_the_endpoint_reports_what_it_replaced(client, mod):
    old = _add(mod.store, phantom="MSF-01", seed=1)
    new = _add(mod.store, phantom="MSF-01", seed=2)
    client.post(f"/api/analyses/{old}/baseline", json={"baseline": True})
    r = client.post(f"/api/analyses/{new}/baseline", json={"baseline": True})
    assert r.json()["replaced"] == [old]


def test_an_analysis_without_results_cannot_be_a_reference(client, mod):
    aid = _add(mod.store, phantom="MSF-01", results=False)
    r = client.post(f"/api/analyses/{aid}/baseline", json={"baseline": True})
    assert r.status_code == 400
    assert "results" in r.json()["detail"]


def test_a_reduced_precision_analysis_cannot_be_a_reference(client, mod):
    aid = _add(mod.store, phantom="MSF-01", reduced=True)
    r = client.post(f"/api/analyses/{aid}/baseline", json={"baseline": True})
    assert r.status_code == 400
    assert "reduced-precision" in r.json()["detail"]


def test_finalising_no_longer_clears_a_reference(client, mod):
    """Finalise is pressed more than once; it must not silently undo a
    deliberate decision made elsewhere."""
    aid = _add(mod.store, phantom="MSF-01")
    client.post(f"/api/analyses/{aid}/baseline", json={"baseline": True})
    r = client.post(f"/api/analyses/{aid}/finalize", json={})
    assert r.status_code == 200, r.text
    assert mod.store.get(aid)["is_baseline"] == 1


def test_finalising_can_still_set_it(client, mod):
    aid = _add(mod.store, phantom="MSF-01")
    r = client.post(f"/api/analyses/{aid}/finalize", json={"baseline": True})
    assert r.status_code == 200, r.text
    assert mod.store.get(aid)["is_baseline"] == 1


def test_the_listing_endpoint_shows_one_per_phantom(client, mod):
    a = _add(mod.store, phantom="MSF-01", seed=1)
    b = _add(mod.store, phantom="MSF-02", seed=2)
    for aid in (a, b):
        client.post(f"/api/analyses/{aid}/baseline", json={"baseline": True})
    rows = client.get("/api/baselines").json()["baselines"]
    assert sorted(r["phantom"] for r in rows) == ["MSF-01", "MSF-02"]


def test_the_comparison_uses_this_phantoms_reference(client, mod):
    """The point of the whole change: an analysis must be compared against its
    OWN phantom's reference, not another phantom's."""
    ref01 = _add(mod.store, phantom="MSF-01", seed=1)
    client.post(f"/api/analyses/{ref01}/baseline", json={"baseline": True})
    ref02 = _add(mod.store, phantom="MSF-02", seed=2)
    client.post(f"/api/analyses/{ref02}/baseline", json={"baseline": True})

    assert mod.store.baseline_for("sig-A", "MSF-02")["id"] == ref02
    later = _add(mod.store, phantom="MSF-02", seed=3)
    assert mod.store.baseline_for("sig-A", "MSF-02", exclude_id=later)["id"] == ref02


def test_the_audit_log_records_setting_and_clearing(client, mod, tmp_path):
    import os
    aid = _add(mod.store, phantom="MSF-01")
    client.post(f"/api/analyses/{aid}/baseline", json={"baseline": True})
    client.post(f"/api/analyses/{aid}/baseline", json={"baseline": False})
    log = os.path.join(str(tmp_path), "logs", "audit.log")
    assert os.path.exists(log)
    with open(log, encoding="utf-8") as f:
        body = f.read()
    assert "event=baseline" in body
    assert "outcome=set" in body and "outcome=cleared" in body
