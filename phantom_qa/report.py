"""Self-contained printable HTML report for a single analysis.

Charts are rendered server-side with matplotlib and inlined as base64 PNGs, so
the report file has no external dependencies (offline/archive friendly).
"""

from __future__ import annotations

import base64
import html
import io

import numpy as np

from .store import flatten_results


def _fig_to_b64(fig) -> str:
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _charts(results: dict) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    charts = {}

    w = results.get("wedge") or {}
    if w.get("rows"):
        steps = [r["step"] for r in w["rows"]]
        means = [r["mean"] for r in w["rows"]]
        fit = w.get("fit") or {}
        fig, ax = plt.subplots(figsize=(5, 3.4))
        ax.plot(steps, means, "ks", label="measured mean")
        if fit:
            xs = np.array([min(steps), max(steps)])
            ax.plot(xs, fit["slope"] * xs + fit["intercept"], "r--",
                    label=f"linear fit  R² = {fit['r2']:.4f}")
        ax.set_xlabel("wedge step (position index)")
        ax.set_ylabel("mean pixel value")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        charts["wedge"] = _fig_to_b64(fig)

    lc = results.get("lowcontrast") or {}
    if lc.get("rows"):
        levels = [r["level"] for r in lc["rows"]]
        cnrs = [r["abs_cnr"] for r in lc["rows"]]
        fig, ax = plt.subplots(figsize=(5, 3.4))
        ax.bar([f"L{l}" for l in levels], cnrs, color="#4a7dbd")
        ax.set_xlabel("circle (design order)")
        ax.set_ylabel("|CNR|")
        ax.grid(alpha=0.3, axis="y")
        charts["lowcontrast"] = _fig_to_b64(fig)

    lp = results.get("linepairs") or {}
    rows = [r for r in lp.get("rows", []) if (r.get("linearity") or {}).get("profile")]
    if rows:
        fig, axes = plt.subplots(len(rows), 2, figsize=(10, 2.1 * len(rows)),
                                 gridspec_kw={"width_ratios": [2, 1]})
        if len(rows) == 1:
            axes = np.array([axes])
        for i, r in enumerate(rows):
            lin = r["linearity"]
            prof = lin["profile"]
            ax = axes[i, 0]
            ax.plot(prof["pos_mm"], prof["value"], lw=0.7, color="#333")
            for p in lin.get("grid_peaks_mm", []):
                ax.axvline(p, color="#d95050", lw=0.5, alpha=0.6)
            ax.set_ylabel(r["id"], fontsize=8)
            ax.tick_params(labelsize=7)
            if i == 0:
                ax.set_title("profile across group (red = fitted line positions)",
                             fontsize=8)
            if i == len(rows) - 1:
                ax.set_xlabel("position [mm]", fontsize=8)
            ax2 = axes[i, 1]
            resid = lin.get("grid_residuals_mm")
            if resid:
                ax2.stem(np.arange(len(resid)), np.array(resid) * 1000,
                         basefmt=" ")
                ax2.axhline(0, color="k", lw=0.5)
                pd = lin.get("pitch_dev_pct")
                ax2.set_title(
                    f"pitch {lin.get('measured_pitch_mm'):.4f} mm "
                    f"({pd:+.2f}% vs 1/{r['freq_lp_mm']})", fontsize=7)
            ax2.set_ylabel("resid [µm]", fontsize=7)
            ax2.tick_params(labelsize=7)
        fig.tight_layout()
        charts["linepairs"] = _fig_to_b64(fig)

    return charts


def _status_chip(s: str) -> str:
    color = {"pass": "#2e9e44", "warn": "#d9a021", "fail": "#cf3f3f",
             "n/a": "#888", "error": "#cf3f3f"}.get(s, "#888")
    return (f'<span style="background:{color};color:#fff;padding:1px 8px;'
            f'border-radius:9px;font-size:11px">{html.escape(str(s))}</span>')


def build_report(record: dict, overlay_png: bytes | None = None,
                 baseline: dict | None = None) -> str:
    results = record.get("results") or {}
    meta = record.get("meta") or {}
    reg = (results.get("_meta") or {}).get("registration") or {}
    charts = _charts(results)

    baseline_map = {}
    if baseline and baseline.get("results"):
        for row in flatten_results(baseline["results"]):
            baseline_map[(row["test"], row["object"], row["metric"])] = row["value"]

    rows_html = []
    for row in flatten_results(results):
        key = (row["test"], row["object"], row["metric"])
        base = baseline_map.get(key)
        delta = ""
        if base not in (None, 0) and isinstance(row["value"], (int, float)):
            delta = f"{100.0 * (row['value'] - base) / abs(base):+.1f}%"
        val = (f"{row['value']:.4g}" if isinstance(row["value"], float)
               else str(row["value"]))
        rows_html.append(
            f"<tr><td>{row['test']}</td><td>{row['object']}</td>"
            f"<td>{row['metric']}</td><td class='num'>{val}</td>"
            f"<td>{row['unit']}</td><td>{row['status']}</td>"
            f"<td class='num'>{html.escape(str(base)) if base is None else (f'{base:.4g}' if isinstance(base, float) else base)}"
            f"</td><td class='num'>{delta}</td></tr>")

    statuses = {
        "Geometry / dimensions": (results.get("geometry") or {}).get("dimension_status", "n/a"),
        "Field alignment": (results.get("geometry") or {}).get("field_status", "n/a"),
        "Line pairs": (results.get("linepairs") or {}).get("status", "n/a"),
        "Low contrast": (results.get("lowcontrast") or {}).get("status", "n/a"),
        "Uniformity": (results.get("uniformity") or {}).get("status", "n/a"),
        "Wedge": (results.get("wedge") or {}).get("status", "n/a"),
    }
    summary = "".join(f"<tr><td>{html.escape(k)}</td><td>{_status_chip(v)}</td></tr>"
                      for k, v in statuses.items())

    overlay_html = ""
    if overlay_png:
        b64 = base64.b64encode(overlay_png).decode()
        overlay_html = (f'<h2>Confirmed geometry overlay</h2>'
                        f'<img style="max-width:100%" src="data:image/png;base64,{b64}">')

    charts_html = ""
    titles = {"wedge": "Wedge response", "lowcontrast": "Low-contrast |CNR|",
              "linepairs": "Line-pattern profiles & linearity"}
    for k, b64 in charts.items():
        charts_html += (f"<h3>{titles.get(k, k)}</h3>"
                        f'<img style="max-width:100%" src="data:image/png;base64,{b64}">')

    audit_html = "".join(
        f"<tr><td>{html.escape(a['ts'])}</td><td>{html.escape(a['stage'])}</td>"
        f"<td>{html.escape(a['action'])}</td>"
        f"<td>{html.escape(str(a.get('detail') or ''))}</td></tr>"
        for a in (record.get("audit") or []))

    meta_rows = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(meta[k]))}</td></tr>"
        for k in ("Manufacturer", "ManufacturerModelName", "StudyDate", "KVP",
                  "ExposureInuAs", "AcquisitionDeviceProcessingDescription",
                  "TransferSyntax") if k in meta)

    reduced = ('<p style="background:#fff3cd;border:1px solid #d9a021;padding:8px">'
               '⚠ REDUCED-PRECISION MODE: plain-image input (no DICOM metadata, '
               'lossy 8-bit data). Excluded from baselines by default.</p>'
               if record.get("reduced_precision") else "")

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Phantom QA report {html.escape(record['id'])}</title>
<style>
 body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color:#222; }}
 table {{ border-collapse: collapse; margin: 8px 0 18px; }}
 td, th {{ border: 1px solid #ccc; padding: 3px 9px; font-size: 12px; }}
 th {{ background: #f0f0f0; }}
 td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
 h1 {{ font-size: 20px; }} h2 {{ font-size: 16px; margin-top: 26px; }}
 @media print {{ .noprint {{ display:none; }} }}
</style></head><body>
<h1>MSF Phantom QA report</h1>
<p><b>Analysis:</b> {html.escape(record['id'])} &nbsp; <b>Created:</b> {html.escape(record['created_at'])}
&nbsp; <b>Source:</b> {html.escape(record['source_name'])}<br>
<b>SHA-256:</b> <code style="font-size:10px">{html.escape(record['sha256'])}</code><br>
<b>Protocol signature:</b> {html.escape(record.get('signature') or '')} &nbsp;
<b>Algorithm:</b> v{html.escape(record.get('algo_version') or '')} &nbsp;
<b>SID:</b> {record.get('sid_mm') or 1000.0} mm</p>
{reduced}
<h2>Summary</h2>
<table>{summary}</table>
<h2>Registration</h2>
<p>rotation {reg.get('rotation_deg', float('nan')):.2f}&deg;, mirrored {reg.get('mirrored')},
scale {reg.get('mm_per_px', float('nan')):.5f} mm/px, landmark residual RMS
{(reg.get('residual_rms_mm') if reg.get('residual_rms_mm') is not None else float('nan')):.3f} mm</p>
<h2>Acquisition metadata</h2>
<table>{meta_rows}</table>
<h2>All metrics {('(vs baseline ' + html.escape(baseline['id']) + ')') if baseline else ''}</h2>
<table><tr><th>test</th><th>object</th><th>metric</th><th>value</th><th>unit</th>
<th>status</th><th>baseline</th><th>&Delta;</th></tr>{''.join(rows_html)}</table>
{charts_html}
{overlay_html}
<h2>Audit trail</h2>
<table><tr><th>time</th><th>stage</th><th>action</th><th>detail</th></tr>{audit_html}</table>
</body></html>"""
