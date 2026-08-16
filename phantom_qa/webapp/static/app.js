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
  labels: true, selectedRoi: null, mode: "normal", manualCorners: [],
  fieldEdgeSide: null, dragRoi: null, dimPreview: null,
  pendingFile: null,
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, html = "") => {
  const e = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
  e.innerHTML = html;
  return e;
};
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v))
  ? "—" : (typeof v === "number" ? v.toFixed(d) : String(v));
const chip = (s) => `<span class="chip ${(s || "na").replace("/", "")}">${s || "n/a"}</span>`;

function csrfToken() {
  const m = document.cookie.match(/(?:^|;\s*)phantomqa_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

/* Every state-changing request carries the CSRF token from the cookie; the
   server rejects the request if the two do not match. */
async function api(path, opts = {}) {
  const o = { credentials: "same-origin", ...opts };
  const method = (o.method || "GET").toUpperCase();
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    o.headers = { ...(o.headers || {}), "X-CSRF-Token": csrfToken() };
  }
  const r = await fetch(path, o);
  if (r.status === 401) {
    window.location = "login";
    throw new Error("Session expired — signing in again");
  }
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* noop */ }
    throw new Error(msg);
  }
  return r.json();
}
const postJSON = (path, body) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body) });

function status(msg, isErr = false) {
  const n = $("#app-status");
  n.textContent = msg;
  n.style.color = isErr ? "var(--fail)" : "var(--muted)";
  if (msg) setTimeout(() => { if (n.textContent === msg) n.textContent = ""; }, 6000);
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
  const img = new Image();
  img.onload = () => {
    const first = !S.imgEl;
    S.imgEl = img;
    S.imgScale = img.width / S.nativeCols;
    if (first) zoomFit(); else draw();
  };
  img.src = `api/analyses/${S.aid}/image.png${params}`;
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

let wlTimer = null;
function onWL() {
  updateWLReadout();
  clearTimeout(wlTimer);
  wlTimer = setTimeout(() => {
    const a = wlAbsolute();
    if (a) loadImage(`?wc=${a.wc.toFixed(1)}&ww=${a.ww.toFixed(1)}`);
  }, 200);
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
    out.push({ roi: g.lowcontrast.block, test: "lowcontrast" });
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
  ctx2d.drawImage(S.imgEl, 0, 0);
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

  /* manual corner clicks */
  S.manualCorners.forEach((p, i) => {
    const s = nat2scr(p);
    ctx2d.fillStyle = "#ff4040";
    ctx2d.beginPath(); ctx2d.arc(s[0], s[1], 5, 0, Math.PI * 2); ctx2d.fill();
    ctx2d.fillText(String(i + 1), s[0] + 8, s[1]);
  });
}

/* ---- mouse interaction ---- */
let panning = null;
canvas.addEventListener("mousedown", (ev) => {
  const pos = [ev.offsetX, ev.offsetY];
  if (S.mode === "corners" || S.mode === "fieldedge") return;
  if (S.stage === "C") {
    const hit = hitRoi(pos);
    if (hit && hit.drag) {
      S.dragRoi = hit;
      S.selectedRoi = hit.roi.id;
      draw();
      return;
    }
  }
  panning = { start: pos, tx: S.view.tx, ty: S.view.ty };
  canvas.style.cursor = "grabbing";
});
canvas.addEventListener("mousemove", (ev) => {
  const pos = [ev.offsetX, ev.offsetY];
  const nat = scr2nat(pos);
  const mm = pxToMm(nat);
  $("#cursor-mm").textContent = mm
    ? `x ${mm[0].toFixed(1)} mm  y ${mm[1].toFixed(1)} mm` : "";
  if (S.dragRoi) {
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
  if (S.mode === "corners") {
    S.manualCorners.push(scr2nat(pos));
    draw();
    if (S.manualCorners.length === 4) await submitManualCorners();
    return;
  }
  if (S.mode === "fieldedge" && S.fieldEdgeSide) {
    await submitFieldEdge(scr2nat(pos));
    return;
  }
  if (S.dragRoi) {
    const roi = S.dragRoi.roi;
    S.dragRoi = null;
    try {
      const r = await postJSON(`api/analyses/${S.aid}/roi`,
        { roi_id: roi.id, center_px: roi.center_px });
      applyChanged(r);
      showRoiDetails(r.roi, r.stats);
      status(`${roi.id} moved — measurement updated`);
      draw();
    } catch (e) {
      status("ROI update failed: " + e.message, true);
      openAnalysis(S.aid);            // resync rather than show a stale ROI
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
  d.innerHTML = `<b>${roi.id}</b>${roi.manually_adjusted ?
      ' <span class="chip warn">manually adjusted</span>' : ""}<br>
    centre (${fmt(mm[0])}, ${fmt(mm[1])}) mm &nbsp;
    μ=${fmt(stats.mean, 1)} σ=${fmt(stats.std, 1)} n=${stats.n}
    ${rotatable ? `
    <div class="rot-row">
      <label>angle
        <input type="range" id="roi-angle" min="-90" max="90" step="0.5"
               value="${ang.toFixed(1)}">
      </label>
      <span id="roi-angle-val" class="mono">${ang.toFixed(1)}°</span>
      <button class="secondary-sm" id="roi-angle-minus">−1°</button>
      <button class="secondary-sm" id="roi-angle-plus">+1°</button>
    </div>
    <span class="hint">Drag the dot to move · rotate here or with [ and ]</span>`
    : ""}`;
  if (!rotatable) return;
  const slider = $("#roi-angle");
  const show = (v) => { $("#roi-angle-val").textContent = (+v).toFixed(1) + "°"; };
  slider.addEventListener("input", () => {
    show(slider.value);
    previewRotation(roi.id, +slider.value);
  });
  slider.addEventListener("change", () => commitRotation(roi.id, +slider.value));
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

/* Rotate locally for instant feedback; the server has the final word. */
function previewRotation(roiId, angleDeg) {
  const roi = findRoi(roiId);
  if (!roi || roi.type !== "rect" || !roi.corners_px) return;
  const delta = (angleDeg - (roi.angle_deg || 0));
  // phantom +y is up while image y is down, so a positive phantom rotation is
  // clockwise on screen
  const a = -delta * Math.PI / 180;
  const c = roi.center_px;
  roi.corners_px = roi.corners_px.map(p => {
    const dx = p[0] - c[0], dy = p[1] - c[1];
    return [c[0] + dx * Math.cos(a) - dy * Math.sin(a),
            c[1] + dx * Math.sin(a) + dy * Math.cos(a)];
  });
  roi.angle_deg = angleDeg;
  draw();
}

async function commitRotation(roiId, angleDeg) {
  try {
    const r = await postJSON(`api/analyses/${S.aid}/roi_rotate`,
                             { roi_id: roiId, angle_deg: angleDeg });
    applyChanged(r);
    showRoiDetails(r.roi, r.stats);
    status(`${roiId} rotated to ${angleDeg.toFixed(1)}° — measurement updated`);
    draw();
  } catch (e) {
    status("Rotation failed: " + e.message, true);
    openAnalysis(S.aid);
  }
}

function nudgeRotation(delta) {
  const slider = $("#roi-angle");
  if (!slider) return;
  slider.value = (+slider.value + delta).toFixed(1);
  $("#roi-angle-val").textContent = (+slider.value).toFixed(1) + "°";
  previewRotation(S.selectedRoi, +slider.value);
  commitRotation(S.selectedRoi, +slider.value);
}

document.addEventListener("keydown", (e) => {
  if (S.stage !== "C" || !S.selectedRoi) return;
  if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
  if (e.key === "[") { e.preventDefault(); nudgeRotation(-1); }
  if (e.key === "]") { e.preventDefault(); nudgeRotation(+1); }
});

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
      <button id="btn-edit-ident" class="secondary-sm">Edit</button>
    </div>
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
    ${missing ? '<div class="ident-warn">⚠ No site or phantom — this analysis '
      + 'will not appear in any grouped trend. Add them now.</div>' : ""}`;
  $("#btn-validate").addEventListener("click", () =>
    setValidation(r, (v) => { Object.assign(S.record, v); renderIdentityBar(); }));
  $("#btn-edit-ident").addEventListener("click", async () => {
    const vals = await editLabelsDialog(r, `Identification — ${r.id}`);
    if (!vals) return;
    try {
      await postJSON(`api/analyses/${S.aid}/labels`, vals);
      Object.assign(S.record, vals);
      renderIdentityBar();
      status("Identification updated.");
    } catch (e) { status("Could not save: " + e.message, true); }
  });
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
function validationDialog(rec) {
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

    const done = (result) => {
      back.classList.add("hidden");
      $("#v-save").onclick = null;
      $("#v-cancel").onclick = null;
      back.onclick = null;
      document.onkeydown = null;
      resolve(result);
    };
    $("#v-save").onclick = () => {
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
      done({ status, validated_by: by, comment: $("#v-comment").value.trim(),
             admin_password: $("#v-pw").value });
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
  const vals = await validationDialog(rec);
  if (!vals) return;
  try {
    const r = await postJSON(`api/analyses/${rec.id}/validation`, vals);
    status(`Recorded: ${VAL_LABEL[r.validation_status] || "pending review"}`
           + (r.validated_by ? ` (${r.validated_by})` : ""));
    if (onDone) onDone(r);
  } catch (e) { status("Validation refused: " + e.message, true); }
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

/* Deleting destroys the stored source file, so on a shared installation it
   needs the ADMIN password plus the analysis id typed back. */
async function deleteAnalysis(aid) {
  let policy = { enabled: true };
  try { policy = await api("api/deletion_policy"); } catch (e) { /* noop */ }
  if (!policy.enabled) {
    alert("Deletion is disabled on this installation.\n\nAn administrator must "
      + "set PHANTOMQA_ADMIN_PASSWORD_HASH in .env\n"
      + "(python -m phantom_qa.manage set-admin-password).");
    return;
  }
  const confirmId = prompt(
    `This permanently deletes analysis ${aid} AND its stored source file.\n`
    + `It cannot be undone.\n\nType the analysis id to confirm:`);
  if (confirmId === null) return;
  if (confirmId.trim() !== aid) { alert("The id did not match — nothing deleted."); return; }
  const pw = prompt("Administrator password (not your login password):");
  if (pw === null) return;
  const reason = prompt("Reason for deletion (recorded in the audit log):", "") || "";
  try {
    await postJSON(`api/analyses/${aid}/delete`,
                   { admin_password: pw, confirm_id: confirmId.trim(), reason });
    status(`Analysis ${aid} deleted.`);
    if (S.aid === aid) {
      S.aid = null; S.record = null; S.imgEl = null; S.geometry = null;
      S.results = null; S.reg = null;
      setStage("U");
      draw();
    }
    loadHistory();
  } catch (e) { status("Delete refused: " + e.message, true); }
}

/* ================= wizard stages ================= */

function setStage(st) {
  S.stage = st;
  document.querySelectorAll("#stage-nav li").forEach(li => {
    const s = li.dataset.stage;
    li.classList.toggle("active", s === st);
    li.classList.toggle("done",
      STAGES.indexOf(s) < STAGES.indexOf(st) && s !== "U");
  });
  const rd = $("#roi-details");
  if (rd && st !== "C") rd.remove();
  renderIdentityBar();
  renderStage();
  draw();
}

function renderStage() {
  const c = $("#stage-content");
  ({ U: stageU, A: stageA, B: stageB, C: stageC, D: stageD, E: stageE,
     F: stageF }[S.stage])(c);
}

/* ---- Stage U: upload (choose file, fill identity, then confirm) ---- */
async function stageU(c) {
  c.innerHTML = `<h2>New analysis</h2>
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
    <button class="secondary" id="btn-clear-file">Clear file</button>`;

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
      box.innerHTML = `<b>${f.name}</b> · ${(f.size / 1048576).toFixed(1)} MB`;
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
  refresh();
}

async function uploadFile(file) {
  const labels = {
    site: ($("#up-site") || {}).value.trim() || "",
    phantom: ($("#up-phantom") || {}).value.trim() || "",
    operator: ($("#up-operator") || {}).value.trim() || "",
    notes: ($("#up-notes") || {}).value.trim() || "",
  };
  ["site", "phantom", "operator"].forEach(
    k => sessionStorage.setItem("lbl_" + k, labels[k]));
  $("#btn-upload").disabled = true;
  status("Uploading and registering…");
  const fd = new FormData();
  fd.append("file", file);
  Object.entries(labels).forEach(([k, v]) => fd.append(k, v));
  try {
    const r = await api("api/analyses", { method: "POST", body: fd });
    const ok = r.analyses.filter(a => a.registered);
    if (!r.analyses.length) throw new Error("no images found");
    if (r.analyses.length > 1)
      status(`${r.analyses.length} images found — opening the first; others are in History.`);
    S.pendingFile = null;
    const first = ok[0] || r.analyses[0];
    await openAnalysis(first.id);
  } catch (e) {
    status("Upload failed: " + e.message, true);
    if ($("#btn-upload")) $("#btn-upload").disabled = false;
  }
}

async function openAnalysis(aid) {
  S.aid = aid;
  S.imgEl = null;
  S.geometry = null; S.results = null; S.selectedRoi = null;
  S.manualCorners = []; S.mode = "normal";
  const rec = await api(`api/analyses/${aid}`);
  S.record = rec;
  S.reg = rec.registration;
  S.geometry = rec.geometry;
  S.results = rec.results;
  S.sid = rec.sid_mm || 1000;
  S.nativeCols = S.reg ? S.reg.image.cols : (rec.meta.Columns || 3000);
  S.nativeRows = S.reg ? S.reg.image.rows : (rec.meta.Rows || 3000);
  loadImage();
  showTab("analyze");
  setStage(rec.geometry ? (rec.results ? "F" : rec.stage || "B") : "A");
}

/* ---- Stage A ---- */
function stageA(c) {
  if (!S.reg) { c.innerHTML = "<p>Registration unavailable.</p>"; return; }
  const s = S.reg.summary;
  const lmRows = Object.entries(S.reg.landmarks || {}).map(([side, l]) =>
    `<tr><td>${side}</td><td class="num">${l.err_mm !== null && l.err_mm !== undefined
       ? fmt(l.err_mm, 3) + " mm" : "not found"}</td></tr>`).join("");
  const rp = S.record.reduced_precision
    ? `<p class="hint" style="color:var(--warn)">⚠ reduced-precision input
       (plain image, no metadata)</p>` : "";
  c.innerHTML = `<h2>Stage A — Registration check</h2>${rp}
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
    <button class="secondary" id="btn-manual-corners">Manual corners…</button>`;
  $("#btn-confirm-a").addEventListener("click", async () => {
    status("Generating pattern proposals…");
    try {
      await postJSON(`api/analyses/${S.aid}/confirm`, { stage: "A" });
      const r = await postJSON(`api/analyses/${S.aid}/propose`, {});
      S.geometry = r.geometry;
      setStage("B");
      status("");
    } catch (e) { status(e.message, true); }
  });
  $("#btn-manual-corners").addEventListener("click", () => {
    S.mode = "corners"; S.manualCorners = [];
    status("Click the 4 phantom corners in order around the square.");
    draw();
  });
}

async function submitManualCorners() {
  S.mode = "normal";
  status("Re-registering with manual corners…");
  try {
    S.reg = await postJSON(`api/analyses/${S.aid}/register`,
      { corners_px: S.manualCorners });
    S.manualCorners = [];
    S.geometry = null;
    setStage("A");
    status("Re-registered.");
  } catch (e) {
    S.manualCorners = [];
    status("Manual registration failed: " + e.message, true);
  }
}

/* ---- Stage B ---- */
function stageB(c) {
  if (!S.geometry) { c.innerHTML = "<p>No proposals yet.</p>"; return; }
  const g = S.geometry;
  const rows = [];
  const add = (test, name, ok, extra = "") => rows.push(
    `<tr><td><span class="swatch" style="background:${COLORS[test]}"></span>
     ${name}</td><td>${ok ? "detected" : "NOT refined (nominal used)"}</td>
     <td>${extra}</td></tr>`);
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
  c.innerHTML = `<h2>Stage B — Pattern identification</h2>
    <p class="hint">Every detected pattern is outlined and labeled on the image.
    Verify each is the right object with the right label (zoom in!). A 180°
    mix-up or mislabeled group must be caught here.</p>
    <table><tr><th>pattern</th><th>detection</th><th></th></tr>${rows.join("")}</table>
    <button class="primary" id="btn-confirm-b">All patterns correct ✓</button>
    <button class="secondary" id="btn-back-a">Back to registration</button>`;
  $("#btn-confirm-b").addEventListener("click", async () => {
    await postJSON(`api/analyses/${S.aid}/confirm`, { stage: "B" });
    setStage("C");
  });
  $("#btn-back-a").addEventListener("click", () => setStage("A"));
}

/* ---- Stage C ---- */
function stageC(c) {
  const fieldBtns = ["top", "right", "bottom", "left"].map(s =>
    `<button class="secondary btn-field" data-side="${s}">${s}</button>`).join(" ");
  c.innerHTML = `<h2>Stage C — Measuring points</h2>
    <p class="hint">Click an ROI center dot to inspect μ/σ; drag it to adjust.
    Adjusted ROIs turn orange and are recorded in the audit trail. Low-contrast:
    solid = object ROI, dotted = background ROI, dashed = full circle outline.</p>
    <h3>Manual field-edge placement</h3>
    <p class="hint">If a field edge was not auto-detected (or looks wrong),
    choose a side and click the visible radiation-field edge on the image.</p>
    <div>${fieldBtns}</div>
    <button class="primary" id="btn-confirm-c">Measuring points confirmed ✓</button>
    <button class="secondary" id="btn-back-b">Back to patterns</button>`;
  document.querySelectorAll(".btn-field").forEach(b =>
    b.addEventListener("click", () => {
      S.mode = "fieldedge"; S.fieldEdgeSide = b.dataset.side;
      status(`Click the radiation-field edge on the ${b.dataset.side} side.`);
    }));
  $("#btn-confirm-c").addEventListener("click", async () => {
    await postJSON(`api/analyses/${S.aid}/confirm`, { stage: "C" });
    setStage("D");
  });
  $("#btn-back-b").addEventListener("click", () => setStage("B"));
}

async function submitFieldEdge(natPoint) {
  S.mode = "normal";
  try {
    const f = await postJSON(`api/analyses/${S.aid}/field_edge`,
      { side: S.fieldEdgeSide, point_px: natPoint });
    S.geometry.geometry.field_edges[S.fieldEdgeSide] = f;
    status(`Field edge ${S.fieldEdgeSide} set (${fmt(f.offset_from_edge_mm, 1)} mm outside phantom edge).`);
    draw();
  } catch (e) { status("Failed: " + e.message, true); }
  S.fieldEdgeSide = null;
}

/* ---- Stage D ---- */
async function stageD(c) {
  c.innerHTML = `<h2>Stage D — Dimension verification</h2>
    <p>SID <input type="number" id="sid-input" value="${S.sid}" step="10"> mm</p>
    <p class="hint">Computing dimensions…</p>`;
  let gr;
  try {
    const r = await postJSON(`api/analyses/${S.aid}/compute_preview`,
      { tests: ["geometry"], sid_mm: S.sid });
    gr = r.geometry;
    S.dimPreview = gr;
  } catch (e) {
    c.innerHTML += `<p style="color:var(--fail)">${e.message}</p>`;
    return;
  }
  const d = gr.dimensions || {};
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
  c.innerHTML = `<h2>Stage D — Dimension verification</h2>
    <p>SID <input type="number" id="sid-input" value="${S.sid}" step="10"> mm
       <button class="secondary" id="btn-recompute-d">recompute</button></p>
    <h3>Corner-mark dimensions ${chip(gr.dimension_status)}</h3>
    <table><tr><th>dimension</th><th>measured</th><th>nominal*</th><th>Δ</th></tr>
    ${dimsHtml}</table>
    <p class="hint">*nominal side ${nomS} mm is assumed design intent
    (no drawing available); calibrated reference is ${fmt(d.calibrated_side_mm, 1)} mm.</p>
    <h3>Side-mark rulers (0.5 cm pitch)</h3>
    <table><tr><th>side</th><th>pitch [mm]</th><th>Δpitch</th>
    <th>linearity RMS [mm]</th><th>central line from edge [mm]</th></tr>${rulRows}</table>
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
    <h3>X-ray field vs central lines ${chip(gr.field_status)}</h3>
    <table><tr><th>side</th><th>deviation [mm]</th><th>% of SID</th><th></th></tr>
    ${fieldRows}</table>
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

/* ---- Stage E ---- */
async function stageE(c) {
  c.innerHTML = `<h2>Stage E — Analysis</h2><p class="hint">Computing…</p>`;
  let r;
  try {
    r = await postJSON(`api/analyses/${S.aid}/compute`, { sid_mm: S.sid });
  } catch (e) {
    c.innerHTML = `<h2>Stage E — Analysis</h2>
      <p style="color:var(--fail)">${e.message}</p>`;
    return;
  }
  S.results = r.results;
  S.baseline = r.baseline;
  const res = r.results;
  const cards = [];

  /* line pairs */
  const lp = res.linepairs || {};
  if (lp.rows) {
    const rows = lp.rows.map(row => {
      const lin = row.linearity || {};
      return `<tr><td>${row.id}</td><td class="num">${fmt(row.std, 1)}</td>
        <td class="num">${lin.measured_pitch_mm ? lin.measured_pitch_mm.toFixed(4) : "—"}</td>
        <td class="num">${lin.pitch_dev_pct !== undefined && lin.pitch_dev_pct !== null ? lin.pitch_dev_pct.toFixed(2) + " %" : "—"}</td>
        <td class="num">${lin.residual_rms_mm ? (lin.residual_rms_mm * 1000).toFixed(1) + " µm" : "—"}</td>
        <td>${chip(row.status)}</td></tr>`;
    }).join("");
    cards.push(`<div class="card"><h3>Line patterns — SD &amp; linearity ${chip(lp.status)}</h3>
      <table><tr><th>group</th><th>SD</th><th>pitch [mm]</th><th>Δpitch</th>
      <th>grid RMS</th><th></th></tr>${rows}</table>
      <div id="lp-charts"></div></div>`);
  }

  /* wedge */
  const w = res.wedge || {};
  if (w.rows) {
    const rows = w.rows.map(row =>
      `<tr><td>S${row.step}</td><td class="num">${fmt(row.mean, 1)}</td>
       <td class="num">${fmt(row.std, 1)}</td>
       <td>${row.saturated ? '<span class="chip fail">saturated</span>' : ""}</td></tr>`).join("");
    cards.push(`<div class="card"><h3>Wedge (7 steps, positional) ${chip(w.status)}</h3>
      <div class="kv"><div>R² (fit vs step index)</div><div>${fmt(w.fit.r2, 4)}
      (min ${w.r2_min})</div><div>slope</div><div>${fmt(w.fit.slope, 1)} /step</div>
      <div>monotonic</div><div>${w.monotonic}</div></div>
      <canvas class="mini-chart" id="chart-wedge" width="420" height="220"></canvas>
      <table><tr><th>step</th><th>mean</th><th>σ</th><th></th></tr>${rows}</table></div>`);
  }

  /* low contrast */
  const lc = res.lowcontrast || {};
  if (lc.rows) {
    const rows = lc.rows.map(row =>
      `<tr><td>${row.id}</td><td class="num">${row.cnr.toFixed(3)}</td>
       <td class="num">${fmt(row.obj_mean, 1)}</td>
       <td class="num">${fmt(row.bg_mean, 1)}</td></tr>`).join("");
    cards.push(`<div class="card"><h3>Low contrast — CNR ${chip(lc.status)}</h3>
      <canvas class="mini-chart" id="chart-lc" width="420" height="200"></canvas>
      <table><tr><th>circle</th><th>CNR</th><th>μ obj</th><th>μ bg</th></tr>${rows}</table>
      ${lc.ordering_ok ? "" : '<p class="hint" style="color:var(--warn)">|CNR| not monotone with design order — check ROI placement.</p>'}</div>`);
  }

  /* uniformity */
  const u = res.uniformity || {};
  if (u.rows) {
    const rows = u.rows.map(row =>
      `<tr><td>${row.id}</td><td class="num">${fmt(row.mean, 1)}</td>
       <td class="num">${fmt(row.std, 2)}</td><td class="num">${fmt(row.snr, 1)}</td>
       <td class="num">${fmt(row.dsnr_pct, 2)} %</td><td>${chip(row.status)}</td></tr>`).join("");
    cards.push(`<div class="card"><h3>Uniformity — SNR ${chip(u.status)}</h3>
      <table><tr><th>square</th><th>μ</th><th>σ</th><th>SNR</th><th>ΔSNR</th><th></th></tr>
      ${rows}</table>
      <p class="hint">tolerance |ΔSNR| ≤ ${u.tolerance_pct}%</p></div>`);
  }

  const base = r.baseline ?
    `<p class="hint">Baseline for this protocol: ${r.baseline.id} — deltas shown
     in the report.</p>` :
    `<p class="hint">No baseline stored for this protocol signature yet — mark
     this analysis as baseline in Stage F if it should become the reference.</p>`;

  c.innerHTML = `<h2>Stage E — Analysis results</h2>
    <p>Overall: ${chip(r.overall)}</p>${base}${cards.join("")}
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
    <label><input type="checkbox" id="cb-baseline"> Mark as baseline for this
    protocol signature</label><br>
    <button class="primary" id="btn-finalize">Finalize</button>
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
  $("#btn-finalize").addEventListener("click", async () => {
    try {
      await postJSON(`api/analyses/${S.aid}/finalize`,
        { baseline: $("#cb-baseline").checked });
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
  $("#btn-new").addEventListener("click", () => {
    S.aid = null; S.imgEl = null; S.geometry = null; S.results = null;
    S.reg = null; S.record = null; S.pendingFile = null;
    setStage("U");
    draw();
  });
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
    ctx.fillText(r.id, x, cv.height - 22);
  });
  ctx.fillStyle = "#555";
  ctx.fillText("|CNR| by design order", 40, 14);
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
$("#tab-analyze").addEventListener("click", () => showTab("analyze"));
$("#tab-history").addEventListener("click", () => showTab("history"));

/* current filter + selection state */
const H = { filter: { site: "", phantom: "", signature: "", validation: "" },
            rows: [] };

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
    sel.innerHTML = '<option value="">(all)</option>' + items.map(s =>
      `<option value="${s.value.replace(/"/g, "&quot;")}"${s.value === cur ? " selected" : ""}>` +
      `${s.value} (${s.count})</option>`).join("");
  };
  fill($("#f-site"), lab.site || [], H.filter.site);
  fill($("#f-phantom"), lab.phantom || [], H.filter.phantom);
  const sigs = await api("api/signatures");
  fill($("#f-signature"),
       sigs.signatures.map(s => ({ value: s.signature, count: s.count })),
       H.filter.signature);

  const q = new URLSearchParams();
  Object.entries(H.filter).forEach(([k, v]) => { if (v) q.set(k, v); });
  const r = await api("api/analyses?" + q.toString());
  H.rows = r.analyses;
  $("#filter-count").textContent =
    `${r.analyses.length} analysis(es) match`;

  const tb = $("#history-table tbody");
  tb.innerHTML = "";
  r.analyses.forEach(a => {
    const tr = el("tr", {}, `
      <td><input type="checkbox" class="sel" data-id="${a.id}"></td>
      <td>${(a.acquired_at || a.created_at || "").slice(0, 16)}</td>
      <td>${a.site || "<span class='hint'>—</span>"}</td>
      <td>${a.phantom || "<span class='hint'>—</span>"}</td>
      <td>${a.source_name}${a.reduced_precision ? " ⚠" : ""}</td>
      <td style="font-size:11px">${a.signature || ""}</td>
      <td>${a.stage}</td><td>${chip(a.status)}</td>
      <td>${valChip(a.validation_status)}${a.validated_by
            ? `<br><span class="hint">${a.validated_by}</span>` : ""}</td>
      <td>${a.is_baseline ? "★" : ""}</td>
      <td><a href="#" class="open" data-id="${a.id}">open</a> ·
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
      await postJSON(`api/analyses/${a.dataset.id}/labels`, vals);
      if (S.aid === a.dataset.id && S.record) {
        Object.assign(S.record, vals);
        renderIdentityBar();
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
async function loadTrends() {
  const q = filterQuery();
  try {
    trendData = await api("api/trends?" + q);
  } catch (e) { trendData = null; }
  const msel = $("#trend-metric");
  if (!trendData || !trendData.analyses.length) {
    msel.innerHTML = "";
    const cv = $("#trend-chart");
    cv.getContext("2d").clearRect(0, 0, cv.width, cv.height);
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
  drawTrend();
}

function drawTrend() {
  if (!trendData) return;
  const key = ($("#trend-metric").value || "").split(" | ");
  const pts = [];
  let baseVal = null;
  trendData.analyses.forEach(a => {
    const row = a.rows.find(r => r.test === key[0] && r.object === key[1]
                                 && r.metric === key[2]);
    if (row && typeof row.value === "number") {
      pts.push({ x: (a.acquired_at || a.created_at).slice(0, 16), y: row.value,
                 baseline: a.is_baseline,
                 label: [a.site, a.phantom].filter(Boolean).join(" / ") });
      if (a.is_baseline) baseVal = row.value;
    }
  });
  const cv = $("#trend-chart"), ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!pts.length) { ctx.fillText("no data", 30, 30); return; }
  const ys = pts.map(p => p.y);
  let ymin = Math.min(...ys), ymax = Math.max(...ys);
  if (baseVal !== null) {
    ymin = Math.min(ymin, baseVal * 0.75);
    ymax = Math.max(ymax, baseVal * 1.25);
  }
  const range = (ymax - ymin) || Math.abs(ymax) * 0.2 || 1;
  ymin -= range * 0.1; ymax += range * 0.1;
  const pad = 60;
  const sx = (i) => pad + i / Math.max(pts.length - 1, 1) * (cv.width - pad - 20);
  const sy = (y) => (cv.height - 40) - (y - ymin) / (ymax - ymin) * (cv.height - 60);
  ctx.strokeStyle = "#999";
  ctx.strokeRect(pad, 20, cv.width - pad - 20, cv.height - 60);
  ctx.fillStyle = "#555"; ctx.font = "11px Segoe UI";
  ctx.fillText(ymax.toPrecision(5), 4, 26);
  ctx.fillText(ymin.toPrecision(5), 4, cv.height - 40);
  if (baseVal !== null) {
    ctx.strokeStyle = "rgba(53,180,92,0.8)";
    ctx.setLineDash([6, 5]);
    [0.8 * baseVal, 1.2 * baseVal].forEach(v => {
      ctx.beginPath(); ctx.moveTo(pad, sy(v));
      ctx.lineTo(cv.width - 20, sy(v)); ctx.stroke();
    });
    ctx.setLineDash([]);
  }
  ctx.strokeStyle = "#4a7dbd"; ctx.lineWidth = 1.5;
  ctx.beginPath();
  pts.forEach((p, i) => i === 0 ? ctx.moveTo(sx(i), sy(p.y))
                                : ctx.lineTo(sx(i), sy(p.y)));
  ctx.stroke();
  pts.forEach((p, i) => {
    ctx.fillStyle = p.baseline ? "#35b45c" : "#4a7dbd";
    ctx.beginPath();
    ctx.arc(sx(i), sy(p.y), p.baseline ? 6 : 4, 0, Math.PI * 2);
    ctx.fill();
    if (p.baseline) {
      ctx.fillStyle = "#35b45c"; ctx.font = "12px Segoe UI";
      ctx.fillText("★", sx(i) - 4, sy(p.y) - 9);
    }
    ctx.fillStyle = "#777"; ctx.font = "9px Segoe UI";
    ctx.save();
    ctx.translate(sx(i), cv.height - 34);
    ctx.rotate(0.5);
    ctx.fillText(p.x, 0, 8);
    ctx.restore();
  });
  ctx.fillStyle = "#333"; ctx.font = "11px Segoe UI";
  ctx.fillText($("#trend-metric").value || "", pad, 14);
}

/* ================= sign-out ================= */

async function initAuth() {
  try {
    const a = await api("api/auth");
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
