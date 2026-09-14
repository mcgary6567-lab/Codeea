"""HOD Portal - Academic Manager Portal (docs/AUDIT_ACADEMICS.md 3.12).

One monitoring page with the supervisor tiles, a From/To Date + Employee search bar and four tabs:
Academics, HR, Billing and Team Progress. Read-only: every action links into the owning module.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import parse_date
from app.database import get_db
from app.models.core import User
from app.models.erp import ClassQuery
from app.models.finance import Invoice, Payment, Subscription
from app.models.people import Client, Employee, HRAttendance, Leave, Student, Teacher
from app.models.scheduling import ClassSession, SESSION_STATUSES
from app.services import classes as class_svc
from app.services import scheduling as svc

router = APIRouter(prefix="/hod", dependencies=[Depends(csrf_protect)])

TABS = [("academics", "Academics"), ("hr", "HR"), ("billing", "Billing"), ("progress", "Team Progress")]


def _scoped_sessions(db: Session, date_from: date, date_to: date, teacher_ids: list[int] | None,
                     employee_id: int | None):
    q = db.query(ClassSession).filter(ClassSession.date >= date_from, ClassSession.date <= date_to)
    if teacher_ids is not None:
        q = q.filter(ClassSession.teacher_id.in_(teacher_ids or [-1]))
    if employee_id:
        q = q.filter(ClassSession.teacher_id == employee_id)
    return q


def _academics(db: Session, rows: list[ClassSession], date_from: date, date_to: date) -> dict:
    """Academics Summary (status + count), Absent Staff, Today Trials, Dropped Students, New Registrations."""
    today = date.today()
    since = today - timedelta(days=30)
    summary = []
    for st in SESSION_STATUSES:
        n = sum(1 for r in rows if r.status == st)
        if n:
            summary.append({"status": st, "count": n})
    summary.sort(key=lambda r: -r["count"])
    from app.services import arrangements as arr_svc
    return {
        "summary": summary,
        "summary_total": len(rows),
        "absent_staff": arr_svc.absent_teachers(db, date_from, date_to),
        "today_trials": [r for r in rows if r.is_trial and r.date == today],
        "dropped": (db.query(Student).filter(Student.status.in_(["cancelled", "drop_out"]),
                                             or_(Student.drop_date >= since, Student.cancelled_at >= since))
                    .order_by(Student.id.desc()).limit(40).all()),
        "new_students": (db.query(Student).filter(Student.join_date >= since)
                         .order_by(Student.join_date.desc()).limit(40).all()),
    }


def _hr(db: Session, date_from: date, date_to: date, employee_id: int | None) -> dict:
    """Late / absent attendance counts and the pending staff leave queue."""
    q = db.query(HRAttendance).filter(HRAttendance.date >= date_from, HRAttendance.date <= date_to)
    emp_id = None
    if employee_id:
        t = db.get(Teacher, employee_id)
        emp_id = t.employee_id if t else None
        q = q.filter(HRAttendance.employee_id == (emp_id or -1))
    rows = q.all()
    by_emp: dict[int, dict] = {}
    for a in rows:
        b = by_emp.setdefault(a.employee_id, {"employee": a.employee, "late": 0, "absent": 0, "present": 0})
        if a.status == "late":
            b["late"] += 1
        elif a.status == "absent":
            b["absent"] += 1
        elif a.status == "present":
            b["present"] += 1
    staff = sorted((b for b in by_emp.values() if b["late"] or b["absent"]),
                   key=lambda b: (-b["absent"], -b["late"]))[:25]
    lq = db.query(Leave).filter(Leave.person_type == "employee", Leave.status == "pending")
    if emp_id:
        lq = lq.filter(Leave.employee_id == emp_id)
    return {"late_total": sum(1 for a in rows if a.status == "late"),
            "absent_total": sum(1 for a in rows if a.status == "absent"),
            "present_total": sum(1 for a in rows if a.status == "present"),
            "staff": staff,
            "pending_leaves": lq.order_by(Leave.start_date).limit(30).all()}


def _billing(db: Session, date_from: date, date_to: date) -> dict:
    """Pending invoice count and amount plus the receipts taken today."""
    today = date.today()
    pending_statuses = ["draft", "pending", "sent", "confirmed", "partial", "overdue"]
    inv = db.query(Invoice).filter(Invoice.status.in_(pending_statuses),
                                   Invoice.issue_date >= date_from, Invoice.issue_date <= date_to).all()
    receipts_today = (db.query(Payment).filter(Payment.status.in_(["completed", "confirmed"]),
                                               func.date(Payment.received_at) == today).all())
    period = (db.query(Payment).filter(Payment.status.in_(["completed", "confirmed"]),
                                       func.date(Payment.received_at) >= date_from,
                                       func.date(Payment.received_at) <= date_to).all())
    return {"pending_count": len(inv),
            "pending_amount": round(sum(float(i.total_in_base or 0) - float(i.paid_amount or 0) for i in inv), 2),
            "receipts_today": receipts_today,
            "receipts_today_amount": round(sum(float(p.amount_in_base or 0) for p in receipts_today), 2),
            "receipts_period": len(period),
            "receipts_period_amount": round(sum(float(p.amount_in_base or 0) for p in period), 2),
            "invoices": sorted(inv, key=lambda i: i.due_date)[:25]}


def _progress(db: Session, rows: list[ClassSession], date_from: date, date_to: date,
              teacher_ids: list[int] | None) -> list[dict]:
    """Per teacher: done, missed, average actual duration and open class queries."""
    qq = db.query(ClassQuery.teacher_id, func.count(ClassQuery.id)).filter(ClassQuery.status == "pending")
    queries = {tid: n for tid, n in qq.group_by(ClassQuery.teacher_id)}
    by: dict[int, dict] = {}
    for r in rows:
        b = by.setdefault(r.teacher_id, {"teacher": r.teacher, "total": 0, "done": 0, "missed": 0, "absent": 0,
                                         "short": 0, "no_activity": 0, "minutes": [], "queries": 0})
        b["total"] += 1
        if r.status == "done":
            b["done"] += 1
            if r.actual_duration_minutes:
                b["minutes"].append(r.actual_duration_minutes)
                if r.actual_duration_minutes < 35:
                    b["short"] += 1
            if not r.activity_updated_at:
                b["no_activity"] += 1
        elif r.status == "missed":
            b["missed"] += 1
        elif r.status == "absent":
            b["absent"] += 1
    out = []
    for tid, b in by.items():
        mins = b.pop("minutes")
        b["avg_duration"] = round(sum(mins) / len(mins), 1) if mins else 0.0
        b["highlight"] = class_svc.duration_highlight(int(b["avg_duration"]) if mins else None)
        b["queries"] = queries.get(tid, 0)
        b["completion"] = round(100 * b["done"] / b["total"], 1) if b["total"] else 0.0
        out.append(b)
    out.sort(key=lambda b: (-b["missed"], b["avg_duration"]))
    return out


@router.get("", include_in_schema=False)
def dashboard(request: Request, tab: str = "academics", date_from: str = "", date_to: str = "",
              employee_id: int | None = None, db: Session = Depends(get_db),
              user: User = Depends(require("supervisor.view"))):
    today = date.today()
    df = parse_date(date_from, today)
    dt = parse_date(date_to, today)
    if dt < df:
        df, dt = dt, df
    tab = tab if tab in dict(TABS) else "academics"
    teacher_ids = svc.scoped_teacher_ids(db, user)
    rows = _scoped_sessions(db, df, dt, teacher_ids, employee_id).order_by(ClassSession.scheduled_start).all()
    monitor_ids = [employee_id] if employee_id else teacher_ids
    ctx = {
        "user": user, "tab": tab, "tabs": TABS, "date_from": df, "date_to": dt, "employee_id": employee_id,
        "tab_items": [(k, lbl, f"/hod?date_from={df}&date_to={dt}&employee_id={employee_id or ''}&tab={k}")
                      for k, lbl in TABS],
        "erp": class_svc.erp_monitor(db, dt, monitor_ids),
        "rows": rows,
        "teacher_options": [(t.id, t.full_name) for t in svc.teachers_for(db, user)],
        "base_url": f"/hod?date_from={df}&date_to={dt}&employee_id={employee_id or ''}",
        "refreshed_at": datetime.utcnow(),
    }
    if tab == "academics":
        ctx["academics"] = _academics(db, rows, df, dt)
    elif tab == "hr":
        ctx["hr"] = _hr(db, df, dt, employee_id)
    elif tab == "billing":
        ctx["billing"] = _billing(db, df, dt)
    else:
        ctx["progress"] = _progress(db, rows, df, dt, teacher_ids)
    return render(request, "hod/dashboard.html", ctx)
