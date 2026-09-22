"""Keeping a phantom's measuring points is a choice, and the quality check
can be overruled only by an administrator.

Two decisions from the first field test, taken together because they guard
the same thing — the phantom's SHARED measuring points and its reference scan,
which every later scan of that phantom starts from or is judged against.

The first: confirming step C used to store the measuring points for the
phantom every time. The audit log shows one phantom's layout rewritten six
times in twelve minutes, three of those from exposures nothing could be
measured in. Now the step asks — "use these measuring points for future scans
of this phantom" — ticked for the first analysis of a phantom and unticked
once it has a layout, never offered for a scan that failed the quality check,
and always sent explicitly. A page cached from before the question existed
sends nothing; the server then applies the same rule, so an old page can still
store a phantom's first layout but can no longer replace one somebody else
confirmed.

The second: a scan that failed the quality check could never become the
reference or set the measuring points, with no way past that at all. An
administrator can now overrule it — with the administrator password and a
written reason, recorded beside the checks that failed — and nobody can where
no administrator password is configured, the same rule as delete.
"""

import os
import re

import pytest

from test_authorization import ADMIN_PW
from test_store_labels import fake_scan, results_for
from test_unusable_exposures import SYNTHETIC, _png16

# Below 65 536: the variant is written into a 16-bit pixel, and anything above
# clips to the same value — which uploads the same file twice.
_variant = iter(range(50_000, 60_000))

REASON = "only exposure possible today, checked by eye"

#: A verdict of the shape quality.assess() writes, for records built without
#: going through an upload.
POOR = {
    "verdict": "poor",
    "checks": [
        {"id": "saturation", "label": "Pixels pinned at the maximum",
         "value": 0.97, "limit": 0.05, "ok": False,
         "detail": "97.0% of the image sits on one value."},
        {"id": "landmarks", "label": "Central ruler lines found",
         "value": 4, "limit": 3, "ok": True, "detail": "4 of 4 found."},
    ],
    "failed": ["saturation"],
    "summary": "Most of this image sits on a single value.",
}
OK = {"verdict": "ok", "checks": [], "failed": [],
      "summary": "The image is suitable for measurement."}


def _make_client(tmp_path, monkeypatch, **env):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch, **env)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


@pytest.fixture()
def client(tmp_path, monkeypatch):
    return _make_client(tmp_path, monkeypatch)


def _proposed(client, phantom, passed=True):
    """An analysis with measuring points, on a synthetic exposure.

    The synthetic exposure is one nothing can be measured in, so the quality
    check fails it. `passed` gives it the verdict of a scan that passed
    instead, which is what lets these tests reach the saving path without a
    real scan."""
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses", files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": phantom})
    assert up.status_code == 200, up.text
    aid = up.json()["analyses"][0]["id"]
    if passed:
        client.mod.store.update(aid, quality=OK, quality_verdict="ok")
    assert client.post(f"/api/analyses/{aid}/confirm",
                       json={"stage": "A"}).status_code == 200
    r = client.post(f"/api/analyses/{aid}/propose", json={})
    assert r.status_code == 200, r.text
    return aid


def _confirm_c(client, aid, **body):
    return client.post(f"/api/analyses/{aid}/confirm",
                       json={"stage": "C", **body})


def _stored_from(client, phantom):
    """Which analysis the phantom's stored layout came from, or None."""
    prof = client.mod.store.get_phantom_profile(phantom)
    return None if prof is None else prof["source_analysis_id"]


def _turn_insert(client, aid):
    seq = client.get(f"/api/analyses/{aid}").json()["history"]["seq"]
    r = client.post(f"/api/analyses/{aid}/lowcontrast_orientation",
                    json={"flipped": True, "expect_seq": seq})
    assert r.status_code == 200, r.text


def _stored_insert(client, phantom):
    prof = client.mod.store.get_phantom_profile(phantom)
    return None if prof is None else prof["layout"].get("lowcontrast_insert")


def _audit_lines(tmp_path, event):
    with open(tmp_path / "logs" / "audit.log", encoding="utf-8") as f:
        return [line for line in f if f"event={event}" in line]


def _reference_candidate(client, phantom="REF", quality=POOR):
    """A measured DICOM analysis that could be a reference but for its
    exposure. Built in the store: a plain-image upload is refused as a
    reference for an unrelated reason (it is reduced precision)."""
    aid = client.mod.store.new_analysis(
        fake_scan(sha=f"{next(_variant):064x}"), b"x", "sig", "1.0.0", "1.0",
        labels={"site": "T", "phantom": phantom})
    client.mod.store.update(aid, results=results_for(), status="pass",
                            quality=quality,
                            quality_verdict=(quality or {}).get("verdict", ""))
    return aid


# ------------------------------------------------------- the default choice

def test_the_first_analysis_of_a_phantom_stores_its_points_by_default(client):
    """Nothing is at stake yet: there are no points for these to replace."""
    aid = _proposed(client, "FIRST")
    answer = _confirm_c(client, aid).json()
    assert answer["save_profile"] is True, "the server did not say what it did"
    assert answer["profile_saved"] is True
    assert _stored_from(client, "FIRST") == aid


def test_the_page_is_told_which_analysis_stored_the_points(client):
    """The page ticks its box by the same rule, so it has to be able to tell
    a layout from this analysis from somebody else's."""
    aid = _proposed(client, "TOLD")
    _confirm_c(client, aid, save_profile=True)
    shown = client.get(f"/api/analyses/{aid}").json()["phantom_profile"]
    assert shown["source_analysis_id"] == aid


def test_once_a_phantom_has_points_a_silent_confirm_leaves_them_alone(client):
    """The field problem itself: an old page, which never asks, confirming a
    later scan of the phantom must not replace what someone else confirmed."""
    first = _proposed(client, "SILENT")
    _confirm_c(client, first, save_profile=True)
    before = client.mod.store.get_phantom_profile("SILENT")

    second = _proposed(client, "SILENT")
    answer = _confirm_c(client, second)
    assert answer.status_code == 200, "the step itself must still confirm"
    assert answer.json()["save_profile"] is False
    assert answer.json()["profile_saved"] is False
    assert answer.json()["profile_error"] is None, (
        "declining by default is not an error to shout about")
    after = client.mod.store.get_phantom_profile("SILENT")
    assert after["source_analysis_id"] == first
    assert after["layout"] == before["layout"]
    assert client.mod.store.get(second)["stage"] == "D"


def test_confirming_the_same_analysis_again_still_updates_its_own_points(client):
    """Operators nudge a mark and confirm again. When the stored points came
    from this very analysis, that must keep working without asking — or the
    first scan of a phantom could never be corrected."""
    aid = _proposed(client, "AGAIN")
    _confirm_c(client, aid)
    first_time = client.mod.store.get_phantom_profile("AGAIN")["updated_at"]
    client.mod.store.update(aid, stage="C")
    answer = _confirm_c(client, aid).json()
    assert answer["save_profile"] is True and answer["profile_saved"] is True
    assert _stored_from(client, "AGAIN") == aid
    assert client.mod.store.get_phantom_profile("AGAIN")["updated_at"] \
        >= first_time


def test_the_silent_default_is_decided_inside_the_write_lock(client):
    """Two old pages confirming two scans of one phantom at once would both
    read "nothing stored yet". The store re-checks under the lock, so the one
    that gets there second leaves the first one's layout standing."""
    store = client.mod.store
    first = _reference_candidate(client, "LOCKED-IN", quality=OK)
    second = _reference_candidate(client, "LOCKED-IN", quality=OK)
    assert store.save_phantom_profile("LOCKED-IN", {"rois": {"a": 1}},
                                      source_analysis_id=first,
                                      replace_others=False) is not None
    assert store.save_phantom_profile("LOCKED-IN", {"rois": {"b": 2}},
                                      source_analysis_id=second,
                                      replace_others=False) is None
    kept = store.get_phantom_profile("LOCKED-IN")
    assert kept["source_analysis_id"] == first
    assert kept["layout"] == {"rois": {"a": 1}}
    # ...while the analysis that owns it can still update it that way
    assert store.save_phantom_profile("LOCKED-IN", {"rois": {"c": 3}},
                                      source_analysis_id=first,
                                      replace_others=False) is not None


# ------------------------------------------------------ the explicit choice

def test_an_explicit_no_never_stores(client):
    aid = _proposed(client, "NO")
    answer = _confirm_c(client, aid, save_profile=False).json()
    assert answer["save_profile"] is False
    assert answer["profile_saved"] is False
    assert client.mod.store.get_phantom_profile("NO") is None


def test_an_explicit_yes_replaces_another_analysis_points(client):
    """Replacing a layout somebody else confirmed is allowed — as a decision."""
    first = _proposed(client, "YES")
    _confirm_c(client, first, save_profile=True)
    second = _proposed(client, "YES")
    answer = _confirm_c(client, second, save_profile=True).json()
    assert answer["profile_saved"] is True
    assert _stored_from(client, "YES") == second


def test_declining_leaves_nothing_for_a_discard_to_unwind(client):
    """The previous layout is put back when the analysis that stored the
    current one is thrown away. An analysis that declined stored nothing, so
    discarding it must leave the phantom's points exactly as they were."""
    owner = _proposed(client, "UNWIND")
    _confirm_c(client, owner, save_profile=True)
    decliner = _proposed(client, "UNWIND")
    _confirm_c(client, decliner, save_profile=False)

    gone = client.post(f"/api/analyses/{decliner}/discard",
                       json={"confirm": True}).json()
    assert gone["layout_restored"] is False and gone["layout_deleted"] is False
    assert _stored_from(client, "UNWIND") == owner


def test_an_explicit_yes_can_still_be_unwound_by_a_discard(client):
    owner = _proposed(client, "UNWIND-YES")
    _confirm_c(client, owner, save_profile=True)
    replacer = _proposed(client, "UNWIND-YES")
    _confirm_c(client, replacer, save_profile=True)
    assert _stored_from(client, "UNWIND-YES") == replacer

    gone = client.post(f"/api/analyses/{replacer}/discard",
                       json={"confirm": True}).json()
    assert gone["layout_restored"] is True
    assert _stored_from(client, "UNWIND-YES") == owner


# --------------------------------------------- the insert orientation follows

def test_the_insert_orientation_is_stored_only_with_the_points(client):
    """It rides on the stored layout, so it must follow the same choice: a
    declined save, and a silent confirm of a later scan, both leave it alone."""
    first = _proposed(client, "INSERT")
    _turn_insert(client, first)
    _confirm_c(client, first, save_profile=False)
    assert client.mod.store.get_phantom_profile("INSERT") is None

    _confirm_c(client, first, save_profile=True)
    assert _stored_insert(client, "INSERT") == {"flipped": True}

    # A later scan set the other way round, confirmed without asking...
    second = _proposed(client, "INSERT")
    client.post(f"/api/analyses/{second}/geometry/reset", json={"to": "auto"})
    seq = client.get(f"/api/analyses/{second}").json()["history"]["seq"]
    client.post(f"/api/analyses/{second}/lowcontrast_orientation",
                json={"flipped": False, "expect_seq": seq})
    _confirm_c(client, second)
    assert _stored_insert(client, "INSERT") == {"flipped": True}
    # ...and the same scan, with the box ticked.
    client.mod.store.update(second, stage="C")
    _confirm_c(client, second, save_profile=True)
    assert _stored_insert(client, "INSERT") == {"flipped": False}


# ----------------------------------------------------- the quality check

def test_a_failed_scan_is_refused_the_points_without_an_override(client):
    aid = _proposed(client, "GATE", passed=False)
    answer = _confirm_c(client, aid, save_profile=True).json()
    assert answer["profile_saved"] is False
    assert answer["profile_blocked_by_quality"] is True
    assert answer["override_available"] is True
    assert "quality" in answer["profile_error"].lower()
    assert client.mod.store.get_phantom_profile("GATE") is None


def test_a_failed_scan_is_refused_as_the_reference_and_told_why(client):
    """The refusal carries the failed checks, so the page can show what an
    administrator would be overruling instead of a bare sentence."""
    aid = _reference_candidate(client, "GATE-REF")
    answer = client.post(f"/api/analyses/{aid}/baseline",
                         json={"baseline": True})
    assert answer.status_code == 409, answer.text
    body = answer.json()
    assert body["quality_refused"] is True
    assert body["override_available"] is True
    assert [c["id"] for c in body["failed_checks"]] == ["saturation"]
    assert body["failed_checks"][0]["detail"]
    assert "quality" in body["detail"].lower()
    assert client.mod.store.baselines() == []


def test_an_administrator_can_keep_a_failed_scans_points(client, tmp_path):
    aid = _proposed(client, "OVERRIDE", passed=False)
    _turn_insert(client, aid)
    answer = _confirm_c(client, aid, save_profile=True,
                        admin_password=ADMIN_PW, reason=REASON)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["profile_saved"] is True
    assert body["profile"]["quality_override"] is True
    assert _stored_from(client, "OVERRIDE") == aid
    assert _stored_insert(client, "OVERRIDE") == {"flipped": True}

    # recorded with the record...
    failed = client.mod.store.get(aid)["quality"]["failed"]
    assert failed, "the synthetic exposure was meant to fail the check"
    trail = client.mod.store.get(aid)["audit"]
    stored = [e for e in trail if e["action"] == "phantom layout stored"]
    assert stored and stored[-1]["detail"]["quality_override"] == {
        "reason": REASON, "failed": failed}
    # ...and in the audit log, beside the checks it overrode
    saved = [l for l in _audit_lines(tmp_path, "phantom_profile")
             if "outcome=saved" in l]
    assert saved, "the override left no trace in the audit log"
    assert '"override":true' in saved[-1]
    assert REASON in saved[-1] and all(f in saved[-1] for f in failed)
    assert ADMIN_PW not in open(tmp_path / "logs" / "audit.log",
                                encoding="utf-8").read()


def test_an_administrator_can_make_a_failed_scan_the_reference(client,
                                                              tmp_path):
    aid = _reference_candidate(client, "OVERRIDE-REF")
    answer = client.post(f"/api/analyses/{aid}/baseline",
                         json={"baseline": True, "admin_password": ADMIN_PW,
                               "reason": REASON})
    assert answer.status_code == 200, answer.text
    assert answer.json()["is_baseline"] is True
    assert answer.json()["quality_override"] is True
    assert [b["id"] for b in client.mod.store.baselines()] == [aid]

    trail = client.mod.store.get(aid)["audit"]
    marked = [e for e in trail if e["action"] == "baseline set"]
    assert marked[-1]["detail"]["quality_override"]["reason"] == REASON
    lines = [l for l in _audit_lines(tmp_path, "baseline")
             if "outcome=set" in l]
    assert lines and '"override":true' in lines[-1] and REASON in lines[-1]


@pytest.mark.parametrize("password", ["wrong-password", ""])
def test_a_wrong_or_missing_password_is_refused(client, password):
    aid = _proposed(client, "BAD-PW", passed=False)
    stage_before = client.mod.store.get(aid)["stage"]
    answer = _confirm_c(client, aid, save_profile=True,
                        admin_password=password, reason=REASON)
    assert answer.status_code == 401, answer.text
    assert client.mod.store.get_phantom_profile("BAD-PW") is None
    # Checked before anything is written: the step stays where it was, so
    # the panel that asked can simply ask again.
    assert client.mod.store.get(aid)["stage"] == stage_before

    ref = _reference_candidate(client, "BAD-PW-REF")
    answer = client.post(f"/api/analyses/{ref}/baseline",
                         json={"baseline": True, "admin_password": password,
                               "reason": REASON})
    assert answer.status_code == 401, answer.text
    assert client.mod.store.baselines() == []


def test_a_missing_reason_is_refused(client):
    aid = _proposed(client, "NO-REASON", passed=False)
    answer = _confirm_c(client, aid, save_profile=True,
                        admin_password=ADMIN_PW, reason="  ")
    assert answer.status_code == 400, answer.text
    assert client.mod.store.get_phantom_profile("NO-REASON") is None

    ref = _reference_candidate(client, "NO-REASON-REF")
    answer = client.post(f"/api/analyses/{ref}/baseline",
                         json={"baseline": True, "admin_password": ADMIN_PW})
    assert answer.status_code == 400, answer.text
    assert client.mod.store.baselines() == []


def test_nobody_can_override_without_an_administrator_password(
        tmp_path, monkeypatch):
    """The same rule as delete: no administrator configured, no override."""
    client = _make_client(tmp_path, monkeypatch,
                          PHANTOMQA_ADMIN_PASSWORD_HASH="")
    aid = _proposed(client, "NO-ADMIN", passed=False)
    plain = _confirm_c(client, aid, save_profile=True).json()
    assert plain["profile_blocked_by_quality"] is True
    assert plain["override_available"] is False, (
        "the page would offer a password field nobody can fill in")

    client.mod.store.update(aid, stage="C")
    forced = _confirm_c(client, aid, save_profile=True,
                        admin_password="anything", reason=REASON)
    assert forced.status_code == 403, forced.text
    assert client.mod.store.get_phantom_profile("NO-ADMIN") is None

    ref = _reference_candidate(client, "NO-ADMIN-REF")
    refused = client.post(f"/api/analyses/{ref}/baseline",
                          json={"baseline": True})
    assert refused.json()["override_available"] is False
    forced = client.post(f"/api/analyses/{ref}/baseline",
                         json={"baseline": True, "admin_password": "anything",
                               "reason": REASON})
    assert forced.status_code == 403, forced.text
    assert client.mod.store.baselines() == []


def test_a_password_sent_for_a_scan_that_passed_is_not_even_checked(client,
                                                                   tmp_path):
    """There is nothing to overrule, so a stray password must neither be
    needed nor count as a failed attempt towards the lockout."""
    aid = _proposed(client, "PASSED")
    answer = _confirm_c(client, aid, save_profile=True,
                        admin_password="wrong-password", reason="")
    assert answer.status_code == 200, answer.text
    assert answer.json()["profile_saved"] is True
    assert answer.json()["profile"]["quality_override"] is False
    assert not [l for l in _audit_lines(tmp_path, "phantom_profile")
                if "outcome=denied" in l]


@pytest.mark.parametrize("lock", ["finalised", "signed off"])
def test_a_locked_record_cannot_store_its_points_even_with_an_override(
        client, lock):
    """The override answers the quality check, not the lock: a signed-off or
    finalised scan still may not redefine where every later scan starts."""
    aid = _proposed(client, f"LOCK-{lock[:3]}", passed=False)
    if lock == "finalised":
        client.mod.store.set_finalized(aid, "qa")
    else:
        client.mod.store.set_validation(aid, "validated", "Dr Test")
    for body in ({"save_profile": True},
                 {"save_profile": True, "admin_password": ADMIN_PW,
                  "reason": REASON}):
        answer = _confirm_c(client, aid, **body)
        assert answer.status_code == 409, answer.text
    assert client.mod.store.get_phantom_profile(f"LOCK-{lock[:3]}") is None


# -------------------------------------------------------------- the page

STATIC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "phantom_qa", "webapp", "static")


def _read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def app_js():
    return _read("app.js")


@pytest.fixture(scope="module")
def index_html():
    return _read("index.html")


def _fn(app_js, name, end_marker):
    start = app_js.index(f"function {name}(")
    return app_js[start:app_js.index(end_marker, start)]


def test_the_step_asks_in_plain_words(app_js):
    block = _fn(app_js, "layoutSaveBlock", "\nfunction reportLayoutSave(")
    assert 'id="cb-save-layout"' in block
    assert "Use these measuring points for future scans of phantom" in block
    assert "${layoutSaveBlock()}" in _fn(app_js, "stageC",
                                         "\nasync function stepHistory(")


def test_the_box_is_ticked_by_the_same_rule_as_the_server(app_js):
    default = _fn(app_js, "layoutSaveDefault", "\nfunction layoutSaveChoice(")
    assert "return !prof || prof.source_analysis_id === S.aid;" in default


def test_the_page_always_sends_the_answer(app_js):
    """An omitted flag is the server's fallback for old pages, not something
    the current page may rely on."""
    stage_c = _fn(app_js, "stageC", "\nasync function stepHistory(")
    assert '{ stage: "C", save_profile: save }' in stage_c
    assert not re.search(r'\{\s*stage:\s*"C"\s*\}', app_js), (
        "a step C confirm still leaves the choice to the server")
    override = _fn(app_js, "overrideLayoutSave", "\n/* The low-contrast block")
    assert 'stage: "C", save_profile: true' in override


def test_where_it_cannot_be_offered_the_step_says_why(app_js):
    block = _fn(app_js, "layoutSaveBlock", "\nfunction reportLayoutSave(")
    # locked, no phantom named, failed the quality check — each explained
    assert "recordLocked()" in block and "signed off" in block \
        and "finalised" in block
    assert "No phantom is named" in block
    assert "qualityRefusesReference(r.quality)" in block
    assert "failedChecksList(r.quality)" in block
    assert 'id="btn-layout-override"' in block
    assert "Use anyway" in block and "(administrator)…" in block
    locked = _fn(app_js, "recordLocked", "\n/* The choice itself")
    assert "signedOff()" in locked and "finalized_at" in locked


def test_the_answer_survives_a_re_render_but_not_a_different_analysis(app_js):
    """Every edit in step C re-renders it; the box must not tick itself again
    behind the operator's back."""
    choice = _fn(app_js, "layoutSaveChoice", "\n/* Signed off or finalised")
    assert "c.aid === S.aid && c.phantom === phantom" in choice
    assert "S.layoutSaveChoice = { aid: S.aid, on: saveBox.checked," in app_js
    reset = _fn(app_js, "clearAnalysisState", "\nconst $ = ")
    assert "S.layoutSaveChoice = null" in reset


def test_after_storing_the_page_knows_the_points_are_its_own(app_js):
    """Otherwise coming back to step C would show the box unticked, as if
    somebody else's points were at stake."""
    report = _fn(app_js, "reportLayoutSave", "\n/* The administrator's way")
    assert "source_analysis_id: S.aid" in report


def test_the_override_panel_exists_and_asks_for_both(app_js, index_html):
    for el_id in ("override-backdrop", "override-title", "override-body",
                  "override-reason", "override-pw", "override-error",
                  "override-go", "override-cancel"):
        assert f'id="{el_id}"' in index_html, el_id
    panel = _fn(app_js, "adminOverride", "\n/* Making a scan that failed")
    assert 'api("api/validation_policy")' in panel, (
        "nobody should type a password where no administrator is configured")
    assert "admin_password:" in panel and "reason" in panel
    assert "failedChecksList(quality)" in panel
    # a wrong password must come back to the panel, not sign the operator out
    assert app_js.count("{ adminAuth: true }") >= 4


def test_a_refused_reference_offers_the_override(app_js):
    assert "err.qualityRefused = { summary:" in app_js
    toggle = _fn(app_js, "toggleBaseline", "\n/* ---- Stage F ---- */")
    assert "e.qualityRefused" in toggle and "overrideBaseline(" in toggle
    assert "qualityRefusesReference(S.record && S.record.quality)" in toggle
    block = _fn(app_js, "baselineBlock", "\nasync function toggleBaseline(")
    assert "qualityRefusesReference(r.quality)" in block
    assert "Use anyway" in block
    assert "err.qualityRefused" in app_js.split("a.base")[-1], (
        "the History star still shows a bare refusal")
