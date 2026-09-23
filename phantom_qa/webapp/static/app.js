/* MSF Phantom QA — wizard frontend (no build step, no external deps). */
"use strict";

const COLORS = { geometry: "#00c8ff", linepairs: "#ffd400", lowcontrast: "#ff7bda",
                 uniformity: "#7bff9f", wedge: "#ff9d5c", reg: "#ff4040" };
const TESTS = ["geometry", "linepairs", "lowcontrast", "uniformity", "wedge"];
const STAGES = ["U", "A", "B", "C", "D", "E", "F"];

const S = {
  aid: null, record: null, reg: null, geometry: null, results: null,
  baseline: null, stage: "U", sid: 1000,
  imgEl: null, imgScale: 1, nativeCols: 1, nativeRows: 1,
  view: { k: 1, tx: 0, ty: 0 },
  visible: { geometry: true, linepairs: true, lowcontrast: true,
             uniformity: true, wedge: true },
  /* manualCorners holds the points being placed for whichever picking job
     S.mode names (see PICKERS) — the phantom's corners, the low-contrast
     block's corners, or one field-edge point. One list, because all three
     are placed, moved and applied the same way. */
  labels: true, selectedRoi: null, mode: "normal", manualCorners: [],
  /* corner picking: which point the keyboard acts on, which is being
     dragged, and where a left press began (so a drag is not a click) */
  selectedCorner: null, dragCorner: null, pickPress: null,
  /* window/level: the picture currently on screen, the window it was
     rendered with, and a re-mapped copy shown while the slider moves */
  wlPreview: null, wlCanvas: null, renderedWL: null,
  fieldEdgeSide: null, dragRoi: null, dimPreview: null,
  pendingFile: null,
  /* measuring-point undo state, mirrored from the server after every edit */
  history: { seq: 0, undo_depth: 0, redo_depth: 0 },
  layoutSource: "", phantomProfile: null,
  lcAngleCommitted: 0, rotTargetId: null, previewPending: false,
  /* Server-side analysis budget, learned at boot from /api/auth. The browser
     waits a little longer than the server does, so a bounded analysis reports
     its own failure rather than being cut off by the page. */
  analysisTimeoutS: 120,
  /* Set once a packed upload is refused as damaged on the way, and kept until
     the page is reloaded: every later file is then sent as it is (see
     uploadFile), so a fault in this browser's packing cannot refuse the same
     file on every attempt. */
  sendUnpacked: false,
  /* True only once /api/auth has answered that this server has no sign-in,
     as run_app.py runs on the operator's own computer. Until then, and on
     any server with sign-in, uploads are packed (see servedFromThisMachine). */
  signInOff: false,
  /* The low-contrast close-up on screen, and the loads that fetch it: the
     number of the newest load, whether a refresh is running, and whether
     another is wanted when it ends (see loadBlockView, refreshBlockView). */
  blockView: null, blockViewGen: 0, blockViewBusy: false, blockViewAgain: false,
};

/* Milliseconds before the page stops waiting for a measuring call. The margin
   covers sending the request and receiving a result over a slow link — the
   server's own deadline is what actually bounds the work. */
function analysisTimeoutMs() {
  return (S.analysisTimeoutS + 60) * 1000;
}

/* ---------------------------------------------- surviving a page reload

   Reloading the window used to lose the open analysis entirely, and the only
   way back an operator could see was to upload the same file again — which is
   both a multi-minute transfer on a field link and the true cause of the
   "different scans identified as same" reports, since the server correctly
   refused the byte-identical duplicate.

   What is remembered is deliberately thin: WHICH analysis was open, and enough
   words to describe it. Not the stage's work, and nothing is re-run. The
   record itself lives on the server, which stays the single source of truth.

   It lives in this browser only. Two operators on two machines therefore have
   independent memories — not because the software can tell them apart (there
   is one shared account) but because the note never leaves the device. On a
   SHARED machine the second person sees the first person's banner, which is
   why it names the file, the phantom, the step and the operator: enough to
   recognise that it is not yours, and nothing happens until it is clicked. */
const OPEN_KEY = "phantomqa_open_analysis";

function rememberOpenAnalysis() {
  const rec = S.record;
  if (!S.aid || !rec) return;
  // Only work still in progress is worth offering back. A finished analysis is
  // in History where it belongs.
  if (rec.results || rec.validation_status) { forgetOpenAnalysis(); return; }
  try {
    localStorage.setItem(OPEN_KEY, JSON.stringify({
      aid: S.aid, source_name: rec.source_name || "", stage: S.stage,
      phantom: rec.phantom || "", operator: rec.operator || "",
      saved_at: new Date().toISOString(),
    }));
  } catch (e) { /* private mode, or storage full — resume is a convenience */ }
}

function forgetOpenAnalysis() {
  try { localStorage.removeItem(OPEN_KEY); } catch (e) { /* noop */ }
}

function rememberedAnalysis() {
  try {
    const raw = localStorage.getItem(OPEN_KEY);
    const v = raw ? JSON.parse(raw) : null;
    return v && v.aid ? v : null;
  } catch (e) { return null; }
}

/* Everything the viewer knows about ONE analysis. Cleared as a unit whenever
   an analysis is closed or deleted, so no fragment of the previous scan — a
   selected ROI id, a half-finished corner click, a dimension preview — can be
   drawn over the next one. */
function clearAnalysisState() {
  S.aid = null; S.record = null; S.reg = null; S.geometry = null;
  S.results = null; S.baseline = null; S.imgEl = null;
  S.wlPreview = null; S.renderedWL = null;
  S.selectedRoi = null; S.mode = "normal"; S.manualCorners = [];
  S.fieldEdgeSide = null; S.dragRoi = null; S.dimPreview = null;
  S.pendingFile = null; S.pickPress = null;
  S.selectedCorner = null; S.dragCorner = null;
  S.history = { seq: 0, undo_depth: 0, redo_depth: 0 };
  S.layoutSource = ""; S.phantomProfile = null; S.layoutSaveChoice = null;
  S.lcAngleCommitted = 0; S.rotTargetId = null; S.previewPending = false;
  const d = document.querySelector("#roi-details");
  if (d) d.remove();
  // The picking strip lives under the image, outside every step, so it has to
  // be told that its job went with the analysis.
  renderCornerControls();
  forgetOpenAnalysis();
}

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, html = "") => {
  const e = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
  e.innerHTML = html;
  return e;
};
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v))
  ? "—" : (typeof v === "number" ? v.toFixed(d) : String(v));
/* The class is the status with everything but letters dropped: "n/a" (what
   analyses stored before "not applicable" and "not measured" were told apart
   still carry) keeps its old "na", and the two-word statuses become one class
   each instead of two unrelated ones. */
const chip = (s) => `<span class="chip ${(s || "na").replace(/[^a-z]/g, "")}">${s || "n/a"}</span>`;

/* The note the overall verdict carries: what it left out because it did not
   apply to this image. Empty for analyses stored before the split. */
const verdictNotes = (notes) => (notes || []).map(html_escape).join("; ");

function csrfToken() {
  const m = document.cookie.match(/(?:^|;\s*)phantomqa_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

/* Every state-changing request carries the CSRF token from the cookie; the
   server rejects the request if the two do not match.

   A 401 normally means the session expired, so we sign in again. The
   administrator-password endpoints (delete, validation, forgetting a stored
   layout, overriding the image-quality check) also answer 401 for a WRONG
   PASSWORD — bouncing to the login page there would log the operator out
   mid-action and never show them why. Those callers pass adminAuth so the
   error comes back for the panel to display. */
async function api(path, opts = {}) {
  const { adminAuth = false, timeoutMs = 0, ...rest } = opts;
  const o = { credentials: "same-origin", ...rest };
  const method = (o.method || "GET").toUpperCase();
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    o.headers = { ...(o.headers || {}), "X-CSRF-Token": csrfToken() };
  }
  // Opt-in per call, never a blanket default: a 7.5 MB upload over a field
  // link legitimately takes minutes, and a timeout that killed it would be a
  // far worse failure than the one it guards against. Only the measuring
  // calls, which the server itself bounds, set one.
  let timer = null;
  if (timeoutMs > 0 && typeof AbortController === "function") {
    const ac = new AbortController();
    o.signal = ac.signal;
    timer = setTimeout(() => ac.abort(), timeoutMs);
  }
  let r;
  try {
    r = await fetch(path, o);
  } catch (e) {
    if (e && e.name === "AbortError") {
      const err = new Error(
        `The server did not answer within ${Math.round(timeoutMs / 1000)} s. `
        + `The scan is stored — nothing needs re-uploading — so you can try `
        + `again, or open it later from History.`);
      err.status = 0;
      err.timedOut = true;
      throw err;
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
  let msg = r.statusText, body = null;
  if (!r.ok) {
    try { body = await r.json(); msg = body.detail || msg; } catch (e) { /* noop */ }
  }
  if (r.status === 401) {
    // The admin-password endpoints answer 401 for two different things. Only a
    // genuine session expiry may bounce to the login page; a refused password
    // has to reach the panel that asked for it. The middleware's expiry
    // message is the one thing that distinguishes them.
    const expired = msg === "Authentication required";
    if (!adminAuth || expired) {
      window.location = "login";
      throw new Error("Session expired — signing in again");
    }
  }
  if (!r.ok) {
    const err = new Error(typeof msg === "string" ? msg : r.statusText);
    err.status = r.status;
    if (body && body.duplicate_of) err.duplicateOf = body.duplicate_of;
    if (body && body.stale_geometry) err.staleGeometry = body;
    // The quality check's refusal carries the checks that failed, so the
    // panel offering the administrator's override can show what it would
    // overrule — in the same shape as the record's own verdict.
    if (body && body.quality_refused)
      err.qualityRefused = { summary: body.summary || "",
                             checks: body.failed_checks || [] };
    throw err;
  }
  return r.json();
}
const postJSON = (path, body, opts = {}) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body), ...opts });

/* An upload that says how far it has got, and can be called off.

   fetch() cannot report how much of a request body has gone, so a 7.5 MB
   scan on a 512 kbit/s link — two minutes — showed a status line that then
   cleared itself after six seconds, leaving a screen indistinguishable from a
   dead one. The field team's response was to reload and send the file again,
   which is where most of the duplicate refusals in the audit log came from.

   XMLHttpRequest is used for this one request only, because it is the only
   way to see upload progress. The error shape matches api()'s exactly, so
   every caller's handling of duplicates and expired sessions is unchanged. */
function uploadWithProgress(path, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path, true);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-CSRF-Token", csrfToken());

    xhr.upload.onprogress = (e) => {
      if (onProgress && e.lengthComputable) onProgress(e.loaded, e.total);
    };
    // The bytes are gone; what remains is the server decoding and registering
    // the scan, which takes seconds and reports nothing. Saying so beats a bar
    // that sits at 100% looking stuck.
    xhr.upload.onload = () => { if (onProgress) onProgress(-1, -1); };

    xhr.onload = () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch (e) { /* noop */ }
      if (xhr.status >= 200 && xhr.status < 300) { resolve(body); return; }
      if (xhr.status === 401 && (!body || body.detail === "Authentication required")) {
        window.location = "login";
        reject(new Error("Session expired — signing in again"));
        return;
      }
      const err = new Error((body && body.detail) || xhr.statusText
                            || `upload failed (${xhr.status})`);
      err.status = xhr.status;
      if (body && body.duplicate_of) err.duplicateOf = body.duplicate_of;
      // A packed file that did not unpack to the file chosen; uploadFile
      // answers it by sending files as they are from then on.
      if (body && body.damaged_transfer) err.damagedTransfer = true;
      reject(err);
    };
    xhr.onerror = () => {
      const err = new Error(
        "The connection dropped while sending. Nothing was stored, so the "
        + "scan can be sent again.");
      err.status = 0;
      err.networkError = true;
      reject(err);
    };
    xhr.onabort = () => {
      const err = new Error("Upload cancelled.");
      err.status = 0;
      err.cancelled = true;
      reject(err);
    };
    xhr.send(formData);
    S.uploadXhr = xhr;
  });
}

/* Seconds remaining, in words an operator can act on.

   Averaged over the transfer so far rather than the last instant: a field
   link stalls in bursts, and an estimate computed from the latest moment
   swings between "3 seconds" and "9 minutes" and tells nobody anything. */
function timeRemaining(loaded, total, startedAt) {
  const elapsed = (Date.now() - startedAt) / 1000;
  if (loaded <= 0 || elapsed < 1.5) return "";
  const rate = loaded / elapsed;
  const left = Math.round((total - loaded) / Math.max(rate, 1));
  if (left <= 0) return "";
  if (left < 60) return `about ${left} s left`;
  const mins = Math.round(left / 60);
  return `about ${mins} minute${mins === 1 ? "" : "s"} left`;
}

/* `sticky` keeps a message up until something replaces it. Anything the
   operator has to act on must not evaporate after six seconds — a failure that
   clears itself looks exactly like a screen that is still working. */
function status(msg, isErr = false, sticky = false) {
  const n = $("#app-status");
  n.textContent = msg;
  n.style.color = isErr ? "var(--fail)" : "var(--muted)";
  if (msg && !sticky) {
    setTimeout(() => { if (n.textContent === msg) n.textContent = ""; }, 6000);
  }
}

/* The last line of defence against a dead screen.

   A step that throws while drawing leaves whatever it had written standing —
   which is how an analysis that had actually FINISHED on the server sat on
   "Computing…" until the window was reloaded. Nothing here can know what went
   wrong, so it says so plainly and leaves the page usable. */
function unexpectedFailure(err) {
  const detail = (err && (err.message || err.reason)) || err || "unknown error";
  console.error("unexpected failure:", err);
  status(`Something went wrong while showing this page: ${detail}. `
         + `Your analysis is safe — reopen it from History.`, true, true);
}
window.addEventListener("error", (e) => unexpectedFailure(e.error || e.message));
window.addEventListener("unhandledrejection", (e) => unexpectedFailure(e.reason));

/* Someone else edited the same analysis while this change was in hand.

   Under one shared account two operators can work on the same record — the
   audit log shows it happening. The edit is refused rather than applied, and
   the page is reloaded from the server so the operator sees the current
   measuring points before deciding what to do. The message stays up: this is
   the one refusal that must not scroll past unnoticed, because the work it
   protects is someone else's. */
function handleStaleGeometry(err) {
  if (!err || !err.staleGeometry) return false;
  status(err.message, true, true);
  if (S.aid) openAnalysis(S.aid).catch(() => { /* gone; History will say */ });
  return true;
}

/* Replaces a half-drawn step with something the operator can act on. */
function stepFailed(container, err, retry) {
  const detail = (err && err.message) || String(err || "unknown error");
  console.error(err);
  container.innerHTML = `<h2>This step could not be shown</h2>
    <p style="color:var(--fail)">${html_escape(detail)}</p>
    <p class="hint">The scan and everything measured so far are stored on the
      server — nothing has been lost, and re-uploading the file is never
      necessary.</p>
    <button class="primary" id="btn-step-retry">Try again</button>
    <button class="secondary" id="btn-step-history">Go to history</button>`;
  const again = $("#btn-step-retry");
  if (again) again.addEventListener("click", () => (retry || renderStage)());
  const hist = $("#btn-step-history");
  if (hist) hist.addEventListener("click", () => showTab("history"));
}

/* ================= coordinate transforms ================= */

function pxToMm(px) {
  if (!S.reg || !S.reg.transform) return null;
  const A = S.reg.transform.A, t = S.reg.transform.t;
  const det = A[0][0] * A[1][1] - A[0][1] * A[1][0];
  const dx = px[0] - t[0], dy = px[1] - t[1];
  return [ (A[1][1] * dx - A[0][1] * dy) / det,
           (-A[1][0] * dx + A[0][0] * dy) / det ];
}

/* screen <-> native image px */
const nat2scr = (p) => [ p[0] * S.imgScale * S.view.k + S.view.tx,
                         p[1] * S.imgScale * S.view.k + S.view.ty ];
const scr2nat = (p) => [ (p[0] - S.view.tx) / (S.imgScale * S.view.k),
                         (p[1] - S.view.ty) / (S.imgScale * S.view.k) ];

/* ================= viewer ================= */

const canvas = $("#viewer");
const ctx2d = canvas.getContext("2d");

function resizeCanvas() {
  const wrap = $("#viewer-wrap");
  canvas.width = wrap.clientWidth;
  canvas.height = wrap.clientHeight;
  draw();
}
window.addEventListener("resize", resizeCanvas);

function zoomFit() {
  if (!S.imgEl) return;
  const k = Math.min(canvas.width / S.imgEl.width,
                     canvas.height / S.imgEl.height) * 0.98;
  S.view = { k, tx: (canvas.width - S.imgEl.width * k) / 2,
             ty: (canvas.height - S.imgEl.height * k) / 2 };
  draw();
}

function loadImage(params = "") {
  if (!S.aid) return;
  // What window these bytes will represent, so a later preview knows what it
  // is re-mapping FROM. Without it the preview would compound its own guesses.
  const asked = wlFromParams(params);
  const img = new Image();
  img.onload = () => {
    const first = !S.imgEl;
    S.imgEl = img;
    S.renderedWL = asked;
    S.wlPreview = null;              // the exact render supersedes the preview
    S.imgScale = img.width / S.nativeCols;
    if (first) zoomFit(); else draw();
  };
  // JPEG: about a tenth of the PNG, two seconds on a field link instead of
  // seventeen. Nothing is measured from this picture — the server measures
  // the original scan, and the discs are placed on their own lossless
  // close-up — and the window preview re-maps whatever bytes arrive.
  img.src = `api/analyses/${S.aid}/image.jpg${params}`;
}

/* The absolute window a request asks for, or the server's own default.

   With no wc/ww the server stretches the 1st-99th percentile, which is what
   display_range reports — so the preview has a defined starting point either
   way. */
function wlFromParams(params) {
  const wc = /[?&]wc=([-\d.]+)/.exec(params);
  const ww = /[?&]ww=([-\d.]+)/.exec(params);
  if (wc && ww) return { wc: parseFloat(wc[1]), ww: parseFloat(ww[1]) };
  const r = (S.reg && S.reg.display_range) || null;
  if (!r) return null;
  return { wc: (r.hi + r.lo) / 2, ww: Math.max(r.hi - r.lo, 1e-6) };
}

/* Window/level is expressed RELATIVE to the image's own value range, because
   detectors differ in bit depth: a fixed 0..4095 slider whites out a 14-bit
   image completely. 0-100 on the sliders maps into [lo, hi] measured from the
   data, and width can exceed the range so the image can be flattened. */
function wlAbsolute() {
  const r = (S.reg && S.reg.display_range) || null;
  if (!r) return null;
  const span = Math.max(r.hi - r.lo, 1e-6);
  const cPct = +$("#wl-center").value / 100;       // 0..1 across the range
  const wPct = +$("#wl-width").value / 100;        // 0..1.5 of the range
  const wc = r.lo + cPct * span;
  const ww = Math.max(wPct * span, span * 0.01);
  return { wc, ww };
}

function updateWLReadout() {
  const a = wlAbsolute();
  $("#wl-readout").textContent = a
    ? `W ${Math.round(a.ww)}  C ${Math.round(a.wc)}`
    : "";
}

/* Window/level, previewed in the browser and confirmed by the server.

   Every pause on the slider used to fetch a freshly rendered image: about a
   megabyte, roughly fifteen seconds at 512 kbit/s. Hunting for the window that
   makes the low-contrast discs visible therefore meant a minute of waiting per
   attempt, which is most of why that task was reported as painful.

   The preview re-maps the picture already on screen, instantly and without
   traffic. It cannot invent detail the current rendering threw away, so the
   server is still asked for the exact image — but only once the slider has
   settled, and the operator sees the effect while deciding rather than after.
   When the exact render arrives the preview is dropped. */
function renderWLPreview() {
  const want = wlAbsolute(), have = S.renderedWL;
  if (!S.imgEl || !want || !have) return;
  if (Math.abs(want.wc - have.wc) < 0.5 && Math.abs(want.ww - have.ww) < 0.5) {
    S.wlPreview = null;              // already exactly what is displayed
    return;
  }
  const w = S.imgEl.width, h = S.imgEl.height;
  if (!S.wlCanvas) S.wlCanvas = document.createElement("canvas");
  const cv = S.wlCanvas;
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const c = cv.getContext("2d", { willReadFrequently: true });
  c.drawImage(S.imgEl, 0, 0);
  const data = c.getImageData(0, 0, w, h);
  const px = data.data;

  // The displayed byte v came from [have.lo, have.hi]; map it back to a value
  // and forward through the requested window. Precomputed over the 256
  // possible bytes, so the per-pixel work is one table lookup.
  const haveLo = have.wc - have.ww / 2, wantLo = want.wc - want.ww / 2;
  const lut = new Uint8ClampedArray(256);
  for (let i = 0; i < 256; i++) {
    const value = haveLo + (i / 255) * have.ww;
    lut[i] = Math.round(((value - wantLo) / want.ww) * 255);
  }
  for (let i = 0; i < px.length; i += 4) {
    px[i] = px[i + 1] = px[i + 2] = lut[px[i]];
  }
  c.putImageData(data, 0, 0);
  S.wlPreview = cv;
}

let wlTimer = null;
function onWL() {
  updateWLReadout();
  renderWLPreview();                 // immediate, no request
  draw();
  clearTimeout(wlTimer);
  wlTimer = setTimeout(() => {
    const a = wlAbsolute();
    if (a) loadImage(`?wc=${a.wc.toFixed(1)}&ww=${a.ww.toFixed(1)}`);
  }, 400);
}
$("#wl-center").addEventListener("input", onWL);
$("#wl-width").addEventListener("input", onWL);
$("#wl-reset").addEventListener("click", () => {
  $("#wl-center").value = 50;
  $("#wl-width").value = 100;
  updateWLReadout();
  loadImage();                       // server default: 1-99 percentile stretch
});
$("#zoom-fit").addEventListener("click", zoomFit);

function drawRoi(roi, color, opts = {}) {
  if (!roi || !roi.type) return;
  ctx2d.strokeStyle = color;
  ctx2d.lineWidth = opts.selected ? 2.5 : 1.2;
  ctx2d.setLineDash(opts.dash || []);
  if (roi.type === "rect" && roi.corners_px) {
    ctx2d.beginPath();
    roi.corners_px.forEach((p, i) => {
      const s = nat2scr(p);
      i === 0 ? ctx2d.moveTo(s[0], s[1]) : ctx2d.lineTo(s[0], s[1]);
    });
    ctx2d.closePath();
    ctx2d.stroke();
  } else if (roi.type === "circle") {
    const c = nat2scr(roi.center_px);
    ctx2d.beginPath();
    ctx2d.arc(c[0], c[1], roi.radius_px * S.imgScale * S.view.k, 0, Math.PI * 2);
    ctx2d.stroke();
  } else if (roi.type === "annulus") {
    const c = nat2scr(roi.center_px);
    const k = S.imgScale * S.view.k;
    ctx2d.setLineDash([2, 3]);
    [roi.inner_radius_px, roi.outer_radius_px].forEach(r => {
      ctx2d.beginPath();
      ctx2d.arc(c[0], c[1], r * k, 0, Math.PI * 2);
      ctx2d.stroke();
    });
    ctx2d.setLineDash([]);
  } else if (roi.type === "segment") {
    const a = nat2scr(roi.p0_px), b = nat2scr(roi.p1_px);
    ctx2d.beginPath(); ctx2d.moveTo(a[0], a[1]); ctx2d.lineTo(b[0], b[1]);
    ctx2d.stroke();
  }
  ctx2d.setLineDash([]);
  if (opts.handle && roi.center_px) {
    const c = nat2scr(roi.center_px);
    ctx2d.fillStyle = roi.manually_adjusted ? "#ff9d00" : color;
    ctx2d.beginPath(); ctx2d.arc(c[0], c[1], 4, 0, Math.PI * 2); ctx2d.fill();
  }
  if (opts.label && roi.center_px) {
    const c = nat2scr(roi.center_px);
    ctx2d.font = "12px Segoe UI";
    ctx2d.fillStyle = color;
    ctx2d.fillText(opts.label, c[0] + 8, c[1] - 8);
  }
}

/* collect drawable+draggable ROIs from the current geometry */
function activeRois() {
  const out = [];
  if (!S.geometry) return out;
  const g = S.geometry;
  if (g.uniformity && !g.uniformity._error && S.visible.uniformity)
    g.uniformity.squares.forEach(sq => out.push(
      { roi: sq.roi, test: "uniformity", label: sq.id, drag: true }));
  if (g.wedge && !g.wedge._error && S.visible.wedge) {
    g.wedge.steps.forEach(st => out.push(
      { roi: st.roi, test: "wedge", label: `S${st.step}`, drag: true }));
    out.push({ roi: g.wedge.axis, test: "wedge" });
  }
  if (g.linepairs && !g.linepairs._error && S.visible.linepairs)
    g.linepairs.groups.forEach(gr => {
      out.push({ roi: gr.roi, test: "linepairs", label: gr.id, drag: true });
      out.push({ roi: gr.profile_seg, test: "linepairs" });
    });
  if (g.lowcontrast && !g.lowcontrast._error && S.visible.lowcontrast) {
    out.push({ roi: g.lowcontrast.block, test: "lowcontrast",
               label: "block", drag: true, block: true });
    g.lowcontrast.circles.forEach(c => {
      out.push({ roi: c.full_circle, test: "lowcontrast", dash: [4, 3] });
      out.push({ roi: c.bg_roi, test: "lowcontrast" });
      out.push({ roi: c.roi, test: "lowcontrast", label: c.id, drag: true });
    });
  }
  if (g.geometry && !g.geometry._error && S.visible.geometry) {
    Object.values(g.geometry.rulers || {}).forEach(r => {
      if (r.probe) out.push({ roi: r.probe, test: "geometry" });
    });
    Object.values(g.geometry.field_edges || {}).forEach(f => {
      if (f.probe) out.push({ roi: f.probe, test: "geometry", dash: [6, 4] });
    });
  }
  return out;
}

function draw() {
  ctx2d.clearRect(0, 0, canvas.width, canvas.height);
  if (!S.imgEl) return;
  ctx2d.imageSmoothingEnabled = S.view.k * S.imgScale < 1.5;
  ctx2d.save();
  ctx2d.translate(S.view.tx, S.view.ty);
  ctx2d.scale(S.view.k, S.view.k);
  ctx2d.drawImage(S.wlPreview || S.imgEl, 0, 0);
  ctx2d.restore();

  /* registration corners */
  if (S.reg && S.reg.summary && S.reg.summary.corners_px) {
    ctx2d.strokeStyle = COLORS.reg; ctx2d.lineWidth = 1.4;
    ctx2d.setLineDash([8, 5]);
    ctx2d.beginPath();
    S.reg.summary.corners_px.concat([S.reg.summary.corners_px[0]])
      .forEach((p, i) => {
        const s = nat2scr(p);
        i === 0 ? ctx2d.moveTo(s[0], s[1]) : ctx2d.lineTo(s[0], s[1]);
      });
    ctx2d.stroke();
    ctx2d.setLineDash([]);
  }

  const showHandles = S.stage === "C";
  const showLabels = S.labels && (S.stage === "B" || S.stage === "C");
  activeRois().forEach(item => {
    drawRoi(item.roi, COLORS[item.test], {
      label: showLabels ? item.label : null,
      handle: showHandles && item.drag,
      dash: item.dash,
      selected: S.selectedRoi && item.roi.id === S.selectedRoi,
    });
  });

  /* field edge markers */
  if (S.geometry && S.geometry.geometry && !S.geometry.geometry._error) {
    Object.entries(S.geometry.geometry.field_edges || {}).forEach(([side, f]) => {
      if (f.edge_pt_px) {
        const c = nat2scr(f.edge_pt_px);
        ctx2d.strokeStyle = COLORS.geometry;
        ctx2d.lineWidth = 2;
        ctx2d.beginPath();
        ctx2d.moveTo(c[0] - 10, c[1]); ctx2d.lineTo(c[0] + 10, c[1]);
        ctx2d.moveTo(c[0], c[1] - 10); ctx2d.lineTo(c[0], c[1] + 10);
        ctx2d.stroke();
        ctx2d.font = "11px Segoe UI"; ctx2d.fillStyle = COLORS.geometry;
        ctx2d.fillText(`field ${side}${f.manual ? " (manual)" : ""}`,
                       c[0] + 12, c[1] + 4);
      }
    });
  }

  /* points being placed — handles, not marks: each one can still be moved.
     Drawn in the colour of what they will move (red for the phantom, pink for
     the low-contrast block, blue for a field edge), so it is plain on the
     image which job the clicks are for. */
  const job = PICKERS[S.mode];
  if (job) S.manualCorners.forEach((p, i) => {
    const s = nat2scr(p);
    const chosen = i === S.selectedCorner;
    ctx2d.fillStyle = job.colour;
    ctx2d.beginPath(); ctx2d.arc(s[0], s[1], 5, 0, Math.PI * 2); ctx2d.fill();
    // A ring marks the point the arrow keys will nudge, so the keyboard has a
    // visible subject rather than an invisible one.
    if (chosen) {
      ctx2d.strokeStyle = "#ffffff";
      ctx2d.lineWidth = 2;
      ctx2d.beginPath(); ctx2d.arc(s[0], s[1], 10, 0, Math.PI * 2); ctx2d.stroke();
    }
    ctx2d.font = "12px Segoe UI";
    ctx2d.fillStyle = job.colour;
    ctx2d.fillText(job.label(i), s[0] + 10, s[1] - 6);
  });
}

/* The three jobs that put points on the image, and what each does with them.

   Only the phantom corners used to be correctable. The low-contrast block
   applied itself on the fourth click and a field edge on the first, so the
   slip the field team described — "we mistakenly clicked sometimes" — still
   went straight to the server there, and the only remedy was Undo after the
   fact. All three now share one way of working: left click places, a placed
   point can be dragged or nudged, the last one can be taken back, and nothing
   is sent until the operator presses Apply. One table rather than three copies
   of the controls, so the three cannot drift apart again. */
const PICKERS = {
  corners: {
    points: 4, colour: "#ff4040",
    heading: () => "Manual corners",
    place: () => "Left click the 4 phantom corners in order around the square.",
    applyLabel: "Apply corners",
    label: (i) => String(i + 1),
    apply: () => submitManualCorners(),
    cancelled: "Manual corners cancelled — the existing registration is unchanged.",
  },
  lccorners: {
    points: 4, colour: COLORS.lowcontrast,
    heading: () => "Low-contrast block corners",
    place: () => "Left click the 4 corners of the low-contrast block, in any "
                 + "order.",
    applyLabel: "Apply corners",
    label: (i) => String(i + 1),
    apply: () => submitBlockCorners(),
    cancelled: "Block corners cancelled — the block has not moved.",
  },
  fieldedge: {
    points: 1, colour: COLORS.geometry,
    heading: () => `Field edge — ${S.fieldEdgeSide} side`,
    place: () => `Left click the visible edge of the radiation field on the `
                 + `${S.fieldEdgeSide} side. Click somewhere else to move the `
                 + `point there.`,
    applyLabel: "Apply edge",
    label: () => `new ${S.fieldEdgeSide} edge`,
    apply: () => submitFieldEdge(),
    cancelled: "Field edge cancelled — nothing was changed.",
  },
};

/* Begin one of the picking jobs with an empty set of points. `side` names the
   field edge being placed and is empty for the corner jobs. */
function startPicking(mode, side = null) {
  S.mode = mode;
  S.fieldEdgeSide = side;
  S.manualCorners = [];
  S.selectedCorner = null;
  S.dragCorner = null;
  S.pickPress = null;
  const job = PICKERS[mode];
  status(`${job.place()} Nothing is sent until you press ${job.applyLabel}.`);
  renderCornerControls();
  draw();
}

/* Leave picking without sending anything. Quiet, because it also runs when
   the operator moves to another step; cancelCornerPicking is the spoken one. */
function endPicking() {
  S.mode = "normal";
  S.manualCorners = [];
  S.selectedCorner = null;
  S.dragCorner = null;
  S.pickPress = null;
  S.fieldEdgeSide = null;
  renderCornerControls();
}

/* A deliberate left click while picking: a new point, never a submission.

   A job that needs one point re-places it — the second click is the operator
   saying "no, there" — while a four-point job ignores clicks once it is full,
   so a stray fifth click cannot quietly replace a corner already judged. */
function placePickPoint(nat) {
  const job = PICKERS[S.mode];
  if (!job) return;
  if (job.points === 1) S.manualCorners = [nat];
  else if (S.manualCorners.length < job.points) S.manualCorners.push(nat);
  renderCornerControls();
  draw();
}

/* Apply is the ONLY way points reach the server, from the button or Enter. */
function applyPicking() {
  const job = PICKERS[S.mode];
  if (job && S.manualCorners.length === job.points) job.apply();
}

/* Which placed corner is under the pointer, or null.

   The same 14-pixel reach the ROI handles use, so grabbing behaves the same
   everywhere in the viewer. */
function hitCorner(screenPos) {
  let best = null, bestD = 14;
  S.manualCorners.forEach((p, i) => {
    const s = nat2scr(p);
    const d = Math.hypot(s[0] - screenPos[0], s[1] - screenPos[1]);
    if (d < bestD) { bestD = d; best = i; }
  });
  return best;
}

/* ---- mouse interaction ---- */
let panning = null;
/* Mouse buttons, by the numbers the DOM uses: 0 left, 1 middle, 2 right.

   Only the LEFT button may place or move anything. Every button used to: the
   handlers never looked at which one was pressed, so a middle click — the one
   an operator reaches for to pan — dropped a registration corner, and a middle
   drag over a measuring point moved it. */
const LEFT = 0, MIDDLE = 1;

/* Held space turns the left button into a pan, the convention every image
   editor shares. It is the fallback for touchpads with no usable middle
   click, and it is tracked here rather than read from the event because
   mousedown carries no key state for the space bar. */
let spaceHeld = false;
document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || isTypingTarget(e.target)) return;
  spaceHeld = true;
  e.preventDefault();               // stop the page scrolling under the image
  canvas.style.cursor = "grab";
});
document.addEventListener("keyup", (e) => {
  if (e.code !== "Space") return;
  spaceHeld = false;
  canvas.style.cursor = "";
});

/* Shortcuts must never fire while a site name or an angle is being typed. */
function isTypingTarget(el) {
  const tag = el && el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT"
         || (el && el.isContentEditable);
}

canvas.addEventListener("mousedown", (ev) => {
  const pos = [ev.offsetX, ev.offsetY];
  // Windows shows the autoscroll cursor on a middle press and then interprets
  // pointer movement as scrolling, which fights any panning built on top of
  // it. Claiming the event here is what stops that.
  if (ev.button === MIDDLE) ev.preventDefault();
  // Panning stays available WHILE picking. It used to be switched off
  // entirely, so the only way to reach a corner was to zoom out again —
  // losing the magnification the operator had zoomed in for, which is why
  // placing corners meant fighting the view instead of reading the image.
  const picking = !!PICKERS[S.mode];
  if (picking) {
    // Anything but a plain left press is a pan: the middle button, the right
    // button, or the space bar held down — three ways to reach the same
    // gesture, because not every laptop touchpad offers a middle click.
    if (ev.button !== LEFT || spaceHeld) { startPan(pos); return; }
    // A left press on a point already placed grabs it instead of adding a
    // fifth: a corner put down slightly wrong is corrected, not restarted.
    const grabbed = hitCorner(pos);
    if (grabbed !== null) {
      S.dragCorner = grabbed;
      S.selectedCorner = grabbed;
      draw();
      return;
    }
    S.pickPress = pos;
    return;
  }
  if (S.stage === "C" && !signedOff() && ev.button === LEFT) {
    const hit = hitRoi(pos);
    if (hit && hit.drag) {
      // Remember where the press started: a click that never moves must stay a
      // click. It used to POST the ROI's unchanged centre, which stamped it
      // "manually adjusted", wrote an audit line, and — on the low-contrast
      // block — discarded the automatic grid refinement.
      S.dragRoi = hit;
      S.dragStart = pos;
      S.dragMoved = false;
      S.selectedRoi = hit.roi.id;
      draw();
      return;
    }
  }
  startPan(pos);
});

function startPan(pos) {
  panning = { start: pos, tx: S.view.tx, ty: S.view.ty };
  canvas.style.cursor = "grabbing";
}
canvas.addEventListener("mousemove", (ev) => {
  const pos = [ev.offsetX, ev.offsetY];
  const nat = scr2nat(pos);
  const mm = pxToMm(nat);
  $("#cursor-mm").textContent = mm
    ? `x ${mm[0].toFixed(1)} mm  y ${mm[1].toFixed(1)} mm` : "";
  if (S.dragCorner !== null && S.dragCorner !== undefined) {
    S.manualCorners[S.dragCorner] = nat;
    draw();
    return;
  }
  if (S.dragRoi) {
    if (S.dragStart && Math.hypot(pos[0] - S.dragStart[0],
                                  pos[1] - S.dragStart[1]) > 3) {
      S.dragMoved = true;
    }
    if (!S.dragMoved) return;
    const natP = scr2nat(pos);
    moveRoiLocal(S.dragRoi.roi, natP);
    draw();
    return;
  }
  if (panning) {
    S.view.tx = panning.tx + (pos[0] - panning.start[0]);
    S.view.ty = panning.ty + (pos[1] - panning.start[1]);
    draw();
  }
});
canvas.addEventListener("mouseup", async (ev) => {
  canvas.style.cursor = "grab";
  const pos = [ev.offsetX, ev.offsetY];
  const placing = !!PICKERS[S.mode];
  if (placing) {
    // A point is a deliberate left click that did not move. Releasing any
    // other button ends a pan and marks nothing; a left press that travelled
    // was a drag, and a drag is not a decision about where a corner is.
    const press = S.pickPress;
    S.pickPress = null;
    if (S.dragCorner !== null && S.dragCorner !== undefined) {
      S.dragCorner = null;          // finished adjusting a placed point
      renderCornerControls();
      return;
    }
    if (ev.button !== LEFT || spaceHeld) { panning = null; return; }
    if (press && Math.hypot(pos[0] - press[0], pos[1] - press[1]) > 4) return;
    // Dropping the fourth corner used to submit immediately — for the phantom
    // and for the low-contrast block — and a field edge went on the first
    // click. One slip meant the change was already away. A click now only
    // fills a slot; nothing is sent until Apply, whichever job this is.
    placePickPoint(scr2nat(pos));
    return;
  }
  if (S.dragRoi) {
    const hit = S.dragRoi, roi = hit.roi, moved = S.dragMoved;
    S.dragRoi = null; S.dragStart = null; S.dragMoved = false;
    if (!moved) {
      // A press that did not move is an inspect, not an edit.
      try {
        const r = await api(`api/analyses/${S.aid}/roi_stats?roi_id=`
                            + encodeURIComponent(roi.id));
        showRoiDetails(r.roi, r.stats);
      } catch (e) { /* noop */ }
      draw();
      return;
    }
    if (hit.block) { await placeBlock({ center_px: roi.center_px }); return; }
    try {
      const r = await postJSON(`api/analyses/${S.aid}/roi`,
        { roi_id: roi.id, center_px: roi.center_px,
          expect_seq: S.history.seq });
      applyChanged(r);
      showRoiDetails(r.roi, r.stats);
      status(`${roi.id} moved — measurement updated`);
      draw();
    } catch (e) {
      if (!handleStaleGeometry(e)) {
        status("ROI update failed: " + e.message, true);
        openAnalysis(S.aid);          // resync rather than show a stale ROI
      }
    }
    return;
  }
  if (panning) {
    const movedFar = Math.hypot(pos[0] - panning.start[0],
                                pos[1] - panning.start[1]) > 4;
    panning = null;
    if (!movedFar && S.stage === "C") {
      const hit = hitRoi(pos);
      if (hit) {
        S.selectedRoi = hit.roi.id;
        try {
          const r = await api(`api/analyses/${S.aid}/roi_stats?roi_id=` +
                              encodeURIComponent(hit.roi.id));
          showRoiDetails(r.roi, r.stats);
        } catch (e) { /* noop */ }
        draw();
      }
    }
  }
});
/* No context menu over the image.

   It would cover the very corner being placed, and the right button is wanted
   as a second way to pan for mice and touchpads without a usable middle one.
   Suppressed only on the canvas; the rest of the page keeps its menu. */
canvas.addEventListener("contextmenu", (ev) => ev.preventDefault());

/* A middle click that reaches the document still triggers autoscroll in some
   browsers even after mousedown was claimed, so the follow-up event is
   claimed too. */
canvas.addEventListener("auxclick", (ev) => {
  if (ev.button === MIDDLE) ev.preventDefault();
});

canvas.addEventListener("wheel", (ev) => {
  ev.preventDefault();
  const f = ev.deltaY < 0 ? 1.15 : 1 / 1.15;
  const pos = [ev.offsetX, ev.offsetY];
  S.view.tx = pos[0] - (pos[0] - S.view.tx) * f;
  S.view.ty = pos[1] - (pos[1] - S.view.ty) * f;
  S.view.k *= f;
  draw();
}, { passive: false });

function hitRoi(screenPos) {
  const items = activeRois().filter(i => i.drag && i.roi.center_px);
  let best = null, bestD = 14;
  items.forEach(i => {
    const c = nat2scr(i.roi.center_px);
    const d = Math.hypot(c[0] - screenPos[0], c[1] - screenPos[1]);
    if (d < bestD) { bestD = d; best = i; }
  });
  return best;
}

function moveRoiLocal(roi, natCenter) {
  const dx = natCenter[0] - roi.center_px[0];
  const dy = natCenter[1] - roi.center_px[1];
  roi.center_px = natCenter;
  if (roi.corners_px)
    roi.corners_px = roi.corners_px.map(p => [p[0] + dx, p[1] + dy]);
}

function replaceRoi(roiId, fresh) {
  const walk = (node) => {
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (node && typeof node === "object") {
      if (node.id === roiId && node.type) {
        Object.keys(node).forEach(k => { delete node[k]; });
        Object.assign(node, fresh);
      } else {
        Object.values(node).forEach(walk);
      }
    }
  };
  walk(S.geometry);
}

/* The server returns every ROI a move or rotation touched — the dragged one
   plus its companions (background ring, object outline, profile line). All of
   them must be redrawn, or the display shows a measurement in one place while
   the number comes from another. */
function applyChanged(response) {
  const list = response.changed || (response.roi ? [response.roi] : []);
  list.forEach(r => { if (r && r.id) replaceRoi(r.id, r); });
  noteHistory(response);
}

/* Every geometry-changing response carries how far undo and redo can now go,
   so the buttons never need a second round trip to know their state. */
function noteHistory(response) {
  if (response && response.history) {
    S.history = response.history;
    refreshHistoryButtons();
  }
}

function refreshHistoryButtons() {
  const u = $("#btn-undo"), r = $("#btn-redo");
  if (u) {
    u.disabled = !S.history.undo_depth;
    u.textContent = `↶ Undo${S.history.undo_depth ? ` (${S.history.undo_depth})` : ""}`;
  }
  if (r) {
    r.disabled = !S.history.redo_depth;
    r.textContent = `↷ Redo${S.history.redo_depth ? ` (${S.history.redo_depth})` : ""}`;
  }
}

function showRoiDetails(roi, stats) {
  let d = $("#roi-details");
  if (!d) {
    d = el("div", { id: "roi-details", class: "roi-details" });
    $("#wizard-pane").appendChild(d);
  }
  S.selectedRoi = roi.id;
  const mm = roi.center_mm || [];
  const rotatable = roi.type === "rect";
  const ang = rotatable ? (roi.angle_deg || 0) : null;
  const badges =
    (roi.manually_adjusted ? ' <span class="chip warn">manually adjusted</span>' : "")
    + (roi.from_profile ? ' <span class="roi-badge">from stored layout</span>' : "");
  // Segments (profile lines, the wedge axis) have no area, so no statistics.
  const statLine = stats
    ? `μ=${fmt(stats.mean, 1)} σ=${fmt(stats.std, 1)} n=${stats.n}` : "";
  d.innerHTML = `<b>${roi.id}</b>${badges}<br>
    centre (${fmt(mm[0])}, ${fmt(mm[1])}) mm &nbsp; ${statLine}
    ${rotatable ? `
    <div class="rot-row">
      <label>angle
        <input type="range" id="roi-angle" min="-180" max="180" step="0.5"
               value="${ang.toFixed(1)}">
      </label>
      <input type="number" id="roi-angle-num" step="0.5" min="-180" max="180"
             value="${ang.toFixed(1)}" title="degrees in the phantom frame">
      <button class="secondary-sm" id="roi-angle-minus">−1°</button>
      <button class="secondary-sm" id="roi-angle-plus">+1°</button>
    </div>
    <span class="hint">Drag the dot to move · type an exact angle or drag the
    slider · [ and ] nudge by 1°</span>`
    : ""}`;
  if (!rotatable) return;
  // The keyboard shortcut acts on the ROI whose panel is actually on screen,
  // not on whatever was last clicked: selecting the low-contrast block on
  // mousedown used to leave a stale panel and send the nudge to the wrong ROI.
  S.rotTargetId = roi.id;
  const slider = $("#roi-angle");
  const num = $("#roi-angle-num");
  const sync = (v, from) => {
    if (from !== "slider") slider.value = v;
    if (from !== "num") num.value = (+v).toFixed(1);
  };
  slider.addEventListener("input", () => {
    sync(slider.value, "slider");
    previewRotation(roi.id, +slider.value);
  });
  slider.addEventListener("change", () => commitRotation(roi.id, +slider.value));
  num.addEventListener("input", () => {
    const v = parseFloat(num.value);
    if (Number.isFinite(v)) { sync(v, "num"); previewRotation(roi.id, v); }
  });
  // Enter commits, not blur: `change` on a number input also fires when the
  // field loses focus, so tabbing away would silently POST a rotation.
  num.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const v = parseFloat(num.value);
    if (Number.isFinite(v)) commitRotation(roi.id, v);
    else status("Enter a number of degrees.", true);
  });
  $("#roi-angle-minus").addEventListener("click", () => nudgeRotation(-1));
  $("#roi-angle-plus").addEventListener("click", () => nudgeRotation(+1));
}

function findRoi(roiId) {
  let found = null;
  const walk = (n) => {
    if (found) return;
    if (Array.isArray(n)) { n.forEach(walk); return; }
    if (n && typeof n === "object") {
      if (n.id === roiId && n.type) { found = n; return; }
      Object.values(n).forEach(walk);
    }
  };
  walk(S.geometry);
  return found;
}

/* A phantom-frame angle expressed in image space — the same mapping the
   server uses (analysis/common.img_angle_deg). The old code assumed the
   phantom's +y always pointed up on screen, which is false for a mirrored
   registration: the local preview then turned the opposite way and only
   snapped back after the round trip. */
function imgAngleDeg(mmDeg) {
  if (!S.reg || !S.reg.transform) return mmDeg;
  const A = S.reg.transform.A, r = mmDeg * Math.PI / 180;
  const c = Math.cos(r), s = Math.sin(r);
  return Math.atan2(A[1][0] * c + A[1][1] * s,
                    A[0][0] * c + A[0][1] * s) * 180 / Math.PI;
}

const rotatePointAbout = (p, c, a) => {
  const dx = p[0] - c[0], dy = p[1] - c[1];
  return [c[0] + dx * Math.cos(a) - dy * Math.sin(a),
          c[1] + dx * Math.sin(a) + dy * Math.cos(a)];
};

/* Rotate locally for instant feedback; the server has the final word. */
function previewRotation(roiId, angleDeg) {
  if (roiId === "lowcontrast/block") { previewBlockAngle(angleDeg); return; }
  const roi = findRoi(roiId);
  if (!roi || roi.type !== "rect" || !roi.corners_px) return;
  const a = (imgAngleDeg(angleDeg) - imgAngleDeg(roi.angle_deg || 0))
            * Math.PI / 180;
  const c = roi.center_px;
  roi.corners_px = roi.corners_px.map(p => rotatePointAbout(p, c, a));
  roi.angle_deg = angleDeg;
  roi.angle_img_deg = imgAngleDeg(angleDeg);
  S.previewPending = true;
  draw();
}

async function commitRotation(roiId, angleDeg) {
  // The block is a rigid group of 25 ROIs; rotating it through the generic ROI
  // endpoint would turn its outline and leave the eight circles behind.
  if (roiId === "lowcontrast/block") { await commitBlockAngle(angleDeg); return; }
  try {
    const r = await postJSON(`api/analyses/${S.aid}/roi_rotate`,
                             { roi_id: roiId, angle_deg: angleDeg,
                               expect_seq: S.history.seq });
    applyChanged(r);
    S.previewPending = false;
    showRoiDetails(r.roi, r.stats);
    status(`${roiId} rotated to ${angleDeg.toFixed(1)}° — measurement updated`);
    draw();
  } catch (e) {
    if (handleStaleGeometry(e)) return;
    status("Rotation failed: " + e.message, true);
    openAnalysis(S.aid);
  }
}

function nudgeRotation(delta) {
  const slider = $("#roi-angle");
  const target = S.rotTargetId;
  if (!slider || !target) return;
  const v = +(+slider.value + delta).toFixed(1);
  slider.value = v;
  const num = $("#roi-angle-num");
  if (num) num.value = v.toFixed(1);
  previewRotation(target, v);
  commitRotation(target, v);
}

/* [ and ] nudge whichever angle control is in play: the block's field when it
   has focus, otherwise the selected ROI's panel, otherwise the block. */
/* Keyboard while placing points: nudge, undo, apply, cancel.

   A mouse cannot reliably place a point on one detector pixel, and the corner
   decides the registration every later measurement rests on. The arrow keys
   give that last pixel without asking the operator to zoom further in. The
   same keys serve every picking job, so the block corners and a field edge
   are corrected exactly as the phantom corners are. */
document.addEventListener("keydown", (e) => {
  const job = PICKERS[S.mode];
  if (!job || isTypingTarget(e.target)) return;
  if (e.key === "Escape") { e.preventDefault(); cancelCornerPicking(); return; }
  if (e.key === "Backspace" && S.manualCorners.length) {
    e.preventDefault();
    S.manualCorners.pop();
    S.selectedCorner = null;
    renderCornerControls();
    draw();
    return;
  }
  if (e.key === "Enter" && S.manualCorners.length === job.points) {
    e.preventDefault();
    applyPicking();
    return;
  }
  const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0],
                 ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
  if (!step || S.selectedCorner === null || S.selectedCorner === undefined) return;
  e.preventDefault();
  // Screen pixels, converted to image pixels, so a nudge means the same
  // distance on screen whatever the zoom — one press, one visible step.
  const by = e.shiftKey ? 10 : 1;
  const p = nat2scr(S.manualCorners[S.selectedCorner]);
  S.manualCorners[S.selectedCorner] =
    scr2nat([p[0] + step[0] * by, p[1] + step[1] * by]);
  draw();
});

document.addEventListener("keydown", (e) => {
  if (S.stage !== "C") return;
  if (e.key !== "[" && e.key !== "]") return;
  const t = e.target;
  if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) && t.id !== "lc-angle")
    return;
  const d = e.key === "[" ? -1 : +1;
  e.preventDefault();
  if (t && t.id === "lc-angle") { nudgeBlockAngle(d); return; }
  if (S.rotTargetId && $("#roi-angle")) { nudgeRotation(d); return; }
  nudgeBlockAngle(d);
});

/* ---- low-contrast block angle ----
   Its own path, because the block is a rigid group: the outline and all eight
   circle triples turn together about the block centre. */
function previewBlockAngle(angleDeg) {
  const lc = S.geometry && S.geometry.lowcontrast;
  if (!lc || lc._error || !lc.block || !lc.block.corners_px) return;
  const c = lc.block.center_px;
  const a = (imgAngleDeg(angleDeg) - imgAngleDeg(lc.block.angle_deg || 0))
            * Math.PI / 180;
  lc.block.corners_px = lc.block.corners_px.map(p => rotatePointAbout(p, c, a));
  lc.block.angle_deg = angleDeg;
  lc.block.angle_img_deg = imgAngleDeg(angleDeg);
  (lc.circles || []).forEach(ci => ["roi", "bg_roi", "full_circle"].forEach(k => {
    if (ci[k] && ci[k].center_px)
      ci[k].center_px = rotatePointAbout(ci[k].center_px, c, a);
  }));
  lc.angle_deg = angleDeg;
  S.previewPending = true;
  draw();
}

async function commitBlockAngle(v) {
  if (!Number.isFinite(v)) { status("Enter a number of degrees.", true); return; }
  await placeBlock({ angle_deg: v });
}

/* Turn the block end for end.

   The block outline is symmetrical about its centre, so the automatic angle is
   only ever determined modulo 180 — detection genuinely cannot tell which end
   is which. Turning it moves every ring onto the disc opposite — L1's ring to
   where L5's was — with all eight still on real discs.

   It no longer renames any disc. Which design level each ring is read as is
   the insert setting's job (renderInsertOrientation), and the server takes a
   turned block into account when applying it, so pressing this can neither
   undo nor double a correction made there. */
async function flipBlock() {
  const lc = S.geometry && S.geometry.lowcontrast;
  if (!lc || lc._error || !lc.block) return;
  const flipped = normaliseAngle((Number(lc.angle_deg) || 0) + 180);
  const f = $("#lc-angle");
  if (f) f.value = flipped.toFixed(1);
  previewBlockAngle(flipped);
  await commitBlockAngle(flipped);
}

/* Keep a phantom-frame angle in -180..180 so the field and the ±1° buttons
   stay usable after a flip. */
function normaliseAngle(deg) {
  let a = ((deg + 180) % 360 + 360) % 360 - 180;
  if (Object.is(a, -180)) a = 180;
  return a;
}

function nudgeBlockAngle(delta) {
  const f = $("#lc-angle");
  if (!f || f.disabled) return;
  const v = +((parseFloat(f.value) || 0) + delta).toFixed(1);
  f.value = v.toFixed(1);
  previewBlockAngle(v);
  commitBlockAngle(v);
}

/* ================= overlay toggles ================= */

function renderToggles() {
  const c = $("#overlay-toggles");
  c.innerHTML = "";
  TESTS.forEach(t => {
    const lab = el("label", {},
      `<span class="swatch" style="background:${COLORS[t]}"></span> ${t}`);
    const cb = el("input", { type: "checkbox" });
    cb.checked = S.visible[t];
    cb.addEventListener("change", () => { S.visible[t] = cb.checked; draw(); });
    lab.prepend(cb);
    c.appendChild(lab);
  });
  const lab = el("label", {}, "labels");
  const cb = el("input", { type: "checkbox" });
  cb.checked = S.labels;
  cb.addEventListener("change", () => { S.labels = cb.checked; draw(); });
  lab.prepend(cb);
  c.appendChild(lab);
}

/* ================= identity (site / phantom) ================= */

/* Shown above every stage once an analysis is open, so the labels are always
   visible and always editable — forgetting them at upload is recoverable. */
function renderIdentityBar() {
  const bar = $("#identity-bar");
  if (!S.aid || !S.record) { bar.innerHTML = ""; bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden");
  const r = S.record;
  const missing = !r.site && !r.phantom;
  const val = (v) => v ? html_escape(v) : '<span class="hint">—</span>';
  bar.innerHTML = `
    <div class="ident-row">
      <div>
        <span class="ident-k">Site</span> ${val(r.site)}
        <span class="ident-sep">·</span>
        <span class="ident-k">Phantom</span> ${val(r.phantom)}
        ${r.operator ? `<span class="ident-sep">·</span>
           <span class="ident-k">Op</span> ${html_escape(r.operator)}` : ""}
      </div>
      <div>
        <button id="btn-edit-ident" class="secondary-sm">Edit</button>
        ${(r.protection || []).length ? "" : `
        <button id="btn-discard" class="secondary-sm danger"
                title="Throw this analysis away. Possible without a password
because nothing has been decided about it yet.">Discard…</button>`}
      </div>
    </div>
    ${exposureRow(r)}
    <div class="ident-row" style="padding-top:0">
      <div>
        <span class="ident-k">Validation</span> ${valChip(r.validation_status)}
        ${r.validated_by
          ? `<span class="hint"> by ${html_escape(r.validated_by)}`
            + `${r.validated_at ? " · " + html_escape(r.validated_at.slice(0, 16)) : ""}</span>`
          : ""}
      </div>
      <button id="btn-validate" class="secondary-sm">Set…</button>
    </div>
    ${r.validation_comment
      ? `<div class="ident-comment">“${html_escape(r.validation_comment)}”</div>`
      : ""}
    ${(!r.results && r.revision_count)
      ? `<div class="ident-warn">A re-run of this analysis is unfinished, so it
           has no results and is absent from trends. Carry on from the step
           shown, or put the previous results back.
           <button id="btn-undo-rerun" class="secondary-sm">Undo re-run</button>
         </div>`
      : ""}
    ${missing ? '<div class="ident-warn">⚠ No site or phantom — this analysis '
      + 'will not appear in any grouped trend. Add them now.</div>' : ""}`;
  $("#btn-validate").addEventListener("click", () =>
    setValidation(r, (v) => { Object.assign(S.record, v); renderIdentityBar(); }));
  // Present while the analysis is open, so a mistake noticed halfway through
  // is cleared where it was noticed rather than hunted down in History later.
  const discard = $("#btn-discard");
  if (discard) discard.addEventListener("click", () => deleteAnalysis(S.aid));
  const undo = $("#btn-undo-rerun");
  if (undo) undo.addEventListener("click", () => undoRerun(S.aid));
  $("#btn-edit-ident").addEventListener("click", async () => {
    const vals = await editLabelsDialog(r, `Identification — ${r.id}`);
    if (!vals) return;
    try {
      const out = await postJSON(`api/analyses/${S.aid}/labels`, vals);
      Object.assign(S.record, vals);
      // A stored layout belongs to the phantom label. Renaming the last
      // analysis off a label leaves that layout describing nothing, so it is
      // discarded — say so, because it is not visible anywhere else.
      if (out.layout_deleted) {
        S.phantomProfile = null;
        status(`Identification updated. The stored measuring-point layout for `
               + `phantom ${out.phantom_before} was discarded — no analyses `
               + `carry that name any more.`);
      } else {
        status("Identification updated.");
      }
      await refreshProfileForRecord();
      renderIdentityBar();
      renderStage();
    } catch (e) { status("Could not save: " + e.message, true); }
  });
}

/* When the scan was taken and how much radiation reached the detector, as the
   detector itself recorded it. An under-exposed scan was behind one of the
   first field test's puzzles; with the deviation index in view it explains
   itself at once. Read from the stored header the page already has, so it
   costs nothing to send. Nothing is judged from these values. */
const EXPOSURE_NOTE = "As written by the detector. A deviation index of 0 is "
  + "the exposure the detector was set up to expect; +1 is about a quarter "
  + "more, −1 about a fifth less.";

/* Indices to the whole number, the deviation index to one decimal with its
   sign, because the sign is the point: minus is under-exposed, plus over.
   Halves round away from zero and the digits are built by hand, exactly as
   report.exposure_text() does, so the screen and the printed report agree. */
function exposureText(v, signed = false) {
  if (typeof v !== "number" || !isFinite(v)) return "not recorded";
  const steps = Math.floor(Math.abs(v) * (signed ? 10 : 1) + 0.5);
  if (!signed) return String(v < 0 ? -steps : steps);
  if (steps === 0) return "0.0";
  return (v > 0 ? "+" : "−") + Math.floor(steps / 10) + "." + (steps % 10);
}

function exposureValue(v, signed = false) {
  const t = exposureText(v, signed);
  return t === "not recorded" ? `<span class="hint">${t}</span>` : t;
}

function exposureRow(r) {
  const m = r.meta || {};
  const flag = r.acquired_flag || "";
  const stamp = html_escape((r.acquired_at || "").slice(0, 16));
  const when = flag === "missing"
    ? '<span class="hint">not recorded</span>'
    : flag ? `<span title="${ACQ_NOTE[flag]}" style="color:var(--warn)">`
             + `${stamp} ⚠</span>`
    : stamp;
  return `
    <div class="ident-row" style="padding-top:0">
      <div title="${EXPOSURE_NOTE}">
        <span class="ident-k">Acquired</span> ${when}
        <span class="ident-sep">·</span>
        <span class="ident-k">Exposure index</span> ${exposureValue(m.ExposureIndex)}
        <span class="hint">(target ${exposureText(m.TargetExposureIndex)})</span>
        <span class="ident-sep">·</span>
        <span class="ident-k">Deviation index</span> ${exposureValue(m.DeviationIndex, true)}
      </div>
    </div>`;
}

/* The History cell: two numbers out of a listing that is fetched whole. */
function exposureCell(a) {
  const ei = a.exposure_index, di = a.deviation_index;
  if (typeof ei !== "number" && typeof di !== "number")
    return '<span class="hint">not recorded</span>';
  return `EI ${exposureValue(ei)} · DI ${exposureValue(di, true)}`;
}

/* After a phantom rename the layout on offer changes, so re-read it rather
   than leaving Stage C advertising the previous phantom's. */
async function refreshProfileForRecord() {
  if (!S.aid) return;
  try {
    const rec = await api(`api/analyses/${S.aid}`);
    S.phantomProfile = rec.phantom_profile || null;
    S.record = { ...S.record, ...rec };
  } catch (e) { /* leave what we have */ }
}

function html_escape(s) {
  return String(s).replace(/[&<>"]/g, ch => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
}

/* Modal editor shared by the identity bar and the History table. */
function editLabelsDialog(rec, title = "Edit identification") {
  return new Promise(async (resolve) => {
    $("#modal-title").textContent = title;
    ["site", "phantom", "operator", "notes"].forEach(
      k => { $("#m-" + k).value = rec[k] || ""; });
    try {
      const lab = await api("api/labels");
      const opts = (items) => (items || [])
        .map(s => `<option value="${s.value.replace(/"/g, "&quot;")}">`).join("");
      $("#dl-site-m").innerHTML = opts(lab.site);
      $("#dl-phantom-m").innerHTML = opts(lab.phantom);
    } catch (e) { /* ignore */ }
    const back = $("#modal-backdrop");
    back.classList.remove("hidden");
    $("#m-site").focus();

    const done = (result) => {
      back.classList.add("hidden");
      $("#m-save").onclick = null;
      $("#m-cancel").onclick = null;
      back.onclick = null;
      document.onkeydown = null;
      resolve(result);
    };
    $("#m-save").onclick = () => done({
      site: $("#m-site").value.trim(),
      phantom: $("#m-phantom").value.trim(),
      operator: $("#m-operator").value.trim(),
      notes: $("#m-notes").value.trim(),
    });
    $("#m-cancel").onclick = () => done(null);
    back.onclick = (e) => { if (e.target === back) done(null); };
    document.onkeydown = (e) => { if (e.key === "Escape") done(null); };
  });
}


/* How far a stored record got, in the operator's words rather than a letter. */
const STAGE_WORDS = {
  A: "registration confirmed",
  B: "measuring points proposed",
  C: "measuring points confirmed",
  D: "ready to measure",
  E: "measuring",
  F: "measured",
};

function stageWords(d) {
  if (d.status && d.status !== "draft") return "finished";
  return STAGE_WORDS[d.stage] || "just uploaded";
}

function fileSize(bytes) {
  if (bytes === null || bytes === undefined || !isFinite(bytes)) return "—";
  return bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + " MB"
                          : Math.max(1, Math.round(bytes / 1024)) + " kB";
}

/* A local file's modified time, which is what tells two exports apart.

   Two scans of the same phantom minutes apart arrive from the machine as two
   files called 003_0000.dcm. The name says nothing; the clock says which is
   which, and the field team had no way to see it before sending. */
function fileStamp(file) {
  const t = file && file.lastModified;
  if (!t) return "—";
  const d = new Date(t);
  const two = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} `
       + `${two(d.getHours())}:${two(d.getMinutes())}`;
}

/* Three-way choice for a re-uploaded file.

   Deliberately not a confirm(): with two buttons, "Cancel" would have to mean
   "analyse it again", so an operator dismissing the dialog would create the
   very duplicate this check exists to prevent. Cancel must mean cancel.

   Both sides are shown because "already analysed" answers a question nobody
   asked. The operator's question is whether the thing already here is the
   thing they meant to send and what became of it — and when it was not
   answered, the field team renamed the file and sent it again to find out.
   The primary action is therefore to carry on with the existing record, which
   is nearly always what was wanted. */
function duplicateDialog(d, file) {
  return new Promise((resolve) => {
    $("#dup-lead").textContent = file
      ? "The file you chose is already stored here, byte for byte. Sending it "
        + "again would cost the transfer and add a second row that counts "
        + "twice in every trend."
      : "This file is byte-for-byte identical to a record already stored.";
    // textContent, not innerHTML: labels, notes and file names are all typed
    // by people or chosen by an export tool.
    $("#dup-new-name").textContent = file ? file.name : "—";
    $("#dup-new-size").textContent = file ? fileSize(file.size) : "—";
    $("#dup-new-mtime").textContent = file ? fileStamp(file) : "—";

    $("#dup-id").textContent = d.id;
    $("#dup-name").textContent = d.source_name || "—";
    $("#dup-size").textContent = fileSize(d.bytes);
    $("#dup-who").textContent =
      [d.site, d.phantom].filter(Boolean).join(" / ") || "unlabelled";
    $("#dup-operator").textContent = d.operator || "—";
    $("#dup-acquired").textContent =
      (d.acquired_at || "").slice(0, 16).replace("T", " ") || "not recorded";
    $("#dup-when").textContent =
      (d.created_at || "").slice(0, 16).replace("T", " ") || "—";
    $("#dup-stage").textContent = stageWords(d);
    $("#dup-status").textContent = d.status || "—";
    // Small on purpose: a few kB settles "is that my scan" on a link where the
    // full image costs fifteen seconds.
    $("#dup-thumb").src = `api/analyses/${encodeURIComponent(d.id)}`
                        + "/image.jpg?scale=200";

    const back = $("#dup-backdrop");
    back.classList.remove("hidden");
    $("#dup-open").focus();

    const done = (result) => {
      back.classList.add("hidden");
      $("#dup-open").onclick = null;
      $("#dup-again").onclick = null;
      $("#dup-cancel").onclick = null;
      back.onclick = null;
      document.onkeydown = null;
      resolve(result);
    };
    $("#dup-open").onclick = () => done("open");
    $("#dup-again").onclick = () => done("again");
    $("#dup-cancel").onclick = () => done("cancel");
    back.onclick = (e) => { if (e.target === back) done("cancel"); };
    document.onkeydown = (e) => { if (e.key === "Escape") done("cancel"); };
  });
}


/* The low-contrast circles are a rigid grid inside the block, so the block is
   the natural handle: place it once and all eight circles follow. */
async function placeBlock(payload) {
  try {
    const r = await postJSON(`api/analyses/${S.aid}/lowcontrast_block`,
                             { ...payload, expect_seq: S.history.seq });
    S.geometry.lowcontrast = r.lowcontrast;
    S.previewPending = false;
    noteHistory(r);
    // Every low-contrast ROI object was just replaced, so anything holding a
    // reference to one — the angle field, an open ROI panel — must re-read.
    S.lcAngleCommitted = Number(r.angle_deg);
    const f = $("#lc-angle");
    if (f) f.value = S.lcAngleCommitted.toFixed(1);
    if (S.selectedRoi && String(S.selectedRoi).startsWith("lowcontrast/")) {
      if (!findRoi(S.selectedRoi)) {
        S.selectedRoi = null; S.rotTargetId = null;
        const d = $("#roi-details");
        if (d) d.remove();
      } else if (S.rotTargetId === "lowcontrast/block") {
        // The details panel lives outside #stage-content, so it survives every
        // re-render and would keep the pre-edit angle on its slider. [ and ]
        // read that slider, so the next nudge would quietly undo the angle
        // just applied.
        const sl = $("#roi-angle"), num = $("#roi-angle-num");
        if (sl) sl.value = S.lcAngleCommitted;
        if (num) num.value = S.lcAngleCommitted.toFixed(1);
      }
    }
    status(`Low-contrast block placed at ${fmt(r.angle_deg, 1)}° — `
           + `all 8 circles moved with it`);
    draw();
    // The close-up has to follow the block. It did not: after a drag, a typed
    // angle or Turn 180° it went on showing the old placement until Refresh
    // was pressed — the one view meant for judging the placement, showing a
    // different one. Named by its content, so returning to an earlier
    // placement costs no transfer. Asked for through refreshBlockView, so a
    // run of quick nudges fetches the close-up of where the block ended up,
    // not one for every step on the way.
    refreshBlockView();
  } catch (e) {
    if (handleStaleGeometry(e)) return;
    status("Could not place block: " + e.message, true);
    // The local preview already moved the block; the server did not accept it,
    // so re-read rather than leave the two disagreeing.
    openAnalysis(S.aid);
  }
}

/* ================= validation (administrator sign-off) ================= */

const VAL_LABEL = {
  "": "pending review",
  validated: "validated",
  conditionally_validated: "conditionally validated",
  not_validated: "not validated",
};
const VAL_CLASS = {
  "": "na", validated: "pass",
  conditionally_validated: "warn", not_validated: "fail",
};

function valChip(st) {
  const s = st || "";
  return `<span class="chip ${VAL_CLASS[s] || "na"}">${VAL_LABEL[s] || s}</span>`;
}

/* Same administrator credential as deletion: it is the other decision an
   ordinary user must not be able to make. The approver's NAME is recorded
   separately, because a shared password cannot say who signed. */
function validationDialog(rec, submit) {
  return new Promise((resolve) => {
    const back = $("#val-backdrop");
    $("#val-target").textContent =
      `${rec.id} · ${[rec.site, rec.phantom].filter(Boolean).join(" / ") || "unlabelled"}`;
    const cur = rec.validation_status || "";
    document.querySelectorAll('input[name="vstatus"]').forEach(
      r => { r.checked = (r.value === cur); });
    if (!document.querySelector('input[name="vstatus"]:checked')) {
      document.querySelector('input[name="vstatus"][value="validated"]').checked = true;
    }
    $("#v-by").value = rec.validated_by || "";
    $("#v-comment").value = rec.validation_comment || "";
    $("#v-pw").value = "";
    $("#val-error").textContent = "";
    back.classList.remove("hidden");
    $("#v-by").focus();

    // The panel owns the submit, for the same reason the delete panel does: a
    // wrong administrator password is a 401, and letting that reach the shared
    // 401 handler threw the operator out to the sign-in page mid-signoff, with
    // the approver name and comment they had typed lost and a throttle strike
    // recorded that they never saw.
    let busy = false;
    const done = (result) => {
      if (busy) return;
      back.classList.add("hidden");
      $("#v-save").onclick = null;
      $("#v-cancel").onclick = null;
      back.onclick = null;
      document.onkeydown = null;
      resolve(result);
    };
    $("#v-save").onclick = async () => {
      const sel = document.querySelector('input[name="vstatus"]:checked');
      const status = sel ? sel.value : "";
      const by = $("#v-by").value.trim();
      if (status && !by) {
        $("#val-error").textContent =
          "The name of the person approving is required.";
        return;
      }
      if (!$("#v-pw").value) {
        $("#val-error").textContent = "The administrator password is required.";
        return;
      }
      busy = true;
      $("#v-save").disabled = true;
      $("#val-error").textContent = "Recording…";
      try {
        const r = await submit({
          status, validated_by: by, comment: $("#v-comment").value.trim(),
          admin_password: $("#v-pw").value });
        busy = false;
        done(r);
      } catch (e) {
        busy = false;
        $("#v-save").disabled = false;
        $("#val-error").textContent = e.message;
        $("#v-pw").select();
      }
    };
    $("#v-cancel").onclick = () => done(null);
    back.onclick = (e) => { if (e.target === back) done(null); };
    document.onkeydown = (e) => { if (e.key === "Escape") done(null); };
  });
}

async function setValidation(rec, onDone) {
  let policy = { enabled: true };
  try { policy = await api("api/validation_policy"); } catch (e) { /* noop */ }
  if (!policy.enabled) {
    alert("Validation requires an administrator password.\n\nAn administrator "
      + "must set PHANTOMQA_ADMIN_PASSWORD_HASH in .env\n"
      + "(python -m phantom_qa.manage set-admin-password).");
    return;
  }
  $("#v-save").disabled = false;
  const r = await validationDialog(rec, (vals) =>
    postJSON(`api/analyses/${rec.id}/validation`, vals, { adminAuth: true }));
  if (!r) return;
  status(`Recorded: ${VAL_LABEL[r.validation_status] || "pending review"}`
         + (r.validated_by ? ` (${r.validated_by})` : ""));
  if (onDone) onDone(r);
}

/* ================= integrity & deletion ================= */

async function verifyAnalysis(aid) {
  status("Re-hashing the stored source file…");
  try {
    const r = await api(`api/analyses/${aid}/verify`);
    const msg = {
      ok: `✔ Verified — the stored file still matches the SHA-256 recorded at `
        + `analysis time.\n\nSHA-256:\n${r.stored_sha256}\n\n`
        + `File: ${r.source_name} (${(r.size_bytes / 1048576).toFixed(1)} MB)`,
      mismatch: `✘ MISMATCH — the stored file no longer matches the hash `
        + `recorded at analysis time. Do not rely on these results.\n\n`
        + `recorded: ${r.stored_sha256}\ncomputed: ${r.computed_sha256}`,
      missing_file: `⚠ The stored source file is missing, so the results can no `
        + `longer be traced back to their input.\n\nrecorded: ${r.stored_sha256}`,
    }[r.status] || r.message;
    alert(msg);
    status(r.status === "ok" ? "Integrity verified." : "Integrity check FAILED.",
           r.status !== "ok");
  } catch (e) { status("Verification failed: " + e.message, true); }
}

/* Deleting destroys the stored source file, the edit history and — when this
   was the last analysis of its phantom — that phantom's stored measuring-point
   layout. It needs the ADMIN password and a written reason.

   A panel with real fields, not a chain of prompt() boxes. The old flow asked
   for the id, then the password, then the reason, and every refusal came back
   as a status line that cleared itself after six seconds. An operator who
   mistyped the password saw the row still there, assumed the browser had not
   caught up, and on re-uploading the same file was offered "open the existing
   analysis" — which brought back the very marks they thought they had
   deleted. Here the target stays on screen while they type and the refusal is
   shown in place until they deal with it. */
function deleteDialog(rec, impact, submit) {
  return new Promise((resolve) => {
    const back = $("#del-backdrop");
    const text = (id, v) => { $(id).textContent = v || "—"; };
    // textContent throughout: site, phantom and the file name are operator-
    // supplied and must never be parsed as markup.
    text("#del-id", rec.id);
    text("#del-who", [rec.site, rec.phantom].filter(Boolean).join(" / ")
                     || "unlabelled");
    text("#del-acquired", (rec.acquired_at || "").slice(0, 16)
                          || "not recorded by the scanner");
    text("#del-uploaded", (rec.created_at || "").slice(0, 16));
    text("#del-source", rec.source_name);
    text("#del-status", rec.status || "-");

    const warn = $("#del-layout-warn");
    if (impact && impact.layout_would_be_deleted) {
      warn.textContent =
        `⚠ This is the last analysis of phantom “${impact.phantom}”. Its stored `
        + `measuring-point layout will be deleted too, so the next scan of that `
        + `phantom starts from automatic detection again.`;
      warn.classList.remove("hidden");
    } else {
      warn.textContent = "";
      warn.classList.add("hidden");
    }

    $("#del-reason").value = "";
    $("#del-pw").value = "";
    $("#del-error").textContent = "";
    $("#del-confirm").disabled = false;
    back.classList.remove("hidden");
    $("#del-reason").focus();

    // While the request is in flight the panel refuses to close. Otherwise
    // Escape would report "nothing was deleted" to an operator whose deletion
    // was, at that moment, succeeding.
    let busy = false;
    const done = (result) => {
      if (busy) return;
      back.classList.add("hidden");
      $("#del-confirm").onclick = null;
      $("#del-cancel").onclick = null;
      back.onclick = null;
      document.onkeydown = null;
      resolve(result);
    };
    const minChars = (impact && impact.min_reason_chars) || 5;
    $("#del-confirm").onclick = async () => {
      const reason = $("#del-reason").value.trim();
      if (reason.length < minChars) {
        $("#del-error").textContent =
          `Give a reason of at least ${minChars} characters — it is the only `
          + "record of why this data was destroyed.";
        return;
      }
      if (!$("#del-pw").value) {
        $("#del-error").textContent = "The administrator password is required.";
        return;
      }
      // The panel stays open until the server actually accepts. A refusal —
      // wrong password, throttled, reason too short — is shown here rather
      // than as a status line that fades, so the deletion can never appear to
      // have happened when it did not.
      busy = true;
      $("#del-confirm").disabled = true;
      $("#del-error").textContent = "Deleting…";
      try {
        const r = await submit({ admin_password: $("#del-pw").value, reason });
        busy = false;
        done(r);
      } catch (e) {
        busy = false;
        $("#del-error").textContent = e.message;
        $("#del-confirm").disabled = false;
        $("#del-pw").select();
      }
    };
    $("#del-cancel").onclick = () => done(null);
    back.onclick = (e) => { if (e.target === back) done(null); };
    document.onkeydown = (e) => { if (e.key === "Escape") done(null); };
  });
}

/* The re-run panel. Refusals are shown in place rather than closing it, so a
   mistyped password does not cost the choices already made. */
function rerunDialog({ needsAdmin, warnings, target = "" }) {
  return new Promise((resolve) => {
    const back = $("#rerun-backdrop");
    $("#rerun-target").textContent = target;
    $("#rerun-warnings").innerHTML = warnings.length
      ? `<div class="reasons-why"><ul>`
        + warnings.map(w => `<li>${w}</li>`).join("") + `</ul></div>`
      : "";
    $("#rerun-admin").classList.toggle("hidden", !needsAdmin);
    $("#rerun-error").textContent = "";
    if (needsAdmin) { $("#rerun-reason").value = ""; $("#rerun-pw").value = ""; }
    back.classList.remove("hidden");

    const done = (vals) => {
      back.classList.add("hidden");
      document.onkeydown = null;
      resolve(vals);
    };
    $("#rerun-go").onclick = () => {
      const start = (document.querySelector('input[name="rerun-start"]:checked')
                     || {}).value || "results";
      if (needsAdmin) {
        const reason = $("#rerun-reason").value.trim();
        if (reason.length < 5) {
          $("#rerun-error").textContent =
            "Give a reason of at least 5 characters — it goes in the audit log.";
          return;
        }
        if (!$("#rerun-pw").value) {
          $("#rerun-error").textContent = "The administrator password is required.";
          return;
        }
        done({ start, reason, admin_password: $("#rerun-pw").value });
        return;
      }
      done({ start });
    };
    $("#rerun-cancel").onclick = () => done(null);
    back.onclick = (e) => { if (e.target === back) done(null); };
    document.onkeydown = (e) => { if (e.key === "Escape") done(null); };
  });
}

/* Measuring a stored scan again.

   Three starting points, because the reason for re-running decides how much
   should survive it: a new algorithm wants only the numbers redone, a
   mis-placed disc wants the measuring points revisited, and a registration
   that found the wrong outline wants everything. The file is never sent
   again — it is already on the server, which is the difference between this
   and the re-upload it replaces.

   Reached from step F with the open record, and from a History row with that
   row — which carries the same protection list, so the same warnings and the
   same password prompt appear either way. Resolves true once the re-run has
   started, false when the server refused it, and null when the operator
   backed out of the panel. */
async function rerunAnalysis(aid, rec = S.record) {
  const r = rec || {};
  const protection = r.protection || [];
  const warnings = [];
  if (protection.includes("finalized"))
    warnings.push("This analysis was finalised. Re-running reopens it.");
  if (protection.includes("signed_off"))
    warnings.push(`It was signed off${r.validated_by
      ? ` by ${html_escape(r.validated_by)}` : ""}. That ruling will be `
      + `withdrawn — it was given for the numbers about to be replaced, and `
      + `must be given again for the new ones.`);
  if (r.is_baseline)
    warnings.push("This is the reference scan for its phantom, so every "
      + "comparison against it will change. It stays the reference.");

  const vals = await rerunDialog({
    needsAdmin: protection.length > 0,
    warnings,
    target: [aid, [r.site, r.phantom].filter(Boolean).join(" / ") || "unlabelled",
             r.source_name].filter(Boolean).join(" · "),
  });
  if (!vals) return null;
  // Starting from registration is seconds of work before the answer comes
  // back, and from a History row nothing else on screen changes meanwhile. A
  // silent wait is what makes an operator press re-run a second time.
  status("Starting the re-run…", false, true);
  let out;
  try {
    out = await postJSON(`api/analyses/${aid}/rerun`, vals,
                         { adminAuth: true, timeoutMs: analysisTimeoutMs() });
  } catch (e) {
    status("Re-run refused: " + e.message, true, true);
    return false;
  }
  status(`Re-running from ${vals.start}. The previous results are kept as `
         + `revision ${out.revision} — use “Undo re-run” if this was a `
         + `mistake.`, false, true);
  // The server writes the step it reopened (out.stage: A, or C when the
  // measuring points were kept) onto the record, and openAnalysis opens a
  // record at its stored step — so the page lands where the server says,
  // through the same rule every other opening goes through.
  try {
    await openAnalysis(aid);
  } catch (e) {
    // The re-run itself went through; only fetching the record failed, which
    // on a field link is a dropped connection. Saying "refused" would send
    // the operator to re-run a second time.
    status(`The re-run has started, but the analysis could not be opened `
           + `(${e.message}). Open it from History to carry on.`, true, true);
  }
  return true;
}

async function undoRerun(aid) {
  try {
    const out = await postJSON(`api/analyses/${aid}/rerun/cancel`, {});
    status(`Restored the previous results (revision ${out.restored}).`);
    await openAnalysis(aid);
  } catch (e) {
    status(e.message, true, true);
  }
}

/* The quality check's verdict, read the way the server reads it: only "poor"
   refuses. A record analysed before the check existed carries no verdict and
   is never refused — the check judges what it measured, nothing more. */
function qualityRefusesReference(q) {
  return !!q && q.verdict === "poor";
}

/* Each failed check with what it measured. A refusal an operator cannot
   understand is a refusal they will work around, and an administrator asked
   to overrule one has to see exactly what they are overruling. */
function failedChecksList(q) {
  const failed = ((q && q.checks) || []).filter(c => !c.ok);
  if (!failed.length) return "";
  return `<ul>${failed.map(c =>
    `<li><b>${html_escape(c.label || c.id || "")}</b> — `
    + `${html_escape(c.detail || "")}</li>`).join("")}</ul>`;
}

/* The administrator's way past the image-quality check.

   A scan that failed the check can still be analysed, but it may not become
   the reference or set a phantom's measuring points on an ordinary user's
   say-so. Sometimes it has to anyway — the one exposure a site could make that
   week, looked at by someone who knows what they are looking at. Then it is an
   administrator's decision, made with the failed checks in front of them, and
   the reason goes in the audit log beside those checks.

   The same shape as the delete and re-run panels: a refusal — a mistyped
   password, a reason too short — is shown in place, and the panel stays open
   until the server has actually accepted. Resolves the server's answer, or
   null when called off or impossible; either way it has already said so. */
async function adminOverride({ title, intro, quality, confirmLabel, send,
                               cancelled }) {
  // Asked first, so nobody types a reason and a password into a panel whose
  // only possible answer is that no administrator password is configured.
  let policy = { enabled: true };
  try { policy = await api("api/validation_policy"); } catch (e) { /* noop */ }
  if (!policy.enabled) {
    status("Only an administrator can approve this, and no administrator "
           + "password is configured on this installation. An administrator "
           + "must set PHANTOMQA_ADMIN_PASSWORD_HASH in .env (python -m "
           + "phantom_qa.manage set-admin-password).", true, true);
    return null;
  }
  return new Promise((resolve) => {
    const back = $("#override-backdrop");
    $("#override-title").textContent = title;
    $("#override-body").innerHTML = `<p>${intro}</p>`
      + (quality && quality.summary
         ? `<p class="hint">${html_escape(quality.summary)}</p>` : "")
      + failedChecksList(quality);
    $("#override-go").textContent = confirmLabel;
    $("#override-go").disabled = false;
    $("#override-reason").value = "";
    $("#override-pw").value = "";
    $("#override-error").textContent = "";
    back.classList.remove("hidden");
    $("#override-reason").focus();

    // While the request is in flight the panel refuses to close, so Escape
    // cannot report "nothing changed" about a decision that is, at that
    // moment, being recorded.
    let busy = false;
    const done = (result) => {
      if (busy) return;
      back.classList.add("hidden");
      $("#override-body").innerHTML = "";
      $("#override-go").onclick = null;
      $("#override-cancel").onclick = null;
      back.onclick = null;
      document.onkeydown = null;
      if (!result && cancelled) status(cancelled);
      resolve(result);
    };
    $("#override-go").onclick = async () => {
      const reason = $("#override-reason").value.trim();
      if (reason.length < 5) {
        $("#override-error").textContent =
          "Give a reason of at least 5 characters — it goes in the audit log "
          + "beside the checks that failed.";
        return;
      }
      if (!$("#override-pw").value) {
        $("#override-error").textContent =
          "The administrator password is required.";
        return;
      }
      busy = true;
      $("#override-go").disabled = true;
      $("#override-error").textContent = "Recording…";
      try {
        const r = await send({ admin_password: $("#override-pw").value, reason });
        busy = false;
        done(r);
      } catch (e) {
        busy = false;
        $("#override-error").textContent = e.message;
        $("#override-go").disabled = false;
        $("#override-pw").select();
      }
    };
    $("#override-cancel").onclick = () => done(null);
    back.onclick = (e) => { if (e.target === back) done(null); };
    document.onkeydown = (e) => { if (e.key === "Escape") done(null); };
  });
}

/* Making a scan that failed the quality check the reference anyway. */
function overrideBaseline(aid, quality) {
  return adminOverride({
    title: "Make this scan the reference anyway?",
    intro: "This image did not pass the quality check. As the reference, every "
         + "later scan of its phantom on this protocol would be compared "
         + "against it. Go ahead only if you have looked at the image and it "
         + "is the best reference available.",
    quality,
    confirmLabel: "Make it the reference",
    cancelled: "Nothing was changed — the phantom's reference is as it was.",
    send: (vals) => postJSON(`api/analyses/${aid}/baseline`,
                             { baseline: true, ...vals }, { adminAuth: true }),
  });
}

/* A yes/no question with enough detail on it to answer.

   Escape and a click outside both mean no, because the safe answer must be the
   easy one. Resolves true only if the confirming button was pressed. */
function confirmPanel({ title, body, confirmLabel }) {
  return new Promise((resolve) => {
    const back = $("#confirm-backdrop");
    $("#confirm-title").textContent = title;
    $("#confirm-body").innerHTML = body;
    $("#confirm-yes").textContent = confirmLabel;
    back.classList.remove("hidden");
    $("#confirm-yes").focus();

    const done = (answer) => {
      back.classList.add("hidden");
      $("#confirm-body").innerHTML = "";
      document.onkeydown = null;
      resolve(answer);
    };
    $("#confirm-yes").onclick = () => done(true);
    $("#confirm-no").onclick = () => done(false);
    back.onclick = (e) => { if (e.target === back) done(false); };
    document.onkeydown = (e) => { if (e.key === "Escape") done(false); };
  });
}

/* Throwing away work nobody has committed to yet.

   No password and no written reason — that weight is what made operators
   delete and re-upload the same file four times in an afternoon. What it does
   ask for is a moment's attention, because the file really does go: on a field
   link, uploading it again is minutes. */
async function discardAnalysis(aid, rec, impact) {
  const what = rec.source_name ? html_escape(rec.source_name) : aid;
  const layout = impact.layout_would_be_deleted
    ? `<p class="hint">This is the only analysis of phantom
       <b>${html_escape(impact.phantom || "")}</b>, so its stored measuring
       points go with it. The next scan of that phantom starts from automatic
       detection again.</p>`
    : "";
  const ok = await confirmPanel({
    title: "Discard this unfinished analysis?",
    body: `<p><b>${what}</b>${impact.created_at
             ? ` · uploaded ${html_escape(impact.created_at.slice(0, 16))}` : ""}
           ${impact.has_results ? " · measured, not finalised" : " · not yet measured"}</p>
       ${layout}
       <p class="hint">The scan file is removed from the server too, so
         analysing it again means uploading it again. To correct labels,
         corners or measuring points instead, close this and use Edit or
         Manual corners.</p>`,
    confirmLabel: "Discard",
  });
  if (!ok) { status("Nothing was discarded."); return; }
  try {
    const r = await postJSON(`api/analyses/${aid}/discard`, { confirm: true });
    // What happened to the phantom's shared marks is not visible anywhere
    // else, and it changes where the next scan of that phantom starts.
    status(`Discarded ${what}.`
           + (r.layout_restored
              ? ` The measuring points for phantom ${r.phantom} went back to `
                + `the ones stored before it.`
              : r.layout_deleted
                ? ` The stored measuring points for phantom ${r.phantom} went `
                  + `with it — the next scan starts from detection again.`
                : ""));
  } catch (e) {
    // Someone finalised it between opening this and confirming.
    status(e.message, true, e.status === 409);
    if (e.status !== 409) return;
  }
  const remembered = rememberedAnalysis();
  if (remembered && remembered.aid === aid) forgetOpenAnalysis();
  if (S.aid === aid) { clearAnalysisState(); setStage("U"); draw(); }
  loadHistory();
}

async function deleteAnalysis(aid) {
  // One small request decides which of the two conversations this is, and
  // carries what the panel shows. It used to fetch the whole record — 195 kB
  // measured — to read about a hundred bytes of it, which on a field link is
  // three seconds of nothing before the dialog appears.
  let rec = (H.rows || []).find(x => x.id === aid) || { id: aid };
  let impact = { min_reason_chars: 5, mode: "admin" };
  try {
    impact = await api(`api/analyses/${aid}/delete_impact`);
    rec = { ...rec, source_name: impact.source_name || rec.source_name,
            created_at: impact.created_at || rec.created_at };
  } catch (e) { /* fall back to what History already knows */ }

  if (impact.mode === "disabled") {
    alert("This analysis has been finalised, so removing it needs the "
      + "administrator password — and none is configured on this "
      + "installation.\n\nAn administrator must set "
      + "PHANTOMQA_ADMIN_PASSWORD_HASH in .env\n"
      + "(python -m phantom_qa.manage set-admin-password).");
    return;
  }
  if (impact.mode === "confirm") { await discardAnalysis(aid, rec, impact); return; }

  const r = await deleteDialog(rec, impact, (vals) =>
    postJSON(`api/analyses/${aid}/delete`, vals, { adminAuth: true }));
  if (!r) { status("Nothing was deleted."); return; }
  status(`Analysis ${aid} deleted.`
         + (r.layout_deleted
            ? ` The stored measuring-point layout for phantom ${r.phantom} `
              + `was removed with it.`
            : ""));
  // Deleting from History need not be the analysis currently open, so the
  // remembered note is cleared on its own account — otherwise the next reload
  // offers to continue a record that no longer exists.
  const remembered = rememberedAnalysis();
  if (remembered && remembered.aid === aid) forgetOpenAnalysis();
  if (S.aid === aid) {
    clearAnalysisState();
    setStage("U");
    draw();
  }
  loadHistory();
}

/* ================= wizard stages ================= */

function setStage(st) {
  // An angle typed but not applied has already been drawn locally. Leaving the
  // step with that preview standing would show geometry the server does not
  // have — and step C, re-entered, would read the previewed angle back out of
  // S.geometry as if it had been committed. Resync instead of guessing.
  const leavingWithPreview = S.stage === "C" && st !== "C" && S.previewPending;
  // Points placed but not applied belong to the step they were placed on. A
  // half-placed set of block corners must not sit waiting, Apply button and
  // all, over step D — where pressing it would move the block after the
  // measuring points were confirmed. Leaving the step drops them unsent.
  if (st !== S.stage && PICKERS[S.mode]) endPicking();
  S.stage = st;
  document.querySelectorAll("#stage-nav li").forEach(li => {
    const s = li.dataset.stage;
    li.classList.toggle("active", s === st);
    li.classList.toggle("done",
      STAGES.indexOf(s) < STAGES.indexOf(st) && s !== "U");
  });
  const rd = $("#roi-details");
  if (rd && st !== "C") { rd.remove(); S.rotTargetId = null; }
  renderIdentityBar();
  renderStage();
  draw();
  rememberOpenAnalysis();
  if (leavingWithPreview) discardPreview();
}

/* Drop an uncommitted local preview by re-reading the stored geometry. Cheap,
   because it only runs when a preview is actually outstanding. */
async function discardPreview() {
  S.previewPending = false;
  if (!S.aid) return;
  try {
    const rec = await api(`api/analyses/${S.aid}`);
    S.geometry = rec.geometry;
    S.history = rec.history || S.history;
    renderStage();
    draw();
  } catch (e) { /* leave the local copy; the next edit will resync */ }
}

function renderStage() {
  const c = $("#stage-content");
  const draw = { U: stageU, A: stageA, B: stageB, C: stageC, D: stageD,
                 E: stageE, F: stageF }[S.stage];
  // The step functions are async, so a failure inside one is a rejected
  // promise nobody was waiting on: the browser swallows it and the half-drawn
  // step stays on screen. Catch both shapes and show something actionable.
  try {
    const running = draw(c);
    if (running && typeof running.catch === "function") {
      running.catch((e) => stepFailed(c, e));
    }
  } catch (e) {
    stepFailed(c, e);
  }
}

/* ---- Stage U: upload (choose file, fill identity, then confirm) ---- */
/* How long ago, in words an operator reads faster than a timestamp. */
function agoWords(iso) {
  const then = Date.parse(iso || "");
  if (!then) return "";
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

/* The analysis this browser had open when the window was last closed.

   Drawn entirely from what was remembered locally — no request is made, and
   nothing is resumed, until the operator presses a button. That is the whole
   safety property: an analysis that misbehaved cannot re-enter the state it
   misbehaved in merely because the page was reloaded. */
function resumeBanner() {
  const m = rememberedAnalysis();
  if (!m) return "";
  const who = m.operator ? ` · started by ${html_escape(m.operator)}` : "";
  const what = m.phantom ? ` · phantom ${html_escape(m.phantom)}` : "";
  return `<div class="reasons-why" id="resume-box">
    <b>You have an unfinished analysis on this computer.</b>
    <p>${html_escape(m.source_name || "(scan)")}${what}${who} ·
       reached step ${html_escape(m.stage || "A")} · ${agoWords(m.saved_at)}</p>
    <p class="hint">It is stored on the server — continuing costs nothing, and
      the file never needs uploading again.</p>
    <button class="primary" id="btn-resume">Continue this analysis</button>
    <button class="secondary" id="btn-forget-resume">Not mine / hide</button>
  </div>`;
}

function wireResumeBanner() {
  const go = $("#btn-resume");
  if (go) {
    go.addEventListener("click", async () => {
      const m = rememberedAnalysis();
      if (!m) return;
      try {
        await openAnalysis(m.aid);
      } catch (e) {
        // Deleted, or on a different server than this browser remembers.
        forgetOpenAnalysis();
        const box = $("#resume-box");
        if (box) box.remove();
        status("That analysis is no longer on the server.", true);
      }
    });
  }
  const hide = $("#btn-forget-resume");
  if (hide) {
    // Only forgets the note in this browser. The analysis itself is untouched
    // and still in History — this button must never destroy someone's work,
    // least of all on a machine two operators share.
    hide.addEventListener("click", () => {
      forgetOpenAnalysis();
      const box = $("#resume-box");
      if (box) box.remove();
    });
  }
}

/* Everything started and never measured, from the server — so work handed
   between machines is findable, which a browser-local note cannot do. Under
   one shared account this is everyone's list, and it says so. */
async function renderUnfinishedList() {
  const box = $("#unfinished-box");
  if (!box) return;
  try {
    const r = await api("api/analyses?unfinished_only=true&limit=5");
    const rows = r.analyses || [];
    if (!rows.length) { box.innerHTML = ""; return; }
    box.innerHTML = `<h3>Unfinished analyses</h3>
      <p class="hint">Started but never measured, by anyone using this
        installation. Continue one instead of uploading its file again.</p>
      <table><tr><th>scan</th><th>phantom</th><th>operator</th>
        <th>uploaded</th><th></th></tr>
      ${rows.map(a => `<tr>
        <td>${html_escape(a.source_name || "")}</td>
        <td>${a.phantom ? html_escape(a.phantom) : "<span class='hint'>—</span>"}</td>
        <td>${a.operator ? html_escape(a.operator) : "<span class='hint'>—</span>"}</td>
        <td class="hint">${agoWords(a.created_at)}</td>
        <td><a href="#" class="resume-one" data-id="${a.id}">continue</a></td>
      </tr>`).join("")}</table>`;
    box.querySelectorAll("a.resume-one").forEach(a =>
      a.addEventListener("click", (e) => {
        e.preventDefault();
        openAnalysis(a.dataset.id).catch(() =>
          status("That analysis is no longer on the server.", true));
      }));
  } catch (e) {
    box.innerHTML = "";          // never let this block starting a new scan
  }
}

async function stageU(c) {
  c.innerHTML = `<h2>New analysis</h2>
    ${resumeBanner()}
    <div id="unfinished-box"></div>
    <p class="hint">DICOM file (preferred), zipped DICOM CD export, or a plain
    image (reduced precision). Nothing is uploaded until you press
    <b>Upload &amp; analyse</b>, so you can set the file and the labels in any
    order.</p>

    <h3>1 · Choose the scan file</h3>
    <div class="drop-zone" id="drop">Drop file here or click to choose</div>
    <input type="file" id="file-input" class="hidden">
    <div id="file-chosen" class="chosen hidden"></div>

    <h3>2 · Identify this scan</h3>
    <p class="hint">Site and phantom are how analyses are grouped for trending.
    Use the same spelling every time — previous values appear as suggestions.
    You can still change these later from the identity bar above.</p>
    <div class="form-grid">
      <label>Site <input id="up-site" list="dl-site" placeholder="e.g. Goma Hospital"></label>
      <label>Phantom <input id="up-phantom" list="dl-phantom" placeholder="e.g. MSF-01"></label>
      <label>Operator <input id="up-operator" placeholder="optional"></label>
      <label>Notes <input id="up-notes" placeholder="optional"></label>
    </div>
    <datalist id="dl-site"></datalist>
    <datalist id="dl-phantom"></datalist>
    <p id="up-warn" class="hint"></p>

    <h3>3 · Confirm</h3>
    <button class="primary" id="btn-upload" disabled>Upload &amp; analyse</button>
    <button class="secondary" id="btn-clear-file">Clear file</button>
    <div id="upload-progress" class="upload-progress hidden">
      <div class="upload-track"><div id="upload-bar"></div></div>
      <div class="upload-row">
        <span id="upload-text"></span>
        <button class="secondary-sm" id="upload-cancel">Cancel</button>
      </div>
    </div>`;

  // Wired before anything is awaited: on a slow link the labels round-trip
  // takes seconds, and the resume button has to work the moment it is visible.
  wireResumeBanner();
  renderUnfinishedList();

  try {
    const lab = await api("api/labels");
    const opts = (items) => (items || [])
      .map(s => `<option value="${s.value.replace(/"/g, "&quot;")}">`).join("");
    $("#dl-site").innerHTML = opts(lab.site);
    $("#dl-phantom").innerHTML = opts(lab.phantom);
  } catch (e) { /* first run: no labels yet */ }

  // remember the last used labels so a batch of scans is not retyped
  ["site", "phantom", "operator"].forEach(k => {
    const v = sessionStorage.getItem("lbl_" + k);
    if (v) $("#up-" + k).value = v;
  });

  const drop = $("#drop"), inp = $("#file-input");
  const refresh = () => {
    const f = S.pendingFile;
    const box = $("#file-chosen");
    box.classList.toggle("hidden", !f);
    if (f) {
      // The modified time is here because the name is not an identifier: a
      // machine exports every scan as 003_0000.dcm, and picking the wrong one
      // of two is invisible until minutes of upload have already been paid.
      box.textContent = "";
      const name = document.createElement("b");
      name.textContent = f.name;
      box.appendChild(name);
      box.appendChild(document.createTextNode(
        ` · ${fileSize(f.size)} · modified ${fileStamp(f)}`));
    }
    $("#btn-upload").disabled = !f;
    const noLabel = !$("#up-site").value.trim() && !$("#up-phantom").value.trim();
    $("#up-warn").innerHTML = noLabel
      ? '<span style="color:var(--warn)">⚠ Without a site or phantom this '
        + 'analysis will not appear in any grouped trend.</span>'
      : "";
  };

  drop.addEventListener("click", () => inp.click());
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("armed"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("armed"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault(); drop.classList.remove("armed");
    if (e.dataTransfer.files.length) { S.pendingFile = e.dataTransfer.files[0]; refresh(); }
  });
  inp.addEventListener("change", () => {
    if (inp.files.length) { S.pendingFile = inp.files[0]; refresh(); }
  });
  ["up-site", "up-phantom"].forEach(id =>
    $("#" + id).addEventListener("input", refresh));
  $("#btn-clear-file").addEventListener("click", () => {
    S.pendingFile = null; inp.value = ""; refresh();
  });
  $("#btn-upload").addEventListener("click", () => {
    if (S.pendingFile) uploadFile(S.pendingFile);
  });
  $("#upload-cancel").addEventListener("click", cancelUpload);
  refresh();
}

/* The first phase of a packed upload: the browser is packing the file and
   nothing has been sent yet.

   Named, and given the bar, because the packing takes a few seconds on an old
   laptop, and a few seconds of an unexplained still screen before a transfer
   is how the old silent upload came to be reloaded and sent twice. Cancel is
   offered here too — stopping now costs nothing at all. */
function showPreparing(done, total) {
  const box = $("#upload-progress");
  if (!box) return;
  box.classList.remove("hidden");
  const bar = $("#upload-bar"), text = $("#upload-text");
  const pct = total > 0 ? Math.min(100, (done / total) * 100) : 0;
  bar.style.width = pct.toFixed(1) + "%";
  bar.classList.remove("working");
  text.textContent = "Preparing the file…"
                   + (done > 0 ? ` (${pct.toFixed(0)}%)` : "")
                   + " — nothing is sent yet";
  const cancel = $("#upload-cancel");
  if (cancel) cancel.disabled = false;
}

/* The upload's own progress, which the six-second status line could not be.

   Deliberately not the status line: that clears itself, and a message that
   evaporates during a two-minute transfer is exactly what made an upload in
   progress look like a dead screen. This stays until the upload ends.

   `packedFrom` is the original size when the file travels packed. The
   numbers, the percentage and the time left are all counted on the bytes
   actually crossing the link, because those decide how long it takes; the
   original size is said beside them so that "2.2 MB" for a 7.5 MB scan does
   not look like the wrong file being sent. */
function showUploadProgress(loaded, total, startedAt, packedFrom) {
  const box = $("#upload-progress");
  if (!box) return;
  box.classList.remove("hidden");
  const bar = $("#upload-bar"), text = $("#upload-text");

  if (loaded < 0) {
    // Every byte is sent; the server is now decoding and registering it.
    // Named as its own phase so a bar parked at 100% does not read as stuck.
    bar.style.width = "100%";
    bar.classList.add("working");
    text.textContent = "Sent — the server is reading the scan…";
    const cancel = $("#upload-cancel");
    // Past the point where stopping would help: the bytes are already there.
    if (cancel) cancel.disabled = true;
    return;
  }
  const pct = total > 0 ? Math.min(100, (loaded / total) * 100) : 0;
  bar.style.width = pct.toFixed(1) + "%";
  bar.classList.remove("working");
  const left = timeRemaining(loaded, total, startedAt);
  const packed = packedFrom ? `, packed from ${fileSize(packedFrom)}` : "";
  text.textContent = `Sending ${fileSize(loaded)} of ${fileSize(total)} `
                   + `(${pct.toFixed(0)}%${packed})${left ? " · " + left : ""}`;
}

function hideUploadProgress() {
  const box = $("#upload-progress");
  if (box) box.classList.add("hidden");
  const cancel = $("#upload-cancel");
  if (cancel) cancel.disabled = false;
  S.uploadXhr = null;
  S.packJob = null;
}

function cancelUpload() {
  // While the file is still being packed nothing has been sent, so stopping
  // the packing is the whole of the cancellation: uploadFile sees the flag
  // before it builds the request and sends nothing at all.
  const job = S.packJob;
  if (job) {
    job.cancelled = true;
    if (job.reader) job.reader.cancel().catch(() => {});
  }
  if (S.uploadXhr) S.uploadXhr.abort();
}

/* The SHA-256 of a chosen file, or null when the browser will not say.

   crypto.subtle exists only in a secure context, so over plain http on a local
   network — which is how this is deployed in some places — there is no digest
   to be had. Returning null then is deliberate: the pre-flight check is an
   optimisation, and the server still checks the bytes it receives, so losing
   it costs a transfer and nothing else. Packing needs the digest too, so
   without one the file also travels as it is — exactly as it always has. */
async function fileSha256(file) {
  try {
    if (!window.crypto || !crypto.subtle || !file.arrayBuffer) return null;
    const digest = await crypto.subtle.digest("SHA-256",
                                              await file.arrayBuffer());
    return Array.from(new Uint8Array(digest))
      .map(b => b.toString(16).padStart(2, "0")).join("");
  } catch (e) {
    return null;                       // insecure context, or no memory for it
  }
}

/* Ask the server whether it already holds this file, before sending it.

   This is the whole point of the change: the answer costs a hundred bytes and
   arrives at once, where the refusal it replaces cost two minutes of a
   512 kbit/s link to say the same thing. Any failure here falls through to
   the upload, which checks properly.

   `sha` is the digest uploadFile has already taken, so the file is not read
   and hashed a second time; left out, it is computed here. */
async function findExistingCopy(file, sha) {
  // A zip is checked member by member on the server; the container's hash
  // fingerprints nothing, so asking about it would always answer "no".
  if (/\.zip$/i.test(file.name || "")) return null;
  if (sha === undefined) sha = await fileSha256(file);
  if (!sha) return null;
  try {
    const r = await postJSON("api/upload_check", { sha256: [sha] });
    const hit = (r.duplicates || [])[0];
    return (hit && hit.duplicate_of && hit.duplicate_of[0]) || null;
  } catch (e) {
    return null;
  }
}

/* ---- Packing a scan before it is sent

   Most detectors write their pixels uncompressed, and on a 512 kbit/s link
   the transfer is nearly all of an operator's waiting. Measured on the real
   scans with the same gzip the browser uses: the field Fuji's 7.5 MB pack to
   2.9 MB (1.1 MB for an over-exposed one), two minutes becoming under fifty
   seconds; the Carestream's 15 MB pack to about 7. The Philips compresses
   inside the file already and gains nothing. Field projects meet a different
   detector every time, so the page decides per file, from the file itself,
   and never sends more than it would have sent unpacked.

   Packing is lossless. The server unpacks it back to the exact original and
   proves it with the SHA-256 taken here from the file on disk before
   packing; what is stored, fingerprinted and measured is the original, byte
   for byte. */

//: The probe: packed first, a fraction of a second even on an old laptop.
const PACK_PROBE_BYTES = 1048576;          // 1 MiB
//: Pack only when it saves at least a tenth. Below that the few seconds of
//: packing buy almost nothing, and a detector that compresses inside the file
//: (the Philips: 99 %) is recognised and sent as it is.
const PACK_WORTH_IT = 0.9;

/* Whether this browser can pack at all. CompressionStream is built in —
   no library to download over the link — but older browsers lack it, and
   they simply send the file as it is, as they always have. */
function canPack() {
  return typeof CompressionStream === "function"
      && typeof TransformStream === "function"
      && typeof Blob === "function"
      && typeof Blob.prototype.stream === "function";
}

/* The names of this computer itself: localhost, 127.x.x.x and ::1, which the
   browser writes in brackets. Nothing else — a LAN address or a server name
   is a network, however fast, and keeps packing. */
const LOOPBACK_HOST = /^(localhost|127(\.\d{1,3}){3}|\[::1\]|::1)$/;

/* Whether the page comes from a server on this same computer, which is how
   run_app.py is used. There is then no link to spare: packing a 15 MB scan
   was measured at half a second of pure waiting, most of it with the page
   frozen, to save nothing.

   The name alone cannot tell. An SSH tunnel or a port forward to a real
   server is opened at localhost too, and every scan would then cross the
   slow link unpacked. So the server must also have said that it has no
   sign-in: that is run_app.py's default, a server without sign-in must never
   be reachable from a network at all (both starters say so), and production
   turns sign-in on. A tunnel to a deployed server therefore keeps packing. */
function servedFromThisMachine() {
  return S.signInOff === true
      && LOOPBACK_HOST.test((location.hostname || "").toLowerCase());
}

/* One blob gzipped, or null when the operator cancelled first.

   Read chunk by chunk through a reader rather than handed to
   new Response(stream).blob(), because a reader can be stopped: it is left
   on the job for cancelUpload, and cancelling it stops the file being read
   as well. The counting step in front reports how much of the file has been
   packed, which is what the bar shows while preparing. */
async function gzipBlob(blob, job, onProgress) {
  let fed = 0;
  const counted = new TransformStream({
    transform(chunk, ctl) {
      fed += chunk.byteLength;
      if (onProgress) onProgress(fed, blob.size);
      ctl.enqueue(chunk);
    },
  });
  const reader = blob.stream().pipeThrough(counted)
    .pipeThrough(new CompressionStream("gzip")).getReader();
  job.reader = reader;
  const parts = [];
  try {
    for (;;) {
      const step = await reader.read();
      if (step.done || job.cancelled) break;
      parts.push(step.value);
    }
  } finally {
    job.reader = null;
    if (job.cancelled) reader.cancel().catch(() => {});
  }
  return job.cancelled ? null : new Blob(parts, { type: "application/gzip" });
}

/* What to send: { blob, packed }. The file as it is whenever packing cannot
   run or would not clearly pay; packed only when it saves at least a tenth.

   Every "no" is cheap. A zip is not even looked at — a CD export is
   compressed already. Otherwise only the first megabyte is packed as a probe,
   and a file whose start does not shrink is sent at once instead of spending
   seconds packing all of it to save nothing. Only then is the whole file
   packed, and the result is checked against the same rule. */
async function packForUpload(file, sha, job, onProgress) {
  const asIs = { blob: file, packed: false };
  if (/\.zip$/i.test(file.name || "")) return asIs;
  // No fingerprint, no packing. Without it the server could check only
  // gzip's own CRC32 and the length, which catch a damaged transfer but not
  // a wrong file; sending the file as it is keeps exactly today's guarantee
  // instead. This is the plain-http case, where crypto.subtle is missing.
  if (!sha || !canPack()) return asIs;
  // A packed upload from this page has already been refused as damaged. The
  // file as it is is the upload that always worked, so that is what is sent
  // until the page is reloaded.
  if (S.sendUnpacked) return asIs;
  // Page and server on the same computer: nothing travels over a link, so
  // packing would only be waited for. The fingerprint and the check before
  // sending have already run, exactly as for any other upload.
  if (servedFromThisMachine()) return asIs;
  try {
    if (onProgress) onProgress(0, file.size);
    const probe = file.slice(0, PACK_PROBE_BYTES);
    const probePacked = await gzipBlob(probe, job, null);
    if (!probePacked || probePacked.size > PACK_WORTH_IT * probe.size)
      return asIs;
    // A file no bigger than the probe has already been packed whole.
    const whole = file.size <= PACK_PROBE_BYTES ? probePacked
                : await gzipBlob(file, job, onProgress);
    if (!whole || whole.size > PACK_WORTH_IT * file.size) return asIs;
    return { blob: whole, packed: true };
  } catch (e) {
    // Out of memory, or a browser that has the pieces but not the whole:
    // packing is a saving, never a reason not to upload.
    return asIs;
  }
}

async function uploadFile(file, opts = {}) {
  const labels = {
    site: ($("#up-site") || {}).value.trim() || "",
    phantom: ($("#up-phantom") || {}).value.trim() || "",
    operator: ($("#up-operator") || {}).value.trim() || "",
    notes: ($("#up-notes") || {}).value.trim() || "",
  };
  ["site", "phantom", "operator"].forEach(
    k => sessionStorage.setItem("lbl_" + k, labels[k]));
  $("#btn-upload").disabled = true;

  // Hashed once and used twice: the pre-check below asks the server about
  // this digest, and a packed upload carries it so the server can prove it
  // unpacked exactly this file. A zip is neither pre-checked nor packed, so
  // it is not read for nothing.
  if (!opts.allowDuplicate)
    status("Checking whether this scan is already here…");
  const sha = /\.zip$/i.test(file.name || "") ? null
            : opts.sha !== undefined ? opts.sha : await fileSha256(file);

  if (!opts.allowDuplicate) {
    const existing = await findExistingCopy(file, sha);
    if (existing) {
      const choice = await duplicateDialog(existing, file);
      if (choice === "open") {
        S.pendingFile = null;
        await openAnalysis(existing.id);
        return;
      }
      if (choice !== "again") {
        status("Upload cancelled — the scan is already here.");
        if ($("#btn-upload")) $("#btn-upload").disabled = false;
        return;
      }
      opts = { ...opts, allowDuplicate: true };
    }
  }

  // The packing job is where Cancel reaches the preparing phase: a flag it
  // sets, and the reader it can stop.
  const job = { cancelled: false, reader: null };
  S.packJob = job;
  try {
    const sending = await packForUpload(
      file, sha, job, (done, total) => showPreparing(done, total));
    if (job.cancelled) {
      // Called off while packing, so nothing has been sent. Thrown in the
      // same shape as an aborted transfer, so the one branch below answers
      // both the same way.
      const err = new Error("Upload cancelled.");
      err.status = 0;
      err.cancelled = true;
      throw err;
    }
    S.packJob = null;

    const fd = new FormData();
    if (sending.packed) {
      // The part is named for what it is. The original's name, size and
      // fingerprint travel beside it; the server unpacks, checks all three
      // and stores the original.
      fd.append("file", sending.blob, file.name + ".gz");
      fd.append("encoding", "gzip");
      fd.append("original_sha256", sha);
      fd.append("original_size", String(file.size));
      fd.append("original_name", file.name);
    } else {
      fd.append("file", file);
    }
    Object.entries(labels).forEach(([k, v]) => fd.append(k, v));
    if (opts.allowDuplicate) fd.append("allow_duplicate", "true");

    const packedFrom = sending.packed ? file.size : 0;
    const startedAt = Date.now();
    showUploadProgress(0, sending.blob.size, startedAt, packedFrom);
    const r = await uploadWithProgress(
      "api/analyses", fd,
      (loaded, total) => showUploadProgress(loaded, total, startedAt,
                                            packedFrom));
    hideUploadProgress();
    const ok = r.analyses.filter(a => a.registered);
    if (!r.analyses.length) throw new Error("no images found");
    const notes = [];
    if (r.analyses.length > 1)
      notes.push(`${r.analyses.length} images found — opening the first; others `
                 + `are in History.`);
    if (r.phantom_profile)
      notes.push(`A stored measuring-point layout for phantom `
                 + `${r.phantom_profile.phantom} will be applied when you `
                 + `confirm the registration.`);
    // A different file carrying the same exposure — re-exported from the
    // archive. Not refused, because a repeated identifier can also come from a
    // mis-set detector; but left unsaid it would count twice in every trend.
    const echo = r.analyses.flatMap(a => a.same_exposure || []);
    if (echo.length)
      notes.push(`Note: analysis ${echo[0].id} was made from the same exposure `
                 + `(same DICOM identifier) in a different file. If that is a `
                 + `re-export of this scan, discard one of them.`);
    S.pendingFile = null;
    const first = ok[0] || r.analyses[0];
    await openAnalysis(first.id);
    if (notes.length) status(notes.join(" "));
  } catch (e) {
    hideUploadProgress();
    if (e.cancelled) {
      // Their own decision, not a failure. Said without alarm, and the file
      // stays chosen so a second try costs nothing but the press.
      status("Upload cancelled. The file is still selected.");
      if ($("#btn-upload")) $("#btn-upload").disabled = false;
      return;
    }
    // Reached when the pre-flight check could not run — an insecure context,
    // or a zip whose members are only hashed on the server. Same dialog, same
    // choices; only the transfer was not saved.
    if (e.duplicateOf && e.duplicateOf.length) {
      const choice = await duplicateDialog(e.duplicateOf[0], file);
      if (choice === "open") {
        S.pendingFile = null;
        await openAnalysis(e.duplicateOf[0].id);
        return;
      }
      if (choice === "again") {
        await uploadFile(file, { allowDuplicate: true, sha });
        return;
      }
      status("Upload cancelled — the scan is already here.");
      if ($("#btn-upload")) $("#btn-upload").disabled = false;
      return;
    }
    // The packed file did not unpack to the file chosen. The link is the
    // likely cause, but the server cannot tell that from this browser's own
    // packing going wrong — and packing that goes wrong once goes wrong the
    // same way on every attempt, which would leave the operator unable to
    // upload from a browser that uploaded fine before packing existed. So the
    // next attempt, and every one after it until the page is reloaded, sends
    // the file as it is: slower, never refused for this reason again. Not
    // retried automatically — on a slow link sending the whole file again is
    // the operator's call, and the message already asks for it.
    if (e.damagedTransfer) {
      S.sendUnpacked = true;
      status("Upload failed: " + e.message + " The next try sends it "
             + "unpacked, which takes longer.", true);
      if ($("#btn-upload")) $("#btn-upload").disabled = false;
      return;
    }
    status("Upload failed: " + e.message, true);
    if ($("#btn-upload")) $("#btn-upload").disabled = false;
  }
}

async function openAnalysis(aid) {
  // Full reset first: nothing of the previously open analysis may survive into
  // this one, not even a selected ROI id or a half-finished corner click.
  clearAnalysisState();
  S.aid = aid;
  const rec = await api(`api/analyses/${aid}`);
  S.record = rec;
  S.reg = rec.registration;
  S.geometry = rec.geometry;
  S.results = rec.results;
  S.history = rec.history || { seq: 0, undo_depth: 0, redo_depth: 0 };
  S.layoutSource = rec.layout_source || "";
  S.phantomProfile = rec.phantom_profile || null;
  S.sid = rec.sid_mm || 1000;
  S.nativeCols = S.reg ? S.reg.image.cols : (rec.meta.Columns || 3000);
  S.nativeRows = S.reg ? S.reg.image.rows : (rec.meta.Rows || 3000);
  loadImage();
  showTab("analyze");
  setStage(openingStage(rec));
}

/* Which step to open a stored analysis at.

   Never step E. Rendering step E starts a measurement — it POSTs /compute the
   moment it is drawn — so opening a record that had reached E would begin the
   work again unasked. For a record interrupted mid-measurement that turns a
   reload into a loop: resume, recompute, reload, recompute, with no way out.
   Such a record opens at D instead, one press away from measuring, so starting
   the work is the operator's decision. */
function openingStage(rec) {
  if (!rec.geometry) return "A";
  if (rec.results) return "F";
  const stage = rec.stage || "B";
  return stage === "E" ? "D" : stage;
}

/* ---- Stage A ---- */
/* The verdict of the acquisition-quality check, where the operator can still
   do something about it.

   Stage A is the right place: the phantom is usually still on the table, so an
   exposure worth repeating can be repeated. Every check that failed is listed
   with what it measured, because a refusal an operator cannot understand is a
   refusal they will work around. */
function qualityBanner() {
  const q = (S.record && S.record.quality) || null;
  if (!q || q.verdict === "ok") return "";
  const failed = (q.checks || []).filter(c => !c.ok);
  const rows = failed.map(c =>
    `<li><b>${html_escape(c.label)}</b> — ${html_escape(c.detail)}</li>`).join("");
  return `<div class="reasons-why" style="border-color:var(--fail)">
    <b>This image did not pass the quality check.</b>
    <p>${html_escape(q.summary || "")}</p>
    <ul>${rows}</ul>
    <p class="hint">You can still analyse it — the numbers may be useful to
      show what went wrong — but without an administrator's approval it cannot
      become the reference scan for this phantom, and it will not set the
      measuring points that future scans of this phantom start from.</p></div>`;
}

function stageA(c) {
  if (!S.reg) { c.innerHTML = "<p>Registration unavailable.</p>"; return; }
  const s = S.reg.summary;
  const lmRows = Object.entries(S.reg.landmarks || {}).map(([side, l]) =>
    `<tr><td>${side}</td><td class="num">${l.err_mm !== null && l.err_mm !== undefined
       ? fmt(l.err_mm, 3) + " mm" : "not found"}</td></tr>`).join("");
  const rp = S.record.reduced_precision
    ? `<p class="hint" style="color:var(--warn)">⚠ reduced-precision input
       (plain image, no metadata)</p>` : "";
  c.innerHTML = `<h2>Stage A — Registration check</h2>${rp}${qualityBanner()}
    <div class="card"><div class="kv">
      <div>rotation</div><div>${fmt(s.rotation_deg, 2)}°</div>
      <div>mirrored</div><div>${s.mirrored}</div>
      <div>scale</div><div>${fmt(s.mm_per_px, 5)} mm/px</div>
      <div>landmark RMS</div><div>${fmt(s.residual_rms_mm, 3)} mm</div>
    </div></div>
    <h3>Ruler central-line verification</h3>
    <table><tr><th>side</th><th>prediction error</th></tr>${lmRows}</table>
    <p class="hint">Check on the image: the red dashed outline must follow the
    phantom edge; rotation/mirroring must be plausible. If detection failed, use
    manual corners.</p>
    <button class="primary" id="btn-confirm-a">Confirm registration ✓</button>
    <button class="secondary" id="btn-manual-corners">Manual corners…</button>
    <button class="secondary" id="btn-back-u">Back to upload</button>`;
  // Every other step has a way back to the one before it; this is the first
  // step's, and the step before it is the upload page. The scan stays on the
  // server and in the unfinished list there, so this costs nothing.
  $("#btn-back-u").addEventListener("click", goToUpload);
  $("#btn-confirm-a").addEventListener("click", async () => {
    status("Detecting patterns…");
    try {
      await postJSON(`api/analyses/${S.aid}/confirm`, { stage: "A" });
      const r = await postJSON(`api/analyses/${S.aid}/propose`, {},
                               { timeoutMs: analysisTimeoutMs() });
      applyProposal(r);
      setStage("B");
    } catch (e) { status(e.message, true); }
  });
  $("#btn-manual-corners").addEventListener("click",
    () => startPicking("corners"));
}

/* Adopt a fresh proposal, and say plainly where the marks came from.

   The operator must be able to tell a stored layout from a detection at the
   moment the marks appear on screen — that difference is the whole point of
   storing a layout, and the absence of it is how "the marks came back from a
   deleted scan" felt like a bug rather than a feature. */
function applyProposal(r) {
  S.geometry = r.geometry;
  S.layoutSource = r.layout_source || "auto";
  if (r.profile) S.phantomProfile = r.profile;
  noteHistory(r);
  if (r.profile_applied && r.profile) {
    status(`Measuring points loaded from the stored layout for phantom `
           + `${r.profile.phantom} (saved `
           + `${(r.profile.updated_at || "").slice(0, 16)}). Stage C can reset `
           + `them to automatic detection.`);
  } else if (r.profile && r.profile_check && !r.profile_check.ok) {
    status(`The stored layout for phantom ${r.profile.phantom} was NOT applied: `
           + r.profile_check.reason, true);
  } else {
    status("");
  }
}

/* The controls that make point picking correctable — for the phantom corners,
   the low-contrast block corners and a field edge alike (see PICKERS).

   Rendered beside the image rather than as a dialog, so the picture stays
   visible while they are used — the whole task is judging the picture. */
function renderCornerControls() {
  const box = $("#corner-controls");
  if (!box) return;
  const job = PICKERS[S.mode];
  if (!job) { box.innerHTML = ""; box.classList.add("hidden"); return; }
  const n = S.manualCorners.length;
  // A four-point job counts its progress; a one-point job only has to say
  // whether its point is down yet.
  const progress = job.points === 1
    ? (n ? "point placed" : "no point yet")
    : `${n} of ${job.points} placed`;
  box.classList.remove("hidden");
  box.innerHTML = `
    <b>${html_escape(job.heading())} — ${progress}.</b>
    <p class="hint">${job.place()} Drag a placed point to adjust it, or
      select one and nudge with the arrow keys (Shift for ten pixels).
      Middle-drag, right-drag or hold Space to pan; the wheel zooms. Nothing is
      sent until you press ${job.applyLabel}.</p>
    <button class="primary" id="btn-corners-apply" ${n === job.points ? "" : "disabled"}>
      ${job.applyLabel}</button>
    <button class="secondary" id="btn-corners-undo" ${n ? "" : "disabled"}>
      Undo last point</button>
    ${job.points > 1 ? `<button class="secondary" id="btn-corners-clear"
      ${n ? "" : "disabled"}>Start over</button>` : ""}
    <button class="secondary" id="btn-corners-cancel">Cancel</button>`;
  $("#btn-corners-apply").addEventListener("click", () => applyPicking());
  $("#btn-corners-undo").addEventListener("click", () => {
    S.manualCorners.pop();
    S.selectedCorner = null;
    renderCornerControls();
    draw();
  });
  const clear = $("#btn-corners-clear");
  if (clear) clear.addEventListener("click", () => {
    S.manualCorners = [];
    S.selectedCorner = null;
    renderCornerControls();
    draw();
  });
  $("#btn-corners-cancel").addEventListener("click", () => cancelCornerPicking());
}

function cancelCornerPicking() {
  const job = PICKERS[S.mode];
  endPicking();
  draw();
  if (job) status(job.cancelled);
}

async function submitManualCorners() {
  if (S.manualCorners.length !== 4) return;
  const corners = S.manualCorners.slice();
  endPicking();
  draw();
  status("Re-registering with manual corners…");
  try {
    S.reg = await postJSON(`api/analyses/${S.aid}/register`,
      { corners_px: corners });
    S.geometry = null;
    setStage("A");
    status("Re-registered.");
  } catch (e) {
    status("Manual registration failed: " + e.message, true);
  }
}

/* The block's four corners, sent only when the operator says they are right.
   placeBlock owns the request, the refusal and the resync, exactly as for a
   drag or a typed angle, so the four-corner path adds no second copy of that. */
async function submitBlockCorners() {
  if (S.manualCorners.length !== 4) return;
  const corners = S.manualCorners.slice();
  endPicking();
  draw();
  await placeBlock({ corners_px: corners });
}

/* ---- Stage B ---- */
/* Detail a confident user wants and a hurried one should not have to wade
   through. A native <details>: it needs no script, survives a re-render, is
   keyboard-accessible, and prints expanded — so the printable report is still
   complete even when the screen is not. */
function advanced(summary, html, open = false) {
  return `<details class="advanced"${open ? " open" : ""}>`
       + `<summary>${summary}</summary>${html}</details>`;
}

function stageB(c) {
  if (!S.geometry) { c.innerHTML = "<p>No proposals yet.</p>"; return; }
  const g = S.geometry;
  const rows = [];
  const missing = [];
  const add = (test, name, ok, extra = "") => {
    if (!ok) missing.push(name);
    rows.push(
      `<tr><td><span class="swatch" style="background:${COLORS[test]}"></span>
       ${name}</td><td>${ok ? "detected" : "NOT refined (nominal used)"}</td>
       <td>${extra}</td></tr>`);
  };
  if (g.linepairs && !g.linepairs._error)
    g.linepairs.groups.forEach(gr =>
      add("linepairs", `line group ${gr.id} (${gr.freq_lp_mm} lp/mm)`, gr.detected));
  if (g.lowcontrast && !g.lowcontrast._error)
    add("lowcontrast", "low-contrast block + 8 circles", g.lowcontrast.detected,
        `angle ${fmt(g.lowcontrast.angle_deg, 1)}°`);
  if (g.wedge && !g.wedge._error)
    add("wedge", "wedge, 7 steps (S1 top … S7 bottom)", g.wedge.detected,
        `x = ${fmt(g.wedge.center_x_mm, 1)} mm`);
  if (g.uniformity && !g.uniformity._error)
    g.uniformity.squares.forEach(sq =>
      add("uniformity", `uniformity square ${sq.id}`, sq.detected,
          sq.outline ? `${fmt(sq.outline.measured_w_mm, 1)}×${fmt(sq.outline.measured_h_mm, 1)} mm` : ""));
  if (g.geometry && !g.geometry._error) {
    Object.entries(g.geometry.rulers).forEach(([side, r]) =>
      add("geometry", `ruler ${side}`, r.detected,
          r.detected ? `${r.line_offsets_mm.length} lines` : ""));
    Object.entries(g.geometry.field_edges).forEach(([side, f]) =>
      add("geometry", `field edge ${side}`, f.detected,
          f.detected ? `${fmt(f.offset_from_edge_mm, 1)} mm outside`
                     : `<span class="hint">${f.reason || "no edge"} — place manually in Stage C if visible</span>`));
  }
  TESTS.forEach(t => {
    if (g[t] && g[t]._error) rows.push(
      `<tr><td>${t}</td><td colspan="2" style="color:var(--fail)">${g[t]._error}</td></tr>`);
  });
  const errored = TESTS.filter(t => g[t] && g[t]._error);
  const total = rows.length;
  const found = total - missing.length;
  let verdict;
  if (errored.length) {
    verdict = `<div class="reasons-why"><b>${errored.join(", ")} could not be
      analysed on this scan.</b> The remaining patterns are still usable.</div>`;
  } else if (!missing.length) {
    verdict = `<div class="reasons-pass"><b>Every pattern was found</b>
      (${total} of ${total}).</div>`;
  } else {
    verdict = `<div class="reasons-why"><b>${found} of ${total} patterns were
      found.</b> These were not, so their nominal positions are being used:
      <ul>${missing.map(m => `<li>${m}</li>`).join("")}</ul></div>`;
  }

  c.innerHTML = `<h2>Stage B — Pattern identification</h2>
    <p class="hint">Look at the image, not at this panel: every pattern found is
    outlined and labelled there. Check that each outline is on the right object
    with the right label — zoom in. A pattern found end-for-end, or a group
    labelled as its neighbour, has to be caught here.</p>
    ${verdict}
    ${advanced("Detection detail — every pattern, with what was measured",
      `<table><tr><th>pattern</th><th>detection</th><th></th></tr>
       ${rows.join("")}</table>`)}
    <button class="primary" id="btn-confirm-b">Verify measuring points →</button>
    <button class="secondary" id="btn-back-a">Back to registration</button>
    <p class="hint">Nothing needs fixing here. Patterns that were not found, or
    were labelled wrongly, are corrected in the next step by dragging and
    rotating their measuring areas.</p>`;
  $("#btn-confirm-b").addEventListener("click", async () => {
    await postJSON(`api/analyses/${S.aid}/confirm`, { stage: "B" });
    setStage("C");
  });
  $("#btn-back-a").addEventListener("click", () => setStage("A"));
}

/* ---- Stage C ---- */

/* Where the measuring points currently come from, and how to change that.

   Two resets rather than one, because "start again" is ambiguous once a
   phantom has a stored layout: the operator has to be able to say whether they
   mean this scan's own detection or the layout confirmed for this phantom. */
/* An analysis that is signed off or finalised is read-only: the server refuses
   every edit to either. Say so before the operator drags something and gets a
   refusal, rather than after. Named for the first case, it covers both — a
   finalised analysis used to look editable here and fail only on the server. */
function signedOff() {
  const r = S.record || {};
  return !!((r.validation_status || "").trim() || (r.finalized_at || "").trim());
}

function lockedBar() {
  const r = S.record || {};
  const signed = (r.validation_status || "").trim();
  const who = signed
    ? `Signed off${r.validated_by ? ` by ${html_escape(r.validated_by)}` : ""}`
      + (r.validated_at ? ` on ${html_escape(r.validated_at.slice(0, 16))}` : "")
    : `Finalised${r.finalized_by ? ` by ${html_escape(r.finalized_by)}` : ""}`
      + (r.finalized_at ? ` on ${html_escape(r.finalized_at.slice(0, 16))}` : "");
  // Re-run is the way through for both. Withdrawing a ruling does not
  // un-finalise, so the old advice to withdraw first led to a second refusal.
  return `<div class="ident-warn" style="border-radius:6px;margin:8px 0">
      🔒 <b>${who}</b> — the measuring points, the registration and the
      results are locked, because ${signed
        ? "someone has taken responsibility for these numbers"
        : "these numbers were declared finished"}. To rework this analysis,
      use <b>Re-run analysis…</b> on the results step; it keeps the previous
      results.
    </div>`;
}

function layoutBar() {
  if (signedOff()) return lockedBar();
  const prof = S.phantomProfile;
  const fromProfile = S.layoutSource === "profile";
  const phantom = (S.record && S.record.phantom) || "";
  const head = fromProfile
    ? `<b>Measuring points: stored layout for phantom ${html_escape(prof
        ? prof.phantom : phantom)}</b>`
    : `<b>Measuring points: automatic detection on this scan</b>`;
  const meta = fromProfile && prof
    ? `<br>saved ${html_escape((prof.updated_at || "").slice(0, 16))}`
      + (prof.updated_by ? ` by ${html_escape(prof.updated_by)}` : "")
      + ` · ${prof.n_rois} measuring area(s)`
    : (prof
       ? `<br>A stored layout for phantom ${html_escape(prof.phantom)} is `
         + `available (saved ${html_escape((prof.updated_at || "").slice(0, 16))}).`
       : (phantom
          ? `<br>No layout stored for phantom ${html_escape(phantom)} yet — `
            + `the choice above the confirm button decides whether these `
            + `measuring points become it.`
          : `<br><span style="color:var(--warn)">No phantom is named, so these `
            + `corrections cannot be reused on the next scan. Add a phantom in `
            + `the identity bar above.</span>`));
  return `<div class="layout-bar ${fromProfile ? "profile" : ""}">
      ${head}${meta}
      <div class="btn-row">
        <button class="secondary-sm" id="btn-undo">↶ Undo</button>
        <button class="secondary-sm" id="btn-redo">↷ Redo</button>
        <button class="secondary-sm" id="btn-reset-auto">Reset to auto-detected</button>
        ${prof ? `<button class="secondary-sm" id="btn-reset-profile">Reset to
          stored layout</button>` : ""}
      </div>
    </div>`;
}

/* Whether confirming this step also makes these the phantom's stored
   measuring points — asked, never assumed.

   The stored points are shared: every later scan of the phantom starts from
   them. Confirming used to store them every time, and the field audit log
   shows what that did — one phantom's points rewritten six times in twelve
   minutes, three of those from exposures nothing could be measured in. So the
   operator is asked, and the page always sends the answer.

   Ticked by default only while the phantom has no stored points yet, or the
   ones it has came from this very analysis — confirming again after nudging a
   mark should still update them. Once another analysis has set them,
   replacing them is a decision, so the box starts empty. The server applies
   the same rule to a request that does not say. */
function layoutSaveDefault() {
  const prof = S.phantomProfile;
  return !prof || prof.source_analysis_id === S.aid;
}

/* The operator's answer has to survive the re-render every edit in this step
   causes; otherwise dragging a mark after unticking the box would quietly
   tick it again. It belongs to one analysis and one phantom name: renaming
   the phantom changes whose stored points are at stake, so the question is
   asked afresh. */
function layoutSaveChoice() {
  const c = S.layoutSaveChoice;
  const phantom = ((S.record && S.record.phantom) || "").trim();
  if (c && c.aid === S.aid && c.phantom === phantom) return c.on;
  return layoutSaveDefault();
}

/* Signed off or finalised. The server refuses to confirm this step on either,
   so nothing here may be offered as if it could still be stored. */
function recordLocked() {
  const r = S.record || {};
  return signedOff() || !!(r.finalized_at || "").trim();
}

/* The choice itself, or — where it cannot be offered — why not. A checkbox
   that is simply missing, or greyed out without a word, reads as a bug; the
   operator needs to know whether the next scan of this phantom will start from
   these points, and if not, what would change that. */
function layoutSaveBlock() {
  const r = S.record || {};
  const phantom = (r.phantom || "").trim();
  const name = html_escape(phantom);
  if (recordLocked()) {
    return `<div class="layout-save unavailable" id="layout-save">
      <b>These measuring points cannot be stored for future scans.</b>
      <p class="hint">This analysis is ${signedOff() ? "signed off" : "finalised"},
      so it can no longer change the measuring points stored for
      ${phantom ? `phantom <b>${name}</b>` : "its phantom"}.</p></div>`;
  }
  if (!phantom) {
    return `<div class="layout-save unavailable" id="layout-save">
      <b>These measuring points cannot be kept for future scans.</b>
      <p class="hint">No phantom is named on this analysis, so there is nothing
      to keep them for. Add the phantom in the identity bar above and this
      choice appears.</p></div>`;
  }
  if (qualityRefusesReference(r.quality)) {
    return `<div class="layout-save unavailable" id="layout-save">
      <b>These measuring points will not be used for future scans of phantom
      ${name}.</b>
      <p class="hint">This image did not pass the quality check, so it cannot
      set the measuring points that later scans of this phantom start from.
      This analysis is still measured with the points you see. These checks
      failed:</p>
      ${failedChecksList(r.quality)}
      <button class="secondary-sm" id="btn-layout-override">Use anyway
        (administrator)…</button></div>`;
  }
  const prof = S.phantomProfile;
  let note;
  if (!prof) {
    note = `Phantom ${name} has no stored measuring points yet. Ticked, the
      next scan of it starts from these.`;
  } else if (prof.source_analysis_id === S.aid) {
    note = `The measuring points stored for this phantom came from this
      analysis. Ticked, your latest corrections replace them.`;
  } else {
    note = `Phantom ${name} already has measuring points, stored
      ${html_escape((prof.updated_at || "").slice(0, 16))}${prof.updated_by
        ? ` by ${html_escape(prof.updated_by)}` : ""}. Tick this only if
      these are better: they would replace those for every later scan of this
      phantom. Left unticked, only this analysis uses the points you see.`;
  }
  return `<div class="layout-save" id="layout-save">
      <label><input type="checkbox" id="cb-save-layout"
                    ${layoutSaveChoice() ? "checked" : ""}>
        Use these measuring points for future scans of phantom ${name}</label>
      <p class="hint">${note} The low-contrast insert setting goes with
      them.</p></div>`;
}

/* What confirming step C did to the phantom's stored measuring points, in
   words. Nothing else on screen shows it, and it decides where the next scan
   of this phantom starts. */
function reportLayoutSave(r, asked) {
  if (r.profile_saved && r.profile) {
    // The stored points now come from this analysis, which is what makes the
    // choice start ticked if the operator comes back to this step.
    S.phantomProfile = { phantom: r.profile.phantom,
                         updated_at: r.profile.updated_at,
                         updated_by: r.profile.updated_by,
                         n_rois: r.profile.n_rois,
                         source_analysis_id: S.aid };
    let msg = `Measuring points stored for phantom ${r.profile.phantom} — `
            + `the next scan of it starts here.`;
    if (r.profile.quality_override)
      msg += ` Stored on an administrator's approval although the image did `
           + `not pass the quality check; the reason is in the audit log.`;
    if (typeof r.profile.insert_turned === "boolean")
      msg += ` Its low-contrast insert is kept `
           + (r.profile.insert_turned ? "turned half round." : "as drawn.");
    if ((r.profile.near_miss || []).length)
      msg += ` Note: a separate layout also exists for `
           + `“${r.profile.near_miss.join("”, “")}”, which differs only in `
           + `spelling.`;
    status(msg);
  } else if (r.profile_error) {
    // A refusal on quality grounds is a decision the operator should see
    // and understand, not a notice that clears itself after six seconds.
    status(r.profile_error, true, !!r.profile_blocked_by_quality);
  } else if (!asked && S.record && (S.record.phantom || "").trim()) {
    const prof = S.phantomProfile;
    status(prof
      ? `Measuring points confirmed for this analysis only. Phantom `
        + `${prof.phantom} keeps the measuring points stored `
        + `${(prof.updated_at || "").slice(0, 16)}.`
      : `Measuring points confirmed for this analysis only — nothing was `
        + `stored for future scans of phantom ${S.record.phantom.trim()}.`);
  }
}

/* The administrator's way to keep the measuring points of a scan that failed
   the quality check. It confirms the step as well: the points being stored
   are the ones on screen, so there is nothing left to confirm afterwards. */
async function overrideLayoutSave() {
  const phantom = ((S.record && S.record.phantom) || "").trim();
  const r = await adminOverride({
    title: `Use these measuring points for phantom ${phantom} anyway?`,
    intro: `This image did not pass the quality check. Storing its measuring
      points makes them where every later scan of phantom
      <b>${html_escape(phantom)}</b> starts. Go ahead only if you have looked
      at the image and the points are right.`,
    quality: S.record && S.record.quality,
    confirmLabel: "Confirm the step and store them",
    cancelled: "Nothing was stored, and the step is not confirmed yet.",
    send: (vals) => postJSON(`api/analyses/${S.aid}/confirm`,
      { stage: "C", save_profile: true, ...vals }, { adminAuth: true }),
  });
  if (!r) return;
  reportLayoutSave(r, true);
  setStage("D");
}

/* The low-contrast block on its own, large and flattened.

   The field report for this step was that the discs are "difficult to see and
   properly adjust the W/C to place the marks". Three things made that true,
   and this addresses each: the block is a small part of a big picture; the
   window that suits the whole phantom is far too wide for objects a fraction
   of a percent in contrast; and the block's own illumination gradient swamps
   them. So the server sends the block alone — straightened, flattened and
   windowed to itself — at about twenty kilobytes against the nine hundred of
   the full render.

   Loaded once per geometry change. The contrast slider then works on the copy
   already in the browser, so hunting for the faintest disc costs nothing on
   the connection — which is what made re-windowing painful in the first
   place. */
async function loadBlockView(force = false) {
  const panel = $("#lc-view-panel");
  if (!panel || !S.aid) return;
  const aid = S.aid;
  const seq = (S.history && S.history.seq) || 0;
  if (!force && S.blockView && S.blockView.aid === aid
      && S.blockView.seq === seq) {
    panel.classList.remove("hidden");
    drawBlockView();
    renderInsertOrientation(S.blockView.meta.orientation);
    return;
  }
  // Only the newest load may draw. An older one answering late would put the
  // previous placement back on screen — or, with another analysis opened in
  // the meantime, this one's close-up under that one's name, where the next
  // visit to step C would find it and show it as current.
  const gen = ++S.blockViewGen;
  const current = () => gen === S.blockViewGen && S.aid === aid;
  try {
    const meta = await api(`api/analyses/${aid}/lowcontrast_view`);
    if (!current()) return;
    const img = new Image();
    // Named by what the picture shows — pixels, registration, block placement
    // — so nudging the block fetches a new one, leaving it alone never
    // re-fetches it, and undoing back to an earlier placement finds it kept.
    img.src = `api/analyses/${aid}/lowcontrast_view.png?key=${meta.key}`;
    await new Promise((ok, fail) => { img.onload = ok; img.onerror = fail; });
    if (!current()) return;
    S.blockView = { aid, seq, meta, img, canvas: null };
    panel.classList.remove("hidden");
    drawBlockView();
    renderBlockLegend(meta);
    renderInsertOrientation(meta.orientation);
  } catch (e) {
    // A close-up that cannot be drawn must never block the step it sits in.
    if (current()) panel.classList.add("hidden");
  }
}

/* A fresh close-up after the block moved, one load at a time. Each load is
   two requests and a render on the server, and a nudge takes a fraction of
   that, so five quick nudges used to start five loads and draw four
   placements that were already gone. A nudge during a load now only notes
   that one more is wanted, made when the load in flight ends — and that one
   shows wherever the block is by then. */
async function refreshBlockView() {
  if (S.blockViewBusy) { S.blockViewAgain = true; return; }
  S.blockViewBusy = true;
  try {
    do {
      S.blockViewAgain = false;
      await loadBlockView(true);
    } while (S.blockViewAgain);
  } finally {
    S.blockViewBusy = false;
  }
}

function drawBlockView() {
  const v = S.blockView, cv = $("#lc-view");
  if (!v || !cv) return;
  const [w, h] = v.meta.size_px;
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const ctx = cv.getContext("2d", { willReadFrequently: true });

  if (!v.canvas) {
    v.canvas = document.createElement("canvas");
    v.canvas.width = w; v.canvas.height = h;
    v.canvas.getContext("2d").drawImage(v.img, 0, 0);
  }
  ctx.drawImage(v.canvas, 0, 0);

  // Contrast stretch about mid grey, as a 256-entry table: the per-pixel work
  // is one lookup, so the slider stays smooth on the hardware in the field.
  const gain = (parseFloat(($("#lc-view-gain") || {}).value) || 100) / 100;
  if (Math.abs(gain - 1) > 0.01) {
    const data = ctx.getImageData(0, 0, w, h), px = data.data;
    const lut = new Uint8ClampedArray(256);
    for (let i = 0; i < 256; i++) lut[i] = Math.round(128 + (i - 128) * gain);
    for (let i = 0; i < px.length; i += 4) {
      px[i] = px[i + 1] = px[i + 2] = lut[px[i]];
    }
    ctx.putImageData(data, 0, 0);
  }

  (v.meta.markers || []).forEach(m => {
    ctx.beginPath();
    ctx.arc(m.x_px, m.y_px, m.r_px, 0, Math.PI * 2);
    ctx.strokeStyle = m.visibility === "clear" ? "rgba(90,200,120,0.85)"
                    : m.visibility === "faint" ? "rgba(230,180,60,0.9)"
                    : "rgba(230,110,90,0.95)";
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.font = "11px Segoe UI";
    ctx.fillText(m.id, m.x_px - m.r_px, m.y_px - m.r_px - 3);
  });
}

/* Which discs are actually there, so eight faint rings do not all have to be
   judged by eye. */
function renderBlockLegend(meta) {
  const host = $("#lc-view-legend");
  if (!host) return;
  const counts = { clear: 0, faint: 0, "at the limit": 0 };
  (meta.markers || []).forEach(m => { counts[m.visibility] += 1; });
  host.textContent = "";
  [["clear", "clearly visible"], ["faint", "faint"],
   ["at the limit", "at the limit of visibility"]].forEach(([key, words]) => {
    if (!counts[key]) return;
    const chip = el("span", { class: "lc-chip lc-chip-" + key.replace(/ /g, "-") });
    chip.textContent = `${counts[key]} ${words}`;
    host.appendChild(chip);
  });
  if (meta.flat) {
    const chip = el("span", { class: "lc-chip lc-chip-at-the-limit" });
    chip.textContent = "this exposure carries no signal in the block";
    host.appendChild(chip);
  }
}

/* Which way round the low-contrast insert is read, and where that came from.

   The two builds of the phantom differ by a half turn of the insert, which
   nothing in the picture shows. The contrast order usually tells them apart,
   but not on a very weak exposure — which is why the answer can be saved for
   the phantom and set here by hand. It is shown in this step, before anything
   is measured, because this is where it can still be changed. */
const INSERT_SOURCE = {
  measured: "read from the contrast",
  stored: "saved for this phantom",
  user: "set by hand",
  undetermined: "could not be read from the contrast",
};

function renderInsertOrientation(o) {
  const drawn = $("#lc-insert-drawn"), turned = $("#lc-insert-turned");
  const src = $("#lc-insert-src"), warn = $("#lc-insert-warn");
  if (!drawn || !turned || !src || !warn || !o) return;
  drawn.checked = !o.flipped;
  turned.checked = !!o.flipped;
  let words = INSERT_SOURCE[o.source] || "";
  if (o.source === "undetermined")
    words += o.flipped ? ", so taken as the rings were turned"
                       : ", so taken as drawn";
  src.textContent = words ? `(${words})` : "";
  // The server's own words, so the page and the results say the same thing.
  warn.textContent = o.conflict
    ? "The discs' contrast order looks like the other build of this phantom "
      + "(insert turned the other way). Check that the phantom ID is right."
    : "";
  warn.classList.toggle("hidden", !o.conflict);
}

async function setInsertOrientation(flipped) {
  const before = S.history.seq;
  try {
    const r = await postJSON(`api/analyses/${S.aid}/lowcontrast_orientation`,
                             { flipped, expect_seq: before });
    if (S.geometry && S.geometry.lowcontrast)
      S.geometry.lowcontrast.orientation = r.orientation;
    noteHistory(r);
    // Nothing moved, so the close-up already downloaded is still the right
    // picture — but only if it was right before this edit. Re-keying a view
    // that was already out of date would pass it off as current, and every
    // later render would keep redrawing the old placement.
    if (S.blockView && S.blockView.aid === S.aid
        && S.blockView.seq === before) {
      S.blockView.seq = S.history.seq;
      S.blockView.meta.orientation = r.view;
    }
    renderInsertOrientation(r.view);
    status(flipped
      ? "Low-contrast insert set as turned half round — the discs are read that way."
      : "Low-contrast insert set as drawn — the discs are read that way.");
  } catch (e) {
    if (handleStaleGeometry(e)) return;
    status("Could not change the insert setting: " + e.message, true);
    if (S.blockView && S.blockView.meta)
      renderInsertOrientation(S.blockView.meta.orientation);
  }
}

function stageC(c) {
  const fieldBtns = ["top", "right", "bottom", "left"].map(s =>
    `<button class="secondary btn-field" data-side="${s}">${s}</button>`).join(" ");
  const locked = signedOff();
  const lc = (S.geometry && S.geometry.lowcontrast) || null;
  const lcOk = !locked && !!lc && !lc._error && !!lc.block;
  const lcAng = lcOk ? Number(lc.angle_deg || 0) : 0;
  S.lcAngleCommitted = lcAng;
  c.innerHTML = `<h2>Stage C — Measuring points</h2>
    ${layoutBar()}
    <p class="hint">Click an ROI center dot to inspect μ/σ; drag it to adjust.
    Adjusted ROIs turn orange and are recorded in the audit trail. Low-contrast:
    solid = object ROI, dotted = background ROI, dashed = full circle outline.
    Every change can be undone — you never have to re-upload the scan to
    recover from a slip.</p>
    <h3>Low-contrast block</h3>
    <p class="hint">The eight circles sit on a fixed grid inside the block, so
    correcting the block once moves them all. Drag the block outline like any
    ROI, type its angle below, or click its four corners — they can be dragged
    and taken back, and the block only moves when you press Apply corners.</p>
    <div class="btn-row">
      <button class="secondary-sm" id="lc-flip" ${lcOk ? "" : "disabled"}
              title="Turn the block end for end — each ring moves to the disc opposite">
        ⟲ Turn 180°</button>
    </div>
    <p class="hint">The block outline is symmetrical, so the insert can be
    fitted either way round and every ring still lands on a real disc — nothing
    looks wrong on the image. Which way round it is fitted is shown under the
    close-up below — read from the contrast, saved for this phantom, or set by
    hand — and that setting decides which disc is which. <b>Turn 180°</b> only
    moves the rings, each onto the disc opposite; the discs are still named by
    the insert setting, so the two never correct the same thing twice.
    Everything below is fine adjustment on top of this.</p>

    <div id="lc-view-panel" class="lc-view-panel hidden">
      <div class="lc-view-head">
        <b>The block, close up</b>
        <label for="lc-view-gain">contrast
          <input type="range" id="lc-view-gain" min="20" max="300" value="100">
        </label>
        <button class="secondary-sm" id="lc-view-refresh">Refresh</button>
      </div>
      <div class="lc-view-stage">
        <canvas id="lc-view"></canvas>
      </div>
      <p class="hint" id="lc-view-note">Straightened, flattened and windowed to
      the block itself, so the discs show without re-windowing the whole image.
      The contrast slider works on the picture already downloaded — it costs no
      connection. Rings mark where each disc is expected.</p>
      <div id="lc-view-legend" class="lc-view-legend"></div>
      <div class="lc-insert" role="radiogroup" aria-label="Low-contrast insert">
        <b>Low-contrast insert:</b>
        <label><input type="radio" name="lc-insert" id="lc-insert-drawn">
          as drawn</label>
        <label><input type="radio" name="lc-insert" id="lc-insert-turned">
          turned half round</label>
        <span class="hint" id="lc-insert-src"></span>
      </div>
      <p class="hint lc-insert-warn hidden" id="lc-insert-warn"></p>
      <p class="hint">The two builds of this phantom differ only by a half turn
      of this insert. Changing it moves nothing — each ring stays on its disc;
      only which design level that disc is read as changes. It is kept for
      this phantom when you confirm the measuring points and they are saved
      for future scans.</p>
    </div>
    <div class="rot-row" id="lc-angle-row">
      <label for="lc-angle">fine angle °
        <input type="number" id="lc-angle" step="0.5" min="-180" max="180"
               value="${lcAng.toFixed(1)}" ${lcOk ? "" : "disabled"}></label>
      <button class="secondary-sm" id="lc-angle-minus" ${lcOk ? "" : "disabled"}>−1°</button>
      <button class="secondary-sm" id="lc-angle-plus" ${lcOk ? "" : "disabled"}>+1°</button>
      <button class="secondary-sm" id="lc-angle-apply" ${lcOk ? "" : "disabled"}>Apply</button>
    </div>
    <p class="hint">Degrees in the phantom frame. Typing previews on the image;
    Enter or Apply commits and re-lays all eight circles. [ and ] nudge by 1°.
    The first commit after detection also drops the automatic sub-millimetre
    grid refinement, so the circles can settle up to about 2 mm from the
    preview; after that, preview and result agree exactly.
    ${lcOk ? "" : "<b>The low-contrast block was not proposed on this scan, so "
              + "there is no angle to set.</b>"}</p>
    <button class="secondary" id="btn-block-corners" ${lcOk ? "" : "disabled"}>Click
      4 block corners…</button>

    <h3>Manual field-edge placement</h3>
    <p class="hint">If a field edge was not auto-detected (or looks wrong),
    choose a side and left-click the visible radiation-field edge on the image.
    The point can be dragged or clicked again somewhere else; nothing changes
    until you press Apply edge under the image.
    Field edges describe the collimation of this exposure, so they are never
    stored as part of the phantom's layout.</p>
    <div>${fieldBtns}</div>
    ${layoutSaveBlock()}
    <button class="primary" id="btn-confirm-c">Measuring points confirmed ✓</button>
    <button class="secondary" id="btn-back-b">Back to patterns</button>`;

  if (!locked) {
    refreshHistoryButtons();
    $("#btn-undo").addEventListener("click", () => stepHistory("undo"));
    $("#btn-redo").addEventListener("click", () => stepHistory("redo"));
    $("#btn-reset-auto").addEventListener("click", () => resetGeometry("auto"));
    const rp = $("#btn-reset-profile");
    if (rp) rp.addEventListener("click", () => resetGeometry("profile"));
  }
  document.querySelectorAll(".btn-field").forEach(
    b => { b.disabled = locked; });

  if (lcOk) {
    const f = $("#lc-angle");
    f.addEventListener("input", () => {
      const v = parseFloat(f.value);
      if (Number.isFinite(v)) previewBlockAngle(v);
    });
    f.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault(); commitBlockAngle(parseFloat(f.value));
      } else if (e.key === "Escape") {
        e.preventDefault();
        previewBlockAngle(S.lcAngleCommitted);
        f.value = S.lcAngleCommitted.toFixed(1);
      }
    });
    $("#lc-angle-apply").addEventListener("click",
      () => commitBlockAngle(parseFloat(f.value)));
    $("#lc-angle-minus").addEventListener("click", () => nudgeBlockAngle(-1));
    $("#lc-angle-plus").addEventListener("click", () => nudgeBlockAngle(+1));
    $("#lc-flip").addEventListener("click", () => flipBlock());
    $("#btn-block-corners").addEventListener("click",
      () => startPicking("lccorners"));
    $("#lc-view-gain").addEventListener("input", drawBlockView);
    $("#lc-view-refresh").addEventListener("click", () => loadBlockView(true));
    $("#lc-insert-drawn").addEventListener("change",
      () => setInsertOrientation(false));
    $("#lc-insert-turned").addEventListener("change",
      () => setInsertOrientation(true));
    loadBlockView();
  }

  // A side button starts a candidate point for that side; the edge is only
  // sent when Apply edge is pressed, so a click that missed the edge is moved
  // rather than applied and then undone.
  document.querySelectorAll(".btn-field").forEach(b =>
    b.addEventListener("click", () => startPicking("fieldedge", b.dataset.side)));
  const saveBox = $("#cb-save-layout");
  if (saveBox) saveBox.addEventListener("change", () => {
    S.layoutSaveChoice = { aid: S.aid, on: saveBox.checked,
                           phantom: ((S.record && S.record.phantom) || "").trim() };
  });
  const overrideBtn = $("#btn-layout-override");
  if (overrideBtn) overrideBtn.addEventListener("click", () => overrideLayoutSave());
  $("#btn-confirm-c").addEventListener("click", async () => {
    // Always said, never left to the server's default: the answer is the one
    // on screen, and where the choice is not offered the answer is no.
    const box = $("#cb-save-layout");
    const save = !!(box && box.checked);
    try {
      const r = await postJSON(`api/analyses/${S.aid}/confirm`,
                               { stage: "C", save_profile: save });
      reportLayoutSave(r, save);
      setStage("D");
    } catch (e) { status(e.message, true); }
  });
  $("#btn-back-b").addEventListener("click", () => setStage("B"));
}

async function stepHistory(which) {
  try {
    const r = await postJSON(`api/analyses/${S.aid}/geometry/${which}`, {});
    S.geometry = r.geometry;
    if (r.layout_source) S.layoutSource = r.layout_source;
    noteHistory(r);
    // An undo can revert any part of the tree, so the whole blob is replaced
    // rather than patched ROI by ROI.
    S.selectedRoi = null; S.rotTargetId = null;
    const d = $("#roi-details");
    if (d) d.remove();
    renderStage();
    draw();
    status(which === "undo" ? "Last measuring-point change undone."
                            : "Change redone.");
  } catch (e) {
    if (e.status === 409) { status(e.message); refreshHistoryButtons(); return; }
    status(`${which} failed: ` + e.message, true);
    openAnalysis(S.aid);
  }
}

async function resetGeometry(to) {
  const label = to === "auto" ? "the automatic detection for this scan"
                             : "the stored layout for this phantom";
  if (!confirm(`Reset every measuring point to ${label}?\n\n`
               + `Your manual corrections on this scan are replaced. `
               + `The reset itself can be undone.`)) return;
  status("Resetting measuring points…");
  try {
    const r = await postJSON(`api/analyses/${S.aid}/geometry/reset`, { to });
    S.geometry = r.geometry;
    S.layoutSource = r.layout_source || S.layoutSource;
    if (r.profile) S.phantomProfile = r.profile;
    noteHistory(r);
    S.selectedRoi = null; S.rotTargetId = null;
    const d = $("#roi-details");
    if (d) d.remove();
    renderStage();
    draw();
    status(to === "auto"
      ? "Measuring points reset to the automatic detection. Undo restores your edits."
      : "Measuring points reset to this phantom's stored layout.");
  } catch (e) { status("Reset refused: " + e.message, true); }
}

/* The candidate field-edge point, sent only once Apply edge is pressed. The
   side and the point are read before picking ends, because ending it clears
   both. */
async function submitFieldEdge() {
  const side = S.fieldEdgeSide, point = S.manualCorners[0];
  if (!side || !point) return;
  endPicking();
  draw();
  try {
    const f = await postJSON(`api/analyses/${S.aid}/field_edge`,
      { side, point_px: point, expect_seq: S.history.seq });
    S.geometry.geometry.field_edges[side] = f;
    noteHistory(f);
    status(`Field edge ${side} set (${fmt(f.offset_from_edge_mm, 1)} mm outside phantom edge).`);
    draw();
  } catch (e) {
    if (!handleStaleGeometry(e)) status("Failed: " + e.message, true);
  }
}

/* ---- Stage D ---- */
async function stageD(c) {
  c.innerHTML = `<h2>Stage D — Dimension verification</h2>
    <p>SID <input type="number" id="sid-input" value="${S.sid}" step="10"> mm</p>
    <p class="hint">Computing dimensions…</p>`;
  let gr;
  try {
    const r = await postJSON(`api/analyses/${S.aid}/compute_preview`,
      { tests: ["geometry"], sid_mm: S.sid },
      { timeoutMs: analysisTimeoutMs() });
    gr = r.geometry;
    S.dimPreview = gr;
  } catch (e) {
    c.innerHTML += `<p style="color:var(--fail)">${e.message}</p>`;
    return;
  }
  const d = gr.dimensions || {};
  // A geometry test that never ran carries only its own status ("not
  // measured"), and both lines have to say so rather than a bare "n/a".
  const dimStatus = gr.dimension_status || gr.status;
  const fieldStatus = gr.field_status || gr.status;
  const dimRow = (name, v, nom) => {
    const dev = nom ? (100 * (v - nom) / nom) : null;
    return `<tr><td>${name}</td><td class="num">${fmt(v, 2)}</td>
      <td class="num">${nom ? fmt(nom, 1) : "—"}</td>
      <td class="num" ${dev !== null && Math.abs(dev) > 1 ? 'style="color:var(--fail)"' : ""}>
      ${dev !== null ? dev.toFixed(2) + " %" : "—"}</td></tr>`;
  };
  const nomS = d.nominal_side_mm;
  const dimsHtml = `
    ${dimRow("side top [mm]", d.side_top_mm, nomS)}
    ${dimRow("side right [mm]", d.side_right_mm, nomS)}
    ${dimRow("side bottom [mm]", d.side_bottom_mm, nomS)}
    ${dimRow("side left [mm]", d.side_left_mm, nomS)}
    ${dimRow("diagonal TL–BR [mm]", d.diag_tlbr_mm, nomS * Math.SQRT2)}
    ${dimRow("diagonal TR–BL [mm]", d.diag_trbl_mm, nomS * Math.SQRT2)}`;
  const seps = gr.central_line_separations || {};
  const rulRows = Object.entries(gr.rulers || {}).map(([side, r]) => r.detected ?
    `<tr><td>${side}</td><td class="num">${fmt(r.pitch_mm, 4)}</td>
     <td class="num">${fmt(r.pitch_dev_pct, 2)} %</td>
     <td class="num">${fmt(r.linearity_rms_mm, 3)}</td>
     <td class="num">${fmt(r.central_line_from_edge_mm, 2)}</td></tr>` :
    `<tr><td>${side}</td><td colspan="4">not detected</td></tr>`).join("");
  const sc = gr.scale || {};
  const spac = sc.dicom_spacings_mm_per_px || {};
  const fieldRows = Object.entries(gr.field_alignment || {}).map(([side, f]) =>
    f.detected ?
    `<tr><td>${side}</td><td class="num">${fmt(f.deviation_from_central_line_mm, 1)}</td>
     <td class="num">${fmt(f.pct_of_sid, 2)} %</td><td>${chip(f.status)}</td></tr>` :
    `<tr><td>${side}</td><td colspan="3" class="hint">not measured — ${f.reason || "no edge found"}</td></tr>`).join("");
  const worstDim = d.dev_from_nominal_pct;
  c.innerHTML = `<h2>Stage D — Dimension verification</h2>
    <p class="hint">This step confirms the millimetre scale before anything is
    measured with it. If both lines below are green you can go straight on.</p>
    <div class="card"><div class="kv">
      <div>Phantom size</div>
      <div>${chip(dimStatus)} mean side ${fmt(d.mean_side_mm, 1)} mm
        ${worstDim !== undefined && worstDim !== null
          ? `(${worstDim >= 0 ? "+" : ""}${fmt(worstDim, 2)} % from the assumed
             ${nomS} mm)` : ""}</div>
      <div>X-ray field</div>
      <div>${chip(fieldStatus)} ${fieldSummary(gr)}</div>
    </div></div>
    ${reasonsBlock(gr.dimension_reasons, dimStatus)}
    ${reasonsBlock(gr.field_reasons, fieldStatus)}
    <p>SID <input type="number" id="sid-input" value="${S.sid}" step="10"> mm
       <button class="secondary" id="btn-recompute-d">recompute</button></p>
    <p class="hint">Source-to-image distance, used to express the field
    deviation as a percentage. Change it only if this exposure used a different
    one.</p>

    ${advanced("Measured dimensions — corners, rulers, scale and field",
      `<h3>Corner-mark dimensions ${chip(dimStatus)}</h3>
       <table><tr><th>dimension</th><th>measured</th><th>nominal*</th><th>Δ</th></tr>
       ${dimsHtml}</table>
       <p class="hint">*nominal side ${nomS} mm is assumed design intent
       (no drawing available); calibrated reference is
       ${fmt(d.calibrated_side_mm, 1)} mm.</p>
       <h3>Side-mark rulers (0.5 cm pitch)</h3>
       <table><tr><th>side</th><th>pitch [mm]</th><th>Δpitch</th>
       <th>linearity RMS [mm]</th><th>central line from edge [mm]</th></tr>
       ${rulRows}</table>
       <h3>Central-line separations</h3>
       <table>
         <tr><td>vertical</td><td class="num">${fmt(seps.vertical_mm, 2)} mm</td>
             <td>nominal ${fmt(seps.nominal_mm, 1)} mm</td></tr>
         <tr><td>horizontal</td><td class="num">${fmt(seps.horizontal_mm, 2)} mm</td>
             <td></td></tr></table>
       <h3>Scale cross-check</h3>
       <table>
         <tr><td>tape-pitch measured</td><td class="num">${fmt(sc.pitch_measured_mm, 4)} mm (nominal 5.000)</td></tr>
         <tr><td>absolute scale (pitch-anchored)</td><td class="num">${fmt(sc.absolute_mm_per_px, 5)} mm/px</td></tr>
         <tr><td>DICOM ImagerPixelSpacing</td><td class="num">${fmt(spac.ImagerPixelSpacing, 5)} mm/px</td></tr>
         <tr><td>DICOM PixelSpacing</td><td class="num">${fmt(spac.PixelSpacing, 5)} mm/px</td></tr>
         <tr><td>implied magnification vs detector plane</td>
             <td class="num">${fmt(sc.implied_magnification_vs_detector_plane, 4)}</td></tr>
       </table>
       <h3>X-ray field vs central lines ${chip(fieldStatus)}</h3>
       <table><tr><th>side</th><th>deviation [mm]</th><th>% of SID</th><th></th></tr>
       ${fieldRows}</table>`)}

    <button class="primary" id="btn-confirm-d">Dimensions verified ✓ — run analysis</button>
    <button class="secondary" id="btn-back-c">Back to measuring points</button>`;
  $("#btn-recompute-d").addEventListener("click", () => {
    S.sid = +$("#sid-input").value || 1000;
    stageD(c);
  });
  $("#btn-confirm-d").addEventListener("click", async () => {
    S.sid = +$("#sid-input").value || 1000;
    await postJSON(`api/analyses/${S.aid}/confirm`, { stage: "D" });
    setStage("E");
  });
  $("#btn-back-c").addEventListener("click", () => setStage("C"));
}


/* The field-alignment headline: the worst side, or why there is none. */
function fieldSummary(gr) {
  const sides = Object.entries(gr.field_alignment || {});
  const measured = sides.filter(([, f]) => f.detected);
  if (!measured.length)
    return "not checked — no field edge was found on any side";
  let worst = measured[0];
  measured.forEach((s) => {
    if (Math.abs(s[1].pct_of_sid) > Math.abs(worst[1].pct_of_sid)) worst = s;
  });
  return `worst side ${worst[0]}, `
       + `${fmt(worst[1].deviation_from_central_line_mm, 1)} mm `
       + `(${fmt(worst[1].pct_of_sid, 2)} % of SID)`
       + (measured.length < sides.length
          ? ` · ${sides.length - measured.length} side(s) not measurable` : "");
}

/* Why a test passed, warned or failed. The status chip alone is not enough to
   troubleshoot with — especially on a phantom the definition does not match. */
function reasonsBlock(reasons, status) {
  const list = (reasons || []).filter(Boolean);
  if (!list.length) return "";
  // A test that does not apply is neither good news nor a warning.
  const cls = status === "pass" ? "reasons-pass"
            : status === "not applicable" ? "reasons-na" : "reasons-why";
  return `<div class="${cls}"><b>Why ${status || ""}:</b><ul>`
    + list.map(r => `<li>${html_escape(r)}</li>`).join("")
    + `</ul></div>`;
}

/* ---- Stage E ---- */
async function stageE(c) {
  c.innerHTML = `<h2>Stage E — Analysis</h2><p class="hint">Computing…</p>`;
  let r;
  try {
    r = await postJSON(`api/analyses/${S.aid}/compute`, { sid_mm: S.sid },
                       { timeoutMs: analysisTimeoutMs() });
  } catch (e) {
    stepFailed(c, e, () => stageE(c));
    return;
  }
  // Drawing is separated from computing on purpose. The measurement had
  // already succeeded when the results page threw on a value it did not
  // expect, and because the failure happened after this point the word
  // "Computing…" stayed on screen — so the operator was told the analysis was
  // still running when in fact it had finished minutes earlier.
  try {
    renderResults(c, r);
  } catch (e) {
    stepFailed(c, e, () => stageE(c));
  }
}

function renderResults(c, r) {
  S.results = r.results;
  S.baseline = r.baseline;
  const res = r.results;
  // Each entry carries what the summary row needs plus the detail behind it,
  // so the two can never disagree about a test's status.
  const cards = [];
  const card = (key, title, status, headline, html) =>
    cards.push({ key, title, status, headline, html });

  /* What a test could not measure at all. An empty table with no explanation
     reads as "nothing wrong here"; these are the objects that carry no number
     and why, so an absent answer looks absent. */
  const notMeasured = (t) => {
    const list = (t && t.not_measured) || [];
    if (!list.length) return "";
    return `<div class="reasons-why"><b>Not measured:</b><ul>`
      + list.map(x => `<li>${html_escape(x.id)} — `
                      + `${html_escape(x.reason || "no reason recorded")}</li>`).join("")
      + `</ul></div>`;
  };

  /* line pairs */
  const lp = res.linepairs || {};
  if (lp.rows && lp.rows.length) {
    const rows = lp.rows.map(row => {
      const lin = row.linearity || {};
      return `<tr><td>${row.id}</td><td class="num">${fmt(row.std, 1)}</td>
        <td class="num">${lin.measured_pitch_mm ? lin.measured_pitch_mm.toFixed(4) : "—"}</td>
        <td class="num">${lin.pitch_dev_pct !== undefined && lin.pitch_dev_pct !== null ? lin.pitch_dev_pct.toFixed(2) + " %" : "—"}</td>
        <td class="num">${lin.residual_rms_mm ? (lin.residual_rms_mm * 1000).toFixed(1) + " µm" : "—"}</td>
        <td>${chip(row.status)}</td></tr>
        ${row.status !== "pass" && row.reason
          ? `<tr class="reason-row"><td colspan="6">${html_escape(row.reason)}</td></tr>`
          : ""}`;
    }).join("");
    const worstLp = lp.rows.filter(x => x.status !== "pass").map(x => x.id);
    card("linepairs", "Line patterns (resolution)", lp.status,
      worstLp.length ? `${worstLp.length} of ${lp.rows.length} group(s) outside `
                       + `tolerance: ${worstLp.join(", ")}`
                     : `all ${lp.rows.length} groups within tolerance`,
      `${reasonsBlock(lp.reasons, lp.status)}
       <table><tr><th>group</th><th>SD</th><th>pitch [mm]</th><th>Δpitch</th>
       <th>grid RMS</th><th></th></tr>${rows}</table>
       <div id="lp-charts"></div>`);
  }

  /* wedge */
  const w = res.wedge || {};
  if (w.rows && w.rows.length) {
    const rows = w.rows.map(row =>
      `<tr><td>S${row.step}</td><td class="num">${fmt(row.mean, 1)}</td>
       <td class="num">${fmt(row.std, 1)}</td>
       <td>${row.saturated ? '<span class="chip fail">saturated</span>' : ""}</td></tr>`).join("");
    const sat = w.rows.filter(x => x.saturated).length;
    card("wedge", "Wedge (dynamic range)", w.status,
      `${w.monotonic ? "steps in order" : "STEPS NOT IN ORDER"}, `
      + `range ${fmt(w.dynamic_range_ratio, 1)}×`
      + (sat ? ` · ${sat} step(s) saturated` : ""),
      `${reasonsBlock(w.reasons, w.status)}
       <div class="kv"><div>R² (fit vs step index)</div><div>${fmt(w.fit.r2, 4)}
       (min ${w.r2_min})</div><div>slope</div><div>${fmt(w.fit.slope, 1)} /step</div>
       <div>monotonic</div><div>${w.monotonic}</div></div>
       <canvas class="mini-chart" id="chart-wedge" width="420" height="220"></canvas>
       <table><tr><th>step</th><th>mean</th><th>σ</th><th></th></tr>${rows}</table>`);
  }

  /* low contrast */
  const lc = res.lowcontrast || {};
  if (lc.rows && lc.rows.length) {
    // fmt() rather than .toFixed(): a disc that could not be measured used to
    // arrive here as null, and calling a number method on it threw — which is
    // what left this page on "Computing…". Such discs are now reported
    // separately, and this stays defensive so a future null cannot do it again.
    //
    // Named by the design level the disc carries (row.disc), as the chart and
    // the printed report are. With the insert fitted a half turn round the
    // ring's position id names a different disc, and the same disc was
    // appearing under two names on the screen and on paper.
    const rows = lc.rows.map(row =>
      `<tr><td>${html_escape(row.disc || row.id)}</td><td class="num">${fmt(row.cnr, 3)}</td>
       <td class="num">${fmt(row.obj_mean, 1)}</td>
       <td class="num">${fmt(row.bg_mean, 1)}</td></tr>`).join("");
    const visible = lc.rows.filter(
      x => typeof x.cnr === "number" && Math.abs(x.cnr) >= 0.2).length;
    // Turning the block no longer changes which disc is which — the insert
    // setting does — so the advice points there, not at Turn 180°.
    const orderWarn = lc.ordering_ok ? "" :
      '<p class="hint" style="color:var(--warn)">|CNR| is not in design order. '
      + ((lc.orientation || {}).conflict
         ? 'The contrast order looks like the other build of this phantom — '
           + 'check the phantom ID, or change the low-contrast insert setting '
           + 'in step C.</p>'
         : 'Check in step C that the rings sit on the discs, and which way '
           + 'round the low-contrast insert is set.</p>');
    card("lowcontrast", "Low contrast (visible discs)", lc.status,
      `${visible} of ${lc.rows.length} discs above CNR 0.2`
      + (lc.ordering_ok ? "" : " · NOT in design order"),
      `${reasonsBlock(lc.reasons, lc.status)}${orderWarn}${notMeasured(lc)}
       <canvas class="mini-chart" id="chart-lc" width="420" height="200"></canvas>
       <table><tr><th>circle</th><th>CNR</th><th>μ obj</th><th>μ bg</th></tr>
       ${rows}</table>`);
  }

  /* uniformity */
  const u = res.uniformity || {};
  if (u.rows && u.rows.length) {
    const rows = u.rows.map(row =>
      `<tr><td>${row.id}</td><td class="num">${fmt(row.mean, 1)}</td>
       <td class="num">${fmt(row.std, 2)}</td><td class="num">${fmt(row.snr, 1)}</td>
       <td class="num">${fmt(row.dsnr_pct, 2)} %</td><td>${chip(row.status)}</td></tr>`).join("");
    card("uniformity", "Uniformity (SNR across the field)", u.status,
      `worst corner ${fmt(u.max_abs_dsnr_pct, 1)} % from the average `
      + `(tolerance ${u.tolerance_pct} %)`,
      `${reasonsBlock(u.reasons, u.status)}${notMeasured(u)}
       <table><tr><th>square</th><th>μ</th><th>σ</th><th>SNR</th><th>ΔSNR</th>
       <th></th></tr>${rows}</table>
       <p class="hint">tolerance |ΔSNR| ≤ ${u.tolerance_pct}%</p>`);
  }

  /* geometry + field alignment had no card at all, so a "fail" overall could
     come from a test the user could not see */
  const gm = res.geometry || {};
  if (gm.dimension_status || gm.field_status) {
    const d = gm.dimensions || {};
    card("geometry", "Geometry &amp; dimensions", gm.dimension_status,
      `mean side ${fmt(d.mean_side_mm, 1)} mm `
      + `(${fmt(d.dev_from_nominal_pct, 2)} % from nominal)`,
      `${reasonsBlock(gm.dimension_reasons, gm.dimension_status)}
       <div class="kv">
         <div>mean side</div><div>${fmt(d.mean_side_mm)} mm</div>
         <div>deviation</div><div>${fmt(d.dev_from_nominal_pct)} %</div>
       </div>`);
    card("alignment", "X-ray field alignment", gm.field_status,
      fieldSummary(gm),
      reasonsBlock(gm.field_reasons, gm.field_status)
      || '<p class="hint">No further detail was recorded for this test.</p>');
  }

  /* A test whose geometry carried an _error, or whose compute() raised, comes
     back as {status:"n/a"|"error", error:"…"} with no rows — so none of the
     blocks above pushed a card for it. Without this it vanishes from the
     summary entirely and the verdict below claims every test passed, on a scan
     where a test was never measured at all. */
  const TEST_TITLES = {
    geometry: "Geometry &amp; dimensions",
    linepairs: "Line patterns (resolution)",
    lowcontrast: "Low contrast (visible discs)",
    uniformity: "Uniformity (SNR across the field)",
    wedge: "Wedge (dynamic range)",
  };
  TESTS.forEach(t => {
    if (cards.some(cd => cd.key === t)) return;
    const rr = res[t] || {};
    // Two different situations end up here and the operator has to be able to
    // tell them apart: the test broke (`error`), or the test ran perfectly well
    // and the image held nothing to measure (`reasons` + `not_measured`). The
    // first is a malfunction to report; the second is an exposure to repeat.
    const broke = !!rr.error;
    const why = rr.error
      || (rr.reasons && rr.reasons.length ? rr.reasons[0] : "")
      || "no result was produced for this test";
    const headline = broke ? "not analysed" : "not measured";
    const advice = broke
      ? `<p class="hint">This is a fault rather than a property of the scan.
           Note the message above before repeating the exposure.</p>`
      : `<p class="hint">Nothing in the image could be measured for this test.
           Go back to step C and place its measuring areas by hand, or — more
           usually — correct the exposure and repeat it.</p>`;
    card(t, TEST_TITLES[t] || t, rr.status || "n/a",
      `${headline} — ${html_escape(why)}`,
      `<p style="color:var(--fail)">This test was ${headline} on this
         scan: ${html_escape(why)}</p>
       ${notMeasured(rr)}
       ${advice}`);
  });

  const phantomName = (S.record && S.record.phantom) || "";
  const base = r.baseline
    ? `<p class="hint">Compared against the reference for
       ${phantomName ? `phantom <b>${html_escape(phantomName)}</b>` : "this phantom"}
       on this protocol: ${r.baseline.id}
       ${r.baseline.acquired_at
         ? `(${html_escape(r.baseline.acquired_at.slice(0, 16))})` : ""}.
       Differences are shown in the printable report.</p>`
    : `<p class="hint">No reference is stored for
       ${phantomName ? `phantom <b>${html_escape(phantomName)}</b>` : "this phantom"}
       on this protocol yet, so there is nothing to compare against. Mark this
       analysis as the reference in step F if it should become one.</p>`;

  // The summary answers "is anything wrong, and where"; the detail behind each
  // row answers "why". Most operators only ever need the first.
  const summaryRows = cards.map(cd =>
    `<tr><td>${cd.title}</td><td>${chip(cd.status)}</td>
     <td class="hint">${cd.headline}</td></tr>`).join("");
  // Anything short of a pass needs a look — "not measured" and the legacy
  // "n/a" included, since both can mean nothing was measured. The one
  // exception is a test that does not apply to this image: there is nothing
  // to look at, and the verdict names it instead.
  const needsLook = (s) => s !== "pass" && s !== "not applicable";
  const attention = cards.filter(cd => needsLook(cd.status));
  const skipped = cards.filter(cd => cd.status === "not applicable");
  const verdict = attention.length
    ? `<div class="reasons-why"><b>Needs attention:</b>
       ${attention.map(cd => cd.title).join(", ")}. The matching section below
       is already open, with the measured values and the reason.</div>`
    : `<div class="reasons-pass"><b>${skipped.length
         ? "Every test that applies to this image passed."
         : "Every test passed."}</b> The detail below is
       there if you want it.</div>`;

  const details = cards.map(cd => advanced(
    `${cd.title} ${chip(cd.status)}`, `<div class="card">${cd.html}</div>`,
    needsLook(cd.status))).join("");

  c.innerHTML = `<h2>Stage E — Analysis results</h2>
    <div class="card"><h3>Overall ${chip(r.overall)}</h3>
      ${verdictNotes(r.verdict_notes)
        ? `<p class="hint">${html_escape(r.overall || "")} — ${verdictNotes(r.verdict_notes)}</p>`
        : ""}
      <table><tr><th>test</th><th>result</th><th>measured</th></tr>
      ${summaryRows}</table></div>
    ${verdict}
    ${base}
    <h3>Detail per pattern</h3>
    <p class="hint">Anything that did not pass is already open.</p>
    ${details}
    <button class="primary" id="btn-confirm-e">Accept results → save</button>
    <button class="secondary" id="btn-back-d">Back to dimensions</button>`;

  drawWedgeChart(res);
  drawLcChart(res);
  drawLpCharts(res);

  $("#btn-confirm-e").addEventListener("click", async () => {
    await postJSON(`api/analyses/${S.aid}/confirm`, { stage: "E" });
    setStage("F");
  });
  $("#btn-back-d").addEventListener("click", () => setStage("D"));
}

/* The reference this phantom's future scans are compared against.

   Scoped to the phantom AND the protocol: two phantoms can differ by design and
   both be valid, so each needs its own. It is also removable — a reference
   chosen from a scan that later turns out to be poor has to be retractable. */
function baselineBlock(r) {
  const phantom = (r.phantom || "").trim();
  if (r.is_baseline) {
    return `<h3>Reference scan</h3>
      <p><span class="chip pass">★ this is the reference</span> for
      ${phantom ? `phantom <b>${html_escape(phantom)}</b>` : "unlabelled scans"}
      on this protocol.</p>
      <p class="hint">Every later scan of this phantom on this protocol is
      compared against it. Remove it if this scan turned out not to be a good
      reference — the phantom then simply has none until another is chosen.</p>
      <button class="secondary" id="btn-baseline">Remove as reference</button>`;
  }
  if (r.reduced_precision) {
    return `<h3>Reference scan</h3>
      <p class="hint">A reduced-precision analysis cannot be a reference: it is
      8-bit, lossy and carries no acquisition metadata.</p>`;
  }
  // Said before the button is pressed rather than after it is refused, and
  // with the way past it: only an administrator can make a failed exposure
  // the standard every later scan is judged against.
  if (qualityRefusesReference(r.quality)) {
    return `<h3>Reference scan</h3>
      <p class="hint">This image did not pass the quality check, so it cannot
      become the reference for ${phantom ? `phantom <b>${html_escape(phantom)}</b>`
      : "this phantom"} without an administrator's approval — every later scan
      would be compared against it. These checks failed:</p>
      ${failedChecksList(r.quality)}
      <button class="secondary" id="btn-baseline">Use anyway
        (administrator)…</button>`;
  }
  return `<h3>Reference scan</h3>
    <p class="hint">Marking this as the reference makes every later scan of
    ${phantom ? `phantom <b>${html_escape(phantom)}</b>` : "this phantom"} on
    this protocol compare against it. Each phantom has its own reference, so
    doing this does not affect any other phantom.${phantom ? ""
      : " <b>Name the phantom first</b>, or the reference will belong to every "
        + "unlabelled scan on this protocol."}</p>
    <button class="secondary" id="btn-baseline">Mark as the reference for
      this phantom</button>`;
}

async function toggleBaseline(value) {
  try {
    let r;
    // A scan that failed the quality check goes straight to the
    // administrator's panel: the page already knows the ordinary request
    // would be refused, and on a slow link a round trip to be told so is
    // worth saving. The server's refusal leads to the same panel, for a
    // record whose verdict changed after the page loaded it.
    if (value && qualityRefusesReference(S.record && S.record.quality)) {
      r = await overrideBaseline(S.aid, S.record.quality);
    } else {
      try {
        r = await postJSON(`api/analyses/${S.aid}/baseline`,
                           { baseline: value });
      } catch (e) {
        if (!(value && e.qualityRefused)) throw e;
        r = await overrideBaseline(S.aid, e.qualityRefused);
      }
    }
    if (!r) return;
    S.record.is_baseline = r.is_baseline ? 1 : 0;
    status(r.is_baseline
      ? `This is now the reference for `
        + `${r.phantom || "unlabelled scans"} on this protocol.`
        + (r.replaced.length
           ? ` It replaced ${r.replaced.join(", ")}.` : "")
        + (r.quality_override
           ? ` Recorded as an administrator's decision, with the reason, `
             + `although the image did not pass the quality check.` : "")
      : `Reference removed. ${r.phantom || "This phantom"} has no reference `
        + `until another scan is marked.`);
    renderStage();
  } catch (e) { status("Could not change the reference: " + e.message, true); }
}

/* ---- Stage F ---- */
function stageF(c) {
  const r = S.record || {};
  const identWarn = (!r.site && !r.phantom)
    ? '<p class="hint" style="color:var(--warn)">⚠ This analysis has no site or '
      + 'phantom, so it will not appear in any grouped trend. Use <b>Edit</b> in '
      + 'the identity bar above to add them — you can do this at any time.</p>'
    : "";
  const valBlock = `
    <h3>Validation</h3>
    <p>${valChip(r.validation_status)}${r.validated_by
        ? ` by <b>${html_escape(r.validated_by)}</b>`
          + (r.validated_at ? ` on ${html_escape(r.validated_at.slice(0, 16))}` : "")
        : ""}</p>
    ${r.validation_comment
      ? `<p class="hint">“${html_escape(r.validation_comment)}”</p>` : ""}
    <p class="hint">The administrator decides whether this phantom is accepted.
    The decision, the approver's name and any comment appear at the top of the
    printable report.</p>
    <button class="secondary" id="btn-validate-f">Set validation…</button>`;
  c.innerHTML = `<h2>Stage F — Save &amp; export</h2>
    <p>Analysis <b>${S.aid}</b> stored with full audit trail.</p>
    ${identWarn}
    ${valBlock}
    ${baselineBlock(r)}
    <button class="primary" id="btn-finalize">Finalize</button>
    <h3>Re-run the analysis</h3>
    <p class="hint">Measures this scan again from the file already on the
      server — it never needs uploading a second time, and the record keeps its
      id, labels and dates, so trends are not double-counted.</p>
    <button class="secondary" id="btn-rerun">Re-run analysis…</button>
    <h3>Export</h3>
    <p>
      <a href="api/analyses/${S.aid}/report.html" target="_blank">📄 Printable report</a><br>
      <a href="api/analyses/${S.aid}/export.csv" download="phantom_qa_${S.aid}.csv">⬇ CSV (flat metrics)</a><br>
      <a href="api/analyses/${S.aid}/export.json" target="_blank">⬇ JSON (full record)</a>
    </p>
    <h3>Source file integrity</h3>
    <p class="hint">The SHA-256 below fingerprints the exact file these results
    came from. Verifying re-hashes the copy the server kept and reports any
    mismatch — corruption, a wrong restore, or a swapped file.</p>
    <p class="mono" style="font-size:10.5px; word-break:break-all">
      ${(S.record && S.record.sha256) || ""}</p>
    <button class="secondary" id="btn-verify">Verify source file</button>
    <br>
    <button class="secondary" id="btn-new">New analysis</button>`;
  const bl = $("#btn-baseline");
  if (bl) bl.addEventListener("click", () => toggleBaseline(!r.is_baseline));
  $("#btn-finalize").addEventListener("click", async () => {
    try {
      // Finalising no longer touches the baseline: it is its own decision now,
      // and re-finalising must not silently clear a reference.
      const done = await postJSON(`api/analyses/${S.aid}/finalize`, {});
      // The page's own copy of the record has to learn it too. Until it did,
      // the Discard button stayed on screen after finalising, and pressing it
      // opened the administrator delete panel instead of a discard.
      if (S.record) {
        S.record.finalized_at = done.finalized_at || S.record.finalized_at;
        S.record.protection = [...new Set([...(S.record.protection || []),
                                           "finalized"])];
      }
      renderIdentityBar();
      renderStage();
      // Finished work belongs in History, not in a "continue this" banner.
      forgetOpenAnalysis();
      status("Finalized.");
    } catch (e) { status(e.message, true); }
  });
  $("#btn-validate-f").addEventListener("click", () =>
    setValidation(S.record, (v) => {
      Object.assign(S.record, v);
      renderIdentityBar();
      renderStage();
    }));
  $("#btn-verify").addEventListener("click", () => verifyAnalysis(S.aid));
  $("#btn-rerun").addEventListener("click", () => rerunAnalysis(S.aid));
  // One road to the upload step, so both entrances behave the same.
  $("#btn-new").addEventListener("click", goToUpload);
}

/* ================= mini charts ================= */

function chartAxes(ctx, W, H, pad, xmin, xmax, ymin, ymax) {
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = "#999"; ctx.lineWidth = 1;
  ctx.strokeRect(pad, 8, W - pad - 8, H - pad - 8 - 8 + 8 - 8);
  const sx = (x) => pad + (x - xmin) / (xmax - xmin || 1) * (W - pad - 8);
  const sy = (y) => (H - pad) - (y - ymin) / (ymax - ymin || 1) * (H - pad - 16);
  ctx.fillStyle = "#555"; ctx.font = "10px Segoe UI";
  ctx.fillText(String(Math.round(ymax)), 2, 14);
  ctx.fillText(String(Math.round(ymin)), 2, H - pad);
  return { sx, sy };
}

function drawWedgeChart(res) {
  const cv = $("#chart-wedge");
  if (!cv || !res.wedge || !res.wedge.rows) return;
  const ctx = cv.getContext("2d");
  const rows = res.wedge.rows;
  const xs = rows.map(r => r.step), ys = rows.map(r => r.mean);
  const { sx, sy } = chartAxes(ctx, cv.width, cv.height, 36,
    Math.min(...xs) - 0.5, Math.max(...xs) + 0.5,
    Math.min(...ys) * 0.95, Math.max(...ys) * 1.05);
  const f = res.wedge.fit;
  ctx.strokeStyle = "#d95050"; ctx.setLineDash([5, 4]);
  ctx.beginPath();
  ctx.moveTo(sx(xs[0]), sy(f.slope * xs[0] + f.intercept));
  ctx.lineTo(sx(xs[xs.length - 1]), sy(f.slope * xs[xs.length - 1] + f.intercept));
  ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = "#222";
  rows.forEach(r => {
    ctx.fillRect(sx(r.step) - 3, sy(r.mean) - 3, 6, 6);
  });
  ctx.fillStyle = "#555"; ctx.font = "10px Segoe UI";
  ctx.fillText("step index →", cv.width - 70, cv.height - 4);
}

function drawLcChart(res) {
  const cv = $("#chart-lc");
  if (!cv || !res.lowcontrast || !res.lowcontrast.rows) return;
  const ctx = cv.getContext("2d");
  const rows = res.lowcontrast.rows;
  const ys = rows.map(r => Math.abs(r.cnr));
  const ymax = Math.max(...ys, 0.1) * 1.15;
  const { sx, sy } = chartAxes(ctx, cv.width, cv.height, 36, 0, rows.length,
                               0, ymax);
  const bw = (cv.width - 44) / rows.length * 0.6;
  rows.forEach((r, i) => {
    ctx.fillStyle = "#4a7dbd";
    const x = sx(i + 0.5) - bw / 2, y = sy(Math.abs(r.cnr));
    ctx.fillRect(x, y, bw, sy(0) - y);
    ctx.fillStyle = "#555"; ctx.font = "10px Segoe UI";
    // The disc's design level, which is not its position on the block when
    // the insert is fitted a half turn round.
    ctx.fillText(r.disc || r.id, x, cv.height - 22);
  });
  ctx.fillStyle = "#555";
  const o = res.lowcontrast.orientation || {};
  ctx.fillText("|CNR| by design order"
               + (o.flipped ? " (insert fitted a half turn round)" : ""),
               40, 14);
}

function drawLpCharts(res) {
  const host = $("#lp-charts");
  if (!host || !res.linepairs || !res.linepairs.rows) return;
  res.linepairs.rows.forEach(row => {
    const lin = row.linearity || {};
    if (!lin.profile) return;
    const cv = el("canvas", { class: "mini-chart", width: 420, height: 130 });
    host.appendChild(cv);
    const ctx = cv.getContext("2d");
    const xs = lin.profile.pos_mm, ys = lin.profile.value;
    const { sx, sy } = chartAxes(ctx, cv.width, cv.height, 36,
      xs[0], xs[xs.length - 1], Math.min(...ys), Math.max(...ys));
    /* fitted line positions */
    (lin.grid_peaks_mm || []).forEach(p => {
      ctx.strokeStyle = "rgba(217,80,80,0.55)";
      ctx.beginPath(); ctx.moveTo(sx(p), sy(Math.min(...ys)));
      ctx.lineTo(sx(p), sy(Math.max(...ys))); ctx.stroke();
    });
    ctx.strokeStyle = "#333"; ctx.lineWidth = 0.8;
    ctx.beginPath();
    xs.forEach((x, i) => i === 0 ? ctx.moveTo(sx(x), sy(ys[i]))
                                 : ctx.lineTo(sx(x), sy(ys[i])));
    ctx.stroke();
    ctx.fillStyle = "#555"; ctx.font = "10px Segoe UI";
    ctx.fillText(`${row.id} profile [mm] — red = fitted grid`, 40, 12);
  });
}

/* ================= history & trends ================= */

function showTab(which) {
  $("#tab-analyze").classList.toggle("active", which === "analyze");
  $("#tab-history").classList.toggle("active", which === "history");
  $("#view-analyze").classList.toggle("hidden", which !== "analyze");
  $("#view-history").classList.toggle("hidden", which !== "history");
  if (which === "history") loadHistory();
  if (which === "analyze") resizeCanvas();
}
/* The first entry of the step bar is a way back, not just a label.

   Once an analysis was open there was no route to the upload step except
   reloading the page: the tabs only switch between the analysis and History,
   and "New analysis" appears on the last step alone. An operator who decided
   half way through to work on a different scan — or to upload another file —
   had nowhere to click, and said so.

   Leaving is not abandoning. The scan is on the server; it stays in History
   and in the unfinished list on the very page this opens, so it can be picked
   up again by this operator or a colleague. Only the first entry is
   clickable: the later steps are a record of where the work has got to, and
   step E starts a measurement the moment it is drawn. */
function goToUpload() {
  // clearAnalysisState also drops this browser's "continue" note, because it
  // is the same call used when a record is deleted. Here the record is very
  // much alive, so an unfinished one keeps its note and stays offered.
  const note = rememberedAnalysis();
  const unfinished = !!(S.record && !S.record.results
                        && !((S.record.validation_status || "").trim()));
  const keep = (note && S.aid && note.aid === S.aid && unfinished) ? note : null;
  clearAnalysisState();
  if (keep) {
    try { localStorage.setItem(OPEN_KEY, JSON.stringify(keep)); }
    catch (e) { /* private mode: the unfinished list still has it */ }
  }
  showTab("analyze");
  setStage("U");
  draw();
}

$("#tab-analyze").addEventListener("click", () => showTab("analyze"));
$("#tab-history").addEventListener("click", () => showTab("history"));
$("#nav-upload").addEventListener("click", goToUpload);
$("#nav-upload").addEventListener("keydown", (e) => {
  // It answers the keyboard like the button it says it is.
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); goToUpload(); }
});

/* current filter + selection state */
const H = { filter: { site: "", phantom: "", signature: "", validation: "" },
            order: "acquired", rows: [] };

/* One date cell. The scanner's clock and this server's clock are independent,
   and neither records a timezone, so an unusable acquisition date is called
   out rather than quietly replaced by the upload time. */
const ACQ_NOTE = {
  missing: "the scanner recorded no acquisition date for this file",
  implausible: "the acquisition date cannot be right — check the scanner clock",
};

function dateCell(a) {
  const flag = a.acquired_flag || "";
  const stamp = (a.acquired_at || "").slice(0, 16);
  if (!flag) return stamp || "<span class='hint'>—</span>";
  return `<span title="${ACQ_NOTE[flag]}" style="color:var(--warn)">`
       + `${stamp || "unknown"} ⚠</span>`;
}

function selectedIds() {
  return [...document.querySelectorAll("#history-table .sel:checked")]
    .map(cb => cb.dataset.id);
}

function filterQuery(extra = {}) {
  const p = new URLSearchParams();
  const ids = selectedIds();
  if (ids.length) p.set("ids", ids.join(","));
  else Object.entries(H.filter).forEach(([k, v]) => { if (v) p.set(k, v); });
  Object.entries(extra).forEach(([k, v]) => p.set(k, v));
  return p.toString();
}

function updateSelectionNote() {
  const n = selectedIds().length;
  $("#selection-note").textContent = n
    ? `${n} row${n > 1 ? "s" : ""} ticked — actions use the ticked rows`
    : "no rows ticked — actions use the filter above";
}

async function loadHistory() {
  const lab = await api("api/labels");
  const fill = (sel, items, cur) => {
    // the values are operator-typed labels; escape the display text the same
    // way the attribute is escaped, or a crafted label injects markup here
    sel.innerHTML = '<option value="">(all)</option>' + items.map(s =>
      `<option value="${s.value.replace(/"/g, "&quot;")}"${s.value === cur ? " selected" : ""}>` +
      `${html_escape(s.value)} (${s.count})</option>`).join("");
  };
  fill($("#f-site"), lab.site || [], H.filter.site);
  fill($("#f-phantom"), lab.phantom || [], H.filter.phantom);
  const sigs = await api("api/signatures");
  fill($("#f-signature"),
       sigs.signatures.map(s => ({ value: s.signature, count: s.count })),
       H.filter.signature);

  const q = new URLSearchParams();
  Object.entries(H.filter).forEach(([k, v]) => { if (v) q.set(k, v); });
  q.set("order", H.order);
  const r = await api("api/analyses?" + q.toString());
  H.rows = r.analyses;
  const flagged = r.analyses.filter(a => a.acquired_flag).length;
  $("#filter-count").textContent =
    `${r.analyses.length} analysis(es) match`
    + (flagged ? ` · ${flagged} with no usable acquisition date` : "");
  $("#f-order").value = H.order;

  const tb = $("#history-table tbody");
  tb.innerHTML = "";
  r.analyses.forEach(a => {
    const tr = el("tr", {}, `
      <td><input type="checkbox" class="sel" data-id="${a.id}"></td>
      <td>${dateCell(a)}</td>
      <td>${(a.created_at || "").slice(0, 16)}</td>
      <td>${a.site ? html_escape(a.site) : "<span class='hint'>—</span>"}</td>
      <td>${a.phantom ? html_escape(a.phantom) : "<span class='hint'>—</span>"}</td>
      <td>${html_escape(a.source_name || "")}${a.reduced_precision ? " ⚠" : ""}${
        a.quality_verdict === "poor"
          ? ` <span class="chip fail" title="This image did not pass the quality check — open it to see which checks failed. Only an administrator can make it the reference scan for its phantom.">image</span>`
          : ""}</td>
      <td style="font-size:11px">${exposureCell(a)}</td>
      <td style="font-size:11px">${a.signature ? html_escape(a.signature) : ""}</td>
      <td>${a.stage}</td><td>${chip(a.status)}${verdictNotes(a.verdict_notes)
            ? `<br><span class="hint">${verdictNotes(a.verdict_notes)}</span>` : ""}</td>
      <td>${valChip(a.validation_status)}${a.validated_by
            ? `<br><span class="hint">${html_escape(a.validated_by)}</span>` : ""}</td>
      <td><a href="#" class="base ${a.is_baseline ? "" : "hint"}"
             data-id="${a.id}" data-on="${a.is_baseline ? 1 : 0}"
             title="${a.is_baseline
               ? "The reference for this phantom on this protocol — click to remove"
               : "Make this the reference for this phantom on this protocol"}"
             >${a.is_baseline ? "★" : "☆"}</a></td>
      <td><a href="#" class="open" data-id="${a.id}">open</a> ·
          ${a.has_results ? `<a href="#" class="rerun" data-id="${a.id}"
             title="Measure this scan again from the file already on the server">re-run…</a> ·` : ""}
          <a href="api/analyses/${a.id}/report.html" target="_blank">report</a> ·
          <a href="#" class="edit" data-id="${a.id}">label</a> ·
          <a href="#" class="validate" data-id="${a.id}">validate</a> ·
          <a href="#" class="verify" data-id="${a.id}">verify</a> ·
          <a href="#" class="del danger" data-id="${a.id}">delete</a></td>`);
    tb.appendChild(tr);
  });
  tb.querySelectorAll("a.open").forEach(a => a.addEventListener("click", (e) => {
    e.preventDefault();
    openAnalysis(a.dataset.id);
  }));
  // Re-running used to mean opening the analysis first, just to reach step F's
  // button. This is that same panel and request, fed with the row — which
  // carries the record's protection list, so a finalised, signed-off or
  // reference record asks for the administrator password here exactly as it
  // does in step F, and an installation without one refuses it the same way.
  // Only a row with results carries the link: a record with nothing measured
  // has nothing to re-run, and opening it is the way on.
  tb.querySelectorAll("a.rerun").forEach(a => a.addEventListener("click", async (e) => {
    e.preventDefault();
    const rec = H.rows.find(x => x.id === a.dataset.id) || { id: a.dataset.id };
    const started = await rerunAnalysis(rec.id, rec);
    // A row that promised no protection and was refused anyway may simply be
    // out of date — someone finalised or signed the record since the list was
    // drawn. Re-read it, so the next attempt asks for what the record now
    // needs. A wrong password on a row that asked for one needs no re-read.
    if (started === false && !(rec.protection || []).length) loadHistory();
  }));
  tb.querySelectorAll("a.base").forEach(a => a.addEventListener("click", async (e) => {
    e.preventDefault();
    const on = a.dataset.on === "1";
    try {
      let r;
      try {
        r = await postJSON(`api/analyses/${a.dataset.id}/baseline`,
                           { baseline: !on });
      } catch (err) {
        // Refused because the image failed the quality check: show which
        // checks, and offer the administrator's override, rather than a bare
        // refusal that fades after six seconds.
        if (on || !err.qualityRefused) throw err;
        r = await overrideBaseline(a.dataset.id, err.qualityRefused);
        if (!r) return;
      }
      status(r.is_baseline
        ? `${r.phantom || "Unlabelled scans"} on this protocol now use `
          + `${a.dataset.id} as the reference.`
          + (r.replaced.length ? ` It replaced ${r.replaced.join(", ")}.` : "")
          + (r.quality_override
             ? ` Recorded as an administrator's decision, with the reason, `
               + `although the image did not pass the quality check.` : "")
        : `${r.phantom || "This phantom"} has no reference scan now.`);
      if (S.aid === a.dataset.id && S.record) {
        S.record.is_baseline = r.is_baseline ? 1 : 0;
        renderStage();
      }
      loadHistory();
    } catch (err) {
      status("Could not change the reference: " + err.message, true);
    }
  }));
  tb.querySelectorAll("a.del").forEach(a => a.addEventListener("click", async (e) => {
    e.preventDefault();
    await deleteAnalysis(a.dataset.id);
  }));
  tb.querySelectorAll("a.verify").forEach(a => a.addEventListener("click", async (e) => {
    e.preventDefault();
    await verifyAnalysis(a.dataset.id);
  }));
  tb.querySelectorAll("a.validate").forEach(a => a.addEventListener("click", async (e) => {
    e.preventDefault();
    const rec = H.rows.find(x => x.id === a.dataset.id) || { id: a.dataset.id };
    await setValidation(rec, () => {
      if (S.aid === rec.id && S.record) openAnalysis(rec.id);
      loadHistory();
    });
  }));
  tb.querySelectorAll("a.edit").forEach(a => a.addEventListener("click", async (e) => {
    e.preventDefault();
    const rec = H.rows.find(x => x.id === a.dataset.id) || {};
    const vals = await editLabelsDialog(rec, `Identification — ${rec.id}`);
    if (!vals) return;
    try {
      const out = await postJSON(`api/analyses/${a.dataset.id}/labels`, vals);
      if (out.layout_deleted)
        status(`Renamed. The stored measuring-point layout for phantom `
               + `${out.phantom_before} was discarded — no analyses carry that `
               + `name any more.`);
      if (S.aid === a.dataset.id && S.record) {
        Object.assign(S.record, vals);
        await refreshProfileForRecord();
        renderIdentityBar();
        renderStage();
      }
      loadHistory();
    } catch (err) { status("Could not save: " + err.message, true); }
  }));
  tb.querySelectorAll(".sel").forEach(cb =>
    cb.addEventListener("change", () => { updateSelectionNote(); loadTrends(); }));
  $("#sel-all").checked = false;
  updateSelectionNote();
  loadTrends();
}

["site", "phantom", "signature", "validation"].forEach(k => {
  $("#f-" + k).addEventListener("change", (e) => {
    H.filter[k] = e.target.value;
    loadHistory();
  });
});
$("#f-order").addEventListener("change", (e) => {
  H.order = e.target.value === "uploaded" ? "uploaded" : "acquired";
  loadHistory();
});
$("#btn-clear-filter").addEventListener("click", () => {
  H.filter = { site: "", phantom: "", signature: "", validation: "" };
  $("#f-validation").value = "";
  loadHistory();
});
$("#sel-all").addEventListener("change", (e) => {
  document.querySelectorAll("#history-table .sel").forEach(
    cb => { cb.checked = e.target.checked; });
  updateSelectionNote();
  loadTrends();
});

$("#btn-comparison").addEventListener("click", () => {
  const q = filterQuery();
  if (!q) { alert("Pick a site/phantom filter or tick some rows first."); return; }
  window.open("api/comparison_report.html?" + q, "_blank");
});
$("#btn-export-long").addEventListener("click", () => {
  window.location = "api/export.csv?" + filterQuery({ layout: "long" });
});
$("#btn-export-wide").addEventListener("click", () => {
  window.location = "api/export.csv?" + filterQuery({ layout: "wide" });
});

let trendData = null;
let trendLayout = null;          // point positions of the last draw, for hover
let trendWired = false;

/* A trend line through scans of DIFFERENT phantoms is not a trend — two
   builds legitimately differ, so the line would show assembly differences as
   if they were drift. The chart therefore draws nothing until the operator
   has said which scans belong together: the Phantom filter, or ticked rows. */
function trendSelectionMissing() {
  return !H.filter.phantom && !selectedIds().length;
}

//: X labels are only drawn as densely as they stay readable. ~80 px fits a
//: full YYYY-MM-DD at the chart's 11 px face with air on both sides.
const TREND_MIN_XLABEL_PX = 80;
const TREND_H = 340;
const TREND_PAD = { l: 64, r: 18, t: 30, b: 34 };

const cssVar = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

async function loadTrends() {
  const sect = $("#trend-section");
  if (sect && !sect.open) return;        // collapsed: fetch nothing
  const msel = $("#trend-metric");
  const cv = $("#trend-chart");

  if (trendSelectionMissing()) {
    trendData = null;
    trendLayout = null;
    msel.innerHTML = "";
    sizeTrendCanvas(cv);
    drawTrendMessage(cv,
      "Pick a Phantom in the filter above, or tick rows in the table.",
      "A line through different phantoms would show their assembly "
      + "differences as if they were drift over time.");
    $("#trend-note").textContent = "";
    return;
  }

  const q = filterQuery();
  try {
    trendData = await api("api/trends?" + q);
  } catch (e) { trendData = null; }
  if (!trendData || !trendData.analyses.length) {
    msel.innerHTML = "";
    trendLayout = null;
    sizeTrendCanvas(cv);
    drawTrendMessage(cv, "No completed analyses in this selection.",
      "Only analyses whose results were computed appear in a trend.");
    $("#trend-note").textContent = "";
    return;
  }
  const metrics = new Set();
  trendData.analyses.forEach(a => a.rows.forEach(row =>
    metrics.add(`${row.test} | ${row.object} | ${row.metric}`)));
  const prev = msel.value;
  const opts = [...metrics].sort();
  msel.innerHTML = opts.map(m =>
    `<option${m === prev ? " selected" : ""}>${m}</option>`).join("");
  msel.onchange = drawTrend;
  $("#trend-axis").onchange = drawTrend;
  wireTrendHover(cv);
  drawTrend();
}

/* Crisp on any display: the bitmap is sized to the element times the device
   pixel ratio. The old fixed 1000-px bitmap was CSS-scaled to fit, which
   blurred every label and let the rotated dates run off the bottom edge. */
function sizeTrendCanvas(cv) {
  // The layout width stays CSS's business ("100%"): pinning an inline pixel
  // width measured from the PARENT's clientWidth included the section's
  // padding, so the canvas came out wider than the space it sits in and
  // overflowed the container on the right. Only the BITMAP is sized here,
  // to the content box the canvas actually got (clientWidth excludes the
  // element's own border).
  cv.style.width = "100%";
  cv.style.height = TREND_H + "px";
  const cssW = Math.max(420, cv.clientWidth ||
                        (cv.parentElement.clientWidth - 24));
  const dpr = window.devicePixelRatio || 1;
  cv.width = Math.round(cssW * dpr);
  cv.height = Math.round(TREND_H * dpr);
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: cssW, h: TREND_H };
}

let trendMessage = null;             // last placeholder, for resize redraws

function drawTrendMessage(cv, line1, line2) {
  trendMessage = [line1, line2];
  const { ctx, w, h } = sizeTrendCanvas(cv);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = cssVar("--bg");
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = cssVar("--muted");
  ctx.textAlign = "center";
  ctx.font = "600 13px 'Segoe UI', system-ui, sans-serif";
  ctx.fillText(line1, w / 2, h / 2 - 10);
  ctx.font = "12px 'Segoe UI', system-ui, sans-serif";
  ctx.fillText(line2, w / 2, h / 2 + 12);
  ctx.textAlign = "left";
}

/* Round axis bounds to 1/2/5 steps so tick values read like numbers a person
   would choose, not like float noise. */
function niceTicks(lo, hi, target = 5) {
  const span = hi - lo || Math.abs(hi) || 1;
  const raw = span / target;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map(m => m * mag).find(st => span / st <= target)
            || 10 * mag;
  const first = Math.ceil(lo / step) * step;
  const out = [];
  for (let v = first; v <= hi + step * 1e-9; v += step) out.push(v);
  return out;
}

const fmtTick = (v) => {
  const a = Math.abs(v);
  if (a >= 10000 || (a > 0 && a < 0.01)) return v.toExponential(1);
  return (+v.toPrecision(4)).toLocaleString("en-US");
};

function drawTrend() {
  if (!trendData) return;
  const key = ($("#trend-metric").value || "").split(" | ");
  const axis = ($("#trend-axis") || {}).value || "acquired";
  // Which clock orders the series. The acquisition axis still falls back to
  // the upload time for a scan whose header carried no date, but marks it so
  // a run of such points cannot be mistaken for a real chronology.
  const stampOf = (a) => (axis === "uploaded"
    ? (a.created_at || "")
    : (a.acquired_at || a.created_at || "")).slice(0, 16);

  const pts = [];
  // Each phantom has its own reference, so a manual selection spanning
  // several can contain several. The +/-20 % band is only meaningful around
  // exactly one.
  const baseVals = [];
  const ordered = trendData.analyses.slice().sort(
    (p, q) => (stampOf(p) < stampOf(q) ? -1 : stampOf(p) > stampOf(q) ? 1 : 0));
  ordered.forEach(a => {
    const row = a.rows.find(r => r.test === key[0] && r.object === key[1]
                                 && r.metric === key[2]);
    if (row && typeof row.value === "number" && isFinite(row.value)) {
      const flagged = axis !== "uploaded" && !!a.acquired_flag;
      pts.push({ stamp: stampOf(a), y: row.value, id: a.id,
                 baseline: !!a.is_baseline, flagged,
                 who: [a.site, a.phantom].filter(Boolean).join(" / ") });
      if (a.is_baseline) baseVals.push(row.value);
    }
  });

  const cv = $("#trend-chart");
  const { ctx, w, h } = sizeTrendCanvas(cv);
  const C = {
    bg: cssVar("--bg"), panel: cssVar("--panel2"), border: cssVar("--border"),
    text: cssVar("--text"), muted: cssVar("--muted"),
    accent: cssVar("--accent"), pass: cssVar("--pass"), warn: cssVar("--warn"),
  };
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = C.bg;
  ctx.fillRect(0, 0, w, h);
  if (!pts.length) {
    drawTrendMessage(cv, "No values for this metric in the selection.", "");
    trendLayout = null;
    return;
  }

  const baseVal = baseVals.length === 1 ? baseVals[0] : null;
  const ys = pts.map(p => p.y);
  let ymin = Math.min(...ys), ymax = Math.max(...ys);
  if (baseVal !== null) {
    ymin = Math.min(ymin, baseVal * 0.78);
    ymax = Math.max(ymax, baseVal * 1.22);
  }
  const spread = (ymax - ymin) || Math.abs(ymax) * 0.2 || 1;
  ymin -= spread * 0.08; ymax += spread * 0.08;

  const plotW = w - TREND_PAD.l - TREND_PAD.r;
  const plotH = h - TREND_PAD.t - TREND_PAD.b;
  const sx = (i) => TREND_PAD.l + (pts.length === 1 ? plotW / 2
    : i / (pts.length - 1) * plotW);
  const sy = (y) => TREND_PAD.t + plotH - (y - ymin) / (ymax - ymin) * plotH;
  const font = (px, weight = "") =>
    `${weight ? weight + " " : ""}${px}px 'Segoe UI', system-ui, sans-serif`;

  // horizontal gridlines on nice values, labels in the left gutter
  ctx.font = font(11);
  ctx.textBaseline = "middle";
  niceTicks(ymin, ymax).forEach(v => {
    const y = sy(v);
    ctx.strokeStyle = C.border;
    ctx.globalAlpha = 0.45;
    ctx.beginPath(); ctx.moveTo(TREND_PAD.l, y);
    ctx.lineTo(w - TREND_PAD.r, y); ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = C.muted;
    ctx.textAlign = "right";
    ctx.fillText(fmtTick(v), TREND_PAD.l - 8, y);
  });

  // the constancy band around the single reference
  if (baseVal !== null) {
    const yTop = sy(1.2 * baseVal), yBot = sy(0.8 * baseVal);
    ctx.fillStyle = C.pass;
    ctx.globalAlpha = 0.08;
    ctx.fillRect(TREND_PAD.l, yTop, plotW, yBot - yTop);
    ctx.globalAlpha = 0.6;
    ctx.strokeStyle = C.pass;
    ctx.setLineDash([5, 4]);
    [yTop, yBot].forEach(y => {
      ctx.beginPath(); ctx.moveTo(TREND_PAD.l, y);
      ctx.lineTo(w - TREND_PAD.r, y); ctx.stroke();
    });
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
  }

  // soft area fill under the series, then the line itself
  if (pts.length > 1) {
    const grad = ctx.createLinearGradient(0, TREND_PAD.t, 0, h - TREND_PAD.b);
    grad.addColorStop(0, C.accent + "2e");
    grad.addColorStop(1, C.accent + "00");
    ctx.beginPath();
    pts.forEach((p, i) => i ? ctx.lineTo(sx(i), sy(p.y))
                            : ctx.moveTo(sx(0), sy(p.y)));
    ctx.lineTo(sx(pts.length - 1), h - TREND_PAD.b);
    ctx.lineTo(sx(0), h - TREND_PAD.b);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.beginPath();
    pts.forEach((p, i) => i ? ctx.lineTo(sx(i), sy(p.y))
                            : ctx.moveTo(sx(0), sy(p.y)));
    ctx.strokeStyle = C.accent;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.stroke();
    ctx.lineWidth = 1;
  }

  // points: smaller and unstroked in a dense series, ringed when sparse
  const dense = pts.length > 120;
  pts.forEach((p, i) => {
    const r = p.baseline ? 5 : (dense ? 2 : 3.5);
    ctx.beginPath();
    ctx.arc(sx(i), sy(p.y), r, 0, Math.PI * 2);
    ctx.fillStyle = p.baseline ? C.pass : (p.flagged ? C.warn : C.accent);
    ctx.fill();
    if (!dense || p.baseline) {
      ctx.strokeStyle = C.bg;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.lineWidth = 1;
    }
  });

  // x labels: horizontal, thinned so neighbours never collide, always the
  // first; later ones only where a full label fits
  const maxTicks = Math.max(2, Math.floor(plotW / TREND_MIN_XLABEL_PX));
  const every = Math.max(1, Math.ceil(pts.length / maxTicks));
  ctx.fillStyle = C.muted;
  ctx.font = font(11);
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  let lastX = -1e9;
  pts.forEach((p, i) => {
    if (i % every !== 0 && i !== pts.length - 1) return;
    const x = sx(i);
    if (x - lastX < TREND_MIN_XLABEL_PX * 0.9) return;
    lastX = x;
    ctx.strokeStyle = C.border;
    ctx.beginPath(); ctx.moveTo(x, h - TREND_PAD.b);
    ctx.lineTo(x, h - TREND_PAD.b + 4); ctx.stroke();
    // The tick stays on its point, but the TEXT is clamped inside the canvas:
    // the last point sits at the plot's right edge, and a label centred there
    // hangs half outside — the rightmost date was always cut.
    const text = p.stamp.slice(0, 10);
    const half = ctx.measureText(text).width / 2;
    const lx = Math.min(Math.max(x, half + 2), w - half - 2);
    ctx.fillText(text, lx, h - TREND_PAD.b + 8);
  });

  // header: the metric on the left, the series size on the right
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = C.text;
  ctx.font = font(12, "600");
  ctx.fillText($("#trend-metric").value || "", TREND_PAD.l, 18);
  ctx.fillStyle = C.muted;
  ctx.font = font(11);
  const nText = `${pts.length} scan${pts.length === 1 ? "" : "s"}`;
  ctx.textAlign = "right";
  ctx.fillText(nText, w - TREND_PAD.r, 18);
  ctx.textAlign = "left";

  trendMessage = null;
  trendLayout = { pts, w, h, sx: pts.map((_, i) => sx(i)),
                  sy: pts.map(p => sy(p.y)) };

  const note = $("#trend-note");
  if (note) {
    note.textContent = baseVals.length > 1
      ? `${baseVals.length} reference scans in this selection (one per phantom),`
        + " so no tolerance band is drawn — filter to a single phantom to see it."
      : (baseVals.length ? "" : "No reference scan in this selection.");
  }
}

/* Hover: the nearest point gets a crosshair and a card with the exact value —
   with a hundred points on screen, reading numbers off the line is guesswork. */
function wireTrendHover(cv) {
  if (trendWired) return;
  trendWired = true;
  cv.addEventListener("mousemove", (ev) => {
    if (!trendLayout) return;
    drawTrend();                                    // clean frame
    const L = trendLayout;
    const rect = cv.getBoundingClientRect();
    const mx = ev.clientX - rect.left;
    let best = -1, bestD = 24;
    L.sx.forEach((x, i) => {
      const d = Math.abs(x - mx);
      if (d < bestD) { bestD = d; best = i; }
    });
    if (best < 0) return;
    const ctx = cv.getContext("2d");
    const p = L.pts[best], x = L.sx[best], y = L.sy[best];
    const C = { border: cssVar("--border"), panel: cssVar("--panel"),
                text: cssVar("--text"), muted: cssVar("--muted"),
                accent: cssVar("--accent"), pass: cssVar("--pass"),
                warn: cssVar("--warn") };
    ctx.strokeStyle = C.border;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x, TREND_PAD.t);
    ctx.lineTo(x, L.h - TREND_PAD.b); ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(x, y, 6, 0, Math.PI * 2);
    ctx.strokeStyle = p.baseline ? C.pass : C.accent;
    ctx.lineWidth = 2; ctx.stroke(); ctx.lineWidth = 1;

    const lines = [
      p.stamp,
      fmtTick(p.y),
      p.who || "(unlabelled)",
      p.baseline ? "★ reference scan" : "",
      p.flagged ? "⚠ acquisition date unreliable" : "",
    ].filter(Boolean);
    ctx.font = "11px 'Segoe UI', system-ui, sans-serif";
    const bw = Math.max(...lines.map(t => ctx.measureText(t).width)) + 20;
    const bh = lines.length * 16 + 12;
    let bx = x + 12, by = Math.max(TREND_PAD.t, y - bh - 10);
    if (bx + bw > L.w - 4) bx = x - bw - 12;
    ctx.fillStyle = C.panel;
    ctx.strokeStyle = C.border;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(bx, by, bw, bh, 6);
    else ctx.rect(bx, by, bw, bh);       // pre-2022 browsers: square corners
    ctx.fill(); ctx.stroke();
    lines.forEach((t, i) => {
      ctx.fillStyle = i === 1 ? C.text : C.muted;
      ctx.font = i === 1 ? "600 12px 'Segoe UI', system-ui, sans-serif"
                         : "11px 'Segoe UI', system-ui, sans-serif";
      ctx.fillText(t, bx + 10, by + 18 + i * 16);
    });
  });
  cv.addEventListener("mouseleave", () => { if (trendLayout) drawTrend(); });
}

// Redraw at the new width when the window changes, and populate lazily the
// first time the collapsed section is opened.
window.addEventListener("resize", () => {
  const sect = $("#trend-section");
  if (!sect || !sect.open) return;
  if (trendData) drawTrend();
  else if (trendMessage) drawTrendMessage($("#trend-chart"), ...trendMessage);
});
const _trendSection = $("#trend-section");
if (_trendSection) {
  _trendSection.addEventListener("toggle", () => {
    if (_trendSection.open) loadTrends();
  });
}

/* ================= sign-out ================= */

async function initAuth() {
  try {
    const a = await api("api/auth");
    if (a.analysis_timeout_s) S.analysisTimeoutS = a.analysis_timeout_s;
    // Only an explicit "no sign-in" counts; anything else keeps packing.
    S.signInOff = a.auth_enabled === false;
    if (a.auth_enabled) {
      if (!a.authenticated) { window.location = "login"; return; }
      const btn = el("button", { id: "logout-btn", class: "tab" },
                     `Sign out (${a.user || ""})`);
      btn.addEventListener("click", async () => {
        await postJSON("api/logout", {});
        window.location = "login";
      });
      $("header nav").appendChild(btn);
    }
  } catch (e) { /* auth endpoint unavailable — leave UI as is */ }
}

/* ================= init ================= */
renderToggles();
setStage("U");
resizeCanvas();
initAuth();
