"""Comparing scans by looking at them.

From the first field test, item 7: "In comparative scans we also want to
visually compare the images, create table with images."

The comparison report was charts and numbers only. It now leads with a table:
one column per scan, one row per test area, every picture straightened to the
phantom's own frame so scans taken at any angle line up, the key numbers under
each picture, and one shared window per row so a brighter picture really is
brighter.

What these tests hold it to:

  every scan gets every picture, and a scan that has none gets a labelled
  empty cell rather than a gap that would shift the columns;
  the pictures fit the link — about 60–70 kB per scan, under 0.8 MB for ten;
  the page is self-contained, because a saved copy is how a comparison
  travels and it must keep its pictures with no server behind it;
  a scan's pictures are drawn once and kept, redrawn when its measuring
  points change, and removed with the analysis;
  and a picture that cannot be drawn costs that one cell, never the page.
"""

import base64
import gzip
import hashlib
import os
import re

import numpy as np
import pytest

from conftest import SAMPLES, needs_samples
from test_authorization import ADMIN_PW
from test_registration import synth_square
from test_unusable_exposures import _png16

_variant = iter(range(40_000, 49_000))

#: The budget the plan was approved with: all five pictures of one scan.
BUDGET_PER_SCAN = 70_000
#: A ten-scan comparison, pictures only.
BUDGET_TEN_SCANS = 800_000


@pytest.fixture()
def full_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


@pytest.fixture()
def fast_charts(monkeypatch):
    """The charts below the table, stubbed out.

    They cost seconds of matplotlib per report and are not what these tests
    are about — test_store_labels.py covers them. The whole-page tests use
    ``full_client`` so the page they check is the real one."""
    from phantom_qa import comparison_report as cr
    for name in ("_status_grid", "_wedge_curves", "_lowcontrast_curves",
                 "_panel", "_deviation_heatmap"):
        monkeypatch.setattr(cr, name, lambda *a, **k: None)
    monkeypatch.setattr(cr, "_variability_chart", lambda *a, **k: (None, []))


@pytest.fixture()
def client(full_client, fast_charts):
    return full_client


def _image(angle_deg=0.0):
    pixels, _ = synth_square(angle_deg=angle_deg)
    pixels[0, 0] = next(_variant)          # a new file every time, not a duplicate
    return pixels


def _computed(client, phantom="PICTURES", pixels=None):
    """An analysis taken all the way to stored results, through the API."""
    pixels = _image() if pixels is None else pixels
    up = client.post("/api/analyses", files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": phantom})
    assert up.status_code == 200, up.text
    aid = up.json()["analyses"][0]["id"]
    assert client.post(f"/api/analyses/{aid}/confirm",
                       json={"stage": "A"}).status_code == 200
    assert client.post(f"/api/analyses/{aid}/propose", json={}).status_code == 200
    for stage in ("B", "C"):
        client.post(f"/api/analyses/{aid}/confirm",
                    json={"stage": stage, "save_profile": False})
    r = client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    assert r.status_code == 200, r.text
    return aid


def _unregistered(client, phantom="NO-REG"):
    """A record with results but no registration, as an import or a broken
    upload can leave one. It still belongs in a comparison."""
    from test_store_labels import fake_scan, results_for
    store = client.mod.store
    aid = store.new_analysis(fake_scan(sha=f"{next(_variant):064d}"),
                             b"not an image", "sig", "1.0.0", "1.0",
                             labels={"site": "T", "phantom": phantom})
    store.update(aid, results=results_for(), status="pass", geometry={})
    return aid


def _report(client, *aids):
    r = client.get("/api/comparison_report.html?ids=" + ",".join(aids))
    assert r.status_code == 200, r.text[:400]
    return r


def _pictures_section(page: str) -> str:
    start = page.index('class="card pics-card"')
    return page[start:page.index("</section>", start)]


def _cells(section: str) -> list[str]:
    return re.findall(r"<td class='pic-cell'>(.*?)</td>", section, re.S)


def _count_renders(monkeypatch):
    from phantom_qa import thumbnails
    calls = []
    real = thumbnails.render_all

    def counting(ctx, geometry):
        calls.append(1)
        return real(ctx, geometry)

    monkeypatch.setattr(thumbnails, "render_all", counting)
    return calls


# ------------------------------------------------------ what the table holds

def test_every_scan_gets_a_picture_of_every_region(client):
    """Five rows, one column per scan, and a real picture in every cell."""
    from phantom_qa import thumbnails
    a, b = _computed(client), _computed(client, pixels=_image(angle_deg=90.0))
    section = _pictures_section(_report(client, a, b).text)
    cells = _cells(section)
    assert len(cells) == 2 * len(thumbnails.REGIONS)
    for cell in cells:
        assert '<figure class="pic"' in cell, cell[:300]
        assert re.search(r'src="data:image/(jpeg|png);base64,[A-Za-z0-9+/=]{200,}"',
                         cell), "a cell holds no embedded picture"
        # the stored numbers sit under every picture
        assert "class='pic-facts'" in cell
    for title in ("Whole phantom", "Line-pair strip", "Wedge",
                  "Low-contrast block", "Uniformity squares"):
        assert title in section, f"no row for {title}"


def test_a_scan_that_was_never_registered_gets_labelled_empty_cells(client):
    """Never a gap: a missing cell would slide the rest of the row one column
    left, and the reader would compare the wrong scans."""
    from phantom_qa import thumbnails
    good, bad = _computed(client), _unregistered(client)
    section = _pictures_section(_report(client, good, bad).text)
    cells = _cells(section)
    assert len(cells) == 2 * len(thumbnails.REGIONS)
    empty = [c for c in cells if "class='pic-missing'" in c]
    assert len(empty) == len(thumbnails.REGIONS)
    for c in empty:
        assert thumbnails.NOT_REGISTERED in c
        # the numbers are still there: they come from the stored results
        assert "class='pic-facts'" in c


def test_a_scan_without_a_placed_block_still_shows_what_it_can(client):
    """Four of the five pictures need only the registration. The close-up
    needs the stored block placement, and without it says so rather than
    guessing one."""
    from phantom_qa import thumbnails
    aid = _computed(client)
    store = client.mod.store
    geometry = store.get(aid)["geometry"]
    geometry["lowcontrast"].pop("block")
    store.update(aid, geometry=geometry)
    cells = _cells(_pictures_section(_report(client, aid).text))
    missing = [c for c in cells if "class='pic-missing'" in c]
    assert len(missing) == 1 and thumbnails.NO_BLOCK in missing[0]


def test_one_window_per_row_taken_from_the_reference_scan(client):
    """The shared window comes from the reference scan when the selection
    holds one, so every other scan is shown the way the reference looks. The
    low-contrast close-up is normalised to itself and is never put on a
    shared window — its brightness means nothing next to another scan's."""
    a, b = _computed(client), _computed(client)
    client.mod.store.set_baseline(b)
    from phantom_qa import thumbnails
    page = _report(client, a, b).text
    kept = {aid: thumbnails._read(client.mod.store.thumbs_dir(aid),
                                  _key(client, aid)) for aid in (a, b)}
    section = _pictures_section(page)
    rows = re.findall(r"<tr( data-ref-lo=\"[^\"]*\" data-ref-hi=\"[^\"]*\")?>"
                      r"<th class='row-head'>([^<]*)", section)
    by_title = {title: attrs for attrs, title in rows}
    assert by_title["Low-contrast block"] == "", \
        "the self-normalised close-up must not get a shared window"
    for region, title in (("phantom", "Whole phantom"),
                          ("uniformity", "Uniformity squares")):
        lo = float(re.search(r'data-ref-lo="([^"]+)"', by_title[title]).group(1))
        assert lo == pytest.approx(kept[b][region]["lo"], rel=1e-6), \
            f"{title}: the shared window is not the reference scan's"
    assert "shared window taken from here" in section
    # Without JavaScript the pictures show in their own windows — and the
    # note has to say so rather than claim a shared one.
    assert "<div class='win-note' data-own=" in section
    assert re.search(r"data-shared='[^']*'>Each picture in its own window\.",
                     section)
    assert "<label class='win-toggle' hidden>" in section


def _rows(section: str) -> dict:
    """Row title -> the row's cells."""
    out = {}
    for title, rest in re.findall(
            r"<th class='row-head'>([^<]+)(.*?)</tr>", section, re.S):
        out[title] = _cells(rest)
    return out


def test_scans_from_another_detector_are_not_put_on_its_window(client):
    """Pixel values only compare within one detector and protocol — the rule
    that already scopes a baseline. Seen in the first real mixed comparison:
    on a Philips scan's window, the Carestream line-pair and uniformity
    pictures were plain black, which reads as a fault that is not there.

    So each protocol shares a window of its own, and a scan alone on its
    protocol keeps its own window; the cells say which."""
    from phantom_qa.comparison_report import _ALONE_ON_PROTOCOL
    aids = [_computed(client) for _ in range(5)]
    store = client.mod.store
    for aid, sig in zip(aids, ("A", "A", "B", "B", "C")):
        store.update(aid, signature=sig)
    cells = _rows(_pictures_section(_report(client, *aids).text))["Whole phantom"]

    def window(cell, attr):
        m = re.search(rf'{attr}="([^"]+)"', cell)
        return float(m.group(1)) if m else None

    own = [window(c, "data-lo") for c in cells]
    target = [window(c, "data-rlo") for c in cells]
    assert target[0] == target[1] == own[0], "the first protocol shares scan 1"
    assert target[2] == target[3] == own[2], \
        "the second protocol shares its own first scan, not scan 1"
    assert own[4] is None and target[4] is None, \
        "a scan alone on its protocol keeps its own window"
    assert _ALONE_ON_PROTOCOL in cells[4]
    assert "another detector or protocol" in cells[2]
    assert "another detector or protocol" not in cells[0]


def _key(client, aid):
    from phantom_qa import ALGO_VERSION, thumbnails
    return thumbnails.cache_key(client.mod.store.get(aid),
                                pdef_version=client.mod.pdef.version,
                                algo_version=ALGO_VERSION)


# ------------------------------------------------------ what a saved copy is

def test_the_page_needs_nothing_from_anywhere_else(full_client):
    """A saved copy is how a comparison travels. Anything fetched from
    elsewhere would be missing from it — and would fail behind the proxy
    anyway. Checked on the whole page, charts included."""
    client = full_client
    page = _report(client, _computed(client), _unregistered(client)).text
    assert "http://" not in page and "https://" not in page
    assert "<link" not in page and "@import" not in page
    assert "<script src" not in page
    for src in re.findall(r'\ssrc="([^"]*)"', page):
        assert src.startswith("data:image/"), f"external source {src[:60]}"


def test_the_inline_script_is_allowed_by_its_hash_and_nothing_else(full_client):
    """The page's one script must run when served, which the policy forbids
    for inline code in general. It is admitted by the hash of its exact text,
    so editing the script without the hash following — or anything else
    inline — stays blocked."""
    client = full_client
    r = _report(client, _computed(client))
    scripts = re.findall(r"<script>(.*?)</script>", r.text, re.S)
    assert len(scripts) == 1
    digest = base64.b64encode(
        hashlib.sha256(scripts[0].encode("utf-8")).digest()).decode()
    csp = r.headers["Content-Security-Policy"]
    assert f"'sha256-{digest}'" in csp
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    # every other page keeps the strict policy
    other = client.get("/api/analyses").headers["Content-Security-Policy"]
    assert "sha256-" not in other


def test_a_damaged_picture_cannot_break_out_of_the_page(fast_charts):
    """The pictures are read back from files on the server's disk and pasted
    into attributes. A damaged file becomes a labelled empty cell — it can
    never close the attribute and put its own markup on the page."""
    from phantom_qa.comparison_report import build_comparison_report
    from test_store_labels import results_for
    hostile = {"mime": "image/jpeg", "window": "raw", "lo": 0, "hi": 1,
               "b64": 'AAAA"><img src=x onerror=alert(1)>', "w": 4, "h": 4}
    odd_mime = {"mime": 'image/jpeg"><b', "b64": "AAAA", "w": 4, "h": 4}
    recs = [{"id": "x1", "site": "S", "phantom": "P", "status": "pass",
             "results": results_for(), "created_at": "2026-09-21 10:00:00"}]
    page = build_comparison_report(
        recs, pictures={"x1": {"phantom": hostile, "wedge": odd_mime}})
    assert "onerror" not in page and 'jpeg"><b' not in page
    cells = _cells(_pictures_section(page))
    assert sum("class='pic-missing'" in c for c in cells) == len(cells)


def test_the_table_prints_on_landscape_pages(client):
    """Several scans side by side do not fit a portrait page."""
    page = _report(client, _computed(client)).text
    assert re.search(r"@page pictures \{[^}]*size:A4 landscape", page)
    assert ".pics-card { page:pictures; }" in page
    assert "table-layout:fixed" in page


# ------------------------------------------------------------------- budget

@needs_samples
def test_the_pictures_of_a_real_scan_fit_the_budget(sample_scans, pdef):
    """About 60–70 kB for all five pictures of a scan, which keeps a ten-scan
    comparison under 0.8 MB — some twelve seconds at 512 kbit/s.

    Measured on the reference DICOMs, because a synthetic square compresses
    far better than a real radiograph and would prove nothing."""
    from phantom_qa import pipeline, thumbnails
    totals = []
    for scan in sample_scans:
        reg = pipeline.run_stage_a(scan, pdef)
        ctx = pipeline.build_ctx(scan, pdef, reg, {"scan_meta": scan.meta})
        pics = thumbnails.render_all(ctx, pipeline.propose_all(ctx))
        assert all("b64" in p for p in pics.values()), pics
        total = sum(p["bytes"] for p in pics.values())
        assert total <= BUDGET_PER_SCAN, \
            f"{total} B for one scan's pictures — the budget is {BUDGET_PER_SCAN}"
        totals.append(total)
    assert 10 * max(totals) <= BUDGET_TEN_SCANS


@needs_samples
def test_what_a_real_comparison_sends_to_the_browser(client):
    """The cost on the wire, measured end to end on a reference scan.

    The pictures travel as base64 inside the page, a third larger than the
    files themselves; the page is gzipped in transit, which takes almost all
    of that back. What matters is what crosses the link."""
    with open(SAMPLES[0], "rb") as f:
        data = f.read()
    up = client.post("/api/analyses", files={"file": ("scan.dcm", data)},
                     data={"site": "T", "phantom": "REAL"})
    assert up.status_code == 200, up.text
    aid = up.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    assert client.post(f"/api/analyses/{aid}/compute",
                       json={"sid_mm": 1000.0}).status_code == 200
    section = _pictures_section(_report(client, aid).text)
    pictures = sum(len(base64.b64decode(b)) for b in
                   re.findall(r'base64,([A-Za-z0-9+/=]+)"', section))
    assert pictures <= BUDGET_PER_SCAN
    on_the_wire = len(gzip.compress(section.encode("utf-8"), 6))
    assert on_the_wire <= 1.1 * BUDGET_PER_SCAN, \
        f"the picture table costs {on_the_wire} B gzipped for one scan"


# -------------------------------------------------------------------- cache

def test_a_second_report_draws_nothing_again(client, monkeypatch):
    """Drawing needs the decoded scan — seconds and tens of megabytes — so a
    comparison reopened later must come from what was kept."""
    a, b = _computed(client), _computed(client)
    calls = _count_renders(monkeypatch)
    first = _report(client, a, b).text
    assert len(calls) == 2
    second = _report(client, a, b).text
    assert len(calls) == 2, "the second report drew the pictures again"
    assert _pictures_section(first) == _pictures_section(second)


def test_the_report_does_not_keep_the_scans_it_decoded(client):
    """A comparison touches many scans once each. Keeping each decoded one
    in the worker — tens of megabytes apiece — is memory nothing gives back."""
    a, b = _computed(client), _computed(client)
    client.mod._scans.clear()
    _report(client, a, b)
    assert not client.mod._scans


def test_moving_the_measuring_points_draws_new_pictures(client, monkeypatch):
    """The close-up is drawn at the stored block placement; move the block
    and the kept picture no longer shows what was measured."""
    aid = _computed(client)
    calls = _count_renders(monkeypatch)
    _report(client, aid)
    directory = client.mod.store.thumbs_dir(aid)
    before = sorted(os.listdir(directory))
    assert client.post(f"/api/analyses/{aid}/lowcontrast_block",
                       json={"angle_deg": -30.0}).status_code == 200
    assert client.post(f"/api/analyses/{aid}/compute",
                       json={"sid_mm": 1000.0}).status_code == 200
    _report(client, aid)
    assert len(calls) == 2, "the moved block was served from the old pictures"
    after = sorted(os.listdir(directory))
    assert len(after) == 1 and after != before, \
        "the pictures of the old placement were left behind"


def test_the_key_notices_what_the_sequence_number_alone_would_miss():
    """The measuring-point sequence number restarts after a re-run from
    registration and repeats after an undo followed by a new edit, so the
    same number can stand for two different placements. The key is built
    from what the pictures are drawn from, not only from that number."""
    from phantom_qa.thumbnails import cache_key
    rec = {"sha256": "a" * 64, "geometry_seq": 3,
           "reg": {"transform": {"A": [[7, 0], [0, -7]], "t": [1400, 1400]}},
           "geometry": {"lowcontrast": {"block": {"center_mm": [-45, -48],
                                                  "angle_deg": -45.0}}}}

    def key(r):
        return cache_key(r, pdef_version="1.0", algo_version="1.0.0")

    base = key(rec)
    assert key(dict(rec)) == base
    moved = {**rec, "geometry": {"lowcontrast": {"block": {
        "center_mm": [-45, -48], "angle_deg": -30.0}}}}
    assert key(moved) != base, "same sequence number, different block"
    refit = {**rec, "reg": {"transform": {"A": [[7, 0], [0, -7]],
                                          "t": [1402, 1400]}}}
    assert key(refit) != base, "same sequence number, new registration"
    assert key({**rec, "geometry_seq": 4}) != base
    assert cache_key(rec, pdef_version="1.1", algo_version="1.0.0") != base


def test_discarding_an_analysis_removes_its_pictures(client):
    aid = _computed(client)
    _report(client, aid)
    directory = client.mod.store.thumbs_dir(aid)
    assert os.listdir(directory)
    r = client.post(f"/api/analyses/{aid}/discard", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert not os.path.exists(directory)


def test_deleting_an_analysis_removes_its_pictures(client):
    """They are cut from the scan: left on disk they would outlive the record
    that explains what they are."""
    aid = _computed(client)
    _report(client, aid)
    directory = client.mod.store.thumbs_dir(aid)
    assert os.listdir(directory)
    r = client.post(f"/api/analyses/{aid}/delete",
                    json={"admin_password": ADMIN_PW, "reason": "regression test"})
    assert r.status_code == 200, r.text
    assert not os.path.exists(directory)


def test_a_hostile_id_never_names_a_directory_to_remove(tmp_path):
    from phantom_qa.store import Store
    store = Store(str(tmp_path))
    for aid in ("..", "../data", "a/b", "", "a\\b", "é"):
        assert store.thumbs_dir(aid) is None, aid


# ---------------------------------------------------------------- failures

def test_a_picture_that_cannot_be_drawn_costs_only_its_cell(client, monkeypatch):
    """And it is not kept, so the next report tries again rather than
    remembering the failure."""
    from phantom_qa import thumbnails
    aid = _computed(client)

    def broken(*_a, **_k):
        raise RuntimeError("simulated rendering failure")

    monkeypatch.setattr(thumbnails, "_wedge", broken)
    cells = _cells(_pictures_section(_report(client, aid).text))
    missing = [c for c in cells if "class='pic-missing'" in c]
    assert len(missing) == 1 and thumbnails.FAILED in missing[0]
    assert sum('<figure class="pic"' in c for c in cells) == \
        len(thumbnails.REGIONS) - 1
    directory = client.mod.store.thumbs_dir(aid)
    assert not os.path.isdir(directory) or not os.listdir(directory)


def test_a_scan_whose_file_has_gone_still_gets_its_report(client):
    """The report is how the numbers are read; a lost source file costs the
    pictures and nothing else."""
    from phantom_qa import thumbnails
    aid = _computed(client)
    client.mod._scans.clear()
    os.remove(client.mod.store.upload_path(aid))
    cells = _cells(_pictures_section(_report(client, aid).text))
    assert all(thumbnails.UNREADABLE in c for c in cells)


def test_anything_unexpected_while_drawing_still_answers_with_a_page(
        client, monkeypatch):
    from phantom_qa import thumbnails
    aid = _computed(client)

    def broken(*_a, **_k):
        raise RuntimeError("simulated failure outside the renderer")

    monkeypatch.setattr(thumbnails, "pictures_for", broken)
    cells = _cells(_pictures_section(_report(client, aid).text))
    assert cells and all(thumbnails.FAILED in c for c in cells)


@pytest.mark.parametrize("damage", ['{"key": "truncated', "[1, 2]"])
def test_a_damaged_kept_file_is_drawn_again_rather_than_served(
        client, monkeypatch, damage):
    """A half-written or corrupted file on disk is a cache miss, not a 500."""
    aid = _computed(client)
    _report(client, aid)
    directory = client.mod.store.thumbs_dir(aid)
    for name in os.listdir(directory):
        with open(os.path.join(directory, name), "w") as f:
            f.write(damage)
    calls = _count_renders(monkeypatch)
    cells = _cells(_pictures_section(_report(client, aid).text))
    assert len(calls) == 1
    assert all('<figure class="pic"' in c for c in cells)


# ------------------------------------------------------------- orientation

def test_the_same_phantom_looks_the_same_however_it_lay():
    """The reason the pictures are sampled through the registration rather
    than cut from the image: a scan taken at 90° or face down must line up
    with one taken square, or the table compares different corners."""
    from phantom_qa import phantom_def, thumbnails
    from phantom_qa.analysis.common import Ctx
    from phantom_qa.registration import Transform

    pdef = phantom_def.load_default()
    size = 1400

    def scene(x_mm, y_mm):
        # a face with two asymmetric marks, so any turn or flip would show
        v = np.full(x_mm.shape, 1000.0)
        v[np.hypot(x_mm - 80, y_mm - 50) < 20] += 800
        v[(np.abs(x_mm + 60) < 30) & (np.abs(y_mm + 90) < 8)] += 400
        v[np.abs(x_mm) > 150] = 200
        v[np.abs(y_mm) > 150] = 200
        return v

    def exposure(A):
        T = Transform(A=np.asarray(A, float), t=np.array([size / 2, size / 2]))
        rows, cols = np.mgrid[0:size, 0:size]
        mm = T.px_to_mm(np.stack([cols.ravel(), rows.ravel()], 1).astype(float))
        img = scene(mm[:, 0], mm[:, 1]).reshape(size, size)
        return Ctx(pixels=img, T=T, pdef=pdef, reg=None, params={})

    square = exposure([[4.0, 0.0], [0.0, -4.0]])
    turned = exposure([[0.0, 4.0], [4.0, 0.0]])        # a quarter turn
    face_down = exposure([[-4.0, 0.0], [0.0, -4.0]])   # mirrored

    def phantom_picture(ctx):
        p = thumbnails.render_all(ctx, {})["phantom"]
        from PIL import Image
        import io
        return np.asarray(Image.open(io.BytesIO(base64.b64decode(p["b64"]))),
                          float)

    ref = phantom_picture(square)
    for name, ctx in (("turned", turned), ("face down", face_down)):
        other = phantom_picture(ctx)
        assert other.shape == ref.shape
        r = np.corrcoef(ref.ravel(), other.ravel())[0, 1]
        assert r > 0.98, f"the {name} exposure does not line up (r={r:.3f})"
