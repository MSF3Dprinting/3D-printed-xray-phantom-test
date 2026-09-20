"""Golden-value benchmark of the analysis pipeline over the HQ reference scans.

The unit tests pin behaviour on synthetic images; this pins it on the real ones.
Its job is to make an unintended change in registration, ROI placement or any
measured number visible as a diff, so that work on the detection algorithms can
proceed without silently moving the numbers every stored baseline depends on.

Three pieces:

``INVENTORY``          which scans count as reference material, in code so the
                       reasoning (and what is deliberately excluded) is
                       reviewable.
``hq_benchmark.json``  the golden values, committed. It holds derived numbers
                       only — no pixels and no identifying DICOM tags.
``measure()``          the one code path both the generator and the test use, so
                       they can never drift apart.

Regenerate after an INTENDED change, and read the diff before committing it:

    python tests/hq_manifest.py --update

The scans themselves are not in git (large, and they carry site-identifying
tags). Everything here degrades to a skip when they are absent.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

MANIFEST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "hq_benchmark.json")

#: Root of the reference scan drops. Overridable for a machine that keeps them
#: elsewhere; the sample-scan fixtures in conftest use the same convention.
HQ_ROOT = os.environ.get("PHANTOMQA_HQ_DIR", "").strip() \
    or os.path.join(ROOT, "HQ testing")

_PH_2707 = os.path.join("20260727 MSF", "MSF", "Export_2026-07-27_10-00-05_1",
                        "10000000", "10000001")
_PH_3007 = os.path.join("20260730 MSF 2", "MSF 2", "Export_2026-07-30_14-23-58_1",
                        "10000000", "10000001")

#: Carestream DICOM/0000NN images showing a COMMERCIAL Leeds PIX-13 test object,
#: not the MSF phantom. They are reference scans of something else entirely and
#: must never enter this benchmark, a phantom definition or a baseline.
LEEDS_PIX13 = frozenset({0, 20, 22, 23, 24, 25, 26, 28, 29, 30, 31, 33})

#: Burned-in label of each Carestream exposure: which physical print it is.
#: Two prints of the same design are in use ("licht blauw" / "donker blauw"),
#: and they differ in build details, so the print is part of a scan's identity.
_CS_LABELS = {
    1: "3 licht blauw", 2: "2 licht blauw", 3: "5 licht blauw",
    4: "15 donker blauw", 5: "16 donker blauw", 6: "14 donker blauw",
    7: "20 donker blauw", 8: "17 donker blauw", 9: "18 donker blauw",
    10: "19 donker blauw", 11: "13 licht blauw", 12: "23 donker blauw",
    13: "21 donker blauw", 14: "22 donker blauw", 15: "12 licht blauw",
    16: "26 donker blauw", 17: "28 donker blauw", 18: "27 donker blauw",
    19: "25 donker blauw", 21: "29 donker blauw", 27: "11 licht blauw",
    32: "10 licht blauw", 34: "9 licht blauw", 35: "8 licht blauw",
    36: "7 licht blauw", 37: "4 licht blauw", 38: "6 licht blauw",
}

#: The everyday subset: small enough to run in the ordinary suite, wide enough
#: to catch a real change. Covers both detectors, both prints, four phantom
#: orientations, the dose extremes, and the two known weak spots (the
#: low-contrast disc order on a dark print, and partial line-block detection).
_SUBSET = {"PH0730_03", "PH0730_05", "CS000001", "CS000004", "CS000018",
           "CS000036"}


def _inventory():
    items = [
        ("PH0727_03", os.path.join(_PH_2707, "10000002", "10000003"),
         "philips", "HmmEi"),
        ("PH0727_05", os.path.join(_PH_2707, "10000004", "10000005"),
         "philips", "A8Tgg"),
        ("PH0730_03", os.path.join(_PH_3007, "10000002", "10000003"),
         "philips", "HmmEi image 1"),
        ("PH0730_05", os.path.join(_PH_3007, "10000004", "10000005"),
         "philips", "A8Tgg image 1"),
        ("PH0730_07", os.path.join(_PH_3007, "10000006", "10000007"),
         "philips", "HmmEi image 2"),
        ("PH0730_09", os.path.join(_PH_3007, "10000008", "10000009"),
         "philips", "A8Tgg image 2"),
    ]
    for i in range(39):
        if i in LEEDS_PIX13:
            continue
        items.append((f"CS{i:06d}", os.path.join("DICOM", f"{i:06d}"),
                      "carestream", _CS_LABELS.get(i, "?")))
    return [{"key": k, "relpath": p.replace("\\", "/"), "group": g, "label": lab,
             "subset": k in _SUBSET} for k, p, g, lab in items]


INVENTORY = _inventory()


def scan_path(entry) -> str:
    return os.path.join(HQ_ROOT, *entry["relpath"].split("/"))


def available(entry) -> bool:
    return os.path.exists(scan_path(entry))


def have_scans() -> bool:
    """True when every inventoried scan is present."""
    return all(available(e) for e in INVENTORY)


# ----------------------------------------------------------------- tolerances
# Results are deterministic on one machine, so these only have to absorb library
# and platform drift. Each is the size of a difference that would matter to an
# operator reading the report — anything larger is a real change and should stop
# the build until someone has looked at it.
TOL = {
    "rotation_deg": 0.05,        # deg
    "mm_per_px_rel": 0.001,      # 0.1 %
    "residual_rms_mm": 0.05,     # mm
    "roi_center_mm": 0.50,       # mm — half the width of a line-pair bar
    "mean_side_mm": 0.10,        # mm
    "dev_pct": 0.10,             # percentage points
    "pitch_mm": 0.005,           # mm
    "cnr": 0.05,                 # CNR units
    "dsnr_pct": 1.0,             # percentage points
    "snr": 0.5,
    "r2": 0.01,
    "ratio_rel": 0.02,           # 2 % on the wedge dynamic range
    "angle_deg": 0.50,           # deg, low-contrast block
    # Acquisition-gate signals. Loose enough to ignore noise, far tighter than
    # the distance between a reference scan and a broken exposure.
    "quality": {
        "saturation": 0.005,
        "clipping": 0.002,
        "landmarks": 0.0,        # a whole ruler line, so exact
        "recognition": 0.10,
        "placement_margin": 0.10,
        "default": 0.05,
    },
}


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _f(v):
    """Round for a readable, diffable manifest; keep None as None."""
    return None if v is None else round(float(v), 6)


def _centers(nodes):
    out = {}
    for n in nodes:
        roi = n.get("roi") or {}
        c = roi.get("center_mm")
        if c is not None:
            out[roi.get("id") or n.get("id")] = [_f(c[0]), _f(c[1])]
    return out


def nonfinite_paths(obj, path=""):
    """Every NaN/Inf in a result tree, by location.

    A reference scan must not produce one. Saturated field scans do, which is
    what turns the results page into a dead 'Computing…' screen, so locking this
    down on the good scans is worth a line in the benchmark."""
    import math
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            bad += nonfinite_paths(v, f"{path}/{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            bad += nonfinite_paths(v, f"{path}[{i}]")
    elif isinstance(obj, float) and not math.isfinite(obj):
        bad.append(path or "<root>")
    return bad


def measure(path: str, pdef) -> dict:
    """Run the full automatic pipeline and reduce it to the pinned numbers."""
    from phantom_qa import ingest, pipeline, quality

    scan = ingest.load_path(path)[0]
    reg = pipeline.run_stage_a(scan, pdef)
    # The acquisition gate is pinned here too. Its thresholds were set from
    # these scans, so recording what each one measures keeps the evidence and
    # the limits in one reviewable place: tighten a threshold and the diff
    # shows every reference scan it would newly reject.
    gate = quality.assess(scan.pixels, reg)
    ctx = pipeline.build_ctx(scan, pdef, reg,
                             {"scan_meta": scan.meta, "sid_mm": 1000.0})
    geom = pipeline.propose_all(ctx)
    res = pipeline.compute_all(ctx, geom)

    gm = res.get("geometry") or {}
    lp, lc = res.get("linepairs") or {}, res.get("lowcontrast") or {}
    un, wd = res.get("uniformity") or {}, res.get("wedge") or {}
    g_lc = geom.get("lowcontrast") or {}
    T = reg.transform

    centers = {}
    centers.update(_centers((geom.get("uniformity") or {}).get("squares") or []))
    centers.update(_centers((geom.get("linepairs") or {}).get("groups") or []))
    centers.update(_centers(g_lc.get("circles") or []))
    centers.update(_centers((geom.get("wedge") or {}).get("steps") or []))

    return {
        "registration": {
            "rotation_deg": _f(T.rotation_deg),
            "mirrored": bool(T.mirrored),
            "mm_per_px": _f(T.mm_per_px),
            "residual_rms_mm": _f(reg.residual_rms_mm),
            "landmarks_found": sum(
                1 for v in reg.landmarks.values() if v.get("err_mm") is not None),
        },
        "status": {
            "overall": pipeline.overall_status(res),
            "geometry_dimension": gm.get("dimension_status"),
            "geometry_field": gm.get("field_status"),
            "linepairs": lp.get("status"),
            "lowcontrast": lc.get("status"),
            "uniformity": un.get("status"),
            "wedge": wd.get("status"),
        },
        "metrics": {
            "mean_side_mm": _f((gm.get("dimensions") or {}).get("mean_side_mm")),
            "dev_from_nominal_pct": _f(
                (gm.get("dimensions") or {}).get("dev_from_nominal_pct")),
            "linepairs_blocks_detected":
                (geom.get("linepairs") or {}).get("n_blocks_detected"),
            "linepairs_row_status": {r["id"]: r.get("status")
                                     for r in lp.get("rows") or []},
            "linepairs_pitch_mm": {
                r["id"]: _f((r.get("linearity") or {}).get("measured_pitch_mm"))
                for r in lp.get("rows") or []},
            "lowcontrast_detected": bool(g_lc.get("detected")),
            "lowcontrast_angle_deg": _f(g_lc.get("angle_deg")),
            "lowcontrast_block_center_mm": [
                _f(v) for v in ((g_lc.get("block") or {}).get("center_mm")
                                or [None, None])],
            "lowcontrast_ordering_ok": bool(lc.get("ordering_ok")),
            "lowcontrast_cnr": {r["id"]: _f(r.get("cnr"))
                                for r in lc.get("rows") or []},
            "uniformity_snr_avg": _f(un.get("snr_avg")),
            "uniformity_dsnr_pct": {r["id"]: _f(r.get("dsnr_pct"))
                                    for r in un.get("rows") or []},
            "wedge_monotonic": bool(wd.get("monotonic")),
            "wedge_r2": _f((wd.get("fit") or {}).get("r2")),
            "wedge_dynamic_range_ratio": _f(wd.get("dynamic_range_ratio")),
        },
        "roi_centers_mm": centers,
        "nonfinite": sorted(nonfinite_paths(res)),
        "quality": {
            "verdict": gate["verdict"],
            "failed": gate["failed"],
            "values": {c["id"]: _f(c["value"]) for c in gate["checks"]},
        },
    }


# ------------------------------------------------------------------ comparison

def _near(a, b, tol):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


def _rel(a, b, tol):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol * max(abs(float(b)), 1e-9)


def compare(actual: dict, expected: dict) -> list[str]:
    """Differences that exceed tolerance, as readable one-liners."""
    out = []

    def exact(label, a, b):
        if a != b:
            out.append(f"{label}: {a!r} != expected {b!r}")

    def close(label, a, b, tol, rel=False):
        ok = _rel(a, b, tol) if rel else _near(a, b, tol)
        if not ok:
            d = "" if a is None or b is None else f" (delta {float(a) - float(b):+.4g})"
            out.append(f"{label}: {a} != expected {b}{d}")

    ra, re_ = actual["registration"], expected["registration"]
    close("registration.rotation_deg", ra["rotation_deg"], re_["rotation_deg"],
          TOL["rotation_deg"])
    exact("registration.mirrored", ra["mirrored"], re_["mirrored"])
    close("registration.mm_per_px", ra["mm_per_px"], re_["mm_per_px"],
          TOL["mm_per_px_rel"], rel=True)
    close("registration.residual_rms_mm", ra["residual_rms_mm"],
          re_["residual_rms_mm"], TOL["residual_rms_mm"])
    exact("registration.landmarks_found", ra["landmarks_found"],
          re_["landmarks_found"])

    for k, v in expected["status"].items():
        exact(f"status.{k}", actual["status"].get(k), v)

    ma, me = actual["metrics"], expected["metrics"]
    close("metrics.mean_side_mm", ma["mean_side_mm"], me["mean_side_mm"],
          TOL["mean_side_mm"])
    close("metrics.dev_from_nominal_pct", ma["dev_from_nominal_pct"],
          me["dev_from_nominal_pct"], TOL["dev_pct"])
    exact("metrics.linepairs_blocks_detected", ma["linepairs_blocks_detected"],
          me["linepairs_blocks_detected"])
    exact("metrics.linepairs_row_status", ma["linepairs_row_status"],
          me["linepairs_row_status"])
    for gid, val in me["linepairs_pitch_mm"].items():
        close(f"metrics.linepairs_pitch_mm[{gid}]",
              ma["linepairs_pitch_mm"].get(gid), val, TOL["pitch_mm"])
    exact("metrics.lowcontrast_detected", ma["lowcontrast_detected"],
          me["lowcontrast_detected"])
    close("metrics.lowcontrast_angle_deg", ma["lowcontrast_angle_deg"],
          me["lowcontrast_angle_deg"], TOL["angle_deg"])
    for i, axis in enumerate("xy"):
        close(f"metrics.lowcontrast_block_center_mm.{axis}",
              ma["lowcontrast_block_center_mm"][i],
              me["lowcontrast_block_center_mm"][i], TOL["roi_center_mm"])
    exact("metrics.lowcontrast_ordering_ok", ma["lowcontrast_ordering_ok"],
          me["lowcontrast_ordering_ok"])
    for cid, val in me["lowcontrast_cnr"].items():
        close(f"metrics.lowcontrast_cnr[{cid}]",
              ma["lowcontrast_cnr"].get(cid), val, TOL["cnr"])
    close("metrics.uniformity_snr_avg", ma["uniformity_snr_avg"],
          me["uniformity_snr_avg"], TOL["snr"])
    for sid, val in me["uniformity_dsnr_pct"].items():
        close(f"metrics.uniformity_dsnr_pct[{sid}]",
              ma["uniformity_dsnr_pct"].get(sid), val, TOL["dsnr_pct"])
    exact("metrics.wedge_monotonic", ma["wedge_monotonic"], me["wedge_monotonic"])
    close("metrics.wedge_r2", ma["wedge_r2"], me["wedge_r2"], TOL["r2"])
    close("metrics.wedge_dynamic_range_ratio", ma["wedge_dynamic_range_ratio"],
          me["wedge_dynamic_range_ratio"], TOL["ratio_rel"], rel=True)

    ca, ce = actual["roi_centers_mm"], expected["roi_centers_mm"]
    missing = set(ce) - set(ca)
    if missing:
        out.append(f"roi_centers_mm: missing {sorted(missing)}")
    for rid, (ex, ey) in ce.items():
        got = ca.get(rid)
        if got is None:
            continue
        if abs(got[0] - ex) > TOL["roi_center_mm"] or \
           abs(got[1] - ey) > TOL["roi_center_mm"]:
            out.append(f"roi_centers_mm[{rid}]: moved "
                       f"({got[0] - ex:+.2f}, {got[1] - ey:+.2f}) mm "
                       f"to ({got[0]:.2f}, {got[1]:.2f})")

    if actual["nonfinite"]:
        out.append("results contain NaN/Inf at: "
                   + ", ".join(actual["nonfinite"][:8]))

    qa, qe = actual.get("quality") or {}, expected.get("quality") or {}
    if qe:
        exact("quality.verdict", qa.get("verdict"), qe.get("verdict"))
        exact("quality.failed", qa.get("failed"), qe.get("failed"))
        for cid, val in (qe.get("values") or {}).items():
            got = (qa.get("values") or {}).get(cid)
            tol = TOL["quality"].get(cid, TOL["quality"]["default"])
            close(f"quality.values[{cid}]", got, val, tol)
    return out


# ------------------------------------------------------------------- generator

def load_manifest() -> dict:
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def build(keys=None, verbose=True) -> dict:
    from phantom_qa import ALGO_VERSION
    from phantom_qa.phantom_def import load_default
    import numpy, scipy, skimage

    pdef = load_default()
    scans = {}
    for entry in INVENTORY:
        if keys and entry["key"] not in keys:
            continue
        path = scan_path(entry)
        if not os.path.exists(path):
            raise SystemExit(f"missing reference scan: {path}")
        if verbose:
            print(f"  {entry['key']:10s} {entry['label']:16s} …",
                  end="", flush=True)
        rec = {"key": entry["key"], "relpath": entry["relpath"],
               "group": entry["group"], "label": entry["label"],
               "subset": entry["subset"], "sha256": sha256_of(path)}
        rec.update(measure(path, pdef))
        scans[entry["key"]] = rec
        if verbose:
            print(f" {rec['status']['overall']}")
    return {
        "_comment": "Golden values for the HQ reference scans. Regenerate with "
                    "`python tests/hq_manifest.py --update` ONLY when a change "
                    "in the numbers is intended, and review the diff.",
        "algo_version": ALGO_VERSION,
        "pdef_version": pdef.version,
        "environment": {
            "python": ".".join(str(v) for v in sys.version_info[:3]),
            "numpy": numpy.__version__, "scipy": scipy.__version__,
            "scikit-image": skimage.__version__,
        },
        "tolerances": TOL,
        "scans": scans,
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--update" not in argv:
        print(__doc__)
        return 2
    only = [a for a in argv if not a.startswith("-")]
    if not have_scans() and not only:
        missing = [e["key"] for e in INVENTORY if not available(e)]
        raise SystemExit(f"reference scans missing ({len(missing)}): "
                         f"{', '.join(missing[:6])}…  looked under {HQ_ROOT}")
    print(f"measuring {len(only) or len(INVENTORY)} reference scans "
          f"from {HQ_ROOT}")
    manifest = build(keys=set(only) or None)
    if only and os.path.exists(MANIFEST_PATH):       # partial refresh
        old = load_manifest()
        old["scans"].update(manifest["scans"])
        manifest = old
    with open(MANIFEST_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"written {MANIFEST_PATH} ({os.path.getsize(MANIFEST_PATH)} bytes, "
          f"{len(manifest['scans'])} scans)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
