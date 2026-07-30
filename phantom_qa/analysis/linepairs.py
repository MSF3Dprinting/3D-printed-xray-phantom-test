"""Spatial-resolution line-pair test: SD constancy + line-pattern linearity.

Guide metric: standard deviation inside a consistently placed ROI per group.
Additional first-class output (rev. 2 workplan): intensity profile across each
group, detected line positions vs an ideal uniform grid, pitch deviation.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from ..features import (centroid_peak, find_peaks_1d, sample_profile)
from .common import Ctx, rect_roi, segment, stats_for_roi


def _refine_group_center(ctx: Ctx, center_mm, half_mm: float = 8.0,
                         block_mm: float = 12.6):
    """Refine a group center with a block-sized matched filter on the local
    variance map. A centroid of high-variance pixels is NOT used because the
    junction frames between groups also carry high variance and attract it;
    the box filter scores a full block-sized window instead, which peaks when
    the window is centered on the serpentine block."""
    T = ctx.T
    c_px = np.asarray(T.mm_to_px(center_mm), float)
    pad = int((half_mm + block_mm / 2 + 2) * T.px_per_mm)
    x0 = int(c_px[0]) - pad
    y0 = int(c_px[1]) - pad
    sub = ctx.pixels[max(0, y0):y0 + 2 * pad, max(0, x0):x0 + 2 * pad]
    if sub.size < 400:
        return center_mm, False
    hp = sub - ndimage.gaussian_filter(sub, 4)
    block_px = int(block_mm * T.px_per_mm * 0.85)   # slightly inside the block
    score = ndimage.uniform_filter(hp * hp, block_px)
    # search restricted to +/- half_mm around the nominal center
    h_search = int(half_mm * T.px_per_mm)
    cy0, cx0 = sub.shape[0] // 2, sub.shape[1] // 2
    win = score[cy0 - h_search:cy0 + h_search, cx0 - h_search:cx0 + h_search]
    if win.size == 0:
        return center_mm, False
    iy, ix = np.unravel_index(int(np.argmax(win)), win.shape)
    cx = x0 + cx0 - h_search + ix
    cy = y0 + cy0 - h_search + iy
    mm = ctx.T.px_to_mm([float(cx), float(cy)])
    return [float(mm[0]), float(mm[1])], True


def propose(ctx: Ctx) -> dict:
    lp = ctx.pdef.linepairs
    roi_mm = lp.get("roi_size_mm", 12.6)
    angle = lp.get("strip_angle_deg", 45.0)
    groups = []
    for g in lp["groups"]:
        # +/-3 mm: enough for registration scatter, too small to jump to a
        # neighboring block (blocks are ~30 mm apart)
        center, detected = _refine_group_center(ctx, g["center_mm"], half_mm=3.0)
        u = np.array([np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle))])
        half_len = roi_mm / 2 + 2.0
        p0 = np.asarray(center) - half_len * u
        p1 = np.asarray(center) + half_len * u
        groups.append({
            "id": g["id"], "freq_lp_mm": g["freq_lp_mm"],
            "nominal_center_mm": g["center_mm"], "detected": detected,
            "roi": rect_roi(ctx, center, (roi_mm, roi_mm), angle,
                            roi_id=f"linepairs/{g['id']}"),
            "profile_seg": segment(ctx, p0, p1, f"linepairs/{g['id']}/profile"),
        })
    return {"groups": groups, "strip_angle_deg": angle, "roi_size_mm": roi_mm}


def _measure_pattern_2dfft(ctx: Ctx, center_mm, half_mm: float = 5.0):
    """Dominant spatial frequency and orientation of the line pattern via 2-D FFT
    of an image-space patch. Returns (freq_lp_mm, direction_unit_px) or None.

    The direction is perpendicular to the printed lines (the axis along which the
    pattern oscillates), expressed in image-pixel space."""
    T = ctx.T
    c_px = np.asarray(T.mm_to_px(center_mm), float)
    h = int(half_mm * T.px_per_mm)
    cx, cy = int(round(c_px[0])), int(round(c_px[1]))
    if (cy - h < 0 or cx - h < 0 or cy + h > ctx.pixels.shape[0]
            or cx + h > ctx.pixels.shape[1]):
        return None
    patch = ctx.pixels[cy - h:cy + h, cx - h:cx + h].astype(float)
    patch = patch - patch.mean()
    win = np.outer(np.hanning(patch.shape[0]), np.hanning(patch.shape[1]))
    F = np.abs(np.fft.fftshift(np.fft.fft2(patch * win)))
    ny, nx = F.shape
    fy = np.fft.fftshift(np.fft.fftfreq(ny))          # cycles / px
    fx = np.fft.fftshift(np.fft.fftfreq(nx))
    FX, FY = np.meshgrid(fx, fy)
    fmag_mm = np.hypot(FX, FY) / T.mm_per_px          # cycles / mm
    band = (fmag_mm > 0.85) & (fmag_mm < 2.6)
    if not band.any():
        return None
    Fb = np.where(band, F, 0)
    iy, ix = np.unravel_index(int(np.argmax(Fb)), F.shape)
    # significance: peak must clearly exceed the band median
    band_vals = F[band]
    if F[iy, ix] < np.median(band_vals) * 8:
        return None
    fvec = np.array([FX[iy, ix], FY[iy, ix]])
    freq = float(np.hypot(*fvec) / T.mm_per_px)
    direction = fvec / np.linalg.norm(fvec)
    return freq, direction


def _analyze_profile(ctx: Ctx, seg: dict, freq_lp_mm: float,
                     center_mm=None):
    """Extract the line-pattern profile and fit a uniform grid to line peaks.

    The profile direction is taken from the 2-D FFT measurement when available
    (robust to strip-angle errors); the nominal segment is the fallback."""
    T = ctx.T
    p0 = np.asarray(seg["p0_px"], float)
    p1 = np.asarray(seg["p1_px"], float)
    measured = None
    if center_mm is not None:
        measured = _measure_pattern_2dfft(ctx, center_mm)
    if measured is not None:
        freq_meas, direction = measured
        half_len = float(np.linalg.norm(p1 - p0)) / 2.0
        c_px = (p0 + p1) / 2.0
        # keep the profile pointing the same general way as the nominal segment
        if np.dot(direction, p1 - p0) < 0:
            direction = -direction
        p0 = c_px - direction * half_len
        p1 = c_px + direction * half_len
    length_px = float(np.linalg.norm(p1 - p0))
    n = max(int(length_px * 2), 64)          # ~0.5 px sampling
    ts, vals = sample_profile(ctx.pixels, p0, p1, n,
                              avg_half_width=4.0 * T.px_per_mm, n_avg=15)
    pos_mm = ts * T.mm_per_px
    nominal_pitch = 1.0 / freq_lp_mm

    # detrend (background slope) then find bright line peaks
    detr = vals - ndimage.gaussian_filter1d(vals, 3.0 * nominal_pitch /
                                            (pos_mm[1] - pos_mm[0]))
    min_dist = max(int(0.6 * nominal_pitch / (pos_mm[1] - pos_mm[0])), 2)
    pk = find_peaks_1d(detr, min_distance=min_dist, threshold_rel=0.45)
    peaks_mm = np.array([np.interp(centroid_peak(detr, i, max(min_dist // 2, 1)),
                                   np.arange(len(pos_mm)), pos_mm) for i in pk])

    result = {
        "profile": {"pos_mm": pos_mm.tolist(), "value": vals.tolist(),
                    "detrended": detr.tolist()},
        "n_lines": int(len(peaks_mm)),
        "peaks_mm": peaks_mm.tolist(),
        "nominal_pitch_mm": nominal_pitch,
        "fft_freq_lp_mm": measured[0] if measured else None,
    }
    if len(peaks_mm) >= 4:
        # keep the longest run of peaks with plausible spacing (drop stray ends)
        gaps = np.diff(peaks_mm)
        ok = (gaps > 0.55 * nominal_pitch) & (gaps < 1.45 * nominal_pitch)
        best_s, best_e = 0, 0
        s = 0
        for i in range(len(ok)):
            if not ok[i]:
                s = i + 1
            elif i + 1 - s > best_e - best_s:
                best_s, best_e = s, i + 1
        run = peaks_mm[best_s:best_e + 1]
        if len(run) >= 4:
            idx = np.arange(len(run))
            A = np.vstack([idx, np.ones_like(idx)]).T
            coef, *_ = np.linalg.lstsq(A, run, rcond=None)
            pitch = float(coef[0])
            resid = (run - (A @ coef))
            result.update({
                "used_lines": int(len(run)),
                "grid_peaks_mm": run.tolist(),
                "measured_pitch_mm": pitch,
                "measured_freq_lp_mm": 1.0 / pitch if pitch > 0 else None,
                "pitch_dev_pct": 100.0 * (pitch - nominal_pitch) / nominal_pitch,
                "grid_residuals_mm": resid.tolist(),
                "residual_rms_mm": float(np.sqrt(np.mean(resid ** 2))),
            })
    return result


def compute(ctx: Ctx, geometry: dict) -> dict:
    tol_pitch = ctx.pdef.tolerances.get("linepair_pitch_dev_pct", 10.0)
    rows = []
    for g in geometry["groups"]:
        s = stats_for_roi(ctx, g["roi"])
        prof = _analyze_profile(ctx, g["profile_seg"], g["freq_lp_mm"],
                                center_mm=g["roi"]["center_mm"])
        pitch_dev = prof.get("pitch_dev_pct")
        status = "pass"
        if pitch_dev is None:
            status = "warn"
        elif abs(pitch_dev) > tol_pitch:
            status = "fail"
        rows.append({
            "id": g["id"], "freq_lp_mm": g["freq_lp_mm"],
            "mean": s["mean"], "std": s["std"], "n": s["n"],
            "linearity": prof, "status": status,
        })
    overall = "pass"
    if any(r["status"] == "fail" for r in rows):
        overall = "fail"
    elif any(r["status"] == "warn" for r in rows):
        overall = "warn"
    return {"rows": rows, "status": overall,
            "pitch_tolerance_pct": tol_pitch}
