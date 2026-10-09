"""Parent communication and referral follow-up (docs/PARENT_COMMUNICATION.md).

Requested 9 Oct 2026 (items 12-13): every conversation with a family is recorded with what was said and agreed; an
issue becomes Issue -> Action -> Responsible -> Due date -> Reminder -> Completion; a referral mentioned in a
conversation is never lost, and its reward (account credit only) is approved by a person.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.core.utils import next_code
from app.database import SessionLocal
from app.main import app
from app.models.core import AuditEvent, Notification, Role, User
from app.models.crm import Case, CaseComment, Lead, LeadActivity, ParentContact, Referral
from app.models.finance import LedgerEntry, Subscription
from app.models.hr_erp import Attachment
from app.models.ops import Task
from app.models.people import Client, Student, Teacher
from app.models.scheduling import ReminderLog, TeacherMatch
from app.models.academic import TeacherAssignment
from app.services import contacts
from app.services import people as people_svc

TAG = "pctest" + uuid.uuid4().hex[:6]
PASSWORD = "Pc!" + uuid.uuid4().hex[:10] + "Aa1"


def _login(email: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": email, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {email}"
    return c


@pytest.fixture(scope="module")
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(scope="module")
def world(db):
    role = db.query(Role).filter(Role.slug == "academy_manager").one()
    am = User(email=f"{TAG}.am@example.com", username=f"{TAG}am", full_name=f"{TAG} Academy Manager",
              hashed_password=hash_password(PASSWORD), role_id=role.id, is_active=True)
    db.add(am)
    families, students = [], []
    for label in ("A", "B"):
        c = Client(client_code=next_code(db, Client, "client_code", "C-"), full_name=f"{TAG} Family {label}", email=f"{TAG}{label}@example.com",
                   phone="+447700900321", whatsapp="+447700900321", country="United Kingdom", currency="GBP", status="active")
        db.add(c)
        db.flush()
        s = Student(student_code=next_code(db, Student, "student_code", "S-"), client_id=c.id, full_name=f"{TAG} Child {label}", age=9,
                    status="active")
        db.add(s)
        db.flush()
        families.append(c)
        students.append(s)
    people_svc.assign_teacher(db, students[0], db.get(Teacher, 2), None, reason="Initial match")
    db.commit()
    yield {"am": am, "family": families[0], "student": students[0], "family_b": families[1], "student_b": students[1]}
    db.rollback()
    fids = [f.id for f in families]
    sids = [s.id for s in students]
    pcs = db.query(ParentContact).filter(ParentContact.client_id.in_(fids)).all()
    task_ids = [pc.follow_up_task_id for pc in pcs if pc.follow_up_task_id]
    ref_ids = [r.id for r in db.query(Referral).filter(or_ids(Referral, fids))]
    for a in db.query(Attachment).filter(Attachment.entity_type == "parent_contact", Attachment.entity_id.in_([p.id for p in pcs] or [-1])):
        p = contacts.recording_path(a)
        if p is not None:
            p.unlink(missing_ok=True)
        db.delete(a)
    case_ids = [pc.case_id for pc in pcs if pc.case_id]
    db.query(ParentContact).filter(ParentContact.client_id.in_(fids)).delete(synchronize_session=False)
    task_ids += [t.id for t in db.query(Task).filter(Task.entity_type == "Referral", Task.entity_id.in_(ref_ids or [-1]))]
    task_ids += [t.id for t in db.query(Task).filter(Task.entity_type.in_(["Student", "Client"]), Task.entity_id.in_(sids + fids))]
    db.query(ReminderLog).filter(ReminderLog.entity_type == "Task", ReminderLog.entity_id.in_(task_ids or [-1])).delete(synchronize_session=False)
    db.query(ReminderLog).filter(ReminderLog.entity_type == "Referral", ReminderLog.entity_id.in_(ref_ids or [-1])).delete(synchronize_session=False)
    db.query(Task).filter(Task.id.in_(task_ids or [-1])).delete(synchronize_session=False)
    lead_ids = [r.referred_lead_id for r in db.query(Referral).filter(Referral.id.in_(ref_ids or [-1])) if r.referred_lead_id]
    db.query(LedgerEntry).filter(LedgerEntry.client_id.in_(fids)).delete(synchronize_session=False)
    db.query(Referral).filter(Referral.id.in_(ref_ids or [-1])).delete(synchronize_session=False)
    db.query(LeadActivity).filter(LeadActivity.lead_id.in_(lead_ids or [-1])).delete(synchronize_session=False)
    db.query(Lead).filter(Lead.id.in_(lead_ids or [-1])).delete(synchronize_session=False)
    if case_ids:
        db.query(CaseComment).filter(CaseComment.case_id.in_(case_ids)).delete(synchronize_session=False)
        db.query(Task).filter(Task.entity_type == "Case", Task.entity_id.in_(case_ids)).delete(synchronize_session=False)
        db.query(Case).filter(Case.id.in_(case_ids)).delete(synchronize_session=False)
    db.query(Subscription).filter(Subscription.client_id.in_(fids)).delete(synchronize_session=False)
    db.query(TeacherAssignment).filter(TeacherAssignment.student_id.in_(sids)).delete(synchronize_session=False)
    db.query(TeacherMatch).filter(TeacherMatch.student_id.in_(sids)).delete(synchronize_session=False)
    db.query(AuditEvent).filter(AuditEvent.entity_type == "Student", AuditEvent.entity_id.in_(sids)).delete(synchronize_session=False)
    db.query(Notification).filter(Notification.user_id == am.id).delete(synchronize_session=False)
    db.query(Notification).filter(Notification.body.like(f"%{TAG}%") | Notification.title.like(f"%{TAG}%")).delete(synchronize_session=False)
    db.query(Student).filter(Student.id.in_(sids)).delete(synchronize_session=False)
    db.query(Client).filter(Client.id.in_(fids)).delete(synchronize_session=False)
    db.query(User).filter(User.id == am.id).delete(synchronize_session=False)
    db.commit()


def or_ids(model, fids):
    from sqlalchemy import or_
    return or_(model.ambassador_client_id.in_(fids), model.referred_client_id.in_(fids))


def _record(c: TestClient, w, **extra) -> ParentContact:
    data = {"client_id": str(w["family"].id), "student_id": str(w["student"].id), "purpose": "attendance", "channel": "phone",
            "direction": "outbound", "summary": f"{TAG} called about absences", "parent_response": "School exams this week",
            "sentiment": "concerned"}
    data.update(extra)
    r = c.post("/parent-contacts/new", data=data, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/parent-contacts/"), r.headers.get("location")
    s = SessionLocal()
    pc = s.get(ParentContact, int(r.headers["location"].rsplit("/", 1)[1]))
    s.expunge(pc)
    s.close()
    return pc


# ------------------------------------------------------------------------------------------------ the conversation
def test_a_conversation_with_an_issue_creates_a_follow_up(db, world):
    am = _login(world["am"].email, PASSWORD)
    pc = _record(am, world, has_issue="1", issue="Missed three classes in exam week", issue_category="attendance",
                 agreed_action="Move classes to the weekend for two weeks")
    pc = db.get(ParentContact, pc.id)
    assert pc.contacted_by_id == world["am"].id and pc.issue and pc.parent_response == "School exams this week"
    task = pc.follow_up_task
    assert task is not None and task.assignee_id == world["am"].id and task.status == "todo"
    assert task.due_date == contacts.org_today() + timedelta(days=2), "defaults to two days"
    assert "Move classes to the weekend" in task.title
    page = am.get(f"/parent-contacts/{pc.id}").text
    assert "Issue → action → responsible → due → completion" in page
    assert f"/parent-contacts/{pc.id}" in am.get(f"/clients/{world['family'].id}?tab=comms").text
    assert "Parent communication" in am.get(f"/students/{world['student'].id}/journey?view=summary").text


def test_nothing_to_follow_up_creates_no_task_and_a_summary_is_required(db, world):
    am = _login(world["am"].email, PASSWORD)
    pc = _record(am, world, purpose="academic", sentiment="positive", summary=f"{TAG} progress update, all good")
    assert pc.follow_up_task_id is None
    r = am.post("/parent-contacts/new", data={"client_id": str(world["family"].id), "purpose": "general", "channel": "phone"},
                follow_redirects=False)
    assert r.headers["location"].startswith("/parent-contacts/new"), "refused without a summary"


def test_follow_up_is_reminded_daily_until_done(db, world):
    am = _login(world["am"].email, PASSWORD)
    pc = _record(am, world, agreed_action="Send the revision sheet", follow_up_date=contacts.org_today().isoformat())
    first = contacts.remind_follow_ups(db)
    db.commit()
    assert first["reminded"] >= 1
    logs = db.query(ReminderLog).filter(ReminderLog.entity_type == "Task", ReminderLog.entity_id == pc.follow_up_task_id).count()
    assert logs == 1
    contacts.remind_follow_ups(db)
    db.commit()
    assert db.query(ReminderLog).filter(ReminderLog.entity_type == "Task", ReminderLog.entity_id == pc.follow_up_task_id).count() == 1, \
        "once per day"
    r = am.post(f"/parent-contacts/{pc.id}/follow-up/done", data={"note": ""}, follow_redirects=False)
    db.expire_all()
    assert db.get(ParentContact, pc.id).follow_up_task.status == "todo", "a note is required"
    am.post(f"/parent-contacts/{pc.id}/follow-up/done", data={"note": "Sheet sent on WhatsApp"})
    db.expire_all()
    t = db.get(ParentContact, pc.id).follow_up_task
    assert t.status == "done" and "Sheet sent on WhatsApp" in t.description
    db.query(ReminderLog).filter(ReminderLog.entity_type == "Task", ReminderLog.entity_id == t.id).delete()
    db.commit()
    contacts.remind_follow_ups(db)
    db.commit()
    assert db.query(ReminderLog).filter(ReminderLog.entity_type == "Task", ReminderLog.entity_id == t.id).count() == 0, "done: no more reminders"


def test_an_issue_can_be_raised_as_a_complaint(db, world):
    am = _login(world["am"].email, PASSWORD)
    pc = _record(am, world, purpose="complaint", has_issue="1", issue="Teacher joined late twice", issue_category="teacher",
                 parent_response="The teacher was 15 minutes late on Monday and Wednesday")
    r = am.post(f"/parent-contacts/{pc.id}/complaint", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/cases/")
    db.expire_all()
    pc = db.get(ParentContact, pc.id)
    case = db.get(Case, pc.case_id)
    assert case.case_type == "complaint" and case.description.startswith("The teacher was 15 minutes late")
    assert case.against_employee_id == db.get(Teacher, 2).employee_id, "the teacher of the student, from the complaint rules"
    assert pc.follow_up_task.priority == "high", "a teacher issue is followed up urgently"


# ------------------------------------------------------------------------------------------------ referrals
def test_a_referral_with_details_becomes_a_lead_with_a_call_task(db, world):
    am = _login(world["am"].email, PASSWORD)
    pc = _record(am, world, purpose="referral", summary=f"{TAG} family happy, wants to refer a cousin", referral_mentioned="1",
                 referred_name=f"{TAG} Cousin", referred_phone="+447700900777", referred_relation="family", referral_note="Two children, Nazra")
    db.expire_all()
    pc = db.get(ParentContact, pc.id)
    ref = pc.referral
    assert ref is not None and ref.status == "lead" and ref.ambassador_client_id == world["family"].id
    lead = db.get(Lead, ref.referred_lead_id)
    assert lead.full_name == f"{TAG} Cousin" and lead.referral_code == db.get(Client, world["family"].id).referral_code
    assert lead.whatsapp_opt_in is False, "no automatic messages to someone who has not asked yet"
    task = db.query(Task).filter(Task.entity_type == "Referral", Task.entity_id == ref.id).one()
    assert task.title.startswith("Call referred family") and task.due_date == contacts.org_today() + timedelta(days=1)


def test_a_referral_without_details_records_an_ask_with_a_task_to_collect_them(db, world):
    am = _login(world["am"].email, PASSWORD)
    pc = _record(am, world, purpose="general", summary=f"{TAG} mentioned a neighbour may join", referral_mentioned="1")
    db.expire_all()
    ref = db.get(ParentContact, pc.id).referral
    assert ref.status == "ask" and ref.referred_lead_id is None
    task = db.query(Task).filter(Task.entity_type == "Referral", Task.entity_id == ref.id).one()
    assert task.title.startswith("Collect referral details") and task.assignee_id == world["am"].id


def test_reward_eligibility_asks_for_approval_and_never_posts_credit(db, world):
    fam, other, child_b = world["family"], world["family_b"], world["student_b"]
    ref = Referral(ambassador_client_id=fam.id, referral_code=fam.referral_code or "X", referred_client_id=other.id, status="signed_up",
                   referred_name=other.full_name, invited_at=datetime.utcnow())
    db.add(ref)
    db.add(Subscription(subscription_code=next_code(db, Subscription, "subscription_code", "SUB-"), client_id=other.id,
                        student_id=child_b.id, status="active", price=40, currency="GBP"))
    db.commit()
    before = db.query(LedgerEntry).filter(LedgerEntry.client_id.in_([fam.id, other.id])).count()
    out = contacts.referral_follow_ups(db)
    db.commit()
    db.refresh(ref)
    assert out["eligible"] >= 1 and ref.eligible_at is not None and ref.status == "signed_up"
    assert contacts.referral_stage(ref) == "Eligible: credit to approve"
    tasks = db.query(Task).filter(Task.entity_type == "Referral", Task.entity_id == ref.id, Task.title.like("Approve referral credit%")).count()
    assert tasks == 1
    assert db.query(LedgerEntry).filter(LedgerEntry.client_id.in_([fam.id, other.id])).count() == before, "credit waits for a person"
    contacts.referral_follow_ups(db)
    db.commit()
    assert db.query(Task).filter(Task.entity_type == "Referral", Task.entity_id == ref.id).count() == 1, "asked once"


# ------------------------------------------------------------------------------------------------ privacy and storage
def test_recordings_are_served_only_through_the_conversation(db, world):
    am = _login(world["am"].email, PASSWORD)
    r = am.post("/parent-contacts/new", data={"client_id": str(world["family"].id), "purpose": "academic", "channel": "phone",
                                              "summary": f"{TAG} recorded call"},
                files={"recording": ("call.mp3", io.BytesIO(b"ID3audio"), "audio/mpeg")}, follow_redirects=False)
    pc_id = int(r.headers["location"].rsplit("/", 1)[1])
    pc = db.get(ParentContact, pc_id)
    att = db.get(Attachment, pc.recording_attachment_id)
    assert am.get(f"/parent-contacts/{pc_id}/recording").status_code == 200
    assert am.get(f"/storage/{att.file_path}").status_code == 404
    teacher = _login("teacher2@oqc.local", "Teacher@123")
    assert teacher.get(f"/parent-contacts/{pc_id}", follow_redirects=False).status_code in (302, 303, 403)


def test_a_conversation_about_a_confidential_complaint_follows_the_complaint(db, world):
    am = _login(world["am"].email, PASSWORD)
    pc = next(p for p in db.query(ParentContact).filter(ParentContact.client_id == world["family"].id, ParentContact.case_id.isnot(None)))
    sup = db.get(User, db.get(Teacher, 2).supervisor_id)
    assert not contacts.can_view(db, sup, pc), "the supervisor of the teacher complained about does not see it"
    assert contacts.can_view(db, world["am"], pc)
    assert am.get("/parent-contacts").status_code == 200
