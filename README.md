# Online Quran College — Digital Operating System

A unified LMS + ERP + CRM + HRM + Finance + QA + AI-monitoring platform built from the
*Master Software Requirements Specification v1.1*. One trusted data layer for students, parents,
teachers, classes, curriculum, billing, HR, payroll, CRM/WhatsApp, quality assurance, retention,
marketing, governance and an executive command center.

## Quick start (localhost)

```bash
# 1. create the virtual environment and install dependencies (Python 3.11+)
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt      # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # Linux / macOS

# 2. configure (SQLite is used by default — nothing else to install)
copy .env.example .env

# 3. create the schema and load the demo dataset
.venv\Scripts\python.exe seed.py --reset

# 4. run
.venv\Scripts\python.exe run.py
```

Open **http://127.0.0.1:8000** and sign in.

| Portal | Account | Password |
|---|---|---|
| Super Admin / CEO | admin@oqc.local | Admin@12345 |
| Manager | manager@oqc.local | Manager@123 |
| Supervisor | supervisor@oqc.local | Super@123 |
| HOD Finance / People / Academics / QA / Marketing / Technology | finance@ · hr@ · academics@ · qa@ · marketing@ · tech@oqc.local | Finance@123 · People@123 · Academ@123 · Quality@123 · Market@123 · Tech@123 |
| Lead Closer / Billing Rep / Accountant | closer@ · billing@ · accountant@oqc.local | Closer@123 · Billing@123 · Account@123 |
| Teacher portal | teacher1@oqc.local … teacher12@oqc.local | Teacher@123 |
| Parent portal | parent1@oqc.local … parent40@oqc.local | Parent@123 |
| Student portal | student1@oqc.local … | Student@123 |
| External auditor (read-only) | auditor@oqc.local | Auditor@123 |

`start.bat` (Windows) / `start.sh` (Linux) run steps 3–4 for you.

## What is inside

| Area | Modules (SRS §6) |
|---|---|
| Overview | Academic Home Dashboard (live counters), CEO Command Center, Supervisor live board, alerts |
| CRM & Growth | Leads & pipeline with AI scoring, WhatsApp shared inbox, campaigns & marketing analytics, sequences, trials, Ambassador referral programme, feedback & NPS surveys, cases/complaints with SLA, retention & churn |
| Clients & Students | Client/parent management, students with teacher-match at enrolment, online registration, parent portal, student portal |
| Academics | 24h × 30-min scheduling with conflict detection, class sessions & browser classroom (Jitsi WebRTC), courses/packages, curriculum (books → chapters → lessons with Quranic text), lesson plans, evaluations, monthly tests & bilingual result cards, dor (revision) quota, certificates with public verification, shared Arabic lesson view with Tajweed colours |
| Quality & AI | QA queue & scorecards, AI class monitoring (camera/punctuality/engagement/coverage/conduct), recordings with access logs, safeguarding & anti-poaching flags (CEO-only), AI governance (model runs, confidence, human review, cost) |
| Finance | Subscriptions with discount ladder (0–20 % manager, 21–35 % CEO, >35 % blocked) and teacher-cost pricing floor, scholarships, invoices/receipts (PDF), payments & reconciliation, client ledger & credits, chart of accounts, journal, P&L, cash flow, aging, budgets, expenses, financial close, multi-currency |
| People & Culture | Teachers, employees, twice-daily attendance, leaves, recruitment pipeline, payroll & payslips, violations, confidential grievances, teacher grading (A/B/C → salary bands) & Ustaadh Lab, one-action onboarding/offboarding provisioning |
| Operations | Tasks/projects/sprints, KPI framework & scorecards, Transformation OS tracker, decision register, structured daily reports, reports & XLSX exports |
| System | Users, roles & permission matrix, settings, notification templates & delivery log, integration hub (WhatsApp Cloud API, GHL, n8n, SMTP, Zoom, Google, Meta/Google Ads, payments, Slack), API keys & webhooks, security center (2FA, sessions, lockouts, IP allowlist, incidents), backups & DR, data migration wizard, immutable audit log |

All external integrations run in **simulation mode** until credentials are set in `.env`, so every
workflow works on localhost.

## Documentation

| File | Contents |
|---|---|
| `docs/SRS_COVERAGE.md` | Every SRS module and section mapped to what was built, plus known limits |
| `docs/ADMIN_GUIDE.md` | Administering users, roles, settings, integrations, backups, migration, security |
| `docs/DEPLOYMENT.md` | Production deployment on Linux (PostgreSQL, systemd, nginx, TLS, backups) and Windows Server |
| `docs/API.md` | REST authentication, endpoints, inbound/outbound webhooks, curl examples |
| `docs/CONVENTIONS.md` | Code conventions and the module contract used to build the platform |

## Verification

```bash
.venv\Scripts\python.exe -m pytest tests\            # 239 automated tests
.venv\Scripts\python.exe tests\smoke_all.py          # crawls 394 pages as all 19 roles
```

Current state: **239 tests passing**, and every one of the 394 GET routes serves cleanly for all
nineteen seeded roles. The demo dataset carries 1,763 class sessions, 253 invoices, 229 payments,
100 leads, 90 monthly tests, 2,700 HR attendance records, 3 payroll runs, 64 KPIs and 1,391 audit
events, so every dashboard and chart shows real numbers. The double-entry journal balances exactly,
both in aggregate and per entry.

## Architecture

* **Backend**: FastAPI, SQLAlchemy 2.0, Pydantic; REST API at `/api/v1` (docs at `/api/docs`).
* **Frontend**: server-rendered Jinja2 pages, Tailwind CSS, Alpine.js, HTMX live partials, Chart.js, Lucide icons; PWA manifest.
* **Database**: SQLite for local use, PostgreSQL in production (`DATABASE_URL`). 117 tables, all defined in `app/models/`.
* **Security**: bcrypt passwords, JWT sessions with server-side revocation, TOTP 2FA, lockouts, CSRF (origin checks), RBAC with module × action permissions and record-level scoping, sensitive-data masking, immutable audit trail.
* **Automation**: APScheduler background jobs (reminders, auto-missed classes, invoicing, SLA escalation, churn scoring, sequences, payroll, backups, webhooks).
* **AI**: provider-agnostic gateway; every result records model/version/prompt/confidence and a human review status.

```
app/
  main.py          application factory, router auto-discovery, error pages
  config.py        settings from .env
  database.py      engine / session
  models/          core, people, academic, scheduling, crm, finance, ops
  core/            security, rbac, deps, audit, notify, templating, nav, utils, scheduler
  services/        business logic (classes, billing, payroll, crm, qa, kpi, ai_gateway, integrations, jobs_*)
  web/             HTML routers (one per module)
  api/             JSON routers (mounted at /api/v1)
  templates/       Jinja2 pages, base.html + macros.html design system
  static/          css/js/img
  seed/            idempotent demo data per module
docs/              CONVENTIONS, ADMIN_GUIDE, DEPLOYMENT, API
storage/           generated PDFs, recordings, uploads, backups
tests/             pytest suite
```

## Deployment

See `docs/DEPLOYMENT.md`. In short: set `APP_ENV=production`, a strong `SECRET_KEY`, a PostgreSQL
`DATABASE_URL`, run `python seed.py --core` (roles, currencies, settings only), and serve with
`uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2` behind nginx/TLS.
