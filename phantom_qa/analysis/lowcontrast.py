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

from .common import Ctx, annulus_roi, circle_roi, rect_roi, stats_for_roi


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


def _measure_discs(ctx: Ctx, circles) -> tuple[list, list]:
    """CNR of each disc against its ring, split into measured and not.

    Shared by compute() and by the quick orientation reading in propose() and
    the marking step, so all three see exactly the same numbers."""
    rows, not_measured = [], []
    for c in circles:
        so = stats_for_roi(ctx, c["roi"])
        sb = stats_for_roi(ctx, c["bg_roi"])
        denom = float(np.sqrt(so["std"] ** 2 + sb["std"] ** 2))
        entry = {
            "id": c["id"], "level": c["level"],
            "obj_mean": so["mean"], "obj_std": so["std"], "obj_n": so["n"],
            "bg_mean": sb["mean"], "bg_std": sb["std"], "bg_n": sb["n"],
        }
        if not np.isfinite(denom) or denom <= 0 \
                or not np.isfinite(so["mean"]) or not np.isfinite(sb["mean"]):
            entry["reason"] = ("the disc and the block around it hold a single "
                               "value between them, so there is no noise to "
                               "form a contrast-to-noise ratio against.")
            not_measured.append(entry)
            continue
        cnr = (so["mean"] - sb["mean"]) / denom
        entry["cnr"] = float(cnr)
        entry["abs_cnr"] = float(abs(cnr))
        rows.append(entry)
    return rows, not_measured


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

    bg_in = lc.get("bg_inner_dia_mm", 12.0)
    bg_out = lc.get("bg_outer_dia_mm", 16.0)
    circles = []
    for c in lc["circles"]:
        cc = (np.asarray(center) + (c["u_mm"] + du_med) * u
              + (c["v_mm"] + dv_med) * v)
        circles.append({
            "id": c["id"], "level": c["level"],
            "roi": circle_roi(ctx, cc, roi_dia, roi_id=f"lowcontrast/{c['id']}"),
            # Local background is the ring around this circle. An ROI placed
            # elsewhere on the block (e.g. on the midline) both picks up the
            # block's own brightness gradient and shows up as an unexplained
            # marker in the middle of the object.
            "bg_roi": annulus_roi(ctx, cc, bg_in, bg_out,
                                  roi_id=f"lowcontrast/{c['id']}/bg"),
            "full_circle": circle_roi(ctx, cc, dia,
                                      roi_id=f"lowcontrast/{c['id']}/outline"),
        })
    block = rect_roi(ctx, center, (lc["size_mm"][0], lc["size_mm"][1]), ang,
                     roi_id="lowcontrast/block")
    geometry = {"block": block, "angle_deg": ang, "detected": detected,
                "grid_shift_mm": [du_med, dv_med], "circles": circles,
                "bg_ring_mm": [bg_in, bg_out]}
    # Read here as well as at compute time, so the marking step can say which
    # way round the insert looks BEFORE anything is measured — that is where
    # the operator can still correct it. Sixteen ROI statistics; nothing next
    # to the grid search above.
    geometry["orientation"] = orientation_state(
        read_orientation(ctx, geometry))
    return geometry


#: The eight discs sit in two rows of four, snaking: L1..L4 along the top row
#: from +u to -u, L5..L8 back along the bottom row. The pattern is symmetric
#: under a half turn about its own centre, which maps every position onto
#: another position in the same set — so an insert fitted the other way round
#: puts the discs in exactly the same places and only exchanges which disc is
#: which: the one designed as L(i+4) is sitting where L(i) is expected.
#:
#: That symmetry is why this is invisible in the picture and why the ROIs land
#: correctly either way. What it breaks is the meaning of the numbers.
FLIP_SHIFT = 4

#: How much better the rank correlation has to be under one labelling than the
#: other before the answer is trusted. Measured over the 33 reference scans:
#: every confident scan separates by at least 1.14, and the single scan that
#: does not (one exposure so weak that no disc reaches |CNR| 0.6) separates by
#: 0.19. There is no third case anywhere in between, so this sits in the gap.
ORIENTATION_MARGIN = 0.6

#: Rank correlation between design order and measured contrast, below which
#: the grid is reported as not sitting on the printed objects.
#:
#: The check it replaces counted how many neighbouring pairs rose and wanted
#: five of seven. That is the wrong instrument: two adjacent discs differ by
#: less than the noise between exposures, so their order flips at random and
#: the count drops for a reason that says nothing about placement. Over the
#: reference scans it warned about three that correlate at 0.88, 0.88 and 0.93
#: — grids demonstrably on the discs — which is precisely the false warning
#: the field reported.
#:
#: Measured over the same scans, the correlation separates cleanly: the worst
#: correctly-placed scan sits at 0.857, and the one scan whose grid really has
#: nothing to hold on to sits at -0.262. Nothing lies between, so the
#: threshold sits in a gap more than a unit wide rather than against the data.
ORDER_MIN_RHO = 0.6


def _rank(values):
    """Ranks, averaging ties — the ranking a Spearman correlation needs."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def _rank_correlation(levels, values) -> float:
    """Spearman's rho between the design order and what was measured.

    Rank-based on purpose: the discs' absolute contrast varies by a factor of
    five between exposures, but their ORDER is a property of the phantom, so
    the statistic has to be blind to the scale.
    """
    n = len(values)
    if n < 3:
        return 0.0
    a, b = _rank(list(levels)), _rank(list(values))
    ma, mb = sum(a) / n, sum(b) / n
    da = [x - ma for x in a]
    db = [x - mb for x in b]
    denom = float(np.sqrt(sum(x * x for x in da) * sum(x * x for x in db)))
    if denom <= 0:                      # every value identical: no order at all
        return 0.0
    return float(sum(x * y for x, y in zip(da, db)) / denom)


def _resolve_orientation(entries, rings_turned: bool = False) -> dict:
    """Which way round the insert is fitted, read from the contrast order.

    Both prints in use are correct phantoms; they differ in how the insert was
    fitted. With the wrong assumption the discs are still measured correctly —
    they are simply attributed to the wrong design levels, so contrast appears
    not to rise with the design order and the test warns about a grid that is
    in fact exactly where it should be. Nine of the thirty-three reference
    scans warned for this reason and none of them for a real one.

    Decided from the measurement rather than from the phantom label, because
    the label is typed by hand and a mistyped one would then silently reverse
    the reading of every disc.

    The contrast order is read under the rings as they are placed, so what it
    measures is the insert and the rings taken together. ``rings_turned`` (see
    :func:`rings_turned`) separates the two, so that ``flipped`` always
    describes the insert itself — the property of the phantom that can be
    saved for it — whichever way somebody has turned the rings by hand.
    """
    usable = [e for e in entries if "abs_cnr" in e]
    # With nothing to go on, the rings' own labels are taken at face value, as
    # they always were: if they were turned by hand, that was somebody saying
    # the insert is turned.
    assumed = ("assumed to match the rings as they were turned by hand."
               if rings_turned else "assumed as designed.")
    if len(usable) < 6:
        return {"flipped": bool(rings_turned), "confidence": 0.0,
                "source": "undetermined",
                "note": (f"only {len(usable)} of 8 discs could be measured, too "
                         f"few to tell which way round the insert is fitted; "
                         f"{assumed}")}
    values = [e["abs_cnr"] for e in usable]
    as_is = [e["level"] for e in usable]
    flipped = [(e["level"] - 1 + FLIP_SHIFT) % 8 + 1 for e in usable]

    rho_as_is = _rank_correlation(as_is, values)
    rho_flipped = _rank_correlation(flipped, values)
    margin = abs(rho_as_is - rho_flipped)
    is_flipped = (rho_flipped > rho_as_is) != bool(rings_turned)

    if margin < ORIENTATION_MARGIN:
        return {"flipped": bool(rings_turned), "confidence": round(margin, 3),
                "source": "undetermined",
                "rho": {"as_designed": round(rho_as_is, 3),
                        "half_turn": round(rho_flipped, 3)},
                "note": ("the discs are too close in contrast to tell which way "
                         "round the insert is fitted; " + assumed)}
    return {"flipped": bool(is_flipped), "confidence": round(margin, 3),
            "source": "measured",
            "rho": {"as_designed": round(rho_as_is, 3),
                    "half_turn": round(rho_flipped, 3)},
            "note": ("the insert is fitted a half turn from the drawing, which "
                     "is a known build variant; the discs are read accordingly."
                     if is_flipped else
                     "the insert is fitted as drawn.")}


#: Orientation sources that are kept rather than decided again from each scan:
#: the one saved for the phantom with its measuring points, and one set by
#: hand in the marking step. "measured" and "undetermined" are this scan's own
#: reading and are always taken again, so a geometry proposed before a block
#: was moved cannot carry a stale verdict into the results.
KEPT_SOURCES = ("stored", "user")

#: Said, and flagged, when a scan confidently contradicts the kept orientation.
#: The kept value still wins: the likeliest reason for the disagreement is a
#: scan filed under the wrong phantom ID, and quietly following the scan would
#: hide exactly that.
ORIENTATION_CONFLICT = (
    "The discs' contrast order looks like the other build of this phantom "
    "(insert turned the other way). Check that the phantom ID is right.")


def rings_turned(ctx: Ctx, geometry: dict) -> bool:
    """Have the rings been turned end for end from the drawing?

    Detection only ever puts the block within 25 degrees of the drawn angle —
    its outline is symmetric, so which end is which is not something it can
    see — and four clicked corners land there too. A block more than a quarter
    turn from the drawing therefore comes from the "Turn 180°" button, or from
    a stored layout saved after it was pressed. That moves every ring onto the
    disc opposite while keeping its label, which is a relabelling of its own.
    It has to be taken out before the insert's orientation is applied, or the
    two corrections would cancel out — or add up.
    """
    nominal = float(ctx.pdef.lowcontrast["angle_deg"])
    g = geometry or {}
    angle = g.get("angle_deg", (g.get("block") or {}).get("angle_deg", nominal))
    try:
        angle = float(angle)
    except (TypeError, ValueError):
        return False
    if not np.isfinite(angle):
        return False
    return abs((angle - nominal + 180.0) % 360.0 - 180.0) > 90.0


def read_orientation(ctx: Ctx, geometry: dict, rows=None) -> dict:
    """Which way round to read this geometry's discs, and on what evidence.

    A kept decision — the phantom's saved one, or one set by hand — is used as
    it stands: that is the point of keeping it, and it is what lets a very weak
    exposure (one reference scan has no disc above |CNR| 0.6) be read the right
    way round at all. The contrast order is still measured and compared, and a
    confident disagreement is flagged in ``conflict`` without being acted on.

    With nothing kept, it is decided from this scan's contrast order exactly as
    before — which is also how profiles saved before orientations were stored
    behave, since they carry none.

    ``rows`` are the measured discs when the caller already has them.
    """
    if rows is None:
        rows, _ = _measure_discs(ctx, (geometry or {}).get("circles") or [])
    turned = rings_turned(ctx, geometry)
    measured = _resolve_orientation(rows, rings_turned=turned)
    kept = (geometry or {}).get("orientation") or {}
    if kept.get("source") in KEPT_SOURCES and isinstance(kept.get("flipped"),
                                                        bool):
        out = {"flipped": kept["flipped"], "source": kept["source"],
               "confidence": measured["confidence"]}
        if "rho" in measured:
            out["rho"] = measured["rho"]
        if measured["source"] == "measured" \
                and measured["flipped"] != kept["flipped"]:
            out["conflict"] = True
            out["note"] = ORIENTATION_CONFLICT
    else:
        out = measured
    if turned:
        out["rings_turned"] = True
    return out


def reads_shifted(orientation: dict) -> bool:
    """Is each ring read as the disc FLIP_SHIFT levels on from its label?

    Yes when exactly one of the two half turns applies — the insert's or the
    rings'. Both together put every ring back on the disc its label names."""
    return bool(orientation.get("flipped")) != bool(
        orientation.get("rings_turned"))


def orientation_state(orientation: dict) -> dict:
    """The part of a reading kept in the geometry: the decision and where it
    came from. The evidence is re-read whenever it is needed, because the
    rings it was read under can be moved afterwards."""
    return {"flipped": bool(orientation.get("flipped")),
            "source": orientation.get("source") or "undetermined",
            "confidence": orientation.get("confidence")}


def orientation_summary(orientation: dict) -> dict:
    """What the marking step shows: four values, a few dozen bytes."""
    return {"flipped": bool(orientation.get("flipped")),
            "source": orientation.get("source") or "undetermined",
            "conflict": bool(orientation.get("conflict")),
            "rings_turned": bool(orientation.get("rings_turned"))}


def compute(ctx: Ctx, geometry: dict) -> dict:
    """CNR of each disc against the ring of block around it.

    CNR needs noise to divide by. Where a disc and its background are both
    perfectly flat — a saturated exposure — there is no contrast-to-noise
    ratio to report, and the old code stored NaN. Serialised that became
    ``null``, and eight discs listed with an empty contrast are what stopped
    the results page on "Computing…" and made the printed report answer 500.

    A disc that cannot be measured is therefore left out of ``rows`` and named
    in ``not_measured`` instead, so every row that exists carries a number.
    """
    rows, not_measured = _measure_discs(ctx, geometry["circles"])

    # Which way round the insert is fitted, before anything is judged by the
    # design order. The ROI ids stay tied to their positions on the block —
    # every stored layout, baseline and trend is keyed on them — and only the
    # design level each one is READ as moves. A saved or hand-set orientation
    # is honoured here; otherwise it is read from this scan's contrast.
    orientation = read_orientation(ctx, geometry, rows)
    shifted = reads_shifted(orientation)
    for entry in rows + not_measured:
        entry["design_level"] = (
            (entry["level"] - 1 + FLIP_SHIFT) % 8 + 1 if shifted
            else entry["level"])
        entry["disc"] = f"L{entry['design_level']}"

    rows.sort(key=lambda r: r["design_level"])
    not_measured.sort(key=lambda r: r["design_level"])
    missing = [r["id"] for r in not_measured]
    total = len(rows) + len(not_measured)

    if not rows:
        return {
            "rows": [], "not_measured": not_measured, "ordering_ok": False,
            "orientation": orientation, "status": "not measured",
            "reasons": [
                f"none of the {total} discs could be measured: the block "
                f"carries no noise, which is what a saturated exposure looks "
                f"like. Repeat the exposure — no statement about low-contrast "
                f"detectability can be made from this image.",
            ],
            "affected": missing,
        }

    # Sanity: |CNR| should broadly increase with the design-order level. Judged
    # only on the discs actually measured, and only when enough of them remain
    # for the trend to mean anything.
    cnrs = [r["abs_cnr"] for r in rows]
    order_rho = _rank_correlation([r["design_level"] for r in rows], cnrs)
    ordering_ok = len(cnrs) >= 4 and order_rho >= ORDER_MIN_RHO

    weak = [r["id"] for r in rows if abs(r["cnr"]) < 0.2]
    reasons = []
    if len(cnrs) < 4:
        reasons.append(
            f"only {len(cnrs)} of {total} discs could be measured, too few to "
            f"judge whether contrast rises with the design order.")
    elif ordering_ok:
        reasons.append(f"|CNR| rises with the design order of the circles "
                       f"(rank correlation {order_rho:.2f}), so the grid is on "
                       f"the right objects.")
    else:
        order = ' < '.join(r['disc'] for r in rows)
        # A kept orientation that the contrast contradicts produces exactly
        # this failure with the grid perfectly placed, so the usual advice
        # would send the operator to move a block that is not wrong.
        if orientation.get("conflict"):
            kept_by = ("saved for this phantom"
                       if orientation["source"] == "stored" else "set by hand")
            cause = (f"The discs were read with the insert orientation "
                     f"{kept_by}, and they rise the other way round — see "
                     f"below.")
        else:
            cause = ("The usual cause is that the block or the circle grid is "
                     "not on the printed objects — reposition the block in "
                     "step C.")
        reasons.append(f"|CNR| does not rise with the circles' design order "
                       f"(rank correlation {order_rho:.2f}; {order} should run "
                       f"weakest to strongest). {cause}")
    # Said whenever it was decided, not only when it is unusual: an operator
    # comparing two prints of the phantom needs to know the reading of the
    # discs was reversed for one of them, and an operator looking at a scan
    # where it could not be decided needs to know that too.
    if orientation["source"] == "measured" and orientation["flipped"]:
        reasons.append(
            f"The low-contrast insert is fitted a half turn from the drawing "
            f"(a known build variant), so each disc is named by the design "
            f"level it actually carries. Read from the contrast itself, not "
            f"from the phantom label — the two labellings differ by "
            f"{orientation['confidence']} in rank correlation.")
    elif orientation["source"] in KEPT_SOURCES:
        # Always said: the reading did not come from this exposure, and an
        # operator comparing it with the numbers has to know that.
        fitted = ("a half turn from the drawing" if orientation["flipped"]
                  else "as drawn")
        kept_by = ("as saved for this phantom"
                   if orientation["source"] == "stored"
                   else "as set by hand in step C")
        reasons.append(f"The low-contrast insert is taken to be fitted "
                       f"{fitted}, {kept_by}, rather than decided from this "
                       f"exposure.")
        if orientation.get("conflict"):
            reasons.append(ORIENTATION_CONFLICT)
    elif orientation["source"] == "undetermined":
        reasons.append(
            f"Which way round the insert is fitted could not be told from this "
            f"exposure: {orientation['note']} If this phantom's insert is "
            f"fitted the other way, the discs are attributed to the wrong "
            f"design levels: set it in step C, and save the measuring points "
            f"for future scans so later scans of this phantom are read the "
            f"same way.")
    if not_measured:
        reasons.append(
            f"{', '.join(missing)} could not be measured at all "
            f"({not_measured[0]['reason']})")
    if weak:
        reasons.append(f"Very low contrast on {', '.join(weak)} "
                       f"(|CNR| < 0.2): either genuinely at the limit of "
                       f"detectability, or the ROI is off the disc.")

    status = ("warn" if not ordering_ok
              else ("not measured" if not_measured else "pass"))
    return {"rows": rows, "not_measured": not_measured,
            "ordering_ok": bool(ordering_ok),
            "order_rho": round(float(order_rho), 3),
            "orientation": orientation,
            "status": status, "reasons": reasons, "affected": weak + missing}


#: Sampling density of the block view, pixels per millimetre. The block is
#: 84.5 x 44.45 mm, so this is about 340 x 180 samples — a few tens of
#: kilobytes as a greyscale PNG, which is the whole point: the operator can
#: look at the discs without pulling the 0.9 MB full render and re-windowing
#: it, which on a 512 kbit/s link costs fifteen seconds every attempt.
VIEW_PX_PER_MM = 4.0

#: Millimetres. Structure broader than this is the block's own illumination
#: gradient rather than a disc, and subtracting it is what makes discs at the
#: limit of visibility actually visible.
VIEW_FLATTEN_MM = 8.0

#: Millimetres. The discs are 10 mm across, so smoothing at this scale costs
#: no real detail and removes the noise that hides the faintest ones.
VIEW_SMOOTH_MM = 1.0


def block_view(ctx: Ctx, center_mm, angle_deg: float, margin_mm: float = 6.0):
    """A straightened, flattened, locally-windowed picture of just the block.

    The field report for this was "it is difficult to see them and properly
    adjust the W/C to place the marks". Three separate things make the discs
    hard to see in the full image, and this addresses each:

    the block is a small part of a large picture, so it is sampled here on its
    own; the window that suits the whole phantom is far too wide for objects
    whose contrast is a fraction of a percent, so the window is taken from the
    block's own values; and the block carries a broad illumination gradient
    that swamps the discs, so it is flattened first.

    Returned in the block's own frame — long axis horizontal, L1's corner the
    same way up every time — so it looks the same on every scan regardless of
    how the phantom happened to lie on the table.
    """
    from scipy.ndimage import gaussian_filter, map_coordinates

    lc = ctx.pdef.lowcontrast
    w_mm = float(lc["size_mm"][0]) + 2 * margin_mm
    h_mm = float(lc["size_mm"][1]) + 2 * margin_mm
    cols = max(int(round(w_mm * VIEW_PX_PER_MM)), 8)
    rows = max(int(round(h_mm * VIEW_PX_PER_MM)), 8)

    # A grid in the block's frame, mapped back into the image and sampled.
    us = (np.arange(cols) + 0.5) / VIEW_PX_PER_MM - w_mm / 2
    vs = (np.arange(rows) + 0.5) / VIEW_PX_PER_MM - h_mm / 2
    uu, vv = np.meshgrid(us, vs)
    a = np.deg2rad(float(angle_deg))
    u_hat = np.array([np.cos(a), np.sin(a)])
    v_hat = np.array([-np.sin(a), np.cos(a)])
    pts_mm = (np.asarray(center_mm, float)
              + uu[..., None] * u_hat + vv[..., None] * v_hat)
    pts_px = ctx.T.mm_to_px(pts_mm.reshape(-1, 2))
    # map_coordinates indexes (row, col) = (y, x); order=1 is bilinear, which
    # is right for a picture meant to be looked at rather than measured.
    sample = map_coordinates(
        ctx.pixels, [pts_px[:, 1], pts_px[:, 0]], order=1, mode="nearest"
    ).reshape(rows, cols).astype(float)

    sigma_flat = VIEW_FLATTEN_MM * VIEW_PX_PER_MM
    sigma_fine = VIEW_SMOOTH_MM * VIEW_PX_PER_MM
    flat = gaussian_filter(sample, sigma_fine) - gaussian_filter(sample,
                                                                 sigma_flat)

    # Window from the block's interior only. Including the margin would let
    # the much brighter surround set the range and flatten the discs back out.
    m = int(round(margin_mm * VIEW_PX_PER_MM))
    inner = flat[m:rows - m, m:cols - m] if rows > 2 * m and cols > 2 * m \
        else flat
    finite = inner[np.isfinite(inner)]
    if finite.size:
        lo, hi = (float(np.percentile(finite, 1)),
                  float(np.percentile(finite, 99)))
    else:
        lo, hi = 0.0, 1.0
    if not (hi > lo):
        # A saturated or empty exposure. Returned as a flat grey picture
        # rather than as an error: the operator still needs to see that there
        # is nothing there, and the quality gate has already said why.
        lo, hi = 0.0, 1.0

    return {
        "image": np.clip((flat - lo) / (hi - lo), 0.0, 1.0),
        "px_per_mm": VIEW_PX_PER_MM,
        "origin_mm": [-w_mm / 2, -h_mm / 2],
        "size_px": [cols, rows],
        "flat": bool(finite.size == 0 or not np.isfinite(finite).any()),
    }


def view_markers(ctx: Ctx, geometry: dict, center_mm, angle_deg: float,
                 margin_mm: float = 6.0) -> list[dict]:
    """Where each disc sits in the block view, and how well it shows.

    The strength is the same matched-filter response the automatic placement
    uses, expressed against the spread of all eight so it means the same thing
    on a strong exposure and a weak one. It is what lets the interface say
    which discs are at the limit of visibility rather than leaving the
    operator to judge eight faint circles by eye.
    """
    lc = ctx.pdef.lowcontrast
    w_mm = float(lc["size_mm"][0]) + 2 * margin_mm
    h_mm = float(lc["size_mm"][1]) + 2 * margin_mm
    a = np.deg2rad(float(angle_deg))
    u_hat = np.array([np.cos(a), np.sin(a)])
    v_hat = np.array([-np.sin(a), np.cos(a)])
    du, dv = (geometry or {}).get("grid_shift_mm", (0.0, 0.0))[:2]

    out = []
    for c in lc["circles"]:
        u = float(c["u_mm"]) + float(du)
        v = float(c["v_mm"]) + float(dv)
        centre_mm = np.asarray(center_mm, float) + u * u_hat + v * v_hat
        out.append({
            "id": c["id"],
            "level": c["level"],
            "x_px": (u + w_mm / 2) * VIEW_PX_PER_MM,
            "y_px": (v + h_mm / 2) * VIEW_PX_PER_MM,
            "r_px": lc["circle_dia_mm"] / 2 * VIEW_PX_PER_MM,
            "response": float(_circle_response(ctx, centre_mm,
                                               lc["circle_dia_mm"])),
        })

    strengths = [m["response"] for m in out]
    top = max(strengths) if strengths else 0.0
    for m in out:
        # Against the strongest disc on this same exposure, so the judgement
        # is about this phantom rather than about the dose.
        share = m["response"] / top if top > 0 else 0.0
        m["visibility"] = ("clear" if share >= 0.5 else
                           "faint" if share >= 0.2 else "at the limit")
    return out


def circles_for_block(ctx: Ctx, center_mm, angle_deg: float,
                      shift_mm=(0.0, 0.0)) -> list[dict]:
    """Rebuild the eight circle ROIs from a block position and angle.

    The circles sit on a rigid grid in the block's own frame, so once the user
    has placed the block rectangle correctly every circle follows from it. This
    is what makes the block draggable and rotatable as a whole: one correction
    instead of eight."""
    lc = ctx.pdef.lowcontrast
    a = np.deg2rad(float(angle_deg))
    u = np.array([np.cos(a), np.sin(a)])
    v = np.array([-np.sin(a), np.cos(a)])
    dia = lc["circle_dia_mm"]
    roi_dia = lc.get("roi_dia_mm", 7.0)
    bg_in = lc.get("bg_inner_dia_mm", 12.0)
    bg_out = lc.get("bg_outer_dia_mm", 16.0)
    du, dv = float(shift_mm[0]), float(shift_mm[1])

    out = []
    for c in lc["circles"]:
        cc = (np.asarray(center_mm, float) + (c["u_mm"] + du) * u
              + (c["v_mm"] + dv) * v)
        out.append({
            "id": c["id"], "level": c["level"],
            "roi": circle_roi(ctx, cc, roi_dia, roi_id=f"lowcontrast/{c['id']}"),
            "bg_roi": annulus_roi(ctx, cc, bg_in, bg_out,
                                  roi_id=f"lowcontrast/{c['id']}/bg"),
            "full_circle": circle_roi(ctx, cc, dia,
                                      roi_id=f"lowcontrast/{c['id']}/outline"),
        })
    return out


def block_from_corners(ctx: Ctx, corners_px) -> tuple[list, float]:
    """Centre and angle of the block from four user-clicked corners.

    Click order does not matter: the long axis is taken from the longer pair of
    opposite edge midpoints, mirroring how the phantom outline is fitted."""
    pts = np.array([ctx.T.px_to_mm(p) for p in corners_px], float)
    if len(pts) != 4:
        raise ValueError("exactly four corners are required")
    centre = pts.mean(axis=0)
    ordered = pts[np.argsort(np.arctan2(pts[:, 1] - centre[1],
                                        pts[:, 0] - centre[0]))]
    mids = [(ordered[i] + ordered[(i + 1) % 4]) / 2.0 for i in range(4)]
    axis_a = mids[2] - mids[0]
    axis_b = mids[3] - mids[1]
    axis = axis_a if np.linalg.norm(axis_a) >= np.linalg.norm(axis_b) else axis_b
    angle = float(np.degrees(np.arctan2(axis[1], axis[0])))
    if angle > 90:
        angle -= 180
    if angle < -90:
        angle += 180
    return [float(centre[0]), float(centre[1])], angle
