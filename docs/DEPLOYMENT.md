# Deploying on a Linux server

Target: a Linux VM running **nginx** in front of **gunicorn**, served from a
sub-path such as `https://something.example.org/x-ray/`.

**This server already runs other applications.** Everything below is written to
be additive: a new `location` block inside the existing nginx site, its own
systemd unit, its own port, running as the account you already use. Nothing
here modifies global nginx settings or touches another app's configuration. The
two things to get right are the **port** (must be free) and the **location
prefix** (must not overlap another app's).

Nothing here is optional for an internet-facing deployment. Run
`python -m phantom_qa.manage check` before going live — it exits non-zero if
anything important is missing.

---

## 1. Install

Deploy as the account you already use — no new system user is created. Adjust
the path to wherever you keep applications.

```bash
cd ~/apps                       # or wherever you deploy
git clone <repo> phantomqa
cd phantomqa
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/pip install gunicorn
```

`data/` and `logs/` are created on first run inside this directory, owned by
the same account. Nothing is written outside it.

### Pick a free port

Gunicorn binds to loopback only, but the port must not clash with the other
apps on this VM:

```bash
ss -ltnp | grep 127.0.0.1        # what is already listening
```

Choose an unused one (this guide uses **8777**) and set `PHANTOMQA_BIND`
accordingly in step 2.

## 2. Configure secrets

```bash
cp .env.example .env
chmod 600 .env
python -m phantom_qa.manage gen-secret          # -> PHANTOMQA_SECRET_KEY
python -m phantom_qa.manage set-password        # -> PHANTOMQA_PASSWORD_HASH
python -m phantom_qa.manage set-admin-password  # -> PHANTOMQA_ADMIN_PASSWORD_HASH
```

Minimum `.env` for this deployment:

```ini
PHANTOMQA_ENV=production
PHANTOMQA_ROOT_PATH=/x-ray
PHANTOMQA_ALLOWED_HOSTS=something.example.org
PHANTOMQA_BIND=127.0.0.1:8777          # the free port from step 1
PHANTOMQA_SECRET_KEY=<from gen-secret>
PHANTOMQA_PASSWORD_HASH=<from set-password>
PHANTOMQA_ADMIN_PASSWORD_HASH=<from set-admin-password>
```

`PHANTOMQA_ENV=production` makes the app **refuse to start** without a secret
key, and **refuse** a plain-text password. A misconfiguration should stop the
deployment, not quietly weaken it.

```bash
venv/bin/python -m phantom_qa.manage check      # must exit 0
```

## 3. The two privilege levels

| Level | Credential | Can do |
|---|---|---|
| **User** | `PHANTOMQA_USERNAME` + `PHANTOMQA_PASSWORD_HASH` | Sign in, upload, run the analysis wizard, edit labels, read reports, verify integrity, export |
| **Admin** | additionally `PHANTOMQA_ADMIN_PASSWORD_HASH` | **Delete** an analysis, **validate** one (validated / conditionally validated / not validated) |

There is one shared user login and one admin password. The admin password is
entered per action, not at sign-in — so an admin uses the app as an ordinary
user and only supplies it when deleting or signing off. Set the two passwords
**different**; `manage check` warns if they match, because then the admin gate
protects nothing.

`tests/test_authorization.py` enumerates every route and asserts which level it
enforces. It fails if a new endpoint is added without being classified, so the
matrix cannot silently drift.

## 4. Sub-path: `PHANTOMQA_ROOT_PATH`

Set `PHANTOMQA_ROOT_PATH=/x-ray`. The app then:

- injects `<base href="/x-ray/">` into the app and login pages, so the
  single-page frontend builds every URL under the mount (all its URLs are
  relative — a test enforces that);
- scopes the session and CSRF cookies to `Path=/x-ray/`, so a different app on
  the same domain never receives them;
- accepts requests whether or not nginx strips the prefix.

Both nginx styles work, so use the simpler one:

```nginx
# forwards /x-ray/api/... unchanged — the app strips the prefix itself
location /x-ray/ { proxy_pass http://127.0.0.1:8777; }
```

```nginx
# strips the prefix (note the trailing slash); also fine
location /x-ray/ { proxy_pass http://127.0.0.1:8777/; }
```

## 5. nginx — add two location blocks, change nothing else

The server already has a TLS site for `something.example.org` serving other
apps. **Add these two blocks inside that existing `server { ... }`.** Do not
create a second `server` block for the same name, and do not edit
`nginx.conf` — every setting below is scoped to this location only, so the
other apps are unaffected.

```nginx
    # ---- MSF Phantom QA -------------------------------------------------
    location /x-ray/ {
        proxy_pass         http://127.0.0.1:8777;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        client_max_body_size 210M;   # local to this block; must exceed
                                     # PHANTOMQA_MAX_UPLOAD_MB
        proxy_read_timeout   900s;   # a large DICOM takes minutes to analyse
        proxy_send_timeout   900s;
        proxy_buffering      off;    # reports stream out as multi-MB HTML
    }

    # /x-ray without the trailing slash would otherwise 404
    location = /x-ray { return 301 /x-ray/; }
    # ---------------------------------------------------------------------
```

```bash
sudo nginx -t && sudo systemctl reload nginx    # -t first: never reload a bad config
```

Points that matter when apps share a server:

- **`client_max_body_size` is set inside the location**, so raising it to 210 MB
  applies only to this app. Setting it at `server` or `http` level would raise
  the limit for everything else too.
- **Do not add `add_header` for CSP or HSTS here.** The app sets its own
  security headers. An `add_header` inside a `location` block *replaces* the
  whole inherited set, so adding one here would silently drop the headers the
  other apps rely on from the server block — and vice versa: if the server
  block already sets `add_header`, this location still gets the app's headers
  because they arrive in the proxied response, which nginx passes through.
- **The prefix must not overlap** another app's location. Check first:
  `grep -rn 'location' /etc/nginx/sites-enabled/ | grep -i 'x-ray\|/x'`.
- If HTTP→HTTPS redirection already exists for this domain, leave it alone.

## 6. gunicorn + systemd

`gunicorn.conf.py` in the repo sets uvicorn workers, a 900 s timeout (the
default 30 s would kill a worker mid-analysis), worker recycling to bound
memory growth from the image pipeline, and loopback binding.

Its own unit, running as **your existing account** — substitute your username
and the path you cloned into. The name `phantomqa` must not collide with an
existing unit (`systemctl list-units | grep phantomqa`).

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

# hardening — scoped to this service only
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

`ProtectHome` is deliberately **not** set: it would hide the home directory the
app is installed in. `ProtectSystem=strict` plus an explicit `ReadWritePaths`
already prevents writes anywhere except this app's own `data/` and `logs/`.

`MemoryMax=4G` caps this service alone, so a large analysis cannot starve the
other applications on the VM. Lower it if the box is small; raise it if 4 GB
proves tight for your image sizes.

### Workers and shared state

Gunicorn runs several worker processes. Two consequences were designed for:

- **The login/admin throttle is stored in SQLite, not process memory**, so all
  workers share one counter. An in-memory counter would have given an attacker
  `max_attempts × workers` guesses. A test asserts a second "worker" sees
  failures recorded by the first.
- The decoded-image cache is per worker, so a scan may be re-read once per
  worker. That costs a little I/O, nothing more.

SQLite handles this fine at QA-team concurrency (WAL mode, small writes). If
you ever outgrow it, the store is the only thing that would need replacing.

## 7. Verify the deployment

```bash
curl -sI  https://something.example.org/x-ray/login | head -1        # 200
curl -s   https://something.example.org/x-ray/api/analyses | head -1 # 401
curl -sI  https://something.example.org/x-ray/api/analyses | grep -i content-security
venv/bin/python -m phantom_qa.manage check
venv/bin/python -m pytest tests/test_authorization.py -q
```

The login page should render, the API should refuse anonymous access, and the
security headers should be present.

Then confirm the **other applications still work** — the point of keeping every
change scoped:

```bash
sudo nginx -t                       # config still valid
curl -sI https://something.example.org/<other-app>/ | head -1
systemctl status nginx --no-pager | head -3
```

---

## What the app enforces

| Control | Implementation |
|---|---|
| **Authentication** | Username + password; stored only as a PBKDF2-HMAC-SHA256 hash (240 000 rounds, per-password salt). Plain-text passwords rejected in production. |
| **Authorization** | Private by default: an explicit allow-list of five public paths, everything else needs a session. Admin actions additionally verify the admin password in the handler. |
| **Sessions** | HMAC-SHA256-signed cookie, `HttpOnly`, `SameSite=Strict`, `Secure` under HTTPS, scoped to the mount path. Expiry inside the signed payload (default 12 h). |
| **Brute force** | Shared per-client throttle: 8 failed sign-ins or 4 failed admin attempts lock that client out for 15 minutes (HTTP 429). |
| **CSRF** | Double-submit token; every `POST`/`PUT`/`PATCH`/`DELETE` is rejected without a matching `X-CSRF-Token`. |
| **Path handling** | The allow-list is compared against a normalised path — duplicate slashes, trailing slashes and the mount prefix cannot dodge it. |
| **Host header** | `PHANTOMQA_ALLOWED_HOSTS` allow-list; anything else gets 400. |
| **Upload size** | Rejected with 413 before the body is read. |
| **Response headers** | CSP, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, COOP, `Permissions-Policy`, HSTS under HTTPS. |
| **API caching** | All `/api/` responses are `Cache-Control: no-store`. |
| **Attack surface** | `/docs`, `/redoc` and the OpenAPI schema are disabled. |
| **Error handling** | Unhandled exceptions return a bare 500 — no traceback, no source paths. A corrupt or missing stored file answers 422/410 with an explanation. |
| **Deletion** | Admin password **plus** typing the analysis id; throttled; disabled entirely when unconfigured. |
| **Validation** | Admin password; records the approver's name and comment. |
| **Integrity** | SHA-256 per analysis, re-checked in every report ([INTEGRITY.md](INTEGRITY.md)). |
| **Logging** | Rotating app/error/audit logs with secrets redacted. |

## Protecting records from accidental deletion

Deleting an analysis (which also removes its stored source file) needs **all**
of: the administrator password, the analysis id typed back as confirmation, and
passing a throttle stricter than sign-in. With no administrator password
configured, deletion is refused outright and the UI explains how to enable it.
Every attempt — refusals, wrong passwords, successful deletions with the site,
phantom, source name and SHA-256 of what was removed — goes to `logs/audit.log`.

Nothing is soft-deleted: when a deletion succeeds, the row and the file are
gone. The audit log is the record that it happened.

## Validation sign-off

Marking an analysis validated / conditionally validated / not validated uses the
same administrator password, and additionally records the **name** of the person
approving plus an optional comment.

The name matters: the password is shared, so it establishes only that someone
entitled to sign off did so. The typed name is what attributes the decision, and
it appears in the report, the exports and the audit log. If your process needs
that name to be *proven* rather than declared, that requires per-user accounts.

A ruling can be changed or withdrawn; the audit log keeps the history including
the previous state, so a reversal is traceable.

## Logging

Three files under `PHANTOMQA_LOG_DIR` (default `logs/`), all size-rotated:

| File | Contents | Default retention |
|---|---|---|
| `phantomqa.log` | Every request (method, path, status, duration, client, user) and application activity | 10 × 10 MB |
| `errors.log` | Warnings and errors only, with tracebacks | 10 × 10 MB |
| `audit.log` | Who did what: sign-ins, uploads, computes, label edits, validation rulings, integrity checks, deletions | 30 × 10 MB |

```bash
grep 'event=delete'          logs/audit.log   # every deletion attempt
grep 'event=validation'      logs/audit.log   # every sign-off and reversal
grep 'outcome=denied'        logs/audit.log   # failed admin/login attempts
grep 'event=verify.*FAILED'  logs/audit.log   # integrity problems
```

Passwords, tokens and cookies are redacted before writing; the redaction is
recursive and covered by a test.

**Client IPs.** With `PHANTOMQA_BEHIND_PROXY=true` the app trusts
`X-Forwarded-For` for the throttle key and the logs. That is correct behind
nginx, and it is why `forwarded_allow_ips` in `gunicorn.conf.py` is limited to
`127.0.0.1` — a client must not be able to spoof the header directly.

**Retention.** Logs contain site names, operator names and client addresses.
Treat them like `data/`: restrict permissions, back them up, and set a retention
period. Rotation caps disk use (~100 MB app + ~300 MB audit) but does not expire
by date — use logrotate if you need time-based deletion.

## What lives in `data/`, and what to back up

Everything is created at runtime except the phantom definition. **Nothing in
`data/` needs to be in git**, and the `.gitignore` already excludes it:

| Path | What it is | In git? | Backup? |
|---|---|---|---|
| `data/phantom_definitions/*.json` | Calibrated phantom geometry — source, not runtime data | **yes** | with the code |
| `data/phantom_qa.sqlite3` | The analyses database | no | **yes** |
| `data/phantom_qa.sqlite3-wal` | SQLite write-ahead log | no | not on its own |
| `data/phantom_qa.sqlite3-shm` | SQLite shared-memory index | no | no |
| `data/uploads/*.bin` | The original scans, kept for traceability | no | **yes** |
| `logs/*` | Application, error and audit logs | no | per your retention policy |

The `-wal` and `-shm` files are created by SQLite whenever a connection is open
and removed again when the last one closes, so you will see them come and go as
the app is used. The database runs in **WAL mode** so gunicorn's workers can
read while another writes.

You never need to manage them:

- **Do not commit them** — already excluded by `.gitignore`.
- **Do not delete them while the app is running.** With it stopped they are
  gone anyway, and removing any leftovers loses nothing.
- **Do not copy the `.sqlite3` on its own while the app is live** — recent
  commits may still be in the `-wal`, so the copy would be stale. Use the
  backup command below, which handles this.

### Taking a backup

Use the built-in command, which produces a single self-contained file with the
app running:

```bash
cd ~/apps/phantomqa
venv/bin/python -m phantom_qa.manage backup data/backup/phantomqa-$(date +%F).sqlite3
tar czf ~/backups/phantomqa-$(date +%F).tar.gz \
        data/backup/phantomqa-$(date +%F).sqlite3 data/uploads .env
```

Or stop the service and copy everything, which is equally safe:

```bash
sudo systemctl stop phantomqa
tar czf ~/backups/phantomqa-$(date +%F).tar.gz data/ logs/ .env
sudo systemctl start phantomqa
```

The database alone is not enough — `data/uploads/` holds the scans the SHA-256
values refer to, so restore both or integrity checks will report missing files.

`manage checkpoint` folds the write-ahead log back into the main file if you
need the `.sqlite3` to be complete on its own for some other tool.

The schema migrates itself in place on startup, so an upgrade preserves existing
analyses — take the copy first anyway.

## Upgrading

```bash
cd ~/apps/phantomqa
venv/bin/python -m phantom_qa.manage backup           # first
git pull
venv/bin/pip install -r requirements.txt
venv/bin/python -m pytest tests -q                    # must be green
venv/bin/python -m phantom_qa.manage check            # must exit 0
sudo systemctl restart phantomqa
```

Restarting this unit does not touch the other applications on the VM.

Rotating `PHANTOMQA_SECRET_KEY` signs everybody out — the fastest way to
invalidate all sessions if a laptop goes missing.

## What it deliberately does not do

- **No TLS termination.** nginx does that.
- **No multi-user accounts or roles.** One shared user login, one admin
  password. `audit.log` records the username used and the client address, so
  you can tell which machine acted, not which person. The approver's name on a
  validation is typed, not authenticated.
- **No rate limiting on analysis endpoints.** An authenticated user can start as
  many analyses as they like, each CPU-heavy. Fine for a trusted QA team; if you
  need protection, add `limit_req` in nginx.
- **No antivirus scanning of uploads.** Files are parsed as DICOM/images and
  stored, never executed — but they are attacker-controlled input to pydicom and
  Pillow. Keep those dependencies patched.
