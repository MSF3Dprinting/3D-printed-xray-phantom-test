"""Geometry & alignment: tape-measure marks, corner dimensions, field edges.

Covers wizard Stage D (dimension verification) and the guide's X-ray/light-field
alignment test.

Scale chain: the registration transform T carries the calibrated phantom scale.
The tape pitch re-measured through T yields a correction factor
k = 5.0 mm / measured_pitch; absolute dimensions are reported as T-mm * k so the
final numbers are anchored to the printed 5 mm pitch, not to metadata.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from ..features import (centroid_peak, edge_position, find_peaks_1d,
                        sample_profile)
from .common import Ctx, segment

SIDES = ("top", "right", "bottom", "left")

# side -> (edge center in units of S/2, inward unit vector)
_SIDE_GEOM = {
    "top": ((0, 1), (0, -1)),
    "right": ((1, 0), (-1, 0)),
    "bottom": ((0, -1), (0, 1)),
    "left": ((-1, 0), (1, 0)),
}


def _ruler_probe(ctx: Ctx, side: str, probe_len_mm: float = 42.0):
    """Detect the 6 ruler lines along the side's inward normal.

    Returns dict with line offsets from the phantom edge (T-mm) and probe segment."""
    T = ctx.T
    S2 = ctx.pdef.side_mm / 2.0
    (ex, ey), (nx, ny) = _SIDE_GEOM[side]
    edge_pt = np.array([ex * S2, ey * S2], float)
    inward = np.array([nx, ny], float)
    p0_mm = edge_pt - inward * 2.0          # start slightly outside the edge
    p1_mm = edge_pt + inward * probe_len_mm
    p0 = T.mm_to_px(p0_mm)
    p1 = T.mm_to_px(p1_mm)
    total_mm = probe_len_mm + 2.0
    n = int(total_mm * T.px_per_mm * 2)
    ts, vals = sample_profile(ctx.pixels, p0, p1, n,
                              avg_half_width=3.5 * T.px_per_mm, n_avg=17)
    pos_mm = ts * T.mm_per_px - 2.0          # 0 = nominal phantom edge (from T)
    # local sub-pixel edge: the strongest rising step within [-2, +6] mm makes the
    # ruler offsets independent of corner-fit residuals
    win = pos_mm <= 6.0
    e = edge_position(np.asarray(vals)[win], rising=True, smooth_sigma=2.0)
    edge_mm = float(np.interp(e, np.arange(int(win.sum())), pos_mm[win]))
    if abs(edge_mm) > 2.5:                    # implausible -> keep nominal edge
        edge_mm = 0.0
    pos_mm = pos_mm - edge_mm                 # 0 = locally detected edge
    pk = find_peaks_1d(vals, min_distance=int(3.0 / (pos_mm[1] - pos_mm[0])),
                       threshold_rel=0.25)
    if len(pk) < 6:
        return {"side": side, "detected": False,
                "probe": segment(ctx, p0_mm, p1_mm, f"ruler/{side}/probe"),
                "line_offsets_mm": [], "profile": {
                    "pos_mm": pos_mm.tolist(), "value": np.asarray(vals).tolist()}}
    strongest = sorted(sorted(pk, key=lambda j: -vals[j])[:6])
    offsets = [float(np.interp(centroid_peak(vals, j, 4),
                               np.arange(len(pos_mm)), pos_mm))
               for j in strongest]
    return {"side": side, "detected": True,
            "probe": segment(ctx, p0_mm, p1_mm, f"ruler/{side}/probe"),
            "line_offsets_mm": offsets,
            "profile": {"pos_mm": pos_mm.tolist(),
                        "value": np.asarray(vals).tolist()}}


def _field_edge(ctx: Ctx, side: str, start_mm: float = 8.0):
    """Radiation-field (collimation) edge along the side's outward normal.

    A collimated border is a genuine STEP: direct exposure on one side, blocked
    beam on the other. Scatter gradients and the phantom's own carrier frame are
    not. The detector therefore fits both a two-plateau step model and a single
    linear ramp, and accepts the step only when ALL of the following hold:

      * the probe is long enough to see a border at all (>= 25 mm),
      * >= 8 mm of plateau exists on both sides of the split (a "step" at the
        very end of a short probe is an artifact of the image boundary),
      * the step model explains the profile far better than a ramp
        (SSE_step <= 0.35 * SSE_linear),
      * most of the plateau difference happens locally (>= 70 % within a few mm),
      * the step height exceeds both the local noise and 4 % of the image range.

    Otherwise the result is ``detected: False`` with a ``reason``, and the wizard
    asks the user to place the edge manually. This matters: in acquisitions where
    the phantom nearly fills the detector, the radiation field extends past the
    image and alignment simply cannot be measured — reporting a spurious
    deviation would be worse than reporting nothing.

    Offset is measured from the phantom edge (positive = outside)."""
    T = ctx.T
    S2 = ctx.pdef.side_mm / 2.0
    (ex, ey), (nx, ny) = _SIDE_GEOM[side]
    edge_pt = np.array([ex * S2, ey * S2], float)
    outward = -np.array([nx, ny], float)

    # probe from start_mm outside the edge to just inside the image border
    p0_mm = edge_pt + outward * start_mm
    p0 = np.asarray(T.mm_to_px(p0_mm), float)
    d_px = np.asarray(T.mm_to_px(edge_pt + outward * (start_mm + 1)), float) - p0
    d_px /= np.linalg.norm(d_px)
    h, w = ctx.pixels.shape
    tmax = np.inf
    for lim, comp, dcomp in ((w - 3, p0[0], d_px[0]), (h - 3, p0[1], d_px[1])):
        if dcomp > 1e-9:
            tmax = min(tmax, (lim - comp) / dcomp)
        elif dcomp < -1e-9:
            tmax = min(tmax, (3 - comp) / dcomp)
    probe_mm = float(tmax) * T.mm_per_px if np.isfinite(tmax) else 0.0
    if not np.isfinite(tmax) or probe_mm < 25.0:
        return {"side": side, "detected": False,
                "reason": f"only {probe_mm:.0f} mm of image outside the phantom "
                          f"edge — radiation field extends beyond the detector"}
    p1 = p0 + d_px * tmax
    p1_mm = T.px_to_mm(p1)
    n = max(int(tmax / 2), 40)
    ts, vals = sample_profile(ctx.pixels, p0, p1, n,
                              avg_half_width=10 * T.px_per_mm, n_avg=21)
    v = ndimage.gaussian_filter1d(np.asarray(vals, float), 3.0)
    probe = segment(ctx, p0_mm, p1_mm, f"field/{side}")
    N = len(v)
    mm_per_sample = probe_mm / N

    def reject(reason):
        return {"side": side, "detected": False, "probe": probe,
                "reason": reason, "probe_len_mm": probe_mm}

    # keep >= 8 mm of plateau on each side of a candidate split
    guard = max(int(8.0 / mm_per_sample), 4)
    if N - 2 * guard < 4:
        return reject("probe too short to separate two plateaus")

    # two-plateau (step) model
    best_i, best_sse = None, np.inf
    csum = np.cumsum(v)
    csum2 = np.cumsum(v * v)
    for i in range(guard, N - guard):
        n1, n2 = i, N - i
        s1, s2 = csum[i - 1], csum[-1] - csum[i - 1]
        q1, q2 = csum2[i - 1], csum2[-1] - csum2[i - 1]
        sse = (q1 - s1 * s1 / n1) + (q2 - s2 * s2 / n2)
        if sse < best_sse:
            best_sse, best_i = sse, i
    if best_i is None:
        return reject("no change point found")

    # single linear ramp model (scatter gradient null hypothesis)
    x = np.arange(N, dtype=float)
    A = np.vstack([x, np.ones_like(x)]).T
    coef, *_ = np.linalg.lstsq(A, v, rcond=None)
    sse_lin = float(np.sum((v - A @ coef) ** 2))
    ramp_ratio = best_sse / max(sse_lin, 1e-9)
    if ramp_ratio > 0.35:
        return reject(f"profile is a gradient, not a step "
                      f"(step/ramp residual ratio {ramp_ratio:.2f})")

    m1 = float(np.mean(v[:best_i]))
    m2 = float(np.mean(v[best_i:]))
    step = abs(m2 - m1)
    noise = float(np.std(np.diff(v))) * 3 + 1e-9
    img_range = float(np.percentile(ctx.pixels, 99) - np.percentile(ctx.pixels, 1))
    if step < max(8 * noise, 0.04 * img_range):
        return reject("step height below noise / dynamic-range threshold")

    # locality: most of the plateau difference must occur within a few mm
    k = max(int(3.0 / mm_per_sample), 2)
    lo = float(np.mean(v[max(0, best_i - 2 * k):max(1, best_i - k)]))
    hi = float(np.mean(v[min(N - 1, best_i + k):min(N, best_i + 2 * k)]))
    locality = abs(hi - lo) / max(step, 1e-9)
    if locality < 0.70:
        return reject(f"transition too gradual for a collimation edge "
                      f"(only {100 * locality:.0f} % of the change is local)")

    pos_mm = start_mm + best_i * mm_per_sample
    return {"side": side, "detected": True,
            "offset_from_edge_mm": float(pos_mm),
            "step_height": step, "confidence": float(step / max(noise, 1e-9)),
            "ramp_ratio": float(ramp_ratio), "locality": float(locality),
            "probe_len_mm": probe_mm, "probe": probe,
            "edge_pt_px": (np.asarray(T.mm_to_px(edge_pt + outward * pos_mm))
                           .tolist())}


def propose(ctx: Ctx) -> dict:
    rulers = {side: _ruler_probe(ctx, side) for side in SIDES}
    fields = {side: _field_edge(ctx, side) for side in SIDES}
    corners = {}
    if ctx.reg is not None:
        S2 = ctx.pdef.side_mm / 2.0
        labels = {(-1, 1): "TL", (1, 1): "TR", (1, -1): "BR", (-1, -1): "BL"}
        for c_px in ctx.reg.corners_px:
            mm = ctx.T.px_to_mm(c_px)
            key = (int(np.sign(mm[0])), int(np.sign(mm[1])))
            if key in labels:
                corners[labels[key]] = {"px": np.asarray(c_px).tolist(),
                                        "mm": np.asarray(mm).tolist()}
    return {"rulers": rulers, "field_edges": fields, "corners": corners}


def compute(ctx: Ctx, geometry: dict) -> dict:
    pdef = ctx.pdef
    tol = pdef.tolerances
    nominal_first = pdef.rulers["top"]["first_line_from_edge_mm"]
    nominal_pitch = pdef.rulers["top"]["pitch_mm"]
    central_idx = pdef.rulers["top"]["central_long_index"]

    # ---- per-side ruler analysis -------------------------------------------
    ruler_rows = {}
    pitches = []
    central_from_edge = {}
    for side, r in geometry["rulers"].items():
        if not r.get("detected") or len(r.get("line_offsets_mm", [])) < 6:
            ruler_rows[side] = {"detected": False}
            continue
        off = np.asarray(r["line_offsets_mm"], float)
        idx = np.arange(len(off))
        A = np.vstack([idx, np.ones_like(idx, dtype=float)]).T
        coef, *_ = np.linalg.lstsq(A, off, rcond=None)
        pitch = float(coef[0])
        resid = off - A @ coef
        pitches.append(pitch)
        central_from_edge[side] = float(off[central_idx])
        ruler_rows[side] = {
            "detected": True,
            "line_offsets_mm": off.tolist(),
            "pitch_mm": pitch,
            "first_line_from_edge_mm": float(off[0]),
            "central_line_from_edge_mm": float(off[central_idx]),
            "linearity_residuals_mm": resid.tolist(),
            "linearity_rms_mm": float(np.sqrt(np.mean(resid ** 2))),
            "nominal_pitch_mm": nominal_pitch,
            "pitch_dev_pct": 100.0 * (pitch - nominal_pitch) / nominal_pitch,
        }

    pitch_T = float(np.mean(pitches)) if pitches else float("nan")
    k = nominal_pitch / pitch_T if pitches else 1.0   # absolute-scale correction

    # ---- corner dimensions --------------------------------------------------
    dims = {}
    corners = geometry.get("corners", {})
    if len(corners) == 4:
        P = {kk: np.asarray(v["mm"], float) for kk, v in corners.items()}
        def d(a, b):
            return float(np.linalg.norm(P[a] - P[b]) * k)
        dims = {
            "side_top_mm": d("TL", "TR"), "side_right_mm": d("TR", "BR"),
            "side_bottom_mm": d("BR", "BL"), "side_left_mm": d("BL", "TL"),
            "diag_tlbr_mm": d("TL", "BR"), "diag_trbl_mm": d("TR", "BL"),
        }
        dims["mean_side_mm"] = float(np.mean([dims["side_top_mm"],
                                              dims["side_right_mm"],
                                              dims["side_bottom_mm"],
                                              dims["side_left_mm"]]))
        dims["nominal_side_mm"] = pdef.nominal_side_mm
        dims["calibrated_side_mm"] = pdef.side_mm
        dims["dev_from_nominal_pct"] = 100.0 * (
            dims["mean_side_mm"] - pdef.nominal_side_mm) / pdef.nominal_side_mm

    # ---- central-line separations ------------------------------------------
    # Edge-to-edge span prefers the corner-measured side lengths (T-mm) over the
    # definition value, since ruler offsets are referenced to the local edges.
    seps = {}
    S_vert_T = S_horiz_T = pdef.side_mm
    if dims:
        S_vert_T = (dims["side_left_mm"] + dims["side_right_mm"]) / 2.0 / k
        S_horiz_T = (dims["side_top_mm"] + dims["side_bottom_mm"]) / 2.0 / k
    if all(s in central_from_edge for s in ("top", "bottom")):
        seps["vertical_mm"] = (S_vert_T - central_from_edge["top"]
                               - central_from_edge["bottom"]) * k
    if all(s in central_from_edge for s in ("left", "right")):
        seps["horizontal_mm"] = (S_horiz_T - central_from_edge["left"]
                                 - central_from_edge["right"]) * k
    seps["nominal_mm"] = (pdef.nominal_side_mm
                          - 2 * (nominal_first + central_idx * nominal_pitch))

    # ---- scale cross-check --------------------------------------------------
    meta_spacings = {}
    if ctx.params:
        meta_spacings = ctx.params.get("spacing_candidates", {}) or {}
    scale = {
        "pitch_measured_mm": pitch_T,
        "scale_correction_k": k,
        "transform_mm_per_px": ctx.T.mm_per_px,
        "absolute_mm_per_px": ctx.T.mm_per_px * k,
        "dicom_spacings_mm_per_px": meta_spacings,
    }
    if "ImagerPixelSpacing" in meta_spacings:
        m = meta_spacings["ImagerPixelSpacing"] / (ctx.T.mm_per_px * k)
        scale["implied_magnification_vs_detector_plane"] = float(m)

    # ---- field alignment ----------------------------------------------------
    sid_mm = float((ctx.params or {}).get("sid_mm", 1000.0))
    central_nominal = nominal_first + central_idx * nominal_pitch
    field_rows = {}
    worst_pct = 0.0
    any_field = False
    for side, f in geometry["field_edges"].items():
        if not f.get("detected"):
            field_rows[side] = {"detected": False,
                                "reason": f.get("reason", "not detected")}
            continue
        any_field = True
        off_edge = f["offset_from_edge_mm"] * k
        central = central_from_edge.get(side, central_nominal)
        dev = off_edge + central                 # distance field edge <-> central line
        pct = 100.0 * dev / sid_mm
        field_rows[side] = {
            "detected": True,
            "offset_from_phantom_edge_mm": off_edge,
            "deviation_from_central_line_mm": dev,
            "pct_of_sid": pct,
            "status": "pass" if abs(pct) <= tol.get("field_pct_sid", 2.0)
                      else "fail",
        }
        worst_pct = max(worst_pct, abs(pct))

    dim_tol = tol.get("dim_dev_pct", 1.0)
    dim_status = "n/a"
    if dims:
        dim_status = ("pass" if abs(dims["dev_from_nominal_pct"]) <= dim_tol
                      else "warn")
    field_status = "n/a"
    if any_field:
        field_status = ("pass" if worst_pct <= tol.get("field_pct_sid", 2.0)
                        else "fail")

    return {
        "rulers": ruler_rows,
        "dimensions": dims, "dimension_status": dim_status,
        "central_line_separations": seps,
        "scale": scale,
        "field_alignment": field_rows, "field_status": field_status,
        "sid_mm": sid_mm,
    }
