"""Full-system audit, 27 Oct 2026: open redirects, API sign-in lockout, per-address limits, security headers,
uploaded files sandboxed, teachers limited to their own students and trials, Alpine data that survives quotes,
unique form-field ids."""
from __future__ import annotations

import re
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.ratelimit import LOGIN_FAILURES, SlidingWindow
from app.core.utils import safe_local_url
from app.database import SessionLocal
from app.main import app
from app.models.core import User
from app.models.people import Student, Teacher
from app.models.scheduling import Trial


def _login(email: str, password: str, **kw) -> TestClient:
    c = TestClient(app, **kw)
    assert c.post("/login", data={"username": email, "password": password}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture(autouse=True)
def _clear_limits():
    LOGIN_FAILURES._hits.clear()
    yield
    LOGIN_FAILURES._hits.clear()


# ------------------------------------------------------------------------------------------------ redirects
@pytest.mark.parametrize("raw,expected", [
    ("/clients?page=2", "/clients?page=2"), ("//evil.example", "/home"), ("https://evil.example/x", "/home"),
    ("/\\evil.example", "/home"), ("/a\r\nSet-Cookie: x=1", "/home"), ("javascript:alert(1)", "/home"), ("", "/home"),
])
def test_only_addresses_on_this_site_are_followed(raw, expected):
    assert safe_local_url(raw) == expected


def test_signing_in_never_sends_someone_to_another_site():
    c = TestClient(app)
    r = c.post("/login", data={"username": "admin@oqc.local", "password": "Admin@12345", "next": "https://evil.example/"},
               follow_redirects=False)
    assert r.status_code == 303 and "evil" not in r.headers["location"]
    r = c.post("/favourites/toggle", data={"path": "/clients", "label": "x", "next": "//evil.example"}, follow_redirects=False)
    assert "evil" not in r.headers.get("location", "")
    c.post("/favourites/toggle", data={"path": "/clients", "label": "x"})


# ------------------------------------------------------------------------------------------------ sign-in limits
def test_the_api_sign_in_counts_failures_and_locks_the_account():
    s = SessionLocal()
    u = s.query(User).filter(User.email == "auditor@oqc.local").one()
    uid, before = u.id, (u.failed_login_attempts, u.locked_until)
    s.close()
    c = TestClient(app)
    try:
        for _ in range(settings.MAX_LOGIN_ATTEMPTS):
            assert c.post("/api/v1/auth/login", json={"username": "auditor@oqc.local", "password": "wrong"}).status_code == 401
        r = c.post("/api/v1/auth/login", json={"username": "auditor@oqc.local", "password": "Auditor@123"})
        assert r.status_code == 423, "the right password does not open a locked account"
    finally:
        s = SessionLocal()
        u = s.get(User, uid)
        u.failed_login_attempts, u.locked_until = 0, None
        s.commit()
        s.close()


def test_many_failures_from_one_address_are_slowed_down():
    w = SlidingWindow(limit=3, seconds=60)
    for _ in range(3):
        assert not w.blocked("1.2.3.4")
        w.hit("1.2.3.4")
    assert w.blocked("1.2.3.4") > 0 and not w.blocked("5.6.7.8")
    for _ in range(30):
        LOGIN_FAILURES.hit("testclient")
    r = TestClient(app).post("/login", data={"username": "admin@oqc.local", "password": "Admin@12345"})
    assert "Too many failed sign-ins" in r.text
    assert TestClient(app).post("/api/v1/auth/login", json={"username": "admin@oqc.local", "password": "x"}).status_code == 429


# ------------------------------------------------------------------------------------------------ headers
def test_pages_carry_a_content_security_policy():
    admin = _login("admin@oqc.local", "Admin@12345")
    h = admin.get("/home").headers
    csp = h.get("content-security-policy", "")
    for part in ("default-src 'self'", "frame-ancestors 'self'", "object-src 'none'", "form-action 'self'", "base-uri 'self'"):
        assert part in csp
    assert f"https://{settings.JITSI_DOMAIN}" in csp and f"https://{settings.JITSI_DOMAIN}" in h.get("permissions-policy", "")


def test_uploaded_files_open_in_a_sandbox():
    r = TestClient(app).get("/storage/anything/x.svg")
    assert "sandbox" in r.headers.get("content-security-policy", "")


# ------------------------------------------------------------------------------------------------ teachers' scope
def test_a_teacher_sees_only_their_own_students_and_trials():
    s = SessionLocal()
    u = s.query(User).filter(User.email == "teacher1@oqc.local").one()
    me = s.query(Teacher).filter(Teacher.user_id == u.id).one()
    mine = s.query(Student).filter(Student.teacher_id == me.id).first()
    other = s.query(Student).filter(Student.teacher_id.isnot(None), Student.teacher_id != me.id).first()
    other_trial = s.query(Trial).filter(Trial.teacher_id.isnot(None), Trial.teacher_id != me.id).first()
    ids = (mine.id if mine else None, other.id if other else None, other_trial.id if other_trial else None, other.full_name if other else "")
    s.close()
    teacher = _login("teacher1@oqc.local", "Teacher@123")
    if ids[0]:
        assert teacher.get(f"/students/{ids[0]}").status_code == 200
    if ids[1]:
        assert teacher.get(f"/students/{ids[1]}", follow_redirects=False).status_code in (302, 303, 403)
        assert teacher.get(f"/students/{ids[1]}/print", follow_redirects=False).status_code in (302, 303, 403)
        assert ids[3] not in teacher.get("/students?rows=500").text.split("<main", 1)[-1]
    if ids[2]:
        assert teacher.get(f"/trials/{ids[2]}", follow_redirects=False).status_code in (302, 303, 403)


# ------------------------------------------------------------------------------------------------ page markup
def test_alpine_data_with_quotes_reaches_the_browser_whole():
    admin = _login("admin@oqc.local", "Admin@12345")
    html = admin.get("/finance/invoices").text
    m = re.search(r'x-data="(\{ subs: [^"]*)"', html)
    assert m and m.group(1).rstrip().endswith("} }"), "the attribute must not be cut at the first quote in the JSON"


def test_form_field_ids_are_unique_and_labels_point_at_them():
    admin = _login("admin@oqc.local", "Admin@12345")
    for url in ("/qa/config/review-parameters", "/hr/attendance", "/classes", "/config/branch-properties"):
        html = admin.get(url).text
        ids = re.findall(r'\sid="([^"]+)"', html)
        assert not [i for i, n in Counter(ids).items() if n > 1], url
        for target in re.findall(r'<label[^>]*\bfor="([^"]+)"', html):
            assert target in ids, (url, target)
