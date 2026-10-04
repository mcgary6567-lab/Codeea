# Deployment Guide

Production deployment of the Online Quran College Digital Operating System.
The reference target is **Ubuntu 22.04 / 24.04 LTS** with PostgreSQL 15+, uvicorn under systemd and nginx terminating
TLS. Windows Server notes are at the end.

## 0. Requirements

| Component | Minimum | Notes |
|---|---|---|
| CPU / RAM | 2 vCPU, 4 GB | 4 vCPU / 8 GB once you pass ~150 concurrent class sessions |
| Disk | 40 GB SSD | Recordings and uploads grow fastest; keep `storage/` on its own volume if you can |
| Python | 3.12 or 3.13 | `requirements.txt` skips the PostgreSQL driver (`psycopg`) on 3.14, which has no wheels yet; `runtime.txt` pins 3.12.7 for Render and the Docker image uses `python:3.12-slim` |
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

Storage is **always `<checkout>/storage`** (`/opt/oqc/app/storage`): the database stores file paths relative to the
repository root, so there is no `STORAGE_DIR` setting and the directory cannot be relocated. To keep the data volume
separate from the code, mount or symlink it at that path:

```bash
sudo rm -rf /opt/oqc/app/storage && sudo ln -s /opt/oqc/storage /opt/oqc/app/storage
```

The Docker, Railway and Fly configurations mount their volumes at `/app/storage` for the same reason.

---

## 2. Virtual environment

```bash
sudo -u oqc -H bash
cd /opt/oqc/app
git clone <your-repo-url> .
python3 -m venv .venv
.venv/bin/pip install --upgrade pip wheel
.venv/bin/pip install -r requirements.txt     # includes psycopg[binary] and uvicorn[standard] on 3.12/3.13
```

Verify the app imports cleanly before going further:

```bash
.venv/bin/python -c "from app.main import app; print('ok')"
```

Nothing has to be built on the server: the Tailwind stylesheet (`app/static/css/tailwind.css`) is compiled by the
developer with `build/build-css.sh` and committed, and Alpine, HTMX, Chart.js and Lucide are vendored under
`app/static/vendor/`. No Node, no CDN.

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
# storage is always <checkout>/storage (see section 1); there is no STORAGE_DIR setting
# SCHEDULER_ENABLED=true   set false only on web workers when a separate jobs process runs (section 6)
# COOKIE_SECURE=           leave unset: derived from APP_ENV (production => HTTPS-only session cookie)

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

**Revisions so far**, in order. Each parity pass is one revision.

| Revision | What it adds |
|---|---|
| `6d42e23b4b1a` | Baseline schema v1.1 (117 tables) |
| `dee1fc82982b` | Online Academics parity: session slots, academic configuration, client contacts and credentials, client requests, class arrangements, reschedule approvals, queries and activities, ledger additions, the QA call pipeline, plus ERP columns across clients, students, employees, leaves, courses, packages, books, evaluations, subscriptions, invoices, payments, class sessions, QA reviews, cases and feedback |
| `77ee14c26d59` | Human Resource parity: violation and bonus catalogues, holidays, grades, downloads, attachments, leave entitlements, attendance change requests, progress notes, employee requests, staff complaints, interview panels and the job application pipeline |
| `592b863b4bb0` | Accounts and Configuration parity: voucher fields on the journal entry, account heads and opening balances, lookups and lookup values, branch properties on settings, payment gateways, WhatsApp senders, support tickets, OTP configuration |
| `30408ac573e4` | Billing Management parity: balance limit, payment day and billing remarks on the family; lead verification columns; the lead closers catalogue |
| `2d78aefa0e3f` | Employee Self Portal parity: the employee ledger, draft/submitted on a progress note, collaborators on a task |
| `cd82be2cce8f` | Parity walk: contract end date, staff notices, QA feedback questions, Confido agent licences, devices and screenshots |
| `6eb79b7d42ee` | CRM automation: tags, contact tags, workflows, workflow runs, automation events |

**Autogenerating a revision.** Alembic emits `create_foreign_key(None, ...)`, which SQLite batch mode rejects
("Constraint must have a name"). After every `alembic revision --autogenerate`, run

```bash
.venv/Scripts/python.exe build/name_migration_fks.py migrations/versions/<new_file>.py
.venv/Scripts/python.exe build/add_server_defaults.py migrations/versions/<new_file>.py
```

The first names each foreign key `fk_<table>_<column>` and fills in the matching `drop_constraint` calls in the
downgrade. The second gives every `NOT NULL` column a server default read from the model's own `default=`.

**Both steps are required, and SQLite will not tell you if you skip the second one.** Adding a non-null column
to a table that already holds rows is the single most common way to break a deploy: SQLite's batch mode quietly
rebuilds the whole table, so the migration passes locally, while PostgreSQL runs a real `ALTER TABLE` and fails
with `NotNullViolation ... contains null values`. This happened on the first ERP parity deploy.

Then apply the revision to a scratch database and autogenerate once more: a second revision with no operations
in it proves the migration captures the models exactly.

## Seeding runs again on every deploy — prove it is idempotent

`render-build.sh` runs `seed.py` against the **existing production database** after the migration. Production
is never reset, so every seed module runs a second, third and fiftieth time over data it already created.
A module that assumes an empty table fails the build, and the service stays on the previous release.

**Before pushing, run the seed twice against the same database without `--reset`:**

```bash
.venv/Scripts/python.exe seed.py
.venv/Scripts/python.exe seed.py
```

The second run must finish with every module ticked and create nothing. `seed.py --reset` will not catch this:
a fresh database is the one case where a non-idempotent module works.

This is not hypothetical. The payroll seed listed the finished statuses it should skip (`approved`, `paid`).
When the ERP parity work added a `posted` status, a month posted by the previous deploy no longer matched, the
seed tried to regenerate it, and `generate_payroll` refused:

```
ValueError: Payroll for 2026-06 is already posted and cannot be regenerated
```

Two deploys failed on it before anyone looked, because a push that reports success has only reached GitHub.
The seed now asks `payroll.REGENERABLE_STATUSES` rather than keeping its own copy of the list.

**Check the deploy actually went live.** Watch the service's Events page, or compare the deployed stylesheet
against the local build — `curl -s https://oqc.onrender.com/static/css/tailwind.css | md5sum` should match
`md5sum app/static/css/tailwind.css` once the release is live.


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
    --host 127.0.0.1 --port 8000 --workers 1 \
    --proxy-headers --forwarded-allow-ips='127.0.0.1' \
    --timeout-keep-alive 30
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30

# hardening. ReadWritePaths only matters with ProtectSystem=strict; with "full" only /usr, /boot and /etc
# become read-only. If you switch to strict, add /opt/oqc/app/storage (the symlink target is listed already).
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=/opt/oqc/storage /opt/oqc/app/storage /opt/oqc/app/data
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
run every job four times (`max_instances=1` only de-duplicates inside one process). Pick one:

- **Simplest (the unit above):** run `--workers 1` and scale with a second machine behind the load balancer later.
- **More workers:** set `SCHEDULER_ENABLED=false` in the web unit's environment and run `--workers 4` there; deploy a
  second unit `oqc-jobs.service` — same `ExecStart` but `--workers 1`, another port that is *not* in the nginx upstream,
  and `Environment=SCHEDULER_ENABLED=true` — so exactly one process owns the jobs. The docker compose file does the same
  with its `jobs` profile.

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

Nothing under `<storage>` is a static mount. Every `/storage/<folder>/<file>` URL is answered by the guarded route in
`app/web/storage_files.py`, which normalises the path, refuses anything that resolves outside the storage directory,
and applies one rule per top-level folder (the table lives at the top of that module, `FOLDER_RULES`):

| Folder | Who may fetch it |
|---|---|
| `invoices/`, `receipts/` | `billing.view`, `payments.view` or `ledger.view`; otherwise the signed-in family the invoice or receipt belongs to (matched on the stored `pdf_path`) |
| `payslips/` | `payroll.view`; otherwise the employee the payslip is for (`Employee.user_id`) |
| `result_cards/` | `monthly_tests.view`, `evaluations.view` or `academics.view`; otherwise the student or the student's family |
| `certificates/` | public, no login — the `/verify` page links them by design |
| `attachments/`, `uploads/`, `downloads/`, `exports/`, `reports/`, `migration/` | any signed-in staff user (role portal `admin`); a family, student or employee only when an `Attachment` row marks the file as theirs |
| `backups/` | `backups.view` only |
| `agent_screenshots/` | always 404 here; `/config/agents/screenshots/{id}` is the only route |
| any other folder | `settings.view`; everyone else sees 404 |

Anonymous requests to a protected folder are redirected to `/login` (browsers) or answered 401 (API clients); refused
requests are written to the audit log as `storage / denied`. **Never** add a plain `location /storage/` alias in nginx —
that would put the whole tree back on the public internet. The `internal` `/media/` block above exists only for
`X-Accel-Redirect` if you later choose to offload large recording downloads to nginx.

---

## 8. Backups

The admin console writes archives into `<storage>/backups` and prunes beyond the retention count. Add a nightly cron
that takes a PostgreSQL dump as well and pushes everything off-site.

`/etc/cron.d/oqc-backup`:

```cron
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin

# 02:15 - PostgreSQL dump. pg_dump does not understand the "+psycopg" driver suffix the app uses in
# DATABASE_URL, so PG_URL below must be the plain form: postgresql://oqc:<password>@localhost:5432/oqc_prod
15 2 * * * oqc pg_dump --format=custom --no-owner "$PG_URL" > /opt/oqc/storage/backups/oqc-$(date +\%Y\%m\%d).dump 2>> /var/log/oqc-backup.log

# 02:30 - push database dumps and the storage tree off-site
30 2 * * * oqc rclone sync /opt/oqc/storage remote:oqc-backups/storage >> /var/log/oqc-backup.log 2>&1

# 03:00 - drop dumps older than 30 days
0 3 * * * oqc find /opt/oqc/storage/backups -name '*.dump' -mtime +30 -delete
```

Put the connection string in `/etc/cron.d/oqc-backup` via a `PG_URL=` line, or derive it from `.env` in a wrapper
script (`PG_URL="postgresql://${DATABASE_URL#postgresql+psycopg://}"`, as `upgrade.sh` does) — do not inline the
password anywhere world-readable.

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
# and take a backup from /admin/backups, or (plain postgresql:// URL, see section 8):
pg_dump --format=custom --no-owner "$PG_URL" > /opt/oqc/storage/backups/pre-deploy-$(date +%Y%m%d-%H%M).dump

# 2. pull and install
git fetch --all
git checkout <tag-or-sha>
.venv/bin/pip install -r requirements.txt

# 3. schema and bootstrap data (both idempotent; the migration must run before the seed)
.venv/bin/python -m alembic upgrade head
.venv/bin/python seed.py --core
.venv/bin/python deploy_secure.py          # no-op unless ADMIN_PASSWORD/DEMO_PASSWORD/ADMIN_EMAIL changed

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
# pg_dump needs the plain postgresql:// form, not the app's postgresql+psycopg:// URL
$pgUrl = $env:DATABASE_URL -replace '^postgresql\+psycopg://', 'postgresql://'
& "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" --format=custom --no-owner $pgUrl `
    --file "C:\oqc\storage\backups\oqc-$stamp.dump"
& "C:\tools\rclone\rclone.exe" sync C:\oqc\storage remote:oqc-backups/storage
```

Register it with `schtasks /create /tn OQC-Backup /tr "powershell -File C:\oqc\backup.ps1" /sc daily /st 02:15 /ru SYSTEM`.

**Permissions.** Run the service as a dedicated low-privilege account and grant it modify rights on the storage and
data directories only. Do not run it as `LocalSystem`.

**PostgreSQL.** The same guidance applies; install the Windows build and keep the data directory off the system drive.
SQLite on Windows also has a practical gotcha for developers: the file is locked while the app is running, so
`seed.py --reset` will refuse to delete it. Stop the server first, or point `DATABASE_URL` at a private database file
while you work.

**Demo laptop.** `install.ps1` at the repository root does the whole local setup (venv, requirements, `.env` with a
generated `SECRET_KEY`, SQLite seed, a Desktop shortcut to `start.bat`). It keeps the seed sign-ins because the laptop
is not public; `python run.py --host 0.0.0.0` with `APP_ENV=development` shows it over a LAN.

---

## 14. Containers and one-action installs

The same application runs unchanged in a container. One image (`Dockerfile`, `python:3.12-slim`, fonts for the
Arabic/Urdu PDFs, no Node) serves docker compose, Railway and Fly.io; Render keeps its native Python runtime
(`render.yaml`, `render-build.sh`). The README's *Deploy* section lists the one action per target.

### Bootstrap order (identical on every target)

| Step | Render (`render-build.sh`, build time) | Container (`deploy/entrypoint.sh`, every start) |
|---|---|---|
| Install dependencies | `pip install -r requirements.txt` | baked into the image |
| Wait for PostgreSQL | — | `python deploy/db_ready.py wait` |
| Migrate | `python -m alembic upgrade head` | same |
| Seed | `seed.py` / `seed.py --core` by `SEED_MODE` | only when `users` is empty (`SEED_ON_START=auto`) or `SEED_ON_START=true` |
| Credentials | `python deploy_secure.py` | same |
| Serve | `uvicorn ... --workers 1` | `uvicorn app.main:app --port $PORT --workers $WEB_CONCURRENCY --proxy-headers` |

Entrypoint switches: `SKIP_BOOTSTRAP=true` (a second container sharing the database, e.g. the `jobs` profile),
`ROTATE_CREDENTIALS=true` (force `deploy_secure.py`), `WEB_CONCURRENCY` (default 1). The container starts as root
only to `chown` the mounted volume to the `oqc` user, then re-executes itself as `oqc` (`gosu`).

### Credentials: `deploy_secure.py` applies only what changed

`deploy_secure.py` stores a sha256 fingerprint of `(ADMIN_PASSWORD, DEMO_PASSWORD, ADMIN_EMAIL)` in the settings
table (`credentials_fingerprint`, with the time it was applied). On every later run it compares first: if the
fingerprint is unchanged and `ROTATE_CREDENTIALS` is not `true`, it prints `credentials unchanged since <date>,
nothing to do` and exits without rewriting a password or revoking a session. So a redeploy never resets the
password the owner chose at first sign-in and never logs everyone out. **To rotate:** change the variable where
the host keeps it (Render: Environment tab, then redeploy; Docker: edit `.env`, `docker compose up -d`; Fly:
`fly secrets set`) — the next start applies it, forces a password change on the superusers and revokes all sessions.

### Storage

Always `<repo>/storage`: `/app/storage` in the image. Docker: named volume `storage`; Railway: volume mounted at
`/app/storage`; Fly: `[[mounts]]` at `/app/storage`; Render: ephemeral unless a Disk is mounted at
`/opt/render/project/src/storage` (paid instance; block commented in `render.yaml`); VPS: the checkout's
`storage/` (section 1). Never relocate it — stored paths are relative to the repository root.

### Scheduler and workers

One APScheduler per process, so `WEB_CONCURRENCY=1` everywhere by default (`numReplicas: 1` on Railway, one machine
on Fly, `--workers 1` on Render). To scale the web tier set `SCHEDULER_ENABLED=false` on the web containers and run
exactly one process with it on — compose profile `scale` (`COMPOSE_PROFILES=server,scale`) starts that `jobs`
container. A process with `SCHEDULER_ENABLED=false` logs `scheduler disabled` at start.

### HTTPS and the session cookie

`APP_ENV=production` sets the `Secure` flag on the session cookie, so production needs HTTPS or logins loop.
Docker: Caddy (`deploy/Caddyfile`) obtains Let's Encrypt certificates when `SITE_ADDRESS` is a domain; with
`SITE_ADDRESS=:80` (no domain) `install.sh` writes `APP_ENV=staging`. Render, Railway and Fly terminate TLS
themselves. `COOKIE_SECURE=true|false` overrides the derived value when you must.

### Backups, upgrades, smoke test

- Docker: the `backup` sidecar (`deploy/backup.sh`, postgres image) writes a daily `pg_dump --format=custom` into
  the `backups` volume, 30-day retention (`BACKUP_KEEP_DAYS`); `upgrade.sh` takes a pre-upgrade dump first. The app's
  own `/admin/backups` archive is only a JSON export on PostgreSQL. Restore: see the header of `deploy/backup.sh`.
- `upgrade.sh` works for both the Docker install and this guide's systemd install: records `.last-good-sha`, dumps,
  `git pull --ff-only`, rebuilds/migrates, restarts, then runs `deploy/smoke.sh <url>`.
- `deploy/smoke.sh <url>`: `GET /health?db=1` (status ok, database ok), the versioned stylesheet link on `/login`
  is served as `text/css` and matches the local `tailwind.css`, and `/storage/agent_screenshots/*` answers 404.
- `GET /health` reports `"commit"` from `GIT_COMMIT` (image build arg), `RENDER_GIT_COMMIT` or
  `RAILWAY_GIT_COMMIT_SHA`; `GET /health?db=1` adds a `SELECT 1` and answers 503 when the database is down.

### Environment variables added by this section

| Variable | Default | Read by |
|---|---|---|
| `SCHEDULER_ENABLED` | `true` | app (`app/config.py`) |
| `COOKIE_SECURE` | derived from `APP_ENV` | app |
| `GIT_COMMIT` | empty | app (`/health`), set by the Dockerfile build arg |
| `SEED_ON_START`, `ROTATE_CREDENTIALS`, `SKIP_BOOTSTRAP`, `WEB_CONCURRENCY` | `auto`, `false`, `false`, `1` | `deploy/entrypoint.sh` |
| `POSTGRES_PASSWORD`, `SITE_ADDRESS`, `BACKUP_KEEP_DAYS`, `COMPOSE_PROFILES` | — | `docker-compose.yml` |
