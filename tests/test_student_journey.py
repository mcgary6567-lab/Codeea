"""Student academic journey (docs/STUDENT_JOURNEY.md): teacher and class-time history that is never overwritten, the
timeline with its period selector, evidence-based assessment, recommendations that create their own follow-up, and the
Academy Manager's monthly summary.

Requested 9 Oct 2026 (items 6-11 of the integrated ERP brief).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.core.utils import next_code
from app.database import SessionLocal
from app.main import app
from app.models.academic import AssessmentAnswer, Evaluation, StudentRecommendation, TeacherAssignment
from app.models.core import AuditEvent, Notification, RiskAlert, Role, Setting, User
from app.models.ops import Task
from app.models.people import Client, Student, Teacher
from app.models.scheduling import ClassSession, TeacherMatch
from app.services import journey
from app.services import people as people_svc

TAG = "jrtest" + uuid.uuid4().hex[:6]
PASSWORD = "Jr!" + uuid.uuid4().hex[:10] + "Aa1"


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
    """A fresh family taught by teacher 1, an Academy Manager to receive follow-ups, and the two seeded teachers."""
    t1, t2 = db.get(Teacher, 1), db.get(Teacher, 2)
    role = db.query(Role).filter(Role.slug == "academy_manager").one()
    am = User(email=f"{TAG}.am@example.com", username=f"{TAG}am", full_name=f"{TAG} Academy Manager",
              hashed_password=hash_password(PASSWORD), role_id=role.id, is_active=True)
    db.add(am)
    client = Client(client_code=next_code(db, Client, "client_code", "C-"), full_name=f"{TAG} Family", email=f"{TAG}@example.com",
                    phone="+447700900123", whatsapp="+447700900123", country="United Kingdom", currency="GBP", status="active")
    db.add(client)
    db.flush()
    student = Student(student_code=next_code(db, Student, "student_code", "S-"), client_id=client.id, full_name=f"{TAG} Child",
                      age=10, status="active", course_id=t1.students[0].course_id if t1.students else None,
                      join_date=journey.org_today() - timedelta(days=120))
    db.add(student)
    db.flush()
    people_svc.assign_teacher(db, student, t1, None, reason="Initial match")
    db.commit()
    yield {"student": student, "t1": t1, "t2": t2, "am": am, "client": client}
    db.rollback()
    sid = student.id
    rec_tasks = [r.task_id for r in db.query(StudentRecommendation).filter(StudentRecommendation.student_id == sid) if r.task_id]
    db.query(StudentRecommendation).filter(StudentRecommendation.student_id == sid).delete(synchronize_session=False)
    db.query(Task).filter(Task.id.in_(rec_tasks or [-1])).delete(synchronize_session=False)
    db.query(Task).filter(Task.entity_type == "Student", Task.entity_id == sid).delete(synchronize_session=False)
    db.query(RiskAlert).filter(RiskAlert.entity_type == "Student", RiskAlert.entity_id == sid).delete(synchronize_session=False)
    ev_ids = [e.id for e in db.query(Evaluation).filter(Evaluation.student_id == sid)]
    db.query(AssessmentAnswer).filter(AssessmentAnswer.evaluation_id.in_(ev_ids or [-1])).delete(synchronize_session=False)
    db.query(Evaluation).filter(Evaluation.id.in_(ev_ids or [-1])).delete(synchronize_session=False)
    db.query(TeacherAssignment).filter(TeacherAssignment.student_id == sid).delete(synchronize_session=False)
    db.query(TeacherMatch).filter(TeacherMatch.student_id == sid).delete(synchronize_session=False)
    db.query(ClassSession).filter(ClassSession.student_id == sid).delete(synchronize_session=False)
    db.query(AuditEvent).filter(AuditEvent.entity_type == "Student", AuditEvent.entity_id == sid).delete(synchronize_session=False)
    db.query(Notification).filter(Notification.user_id == am.id).delete(synchronize_session=False)
    db.query(Notification).filter(Notification.link.like(f"/students/{sid}/%")).delete(synchronize_session=False)
    db.query(Student).filter(Student.id == sid).delete(synchronize_session=False)
    db.query(Client).filter(Client.id == client.id).delete(synchronize_session=False)
    db.query(User).filter(User.id == am.id).delete(synchronize_session=False)
    db.commit()


# ------------------------------------------------------------------------------------------------ teacher history
def test_a_teacher_change_keeps_the_previous_teacher_time_and_reason(db, world):
    s = world["student"]
    rows = journey.history(db, s)
    assert len(rows) == 1 and rows[0].teacher_id == world["t1"].id and rows[0].end_date is None
    admin = _login("admin@oqc.local", "Admin@12345")
    r = admin.post(f"/students/{s.id}/change-teacher", data={"teacher_id": str(world["t2"].id), "reason_category": "timing_conflict",
                                                             "reason": "Parent asked for a later class."}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    rows = journey.history(db, db.get(Student, s.id))
    assert len(rows) == 2, "the earlier period is kept, not overwritten"
    new, old = rows[0], rows[1]
    assert new.teacher_id == world["t2"].id and new.previous_teacher_id == world["t1"].id and new.end_date is None
    assert new.reason_category == "timing_conflict" and new.reason == "Parent asked for a later class."
    assert old.end_date is not None and old.teacher_id == world["t1"].id


def test_a_time_change_is_its_own_history_row(db, world):
    s = db.get(Student, world["student"].id)
    open_row = journey.open_assignment(db, s.id)
    open_row.class_time, open_row.days = "18:00", [0, 2]
    db.flush()
    row = journey.record_assignment(db, s, s.teacher_id, None, "time_change", reason_category="schedule_change",
                                    reason="Moved to suit school hours", class_time="19:00", days=[0, 2])
    db.commit()
    assert row.change_type == "time_change" and row.previous_time == "18:00" and row.class_time == "19:00"
    assert journey.record_assignment(db, s, s.teacher_id, None, "time_change", class_time="19:00", days=[0, 2]) is None, \
        "nothing is recorded when nothing changed"
    db.rollback()


def test_reconstruction_ends_with_the_previous_teacher_when_the_caller_already_overwrote_it(db, world):
    s = db.get(Student, world["student"].id)
    db.query(TeacherAssignment).filter(TeacherAssignment.student_id == s.id).delete()
    before = s.teacher_id
    other = world["t1"].id if before == world["t2"].id else world["t2"].id
    s.teacher_id = other  # the request-approval path writes the new teacher first
    journey.record_assignment(db, s, other, None, "teacher_change", reason_category="parent_request", previous_teacher_id=before)
    rows = journey.history(db, s)
    assert rows[0].teacher_id == other and rows[0].previous_teacher_id == before and rows[0].reason_category == "parent_request"
    db.rollback()


# ------------------------------------------------------------------------------------------------ timeline and periods
def test_timeline_and_period_selector(db, world):
    s = world["student"]
    admin = _login("admin@oqc.local", "Admin@12345")
    for q in ["", "?period=previous", "?period=all", "?period=range&date_from=2026-08-01&date_to=2026-10-31",
              "?period=month&month=2026-09", "?view=summary", "?view=teachers", "?view=syllabus&period=all", "?view=assessments&period=all"]:
        r = admin.get(f"/students/{s.id}/journey{q}")
        assert r.status_code == 200, q
    page = admin.get(f"/students/{s.id}/journey?period=all&kinds=teacher").text
    assert "Teacher changed" in page or "Teacher assigned" in page
    win = journey.resolve_period(db, db.get(Student, s.id), "all")
    assert win["start"] <= db.get(Student, s.id).join_date


def test_a_teacher_sees_only_their_own_students(db, world):
    t3 = db.get(Teacher, 3)
    other = _login(t3.user.email, "Teacher@123")
    assert other.get(f"/students/{world['student'].id}/journey", follow_redirects=False).status_code in (302, 303, 403)
    own = db.query(Student).filter(Student.teacher_id == t3.id).first()
    if own is not None:
        assert other.get(f"/students/{own.id}/journey").status_code == 200


# ------------------------------------------------------------------------------------------------ evidence
def test_assessment_stores_question_answer_result_and_creates_follow_ups(db, world):
    s = world["student"]
    admin = _login("admin@oqc.local", "Admin@12345")
    r = admin.post("/academics/evaluations/new", data={
        "student_id": str(s.id), "evaluation_type": "monthly", "date": journey.org_today().isoformat(),
        "teacher_comment": f"{TAG} reads fluently but rushes the madd", "stage": "Nazra, Juz 2",
        "strengths": "Clear makharij", "weaknesses": "Madd length", "recommendations": "Practise madd daily",
        "parent_observations": "Parent asks for weekly updates", "follow_up": "Re-test madd in two weeks",
        "q_id": ["", "", ""], "q_text": ["Recite Al-Fatiha", "What is madd tabee'i?", "Read page 12, line 3"],
        "q_expected": ["Correct recitation", "Two counts", ""], "q_answer": ["Recited well", "Four counts", "Skipped"],
        "q_result": ["correct", "incorrect", "not_attempted"], "q_marks": ["5", "0", "0"], "q_max": ["5", "5", "5"],
        "rec_kind": ["attendance", "needs_revision"]}, follow_redirects=False)
    assert r.status_code == 303
    ev = db.query(Evaluation).filter(Evaluation.teacher_comment.like(f"{TAG}%")).one()
    assert [a.result for a in ev.answers] == ["correct", "incorrect", "not_attempted"]
    assert ev.answers[1].student_answer == "Four counts" and ev.answers[1].expected_answer == "Two counts"
    assert round(ev.score, 1) == 33.3, "the score comes from the marks: 5 of 15"
    assert ev.stage == "Nazra, Juz 2" and ev.weaknesses == "Madd length" and ev.follow_up
    recs = db.query(StudentRecommendation).filter(StudentRecommendation.evaluation_id == ev.id).all()
    assert {r.kind for r in recs} == {"attendance", "needs_revision"}
    att = next(r for r in recs if r.kind == "attendance")
    assert att.task is not None and db.get(User, att.task.assignee_id).role_slug == "academy_manager"
    assert att.alert_id is not None, "an attendance concern is urgent: it raises an alert"
    rev = next(r for r in recs if r.kind == "needs_revision")
    teacher_user = db.get(Student, s.id).teacher.user_id
    assert rev.task.assignee_id == teacher_user, "revision goes to the student's teacher"
    detail = admin.get(f"/academics/evaluations/{ev.id}").text
    assert "Four counts" in detail and "What was assessed" in detail


def test_the_same_concern_raised_again_is_escalated(db, world):
    s = world["student"]
    admin = _login("admin@oqc.local", "Admin@12345")
    admin.post(f"/students/{s.id}/recommendations", data={"kind": "late_arrivals", "note": f"{TAG} late twice"})
    admin.post(f"/students/{s.id}/recommendations", data={"kind": "late_arrivals", "note": f"{TAG} late again"})
    recs = (db.query(StudentRecommendation).filter(StudentRecommendation.student_id == s.id, StudentRecommendation.kind == "late_arrivals")
            .order_by(StudentRecommendation.id).all())
    assert len(recs) == 2 and recs[0].action == "task" and recs[1].action == "escalation"
    alert = db.get(RiskAlert, recs[1].alert_id)
    assert alert.alert_type == "student_recommendation_repeat" and alert.visibility == "management"


def test_progressing_well_is_recorded_without_creating_work(db, world):
    s = world["student"]
    rec = journey.recommend(db, db.get(Student, s.id), "progressing_well", "Excellent month", None)
    assert rec.task_id is None and rec.action == "none"
    db.rollback()


# ------------------------------------------------------------------------------------------------ monthly summary
def test_monthly_summary_and_board(db, world):
    s = world["student"]
    admin = _login("admin@oqc.local", "Admin@12345")
    month = journey.org_today().strftime("%Y-%m")
    page = admin.get(f"/students/{s.id}/journey?view=summary&period=month&month={month}").text
    for heading in ("Attendance and classes", "Syllabus", "Assessments", "Teacher observations", "Teacher recommendations",
                    "Parent concerns", "Outstanding actions"):
        assert heading in page, heading
    assert "Open teacher recommendation" in page
    board = admin.get(f"/academics/student-summary?month={month}&q={TAG}")
    assert board.status_code == 200 and f"{TAG} Child" in board.text and "Open teacher recommendation" in board.text


def test_monthly_digest_is_sent_once(db, world):
    from app.services import jobs_academic
    marker = db.query(Setting).filter(Setting.key == jobs_academic.SUMMARY_MARKER_KEY).first()
    saved = dict(marker.value) if marker and isinstance(marker.value, dict) else None
    if marker:
        db.delete(marker)
        db.commit()
    try:
        first_of_month = journey.org_today().replace(day=1)
        out = jobs_academic.monthly_student_summary_digest(db, today=first_of_month)
        db.commit()
        assert out.get("notified", 0) >= 1 and "flagged" in out
        assert jobs_academic.monthly_student_summary_digest(db, today=first_of_month).get("skipped") == "already sent"
        assert jobs_academic.monthly_student_summary_digest(db, today=first_of_month + timedelta(days=10)).get("skipped")
    finally:
        db.query(Setting).filter(Setting.key == jobs_academic.SUMMARY_MARKER_KEY).delete()
        if saved is not None:
            db.add(Setting(key=jobs_academic.SUMMARY_MARKER_KEY, group="jobs", value=saved))
        db.query(Notification).filter(Notification.event_type == "digest",
                                      Notification.created_at >= datetime.utcnow() - timedelta(minutes=10)).delete(synchronize_session=False)
        db.commit()


def test_teacher_portal_assessment_records_evidence(db, world):
    s = db.get(Student, world["student"].id)
    teacher = s.teacher
    c = _login(teacher.user.email, "Teacher@123")
    page = c.get("/teacher/evaluations").text
    assert "Questions asked and the student" in page
    r = c.post("/teacher/evaluations", data={
        "student_id": str(s.id), "evaluation_type": "weekly", "date": journey.org_today().isoformat(),
        "teacher_comment": f"{TAG} portal check", "q_text": ["Name the letters of the throat"], "q_answer": ["Ayn, Ha"],
        "q_result": ["partly_correct"], "q_marks": ["3"], "q_max": ["6"], "q_id": [""], "q_expected": ["Six letters"],
        "rec_kind": ["more_practice"]}, follow_redirects=False)
    assert r.status_code == 303
    ev = db.query(Evaluation).filter(Evaluation.teacher_comment == f"{TAG} portal check").one()
    assert ev.answers[0].result == "partly_correct" and ev.score == 50.0
    rec = db.query(StudentRecommendation).filter(StudentRecommendation.evaluation_id == ev.id).one()
    assert rec.kind == "more_practice" and rec.task.assignee_id == teacher.user_id
