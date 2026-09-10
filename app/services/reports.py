"""Report catalogue, builders and exporters (Module 26).

* ``REPORTS`` - the catalogue (key, name, group, description, parameters).
* ``build(db, key, params)`` -> ``{"headers": [...], "rows": [[...]], "totals": [...]|None, "note": str}``
* ``export_xlsx(headers, rows, name, sheet="Report")`` / ``export_csv(headers, rows, name)`` -> file path under
  ``storage/reports`` (created at runtime).

Every builder tolerates completely empty tables and returns an empty row list.
"""
from __future__ import annotations

import csv
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.models.academic import Course, Evaluation, MonthlyTest, StudentProgress
from app.models.core import AIModelRun, Department, User
from app.models.crm import Campaign, Case, Feedback, Lead, LeadSource, Referral
from app.models.finance import Expense, Invoice, Payment, Subscription
from app.models.ops import KPI, KPIValue, ReportRun
from app.models.people import Client, Employee, HRAttendance, PayrollRun, Payslip, Student, Teacher
from app.models.scheduling import AIClassAnalysis, Attendance, ClassSession, QAReview, Trial

REPORTS_DIR = Path(BASE_DIR) / "storage" / "reports"

PARAM_LABELS = {"range": "Date range", "department": "Department", "teacher": "Teacher", "course": "Course"}

REPORTS: list[dict] = [
    {"key": "students_status", "name": "Students by status", "group": "Students", "icon": "graduation-cap",
     "description": "Every student with status, course, teacher, client and join date.", "params": ["course", "teacher"]},
    {"key": "students_course", "name": "Students by course", "group": "Students", "icon": "book-open",
     "description": "Enrolment counts and status mix per course and division.", "params": []},
    {"key": "students_country", "name": "Students by country", "group": "Students", "icon": "globe",
     "description": "Geographic distribution of the active roster (from the client record).", "params": []},
    {"key": "teacher_performance", "name": "Teacher performance", "group": "Academics", "icon": "user-check",
     "description": "Classes, missed, punctuality, QA, AI score, retention and grade per teacher.", "params": ["range"]},
    {"key": "daily_class_summary", "name": "Daily class-status summary", "group": "Academics", "icon": "calendar-days",
     "description": "Per-day breakdown of class statuses across the period.", "params": ["range", "teacher"]},
    {"key": "attendance", "name": "Attendance register", "group": "Academics", "icon": "clipboard-check",
     "description": "Student and teacher attendance marks per session.", "params": ["range", "teacher", "course"]},
    {"key": "invoices", "name": "Invoices", "group": "Finance", "icon": "receipt",
     "description": "All invoices issued in the period with balance and status.", "params": ["range"]},
    {"key": "payments", "name": "Payments received", "group": "Finance", "icon": "credit-card",
     "description": "Payments with method, gateway and base-currency amount.", "params": ["range"]},
    {"key": "aging", "name": "Receivables aging", "group": "Finance", "icon": "hourglass",
     "description": "Outstanding invoice balances bucketed by days overdue.", "params": []},
    {"key": "revenue_by_month", "name": "Revenue by month", "group": "Finance", "icon": "line-chart",
     "description": "Collected revenue, expenses, payroll and net per month.", "params": ["range"]},
    {"key": "revenue_by_currency", "name": "Revenue by currency", "group": "Finance", "icon": "coins",
     "description": "Payments grouped by billing currency with base-currency equivalent.", "params": ["range"]},
    {"key": "subscription_value", "name": "Subscription value", "group": "Finance", "icon": "repeat",
     "description": "Active subscriptions, price, teacher cost and margin.", "params": []},
    {"key": "expenses", "name": "Expenses", "group": "Finance", "icon": "wallet",
     "description": "Expenses by category, department and status.", "params": ["range", "department"]},
    {"key": "payroll_summary", "name": "Payroll summary", "group": "Finance", "icon": "banknote",
     "description": "Payslip totals per payroll run and per employee.", "params": ["range"]},
    {"key": "leads_conversion", "name": "Leads & conversion", "group": "Growth", "icon": "funnel",
     "description": "Leads by source and campaign with won counts and conversion %.", "params": ["range"]},
    {"key": "trials", "name": "Trials", "group": "Growth", "icon": "flask-conical",
     "description": "Trial classes with teacher, outcome and conversion.", "params": ["range", "teacher"]},
    {"key": "referrals", "name": "Referrals & ambassadors", "group": "Growth", "icon": "gift",
     "description": "Referral pipeline with status and credits issued.", "params": ["range"]},
    {"key": "cases_sla", "name": "Cases & SLA", "group": "Service", "icon": "life-buoy",
     "description": "Cases with priority, SLA due, breach flag and resolution time.", "params": ["range", "department"]},
    {"key": "qa_scores", "name": "QA scores", "group": "Quality", "icon": "shield-check",
     "description": "Quality reviews with per-dimension scores and reviewer.", "params": ["range", "teacher"]},
    {"key": "ai_monitoring", "name": "AI monitoring summary", "group": "Quality", "icon": "brain-circuit",
     "description": "AI class analyses with engagement, coverage, risk level and review status.", "params": ["range", "teacher"]},
    {"key": "feedback_nps", "name": "Feedback & NPS", "group": "Quality", "icon": "message-square-heart",
     "description": "Survey responses with NPS, rating and sentiment.", "params": ["range"]},
    {"key": "retention_risk", "name": "Retention risk", "group": "Students", "icon": "heart-pulse",
     "description": "Students ranked by churn-risk score with the contributing factors.", "params": []},
    {"key": "hr_attendance", "name": "HR attendance", "group": "People", "icon": "clock",
     "description": "Staff attendance marks with late minutes per employee.", "params": ["range", "department"]},
    {"key": "kpi_snapshot", "name": "KPI snapshot", "group": "Governance", "icon": "target",
     "description": "Stored KPI values for a month with target and RAG status.", "params": ["range"]},
]

REPORTS_BY_KEY = {r["key"]: r for r in REPORTS}
GROUPS = ["Students", "Academics", "Finance", "Growth", "Service", "Quality", "People", "Governance"]


# ============================================================================== helpers
def _s(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (datetime,)):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.isoformat()
    try:
        from decimal import Decimal
        if isinstance(v, Decimal):
            return float(v)
    except Exception:  # pragma: no cover
        pass
    return v


def _n(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "report").lower()).strip("_") or "report"


def _path(name: str, ext: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return REPORTS_DIR / f"{_slug(name)}-{stamp}.{ext}"


# ============================================================================== exporters
def export_xlsx(headers: list, rows: list, name: str, sheet: str = "Report", totals: Optional[list] = None) -> str:
    """Write a styled .xlsx (bold header, frozen pane, auto-sized columns) and return its path."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = (sheet or "Report")[:31]
    header_fill = PatternFill("solid", fgColor="0F766E")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    thin = Side(style="thin", color="E2E8F0")
    ws.append([str(h) for h in headers])
    for c in ws[1]:
        c.fill = header_fill
        c.font = header_font
        c.alignment = Alignment(vertical="center", horizontal="left")
        c.border = Border(bottom=thin)
    for r in rows:
        ws.append([_s(v) for v in r])
    if totals:
        ws.append([_s(v) for v in totals])
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for c in row:
            widths[c.column] = max(widths.get(c.column, 10), min(52, len(str(c.value or "")) + 3))
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(max(1, len(headers)))}{max(1, ws.max_row)}"
    p = _path(name, "xlsx")
    wb.save(p)
    return str(p)


def export_csv(headers: list, rows: list, name: str, totals: Optional[list] = None) -> str:
    p = _path(name, "csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow([str(h) for h in headers])
        for r in rows:
            w.writerow([_s(v) for v in r])
        if totals:
            w.writerow([_s(v) for v in totals])
    return str(p)


def log_run(db: Session, user, key: str, name: str, params: dict, fmt: str, path: Optional[str], rows: int) -> ReportRun:
    run = ReportRun(name=name, report_type=key, params={k: _s(v) for k, v in (params or {}).items()},
                    format=fmt, file_path=path, generated_by_id=getattr(user, "id", None), row_count=rows)
    db.add(run)
    db.flush()
    return run


# ============================================================================== builders
def _range(p: dict) -> tuple[date, date]:
    end = p.get("end") or date.today()
    start = p.get("start") or (end - timedelta(days=29))
    return start, end


def _dt(s: date, e: date):
    return datetime.combine(s, datetime.min.time()), datetime.combine(e, datetime.max.time())


def r_students_status(db: Session, p: dict) -> dict:
    q = db.query(Student)
    if p.get("course_id"):
        q = q.filter(Student.course_id == p["course_id"])
    if p.get("teacher_id"):
        q = q.filter(Student.teacher_id == p["teacher_id"])
    rows = []
    for s in q.order_by(Student.student_code).limit(5000):
        rows.append([s.student_code, s.full_name, s.status, s.course.name if s.course else "", s.level or "",
                     s.teacher.full_name if s.teacher else "", s.client.full_name if s.client else "",
                     s.client.country if s.client else "", s.join_date, s.risk_level or "", s.risk_score or 0])
    return {"headers": ["Code", "Student", "Status", "Course", "Level", "Teacher", "Client", "Country", "Joined", "Risk", "Score"],
            "rows": rows, "totals": ["Total", len(rows)] + [""] * 9}


def r_students_course(db: Session, p: dict) -> dict:
    rows = []
    for c in db.query(Course).order_by(Course.order, Course.name):
        base = db.query(Student).filter(Student.course_id == c.id)
        counts = dict(db.query(Student.status, func.count(Student.id)).filter(Student.course_id == c.id).group_by(Student.status).all())
        rows.append([c.code, c.name, base.count(), counts.get("active", 0), counts.get("trial", 0),
                     counts.get("frozen", 0), counts.get("cancelled", 0), counts.get("graduated", 0)])
    unassigned = db.query(Student).filter(Student.course_id.is_(None)).count()
    if unassigned:
        rows.append(["-", "Unassigned", unassigned, "", "", "", "", ""])
    tot = [sum(_n(r[i]) for r in rows) for i in range(2, 8)]
    return {"headers": ["Code", "Course", "Students", "Active", "Trial", "Frozen", "Cancelled", "Graduated"],
            "rows": rows, "totals": ["", "Total"] + [int(t) for t in tot]}


def r_students_country(db: Session, p: dict) -> dict:
    agg: dict[str, dict] = {}
    for st, cl in db.query(Student, Client).outerjoin(Client, Client.id == Student.client_id):
        key = (cl.country if cl and cl.country else "Unknown")
        a = agg.setdefault(key, {"total": 0, "active": 0, "trial": 0})
        a["total"] += 1
        if st.status == "active":
            a["active"] += 1
        if st.status == "trial":
            a["trial"] += 1
    rows = [[k, v["total"], v["active"], v["trial"]] for k, v in sorted(agg.items(), key=lambda kv: -kv[1]["total"])]
    return {"headers": ["Country", "Students", "Active", "Trial"], "rows": rows,
            "totals": ["Total", sum(r[1] for r in rows), sum(r[2] for r in rows), sum(r[3] for r in rows)]}


def r_teacher_performance(db: Session, p: dict) -> dict:
    from app.services import kpi as k
    s, e = _range(p)
    rows = []
    for t in db.query(Teacher).order_by(Teacher.teacher_code):
        sc = k.session_counts(db, s, e, t.id)
        rows.append([t.teacher_code, t.full_name, t.grade or "", t.status, sc["total"], sc["done"], sc["missed"],
                     sc["completion"], sc["punctuality"] if sc["punctuality"] is not None else "",
                     k.qa_avg(db, s, e, t.id) or "", k.ai_avg(db, s, e, t.id) or "",
                     db.query(Student).filter(Student.teacher_id == t.id, Student.status == "active").count(),
                     round(_n(t.retention_rate), 1), round(_n(t.rating), 2)])
    return {"headers": ["Code", "Teacher", "Grade", "Status", "Sessions", "Done", "Missed", "Completion %", "Punctuality %",
                        "QA avg", "AI avg", "Active students", "Retention %", "Rating"],
            "rows": rows, "totals": ["", f"{len(rows)} teachers", "", "", sum(_n(r[4]) for r in rows), sum(_n(r[5]) for r in rows),
                                     sum(_n(r[6]) for r in rows)] + [""] * 7}


def r_daily_class_summary(db: Session, p: dict) -> dict:
    s, e = _range(p)
    q = db.query(ClassSession.date, ClassSession.status, func.count(ClassSession.id)).filter(ClassSession.date >= s, ClassSession.date <= e)
    if p.get("teacher_id"):
        q = q.filter(ClassSession.teacher_id == p["teacher_id"])
    agg: dict[date, dict] = {}
    for d, st, n in q.group_by(ClassSession.date, ClassSession.status):
        agg.setdefault(d, {})[st] = n
    cols = ["pending", "started", "done", "missed", "absent", "leave", "cancelled", "rescheduled"]
    rows = []
    for d in sorted(agg):
        row = agg[d]
        total = sum(row.values())
        done = row.get("done", 0)
        terminal = sum(row.get(c, 0) for c in ("done", "missed", "absent", "leave", "cancelled", "rescheduled"))
        rows.append([d, total] + [row.get(c, 0) for c in cols] + [round(100 * done / terminal, 1) if terminal else 0.0])
    totals = ["Total", sum(r[1] for r in rows)] + [sum(r[i + 2] for r in rows) for i in range(len(cols))] + [""]
    return {"headers": ["Date", "Total"] + [c.title() for c in cols] + ["Completion %"], "rows": rows, "totals": totals}


def r_attendance(db: Session, p: dict) -> dict:
    s, e = _range(p)
    q = db.query(Attendance).filter(Attendance.date >= s, Attendance.date <= e)
    if p.get("teacher_id"):
        q = q.filter(Attendance.teacher_id == p["teacher_id"])
    rows = []
    for a in q.order_by(Attendance.date.desc()).limit(5000):
        st = a.student
        if p.get("course_id") and (not st or st.course_id != p["course_id"]):
            continue
        rows.append([a.date, st.student_code if st else "", st.full_name if st else "",
                     a.teacher.full_name if a.teacher else "", a.student_status, a.teacher_status or "", a.remarks or ""])
    present = sum(1 for r in rows if r[4] == "present")
    return {"headers": ["Date", "Student code", "Student", "Teacher", "Student status", "Teacher status", "Remarks"],
            "rows": rows, "totals": ["Total", len(rows), f"Present {present}", "", "", "", ""]}


def r_invoices(db: Session, p: dict) -> dict:
    s, e = _range(p)
    rows = []
    for i in db.query(Invoice).filter(Invoice.issue_date >= s, Invoice.issue_date <= e).order_by(Invoice.issue_date.desc()).limit(5000):
        rows.append([i.invoice_number, i.issue_date, i.due_date, i.client.full_name if i.client else "", i.currency,
                     _n(i.total), _n(i.paid_amount), round(_n(i.total) - _n(i.paid_amount), 2), _n(i.total_in_base), i.status])
    return {"headers": ["Invoice", "Issued", "Due", "Client", "Currency", "Total", "Paid", "Balance", "Total (base)", "Status"],
            "rows": rows, "totals": ["Total", len(rows), "", "", "", round(sum(r[5] for r in rows), 2),
                                     round(sum(r[6] for r in rows), 2), round(sum(r[7] for r in rows), 2),
                                     round(sum(r[8] for r in rows), 2), ""]}


def r_payments(db: Session, p: dict) -> dict:
    s, e = _range(p)
    a, b = _dt(s, e)
    rows = []
    for x in db.query(Payment).filter(Payment.received_at >= a, Payment.received_at <= b).order_by(Payment.received_at.desc()).limit(5000):
        rows.append([x.payment_number, x.received_at, x.client.full_name if x.client else "",
                     x.invoice.invoice_number if x.invoice else "", x.currency, _n(x.amount), _n(x.amount_in_base),
                     x.method or "", x.gateway or "", x.status])
    return {"headers": ["Payment", "Received", "Client", "Invoice", "Currency", "Amount", "Amount (base)", "Method", "Gateway", "Status"],
            "rows": rows, "totals": ["Total", len(rows), "", "", "", round(sum(r[5] for r in rows), 2),
                                     round(sum(r[6] for r in rows), 2), "", "", ""]}


def r_aging(db: Session, p: dict) -> dict:
    today = date.today()
    buckets = [("Current", 0, 0), ("1-30 days", 1, 30), ("31-60 days", 31, 60), ("61-90 days", 61, 90), ("90+ days", 91, 100000)]
    agg = {b[0]: {"count": 0, "amount": 0.0} for b in buckets}
    rows = []
    for i in db.query(Invoice).filter(Invoice.status.in_(["sent", "partial", "overdue"])).order_by(Invoice.due_date):
        bal = round(_n(i.total) - _n(i.paid_amount), 2)
        if bal <= 0:
            continue
        days = (today - i.due_date).days if i.due_date else 0
        label = next((b[0] for b in buckets if b[1] <= max(0, days) <= b[2]), "90+ days")
        if days <= 0:
            label = "Current"
        agg[label]["count"] += 1
        agg[label]["amount"] += bal
        rows.append([i.invoice_number, i.client.full_name if i.client else "", i.due_date, max(0, days), i.currency, bal, label])
    return {"headers": ["Invoice", "Client", "Due", "Days overdue", "Currency", "Balance", "Bucket"], "rows": rows,
            "totals": ["Total", len(rows), "", "", "", round(sum(r[5] for r in rows), 2),
                       " | ".join(f"{k}: {v['count']}" for k, v in agg.items() if v["count"])]}


def r_revenue_by_month(db: Session, p: dict) -> dict:
    from app.services import kpi as k
    s, e = _range(p)
    months, d = [], k.month_start(s)
    while d <= e:
        months.append(k.period_key(d))
        d = k.add_months(d, 1)
    if not months:
        months = [k.period_key(e)]
    rows = []
    for m in months:
        ms, me = k.period_bounds(m)
        rev, exp, pay = k.revenue(db, ms, me), k.expenses(db, ms, me), k.payroll(db, ms, me)
        rows.append([m, k.period_label(m), round(rev, 2), round(exp, 2), round(pay, 2), round(rev - exp - pay, 2)])
    return {"headers": ["Period", "Month", "Revenue (base)", "Expenses (base)", "Payroll (base)", "Net (base)"], "rows": rows,
            "totals": ["Total", "", round(sum(r[2] for r in rows), 2), round(sum(r[3] for r in rows), 2),
                       round(sum(r[4] for r in rows), 2), round(sum(r[5] for r in rows), 2)]}


def r_revenue_by_currency(db: Session, p: dict) -> dict:
    s, e = _range(p)
    a, b = _dt(s, e)
    rows = []
    q = (db.query(Payment.currency, func.count(Payment.id), func.sum(Payment.amount), func.sum(Payment.amount_in_base))
         .filter(Payment.status == "completed", Payment.received_at >= a, Payment.received_at <= b).group_by(Payment.currency))
    for cur, n, amt, base in q:
        rows.append([cur or "-", n, round(_n(amt), 2), round(_n(base), 2)])
    rows.sort(key=lambda r: -r[3])
    return {"headers": ["Currency", "Payments", "Amount", "Amount (base)"], "rows": rows,
            "totals": ["Total", sum(r[1] for r in rows), "", round(sum(r[3] for r in rows), 2)]}


def r_subscription_value(db: Session, p: dict) -> dict:
    rows = []
    for sub in db.query(Subscription).filter(Subscription.status.in_(["active", "paused"])).order_by(Subscription.subscription_code).limit(5000):
        price = _n(sub.price_in_base)
        cost = _n(sub.teacher_cost_base)
        rows.append([sub.subscription_code, sub.client.full_name if sub.client else "",
                     sub.student.full_name if sub.student else "", sub.course.name if sub.course else "",
                     sub.sessions_per_week, sub.currency, _n(sub.price), price, cost, round(price - cost, 2),
                     round(100 * (price - cost) / price, 1) if price else 0.0, sub.status])
    return {"headers": ["Code", "Client", "Student", "Course", "Sessions/wk", "Currency", "Price", "Price (base)",
                        "Teacher cost", "Margin", "Margin %", "Status"], "rows": rows,
            "totals": ["Total", len(rows), "", "", "", "", "", round(sum(r[7] for r in rows), 2),
                       round(sum(r[8] for r in rows), 2), round(sum(r[9] for r in rows), 2), "", ""]}


def r_expenses(db: Session, p: dict) -> dict:
    s, e = _range(p)
    q = db.query(Expense).filter(Expense.expense_date >= s, Expense.expense_date <= e)
    if p.get("department_id"):
        q = q.filter(Expense.department_id == p["department_id"])
    rows = []
    for x in q.order_by(Expense.expense_date.desc()).limit(5000):
        rows.append([x.expense_number, x.expense_date, x.category, x.department.name if x.department else "",
                     x.vendor or "", x.currency, _n(x.amount), _n(x.amount_in_base), x.status])
    return {"headers": ["Number", "Date", "Category", "Department", "Vendor", "Currency", "Amount", "Amount (base)", "Status"],
            "rows": rows, "totals": ["Total", len(rows), "", "", "", "", "", round(sum(r[7] for r in rows), 2), ""]}


def r_payroll_summary(db: Session, p: dict) -> dict:
    from app.services import kpi as k
    s, e = _range(p)
    months, d = set(), k.month_start(s)
    while d <= e:
        months.add(k.period_key(d))
        d = k.add_months(d, 1)
    rows = []
    q = db.query(Payslip, PayrollRun).join(PayrollRun, PayrollRun.id == Payslip.payroll_run_id).filter(PayrollRun.period.in_(list(months)))
    for ps, run in q.limit(5000):
        emp = ps.employee if hasattr(ps, "employee") else None
        emp = emp or db.query(Employee).filter(Employee.id == ps.employee_id).first()
        rows.append([run.period, run.status, emp.employee_code if emp else "", emp.full_name if emp else "",
                     emp.department.name if emp and emp.department else "", ps.classes_taught or 0, _n(ps.basic),
                     _n(ps.class_pay), _n(ps.bonus), _n(ps.deductions), _n(ps.gross), _n(ps.net), ps.status])
    return {"headers": ["Period", "Run", "Employee code", "Employee", "Department", "Classes", "Basic", "Class pay",
                        "Bonus", "Deductions", "Gross", "Net", "Status"], "rows": rows,
            "totals": ["Total", len(rows), "", "", "", "", round(sum(r[6] for r in rows), 2), round(sum(r[7] for r in rows), 2),
                       round(sum(r[8] for r in rows), 2), round(sum(r[9] for r in rows), 2), round(sum(r[10] for r in rows), 2),
                       round(sum(r[11] for r in rows), 2), ""]}


def r_leads_conversion(db: Session, p: dict) -> dict:
    s, e = _range(p)
    a, b = _dt(s, e)
    agg: dict[tuple, dict] = {}
    for lead in db.query(Lead).filter(Lead.created_at >= a, Lead.created_at <= b).limit(20000):
        src = lead.source.name if getattr(lead, "source", None) else "Direct"
        camp = lead.campaign.name if getattr(lead, "campaign", None) else "-"
        row = agg.setdefault((src, camp), {"leads": 0, "won": 0, "lost": 0, "score": 0})
        row["leads"] += 1
        row["score"] += lead.score or 0
        if lead.stage == "won":
            row["won"] += 1
        if lead.stage == "lost":
            row["lost"] += 1
    rows = []
    for (src, camp), v in sorted(agg.items(), key=lambda kv: -kv[1]["leads"]):
        rows.append([src, camp, v["leads"], v["won"], v["lost"], round(100 * v["won"] / v["leads"], 1) if v["leads"] else 0.0,
                     round(v["score"] / v["leads"], 1) if v["leads"] else 0.0])
    tl, tw = sum(r[2] for r in rows), sum(r[3] for r in rows)
    return {"headers": ["Source", "Campaign", "Leads", "Won", "Lost", "Conversion %", "Avg score"], "rows": rows,
            "totals": ["Total", "", tl, tw, sum(r[4] for r in rows), round(100 * tw / tl, 1) if tl else 0.0, ""]}


def r_trials(db: Session, p: dict) -> dict:
    s, e = _range(p)
    a, b = _dt(s, e)
    q = db.query(Trial).filter(Trial.scheduled_at >= a, Trial.scheduled_at <= b)
    if p.get("teacher_id"):
        q = q.filter(Trial.teacher_id == p["teacher_id"])
    rows = []
    for t in q.order_by(Trial.scheduled_at.desc()).limit(5000):
        rows.append([t.scheduled_at, t.student_name or (t.student.full_name if t.student else ""),
                     t.teacher.full_name if t.teacher else "", t.course.name if t.course else "", t.status,
                     t.outcome or "", "Yes" if t.converted_subscription_id else "No", t.follow_up_count or 0])
    conv = sum(1 for r in rows if r[6] == "Yes")
    return {"headers": ["Scheduled", "Student", "Teacher", "Course", "Status", "Outcome", "Converted", "Follow-ups"],
            "rows": rows, "totals": ["Total", len(rows), "", "", "", "", f"{conv} converted", ""]}


def r_referrals(db: Session, p: dict) -> dict:
    s, e = _range(p)
    a, b = _dt(s, e)
    rows = []
    for r in db.query(Referral).filter(Referral.created_at >= a, Referral.created_at <= b).order_by(Referral.created_at.desc()).limit(5000):
        amb = db.query(Client).filter(Client.id == r.ambassador_client_id).first()
        rows.append([r.referral_code or "", amb.full_name if amb else "", r.referred_name or "", r.status,
                     r.invited_at, r.qualified_at, _n(r.credit_amount), r.credit_currency or ""])
    return {"headers": ["Code", "Ambassador", "Referred", "Status", "Invited", "Qualified", "Credit", "Currency"],
            "rows": rows, "totals": ["Total", len(rows), "", "", "", "", round(sum(r[6] for r in rows), 2), ""]}


def r_cases_sla(db: Session, p: dict) -> dict:
    s, e = _range(p)
    a, b = _dt(s, e)
    q = db.query(Case).filter(Case.created_at >= a, Case.created_at <= b)
    if p.get("department_id"):
        q = q.filter(Case.department_id == p["department_id"])
    rows = []
    for c in q.order_by(Case.created_at.desc()).limit(5000):
        hours = round((c.resolved_at - c.created_at).total_seconds() / 3600, 1) if c.resolved_at and c.created_at else ""
        rows.append([c.case_number, c.created_at, c.case_type, c.title, c.priority, c.status,
                     c.department.name if c.department else "", c.sla_due_at, "Yes" if c.sla_breached else "No", hours])
    breached = sum(1 for r in rows if r[8] == "Yes")
    return {"headers": ["Case", "Raised", "Type", "Title", "Priority", "Status", "Department", "SLA due", "Breached", "Resolution hrs"],
            "rows": rows, "totals": ["Total", len(rows), "", "", "", "", "", "", f"{breached} breached", ""]}


def r_qa_scores(db: Session, p: dict) -> dict:
    s, e = _range(p)
    a, b = _dt(s, e)
    q = db.query(QAReview).filter(QAReview.created_at >= a, QAReview.created_at <= b)
    if p.get("teacher_id"):
        q = q.filter(QAReview.teacher_id == p["teacher_id"])
    rows = []
    for r in q.order_by(QAReview.created_at.desc()).limit(5000):
        rows.append([r.created_at, r.teacher.full_name if r.teacher else "", r.sample_type or "", r.status,
                     _n(r.tajweed_score), _n(r.methodology_score), _n(r.engagement_score), _n(r.punctuality_score),
                     _n(r.environment_score), _n(r.professionalism_score), _n(r.overall_score),
                     r.reviewer.full_name if getattr(r, "reviewer", None) else ""])
    avg = round(sum(r[10] for r in rows) / len(rows), 1) if rows else 0.0
    return {"headers": ["Date", "Teacher", "Sample", "Status", "Tajweed", "Methodology", "Engagement", "Punctuality",
                        "Environment", "Professionalism", "Overall", "Reviewer"], "rows": rows,
            "totals": ["Total", len(rows), "", "", "", "", "", "", "", "", f"Avg {avg}", ""]}


def r_ai_monitoring(db: Session, p: dict) -> dict:
    s, e = _range(p)
    a, b = _dt(s, e)
    q = db.query(AIClassAnalysis).filter(AIClassAnalysis.created_at >= a, AIClassAnalysis.created_at <= b)
    if p.get("teacher_id"):
        q = q.filter(AIClassAnalysis.teacher_id == p["teacher_id"])
    rows = []
    for x in q.order_by(AIClassAnalysis.created_at.desc()).limit(5000):
        rows.append([x.created_at, x.teacher.full_name if getattr(x, "teacher", None) else "", _n(x.camera_presence_pct),
                     x.punctuality_minutes or 0, _n(x.active_teaching_pct), _n(x.student_engagement_score),
                     _n(x.curriculum_coverage_pct), _n(x.overall_score), x.risk_level or "", x.review_status or "",
                     "Yes" if x.contact_exchange_detected else "No"])
    avg = round(sum(r[7] for r in rows) / len(rows), 1) if rows else 0.0
    return {"headers": ["Date", "Teacher", "Camera %", "Late min", "Active teaching %", "Engagement", "Coverage %",
                        "Overall", "Risk", "Review", "Contact exchange"], "rows": rows,
            "totals": ["Total", len(rows), "", "", "", "", "", f"Avg {avg}", "", "", ""]}


def r_feedback_nps(db: Session, p: dict) -> dict:
    s, e = _range(p)
    a, b = _dt(s, e)
    rows = []
    for f in db.query(Feedback).filter(Feedback.created_at >= a, Feedback.created_at <= b).order_by(Feedback.created_at.desc()).limit(5000):
        rows.append([f.created_at, f.respondent_type, f.trigger or "", f.nps if f.nps is not None else "",
                     f.rating if f.rating is not None else "", f.sentiment or "",
                     f.teacher.full_name if getattr(f, "teacher", None) else "", (f.comment or "")[:160], f.status])
    scores = [r[3] for r in rows if isinstance(r[3], (int, float))]
    nps_val = round(100 * (sum(1 for x in scores if x >= 9) - sum(1 for x in scores if x <= 6)) / len(scores), 1) if scores else 0.0
    return {"headers": ["Date", "Respondent", "Trigger", "NPS", "Rating", "Sentiment", "Teacher", "Comment", "Status"],
            "rows": rows, "totals": ["Total", len(rows), "", f"NPS {nps_val}", "", "", "", "", ""]}


def r_retention_risk(db: Session, p: dict) -> dict:
    rows = []
    q = db.query(Student).filter(Student.status.in_(["active", "trial", "frozen"])).order_by(Student.risk_score.desc())
    for st in q.limit(1000):
        factors = st.risk_factors
        if isinstance(factors, dict):
            ftxt = ", ".join(f"{k}" for k in factors)
        elif isinstance(factors, list):
            ftxt = ", ".join(str(f) for f in factors)
        else:
            ftxt = ""
        rows.append([st.student_code, st.full_name, st.status, st.risk_level or "", _n(st.risk_score),
                     st.teacher.full_name if st.teacher else "", st.client.full_name if st.client else "",
                     st.join_date, ftxt[:180]])
    high = sum(1 for r in rows if r[3] == "high")
    return {"headers": ["Code", "Student", "Status", "Risk level", "Score", "Teacher", "Client", "Joined", "Factors"],
            "rows": rows, "totals": ["Total", len(rows), "", f"{high} high", "", "", "", "", ""]}


def r_hr_attendance(db: Session, p: dict) -> dict:
    s, e = _range(p)
    q = db.query(HRAttendance).filter(HRAttendance.date >= s, HRAttendance.date <= e)
    if p.get("department_id"):
        q = q.join(Employee, Employee.id == HRAttendance.employee_id).filter(Employee.department_id == p["department_id"])
    rows = []
    for a in q.order_by(HRAttendance.date.desc()).limit(5000):
        emp = a.employee if getattr(a, "employee", None) else db.query(Employee).filter(Employee.id == a.employee_id).first()
        rows.append([a.date, emp.employee_code if emp else "", emp.full_name if emp else "",
                     emp.department.name if emp and emp.department else "", a.session or "", a.check_in, a.check_out,
                     a.status, a.late_minutes or 0])
    present = sum(1 for r in rows if r[7] == "present")
    return {"headers": ["Date", "Code", "Employee", "Department", "Session", "Check in", "Check out", "Status", "Late min"],
            "rows": rows, "totals": ["Total", len(rows), "", "", "", "", "", f"{present} present",
                                     sum(_n(r[8]) for r in rows)]}


def r_kpi_snapshot(db: Session, p: dict) -> dict:
    from app.services import kpi as k
    period = k.period_key(p.get("end") or date.today())
    rows = []
    for v, kp in (db.query(KPIValue, KPI).join(KPI, KPI.id == KPIValue.kpi_id)
                  .filter(KPIValue.period == period, KPIValue.entity_type.is_(None)).order_by(KPI.role_slug, KPI.name)):
        status = k.rag(v.value, v.target if v.target is not None else kp.target, kp.direction)
        rows.append([kp.code, kp.name, kp.role_slug or "", kp.unit, v.value, v.target if v.target is not None else kp.target,
                     kp.direction, k.RAG_LABELS.get(status, status), v.source, period])
    return {"headers": ["Code", "KPI", "Role", "Unit", "Value", "Target", "Direction", "RAG", "Source", "Period"],
            "rows": rows, "totals": ["Total", len(rows), "", "", "", "", "", "", "", ""],
            "note": f"Stored KPI values for {k.period_label(period)}. Run a snapshot from /kpis if this is empty."}


BUILDERS: dict[str, Callable[[Session, dict], dict]] = {
    "students_status": r_students_status, "students_course": r_students_course, "students_country": r_students_country,
    "teacher_performance": r_teacher_performance, "daily_class_summary": r_daily_class_summary, "attendance": r_attendance,
    "invoices": r_invoices, "payments": r_payments, "aging": r_aging, "revenue_by_month": r_revenue_by_month,
    "revenue_by_currency": r_revenue_by_currency, "subscription_value": r_subscription_value, "expenses": r_expenses,
    "payroll_summary": r_payroll_summary, "leads_conversion": r_leads_conversion, "trials": r_trials,
    "referrals": r_referrals, "cases_sla": r_cases_sla, "qa_scores": r_qa_scores, "ai_monitoring": r_ai_monitoring,
    "feedback_nps": r_feedback_nps, "retention_risk": r_retention_risk, "hr_attendance": r_hr_attendance,
    "kpi_snapshot": r_kpi_snapshot,
}


def build(db: Session, key: str, params: Optional[dict] = None) -> dict:
    """Run a report builder. Never raises - errors degrade to an empty table with a note."""
    meta = REPORTS_BY_KEY.get(key)
    if not meta:
        return {"headers": [], "rows": [], "totals": None, "note": "Unknown report."}
    fn = BUILDERS.get(key)
    try:
        out = fn(db, params or {})
    except Exception as exc:  # keep the page rendering when a sibling module has no data yet
        return {"headers": [], "rows": [], "totals": None, "note": f"This report could not be built: {exc}"}
    out.setdefault("totals", None)
    out.setdefault("note", "")
    out["meta"] = meta
    return out
