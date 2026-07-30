"""Low-contrast test: 8 circles (diameter 10 mm) in the rounded-rectangle block.

Guide: CNR = (mu_obj - mu_bg) / sqrt(sigma_obj^2 + sigma_bg^2) per circle.
The circles are printed with fewer layers than the surroundings -> less
attenuating -> darker (lower value) than the block background.

No nominal contrast values are assumed: circles carry a positional design-order
``level`` (L1 = weakest ... L8 = strongest, as measured on the reference scans).
The QA criterion is constancy of each circle's CNR over time.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from .common import Ctx, circle_roi, rect_roi, stats_for_roi


def _refine_block(ctx: Ctx):
    """Refine block center and long-axis angle from the smoothed bright blob."""
    T = ctx.T
    lc = ctx.pdef.lowcontrast
    c_px = np.asarray(T.mm_to_px(lc["center_mm"]), float)
    half = int(60 * T.px_per_mm)
    x0, y0 = int(c_px[0]) - half, int(c_px[1]) - half
    sub = ctx.pixels[max(0, y0):y0 + 2 * half, max(0, x0):x0 + 2 * half]
    ds = 4
    small = sub[::ds, ::ds]
    sm = ndimage.gaussian_filter(small, 3)
    lo, hi = np.percentile(sm, [20, 99])
    blob = sm > lo + 0.55 * (hi - lo)
    lab, nl = ndimage.label(blob)
    if nl == 0:
        return lc["center_mm"], lc["angle_deg"], False
    sizes = ndimage.sum(blob, lab, range(1, nl + 1))
    big = 1 + int(np.argmax(sizes))
    ys, xs = np.nonzero(lab == big)
    cx = xs.mean() * ds + x0
    cy = ys.mean() * ds + y0
    dx = (xs - xs.mean()) * ds
    dy = (ys - ys.mean()) * ds
    cov = np.array([[np.mean(dx * dx), np.mean(dx * dy)],
                    [np.mean(dx * dy), np.mean(dy * dy)]])
    evals, evecs = np.linalg.eigh(cov)
    v = evecs[:, int(np.argmax(evals))]          # long axis, image px frame
    v_mm = ctx.T.px_to_mm(np.array([cx, cy]) + v * 50) - ctx.T.px_to_mm([cx, cy])
    ang = float(np.degrees(np.arctan2(v_mm[1], v_mm[0])))
    # snap to the nominal angle modulo 180 deg
    nom = lc["angle_deg"]
    for cand in (ang, ang + 180, ang - 180):
        if abs(cand - nom) < 25:
            ang = cand
            break
    else:
        return lc["center_mm"], nom, False
    center = ctx.T.px_to_mm([cx, cy])
    if np.hypot(center[0] - lc["center_mm"][0], center[1] - lc["center_mm"][1]) > 15:
        return lc["center_mm"], nom, False
    return [float(center[0]), float(center[1])], ang, True


def _circle_response(ctx: Ctx, center_mm, dia_mm: float) -> float:
    """Matched-filter response for a dark disc: annulus mean - disc mean."""
    r_in = circle_roi(ctx, center_mm, dia_mm * 0.8)
    s_in = stats_for_roi(ctx, r_in)
    r_out_a = circle_roi(ctx, center_mm, dia_mm * 1.5)
    s_out_a = stats_for_roi(ctx, r_out_a)
    # annulus stats from the two discs
    n_ann = s_out_a["n"] - s_in["n"]
    if n_ann <= 0:
        return 0.0
    mean_ann = (s_out_a["mean"] * s_out_a["n"] - s_in["mean"] * s_in["n"]) / n_ann
    return float(mean_ann - s_in["mean"])


def propose(ctx: Ctx) -> dict:
    lc = ctx.pdef.lowcontrast
    center, ang, detected = _refine_block(ctx)
    a = np.deg2rad(ang)
    u = np.array([np.cos(a), np.sin(a)])          # long axis
    v = np.array([-np.sin(a), np.cos(a)])         # short axis
    dia = lc["circle_dia_mm"]
    roi_dia = lc.get("roi_dia_mm", 7.0)

    # grid refinement: median shift from the strongest-responding circles
    shifts = []
    for c in lc["circles"]:
        base = np.asarray(center) + c["u_mm"] * u + c["v_mm"] * v
        best = (0.0, (0.0, 0.0))
        for du in np.linspace(-2.5, 2.5, 11):
            for dv in np.linspace(-2.5, 2.5, 11):
                resp = _circle_response(ctx, base + du * u + dv * v, dia)
                if resp > best[0]:
                    best = (resp, (du, dv))
        shifts.append((best[0], best[1]))
    responses = np.array([s[0] for s in shifts])
    order = np.argsort(responses)[::-1]
    strong = [shifts[i][1] for i in order[:3] if responses[i] > 0]
    if strong and detected:
        du_med = float(np.median([s[0] for s in strong]))
        dv_med = float(np.median([s[1] for s in strong]))
    else:
        du_med = dv_med = 0.0

    circles = []
    for c in lc["circles"]:
        cc = (np.asarray(center) + (c["u_mm"] + du_med) * u
              + (c["v_mm"] + dv_med) * v)
        bg = (np.asarray(center) + (c["u_mm"] + du_med) * u + dv_med * v)
        circles.append({
            "id": c["id"], "level": c["level"],
            "roi": circle_roi(ctx, cc, roi_dia, roi_id=f"lowcontrast/{c['id']}"),
            "bg_roi": circle_roi(ctx, bg, roi_dia,
                                 roi_id=f"lowcontrast/{c['id']}/bg"),
            "full_circle": circle_roi(ctx, cc, dia,
                                      roi_id=f"lowcontrast/{c['id']}/outline"),
        })
    block = rect_roi(ctx, center, (lc["size_mm"][0], lc["size_mm"][1]), ang,
                     roi_id="lowcontrast/block")
    return {"block": block, "angle_deg": ang, "detected": detected,
            "grid_shift_mm": [du_med, dv_med], "circles": circles}


def compute(ctx: Ctx, geometry: dict) -> dict:
    rows = []
    for c in geometry["circles"]:
        so = stats_for_roi(ctx, c["roi"])
        sb = stats_for_roi(ctx, c["bg_roi"])
        denom = float(np.sqrt(so["std"] ** 2 + sb["std"] ** 2))
        cnr = (so["mean"] - sb["mean"]) / denom if denom > 0 else float("nan")
        rows.append({
            "id": c["id"], "level": c["level"],
            "obj_mean": so["mean"], "obj_std": so["std"], "obj_n": so["n"],
            "bg_mean": sb["mean"], "bg_std": sb["std"], "bg_n": sb["n"],
            "cnr": float(cnr), "abs_cnr": float(abs(cnr)),
        })
    rows.sort(key=lambda r: r["level"])
    # sanity: |CNR| should broadly increase with the design-order level
    cnrs = [r["abs_cnr"] for r in rows]
    increasing = sum(1 for i in range(len(cnrs) - 1) if cnrs[i + 1] >= cnrs[i])
    ordering_ok = increasing >= max(len(cnrs) - 3, 1)
    return {"rows": rows, "ordering_ok": bool(ordering_ok),
            "status": "pass" if ordering_ok else "warn"}
