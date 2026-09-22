"""Several operators at once, and datasets of production size.

The deployment is gunicorn with several worker processes sharing one SQLite
database in WAL mode, so "concurrent" here means what it means in the field:
two physicists editing, an administrator deleting, and a coordinator exporting,
all in the same minute. These tests drive the real HTTP endpoints from real
threads and assert the invariants that must hold whatever the interleaving:
no request ever answers 500, no edit is silently lost, and every one-per-scope
rule (baseline, stored layout) still holds afterwards.

The timing assertions are deliberately loose — they are tripwires for a
pathological regression (an accidental O(n²), a lost index), not benchmarks.
"""

from __future__ import annotations

import hashlib
import io
import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from phantom_qa import pipeline
from phantom_qa.analysis.common import Ctx, rect_roi
from phantom_qa.phantom_def import load_default
from phantom_qa.registration import Transform
from test_authorization import ADMIN_PW, _build_app, _login
from test_store_labels import fake_scan, results_for


def _png(seed: int = 0, n: int = 24) -> bytes:
    from PIL import Image
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 255, (n, n), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def make_ctx(n=64):
    rng = np.random.default_rng(5)
    return Ctx(pixels=2000 + rng.normal(0, 20, (n, n)),
               T=Transform(A=np.array([[1.0, 0.0], [0.0, -1.0]]),
                           t=np.array([n / 2.0, n / 2.0])),
               pdef=load_default())


def _many_roi_geometry(ctx, count=8):
    return {"uniformity": {"roi_size_mm": 30.0, "squares": [
        {"id": f"R{i}", "detected": True,
         "roi": rect_roi(ctx, (float(i * 3), 0.0), (10.0, 10.0), 0.0,
                         f"uniformity/R{i}")}
        for i in range(count)]}}


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    return _build_app(tmp_path, monkeypatch)


def _client(mod) -> TestClient:
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    return c


@pytest.fixture()
def client(mod):
    return _client(mod)


def _editable_analysis(mod, phantom="MSF-01", seed=0, rois=8):
    """An analysis whose stored file decodes, so /roi works over HTTP."""
    payload = _png(seed)
    ctx = make_ctx()
    aid = mod.store.new_analysis(
        fake_scan(sha=hashlib.sha256(payload).hexdigest(), name="scan.png"),
        payload, "sig", "1.0.0", "1.0",
        labels={"site": "Goma", "phantom": phantom})
    mod.store.update(aid, reg={"transform": ctx.T.to_dict(),
                               "corners_px": [[0, 0], [1, 0], [1, 1], [0, 1]]})
    mod.store.set_geometry_baseline(
        aid, pipeline.to_jsonable(_many_roi_geometry(ctx, rois)))
    return aid, ctx


def _run_threads(workers):
    """Start every worker on a barrier and surface every exception."""
    gate = threading.Barrier(len(workers))
    errors: list[BaseException] = []
    results: list = [None] * len(workers)

    def wrap(i, fn):
        try:
            gate.wait(timeout=30)
            results[i] = fn()
        except BaseException as exc:       # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=wrap, args=(i, fn))
               for i, fn in enumerate(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
        assert not t.is_alive(), "a worker deadlocked"
    assert not errors, errors
    return results


# ------------------------------------------------------------- multi-operator

def test_eight_operators_upload_at_the_same_moment(mod):
    """Eight distinct files land simultaneously: eight rows, no 500, and the
    write lock queues them rather than corrupting the database."""
    def uploader(i):
        c = _client(mod)
        r = c.post("/api/analyses",
                   files={"file": (f"scan{i}.png", io.BytesIO(_png(i)))},
                   data={"site": "Goma", "phantom": f"MSF-{i:02d}"})
        return r.status_code

    codes = _run_threads([lambda i=i: uploader(i) for i in range(8)])
    assert codes == [200] * 8, codes
    rows = mod.store.list_all()
    assert len(rows) == 8
    assert len({r["id"] for r in rows}) == 8


def test_concurrent_roi_edits_over_http_all_survive(mod):
    """The round-2 lost-update bug, exercised through the full HTTP stack
    rather than the store alone: eight simultaneous drags, eight kept."""
    aid, ctx = _editable_analysis(mod, rois=8)

    def mover(i):
        c = _client(mod)
        target = ctx.T.mm_to_px([float(i * 3), float(10 + i)])
        r = c.post(f"/api/analyses/{aid}/roi",
                   json={"roi_id": f"uniformity/R{i}",
                         "center_px": [float(target[0]), float(target[1])]})
        return r.status_code

    codes = _run_threads([lambda i=i: mover(i) for i in range(8)])
    assert codes == [200] * 8, codes

    geom = mod.store.get(aid)["geometry"]
    for i, sq in enumerate(geom["uniformity"]["squares"]):
        assert sq["roi"]["center_mm"] == pytest.approx(
            [float(i * 3), float(10 + i)], abs=0.01), (
            f"edit to R{i} was lost under concurrency")
    st = mod.store.geometry_state(aid)
    assert st["seq"] == 8 and st["undo_depth"] == 8
    # the audit trail is appended under the same write lock now, so no note
    # about a kept edit may be missing either
    trail = mod.store.get(aid)["audit"] or []
    moved = [e for e in trail if e.get("action") == "roi moved"]
    assert len(moved) == 8, f"only {len(moved)} of 8 audit notes survived"


def test_delete_racing_edits_never_answers_500(mod):
    """An administrator deletes while two colleagues are still editing. Any
    of them may lose the race — none of them may see a server error, and the
    record must be gone at the end."""
    aid, ctx = _editable_analysis(mod, rois=4)

    def deleter():
        c = _client(mod)
        r = c.post(f"/api/analyses/{aid}/delete",
                   json={"admin_password": ADMIN_PW,
                         "reason": "concurrency drill"})
        return ("delete", r.status_code)

    def editor(i):
        c = _client(mod)
        r = c.post(f"/api/analyses/{aid}/roi",
                   json={"roi_id": f"uniformity/R{i}",
                         "center_px": [30.0 + i, 30.0]})
        return ("edit", r.status_code)

    outcomes = _run_threads([deleter,
                             lambda: editor(0), lambda: editor(1),
                             lambda: editor(2)])
    for kind, code in outcomes:
        assert code != 500, f"{kind} answered 500 during the race"
        assert code in (200, 404, 409, 410), (kind, code)
    assert dict(outcomes)["delete"] == 200 or \
        ("delete", 200) in outcomes, "the delete itself must have succeeded"
    assert mod.store.get(aid) is None


def test_simultaneous_baseline_marks_leave_exactly_one(mod):
    """Two operators star two scans of the same phantom at the same instant.
    Whoever wins, the one-reference-per-phantom rule must hold afterwards."""
    ids = []
    for seed in (1, 2):
        payload = _png(seed)
        aid = mod.store.new_analysis(
            fake_scan(sha=hashlib.sha256(payload).hexdigest()), payload,
            "sig", "1.0.0", "1.0", labels={"site": "Goma", "phantom": "MSF-01"})
        mod.store.update(aid, results=results_for(), status="pass")
        ids.append(aid)

    def marker(aid):
        c = _client(mod)
        return c.post(f"/api/analyses/{aid}/baseline",
                      json={"baseline": True}).status_code

    codes = _run_threads([lambda a=a: marker(a) for a in ids])
    assert codes == [200, 200], codes
    marked = [r for r in mod.store.baselines() if r["phantom"] == "MSF-01"]
    assert len(marked) == 1, (
        f"the phantom ended up with {len(marked)} references")


def test_simultaneous_stage_c_confirms_share_one_layout(mod):
    """Two scans of one phantom confirmed at the same moment: the stored
    layout is an upsert inside the write lock, so both succeed and one row
    remains.

    Both operators ticked "use these measuring points for future scans".
    Without that the second to arrive leaves the first one's layout alone,
    and there is no race on the row left to test."""
    a1, _ = _editable_analysis(mod, phantom="MSF-01", seed=1)
    a2, _ = _editable_analysis(mod, phantom="MSF-01", seed=2)

    def confirmer(aid):
        c = _client(mod)
        r = c.post(f"/api/analyses/{aid}/confirm",
                   json={"stage": "C", "save_profile": True})
        return r.status_code, r.json().get("profile_saved")

    out = _run_threads([lambda a=a1: confirmer(a), lambda a=a2: confirmer(a)])
    assert all(code == 200 and saved for code, saved in out), out
    profiles = mod.store.list_phantom_profiles()
    assert [p["phantom_key"] for p in profiles] == ["MSF-01"]


def test_readers_and_writers_mix_without_errors(mod):
    """Exports and listings keep answering while edits are in flight — the
    point of WAL mode, verified end to end."""
    aid, ctx = _editable_analysis(mod, rois=6)
    for seed in range(3):
        payload = _png(100 + seed)
        other = mod.store.new_analysis(
            fake_scan(sha=hashlib.sha256(payload).hexdigest()), payload,
            "sig", "1.0.0", "1.0", labels={"site": "Goma", "phantom": "P"})
        mod.store.update(other, results=results_for(), status="pass")

    def reader(path):
        c = _client(mod)
        return c.get(path).status_code

    def writer(i):
        c = _client(mod)
        return c.post(f"/api/analyses/{aid}/roi",
                      json={"roi_id": f"uniformity/R{i}",
                            "center_px": [20.0 + i, 20.0]}).status_code

    codes = _run_threads([
        lambda: reader("/api/analyses"),
        lambda: reader("/api/trends"),
        lambda: reader("/api/export.csv"),
        lambda: reader("/api/labels"),
        lambda: writer(0), lambda: writer(1), lambda: writer(2),
    ])
    assert all(c == 200 for c in codes), codes


def test_the_admin_throttle_is_shared_across_concurrent_workers(mod):
    """Wrong admin passwords fired in parallel all land in the shared SQLite
    throttle: after the limit, further attempts answer 429, not another 401
    guess opportunity per thread."""
    aid, _ = _editable_analysis(mod)

    def bad_delete():
        c = _client(mod)
        return c.post(f"/api/analyses/{aid}/delete",
                      json={"admin_password": "wrong",
                            "reason": "throttle drill"}).status_code

    codes = _run_threads([bad_delete] * 6)
    assert set(codes) <= {401, 429}, codes
    # the lockout is now in force for everyone
    c = _client(mod)
    r = c.post(f"/api/analyses/{aid}/delete",
               json={"admin_password": "wrong", "reason": "throttle drill"})
    assert r.status_code == 429
    assert mod.store.get(aid) is not None


# ------------------------------------------------------------- at scale

@pytest.fixture()
def big_store(mod):
    """300 completed analyses across 3 sites and 6 phantoms, with results
    padded to a realistic stored size."""
    padding = "x" * 60_000          # a real results blob is ~130 kB
    n = 300
    with mod.store.write_transaction() as c:
        import json as _json
        for i in range(n):
            res = results_for(sd=100.0 + i)
            res["_padding"] = padding
            c.execute(
                "INSERT INTO analyses (id, created_at, acquired_at,"
                " source_name, sha256, kind, reduced_precision, signature,"
                " meta_json, stage, audit_json, algo_version, pdef_version,"
                " status, is_baseline, site, phantom, operator, notes,"
                " results_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?)",
                (f"scale{i:04d}", f"2026-06-{(i % 28) + 1:02d} 10:00:00",
                 f"2026-05-{(i % 28) + 1:02d} 09:00:00", f"scan{i}.dcm",
                 f"{i:064d}"[:64], "dicom", 0, f"sig-{i % 2}",
                 "{}", "F", "[]", "1.0.0", "1.0", "pass",
                 f"Site-{i % 3}", f"MSF-{i % 6:02d}", "op", "",
                 _json.dumps(res)))
    return n


def test_listing_trends_and_exports_stay_correct_at_scale(client, big_store):
    t0 = time.perf_counter()
    listing = client.get("/api/analyses").json()["analyses"]
    t_list = time.perf_counter() - t0
    assert len(listing) == big_store

    t0 = time.perf_counter()
    trends = client.get("/api/trends").json()["analyses"]
    t_trends = time.perf_counter() - t0
    assert len(trends) == big_store

    t0 = time.perf_counter()
    csv_long = client.get("/api/export.csv").text
    t_csv = time.perf_counter() - t0
    # one header + at least one metric row per analysis
    assert len(csv_long.splitlines()) > big_store

    t0 = time.perf_counter()
    wide = client.get("/api/export.csv?layout=wide").text
    t_wide = time.perf_counter() - t0
    assert len(wide.splitlines()[0].split(",")) == 4 + big_store

    print(f"\n300 analyses: list={t_list:.2f}s trends={t_trends:.2f}s "
          f"csv={t_csv:.2f}s wide={t_wide:.2f}s")
    # Tripwires, not benchmarks: with the batched slim fetch these run well
    # under a second on this machine; a return of the per-row get() would put
    # trends back near 8 s and an O(n^2) regression far past these.
    assert t_list < 10, f"listing took {t_list:.1f}s for 300 rows"
    assert t_trends < 15, f"trends took {t_trends:.1f}s for 300 rows"
    assert t_csv < 15 and t_wide < 15


def test_filters_narrow_correctly_at_scale(client, big_store):
    one_site = client.get("/api/analyses?site=Site-1").json()["analyses"]
    assert len(one_site) == big_store // 3
    assert all(a["site"] == "Site-1" for a in one_site)
    one_phantom = client.get("/api/analyses?phantom=MSF-03").json()["analyses"]
    assert len(one_phantom) == big_store // 6


def test_a_150_scan_trend_series_for_one_phantom(client, mod):
    """The data behind the reworked trend chart, at the size the chart's
    label-thinning was designed for: one phantom, 150 completed scans across
    five months. The series must arrive complete, in date order, and with the
    fields the chart reads (value, both dates, the trust flag, the reference
    mark) on every point."""
    import json as _json
    with mod.store.write_transaction() as c:
        for i in range(150):
            month, day = 3 + i // 30, (i % 28) + 1
            c.execute(
                "INSERT INTO analyses (id, created_at, acquired_at,"
                " source_name, sha256, kind, reduced_precision, signature,"
                " meta_json, stage, audit_json, algo_version, pdef_version,"
                " status, is_baseline, site, phantom, operator, notes,"
                " results_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"trend{i:04d}", f"2026-0{month}-{day:02d} 11:00:00",
                 f"2026-0{month}-{day:02d} 09:30:00", f"scan{i}.dcm",
                 f"{i:064d}"[:64], "dicom", 0, "sig-A", "{}", "F", "[]",
                 "1.0.0", "1.0", "pass", 1 if i == 0 else 0,
                 "Goma", "MSF-01", "op", "",
                 _json.dumps(results_for(sd=100.0 + i))))

    r = client.get("/api/trends?phantom=MSF-01")
    assert r.status_code == 200
    series = r.json()["analyses"]
    assert len(series) == 150

    stamps = [a["acquired_at"] for a in series]
    assert stamps == sorted(stamps), "the series must arrive in date order"
    assert sum(1 for a in series if a["is_baseline"]) == 1
    for a in series[:5] + series[-5:]:
        assert a["acquired_at"] and a["created_at"]
        assert a["acquired_flag"] == ""
        sd = next(row["value"] for row in a["rows"]
                  if row["test"] == "linepairs" and row["metric"] == "sd")
        assert isinstance(sd, (int, float))

    # a second phantom's scans must not leak into this series
    other = client.get("/api/trends?phantom=MSF-99").json()["analyses"]
    assert other == []


def test_a_large_image_uploads_and_renders(mod):
    """A full-size detector image (3000x3000) travels the whole path: upload,
    storage, thumbnail rendering. Registration may legitimately fail on a
    synthetic gradient — what must not happen is an error status."""
    from PIL import Image
    xx, yy = np.meshgrid(np.arange(3000, dtype=np.uint16),
                         np.arange(3000, dtype=np.uint16))
    arr = ((xx + yy) % 256).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    payload = buf.getvalue()

    c = _client(mod)
    t0 = time.perf_counter()
    r = c.post("/api/analyses",
               files={"file": ("big.png", io.BytesIO(payload))},
               data={"site": "Goma", "phantom": "MSF-01"})
    t_up = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    aid = r.json()["analyses"][0]["id"]

    t0 = time.perf_counter()
    img = c.get(f"/api/analyses/{aid}/image.png")
    t_img = time.perf_counter() - t0
    assert img.status_code == 200
    assert img.content[:8] == b"\x89PNG\r\n\x1a\n"
    # the second request comes from the cache
    assert c.get(f"/api/analyses/{aid}/image.png").status_code == 200
    print(f"\n3000x3000 upload={t_up:.1f}s first-render={t_img:.1f}s")
    assert t_up < 120 and t_img < 60
