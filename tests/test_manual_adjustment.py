"""Manual ROI correction must actually change the measurement.

When automatic placement fails — which is expected on a phantom that differs
from the definition — the user drags and rotates the ROIs by hand. Every
companion of an ROI (background ring, object outline, profile line) has to
travel with it, and the returned numbers must come from the new position.
"""

import numpy as np
import pytest

from phantom_qa.analysis import linepairs, lowcontrast, wedge
from phantom_qa.analysis.common import (Ctx, roi_angle_deg, roi_center_from_px,
                                        roi_center_mm, roi_rotate,
                                        roi_translate_mm, stats_for_roi)
from phantom_qa.phantom_def import load_default
from phantom_qa.registration import Transform


@pytest.fixture()
def pdef():
    return load_default()


def make_ctx(px_per_mm=7.0, n=2400, pdef=None, seed=3):
    """A synthetic field with structure, so moving an ROI changes the numbers."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:n, 0:n].astype(float)
    img = 2000 + 600 * np.sin(xx / 40.0) * np.cos(yy / 55.0)
    img += rng.normal(0, 20, img.shape)
    T = Transform(A=np.array([[px_per_mm, 0.0], [0.0, -px_per_mm]]),
                  t=np.array([n / 2.0, n / 2.0]))
    return Ctx(pixels=img, T=T, pdef=pdef or load_default())


# ------------------------------------------------------------- ROI geometry

def test_moving_a_rect_changes_its_statistics(pdef):
    ctx = make_ctx(pdef=pdef)
    from phantom_qa.analysis.common import rect_roi
    roi = rect_roi(ctx, (0.0, 0.0), (12.6, 12.6), 0.0, "t")
    before = stats_for_roi(ctx, roi)
    moved = roi_center_from_px(ctx, roi, ctx.T.mm_to_px((25.0, 15.0)))
    after = stats_for_roi(ctx, moved)
    assert moved["center_mm"] == pytest.approx([25.0, 15.0], abs=0.01)
    assert abs(after["mean"] - before["mean"]) > 1e-9


def test_rotating_a_rect_changes_its_statistics(pdef):
    ctx = make_ctx(pdef=pdef)
    from phantom_qa.analysis.common import rect_roi
    roi = rect_roi(ctx, (0.0, 0.0), (30.0, 8.0), 0.0, "t")
    before = stats_for_roi(ctx, roi)
    turned = roi_rotate(ctx, roi, 55.0)
    after = stats_for_roi(ctx, turned)
    assert turned["angle_deg"] == pytest.approx(55.0)
    assert abs(after["std"] - before["std"]) > 1e-9
    assert roi_angle_deg(turned) == pytest.approx(55.0)


def test_segments_translate_as_a_whole(pdef):
    ctx = make_ctx(pdef=pdef)
    from phantom_qa.analysis.common import segment
    seg = segment(ctx, (-5.0, 0.0), (5.0, 0.0), "s")
    length = np.hypot(*(np.array(seg["p1_mm"]) - np.array(seg["p0_mm"])))
    moved = roi_center_from_px(ctx, seg, ctx.T.mm_to_px((20.0, 10.0)))
    assert roi_center_mm(moved) == pytest.approx([20.0, 10.0], abs=0.01)
    new_len = np.hypot(*(np.array(moved["p1_mm"]) - np.array(moved["p0_mm"])))
    assert new_len == pytest.approx(length), "a move must not change the length"


def test_segments_rotate_about_their_own_centre(pdef):
    ctx = make_ctx(pdef=pdef)
    from phantom_qa.analysis.common import segment
    seg = segment(ctx, (-5.0, 0.0), (5.0, 0.0), "s")
    turned = roi_rotate(ctx, seg, 90.0)
    assert roi_center_mm(turned) == pytest.approx([0.0, 0.0], abs=1e-6)
    assert roi_angle_deg(turned) == pytest.approx(90.0, abs=1e-6)
    length = np.hypot(*(np.array(turned["p1_mm"]) - np.array(turned["p0_mm"])))
    assert length == pytest.approx(10.0)


def test_translate_helper_handles_every_type(pdef):
    ctx = make_ctx(pdef=pdef)
    from phantom_qa.analysis.common import (annulus_roi, circle_roi, rect_roi,
                                            segment)
    for roi in (rect_roi(ctx, (0, 0), (10, 10), 0.0, "a"),
                circle_roi(ctx, (0, 0), 7.0, "b"),
                annulus_roi(ctx, (0, 0), 12.0, 16.0, "c"),
                segment(ctx, (-5, 0), (5, 0), "d")):
        moved = roi_translate_mm(ctx, roi, 8.0, -3.0)
        assert roi_center_mm(moved) == pytest.approx([8.0, -3.0], abs=1e-6), \
            f"{roi['type']} did not translate"


# --------------------------------------------------- line-pair ROI placement

def test_line_roi_is_offset_45_degrees_from_the_strip(pdef):
    """The guide places the ROI corners on the nub markers and the centre line,
    i.e. rotated from the strip axis rather than square to it."""
    ctx = make_ctx(pdef=pdef)
    geom = linepairs.propose(ctx)
    strip = geom["strip_angle_deg"]
    assert geom["roi_angle_offset_deg"] == pytest.approx(45.0)
    for g in geom["groups"]:
        offset = (g["roi"]["angle_deg"] - strip) % 180.0
        assert offset == pytest.approx(45.0, abs=1.0), \
            f"{g['id']} ROI is at {offset:.1f} deg to the strip, expected 45"


def test_profile_follows_a_moved_line_roi(pdef):
    """The reported numbers must come from where the square now is."""
    ctx = make_ctx(pdef=pdef)
    geom = linepairs.propose(ctx)
    g = geom["groups"][0]
    target = [g["roi"]["center_mm"][0] + 20.0, g["roi"]["center_mm"][1] - 12.0]

    g["roi"] = roi_center_from_px(ctx, g["roi"], ctx.T.mm_to_px(target))
    g["profile_seg"] = linepairs.profile_for_center(
        ctx, g["roi"]["center_mm"], geom["roi_size_mm"], g["profile_seg"]["id"])

    seg_centre = roi_center_mm(g["profile_seg"])
    assert seg_centre == pytest.approx(target, abs=0.5), \
        "the profile line stayed behind when the ROI moved"


def test_moving_a_line_roi_changes_the_reported_sd(pdef):
    ctx = make_ctx(pdef=pdef)
    geom = linepairs.propose(ctx)
    before = linepairs.compute(ctx, geom)["rows"][0]["std"]

    g = geom["groups"][0]
    target = [g["roi"]["center_mm"][0] + 18.0, g["roi"]["center_mm"][1] + 9.0]
    g["roi"] = roi_center_from_px(ctx, g["roi"], ctx.T.mm_to_px(target))
    g["profile_seg"] = linepairs.profile_for_center(
        ctx, g["roi"]["center_mm"], geom["roi_size_mm"], g["profile_seg"]["id"])

    after = linepairs.compute(ctx, geom)["rows"][0]["std"]
    assert abs(after - before) > 1e-6, \
        "SD did not change after moving the ROI — it is still measuring the old spot"


def test_profile_is_sampled_at_the_roi_not_the_stale_segment(pdef):
    """Even with a stale stored segment, compute() must follow the ROI."""
    ctx = make_ctx(pdef=pdef)
    geom = linepairs.propose(ctx)
    g = geom["groups"][0]
    stale = dict(g["profile_seg"])                 # deliberately left behind
    g["roi"] = roi_center_from_px(
        ctx, g["roi"], ctx.T.mm_to_px([g["roi"]["center_mm"][0] + 30.0,
                                       g["roi"]["center_mm"][1]]))
    g["profile_seg"] = stale
    res = linepairs.compute(ctx, geom)["rows"][0]
    prof = res["linearity"]["profile"]
    assert prof["pos_mm"], "a profile should still be produced"
    # the sampled values must differ from those at the stale location
    g2 = dict(g)
    res_stale = linepairs._analyze_profile(ctx, stale, g["freq_lp_mm"],
                                           center_mm=roi_center_mm(stale))
    assert prof["value"] != res_stale["profile"]["value"], \
        "compute() sampled the stale segment position instead of the ROI"


def test_manual_profile_direction_is_respected(pdef):
    """A segment the user rotated must keep that direction."""
    ctx = make_ctx(pdef=pdef)
    geom = linepairs.propose(ctx)
    g = geom["groups"][0]
    turned = roi_rotate(ctx, g["profile_seg"], 12.0)
    turned["manually_adjusted"] = True
    g["profile_seg"] = turned
    out = linepairs._analyze_profile(ctx, turned, g["freq_lp_mm"],
                                     center_mm=g["roi"]["center_mm"])
    assert out["fft_freq_lp_mm"] is None, \
        "a hand-set direction must not be overridden by the FFT"


# ------------------------------------------------------------- low contrast

def test_low_contrast_background_ring_is_concentric(pdef):
    ctx = make_ctx(pdef=pdef)
    geom = lowcontrast.propose(ctx)
    for c in geom["circles"]:
        assert c["bg_roi"]["center_mm"] == pytest.approx(c["roi"]["center_mm"])
        assert c["full_circle"]["center_mm"] == pytest.approx(c["roi"]["center_mm"])


def test_moving_a_circle_must_move_its_ring_and_outline(pdef):
    """The ring is what the CNR background is taken from; if it stays behind,
    the displayed ROI and the measurement disagree."""
    ctx = make_ctx(pdef=pdef)
    geom = lowcontrast.propose(ctx)
    c = geom["circles"][0]
    target = [c["roi"]["center_mm"][0] + 14.0, c["roi"]["center_mm"][1] + 6.0]
    old = roi_center_mm(c["roi"])
    c["roi"] = roi_center_from_px(ctx, c["roi"], ctx.T.mm_to_px(target))
    dx = roi_center_mm(c["roi"])[0] - old[0]
    dy = roi_center_mm(c["roi"])[1] - old[1]
    c["bg_roi"] = roi_translate_mm(ctx, c["bg_roi"], dx, dy)
    c["full_circle"] = roi_translate_mm(ctx, c["full_circle"], dx, dy)

    assert c["bg_roi"]["center_mm"] == pytest.approx(target, abs=0.01)
    assert c["full_circle"]["center_mm"] == pytest.approx(target, abs=0.01)


# -------------------------------------------------------------------- wedge

def test_every_wedge_roi_is_the_same_fixed_size(pdef):
    """Comparing the same step between phantoms requires identical areas."""
    ctx = make_ctx(pdef=pdef)
    geom = wedge.propose(ctx)
    sizes = {tuple(np.round(s["roi"]["size_mm"], 6)) for s in geom["steps"]}
    assert len(sizes) == 1, f"wedge ROI sizes differ between steps: {sizes}"
    assert sizes.pop() == (pdef.wedge["roi_w_mm"], pdef.wedge["roi_h_mm"])


def test_wedge_roi_size_comes_from_the_definition(pdef):
    ctx = make_ctx(pdef=pdef)
    geom = wedge.propose(ctx)
    assert geom["roi_size_mm"] == [pdef.wedge["roi_w_mm"],
                                   pdef.wedge["roi_h_mm"]]
    for s in geom["steps"]:
        assert "step_height_mm" in s and "fits" in s


def test_wedge_roi_pixel_counts_match(pdef):
    """Same size in mm must mean the same number of pixels averaged."""
    ctx = make_ctx(pdef=pdef)
    geom = wedge.propose(ctx)
    counts = {stats_for_roi(ctx, s["roi"])["n"] for s in geom["steps"]}
    assert max(counts) - min(counts) <= 2, \
        f"step ROIs average different pixel counts: {sorted(counts)}"
