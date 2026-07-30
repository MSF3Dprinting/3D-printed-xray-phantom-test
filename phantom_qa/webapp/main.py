"""FastAPI backend for the phantom QA wizard.

Run with:  python run_app.py   (or: uvicorn phantom_qa.webapp.main:app)
"""

from __future__ import annotations

import io
import os

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import ALGO_VERSION
from .. import ingest, pipeline
from ..analysis.common import roi_center_from_px
from ..phantom_def import load_default
from ..registration import Registration, Transform
from ..report import build_report
from ..store import Store, csv_export, flatten_results

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
app = FastAPI(title="MSF Phantom QA")
store = Store(ROOT)
pdef = load_default()

_scans: dict[str, ingest.ScanData] = {}       # id -> ScanData cache
_regs: dict[str, Registration] = {}
_img_cache: dict[tuple, bytes] = {}


def _scan(aid: str) -> ingest.ScanData:
    if aid in _scans:
        return _scans[aid]
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "analysis not found")
    with open(store.upload_path(aid), "rb") as f:
        data = f.read()
    scans = ingest.load_any_bytes(data, rec["source_name"])
    match = next((s for s in scans if s.sha256 == rec["sha256"]), scans[0])
    _scans[aid] = match
    return match


def _reg(aid: str) -> Registration:
    if aid in _regs:
        return _regs[aid]
    rec = store.get(aid)
    if rec is None or not rec.get("reg"):
        raise HTTPException(400, "not registered yet")
    r = rec["reg"]
    reg = Registration(
        transform=Transform.from_dict(r["transform"]),
        corners_px=np.asarray(r["corners_px"], float),
        coarse_angle_deg=r.get("coarse_angle_deg", float("nan")),
        score=r.get("score", {}),
        candidate_scores=r.get("candidate_scores", []),
        landmarks=r.get("landmarks", {}),
        residual_rms_mm=r.get("residual_rms_mm", float("nan")),
    )
    _regs[aid] = reg
    return reg


def _ctx(aid: str):
    scan = _scan(aid)
    rec = store.get(aid)
    return pipeline.build_ctx(scan, pdef, _reg(aid),
                              {"scan_meta": scan.meta,
                               "sid_mm": rec.get("sid_mm") or 1000.0})


def _reg_payload(aid: str, reg: Registration) -> dict:
    scan = _scan(aid)
    return pipeline.to_jsonable({
        "summary": reg.summary(),
        "transform": reg.transform.to_dict(),
        "landmarks": reg.landmarks,
        "candidates": reg.candidate_scores[:4],
        "image": {"rows": scan.shape[0], "cols": scan.shape[1]},
        "reduced_precision": scan.reduced_precision,
        "meta": scan.meta,
    })


def _do_register(aid: str, corners_hint=None):
    scan = _scan(aid)
    reg = pipeline.run_stage_a(scan, pdef, corners_hint=corners_hint)
    _regs[aid] = reg
    store.update(aid, reg=pipeline.to_jsonable({
        "transform": reg.transform.to_dict(),
        "corners_px": reg.corners_px,
        "coarse_angle_deg": reg.coarse_angle_deg,
        "score": reg.score,
        "candidate_scores": reg.candidate_scores,
        "landmarks": reg.landmarks,
        "residual_rms_mm": reg.residual_rms_mm,
    }))
    return reg


# ------------------------------------------------------------------ endpoints

@app.post("/api/analyses")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    try:
        scans = ingest.load_any_bytes(data, file.filename or "upload")
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")
    created = []
    for scan in scans:
        aid = store.new_analysis(scan, data, ingest.protocol_signature(scan.meta),
                                 ALGO_VERSION, pdef.version)
        _scans[aid] = scan
        store.audit(aid, "A", "uploaded",
                    {"source": scan.source_name, "kind": scan.kind})
        try:
            reg = _do_register(aid)
            created.append({"id": aid, "source_name": scan.source_name,
                            "registered": True,
                            "registration": _reg_payload(aid, reg)})
        except Exception as e:
            created.append({"id": aid, "source_name": scan.source_name,
                            "registered": False, "error": str(e)})
    return {"analyses": created}


@app.get("/api/analyses")
def list_analyses():
    return {"analyses": store.list_all()}


@app.get("/api/analyses/{aid}")
def get_analysis(aid: str):
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    payload = {k: rec[k] for k in ("id", "created_at", "source_name", "sha256",
                                   "kind", "reduced_precision", "signature",
                                   "stage", "status", "sid_mm", "is_baseline",
                                   "algo_version", "pdef_version")}
    payload["meta"] = rec.get("meta")
    payload["geometry"] = rec.get("geometry")
    payload["results"] = rec.get("results")
    payload["audit"] = rec.get("audit")
    try:
        payload["registration"] = _reg_payload(aid, _reg(aid))
    except HTTPException:
        payload["registration"] = None
    return pipeline.to_jsonable(payload)


@app.get("/api/analyses/{aid}/image.png")
def image_png(aid: str, wc: float | None = None, ww: float | None = None,
              scale: int = 1600):
    key = (aid, wc, ww, scale)
    if key not in _img_cache:
        from PIL import Image
        img = _scan(aid).pixels
        if wc is None or ww is None:
            lo, hi = np.percentile(img, [1, 99])
        else:
            lo, hi = wc - ww / 2, wc + ww / 2
        a = np.clip((img - lo) / max(hi - lo, 1e-9), 0, 1)
        pil = Image.fromarray((a * 255).astype(np.uint8))
        if max(pil.size) > scale:
            pil.thumbnail((scale, scale), Image.LANCZOS)
        buf = io.BytesIO()
        pil.save(buf, format="png")
        if len(_img_cache) > 24:
            _img_cache.clear()
        _img_cache[key] = buf.getvalue()
    return Response(_img_cache[key], media_type="image/png")


class CornersBody(BaseModel):
    corners_px: list[list[float]] | None = None


@app.post("/api/analyses/{aid}/register")
def re_register(aid: str, body: CornersBody):
    hint = np.asarray(body.corners_px, float) if body.corners_px else None
    try:
        reg = _do_register(aid, corners_hint=hint)
    except Exception as e:
        raise HTTPException(400, f"registration failed: {e}")
    store.audit(aid, "A", "re-registered",
                {"manual_corners": body.corners_px is not None})
    # geometry proposals depend on the transform -> invalidate
    store.update(aid, geometry=None, results=None, stage="A")
    return _reg_payload(aid, reg)


@app.post("/api/analyses/{aid}/propose")
def propose(aid: str):
    ctx = _ctx(aid)
    geom = pipeline.propose_all(ctx)
    store.update(aid, geometry=geom, stage="B")
    store.audit(aid, "B", "proposals generated")
    return {"geometry": geom}


class RoiMove(BaseModel):
    roi_id: str
    center_px: list[float]


def _walk_find(node, roi_id):
    if isinstance(node, dict):
        if node.get("id") == roi_id and node.get("type") in ("rect", "circle"):
            return node
        for v in node.values():
            r = _walk_find(v, roi_id)
            if r is not None:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _walk_find(v, roi_id)
            if r is not None:
                return r
    return None


@app.post("/api/analyses/{aid}/roi")
def move_roi(aid: str, body: RoiMove):
    rec = store.get(aid)
    if not rec or not rec.get("geometry"):
        raise HTTPException(400, "no geometry yet")
    ctx = _ctx(aid)
    geom = rec["geometry"]
    node = _walk_find(geom, body.roi_id)
    if node is None:
        raise HTTPException(404, f"ROI {body.roi_id} not found")
    old_center = list(node.get("center_mm", []))
    updated = roi_center_from_px(ctx, node, body.center_px)
    node.clear()
    node.update(pipeline.to_jsonable(updated))
    node["manually_adjusted"] = True
    node["auto_center_mm"] = node.get("auto_center_mm", old_center)
    from ..analysis.common import stats_for_roi
    stats = stats_for_roi(ctx, node)
    store.update(aid, geometry=geom)
    store.audit(aid, "C", "roi moved",
                {"roi": body.roi_id, "from_mm": old_center,
                 "to_mm": node["center_mm"]})
    return pipeline.to_jsonable({"roi": node, "stats": stats})


@app.get("/api/analyses/{aid}/roi_stats")
def roi_stats(aid: str, roi_id: str):
    rec = store.get(aid)
    if not rec or not rec.get("geometry"):
        raise HTTPException(400, "no geometry yet")
    node = _walk_find(rec["geometry"], roi_id)
    if node is None:
        raise HTTPException(404, f"ROI {roi_id} not found")
    from ..analysis.common import stats_for_roi
    ctx = _ctx(aid)
    return pipeline.to_jsonable({"roi": node, "stats": stats_for_roi(ctx, node)})


class PreviewBody(BaseModel):
    tests: list[str] = ["geometry"]
    sid_mm: float = 1000.0


@app.post("/api/analyses/{aid}/compute_preview")
def compute_preview(aid: str, body: PreviewBody):
    """Run a subset of tests on the current geometry WITHOUT storing results.
    Used by wizard Stage D (dimension verification)."""
    rec = store.get(aid)
    if not rec or not rec.get("geometry"):
        raise HTTPException(400, "no geometry yet")
    store.update(aid, sid_mm=body.sid_mm)
    ctx = _ctx(aid)
    out = {}
    for name in body.tests:
        if name not in pipeline.TESTS:
            continue
        geom = rec["geometry"].get(name)
        if not geom or geom.get("_error"):
            out[name] = {"status": "n/a"}
            continue
        try:
            out[name] = pipeline._MODULES[name].compute(ctx, geom)
        except Exception as e:
            out[name] = {"status": "error", "error": str(e)}
    return pipeline.to_jsonable(out)


class FieldEdgeSet(BaseModel):
    side: str
    point_px: list[float]


@app.post("/api/analyses/{aid}/field_edge")
def set_field_edge(aid: str, body: FieldEdgeSet):
    rec = store.get(aid)
    if not rec or not rec.get("geometry"):
        raise HTTPException(400, "no geometry yet")
    ctx = _ctx(aid)
    geom = rec["geometry"]
    side_geom = {"top": ((0, 1), (0, -1)), "right": ((1, 0), (-1, 0)),
                 "bottom": ((0, -1), (0, 1)), "left": ((-1, 0), (1, 0))}
    if body.side not in side_geom:
        raise HTTPException(400, "bad side")
    (ex, ey), (nx, ny) = side_geom[body.side]
    S2 = pdef.side_mm / 2.0
    edge_pt = np.array([ex * S2, ey * S2], float)
    outward = -np.array([nx, ny], float)
    p_mm = np.asarray(ctx.T.px_to_mm(body.point_px), float)
    offset = float(np.dot(p_mm - edge_pt, outward))
    geom["geometry"]["field_edges"][body.side] = pipeline.to_jsonable({
        "side": body.side, "detected": True, "manual": True,
        "offset_from_edge_mm": offset,
        "edge_pt_px": np.asarray(
            ctx.T.mm_to_px(edge_pt + outward * offset)).tolist(),
    })
    store.update(aid, geometry=geom)
    store.audit(aid, "C", "field edge set manually",
                {"side": body.side, "offset_mm": offset})
    return geom["geometry"]["field_edges"][body.side]


class StageConfirm(BaseModel):
    stage: str
    note: str | None = None


@app.post("/api/analyses/{aid}/confirm")
def confirm_stage(aid: str, body: StageConfirm):
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    order = ["A", "B", "C", "D", "E", "F"]
    if body.stage not in order:
        raise HTTPException(400, "bad stage")
    nxt = order[min(order.index(body.stage) + 1, len(order) - 1)]
    store.update(aid, stage=nxt)
    store.audit(aid, body.stage, "confirmed", {"note": body.note})
    return {"stage": nxt}


class ComputeBody(BaseModel):
    sid_mm: float = 1000.0


@app.post("/api/analyses/{aid}/compute")
def compute(aid: str, body: ComputeBody):
    rec = store.get(aid)
    if not rec or not rec.get("geometry"):
        raise HTTPException(400, "no confirmed geometry")
    store.update(aid, sid_mm=body.sid_mm)
    ctx = _ctx(aid)
    results = pipeline.compute_all(ctx, rec["geometry"])
    status = pipeline.overall_status(results)
    store.update(aid, results=results, status=status, stage="F")
    store.audit(aid, "E", "computed", {"overall": status,
                                       "sid_mm": body.sid_mm})
    baseline = store.baseline_for(rec["signature"], exclude_id=aid)
    return pipeline.to_jsonable({
        "results": results, "overall": status,
        "baseline": ({"id": baseline["id"],
                      "rows": flatten_results(baseline["results"])}
                     if baseline and baseline.get("results") else None),
    })


class FinalizeBody(BaseModel):
    baseline: bool = False


@app.post("/api/analyses/{aid}/finalize")
def finalize(aid: str, body: FinalizeBody):
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    store.update(aid, stage="F", status=rec.get("status") or "complete")
    if body.baseline:
        if rec.get("reduced_precision"):
            raise HTTPException(400, "reduced-precision analyses cannot be baselines")
        store.set_baseline(aid, True)
    store.audit(aid, "F", "finalized", {"baseline": body.baseline})
    return {"ok": True}


@app.delete("/api/analyses/{aid}")
def delete_analysis(aid: str):
    store.delete(aid)
    _scans.pop(aid, None)
    _regs.pop(aid, None)
    return {"ok": True}


@app.get("/api/analyses/{aid}/export.json")
def export_json(aid: str):
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    return pipeline.to_jsonable(rec)


@app.get("/api/analyses/{aid}/export.csv", response_class=PlainTextResponse)
def export_csv_one(aid: str):
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    return csv_export([rec])


@app.get("/api/export.csv", response_class=PlainTextResponse)
def export_csv_many(ids: str):
    recs = [store.get(a) for a in ids.split(",") if store.get(a)]
    return csv_export(recs)


@app.get("/api/analyses/{aid}/report.html", response_class=HTMLResponse)
def report_html(aid: str):
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    overlay = None
    if rec.get("geometry"):
        try:
            ctx = _ctx(aid)
            overlay = pipeline.render_overlay(_scan(aid), ctx, rec["geometry"])
        except Exception:
            overlay = None
    baseline = store.baseline_for(rec["signature"], exclude_id=aid)
    return build_report(rec, overlay_png=overlay, baseline=baseline)


@app.get("/api/trends")
def trends(signature: str):
    out = []
    for item in store.list_all():
        if item["signature"] != signature:
            continue
        rec = store.get(item["id"])
        if not rec or not rec.get("results"):
            continue
        out.append({"id": rec["id"], "created_at": rec["created_at"],
                    "is_baseline": rec["is_baseline"],
                    "rows": flatten_results(rec["results"])})
    return pipeline.to_jsonable({"signature": signature, "analyses": out})


@app.get("/api/signatures")
def signatures():
    sigs = {}
    for item in store.list_all():
        sigs.setdefault(item["signature"], 0)
        sigs[item["signature"]] += 1
    return {"signatures": [{"signature": k, "count": v} for k, v in sigs.items()]}


app.mount("/", StaticFiles(
    directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"),
    html=True), name="static")
