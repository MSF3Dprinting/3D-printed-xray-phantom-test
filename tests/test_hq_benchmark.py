"""Regression benchmark against the HQ reference scans.

Everything else in the suite measures synthetic images or one or two samples.
This measures the real reference set — both detectors, both physical prints,
every orientation and the whole dose series — and compares every number against
committed golden values (tests/hq_benchmark.json).

It exists so that the planned work on registration, line-pair matching and
low-contrast detection can be judged by what it does to the reference scans
rather than by whether the unit tests still pass. Stored baselines out in the
field are expressed in exactly these numbers.

    normal run      six representative scans   (~40 s)
    full run        all 33                     PHANTOMQA_HQ_FULL=1  (~4 min)
    scans absent    skipped, loudly (see conftest's end-of-run summary)

A failure is not automatically a bug: if the change was intended, regenerate the
manifest with `python tests/hq_manifest.py --update` and review the diff — that
diff is the record of what the change did to real measurements.
"""

import json
import os

import pytest

import hq_manifest as hq

_FULL = os.environ.get("PHANTOMQA_HQ_FULL", "").strip().lower() \
    in ("1", "true", "yes", "on")

_HAVE_MANIFEST = os.path.exists(hq.MANIFEST_PATH)
_MANIFEST = hq.load_manifest() if _HAVE_MANIFEST else {"scans": {}}

needs_hq = pytest.mark.skipif(
    not _HAVE_MANIFEST or not hq.have_scans(),
    reason="HQ reference scans not present")


def _selected():
    entries = [e for e in hq.INVENTORY if _FULL or e["subset"]]
    return [pytest.param(e, id=e["key"]) for e in entries]


@pytest.fixture(scope="session")
def manifest():
    return _MANIFEST


# --------------------------------------------------------- the benchmark itself

@needs_hq
@pytest.mark.parametrize("entry", _selected())
def test_reference_scan_measures_the_same_as_before(entry, manifest, pdef):
    expected = manifest["scans"].get(entry["key"])
    assert expected, (
        f"{entry['key']} is in the inventory but not in the manifest — "
        f"regenerate with: python tests/hq_manifest.py --update")

    path = hq.scan_path(entry)
    assert hq.sha256_of(path) == expected["sha256"], (
        f"{entry['key']}: the file on disk is not the one the golden values "
        f"were measured from ({path}). Restore the original scan, or "
        f"regenerate the manifest if the drop was legitimately replaced.")

    actual = hq.measure(path, pdef)
    diffs = hq.compare(actual, expected)
    assert not diffs, (
        f"{entry['key']} ({entry['group']}, {entry['label']}) no longer "
        f"measures the same:\n  " + "\n  ".join(diffs)
        + "\n\nIf this change was intended, regenerate the manifest "
          "(python tests/hq_manifest.py --update) and review the diff.")


@needs_hq
def test_no_reference_scan_produces_nan_or_infinity(manifest):
    """A good scan must yield numbers all the way through.

    NaN is what makes the results page hang and the printed report fail on a
    saturated exposure; on reference-quality images it must never appear."""
    offenders = {k: v["nonfinite"] for k, v in manifest["scans"].items()
                 if v.get("nonfinite")}
    assert not offenders, f"non-finite values on reference scans: {offenders}"


# ------------------------------------------------------- the manifest is honest

def test_the_manifest_covers_the_whole_inventory():
    if not _HAVE_MANIFEST:
        pytest.skip("manifest not generated yet")
    missing = [e["key"] for e in hq.INVENTORY
               if e["key"] not in _MANIFEST["scans"]]
    extra = [k for k in _MANIFEST["scans"]
             if k not in {e["key"] for e in hq.INVENTORY}]
    assert not missing, f"scans in the inventory but not measured: {missing}"
    assert not extra, f"manifest holds scans that are not in the inventory: {extra}"


def test_the_leeds_test_object_is_excluded():
    """DICOM/000000 and friends are a commercial Leeds PIX-13, not this phantom.

    They must not reach the benchmark, and by extension must never be used to
    tune the phantom definition or set a baseline."""
    keys = {e["key"] for e in hq.INVENTORY}
    for i in sorted(hq.LEEDS_PIX13):
        assert f"CS{i:06d}" not in keys
        assert f"CS{i:06d}" not in _MANIFEST["scans"]
    assert len(hq.INVENTORY) == 33, "expected 6 Philips + 27 Carestream scans"


def test_the_subset_is_representative():
    """The cheap everyday selection must still span what can break."""
    if not _HAVE_MANIFEST:
        pytest.skip("manifest not generated yet")
    sub = [_MANIFEST["scans"][e["key"]] for e in hq.INVENTORY if e["subset"]
           if e["key"] in _MANIFEST["scans"]]
    assert {s["group"] for s in sub} == {"philips", "carestream"}
    assert any("licht" in s["label"] for s in sub), "no light-blue print"
    assert any("donker" in s["label"] for s in sub), "no dark-blue print"
    rotations = {round(s["registration"]["rotation_deg"] / 90.0) % 4 for s in sub}
    assert len(rotations) >= 2, f"subset covers only rotation(s) {rotations}"


def test_the_manifest_records_what_produced_it():
    """Golden numbers are meaningless without the versions behind them."""
    if not _HAVE_MANIFEST:
        pytest.skip("manifest not generated yet")
    from phantom_qa import ALGO_VERSION
    assert _MANIFEST["algo_version"] == ALGO_VERSION, (
        f"the manifest was measured with algorithm "
        f"{_MANIFEST['algo_version']}, the code is {ALGO_VERSION} — "
        f"regenerate it and review what the new version changed")
    assert _MANIFEST["environment"]["numpy"]
    assert _MANIFEST["tolerances"] == hq.TOL, (
        "tolerances changed in code but not in the manifest — regenerate it")


@pytest.mark.parametrize("what,mutate", [
    ("registration drift",
     lambda r: r["registration"].update(rotation_deg=r["registration"]["rotation_deg"] + 0.5)),
    ("scale drift",
     lambda r: r["registration"].update(mm_per_px=r["registration"]["mm_per_px"] * 1.01)),
    ("mirror flip",
     lambda r: r["registration"].update(mirrored=not r["registration"]["mirrored"])),
    ("a lost landmark",
     lambda r: r["registration"].update(landmarks_found=r["registration"]["landmarks_found"] - 1)),
    ("a status change",
     lambda r: r["status"].update(linepairs="pass" if r["status"]["linepairs"] != "pass" else "fail")),
    ("an ROI moved 1 mm",
     lambda r: r["roi_centers_mm"].__setitem__(
         next(iter(r["roi_centers_mm"])),
         [r["roi_centers_mm"][next(iter(r["roi_centers_mm"]))][0] + 1.0,
          r["roi_centers_mm"][next(iter(r["roi_centers_mm"]))][1]])),
    ("a CNR change",
     lambda r: r["metrics"]["lowcontrast_cnr"].update(
         {k: (v or 0) + 0.3 for k, v in list(r["metrics"]["lowcontrast_cnr"].items())[:1]})),
    ("the disc order flipping",
     lambda r: r["metrics"].update(
         lowcontrast_ordering_ok=not r["metrics"]["lowcontrast_ordering_ok"])),
    ("a line-pair pitch change",
     lambda r: r["metrics"]["linepairs_pitch_mm"].update(
         {k: (v + 0.05) for k, v in r["metrics"]["linepairs_pitch_mm"].items()
          if v is not None})),
    ("NaN appearing",
     lambda r: r.__setitem__("nonfinite", ["/lowcontrast/rows[0]/cnr"])),
])
def test_the_comparison_actually_detects_changes(what, mutate):
    """Guard the guard.

    A comparison that silently returns 'no differences' is worse than no
    benchmark at all, because it reads as proof that nothing moved. Each case
    below is a change an operator would notice in a report; every one must be
    reported."""
    if not _HAVE_MANIFEST:
        pytest.skip("manifest not generated yet")
    good = json.loads(json.dumps(_MANIFEST["scans"]["CS000004"]))
    assert hq.compare(good, good) == [], "an unchanged scan must compare clean"

    changed = json.loads(json.dumps(good))
    mutate(changed)
    assert hq.compare(changed, good), f"{what} went unnoticed"


def test_tolerances_allow_ordinary_floating_point_noise():
    """It must not cry wolf over the last decimal place."""
    if not _HAVE_MANIFEST:
        pytest.skip("manifest not generated yet")
    good = json.loads(json.dumps(_MANIFEST["scans"]["CS000004"]))
    jittered = json.loads(json.dumps(good))
    jittered["registration"]["mm_per_px"] *= 1 + 1e-7
    jittered["registration"]["rotation_deg"] += 1e-4
    first = next(iter(jittered["roi_centers_mm"]))
    jittered["roi_centers_mm"][first][0] += 1e-4
    assert hq.compare(jittered, good) == []


def test_the_manifest_carries_no_identifying_tags():
    """It is committed, so it must hold derived numbers only."""
    if not _HAVE_MANIFEST:
        pytest.skip("manifest not generated yet")
    text = json.dumps(_MANIFEST).lower()
    for tag in ("patientname", "patientid", "stationname", "institution",
                "deviceserial", "accession", "msf^phantom", "operator"):
        assert tag not in text, f"manifest leaks {tag}"
