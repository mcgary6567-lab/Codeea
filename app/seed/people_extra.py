"""Extra People & Portals seed data (idempotent).

Adds: TeacherMatch decisions for the seeded students (some overridden), student leaves,
30 days of HR attendance for teachers, a few Ustaadh Lab training assignments and a
handful of website registration leads (with duplicates).

Schedules, class sessions, invoices and CRM pipelines belong to other modules.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.academic import Course
from app.models.core import User
from app.models.crm import Lead, LeadSource, Referral
from app.models.people import Client, Student, Teacher, Employee, HRAttendance, Leave, TrainingAssignment
from app.models.scheduling import TeacherMatch, Trial

rnd = random.Random(4409)

OVERRIDE_REASONS = [
    "Parent specifically requested a female teacher for their daughter.",
    "Family asked to keep both siblings with the same ustaadh.",
    "Top recommendation was fully booked in the requested evening slot.",
    "Student needs Bengali-speaking support, which only this teacher offers.",
    "Previous trial was delivered by this teacher and the child bonded with them.",
    "Timezone clash with the recommended teacher after the family moved.",
]

MATCH_REASONS = [
    "Teaches the course; evening shift covers the student's evening; low current load; Grade A.",
    "Course fit, female teacher for a female student, strong timezone overlap.",
    "Course fit and grade A with the lightest load in the shift.",
    "Best available overlap for the family's timezone with a verified teacher.",
]

TRAINING = [
    ("Tajweed refresher: rules of Madd", "tajweed", True),
    ("Engaging young learners online", "engagement", False),
    ("Lesson-plan discipline and variance", "methodology", True),
    ("Classroom technology and connection quality", "technology", False),
    ("Safeguarding and professional conduct refresher", "conduct", True),
]

WEBSITE_LEADS = [
    ("Bilal Ahmed", "bilal.ahmed@example.co.uk", "+447700900411", "United Kingdom", "Europe/London", "Yusuf", 8, "male", "Weekday evenings", "Google search"),
    ("Fatima Noor", "fatima.noor@example.com", "+13475550188", "United States", "America/New_York", "Maryam", 6, "female", "Weekends", "A friend or family member"),
    ("Omar Farouk", "omar.farouk@example.ca", "+14165550143", "Canada", "America/Toronto", "Zayd", 11, "male", "Weekday evenings", "Facebook / Instagram"),
    ("Ayesha Siddiq", "ayesha.siddiq@example.co.uk", "+447700900522", "United Kingdom", "Europe/London", "Hana", 7, "female", "Weekday afternoons", "My local masjid"),
    ("Ibrahim Malik", "ibrahim.malik@example.com.au", "+61455550177", "Australia", "Australia/Sydney", "Sumayya", 9, "female", "Flexible", "YouTube"),
    ("Khadija Rahman", "khadija.rahman@example.se", "+46701234567", "Sweden", "Europe/Stockholm", "Adam", 5, "male", "Late evenings", "Google search"),
    ("Yasir Chaudhry", "yasir.chaudhry@example.ae", "+971505550199", "United Arab Emirates", "Asia/Dubai", "Bilal", 13, "male", "Weekday evenings", "WhatsApp"),
    ("Sana Iqbal", "sana.iqbal@example.co.uk", "+447700900633", "United Kingdom", "Europe/London", "Zainab", 10, "female", "Weekends", "TikTok"),
    # deliberate duplicates of the first two (same phone / email) to exercise duplicate detection
    ("Bilal Ahmed", "bilal.a@example.co.uk", "+447700900411", "United Kingdom", "Europe/London", "Yusuf", 8, "male", "Weekday evenings", "Google search"),
    ("Fatima Noor", "fatima.noor@example.com", "+13475550999", "United States", "America/New_York", "Maryam", 6, "female", "Weekends", "A friend or family member"),
]


# --------------------------------------------------------------------------- teacher matches
def _teacher_matches(db: Session) -> int:
    from app.services.people import recommend_teachers, _serialise_ranked
    made = 0
    students = db.query(Student).order_by(Student.id).all()
    for i, s in enumerate(students):
        if db.query(TeacherMatch).filter(TeacherMatch.student_id == s.id).first():
            continue
        ranked = recommend_teachers(db, s.course.code if s.course else None, s.gender, s.age, s.timezone)
        if not ranked:
            continue
        top = ranked[0]
        chosen = s.teacher_id or top["teacher_id"]
        overridden = chosen != top["teacher_id"] or (i % 7 == 3)
        if i % 7 == 3 and len(ranked) > 1 and s.teacher_id is None:
            chosen = ranked[1]["teacher_id"]
        entry = next((r for r in ranked if r["teacher_id"] == chosen), None)
        created = datetime.combine(s.join_date or date.today(), datetime.min.time()) + timedelta(hours=10)
        m = TeacherMatch(student_id=s.id, recommended_teacher_id=top["teacher_id"], chosen_teacher_id=chosen,
                         ranked_candidates=_serialise_ranked(ranked),
                         match_score=entry["score"] if entry else top["score"],
                         match_reason="; ".join(entry["reasons"]) if entry else rnd.choice(MATCH_REASONS),
                         overridden=overridden,
                         override_reason=rnd.choice(OVERRIDE_REASONS) if overridden else None,
                         decided_by_id=None)
        m.created_at = created
        age_days = (date.today() - (s.join_date or date.today())).days
        if age_days >= 90:
            m.survived_90_days = s.status != "cancelled" and s.teacher_id == chosen
        db.add(m)
        made += 1
    return made


# --------------------------------------------------------------------------- student leaves
def _student_leaves(db: Session) -> int:
    made = 0
    students = db.query(Student).filter(Student.status.in_(["active", "trial"])).order_by(Student.id).all()
    for i, s in enumerate(students):
        if i % 4:
            continue
        if db.query(Leave).filter(Leave.person_type == "student", Leave.student_id == s.id).first():
            continue
        pending = (i % 8 == 0)
        start = date.today() + timedelta(days=rnd.randint(3, 25)) if pending else date.today() - timedelta(days=rnd.randint(10, 60))
        end = start + timedelta(days=rnd.randint(1, 6))
        lv = Leave(person_type="student", student_id=s.id,
                   leave_type=rnd.choice(["vacation", "sick", "exam", "emergency"]),
                   start_date=start, end_date=end,
                   reason=rnd.choice(["School exams", "Family travel", "Illness", "Eid holidays", "Moving house"]),
                   status="pending" if pending else rnd.choice(["approved", "approved", "rejected"]),
                   requested_by_id=s.client.user_id if s.client else None)
        if lv.status in ("approved", "rejected"):
            lv.approved_at = datetime.combine(start - timedelta(days=1), datetime.min.time())
        db.add(lv)
        made += 1
    return made


# --------------------------------------------------------------------------- HR attendance
def _hr_attendance(db: Session) -> int:
    made = 0
    teachers = db.query(Teacher).order_by(Teacher.id).all()
    emp_ids = [t.employee_id for t in teachers if t.employee_id]
    if not emp_ids:
        return 0
    existing = {(e, d, s) for e, d, s in db.query(HRAttendance.employee_id, HRAttendance.date, HRAttendance.session)
                .filter(HRAttendance.employee_id.in_(emp_ids))}
    base_in = {"am": 9, "evening": 14, "night": 20}
    for t in teachers:
        if not t.employee_id:
            continue
        emp = db.query(Employee).get(t.employee_id)
        start_hour = base_in.get(t.shift, 14) if t.shift != "morning" else 6
        for d in range(30, 0, -1):
            day = date.today() - timedelta(days=d)
            if day.weekday() == 6:  # Sunday off
                continue
            for session, offset in (("am", 0), ("pm", 4)):
                if (t.employee_id, day, session) in existing:
                    continue
                roll = rnd.random()
                if roll < 0.05:
                    row = HRAttendance(employee_id=t.employee_id, date=day, session=session, status="absent")
                else:
                    late = rnd.randint(6, 35) if roll < 0.20 else 0
                    check_in = datetime.combine(day, datetime.min.time()) + timedelta(hours=start_hour + offset, minutes=late)
                    row = HRAttendance(employee_id=t.employee_id, date=day, session=session,
                                       check_in=check_in, check_out=check_in + timedelta(hours=4),
                                       status="late" if late else "present", late_minutes=late, ip="127.0.0.1")
                    if roll < 0.08:
                        row.correction_requested = True
                        row.correction_reason = "Internet outage; joined the class from mobile data."
                        row.correction_status = "pending"
                db.add(row)
                made += 1
    return made


# --------------------------------------------------------------------------- training
def _training(db: Session) -> int:
    made = 0
    teachers = db.query(Teacher).order_by(Teacher.id).all()
    hod = db.query(User).filter(User.email == "qa@oqc.local").first() or db.query(User).filter(User.email == "hr@oqc.local").first()
    for i, t in enumerate(teachers):
        if i % 2:
            continue
        title, category, gate = TRAINING[i % len(TRAINING)]
        if db.query(TrainingAssignment).filter(TrainingAssignment.teacher_id == t.id, TrainingAssignment.title == title).first():
            continue
        status = ["assigned", "in_progress", "completed"][i % 3]
        ta = TrainingAssignment(teacher_id=t.id, title=title, category=category,
                                description="Assigned from the QA review cycle. Complete the module and record your reflection.",
                                assigned_by_id=hod.id if hod else None,
                                due_date=date.today() + timedelta(days=rnd.randint(-10, 30)),
                                status=status, is_promotion_gate=gate,
                                score=round(rnd.uniform(70, 95), 1) if status == "completed" else None,
                                completed_at=datetime.utcnow() - timedelta(days=rnd.randint(1, 20)) if status == "completed" else None)
        db.add(ta)
        made += 1
    return made


# --------------------------------------------------------------------------- website leads
def _website_leads(db: Session) -> int:
    from app.core.utils import next_code
    src = db.query(LeadSource).filter(func.lower(LeadSource.name) == "website").first()
    if not src:
        src = LeadSource(name="Website", source_type="inbound", is_active=True)
        db.add(src)
        db.flush()
    courses = db.query(Course).filter(Course.is_active.is_(True)).order_by(Course.order).all()
    ambassadors = db.query(Client).filter(Client.is_ambassador.is_(True)).all()
    made = 0
    for i, (name, email, phone, country, tz, child, age, gender, pref_time, heard) in enumerate(WEBSITE_LEADS):
        if db.query(Lead).filter(Lead.email == email, Lead.phone == phone).first():
            continue
        dup = (db.query(Lead).filter(Lead.source_id == src.id)
               .filter((Lead.phone == phone) | (func.lower(Lead.email) == email.lower())).first())
        course = courses[i % len(courses)] if courses else None
        ref_client = ambassadors[i % len(ambassadors)] if ambassadors and i % 3 == 0 else None
        created = datetime.utcnow() - timedelta(days=rnd.randint(1, 25), hours=rnd.randint(0, 20))
        lead = Lead(lead_code=next_code(db, Lead, "lead_code", "L-"), full_name=name, email=email, phone=phone,
                    whatsapp=phone, country=country, timezone=tz, student_name=child, student_age=age,
                    students_count=1 if i % 3 else 2, course_interest_id=course.id if course else None,
                    preferred_time=pref_time, source_id=src.id, stage="new" if i % 3 else "contacted",
                    score=rnd.randint(45, 92), whatsapp_opt_in=True,
                    referral_code=ref_client.referral_code if ref_client else None,
                    is_duplicate_of_id=dup.id if dup else None,
                    notes="\n".join([
                        f"How did you hear about us: {heard}",
                        f"Students: {child} ({gender}, {age}y)" + (f"; {child} sibling (male, {max(4, age - 3)}y)" if i % 3 == 0 else ""),
                    ]))
        lead.created_at = created
        db.add(lead)
        db.flush()
        if ref_client:
            db.add(Referral(ambassador_client_id=ref_client.id, referral_code=ref_client.referral_code,
                            referred_name=name, referred_phone=phone, referred_lead_id=lead.id, status="lead",
                            invited_at=created, credit_currency=ref_client.currency))
        db.add(Trial(lead_id=lead.id, student_name=child, course_id=course.id if course else None,
                     status="requested", notes="Requested from the website registration form.",
                     follow_up_date=(created + timedelta(days=1)).date()))
        made += 1
    return made


def run(db: Session) -> None:
    matches = _teacher_matches(db)
    db.flush()
    leaves = _student_leaves(db)
    db.flush()
    attendance = _hr_attendance(db)
    db.flush()
    training = _training(db)
    db.flush()
    leads = _website_leads(db)
    db.commit()
    print(f"    people_extra: {matches} teacher matches, {leaves} student leaves, {attendance} HR attendance rows, "
          f"{training} training assignments, {leads} website leads")
