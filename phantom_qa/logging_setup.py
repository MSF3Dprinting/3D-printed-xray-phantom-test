"""Application and audit logging with rotation.

Two destinations, because they answer different questions and have different
retention needs:

  * ``phantomqa.log`` — everything the application does, for debugging.
  * ``audit.log``     — who did what to the data: sign-ins, uploads, label
                        edits, deletions, integrity checks. This is the record
                        you consult after "who deleted that analysis?", so it is
                        written at INFO regardless of the app log level, kept
                        for longer, and never contains secrets.

Both rotate by size so a long-running server cannot fill the disk.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import time

APP_LOGGER = "phantomqa"
AUDIT_LOGGER = "phantomqa.audit"

_configured = False

# Values that must never reach a log file even if a caller passes them.
_REDACT_KEYS = {"password", "passwd", "secret", "token", "authorization",
                "cookie", "password_hash", "admin_password", "csrf"}


def redact(data):
    """Recursively replace secret-looking values with '***'."""
    if isinstance(data, dict):
        return {k: ("***" if str(k).lower() in _REDACT_KEYS else redact(v))
                for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [redact(v) for v in data]
    return data


class _UtcFormatter(logging.Formatter):
    converter = time.gmtime
    default_msec_format = "%s.%03d"


def setup_logging(log_dir: str, level: str = "INFO", max_mb: int = 10,
                  backups: int = 10, audit_backups: int = 30,
                  console: bool = True) -> logging.Logger:
    """Configure logging once. Returns the application logger."""
    global _configured
    app = logging.getLogger(APP_LOGGER)
    if _configured:
        return app

    os.makedirs(log_dir, exist_ok=True)
    lvl = getattr(logging, str(level).upper(), logging.INFO)
    app.setLevel(lvl)
    app.propagate = False

    fmt = _UtcFormatter(
        "%(asctime)sZ %(levelname)-7s %(name)s %(message)s")

    app_file = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "phantomqa.log"),
        maxBytes=max_mb * 1024 * 1024, backupCount=backups, encoding="utf-8")
    app_file.setFormatter(fmt)
    app_file.setLevel(lvl)
    app.addHandler(app_file)

    err_file = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "errors.log"),
        maxBytes=max_mb * 1024 * 1024, backupCount=backups, encoding="utf-8")
    err_file.setFormatter(fmt)
    err_file.setLevel(logging.WARNING)
    app.addHandler(err_file)

    if console:
        con = logging.StreamHandler()
        con.setFormatter(_UtcFormatter("%(levelname)-7s %(message)s"))
        con.setLevel(lvl)
        app.addHandler(con)

    # --- audit: own file, own handlers, always INFO
    audit = logging.getLogger(AUDIT_LOGGER)
    audit.setLevel(logging.INFO)
    audit.propagate = False
    audit_file = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "audit.log"),
        maxBytes=max_mb * 1024 * 1024, backupCount=audit_backups,
        encoding="utf-8")
    audit_file.setFormatter(_UtcFormatter("%(asctime)sZ %(message)s"))
    audit.addHandler(audit_file)
    if console:
        acon = logging.StreamHandler()
        acon.setFormatter(_UtcFormatter("AUDIT   %(message)s"))
        audit.addHandler(acon)

    # keep uvicorn's own noise in the same files
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = [app_file] + ([con] if console else [])
        lg.setLevel(lvl)
        lg.propagate = False
    # uvicorn.access is very chatty and we log requests ourselves
    logging.getLogger("uvicorn.access").handlers = [app_file]
    logging.getLogger("uvicorn.access").propagate = False

    _configured = True
    app.info("logging configured dir=%s level=%s max_mb=%s backups=%s",
             log_dir, level, max_mb, backups)
    return app


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{APP_LOGGER}.{name}" if name else APP_LOGGER)


def _field(value, limit: int = 200) -> str:
    """One fixed audit field, safe to interpolate into the line format.

    The JSON tail escapes newlines on its own, but the fixed fields are
    interpolated raw — and one of them, the username on a failed sign-in, is
    attacker-typed. A newline there would forge a second line that greps
    exactly like a real audit record, which defeats the file's purpose."""
    s = "".join(ch if ch.isprintable() else " " for ch in str(value))
    return s[:limit] if s else "-"


def audit(event: str, *, user: str = "-", client: str = "-",
          analysis: str = "-", outcome: str = "ok", **detail):
    """Write one structured audit line.

    Fixed leading fields make the file greppable (``grep 'event=delete'``) and
    the JSON tail keeps the detail machine-readable."""
    safe = redact(detail)
    try:
        blob = json.dumps(safe, default=str, separators=(",", ":"))
    except Exception:
        blob = str(safe)
    logging.getLogger(AUDIT_LOGGER).info(
        "event=%s outcome=%s user=%s client=%s analysis=%s detail=%s",
        _field(event), _field(outcome), _field(user), _field(client),
        _field(analysis), blob)
