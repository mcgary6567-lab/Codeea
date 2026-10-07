"""HR > Users & Access: the guided Create User, the simple user page, the Permissions overview and the 4-level editor.

The client asked for the previous ERP's path: Create User -> Select Employee -> Select Role -> access applied ->
customise if required -> Save, with user management, roles and permissions living in HR. The checks below cover
that path and the rules every role/access change keeps (no self change, no escalation, staff only).
The suite runs twice on one database, so everything created here is removed afterwards.
"""
from __future__ import annotations

import html
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core import rbac
from app.database import SessionLocal
from app.main import app
from app.models.core import AuditEvent, Notification, Role, User, UserSession
from app.models.people import Employee
from app.services import roles as role_svc
from app.services import system as sys_svc
from app.web.hr_users import SELF_REASON

TAG = "hu" + uuid.uuid4().hex[:6]


def _client(email: str, password: str) -> TestClient:
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
def made(db):
    created: dict[str, list] = {"employees": [], "roles": []}
    yield created
    db.rollback()
    ids = [u.id for u in db.query(User).filter(User.email.like(f"{TAG}%")).all()]
    if ids:
        db.query(Employee).filter(Employee.user_id.in_(ids)).update({Employee.user_id: None}, synchronize_session=False)
        db.query(Notification).filter(Notification.user_id.in_(ids)).delete(synchronize_session=False)
        db.query(UserSession).filter(UserSession.user_id.in_(ids)).delete(synchronize_session=False)
        db.query(AuditEvent).filter(AuditEvent.entity_type == "User", AuditEvent.entity_id.in_(ids)).delete(synchronize_session=False)
        db.query(AuditEvent).filter(AuditEvent.actor_id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
    db.query(Employee).filter(Employee.employee_code.like(f"{TAG}%")).delete(synchronize_session=False)
    for rid in created["roles"]:
        db.query(AuditEvent).filter(AuditEvent.entity_type == "Role", AuditEvent.entity_id == rid).delete(synchronize_session=False)
        db.query(Role).filter(Role.id == rid).delete(synchronize_session=False)
    db.commit()


@pytest.fixture(scope="module")
def admin():
    return _client("admin@oqc.local", "Admin@12345")


@pytest.fixture(scope="module")
def hr():
    return _client("hr@oqc.local", "People@123")


def _employee(db, made, name="Test Person") -> Employee:
    n = len(made["employees"])
    e = Employee(employee_code=f"{TAG}{n}", full_name=f"{name} {n}", designation="Coordinator", status="active",
                 email=f"{TAG}.emp{n}@example.test", phone="0300-1234567")
    db.add(e)
    db.commit()
    made["employees"].append(e.id)
    return e


def _role(db, slug) -> Role:
    return db.query(Role).filter(Role.slug == slug).one()


def _user_by_email(db, email) -> User | None:
    db.expire_all()
    return db.query(User).filter(User.email == email).first()


# ----------------------------------------------------------------------------- the level functions
def test_module_level_reads_the_highest_level_held():
    assert role_svc.module_level(["students.view", "students.export"], "students") == "view"
    assert role_svc.module_level(["students.view"], "students") == "view", "plain view (as *.view gives) reads as View"
    assert role_svc.module_level(["students.view", "students.add", "students.update"], "students") == "edit"
    assert role_svc.module_level(["students.*"], "students") == "full"
    assert role_svc.module_level(["*"], "students") == "full"
    assert role_svc.module_level(["*.view"], "billing") == "view"
    assert role_svc.module_level(["students.add"], "students") == "none"
    for level in role_svc.LEVELS:
        perms = {f"leads.{a}" for a in role_svc.LEVEL_ACTIONS[level]}
        assert role_svc.module_level(perms, "leads") == level


def test_customise_round_trip():
    role = ["students.view", "audit.view", "reports.*", "*.view"]
    chosen = {"students": "edit", "audit": "none", "billing": "full"}
    extra, denied = role_svc.customise(role, chosen)
    effective = sys_svc.expand_permissions(role + extra) - sys_svc.expand_permissions(denied)
    assert effective == role_svc.apply_levels(role, chosen)
    assert role_svc.module_level(effective, "students") == "edit"
    assert role_svc.module_level(effective, "audit") == "none"
    assert role_svc.module_level(effective, "billing") == "full"
    # untouched modules stay exactly as the role defines them
    assert role_svc.module_actions(effective, "reports") == set(rbac.ACTIONS)
    assert role_svc.module_actions(effective, "leads") == {"view"}
    assert role_svc.customise(role, {}) == ([], [])
    # starting from an existing customisation keeps it for modules not changed now
    extra2, denied2 = role_svc.customise(role, {"leads": "edit"}, current=effective)
    again = sys_svc.expand_permissions(role + extra2) - sys_svc.expand_permissions(denied2)
    assert role_svc.module_level(again, "students") == "edit" and role_svc.module_level(again, "leads") == "edit"


def test_areas_cover_each_menu_module_once_and_summary_counts_pages():
    areas = role_svc.area_modules("admin")
    seen = [m["module"] for a in areas for m in a["modules"]]
    assert len(seen) == len(set(seen)) == len(rbac.MODULES)
    assert areas[-1]["label"] == role_svc.OTHER_AREA
    full = role_svc.access_summary(["*"])
    assert all(a["pages"] == a["total"] and a["level"] == "full" for a in full)
    assert all(a["pages"] == 0 and a["level"] == "none" for a in role_svc.access_summary([]))
    sentence = role_svc.summary_sentence(role_svc.access_summary(["employees.*", "users.view"]))
    assert sentence.startswith("Human Resource (") and "full" in sentence


# ----------------------------------------------------------------------------- create
def test_create_user_from_an_employee_takes_the_role_as_is(admin, db, made):
    emp = _employee(db, made)
    auditor = _role(db, "auditor")
    page = admin.get("/hr/users/new").text
    assert emp.employee_code in page and "Customize access (optional)" in page
    email = f"{TAG}.plain@example.test"
    r = admin.post("/hr/users/new", data={"employee_id": str(emp.id), "full_name": emp.full_name, "email": email,
                                          "role_id": str(auditor.id), "customise": "0", "password": "",
                                          "must_change_password": "1"}, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    u = _user_by_email(db, email)
    assert u is not None and r.headers["location"] == f"/hr/users/{u.id}"
    assert db.get(Employee, emp.id).user_id == u.id
    assert u.role_id == auditor.id and u.extra_permissions == [] and u.denied_permissions == []
    assert u.must_change_password
    assert role_svc.held_permissions(u) == sys_svc.expand_permissions(auditor.permissions)
    landing = admin.get(r.headers["location"]).text
    assert "Temporary password (shown once, copy it now)" in landing
    assert db.query(AuditEvent).filter(AuditEvent.entity_type == "User", AuditEvent.entity_id == u.id,
                                       AuditEvent.action == "create").count() == 1
    assert db.query(Notification).filter(Notification.user_id == u.id, Notification.event_type == "account_created").count() == 1
    # the employee is no longer offered
    assert emp.employee_code not in admin.get("/hr/users/new").text


def test_create_user_with_customised_access(admin, db, made):
    emp = _employee(db, made)
    auditor = _role(db, "auditor")
    email = f"{TAG}.custom@example.test"
    r = admin.post("/hr/users/new", data={"employee_id": str(emp.id), "full_name": emp.full_name, "email": email,
                                          "role_id": str(auditor.id), "customise": "1", "lvl__students": "edit",
                                          "lvl__audit": "none", "lvl__billing": "view"}, follow_redirects=False)
    assert r.status_code == 303
    u = _user_by_email(db, email)
    assert u.extra_permissions and u.denied_permissions
    assert rbac.has_permission(u, "students.add") and rbac.has_permission(u, "students.update")
    assert not rbac.has_permission(u, "students.delete")
    assert not rbac.has_permission(u, "audit.view")
    assert rbac.has_permission(u, "billing.view") and rbac.has_permission(u, "reports.export")
    words = role_svc.describe_overrides(u)
    assert any(line.startswith("Students:") for line in words["extra"])
    assert any(line.startswith("Audit Log:") for line in words["removed"])


def test_create_without_an_employee(admin, db, made):
    email = f"{TAG}.external@example.test"
    r = admin.post("/hr/users/new", data={"not_employee": "1", "full_name": "External Auditor", "email": email,
                                          "role_id": str(_role(db, "auditor").id)}, follow_redirects=False)
    assert r.status_code == 303
    u = _user_by_email(db, email)
    assert u is not None and db.query(Employee).filter(Employee.user_id == u.id).first() is None


def test_hr_head_cannot_escalate(hr, db, made):
    emp = _employee(db, made)
    officer, accountant = _role(db, "hr_officer"), _role(db, "accountant")
    base = {"employee_id": str(emp.id), "full_name": emp.full_name}
    # 1. an Accountant-only module level they do not hold
    email = f"{TAG}.esc1@example.test"
    r = hr.post("/hr/users/new", data={**base, "email": email, "role_id": str(officer.id), "customise": "1",
                                       "lvl__accounts": "edit"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/hr/users/new"
    assert _user_by_email(db, email) is None
    # 2. a role with permissions they lack is neither offered nor accepted
    role_select = hr.get("/hr/users/new").text.split('name="role_id"')[1].split("</select>")[0]
    assert f'value="{officer.id}"' in role_select and f'value="{accountant.id}"' not in role_select
    email = f"{TAG}.esc2@example.test"
    hr.post("/hr/users/new", data={**base, "email": email, "role_id": str(accountant.id)}, follow_redirects=False)
    assert _user_by_email(db, email) is None
    # 3. within their own rights it works
    email = f"{TAG}.ok@example.test"
    r = hr.post("/hr/users/new", data={**base, "email": email, "role_id": str(officer.id)}, follow_redirects=False)
    assert r.status_code == 303
    u = _user_by_email(db, email)
    assert u is not None and u.role_id == officer.id
    # 4. nor on the user page: raising a module they do not hold, or giving the Accountant role
    hr.post(f"/hr/users/{u.id}/access", data={"lvl__accounts": "full", "rationale": "x"}, follow_redirects=False)
    hr.post(f"/hr/users/{u.id}/role", data={"role_id": str(accountant.id), "rationale": "x"}, follow_redirects=False)
    u = _user_by_email(db, email)
    assert not rbac.has_permission(u, "accounts.view") and u.role_id == officer.id


# ----------------------------------------------------------------------------- the user page
def test_change_role_customise_and_reset_on_the_user_page(admin, db, made):
    emp = _employee(db, made)
    email = f"{TAG}.page@example.test"
    admin.post("/hr/users/new", data={"employee_id": str(emp.id), "full_name": emp.full_name, "email": email,
                                      "role_id": str(_role(db, "auditor").id)}, follow_redirects=False)
    u = _user_by_email(db, email)
    page = admin.get(f"/hr/users/{u.id}").text
    assert "Change role" in page and "Customize access" in page and f'href="/admin/users/{u.id}"' in page
    officer = _role(db, "hr_officer")
    r = admin.post(f"/hr/users/{u.id}/role", data={"role_id": str(officer.id)}, follow_redirects=False)
    assert _user_by_email(db, email).role_id != officer.id, "a reason is required"
    r = admin.post(f"/hr/users/{u.id}/role", data={"role_id": str(officer.id), "rationale": "moved to HR"}, follow_redirects=False)
    assert r.status_code == 303 and _user_by_email(db, email).role_id == officer.id
    r = admin.post(f"/hr/users/{u.id}/access", data={"lvl__students": "edit", "lvl__payroll": "none", "rationale": "covers admissions"},
                   follow_redirects=False)
    assert r.status_code == 303
    u = _user_by_email(db, email)
    assert rbac.has_permission(u, "students.add") and not rbac.has_permission(u, "payroll.view")
    assert rbac.has_permission(u, "employees.delete"), "modules not changed keep the role's access"
    assert "CUSTOMISED" in admin.get(f"/hr/users/{u.id}").text
    r = admin.post(f"/hr/users/{u.id}/access", data={"reset": "1", "rationale": "back to normal"}, follow_redirects=False)
    u = _user_by_email(db, email)
    assert u.extra_permissions == [] and u.denied_permissions == []
    # account actions
    r = admin.post(f"/hr/users/{u.id}/status", data={"activate": "0", "rationale": "left"}, follow_redirects=False)
    assert r.status_code == 303 and not _user_by_email(db, email).is_active
    r = admin.post(f"/hr/users/{u.id}/status", data={"activate": "1", "rationale": "back"}, follow_redirects=False)
    assert _user_by_email(db, email).is_active
    r = admin.post(f"/hr/users/{u.id}/reset-password", data={"rationale": "forgot"}, follow_redirects=False)
    assert r.status_code == 303 and "Temporary password" in admin.get(r.headers["location"]).text


def test_nobody_changes_their_own_role_or_access(hr, db):
    me = _user_by_email(db, "hr@oqc.local")
    before = (me.role_id, list(me.extra_permissions or []), list(me.denied_permissions or []))
    page = hr.get(f"/hr/users/{me.id}").text
    assert html.escape(SELF_REASON) in page
    hr.post(f"/hr/users/{me.id}/role", data={"role_id": str(_role(db, 'hr_officer').id), "rationale": "self"}, follow_redirects=False)
    hr.post(f"/hr/users/{me.id}/access", data={"lvl__billing": "full", "rationale": "self"}, follow_redirects=False)
    me = _user_by_email(db, "hr@oqc.local")
    assert (me.role_id, list(me.extra_permissions or []), list(me.denied_permissions or [])) == before


# ----------------------------------------------------------------------------- lists and overview
def test_users_list_shows_staff_only(admin):
    assert "parent1@oqc.local" not in admin.get("/hr/users?q=parent1").text
    assert "student1@oqc.local" not in admin.get("/hr/users?q=student1").text
    body = admin.get("/hr/users?q=teacher1%40").text
    assert "teacher1@oqc.local" in body and "Create User" in body and "No Employee Linked" in body


def test_permissions_overview_matches_access_summary(admin, db):
    body = admin.get("/hr/permissions").text
    for r in role_svc.staff_roles(db):
        assert f'href="/admin/roles/{r.id}"' in body
    for r in role_svc.staff_roles(db):
        assert role_svc.audience(r.portal) == "staff"
    accountant = _role(db, "accountant")
    hr_area = next(a for a in role_svc.access_summary(accountant.permissions) if a["label"] == "Human Resource")
    assert f'title="Accountant opens {hr_area["pages"]} of {hr_area["total"]} Human Resource pages' in body
    word = {"full": "Full", "edit": "Edit", "view": "View", "partial": "Partial", "none": "—"}[hr_area["level"]]
    cell = body.split(f'title="Accountant opens {hr_area["pages"]} of {hr_area["total"]} Human Resource pages')[1]
    assert cell.split(">", 1)[1].startswith(word)
    # one person's access
    officer = _user_by_email(db, "hrofficer@oqc.local")
    assert "Can open:" in admin.get(f"/hr/permissions?user_id={officer.id}").text


def test_old_user_screens_redirect(admin):
    r = admin.get("/admin/users?status=locked", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/hr/users?status=locked"
    r = admin.get("/admin/users/new", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/hr/users/new"


def test_roles_page_is_the_grouped_erp_list(admin, db):
    body = admin.get("/admin/roles").text
    assert "Create role" in body and "Human Resource <span" in body
    acc = _role(db, "accountant")
    assert f'href="/admin/roles/{acc.id}#assign"' in body and 'href="/admin/roles/compare"' in body
    assert 'href="/admin/roles/permission-test"' in body


def test_role_page_simple_level_editor(admin, db, made):
    role = Role(name=f"{TAG} desk", slug=f"{TAG}_desk", portal="admin", permissions=["students.view", "leads.view"])
    db.add(role)
    db.commit()
    made["roles"].append(role.id)
    page = admin.get(f"/admin/roles/{role.id}").text
    assert "Advanced: permission matrix" in page and 'name="lvl__students"' in page and "This role can open:" in page
    r = admin.post(f"/admin/roles/{role.id}", data={"mode": "levels", "name": role.name, "portal": "admin",
                                                    "rationale": "front desk", "lvl__students": "edit", "lvl__leads": "view"},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    perms = sys_svc.expand_permissions(db.get(Role, role.id).permissions)
    assert {"students.add", "students.update", "students.view"} <= perms
    assert "leads.view" in perms and "leads.export" not in perms, "a module left at its level is not touched"


def test_hr_head_opens_users_roles_permissions_and_a_teacher_cannot(hr):
    for url in ("/hr/users", "/hr/users/new", "/admin/roles", "/hr/permissions", "/home/hr/access"):
        assert hr.get(url).status_code == 200, url
    teacher = _client("teacher1@oqc.local", "Teacher@123")
    for url in ("/hr/users", "/hr/users/new", "/admin/roles", "/hr/permissions"):
        assert teacher.get(url, follow_redirects=False).status_code in (303, 403, 404), url
