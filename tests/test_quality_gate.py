"""The acquisition-quality gate: can this exposure be measured at all?

Two things have to be true of a gate like this, and they pull against each
other. It must catch the exposures the field actually produced — an over-ranged
image in which the phantom is a white slab, one with the phantom hanging off
the detector edge. And it must not fire on a single good scan, because a gate
that cries wolf is one operators learn to click past.

So the limits are checked here against the whole reference set, not against a
couple of examples, and the margins are asserted rather than assumed. The
reference scans also carry their measured gate values in the benchmark
manifest, so tightening a limit shows up as a diff listing every scan it would
newly reject.

What the gate does NOT do is stop the analysis. An operator may need the
numbers from a bad exposure to show what went wrong. It stops one thing: a
broken exposure becoming the reference other scans are judged against, or
supplying the measuring points the next operator starts from.
"""

import os

import numpy as np
import pytest

import field_scans
import hq_manifest
from phantom_qa import pipeline, quality
from test_unusable_exposures import (SYNTHETIC, _as_scan, _png16,
                                     clipped_image, saturated_image)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _assess(pixels, pdef):
    scan = _as_scan(pixels)
    return quality.assess(scan.pixels, pipeline.run_stage_a(scan, pdef))


# ------------------------------------------------------ it catches the bad ones

@pytest.mark.parametrize("kind", ["saturated", "flat", "clipped"])
def test_an_unusable_exposure_is_refused(kind, pdef):
    q = _assess(SYNTHETIC[kind](), pdef)
    assert q["verdict"] == "poor", f"{kind} passed the gate: {q}"
    assert q["failed"], "a refusal must name the checks that failed"
    assert q["summary"] and "repeat" in q["summary"].lower(), \
        "the operator has to be told what to do about it"


def test_a_refusal_names_what_was_measured_and_the_limit(pdef):
    """A refusal nobody can interpret is a refusal people work around."""
    q = _assess(saturated_image(), pdef)
    for check in q["checks"]:
        assert check["label"] and check["detail"]
        assert check["limit"] is not None
    failed = [c for c in q["checks"] if not c["ok"]]
    assert any(c["id"] == "saturation" for c in failed)


@pytest.mark.parametrize("kind", sorted(SYNTHETIC))
def test_the_verdict_can_always_be_stored_and_sent(kind, pdef):
    """Every value has to survive JSON, whatever the image was.

    It did not: a scan offering only one placement candidate produced an
    infinite "lead over the runner-up", and the whole upload response then
    failed to serialise with "Out of range float values are not JSON
    compliant" — an upload broken by the very check meant to protect it."""
    import json
    q = _assess(SYNTHETIC[kind](), pdef)
    json.dumps(q, allow_nan=False)
    for check in q["checks"]:
        assert check["value"] is None or np.isfinite(check["value"]), check


def test_a_measurement_that_does_not_exist_is_absent_not_infinite(pdef):
    """One placement candidate means no ambiguity, not unbounded confidence."""
    scan = _as_scan(SYNTHETIC["saturated"]())
    reg = pipeline.run_stage_a(scan, pdef)
    reg.candidate_scores = [{"total": 7.0}]        # a single candidate
    q = quality.assess(scan.pixels, reg)
    margin = next(c for c in q["checks"] if c["id"] == "placement_margin")
    assert margin["value"] is None
    assert margin["ok"] is True, "nothing to be ambiguous against"
    assert "only one placement" in margin["detail"]


def test_each_failure_mode_is_caught_by_more_than_one_check(pdef):
    """No single signal has to be perfect.

    Saturation and lost ruler lines both catch an over-ranged exposure;
    stretched registration and a thin placement margin both catch a clipped
    one. One check drifting cannot open the gate on its own."""
    assert len(_assess(saturated_image(), pdef)["failed"]) >= 2
    assert len(_assess(clipped_image(), pdef)["failed"]) >= 2


def test_saturation_is_measured_against_the_images_own_maximum():
    """Vendor processing rescales, so the detector's full scale is no guide."""
    assert quality.saturated_fraction(np.full((50, 50), 700.0)) == 1.0
    mixed = np.zeros((10, 10)); mixed[:3] = 4095.0
    assert quality.saturated_fraction(mixed) == pytest.approx(0.30)
    assert quality.saturated_fraction(np.array([[]])) == 1.0


# ------------------------------------------------- it leaves the good ones alone

@pytest.mark.skipif(not os.path.exists(hq_manifest.MANIFEST_PATH),
                    reason="reference manifest not generated yet")
def test_no_reference_scan_is_refused():
    """33 scans, two detectors, two prints, every orientation, full dose series."""
    manifest = hq_manifest.load_manifest()
    refused = {k: s["quality"]["failed"] for k, s in manifest["scans"].items()
               if s.get("quality", {}).get("verdict") != "ok"}
    assert not refused, f"the gate rejects reference scans: {refused}"


@pytest.mark.skipif(not os.path.exists(hq_manifest.MANIFEST_PATH),
                    reason="reference manifest not generated yet")
def test_every_limit_keeps_a_real_margin_over_the_reference_scans():
    """Not merely "no reference scan fails" — each limit has room to spare.

    A limit that only just clears the worst reference scan would start firing
    on the next detector the phantom meets."""
    manifest = hq_manifest.load_manifest()
    values = {}
    for scan in manifest["scans"].values():
        for cid, v in (scan.get("quality") or {}).get("values", {}).items():
            values.setdefault(cid, []).append(v)

    t = quality.THRESHOLDS
    worst_sat = max(values["saturation"])
    assert worst_sat < t["saturated_fraction_max"] / 10, (
        f"saturation limit {t['saturated_fraction_max']} is close to the worst "
        f"reference scan ({worst_sat})")

    worst_aniso = max(values["clipping"])
    assert worst_aniso - 1 < (t["anisotropy_max"] - 1) / 2, (
        f"clipping limit leaves too little room: worst reference {worst_aniso}")

    assert min(values["landmarks"]) >= t["landmarks_min"] + 1, \
        "every reference scan should find all four ruler lines"
    assert min(values["recognition"]) > t["score_min"] * 1.2
    assert min(values["placement_margin"]) > t["score_margin_min"] * 2


@field_scans.needs_field
@pytest.mark.parametrize("entry", [pytest.param(e, id=e["key"])
                                   for e in field_scans.INVENTORY])
def test_the_gate_agrees_with_what_the_field_exposures_actually_are(entry, pdef):
    """The five real exposures, three of them knowingly broken.

    These confirm the gate; they never set it. Every limit comes from the
    reference scans."""
    scan = field_scans.load(entry)
    q = quality.assess(scan.pixels, pipeline.run_stage_a(scan, pdef))
    expected = "ok" if entry["usable"] else "poor"
    assert q["verdict"] == expected, (
        f"{entry['key']} ({entry['note']}) judged {q['verdict']}: {q['failed']}")


# --------------------------------------------- what a refusal actually prevents

def _analyse_through_the_app(client, pixels, phantom, name="scan.png"):
    up = client.post("/api/analyses",
                     files={"file": (name, _png16(pixels))},
                     data={"site": "Test", "phantom": phantom})
    assert up.status_code == 200, up.text
    aid = up.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    confirmed = client.post(f"/api/analyses/{aid}/confirm", json={"stage": "C"})
    computed = client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    return aid, up.json()["analyses"][0], confirmed.json(), computed


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


def test_a_refused_exposure_can_still_be_analysed(client):
    """The gate withholds trust, not function."""
    aid, created, _, computed = _analyse_through_the_app(
        client, saturated_image(), "GATE-ANALYSE")
    assert created["quality"]["verdict"] == "poor"
    assert computed.status_code == 200
    assert client.get(f"/api/analyses/{aid}/report.html").status_code == 200
    assert client.get(f"/api/analyses/{aid}/export.csv").status_code == 200


def test_a_refused_exposure_cannot_become_the_reference_scan(client):
    aid, _, _, _ = _analyse_through_the_app(
        client, saturated_image(), "GATE-BASELINE")
    answer = client.post(f"/api/analyses/{aid}/baseline",
                         json={"is_baseline": True})
    assert answer.status_code == 409, answer.text
    assert "quality" in answer.json()["detail"].lower()
    assert client.get("/api/baselines").json()["baselines"] == []


def test_a_refused_exposure_cannot_set_the_phantoms_measuring_points(client):
    """The one that was happening in the field.

    The audit log shows a phantom's shared layout rewritten three times in
    twelve minutes from exposures nothing could be measured in — so every
    later scan of that phantom started from marks taken off a white slab."""
    _, _, confirmed, _ = _analyse_through_the_app(
        client, saturated_image(), "GATE-LAYOUT")
    assert confirmed["profile_saved"] is False
    assert confirmed["profile_blocked_by_quality"] is True
    assert confirmed["profile_error"]
    stored = [p for p in client.get("/api/phantom_profiles").json()["profiles"]
              if "GATE-LAYOUT" in p["phantom"].upper()]
    assert not stored, "a layout was stored from an unusable exposure"


def test_the_verdict_travels_with_the_record(client):
    """History needs a badge; the record needs the detail behind it."""
    aid, _, _, _ = _analyse_through_the_app(
        client, saturated_image(), "GATE-TRAVEL")
    listed = client.get("/api/analyses").json()["analyses"][0]
    assert listed["quality_verdict"] == "poor", \
        "History cannot badge a row it is not told about"
    assert "checks" not in listed, \
        "the listing must stay small — detail belongs in the record"

    record = client.get(f"/api/analyses/{aid}").json()
    assert record["quality"]["verdict"] == "poor"
    assert [c for c in record["quality"]["checks"] if not c["ok"]]


def test_a_record_from_before_the_gate_reads_as_unassessed(client):
    """Never as a pass it was never given — and never as a refusal either.

    Records analysed before this existed carry no verdict. They must go on
    behaving exactly as they did: the gate judges what it has measured, and
    says nothing about what it has not."""
    aid, _, _, _ = _analyse_through_the_app(
        client, saturated_image(), "GATE-LEGACY")
    client.mod.store.update(aid, quality=None, quality_verdict="")

    record = client.get(f"/api/analyses/{aid}").json()
    assert record["quality"] is None
    assert not quality.blocks_reference_use(record["quality"])

    # Whatever happens to a legacy record, it is not the gate refusing it.
    # (A plain image is refused as a reference for an unrelated reason — it is
    # 8-bit and carries no acquisition metadata — so only 409 is diagnostic.)
    answer = client.post(f"/api/analyses/{aid}/baseline",
                         json={"is_baseline": True})
    assert answer.status_code != 409, answer.text

    confirmed = client.post(f"/api/analyses/{aid}/confirm",
                            json={"stage": "C"}).json()
    assert not confirmed.get("profile_blocked_by_quality"), \
        "an unassessed record must not be blocked from storing its layout"
    assert confirmed["profile_saved"] is True
