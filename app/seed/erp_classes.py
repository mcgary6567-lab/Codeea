"""ERP-parity class management seed (WP-3, audit section 3.6 / 3.10 / 3.11 / 3.12).

Backfills the ERP fields on subscriptions and class sessions (session slot, days, language, course method,
session category, trial days, follow-ups) and then builds the class-management history the new pages need:
class arrangements (manual + automatic), reschedule requests with mixed approval statuses, class queries,
class activities, teacher-available / started classes today and a handful of short classes so the
duration highlight rules on Class Status Summary and the HOD portal are visible.

Idempotent: every block checks for existing rows before inserting.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models.academic import Book
from app.models.core import User
from app.models.erp import ClassActivity, ClassArrangement, ClassQuery, RescheduleRequest, SessionSlot
from app.models.finance import Subscription
from app.models.people import Student, Teacher
from app.models.scheduling import ClassSession, Schedule
from app.services import arrangements as arr_svc
from app.services import scheduling as sched

rnd = random.Random(20260913)

LANGUAGES = ["English"] * 6 + ["Urdu"] * 5 + ["Arabic", "French", "Pashto", "Punjabi"]
ARRANGEMENT_REASONS = [
    "Teacher on approved sick leave; classes covered by a colleague.",
    "Family emergency; cover arranged for the week.",
    "Teacher attending the annual Tajweed refresher training.",
    "Internet outage at the teacher's location; cover for two days.",
    "Teacher travelling for an examination; temporary cover.",
    "Hajj leave approved; cover arranged for the whole period.",
]
RESCHEDULE_REASONS = [
    "Family requested a different time because of school hours.",
    "Teacher has a medical appointment at the class time.",
    "Public holiday in the client's country.",
    "Student has an exam; class moved to the weekend.",
    "Client travelling; class moved by one day.",
    "Power outage during the usual class slot.",
]
QUERY_DETAILS = [
    "The father has asked to speak to the academic manager about the course plan.",
    "Audio keeps dropping on the family's side; please check their setup.",
    "The student does not have the Noorani Qaida book yet.",
    "Mother asked whether a sibling can join the same session.",
    "Screen sharing is blocked on the student's tablet.",
    "The family wants to change the session time from next month.",
    "Student's microphone is not working; class ran on chat only.",
    "Family asked for a written progress report before the next invoice.",
    "The student joined 15 minutes late three times this week.",
    "Family wants the class moved to a female teacher.",
]
ACTIVITY_REMARKS = [
    "Revision of the previous lesson, then new letters introduced.",
    "Sabaq recited twice; makhraj corrected for heavy letters.",
    "Good fluency today; homework set for the next class.",
    "Struggled with madd rules; extra practice assigned.",
    "Completed the page with two mistakes only.",
    "Memorisation revised from the start of the surah.",
    "Tajweed rules of noon saakin practised with examples.",
    "Student tired; shorter lesson with oral revision only.",
]


# --------------------------------------------------------------------------- helpers
def _admin(db: Session) -> User | None:
    return (db.query(User).filter(User.email == "admin@oqc.local").first()
            or db.query(User).filter(User.is_superuser.is_(True)).first())


def _ensure_slots(db: Session) -> int:
    """The 48 half-hour session slots come from the erp_config seed; create them here if that seed never ran."""
    created = sched.ensure_default_slots(db)
    if created:
        db.flush()
    return created


def _slot_map(db: Session) -> dict[tuple[str, time], SessionSlot]:
    return {(s.category, s.start_time): s for s in db.query(SessionSlot).all()}


# --------------------------------------------------------------------------- 1. subscriptions
def _backfill_subscriptions(db: Session) -> dict:
    """Session slot, days, language, method, category, trial days, trial status and follow-ups."""
    stats = {"subscriptions": 0, "follow_ups": 0, "trials": 0}
    subs = db.query(Subscription).order_by(Subscription.id).all()
    schedules_by_student: dict[int, Schedule] = {}
    for sch in db.query(Schedule).order_by(Schedule.status != "active", Schedule.id).all():
        schedules_by_student.setdefault(sch.student_id, sch)
    slots = _slot_map(db)
    follow_up_slots = {i for i in range(0, len(subs), max(1, len(subs) // 10))}
    for i, sub in enumerate(subs):
        sch = schedules_by_student.get(sub.student_id)
        changed = False
        minutes = (sch.duration_minutes if sch else None) or sub.session_minutes or 30
        category = sched.category_for_minutes(minutes)
        if sub.session_category != category:
            sub.session_category = category
            changed = True
        if not sub.slot_id and sch:
            start = time(sch.start_time.hour, sch.start_time.minute)
            slot = slots.get((category, start)) or sched.slot_for_time(db, start, category)
            if slot:
                sub.slot_id = slot.id
                changed = True
        if not sub.days_of_week:
            sub.days_of_week = sorted(int(d) for d in ((sch.days_of_week if sch else None) or [0, 2, 4]))
            changed = True
        if not sub.language or sub.language == "English":
            lang = LANGUAGES[i % len(LANGUAGES)]
            if sub.language != lang:
                sub.language = lang
                changed = True
        method = "group" if i % 10 == 7 else "one_on_one"
        if sub.course_method != method:
            sub.course_method = method
            changed = True
        if not sub.trial_days:
            sub.trial_days = rnd.choice([3, 3, 3, 5, 7])
            changed = True
        if sch is not None and sch.is_trial and sub.status in ("active", "pending_approval"):
            sub.status = "trial"
            sub.trial_days = sub.trial_days or 3
            stats["trials"] += 1
            changed = True
        if i in follow_up_slots and not sub.follow_up_date:
            sub.follow_up_date = date.today() + timedelta(days=rnd.randint(1, 21))
            stats["follow_ups"] += 1
            changed = True
        if sch is not None and not sub.schedule_id:
            sub.schedule_id = sch.id
            changed = True
        if sch is not None and sch.subscription_id is None:
            sch.subscription_id = sub.id
        if changed:
            stats["subscriptions"] += 1
    db.flush()
    return stats


def _backfill_sessions(db: Session) -> int:
    """ClassSession.slot_id and subscription_id."""
    sub_by_schedule = {s.schedule_id: s.id for s in db.query(Subscription).filter(Subscription.schedule_id.isnot(None))}
    sub_by_student: dict[int, int] = {}
    for s in db.query(Subscription).order_by(Subscription.id.desc()):
        sub_by_student.setdefault(s.student_id, s.id)
    slots = _slot_map(db)
    n = 0
    rows = db.query(ClassSession).filter((ClassSession.slot_id.is_(None)) | (ClassSession.subscription_id.is_(None))).all()
    for cs in rows:
        changed = False
        if not cs.slot_id:
            category = sched.category_for_minutes(cs.duration_minutes)
            start = time(cs.start_time.hour, cs.start_time.minute)
            slot = slots.get((category, start)) or sched.slot_for_time(db, start, category)
            if slot:
                cs.slot_id = slot.id
                changed = True
        if not cs.subscription_id:
            sid = sub_by_schedule.get(cs.schedule_id) or sub_by_student.get(cs.student_id)
            if sid:
                cs.subscription_id = sid
                changed = True
        n += 1 if changed else 0
    db.flush()
    return n


# --------------------------------------------------------------------------- 2. arrangements
def _arrangements(db: Session, admin: User | None) -> int:
    if db.query(ClassArrangement).count():
        return 0
    today = date.today()
    teachers = db.query(Teacher).filter(Teacher.status == "active", Teacher.is_verified.is_(True)).order_by(Teacher.id).all()
    if len(teachers) < 4:
        return 0
    created = 0
    for i in range(6):
        frm = teachers[i % len(teachers)]
        to = teachers[(i + 3) % len(teachers)]
        if frm.id == to.id:
            continue
        start = today - timedelta(days=rnd.randint(8, 50))
        end = start + timedelta(days=rnd.randint(1, 4))
        arr = ClassArrangement(from_teacher_id=frm.id, to_teacher_id=to.id, from_date=start, to_date=end,
                               session_category="30 Minutes", slot_id=None,
                               reason=ARRANGEMENT_REASONS[i % len(ARRANGEMENT_REASONS)],
                               status="active" if i < 5 else "inactive", is_auto=(i in (2, 5)),
                               created_by_id=admin.id if admin else None)
        db.add(arr)
        db.flush()
        moved = 0
        for cs in (db.query(ClassSession)
                   .filter(ClassSession.teacher_id == frm.id, ClassSession.date >= start, ClassSession.date <= end)
                   .order_by(ClassSession.scheduled_start).all()):
            cs.teacher_id = to.id
            cs.substitute_for_teacher_id = frm.id
            cs.arrangement_id = arr.id
            if cs.status == "done":
                cs.done_by_teacher_id = to.id
            moved += 1
        arr.applied_count = moved
        created += 1
    db.flush()
    return created


# --------------------------------------------------------------------------- 3. reschedule requests
def _reschedules(db: Session, admin: User | None) -> dict:
    out = {"created": 0, "approved": 0}
    if db.query(RescheduleRequest).count():
        return out
    today = date.today()
    candidates = (db.query(ClassSession)
                  .filter(ClassSession.status.in_(["pending", "available"]), ClassSession.date >= today)
                  .order_by(ClassSession.scheduled_start).limit(200).all())
    past = (db.query(ClassSession).filter(ClassSession.date < today, ClassSession.status.in_(["done", "missed", "absent"]))
            .order_by(ClassSession.date.desc()).limit(200).all())
    pool = candidates[:8] + past[:6]
    statuses = ["approved", "approved", "approved", "approved", "pending", "pending", "pending",
                "rejected", "rejected", "cancelled", "approved", "pending"]
    slots = db.query(SessionSlot).filter(SessionSlot.category == "30 Minutes").order_by(SessionSlot.start_time).all()
    used: set[int] = set()
    for i, status in enumerate(statuses):
        cs = next((c for c in pool if c.id not in used), None)
        if cs is None:
            break
        used.add(cs.id)
        new_date = cs.date + timedelta(days=rnd.randint(1, 5))
        slot = rnd.choice(slots) if slots else None
        new_time = slot.start_time if slot else cs.start_time
        rr = RescheduleRequest(session_id=cs.id, subscription_id=cs.subscription_id, old_date=cs.date, new_date=new_date,
                               old_start_time=cs.start_time, new_start_time=new_time, old_teacher_id=cs.teacher_id,
                               new_teacher_id=cs.teacher_id, reason=RESCHEDULE_REASONS[i % len(RESCHEDULE_REASONS)],
                               status="pending", requested_by_id=admin.id if admin else None)
        db.add(rr)
        db.flush()
        out["created"] += 1
        if status == "approved":
            try:
                arr_svc.approve_reschedule(db, admin, rr, "Approved by the academic manager.")
                out["approved"] += 1
            except ValueError:
                rr.status = "pending"
        elif status in ("rejected", "cancelled"):
            rr.status = status
            rr.comments = ("The requested slot is not free for this teacher." if status == "rejected"
                           else "The family withdrew the request.")
            rr.approved_by_id = admin.id if admin else None
            rr.approved_at = datetime.utcnow() - timedelta(days=rnd.randint(1, 10))
    db.flush()
    return out


# --------------------------------------------------------------------------- 4. class queries
def _queries(db: Session, admin: User | None) -> int:
    if db.query(ClassQuery).count():
        return 0
    today = date.today()
    rows = (db.query(ClassSession).filter(ClassSession.date >= today - timedelta(days=25))
            .order_by(ClassSession.date.desc()).limit(300).all())
    if not rows:
        return 0
    types = ["Family want to talk with manager", "Facing Tech Issue", "Book / material issue", "Other"]
    created = 0
    for i, detail in enumerate(QUERY_DETAILS):
        cs = rows[(i * 7) % len(rows)]
        closed = i % 3 == 0
        cq = ClassQuery(session_id=cs.id, teacher_id=cs.teacher_id, student_id=cs.student_id,
                        query_type=types[i % len(types)], detail=detail,
                        status="closed" if closed else "pending",
                        response=("Handled by the shift manager; the family has been called." if closed else None),
                        closed_by_id=(admin.id if (closed and admin) else None),
                        closed_at=(datetime.utcnow() - timedelta(days=rnd.randint(1, 6))) if closed else None)
        db.add(cq)
        created += 1
    db.flush()
    return created


# --------------------------------------------------------------------------- 5. activities + today's statuses
def _activities(db: Session, admin: User | None, target: int = 150) -> int:
    if db.query(ClassActivity).count():
        return 0
    today = date.today()
    rows = (db.query(ClassSession).filter(ClassSession.status == "done", ClassSession.date >= today - timedelta(days=45))
            .order_by(ClassSession.date.desc()).limit(target + 40).all())
    books_by_course: dict[int, list[Book]] = {}
    for b in db.query(Book).filter(Book.status == "active").all():
        books_by_course.setdefault(b.course_id, []).append(b)
    created = 0
    for i, cs in enumerate(rows):
        if created >= target:
            break
        if i % 8 == 5:  # leave some done classes without activity so "No Activity" shows
            continue
        books = books_by_course.get(cs.course_id or -1) or []
        book = books[i % len(books)] if books else None
        page = str(rnd.randint(1, 120))
        when = datetime.combine(cs.date, cs.end_time)
        act = ClassActivity(session_id=cs.id, student_id=cs.student_id, teacher_id=cs.teacher_id,
                            activity_type="manual" if i % 4 else "auto", book_id=book.id if book else None,
                            page_no=page, remarks=ACTIVITY_REMARKS[i % len(ACTIVITY_REMARKS)],
                            created_by_id=admin.id if admin else None)
        db.add(act)
        cs.activity_updated_at = when
        created += 1
    db.flush()
    return created


def _classes_today(db: Session, want: int = 16) -> int:
    """Make sure today has classes to monitor: the seeded day patterns can skip today's weekday.

    Pulls the earliest future pending classes onto today (keeping their teacher, student and session time)
    without creating a clash. Idempotent through the ``want`` threshold.
    """
    today = date.today()
    have = db.query(ClassSession).filter(ClassSession.date == today).count()
    if have >= want:
        return 0
    taken_teacher = {(c.teacher_id, c.start_time) for c in db.query(ClassSession).filter(ClassSession.date == today)}
    taken_student = {(c.student_id, c.start_time) for c in db.query(ClassSession).filter(ClassSession.date == today)}
    moved = 0
    rows = (db.query(ClassSession).filter(ClassSession.date > today, ClassSession.status == "pending")
            .order_by(ClassSession.scheduled_start).limit(400).all())
    for cs in rows:
        if have + moved >= want:
            break
        if (cs.teacher_id, cs.start_time) in taken_teacher or (cs.student_id, cs.start_time) in taken_student:
            continue
        cs.date = today
        cs.scheduled_start = datetime.combine(today, cs.start_time)
        taken_teacher.add((cs.teacher_id, cs.start_time))
        taken_student.add((cs.student_id, cs.start_time))
        moved += 1
    db.flush()
    return moved


def _today_statuses(db: Session) -> dict:
    """A few classes today marked available / started / done, and short done classes for the highlight rules."""
    out = {"available": 0, "started": 0, "done": 0, "short": 0}
    today = date.today()
    todays = (db.query(ClassSession).filter(ClassSession.date == today, ClassSession.status == "pending")
              .order_by(ClassSession.scheduled_start).all())
    now = sched.org_now()
    for cs in todays[10:14]:  # a few already finished earlier today
        cs.status = "done"
        cs.teacher_joined_at = datetime.utcnow() - timedelta(hours=2)
        cs.teacher_left_at = cs.teacher_joined_at + timedelta(minutes=rnd.choice([19, 24, 28, 30]))
        cs.actual_duration_minutes = int((cs.teacher_left_at - cs.teacher_joined_at).total_seconds() // 60)
        cs.done_by_teacher_id = cs.teacher_id
        cs.status_changed_at = datetime.utcnow()
        out["done"] += 1
    for i, cs in enumerate(todays[:10]):
        if i % 2 == 0:
            cs.status = "available"
            cs.teacher_available_at = (now + timedelta(minutes=rnd.choice([-4, 2, 6, 11]))) - timedelta(hours=5)
            cs.status_changed_at = datetime.utcnow()
            out["available"] += 1
        else:
            cs.status = "started"
            cs.teacher_joined_at = datetime.utcnow() - timedelta(minutes=rnd.randint(2, 20))
            cs.status_changed_at = datetime.utcnow()
            out["started"] += 1
    done = (db.query(ClassSession).filter(ClassSession.status == "done", ClassSession.date >= today - timedelta(days=20))
            .order_by(ClassSession.date.desc()).limit(60).all())
    for i, cs in enumerate(done):
        if i % 5:
            continue
        cs.actual_duration_minutes = [12, 18, 23, 31][(i // 5) % 4]
        if not cs.done_by_teacher_id:
            cs.done_by_teacher_id = cs.teacher_id
        out["short"] += 1
        if out["short"] >= 12:
            break
    for cs in db.query(ClassSession).filter(ClassSession.status == "done",
                                            ClassSession.done_by_teacher_id.is_(None)).limit(400).all():
        cs.done_by_teacher_id = cs.teacher_id
    db.flush()
    return out


# --------------------------------------------------------------------------- entry point
def run(db: Session) -> None:
    admin = _admin(db)
    slots = _ensure_slots(db)
    subs = _backfill_subscriptions(db)
    db.commit()
    sessions = _backfill_sessions(db)
    db.commit()
    arrangements = _arrangements(db, admin)
    db.commit()
    resched = _reschedules(db, admin)
    db.commit()
    queries = _queries(db, admin)
    activities = _activities(db, admin)
    moved = _classes_today(db)
    today = _today_statuses(db)
    db.commit()
    print(f"    session slots created: {slots}; subscriptions backfilled: {subs['subscriptions']} "
          f"(trials {subs['trials']}, follow-ups {subs['follow_ups']}); sessions backfilled: {sessions}; "
          f"arrangements: {arrangements}; reschedules: {resched['created']} (approved {resched['approved']}); "
          f"queries: {queries}; activities: {activities}; classes moved onto today: {moved}; "
          f"today available/started/done: {today['available']}/{today['started']}/{today['done']}; "
          f"short classes: {today['short']}")
