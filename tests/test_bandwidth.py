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

The second round re-encoded the two pictures that were left
(tests/test_lighter_downloads.py): on the reference Philips scan the report
fell from 1.72 MB to 0.39 with a JPEG overview. Both pictures went to JPEG
and came back, because the user's rule is that nothing may lose visual
detail. The viewer's picture is now lossless and sized to the viewer
(tests/test_exact_viewer.py): 341 kB as WebP at 1024 px on this scan. The
report's is the scan averaged to 1000 px, lossless, with vector outlines
(tests/test_exact_reports.py): the report is 0.61 MB, 0.45 as it travels.
Both measured 2026-09-28. The budgets below hold those figures on a real
scan.
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
                                  ("png", "image.png?scale=1024"),
                                  ("webp", "image.webp?scale=1024"),
                                  ("thumb_png", "image.png?scale=200"),
                                  ("thumb_webp", "image.webp?scale=200"))}
        c.close()
    for name, r in got.items():
        assert r.status_code == 200, f"{name}: {r.status_code}"
    return got


@needs_samples
def test_the_printed_report_fits_its_budget(real_scan):
    """1.72 MB once, 0.39 with a JPEG overview, 0.61 now that the overview is
    the scan averaged to 1000 px and lossless (measured 2026-09-28; 0.45 MB as
    it travels, compressed). The budget has room for the charts going
    lossless too; none for the overview going back to a 1400 px PNG (1.3 MB)
    or growing past the report column."""
    size = len(real_scan["report"].content)
    assert size < 700_000, f"the report is {size / 1e6:.2f} MB"


@needs_samples
def test_the_report_carries_the_overview_lossless_and_the_charts(real_scan):
    kinds = re.findall(r'data:image/(\w+);base64,',
                       real_scan["report"].text)
    assert "jpeg" not in kinds, "no lossy picture of the scan in a report"
    assert kinds.count("webp") >= 1, kinds
    assert len(kinds) >= 4, "the charts are missing from the report"


@needs_samples
def test_the_viewer_picture_fits_its_budget(real_scan):
    """Opened with every analysis, and again each time the window settles.

    Lossless, so the budget is what lossless costs at a laptop viewer's size:
    341 kB measured, with room for a little variation and none for a
    picture larger than it has to be. WebP is the smaller of the two exact
    formats; if it stops being so, the page's choice of it is wrong."""
    png, webp = real_scan["png"], real_scan["webp"]
    assert webp.headers["content-type"] == "image/webp"
    assert len(webp.content) < 400_000, f"{len(webp.content)} B"
    assert len(webp.content) < len(png.content), (
        f"WebP {len(webp.content)} B against PNG {len(png.content)} B")


@needs_samples
def test_the_duplicate_thumbnail_stays_small(real_scan):
    """10.5 kB as lossless WebP, measured; it settles "is that my scan"."""
    thumb = real_scan["thumb_webp"].content
    assert len(thumb) < 16_000, f"{len(thumb)} B"
    assert len(thumb) <= len(real_scan["thumb_png"].content)


def test_a_rendered_image_may_be_kept_but_only_by_the_operator(client, analysed):
    """The pixels never change and the URL carries everything that varies it.

    Private, because it is patient-adjacent imagery that must not rest in a
    shared proxy. Kept only when the address names the picture version the
    page learned at start (tests/test_exact_viewer.py)."""
    v = client.get("/api/auth").json()["picture_version"]
    answer = client.get(f"/api/analyses/{analysed}/image.png?v={v}")
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
