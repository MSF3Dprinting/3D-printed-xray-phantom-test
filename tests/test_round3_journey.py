"""The tester's own sequence, driven over HTTP against a real DICOM.

The unit tests each pin one mechanism. This file walks the whole path an
operator actually takes, because the round-3 report was about how the parts
behave together: upload, correct the measuring points, confirm, delete, upload
the same file again — and, separately, upload a second scan of the same phantom
and find the corrections already applied.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from conftest import SAMPLES, needs_samples
from test_authorization import ADMIN_PW, _build_app, _login

REASON = "test exposure, wrong phantom fitted"


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    return _build_app(tmp_path, monkeypatch)


@pytest.fixture()
def client(mod):
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    return c


def _upload(client, path, phantom="MSF-01", allow_duplicate=False):
    data = {"site": "Goma", "phantom": phantom}
    if allow_duplicate:
        data["allow_duplicate"] = "true"
    with open(path, "rb") as f:
        r = client.post("/api/analyses",
                        files={"file": ("scan.dcm", io.BytesIO(f.read()))},
                        data=data)
    assert r.status_code == 200, r.text
    return r.json()


def _to_stage_c(client, aid):
    assert client.post(f"/api/analyses/{aid}/confirm",
                       json={"stage": "A"}).status_code == 200
    r = client.post(f"/api/analyses/{aid}/propose", json={})
    assert r.status_code == 200, r.text
    return r.json()


def _first_uniformity_roi(geometry):
    return geometry["uniformity"]["squares"][0]["roi"]


@needs_samples
def test_correcting_a_scan_then_deleting_it_leaves_nothing_behind(client, mod):
    """The reported bug, end to end on a real file."""
    first = _upload(client, SAMPLES[0])["analyses"][0]["id"]
    geom = _to_stage_c(client, first)["geometry"]

    roi = _first_uniformity_roi(geom)
    moved_to = [roi["center_px"][0] + 60, roi["center_px"][1] + 40]
    r = client.post(f"/api/analyses/{first}/roi",
                    json={"roi_id": roi["id"], "center_px": moved_to})
    assert r.status_code == 200, r.text
    assert r.json()["roi"]["manually_adjusted"] is True
    corrected_mm = r.json()["roi"]["center_mm"]

    # confirming Stage C stores the layout for phantom MSF-01
    confirmed = client.post(f"/api/analyses/{first}/confirm", json={"stage": "C"})
    assert confirmed.json()["profile_saved"] is True

    # ...and deleting the only analysis of that phantom takes the layout too
    gone = client.post(f"/api/analyses/{first}/delete",
                       json={"admin_password": ADMIN_PW, "reason": REASON})
    assert gone.status_code == 200, gone.text
    assert gone.json()["layout_deleted"] is True

    # re-uploading the very same bytes must start from nothing
    again = _upload(client, SAMPLES[0])
    second = again["analyses"][0]["id"]
    assert second != first
    assert again["phantom_profile"] is None, (
        "a layout from the deleted analysis was offered for the new one")

    rec = client.get(f"/api/analyses/{second}").json()
    assert rec["geometry"] is None
    assert rec["history"]["undo_depth"] == 0
    assert rec["phantom_profile"] is None

    fresh = _to_stage_c(client, second)
    assert fresh["profile_applied"] is False
    assert fresh["layout_source"] == "auto"
    auto_roi = _first_uniformity_roi(fresh["geometry"])
    assert not auto_roi.get("manually_adjusted"), (
        "the re-uploaded scan came back already marked as hand-adjusted")
    assert auto_roi["center_mm"] != pytest.approx(corrected_mm, abs=0.5), (
        "the re-uploaded scan inherited the deleted analysis's correction")


@needs_samples
def test_a_second_scan_of_the_same_phantom_starts_from_the_corrections(client,
                                                                      mod):
    """The feature the bug was mistaken for: this time it is deliberate, it is
    announced, and the analysis it came from still exists."""
    first = _upload(client, SAMPLES[0])["analyses"][0]["id"]
    geom = _to_stage_c(client, first)["geometry"]
    roi = _first_uniformity_roi(geom)
    moved = client.post(
        f"/api/analyses/{first}/roi",
        json={"roi_id": roi["id"],
              "center_px": [roi["center_px"][0] + 60,
                            roi["center_px"][1] + 40]}).json()
    corrected_mm = moved["roi"]["center_mm"]
    assert client.post(f"/api/analyses/{first}/confirm",
                       json={"stage": "C"}).json()["profile_saved"] is True

    # a different scan of the same phantom
    second = _upload(client, SAMPLES[1])["analyses"][0]["id"]
    r = _to_stage_c(client, second)
    assert r["profile_applied"] is True, r.get("profile_check")
    assert r["layout_source"] == "profile"
    assert r["profile"]["phantom"] == "MSF-01"

    replayed = _first_uniformity_roi(r["geometry"])
    assert replayed["center_mm"] == pytest.approx(corrected_mm, abs=1e-6), (
        "the stored correction was not replayed onto the second scan")
    assert replayed["from_profile"] is True
    # pixels re-derived for THIS scan, not copied from the first
    assert replayed["center_px"] == pytest.approx(
        client.get(f"/api/analyses/{second}/roi_stats"
                   f"?roi_id={replayed['id']}").json()["roi"]["center_px"])


@needs_samples
def test_the_operator_can_go_back_to_automatic_detection(client, mod):
    first = _upload(client, SAMPLES[0])["analyses"][0]["id"]
    geom = _to_stage_c(client, first)["geometry"]
    roi = _first_uniformity_roi(geom)
    auto_mm = list(roi["center_mm"])
    client.post(f"/api/analyses/{first}/roi",
                json={"roi_id": roi["id"],
                      "center_px": [roi["center_px"][0] + 60,
                                    roi["center_px"][1] + 40]})
    client.post(f"/api/analyses/{first}/confirm", json={"stage": "C"})

    second = _upload(client, SAMPLES[1])["analyses"][0]["id"]
    applied = _to_stage_c(client, second)
    assert applied["profile_applied"] is True

    r = client.post(f"/api/analyses/{second}/geometry/reset", json={"to": "auto"})
    assert r.status_code == 200, r.text
    assert r.json()["layout_source"] == "auto"
    back = _first_uniformity_roi(r.json()["geometry"])
    assert back["center_mm"] == pytest.approx(auto_mm, abs=1.5), (
        "reset did not return to this scan's own detection")

    # and the reset is undoable, so it is safe to press
    undone = client.post(f"/api/analyses/{second}/geometry/undo", json={})
    assert undone.status_code == 200
    assert _first_uniformity_roi(undone.json()["geometry"])["from_profile"] is True


@needs_samples
def test_undo_walks_back_through_several_corrections(client, mod):
    aid = _upload(client, SAMPLES[0])["analyses"][0]["id"]
    geom = _to_stage_c(client, aid)["geometry"]
    roi = _first_uniformity_roi(geom)
    start = list(roi["center_px"])

    seen = [list(roi["center_mm"])]
    for step in (20, 40, 60):
        r = client.post(f"/api/analyses/{aid}/roi",
                        json={"roi_id": roi["id"],
                              "center_px": [start[0] + step, start[1]]})
        assert r.status_code == 200, r.text
        seen.append(r.json()["roi"]["center_mm"])
    assert client.get(f"/api/analyses/{aid}").json()["history"]["undo_depth"] == 3

    for expected in reversed(seen[:-1]):
        u = client.post(f"/api/analyses/{aid}/geometry/undo", json={})
        assert u.status_code == 200, u.text
        assert _first_uniformity_roi(u.json()["geometry"])["center_mm"] == \
            pytest.approx(expected, abs=1e-6)

    assert client.post(f"/api/analyses/{aid}/geometry/undo",
                       json={}).status_code == 409


@needs_samples
def test_a_layout_is_refused_on_a_scan_it_does_not_fit(client, mod):
    """The safety gate, exercised with a layout deliberately displaced by more
    than any phantom-to-phantom difference."""
    first = _upload(client, SAMPLES[0])["analyses"][0]["id"]
    _to_stage_c(client, first)
    client.post(f"/api/analyses/{first}/confirm", json={"stage": "C"})

    prof = mod.store.get_phantom_profile("MSF-01")
    for rid, entry in prof["layout"]["rois"].items():
        if "center_mm" in entry:
            entry["center_mm"] = [entry["center_mm"][0] + 120.0,
                                  entry["center_mm"][1] - 120.0]
    mod.store.save_phantom_profile("MSF-01", prof["layout"])

    second = _upload(client, SAMPLES[1])["analyses"][0]["id"]
    r = _to_stage_c(client, second)
    assert r["profile_applied"] is False
    assert r["profile_check"]["ok"] is False
    assert "orientation" in r["profile_check"]["reason"]
    assert r["layout_source"] == "auto", (
        "a refused layout must leave the scan on its own detection")


@needs_samples
def test_the_two_dates_differ_on_a_real_scan(client, mod):
    """Both columns must carry real, distinguishable values."""
    aid = _upload(client, SAMPLES[0])["analyses"][0]["id"]
    rec = client.get(f"/api/analyses/{aid}").json()
    assert rec["acquired_at"], "the sample DICOM should carry a StudyDate"
    assert rec["created_at"]
    assert rec["acquired_at"] != rec["created_at"], (
        "the acquisition time and the upload time collapsed into one value")
    assert rec["acquired_flag"] == "", rec["acquired_flag"]
