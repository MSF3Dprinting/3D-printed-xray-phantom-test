"""Trust boundary for proxy headers, and isolation of the gunicorn config.

The ASGI equivalent of WSGI's ProxyFix is uvicorn's ProxyHeadersMiddleware. It
is enabled by default and rewrites the client address from X-Forwarded-For —
but only when the immediate peer is in `forwarded_allow_ips`. The application
must therefore NOT parse that header itself, or it throws the trust boundary
away.
"""

import os
import re

import pytest
from fastapi.testclient import TestClient

from test_authorization import USER_PW, _build_app, _login

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------- header cannot be forged

def test_spoofed_forwarded_for_cannot_evade_the_login_throttle(tmp_path,
                                                               monkeypatch):
    """Anything that can reach gunicorn directly could send this header.
    Rotating it must not buy extra password guesses."""
    m = _build_app(tmp_path, monkeypatch, PHANTOMQA_BEHIND_PROXY="true",
                   PHANTOMQA_MAX_LOGIN_ATTEMPTS="3")
    m.throttle = __import__("phantom_qa.security", fromlist=["SharedThrottle"]) \
        .SharedThrottle(m.store.db_path, "login", 3, 15)
    c = TestClient(m.app)
    codes = [c.post("/api/login",
                    json={"username": "qa", "password": "wrong"},
                    headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code
             for i in range(9)]
    assert 429 in codes, (
        "rotating a forged X-Forwarded-For gave unlimited guesses — the app "
        "must not trust that header itself")


def test_spoofed_forwarded_for_cannot_evade_the_admin_throttle(tmp_path,
                                                               monkeypatch):
    m = _build_app(tmp_path, monkeypatch, PHANTOMQA_BEHIND_PROXY="true")
    from phantom_qa.security import SharedThrottle
    m.admin_throttle = SharedThrottle(m.store.db_path, "admin", 3, 15)
    from test_store_labels import fake_scan, results_for
    aid = m.store.new_analysis(fake_scan(), b"x", "sig", "1", "1", labels={})
    m.store.update(aid, results=results_for())

    c = TestClient(m.app)
    csrf = _login(c)
    c.headers.update({"X-CSRF-Token": csrf})
    codes = [c.post(f"/api/analyses/{aid}/validation",
                    json={"status": "validated", "validated_by": "X",
                          "admin_password": "wrong"},
                    headers={"X-Forwarded-For": f"172.16.0.{i}"}).status_code
             for i in range(8)]
    assert 429 in codes, "admin password guesses must be throttled per real peer"


def test_forged_header_does_not_reach_the_audit_log(tmp_path, monkeypatch):
    m = _build_app(tmp_path, monkeypatch, PHANTOMQA_BEHIND_PROXY="true")
    c = TestClient(m.app)
    c.post("/api/login", json={"username": "qa", "password": "wrong"},
           headers={"X-Forwarded-For": "203.0.113.99"})
    import logging
    for h in logging.getLogger("phantomqa.audit").handlers:
        h.flush()
    log = os.path.join(str(tmp_path), "logs", "audit.log")
    text = open(log, encoding="utf-8").read() if os.path.exists(log) else ""
    assert "203.0.113.99" not in text, (
        "a forged address was written to the audit log as if it were real")


def test_app_does_not_parse_forwarded_headers_itself():
    """Regression guard: the parsing belongs to the ASGI server, which checks
    the peer first. Compares the executable code only — the docstring is
    allowed (and expected) to discuss the header."""
    import ast
    src = open(os.path.join(REPO, "phantom_qa", "webapp", "main.py"),
               encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_client_key")
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]                                   # drop the docstring
    code = "\n".join(ast.unparse(stmt) for stmt in body).lower()
    assert "forwarded" not in code, (
        "_client_key must not read X-Forwarded-For; uvicorn already does, "
        "gated on forwarded_allow_ips")
    assert "headers" not in code, \
        "_client_key must take the address from the server, not from a header"


def test_client_address_comes_from_the_server(tmp_path, monkeypatch):
    m = _build_app(tmp_path, monkeypatch, PHANTOMQA_BEHIND_PROXY="true")
    from starlette.requests import Request

    scope = {"type": "http", "headers": [(b"x-forwarded-for", b"1.2.3.4")],
             "client": ("127.0.0.1", 1234), "method": "GET", "path": "/"}
    assert m._client_key(Request(scope)) == "127.0.0.1"

    # when uvicorn HAS applied the trusted rewrite, we use its answer
    scope["client"] = ("198.51.100.7", 5678)
    assert m._client_key(Request(scope)) == "198.51.100.7"


# ------------------------------------------------- gunicorn config isolation

def _gunicorn_src() -> str:
    return open(os.path.join(REPO, "gunicorn.conf.py"), encoding="utf-8").read()


def test_gunicorn_config_only_reads_prefixed_env_vars():
    """A variable set for another service on this VM must not change this app."""
    src = _gunicorn_src()
    names = set(re.findall(r'os\.environ\.get\(\s*["\']([A-Z_]+)["\']', src))
    unprefixed = {n for n in names if not n.startswith("PHANTOMQA_")}
    assert not unprefixed, (
        f"gunicorn.conf.py reads shared environment variables {unprefixed} — "
        f"prefix them so another app's settings cannot leak in")


def test_gunicorn_config_does_not_widen_the_proxy_trust():
    src = _gunicorn_src()
    m = re.search(r'forwarded_allow_ips\s*=\s*os\.environ\.get\(\s*'
                  r'["\']PHANTOMQA_FORWARDED_ALLOW_IPS["\']\s*,\s*'
                  r'["\']([^"\']+)["\']', src)
    assert m, "forwarded_allow_ips must be set explicitly"
    assert m.group(1) == "127.0.0.1", \
        f"default trusted proxy is {m.group(1)!r}; must be the loopback only"


def test_gunicorn_config_binds_to_loopback_by_default():
    m = re.search(r'bind\s*=\s*os\.environ\.get\(\s*["\']PHANTOMQA_BIND["\']'
                  r'\s*,\s*["\']([^"\']+)["\']', _gunicorn_src())
    assert m and m.group(1).startswith("127.0.0.1"), \
        "gunicorn must bind to loopback; nginx is the only entry point"


def test_gunicorn_config_sets_a_timeout_long_enough_for_an_analysis():
    m = re.search(r'timeout\s*=\s*int\(os\.environ\.get\('
                  r'["\']PHANTOMQA_TIMEOUT["\']\s*,\s*(\d+)', _gunicorn_src())
    assert m and int(m.group(1)) >= 600, \
        "the default 30 s would kill a worker mid-analysis"


def test_gunicorn_config_does_not_set_script_name():
    """SCRIPT_NAME is a WSGI convention uvicorn ignores; the sub-path comes
    from FastAPI(root_path=...). Setting it would only look reassuring."""
    assert "SCRIPT_NAME=" not in _gunicorn_src()


def test_gunicorn_config_is_importable_and_self_contained(tmp_path, monkeypatch):
    """It must load without a gunicorn install and without touching global state."""
    monkeypatch.setenv("PHANTOMQA_AUTH_ENABLED", "false")
    monkeypatch.setenv("PHANTOMQA_SECRET_KEY", "k" * 40)
    monkeypatch.delenv("PHANTOMQA_ENV", raising=False)
    import phantom_qa.config as cfgmod
    cfgmod._config = None

    ns: dict = {}
    exec(compile(_gunicorn_src(), "gunicorn.conf.py", "exec"), ns)
    assert ns["bind"].startswith("127.0.0.1")
    assert ns["workers"] >= 1
    assert "UvicornWorker" in ns["worker_class"]
    assert ns["forwarded_allow_ips"] == "127.0.0.1"
    assert ns["proc_name"] == "phantomqa"
    assert ns["accesslog"] is None
