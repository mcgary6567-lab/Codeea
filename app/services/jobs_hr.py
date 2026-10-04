"""Background jobs for HR / People & Culture / Payroll (discovered by app/core/scheduler.py).

    JOBS = [("job id", callable(db), interval_minutes), ...]
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.notify import notify
from app.models.core import Role, User
from app.models.people import Employee, Teacher, Leave, Violation, PayrollRun
from app.models.scheduling import ReminderLog
from app.services import hr as svc
from app.services import payroll as pay
from app.services import scheduling as sched_svc

log = logging.getLogger("oqc.jobs.hr")

# A job keyed on a calendar date catches up this many days when its tick was missed (restart, downtime).
CATCHUP_DAYS = 3
CONTRACT_END_THRESHOLDS = (30, 7)
CONTRACT_MARKER_KEY = "job_contract_end_reminders_last_run"
PAYROLL_MARKER_KEY = "job_monthly_draft_payroll_last_period"
GRADES_MARKER_KEY = "job_weekly_teacher_grades_last_week"


def _system_user(db: Session):
    return db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()


def _marker(db: Session, key: str, field: str = "value"):
    return sched_svc.setting_value(db, key, None, field=field)


def _set_marker(db: Session, key: str, value: str, extra: dict | None = None) -> None:
    sched_svc.set_setting(db, key, {"value": value, "at": datetime.utcnow().isoformat(), **(extra or {})},
                          group="jobs", description="Last run marker for an HR background job")


def _already_logged(db: Session, kind: str, entity_type: str, entity_id: int) -> bool:
    return db.query(ReminderLog.id).filter(ReminderLog.reminder_type == kind, ReminderLog.entity_type == entity_type,
                                           ReminderLog.entity_id == entity_id).first() is not None


def people_culture_heads(db: Session) -> list[User]:
    """The People & Culture head(s): active users holding the hod_people role."""
    return db.query(User).join(Role, User.role_id == Role.id).filter(Role.slug == "hod_people", User.is_active.is_(True)).all()


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


def contract_end_reminders(db: Session, today: date | None = None) -> dict:
    """Daily: 30 and 7 days before an employee's contract ends, tell the People & Culture head and the manager.

    A day the job missed is caught up: every threshold whose date fell between the last run and today fires once.
    ReminderLog rows (``contract_end_30`` / ``contract_end_7`` per employee) plus the last-run Setting guarantee
    that a threshold is never announced twice."""
    today = today or date.today()
    last = _marker(db, CONTRACT_MARKER_KEY)
    try:
        since = date.fromisoformat(last) + timedelta(days=1) if last else today - timedelta(days=CATCHUP_DAYS)
    except ValueError:
        since = today - timedelta(days=CATCHUP_DAYS)
    since = max(since, today - timedelta(days=CATCHUP_DAYS))
    if since > today:
        return {"skipped": "already ran today", "notifications": 0, "employees": 0}
    heads = people_culture_heads(db)
    sent = employees = 0
    for threshold in CONTRACT_END_THRESHOLDS:
        rows = (db.query(Employee).filter(Employee.status.in_(["active", "probation"]), Employee.contract_end_date.isnot(None),
                                          Employee.contract_end_date >= since + timedelta(days=threshold),
                                          Employee.contract_end_date <= today + timedelta(days=threshold))
                .order_by(Employee.contract_end_date).all())
        kind = f"contract_end_{threshold}"
        for e in rows:
            if _already_logged(db, kind, "Employee", e.id):
                continue
            days_left = (e.contract_end_date - today).days
            when = "today" if days_left == 0 else f"in {days_left} day(s)"
            title = f"Contract ends {when}: {e.full_name}"
            body = (f"{e.full_name} ({e.employee_code}, {e.designation}) has a contract ending on {e.contract_end_date:%d %b %Y}. "
                    f"Decide on renewal or exit paperwork.")
            recipients: dict[int, User] = {u.id: u for u in heads}
            if e.manager and e.manager.user_id:
                recipients.setdefault(e.manager.user_id, e.manager.user)
            for uid in recipients:
                notify(db, uid, title, body, event_type="hr", link=f"/hr/employees/{e.id}")
                sent += 1
            db.add(ReminderLog(reminder_type=kind, user_id=e.user_id, entity_type="Employee", entity_id=e.id,
                               channel="in_app", status="sent"))
            employees += 1
    _set_marker(db, CONTRACT_MARKER_KEY, today.isoformat(), {"employees": employees, "notifications": sent})
    return {"since": str(since), "employees": employees, "notifications": sent}


def leave_reminders(db: Session) -> dict:
    """Remind the employee and their manager the day before leave starts and on the last day.

    Keyed on the leave's own ``reminder_sent_*`` flags, so a missed tick catches up: any leave starting within
    the last few days (or tomorrow) that was never announced is announced once."""
    today = date.today()
    tomorrow = today + timedelta(days=1)
    catchup = timedelta(days=CATCHUP_DAYS)
    starting = db.query(Leave).filter(Leave.person_type == "employee", Leave.status == "approved",
                                      Leave.start_date >= tomorrow - catchup, Leave.start_date <= tomorrow,
                                      Leave.reminder_sent_start.is_(False)).all()
    ending = db.query(Leave).filter(Leave.person_type == "employee", Leave.status == "approved",
                                    Leave.end_date >= today - catchup, Leave.end_date <= today,
                                    Leave.reminder_sent_end.is_(False)).all()
    for l in starting:
        emp = l.employee
        if emp and emp.user_id:
            when = "tomorrow" if l.start_date == tomorrow else ("today" if l.start_date == today else f"on {l.start_date:%d %b}")
            notify(db, emp.user_id, f"Your leave starts {when}",
                   f"{l.leave_type.title()} leave {l.start_date} to {l.end_date}. Please hand over anything outstanding.",
                   event_type="leave", link="/hr/me?tab=leaves")
        if emp and emp.department and emp.department.hod_user_id:
            notify(db, emp.department.hod_user_id, "Team member on leave" + (" tomorrow" if l.start_date == tomorrow else ""),
                   f"{emp.full_name} is away {l.start_date} to {l.end_date}.", event_type="leave", link=f"/hr/leaves/{l.id}")
        l.reminder_sent_start = True
    for l in ending:
        emp = l.employee
        if emp and emp.user_id:
            notify(db, emp.user_id, "Welcome back tomorrow",
                   f"Your {l.leave_type} leave ends today. Remember to check in for your next shift.",
                   event_type="leave", link="/hr/me?tab=attendance")
        l.reminder_sent_end = True
    db.flush()   # the session does not autoflush; the flags must be visible to a run that follows in the same session
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


def weekly_teacher_grades(db: Session, today: date | None = None) -> dict:
    """Recompute every teacher grade once a week, due on Monday.

    The week's Monday is the marker: a Monday tick that was missed is caught up on the next run that week,
    and a second run in the same week is a no-op."""
    today = today or date.today()
    week = (today - timedelta(days=today.weekday())).isoformat()
    if _marker(db, GRADES_MARKER_KEY) == week:
        return {"skipped": f"already recomputed for the week of {week}", "week": week}
    result = pay.recompute_all_grades(db, _system_user(db))
    _set_marker(db, GRADES_MARKER_KEY, week)
    log.info("weekly teacher grading (week of %s): %s", week, result)
    return {"week": week, **(result if isinstance(result, dict) else {"result": result})}


def monthly_draft_payroll(db: Session, today: date | None = None) -> dict:
    """Due on the 1st of the month: draft the previous month's payroll for review.

    Catches up a missed 1st during the first days of the month; the drafted period is the marker, so the
    draft is produced once per month however many ticks fall in that window."""
    today = today or date.today()
    if today.day > 1 + CATCHUP_DAYS:
        return {"skipped": "not the 1st (catch-up window passed)"}
    period = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    if _marker(db, PAYROLL_MARKER_KEY) == period:
        return {"period": period, "skipped": "already drafted this month"}
    existing = db.query(PayrollRun).filter(PayrollRun.period == period).first()
    if existing and existing.status not in pay.REGENERABLE_STATUSES:
        _set_marker(db, PAYROLL_MARKER_KEY, period, {"run_id": existing.id, "status": existing.status})
        return {"period": period, "skipped": f"already {existing.status}"}
    if existing:
        _set_marker(db, PAYROLL_MARKER_KEY, period, {"run_id": existing.id, "status": existing.status})
        return {"period": period, "skipped": f"a {existing.status} run already exists", "run_id": existing.id}
    user = _system_user(db)
    run = pay.generate_payroll(db, period, user)
    _set_marker(db, PAYROLL_MARKER_KEY, period, {"run_id": run.id})
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
    ("hr_contract_end_reminders", contract_end_reminders, 720),
    ("hr_leave_reminders", leave_reminders, 360),
    ("hr_auto_violations_missed_classes", auto_violations_for_missed_classes, 720),
    ("hr_weekly_teacher_grades", weekly_teacher_grades, 720),
    ("hr_monthly_draft_payroll", monthly_draft_payroll, 720),
    ("hr_pending_leave_reminders", pending_leave_reminders, 480),
]
