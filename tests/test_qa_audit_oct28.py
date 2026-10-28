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


# --------------------------------------------------------------------------- OQC-02 Teacher Change Requests
def test_change_status_returns_to_the_list_the_person_was_on(admin):
    html = admin.get("/requests/teacher-change?status=pending").text
    back = re.search(r'name="back" value="([^"]+)"', html).group(1).replace("&amp;", "&")
    assert back.startswith("/requests/teacher-change?status=pending")
    r = admin.post("/requests/teacher-change/change-status", follow_redirects=False,
                   data={"status": "approved", "remarks": "x", "back": back})   # no row ticked
    assert r.status_code == 303 and r.headers["location"] == back, "the error must keep the person's filters"
    r = admin.post("/requests/teacher-change/change-status", follow_redirects=False,
                   data={"status": "approved", "remarks": "x", "back": "https://evil.example/x"})
    assert r.headers["location"] == "/requests/teacher-change", "a back link off this list is never followed"


def test_report_toolbar_adopts_a_page_s_own_row_checkbox():
    """The interactive report used to add its own checkbox column beside the page's name="ids" one: two boxes per row,
    and ticking the report's box sent no ids. It now adopts the page's box; the script is fingerprinted so the fix
    reaches browsers that cached the old one."""
    js = pathlib.Path("app/static/js/report.js").read_text(encoding="utf-8")
    assert "var own = this.trs.every" in js and "setAttribute('data-ir-row', '')" in js
    from app.core import templating
    src = pathlib.Path(templating.__file__).read_text(encoding="utf-8")
    assert '"js/report.js"' in src.split("def _asset_version", 1)[1].split("ASSET_VERSION =", 1)[0]


# --------------------------------------------------------------------------- OQC-05 Class Schedules Rescheduled filter
def test_class_status_filter_offers_rescheduled_but_the_change_action_does_not(admin):
    html = admin.get("/classes").text
    filter_form = re.search(r'<form method="get" action="/classes".*?</form>', html, re.S).group(0)
    assert '<option value="rescheduled"' in filter_form
    selects = re.findall(r'<select[^>]*name="status"[^>]*>(.*?)</select>', html, re.S)
    assert any('value="rescheduled"' not in sel for sel in selects[1:]), "New status must not offer Rescheduled"
    assert admin.get("/classes?status=rescheduled").status_code == 200


# --------------------------------------------------------------------------- OQC-12 / OQC-21 Syllabus Compliance
def test_students_with_nothing_due_are_not_assessed_and_stay_out_of_the_averages(db):
    from app.services import academic
    rows = academic.syllabus_compliance(db)
    for r in rows:
        if r["expected"] == 0:
            assert r["compliance"] is None and r["status"] in ("no_curriculum", "not_due") and not r["assessed"]
        else:
            assert r["assessed"] and r["status"] in ("on_track", "behind", "at_risk")
        if r["plans"] == 0:
            assert r["delivery_rate"] is None


def test_compliance_page_says_na_instead_of_zero_and_explains_an_empty_chart(admin, db):
    from app.services import academic
    html = admin.get("/academics/curriculum/compliance").text
    rows = academic.syllabus_compliance(db)
    assert "Not assessed" in html and "Compliance = lessons completed" in html
    assessed = [r for r in rows if r["assessed"]]
    tile = re.search(r'text-\[30px\][^"]*">\s*(\d+)\s*</div>\s*<div[^>]*>Students assessed</div>', html)
    assert tile and int(tile.group(1)) == len(assessed)
    if not assessed:
        assert "no compliance to chart" in html and "complianceChart" not in html


# --------------------------------------------------------------------------- OQC-15 / OQC-16 Journey and monthly tests
def test_journey_assessments_tab_lists_the_period_s_monthly_tests(admin, db):
    from app.models.academic import MonthlyTest
    t = db.query(MonthlyTest).order_by(MonthlyTest.id.desc()).first()
    if t is None:
        pytest.skip("no monthly test in this database")
    html = admin.get(f"/students/{t.student_id}/journey?view=assessments&period=month&month={t.period}").text
    assert f"Monthly test · {t.period}" in html and f"/academics/monthly-tests/{t.id}" in html
    assert "No evaluation or monthly test in this period" not in html


def test_monthly_tests_are_not_generated_without_a_published_curriculum(tx):
    from app.models.academic import Course, MonthlyTest
    from app.models.people import Student
    from app.services import academic
    bare = Course(name=f"Course {TAG}", code=f"Q{TAG[-4:]}".upper())
    tx.add(bare)
    tx.flush()
    s = tx.query(Student).filter(Student.status.in_(["active", "trial"])).first()
    s.course_id = bare.id
    tx.flush()
    skipped: list = []
    academic.generate_tests_for_period(tx, "2099-01", skipped=skipped)
    assert s in skipped
    assert tx.query(MonthlyTest).filter(MonthlyTest.student_id == s.id, MonthlyTest.period == "2099-01").count() == 0


# --------------------------------------------------------------------------- OQC-01 Teacher assignment from Edit Student
def test_edit_student_shows_the_teacher_and_a_way_to_assign_one(admin, db):
    from app.models.people import Student
    s = db.query(Student).filter(Student.status == "active").first()
    html = admin.get(f"/students/{s.id}/edit").text
    assert f'href="/students/{s.id}/change-teacher"' in html
    assert ("Change teacher" if s.teacher_id else "Assign teacher") in html


def test_change_teacher_page_offers_every_verified_teacher(admin, db):
    from app.models.people import Student, Teacher
    s = db.query(Student).filter(Student.status == "active").first()
    html = admin.get(f"/students/{s.id}/change-teacher").text
    verified = db.query(Teacher).filter(Teacher.is_verified.is_(True), Teacher.status == "active")
    if s.teacher_id:
        verified = verified.filter(Teacher.id != s.teacher_id)
    sel = re.search(r'<select[^>]*name="other_teacher_id"[^>]*>(.*?)</select>', html, re.S)
    assert sel is not None
    assert sel.group(1).count("<option value=") - 1 == verified.count(), "the list must hold every verified teacher"
    r = admin.post(f"/students/{s.id}/change-teacher", data={"other_teacher_id": "0", "reason": "x"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/change-teacher")


# --------------------------------------------------------------------------- OQC-03 icons that never drew
def test_every_icon_the_app_names_is_in_the_bundled_lucide():
    """Ledger Additions asked for "plus-minus" and the CRM pages for "funnel": names this build of Lucide does not
    have, so the tile drew an empty circle. Every icon named in a menu, a macro call or a data-lucide must exist."""
    js = pathlib.Path("app/static/vendor/lucide.min.js").read_text(encoding="utf-8")

    def have(name):
        return re.search(r"\b" + "".join(p[:1].upper() + p[1:] for p in name.split("-")) + r"\b", js) is not None
    pats = [r'data-lucide="([a-z0-9-]+)"', r"""\bicon\s*=\s*['"]([a-z0-9-]+)['"]""",
            r"""['"]icon['"]\s*:\s*['"]([a-z0-9-]+)['"]""", r"""_i\("[^"]*",\s*"[^"]*",\s*"([a-z0-9-]+)\""""]
    missing = {}
    for p in list(pathlib.Path("app/templates").rglob("*.html")) + list(pathlib.Path("app").rglob("*.py")):
        s = p.read_text(encoding="utf-8")
        for pat in pats:
            for m in re.finditer(pat, s):
                if not have(m.group(1)):
                    missing.setdefault(m.group(1), set()).add(str(p))
    assert not missing, missing


# --------------------------------------------------------------------------- OQC-17 Bilingual result card layout
def test_result_card_text_stays_inside_its_margins_and_columns(tx, tmp_path, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    from app.models.academic import MonthlyTest
    from app.services import academic
    t = tx.query(MonthlyTest).filter(MonthlyTest.percentage.isnot(None)).first()
    if t is None:
        pytest.skip("no scored monthly test in this database")
    monkeypatch.setattr(academic, "CARD_DIR", tmp_path)
    t.student.full_name = t.student.full_name + " bin Abdul-Rahman Al-Hashimi Qureshi Siddiqui"
    academic.generate_result_card_pdf(tx, t)
    pdf = next(tmp_path.glob("*.pdf"))
    page = pymupdf.open(pdf)[0]
    width = page.rect.width
    words = page.get_text("words")
    assert words
    assert max(w[2] for w in words) <= width - 40 + 0.5, "text runs past the right margin"
    name_line = [w for w in words if "Siddiqui" in w[4] or "Qureshi" in w[4]]
    assert name_line and max(w[2] for w in name_line) <= 40 + 250 + 0.5, "the student's name ran into the Course column"


# --------------------------------------------------------------------------- OQC-04 Client status, as on the student page
def test_client_page_changes_status_with_a_reason_and_audits_it(admin, db):
    from app.models.core import AuditEvent
    from app.models.people import Client
    c = db.query(Client).filter(Client.status == "active").order_by(Client.id.desc()).first()
    before = (c.status, c.status_remarks, c.user.is_active if c.user else None)
    try:
        html = admin.get(f"/clients/{c.id}").text
        assert f'action="/clients/{c.id}/status"' in html and "Change client status" in html
        r = admin.post(f"/clients/{c.id}/status", data={"status": "on_leave"}, follow_redirects=False)
        db.expire_all()
        assert r.status_code == 303 and db.get(Client, c.id).status == "active", "no reason, no change"
        admin.post(f"/clients/{c.id}/status", data={"status": "on_leave", "reason": f"{TAG} travelling"}, follow_redirects=False)
        db.expire_all()
        c2 = db.get(Client, c.id)
        assert c2.status == "on_leave" and c2.status_remarks == f"{TAG} travelling"
        assert db.query(AuditEvent).filter(AuditEvent.entity_type == "Client", AuditEvent.entity_id == c.id,
                                           AuditEvent.rationale == f"{TAG} travelling").count() == 1
        admin.post(f"/clients/{c.id}/status", data={"status": "inactive", "reason": f"{TAG} stopped"}, follow_redirects=False)
        db.expire_all()
        if db.get(Client, c.id).user:
            assert db.get(Client, c.id).user.is_active is False, "an inactive family loses its portal login"
    finally:
        c = db.get(Client, c.id)
        c.status, c.status_remarks = before[0], before[1]
        if c.user and before[2] is not None:
            c.user.is_active = before[2]
        db.commit()
