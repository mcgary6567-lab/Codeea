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


# ============================================================================= ERP parity (docs/AUDIT_ACADEMICS.md 3.5 / 3.6)
# Session slots, subscription <-> schedule synchronisation and teacher availability search (WP-3).
SLOT_CATEGORIES = ["30 Minutes", "45 Minutes"]
LANGUAGES = ["Arabic", "Chinese", "English", "French", "Japanese", "Pashto", "Punjabi", "Sindhi", "Urdu"]
COURSE_METHODS = [("one_on_one", "One on One"), ("group", "Group Class")]
SESSION_TYPES = ["Job Time Session", "Free Session"]
SUBSCRIPTION_STATUS_OPTIONS = [("cancelled", "Cancelled"), ("completed", "Completed"), ("freeze", "Freeze"),
                               ("regular", "Regular"), ("trial", "Trial")]
# ERP vocabulary -> every internal value that means the same thing (existing rows use active/frozen/expired).
SUBSCRIPTION_STATUS_ALIASES = {
    "regular": ["regular", "active"], "active": ["regular", "active"],
    "freeze": ["freeze", "frozen"], "frozen": ["freeze", "frozen"],
    "completed": ["completed", "expired"], "expired": ["completed", "expired"],
    "trial": ["trial"], "cancelled": ["cancelled"], "pending_approval": ["pending_approval"],
}
LIVE_SUBSCRIPTION_STATUSES = ["regular", "active", "trial", "freeze", "frozen", "pending_approval"]
RUNNING_SUBSCRIPTION_STATUSES = ["regular", "active", "trial"]


def status_values(status: str) -> list[str]:
    """All internal status values that the ERP label ``status`` covers."""
    return SUBSCRIPTION_STATUS_ALIASES.get((status or "").lower(), [status]) if status else []


def category_for_minutes(minutes: Optional[int]) -> str:
    return "45 Minutes" if (minutes or 30) >= 45 else "30 Minutes"


def minutes_for_category(category: Optional[str]) -> int:
    return 45 if str(category or "").startswith("45") else 30


def slot_label(start: time, duration: int) -> str:
    """ERP session label: "07:00 AM - 07:30 AM"."""
    total = start.hour * 60 + start.minute + (duration or 30)
    end = time((total // 60) % 24, total % 60)
    return f"{start.strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}"


def slot_display(slot) -> str:
    """Filter / select label: "07:00 AM - 07:30 AM (PST) / 02:00 AM UTC"."""
    if not slot:
        return "-"
    return f"{slot.label} (PST) / {slot.utc_label} UTC"


def ensure_slots(db: Session, category: str = "30 Minutes") -> int:
    """Create the 48 half-hour session slots of a category when none exist (idempotent). Returns rows created."""
    from app.models.erp import SessionSlot
    if db.query(SessionSlot).filter(SessionSlot.category == category).count():
        return 0
    minutes = minutes_for_category(category)
    n = 0
    for i, st in enumerate(SLOTS):
        db.add(SessionSlot(category=category, label=slot_label(st, minutes), start_time=st, duration_minutes=minutes,
                           status="active", sort_no=i + 1))
        n += 1
    db.flush()
    return n


def ensure_default_slots(db: Session) -> int:
    return sum(ensure_slots(db, c) for c in SLOT_CATEGORIES)


def slots_for(db: Session, category: Optional[str] = None, active_only: bool = True) -> list:
    from app.models.erp import SessionSlot
    q = db.query(SessionSlot)
    if category:
        q = q.filter(SessionSlot.category == category)
    if active_only:
        q = q.filter(SessionSlot.status == "active")
    return q.order_by(SessionSlot.category, SessionSlot.start_time, SessionSlot.sort_no).all()


def slot_options(db: Session, category: Optional[str] = None) -> list[tuple[int, str]]:
    return [(s.id, (slot_display(s) if category else f"{s.category}: {slot_display(s)}")) for s in slots_for(db, category)]


def slot_for_time(db: Session, t: Optional[time], category: Optional[str] = "30 Minutes"):
    """The SessionSlot starting at ``t`` for the category (falls back to any category, then the closest earlier slot)."""
    from app.models.erp import SessionSlot
    if t is None:
        return None
    if not isinstance(t, time):
        t = _parse_time(t)
    t = time(t.hour, t.minute)
    q = db.query(SessionSlot).filter(SessionSlot.start_time == t)
    slot = q.filter(SessionSlot.category == category).first() if category else None
    slot = slot or q.order_by(SessionSlot.category).first()
    if slot:
        return slot
    q = db.query(SessionSlot).filter(SessionSlot.start_time <= t)
    if category:
        q = q.filter(SessionSlot.category == category)
    return q.order_by(SessionSlot.start_time.desc()).first()


def backfill_session_slots(db: Session, schedule: Schedule, subscription_id: Optional[int] = None) -> int:
    """Set ClassSession.slot_id / subscription_id on a schedule's sessions where missing."""
    n = 0
    for s in db.query(ClassSession).filter(ClassSession.schedule_id == schedule.id).all():
        changed = False
        if not s.slot_id:
            slot = slot_for_time(db, s.start_time, category_for_minutes(s.duration_minutes))
            if slot:
                s.slot_id = slot.id
                changed = True
        sid = subscription_id or schedule.subscription_id
        if sid and not s.subscription_id:
            s.subscription_id = sid
            changed = True
        n += 1 if changed else 0
    db.flush()
    return n


# ----------------------------------------------------------------------------- teacher availability
def teacher_matches(teacher: Teacher, course=None, language: str = "") -> bool:
    ok = True
    if course is not None and teacher.courses:
        ok = ok and (course.code in (teacher.courses or []))
    if language and teacher.languages:
        ok = ok and (language in (teacher.languages or []))
    return ok


def teacher_slot_busy(db: Session, teacher_id: int, days: list[int], slot, horizon_days: int = 14,
                      exclude_schedule_id: Optional[int] = None) -> bool:
    """True when the teacher has an active schedule or a pending class in the slot on any of the days."""
    if class_svc.has_conflict(db, teacher_id, days, slot.start_time, slot.duration_minutes or 30, exclude_schedule_id=exclude_schedule_id):
        return True
    today = date.today()
    q = db.query(ClassSession).filter(ClassSession.teacher_id == teacher_id, ClassSession.date >= today,
                                      ClassSession.date <= today + timedelta(days=horizon_days),
                                      ClassSession.status.in_(["pending", "available", "started"]))
    if exclude_schedule_id:
        q = q.filter(or_(ClassSession.schedule_id != exclude_schedule_id, ClassSession.schedule_id.is_(None)))
    st_min = slot.start_time.hour * 60 + slot.start_time.minute
    en_min = st_min + (slot.duration_minutes or 30)
    for s in q.all():
        if s.date.weekday() not in days:
            continue
        o_st = s.start_time.hour * 60 + s.start_time.minute
        o_en = o_st + (s.duration_minutes or 30)
        if o_st < en_min and st_min < o_en:
            return True
    return False


def available_teachers(db: Session, slot, days: list[int], course=None, language: str = "", gender: str = "",
                       exclude_schedule_id: Optional[int] = None) -> tuple[list[Teacher], bool]:
    """Verified active teachers free in the slot on the days. Returns (teachers, strict) where strict=False means
    the course/language match produced nobody and every free teacher is listed instead."""
    if slot is None or not days:
        return [], True
    pool = db.query(Teacher).filter(Teacher.status == "active", Teacher.is_verified.is_(True)).order_by(Teacher.full_name).all()
    free = [t for t in pool if not teacher_slot_busy(db, t.id, days, slot, exclude_schedule_id=exclude_schedule_id)]
    strict = [t for t in free if teacher_matches(t, course, language)]
    if strict:
        return strict, True
    return free, False


def teacher_week_sessions(db: Session, teacher_id: int) -> dict[int, list[Schedule]]:
    """Booked slots per weekday for the Teacher's Sessions panel: {weekday: [schedules...]} sorted by time."""
    out: dict[int, list[Schedule]] = {d: [] for d in range(7)}
    for sch in db.query(Schedule).filter(Schedule.teacher_id == teacher_id, Schedule.status == "active").all():
        for d in sch.days_of_week or []:
            out[int(d)].append(sch)
    for d in out:
        out[d].sort(key=lambda s: (s.start_time, s.id))
    return out


# ----------------------------------------------------------------------------- subscription <-> schedule
def subscription_schedule(db: Session, sub) -> Optional[Schedule]:
    sch = db.get(Schedule, sub.schedule_id) if sub.schedule_id else None
    if not sch:
        sch = (db.query(Schedule).filter(Schedule.subscription_id == sub.id).order_by(Schedule.status == "active", Schedule.id.desc()).first())
    if not sch:
        sch = (db.query(Schedule).filter(Schedule.student_id == sub.student_id, Schedule.status == "active")
               .order_by(Schedule.id.desc()).first())
    return sch


def sync_subscription_schedule(db: Session, user: Optional[User], sub, *, reason: str = "", request=None,
                               horizon_days: int = 14) -> Optional[Schedule]:
    """Create or refresh the recurring Schedule of a subscription from its slot / days / teacher / course and
    (re)generate the pending sessions. Sessions carry slot_id and subscription_id."""
    slot = sub.slot
    if not slot or not sub.teacher_id or not sub.days_of_week:
        return subscription_schedule(db, sub)
    days = sorted({int(d) for d in sub.days_of_week})
    is_trial = sub.status == "trial"
    sch = subscription_schedule(db, sub)
    if sch is None:
        sch = create_schedule(db, user, student_id=sub.student_id, teacher_id=sub.teacher_id, days=days, start_time=slot.start_time,
                              duration=slot.duration_minutes or 30, course_id=sub.course_id, subscription_id=sub.id,
                              start_date=date.today(), is_trial=is_trial, notes=sub.remarks, request=request, horizon_days=horizon_days)
    else:
        sch.subscription_id = sub.id
        sch.is_trial = is_trial
        changed = (sch.teacher_id != sub.teacher_id or sorted(int(d) for d in (sch.days_of_week or [])) != days
                   or sch.start_time != slot.start_time or (sch.duration_minutes or 30) != (slot.duration_minutes or 30)
                   or (sub.course_id and sch.course_id != sub.course_id))
        if changed:
            update_schedule(db, user, sch, teacher_id=sub.teacher_id, days=days, start_time=slot.start_time,
                            duration=slot.duration_minutes or 30, course_id=sub.course_id, subscription_id=sub.id,
                            shift_id=sch.shift_id, start_date=sch.start_date, end_date=None, notes=sch.notes,
                            reason=reason or f"Subscription {sub.subscription_code} updated", request=request)
        if sch.status != "active" and sub.status in RUNNING_SUBSCRIPTION_STATUSES:
            change_schedule_status(db, user, sch, "active", reason or f"Subscription {sub.subscription_code} active", request=request)
    sub.schedule_id = sch.id
    backfill_session_slots(db, sch, sub.id)
    return sch


def end_subscription_schedule(db: Session, user: Optional[User], sub, status: str, reason: str, request=None) -> int:
    """Pause (freeze) or end (cancel / complete) the subscription's schedule; future pending classes are cancelled."""
    sch = subscription_schedule(db, sub)
    if not sch or sch.status == status:
        return 0
    return change_schedule_status(db, user, sch, status, reason, request=request)


def subscription_price(db: Session, package, course, currency: str) -> float:
    """List price: the package price (converted into the client's currency) or the course fee (base currency)."""
    from app.services import billing
    if package and float(package.price or 0) > 0:
        return billing.convert(db, float(package.price), package.currency or billing.base_currency(db), currency)
    if course and float(course.fee or 0) > 0:
        return billing.convert(db, float(course.fee), billing.base_currency(db), currency)
    return 0.0


def create_erp_subscription(db: Session, user: Optional[User], *, student, course, package, teacher: Teacher, slot, days: list[int],
                            language: str = "English", course_method: str = "one_on_one", session_category: str = "30 Minutes",
                            session_type: str = "Job Time Session", status: str = "trial", trial_days: int = 3,
                            remarks: Optional[str] = None, books: Optional[list[int]] = None, grade: Optional[str] = None,
                            follow_up_date: Optional[date] = None, request=None):
    """ERP "Create Subscription": subscription + recurring schedule + generated sessions + audit + client notice."""
    from app.core.utils import next_code
    from app.models.finance import Subscription
    from app.services import billing
    if not student or not student.client:
        raise ValueError("Choose a student that belongs to a client.")
    if not teacher or not teacher.is_verified:
        raise ValueError("Choose a verified teacher.")
    if slot is None:
        raise ValueError("Choose a session time.")
    days = sorted({int(d) for d in days if 0 <= int(d) < 7})
    if not days:
        raise ValueError("Select at least one day of the week.")
    if status not in ("trial", "regular"):
        status = "trial"
    if teacher_slot_busy(db, teacher.id, days, slot):
        raise ValueError(f"{teacher.full_name} already has a class in {slot.label} on one of the selected days.")
    client = student.client
    currency = (client.currency or "GBP").upper()
    price = round(subscription_price(db, package, course, currency), 2)
    trial_days = int(trial_days or 3)
    sub = Subscription(
        subscription_code=next_code(db, Subscription, "subscription_code", "SUB-"), client_id=client.id, student_id=student.id,
        package_id=package.id if package else None, course_id=course.id if course else student.course_id, teacher_id=teacher.id,
        sessions_per_week=len(days), session_minutes=slot.duration_minutes or 30, list_price=price, discount_pct=0, discount_amount=0,
        price=price, currency=currency, price_in_base=billing.convert_to_base(db, price, currency),
        teacher_cost_base=billing.teacher_monthly_cost(db, teacher, len(days)), billing_cycle=(package.billing_cycle if package else "monthly"),
        start_date=date.today(), next_billing_date=(date.today() + timedelta(days=trial_days)) if status == "trial" else date.today(),
        status=status, auto_renew=status == "regular", created_by_id=user.id if user else None, notes=remarks,
        slot_id=slot.id, days_of_week=days, language=language or "English", course_method=course_method or "one_on_one",
        session_category=session_category or category_for_minutes(slot.duration_minutes), session_type=session_type or "Job Time Session",
        trial_days=trial_days, remarks=remarks, supervisor_id=teacher.supervisor_id, books=[int(b) for b in (books or [])],
        follow_up_date=follow_up_date or ((date.today() + timedelta(days=trial_days)) if status == "trial" else None))
    db.add(sub)
    db.flush()
    # keep the student record aligned with its subscription
    student.teacher_id = teacher.id
    student.course_id = sub.course_id
    student.preferred_language = sub.language
    if grade:
        student.grade = grade
    if status == "trial":
        student.status = "trial"
        student.trial_days = trial_days
    elif student.status in ("trial", "free", "frozen", "cancelled"):
        student.status = "active"
    sch = sync_subscription_schedule(db, user, sub, reason=f"Subscription {sub.subscription_code} created", request=request)
    n_sessions = db.query(ClassSession).filter(ClassSession.schedule_id == sch.id).count() if sch else 0
    log_action(db, user, "create", "subscriptions", entity=sub,
               description=(f"Subscription {sub.subscription_code} created for {student.full_name}: {course.name if course else '-'} with "
                            f"{teacher.full_name} at {slot.label} on {day_label(days)} ({status}); {n_sessions} class(es) generated"),
               rationale=remarks, after={"status": status, "slot": slot.label, "days": days, "price": float(price), "currency": currency},
               request=request, consequential=True)
    if client.user_id:
        notify(db, client.user_id, "New subscription",
               f"{student.full_name}'s {course.name if course else 'course'} classes with {teacher.full_name} start at {slot.label} PKT "
               f"on {day_label(days)} ({'trial' if status == 'trial' else 'regular'}).", event_type="subscription", link="/portal/schedule")
    if teacher.user_id:
        notify(db, teacher.user_id, "New student assigned", f"{student.full_name} - {slot.label} on {day_label(days)} ({status}).",
               event_type="subscription", link="/teacher/online-class")
    db.flush()
    return sub
