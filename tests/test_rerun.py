"""Analysing a stored scan again, in place.

Asked for after the first field test: "We should be able to redo the whole
analysis for every phantom. So open any analysis and run it again from the UI.
After finalizing, consider doing it after admin password is entered."

The server could always do this — the scan file is kept — but nothing in the
interface reached it. A completed record opened on the last step, which has no
way back, so the only route to fresh numbers was to upload the same file again
and press "analyse anyway": a multi-minute transfer on a field link, and a
second record double-counting in every trend.

Two things make rewriting a finished record safe enough to offer. The previous
state is kept as a revision, so an interrupted re-run can be undone; and a
record that had been finalised or signed off is reopened explicitly rather than
quietly, because a ruling that outlived the numbers it was given for would be
worse than no ruling at all.
"""

import pytest

from test_authorization import ADMIN_PW
from test_unusable_exposures import SYNTHETIC, _png16

_variant = iter(range(60_000, 70_000))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


def _measured(client, phantom="RERUN"):
    """An analysis taken all the way to results."""
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses",
                     files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": phantom, "operator": "Stacy"})
    aid = up.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    client.post(f"/api/analyses/{aid}/confirm",
                json={"stage": "C", "save_profile": False})
    client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    return aid


# ------------------------------------------------------------- no upload again

def test_an_open_analysis_is_rerun_without_a_password(client):
    aid = _measured(client)
    answer = client.post(f"/api/analyses/{aid}/rerun",
                         json={"start": "registration"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["stage"] == "A"

    record = client.get(f"/api/analyses/{aid}").json()
    assert record["results"] is None, "the old numbers are cleared"
    assert record["status"] == "draft"


def test_the_record_keeps_its_identity(client):
    """Same id, same labels, same dates, same file — that is the whole point.

    A second record would double-count in every trend, which is exactly what
    re-uploading produced."""
    aid = _measured(client, phantom="RERUN-IDENTITY")
    before = client.get(f"/api/analyses/{aid}").json()
    client.post(f"/api/analyses/{aid}/rerun", json={"start": "registration"})
    after = client.get(f"/api/analyses/{aid}").json()
    for field in ("id", "sha256", "source_name", "created_at", "acquired_at",
                  "site", "phantom", "operator"):
        assert after[field] == before[field], field
    assert len(client.get("/api/analyses").json()["analyses"]) == 1


@pytest.mark.parametrize("start,expect_stage,keeps_geometry", [
    ("registration", "A", False),
    ("points", "C", True),
    ("results", "C", True),
])
def test_each_starting_point_keeps_what_it_says(client, start, expect_stage,
                                                keeps_geometry):
    aid = _measured(client, phantom=f"RERUN-{start}")
    answer = client.post(f"/api/analyses/{aid}/rerun", json={"start": start})
    assert answer.status_code == 200, answer.text
    assert answer.json()["stage"] == expect_stage
    record = client.get(f"/api/analyses/{aid}").json()
    assert bool(record["geometry"]) is keeps_geometry


def test_starting_from_points_needs_points_to_start_from(client):
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses", files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": "RERUN-NOGEOM"})
    aid = up.json()["analyses"][0]["id"]
    answer = client.post(f"/api/analyses/{aid}/rerun", json={"start": "points"})
    assert answer.status_code == 400
    assert "registration" in answer.json()["detail"]


def test_an_unknown_starting_point_is_refused(client):
    aid = _measured(client)
    assert client.post(f"/api/analyses/{aid}/rerun",
                       json={"start": "everything"}).status_code == 400


# --------------------------------------------------- what protection changes

def test_a_finalised_analysis_needs_the_administrator_password(client):
    aid = _measured(client, phantom="RERUN-FINAL")
    client.post(f"/api/analyses/{aid}/finalize", json={})

    assert client.post(f"/api/analyses/{aid}/rerun",
                       json={"start": "registration"}).status_code == 401
    assert client.post(f"/api/analyses/{aid}/rerun",
                       json={"start": "registration",
                             "admin_password": "wrong"}).status_code == 401
    # right password, no reason
    assert client.post(f"/api/analyses/{aid}/rerun",
                       json={"start": "registration",
                             "admin_password": ADMIN_PW}).status_code == 400
    ok = client.post(f"/api/analyses/{aid}/rerun",
                     json={"start": "registration", "admin_password": ADMIN_PW,
                           "reason": "algorithm updated since"})
    assert ok.status_code == 200, ok.text
    assert client.get(f"/api/analyses/{aid}").json()["finalized_at"] == "", \
        "a re-run reopens the record"


def test_rerunning_a_signed_off_analysis_withdraws_the_ruling(client):
    """A ruling refers to numbers. Replace the numbers and it must not stand.

    Withdrawn explicitly and reported, never silently: the approver has to be
    asked again once there is something new to approve."""
    aid = _measured(client, phantom="RERUN-SIGNED")
    client.post(f"/api/analyses/{aid}/validation",
                json={"status": "validated", "validated_by": "Silvestr",
                      "comment": "fine", "admin_password": ADMIN_PW})
    answer = client.post(f"/api/analyses/{aid}/rerun",
                         json={"start": "results", "admin_password": ADMIN_PW,
                               "reason": "recheck after update"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["withdrew_ruling"] == "validated"
    assert client.get(f"/api/analyses/{aid}").json()["validation_status"] == ""


def test_the_reference_flag_is_kept_and_reported(client):
    """Clearing it would leave the phantom comparing against nothing while the
    re-run is in progress. The operator is told instead."""
    aid = _measured(client, phantom="RERUN-BASE")
    client.mod.store.set_baseline(aid, True)
    answer = client.post(f"/api/analyses/{aid}/rerun",
                         json={"start": "results", "admin_password": ADMIN_PW,
                               "reason": "re-measuring the reference"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["was_baseline"] is True
    assert client.get(f"/api/analyses/{aid}").json()["is_baseline"] == 1


def test_no_administrator_password_configured_means_no_rerun_once_finalised(
        tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch, PHANTOMQA_ADMIN_PASSWORD_HASH="")
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    aid = _measured(c, phantom="RERUN-NOADMIN")
    assert c.post(f"/api/analyses/{aid}/rerun",
                  json={"start": "results"}).status_code == 200, \
        "an open record is still freely re-runnable"
    c.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    c.post(f"/api/analyses/{aid}/finalize", json={})
    assert c.post(f"/api/analyses/{aid}/rerun",
                  json={"start": "results"}).status_code == 403


# ------------------------------------------------------------ undoing a re-run

def test_the_previous_state_is_kept_and_can_be_restored(client):
    """A dropped connection mid-re-run must not cost the old numbers."""
    aid = _measured(client, phantom="RERUN-UNDO")
    client.post(f"/api/analyses/{aid}/finalize", json={})
    before = client.get(f"/api/analyses/{aid}").json()

    client.post(f"/api/analyses/{aid}/rerun",
                json={"start": "registration", "admin_password": ADMIN_PW,
                      "reason": "checking something"})
    assert client.get(f"/api/analyses/{aid}").json()["results"] is None

    restored = client.post(f"/api/analyses/{aid}/rerun/cancel", json={})
    assert restored.status_code == 200, restored.text
    after = client.get(f"/api/analyses/{aid}").json()
    assert after["results"] == before["results"]
    assert after["status"] == before["status"]
    assert after["finalized_at"] == before["finalized_at"], \
        "the record was finalised before; cancelling restores that too"


def test_cancelling_restores_the_ruling_that_was_given_for_those_numbers(client):
    aid = _measured(client, phantom="RERUN-UNDO-SIGNED")
    client.post(f"/api/analyses/{aid}/validation",
                json={"status": "conditionally_validated",
                      "validated_by": "Silvestr", "comment": "with a caveat",
                      "admin_password": ADMIN_PW})
    client.post(f"/api/analyses/{aid}/rerun",
                json={"start": "results", "admin_password": ADMIN_PW,
                      "reason": "second opinion"})
    client.post(f"/api/analyses/{aid}/rerun/cancel", json={})
    record = client.get(f"/api/analyses/{aid}").json()
    assert record["validation_status"] == "conditionally_validated"
    assert record["validated_by"] == "Silvestr"
    assert record["validation_comment"] == "with a caveat"


def test_cancelling_is_refused_once_new_numbers_exist(client):
    """By then there is a real choice to make, not an interruption to undo."""
    aid = _measured(client, phantom="RERUN-TOOLATE")
    client.post(f"/api/analyses/{aid}/rerun", json={"start": "results"})
    client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    answer = client.post(f"/api/analyses/{aid}/rerun/cancel", json={})
    assert answer.status_code == 409
    assert "throw those away" in answer.json()["detail"]


def test_cancelling_with_nothing_to_restore_is_a_clean_refusal(client):
    aid = _measured(client)
    assert client.post(f"/api/analyses/{aid}/rerun/cancel",
                       json={}).status_code == 409


def test_revisions_are_listed_without_their_contents(client):
    """The snapshots are large; the list is what the interface shows."""
    aid = _measured(client, phantom="RERUN-LIST")
    client.post(f"/api/analyses/{aid}/rerun",
                json={"start": "results", "reason": "first"})
    listed = client.get(f"/api/analyses/{aid}/revisions").json()["revisions"]
    assert len(listed) == 1
    entry = listed[0]
    assert entry["rev"] == 1 and entry["mode"] == "results"
    assert entry["reason"] == "first"
    assert "snapshot_z" not in entry and "results" not in entry


def test_old_revisions_are_trimmed_but_the_first_is_kept(client):
    """The original numbers are the ones worth keeping longest."""
    from phantom_qa.store import Store
    aid = _measured(client, phantom="RERUN-DEPTH")
    for i in range(Store.REVISION_DEPTH + 3):
        client.post(f"/api/analyses/{aid}/rerun",
                    json={"start": "results", "reason": f"round {i}"})
        client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    revs = [r["rev"] for r in
            client.get(f"/api/analyses/{aid}/revisions").json()["revisions"]]
    assert 1 in revs, "revision 1 must never be trimmed"
    assert len(revs) <= Store.REVISION_DEPTH + 1


def test_revisions_go_when_the_record_goes(client):
    aid = _measured(client, phantom="RERUN-CASCADE")
    client.post(f"/api/analyses/{aid}/rerun", json={"start": "results"})
    client.mod.store.delete(aid)
    assert client.mod.store.list_revisions(aid) == []


# ------------------------------------------------------------------ integrity

def test_a_scan_that_no_longer_matches_its_fingerprint_is_not_rerun(client):
    """New numbers must not be computed from a file that is not the one the
    old numbers came from."""
    aid = _measured(client, phantom="RERUN-TAMPERED")
    with open(client.mod.store.upload_path(aid), "wb") as f:
        f.write(b"not the scan any more")
    answer = client.post(f"/api/analyses/{aid}/rerun", json={"start": "results"})
    assert answer.status_code == 409
    assert "fingerprint" in answer.json()["detail"]
    assert client.get(f"/api/analyses/{aid}").json()["results"] is not None, \
        "a refused re-run must not have cleared anything"


def test_the_whole_thing_is_written_to_the_audit_log(client, tmp_path):
    aid = _measured(client, phantom="RERUN-AUDIT")
    client.post(f"/api/analyses/{aid}/finalize", json={})
    client.post(f"/api/analyses/{aid}/rerun",
                json={"start": "points", "admin_password": ADMIN_PW,
                      "reason": "checked against the new definition"})
    with open(tmp_path / "logs" / "audit.log", encoding="utf-8") as f:
        lines = [l for l in f if "event=rerun" in l]
    assert lines
    assert "checked against the new definition" in lines[-1]
    assert "finalized" in lines[-1], "what protection was overridden"


# ------------------------------------------- what "finalised" now prevents

def test_a_finalised_analysis_refuses_the_edits_that_used_to_go_through(client):
    """Finalising protected nothing at all before.

    An ROI nudge on a finalised record dropped its results, set it back to
    draft and removed it from every trend and export — with no warning, and
    nothing on the record to show it had happened."""
    aid = _measured(client, phantom="LOCK-EDIT")
    geometry = client.get(f"/api/analyses/{aid}").json()["geometry"]
    roi = geometry["uniformity"]["squares"][0]["roi"]
    client.post(f"/api/analyses/{aid}/finalize", json={})

    refused = client.post(f"/api/analyses/{aid}/roi",
                          json={"roi_id": roi["id"],
                                "center_px": [roi["center_px"][0] + 3,
                                              roi["center_px"][1]]})
    assert refused.status_code == 409, refused.text
    assert "Re-run" in refused.json()["detail"], \
        "the refusal must name the way forward"
    assert client.get(f"/api/analyses/{aid}").json()["results"] is not None


def test_confirming_step_c_on_a_locked_record_cannot_rewrite_the_layout(client):
    """It writes the phantom's SHARED marks — an edit beyond this record."""
    aid = _measured(client, phantom="LOCK-LAYOUT")
    client.post(f"/api/analyses/{aid}/finalize", json={})
    refused = client.post(f"/api/analyses/{aid}/confirm", json={"stage": "C"})
    assert refused.status_code == 409
    assert "measuring points" in refused.json()["detail"]


def test_previewing_on_a_locked_record_cannot_change_its_stored_sid(client):
    """The report prints the stored SID; the signed verdict used the old one."""
    aid = _measured(client, phantom="LOCK-SID")
    client.post(f"/api/analyses/{aid}/finalize", json={})
    before = client.get(f"/api/analyses/{aid}").json()["sid_mm"]

    preview = client.post(f"/api/analyses/{aid}/compute_preview",
                          json={"tests": ["geometry"], "sid_mm": 1234.0})
    assert preview.status_code == 200, "reading must still be allowed"
    assert client.get(f"/api/analyses/{aid}").json()["sid_mm"] == before


def test_reopening_by_re_running_makes_it_editable_again(client):
    """The lock is not a dead end — re-run is the documented way through it."""
    aid = _measured(client, phantom="LOCK-REOPEN")
    client.post(f"/api/analyses/{aid}/finalize", json={})
    client.post(f"/api/analyses/{aid}/rerun",
                json={"start": "points", "admin_password": ADMIN_PW,
                      "reason": "needs a correction"})
    assert client.post(f"/api/analyses/{aid}/confirm",
                       json={"stage": "C", "save_profile": False}
                       ).status_code == 200
