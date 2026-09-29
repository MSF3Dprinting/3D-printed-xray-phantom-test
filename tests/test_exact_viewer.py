"""The viewer's picture is exact.

The user's rule, after the viewer had been switched to JPEG: "The compression
could never lead to lose of visual details! Never in any part of the app.
This is a critical qc tool we cannot compromise it." Measured on the original
Philips scans, that JPEG kept 0.3-1.5 % of the finest line-pair group's bar
contrast: the bars were gone, and looking at the picture is the inspection.

So the viewer draws a lossless picture — WebP in its lossless mode, PNG where
a browser cannot show that. These tests decode what the server sends and
compare it pixel for pixel with the window and the averaging written out
here, independently of the code that made it.
"""

import io
import os

import numpy as np
import pytest
from PIL import Image

from conftest import SAMPLES, needs_samples
from test_authorization import ADMIN_PW
from test_unusable_exposures import SYNTHETIC, _png16

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "phantom_qa", "webapp", "static")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
KEEP = "private, max-age=2592000, immutable, no-transform"
NO_KEEP = "no-store, no-transform"

_variant = iter(range(50_000, 59_000))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


def _pixels():
    """The synthetic phantom with integer noise over all of it.

    A picture made of two flat values comes out exact from almost any
    encoder; noise in every pixel is what a lossy one cannot keep."""
    rng = np.random.default_rng(7)
    pixels = SYNTHETIC["saturated"]() + rng.integers(0, 61, (1400, 1400))
    pixels[0, 0] = next(_variant)          # a new file, not a duplicate
    # Whole numbers, as a 16-bit upload stores them (_png16 truncates).
    return np.clip(pixels, 0, 65535).astype(np.uint16).astype(float)


def _uploaded(client, pixels, phantom="EXACT"):
    up = client.post("/api/analyses", files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": phantom})
    assert up.status_code == 200, up.text
    return up.json()["analyses"][0]["id"]


def _window(values, lo, hi):
    """Stored values -> grey levels, as a window defines them: nearest level,
    black and white outside it. Written out here, not taken from
    phantom_qa.imaging, so that the check does not share its mistakes."""
    v = np.asarray(values, dtype=np.float64)
    return np.clip(np.rint((v - lo) * (255.0 / (hi - lo))), 0, 255
                   ).astype(np.uint8)


def _grey(payload: bytes) -> np.ndarray:
    """The decoded picture as one channel. WebP has no grey mode and comes
    back as three channels, which must then be identical."""
    arr = np.asarray(Image.open(io.BytesIO(payload)))
    if arr.ndim == 3:
        assert np.array_equal(arr[..., 0], arr[..., 1]) \
            and np.array_equal(arr[..., 0], arr[..., 2]), "the picture is not grey"
        arr = arr[..., 0]
    return arr


def _limits(pixels, query):
    """The window a query asks for, or the server's own default."""
    if "wc=" in query:
        q = dict(p.split("=") for p in query.split("&"))
        wc, ww = float(q["wc"]), float(q["ww"])
        return wc - ww / 2, wc + ww / 2
    lo, hi = np.percentile(np.asarray(pixels, dtype=float), [1, 99])
    return lo, hi


def _app_js() -> str:
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------ every pixel exact

# Each test here uploads its scan once and loops over the formats, windows or
# sizes it checks: every upload rebuilds the application, and one test per
# variant made this file take minutes.

FORMATS = ("webp", "png")


def test_at_full_size_every_pixel_is_the_stored_value_windowed(client):
    pixels = _pixels()
    aid = _uploaded(client, pixels)
    assert np.array_equal(client.mod._scan(aid).pixels, pixels), \
        "the stored values are not what was uploaded"
    for window in ("", "wc=2100.5&ww=3900.25", "wc=500&ww=200"):
        lo, hi = _limits(pixels, window)
        for fmt in FORMATS:
            r = client.get(f"/api/analyses/{aid}/image.{fmt}?scale=4096&{window}")
            assert r.status_code == 200, r.text
            assert np.array_equal(_grey(r.content), _window(pixels, lo, hi)), \
                f"{fmt}, window {window or 'automatic'}"


def test_a_smaller_picture_shows_the_average_of_the_pixels_it_covers(client):
    """Half size: each picture pixel is the mean of a 2x2 block of stored
    values, windowed after averaging. A sharpening filter (Lanczos, as before)
    draws halos at edges that are not in the scan."""
    pixels = _pixels()
    aid = _uploaded(client, pixels)
    means = pixels.reshape(700, 2, 700, 2).mean(axis=(1, 3))
    lo, hi = _limits(pixels, "")
    for fmt in FORMATS:
        r = client.get(f"/api/analyses/{aid}/image.{fmt}?scale=700")
        assert np.array_equal(_grey(r.content), _window(means, lo, hi)), fmt


def test_the_two_formats_are_the_same_picture(client):
    """The page asks for PNG only where the browser cannot show lossless WebP;
    the operator must see the same thing either way."""
    aid = _uploaded(client, _pixels())
    for query in ("", "?scale=900", "?wc=3000&ww=400&scale=333"):
        webp = client.get(f"/api/analyses/{aid}/image.webp{query}")
        png = client.get(f"/api/analyses/{aid}/image.png{query}")
        assert np.array_equal(_grey(webp.content), _grey(png.content)), query


def test_webp_is_the_lossless_kind(client):
    """Lossy WebP ('VP8 ') would be JPEG again under another name."""
    from phantom_qa import imaging
    if not imaging.WEBP_AVAILABLE:
        pytest.skip("this server cannot write WebP")
    r = client.get(f"/api/analyses/{_uploaded(client, _pixels())}/image.webp")
    assert r.headers["content-type"] == "image/webp"
    assert r.content[:4] == b"RIFF" and r.content[12:16] == b"VP8L"


def test_it_is_exactly_the_size_the_outlines_are_placed_by(client):
    """The viewer places every outline through the picture's width over the
    scan's. A picture one pixel off would move every ROI on screen; one larger
    than the scan would invent pixels."""
    aid = _uploaded(client, _pixels())
    for scale, width in ((200, 200), (900, 900), (4096, 1400)):
        for fmt in FORMATS:
            got = _grey(client.get(f"/api/analyses/{aid}/image.{fmt}?scale={scale}"
                                   ).content)
            assert got.shape == (width, width), f"{fmt} at {scale}: {got.shape}"


def test_the_two_formats_never_answer_for_each_other(client):
    """They share one cache. Keyed without the format, whichever was asked
    for first would be served under both names."""
    aid = _uploaded(client, _pixels())
    for first, second, magic in (("png", "webp", b"RIFF"),
                                 ("webp", "png", PNG_MAGIC)):
        client.get(f"/api/analyses/{aid}/image.{first}?scale=300")
        again = client.get(f"/api/analyses/{aid}/image.{second}?scale=300")
        assert again.content.startswith(magic), \
            f"image.{second} answered with the cached image.{first}"
        client.mod._img_cache.clear()


def test_there_is_no_jpeg_picture_any_more(client):
    aid = _uploaded(client, _pixels())
    assert client.get(f"/api/analyses/{aid}/image.jpg").status_code == 404
    assert "/image.jpg" not in _app_js()


# ------------------------------------------------------ kept, and left alone

def test_a_picture_named_by_the_current_version_is_kept_and_left_alone(client):
    """Kept for 30 days without asking again: the pixels never change and the
    address carries everything that varies the picture. Private, because it
    is patient-adjacent imagery; no-transform, so no proxy on the way may
    recompress it."""
    aid = _uploaded(client, _pixels())
    v = client.mod.PICTURE_VERSION
    for fmt in ("webp", "png"):
        r = client.get(f"/api/analyses/{aid}/image.{fmt}?scale=300&v={v}")
        assert r.headers["cache-control"] == KEEP, fmt


def test_a_picture_without_the_current_version_is_not_kept(client):
    """A page from before a rendering change asks without the new number.
    Its answer may differ from the next one, so it must not be kept."""
    aid = _uploaded(client, _pixels())
    for v in ("", "1", "old"):
        for fmt in FORMATS:
            r = client.get(f"/api/analyses/{aid}/image.{fmt}?scale=300&v={v}")
            assert r.status_code == 200
            assert r.headers["cache-control"] == NO_KEEP, f"{fmt}, v={v!r}"


def test_the_page_learns_the_version_when_it_starts(client):
    assert client.get("/api/auth").json()["picture_version"] \
        == client.mod.PICTURE_VERSION


def test_it_is_not_compressed_a_second_time(client):
    """A lossless picture gains nothing from gzip and costs CPU per request."""
    aid = _uploaded(client, _pixels())
    for fmt in ("webp", "png"):
        r = client.get(f"/api/analyses/{aid}/image.{fmt}",
                       headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200
        assert r.headers.get("content-encoding") != "gzip", fmt


HOSTILE = ("scale=0", "scale=-5", "scale=99999999", "wc=nan&ww=nan",
           "wc=inf&ww=-inf", "wc=100&ww=0", "wc=100&ww=-50")


def test_it_survives_any_viewer_parameters(client):
    """Slider values are viewer state, not trusted input."""
    aid = _uploaded(client, _pixels())
    for query in HOSTILE:
        for fmt in FORMATS:
            r = client.get(f"/api/analyses/{aid}/image.{fmt}?{query}")
            assert r.status_code == 200, f"{fmt} ?{query} -> {r.status_code}"
            assert _grey(r.content).dtype == np.uint8


def test_an_unknown_analysis_is_not_found(client):
    for fmt in ("webp", "png"):
        assert client.get(f"/api/analyses/deadbeef/image.{fmt}").status_code \
            == 404


def test_a_deleted_analysis_stops_serving_it(client):
    """Cached per worker, so a delete handled by another worker must not
    leave it serveable."""
    aid = _uploaded(client, _pixels(), phantom="EXACT-DELETE")
    assert client.get(f"/api/analyses/{aid}/image.webp").status_code == 200
    client.post(f"/api/analyses/{aid}/delete",
                json={"admin_password": ADMIN_PW, "reason": "regression test"})
    assert client.get(f"/api/analyses/{aid}/image.webp").status_code == 404
    assert not [k for k in client.mod._img_cache if k[0] == aid], \
        "rendered views of a deleted record stayed in memory"


def test_a_discarded_analysis_takes_its_pictures_with_it(client):
    aid = _uploaded(client, _pixels(), phantom="EXACT-DISCARD")
    client.get(f"/api/analyses/{aid}/image.webp?scale=200")
    client.post(f"/api/analyses/{aid}/discard", json={"confirm": True})
    assert client.get(f"/api/analyses/{aid}/image.webp?scale=200"
                      ).status_code == 404


# ------------------------------------------------------ full detail on zoom

TILE = 256


def _n_tiles(n: int, level: int) -> int:
    """Tiles across a side of n scan pixels at a level."""
    size = -(-n // 2 ** level)
    return -(-size // TILE)


def _stitched(client, aid, level, shape, fmt="webp", query=""):
    """Every tile of a level, fetched and put back together."""
    rows, cols = _n_tiles(shape[0], level), _n_tiles(shape[1], level)
    return np.vstack([
        np.hstack([_grey(client.get(
            f"/api/analyses/{aid}/tile/{level}/{tx}/{ty}.{fmt}?{query}").content)
            for tx in range(cols)])
        for ty in range(rows)])


def _block_means(values, f):
    """Each f x f block's mean, edge blocks over what is there — from a
    summed-area table, a different road from the one the server takes."""
    v = np.asarray(values, dtype=np.float64)
    h, w = v.shape
    table = np.zeros((h + 1, w + 1))
    table[1:, 1:] = v.cumsum(0).cumsum(1)
    ys, xs = np.arange(0, h, f), np.arange(0, w, f)
    y1, x1 = np.minimum(ys + f, h), np.minimum(xs + f, w)
    sums = (table[y1][:, x1] - table[ys][:, x1]
            - table[y1][:, xs] + table[ys][:, xs])
    return sums / ((y1 - ys)[:, None] * (x1 - xs)[None, :])


def test_the_full_detail_pieces_are_the_scan_itself(client):
    """Stitched together, the level-0 pieces are the full-size picture:
    every scan pixel, windowed, nothing lost."""
    pixels = _pixels()
    aid = _uploaded(client, pixels)
    for window in ("", "wc=2100.5&ww=3900.25"):
        lo, hi = _limits(pixels, window)
        for fmt in FORMATS:
            got = _stitched(client, aid, 0, pixels.shape, fmt, window)
            what = f"{fmt}, window {window or 'automatic'}"
            assert np.array_equal(got, _window(pixels, lo, hi)), what
            whole = client.get(f"/api/analyses/{aid}/image.{fmt}?scale=4096&{window}")
            assert np.array_equal(got, _grey(whole.content)), what


def test_the_smaller_levels_are_exact_block_averages(client):
    """A scan whose sides do not divide evenly, so the edge blocks are
    partial: each level pixel is the mean of exactly the scan pixels in its
    block, windowed like the overview it fills in."""
    pixels = _pixels()[:1399, :1397]
    aid = _uploaded(client, pixels)
    lo, hi = _limits(pixels, "")
    for level in (1, 2):
        got = _stitched(client, aid, level, pixels.shape)
        assert np.array_equal(
            got, _window(_block_means(pixels, 2 ** level), lo, hi)), level


def test_a_piece_at_the_edge_stops_where_the_scan_does(client):
    aid = _uploaded(client, _pixels())
    edge = _grey(client.get(f"/api/analyses/{aid}/tile/0/5/0.webp").content)
    assert edge.shape == (TILE, 1400 - 5 * TILE)
    corner = _grey(client.get(f"/api/analyses/{aid}/tile/2/1/1.webp").content)
    assert corner.shape == (350 - TILE, 350 - TILE)


def test_a_piece_that_does_not_exist_is_not_found(client):
    aid = _uploaded(client, _pixels())
    for piece in ("0/6/0", "0/0/6", "0/-1/0", "3/0/0", "-1/0/0", "2/2/0"):
        for fmt in FORMATS:
            r = client.get(f"/api/analyses/{aid}/tile/{piece}.{fmt}")
            assert r.status_code == 404, f"{piece}.{fmt} -> {r.status_code}"


def test_a_piece_named_by_the_current_version_is_kept_and_left_alone(client):
    aid = _uploaded(client, _pixels())
    v = client.mod.PICTURE_VERSION
    for fmt in ("webp", "png"):
        kept = client.get(f"/api/analyses/{aid}/tile/1/0/0.{fmt}?v={v}",
                          headers={"Accept-Encoding": "gzip"})
        assert kept.headers["cache-control"] == KEEP, fmt
        assert kept.headers.get("content-encoding") != "gzip", fmt
        assert client.get(f"/api/analyses/{aid}/tile/1/0/0.{fmt}"
                          ).headers["cache-control"] == NO_KEEP, fmt


def test_a_piece_survives_any_viewer_parameters(client):
    aid = _uploaded(client, _pixels())
    for query in HOSTILE:
        r = client.get(f"/api/analyses/{aid}/tile/0/0/0.webp?{query}")
        assert r.status_code == 200, f"?{query} -> {r.status_code}"


def test_a_deleted_analysis_stops_serving_its_pieces(client):
    aid = _uploaded(client, _pixels(), phantom="EXACT-TILE-DELETE")
    assert client.get(f"/api/analyses/{aid}/tile/1/0/0.webp").status_code == 200
    assert aid in client.mod._levels
    client.post(f"/api/analyses/{aid}/delete",
                json={"admin_password": ADMIN_PW, "reason": "regression test"})
    assert client.get(f"/api/analyses/{aid}/tile/1/0/0.webp").status_code == 404
    assert aid not in client.mod._levels, "its zoom levels stayed in memory"
    assert client.get("/api/analyses/deadbeef/tile/0/0/0.webp").status_code == 404


def test_the_page_learns_how_long_to_wait_before_fetching_detail(client):
    """The user's choice: 1 s of stillness, adjustable in the configuration."""
    assert client.get("/api/auth").json()["detail_delay_s"] == 1.0


@pytest.mark.parametrize("setting, expected", [("2.5", 2.5), ("0", 0.0),
                                               ("-3", 1.0), ("soon", 1.0),
                                               ("nan", 1.0)])
def test_the_wait_is_set_in_the_configuration(tmp_path, monkeypatch, setting,
                                               expected):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app
    mod = _build_app(tmp_path, monkeypatch, PHANTOMQA_DETAIL_DELAY_S=setting)
    assert TestClient(mod.app).get("/api/auth").json()["detail_delay_s"] \
        == expected


# ------------------------------------------------------ a real scan

@needs_samples
def test_a_real_scan_is_shown_exactly(client):
    """A synthetic picture is kind to any encoder; an X-ray is not. On the
    reference Philips scan every pixel of the full-size picture must be the
    stored value windowed, and the viewer-sized WebP and PNG identical."""
    with open(SAMPLES[0], "rb") as f:
        up = client.post("/api/analyses",
                         files={"file": ("scan.dcm", io.BytesIO(f.read()))},
                         data={"site": "T", "phantom": "EXACT-REAL"})
    aid = up.json()["analyses"][0]["id"]
    stored = client.mod._scan(aid).pixels
    full = client.get(f"/api/analyses/{aid}/image.webp?scale=4096")
    lo, hi = _limits(stored, "")
    assert np.array_equal(_grey(full.content), _window(stored, lo, hi))
    webp = client.get(f"/api/analyses/{aid}/image.webp?scale=1024")
    png = client.get(f"/api/analyses/{aid}/image.png?scale=1024")
    assert np.array_equal(_grey(webp.content), _grey(png.content))
    # A full-detail piece in the middle of the scan, and one at half size.
    piece = _grey(client.get(f"/api/analyses/{aid}/tile/0/5/5.webp").content)
    assert np.array_equal(piece, _window(stored[1280:1536, 1280:1536], lo, hi))
    half = _grey(client.get(f"/api/analyses/{aid}/tile/1/2/2.webp").content)
    means = _block_means(stored[1024:1536, 1024:1536], 2)
    assert np.array_equal(half, _window(means, lo, hi))


# ------------------------------------------------------ what the page asks

def _body(app_js, start, end):
    """The source from `start` up to the next `end` after it."""
    body = app_js[app_js.index(start):]
    return body[:body.index(end)]


def test_the_viewer_asks_for_the_lossless_picture_with_the_version():
    load = _body(_app_js(), "function loadImage(", "function wlFromParams")
    assert "/image.${S.pictureFormat}" in load
    assert "S.pictureVersion" in load, \
        "without the version the browser may not keep the picture"
    assert load.index("pictureFormatKnown.then(") < load.index("img.src"), \
        "asked before the browser's WebP support is known"


def test_the_duplicate_thumbnail_is_lossless_too():
    assert "`/image.${S.pictureFormat}?scale=200&v=`" in _app_js()


def test_the_webp_check_is_a_real_lossless_picture():
    """The page decides between WebP and PNG by showing a one-pixel picture.
    It has to be the LOSSLESS kind ('VP8L'): a browser that shows only lossy
    WebP would otherwise pass the check and then fail on every picture."""
    import base64
    import re
    uri = re.search(r'"data:image/webp;base64,([A-Za-z0-9+/=]+)"', _app_js())
    raw = base64.b64decode(uri.group(1))
    assert raw[:4] == b"RIFF" and raw[12:16] == b"VP8L"
    assert Image.open(io.BytesIO(raw)).size == (1, 1)


def test_a_browser_without_lossless_webp_is_sent_png():
    probe = _body(_app_js(), "const pictureFormatKnown", "\n});")
    assert 'probe.onerror = () => { S.pictureFormat = "png"' in probe
    assert '? "webp" : "png"' in probe


def test_the_canvas_has_the_screens_own_pixels():
    """On a laptop at 125-150 % a canvas of CSS pixels is stretched by the
    browser and the whole picture goes soft."""
    resize = _body(_app_js(), "function resizeCanvas(", "\n}\n")
    assert "window.devicePixelRatio" in resize
    assert "canvas.width = Math.round(S.cssW * S.dpr)" in resize
    assert "canvas.height = Math.round(S.cssH * S.dpr)" in resize
    # Its size on the page stays the viewer's, or it would spill out of it.
    assert "canvas.style.width = `${S.cssW}px`" in resize
    draw = _body(_app_js(), "function draw(", "/* registration corners */")
    assert "ctx2d.setTransform(S.dpr, 0, 0, S.dpr, 0, 0)" in draw


def test_an_enlarged_picture_shows_its_pixels_not_a_blur():
    draw = _body(_app_js(), "function draw(", "/* registration corners */")
    assert "ctx2d.imageSmoothingEnabled = S.view.k * S.dpr < 0.999" in draw


def test_the_picture_is_asked_for_at_the_size_the_screen_shows():
    """Rounded down to a 128 px step, so the kept picture serves a slightly
    different window size too, and never larger than the scan."""
    size = _body(_app_js(), "function pictureScale(", "\n}\n")
    assert "S.cssW * S.dpr / S.nativeCols" in size
    assert "Math.floor(longest * fit / 128) * 128" in size
    assert "Math.min(longest," in size
    load = _body(_app_js(), "function loadImage(", "function wlFromParams")
    assert "scale=${scale}" in load and "pictureScale()" in load


def test_fit_shows_the_picture_one_to_one_on_whole_pixels():
    """What the server averaged, with nothing resampled by the browser: one
    picture pixel per screen pixel, starting on a whole screen pixel."""
    fit = _body(_app_js(), "function zoomFit(", "\n}\n")
    assert "Math.min(fill, 1 / S.dpr)" in fit
    assert "Math.round(css * S.dpr) / S.dpr" in fit


def test_the_viewer_is_measured_before_the_picture_is_asked_for():
    """Measured while hidden, the viewer is 0 px and the picture would come
    at the smallest size and then be downloaded a second time."""
    body = _body(_app_js(), "async function openAnalysis(", "\n}\n")
    assert body.index('showTab("analyze")') < body.index("loadImage()")


def test_a_resized_viewer_asks_again_once_resizing_stops():
    resize = _body(_app_js(), "function resizeCanvas(", "\n}\n")
    assert "pictureScale() !== S.pictureScale" in resize
    assert "setTimeout(() => loadImage(S.imageParams)" in resize
    load = _body(_app_js(), "function loadImage(", "function wlFromParams")
    assert "S.view.k *= S.imgEl.width / img.width" in load, \
        "a picture of another size must keep the view where it was"


def test_a_picture_for_an_analysis_no_longer_open_is_not_drawn():
    load = _body(_app_js(), "function loadImage(", "function wlFromParams")
    assert "if (S.aid !== aid) return;" in load


# ------------------------------------------------ full detail on zoom, page

def test_the_page_and_the_server_agree_on_the_pieces():
    """Different sizes would put every piece in the wrong place."""
    import re
    from phantom_qa import imaging
    m = re.search(r"const TILE = (\d+), LEVELS = (\d+);", _app_js())
    assert (int(m.group(1)), int(m.group(2))) == (imaging.TILE, imaging.LEVELS)


def test_detail_is_fetched_only_after_the_view_has_been_still():
    """The user's decision: 1 s of stillness, adjustable. Every change of the
    view starts the time again, so nothing is fetched on the way to where the
    operator is going."""
    app_js = _app_js()
    boot = _body(app_js, "async function initAuth(", "\n}\n")
    assert "a.detail_delay_s" in boot
    sched = _body(app_js, "function scheduleDetail(", "\n}\n")
    for part in ("S.view.k", "S.view.tx", "S.view.ty", "S.shownParams"):
        assert part in sched, f"a change of {part} must restart the wait"
    assert "clearTimeout(S.detailTimer)" in sched
    assert "setTimeout(fetchDetail, S.detailDelayS * 1000)" in sched
    draw = _body(app_js, "function draw(", "/* registration corners */")
    assert "scheduleDetail()" in draw


def test_nothing_is_fetched_while_the_overview_is_enough():
    """At fit the overview already has a pixel for every screen pixel."""
    level = _body(_app_js(), "function detailLevel(", "\n}\n")
    assert "S.view.k * S.dpr <= 1.01) return -1" in level
    assert "Math.floor(Math.log2(1 / screenPerScanPx()))" in level
    assert "Math.min(LEVELS - 1, Math.max(0, level))" in level


def test_the_pieces_are_drawn_where_they_are_in_the_scan():
    """In scan pixels, the coordinates of every outline, so the measuring
    areas stay exactly where they were."""
    detail = _body(_app_js(), "function drawDetail(", "\n}\n")
    assert "perScanPx = S.view.k * S.imgScale" in detail
    assert "ctx2d.scale(perScanPx, perScanPx)" in detail
    assert "ctx2d.drawImage(t.img, tx * TILE * f, ty * TILE * f," in detail
    assert "t.img.width * f, t.img.height * f)" in detail
    assert "ctx2d.imageSmoothingEnabled = z * f < 0.999" in detail


def test_a_coarser_piece_is_never_drawn_over_a_finer_one():
    detail = _body(_app_js(), "function drawDetail(", "\n}\n")
    # Down to level 0: a finer piece that has arrived (a preloaded one) is
    # the best there is at any zoom, and it goes on top.
    assert "for (let level = LEVELS - 1; level >= 0; level--)" in detail
    draw = _body(_app_js(), "function draw(", "/* registration corners */")
    assert draw.index("ctx2d.drawImage(S.wlPreview || S.imgEl") \
        < draw.index("drawDetail()"), "the overview goes under the pieces"


def test_a_new_window_gets_its_own_pieces():
    """A piece of the old window over the new picture would show grey levels
    the slider no longer names."""
    app_js = _app_js()
    assert "`${S.shownParams}|${level}|${tx}|${ty}`" in app_js
    load = _body(app_js, "function loadImage(", "function wlFromParams")
    assert "if (S.shownParams !== params) clearTiles()" in load
    fetch = _body(app_js, "function fetchDetail(", "\n}\n")
    assert "S.wlPreview" in fetch.split("visibleTiles")[0], \
        "no pieces of the old window while a preview stands in"
    piece = _body(app_js, "function newTile(", "\n}\n")
    assert "tile/${level}/${tx}/${ty}" in piece and "${params}" in piece
    assert "params = S.shownParams" in piece
    detail = _body(app_js, "function drawDetail(", "\n}\n")
    assert "S.wlPreview) return" in detail


def test_another_analysis_starts_without_pieces():
    clear = _body(_app_js(), "function clearAnalysisState(", "\n}\n")
    assert "clearTiles()" in clear
    assert "clearTimeout(S.detailTimer)" in clear
    tiles = _body(_app_js(), "function clearTiles(", "\n}\n")
    assert "e.ctrl.abort()" in tiles, "downloads of the old one must stop"
    assert "S.tiles = new Map()" in tiles and "S.tileQueue = []" in tiles


# ------------------------------------------------ full detail never blocks

def test_at_most_two_pieces_download_at_once():
    """More at once only share a slow link, each arriving later."""
    app_js = _app_js()
    assert "const TILE_PARALLEL = 2;" in app_js
    pump = _body(app_js, "function pumpTiles(", "\n}\n")
    assert "while (S.tileActive < TILE_PARALLEL && S.tileQueue.length)" in pump
    load = _body(app_js, "async function loadTile(", "\n}\n")
    assert "S.tileActive++" in load
    finish = load[load.index("} finally {"):]
    assert "S.tileActive--" in finish and "pumpTiles()" in finish, \
        "a finished, failed or cancelled download must free its place"


def test_the_centre_of_the_screen_comes_first():
    fetch = _body(_app_js(), "function fetchDetail(", "\n}\n")
    assert "scr2nat([S.cssW / 2, S.cssH / 2])" in fetch
    assert "S.tileQueue.sort((a, b) => away(a) - away(b))" in fetch
    assert fetch.index("S.tileQueue.sort(") < fetch.index("pumpTiles()")


def test_pieces_the_operator_moved_away_from_are_cancelled():
    """At once, on every change of the view — not after the stillness time —
    so the link is free for what is on screen."""
    app_js = _app_js()
    sched = _body(app_js, "function scheduleDetail(", "\n}\n")
    assert "pruneDetail(detailLevel())" in sched
    prune = _body(app_js, "function pruneDetail(", "\n}\n")
    assert "e.ctrl.abort()" in prune
    assert "pieceWanted(e, need)" in prune
    load = _body(app_js, "async function loadTile(", "\n}\n")
    assert "signal: entry.ctrl.signal" in load
    assert 'err.name !== "AbortError"' in load, "a cancel is not a failure"


def test_pieces_never_hold_up_the_page():
    """Decoded off the page's own thread, and exactly as sent."""
    load = _body(_app_js(), "async function loadTile(", "\n}\n")
    assert "createImageBitmap(" in load
    assert 'colorSpaceConversion: "none"' in load
    assert "no-store" not in load and "cache:" not in load, \
        "the browser's cache must give back pieces seen before"


def test_the_screen_says_when_full_detail_is_still_coming():
    app_js = _app_js()
    note = _body(app_js, "function updatePictureNote(", "\n}\n")
    assert '"Loading full detail…"' in note and "detailMissing()" in note
    # The exact-picture messages come first: they matter more.
    assert note.index("S.imageFailed") < note.index("S.wlPreview") \
        < note.index("detailMissing()")
    missing = _body(app_js, "function detailMissing(", "\n}\n")
    assert "visibleTiles(need).some(" in missing and "!covered(need" in missing


def test_a_piece_that_keeps_failing_is_said_not_retried_forever():
    app_js = _app_js()
    failed = _body(app_js, "function tileFailed(", "\n}\n")
    assert "tries >= 3" in failed and "S.detailFailed = true" in failed
    note = _body(app_js, "function updatePictureNote(", "\n}\n")
    assert '"Full detail could not be loaded — zoom or pan to try again"' in note
    fetch = _body(app_js, "function fetchDetail(", "\n}\n")
    assert "(S.tileTries.get(key) || 0) >= 3" in fetch


# ------------------------------------------------ preloading where one zooms

def _regions(client, geometry=None, shape=(1400, 1400)):
    """The preload boxes for a phantom registered at 4 px/mm, centred at
    (700, 700) — the transform the synthetic tests use."""
    from types import SimpleNamespace
    from phantom_qa.registration import Transform
    T = Transform(A=np.array([[4.0, 0.0], [0.0, 4.0]]), t=np.array([700.0, 700.0]))
    return client.mod._detail_regions({"geometry": geometry},
                                      SimpleNamespace(transform=T), shape)


def test_every_group_and_disc_gets_a_box_around_it(client):
    """The user's decision: full detail of the line-pair strip and the disc
    block ahead of time. One small box per group and per disc: the strip
    runs diagonally, so a box round all of it would be mostly other parts of
    the phantom."""
    boxes = _regions(client)
    names = [b["name"] for b in boxes]
    assert names[:5] == [f"line pairs {g}" for g in
                         ("G2.0", "G1.6", "G1.4", "G1.2", "G1.1")]
    assert names[5:9] == ["line-pair strip"] * 4
    assert names[9:] == [f"low contrast L{i}" for i in range(1, 9)]
    # G2.0 at (-84.64, -1.31) mm: half the 12.6 mm group plus the 4 mm
    # margin, at 4 px/mm, either side of its place on the scan.
    x, y, half = -84.64 * 4 + 700, -1.31 * 4 + 700, (6.3 + 4.0) * 4
    assert boxes[0]["px"] == [int(x - half), int(y - half),
                              int(np.ceil(x + half)), int(np.ceil(y + half))]


def test_a_disc_box_holds_the_disc_and_its_background_ring(client):
    """L1 from the block's centre and angle, the way the analysis places it;
    the box reaches past the 16 mm background ring by the margin."""
    import math
    lc = client.mod.pdef.lowcontrast
    a = math.radians(lc["angle_deg"])
    c = lc["circles"][0]
    mx = lc["center_mm"][0] + c["u_mm"] * math.cos(a) - c["v_mm"] * math.sin(a)
    my = lc["center_mm"][1] + c["u_mm"] * math.sin(a) + c["v_mm"] * math.cos(a)
    x0, y0, x1, y1 = _regions(client)[9]["px"]
    assert x0 < mx * 4 + 700 < x1 and y0 < my * 4 + 700 < y1
    assert x1 - x0 >= (8 + 4) * 2 * 4


def test_placed_measuring_points_take_over_from_the_definition(client):
    geometry = {"linepairs": {"groups": [{"id": "G2.0",
                                          "roi": {"center_px": [500.0, 600.0]}}]},
                "lowcontrast": {"block": {"center_mm": [0.0, 0.0],
                                          "angle_deg": 0.0}}}
    boxes = _regions(client, geometry)
    x0, y0, x1, y1 = boxes[0]["px"]
    assert x0 < 500 < x1 and y0 < 600 < y1
    lc = client.mod.pdef.lowcontrast
    c = lc["circles"][0]                           # at angle 0: (u, v) mm
    x0, y0, x1, y1 = boxes[9]["px"]
    assert x0 < c["u_mm"] * 4 + 700 < x1 and y0 < c["v_mm"] * 4 + 700 < y1


def test_the_strip_is_covered_as_a_band_not_five_spots(client):
    """A box halfway between each two neighbouring groups closes the gaps
    between them. On the blue prints the strip is mounted the other way
    round, and its groups do not sit at the definition's positions."""
    boxes = _regions(client)
    groups = [np.array([(b["px"][0] + b["px"][2]) / 2, (b["px"][1] + b["px"][3]) / 2])
              for b in boxes[:5]]
    for a, b, mid in zip(groups, groups[1:], boxes[5:9]):
        x0, y0, x1, y1 = mid["px"]
        cx, cy = (a + b) / 2
        assert x0 < cx < x1 and y0 < cy < y1


def test_boxes_stop_at_the_edge_of_the_scan(client):
    for b in _regions(client, shape=(700, 400)):
        x0, y0, x1, y1 = b["px"]
        assert 0 <= x0 < x1 <= 400 and 0 <= y0 < y1 <= 700, b


@needs_samples
def test_on_a_real_scan_each_box_holds_what_it_is_for(client):
    """Before the measuring points exist the boxes come from the phantom
    definition through the registration; the margin has to absorb the few mm
    by which the real positions differ."""
    with open(SAMPLES[0], "rb") as f:
        up = client.post("/api/analyses",
                         files={"file": ("scan.dcm", io.BytesIO(f.read()))},
                         data={"site": "T", "phantom": "EXACT-PRELOAD"})
    aid = up.json()["analyses"][0]["id"]
    before = client.get(f"/api/analyses/{aid}").json()["detail_regions"]
    assert len(before) == 17
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    geom = client.get(f"/api/analyses/{aid}").json()["geometry"]
    for box, g in zip(before, geom["linepairs"]["groups"]):
        x0, y0, x1, y1 = box["px"]
        cx, cy = g["roi"]["center_px"]
        assert x0 < cx < x1 and y0 < cy < y1, (box["name"], cx, cy)


def test_the_preload_is_part_of_the_step_by_step_workflow_only():
    """Field analysis, when it comes, shows no zoomed picture to wait for."""
    app_js = _app_js()
    opening = _body(app_js, "async function openAnalysis(", "\n}\n")
    # Held back while Field analysis is on offer or done, and started when
    # the operator chooses to work step by step (startWithheldPreload).
    assert "S.preloadWithheld = fieldAhead(rec);" in opening
    assert "S.preloadPending = !S.preloadWithheld;" in opening
    ahead = _body(app_js, "function fieldAhead(", "\n}\n")
    assert 'rec.analysis_mode === "field"' in ahead
    assert "rec.field_offer.offered" in ahead
    assert "S.detailRegions = rec.detail_regions || []" in opening
    load = _body(app_js, "function loadImage(", "function wlFromParams")
    assert "if (first && S.preloadPending)" in load
    assert load.index("zoomFit()") < load.index("preloadDetail()"), \
        "the picture on screen first; the preload must not delay it"
    clear = _body(app_js, "function clearAnalysisState(", "\n}\n")
    assert "S.preloadPending = false" in clear


def test_the_preload_never_stands_in_the_way_of_the_screen():
    """One piece at a time, only while nothing downloads for the screen; a
    piece waiting in the preload that comes on screen goes to the front; and
    moving the view never cancels the preload."""
    app_js = _app_js()
    pump = _body(app_js, "function pumpTiles(", "\n}\n")
    assert pump.index("S.tileQueue") < pump.index("S.preloadQueue")
    assert "while (S.tileActive < 1 && S.preloadQueue.length)" in pump
    fetch = _body(app_js, "function fetchDetail(", "\n}\n")
    assert "have.preload = false;" in fetch and "S.tileQueue.push(have)" in fetch
    prune = _body(app_js, "function pruneDetail(", "\n}\n")
    assert "!e.preload" in prune
    preload = _body(app_js, "function preloadDetail(", "\n}\n")
    assert "newTile(0, tx, ty)" in preload, "full-size pieces serve every zoom"


def test_a_preloaded_full_size_piece_counts_at_every_zoom():
    covered = _body(_app_js(), "function covered(", "\n}\n")
    assert "if (t && t.ready) return true" in covered
    assert "covered(level - 1, x, y)" in covered
    assert "visibleTiles(need).some(([tx, ty]) => !covered(need, tx, ty))" \
        in _app_js()
    fetch = _body(_app_js(), "function fetchDetail(", "\n}\n")
    assert "covered(level, tx, ty)" in fetch


# ------------------------------------------------ the disc close-up

def _close_up(client, pixels=None, phantom="EXACT-CLOSEUP"):
    """An analysis with its measuring points placed, and its close-up's
    processed values and window as the server computes them."""
    aid = _uploaded(client, _pixels() if pixels is None else pixels, phantom)
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    meta = client.get(f"/api/analyses/{aid}/lowcontrast_view").json()
    rec = client.mod.store.get(aid)
    _, centre, angle = client.mod._block_placement(rec)
    view = client.mod.lowcontrast.block_view(client.mod._ctx(aid, rec),
                                             centre, angle)
    return aid, meta["key"], view


def test_every_contrast_step_is_made_exactly_from_the_processed_values(client):
    """The slider used to stretch the 8-bit picture in the browser; at 1.5-3x
    only 87-171 of 256 grey levels were left. Each step is now the close-up's
    window narrowed about its middle, applied to the unrounded values."""
    aid, key, view = _close_up(client)
    lo, hi = view["window"]
    values = np.where(np.isfinite(view["values"]), view["values"], lo)
    for gain in (20, 100, 250):
        mid, half = (lo + hi) / 2, (hi - lo) / 2 / (gain / 100)
        want = _window(values, mid - half, mid + half)
        for fmt in FORMATS:
            r = client.get(f"/api/analyses/{aid}/lowcontrast_view.{fmt}"
                           f"?key={key}&gain={gain}")
            assert r.status_code == 200
            assert np.array_equal(_grey(r.content), want), f"{fmt} at {gain} %"


def test_contrast_steps_are_the_sliders_steps(client):
    """Steps of 10 between 20 and 300: every step is one picture the browser
    keeps, not one per pixel of slider travel."""
    aid, key, _ = _close_up(client)
    get = lambda g: client.get(f"/api/analyses/{aid}/lowcontrast_view.png"
                               f"?key={key}&gain={g}").content
    assert get(133) == get(130)
    assert get(5) == get(20) and get(999) == get(300)
    assert get(100) != get(200)


def test_a_contrast_step_is_kept_under_its_name(client):
    aid, key, _ = _close_up(client)
    for fmt in FORMATS:
        r = client.get(f"/api/analyses/{aid}/lowcontrast_view.{fmt}"
                       f"?key={key}&gain=150")
        assert r.headers["cache-control"] == KEEP, fmt
        stale = client.get(f"/api/analyses/{aid}/lowcontrast_view.{fmt}"
                           f"?key={'0' * 16}&gain=150")
        assert stale.headers["cache-control"] == NO_KEEP, fmt


def test_the_close_up_says_it_is_a_processed_view():
    """Smoothed and flattened on purpose, so it must not pass for the scan's
    own pixels — those are in the main image."""
    app_js = _app_js()
    assert "Processed view — smoothed to make the discs\n      visible" in app_js
    assert 'step="10"' in app_js[app_js.index('id="lc-view-gain"') - 120:
                                 app_js.index('id="lc-view-gain"') + 120]


def test_the_close_up_is_never_re_mapped_in_the_browser():
    draw = _body(_app_js(), "function drawBlockView(", "\n}\n")
    for trick in ("getImageData", "putImageData", "lut"):
        assert trick not in draw, f"{trick} in drawBlockView"
    step = _body(_app_js(), "function onBlockGain(", "\n}\n")
    assert "blockPictureUrl(v.aid, v.meta.key, gain)" in step
    assert "gen !== blockGainGen" in step, "only the newest step is drawn"


# ------------------------------------------------ the window/level preview

def _index_html() -> str:
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        return f.read()


def test_the_preview_is_labelled_until_the_exact_picture_is_on_screen():
    """The user's decision: a quick preview while the slider moves is allowed
    only "clearly labelled". The label is tied to the preview itself, so it
    cannot go before the exact picture replaces it."""
    note = _body(_app_js(), "function updatePictureNote(", "\n}\n")
    assert '"Preview — exact picture loading"' in note
    assert "else if (S.wlPreview)" in note
    assert 'note.classList.toggle("hidden", !text)' in note
    draw = _body(_app_js(), "function draw(", "/* registration corners */")
    assert draw.index("updatePictureNote()") < draw.index("if (!S.imgEl) return")


def test_the_label_sits_over_the_picture_and_is_announced():
    html = _index_html()
    wrap = html[html.index('<div id="viewer-wrap">'):]
    wrap = wrap[:wrap.index("</div>\n    </div>")]
    assert 'id="picture-note" class="hidden" role="status"' in wrap
    with open(os.path.join(STATIC, "style.css"), encoding="utf-8") as f:
        css = f.read()
    rule = css[css.index("#picture-note {"):]
    assert "position: absolute" in rule[:rule.index("}")]
    assert "pointer-events: none" in rule[:rule.index("}")], \
        "the label must not swallow clicks meant for the picture"


def test_a_failed_exact_picture_is_said_not_hidden():
    """Before, a failed download left the preview on screen looking exact."""
    app_js = _app_js()
    note = _body(app_js, "function updatePictureNote(", "\n}\n")
    assert '"Exact picture could not be loaded — try again"' in note
    assert note.index("S.imageFailed") < note.index("S.wlPreview"), \
        "a failure must be said even while a preview is on screen"
    load = _body(app_js, "function loadImage(", "function wlFromParams")
    fail = load[load.index("img.onerror"):]
    assert "S.imageFailed = true" in fail
    assert "S.wlPreview = null" not in fail, "the preview must stay labelled"
    assert '$("#picture-retry").addEventListener("click", () => loadImage(S.imageParams))' \
        in app_js


def test_only_the_newest_picture_request_reaches_the_screen():
    """A late answer for an older window would show a window the slider no
    longer names — and drop the label as if it were exact."""
    load = _body(_app_js(), "function loadImage(", "function wlFromParams")
    assert "const seq = ++S.imageRequest" in load
    ok = load[load.index("img.onload"):load.index("img.onerror")]
    assert "if (seq !== S.imageRequest) return;" in ok
    assert "seq !== S.imageRequest" in load[load.index("img.onerror"):]


def test_a_picture_that_arrives_after_the_slider_moved_on_stays_a_preview():
    load = _body(_app_js(), "function loadImage(", "function wlFromParams")
    ok = load[load.index("img.onload"):load.index("img.onerror")]
    assert ok.index("S.wlPreview = null") < ok.index("renderWLPreview()"), \
        "the new picture becomes the base of a preview for the current window"


def test_the_resume_banner_says_what_it_really_means():
    """It is kept in this browser's storage: another browser, or a private
    window, on the same computer does not see it."""
    app_js = _app_js()
    assert "You have an unfinished analysis in this browser." in app_js
    assert "on this computer." not in app_js


def test_the_page_takes_the_version_from_the_server():
    app_js = _app_js()
    boot = app_js[app_js.index("async function initAuth("):]
    boot = boot[:boot.index("\n}\n")]
    assert "a.picture_version" in boot and "S.pictureVersion" in boot


def test_the_window_preview_does_not_care_what_format_it_was_given():
    """The preview re-maps the decoded picture already on screen. Nothing in
    it may depend on how those bytes travelled, or switching the format would
    quietly break the one control that makes a slow link bearable."""
    app_js = _app_js()
    body = app_js[app_js.index("function renderWLPreview("):]
    body = body[:body.index("\nlet wlTimer")]
    assert "drawImage(S.imgEl" in body and "getImageData" in body
    assert "px[i] = px[i + 1] = px[i + 2] = lut[px[i]]" in body, \
        "it reads one channel of the decoded picture and writes all three"
    for word in ("png", "jpg", "jpeg", "webp"):
        assert word not in body.lower()
