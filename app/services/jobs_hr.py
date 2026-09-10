"""Background jobs for HR / People & Culture / Payroll (discovered by app/core/scheduler.py).

    JOBS = [("job id", callable(db), interval_minutes), ...]
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.notify import notify
from app.core.utils import month_key
from app.models.core import User, Role, Department
from app.models.people import Employee, Teacher, Leave, HRAttendance, Violation, PayrollRun
from app.services import hr as svc
from app.services import payroll as pay

log = logging.getLogger("oqc.jobs.hr")


def _system_user(db: Session):
    return db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()


def daily_mark_absent(db: Session) -> dict:
    """After the shift has ended, employees with no check-in are marked absent (or leave if approved)."""
    today = date.today()
    marked = svc.mark_absent_for_missing(db, today, _system_user(db), only_after_shift_end=True)
    # also close yesterday off entirely
    yesterday = today - timedelta(days=1)
    marked_prev = svc.mark_absent_for_missing(db, yesterday, _system_user(db))
    return {"date": str(today), "today_sessions": marked, "yesterday_sessions": marked_prev}


def probation_reminders(db: Session) -> dict:
    """Warn People & Culture 7 days before a probation period ends, and on the day."""
    today = date.today()
    horizon = today + timedelta(days=7)
    rows = db.query(Employee).filter(Employee.status.in_(["probation", "active"]), Employee.probation_end.isnot(None),
                                     Employee.probation_end >= today, Employee.probation_end <= horizon).all()
    sent = 0
    recipients = svc.hr_officer_users(db)
    for e in rows:
        days = (e.probation_end - today).days
        if days not in (0, 7):
            continue
        for u in recipients:
            notify(db, u, "Probation review due", f"{e.full_name} ({e.employee_code}) finishes probation on {e.probation_end}"
                   + (" — today." if days == 0 else " — in 7 days."), event_type="hr", link=f"/hr/employees/{e.id}")
            sent += 1
        if e.manager and e.manager.user_id:
            notify(db, e.manager.user_id, "Probation review due", f"{e.full_name} finishes probation on {e.probation_end}.",
                   event_type="hr", link=f"/hr/employees/{e.id}")
            sent += 1
    return {"upcoming": len(rows), "notifications": sent}


def leave_reminders(db: Session) -> dict:
    """Remind the employee and their manager the day before leave starts and on the last day."""
    today = date.today()
    tomorrow = today + timedelta(days=1)
    starting = db.query(Leave).filter(Leave.person_type == "employee", Leave.status == "approved",
                                      Leave.start_date == tomorrow, Leave.reminder_sent_start.is_(False)).all()
    ending = db.query(Leave).filter(Leave.person_type == "employee", Leave.status == "approved",
                                    Leave.end_date == today, Leave.reminder_sent_end.is_(False)).all()
    for l in starting:
        emp = l.employee
        if emp and emp.user_id:
            notify(db, emp.user_id, "Your leave starts tomorrow",
                   f"{l.leave_type.title()} leave {l.start_date} to {l.end_date}. Please hand over anything outstanding today.",
                   event_type="leave", link="/hr/me?tab=leaves")
        if emp and emp.department and emp.department.hod_user_id:
            notify(db, emp.department.hod_user_id, "Team member on leave tomorrow",
                   f"{emp.full_name} is away {l.start_date} to {l.end_date}.", event_type="leave", link=f"/hr/leaves/{l.id}")
        l.reminder_sent_start = True
    for l in ending:
        emp = l.employee
        if emp and emp.user_id:
            notify(db, emp.user_id, "Welcome back tomorrow",
                   f"Your {l.leave_type} leave ends today. Remember to check in for your next shift.",
                   event_type="leave", link="/hr/me?tab=attendance")
        l.reminder_sent_end = True
    return {"starting": len(starting), "ending": len(ending)}


def auto_violations_for_missed_classes(db: Session) -> dict:
    """Raise a violation for teachers whose class was marked missed yesterday (once per teacher per day)."""
    day = date.today() - timedelta(days=1)
    created = 0
    try:
        from app.models.scheduling import ClassSession
    except Exception:  # pragma: no cover
        return {"created": 0, "reason": "scheduling module unavailable"}
    rows = db.query(ClassSession.teacher_id, func.count(ClassSession.id))\
        .filter(ClassSession.date == day, ClassSession.status == "missed").group_by(ClassSession.teacher_id).all()
    user = _system_user(db)
    for teacher_id, n in rows:
        t = db.query(Teacher).get(teacher_id)
        if not t or not t.employee_id:
            continue
        emp = db.query(Employee).get(t.employee_id)
        if not emp:
            continue
        exists = db.query(Violation).filter(Violation.employee_id == emp.id, Violation.date == day,
                                            Violation.violation_type == "missed_class").first()
        if exists:
            continue
        severity = "critical" if n >= 3 else ("major" if n >= 2 else "minor")
        svc.record_violation(db, emp, "missed_class", severity,
                             f"{n} class(es) marked missed on {day} without cover.", user,
                             action_taken="Automatic flag — supervisor follow-up required", day=day)
        created += 1
    return {"day": str(day), "teachers_flagged": created}


def weekly_teacher_grades(db: Session) -> dict:
    """Recompute every teacher grade once a week (Mondays)."""
    if date.today().weekday() != 0:
        return {"skipped": "not Monday"}
    result = pay.recompute_all_grades(db, _system_user(db))
    log.info("weekly teacher grading: %s", result)
    return result


def monthly_draft_payroll(db: Session) -> dict:
    """On the 1st of the month, draft the previous month's payroll for review."""
    today = date.today()
    if today.day != 1:
        return {"skipped": "not the 1st"}
    period = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    existing = db.query(PayrollRun).filter(PayrollRun.period == period).first()
    if existing and existing.status not in ("draft",):
        return {"period": period, "skipped": f"already {existing.status}"}
    user = _system_user(db)
    run = pay.generate_payroll(db, period, user)
    for u in svc.hr_notify_users(db):
        notify(db, u, "Draft payroll ready", f"Payroll for {period} has been drafted: {len(run.payslips)} payslip(s), "
               f"net {float(run.total_net):,.0f} PKR. Review and submit for approval.",
               event_type="payroll", link=f"/hr/payroll/{run.id}")
    return {"period": period, "payslips": len(run.payslips), "net": float(run.total_net)}


def pending_leave_reminders(db: Session) -> dict:
    """Nudge HODs and People & Culture about leave requests waiting more than 24 hours."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    pending = db.query(Leave).filter(Leave.person_type == "employee", Leave.status == "pending",
                                     Leave.created_at <= cutoff).all()
    if not pending:
        return {"pending": 0}
    by_hod: dict[int, list] = {}
    for l in pending:
        emp = l.employee
        hod = emp.department.hod_user_id if emp and emp.department else None
        if hod:
            by_hod.setdefault(hod, []).append(l)
    sent = 0
    for uid, rows in by_hod.items():
        notify(db, uid, f"{len(rows)} leave request(s) awaiting your decision",
               ", ".join(f"{l.employee.full_name} ({l.start_date})" for l in rows[:5]),
               event_type="leave", link="/hr/leaves?status=pending")
        sent += 1
    for u in svc.hr_officer_users(db):
        notify(db, u, f"{len(pending)} leave request(s) older than 24 hours",
               "Staff leave decisions are overdue.", event_type="leave", link="/hr/leaves?status=pending")
        sent += 1
    return {"pending": len(pending), "notifications": sent}


JOBS = [
    ("hr_daily_mark_absent", daily_mark_absent, 120),
    ("hr_probation_reminders", probation_reminders, 720),
    ("hr_leave_reminders", leave_reminders, 360),
    ("hr_auto_violations_missed_classes", auto_violations_for_missed_classes, 720),
    ("hr_weekly_teacher_grades", weekly_teacher_grades, 720),
    ("hr_monthly_draft_payroll", monthly_draft_payroll, 720),
    ("hr_pending_leave_reminders", pending_leave_reminders, 480),
]
