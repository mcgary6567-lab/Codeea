"""Platform-wide tests: auth, RBAC, scoping, audit immutability, health, API.

Module-specific tests live alongside these; this file covers the cross-cutting guarantees
listed in SRS section 26 (Acceptance Criteria) that must never regress.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.core import rbac
from app.core.security import (create_access_token, decode_token, hash_password, verify_password,
                               generate_totp_secret, totp_now, verify_totp, mask, sign_value, verify_signed)
from app.models.core import AuditEvent, Role, User
from app.models.people import Client, Student


# --------------------------------------------------------------------------- health & docs
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_openapi_schema(client):
    r = client.get("/api/openapi.json")
    assert r.status_code == 200 and "paths" in r.json()


# --------------------------------------------------------------------------- security primitives
def test_password_hash_roundtrip():
    h = hash_password("Test@12345")
    assert verify_password("Test@12345", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip():
    token = create_access_token(1, jti="abc123")
    payload = decode_token(token)
    assert payload and payload["sub"] == "1" and payload["jti"] == "abc123"


def test_jwt_rejects_tampering():
    assert decode_token(create_access_token(1)[:-3] + "aaa") is None


def test_totp_roundtrip():
    secret = generate_totp_secret()
    assert verify_totp(secret, totp_now(secret))
    assert not verify_totp(secret, "000000")


def test_signed_values():
    signed = sign_value("42", "survey")
    assert verify_signed(signed, "survey") == "42"
    assert verify_signed(signed, "other") is None


def test_masking():
    assert mask("+447700900123").endswith("123")
    assert "*" in mask("+447700900123")
    assert mask("parent@example.com").endswith("@example.com")


# --------------------------------------------------------------------------- authentication
def test_anonymous_redirected_to_login(client):
    r = client.get("/profile", follow_redirects=False)
    assert r.status_code in (303, 307) and "/login" in r.headers.get("location", "")


def test_login_rejects_bad_credentials(client):
    r = client.post("/login", data={"username": "admin@oqc.local", "password": "nope"}, follow_redirects=False)
    assert r.status_code == 200 and "Invalid credentials" in r.text


def test_logout_ends_session(admin):
    assert admin.get("/profile", follow_redirects=False).status_code == 200
    admin.get("/logout", follow_redirects=False)
    r = admin.get("/profile", follow_redirects=False)
    assert r.status_code in (303, 307)


def test_api_token_flow(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin@oqc.local", "password": "Admin@12345"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["role"] == "super_admin"


def test_api_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 401


# --------------------------------------------------------------------------- RBAC
def test_every_role_has_a_portal_and_permissions(db):
    for slug, spec in rbac.ROLE_DEFINITIONS.items():
        role = db.query(Role).filter(Role.slug == slug).first()
        assert role is not None, f"role {slug} not seeded"
        assert role.portal in ("admin", "teacher", "client", "student")
        assert role.permissions, f"role {slug} has no permissions"


def test_permission_wildcards(db):
    admin = db.query(User).filter(User.email == "admin@oqc.local").first()
    assert rbac.has_permission(admin, "anything.delete")
    teacher = db.query(User).filter(User.email == "teacher1@oqc.local").first()
    assert rbac.has_permission(teacher, "portal_teacher.view")
    assert not rbac.has_permission(teacher, "payroll.approve")
    assert not rbac.has_permission(teacher, "users.delete")


def test_denied_permissions_win(db):
    teacher = db.query(User).filter(User.email == "teacher1@oqc.local").first()
    original = list(teacher.denied_permissions or [])
    teacher.denied_permissions = original + ["lesson_plans.update"]
    assert not rbac.has_permission(teacher, "lesson_plans.update")
    teacher.denied_permissions = original


def test_auditor_is_read_only(auditor):
    """External auditor may read but never mutate (SRS section 4)."""
    r = auditor.post("/alerts/1/resolve", follow_redirects=False)
    assert r.status_code in (403, 404), f"auditor was able to mutate: {r.status_code}"


def test_client_role_cannot_reach_admin_pages(parent):
    for path in ("/admin/users", "/hr/payroll", "/finance/invoices", "/students"):
        r = parent.get(path, follow_redirects=False)
        assert r.status_code in (303, 307, 403, 404), f"{path} leaked to a parent: {r.status_code}"


def test_teacher_cannot_reach_admin_pages(teacher):
    for path in ("/admin/users", "/admin/roles", "/hr/payroll", "/finance/invoices"):
        r = teacher.get(path, follow_redirects=False)
        assert r.status_code in (303, 307, 403, 404), f"{path} leaked to a teacher: {r.status_code}"


# --------------------------------------------------------------------------- record-level scoping
def test_parent_cannot_open_another_familys_student(parent, db):
    """No critical cross-role data leakage (SRS section 26)."""
    own = db.query(Client).filter(Client.user_id == db.query(User.id).filter(User.email == "parent1@oqc.local").scalar_subquery()).first()
    own_ids = {s.id for s in own.students} if own else set()
    foreign = db.query(Student).filter(~Student.id.in_(own_ids or {-1})).first()
    if foreign is None:
        pytest.skip("no foreign student in the dataset")
    r = parent.get(f"/students/{foreign.id}", follow_redirects=False)
    assert r.status_code in (303, 307, 403, 404), f"parent read a foreign student: {r.status_code}"


# --------------------------------------------------------------------------- audit trail
def test_login_is_audited(client, db):
    before = db.query(AuditEvent).filter(AuditEvent.action == "login").count()
    client.post("/login", data={"username": "manager@oqc.local", "password": "Manager@123"}, follow_redirects=False)
    db.expire_all()
    assert db.query(AuditEvent).filter(AuditEvent.action == "login").count() > before


def test_audit_events_carry_actor_and_timestamp(db):
    for ev in db.query(AuditEvent).order_by(AuditEvent.id.desc()).limit(25):
        assert ev.created_at is not None and isinstance(ev.created_at, datetime)
        assert ev.module and ev.action
        assert ev.actor_name is not None


def test_no_route_deletes_audit_events():
    """Audit log immutability (Module 48): no route may issue a delete against audit_events."""
    from app.main import app
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if "audit" in path and "DELETE" in methods:
            pytest.fail(f"audit deletion route exists: {path}")


# --------------------------------------------------------------------------- error handling
def test_404_page(client):
    r = client.get("/definitely-not-a-real-page")
    assert r.status_code == 404


def test_api_404_returns_json(client):
    r = client.get("/api/v1/not-a-real-endpoint")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
