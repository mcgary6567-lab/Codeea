"""Access hardening found by crawling every role on PostgreSQL (13 Oct 2026, Hostinger VPS go-live).

* Families and students reached staff pages: the notification log and broadcast (guarded by notifications.*,
  which every role holds for its own inbox), every staff support ticket, and the Employee Self Portal.
* Pages showed links the role could not open (403): app/core/link_guard.py now drops or flattens them.
* Security Center and the Audit Log failed on PostgreSQL (date = varchar); see test_postgres_safe_values.py.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import link_guard, nav, rbac
from app.database import SessionLocal
from app.main import app
from app.models.core import User


def _login(email: str, password: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/login", data={"username": email, "password": password}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture(scope="module")
def parent():
    return _login("parent1@oqc.local", "Parent@123")


@pytest.fixture(scope="module")
def student():
    return _login("student1@oqc.local", "Student@123")


@pytest.fixture(scope="module")
def teacher():
    return _login("teacher1@oqc.local", "Teacher@123")


@pytest.mark.parametrize("who", ["parent", "student"])
@pytest.mark.parametrize("url", ["/admin/notifications", "/admin/notifications/templates/new", "/config/support-tickets",
                                 "/hr/me", "/hr/me/ledger", "/hr/me/tasks"])
def test_families_and_students_never_reach_staff_pages(who, url, request):
    c = request.getfixturevalue(who)
    assert c.get(url, follow_redirects=False).status_code == 403, url


@pytest.mark.parametrize("who", ["parent", "student"])
def test_families_and_students_cannot_broadcast(who, request):
    c = request.getfixturevalue(who)
    r = c.post("/admin/notifications/broadcast", data={"title": "x", "message": "x", "audience": "all"}, follow_redirects=False)
    assert r.status_code == 403


def test_their_own_notifications_still_open(parent, student):
    assert parent.get("/notifications").status_code == 200
    assert student.get("/notifications").status_code == 200


def test_staff_keep_their_pages(teacher):
    for url in ("/hr/me", "/hr/me/ledger", "/config/support-tickets"):
        assert teacher.get(url, follow_redirects=False).status_code == 200, url
    assert teacher.get("/admin/notifications", follow_redirects=False).status_code == 403, "templates are configuration"
    admin = _login("admin@oqc.local", "Admin@12345")
    assert admin.get("/admin/notifications").status_code == 200


def test_security_center_and_audit_log_render():
    admin = _login("admin@oqc.local", "Admin@12345")
    assert admin.get("/admin/security").status_code == 200
    assert admin.get("/admin/audit").status_code == 200


def test_grievances_leave_the_menu_of_whoever_cannot_read_them():
    s = SessionLocal()
    try:
        finance = s.query(User).filter(User.email == "finance@oqc.local").one()
        hr = s.query(User).filter(User.email == "hr@oqc.local").one()
        urls = lambda u: {i["url"] for sec in nav.nav_for(u) for g in (sec.get("groups") or [{"items": sec.get("items", [])}])  # noqa: E731
                          for i in g["items"]}
        assert not rbac.can_read_grievances(finance) and "/hr/grievances" not in urls(finance)
        assert rbac.can_read_grievances(hr) and "/hr/grievances" in urls(hr)
    finally:
        s.close()


# ------------------------------------------------------------------------------------------------ the link guard
def test_the_guard_drops_buttons_and_flattens_links():
    s = SessionLocal()
    try:
        teacher = s.query(User).filter(User.email == "teacher1@oqc.local").one()
        html = ('<a href="/clients/1/edit" class="inline-flex items-center gap-1.5 rounded px-3">Edit</a>'
                '<a href="/clients/1" class="font-medium">The family</a>'
                '<a href="/teacher/classes" class="x">My classes</a>'
                '<a href="/static/css/app.css">css</a>')
        out = link_guard.filter_links(html, teacher)
        assert "Edit" not in out, "a button the user cannot use is removed"
        assert '<span class="font-medium">The family</span>' in out and 'href="/clients/1"' not in out
        assert 'href="/teacher/classes"' in out and 'href="/static/css/app.css"' in out
        assert not link_guard.can_open(teacher, "/home/hr"), "an area outside the teacher's menu"
        assert link_guard.can_open(teacher, "/home")
    finally:
        s.close()


def test_pages_carry_no_link_the_role_cannot_open(teacher):
    import re
    for page in ("/students", "/hr/attendance", "/classes/rescheduled", "/hr/complaints"):
        r = teacher.get(page)
        assert r.status_code == 200, page
        hrefs = {h.replace("&amp;", "&") for h in re.findall(r'href="(/[^"#]*)"', r.text)
                 if not h.startswith(("/static", "/logout"))}
        bad = [h for h in sorted(hrefs) if teacher.get(h, follow_redirects=False).status_code in (403, 404)]
        assert not bad, f"{page} links to pages the teacher cannot open: {bad[:5]}"


def test_a_family_sees_its_classroom_without_staff_links_or_the_lesson_plan(parent):
    s = SessionLocal()
    try:
        from app.models.people import Client
        from app.models.scheduling import ClassSession
        fam = s.query(Client).filter(Client.user.has(email="parent1@oqc.local")).one()
        cs = s.query(ClassSession).filter(ClassSession.student_id.in_([st.id for st in fam.students])).first()
    finally:
        s.close()
    r = parent.get(f"/classroom/{cs.id}")
    assert r.status_code == 200
    assert 'href="/classes' not in r.text and "Save lesson plan" not in r.text
    assert 'href="/portal/schedule"' in r.text
