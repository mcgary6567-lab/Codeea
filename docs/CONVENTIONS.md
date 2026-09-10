# Online Quran College OS — Module Development Conventions

This document is the contract every module follows. Read it fully before writing code.

## Stack
- Python 3.14, FastAPI, SQLAlchemy 2 (declarative, `Mapped[]`), Jinja2 server-rendered pages, Tailwind (CDN), Alpine.js, HTMX, Chart.js, Lucide icons.
- SQLite locally (`data/oqc.db`), PostgreSQL on the server via `DATABASE_URL`. **Never use SQLite-only SQL.** Use SQLAlchemy ORM / `func`.
- Run the app: `.venv/Scripts/python.exe run.py` (Windows). Seed: `.venv/Scripts/python.exe seed.py --reset`.
- Test quickly with `fastapi.testclient.TestClient(app)` after logging in via `POST /login` (form: `username`, `password`). Always verify your pages return 200 and forms redirect 303.

## Project layout
```
app/main.py                 auto-discovers routers: every app/web/*.py and app/api/*.py exposing `router`
app/models/*.py             ALL models already exist (core, people, academic, scheduling, crm, finance, ops). Read them.
app/core/deps.py            get_db, get_current_user, get_user_context (UserContext), require("perm"), require_ceo, csrf_protect
app/core/rbac.py            permission strings, MODULES catalogue, role definitions, is_ceo/is_management
app/core/audit.py           log_action(db, actor, action, module, entity=..., description=..., rationale=..., before=..., after=..., request=...)
app/core/notify.py          notify(db, user_or_id, title, body, event_type=..., link=..., channels=("in_app",))
app/core/templating.py      render(request, "template.html", {...})  + filters: date, datetime, time, money, titleize, badge, mask, ago, pct
app/core/utils.py           next_code(db, Model, "field", "S-"), paginate(query, page, per_page) -> Page, redirect(url, flash, level), parse_date/int/float/bool, month_key, month_bounds
app/core/nav.py             sidebar items (URLs you must implement are listed here — do not rename them)
app/services/classes.py     shared class-session service (generate_sessions, set_status, mark_join, counters_for_date, has_conflict, teacher_stats, student_attendance_pct)
app/services/ai_gateway.py  ai(db, module, task, payload, entity) -> (result, AIModelRun). Simulated deterministic output on localhost.
app/services/integrations.py send_whatsapp, send_email, ghl_upsert_contact, emit_event (outbound webhooks), build_join_url
app/core/scheduler.py       background jobs: create app/services/jobs_<module>.py with JOBS = [("id", fn(db), interval_minutes)]
app/seed/<module>.py        seed data: def run(db). Idempotent (check before insert). Run order: core, academic, people, scheduling, crm, finance, hr, ops
app/templates/<module>/     your templates. Extend base.html, import macros: {% import "macros.html" as ui %}
```

## Routers
```python
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.core.deps import get_current_user, get_user_context, require, csrf_protect, UserContext
from app.core.templating import render
from app.core.utils import redirect, paginate, next_code, parse_date, parse_int
from app.core.audit import log_action
from app.database import get_db
from app.models.core import User

router = APIRouter(prefix="/students", dependencies=[Depends(csrf_protect)])   # web routers: keep csrf_protect

@router.get("", include_in_schema=False)
def list_students(request: Request, page: int = 1, q: str = "", db: Session = Depends(get_db), user: User = Depends(require("students.view"))):
    ...
    return render(request, "students/list.html", {"user": user, "page": pg, "q": q})

@router.post("/{id}/edit", include_in_schema=False)
async def edit(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("students.update"))):
    form = await request.form()          # read form fields with form.get("name")
    ...
    log_action(db, user, "update", "students", entity=obj, description="...", before=..., after=..., request=request)
    db.commit()
    return redirect(f"/students/{id}", "Student updated.")
```
- Web pages are `include_in_schema=False`. API routers (`app/api/*.py`) use `APIRouter(prefix="/students", tags=["students"])` and get mounted at `/api/v1`; use Pydantic models; auth via `Depends(get_current_user)` / `require(...)` (Bearer token or API key).
- **Permissions**: use the module keys in `app/core/rbac.py::MODULES` with actions view/add/update/approve/delete/execute/export/assign/configure. Every route must be guarded.
- **Record-level scoping**: `ctx: UserContext = Depends(get_user_context)` gives `ctx.teacher`, `ctx.client`, `ctx.student`, `ctx.employee`, `ctx.student_ids`. Teachers only see their own students/classes; clients only their own students/invoices; students only themselves. Raise `PermissionDenied` (from app.core.deps) or return 404 when out of scope.
- **Audit**: every create/update/delete/approve/status-change calls `log_action`. Consequential actions (approvals, refunds, discounts, grade/salary changes, schedule changes, safeguarding access, overrides) must include `rationale=` from a form field named `rationale` or `reason`.
- **Notifications**: use `notify()` for in-app; external channels via `channels=("in_app","whatsapp")` with `recipient_address=client.whatsapp`.
- **Money**: store Numeric; display with `|money(currency)`. Convert to base currency via `Currency.rate_to_base` (amount_in_base = amount * rate).
- **Cross-module services**: import other modules' services **lazily inside the function** (`from app.services.billing import post_credit`) so routers import even when another module isn't finished yet.
- Do NOT edit files outside your ownership list except: you may **append** new columns to models in `app/models/*.py` if truly needed (append-only, keep names descriptive; the DB is recreated with `seed.py --reset`), and you may add `SEED_MODULES` entries only if instructed.
- Do not modify `base.html`, `macros.html`, `nav.py`, `rbac.py`, `deps.py`, `main.py`. If a macro is missing, write the HTML inline in your template.

## Templates
```jinja
{% extends "base.html" %}
{% import "macros.html" as ui %}
{% block title %}Students{% endblock %}
{% block content %}
{% call ui.page_header("Students", "All enrolled learners") %}
  {{ ui.button("New student", href="/students/new", icon="plus") }}
{% endcall %}
<div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 mb-6">
  {{ ui.stat("Active", 120, icon="graduation-cap", color="emerald") }}
</div>
{% call ui.filter_bar('/students') %}
  {{ ui.input('q', 'Search', q) }}
  {{ ui.select('status', 'Status', ['active','trial','frozen','cancelled'], status, placeholder='All') }}
{% endcall %}
{{ ui.table_start(['Code','Name','Course','Teacher','Status','']) }}
{% for s in page.items %}
<tr><td class="px-4 py-2 font-mono text-xs">{{ s.student_code }}</td> ... <td>{{ ui.badge(s.status) }}</td>
<td class="px-4 py-2 text-right">{{ ui.button('Open', href='/students/' ~ s.id, variant='ghost', size='sm') }}</td></tr>
{% else %}{{ ui.empty('No students found.', 6) }}{% endfor %}
{{ ui.table_end() }}
{{ ui.pagination(page, '/students?q=' ~ q) }}
{% endblock %}
```
Available macros (`macros.html`): `page_header(title, subtitle, back)` (use `{% call %}` for action buttons), `stat(label, value, icon, color, hint, href, delta)`, `card(title, subtitle, padding)` (with `{% call %}`), `badge(value, label)`, `button(label, href, icon, variant, type, size, attrs)`, `input`, `textarea`, `select(name,label,options,value,required,placeholder,help)`, `checkbox`, `table_start(headers)`, `table_end()`, `empty(msg, colspan)`, `empty_state`, `pagination(page, base_url)`, `modal(id, title, size)` (open with `@click="$dispatch('open-modal','id')"`), `confirm_form(action, label, icon, variant, message, hidden)`, `dl([(label,value),...])`, `avatar(name)`, `progress(value, color, label)`, `tabs([(key,label,url)], current)`, `filter_bar(action)`, `alert(message, level, title)`, `chart(id, height)`, `arabic(text)`, `urdu(text)`.
- Charts: `{{ ui.chart('c1') }}` then in `{% block scripts %}<script>OQC.line('c1', {{ labels|tojson }}, [{label:'Revenue', data: {{ data|tojson }}}]);</script>{% endblock %}`. Also `OQC.bar`, `OQC.doughnut`.
- Forms: plain `<form method="post">` with `ui.input` etc. Detail pages use `ui.tabs`. Use `ui.modal` for quick-add forms. Colors: brand(teal), emerald, sky, indigo, amber, rose, violet, orange, slate.
- Icons: `<i data-lucide="icon-name" class="h-4 w-4"></i>` (Lucide names).
- Arabic / Urdu: `dir="rtl"` with `font-arabic` / `font-urdu` classes; Tajweed classes `tj-ghunna tj-ikhfa tj-idgham tj-qalqala tj-madd tj-iqlab`.
- Every list page: stats row, filter bar, table, pagination, a "New" action. Every detail page: header with status badges, tabs or cards, an activity/audit section where relevant. Every mutation: flash message + redirect.
- Pages must look finished and professional (dense, consistent spacing, no lorem ipsum, no placeholder text). Empty states must be handled.

## Demo accounts (seeded)
admin@oqc.local / Admin@12345 (super admin) · manager@oqc.local / Manager@123 · supervisor@oqc.local / Super@123 · teacher1..12@oqc.local / Teacher@123 · parent1..40@oqc.local / Parent@123 · student1..N@oqc.local / Student@123 · hr@oqc.local / People@123 · finance@oqc.local / Finance@123 · academics@oqc.local / Academ@123 · qa@oqc.local / Quality@123 · marketing@oqc.local / Market@123 · closer@oqc.local / Closer@123 · billing@oqc.local / Billing@123 · accountant@oqc.local / Account@123 · hrofficer@oqc.local / HROff@123 · qaofficer@oqc.local / QAOff@123 · coordinator@oqc.local / Coord@123 · auditor@oqc.local / Auditor@123

## Seed data expectations
Core seed gives: 7 departments, 20 roles, staff users, 6 currencies (base PKR), integrations, notification templates, settings.
Academic seed gives: 6 courses with divisions, 10 packages. People seed gives: 12 teachers (T-00001..) with users/employees, 18 staff employees, 40 clients (C-00001..) with parent users, ~50 students (S-00001..) assigned to teachers (statuses active/trial/cancelled) with risk scores.
Your seed must build on these (query them) and be idempotent. Make the data realistic and dense enough that every dashboard/chart shows meaningful numbers (e.g. 60–90 days of history).

## Definition of done (Appendix B of the SRS)
A module is complete only when it has: data model usage, permissions, workflows, validation, audit logging, notifications, API endpoints, error handling, reports/exports where relevant, seed data, and you have exercised every page and form with TestClient (200 on GET, 303 on POST, no template errors). Run `.venv/Scripts/python.exe -c "from app.main import app"` and fix any import errors before finishing.
