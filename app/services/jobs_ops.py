"""Background jobs for the Executive Command Center & Operations modules.

    JOBS = [("job id", callable(db), interval_minutes), ...]

* task escalation (every 15 minutes)
* daily KPI snapshot, anomaly detection, daily-report reminders, recurring-task generation
* weekly department-scorecard reminders
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.notify import notify
from app.models.core import Department, Notification, RiskAlert, Setting, User
from app.models.ops import DailyReport, DepartmentScorecard, Task
from app.models.people import Employee
from app.services import kpi as kpi_svc

log = logging.getLogger("oqc.jobs.ops")

ORG_TZ = "Asia/Karachi"


def org_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(ORG_TZ)).replace(tzinfo=None)
    except Exception:  # pragma: no cover
        return datetime.utcnow() + timedelta(hours=5)


def setting(db: Session, key: str, default=None):
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row and row.value is not None else default


def deadline_time(db: Session, slot: str) -> time:
    raw = setting(db, f"report_deadline_{slot}", {"time": "10:00" if slot == "morning" else "17:00"}) or {}
    txt = raw.get("time") if isinstance(raw, dict) else str(raw)
    try:
        h, m = str(txt or "10:00").split(":")[:2]
        return time(int(h), int(m))
    except Exception:
        return time(10, 0) if slot == "morning" else time(17, 0)


def _system_user(db: Session) -> Optional[User]:
    return db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()


def _escalation_targets(db: Session, task: Task) -> list[User]:
    """The assignee's line manager plus the HOD of the relevant department."""
    out: list[User] = []
    if task.assignee_id:
        emp = db.query(Employee).filter(Employee.user_id == task.assignee_id).first()
        if emp and emp.manager_id:
            mgr = db.query(Employee).filter(Employee.id == emp.manager_id).first()
            if mgr and mgr.user_id:
                u = db.query(User).filter(User.id == mgr.user_id).first()
                if u:
                    out.append(u)
        dept_id = task.department_id or (emp.department_id if emp else None)
        if dept_id:
            dept = db.query(Department).filter(Department.id == dept_id).first()
            if dept and dept.hod_user_id:
                u = db.query(User).filter(User.id == dept.hod_user_id).first()
                if u:
                    out.append(u)
    if not out and task.creator_id:
        u = db.query(User).filter(User.id == task.creator_id).first()
        if u:
            out.append(u)
    seen, uniq = set(), []
    for u in out:
        if u.id not in seen:
            seen.add(u.id)
            uniq.append(u)
    return uniq


def escalate_overdue_tasks(db: Session) -> dict:
    """Tasks overdue by more than 24h get an escalated flag, a notification chain and a RiskAlert."""
    cutoff = date.today() - timedelta(days=1)
    q = (db.query(Task).filter(Task.status.in_(["todo", "in_progress", "review"]), Task.escalated.is_(False),
                               Task.due_date.isnot(None), Task.due_date < cutoff))
    escalated = 0
    for task in q.limit(200):
        task.escalated = True
        task.escalated_at = datetime.utcnow()
        escalated += 1
        days = (date.today() - task.due_date).days
        body = f"Task '{task.title}' is {days} day(s) overdue and has been escalated."
        if task.assignee_id:
            notify(db, task.assignee_id, "Task escalated", body, event_type="task_escalated", link=f"/tasks/{task.id}")
        for u in _escalation_targets(db, task):
            notify(db, u, "Team task escalated", body + f" Assignee: {task.assignee.full_name if task.assignee else 'unassigned'}.",
                   event_type="task_escalated", link=f"/tasks/{task.id}")
        exists = db.query(RiskAlert).filter(RiskAlert.alert_type == "task_overdue", RiskAlert.entity_type == "Task",
                                            RiskAlert.entity_id == task.id, RiskAlert.status.in_(["open", "acknowledged"])).first()
        if not exists:
            db.add(RiskAlert(alert_type="task_overdue", severity="high" if task.priority in ("high", "urgent") else "medium",
                             title=f"Overdue task: {task.title[:120]}", message=body, entity_type="Task", entity_id=task.id,
                             visibility="management", status="open", source="system"))
        log_action(db, _system_user(db), "escalate", "tasks", entity=task, description=body, severity="warning")
    db.flush()
    return {"escalated": escalated}


_RECUR_DELTA = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


def _next_due(d: date, recurrence: str) -> date:
    if recurrence == "monthly":
        return kpi_svc.add_months(d, 1)
    return d + _RECUR_DELTA.get(recurrence, timedelta(days=7))


def generate_recurring_tasks(db: Session) -> dict:
    """For every completed recurring task, create the next occurrence (idempotent on title+assignee+due date)."""
    created = 0
    q = db.query(Task).filter(Task.recurrence.in_(["daily", "weekly", "monthly"]), Task.status.in_(["done", "cancelled"]))
    for t in q.limit(500):
        base = t.due_date or (t.completed_at.date() if t.completed_at else date.today())
        nxt = _next_due(base, t.recurrence)
        while nxt < date.today():
            nxt = _next_due(nxt, t.recurrence)
        dup = db.query(Task).filter(Task.title == t.title, Task.recurrence == t.recurrence, Task.due_date == nxt,
                                    Task.assignee_id == t.assignee_id).first()
        if dup:
            continue
        db.add(Task(title=t.title, description=t.description, project_id=t.project_id, sprint_id=t.sprint_id,
                    assignee_id=t.assignee_id, creator_id=t.creator_id, department_id=t.department_id,
                    priority=t.priority, status="todo", due_date=nxt, estimate_hours=t.estimate_hours,
                    recurrence=t.recurrence, kpi_id=t.kpi_id))
        created += 1
    db.flush()
    return {"created": created}


def daily_kpi_snapshot(db: Session) -> dict:
    return kpi_svc.snapshot_kpis(db, kpi_svc.period_key())


def anomaly_detection(db: Session) -> dict:
    from app.services.insights import detect_anomalies
    return detect_anomalies(db, kpi_svc.resolve_period("this_month"))


def _reporting_users(db: Session) -> list[User]:
    return [u for u in db.query(User).filter(User.is_active.is_(True)).all() if u.portal in ("admin", "teacher")]


def daily_report_reminders(db: Session) -> dict:
    """30 minutes before each deadline, remind everyone who has not yet submitted that slot."""
    now = org_now()
    today = now.date()
    sent = 0
    for slot in ("morning", "afternoon"):
        dl = deadline_time(db, slot)
        target = datetime.combine(today, dl) - timedelta(minutes=30)
        if not (target <= now < target + timedelta(minutes=45)):
            continue
        submitted = {r[0] for r in db.query(DailyReport.user_id).filter(DailyReport.report_date == today, DailyReport.slot == slot)}
        day_start = datetime.combine(today, time.min)
        for u in _reporting_users(db):
            if u.id in submitted:
                continue
            already = db.query(Notification).filter(Notification.user_id == u.id, Notification.event_type == f"daily_report_{slot}",
                                                    Notification.created_at >= day_start).first()
            if already:
                continue
            notify(db, u, f"{slot.title()} report due at {dl.strftime('%H:%M')}",
                   "Submit your structured daily report before the deadline (free chat is not a record).",
                   event_type=f"daily_report_{slot}", link="/daily-reports")
            sent += 1
    db.flush()
    return {"reminders": sent}


def scorecard_reminders(db: Session) -> dict:
    """Weekly nudge to HODs whose department scorecard for the current month is not submitted."""
    period = kpi_svc.period_key()
    sent = 0
    for dept in db.query(Department).filter(Department.is_active.is_(True)):
        if not dept.hod_user_id:
            continue
        sc = db.query(DepartmentScorecard).filter(DepartmentScorecard.department_id == dept.id,
                                                  DepartmentScorecard.period == period).first()
        if sc and sc.status == "submitted":
            continue
        notify(db, dept.hod_user_id, f"Department scorecard due - {kpi_svc.period_label(period)}",
               f"Submit the {dept.name} scorecard with highlights and risks before the trajectory meeting.",
               event_type="scorecard_due", link=f"/kpis/scorecards?period={period}&department_id={dept.id}")
        sent += 1
    db.flush()
    return {"period": period, "reminders": sent}


JOBS = [
    ("ops_task_escalation", escalate_overdue_tasks, 15),
    ("ops_daily_report_reminders", daily_report_reminders, 15),
    ("ops_kpi_snapshot", daily_kpi_snapshot, 1440),
    ("ops_anomaly_detection", anomaly_detection, 720),
    ("ops_recurring_tasks", generate_recurring_tasks, 1440),
    ("ops_scorecard_reminders", scorecard_reminders, 10080),
]
