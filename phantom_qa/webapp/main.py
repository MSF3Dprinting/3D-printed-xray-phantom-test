"""FastAPI backend for the phantom QA wizard.

Run with:  python run_app.py   (or: uvicorn phantom_qa.webapp.main:app)
"""

from __future__ import annotations

import html as _html
import io
import logging
import math
import os
import time

html_escape = _html.escape

import numpy as np
from fastapi import (FastAPI, File, Form, HTTPException, Request, Response,
                     UploadFile)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, conlist, field_validator

from .. import ALGO_VERSION
from .. import ingest, pipeline
from ..analysis import linepairs
from ..analysis.common import roi_center_from_px
from ..comparison_report import build_comparison_report
from ..config import get_config
from ..logging_setup import audit, get_logger, setup_logging
from ..phantom_def import load_default
from ..registration import Registration, Transform
from ..report import build_report
from ..security import (CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, SharedThrottle,
                        csrf_ok, issue_session, new_csrf_token, read_session,
                        verify_password)
from ..store import (VALIDATION_LABELS, VALIDATION_STATES, Store, csv_export,
                     flatten_results, wide_csv_export)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
cfg = get_config()

log_dir = cfg.log_dir if os.path.isabs(cfg.log_dir) \
    else os.path.join(ROOT, cfg.log_dir)
setup_logging(log_dir, level=cfg.log_level, max_mb=cfg.log_max_mb,
              backups=cfg.log_backups, audit_backups=cfg.log_audit_backups,
              console=cfg.log_console)
log = get_logger("web")
log.info("starting: %s", cfg.summary())
if not cfg.deletion_enabled:
    log.warning("deletion is DISABLED (no PHANTOMQA_ADMIN_PASSWORD_HASH set)")

app = FastAPI(title="MSF Phantom QA", docs_url=None, redoc_url=None,
              openapi_url=None, root_path=cfg.root_path)
store = Store(ROOT)
pdef = load_default()

# Shared across gunicorn workers — an in-process counter would give an attacker
# max_attempts x worker_count guesses.
throttle = SharedThrottle(store.db_path, "login", cfg.max_login_attempts,
                          cfg.lockout_minutes)
admin_throttle = SharedThrottle(store.db_path, "admin",
                                max(cfg.max_login_attempts // 2, 3),
                                cfg.lockout_minutes)

_scans: dict[str, ingest.ScanData] = {}       # id -> ScanData cache
_regs: dict[str, Registration] = {}
_img_cache: dict[tuple, bytes] = {}


# ------------------------------------------------------------- security layer

# Everything else requires a session. Kept as an explicit allow-list so a new
# route is private by default — adding an endpoint can never accidentally
# publish it.
_PUBLIC_PATHS = frozenset({"/login", "/api/login", "/api/auth", "/style.css",
                           "/login.js", "/favicon.ico"})


def _app_path(request: Request) -> str:
    """Path relative to the mount point, normalised.

    Behind nginx at /x-ray/ the incoming path may or may not carry the prefix
    depending on how proxy_pass is written, so strip it if present. Duplicate
    slashes are collapsed and a trailing slash removed so that '/api/login/'
    or '//api/login' cannot dodge the allow-list comparison."""
    p = request.url.path
    rp = (request.scope.get("root_path") or "")
    if rp and p.startswith(rp):
        p = p[len(rp):] or "/"
    while "//" in p:
        p = p.replace("//", "/")
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/") or "/"
    return p


def _client_key(request: Request) -> str:
    """The client address used for throttling and the audit log.

    Deliberately does NOT read X-Forwarded-For itself. The ASGI server already
    does that — uvicorn's ProxyHeadersMiddleware (the equivalent of WSGI's
    ProxyFix) rewrites the client address from the header, but ONLY when the
    immediate peer is listed in `forwarded_allow_ips`. Parsing the header here
    as well would throw that trust boundary away: anything able to reach
    gunicorn directly on the loopback — another app on the same VM, a local
    user, an SSRF in a colocated service — could then forge an address per
    request and get unlimited password guesses.

    So: trust the server's answer, and make sure the server is configured with
    the right `forwarded_allow_ips` (see gunicorn.conf.py)."""
    return request.client.host if request.client else "unknown"


def _current_user(request: Request) -> str:
    if not cfg.auth_enabled:
        return "anonymous"
    s = read_session(cfg.secret_key, request.cookies.get(SESSION_COOKIE))
    return (s or {}).get("u", "-")


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # 1. Host allow-list (defends against Host-header poisoning)
    if cfg.allowed_hosts:
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if host not in cfg.allowed_hosts:
            return JSONResponse({"detail": "Host not allowed"}, status_code=400)

    # 2. Body-size cap (uploads are large but not unbounded)
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > cfg.max_upload_mb * 1024 * 1024:
        return JSONResponse(
            {"detail": f"Upload exceeds {cfg.max_upload_mb} MB"}, status_code=413)

    path = _app_path(request)
    if cfg.auth_enabled and path not in _PUBLIC_PATHS:
        session = read_session(cfg.secret_key,
                               request.cookies.get(SESSION_COOKIE))
        if session is None:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Authentication required"},
                                    status_code=401)
            return HTMLResponse(_login_page(), status_code=401)
        # 3. CSRF: state-changing requests must echo the cookie in a header
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if not csrf_ok(request.cookies.get(CSRF_COOKIE),
                           request.headers.get(CSRF_HEADER)):
                return JSONResponse({"detail": "CSRF token missing or invalid"},
                                    status_code=403)

    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error %s %s client=%s user=%s",
                      request.method, path, _client_key(request),
                      _current_user(request))
        raise
    dt_ms = (time.perf_counter() - t0) * 1000.0
    if not path.startswith(("/style.css", "/app.js", "/login.js", "/favicon")):
        lvl = logging.WARNING if response.status_code >= 400 else logging.INFO
        log.log(lvl, "%s %s -> %s in %.0f ms client=%s user=%s",
                request.method, path, response.status_code, dt_ms,
                _client_key(request), _current_user(request))

    # 4. Response hardening headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = \
        "geolocation=(), microphone=(), camera=()"
    # Reports embed their charts as data: URIs; nothing is loaded cross-origin.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'")
    if cfg.https_only:
        response.headers["Strict-Transport-Security"] = \
            "max-age=31536000; includeSubDomains"
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    """Return a clean 422 instead of echoing the rejected input back.

    FastAPI's default handler puts the offending value in the response. Two
    problems with that: a body containing NaN or Infinity (legal to Python's
    JSON parser, not to its encoder) makes serialising the error itself fail,
    turning a 422 into a 500; and reflecting arbitrary client input into a
    response is a habit worth not having. Only the field location and the
    message go back."""
    detail = [{"loc": [str(p) for p in e.get("loc", [])],
               "msg": str(e.get("msg", "invalid value")),
               "type": str(e.get("type", ""))}
              for e in exc.errors()]
    log.info("422 %s %s: %s", request.method, _app_path(request), detail)
    return JSONResponse(status_code=422, content={"detail": detail})


def _set_auth_cookies(response, username: str, csrf: str) -> None:
    token = issue_session(cfg.secret_key, username, cfg.session_hours)
    common = dict(secure=cfg.https_only, samesite="strict",
                  max_age=cfg.session_hours * 3600, path=cfg.cookie_path)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **common)
    # readable by JS on purpose: the frontend echoes it in the CSRF header
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **common)


def _base_href() -> str:
    """URL prefix the browser must use. Everything the frontend requests is
    relative to this, so the app works at / and at /x-ray/ unchanged."""
    return (cfg.root_path + "/") if cfg.root_path else "/"


def _login_page() -> str:
    base = html_escape(_base_href())
    return f"""<!doctype html><html><head><meta charset="utf-8">
<base href="{base}">
<title>MSF Phantom QA — sign in</title><link rel="stylesheet" href="style.css">
</head><body class="login-body">
<form id="login-form" class="login-card">
  <h1>MSF Phantom QA</h1>
  <label>User <input name="username" autocomplete="username" required></label>
  <label>Password <input name="password" type="password"
         autocomplete="current-password" required></label>
  <button class="primary" type="submit">Sign in</button>
  <p id="login-error" class="login-error"></p>
</form>
<script src="login.js"></script></body></html>"""


class LoginBody(BaseModel):
    username: str
    password: str


@app.get("/api/auth")
def auth_state(request: Request):
    if not cfg.auth_enabled:
        return {"auth_enabled": False, "authenticated": True,
                "csrf": request.cookies.get(CSRF_COOKIE)}
    s = read_session(cfg.secret_key, request.cookies.get(SESSION_COOKIE))
    return {"auth_enabled": True, "authenticated": s is not None,
            "user": (s or {}).get("u"),
            "csrf": request.cookies.get(CSRF_COOKIE)}


@app.post("/api/login")
def login(body: LoginBody, request: Request):
    csrf = new_csrf_token()
    if not cfg.auth_enabled:
        resp = JSONResponse({"ok": True, "auth_enabled": False, "csrf": csrf})
        _set_auth_cookies(resp, "anonymous", csrf)
        return resp
    key = _client_key(request)
    wait = throttle.locked_for(key)
    if wait > 0:
        raise HTTPException(429, f"Too many failed attempts. "
                                 f"Try again in {wait // 60 + 1} min.")
    ok_user = secrets_equal(body.username, cfg.username)
    if cfg.password_hash:
        ok_pass = verify_password(body.password, cfg.password_hash)
    else:
        ok_pass = secrets_equal(body.password, cfg.password_plain)
    # both checks always run, then combine — no early return that would leak
    # which of the two was wrong via response timing
    if not (ok_user and ok_pass):
        throttle.record_failure(key)
        audit("login", user=body.username, client=key, outcome="denied")
        log.warning("failed sign-in for %r from %s", body.username, key)
        raise HTTPException(401, "Invalid credentials")
    throttle.reset(key)
    audit("login", user=body.username, client=key, outcome="ok")
    resp = JSONResponse({"ok": True, "csrf": csrf})
    _set_auth_cookies(resp, body.username, csrf)
    return resp


@app.post("/api/logout")
def logout(request: Request):
    audit("logout", user=_current_user(request), client=_client_key(request))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path=cfg.cookie_path)
    resp.delete_cookie(CSRF_COOKIE, path=cfg.cookie_path)
    return resp


def secrets_equal(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


def _scan(aid: str) -> ingest.ScanData:
    if aid in _scans:
        return _scans[aid]
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "analysis not found")
    path = store.upload_path(aid)
    if not os.path.exists(path):
        log.error("stored source file missing for analysis=%s", aid)
        raise HTTPException(
            410, "The stored source file for this analysis is missing, so the "
                 "image can no longer be loaded. Run 'verify' for details.")
    with open(path, "rb") as f:
        data = f.read()
    try:
        scans = ingest.load_any_bytes(data, rec["source_name"])
    except Exception as e:
        # A stored file that no longer decodes is a data problem, not a bug —
        # answer with a clear status instead of an unhandled 500.
        log.error("stored file for analysis=%s could not be decoded: %s", aid, e)
        raise HTTPException(
            422, "The stored source file could not be decoded as an image. "
                 "It may be corrupted — run 'verify' to check its SHA-256.")
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


def _display_range(aid: str) -> dict:
    """Value range the viewer should map to black..white.

    Detectors differ in bit depth — 12-bit on one unit, 14-bit on another — so
    a fixed 0..4095 slider range whites out a 14-bit image entirely. The
    window/level controls work relative to this measured range instead."""
    img = _scan(aid).pixels
    lo, hi = (float(x) for x in np.percentile(img, [0.5, 99.5]))
    if hi - lo < 1e-6:
        lo, hi = float(img.min()), float(img.max()) or 1.0
    return {"lo": lo, "hi": hi,
            "min": float(img.min()), "max": float(img.max())}


def _reg_payload(aid: str, reg: Registration) -> dict:
    scan = _scan(aid)
    return pipeline.to_jsonable({
        "summary": reg.summary(),
        "transform": reg.transform.to_dict(),
        "display_range": _display_range(aid),
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
async def upload(request: Request, file: UploadFile = File(...),
                 site: str = Form(""), phantom: str = Form(""),
                 operator: str = Form(""), notes: str = Form(""),
                 allow_duplicate: bool = Form(False)):
    user, client = _current_user(request), _client_key(request)
    data = await file.read()
    try:
        scans = ingest.load_any_bytes(data, file.filename or "upload")
    except Exception as e:
        log.warning("upload rejected (%s) name=%r user=%s",
                    e, file.filename, user)
        audit("upload", user=user, client=client, outcome="rejected",
              filename=file.filename, error=str(e))
        raise HTTPException(400, f"Could not read file: {e}")
    labels = {"site": site, "phantom": phantom,
              "operator": operator, "notes": notes}

    # The same file analysed twice produces two records that look identical in
    # History and double-count in any trend. The SHA-256 is already computed,
    # so say so instead of silently creating the duplicate.
    if not allow_duplicate:
        dupes = []
        for scan in scans:
            dupes.extend(store.find_by_sha256(scan.sha256))
        if dupes:
            audit("upload", user=user, client=client, outcome="duplicate",
                  filename=file.filename, existing=[d["id"] for d in dupes])
            log.info("upload rejected as duplicate of %s",
                     [d["id"] for d in dupes])
            return JSONResponse(status_code=409, content=pipeline.to_jsonable({
                "detail": "This file has already been analysed.",
                "duplicate_of": dupes,
            }))

    created = []
    for scan in scans:
        aid = store.new_analysis(scan, data, ingest.protocol_signature(scan.meta),
                                 ALGO_VERSION, pdef.version, labels=labels)
        _scans[aid] = scan
        store.audit(aid, "A", "uploaded",
                    {"source": scan.source_name, "kind": scan.kind,
                     **{k: v for k, v in labels.items() if v}})
        audit("upload", user=user, client=client, analysis=aid,
              filename=scan.source_name, kind=scan.kind,
              sha256=scan.sha256, bytes=len(data),
              **{k: v for k, v in labels.items() if v})
        log.info("uploaded analysis=%s source=%r site=%r phantom=%r sha=%s",
                 aid, scan.source_name, labels["site"], labels["phantom"],
                 scan.sha256[:16])
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
def list_analyses(site: str = "", phantom: str = "", signature: str = "",
                  validation: str = "", completed_only: bool = False):
    return {"analyses": store.list_all(site=site or None,
                                       phantom=phantom or None,
                                       signature=signature or None,
                                       validation=validation or None,
                                       completed_only=completed_only)}


@app.get("/api/labels")
def labels():
    return store.labels()


class LabelBody(BaseModel):
    site: str | None = None
    phantom: str | None = None
    operator: str | None = None
    notes: str | None = None


@app.post("/api/analyses/{aid}/labels")
def set_labels(aid: str, body: LabelBody, request: Request):
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    before = {k: rec.get(k) for k in fields}
    store.set_labels(aid, fields)
    store.audit(aid, "F", "labels edited", fields)
    audit("labels", user=_current_user(request), client=_client_key(request),
          analysis=aid, before=before, after=fields)
    return {"ok": True, **fields}


@app.get("/api/analyses/{aid}")
def get_analysis(aid: str):
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    payload = {k: rec[k] for k in ("id", "created_at", "source_name", "sha256",
                                   "kind", "reduced_precision", "signature",
                                   "stage", "status", "sid_mm", "is_baseline",
                                   "algo_version", "pdef_version",
                                   "site", "phantom", "operator", "notes",
                                   "acquired_at", "validation_status",
                                   "validated_by", "validation_comment",
                                   "validated_at")}
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


_ROI_TYPES = ("rect", "circle", "annulus", "segment")


def _walk_find(node, roi_id, types=("rect", "circle", "annulus", "segment")):
    if isinstance(node, dict):
        if node.get("id") == roi_id and node.get("type") in types:
            return node
        for v in node.values():
            r = _walk_find(v, roi_id, types)
            if r is not None:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _walk_find(v, roi_id, types)
            if r is not None:
                return r
    return None


def _walk_children(node, roi_id, out=None):
    """Every ROI whose id is '<roi_id>/…' — the companions of one handle."""
    if out is None:
        out = []
    prefix = roi_id + "/"
    if isinstance(node, dict):
        if (isinstance(node.get("id"), str) and node["id"].startswith(prefix)
                and node.get("type") in _ROI_TYPES):
            out.append(node)
        else:
            for v in node.values():
                _walk_children(v, roi_id, out)
    elif isinstance(node, list):
        for v in node:
            _walk_children(v, roi_id, out)
    return out


def _seg_angle(seg: dict):
    from ..analysis.common import roi_angle_deg
    return roi_angle_deg(seg)


@app.post("/api/analyses/{aid}/roi")
def move_roi(aid: str, body: RoiMove, request: Request):
    """Move one ROI and everything attached to it.

    The whole edit happens inside one write transaction. The geometry is a
    single JSON blob, so a plain read-modify-write would let two overlapping
    edits discard one another — the user moves an ROI, it springs back, and
    moving a different one appears to 'fix' it."""
    ctx = _ctx(aid)
    from ..analysis.common import (roi_center_mm, roi_translate_mm,
                                   stats_for_roi)

    def edit(geom):
        if not geom:
            raise HTTPException(400, "no geometry yet")
        node = _walk_find(geom, body.roi_id)
        if node is None:
            raise HTTPException(404, f"ROI {body.roi_id} not found")

        old_center = roi_center_mm(node)
        updated = roi_center_from_px(ctx, node, body.center_px)
        node.clear()
        node.update(pipeline.to_jsonable(updated))
        node["manually_adjusted"] = True
        node["auto_center_mm"] = node.get("auto_center_mm", old_center)
        new_center = roi_center_mm(node)
        dx = new_center[0] - old_center[0]
        dy = new_center[1] - old_center[1]

        # Companions are found by id prefix, so a new one cannot be forgotten.
        changed = [node]
        for comp in _walk_children(geom, body.roi_id):
            if comp.get("type") == "segment" and body.roi_id.startswith("linepairs/"):
                fresh = linepairs.profile_for_center(
                    ctx, new_center,
                    (geom.get("linepairs") or {}).get("roi_size_mm", 12.6),
                    comp.get("id", ""), fallback_dir_deg=_seg_angle(comp))
            else:
                fresh = roi_translate_mm(ctx, comp, dx, dy)
            comp.clear()
            comp.update(pipeline.to_jsonable(fresh))
            changed.append(comp)
        return old_center, new_center, node, changed

    try:
        old_center, new_center, node, changed = store.mutate_json(
            aid, "geometry", edit)
    except KeyError:
        raise HTTPException(404, "analysis not found")

    store.audit(aid, "C", "roi moved",
                {"roi": body.roi_id, "from_mm": old_center, "to_mm": new_center})
    return pipeline.to_jsonable({"roi": node,
                                 "stats": stats_for_roi(ctx, node),
                                 "changed": changed})


class RoiRotate(BaseModel):
    roi_id: str
    angle_deg: float


@app.post("/api/analyses/{aid}/roi_rotate")
def rotate_roi(aid: str, body: RoiRotate, request: Request):
    """Set an ROI's phantom-frame angle.

    Needed when automatic placement gets the orientation wrong on a phantom
    that differs from the definition."""
    ctx = _ctx(aid)
    from ..analysis.common import roi_angle_deg, roi_rotate, stats_for_roi

    def edit(geom):
        if not geom:
            raise HTTPException(400, "no geometry yet")
        node = _walk_find(geom, body.roi_id)
        if node is None:
            raise HTTPException(404, f"ROI {body.roi_id} not found")
        old_angle = roi_angle_deg(node)
        if old_angle is None:
            raise HTTPException(400, "this ROI has no orientation to set")

        rotated = roi_rotate(ctx, node, body.angle_deg)
        node.clear()
        node.update(pipeline.to_jsonable(rotated))
        node["manually_adjusted"] = True
        if node.get("auto_angle_deg") is None:
            node["auto_angle_deg"] = old_angle

        changed = [node]
        # rotating the square is the user overriding the measured direction
        for comp in _walk_children(geom, body.roi_id):
            if comp.get("type") == "segment":
                fresh = roi_rotate(ctx, comp, body.angle_deg)
                comp.clear()
                comp.update(pipeline.to_jsonable(fresh))
                comp["manually_adjusted"] = True
                changed.append(comp)
        return old_angle, node, changed

    try:
        old_angle, node, changed = store.mutate_json(aid, "geometry", edit)
    except KeyError:
        raise HTTPException(404, "analysis not found")

    store.audit(aid, "C", "roi rotated",
                {"roi": body.roi_id, "from_deg": old_angle,
                 "to_deg": body.angle_deg})
    return pipeline.to_jsonable({"roi": node,
                                 "stats": stats_for_roi(ctx, node),
                                 "changed": changed})


class BlockPlace(BaseModel):
    """Reposition the low-contrast block as a whole.

    Either give centre+angle (drag / rotate) or four clicked corners. The
    shapes are pinned here so a malformed body is a 400 from the model rather
    than a 500 from numpy further down."""
    center_px: conlist(float, min_length=2, max_length=2) | None = None
    angle_deg: float | None = None
    corners_px: conlist(
        conlist(float, min_length=2, max_length=2),
        min_length=4, max_length=4) | None = None

    @field_validator("center_px", "corners_px")
    @classmethod
    def _finite(cls, v):
        if v is None:
            return v
        flat = v if isinstance(v[0], float) else [c for p in v for c in p]
        if not all(math.isfinite(c) for c in flat):
            raise ValueError("coordinates must be finite")
        return v

    @field_validator("angle_deg")
    @classmethod
    def _finite_angle(cls, v):
        if v is not None and not math.isfinite(v):
            raise ValueError("angle must be finite")
        return v


@app.post("/api/analyses/{aid}/lowcontrast_block")
def place_lowcontrast_block(aid: str, body: BlockPlace, request: Request):
    """Move or rotate the whole low-contrast block; the eight circles follow.

    The circles sit on a rigid grid inside the block, so correcting the block
    once is far better than dragging eight circles individually."""
    ctx = _ctx(aid)
    from ..analysis import lowcontrast
    from ..analysis.common import rect_roi

    def edit(geom):
        if not geom or not geom.get("lowcontrast"):
            raise HTTPException(400, "no low-contrast geometry yet")
        lcg = geom["lowcontrast"]
        block = lcg.get("block") or {}

        if body.corners_px:
            if len(body.corners_px) != 4:
                raise HTTPException(400, "exactly four corners are required")
            centre, angle = lowcontrast.block_from_corners(ctx, body.corners_px)
        else:
            centre = (list(ctx.T.px_to_mm(body.center_px))
                      if body.center_px else list(block.get("center_mm", [0, 0])))
            angle = (float(body.angle_deg) if body.angle_deg is not None
                     else float(block.get("angle_deg", 0.0)))

        size = block.get("size_mm") or ctx.pdef.lowcontrast["size_mm"]
        lcg["block"] = pipeline.to_jsonable(
            rect_roi(ctx, centre, size, angle, roi_id="lowcontrast/block"))
        lcg["block"]["manually_adjusted"] = True
        lcg["angle_deg"] = angle
        # the grid shift was a refinement of the OLD placement; drop it
        lcg["grid_shift_mm"] = [0.0, 0.0]
        lcg["circles"] = pipeline.to_jsonable(
            lowcontrast.circles_for_block(ctx, centre, angle))
        for c in lcg["circles"]:
            for k in ("roi", "bg_roi", "full_circle"):
                c[k]["manually_adjusted"] = True
        return centre, angle, lcg

    try:
        centre, angle, lcg = store.mutate_json(aid, "geometry", edit)
    except KeyError:
        raise HTTPException(404, "analysis not found")

    store.audit(aid, "C", "low-contrast block placed",
                {"center_mm": centre, "angle_deg": angle,
                 "by": "corners" if body.corners_px else "drag"})
    return pipeline.to_jsonable({"lowcontrast": lcg,
                                 "center_mm": centre, "angle_deg": angle})


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
def compute(aid: str, body: ComputeBody, request: Request):
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
    audit("compute", user=_current_user(request), client=_client_key(request),
          analysis=aid, outcome=status, sid_mm=body.sid_mm)
    log.info("computed analysis=%s overall=%s", aid, status)
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
def finalize(aid: str, body: FinalizeBody, request: Request):
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    store.update(aid, stage="F", status=rec.get("status") or "complete")
    if body.baseline:
        if rec.get("reduced_precision"):
            raise HTTPException(400, "reduced-precision analyses cannot be baselines")
        store.set_baseline(aid, True)
    store.audit(aid, "F", "finalized", {"baseline": body.baseline})
    audit("finalize", user=_current_user(request), client=_client_key(request),
          analysis=aid, baseline=body.baseline,
          site=rec.get("site"), phantom=rec.get("phantom"))
    return {"ok": True}


class DeleteBody(BaseModel):
    admin_password: str = ""
    confirm_id: str = ""
    reason: str = ""


@app.post("/api/analyses/{aid}/delete")
def delete_analysis(aid: str, body: DeleteBody, request: Request):
    """Delete an analysis and its stored source file.

    Deliberately hard to do by accident on a shared installation:
      * a separate ADMIN password is required — not the everyday login;
      * the analysis id must be typed back to confirm;
      * every attempt, successful or not, goes to the audit log.
    With no admin password configured the endpoint refuses outright."""
    user = _current_user(request)
    client = _client_key(request)
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")

    if not cfg.deletion_enabled:
        audit("delete", user=user, client=client, analysis=aid,
              outcome="refused", reason="deletion disabled")
        raise HTTPException(
            403, "Deletion is disabled on this installation. An administrator "
                 "must set PHANTOMQA_ADMIN_PASSWORD_HASH in .env "
                 "(python -m phantom_qa.manage set-admin-password).")

    wait = admin_throttle.locked_for(client)
    if wait > 0:
        audit("delete", user=user, client=client, analysis=aid,
              outcome="throttled")
        raise HTTPException(429, f"Too many failed admin attempts. Try again in "
                                 f"{wait // 60 + 1} min.")

    if body.confirm_id.strip() != aid:
        audit("delete", user=user, client=client, analysis=aid,
              outcome="refused", reason="confirmation id mismatch")
        raise HTTPException(400, "Type the analysis id exactly to confirm.")

    if not verify_password(body.admin_password, cfg.admin_password_hash):
        admin_throttle.record_failure(client)
        audit("delete", user=user, client=client, analysis=aid,
              outcome="denied", reason="bad admin password")
        log.warning("delete denied (bad admin password) analysis=%s client=%s "
                    "user=%s", aid, client, user)
        raise HTTPException(401, "Incorrect administrator password.")

    admin_throttle.reset(client)
    audit("delete", user=user, client=client, analysis=aid, outcome="ok",
          site=rec.get("site"), phantom=rec.get("phantom"),
          source=rec.get("source_name"), sha256=rec.get("sha256"),
          created_at=rec.get("created_at"), reason=body.reason)
    log.warning("DELETED analysis=%s site=%r phantom=%r by user=%s client=%s",
                aid, rec.get("site"), rec.get("phantom"), user, client)
    store.delete(aid)
    _scans.pop(aid, None)
    _regs.pop(aid, None)
    return {"ok": True}


@app.get("/api/deletion_policy")
def deletion_policy():
    return {"enabled": cfg.deletion_enabled,
            "requires_admin_password": True,
            "requires_id_confirmation": True}


class ValidationBody(BaseModel):
    status: str                      # validated | conditionally_validated |
                                     # not_validated | "" to withdraw
    validated_by: str = ""           # the NAME of the person signing off
    comment: str = ""
    admin_password: str = ""


@app.get("/api/validation_policy")
def validation_policy():
    return {"enabled": cfg.deletion_enabled,     # same admin credential
            "states": list(VALIDATION_STATES),
            "labels": VALIDATION_LABELS}


@app.post("/api/analyses/{aid}/validation")
def set_validation(aid: str, body: ValidationBody, request: Request):
    """Administrator's ruling on an analysis.

    Gated by the same administrator password as deletion, because it is the
    other decision an ordinary user must not be able to make. The approver's
    NAME is recorded separately from the password: a shared credential proves
    the right to sign off, not who did it."""
    user, client = _current_user(request), _client_key(request)
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")

    if not cfg.deletion_enabled:
        audit("validation", user=user, client=client, analysis=aid,
              outcome="refused", reason="no administrator password configured")
        raise HTTPException(
            403, "Validation requires an administrator password. Set "
                 "PHANTOMQA_ADMIN_PASSWORD_HASH in .env "
                 "(python -m phantom_qa.manage set-admin-password).")

    wait = admin_throttle.locked_for(client)
    if wait > 0:
        audit("validation", user=user, client=client, analysis=aid,
              outcome="throttled")
        raise HTTPException(429, f"Too many failed admin attempts. Try again in "
                                 f"{wait // 60 + 1} min.")

    if not verify_password(body.admin_password, cfg.admin_password_hash):
        admin_throttle.record_failure(client)
        audit("validation", user=user, client=client, analysis=aid,
              outcome="denied", reason="bad admin password")
        log.warning("validation denied (bad admin password) analysis=%s "
                    "client=%s", aid, client)
        raise HTTPException(401, "Incorrect administrator password.")
    admin_throttle.reset(client)

    try:
        applied = store.set_validation(aid, body.status, body.validated_by,
                                       body.comment)
    except ValueError as e:
        raise HTTPException(400, str(e))

    before = {"validation_status": rec.get("validation_status", ""),
              "validated_by": rec.get("validated_by", "")}
    store.audit(aid, "F", "validation set", applied)
    audit("validation", user=user, client=client, analysis=aid,
          outcome=applied["validation_status"] or "withdrawn",
          approver=applied["validated_by"], comment=applied["validation_comment"],
          before=before, site=rec.get("site"), phantom=rec.get("phantom"))
    log.info("validation analysis=%s -> %r by %r (login %s)", aid,
             applied["validation_status"], applied["validated_by"], user)
    return {"ok": True, **applied}


@app.get("/api/analyses/{aid}/verify")
def verify(aid: str, request: Request):
    """Re-hash the stored source file and compare with the recorded SHA-256."""
    result = store.verify_integrity(aid)
    if result["status"] == "not_found":
        raise HTTPException(404, "not found")
    outcome = "ok" if result["status"] == "ok" else "FAILED"
    audit("verify", user=_current_user(request), client=_client_key(request),
          analysis=aid, outcome=outcome, result=result["status"])
    if result["status"] != "ok":
        log.error("integrity check %s for analysis=%s: %s",
                  result["status"], aid, result["message"])
    return result


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


def _selected_records(ids: str = "", site: str = "", phantom: str = "",
                      signature: str = "") -> list[dict]:
    """Records for an explicit id list, or for a label filter."""
    if ids:
        out = []
        for a in ids.split(","):
            rec = store.get(a.strip())
            if rec:
                out.append(rec)
        return out
    listing = store.list_all(site=site or None, phantom=phantom or None,
                             signature=signature or None, completed_only=True)
    return [store.get(item["id"]) for item in listing]


@app.get("/api/export.csv", response_class=PlainTextResponse)
def export_csv_many(ids: str = "", site: str = "", phantom: str = "",
                    signature: str = "", layout: str = "long"):
    recs = _selected_records(ids, site, phantom, signature)
    if not recs:
        raise HTTPException(404, "no matching analyses")
    return wide_csv_export(recs) if layout == "wide" else csv_export(recs)


@app.get("/api/comparison_report.html", response_class=HTMLResponse)
def comparison_report(ids: str = "", site: str = "", phantom: str = "",
                      signature: str = ""):
    recs = _selected_records(ids, site, phantom, signature)
    if not recs:
        raise HTTPException(404, "no matching analyses")
    suffix = ""
    if site or phantom:
        suffix = " — " + " / ".join(x for x in (site, phantom) if x)
    return build_comparison_report(
        recs, title_suffix=suffix,
        filters={"site": site, "phantom": phantom, "signature": signature})


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
    # the report states whether the source file still matches its recorded hash
    integrity = store.verify_integrity(aid)
    if integrity.get("status") != "ok":
        log.error("integrity %s while building report for analysis=%s",
                  integrity.get("status"), aid)
    return build_report(rec, overlay_png=overlay, baseline=baseline,
                        integrity=integrity)


@app.get("/api/trends")
def trends(signature: str = "", site: str = "", phantom: str = "",
           ids: str = ""):
    """Trend data for a label filter, a signature, or an explicit id list."""
    if ids:
        listing = [{"id": a.strip()} for a in ids.split(",") if a.strip()]
    else:
        listing = store.list_all(site=site or None, phantom=phantom or None,
                                 signature=signature or None,
                                 completed_only=True)
    out = []
    for item in listing:
        rec = store.get(item["id"])
        if not rec or not rec.get("results"):
            continue
        out.append({"id": rec["id"], "created_at": rec["created_at"],
                    "acquired_at": rec.get("acquired_at") or rec["created_at"],
                    "site": rec.get("site", ""), "phantom": rec.get("phantom", ""),
                    "signature": rec.get("signature", ""),
                    "status": rec.get("status", ""),
                    "is_baseline": rec["is_baseline"],
                    "rows": flatten_results(rec["results"])})
    out.sort(key=lambda a: a["acquired_at"])
    return pipeline.to_jsonable({
        "filter": {"signature": signature, "site": site, "phantom": phantom},
        "analyses": out})


@app.get("/api/signatures")
def signatures():
    sigs = {}
    for item in store.list_all():
        sigs.setdefault(item["signature"], 0)
        sigs[item["signature"]] += 1
    return {"signatures": [{"signature": k, "count": v} for k, v in sigs.items()]}


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return _login_page()


_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/", response_class=HTMLResponse)
def index():
    """index.html with the mount prefix injected, so the single-page app builds
    its URLs correctly whether it is served from / or from /x-ray/."""
    with open(os.path.join(_STATIC_DIR, "index.html"), encoding="utf-8") as f:
        page = f.read()
    return page.replace("<head>", f'<head>\n<base href="{html_escape(_base_href())}">', 1)


app.mount("/", StaticFiles(directory=_STATIC_DIR, html=False), name="static")
