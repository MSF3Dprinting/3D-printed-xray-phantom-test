"""FastAPI backend for the phantom QA wizard.

Run with:  python run_app.py   (or: uvicorn phantom_qa.webapp.main:app)
"""

from __future__ import annotations

import hashlib
import html as _html
import io
import json
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
from starlette.concurrency import run_in_threadpool
from starlette.middleware import gzip as _gzip
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, conlist, field_validator

from .. import ALGO_VERSION
from .. import ingest, layout_profile, pipeline, quality
from ..analysis import linepairs, lowcontrast
from ..analysis.common import roi_center_from_px
from ..comparison_report import COMPARISON_SCRIPT_CSP, build_comparison_report
from .. import thumbnails
from ..config import get_config
from ..logging_setup import audit, get_logger, setup_logging
from ..phantom_def import load_default
from ..registration import Registration, Transform
from ..report import build_report
from ..security import (CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, SharedThrottle,
                        csrf_ok, issue_session, new_csrf_token, read_session,
                        verify_password)
from ..store import (VALIDATION_LABELS, VALIDATION_STATES,
                     ProtectedAnalysis, StaleGeometry, Store,
                     acquisition_flag, csv_export, flatten_results,
                     resolve_root, wide_csv_export)

#: A deletion reason short enough to be meaningless is the same as none at all,
#: and the audit log is the only record of why data was destroyed.
MIN_DELETE_REASON_CHARS = 5
MAX_DELETE_REASON_CHARS = 500

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

# Nothing was compressed before — not the JSON, not the HTML, not the 127 kB of
# browser code. Over a field link that is the difference between a usable page
# and a slow one: measured on a real record, the analysis payload falls from
# 197 kB to 90, a proposal from 92 to 41, the printed report from 1.7 MB to
# 1.3, and app.js from 127 kB to 37.
#
# In the application rather than in the proxy on purpose: the proxy
# configuration is deployment, which this work does not change, and a site that
# already compresses simply sees content-encoding set and leaves it alone.
# A rendered scan is a PNG and a DICOM is already packed: compressing them
# again spends CPU on every request to gain a fraction of a percent. Starlette
# decides that from a module-level tuple and offers no constructor argument for
# it, so the tuple is extended here. A test asserts images come back
# uncompressed, so if a future version changes the mechanism it fails loudly
# instead of quietly going back to wasting the time.
_gzip.DEFAULT_EXCLUDED_CONTENT_TYPES = tuple(
    set(_gzip.DEFAULT_EXCLUDED_CONTENT_TYPES)
    | {"image/", "application/zip", "application/dicom", "application/gzip"})
app.add_middleware(GZipMiddleware, minimum_size=900, compresslevel=6)
store = Store(resolve_root(ROOT))
pdef = load_default()

# Shared across gunicorn workers — an in-process counter would give an attacker
# max_attempts x worker_count guesses.
throttle = SharedThrottle(store.db_path, "login", cfg.max_login_attempts,
                          cfg.lockout_minutes)
admin_throttle = SharedThrottle(store.db_path, "admin",
                                max(cfg.max_login_attempts // 2, 3),
                                cfg.lockout_minutes)

# Per-process caches: under gunicorn each worker has its own copy, so nothing
# here may be treated as authoritative. _regs in particular is keyed by the
# stored registration itself, not by the analysis id — otherwise a worker that
# served an earlier request keeps handing out a transform that a /register on
# another worker has since replaced, and every pixel coordinate derived from it
# would be wrong.
_scans: dict[str, ingest.ScanData] = {}       # id -> ScanData cache
_regs: dict[tuple, Registration] = {}         # (id, reg fingerprint) -> Registration
_img_cache: dict[tuple, bytes] = {}


def _forget(aid: str):
    """Drop every cached artefact of one analysis in THIS worker."""
    _scans.pop(aid, None)
    for key in [k for k in _regs if k[0] == aid]:
        _regs.pop(key, None)
    for key in [k for k in _img_cache if k[0] == aid]:
        _img_cache.pop(key, None)


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
    # The comparison report carries one inline script — the shared picture
    # window and the enlargement — inline so that a copy saved from the
    # browser keeps working. It is admitted by the hash of its exact text,
    # not by 'unsafe-inline', so nothing else inline can run on that page.
    script_src = "'self'"
    if path == "/api/comparison_report.html":
        script_src += " " + COMPARISON_SCRIPT_CSP
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        f"script-src {script_src}; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'")
    if cfg.https_only:
        response.headers["Strict-Transport-Security"] = \
            "max-age=31536000; includeSubDomains"
    if path.startswith("/api/"):
        # Records, listings and verdicts change, so they are never cached.
        # A rendered scan image is the exception: the uploaded pixels are
        # immutable and the URL already carries everything that varies the
        # picture (id, window, scale), so the same URL can only ever mean the
        # same bytes. Re-fetching ~1 MB on every open and every window change
        # was the single heaviest habit on a field link — about fifteen
        # seconds each time at 512 kbit/s. Private: it is patient-adjacent
        # imagery and must not sit in a shared proxy. The JPEG the viewer now
        # draws is the same render, so the same reasoning holds for it.
        if path.endswith(("/image.png", "/image.jpg")) \
                and response.status_code == 200:
            response.headers["Cache-Control"] = "private, max-age=86400"
        elif path.endswith("/lowcontrast_view.png") \
                and response.status_code == 200 \
                and response.headers.get("Cache-Control", "").startswith(
                    "private"):
            # The close-up decides for itself: it is keepable only when its
            # URL names the placement it actually shows, so its own header
            # stands.
            pass
        else:
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


@app.exception_handler(StaleGeometry)
async def _stale_geometry(request: Request, exc: StaleGeometry):
    """Someone else moved the measuring points while this edit was in hand.

    Handled centrally so a future editing endpoint cannot forget it. 409 —
    the request was valid, the state it assumed was not — and the current
    state travels back so the page can resynchronise rather than guess."""
    log.info("stale geometry edit on %s: client had %s, stored is %s",
             _app_path(request), exc.expected, exc.actual)
    return JSONResponse(status_code=409, content={
        "detail": (
            "Someone else changed the measuring points on this analysis while "
            "you were working on it, so this change was not applied — applying "
            "it would have silently undone theirs. The analysis is being "
            "reloaded; make your change again on the current points."),
        "stale_geometry": True,
        "expected_seq": exc.expected,
        "current_seq": exc.actual,
    })


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
    # analysis_timeout_s rides along on a request the page already makes at
    # boot, so the browser can give up a little after the server would rather
    # than guessing, and without another round trip on a slow link.
    common = {"csrf": request.cookies.get(CSRF_COOKIE),
              "analysis_timeout_s": cfg.analysis_timeout_s}
    if not cfg.auth_enabled:
        return {"auth_enabled": False, "authenticated": True, **common}
    s = read_session(cfg.secret_key, request.cookies.get(SESSION_COOKIE))
    return {"auth_enabled": True, "authenticated": s is not None,
            "user": (s or {}).get("u"), **common}


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


def _scan(aid: str, cache: bool = True) -> ingest.ScanData:
    if aid in _scans:
        # The cache is per gunicorn worker. A delete served by ANOTHER worker
        # cannot reach this dict, so an existence check is what keeps a deleted
        # scan's pixels from being served forever. One indexed SELECT — cheap
        # next to decoding an image.
        if store.exists(aid):
            return _scans[aid]
        _forget(aid)
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
    # A comparison reads many scans once each. Keeping every one of them
    # decoded — some 70 MB apiece at float64, in every worker — would turn one
    # ten-scan report into most of a gigabyte that nothing ever releases.
    if cache:
        _scans[aid] = match
    return match


def _reg(aid: str, rec: dict | None = None) -> Registration:
    rec = rec if rec is not None else store.get(aid)
    if rec is None or not rec.get("reg"):
        raise HTTPException(400, "not registered yet")
    r = rec["reg"]
    # Fingerprint the stored transform, so a re-registration invalidates the
    # cache in every worker instead of only the one that served it.
    key = (aid, json_dumps_stable(r.get("transform")))
    if key in _regs:
        return _regs[key]
    try:
        reg = Registration(
            transform=Transform.from_dict(r["transform"]),
            corners_px=np.asarray(r["corners_px"], float),
            coarse_angle_deg=r.get("coarse_angle_deg", float("nan")),
            score=r.get("score", {}),
            candidate_scores=r.get("candidate_scores", []),
            landmarks=r.get("landmarks", {}),
            residual_rms_mm=r.get("residual_rms_mm", float("nan")),
        )
    except (KeyError, TypeError, ValueError) as e:
        # A corrupted stored registration must not 500 every endpoint that
        # builds a context from it — say what is wrong and how to recover.
        log.error("stored registration for analysis=%s is corrupted: %s",
                  aid, e)
        raise HTTPException(
            409, "The stored registration for this analysis is corrupted. "
                 "Re-register it in Stage A (manual corners work too).")
    if len(_regs) > 64:
        _regs.clear()
    _regs[key] = reg
    return reg


def json_dumps_stable(obj) -> str:
    import json as _json
    return _json.dumps(obj, sort_keys=True)


def _ctx(aid: str, rec: dict | None = None):
    scan = _scan(aid)
    rec = rec if rec is not None else store.get(aid)
    return pipeline.build_ctx(scan, pdef, _reg(aid, rec),
                              {"scan_meta": scan.meta,
                               "sid_mm": rec.get("sid_mm") or 1000.0})


def _require_unsigned(rec: dict, what: str):
    """Refuse to change measurements an administrator has signed off.

    Changing the geometry drops the stored results, because results computed
    from geometry that no longer exists are worse than none. On a validated
    analysis that combination is worse still: the ruling, the approver's name
    and the date all survive while the numbers they refer to are gone, and the
    record then vanishes from every trend and export, which filter on completed
    results. The reanalyze CLI already skips signed-off analyses; the web path
    now does the same.

    The way through is a re-run, not withdrawing the ruling: signing off also
    finalises, and withdrawing a ruling does not un-finalise, so an operator
    who followed "withdraw first" met a second refusal. A re-run withdraws the
    ruling and keeps the numbers it replaces, in one recorded step."""
    if (rec.get("validation_status") or "").strip():
        raise HTTPException(
            409,
            f"This analysis has been signed off by "
            f"{rec.get('validated_by') or 'an administrator'}"
            + (f" on {rec['validated_at'][:16]}" if rec.get("validated_at") else "")
            + f", so {what} would change measurements somebody has taken "
              f"responsibility for. If it really needs reworking, use "
              f"“Re-run analysis” with the administrator password — that "
              f"withdraws the ruling and keeps the previous results.")
    # Finalising is a weaker statement than signing off, but it is still a
    # statement: the operator said these numbers are done. Editing straight
    # through it used to drop the results silently and take the record out of
    # every trend, with nothing to show that had happened. Re-run is the way
    # to rework it, because that keeps what it replaces.
    if (rec.get("finalized_at") or "").strip():
        raise HTTPException(
            409,
            f"This analysis was finalised on {rec['finalized_at'][:16]}"
            + (f" by {rec['finalized_by']}" if rec.get("finalized_by") else "")
            + f", so {what} would change numbers that were declared finished. "
              f"Use “Re-run analysis” instead — it keeps the previous results "
              f"so nothing is lost.")


def _roi_stats_or_none(ctx, node):
    """Statistics for an ROI, or None for shapes that have none.

    Segments (the line-pair profile lines, the wedge axis) carry no area, and
    stats_for_roi raises on them. Without this guard, rotating a profile line
    committed the edit and THEN returned a 500, leaving the client convinced
    the change had failed while the database said otherwise."""
    from ..analysis.common import stats_for_roi
    if (node or {}).get("type") not in ("rect", "circle", "annulus"):
        return None
    return stats_for_roi(ctx, node)


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


def _deadline() -> pipeline.Deadline:
    """The time budget for one request's worth of measuring."""
    return pipeline.Deadline(cfg.analysis_timeout_s)


def _do_register(aid: str, corners_hint=None):
    scan = _scan(aid)
    reg = pipeline.run_stage_a(scan, pdef, corners_hint=corners_hint)
    # Judge the exposure here rather than at upload: the useful signals come
    # from the registration, and re-registering by hand is exactly when the
    # verdict should be revisited — manual corners can rescue a scan the
    # automatic fit had mangled.
    verdict = quality.assess(scan.pixels, reg)
    store.update(aid, quality=verdict, quality_verdict=verdict["verdict"])
    if verdict["verdict"] != "ok":
        store.audit(aid, "A", "acquisition quality", {
            "verdict": verdict["verdict"], "failed": verdict["failed"]})
        log.info("analysis=%s flagged as %s: %s", aid, verdict["verdict"],
                 ", ".join(verdict["failed"]))
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

class UploadCheckBody(BaseModel):
    #: Hex SHA-256 of each file the operator picked, computed in the browser.
    sha256: list[str] = []


@app.post("/api/upload_check")
def upload_check(body: UploadCheckBody, request: Request):
    """Is this exact file already here? Asked before sending it.

    The duplicate refusal itself was always correct — seven weeks of audit log
    show eight refusals, every one of them byte-identical to a record that
    already existed, and three file names that carried genuinely different
    files each went through without complaint. What made it hurt was the
    price: on a 512 kbit/s link the operator paid two minutes to be told the
    file was already there.

    Hashing in the browser costs nothing and moves the answer in front of the
    transfer. A hash is cheap to fake, but nothing here acts on it — it can
    only reveal records the same login may already read — and the real check
    still runs on the bytes at upload, so a lie changes nothing but the
    advice.

    Only the hashes actually asked about are answered, and only for files the
    server holds; an unknown hash returns nothing at all, so the endpoint
    cannot be used to walk the archive."""
    seen, out = set(), []
    for raw in body.sha256[:32]:                 # a CD export, not a scrape
        h = str(raw or "").strip().lower()
        if len(h) != 64 or not all(c in "0123456789abcdef" for c in h):
            continue
        if h in seen:
            continue
        seen.add(h)
        dupes = store.find_by_sha256(h)
        if dupes:
            out.append({"sha256": h, "duplicate_of": dupes})
    if out:
        audit("upload_check", user=_current_user(request),
              client=_client_key(request), outcome="duplicate",
              existing=[d["id"] for e in out for d in e["duplicate_of"]])
    return pipeline.to_jsonable({"duplicates": out})


#: How an upload may arrive: the file as it is (empty, or "identity"), or
#: packed with gzip by the browser. Anything else is refused rather than
#: guessed at — storing bytes the server did not know how to read back would
#: store something other than the scan, under the scan's name.
_UPLOAD_ENCODINGS = ("", "identity", "gzip")

#: What the operator reads when a packed upload does not unpack to the file
#: they chose. Plain on purpose: the cause is the link or the machine, the
#: remedy is the same either way, and the reason in detail is in the audit log.
DAMAGED_UPLOAD_MESSAGE = ("The file was damaged on the way — nothing was "
                          "stored. Please send it again.")

_HEX = frozenset("0123456789abcdef")


class DamagedUpload(Exception):
    """A packed upload that did not unpack to the file the browser described.

    Its own exception rather than a plain 400, for the same reason as
    QualityRefusal: the page has to tell this refusal from every other one
    without parsing an English sentence. The server cannot tell damage on the
    link from the browser's own packing going wrong, and the second would go
    wrong the same way on every attempt — so on this answer, and only this
    one, the page stops packing and sends the file as it is."""


@app.exception_handler(DamagedUpload)
async def _damaged_upload(request: Request, exc: DamagedUpload):
    return JSONResponse(status_code=400, content={
        "detail": DAMAGED_UPLOAD_MESSAGE,
        "damaged_transfer": True,
    })


def _is_byte_count(text: str) -> bool:
    """Whether a form field holds a size the way the page writes one.

    ASCII digits, and not too many of them. str.isdigit() alone also passes
    characters such as "²" that int() cannot read, and digit strings past
    int()'s 4300-digit limit; either would escape as a 500 with no audit line,
    where a malformed size deserves the same audited refusal as a missing one.
    Fifteen digits is a petabyte, far past any upload cap, so nothing a browser
    actually measured is turned away here."""
    return text.isascii() and text.isdigit() and len(text) <= 15


async def _unpack_upload(received: bytes, *, user: str, client: str,
                         filename: str, original_sha256: str,
                         original_size: str) -> bytes:
    """The original file from a gzip-packed upload, or an HTTP refusal.

    A packed upload must say what it packs: the browser's SHA-256 of the
    original and its size. Without the fingerprint the server could check
    only gzip's own CRC32 and the length — which catch a damaged transfer but
    not a wrong file — and the page is written never to pack without one, so
    a packed body arriving without it did not come from the page and is
    refused rather than half-verified."""
    sha = str(original_sha256 or "").strip().lower()
    size_text = str(original_size or "").strip()
    trail = {"filename": filename, "encoding": "gzip",
             "sent_bytes": len(received)}
    if len(sha) != 64 or not set(sha) <= _HEX or not _is_byte_count(size_text):
        audit("upload", user=user, client=client, outcome="rejected",
              error="packed upload without the original's fingerprint "
                    "and size", **trail)
        raise HTTPException(
            400, "A packed upload must carry the SHA-256 and the size of "
                 "the original file.")
    size = int(size_text)
    trail.update(original_bytes=size, original_sha256=sha)
    cap = cfg.max_upload_mb * 1024 * 1024
    try:
        # Unpacking and fingerprinting are CPU work like the decode below,
        # and this endpoint is async; same reasoning, same threadpool.
        return await run_in_threadpool(
            ingest.unpack_gzip, received, original_size=size,
            original_sha256=sha, max_bytes=cap)
    except ingest.TransferRefused as e:
        damaged = isinstance(e, ingest.DamagedTransfer)
        log.warning("packed upload refused (%s) name=%r sent=%d declared=%d "
                    "user=%s", e, filename, len(received), size, user)
        audit("upload", user=user, client=client,
              outcome="damaged" if damaged else "rejected",
              error=str(e), **trail)
        if damaged:
            raise DamagedUpload() from None
        raise HTTPException(
            e.status, f"Upload exceeds {cfg.max_upload_mb} MB once unpacked "
                      f"— nothing was stored.")


@app.post("/api/analyses")
async def upload(request: Request, file: UploadFile = File(...),
                 site: str = Form(""), phantom: str = Form(""),
                 operator: str = Form(""), notes: str = Form(""),
                 allow_duplicate: bool = Form(False),
                 encoding: str = Form(""), original_sha256: str = Form(""),
                 original_size: str = Form(""),
                 original_name: str = Form("")):
    user, client = _current_user(request), _client_key(request)
    received = await file.read()
    # The middleware caps the DECLARED size, but a chunked upload carries no
    # Content-Length and a hostile client can lie in the header. Measuring the
    # bytes actually received closes both holes; starlette has already spooled
    # them to disk by now, so this costs nothing extra in memory.
    if len(received) > cfg.max_upload_mb * 1024 * 1024:
        audit("upload", user=user, client=client, outcome="rejected",
              filename=file.filename, error="body larger than declared cap")
        raise HTTPException(413, f"Upload exceeds {cfg.max_upload_mb} MB")

    # A packed upload is unpacked and proven identical to the original before
    # anything else sees it. From here on `data` is the original file whichever
    # way it travelled, so the stored bytes, the recorded sha256, the
    # duplicate check and the analysis are all exactly what they would have
    # been had it been sent as it is — and the same file sent both ways is
    # recognised as the same file.
    enc = (encoding or "").strip().lower()
    if enc not in _UPLOAD_ENCODINGS:
        audit("upload", user=user, client=client, outcome="rejected",
              filename=file.filename, encoding=str(encoding)[:40],
              error="unknown encoding")
        raise HTTPException(
            400, f"Unknown upload encoding {str(encoding)[:40]!r} — send the "
                 f"file as it is, or packed with gzip.")
    if enc == "gzip":
        # The name decides how the bytes are read (a .png is an image, a
        # .zip a CD export), so it has to be the original's, not the name of
        # the packed part.
        packed_name = file.filename or "upload"
        name = (original_name or "").strip() or (
            packed_name[:-3] if packed_name.lower().endswith(".gz")
            else packed_name)
        data = await _unpack_upload(
            received, user=user, client=client, filename=name,
            original_sha256=original_sha256, original_size=original_size)
    else:
        enc, name, data = "identity", file.filename, received
    # What the link actually carried, next to the file it carried: the audit
    # line is where a slow upload is explained after the fact. Only its size
    # is kept — the decode and registration below take seconds, and a worker
    # has no reason to hold the packed copy beside the original meanwhile.
    sent = len(received)
    del received
    transfer = {"encoding": enc, "sent_bytes": sent}
    try:
        # Decoding a DICOM is seconds of CPU. This is the one endpoint declared
        # `async` — because the upload body has to be awaited — so doing that
        # work inline blocked the worker's whole event loop: every other
        # operator served by the same worker waited for this scan to decode.
        # The threadpool is where every other (synchronous) endpoint already
        # runs, so this only puts the upload back on an equal footing.
        scans = await run_in_threadpool(
            ingest.load_any_bytes, data, name or "upload")
    except Exception as e:
        log.warning("upload rejected (%s) name=%r user=%s",
                    e, name, user)
        audit("upload", user=user, client=client, outcome="rejected",
              filename=name, error=str(e), **transfer)
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
                  filename=name, existing=[d["id"] for d in dupes],
                  **transfer)
            log.info("upload rejected as duplicate of %s",
                     [d["id"] for d in dupes])
            return JSONResponse(status_code=409, content=pipeline.to_jsonable({
                "detail": "This file has already been analysed.",
                "duplicate_of": dupes,
            }))

    # The stored layout is looked up once and only advertised here. Applying it
    # means proposing every pattern first, which is the expensive step and is
    # thrown away the moment the operator corrects the registration by hand —
    # so that happens in /propose, at Stage A confirm, not per uploaded image.
    stored_layout = _profile_summary(
        store.get_phantom_profile(labels["phantom"]))

    created = []
    for scan in scans:
        # Store the bytes the recorded hash actually describes. For a zip
        # (CD export) that is the extracted member, NOT the container:
        # storing the container made every integrity check fail, because the
        # recorded sha256 is the member's.
        aid = store.new_analysis(scan, scan.source_bytes or data,
                                 ingest.protocol_signature(scan.meta),
                                 ALGO_VERSION, pdef.version, labels=labels)
        _scans[aid] = scan
        store.audit(aid, "A", "uploaded",
                    {"source": scan.source_name, "kind": scan.kind,
                     **{k: v for k, v in labels.items() if v}})
        audit("upload", user=user, client=client, analysis=aid,
              filename=scan.source_name, kind=scan.kind,
              sha256=scan.sha256, bytes=len(data), **transfer,
              **{k: v for k, v in labels.items() if v})
        log.info("uploaded analysis=%s source=%r site=%r phantom=%r sha=%s "
                 "sent=%d of %d bytes (%s)",
                 aid, scan.source_name, labels["site"], labels["phantom"],
                 scan.sha256[:16], sent, len(data), enc)
        # Different file, same exposure: re-exported from the archive with the
        # header rewritten. The hash cannot see it, and the second record would
        # count again in every trend. Said, not refused — and only once the
        # record exists, so accepting it costs the operator nothing.
        same_exposure = store.find_by_sop_uid(
            (scan.meta or {}).get("SOPInstanceUID", ""), exclude_id=aid)
        if same_exposure:
            store.audit(aid, "A", "same exposure as existing record",
                        {"existing": [d["id"] for d in same_exposure]})
            audit("upload", user=user, client=client, analysis=aid,
                  outcome="same_exposure",
                  existing=[d["id"] for d in same_exposure])
        try:
            # Registration is the other CPU-heavy half of an upload; same
            # reasoning as the decode above.
            reg = await run_in_threadpool(_do_register, aid)
            created.append({"id": aid, "source_name": scan.source_name,
                            "registered": True,
                            "phantom_profile": stored_layout,
                            "same_exposure": same_exposure,
                            # The operator is standing at the machine now; if
                            # the exposure is unusable this is the moment to
                            # say so, while repeating it is still easy.
                            "quality": (store.get(aid) or {}).get("quality"),
                            "registration": _reg_payload(aid, reg)})
        except Exception as e:
            created.append({"id": aid, "source_name": scan.source_name,
                            "registered": False, "error": str(e),
                            "same_exposure": same_exposure,
                            "phantom_profile": stored_layout})
    return {"analyses": created, "phantom_profile": stored_layout}


_VALIDATION_FILTERS = ("", "pending") + VALIDATION_STATES


def _check_filters(validation: str, order: str = "acquired"):
    """Reject filter values nothing recognises.

    Both parameters used to fall back silently — order=newest listed by
    acquisition date, validation=Validated exported every analysis — which on
    a scripted export reads as a correct answer to the wrong question."""
    if validation not in _VALIDATION_FILTERS:
        raise HTTPException(
            400, f"unknown validation filter {validation!r} — one of "
                 f"{', '.join(v or 'pending' for v in _VALIDATION_FILTERS)}")
    if order not in ("acquired", "uploaded"):
        raise HTTPException(400, "order must be 'acquired' or 'uploaded'")


@app.get("/api/analyses")
def list_analyses(site: str = "", phantom: str = "", signature: str = "",
                  validation: str = "", completed_only: bool = False,
                  unfinished_only: bool = False, limit: int = 0,
                  order: str = "acquired"):
    _check_filters(validation, order)
    if completed_only and unfinished_only:
        raise HTTPException(
            400, "completed_only and unfinished_only are opposites; "
                 "asking for both returns nothing.")
    # The upload page asks for a handful of unfinished records, not the whole
    # history — a listing is one of the few things an operator on a slow link
    # waits for repeatedly.
    limit = max(0, min(int(limit or 0), 200))
    # History prints its verdict with the same note the results page and the
    # report print ("pass — X-ray field alignment not checked"), built from the
    # per-test statuses alone so the results themselves never travel here.
    # They are read with the listing, in the same pass over the table.
    rows = store.list_all(site=site or None,
                          phantom=phantom or None,
                          signature=signature or None,
                          validation=validation or None,
                          completed_only=completed_only,
                          unfinished_only=unfinished_only,
                          limit=limit or None,
                          order_by=order,
                          status_fields=[(test, key) for test, key, _
                                         in pipeline.STATUS_FIELDS])
    for a in rows:
        # The statuses only make the note; the note is what travels.
        a["verdict_notes"] = pipeline.verdict_notes(a.pop("statuses"))
        # History's "re-run" asks for the administrator password exactly when
        # step F would. Both read the one definition of protection, so a row
        # and the opened record cannot disagree about the same analysis.
        a["protection"] = store.protection(a)
    return {"analyses": rows,
            "order": "uploaded" if order == "uploaded" else "acquired"}


@app.get("/api/labels")
def labels():
    return store.labels()


@app.get("/api/phantom_profiles")
def phantom_profiles():
    """Every stored measuring-point layout, with how many analyses still use it.

    A layout is dropped automatically when the last analysis carrying its
    phantom label goes, so a row with n_analyses = 0 should not normally
    appear; when it does, something deleted analyses outside the store."""
    return {"profiles": store.list_phantom_profiles()}


class ProfileDeleteBody(BaseModel):
    phantom: str = ""
    admin_password: str = ""
    reason: str = ""


@app.post("/api/phantom_profiles/forget")
def forget_phantom_profile(body: ProfileDeleteBody, request: Request):
    """Discard a phantom's stored layout without touching its analyses.

    Admin-gated for the same reason deletion is: the layout is shared by every
    future scan of that phantom, so dropping it is a decision about other
    people's work, not just the caller's."""
    user, client = _current_user(request), _client_key(request)
    label = store.profile_key(body.phantom)
    if not label:
        raise HTTPException(400, "name the phantom whose layout to forget")
    if not cfg.deletion_enabled:
        raise HTTPException(
            403, "Removing a stored layout requires an administrator password. "
                 "Set PHANTOMQA_ADMIN_PASSWORD_HASH in .env "
                 "(python -m phantom_qa.manage set-admin-password).")
    wait = admin_throttle.locked_for(client)
    if wait > 0:
        raise HTTPException(429, f"Too many failed admin attempts. Try again in "
                                 f"{wait // 60 + 1} min.")
    if not verify_password(body.admin_password, cfg.admin_password_hash):
        admin_throttle.record_failure(client)
        audit("phantom_profile", user=user, client=client, phantom=label,
              outcome="denied", refusal="bad admin password")
        raise HTTPException(401, "Incorrect administrator password.")
    admin_throttle.reset(client)
    gone = store.delete_phantom_profile(label)
    audit("phantom_profile", user=user, client=client, phantom=label,
          outcome="forgotten" if gone else "absent", reason=body.reason)
    log.warning("stored layout for phantom=%r forgotten by user=%s (%s)",
                label, user, "removed" if gone else "there was none")
    return {"ok": True, "deleted": gone, "phantom": label}


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
    try:
        out = store.set_labels(aid, fields)
    except KeyError:
        raise HTTPException(404, "not found")   # deleted while we were editing
    store.audit(aid, "F", "labels edited",
                {**fields, "layout_deleted": out.get("profile_deleted", False),
                 "baseline_demoted": out.get("baseline_demoted", False)})
    audit("labels", user=_current_user(request), client=_client_key(request),
          analysis=aid, before=before, after=fields,
          layout_deleted=out.get("profile_deleted", False))
    return {"ok": True, **fields,
            # A stored layout belongs to the phantom label. Renaming the last
            # analysis off a label leaves nothing for that layout to describe,
            # so it goes — and the operator is told, because it is not obvious.
            "layout_deleted": out.get("profile_deleted", False),
            "baseline_demoted": out.get("baseline_demoted", False),
            "phantom_before": out.get("phantom_before", "")}


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
                                   "validated_at", "finalized_at",
                                   "finalized_by")}
    # One list, computed in one place, so the page, the discard endpoint and
    # the re-run endpoint cannot disagree about whether a record is protected.
    payload["protection"] = store.protection(rec)
    # A record with a kept previous state and no results is a re-run that has
    # not finished — possibly because a connection dropped. The interface uses
    # this to offer the way back rather than leaving the operator with a
    # record that has quietly vanished from every trend.
    payload["revision_count"] = len(store.list_revisions(aid))
    payload["meta"] = rec.get("meta")
    payload["geometry"] = rec.get("geometry")
    payload["results"] = rec.get("results")
    payload["audit"] = rec.get("audit")
    # None on records analysed before the gate existed, which the interface
    # shows as "not assessed" — never as a pass it was never given.
    payload["quality"] = rec.get("quality")
    payload["acquired_flag"] = acquisition_flag(rec)
    payload["layout_source"] = rec.get("layout_source") or ""
    payload["history"] = store.geometry_state(aid)
    prof = store.get_phantom_profile(rec.get("phantom") or "")
    payload["phantom_profile"] = _profile_summary(prof)
    # What a delete would take with it. Shown in the confirmation panel, so an
    # operator can see that removing this row also forgets the phantom's
    # measuring-point layout before they agree to it.
    n_same = store.count_for_phantom(rec.get("phantom") or "")
    payload["delete_impact"] = {
        "phantom": store.profile_key(rec.get("phantom") or ""),
        "analyses_for_phantom": n_same,
        "is_last_for_phantom": bool(n_same == 1),
        "layout_would_be_deleted": bool(prof and n_same <= 1),
    }
    try:
        payload["registration"] = _reg_payload(aid, _reg(aid, rec))
    except HTTPException:
        payload["registration"] = None
    except Exception as e:                     # corrupted reg or missing file
        log.error("registration payload failed for analysis=%s: %s", aid, e)
        payload["registration"] = None
    return pipeline.to_jsonable(payload)


def _drop_cached_image(aid: str):
    """Forget every rendered view of one analysis, and its decoded pixels.

    The caches are per worker and keyed by window, scale and which picture it
    is, so one entry per view anyone happened to look at. A delete handled by
    another worker leaves all of them behind, still serveable.
    """
    for cached in [k for k in _img_cache if k[0] == aid]:
        _img_cache.pop(cached, None)
    _scans.pop(aid, None)


#: How the viewer's picture is encoded as JPEG. Quality 75 puts a reference
#: scan at 1600 px near a hundred kilobytes, against about 1.1 MB as PNG —
#: two seconds at 512 kbit/s instead of seventeen, on every analysis opened.
#: The picture is single-channel, so there is no colour to subsample, and
#: optimised, progressive coding is lossless and a few per cent smaller.
_VIEW_JPEG = {"quality": 75, "optimize": True, "progressive": True}


def _render_view(aid: str, wc: float | None, ww: float | None, scale: int,
                 fmt: str) -> bytes:
    """The scan windowed to eight bits for looking at, encoded as ``fmt``.

    One function behind both the PNG and the JPEG route, so the two cannot
    drift apart in the window they apply, the size they come out at, or the
    checks around them. The size matters most: the viewer places every
    outline through the picture's width over the scan's, so a picture one
    pixel narrower would put every ROI in the wrong place.
    """
    # Query values are viewer state, not trusted input: a zero or negative
    # scale crashes PIL's thumbnail, a huge one asks for a gigapixel resample,
    # and NaN passes FastAPI's float parsing and poisons the window arithmetic.
    scale = min(max(scale, 64), 4096)
    if wc is not None and not math.isfinite(wc):
        wc = None
    if ww is not None and not math.isfinite(ww):
        ww = None
    # Every other route reads the record first and answers 404 when it is gone;
    # this one could answer from its own memory instead. The caches are
    # per-worker, so a delete served by one worker left the others still
    # handing out the picture of a record that no longer exists — and, with the
    # image now privately cacheable, the browser would go on showing it.
    if not store.exists(aid):
        _drop_cached_image(aid)
        raise HTTPException(404, "not found")
    key = (aid, wc, ww, scale, fmt)
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
        if fmt == "jpeg":
            pil.save(buf, format="jpeg", **_VIEW_JPEG)
        else:
            pil.save(buf, format="png")
        # FIFO eviction: clearing the whole cache meant one operator paging
        # through History threw away every other operator's rendered view.
        while len(_img_cache) > 24:
            _img_cache.pop(next(iter(_img_cache)), None)
        _img_cache[key] = buf.getvalue()
    return _img_cache[key]


@app.get("/api/analyses/{aid}/image.png")
def image_png(aid: str, wc: float | None = None, ww: float | None = None,
              scale: int = 1600):
    """The lossless render. The viewer takes the JPEG below; this stays for
    anything that wants the exact eight-bit picture."""
    return Response(_render_view(aid, wc, ww, scale, "png"),
                    media_type="image/png")


@app.get("/api/analyses/{aid}/image.jpg")
def image_jpg(aid: str, wc: float | None = None, ww: float | None = None,
              scale: int = 1600):
    """The same render as JPEG: what the viewer draws, about a tenth the bytes.

    Safe to be lossy because nothing is measured from it. Every number comes
    from the original scan on the server, and the low-contrast discs — the
    one place where a few grey levels decide what an operator can see — are
    placed on their own lossless close-up, lowcontrast_view.png. The
    browser's window preview re-maps whatever picture it was given, so it
    works on the decoded JPEG exactly as it did on the PNG.
    """
    return Response(_render_view(aid, wc, ww, scale, "jpeg"),
                    media_type="image/jpeg")


def _block_placement(rec: dict):
    """Where the low-contrast block currently is, as the operator left it."""
    geom = (rec.get("geometry") or {}).get("lowcontrast") or {}
    block = geom.get("block") or {}
    if not block.get("center_mm"):
        raise HTTPException(400, "the measuring points have not been proposed "
                                 "for this analysis yet")
    return geom, block["center_mm"], float(block.get("angle_deg", 0.0))


def _block_view_key(rec: dict, centre, angle: float) -> str:
    """What the close-up picture depends on, as a short digest.

    The edit counter cannot stand in for it: it steps back on undo and then
    forward again onto a different edit, and it restarts at zero when the
    phantom is registered again, so the same counter value can name two
    different placements — and a cache keyed on it served the old picture
    under the new rings. The picture is a function of the stored pixels, the
    registration and where the block sits, so those are what it is keyed on.
    """
    reg = (rec.get("reg") or {}).get("transform")
    basis = json.dumps([rec.get("sha256") or "", reg, ALGO_VERSION,
                        [round(float(c), 4) for c in centre],
                        round(float(angle), 4)], sort_keys=True, default=str)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _keep_block_png(aid: str, key: str, view: dict) -> bytes:
    """The close-up as PNG, kept in this worker under the key it shows.

    The one place it is encoded, whichever request gets there first. Keyed by
    what the picture shows, so whatever is kept under a key is by
    construction that key's picture and never has to be checked again."""
    cache_key = (aid, "lcview", key)
    # Held here rather than read back from the cache: another request's
    # eviction may drop the entry between storing it and answering.
    png = _img_cache.get(cache_key)
    if png is None:
        from PIL import Image
        buf = io.BytesIO()
        Image.fromarray((view["image"] * 255).astype(np.uint8)).save(
            buf, format="png")
        png = buf.getvalue()
        while len(_img_cache) > 24:
            _img_cache.pop(next(iter(_img_cache)), None)
        _img_cache[cache_key] = png
    return png


@app.get("/api/analyses/{aid}/lowcontrast_view")
def lowcontrast_view(aid: str):
    """Where each disc is in the block view, and how plainly it shows.

    Separate from the picture so the picture can be cached on its own: the
    markers change whenever the block is nudged, the picture only when the
    geometry it was rendered from changes.
    """
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    geom, centre, angle = _block_placement(rec)
    ctx = _ctx(aid, rec)
    view = lowcontrast.block_view(ctx, centre, angle)
    key = _block_view_key(rec, centre, angle)
    # The page asks for exactly this picture next, by this key. Encoding it
    # now, from the view just computed, makes that request a lookup: the
    # close-up used to be computed twice for every placement, once here for
    # its size and once more for its pixels. Only when this process answers
    # that request too — always under run_app.py; under gunicorn another
    # worker may take it and render as before, and this encode is then spent.
    _keep_block_png(aid, key, view)
    return pipeline.to_jsonable({
        "size_px": view["size_px"],
        "px_per_mm": view["px_per_mm"],
        "flat": view["flat"],
        "seq": rec.get("geometry_seq") or 0,
        "key": key,
        "markers": lowcontrast.view_markers(ctx, geom, centre, angle),
        # Read afresh from the rings as they now sit, the same way compute()
        # will read them, so the close-up can show which way round the
        # insert is taken — and any disagreement with a kept value — before
        # anything is measured. Four values; no extra request.
        "orientation": lowcontrast.orientation_summary(
            lowcontrast.read_orientation(ctx, geom)),
    })


@app.get("/api/analyses/{aid}/lowcontrast_view.png")
def lowcontrast_view_png(aid: str, key: str = "", seq: int = 0):
    """The block on its own: straightened, flattened, windowed to itself.

    About twenty kilobytes against the nine hundred of the full render, which
    is the difference between glancing at the discs and waiting fifteen
    seconds for them on the link this is deployed over — and the reason the
    field report was that the discs were hard to see at all: re-windowing the
    whole image to hunt for objects a fraction of a percent in contrast cost a
    transfer every attempt.

    ``key`` names the picture the page wants. One this worker already holds
    under that name is sent straight away, and may be kept: the name is a
    digest of what the picture shows, so the bytes kept under it are that
    picture, and reading the whole record again to decide so would only
    repeat what the name already says. That is the ordinary case when one
    process answers both requests, because the markers the page just asked
    for kept it here. Under gunicorn another worker may have answered the
    markers, and this one then renders the picture below as it always did.

    Otherwise the picture is rendered from the record as it is now. When
    ``key`` names that, the URL names exactly these bytes and the browser may
    keep them. When it does not — a page asking about a placement that has
    since moved — the current picture is still sent, but marked not to be
    kept, so it can never be stored under the name of a placement it does not
    show. ``seq`` is accepted from pages loaded before this changed and
    otherwise ignored.
    """
    # Before any lookup: a record deleted through another worker must stop
    # being served from this one's memory, whatever name it is asked by.
    if not store.exists(aid):
        _drop_cached_image(aid)
        raise HTTPException(404, "not found")
    kept = _img_cache.get((aid, "lcview", key)) if key else None
    if kept is not None:
        return Response(kept, media_type="image/png",
                        headers={"Cache-Control": "private, max-age=86400"})
    rec = store.get(aid)
    _, centre, angle = _block_placement(rec)
    current = _block_view_key(rec, centre, angle)
    png = _img_cache.get((aid, "lcview", current))
    if png is None:
        png = _keep_block_png(aid, current, lowcontrast.block_view(
            _ctx(aid, rec), centre, angle))
    keep = "private, max-age=86400" if key == current else "no-store"
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": keep})


class CornersBody(BaseModel):
    corners_px: list[list[float]] | None = None


@app.post("/api/analyses/{aid}/register")
def re_register(aid: str, body: CornersBody):
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    _require_unsigned(rec, "re-registering the phantom")
    hint = np.asarray(body.corners_px, float) if body.corners_px else None
    try:
        reg = _do_register(aid, corners_hint=hint)
    except Exception as e:
        raise HTTPException(400, f"registration failed: {e}")
    store.audit(aid, "A", "re-registered",
                {"manual_corners": body.corners_px is not None})
    # Geometry proposals are expressed in pixels derived from the transform, so
    # a new transform invalidates them — and every undo state built on top.
    #
    # The verdict goes with them. It was left behind, so a record re-registered
    # after a "pass" kept showing pass in History and in the label counts while
    # holding no results at all — a green chip for a measurement that no longer
    # existed.
    store.update(aid, geometry=None, results=None, stage="A", geometry_seq=0,
                 status="draft")
    store.clear_geometry_history(aid)
    return _reg_payload(aid, reg)


def _profile_summary(prof: dict | None) -> dict | None:
    if not prof:
        return None
    return {"phantom": prof["phantom_key"], "updated_at": prof["updated_at"],
            "updated_by": prof.get("updated_by") or "",
            "pdef_version": prof.get("pdef_version") or "",
            "source_analysis_id": prof.get("source_analysis_id") or "",
            "n_rois": len((prof.get("layout") or {}).get("rois") or {})}


class ProposeBody(BaseModel):
    #: None means "do what this analysis did last time"; the Stage C reset
    #: buttons send an explicit true / false.
    use_profile: bool | None = None


@app.post("/api/analyses/{aid}/propose")
def propose(aid: str, body: ProposeBody | None = None, request: Request = None):
    """Re-detect every measuring point, then optionally replay the phantom's
    stored layout on top.

    The automatic proposal always runs first, even when a layout is going to be
    applied: its per-pattern detection flags are the evidence Stage B shows, and
    they are only meaningful if they came from this scan. The raw proposal is
    also pinned as undo state 0, which is what "reset to auto-detected" returns
    to — exactly, rather than by re-detecting and hoping for the same answer."""
    body = body or ProposeBody()
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    _require_unsigned(rec, "re-detecting the patterns")
    ctx = _ctx(aid, rec)
    geom = pipeline.propose_all(ctx, deadline=_deadline())
    user = _current_user(request) if request is not None else ""

    # seq 0 is the untouched automatic proposal, always.
    try:
        store.set_geometry_baseline(aid, geom, action="propose", user=user)
    except KeyError:
        raise HTTPException(404, "not found")   # deleted while proposing
    store.audit(aid, "B", "proposals generated")

    want = (body.use_profile if body.use_profile is not None
            else (rec.get("layout_source") or "") != "auto")
    prof = store.get_phantom_profile(rec.get("phantom") or "") if want else None
    applied, check, report = False, None, None
    if prof:
        check = layout_profile.layout_agrees(prof["layout"], geom)
        if check["ok"]:
            geom, report = layout_profile.apply_layout(ctx, geom, prof["layout"])
            store.replace_geometry(aid, geom, action="apply_profile", user=user,
                                   detail={"phantom": prof["phantom_key"]})
            applied = True
            store.audit(aid, "B", "stored phantom layout applied",
                        {"phantom": prof["phantom_key"], **report})
        else:
            log.warning("stored layout for phantom=%r refused on analysis=%s: %s",
                        prof["phantom_key"], aid, check["reason"])
            store.audit(aid, "B", "stored phantom layout refused",
                        {"phantom": prof["phantom_key"], **check})

    store.update(aid, stage="B", layout_source=("profile" if applied else "auto"))
    return pipeline.to_jsonable({
        "geometry": geom,
        "layout_source": "profile" if applied else "auto",
        "profile": _profile_summary(prof),
        "profile_applied": applied,
        "profile_report": report,
        "profile_check": check,
        "history": store.geometry_state(aid),
    })


def _finite_point(v):
    """A pixel coordinate pair that numpy can actually use.

    `list[float]` alone accepts [], [1.0] and [1, 2, 3]: the first two reach
    numpy and raise, giving a 500 for what is plainly a bad request, and a
    one-element list silently indexes as a coordinate of (1, 1)."""
    if v is None:
        return v
    if len(v) != 2:
        raise ValueError("expected exactly two coordinates, x and y")
    if not all(math.isfinite(c) for c in v):
        raise ValueError("coordinates must be finite")
    return v


class RoiMove(BaseModel):
    roi_id: str
    center_px: conlist(float, min_length=2, max_length=2)
    #: State of the measuring points this edit was made from. When given
    #: and no longer current, the edit is refused instead of silently
    #: overwriting someone else's correction. Optional: omitting it keeps
    #: the previous behaviour.
    expect_seq: int | None = None

    @field_validator("center_px")
    @classmethod
    def _finite(cls, v):
        return _finite_point(v)


# The lookup semantics are shared with the layout-profile writer/reader, so
# the two cannot drift apart about what counts as an ROI.
_ROI_TYPES = layout_profile.ROI_TYPES
_walk_find = layout_profile.walk_find
_walk_children = layout_profile.walk_children


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
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "analysis not found")
    _require_unsigned(rec, "moving a measuring area")
    if body.roi_id == "lowcontrast/block":
        raise HTTPException(
            400, "The low-contrast block is a rigid group — use the "
                 "lowcontrast_block endpoint so the eight circles move with it.")
    ctx = _ctx(aid, rec)
    from ..analysis.common import roi_center_mm, roi_translate_mm

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
        (old_center, new_center, node, changed), hist = store.mutate_geometry(
            aid, edit, action="roi", user=_current_user(request),
            detail={"roi": body.roi_id}, expect_seq=body.expect_seq)
    except KeyError:
        raise HTTPException(404, "analysis not found")

    store.audit(aid, "C", "roi moved",
                {"roi": body.roi_id, "from_mm": old_center, "to_mm": new_center})
    return pipeline.to_jsonable({"roi": node,
                                 "stats": _roi_stats_or_none(ctx, node),
                                 "changed": changed, "history": hist})


class RoiRotate(BaseModel):
    roi_id: str
    angle_deg: float
    #: State of the measuring points this edit was made from. When given
    #: and no longer current, the edit is refused instead of silently
    #: overwriting someone else's correction. Optional: omitting it keeps
    #: the previous behaviour.
    expect_seq: int | None = None

    @field_validator("angle_deg")
    @classmethod
    def _finite(cls, v):
        # NaN survives JSON, reaches the rotation matrix and poisons every
        # coordinate it touches — the ROI simply disappears from the overlay.
        if not math.isfinite(v):
            raise ValueError("the angle must be a finite number of degrees")
        return v


@app.post("/api/analyses/{aid}/roi_rotate")
def rotate_roi(aid: str, body: RoiRotate, request: Request):
    """Set an ROI's phantom-frame angle.

    Needed when automatic placement gets the orientation wrong on a phantom
    that differs from the definition."""
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "analysis not found")
    _require_unsigned(rec, "rotating a measuring area")
    if body.roi_id == "lowcontrast/block":
        raise HTTPException(
            400, "The low-contrast block is a rigid group — use the "
                 "lowcontrast_block endpoint so the eight circles turn with it.")
    ctx = _ctx(aid, rec)
    from ..analysis.common import roi_angle_deg, roi_rotate

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

        delta = float(body.angle_deg) - float(old_angle)
        changed = [node]
        # The companion turns BY the same amount, not TO the same angle. The
        # line-pair square deliberately sits 45 deg off its profile line, so
        # setting both to one absolute angle would swing the measuring line off
        # the pattern — and mark it hand-set, which suppresses the automatic
        # re-measurement for good.
        for comp in _walk_children(geom, body.roi_id):
            if comp.get("type") == "segment":
                comp_angle = roi_angle_deg(comp)
                if comp_angle is None:
                    continue
                fresh = roi_rotate(ctx, comp, comp_angle + delta)
                comp.clear()
                comp.update(pipeline.to_jsonable(fresh))
                comp["manually_adjusted"] = True
                changed.append(comp)
        return old_angle, node, changed

    try:
        (old_angle, node, changed), hist = store.mutate_geometry(
            aid, edit, action="roi_rotate", user=_current_user(request),
            detail={"roi": body.roi_id, "to_deg": body.angle_deg},
            expect_seq=body.expect_seq)
    except KeyError:
        raise HTTPException(404, "analysis not found")

    store.audit(aid, "C", "roi rotated",
                {"roi": body.roi_id, "from_deg": old_angle,
                 "to_deg": body.angle_deg})
    return pipeline.to_jsonable({"roi": node,
                                 "stats": _roi_stats_or_none(ctx, node),
                                 "changed": changed, "history": hist})


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
    #: State of the measuring points this edit was made from. When given
    #: and no longer current, the edit is refused instead of silently
    #: overwriting someone else's correction. Optional: omitting it keeps
    #: the previous behaviour.
    expect_seq: int | None = None

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
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "analysis not found")
    _require_unsigned(rec, "moving the low-contrast block")
    ctx = _ctx(aid, rec)
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
        (centre, angle, lcg), hist = store.mutate_geometry(
            aid, edit, action="lowcontrast_block",
            user=_current_user(request), detail={"angle_deg": body.angle_deg},
            expect_seq=body.expect_seq)
    except KeyError:
        raise HTTPException(404, "analysis not found")

    store.audit(aid, "C", "low-contrast block placed",
                {"center_mm": centre, "angle_deg": angle,
                 "by": "corners" if body.corners_px else "drag"})
    return pipeline.to_jsonable({"lowcontrast": lcg,
                                 "center_mm": centre, "angle_deg": angle,
                                 "history": hist})


class InsertOrientation(BaseModel):
    #: True when the insert is fitted a half turn from the drawing.
    flipped: bool
    #: State of the measuring points this edit was made from. When given
    #: and no longer current, the edit is refused instead of silently
    #: overwriting someone else's correction.
    expect_seq: int | None = None


@app.post("/api/analyses/{aid}/lowcontrast_orientation")
def set_lowcontrast_orientation(aid: str, body: InsertOrientation,
                                request: Request):
    """Say by hand which way round the low-contrast insert is fitted.

    For the scan the contrast order cannot decide, and for the phantom whose
    saved value turns out to be wrong. It is an edit to the measuring points
    like any other — undoable, refused on a stale state or a locked record,
    audited — and it stays on this analysis: it reaches the phantom's stored
    layout only when the operator saves the measuring points for future scans,
    and no analysis already measured is ever read again because of it.

    Nothing moves. The rings stay on the discs they are on; only the design
    level each one is read as changes, which is why this is a different thing
    from turning the block end for end."""
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "analysis not found")
    _require_unsigned(rec, "changing which way round the low-contrast insert "
                           "is read")
    ctx = _ctx(aid, rec)
    wanted = {"flipped": bool(body.flipped), "source": "user",
              "confidence": None}

    def edit(geom):
        lcg = (geom or {}).get("lowcontrast")
        if not isinstance(lcg, dict) or lcg.get("_error") \
                or not lcg.get("circles"):
            raise HTTPException(400, "the low-contrast discs were not proposed "
                                     "on this scan, so there is nothing to read "
                                     "either way round")
        before = lcg.get("orientation")
        lcg["orientation"] = dict(wanted)
        return before, lcg

    try:
        (before, lcg), hist = store.mutate_geometry(
            aid, edit, action="lowcontrast_orientation",
            user=_current_user(request), detail={"flipped": wanted["flipped"]},
            expect_seq=body.expect_seq)
    except KeyError:
        raise HTTPException(404, "analysis not found")

    store.audit(aid, "C", "low-contrast insert orientation set",
                {"from": before, "to": wanted})
    # How the discs will now be read, conflict included, so the close-up can
    # update from this answer instead of fetching its picture again.
    return pipeline.to_jsonable({
        "orientation": lcg["orientation"],
        "view": lowcontrast.orientation_summary(
            lowcontrast.read_orientation(ctx, lcg)),
        "history": hist})


@app.get("/api/analyses/{aid}/roi_stats")
def roi_stats(aid: str, roi_id: str):
    rec = store.get(aid)
    if not rec or not rec.get("geometry"):
        raise HTTPException(400, "no geometry yet")
    node = _walk_find(rec["geometry"], roi_id)
    if node is None:
        raise HTTPException(404, f"ROI {roi_id} not found")
    ctx = _ctx(aid, rec)
    return pipeline.to_jsonable({"roi": node,
                                 "stats": _roi_stats_or_none(ctx, node)})


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
    # Previewing is harmless; STORING the SID is not. This wrote it to the
    # record whatever its state, so a signed-off analysis could end up printing
    # a source-to-detector distance its field-alignment verdict was never
    # computed with. The preview still runs on a locked record — reading is
    # always allowed — using the SID already stored.
    if store.protection(rec):
        body.sid_mm = rec.get("sid_mm") or body.sid_mm
    else:
        store.update(aid, sid_mm=body.sid_mm)
    ctx = _ctx(aid)
    out = {}
    for name in body.tests:
        if name not in pipeline.TESTS:
            continue
        geom = rec["geometry"].get(name)
        if not geom or geom.get("_error"):
            # Worded as compute_all words it, so step D and step E cannot
            # disagree about the same missing geometry.
            out[name] = {"status": pipeline.NOT_MEASURED,
                         "error": (geom or {}).get(
                             "_error", "no measuring areas were placed for "
                                       "this test")}
            continue
        try:
            out[name] = pipeline._MODULES[name].compute(ctx, geom)
        except Exception as e:
            out[name] = {"status": "error", "error": str(e)}
    return pipeline.to_jsonable(out)


class FieldEdgeSet(BaseModel):
    side: str
    point_px: conlist(float, min_length=2, max_length=2)
    #: State of the measuring points this edit was made from. When given
    #: and no longer current, the edit is refused instead of silently
    #: overwriting someone else's correction. Optional: omitting it keeps
    #: the previous behaviour.
    expect_seq: int | None = None

    @field_validator("point_px")
    @classmethod
    def _finite(cls, v):
        return _finite_point(v)


@app.post("/api/analyses/{aid}/field_edge")
def set_field_edge(aid: str, body: FieldEdgeSet, request: Request):
    """Place a radiation-field edge by hand.

    Serialised like every other Stage C edit: this used to read the whole
    geometry blob, change it in memory and write it back, so an ROI drag
    committed in between was silently thrown away."""
    side_geom = {"top": ((0, 1), (0, -1)), "right": ((1, 0), (-1, 0)),
                 "bottom": ((0, -1), (0, 1)), "left": ((-1, 0), (1, 0))}
    if body.side not in side_geom:
        raise HTTPException(400, "bad side")
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "analysis not found")
    _require_unsigned(rec, "moving a field edge")
    ctx = _ctx(aid, rec)
    (ex, ey), (nx, ny) = side_geom[body.side]
    S2 = pdef.side_mm / 2.0
    edge_pt = np.array([ex * S2, ey * S2], float)
    outward = -np.array([nx, ny], float)
    p_mm = np.asarray(ctx.T.px_to_mm(body.point_px), float)
    offset = float(np.dot(p_mm - edge_pt, outward))
    entry = pipeline.to_jsonable({
        "side": body.side, "detected": True, "manual": True,
        "offset_from_edge_mm": offset,
        "edge_pt_px": np.asarray(
            ctx.T.mm_to_px(edge_pt + outward * offset)).tolist(),
    })

    def edit(geom):
        if not geom:
            raise HTTPException(400, "no geometry yet")
        gg = geom.get("geometry")
        # propose_all stores {"_error": ...} for a test that raised, so the
        # field_edges dict is not guaranteed to exist.
        if not isinstance(gg, dict) or not isinstance(gg.get("field_edges"), dict):
            raise HTTPException(
                400, "this scan has no field-edge geometry to correct — the "
                     "geometry test did not propose successfully")
        gg["field_edges"][body.side] = entry

    try:
        _, hist = store.mutate_geometry(
            aid, edit, action="field_edge", user=_current_user(request),
            detail={"side": body.side, "offset_mm": offset},
            expect_seq=body.expect_seq)
    except KeyError:
        raise HTTPException(404, "analysis not found")
    store.audit(aid, "C", "field edge set manually",
                {"side": body.side, "offset_mm": offset})
    return {**entry, "history": hist}


# ------------------------------------------- measuring-point undo / redo / reset

def _layout_source_of(geometry) -> str:
    """What a geometry state's measuring points are actually based on.

    Undo and redo can step across the point where a stored layout was applied,
    so the column cannot simply be left as it was — it would then say
    "profile" over a state that is pure detection, or the reverse, and the
    Stage C banner and the next propose would both act on the lie."""
    for _, node in layout_profile.walk_rois(geometry or {}):
        if node.get("from_profile"):
            return "profile"
    return "auto"


@app.post("/api/analyses/{aid}/geometry/undo")
def geometry_undo(aid: str, request: Request):
    """Step the measuring points back one edit.

    Server-side rather than in the browser, because the browser resyncs from
    the server whenever a request fails or the analysis is reopened — a stack
    kept in the page would vanish exactly when it was most needed."""
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    _require_unsigned(rec, "undoing a measuring-point change")
    try:
        out = store.undo_geometry(aid)
    except LookupError as e:
        raise HTTPException(409, str(e))
    store.audit(aid, "C", "undo", {"to_seq": out["seq"]})
    source = _layout_source_of(out["geometry"])
    store.update(aid, layout_source=source)
    return pipeline.to_jsonable({"geometry": out.pop("geometry"),
                                 "history": out, "layout_source": source})


@app.post("/api/analyses/{aid}/geometry/redo")
def geometry_redo(aid: str, request: Request):
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    _require_unsigned(rec, "redoing a measuring-point change")
    try:
        out = store.redo_geometry(aid)
    except LookupError as e:
        raise HTTPException(409, str(e))
    store.audit(aid, "C", "redo", {"to_seq": out["seq"]})
    source = _layout_source_of(out["geometry"])
    store.update(aid, layout_source=source)
    return pipeline.to_jsonable({"geometry": out.pop("geometry"),
                                 "history": out, "layout_source": source})


class GeometryReset(BaseModel):
    #: "auto"    — back to this scan's untouched automatic proposal
    #: "profile" — back to the stored layout of this phantom
    to: str = "auto"


@app.post("/api/analyses/{aid}/geometry/reset")
def geometry_reset(aid: str, body: GeometryReset, request: Request):
    """Discard the manual corrections and start from a known layout again.

    A reset is recorded as an ordinary edit rather than as a rewind, so it is
    itself undoable: pressing it by mistake never destroys an afternoon's work.
    "auto" restores the exact snapshot taken when the patterns were proposed —
    not a re-detection, which could legitimately land somewhere else."""
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    _require_unsigned(rec, "resetting the measuring points")
    user = _current_user(request)

    if body.to == "auto":
        base = store.geometry_at(aid, 0)
        if base is None:
            raise HTTPException(
                409, "there is no automatic proposal to go back to — confirm "
                     "the registration in Stage A to generate one")
        try:
            hist = store.replace_geometry(aid, base, action="reset_auto",
                                          user=user)
        except KeyError:
            raise HTTPException(404, "not found")
        store.update(aid, layout_source="auto")
        store.audit(aid, "C", "measuring points reset to auto-detected")
        return pipeline.to_jsonable({"geometry": base, "history": hist,
                                     "layout_source": "auto"})

    if body.to != "profile":
        raise HTTPException(400, "reset target must be 'auto' or 'profile'")

    prof = store.get_phantom_profile(rec.get("phantom") or "")
    if not prof:
        raise HTTPException(
            404, "no measuring-point layout is stored for this phantom yet")
    base = store.geometry_at(aid, 0)
    if base is None:
        raise HTTPException(409, "there is no automatic proposal to build on")
    check = layout_profile.layout_agrees(prof["layout"], base)
    if not check["ok"]:
        raise HTTPException(409, check["reason"])
    ctx = _ctx(aid, rec)
    geom, report = layout_profile.apply_layout(ctx, base, prof["layout"])
    hist = store.replace_geometry(aid, geom, action="reset_profile", user=user,
                                  detail={"phantom": prof["phantom_key"]})
    store.update(aid, layout_source="profile")
    store.audit(aid, "C", "measuring points reset to the stored phantom layout",
                {"phantom": prof["phantom_key"], **report})
    return pipeline.to_jsonable({
        "geometry": geom, "history": hist, "layout_source": "profile",
        "profile": _profile_summary(prof), "profile_report": report})


def _insert_orientation_to_store(aid: str, rec: dict, label: str):
    """The insert orientation a layout saved from this analysis should carry.

    Read from the confirmed rings the way compute() will read them, not taken
    from the value noted when the patterns were proposed: the block may have
    been moved since, and that note was read under the old rings. When the
    image cannot be read here — a record whose stored file or registration is
    gone — the geometry's own note stands in, because the layout itself does
    not need the image and must still be storable."""
    lcg = (rec.get("geometry") or {}).get("lowcontrast")
    if not isinstance(lcg, dict) or lcg.get("_error") \
            or not lcg.get("circles"):
        return None
    try:
        reading = lowcontrast.read_orientation(_ctx(aid, rec), lcg)
    except HTTPException:
        reading = lcg.get("orientation")
    prev = store.get_phantom_profile(label)
    return layout_profile.insert_to_store(reading, (prev or {}).get("layout"))


class StageConfirm(BaseModel):
    stage: str
    note: str | None = None
    #: Stage C only: whether these measuring points become the phantom's
    #: stored layout. The page always says, from a checkbox the operator can
    #: see. None — a page cached from before the question was asked — gets the
    #: page's own default (_layout_save_default), so an old page can still
    #: store a phantom's FIRST layout but can no longer replace one that
    #: another analysis stored.
    save_profile: bool | None = None
    #: Stage C only, and only for a scan that failed the image-quality check
    #: whose measuring points are to be stored anyway: the administrator's
    #: password and a written reason. Ignored when the check passed.
    admin_password: str = ""
    reason: str = ""


def _layout_save_default(aid: str, rec: dict) -> bool:
    """Whether confirming step C stores the layout when nobody said.

    Yes while the phantom has no stored layout, or the one it has came from
    this very analysis — nudging a mark and confirming again must still
    update it. No once ANOTHER analysis has set the phantom's points: the
    field audit log shows one phantom's layout rewritten six times in twelve
    minutes, three of those from exposures nothing could be measured in, each
    one silently becoming where the next operator started. Replacing a layout
    somebody else confirmed is a decision, so it has to be asked for.

    The page ticks its checkbox by exactly this rule; this is the same rule
    for a request that does not carry the answer."""
    prof = store.get_phantom_profile(rec.get("phantom") or "")
    return prof is None or (prof.get("source_analysis_id") or "") == aid


@app.post("/api/analyses/{aid}/confirm")
def confirm_stage(aid: str, body: StageConfirm, request: Request):
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    order = ["A", "B", "C", "D", "E", "F"]
    if body.stage not in order:
        raise HTTPException(400, "bad stage")
    want_layout, override = False, None
    # Confirming step C writes the phantom's SHARED measuring points, which is
    # an edit with consequences beyond this record — on a locked analysis it
    # let a signed-off scan quietly redefine where every later scan starts.
    if body.stage == "C":
        _require_unsigned(rec, "storing the measuring points for this phantom")
        want_layout = (body.save_profile if body.save_profile is not None
                       else _layout_save_default(aid, rec))
        # An administrator's override is checked before anything is written,
        # so a mistyped password leaves the step exactly where it was and the
        # panel that asked for it can simply ask again.
        if want_layout:
            override = _quality_override(request, rec, aid, "phantom_profile",
                                         body.admin_password, body.reason)
    nxt = order[min(order.index(body.stage) + 1, len(order) - 1)]
    store.update(aid, stage=nxt)
    store.audit(aid, body.stage, "confirmed", {"note": body.note})

    # `save_profile` says what was decided, which for a request that did not
    # say is the only way its sender can find out.
    out = {"stage": nxt, "profile_saved": False, "profile": None,
           "profile_error": None, "save_profile": want_layout}
    # Confirming Stage C is the moment the operator says "these measuring
    # points are right for this phantom" — so that is when the layout becomes
    # the phantom's stored default, if they choose. Saving on every drag
    # instead would let a half-finished correction become the default for
    # everyone.
    if body.stage != "C" or not want_layout:
        return out
    label = store.profile_key(rec.get("phantom") or "")
    if not label:
        out["profile_error"] = ("no phantom is named on this analysis, so the "
                                "measuring points could not be stored for "
                                "future scans of it")
        return out
    if not rec.get("geometry"):
        out["profile_error"] = "there is no confirmed geometry to store"
        return out
    refused = _reference_use_refused(
        rec, "set the measuring points for future scans of this phantom")
    if refused and override is None:
        # The analysis itself goes on; only the shared layout is protected.
        out["profile_error"] = refused
        out["profile_blocked_by_quality"] = True
        out["override_available"] = cfg.deletion_enabled
        store.audit(aid, "C", "phantom layout refused",
                    {"phantom": store.profile_key(rec.get("phantom") or ""),
                     "reason": "acquisition quality"})
        audit("phantom_profile", user=_current_user(request),
              client=_client_key(request), analysis=aid,
              phantom=store.profile_key(rec.get("phantom") or ""),
              outcome="refused", reason="acquisition quality",
              failed=(rec.get("quality") or {}).get("failed"))
        log.info("refused to store layout from analysis=%s: quality %s",
                 aid, (rec.get("quality") or {}).get("failed"))
        return out
    try:
        reg = _reg(aid, rec).summary()
    except HTTPException:
        reg = {}
    insert = _insert_orientation_to_store(aid, rec, label)
    layout = layout_profile.extract_layout(
        rec["geometry"], pdef_name=pdef.name, pdef_version=pdef.version,
        algo_version=ALGO_VERSION, registration=pipeline.to_jsonable(reg),
        lowcontrast_insert=insert)
    saved = store.save_phantom_profile(
        label, layout, pdef_version=pdef.version, algo_version=ALGO_VERSION,
        source_analysis_id=aid, updated_by=_current_user(request),
        # Only an explicit yes may replace what another analysis stored.
        replace_others=body.save_profile is not None)
    if saved is None:
        # A request that did not say, beaten to it by another analysis of the
        # same phantom between reading the default and taking the lock. The
        # layout that got there first stands, as it would have if this
        # request had arrived a moment later.
        out["save_profile"] = False
        log.info("left the stored layout for phantom=%r alone: analysis=%s "
                 "did not ask to replace it", label, aid)
        return out
    store.audit(aid, "C", "phantom layout stored",
                {"phantom": label, "rois": saved["n_rois"],
                 "insert_turned": (insert or {}).get("flipped"),
                 **({"quality_override": override} if override else {})})
    audit("phantom_profile", user=_current_user(request),
          client=_client_key(request), analysis=aid, phantom=label,
          outcome="saved", rois=saved["n_rois"],
          **({"override": True, "reason": override["reason"],
              "failed": override["failed"]} if override else {}))
    log.info("stored measuring-point layout for phantom=%r from analysis=%s "
             "(%d ROIs)%s", label, aid, saved["n_rois"],
             " over a failed quality check, by administrator override"
             if override else "")
    out["profile_saved"] = True
    # None when nothing was decided about the insert, so the page can say
    # what later scans of this phantom will be read with — or that they will
    # still decide for themselves.
    out["profile"] = {**saved, "insert_turned": (insert or {}).get("flipped"),
                      "quality_override": bool(override)}
    return out


class ComputeBody(BaseModel):
    sid_mm: float = 1000.0


@app.post("/api/analyses/{aid}/compute")
def compute(aid: str, body: ComputeBody, request: Request):
    rec = store.get(aid)
    if not rec or not rec.get("geometry"):
        raise HTTPException(400, "no confirmed geometry")
    _require_unsigned(rec, "recomputing the results")
    store.update(aid, sid_mm=body.sid_mm)
    # Keep the in-hand record in step with what was just written: building the
    # context from the stale copy analysed with the PREVIOUS SID, so the
    # field-alignment %-of-SID verdict disagreed with the SID shown everywhere.
    rec["sid_mm"] = body.sid_mm
    ctx = _ctx(aid, rec)
    results = pipeline.compute_all(ctx, rec["geometry"], deadline=_deadline())
    status = pipeline.overall_status(results)
    timed_out = [t for t in pipeline.TESTS
                 if (results.get(t) or {}).get("timed_out")]
    # Stamp what actually produced these numbers. The record kept the versions
    # it was uploaded under, so a re-measurement after an upgrade left results
    # from the new code labelled with the old version — and `reanalyze` skips
    # records whose stamp already matches, so those were quietly excluded from
    # the very sweep meant to bring them up to date.
    store.update(aid, results=results, status=status, stage="F",
                 algo_version=ALGO_VERSION, pdef_version=pdef.version)
    store.audit(aid, "E", "computed", {"overall": status,
                                       "sid_mm": body.sid_mm,
                                       **({"timed_out": timed_out}
                                          if timed_out else {})})
    if timed_out:
        log.warning("analysis=%s exceeded the %s s time limit; %s not measured",
                    aid, cfg.analysis_timeout_s, ", ".join(timed_out))
    audit("compute", user=_current_user(request), client=_client_key(request),
          analysis=aid, outcome=status, sid_mm=body.sid_mm)
    log.info("computed analysis=%s overall=%s", aid, status)
    baseline = store.baseline_for(rec["signature"], rec.get("phantom", ""),
                                  exclude_id=aid)
    return pipeline.to_jsonable({
        "results": results, "overall": status,
        "verdict_notes": pipeline.verdict_notes(results),
        "baseline": ({"id": baseline["id"],
                      "phantom": baseline.get("phantom", ""),
                      "acquired_at": baseline.get("acquired_at", ""),
                      "created_at": baseline.get("created_at", ""),
                      "rows": flatten_results(baseline["results"])}
                     if baseline and baseline.get("results") else None),
    })


class FinalizeBody(BaseModel):
    #: None leaves the baseline flag alone. Finalising is not the only way to
    #: set it any more, so re-finalising must not silently clear it.
    baseline: bool | None = None


def _reference_use_refused(rec: dict, what: str) -> str | None:
    """Why this exposure may not become reference data, or None if it may.

    A poor exposure can still be analysed — the operator may need the numbers,
    or want to show what went wrong — but it must not become the standard later
    scans are judged against, nor supply the measuring points the next operator
    starts from. The field audit log records one phantom's shared layout being
    rewritten three times in twelve minutes from exposures in which nothing
    could be measured; everyone who analysed that phantom afterwards inherited
    marks taken from a white slab.
    """
    if not quality.blocks_reference_use(rec.get("quality")):
        return None
    q = rec.get("quality") or {}
    return (f"This exposure did not pass the image-quality check, so it cannot "
            f"{what}. {q.get('summary', '')} "
            f"(failed: {', '.join(q.get('failed') or [])})").strip()


class QualityRefusal(Exception):
    """A failed exposure refused as reference data.

    Its own exception rather than a plain 409, so the page can tell this
    refusal from every other one and put the failed checks — and the one way
    past them, the administrator's override — in front of the person deciding,
    instead of parsing an English sentence to find out what happened."""

    def __init__(self, message: str, rec: dict):
        super().__init__(message)
        self.message = message
        q = rec.get("quality") or {}
        self.summary = q.get("summary") or ""
        self.failed_checks = [
            {"id": c.get("id"), "label": c.get("label"),
             "detail": c.get("detail"), "ok": False}
            for c in (q.get("checks") or []) if not c.get("ok")]


@app.exception_handler(QualityRefusal)
async def _quality_refusal(request: Request, exc: QualityRefusal):
    return JSONResponse(status_code=409, content=pipeline.to_jsonable({
        "detail": exc.message,
        "quality_refused": True,
        "summary": exc.summary,
        "failed_checks": exc.failed_checks,
        # Whether asking an administrator is even possible here, so the page
        # does not offer a password field nobody can fill in.
        "override_available": cfg.deletion_enabled,
    }))


def _quality_override(request: Request, rec: dict, aid: str, event: str,
                      password: str, reason: str) -> dict | None:
    """An administrator's decision to use a failed exposure as reference data.

    The gate withholds trust from an exposure nothing could be measured in,
    and that is right almost every time. Almost: a site may have one
    exposure this week, looked at by someone who knows what they are looking
    at. That is let through — but as an administrator's decision, with a
    written reason, recorded beside the checks it overrode.

    None when the exposure passed (there is nothing to override, and a
    password sent anyway is ignored rather than checked), or when no override
    was asked for, in which case the ordinary refusal applies. Otherwise
    _require_admin decides, with its usual order and throttle, and refuses
    outright where no administrator password is configured — the same rule as
    delete."""
    if not quality.blocks_reference_use(rec.get("quality")):
        return None
    if not (password or (reason or "").strip()):
        return None
    why = _require_admin(request, password, event, aid, reason=reason)
    return {"reason": why,
            "failed": list((rec.get("quality") or {}).get("failed") or [])}


def _set_baseline(aid: str, rec: dict, value: bool, request: Request,
                  override: dict | None = None) -> dict:
    if value:
        refused = _reference_use_refused(rec, "become the reference scan")
        if refused and override is None:
            raise QualityRefusal(refused, rec)
    if value and rec.get("reduced_precision"):
        raise HTTPException(
            400, "A reduced-precision analysis (a plain image, 8-bit and "
                 "without acquisition metadata) cannot be a reference.")
    if value and not rec.get("results"):
        raise HTTPException(
            400, "This analysis has no results yet, so there is nothing for "
                 "later scans to be compared against.")
    try:
        out = store.set_baseline(aid, value)
    except KeyError:
        raise HTTPException(404, "not found")   # deleted while we were editing
    if value:
        # A scan every later scan is judged against is a decision, not a
        # draft. Finalising it here means the protections that follow from
        # being finished apply to the reference as well, without the operator
        # having to remember two separate actions.
        store.set_finalized(aid, _current_user(request))
    # An overridden refusal is written down beside the checks it overrode, so
    # anyone reading the record later sees that the reference was a failed
    # exposure somebody chose to trust, and why.
    overridden = bool(value and override)
    store.audit(aid, "F", "baseline set" if value else "baseline cleared",
                {"phantom": out["phantom"], "replaced": out["replaced"],
                 **({"quality_override": override} if overridden else {})})
    audit("baseline", user=_current_user(request), client=_client_key(request),
          analysis=aid, outcome="set" if value else "cleared",
          phantom=out["phantom"], signature=out["signature"],
          replaced=out["replaced"],
          **({"override": True, "reason": override["reason"],
              "failed": override["failed"]} if overridden else {}))
    log.info("baseline %s for phantom=%r protocol=%r: analysis=%s%s%s",
             "set" if value else "cleared", out["phantom"], out["signature"],
             aid, f" (replacing {out['replaced']})" if out["replaced"] else "",
             " over a failed quality check, by administrator override"
             if overridden else "")
    return {**out, "quality_override": overridden}


class BaselineBody(BaseModel):
    baseline: bool = True
    #: Only to make an exposure that failed the image-quality check the
    #: reference anyway: the administrator's password and a written reason.
    #: Ignored for an exposure that passed, and for clearing a reference.
    admin_password: str = ""
    reason: str = ""


@app.post("/api/analyses/{aid}/baseline")
def set_baseline(aid: str, body: BaselineBody, request: Request):
    """Make this analysis its phantom's reference, or stop it being one.

    Scoped to the phantom AND the protocol: two phantoms can differ by design
    and both be valid, so each needs its own reference, while comparing across
    protocols is meaningless whatever the phantom."""
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    override = (_quality_override(request, rec, aid, "baseline",
                                  body.admin_password, body.reason)
                if body.baseline else None)
    out = _set_baseline(aid, rec, bool(body.baseline), request,
                        override=override)
    return {"ok": True, **out}


@app.get("/api/baselines")
def list_baselines():
    """Every current reference — one per phantom per protocol."""
    return {"baselines": store.baselines()}


@app.post("/api/analyses/{aid}/finalize")
def finalize(aid: str, body: FinalizeBody, request: Request):
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    # Finalising a record with nothing measured used to succeed and leave a
    # record claiming to be complete with no numbers in it.
    if not rec.get("results"):
        raise HTTPException(
            400, "This analysis has no results yet, so there is nothing to "
                 "finalise. Compute the results first.")
    store.update(aid, stage="F", status=rec.get("status") or "complete")
    when = store.set_finalized(aid, _current_user(request))
    changed = None
    if body.baseline is not None:
        changed = _set_baseline(aid, rec, body.baseline, request)
    store.audit(aid, "F", "finalized",
                {"baseline": body.baseline, "at": when})
    audit("finalize", user=_current_user(request), client=_client_key(request),
          analysis=aid, baseline=body.baseline, finalized_at=when,
          site=rec.get("site"), phantom=rec.get("phantom"))
    return {"ok": True, "baseline": changed, "finalized_at": when}


class DeleteBody(BaseModel):
    admin_password: str = ""
    reason: str = ""


def _require_admin(request: Request, password: str, event: str, aid: str = "-",
                   *, reason: str | None = None) -> str:
    """The administrator gate, in one place.

    Three endpoints grew their own copy of this sequence and a fourth was about
    to. The order matters and is easy to get subtly wrong: the password is
    checked BEFORE the reason, so a missing reason cannot be used to find out
    whether a password was right; every outcome reaches the audit log; and a
    wrong password counts towards the shared throttle, which is shared across
    workers so an attacker does not get one allowance per process.

    Returns the trimmed reason when one was required.
    """
    user, client = _current_user(request), _client_key(request)
    if not cfg.deletion_enabled:
        audit(event, user=user, client=client, analysis=aid, outcome="refused",
              refusal="no administrator password configured")
        raise HTTPException(
            403, "This needs an administrator password, and none is configured "
                 "on this installation. Set PHANTOMQA_ADMIN_PASSWORD_HASH in "
                 ".env (python -m phantom_qa.manage set-admin-password).")

    wait = admin_throttle.locked_for(client)
    if wait > 0:
        audit(event, user=user, client=client, analysis=aid,
              outcome="throttled")
        raise HTTPException(429, f"Too many failed admin attempts. Try again "
                                 f"in {wait // 60 + 1} min.")

    if not verify_password(password, cfg.admin_password_hash):
        admin_throttle.record_failure(client)
        audit(event, user=user, client=client, analysis=aid, outcome="denied",
              refusal="bad admin password")
        log.warning("%s denied (bad admin password) analysis=%s client=%s",
                    event, aid, client)
        raise HTTPException(401, "Incorrect administrator password.")
    admin_throttle.reset(client)

    if reason is None:
        return ""
    trimmed = (reason or "").strip()
    if len(trimmed) < MIN_DELETE_REASON_CHARS:
        audit(event, user=user, client=client, analysis=aid, outcome="refused",
              refusal="no reason given")
        raise HTTPException(
            400, f"Give a reason of at least {MIN_DELETE_REASON_CHARS} "
                 f"characters — it is recorded in the audit log.")
    return trimmed[:MAX_DELETE_REASON_CHARS]


class RerunBody(BaseModel):
    #: Where to pick the analysis up again.
    #:   registration — re-detect the phantom, then everything after it
    #:   points       — keep the registration, re-check the measuring points
    #:   results      — keep both, recompute the numbers
    start: str = "registration"
    admin_password: str = ""
    reason: str = ""


@app.post("/api/analyses/{aid}/rerun")
def rerun(aid: str, body: RerunBody, request: Request):
    """Analyse a stored scan again, in place.

    The server has always been able to do this from the file it keeps — no
    upload is needed — but nothing in the interface could reach it. A completed
    record opened on the last step, which has no way back, so the only route to
    fresh numbers was to upload the same 7.5 MB again and press "analyse anyway",
    producing a second record that double-counts in every trend.

    Before the record is finalised this is an ordinary edit and needs nothing.
    Afterwards it rewrites numbers somebody declared done — and possibly signed
    — so it takes the administrator password and a written reason, like every
    other decision of that weight. What it never needs is the file again.
    """
    user, client = _current_user(request), _client_key(request)
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    if body.start not in ("registration", "points", "results"):
        raise HTTPException(400, "start must be registration, points or results")
    if body.start != "registration" and not rec.get("geometry"):
        raise HTTPException(
            400, "This analysis has no measuring points yet, so there is "
                 "nothing to keep — start again from registration.")

    protection = store.protection(rec)
    if protection:
        _require_admin(request, body.admin_password, "rerun", aid,
                       reason=body.reason)

    # New numbers must not be computed from a file that is no longer the one
    # the old numbers came from — the same rule the command line applies.
    integrity = store.verify_integrity(aid)
    if integrity["status"] != "ok":
        raise HTTPException(409, (
            f"The stored scan file no longer matches the fingerprint recorded "
            f"when it was analysed ({integrity['status']}), so it cannot be "
            f"re-analysed. Check the source file before trusting this record."))

    # Registration is seconds of CPU and must not run inside the write lock.
    new_reg = None
    if body.start == "registration":
        reg = pipeline.run_stage_a(_scan(aid), pdef)
        new_reg = pipeline.to_jsonable({
            "transform": reg.transform.to_dict(),
            "corners_px": reg.corners_px,
            "coarse_angle_deg": reg.coarse_angle_deg,
            "score": reg.score, "candidate_scores": reg.candidate_scores,
            "landmarks": reg.landmarks,
            "residual_rms_mm": reg.residual_rms_mm,
        })

    try:
        out = store.begin_rerun(
            aid, user=user, reason=body.reason, mode=body.start,
            new_reg=new_reg, keep_geometry=body.start != "registration")
    except KeyError:
        raise HTTPException(404, "not found")
    _forget(aid)

    if body.start == "registration":
        # Re-assess the exposure against the fresh fit, exactly as an upload
        # would — manual corners can rescue a scan the automatic fit mangled,
        # and the verdict should follow the registration it describes.
        verdict = quality.assess(_scan(aid).pixels, reg)
        store.update(aid, quality=verdict, quality_verdict=verdict["verdict"])

    store.audit(aid, "A", "re-run started", {
        "start": body.start, "revision": out["revision"],
        "reason": body.reason, "was_protected": protection})
    audit("rerun", user=user, client=client, analysis=aid, outcome="ok",
          start=body.start, revision=out["revision"], reason=body.reason,
          was_protected=protection, site=rec.get("site"),
          phantom=rec.get("phantom"))
    log.info("re-run analysis=%s from %s (revision %s kept, was %s)", aid,
             body.start, out["revision"], protection or "open")
    return {"ok": True, "stage": out["stage"], "revision": out["revision"],
            "was_baseline": bool(rec.get("is_baseline")),
            "withdrew_ruling": rec.get("validation_status") or "",
            "registration": _reg_payload(aid, reg) if new_reg else None}


@app.post("/api/analyses/{aid}/rerun/cancel")
def rerun_cancel(aid: str, request: Request):
    """Put the previous results back and forget the re-run.

    A re-run interrupted by a dropped connection leaves a record with no
    numbers, missing from every trend — and if it was the reference, its
    phantom comparing against nothing. This is the way back, and it needs no
    password: it restores a state that was already approved rather than
    creating a new one.
    """
    user, client = _current_user(request), _client_key(request)
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    if rec.get("results"):
        raise HTTPException(
            409, "This analysis has new results already. Cancelling now would "
                 "throw those away — re-run it again if they are wrong.")
    try:
        out = store.restore_revision(aid)
    except KeyError:
        raise HTTPException(409, "There is no previous state to restore.")
    _forget(aid)
    store.audit(aid, "F", "re-run cancelled", out)
    audit("rerun", user=user, client=client, analysis=aid, outcome="cancelled",
          restored=out["restored"], site=rec.get("site"),
          phantom=rec.get("phantom"))
    return {"ok": True, **out}


@app.get("/api/analyses/{aid}/revisions")
def revisions(aid: str):
    """Metadata only — the snapshots themselves never travel."""
    if not store.exists(aid):
        raise HTTPException(404, "not found")
    return {"revisions": store.list_revisions(aid)}


class DiscardBody(BaseModel):
    #: Guards against a bare POST reaching this by accident. Not a password:
    #: the operator is throwing away their own unfinished work, and requiring
    #: a credential for that is exactly what drove the field team to delete
    #: and re-upload the same file four times in seventy minutes.
    confirm: bool = False


@app.post("/api/analyses/{aid}/discard")
def discard_analysis(aid: str, body: DiscardBody, request: Request):
    """Throw away an analysis that was never finished. Confirmation only.

    The everyday mistake — the wrong phantom typed in, a mis-set exposure, an
    attempt abandoned half way — needed the administrator password to clear,
    and on an installation with no administrator password configured it could
    not be cleared at all. The field audit log shows what that cost: the same
    file deleted and re-uploaded four times inside seventy minutes, each cycle
    a multi-megabyte transfer, with reasons like "Mark alighment correction"
    and "Failed manual point detection".

    Deliberately NOT gated, because nothing here has been decided yet. The
    moment anything has been — finalised, ruled on, or made a reference —
    this refuses and the administrator-gated delete applies unchanged.
    """
    user, client = _current_user(request), _client_key(request)
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    if not body.confirm:
        raise HTTPException(400, "Discarding needs an explicit confirmation.")

    try:
        out = store.delete(aid, only_if_unprotected=True)
    except ProtectedAnalysis as blocked:
        why = ", ".join(store.PROTECTION_REASONS.get(r, r)
                        for r in blocked.reasons)
        audit("delete", mode="discard", user=user, client=client, analysis=aid,
              outcome="refused", refusal=",".join(blocked.reasons))
        raise HTTPException(409, (
            f"This analysis cannot be discarded because {why}. Removing it "
            f"destroys a decision someone recorded, so it needs the "
            f"administrator password."))

    # Same event name as the administrator delete, with the mode alongside, so
    # one `grep event=delete` still shows everything that ever removed data.
    audit("delete", mode="discard", user=user, client=client, analysis=aid,
          outcome="ok", stage=rec.get("stage"), status=rec.get("status"),
          had_results=bool(rec.get("results")), site=rec.get("site"),
          phantom=rec.get("phantom"), source=rec.get("source_name"),
          sha256=rec.get("sha256"), created_at=rec.get("created_at"),
          acquired_at=rec.get("acquired_at"),
          layout_deleted=out["profile_deleted"],
          layout_restored=out["profile_restored"])
    log.info("discarded unfinished analysis=%s source=%r phantom=%r "
             "(layout %s)", aid, rec.get("source_name"), rec.get("phantom"),
             "restored" if out["profile_restored"] else
             "deleted" if out["profile_deleted"] else "untouched")
    _forget(aid)
    return {"ok": True, "phantom": out["phantom"],
            "layout_deleted": out["profile_deleted"],
            "layout_restored": out["profile_restored"]}


@app.get("/api/analyses/{aid}/delete_impact")
def delete_impact(aid: str):
    """What removing this record would take with it, and how.

    Opening the delete panel used to fetch the entire record — measured at
    195 kB on a real analysis — to read a hundred bytes of it. On a field link
    that is three seconds before the dialog appears."""
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    blocked = store.protection(rec)
    label = store.profile_key(rec.get("phantom") or "")
    n_same = store.count_for_phantom(rec.get("phantom") or "")
    profile = store.get_phantom_profile(rec.get("phantom") or "")
    return {
        "mode": ("admin" if blocked and cfg.deletion_enabled else
                 "disabled" if blocked else "confirm"),
        "protection": blocked,
        "protection_reasons": [store.PROTECTION_REASONS.get(r, r)
                               for r in blocked],
        "phantom": label,
        "analyses_for_phantom": n_same,
        "layout_would_be_deleted": bool(profile and n_same <= 1),
        "min_reason_chars": MIN_DELETE_REASON_CHARS,
        "source_name": rec.get("source_name"),
        "created_at": rec.get("created_at"),
        "has_results": bool(rec.get("results")),
    }


@app.post("/api/analyses/{aid}/delete")
def delete_analysis(aid: str, body: DeleteBody, request: Request):
    """Delete an analysis, its stored source file and its edit history.

    Deliberately hard to do by accident on a shared installation:
      * a separate ADMIN password is required — not the everyday login;
      * a written reason of at least a few characters must be given, and is
        recorded in the audit log next to who did it and from where;
      * every attempt, successful or not, goes to the audit log.
    With no admin password configured the endpoint refuses outright.

    Typing the analysis id back used to be required as well. It was dropped
    because it protected nothing an operator could not satisfy by copy-paste,
    while the failure it produced was a silent 400 the browser showed for six
    seconds — after which a re-upload of the same file offered to reopen the
    record the operator believed they had deleted."""
    user = _current_user(request)
    client = _client_key(request)
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")

    if not cfg.deletion_enabled:
        audit("delete", user=user, client=client, analysis=aid,
              outcome="refused", refusal="deletion disabled")
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

    if not verify_password(body.admin_password, cfg.admin_password_hash):
        admin_throttle.record_failure(client)
        audit("delete", user=user, client=client, analysis=aid,
              outcome="denied", refusal="bad admin password")
        log.warning("delete denied (bad admin password) analysis=%s client=%s "
                    "user=%s", aid, client, user)
        raise HTTPException(401, "Incorrect administrator password.")

    # After the password, so a missing reason cannot be used to probe whether a
    # password was right, and so a wrong password still answers 401.
    reason = (body.reason or "").strip()
    if len(reason) < MIN_DELETE_REASON_CHARS:
        audit("delete", user=user, client=client, analysis=aid,
              outcome="refused", refusal="no reason given")
        raise HTTPException(
            400, f"Give a reason of at least {MIN_DELETE_REASON_CHARS} "
                 f"characters. It is recorded in the audit log and is the only "
                 f"record of why this data was destroyed.")
    reason = reason[:MAX_DELETE_REASON_CHARS]

    admin_throttle.reset(client)
    out = store.delete(aid)
    audit("delete", user=user, client=client, analysis=aid, outcome="ok",
          site=rec.get("site"), phantom=rec.get("phantom"),
          source=rec.get("source_name"), sha256=rec.get("sha256"),
          created_at=rec.get("created_at"), acquired_at=rec.get("acquired_at"),
          layout_deleted=out["profile_deleted"],
          layout_restored=out["profile_restored"], reason=reason)
    log.warning("DELETED analysis=%s site=%r phantom=%r by user=%s client=%s "
                "layout_deleted=%s", aid, rec.get("site"), rec.get("phantom"),
                user, client, out["profile_deleted"])
    _forget(aid)
    return {"ok": True, "phantom": out["phantom"],
            "layout_deleted": out["profile_deleted"],
            "layout_restored": out["profile_restored"]}


@app.get("/api/deletion_policy")
def deletion_policy():
    return {"enabled": cfg.deletion_enabled,
            "requires_admin_password": True,
            "requires_reason": True,
            "min_reason_chars": MIN_DELETE_REASON_CHARS}


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
              outcome="refused", refusal="no administrator password configured")
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
              outcome="denied", refusal="bad admin password")
        log.warning("validation denied (bad admin password) analysis=%s "
                    "client=%s", aid, client)
        raise HTTPException(401, "Incorrect administrator password.")
    admin_throttle.reset(client)

    try:
        applied = store.set_validation(aid, body.status, body.validated_by,
                                       body.comment)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # A ruling is a stronger statement than "finished", so it implies it. The
    # reverse is not true, and withdrawing a ruling does not un-finalise:
    # someone still declared the numbers done, and the re-run path is where
    # that gets undone deliberately.
    if applied["validation_status"]:
        store.set_finalized(aid, user)

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
                      signature: str = "", validation: str = "") -> list[dict]:
    """Records for an explicit id list, or for a label filter.

    Every filter the History tab offers has to be honoured here too. The
    validation filter was not, so exporting "the analyses nobody has signed
    off" silently produced a file covering all of them — the sort of thing that
    is only noticed once the wrong list has been acted on."""
    if ids:
        wanted = [a.strip() for a in ids.split(",") if a.strip()]
        return store.get_slim(wanted)
    listing = store.list_all(site=site or None, phantom=phantom or None,
                             signature=signature or None,
                             validation=validation or None,
                             completed_only=True)
    # One batched query instead of a full get() per row — the exports and the
    # comparison report read labels and results, never the geometry blob.
    return store.get_slim([item["id"] for item in listing])


@app.get("/api/export.csv", response_class=PlainTextResponse)
def export_csv_many(ids: str = "", site: str = "", phantom: str = "",
                    signature: str = "", validation: str = "",
                    layout: str = "long"):
    _check_filters(validation)
    recs = _selected_records(ids, site, phantom, signature, validation)
    if not recs:
        raise HTTPException(404, "no matching analyses")
    return wide_csv_export(recs) if layout == "wide" else csv_export(recs)


def _picture_ctx(aid: str, rec: dict):
    """An analysis context for drawing the comparison pictures.

    The same as _ctx, except that the decoded scan is not kept in this
    worker's cache — see _scan."""
    scan = _scan(aid, cache=False)
    return pipeline.build_ctx(scan, pdef, _reg(aid, rec),
                              {"scan_meta": scan.meta,
                               "sid_mm": rec.get("sid_mm") or 1000.0})


#: Columns of pictures in one comparison. Twelve fit an A4 landscape page at
#: a readable size, and at about fifty kilobytes a scan keep the page near
#: half a megabyte of pictures.
PICTURE_COLUMNS_MAX = 12


def _comparison_pictures(recs: list[dict]) -> dict:
    """The pictures of every test area, per analysis, for the comparison.

    Kept on disk once drawn (thumbnails.pictures_for), so only a scan never
    shown before costs a decode. Whatever goes wrong with one scan — a
    missing source file, a corrupted registration, a region that will not
    render — becomes labelled empty cells for that scan; the report itself
    never fails because of a picture.

    At most PICTURE_COLUMNS_MAX scans get pictures: the reference scans first,
    then the most recent. A comparison is built from a filter, and "every
    analysis of this phantom" can be sixty — about three megabytes of pictures
    and minutes of first-time decoding inside one request, on a 512 kbit/s
    link. The page says how many were left out; the charts still cover all.
    """
    # Only analyses with results are in the report at all.
    eligible = [r for r in recs if r.get("results")]
    references = [r for r in eligible if r.get("is_baseline")]
    others = sorted((r for r in eligible if not r.get("is_baseline")),
                    key=lambda r: r.get("acquired_at") or r.get("created_at")
                    or "", reverse=True)
    out = {}
    for slim in (references + others)[:PICTURE_COLUMNS_MAX]:
        aid = slim["id"]
        try:
            rec = store.get(aid)        # the listing omits reg and geometry
            if rec is None:
                continue
            directory = store.thumbs_dir(aid)
            out[aid] = thumbnails.pictures_for(
                rec, directory, lambda a=aid, r=rec: _picture_ctx(a, r),
                pdef_version=pdef.version, algo_version=ALGO_VERSION)
            # Deleted by another request while this one was drawing: the
            # pictures just written would otherwise outlive the record.
            if not store.exists(aid):
                thumbnails.forget(directory)
        except Exception:
            log.exception("comparison pictures failed for analysis=%s", aid)
            out[aid] = thumbnails.unavailable(thumbnails.FAILED)
    return out


@app.get("/api/comparison_report.html", response_class=HTMLResponse)
def comparison_report(ids: str = "", site: str = "", phantom: str = "",
                      signature: str = "", validation: str = ""):
    _check_filters(validation)
    recs = _selected_records(ids, site, phantom, signature, validation)
    if not recs:
        raise HTTPException(404, "no matching analyses")
    suffix = ""
    if site or phantom:
        suffix = " — " + " / ".join(x for x in (site, phantom) if x)
    return build_comparison_report(
        recs, title_suffix=suffix,
        filters={"site": site, "phantom": phantom, "signature": signature,
                 "validation": validation},
        pictures=_comparison_pictures(recs))


@app.get("/api/analyses/{aid}/report.html", response_class=HTMLResponse)
def report_html(aid: str):
    rec = store.get(aid)
    if rec is None:
        raise HTTPException(404, "not found")
    overlay = None
    if rec.get("geometry"):
        try:
            ctx = _ctx(aid)
            overlay = pipeline.render_overlay(_scan(aid), ctx, rec["geometry"],
                                              fmt="jpeg")
        except Exception:
            overlay = None
    baseline = store.baseline_for(rec["signature"], rec.get("phantom", ""),
                                  exclude_id=aid)
    # the report states whether the source file still matches its recorded hash
    integrity = store.verify_integrity(aid)
    if integrity.get("status") != "ok":
        log.error("integrity %s while building report for analysis=%s",
                  integrity.get("status"), aid)
    return build_report(rec, overlay=overlay, baseline=baseline,
                        integrity=integrity)


@app.get("/api/trends")
def trends(signature: str = "", site: str = "", phantom: str = "",
           validation: str = "", ids: str = ""):
    """Trend data for a label filter, a signature, or an explicit id list."""
    _check_filters(validation)
    if ids:
        listing = [{"id": a.strip()} for a in ids.split(",") if a.strip()]
    else:
        listing = store.list_all(site=site or None, phantom=phantom or None,
                                 signature=signature or None,
                                 validation=validation or None,
                                 completed_only=True)
    out = []
    for rec in store.get_slim([item["id"] for item in listing]):
        if not rec.get("results"):
            continue
        # Both dates travel separately. Collapsing them here is what made a
        # detector with a reset clock indistinguishable from a correct one.
        out.append({"id": rec["id"], "created_at": rec["created_at"],
                    "acquired_at": rec.get("acquired_at") or "",
                    "acquired_flag": acquisition_flag(rec),
                    "source_name": rec.get("source_name", ""),
                    "site": rec.get("site", ""), "phantom": rec.get("phantom", ""),
                    "signature": rec.get("signature", ""),
                    "status": rec.get("status", ""),
                    "is_baseline": rec["is_baseline"],
                    "rows": flatten_results(rec["results"])})
    out.sort(key=lambda a: (a["acquired_at"] or a["created_at"]))
    return pipeline.to_jsonable({
        "filter": {"signature": signature, "site": site, "phantom": phantom,
                   "validation": validation},
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
