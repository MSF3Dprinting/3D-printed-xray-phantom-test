"""Authentication: password hashing, signed session cookies, rate limiting.

Uses only the standard library (PBKDF2-HMAC-SHA256 + HMAC-signed cookies), so
there is no extra dependency to keep patched in a hospital deployment.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import secrets
import sqlite3
import time

PBKDF2_ROUNDS = 240_000
SESSION_COOKIE = "phantomqa_session"
CSRF_COOKIE = "phantomqa_csrf"
CSRF_HEADER = "x-csrf-token"


# ------------------------------------------------------------------ passwords

def hash_password(password: str, rounds: int = PBKDF2_ROUNDS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${base64.b64encode(salt).decode()}" \
           f"${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds_s, salt_b64, dk_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 base64.b64decode(salt_b64), int(rounds_s))
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


# ------------------------------------------------------------------- sessions

def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_session(secret_key: str, username: str, hours: int) -> str:
    payload = {"u": username, "exp": int(time.time()) + hours * 3600,
               "jti": secrets.token_urlsafe(8)}
    body = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret_key.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64u(sig)}"


def read_session(secret_key: str, token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    expected = hmac.new(secret_key.encode(), body.encode(), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(_b64u_dec(sig), expected):
            return None
        payload = json.loads(_b64u_dec(body))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < time.time():
        return None
    return payload


# ---------------------------------------------------------------- rate limits

class LoginThrottle:
    """Per-client failed-attempt throttle, in process memory.

    Only correct for a SINGLE worker. Under gunicorn with N workers each
    process keeps its own counter, so an attacker effectively gets N times the
    allowed attempts. Use :class:`SharedThrottle` for a multi-worker deployment.
    """

    def __init__(self, max_attempts: int, lockout_minutes: int):
        self.max_attempts = max_attempts
        self.lockout = lockout_minutes * 60
        self._fails: dict[str, list[float]] = {}

    def locked_for(self, key: str) -> int:
        now = time.time()
        hits = [t for t in self._fails.get(key, []) if now - t < self.lockout]
        self._fails[key] = hits
        if len(hits) >= self.max_attempts:
            return int(self.lockout - (now - hits[0]))
        return 0

    def record_failure(self, key: str):
        self._fails.setdefault(key, []).append(time.time())

    def reset(self, key: str):
        self._fails.pop(key, None)


class SharedThrottle:
    """Failed-attempt throttle shared across processes via SQLite.

    Gunicorn runs several worker processes, so an in-memory counter would give
    an attacker `max_attempts x workers` guesses. This keeps the counter in the
    same SQLite file the rest of the app uses, so every worker sees the same
    state. Writes are tiny and rare (only on failure), so the extra I/O is
    irrelevant next to the image analysis this app does."""

    def __init__(self, db_path: str, scope: str, max_attempts: int,
                 lockout_minutes: int):
        self.db_path = db_path
        self.scope = scope
        self.max_attempts = max_attempts
        self.lockout = lockout_minutes * 60
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS auth_failures (
                            scope TEXT, client TEXT, ts REAL)""")
            c.execute("""CREATE INDEX IF NOT EXISTS idx_auth_failures
                         ON auth_failures(scope, client, ts)""")

    @contextlib.contextmanager
    def _conn(self):
        # Same file and therefore the same WAL mode the Store sets; the
        # busy_timeout matters here because several workers may record a failed
        # attempt at once. Closed explicitly — `with sqlite3.connect(...)` only
        # commits, so relying on it would leak a handle per login attempt.
        c = sqlite3.connect(self.db_path, timeout=15)
        c.execute("PRAGMA busy_timeout=15000")
        try:
            with c:
                yield c
        finally:
            c.close()

    def _purge(self, c, now: float):
        c.execute("DELETE FROM auth_failures WHERE ts < ?", (now - self.lockout,))

    def locked_for(self, key: str) -> int:
        now = time.time()
        with self._conn() as c:
            self._purge(c, now)
            rows = c.execute(
                "SELECT ts FROM auth_failures WHERE scope=? AND client=?"
                " ORDER BY ts", (self.scope, key)).fetchall()
        if len(rows) >= self.max_attempts:
            return int(self.lockout - (now - rows[0][0]))
        return 0

    def record_failure(self, key: str):
        with self._conn() as c:
            c.execute("INSERT INTO auth_failures (scope, client, ts)"
                      " VALUES (?,?,?)", (self.scope, key, time.time()))

    def reset(self, key: str):
        with self._conn() as c:
            c.execute("DELETE FROM auth_failures WHERE scope=? AND client=?",
                      (self.scope, key))


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(cookie_value: str | None, header_value: str | None) -> bool:
    return bool(cookie_value and header_value
                and hmac.compare_digest(cookie_value, header_value))
