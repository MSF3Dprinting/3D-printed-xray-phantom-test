# Deploying on a server

The app was built to run locally, but it is safe to expose on an internal
network or the internet **provided the steps below are followed**. Nothing here
is optional for an internet-facing deployment.

---

## 1. Configure secrets

Secrets live in `.env`, which is git-ignored. `.env.example` is the template.

```bash
cp .env.example .env
python -m phantom_qa.manage gen-secret      # -> paste into PHANTOMQA_SECRET_KEY
python -m phantom_qa.manage set-password    # -> paste into PHANTOMQA_PASSWORD_HASH
```

Then set in `.env`:

```ini
PHANTOMQA_ENV=production
PHANTOMQA_ALLOWED_HOSTS=phantomqa.yourdomain.org
```

If anyone should be able to delete analyses, also create the **administrator
password** — a second, different password:

```bash
python -m phantom_qa.manage set-admin-password   # -> PHANTOMQA_ADMIN_PASSWORD_HASH
```

Leaving it empty **disables deletion entirely**, which is the right default for
a shared installation.

Verify before starting:

```bash
python -m phantom_qa.manage check     # exit code 0 = no warnings
```

`PHANTOMQA_ENV=production` makes the app **refuse to start** without a secret
key, and **refuse** a plain-text password. That is deliberate: a
misconfiguration should stop the deployment, not quietly weaken it.

## 2. Put TLS in front of it

The app speaks plain HTTP and does **not** terminate TLS. Run it behind nginx,
Caddy or Traefik and let that handle certificates.

```nginx
server {
    listen 443 ssl http2;
    server_name phantomqa.yourdomain.org;

    ssl_certificate     /etc/letsencrypt/live/phantomqa.yourdomain.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/phantomqa.yourdomain.org/privkey.pem;

    client_max_body_size 210M;         # must exceed PHANTOMQA_MAX_UPLOAD_MB

    location / {
        proxy_pass         http://127.0.0.1:8777;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 900s;           # analysis of a large DICOM is slow
    }
}
server {                                   # redirect plain HTTP
    listen 80;
    server_name phantomqa.yourdomain.org;
    return 301 https://$host$request_uri;
}
```

Keep the app bound to loopback so it can only be reached through the proxy:

```bash
PHANTOMQA_HOST=127.0.0.1 python run_app.py 8777
```

## 3. Run it as a service

```ini
# /etc/systemd/system/phantomqa.service
[Unit]
Description=MSF Phantom QA
After=network.target

[Service]
Type=simple
User=phantomqa
Group=phantomqa
WorkingDirectory=/opt/phantomqa
Environment=PHANTOMQA_HOST=127.0.0.1
ExecStart=/opt/phantomqa/venv/bin/python run_app.py 8777
Restart=on-failure

# hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/phantomqa/data
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
```

Then `chmod 600 .env` and make sure `data/` is owned by the service user.

---

## What the app does for itself

| Control | Implementation |
|---|---|
| **Authentication** | Username + password; password stored only as a PBKDF2-HMAC-SHA256 hash (240 000 rounds, per-password salt). Plain-text passwords are rejected in production. |
| **Sessions** | HMAC-SHA256-signed cookie, `HttpOnly`, `SameSite=Strict`, `Secure` when `PHANTOMQA_HTTPS_ONLY=true`. Tampering with the payload invalidates the signature. Expiry is inside the signed payload (default 12 h). |
| **Brute force** | Per-client throttle: after `PHANTOMQA_MAX_LOGIN_ATTEMPTS` failures (default 8) that client is locked out for `PHANTOMQA_LOCKOUT_MINUTES` (default 15) and gets HTTP 429. |
| **CSRF** | Double-submit token: a non-`HttpOnly` cookie the frontend echoes in `X-CSRF-Token`. Every `POST`/`PUT`/`PATCH`/`DELETE` is rejected with 403 unless the two match. |
| **Host header** | `PHANTOMQA_ALLOWED_HOSTS` allow-list; anything else gets 400. |
| **Upload size** | Requests over `PHANTOMQA_MAX_UPLOAD_MB` (default 200) are rejected with 413 before the body is read. |
| **Response headers** | `Content-Security-Policy` (self only; `data:` images for the inlined report charts), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy`, `Permissions-Policy`, and HSTS when HTTPS-only. |
| **API caching** | All `/api/` responses are `Cache-Control: no-store`, so results never sit in a shared proxy cache. |
| **Attack surface** | The interactive API docs (`/docs`, `/redoc`) and the OpenAPI schema are disabled. |
| **Deletion** | Requires a separate administrator password plus typing the analysis id; throttled harder than sign-in; disabled entirely when unconfigured. |
| **Integrity** | Every analysis stores a SHA-256 of its source file; reports re-check it (see [INTEGRITY.md](INTEGRITY.md)). |
| **Timing** | The username and password checks both always run before the result is combined, so a wrong username and a wrong password take the same time. |

## Protecting records from accidental deletion

Several people share one login, so an ordinary session must not be able to
destroy a record. Deleting an analysis (which also removes its stored source
file) needs **all** of the following:

1. **A separate administrator password** — `PHANTOMQA_ADMIN_PASSWORD_HASH`, not
   the everyday login. `manage check` warns if the two are the same, because
   then it protects nothing.
2. **The analysis id typed back** as confirmation. A misclick cannot satisfy it.
3. Attempts are **throttled harder than sign-in** (half the allowed attempts).

With no administrator password configured, deletion is **refused outright** and
the UI explains how to enable it. Everything — refusals, wrong passwords,
successful deletions with the site, phantom, source name and SHA-256 of what was
removed — is written to `logs/audit.log`.

Nothing is soft-deleted: when a deletion succeeds the row and the file are gone.
The audit log is the record that it happened, so keep it (see retention below)
and back up `data/` on a schedule that matches how much you could afford to lose.

## Logging

Three files under `PHANTOMQA_LOG_DIR` (default `logs/`), all size-rotated:

| File | Contents | Default retention |
|---|---|---|
| `phantomqa.log` | Every request (method, path, status, duration, client, user) and application activity | 10 × 10 MB |
| `errors.log` | Warnings and errors only, with tracebacks | 10 × 10 MB |
| `audit.log` | Who did what to the data: sign-ins, uploads, computes, label edits, integrity checks, deletions | 30 × 10 MB |

`audit.log` is what you consult after "who deleted that?" — it is written at
INFO regardless of `PHANTOMQA_LOG_LEVEL` and kept for longer. Lines are
greppable with a JSON tail:

```
2026-07-31 15:19:16Z event=delete outcome=ok user=qa client=10.0.0.4 \
  analysis=499b02d1e219 detail={"site":"Goma Hospital","phantom":"MSF-01",...}
```

```bash
grep 'event=delete' logs/audit.log            # every deletion attempt
grep 'outcome=denied' logs/audit.log          # failed admin/login attempts
grep 'event=verify.*FAILED' logs/audit.log    # integrity problems
```

Passwords, tokens and cookies are redacted before anything is written; the
redaction is recursive and covered by a test.

**Retention.** Logs contain site names, operator names and client IP addresses.
Treat them like `data/`: restrict permissions, include them in backup, and set a
retention period that matches local policy. Rotation caps disk use (default
~100 MB app + ~300 MB audit) but does not expire by date — use logrotate or a
scheduled cleanup if you need time-based deletion.

## What it deliberately does not do

- **No TLS termination** — use the reverse proxy. Rolling our own would be worse.
- **No multi-user accounts or roles.** There is one shared login plus one
  administrator password for deletion. `audit.log` records the *username used*
  and the client address, so with a shared account you can tell which machine
  did something but not which person. If you need real per-user accountability,
  that is a feature to add, not a config flag.
- **No rate limiting on analysis endpoints.** An authenticated user can start as
  many analyses as they like; each one is CPU-heavy. Fine for a small trusted
  team, not for untrusted users.
- **The in-process login throttle is per worker.** Run a single worker (the
  default) or move the throttle to shared storage before scaling out.

## Database upgrades

The schema migrates itself in place on startup: missing columns are added and
existing rows are preserved (`tests/test_store_labels.py` covers upgrading a
pre-labels database). Still take a copy of `data/phantom_qa.sqlite3` before an
upgrade — it is a single file, so a copy is the whole backup.

## Patient data

The uploaded DICOM files are kept under `data/uploads/` for traceability, and
the full header is stored in the database. **These are phantom scans, but the
headers still carry institution, device and operator fields.** Treat `data/` as
sensitive: restrict filesystem permissions, include it in your backup and
retention policy, and delete analyses you no longer need (the delete endpoint
removes both the database row and the stored file).

## Upgrade checklist

```bash
git pull
pip install -r requirements.txt          # review changes first
python -m pytest tests -q                # must be green
python -m phantom_qa.manage check        # must report no warnings
systemctl restart phantomqa
```

Rotating `PHANTOMQA_SECRET_KEY` signs everybody out — that is the fastest way to
invalidate all sessions if a laptop goes missing.
