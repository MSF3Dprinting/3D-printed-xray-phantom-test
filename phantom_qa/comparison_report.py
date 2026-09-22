"""Visual comparison report across several analyses.

Works for two different questions with the same layout:

  * one phantom over time      -> the entities happen to be dates
  * many phantoms side by side -> the entities are different phantoms/sites

Because "many phantoms" has no meaningful first entry, every deviation is
measured against the **median of the selection**, not against the first scan.
The output is plots rather than tables — the numeric table is kept as a
collapsed appendix. Every visual identifies entries by Site / Phantom ID.
Near the top, a table of pictures shows every test area of every scan side by
side, so the images themselves can be compared and not only their numbers.
"""

from __future__ import annotations

import base64
import hashlib
import html
import io

import numpy as np

from .store import flatten_results
from .thumbnails import REGIONS as _PICTURE_REGIONS

#: "not measured" counts as a warning and shares its colour; "not applicable"
#: shares the grey of the legacy "n/a" it partly replaces.
_STATUS_COLOR = {"pass": "#2e9e44", "warn": "#d9a021", "fail": "#cf3f3f",
                 "n/a": "#9aa4b2", "error": "#cf3f3f", "": "#9aa4b2",
                 "not measured": "#d9a021", "not applicable": "#9aa4b2"}

_VALIDATION_COLOR = {"validated": "#2e9e44",
                     "conditionally_validated": "#d9a021",
                     "not_validated": "#cf3f3f",
                     "": "#9aa4b2"}
_VALIDATION_SHORT = {"validated": "validated",
                     "conditionally_validated": "conditional",
                     "not_validated": "NOT validated",
                     "": "pending"}

_SECTION_TITLES = {
    "geometry": "Geometry & dimensions",
    "alignment": "X-ray field alignment",
    "linepairs": "Line patterns (spatial resolution)",
    "lowcontrast": "Low contrast",
    "uniformity": "Uniformity",
    "wedge": "Attenuation wedge (dynamic range)",
}
_SECTION_ORDER = ["linepairs", "lowcontrast", "uniformity", "wedge",
                  "geometry", "alignment"]

# Metrics worth a dedicated per-object comparison panel.
_PANELS = {
    "linepairs": [("sd", "SD inside the group ROI", ""),
                  ("pitch_dev", "Pitch deviation vs nominal", "%")],
    "lowcontrast": [("cnr", "CNR per circle", "")],
    "uniformity": [("snr", "SNR per square", ""),
                   ("dsnr", "ΔSNR vs the scan's own mean", "%")],
    "wedge": [("mean", "Mean value per step", "")],
    "geometry": [("pitch", "Ruler pitch per side", "mm"),
                 ("linearity_rms", "Ruler mark linearity RMS", "mm")],
}


def _b64(fig) -> str:
    """A chart as a 64-colour palette PNG.

    The same encoding the single report uses, for the same reason: a chart is
    a few flat colours and text, so a palette loses nothing visible and halves
    the bytes — which matters more here, where the charts repeat for every
    test and the page now also carries the pictures. Max-coverage keeps the
    colours the chart actually uses (the faster octree greyed the white page).
    """
    import matplotlib.pyplot as plt
    from PIL import Image
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=115, bbox_inches="tight")
    plt.close(fig)
    chart = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    out = io.BytesIO()
    chart.quantize(colors=64, method=Image.Quantize.MAXCOVERAGE).save(
        out, format="png", optimize=True)
    return base64.b64encode(out.getvalue()).decode()


def _img(b64, cls="") -> str:
    return f'<img class="{cls}" src="data:image/png;base64,{b64}">' if b64 else ""


def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


# ------------------------------------------------------------------- entities

def _entity_labels(records) -> list[str]:
    """Short unique label per analysis, always carrying Site / Phantom.

    Duplicates (the same phantom measured repeatedly) are disambiguated with
    the acquisition date, and only then with a sequence number."""
    base = []
    for r in records:
        site = (r.get("site") or "").strip()
        ph = (r.get("phantom") or "").strip()
        if site and ph:
            base.append(f"{site} / {ph}")
        elif ph:
            base.append(ph)
        elif site:
            base.append(site)
        else:
            base.append(f"(unlabelled {r['id'][:6]})")
    counts = {}
    for b in base:
        counts[b] = counts.get(b, 0) + 1
    out, used = [], {}
    for b, r in zip(base, records):
        if counts[b] == 1:
            out.append(b)
            continue
        date = (r.get("acquired_at") or r.get("created_at") or "")[:10]
        cand = f"{b}\n{date}"
        used[cand] = used.get(cand, 0) + 1
        if used[cand] > 1:
            cand = f"{b}\n{date} #{used[cand]}"
        out.append(cand)
    return out


def _order(records):
    """Group the same phantom together; within a phantom, chronological.
    That is time order for a single-phantom series and a sensible grouping
    for a multi-phantom comparison."""
    return sorted([r for r in records if r.get("results")],
                  key=lambda r: ((r.get("site") or "~").lower(),
                                 (r.get("phantom") or "~").lower(),
                                 r.get("acquired_at") or r.get("created_at")))


def _collect(records):
    ordered = _order(records)
    keys, seen, maps = [], set(), []
    for rec in ordered:
        v, u, s = {}, {}, {}
        for row in flatten_results(rec["results"]):
            k = (row["test"], row["object"], row["metric"])
            if k not in seen:
                seen.add(k)
                keys.append(k)
            v[k] = row["value"]
            u[k] = row["unit"]
            s[k] = row["status"]
        maps.append({"v": v, "u": u, "s": s})
    return ordered, keys, maps


def _site_colors(ordered):
    """One colour per site so multi-site selections are readable at a glance."""
    plt = _mpl()
    sites = sorted({(r.get("site") or "(none)") for r in ordered})
    cmap = plt.get_cmap("tab10" if len(sites) <= 10 else "tab20")
    return {s: cmap(i % cmap.N) for i, s in enumerate(sites)}, sites


# ---------------------------------------------------------------------- plots

def _fig_width(n, per=0.62, base=4.2, lo=7.0, hi=18.0):
    return float(np.clip(base + per * n, lo, hi))


def _status_grid(ordered, labels):
    plt = _mpl()
    tests = [("geometry", "dimension_status", "Geometry"),
             ("geometry", "field_status", "Field alignment"),
             ("linepairs", "status", "Line patterns"),
             ("lowcontrast", "status", "Low contrast"),
             ("uniformity", "status", "Uniformity"),
             ("wedge", "status", "Wedge")]
    n = len(ordered)
    nrows = len(tests) + 1                     # + the validation row
    fig, ax = plt.subplots(figsize=(_fig_width(n), 0.42 * nrows + 2.2))
    for row, (test, key, label) in enumerate(tests):
        for col, rec in enumerate(ordered):
            blob = (rec.get("results") or {}).get(test) or {}
            # a geometry test that never ran has only its own overall status
            st = blob.get(key) or blob.get("status") or "n/a"
            ax.add_patch(plt.Rectangle((col - 0.46, row - 0.42), 0.92, 0.84,
                                       color=_STATUS_COLOR.get(st, "#9aa4b2")))
    # the administrator's ruling, separated by a gap so it reads as a verdict
    vrow = len(tests) + 0.35
    for col, rec in enumerate(ordered):
        v = rec.get("validation_status") or ""
        ax.add_patch(plt.Rectangle((col - 0.46, vrow - 0.42), 0.92, 0.84,
                                   color=_VALIDATION_COLOR.get(v, "#9aa4b2")))
    ax.set_xlim(-0.6, n - 0.4)
    ax.set_ylim(-0.6, vrow + 0.5)
    ax.set_yticks(list(range(len(tests))) + [vrow])
    ax.set_yticklabels([t[2] for t in tests] + ["VALIDATION"], fontsize=8)
    for lbl in ax.get_yticklabels()[-1:]:
        lbl.set_fontweight("bold")
    ax.set_xticks(range(n))
    ax.set_xticklabels(labels, rotation=40, ha="right", fontsize=7)
    ax.invert_yaxis()
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    handles = [plt.Rectangle((0, 0), 1, 1, color=_STATUS_COLOR[k])
               for k in ("pass", "warn", "fail", "n/a")]
    ax.legend(handles,
              ["pass / validated", "warn, not measured / conditional",
               "fail / not validated", "not applicable, n/a / pending"],
              ncol=4, fontsize=7, loc="upper center",
              bbox_to_anchor=(0.5, -0.42), frameon=False)
    fig.tight_layout()
    return _b64(fig)


def _row_delta(vals, unit):
    """Deviation of each value from the row median, in a unit that stays finite.

    A metric that is ALREADY a percentage (pitch deviation, ΔSNR, …) has a
    median close to zero, so a relative deviation would explode — those are
    reported in percentage points instead."""
    med = np.nanmedian(vals)
    if not np.isfinite(med):
        return None, None
    if unit == "%":
        return vals - med, "pp"
    if abs(med) < 1e-12:
        return None, None
    return 100.0 * (vals - med) / abs(med), "%"


def _deviation_heatmap(ordered, labels, keys, maps, test):
    """Metric rows x analysis columns, coloured by deviation from the row median.

    Each row is scaled to its own largest deviation, so heterogeneous metrics
    (mm, counts, percentages) can share one picture; the printed number is the
    real deviation with its unit. Rows that are flat within noise are greyed."""
    plt = _mpl()
    rows = [k for k in keys
            if k[0] == test and k[1] not in ("all", "fit", "scale")]
    data, ann, rowlab = [], [], []
    flat = 0
    for k in rows:
        raw = [m["v"].get(k) for m in maps]
        vals = np.array([v if isinstance(v, (int, float)) else np.nan
                         for v in raw], float)
        if not np.isfinite(vals).any():
            continue
        unit = next((m["u"].get(k) for m in maps if m["u"].get(k)), "")
        delta, dunit = _row_delta(vals, unit)
        if delta is None:
            continue
        scale = float(np.nanmax(np.abs(delta)))
        # Each row is scaled to its own spread, so a metric that is identical
        # everywhere would otherwise be painted in full colour for a rounding
        # difference. Those rows are dropped and counted instead.
        floor = 0.5 if dunit == "pp" else 2.0
        if not np.isfinite(scale) or scale < floor:
            flat += 1
            continue
        data.append(delta / scale)
        ann.append((delta, dunit))
        rowlab.append(f"{k[1]} · {k[2]}")
    if not data:
        return None
    M = np.array(data)
    n = M.shape[1]
    fig, ax = plt.subplots(figsize=(_fig_width(n, per=0.62),
                                    0.32 * len(rowlab) + 2.2))
    im = ax.imshow(M, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(n))
    ax.set_xticklabels(labels, rotation=40, ha="right", fontsize=7)
    ax.set_yticks(range(len(rowlab)))
    ax.set_yticklabels(rowlab, fontsize=7)
    if M.size <= 260:
        for i in range(M.shape[0]):
            delta, dunit = ann[i]
            for j in range(M.shape[1]):
                if np.isfinite(delta[j]):
                    txt = (f"{delta[j]:+.2g}" if abs(delta[j]) < 100
                           else f"{delta[j]:+.0f}")
                    ax.text(j, i, txt, ha="center", va="center", fontsize=5.8,
                            color="white" if abs(M[i, j]) > 0.6 else "#222")
    cb = fig.colorbar(im, ax=ax, fraction=0.022, pad=0.012,
                      ticks=[-1, 0, 1])
    cb.ax.set_yticklabels(["lowest", "median", "highest"], fontsize=7)
    subtitle = ("colour = position within each metric's own spread; "
                "number = actual deviation, % or percentage points")
    if flat:
        subtitle += f"\n{flat} metric(s) omitted — effectively identical here"
    ax.set_title("Deviation from the median of the selection\n" + subtitle,
                 fontsize=9)
    fig.tight_layout()
    return _b64(fig)


def _panel(ordered, labels, maps, test, metric, title, unit):
    """One subplot per object; each analysis is a labelled point.

    A dot plot rather than a line: with ten different phantoms there is no
    meaningful line to draw between them, and points stay readable."""
    plt = _mpl()
    objects = sorted({k[1] for m in maps for k in m["v"]
                      if k[0] == test and k[2] == metric
                      and k[1] not in ("all", "fit", "scale")})
    if not objects:
        return None
    colors, sites = _site_colors(ordered)
    n = len(ordered)
    ncol = min(len(objects), 4)
    nrow = int(np.ceil(len(objects) / ncol))
    fig, axes = plt.subplots(nrow, ncol, squeeze=False,
                             figsize=(max(3.4 * ncol, 8), 2.7 * nrow))
    x = np.arange(n)
    any_data = False
    for idx, obj in enumerate(objects):
        ax = axes[idx // ncol][idx % ncol]
        ys = np.array([m["v"].get((test, obj, metric), np.nan)
                       if isinstance(m["v"].get((test, obj, metric)),
                                     (int, float)) else np.nan
                       for m in maps], float)
        if np.all(~np.isfinite(ys)):
            ax.axis("off")
            continue
        any_data = True
        med = np.nanmedian(ys)
        ax.axhline(med, color="#888", lw=0.9, ls="--", zorder=1)
        for i, rec in enumerate(ordered):
            ax.scatter(x[i], ys[i], s=46, zorder=3,
                       color=colors[rec.get("site") or "(none)"],
                       edgecolor="#333", linewidth=0.4)
        ax.set_title(f"{obj}", fontsize=9)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=40, ha="right", fontsize=6)
        ax.tick_params(axis="y", labelsize=7)
        ax.grid(alpha=0.25, axis="y")
        if idx % ncol == 0:
            ax.set_ylabel(unit or "value", fontsize=8)
    for idx in range(len(objects), nrow * ncol):
        axes[idx // ncol][idx % ncol].axis("off")
    if not any_data:
        plt.close(fig)
        return None
    if len(sites) > 1:
        handles = [plt.Line2D([], [], marker="o", ls="", color=colors[s],
                              markeredgecolor="#333", label=s) for s in sites]
        fig.legend(handles=handles, loc="upper right", fontsize=7, ncol=len(sites),
                   frameon=False, bbox_to_anchor=(1.0, 1.02))
    fig.suptitle(title + ("  ·  dashed line = median of the selection"),
                 fontsize=10, y=1.0)
    fig.tight_layout()
    return _b64(fig)


def _wedge_curves(ordered, labels, maps):
    """Wedge response of every analysis on one axis — directly comparable."""
    plt = _mpl()
    steps = sorted({int(k[1][1:]) for m in maps for k in m["v"]
                    if k[0] == "wedge" and k[2] == "mean"
                    and k[1].startswith("S") and k[1][1:].isdigit()})
    if not steps:
        return None
    colors, sites = _site_colors(ordered)
    fig, ax = plt.subplots(figsize=(9, 4.0))
    cmap = plt.get_cmap("viridis")
    for i, (rec, m) in enumerate(zip(ordered, maps)):
        ys = [m["v"].get(("wedge", f"S{s}", "mean")) for s in steps]
        if all(y is None for y in ys):
            continue
        ax.plot(steps, [np.nan if y is None else y for y in ys], "o-", ms=4,
                lw=1.3, color=cmap(i / max(len(ordered) - 1, 1)),
                label=labels[i].replace("\n", " "))
    ax.set_xlabel("wedge step (S1 = top)")
    ax.set_ylabel("mean pixel value")
    ax.set_title("Wedge response curve per analysis", fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=6.5, ncol=2 if len(ordered) > 6 else 1,
              loc="best", frameon=False)
    fig.tight_layout()
    return _b64(fig)


def _lowcontrast_curves(ordered, labels, maps):
    plt = _mpl()
    levels = sorted({k[1] for m in maps for k in m["v"]
                     if k[0] == "lowcontrast" and k[2] == "cnr"})
    if not levels:
        return None
    fig, ax = plt.subplots(figsize=(9, 4.0))
    cmap = plt.get_cmap("viridis")
    for i, m in enumerate(maps):
        ys = [m["v"].get(("lowcontrast", l, "cnr")) for l in levels]
        ys = [abs(y) if isinstance(y, (int, float)) else np.nan for y in ys]
        if all(not np.isfinite(y) for y in ys):
            continue
        ax.plot(range(len(levels)), ys, "o-", ms=4, lw=1.3,
                color=cmap(i / max(len(ordered) - 1, 1)),
                label=labels[i].replace("\n", " "))
    ax.set_xticks(range(len(levels)))
    ax.set_xticklabels(levels)
    ax.set_xlabel("circle (design order, L1 = weakest)")
    ax.set_ylabel("|CNR|")
    ax.set_title("Low-contrast detectability per analysis", fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=6.5, ncol=2 if len(ordered) > 6 else 1, loc="best",
              frameon=False)
    fig.tight_layout()
    return _b64(fig)


def _variability_chart(keys, maps, top=14):
    """Which metrics differ most across the selection.

    Split by unit: magnitudes are ranked by relative range (% of median),
    metrics that are already percentages by their absolute range in percentage
    points. Mixing the two on one axis would put a ΔSNR of a few percent next
    to a 13 000 % artefact of dividing by a near-zero median."""
    plt = _mpl()
    rel, absolute = [], []
    for k in keys:
        if k[1] in ("all", "fit", "scale"):
            continue
        vals = np.array([m["v"].get(k, np.nan) if isinstance(
            m["v"].get(k), (int, float)) else np.nan for m in maps], float)
        good = vals[np.isfinite(vals)]
        if good.size < 2:
            continue
        unit = next((m["u"].get(k) for m in maps if m["u"].get(k)), "")
        rng = float(good.max() - good.min())
        name = f"{k[0]} · {k[1]} · {k[2]}"
        if unit == "%":
            absolute.append((name, rng))
        else:
            med = float(np.median(good))
            if abs(med) < 1e-12:
                continue
            rel.append((name, 100.0 * rng / abs(med)))
    if not rel and not absolute:
        return None, []
    rel.sort(key=lambda t: -t[1])
    absolute.sort(key=lambda t: -t[1])
    rel, absolute = rel[:top], absolute[:top]

    panels = [(rel, "relative range (% of that metric's median)", (10.0, 20.0)),
              (absolute, "range in percentage points", (2.0, 5.0))]
    panels = [p for p in panels if p[0]]
    heights = [0.30 * len(p[0]) + 1.1 for p in panels]
    fig, axes = plt.subplots(len(panels), 1, squeeze=False,
                             figsize=(9.5, sum(heights)),
                             gridspec_kw={"height_ratios": heights})
    for ax, (rows, xlabel, (amber, red)) in zip(axes[:, 0], panels):
        y = np.arange(len(rows))
        ax.barh(y, [r[1] for r in rows],
                color=["#cf3f3f" if r[1] > red else "#d9a021" if r[1] > amber
                       else "#4a7dbd" for r in rows])
        for yi, r in zip(y, rows):
            ax.text(r[1], yi, f"  {r[1]:.1f}", va="center", fontsize=6.5,
                    color="#444")
        ax.set_yticks(y)
        ax.set_yticklabels([r[0] for r in rows], fontsize=7)
        ax.invert_yaxis()
        ax.set_xlabel(xlabel, fontsize=8)
        ax.grid(alpha=0.3, axis="x")
        ax.margins(x=0.12)
    axes[0, 0].set_title("Metrics that differ most across this selection",
                         fontsize=10)
    fig.tight_layout()
    return _b64(fig), rel + absolute


# ------------------------------------------------------------------- pictures
#
# The table of pictures answers the field's request to compare the images
# themselves, not only the numbers. The pictures are made by thumbnails.py and
# handed in; this only lays them out. Everything is embedded, so the page saved
# from the browser keeps every picture — nothing is e-mailed from the app, and
# a saved copy is how a comparison travels.

_PICTURE_TITLES = {
    "phantom": "Whole phantom",
    "linepairs": "Line-pair strip",
    "wedge": "Wedge",
    "lowcontrast": "Low-contrast block",
    "uniformity": "Uniformity squares",
}
_PICTURE_HINTS = {
    "linepairs": "along the strip as printed; enlarge to see the lines",
    "wedge": "laid on its side — S1, the top of the phantom, at the left",
    "uniformity": "the five squares side by side",
}
_OWN_WINDOW = "Each picture in its own window."
_ALONE_ON_PROTOCOL = ("own window — the only scan here from its detector and "
                      "protocol, so its brightness does not compare")
_SELF_NORMALISED = (
    "Flattened and windowed to itself, as in the marking step. Brightness "
    "does not compare between scans here — compare which discs you can see.")

_PICTURE_MIMES = ("image/jpeg", "image/png")
_B64_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                          "0123456789+/=")


def _clean_b64(value) -> str:
    """The picture data, if it is plainly base64 and nothing else.

    It comes from a file on the server's own disk, but it is pasted into an
    attribute; a damaged file must not be able to close that attribute."""
    s = str(value or "")
    return s if s and set(s) <= _B64_ALPHABET else ""


def _num(value, spec: str) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not np.isfinite(value):
        return None
    return format(value, spec)


def _picture_facts(region: str, results: dict) -> tuple[str, list[str]]:
    """The status and the few numbers worth reading under one picture.

    Taken from the stored results, never re-measured from the picture: the
    picture is for looking, the numbers are the record."""
    res = results or {}
    lines: list[str] = []
    if region == "phantom":
        g = res.get("geometry") or {}
        d = g.get("dimensions") or {}
        side = _num(d.get("mean_side_mm"), ".1f")
        dev = _num(d.get("dev_from_nominal_pct"), "+.2f")
        if side:
            lines.append(f"side {side} mm"
                         + (f" ({dev} % from nominal)" if dev else ""))
        if g.get("field_status"):
            lines.append(f"field alignment: {g['field_status']}")
        # A geometry test that never produced dimensions — errored, timed out,
        # or had no measuring areas — carries only its own status. Falling
        # back to it shows "error" or "not measured", as the status grid on
        # the same page does, instead of a neutral "no result".
        return g.get("dimension_status") or g.get("status"), lines
    if region == "linepairs":
        lp = res.get("linepairs") or {}
        rows = [r for r in lp.get("rows") or [] if isinstance(r, dict)]
        fitted = [(r.get("id"), (r.get("linearity") or {}).get("pitch_dev_pct"))
                  for r in rows]
        fitted = [(gid, v) for gid, v in fitted if _num(v, "f")]
        if rows:
            lines.append(f"pitch measured on {len(fitted)} of {len(rows)} "
                         f"groups")
        if fitted:
            gid, dev = max(fitted, key=lambda t: abs(t[1]))
            tol = _num(lp.get("pitch_tolerance_pct"), "g")
            lines.append(f"largest pitch deviation {dev:+.1f} % ({gid})"
                         + (f", limit ±{tol} %" if tol else ""))
        return lp.get("status"), lines
    if region == "wedge":
        w = res.get("wedge") or {}
        ratio = _num(w.get("dynamic_range_ratio"), ".1f")
        if ratio:
            lines.append(f"dynamic range {ratio}×")
        if "monotonic" in w:
            lines.append("steps in order" if w.get("monotonic")
                         else "steps out of order")
        sat = [f"S{r.get('step')}" for r in w.get("rows") or []
               if isinstance(r, dict) and r.get("saturated")]
        if sat:
            lines.append("saturated: " + ", ".join(sat))
        return w.get("status"), lines
    if region == "lowcontrast":
        lc = res.get("lowcontrast") or {}
        rows = [r for r in lc.get("rows") or [] if isinstance(r, dict)]
        cnrs = [abs(r["cnr"]) for r in rows if _num(r.get("cnr"), "f")]
        rho = _num(lc.get("order_rho"), ".2f")
        if rho:
            lines.append(f"disc order {rho} (rank correlation; 1 = as "
                         f"designed)")
        if cnrs:
            lines.append(f"|CNR| {min(cnrs):.2f} – {max(cnrs):.2f}")
        total = len(rows) + len(lc.get("not_measured") or [])
        if total:
            lines.append(f"{len(cnrs)} of {total} discs measured")
        if (lc.get("orientation") or {}).get("flipped"):
            lines.append("insert fitted a half turn round")
        return lc.get("status"), lines
    if region == "uniformity":
        u = res.get("uniformity") or {}
        worst = _num(u.get("max_abs_dsnr_pct"), ".1f")
        tol = _num(u.get("tolerance_pct"), "g")
        if worst:
            lines.append(f"worst SNR deviation {worst} %"
                         + (f" (limit ±{tol} %)" if tol else ""))
        n_ok, n_not = len(u.get("rows") or []), len(u.get("not_measured") or [])
        if n_not:
            lines.append(f"{n_ok} of {n_ok + n_not} squares measured")
        return u.get("status"), lines
    return "", lines


def _status_chip(status) -> str:
    st = str(status or "")
    word = st.replace("_", " ") if st else "no result"
    return (f"<span class='chip' style='background:"
            f"{_STATUS_COLOR.get(st, '#9aa4b2')}'>{html.escape(word)}</span>")


def _picture_cell(pic, caption: str, rec: dict, region: str,
                  shared_with: dict | None = None, own_note: str = "") -> str:
    """One scan's picture of one region, with its numbers underneath.

    ``shared_with`` is the picture whose window this one is shown on in the
    page; without it the picture keeps its own window, and ``own_note`` says
    whose window it is when that is not the row's. A missing picture is a
    labelled empty cell saying why — never a gap, which would shift the rest
    of the row and let the reader compare the wrong columns."""
    pic = pic if isinstance(pic, dict) else {}
    b64 = _clean_b64(pic.get("b64"))
    mime = pic.get("mime") if pic.get("mime") in _PICTURE_MIMES else ""
    if b64 and mime:
        window = ""
        if shared_with:
            window = (f' data-lo="{float(pic["lo"]):.7g}"'
                      f' data-hi="{float(pic["hi"]):.7g}"'
                      f' data-rlo="{float(shared_with["lo"]):.7g}"'
                      f' data-rhi="{float(shared_with["hi"]):.7g}"')
        w, h = int(pic.get("w") or 0), int(pic.get("h") or 0)
        size = f' width="{w}" height="{h}"' if w > 0 and h > 0 else ""
        body = (f'<figure class="pic" data-caption="{html.escape(caption)}">'
                f'<img src="data:{mime};base64,{b64}"{size}{window} '
                f'alt="{html.escape(caption)}"></figure>')
        labels = [str(x) for x in pic.get("labels") or []]
        if labels:
            body += ("<div class='pic-labels'>"
                     + "".join(f"<span>{html.escape(x)}</span>" for x in labels)
                     + "</div>")
        if own_note:
            body += f"<div class='pic-own'>{html.escape(own_note)}</div>"
    else:
        reason = str(pic.get("missing") or "no picture")
        body = f"<div class='pic-missing'>{html.escape(reason)}</div>"
    try:
        status, lines = _picture_facts(region, rec.get("results") or {})
    except Exception:           # odd stored results must not cost the page
        status, lines = "", []
    facts = (f"<div class='pic-facts'>{_status_chip(status)}"
             + "".join(f"<br>{html.escape(x)}" for x in lines) + "</div>")
    return f"<td class='pic-cell'>{body}{facts}</td>"


def _window_reference(ordered) -> int:
    """Which scan's window the rows share: the reference scan when the
    selection holds one, otherwise the first scan."""
    return next((i for i, r in enumerate(ordered) if r.get("is_baseline")), 0)


def _window_sources(ordered, usable: list[int], ref: int) -> dict[int, int]:
    """For each picture in a row, whose window it is shown on.

    Pixel values only compare within one detector and protocol — the same
    rule that scopes a baseline. On the window of a scan from another
    detector a picture comes out black or white, which reads as a fault that
    is not there. So the scans are grouped by protocol: the reference scan's
    group shares its window, and any other group shares the window of its own
    reference scan, or else its first. A scan alone on its protocol has
    nothing to share with and keeps its own window (it is left out).

    In the usual comparison — one phantom on one detector over time — there
    is one group, and this is simply "the reference scan's window"."""
    groups: dict[str, list[int]] = {}
    for i in usable:
        groups.setdefault(ordered[i].get("signature") or "", []).append(i)
    source = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        own_ref = next((i for i in members if ordered[i].get("is_baseline")),
                       members[0])
        src = ref if ref in members else own_ref
        for i in members:
            source[i] = src
    return source


def _picture_section(ordered, labels, pictures: dict) -> str:
    """The table: one column per scan, one row per test area.

    Only scans that were given pictures get a column. The caller leaves some
    out on purpose when many are selected (see the note this adds), and a
    column of empty cells for each of those would be both heavy and useless;
    a scan whose pictures failed still has an entry, and so still gets its
    labelled empty cells."""
    shown = [(r, lab) for r, lab in zip(ordered, labels) if r["id"] in pictures]
    left_out = len(ordered) - len(shown)
    ordered = [r for r, _ in shown]
    labels = [lab for _, lab in shown]
    ref = _window_reference(ordered)
    flat_labels = [lab.replace("\n", " ") for lab in labels]

    body, window_sources = "", set()
    for region in _PICTURE_REGIONS:
        row_pics = [(pictures.get(r["id"]) or {}).get(region) for r in ordered]
        title = _PICTURE_TITLES.get(region, region)
        hint = _PICTURE_HINTS.get(region, "")
        attrs, note = "", f"<div class='win-note'>{_OWN_WINDOW}</div>"
        on_shared, own_notes = {}, {}
        if region == "lowcontrast":
            note = f"<div class='win-note'>{html.escape(_SELF_NORMALISED)}</div>"
        else:
            # Whose window each picture is shown on; the note names the one
            # most pictures share, which is the reference scan's whenever it
            # has a picture in this row.
            usable = [i for i, p in enumerate(row_pics)
                      if isinstance(p, dict) and p.get("window") == "raw"
                      and _clean_b64(p.get("b64"))
                      and _num(p.get("lo"), "g") and _num(p.get("hi"), "g")]
            source = _window_sources(ordered, usable, ref)
            window_sources.update(source.values())
            if source:
                primary = source.get(ref, min(source.values()))
                if len(set(source.values())) == 1:
                    shared = (f"One window for every scan, taken from "
                              f"{flat_labels[primary]}, so a brighter picture "
                              f"really is brighter.")
                else:
                    shared = (f"One window for each detector and protocol, "
                              f"taken from its reference or first scan "
                              f"({flat_labels[primary]} for the first), so a "
                              f"brighter picture really is brighter.")
                for i in usable:
                    if i not in source:
                        own_notes[i] = _ALONE_ON_PROTOCOL
                    elif source[i] != primary:
                        own_notes[i] = (f"window from {flat_labels[source[i]]}"
                                        f" — another detector or protocol")
                on_shared = {i: row_pics[s] for i, s in source.items()}
                attrs = (f' data-ref-lo="{float(row_pics[primary]["lo"]):.7g}"'
                         f' data-ref-hi="{float(row_pics[primary]["hi"]):.7g}"')
                # Without JavaScript every picture shows in its own window,
                # and the note says so; the script switches both.
                note = (f"<div class='win-note' data-own='{_OWN_WINDOW}' "
                        f"data-shared='{html.escape(shared, quote=True)}'>"
                        f"{_OWN_WINDOW}</div>"
                        f"<label class='win-toggle' hidden><input "
                        f"type='checkbox'> window each picture on its own"
                        f"</label>")
        cells = "".join(
            _picture_cell(p, f"{flat_labels[i]} — {title}", ordered[i], region,
                          shared_with=on_shared.get(i),
                          own_note=own_notes.get(i, ""))
            for i, p in enumerate(row_pics))
        body += (f"<tr{attrs}><th class='row-head'>{html.escape(title)}"
                 + (f"<div class='muted'>{html.escape(hint)}</div>"
                    if hint else "")
                 + f"{note}</th>{cells}</tr>")

    head = ""
    for i, rec in enumerate(ordered):
        acquired = (rec.get("acquired_at") or "")[:16]
        when = (f"acquired {acquired}" if acquired else
                f"acquisition date not recorded · uploaded "
                f"{(rec.get('created_at') or '')[:16]}")
        tags = []
        if rec.get("is_baseline"):
            tags.append("reference scan")
        if i in window_sources:
            tags.append("shared window taken from here")
        head += (f"<th class='pic-head'>"
                 f"<b>{html.escape(rec.get('site') or '—')} / "
                 f"{html.escape(rec.get('phantom') or '—')}</b>"
                 f"<br><span class='muted'>{html.escape(when)}</span>"
                 f"<br>{_status_chip(rec.get('status'))}"
                 + "".join(f" <span class='tag'>{html.escape(t)}</span>"
                           for t in tags)
                 + "</th>")

    return f"""
<section class="card pics-card">
  <h2>The scans side by side</h2>
  <p class="muted">Every picture is straightened to the phantom's own frame, so
  scans taken at any angle, or face down, line up column by column. The numbers
  under each picture are the stored results. Click a picture to enlarge it.
  The pictures are part of this page, so saving the page keeps them.</p>
  {(f'<p class="muted"><b>Pictures are shown for {len(ordered)} of '
    f'{len(ordered) + left_out} scans</b> — the reference scan and the most '
    f'recent ones — so the page stays light on a slow connection. The charts '
    f'below still cover all of them. Select fewer scans to see the others '
    f'side by side.</p>') if left_out else ''}
  <div class="scroll"><table class="pics">
    <thead><tr><th class="row-head"></th>{head}</tr></thead>
    <tbody>{body}</tbody>
  </table></div>
</section>"""


#: Row windows and picture enlargement. Inline, so a copy saved from the
#: browser keeps working with no server behind it; the server admits exactly
#: this text by its hash (COMPARISON_SCRIPT_CSP) rather than allowing inline
#: scripts in general. Without it the page still reads: every picture then
#: shows in its own window, and the row notes say so.
_PICTURE_SCRIPT = """
(function () {
  "use strict";
  function each(list, fn) { Array.prototype.forEach.call(list, fn); }
  function ready(img, fn) {
    if (img.complete && img.naturalWidth) { fn(); }
    else { img.addEventListener("load", fn); }
  }
  // Every picture was encoded with 0..255 standing for its own data-lo..
  // data-hi, so one 256-entry table moves it onto the shared window
  // data-rlo..data-rhi without fetching anything.
  function remap(img, rlo, rhi) {
    var box = img.parentNode, old = box.querySelector("canvas");
    if (old) { box.removeChild(old); }
    var lo = parseFloat(img.getAttribute("data-lo"));
    var hi = parseFloat(img.getAttribute("data-hi"));
    var c = document.createElement("canvas");
    c.width = img.naturalWidth;
    c.height = img.naturalHeight;
    var g = c.getContext("2d");
    g.drawImage(img, 0, 0);
    var d = g.getImageData(0, 0, c.width, c.height), p = d.data;
    var lut = new Uint8ClampedArray(256), k = (hi - lo) / 255, span = rhi - rlo;
    for (var v = 0; v < 256; v++) { lut[v] = (lo + v * k - rlo) / span * 255; }
    for (var i = 0; i < p.length; i += 4) {
      var q = lut[p[i]];
      p[i] = q; p[i + 1] = q; p[i + 2] = q;
    }
    g.putImageData(d, 0, 0);
    box.appendChild(c);
  }
  each(document.querySelectorAll("tr[data-ref-lo]"), function (row) {
    var note = row.querySelector(".win-note");
    var toggle = row.querySelector(".win-toggle");
    var own = toggle ? toggle.querySelector("input") : null;
    function show() {
      var mine = !own || own.checked;
      row.classList.toggle("own-window", mine);
      if (note) {
        note.textContent = note.getAttribute(mine ? "data-own" : "data-shared");
      }
    }
    function failed() {
      own = null;
      if (toggle) { toggle.hidden = true; }
      show();
    }
    each(row.querySelectorAll("img[data-rlo]"), function (img) {
      var rlo = parseFloat(img.getAttribute("data-rlo"));
      var rhi = parseFloat(img.getAttribute("data-rhi"));
      if (!(rhi > rlo)) { return; }
      ready(img, function () {
        try { remap(img, rlo, rhi); } catch (e) { failed(); }
      });
    });
    if (toggle && own) {
      toggle.hidden = false;
      own.addEventListener("change", show);
    }
    show();
  });

  var box = document.getElementById("pic-lightbox");
  if (!box) { return; }
  var big = box.querySelector("img"), cap = box.querySelector("figcaption");
  function close() { box.hidden = true; big.removeAttribute("src"); }
  function open(fig) {
    var img = fig.querySelector("img"), c = fig.querySelector("canvas");
    var row = fig.closest ? fig.closest("tr") : null;
    var shared = c && row && !row.classList.contains("own-window");
    big.src = shared ? c.toDataURL("image/png") : img.src;
    var w = img.naturalWidth || 300, h = img.naturalHeight || 300;
    var k = Math.max(1, Math.min(4, window.innerWidth * 0.94 / w,
                                 window.innerHeight * 0.8 / h));
    big.style.width = Math.round(w * k) + "px";
    cap.textContent = fig.getAttribute("data-caption") || "";
    box.hidden = false;
  }
  document.addEventListener("click", function (e) {
    if (!box.hidden) { close(); return; }
    var fig = e.target.closest ? e.target.closest(".pic") : null;
    if (fig && fig.querySelector("img")) { open(fig); }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !box.hidden) { close(); }
  });
})();
"""

#: The Content-Security-Policy source that admits _PICTURE_SCRIPT and nothing
#: else. Computed from the text itself, so editing the script cannot leave a
#: stale hash behind that silently switches the script off.
COMPARISON_SCRIPT_CSP = "'sha256-{}'".format(base64.b64encode(
    hashlib.sha256(_PICTURE_SCRIPT.encode("utf-8")).digest()).decode("ascii"))

_PICTURE_CSS = """
 table.pics { width:auto; }
 table.pics td, table.pics th { white-space:normal; vertical-align:top; }
 table.pics th.row-head { width:150px; min-width:130px; background:#f7f9fb; }
 table.pics th.pic-head { width:250px; min-width:170px; font-weight:400; }
 table.pics td.pic-cell { width:250px; min-width:170px; }
 .pic { position:relative; display:block; margin:0; cursor:zoom-in;
        line-height:0; }
 .pic img { display:block; margin:0; width:100%; max-width:100%; height:auto;
            background:#000; }
 .pic canvas { position:absolute; top:0; left:0; width:100%; height:100%; }
 tr.own-window .pic canvas { visibility:hidden; }
 .pic-labels { display:flex; justify-content:space-around; font-size:10px;
               color:#6b7688; margin-top:2px; }
 .pic-own { font-size:10px; color:#8a5a00; margin-top:3px; }
 tr.own-window .pic-own { display:none; }
 .pic-missing { display:flex; align-items:center; justify-content:center;
                min-height:70px; padding:8px; text-align:center; font-size:11px;
                color:#6b7688; background:repeating-linear-gradient(45deg,
                #f4f6f8, #f4f6f8 6px, #eceff3 6px, #eceff3 12px);
                border:1px dashed #c9d1db; border-radius:6px; }
 .pic-facts { font-size:11px; line-height:1.45; margin-top:5px; }
 .win-note { font-size:10.5px; font-weight:400; color:#41506a; margin-top:6px; }
 .win-toggle { display:block; font-size:10.5px; font-weight:400;
               margin-top:6px; cursor:pointer; }
 .win-toggle[hidden] { display:none; }
 .tag { display:inline-block; font-size:10px; color:#41506a;
        background:#e3eaf3; border-radius:8px; padding:0 7px; margin-top:3px; }
 .lightbox { position:fixed; inset:0; background:rgba(10,14,20,.88);
             display:flex; align-items:center; justify-content:center;
             z-index:10; cursor:zoom-out; }
 .lightbox[hidden] { display:none; }
 .lightbox figure { margin:0; text-align:center; }
 .lightbox img { max-width:96vw; max-height:84vh; width:auto; height:auto;
                 margin:0 auto; background:#000; }
 .lightbox figcaption { color:#e8edf3; font-size:13px; margin-top:8px; }
 @page pictures { size:A4 landscape; margin:10mm; }
 @media print {
   .pics-card { page:pictures; }
   .pics-card .scroll { overflow:visible; }
   table.pics { width:100%; table-layout:fixed; }
   table.pics th.pic-head, table.pics td.pic-cell { width:auto; min-width:0; }
   table.pics tr { break-inside:avoid; }
   .win-toggle, .lightbox { display:none !important; }
 }
"""


# --------------------------------------------------------------------- report

def build_comparison_report(records: list[dict], title_suffix: str = "",
                            filters: dict | None = None,
                            pictures: dict | None = None) -> str:
    """The whole comparison as one self-contained page.

    ``pictures`` maps an analysis id to its pictures from
    ``thumbnails.pictures_for``. Given, the page leads with a table of them;
    an analysis missing from it gets labelled empty cells. Left out, the page
    is charts only, as it was before the pictures existed."""
    ordered, keys, maps = _collect(records)
    if not ordered:
        return ("<!doctype html><html><body style='font-family:sans-serif;"
                "padding:40px'><h1>No completed analyses</h1><p>The selection "
                "contains no analyses with computed results.</p>"
                "</body></html>")

    labels = _entity_labels(ordered)
    n = len(ordered)
    sites = sorted({(r.get("site") or "(unlabelled)") for r in ordered})
    phantoms = sorted({(r.get("phantom") or "(unlabelled)") for r in ordered})

    # ---- key: label -> full identification -------------------------------
    key_rows = "".join(
        f"<tr><td><b>{html.escape(labels[i].replace(chr(10), ' '))}</b></td>"
        f"<td>{html.escape(r.get('site') or '—')}</td>"
        f"<td>{html.escape(r.get('phantom') or '—')}</td>"
        f"<td>{html.escape((r.get('acquired_at') or '')[:16] or '—')}</td>"
        f"<td>{html.escape((r.get('created_at') or '')[:16])}</td>"
        f"<td><span class='chip' style='background:"
        f"{_STATUS_COLOR.get(r.get('status'), '#9aa4b2')}'>"
        f"{html.escape(str(r.get('status') or 'n/a'))}</span></td>"
        f"<td><span class='chip' style='background:"
        f"{_VALIDATION_COLOR.get(r.get('validation_status') or '', '#9aa4b2')}'>"
        f"{html.escape(_VALIDATION_SHORT.get(r.get('validation_status') or '', ''))}"
        f"</span>"
        f"{('<br><span class=muted>' + html.escape(r.get('validated_by') or '') + '</span>') if r.get('validated_by') else ''}"
        f"</td>"
        f"<td class='muted'>{html.escape(r.get('signature') or '')}</td>"
        f"<td class='muted'>{html.escape(r['id'][:8])}</td></tr>"
        for i, r in enumerate(ordered))

    grid = _status_grid(ordered, labels)
    var_chart, var_rows = _variability_chart(keys, maps)

    # ---- per-pattern sections -------------------------------------------
    sections = ""
    for test in _SECTION_ORDER:
        tkeys = [k for k in keys if k[0] == test]
        if not tkeys:
            continue
        blocks = ""
        if test == "wedge":
            blocks += _img(_wedge_curves(ordered, labels, maps))
        if test == "lowcontrast":
            blocks += _img(_lowcontrast_curves(ordered, labels, maps))
        for metric, ptitle, unit in _PANELS.get(test, []):
            blocks += _img(_panel(ordered, labels, maps, test, metric,
                                  ptitle, unit))
        hm = _deviation_heatmap(ordered, labels, keys, maps, test)
        blocks += _img(hm)
        if not blocks:
            continue

        # numeric detail, collapsed
        head = "".join(f"<th>{html.escape(l.replace(chr(10), ' '))}</th>"
                       for l in labels)
        body = ""
        for k in tkeys:
            unit = next((m["u"].get(k) for m in maps if m["u"].get(k)), "")
            cells = ""
            for m in maps:
                v = m["v"].get(k)
                cells += (f"<td class='num'>"
                          f"{('%.4g' % v) if isinstance(v, (int, float)) and np.isfinite(v) else '—'}"
                          f"</td>")
            body += (f"<tr><td>{html.escape(k[1])}</td><td>{html.escape(k[2])}</td>"
                     f"<td class='muted'>{html.escape(unit)}</td>{cells}</tr>")
        sections += f"""
<section class="card">
  <h2>{html.escape(_SECTION_TITLES[test])}</h2>
  {blocks}
  <details><summary>Numeric detail ({len(tkeys)} metrics)</summary>
    <div class="scroll"><table>
      <tr><th>object</th><th>metric</th><th>unit</th>{head}</tr>{body}
    </table></div>
  </details>
</section>"""

    filt = ""
    if filters:
        parts = [f"{k}: {v}" for k, v in filters.items() if v]
        filt = " · ".join(html.escape(p) for p in parts)

    def _stamp(r):
        return (r.get("acquired_at") or "")[:16] or (r.get("created_at") or "")[:16]

    span = f"{_stamp(ordered[0])} → {_stamp(ordered[-1])}"

    picture_card, picture_css, picture_tail = "", "", ""
    if pictures is not None:
        picture_card = _picture_section(ordered, labels, pictures)
        picture_css = _PICTURE_CSS
        picture_tail = ('<div id="pic-lightbox" class="lightbox" hidden>'
                        '<figure><img alt=""><figcaption></figcaption>'
                        '</figure></div>'
                        f"<script>{_PICTURE_SCRIPT}</script>")

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Phantom QA — comparison</title>
<style>
 body {{ font-family:"Segoe UI",system-ui,Arial,sans-serif; margin:0;
        background:#f4f6f8; color:#1d2530; }}
 .wrap {{ max-width:1180px; margin:0 auto; padding:24px; }}
 h1 {{ font-size:22px; margin:0 0 4px; }}
 h2 {{ font-size:16px; margin:0 0 12px; }}
 .card {{ background:#fff; border:1px solid #dde3ea; border-radius:10px;
          padding:16px 18px; margin:16px 0; }}
 table {{ border-collapse:collapse; width:100%; }}
 td, th {{ border:1px solid #dfe4ea; padding:4px 9px; font-size:11.5px;
           text-align:left; white-space:nowrap; }}
 th {{ background:#eef2f6; font-weight:600; }}
 td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
 .muted {{ color:#8994a4; font-weight:400; font-size:10.5px; }}
 .chip {{ color:#fff; padding:1px 9px; border-radius:9px; font-size:10.5px;
          font-weight:600; }}
 .scroll {{ overflow-x:auto; }}
 img {{ max-width:100%; height:auto; display:block; margin:10px auto; }}
 details {{ margin-top:10px; }}
 summary {{ cursor:pointer; font-size:12px; color:#41506a; }}
 .facts {{ display:flex; gap:10px; flex-wrap:wrap; margin:6px 0 12px; }}
 .fact {{ background:#eef2f6; border-radius:8px; padding:7px 12px;
          font-size:12px; }}
 .fact b {{ font-size:15px; display:block; }}
 @media print {{ body {{ background:#fff; }} .card {{ break-inside:avoid; }} }}
{picture_css}
</style></head><body><div class="wrap">
<h1>MSF Phantom QA — comparison{html.escape(title_suffix)}</h1>
<p class="muted" style="font-size:12px">{html.escape(span)}
{(' · ' + filt) if filt else ''}</p>

<section class="card">
  <h2>What is being compared</h2>
  <div class="facts">
    <div class="fact"><b>{n}</b>analyses</div>
    <div class="fact"><b>{len(sites)}</b>site(s)</div>
    <div class="fact"><b>{len(phantoms)}</b>phantom(s)</div>
  </div>
  <div class="scroll"><table>
    <tr><th>label used in the plots</th><th>site</th><th>phantom</th>
        <th>acquired</th><th>uploaded</th><th>overall</th><th>validation</th>
        <th>protocol</th><th>id</th></tr>
    {key_rows}
  </table></div>
  <p class="muted">Entries are grouped by site and phantom, and chronologically
  within a phantom. Every deviation on this page is measured against the
  <b>median of this selection</b>, not against the first entry — so the report
  reads the same whether you are following one phantom over time or comparing
  many phantoms against each other.</p>
  {_img(grid)}
</section>
{picture_card}
<section class="card">
  <h2>Where the differences are</h2>
  {_img(var_chart)}
  <p class="muted">Metrics that are themselves percentages (pitch deviation,
  ΔSNR, …) are ranked by their range in percentage points; everything else by
  its range relative to its own median. Blue is small, amber is worth a look,
  red stands out.</p>
</section>

{sections}
</div>{picture_tail}</body></html>"""
