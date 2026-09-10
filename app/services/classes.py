"""Shared class-session service used by dashboards, portals, supervisor view, QA and AI pipelines.

All status transitions go through ``set_status`` so audit logging, attendance and alerts are consistent.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.notify import notify
from app.models.core import RiskAlert, User
from app.models.people import Student, Teacher
from app.models.scheduling import Schedule, ClassSession, Attendance, SESSION_STATUSES
from app.services.integrations import build_join_url

SLOT_MINUTES = 30
DAY_SLOTS = [time(h, m) for h in range(24) for m in (0, 30)]  # 48 half-hour slots


def room_name_for(schedule_or_session) -> str:
    sid = getattr(schedule_or_session, "student_id", 0)
    tid = getattr(schedule_or_session, "teacher_id", 0)
    return f"OQC-{tid:03d}-{sid:04d}"


def slot_label(t: time) -> str:
    return t.strftime("%H:%M")


def generate_sessions(db: Session, schedule: Schedule, start: date, end: date) -> list[ClassSession]:
    """Materialise ClassSession rows for a recurring schedule between two dates (idempotent)."""
    if schedule.status != "active":
        return []
    existing = {s.date for s in db.query(ClassSession.date).filter(ClassSession.schedule_id == schedule.id,
                                                                    ClassSession.date >= start, ClassSession.date <= end)}
    created = []
    d = max(start, schedule.start_date)
    last = min(end, schedule.end_date) if schedule.end_date else end
    while d <= last:
        if d.weekday() in (schedule.days_of_week or []) and d not in existing:
            st = datetime.combine(d, schedule.start_time)
            et = st + timedelta(minutes=schedule.duration_minutes or SLOT_MINUTES)
            room = schedule.room_name or room_name_for(schedule)
            s = ClassSession(schedule_id=schedule.id, student_id=schedule.student_id, teacher_id=schedule.teacher_id,
                             course_id=schedule.course_id, date=d, start_time=schedule.start_time, end_time=et.time(),
                             scheduled_start=st, duration_minutes=schedule.duration_minutes or SLOT_MINUTES,
                             status="pending", is_trial=schedule.is_trial, room_name=room, join_url=build_join_url(room))
            db.add(s)
            created.append(s)
        d += timedelta(days=1)
    db.flush()
    return created


def generate_all_sessions(db: Session, days_ahead: int = 14) -> int:
    today = date.today()
    n = 0
    for sch in db.query(Schedule).filter(Schedule.status == "active"):
        n += len(generate_sessions(db, sch, today, today + timedelta(days=days_ahead)))
    return n


def has_conflict(db: Session, teacher_id: int, days: Iterable[int], start_time: time, duration: int, exclude_schedule_id: Optional[int] = None,
                 student_id: Optional[int] = None) -> Optional[Schedule]:
    """Return a conflicting schedule for the teacher (or student) if any."""
    end_minutes = start_time.hour * 60 + start_time.minute + duration
    q = db.query(Schedule).filter(Schedule.status == "active")
    if student_id:
        q = q.filter((Schedule.teacher_id == teacher_id) | (Schedule.student_id == student_id))
    else:
        q = q.filter(Schedule.teacher_id == teacher_id)
    if exclude_schedule_id:
        q = q.filter(Schedule.id != exclude_schedule_id)
    days = set(days)
    for other in q:
        if not days.intersection(set(other.days_of_week or [])):
            continue
        o_start = other.start_time.hour * 60 + other.start_time.minute
        o_end = o_start + (other.duration_minutes or SLOT_MINUTES)
        s_start = start_time.hour * 60 + start_time.minute
        if s_start < o_end and o_start < end_minutes:
            return other
    return None


def set_status(db: Session, session: ClassSession, status: str, user: Optional[User], reason: Optional[str] = None,
               request=None, notify_parties: bool = True) -> ClassSession:
    if status not in SESSION_STATUSES:
        raise ValueError(f"invalid status {status}")
    before = session.status
    session.status = status
    session.status_changed_by_id = user.id if user else None
    session.status_changed_at = datetime.utcnow()
    if reason:
        session.status_reason = reason
    now = datetime.utcnow()
    if status == "started" and not session.teacher_joined_at:
        session.teacher_joined_at = now
        session.teacher_late_minutes = max(0, int((now - session.scheduled_start).total_seconds() // 60))
    if status == "done":
        session.teacher_left_at = session.teacher_left_at or now
        if session.teacher_joined_at:
            session.actual_duration_minutes = max(1, int((session.teacher_left_at - session.teacher_joined_at).total_seconds() // 60))
        else:
            session.actual_duration_minutes = session.duration_minutes
    # attendance record for terminal states
    if status in ("done", "missed", "absent", "leave"):
        att = db.query(Attendance).filter(Attendance.session_id == session.id).first()
        if not att:
            att = Attendance(session_id=session.id, student_id=session.student_id, teacher_id=session.teacher_id, date=session.date)
            db.add(att)
        att.student_status = {"done": "present", "missed": "present", "absent": "absent", "leave": "leave"}[status]
        att.teacher_status = {"done": "late" if session.teacher_late_minutes > 5 else "present", "missed": "absent", "absent": "present", "leave": "present"}[status]
        att.marked_by_id = user.id if user else None
    if status == "missed":
        db.add(RiskAlert(alert_type="missed_class", severity="high", title=f"Class missed by teacher — {session.teacher.full_name if session.teacher else session.teacher_id}",
                         message=f"Student {session.student.full_name if session.student else session.student_id} at {session.start_time.strftime('%H:%M')} on {session.date}. {reason or ''}",
                         entity_type="ClassSession", entity_id=session.id, visibility="ops", source="system"))
    log_action(db, user, "schedule_change" if status in ("cancelled", "rescheduled") else "status_change", "classes", entity=session,
               description=f"Class status {before} → {status}", rationale=reason, before={"status": before}, after={"status": status},
               request=request, consequential=status in ("cancelled", "rescheduled", "missed"))
    if notify_parties and status in ("cancelled", "rescheduled", "missed"):
        for uid in _party_user_ids(session):
            notify(db, uid, f"Class {status}", f"Class on {session.date} at {session.start_time.strftime('%H:%M')} was marked {status}. {reason or ''}",
                   event_type="class_status", link="/portal/schedule")
    db.flush()
    return session


def _party_user_ids(session: ClassSession) -> list[int]:
    ids = []
    if session.teacher and session.teacher.user_id:
        ids.append(session.teacher.user_id)
    if session.student:
        if session.student.user_id:
            ids.append(session.student.user_id)
        if session.student.client and session.student.client.user_id:
            ids.append(session.student.client.user_id)
    return ids


def mark_join(db: Session, session: ClassSession, who: str) -> None:
    now = datetime.utcnow()
    if who == "teacher":
        if not session.teacher_joined_at:
            session.teacher_joined_at = now
            session.teacher_late_minutes = max(0, int((now - session.scheduled_start).total_seconds() // 60))
        if session.status == "pending":
            session.status = "started"
            session.status_changed_at = now
    else:
        if not session.student_joined_at:
            session.student_joined_at = now
    db.flush()


def counters_for_date(db: Session, day: date, teacher_ids: Optional[list[int]] = None, shift_id: Optional[int] = None) -> dict:
    """Academic Home Dashboard counters: Pending, Started, Done, Missed, Absent, Leave, Cancelled, Rescheduled, Free."""
    q = db.query(ClassSession.status, func.count(ClassSession.id)).filter(ClassSession.date == day)
    if teacher_ids is not None:
        q = q.filter(ClassSession.teacher_id.in_(teacher_ids or [-1]))
    if shift_id:
        q = q.join(Schedule, Schedule.id == ClassSession.schedule_id).filter(Schedule.shift_id == shift_id)
    counts = {s: 0 for s in SESSION_STATUSES}
    for status, n in q.group_by(ClassSession.status):
        counts[status] = n
    counts["total"] = sum(counts[s] for s in SESSION_STATUSES)
    counts["free_students"] = db.query(Student).filter(Student.status == "free").count()
    return counts


def auto_mark_missed(db: Session, grace_minutes: int = 10) -> int:
    """Sessions still pending after scheduled start + grace → missed (teacher no-show). Returns count."""
    cutoff = datetime.utcnow() + timedelta(hours=5) - timedelta(minutes=grace_minutes)  # org time ≈ UTC+5 (Asia/Karachi)
    n = 0
    for s in db.query(ClassSession).filter(ClassSession.status == "pending", ClassSession.scheduled_start < cutoff):
        set_status(db, s, "missed", None, reason="Auto-marked: teacher did not start the class within the grace period")
        n += 1
    return n


def teacher_stats(db: Session, teacher_id: int, since: Optional[date] = None) -> dict:
    q = db.query(ClassSession).filter(ClassSession.teacher_id == teacher_id)
    if since:
        q = q.filter(ClassSession.date >= since)
    rows = q.all()
    total = len(rows)
    done = sum(1 for r in rows if r.status == "done")
    missed = sum(1 for r in rows if r.status == "missed")
    late = sum(1 for r in rows if r.status == "done" and (r.teacher_late_minutes or 0) > 5)
    return {"total": total, "done": done, "missed": missed, "late": late,
            "completion_rate": round(100 * done / total, 1) if total else 0.0,
            "punctuality_rate": round(100 * (done - late) / done, 1) if done else 100.0,
            "missed_rate": round(100 * missed / total, 1) if total else 0.0}


def student_attendance_pct(db: Session, student_id: int, days: int = 30) -> float:
    since = date.today() - timedelta(days=days)
    rows = db.query(ClassSession.status).filter(ClassSession.student_id == student_id, ClassSession.date >= since,
                                                ClassSession.date <= date.today(),
                                                ClassSession.status.in_(["done", "absent", "missed", "leave"])).all()
    if not rows:
        return 100.0
    present = sum(1 for (s,) in rows if s in ("done", "missed"))
    return round(100 * present / len(rows), 1)
