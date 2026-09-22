"""Which way round the low-contrast insert is fitted.

Two prints of the phantom are in use and both are correct. They differ in how
the eight-disc insert was fitted: one a half turn from the other. The disc
pattern is symmetric under that half turn — every disc lands where another
disc is expected — so the ROIs are placed correctly either way and nothing in
the picture shows which build it is. What changes is only which disc is which.

Read with the wrong assumption, contrast appears not to rise with the design
order, and the test warns that "the block or the circle grid is not on the
printed objects" — about a grid that is exactly where it should be. Nine of
the thirty-three reference scans warned for this reason and none of them for a
real one.

The answer is read from the contrast order itself rather than from the phantom
label, because the label is typed by hand: a mistyped one would otherwise
silently reverse the reading of every disc.
"""

from __future__ import annotations

import json
import os

import pytest

from phantom_qa.analysis import lowcontrast as LC

HERE = os.path.dirname(os.path.abspath(__file__))


def _rows(values):
    """Eight discs whose |CNR| is given in design order L1…L8."""
    return [{"level": i + 1, "abs_cnr": float(v)} for i, v in enumerate(values)]


RISING = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1]


# ------------------------------------------------------------- the statistic

def test_contrast_rising_with_the_design_order_reads_as_designed(client=None):
    o = LC._resolve_orientation(_rows(RISING))
    assert o["flipped"] is False
    assert o["source"] == "measured"


def test_the_same_discs_seen_a_half_turn_round_read_as_flipped(client=None):
    """The exact transformation the second print applies, and nothing else.

    A half turn exchanges the two rows of four, so the disc designed as L5 is
    sitting where L1 is expected."""
    turned = RISING[4:] + RISING[:4]
    o = LC._resolve_orientation(_rows(turned))
    assert o["flipped"] is True
    assert o["source"] == "measured"


def test_reading_it_the_right_way_round_is_what_makes_the_order_rise(client=None):
    """The point of the whole exercise, stated as an equality."""
    turned = RISING[4:] + RISING[:4]
    rows = _rows(turned)
    o = LC._resolve_orientation(rows)
    resolved = sorted(
        ((r["level"] - 1 + LC.FLIP_SHIFT) % 8 + 1 if o["flipped"] else r["level"],
         r["abs_cnr"]) for r in rows)
    values = [v for _, v in resolved]
    assert values == sorted(values), "resolved order still does not rise"


def test_discs_too_close_together_are_not_guessed_at(client=None):
    """Refusing to decide is the honest answer, and it must be said out loud.

    One reference exposure is this weak. Guessing there would reverse every
    disc on a coin toss and report it as fact."""
    o = LC._resolve_orientation(_rows([0.30, 0.31, 0.30, 0.31,
                                       0.30, 0.31, 0.30, 0.31]))
    assert o["source"] == "undetermined"
    assert o["flipped"] is False, "undetermined must fall back to as designed"
    assert "could not" in o["note"] or "too close" in o["note"]


def test_too_few_measured_discs_is_also_not_guessed_at(client=None):
    o = LC._resolve_orientation(_rows(RISING)[:4])
    assert o["source"] == "undetermined"
    assert "4 of 8" in o["note"]


def test_a_flat_exposure_does_not_divide_by_zero(client=None):
    o = LC._resolve_orientation(_rows([0.5] * 8))
    assert o["source"] == "undetermined"
    assert o["confidence"] == 0.0


def test_the_confidence_is_the_gap_between_the_two_readings(client=None):
    o = LC._resolve_orientation(_rows(RISING))
    assert o["confidence"] == pytest.approx(
        abs(o["rho"]["as_designed"] - o["rho"]["half_turn"]), abs=1e-3)


def test_the_statistic_ignores_how_strong_the_contrast_is(client=None):
    """Absolute contrast varies five-fold between exposures; the ORDER is a
    property of the phantom, so scaling every disc must change nothing."""
    weak = LC._resolve_orientation(_rows([v * 0.1 for v in RISING]))
    strong = LC._resolve_orientation(_rows([v * 10 for v in RISING]))
    assert weak["flipped"] == strong["flipped"] is False
    assert weak["confidence"] == strong["confidence"]


@pytest.mark.parametrize("values", [
    [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1],
    [1.1, 1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4],
    [0.1, 0.9, 0.2, 0.8, 0.3, 0.7, 0.4, 0.6],
])
def test_it_never_throws_whatever_the_discs_measure(values):
    o = LC._resolve_orientation(_rows(values))
    assert o["source"] in ("measured", "undetermined")
    assert isinstance(o["flipped"], bool)
    assert o["confidence"] >= 0


# ------------------------------------------------- against the real phantoms

def _benchmark():
    path = os.path.join(HERE, "hq_benchmark.json")
    if not os.path.exists(path):
        pytest.skip("HQ benchmark not present")
    with open(path, encoding="utf-8") as f:
        return json.load(f)["scans"]


def _expected_flip(entry):
    """The dark print is the one fitted a half turn round.

    Taken from the label burned into each exposure, which is the physical
    print it is — evidence, not something the code under test can see."""
    return "donker" in (entry.get("label") or "")


def test_every_reference_scan_is_read_the_way_its_print_is_built():
    """Thirty-three scans, two prints, decided from the pixels alone.

    The one scan that is not decided is reported as undecided rather than
    guessed — it carries no disc above |CNR| 0.55, which is too little to
    support the judgement."""
    scans = _benchmark()
    carestream = {k: v for k, v in scans.items()
                  if v.get("label") and ("licht" in v["label"]
                                         or "donker" in v["label"])}
    assert len(carestream) >= 25, "the reference set shrank unexpectedly"

    wrong, undecided = [], []
    for key, entry in sorted(carestream.items()):
        cnr = entry["metrics"]["lowcontrast_cnr"]
        o = LC._resolve_orientation(
            [{"level": i, "abs_cnr": abs(cnr[f"L{i}"])} for i in range(1, 9)])
        if o["source"] != "measured":
            undecided.append((key, entry["label"], o["confidence"]))
        elif o["flipped"] != _expected_flip(entry):
            wrong.append((key, entry["label"], o))

    assert not wrong, f"read the wrong way round: {wrong}"
    assert len(undecided) <= 1, f"too many undecided: {undecided}"


def test_no_scan_of_the_light_print_is_ever_read_as_turned():
    """A false flip is worse than no decision: it renames every disc."""
    for key, entry in sorted(_benchmark().items()):
        if "licht" not in (entry.get("label") or ""):
            continue
        cnr = entry["metrics"]["lowcontrast_cnr"]
        o = LC._resolve_orientation(
            [{"level": i, "abs_cnr": abs(cnr[f"L{i}"])} for i in range(1, 9)])
        assert o["flipped"] is False, f"{key} {entry['label']} read as turned"


def test_the_decided_scans_are_decided_by_a_wide_margin():
    """The threshold sits in a gap in the evidence, not near the data.

    Every confidently decided reference scan separates by at least 1.1, and
    the threshold is 0.6 — so this is not a value tuned until the answers came
    out right."""
    margins = []
    for entry in _benchmark().values():
        if not entry.get("label"):
            continue
        cnr = entry["metrics"]["lowcontrast_cnr"]
        o = LC._resolve_orientation(
            [{"level": i, "abs_cnr": abs(cnr[f"L{i}"])} for i in range(1, 9)])
        if o["source"] == "measured":
            margins.append(o["confidence"])
    assert margins, "no scan was decided at all"
    assert min(margins) > LC.ORIENTATION_MARGIN * 1.5, \
        f"the closest decided scan sits at {min(margins)}"


# ------------------------------------------- how the disc order is judged

def _resolved_rho(entry):
    cnr = entry["metrics"]["lowcontrast_cnr"]
    rows = [{"level": i, "abs_cnr": abs(cnr[f"L{i}"])} for i in range(1, 9)]
    o = LC._resolve_orientation(rows)
    levels = [(r["level"] - 1 + LC.FLIP_SHIFT) % 8 + 1 if o["flipped"]
              else r["level"] for r in rows]
    return LC._rank_correlation(levels, [r["abs_cnr"] for r in rows])


def test_the_order_check_no_longer_warns_about_grids_that_are_on_the_discs():
    """The criterion it replaces counted neighbouring pairs that rose.

    Two adjacent discs differ by less than the noise between exposures, so
    their order flips at random — and the count then drops for a reason that
    says nothing about where the grid is. It warned about three reference
    scans correlating at 0.88, 0.88 and 0.93, every one of them placed
    correctly. That is the false warning the field reported."""
    good = [k for k, v in _benchmark().items() if _resolved_rho(v) >= 0.85]
    assert len(good) >= 30, "the reference set shrank unexpectedly"
    for key in good:
        rho = _resolved_rho(_benchmark()[key])
        assert rho >= LC.ORDER_MIN_RHO, f"{key} would still be warned about"


def test_the_one_scan_with_nothing_to_hold_on_to_is_still_warned_about():
    """The check has to keep catching the case it exists for."""
    scans = _benchmark()
    bad = [k for k, v in scans.items() if _resolved_rho(v) < LC.ORDER_MIN_RHO]
    assert len(bad) == 1, f"expected exactly one, got {bad}"
    cnr = scans[bad[0]]["metrics"]["lowcontrast_cnr"]
    assert max(abs(v) for v in cnr.values()) < 0.6, \
        "the scan that fails should be one with almost no contrast anywhere"


def test_the_threshold_sits_in_a_gap_rather_than_against_the_data():
    """A limit set just past the worst good case is a limit fitted to it.

    Here the worst correctly-placed scan correlates at about 0.86 and the one
    genuinely bad scan at about -0.26, so any value in between would do and
    the choice is not load-bearing."""
    rhos = sorted(_resolved_rho(v) for v in _benchmark().values())
    below = [r for r in rhos if r < LC.ORDER_MIN_RHO]
    above = [r for r in rhos if r >= LC.ORDER_MIN_RHO]
    assert below and above
    assert min(above) - max(below) > 1.0, \
        f"only {min(above) - max(below):.2f} between the two groups"


def test_an_order_that_runs_the_wrong_way_is_still_caught():
    """Reversed is not the same as turned round: a half turn exchanges the two
    rows of four, it does not reverse all eight. A truly reversed sequence has
    to fail both readings."""
    from phantom_qa.analysis.lowcontrast import _rank_correlation
    reversed_seq = [1.1, 1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4]
    o = LC._resolve_orientation(_rows(reversed_seq))
    levels = [(i + LC.FLIP_SHIFT) % 8 + 1 if o["flipped"] else i + 1
              for i in range(8)]
    rho = _rank_correlation(levels, reversed_seq)
    assert rho < LC.ORDER_MIN_RHO, "a reversed order must not read as fine"
