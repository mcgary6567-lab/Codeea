"""Global security from the 6 Nov 2026 QA change request: Admin vs non-admin, on the screens and through URLs and the API.

Admin = superuser, CEO, roles that may configure the system (rbac.is_admin). Everyone else cannot delete, import,
export or download, and sees sensitive data masked. The seeded Manager (management, not Admin) is the non-admin.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core import rbac
from app.database import SessionLocal
from app.main import app
from app.models.core import User

TAG = f"sec06-{uuid4().hex[:6]}"


def _login(email: str, password: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/login", data={"username": email, "password": password}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture(scope="module")
def admin():
    return _login("admin@oqc.local", "Admin@12345")


@pytest.fixture(scope="module")
def manager():
    return _login("manager@oqc.local", "Manager@123")


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _user(db, email):
    return db.query(User).filter(User.email == email).one()


# --------------------------------------------------------------------------- who is Admin
def test_admin_is_superuser_ceo_or_a_configuring_role(db):
    assert rbac.is_admin(_user(db, "admin@oqc.local"))
    manager = _user(db, "manager@oqc.local")
    assert rbac.is_management(manager) and not rbac.is_admin(manager), "management alone is not Admin"
    assert not rbac.is_admin(_user(db, "teacher1@oqc.local"))


# --------------------------------------------------------------------------- delete
def test_only_admins_hold_delete_whatever_the_role_wildcard_says(db):
    manager = _user(db, "manager@oqc.local")
    assert rbac.matches_any(tuple(manager.role.permissions), "tasks.delete"), "the role's tasks.* covers delete"
    assert not rbac.has_permission(manager, "tasks.delete")
    assert not rbac.has_permission(manager, "clients.delete")
    assert rbac.has_permission(manager, "tasks.update"), "other actions are untouched"
    assert rbac.has_permission(manager, "notifications.delete"), "clearing your own notifications is not deleting a record"
    assert rbac.has_permission(_user(db, "admin@oqc.local"), "tasks.delete")


def test_a_non_admin_delete_by_url_is_refused_and_the_record_stays(manager, admin, db):
    from app.models.ops import Task
    t = Task(title=f"{TAG} keep me", creator_id=_user(db, "manager@oqc.local").id)
    db.add(t)
    db.commit()
    tid = t.id
    try:
        r = manager.post(f"/tasks/{tid}/delete", follow_redirects=False)
        assert r.status_code == 403
        db.expire_all()
        assert db.get(Task, tid) is not None
        r = admin.post(f"/tasks/{tid}/delete", follow_redirects=False)
        assert r.status_code in (302, 303)
        db.expire_all()
        assert db.get(Task, tid) is None
        from app.models.core import AuditEvent
        assert db.query(AuditEvent).filter(AuditEvent.action == "delete", AuditEvent.entity_type == "Task",
                                           AuditEvent.entity_id == tid,
                                           AuditEvent.description.contains(TAG)).count() == 1, "an Admin delete is audited"
    finally:
        db.rollback()
        left = db.get(Task, tid)
        if left is not None:
            db.delete(left)
            db.commit()


# --------------------------------------------------------------------------- export / download
@pytest.mark.parametrize("url", ["/reports/students_status/export?format=csv", "/reports/students_status/export?format=xlsx",
                                 "/finance/clients-summary?format=csv"])
def test_exports_and_downloads_are_admin_only_even_by_url(manager, admin, url):
    refused = manager.get(url, follow_redirects=False)
    assert refused.status_code == 403 and "limited to admins" in refused.text
    ok = admin.get(url, follow_redirects=False)
    assert ok.status_code == 200 and ok.headers["content-type"].startswith(("text/csv", "application/vnd.openxml"))


def test_the_actions_menu_and_export_links_are_gone_for_non_admins(manager, admin):
    assert "data-ir-no-export" in manager.get("/students").text
    assert "data-ir-no-export" not in admin.get("/students").text
    assert manager.post("/ir/export", json={"headers": ["a"], "rows": [["1"]], "format": "csv"}).status_code == 403


def test_exports_switch_is_yes(db):
    from app.services.scheduling import setting_value
    assert setting_value(db, "exports_admin_only", None) is True


# --------------------------------------------------------------------------- import
def test_imports_are_admin_only(manager, db):
    from app.services.ir import can_import
    assert can_import(_user(db, "admin@oqc.local"))
    assert not can_import(_user(db, "manager@oqc.local"))
    r = manager.post("/admin/migration/upload", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}, follow_redirects=False)
    assert r.status_code == 403


# --------------------------------------------------------------------------- API
def test_the_api_serves_admins_only(admin):
    def token(email, password):
        c = TestClient(app)
        r = c.post("/api/v1/auth/login", json={"username": email, "password": password})
        assert r.status_code == 200, r.text
        return r.json()["access_token"]
    c = TestClient(app)
    refused = c.get("/api/v1/people/clients", headers={"Authorization": f"Bearer {token('manager@oqc.local', 'Manager@123')}"})
    assert refused.status_code == 403 and "admins" in refused.json()["detail"]
    ok = c.get("/api/v1/people/clients", headers={"Authorization": f"Bearer {token('admin@oqc.local', 'Admin@12345')}"})
    assert ok.status_code == 200
    assert c.get("/api/v1/people/clients").status_code == 401, "no credentials is still 401"


# --------------------------------------------------------------------------- masking
def test_non_admins_see_client_and_lead_contacts_masked(manager, admin, db):
    from app.models.crm import Lead
    from app.models.people import Client
    from app.services import contact_reveal
    if contact_reveal.otp_required(db, _user(db, "admin@oqc.local")):
        pytest.skip("OTP is on in this database, so even the Admin sees masks")
    c = db.query(Client).filter(Client.email.isnot(None), Client.email != "").order_by(Client.id.desc()).first()
    assert c.email in admin.get(f"/clients/{c.id}").text
    page = manager.get(f"/clients/{c.id}").text
    assert c.email not in page and f'data-reveal="/clients/{c.id}/reveal/email"' in page
    lead = db.query(Lead).filter(Lead.phone.isnot(None), Lead.phone != "").order_by(Lead.id.desc()).first()
    if lead is not None:
        page = manager.get(f"/crm/leads/{lead.id}").text
        assert lead.phone not in page and f"/crm/leads/{lead.id}/reveal/phone" in page
        shown = manager.get(f"/crm/leads/{lead.id}/reveal/phone")
        assert shown.status_code == 200 and lead.phone in shown.text, "View opens it, audited"


def test_a_masked_value_saved_back_keeps_the_stored_one():
    from app.core.security import mask, unmasked
    assert unmasked(mask("+447700900123"), "+447700900123") == "+447700900123"
    assert unmasked("+447700900999", "+447700900123") == "+447700900999"
    assert unmasked("", None) == ""


def test_employee_sensitive_fields_are_admin_only(db):
    from app.web.hr import can_see_sensitive
    assert can_see_sensitive(_user(db, "admin@oqc.local"))
    assert not can_see_sensitive(_user(db, "manager@oqc.local"))
