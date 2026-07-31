"""SQLite persistence for analyses, plus flat metric export.

One row per analysis. The original upload bytes are kept on disk
(data/uploads/<id>.bin) for traceability and re-analysis.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
  id TEXT PRIMARY KEY,
  created_at TEXT,
  source_name TEXT,
  sha256 TEXT,
  kind TEXT,
  reduced_precision INTEGER,
  signature TEXT,
  meta_json TEXT,
  stage TEXT,
  reg_json TEXT,
  geometry_json TEXT,
  results_json TEXT,
  audit_json TEXT,
  sid_mm REAL,
  is_baseline INTEGER DEFAULT 0,
  algo_version TEXT,
  pdef_version TEXT,
  status TEXT
);
"""


class Store:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(os.path.join(root, "data", "uploads"), exist_ok=True)
        self.db_path = os.path.join(root, "data", "phantom_qa.sqlite3")
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    # ------------------------------------------------------------- lifecycle

    def new_analysis(self, scan, file_bytes: bytes, signature: str,
                     algo_version: str, pdef_version: str) -> str:
        aid = uuid.uuid4().hex[:12]
        with open(self.upload_path(aid), "wb") as f:
            f.write(file_bytes)
        with self._conn() as c:
            c.execute(
                "INSERT INTO analyses (id, created_at, source_name, sha256, kind,"
                " reduced_precision, signature, meta_json, stage, audit_json,"
                " algo_version, pdef_version, status, is_baseline)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                (aid, time.strftime("%Y-%m-%d %H:%M:%S"), scan.source_name,
                 scan.sha256, scan.kind, int(scan.reduced_precision), signature,
                 json.dumps(scan.meta), "A", json.dumps([]),
                 algo_version, pdef_version, "draft"))
        return aid

    def upload_path(self, aid: str) -> str:
        return os.path.join(self.root, "data", "uploads", f"{aid}.bin")

    def get(self, aid: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM analyses WHERE id=?", (aid,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        for k in ("meta_json", "reg_json", "geometry_json", "results_json",
                  "audit_json"):
            d[k[:-5]] = json.loads(d.pop(k)) if d.get(k) else None
        return d

    def update(self, aid: str, **fields):
        cols, vals = [], []
        for k, v in fields.items():
            if k in ("meta", "reg", "geometry", "results", "audit"):
                cols.append(f"{k}_json=?")
                vals.append(json.dumps(v))
            else:
                cols.append(f"{k}=?")
                vals.append(v)
        vals.append(aid)
        with self._conn() as c:
            c.execute(f"UPDATE analyses SET {', '.join(cols)} WHERE id=?", vals)

    def audit(self, aid: str, stage: str, action: str, detail=None):
        rec = self.get(aid)
        log = rec["audit"] or []
        log.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "stage": stage,
                    "action": action, "detail": detail})
        self.update(aid, audit=log)

    def list_all(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, created_at, source_name, signature, stage, status,"
                " is_baseline, sha256, reduced_precision, sid_mm"
                " FROM analyses ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def set_baseline(self, aid: str, value: bool = True):
        rec = self.get(aid)
        if value and rec:
            # single baseline per signature
            with self._conn() as c:
                c.execute("UPDATE analyses SET is_baseline=0 WHERE signature=?",
                          (rec["signature"],))
        self.update(aid, is_baseline=int(value))

    def baseline_for(self, signature: str, exclude_id: str | None = None):
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM analyses WHERE signature=? AND is_baseline=1"
                " AND id != ? LIMIT 1", (signature, exclude_id or "")).fetchone()
        if row is None:
            return None
        return self.get(row["id"])

    def delete(self, aid: str):
        with self._conn() as c:
            c.execute("DELETE FROM analyses WHERE id=?", (aid,))
        p = self.upload_path(aid)
        if os.path.exists(p):
            os.remove(p)


# ------------------------------------------------------------------ flattening

def flatten_results(results: dict) -> list[dict]:
    """One row per metric: {test, object, metric, value, unit, status}."""
    rows = []

    def add(test, obj, metric, value, unit="", status=""):
        if value is None:
            return
        rows.append({"test": test, "object": obj, "metric": metric,
                     "value": value, "unit": unit, "status": status})

    g = results.get("geometry") or {}
    for side, r in (g.get("rulers") or {}).items():
        if not r.get("detected"):
            continue
        add("geometry", f"ruler_{side}", "pitch", r["pitch_mm"], "mm")
        add("geometry", f"ruler_{side}", "pitch_dev", r["pitch_dev_pct"], "%")
        add("geometry", f"ruler_{side}", "linearity_rms", r["linearity_rms_mm"], "mm")
        add("geometry", f"ruler_{side}", "central_line_from_edge",
            r["central_line_from_edge_mm"], "mm")
    d = g.get("dimensions") or {}
    for k in ("side_top_mm", "side_right_mm", "side_bottom_mm", "side_left_mm",
              "diag_tlbr_mm", "diag_trbl_mm", "mean_side_mm"):
        if k in d:
            add("geometry", "corners", k.replace("_mm", ""), d[k], "mm",
                g.get("dimension_status", ""))
    if "dev_from_nominal_pct" in d:
        add("geometry", "corners", "dev_from_nominal", d["dev_from_nominal_pct"],
            "%", g.get("dimension_status", ""))
    seps = g.get("central_line_separations") or {}
    add("geometry", "central_lines", "separation_vertical",
        seps.get("vertical_mm"), "mm")
    add("geometry", "central_lines", "separation_horizontal",
        seps.get("horizontal_mm"), "mm")
    sc = g.get("scale") or {}
    add("geometry", "scale", "pitch_measured", sc.get("pitch_measured_mm"), "mm")
    add("geometry", "scale", "absolute_mm_per_px",
        sc.get("absolute_mm_per_px"), "mm/px")
    for side, f in (g.get("field_alignment") or {}).items():
        if f.get("detected"):
            add("alignment", f"field_{side}", "deviation",
                f["deviation_from_central_line_mm"], "mm", f.get("status", ""))
            add("alignment", f"field_{side}", "pct_of_sid", f["pct_of_sid"], "%",
                f.get("status", ""))

    u = results.get("uniformity") or {}
    for r in u.get("rows", []):
        add("uniformity", r["id"], "mean", r["mean"])
        add("uniformity", r["id"], "std", r["std"])
        add("uniformity", r["id"], "snr", r["snr"], "", r.get("status", ""))
        add("uniformity", r["id"], "dsnr", r["dsnr_pct"], "%", r.get("status", ""))
    add("uniformity", "all", "snr_avg", u.get("snr_avg"))
    add("uniformity", "all", "max_abs_dsnr", u.get("max_abs_dsnr_pct"), "%",
        u.get("status", ""))

    w = results.get("wedge") or {}
    for r in w.get("rows", []):
        add("wedge", f"S{r['step']}", "mean", r["mean"], "",
            "saturated" if r.get("saturated") else "")
        add("wedge", f"S{r['step']}", "std", r["std"])
    if w.get("fit"):
        add("wedge", "fit", "r2", w["fit"]["r2"], "", w.get("status", ""))
        add("wedge", "fit", "slope", w["fit"]["slope"], "/step")
    add("wedge", "all", "dynamic_range_ratio", w.get("dynamic_range_ratio"), "x",
        w.get("status", ""))
    add("wedge", "all", "span", w.get("span"))
    if "monotonic" in w:
        add("wedge", "all", "monotonic", int(bool(w["monotonic"])))

    lp = results.get("linepairs") or {}
    for r in lp.get("rows", []):
        lin = r.get("linearity") or {}
        add("linepairs", r["id"], "sd", r["std"], "", r.get("status", ""))
        add("linepairs", r["id"], "mean", r["mean"])
        add("linepairs", r["id"], "measured_pitch", lin.get("measured_pitch_mm"), "mm")
        add("linepairs", r["id"], "pitch_dev", lin.get("pitch_dev_pct"), "%")
        add("linepairs", r["id"], "grid_residual_rms",
            lin.get("residual_rms_mm"), "mm")
        add("linepairs", r["id"], "n_lines", lin.get("used_lines"))
        add("linepairs", r["id"], "measured_freq", lin.get("measured_freq_lp_mm"),
            "lp/mm")

    lc = results.get("lowcontrast") or {}
    for r in lc.get("rows", []):
        add("lowcontrast", r["id"], "cnr", r["cnr"], "", lc.get("status", ""))
        add("lowcontrast", r["id"], "obj_mean", r["obj_mean"])
        add("lowcontrast", r["id"], "bg_mean", r["bg_mean"])
    return rows


def csv_export(records: list[dict]) -> str:
    """Long-format CSV across one or more analyses (Excel-friendly)."""
    import csv
    import io as _io
    buf = _io.StringIO()
    wr = csv.writer(buf, lineterminator="\n")
    wr.writerow(["analysis_id", "created_at", "source", "signature",
                 "is_baseline", "test", "object", "metric", "value", "unit",
                 "status"])
    for rec in records:
        res = rec.get("results")
        if not res:
            continue
        for row in flatten_results(res):
            wr.writerow([rec["id"], rec["created_at"], rec["source_name"],
                         rec["signature"], rec.get("is_baseline", 0),
                         row["test"], row["object"], row["metric"],
                         row["value"], row["unit"], row["status"]])
    return buf.getvalue()
