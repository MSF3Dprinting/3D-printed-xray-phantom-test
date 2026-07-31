"""Wedge pass criteria and annulus-ROI behaviour."""

import numpy as np
import pytest

from phantom_qa.analysis import wedge
from phantom_qa.analysis.common import Ctx, annulus_roi, circle_roi, stats_for_roi
from phantom_qa.features import roi_stats_annulus
from phantom_qa.phantom_def import load_default
from phantom_qa.registration import Transform


def make_ctx(img, px_per_mm=4.0, pdef=None):
    T = Transform(A=np.array([[px_per_mm, 0.0], [0.0, -px_per_mm]]),
                  t=np.array([img.shape[1] / 2.0, img.shape[0] / 2.0]))
    return Ctx(pixels=img, T=T, pdef=pdef or load_default())


def wedge_geometry(ctx, means):
    """Build a wedge geometry dict with ROIs over synthetic steps."""
    pdef = ctx.pdef
    w = pdef.wedge
    n = len(means)
    y_top, y_bot = w["y_top_mm"], w["y_bottom_mm"]
    h = (y_top - y_bot) / n
    from phantom_qa.analysis.common import rect_roi, segment
    steps = []
    for i in range(n):
        yc = y_top - (i + 0.5) * h
        steps.append({"step": i + 1,
                      "roi": rect_roi(ctx, (w["center_x_mm"], yc), (10.0, h - 6),
                                      0.0, roi_id=f"wedge/S{i+1}")})
    return {"steps": steps,
            "axis": segment(ctx, (w["center_x_mm"], y_top),
                            (w["center_x_mm"], y_bot), "wedge/axis")}


def synth_wedge(means, px_per_mm=4.0, noise=6.0, seed=1):
    """Image with a vertical column of 7 steps at the given mean values."""
    rng = np.random.default_rng(seed)
    pdef = load_default()
    w = pdef.wedge
    n_px = int(320 * px_per_mm)
    img = np.full((n_px, n_px), 1500.0)
    T = Transform(A=np.array([[px_per_mm, 0.0], [0.0, -px_per_mm]]),
                  t=np.array([n_px / 2.0, n_px / 2.0]))
    yy, xx = np.mgrid[0:n_px, 0:n_px].astype(float)
    X = (xx - n_px / 2) / px_per_mm
    Y = (n_px / 2 - yy) / px_per_mm
    y_top, y_bot = w["y_top_mm"], w["y_bottom_mm"]
    h = (y_top - y_bot) / len(means)
    for i, m in enumerate(means):
        top = y_top - i * h
        bot = y_top - (i + 1) * h
        sel = (np.abs(X - w["center_x_mm"]) < 12) & (Y <= top) & (Y > bot)
        img[sel] = m
    img += rng.normal(0, noise, img.shape)
    return Ctx(pixels=img, T=T, pdef=pdef)


# Reproduced from the six reference scans: the printed steps are not equal
# attenuation increments, so the response is S-shaped (linear R^2 ~ 0.92).
REAL_SHAPE = [3545, 3448, 3288, 2881, 2327, 1753, 855]


def test_real_wedge_shape_passes():
    """The characteristic S-shape of this phantom must not be a failure."""
    ctx = synth_wedge(REAL_SHAPE)
    res = wedge.compute(ctx, wedge_geometry(ctx, REAL_SHAPE))
    assert res["monotonic"]
    assert res["fit"]["r2"] < 0.95, "test premise: this shape is not linear"
    assert res["status"] == "pass", (
        f"S-shaped but monotonic wedge should pass, got {res['status']} "
        f"({res['reasons']})")


def test_non_monotonic_wedge_fails():
    bad = [3545, 3448, 3288, 3400, 2327, 1753, 855]   # step 4 goes back up
    ctx = synth_wedge(bad)
    res = wedge.compute(ctx, wedge_geometry(ctx, bad))
    assert not res["monotonic"]
    assert res["status"] == "fail"
    assert any("monotonic" in r for r in res["reasons"])


def test_very_nonlinear_wedge_warns_not_fails():
    """Below the R² threshold but still monotonic -> warn, never a hard fail."""
    curved = [4000, 3990, 3970, 3930, 3850, 3000, 200]
    ctx = synth_wedge(curved)
    res = wedge.compute(ctx, wedge_geometry(ctx, curved))
    assert res["monotonic"]
    assert res["fit"]["r2"] < res["r2_min"]
    assert res["status"] == "warn"


def test_wedge_reports_dynamic_range():
    ctx = synth_wedge(REAL_SHAPE)
    res = wedge.compute(ctx, wedge_geometry(ctx, REAL_SHAPE))
    assert res["dynamic_range_ratio"] == pytest.approx(
        REAL_SHAPE[0] / REAL_SHAPE[-1], rel=0.05)
    assert len(res["fit"]["residuals_pct_of_span"]) == len(REAL_SHAPE)


# ------------------------------------------------------------------- annulus

def test_annulus_stats_exclude_the_disc():
    """The background ring must sample only the ring, not the object inside."""
    img = np.full((400, 400), 100.0)
    yy, xx = np.mgrid[0:400, 0:400]
    img[(xx - 200) ** 2 + (yy - 200) ** 2 <= 40 ** 2] = 10.0   # dark disc
    s = roi_stats_annulus(img, (200, 200), r_inner=60, r_outer=80)
    assert s["mean"] == pytest.approx(100.0)
    assert s["n"] > 0


def test_annulus_roi_geometry_and_stats():
    img = np.full((400, 400), 100.0)
    yy, xx = np.mgrid[0:400, 0:400]
    img[(xx - 200) ** 2 + (yy - 200) ** 2 <= 20 ** 2] = 40.0
    ctx = make_ctx(img, px_per_mm=4.0)
    obj = circle_roi(ctx, (0.0, 0.0), 7.0, "obj")
    bg = annulus_roi(ctx, (0.0, 0.0), 12.0, 16.0, "bg")
    assert bg["type"] == "annulus"
    assert bg["outer_radius_px"] > bg["inner_radius_px"]
    s_obj = stats_for_roi(ctx, obj)
    s_bg = stats_for_roi(ctx, bg)
    assert s_obj["mean"] == pytest.approx(40.0, abs=1e-6)
    assert s_bg["mean"] == pytest.approx(100.0, abs=1e-6)


def test_lowcontrast_background_is_not_on_the_block_midline():
    """Regression: background ROIs used to be stacked on the block centre line,
    where they overlapped each other and sat on top of the real circles."""
    from phantom_qa.analysis import lowcontrast
    pdef = load_default()
    img = np.full((2400, 2400), 2400.0)
    ctx = make_ctx(img, px_per_mm=7.0, pdef=pdef)
    geom = lowcontrast.propose(ctx)
    centers = []
    for c in geom["circles"]:
        assert c["bg_roi"]["type"] == "annulus"
        # concentric with its own object, so it can never drift onto another
        assert c["bg_roi"]["center_mm"] == pytest.approx(c["roi"]["center_mm"])
        centers.append(tuple(np.round(c["bg_roi"]["center_mm"], 3)))
    assert len(set(centers)) == len(centers), \
        "background ROIs must not coincide with one another"
