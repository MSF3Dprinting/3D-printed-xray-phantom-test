"""Two operators, one analysis, one shared account.

There is a single login, so the software cannot tell two people apart, and the
field audit log shows them working on the same record anyway — analysis
`e800a62a9141` was edited from two addresses minutes apart, `9db972d05e65`
computed from one and then another.

Their edits were already serialised: each runs in its own transaction, so
neither can corrupt the stored geometry. But serialising only decides which one
silently overwrites the other. The second operator drags a point, the first
one's correction vanishes, and nothing anywhere says so — the kind of loss that
is discovered, if at all, long afterwards.

An edit may now declare which state of the measuring points it was made from.
If the stored geometry has moved on, the edit is refused and the page reloads
so the operator sees the current points before redoing the change. Declaring it
is optional: re-analysis and the command line deliberately overwrite whatever
is there.
"""

import pytest

from phantom_qa.store import StaleGeometry
from test_unusable_exposures import SYNTHETIC, _png16

_variant = iter(range(20_000, 30_000))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


@pytest.fixture()
def ready(client):
    """An analysis with measuring points, waiting to be adjusted."""
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses",
                     files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": "CONCURRENT"})
    aid = up.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    proposed = client.post(f"/api/analyses/{aid}/propose", json={}).json()
    roi = proposed["geometry"]["uniformity"]["squares"][0]["roi"]
    return aid, roi


def _seq(client, aid):
    return client.get(f"/api/analyses/{aid}").json()["history"]["seq"]


def _move(client, aid, roi, dx, expect_seq=None):
    body = {"roi_id": roi["id"],
            "center_px": [roi["center_px"][0] + dx, roi["center_px"][1]]}
    if expect_seq is not None:
        body["expect_seq"] = expect_seq
    return client.post(f"/api/analyses/{aid}/roi", json=body)


# ------------------------------------------------------- the loss it prevents

def test_a_second_edit_from_a_stale_view_is_refused(client, ready):
    """The whole point: the later edit does not silently win."""
    aid, roi = ready
    both_saw = _seq(client, aid)

    first = _move(client, aid, roi, 4.0, expect_seq=both_saw)
    assert first.status_code == 200, first.text

    # The other operator still has the view they loaded before that happened.
    second = _move(client, aid, roi, -4.0, expect_seq=both_saw)
    assert second.status_code == 409, second.text
    body = second.json()
    assert body["stale_geometry"] is True
    assert body["expected_seq"] == both_saw
    assert body["current_seq"] > both_saw


def test_the_refusal_explains_itself_to_the_person_who_hit_it(client, ready):
    aid, roi = ready
    stale = _seq(client, aid)
    _move(client, aid, roi, 4.0, expect_seq=stale)
    detail = _move(client, aid, roi, -4.0, expect_seq=stale).json()["detail"]
    assert "someone else" in detail.lower()
    assert "not applied" in detail.lower(), \
        "the operator must know their change did NOT take effect"
    assert "again" in detail.lower(), "and what to do about it"


def test_the_first_edit_survives_the_refusal(client, ready):
    """A refusal must change nothing at all."""
    aid, roi = ready
    stale = _seq(client, aid)
    _move(client, aid, roi, 4.0, expect_seq=stale)
    after_first = client.get(f"/api/analyses/{aid}").json()

    _move(client, aid, roi, -4.0, expect_seq=stale)          # refused
    after_refusal = client.get(f"/api/analyses/{aid}").json()

    assert after_refusal["history"]["seq"] == after_first["history"]["seq"]
    assert after_refusal["geometry"] == after_first["geometry"], \
        "a refused edit left a mark on the stored measuring points"


def test_an_edit_from_the_current_state_is_applied(client, ready):
    """Refusing everything would be safe and useless."""
    aid, roi = ready
    first = _move(client, aid, roi, 4.0, expect_seq=_seq(client, aid))
    assert first.status_code == 200
    moved = first.json()["roi"]["center_px"]

    # Re-read, as the page does after every edit, and continue working.
    second = _move(client, aid, {**roi, "center_px": moved}, 3.0,
                   expect_seq=_seq(client, aid))
    assert second.status_code == 200, second.text
    assert second.json()["roi"]["center_px"][0] == pytest.approx(
        moved[0] + 3.0, abs=0.51)


# ---------------------------------------------------------- staying optional

def test_an_edit_that_declares_nothing_behaves_as_before(client, ready):
    """Re-analysis and the command line overwrite deliberately.

    Making the declaration compulsory would break them, and would also mean an
    older browser could no longer edit at all."""
    aid, roi = ready
    _move(client, aid, roi, 4.0, expect_seq=_seq(client, aid))
    assert _move(client, aid, roi, -4.0).status_code == 200, \
        "omitting expect_seq must keep the previous behaviour"


def test_the_check_covers_every_way_of_moving_a_measuring_point(client, ready):
    """Rotation, the low-contrast block and the field edge, not just dragging.

    A guard on one of four editing paths would be worse than none: it would
    read as protection while three routes stayed open."""
    aid, _ = ready
    stale = _seq(client, aid)
    proposed = client.get(f"/api/analyses/{aid}").json()["geometry"]
    group = proposed["linepairs"]["groups"][0]["roi"]

    _move(client, aid, group, 2.0, expect_seq=stale)          # move it on

    refusals = {
        "roi_rotate": client.post(
            f"/api/analyses/{aid}/roi_rotate",
            json={"roi_id": group["id"], "angle_deg": 3.0,
                  "expect_seq": stale}),
        "lowcontrast_block": client.post(
            f"/api/analyses/{aid}/lowcontrast_block",
            json={"angle_deg": -44.0, "expect_seq": stale}),
        "field_edge": client.post(
            f"/api/analyses/{aid}/field_edge",
            json={"side": "top", "point_px": [500.0, 40.0],
                  "expect_seq": stale}),
    }
    for name, answer in refusals.items():
        assert answer.status_code == 409, f"{name} accepted a stale edit"
        assert answer.json()["stale_geometry"] is True


# ------------------------------------------------- the check is where it must be

def test_the_state_is_checked_inside_the_write_transaction(client, ready):
    """Checking before the transaction would leave the race it closes.

    Two edits could both read the same state, both pass a check made outside
    the lock, and both write. The store raises from inside `write_transaction`,
    so the state cannot move between the check and the write."""
    import inspect
    from phantom_qa.store import Store
    source = inspect.getsource(Store.mutate_geometry)
    body = source[source.index("with self.write_transaction()"):]
    assert "StaleGeometry" in body, \
        "the staleness check must sit inside the write transaction"


def test_the_store_raises_rather_than_returning_a_flag(client, ready):
    """So a caller cannot forget to look."""
    aid, roi = ready
    stale = _seq(client, aid)
    _move(client, aid, roi, 4.0, expect_seq=stale)
    with pytest.raises(StaleGeometry) as raised:
        client.mod.store.mutate_geometry(
            aid, lambda g: None, action="roi", expect_seq=stale)
    assert raised.value.expected == stale
    assert raised.value.actual > stale


def test_a_missing_analysis_is_still_a_404_not_a_conflict(client):
    answer = client.post("/api/analyses/deadbeef/roi",
                         json={"roi_id": "uniformity/TL",
                               "center_px": [10.0, 10.0], "expect_seq": 0})
    assert answer.status_code == 404
