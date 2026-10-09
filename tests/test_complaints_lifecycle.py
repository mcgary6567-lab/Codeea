"""Complaint lifecycle (docs/COMPLAINTS.md): ticket, investigation finding, escalation ladder, parent confirmation,
confidential complaint history and the guards around them.

Requested 9 Oct 2026: a parent complaint must become a trackable ticket; the family's words stay separate from the
verified finding; it climbs a defined ladder (Academy Manager, Head of Admissions, HR, CEO); it is never simply
"resolved" internally but waits for the family to confirm; and complaint history appears on the staff profile, with
privacy controls and an audit trail.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core import rbac
from app.core.security import hash_password
from app.database import SessionLocal
from app.main import app
from app.models.core import AuditEvent, Notification, RiskAlert, Role, Setting, User
from app.models.crm import Case, CaseComment, ParentContact
from app.models.hr_erp import Attachment
from app.models.ops import Task
from app.models.people import Client, Employee, Teacher
from app.services import complaints as cx

TAG = "cxtest" + uuid.uuid4().hex[:6]
PASSWORD = "Cx!" + uuid.uuid4().hex[:10] + "Aa1"


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
    """Two new staff (Head of Admissions, Academy Manager), the seeded family parent1 and their child's teacher."""
    saved = {k: db.query(Setting).filter(Setting.key == k).first() for k in (cx.LADDER_KEY, cx.CONFIRM_DAYS_KEY, cx.UNREACHABLE_KEY)}
    saved = {k: (dict(v.value) if isinstance(v.value, dict) else v.value) if v else None for k, v in saved.items()}
    for k in saved:
        db.query(Setting).filter(Setting.key == k).delete()
    users = {}
    for key, slug in (("ha", "head_of_admissions"), ("am", "academy_manager")):
        role = db.query(Role).filter(Role.slug == slug).one()
        u = User(email=f"{TAG}.{key}@example.com", username=f"{TAG}{key}", full_name=f"{TAG} {key.upper()}",
                 hashed_password=hash_password(PASSWORD), role_id=role.id, is_active=True)
        db.add(u)
        users[key] = u
    db.commit()
    parent = db.query(User).filter(User.email == "parent1@oqc.local").one()
    family = db.query(Client).filter(Client.user_id == parent.id).one()
    child = next(s for s in family.students if s.teacher and s.teacher.user_id and s.teacher.employee_id)
    teacher = child.teacher
    supervisor = db.get(User, teacher.supervisor_id) if teacher.supervisor_id else None
    yield {"ha": users["ha"], "am": users["am"], "family": family, "child": child, "teacher": teacher,
           "employee": db.get(Employee, teacher.employee_id), "supervisor": supervisor}
    # ---- clean up everything this module created, so the suite can run twice on the same database
    db.rollback()
    ids = [i for (i,) in db.query(Case.id).filter(Case.title.like(f"{TAG}%"))]
    if ids:
        for a in db.query(Attachment).filter(Attachment.entity_type == "case", Attachment.entity_id.in_(ids)):
            p = cx.evidence_path(a)
            if p is not None:
                p.unlink(missing_ok=True)
            db.delete(a)
        db.query(ParentContact).filter(ParentContact.case_id.in_(ids)).delete(synchronize_session=False)
        db.query(CaseComment).filter(CaseComment.case_id.in_(ids)).delete(synchronize_session=False)
        db.query(Task).filter(Task.entity_type == "Case", Task.entity_id.in_(ids)).delete(synchronize_session=False)
        db.query(RiskAlert).filter(RiskAlert.entity_type == "Case", RiskAlert.entity_id.in_(ids)).delete(synchronize_session=False)
        db.query(AuditEvent).filter(AuditEvent.entity_type == "Case", AuditEvent.entity_id.in_(ids)).delete(synchronize_session=False)
        for cid in ids:
            db.query(Notification).filter(Notification.link.like(f"%/cases/{cid}")).delete(synchronize_session=False)
        db.query(Case).filter(Case.repeat_of_id.in_(ids)).update({Case.repeat_of_id: None}, synchronize_session=False)
        db.query(Case).filter(Case.id.in_(ids)).delete(synchronize_session=False)
    for u in users.values():
        db.query(Notification).filter(Notification.user_id == u.id).delete(synchronize_session=False)
        db.query(User).filter(User.id == u.id).delete(synchronize_session=False)
    for k, v in saved.items():
        db.query(Setting).filter(Setting.key == k).delete()
        if v is not None:
            db.add(Setting(key=k, group="cases", is_editable=False, value=v))
    db.commit()


def _new_complaint(c: TestClient, w, title: str, **extra) -> Case:
    data = {"case_type": "complaint", "title": f"{TAG} {title}", "complaint_type": "Teacher Punctuality",
            "description": "The parent reported that the teacher was not following the class schedule.",
            "client_id": str(w["family"].id), "student_id": str(w["child"].id), "teacher_id": str(w["teacher"].id),
            "source": "phone", "incident_date": "2026-10-02"}
    data.update(extra)
    r = c.post("/cases/new", data=data, follow_redirects=False)
    assert r.status_code == 303
    s = SessionLocal()
    case = s.query(Case).filter(Case.title == f"{TAG} {title}").order_by(Case.id.desc()).first()
    s.expunge(case)
    s.close()
    return case


def _case(case_id: int) -> Case:
    s = SessionLocal()
    c = s.get(Case, case_id)
    s.expunge(c)
    s.close()
    return c


def _resolve(c: TestClient, case_id: int) -> None:
    c.post(f"/cases/{case_id}/findings", data={"investigation_finding": "Class log confirms the teacher missed the classes on 2 and 4 October.",
                                               "finding_outcome": "substantiated", "severity": "moderate"})
    c.post(f"/cases/{case_id}/resolve", data={"resolution": "Make-up classes booked and the teacher's timetable corrected.",
                                              "root_cause_category": "Scheduling", "corrective_action": "Two make-up classes",
                                              "preventive_action": "Timetable clash check before assigning slots"})


# ------------------------------------------------------------------------------------------------ roles
def test_new_roles_exist_with_named_complaint_history_access(db, world):
    for slug in ("head_of_admissions", "academy_manager"):
        role = db.query(Role).filter(Role.slug == slug).one()
        assert "cases.*" in role.permissions and "complaint_history.view" in role.permissions
    assert rbac.has_explicit_permission(world["ha"], "complaint_history.view")
    auditor = db.query(User).filter(User.email == "auditor@oqc.local").one()
    assert rbac.has_permission(auditor, "complaint_history.view"), "auditor reads everything through *.view"
    assert not rbac.has_explicit_permission(auditor, "complaint_history.view"), "but not confidential complaint history"


# ------------------------------------------------------------------------------------------------ intake
def test_head_of_admissions_opens_a_ticket_against_the_teacher(db, world):
    ha = _login(world["ha"].email, PASSWORD)
    case = _new_complaint(ha, world, "missed classes")
    assert case.case_type == "complaint" and case.case_number.startswith("CS-")
    assert case.against_employee_id == world["employee"].id, "a complaint about a teacher is recorded against their employee record"
    assert case.description.startswith("The parent reported"), "the family's words are stored as received"
    assert case.assigned_to_id != world["teacher"].user_id, "never assigned to the person it is about"
    assert case.incident_date.isoformat() == "2026-10-02"
    am_notes = db.query(Notification).filter(Notification.user_id == world["am"].id, Notification.link == f"/cases/{case.id}").count()
    assert am_notes == 1, "the Academy Manager is told about every complaint"


def test_the_person_complained_about_and_their_supervisor_cannot_see_it(db, world):
    ha = _login(world["ha"].email, PASSWORD)
    case = _new_complaint(ha, world, "hidden from subject")
    teacher_user = db.get(User, world["teacher"].user_id)
    assert not cx.can_view(db, teacher_user, db.get(Case, case.id))
    if world["supervisor"] is not None:
        assert not cx.can_view(db, world["supervisor"], db.get(Case, case.id)), "a named-staff complaint is confidential"
    assert cx.can_view(db, world["am"], db.get(Case, case.id)), "ladder roles see every complaint"
    # the teacher holds requests.view: the Complaints request list must not show it to them either
    t = _login(teacher_user.email, "Teacher@123")
    page = t.get("/requests/complaints").text
    assert case.case_number not in page


def test_repeat_complaint_from_the_same_family_is_linked(world):
    ha = _login(world["ha"].email, PASSWORD)
    first = _new_complaint(ha, world, "repeat one")
    second = _new_complaint(ha, world, "repeat two")
    assert second.repeat_of_id is not None and second.repeat_of_id >= first.id


def test_cannot_assign_a_complaint_to_its_subject(world):
    ha = _login(world["ha"].email, PASSWORD)
    case = _new_complaint(ha, world, "assign guard")
    ha.post(f"/cases/{case.id}/assign", data={"assigned_to_id": str(world["teacher"].user_id), "priority": "high"})
    assert _case(case.id).assigned_to_id != world["teacher"].user_id


# ------------------------------------------------------------------------------------------------ findings and resolution
def test_finding_is_required_and_kept_separate_from_the_complaint(world):
    ha = _login(world["ha"].email, PASSWORD)
    case = _new_complaint(ha, world, "finding first")
    ha.post(f"/cases/{case.id}/resolve", data={"resolution": "Sorted", "root_cause_category": "Scheduling"})
    assert _case(case.id).status not in ("pending_confirmation", "resolved", "closed"), "cannot resolve before recording a finding"
    ha.post(f"/cases/{case.id}/findings", data={"investigation_finding": "Investigation confirmed the teacher missed the scheduled classes.",
                                                "finding_outcome": "substantiated", "severity": "serious"})
    c = _case(case.id)
    assert c.status == "findings_recorded" and c.severity == "serious"
    assert c.investigation_finding.startswith("Investigation confirmed")
    assert c.description.startswith("The parent reported"), "the original complaint is not overwritten"


def test_resolving_waits_for_the_family_and_creates_a_follow_up(db, world):
    ha = _login(world["ha"].email, PASSWORD)
    case = _new_complaint(ha, world, "pending confirmation")
    _resolve(ha, case.id)
    c = _case(case.id)
    assert c.status == "pending_confirmation" and c.confirmation_due_at is not None and c.root_cause_category == "Scheduling"
    follow = db.query(Task).filter(Task.entity_type == "Case", Task.entity_id == case.id, Task.title.like("Confirm with the family%")).all()
    assert len(follow) == 1 and follow[0].status == "todo"
    # the plain status form cannot close it behind the family's back
    ha.post(f"/cases/{case.id}/status", data={"status": "closed", "resolution": "x", "root_cause": "y"})
    assert _case(case.id).status == "pending_confirmation"


def test_family_confirms_in_the_portal_and_the_complaint_closes(db, world):
    ha = _login(world["ha"].email, PASSWORD)
    case = _new_complaint(ha, world, "parent confirms")
    _resolve(ha, case.id)
    parent = _login("parent1@oqc.local", "Parent@123")
    assert "Is this resolved for you?" in parent.get(f"/portal/cases/{case.id}").text
    parent.post(f"/portal/cases/{case.id}/confirm", data={"answer": "yes", "comment": "Yes, the classes are back on time."})
    c = _case(case.id)
    assert c.status == "closed" and c.closure_type == "confirmed_by_parent" and c.confirmed_by_parent
    assert cx.status_label(c) == "Closed, confirmed by parent"
    open_follow = db.query(Task).filter(Task.entity_type == "Case", Task.entity_id == case.id, Task.status == "todo").count()
    assert open_follow == 0, "the confirmation follow-up is completed"


def test_family_not_satisfied_reopens_and_escalates_up_the_ladder(db, world):
    ha = _login(world["ha"].email, PASSWORD)
    case = _new_complaint(ha, world, "parent unhappy")
    _resolve(ha, case.id)
    parent = _login("parent1@oqc.local", "Parent@123")
    parent.post(f"/portal/cases/{case.id}/confirm", data={"answer": "no", "comment": "The teacher was late again yesterday."})
    c = _case(case.id)
    assert c.reopen_count == 1 and c.status == "escalated" and c.escalation_level == 1
    assert db.get(User, c.escalated_to_id).role_slug == "academy_manager", "step 1 of the ladder is the Academy Manager"
    pc = db.query(ParentContact).filter(ParentContact.case_id == case.id).one()
    assert pc.channel == "portal" and pc.satisfaction == "not_satisfied" and pc.outcome == "escalated"


def test_staff_call_records_the_conversation_and_agreed_follow_up(db, world):
    ha = _login(world["ha"].email, PASSWORD)
    case = _new_complaint(ha, world, "call record")
    _resolve(ha, case.id)
    ha.post(f"/cases/{case.id}/contact", data={
        "channel": "phone", "satisfaction": "satisfied", "parent_response": "Happy with the make-up classes.",
        "agreed_action": "Send a weekly progress note for a month", "follow_up_date": "2026-11-01",
        "recording_url": "https://calls.example/rec/123", "transcript": "Parent: thank you..."},
        files={"recording": ("call.mp3", io.BytesIO(b"ID3fake-audio"), "audio/mpeg")})
    c = _case(case.id)
    assert c.status == "closed"
    pc = db.query(ParentContact).filter(ParentContact.case_id == case.id).one()
    assert pc.contacted_by_id == world["ha"].id and pc.recording_url and pc.recording_attachment_id and pc.transcript
    task = db.get(Task, pc.follow_up_task_id)
    assert task is not None and task.due_date.isoformat() == "2026-11-01", "the agreed action becomes a task with reminders"


def test_unreachable_family_can_be_closed_only_after_the_set_attempts(world):
    ha = _login(world["ha"].email, PASSWORD)
    case = _new_complaint(ha, world, "unreachable")
    _resolve(ha, case.id)
    ha.post(f"/cases/{case.id}/close-unconfirmed", data={"reason": "No answer"})
    assert _case(case.id).status == "pending_confirmation", "refused before any attempt"
    for _ in range(cx.unreachable_attempts(SessionLocal())):
        ha.post(f"/cases/{case.id}/contact", data={"channel": "phone", "satisfaction": "unreachable"})
    assert _case(case.id).confirmation_attempts == 3
    ha.post(f"/cases/{case.id}/close-unconfirmed", data={"reason": "Three calls unanswered"})
    c = _case(case.id)
    assert c.status == "closed" and c.closure_type == "parent_unreachable" and not c.confirmed_by_parent


# ------------------------------------------------------------------------------------------------ escalation
def test_deadline_escalation_climbs_the_ladder_one_step_at_a_time(db, world):
    ha = _login(world["ha"].email, PASSWORD)
    case = _new_complaint(ha, world, "ladder")
    c = db.get(Case, case.id)
    cx.escalate(db, c, None, "Deadline missed")
    assert c.escalation_level == 1 and db.get(User, c.escalated_to_id).role_slug == "academy_manager"
    cx.escalate(db, c, None, "Still unresolved")
    assert c.escalation_level == 2 and db.get(User, c.escalated_to_id).role_slug == "head_of_admissions"
    db.commit()
    with pytest.raises(ValueError):
        cx.escalate(db, c, None, "To the teacher?", target=db.get(User, world["teacher"].user_id))
    db.rollback()


# ------------------------------------------------------------------------------------------------ history and privacy
def test_complaint_history_on_the_staff_profile_is_confidential_and_audited(db, world):
    ha = _login(world["ha"].email, PASSWORD)
    case = _new_complaint(ha, world, "history")
    emp = world["employee"]
    hr = _login("hr@oqc.local", "People@123")
    page = hr.get(f"/hr/employees/{emp.id}?tab=complaints")
    assert page.status_code == 200 and case.case_number in page.text and "Confidential" in page.text
    seen = db.query(AuditEvent).filter(AuditEvent.module == "complaint_history", AuditEvent.entity_type == "Employee",
                                       AuditEvent.entity_id == emp.id).count()
    assert seen >= 1, "every read of complaint history is audited"
    auditor = _login("auditor@oqc.local", "Auditor@123")
    assert f"/hr/employees/{emp.id}?tab=complaints" not in auditor.get(f"/hr/employees/{emp.id}").text
    assert auditor.get(f"/hr/employees/{emp.id}?tab=complaints", follow_redirects=False).status_code in (302, 303, 403)
    teacher_tab = hr.get(f"/teachers/{world['teacher'].id}?tab=complaints")
    assert teacher_tab.status_code == 200 and case.case_number in teacher_tab.text


def test_evidence_is_served_only_through_the_case(world):
    ha = _login(world["ha"].email, PASSWORD)
    case = _new_complaint(ha, world, "evidence")
    r = ha.post(f"/cases/{case.id}/evidence", data={"title": "Class log screenshot"},
                files={"file": ("log.png", io.BytesIO(b"\x89PNG\r\n\x1a\nfake"), "image/png")}, follow_redirects=False)
    assert r.status_code == 303
    s = SessionLocal()
    att = s.query(Attachment).filter(Attachment.entity_type == "case", Attachment.entity_id == case.id).one()
    stored = att.file_path
    s.close()
    assert ha.get(f"/cases/{case.id}/evidence/{att.id}").status_code == 200
    assert ha.get(f"/storage/{stored}").status_code == 404, "never through the shared storage route"
    teacher = _login(SessionLocal().get(User, world["teacher"].user_id).email, "Teacher@123")
    assert teacher.get(f"/cases/{case.id}/evidence/{att.id}", follow_redirects=False).status_code in (302, 303, 403, 404)
    bad = ha.post(f"/cases/{case.id}/evidence", data={"title": "script"}, files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/octet-stream")})
    assert bad.status_code == 200
    s = SessionLocal()
    assert s.query(Attachment).filter(Attachment.entity_type == "case", Attachment.entity_id == case.id).count() == 1, "unsafe types refused"
    s.close()


# ------------------------------------------------------------------------------------------------ management
def test_intelligence_and_settings_pages(db, world):
    admin = _login("admin@oqc.local", "Admin@12345")
    page = admin.get("/cases/trends")
    assert page.status_code == 200 and "Summary for management" in page.text and "Needs management attention" in page.text
    r = admin.post("/cases/settings", data={"step_1": "academy_manager", "step_2": "head_of_admissions", "step_3": "hod_people",
                                            "step_4": "super_admin", "days": "3", "attempts": "2", "rationale": "Test"},
                   follow_redirects=False)
    assert r.status_code == 303
    s = SessionLocal()
    assert cx.ladder(s) == ["academy_manager", "head_of_admissions", "hod_people", "super_admin"] and cx.confirmation_days(s) == 3
    s.close()
    assert admin.post("/cases/settings", data={"days": "2", "attempts": "3", "rationale": "Test"}).status_code == 200
    s = SessionLocal()
    assert cx.ladder(s), "an empty ladder is refused"
    s.close()
    # a ladder member holds cases.configure but must not be able to take the steps above them off the ladder
    ha = _login(world["ha"].email, PASSWORD)
    ha.post("/cases/settings", data={"step_1": "head_of_admissions", "days": "2", "attempts": "3", "rationale": "Test"})
    s = SessionLocal()
    assert cx.ladder(s) == ["academy_manager", "head_of_admissions", "hod_people", "super_admin"]
    s.close()


def test_risk_score_explains_itself(db, world):
    ha = _login(world["ha"].email, PASSWORD)
    case = _new_complaint(ha, world, "risk", priority="urgent")
    c = db.get(Case, case.id)
    c.sla_breached, c.reopen_count = True, 1
    score, why = cx.escalation_risk(db, c, datetime.utcnow() + timedelta(minutes=1))
    db.rollback()
    assert score >= 60 and "urgent priority" in why and "response deadline missed" in why


def test_a_reopened_complaint_climbs_further_on_the_next_escalation(db, world):
    ha = _login(world["ha"].email, PASSWORD)
    case = _new_complaint(ha, world, "climb again")
    _resolve(ha, case.id)
    ha.post(f"/cases/{case.id}/contact", data={"channel": "phone", "satisfaction": "not_satisfied",
                                               "parent_response": "Still late."})
    c = db.get(Case, case.id)
    db.refresh(c)
    assert c.escalation_level == 1 and c.escalated
    _resolve(ha, case.id)
    ha.post(f"/cases/{case.id}/contact", data={"channel": "phone", "satisfaction": "partly_satisfied",
                                               "parent_response": "Better, not fixed."})
    db.refresh(c)
    assert c.status == "reopened" and not c.escalated and c.reopen_count == 2
    cx.escalate(db, c, None, "Response deadline missed")
    db.commit()
    assert c.escalation_level == 2 and db.get(User, c.escalated_to_id).role_slug == "head_of_admissions"
