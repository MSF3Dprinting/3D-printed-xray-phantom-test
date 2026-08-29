"""A signed-off analysis is not editable, and bad coordinates are not a 500.

Both were found by probing the Stage C endpoints directly.

Validation records that a named person took responsibility for a set of
numbers. Because a geometry change now correctly drops results computed from
geometry that no longer exists, an ROI nudge on a validated analysis used to
leave the ruling, the approver's name and the date intact with the numbers
gone — and the record then disappeared from every trend and export, all of
which select on completed results. The reanalyze CLI already refuses to touch
signed-off analyses; the web path now does too.
"""

from __future__ import annotations

import hashlib
import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from phantom_qa import pipeline
from phantom_qa.analysis.common import Ctx, rect_roi, segment
from phantom_qa.phantom_def import load_default
from phantom_qa.registration import Transform
from test_authorization import ADMIN_PW, _build_app, _login
from test_store_labels import fake_scan, results_for


def _png(seed: int = 0) -> bytes:
    from PIL import Image
    arr = np.full((64, 64), 100 + (seed % 50), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def make_ctx(n=64):
    rng = np.random.default_rng(5)
    return Ctx(pixels=2000 + rng.normal(0, 20, (n, n)),
               T=Transform(A=np.array([[1.0, 0.0], [0.0, -1.0]]),
                           t=np.array([n / 2.0, n / 2.0])),
               pdef=load_default())


def _geometry(ctx):
    return {"linepairs": {
        "strip_angle_deg": 44.1, "roi_size_mm": 12.6,
        "roi_angle_offset_deg": 45.0,
        "groups": [{"id": "G2.0", "freq_lp_mm": 2.0, "detected": True,
                    "roi": rect_roi(ctx, (5.0, 5.0), (12.6, 12.6), 89.1,
                                    "linepairs/G2.0"),
                    "profile_seg": segment(ctx, (2.0, 2.0), (8.0, 8.0),
                                           "linepairs/G2.0/profile")}]},
        "geometry": {"field_edges": {}, "rulers": {}}}


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    return _build_app(tmp_path, monkeypatch)


@pytest.fixture()
def client(mod):
    # raise_server_exceptions=False so an unhandled 500 shows up as a status
    # code rather than as a raised exception the test would report as an error.
    c = TestClient(mod.app, raise_server_exceptions=False)
    c.headers.update({"X-CSRF-Token": _login(c)})
    return c


@pytest.fixture()
def aid(mod):
    payload = _png()
    ctx = make_ctx()
    a = mod.store.new_analysis(
        fake_scan(sha=hashlib.sha256(payload).hexdigest(), name="scan.png"),
        payload, "sig", "1.0.0", "1.0", labels={"phantom": "MSF-01"})
    mod.store.update(a, reg={"transform": ctx.T.to_dict(),
                             "corners_px": [[0, 0], [1, 0], [1, 1], [0, 1]]})
    mod.store.set_geometry_baseline(a, pipeline.to_jsonable(_geometry(ctx)))
    mod.store.update(a, results=results_for(), status="pass", stage="F")
    return a


@pytest.fixture()
def signed_off(client, mod, aid):
    r = client.post(f"/api/analyses/{aid}/validation",
                    json={"status": "validated", "validated_by": "Dr A",
                          "admin_password": ADMIN_PW})
    assert r.status_code == 200, r.text
    return aid


#: Every route that would change the measurements or the geometry behind them.
MUTATORS = [
    ("/api/analyses/{aid}/roi",
     {"roi_id": "linepairs/G2.0", "center_px": [30.0, 30.0]}),
    ("/api/analyses/{aid}/roi_rotate",
     {"roi_id": "linepairs/G2.0", "angle_deg": 12.0}),
    ("/api/analyses/{aid}/lowcontrast_block", {"angle_deg": -45.0}),
    ("/api/analyses/{aid}/field_edge", {"side": "top", "point_px": [32.0, 2.0]}),
    ("/api/analyses/{aid}/geometry/undo", {}),
    ("/api/analyses/{aid}/geometry/redo", {}),
    ("/api/analyses/{aid}/geometry/reset", {"to": "auto"}),
    ("/api/analyses/{aid}/propose", {}),
    ("/api/analyses/{aid}/register", {}),
    ("/api/analyses/{aid}/compute", {"sid_mm": 1000.0}),
]


@pytest.mark.parametrize("path,body", MUTATORS)
def test_a_signed_off_analysis_refuses_every_edit(client, signed_off, path, body):
    r = client.post(path.format(aid=signed_off), json=body)
    assert r.status_code == 409, (
        f"{path} returned {r.status_code} on a validated analysis")
    detail = r.json()["detail"]
    assert "Dr A" in detail and "Withdraw" in detail, detail


def test_the_refusal_leaves_the_signed_off_record_intact(client, mod, signed_off):
    client.post(f"/api/analyses/{signed_off}/roi",
                json={"roi_id": "linepairs/G2.0", "center_px": [30.0, 30.0]})
    rec = mod.store.get(signed_off)
    assert rec["validation_status"] == "validated"
    assert rec["results"] is not None, "the signed-off numbers were destroyed"
    assert rec["status"] == "pass"
    # ...and it is still in every output that selects on completed results
    assert [a["id"] for a in client.get("/api/trends").json()["analyses"]] == \
        [signed_off]
    assert client.get("/api/export.csv").status_code == 200


def test_withdrawing_the_ruling_makes_it_editable_again(client, mod, signed_off):
    r = client.post(f"/api/analyses/{signed_off}/validation",
                    json={"status": "", "admin_password": ADMIN_PW})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/analyses/{signed_off}/roi",
                    json={"roi_id": "linepairs/G2.0", "center_px": [30.0, 30.0]})
    assert r.status_code == 200, r.text


def test_an_unvalidated_analysis_is_still_freely_editable(client, aid):
    r = client.post(f"/api/analyses/{aid}/roi",
                    json={"roi_id": "linepairs/G2.0", "center_px": [30.0, 30.0]})
    assert r.status_code == 200, r.text


def test_reading_a_signed_off_analysis_is_never_blocked(client, signed_off):
    for path in (f"/api/analyses/{signed_off}",
                 f"/api/analyses/{signed_off}/report.html",
                 f"/api/analyses/{signed_off}/export.csv",
                 f"/api/analyses/{signed_off}/verify",
                 f"/api/analyses/{signed_off}/roi_stats?roi_id=linepairs/G2.0"):
        assert client.get(path).status_code == 200, path


# ------------------------------------------------- malformed input is a 400

@pytest.mark.parametrize("center_px", [[], [1.0], [1.0, 2.0, 3.0]])
def test_a_bad_coordinate_pair_is_rejected_not_crashed(client, aid, center_px):
    """`list[float]` accepted these: [] and [1,2,3] reached numpy and raised a
    500, and [1.0] silently moved the ROI to a coordinate nobody asked for."""
    r = client.post(f"/api/analyses/{aid}/roi",
                    json={"roi_id": "linepairs/G2.0", "center_px": center_px})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("point_px", [[], [1.0], [1.0, 2.0, 3.0]])
def test_a_bad_field_edge_point_is_rejected_not_crashed(client, aid, point_px):
    r = client.post(f"/api/analyses/{aid}/field_edge",
                    json={"side": "top", "point_px": point_px})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_rotation_is_rejected(client, aid, bad):
    """NaN survives JSON, reaches the rotation matrix and poisons every
    coordinate — the ROI simply vanishes from the overlay."""
    r = client.post(f"/api/analyses/{aid}/roi_rotate",
                    content=json.dumps({"roi_id": "linepairs/G2.0",
                                        "angle_deg": bad}),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_non_finite_coordinate_is_rejected(client, aid, bad):
    r = client.post(f"/api/analyses/{aid}/roi",
                    content=json.dumps({"roi_id": "linepairs/G2.0",
                                        "center_px": [bad, 2.0]}),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 422, r.text
