# Deployment

Installing on a Linux server running nginx in front of gunicorn, served from a
sub-path such as `https://something.example.org/x-ray/`.

Everything here is scoped to this application: a `location` block added to an
existing nginx site, its own systemd unit, its own port, running as an existing
account. No global configuration is modified, so other applications on the same
server are unaffected.

Run `python -m phantom_qa.manage check` before going live; it exits non-zero if
anything important is missing.

---

## 1. Install

Deploy as the account you already use. Adjust the path to suit.

```bash
cd ~/apps
git clone <repo> phantomqa
cd phantomqa
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/pip install gunicorn uvicorn-worker
```

`uvicorn-worker` supplies `uvicorn_worker.UvicornWorker`; the older
`uvicorn.workers.UvicornWorker` is deprecated upstream. `gunicorn.conf.py`
prefers the new one and falls back if it is absent.

`data/` and `logs/` are created on first run inside this directory. Nothing is
written outside it.

### Choose a free port

Gunicorn binds to loopback only, but the port must not clash with another
service:

```bash
ss -ltnp | grep 127.0.0.1
```

This guide uses **8777**.

## 2. Configure

```bash
cp .env.example .env
chmod 600 .env
venv/bin/python -m phantom_qa.manage gen-secret          # PHANTOMQA_SECRET_KEY
venv/bin/python -m phantom_qa.manage set-password        # user login
venv/bin/python -m phantom_qa.manage set-admin-password  # delete, re-run, validate
```

Minimum `.env`:

```ini
PHANTOMQA_ENV=production
PHANTOMQA_ROOT_PATH=/x-ray
PHANTOMQA_ALLOWED_HOSTS=something.example.org
PHANTOMQA_BIND=127.0.0.1:8777
PHANTOMQA_SECRET_KEY=<from gen-secret>
PHANTOMQA_PASSWORD_HASH=<from set-password>
PHANTOMQA_ADMIN_PASSWORD_HASH=<from set-admin-password>
```

`PHANTOMQA_ENV=production` makes the application refuse to start without a
secret key, and refuse a plain-text password.

Leaving `PHANTOMQA_ADMIN_PASSWORD_HASH` empty disables deletion of finished
analyses, re-running finished analyses, and validation sign-off. Operators can
still discard their own unfinished analyses, which needs no password.

```bash
venv/bin/python -m phantom_qa.manage check    # must exit 0
```

Application settings are listed in `.env.example`, including the server-level
keys read by `gunicorn.conf.py` (`PHANTOMQA_BIND`, `PHANTOMQA_WORKERS`,
`PHANTOMQA_TIMEOUT`, `PHANTOMQA_FORWARDED_ALLOW_IPS`) and by `run_app.py`
(`PHANTOMQA_HOST`, `PHANTOMQA_PORT`).

## 3. Sub-path mounting

Set `PHANTOMQA_ROOT_PATH=/x-ray`. The application then:

- injects `<base href="/x-ray/">` into its pages, so the frontend builds every
  URL under the mount;
- scopes the session and CSRF cookies to `Path=/x-ray/`, so another application
  on the same domain never receives them;
- accepts requests whether or not nginx strips the prefix.

Both nginx styles work:

```nginx
location /x-ray/ { proxy_pass http://127.0.0.1:8777; }    # forwards the prefix
location /x-ray/ { proxy_pass http://127.0.0.1:8777/; }   # strips the prefix
```

Leave `PHANTOMQA_ROOT_PATH` empty when serving from the domain root.

## 4. nginx

Add these two blocks inside the existing `server { … }` for the domain. Do not
create a second `server` block for the same name, and do not edit `nginx.conf`.

```nginx
    # ---- MSF Phantom QA -------------------------------------------------
    location /x-ray/ {
        proxy_pass         http://127.0.0.1:8777;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        client_max_body_size 210M;   # must exceed PHANTOMQA_MAX_UPLOAD_MB
        proxy_read_timeout   900s;   # a large DICOM takes minutes to analyse
        proxy_send_timeout   900s;
        proxy_buffering      off;    # reports are multi-megabyte HTML
    }

    location = /x-ray { return 301 /x-ray/; }
    # ---------------------------------------------------------------------
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Points to observe when applications share a server:

- **`client_max_body_size` is set inside the location**, so the 210 MB limit
  applies to this application only.
- **Do not add `add_header` for CSP or HSTS here.** The application sets its own
  security headers, and an `add_header` inside a `location` replaces the whole
  inherited set — which would drop the headers other applications rely on from
  the server block.
- **Check the prefix does not overlap** another application's location:
  `grep -rn 'location' /etc/nginx/sites-enabled/`.
- Leave any existing HTTP-to-HTTPS redirect alone.

## 5. gunicorn and systemd

`gunicorn.conf.py` sets uvicorn workers, a 900-second timeout (the 30-second
default would kill a worker mid-analysis), worker recycling to bound memory
growth, and loopback binding.

These 900-second limits — gunicorn's and the proxy's — are the outer envelope,
sized for a slow upload rather than for measuring. The analysis bounds itself
much sooner with `PHANTOMQA_ANALYSIS_TIMEOUT_S` (default 120 s) and records a
failure the operator can open. Keep the envelope well above it: the point is
that the application reports its own timeout, instead of gunicorn killing a
worker or the proxy returning a gateway error, neither of which leaves the
operator anything to read.

```ini
# /etc/systemd/system/phantomqa.service
[Unit]
Description=MSF Phantom QA
After=network.target

[Service]
Type=simple
User=YOUR_USER
Group=YOUR_GROUP
WorkingDirectory=/home/YOUR_USER/apps/phantomqa
EnvironmentFile=/home/YOUR_USER/apps/phantomqa/.env
ExecStart=/home/YOUR_USER/apps/phantomqa/venv/bin/gunicorn \
          -c gunicorn.conf.py phantom_qa.webapp.main:app
Restart=on-failure
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/home/YOUR_USER/apps/phantomqa/data /home/YOUR_USER/apps/phantomqa/logs
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
MemoryMax=4G

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now phantomqa
systemctl status phantomqa
```

`ProtectHome` is deliberately omitted: it would hide the directory the
application is installed in. `ProtectSystem=strict` with an explicit
`ReadWritePaths` provides the confinement instead.

`MemoryMax=4G` caps this service alone, so a large analysis cannot starve other
applications. Adjust to suit the machine.

### Isolation from other applications

A gunicorn configuration is per-invocation: `gunicorn -c gunicorn.conf.py …`
reads only that file, into its own process tree. There is no global gunicorn
configuration, and each application has its own unit, virtualenv, port and
config.

Two things could couple them, and both are avoided:

- **Environment variables.** Every variable `gunicorn.conf.py` reads is prefixed
  `PHANTOMQA_`. In particular it does not read `WEB_CONCURRENCY`, the
  conventional worker-count variable that may already be set for another
  service; worker count comes from `PHANTOMQA_WORKERS`.
- **The port**, the only genuinely shared resource. Choose a free one in step 1.

### Workers and shared state

Gunicorn runs several worker processes:

- The login and administrator throttles are stored in SQLite, so all workers
  share one counter. An in-memory counter would give an attacker
  `max_attempts × workers` guesses.
- The decoded-image cache is per worker, so a scan may be read once per worker.
  This costs a little I/O and nothing else.

SQLite in WAL mode handles this at QA-team concurrency.

## 6. Proxy headers

Behind a reverse proxy the application must see the real client address rather
than the proxy's. In WSGI applications this is the job of
`werkzeug.middleware.proxy_fix.ProxyFix`. **ProxyFix is WSGI-only and cannot be
used here** — this is an ASGI application, where the equivalent is uvicorn's
`ProxyHeadersMiddleware`, which is enabled by default.

| | WSGI (`ProxyFix`) | ASGI (uvicorn) |
|---|---|---|
| Real client IP | `REMOTE_ADDR` from `X-Forwarded-For` | `scope["client"]` from `X-Forwarded-For` |
| Scheme | `wsgi.url_scheme` from `X-Forwarded-Proto` | `scope["scheme"]` from `X-Forwarded-Proto` |
| Trust model | `x_for=N` hop count | `forwarded_allow_ips` |
| Sub-path | `X-Forwarded-Prefix` → `SCRIPT_NAME` | `PHANTOMQA_ROOT_PATH` (configuration, not a header) |

No additional middleware is required, but **`forwarded_allow_ips` must be
correct**. `gunicorn.conf.py` passes it to uvicorn and defaults it to
`127.0.0.1`, the address nginx connects from. Widening it to `*` would let
anything able to reach the port forge a client address.

The application does not parse `X-Forwarded-For` itself. Doing so would discard
uvicorn's peer check, allowing any process able to reach the loopback port to
forge an address per request and evade the login throttle.

The sub-path is configured rather than read from `X-Forwarded-Prefix`: a header
the client controls should not determine where the application believes it is
mounted.

If every log entry shows `127.0.0.1`, `forwarded_allow_ips` does not include the
address nginx actually connects from.

## 7. Verify

```bash
curl -sI  https://something.example.org/x-ray/login | head -1        # 200
curl -s   https://something.example.org/x-ray/api/analyses | head -1 # 401
curl -sI  https://something.example.org/x-ray/api/analyses | grep -i content-security
venv/bin/python -m phantom_qa.manage check
venv/bin/python -m pytest tests/test_authorization.py -q
```

Then confirm the other applications still respond:

```bash
sudo nginx -t
curl -sI https://something.example.org/<other-app>/ | head -1
```

---

## Security model

| Control | Implementation |
|---|---|
| Authentication | Username and password; stored only as a PBKDF2-HMAC-SHA256 hash (240 000 rounds, per-password salt). Plain-text passwords are rejected in production |
| Authorization | Private by default: an explicit allow-list of six public paths; everything else requires a session. Administrator actions verify the administrator password in the handler |
| Sessions | HMAC-SHA256-signed cookie, `HttpOnly`, `SameSite=Strict`, `Secure` under HTTPS, scoped to the mount path. Expiry is inside the signed payload, default 12 hours |
| Brute force | Shared per-client throttle: 8 failed sign-ins or 4 failed administrator attempts lock that client out for 15 minutes |
| CSRF | Double-submit token; `POST`, `PUT`, `PATCH` and `DELETE` are rejected without a matching `X-CSRF-Token` |
| Path handling | The allow-list is compared against a normalised path, so duplicate slashes, trailing slashes and the mount prefix cannot bypass it |
| Host header | `PHANTOMQA_ALLOWED_HOSTS` allow-list; anything else receives 400 |
| Upload size | Rejected with 413 before the body is read |
| Response headers | CSP (`script-src 'self'`; the comparison report alone also admits its one inline script by that script's SHA-256 hash, never `'unsafe-inline'`), `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, COOP, `Permissions-Policy`, and HSTS under HTTPS |
| API caching | `/api/` responses are `Cache-Control: no-store`, except rendered scan pictures (`image.png`, `image.jpg`) and a low-contrast close-up requested under its current key, which are `private, max-age=86400` — immutable per URL, and never stored by a shared proxy |
| Attack surface | `/docs`, `/redoc` and the OpenAPI schema are disabled |
| Error handling | Unhandled exceptions return a bare 500 with no traceback. A corrupt or missing stored file returns 422 or 410 with an explanation |
| Request validation | Malformed request bodies return 422 with the field location and message only — the rejected value is never echoed back |
| Duplicate uploads | A file whose SHA-256 already exists is refused with 409 unless the operator explicitly confirms a re-analysis |
| Deletion | Unfinished analyses: discard with confirmation only. Finalised, signed-off or reference analyses: administrator password plus a written reason; throttled; disabled entirely when unconfigured |
| Re-run | Free before an analysis is finalised; afterwards administrator password plus a written reason |
| Image-quality override | An exposure that failed the image-quality check becomes a reference scan, or sets a phantom's stored measuring points, only with the administrator password plus a written reason; audited with the failed checks; throttled; impossible when unconfigured |
| Validation | Administrator password; records the approver's name and comment |
| Integrity | SHA-256 per analysis, re-checked in every report |
| Logging | Rotating application, error and audit logs with secrets redacted |

`tests/test_authorization.py` enumerates every route and asserts the level it
enforces. It fails if an endpoint is added without being classified as public,
user or admin, so the matrix cannot drift.

### Deletion

Deleting an analysis also removes its stored source file, its measuring-point
edit history, its kept earlier states, its comparison pictures under
`data/thumbs/`, and — when it was the last analysis carrying that phantom name —
that phantom's stored measuring-point layout; when it was the analysis that
stored the phantom's current layout, the previous layout is put back.

**Unfinished** analyses — not finalised, not signed off, not a reference — are
removed with **Discard**, which asks only for confirmation. That is deliberate:
the field audit log showed operators deleting and re-uploading the same file
repeatedly because clearing a mistake needed the administrator. The protection
check runs inside the same database transaction as the delete, so a record
finalised meanwhile is refused. Discards are audited as `event=delete` with
`"mode":"discard"`.

Removing anything that **has** been decided on requires all of: the
administrator password, a written reason of at least five characters, and
passing a throttle stricter than sign-in. With no administrator password
configured, such a deletion is refused and the interface explains how to enable
it.

The reason is mandatory because the audit log is the only surviving record of
why data was destroyed. It is capped at 500 characters before being logged.

> Typing the analysis id back was required until the third tester round. It was
> dropped: a copy-paste satisfied it, while its refusal surfaced only as a
> transient status message — an operator could believe a deletion had happened
> when it had not. The confirmation panel now shows the target record and keeps
> every refusal on screen until it is dealt with.

Every attempt — refusals, wrong passwords, and successful deletions with the
site, phantom, source name, SHA-256, both dates and whether a stored layout went
with it — is written to `logs/audit.log`. Nothing is soft-deleted.

Removing a phantom's stored measuring-point layout on its own
(`POST /api/phantom_profiles/forget`) is gated by the same administrator
password, because that layout is shared by every future scan of that phantom.

### Validation

Marking an analysis validated, conditionally validated or not validated uses the
same administrator password, and records the **name** of the person approving
plus an optional comment.

The password is shared, so it establishes only that someone entitled to sign off
did so. The typed name attributes the decision, and appears in the report, the
exports and the audit log. If the name must be proven rather than declared, that
requires per-user accounts.

### Not provided

- **TLS termination** — nginx does this.
- **Multi-user accounts or roles.** One shared user login, one administrator
  password. `audit.log` records the username used and the client address, so you
  can identify the machine but not the person.
- **Rate limiting on analysis endpoints.** An authenticated user can start any
  number of CPU-heavy analyses. Add nginx `limit_req` if this matters.
- **Antivirus scanning of uploads.** Files are parsed as DICOM or images and
  stored, never executed, but they are attacker-controlled input to pydicom and
  Pillow. Keep those dependencies patched.

---

## Upgrading

```bash
cd ~/apps/phantomqa
venv/bin/python -m phantom_qa.manage backup
git pull
venv/bin/pip install -r requirements.txt
venv/bin/python -m pytest tests -q                    # must pass
venv/bin/python -m phantom_qa.manage check            # must exit 0
sudo systemctl restart phantomqa
```

The database schema migrates itself on startup; existing analyses are preserved.
The first start after the upgrade that added the exposure columns also reads the
header of every stored DICOM once to fill them in — milliseconds per record, the
pixel data is not decoded — and logs how many it filled. Restarting this unit
does not affect other applications.

Rotating `PHANTOMQA_SECRET_KEY` signs everyone out, which is the fastest way to
invalidate all sessions.

See [MAINTENANCE.md](MAINTENANCE.md) for backups, integrity checks, re-analysis
after an upgrade, and log retention.
