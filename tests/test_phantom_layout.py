"""Per-phantom measuring-point layouts.

Two phantoms built to the same drawing are not the same object: on the MSF
reference scans the printed internal features sit millimetres apart between two
builds, while repeat scans of one build reproduce to a few tenths. That is
assembly, and it does not change over time — so a correction made once is worth
replaying on the next scan of the same phantom.

The three things that make it safe rather than dangerous:

* it is stored in phantom-frame millimetres, so it replays correctly when the
  phantom lies at a different angle on the detector;
* it is applied ON TOP of a fresh detection, never instead of one, and refused
  outright when it disagrees with that detection about where the phantom is;
* it dies with the last analysis that carries its phantom label, so it can
  never be inherited by an unrelated scan.
"""

from __future__ import annotations

import hashlib
import io
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import needs_samples
from phantom_qa import layout_profile, pipeline
from phantom_qa.analysis.common import Ctx
from phantom_qa.phantom_def import load_default
from phantom_qa.registration import Transform
from phantom_qa.store import Store
from test_authorization import ADMIN_PW, _build_app, _login
from test_store_labels import fake_scan


def _png(seed: int = 0) -> bytes:
    from PIL import Image
    arr = np.full((24, 24), 100 + (seed % 50), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def make_ctx(px_per_mm=7.0, n=2400, rotation_deg=0.0, mirrored=False):
    """A context whose transform can be turned, to prove the frame invariance."""
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:n, 0:n].astype(float)
    img = 2000 + 600 * np.sin(xx / 40.0) * np.cos(yy / 55.0)
    img += rng.normal(0, 20, img.shape)
    a = math.radians(rotation_deg)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    base = np.array([[px_per_mm, 0.0], [0.0, -px_per_mm]])
    if mirrored:
        base = base @ np.array([[-1.0, 0.0], [0.0, 1.0]])
    T = Transform(A=R @ base, t=np.array([n / 2.0, n / 2.0]))
    return Ctx(pixels=img, T=T, pdef=load_default())


def sample_geometry(ctx) -> dict:
    """A small but structurally faithful geometry blob."""
    from phantom_qa.analysis.common import (annulus_roi, circle_roi, rect_roi,
                                            segment)
    return {
        "uniformity": {
            "roi_size_mm": 30.0,
            "squares": [
                {"id": "C", "detected": True,
                 "roi": rect_roi(ctx, (0.0, 0.0), (30.0, 30.0), 0.0,
                                 "uniformity/C")},
                {"id": "TL", "detected": True,
                 "roi": rect_roi(ctx, (-100.0, 100.0), (30.0, 30.0), 0.0,
                                 "uniformity/TL")},
            ]},
        "linepairs": {
            "strip_angle_deg": 44.1, "roi_size_mm": 12.6,
            "roi_angle_offset_deg": 45.0,
            "groups": [
                {"id": "G2.0", "freq_lp_mm": 2.0, "detected": True,
                 "roi": rect_roi(ctx, (30.0, 30.0), (12.6, 12.6), 89.1,
                                 "linepairs/G2.0"),
                 "profile_seg": segment(ctx, (25.0, 25.0), (35.0, 35.0),
                                        "linepairs/G2.0/profile")},
                {"id": "G1.1", "freq_lp_mm": 1.1, "detected": True,
                 "roi": rect_roi(ctx, (60.0, 60.0), (12.6, 12.6), 89.1,
                                 "linepairs/G1.1"),
                 "profile_seg": segment(ctx, (55.0, 55.0), (65.0, 65.0),
                                        "linepairs/G1.1/profile")},
            ]},
        "lowcontrast": {
            "angle_deg": -45.1, "grid_shift_mm": [0.0, 0.0],
            "bg_ring_mm": [12.0, 16.0], "detected": True,
            "block": rect_roi(ctx, (-40.0, -40.0), (90.0, 40.0), -45.1,
                              "lowcontrast/block"),
            "circles": [
                {"id": "L1", "level": 1,
                 "roi": circle_roi(ctx, (-60.0, -35.0), 7.0, "lowcontrast/L1"),
                 "bg_roi": annulus_roi(ctx, (-60.0, -35.0), 12.0, 16.0,
                                       "lowcontrast/L1/bg"),
                 "full_circle": circle_roi(ctx, (-60.0, -35.0), 10.0,
                                           "lowcontrast/L1/outline")},
            ]},
        "wedge": {
            "center_x_mm": 50.0, "y_top_mm": 60.0, "y_bottom_mm": -60.0,
            "boundaries_y_mm": [40.0, 20.0, 0.0, -20.0, -40.0],
            "roi_size_mm": [10.0, 10.0], "detected": True,
            "steps": [
                {"step": 1, "roi": rect_roi(ctx, (50.0, 50.0), (10.0, 10.0),
                                            0.0, "wedge/S1")},
                {"step": 7, "roi": rect_roi(ctx, (50.0, -50.0), (10.0, 10.0),
                                            0.0, "wedge/S7")},
            ],
            "axis": segment(ctx, (50.0, 64.0), (50.0, -64.0), "wedge/axis")},
        "geometry": {
            "rulers": {"top": {"detected": True, "line_offsets_mm": [1, 2, 3]}},
            "field_edges": {"top": {"side": "top", "detected": True,
                                    "offset_from_edge_mm": 12.0}},
            "corners": {"TL": {"mm": [-150, 150]}}},
    }


# --------------------------------------------------------------- extraction

def test_only_millimetre_geometry_is_stored():
    """Pixel fields belong to one scan's transform; storing them would defeat
    the whole point."""
    ctx = make_ctx()
    layout = layout_profile.extract_layout(sample_geometry(ctx),
                                           pdef_version="1.0")
    blob = str(layout)
    for forbidden in ("center_px", "corners_px", "size_px", "radius_px",
                      "angle_img_deg", "p0_px", "p1_px",
                      "inner_radius_px", "outer_radius_px"):
        assert forbidden not in blob, f"{forbidden} leaked into the layout"


def test_every_adjustable_measuring_area_is_captured():
    ctx = make_ctx()
    layout = layout_profile.extract_layout(sample_geometry(ctx),
                                           pdef_version="1.0")
    ids = set(layout["rois"])
    assert {"uniformity/C", "uniformity/TL", "linepairs/G2.0",
            "linepairs/G2.0/profile", "lowcontrast/block", "lowcontrast/L1",
            "lowcontrast/L1/bg", "lowcontrast/L1/outline",
            "wedge/S1", "wedge/S7", "wedge/axis"} <= ids


def test_the_exposure_is_not_part_of_the_phantom():
    """Collimation and ruler readings describe this exposure, not the build."""
    ctx = make_ctx()
    layout = layout_profile.extract_layout(sample_geometry(ctx),
                                           pdef_version="1.0")
    assert not [i for i in layout["rois"] if i.startswith("field/")]
    assert not [i for i in layout["rois"] if i.startswith("ruler/")]
    assert "geometry" not in layout["scalars"]
    assert "field_edges" not in str(layout)


def test_a_test_that_failed_to_propose_is_skipped():
    ctx = make_ctx()
    geom = sample_geometry(ctx)
    geom["wedge"] = {"_error": "ValueError: no wedge"}
    layout = layout_profile.extract_layout(geom, pdef_version="1.0")
    assert not [i for i in layout["rois"] if i.startswith("wedge/")]


def test_per_phantom_scalars_travel_with_the_layout():
    ctx = make_ctx()
    layout = layout_profile.extract_layout(sample_geometry(ctx),
                                           pdef_version="1.0")
    assert layout["scalars"]["linepairs"]["strip_angle_deg"] == 44.1
    assert layout["scalars"]["lowcontrast"]["angle_deg"] == -45.1
    assert layout["scalars"]["wedge"]["center_x_mm"] == 50.0


def test_a_hand_set_profile_direction_keeps_its_flag():
    """linepairs.compute re-measures the direction by FFT unless the segment
    says a human set it — losing the flag silently discards the correction."""
    ctx = make_ctx()
    geom = sample_geometry(ctx)
    geom["linepairs"]["groups"][0]["profile_seg"]["manually_adjusted"] = True
    layout = layout_profile.extract_layout(geom, pdef_version="1.0")
    assert layout["rois"]["linepairs/G2.0/profile"]["manually_adjusted"] is True

    fresh = sample_geometry(ctx)
    out, _ = layout_profile.apply_layout(ctx, fresh, layout)
    seg = layout_profile.walk_find(out, "linepairs/G2.0/profile")
    assert seg["manually_adjusted"] is True


# ------------------------------------------------------- rotation invariance

@pytest.mark.parametrize("rotation_deg,mirrored",
                         [(0.0, False), (90.0, False), (180.0, False),
                          (270.0, False), (0.0, True), (90.0, True)])
def test_a_layout_replays_onto_a_differently_oriented_scan(rotation_deg, mirrored):
    """The scan the layout was confirmed on and the scan it is replayed onto
    have completely different pixel transforms. What must survive is the
    phantom-frame position — and the pixels must be re-derived, not reused."""
    src = make_ctx()
    layout = layout_profile.extract_layout(sample_geometry(src),
                                           pdef_version="1.0")
    # the operator had moved the centre square
    layout["rois"]["uniformity/C"]["center_mm"] = [12.0, -8.0]

    dst = make_ctx(rotation_deg=rotation_deg, mirrored=mirrored)
    fresh = sample_geometry(dst)
    out, report = layout_profile.apply_layout(dst, fresh, layout)

    roi = layout_profile.walk_find(out, "uniformity/C")
    assert roi["center_mm"] == pytest.approx([12.0, -8.0])
    # pixels recomputed for THIS scan's transform, not copied from the source
    expected_px = np.asarray(dst.T.mm_to_px([12.0, -8.0]), float)
    assert roi["center_px"] == pytest.approx(expected_px.tolist(), abs=1e-6)
    if rotation_deg or mirrored:
        src_px = np.asarray(src.T.mm_to_px([12.0, -8.0]), float)
        assert not np.allclose(roi["center_px"], src_px), (
            "the replay reused the source scan's pixel coordinates")
    assert report["n_skipped"] == 0


def test_a_replay_marks_where_the_positions_came_from():
    ctx = make_ctx()
    layout = layout_profile.extract_layout(sample_geometry(ctx),
                                           pdef_version="1.0")
    out, _ = layout_profile.apply_layout(ctx, sample_geometry(ctx), layout)
    assert layout_profile.walk_find(out, "uniformity/C")["from_profile"] is True


def test_measuring_areas_the_definition_no_longer_has_are_skipped():
    ctx = make_ctx()
    layout = layout_profile.extract_layout(sample_geometry(ctx),
                                           pdef_version="1.0")
    layout["rois"]["uniformity/GONE"] = {"type": "rect",
                                         "center_mm": [1.0, 1.0],
                                         "size_mm": [30.0, 30.0],
                                         "angle_deg": 0.0}
    out, report = layout_profile.apply_layout(ctx, sample_geometry(ctx), layout)
    assert "uniformity/GONE" in report["skipped"]
    assert out is not None


# --------------------------------------------------------- the safety gate

def test_a_layout_from_the_same_phantom_is_accepted():
    ctx = make_ctx()
    geom = sample_geometry(ctx)
    layout = layout_profile.extract_layout(geom, pdef_version="1.0")
    # an assembly difference of a few millimetres is normal between builds
    for rid in ("linepairs/G2.0", "wedge/S1"):
        layout["rois"][rid]["center_mm"] = [
            layout["rois"][rid]["center_mm"][0] + 4.0,
            layout["rois"][rid]["center_mm"][1] - 3.0]
    check = layout_profile.layout_agrees(layout, geom)
    assert check["ok"], check["reason"]


def test_a_layout_that_lands_a_hundred_millimetres_away_is_refused():
    """A phantom registered the wrong way up puts every ROI on the wrong
    object — and uniformity and the corner dimensions cannot detect it,
    because both are symmetric under every candidate orientation."""
    ctx = make_ctx()
    geom = sample_geometry(ctx)
    layout = layout_profile.extract_layout(geom, pdef_version="1.0")
    for rid in ("linepairs/G2.0", "linepairs/G1.1", "lowcontrast/block",
                "wedge/S1", "wedge/S7"):
        c = layout["rois"][rid]["center_mm"]
        layout["rois"][rid]["center_mm"] = [-c[0], -c[1]]   # 180-degree flip
    check = layout_profile.layout_agrees(layout, geom)
    assert not check["ok"]
    assert check["median_mm"] > layout_profile.ORIENTATION_TOLERANCE_MM
    assert "orientation" in check["reason"]


def test_the_gate_ignores_the_symmetric_patterns():
    """Uniformity votes 'fine' for every orientation, so it must not vote."""
    assert "uniformity/C" not in layout_profile.ORIENTATION_PROBES
    assert not [p for p in layout_profile.ORIENTATION_PROBES
                if p.startswith(("uniformity/", "ruler/", "field/"))]


def test_a_scan_with_no_detectable_patterns_refuses_the_layout():
    ctx = make_ctx()
    geom = sample_geometry(ctx)
    layout = layout_profile.extract_layout(geom, pdef_version="1.0")
    blind = {"uniformity": geom["uniformity"]}          # nothing orientable
    check = layout_profile.layout_agrees(layout, blind)
    assert not check["ok"]
    assert "cannot be checked" in check["reason"]


# ------------------------------------------------------------- the lifecycle

@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path))


def _analysis(store, phantom="MSF-01", seed=0):
    payload = _png(seed)
    return store.new_analysis(
        fake_scan(sha=hashlib.sha256(payload).hexdigest()), payload,
        "sig", "1.0.0", "1.0", labels={"site": "Goma", "phantom": phantom})


def test_a_layout_is_keyed_by_the_exact_label(store):
    """History groups on the raw value, so 'MSF-01' and 'msf-01' are two
    phantoms there. A folded key would serve both and break the delete rule."""
    _analysis(store, "MSF-01")
    store.save_phantom_profile("MSF-01", {"rois": {"a": {}}})
    assert store.get_phantom_profile("msf-01") is None
    assert store.get_phantom_profile(" MSF-01 ") is not None    # trimmed only


def test_a_near_duplicate_spelling_is_reported_not_merged(store):
    store.save_phantom_profile("MSF-01", {"rois": {}})
    out = store.save_phantom_profile("msf-01", {"rois": {}})
    assert out["near_miss"] == ["MSF-01"]


def test_an_empty_label_can_never_own_a_layout(store):
    for bad in ("", "   ", None):
        with pytest.raises(ValueError):
            store.save_phantom_profile(bad, {"rois": {}})


def test_renaming_the_last_analysis_off_a_label_drops_its_layout(store):
    aid = _analysis(store, "MSF-01")
    store.save_phantom_profile("MSF-01", {"rois": {}}, source_analysis_id=aid)
    out = store.set_labels(aid, {"phantom": "MSF-02"})
    assert out["profile_deleted"] is True
    assert store.get_phantom_profile("MSF-01") is None


def test_renaming_does_not_carry_the_layout_to_the_new_name(store):
    """The layout describes one physical phantom's assembly. Moving it onto a
    different name would apply one phantom's quirks to another."""
    aid = _analysis(store, "MSF-01")
    store.save_phantom_profile("MSF-01", {"rois": {"x": {}}},
                               source_analysis_id=aid)
    store.set_labels(aid, {"phantom": "MSF-02"})
    assert store.get_phantom_profile("MSF-02") is None


def test_renaming_one_of_several_keeps_the_layout(store):
    a = _analysis(store, "MSF-01", seed=1)
    b = _analysis(store, "MSF-01", seed=2)
    store.save_phantom_profile("MSF-01", {"rois": {}}, source_analysis_id=a)
    out = store.set_labels(b, {"phantom": "MSF-02"})
    assert out["profile_deleted"] is False
    assert store.get_phantom_profile("MSF-01") is not None


def test_blanking_the_label_prunes_the_layout(store):
    aid = _analysis(store, "MSF-01")
    store.save_phantom_profile("MSF-01", {"rois": {}}, source_analysis_id=aid)
    out = store.set_labels(aid, {"phantom": ""})
    assert out["profile_deleted"] is True
    assert store.get_phantom_profile("MSF-01") is None


def test_the_phantom_label_cannot_be_changed_behind_set_labels(store):
    """update() would bypass the prune and orphan a layout."""
    aid = _analysis(store, "MSF-01")
    with pytest.raises(ValueError, match="set_labels"):
        store.update(aid, phantom="MSF-02")


def test_saving_preserves_when_the_layout_was_first_created(store):
    _analysis(store, "MSF-01")
    first = store.save_phantom_profile("MSF-01", {"rois": {}})
    created = store.get_phantom_profile("MSF-01")["created_at"]
    store.save_phantom_profile("MSF-01", {"rois": {"b": {}}})
    assert store.get_phantom_profile("MSF-01")["created_at"] == created
    assert first["phantom"] == "MSF-01"


def test_the_listing_counts_the_analyses_still_using_each_layout(store):
    _analysis(store, "MSF-01", seed=1)
    _analysis(store, "MSF-01", seed=2)
    store.save_phantom_profile("MSF-01", {"rois": {}})
    rows = store.list_phantom_profiles()
    assert len(rows) == 1
    assert rows[0]["phantom_key"] == "MSF-01"
    assert rows[0]["n_analyses"] == 2


# ------------------------------------------------------------------ over HTTP

@pytest.fixture()
def mod(tmp_path, monkeypatch):
    return _build_app(tmp_path, monkeypatch)


@pytest.fixture()
def client(mod):
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    return c


def test_confirming_stage_c_stores_the_layout(client, mod):
    ctx = make_ctx()
    aid = _analysis(mod.store, "MSF-01")
    mod.store.set_geometry_baseline(aid, pipeline.to_jsonable(sample_geometry(ctx)))
    r = client.post(f"/api/analyses/{aid}/confirm", json={"stage": "C"})
    assert r.status_code == 200, r.text
    assert r.json()["profile_saved"] is True
    assert r.json()["profile"]["phantom"] == "MSF-01"
    assert mod.store.get_phantom_profile("MSF-01")["layout"]["rois"]


def test_an_operator_can_decline_to_store_the_layout(client, mod):
    """A one-off correction should not become the default for every future
    scan of that phantom."""
    ctx = make_ctx()
    aid = _analysis(mod.store, "MSF-01")
    mod.store.set_geometry_baseline(aid, pipeline.to_jsonable(sample_geometry(ctx)))
    r = client.post(f"/api/analyses/{aid}/confirm",
                    json={"stage": "C", "save_profile": False})
    assert r.json()["profile_saved"] is False
    assert mod.store.get_phantom_profile("MSF-01") is None


def test_an_unnamed_phantom_says_why_nothing_was_stored(client, mod):
    ctx = make_ctx()
    aid = _analysis(mod.store, "")
    mod.store.set_geometry_baseline(aid, pipeline.to_jsonable(sample_geometry(ctx)))
    r = client.post(f"/api/analyses/{aid}/confirm", json={"stage": "C"})
    assert r.json()["profile_saved"] is False
    assert "phantom" in r.json()["profile_error"]


def test_confirming_another_stage_stores_nothing(client, mod):
    ctx = make_ctx()
    aid = _analysis(mod.store, "MSF-01")
    mod.store.set_geometry_baseline(aid, pipeline.to_jsonable(sample_geometry(ctx)))
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "B"})
    assert mod.store.get_phantom_profile("MSF-01") is None


def test_the_upload_advertises_an_available_layout(client, mod):
    _analysis(mod.store, "MSF-01", seed=1)
    mod.store.save_phantom_profile("MSF-01", {"rois": {"uniformity/C": {}}})
    r = client.post("/api/analyses",
                    files={"file": ("scan.png", io.BytesIO(_png(9)))},
                    data={"site": "Goma", "phantom": "MSF-01"})
    assert r.status_code == 200, r.text
    assert r.json()["phantom_profile"]["phantom"] == "MSF-01"


def test_the_profile_listing_is_available_to_a_signed_in_user(client, mod):
    _analysis(mod.store, "MSF-01")
    mod.store.save_phantom_profile("MSF-01", {"rois": {}})
    r = client.get("/api/phantom_profiles")
    assert r.status_code == 200
    assert [p["phantom_key"] for p in r.json()["profiles"]] == ["MSF-01"]


def test_forgetting_a_layout_needs_the_administrator_password(client, mod):
    _analysis(mod.store, "MSF-01")
    mod.store.save_phantom_profile("MSF-01", {"rois": {}})
    bad = client.post("/api/phantom_profiles/forget",
                      json={"phantom": "MSF-01", "admin_password": "wrong"})
    assert bad.status_code == 401
    assert mod.store.get_phantom_profile("MSF-01") is not None

    ok = client.post("/api/phantom_profiles/forget",
                     json={"phantom": "MSF-01", "admin_password": ADMIN_PW,
                           "reason": "rebuilt the phantom"})
    assert ok.status_code == 200 and ok.json()["deleted"] is True
    assert mod.store.get_phantom_profile("MSF-01") is None


# --------------------------------------------------- on the reference scans

@needs_samples
def test_real_scans_of_one_phantom_land_on_the_same_millimetres(sample_scans,
                                                                pdef):
    """The claim the whole feature rests on, measured rather than assumed:
    the phantom frame is reproducible between scans, so a stored layout is
    meaningful across them."""
    ctxs = []
    for scan in sample_scans[:2]:
        reg = pipeline.run_stage_a(scan, pdef)
        ctxs.append(pipeline.build_ctx(scan, pdef, reg, {"scan_meta": scan.meta}))
    a, b = (pipeline.propose_all(c) for c in ctxs)
    la = layout_profile.extract_layout(a, pdef_version=pdef.version)
    check = layout_profile.layout_agrees(la, b)
    assert check["ok"], check["reason"]
    assert check["median_mm"] < layout_profile.ORIENTATION_TOLERANCE_MM


@needs_samples
def test_a_layout_survives_the_phantom_being_rotated_on_the_detector(sample_scans,
                                                                    pdef):
    """The requirement in the tester's own words: 'every scan the phantom can
    be rotated differently'."""
    scan = sample_scans[0]
    reg = pipeline.run_stage_a(scan, pdef)
    ctx = pipeline.build_ctx(scan, pdef, reg, {"scan_meta": scan.meta})
    layout = layout_profile.extract_layout(pipeline.propose_all(ctx),
                                           pdef_version=pdef.version)

    turned = type(scan)(**{**scan.__dict__, "pixels": np.rot90(scan.pixels, 1)}) \
        if hasattr(scan, "__dict__") else None
    if turned is None:
        pytest.skip("scan object is not reconstructible")
    reg2 = pipeline.run_stage_a(turned, pdef)
    ctx2 = pipeline.build_ctx(turned, pdef, reg2, {"scan_meta": turned.meta})
    fresh = pipeline.propose_all(ctx2)

    check = layout_profile.layout_agrees(layout, fresh)
    assert check["ok"], (
        f"a layout from the upright scan was refused on the same scan rotated "
        f"90 degrees: {check['reason']}")

    out, report = layout_profile.apply_layout(ctx2, fresh, layout)
    assert report["n_applied"] > 10
    roi = layout_profile.walk_find(out, "uniformity/C")
    expected = np.asarray(ctx2.T.mm_to_px(roi["center_mm"]), float)
    assert roi["center_px"] == pytest.approx(expected.tolist(), abs=1e-6)
