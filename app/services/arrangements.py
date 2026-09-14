"""Class arrangements (substitute cover) and reschedule approvals - ERP parity, docs/AUDIT_ACADEMICS.md 3.6.

* apply_arrangement / revert_arrangement move pending sessions of the "from" teacher onto the "to" teacher
  for the arrangement's date range (optionally limited to one session slot / category).
* auto_arrange finds teachers who are absent or on approved leave in a range and creates arrangements
  (is_auto=True) with the first free teacher per slot.
* free_teachers_for lists verified teachers with no class in a slot on a given day.
* approve_reschedule / reject_reschedule drive the Rescheduled Classes approval workflow.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.notify import notify
from app.models.core import User
from app.models.erp import ClassArrangement, RescheduleRequest, SessionSlot
from app.models.people import Employee, HRAttendance, Leave, Teacher
from app.models.scheduling import ClassSession
from app.services import classes as class_svc
from app.services import scheduling as sched


def _range_sessions(db: Session, arr: ClassArrangement, teacher_id: int):
    q = db.query(ClassSession).filter(ClassSession.teacher_id == teacher_id, ClassSession.date >= arr.from_date,
                                      ClassSession.date <= arr.to_date, ClassSession.status.in_(["pending", "available"]))
    if arr.slot_id:
        slot = db.get(SessionSlot, arr.slot_id)
        if slot:
            q = q.filter(or_(ClassSession.slot_id == slot.id, ClassSession.start_time == slot.start_time))
    if arr.session_category:
        minutes = 45 if str(arr.session_category).startswith("45") else 30
        q = q.filter(ClassSession.duration_minutes == minutes)
    return q.order_by(ClassSession.scheduled_start).all()


def _teacher_busy(db: Session, teacher_id: int, day: date, start: time, minutes: int, exclude_id: Optional[int] = None) -> bool:
    st = datetime.combine(day, start)
    en = st + timedelta(minutes=minutes or 30)
    q = db.query(ClassSession).filter(ClassSession.teacher_id == teacher_id, ClassSession.date == day,
                                      ClassSession.status.in_(["pending", "available", "started"]))
    if exclude_id:
        q = q.filter(ClassSession.id != exclude_id)
    for o in q.all():
        o_end = o.scheduled_start + timedelta(minutes=o.duration_minutes or 30)
        if o.scheduled_start < en and st < o_end:
            return True
    return False


def apply_arrangement(db: Session, user: Optional[User], arr: ClassArrangement, request=None) -> int:
    """Reassign the from-teacher's pending sessions in range to the to-teacher. Returns the number moved."""
    if arr.from_teacher_id == arr.to_teacher_id:
        raise ValueError("Choose a different teacher to take the classes.")
    to_teacher = db.get(Teacher, arr.to_teacher_id)
    if not to_teacher or not to_teacher.is_verified:
        raise ValueError("The covering teacher must be verified for live classes.")
    moved = 0
    for s in _range_sessions(db, arr, arr.from_teacher_id):
        if _teacher_busy(db, arr.to_teacher_id, s.date, s.start_time, s.duration_minutes, exclude_id=s.id):
            continue
        s.teacher_id = arr.to_teacher_id
        s.substitute_for_teacher_id = arr.from_teacher_id
        s.arrangement_id = arr.id
        moved += 1
    arr.applied_count = (arr.applied_count or 0) + moved
    arr.status = "active"
    log_action(db, user, "schedule_change", "classes", entity=arr,
               description=(f"Class arrangement #{arr.id}: {arr.from_teacher.full_name if arr.from_teacher else arr.from_teacher_id} -> "
                            f"{to_teacher.full_name} {arr.from_date} to {arr.to_date}; {moved} class(es) reassigned"),
               rationale=arr.reason, after={"applied": moved, "auto": arr.is_auto}, request=request, consequential=True)
    for t in (arr.from_teacher, to_teacher):
        if t and t.user_id:
            notify(db, t.user_id, "Class arrangement",
                   f"{moved} class(es) of {arr.from_teacher.full_name if arr.from_teacher else '-'} from {arr.from_date} to {arr.to_date} "
                   f"are covered by {to_teacher.full_name}. {arr.reason or ''}", event_type="teacher_change", link="/teacher/online-class")
    db.flush()
    return moved


def revert_arrangement(db: Session, user: Optional[User], arr: ClassArrangement, request=None) -> int:
    """Set the arrangement In-Active: future pending sessions go back to the original teacher."""
    now = sched.org_now()
    n = 0
    for s in db.query(ClassSession).filter(ClassSession.arrangement_id == arr.id, ClassSession.status.in_(["pending", "available"]),
                                           ClassSession.scheduled_start > now).all():
        s.teacher_id = arr.from_teacher_id
        s.substitute_for_teacher_id = None
        s.arrangement_id = None
        n += 1
    arr.status = "inactive"
    log_action(db, user, "schedule_change", "classes", entity=arr,
               description=f"Class arrangement #{arr.id} set In-Active; {n} future class(es) returned to the original teacher",
               rationale=arr.reason, request=request, consequential=True)
    for t in (arr.from_teacher, arr.to_teacher):
        if t and t.user_id:
            notify(db, t.user_id, "Class arrangement ended", f"Arrangement #{arr.id} is inactive; {n} class(es) reverted.",
                   event_type="teacher_change", link="/teacher/online-class")
    db.flush()
    return n


def free_teachers_for(db: Session, day: date, slot_id: int) -> list[Teacher]:
    """Verified, active teachers with no pending/started class in that slot on that day."""
    slot = db.get(SessionSlot, slot_id)
    if not slot:
        return []
    out = []
    for t in db.query(Teacher).filter(Teacher.status == "active", Teacher.is_verified.is_(True)).order_by(Teacher.full_name).all():
        if not _teacher_busy(db, t.id, day, slot.start_time, slot.duration_minutes or 30):
            out.append(t)
    return out


def absent_teachers(db: Session, date_from: date, date_to: date) -> list[dict]:
    """Teachers on approved leave or marked absent in HR attendance within the range."""
    out: dict[int, dict] = {}
    leaves = (db.query(Leave).join(Employee, Employee.id == Leave.employee_id)
              .filter(Leave.person_type == "employee", Leave.status == "approved", Employee.is_teacher.is_(True),
                      Leave.start_date <= date_to, Leave.end_date >= date_from).all())
    for lv in leaves:
        t = lv.employee.teacher if lv.employee else None
        if not t:
            continue
        row = out.setdefault(t.id, {"teacher": t, "reasons": [], "from": lv.start_date, "to": lv.end_date, "days": set()})
        row["reasons"].append(f"{lv.leave_type.title()} leave {lv.start_date} to {lv.end_date}")
        d = max(lv.start_date, date_from)
        while d <= min(lv.end_date, date_to):
            row["days"].add(d)
            d += timedelta(days=1)
    att = (db.query(HRAttendance).join(Employee, Employee.id == HRAttendance.employee_id)
           .filter(Employee.is_teacher.is_(True), HRAttendance.status == "absent", HRAttendance.date >= date_from,
                   HRAttendance.date <= date_to).all())
    for a in att:
        t = a.employee.teacher if a.employee else None
        if not t:
            continue
        row = out.setdefault(t.id, {"teacher": t, "reasons": [], "from": a.date, "to": a.date, "days": set()})
        row["reasons"].append(f"Absent in attendance on {a.date}")
        row["days"].add(a.date)
        row["from"] = min(row["from"], a.date)
        row["to"] = max(row["to"], a.date)
    rows = list(out.values())
    for r in rows:
        r["days"] = sorted(r["days"])
        r["pending"] = db.query(ClassSession).filter(ClassSession.teacher_id == r["teacher"].id, ClassSession.date.in_(r["days"] or [date_from]),
                                                     ClassSession.status.in_(["pending", "available"])).count()
    rows.sort(key=lambda r: r["teacher"].full_name)
    return rows


def auto_arrange(db: Session, user: Optional[User], date_from: date, date_to: date, request=None) -> list[ClassArrangement]:
    """For every absent / on-leave teacher, cover each pending class with a free verified teacher (is_auto=True)."""
    created: list[ClassArrangement] = []
    for row in absent_teachers(db, date_from, date_to):
        t = row["teacher"]
        days = row["days"]
        if not days:
            continue
        sessions = (db.query(ClassSession).filter(ClassSession.teacher_id == t.id, ClassSession.date.in_(days),
                                                  ClassSession.status.in_(["pending", "available"]))
                    .order_by(ClassSession.scheduled_start).all())
        # group by slot so one arrangement covers a whole slot across the absence
        by_slot: dict[time, list[ClassSession]] = {}
        for s in sessions:
            by_slot.setdefault(s.start_time, []).append(s)
        for start, items in by_slot.items():
            slot = sched.slot_for_time(db, start, sched.category_for_minutes(items[0].duration_minutes))
            cover = None
            for cand in db.query(Teacher).filter(Teacher.status == "active", Teacher.is_verified.is_(True), Teacher.id != t.id).order_by(Teacher.full_name).all():
                if all(not _teacher_busy(db, cand.id, s.date, s.start_time, s.duration_minutes) for s in items):
                    cover = cand
                    break
            if not cover:
                continue
            arr = ClassArrangement(from_teacher_id=t.id, to_teacher_id=cover.id, from_date=min(days), to_date=max(days),
                                   session_category=slot.category if slot else None, slot_id=slot.id if slot else None,
                                   reason=("Auto arrangement: " + "; ".join(row["reasons"]))[:200], status="active", is_auto=True,
                                   created_by_id=user.id if user else None)
            db.add(arr)
            db.flush()
            apply_arrangement(db, user, arr, request=request)
            created.append(arr)
    db.flush()
    return created


# ----------------------------------------------------------------------------- reschedule approvals
def create_reschedule_request(db: Session, user: Optional[User], session: ClassSession, new_date: date, new_slot: Optional[SessionSlot],
                              new_time: Optional[time], new_teacher_id: Optional[int], reason: str, request=None) -> RescheduleRequest:
    if session.status not in ("pending", "available", "started", "missed", "absent", "cancelled"):
        raise ValueError(f"A class in status '{session.status}' cannot be rescheduled.")
    if not reason:
        raise ValueError("A reason is required to reschedule a class.")
    nt = new_slot.start_time if new_slot else (new_time or session.start_time)
    rr = RescheduleRequest(session_id=session.id, subscription_id=session.subscription_id, old_date=session.date, new_date=new_date,
                           old_start_time=session.start_time, new_start_time=nt, old_teacher_id=session.teacher_id,
                           new_teacher_id=new_teacher_id or session.teacher_id, reason=reason[:200], status="pending",
                           requested_by_id=user.id if user else None)
    db.add(rr)
    db.flush()
    log_action(db, user, "create", "classes", entity=rr,
               description=f"Reschedule requested for class #{session.id}: {session.date} {session.start_time.strftime('%H:%M')} -> {new_date} {nt.strftime('%H:%M')}",
               rationale=reason, request=request)
    return rr


def approve_reschedule(db: Session, user: Optional[User], rr: RescheduleRequest, comments: str = "", request=None) -> ClassSession:
    """Apply a pending reschedule: old session -> 'rescheduled', new session created at the new date/slot/teacher."""
    if rr.status != "pending":
        raise ValueError(f"This request is already {rr.status}.")
    old = rr.session
    if not old:
        raise ValueError("The original class no longer exists.")
    new_start = datetime.combine(rr.new_date, rr.new_start_time)
    teacher_id = rr.new_teacher_id or old.teacher_id
    if _teacher_busy(db, teacher_id, rr.new_date, rr.new_start_time, old.duration_minutes, exclude_id=old.id):
        raise ValueError("The teacher already has a class at the new time.")
    end = new_start + timedelta(minutes=old.duration_minutes or 30)
    slot = sched.slot_for_time(db, rr.new_start_time, sched.category_for_minutes(old.duration_minutes))
    new = ClassSession(schedule_id=old.schedule_id, student_id=old.student_id, teacher_id=teacher_id, course_id=old.course_id,
                       date=rr.new_date, start_time=rr.new_start_time, end_time=end.time(), scheduled_start=new_start,
                       duration_minutes=old.duration_minutes, status="pending", is_trial=old.is_trial, room_name=old.room_name,
                       join_url=old.join_url, lesson_id=old.lesson_id, slot_id=slot.id if slot else None,
                       subscription_id=old.subscription_id,
                       substitute_for_teacher_id=(old.teacher_id if teacher_id != old.teacher_id else None),
                       status_reason=f"Rescheduled from {old.date} {old.start_time.strftime('%H:%M')} (request #{rr.id})")
    db.add(new)
    db.flush()
    old.rescheduled_to_id = new.id
    if old.status != "rescheduled":
        class_svc.set_status(db, old, "rescheduled", user, reason=rr.reason, request=request)
    rr.status = "approved"
    rr.approved_by_id = user.id if user else None
    rr.approved_at = datetime.utcnow()
    rr.comments = comments or rr.comments
    rr.new_session_id = new.id
    log_action(db, user, "approve", "classes", entity=rr, description=f"Reschedule #{rr.id} approved; new class #{new.id} on {rr.new_date}",
               rationale=comments or rr.reason, request=request, consequential=True)
    if new.teacher and new.teacher.user_id:
        notify(db, new.teacher.user_id, "Class rescheduled", f"Class with {new.student.full_name if new.student else 'student'} moved to {rr.new_date} {rr.new_start_time.strftime('%H:%M')}.",
               event_type="class_status", link="/teacher/online-class")
    db.flush()
    return new


def decide_reschedule(db: Session, user: Optional[User], rr: RescheduleRequest, status: str, comments: str = "", request=None) -> RescheduleRequest:
    if status == "approved":
        approve_reschedule(db, user, rr, comments, request=request)
        return rr
    if status not in ("rejected", "cancelled", "pending"):
        raise ValueError("Invalid approval status.")
    before = rr.status
    rr.status = status
    rr.comments = comments or rr.comments
    if status in ("rejected", "cancelled"):
        rr.approved_by_id = user.id if user else None
        rr.approved_at = datetime.utcnow()
    log_action(db, user, "reject" if status == "rejected" else "status_change", "classes", entity=rr,
               description=f"Reschedule #{rr.id} {before} -> {status}", rationale=comments or rr.reason, request=request,
               consequential=status == "rejected")
    if rr.requested_by_id:
        notify(db, rr.requested_by_id, f"Reschedule request {status}", f"Request #{rr.id} for {rr.new_date} was {status}. {comments}",
               event_type="class_status", link="/classes/rescheduled")
    db.flush()
    return rr
