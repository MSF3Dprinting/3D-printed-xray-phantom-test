"""Pipeline orchestration: wizard stages as pure functions over ScanData.

Stage A: register()                -> registration + transform
Stage B/C: propose_all()           -> per-test geometry proposals (editable)
Stage D/E: compute_all()           -> results from (possibly adjusted) geometry
Overlays: report_overview()        -> lossless picture + SVG outlines (report)
          render_overlay()         -> annotated PNG (command line)
"""

from __future__ import annotations

import io
import time

import numpy as np

from . import ALGO_VERSION
from .analysis import geometry as geo_mod
from .analysis import linepairs, lowcontrast, uniformity, wedge
from .analysis.common import Ctx
from .ingest import ScanData, protocol_signature
from .phantom_def import PhantomDef
from .registration import Registration, register

TESTS = ("geometry", "linepairs", "lowcontrast", "uniformity", "wedge")
_MODULES = {"geometry": geo_mod, "linepairs": linepairs,
            "lowcontrast": lowcontrast, "uniformity": uniformity,
            "wedge": wedge}


class Deadline:
    """A time budget the analysis checks between tests.

    "If the analysis is not done within certain time, it should rather provide
    failed result than get stuck" — so an analysis that runs long stops and
    says so, instead of holding a request open until something upstream gives
    up and the operator is left guessing.

    The check is **cooperative**: it happens between the five tests, not inside
    them. A single numpy call cannot be interrupted part-way without a separate
    process, which would change how this is deployed. That granularity is
    enough in practice — across 38 scans no individual test took longer than
    about four seconds, so the budget is spent between checks, not inside one.

    A deadline of 0 or None never expires, which is what the command line and
    the tests use.
    """

    def __init__(self, seconds: float | None = None):
        self.seconds = float(seconds) if seconds else 0.0
        self.started = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def expired(self) -> bool:
        return bool(self.seconds) and self.elapsed() >= self.seconds

    def note(self, what: str) -> str:
        return (f"the analysis passed its time limit of {self.seconds:g} s "
                f"after {self.elapsed():.1f} s, so {what} was not measured. "
                f"The scan is stored and can be analysed again; if this keeps "
                f"happening the limit is set in PHANTOMQA_ANALYSIS_TIMEOUT_S.")


def to_jsonable(obj):
    """Recursively convert numpy types for JSON serialization."""
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return v if np.isfinite(v) else None
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def build_ctx(scan: ScanData, pdef: PhantomDef, reg: Registration,
              params: dict | None = None) -> Ctx:
    p = {"spacing_candidates": scan.spacing_candidates}
    if params:
        p.update(params)
    return Ctx(pixels=scan.pixels, T=reg.transform, pdef=pdef, reg=reg, params=p)


def run_stage_a(scan: ScanData, pdef: PhantomDef,
                corners_hint=None) -> Registration:
    return register(scan.pixels, pdef, corners_hint=corners_hint)


def registration_to_dict(reg: Registration) -> dict:
    return to_jsonable({
        "transform": reg.transform.to_dict(),
        "corners_px": reg.corners_px,
        "coarse_angle_deg": reg.coarse_angle_deg,
        "score": reg.score,
        "candidate_scores": reg.candidate_scores,
        "landmarks": reg.landmarks,
        "residual_rms_mm": reg.residual_rms_mm,
    })


def registration_from_dict(d: dict) -> Registration:
    """Rebuild a stored registration, so results can be recomputed from the
    geometry the user confirmed instead of detecting it again."""
    from .registration import Transform
    return Registration(
        transform=Transform.from_dict(d["transform"]),
        corners_px=np.asarray(d["corners_px"], float),
        coarse_angle_deg=d.get("coarse_angle_deg", float("nan")),
        score=d.get("score", {}),
        candidate_scores=d.get("candidate_scores", []),
        landmarks=d.get("landmarks", {}),
        residual_rms_mm=d.get("residual_rms_mm", float("nan")),
    )


def propose_all(ctx: Ctx, deadline: "Deadline | None" = None) -> dict:
    out = {}
    for name in TESTS:
        if deadline is not None and deadline.expired():
            out[name] = {"_error": deadline.note(f"the {name} geometry")}
            continue
        try:
            out[name] = _MODULES[name].propose(ctx)
            out[name]["_error"] = None
        except Exception as e:  # keep the wizard usable when one test fails
            out[name] = {"_error": f"{type(e).__name__}: {e}"}
    return to_jsonable(out)


def compute_all(ctx: Ctx, geometry_all: dict,
                deadline: "Deadline | None" = None) -> dict:
    out = {}
    for name in TESTS:
        if deadline is not None and deadline.expired():
            # "error" rather than "fail": the detector did not fail this test,
            # the software never got to it. Both rank equally in the overall
            # verdict, so the analysis still comes out as a failure — which is
            # the point — but nobody reads it as a measurement.
            out[name] = {"status": "error", "timed_out": True,
                         "error": deadline.note(f"the {name} test")}
            continue
        geom = geometry_all.get(name)
        if not geom or geom.get("_error"):
            # "not measured", never "not applicable": the test applies to
            # every exposure of this phantom, it simply had nowhere to measure.
            out[name] = {"status": NOT_MEASURED,
                         "error": (geom or {}).get(
                             "_error", "no measuring areas were placed for "
                                       "this test")}
            continue
        try:
            out[name] = _MODULES[name].compute(ctx, geom)
        except Exception as e:
            out[name] = {"status": "error", "error": f"{type(e).__name__}: {e}"}
    # `params` is optional on Ctx and every other reader goes through
    # ctx.param(); dereferencing it directly turned a context built without one
    # into an AttributeError 500 rather than a result.
    params = ctx.params or {}
    out["_meta"] = {
        "algo_version": ALGO_VERSION,
        "signature": protocol_signature(params.get("scan_meta", {}))
        if params.get("scan_meta") else None,
        "registration": ctx.reg.summary() if ctx.reg else None,
    }
    return to_jsonable(out)


#: A test that does not apply to this image — at present only X-ray field
#: alignment when no field edge is in the picture, which is how every
#: exposure so far has been taken. Nothing is wrong and nothing was missed,
#: so it does not lower the overall verdict; the verdict says it separately.
NOT_APPLICABLE = "not applicable"

#: A test that applies and could not produce an answer: nothing measurable
#: in its areas, too few objects to judge, or no areas placed at all. It
#: counts as a warning, because the usual cause is an exposure that has to
#: be repeated, and an exposure nothing could be measured on must never read
#: as a pass.
NOT_MEASURED = "not measured"

#: What both of the above were called before they were told apart. Nothing
#: writes it any more; it is only read back from analyses stored before then.
LEGACY_NA = "n/a"

#: Rank of each per-test status in the overall verdict. "not applicable" is
#: absent on purpose: it is skipped, not ranked. The legacy "n/a" keeps the
#: rank it had when it meant both things at once, so re-reading an analysis
#: stored before the split reproduces the verdict it was stored with.
_RANK = {"pass": 0, LEGACY_NA: 1, "warn": 2, "fail": 3, "error": 3}


def overall_status(results: dict) -> str:
    """The worst per-test status, as one of pass / warn / fail / error / n/a.

    "not measured" is reported as "warn", so the overall vocabulary — what
    History, the exports and every stored analysis already use — does not
    grow. A status this function does not know counts as a warning too:
    ranking an unrecognised word as a pass is how an unmeasured test would
    slip through. "n/a" comes back only when nothing was judged at all."""
    worst, judged = "pass", False
    for name in TESTS:
        r = results.get(name) or {}
        for key in ("status", "field_status", "dimension_status"):
            s = r.get(key)
            if not s or s == NOT_APPLICABLE:
                continue
            judged = True
            if s == NOT_MEASURED or s not in _RANK:
                s = "warn"
            if _RANK[s] > _RANK[worst]:
                worst = s
    return worst if judged else LEGACY_NA


#: Every status an analysis carries, with the name a reader knows it by.
STATUS_FIELDS = (
    ("geometry", "status", "Geometry"),
    ("geometry", "dimension_status", "Phantom size"),
    ("geometry", "field_status", "X-ray field alignment"),
    ("linepairs", "status", "Line patterns"),
    ("lowcontrast", "status", "Low contrast"),
    ("uniformity", "status", "Uniformity"),
    ("wedge", "status", "Wedge"),
)

#: Why a test does not apply, wherever more can be said than that.
_NOT_APPLICABLE_WHY = {
    ("geometry", "field_status"): "no field edge found in the image",
}


def verdict_notes(results: dict | None) -> list[str]:
    """What the overall verdict leaves out, in words, for printing beside it.

    A "pass" that silently skipped a test reads as though that test passed
    too. Skipping it is right — the test does not apply — but the reader of a
    signed report has to be told, e.g. "X-ray field alignment not checked (no
    field edge found in the image)". Only "not applicable" produces a note: a
    test that was not measured already pulls the verdict down to a warning
    and is named wherever the warning is explained."""
    notes = []
    for name, key, title in STATUS_FIELDS:
        r = (results or {}).get(name)
        if isinstance(r, dict) and r.get(key) == NOT_APPLICABLE:
            why = _NOT_APPLICABLE_WHY.get((name, key),
                                          "it does not apply to this image")
            notes.append(f"{title} not checked ({why})")
    return notes


# ------------------------------------------------------------------ overlays

_COLORS = {"geometry": "#00c8ff", "linepairs": "#ffd400",
           "lowcontrast": "#ff7bda", "uniformity": "#7bff9f",
           "wedge": "#ff9d5c", "reg": "#ff4040"}

def outline_shapes(ctx: Ctx, geometry_all: dict) -> list[dict]:
    """Every outline drawn over a picture of the scan, in scan pixels.

    One list for both pictures that show them — the printed report draws it as
    sharp vector lines, the command line into its PNG — so the two can never
    disagree about where something was measured. Each shape is a dict:
    kind "poly" (points, closed), "line" (points), or "circle" (centre, r);
    with its colour, a relative line weight, and whether it is dashed or
    dotted."""
    shapes = []
    if ctx.reg is not None:
        shapes.append({"kind": "poly", "color": _COLORS["reg"], "weight": 1.2,
                       "dash": "dashed",
                       "points": np.asarray(ctx.reg.corners_px, float).tolist()})

    def add(roi, color):
        t = roi.get("type")
        if t == "rect":
            shapes.append({"kind": "poly", "color": color, "weight": 1.0,
                           "dash": "", "points": np.asarray(
                               roi["corners_px"], float).tolist()})
        elif t == "circle":
            shapes.append({"kind": "circle", "color": color, "weight": 1.0,
                           "dash": "", "centre": list(roi["center_px"]),
                           "r": float(roi["radius_px"])})
        elif t == "annulus":
            for r in (roi["inner_radius_px"], roi["outer_radius_px"]):
                shapes.append({"kind": "circle", "color": color, "weight": 0.6,
                               "dash": "dotted",
                               "centre": list(roi["center_px"]), "r": float(r)})
        elif t == "segment":
            shapes.append({"kind": "line", "color": color, "weight": 0.8,
                           "dash": "", "points": [list(roi["p0_px"]),
                                                  list(roi["p1_px"])]})

    def walk(node, color):
        if isinstance(node, dict):
            if node.get("type") in ("rect", "circle", "segment", "annulus"):
                add(node, color)
            else:
                for v in node.values():
                    walk(v, color)
        elif isinstance(node, list):
            for v in node:
                walk(v, color)

    for name in TESTS:
        g = geometry_all.get(name)
        if g and not g.get("_error"):
            walk(g, _COLORS[name])
    return shapes


def outlines_svg(shapes: list[dict], width: int, height: int) -> str:
    """The outlines as an SVG laid exactly over a picture of the whole scan.

    Its coordinates are scan pixels (viewBox), stretched onto the picture's
    box like the picture itself, so every line sits where it was measured
    however the page or the printer scales it — and stays sharp at any size,
    which drawn into the picture it could not. Line widths and dashes are set
    for a picture about 1000 px across."""
    unit = max(width, height) / 1000.0            # scan px per picture px
    dashes = {"dashed": f"{6 * unit:.1f} {4 * unit:.1f}",
              "dotted": f"{1.5 * unit:.1f} {2.5 * unit:.1f}"}
    out = [f'<svg class="outlines" viewBox="0 0 {width} {height}" '
           'preserveAspectRatio="none" aria-hidden="true" '
           'xmlns="http://www.w3.org/2000/svg">']
    for s in shapes:
        style = (f'fill="none" stroke="{s["color"]}" '
                 f'stroke-width="{1.4 * s["weight"] * unit:.2f}"')
        if s["dash"]:
            style += f' stroke-dasharray="{dashes[s["dash"]]}"'
        if s["kind"] == "circle":
            (x, y), r = s["centre"], s["r"]
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" {style}/>')
        else:
            pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in s["points"])
            tag = "polygon" if s["kind"] == "poly" else "polyline"
            out.append(f'<{tag} points="{pts}" {style}/>')
    out.append("</svg>")
    return "".join(out)


#: Longest side of the printed report's picture of the scan: about the width
#: of the report column on screen, and about 130 dpi across A4 when printed.
REPORT_PICTURE_PX = 1000


def report_overview(scan: ScanData, ctx: Ctx, geometry_all: dict,
                    fmt: str = "webp") -> dict:
    """The printed report's picture of the scan, and the outlines over it.

    The scan averaged down to REPORT_PICTURE_PX in the viewer's automatic
    window, encoded losslessly (the user chose WebP for reports: they are not
    used for visual QC, but nothing may lose detail anywhere). The outlines
    travel beside it as SVG rather than drawn into it: sharp at any zoom and
    in print, and the picture stays exactly the scan.

    The report's JPEG before this kept every 2nd or 3rd scan pixel with no
    averaging and was redrawn by the chart library, which kept 8-46 % of the
    finest line-pair group's bar contrast."""
    from . import imaging
    img = scan.pixels
    h, w = img.shape
    k = REPORT_PICTURE_PX / max(h, w)
    size = None if k >= 1 else (max(1, round(w * k)), max(1, round(h * k)))
    lo, hi = np.percentile(img, [1, 99])
    data, media = imaging.render(img, lo, hi, size, fmt)
    return {"picture": data, "media": media,
            "svg": outlines_svg(outline_shapes(ctx, geometry_all), w, h)}


def render_overlay(scan: ScanData, ctx: Ctx, geometry_all: dict,
                   max_px: int = 1400) -> bytes:
    """Annotated overview for the command line: image + registration corners
    + all ROIs, as a PNG beside its results. The scan is reduced by
    averaging, never by skipping pixels (which invents patterns in the line
    pairs), and PNG is lossless."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle as MplCircle
    from matplotlib.patches import Polygon as MplPolygon
    from . import imaging

    img = scan.pixels
    ds = max(1, int(np.ceil(max(img.shape) / max_px)))
    small = imaging.shrink_by_averaging(
        img, (-(-img.shape[1] // ds), -(-img.shape[0] // ds)))
    lo, hi = np.percentile(img, [1, 99])

    fig, axp = plt.subplots(figsize=(11, 11), dpi=120)
    axp.imshow(small, cmap="gray", vmin=lo, vmax=hi, interpolation="nearest",
               extent=(0, img.shape[1], img.shape[0], 0))
    axp.set_axis_off()

    styles = {"": "-", "dashed": "--", "dotted": ":"}
    for s in outline_shapes(ctx, geometry_all):
        lw, ls = s["weight"], styles[s["dash"]]
        if s["kind"] == "circle":
            axp.add_patch(MplCircle(s["centre"], s["r"], fill=False,
                                    edgecolor=s["color"], lw=lw, ls=ls))
        elif s["kind"] == "poly":
            axp.add_patch(MplPolygon(np.asarray(s["points"]), closed=True,
                                     fill=False, edgecolor=s["color"], lw=lw,
                                     ls=ls))
        else:
            p = np.asarray(s["points"])
            axp.plot(p[:, 0], p[:, 1], color=s["color"], lw=lw, ls=ls)

    buf = io.BytesIO()
    fig.tight_layout(pad=0.2)
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
