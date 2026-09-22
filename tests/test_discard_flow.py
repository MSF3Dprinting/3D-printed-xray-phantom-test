"""Throwing away work that was never finished.

Asked for after the first field test: "Before the phantom is finalized, user
should be able to delete it easily without password just with confirmation,
sometimes we make mistake in the process and it is just easier to delete it."

The field audit log shows the cost of not having it. The same file was deleted
and re-uploaded four times inside seventy minutes, each cycle a multi-megabyte
transfer over a field link, with reasons recorded as "Mark alighment
correction", "Failed manual point detection" and "Artifact in software - bug".
Every one of those needed the administrator password, and on an installation
with none configured they could not have been cleared at all.

The boundary is what makes this safe to give away. Nothing about an unfinished
record has been decided, so removing it destroys no judgement. The moment
something HAS been decided — finalised, ruled on, or made the reference its
phantom is judged against — this refuses, and the administrator-gated delete
applies exactly as before.
"""

import pytest

from phantom_qa.store import ProtectedAnalysis
from test_authorization import ADMIN_PW
from test_unusable_exposures import SYNTHETIC, _png16

_variant = iter(range(40_000, 50_000))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


def _analysis(client, phantom="DISCARD", finish=False):
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses",
                     files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": phantom, "operator": "Stacy"})
    aid = up.json()["analyses"][0]["id"]
    if finish:
        client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
        client.post(f"/api/analyses/{aid}/propose", json={})
        client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    return aid


# ------------------------------------------------------- what it makes possible

def test_an_unfinished_analysis_goes_with_a_confirmation_alone(client):
    aid = _analysis(client)
    answer = client.post(f"/api/analyses/{aid}/discard", json={"confirm": True})
    assert answer.status_code == 200, answer.text
    assert client.get(f"/api/analyses/{aid}").status_code == 404


def test_a_computed_but_unfinalised_analysis_can_still_go(client):
    """Results are not a decision. Pressing Finalize is."""
    aid = _analysis(client, finish=True)
    assert client.post(f"/api/analyses/{aid}/discard",
                       json={"confirm": True}).status_code == 200


def test_discarding_works_with_no_administrator_password_configured(
        tmp_path, monkeypatch):
    """The case that was completely stuck before.

    With no admin password the old delete refused outright, so a mistaken
    upload could not be removed by anybody."""
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch, PHANTOMQA_ADMIN_PASSWORD_HASH="")
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    aid = _analysis(c)
    assert c.post(f"/api/analyses/{aid}/discard",
                  json={"confirm": True}).status_code == 200
    # and the administrator path is still refused, as it always was
    other = _analysis(c)
    assert c.post(f"/api/analyses/{other}/delete",
                  json={"admin_password": "x", "reason": "testing"}
                  ).status_code == 403


def test_a_bare_post_does_not_delete_anything(client):
    aid = _analysis(client)
    assert client.post(f"/api/analyses/{aid}/discard", json={}).status_code == 400
    assert client.get(f"/api/analyses/{aid}").status_code == 200


def test_the_record_leaves_history_trends_and_the_duplicate_check(client):
    """"It should then not appear in the history and trend." """
    aid = _analysis(client, phantom="DISCARD-GONE", finish=True)
    client.post(f"/api/analyses/{aid}/discard", json={"confirm": True})

    listed = [a["id"] for a in client.get("/api/analyses").json()["analyses"]]
    assert aid not in listed
    assert aid not in client.get("/api/trends").text
    assert "DISCARD-GONE" not in client.get("/api/labels").text
    assert client.get(f"/api/analyses/{aid}/export.csv").status_code == 404


def test_the_same_file_can_be_uploaded_again_afterwards(client):
    """Discarding must really release the file, not merely hide the record.

    Otherwise the operator meets "this file has already been analysed" about
    something they just threw away."""
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    payload = _png16(pixels)
    first = client.post("/api/analyses", files={"file": ("s.png", payload)},
                        data={"site": "T", "phantom": "AGAIN"})
    aid = first.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/discard", json={"confirm": True})
    again = client.post("/api/analyses", files={"file": ("s.png", payload)},
                        data={"site": "T", "phantom": "AGAIN"})
    assert again.status_code == 200, again.text


# ------------------------------------------------------------ where it stops

@pytest.mark.parametrize("protect", ["finalize", "ruling", "baseline"])
def test_a_decided_analysis_is_refused(client, protect):
    aid = _analysis(client, phantom=f"KEEP-{protect}", finish=True)
    if protect == "finalize":
        assert client.post(f"/api/analyses/{aid}/finalize",
                           json={}).status_code == 200
    elif protect == "ruling":
        assert client.post(f"/api/analyses/{aid}/validation",
                           json={"status": "validated", "validated_by": "S",
                                 "comment": "ok",
                                 "admin_password": ADMIN_PW}).status_code == 200
    else:
        # Through the store, not the endpoint: a synthetic image cannot pass
        # the acquisition gate, which refuses to let it become a reference.
        # What is under test here is the protection, not how it was acquired.
        client.mod.store.set_baseline(aid, True)

    refused = client.post(f"/api/analyses/{aid}/discard", json={"confirm": True})
    assert refused.status_code == 409, refused.text
    assert "administrator" in refused.json()["detail"]
    assert client.get(f"/api/analyses/{aid}").status_code == 200, \
        "a refused discard must leave the record untouched"


def test_the_administrator_can_still_remove_a_decided_analysis(client):
    """The old path is unchanged; this only adds a lighter one beside it."""
    aid = _analysis(client, phantom="ADMIN-STILL", finish=True)
    client.post(f"/api/analyses/{aid}/finalize", json={})
    assert client.post(f"/api/analyses/{aid}/delete",
                       json={"admin_password": ADMIN_PW,
                             "reason": "withdrawn by the site"}
                       ).status_code == 200
    assert client.get(f"/api/analyses/{aid}").status_code == 404


def test_the_protection_check_happens_inside_the_transaction(client):
    """A colleague finalising between the dialog and the click must win.

    Checked before the lock, both could pass and the record would go anyway."""
    import inspect
    from phantom_qa.store import Store
    body = inspect.getsource(Store.delete)
    body = body[body.index("with self.write_transaction()"):]
    assert "ProtectedAnalysis" in body

    aid = _analysis(client, finish=True)
    client.post(f"/api/analyses/{aid}/finalize", json={})
    with pytest.raises(ProtectedAnalysis) as raised:
        client.mod.store.delete(aid, only_if_unprotected=True)
    assert "finalized" in raised.value.reasons


def test_an_ordinary_delete_still_ignores_protection(client):
    """only_if_unprotected is opt-in; re-analysis and the CLI must not change."""
    aid = _analysis(client, finish=True)
    client.post(f"/api/analyses/{aid}/finalize", json={})
    assert client.mod.store.delete(aid)["deleted"] is True


# ------------------------------------------------------------- what it records

def test_a_discard_is_written_to_the_audit_log(client, tmp_path):
    """One `grep event=delete` must still show everything that removed data."""
    aid = _analysis(client, phantom="AUDITED")
    client.post(f"/api/analyses/{aid}/discard", json={"confirm": True})
    with open(tmp_path / "logs" / "audit.log", encoding="utf-8") as f:
        lines = [l for l in f if "event=delete" in l]
    assert lines, "the discard left no trace in the audit log"
    last = lines[-1]
    assert '"mode":"discard"' in last and "outcome=ok" in last
    assert aid in last and "AUDITED" in last
    assert "sha256" in last, "the fingerprint of what was destroyed"


def test_a_refused_discard_is_recorded_too(client, tmp_path):
    aid = _analysis(client, finish=True)
    client.post(f"/api/analyses/{aid}/finalize", json={})
    client.post(f"/api/analyses/{aid}/discard", json={"confirm": True})
    with open(tmp_path / "logs" / "audit.log", encoding="utf-8") as f:
        assert any('"mode":"discard"' in l and "outcome=refused" in l
               for l in f)


# ------------------------------------------- what the confirmation panel reads

def test_the_panel_learns_which_dialog_to_show_without_the_whole_record(client):
    """It used to fetch the entire record — 195 kB — to read a hundred bytes."""
    aid = _analysis(client, phantom="IMPACT")
    impact = client.get(f"/api/analyses/{aid}/delete_impact")
    assert impact.status_code == 200
    body = impact.json()
    assert body["mode"] == "confirm"
    assert body["protection"] == []
    assert body["source_name"] and "has_results" in body
    assert "geometry" not in body and "results" not in body, \
        "the impact summary must stay small"
    assert len(impact.content) < 1000, len(impact.content)


def test_the_panel_is_told_when_the_administrator_password_is_needed(client):
    aid = _analysis(client, finish=True)
    client.post(f"/api/analyses/{aid}/finalize", json={})
    body = client.get(f"/api/analyses/{aid}/delete_impact").json()
    assert body["mode"] == "admin"
    assert body["protection"] == ["finalized"]
    assert "finalised" in " ".join(body["protection_reasons"])


def test_the_panel_says_when_nobody_can_remove_it(tmp_path, monkeypatch):
    """Finalised, on an installation with no administrator password."""
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch, PHANTOMQA_ADMIN_PASSWORD_HASH="")
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    aid = _analysis(c, finish=True)
    c.post(f"/api/analyses/{aid}/finalize", json={})
    assert c.get(f"/api/analyses/{aid}/delete_impact").json()["mode"] == "disabled"


# ------------------------------------------- the phantom's shared measuring points

def _with_layout(client, aid, phantom, mark):
    """Take an analysis to step C and let it store the phantom's layout."""
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    client.mod.store.save_phantom_profile(
        phantom, {"rois": {mark: 1}}, source_analysis_id=aid,
        updated_by="stacy")


def test_discarding_puts_back_the_layout_it_replaced(client):
    """A layout is stored at step C, before anyone knows the analysis was good.

    Throwing that analysis away used to leave its measuring points standing as
    the default for every later scan of the phantom — the mistake outliving the
    record, silently, on everybody else's work."""
    first = _analysis(client, phantom="LAYOUT")
    _with_layout(client, first, "LAYOUT", "good")
    second = _analysis(client, phantom="LAYOUT")
    _with_layout(client, second, "LAYOUT", "mistaken")

    assert client.mod.store.get_phantom_profile("LAYOUT")["layout"] \
        == {"rois": {"mistaken": 1}}

    answer = client.post(f"/api/analyses/{second}/discard", json={"confirm": True})
    assert answer.status_code == 200
    assert answer.json()["layout_restored"] is True
    profile = client.mod.store.get_phantom_profile("LAYOUT")
    assert profile["layout"] == {"rois": {"good": 1}}
    assert profile["source_analysis_id"] == first


def test_with_nothing_to_fall_back_on_the_layout_is_forgotten(client):
    """Detection from scratch beats marks nobody trusts."""
    aid = _analysis(client, phantom="LAYOUT-ONLY")
    _with_layout(client, aid, "LAYOUT-ONLY", "mistaken")
    answer = client.post(f"/api/analyses/{aid}/discard", json={"confirm": True})
    assert answer.json()["layout_deleted"] is True
    assert client.mod.store.get_phantom_profile("LAYOUT-ONLY") is None


def test_a_layout_stored_by_another_analysis_is_left_alone(client):
    """Discarding one record must not disturb marks it did not write."""
    owner = _analysis(client, phantom="LAYOUT-OTHER")
    _with_layout(client, owner, "LAYOUT-OTHER", "owned")
    bystander = _analysis(client, phantom="LAYOUT-OTHER")

    answer = client.post(f"/api/analyses/{bystander}/discard",
                         json={"confirm": True})
    assert answer.json()["layout_restored"] is False
    assert answer.json()["layout_deleted"] is False
    assert client.mod.store.get_phantom_profile("LAYOUT-OTHER")["layout"] \
        == {"rois": {"owned": 1}}


def test_reconfirming_the_same_analysis_does_not_lose_the_predecessor(client):
    """Operators nudge marks and re-confirm repeatedly.

    If each re-confirm rotated the current layout into the previous slot, the
    history worth keeping would be gone after the first nudge — overwritten by
    the same analysis's own earlier attempt."""
    first = _analysis(client, phantom="LAYOUT-REDO")
    _with_layout(client, first, "LAYOUT-REDO", "good")
    second = _analysis(client, phantom="LAYOUT-REDO")
    _with_layout(client, second, "LAYOUT-REDO", "try-one")
    _with_layout(client, second, "LAYOUT-REDO", "try-two")   # same analysis

    client.post(f"/api/analyses/{second}/discard", json={"confirm": True})
    assert client.mod.store.get_phantom_profile("LAYOUT-REDO")["layout"] \
        == {"rois": {"good": 1}}, "the predecessor was overwritten by a re-confirm"
