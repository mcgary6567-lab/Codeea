# Deployment Guide

Production deployment of the Online Quran College Digital Operating System.
The reference target is **Ubuntu 22.04 / 24.04 LTS** with PostgreSQL 15+, uvicorn under systemd and nginx terminating
TLS. Windows Server notes are at the end.

## 0. Requirements

| Component | Minimum | Notes |
|---|---|---|
| CPU / RAM | 2 vCPU, 4 GB | 4 vCPU / 8 GB once you pass ~150 concurrent class sessions |
| Disk | 40 GB SSD | Recordings and uploads grow fastest; keep `storage/` on its own volume if you can |
| Python | 3.12+ (3.14 recommended) | The codebase targets 3.14 |
| Database | PostgreSQL 15+ | SQLite is for local development only |
| Reverse proxy | nginx 1.22+ | TLS termination, static files, rate limiting |
| OS packages | `python3-venv python3-dev build-essential libpq-dev nginx certbot python3-certbot-nginx git` | |

---

## 1. System user and directory layout

Never run the application as root.

```bash
sudo adduser --system --group --home /opt/oqc --shell /bin/bash oqc
sudo mkdir -p /opt/oqc/app /opt/oqc/storage /opt/oqc/backups
sudo chown -R oqc:oqc /opt/oqc
```

| Path | Contents |
|---|---|
| `/opt/oqc/app` | The application checkout |
| `/opt/oqc/app/.venv` | Virtual environment |
| `/opt/oqc/app/app/static` | Static assets served directly by nginx |
| `/opt/oqc/storage` | Uploads, recordings, exports, certificates, migration files, **backups** |
| `/opt/oqc/app/.env` | Configuration and secrets — mode `0600`, owner `oqc` |

The application resolves `storage_dir` from configuration; point it at `/opt/oqc/storage` so the data volume is
separate from the code.

---

## 2. Virtual environment

```bash
sudo -u oqc -H bash
cd /opt/oqc/app
git clone <your-repo-url> .
python3 -m venv .venv
.venv/bin/pip install --upgrade pip wheel
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install "psycopg[binary]" uvicorn[standard]
```

Verify the app imports cleanly before going further:

```bash
.venv/bin/python -c "from app.main import app; print('ok')"
```

---

## 3. PostgreSQL

```bash
sudo -u postgres createuser --pwprompt oqc
sudo -u postgres createdb --owner=oqc oqc_prod
```

Harden `pg_hba.conf` to `scram-sha-256` for local TCP connections and restart PostgreSQL. Keep the database on
`localhost` unless you have a managed instance, in which case require TLS (`?sslmode=require`).

---

## 4. `.env`

Copy `.env.example` and edit. This file holds every secret; it is never read into the database and is never displayed
in the admin console.

```ini
APP_NAME="Online Quran College OS"
APP_ENV=production
SECRET_KEY=<64 random hex characters>
DATABASE_URL=postgresql+psycopg://oqc:<password>@localhost:5432/oqc_prod

HOST=127.0.0.1
PORT=8000
BASE_URL=https://os.onlinequrancollege.com

SESSION_HOURS=12
ACCESS_TOKEN_MINUTES=720
MAX_LOGIN_ATTEMPTS=5
LOCKOUT_MINUTES=15

BASE_CURRENCY=PKR
DEFAULT_TIMEZONE=Asia/Karachi
STORAGE_DIR=/opt/oqc/storage

VIDEO_PROVIDER=jitsi
JITSI_DOMAIN=meet.yourdomain.com

WHATSAPP_TOKEN=<cloud api token>
WHATSAPP_PHONE_ID=<phone number id>
GHL_API_KEY=<gohighlevel key>
SMTP_HOST=smtp.yourprovider.com
SMTP_PORT=587
SMTP_USER=<user>
SMTP_PASSWORD=<password>
SMTP_FROM=noreply@onlinequrancollege.com
AI_PROVIDER=<provider>
AI_API_KEY=<key>
```

```bash
sudo chown oqc:oqc /opt/oqc/app/.env
sudo chmod 600 /opt/oqc/app/.env
```

Generate the secret with `python -c "import secrets; print(secrets.token_hex(32))"`.
Leaving `SECRET_KEY` at its default invalidates every session on restart and is flagged in red on
`/admin/settings?tab=environment`.

---

## 5. Schema and bootstrap data

### Migrations (Alembic)

The schema is version-controlled with Alembic. `migrations/versions/` holds the baseline for v1.1 and every
change after it. Alembic reads `DATABASE_URL` from `.env` through `migrations/env.py`, so it always targets
the same database as the application.

```bash
cd /opt/oqc/app
.venv/bin/python -m alembic upgrade head          # create or update the schema
.venv/bin/python -m alembic current               # show the deployed revision
.venv/bin/python -m alembic history --verbose     # audit trail of schema changes
```

After changing a model, generate and review a revision before deploying it:

```bash
.venv/bin/python -m alembic revision --autogenerate -m "add teacher payout account"
# read the generated file in migrations/versions/ before committing - autogenerate
# does not detect every change (renames, server defaults, some constraint edits)
.venv/bin/python -m alembic upgrade head
```

`alembic downgrade -1` reverses the last revision. Always take a backup before a downgrade on production.

Upgrading an existing database that predates Alembic: run `alembic stamp head` once so Alembic records the
current revision without re-creating tables.

### Bootstrap data

`seed.py --core` loads only what production needs: the organisation, branches, departments, roles, the
bootstrap staff accounts, currencies, integration rows, notification templates and settings. It does **not**
load demo clients, students or classes. It also creates any missing tables, so it is safe on a fresh database,
but on a server run `alembic upgrade head` first so the schema is recorded as a revision.

```bash
cd /opt/oqc/app
.venv/bin/python -m alembic upgrade head
.venv/bin/python seed.py --core
```

It is idempotent — safe to re-run after a deploy that adds a role or a setting.

**Immediately after the first run:**

1. Sign in as `admin@oqc.local`, change the password and enrol in 2FA.
2. Change or deactivate every other seeded demo account you are not using.
3. Turn on **force 2FA for privileged roles** at `/admin/security/policy`.
4. Set the organisation profile, branches and departments at `/admin/settings`.
5. Take a backup and run a restore test on it.

---

## 6. systemd unit

`/etc/systemd/system/oqc.service`:

```ini
[Unit]
Description=Online Quran College Digital Operating System
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=oqc
Group=oqc
WorkingDirectory=/opt/oqc/app
EnvironmentFile=/opt/oqc/app/.env
ExecStart=/opt/oqc/app/.venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 --port 8000 --workers 4 \
    --proxy-headers --forwarded-allow-ips='127.0.0.1' \
    --timeout-keep-alive 30
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30

# hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=/opt/oqc/storage /opt/oqc/app/data
LimitNOFILE=8192

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now oqc
sudo systemctl status oqc
journalctl -u oqc -f
```

### A note on workers and background jobs

The scheduler starts inside the application process. With `--workers 4` each worker would start its own scheduler and
run every job four times. Pick one:

- **Simplest:** run `--workers 1` and scale with a second machine behind the load balancer later.
- **Recommended:** run multiple workers and disable the in-process scheduler on all but one instance — deploy a second
  systemd unit (`oqc-jobs.service`) with `--workers 1` on a different port that is *not* in the nginx upstream, and set
  the scheduler off in the web workers.

Whichever you choose, confirm on `/admin/settings?tab=jobs` that each job's last run advances exactly once per interval.

---

## 7. nginx and TLS

`/etc/nginx/sites-available/oqc`:

```nginx
upstream oqc_app { server 127.0.0.1:8000; keepalive 32; }

limit_req_zone $binary_remote_addr zone=oqc_login:10m rate=10r/m;
limit_req_zone $binary_remote_addr zone=oqc_api:10m   rate=120r/m;

server {
    listen 80;
    server_name os.onlinequrancollege.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name os.onlinequrancollege.com;

    ssl_certificate     /etc/letsencrypt/live/os.onlinequrancollege.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/os.onlinequrancollege.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    client_max_body_size 64M;   # migration files and uploads

    # static assets straight from disk
    location /static/ {
        alias /opt/oqc/app/app/static/;
        expires 7d;
        access_log off;
    }

    # protected user storage - never expose the whole directory
    location /media/ {
        internal;
        alias /opt/oqc/storage/;
    }

    location = /login      { limit_req zone=oqc_login burst=5 nodelay; proxy_pass http://oqc_app; include proxy_params; }
    location /api/v1/auth/ { limit_req zone=oqc_login burst=5 nodelay; proxy_pass http://oqc_app; include proxy_params; }
    location /api/         { limit_req zone=oqc_api burst=60 nodelay;  proxy_pass http://oqc_app; include proxy_params; }

    location / {
        proxy_pass http://oqc_app;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/oqc /etc/nginx/sites-enabled/oqc
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d os.onlinequrancollege.com
```

`--proxy-headers` on uvicorn plus `X-Forwarded-For` from nginx is what makes the real client IP appear in the audit
log and in the per-user IP allowlist check. Without it every event is logged as coming from `127.0.0.1`.

### Storage and downloads

Application-generated files (backups, subject-access exports, migration error CSVs) are served through authenticated
routes, not from a public path. **Never** add a plain `location /storage/` alias — that would expose recordings and
personal data to anyone with the URL. The `internal` `/media/` block above exists only for `X-Accel-Redirect` if you
later choose to offload large recording downloads to nginx.

---

## 8. Backups

The admin console writes archives into `<storage>/backups` and prunes beyond the retention count. Add a nightly cron
that takes a PostgreSQL dump as well and pushes everything off-site.

`/etc/cron.d/oqc-backup`:

```cron
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin

# 02:15 - PostgreSQL dump
15 2 * * * oqc pg_dump --format=custom --no-owner "$DATABASE_URL" > /opt/oqc/storage/backups/oqc-$(date +\%Y\%m\%d).dump 2>> /var/log/oqc-backup.log

# 02:30 - push database dumps and the storage tree off-site
30 2 * * * oqc rclone sync /opt/oqc/storage remote:oqc-backups/storage >> /var/log/oqc-backup.log 2>&1

# 03:00 - drop dumps older than 30 days
0 3 * * * oqc find /opt/oqc/storage/backups -name '*.dump' -mtime +30 -delete
```

Put the connection string in `/etc/cron.d/oqc-backup` via a `DATABASE_URL=` line, or read it from `.env` in a wrapper
script — do not inline the password anywhere world-readable.

**Weekly, without exception:** open `/admin/backups`, run a restore test on the newest archive, and confirm it passes.
Once a quarter, do a full rehearsal into a scratch database following `/admin/backups/runbook`. Record the RPO and RTO
you actually achieved against the configured targets.

---

## 9. Environments

Run three, all from the same repository and the same deployment procedure. What differs is the `.env` and the data.

| | Development | Staging | Production |
|---|---|---|---|
| Host | Developer laptop | `staging.<domain>` | `os.<domain>` |
| `APP_ENV` | `development` | `staging` | `production` |
| Database | SQLite `data/oqc.db` | PostgreSQL `oqc_staging` | PostgreSQL `oqc_prod` |
| Seed | `seed.py --reset` (full demo data) | Anonymised production restore | `seed.py --core` only |
| Integrations | All simulated (blank secrets) | Sandbox credentials | Live credentials |
| Access | Local only | HTTP basic auth on nginx, or IP allowlist | Public, TLS, 2FA enforced |
| `robots.txt` | n/a | `Disallow: /` | Standard |

**Never point staging at production credentials.** A staging WhatsApp token sends real messages to real parents. When
refreshing staging from a production backup, restore into `oqc_staging` and then anonymise: clear
`WHATSAPP_TOKEN`, `GHL_API_KEY` and `SMTP_HOST` in the staging `.env` so every channel drops back to simulation, and
run the anonymisation tool over any family whose data does not need to be real.

Developers work against SQLite, but **never write SQLite-only SQL** — every query goes through the ORM so the same
code runs on PostgreSQL unchanged.

---

## 10. Deploying a new version

```bash
sudo -u oqc -H bash
cd /opt/oqc/app

# 1. record the rollback point
git rev-parse HEAD > /opt/oqc/last-good-sha
# and take a backup from /admin/backups, or:
pg_dump --format=custom --no-owner "$DATABASE_URL" > /opt/oqc/storage/backups/pre-deploy-$(date +%Y%m%d-%H%M).dump

# 2. pull and install
git fetch --all
git checkout <tag-or-sha>
.venv/bin/pip install -r requirements.txt

# 3. schema and bootstrap data (idempotent)
.venv/bin/python seed.py --core

# 4. sanity check before restarting
.venv/bin/python -c "from app.main import app; print('import ok')"

exit
sudo systemctl restart oqc
curl -fsS https://os.onlinequrancollege.com/health && echo " health ok"
```

Then check `/admin/settings?tab=jobs` (jobs still running), `/admin/integrations` (health check passes) and
`/admin/audit` (new events arriving).

---

## 11. Rollback

The application code and the database roll back separately. Code first — it is fast and usually enough.

**Code only** (no schema change in the bad release):

```bash
sudo -u oqc git -C /opt/oqc/app checkout $(cat /opt/oqc/last-good-sha)
sudo -u oqc /opt/oqc/app/.venv/bin/pip install -r /opt/oqc/app/requirements.txt
sudo systemctl restart oqc
```

**Code and data** (the release corrupted or migrated data):

```bash
sudo systemctl stop oqc                     # freeze writes first
sudo -u postgres createdb --owner=oqc oqc_rollback
sudo -u oqc pg_restore -d oqc_rollback --no-owner /opt/oqc/storage/backups/pre-deploy-<stamp>.dump
sudo -u oqc psql -d oqc_rollback -c 'SELECT count(*) FROM users'   # sanity check
# point DATABASE_URL at oqc_rollback in .env, then:
sudo -u oqc git -C /opt/oqc/app checkout $(cat /opt/oqc/last-good-sha)
sudo systemctl start oqc
```

Keep the bad database — rename it rather than dropping it — so you can work out what happened. Restore the storage
tree too if the release touched uploads. Then follow the verification checklist on `/admin/backups/runbook` and write
the post-mortem within 48 hours.

If you must take the site down while you work, put nginx into maintenance mode:

```nginx
location / { return 503; }
error_page 503 /maintenance.html;
location = /maintenance.html { root /var/www/oqc-maintenance; internal; }
```

---

## 12. Monitoring

- `GET /health` — cheap liveness probe for the load balancer and uptime monitor.
- `journalctl -u oqc` — application logs; ship them to your log stack.
- `/admin/settings?tab=jobs` — background jobs must show a recent successful run.
- `/admin/integrations` — provider health; a degraded provider raises an `integration_down` notification.
- `/admin/security` — open incidents and failed-login trend.
- `/admin/backups` — the newest archive must be recent **and** restore-tested.

Alert on: the service being down, the `/health` endpoint failing, disk above 80%, no backup in 26 hours, and any
`critical` audit event.

---

## 13. Windows Server notes

The platform runs on Windows Server 2019/2022, and this is how the development machine is set up. The differences:

**Paths and shell.** Use `.venv\Scripts\python.exe` instead of `.venv/bin/python`. The console codepage is `cp1252`,
so avoid non-ASCII output in any script you run from `cmd.exe`; `seed.py` already reconfigures stdout to UTF-8.

**Run as a service.** There is no systemd. Use [NSSM](https://nssm.cc/) to wrap uvicorn:

```powershell
nssm install OQC "C:\oqc\app\.venv\Scripts\python.exe" "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers"
nssm set OQC AppDirectory C:\oqc\app
nssm set OQC AppStdout C:\oqc\logs\oqc.log
nssm set OQC AppStderr C:\oqc\logs\oqc-err.log
nssm set OQC Start SERVICE_AUTO_START
nssm start OQC
```

Environment variables come from the machine environment or a `.env` in `AppDirectory`. Note that uvicorn's
`--workers` uses `multiprocessing` on Windows; one worker is the reliable configuration.

**Reverse proxy.** Either install nginx for Windows with the same configuration as above, or use IIS with the
Application Request Routing and URL Rewrite modules. If you use IIS, enable `X-Forwarded-For` forwarding explicitly —
otherwise every audit event records the proxy's address.

**Backups.** Replace the cron entries with Task Scheduler jobs. A PowerShell wrapper is the least painful:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd'
& "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" --format=custom --no-owner $env:DATABASE_URL `
    | Out-File -Encoding byte "C:\oqc\storage\backups\oqc-$stamp.dump"
& "C:\tools\rclone\rclone.exe" sync C:\oqc\storage remote:oqc-backups/storage
```

Register it with `schtasks /create /tn OQC-Backup /tr "powershell -File C:\oqc\backup.ps1" /sc daily /st 02:15 /ru SYSTEM`.

**Permissions.** Run the service as a dedicated low-privilege account and grant it modify rights on the storage and
data directories only. Do not run it as `LocalSystem`.

**PostgreSQL.** The same guidance applies; install the Windows build and keep the data directory off the system drive.
SQLite on Windows also has a practical gotcha for developers: the file is locked while the app is running, so
`seed.py --reset` will refuse to delete it. Stop the server first, or point `DATABASE_URL` at a private database file
while you work.
