"""Deletion must be hard to do by accident on a shared installation."""

import pytest

from phantom_qa.security import hash_password, verify_password


def test_admin_password_is_separate_from_the_login_password(tmp_path, monkeypatch):
    from phantom_qa.config import Config
    monkeypatch.setenv("PHANTOMQA_AUTH_ENABLED", "true")
    monkeypatch.setenv("PHANTOMQA_PASSWORD_HASH", hash_password("everyday-pass-1"))
    monkeypatch.setenv("PHANTOMQA_ADMIN_PASSWORD_HASH",
                       hash_password("admin-pass-2222"))
    c = Config(env_path=str(tmp_path / "none.env"))
    assert c.deletion_enabled
    assert verify_password("admin-pass-2222", c.admin_password_hash)
    assert not verify_password("everyday-pass-1", c.admin_password_hash)


def test_deletion_disabled_when_no_admin_password(tmp_path, monkeypatch):
    """The safe default: with nothing configured, nothing can be deleted."""
    from phantom_qa.config import Config
    monkeypatch.setenv("PHANTOMQA_AUTH_ENABLED", "false")
    monkeypatch.delenv("PHANTOMQA_ADMIN_PASSWORD_HASH", raising=False)
    c = Config(env_path=str(tmp_path / "none.env"))
    assert c.deletion_enabled is False
    assert "DISABLED" in c.summary()


def test_summary_reports_admin_protection(tmp_path, monkeypatch):
    from phantom_qa.config import Config
    monkeypatch.setenv("PHANTOMQA_AUTH_ENABLED", "false")
    monkeypatch.setenv("PHANTOMQA_ADMIN_PASSWORD_HASH", hash_password("x" * 14))
    c = Config(env_path=str(tmp_path / "none.env"))
    assert "admin-password" in c.summary()


def test_admin_throttle_is_stricter_than_login(tmp_path, monkeypatch):
    """A password that destroys data should tolerate fewer guesses."""
    from phantom_qa.config import Config
    from phantom_qa.security import LoginThrottle
    monkeypatch.setenv("PHANTOMQA_AUTH_ENABLED", "false")
    monkeypatch.setenv("PHANTOMQA_MAX_LOGIN_ATTEMPTS", "8")
    c = Config(env_path=str(tmp_path / "none.env"))
    admin_max = max(c.max_login_attempts // 2, 3)
    assert admin_max < c.max_login_attempts
    t = LoginThrottle(admin_max, c.lockout_minutes)
    for _ in range(admin_max):
        t.record_failure("client")
    assert t.locked_for("client") > 0


@pytest.mark.parametrize("typed,aid,should_match", [
    ("abc123", "abc123", True),
    (" abc123 ", "abc123", True),      # trimmed
    ("abc12", "abc123", False),
    ("ABC123", "abc123", False),       # case sensitive
    ("", "abc123", False),
])
def test_confirmation_id_matching(typed, aid, should_match):
    assert (typed.strip() == aid) is should_match


def test_store_delete_removes_row_and_file(tmp_path):
    from phantom_qa.store import Store
    from test_store_labels import fake_scan
    s = Store(str(tmp_path))
    aid = s.new_analysis(fake_scan(), b"payload", "sig", "1", "1")
    path = s.upload_path(aid)
    assert s.get(aid) is not None
    import os
    assert os.path.exists(path)
    s.delete(aid)
    assert s.get(aid) is None
    assert not os.path.exists(path), "the stored source file must go too"
