"""Is this exposure worth measuring at all?

Every test downstream assumes the image actually contains a phantom that was
exposed sensibly. When it does not, the measurements do not become wrong in an
obvious way — they become meaningless in a quiet way, and the first field test
showed what that costs:

* an over-ranged exposure, in which the phantom is one flat slab, was analysed,
  finalised, and its measuring points were written to the phantom's *shared*
  layout — so the next operator inherited marks derived from an image in which
  nothing could be measured;
* an exposure with the phantom hanging off the detector edge was accepted with
  no warning at all, although the registration had stretched 5 % along one axis
  and every ROI on that side was off its target.

This module answers one question — *can this image be measured?* — from signals
that need no knowledge of what the phantom contains, so a broken exposure is
caught before any of it matters.

Thresholds are set from the 33 HQ reference scans (both detectors, both prints,
every orientation, the whole dose series) and verified to pass all of them by a
wide margin while failing the broken field exposures. `tests/test_quality.py`
re-derives them from the reference benchmark, so a threshold cannot quietly
drift away from the evidence behind it. Field exposures are only ever used to
confirm the gate fires; nothing here is tuned on them.
"""

from __future__ import annotations

import numpy as np

#: Each limit sits between what the reference scans measure and what a broken
#: exposure measures, with the observed range of both in the comment. The gate
#: is deliberately loose: it exists to catch images nothing can be measured in,
#: not to judge quality. A scan has to be clearly unusable to trip it.
THRESHOLDS = {
    # share of the image pinned at its own maximum
    #   reference 0.000-0.0007   ·  over-ranged field exposures 0.966, 0.970
    "saturated_fraction_max": 0.05,
    # stretch of the fitted transform; 1.0 is rigid
    #   reference 1.0009-1.0025  ·  phantom clipped at the detector edge 1.0515
    "anisotropy_max": 1.010,
    # central ruler lines found, of four
    #   reference 4/4            ·  over-ranged exposures 0/4
    "landmarks_min": 3,
    # registration score of the accepted candidate
    #   reference 7.33-7.89      ·  over-ranged 3.79-3.84, Leeds object 4.2-4.9
    "score_min": 5.5,
    # how far the accepted candidate beat the runner-up
    #   reference 0.89-1.83      ·  clipped 0.12, over-ranged 0.17-0.22
    "score_margin_min": 0.40,
}


def saturated_fraction(pixels) -> float:
    """Share of the image sitting on its own maximum value.

    Deliberately measured against the image's own maximum rather than the
    detector's full scale: vendor processing rescales, and what matters is that
    a large part of the image has been flattened onto one value, whatever that
    value turns out to be.
    """
    px = np.asarray(pixels, dtype=float)
    if px.size == 0:
        return 1.0
    top = float(px.max())
    if not np.isfinite(top):
        return 1.0
    return float(np.mean(px >= top - 0.5))


def anisotropy(transform):
    """Ratio of the transform's two singular values; 1.0 is perfectly rigid.

    The phantom is rigid, so a fit that has to stretch one axis to match it did
    not see the whole outline. That is what an edge leaving the detector looks
    like, and it is visible without knowing anything about the phantom.

    None when the fit has collapsed onto a line and the ratio does not exist.
    """
    sv = np.linalg.svd(np.asarray(transform.A, dtype=float), compute_uv=False)
    if sv[1] <= 0:
        return None
    return _num(sv[0] / sv[1])


def _num(v):
    """A real number, or None.

    Every value this module reports crosses two boundaries that reject
    infinities and NaN — SQLite's JSON column and the browser's parser — and a
    non-finite one used to abort the whole upload response with "Out of range
    float values are not JSON compliant". More to the point, a check whose
    measurement does not exist has to say so rather than smuggle a special
    float through and hope every later comparison happens to behave.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _check(cid, label, value, limit, ok, detail):
    return {"id": cid, "label": label, "value": _num(value), "limit": limit,
            "ok": bool(ok), "detail": detail}


def assess(pixels, reg) -> dict:
    """Judge one exposure. Returns a verdict plus every check behind it.

    The checks overlap on purpose. Saturation and a lost landmark both catch an
    over-ranged exposure; anisotropy and the score margin both catch a clipped
    one. No single signal has to be perfect, and the operator is shown which
    ones tripped rather than an unexplained refusal.
    """
    t = THRESHOLDS
    checks = []

    sat = saturated_fraction(pixels)
    checks.append(_check(
        "saturation", "Pixels pinned at the maximum", sat,
        t["saturated_fraction_max"], sat <= t["saturated_fraction_max"],
        f"{sat:.1%} of the image sits on one value. Above "
        f"{t['saturated_fraction_max']:.0%} there is nothing left to measure "
        f"inside the phantom."))

    aniso = anisotropy(reg.transform)
    checks.append(_check(
        "clipping", "Squareness of the fitted phantom", aniso,
        t["anisotropy_max"],
        aniso is not None and aniso <= t["anisotropy_max"],
        (f"the fit had to stretch {(aniso - 1) * 100:.1f}% along one axis. The "
         f"phantom is rigid, so this means part of its outline is not on the "
         f"image — usually an edge past the detector boundary.")
        if aniso is not None else
        "the fitted outline has collapsed, so the phantom was not located."))

    found = sum(1 for v in (reg.landmarks or {}).values()
                if v.get("err_mm") is not None)
    checks.append(_check(
        "landmarks", "Central ruler lines found", found, t["landmarks_min"],
        found >= t["landmarks_min"],
        f"{found} of 4 ruler lines could be located. They are the strongest "
        f"features on the phantom; losing them means the image carries very "
        f"little detail."))

    score = _num((reg.score or {}).get("total"))
    checks.append(_check(
        "recognition", "Confidence the phantom was recognised", score,
        t["score_min"], score is not None and score >= t["score_min"],
        (f"the accepted placement scored {score:.2f}. A reference scan scores "
         f"above {t['score_min']:.1f}; below it, what was found may not be "
         f"this phantom at all.")
        if score is not None else
        "the placement was not scored, so nothing confirms a phantom was found."))

    totals = sorted((v for v in ((_num(c.get("total"))
                                  for c in (reg.candidate_scores or [])))
                     if v is not None), reverse=True)
    # With a single candidate there is no runner-up to be ambiguous against, so
    # the check has nothing to object to. That is reported as an absent
    # measurement rather than an infinite lead — infinity is not a number any
    # of the things downstream will accept.
    margin = (totals[0] - totals[1]) if len(totals) >= 2 else None
    checks.append(_check(
        "placement_margin", "Lead over the next-best placement", margin,
        t["score_margin_min"],
        margin is None or margin >= t["score_margin_min"],
        (f"the chosen placement beat the runner-up by only {margin:.2f}. When "
         f"the image is this ambiguous the phantom may have been located in "
         f"the wrong orientation or position.")
        if margin is not None else
        "only one placement was considered, so there was nothing to confuse "
        "it with."))

    # Belt and braces: this dict is written to a JSON column and returned in
    # the upload response, and neither accepts a non-finite float. _check()
    # already cleans every value; assert it here so a future check that forgets
    # cannot take an operator's upload down with it.
    assert all(c["value"] is None or np.isfinite(c["value"]) for c in checks)

    failed = [c for c in checks if not c["ok"]]
    return {
        "verdict": "poor" if failed else "ok",
        "checks": checks,
        "failed": [c["id"] for c in failed],
        "summary": _summary(failed),
    }


def _summary(failed) -> str:
    """One sentence an operator can act on.

    It says what is observed, never what caused it. The field team described
    their ruined exposures as "underexposed" while the detector reported the
    opposite (exposure index 2478 against a target of 876) — so naming a cause
    would have printed the wrong advice with great confidence.
    """
    if not failed:
        return "The image is suitable for measurement."
    ids = {c["id"] for c in failed}
    if "saturation" in ids:
        head = ("Most of this image sits on a single value, so the phantom "
                "carries no signal to measure. Check the exposure — the "
                "detector's own index will say whether it was too high or too "
                "low — and repeat it.")
    elif "clipping" in ids:
        head = ("The phantom does not appear to be fully inside the image, so "
                "measurements near the missing edge cannot be trusted. "
                "Reposition the phantom and repeat the exposure.")
    elif "recognition" in ids or "placement_margin" in ids:
        head = ("The phantom could not be located with confidence. Check that "
                "the right object was imaged and that it lies flat and fully "
                "within the field.")
    else:
        head = ("This image is missing features the measurements rely on.")
    return head + " The analysis can still be run, but it cannot become a " \
                  "reference scan or set the measuring points for this " \
                  "phantom unless an administrator approves it."


def blocks_reference_use(quality) -> bool:
    """Whether this exposure may become a baseline or a stored layout.

    A poor exposure can still be analysed — an operator may need the numbers,
    or want to show what went wrong — but it must not become the standard that
    later scans are compared against, nor supply the measuring points the next
    operator starts from. That is how one ruined exposure contaminates a
    phantom's whole history.

    True means refused on an ordinary user's say-so. The web layer lets an
    administrator overrule it, with a written reason, for the rare exposure
    that failed a check and is still the best a site has.
    """
    return bool(quality) and quality.get("verdict") == "poor"
