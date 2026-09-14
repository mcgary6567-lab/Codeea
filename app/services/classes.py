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


# ============================================================================= ERP parity (WP-3 appendix)
# Teacher availability marker, class activities, Class Status Summary grid and Schedule Summary report.
DURATION_HIGHLIGHTS = [(15, "duration15", "rose"), (20, "duration20", "orange"), (25, "duration25", "yellow"), (35, "duration35", "lime")]


def duration_highlight(actual: Optional[int]) -> Optional[dict]:
    """ERP highlight rules: <15 red, <20 orange, <25 yellow, <35 lime (None when no actual duration or >= 35)."""
    if actual is None:
        return None
    for limit, key, color in DURATION_HIGHLIGHTS:
        if actual < limit:
            return {"key": key, "color": color, "limit": limit}
    return None


def mark_available(db: Session, session: ClassSession, user: Optional[User], request=None) -> ClassSession:
    """'Teacher is Available': the teacher is in the room waiting for the student."""
    if session.status not in ("pending", "available"):
        raise ValueError(f"A class in status '{session.status}' cannot be marked available.")
    now = datetime.utcnow()
    session.teacher_available_at = session.teacher_available_at or now
    set_status(db, session, "available", user, reason="Teacher marked available", request=request, notify_parties=False)
    late = int((now + timedelta(hours=5) - session.scheduled_start).total_seconds() // 60)
    if session.student and session.student.client and session.student.client.user_id:
        notify(db, session.student.client.user_id, "Your teacher is waiting",
               f"{session.teacher.full_name if session.teacher else 'The teacher'} is available for the {session.start_time.strftime('%H:%M')} class.",
               event_type="class_status", link="/portal/schedule")
    session.teacher_late_minutes = max(0, late) if late > 0 else session.teacher_late_minutes
    db.flush()
    return session


def add_activity(db: Session, session: ClassSession, user: Optional[User], page_no: Optional[str], remarks: Optional[str],
                 book_id: Optional[int] = None, activity_type: str = "manual", request=None):
    """Record what was covered in a class and stamp ClassSession.activity_updated_at."""
    from app.models.erp import ClassActivity
    if not (page_no or remarks):
        raise ValueError("Enter a page number or remarks for the activity.")
    act = ClassActivity(session_id=session.id, student_id=session.student_id, teacher_id=session.teacher_id,
                        activity_type=activity_type, book_id=book_id, page_no=(page_no or None), remarks=(remarks or None),
                        created_by_id=user.id if user else None)
    db.add(act)
    session.activity_updated_at = datetime.utcnow()
    db.flush()
    log_action(db, user, "create", "classes", entity=act, description=f"Class activity added to session #{session.id}: page {page_no or '-'}",
               request=request)
    return act


def status_grid(db: Session, day: date, teacher_ids: Optional[list[int]] = None, category: Optional[str] = None,
                slot_id: Optional[int] = None) -> dict:
    """Class Status Summary: teachers (rows, Employee.sort_no order) x session slots (columns).

    Each cell: list of {"session", "status", "duration", "highlight", "late_available", "no_activity"}.
    """
    from app.models.erp import SessionSlot
    from app.models.people import Employee
    q = db.query(ClassSession).filter(ClassSession.date == day)
    if teacher_ids is not None:
        q = q.filter(ClassSession.teacher_id.in_(teacher_ids or [-1]))
    if slot_id:
        q = q.filter(ClassSession.slot_id == slot_id)
    if category:
        minutes = 45 if category.startswith("45") else 30
        q = q.filter(ClassSession.duration_minutes == minutes)
    rows = q.order_by(ClassSession.start_time).all()
    slot_by_time = {}
    for s in db.query(SessionSlot).filter(SessionSlot.status == "active").order_by(SessionSlot.sort_no):
        if category and s.category != category:
            continue
        slot_by_time.setdefault(s.start_time, s)
    used_times = sorted({s.start_time for s in rows})
    columns = []
    for t in used_times:
        sl = slot_by_time.get(t)
        columns.append({"time": t, "label": sl.label if sl else t.strftime("%I:%M %p"), "slot": sl})
    teachers: dict[int, dict] = {}
    for s in rows:
        row = teachers.setdefault(s.teacher_id, {"teacher": s.teacher, "cells": {}, "totals": {"total": 0, "done": 0, "missed": 0, "short": 0}})
        actual = s.actual_duration_minutes if s.status == "done" else None
        hl = duration_highlight(actual)
        late_available = bool(s.teacher_available_at and s.teacher_available_at + timedelta(hours=5) > s.scheduled_start + timedelta(minutes=1))
        no_activity = s.status == "done" and s.activity_updated_at is None
        row["cells"].setdefault(s.start_time, []).append({
            "session": s, "status": s.status, "duration": actual, "highlight": hl,
            "late_available": late_available, "no_activity": no_activity})
        row["totals"]["total"] += 1
        if s.status == "done":
            row["totals"]["done"] += 1
        if s.status == "missed":
            row["totals"]["missed"] += 1
        if hl:
            row["totals"]["short"] += 1
    order = {}
    for e in db.query(Employee).filter(Employee.is_teacher.is_(True)).all():
        order[e.id] = e.sort_no or 0

    def sort_key(item):
        t = item["teacher"]
        emp_sort = order.get(t.employee_id, 999) if t and t.employee_id else 999
        return (emp_sort, t.full_name if t else "")

    grid_rows = sorted(teachers.values(), key=sort_key)
    counts = {st: sum(1 for s in rows if s.status == st) for st in SESSION_STATUSES}
    counts["total"] = len(rows)
    return {"columns": columns, "rows": grid_rows, "counts": counts, "day": day}


def schedule_summary(db: Session, category: Optional[str] = None, teacher_ids: Optional[list[int]] = None) -> dict:
    """Schedule Summary Report: per teacher, per slot Free/Total. Free = number of weekdays (of 7) with no active schedule."""
    from app.models.erp import SessionSlot
    from app.models.people import Employee
    slots = db.query(SessionSlot).filter(SessionSlot.status == "active")
    if category:
        slots = slots.filter(SessionSlot.category == category)
    slots = slots.order_by(SessionSlot.sort_no, SessionSlot.start_time).all()
    # collapse to one column per start time (categories share the 48-slot grid)
    seen, columns = set(), []
    for s in slots:
        if s.start_time in seen:
            continue
        seen.add(s.start_time)
        columns.append(s)
    tq = db.query(Teacher).filter(Teacher.status != "inactive")
    if teacher_ids is not None:
        tq = tq.filter(Teacher.id.in_(teacher_ids or [-1]))
    teachers = tq.all()
    sort_no = {e.id: (e.sort_no or 0) for e in db.query(Employee).filter(Employee.is_teacher.is_(True))}
    teachers.sort(key=lambda t: (sort_no.get(t.employee_id, 999) if t.employee_id else 999, t.full_name))
    schedules = db.query(Schedule).filter(Schedule.status == "active", Schedule.teacher_id.in_([t.id for t in teachers] or [-1])).all()
    busy: dict[int, dict[time, set]] = {}
    for sch in schedules:
        st = time(sch.start_time.hour, sch.start_time.minute)
        busy.setdefault(sch.teacher_id, {}).setdefault(st, set()).update(int(d) for d in (sch.days_of_week or []))
    rows = []
    total_days = 7
    for t in teachers:
        cells = []
        free_sum = total_sum = 0
        for col in columns:
            used = len(busy.get(t.id, {}).get(col.start_time, set()))
            free = total_days - used
            cells.append({"slot": col, "free": free, "total": total_days, "used": used})
            free_sum += free
            total_sum += total_days
        rows.append({"teacher": t, "cells": cells, "free": free_sum, "total": total_sum,
                     "schedules": sum(1 for s in schedules if s.teacher_id == t.id)})
    return {"columns": columns, "rows": rows, "slot_count": len(columns)}


# ============================================================================= ERP parity (WP-3 appendix 2)
# Status tile counters, teacher-portal tiles, student local time and the monitoring panels shared by the
# Supervisor Portal (3.11) and the HOD / Academic Manager Portal (3.12).
def counts_by_status(query) -> dict:
    """status -> count for an existing ClassSession query, plus 'total'. The query is not consumed."""
    counts = {s: 0 for s in SESSION_STATUSES}
    rows = query.with_entities(ClassSession.status, func.count(ClassSession.id)).group_by(ClassSession.status).all()
    for status, n in rows:
        counts[status] = counts.get(status, 0) + n
    counts["total"] = sum(n for _, n in rows)
    return counts


def student_local_time(student, when: Optional[datetime] = None) -> dict:
    """"Client - Student Time Status": the class time (org / Asia-Karachi) rendered in the student's own zone."""
    from datetime import timezone as _tz
    org = "Asia/Karachi"
    tz_name = (getattr(student, "timezone", None) or "Europe/London") if student else "Europe/London"
    when = when or (datetime.utcnow() + timedelta(hours=5))
    try:
        from zoneinfo import ZoneInfo
        aware = when.replace(tzinfo=ZoneInfo(org))
        local = aware.astimezone(ZoneInfo(tz_name))
        offset = (local.utcoffset() or timedelta()) - (aware.utcoffset() or timedelta())
    except Exception:  # unknown zone / no tzdata
        local, offset = when, timedelta()
    hours = offset.total_seconds() / 3600.0
    return {"timezone": tz_name, "org_timezone": org, "org_time": when, "local_time": local,
            "offset_hours": round(hours, 1),
            "offset_label": ("same time" if abs(hours) < 0.01 else f"{'+' if hours > 0 else ''}{hours:g} h vs Pakistan")}


def teacher_day_tiles(db: Session, teacher_id: int, day: date) -> dict:
    """Teacher portal tiles: Total Classes, Regular, Trial, Arrangements (for one teacher on one day)."""
    rows = db.query(ClassSession).filter(ClassSession.teacher_id == teacher_id, ClassSession.date == day).all()
    return {"total": len(rows), "trial": sum(1 for r in rows if r.is_trial),
            "regular": sum(1 for r in rows if not r.is_trial),
            "arrangements": sum(1 for r in rows if r.arrangement_id or r.substitute_for_teacher_id),
            "done": sum(1 for r in rows if r.status == "done"), "pending": sum(1 for r in rows if r.status == "pending")}


MONITOR_PANELS = [
    ("pending", "Pending Classes", "clock", "amber"),
    ("available", "Marked Available", "hand", "sky"),
    ("started", "Started Classes", "play-circle", "indigo"),
    ("done", "Done Classes", "check-circle-2", "emerald"),
    ("missed", "Missed Classes", "user-x", "rose"),
    ("absent", "Absent Students Classes", "user-minus", "orange"),
    ("leave", "Student On Leave Classes", "palmtree", "violet"),
    ("cancelled", "Cancelled", "ban", "slate"),
]


def erp_monitor(db: Session, day: date, teacher_ids: Optional[list[int]] = None, hours_ahead: int = 2) -> dict:
    """Supervisor / HOD monitoring dashboard: ERP tiles + the per-status panels, upcoming classes and trials.

    Tiles: Pending, Done, Missed, Student Absent, Student On Leave, Cancelled, Reschedule Classes.
    """
    from app.models.erp import ClassQuery, RescheduleRequest
    from app.services import scheduling as sched
    q = db.query(ClassSession).filter(ClassSession.date == day)
    if teacher_ids is not None:
        q = q.filter(ClassSession.teacher_id.in_(teacher_ids or [-1]))
    rows = q.order_by(ClassSession.scheduled_start).all()
    now = sched.org_now()
    by_status: dict[str, list] = {}
    for s in rows:
        by_status.setdefault(s.status, []).append(s)
    panels = [{"key": k, "label": lbl, "icon": ic, "color": col, "items": by_status.get(k, [])}
              for k, lbl, ic, col in MONITOR_PANELS]
    upcoming = [s for s in rows if s.status in ("pending", "available") and now <= s.scheduled_start <= now + timedelta(hours=hours_ahead)]
    rq = db.query(RescheduleRequest).filter(RescheduleRequest.status == "pending")
    if teacher_ids is not None:
        rq = rq.filter(RescheduleRequest.old_teacher_id.in_(teacher_ids or [-1]))
    reschedules = rq.order_by(RescheduleRequest.id.desc()).limit(50).all()
    cq = db.query(ClassQuery).filter(ClassQuery.status == "pending")
    if teacher_ids is not None:
        cq = cq.filter(ClassQuery.teacher_id.in_(teacher_ids or [-1]))
    queries = cq.order_by(ClassQuery.id.desc()).limit(50).all()
    trials = [s for s in rows if s.is_trial and s.status in ("pending", "available", "started")]
    counts = {k: len(by_status.get(k, [])) for k, _, _, _ in MONITOR_PANELS}
    counts["total"] = len(rows)
    counts["rescheduled"] = len(by_status.get("rescheduled", []))
    tiles = [
        {"key": "pending", "label": "Pending", "value": counts.get("pending", 0), "href": f"/classes?date={day}&status=pending"},
        {"key": "done", "label": "Done", "value": counts.get("done", 0), "href": f"/classes?date={day}&status=done"},
        {"key": "missed", "label": "Missed", "value": counts.get("missed", 0), "href": f"/classes?date={day}&status=missed"},
        {"key": "absent", "label": "Student Absent", "value": counts.get("absent", 0), "href": f"/classes?date={day}&status=absent"},
        {"key": "leave", "label": "Student On Leave", "value": counts.get("leave", 0), "href": f"/classes?date={day}&status=leave"},
        {"key": "cancelled", "label": "Cancelled", "value": counts.get("cancelled", 0), "href": f"/classes?date={day}&status=cancelled"},
        {"key": "reschedule", "label": "Reschedule Classes", "value": len(reschedules), "href": "/classes/rescheduled?status=pending"},
    ]
    return {"day": day, "rows": rows, "panels": panels, "counts": counts, "tiles": tiles, "now": now,
            "upcoming": upcoming, "reschedules": reschedules, "queries": queries, "trials": trials}
