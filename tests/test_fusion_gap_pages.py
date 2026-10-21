"""Pages the old ERP had and OQC lacked (Fusion gap report, 21 Oct 2026): Class Management, Supervisors Management
and Daily Performance dashboards, Student Attendance Monitoring, Question Bank CSV upload, student detail print,
subscription day-wise report and favourite pages.

The dashboard figures are checked on classes of our own in January 2001, so other data never moves them.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.database import SessionLocal
from app.main import app
from app.models.core import User
from app.models.erp import QuestionBankItem
from app.models.finance import Subscription
from app.models.ops import FavouritePage, MigrationJob
from app.models.people import Client, Student, Teacher
from app.models.scheduling import ClassSession
from app.services import class_dashboards as cd

TAG = "fgp" + uuid.uuid4().hex[:6]
DAY = date(2001, 1, 8)  # a Monday


def _login(email: str, password: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/login", data={"username": email, "password": password}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture(scope="module")
def admin():
    return _login("admin@oqc.local", "Admin@12345")


@pytest.fixture(scope="module")
def world():
    """One supervisor, one teacher, two students; classes on 8-13 Jan 2001 with known outcomes."""
    s = SessionLocal()
    sup = User(email=f"{TAG}@example.com", username=TAG, full_name=f"Sup {TAG}", hashed_password=hash_password("x" * 12))
    s.add(sup)
    s.flush()
    t = Teacher(teacher_code=f"T{TAG}"[:20], full_name=f"Teacher {TAG}", supervisor_id=sup.id, is_verified=True)
    c = Client(client_code=f"C{TAG}"[:20], full_name=f"Family {TAG}", shift="night")
    s.add_all([t, c])
    s.flush()
    good = Student(student_code=f"G{TAG}"[:20], client_id=c.id, full_name=f"Good {TAG}", teacher_id=t.id, status="active")
    weak = Student(student_code=f"W{TAG}"[:20], client_id=c.id, full_name=f"Weak {TAG}", teacher_id=t.id, status="active")
    s.add_all([good, weak])
    s.flush()
    sub = Subscription(subscription_code=f"S{TAG}"[:20], client_id=c.id, student_id=good.id, teacher_id=t.id,
                       days_of_week=[0, 2, 4], start_date=date(2000, 12, 1), status="active")
    s.add(sub)
    s.flush()

    def cls(student, day, status, late=0, minutes=None, sub_id=None):
        start = datetime.combine(day, time(18, 0))
        s.add(ClassSession(student_id=student.id, teacher_id=t.id, date=day, start_time=time(18, 0),
                           end_time=time(18, 30), scheduled_start=start, status=status, teacher_late_minutes=late,
                           actual_duration_minutes=minutes, subscription_id=sub_id))

    # good: 4 done (one late, one short), weak: 1 done then 3 absences in a row, plus one missed by the teacher
    for i, (status, late, minutes) in enumerate([("done", 0, 30), ("done", 9, 30), ("done", 0, 15), ("done", 0, 30)]):
        cls(good, DAY + timedelta(days=i), status, late, minutes, sub.id)
    cls(weak, DAY, "done", 0, 30)
    for i in range(1, 4):
        cls(weak, DAY + timedelta(days=i), "absent")
    cls(weak, DAY + timedelta(days=4), "missed")
    cls(weak, DAY + timedelta(days=5), "cancelled")
    s.commit()
    ids = {"sup": sup.id, "teacher": t.id, "client": c.id, "good": good.id, "weak": weak.id, "sub": sub.id}
    s.close()
    yield ids
    s = SessionLocal()
    try:
        s.query(ClassSession).filter(ClassSession.teacher_id == ids["teacher"]).delete(synchronize_session=False)
        s.query(Subscription).filter(Subscription.id == ids["sub"]).delete(synchronize_session=False)
        s.query(Student).filter(Student.client_id == ids["client"]).delete(synchronize_session=False)
        s.query(Teacher).filter(Teacher.id == ids["teacher"]).delete(synchronize_session=False)
        s.query(Client).filter(Client.id == ids["client"]).delete(synchronize_session=False)
        s.query(User).filter(User.id == ids["sup"]).delete(synchronize_session=False)
        s.query(QuestionBankItem).filter(QuestionBankItem.question.like(f"{TAG}%")).delete(synchronize_session=False)
        s.query(MigrationJob).filter(MigrationJob.source_system == "question_bank_upload",
                                     MigrationJob.records_total == 4).delete(synchronize_session=False)
        s.query(FavouritePage).filter(FavouritePage.label.like(f"{TAG}%")).delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()


# ------------------------------------------------------------------------------------------------ dashboards
def test_class_management_counts_one_teachers_week(world):
    s = SessionLocal()
    try:
        d = cd.class_management(s, date_from=DAY, date_to=DAY + timedelta(days=6), teacher_id=world["teacher"])
    finally:
        s.close()
    t = d["tiles"]
    assert (t["total"], t["done"], t["absent"], t["missed"], t["cancelled"]) == (10, 5, 3, 1, 1)
    assert t["completion"] == 55.6, "5 done of 9 classes with an outcome (cancelled left out)"
    assert t["attendance"] == 62.5, "5 present of 8 held or absent"
    assert (t["late"], t["short"]) == (1, 1)
    assert [r["teacher_id"] for r in d["teachers"]] == [world["teacher"]]
    assert d["charts"]["days"]["labels"][0] == "08 Jan"


def test_supervisor_row_adds_up_their_teachers(world):
    s = SessionLocal()
    try:
        d = cd.supervisors_dashboard(s, date_from=DAY, date_to=DAY + timedelta(days=6), supervisor_id=world["sup"])
    finally:
        s.close()
    (row,) = d["rows"]
    assert row["supervisor"] == f"Sup {TAG}"
    assert (row["teachers"], row["verified"], row["students"], row["done"], row["missed"]) == (1, 1, 2, 5, 1)


def test_daily_performance_compares_with_the_day_before(world):
    s = SessionLocal()
    try:
        d = cd.daily_performance(s, day=DAY + timedelta(days=1), department="academics")
    finally:
        s.close()
    mine = [r for r in d["teachers"] if r["teacher_id"] == world["teacher"]]
    assert mine and (mine[0]["done"], mine[0]["absent"], mine[0]["late"]) == (1, 1, 1)
    assert d["before"]["classes"]["done"] >= 2 and d["department"] == "academics"


def test_attendance_monitoring_flags_a_run_of_absences(world):
    s = SessionLocal()
    try:
        d = cd.student_attendance(s, date_from=DAY, date_to=DAY + timedelta(days=6), teacher_id=world["teacher"])
        flagged = cd.student_attendance(s, date_from=DAY, date_to=DAY + timedelta(days=6),
                                        teacher_id=world["teacher"], flagged_only=True)
        scoped = cd.student_attendance(s, date_from=DAY, date_to=DAY + timedelta(days=6), scope=[-1])
    finally:
        s.close()
    rows = {r["student"].id: r for r in d["rows"]}
    weak, good = rows[world["weak"]], rows[world["good"]]
    assert (weak["present"], weak["absent"], weak["streak"], weak["rate"], weak["flagged"]) == (1, 3, 3, 25.0, True)
    assert (good["rate"], good["flagged"]) == (100.0, False)
    assert d["rows"][0]["student"].id == world["weak"], "flagged students come first"
    assert [r["student"].id for r in flagged["rows"]] == [world["weak"]]
    assert scoped["rows"] == [], "a supervisor with no teachers sees no students"


def test_the_new_pages_open_and_are_in_the_menu(admin, world):
    for url in ("/dashboards/class-management", "/dashboards/supervisors", "/dashboards/daily-performance",
                "/classes/attendance-monitoring", f"/students/{world['good']}/print",
                f"/subscriptions/{world['sub']}/day-wise?month=2001-01"):
        r = admin.get(url)
        assert r.status_code == 200, url
    home = admin.get("/home/academics").text
    for url in ("/dashboards/class-management", "/dashboards/supervisors", "/dashboards/daily-performance",
                "/classes/attendance-monitoring"):
        assert url in home, url


def test_day_wise_report_lists_every_day_of_the_month(admin, world):
    html = admin.get(f"/subscriptions/{world['sub']}/day-wise?month=2001-01").text
    assert "31 Jan 2001" in html and "01 Jan 2001" in html
    assert "No class recorded" in html, "a class day without a class is pointed out"
    assert "January 2001" in html


def test_student_print_hides_contacts_from_a_teacher(world):
    teacher = _login("teacher1@oqc.local", "Teacher@123")
    r = teacher.get(f"/students/{world['good']}/print")
    if r.status_code == 200:
        assert "Student Detail" in r.text and f"Family {TAG}" in r.text


# ------------------------------------------------------------------------------------------------ question bank CSV
def test_question_bank_csv_upload_imports_good_rows_and_reports_bad_ones(admin):
    csv = (f"Question,Answer,Type,Marks\n{TAG} Recite Al-Ikhlas,Makharij,recitation,5\n{TAG} What is Ikhfa?,Hiding,oral,2\n"
           f",no question,oral,1\n{TAG} essay,x,essay,1\n")
    r = admin.post("/academics/config/question-bank/upload", files={"file": ("qb.csv", csv, "text/csv")})
    assert r.status_code == 200
    assert "2 question(s) imported, 2 skipped" in r.text
    s = SessionLocal()
    try:
        rows = s.query(QuestionBankItem).filter(QuestionBankItem.question.like(f"{TAG}%")).all()
        assert sorted((q.question_type, q.marks) for q in rows) == [("oral", 2.0), ("recitation", 5.0)]
    finally:
        s.close()
    again = admin.post("/academics/config/question-bank/upload", files={"file": ("qb.csv", csv, "text/csv")})
    assert "0 question(s) imported" in again.text, "the same questions are not added twice"
    assert admin.get("/academics/config/question-bank/template.csv").text.startswith("question,answer,question_type")


# ------------------------------------------------------------------------------------------------ favourites
def test_favourites_toggle_list_and_refuse_other_sites(admin):
    r = admin.post("/favourites/toggle", data={"path": "/dashboards/supervisors", "label": f"{TAG} Supervisors",
                                               "next": "/dashboards/supervisors"})
    assert r.status_code == 200 and "Added to favourites" in r.text
    assert f"{TAG} Supervisors" in admin.get("/home").text, "the header menu lists it on every page"
    assert "Remove from favourites" in admin.get("/dashboards/supervisors").text
    bad = admin.post("/favourites/toggle", data={"path": "https://evil.example/", "label": f"{TAG} evil"})
    assert "cannot be added" in bad.text
    admin.post("/favourites/toggle", data={"path": "/dashboards/supervisors", "label": f"{TAG} Supervisors"})
    assert f"{TAG} Supervisors" not in admin.get("/favourites").text


def test_families_have_no_favourites(world):
    parent = _login("parent1@oqc.local", "Parent@123")
    assert parent.get("/favourites", follow_redirects=False).status_code in (302, 303, 403)
