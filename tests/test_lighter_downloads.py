"""The printed report, made light.

From the plan for the second field test: "Consider that the user will work
with very low connection speed." After the first round of bandwidth work
(tests/test_bandwidth.py) the printed report still weighed 1.7 MB, of which
one embedded PNG — the annotated overview of the scan — was 1.3 MB once
base64 had grown it. The report then carried the overview as JPEG and its
charts as palette PNGs; since the user's rule that no picture may lose
anything, it carries the overview averaged to the report column and the
charts in full colour, both lossless WebP (tests/test_exact_reports.py). The
budgets on a real scan are in test_bandwidth.py; this file checks that the
pictures stay light and are still exactly the same pictures.

The viewer's picture went the same way to JPEG and came back: the user's rule
is that nothing may lose visual detail, and looking at that picture is the
inspection. Its tests are in tests/test_exact_viewer.py.
"""

import base64
import io
import re

import numpy as np
import pytest
from PIL import Image

from test_unusable_exposures import SYNTHETIC, _png16

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

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


def _overviews(html: str):
    """The picture of the scan in a report — not its charts — as
    (MIME subtype, decoded bytes)."""
    return [(kind, base64.b64decode(data)) for kind, data in re.findall(
        r'<img alt="The scan with the measuring areas outlined" '
        r'src="data:image/(\w+);base64,([A-Za-z0-9+/=]+)"', html)]


def test_an_overview_handed_in_as_bytes_travels_lossless_and_column_wide():
    """A caller drawing its own overview (the command line) hands in bytes.
    They are re-encoded so no caller can put the megabyte back — shrunk by
    averaging to the report column, lossless WebP, never a lossy pass. (The
    web route hands in the picture and its outlines separately; see
    tests/test_exact_reports.py.)"""
    from phantom_qa.report import build_report
    big = _noisy_png(2000, 1500)
    html = build_report(_record(), overlay=big)
    photos = _overviews(html)
    assert len(photos) == 1 and photos[0][0] == "webp",         "the overview is not embedded as lossless WebP"
    assert "jpeg" not in {kind for kind, _ in _embedded(html)}
    photos = [raw for _, raw in photos]
    assert photos[0][12:16] == b"VP8L"
    pic = Image.open(io.BytesIO(photos[0]))
    assert pic.width == 1000, f"{pic.width} px wide; wider than the column"
    assert pic.height == 750, "the overview lost its proportions"
    assert len(photos[0]) < len(big)


def test_an_overview_handed_in_as_jpeg_is_not_compressed_a_second_time():
    """Its losses are already made; re-encoding it lossy would add more."""
    from phantom_qa.report import build_report
    buf = io.BytesIO()
    Image.open(io.BytesIO(_noisy_png(600, 400))).save(buf, format="jpeg",
                                                      quality=80)
    html = build_report(_record(), overlay=buf.getvalue())
    (kind, raw), = _overviews(html)
    decoded = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))
    assert np.array_equal(decoded, np.asarray(
        Image.open(io.BytesIO(buf.getvalue())).convert("RGB")))


def test_a_chart_loses_nothing_and_still_travels_light():
    """A chart used to be reduced to a 64-colour palette for half the bytes;
    that moved up to 7 grey levels on edges and text, and the user's rule is
    that no picture may lose anything. Now it is lossless WebP: every pixel as
    the chart library drew it, and still smaller than the full-colour PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from phantom_qa.report import _chart

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
    src = _chart(chart())
    assert src.startswith("data:image/webp;base64,"), src[:30]
    small = base64.b64decode(src.split(",", 1)[1])

    assert small[12:16] == b"VP8L", "only the lossless kind of WebP"
    a = np.asarray(Image.open(io.BytesIO(full.getvalue())).convert("RGB"))
    b = np.asarray(Image.open(io.BytesIO(small)).convert("RGB"))
    assert np.array_equal(a, b), "the chart is not exactly as drawn"
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
