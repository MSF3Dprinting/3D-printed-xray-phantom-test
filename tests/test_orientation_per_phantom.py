"""Which way round the low-contrast insert is fitted, kept per phantom.

Two builds of the phantom are in use, and they differ in one way that matters
here: on the dark-blue print the eight-disc insert is fitted a half turn round,
which swaps the reading of disc L(i) with L(i+4). Until now that was decided
afresh from every scan's contrast order. It is right on 32 of the 33 reference
scans; the 33rd is so weak that no disc reaches |CNR| 0.6 and the order cannot
decide it at all.

The orientation is a property of the phantom, not of the exposure, so it is now
kept with the phantom's measuring points — saved only through the explicit
"use these measuring points for future scans" choice, never from an exposure
that failed the quality gate — and replayed onto later scans of that phantom.
A scan that confidently disagrees with the kept value is flagged, not obeyed:
the likeliest cause is a scan filed under the wrong phantom ID. And the
operator can change it by hand in the marking step, as an ordinary undoable
edit that reaches the phantom only when the measuring points are saved.

Layouts saved before any of this carry no orientation and behave exactly as
before: each scan decides for itself.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from phantom_qa import layout_profile, phantom_def
from phantom_qa.analysis import lowcontrast as LC
from phantom_qa.analysis.common import Ctx
from phantom_qa.registration import Transform
from test_unusable_exposures import SYNTHETIC, _png16

HERE = os.path.dirname(os.path.abspath(__file__))
PDEF = phantom_def.load_default()
NOMINAL = float(PDEF.lowcontrast["angle_deg"])
CENTRE = list(PDEF.lowcontrast["center_mm"])

_variant = iter(range(40_000, 49_000))


# ------------------------------------------------------ a phantom in numpy

def _disc_scan(depth_by_position, angle=NOMINAL, seed=5):
    """A block whose disc at drawn position L(i) is `depth_by_position[i-1]`
    darker than its surround, with the eight rings placed at `angle`.

    Deeper means more contrast, so a list rising with i is the build fitted
    as drawn, and one rising from position 5 is the build turned half round.
    Placing the rings at the drawn angle + 180 is what "Turn 180°" does."""
    size = 900
    T = Transform(A=np.array([[4.0, 0.0], [0.0, 4.0]]),
                  t=np.array([size / 2, size / 2]))
    img = np.random.default_rng(seed).normal(1000.0, 4.0, (size, size))
    ctx = Ctx(pixels=img, T=T, pdef=PDEF, reg=None, params={})
    yy, xx = np.mgrid[0:size, 0:size]
    for c, depth in zip(LC.circles_for_block(ctx, CENTRE, NOMINAL),
                        depth_by_position):
        cx, cy = c["full_circle"]["center_px"]
        r = c["full_circle"]["radius_px"]
        img[(xx - cx) ** 2 + (yy - cy) ** 2 < r * r] -= depth
    geometry = {"angle_deg": angle, "grid_shift_mm": [0.0, 0.0],
                "circles": LC.circles_for_block(ctx, CENTRE, angle)}
    return ctx, geometry


AS_DRAWN = [2, 3, 4, 5, 6, 7, 8, 9]           # contrast rises L1 -> L8
TURNED = AS_DRAWN[4:] + AS_DRAWN[:4]          # the dark-blue build
TOO_WEAK = [3, 3.1, 3, 3.1, 3, 3.1, 3, 3.1]   # no order to read


def _kept(flipped, source="stored"):
    return {"flipped": flipped, "source": source, "confidence": None}


def _disc_of(result, roi_id):
    """The design level a ring was read as, measured or not."""
    for r in result["rows"] + result["not_measured"]:
        if r["id"] == roi_id:
            return r["disc"]
    raise KeyError(roi_id)


# ------------------------------------------------ reading with a kept value

def test_a_weak_scan_of_a_phantom_with_a_saved_orientation_is_read_that_way():
    """The case the whole feature exists for.

    On its own this exposure cannot say which build it is, and used to be
    read as drawn on a coin toss. With the phantom's saved orientation it is
    read the way the phantom is actually built."""
    ctx, geom = _disc_scan(TOO_WEAK)
    alone = LC.compute(ctx, geom)
    assert alone["orientation"]["source"] == "undetermined"

    geom["orientation"] = _kept(True)
    res = LC.compute(ctx, geom)
    assert res["orientation"]["source"] == "stored"
    assert res["orientation"]["flipped"] is True
    assert not res["orientation"].get("conflict"), \
        "a scan that cannot tell must not be said to disagree"
    assert _disc_of(res, "L1") == "L5" and _disc_of(res, "L5") == "L1"


def test_the_measured_numbers_do_not_change_only_their_names():
    """Which disc is which is the only thing a kept orientation may touch.

    The CNR of every ring is keyed on its position (its ROI id) in every
    baseline and trend, so it has to be the same number either way."""
    ctx, geom = _disc_scan(TURNED)
    measured = {r["id"]: r["cnr"] for r in LC.compute(ctx, geom)["rows"]}
    geom["orientation"] = _kept(True)
    kept = {r["id"]: r["cnr"] for r in LC.compute(ctx, geom)["rows"]}
    assert kept == measured


def test_a_scan_that_contradicts_the_saved_value_is_flagged_not_obeyed():
    """The phantom ID is typed by hand. A light-print scan filed under a
    dark-print ID would otherwise quietly re-read every disc; the kept value
    stands and the operator is told to check the ID."""
    ctx, geom = _disc_scan(AS_DRAWN)
    geom["orientation"] = _kept(True)
    res = LC.compute(ctx, geom)
    o = res["orientation"]
    assert o["flipped"] is True and o["source"] == "stored", \
        "the saved value must be kept, not overruled by the scan"
    assert o["conflict"] is True
    assert LC.ORIENTATION_CONFLICT == (
        "The discs' contrast order looks like the other build of this phantom "
        "(insert turned the other way). Check that the phantom ID is right.")
    assert LC.ORIENTATION_CONFLICT in res["reasons"]
    assert res["status"] == "warn"
    # ...and the order warning does not send the operator to move a block
    # that is exactly where it should be.
    assert not any("reposition the block" in r for r in res["reasons"])


def test_a_hand_set_value_that_the_scan_contradicts_is_flagged_too():
    ctx, geom = _disc_scan(TURNED)
    geom["orientation"] = _kept(False, source="user")
    res = LC.compute(ctx, geom)
    assert res["orientation"]["source"] == "user"
    assert res["orientation"]["conflict"] is True
    assert any("set by hand" in r for r in res["reasons"])


def test_a_saved_value_the_scan_agrees_with_raises_nothing():
    ctx, geom = _disc_scan(TURNED)
    geom["orientation"] = _kept(True)
    res = LC.compute(ctx, geom)
    assert not res["orientation"].get("conflict")
    assert res["status"] == "pass"
    assert any("saved for this phantom" in r for r in res["reasons"]), \
        "a reading that did not come from this exposure has to say so"


def test_without_a_kept_value_each_scan_decides_as_before():
    """What every scan did until now, and what a layout saved before this
    change still gets. Including a reading noted when the patterns were
    proposed: that one is this scan's own and is always taken again, because
    the rings may have been moved since."""
    ctx, geom = _disc_scan(TURNED)
    plain = LC.compute(ctx, geom)
    expected = LC._resolve_orientation(plain["rows"])
    assert plain["orientation"]["flipped"] == expected["flipped"] is True
    assert plain["orientation"]["source"] == "measured"

    geom["orientation"] = {"flipped": False, "source": "measured",
                           "confidence": 1.5}
    again = LC.compute(ctx, geom)
    assert again["orientation"]["flipped"] is True, \
        "a stale propose-time reading overruled the scan"
    assert [r["disc"] for r in again["rows"]] == \
        [r["disc"] for r in plain["rows"]]


# ----------------------------------------- the "Turn 180°" button, alongside

@pytest.mark.parametrize("kept", [None, True])
def test_turning_the_rings_can_neither_undo_nor_double_the_correction(kept):
    """Turn 180° moves every ring onto the disc opposite; the insert setting
    renames the discs. Kept independent, pressing one after the other must
    leave every disc named by the design level it really carries.

    On the dark-blue build the disc designed as L1 sits at drawn position 5.
    Wherever the rings are, the ring on that disc must be read as L1."""
    for angle in (NOMINAL, NOMINAL + 180.0):
        ctx, geom = _disc_scan(TURNED, angle=angle)
        if kept is not None:
            geom["orientation"] = _kept(kept)
        res = LC.compute(ctx, geom)
        o = res["orientation"]
        assert o["flipped"] is True, "the insert itself did not turn"
        assert not o.get("conflict"), f"double correction at {angle}°"
        assert res["ordering_ok"], f"the discs came out misnamed at {angle}°"
        assert bool(o.get("rings_turned")) == (angle != NOMINAL)


def test_detection_never_turns_the_rings_by_itself():
    """Only the button, or a layout saved after it was pressed, puts the block
    more than a quarter turn from the drawing."""
    ctx, _ = _disc_scan(AS_DRAWN)
    assert LC.rings_turned(ctx, {"angle_deg": NOMINAL + 20}) is False
    assert LC.rings_turned(ctx, {"angle_deg": NOMINAL + 180}) is True
    assert LC.rings_turned(ctx, {"angle_deg": NOMINAL - 175}) is True
    assert LC.rings_turned(ctx, {}) is False


def test_the_pattern_proposal_already_says_which_way_round():
    """So the marking step can show it before anything is measured."""
    ctx, _ = _disc_scan(TURNED)
    geom = LC.propose(ctx)
    assert geom["orientation"] == {"flipped": True, "source": "measured",
                                   "confidence": geom["orientation"]["confidence"]}
    assert geom["orientation"]["confidence"] >= LC.ORIENTATION_MARGIN


# ------------------------------------------------------ the stored layout

def _layout_geometry(ctx):
    from phantom_qa.analysis.common import rect_roi
    return {"lowcontrast": {
        "angle_deg": NOMINAL, "grid_shift_mm": [0.0, 0.0],
        "bg_ring_mm": [12.0, 16.0],
        "block": rect_roi(ctx, CENTRE, (84.5, 44.45), NOMINAL,
                          "lowcontrast/block"),
        "circles": LC.circles_for_block(ctx, CENTRE, NOMINAL),
        "orientation": _kept(True, source="user")}}


def test_the_layout_records_a_decision_and_replays_it_as_kept():
    ctx, _ = _disc_scan(AS_DRAWN)
    layout = layout_profile.extract_layout(
        _layout_geometry(ctx), lowcontrast_insert={"flipped": True})
    assert layout["lowcontrast_insert"] == {"flipped": True}

    fresh = _layout_geometry(ctx)
    fresh["lowcontrast"]["orientation"] = {"flipped": False,
                                           "source": "measured",
                                           "confidence": 1.5}
    out, report = layout_profile.apply_layout(ctx, fresh, layout)
    assert out["lowcontrast"]["orientation"] == {
        "flipped": True, "source": "stored", "confidence": None}
    assert report["insert_turned"] is True


def test_a_layout_saved_before_this_replays_exactly_as_before():
    """No key, no change: the scan's own reading stays in charge."""
    ctx, _ = _disc_scan(AS_DRAWN)
    old = layout_profile.extract_layout(_layout_geometry(ctx))
    assert "lowcontrast_insert" not in old, \
        "an orientation was stored that nobody decided"
    fresh = _layout_geometry(ctx)
    own = {"flipped": False, "source": "undetermined", "confidence": 0.1}
    fresh["lowcontrast"]["orientation"] = dict(own)
    out, report = layout_profile.apply_layout(ctx, fresh, old)
    assert out["lowcontrast"]["orientation"] == own
    assert "insert_turned" not in report


@pytest.mark.parametrize("reading,previous,stored", [
    ({"flipped": True, "source": "measured"}, None, {"flipped": True}),
    ({"flipped": False, "source": "user"}, {"flipped": True},
     {"flipped": False}),
    ({"flipped": True, "source": "stored"}, {"flipped": True},
     {"flipped": True}),
    # a scan that could not tell must not erase what a good one established
    ({"flipped": False, "source": "undetermined"}, {"flipped": True},
     {"flipped": True}),
    ({"flipped": False, "source": "undetermined"}, None, None),
    (None, None, None),
])
def test_what_a_saved_layout_records(reading, previous, stored):
    prev_layout = {"lowcontrast_insert": previous} if previous else {}
    assert layout_profile.insert_to_store(reading, prev_layout) == stored


# ------------------------------------------------------------ over HTTP

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


def _proposed(client, phantom, usable=True):
    """An analysis with measuring points, on a synthetic exposure.

    The synthetic exposure is saturated, so the quality gate refuses it as
    reference data. `usable` clears the verdict the way a record from before
    the gate reads, which the gate never refuses — that is what lets these
    tests exercise the saving path without a real scan."""
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses", files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": phantom})
    assert up.status_code == 200, up.text
    aid = up.json()["analyses"][0]["id"]
    if usable:
        client.mod.store.update(aid, quality=None, quality_verdict="")
    assert client.post(f"/api/analyses/{aid}/confirm",
                       json={"stage": "A"}).status_code == 200
    r = client.post(f"/api/analyses/{aid}/propose", json={})
    assert r.status_code == 200, r.text
    return aid, r.json()


def _seq(client, aid):
    return client.get(f"/api/analyses/{aid}").json()["history"]["seq"]


def _set(client, aid, flipped, expect_seq=None):
    return client.post(
        f"/api/analyses/{aid}/lowcontrast_orientation",
        json={"flipped": flipped,
              "expect_seq": _seq(client, aid) if expect_seq is None
              else expect_seq})


def _stored_insert(client, phantom):
    prof = client.mod.store.get_phantom_profile(phantom)
    return None if prof is None else prof["layout"].get("lowcontrast_insert")


def test_it_reaches_the_phantom_only_through_the_explicit_save(client):
    """Setting it, and even confirming the step while declining to save the
    measuring points, leaves the phantom alone. Only the save stores it."""
    aid, _ = _proposed(client, "ORIENT-SAVE")
    assert _set(client, aid, True).status_code == 200
    assert client.mod.store.get_phantom_profile("ORIENT-SAVE") is None

    declined = client.post(f"/api/analyses/{aid}/confirm",
                           json={"stage": "C", "save_profile": False}).json()
    assert declined["profile_saved"] is False
    assert client.mod.store.get_phantom_profile("ORIENT-SAVE") is None

    saved = client.post(f"/api/analyses/{aid}/confirm",
                        json={"stage": "C", "save_profile": True}).json()
    assert saved["profile_saved"] is True
    assert saved["profile"]["insert_turned"] is True
    assert _stored_insert(client, "ORIENT-SAVE") == {"flipped": True}


def test_the_next_scan_of_that_phantom_is_read_with_it(client):
    first, _ = _proposed(client, "ORIENT-REPLAY")
    _set(client, first, True)
    client.post(f"/api/analyses/{first}/confirm", json={"stage": "C"})

    second, proposed = _proposed(client, "ORIENT-REPLAY")
    assert proposed["profile_applied"] is True, proposed.get("profile_check")
    assert proposed["geometry"]["lowcontrast"]["orientation"] == {
        "flipped": True, "source": "stored", "confidence": None}

    # The close-up says so before anything is measured...
    view = client.get(f"/api/analyses/{second}/lowcontrast_view").json()
    assert view["orientation"]["source"] == "stored"
    assert view["orientation"]["flipped"] is True

    # ...and the results are read that way. This exposure carries no signal,
    # so on its own it could never have decided.
    client.post(f"/api/analyses/{second}/confirm",
                json={"stage": "C", "save_profile": False})
    res = client.post(f"/api/analyses/{second}/compute",
                      json={"sid_mm": 1000.0}).json()["results"]["lowcontrast"]
    assert res["orientation"]["source"] == "stored"
    assert _disc_of(res, "L1") == "L5"


def test_another_phantom_is_not_read_with_it(client):
    first, _ = _proposed(client, "ORIENT-A")
    _set(client, first, True)
    client.post(f"/api/analyses/{first}/confirm", json={"stage": "C"})
    _, proposed = _proposed(client, "ORIENT-B")
    assert proposed["geometry"]["lowcontrast"]["orientation"]["source"] \
        != "stored"


def test_a_scan_failing_the_quality_gate_does_not_save_it(client):
    """The existing refusal covers the orientation too: a white slab must not
    decide how every later scan of the phantom is read."""
    aid, _ = _proposed(client, "ORIENT-GATE", usable=False)
    assert _set(client, aid, True).status_code == 200
    confirmed = client.post(f"/api/analyses/{aid}/confirm",
                            json={"stage": "C"}).json()
    assert confirmed["profile_saved"] is False
    assert confirmed["profile_blocked_by_quality"] is True
    assert client.mod.store.get_phantom_profile("ORIENT-GATE") is None


def test_a_profile_saved_without_an_orientation_lets_each_scan_decide(client):
    """A layout stored from a scan that could not tell — or before this change
    — carries none, and later scans read their own contrast as they always
    did."""
    first, _ = _proposed(client, "ORIENT-OLD")
    client.post(f"/api/analyses/{first}/confirm", json={"stage": "C"})
    assert client.mod.store.get_phantom_profile("ORIENT-OLD") is not None
    assert _stored_insert(client, "ORIENT-OLD") is None

    second, proposed = _proposed(client, "ORIENT-OLD")
    assert proposed["profile_applied"] is True
    assert proposed["geometry"]["lowcontrast"]["orientation"]["source"] \
        == "undetermined"


def test_a_weak_scan_does_not_erase_a_saved_orientation(client):
    """Re-saving the measuring points from a scan that cannot tell keeps what
    the phantom already had."""
    first, _ = _proposed(client, "ORIENT-KEEP")
    _set(client, first, True)
    client.post(f"/api/analyses/{first}/confirm", json={"stage": "C"})

    second, _ = _proposed(client, "ORIENT-KEEP", usable=True)
    # Back to this scan's own reading, which is undetermined.
    client.post(f"/api/analyses/{second}/geometry/reset", json={"to": "auto"})
    # Ticked on purpose: the phantom already has a layout from the first scan,
    # so by default this one would not be stored at all, and the test would
    # pass without anything having been re-saved.
    resaved = client.post(f"/api/analyses/{second}/confirm",
                          json={"stage": "C", "save_profile": True}).json()
    assert resaved["profile_saved"] is True
    assert _stored_insert(client, "ORIENT-KEEP") == {"flipped": True}


# ------------------------------------------------ changing it by hand

def test_changing_it_by_hand_is_an_ordinary_undoable_edit(client):
    aid, proposed = _proposed(client, "ORIENT-EDIT")
    before = proposed["geometry"]["lowcontrast"]["orientation"]
    seq = _seq(client, aid)

    r = _set(client, aid, True, expect_seq=seq)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["orientation"] == {"flipped": True, "source": "user",
                                   "confidence": None}
    assert body["view"]["source"] == "user"
    assert body["history"]["seq"] > seq and body["history"]["undo_depth"] >= 1

    rec = client.mod.store.get(aid)
    assert rec["geometry"]["lowcontrast"]["orientation"]["source"] == "user"
    assert any(e["action"] == "low-contrast insert orientation set"
               for e in rec["audit"]), "the change left no trace"

    undone = client.post(f"/api/analyses/{aid}/geometry/undo", json={})
    assert undone.status_code == 200
    assert undone.json()["geometry"]["lowcontrast"]["orientation"] == before


def test_changing_it_drops_results_that_were_read_the_other_way(client):
    """The numbers do not move, but their names do; a report whose disc names
    came from the old setting must not survive the change."""
    aid, _ = _proposed(client, "ORIENT-RESULTS")
    client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    assert client.mod.store.get(aid)["results"] is not None
    _set(client, aid, True)
    assert client.mod.store.get(aid)["results"] is None


def test_a_change_made_on_a_stale_state_is_refused(client):
    """Under one shared login two operators can hold the same analysis."""
    aid, _ = _proposed(client, "ORIENT-STALE")
    seq = _seq(client, aid)
    client.post(f"/api/analyses/{aid}/lowcontrast_block",
                json={"angle_deg": NOMINAL + 1.0, "expect_seq": seq})
    late = _set(client, aid, True, expect_seq=seq)
    assert late.status_code == 409
    assert late.json()["stale_geometry"] is True
    assert client.mod.store.get(aid)["geometry"]["lowcontrast"][
        "orientation"]["source"] != "user"


@pytest.mark.parametrize("lock", ["finalised", "signed off"])
def test_a_locked_analysis_cannot_be_changed(client, lock):
    aid, _ = _proposed(client, f"ORIENT-LOCK-{lock[:3]}")
    if lock == "finalised":
        client.mod.store.set_finalized(aid, "qa")
    else:
        client.mod.store.set_validation(aid, "validated", "Dr Test")
    r = _set(client, aid, True)
    assert r.status_code == 409, r.text
    assert client.mod.store.get(aid)["geometry"]["lowcontrast"][
        "orientation"]["source"] != "user"


def test_asking_before_the_points_exist_is_a_plain_refusal(client):
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses", files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": "ORIENT-EARLY"})
    aid = up.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    r = client.post(f"/api/analyses/{aid}/lowcontrast_orientation",
                    json={"flipped": True})
    assert r.status_code == 400, r.text


def test_past_analyses_are_never_rewritten(client):
    """Saving a new orientation for the phantom changes how LATER scans start;
    an analysis already measured keeps the reading it was measured with."""
    first, _ = _proposed(client, "ORIENT-PAST")
    client.post(f"/api/analyses/{first}/confirm",
                json={"stage": "C", "save_profile": False})
    client.post(f"/api/analyses/{first}/compute", json={"sid_mm": 1000.0})
    kept = client.mod.store.get(first)
    before = (kept["geometry"], kept["results"])

    second, _ = _proposed(client, "ORIENT-PAST")
    _set(client, second, True)
    client.post(f"/api/analyses/{second}/confirm", json={"stage": "C"})
    assert _stored_insert(client, "ORIENT-PAST") == {"flipped": True}

    after = client.mod.store.get(first)
    assert (after["geometry"], after["results"]) == before


# ------------------------------------------------ what the page is sent

def test_the_close_up_carries_the_orientation_in_a_few_bytes(client):
    """No extra request, and next to nothing on a 512 kbit/s link."""
    aid, _ = _proposed(client, "ORIENT-VIEW")
    o = client.get(f"/api/analyses/{aid}/lowcontrast_view").json()["orientation"]
    assert set(o) == {"flipped", "source", "conflict", "rings_turned"}
    assert len(json.dumps(o)) < 120


def test_the_endpoint_is_classified_in_the_route_inventory():
    from test_authorization import ROUTES
    assert ("POST", "/api/analyses/{aid}/lowcontrast_orientation", "user") \
        in ROUTES


def _app_js():
    path = os.path.join(os.path.dirname(HERE), "phantom_qa", "webapp",
                        "static", "app.js")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_the_marking_step_offers_the_choice_and_says_where_it_came_from():
    js = _app_js()
    for words in ("as drawn", "turned half round", "read from the contrast",
                  "saved for this phantom", "set by hand"):
        assert words in js, f"the marking step never says {words!r}"
    assert "lc-insert-drawn" in js and "lc-insert-turned" in js


def test_the_page_declares_the_state_it_edited_and_resyncs_on_refusal():
    js = _app_js()
    where = js.index("/lowcontrast_orientation`")
    assert "expect_seq" in js[where:where + 200]
    body = js[js.index("async function setInsertOrientation("):]
    body = body[:body.index("\n}")]
    assert "handleStaleGeometry(e)" in body


def test_the_hint_says_how_the_insert_setting_and_turn_180_relate():
    js = _app_js()
    start = js.index("function stageC(")
    stage = js[start:start + 6000]
    assert "never correct the same thing twice" in stage


# ------------------------------------------------ against the real phantoms

def _reference_scan(key):
    import hq_manifest as hq
    entry = next((e for e in hq.INVENTORY if e["key"] == key), None)
    if entry is None or not hq.available(entry):
        pytest.skip(f"reference scan {key} not present")
    return entry, hq.scan_path(entry)


def test_the_one_undecidable_reference_scan_is_read_with_its_saved_orientation(
        pdef):
    """CS000021, "29 donker blauw": a dark-blue print whose exposure is too
    weak to show which way round its insert is. With the phantom's saved
    orientation it is read as the build it is, and not one number moves."""
    from phantom_qa import ingest, pipeline
    entry, path = _reference_scan("CS000021")
    with open(os.path.join(HERE, "hq_benchmark.json"), encoding="utf-8") as f:
        golden = json.load(f)["scans"]["CS000021"]["metrics"]["lowcontrast_cnr"]

    scan = ingest.load_path(path)[0]
    reg = pipeline.run_stage_a(scan, pdef)
    ctx = pipeline.build_ctx(scan, pdef, reg, {"scan_meta": scan.meta})
    geom = LC.propose(ctx)
    assert geom["orientation"]["source"] == "undetermined", \
        "this scan was expected to be the undecidable one"

    geom["orientation"] = _kept("donker" in entry["label"])
    res = LC.compute(ctx, geom)
    assert res["orientation"]["source"] == "stored"
    assert res["orientation"]["flipped"] is True
    assert not res["orientation"].get("conflict")
    assert _disc_of(res, "L1") == "L5"
    for r in res["rows"]:
        assert r["cnr"] == pytest.approx(golden[r["id"]], abs=1e-6)
