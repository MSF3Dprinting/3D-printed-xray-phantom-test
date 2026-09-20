"""Configuration and secrets, loaded from environment / .env file.

Secrets are NEVER hard-coded and never committed: `.env` is git-ignored and
`.env.example` documents the required keys. On first run in a non-production
setting a random secret key is generated in memory so development works without
setup, but a missing password is a hard error whenever auth is enabled.
"""

from __future__ import annotations

import os
import secrets
import sys


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            os.environ.setdefault(key, val)


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Config:
    def __init__(self, env_path: str = ".env"):
        _load_dotenv(env_path)

        self.env = os.environ.get("PHANTOMQA_ENV", "development").lower()
        self.is_production = self.env == "production"

        # --- authentication
        self.auth_enabled = _bool("PHANTOMQA_AUTH_ENABLED", self.is_production)
        self.username = os.environ.get("PHANTOMQA_USERNAME", "qa")
        self.password_hash = os.environ.get("PHANTOMQA_PASSWORD_HASH", "")
        self.password_plain = os.environ.get("PHANTOMQA_PASSWORD", "")
        self.session_hours = _int("PHANTOMQA_SESSION_HOURS", 12)
        self.max_login_attempts = _int("PHANTOMQA_MAX_LOGIN_ATTEMPTS", 8)
        self.lockout_minutes = _int("PHANTOMQA_LOCKOUT_MINUTES", 15)

        # --- transport / cookies
        self.https_only = _bool("PHANTOMQA_HTTPS_ONLY", self.is_production)
        self.behind_proxy = _bool("PHANTOMQA_BEHIND_PROXY", self.is_production)
        # Sub-path mount, e.g. "/x-ray" for https://host/x-ray/. Normalised to
        # a leading slash and no trailing slash; "" means mounted at the root.
        rp = (os.environ.get("PHANTOMQA_ROOT_PATH", "") or "").strip().rstrip("/")
        if rp and not rp.startswith("/"):
            rp = "/" + rp
        self.root_path = rp
        # Cookies are scoped to the mount so a different app on the same domain
        # never receives this session cookie.
        self.cookie_path = (rp + "/") if rp else "/"
        self.allowed_hosts = [
            h.strip() for h in os.environ.get("PHANTOMQA_ALLOWED_HOSTS", "").split(",")
            if h.strip()]
        self.cors_origins = [
            o.strip() for o in os.environ.get("PHANTOMQA_CORS_ORIGINS", "").split(",")
            if o.strip()]

        # --- destructive actions
        # Deleting an analysis destroys the stored source file too, so it is
        # gated behind a SEPARATE password that ordinary users do not have.
        # With no admin password configured, deletion is refused outright —
        # the safe default for a shared installation.
        self.admin_password_hash = os.environ.get(
            "PHANTOMQA_ADMIN_PASSWORD_HASH", "")
        self.deletion_enabled = bool(self.admin_password_hash)

        # --- limits
        self.max_upload_mb = _int("PHANTOMQA_MAX_UPLOAD_MB", 200)
        # Seconds one analysis may spend measuring before the remaining tests
        # are abandoned and the result is recorded as a failure. It is a
        # backstop, not a target: every scan measured so far finishes inside
        # ten seconds. 0 disables it.
        self.analysis_timeout_s = _int("PHANTOMQA_ANALYSIS_TIMEOUT_S", 120)

        # --- logging
        self.log_dir = os.environ.get("PHANTOMQA_LOG_DIR", "logs")
        self.log_level = os.environ.get("PHANTOMQA_LOG_LEVEL", "INFO")
        self.log_max_mb = _int("PHANTOMQA_LOG_MAX_MB", 10)
        self.log_backups = _int("PHANTOMQA_LOG_BACKUPS", 10)
        self.log_audit_backups = _int("PHANTOMQA_LOG_AUDIT_BACKUPS", 30)
        self.log_console = _bool("PHANTOMQA_LOG_CONSOLE", True)

        # --- secret key (session signing)
        key = os.environ.get("PHANTOMQA_SECRET_KEY", "")
        if not key:
            if self.is_production:
                raise SystemExit(
                    "PHANTOMQA_SECRET_KEY is required in production. "
                    "Generate one with:  python -m phantom_qa.manage gen-secret")
            key = secrets.token_urlsafe(48)
        self.secret_key = key

        if self.auth_enabled and not (self.password_hash or self.password_plain):
            raise SystemExit(
                "Authentication is enabled but no password is configured.\n"
                "Set PHANTOMQA_PASSWORD_HASH in .env (recommended) — create it with:\n"
                "    python -m phantom_qa.manage set-password")
        if self.is_production and self.password_plain and not self.password_hash:
            raise SystemExit(
                "PHANTOMQA_PASSWORD (plain text) must not be used in production. "
                "Use PHANTOMQA_PASSWORD_HASH instead: python -m phantom_qa.manage set-password")

    def summary(self) -> str:
        return (f"env={self.env} auth={'on' if self.auth_enabled else 'OFF'} "
                f"https_only={self.https_only} "
                f"hosts={self.allowed_hosts or '*'} "
                f"max_upload={self.max_upload_mb}MB "
                f"deletion={'admin-password' if self.deletion_enabled else 'DISABLED'} "
                f"root_path={self.root_path or '/'} "
                f"logs={self.log_dir}")


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
