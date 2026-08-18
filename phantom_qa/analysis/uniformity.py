"""Uniformity test: 5 printed squares (center + 4 corners), SNR consistency.

Guide: SNR = mu/sigma per ROI; relative deviation of each SNR from the average
should be reasonably small (default tolerance 20 %).
"""

from __future__ import annotations

import numpy as np

from ..features import locate_line_center
from .common import Ctx, rect_roi, stats_for_roi


def _refine_square(ctx: Ctx, center_mm, size_mm: float):
    """Locate the 4 outline lines of a printed square and return the refined
    center (mm) plus measured size, or None if detection is too weak."""
    T = ctx.T
    ppm = T.px_per_mm
    half = size_mm / 2.0
    found = {}
    # (key, axis unit vector along which the line position varies, sample offsets axis)
    for key, line_pos, axis in (("left", (-half, 0), (1, 0)),
                                ("right", (half, 0), (1, 0)),
                                ("bottom", (0, -half), (0, 1)),
                                ("top", (0, half), (0, 1))):
        axis = np.asarray(axis, float)
        perp = np.array([-axis[1], axis[0]])
        d_px = T.mm_to_px(axis) - T.mm_to_px([0, 0])
        d_px = d_px / np.linalg.norm(d_px)
        positions = []
        for off in (-size_mm * 0.25, 0.0, size_mm * 0.25):
            p_mm = (np.asarray(center_mm) + np.asarray(line_pos)
                    + off * perp)
            p_px = T.mm_to_px(p_mm)
            pt, strength = locate_line_center(
                ctx.pixels, p_px, d_px, search_half=4.0 * ppm,
                avg_half_width=1.5 * ppm, n_avg=3)
            if pt is not None:
                proj = float(np.dot(np.asarray(T.px_to_mm(pt)) - np.asarray(center_mm),
                                    axis))
                positions.append(proj)
        if len(positions) >= 2:
            found[key] = float(np.median(positions))
    if not all(k in found for k in ("left", "right", "top", "bottom")):
        return None
    cx = center_mm[0] + (found["left"] + found["right"]) / 2.0
    cy = center_mm[1] + (found["bottom"] + found["top"]) / 2.0
    return {
        "center_mm": [float(cx), float(cy)],
        "measured_w_mm": float(found["right"] - found["left"]),
        "measured_h_mm": float(found["top"] - found["bottom"]),
    }


def propose(ctx: Ctx) -> dict:
    u = ctx.pdef.uniformity
    roi_size = u.get("roi_size_mm", 30.0)
    squares = []
    for sq in u["squares"]:
        refined = _refine_square(ctx, sq["center_mm"], u["size_mm"])
        center = refined["center_mm"] if refined else sq["center_mm"]
        entry = {
            "id": sq["id"],
            "nominal_center_mm": sq["center_mm"],
            "detected": refined is not None,
            "outline": refined,
            "roi": rect_roi(ctx, center, (roi_size, roi_size), 0.0,
                            roi_id=f"uniformity/{sq['id']}"),
        }
        squares.append(entry)
    return {"squares": squares, "roi_size_mm": roi_size}


def compute(ctx: Ctx, geometry: dict) -> dict:
    tol = ctx.pdef.tolerances.get("uniformity_dsnr_pct", 20.0)
    rows = []
    for sq in geometry["squares"]:
        s = stats_for_roi(ctx, sq["roi"])
        snr = s["mean"] / s["std"] if s["std"] > 0 else float("nan")
        rows.append({"id": sq["id"], "mean": s["mean"], "std": s["std"],
                     "n": s["n"], "snr": snr})
    snrs = np.array([r["snr"] for r in rows], float)
    means = np.array([r["mean"] for r in rows], float)
    snr_avg = float(np.nanmean(snrs))
    mean_avg = float(np.nanmean(means))
    worst = 0.0
    for r in rows:
        r["dsnr_pct"] = 100.0 * (r["snr"] - snr_avg) / snr_avg
        r["dmean_pct"] = 100.0 * (r["mean"] - mean_avg) / mean_avg
        r["status"] = "pass" if abs(r["dsnr_pct"]) <= tol else "fail"
        worst = max(worst, abs(r["dsnr_pct"]))
    status = "pass" if worst <= tol else "fail"
    bad = [r for r in rows if r["status"] != "pass"]
    if status == "pass":
        reasons = [f"every square is within +/-{tol:g}% of the mean SNR "
                   f"(worst {worst:.1f}%)."]
    else:
        reasons = [f"{r['id']}: SNR {r['snr']:.1f} is {r['dsnr_pct']:+.1f}% from "
                   f"the scan mean of {snr_avg:.1f} (tolerance +/-{tol:g}%)."
                   for r in bad]
        reasons.append("A single deviating corner usually means the ROI is not "
                       "inside its printed square; a consistent gradient across "
                       "several means genuine non-uniformity.")
    return {
        "rows": rows, "snr_avg": snr_avg, "mean_avg": mean_avg,
        "max_abs_dsnr_pct": worst, "tolerance_pct": tol,
        "status": status, "reasons": reasons,
        "affected": [r["id"] for r in bad],
    }
