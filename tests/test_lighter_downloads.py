"""The two heaviest things an operator downloads, made light.

From the plan for the second field test: "Consider that the user will work
with very low connection speed." After the first round of bandwidth work
(tests/test_bandwidth.py) two payloads still dominated:

  the printed report, 1.7 MB, of which one embedded PNG — the annotated
  overview of the scan — was 1.3 MB once base64 had grown it;
  the viewer's background picture, about 1.1 MB as PNG, fetched every time an
  analysis is opened and every time the window settles on a new value.

Both are pictures for looking at. Every number is measured on the original
scan on the server, and the low-contrast discs are placed on their own
lossless close-up, so neither picture needs to be lossless. The report now
carries the overview as JPEG and its charts as palette PNGs, and the viewer
draws a JPEG of the same render. The budgets on a real scan are in
test_bandwidth.py; this file checks that the lighter pictures are still the
same pictures, and are guarded like the ones they replace.
"""

import base64
import io
import os
import re

import numpy as np
import pytest
from PIL import Image

from test_authorization import ADMIN_PW
from test_unusable_exposures import SYNTHETIC, _png16

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "phantom_qa", "webapp", "static")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"

_variant = iter(range(40_000, 49_000))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


def _uploaded(client, phantom="LIGHTER"):
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses", files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": phantom})
    assert up.status_code == 200, up.text
    return up.json()["analyses"][0]["id"]


def _grey(payload: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(payload)).convert("L"), dtype=float)


def _app_js() -> str:
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------ the viewer picture as JPEG

def test_the_jpeg_is_served_as_a_jpeg(client):
    aid = _uploaded(client)
    r = client.get(f"/api/analyses/{aid}/image.jpg")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content[:3] == JPEG_MAGIC


@pytest.mark.parametrize("query", ["", "?scale=200", "?wc=1000&ww=2000",
                                   "?wc=3000&ww=400&scale=900"])
def test_it_is_the_same_picture_at_exactly_the_same_size(client, query):
    """The viewer places every outline through the picture's width over the
    scan's. A JPEG one pixel narrower than the PNG it replaces would move
    every ROI on screen, and nobody would know why.

    And it has to be the same render: the same window, the same resampling.
    JPEG may cost a grey level or two on average, not a different picture."""
    aid = _uploaded(client)
    png = client.get(f"/api/analyses/{aid}/image.png{query}").content
    jpg = client.get(f"/api/analyses/{aid}/image.jpg{query}").content
    a, b = _grey(png), _grey(jpg)
    assert a.shape == b.shape, f"PNG {a.shape} against JPEG {b.shape}"
    assert np.abs(a - b).mean() < 2.0, \
        f"mean difference {np.abs(a - b).mean():.2f} grey levels"


def test_it_follows_the_window_it_is_asked_for(client):
    """The exact render the browser asks for once the slider settles has to
    be the window it asked for, or the preview and the answer disagree."""
    aid = _uploaded(client)
    # Windows either side of the synthetic phantom's background level, so the
    # bulk of the picture has to come out mid-grey in one and near-black in
    # the other.
    low = _grey(client.get(f"/api/analyses/{aid}/image.jpg?wc=200&ww=400"
                           ).content)
    high = _grey(client.get(f"/api/analyses/{aid}/image.jpg?wc=4000&ww=8000"
                            ).content)
    assert np.abs(low - high).mean() > 40, "the window made no difference"


def test_it_is_single_channel_so_the_preview_reads_it_exactly(client):
    """The window preview reads one channel of each decoded pixel and writes
    it to all three. A greyscale JPEG decodes with the three equal, which a
    colour one only approximates — and greyscale is smaller as well."""
    aid = _uploaded(client)
    jpg = client.get(f"/api/analyses/{aid}/image.jpg").content
    assert Image.open(io.BytesIO(jpg)).mode == "L"


def test_the_two_formats_never_answer_for_each_other(client):
    """They share one cache. Keyed without the format, whichever was asked
    for first would be served under both names — PNG bytes labelled JPEG."""
    aid = _uploaded(client)
    for first, second, magic in (("png", "jpg", JPEG_MAGIC),
                                 ("jpg", "png", PNG_MAGIC)):
        client.get(f"/api/analyses/{aid}/image.{first}?scale=300")
        again = client.get(f"/api/analyses/{aid}/image.{second}?scale=300")
        assert again.content.startswith(magic), \
            f"image.{second} answered with the cached image.{first}"
        client.mod._img_cache.clear()


def test_it_may_be_kept_but_only_by_the_operator(client):
    """The same render as the PNG, so the same rule: the URL carries
    everything that varies it, and it is patient-adjacent imagery."""
    aid = _uploaded(client)
    cache = client.get(f"/api/analyses/{aid}/image.jpg"
                       ).headers.get("cache-control", "")
    assert "private" in cache and "max-age" in cache, cache
    assert "no-store" not in cache


def test_it_is_not_compressed_a_second_time(client):
    aid = _uploaded(client)
    r = client.get(f"/api/analyses/{aid}/image.jpg",
                   headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") != "gzip"


@pytest.mark.parametrize("query", ["scale=0", "scale=-5", "scale=99999999",
                                   "wc=nan&ww=nan", "wc=inf&ww=-inf"])
def test_it_survives_any_viewer_parameters(client, query):
    """The same hostile slider values the PNG is guarded against."""
    aid = _uploaded(client)
    r = client.get(f"/api/analyses/{aid}/image.jpg?{query}")
    assert r.status_code == 200, f"?{query} -> {r.status_code}"
    assert r.content[:3] == JPEG_MAGIC


def test_an_unknown_analysis_is_not_found(client):
    assert client.get("/api/analyses/deadbeef/image.jpg").status_code == 404


def test_a_deleted_analysis_stops_serving_it(client):
    """Privately cacheable and cached per worker, exactly like the PNG, so a
    delete handled by another worker must not leave it serveable."""
    aid = _uploaded(client, phantom="LIGHTER-DELETE")
    assert client.get(f"/api/analyses/{aid}/image.jpg").status_code == 200
    client.post(f"/api/analyses/{aid}/delete",
                json={"admin_password": ADMIN_PW, "reason": "regression test"})
    assert client.get(f"/api/analyses/{aid}/image.jpg").status_code == 404
    assert not [k for k in client.mod._img_cache if k[0] == aid], \
        "rendered views of a deleted record stayed in memory"


def test_a_discarded_analysis_takes_its_jpeg_with_it(client):
    aid = _uploaded(client, phantom="LIGHTER-DISCARD")
    client.get(f"/api/analyses/{aid}/image.jpg?scale=200")
    client.post(f"/api/analyses/{aid}/discard", json={"confirm": True})
    assert client.get(f"/api/analyses/{aid}/image.jpg?scale=200"
                      ).status_code == 404


# --------------------------------------------------------- what the page asks

def test_the_viewer_asks_for_the_jpeg():
    app_js = _app_js()
    load = app_js[app_js.index("function loadImage("):]
    load = load[:load.index("function wlFromParams")]
    assert "/image.jpg" in load, "the viewer still downloads the PNG"
    assert "/image.png" not in load


def test_no_picture_of_the_whole_scan_is_fetched_as_png():
    """The duplicate dialog's thumbnail was the other caller: 13 kB as PNG,
    under 4 kB as JPEG. The low-contrast close-up stays PNG on purpose — the
    discs are a few grey levels deep and are placed by eye on it."""
    app_js = _app_js()
    assert "/image.png" not in app_js
    assert "/image.jpg?scale=200" in app_js
    assert "lowcontrast_view.png" in app_js


def test_the_window_preview_does_not_care_what_format_it_was_given():
    """The preview re-maps the decoded picture already on screen. Nothing in
    it may depend on how those bytes travelled, or switching the format would
    have quietly broken the one control that makes a slow link bearable."""
    app_js = _app_js()
    body = app_js[app_js.index("function renderWLPreview("):]
    body = body[:body.index("\nlet wlTimer")]
    assert "drawImage(S.imgEl" in body and "getImageData" in body
    assert "px[i] = px[i + 1] = px[i + 2] = lut[px[i]]" in body, \
        "it reads one channel of the decoded picture and writes all three"
    for word in ("png", "jpg", "jpeg"):
        assert word not in body.lower()


# ------------------------------------------------------------ the report

def _embedded(html: str):
    """Every picture in a report, as (MIME subtype, decoded bytes)."""
    return [(kind, base64.b64decode(data)) for kind, data in
            re.findall(r'data:image/(\w+);base64,([A-Za-z0-9+/=]+)', html)]


def _record():
    from test_store_labels import results_for
    return {"id": "abc123def456", "created_at": "2026-08-01 10:00:00",
            "acquired_at": "2026-07-27 09:37:26", "source_name": "scan.dcm",
            "sha256": "a" * 64, "signature": "sig", "algo_version": "1.0.0",
            "sid_mm": 1000.0, "site": "Goma", "phantom": "MSF-01",
            "results": results_for(), "meta": {}, "reg": None,
            "geometry": None, "status": "pass", "is_baseline": 0,
            "validation_status": ""}


def _noisy_png(width, height) -> bytes:
    rng = np.random.default_rng(5)
    pixels = rng.normal(128, 30, (height, width, 3)).clip(0, 255)
    buf = io.BytesIO()
    Image.fromarray(pixels.astype(np.uint8)).save(buf, format="png")
    return buf.getvalue()


def test_an_overview_handed_in_as_png_still_travels_as_jpeg():
    """The web route renders JPEG to begin with; anything else is re-encoded,
    so no caller can quietly put the megabyte back."""
    from phantom_qa.report import build_report
    big = _noisy_png(2000, 1500)
    html = build_report(_record(), overlay=big)
    photos = [raw for kind, raw in _embedded(html) if kind == "jpeg"]
    assert len(photos) == 1, "the overview is not embedded as JPEG"
    assert {kind for kind, _ in _embedded(html)} <= {"jpeg", "png"}
    pic = Image.open(io.BytesIO(photos[0]))
    assert pic.width == 1400, f"{pic.width} px wide; wider than any page needs"
    assert pic.height == 1050, "the overview lost its proportions"
    assert len(photos[0]) < len(big) / 3


def test_an_overview_that_is_already_jpeg_is_not_compressed_twice():
    """A second lossy pass would cost quality and gain almost nothing."""
    from phantom_qa.report import build_report
    buf = io.BytesIO()
    Image.open(io.BytesIO(_noisy_png(600, 400))).save(buf, format="jpeg",
                                                      quality=80)
    html = build_report(_record(), overlay=buf.getvalue())
    photos = [raw for kind, raw in _embedded(html) if kind == "jpeg"]
    assert photos == [buf.getvalue()]


def test_the_overview_the_route_renders_is_a_jpeg_fit_for_a_page():
    from phantom_qa import pipeline
    from test_unusable_exposures import _as_scan
    from phantom_qa.analysis.common import Ctx
    from phantom_qa.phantom_def import load_default
    from phantom_qa.registration import Transform
    scan = _as_scan(SYNTHETIC["saturated"]())
    ctx = Ctx(pixels=scan.pixels, pdef=load_default(), reg=None,
              T=Transform(A=np.array([[4.0, 0.0], [0.0, 4.0]]),
                          t=np.array([700.0, 700.0])))
    raw = pipeline.render_overlay(scan, ctx, {}, fmt="jpeg")
    assert raw[:3] == JPEG_MAGIC
    pic = Image.open(io.BytesIO(raw))
    assert pic.mode == "RGB", "the ROI outlines are coloured"
    assert 1200 <= pic.width <= 1400, pic.size
    # The command line's overview beside its results stays a PNG.
    assert pipeline.render_overlay(scan, ctx, {})[:8] == PNG_MAGIC


def test_a_chart_loses_bytes_but_nothing_a_reader_can_see():
    """A palette holds a chart's flat colours and the anti-aliasing between
    them; the palette PNG has to look like the full-colour one. The page white
    in particular has to stay white — the first quantiser tried turned it into
    254, which is invisible on screen but is not what was drawn."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from phantom_qa.report import _fig_to_b64

    def chart():
        fig, ax = plt.subplots(figsize=(6, 3))
        x = np.linspace(0, 20, 400)
        ax.plot(x, np.sin(3 * x), color="#333", lw=0.7)
        ax.bar(np.arange(8), np.arange(8) / 4, color="#4a7dbd")
        for p in range(0, 20, 2):
            ax.axvline(p, color="#d95050", lw=0.5, alpha=0.55)
        ax.set_title("intensity profile (red = fitted line grid)", fontsize=9)
        return fig

    full = io.BytesIO()
    fig = chart()
    fig.savefig(full, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    small = base64.b64decode(_fig_to_b64(chart()))

    assert small[:8] == PNG_MAGIC, "a chart must stay a PNG — JPEG blurs text"
    assert Image.open(io.BytesIO(small)).mode == "P"
    a = np.asarray(Image.open(io.BytesIO(full.getvalue())).convert("RGB"),
                   dtype=float)
    b = np.asarray(Image.open(io.BytesIO(small)).convert("RGB"), dtype=float)
    assert a.shape == b.shape
    assert np.abs(a - b).mean() < 1.0, \
        f"mean colour difference {np.abs(a - b).mean():.2f}"
    assert np.percentile(np.abs(a - b).max(axis=-1), 99.9) <= 16, \
        "some of the chart changed visibly"
    white = (a == 255).all(axis=-1)
    assert (b[white] == 255).all(), "the page white did not stay white"
    assert len(small) < len(full.getvalue())


def test_the_report_is_still_one_self_contained_file(client):
    """It is saved, mailed and archived at sites with no connection to the
    server, so every picture has to travel inside it."""
    aid = _uploaded(client, phantom="LIGHTER-REPORT")
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    html = client.get(f"/api/analyses/{aid}/report.html").text
    for outside in ('src="http', "src='http", 'href="http', "url(", "@import",
                    'src="api/', 'src="/'):
        assert outside not in html, f"the report reaches outside: {outside}"
    assert _embedded(html), "the report carries no pictures at all"
