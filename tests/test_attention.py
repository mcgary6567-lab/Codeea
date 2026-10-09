"""The attention layer (docs/ATTENTION.md): explainable findings, one live item per subject, decisions by staff,
self-resolution, referral opportunities, teacher problems and who may see what.

Requested 9 Oct 2026 (item 15): "AI Alert: Student's attendance has declined for two consecutive months, academic
progress is below the expected level, and a parent follow-up is pending" — then recommend or create the action.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.core.utils import next_code
from app.database import SessionLocal
from app.main import app
from app.models.core import AIModelRun, AuditEvent, Notification, Role, Setting, User
from app.models.crm import Feedback
from app.models.ops import AttentionItem, Task
from app.models.people import Client, Student, Teacher
from app.models.scheduling import ClassSession, TeacherMatch
from app.models.academic import TeacherAssignment
from app.services import attention as svc
from app.services import people as people_svc

TAG = "attest" + uuid.uuid4().hex[:6]
PASSWORD = "At!" + uuid.uuid4().hex[:10] + "Aa1"


def _login(email: str, password: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/login", data={"username": email, "password": password}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture(scope="module")
def db():
    s = SessionLocal()
    yield s
    s.close()


def _session(db, student, teacher, day: date, status: str, minute: int = 0) -> ClassSession:
    start = datetime.combine(day, time(18, 0))
    cs = ClassSession(student_id=student.id, teacher_id=teacher.id, date=day, start_time=time(18, 0), end_time=time(18, 30),
                      scheduled_start=start, duration_minutes=30, status=status)
    db.add(cs)
    return cs


@pytest.fixture(scope="module")
def world(db):
    role = db.query(Role).filter(Role.slug == "academy_manager").one()
    am = User(email=f"{TAG}.am@example.com", username=f"{TAG}am", full_name=f"{TAG} Academy Manager",
              hashed_password=hash_password(PASSWORD), role_id=role.id, is_active=True)
    db.add(am)
    teacher = db.get(Teacher, 4)
    fam = Client(client_code=next_code(db, Client, "client_code", "C-"), full_name=f"{TAG} Family", email=f"{TAG}@example.com",
                 phone="+447700900444", country="United Kingdom", currency="GBP", status="active",
                 joined_at=date.today() - timedelta(days=120))
    db.add(fam)
    db.flush()
    st = Student(student_code=next_code(db, Student, "student_code", "S-"), client_id=fam.id, full_name=f"{TAG} Child", age=9,
                 status="active", join_date=date.today() - timedelta(days=120))
    db.add(st)
    db.flush()
    people_svc.assign_teacher(db, st, teacher, None, reason="Initial match")
    today = svc.today()
    # attendance falls across three 30-day windows: 10/10, 8/10, 5/10
    for offset, absences in ((75, 0), (45, 2), (15, 5)):
        for k in range(10):
            _session(db, st, teacher, today - timedelta(days=offset - k), "absent" if k < absences else "done")
    db.commit()
    yield {"am": am, "family": fam, "student": st, "teacher": teacher}
    db.rollback()
    db.query(Task).filter(Task.entity_type.in_(["Student", "Client"]), Task.entity_id.in_([st.id, fam.id])).delete(synchronize_session=False)
    db.query(AttentionItem).filter(((AttentionItem.subject_type == "student") & (AttentionItem.subject_id == st.id)) |
                                   ((AttentionItem.subject_type == "family") & (AttentionItem.subject_id == fam.id))).delete(synchronize_session=False)
    db.query(Feedback).filter(Feedback.client_id == fam.id).delete(synchronize_session=False)
    db.query(ClassSession).filter(ClassSession.student_id == st.id).delete(synchronize_session=False)
    db.query(TeacherAssignment).filter(TeacherAssignment.student_id == st.id).delete(synchronize_session=False)
    db.query(TeacherMatch).filter(TeacherMatch.student_id == st.id).delete(synchronize_session=False)
    db.query(AuditEvent).filter(AuditEvent.entity_type == "Student", AuditEvent.entity_id == st.id).delete(synchronize_session=False)
    db.query(Notification).filter(Notification.user_id == am.id).delete(synchronize_session=False)
    db.query(Student).filter(Student.id == st.id).delete(synchronize_session=False)
    db.query(Client).filter(Client.id == fam.id).delete(synchronize_session=False)
    db.query(User).filter(User.id == am.id).delete(synchronize_session=False)
    db.commit()


def _item(db, world) -> AttentionItem:
    return svc.check_student(db, db.get(Student, world["student"].id))


def test_findings_are_explainable_and_summarised(db, world):
    item = _item(db, world)
    db.commit()
    keys = {s["key"] for s in item.signals}
    assert {"attendance", "absences"} <= keys
    att = next(s for s in item.signals if s["key"] == "attendance")
    assert att["label"] == "Attendance has declined for two consecutive months" and att["detail"] == "100% → 80% → 50%"
    assert item.summary.startswith("Attendance has declined for two consecutive months (100% → 80% → 50%)")
    assert item.summary_source == "rules", "no AI provider in the test environment: put together from the findings"
    assert item.level in ("medium", "high") and item.status == "open"
    assert any(a["key"] == "call_attendance" and a["owner"] == "academy_manager" for a in item.recommended)


def test_one_live_item_per_student(db, world):
    first = _item(db, world)
    again = _item(db, world)
    db.commit()
    assert first.id == again.id
    assert db.query(AttentionItem).filter(AttentionItem.subject_type == "student", AttentionItem.subject_id == world["student"].id,
                                          AttentionItem.status.in_(svc.LIVE)).count() == 1


def test_staff_approve_the_actions_and_tasks_are_assigned(db, world):
    item = _item(db, world)
    db.commit()
    am = _login(world["am"].email, PASSWORD)
    r = am.post(f"/attention/{item.id}/approve", data={"actions": ["call_attendance"], "note": f"{TAG} please call today"},
                follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    item = db.get(AttentionItem, item.id)
    assert item.status == "actioned" and item.decided_by_id == world["am"].id and len(item.task_ids) == 1
    task = db.get(Task, item.task_ids[0])
    assert task.title.startswith("Call the family about attendance") and task.priority == "high"
    assert task.assignee_id is not None and task.due_date == svc.today() + timedelta(days=2)
    # the same findings again: it stays actioned (no duplicate work)
    assert _item(db, world).status == "actioned"


def test_something_new_reopens_an_actioned_item(db, world):
    st = db.get(Student, world["student"].id)
    db.add(Task(title=f"{TAG} overdue follow-up", status="todo", due_date=svc.today() - timedelta(days=3), entity_type="Student",
                entity_id=st.id, priority="medium"))
    db.flush()
    item = svc.check_student(db, st)
    db.commit()
    assert item.status == "open" and "follow_ups" in {s["key"] for s in item.signals}
    assert "follow-ups are overdue" in item.summary


def test_dismiss_needs_a_reason_and_is_remembered(db, world):
    item = _item(db, world)
    db.commit()
    with pytest.raises(ValueError):
        svc.dismiss(db, item, world["am"], "")
    svc.dismiss(db, item, world["am"], "Family travelling; already spoken to")
    db.commit()
    assert _item(db, world) is None, "the same findings do not come back once a person has decided"


def test_items_resolve_themselves_when_the_signals_clear(db, world):
    st = db.get(Student, world["student"].id)
    db.query(AttentionItem).filter(AttentionItem.subject_type == "student", AttentionItem.subject_id == st.id).delete()
    item = svc.check_student(db, st)
    db.commit()
    assert item is not None
    db.query(ClassSession).filter(ClassSession.student_id == st.id, ClassSession.status == "absent").update({ClassSession.status: "done"})
    db.query(Task).filter(Task.entity_type == "Student", Task.entity_id == st.id).update({Task.status: "done"})
    db.flush()
    assert svc.check_student(db, st) is None
    db.commit()
    db.refresh(item)
    assert item.status == "resolved" and item.resolved_at is not None


def test_snooze(db, world):
    st = db.get(Student, world["student"].id)
    for k, cs in enumerate(db.query(ClassSession).filter(ClassSession.student_id == st.id, ClassSession.date >= svc.today() - timedelta(days=29))):
        if k < 5:
            cs.status = "absent"
    db.flush()
    item = svc.check_student(db, st)
    svc.snooze(db, item, world["am"], 7)
    db.commit()
    assert item.status == "snoozed" and item.snoozed_until == svc.today() + timedelta(days=7)


# ------------------------------------------------------------------------------------------------ families and teachers
def test_a_happy_settled_family_is_a_referral_opportunity(db, world):
    fam = db.get(Client, world["family"].id)
    assert svc.check_family(db, fam) is None
    db.add(Feedback(client_id=fam.id, respondent_type="client", nps=10, submitted_at=datetime.utcnow(), status="submitted"))
    db.flush()
    item = svc.check_family(db, fam)
    db.commit()
    assert item.kind == "opportunity" and item.signals[0]["key"] == "referral"
    assert item.recommended[0]["key"] == "ambassador_invite" and item.recommended[0]["owner"] == "head_of_admissions"


def test_recurring_teacher_problems_are_found(db, world):
    st, teacher = db.get(Student, world["student"].id), world["teacher"]
    for k in range(3):
        _session(db, st, teacher, svc.today() - timedelta(days=k + 1), "missed")
    db.flush()
    sigs = {s["key"] for s in svc.teacher_signals(db, teacher)}
    db.rollback()
    assert "teacher_missed" in sigs


# ------------------------------------------------------------------------------------------------ access
def test_who_can_see_and_decide(db, world):
    am = _login(world["am"].email, PASSWORD)
    page = am.get("/attention")
    assert page.status_code == 200 and "Needs attention" in page.text
    billing = _login("billing@oqc.local", "Billing@123")
    assert billing.get("/attention", follow_redirects=False).status_code in (302, 303, 403)
    sup = db.query(User).filter(User.email == "supervisor@oqc.local").one()
    visible = svc.visible(db, sup, db.query(AttentionItem)).all()
    mine = {t.id for t in db.query(Teacher).filter(Teacher.supervisor_id == sup.id)}
    for i in visible:
        if i.subject_type == "teacher":
            assert i.subject_id in mine
        else:
            assert i.subject_type == "student" and db.get(Student, i.subject_id).teacher_id in mine


def test_the_daily_job_runs_once_a_day(db, world):
    from app.services import jobs_attention
    db.query(Setting).filter(Setting.key == jobs_attention.MARKER_KEY).delete()
    db.commit()
    first = jobs_attention.attention_check(db)
    db.commit()
    assert first.get("students", 0) >= 1
    assert jobs_attention.attention_check(db).get("skipped") == "already checked today"


def test_the_summary_follows_the_evidence(db, world):
    st = db.get(Student, world["student"].id)
    db.query(AttentionItem).filter(AttentionItem.subject_type == "student", AttentionItem.subject_id == st.id).delete()
    item = svc.check_student(db, st)
    before = item.summary
    extra = next(cs for cs in db.query(ClassSession).filter(ClassSession.student_id == st.id, ClassSession.status == "done",
                                                            ClassSession.date >= svc.today() - timedelta(days=29)))
    extra.status = "absent"
    db.flush()
    item = svc.check_student(db, st)
    db.commit()
    assert item.summary != before and "6 absences" in item.summary
