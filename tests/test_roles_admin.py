"""Role administration: create a role, give it to people, move them, and the rules that keep it safe.

Reported: "roles not making in admin side and not working to assign to users". The causes were the screens
(Configuration › Roles was read-only, its links went nowhere useful, and a role could only be given one user
at a time from inside each user's profile) and the deploy pipeline (every deploy reset the built-in roles'
permissions). Two security holes sat in the same code: anyone with user-update rights could hand out the
Super Admin role, and saving the Super Admin matrix replaced its "*" with a fixed list.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core import rbac
from app.core.security import hash_password
from app.database import SessionLocal
from app.main import app
from app.models.core import AuditEvent, Notification, Role, Setting, User
from app.services import roles as role_svc

TAG = "rtest" + uuid.uuid4().hex[:6]
PASSWORD = "Rt!" + uuid.uuid4().hex[:10] + "Aa1"


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
    """Users and roles this module creates; removed afterwards so the suite can run twice."""
    created: dict[str, list] = {"roles": [], "users": []}
    yield created
    db.rollback()
    for uid in created["users"]:
        db.query(Notification).filter(Notification.user_id == uid).delete(synchronize_session=False)
        db.query(AuditEvent).filter(AuditEvent.entity_type == "User", AuditEvent.entity_id == uid).delete(synchronize_session=False)
        db.query(User).filter(User.id == uid).delete(synchronize_session=False)
    for rid in created["roles"]:
        db.query(User).filter(User.role_id == rid).update({User.role_id: None}, synchronize_session=False)
        db.query(Role).filter(Role.id == rid).delete(synchronize_session=False)
    db.query(Role).filter(Role.slug.like(f"{TAG}%")).delete(synchronize_session=False)
    db.commit()


def _role(db, made, permissions, portal="admin") -> Role:
    r = Role(name=f"{TAG} {len(made['roles'])}", slug=f"{TAG}_{len(made['roles'])}", portal=portal, permissions=permissions)
    db.add(r)
    db.commit()
    made["roles"].append(r.id)
    return r


def _user(db, made, role: Role | None) -> User:
    n = len(made["users"])
    u = User(email=f"{TAG}.{n}@example.test", username=f"{TAG}_{n}", full_name=f"Role Test {n}",
             hashed_password=hash_password(PASSWORD), role_id=role.id if role else None, is_active=True)
    db.add(u)
    db.commit()
    made["users"].append(u.id)
    return u


@pytest.fixture(scope="module")
def admin():
    return _client("admin@oqc.local", "Admin@12345")


# ----------------------------------------------------------------------------- the screens
def test_configuration_roles_page_can_create_and_links_to_each_role(admin, db):
    body = admin.get("/config/roles").text
    assert "Read only" not in body
    assert 'href="/admin/roles/new"' in body
    some = db.query(Role).filter(Role.slug == "accountant").first()
    assert f'href="/admin/roles/{some.id}"' in body, "Permissions must open that role, not the list"
    assert f'href="/admin/roles/{some.id}#assign"' in body, "Assign Users must open the assign panel"


def test_create_role_cleans_the_slug_and_lands_on_the_matrix(admin, db, made):
    r = admin.post("/admin/roles/new", data={"name": f"{TAG} Front Desk", "slug": f"{TAG} Front-Desk Officer!",
                                             "portal": "admin"}, follow_redirects=False)
    assert r.status_code == 303
    role = db.query(Role).filter(Role.slug == f"{TAG}_front_desk_officer").first()
    assert role is not None, "the typed slug is normalised to letters, digits and underscores"
    made["roles"].append(role.id)
    assert r.headers["location"] == f"/admin/roles/{role.id}"
    page = admin.get(f"/admin/roles/{role.id}").text
    assert 'id="assign"' in page and "Assign role to selected users" in page


def test_assign_a_role_to_several_users_at_once(admin, db, made):
    role = _role(db, made, ["students.view", "clients.view"])
    a, b = _user(db, made, None), _user(db, made, None)
    r = admin.post(f"/admin/roles/{role.id}/assign", data={"user_ids": [str(a.id), str(b.id)], "rationale": "front desk"},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    for u in (a, b):
        u = db.get(User, u.id)
        assert u.role_id == role.id
        assert rbac.has_permission(u, "students.view") and not rbac.has_permission(u, "billing.view")
        assert db.query(AuditEvent).filter(AuditEvent.entity_type == "User", AuditEvent.entity_id == u.id,
                                           AuditEvent.action == "role_change").count() == 1
        assert db.query(Notification).filter(Notification.user_id == u.id, Notification.event_type == "role_change").count() == 1
    # the assigned user's access follows on the very next request
    c = _client(a.email, PASSWORD)
    assert c.get("/students", follow_redirects=False).status_code == 200
    assert c.get("/finance/invoices", follow_redirects=False).status_code == 403


def test_assign_requires_a_rationale_and_a_selection(admin, db, made):
    role = _role(db, made, ["students.view"])
    u = _user(db, made, None)
    admin.post(f"/admin/roles/{role.id}/assign", data={"user_ids": [str(u.id)]}, follow_redirects=False)
    admin.post(f"/admin/roles/{role.id}/assign", data={"rationale": "x"}, follow_redirects=False)
    db.expire_all()
    assert db.get(User, u.id).role_id is None


def test_move_members_to_another_role(admin, db, made):
    old, new = _role(db, made, ["students.view"]), _role(db, made, ["clients.view"])
    u = _user(db, made, old)
    r = admin.post(f"/admin/roles/{old.id}/assign", data={"user_ids": [str(u.id)], "move_to": str(new.id), "rationale": "moved"},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(User, u.id).role_id == new.id


# ----------------------------------------------------------------------------- the rules
def test_nobody_changes_their_own_role(admin, db):
    me = db.query(User).filter(User.email == "admin@oqc.local").first()
    other = db.query(Role).filter(Role.slug == "accountant").first()
    before = me.role_id
    admin.post(f"/admin/users/{me.id}/edit", data={"full_name": me.full_name, "email": me.email, "role_id": str(other.id),
                                                  "rationale": "self"}, follow_redirects=False)
    admin.post(f"/admin/roles/{other.id}/assign", data={"user_ids": [str(me.id)], "rationale": "self"}, follow_redirects=False)
    db.expire_all()
    assert db.get(User, me.id).role_id == before


def test_a_limited_administrator_cannot_escalate(db, made):
    # an administrator who manages users and roles but holds no billing or payroll rights
    limited = _role(db, made, ["users.*", "roles.*", "students.view", "dashboard.view"])
    boss = _user(db, made, limited)
    rich = _role(db, made, ["students.view", "payroll.*"])
    victim = _user(db, made, None)
    c = _client(boss.email, PASSWORD)
    # 1. cannot hand out a role carrying permissions they do not hold
    c.post(f"/admin/roles/{rich.id}/assign", data={"user_ids": [str(victim.id)], "rationale": "x"}, follow_redirects=False)
    c.post(f"/admin/users/{victim.id}/edit", data={"full_name": victim.full_name, "email": victim.email,
                                                   "role_id": str(rich.id), "rationale": "x"}, follow_redirects=False)
    db.expire_all()
    assert db.get(User, victim.id).role_id is None
    # 2. cannot add such permissions to a role's matrix
    narrow = _role(db, made, ["students.view"])
    c.post(f"/admin/roles/{narrow.id}", data={"name": narrow.name, "portal": "admin", "rationale": "x",
                                              "p__students__view": "1", "p__payroll__approve": "1"}, follow_redirects=False)
    db.expire_all()
    assert "payroll.approve" not in role_svc.held_permissions(_user(db, made, db.get(Role, narrow.id)))
    # 3. cannot hand out the full-access role at all
    sa = db.query(Role).filter(Role.slug == "super_admin").first()
    c.post(f"/admin/roles/{sa.id}/assign", data={"user_ids": [str(victim.id)], "rationale": "x"}, follow_redirects=False)
    db.expire_all()
    assert db.get(User, victim.id).role_id is None
    # 4. but can assign a role within their own rights
    within = _role(db, made, ["students.view"])
    c.post(f"/admin/roles/{within.id}/assign", data={"user_ids": [str(victim.id)], "rationale": "ok"}, follow_redirects=False)
    db.expire_all()
    assert db.get(User, victim.id).role_id == within.id


def test_a_family_account_is_never_offered_or_given_a_staff_role(admin, db, made):
    staff_role = _role(db, made, ["students.view"])
    parent = db.query(User).filter(User.email == "parent1@oqc.local").first()
    before = parent.role_id
    page = admin.get(f"/admin/roles/{staff_role.id}").text
    assert "parent1@oqc.local" not in page.split('id="assign"')[1], "families are not listed for a staff role"
    admin.post(f"/admin/roles/{staff_role.id}/assign", data={"user_ids": [str(parent.id)], "rationale": "x"}, follow_redirects=False)
    db.expire_all()
    assert db.get(User, parent.id).role_id == before, "a family login must never become a staff login"
    staff = _user(db, made, staff_role)
    client_role = db.query(Role).filter(Role.slug == "client").first()
    assert role_svc.can_assign(db.query(User).filter(User.email == "admin@oqc.local").first(), staff, client_role)


def test_full_access_role_keeps_its_wildcard(admin, db):
    sa = db.query(Role).filter(Role.slug == "super_admin").first()
    before_name = sa.name
    admin.post(f"/admin/roles/{sa.id}", data={"name": before_name, "portal": sa.portal, "rationale": "probe",
                                              "description": "probe", "p__students__view": "1"}, follow_redirects=False)
    db.expire_all()
    sa = db.get(Role, sa.id)
    assert sa.permissions == ["*"], "saving the matrix must not narrow a full-access role"
    sa.description = None
    db.commit()


# ----------------------------------------------------------------------------- deploys
def test_deploys_keep_administrator_edits_and_apply_code_changes(db, monkeypatch):
    role = db.query(Role).filter(Role.slug == "accountant").first()
    original_perms = list(role.permissions or [])
    setting = db.query(Setting).filter(Setting.key == role_svc.DEFAULTS_KEY).first()
    original_store = dict(setting.value) if setting and isinstance(setting.value, dict) else None
    defaults = list(rbac.ROLE_DEFINITIONS["accountant"]["permissions"])
    try:
        role_svc.sync_system_roles(db)                      # make sure a baseline is recorded
        # an administrator removes one default and adds an extra grant
        dropped = defaults[0]
        role.permissions = [p for p in role.permissions if p != dropped] + ["students.view"]
        db.flush()
        # next deploy, unchanged code: the edits survive
        role_svc.sync_system_roles(db)
        assert dropped not in role.permissions and "students.view" in role.permissions
        # a later release adds a permission to the role and withdraws another default
        withdrawn = defaults[-1]
        monkeypatch.setitem(rbac.ROLE_DEFINITIONS["accountant"], "permissions",
                            [p for p in defaults if p != withdrawn] + ["kpis.view"])
        role_svc.sync_system_roles(db)
        assert "kpis.view" in role.permissions, "a permission the code adds reaches the role"
        assert withdrawn not in role.permissions, "a permission the code withdraws leaves the role"
        assert dropped not in role.permissions and "students.view" in role.permissions, "the administrator's edits remain"
    finally:
        db.rollback()
        role = db.query(Role).filter(Role.slug == "accountant").first()
        role.permissions = original_perms
        setting = db.query(Setting).filter(Setting.key == role_svc.DEFAULTS_KEY).first()
        if original_store is None and setting is not None:
            db.delete(setting)
        elif setting is not None:
            setting.value = original_store
        db.commit()


def test_seed_twice_changes_nothing(db):
    before = {r.slug: (list(r.permissions or []), r.portal) for r in db.query(Role).filter(Role.is_system.is_(True))}
    role_svc.sync_system_roles(db)
    role_svc.sync_system_roles(db)
    after = {r.slug: (list(r.permissions or []), r.portal) for r in db.query(Role).filter(Role.is_system.is_(True))}
    db.rollback()
    assert before == after


def test_normalise_slug():
    assert role_svc.normalise_slug("Front Desk-Officer!") == "front_desk_officer"
    assert role_svc.normalise_slug("  __HOD  QA__ ") == "hod_qa"
