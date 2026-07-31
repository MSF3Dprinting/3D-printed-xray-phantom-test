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


_RECT_STEP_MM = 0.25          # sampling of the local rectified corridor
_PERIOD_WIN_MM = 10.0         # analysis window for the periodicity map
_PERIOD_STRIDE_MM = 2.0
_FREQ_LO, _FREQ_HI = 1.0, 2.4  # cycles/mm band that contains all line groups


def _rectify_region(ctx: Ctx, x_range, y_range, step_mm=_RECT_STEP_MM):
    """Resample an axis-aligned region of the phantom frame onto a mm grid.
    Returns (image, x0_mm, y0_mm, step) with row 0 = y_range[1] (top)."""
    xs = np.arange(x_range[0], x_range[1], step_mm)
    ys = np.arange(y_range[1], y_range[0], -step_mm)
    XX, YY = np.meshgrid(xs, ys)
    pts = np.stack([XX.ravel(), YY.ravel()], axis=1)
    px = pts @ ctx.T.A.T + ctx.T.t
    img = ndimage.map_coordinates(
        ctx.pixels, [px[:, 1].reshape(XX.shape), px[:, 0].reshape(XX.shape)],
        order=1, mode="constant", cval=0.0)
    return img, xs[0], ys[0], step_mm


def _periodicity_map(img, step_mm, win_mm=_PERIOD_WIN_MM,
                     stride_mm=_PERIOD_STRIDE_MM):
    """Band-limited periodicity: peak/median energy ratio in the line-pair band.

    Local VARIANCE cannot be used to find the line groups — the dark wedge steps
    carry far more quantum noise than the tungsten patterns carry signal, so a
    variance detector locks onto the wedge. Noise is broadband, a printed grating
    is a sharp spectral peak, so the peak-to-median ratio separates them cleanly.

    Returns (ratio_map, freq_map, dir_map, win_px, stride_px)."""
    w = int(round(win_mm / step_mm))
    st = max(int(round(stride_mm / step_mm)), 1)
    f = np.fft.fftshift(np.fft.fftfreq(w, d=step_mm))
    FX, FY = np.meshgrid(f, f)
    fmag = np.hypot(FX, FY)
    band = (fmag > _FREQ_LO) & (fmag < _FREQ_HI)
    han = np.outer(np.hanning(w), np.hanning(w))
    rows = range(0, img.shape[0] - w, st)
    cols = range(0, img.shape[1] - w, st)
    nr, nc = len(list(rows)), len(list(cols))
    ratio = np.zeros((nr, nc))
    freq = np.zeros((nr, nc))
    ang = np.zeros((nr, nc))
    for i, r in enumerate(range(0, img.shape[0] - w, st)):
        for j, c in enumerate(range(0, img.shape[1] - w, st)):
            patch = img[r:r + w, c:c + w]
            if patch.min() <= 0:          # outside the detector / phantom
                continue
            F = np.abs(np.fft.fftshift(np.fft.fft2((patch - patch.mean()) * han)))
            bv = F[band]
            if bv.size == 0:
                continue
            pk = float(bv.max())
            med = float(np.median(bv)) + 1e-9
            ratio[i, j] = pk / med
            iy, ix = np.unravel_index(int(np.argmax(np.where(band, F, 0))), F.shape)
            freq[i, j] = fmag[iy, ix]
            # Row index runs along DECREASING phantom y, so the row-axis
            # frequency is the negative of the phantom-y frequency. Without the
            # sign flip the angle comes out mirrored about the x axis.
            ang[i, j] = np.degrees(np.arctan2(-FY[iy, ix], FX[iy, ix])) % 180.0
    return ratio, freq, ang, w, st


def _refine_center_fine(img, x0, y0, step, center_mm, freq_lp_mm, angle_deg,
                        block_mm, search_mm=5.0):
    """Sub-window refinement of a block centre.

    The periodicity map is computed on a coarse stride with a window comparable
    to the block itself, so its centroid is only good to ~1 mm. Here the
    rectified region is band-passed around the block's own measured frequency
    and the resulting pattern energy is box-filtered at the block size; the peak
    of that is the block centre to a fraction of a millimetre."""
    pitch = 1.0 / max(freq_lp_mm, 1e-6)
    s1 = max(pitch / 4.0 / step, 0.6)
    s2 = max(pitch / 1.2 / step, s1 + 0.6)
    bp = ndimage.gaussian_filter(img, s1) - ndimage.gaussian_filter(img, s2)
    energy = ndimage.gaussian_filter(bp * bp, max(pitch / step, 1.0))

    # Isotropic, iterated centre of mass. A box/uniform filter would bias the
    # result for a block rotated ~45 deg to the sampling grid; a disc does not.
    rad = block_mm / 2.0
    cur = [float(center_mm[0]), float(center_mm[1])]
    h, w = energy.shape
    for _ in range(4):
        cj = (cur[0] - x0) / step
        ci = (y0 - cur[1]) / step
        r = int(round(rad / step))
        i0, i1 = int(max(ci - r, 0)), int(min(ci + r + 1, h))
        j0, j1 = int(max(cj - r, 0)), int(min(cj + r + 1, w))
        if i1 - i0 < 3 or j1 - j0 < 3:
            return cur
        sub = energy[i0:i1, j0:j1]
        ii, jj = np.mgrid[i0:i1, j0:j1]
        disc = ((ii - ci) ** 2 + (jj - cj) ** 2) <= (rad / step) ** 2
        wv = np.where(disc, sub, 0.0)
        tot = wv.sum()
        if tot <= 0:
            return cur
        ci_f = float((ii * wv).sum() / tot)
        cj_f = float((jj * wv).sum() / tot)
        new = [float(x0 + cj_f * step), float(y0 - ci_f * step)]
        moved = np.hypot(new[0] - cur[0], new[1] - cur[1])
        # never wander outside the coarse search radius
        if np.hypot(new[0] - center_mm[0], new[1] - center_mm[1]) > search_mm:
            return cur
        cur = new
        if moved < 0.05:
            break
    return cur


def _axis_through(blocks):
    """Principal axis (deg, mod 180) through a set of block centres."""
    if len(blocks) < 2:
        return None
    P = np.array([b["center_mm"] for b in blocks], float)
    C = P - P.mean(axis=0)
    if not np.isfinite(C).all() or np.allclose(C, 0):
        return None
    _, _, vt = np.linalg.svd(C, full_matrices=False)
    v = vt[0]
    return float(np.degrees(np.arctan2(v[1], v[0])) % 180.0)


def detect_line_blocks(ctx: Ctx, ratio_threshold: float = 12.0):
    """Locate the line-pair blocks by measurement rather than by stored position.

    Returns a list of dicts (measured_center_mm, freq_lp_mm, mod_dir_deg,
    n_windows) ordered along the strip, plus the fitted strip angle."""
    lp = ctx.pdef.linepairs
    nominal = np.array([g["center_mm"] for g in lp["groups"]], float)
    pad = lp.get("search_pad_mm", 28.0)
    x_range = (nominal[:, 0].min() - pad, nominal[:, 0].max() + pad)
    y_range = (nominal[:, 1].min() - pad, nominal[:, 1].max() + pad)
    img, x0, y0, step = _rectify_region(ctx, x_range, y_range)
    ratio, freq, ang, w, st = _periodicity_map(img, step)

    mask = ratio > ratio_threshold
    mask = ndimage.binary_opening(mask, np.ones((2, 2)))
    lab, nl = ndimage.label(mask)
    blocks = []
    for k in range(1, nl + 1):
        sel = lab == k
        if sel.sum() < 4:
            continue
        wgt = ratio * sel
        ii, jj = np.nonzero(sel)
        # periodicity-weighted centroid of the window centres
        wv = wgt[ii, jj]
        ci = float(np.sum(ii * wv) / np.sum(wv))
        cj = float(np.sum(jj * wv) / np.sum(wv))
        cx = x0 + (cj * st + w / 2.0) * step
        cy = y0 - (ci * st + w / 2.0) * step
        f_med = float(np.median(freq[ii, jj]))
        # circular median of the modulation direction (mod 180)
        a2 = np.deg2rad(ang[ii, jj] * 2.0)
        a_med = float((np.degrees(np.arctan2(np.sin(a2).mean(),
                                             np.cos(a2).mean())) / 2.0) % 180.0)
        blocks.append({"center_mm": [cx, cy], "freq_lp_mm": f_med,
                       "mod_dir_deg": a_med, "n_windows": int(sel.sum())})

    if len(blocks) < 2:
        return blocks, None

    # --- reject impostors -------------------------------------------------
    # Uniformity-square outlines, ruler marks and the wedge frame also produce
    # spectral peaks in the band, but they are axis-aligned one-offs. The real
    # groups all live on ONE strip, so they share a modulation direction and are
    # collinear. Both facts are used to filter.
    def circ_median(angles, weights):
        a2 = np.deg2rad(np.asarray(angles, float) * 2.0)
        w = np.asarray(weights, float)
        return (np.degrees(np.arctan2((np.sin(a2) * w).sum(),
                                      (np.cos(a2) * w).sum())) / 2.0) % 180.0

    def ang_diff(a, b):
        d = abs((a - b) % 180.0)
        return min(d, 180.0 - d)

    weights = np.array([b["n_windows"] for b in blocks], float)
    mod_med = circ_median([b["mod_dir_deg"] for b in blocks], weights)
    kept = [b for b in blocks
            if ang_diff(b["mod_dir_deg"], mod_med) <= 12.0]
    if len(kept) >= 2:
        mod_med = circ_median([b["mod_dir_deg"] for b in kept],
                              [b["n_windows"] for b in kept])
        kept = [b for b in kept if ang_diff(b["mod_dir_deg"], mod_med) <= 12.0]
    if len(kept) < 2:
        kept = blocks

    # Strip axis comes from the geometry of the block centres, which are
    # collinear by construction. Deriving it from the modulation direction
    # instead would require knowing whether the printed lines run along or
    # across the strip — an assumption that is wrong for half the phantoms.
    strip_angle = _axis_through(kept)
    if strip_angle is None:
        strip_angle = (mod_med - 90.0) % 180.0
    u = np.array([np.cos(np.deg2rad(strip_angle)), np.sin(np.deg2rad(strip_angle))])
    perp = np.array([-u[1], u[0]])

    # collinearity: drop blobs far from the strip centre line
    if len(kept) >= 3:
        P = np.array([b["center_mm"] for b in kept], float)
        offs = P @ perp
        med = float(np.median(offs))
        mad = float(np.median(np.abs(offs - med))) + 1e-6
        kept = [b for b, o in zip(kept, offs)
                if abs(o - med) <= max(4.0 * mad, 6.0)]
        refit = _axis_through(kept)
        if refit is not None:
            strip_angle = refit
            u = np.array([np.cos(np.deg2rad(strip_angle)),
                          np.sin(np.deg2rad(strip_angle))])

    n_expected = len(ctx.pdef.linepairs["groups"])
    if len(kept) > n_expected:                      # keep the strongest
        kept = sorted(kept, key=lambda b: -b["n_windows"])[:n_expected]

    block_mm = ctx.pdef.linepairs.get("roi_size_mm", 12.6)
    for b in kept:
        b["coarse_center_mm"] = list(b["center_mm"])
        b["center_mm"] = _refine_center_fine(
            img, x0, y0, step, b["center_mm"], b["freq_lp_mm"],
            strip_angle, block_mm)
        b["t"] = float(np.dot(b["center_mm"], u))
    kept.sort(key=lambda b: b["t"])
    return kept, strip_angle


def _match_blocks_to_groups(ctx: Ctx, blocks, strip_angle):
    """Assign detected blocks to definition groups by measured frequency,
    keeping the along-strip ordering consistent."""
    groups = ctx.pdef.linepairs["groups"]
    if not blocks:
        return {}
    nom_f = np.array([g["freq_lp_mm"] for g in groups], float)
    # order groups along the strip using their nominal positions
    u = np.array([np.cos(np.deg2rad(strip_angle)), np.sin(np.deg2rad(strip_angle))])
    order = np.argsort([np.dot(g["center_mm"], u) for g in groups])
    assigned = {}
    if len(blocks) == len(groups):
        for slot, b in zip(order, blocks):          # both sorted along strip
            assigned[groups[slot]["id"]] = b
        # ordering sanity: measured frequency must track the nominal ordering
        meas = [assigned[groups[s]["id"]]["freq_lp_mm"] for s in order]
        nom = [nom_f[s] for s in order]
        if np.corrcoef(meas, nom)[0, 1] < 0.5:
            assigned = {}
    if not assigned:                                 # fall back to frequency match
        used = set()
        for gi, g in enumerate(groups):
            best, bd = None, np.inf
            for k, b in enumerate(blocks):
                if k in used:
                    continue
                d = abs(b["freq_lp_mm"] - g["freq_lp_mm"])
                if d < bd:
                    bd, best = d, k
            if best is not None and bd < 0.35:
                assigned[g["id"]] = blocks[best]
                used.add(best)
    # A block whose measured frequency contradicts its assigned identity is a
    # mis-assignment, not a measurement: drop it rather than report it.
    for g in groups:
        b = assigned.get(g["id"])
        if b is not None and abs(b["freq_lp_mm"] - g["freq_lp_mm"]) > 0.35:
            del assigned[g["id"]]
    return assigned


def propose(ctx: Ctx) -> dict:
    """Measure the line-pair blocks in this scan and build ROIs on what was
    found. The definition supplies nominal positions/frequencies for identifying
    the blocks and as a fallback — it does not fix where the ROIs land."""
    lp = ctx.pdef.linepairs
    roi_mm = lp.get("roi_size_mm", 12.6)
    nominal_angle = lp.get("strip_angle_deg", 45.0)
    try:
        blocks, strip_angle = detect_line_blocks(ctx)
        assigned = _match_blocks_to_groups(ctx, blocks, strip_angle or nominal_angle)
    except Exception:
        blocks, strip_angle, assigned = [], None, {}
    if strip_angle is None:
        strip_angle = nominal_angle

    groups = []
    for g in lp["groups"]:
        b = assigned.get(g["id"])
        detected = b is not None
        center = b["center_mm"] if detected else list(g["center_mm"])
        # ROI is square, so aligning it to the strip axis and to the modulation
        # direction are equivalent; use the per-block measurement when we have it
        angle = b["mod_dir_deg"] if detected else strip_angle
        u = np.array([np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle))])
        half_len = roi_mm / 2 + 2.0
        p0 = np.asarray(center, float) - half_len * u
        p1 = np.asarray(center, float) + half_len * u
        entry = {
            "id": g["id"], "freq_lp_mm": g["freq_lp_mm"],
            "nominal_center_mm": list(g["center_mm"]), "detected": detected,
            "roi": rect_roi(ctx, center, (roi_mm, roi_mm), angle,
                            roi_id=f"linepairs/{g['id']}"),
            "profile_seg": segment(ctx, p0, p1, f"linepairs/{g['id']}/profile"),
        }
        if detected:
            entry["measured"] = {
                "freq_lp_mm": b["freq_lp_mm"], "mod_dir_deg": b["mod_dir_deg"],
                "offset_from_nominal_mm": float(np.hypot(
                    center[0] - g["center_mm"][0], center[1] - g["center_mm"][1])),
            }
        groups.append(entry)
    return {"groups": groups, "strip_angle_deg": float(strip_angle),
            "roi_size_mm": roi_mm, "n_blocks_detected": len(blocks)}


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
        gaps = np.diff(peaks_mm)
        # Coarse plausibility against the nominal, then a robust second pass
        # against the MEASURED median gap. The block edges throw off short
        # spurious gaps that sit inside a nominal-only window and would drag the
        # fitted pitch down by several percent.
        coarse = gaps[(gaps > 0.55 * nominal_pitch) & (gaps < 1.45 * nominal_pitch)]
        med_gap = float(np.median(coarse)) if coarse.size else nominal_pitch
        ok = np.abs(gaps - med_gap) <= 0.18 * med_gap

        best_s, best_e = 0, 0
        s = 0
        for i in range(len(ok)):
            if not ok[i]:
                s = i + 1
            elif i + 1 - s > best_e - best_s:
                best_s, best_e = s, i + 1
        run = peaks_mm[best_s:best_e + 1]

        if len(run) >= 4:
            def fit(vals):
                idx = np.arange(len(vals))
                A = np.vstack([idx, np.ones_like(idx)]).T
                coef, *_ = np.linalg.lstsq(A, vals, rcond=None)
                return coef, vals - (A @ coef)

            coef, resid = fit(run)
            # one robust pass: drop any line more than 4 MAD off the grid
            mad = float(np.median(np.abs(resid - np.median(resid)))) + 1e-9
            keep = np.abs(resid - np.median(resid)) <= max(4.0 * mad,
                                                           0.08 * med_gap)
            if keep.sum() >= 4 and keep.sum() < len(run):
                # renumber the kept lines by their grid index so gaps stay right
                idx_all = np.round((run - run[0]) / coef[0]).astype(int)
                kept_idx = idx_all[keep]
                A = np.vstack([kept_idx, np.ones_like(kept_idx)]).T
                coef, *_ = np.linalg.lstsq(A, run[keep], rcond=None)
                run = run[keep]
                resid = run - (A @ coef)

            pitch = float(coef[0])
            result.update({
                "used_lines": int(len(run)),
                "grid_peaks_mm": run.tolist(),
                "measured_pitch_mm": pitch,
                "measured_freq_lp_mm": 1.0 / pitch if pitch > 0 else None,
                "pitch_dev_pct": 100.0 * (pitch - nominal_pitch) / nominal_pitch,
                "grid_residuals_mm": resid.tolist(),
                "residual_rms_mm": float(np.sqrt(np.mean(resid ** 2))),
                "median_gap_mm": med_gap,
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
