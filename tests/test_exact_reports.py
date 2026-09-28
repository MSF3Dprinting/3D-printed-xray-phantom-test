"""The reports' pictures lose nothing.

The user's rule: "The compression could never lead to lose of visual details!
Never in any part of the app." Reports are not used for visual QC — the user
chose lossless WebP for them for that reason, to keep them light — but what
they show must still be the scan. The printed report's picture used to keep
every 2nd or 3rd scan pixel with no averaging, redrawn by the chart library,
as JPEG: 8-46 % of the finest line-pair group's bar contrast was left.

Now it is the scan averaged to 1000 px, lossless, with the measuring-area
outlines as vector lines over it. These tests decode it and compare it with
the averaging and window written out here.
"""

import base64
import io
import re

import numpy as np
import pytest
from PIL import Image

from conftest import SAMPLES, needs_samples
from test_unusable_exposures import _as_scan


def _grey(payload: bytes) -> np.ndarray:
    arr = np.asarray(Image.open(io.BytesIO(payload)))
    if arr.ndim == 3:
        assert np.array_equal(arr[..., 0], arr[..., 1]) \
            and np.array_equal(arr[..., 0], arr[..., 2]), "the picture is not grey"
        arr = arr[..., 0]
    return arr


def _window(values, lo, hi):
    """Nearest grey level, black and white outside — written out here."""
    v = np.asarray(values, dtype=np.float64)
    return np.clip(np.rint((v - lo) * (255.0 / (hi - lo))), 0, 255
                   ).astype(np.uint8)


def _ctx(pixels):
    from phantom_qa.analysis.common import Ctx
    from phantom_qa.phantom_def import load_default
    from phantom_qa.registration import Transform
    return Ctx(pixels=pixels, pdef=load_default(), reg=None,
               T=Transform(A=np.array([[4.0, 0.0], [0.0, 4.0]]),
                           t=np.array([700.0, 700.0])))


def _scan_2000x1500():
    """Integer values with noise everywhere, 2000 wide: the report picture is
    exactly half size, so each pixel is the mean of a 2x2 block."""
    rng = np.random.default_rng(11)
    return rng.integers(200, 4000, (1500, 2000)).astype(float)


GEOMETRY = {
    "lowcontrast": {"discs": [{"type": "circle", "center_px": [300.0, 400.0],
                               "radius_px": 25.0},
                              {"type": "annulus", "center_px": [300.0, 400.0],
                               "inner_radius_px": 40.0,
                               "outer_radius_px": 55.0}]},
    "linepairs": {"groups": [{"roi": {"type": "rect", "corners_px": [
        [10.0, 20.0], [110.0, 20.0], [110.0, 60.0], [10.0, 60.0]]}},
        {"type": "segment", "p0_px": [5.0, 5.0], "p1_px": [50.0, 50.0]}]},
}


# ------------------------------------------------ the picture of the scan

def test_the_report_picture_is_the_scan_averaged_and_lossless():
    from phantom_qa import pipeline
    pixels = _scan_2000x1500()
    out = pipeline.report_overview(_as_scan(pixels), _ctx(pixels), {})
    assert out["media"] in ("image/webp", "image/png")
    if out["media"] == "image/webp":
        assert out["picture"][12:16] == b"VP8L", "only the lossless kind"
    means = pixels.reshape(750, 2, 1000, 2).mean(axis=(1, 3))
    lo, hi = np.percentile(pixels, [1, 99])
    assert np.array_equal(_grey(out["picture"]), _window(means, lo, hi))


def test_it_is_no_larger_than_the_report_column():
    from phantom_qa import pipeline
    pixels = np.zeros((3000, 2400)) + np.arange(2400)
    out = pipeline.report_overview(_as_scan(pixels), _ctx(pixels), {})
    assert _grey(out["picture"]).shape == (1000, 800)
    small = np.zeros((600, 500)) + np.arange(500)
    out = pipeline.report_overview(_as_scan(small), _ctx(small), {})
    assert _grey(out["picture"]).shape == (600, 500), "never enlarged"


# ------------------------------------------------ the outlines over it

def test_the_outlines_are_vector_lines_in_scan_coordinates():
    """Laid over the picture by a viewBox of the scan's own size, so each
    line is where it was measured at any zoom and in print."""
    from phantom_qa import pipeline
    pixels = _scan_2000x1500()
    svg = pipeline.report_overview(_as_scan(pixels), _ctx(pixels),
                                   GEOMETRY)["svg"]
    assert svg.startswith('<svg class="outlines" viewBox="0 0 2000 1500" '
                          'preserveAspectRatio="none"')
    assert '<circle cx="300.0" cy="400.0" r="25.0"' in svg
    # The annulus as its two rings, dotted.
    assert svg.count('cx="300.0" cy="400.0"') == 3
    assert svg.count("stroke-dasharray") == 2
    assert '<polygon points="10.0,20.0 110.0,20.0 110.0,60.0 10.0,60.0"' in svg
    assert '<polyline points="5.0,5.0 50.0,50.0"' in svg


def test_each_pattern_keeps_its_colour():
    from phantom_qa import pipeline
    shapes = pipeline.outline_shapes(_ctx(np.zeros((10, 10))), GEOMETRY)
    colours = {s["color"] for s in shapes}
    assert colours == {pipeline._COLORS["lowcontrast"],
                       pipeline._COLORS["linepairs"]}


def test_a_pattern_whose_proposal_failed_draws_nothing():
    from phantom_qa import pipeline
    geometry = {**GEOMETRY, "lowcontrast": {"_error": "no block found"}}
    shapes = pipeline.outline_shapes(_ctx(np.zeros((10, 10))), geometry)
    assert {s["color"] for s in shapes} == {pipeline._COLORS["linepairs"]}


def test_the_command_line_picture_uses_the_same_outlines_and_stays_png():
    from phantom_qa import pipeline
    pixels = _scan_2000x1500()
    raw = pipeline.render_overlay(_as_scan(pixels), _ctx(pixels), GEOMETRY)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    import inspect
    src = inspect.getsource(pipeline.render_overlay)
    assert "outline_shapes(ctx, geometry_all)" in src
    assert "shrink_by_averaging" in src and "[::ds" not in src, \
        "skipping pixels invents patterns in the line pairs"


# ------------------------------------------------ in the report

def _report(overlay):
    from phantom_qa.report import build_report
    from test_store_labels import results_for
    rec = {"id": "abc123def456", "created_at": "2026-08-01 10:00:00",
           "acquired_at": "2026-07-27 09:37:26", "source_name": "scan.dcm",
           "sha256": "a" * 64, "signature": "sig", "algo_version": "1.0.0",
           "sid_mm": 1000.0, "site": "Goma", "phantom": "MSF-01",
           "results": results_for(), "meta": {}, "reg": None,
           "geometry": None, "status": "pass", "is_baseline": 0,
           "validation_status": ""}
    return build_report(rec, overlay=overlay)


def test_the_report_carries_the_picture_and_its_outlines_together():
    from phantom_qa import pipeline
    pixels = _scan_2000x1500()
    overview = pipeline.report_overview(_as_scan(pixels), _ctx(pixels),
                                        GEOMETRY)
    html = _report(overview)
    figure = re.search(r'<div class="overview"><img [^>]*src="data:image/'
                       r'(webp|png);base64,([A-Za-z0-9+/=]+)">(<svg .*?</svg>)'
                       r'</div>', html, re.S)
    assert figure, "the picture and the outlines must share one frame"
    assert base64.b64decode(figure.group(2)) == overview["picture"]
    assert figure.group(3) == overview["svg"]
    assert "image/jpeg" not in html


def test_the_frame_is_exactly_the_pictures_size():
    """Wider than the picture, the outlines would drift off what they mark."""
    html = _report({"picture": b"x", "media": "image/webp", "svg": "<svg></svg>"})
    css = html[html.index(".overview {"):]
    rule = css[:css.index("}")]
    assert "display:inline-block" in rule and "position:relative" in rule
    svg_rule = css[css.index(".overview svg.outlines {"):]
    svg_rule = svg_rule[:svg_rule.index("}")]
    assert "position:absolute" in svg_rule
    assert "width:100%" in svg_rule and "height:100%" in svg_rule


def test_no_picture_in_the_printed_report_is_reduced_or_lossy():
    """Neither the scan nor the charts: no palette, no JPEG, anywhere in the
    report's code."""
    import inspect
    from phantom_qa import report
    src = inspect.getsource(report)
    for lossy in ("quantize(", '"jpeg"', "'jpeg'", "JPEG", "quality="):
        assert lossy not in src, f"{lossy} in report.py"


# ------------------------------------------------ Export to PDF

def test_the_report_has_an_export_to_pdf_button():
    """The user's request: "add the export to PDF to every report". It opens
    the browser's print window, where the operator chooses Save as PDF — no
    PDF library on the server."""
    from phantom_qa.report import REPORT_SCRIPT
    html = _report(None)
    assert '<button id="export-pdf" type="button">Export to\nPDF</button>' in html
    assert "window.print()" in REPORT_SCRIPT
    assert f"<script>{REPORT_SCRIPT}</script>" in html
    assert html.index('id="export-pdf"') < html.index("<h1>"), "at the top"


def test_the_buttons_script_is_allowed_by_its_hash_and_nothing_else(
        tmp_path, monkeypatch):
    """The page's security policy forbids inline scripts in general; the one
    script is admitted by the hash of its exact text, only on report pages."""
    import hashlib
    from fastapi.testclient import TestClient
    from phantom_qa.report import REPORT_SCRIPT, REPORT_SCRIPT_CSP
    from test_authorization import _build_app, _login
    digest = base64.b64encode(
        hashlib.sha256(REPORT_SCRIPT.encode("utf-8")).digest()).decode()
    assert REPORT_SCRIPT_CSP == f"'sha256-{digest}'"
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    from test_unusable_exposures import SYNTHETIC, _png16
    up = c.post("/api/analyses", files={"file": ("s.png",
                                                 _png16(SYNTHETIC["saturated"]()))},
                data={"site": "T", "phantom": "PDF"})
    aid = up.json()["analyses"][0]["id"]
    csp = c.get(f"/api/analyses/{aid}/report.html").headers["Content-Security-Policy"]
    script_src = csp.split("script-src")[1].split(";")[0]
    assert REPORT_SCRIPT_CSP in script_src and "unsafe-inline" not in script_src
    assert REPORT_SCRIPT_CSP not in c.get("/api/analyses").headers[
        "Content-Security-Policy"]


def test_it_prints_on_a4_with_what_it_belongs_to_on_every_page():
    """A single printed page still says which phantom, which analysis and
    which page it is."""
    html = _report(None)
    css = html[html.index("@page {"):]
    page = css[:css.index("\n}\n")]
    assert "size: A4 portrait" in page
    assert '@top-left { content: "MSF Phantom QA — Goma / MSF-01"' in page
    assert '@top-right { content: "Analysis abc123def456"' in page
    assert '@bottom-right { content: "Page " counter(page) " of " counter(pages)' \
        in page


def test_nothing_to_click_is_printed_and_nothing_is_cut():
    html = _report(None)
    rules = html[html.index("@media print {"):]
    rules = rules[:rules.index("\n}\n")]
    assert ".no-print, button, input, select, textarea { display: none !important; }" \
        in rules
    assert "table, img, .overview, .summary, .idgrid { break-inside: avoid; }" in rules
    assert "h1, h2, h3 { break-after: avoid; }" in rules
    assert "thead { display: table-header-group; }" in rules, \
        "a long table repeats its heading on the next page"
    assert "print-color-adjust: exact" in rules, "the verdict colours print"
    assert '<div class="toolbar no-print">' in html


def test_no_label_can_break_out_of_the_page_margins():
    """Site and phantom are typed by operators and end up in a CSS string."""
    from phantom_qa.report import _css_string, _print_css
    assert _css_string('a"b\\c</style>') == '"a\\22 b\\5c c\\3c /style\\3e "'
    css = _print_css({"id": "x", "site": 'Go"ma', "phantom": "</style><b>"})
    assert "</style>" not in css and 'Go"ma' not in css


# ------------------------------------------------ Export to PDF, comparison

def _comparison(pictures=None, filters=None):
    from phantom_qa.comparison_report import build_comparison_report
    from test_store_labels import results_for
    recs = [{"id": f"x{i}", "site": "Goma", "phantom": "MSF-01", "status": "pass",
             "results": results_for(), "created_at": f"2026-09-2{i} 10:00:00"}
            for i in range(2)]
    return build_comparison_report(recs, filters=filters, pictures=pictures)


def test_the_comparison_has_an_export_to_pdf_button_too():
    """"Every report" — the comparison as well, with or without its picture
    table. The page keeps one script, admitted by its hash."""
    from phantom_qa.comparison_report import (COMPARISON_SCRIPT_CSP,
                                              _COMPARISON_SCRIPT)
    import hashlib
    for pictures in (None, {}):
        html = _comparison(pictures)
        assert '<button id="export-pdf" type="button">Export to\nPDF</button>' in html
        assert html.count("<script>") == 1
        assert f"<script>{_COMPARISON_SCRIPT}</script>" in html
    assert "window.print()" in _COMPARISON_SCRIPT
    digest = base64.b64encode(
        hashlib.sha256(_COMPARISON_SCRIPT.encode("utf-8")).digest()).decode()
    assert COMPARISON_SCRIPT_CSP == f"'sha256-{digest}'"


def test_the_comparison_prints_landscape_with_what_it_is_on_every_page():
    html = _comparison({}, filters={"site": "Goma"})
    css = html[html.index("@page {"):]
    page = css[:css.index("\n}\n")]
    assert "size: A4 landscape" in page
    assert '@top-left { content: "MSF Phantom QA — comparison"' in page
    assert '@top-right { content: "2 analyses · ' in page
    assert '@bottom-left { content: "site: Goma"' in page
    assert 'counter(page) " of " counter(pages)' in page


def test_the_comparison_never_cuts_a_chart_or_a_row_of_pictures():
    """Measured on a real 12-scan PDF: kept whole, a section taller than a
    page left the first page with only the title and was split anyway; a
    chart taller than a page was cut at its edge. So sections flow, and
    charts, pictures and rows stay whole — charts shrunk to fit a page."""
    html = _comparison({})
    rules = html[html.index("@media print {\n  body"):]
    rules = rules[:rules.index("\n}\n")]
    assert ".card { margin: 8px 0; }" in rules
    assert ".card { break-inside: avoid" not in rules
    assert "img, .facts, figure { break-inside: avoid; }" in rules
    assert "img { max-height: 170mm; object-fit: contain; }" in rules
    assert "thead { display: table-header-group; }" in rules
    assert ".scroll { overflow: visible; }" in rules, "nothing runs off the page"
    assert ".no-print, button, input, select, textarea { display: none !important; }" \
        in rules
    pics = html[html.index("table.pics { width:100%; table-layout:fixed; }"):]
    assert "table.pics tr { break-inside:avoid; }" in pics
    assert "table.pics thead { display:table-header-group; }" in pics


def test_no_filter_text_can_break_out_of_the_comparison_margins():
    html = _comparison({}, filters={"site": '"}</style><b>x'})
    css = html[html.index("@page {"):html.index("@media print {\n  body")]
    assert "</style>" not in css and '"}' not in css.replace('" counter', "")


@needs_samples
def test_on_a_real_scan_the_report_picture_is_exact(tmp_path, monkeypatch):
    """Through the real route: the picture in the report of the reference
    Philips scan is the stored values averaged and windowed, and every
    measuring area has its outline."""
    from fastapi.testclient import TestClient
    from phantom_qa import imaging
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    with open(SAMPLES[0], "rb") as f:
        up = c.post("/api/analyses", files={"file": ("scan.dcm", f.read())},
                    data={"site": "T", "phantom": "EXACT-REPORT"})
    aid = up.json()["analyses"][0]["id"]
    c.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    c.post(f"/api/analyses/{aid}/propose", json={})
    html = c.get(f"/api/analyses/{aid}/report.html").text
    raw = base64.b64decode(re.search(
        r'<div class="overview"><img [^>]*src="data:image/\w+;base64,'
        r'([A-Za-z0-9+/=]+)"', html).group(1))
    stored = mod._scan(aid).pixels
    h, w = stored.shape
    k = 1000 / max(h, w)
    lo, hi = np.percentile(stored, [1, 99])
    want = imaging.window_to_grey(
        imaging.shrink_by_averaging(stored, (round(w * k), round(h * k))), lo, hi)
    assert np.array_equal(_grey(raw), want)
    svg = re.search(r'<svg class="outlines".*?</svg>', html, re.S).group(0)
    assert f'viewBox="0 0 {w} {h}"' in svg
    assert svg.count("<circle") >= 8 * 3, "each disc: its circle and two rings"
