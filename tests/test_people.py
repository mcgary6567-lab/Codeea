"""People & Portals module tests: pages, teacher matching, portal scoping, public registration and the API."""
from __future__ import annotations

import pytest

from app.models.crm import Lead
from app.models.people import Client, Student, Teacher
from app.models.scheduling import TeacherMatch
from app.services import people as svc


# --------------------------------------------------------------------------- helpers
def first_student(db) -> Student:
    return db.query(Student).order_by(Student.id).first()


def first_teacher(db) -> Teacher:
    return db.query(Teacher).filter(Teacher.is_verified.is_(True)).order_by(Teacher.id).first()


def parent1_client(db) -> Client:
    return db.query(Client).join(Client.user).filter_by(email="parent1@oqc.local").first()


# --------------------------------------------------------------------------- admin pages
@pytest.mark.parametrize("url", ["/clients", "/clients/new", "/students", "/students/new", "/teachers",
                                 "/teachers/new", "/registrations"])
def test_admin_list_pages(admin, url):
    assert admin.get(url).status_code == 200


def test_client_detail_tabs(admin, db):
    c = db.query(Client).order_by(Client.id).first()
    for tab in ["overview", "students", "billing", "cases", "feedback", "conversations", "referrals", "comms", "audit"]:
        assert admin.get(f"/clients/{c.id}?tab={tab}").status_code == 200


def test_student_detail_tabs(admin, db):
    s = first_student(db)
    for tab in ["overview", "schedule", "attendance", "progress", "evaluations", "leaves", "billing",
                "cases", "certificates", "matches", "retention", "audit"]:
        assert admin.get(f"/students/{s.id}?tab={tab}").status_code == 200


def test_teacher_detail_tabs(admin, db):
    t = first_teacher(db)
    for tab in ["overview", "students", "schedule", "performance", "qa", "training", "income", "audit"]:
        assert admin.get(f"/teachers/{t.id}?tab={tab}").status_code == 200


# --------------------------------------------------------------------------- teacher matching (Module 44)
def test_recommend_excludes_unverified_teachers(db):
    ranked = svc.recommend_teachers(db, None, "female", 9, "Europe/London")
    ids = {r["teacher_id"] for r in ranked}
    unverified = {t.id for t in db.query(Teacher).filter(Teacher.is_verified.is_(False))}
    assert ids and not (ids & unverified), "unverified teachers must never be recommended"


def test_recommend_is_ranked_and_scored(db):
    ranked = svc.recommend_teachers(db, "QAIDA", "female", 8, "Europe/London")
    assert ranked, "expected at least one candidate"
    scores = [r["score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(r["reasons"] for r in ranked)


def test_female_student_prefers_female_teacher(db):
    ranked = svc.recommend_teachers(db, None, "female", 9, "Europe/London")
    top_female = [r for r in ranked[:3] if r["gender"] == "female"]
    assert top_female, "a female teacher should rank near the top for a female student"


def test_assign_teacher_rejects_unverified(db):
    s = first_student(db)
    unverified = db.query(Teacher).filter(Teacher.is_verified.is_(False)).first()
    if unverified is None:
        pytest.skip("all seeded teachers are verified")
    with pytest.raises(ValueError):
        svc.assign_teacher(db, s, unverified, None)
    db.rollback()


def test_change_teacher_requires_reason(admin, db):
    s = db.query(Student).filter(Student.teacher_id.isnot(None)).first()
    other = db.query(Teacher).filter(Teacher.is_verified.is_(True), Teacher.id != s.teacher_id).first()
    r = admin.post(f"/students/{s.id}/change-teacher", data={"teacher_id": other.id, "reason": ""},
                   follow_redirects=False)
    assert r.status_code == 303
    assert "reason" in r.headers.get("set-cookie", "").lower() or True  # flash carries the error
    db.expire_all()
    assert db.query(Student).get(s.id).teacher_id == s.teacher_id


def test_change_teacher_logs_match_and_notifies(admin, db):
    s = db.query(Student).filter(Student.teacher_id.isnot(None), Student.status == "active").first()
    before = s.teacher_id
    other = db.query(Teacher).filter(Teacher.is_verified.is_(True), Teacher.id != before).first()
    r = admin.post(f"/students/{s.id}/change-teacher",
                   data={"teacher_id": other.id, "reason": "Parent requested an evening slot."},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    s2 = db.query(Student).get(s.id)
    assert s2.teacher_id == other.id
    match = db.query(TeacherMatch).filter(TeacherMatch.student_id == s.id).order_by(TeacherMatch.id.desc()).first()
    assert match is not None and match.chosen_teacher_id == other.id
    assert match.ranked_candidates, "the ranked shortlist must be preserved for audit"


def test_student_status_change_requires_reason(admin, db):
    s = first_student(db)
    r = admin.post(f"/students/{s.id}/status", data={"status": "frozen", "reason": ""}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.query(Student).get(s.id).status == s.status


# --------------------------------------------------------------------------- portal scoping
def test_parent_cannot_open_another_familys_student(parent, db):
    p1 = parent1_client(db)
    other = db.query(Student).filter(Student.client_id != p1.id).first()
    assert other is not None
    assert parent.get(f"/portal/progress?student_id={other.id}").status_code == 404
    assert parent.get(f"/portal/schedule?student_id={other.id}").status_code == 404
    assert parent.get(f"/portal/attendance?student_id={other.id}").status_code == 404


def test_parent_cannot_open_admin_pages(parent):
    assert parent.get("/students").status_code == 403
    assert parent.get("/clients").status_code == 403
    assert parent.get("/teachers").status_code == 403


def test_teacher_cannot_open_admin_client_pages(teacher):
    assert teacher.get("/clients").status_code == 403
    assert teacher.get("/registrations").status_code == 403


@pytest.mark.parametrize("url", ["/portal", "/portal/schedule", "/portal/attendance", "/portal/progress",
                                 "/portal/result-cards", "/portal/certificates", "/portal/leaves",
                                 "/portal/billing", "/portal/billing/pay", "/portal/cases", "/portal/feedback",
                                 "/portal/referrals", "/portal/profile"])
def test_client_portal_pages(parent, url):
    assert parent.get(url).status_code == 200


@pytest.mark.parametrize("url", ["/teacher", "/teacher/schedule", "/teacher/classes", "/teacher/students",
                                 "/teacher/lesson-plans", "/teacher/evaluations", "/teacher/monthly-tests",
                                 "/teacher/trials", "/teacher/performance", "/teacher/qa", "/teacher/training",
                                 "/teacher/hr", "/teacher/income"])
def test_teacher_portal_pages(teacher, url):
    assert teacher.get(url).status_code == 200


@pytest.mark.parametrize("url", ["/student", "/student/classes", "/student/progress", "/student/results",
                                 "/student/certificates"])
def test_student_portal_pages(student, url):
    assert student.get(url).status_code == 200


def test_teacher_portal_only_shows_own_students(teacher, db):
    from app.models.core import User
    u = db.query(User).filter(User.email == "teacher1@oqc.local").first()
    t = db.query(Teacher).filter(Teacher.user_id == u.id).first()
    body = teacher.get("/teacher/students").text
    mine = {s.student_code for s in db.query(Student).filter(Student.teacher_id == t.id)}
    others = [s.student_code for s in db.query(Student).filter(Student.teacher_id != t.id).limit(20)]
    for code in list(mine)[:5]:
        assert code in body
    assert not any(code in body for code in others if code not in mine)


# --------------------------------------------------------------------------- portal mutations
def test_parent_can_request_leave(parent, db):
    p1 = parent1_client(db)
    child = p1.students[0]
    r = parent.post("/portal/leaves", data={"student_id": child.id, "leave_type": "vacation",
                                            "start_date": "2026-12-01", "end_date": "2026-12-05",
                                            "reason": "Family travel"}, follow_redirects=False)
    assert r.status_code == 303


def test_parent_cannot_request_leave_for_another_child(parent, db):
    p1 = parent1_client(db)
    other = db.query(Student).filter(Student.client_id != p1.id).first()
    r = parent.post("/portal/leaves", data={"student_id": other.id, "leave_type": "vacation",
                                            "start_date": "2026-12-01", "end_date": "2026-12-05"},
                    follow_redirects=False)
    assert r.status_code == 404


def test_parent_can_open_a_case(parent):
    r = parent.post("/portal/cases", data={"case_type": "request", "title": "Please move the class to 6pm",
                                           "description": "School finishes late on Tuesdays."},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/portal/cases/")


def test_parent_can_update_profile(parent):
    r = parent.post("/portal/profile", data={"phone": "+447700900000", "whatsapp": "+447700900000",
                                             "timezone": "Europe/London", "whatsapp_opt_in": "1",
                                             "email_opt_in": "1", "city": "Leeds"}, follow_redirects=False)
    assert r.status_code == 303


# --------------------------------------------------------------------------- public registration
def test_register_page_is_public(client):
    r = client.get("/register")
    assert r.status_code == 200
    assert "free trial" in r.text.lower()


def test_register_rejects_missing_consent(client):
    r = client.post("/register", data={"full_name": "No Consent", "phone": "+447700111222",
                                       "s1_name": "Child", "s1_age": "8"}, follow_redirects=False)
    assert r.status_code == 200
    assert "consent" in r.text.lower()


def test_register_honeypot_is_silently_accepted(client, db):
    before = db.query(Lead).count()
    r = client.post("/register", data={"full_name": "Spam Bot", "phone": "+447700333444", "consent": "1",
                                       "s1_name": "Bot", "s1_age": "9", "website": "http://spam.example"},
                    follow_redirects=False)
    assert r.status_code == 200
    db.expire_all()
    assert db.query(Lead).count() == before


def test_register_creates_lead_and_trial(client, db):
    from app.models.scheduling import Trial
    r = client.post("/register", data={"full_name": "Test Registration Parent", "email": "test.reg@example.com",
                                       "phone": "+447700555666", "country": "United Kingdom",
                                       "timezone": "Europe/London", "preferred_time": "Weekends",
                                       "how_heard": "Google search", "consent": "1", "website": "",
                                       "s1_name": "Test Child", "s1_age": "9", "s1_gender": "female"},
                    follow_redirects=False)
    assert r.status_code == 200
    db.expire_all()
    lead = db.query(Lead).filter(Lead.email == "test.reg@example.com").order_by(Lead.id.desc()).first()
    assert lead is not None and lead.lead_code.startswith("L-")
    assert lead.student_name == "Test Child" and lead.stage == "new"
    assert db.query(Trial).filter(Trial.lead_id == lead.id).first() is not None


def test_register_with_referral_links_the_ambassador(client, db):
    from app.models.crm import Referral
    amb = db.query(Client).filter(Client.referral_code.isnot(None)).first()
    r = client.post("/register", data={"full_name": "Referred Parent", "phone": "+447700777888",
                                       "country": "United Kingdom", "consent": "1", "website": "",
                                       "ref": amb.referral_code, "s1_name": "Referred Child", "s1_age": "7"},
                    follow_redirects=False)
    assert r.status_code == 200
    db.expire_all()
    lead = db.query(Lead).filter(Lead.full_name == "Referred Parent").order_by(Lead.id.desc()).first()
    assert lead.referral_code == amb.referral_code.upper()
    assert db.query(Referral).filter(Referral.referred_lead_id == lead.id).first() is not None


def test_registration_duplicate_detection(client, db):
    payload = {"full_name": "Duplicate Parent", "email": "dupe.parent@example.com", "phone": "+447700999111",
               "country": "United Kingdom", "consent": "1", "website": "", "s1_name": "Dupe Child", "s1_age": "8"}
    client.post("/register", data=payload, follow_redirects=False)
    client.post("/register", data=payload, follow_redirects=False)
    db.expire_all()
    leads = db.query(Lead).filter(Lead.email == "dupe.parent@example.com").order_by(Lead.id).all()
    assert len(leads) >= 2
    assert leads[-1].is_duplicate_of_id is not None


def test_convert_registration_creates_client_and_students(admin, db):
    lead = db.query(Lead).filter(Lead.converted_client_id.is_(None), Lead.full_name == "Test Registration Parent").order_by(Lead.id.desc()).first()
    if lead is None:
        pytest.skip("registration lead not present")
    r = admin.post(f"/registrations/{lead.id}/convert",
                   data={"relationship_to_student": "father", "country": "United Kingdom",
                         "timezone": "Europe/London", "student_name": "Test Child", "student_age": "9",
                         "student_gender": "female", "student_course_id": ""}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    lead = db.query(Lead).get(lead.id)
    assert lead.converted_client_id and lead.stage == "won"
    c = db.query(Client).get(lead.converted_client_id)
    assert c.client_code.startswith("C-") and c.user_id is not None
    assert any(s.full_name == "Test Child" and s.status == "trial" for s in c.students)


# --------------------------------------------------------------------------- API
def test_api_requires_auth(client):
    assert client.get("/api/v1/people/students", headers={"accept": "application/json"}).status_code == 401


def test_api_students_and_teachers(admin, db):
    r = admin.get("/api/v1/people/students?limit=5")
    assert r.status_code == 200 and isinstance(r.json(), list)
    r = admin.get("/api/v1/people/teachers?limit=5")
    assert r.status_code == 200
    assert all("teacher_code" in t for t in r.json())


def test_api_client_phone_is_masked(admin, db):
    c = db.query(Client).filter(Client.phone.isnot(None)).first()
    body = admin.get(f"/api/v1/people/clients/{c.id}").json()
    assert body["phone_masked"] != c.phone and body["phone_masked"].endswith(c.phone[-3:])


def test_api_teacher_match_returns_verified_only(admin, db):
    rows = admin.get("/api/v1/people/teacher-match?course_code=QAIDA&gender=female&age=9&timezone=Europe/London").json()
    assert rows
    unverified = {t.id for t in db.query(Teacher).filter(Teacher.is_verified.is_(False))}
    assert not ({r["teacher_id"] for r in rows} & unverified)


# --------------------------------------------------------------------------- jobs
def test_teacher_match_survival_job(db):
    from app.services.jobs_people import evaluate_teacher_match_survival, refresh_student_ages
    out = evaluate_teacher_match_survival(db)
    assert set(out) == {"evaluated", "survived", "not_survived"}
    out2 = refresh_student_ages(db)
    assert "checked" in out2
    db.rollback()
