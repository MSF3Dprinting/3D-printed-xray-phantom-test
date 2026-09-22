"""SQLite persistence for analyses, plus flat metric export.

One row per analysis. The original upload bytes are kept on disk
(data/uploads/<id>.bin) for traceability and re-analysis.

Two side tables hang off it:

``geometry_history``  one compressed snapshot per measuring-point edit, so
                      Stage C can offer undo / redo / reset without the user
                      having to re-upload the scan.
``phantom_profiles``  the confirmed measuring-point layout of ONE physical
                      phantom, keyed by its operator-typed label. Stored in
                      phantom-frame millimetres, which is why it replays
                      correctly onto a scan where the phantom lay at a
                      different angle on the detector.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import time
import uuid
import zlib

log = logging.getLogger("phantomqa.store")


def _safe_json(raw):
    """Decode a JSON column, or None. Never raises on a damaged blob."""
    try:
        return json.loads(raw) if raw else None
    except (json.JSONDecodeError, TypeError):
        return None


def _safe_trail(raw):
    """An audit trail as a list, whatever is actually in the column.

    Used by the schema upgrade, which runs before anything else can report a
    problem: one corrupted blob must not stop a database from opening."""
    try:
        trail = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return []
    return trail if isinstance(trail, list) else []


class ProtectedAnalysis(Exception):
    """A record that may not be discarded without the administrator password.

    Discarding is meant for work in progress — a mis-set exposure, a wrong
    phantom typed in, an attempt abandoned half way. Once an analysis has been
    finalised, ruled on, or made the reference its phantom is judged against,
    removing it destroys a decision somebody made, and that stays behind the
    administrator password it always was.
    """

    def __init__(self, aid: str, reasons: list[str]):
        super().__init__(f"analysis {aid} is protected: {', '.join(reasons)}")
        self.aid = aid
        self.reasons = reasons


class StaleGeometry(Exception):
    """An edit was computed from measuring points that have since changed.

    There is one shared login, and the audit log shows two people working on
    the same analysis minutes apart from different addresses. Their edits are
    serialised — each is a transaction — but serialising them only decides the
    ORDER in which one silently overwrites the other. The second operator drags
    a point, the first one's correction disappears, and nothing anywhere says
    so.

    So an edit may declare which state it was based on. If the stored geometry
    has moved on, it is refused rather than applied blindly, and the operator
    is told to reopen the analysis. Omitting the declaration keeps the previous
    behaviour, which is what the command line and older clients rely on.
    """

    def __init__(self, expected: int, actual: int):
        super().__init__(
            f"these measuring points were edited from state {expected}, but "
            f"the analysis is now at state {actual}")
        self.expected = expected
        self.actual = actual

#: Environment variable naming the directory that holds ``data/``. Unset means
#: the application directory, which is what every existing deployment uses — so
#: leaving it alone changes nothing. It exists because the web module builds its
#: Store at IMPORT time: anything that imports it (a test helper, a stray
#: ``python -c``) otherwise creates — and migrates — the database of whatever
#: checkout it is sitting in, which on a server is the live one.
DATA_ROOT_ENV = "PHANTOMQA_DATA_ROOT"


def resolve_root(app_root: str) -> str:
    """Directory under which ``data/`` lives, honouring DATA_ROOT_ENV."""
    override = os.environ.get(DATA_ROOT_ENV, "").strip()
    return os.path.abspath(override) if override else app_root


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
  validated_at TEXT DEFAULT '',
  geometry_seq INTEGER DEFAULT 0,
  layout_source TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS geometry_history (
  analysis_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  user TEXT DEFAULT '',
  action TEXT NOT NULL,
  detail_json TEXT,
  geometry_z BLOB NOT NULL,
  PRIMARY KEY (analysis_id, seq)
);

-- One snapshot of everything a re-analysis is about to overwrite.
--
-- Reworking a record has always destroyed the previous numbers in place: the
-- report would be rendered from new results with no trace of what they
-- replaced, and an operator who started a re-run on a slow link and lost the
-- connection was left with a record holding nothing at all. A QA record that
-- can change without keeping what it changed from is not much of a record.
CREATE TABLE IF NOT EXISTS analysis_revisions (
  analysis_id TEXT NOT NULL,
  rev INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  user TEXT DEFAULT '',
  reason TEXT DEFAULT '',
  mode TEXT DEFAULT '',
  status TEXT DEFAULT '',
  overall TEXT DEFAULT '',
  finalized_at TEXT DEFAULT '',
  finalized_by TEXT DEFAULT '',
  validation_json TEXT,
  was_baseline INTEGER DEFAULT 0,
  sid_mm REAL,
  layout_source TEXT DEFAULT '',
  snapshot_z BLOB NOT NULL,
  PRIMARY KEY (analysis_id, rev)
);

CREATE TABLE IF NOT EXISTS phantom_profiles (
  phantom_key TEXT PRIMARY KEY,
  phantom_norm TEXT DEFAULT '',
  layout_json TEXT NOT NULL DEFAULT '{}',
  pdef_version TEXT DEFAULT '',
  algo_version TEXT DEFAULT '',
  source_analysis_id TEXT DEFAULT '',
  created_at TEXT DEFAULT '',
  updated_at TEXT DEFAULT '',
  updated_by TEXT DEFAULT ''
);
"""

# Created only after the column migration has run — on a pre-labels database
# the indexed columns do not exist yet.
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_analyses_site ON analyses(site);
CREATE INDEX IF NOT EXISTS idx_analyses_phantom ON analyses(phantom);
CREATE INDEX IF NOT EXISTS idx_geomhist ON geometry_history(analysis_id, seq);
CREATE INDEX IF NOT EXISTS idx_profiles_norm ON phantom_profiles(phantom_norm);
CREATE INDEX IF NOT EXISTS idx_revisions ON analysis_revisions(analysis_id, rev);
"""

# Columns added after the first release; existing databases are migrated in
# place so an upgrade never loses stored analyses. Keyed by table, because
# the side tables gain columns the same way the main one does.
_ADDED_COLUMNS = {
    "analyses": {
        "site": "TEXT DEFAULT ''",
        "phantom": "TEXT DEFAULT ''",
        "operator": "TEXT DEFAULT ''",
        "notes": "TEXT DEFAULT ''",
        "acquired_at": "TEXT DEFAULT ''",
        "validation_status": "TEXT DEFAULT ''",
        "validated_by": "TEXT DEFAULT ''",
        "validation_comment": "TEXT DEFAULT ''",
        "validated_at": "TEXT DEFAULT ''",
        "geometry_seq": "INTEGER DEFAULT 0",
        "layout_source": "TEXT DEFAULT ''",
        # Verdict of the acquisition-quality gate, as JSON. Empty on rows that
        # predate it, which reads as "never assessed" rather than "passed" —
        # the record is what the gate said, not a promise that it ran.
        "quality_json": "TEXT DEFAULT ''",
        # The one word out of that blob, kept separately so History can show a
        # badge per row without carrying the whole assessment. Operators work
        # over connections where a listing of fifty rows matters.
        "quality_verdict": "TEXT DEFAULT ''",
        # When the operator declared this analysis done, and who was signed in.
        # Finalize used to change no column at all — it only appended a line to
        # the record's own audit trail — so "is this finished?" could not be
        # asked in SQL, and nothing could be allowed or refused on the strength
        # of the answer. Empty means not finalised.
        "finalized_at": "TEXT DEFAULT ''",
        "finalized_by": "TEXT DEFAULT ''",
        # The detector's exposure values, copied out of meta_json for the same
        # reason quality_verdict is: History shows them on every row and the
        # CSV exports carry them, and neither reads the header blob. NULL means
        # the detector did not record the value — never zero, never a guess.
        # See EXPOSURE_FIELDS; the header in meta_json stays the original.
        "exposure_index": "REAL",
        "target_exposure_index": "REAL",
        "deviation_index": "REAL",
        "sensitivity": "REAL",
    },
    "phantom_profiles": {
        "phantom_norm": "TEXT DEFAULT ''",
        "algo_version": "TEXT DEFAULT ''",
        "updated_by": "TEXT DEFAULT ''",
        # The layout this one replaced, kept one level deep.
        #
        # A layout is written when step C is confirmed — before the analysis is
        # finalised, and before anyone knows whether it was any good. Throwing
        # that analysis away therefore used to leave its measuring points
        # standing as the phantom's default for every later scan. Keeping the
        # previous one means a discard can put back what was there rather than
        # leaving a mistake in charge.
        "prev_layout_json": "TEXT DEFAULT ''",
        "prev_source_analysis_id": "TEXT DEFAULT ''",
        "prev_updated_at": "TEXT DEFAULT ''",
        "prev_updated_by": "TEXT DEFAULT ''",
        "prev_pdef_version": "TEXT DEFAULT ''",
        "prev_algo_version": "TEXT DEFAULT ''",
    },
}

LABEL_FIELDS = ("site", "phantom", "operator", "notes")

#: Each exposure column and the DICOM keyword it is copied from (the same
#: keywords as ingest.EXPOSURE_TAGS). Nothing is judged from these values yet;
#: they are shown so an operator can see at once that an exposure was off.
EXPOSURE_FIELDS = (("exposure_index", "ExposureIndex"),
                   ("target_exposure_index", "TargetExposureIndex"),
                   ("deviation_index", "DeviationIndex"),
                   ("sensitivity", "Sensitivity"))

#: How many measuring-point states are kept per analysis. seq 0 (the untouched
#: automatic proposal) is pinned and never trimmed, so "reset to auto-detected"
#: keeps working no matter how many edits have been made since.
GEOMETRY_HISTORY_DEPTH = 30

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

    @staticmethod
    def _add_column(conn, table: str, col: str, decl: str) -> bool:
        """Add one column; False if another process got there first.

        Every gunicorn worker opens the store at start-up, and on the first
        start after an upgrade they all read the old column list at the same
        moment. The one that loses the race used to die on "duplicate column
        name" — and a worker that fails to boot takes the server down with it.
        A column that already exists is exactly the state this wanted."""
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            return True
        except sqlite3.OperationalError as exc:
            if "duplicate column name" in str(exc).lower():
                return False
            raise

    @staticmethod
    def _state_done(conn, key: str) -> bool:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_state ("
                     " key TEXT PRIMARY KEY, done_at TEXT NOT NULL)")
        return conn.execute("SELECT 1 FROM schema_state WHERE key=?",
                            (key,)).fetchone() is not None

    @staticmethod
    def _mark_done(conn, key: str):
        conn.execute("INSERT OR IGNORE INTO schema_state (key, done_at)"
                     " VALUES (?, ?)", (key, time.strftime("%Y-%m-%d %H:%M:%S")))

    def _migrate(self, conn):
        for table, cols in _ADDED_COLUMNS.items():
            have = {r["name"] for r in
                    conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if not have:
                # The table does not exist on this database at all. _SCHEMA has
                # already run, so this can only mean an older layout we do not
                # migrate column-by-column; leave it alone.
                continue
            added = [col for col, decl in cols.items()
                     if col not in have
                     and self._add_column(conn, table, col, decl)]
            # Deliberately tied to the moment the column appears, and to the
            # worker that added it. It reconstructs which records were treated
            # as finished from what their audit trails say; run again later, it
            # would re-finalise records an operator has since reopened with a
            # re-run, whose trail still carries the old "finalized" line.
            if table == "analyses" and "finalized_at" in added:
                self._backfill_finalized(conn)
        # Unlike the finalised stamp this only ever fills what is empty, so it
        # is keyed on having COMPLETED rather than on who added the columns: a
        # worker that died half way through, or lost the race to add them,
        # would otherwise leave every older record "not recorded" for good.
        if not self._state_done(conn, "exposure_backfill"):
            self._backfill_exposure(conn)
            self._mark_done(conn, "exposure_backfill")

    @staticmethod
    def _backfill_finalized(conn):
        """Decide, once, which existing records count as finalised.

        Pressing Finalize never wrote a column, so on an upgraded database the
        answer has to be reconstructed. Three things mean an operator treated a
        record as done, in descending order of how much they committed to it:
        a validation ruling, the reference-scan flag, or a "finalized" line in
        the record's own audit trail — which carries the moment it happened, so
        the real timestamp is recovered rather than invented.

        Everything else stays un-finalised deliberately. Getting this wrong in
        that direction merely leaves a record easy to discard; the other way
        would lock the field test's own clutter behind an administrator
        password, which is exactly what this work exists to avoid.

        Runs only in the branch that has just added the column, so it cannot
        re-run and cannot touch a record finalised since.
        """
        rows = conn.execute(
            "SELECT id, created_at, validated_at, validation_status,"
            " is_baseline, audit_json FROM analyses").fetchall()
        done = 0
        for r in rows:
            when = ""
            for entry in _safe_trail(r["audit_json"]):
                if entry.get("action") == "finalized" and entry.get("ts"):
                    when = str(entry["ts"])          # keep the last one
            if not when and r["validation_status"]:
                when = r["validated_at"] or r["created_at"] or ""
            if not when and r["is_baseline"]:
                when = r["created_at"] or ""
            if not when:
                continue
            conn.execute(
                "UPDATE analyses SET finalized_at=?, finalized_by=?"
                " WHERE id=?", (when, "migration", r["id"]))
            done += 1
        if done:
            log.info("schema upgrade: %d existing analyses marked finalised "
                     "(ruling, reference flag, or a finalize in the trail)",
                     done)

    def _backfill_exposure(self, conn) -> dict:
        """Give records stored before the exposure values were kept those
        values, read again from each record's own stored file.

        Unlike the finalised stamp, nothing has to be reconstructed: the values
        were in the file all along and simply were not transcribed. They are
        read from the header of the exact upload the record was made from, and
        from nothing else — stopping before the pixels keeps it to milliseconds
        a record, where decoding the image would take seconds.

        Strictly additive. It adds the keys meta_json lacks and fills the
        exposure columns, and writes nothing else: not results, status,
        geometry, validation, or any protection. That is why it may touch a
        finalised or signed record — what was signed is the measurements, and
        this transcribes more of the same file's header, which the record
        already holds as its own. A key already present is never overwritten,
        and the write is refused if meta_json changed after it was read.

        A missing or unreadable file is logged and skipped: that record shows
        "not recorded", which is no worse than before. Runs at start-up until
        it has completed once on this database (see _migrate); running it again
        changes nothing, because it only ever fills what is empty.
        """
        from . import ingest

        # Commit whatever an earlier step of this same upgrade wrote, so the
        # file reads below happen without holding the write lock that the
        # other workers starting at the same moment would queue behind.
        conn.commit()
        columns = [col for col, _ in EXPOSURE_FIELDS]
        rows = conn.execute(
            f"SELECT id, kind, meta_json, {', '.join(columns)}"
            " FROM analyses").fetchall()
        plans, missing, unreadable = [], [], 0
        for r in rows:
            raw = r["meta_json"]
            meta = _safe_json(raw) if raw else {}
            if not isinstance(meta, dict):
                # Left for the integrity tooling to find: rewriting a damaged
                # blob here would bury the damage under a valid-looking one.
                log.warning("exposure backfill: analysis=%s has an unreadable "
                            "stored header — left as it is", r["id"])
                continue
            added = {}
            already = any(tag in meta for _, tag in EXPOSURE_FIELDS)
            if not already and r["kind"] != "image":
                path = self.upload_path(r["id"])
                if not os.path.exists(path):
                    missing.append(r["id"])
                else:
                    try:
                        found = ingest.read_exposure_header(path)
                    except Exception as exc:
                        unreadable += 1
                        log.warning("exposure backfill: the stored file of "
                                    "analysis=%s could not be read (%s) — "
                                    "skipped", r["id"], exc)
                        found = {}
                    added = {k: v for k, v in found.items() if k not in meta}
            values = exposure_columns({**meta, **added})
            if not added and values == [r[col] for col in columns]:
                continue                  # nothing new: write nothing
            plans.append((r["id"], raw,
                          json.dumps({**meta, **added}) if added else raw,
                          values))
        updated = 0
        for aid, raw, new_raw, values in plans:
            updated += conn.execute(
                "UPDATE analyses SET meta_json=?, exposure_index=?,"
                " target_exposure_index=?, deviation_index=?, sensitivity=?"
                " WHERE id=? AND meta_json IS ?",
                (new_raw, *values, aid, raw)).rowcount
        if missing:
            log.warning("exposure backfill: %d analyses have no stored file "
                        "and keep no exposure values (%s%s)", len(missing),
                        ", ".join(missing[:10]),
                        ", …" if len(missing) > 10 else "")
        if updated or unreadable:
            log.info("schema upgrade: exposure values filled in on %d existing "
                     "analyses from their stored files (%d unreadable)",
                     updated, unreadable)
        return {"updated": updated, "missing": len(missing),
                "unreadable": unreadable}

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

    # ------------------------------------------- measuring-point undo history

    # Snapshots are stored whole and compressed rather than as deltas. A single
    # low-contrast block placement rewrites 25 ROIs at once and a re-propose
    # rewrites everything, so a delta scheme would have to encode the whole
    # subtree anyway; ~120 kB of geometry compresses to ~50 kB, and the depth
    # cap bounds the cost per analysis.

    @staticmethod
    def _pack(geometry) -> bytes:
        return zlib.compress(json.dumps(geometry).encode("utf-8"), 6)

    @staticmethod
    def _unpack(blob):
        if blob is None:
            return None
        return json.loads(zlib.decompress(blob).decode("utf-8"))

    @staticmethod
    def _loads_geometry(raw, aid: str):
        """The live geometry blob, or None when it is missing OR corrupted.

        The edit callbacks already answer 'no geometry yet' for None, which is
        the right degradation for corruption too - the alternative was a 500
        on every Stage C interaction with that analysis."""
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            log.error("corrupted geometry_json for analysis=%s", aid)
            return None
        if not isinstance(value, dict):
            log.error("geometry_json for analysis=%s is not an object", aid)
            return None
        return value

    @classmethod
    def _unpack_or_none(cls, blob, what: str):
        """A snapshot that no longer decompresses is corruption on disk.
        Callers treat it as absent rather than exploding — losing one undo
        step is recoverable, a 500 on every undo click is not."""
        try:
            return cls._unpack(blob)
        except (zlib.error, json.JSONDecodeError, UnicodeDecodeError, TypeError):
            log.error("corrupted geometry snapshot (%s) — treating as absent",
                      what)
            return None

    def _hist_state(self, c, aid: str) -> dict:
        """Cursor position and how far it can move, from an open connection."""
        row = c.execute(
            "SELECT COALESCE(geometry_seq, 0) AS seq FROM analyses WHERE id=?",
            (aid,)).fetchone()
        if row is None:
            raise KeyError(aid)
        seq = int(row["seq"] or 0)
        back = c.execute(
            "SELECT COUNT(*) FROM geometry_history WHERE analysis_id=? AND seq<?",
            (aid, seq)).fetchone()[0]
        fwd = c.execute(
            "SELECT COUNT(*) FROM geometry_history WHERE analysis_id=? AND seq>?",
            (aid, seq)).fetchone()[0]
        has_base = c.execute(
            "SELECT COUNT(*) FROM geometry_history WHERE analysis_id=? AND seq=0",
            (aid,)).fetchone()[0]
        # 'has_auto_proposal', not 'has_baseline': this dict travels in the
        # same API payload as is_baseline (the reference SCAN), and two
        # unrelated meanings of 'baseline' side by side invited wiring the
        # undo anchor to the reference star.
        return {"seq": seq, "undo_depth": int(back), "redo_depth": int(fwd),
                "has_auto_proposal": bool(has_base)}

    def _push_state(self, c, aid: str, geometry, *, action: str,
                    user: str = "", detail=None) -> dict:
        """Append one state after the cursor and move the cursor onto it."""
        state = self._hist_state(c, aid)
        seq = state["seq"]
        if not state["has_auto_proposal"]:
            # A record written before this feature existed, or one whose
            # baseline was trimmed: seed seq 0 from whatever geometry it holds
            # so the very first edit still has something to undo back to.
            row = c.execute("SELECT geometry_json FROM analyses WHERE id=?",
                            (aid,)).fetchone()
            base = self._loads_geometry(row["geometry_json"], aid) if row \
                else None
            c.execute(
                "INSERT OR REPLACE INTO geometry_history"
                " (analysis_id, seq, created_at, user, action, detail_json,"
                "  geometry_z) VALUES (?,0,?,?,?,?,?)",
                (aid, time.strftime("%Y-%m-%d %H:%M:%S"), user, "baseline",
                 None, self._pack(base)))
            seq = 0
        # A new edit discards whatever was redoable — the standard rule.
        c.execute("DELETE FROM geometry_history WHERE analysis_id=? AND seq>?",
                  (aid, seq))
        nxt = seq + 1
        c.execute(
            "INSERT INTO geometry_history (analysis_id, seq, created_at, user,"
            " action, detail_json, geometry_z) VALUES (?,?,?,?,?,?,?)",
            (aid, nxt, time.strftime("%Y-%m-%d %H:%M:%S"), user, action,
             json.dumps(detail) if detail is not None else None,
             self._pack(geometry)))
        # Trim the oldest edits but never seq 0 — it is the reset-to-auto target.
        c.execute(
            "DELETE FROM geometry_history WHERE analysis_id=? AND seq>0"
            " AND seq<=?", (aid, nxt - GEOMETRY_HISTORY_DEPTH))
        return nxt

    @staticmethod
    def _write_geometry(c, aid: str, geometry, seq: int,
                        invalidate_results: bool):
        """Store the geometry and, when asked, drop results computed from the
        geometry that just changed.

        Leaving them would show a report whose overlay comes from the new ROIs
        and whose numbers come from the old ones."""
        if invalidate_results:
            c.execute(
                "UPDATE analyses SET geometry_json=?, geometry_seq=?,"
                " results_json=NULL, status='draft' WHERE id=?",
                (json.dumps(geometry), seq, aid))
        else:
            c.execute(
                "UPDATE analyses SET geometry_json=?, geometry_seq=? WHERE id=?",
                (json.dumps(geometry), seq, aid))

    def geometry_state(self, aid: str) -> dict:
        with self._conn() as c:
            return self._hist_state(c, aid)

    def clear_geometry_history(self, aid: str):
        """Throw the undo stack away — the states no longer describe anything.

        Used after a re-registration: every snapshot holds pixel coordinates
        derived from a transform that has been replaced."""
        with self._conn() as c:
            c.execute("DELETE FROM geometry_history WHERE analysis_id=?", (aid,))

    def set_geometry_baseline(self, aid: str, geometry, *, action: str = "propose",
                              user: str = "", detail=None,
                              invalidate_results: bool = True) -> dict:
        """Install a fresh automatic proposal as the new seq-0 baseline.

        Everything the user had done before is dropped along with the history:
        those states were built against a proposal that no longer exists.

        ``invalidate_results`` is False only for a caller that has just
        computed results FROM the geometry it is installing — a full
        re-analysis. There the two already agree, so blanking the results would
        destroy exactly the work that was done."""
        with self.write_transaction() as c:
            if c.execute("SELECT 1 FROM analyses WHERE id=?", (aid,)).fetchone() is None:
                raise KeyError(aid)
            c.execute("DELETE FROM geometry_history WHERE analysis_id=?", (aid,))
            c.execute(
                "INSERT INTO geometry_history (analysis_id, seq, created_at,"
                " user, action, detail_json, geometry_z) VALUES (?,0,?,?,?,?,?)",
                (aid, time.strftime("%Y-%m-%d %H:%M:%S"), user, action,
                 json.dumps(detail) if detail is not None else None,
                 self._pack(geometry)))
            self._write_geometry(c, aid, geometry, 0, invalidate_results)
            return self._hist_state(c, aid)

    def mutate_geometry(self, aid: str, fn, *, action: str, user: str = "",
                        detail=None, invalidate_results: bool = True,
                        expect_seq: int | None = None):
        """Edit the geometry blob and record an undo state, in ONE transaction.

        ``fn`` receives the decoded geometry, mutates it in place and may return
        a value which is handed back to the caller. BEGIN IMMEDIATE serialises
        two overlapping edits instead of letting the later one silently discard
        the earlier — the "the point did not register" failure.

        ``fn`` must not call any other Store method: that would open a second
        connection which, in WAL mode, reads the pre-transaction snapshot and
        blocks on the write lock.

        ``expect_seq`` is the state the caller believes it is editing. Checked
        INSIDE the transaction, so a concurrent edit cannot slip between the
        check and the write. Omit it to overwrite whatever is there, which is
        what re-analysis and the command line want."""
        with self.write_transaction() as c:
            row = c.execute(
                "SELECT geometry_json, COALESCE(geometry_seq, 0) AS seq"
                " FROM analyses WHERE id=?", (aid,)).fetchone()
            if row is None:
                raise KeyError(aid)
            if expect_seq is not None and int(row["seq"]) != int(expect_seq):
                raise StaleGeometry(int(expect_seq), int(row["seq"]))
            geometry = self._loads_geometry(row["geometry_json"], aid)
            out = fn(geometry)
            seq = self._push_state(c, aid, geometry, action=action, user=user,
                                   detail=detail)
            self._write_geometry(c, aid, geometry, seq, invalidate_results)
            return out, self._hist_state(c, aid)

    def replace_geometry(self, aid: str, geometry, *, action: str,
                         user: str = "", detail=None) -> dict:
        """Append a wholly new geometry state (a reset) as an undoable edit."""
        with self.write_transaction() as c:
            if c.execute("SELECT 1 FROM analyses WHERE id=?", (aid,)).fetchone() is None:
                raise KeyError(aid)
            seq = self._push_state(c, aid, geometry, action=action, user=user,
                                   detail=detail)
            self._write_geometry(c, aid, geometry, seq, True)
            return self._hist_state(c, aid)

    def geometry_at(self, aid: str, seq: int):
        with self._conn() as c:
            row = c.execute(
                "SELECT geometry_z FROM geometry_history"
                " WHERE analysis_id=? AND seq=?", (aid, seq)).fetchone()
        if row is None:
            return None
        return self._unpack_or_none(row["geometry_z"], f"{aid} seq={seq}")

    def _step_geometry(self, aid: str, direction: int) -> dict:
        """Move the cursor to the NEAREST SURVIVING state and restore it.

        Nearest rather than exactly one back, because the depth trim leaves a
        hole: it removes the oldest edits but never seq 0, so after a long
        session the states run 0, then 35, 36, … Stepping by exactly one would
        stall at 35 with the Undo button still offering a step that could never
        be taken, and the pinned automatic proposal would be unreachable by
        undo. Counting rows either side of the cursor — which is what
        undo_depth and redo_depth report — then means exactly the number of
        steps that can really be taken.

        Both ends run inside the transaction that read the cursor, so a
        double-clicked Undo cannot step twice off one reading."""
        with self.write_transaction() as c:
            state = self._hist_state(c, aid)
            if direction < 0:
                row = c.execute(
                    "SELECT seq, geometry_z, action FROM geometry_history"
                    " WHERE analysis_id=? AND seq<? ORDER BY seq DESC LIMIT 1",
                    (aid, state["seq"])).fetchone()
            else:
                row = c.execute(
                    "SELECT seq, geometry_z, action FROM geometry_history"
                    " WHERE analysis_id=? AND seq>? ORDER BY seq ASC LIMIT 1",
                    (aid, state["seq"])).fetchone()
            if row is None:
                raise LookupError("nothing to undo" if direction < 0
                                  else "nothing to redo")
            geometry = self._unpack_or_none(
                row["geometry_z"], f"{aid} seq={row['seq']}")
            if geometry is None:
                raise LookupError(
                    "that stored state is corrupted and cannot be restored — "
                    "the current measuring points are unaffected")
            self._write_geometry(c, aid, geometry, row["seq"], True)
            out = self._hist_state(c, aid)
        out["geometry"] = geometry
        out["action"] = row["action"]
        return out

    def undo_geometry(self, aid: str) -> dict:
        return self._step_geometry(aid, -1)

    def redo_geometry(self, aid: str) -> dict:
        return self._step_geometry(aid, +1)

    # ------------------------------------------------- per-phantom layouts

    @staticmethod
    def profile_key(phantom: str) -> str:
        """The exact stored label, trimmed.

        Deliberately NOT case-folded: History, the filters and the trends group
        on the raw value under SQLite's binary collation, so 'MSF-01' and
        'msf-01' are two different phantoms there. A folded profile key would
        serve both buckets and break the delete-when-empty rule."""
        return str(phantom or "").strip()

    @staticmethod
    def profile_norm(phantom: str) -> str:
        """Loose form used ONLY to warn about near-duplicate labels."""
        return " ".join(str(phantom or "").lower().split())

    def _prune_profile(self, c, label: str) -> bool:
        """Drop a stored layout once no analysis carries its label any more."""
        label = self.profile_key(label)
        if not label:
            return False
        n = c.execute("SELECT COUNT(*) FROM analyses WHERE phantom=?",
                      (label,)).fetchone()[0]
        if n:
            return False
        return c.execute("DELETE FROM phantom_profiles WHERE phantom_key=?",
                         (label,)).rowcount > 0

    def _unwind_profile(self, c, label: str, aid: str) -> str:
        """Undo a layout that the analysis being removed had stored.

        The layout is the phantom's shared default — the marks every later scan
        of it starts from. Leaving one behind that came from an analysis
        somebody has just thrown away means the mistake outlives the record,
        silently, on everyone else's scans. The field audit log shows exactly
        this happening: one phantom's layout rewritten six times in twelve
        minutes, three of those from exposures nothing could be measured in.

        Returns "restored", "deleted" or "" for what happened.
        """
        label = self.profile_key(label)
        if not label or not aid:
            return ""
        row = c.execute(
            "SELECT source_analysis_id, COALESCE(prev_layout_json,'') AS prev,"
            " COALESCE(prev_source_analysis_id,'') AS prev_src,"
            " COALESCE(prev_updated_at,'') AS prev_at,"
            " COALESCE(prev_updated_by,'') AS prev_by,"
            " COALESCE(prev_pdef_version,'') AS prev_pdef,"
            " COALESCE(prev_algo_version,'') AS prev_algo"
            " FROM phantom_profiles WHERE phantom_key=?", (label,)).fetchone()
        if row is None or row["source_analysis_id"] != aid:
            return ""                      # not this analysis's doing
        # Only restore a predecessor that is not the record being removed.
        if row["prev"] and row["prev_src"] != aid:
            c.execute(
                "UPDATE phantom_profiles SET layout_json=?,"
                " source_analysis_id=?, updated_at=?, updated_by=?,"
                " pdef_version=?, algo_version=?, prev_layout_json='',"
                " prev_source_analysis_id='', prev_updated_at='',"
                " prev_updated_by='', prev_pdef_version='',"
                " prev_algo_version='' WHERE phantom_key=?",
                (row["prev"], row["prev_src"], row["prev_at"], row["prev_by"],
                 row["prev_pdef"], row["prev_algo"], label))
            return "restored"
        # Nothing to fall back to: the next scan starts from detection again,
        # which is the right default and better than a layout nobody trusts.
        c.execute("DELETE FROM phantom_profiles WHERE phantom_key=?", (label,))
        return "deleted"

    def get_phantom_profile(self, phantom: str) -> dict | None:
        key = self.profile_key(phantom)
        if not key:
            return None
        with self._conn() as c:
            row = c.execute("SELECT * FROM phantom_profiles WHERE phantom_key=?",
                            (key,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["layout"] = json.loads(d.pop("layout_json") or "{}")
            if not isinstance(d["layout"], dict):
                raise TypeError("layout is not an object")
        except (json.JSONDecodeError, TypeError):
            # A corrupted stored layout must degrade to "no layout": the scan
            # then runs on its own detection, which is always a valid answer.
            log.error("corrupted stored layout for phantom=%r — ignoring it",
                      d.get("phantom_key"))
            return None
        return d

    def save_phantom_profile(self, phantom: str, layout: dict, *,
                             pdef_version: str = "", algo_version: str = "",
                             source_analysis_id: str = "",
                             updated_by: str = "",
                             replace_others: bool = True) -> dict | None:
        """Store `layout` as the phantom's shared measuring points.

        With replace_others=False a layout that a DIFFERENT analysis stored is
        left exactly as it is and None comes back. That is the rule for a
        caller who never said whether to replace it — a page cached from
        before the operator was asked — and it is decided here, inside the
        write lock, because two such confirms of one phantom arriving together
        would otherwise both read "nothing stored yet" and the second would
        quietly replace the first."""
        key = self.profile_key(phantom)
        if not key:
            raise ValueError("a phantom label is required to store a layout")
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        with self.write_transaction() as c:
            # Rotate the current layout into the previous slot, but only when
            # it came from a DIFFERENT analysis. Re-confirming step C on the
            # same one — which operators do repeatedly while adjusting marks —
            # would otherwise overwrite the previous layout with itself, and
            # the history worth keeping would be gone after the first nudge.
            cur = c.execute(
                "SELECT layout_json, source_analysis_id, updated_at,"
                " updated_by, pdef_version, algo_version FROM phantom_profiles"
                " WHERE phantom_key=?", (key,)).fetchone()
            if (not replace_others and cur is not None
                    and cur["source_analysis_id"] != source_analysis_id):
                return None
            if cur is not None and cur["source_analysis_id"] != source_analysis_id:
                c.execute(
                    "UPDATE phantom_profiles SET prev_layout_json=?,"
                    " prev_source_analysis_id=?, prev_updated_at=?,"
                    " prev_updated_by=?, prev_pdef_version=?,"
                    " prev_algo_version=? WHERE phantom_key=?",
                    (cur["layout_json"], cur["source_analysis_id"],
                     cur["updated_at"], cur["updated_by"],
                     cur["pdef_version"], cur["algo_version"], key))
            c.execute(
                "INSERT INTO phantom_profiles (phantom_key, phantom_norm,"
                " layout_json, pdef_version, algo_version, source_analysis_id,"
                " created_at, updated_at, updated_by)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(phantom_key) DO UPDATE SET"
                "   phantom_norm=excluded.phantom_norm,"
                "   layout_json=excluded.layout_json,"
                "   pdef_version=excluded.pdef_version,"
                "   algo_version=excluded.algo_version,"
                "   source_analysis_id=excluded.source_analysis_id,"
                "   updated_at=excluded.updated_at,"
                "   updated_by=excluded.updated_by",
                (key, self.profile_norm(key), json.dumps(layout), pdef_version,
                 algo_version, source_analysis_id, now, now, updated_by))
            near = [r["phantom_key"] for r in c.execute(
                "SELECT phantom_key FROM phantom_profiles"
                " WHERE phantom_norm=? AND phantom_key<>?",
                (self.profile_norm(key), key)).fetchall()]
        return {"phantom": key, "updated_at": now, "updated_by": updated_by,
                "n_rois": len((layout or {}).get("rois") or {}),
                "near_miss": near}

    def delete_phantom_profile(self, phantom: str) -> bool:
        key = self.profile_key(phantom)
        if not key:
            return False
        with self._conn() as c:
            return c.execute("DELETE FROM phantom_profiles WHERE phantom_key=?",
                             (key,)).rowcount > 0

    def list_phantom_profiles(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT p.phantom_key, p.updated_at, p.updated_by,"
                " p.pdef_version, p.algo_version, p.source_analysis_id,"
                " LENGTH(p.layout_json) AS layout_bytes,"
                " (SELECT COUNT(*) FROM analyses a WHERE a.phantom=p.phantom_key)"
                "   AS n_analyses"
                " FROM phantom_profiles p ORDER BY p.phantom_key").fetchall()
        # both spellings: phantom_key is the stored column, but every sibling
        # payload (and the forget endpoint's request body) says 'phantom'
        return [{**dict(r), "phantom": r["phantom_key"]} for r in rows]

    def count_for_phantom(self, phantom: str) -> int:
        key = self.profile_key(phantom)
        if not key:
            return 0
        with self._conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM analyses WHERE phantom=?",
                                 (key,)).fetchone()[0])

    def find_by_sha256(self, sha256: str, exclude_id: str | None = None):
        """Analyses already holding this exact file.

        Returns everything the refusal has to show, because the operator's real
        question is never "is this a duplicate" — it is "is the thing you
        already have the thing I meant to send, and what became of it". The
        field test produced eight refusals in seven weeks and not one of them
        was a false positive; what they lacked was the answer to that question,
        so the operator re-sent the file under a new name to find out.

        The size comes from the stored copy rather than a column: it is what is
        actually on disk, so a record whose file went missing shows no size
        instead of a confident wrong one."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, created_at, acquired_at, source_name, site, phantom,"
                " operator, stage, status, validation_status, is_baseline,"
                " finalized_at FROM analyses"
                " WHERE sha256=? AND id != ? ORDER BY created_at",
                (sha256, exclude_id or "")).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["bytes"] = os.path.getsize(self.upload_path(d["id"]))
            except OSError:
                d["bytes"] = None
            out.append(d)
        return out

    def find_by_sop_uid(self, uid: str, exclude_id: str | None = None):
        """Records made from the same exposure, whatever wrapper it arrived in.

        A scan re-exported from the archive — recompressed, or with the header
        rewritten — is a different file with a different hash, so the duplicate
        check lets it through and the same exposure is counted twice in every
        trend. The SOP Instance UID is the one identifier that survives the
        re-export, because it names the exposure rather than the file.

        This only ever produces a remark: a UID can legitimately repeat when a
        detector is misconfigured, and refusing the upload over it would block
        real work to prevent a bookkeeping error. The caller says so and lets
        the operator decide.

        Matched with LIKE against the stored header rather than a column, so it
        works on records made before this existed. The quotes and the field
        name are part of the pattern, so the UID cannot match some other
        field's value that merely contains the same digits."""
        uid = str(uid or "").strip()
        # A UID is digits and dots. Anything else cannot be one, and would
        # only risk smuggling LIKE wildcards into the pattern.
        if not uid or not all(c.isdigit() or c == "." for c in uid):
            return []
        needle = '%"SOPInstanceUID": "' + uid + '"%'
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, created_at, acquired_at, source_name, site, phantom,"
                " operator, stage, status, validation_status, is_baseline,"
                " finalized_at FROM analyses"
                " WHERE meta_json LIKE ? AND id != ? ORDER BY created_at",
                (needle, exclude_id or "")).fetchall()
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
        # The acquisition time comes from the scan itself; it is what the trend
        # axis should use, not the moment the file happened to be uploaded.
        #
        # When the header carries no usable date — a plain image, or a detector
        # whose clock was reset — this stays EMPTY on purpose. Filling it with
        # the upload clock (which is what this used to do) made "when the scan
        # was taken" and "when it was uploaded" indistinguishable afterwards,
        # which is exactly what the separate upload column exists to fix.
        # Everything downstream already falls back to created_at for ordering.
        acquired = _acquired_at(scan.meta)
        with self._conn() as c:
            c.execute(
                "INSERT INTO analyses (id, created_at, source_name, sha256, kind,"
                " reduced_precision, signature, meta_json, stage, audit_json,"
                " algo_version, pdef_version, status, is_baseline,"
                " site, phantom, operator, notes, acquired_at,"
                " exposure_index, target_exposure_index, deviation_index,"
                " sensitivity)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?)",
                (aid, time.strftime("%Y-%m-%d %H:%M:%S"), scan.source_name,
                 scan.sha256, scan.kind, int(scan.reduced_precision), signature,
                 json.dumps(scan.meta), "A", json.dumps([]),
                 algo_version, pdef_version, "draft",
                 lab["site"], lab["phantom"], lab["operator"], lab["notes"],
                 acquired, *exposure_columns(scan.meta)))
        return aid

    #: The four blobs a record carries that the collection views never read.
    #: geometry_json alone is ~120 kB per analysis.
    _HEAVY_JSON = ("meta_json", "reg_json", "geometry_json", "audit_json")

    def get_slim(self, ids: list[str]) -> list[dict]:
        """Many records at once, without the blobs the collection views skip.

        Trends, the CSV exports and the comparison report used to call get()
        once PER ROW, deserialising a quarter-megabyte of JSON each time to
        read a few labels and the results — at 300 analyses that was seconds
        of pure parsing per request, multiplied by every operator with the
        History tab open. One query, results only, order preserved.

        The skipped fields are present as None, so a consumer that does stray
        onto one degrades exactly like a record whose blob is corrupted."""
        if not ids:
            return []
        with self._conn() as c:
            cols = [r["name"] for r in
                    c.execute("PRAGMA table_info(analyses)").fetchall()
                    if r["name"] not in self._HEAVY_JSON]
            by_id: dict[str, dict] = {}
            CHUNK = 400                     # stay far below SQLite's 999 limit
            for i in range(0, len(ids), CHUNK):
                chunk = ids[i:i + CHUNK]
                marks = ",".join("?" for _ in chunk)
                for row in c.execute(
                        f"SELECT {', '.join(cols)} FROM analyses"
                        f" WHERE id IN ({marks})", chunk).fetchall():
                    d = dict(row)
                    raw = d.pop("results_json", None)
                    try:
                        value = json.loads(raw) if raw else None
                        if value is not None and not isinstance(value, dict):
                            raise TypeError("expected dict")
                        d["results"] = value
                    except (json.JSONDecodeError, TypeError):
                        log.error("corrupted results_json for analysis=%s — "
                                  "treating as absent", d.get("id"))
                        d["results"] = None
                    for k in ("meta", "reg", "geometry", "audit"):
                        d[k] = None
                    by_id[d["id"]] = d
        return [by_id[a] for a in ids if a in by_id]

    def exists(self, aid: str) -> bool:
        """One indexed SELECT - for cache-validation paths where get() would
        pointlessly deserialise a quarter-megabyte of JSON."""
        with self._conn() as c:
            return c.execute("SELECT 1 FROM analyses WHERE id=?",
                             (aid,)).fetchone() is not None

    def upload_path(self, aid: str) -> str:
        return os.path.join(self.root, "data", "uploads", f"{aid}.bin")

    def thumbs_dir(self, aid: str) -> str | None:
        """Where the comparison report keeps this analysis's pictures.

        Derived data: removed with the analysis, and safe to delete at any
        time — it is rendered again when next asked for. None for an id that
        is not a plain token, because the directory is removed wholesale and a
        path built from '..' must never reach that."""
        if not aid or not all(c.isascii() and (c.isalnum() or c in "-_")
                              for c in aid):
            return None
        return os.path.join(self.root, "data", "thumbs", aid)

    def get(self, aid: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM analyses WHERE id=?", (aid,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        for k in ("meta_json", "reg_json", "geometry_json", "results_json",
                  "audit_json", "quality_json"):
            raw = d.pop(k)
            if not raw:
                d[k[:-5]] = None
                continue
            try:
                value = json.loads(raw)
                # A decoded blob of the wrong shape (a bare string, a list
                # where a dict belongs) crashes every consumer just as surely
                # as one that does not decode.
                want = list if k == "audit_json" else dict
                if value is not None and not isinstance(value, want):
                    raise TypeError(f"expected {want.__name__}")
                d[k[:-5]] = value
            except (json.JSONDecodeError, TypeError):
                # Disk corruption or a botched restore. One damaged blob must
                # not make the whole record unreachable through every endpoint
                # that reads it — the report and the exports degrade instead,
                # and the integrity check is the tool for diagnosing it.
                log.error("corrupted %s for analysis=%s — treating as absent",
                          k, aid)
                d[k[:-5]] = None
        return d

    def update(self, aid: str, **fields):
        # The phantom label owns a stored measuring-point layout, and changing
        # it has to prune the old one. Routing every change through set_labels
        # is what keeps a renamed phantom from leaving an orphan profile that
        # would later be replayed onto an unrelated scan.
        if "phantom" in fields:
            raise ValueError(
                "use set_labels() to change the phantom label, so the stored "
                "measuring-point layout stays consistent")
        cols, vals = [], []
        for k, v in fields.items():
            if k in ("meta", "reg", "geometry", "results", "audit", "quality"):
                cols.append(f"{k}_json=?")
                # None must become SQL NULL: json.dumps(None) is the text
                # 'null', which passes every IS NOT NULL filter, so a
                # re-registered analysis still counted as completed.
                vals.append(json.dumps(v) if v is not None else None)
            else:
                cols.append(f"{k}=?")
                vals.append(v)
        vals.append(aid)
        with self._conn() as c:
            c.execute(f"UPDATE analyses SET {', '.join(cols)} WHERE id=?", vals)

    def audit(self, aid: str, stage: str, action: str, detail=None):
        """Append one entry to the record's own audit trail.

        Reads ONLY the trail column — this runs on every edit, and it used to
        deserialise the whole record (a quarter-megabyte of JSON) to append
        one line. The append runs under the write lock, because two concurrent
        edits both doing read-append-write outside it could lose one
        another's line even though the edits themselves were serialised."""
        with self.write_transaction() as c:
            row = c.execute("SELECT audit_json FROM analyses WHERE id=?",
                            (aid,)).fetchone()
            if row is None:
                # Deleted between a colleague's edit and this note about it.
                # The race is legitimate — the edit answered before the
                # delete — and there is no record left for the note to live
                # on, so dropping it beats a 500 on work that succeeded.
                log.info("audit note dropped — analysis %s was deleted "
                         "first (%s)", aid, action)
                return
            try:
                trail = json.loads(row["audit_json"]) if row["audit_json"] else []
            except (json.JSONDecodeError, TypeError):
                log.error("corrupted audit_json for analysis=%s — starting a "
                          "fresh trail", aid)
                trail = []
            if not isinstance(trail, list):
                trail = []
            trail.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                          "stage": stage, "action": action, "detail": detail})
            c.execute("UPDATE analyses SET audit_json=? WHERE id=?",
                      (json.dumps(trail), aid))

    def list_all(self, site: str | None = None, phantom: str | None = None,
                 signature: str | None = None, validation: str | None = None,
                 completed_only: bool = False, unfinished_only: bool = False,
                 limit: int | None = None,
                 order_by: str = "acquired") -> list[dict]:
        sql = ("SELECT id, created_at, acquired_at, source_name, signature,"
               " stage, status, is_baseline, sha256, reduced_precision, sid_mm,"
               " site, phantom, operator, notes,"
               " validation_status, validated_by, validation_comment,"
               " validated_at, quality_verdict, finalized_at, finalized_by,"
               # Two numbers, not the whole exposure record: they are what
               # the row shows, and this listing is fetched over slow links.
               " exposure_index, deviation_index,"
               # Whether there are results, not the results: History offers
               # "re-run" only on a row that has something to re-run.
               " results_json IS NOT NULL AS has_results"
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
        if unfinished_only:
            # Started but never measured — the records an operator is offered
            # to continue, and the ones that pile up after an interrupted
            # session. Always ordered by upload time: what matters is which
            # attempt was abandoned most recently, not when it was exposed.
            sql += " AND results_json IS NULL"
            order_by = "uploaded"
        # "acquired" still falls back to the upload time for rows with no usable
        # header date, so the ordering never collapses; "uploaded" is the order
        # to use when a detector's clock is suspect.
        sql += (" ORDER BY created_at DESC" if order_by == "uploaded"
                else " ORDER BY COALESCE(NULLIF(acquired_at,''), created_at) DESC")
        if limit:
            sql += " LIMIT ?"
            args.append(int(limit))
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["acquired_flag"] = acquisition_flag(d)
            d["has_results"] = bool(d["has_results"])
            out.append(d)
        return out

    def result_statuses(self, ids: list[str],
                        fields: list[tuple[str, str]]) -> dict[str, dict]:
        """A few per-test statuses of many analyses, without their results.

        History names what its verdict left out ("X-ray field alignment not
        checked"), and that is written in the per-test statuses inside the
        results — about 130 kB per analysis, which the listing otherwise never
        reads. SQLite picks the requested ``(test, key)`` statuses out itself,
        one parse per row, and only those few words reach Python, as
        ``{id: {test: {key: status}}}``.

        A results blob that is not valid JSON reads as having no statuses
        rather than failing the listing, and so does an SQLite built without
        its JSON functions: the note is a courtesy, History is not."""
        out: dict[str, dict] = {}
        if not ids or not fields:
            return out
        paths = ", ".join("?" for _ in fields)
        path_args = [f"$.{test}.{key}" for test, key in fields]
        with self._conn() as c:
            CHUNK = 400                     # stay far below SQLite's 999 limit
            for i in range(0, len(ids), CHUNK):
                chunk = ids[i:i + CHUNK]
                marks = ",".join("?" for _ in chunk)
                try:
                    rows = c.execute(
                        f"SELECT id, json_extract(CASE WHEN json_valid("
                        f"results_json) THEN results_json END, {paths}) AS s"
                        f" FROM analyses WHERE id IN ({marks})"
                        f" AND results_json IS NOT NULL",
                        [*path_args, *chunk]).fetchall()
                except sqlite3.OperationalError as e:
                    log.warning("per-test statuses not read for the listing: "
                                "%s", e)
                    return {}
                for row in rows:
                    # One requested path comes back as the bare value, several
                    # as a JSON array in the order they were asked for.
                    if len(fields) == 1:
                        found = [row["s"]]
                    else:
                        try:
                            found = json.loads(row["s"]) if row["s"] else None
                        except (json.JSONDecodeError, TypeError):
                            found = None
                    if not isinstance(found, list):
                        continue
                    d: dict[str, dict] = {}
                    for (test, key), status in zip(fields, found):
                        if isinstance(status, str):
                            d.setdefault(test, {})[key] = status
                    out[row["id"]] = d
        return out

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

    def set_labels(self, aid: str, labels: dict) -> dict:
        """Rewrite the identification labels of one analysis.

        A stored measuring-point layout belongs to the LABEL, never to the
        analysis. So renaming the phantom does not carry the layout across: the
        new label keeps whatever layout it already had (or none), and the old
        label's layout is pruned if that was its last analysis. Anything else
        would apply one phantom's assembly quirks to a different phantom."""
        fields = {k: str(v or "").strip() for k, v in labels.items()
                  if k in LABEL_FIELDS}
        if not fields:
            return {"changed": False, "profile_deleted": False}
        with self.write_transaction() as c:
            row = c.execute(
                "SELECT phantom, signature, is_baseline FROM analyses WHERE id=?",
                (aid,)).fetchone()
            if row is None:
                raise KeyError(aid)
            before = self.profile_key(row["phantom"])
            sets = ", ".join(f"{k}=?" for k in fields)
            c.execute(f"UPDATE analyses SET {sets} WHERE id=?",
                      [*fields.values(), aid])
            after = self.profile_key(fields.get("phantom", before))
            pruned = self._prune_profile(c, before) if after != before else False

            # A baseline belongs to one phantom on one protocol. Renaming it
            # into a phantom that already has a reference would leave two, and
            # baseline_for would return whichever the database happened to
            # reach first. The moved analysis stands down: the phantom it just
            # joined already has a reference chosen deliberately.
            demoted = False
            if row["is_baseline"] and after != before:
                clash = c.execute(
                    "SELECT 1 FROM analyses WHERE is_baseline=1 AND id<>?"
                    " AND COALESCE(TRIM(phantom),'')=?"
                    " AND COALESCE(signature,'')=? LIMIT 1",
                    (aid, after, row["signature"] or "")).fetchone()
                if clash:
                    c.execute("UPDATE analyses SET is_baseline=0 WHERE id=?",
                              (aid,))
                    demoted = True
        return {"changed": True, "phantom_before": before,
                "phantom_after": after, "profile_deleted": pruned,
                "baseline_demoted": demoted}

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

    # ------------------------------------------------------------- baselines

    # A baseline is the reference a constancy test is measured against, and it
    # is scoped to ONE PHANTOM on ONE PROTOCOL.
    #
    # The phantom half matters because two phantoms can differ by design and
    # both be valid — the reference set here contains two builds whose internal
    # features sit millimetres apart. Scoping by protocol alone meant the second
    # phantom's baseline silently demoted the first one's, so a site running two
    # phantoms on one machine could never have a reference for both.
    #
    # The protocol half stays because pixel values in processed radiographs are
    # not proportional to dose: comparing across kV, detector or processing is
    # meaningless whatever phantom was used.

    @staticmethod
    def baseline_scope(rec: dict) -> tuple:
        return (str((rec or {}).get("phantom") or "").strip(),
                str((rec or {}).get("signature") or ""))

    #: Why a record may not be discarded or freely reworked. One definition,
    #: used by the discard endpoint, the re-run endpoint and the interface, so
    #: the three cannot come to different conclusions about the same record.
    PROTECTION_REASONS = {
        "finalized": "it has been finalised",
        "signed_off": "it carries a validation ruling",
        "baseline": "it is the reference scan for its phantom",
    }

    @staticmethod
    def protection(rec: dict) -> list[str]:
        """Which protections apply to this record, in order of seniority."""
        reasons = []
        if rec.get("finalized_at"):
            reasons.append("finalized")
        if rec.get("validation_status"):
            reasons.append("signed_off")
        if rec.get("is_baseline"):
            reasons.append("baseline")
        return reasons

    def set_finalized(self, aid: str, user: str = "") -> str:
        """Stamp the record as finalised, keeping the FIRST time.

        Finalize is pressable more than once — the interface offers it
        whenever the results are on screen, and operators do press it twice.
        Re-stamping would move the moment the record was declared done every
        time somebody looked at it, so the original stands."""
        with self.write_transaction() as c:
            row = c.execute(
                "SELECT COALESCE(finalized_at,'') AS at FROM analyses"
                " WHERE id=?", (aid,)).fetchone()
            if row is None:
                raise KeyError(aid)
            if row["at"]:
                return row["at"]
            when = time.strftime("%Y-%m-%d %H:%M:%S")
            c.execute(
                "UPDATE analyses SET finalized_at=?, finalized_by=? WHERE id=?",
                (when, user, aid))
            return when

    def clear_finalized(self, aid: str) -> None:
        """Reopen a finalised record. The re-run path uses this."""
        with self.write_transaction() as c:
            c.execute("UPDATE analyses SET finalized_at='', finalized_by=''"
                      " WHERE id=?", (aid,))

    #: How many previous states of one analysis are kept. Revision 1 — the
    #: numbers as first computed — is pinned, exactly as geometry history pins
    #: its untouched proposal: the original is the one worth keeping longest.
    REVISION_DEPTH = 5

    def _snapshot(self, c, aid: str, *, user: str, reason: str,
                  mode: str) -> int:
        """Store everything the caller is about to overwrite. Inside a
        transaction the caller already owns."""
        row = c.execute(
            "SELECT reg_json, geometry_json, results_json, status, sid_mm,"
            " COALESCE(layout_source,'') AS layout_source,"
            " COALESCE(finalized_at,'') AS finalized_at,"
            " COALESCE(finalized_by,'') AS finalized_by,"
            " COALESCE(validation_status,'') AS validation_status,"
            " COALESCE(validated_by,'') AS validated_by,"
            " COALESCE(validation_comment,'') AS validation_comment,"
            " COALESCE(validated_at,'') AS validated_at,"
            " is_baseline FROM analyses WHERE id=?", (aid,)).fetchone()
        if row is None:
            raise KeyError(aid)
        nxt = 1 + int(c.execute(
            "SELECT COALESCE(MAX(rev), 0) FROM analysis_revisions"
            " WHERE analysis_id=?", (aid,)).fetchone()[0])
        blob = zlib.compress(json.dumps({
            "reg": row["reg_json"], "geometry": row["geometry_json"],
            "results": row["results_json"],
        }).encode("utf-8"), 6)
        validation = {k: row[k] for k in ("validation_status", "validated_by",
                                          "validation_comment", "validated_at")}
        results = _safe_json(row["results_json"]) or {}
        c.execute(
            "INSERT INTO analysis_revisions (analysis_id, rev, created_at,"
            " user, reason, mode, status, overall, finalized_at, finalized_by,"
            " validation_json, was_baseline, sid_mm, layout_source, snapshot_z)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, nxt, time.strftime("%Y-%m-%d %H:%M:%S"), user, reason, mode,
             row["status"] or "", (results.get("_meta") or {}).get("overall", "")
             or row["status"] or "", row["finalized_at"], row["finalized_by"],
             json.dumps(validation), int(row["is_baseline"] or 0),
             row["sid_mm"], row["layout_source"], blob))
        # Trim the oldest, never revision 1.
        c.execute(
            "DELETE FROM analysis_revisions WHERE analysis_id=? AND rev>1"
            " AND rev<=?", (aid, nxt - self.REVISION_DEPTH))
        return nxt

    def begin_rerun(self, aid: str, *, user: str, reason: str, mode: str,
                    new_reg=None, keep_geometry: bool = False) -> dict:
        """Snapshot the record, then clear what the re-run will replace.

        One transaction, so an analysis can never be found half-reworked: it is
        either the old one or an open draft with its previous state kept.

        A re-run reopens the record — finalised stamp cleared, any ruling
        withdrawn — because the numbers those attach to are about to change. A
        ruling that outlived the numbers it was given for would be worse than
        no ruling at all. Both are inside the snapshot, so cancelling puts them
        back exactly.

        The reference flag is deliberately NOT cleared: clearing it would leave
        the phantom with no reference at all while the re-run is in progress,
        and every other scan of it comparing against nothing.
        """
        with self.write_transaction() as c:
            rev = self._snapshot(c, aid, user=user, reason=reason, mode=mode)
            sets = ["results_json=NULL", "status='draft'",
                    "finalized_at=''", "finalized_by=''",
                    "validation_status=''", "validated_by=''",
                    "validation_comment=''", "validated_at=''"]
            args: list = []
            if new_reg is not None:
                sets.append("reg_json=?")
                args.append(json.dumps(new_reg))
            if not keep_geometry:
                # Geometry derived from a transform that has been replaced is
                # meaningless, and its undo history holds pixel coordinates
                # from the same transform.
                sets += ["geometry_json=NULL", "geometry_seq=0",
                         "layout_source=''", "stage='A'"]
            else:
                sets.append("stage='C'")
            args.append(aid)
            c.execute(f"UPDATE analyses SET {', '.join(sets)} WHERE id=?", args)
            if not keep_geometry:
                c.execute("DELETE FROM geometry_history WHERE analysis_id=?",
                          (aid,))
            return {"revision": rev,
                    "stage": "C" if keep_geometry else "A"}

    def list_revisions(self, aid: str) -> list[dict]:
        """Revision metadata — never the snapshot itself, which is large."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT rev, created_at, user, reason, mode, status, overall,"
                " finalized_at, finalized_by, was_baseline, sid_mm"
                " FROM analysis_revisions WHERE analysis_id=?"
                " ORDER BY rev DESC", (aid,)).fetchall()
        return [dict(r) for r in rows]

    def restore_revision(self, aid: str, rev: int | None = None) -> dict:
        """Put a snapshot back, including the ruling and the finalised stamp.

        The restored numbers are the ones somebody signed, so they are restored
        with the signature that was given for them."""
        with self.write_transaction() as c:
            row = c.execute(
                "SELECT * FROM analysis_revisions WHERE analysis_id=?"
                + (" AND rev=?" if rev else "")
                + " ORDER BY rev DESC LIMIT 1",
                (aid, rev) if rev else (aid,)).fetchone()
            if row is None:
                raise KeyError(f"{aid} has no revision to restore")
            snap = json.loads(zlib.decompress(row["snapshot_z"]).decode("utf-8"))
            validation = _safe_json(row["validation_json"]) or {}
            c.execute(
                "UPDATE analyses SET reg_json=?, geometry_json=?,"
                " results_json=?, status=?, sid_mm=?, layout_source=?,"
                " finalized_at=?, finalized_by=?, validation_status=?,"
                " validated_by=?, validation_comment=?, validated_at=?,"
                " stage='F' WHERE id=?",
                (snap["reg"], snap["geometry"], snap["results"],
                 row["status"], row["sid_mm"], row["layout_source"],
                 row["finalized_at"], row["finalized_by"],
                 validation.get("validation_status", ""),
                 validation.get("validated_by", ""),
                 validation.get("validation_comment", ""),
                 validation.get("validated_at", ""), aid))
            c.execute("DELETE FROM analysis_revisions WHERE analysis_id=?"
                      " AND rev=?", (aid, row["rev"]))
            return {"restored": int(row["rev"]), "status": row["status"]}

    def set_baseline(self, aid: str, value: bool = True) -> dict:
        """Mark or unmark this analysis as its phantom's reference.

        Unmarking is a first-class action: a baseline chosen from a scan that
        later turns out to be poor has to be retractable, and until now nothing
        could clear the flag once set."""
        with self.write_transaction() as c:
            row = c.execute(
                "SELECT phantom, signature FROM analyses WHERE id=?",
                (aid,)).fetchone()
            if row is None:
                raise KeyError(aid)
            phantom, signature = self.baseline_scope(dict(row))
            replaced = []
            if value:
                replaced = [r["id"] for r in c.execute(
                    "SELECT id FROM analyses WHERE is_baseline=1 AND id<>?"
                    " AND COALESCE(TRIM(phantom),'')=? AND COALESCE(signature,'')=?",
                    (aid, phantom, signature)).fetchall()]
                if replaced:
                    c.execute(
                        "UPDATE analyses SET is_baseline=0 WHERE is_baseline=1"
                        " AND id<>? AND COALESCE(TRIM(phantom),'')=?"
                        " AND COALESCE(signature,'')=?",
                        (aid, phantom, signature))
            c.execute("UPDATE analyses SET is_baseline=? WHERE id=?",
                      (int(value), aid))
        return {"is_baseline": bool(value), "phantom": phantom,
                "signature": signature, "replaced": replaced}

    def baseline_for(self, signature: str, phantom: str = "",
                     exclude_id: str | None = None):
        """The reference for one phantom on one protocol."""
        with self._conn() as c:
            row = c.execute(
                "SELECT id FROM analyses WHERE is_baseline=1"
                " AND COALESCE(signature,'')=? AND COALESCE(TRIM(phantom),'')=?"
                " AND id != ? LIMIT 1",
                (signature or "", str(phantom or "").strip(),
                 exclude_id or "")).fetchone()
        if row is None:
            return None
        return self.get(row["id"])

    def baselines(self) -> list[dict]:
        """Every current reference, one row per phantom-and-protocol."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, phantom, site, signature, acquired_at, created_at,"
                " status FROM analyses WHERE is_baseline=1"
                " ORDER BY phantom, signature").fetchall()
        return [dict(r) for r in rows]

    def delete(self, aid: str, *, only_if_unprotected: bool = False) -> dict:
        """Remove an analysis, its stored file, its edit history — and, when it
        was the last analysis of its phantom, that phantom's stored layout.

        The cascade lives here rather than in the web endpoint so a deletion
        from the CLI, a test or a future admin command cannot leave an orphan
        layout behind that would later be replayed onto an unrelated scan.
        Count-then-delete runs inside BEGIN IMMEDIATE, so two workers deleting
        the last two analyses of one phantom cannot both see a non-zero count.

        ``only_if_unprotected`` is what lets an operator throw away their own
        unfinished work without an administrator. The check happens INSIDE the
        transaction: a colleague finalising the same record between the moment
        the dialog opened and the moment it was confirmed must win, and a check
        made before the lock could not guarantee that.
        """
        with self.write_transaction() as c:
            row = c.execute(
                "SELECT phantom, COALESCE(finalized_at,'') AS finalized_at,"
                " COALESCE(validation_status,'') AS validation_status,"
                " is_baseline FROM analyses WHERE id=?", (aid,)).fetchone()
            if only_if_unprotected and row is not None:
                blocked = self.protection(dict(row))
                if blocked:
                    raise ProtectedAnalysis(aid, blocked)
            label = self.profile_key(row["phantom"]) if row else ""
            c.execute("DELETE FROM analyses WHERE id=?", (aid,))
            c.execute("DELETE FROM geometry_history WHERE analysis_id=?", (aid,))
            c.execute("DELETE FROM analysis_revisions WHERE analysis_id=?",
                      (aid,))
            # Order matters: unwind first, because pruning drops the row this
            # would have read. Pruning then handles the case where the label
            # has no analyses left at all.
            layout = self._unwind_profile(c, label, aid)
            pruned = self._prune_profile(c, label)
        p = self.upload_path(aid)
        if os.path.exists(p):
            os.remove(p)
        # The comparison pictures are cut from this scan; left behind, they
        # would outlive the record they show — patient-adjacent imagery on
        # disk with nothing pointing at it.
        thumbs = self.thumbs_dir(aid)
        if thumbs and os.path.isdir(thumbs):
            shutil.rmtree(thumbs, ignore_errors=True)
        return {"deleted": row is not None, "phantom": label,
                "profile_deleted": pruned or layout == "deleted",
                "profile_restored": layout == "restored"}

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


_DATE_TAGS = ("StudyDate", "AcquisitionDate", "ContentDate", "SeriesDate",
              "InstanceCreationDate")
_TIME_TAGS = ("SeriesTime", "AcquisitionTime", "StudyTime", "ContentTime")


def _first_str(meta: dict, tags) -> str:
    """First non-empty tag value as a plain string.

    pydicom hands back a MultiValue for a multi-valued tag; str() on that gives
    "['20260727']", which fails every digit test below and silently produces no
    date at all. Take the first element instead."""
    for tag in tags:
        v = (meta or {}).get(tag)
        if isinstance(v, (list, tuple)):
            v = v[0] if v else None
        s = str(v or "").strip()
        if s:
            return s
    return ""


def _acquired_at(meta: dict) -> str:
    """Acquisition timestamp from the DICOM header, as 'YYYY-MM-DD HH:MM:SS'.

    Empty when the header carries no usable date. That is a real answer, not a
    failure: it is what lets the History table say "unknown" instead of quietly
    showing the upload time in the acquisition column."""
    d = _first_str(meta, _DATE_TAGS)
    t = _first_str(meta, _TIME_TAGS)
    if len(d) != 8 or not d.isdigit():
        return ""
    stamp = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    digits = "".join(ch for ch in t if ch.isdigit())
    if len(digits) >= 6:
        stamp += f" {digits[0:2]}:{digits[2:4]}:{digits[4:6]}"
    elif len(digits) == 4:
        # A valid DICOM TM may carry only HHMM. Reading it as midnight loses
        # real information and makes same-day scans unorderable.
        stamp += f" {digits[0:2]}:{digits[2:4]}:00"
    else:
        stamp += " 00:00:00"
    return stamp


def acquisition_flag(rec: dict) -> str:
    """How far the recorded acquisition time can be trusted.

    '' the header gave a plausible time; 'missing' it gave none, so anything
    date-driven falls back to the upload time; 'implausible' it gave one that
    cannot be right — a detector whose clock was reset reports 1980, and a scan
    cannot have been taken appreciably after it was uploaded.

    The tolerance is a whole day on purpose: DICOM times are scanner-local and
    the upload time is server-local, and nothing records either offset."""
    acq = str((rec or {}).get("acquired_at") or "").strip()
    if not acq:
        return "missing"
    year = acq[:4]
    if not year.isdigit() or int(year) < 2000:
        return "implausible"
    created = str((rec or {}).get("created_at") or "").strip()
    if created:
        try:
            a = time.mktime(time.strptime(acq[:19], "%Y-%m-%d %H:%M:%S"))
            c = time.mktime(time.strptime(created[:19], "%Y-%m-%d %H:%M:%S"))
            if a - c > 86400:
                return "implausible"
        except ValueError:
            return "implausible"
    return ""


def acquired_or_uploaded(rec: dict) -> str:
    """The timestamp to sort and plot by when no explicit choice was made."""
    return (str((rec or {}).get("acquired_at") or "").strip()
            or str((rec or {}).get("created_at") or "").strip())


def _exposure_number(value):
    """A finite number, or None. A JSON true is not the number 1."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if float("-inf") < value < float("inf") else None


def exposure_columns(meta: dict) -> list:
    """The exposure columns' values from a stored header, in EXPOSURE_FIELDS
    order. None where the detector wrote nothing."""
    meta = meta if isinstance(meta, dict) else {}
    return [_exposure_number(meta.get(tag)) for _, tag in EXPOSURE_FIELDS]


def exposure_of(rec: dict) -> dict:
    """The exposure values of one record, keyed by column name.

    Taken from the columns when the record carries them and from the header
    otherwise, so a full record, a slim one and a listing row all answer the
    same way."""
    rec = rec or {}
    from_meta = dict(zip((col for col, _ in EXPOSURE_FIELDS),
                         exposure_columns(rec.get("meta"))))
    return {col: (_exposure_number(rec.get(col))
                  if rec.get(col) is not None else from_meta[col])
            for col, _ in EXPOSURE_FIELDS}


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
    # The exposure columns come last so that every consumer written against
    # the earlier layout still finds each column where it was. An empty cell
    # means the detector recorded no value, as elsewhere in both exports.
    wr.writerow(["analysis_id", "site", "phantom", "operator", "acquired_at",
                 "acquired_flag", "created_at", "source", "signature",
                 "is_baseline", "validation", "validated_by", "validated_at",
                 "test", "object", "metric", "value", "unit", "status"]
                + [col for col, _ in EXPOSURE_FIELDS])
    for rec in records:
        res = rec.get("results")
        if not res:
            continue
        exposure = exposure_of(rec)
        tail = ["" if exposure[col] is None else exposure[col]
                for col, _ in EXPOSURE_FIELDS]
        for row in flatten_results(res):
            wr.writerow([rec["id"], rec.get("site", ""), rec.get("phantom", ""),
                         rec.get("operator", ""), rec.get("acquired_at", ""),
                         acquisition_flag(rec),
                         rec["created_at"], rec["source_name"],
                         rec["signature"], rec.get("is_baseline", 0),
                         VALIDATION_LABELS.get(rec.get("validation_status", ""),
                                               rec.get("validation_status", "")),
                         rec.get("validated_by", ""),
                         rec.get("validated_at", ""),
                         row["test"], row["object"], row["metric"],
                         row["value"], row["unit"], row["status"]] + tail)
    return buf.getvalue()


def wide_csv_export(records: list[dict]) -> str:
    """Wide-format CSV: one row per metric, one column per analysis.

    This is the shape you want for eyeballing drift across a series — the
    long format is better for pivot tables, this one is better for reading."""
    import csv
    import io as _io
    ordered = sorted(records, key=acquired_or_uploaded)
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
    # The two dates are separate rows on purpose: a detector whose clock was
    # reset makes "acquired" untrustworthy, and "uploaded" is then the only
    # timestamp that can order a large dataset.
    for label in ("site", "phantom", "operator", "acquired_at", "created_at",
                  "signature", "status", "validation_status", "validated_by",
                  "validated_at"):
        wr.writerow(["", "", f"# {label}", ""]
                    + [str(r.get(label, "") or "") for r in ordered])
    wr.writerow(["", "", "# acquired_flag", ""]
                + [acquisition_flag(r) or "ok" for r in ordered])
    # the long CSV's 'validation' column uses the human labels; mirror them so
    # the two exports agree about what a ruling is called
    wr.writerow(["", "", "# validation", ""]
                + [VALIDATION_LABELS.get(r.get("validation_status", ""),
                                         r.get("validation_status", ""))
                   for r in ordered])
    for key, unit in metrics:
        wr.writerow(list(key) + [unit]
                    + [m.get(key, "") for m in per_rec])
    # After every metric row rather than with the labels above: a spreadsheet
    # built on this export keeps each existing row where it was.
    exposures = [exposure_of(r) for r in ordered]
    for col, _ in EXPOSURE_FIELDS:
        wr.writerow(["", "", f"# {col}", ""]
                    + ["" if e[col] is None else e[col] for e in exposures])
    return buf.getvalue()
