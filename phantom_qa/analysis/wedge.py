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


def _full_scale(ctx: Ctx) -> float:
    """Detector full-scale value. Prefers the DICOM bit depth; otherwise infers
    the next power of two above the image maximum (integer detector data)."""
    meta = (ctx.params or {}).get("scan_meta") or {}
    bits = meta.get("BitsStored")
    try:
        if bits:
            return float(2 ** int(bits) - 1)
    except (TypeError, ValueError):
        pass
    hi = float(ctx.pixels.max())
    if hi <= 0:
        return 1.0
    return float(2 ** int(np.ceil(np.log2(hi + 1))) - 1)


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
    roi_w = w.get("roi_w_mm", 10.0)
    # Every step ROI is the SAME fixed size, so a mean from one phantom is
    # directly comparable with the same step on another. Sizing each ROI to its
    # own step would make the areas differ (the steps are not equal heights) and
    # quietly change how much of each step is averaged.
    roi_h = w.get("roi_h_mm", 10.0)
    steps = []
    for i, st in enumerate(w["steps"]):
        yc = (edges[i] + edges[i + 1]) / 2.0
        step_h = abs(edges[i] - edges[i + 1])
        steps.append({
            "step": st["step"],
            "step_height_mm": float(step_h),
            "fits": bool(step_h >= roi_h + 2.0),
            "roi": rect_roi(ctx, (cx_ref, yc), (roi_w, roi_h), 0.0,
                            roi_id=f"wedge/S{st['step']}"),
        })
    axis = segment(ctx, (cx_ref, y_top_ref + 4), (cx_ref, y_bot_ref - 4),
                   "wedge/axis")
    return {"center_x_mm": cx_ref, "boundaries_y_mm": boundaries,
            "y_top_mm": y_top_ref, "y_bottom_mm": y_bot_ref,
            "detected": detected, "steps": steps, "axis": axis,
            "roi_size_mm": [roi_w, roi_h]}


def compute(ctx: Ctx, geometry: dict) -> dict:
    """Wedge metrics.

    The printed steps are NOT equal increments of attenuation — measured on the
    reference scans the response is reproducibly S-shaped (linear-fit R^2 ~0.92
    with the same residual pattern in every scan). Judging a detector by how
    linear that curve is would fail a perfectly good detector because of the
    phantom's own step geometry. So:

      hard  (fail) : monotonic response, no saturated step
      soft  (warn) : linear-fit R^2 below ``wedge_r2_min``

    The R^2 remains reported as a shape descriptor, and the per-step means are
    what actually gets trended against the baseline.
    """
    tol_r2 = ctx.pdef.tolerances.get("wedge_r2_min", 0.85)
    full_scale = _full_scale(ctx)
    rows = []
    for st in geometry["steps"]:
        s = stats_for_roi(ctx, st["roi"])
        # Saturation means pinned at the DETECTOR's limit — not merely being the
        # brightest thing in this particular image. A flat (zero-variance) ROI
        # is the other signature of clipping.
        saturated = (s["mean"] >= 0.995 * full_scale
                     or s["mean"] <= 0.005 * full_scale
                     or s["std"] < 0.5)
        rows.append({"step": st["step"], "mean": s["mean"], "std": s["std"],
                     "n": s["n"], "saturated": bool(saturated)})
    idx = [r["step"] for r in rows]
    means = [r["mean"] for r in rows]
    fit = linear_fit_r2(idx, means)
    diffs = np.diff(means)
    monotonic = bool(np.all(diffs > 0) or np.all(diffs < 0))
    any_sat = any(r["saturated"] for r in rows)
    span = float(max(means) - min(means))
    dyn_ratio = float(max(means) / max(min(means), 1e-9))
    if not monotonic or any_sat:
        status = "fail"
    elif fit["r2"] < tol_r2:
        status = "warn"
    else:
        status = "pass"
    reasons = []
    if status == "pass":
        reasons.append(
            f"response decreases monotonically over all {len(rows)} steps, "
            f"dynamic range {dyn_ratio:.1f}x, no step saturated "
            f"(linear-fit R² {fit['r2']:.3f}).")
    if not monotonic:
        breaks = [f"S{rows[i]['step']}->S{rows[i+1]['step']}"
                  for i in range(len(rows) - 1)
                  if (means[i + 1] - means[i]) * (means[1] - means[0]) < 0]
        reasons.append(
            "response is not monotonic across the steps"
            + (f" (reverses at {', '.join(breaks)})" if breaks else "")
            + ". A step ROI sitting on the wrong step, or on a boundary, is the "
              "usual cause — check the wedge ROIs in step C.")
    if any_sat:
        sat = [f"S{r['step']}" for r in rows if r["saturated"]]
        reasons.append(
            f"saturated step(s): {', '.join(sat)} — the value is pinned at the "
            f"detector limit or the ROI has no variation, so the measurement "
            f"there is meaningless. Reduce the exposure or check the ROI.")
    if fit["r2"] < tol_r2:
        reasons.append(f"linear-fit R² {fit['r2']:.3f} below {tol_r2:.2f} "
                       f"(shape descriptor only — the phantom's steps are not "
                       f"equal attenuation increments)")
    # profile along the wedge axis for the result plot
    ax = geometry["axis"]
    n = int(np.linalg.norm(np.asarray(ax["p1_px"]) - np.asarray(ax["p0_px"])))
    ts, vals = sample_profile(ctx.pixels, ax["p0_px"], ax["p1_px"], max(n, 64),
                              avg_half_width=3 * ctx.T.px_per_mm / 2, n_avg=5)
    return {
        "rows": rows, "fit": {"slope": fit["a"], "intercept": fit["b"],
                              "r2": fit["r2"],
                              "residuals_pct_of_span": [
                                  100.0 * r / span if span else 0.0
                                  for r in fit["residuals"]]},
        "monotonic": monotonic, "saturated": any_sat,
        "span": span, "dynamic_range_ratio": dyn_ratio,
        "r2_min": tol_r2, "status": status, "reasons": reasons,
        "axis_profile": {
            "pos_mm": (ts * ctx.T.mm_per_px).tolist(),
            "value": np.asarray(vals).tolist(),
        },
    }
