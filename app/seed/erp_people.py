"""ERP-parity people seed (audit sections 3.2 / 3.3 / 3.4).

Fills the ERP fields on the clients and students created by ``seed.people`` (fee recurrence, shift, state,
legacy code, opening balance, referrals, academic manager / group, status remarks, trial days, drop dates),
adds the Client Form's Contacts and Credentials grids, and spreads 60 days of client requests over the five
approval lists: time change, teacher change, referred contacts, complaints and student leaves.

Idempotent: every block checks for existing rows before inserting.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.core import User
from app.models.crm import Case
from app.models.erp import (ClientAcademicGroup, ClientContact, ClientCredential, ReferredContact, SessionSlot,
                            TeacherChangeRequest, TimeChangeRequest)
from app.models.finance import Subscription
from app.models.people import Client, Leave, Student, Teacher

rnd = random.Random(2609)

FEE_RECURRENCE = ["monthly", "monthly", "monthly", "quarterly", "quarterly", "half_yearly", "yearly", "per_class"]

STATES = {
    "United Kingdom": ["Greater London", "West Midlands", "Greater Manchester", "West Yorkshire", "Lancashire", "Kent"],
    "United States": ["Texas", "New Jersey", "Illinois", "California", "New York", "Virginia"],
    "Canada": ["Ontario", "Alberta", "Quebec", "British Columbia"],
    "Australia": ["New South Wales", "Victoria", "Queensland", "Western Australia"],
    "Pakistan": ["Punjab", "Sindh", "Khyber Pakhtunkhwa", "Islamabad Capital Territory"],
    "United Arab Emirates": ["Dubai", "Abu Dhabi", "Sharjah"],
    "Saudi Arabia": ["Riyadh", "Makkah", "Eastern Province"],
}

CHURN_REMARKS = [
    "Family moved to a local madrasah after relocating.",
    "Paused for financial reasons; win-back call booked for next term.",
    "Children started school exams; asked to be contacted in the summer.",
    "Unhappy with the timing options available in their timezone.",
    "Completed the course and did not renew for the next level.",
]

CONTACT_REMARKS = ["Primary contact", "Father's mobile", "Mother's WhatsApp", "Evening contact only",
                   "Use for billing reminders", "Emergency contact", "Do not call before 5pm"]

REFERENCE_NAMES = [
    ("Abdul Hakeem Patel", "a.patel@example.com", "+44 7700 900311", "friend"),
    ("Ruqayya Ismail", "r.ismail@example.com", "+1 202 555 0143", "family"),
    ("Tariq Mahmood", "t.mahmood@example.com", "+44 7700 900288", "colleague"),
    ("Nadia Suleiman", "n.suleiman@example.com", "+61 4 5550 1177", "community"),
    ("Imran Dawood", "i.dawood@example.com", "+1 416 555 0192", "friend"),
    ("Sadia Anwar", "s.anwar@example.com", "+44 7700 900455", "family"),
    ("Hassan Bhatti", "h.bhatti@example.com", "+92 300 5550123", "colleague"),
    ("Fareeda Kaleem", "f.kaleem@example.com", "+44 7700 900677", "community"),
    ("Junaid Vawda", "j.vawda@example.com", "+27 82 555 0166", "friend"),
    ("Ayesha Motala", "a.motala@example.com", "+44 7700 900822", "family"),
]

COMPLAINTS = [  # complaint_type, title, description, company_response (None = still being worked)
    ("Timing", "Class keeps starting five minutes late",
     "For the last two weeks the teacher joins around five minutes after the scheduled time.",
     "We reviewed the join logs with the teacher and moved the slot so there is no back-to-back class before it."),
    ("Teacher", "Would like a female teacher for our daughter",
     "Our daughter is shy with a male teacher and is not speaking during the class.", None),
    ("Billing", "Invoice charged for a month we were on leave",
     "We applied for leave in advance but the invoice still shows the full month.",
     "The leave discount has been applied and a credit note issued on the next invoice."),
    ("Technical", "Audio keeps dropping during the class",
     "The sound cuts out several times in every class; the children lose the lesson.",
     "Our technical team moved the class to the Zoom bridge and the last four classes were clean."),
    ("Behaviour", "Teacher was short with my son",
     "My son says the teacher raised his voice when he could not read the page.", None),
    ("Teacher", "Substitute teacher every week",
     "We have had a different teacher three weeks in a row and there is no continuity.",
     "A permanent teacher has been allocated and the arrangement cover has been ended."),
    ("Timing", "Need a later slot after school hours",
     "The current time clashes with school pick-up twice a week.", None),
    ("Other", "Books were not sent",
     "We were told the course book would be emailed after the trial and nothing arrived.",
     "The full book pack has been emailed and added to the portal library."),
]

LEAVE_REASONS = [
    ("vacation", "Family travelling to visit grandparents.", "Back on the Monday after Eid week."),
    ("sick", "Chickenpox - doctor advised a week of rest.", "Will send a note when they are clear."),
    ("exam", "School end-of-term exams.", "Please hold the slot; we resume straight after."),
    ("emergency", "Family bereavement.", "We will confirm the return date by phone."),
    ("vacation", "Half-term break away from home.", "No internet at the cottage."),
    ("casual", "Wedding in the family this weekend.", "Only the weekend classes are affected."),
]

CREDENTIALS = [  # type, login template, secret
    ("zoom", "zoom.{code}@quran-college.org", "Zoom!Demo{n}"),
    ("teams", "teams.{code}@quran-college.org", "Teams!Demo{n}"),
]

REQUEST_STATUS_MIX = ["pending", "pending", "approved", "approved", "approved", "rejected", "cancelled"]


def _ago(days: int) -> datetime:
    return datetime.utcnow() - timedelta(days=days, hours=rnd.randint(0, 20))


def _clients(db: Session) -> list[Client]:
    return db.query(Client).order_by(Client.id).all()


# --------------------------------------------------------------------------- client ERP fields
def _fill_clients(db: Session, clients: list[Client]) -> int:
    academics = db.query(User).filter(User.email == "academics@oqc.local").first()
    if academics is None:
        academics = db.query(User).filter(User.email.in_(["manager@oqc.local", "admin@oqc.local"])).first()
    groups = {g.shift_group: g for g in db.query(ClientAcademicGroup).filter(ClientAcademicGroup.status == "active")}
    n = 0
    for i, c in enumerate(clients):
        c.fee_recurrence = FEE_RECURRENCE[i % len(FEE_RECURRENCE)]
        c.shift = "morning" if (i % 5) < 2 else "night"  # ~40 / 60
        if not c.state:
            c.state = rnd.choice(STATES.get(c.country, ["Central"]))
        if i % 2 == 0 and not c.legacy_code:
            c.legacy_code = f"OQC-1{1000 + i:04d}"[:9]
        if i % 9 == 3 and not float(c.opening_balance or 0):
            c.opening_balance = rnd.choice([25, 40, 55, 75, 120])
        if academics is not None and not c.academic_manager_id:
            c.academic_manager_id = academics.id
        grp = groups.get(c.shift)
        if grp is not None and not c.academic_group_id:
            c.academic_group_id = grp.id
        if c.status == "churned" and not c.status_remarks:
            c.status_remarks = CHURN_REMARKS[i % len(CHURN_REMARKS)]
        n += 1
    # ~6 families referred by another family
    pool = [c for c in clients if c.status in ("active", "trial")]
    if len(pool) > 12:
        for j in range(6):
            child = pool[6 + j * 2]
            parent = pool[j]
            if child.id != parent.id and not child.referred_by_client_id:
                child.referred_by_client_id = parent.id
                parent.is_ambassador = True
                if not parent.referral_code:
                    parent.referral_code = f"REF{parent.id:05d}"
    db.flush()
    return n


def _fill_students(db: Session) -> int:
    students = db.query(Student).order_by(Student.id).all()
    trial_days = [3, 3, 5, 5, 7]
    for i, s in enumerate(students):
        s.trial_days = trial_days[i % len(trial_days)]
        if not s.legacy_code:
            s.legacy_code = f"OQC-S{2000 + i:04d}"[:10]
        if s.status == "cancelled" and not s.drop_date:
            s.drop_date = s.cancelled_at or (date.today() - timedelta(days=rnd.randint(10, 120)))
        if i % 4 == 0 and not s.referred_by:
            s.referred_by = rnd.choice(["Friend at the masjid", "Facebook group", "Family in the UK",
                                        "Our older child's teacher", "School parent"])
        if i % 3 == 0 and not s.email:
            handle = s.full_name.lower().replace(" ", ".").replace("'", "")
            s.email = f"{handle}{s.id}@example.com"
    db.flush()
    return len(students)


# --------------------------------------------------------------------------- contacts & credentials
def _contacts(db: Session, clients: list[Client], target: int = 30) -> int:
    have = db.query(ClientContact).count()
    if have >= target:
        return 0
    made = 0
    for i, c in enumerate(clients):
        if have + made >= target:
            break
        if db.query(ClientContact).filter(ClientContact.client_id == c.id).count():
            continue
        rows = [("phone", c.phone or f"+44 7700 9{i:05d}"), ("whatsapp", c.whatsapp or c.phone or f"+44 7700 9{i:05d}")]
        if c.email:
            rows.append(("email", c.email))
        for kind, detail in rows[: 2 if i % 2 else 3]:
            db.add(ClientContact(client_id=c.id, contact_type=kind, detail=detail,
                                 remarks=CONTACT_REMARKS[(i + len(kind)) % len(CONTACT_REMARKS)], status="active"))
            made += 1
            if have + made >= target:
                break
    db.flush()
    return made


def _credentials(db: Session, clients: list[Client], target: int = 10) -> int:
    have = db.query(ClientCredential).count()
    if have >= target:
        return 0
    made = 0
    for i, c in enumerate(clients):
        if have + made >= target:
            break
        if db.query(ClientCredential).filter(ClientCredential.client_id == c.id).count():
            continue
        kind, login, secret = CREDENTIALS[i % len(CREDENTIALS)]
        db.add(ClientCredential(client_id=c.id, credential_type=kind,
                               login=login.format(code=c.client_code.lower()), secret=secret.format(n=1000 + i),
                               remarks="Demo credential - not a real account.", status="active"))
        made += 1
    db.flush()
    return made


# --------------------------------------------------------------------------- client requests
def _decide(obj, status: str, decider: User | None, when: datetime, remarks: str) -> None:
    obj.status = status
    if status != "pending":
        obj.decided_by_id = decider.id if decider else None
        obj.decided_at = when
        obj.decision_remarks = remarks


def _request_students(db: Session) -> list[Student]:
    """Students the demo requests are raised for (subscriptions are seeded later, so requests key on students)."""
    return (db.query(Student).filter(Student.status.in_(["active", "trial"]))
            .order_by(Student.id).limit(200).all())


def _subs_by_student(db: Session) -> dict:
    rows = (db.query(Subscription).filter(Subscription.status.notin_(["cancelled", "expired"]))
            .order_by(Subscription.id).all())
    out: dict = {}
    for s in rows:
        out.setdefault(s.student_id, s)
    return out


def _backfill_subscriptions(db: Session) -> int:
    """Attach subscriptions to requests seeded before seed.finance created them (later runs of seed.py)."""
    subs = _subs_by_student(db)
    if not subs:
        return 0
    n = 0
    for r in db.query(TimeChangeRequest).filter(TimeChangeRequest.subscription_id.is_(None)).all():
        sub = subs.get(r.student_id)
        if sub is not None:
            r.subscription_id = sub.id
            r.current_slot_id = r.current_slot_id or sub.slot_id
            n += 1
    for r in db.query(TeacherChangeRequest).filter(TeacherChangeRequest.subscription_id.is_(None)).all():
        sub = subs.get(r.student_id)
        if sub is not None:
            r.subscription_id = sub.id
            r.current_teacher_id = r.current_teacher_id or sub.teacher_id
            n += 1
    db.flush()
    return n


def _time_changes(db: Session, clients: list[Client], decider: User | None, target: int = 12) -> int:
    have = db.query(TimeChangeRequest).count()
    if have >= target:
        return 0
    slots = db.query(SessionSlot).filter(SessionSlot.status == "active").order_by(SessionSlot.start_time).all()
    students = _request_students(db)
    subs = _subs_by_student(db)
    if not students or not slots:
        return 0
    made = 0
    for i in range(target - have):
        st = students[(i * 3) % len(students)]
        sub = subs.get(st.id)
        status = REQUEST_STATUS_MIX[i % len(REQUEST_STATUS_MIX)]
        created = _ago(rnd.randint(1, 60))
        r = TimeChangeRequest(client_id=st.client_id, student_id=st.id,
                              subscription_id=sub.id if sub else None,
                              current_slot_id=(sub.slot_id if sub else None) or slots[(i * 5) % len(slots)].id,
                              new_slot_id=slots[(i * 7 + 3) % len(slots)].id,
                              days=sorted(rnd.sample([0, 1, 2, 3, 4], rnd.choice([3, 4, 5]))),
                              description=rnd.choice([
                                  "School has changed our afternoons - we need a later slot.",
                                  "Clashes with the children's swimming lesson.",
                                  "Daylight saving has moved the class to bedtime.",
                                  "Father's shift changed; we need an earlier class.",
                                  "We are travelling and the timezone no longer works."]),
                              status="pending", created_at=created, updated_at=created)
        db.add(r)
        _decide(r, status, decider, created + timedelta(days=1),
                "Slot confirmed with the teacher." if status == "approved" else
                ("No free teacher at that time." if status == "rejected" else "Family withdrew the request."))
        made += 1
    db.flush()
    return made


def _teacher_changes(db: Session, decider: User | None, target: int = 8) -> int:
    have = db.query(TeacherChangeRequest).count()
    if have >= target:
        return 0
    students = _request_students(db)
    subs = _subs_by_student(db)
    teachers = db.query(Teacher).filter(Teacher.status == "active").order_by(Teacher.id).all()
    if not students or len(teachers) < 2:
        return 0
    made = 0
    for i in range(target - have):
        st = students[(i * 5 + 2) % len(students)]
        sub = subs.get(st.id)
        current_teacher_id = (sub.teacher_id if sub else None) or st.teacher_id
        new_t = teachers[(i * 3) % len(teachers)]
        if new_t.id == current_teacher_id:
            new_t = teachers[(i * 3 + 1) % len(teachers)]
        status = REQUEST_STATUS_MIX[(i + 2) % len(REQUEST_STATUS_MIX)]
        created = _ago(rnd.randint(1, 60))
        r = TeacherChangeRequest(client_id=st.client_id, student_id=st.id,
                                 subscription_id=sub.id if sub else None,
                                 current_teacher_id=current_teacher_id, new_teacher_id=new_t.id,
                                 description=rnd.choice([
                                     "We would prefer a female teacher for our daughter.",
                                     "The children respond better to a teacher who speaks Urdu.",
                                     "Teacher has been absent twice this month.",
                                     "Looking for a Hifz specialist as we move to memorisation.",
                                     "Accent is hard for our youngest to follow."]),
                                 status="pending", created_at=created, updated_at=created)
        db.add(r)
        _decide(r, status, decider, created + timedelta(days=2),
                "Reallocated and the first class went well." if status == "approved" else
                ("Current teacher retained after a call with the family." if status == "rejected"
                 else "Family cancelled after speaking with the manager."))
        made += 1
    db.flush()
    return made


def _references(db: Session, clients: list[Client], decider: User | None, target: int = 10) -> int:
    have = db.query(ReferredContact).count()
    if have >= target:
        return 0
    pool = [c for c in clients if c.status in ("active", "trial")] or clients
    if not pool:
        return 0
    made = 0
    for i in range(target - have):
        name, email, phone, rtype = REFERENCE_NAMES[i % len(REFERENCE_NAMES)]
        c = pool[(i * 4) % len(pool)]
        status = REQUEST_STATUS_MIX[(i + 1) % len(REQUEST_STATUS_MIX)]
        created = _ago(rnd.randint(1, 60))
        r = ReferredContact(client_id=c.id, name=name, email=email, contact_no=phone, reference_type=rtype,
                            description=rnd.choice([
                                "They have two children who want to start Qaida.",
                                "Colleague at work; asked about evening classes.",
                                "Cousin in Birmingham looking for a female teacher.",
                                "Neighbour who saw our children's certificates.",
                                "Met at the masjid; wants a trial for three children."]),
                            status="pending", created_at=created, updated_at=created)
        db.add(r)
        _decide(r, status, decider, created + timedelta(days=1),
                "Lead created and passed to the closer." if status == "approved" else
                ("Already in the system as an existing lead." if status == "rejected"
                 else "Contact asked not to be called."))
        made += 1
    db.flush()
    return made


def _complaints(db: Session, clients: list[Client], target: int = 8) -> int:
    have = db.query(Case).filter(Case.case_type == "complaint", Case.complaint_type.isnot(None)).count()
    if have >= target:
        return 0
    pool = [c for c in clients if c.status in ("active", "trial", "churned")] or clients
    if not pool:
        return 0
    from app.core.utils import next_code
    made = 0
    for i in range(target - have):
        ctype, title, body, response = COMPLAINTS[i % len(COMPLAINTS)]
        c = pool[(i * 3 + 1) % len(pool)]
        student = c.students[0] if c.students else None
        status = REQUEST_STATUS_MIX[(i + 3) % len(REQUEST_STATUS_MIX)]
        created = _ago(rnd.randint(1, 60))
        k = Case(case_number=next_code(db, Case, "case_number", "CS-"), case_type="complaint", title=title,
                 description=body, raised_by_type="client", raised_by_user_id=c.user_id, client_id=c.id,
                 student_id=student.id if student else None,
                 teacher_id=student.teacher_id if student else None,
                 category="teaching_quality" if ctype == "Teacher" else ctype.lower(),
                 priority="high" if ctype in ("Teacher", "Behaviour") else "medium",
                 status="resolved" if status == "approved" else "open",
                 sla_hours=48, sla_due_at=created + timedelta(hours=48), source="portal",
                 complaint_type=ctype, company_response=response, approval_status=status,
                 created_at=created, updated_at=created)
        if status == "approved":
            k.resolution = response or "Resolved with the family by phone."
            k.resolved_at = created + timedelta(days=2)
        db.add(k)
        db.flush()
        made += 1
    return made


def _leaves(db: Session, decider: User | None = None, target: int = 6) -> int:
    have = db.query(Leave).filter(Leave.person_type == "student", Leave.apply_date.isnot(None)).count()
    if have >= target:
        return 0
    students = (db.query(Student).filter(Student.status.in_(["active", "trial"]))
                .order_by(Student.id).limit(120).all())
    if not students:
        return 0
    made = 0
    for i in range(target - have):
        s = students[(i * 9) % len(students)]
        ltype, reason, detail = LEAVE_REASONS[i % len(LEAVE_REASONS)]
        status = REQUEST_STATUS_MIX[(i + 4) % len(REQUEST_STATUS_MIX)]
        applied = date.today() - timedelta(days=rnd.randint(3, 60))
        start = applied + timedelta(days=rnd.randint(2, 10))
        created = datetime.combine(applied, datetime.min.time()) + timedelta(hours=rnd.randint(8, 20))
        lv = Leave(person_type="student", student_id=s.id, leave_type=ltype, start_date=start,
                   end_date=start + timedelta(days=rnd.choice([2, 4, 6, 9])), reason=reason,
                   leave_detail=detail, leave_for_all=(i % 3 == 0), apply_date=applied, status=status,
                   created_at=created, updated_at=created)
        if status == "approved":
            lv.approved_at = created + timedelta(days=1)
            lv.approved_by_id = decider.id if decider else None
        db.add(lv)
        made += 1
    db.flush()
    return made


# --------------------------------------------------------------------------- deferred stage
# The seed runner order is core, academic, erp_config, people, erp_people, ..., finance: subscriptions and the
# client academic groups therefore do not exist yet on a fresh reset. Finish those links at the end of the run
# (the same atexit pattern seed.erp_config and seed.academic_curriculum use).
_DEFERRED_ARMED = False
_DEFERRED_DONE = False


def _assign_groups(db: Session) -> int:
    groups = {g.shift_group: g for g in db.query(ClientAcademicGroup).filter(ClientAcademicGroup.status == "active")}
    if not groups:
        return 0
    n = 0
    for c in db.query(Client).filter(Client.academic_group_id.is_(None)).all():
        grp = groups.get(c.shift or "night")
        if grp is not None:
            c.academic_group_id = grp.id
            n += 1
    db.flush()
    return n


def _run_deferred(db: Session) -> str:
    global _DEFERRED_DONE
    if not db.query(ClientAcademicGroup.id).first():
        from app.seed import erp_config  # idempotent; creates the groups now that employees exist
        erp_config.run(db)
    groups = _assign_groups(db)
    linked = _backfill_subscriptions(db)
    db.commit()
    _DEFERRED_DONE = True
    return f"academic groups set on {groups} client(s), {linked} request(s) linked to a subscription"


def _arm_deferred() -> None:
    global _DEFERRED_ARMED
    if _DEFERRED_ARMED:
        return
    _DEFERRED_ARMED = True
    import atexit

    def _finish() -> None:
        if _DEFERRED_DONE:
            return
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            if not db.query(Subscription.id).first() and not db.query(Client.id).first():
                return
            print("  + seed.erp_people (link stage): " + _run_deferred(db))
        except Exception as exc:  # pragma: no cover - seeding must never break the run
            db.rollback()
            print(f"  ! seed.erp_people deferred stage failed: {exc}")
        finally:
            db.close()

    atexit.register(_finish)


# --------------------------------------------------------------------------- entry point
def run(db: Session) -> None:
    clients = _clients(db)
    if not clients:
        print("    - seed.erp_people: no clients yet, skipped")
        return
    decider = db.query(User).filter(User.email == "academics@oqc.local").first() or \
        db.query(User).filter(User.email == "admin@oqc.local").first()
    counts = {
        "clients_updated": _fill_clients(db, clients),
        "students_updated": _fill_students(db),
        "contacts": _contacts(db, clients),
        "credentials": _credentials(db, clients),
        "time_change_requests": _time_changes(db, clients, decider),
        "teacher_change_requests": _teacher_changes(db, decider),
        "referred_contacts": _references(db, clients, decider),
        "complaints": _complaints(db, clients),
        "student_leaves": _leaves(db, decider),
        "subscriptions_linked": _backfill_subscriptions(db),
    }
    db.flush()
    print("    erp_people: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    if db.query(Subscription.id).first() and db.query(ClientAcademicGroup.id).first():
        print("    erp_people: " + _run_deferred(db))
    else:
        _arm_deferred()
