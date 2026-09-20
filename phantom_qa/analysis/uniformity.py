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
    """SNR uniformity across the five squares.

    A square with no variation at all (sigma = 0) has no SNR: the ratio is not
    large, it is undefined. That happens when the exposure is pinned at the
    detector's limit, and it used to be carried along as a NaN — which made the
    worst deviation ``max(0.0, nan)`` = 0.0 and reported the test as **passed**
    on an image holding no signal whatsoever, while every one of its own rows
    said "fail".

    So an unmeasurable square is not a row with an empty number in it. It is
    kept out of ``rows`` entirely and named in ``not_measured``, which leaves
    one rule for everything downstream: a row always carries real numbers, and
    a test that could not measure says so in its status.
    """
    tol = ctx.pdef.tolerances.get("uniformity_dsnr_pct", 20.0)
    rows, not_measured = [], []
    for sq in geometry["squares"]:
        s = stats_for_roi(ctx, sq["roi"])
        if not np.isfinite(s["mean"]) or not np.isfinite(s["std"]) \
                or s["std"] <= 0:
            not_measured.append({
                "id": sq["id"], "mean": s["mean"], "std": s["std"], "n": s["n"],
                "reason": "every pixel in this square has the same value, so "
                          "signal-to-noise cannot be formed. The usual cause "
                          "is an exposure pinned at the detector's limit.",
            })
            continue
        rows.append({"id": sq["id"], "mean": s["mean"], "std": s["std"],
                     "n": s["n"], "snr": s["mean"] / s["std"]})

    missing = [e["id"] for e in not_measured]
    if not rows:
        return {
            "rows": [], "not_measured": not_measured,
            "snr_avg": None, "mean_avg": None, "max_abs_dsnr_pct": None,
            "tolerance_pct": tol, "status": "n/a",
            "reasons": [
                f"not one of the {len(not_measured)} squares carries any "
                f"variation, so uniformity could not be measured at all. "
                f"This is what a saturated or blank exposure looks like — "
                f"repeat the exposure rather than reading anything into it.",
            ],
            "affected": missing,
        }

    snr_avg = float(np.mean([r["snr"] for r in rows]))
    mean_avg = float(np.mean([r["mean"] for r in rows]))
    worst = 0.0
    for r in rows:
        r["dsnr_pct"] = 100.0 * (r["snr"] - snr_avg) / snr_avg
        r["dmean_pct"] = 100.0 * (r["mean"] - mean_avg) / mean_avg
        r["status"] = "pass" if abs(r["dsnr_pct"]) <= tol else "fail"
        worst = max(worst, abs(r["dsnr_pct"]))
    bad = [r for r in rows if r["status"] != "pass"]

    reasons = []
    if bad:
        reasons += [f"{r['id']}: SNR {r['snr']:.1f} is {r['dsnr_pct']:+.1f}% from "
                    f"the scan mean of {snr_avg:.1f} (tolerance +/-{tol:g}%)."
                    for r in bad]
        reasons.append("A single deviating corner usually means the ROI is not "
                       "inside its printed square; a consistent gradient across "
                       "several means genuine non-uniformity.")
    else:
        reasons.append(f"every square that could be measured is within "
                       f"+/-{tol:g}% of the mean SNR (worst {worst:.1f}%).")
    if not_measured:
        reasons.append(
            f"{', '.join(missing)} carried no variation and were left out of "
            f"the comparison, so this result covers only {len(rows)} of "
            f"{len(rows) + len(not_measured)} squares.")

    # An incomplete answer is not a pass: with a square missing, the comparison
    # is against a mean that square never contributed to.
    status = "fail" if bad else ("n/a" if not_measured else "pass")
    return {
        "rows": rows, "not_measured": not_measured,
        "snr_avg": snr_avg, "mean_avg": mean_avg,
        "max_abs_dsnr_pct": worst, "tolerance_pct": tol,
        "status": status, "reasons": reasons,
        "affected": [r["id"] for r in bad] + missing,
    }
