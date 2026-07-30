"""Dynamic-range wedge test: 7 attenuation steps, labeled by position only.

No material assumptions are made: steps are indexed S1 (top, most attenuating in
the reference scans) through S7 (bottom). Reported metrics are the measured mean
pixel value per step, a linear fit of mean value vs step index, R^2, and
monotonicity. Constancy of these values over time is the QA criterion.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from ..features import linear_fit_r2, parabolic_refine, sample_profile
from .common import Ctx, rect_roi, segment, stats_for_roi


def _refine_axis(ctx: Ctx):
    """Refine the wedge column x-center (via the dark frame minima) and the step
    boundary y-positions (via gradient peaks along the wedge axis)."""
    T = ctx.T
    w = ctx.pdef.wedge
    cx = w["center_x_mm"]
    y_top, y_bot = w["y_top_mm"], w["y_bottom_mm"]
    n_steps = len(w["steps"])

    # --- x-center: horizontal profile at three heights, dark frame minima
    xs_found = []
    for y in (0.25 * y_top, 0.0, 0.25 * y_bot):
        p0 = T.mm_to_px([cx - 16, y])
        p1 = T.mm_to_px([cx + 16, y])
        ts, vals = sample_profile(ctx.pixels, p0, p1, 224, avg_half_width=6, n_avg=5)
        half = len(vals) // 2
        il = int(np.argmin(vals[:half]))
        ir = half + int(np.argmin(vals[half:]))
        mid_t = (ts[il] + ts[ir]) / 2.0
        u = (np.asarray(p1, float) - np.asarray(p0, float))
        u /= np.linalg.norm(u)
        pt = np.asarray(p0, float) + mid_t * u
        xs_found.append(float(T.px_to_mm(pt)[0]))
    cx_ref = float(np.median(xs_found))

    # --- step boundaries along the wedge axis
    p0 = T.mm_to_px([cx_ref, y_top + 6])
    p1 = T.mm_to_px([cx_ref, y_bot - 6])
    n = int(abs(y_top - y_bot + 12) * T.px_per_mm)
    ts, vals = sample_profile(ctx.pixels, p0, p1, n, avg_half_width=4 * T.px_per_mm / 2,
                              n_avg=7)
    sm = ndimage.gaussian_filter1d(vals, 2.0)
    grad = np.abs(np.gradient(sm))
    total_len = abs(y_top - y_bot + 12)

    def y_of_index(i):
        frac = np.interp(i, np.arange(len(ts)), ts) / ts[-1]
        return (y_top + 6) - frac * total_len

    samples_per_mm = len(ts) / total_len

    def refine_near(y_exp, half_mm):
        i_exp = (y_top + 6 - y_exp) * samples_per_mm
        i0 = int(max(0, i_exp - half_mm * samples_per_mm))
        i1 = int(min(len(grad) - 1, i_exp + half_mm * samples_per_mm))
        i = i0 + int(np.argmax(grad[i0:i1 + 1]))
        return float(y_of_index(parabolic_refine(grad, i)))

    # ends first (strong edges), then interior boundaries near their nominals
    y_top_ref = refine_near(y_top, 3.0)
    y_bot_ref = refine_near(y_bot, 3.0)
    nominal_bounds = w.get("nominal_boundaries_y_mm")
    if not nominal_bounds:
        step_mm = (y_top_ref - y_bot_ref) / n_steps
        nominal_bounds = [y_top_ref - k * step_mm for k in range(1, n_steps)]
    boundaries = [refine_near(y, 3.0) for y in nominal_bounds]
    return cx_ref, y_top_ref, y_bot_ref, boundaries


def propose(ctx: Ctx) -> dict:
    w = ctx.pdef.wedge
    n_steps = len(w["steps"])
    y_top, y_bot = w["y_top_mm"], w["y_bottom_mm"]
    step_mm = (y_top - y_bot) / n_steps
    try:
        cx_ref, y_top_ref, y_bot_ref, boundaries = _refine_axis(ctx)
        detected = True
    except Exception:
        cx_ref = w["center_x_mm"]
        y_top_ref, y_bot_ref = y_top, y_bot
        boundaries = [y_top - k * step_mm for k in range(1, n_steps)]
        detected = False

    edges = [y_top_ref] + boundaries + [y_bot_ref]
    margin = w.get("roi_margin_mm", 3.0)
    roi_w = w.get("roi_w_mm", 10.0)
    steps = []
    for i, st in enumerate(w["steps"]):
        yc = (edges[i] + edges[i + 1]) / 2.0
        h = max(abs(edges[i] - edges[i + 1]) - 2 * margin, 4.0)
        steps.append({
            "step": st["step"],
            "roi": rect_roi(ctx, (cx_ref, yc), (roi_w, h), 0.0,
                            roi_id=f"wedge/S{st['step']}"),
        })
    axis = segment(ctx, (cx_ref, y_top_ref + 4), (cx_ref, y_bot_ref - 4),
                   "wedge/axis")
    return {"center_x_mm": cx_ref, "boundaries_y_mm": boundaries,
            "y_top_mm": y_top_ref, "y_bottom_mm": y_bot_ref,
            "detected": detected, "steps": steps, "axis": axis}


def compute(ctx: Ctx, geometry: dict) -> dict:
    tol_r2 = ctx.pdef.tolerances.get("wedge_r2_min", 0.95)
    img_min, img_max = float(ctx.pixels.min()), float(ctx.pixels.max())
    rng = img_max - img_min
    rows = []
    for st in geometry["steps"]:
        s = stats_for_roi(ctx, st["roi"])
        saturated = (s["mean"] > img_max - 0.01 * rng) or \
                    (s["mean"] < img_min + 0.01 * rng) or s["std"] < 1e-9
        rows.append({"step": st["step"], "mean": s["mean"], "std": s["std"],
                     "n": s["n"], "saturated": bool(saturated)})
    idx = [r["step"] for r in rows]
    means = [r["mean"] for r in rows]
    fit = linear_fit_r2(idx, means)
    diffs = np.diff(means)
    monotonic = bool(np.all(diffs > 0) or np.all(diffs < 0))
    status = "pass" if (fit["r2"] >= tol_r2 and monotonic
                        and not any(r["saturated"] for r in rows)) else "fail"
    # profile along the wedge axis for the result plot
    ax = geometry["axis"]
    n = int(np.linalg.norm(np.asarray(ax["p1_px"]) - np.asarray(ax["p0_px"])))
    ts, vals = sample_profile(ctx.pixels, ax["p0_px"], ax["p1_px"], max(n, 64),
                              avg_half_width=3 * ctx.T.px_per_mm / 2, n_avg=5)
    return {
        "rows": rows, "fit": {"slope": fit["a"], "intercept": fit["b"],
                              "r2": fit["r2"]},
        "monotonic": monotonic, "r2_min": tol_r2, "status": status,
        "axis_profile": {
            "pos_mm": (ts * ctx.T.mm_per_px).tolist(),
            "value": np.asarray(vals).tolist(),
        },
    }
