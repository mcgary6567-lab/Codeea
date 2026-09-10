"""People services: teacher matching (Module 44), client/student/teacher creation, balances, timezone helpers.

Other modules may import from here. Cross-module services are imported lazily inside functions.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, time
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import log_action, snapshot
from app.core.notify import notify
from app.core.security import hash_password
from app.core.utils import next_code
from app.models.academic import Course, Division, StudentProgress, Lesson, Chapter, Book
from app.models.core import User, Role, Department, Branch, CommunicationPreference
from app.models.finance import LedgerEntry
from app.models.people import Client, Student, Teacher, Employee, Household, SalaryStructure
from app.models.scheduling import TeacherMatch, Schedule, ClassSession
from app.models.finance import Subscription

ORG_TZ = "Asia/Karachi"

# country, timezone, currency, dial prefix
COUNTRY_INFO = [
    ("United Kingdom", "Europe/London", "GBP", "+44"),
    ("United States", "America/New_York", "USD", "+1"),
    ("Canada", "America/Toronto", "CAD", "+1"),
    ("Australia", "Australia/Sydney", "AUD", "+61"),
    ("Pakistan", "Asia/Karachi", "PKR", "+92"),
    ("United Arab Emirates", "Asia/Dubai", "USD", "+971"),
    ("Saudi Arabia", "Asia/Riyadh", "USD", "+966"),
    ("Qatar", "Asia/Qatar", "USD", "+974"),
    ("Germany", "Europe/Berlin", "GBP", "+49"),
    ("France", "Europe/Paris", "GBP", "+33"),
    ("Netherlands", "Europe/Amsterdam", "GBP", "+31"),
    ("Norway", "Europe/Oslo", "GBP", "+47"),
    ("Sweden", "Europe/Stockholm", "GBP", "+46"),
    ("Ireland", "Europe/Dublin", "GBP", "+353"),
    ("South Africa", "Africa/Johannesburg", "USD", "+27"),
    ("Malaysia", "Asia/Kuala_Lumpur", "USD", "+60"),
    ("Singapore", "Asia/Singapore", "USD", "+65"),
    ("New Zealand", "Pacific/Auckland", "AUD", "+64"),
    ("India", "Asia/Kolkata", "USD", "+91"),
    ("Bangladesh", "Asia/Dhaka", "USD", "+880"),
    ("Other", "UTC", "USD", ""),
]
COUNTRY_MAP = {c[0]: c for c in COUNTRY_INFO}
COUNTRY_NAMES = [c[0] for c in COUNTRY_INFO]
TIMEZONES = sorted({c[1] for c in COUNTRY_INFO} | {"America/Chicago", "America/Denver", "America/Los_Angeles", "Europe/Madrid", "Europe/Rome", "Asia/Tokyo"})
CURRENCIES = ["GBP", "USD", "CAD", "AUD", "PKR", "EUR"]
RELATIONSHIPS = ["father", "mother", "guardian", "self", "sibling", "other"]
SHIFT_WINDOWS = {"morning": (6, 14), "evening": (14, 22), "night": (20, 28)}  # org-time hours; night wraps past midnight
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# ----------------------------------------------------------------------------- time helpers
def safe_zone(tz: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(tz or ORG_TZ)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return ZoneInfo(ORG_TZ)


def to_client_tz(dt: Optional[datetime], tz: Optional[str]) -> Optional[datetime]:
    """Convert a naive org-time (Asia/Karachi) datetime into the client's timezone (aware datetime)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=safe_zone(ORG_TZ))
    return dt.astimezone(safe_zone(tz))


def org_now() -> datetime:
    return datetime.now(safe_zone(ORG_TZ)).replace(tzinfo=None)


def age_from_dob(dob: Optional[date]) -> Optional[int]:
    if not dob:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _local_hour_to_org(hour: int, tz: str) -> int:
    """Map an hour-of-day in the student's timezone to the org timezone hour (today)."""
    local = datetime.combine(date.today(), time(hour, 0)).replace(tzinfo=safe_zone(tz))
    return local.astimezone(safe_zone(ORG_TZ)).hour


def shift_for_timezone(tz: str) -> str:
    """Which org shift best covers the student's evening (16:00-21:00 local)?"""
    best, best_n = "evening", -1
    hours = [_local_hour_to_org(h, tz) for h in range(16, 22)]
    for shift, (start, end) in SHIFT_WINDOWS.items():
        n = sum(1 for h in hours if start <= h < end or start <= h + 24 < end)
        if n > best_n:
            best, best_n = shift, n
    return best


# ----------------------------------------------------------------------------- teacher match (Module 44)
def teacher_load(db: Session, teacher_id: int) -> int:
    return db.query(func.count(Student.id)).filter(Student.teacher_id == teacher_id, Student.status.in_(["active", "trial", "free"])).scalar() or 0


def recommend_teachers(db: Session, course_code: Optional[str], gender: str, age: Optional[int], timezone: str,
                       preferred_shift: Optional[str] = None, exclude_ids=()) -> list[dict]:
    """Rank verified, active teachers for a student. Unverified teachers are excluded (safeguarding gate)."""
    gender = (gender or "male").lower()
    age = age if age is not None else 10
    timezone = timezone or "Europe/London"
    local_eve_hours = [_local_hour_to_org(h, timezone) for h in range(16, 22)]
    out = []
    q = db.query(Teacher).filter(Teacher.is_verified.is_(True), Teacher.status == "active")
    for t in q:
        if t.id in set(exclude_ids or ()):
            continue
        score, reasons = 0.0, []
        # course fit
        if course_code and course_code in (t.courses or []):
            score += 35; reasons.append(f"Teaches {course_code}")
        elif not course_code:
            score += 15
        else:
            reasons.append(f"Does not list {course_code}")
        # gender / safeguarding fit
        if gender == "female":
            if t.gender == "female":
                score += 20; reasons.append("Female teacher for female student")
        elif age is not None and age < 8:
            if t.gender == "female":
                score += 12; reasons.append("Female teacher preferred for young child")
            else:
                score += 6
        else:
            score += 10
        # shift / timezone fit
        start, end = SHIFT_WINDOWS.get(t.shift or "evening", (14, 22))
        overlap = sum(1 for h in local_eve_hours if start <= h < end or start <= h + 24 < end)
        shift_pts = round(20 * overlap / 6, 1)
        score += shift_pts
        if overlap >= 4:
            reasons.append(f"{t.shift.title()} shift covers the student's evening ({timezone})")
        elif overlap == 0:
            reasons.append(f"{t.shift.title()} shift does not overlap the student's evening")
        if preferred_shift and preferred_shift == t.shift:
            score += 8; reasons.append("Matches preferred shift")
        # load
        load = teacher_load(db, t.id)
        cap = max(1, (t.max_classes_per_day or 12))
        load_pts = max(0.0, 10 - 10 * load / cap)
        score += round(load_pts, 1)
        reasons.append(f"Current load {load} active students")
        # grade & quality
        gpts = {"A": 10, "B": 6, "C": 2}.get(t.grade or "B", 5)
        score += gpts
        reasons.append(f"Grade {t.grade}, QA avg {round(t.qa_score_avg or 0)}")
        out.append({"teacher": t, "teacher_id": t.id, "name": t.full_name, "code": t.teacher_code, "gender": t.gender,
                    "shift": t.shift, "grade": t.grade, "load": load, "score": round(min(100.0, score), 1), "reasons": reasons})
    out.sort(key=lambda r: (-r["score"], r["load"], r["name"]))
    return out


def _serialise_ranked(ranked: list[dict]) -> list[dict]:
    return [{"teacher_id": r["teacher_id"], "name": r["name"], "score": r["score"], "reasons": r["reasons"], "load": r["load"], "grade": r["grade"]}
            for r in ranked[:10]]


def assign_teacher(db: Session, student: Student, teacher: Optional[Teacher], user: Optional[User], reason: Optional[str] = None,
                   overridden: bool = False, ranked: Optional[list[dict]] = None, request=None) -> TeacherMatch:
    """Record a TeacherMatch decision and set the student's teacher. Unverified teachers are rejected."""
    if teacher is not None and not teacher.is_verified:
        raise ValueError("Teacher is not verified: cannot be assigned live classes (safeguarding gate).")
    if ranked is None:
        ranked = recommend_teachers(db, student.course.code if student.course else None, student.gender, student.age, student.timezone)
    top = ranked[0] if ranked else None
    chosen_id = teacher.id if teacher else None
    entry = next((r for r in ranked if r["teacher_id"] == chosen_id), None)
    if top and chosen_id and chosen_id != top["teacher_id"]:
        overridden = True
    match = TeacherMatch(student_id=student.id, recommended_teacher_id=top["teacher_id"] if top else None, chosen_teacher_id=chosen_id,
                         ranked_candidates=_serialise_ranked(ranked), match_score=entry["score"] if entry else None,
                         match_reason="; ".join(entry["reasons"]) if entry else (reason or "Manual assignment"),
                         overridden=overridden, override_reason=reason if overridden else None, decided_by_id=user.id if user else None)
    db.add(match)
    before = {"teacher_id": student.teacher_id}
    student.teacher_id = chosen_id
    db.flush()
    log_action(db, user, "override" if overridden else "assign", "students", entity=student,
               description=f"Teacher assigned: {teacher.full_name if teacher else 'none'} (recommended #{1 if top and top['teacher_id']==chosen_id else '-'})",
               rationale=reason, before=before, after={"teacher_id": chosen_id, "match_id": match.id}, request=request, consequential=overridden)
    return match


def propagate_teacher_change(db: Session, student: Student, old_teacher: Optional[Teacher], new_teacher: Optional[Teacher], user: Optional[User],
                             reason: Optional[str] = None) -> dict:
    """Teacher-change propagation: schedules, pending future sessions, subscription, notifications to old/new teacher & parent."""
    changed = {"schedules": 0, "sessions": 0, "subscriptions": 0}
    if new_teacher is None:
        return changed
    for sch in db.query(Schedule).filter(Schedule.student_id == student.id, Schedule.status == "active"):
        if sch.teacher_id != new_teacher.id:
            sch.teacher_id = new_teacher.id
            changed["schedules"] += 1
    for cs in db.query(ClassSession).filter(ClassSession.student_id == student.id, ClassSession.status == "pending",
                                            ClassSession.scheduled_start >= datetime.utcnow() - timedelta(hours=6)):
        if cs.teacher_id != new_teacher.id:
            cs.teacher_id = new_teacher.id
            changed["sessions"] += 1
    for sub in db.query(Subscription).filter(Subscription.student_id == student.id, Subscription.status.in_(["active", "frozen", "pending_approval"])):
        if sub.teacher_id != new_teacher.id:
            sub.teacher_id = new_teacher.id
            changed["subscriptions"] += 1
    body = f"{student.full_name} ({student.student_code}) has been moved from {old_teacher.full_name if old_teacher else 'unassigned'} to {new_teacher.full_name}. {reason or ''}".strip()
    if old_teacher and old_teacher.user_id:
        notify(db, old_teacher.user_id, "Student reassigned", body, event_type="teacher_change", link="/teacher/students")
    if new_teacher.user_id:
        notify(db, new_teacher.user_id, "New student assigned to you", body, event_type="teacher_change", link="/teacher/students")
    if student.client and student.client.user_id:
        notify(db, student.client.user_id, f"New teacher for {student.full_name}",
               f"{new_teacher.full_name} will now teach {student.full_name}. Classes continue at the same times.",
               event_type="teacher_change", link="/portal/schedule",
               channels=("in_app", "whatsapp") if student.client.whatsapp_opt_in and student.client.whatsapp else ("in_app",),
               recipient_address=student.client.whatsapp)
    return changed


# ----------------------------------------------------------------------------- creation helpers
def _role(db: Session, slug: str) -> Optional[Role]:
    return db.query(Role).filter(Role.slug == slug).first()


def _unique_username(db: Session, base: str) -> str:
    base = "".join(ch for ch in (base or "user").lower() if ch.isalnum() or ch in "._-") or "user"
    candidate, n = base, 1
    while db.query(User).filter(User.username == candidate).first():
        n += 1
        candidate = f"{base}{n}"
    return candidate


def temp_password() -> str:
    return "Oqc-" + secrets.token_urlsafe(6) + "9"


def create_portal_user(db: Session, client: Client, actor: Optional[User], request=None) -> tuple[User, str]:
    """Create a portal login (role client) for a client. Returns (user, temp_password)."""
    if client.user_id and client.user:
        raise ValueError("Client already has a portal login.")
    email = (client.email or "").strip().lower()
    if not email:
        email = f"{client.client_code.lower()}@portal.oqc.local"
    if db.query(User).filter(User.email == email).first():
        # The address is already in use (a sibling account, or a re-registration of the same family).
        # Fall back to the institutional portal address so the family still gets a working login
        # rather than a client record silently left without one.
        base = f"{client.client_code.lower()}@portal.oqc.local"
        email, n = base, 1
        while db.query(User).filter(User.email == email).first():
            n += 1
            email = f"{client.client_code.lower()}-{n}@portal.oqc.local"
    pwd = temp_password()
    u = User(email=email, username=_unique_username(db, email.split("@")[0]), full_name=client.full_name, hashed_password=hash_password(pwd),
             role_id=(_role(db, "client").id if _role(db, "client") else None), timezone=client.timezone, phone=client.phone,
             must_change_password=True)
    db.add(u)
    db.flush()
    client.user_id = u.id
    db.flush()
    log_action(db, actor, "create", "clients", entity=client, description=f"Portal login created for {client.client_code} ({email})",
               after={"user_id": u.id}, request=request, severity="warning")
    return u, pwd


def upsert_comm_prefs(db: Session, client: Client, prefs: dict[str, bool]) -> None:
    now = datetime.utcnow()
    for channel, opted in prefs.items():
        row = db.query(CommunicationPreference).filter(CommunicationPreference.client_id == client.id, CommunicationPreference.channel == channel).first()
        if not row:
            row = CommunicationPreference(client_id=client.id, user_id=client.user_id, channel=channel)
            db.add(row)
        if row.opted_in != bool(opted) or row.consent_at is None:
            row.consent_at = now
        row.opted_in = bool(opted)
        row.user_id = client.user_id
    db.flush()


def create_client_with_portal(db: Session, data: dict, user: Optional[User], request=None, with_portal: bool = True) -> tuple[Client, Optional[str]]:
    """Create a client (+ household + comm preferences) and optionally a portal login. Returns (client, temp_password)."""
    country = data.get("country") or "United Kingdom"
    info = COUNTRY_MAP.get(country, COUNTRY_MAP["Other"])
    hh = None
    if data.get("household_id"):
        hh = db.query(Household).get(int(data["household_id"]))
    elif data.get("household_name"):
        hh = Household(name=data["household_name"].strip(), country=country)
        db.add(hh)
        db.flush()
    consent = bool(data.get("consent_given"))
    c = Client(client_code=next_code(db, Client, "client_code", "C-"), full_name=(data.get("full_name") or "").strip(),
               email=(data.get("email") or "").strip().lower() or None, phone=(data.get("phone") or "").strip() or None,
               whatsapp=(data.get("whatsapp") or data.get("phone") or "").strip() or None, country=country, city=data.get("city") or None,
               timezone=data.get("timezone") or info[1], currency=data.get("currency") or info[2], address=data.get("address") or None,
               relationship_to_student=data.get("relationship_to_student") or "father", status=data.get("status") or "trial",
               source=data.get("source") or "Manual", billing_rep_id=int(data["billing_rep_id"]) if data.get("billing_rep_id") else None,
               consent_given=consent, consent_at=datetime.utcnow() if consent else None, whatsapp_opt_in=bool(data.get("whatsapp_opt_in", True)),
               preferences=data.get("preferences") or {}, notes=data.get("notes") or None, household_id=hh.id if hh else None,
               lead_id=data.get("lead_id"), referral_code=None)
    db.add(c)
    db.flush()
    c.referral_code = f"REF{c.id:05d}"
    upsert_comm_prefs(db, c, {"whatsapp": c.whatsapp_opt_in, "email": bool(c.email), "in_app": True})
    log_action(db, user, "create", "clients", entity=c, description=f"Client {c.client_code} created", after=snapshot(c), request=request)
    pwd = None
    if with_portal:
        try:
            _, pwd = create_portal_user(db, c, user, request=request)
        except ValueError as exc:
            # Only "already has a login" reaches here now; record it rather than failing silently.
            pwd = None
            log_action(db, user, "update", "clients", entity=c, severity="warning",
                       description=f"Portal login not created for {c.client_code}: {exc}", request=request)
    return c, pwd


def create_student(db: Session, client: Client, data: dict, user: Optional[User], request=None, with_portal: bool = False) -> Student:
    dob = data.get("date_of_birth")
    age = data.get("age") if data.get("age") not in (None, "") else age_from_dob(dob)
    age = int(age) if age not in (None, "") else None
    course = db.query(Course).get(int(data["course_id"])) if data.get("course_id") else None
    division = db.query(Division).get(int(data["division_id"])) if data.get("division_id") else None
    s = Student(student_code=next_code(db, Student, "student_code", "S-"), client_id=client.id, full_name=(data.get("full_name") or "").strip(),
                gender=data.get("gender") or "male", date_of_birth=dob, age=age, is_minor=(age is None or age < 18),
                course_id=course.id if course else None, division_id=division.id if division else None,
                level=data.get("level") or (division.name if division else None), status=data.get("status") or "trial",
                timezone=data.get("timezone") or client.timezone, preferred_language=data.get("preferred_language") or "English",
                notes=data.get("notes") or None, guardian_consent=bool(data.get("guardian_consent", client.consent_given)),
                sabaq_position=data.get("sabaq_position") or (f"{division.name} — lesson 1" if division else None))
    db.add(s)
    db.flush()
    if with_portal:
        email = f"{s.student_code.lower()}@portal.oqc.local"
        if not db.query(User).filter(User.email == email).first():
            su = User(email=email, username=_unique_username(db, s.student_code.lower()), full_name=s.full_name, hashed_password=hash_password(temp_password()),
                      role_id=(_role(db, "student").id if _role(db, "student") else None), timezone=s.timezone, must_change_password=True)
            db.add(su)
            db.flush()
            s.user_id = su.id
    log_action(db, user, "create", "students", entity=s, description=f"Student {s.student_code} created for {client.client_code}", after=snapshot(s), request=request)
    return s


def create_teacher_full(db: Session, data: dict, user: Optional[User], request=None) -> tuple[Teacher, str]:
    """Create User (role teacher) + Employee(is_teacher) + Teacher in one go. Returns (teacher, temp_password)."""
    email = (data.get("email") or "").strip().lower()
    if not email:
        raise ValueError("Email is required.")
    if db.query(User).filter(User.email == email).first():
        raise ValueError(f"A user with email {email} already exists.")
    name = (data.get("full_name") or "").strip()
    role = _role(db, "teacher")
    dept = db.query(Department).filter(Department.code == "academics").first()
    branch = db.query(Branch).first()
    pwd = temp_password()
    u = User(email=email, username=_unique_username(db, email.split("@")[0]), full_name=name, hashed_password=hash_password(pwd),
             role_id=role.id if role else None, department_id=dept.id if dept else None, branch_id=branch.id if branch else None,
             phone=data.get("phone") or None, timezone=ORG_TZ, must_change_password=True)
    db.add(u)
    db.flush()
    shift = data.get("shift") or "evening"
    emp = Employee(employee_code=next_code(db, Employee, "employee_code", "E-"), user_id=u.id, full_name=name, designation="Quran Teacher",
                   department_id=dept.id if dept else None, branch_id=branch.id if branch else None, gender=data.get("gender") or "male",
                   email=email, phone=data.get("phone") or None, cnic=data.get("cnic") or None, join_date=data.get("join_date") or date.today(),
                   employment_type=data.get("employment_type") or "full_time", shift=shift,
                   shift_start={"morning": "06:00", "evening": "14:00", "night": "20:00"}.get(shift, "14:00"),
                   shift_end={"morning": "14:00", "evening": "22:00", "night": "04:00"}.get(shift, "22:00"),
                   base_salary=float(data.get("base_salary") or 0), currency="PKR", status="probation", is_teacher=True,
                   background_check_status="pending", probation_end=date.today() + timedelta(days=90), device_name=None)
    db.add(emp)
    db.flush()
    db.add(SalaryStructure(employee_id=emp.id, basic=float(data.get("base_salary") or 0), allowances={"internet": 3000}, deductions={},
                           per_class_rate=float(data.get("per_class_rate") or 250), absence_deduction_per_day=round(float(data.get("base_salary") or 0) / 26),
                           late_deduction_per_instance=300, currency="PKR"))
    availability = data.get("availability") or {d: [{"morning": "06:00-14:00", "evening": "14:00-22:00", "night": "20:00-04:00"}.get(shift, "14:00-22:00")] for d in WEEKDAYS[:6]}
    t = Teacher(teacher_code=next_code(db, Teacher, "teacher_code", "T-"), user_id=u.id, employee_id=emp.id, full_name=name, gender=data.get("gender") or "male",
                qualifications=data.get("qualifications") or None, courses=data.get("courses") or [], languages=data.get("languages") or ["English", "Urdu"],
                shift=shift, availability=availability, timezone=ORG_TZ, max_classes_per_day=int(data.get("max_classes_per_day") or 12),
                supervisor_id=int(data["supervisor_id"]) if data.get("supervisor_id") else None, per_class_rate=float(data.get("per_class_rate") or 250),
                grade=data.get("grade") or "B", status="active", bio=data.get("bio") or None, is_verified=False)
    db.add(t)
    db.flush()
    log_action(db, user, "create", "teachers", entity=t, description=f"Teacher {t.teacher_code} created with user {email} and employee {emp.employee_code}",
               after=snapshot(t), request=request)
    return t, pwd


# ----------------------------------------------------------------------------- finance glue
def client_balance(db: Session, client: Client) -> float:
    """Outstanding balance = sum(debit) - sum(credit) from the ledger. Prefers app.services.billing.client_balance if present."""
    try:
        from app.services.billing import client_balance as _cb  # type: ignore
        return float(_cb(db, client))
    except Exception:
        pass
    debit = db.query(func.coalesce(func.sum(LedgerEntry.debit), 0)).filter(LedgerEntry.client_id == client.id).scalar() or 0
    credit = db.query(func.coalesce(func.sum(LedgerEntry.credit), 0)).filter(LedgerEntry.client_id == client.id).scalar() or 0
    return round(float(debit) - float(credit), 2)


# ----------------------------------------------------------------------------- progress
def student_progress_summary(db: Session, student: Student) -> dict:
    """Curriculum progress summary. Uses app.services.academic.student_progress_summary when available."""
    try:
        from app.services.academic import student_progress_summary as _sps  # type: ignore
        res = _sps(db, student)
        if isinstance(res, dict):
            res.setdefault("pct", res.get("progress_pct", 0))
            return res
    except Exception:
        pass
    total = 0
    if student.course_id:
        total = (db.query(func.count(Lesson.id)).join(Chapter, Lesson.chapter_id == Chapter.id).join(Book, Chapter.book_id == Book.id)
                 .filter(Book.course_id == student.course_id).scalar() or 0)
    rows = db.query(StudentProgress).filter(StudentProgress.student_id == student.id).all()
    completed = sum(1 for r in rows if r.status == "completed")
    in_progress = sum(1 for r in rows if r.status == "in_progress")
    revision = sum(1 for r in rows if r.status == "revision")
    scores = [r.score for r in rows if r.score is not None]
    pct = round(100 * completed / total, 1) if total else (round(100 * completed / len(rows), 1) if rows else 0.0)
    current = None
    if student.current_lesson_id:
        current = db.query(Lesson).get(student.current_lesson_id)
    return {"total_lessons": total, "completed": completed, "in_progress": in_progress, "revision": revision, "pct": pct,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None, "current_lesson": current.title if current else student.sabaq_position,
            "rows": rows}


def student_status_change(db: Session, student: Student, new_status: str, user: Optional[User], reason: Optional[str], request=None) -> None:
    before = {"status": student.status}
    student.status = new_status
    if new_status == "cancelled":
        student.cancelled_at = date.today()
        student.cancel_reason = (reason or "")[:200] or None
    elif new_status == "active":
        student.cancelled_at = None
    log_action(db, user, {"cancelled": "cancel", "frozen": "freeze"}.get(new_status, "status_change"), "students", entity=student,
               description=f"Student status {before['status']} -> {new_status}", rationale=reason, before=before, after={"status": new_status},
               request=request, consequential=new_status in ("cancelled", "frozen", "graduated"))
    if student.client and student.client.user_id:
        titles = {"active": "Enrolment activated", "frozen": "Classes paused", "cancelled": "Enrolment cancelled", "graduated": "Congratulations - course completed",
                  "free": "Free classes granted", "trial": "Trial started"}
        notify(db, student.client.user_id, titles.get(new_status, "Student status updated"),
               f"{student.full_name}'s status is now '{new_status}'. {reason or ''}".strip(), event_type="student_status", link="/portal")
    if student.teacher and student.teacher.user_id:
        notify(db, student.teacher.user_id, "Student status updated", f"{student.full_name} is now {new_status}.", event_type="student_status", link="/teacher/students")
