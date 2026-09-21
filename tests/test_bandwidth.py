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
"""

import os

import pytest

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
