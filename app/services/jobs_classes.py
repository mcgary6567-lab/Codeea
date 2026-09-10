"""Background jobs for scheduling, live classes, recordings, AI monitoring and QA.

Registered automatically by ``app.core.scheduler`` through the ``JOBS`` list.
Every job is idempotent and safe to run repeatedly; day-scoped work is guarded by a last-run Setting.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.notify import notify
from app.models.core import RiskAlert
from app.models.people import Leave
from app.models.scheduling import ClassSession, Recording, ReminderLog
from app.services import classes as class_svc
from app.services import qa as qa_svc
from app.services import scheduling as sched_svc

log = logging.getLogger("oqc.jobs.classes")

SESSION_HORIZON_DAYS = 14


# ----------------------------------------------------------------------------- helpers
def _ran_today(db: Session, key: str) -> bool:
    return sched_svc.setting_value(db, key, None, field="date") == date.today().isoformat()


def _mark_ran(db: Session, key: str, extra: dict | None = None) -> None:
    sched_svc.set_setting(db, key, {"date": date.today().isoformat(), "at": datetime.utcnow().isoformat(), **(extra or {})},
                          group="jobs", description="Last run marker for a scheduling background job")


def _already_reminded(db: Session, kind: str, session_id: int, user_id: int | None) -> bool:
    q = db.query(ReminderLog).filter(ReminderLog.reminder_type == kind, ReminderLog.entity_type == "ClassSession",
                                     ReminderLog.entity_id == session_id)
    if user_id:
        q = q.filter(ReminderLog.user_id == user_id)
    return db.query(q.exists()).scalar()


# ----------------------------------------------------------------------------- jobs
def generate_sessions_job(db: Session) -> str:
    """Once a day: materialise the next 14 days of class sessions for every active schedule."""
    if _ran_today(db, "job_generate_sessions_last_run"):
        return "already generated today"
    n = class_svc.generate_all_sessions(db, SESSION_HORIZON_DAYS)
    _mark_ran(db, "job_generate_sessions_last_run", {"created": n})
    return f"{n} sessions generated"


def class_reminders_job(db: Session) -> str:
    """Notify teacher (in-app) and student/parent (in-app + WhatsApp) before class starts."""
    cfg = sched_svc.setting_value(db, "class_reminder_minutes", {"teacher": 15, "student": 30}, field=None) or {}
    t_lead = int(cfg.get("teacher", 15) if isinstance(cfg, dict) else 15)
    s_lead = int(cfg.get("student", 30) if isinstance(cfg, dict) else 30)
    now = sched_svc.org_now()
    horizon = now + timedelta(minutes=max(t_lead, s_lead))
    rows = (db.query(ClassSession).filter(ClassSession.status == "pending", ClassSession.scheduled_start >= now,
                                          ClassSession.scheduled_start <= horizon).all())
    sent = 0
    for s in rows:
        minutes = int((s.scheduled_start - now).total_seconds() // 60)
        when = s.start_time.strftime("%H:%M")
        teacher = s.teacher
        if minutes <= t_lead and teacher and teacher.user_id and not _already_reminded(db, "class_teacher", s.id, teacher.user_id):
            notify(db, teacher.user_id, f"Class in {minutes} minutes",
                   f"{s.student.full_name if s.student else 'Your student'} at {when}. Room {s.room_name}.",
                   event_type="class_reminder", link=f"/classroom/{s.id}")
            db.add(ReminderLog(reminder_type="class_teacher", user_id=teacher.user_id, entity_type="ClassSession",
                               entity_id=s.id, channel="in_app"))
            s.reminder_sent_teacher = True
            sent += 1
        if minutes <= s_lead and s.student:
            targets = []
            if s.student.user_id:
                targets.append((s.student.user_id, ("in_app",), None))
            client = s.student.client
            if client and client.user_id:
                channels = ("in_app", "whatsapp") if client.whatsapp and client.whatsapp_opt_in else ("in_app",)
                targets.append((client.user_id, channels, client.whatsapp))
            for uid, channels, address in targets:
                if _already_reminded(db, "class_student", s.id, uid):
                    continue
                notify(db, uid, "Quran class reminder",
                       f"{s.student.full_name}'s class with {teacher.full_name if teacher else 'the teacher'} starts at {when}.",
                       event_type="class_reminder", link="/portal/schedule", channels=channels, recipient_address=address)
                for ch in channels:
                    db.add(ReminderLog(reminder_type="class_student", user_id=uid, entity_type="ClassSession",
                                       entity_id=s.id, channel=ch))
                s.reminder_sent_student = True
                sent += 1
    return f"{sent} reminders sent"


def auto_mark_missed_job(db: Session) -> str:
    grace = int(sched_svc.setting_value(db, "missed_class_grace_minutes", 10) or 10)
    n = class_svc.auto_mark_missed(db, grace)
    return f"{n} sessions auto-marked missed (grace {grace} min)"


def leave_reminders_job(db: Session) -> str:
    """Leave start / end reminders for parents and teachers, plus post-leave absence alerts."""
    today = date.today()
    sent = 0
    for lv in db.query(Leave).filter(Leave.person_type == "student", Leave.status == "approved").all():
        student = lv.student
        if not student:
            continue
        parties = [uid for uid in (student.user_id, student.client.user_id if student.client else None,
                                   student.teacher.user_id if student.teacher else None) if uid]
        if lv.start_date == today + timedelta(days=1) and not lv.reminder_sent_start:
            for uid in parties:
                notify(db, uid, "Leave starts tomorrow",
                       f"{student.full_name} is on approved {lv.leave_type} leave from {lv.start_date} to {lv.end_date}.",
                       event_type="leave_reminder", link="/leaves/students")
                db.add(ReminderLog(reminder_type="leave_start", user_id=uid, entity_type="Leave", entity_id=lv.id))
            lv.reminder_sent_start = True
            sent += len(parties)
        if lv.end_date == today - timedelta(days=1) and not lv.reminder_sent_end:
            for uid in parties:
                notify(db, uid, "Classes resume today",
                       f"{student.full_name}'s leave ended on {lv.end_date}. Classes resume as scheduled.",
                       event_type="leave_reminder", link="/portal/schedule")
                db.add(ReminderLog(reminder_type="leave_end", user_id=uid, entity_type="Leave", entity_id=lv.id))
            lv.reminder_sent_end = True
            sent += len(parties)
    flagged = sched_svc.flag_post_leave_absences(db)
    return f"{sent} leave reminders, {flagged} post-leave absences flagged"


def recording_pipeline_job(db: Session) -> str:
    """Ingest recordings for sessions finished more than five minutes ago and run AI analysis."""
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    since = date.today() - timedelta(days=2)
    rows = (db.query(ClassSession).filter(ClassSession.status == "done", ClassSession.date >= since,
                                          ClassSession.status_changed_at.isnot(None),
                                          ClassSession.status_changed_at <= cutoff).limit(50).all())
    ingested, analysed = 0, 0
    for s in rows:
        if not s.recording:
            try:
                qa_svc.ingest_recording(db, s, None)
                ingested += 1
            except ValueError:
                continue
        if not s.ai_analysis:
            qa_svc.run_ai_analysis(db, s)
            analysed += 1
    return f"{ingested} recordings ingested, {analysed} analysed"


def recording_retention_job(db: Session) -> str:
    if _ran_today(db, "job_recording_retention_last_run"):
        return "already checked today"
    n = qa_svc.purge_expired_recordings(db)
    expiring = db.query(Recording).filter(Recording.status == "available", Recording.retention_until.isnot(None),
                                          Recording.retention_until <= date.today() + timedelta(days=7)).count()
    _mark_ran(db, "job_recording_retention_last_run", {"expired": n})
    return f"{n} recordings expired, {expiring} expiring within 7 days"


def qa_sampling_job(db: Session) -> str:
    """Weekly random QA sample every Monday, plus corrective-action overdue refresh."""
    overdue = qa_svc.refresh_overdue_actions(db)
    if date.today().weekday() != 0:
        return f"not Monday; {overdue} corrective actions marked overdue"
    if _ran_today(db, "job_qa_weekly_sample_last_run"):
        return "weekly sample already drawn"
    picked = qa_svc.sample_random(db, 12)
    risk = qa_svc.sample_risk_based(db, 6)
    _mark_ran(db, "job_qa_weekly_sample_last_run", {"random": len(picked), "risk": len(risk)})
    return f"{len(picked)} random + {len(risk)} risk-based reviews queued, {overdue} overdue actions"


def unverified_teacher_gate_job(db: Session) -> str:
    """Safeguarding gate: unverified teachers must not hold live schedules."""
    if _ran_today(db, "job_unverified_gate_last_run"):
        return "already checked today"
    from app.models.people import Teacher
    from app.models.scheduling import Schedule
    n = 0
    for t in db.query(Teacher).filter(Teacher.is_verified.is_(False)).all():
        count = db.query(Schedule).filter(Schedule.teacher_id == t.id, Schedule.status == "active").count()
        if not count:
            continue
        exists = db.query(RiskAlert).filter(RiskAlert.alert_type == "unverified_teacher", RiskAlert.entity_id == t.id,
                                            RiskAlert.status.in_(["open", "acknowledged"])).first()
        if exists:
            continue
        db.add(RiskAlert(alert_type="unverified_teacher", severity="critical",
                         title=f"Unverified teacher holds {count} active schedule(s) - {t.full_name}",
                         message="Background verification is incomplete. Reassign the classes or complete the check.",
                         entity_type="Teacher", entity_id=t.id, visibility="management", source="system"))
        n += 1
    _mark_ran(db, "job_unverified_gate_last_run", {"alerts": n})
    return f"{n} unverified-teacher alerts raised"


JOBS = [
    ("classes.generate_sessions", generate_sessions_job, 180),
    ("classes.reminders", class_reminders_job, 5),
    ("classes.auto_missed", auto_mark_missed_job, 10),
    ("classes.leave_reminders", leave_reminders_job, 120),
    ("classes.recording_pipeline", recording_pipeline_job, 15),
    ("classes.recording_retention", recording_retention_job, 360),
    ("classes.qa_sampling", qa_sampling_job, 240),
    ("classes.unverified_gate", unverified_teacher_gate_job, 360),
]
