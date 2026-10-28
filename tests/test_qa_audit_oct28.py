"""Fixes for the customer's Final Consolidated QA & Audit Report (28 October 2026), one block per finding.

Each test creates what it needs under a unique tag and removes it again, so the module runs against the shared
development database any number of times.
"""
from __future__ import annotations

import pathlib
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app

TAG = f"qa28-{uuid4().hex[:6]}"


@pytest.fixture(scope="module")
def admin():
    c = TestClient(app)
    assert c.post("/login", data={"username": "admin@oqc.local", "password": "Admin@12345"}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# --------------------------------------------------------------------------- OQC-09 / OQC-10 / OQC-11 Curriculum Builder
def test_a_new_book_shows_in_the_list_before_it_has_a_chapter(admin, db):
    from app.models.academic import Book, Course
    course = db.query(Course).order_by(Course.order, Course.id).first()
    title = f"Book {TAG}"
    try:
        r = admin.post("/academics/curriculum/books", follow_redirects=False,
                       data={"course_id": str(course.id), "title": title})
        assert r.status_code == 303
        html = admin.get(f"/academics/curriculum?course_id={course.id}").text
        assert title in html, "the book the success message announced is missing from the list"
        books = db.query(Book).filter(Book.course_id == course.id).count()
        assert len(re.findall(r'action="/academics/curriculum/books/\d+/edit"', html)) == books, \
            "the Books counter and the list disagree"
    finally:
        db.query(Book).filter(Book.title == title).delete()
        db.commit()


def test_a_search_still_hides_books_without_a_match(admin, db):
    from app.models.academic import Book, Course
    course = db.query(Course).order_by(Course.order, Course.id).first()
    title = f"Book {TAG} search"
    db.add(Book(course_id=course.id, title=title))
    db.commit()
    try:
        html = admin.get(f"/academics/curriculum?course_id={course.id}&q=zzz-no-lesson-{TAG}").text
        assert title not in html
    finally:
        db.query(Book).filter(Book.title == title).delete()
        db.commit()


def test_form_fields_never_take_their_class_from_attrs():
    """A class inside attrs came before the macro's own and the browser kept it, dropping w-full and the border:
    the New Book dialog scrolled sideways. Extra classes go through cls= instead."""
    pat = re.compile(r"ui\.(input|textarea|select)\((?:[^()]|\([^()]*\))*?attrs=(['\"])(.*?)\2", re.S)
    offenders = []
    for p in pathlib.Path("app/templates").rglob("*.html"):
        s = p.read_text(encoding="utf-8")
        offenders += [f"{p}:{s[:m.start()].count(chr(10)) + 1}" for m in pat.finditer(s) if "class=" in m.group(3)]
    assert not offenders, offenders


def test_arabic_title_field_keeps_its_full_width(admin):
    html = admin.get("/academics/curriculum").text
    field = re.search(r'<input id="[^"]+" name="arabic_title"[^>]*>', html).group(0)
    assert field.count("class=") == 1 and "w-full" in field and "font-arabic" in field


# --------------------------------------------------------------------------- OQC-14 / OQC-13 Evaluations
def _eval_form(student_id, **extra):
    from datetime import date
    data = {"student_id": str(student_id), "evaluation_type": "manual", "date": date.today().isoformat(),
            "teacher_comment": f"{TAG} evaluation"}
    data.update(extra)
    return data


def _drop_evaluations(db):
    from app.models.academic import Evaluation
    for ev in db.query(Evaluation).filter(Evaluation.teacher_comment.like(f"{TAG}%")):
        db.delete(ev)
    db.commit()


@pytest.mark.parametrize("score", ["-10", "-1", "100.1", "150"])
def test_out_of_range_scores_are_refused_by_the_server(admin, db, score):
    from app.models.academic import Evaluation
    from app.models.people import Student
    s = db.query(Student).filter(Student.status == "active").first()
    try:
        r = admin.post("/academics/evaluations/new", data=_eval_form(s.id, score=score), follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/academics/evaluations"
        assert db.query(Evaluation).filter(Evaluation.teacher_comment.like(f"{TAG}%")).count() == 0, \
            f"a score of {score} out of 100 was saved"
    finally:
        _drop_evaluations(db)


@pytest.mark.parametrize("score,expected", [("0", "fail"), ("54.9", "fail"), ("55", "pass"), ("100", "pass")])
def test_boundary_scores_are_kept_and_pass_follows_the_55_percent_line(admin, db, score, expected):
    from app.models.academic import Evaluation
    from app.models.people import Student
    s = db.query(Student).filter(Student.status == "active").first()
    try:
        r = admin.post("/academics/evaluations/new", data=_eval_form(s.id, score=score), follow_redirects=False)
        assert r.status_code == 303
        ev = db.query(Evaluation).filter(Evaluation.teacher_comment.like(f"{TAG}%")).one()
        assert ev.score == float(score) and ev.result == expected
    finally:
        _drop_evaluations(db)


def test_a_chosen_pass_cannot_override_a_failing_score(admin, db):
    from app.models.academic import Evaluation
    from app.models.people import Student
    s = db.query(Student).filter(Student.status == "active").first()
    try:
        admin.post("/academics/evaluations/new", data=_eval_form(s.id, score="20", result="pass"), follow_redirects=False)
        assert db.query(Evaluation).filter(Evaluation.teacher_comment.like(f"{TAG}%")).one().result == "fail"
    finally:
        _drop_evaluations(db)


def test_question_marks_and_criteria_outside_their_range_are_refused(admin, db):
    from app.models.academic import Evaluation
    from app.models.people import Student
    s = db.query(Student).filter(Student.status == "active").first()
    try:
        for extra in ({"q_text": ["Recite"], "q_marks": ["8"], "q_max": ["5"], "q_result": ["correct"]},
                      {"q_text": ["Recite"], "q_marks": ["-2"], "q_max": ["5"], "q_result": ["correct"]},
                      {"criteria_tajweed": "15"}, {"criteria_fluency": "-1"}):
            admin.post("/academics/evaluations/new", data=_eval_form(s.id, **extra), follow_redirects=False)
        assert db.query(Evaluation).filter(Evaluation.teacher_comment.like(f"{TAG}%")).count() == 0
    finally:
        _drop_evaluations(db)


def test_editing_cannot_push_a_score_out_of_range(admin, db):
    from app.models.academic import Evaluation
    from app.models.people import Student
    s = db.query(Student).filter(Student.status == "active").first()
    try:
        admin.post("/academics/evaluations/new", data=_eval_form(s.id, score="60"), follow_redirects=False)
        ev = db.query(Evaluation).filter(Evaluation.teacher_comment.like(f"{TAG}%")).one()
        admin.post(f"/academics/evaluations/{ev.id}/edit", data={"score": "150"}, follow_redirects=False)
        db.expire_all()
        assert db.get(Evaluation, ev.id).score == 60.0
        admin.post(f"/academics/evaluations/{ev.id}/edit", data={"score": "30"}, follow_redirects=False)
        db.expire_all()
        assert db.get(Evaluation, ev.id).result == "fail", "lowering the score below the line turns it to fail"
    finally:
        _drop_evaluations(db)


def test_teacher_portal_refuses_out_of_range_criteria(db):
    from app.models.academic import Evaluation
    from app.models.people import Student, Teacher
    from app.models.core import User
    c = TestClient(app)
    assert c.post("/login", data={"username": "teacher1@oqc.local", "password": "Teacher@123"}, follow_redirects=False).status_code == 303
    t = db.query(Teacher).join(User, Teacher.user_id == User.id).filter(User.email == "teacher1@oqc.local").first()
    s = db.query(Student).filter(Student.teacher_id == t.id).first()
    if s is None:
        pytest.skip("teacher1 has no students")
    try:
        c.post("/teacher/evaluations", data={"student_id": str(s.id), "tajweed": "40", "fluency": "8",
                                             "teacher_comment": f"{TAG} portal"}, follow_redirects=False)
        assert db.query(Evaluation).filter(Evaluation.teacher_comment.like(f"{TAG}%")).count() == 0
    finally:
        _drop_evaluations(db)


@pytest.mark.parametrize("tab", ["evaluations", "manual"])
def test_evaluation_tiles_count_the_tab_the_table_shows(admin, db, tab):
    from app.models.academic import Evaluation
    html = admin.get(f"/academics/evaluations?tab={tab}").text
    rows = db.query(Evaluation).filter(Evaluation.is_manual.is_(tab == "manual")).count()
    tile = re.search(r'text-\[30px\][^"]*">\s*([\d,]+)\s*</div>\s*<div[^>]*>Evaluations</div>', html)
    assert tile is not None and int(tile.group(1).replace(",", "")) == rows


# --------------------------------------------------------------------------- OQC-06 / OQC-07 Reschedule and linked class
@pytest.fixture()
def tx():
    """A session whose work is rolled back: nothing these scheduling checks do is kept."""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _future_class_with_schedule(tx):
    """A pending class three weeks out on a running schedule of an active subscription, made inside the rolled-back
    session so the check never depends on how old the development database is."""
    from datetime import date, datetime, timedelta
    from app.models.finance import Subscription
    from app.models.scheduling import ClassSession, Schedule
    sch = (tx.query(Schedule).join(Subscription, Schedule.subscription_id == Subscription.id)
           .filter(Schedule.status == "active", Subscription.status == "active", Schedule.teacher_id.isnot(None))
           .order_by(Schedule.id).first())
    if sch is None:
        pytest.skip("no active schedule on an active subscription in this database")
    d = date.today() + timedelta(days=21)
    start = datetime.combine(d, sch.start_time)
    cs = ClassSession(schedule_id=sch.id, student_id=sch.student_id, teacher_id=sch.teacher_id, course_id=sch.course_id,
                      date=d, start_time=sch.start_time, end_time=(start + timedelta(minutes=sch.duration_minutes or 30)).time(),
                      scheduled_start=start, duration_minutes=sch.duration_minutes or 30, status="pending",
                      subscription_id=sch.subscription_id)
    tx.add(cs)
    tx.flush()
    return cs


def _other_teacher(tx, not_id):
    from app.models.people import Teacher
    t = tx.query(Teacher).filter(Teacher.id != not_id, Teacher.is_verified.is_(True)).order_by(Teacher.id.desc()).first()
    if t is None:
        pytest.skip("only one verified teacher")
    return t


def _free_day(tx, teacher_id, start, duration):
    """A date 40-60 days out with nothing on the substitute's calendar at that time."""
    from datetime import date, timedelta
    from app.services import arrangements
    for k in range(40, 60):
        d = date.today() + timedelta(days=k)
        if not arrangements._teacher_busy(tx, teacher_id, d, start, duration):
            return d
    pytest.skip("no free day for the substitute")


def test_an_approved_substitute_survives_a_schedule_regeneration_and_a_teacher_change(tx):
    from app.models.core import User
    from app.models.scheduling import ClassSession, Schedule
    from app.services import arrangements, people, scheduling
    cs = _future_class_with_schedule(tx)
    sub_teacher = _other_teacher(tx, cs.teacher_id)
    admin_user = tx.query(User).filter(User.email == "admin@oqc.local").first()
    day = _free_day(tx, sub_teacher.id, cs.start_time, cs.duration_minutes)
    rr = arrangements.create_reschedule_request(tx, admin_user, cs, day, None, cs.start_time, sub_teacher.id, f"{TAG} move")
    new = arrangements.approve_reschedule(tx, admin_user, rr)
    assert new.teacher_id == sub_teacher.id == rr.new_teacher_id

    sch = tx.get(Schedule, cs.schedule_id)
    scheduling.regenerate_future_sessions(tx, sch)
    tx.flush()
    kept = tx.get(ClassSession, rr.new_session_id)
    assert kept is not None, "regenerating the weekly pattern deleted the class the reschedule created"
    assert kept.teacher_id == sub_teacher.id

    third = _other_teacher(tx, sub_teacher.id)
    people.propagate_teacher_change(tx, cs.student, cs.teacher, third, admin_user, reason=f"{TAG} change")
    tx.flush()
    assert tx.get(ClassSession, rr.new_session_id).teacher_id == sub_teacher.id, \
        "a general teacher change overrode the substitute the reschedule approved"


def test_a_same_teacher_reschedule_follows_a_new_schedule_teacher(tx):
    from app.models.core import User
    from app.models.scheduling import ClassSession, Schedule
    from app.services import arrangements, scheduling
    cs = _future_class_with_schedule(tx)
    admin_user = tx.query(User).filter(User.email == "admin@oqc.local").first()
    day = _free_day(tx, cs.teacher_id, cs.start_time, cs.duration_minutes)
    rr = arrangements.create_reschedule_request(tx, admin_user, cs, day, None, cs.start_time, None, f"{TAG} same")
    arrangements.approve_reschedule(tx, admin_user, rr)
    sch = tx.get(Schedule, cs.schedule_id)
    sch.teacher_id = _other_teacher(tx, cs.teacher_id).id
    scheduling.regenerate_future_sessions(tx, sch)
    tx.flush()
    kept = tx.get(ClassSession, rr.new_session_id)
    assert kept is not None and kept.teacher_id == sch.teacher_id


def test_a_cancelled_subscription_cannot_have_its_classes_rescheduled(tx):
    from app.models.core import User
    from app.services import arrangements, billing
    cs = _future_class_with_schedule(tx)
    admin_user = tx.query(User).filter(User.email == "admin@oqc.local").first()
    day = _free_day(tx, cs.teacher_id, cs.start_time, cs.duration_minutes)
    rr = arrangements.create_reschedule_request(tx, admin_user, cs, day, None, cs.start_time, None, f"{TAG} pending")
    billing.cancel_subscription(tx, cs.subscription, admin_user, f"{TAG} cancelled")
    tx.flush()
    assert rr.status == "cancelled" and "cancelled" in (rr.comments or "")
    with pytest.raises(ValueError, match="renew it"):
        arrangements.create_reschedule_request(tx, admin_user, cs, day, None, cs.start_time, None, f"{TAG} again")
