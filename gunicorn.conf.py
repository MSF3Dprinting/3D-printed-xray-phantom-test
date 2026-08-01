"""Gunicorn configuration for the MSF Phantom QA app.

    gunicorn -c gunicorn.conf.py phantom_qa.webapp.main:app

Everything else (auth, secrets, sub-path, logging) comes from .env — see
.env.example and docs/DEPLOYMENT.md.
"""

import multiprocessing
import os

from phantom_qa.config import get_config

_cfg = get_config()

# --- binding: loopback only; nginx terminates TLS and proxies to us
bind = os.environ.get("PHANTOMQA_BIND", "127.0.0.1:8777")

# --- workers
# ASGI app, so uvicorn workers. Analysis is CPU-heavy and releases the GIL only
# partially, so processes (not threads) are what scale here.
workers = int(os.environ.get("WEB_CONCURRENCY",
                             min(multiprocessing.cpu_count(), 4)))
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

# --- sub-path mount (e.g. /x-ray). Passed to the ASGI app as root_path.
if _cfg.root_path:
    raw_env = [f"SCRIPT_NAME={_cfg.root_path}"]

# --- logging: the app writes its own rotating files; keep gunicorn's on stderr
# so systemd/journald captures start-up problems.
accesslog = None            # requests are logged by the app, with the username
errorlog = "-"
loglevel = os.environ.get("PHANTOMQA_LOG_LEVEL", "info").lower()
proc_name = "phantomqa"

# --- hardening
forwarded_allow_ips = os.environ.get("PHANTOMQA_FORWARDED_ALLOW_IPS", "127.0.0.1")
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 16380


def on_starting(server):
    server.log.info("MSF Phantom QA starting: %s", _cfg.summary())
    if not _cfg.auth_enabled:
        server.log.error("AUTHENTICATION IS DISABLED — refusing to serve is "
                         "your call, but do not expose this to a network.")
    if workers > 1 and not _cfg.is_production:
        server.log.warning("multiple workers outside production; make sure "
                           "PHANTOMQA_ENV=production for a real deployment")
