"""Three things that stayed behind after the thing they described was gone.

None of these was reported from the field — they were found while reading the
code for the reported defects, and each one shows something that is no longer
true: a version stamp naming the code that did not produce the numbers, a
green verdict for a measurement that had been thrown away, and a picture of a
record that had been deleted.

They are grouped because they share a shape. State that describes something
else has to be written in the same breath as the thing it describes, or it
goes on being believed.
"""

import pytest

from test_authorization import ADMIN_PW
from test_unusable_exposures import SYNTHETIC, _png16

_variant = iter(range(20_000, 29_000))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


def _uploaded(client, phantom="STALE"):
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses", files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": phantom})
    assert up.status_code == 200, up.text
    return up.json()["analyses"][0]["id"]


def _measured(client, phantom="STALE"):
    aid = _uploaded(client, phantom)
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    client.post(f"/api/analyses/{aid}/confirm",
                json={"stage": "C", "save_profile": False})
    client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    return aid


# ------------------------------------------- 1 · the version that measured

def test_measuring_stamps_the_version_that_did_the_measuring(client):
    """The stamp was written at upload and never again.

    So a record measured after an upgrade carried the numbers of the new code
    under the version of the old — and `reanalyze` skips records whose stamp
    already matches, which meant those records were left out of the very sweep
    that exists to bring them up to date."""
    aid = _uploaded(client)
    client.mod.store.update(aid, algo_version="0.0.1-old",
                            pdef_version="0.0.1-old")

    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    client.post(f"/api/analyses/{aid}/confirm",
                json={"stage": "C", "save_profile": False})
    client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})

    rec = client.get(f"/api/analyses/{aid}").json()
    assert rec["algo_version"] == client.mod.ALGO_VERSION
    assert rec["pdef_version"] == client.mod.pdef.version


def test_the_stamp_and_the_results_are_written_together(client):
    """Two writes could leave the pair disagreeing if one of them failed."""
    aid = _measured(client, phantom="STALE-PAIR")
    rec = client.mod.store.get(aid)
    assert rec["results"] is not None
    assert rec["algo_version"] == client.mod.ALGO_VERSION


# ------------------------------------------- 2 · the verdict that was kept

def test_re_registering_withdraws_the_verdict_with_the_results(client):
    """A green chip for a measurement that no longer exists.

    Re-registering throws away the geometry and the results — it has to,
    because both are expressed in pixels derived from the old transform — but
    the overall verdict stayed, so History and the label counts went on
    reporting a pass for a record holding nothing."""
    aid = _measured(client, phantom="STALE-CHIP")
    verdict = client.mod.store.get(aid)["status"]
    assert verdict in ("pass", "fail", "warn"), verdict

    assert client.post(f"/api/analyses/{aid}/register", json={}
                       ).status_code == 200
    rec = client.mod.store.get(aid)
    assert rec["results"] is None
    assert rec["status"] == "draft", "a verdict outlived the results it described"


def test_the_withdrawn_verdict_is_gone_from_the_collection_views(client):
    """The chip is one symptom; the counts are the other."""
    aid = _measured(client, phantom="STALE-COUNTS")
    client.post(f"/api/analyses/{aid}/register", json={})

    rows = client.get("/api/analyses").json()["analyses"]
    assert [r["status"] for r in rows] == ["draft"]
    assert client.get("/api/trends").json()["analyses"] == []


def test_a_record_that_is_measured_again_gets_its_verdict_back(client):
    """Withdrawing must not be a one-way door."""
    aid = _measured(client, phantom="STALE-AGAIN")
    client.post(f"/api/analyses/{aid}/register", json={})
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    client.post(f"/api/analyses/{aid}/confirm",
                json={"stage": "C", "save_profile": False})
    client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    assert client.mod.store.get(aid)["status"] != "draft"


# ------------------------------------------- 3 · the picture that outlived it

def test_the_image_of_a_deleted_record_is_not_served_from_memory(client):
    """Every other route reads the record first. This one answered from its
    own cache, so a delete handled by one worker left the others still handing
    out the picture — and the image is now privately cacheable in the browser,
    which would have made it stick."""
    aid = _measured(client, phantom="STALE-IMAGE")
    assert client.get(f"/api/analyses/{aid}/image.png").status_code == 200

    client.post(f"/api/analyses/{aid}/delete",
                json={"admin_password": ADMIN_PW, "reason": "regression test"})
    assert client.get(f"/api/analyses/{aid}/image.png").status_code == 404


def test_a_discarded_record_takes_its_picture_with_it(client):
    """The password-free route out has to behave the same way."""
    aid = _uploaded(client, phantom="STALE-DISCARD")
    client.get(f"/api/analyses/{aid}/image.png?scale=200")
    client.post(f"/api/analyses/{aid}/discard", json={"confirm": True})
    assert client.get(f"/api/analyses/{aid}/image.png?scale=200"
                      ).status_code == 404


def test_every_rendered_window_of_it_is_dropped_not_just_the_one_asked_for(
        client):
    """The cache is keyed by window and scale, so one entry per view the
    operator happened to look at. Dropping only the key just requested would
    leave the rest to be served later."""
    aid = _measured(client, phantom="STALE-WINDOWS")
    for wc, ww in ((1000, 500), (2000, 900)):
        assert client.get(f"/api/analyses/{aid}/image.png?wc={wc}&ww={ww}"
                          ).status_code == 200
    assert any(k[0] == aid for k in client.mod._img_cache)

    client.post(f"/api/analyses/{aid}/delete",
                json={"admin_password": ADMIN_PW, "reason": "regression test"})
    client.get(f"/api/analyses/{aid}/image.png")
    assert not [k for k in client.mod._img_cache if k[0] == aid], \
        "rendered views of a deleted record stayed in memory"
    assert aid not in client.mod._scans


def test_a_live_record_still_serves_its_image_from_the_cache(client):
    """The check is one indexed lookup; it must not have cost the cache."""
    aid = _measured(client, phantom="STALE-LIVE")
    first = client.get(f"/api/analyses/{aid}/image.png?scale=200")
    second = client.get(f"/api/analyses/{aid}/image.png?scale=200")
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
