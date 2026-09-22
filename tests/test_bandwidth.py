"""What the application puts on the wire.

Operators work over links where a listing is something you wait for. Three
habits dominated the traffic, and all three were free to fix:

* nothing was compressed — not the JSON, not the HTML, not the 127 kB of
  browser code;
* the rendered scan image was marked `no-store`, so roughly a megabyte came
  down again on every open and every window change, although the uploaded
  pixels never change;
* every window/level adjustment fetched a fresh render, which is most of why
  finding the right window for the low-contrast discs was reported as painful.

Measured before this work, at 512 kbit/s: the record 197 kB, a proposal 92 kB,
the printed report 1.7 MB, each image or window change about fifteen seconds.

The second round re-encoded the two pictures that were left, since both are
only looked at (tests/test_lighter_downloads.py). On the reference Philips
scan the report fell from 1.72 MB to 0.39 (1.3 MB to 0.27 as it travels,
compressed) and the viewer's picture from 1.1 MB to 0.11 — about four seconds
and two at 512 kbit/s, where they were twenty and seventeen. The budgets below
hold those gains on a real scan.
"""

import io
import os
import re

import pytest

from conftest import HAVE_SAMPLES, SAMPLES, SKIP_REASON, needs_samples
from test_unusable_exposures import SYNTHETIC, _png16

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "phantom_qa", "webapp", "static")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    return c


@pytest.fixture()
def analysed(client):
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = 4242
    up = client.post("/api/analyses",
                     files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": "BANDWIDTH"})
    aid = up.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    return aid


def test_text_responses_are_compressed(client, analysed):
    """The heaviest text payloads, which used to travel raw."""
    for path in (f"/api/analyses/{analysed}", "/api/analyses",
                 f"/api/analyses/{analysed}/report.html", "/app.js"):
        answer = client.get(path, headers={"Accept-Encoding": "gzip"})
        assert answer.status_code == 200, path
        if len(answer.content) < 900:
            continue                      # below the floor, not worth the CPU
        assert answer.headers.get("content-encoding") == "gzip", \
            f"{path} was sent uncompressed"


def test_compression_actually_shrinks_the_big_payloads(client, analysed):
    import gzip
    raw = client.get(f"/api/analyses/{analysed}").content
    packed = gzip.compress(raw, 6)
    assert len(packed) < len(raw) * 0.75, (
        f"the record compresses from {len(raw)} to {len(packed)} bytes; if "
        f"that ratio has collapsed the payload shape has changed")


# ------------------------------------------------ budgets on a real scan

@pytest.fixture(scope="module")
def real_scan(tmp_path_factory):
    """What one reference scan costs to open, look at and print.

    A synthetic image compresses far better than an X-ray, so a budget checked
    on one would pass whatever the encoding. Measured once for the module:
    the analysis takes a while, and every payload is captured before the next
    test reloads the application underneath it."""
    if not HAVE_SAMPLES:
        pytest.skip(SKIP_REASON)
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    with pytest.MonkeyPatch.context() as mp:
        mod = _build_app(tmp_path_factory.mktemp("budget"), mp)
        c = TestClient(mod.app)
        c.headers.update({"X-CSRF-Token": _login(c)})
        with open(SAMPLES[0], "rb") as f:
            up = c.post("/api/analyses",
                        files={"file": ("scan.dcm", io.BytesIO(f.read()))},
                        data={"site": "T", "phantom": "BUDGET"})
        aid = up.json()["analyses"][0]["id"]
        c.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
        c.post(f"/api/analyses/{aid}/propose", json={})
        c.post(f"/api/analyses/{aid}/confirm",
               json={"stage": "C", "save_profile": False})
        c.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
        got = {name: c.get(f"/api/analyses/{aid}/{path}")
               for name, path in (("report", "report.html"),
                                  ("png", "image.png"),
                                  ("jpg", "image.jpg"),
                                  ("thumb_png", "image.png?scale=200"),
                                  ("thumb_jpg", "image.jpg?scale=200"))}
        c.close()
    for name, r in got.items():
        assert r.status_code == 200, f"{name}: {r.status_code}"
    return got


@needs_samples
def test_the_printed_report_fits_its_budget(real_scan):
    """1.72 MB before, 0.39 after. The budget has room for the report to grow
    a little; it has no room for the overview going back to PNG (1.3 MB) or
    the charts losing their palette (another 0.14 MB)."""
    size = len(real_scan["report"].content)
    assert size < 450_000, f"the report is {size / 1e6:.2f} MB"


@needs_samples
def test_the_report_carries_the_overview_as_jpeg_and_the_charts_as_png(
        real_scan):
    kinds = re.findall(r'data:image/(\w+);base64,',
                       real_scan["report"].text)
    assert kinds.count("jpeg") == 1, kinds
    assert kinds.count("png") >= 3, "the charts are missing from the report"


@needs_samples
def test_the_viewer_picture_fits_its_budget(real_scan):
    """Opened with every analysis, and again each time the window settles."""
    png, jpg = real_scan["png"], real_scan["jpg"]
    assert jpg.headers["content-type"] == "image/jpeg"
    assert len(jpg.content) < 150_000, f"{len(jpg.content)} B"
    assert len(jpg.content) < len(png.content) / 5, (
        f"JPEG {len(jpg.content)} B against PNG {len(png.content)} B")


@needs_samples
def test_the_duplicate_thumbnail_is_lighter_as_jpeg(real_scan):
    thumb = real_scan["thumb_jpg"].content
    assert len(thumb) < 8_000, f"{len(thumb)} B"
    assert len(thumb) < len(real_scan["thumb_png"].content)


def test_a_rendered_image_may_be_kept_but_only_by_the_operator(client, analysed):
    """The pixels never change and the URL carries everything that varies it.

    Private, because it is patient-adjacent imagery that must not rest in a
    shared proxy."""
    answer = client.get(f"/api/analyses/{analysed}/image.png")
    cache = answer.headers.get("cache-control", "")
    assert "private" in cache and "max-age" in cache, cache
    assert "no-store" not in cache


@pytest.mark.parametrize("path", ["/api/analyses", "/api/labels",
                                  "/api/baselines", "/api/trends"])
def test_everything_else_is_still_never_cached(client, analysed, path):
    """Records, listings and verdicts change; only the picture is immutable."""
    assert client.get(path).headers["cache-control"] == "no-store"


def test_the_window_can_be_previewed_without_asking_the_server():
    """Adjusting the window used to cost a megabyte per attempt.

    The preview re-maps the picture already on screen, so the slider responds
    at once; the server is still asked for the exact rendering, but only after
    the slider settles."""
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        app_js = f.read()
    assert "function renderWLPreview(" in app_js
    body = app_js[app_js.index("function renderWLPreview("):]
    body = body[:body.index("\nlet wlTimer")]
    assert "api(" not in body and "fetch(" not in body and "loadImage" not in body, \
        "the preview must cost no traffic at all"
    assert "getImageData" in body and "lut" in body.lower(), \
        "it re-maps the displayed bytes through the requested window"

    handler = app_js[app_js.index("function onWL()"):]
    handler = handler[:handler.index("$(\"#wl-center\")")]
    assert "renderWLPreview()" in handler
    assert handler.index("renderWLPreview()") < handler.index("setTimeout"), \
        "the preview must come first; the request follows once it settles"


def test_the_preview_knows_which_window_it_is_remapping_from():
    """Without that it would compound its own approximations each time."""
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        app_js = f.read()
    assert "S.renderedWL" in app_js
    load = app_js[app_js.index("function loadImage("):]
    load = load[:load.index("function wlFromParams")]
    assert "S.renderedWL = asked" in load
    assert "S.wlPreview = null" in load, \
        "the exact render must supersede the preview"


def test_an_already_compressed_payload_is_not_compressed_again(client, analysed):
    """A PNG gains a fraction of a percent and costs real CPU per request.

    Starlette decides this from a module-level tuple with no constructor
    argument, so the application extends it. If a later version changes that
    mechanism this test fails, rather than the server quietly going back to
    spending time for nothing."""
    answer = client.get(f"/api/analyses/{analysed}/image.png",
                        headers={"Accept-Encoding": "gzip"})
    assert answer.status_code == 200
    assert answer.headers.get("content-encoding") != "gzip", \
        "the rendered image is being compressed a second time"
    assert answer.headers["content-type"] == "image/png"
