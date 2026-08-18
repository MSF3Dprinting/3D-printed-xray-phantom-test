"""SQLite persistence for analyses, plus flat metric export.

One row per analysis. The original upload bytes are kept on disk
(data/uploads/<id>.bin) for traceability and re-analysis.
"""

from __future__ import annotations

import contextlib
import hashlib
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
  status TEXT,
  site TEXT DEFAULT '',
  phantom TEXT DEFAULT '',
  operator TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  acquired_at TEXT DEFAULT '',
  validation_status TEXT DEFAULT '',
  validated_by TEXT DEFAULT '',
  validation_comment TEXT DEFAULT '',
  validated_at TEXT DEFAULT ''
);
"""

# Created only after the column migration has run — on a pre-labels database
# the indexed columns do not exist yet.
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_analyses_site ON analyses(site);
CREATE INDEX IF NOT EXISTS idx_analyses_phantom ON analyses(phantom);
"""

# Columns added after the first release; existing databases are migrated in
# place so an upgrade never loses stored analyses.
_ADDED_COLUMNS = {
    "site": "TEXT DEFAULT ''",
    "phantom": "TEXT DEFAULT ''",
    "operator": "TEXT DEFAULT ''",
    "notes": "TEXT DEFAULT ''",
    "acquired_at": "TEXT DEFAULT ''",
    "validation_status": "TEXT DEFAULT ''",
    "validated_by": "TEXT DEFAULT ''",
    "validation_comment": "TEXT DEFAULT ''",
    "validated_at": "TEXT DEFAULT ''",
}

LABEL_FIELDS = ("site", "phantom", "operator", "notes")

# The administrator's sign-off on an analysis. "" means nobody has ruled yet.
VALIDATION_STATES = ("validated", "conditionally_validated", "not_validated")
VALIDATION_LABELS = {
    "": "pending review",
    "validated": "validated",
    "conditionally_validated": "conditionally validated",
    "not_validated": "not validated",
}


class Store:
    #: JSON blob columns that :meth:`mutate_json` is allowed to touch.
    JSON_FIELDS = frozenset({"meta", "reg", "geometry", "results", "audit"})

    def __init__(self, root: str):
        self.root = root
        os.makedirs(os.path.join(root, "data", "uploads"), exist_ok=True)
        self.db_path = os.path.join(root, "data", "phantom_qa.sqlite3")
        with self._conn() as c:
            c.executescript(_SCHEMA)
            self._migrate(c)
            c.executescript(_INDEXES)

    def _migrate(self, conn):
        have = {r["name"] for r in
                conn.execute("PRAGMA table_info(analyses)").fetchall()}
        for col, decl in _ADDED_COLUMNS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE analyses ADD COLUMN {col} {decl}")

    def _connect(self) -> sqlite3.Connection:
        """A configured connection. The caller owns it and must close it.

        WAL lets gunicorn's worker processes read while another writes, which
        plain rollback-journal mode does not. It is a persistent property of
        the database file and creates two runtime companions next to it
        (-wal, -shm) that SQLite recreates automatically and that must never be
        committed or backed up on their own — see docs/DEPLOYMENT.md.
        busy_timeout makes a worker wait for a lock instead of failing the
        request with "database is locked"."""
        c = sqlite3.connect(self.db_path, timeout=15)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=15000")
        c.execute("PRAGMA synchronous=NORMAL")
        c.row_factory = sqlite3.Row
        return c

    @contextlib.contextmanager
    def _conn(self):
        """Commit-or-rollback AND close.

        `with sqlite3.connect(...)` only commits — it leaves the connection
        open, which leaks a file handle per request in a long-running server."""
        c = self._connect()
        try:
            with c:
                yield c
        finally:
            c.close()

    @contextlib.contextmanager
    def write_transaction(self):
        """A serialised read-modify-write.

        BEGIN IMMEDIATE takes the write lock up front, so two requests editing
        the same record queue instead of overlapping. Without it, read-whole-
        blob / modify / write-whole-blob silently discards whichever change
        was read first — a user moves an ROI and it springs back."""
        c = self._connect()
        c.isolation_level = None                 # we drive the transaction
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            c.close()

    def mutate_json(self, aid: str, field: str, fn):
        """Apply ``fn`` to one stored JSON blob atomically.

        ``fn`` receives the decoded value, mutates it **in place**, and may
        return a result which is passed back to the caller. A value returned
        by ``fn`` is not stored — only the in-place mutation is."""
        # The column name cannot be a bound parameter, so it is interpolated.
        # Every caller passes a literal today; the whitelist keeps it that way
        # if one ever starts forwarding a request field.
        if field not in self.JSON_FIELDS:
            raise ValueError(f"not a mutable JSON field: {field!r}")
        col = f"{field}_json"
        with self.write_transaction() as c:
            row = c.execute(f"SELECT {col} FROM analyses WHERE id=?",
                            (aid,)).fetchone()
            if row is None:
                raise KeyError(aid)
            value = json.loads(row[0]) if row[0] else None
            out = fn(value)
            c.execute(f"UPDATE analyses SET {col}=? WHERE id=?",
                      (json.dumps(value), aid))
            return out

    def find_by_sha256(self, sha256: str, exclude_id: str | None = None):
        """Analyses already holding this exact file."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, created_at, acquired_at, source_name, site, phantom,"
                " status, validation_status FROM analyses"
                " WHERE sha256=? AND id != ? ORDER BY created_at",
                (sha256, exclude_id or "")).fetchall()
        return [dict(r) for r in rows]

    def checkpoint(self):
        """Fold the write-ahead log back into the main database file.

        Call before copying the .sqlite3 file, so the copy is complete on its
        own and does not depend on a -wal file that was not copied with it."""
        c = self._connect()
        try:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.commit()
        finally:
            c.close()

    def backup_to(self, dest_path: str):
        """Consistent copy of the whole database while the app is running."""
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        src = self._connect()
        try:
            dst = sqlite3.connect(dest_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return dest_path

    # ------------------------------------------------------------- lifecycle

    def new_analysis(self, scan, file_bytes: bytes, signature: str,
                     algo_version: str, pdef_version: str,
                     labels: dict | None = None) -> str:
        aid = uuid.uuid4().hex[:12]
        with open(self.upload_path(aid), "wb") as f:
            f.write(file_bytes)
        lab = {k: str((labels or {}).get(k, "") or "").strip()
               for k in LABEL_FIELDS}
        # Prefer the acquisition time from the scan itself; it is what the
        # trend axis should use, not the moment the file happened to be uploaded.
        acquired = _acquired_at(scan.meta) or time.strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as c:
            c.execute(
                "INSERT INTO analyses (id, created_at, source_name, sha256, kind,"
                " reduced_precision, signature, meta_json, stage, audit_json,"
                " algo_version, pdef_version, status, is_baseline,"
                " site, phantom, operator, notes, acquired_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?)",
                (aid, time.strftime("%Y-%m-%d %H:%M:%S"), scan.source_name,
                 scan.sha256, scan.kind, int(scan.reduced_precision), signature,
                 json.dumps(scan.meta), "A", json.dumps([]),
                 algo_version, pdef_version, "draft",
                 lab["site"], lab["phantom"], lab["operator"], lab["notes"],
                 acquired))
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

    def list_all(self, site: str | None = None, phantom: str | None = None,
                 signature: str | None = None, validation: str | None = None,
                 completed_only: bool = False) -> list[dict]:
        sql = ("SELECT id, created_at, acquired_at, source_name, signature,"
               " stage, status, is_baseline, sha256, reduced_precision, sid_mm,"
               " site, phantom, operator, notes,"
               " validation_status, validated_by, validation_comment,"
               " validated_at"
               " FROM analyses WHERE 1=1")
        args: list = []
        for col, val in (("site", site), ("phantom", phantom),
                         ("signature", signature)):
            if val:
                sql += f" AND {col}=?"
                args.append(val)
        if validation is not None:
            # "pending" selects the rows nobody has ruled on yet
            sql += " AND COALESCE(validation_status,'')=?"
            args.append("" if validation == "pending" else validation)
        if completed_only:
            sql += " AND results_json IS NOT NULL"
        sql += " ORDER BY COALESCE(NULLIF(acquired_at,''), created_at) DESC"
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def labels(self) -> dict:
        """Distinct site / phantom values with counts, for the filter menus."""
        out = {}
        with self._conn() as c:
            for col in ("site", "phantom"):
                rows = c.execute(
                    f"SELECT {col} AS v, COUNT(*) AS n FROM analyses"
                    f" WHERE COALESCE({col},'') <> '' GROUP BY {col}"
                    f" ORDER BY {col}").fetchall()
                out[col] = [{"value": r["v"], "count": r["n"]} for r in rows]
        return out

    def set_labels(self, aid: str, labels: dict):
        fields = {k: str(v or "").strip() for k, v in labels.items()
                  if k in LABEL_FIELDS}
        if fields:
            self.update(aid, **fields)

    # ---------------------------------------------------------- validation

    def set_validation(self, aid: str, status: str, validated_by: str,
                       comment: str = "") -> dict:
        """Record the administrator's ruling on an analysis.

        ``validated_by`` is the NAME of the person taking responsibility. The
        admin password proves the right to sign off; the name says who did,
        which a shared password cannot."""
        status = (status or "").strip()
        if status and status not in VALIDATION_STATES:
            raise ValueError(f"unknown validation status: {status!r}")
        who = str(validated_by or "").strip()
        if status and not who:
            raise ValueError("the name of the approver is required")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S") if status else ""
        self.update(aid, validation_status=status, validated_by=who,
                    validation_comment=str(comment or "").strip(),
                    validated_at=stamp)
        return {"validation_status": status, "validated_by": who,
                "validation_comment": str(comment or "").strip(),
                "validated_at": stamp}

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

    # ----------------------------------------------------------- integrity

    def verify_integrity(self, aid: str) -> dict:
        """Re-hash the stored source file and compare with the hash recorded
        when it was analysed.

        A mismatch means the bytes on disk are no longer the bytes that
        produced the results — corruption, a restore of the wrong file, or
        tampering. It never happens by accident, so it is worth checking before
        relying on an old report."""
        rec = self.get(aid)
        if rec is None:
            return {"id": aid, "status": "not_found",
                    "message": "no such analysis"}
        path = self.upload_path(aid)
        if not os.path.exists(path):
            return {"id": aid, "status": "missing_file",
                    "stored_sha256": rec["sha256"], "computed_sha256": None,
                    "path": path, "size_bytes": None,
                    "message": "the stored source file is gone; results cannot "
                               "be traced back to their input"}
        h = hashlib.sha256()
        size = 0
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
                size += len(chunk)
        computed = h.hexdigest()
        ok = (computed == rec["sha256"])
        return {
            "id": aid,
            "status": "ok" if ok else "mismatch",
            "stored_sha256": rec["sha256"],
            "computed_sha256": computed,
            "path": path,
            "size_bytes": size,
            "source_name": rec["source_name"],
            "message": ("the stored file still hashes to the value recorded at "
                        "analysis time" if ok else
                        "THE STORED FILE NO LONGER MATCHES the hash recorded at "
                        "analysis time — do not rely on these results"),
        }

    def verify_all(self) -> list[dict]:
        return [self.verify_integrity(item["id"]) for item in self.list_all()]


def _acquired_at(meta: dict) -> str:
    """Acquisition timestamp from the DICOM header, as 'YYYY-MM-DD HH:MM:SS'."""
    d = str((meta or {}).get("StudyDate") or "").strip()
    t = str((meta or {}).get("SeriesTime")
            or (meta or {}).get("AcquisitionTime")
            or (meta or {}).get("StudyTime") or "").strip()
    if len(d) != 8 or not d.isdigit():
        return ""
    stamp = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    digits = "".join(ch for ch in t if ch.isdigit())
    if len(digits) >= 6:
        stamp += f" {digits[0:2]}:{digits[2:4]}:{digits[4:6]}"
    else:
        stamp += " 00:00:00"
    return stamp


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
    wr.writerow(["analysis_id", "site", "phantom", "operator", "acquired_at",
                 "created_at", "source", "signature", "is_baseline",
                 "validation", "validated_by", "validated_at",
                 "test", "object", "metric", "value", "unit", "status"])
    for rec in records:
        res = rec.get("results")
        if not res:
            continue
        for row in flatten_results(res):
            wr.writerow([rec["id"], rec.get("site", ""), rec.get("phantom", ""),
                         rec.get("operator", ""), rec.get("acquired_at", ""),
                         rec["created_at"], rec["source_name"],
                         rec["signature"], rec.get("is_baseline", 0),
                         VALIDATION_LABELS.get(rec.get("validation_status", ""),
                                               rec.get("validation_status", "")),
                         rec.get("validated_by", ""),
                         rec.get("validated_at", ""),
                         row["test"], row["object"], row["metric"],
                         row["value"], row["unit"], row["status"]])
    return buf.getvalue()


def wide_csv_export(records: list[dict]) -> str:
    """Wide-format CSV: one row per metric, one column per analysis.

    This is the shape you want for eyeballing drift across a series — the
    long format is better for pivot tables, this one is better for reading."""
    import csv
    import io as _io
    ordered = sorted(records,
                     key=lambda r: (r.get("acquired_at") or r["created_at"]))
    ordered = [r for r in ordered if r.get("results")]
    metrics: list[tuple] = []
    seen = set()
    per_rec = []
    for rec in ordered:
        m = {}
        for row in flatten_results(rec["results"]):
            key = (row["test"], row["object"], row["metric"])
            if key not in seen:
                seen.add(key)
                metrics.append((key, row["unit"]))
            m[key] = row["value"]
        per_rec.append(m)

    buf = _io.StringIO()
    wr = csv.writer(buf, lineterminator="\n")
    wr.writerow(["test", "object", "metric", "unit"]
                + [r["id"] for r in ordered])
    for label in ("site", "phantom", "operator", "acquired_at", "signature",
                  "status", "validation_status", "validated_by", "validated_at"):
        wr.writerow(["", "", f"# {label}", ""]
                    + [str(r.get(label, "") or "") for r in ordered])
    for key, unit in metrics:
        wr.writerow(list(key) + [unit]
                    + [m.get(key, "") for m in per_rec])
    return buf.getvalue()
