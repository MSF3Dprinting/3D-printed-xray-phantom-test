"""A test that could not be analysed must never read as a test that passed.

`propose_all` deliberately swallows a per-test exception into
``{"_error": ...}`` so one failed detection does not block the wizard, and
`compute_all` turns that into ``{"status": "not measured", "error": ...}`` with
no rows.
Step B already reports it. Everything downstream has to as well: an absent
answer on a document somebody signs has to look absent, not favourable.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

from phantom_qa import pipeline
from phantom_qa.analysis.common import Ctx
from phantom_qa.phantom_def import load_default
from phantom_qa.registration import Transform
from phantom_qa.report import build_report


def _ctx():
    rng = np.random.default_rng(2)
    return Ctx(pixels=2000 + rng.normal(0, 20, (64, 64)),
               T=Transform(A=np.array([[1.0, 0.0], [0.0, -1.0]]),
                           t=np.array([32.0, 32.0])),
               pdef=load_default())


# ----------------------------------------------------- what the backend emits

def test_a_failed_test_produces_no_rows_at_all():
    """The shape everything downstream has to cope with."""
    out = pipeline.compute_all(
        _ctx(), {"lowcontrast": {"_error": "ValueError: block not found"}})
    lc = out["lowcontrast"]
    assert lc["status"] == "not measured"
    assert "rows" not in lc
    assert lc["error"] == "ValueError: block not found"
    # and a test that was not proposed at all is equally rowless
    assert "rows" not in out["wedge"]
    # a test that was never measured weighs as a warning, never as a pass
    assert pipeline.overall_status(out) == "warn"


# ------------------------------------------------------- the printable report

def _results_with_one_failure():
    return pipeline.compute_all(
        _ctx(), {"lowcontrast": {"_error": "ValueError: block not found"}})


def _record(results):
    return {"id": "abc123def456", "created_at": "2026-08-01 10:00:00",
            "acquired_at": "2026-07-27 09:37:26", "source_name": "scan.dcm",
            "sha256": "a" * 64, "signature": "sig", "algo_version": "1.0.0",
            "sid_mm": 1000.0, "site": "Goma", "phantom": "MSF-01",
            "results": results, "meta": {}, "reg": None, "geometry": None,
            "status": pipeline.overall_status(results), "is_baseline": 0,
            "validation_status": ""}


@pytest.mark.parametrize("title", ["Line patterns", "Low contrast",
                                   "Uniformity", "Wedge"])
def test_the_report_still_names_a_test_it_could_not_analyse(title):
    """Dropping the section made a scan where a pattern was never measured
    read exactly like one where it passed."""
    html = build_report(_record(_results_with_one_failure()))
    assert title in html, f"the report silently omitted the {title} section"


def test_the_report_says_why_it_could_not_be_analysed():
    html = build_report(_record(_results_with_one_failure()))
    assert "could not be analysed on this scan" in html
    assert "block not found" in html


def test_a_complete_analysis_is_unaffected():
    from test_store_labels import results_for
    html = build_report(_record(results_for()))
    assert "could not be analysed" not in html
    assert "Line patterns" in html


# --------------------------------------------------------------- the wizard

def _app_js() -> str:
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "phantom_qa", "webapp", "static", "app.js")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_step_e_gives_every_test_a_row_even_with_no_results():
    """Step E builds its summary from `cards`, and only a test with rows used
    to push one. A rowless test therefore had no summary row, no detail and no
    error message — while the verdict beneath announced that every test
    passed."""
    js = _app_js()
    assert "TEST_TITLES" in js, (
        "step E has no fallback for a test that produced no rows")
    assert "cards.some(cd => cd.key === t)" in js
    assert "not analysed" in js


def test_step_e_does_not_count_not_analysed_as_passed():
    js = _app_js()
    # Only a pass and "not applicable" are spared attention; "not measured"
    # and the legacy "n/a" are the absence of an answer, not a pass.
    assert ('const needsLook = (s) => s !== "pass" && s !== "not applicable";'
            in js), "step E no longer says which statuses need a look"
    assert "const attention = cards.filter(cd => needsLook(cd.status));" in js, (
        'a status of "not measured" is the absence of an answer, not a pass')
    # and such a section opens itself, like any other non-pass
    assert re.search(r'needsLook\(cd\.status\)\)\)\.join\(""\);', js), (
        "a test that was not analysed stays collapsed")


def test_a_test_that_measured_nothing_reads_differently_from_one_that_broke():
    """Two absences, two meanings.

    A test that raised is a fault in the software. A test that ran and found
    nothing measurable is a verdict on the exposure — the operator should
    repeat it, not report a bug. Collapsing both into one phrase loses the
    only thing that tells them which to do."""
    js = _app_js()
    assert '"not analysed"' in js and '"not measured"' in js, \
        "step E must distinguish a broken test from an unmeasurable image"
    assert "rr.error" in js, "the distinction has to key on the error field"

    from phantom_qa.report import _not_analysed
    broke = _not_analysed("Low contrast",
                          {"lowcontrast": {"status": "error",
                                           "error": "ValueError: boom"}},
                          "lowcontrast")
    assert "could not be analysed" in broke and "boom" in broke

    nothing = _not_analysed(
        "Low contrast",
        {"lowcontrast": {"status": "not measured",
                         "reasons": ["the block carries no noise"],
                         "not_measured": [{"id": "L1", "reason": "no noise"}]}},
        "lowcontrast")
    assert "could not be measured" in nothing
    assert "the block carries no noise" in nothing
    assert "L1" in nothing and "Not measured" in nothing
