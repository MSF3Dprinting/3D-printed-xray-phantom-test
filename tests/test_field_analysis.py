"""Field analysis: when it is offered, and the automatic run.

Field analysis places every measuring point from the phantom's saved points
and shows the operator only the results. It is offered only where nothing an
operator would have caught on screen can be wrong (request B; decisions 11 and
12 in docs/FIELD_TEST_PLAN.md):

1. the phantom has saved points that a clean, full analysis established;
2. they were saved under the phantom description in use now;
3. the file is the original DICOM;
4. the scan passed the image-quality check;
5. all four ruler lines were found, each within 1.0 mm;
6. nobody has worked on the analysis yet, and it is not finalised or signed
   off.

Otherwise only the full analysis is offered, with one plain line saying why.
The offer is tested on records built in the store, so every condition can be
set on its own. The run is tested on real reference scans of phantom HmmEi
(skipped where they are not on the computer): one run end to end against the
same scan done step by step, and each stop with one step made to fail.
"""

import json
import os

import pytest

import hq_manifest as hm
from phantom_qa.store import geometry_fingerprint
from test_layout_save_choice import OK, POOR, _audit_lines, _make_client
from test_store_labels import fake_scan, results_for

_variant = iter(range(1, 100_000))

#: Measuring points of the analysis the saved points came from. Their content
#: does not matter here, only that the fingerprint recorded with the saved
#: points still matches them.
GEOMETRY = {"uniformity": {"squares": [{"id": "C", "type": "rect",
                                        "center_mm": [0.0, 0.0]}]}}


def _landmarks(*errs):
    """A registration's ruler lines; None is a line that was not found."""
    sides = ("top", "bottom", "left", "right")
    return {"landmarks": {s: {"predicted_px": [0, 0], "err_mm": e,
                              "strength": 1.0}
                          for s, e in zip(sides, errs)}}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    return _make_client(tmp_path, monkeypatch)


def _record(client, phantom, **fields):
    store = client.mod.store
    aid = store.new_analysis(
        fake_scan(sha=f"{next(_variant):064x}", name=f"{phantom}.dcm"), b"x",
        "sig", "1.0.0", client.mod.pdef.version,
        labels={"site": "T", "phantom": phantom})
    if fields:
        store.update(aid, **fields)
    return aid


def _saved_points(client, phantom, *, ordering_ok=True, pdef_version=None,
                  source=None):
    """A measured full analysis whose points are the phantom's saved points."""
    pdef = client.mod.pdef
    results = results_for()
    results["lowcontrast"].update(ordering_ok=ordering_ok,
                                  order_rho=0.93 if ordering_ok else 0.12)
    aid = _record(client, phantom, geometry=GEOMETRY, results=results,
                  status="pass", stage="F", quality=OK, quality_verdict="ok")
    version = pdef.version if pdef_version is None else pdef_version
    client.mod.store.save_phantom_profile(
        phantom, {"pdef_name": pdef.name, "pdef_version": version,
                  "rois": {"uniformity/C": {"type": "rect"}}},
        pdef_version=version, source_analysis_id=aid, updated_by="qa",
        source=source if source is not None else {
            "source_name": f"{phantom}-first.dcm", "quality_verdict": "ok",
            "quality_override": False,
            "geometry_fingerprint": geometry_fingerprint(GEOMETRY)})
    return aid


def _fresh(client, phantom, **fields):
    """A new DICOM scan of the phantom, located and passed, nothing done yet."""
    return _record(client, phantom, **{
        "quality": OK, "quality_verdict": "ok", "stage": "A",
        "reg": _landmarks(0.11, 0.3, 0.42, 0.55), **fields})


def _offer(client, aid):
    r = client.get(f"/api/analyses/{aid}")
    assert r.status_code == 200, r.text
    return r.json()["field_offer"]


# ------------------------------------------------------------ offered

def test_offered_when_every_condition_holds(client):
    _saved_points(client, "FIELD")
    offer = _offer(client, _fresh(client, "FIELD"))
    assert offer["offered"] is True
    assert offer["why"] == "" and offer["reasons"] == []
    assert offer["phantom"] == "FIELD"
    assert offer["points_from"] == "FIELD-first.dcm"
    assert offer["points_saved_by"] == "qa" and offer["points_saved_at"]


def test_the_application_placing_the_points_keeps_it_fresh(client):
    """Registration confirmed, patterns detected and the saved points laid on
    top: all of that is the application's own work, not an operator's."""
    _saved_points(client, "AUTO")
    aid = _fresh(client, "AUTO", stage="B")
    store = client.mod.store
    store.set_geometry_baseline(aid, {"u": {"c": 1.0}}, action="propose")
    store.replace_geometry(aid, {"u": {"c": 2.0}}, action="apply_profile")
    assert _offer(client, aid)["offered"] is True


def test_a_ruler_line_exactly_at_the_limit_is_accepted(client):
    _saved_points(client, "EDGE")
    aid = _fresh(client, "EDGE", reg=_landmarks(0.2, 0.2, 0.2, 1.0))
    assert _offer(client, aid)["offered"] is True


# -------------------------------------------- 1. the saved points

def test_not_offered_without_a_phantom_name(client):
    offer = _offer(client, _fresh(client, ""))
    assert offer["offered"] is False
    assert offer["why"] == ("No phantom is named on this analysis, so there "
                            "are no saved measuring points to use.")


def test_not_offered_before_the_phantom_has_saved_points(client):
    offer = _offer(client, _fresh(client, "NEW"))
    assert offer["reasons"] == [
        "Phantom NEW has no saved measuring points yet. Its first analysis is "
        "always a full one."]


def test_not_offered_on_points_whose_discs_were_out_of_order(client):
    _saved_points(client, "DISORDER", ordering_ok=False)
    offer = _offer(client, _fresh(client, "DISORDER"))
    assert offer["reasons"] == [
        "The discs of the analysis these points came from did not measure in "
        "design order (rank correlation 0.12)."]


def test_not_offered_on_points_kept_by_administrator_override(client):
    _saved_points(client, "OVERRULED", source={
        "source_name": "x.dcm", "quality_verdict": "poor",
        "quality_override": True,
        "geometry_fingerprint": geometry_fingerprint(GEOMETRY)})
    offer = _offer(client, _fresh(client, "OVERRULED"))
    assert offer["reasons"] == [
        "These points were saved from a scan that failed the image-quality "
        "check, by administrator override."]


def test_not_offered_on_points_saved_before_their_origin_was_recorded(client):
    _saved_points(client, "OLD", source={})
    offer = _offer(client, _fresh(client, "OLD"))
    assert offer["why"].startswith("Where these points came from was not "
                                   "recorded")


# -------------------------------------------- 2. the phantom description

def test_not_offered_on_points_saved_under_another_description(client):
    _saved_points(client, "OLDDEF", pdef_version="0.9")
    offer = _offer(client, _fresh(client, "OLDDEF"))
    assert offer["reasons"] == [
        f"The saved points were made for phantom description 0.9, and this is "
        f"{client.mod.pdef.version}. Save them again from a full analysis."]


# ---------------------------------------------- 3. the file, 4. quality

def test_not_offered_on_a_plain_picture(client):
    _saved_points(client, "PICTURE")
    offer = _offer(client, _fresh(client, "PICTURE", kind="image"))
    assert offer["reasons"] == ["Field analysis needs the original DICOM "
                                "file, and this is a plain picture."]


@pytest.mark.parametrize("verdict,quality,line", [
    ("poor", POOR, "This scan did not pass the image-quality check."),
    ("", None, "This scan's image quality has not been checked."),
])
def test_not_offered_without_a_passed_quality_check(client, verdict, quality,
                                                    line):
    _saved_points(client, f"Q{verdict}")
    aid = _fresh(client, f"Q{verdict}", quality=quality,
                 quality_verdict=verdict)
    assert _offer(client, aid)["reasons"] == [line]


# --------------------------------------------------- 5. the ruler lines

@pytest.mark.parametrize("reg,line", [
    (None, "The phantom has not been located on this scan yet."),
    (_landmarks(0.2, 0.3, 0.4, None),
     "Only 3 of the 4 ruler lines were found when the phantom was located."),
    (_landmarks(0.2, 1.01, 0.4, 0.3),
     "A ruler line sits 1.01 mm from where the phantom was located. Field "
     "analysis needs 1.0 mm or less."),
])
def test_not_offered_on_a_doubtful_registration(client, reg, line):
    _saved_points(client, "RULER")
    aid = _fresh(client, "RULER", reg=reg)
    assert _offer(client, aid)["reasons"] == [line]


# ------------------------------------------------------ 6. fresh

def _finalise(client, aid):
    client.mod.store.set_finalized(aid, "qa")


def _sign_off(client, aid):
    client.mod.store.set_validation(aid, "validated", "Dr Test")


def _run_as_field(client, aid):
    client.mod.store.update(aid, analysis_mode="field")


def _measure(client, aid):
    client.mod.store.update(aid, results=results_for(), status="pass")


def _past_step_c(client, aid):
    client.mod.store.update(aid, stage="D")


def _move_a_point(client, aid):
    store = client.mod.store
    store.set_geometry_baseline(aid, {"u": {"c": 1.0}}, action="propose")
    store.mutate_geometry(aid, lambda g: g["u"].__setitem__("c", 1.5),
                          action="roi")


def _re_run(client, aid):
    client.mod.store.begin_rerun(aid, user="qa", reason="check",
                                 mode="registration")


@pytest.mark.parametrize("act,line", [
    (_sign_off, "This analysis has been signed off."),
    (_finalise, "This analysis is finalised."),
    (_run_as_field, "Field analysis has already been run on this analysis."),
    (_measure, "Work has already been done on this analysis step by step."),
    (_past_step_c, "Work has already been done on this analysis step by step."),
    (_move_a_point,
     "Work has already been done on this analysis step by step."),
    (_re_run, "Work has already been done on this analysis step by step."),
], ids=["signed off", "finalised", "already run", "measured", "past step C",
        "point moved", "re-run"])
def test_not_offered_once_the_analysis_is_not_fresh(client, act, line):
    _saved_points(client, "WORKED")
    aid = _fresh(client, "WORKED")
    act(client, aid)
    assert _offer(client, aid)["reasons"] == [line]


# ------------------------------------------------------ the one line

def test_the_line_shown_is_the_first_of_every_reason(client):
    _saved_points(client, "MANY", pdef_version="0.9")
    aid = _fresh(client, "MANY", kind="image", quality=POOR,
                 quality_verdict="poor", reg=_landmarks(0.2, None, None, 0.3))
    offer = _offer(client, aid)
    assert offer["offered"] is False
    assert len(offer["reasons"]) == 4
    assert offer["why"] == offer["reasons"][0]
    assert offer["why"].startswith("The saved points were made for phantom "
                                   "description 0.9")


def test_an_uploaded_picture_is_told_why_through_the_whole_path(client):
    """Through the upload: a plain picture of a phantom whose saved points
    are fine is refused for being a picture, and for failing the check."""
    from test_unusable_exposures import SYNTHETIC, _png16
    _saved_points(client, "UPLOADED")
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = 60_001
    up = client.post("/api/analyses", files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": "UPLOADED"})
    assert up.status_code == 200, up.text
    offer = _offer(client, up.json()["analyses"][0]["id"])
    assert offer["offered"] is False
    assert offer["reasons"][:2] == [
        "Field analysis needs the original DICOM file, and this is a plain "
        "picture.",
        "This scan did not pass the image-quality check."]


# =================================================== the automatic run
#
# One request places the saved points, measures and stores the results with
# the code the steps use, so the numbers are identical (request B). It stops
# at step B where an operator would have had to act, flags what should be
# looked at, never changes the saved points, and refuses a second press and a
# locked analysis.

FIRST, LATER = "PH0727_03", "PH0730_03"      # two scans of phantom HmmEi


def _entry(key):
    entry = next(e for e in hm.INVENTORY if e["key"] == key)
    if not hm.available(entry):
        pytest.skip(f"{key} is not on this computer")
    return entry


def _upload(client, key, phantom, again=False):
    with open(hm.scan_path(_entry(key)), "rb") as f:
        up = client.post("/api/analyses",
                         files={"file": (f"{key}.dcm", f.read())},
                         data={"site": "T", "phantom": phantom,
                               **({"allow_duplicate": "true"} if again
                                  else {})})
    assert up.status_code == 200, up.text
    return up.json()["analyses"][0]["id"]


def _step_by_step(client, aid, save):
    """Every button a careful operator presses, accepting each point."""
    for path, body in (("confirm", {"stage": "A"}), ("propose", {}),
                       ("confirm", {"stage": "B"}),
                       ("confirm", {"stage": "C", "save_profile": save}),
                       ("confirm", {"stage": "D"}),
                       ("compute", {"sid_mm": 1000.0}),
                       ("confirm", {"stage": "E"})):
        r = client.post(f"/api/analyses/{aid}/{path}", json=body)
        assert r.status_code == 200, (path, r.text)


def _run(client, aid):
    return client.post(f"/api/analyses/{aid}/field_run")


def _profile_row(client, phantom):
    with client.mod.store._conn() as c:
        return dict(c.execute("SELECT * FROM phantom_profiles WHERE"
                              " phantom_key=?", (phantom,)).fetchone())


def test_a_field_run_on_a_real_scan(client, tmp_path):
    """The whole path on two real scans of one phantom: the first saves the
    points step by step, the second runs as Field analysis, and the same scan
    done step by step gives exactly the same numbers."""
    first = _upload(client, FIRST, "HmmEi")
    _step_by_step(client, first, save=True)
    saved = _profile_row(client, "HmmEi")

    aid = _upload(client, LATER, "HmmEi")
    assert _offer(client, aid)["offered"] is True, _offer(client, aid)
    r = _run(client, aid)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["outcome"] == "finished" and run["stage"] == "F"

    # the same scan, every button pressed by hand
    by_hand = _upload(client, LATER, "HmmEi", again=True)
    _step_by_step(client, by_hand, save=False)
    store = client.mod.store
    auto, hand = store.get(aid), store.get(by_hand)
    assert auto["results"] == hand["results"], "the numbers differ"
    assert auto["status"] == hand["status"] == run["overall"]
    assert auto["geometry"] == hand["geometry"], "the points differ"
    assert run["review"] == client.mod._field_review(hand["results"])

    # the answer carries what the result screen draws, the points included,
    # and reopening shows the same
    assert run["geometry"] == auto["geometry"]
    reopened = client.get(f"/api/analyses/{aid}").json()
    assert run["field_result"] == reopened["field_result"]
    assert reopened["field_result"]["run"]["points_from"] == f"{FIRST}.dcm"
    assert reopened["field_stopped"] == ""

    # recorded as what it is, and left for the operator to finalise
    assert auto["analysis_mode"] == "field" and auto["stage"] == "F"
    assert not auto["finalized_at"] and not auto["validation_status"]
    assert hand["analysis_mode"] == "full"

    # the saved points are exactly as they were
    assert _profile_row(client, "HmmEi") == saved

    # every check value, in the record's own trail...
    trail = auto["audit"]
    done = [e for e in trail if e["action"] == "field analysis"][-1]["detail"]
    assert done["points_from_analysis"] == first
    assert done["points_full_analysis"] is True
    assert done["points_quality_ok"] is True
    assert done["points_discs_in_order"] is True
    assert done["kind"] == "dicom" and done["quality_verdict"] == "ok"
    assert len(done["ruler_err_mm"]) == 4
    assert all(e <= 1.0 for e in done["ruler_err_mm"].values())
    assert done["layout_check"]["ok"] is True
    assert done["placed"]["n_applied"] > 0 and done["placed"]["n_skipped"] == 0
    assert done["overall"] == run["overall"] and done["review"] == run["review"]
    assert done["seconds"] == run["seconds"]
    confirmed = [e["stage"] for e in trail if e["action"] == "confirmed"
                 and (e.get("detail") or {}).get("note")
                 == "confirmed by Field analysis"]
    assert confirmed == ["A", "B", "C", "D", "E"]
    # ...and in the audit log
    assert any("outcome=finished" in line
               for line in _audit_lines(tmp_path, "field_run"))

    # a second press is refused, and says why
    again = _run(client, aid)
    assert again.status_code == 409
    assert again.json()["detail"] == ("Field analysis has already been run "
                                      "on this analysis.")
    print(f"\nField run on {LATER}: {run['seconds']} s, {run['overall']}, "
          f"review {run['review']}")


# ------------------------------------------------------------ refused

def test_a_run_that_is_not_offered_is_refused_with_the_reason(client,
                                                              tmp_path):
    aid = _fresh(client, "NEW")
    r = _run(client, aid)
    assert r.status_code == 409
    assert r.json()["detail"] == ("Phantom NEW has no saved measuring points "
                                  "yet. Its first analysis is always a full "
                                  "one.")
    assert client.mod.store.get(aid)["analysis_mode"] == "full"
    assert any("outcome=refused" in line
               for line in _audit_lines(tmp_path, "field_run"))


@pytest.mark.parametrize("lock", ["finalised", "signed off"])
def test_a_locked_analysis_is_refused(client, lock):
    _saved_points(client, "LOCKED")
    aid = _fresh(client, "LOCKED")
    (_finalise if lock == "finalised" else _sign_off)(client, aid)
    assert _run(client, aid).status_code == 409
    assert client.mod.store.get(aid)["analysis_mode"] == "full"


def test_only_one_of_two_presses_at_once_gets_the_analysis(client):
    _saved_points(client, "RACE")
    aid = _fresh(client, "RACE")
    store = client.mod.store
    assert store.claim_field_run(aid) is True
    assert store.claim_field_run(aid) is False
    assert store.get(aid)["analysis_mode"] == "field"


# -------------------------------------------------------------- stops

def _ready(client, phantom):
    """A real scan of a phantom whose saved points are clean on paper."""
    _saved_points(client, phantom)
    aid = _upload(client, LATER, phantom)
    assert _offer(client, aid)["offered"] is True, _offer(client, aid)
    return aid


def _assert_stopped(client, aid, reason):
    r = _run(client, aid)
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "stopped" and r.json()["stage"] == "B"
    assert r.json()["reason"] == reason
    rec = client.mod.store.get(aid)
    assert rec["stage"] == "B" and rec["analysis_mode"] == "full"
    assert not rec["results"]
    # what step B needs comes with the answer, and the reason with the record
    assert r.json()["geometry"] == rec["geometry"]
    assert r.json()["history"]["seq"] == rec["geometry_seq"]
    assert client.get(f"/api/analyses/{aid}").json()["field_stopped"] == reason
    stopped = [e for e in rec["audit"]
               if e["action"] == "field analysis stopped"][-1]["detail"]
    assert stopped["reason"] == reason and "ruler_err_mm" in stopped
    # not offered again: the operator continues step by step
    offer = _offer(client, aid)
    assert offer["offered"] is False
    assert offer["why"] == (f"Field analysis stopped on this analysis: "
                            f"{reason} Continue step by step.")
    assert _run(client, aid).status_code == 409


def test_it_stops_when_a_pattern_is_not_found(client, monkeypatch):
    aid = _ready(client, "NOTFOUND")
    real = client.mod.pipeline.propose_all

    def wedge_lost(ctx, deadline=None):
        geom = real(ctx, deadline=deadline)
        geom["wedge"] = {"_error": "no wedge edge found"}
        return geom
    monkeypatch.setattr(client.mod.pipeline, "propose_all", wedge_lost)
    _assert_stopped(client, aid, "Wedge could not be found on this scan "
                                 "(no wedge edge found).")


def test_it_stops_when_the_saved_points_are_more_than_8_mm_off(client,
                                                              monkeypatch):
    aid = _ready(client, "FAROFF")
    monkeypatch.setattr(
        client.mod.layout_profile, "layout_agrees",
        lambda layout, geom: {"ok": False, "checked": [], "median_mm": 12.0,
                              "reason": "the stored layout sits 12 mm off."})
    _assert_stopped(client, aid, "The saved points do not fit this scan: the "
                                 "stored layout sits 12 mm off.")


def test_it_stops_when_a_saved_area_has_nothing_to_go_with(client,
                                                          monkeypatch):
    aid = _ready(client, "SKIPPED")
    layout = client.mod.store.get_phantom_profile("SKIPPED")["layout"]
    layout["rois"] = {"uniformity/Z": {"type": "rect"}}
    with client.mod.store._conn() as c:
        c.execute("UPDATE phantom_profiles SET layout_json=? WHERE"
                  " phantom_key='SKIPPED'", (json.dumps(layout),))
    monkeypatch.setattr(client.mod.layout_profile, "layout_agrees",
                        lambda layout, geom: {"ok": True, "checked": [],
                                              "median_mm": 0.5, "reason": ""})
    _assert_stopped(client, aid, "1 saved measuring area found nothing to go "
                                 "with on this scan (uniformity/Z).")


def test_a_run_that_fails_leaves_an_ordinary_analysis(client, monkeypatch):
    aid = _ready(client, "BROKEN")

    def broken(ctx, deadline=None):
        raise RuntimeError("detector exploded")
    monkeypatch.setattr(client.mod.pipeline, "propose_all", broken)
    with pytest.raises(RuntimeError):
        _run(client, aid)
    rec = client.mod.store.get(aid)
    assert rec["analysis_mode"] == "full" and not rec["results"]
    assert _offer(client, aid)["why"] == (
        "Field analysis stopped on this analysis: the automatic run failed "
        "(RuntimeError). Continue step by step.")


# ------------------------------------------------------------- review

def test_review_names_every_test_that_did_not_pass_and_the_other_build(
        client):
    results = results_for()
    results["lowcontrast"].update(
        status="warn", reasons=["|CNR| does not rise with the design order."],
        orientation={"conflict": True})
    results["wedge"] = {"status": "not measured",
                        "error": "no measuring areas were placed"}
    results["uniformity"]["status"] = "fail"
    results["uniformity"]["reasons"] = []
    assert client.mod._field_review(results) == [
        "The low-contrast discs look like the other build of this phantom. "
        "Check the phantom ID.",
        "Low contrast did not pass (warn): |CNR| does not rise with the design "
        "order.",
        "Uniformity did not pass (fail): no reason given",
        "Wedge could not be analysed: no measuring areas were placed"]


def test_nothing_to_review_when_every_test_passed(client):
    assert client.mod._field_review(results_for()) == []


# ============================================================ the screens
#
# The choice after registration and the result screen (decisions 13 and 14).
# What the server sends for them is tested here; the page itself is read as
# source, as the other page tests do, and was looked at in Edge.

def test_one_line_per_test_with_why_for_anything_that_did_not_pass(client):
    results = results_for()
    results["lowcontrast"].update(status="warn",
                                  reasons=["|CNR| is not in design order.",
                                           "second sentence"])
    lines = {l["test"]: l for l in client.mod._test_lines(results)}
    assert lines["Low contrast"] == {"test": "Low contrast", "status": "warn",
                                     "reason": "|CNR| is not in design order."}
    assert lines["Uniformity"]["reason"] == ""       # passed: nothing to say


def test_only_a_field_analysis_carries_a_field_result(client):
    _saved_points(client, "RESULT")
    aid = _fresh(client, "RESULT", results=results_for(), status="pass",
                 stage="F", analysis_mode="field")
    client.mod.store.audit(aid, "F", "field analysis", {
        "points_from": "first.dcm", "points_saved_at": "2026-09-28 10:00:00",
        "points_saved_by": "qa", "seconds": 1.9, "ruler_err_mm": {}})
    got = client.get(f"/api/analyses/{aid}").json()["field_result"]
    assert got["review"] == [] and got["lines"]
    assert got["run"] == {"points_from": "first.dcm",
                          "points_saved_at": "2026-09-28 10:00:00",
                          "points_saved_by": "qa", "seconds": 1.9}
    full = _fresh(client, "RESULT", results=results_for(), stage="F")
    assert client.get(f"/api/analyses/{full}").json()["field_result"] is None


def test_registering_again_answers_with_the_new_offer(client):
    """Manual corners change the ruler lines and the verdict, and with them
    the offer, which the page learns from the same answer."""
    aid = _ready(client, "REREG")
    corners = client.mod.store.get(aid)["reg"]["corners_px"]
    r = client.post(f"/api/analyses/{aid}/register",
                    json={"corners_px": corners})
    assert r.status_code == 200, r.text
    assert r.json()["field_offer"]["offered"] is True


STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "phantom_qa", "webapp", "static")


@pytest.fixture(scope="module")
def app_js():
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        return f.read()


def _function(app_js, name):
    start = app_js.index(f"function {name}(")
    end = app_js.find("\nfunction ", start + 1)
    nxt = app_js.find("\nasync function ", start + 1)
    ends = [e for e in (end, nxt) if e > 0]
    return app_js[start:min(ends)] if ends else app_js[start:]


def test_the_choice_highlights_field_and_shows_the_phantom_large(app_js):
    choice = _function(app_js, "fieldChoice")
    assert "Field analysis — automatic" in choice
    assert "Full analysis — step by step" in choice
    assert 'class="field-phantom"' in choice and "offer.phantom" in choice
    for part in ("points_saved_at", "points_saved_by", "points_from"):
        assert part in choice
    assert 'class="choice choice-field"' in choice
    assert 'id="btn-field"' in choice and 'id="btn-confirm-a"' in choice
    # not offered: the full analysis only, and the one line
    assert "Field analysis is not available:" in choice
    assert "offer.why" in choice


def test_the_run_goes_to_its_result_or_back_to_step_b(app_js):
    run = _function(app_js, "runField")
    assert "field_run" in run
    assert 'r.outcome === "stopped"' in run and 'setStage("B")' in run
    assert 'setStage("F")' in run
    # a refusal or a lost connection shows what the server holds
    assert "refreshRecord()" in run
    # step B says why the run stopped there, on reopening too
    step_b = _function(app_js, "stageB")
    assert "S.record.field_stopped" in step_b
    assert "Field analysis\n      stopped here." in step_b


def test_a_field_analysis_finishes_and_reopens_on_its_result_screen(app_js):
    assert ('r.analysis_mode === "field" && S.results) return fieldResult(c)'
            in _function(app_js, "stageF"))
    # reopening a measured analysis goes to step F
    assert 'if (rec.results) return "F";' in _function(app_js, "openingStage")


def test_the_result_screen_has_its_four_buttons_and_no_reference(app_js):
    screen = _function(app_js, "fieldResult")
    for label in ("Open full report", "Review step by step", "Finalise",
                  "New analysis", "Review recommended"):
        assert label in screen, label
    assert "baseline" not in screen.lower()
    assert "reference" not in screen.lower()
    assert "finalizeAnalysis" in screen


def test_field_screens_download_nothing_more(app_js):
    """The picture is the one already on screen, and the zoom preload waits
    until someone chooses to work step by step."""
    screen = _function(app_js, "fieldResult")
    assert "loadImage" not in screen and "api(" not in screen
    opening = _function(app_js, "openAnalysis")
    assert "S.preloadWithheld = fieldAhead(rec);" in opening
    assert "S.preloadPending = !S.preloadWithheld;" in opening
    assert "startWithheldPreload();" in _function(app_js, "stageA")
    assert "startWithheldPreload();" in screen


# ============================================ the mode everywhere, the rules
#
# Decisions 4, 5 and 14: the report, History, the sign-off panel and the CSV
# all say a Field analysis is one; it cannot become the reference scan; an
# administrator may sign it off, printed as automatic; reviewing it step by
# step makes it a full one; discarding or re-running it leaves the phantom's
# saved points alone.

MODE = "Field analysis — automatic, measuring points not reviewed on screen"


def _field_record(client, phantom="MODE", **fields):
    """A measured Field analysis, as the automatic run leaves it."""
    _saved_points(client, phantom)
    aid = _fresh(client, phantom, **{"results": results_for(),
                                     "status": "pass", "stage": "F",
                                     "analysis_mode": "field", **fields})
    client.mod.store.audit(aid, "F", "field analysis", {
        "phantom": phantom, "points_from": "first.dcm",
        "points_saved_at": "2026-09-28 10:00:00", "points_saved_by": "qa"})
    return aid


def _report(client, aid):
    from phantom_qa.report import build_report
    return build_report(client.mod.store.get(aid))


def test_the_report_says_it_was_automatic_on_every_page(client):
    html = _report(client, _field_record(client))
    assert f"<b>{MODE}.</b>" in html
    assert ("placed from the points saved for phantom MODE on "
            "2026-09-28 10:00 by qa, from scan first.dcm") in html
    assert "Field analysis — automatic" in html[html.index("@top-right"):
                                               html.index("@bottom-left")]
    full = _fresh(client, "MODE", results=results_for(), status="pass",
                  stage="F")
    plain = _report(client, full)
    assert MODE not in plain and "modebox\">" not in plain


def test_a_signed_off_field_analysis_is_printed_as_automatic(client):
    aid = _field_record(client)
    client.mod.store.set_validation(aid, "validated", "Dr Test")
    html = _report(client, aid)
    assert "VALIDATED (AUTOMATIC)</div>" in html
    assert "signed off on a Field analysis" in html
    full = _fresh(client, "MODE", results=results_for(), status="pass",
                  stage="F")
    client.mod.store.set_validation(full, "validated", "Dr Test")
    assert "(AUTOMATIC)" not in _report(client, full)


def test_an_administrator_may_sign_off_a_field_analysis(client, tmp_path):
    from test_authorization import ADMIN_PW
    aid = _field_record(client)
    r = client.post(f"/api/analyses/{aid}/validation", json={
        "status": "validated", "validated_by": "Dr Test", "comment": "",
        "admin_password": ADMIN_PW})
    assert r.status_code == 200, r.text
    trail = client.mod.store.get(aid)["audit"]
    assert [e for e in trail if e["action"] == "validation set"][-1][
        "detail"]["automatic"] is True
    assert any('"automatic":true' in line
               for line in _audit_lines(tmp_path, "validation"))


def test_history_and_both_csv_exports_carry_the_mode(client):
    from phantom_qa.store import csv_export, wide_csv_export
    field = _field_record(client)
    full = _fresh(client, "MODE", results=results_for(), status="pass",
                  stage="F")
    rows = {a["id"]: a for a in client.get("/api/analyses").json()["analyses"]}
    assert rows[field]["analysis_mode"] == "field"
    assert rows[full]["analysis_mode"] == "full"

    recs = client.mod.store.get_slim([field, full])
    long = csv_export(recs).splitlines()
    assert long[0].split(",")[-1] == "analysis_mode"
    modes = {line.split(",")[0]: line.split(",")[-1] for line in long[1:]}
    assert modes == {field: "field", full: "full"}
    wide = wide_csv_export(recs).splitlines()
    assert wide[-1].split(",")[2] == "# analysis_mode"
    assert sorted(wide[-1].split(",")[4:]) == ["field", "full"]


def test_a_field_analysis_cannot_become_the_reference(client):
    aid = _field_record(client)
    for path, body in (("baseline", {"baseline": True}),
                       ("finalize", {"baseline": True})):
        r = client.post(f"/api/analyses/{aid}/{path}", json=body)
        assert r.status_code == 409, (path, r.text)
        assert r.json()["detail"].startswith(
            "A Field analysis cannot be the reference scan")
    assert not client.mod.store.get(aid)["is_baseline"]
    # removing a reference is never refused, and finalising alone is fine
    assert client.post(f"/api/analyses/{aid}/finalize",
                       json={}).status_code == 200


def test_discarding_a_field_analysis_leaves_the_saved_points(client):
    aid = _field_record(client, "DISCARD", stage="E")
    before = _profile_row(client, "DISCARD")
    client.mod.store.delete(aid)
    assert client.mod.store.get(aid) is None
    assert _profile_row(client, "DISCARD") == before


def test_the_page_tags_field_analyses_in_history_and_sign_off(app_js):
    assert f'const FIELD_MODE_TEXT =\n  "{MODE}";' in app_js
    history = app_js[app_js.index("r.analyses.forEach(a => {"):]
    history = history[:history.index("tb.appendChild(tr);")]
    assert 'a.analysis_mode === "field"' in history
    assert '<span class="chip field"' in history
    assert "cannot be the reference scan" in history      # no star to press
    dialog = _function(app_js, "validationDialog")
    assert 'rec.analysis_mode === "field"' in dialog and "FIELD_MODE_TEXT" in dialog


def test_reviewing_it_step_by_step_makes_it_a_full_analysis(client):
    """On real scans: after the automatic run, the operator goes through the
    steps and measures again. The phantom's saved points are untouched by a
    re-run and by the review, and the reviewed analysis may then be the
    reference."""
    first = _upload(client, FIRST, "HmmEi")
    _step_by_step(client, first, save=True)
    saved = _profile_row(client, "HmmEi")
    aid = _upload(client, LATER, "HmmEi")
    assert _run(client, aid).json()["outcome"] == "finished"

    # a re-run started and cancelled leaves the points and the mode
    r = client.post(f"/api/analyses/{aid}/rerun",
                    json={"start": "points", "reason": "check"})
    assert r.status_code == 200, r.text
    assert _profile_row(client, "HmmEi") == saved
    assert client.post(f"/api/analyses/{aid}/rerun/cancel",
                       json={}).status_code == 200
    assert client.mod.store.get(aid)["analysis_mode"] == "field"

    # measured again without anyone confirming the points: still automatic
    r = client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    assert r.status_code == 200, r.text
    assert client.mod.store.get(aid)["analysis_mode"] == "field"

    for path, body in (("confirm", {"stage": "B"}),
                       ("confirm", {"stage": "C", "save_profile": False}),
                       ("confirm", {"stage": "D"}),
                       ("compute", {"sid_mm": 1000.0})):
        r = client.post(f"/api/analyses/{aid}/{path}", json=body)
        assert r.status_code == 200, (path, r.text)
    rec = client.mod.store.get(aid)
    assert rec["analysis_mode"] == "full"
    assert any(e["action"] == "field analysis reviewed step by step"
               for e in rec["audit"])
    assert client.get(f"/api/analyses/{aid}").json()["field_result"] is None
    assert _profile_row(client, "HmmEi") == saved
    r = client.post(f"/api/analyses/{aid}/baseline", json={"baseline": True})
    assert r.status_code == 200, r.text
