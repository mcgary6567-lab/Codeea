"""Scheduling service: schedule CRUD with conflict detection, session regeneration, teacher substitution,
student-leave synchronisation, rescheduling, supervisor scoping and grid/timeline builders.

All status transitions still go through ``app.services.classes.set_status``.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.notify import notify
from app.models.core import Setting, User, RiskAlert
from app.models.people import Teacher, Student, Leave
from app.models.scheduling import Schedule, ClassSession, Shift, ReminderLog, SESSION_STATUSES
from app.services import classes as class_svc

ORG_UTC_OFFSET_HOURS = 5  # Asia/Karachi
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
COURSE_COLORS = {"QAIDA": "sky", "NAZRA": "emerald", "HIFZ": "violet", "TAJWEED": "indigo", "TARJUMA": "amber", "ISLAMIC": "orange"}
DURATIONS = [30, 45, 60]
SLOTS = class_svc.DAY_SLOTS


# ----------------------------------------------------------------------------- time helpers
def org_now() -> datetime:
    """Naive organisation-local time (Asia/Karachi), comparable with ClassSession.scheduled_start."""
    return datetime.utcnow() + timedelta(hours=ORG_UTC_OFFSET_HOURS)


def setting_value(db: Session, key: str, default=None, field: Optional[str] = "value"):
    s = db.query(Setting).filter(Setting.key == key).first()
    if not s or s.value is None:
        return default
    v = s.value
    if isinstance(v, dict):
        if field and field in v:
            return v[field]
        return v if field is None else default
    return v


def set_setting(db: Session, key: str, value: dict, group: str = "jobs", description: str = "") -> None:
    s = db.query(Setting).filter(Setting.key == key).first()
    if not s:
        s = Setting(key=key, group=group, description=description)
        db.add(s)
    s.value = value
    db.flush()


def course_color(course) -> str:
    return COURSE_COLORS.get(getattr(course, "code", "") or "", "slate")


def day_label(days: Iterable[int]) -> str:
    return ", ".join(DAY_NAMES[d] for d in sorted(set(int(x) for x in (days or []))) if 0 <= d < 7) or "-"


# ----------------------------------------------------------------------------- scoping
def scoped_teacher_ids(db: Session, user: User) -> Optional[list[int]]:
    """None = unrestricted. Supervisors see only the teachers they supervise; teachers only themselves."""
    if user.is_superuser or rbac.is_management(user):
        return None
    slug = user.role_slug
    if slug == "supervisor":
        return [tid for (tid,) in db.query(Teacher.id).filter(Teacher.supervisor_id == user.id)]
    if slug == "teacher":
        t = db.query(Teacher).filter(Teacher.user_id == user.id).first()
        return [t.id] if t else []
    return None


def teachers_for(db: Session, user: User, verified_only: bool = False) -> list[Teacher]:
    q = db.query(Teacher).filter(Teacher.status != "inactive")
    ids = scoped_teacher_ids(db, user)
    if ids is not None:
        q = q.filter(Teacher.id.in_(ids or [-1]))
    if verified_only:
        q = q.filter(Teacher.is_verified.is_(True))
    return q.order_by(Teacher.full_name).all()


# ----------------------------------------------------------------------------- schedules
def describe_conflict(other: Schedule, teacher_id: int, student_id: Optional[int]) -> str:
    if other.teacher_id == teacher_id:
        who = "Teacher"
        name = other.teacher.full_name if other.teacher else f"#{other.teacher_id}"
        with_ = other.student.full_name if other.student else f"student #{other.student_id}"
    else:
        who = "Student"
        name = other.student.full_name if other.student else f"#{other.student_id}"
        with_ = other.teacher.full_name if other.teacher else f"teacher #{other.teacher_id}"
    return (f"Conflict: {who} {name} already has a class at {other.start_time.strftime('%H:%M')} "
            f"({other.duration_minutes} min) on {day_label(other.days_of_week)} with {with_} (schedule #{other.id}).")


def _parse_time(value) -> time:
    if isinstance(value, time):
        return value
    h, m = str(value)[:5].split(":")
    return time(int(h), int(m))


def create_schedule(db: Session, user: Optional[User], *, student_id: int, teacher_id: int, days: list[int], start_time, duration: int,
                    course_id: Optional[int] = None, subscription_id: Optional[int] = None, shift_id: Optional[int] = None,
                    start_date: Optional[date] = None, end_date: Optional[date] = None, is_trial: bool = False, notes: Optional[str] = None,
                    request=None, horizon_days: int = 14) -> Schedule:
    teacher = db.get(Teacher, teacher_id)
    student = db.get(Student, student_id)
    if not teacher or not student:
        raise ValueError("Teacher or student not found.")
    if not teacher.is_verified:
        raise ValueError(f"{teacher.full_name} has not passed background verification and cannot be scheduled for live classes.")
    days = sorted({int(d) for d in days if 0 <= int(d) < 7})
    if not days:
        raise ValueError("Select at least one day of the week.")
    st = _parse_time(start_time)
    if st.minute not in (0, 30):
        raise ValueError("Start time must be on a 30-minute slot boundary.")
    duration = int(duration) if int(duration) in DURATIONS else 30
    conflict = class_svc.has_conflict(db, teacher_id, days, st, duration, student_id=student_id)
    if conflict:
        raise ValueError(describe_conflict(conflict, teacher_id, student_id))
    sch = Schedule(student_id=student_id, teacher_id=teacher_id, subscription_id=subscription_id, course_id=course_id or student.course_id,
                   days_of_week=days, start_time=st, duration_minutes=duration, start_date=start_date or date.today(), end_date=end_date,
                   shift_id=shift_id or shift_for_time(db, st), status="active", is_trial=is_trial, notes=notes)
    sch.room_name = class_svc.room_name_for(sch)
    db.add(sch)
    db.flush()
    today = date.today()
    created = class_svc.generate_sessions(db, sch, today, today + timedelta(days=horizon_days))
    log_action(db, user, "create", "schedules", entity=sch,
               description=f"Schedule created: {student.full_name} with {teacher.full_name} at {st.strftime('%H:%M')} on {day_label(days)}; {len(created)} sessions generated",
               after={"days": days, "start_time": st.isoformat(), "duration": duration}, request=request)
    return sch


def shift_for_time(db: Session, t: time) -> Optional[int]:
    for sh in db.query(Shift).filter(Shift.is_active.is_(True)).order_by(Shift.id):
        if sh.start_time <= sh.end_time:
            if sh.start_time <= t < sh.end_time:
                return sh.id
        elif t >= sh.start_time or t < sh.end_time:  # wraps midnight
            return sh.id
    return None


def regenerate_future_sessions(db: Session, sch: Schedule, horizon_days: int = 14) -> int:
    """Drop future pending sessions (not yet started) and rebuild them from the schedule definition."""
    now = org_now()
    for s in db.query(ClassSession).filter(ClassSession.schedule_id == sch.id, ClassSession.status == "pending",
                                           ClassSession.scheduled_start > now):
        db.delete(s)
    db.flush()
    if sch.status != "active":
        return 0
    today = date.today()
    return len(class_svc.generate_sessions(db, sch, today, today + timedelta(days=horizon_days)))


def update_schedule(db: Session, user: User, sch: Schedule, *, teacher_id: int, days: list[int], start_time, duration: int,
                    course_id=None, subscription_id=None, shift_id=None, start_date=None, end_date=None, notes=None, reason: str = "",
                    request=None) -> Schedule:
    teacher = db.get(Teacher, teacher_id)
    if not teacher or not teacher.is_verified:
        raise ValueError("Selected teacher is not available for live classes (not verified).")
    days = sorted({int(d) for d in days if 0 <= int(d) < 7})
    if not days:
        raise ValueError("Select at least one day of the week.")
    st = _parse_time(start_time)
    duration = int(duration) if int(duration) in DURATIONS else 30
    conflict = class_svc.has_conflict(db, teacher_id, days, st, duration, exclude_schedule_id=sch.id, student_id=sch.student_id)
    if conflict:
        raise ValueError(describe_conflict(conflict, teacher_id, sch.student_id))
    before = {"teacher_id": sch.teacher_id, "days": sch.days_of_week, "start_time": sch.start_time.isoformat(), "duration": sch.duration_minutes}
    sch.teacher_id, sch.days_of_week, sch.start_time, sch.duration_minutes = teacher_id, days, st, duration
    sch.course_id = course_id or sch.course_id
    sch.subscription_id = subscription_id
    sch.shift_id = shift_id or shift_for_time(db, st)
    sch.start_date = start_date or sch.start_date
    sch.end_date = end_date
    sch.notes = notes
    sch.room_name = class_svc.room_name_for(sch)
    n = regenerate_future_sessions(db, sch)
    log_action(db, user, "schedule_change", "schedules", entity=sch, description=f"Schedule updated; {n} future sessions regenerated",
               rationale=reason, before=before,
               after={"teacher_id": teacher_id, "days": days, "start_time": st.isoformat(), "duration": duration}, request=request)
    return sch


def schedule_party_ids(sch: Schedule) -> list[int]:
    ids = []
    if sch.teacher and sch.teacher.user_id:
        ids.append(sch.teacher.user_id)
    if sch.student:
        if sch.student.user_id:
            ids.append(sch.student.user_id)
        if sch.student.client and sch.student.client.user_id:
            ids.append(sch.student.client.user_id)
    return ids


def change_schedule_status(db: Session, user: User, sch: Schedule, status: str, reason: str, request=None) -> int:
    """pause / end / resume a schedule. Future pending sessions are cancelled (or regenerated on resume)."""
    if status not in ("active", "paused", "ended"):
        raise ValueError("Invalid schedule status")
    before = sch.status
    sch.status = status
    if status == "ended" and not sch.end_date:
        sch.end_date = date.today()
    n = 0
    if status in ("paused", "ended"):
        now = org_now()
        for s in db.query(ClassSession).filter(ClassSession.schedule_id == sch.id, ClassSession.status == "pending",
                                               ClassSession.scheduled_start > now):
            class_svc.set_status(db, s, "cancelled", user, reason=f"Schedule {status}: {reason}", request=request, notify_parties=False)
            n += 1
        for uid in schedule_party_ids(sch):
            notify(db, uid, f"Schedule {status}",
                   f"The recurring class at {sch.start_time.strftime('%H:%M')} ({day_label(sch.days_of_week)}) was {status}. {reason}",
                   event_type="schedule_change", link="/portal/schedule")
    elif status == "active":
        n = regenerate_future_sessions(db, sch)
    log_action(db, user, "schedule_change", "schedules", entity=sch, description=f"Schedule {before} -> {status} ({n} sessions affected)",
               rationale=reason, before={"status": before}, after={"status": status}, request=request)
    return n


def bulk_teacher_change(db: Session, user: User, from_teacher: Teacher, to_teacher: Teacher, start: date, end: Optional[date], reason: str,
                        request=None) -> dict:
    """Substitute / reassign: move teacher A's schedules (or only the sessions in a date range) to teacher B."""
    if not to_teacher.is_verified:
        raise ValueError(f"{to_teacher.full_name} is not verified and cannot take live classes.")
    if from_teacher.id == to_teacher.id:
        raise ValueError("Choose a different substitute teacher.")
    permanent = end is None
    moved_schedules, moved_sessions, skipped = 0, 0, []
    for sch in db.query(Schedule).filter(Schedule.teacher_id == from_teacher.id, Schedule.status == "active").all():
        conflict = class_svc.has_conflict(db, to_teacher.id, sch.days_of_week or [], sch.start_time, sch.duration_minutes or 30,
                                          exclude_schedule_id=sch.id)
        if conflict:
            skipped.append(f"{sch.student.full_name if sch.student else sch.id} ({sch.start_time.strftime('%H:%M')})")
            continue
        q = db.query(ClassSession).filter(ClassSession.schedule_id == sch.id, ClassSession.status == "pending", ClassSession.date >= start)
        if end:
            q = q.filter(ClassSession.date <= end)
        for s in q:
            s.teacher_id = to_teacher.id
            s.substitute_for_teacher_id = from_teacher.id
            moved_sessions += 1
        if permanent:
            sch.teacher_id = to_teacher.id
            sch.room_name = class_svc.room_name_for(sch)
            moved_schedules += 1
        verb = "take over" if permanent else "cover"
        until = "" if permanent else " to " + end.strftime("%d %b")
        body = (f"{to_teacher.full_name} will {verb} the {sch.start_time.strftime('%H:%M')} class "
                f"({day_label(sch.days_of_week)}) from {start.strftime('%d %b')}{until}. Reason: {reason}")
        for uid in schedule_party_ids(sch):
            if uid == from_teacher.user_id:
                continue
            notify(db, uid, "Teacher change", body, event_type="teacher_change", link="/portal/schedule")
            db.add(ReminderLog(reminder_type="teacher_change", user_id=uid, entity_type="Schedule", entity_id=sch.id, channel="in_app"))
        client = sch.student.client if sch.student else None
        if client and client.whatsapp and client.user_id:
            notify(db, client.user_id, "Teacher change", body, event_type="teacher_change", channels=("whatsapp",), recipient_address=client.whatsapp)
    for t in (from_teacher, to_teacher):
        if t.user_id:
            notify(db, t.user_id, "Teacher change propagation",
                   f"{moved_sessions} session(s) of {from_teacher.full_name} were {'reassigned to' if permanent else 'covered by'} {to_teacher.full_name} from {start}. {reason}",
                   event_type="teacher_change", link="/teacher/schedule")
    log_action(db, user, "schedule_change", "schedules", entity=to_teacher, entity_type="Teacher",
               description=(f"Bulk teacher change {from_teacher.full_name} -> {to_teacher.full_name}: {moved_schedules} schedules, "
                            f"{moved_sessions} sessions ({'permanent' if permanent else str(start) + ' to ' + str(end)}); skipped {len(skipped)}"),
               rationale=reason, request=request)
    return {"schedules": moved_schedules, "sessions": moved_sessions, "skipped": skipped, "permanent": permanent}


# ----------------------------------------------------------------------------- sessions
def reschedule_session(db: Session, user: User, session: ClassSession, new_start: datetime, reason: str, request=None) -> ClassSession:
    if session.status not in ("pending", "started", "missed", "absent", "cancelled"):
        raise ValueError(f"A session in status '{session.status}' cannot be rescheduled.")
    end = new_start + timedelta(minutes=session.duration_minutes or 30)
    others = db.query(ClassSession).filter(ClassSession.teacher_id == session.teacher_id, ClassSession.date == new_start.date(),
                                           ClassSession.status.in_(["pending", "started"]), ClassSession.id != session.id).all()
    for o in others:
        o_end = o.scheduled_start + timedelta(minutes=o.duration_minutes or 30)
        if o.scheduled_start < end and new_start < o_end:
            raise ValueError(f"Teacher already has a class at {o.start_time.strftime('%H:%M')} on {o.date}.")
    new = ClassSession(schedule_id=session.schedule_id, student_id=session.student_id, teacher_id=session.teacher_id, course_id=session.course_id,
                       date=new_start.date(), start_time=new_start.time(), end_time=end.time(), scheduled_start=new_start,
                       duration_minutes=session.duration_minutes, status="pending", is_trial=session.is_trial, room_name=session.room_name,
                       join_url=session.join_url, lesson_id=session.lesson_id,
                       status_reason=f"Rescheduled from {session.date} {session.start_time.strftime('%H:%M')}")
    db.add(new)
    db.flush()
    session.rescheduled_to_id = new.id
    class_svc.set_status(db, session, "rescheduled", user, reason=reason, request=request)
    log_action(db, user, "create", "classes", entity=new, description=f"Rescheduled session created from #{session.id}", rationale=reason, request=request)
    return new


def sessions_query(db: Session, *, day: Optional[date] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
                   status: str = "", teacher_id: Optional[int] = None, student_id: Optional[int] = None, course_id: Optional[int] = None,
                   shift_id: Optional[int] = None, trial: Optional[bool] = None, teacher_ids: Optional[list[int]] = None, q: str = ""):
    qry = db.query(ClassSession)
    if day:
        qry = qry.filter(ClassSession.date == day)
    if date_from:
        qry = qry.filter(ClassSession.date >= date_from)
    if date_to:
        qry = qry.filter(ClassSession.date <= date_to)
    if status:
        qry = qry.filter(ClassSession.status == status)
    if teacher_id:
        qry = qry.filter(ClassSession.teacher_id == teacher_id)
    if student_id:
        qry = qry.filter(ClassSession.student_id == student_id)
    if course_id:
        qry = qry.filter(ClassSession.course_id == course_id)
    if trial is not None:
        qry = qry.filter(ClassSession.is_trial.is_(trial))
    if shift_id:
        qry = qry.join(Schedule, Schedule.id == ClassSession.schedule_id).filter(Schedule.shift_id == shift_id)
    if teacher_ids is not None:
        qry = qry.filter(ClassSession.teacher_id.in_(teacher_ids or [-1]))
    if q:
        like = f"%{q}%"
        qry = qry.join(Student, Student.id == ClassSession.student_id).filter(
            or_(Student.full_name.ilike(like), Student.student_code.ilike(like), ClassSession.room_name.ilike(like)))
    return qry


def timeline_for_day(db: Session, day: date, teacher_ids: Optional[list[int]] = None, hours_ahead: int = 4, hours_back: int = 1) -> list[dict]:
    """Sessions grouped by hour for the live timeline around 'now' (or the whole day when not today)."""
    q = sessions_query(db, day=day, teacher_ids=teacher_ids)
    if day == date.today():
        now = org_now()
        lo = (now - timedelta(hours=hours_back)).replace(minute=0, second=0, microsecond=0)
        hi = now + timedelta(hours=hours_ahead)
        q = q.filter(ClassSession.scheduled_start >= lo, ClassSession.scheduled_start <= hi)
    rows = q.order_by(ClassSession.scheduled_start).all()
    groups: dict[int, list] = {}
    for s in rows:
        groups.setdefault(s.start_time.hour, []).append(s)
    out = []
    for h in sorted(groups):
        items = groups[h]
        out.append({"hour": h, "label": f"{h:02d}:00", "items": items,
                    "counts": {st: sum(1 for i in items if i.status == st) for st in SESSION_STATUSES}})
    return out


def week_grid(db: Session, teacher_id: Optional[int] = None, student_id: Optional[int] = None) -> dict:
    """48 half-hour slots x 7 days. grid[slot_idx][day] = [{"schedule", "start": bool}]."""
    q = db.query(Schedule).filter(Schedule.status == "active")
    if teacher_id:
        q = q.filter(Schedule.teacher_id == teacher_id)
    if student_id:
        q = q.filter(Schedule.student_id == student_id)
    grid = {i: {d: [] for d in range(7)} for i in range(48)}
    for sch in q.all():
        start_idx = sch.start_time.hour * 2 + (1 if sch.start_time.minute >= 30 else 0)
        n_slots = max(1, -(-(sch.duration_minutes or 30) // 30))
        for d in sch.days_of_week or []:
            for k in range(n_slots):
                idx = start_idx + k
                if idx < 48:
                    grid[idx][int(d)].append({"schedule": sch, "start": k == 0})
    used = [i for i in range(48) if any(grid[i][d] for d in range(7))]
    return {"grid": grid, "slots": SLOTS, "used_slots": used, "days": DAY_NAMES}


# ----------------------------------------------------------------------------- leaves
def apply_student_leave(db: Session, leave: Leave, user: Optional[User], request=None) -> int:
    """Approved student leave: pending sessions in the range become 'leave'."""
    n = 0
    q = db.query(ClassSession).filter(ClassSession.student_id == leave.student_id, ClassSession.date >= leave.start_date,
                                      ClassSession.date <= leave.end_date, ClassSession.status == "pending")
    for s in q.all():
        class_svc.set_status(db, s, "leave", user, reason=f"Approved {leave.leave_type} leave #{leave.id}", request=request, notify_parties=False)
        n += 1
    return n


def revert_student_leave(db: Session, leave: Leave, user: Optional[User], request=None) -> int:
    """Rejected / cancelled leave: future sessions that were placed on leave go back to pending."""
    n = 0
    now = org_now()
    q = db.query(ClassSession).filter(ClassSession.student_id == leave.student_id, ClassSession.date >= leave.start_date,
                                      ClassSession.date <= leave.end_date, ClassSession.status == "leave", ClassSession.scheduled_start > now)
    for s in q.all():
        class_svc.set_status(db, s, "pending", user, reason=f"Leave #{leave.id} {leave.status}", request=request, notify_parties=False)
        n += 1
    return n


def first_session_after_leave(db: Session, leave: Leave) -> Optional[ClassSession]:
    return (db.query(ClassSession).filter(ClassSession.student_id == leave.student_id, ClassSession.date > leave.end_date,
                                          ClassSession.status.in_(["done", "absent", "missed"]))
            .order_by(ClassSession.scheduled_start).first())


def flag_post_leave_absences(db: Session) -> int:
    """Students absent in their first class after an approved leave ended -> RiskAlert (retention signal)."""
    n = 0
    today = date.today()
    q = db.query(Leave).filter(Leave.person_type == "student", Leave.status == "approved", Leave.end_date < today,
                               Leave.post_leave_absence_flagged.is_(False))
    for lv in q.all():
        s = first_session_after_leave(db, lv)
        if not s:
            continue
        lv.post_leave_absence_flagged = True
        if s.status == "absent":
            db.add(RiskAlert(alert_type="post_leave_absence", severity="medium",
                             title=f"Post-leave absence: {lv.student.full_name if lv.student else lv.student_id}",
                             message=f"Student did not attend the first class ({s.date}) after leave ending {lv.end_date}. Retention follow-up recommended.",
                             entity_type="Student", entity_id=lv.student_id, visibility="ops", source="system"))
            n += 1
    db.flush()
    return n


# ----------------------------------------------------------------------------- reports
def teacher_day_stats(db: Session, teacher_id: int, day: date) -> dict:
    rows = db.query(ClassSession).filter(ClassSession.teacher_id == teacher_id, ClassSession.date == day).all()
    c = {st: sum(1 for r in rows if r.status == st) for st in SESSION_STATUSES}
    late = [r.teacher_late_minutes or 0 for r in rows if r.status in ("done", "started")]
    c["total"] = len(rows)
    c["avg_late"] = round(sum(late) / len(late), 1) if late else 0.0
    c["on_time_pct"] = round(100 * sum(1 for m in late if m <= 5) / len(late), 1) if late else 100.0
    return c


def missed_response_times(db: Session, since: date) -> list[float]:
    """Minutes between a missed-class alert being raised and its acknowledgement/resolution."""
    rows = db.query(RiskAlert).filter(RiskAlert.alert_type == "missed_class", RiskAlert.created_at >= datetime.combine(since, time.min),
                                      RiskAlert.status.in_(["acknowledged", "resolved"])).all()
    return [max(0.0, (a.updated_at - a.created_at).total_seconds() / 60) for a in rows if a.updated_at and a.created_at]
