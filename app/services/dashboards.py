"""ERP-parity dashboards (docs/AUDIT_ACADEMICS.md 3.14).

Seven read-only dashboards: Client Management, Subscriptions (Count), Subscriptions (Amount), Billing Management,
Monthly Performance, Financial Summary and Monthly Performance Insights.

Every function here is pure: it takes a Session plus filter values and returns a plain dict of numbers, labels and
chart series shaped ``{"labels": [...], "data": [...]}``, so ``app/web/dashboards.py`` only renders and the tests
call the functions directly. Aggregation is done in Python over the filtered row sets (never SQLite-only SQL);
the row counts stay small at college scale.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Sequence

from sqlalchemy.orm import Session

from app.models.academic import Course
from app.models.core import User
from app.models.crm import Case, Lead
from app.models.finance import Currency, Invoice, LedgerEntry, Payment, Subscription
from app.models.people import (Bonus, Client, Employee, Leave, PayrollRun, SalaryAdvance, Student, Teacher,
                               Violation)
from app.models.scheduling import ClassSession, Trial

# --------------------------------------------------------------------------- vocabularies
# ERP client status filter (Black List - Drop Out - On-Leave - Pass Out - Regular - Trial) mapped onto the
# internal Client.status values. Keep the ERP order: it is the order the portal's select uses.
CLIENT_STATUS_OPTIONS = [("black_list", "Black List"), ("drop_out", "Drop Out"), ("on_leave", "On-Leave"),
                         ("pass_out", "Pass Out"), ("regular", "Regular"), ("trial", "Trial")]
CLIENT_STATUS_SETS = {
    "black_list": ("inactive", "black_list"),
    "drop_out": ("churned", "drop_out", "cancelled"),
    "on_leave": ("frozen", "on_leave"),
    "pass_out": ("graduated", "pass_out"),
    "regular": ("active", "regular"),
    "trial": ("trial",),
}

SUBSCRIPTION_STATUS_OPTIONS = [("regular", "Regular"), ("trial", "Trial"), ("freeze", "Freeze"),
                              ("cancelled", "Cancelled"), ("completed", "Completed")]
SUBSCRIPTION_STATUS_SETS = {
    "regular": ("active", "regular"),
    "trial": ("trial",),
    "freeze": ("frozen", "freeze"),
    "cancelled": ("cancelled",),
    "completed": ("expired", "completed"),
}

STUDENT_ACTIVE_SET = ("active", "regular")
SHIFTS = [("morning", "Morning"), ("night", "Night")]
DEPARTMENT_TABS = [("all", "Show All"), ("academics", "Academics"), ("marketing", "Marketing"),
                   ("billing", "Billing"), ("hr", "HR")]

MAX_MONTHS = 24
MAX_CHART_SLICES = 12


# --------------------------------------------------------------------------- small helpers
def _f(value) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _pct(part: float, whole: float, digits: int = 1) -> float:
    return round(100.0 * float(part) / float(whole), digits) if whole else 0.0


def _as_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


def _chart(counter: dict, limit: int = MAX_CHART_SLICES, sort: bool = True) -> dict:
    """A {label: value} mapping as a Chart.js-ready series, biggest first, tail merged into 'Other'."""
    items = list(counter.items())
    if sort:
        items.sort(key=lambda kv: (-float(kv[1] or 0), str(kv[0])))
    if limit and len(items) > limit:
        head, tail = items[:limit - 1], items[limit - 1:]
        items = head + [("Other", round(sum(float(v or 0) for _, v in tail), 2))]
    return {"labels": [str(k) for k, _ in items], "data": [round(float(v or 0), 2) for _, v in items]}


def _months_between(start: date, end: date) -> list[date]:
    out: list[date] = []
    cur = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cur <= last and len(out) < MAX_MONTHS:
        out.append(cur)
        cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
    return out or [date(end.year, end.month, 1)]


def _month_span(dates: Iterable[Optional[date]], date_from: Optional[date] = None,
                date_to: Optional[date] = None) -> list[date]:
    """Month buckets covering the data (or the explicit filter window when one was given)."""
    clean = [d for d in (_as_date(x) for x in dates) if d]
    start = date_from or (min(clean) if clean else date.today())
    end = date_to or (max(clean) if clean else date.today())
    if end < start:
        start, end = end, start
    months = _months_between(start, end)
    return months[-MAX_MONTHS:]


def _month_series(pairs: Sequence[tuple], months: Sequence[date]) -> dict:
    """``pairs`` of (date, value) bucketed into ``months`` -> {"labels", "data"}."""
    buckets = {(m.year, m.month): 0.0 for m in months}
    for d, value in pairs:
        d = _as_date(d)
        if d is None:
            continue
        key = (d.year, d.month)
        if key in buckets:
            buckets[key] = round(buckets[key] + float(value or 0), 2)
    return {"labels": [m.strftime("%b %Y") for m in months],
            "data": [round(buckets[(m.year, m.month)], 2) for m in months]}


def _weeks_between(start: date, end: date, limit: int = 14) -> list[date]:
    cur = start - timedelta(days=start.weekday())
    out: list[date] = []
    while cur <= end and len(out) < 60:
        out.append(cur)
        cur += timedelta(days=7)
    return (out or [start])[-limit:]


def _week_series(pairs: Sequence[tuple], weeks: Sequence[date]) -> dict:
    buckets = {w: 0.0 for w in weeks}
    for d, value in pairs:
        d = _as_date(d)
        if d is None:
            continue
        wk = d - timedelta(days=d.weekday())
        if wk in buckets:
            buckets[wk] = round(buckets[wk] + float(value or 0), 2)
    return {"labels": [w.strftime("%d %b") for w in weeks], "data": [round(buckets[w], 2) for w in weeks]}


def _shift_of(client: Optional[Client]) -> str:
    return (getattr(client, "shift", None) or "night").lower()


def _day_night(client: Optional[Client]) -> str:
    """The ERP splits its insight columns into Day (morning shift) and Night (night shift)."""
    return "day" if _shift_of(client) == "morning" else "night"


def _rates(db: Session) -> dict:
    return {c.code: float(c.rate_to_base or 1) for c in db.query(Currency).all()}


def _to_base(rates: dict, amount, currency: Optional[str]) -> float:
    return round(float(amount or 0) * float(rates.get((currency or "").upper(), 1.0)), 2)


def _base_code(db: Session) -> str:
    row = db.query(Currency).filter(Currency.is_base.is_(True)).first()
    return row.code if row else "PKR"


def _user_names(db: Session) -> dict:
    return {u.id: u.full_name for u in db.query(User).all()}


def _client_balances(db: Session) -> dict:
    """{client_id: balance} in the client's own currency (debit - credit), opening balance included."""
    out: dict[int, float] = defaultdict(float)
    for cid, debit, credit in db.query(LedgerEntry.client_id, LedgerEntry.debit, LedgerEntry.credit).all():
        out[cid] = round(out[cid] + float(debit or 0) - float(credit or 0), 2)
    for cid, opening in db.query(Client.id, Client.opening_balance).all():
        if opening:
            out[cid] = round(out[cid] + float(opening), 2)
    return dict(out)


def _balance_on(db: Session, client_ids: Optional[Sequence[int]], on: Optional[date]) -> float:
    """Total client receivable (base-currency-agnostic sum in ledger currency) up to and including ``on``."""
    rates = _rates(db)
    q = db.query(LedgerEntry)
    if client_ids is not None:
        if not client_ids:
            return 0.0
        q = q.filter(LedgerEntry.client_id.in_(list(client_ids)))
    if on:
        q = q.filter(LedgerEntry.entry_date <= on)
    total = 0.0
    for e in q.all():
        total += _to_base(rates, float(e.debit or 0) - float(e.credit or 0), e.currency)
    oq = db.query(Client)
    if client_ids is not None:
        oq = oq.filter(Client.id.in_(list(client_ids)))
    for c in oq.all():
        total += _to_base(rates, c.opening_balance, c.currency)
    return round(total, 2)


# --------------------------------------------------------------------------- shared select options
def options(db: Session) -> dict:
    """Select-list options shared by the dashboard filter bars."""
    from app.models.erp import ClientAcademicGroup
    clients = [(c.id, f"{c.client_code} - {c.full_name}") for c in
               db.query(Client).order_by(Client.client_code).all()]
    teachers = [(t.id, f"{t.teacher_code} - {t.full_name}") for t in
                db.query(Teacher).order_by(Teacher.teacher_code).all()]
    employees = [(e.id, f"{e.employee_code} - {e.full_name}") for e in
                 db.query(Employee).order_by(Employee.employee_code).all()]
    countries = sorted({c.country for c in db.query(Client).all() if c.country})
    states = sorted({c.state for c in db.query(Client).all() if c.state})
    currencies = sorted({c.code for c in db.query(Currency).filter(Currency.is_active.is_(True)).all()})
    groups = [(g.id, g.name) for g in db.query(ClientAcademicGroup).order_by(ClientAcademicGroup.name).all()]
    return {"clients": clients, "teachers": teachers, "employees": employees, "countries": countries,
            "states": states, "currencies": currencies, "groups": groups, "shifts": SHIFTS,
            "client_statuses": CLIENT_STATUS_OPTIONS, "subscription_statuses": SUBSCRIPTION_STATUS_OPTIONS,
            "departments": DEPARTMENT_TABS, "base": _base_code(db)}


# ============================================================================ 1. Client Management Dashboard
def client_dashboard(db: Session, date_from: Optional[date] = None, date_to: Optional[date] = None,
                     client_id: Optional[int] = None, country: str = "", shift: str = "",
                     status: str = "") -> dict:
    """ERP "Client Management" dashboard: registration tiles and nine breakdown charts."""
    q = db.query(Client)
    if date_from:
        q = q.filter(Client.joined_at >= date_from)
    if date_to:
        q = q.filter(Client.joined_at <= date_to)
    if client_id:
        q = q.filter(Client.id == client_id)
    if country:
        q = q.filter(Client.country == country)
    if shift:
        q = q.filter(Client.shift == shift)
    if status and status in CLIENT_STATUS_SETS:
        q = q.filter(Client.status.in_(CLIENT_STATUS_SETS[status]))
    clients = q.order_by(Client.id).all()
    ids = [c.id for c in clients]

    def tile(key: str) -> int:
        allowed = CLIENT_STATUS_SETS[key]
        return sum(1 for c in clients if (c.status or "") in allowed)

    tiles = {"regular": tile("regular"), "trial": tile("trial"), "on_leave": tile("on_leave"),
             "pass_out": tile("pass_out"), "drop_out": tile("drop_out"), "black_list": tile("black_list")}

    names = _user_names(db)
    status_label = {v: lbl for key, lbl in CLIENT_STATUS_OPTIONS for v in CLIENT_STATUS_SETS[key]}
    months = _month_span([c.joined_at for c in clients], date_from, date_to)

    gateway: Counter = Counter()
    if ids:
        pq = db.query(Payment).filter(Payment.client_id.in_(ids))
        if date_from:
            pq = pq.filter(Payment.received_at >= datetime.combine(date_from, datetime.min.time()))
        if date_to:
            pq = pq.filter(Payment.received_at <= datetime.combine(date_to, datetime.max.time()))
        for p in pq.all():
            gateway[(p.gateway or p.category or p.method or "Other").title()] += 1

    charts = {
        "month": _month_series([(c.joined_at, 1) for c in clients], months),
        "country": _chart(Counter((c.country or "Unknown") for c in clients)),
        "shift": _chart(Counter((c.shift or "night").title() for c in clients)),
        "status": _chart(Counter(status_label.get(c.status or "", (c.status or "Unknown").title()) for c in clients)),
        "state": _chart(Counter((c.state or "Not set") for c in clients)),
        "gateway": _chart(gateway),
        "currency": _chart(Counter((c.currency or "-") for c in clients)),
        "billing_rep": _chart(Counter(names.get(c.billing_rep_id, "Unassigned") for c in clients)),
        "academic_manager": _chart(Counter(names.get(c.academic_manager_id, "Unassigned") for c in clients)),
    }
    return {"total": len(clients), "tiles": tiles, "charts": charts, "client_ids": ids,
            "filters": {"date_from": date_from, "date_to": date_to, "client_id": client_id,
                        "country": country, "shift": shift, "status": status}}


# ============================================================================ 2/3. Subscriptions (count / amount)
def subscription_dashboard(db: Session, date_from: Optional[date] = None, date_to: Optional[date] = None,
                           teacher_id: Optional[int] = None, shift: str = "", status: str = "",
                           country: str = "", amount: bool = False) -> dict:
    """ERP "Subscriptions (Count)" and "Subscriptions (Amount)". ``amount`` sums price_in_base instead of counting."""
    q = db.query(Subscription)
    if date_from:
        q = q.filter(Subscription.start_date >= date_from)
    if date_to:
        q = q.filter(Subscription.start_date <= date_to)
    if teacher_id:
        q = q.filter(Subscription.teacher_id == teacher_id)
    if status and status in SUBSCRIPTION_STATUS_SETS:
        q = q.filter(Subscription.status.in_(SUBSCRIPTION_STATUS_SETS[status]))
    subs = q.order_by(Subscription.id).all()
    if shift:
        subs = [s for s in subs if _shift_of(s.client) == shift]
    if country:
        subs = [s for s in subs if (s.client.country if s.client else None) == country]

    def value(s: Subscription) -> float:
        return _f(s.price_in_base) if amount else 1.0

    def tile(key: str) -> float:
        allowed = SUBSCRIPTION_STATUS_SETS[key]
        total = sum(value(s) for s in subs if (s.status or "") in allowed)
        return round(total, 2) if amount else int(total)

    tiles = {"regular": tile("regular"), "trial": tile("trial"), "cancelled": tile("cancelled"),
             "completed": tile("completed")}

    names = _user_names(db)
    courses = {c.id: c.name for c in db.query(Course).all()}
    teachers = {t.id: t.full_name for t in db.query(Teacher).all()}
    status_label = {v: lbl for key, lbl in SUBSCRIPTION_STATUS_OPTIONS for v in SUBSCRIPTION_STATUS_SETS[key]}
    months = _month_span([s.start_date for s in subs], date_from, date_to)

    def group(keyfn) -> Counter:
        out: Counter = Counter()
        for s in subs:
            out[keyfn(s)] += value(s)
        return out

    charts = {
        "month": _month_series([(s.start_date, value(s)) for s in subs], months),
        "country": _chart(group(lambda s: (s.client.country if s.client else None) or "Unknown")),
        "status": _chart(group(lambda s: status_label.get(s.status or "", (s.status or "Unknown").title()))),
        "shift": _chart(group(lambda s: _shift_of(s.client).title())),
        "teacher": _chart(group(lambda s: teachers.get(s.teacher_id, "Unassigned"))),
        "supervisor": _chart(group(lambda s: names.get(s.supervisor_id, "Unassigned"))),
        "course": _chart(group(lambda s: courses.get(s.course_id, "Not set"))),
    }
    total = sum(value(s) for s in subs)
    return {"amount": amount, "count": len(subs), "total": round(total, 2) if amount else int(total),
            "tiles": tiles, "charts": charts, "base": _base_code(db),
            "filters": {"date_from": date_from, "date_to": date_to, "teacher_id": teacher_id, "shift": shift,
                        "status": status, "country": country}}


# ============================================================================ 4. Billing Management Dashboard
def billing_dashboard(db: Session, date_from: Optional[date] = None, date_to: Optional[date] = None,
                      teacher_id: Optional[int] = None, country: str = "", shift: str = "") -> dict:
    """ERP "Billing Management": business volume, client receivables, confirmed invoices, payroll and flow charts."""
    from app.services import billing as billing_svc

    today = date.today()
    date_to = date_to or today
    date_from = date_from or date(today.year, today.month, 1)
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    rates = _rates(db)
    base = _base_code(db)

    clients = db.query(Client).all()
    if country:
        clients = [c for c in clients if c.country == country]
    if shift:
        clients = [c for c in clients if _shift_of(c) == shift]
    client_ids = [c.id for c in clients]
    cid_set = set(client_ids)

    subs = db.query(Subscription).order_by(Subscription.id).all()
    subs = [s for s in subs if s.client_id in cid_set]
    if teacher_id:
        subs = [s for s in subs if s.teacher_id == teacher_id]

    regular = [s for s in subs if (s.status or "") in SUBSCRIPTION_STATUS_SETS["regular"]]
    cancelled = [s for s in subs if (s.status or "") in SUBSCRIPTION_STATUS_SETS["cancelled"]]
    volume = {"count": len(regular), "monthly_base": round(sum(_f(s.price_in_base) for s in regular), 2),
              "annual_base": round(sum(_f(s.price_in_base) for s in regular) * 12, 2),
              "avg_base": round(sum(_f(s.price_in_base) for s in regular) / len(regular), 2) if regular else 0.0}

    opening = _balance_on(db, client_ids, date_from - timedelta(days=1))
    closing = _balance_on(db, client_ids, date_to)
    receivables = {"opening": opening, "closing": closing, "difference": round(closing - opening, 2)}

    inv_q = db.query(Invoice).filter(Invoice.issue_date >= date_from, Invoice.issue_date <= date_to)
    invoices = [i for i in inv_q.all() if i.client_id in cid_set]
    confirmed_set = ("confirmed", "paid", "partial")
    confirmed = [i for i in invoices if (i.status or "") in confirmed_set]
    invoices_panel = {
        "confirmed_count": len(confirmed),
        "confirmed_base": round(sum(_to_base(rates, i.total, i.currency) for i in confirmed), 2),
        "all_count": len(invoices),
        "all_base": round(sum(_to_base(rates, i.total, i.currency) for i in invoices
                              if (i.status or "") not in billing_svc.INVOICE_CANCELLED_SET), 2),
        "paid_base": round(sum(_to_base(rates, i.paid_amount, i.currency) for i in invoices), 2),
    }

    payroll_rows = []
    cursor = date(date_to.year, date_to.month, 1)
    periods = []
    for _ in range(3):
        periods.append(cursor.strftime("%Y-%m"))
        cursor = date(cursor.year - 1, 12, 1) if cursor.month == 1 else date(cursor.year, cursor.month - 1, 1)
    runs = {r.period: r for r in db.query(PayrollRun).filter(PayrollRun.period.in_(periods)).all()}
    for p in reversed(periods):
        run = runs.get(p)
        payroll_rows.append({"period": p,
                             "label": datetime.strptime(p, "%Y-%m").strftime("%b %Y"),
                             "gross": _f(run.total_gross) if run else 0.0,
                             "net": _f(run.total_net) if run else 0.0,
                             "status": run.status if run else "not run",
                             "currency": (run.currency if run else base)})
    payroll = {"rows": payroll_rows, "total_net": round(sum(r["net"] for r in payroll_rows), 2),
               "currency": payroll_rows[0]["currency"] if payroll_rows else base}

    months = _months_between(min(date_from, date_to - timedelta(days=180)), date_to)
    flow = {
        "regular": _month_series([(s.start_date, 1) for s in regular], months),
        "cancelled": _month_series([(s.cancelled_at or s.end_date, 1) for s in cancelled], months),
        "labels": [m.strftime("%b %Y") for m in months],
    }
    return {"volume": volume, "receivables": receivables, "invoices": invoices_panel, "payroll": payroll,
            "flow": flow, "base": base, "clients": len(clients),
            "filters": {"date_from": date_from, "date_to": date_to, "teacher_id": teacher_id,
                        "country": country, "shift": shift}}


# ============================================================================ 5. Monthly Performance Dashboard
# key, label, department, kind ("count" | "amount")
PERFORMANCE_PANELS = [
    ("regular", "Regular", "academics", "count"),
    ("dropped", "Dropped", "academics", "count"),
    ("freeze", "Freeze", "academics", "count"),
    ("completed", "Completed Subscriptions", "academics", "count"),
    ("missed_classes", "Missed Classes", "academics", "count"),
    ("on_leave_students", "On leave Students", "academics", "count"),
    ("short_classes", "20 Mints Classes", "academics", "count"),
    ("no_activity", "NO Activity update", "academics", "count"),
    ("leads_generated", "Leads Generated", "marketing", "count"),
    ("leads_status", "Status Wise Leads", "marketing", "count"),
    ("leads_conversion", "Leads Conversion Status Wise", "marketing", "count"),
    ("lead_to_trial", "Converted (Lead to Trial)", "marketing", "count"),
    ("trial_to_dropped", "Converted (Trial to Dropped)", "marketing", "count"),
    ("trial_to_regular", "Converted (Trial to Regular)", "marketing", "count"),
    ("invoices_confirmed", "Invoices Confirmed", "billing", "amount"),
    ("receipts_confirmed", "Confirmed Receipts (Amount)", "billing", "amount"),
    ("ledger_additions", "Ledger Additions", "billing", "amount"),
    ("new_enrollment", "New Enrollment", "academics", "count"),
    ("inactive_employees", "In-Active Employees", "hr", "count"),
    ("change_requests", "Change Request", "academics", "count"),
    ("complaints", "Complaints", "academics", "count"),
    ("leave_requests", "Leave Request", "academics", "count"),
    ("employee_requests", "Employee Request", "hr", "count"),
    ("violations", "Violations", "hr", "count"),
    ("bonus", "Bonus", "hr", "amount"),
    ("advances", "Advances", "hr", "amount"),
]


def _in(d: Optional[date], start: date, end: date) -> bool:
    d = _as_date(d)
    return bool(d and start <= d <= end)


def monthly_performance(db: Session, start: Optional[date] = None, end: Optional[date] = None,
                        employee_ids: Optional[Sequence[int]] = None, department: str = "all") -> dict:
    """ERP "Monthly Performance Dashboard": one count-or-amount card plus a weekly mini bar for each metric."""
    from app.models.erp import LedgerAddition, TeacherChangeRequest, TimeChangeRequest

    today = date.today()
    end = end or today
    start = start or date(end.year, end.month, 1)
    if start > end:
        start, end = end, start
    department = (department or "all").lower()
    if department not in {k for k, _ in DEPARTMENT_TABS}:
        department = "all"
    employee_ids = [int(i) for i in (employee_ids or []) if i]
    emp_set = set(employee_ids)
    rates = _rates(db)
    base = _base_code(db)

    teacher_ids = set()
    user_ids = set()
    if emp_set:
        for t in db.query(Teacher).filter(Teacher.employee_id.in_(employee_ids)).all():
            teacher_ids.add(t.id)
        for e in db.query(Employee).filter(Employee.id.in_(employee_ids)).all():
            if e.user_id:
                user_ids.add(e.user_id)

    def keep_teacher(tid) -> bool:
        return (not teacher_ids) or (tid in teacher_ids)

    def keep_employee(eid) -> bool:
        return (not emp_set) or (eid in emp_set)

    def keep_user(uid) -> bool:
        return (not user_ids) or (uid in user_ids)

    data: dict[str, list[tuple]] = {}
    breakdowns: dict[str, dict] = {}

    subs = db.query(Subscription).all()
    data["regular"] = [(s.start_date, 1) for s in subs
                       if (s.status or "") in SUBSCRIPTION_STATUS_SETS["regular"]
                       and _in(s.start_date, start, end) and keep_teacher(s.teacher_id)]
    data["dropped"] = [(s.cancelled_at, 1) for s in subs
                       if (s.status or "") in SUBSCRIPTION_STATUS_SETS["cancelled"]
                       and _in(s.cancelled_at, start, end) and keep_teacher(s.teacher_id)]
    data["freeze"] = [(s.freeze_start, 1) for s in subs
                      if (s.status or "") in SUBSCRIPTION_STATUS_SETS["freeze"]
                      and _in(s.freeze_start, start, end) and keep_teacher(s.teacher_id)]
    data["completed"] = [((s.completion_date or s.end_date), 1) for s in subs
                         if (s.status or "") in SUBSCRIPTION_STATUS_SETS["completed"]
                         and _in(s.completion_date or s.end_date, start, end) and keep_teacher(s.teacher_id)]

    sessions = db.query(ClassSession).filter(ClassSession.date >= start, ClassSession.date <= end).all()
    sessions = [s for s in sessions if keep_teacher(s.teacher_id)]
    data["missed_classes"] = [(s.date, 1) for s in sessions if (s.status or "") == "missed"]
    data["short_classes"] = [(s.date, 1) for s in sessions
                             if (s.status or "") == "done" and (s.actual_duration_minutes or 0) <= 20]
    data["no_activity"] = [(s.date, 1) for s in sessions
                           if (s.status or "") == "done" and s.activity_updated_at is None]

    leaves = db.query(Leave).all()
    data["on_leave_students"] = [(l.start_date, 1) for l in leaves
                                 if l.person_type == "student" and _in(l.start_date, start, end)]
    data["leave_requests"] = [((l.apply_date or _as_date(l.created_at)), 1) for l in leaves
                              if l.person_type == "student" and _in(l.apply_date or l.created_at, start, end)]
    data["employee_requests"] = [((l.apply_date or _as_date(l.created_at)), 1) for l in leaves
                                 if l.person_type == "employee"
                                 and _in(l.apply_date or l.created_at, start, end)
                                 and keep_employee(l.employee_id)]

    leads = db.query(Lead).all()
    generated = [l for l in leads if _in(l.created_at, start, end)
                 and (keep_user(l.generator_id) or keep_user(l.assigned_to_id))]
    data["leads_generated"] = [(_as_date(l.created_at), 1) for l in generated]
    data["leads_status"] = list(data["leads_generated"])
    breakdowns["leads_status"] = dict(Counter((l.stage or "new").replace("_", " ").title() for l in generated))
    converted = [l for l in leads if _in(l.converted_at, start, end) and keep_user(l.assigned_to_id)]
    data["leads_conversion"] = [(_as_date(l.converted_at), 1) for l in converted]
    breakdowns["leads_conversion"] = dict(Counter((l.stage or "won").replace("_", " ").title() for l in converted))

    trials = db.query(Trial).all()
    data["lead_to_trial"] = [(_as_date(t.scheduled_at or t.created_at), 1) for t in trials
                             if t.lead_id and _in(t.scheduled_at or t.created_at, start, end)
                             and keep_teacher(t.teacher_id)]
    data["trial_to_dropped"] = [(_as_date(t.updated_at), 1) for t in trials
                                if (t.status or "") in ("lost", "no_show") and _in(t.updated_at, start, end)
                                and keep_teacher(t.teacher_id)]
    data["trial_to_regular"] = [(_as_date(t.updated_at), 1) for t in trials
                                if (t.status or "") == "converted" and _in(t.updated_at, start, end)
                                and keep_teacher(t.teacher_id)]

    invoices = db.query(Invoice).filter(Invoice.issue_date >= start, Invoice.issue_date <= end).all()
    data["invoices_confirmed"] = [(i.issue_date, _to_base(rates, i.total, i.currency)) for i in invoices
                                  if (i.status or "") in ("confirmed", "paid", "partial")]
    payments = db.query(Payment).all()
    data["receipts_confirmed"] = [((p.receipt_date or _as_date(p.received_at)), _f(p.amount_in_base))
                                  for p in payments
                                  if (p.status or "") in ("confirmed", "completed")
                                  and _in(p.receipt_date or p.received_at, start, end)]
    additions = db.query(LedgerAddition).filter(LedgerAddition.addition_date >= start,
                                                LedgerAddition.addition_date <= end).all()
    data["ledger_additions"] = [(a.addition_date, _f(a.lc_amount)) for a in additions if a.status == "confirmed"]

    students = db.query(Student).all()
    data["new_enrollment"] = [(s.join_date, 1) for s in students
                              if _in(s.join_date, start, end) and keep_teacher(s.teacher_id)]

    employees = db.query(Employee).all()
    data["inactive_employees"] = [((e.exit_date or _as_date(e.updated_at)), 1) for e in employees
                                  if (e.status or "") in ("resigned", "terminated", "inactive")
                                  and _in(e.exit_date or e.updated_at, start, end) and keep_employee(e.id)]

    changes = [(_as_date(r.created_at), 1) for r in db.query(TimeChangeRequest).all()
               if _in(r.created_at, start, end)]
    changes += [(_as_date(r.created_at), 1) for r in db.query(TeacherChangeRequest).all()
                if _in(r.created_at, start, end)]
    data["change_requests"] = changes

    data["complaints"] = [(_as_date(c.created_at), 1) for c in db.query(Case).all()
                          if (c.case_type or "") == "complaint" and _in(c.created_at, start, end)]

    data["violations"] = [(v.date, 1) for v in db.query(Violation).all()
                          if _in(v.date, start, end) and keep_employee(v.employee_id)]
    data["bonus"] = [(_as_date(b.created_at), _f(b.amount)) for b in db.query(Bonus).all()
                     if _in(b.created_at, start, end) and keep_employee(b.employee_id)]
    data["advances"] = [(_as_date(a.created_at), _f(a.amount)) for a in db.query(SalaryAdvance).all()
                        if _in(a.created_at, start, end) and keep_employee(a.employee_id)]

    weeks = _weeks_between(start, end)
    panels = []
    for key, label, dept, kind in PERFORMANCE_PANELS:
        if department != "all" and dept != department:
            continue
        pairs = data.get(key, [])
        total = sum(float(v or 0) for _, v in pairs)
        panels.append({"key": key, "label": label, "department": dept, "kind": kind,
                       "value": round(total, 2) if kind == "amount" else int(total),
                       "count": len(pairs), "series": _week_series(pairs, weeks),
                       "breakdown": breakdowns.get(key)})
    totals = {key: (round(sum(float(v or 0) for _, v in data.get(key, [])), 2) if kind == "amount"
                    else int(sum(float(v or 0) for _, v in data.get(key, []))))
              for key, _label, _dept, kind in PERFORMANCE_PANELS}
    return {"panels": panels, "totals": totals, "base": base, "week_labels": [w.strftime("%d %b") for w in weeks],
            "filters": {"start": start, "end": end, "employee_ids": employee_ids, "department": department}}


# ============================================================================ 6. Financial Summary
def financial_summary(db: Session, date_from: Optional[date] = None, date_to: Optional[date] = None,
                      client_id: Optional[int] = None, shift: str = "", currency: str = "",
                      group_id: Optional[int] = None, country: str = "", state: str = "",
                      status: str = "") -> dict:
    """ERP "Financial Summary": fifteen tiles and seven trend / breakdown charts over the filtered families."""
    from app.models.erp import LedgerAddition

    rates = _rates(db)
    base = _base_code(db)

    cq = db.query(Client)
    if client_id:
        cq = cq.filter(Client.id == client_id)
    if shift:
        cq = cq.filter(Client.shift == shift)
    if currency:
        cq = cq.filter(Client.currency == currency)
    if group_id:
        cq = cq.filter(Client.academic_group_id == group_id)
    if country:
        cq = cq.filter(Client.country == country)
    if state:
        cq = cq.filter(Client.state == state)
    if status and status in CLIENT_STATUS_SETS:
        cq = cq.filter(Client.status.in_(CLIENT_STATUS_SETS[status]))
    clients = cq.order_by(Client.client_code).all()
    ids = [c.id for c in clients]
    id_set = set(ids)

    inv_q = db.query(Invoice)
    if date_from:
        inv_q = inv_q.filter(Invoice.issue_date >= date_from)
    if date_to:
        inv_q = inv_q.filter(Invoice.issue_date <= date_to)
    invoices = [i for i in inv_q.all() if i.client_id in id_set and (i.status or "") not in ("cancelled", "void")]

    pay_q = db.query(Payment)
    payments = [p for p in pay_q.all() if p.client_id in id_set and (p.status or "") in ("confirmed", "completed")]
    if date_from:
        payments = [p for p in payments if (p.receipt_date or _as_date(p.received_at)) >= date_from]
    if date_to:
        payments = [p for p in payments if (p.receipt_date or _as_date(p.received_at)) <= date_to]

    subs = [s for s in db.query(Subscription).all() if s.client_id in id_set]
    regular_subs = [s for s in subs if (s.status or "") in SUBSCRIPTION_STATUS_SETS["regular"]]
    students = [s for s in db.query(Student).all() if s.client_id in id_set]

    balances = _client_balances(db)
    balances = {cid: bal for cid, bal in balances.items() if cid in id_set}
    outstanding_base = round(sum(_to_base(rates, bal, next((c.currency for c in clients if c.id == cid), base))
                                 for cid, bal in balances.items() if bal > 0), 2)

    invoiced_base = round(sum(_to_base(rates, i.total, i.currency) for i in invoices), 2)
    received_base = round(sum(_f(p.amount_in_base) or _to_base(rates, p.amount, p.currency) for p in payments), 2)
    pending_base = round(sum(_to_base(rates, float(i.total or 0) - float(i.paid_amount or 0), i.currency)
                             for i in invoices if float(i.total or 0) > float(i.paid_amount or 0)), 2)

    monthly_value = defaultdict(float)
    for s in regular_subs:
        monthly_value[s.client_id] += _f(s.price)
    exceeded = sum(1 for c in clients
                   if balances.get(c.id, 0.0) > 2 * max(monthly_value.get(c.id, 0.0), 0.01)
                   and monthly_value.get(c.id, 0.0) > 0)

    sub_client_ids = {s.client_id for s in subs}
    student_client_ids = {s.client_id for s in students}
    additions = db.query(LedgerAddition).all()
    if date_from:
        additions = [a for a in additions if a.addition_date >= date_from]
    if date_to:
        additions = [a for a in additions if a.addition_date <= date_to]
    additions = [a for a in additions if a.client_id in id_set and a.status == "confirmed"]

    tiles = {
        "invoices_amount": invoiced_base,
        "invoice_clients": len({i.client_id for i in invoices}),
        "pending_amount": pending_base,
        "outstanding_balance": outstanding_base,
        "received_amount": received_base,
        "total_clients": len(clients),
        "active_students": sum(1 for s in students if (s.status or "") in STUDENT_ACTIVE_SET),
        "regular_subscriptions": len(regular_subs),
        "clients_without_subscription": sum(1 for c in clients if c.id not in sub_client_ids),
        "exceeded_accounts": exceeded,
        "collection_efficiency": _pct(received_base, invoiced_base),
        "avg_revenue_per_client": round(received_base / len(clients), 2) if clients else 0.0,
        "clients_zero_students": sum(1 for c in clients if c.id not in student_client_ids),
        "ledger_addition": round(sum(_f(a.lc_amount) for a in additions if a.effect == "add"), 2),
        "ledger_deduction": round(sum(_f(a.lc_amount) for a in additions if a.effect == "minus"), 2),
    }

    months = _month_span([i.issue_date for i in invoices] + [(p.receipt_date or _as_date(p.received_at))
                                                             for p in payments], date_from, date_to)
    invoice_trend = _month_series([(i.issue_date, _to_base(rates, i.total, i.currency)) for i in invoices], months)
    collection_trend = _month_series([((p.receipt_date or _as_date(p.received_at)),
                                       _f(p.amount_in_base) or _to_base(rates, p.amount, p.currency))
                                      for p in payments], months)
    efficiency_trend = {"labels": invoice_trend["labels"],
                        "data": [_pct(c, i) for c, i in zip(collection_trend["data"], invoice_trend["data"])]}

    top = sorted(((c, balances.get(c.id, 0.0)) for c in clients), key=lambda kv: -kv[1])[:10]
    top = [(c, bal) for c, bal in top if bal > 0]
    sub_status_label = {v: lbl for key, lbl in SUBSCRIPTION_STATUS_OPTIONS for v in SUBSCRIPTION_STATUS_SETS[key]}

    charts = {
        "invoice_trend": invoice_trend,
        "collection_trend": collection_trend,
        "efficiency_trend": efficiency_trend,
        "currency": _chart(Counter((c.currency or "-") for c in clients)),
        "student_shift": _chart(Counter(_shift_of(s.client).title() for s in students if s.client)),
        "top_outstanding": {"labels": [f"{c.client_code} {c.full_name}"[:28] for c, _ in top],
                            "data": [round(bal, 2) for _, bal in top]},
        "subscription_status": _chart(Counter(sub_status_label.get(s.status or "", (s.status or "Unknown").title())
                                              for s in subs)),
    }
    return {"tiles": tiles, "charts": charts, "base": base, "client_count": len(clients),
            "filters": {"date_from": date_from, "date_to": date_to, "client_id": client_id, "shift": shift,
                        "currency": currency, "group_id": group_id, "country": country, "state": state,
                        "status": status}}


# ============================================================================ 7. Monthly Performance Insights
# key, label, format ("int" | "pct" | "ratio" | "money")
INSIGHT_ROWS = [
    ("leads", "Leads", "int"),
    ("trial", "Trial", "int"),
    ("trial_drop", "Trial Drop", "int"),
    ("trial_on_leave", "Trial On Leave", "int"),
    ("total_fail_trial", "Total Fail Trial", "int"),
    ("sign_up", "Sign Up", "int"),
    ("reference", "Reference", "int"),
    ("active", "Active", "int"),
    ("total_in", "Total In", "int"),
    ("dropout", "Dropout", "int"),
    ("on_leave", "On Leave", "int"),
    ("total_out", "Total Out", "int"),
    ("current_student", "Current Student", "int"),
    ("total_session", "Total Session", "int"),
    ("free_session", "Free Session", "int"),
    ("teacher", "Teacher", "int"),
    ("average_student", "Average Student", "ratio"),
    ("pending_family", "Pending Family", "int"),
    ("total_invoices", "Total Invoices", "int"),
    ("paid_invoices", "Paid Invoices", "int"),
    ("fee_recovery", "Fee Recovery %", "pct"),
    ("pending_fee", "Pending Fee %", "pct"),
    ("trial_conversion", "Trial Conversion %", "pct"),
    ("student_retention", "Student Retention %", "pct"),
    ("trial_dropout_ratio", "Trial Dropout Ratio", "pct"),
    ("income", "Income", "money"),
]

_COLUMNS = ("day", "night", "total")


def _blank() -> dict:
    return {k: {c: 0.0 for c in _COLUMNS} for k, _l, _f2 in INSIGHT_ROWS}


def _add(store: dict, key: str, client: Optional[Client], value: float = 1.0) -> None:
    col = _day_night(client)
    store[key][col] = round(store[key][col] + value, 2)
    store[key]["total"] = round(store[key]["total"] + value, 2)


def month_bounds_of(month: str) -> tuple[date, date]:
    y, m = (int(x) for x in month.split("-"))
    start = date(y, m, 1)
    end = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
    return start, end


def previous_month(month: str) -> str:
    y, m = (int(x) for x in month.split("-"))
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def _insight_block(db: Session, month: str) -> dict:
    """Raw Day / Night / Total numbers for one month."""
    start, end = month_bounds_of(month)
    rates = _rates(db)
    store = _blank()
    clients = {c.id: c for c in db.query(Client).all()}
    students = db.query(Student).all()
    student_client = {s.id: clients.get(s.client_id) for s in students}

    for lead in db.query(Lead).all():
        if not _in(lead.created_at, start, end):
            continue
        _add(store, "leads", clients.get(lead.converted_client_id) if lead.converted_client_id else None)

    for t in db.query(Trial).all():
        client = clients.get(t.client_id) if t.client_id else None
        if _in(t.scheduled_at or t.created_at, start, end):
            _add(store, "trial", client)
        if (t.status or "") == "lost" and _in(t.updated_at, start, end):
            _add(store, "trial_drop", client)
            _add(store, "total_fail_trial", client)
        if (t.status or "") == "no_show" and _in(t.updated_at, start, end):
            _add(store, "trial_on_leave", client)
            _add(store, "total_fail_trial", client)

    converted_trials = [t for t in db.query(Trial).all()
                        if (t.status or "") == "converted" and _in(t.updated_at, start, end)]

    for c in clients.values():
        if _in(c.joined_at, start, end):
            if c.referred_by_client_id:
                _add(store, "reference", c)
            else:
                _add(store, "sign_up", c)
            _add(store, "total_in", c)

    for s in students:
        client = student_client.get(s.id)
        if (s.status or "") in STUDENT_ACTIVE_SET and _as_date(s.join_date) and s.join_date <= end:
            _add(store, "active", client)
            _add(store, "current_student", client)
        drop = s.drop_date or s.cancelled_at
        if _in(drop, start, end):
            _add(store, "dropout", client)
            _add(store, "total_out", client)

    for lv in db.query(Leave).all():
        if lv.person_type != "student" or not lv.student_id:
            continue
        if lv.start_date and lv.end_date and lv.start_date <= end and lv.end_date >= start:
            client = student_client.get(lv.student_id)
            _add(store, "on_leave", client)
            _add(store, "total_out", client)

    # distinct teachers per column; a teacher who serves both shifts appears in Day and Night,
    # so this row is deliberately not additive (Day + Night can exceed Total).
    teachers_seen = {c: set() for c in _COLUMNS}
    for sess in db.query(ClassSession).filter(ClassSession.date >= start, ClassSession.date <= end).all():
        client = student_client.get(sess.student_id)
        _add(store, "total_session", client)
        if sess.is_trial or (sess.status or "") == "free":
            _add(store, "free_session", client)
        col = _day_night(client)
        teachers_seen[col].add(sess.teacher_id)
        teachers_seen["total"].add(sess.teacher_id)
    for col in _COLUMNS:
        store["teacher"][col] = len(teachers_seen[col])

    invoices = db.query(Invoice).filter(Invoice.issue_date >= start, Invoice.issue_date <= end).all()
    invoiced = {c: 0.0 for c in _COLUMNS}
    collected = {c: 0.0 for c in _COLUMNS}
    for inv in invoices:
        if (inv.status or "") in ("cancelled", "void"):
            continue
        client = clients.get(inv.client_id)
        col = _day_night(client)
        _add(store, "total_invoices", client)
        if (inv.status or "") == "paid":
            _add(store, "paid_invoices", client)
        amount = _to_base(rates, inv.total, inv.currency)
        invoiced[col] = round(invoiced[col] + amount, 2)
        invoiced["total"] = round(invoiced["total"] + amount, 2)
        if float(inv.total or 0) - float(inv.paid_amount or 0) > 0.01:
            _add(store, "pending_family", client)

    for p in db.query(Payment).all():
        if (p.status or "") not in ("confirmed", "completed"):
            continue
        when = p.receipt_date or _as_date(p.received_at)
        if not _in(when, start, end):
            continue
        client = clients.get(p.client_id)
        col = _day_night(client)
        amount = _f(p.amount_in_base) or _to_base(rates, p.amount, p.currency)
        _add(store, "income", client, amount)
        collected[col] = round(collected[col] + amount, 2)
        collected["total"] = round(collected["total"] + amount, 2)

    for col in _COLUMNS:
        teachers = store["teacher"][col]
        store["average_student"][col] = round(store["current_student"][col] / teachers, 1) if teachers else 0.0
        store["fee_recovery"][col] = _pct(collected[col], invoiced[col])
        store["pending_fee"][col] = round(max(0.0, 100.0 - store["fee_recovery"][col]), 1)
        trials = store["trial"][col]
        conv = sum(1 for t in converted_trials
                   if _day_night(clients.get(t.client_id) if t.client_id else None) == col) if col != "total" \
            else len(converted_trials)
        store["trial_conversion"][col] = _pct(conv, trials)
        store["trial_dropout_ratio"][col] = _pct(store["trial_drop"][col], trials)
        current = store["current_student"][col]
        store["student_retention"][col] = round(max(0.0, 100.0 - _pct(store["dropout"][col], current)), 1) \
            if current else 0.0
    return store


def monthly_insights(db: Session, month: Optional[str] = None) -> dict:
    """ERP "Monthly Performance Insights": the chosen month vs the previous one, Day / Night / Total per row."""
    month = month or date.today().strftime("%Y-%m")
    try:
        month_bounds_of(month)
    except (ValueError, TypeError):
        month = date.today().strftime("%Y-%m")
    prev = previous_month(month)
    current = _insight_block(db, month)
    before = _insight_block(db, prev)

    rows = []
    for key, label, fmt in INSIGHT_ROWS:
        cur_total = current[key]["total"]
        prev_total = before[key]["total"]
        if round(cur_total, 2) == round(prev_total, 2):
            change, direction = "No Change", "flat"
        elif cur_total > prev_total:
            change, direction = "up", "up"
        else:
            change, direction = "down", "down"
        rows.append({"key": key, "label": label, "format": fmt,
                     "current": current[key], "previous": before[key],
                     "delta": round(cur_total - prev_total, 2), "change": change, "direction": direction})

    def label_of(m: str) -> str:
        return datetime.strptime(m, "%Y-%m").strftime("%B %Y")

    return {"month": month, "previous_month": prev, "month_label": label_of(month),
            "previous_label": label_of(prev), "rows": rows, "base": _base_code(db),
            "generated_on": date.today(), "generated_by": "Online Quran College",
            "months": recent_months(12)}


def recent_months(n: int = 12) -> list[tuple[str, str]]:
    """[(YYYY-MM, "September 2026")] newest first, for the Select Month dropdown."""
    out = []
    cur = date.today().replace(day=1)
    for _ in range(n):
        out.append((cur.strftime("%Y-%m"), cur.strftime("%B %Y")))
        cur = date(cur.year - 1, 12, 1) if cur.month == 1 else date(cur.year, cur.month - 1, 1)
    return out
