"""Seeing the low-contrast discs well enough to mark them.

From the first field test: "Improve detection of low contrast measuring
points. It is difficult to see them and properly adjust the W/C to place the
marks."

Three separate things made that true, and each is addressed here rather than
by asking the operator to work harder at the window controls:

  the block is a small part of a large picture, so it is sent on its own;
  the window that suits the whole phantom is far too wide for objects a
  fraction of a percent in contrast, so the window is taken from the block's
  own values;
  and the block carries a broad illumination gradient that swamps the discs,
  so it is flattened before it is shown.

The bandwidth is the fourth reason. Re-windowing the full render cost a
fifteen-second transfer on a 512 kbit/s link every time it was tried, which is
what made "properly adjust the W/C" the painful part. The close-up is about
twenty kilobytes and the contrast control then works on the copy already in
the browser.
"""

import io

import numpy as np
import pytest

from test_authorization import ADMIN_PW
from test_unusable_exposures import SYNTHETIC, _png16

_variant = iter(range(30_000, 39_000))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


def _proposed(client, phantom="BLOCKVIEW"):
    """An analysis taken as far as having measuring points."""
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses", files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": phantom})
    assert up.status_code == 200, up.text
    aid = up.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    return aid


# ----------------------------------------------------------- what is served

def test_the_close_up_is_served_for_an_analysis_with_measuring_points(client):
    aid = _proposed(client)
    png = client.get(f"/api/analyses/{aid}/lowcontrast_view.png")
    assert png.status_code == 200, png.text
    assert png.headers["content-type"] == "image/png"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_it_is_a_small_fraction_of_the_full_render(client):
    """The reason this exists at all.

    A close-up that cost the same as the full image would not have helped —
    the transfer was the painful part, not the windowing."""
    aid = _proposed(client)
    close_up = client.get(f"/api/analyses/{aid}/lowcontrast_view.png").content
    full = client.get(f"/api/analyses/{aid}/image.png").content
    assert len(close_up) < len(full) / 4, (
        f"close-up {len(close_up)} B against full render {len(full)} B")
    assert len(close_up) < 200_000


def test_it_says_where_every_disc_is_and_how_plainly_it_shows(client):
    aid = _proposed(client)
    meta = client.get(f"/api/analyses/{aid}/lowcontrast_view").json()
    assert len(meta["markers"]) == 8
    for m in meta["markers"]:
        assert set(m) >= {"id", "level", "x_px", "y_px", "r_px",
                          "response", "visibility"}
        assert m["visibility"] in ("clear", "faint", "at the limit")
        # Inside the picture it is drawn on, or the ring lands off-screen.
        assert 0 <= m["x_px"] <= meta["size_px"][0]
        assert 0 <= m["y_px"] <= meta["size_px"][1]


def test_the_rings_sit_where_the_measuring_ROIs_sit(client):
    """The close-up is for judging the placement, so it has to show the
    placement that is actually stored, not a fresh guess at one."""
    aid = _proposed(client)
    meta = client.get(f"/api/analyses/{aid}/lowcontrast_view").json()
    geom = client.get(f"/api/analyses/{aid}").json()["geometry"]
    ids = [c["id"] for c in geom["lowcontrast"]["circles"]]
    assert [m["id"] for m in meta["markers"]] == ids


def test_moving_the_block_changes_the_picture_that_is_served(client):
    """Otherwise the operator adjusts the block and sees the old crop, which
    is worse than showing nothing."""
    aid = _proposed(client)
    before = client.get(f"/api/analyses/{aid}/lowcontrast_view").json()
    client.post(f"/api/analyses/{aid}/lowcontrast_block", json={"angle_deg": -30.0})
    after = client.get(f"/api/analyses/{aid}/lowcontrast_view").json()
    assert after["seq"] != before["seq"], \
        "the cache key must move when the block does"


def test_asking_before_the_points_exist_is_a_plain_refusal(client):
    """Not a 500: the step that would show this is reachable early."""
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses", files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": "BLOCKVIEW-EARLY"})
    aid = up.json()["analyses"][0]["id"]
    for path in ("lowcontrast_view", "lowcontrast_view.png"):
        r = client.get(f"/api/analyses/{aid}/{path}")
        assert r.status_code == 400, f"{path} -> {r.status_code}"
        assert "proposed" in r.json()["detail"]


def test_an_unknown_analysis_is_not_found_rather_than_an_error(client):
    assert client.get("/api/analyses/deadbeef/lowcontrast_view.png"
                      ).status_code == 404


def test_a_deleted_analysis_stops_serving_its_close_up(client):
    """The close-up is privately cacheable like the main image, so a stale one
    would stick in the browser as well as in the worker."""
    aid = _proposed(client)
    assert client.get(f"/api/analyses/{aid}/lowcontrast_view.png"
                      ).status_code == 200
    client.post(f"/api/analyses/{aid}/delete",
                json={"admin_password": ADMIN_PW, "reason": "regression test"})
    assert client.get(f"/api/analyses/{aid}/lowcontrast_view.png"
                      ).status_code == 404
    assert not [k for k in client.mod._img_cache if k[0] == aid]


def test_an_exposure_with_no_signal_still_produces_a_picture(client):
    """A saturated scan is exactly when the operator needs to SEE that there
    is nothing there. Refusing to draw it would leave them guessing."""
    aid = _proposed(client, phantom="BLOCKVIEW-FLAT")
    meta = client.get(f"/api/analyses/{aid}/lowcontrast_view").json()
    assert "flat" in meta
    assert client.get(f"/api/analyses/{aid}/lowcontrast_view.png"
                      ).status_code == 200


# ------------------------------------------------- what the flattening does

def test_the_close_up_spreads_the_block_across_the_whole_grey_range():
    """The measurable half of "difficult to see them".

    Windowed for the whole phantom, the block occupies a narrow band of grey
    and the discs are a fraction of that. Windowed to itself and flattened, it
    fills the range — which is what makes a disc at the limit visible at
    all."""
    from phantom_qa import phantom_def, pipeline
    from phantom_qa.analysis import lowcontrast as LC
    from phantom_qa.analysis.common import Ctx
    from phantom_qa.registration import Transform

    pdef = phantom_def.load_default()
    rng = np.random.default_rng(7)
    size = 900
    # A bright block on a darker field, with a strong illumination gradient
    # across it and eight faint discs on top — the situation in the field.
    img = rng.normal(1000, 12, (size, size))
    img += np.linspace(0, 260, size)[None, :]
    yy, xx = np.mgrid[0:size, 0:size]
    block = (np.abs(xx - size / 2) < 170) & (np.abs(yy - size / 2) < 90)
    img[block] += 420
    for i, (dx, dy) in enumerate([(-120, -40), (-40, -40), (40, -40), (120, -40),
                                  (-120, 40), (-40, 40), (40, 40), (120, 40)]):
        disc = ((xx - (size / 2 + dx)) ** 2
                + (yy - (size / 2 + dy)) ** 2) < 20 ** 2
        img[disc] -= 6 + 3 * i

    T = Transform(A=np.array([[4.0, 0.0], [0.0, 4.0]]),
                  t=np.array([size / 2, size / 2]))
    ctx = Ctx(pixels=img, T=T, pdef=pdef, reg=None, params={})
    view = LC.block_view(ctx, [0.0, 0.0], 0.0)

    assert view["image"].min() < 0.05 and view["image"].max() > 0.95, \
        "the close-up does not use the grey range it was given"
    assert view["image"].std() > 0.1, "the picture is nearly uniform"
    assert not view["flat"]


def test_the_close_up_is_the_same_way_up_however_the_phantom_lay():
    """Straightened into the block's own frame, so an operator comparing two
    scans is not also compensating for how the phantom sat on the table."""
    from phantom_qa import phantom_def
    from phantom_qa.analysis import lowcontrast as LC
    from phantom_qa.analysis.common import Ctx
    from phantom_qa.registration import Transform

    pdef = phantom_def.load_default()
    rng = np.random.default_rng(11)
    size = 900
    img = rng.normal(1000, 10, (size, size))
    T = Transform(A=np.array([[4.0, 0.0], [0.0, 4.0]]),
                  t=np.array([size / 2, size / 2]))
    ctx = Ctx(pixels=img, T=T, pdef=pdef, reg=None, params={})
    straight = LC.block_view(ctx, [0.0, 0.0], 0.0)
    turned = LC.block_view(ctx, [0.0, 0.0], 30.0)
    assert straight["size_px"] == turned["size_px"], \
        "the close-up must not change shape with the phantom's angle"


def test_every_marker_lands_inside_the_picture_at_any_angle():
    from phantom_qa import phantom_def
    from phantom_qa.analysis import lowcontrast as LC
    from phantom_qa.analysis.common import Ctx
    from phantom_qa.registration import Transform

    pdef = phantom_def.load_default()
    img = np.random.default_rng(3).normal(1000, 10, (900, 900))
    T = Transform(A=np.array([[4.0, 0.0], [0.0, 4.0]]),
                  t=np.array([450.0, 450.0]))
    ctx = Ctx(pixels=img, T=T, pdef=pdef, reg=None, params={})
    for angle in (-90.0, -45.1, 0.0, 33.0, 179.0):
        view = LC.block_view(ctx, [0.0, 0.0], angle)
        marks = LC.view_markers(ctx, {"grid_shift_mm": [0.0, 0.0]},
                                [0.0, 0.0], angle)
        w, h = view["size_px"]
        for m in marks:
            assert 0 <= m["x_px"] <= w and 0 <= m["y_px"] <= h, \
                f"{m['id']} falls outside the close-up at {angle}°"


# ------------------------------------------------ naming the picture safely

def _view(client, aid):
    return client.get(f"/api/analyses/{aid}/lowcontrast_view").json()


def _picture(client, aid, key):
    return client.get(f"/api/analyses/{aid}/lowcontrast_view.png?key={key}")


def test_a_repeated_edit_counter_never_serves_the_old_picture(client,
                                                             monkeypatch):
    """The defect this guards: the picture used to be cached under the edit
    counter, and undo steps that counter back — so the next, different edit
    reuses its number. The server then handed back the old close-up under the
    new rings: the operator judged discs that were no longer where the rings
    said.

    The synthetic scan is saturated, so its real close-up is flat grey at any
    angle; the render is wrapped to paint the angle into the picture, so the
    bytes show which placement they were made from."""
    real = client.mod.lowcontrast.block_view

    def telltale(ctx, centre, angle, *a, **k):
        view = real(ctx, centre, angle, *a, **k)
        return {**view, "image": np.full_like(view["image"],
                                              (abs(angle) % 90.0) / 90.0)}

    monkeypatch.setattr(client.mod.lowcontrast, "block_view", telltale)
    aid = _proposed(client, phantom="BLOCKVIEW-UNDO")
    client.post(f"/api/analyses/{aid}/lowcontrast_block", json={"angle_deg": -30.0})
    first = _view(client, aid)
    first_png = _picture(client, aid, first["key"]).content

    client.post(f"/api/analyses/{aid}/geometry/undo", json={})
    client.post(f"/api/analyses/{aid}/lowcontrast_block", json={"angle_deg": -60.0})
    second = _view(client, aid)

    assert second["seq"] == first["seq"], \
        "the scenario needs the counter to repeat; if it no longer does, " \
        "this test should be rewritten, not deleted"
    assert second["key"] != first["key"]
    assert _picture(client, aid, second["key"]).content != first_png


def test_undoing_back_to_a_placement_gives_back_its_name(client):
    """So a picture the browser already holds is found again, not re-sent."""
    aid = _proposed(client, phantom="BLOCKVIEW-BACK")
    before = _view(client, aid)["key"]
    client.post(f"/api/analyses/{aid}/lowcontrast_block", json={"angle_deg": -30.0})
    client.post(f"/api/analyses/{aid}/geometry/undo", json={})
    assert _view(client, aid)["key"] == before


def test_the_browser_keeps_the_picture_only_under_its_own_name(client):
    """Keepable when the URL names what it shows; never otherwise — a stale
    page must not store today's picture under yesterday's name."""
    aid = _proposed(client, phantom="BLOCKVIEW-CACHE")
    key = _view(client, aid)["key"]
    right = _picture(client, aid, key)
    assert right.headers["cache-control"].startswith("private")
    for wrong in ("0" * 16, ""):
        stale = _picture(client, aid, wrong)
        assert stale.status_code == 200
        assert stale.headers["cache-control"] == "no-store"
        assert stale.content == right.content, "the current picture is still sent"


def test_the_page_asks_for_the_picture_by_its_name(app_js=None):
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "phantom_qa", "webapp", "static", "app.js")
    src = open(path, encoding="utf-8").read()
    assert "lowcontrast_view.png?key=${meta.key}" in src
    assert "lowcontrast_view.png?seq=" not in src
