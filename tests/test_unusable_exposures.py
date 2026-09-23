"""Exposures that cannot be measured: saturated, clipped, or with no phantom.

`test_degraded_inputs.py` covers broken *files* — truncated uploads, corrupted
blobs, hostile parameters. This covers intact files holding a useless *image*,
which is what the field actually produced: a scan so over-ranged that the
phantom is a white slab, and one with the phantom hanging off the detector edge.

Two sets of fixtures, deliberately:

* **synthetic** — built with numpy, so these run everywhere, including where no
  scans exist. They reproduce every failure mode below.
* **field** — the real exposures from 2026-09-20, skipped when absent. They
  prove the synthetic cases resemble what a clinic produces.

Nothing here tunes anything. Thresholds belong to the HQ reference scans; see
the warning in field_scans.py and the guard tests at the end of this file.

Two tests are `xfail(strict=True)`. They describe defects found in the field
review and scheduled for repair, so they will start passing — strict makes that
announce itself rather than pass unnoticed, and the fix should delete the mark.
"""

import json
import os

import numpy as np
import pytest

import field_scans
from phantom_qa import pipeline
from phantom_qa.phantom_def import load_default
from test_registration import synth_square

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------ synthetic images

def _as_scan(pixels):
    """The minimum a ScanData needs to go through the pipeline."""
    class _Scan:
        pass
    s = _Scan()
    s.pixels = np.asarray(pixels, dtype=float)
    s.meta = {"Rows": s.pixels.shape[0], "Columns": s.pixels.shape[1]}
    s.spacing_candidates = {}
    s.kind = "image"
    s.reduced_precision = True
    s.shape = s.pixels.shape
    return s


def saturated_image():
    """A phantom driven past the top of the detector's range.

    Everything inside the outline lands on one value, so no pattern within it
    carries any signal — the state both 003 exposures came back in."""
    img, _ = synth_square(angle_deg=0.0)
    img[img > 600] = 4095.0
    return img


def clipped_image():
    """A phantom larger than the detector, so its edges leave the image."""
    img, _ = synth_square(angle_deg=0.0, side_px=1500, img=1400)
    return img


def flat_image():
    """No exposure at all: one constant value."""
    return np.full((1400, 1400), 500.0)


def noise_only_image():
    """Exposure with no phantom in the beam."""
    return np.random.default_rng(0).normal(500, 50, (1400, 1400))


SYNTHETIC = {
    "saturated": saturated_image,
    "clipped": clipped_image,
    "flat": flat_image,
    "noise_only": noise_only_image,
}


def analyse(scan, pdef):
    """Registration, proposal and measurement, exactly as the wizard does."""
    reg = pipeline.run_stage_a(scan, pdef)
    ctx = pipeline.build_ctx(scan, pdef, reg,
                             {"scan_meta": scan.meta, "sid_mm": 1000.0})
    geometry = pipeline.propose_all(ctx)
    return reg, geometry, pipeline.compute_all(ctx, geometry)


# --------------------------------------------------------------- measurements

def saturated_fraction(pixels) -> float:
    """Share of the image sitting on its own maximum."""
    px = np.asarray(pixels, float)
    return float(np.mean(px >= px.max() - 0.5))


def anisotropy(reg) -> float:
    """How far the fitted transform departs from a rigid one.

    A phantom whose edge leaves the detector has no outline to fit on that
    side, so the fit stretches along one axis. 1.0 is perfectly rigid."""
    sv = np.linalg.svd(reg.transform.A, compute_uv=False)
    return float(sv[0] / sv[1])


def statuses_worse_than_summary(results) -> list[str]:
    """Tests claiming 'pass' while one of their own rows says 'fail'."""
    bad = []
    for name, blob in results.items():
        if not isinstance(blob, dict):
            continue
        rows = blob.get("rows") or []
        row_states = {r.get("status") for r in rows if isinstance(r, dict)}
        if blob.get("status") == "pass" and "fail" in row_states:
            bad.append(f"{name}: summary 'pass' over rows {sorted(row_states)}")
    return bad


def null_lowcontrast_metrics(results) -> list[str]:
    """Discs reported without a contrast number.

    ``None`` here is what the results page calls .toFixed() on and what the
    printed report hands to the chart, so a row that exists must carry a
    number. A test that measured nothing should say so instead of listing
    eight empty discs."""
    lc = results.get("lowcontrast") or {}
    if lc.get("status") in ("not measured", "error"):
        return []
    return [f"{r.get('id')}.{k}" for r in lc.get("rows") or []
            for k in ("cnr", "abs_cnr") if r.get(k) is None]


# ---------------------------------------------------- the pipeline stays alive

@pytest.mark.parametrize("kind", sorted(SYNTHETIC))
def test_an_unusable_exposure_is_measured_without_raising(kind, pdef):
    """Whatever the image, the operator gets an answer rather than a traceback.

    This is the floor the 'stuck at Computing…' report sits on: the server did
    finish, so a failure has to arrive as a result, not as an exception."""
    reg, geometry, results = analyse(_as_scan(SYNTHETIC[kind]()), pdef)
    assert pipeline.overall_status(results) in ("pass", "n/a", "warn", "fail",
                                                "error")
    for test in ("geometry", "linepairs", "lowcontrast", "uniformity", "wedge"):
        assert test in results, f"{test} produced no entry at all"


@pytest.mark.parametrize("kind", sorted(SYNTHETIC))
def test_results_of_an_unusable_exposure_can_be_stored_and_sent(kind, pdef):
    """They must survive strict JSON: NaN is not valid JSON and a browser
    refuses to parse it."""
    _, _, results = analyse(_as_scan(SYNTHETIC[kind]()), pdef)
    json.dumps(results, allow_nan=False)


@pytest.mark.parametrize("kind", sorted(SYNTHETIC))
def test_an_unusable_exposure_never_passes_overall(kind, pdef):
    """Nothing measurable is in these images, so none may be reported good."""
    _, _, results = analyse(_as_scan(SYNTHETIC[kind]()), pdef)
    assert pipeline.overall_status(results) != "pass"


# ------------------------------------------- what the quality gate will key on

def test_saturation_separates_a_ruined_exposure_from_a_usable_one():
    """The discriminator the planned upload check uses, with its margin.

    Measured on the reference scans: none exceeds 0.07 % of pixels at the
    maximum. A ruined exposure is three orders of magnitude above that."""
    assert saturated_fraction(saturated_image()) > 0.20
    good, _ = synth_square(angle_deg=0.0)
    assert saturated_fraction(good) < 0.01


def test_a_phantom_off_the_detector_edge_shows_up_in_the_registration(pdef):
    """Clipping is visible without any knowledge of what the phantom contains.

    With one edge missing the affine fit stretches: reference scans sit between
    1.0009 and 1.0025, a clipped exposure well above 1.01."""
    reg_clipped = pipeline.run_stage_a(_as_scan(clipped_image()), pdef)
    reg_good = pipeline.run_stage_a(_as_scan(synth_square(angle_deg=0.0)[0]),
                                    pdef)
    assert anisotropy(reg_clipped) > 1.01
    assert anisotropy(reg_good) < 1.01


# ------------------------------------- an unmeasurable image says so honestly

@pytest.mark.parametrize("kind", sorted(SYNTHETIC))
def test_a_summary_status_is_never_better_than_its_own_rows(kind, pdef):
    """A saturated exposure used to report uniformity as **passed**.

    Every SNR was NaN, the worst deviation stayed 0.0 because `max(0.0, nan)`
    is 0.0, and the summary read "pass" while all five of its own rows read
    "fail". A summary may never be kinder than the rows underneath it."""
    _, _, results = analyse(_as_scan(SYNTHETIC[kind]()), pdef)
    assert not statuses_worse_than_summary(results)


@pytest.mark.parametrize("kind", sorted(SYNTHETIC))
def test_a_disc_is_never_reported_without_a_contrast_number(kind, pdef):
    """The null that stopped the results page and broke the printed report.

    A disc whose surroundings carry no noise has no CNR. It is now left out of
    the rows and named in `not_measured`, so nothing downstream is handed an
    empty number where it expects a value."""
    _, _, results = analyse(_as_scan(SYNTHETIC[kind]()), pdef)
    assert not null_lowcontrast_metrics(results)


@pytest.mark.parametrize("kind", sorted(SYNTHETIC))
def test_every_row_that_exists_carries_real_numbers(kind, pdef):
    """The contract the modules now keep, stated once for every test.

    Either a value was measured and the row holds a number, or it was not and
    the object is named in `not_measured` with the reason. Nothing in between,
    so no consumer has to guess what an empty cell means."""
    _, _, results = analyse(_as_scan(SYNTHETIC[kind]()), pdef)
    skip = {"id", "level", "status", "reason", "saturated", "linearity"}
    for name, blob in results.items():
        if not isinstance(blob, dict):
            continue
        for row in blob.get("rows") or []:
            empty = [k for k, v in row.items() if k not in skip and v is None]
            assert not empty, f"{name} row {row.get('id')} has empty {empty}"


@pytest.mark.parametrize("kind", sorted(SYNTHETIC))
def test_what_could_not_be_measured_is_named_and_explained(kind, pdef):
    """An operator has to be able to tell 'not measured' from 'measured zero'."""
    _, _, results = analyse(_as_scan(SYNTHETIC[kind]()), pdef)
    for name in ("uniformity", "lowcontrast"):
        blob = results.get(name) or {}
        for entry in blob.get("not_measured") or []:
            assert entry.get("id")
            assert entry.get("reason"), f"{name}/{entry['id']} gives no reason"
        if blob.get("not_measured"):
            assert blob.get("status") != "pass"
            assert blob.get("reasons"), f"{name} explains nothing to the operator"


# ------------------------------------------- the whole chain, through the app

def _png16(pixels) -> bytes:
    """A 16-bit greyscale PNG, so the image survives an upload intact."""
    import io as _io
    from PIL import Image
    arr = np.clip(np.asarray(pixels, float), 0, 65535).astype("uint16")
    buf = _io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")   # uint16 infers mode I;16
    return buf.getvalue()


@pytest.mark.parametrize("kind", ["saturated", "flat"])
def test_an_unusable_exposure_still_produces_a_printable_report(
        kind, tmp_path, monkeypatch):
    """The failure the field actually hit: "report shows internal server error".

    Analysis finished, the operator opened the report, and it answered 500 —
    a chart had been handed an empty contrast value. The whole chain is walked
    here, upload to printed report, because that is where it broke."""
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login

    mod = _build_app(tmp_path, monkeypatch)
    client = TestClient(mod.app)
    client.headers.update({"X-CSRF-Token": _login(client)})

    upload = client.post("/api/analyses",
                         files={"file": (f"{kind}.png", _png16(SYNTHETIC[kind]()))},
                         data={"site": "Test", "phantom": f"UNUSABLE-{kind}"})
    assert upload.status_code == 200, upload.text
    aid = upload.json()["analyses"][0]["id"]

    assert client.post(f"/api/analyses/{aid}/confirm",
                       json={"stage": "A"}).status_code == 200
    assert client.post(f"/api/analyses/{aid}/propose",
                       json={}).status_code == 200
    for stage in ("B", "C"):
        assert client.post(f"/api/analyses/{aid}/confirm",
                           json={"stage": stage,
                                 "save_profile": False}).status_code == 200

    computed = client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    assert computed.status_code == 200, computed.text
    assert computed.json()["overall"] != "pass"

    for path in (f"/api/analyses/{aid}/report.html",
                 f"/api/analyses/{aid}/export.csv",
                 f"/api/analyses/{aid}/export.json",
                 "/api/comparison_report.html?ids=" + aid,
                 "/api/trends"):
        answer = client.get(path)
        assert answer.status_code == 200, \
            f"{path} answered {answer.status_code}: {answer.text[:300]}"


def test_a_saved_layout_is_never_taken_from_an_unusable_exposure(
        tmp_path, monkeypatch):
    """Measuring points from a saturated image must not become the default.

    The field audit log shows this happening: the shared layout for one
    phantom was rewritten three times from exposures nothing could be measured
    in. Until the quality gate lands, the least that must hold is that a
    caller who declines to save a layout really does not save one."""
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login

    mod = _build_app(tmp_path, monkeypatch)
    client = TestClient(mod.app)
    client.headers.update({"X-CSRF-Token": _login(client)})

    upload = client.post(
        "/api/analyses",
        files={"file": ("saturated.png", _png16(saturated_image()))},
        data={"site": "Test", "phantom": "NO-LAYOUT-PLEASE"})
    aid = upload.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    client.post(f"/api/analyses/{aid}/confirm",
                json={"stage": "C", "save_profile": False})

    profiles = client.get("/api/phantom_profiles").json()
    stored = [p for p in profiles.get("profiles", profiles)
              if isinstance(p, dict) and p.get("phantom") == "NO-LAYOUT-PLEASE"]
    assert not stored, "a layout was stored from an unmeasurable exposure"


# --------------------------------------------------- the real field exposures

@field_scans.needs_field
@pytest.mark.parametrize("entry", [pytest.param(e, id=e["key"])
                                   for e in field_scans.INVENTORY])
def test_a_real_field_exposure_is_measured_without_raising(entry, pdef):
    scan = field_scans.load(entry)
    _, _, results = analyse(scan, pdef)
    json.dumps(results, allow_nan=False)
    assert pipeline.overall_status(results) != "pass" or entry["usable"]


@field_scans.needs_field
def test_the_field_exposures_are_the_ones_these_expectations_describe():
    """Pin the files, so a swapped drop cannot quietly change what is tested."""
    import hq_manifest
    for entry in field_scans.INVENTORY:
        got = hq_manifest.sha256_of(field_scans.path_of(entry))
        assert got == entry["sha256"], (
            f"{entry['key']} is not the exposure this fixture describes "
            f"({entry['note']})")


@field_scans.needs_field
def test_the_two_saturated_exposures_are_recognisable_as_such():
    for entry in field_scans.by_defect("saturated"):
        frac = saturated_fraction(field_scans.load(entry).pixels)
        assert frac > 0.20, f"{entry['key']}: only {frac:.1%} at the maximum"
    for entry in field_scans.by_defect(None):
        frac = saturated_fraction(field_scans.load(entry).pixels)
        assert frac < 0.05, f"{entry['key']}: {frac:.1%} at the maximum"


@field_scans.needs_field
def test_the_clipped_exposure_is_recognisable_from_its_registration(pdef):
    clipped = field_scans.by_defect("clipped")[0]
    a_bad = anisotropy(pipeline.run_stage_a(field_scans.load(clipped), pdef))
    assert a_bad > 1.01, f"clipping left no trace: anisotropy {a_bad:.4f}"
    for entry in field_scans.by_defect(None):
        a_ok = anisotropy(pipeline.run_stage_a(field_scans.load(entry), pdef))
        assert a_ok < 1.01, f"{entry['key']} looks clipped: {a_ok:.4f}"


@field_scans.needs_field
def test_the_two_exposures_sharing_a_name_are_different_images():
    """The pair behind 'different scans identified as same'.

    Same file name, same byte size, same PatientID — different exposures. The
    audit log shows the software always told them apart; the operators could
    not, because nothing in the interface distinguished them."""
    a, b = field_scans.by_defect("saturated")
    pa, pb = field_scans.path_of(a), field_scans.path_of(b)
    assert os.path.basename(pa) == os.path.basename(pb)
    assert os.path.getsize(pa) == os.path.getsize(pb)
    assert a["sha256"] != b["sha256"]
    assert not np.array_equal(field_scans.load(a).pixels,
                              field_scans.load(b).pixels)


# ------------------------------------------------------------- the guard rails

def _sources(*dirs):
    for d in dirs:
        for base, _, files in os.walk(os.path.join(REPO, d)):
            if "__pycache__" in base:
                continue
            for name in files:
                if name.endswith((".py", ".json")):
                    yield os.path.join(base, name)


#: Only these may name the field drop. Everything else measuring it would be a
#: route by which a broken exposure reaches reference data.
_MAY_USE_FIELD_SCANS = {"field_scans.py", "test_unusable_exposures.py",
                        # checks the acquisition gate against the real broken
                        # exposures; sets nothing from them
                        "test_quality_gate.py",
                        # checks that turning MONOCHROME1 images the right way
                        # up leaves the readable field scans identical to the
                        # last pixel; an equality check, it sets nothing
                        "test_monochrome1.py",
                        # checks that a packed upload of the field Fuji scans
                        # unpacks byte-identical; an equality check, it sets
                        # nothing
                        "test_upload_compression.py"}


def test_no_analysis_code_reads_the_field_scans():
    """The application must not know these files exist."""
    offenders = []
    for path in _sources("phantom_qa"):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if "Field testing" in text or "PHANTOMQA_FIELD_DIR" in text:
            offenders.append(os.path.relpath(path, REPO))
    assert not offenders, f"application code references the field drop: {offenders}"


def test_only_the_negative_fixtures_reach_for_the_field_scans():
    # Guard against passing vacuously: if the search string never appeared
    # anywhere, this test would look green while checking nothing.
    allowed = [os.path.join(REPO, "tests", n) for n in _MAY_USE_FIELD_SCANS]
    assert any("Field testing" in open(p, encoding="utf-8").read()
               for p in allowed), \
        "the allow-listed fixtures no longer name the field drop — is this " \
        "test still looking for the right thing?"

    offenders = []
    for path in _sources("tests"):
        if os.path.basename(path) in _MAY_USE_FIELD_SCANS:
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if "Field testing" in text or "PHANTOMQA_FIELD_DIR" in text \
                or "field_scans" in text:
            offenders.append(os.path.relpath(path, REPO))
    assert not offenders, (
        f"{offenders} use the field exposures. They are negative fixtures "
        f"only — tuning or reference work belongs to the HQ scans.")


def test_no_field_exposure_is_in_the_reference_benchmark():
    """The strongest guard available: by content, not by path.

    If a field exposure ever became reference material its hash would appear
    among the golden values."""
    import hq_manifest
    if not os.path.exists(hq_manifest.MANIFEST_PATH):
        pytest.skip("reference manifest not generated yet")
    reference = {s["sha256"] for s in hq_manifest.load_manifest()["scans"].values()}
    for entry in field_scans.INVENTORY:
        assert entry["sha256"] not in reference, (
            f"{entry['key']} ({entry['note']}) is in the reference benchmark")


def test_the_field_drop_is_not_searched_for_reference_or_sample_scans():
    """conftest and the benchmark must look somewhere else entirely."""
    import conftest
    import hq_manifest
    field = os.path.abspath(field_scans.FIELD_ROOT)
    assert os.path.abspath(hq_manifest.HQ_ROOT) != field
    assert os.path.abspath(conftest.SAMPLE_DIR) != field
    for entry in hq_manifest.INVENTORY:
        assert field not in os.path.abspath(hq_manifest.scan_path(entry))
    for sample in conftest.SAMPLES:
        assert field not in os.path.abspath(sample)


def test_the_field_inventory_describes_what_it_holds():
    keys = [e["key"] for e in field_scans.INVENTORY]
    assert len(keys) == len(set(keys)) == 5
    assert len(field_scans.by_defect("saturated")) == 2
    assert len(field_scans.by_defect("clipped")) == 1
    assert len(field_scans.by_defect(None)) == 2
    for entry in field_scans.INVENTORY:
        assert len(entry["sha256"]) == 64
        assert entry["note"]
        assert entry["usable"] is (entry["defect"] is None)
