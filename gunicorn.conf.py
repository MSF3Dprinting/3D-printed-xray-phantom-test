"""Gunicorn configuration for the MSF Phantom QA app.

    gunicorn -c gunicorn.conf.py phantom_qa.webapp.main:app

THIS FILE AFFECTS ONLY THIS APP. A gunicorn config is per-invocation: each
`gunicorn -c <file> <app>` command reads only the file it is given, into its own
process tree. There is no shared or global gunicorn configuration, so other apps
on this VM — with their own units, ports and configs — are untouched by anything
here. Every environment variable read below is prefixed `PHANTOMQA_` so a
variable set for another service cannot leak into this one.

Everything else (auth, secrets, sub-path, logging) comes from .env — see
.env.example and docs/DEPLOYMENT.md.
"""

import multiprocessing
import os

from phantom_qa.config import get_config

_cfg = get_config()

# --- binding: loopback only; nginx terminates TLS and proxies to us.
# The port must be free on this host — see docs/DEPLOYMENT.md step 1.
bind = os.environ.get("PHANTOMQA_BIND", "127.0.0.1:8777")

# --- workers
# ASGI app, so uvicorn workers. Analysis is CPU-heavy, so processes scale it.
# Note the app-specific variable: WEB_CONCURRENCY is a conventional name that
# may already be set on this box for another service.
workers = int(os.environ.get("PHANTOMQA_WORKERS",
                             min(multiprocessing.cpu_count(), 4)))

# `uvicorn.workers.UvicornWorker` is deprecated upstream in favour of the
# separate `uvicorn-worker` package. Prefer it, fall back if not installed.
try:
    import uvicorn_worker  # noqa: F401
    worker_class = "uvicorn_worker.UvicornWorker"
except ImportError:  # pragma: no cover - depends on what is installed
    worker_class = "uvicorn.workers.UvicornWorker"

# A single analysis can take a couple of minutes on a large DICOM; the default
# 30 s would kill the worker mid-request.
timeout = int(os.environ.get("PHANTOMQA_TIMEOUT", 900))
graceful_timeout = 120
keepalive = 5

# Recycle workers periodically: the image pipeline allocates large arrays and
# this bounds long-term memory growth.
max_requests = 200
max_requests_jitter = 40

# --- trusted proxy -----------------------------------------------------------
# This is the ASGI equivalent of WSGI's ProxyFix, and it matters.
#
# uvicorn wraps the app in ProxyHeadersMiddleware (enabled by default). That
# middleware rewrites the client address from X-Forwarded-For and the scheme
# from X-Forwarded-Proto — but ONLY when the immediate peer is listed here.
# gunicorn passes this setting straight through to uvicorn.
#
# Keep it to the address nginx connects from. Widening it to "*" would let
# anything that can reach this port forge a client address, which would defeat
# the login throttle and poison the audit log.
forwarded_allow_ips = os.environ.get("PHANTOMQA_FORWARDED_ALLOW_IPS", "127.0.0.1")

# The sub-path (/x-ray) is applied by the application itself via
# FastAPI(root_path=...) from PHANTOMQA_ROOT_PATH. Do NOT set SCRIPT_NAME here:
# that is a WSGI convention which uvicorn does not read, so it would look like
# it were doing something while doing nothing.

# --- logging: the app writes its own rotating files; keep gunicorn's on stderr
# so systemd/journald captures start-up problems.
accesslog = None            # requests are logged by the app, with the username
errorlog = "-"
loglevel = os.environ.get("PHANTOMQA_LOG_LEVEL", "info").lower()
proc_name = "phantomqa"

# --- request limits (per worker process, not global)
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 16380


def on_starting(server):
    server.log.info("MSF Phantom QA starting: %s", _cfg.summary())
    server.log.info("workers=%s class=%s trusted proxy=%s",
                    workers, worker_class, forwarded_allow_ips)
    if not _cfg.auth_enabled:
        server.log.error("AUTHENTICATION IS DISABLED — do not expose this to a "
                         "network.")
    if _cfg.behind_proxy and forwarded_allow_ips in ("*", "0.0.0.0"):
        server.log.error("forwarded_allow_ips is wide open: any client could "
                         "forge its address and evade the login throttle.")
    if workers > 1 and not _cfg.is_production:
        server.log.warning("multiple workers outside production; set "
                           "PHANTOMQA_ENV=production for a real deployment")
