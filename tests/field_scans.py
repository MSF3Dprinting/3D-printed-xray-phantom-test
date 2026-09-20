"""The first field test's exposures, as NEGATIVE fixtures.

These five images came back from the field on 2026-09-20 and three of them are
knowingly unusable: one has the phantom running off the detector edge and two
are so over-ranged that the phantom is a white slab. They are kept because a
broken exposure is the hardest thing to test with and the easiest thing to meet
in a clinic — the "stuck at Computing…" report came from one of them.

    ┌──────────────────────────────────────────────────────────────────────┐
    │  NEVER measure these to tune anything.                               │
    │                                                                      │
    │  Not the phantom definition, not a stored layout profile, not a      │
    │  baseline, and not a detection threshold. Thresholds are set from    │
    │  the HQ reference scans (tests/hq_manifest.py) and only CHECKED      │
    │  here — the good ones must stay inside the gate, the broken ones     │
    │  must fall outside it.                                               │
    │                                                                      │
    │  test_unusable_exposures.py enforces this; see the guard tests at    │
    │  the end of that file.                                               │
    └──────────────────────────────────────────────────────────────────────┘

Absent on most machines (they are not in git), so everything that uses them
skips. The synthetic equivalents in test_unusable_exposures.py cover the same
failure modes and always run.
"""

from __future__ import annotations

import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Where the field drops live. Same convention as PHANTOMQA_SAMPLE_DIR and
#: PHANTOMQA_HQ_DIR, so a machine that keeps scans elsewhere can say so.
FIELD_ROOT = os.environ.get("PHANTOMQA_FIELD_DIR", "").strip() \
    or os.path.join(ROOT, "Field testing")

#: What is wrong with each exposure, established by measuring them (see
#: docs/FIELD_TEST_PLAN.md). ``defect`` is what the software has to recognise;
#: ``usable`` marks the two that a QA operator would accept.
INVENTORY = [
    {
        "key": "F001_clipped",
        "relpath": "20260920-091852/001_0000.dcm",
        "sha256": "441289f12dfce83d3284052cc93b0e01c7cae9a8429d2ce1b84fa037565b78fa",
        "defect": "clipped",
        "usable": False,
        "note": "phantom cut off at the right detector edge; registration "
                "stretches to the border and every right-hand ROI shifts, "
                "yet the scan is accepted without a warning",
    },
    {
        "key": "F002_usable",
        "relpath": "20260920-091914/002_0000.dcm",
        "sha256": "1d1ccdd9e6e5bd190f0fe6c7d3b864aaec6b31fa88c4d74d480f409cfba4f245",
        "defect": None,
        "usable": True,
        "note": "good exposure, phantom rotated 90 degrees",
    },
    {
        "key": "F003_saturated",
        "relpath": "20260920-091938/003_0000.dcm",
        "sha256": "754362415c0c4343111309afef8e8121f7e01f56ddf42de66843431295d3800c",
        "defect": "saturated",
        "usable": False,
        "note": "over-ranged (EI 2478 against a target of 876): the phantom "
                "body is one flat value, so nothing inside it can be measured",
    },
    {
        "key": "F003B_saturated",
        "relpath": "20260920-091956/003_0000.dcm",
        "sha256": "459307d281c47686c63f9f1d9512bc072e7d283403601e5bba3139b5b7494cde",
        "defect": "saturated",
        "usable": False,
        "note": "second over-ranged retake. Shares file name AND byte size "
                "with F003 but is a different exposure — the pair that the "
                "field team thought the software had confused",
    },
    {
        "key": "F004_usable",
        "relpath": "20260920-092019/004_0000.dcm",
        "sha256": "79396cc6be468019f109bc10698b8efcab8bb7a59d10427437ae69e1e486c7d4",
        "defect": None,
        "usable": True,
        "note": "good exposure, phantom upright",
    },
]


def path_of(entry) -> str:
    return os.path.join(FIELD_ROOT, *entry["relpath"].split("/"))


def available(entry) -> bool:
    return os.path.exists(path_of(entry))


def have_scans() -> bool:
    return all(available(e) for e in INVENTORY)


def by_defect(defect):
    """Entries with a given defect; ``None`` gives the usable ones."""
    return [e for e in INVENTORY if e["defect"] == defect]


needs_field = pytest.mark.skipif(
    not have_scans(), reason="field-test scans not present")


def load(entry):
    """Pixels of one field exposure, in analysis polarity."""
    from phantom_qa import ingest
    return ingest.load_path(path_of(entry))[0]
