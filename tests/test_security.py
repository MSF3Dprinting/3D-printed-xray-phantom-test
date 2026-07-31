"""Authentication, session, CSRF and hardening tests."""

import time

import pytest

from phantom_qa.security import (LoginThrottle, csrf_ok, hash_password,
                                 issue_session, new_csrf_token, read_session,
                                 verify_password)


# ------------------------------------------------------------------ passwords

def test_password_hash_roundtrip():
    h = hash_password("correct horse battery staple", rounds=1000)
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong password", h)


def test_password_hash_is_salted():
    a = hash_password("same", rounds=1000)
    b = hash_password("same", rounds=1000)
    assert a != b, "identical passwords must not produce identical hashes"
    assert verify_password("same", a) and verify_password("same", b)


def test_password_hash_format_and_no_plaintext():
    h = hash_password("s3cret-passphrase", rounds=1000)
    assert h.startswith("pbkdf2_sha256$")
    assert "s3cret-passphrase" not in h


@pytest.mark.parametrize("bad", ["", "not-a-hash", "pbkdf2_sha256$x$y",
                                 "md5$1$a$b", "pbkdf2_sha256$1000$!!$??"])
def test_verify_rejects_malformed(bad):
    assert not verify_password("anything", bad)


# ------------------------------------------------------------------- sessions

def test_session_roundtrip():
    tok = issue_session("k" * 32, "qa", 12)
    s = read_session("k" * 32, tok)
    assert s and s["u"] == "qa"


def test_session_rejects_wrong_key():
    tok = issue_session("key-one" * 8, "qa", 12)
    assert read_session("key-two" * 8, tok) is None


def test_session_rejects_tampering():
    key = "k" * 32
    tok = issue_session(key, "qa", 12)
    body, _, sig = tok.partition(".")
    forged = issue_session(key, "admin", 12).partition(".")[0]
    assert read_session(key, f"{forged}.{sig}") is None


def test_session_expires():
    tok = issue_session("k" * 32, "qa", 0)
    time.sleep(1.1)
    assert read_session("k" * 32, tok) is None


@pytest.mark.parametrize("tok", [None, "", "no-dot", "a.b"])
def test_session_rejects_garbage(tok):
    assert read_session("k" * 32, tok) is None


# ----------------------------------------------------------------------- csrf

def test_csrf_requires_match():
    t = new_csrf_token()
    assert csrf_ok(t, t)
    assert not csrf_ok(t, new_csrf_token())
    assert not csrf_ok(None, t)
    assert not csrf_ok(t, None)
    assert not csrf_ok(None, None)


# ------------------------------------------------------------------ throttling

def test_throttle_locks_after_max_attempts():
    t = LoginThrottle(max_attempts=3, lockout_minutes=10)
    assert t.locked_for("1.2.3.4") == 0
    for _ in range(3):
        t.record_failure("1.2.3.4")
    assert t.locked_for("1.2.3.4") > 0
    assert t.locked_for("5.6.7.8") == 0, "throttle must be per-client"


def test_throttle_reset_on_success():
    t = LoginThrottle(max_attempts=2, lockout_minutes=10)
    t.record_failure("a")
    t.record_failure("a")
    assert t.locked_for("a") > 0
    t.reset("a")
    assert t.locked_for("a") == 0


# ------------------------------------------------------------------ config

def test_config_requires_secret_in_production(tmp_path, monkeypatch):
    from phantom_qa.config import Config
    monkeypatch.setenv("PHANTOMQA_ENV", "production")
    monkeypatch.delenv("PHANTOMQA_SECRET_KEY", raising=False)
    with pytest.raises(SystemExit):
        Config(env_path=str(tmp_path / "nonexistent.env"))


def test_config_rejects_plaintext_password_in_production(tmp_path, monkeypatch):
    from phantom_qa.config import Config
    monkeypatch.setenv("PHANTOMQA_ENV", "production")
    monkeypatch.setenv("PHANTOMQA_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("PHANTOMQA_PASSWORD", "plaintext-password")
    monkeypatch.delenv("PHANTOMQA_PASSWORD_HASH", raising=False)
    with pytest.raises(SystemExit):
        Config(env_path=str(tmp_path / "nonexistent.env"))


def test_config_requires_password_when_auth_enabled(tmp_path, monkeypatch):
    from phantom_qa.config import Config
    monkeypatch.setenv("PHANTOMQA_ENV", "development")
    monkeypatch.setenv("PHANTOMQA_AUTH_ENABLED", "true")
    monkeypatch.delenv("PHANTOMQA_PASSWORD", raising=False)
    monkeypatch.delenv("PHANTOMQA_PASSWORD_HASH", raising=False)
    with pytest.raises(SystemExit):
        Config(env_path=str(tmp_path / "nonexistent.env"))


def test_dotenv_loader_parses_quotes_and_comments(tmp_path, monkeypatch):
    from phantom_qa.config import Config
    p = tmp_path / ".env"
    p.write_text('# a comment\nPHANTOMQA_USERNAME="quoted-user"\n'
                 "PHANTOMQA_MAX_UPLOAD_MB=42\n\nPHANTOMQA_AUTH_ENABLED=false\n")
    for k in ("PHANTOMQA_USERNAME", "PHANTOMQA_MAX_UPLOAD_MB",
              "PHANTOMQA_AUTH_ENABLED", "PHANTOMQA_ENV"):
        monkeypatch.delenv(k, raising=False)
    c = Config(env_path=str(p))
    assert c.username == "quoted-user"
    assert c.max_upload_mb == 42
    assert c.auth_enabled is False
