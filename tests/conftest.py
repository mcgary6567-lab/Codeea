"""Shared pytest fixtures. The suite runs against the seeded development database."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal, init_db
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _schema():
    init_db()


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _login(c: TestClient, username: str, password: str) -> TestClient:
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}: {r.status_code}"
    return c


@pytest.fixture()
def admin(client):
    return _login(client, "admin@oqc.local", "Admin@12345")


@pytest.fixture()
def teacher(client):
    return _login(client, "teacher1@oqc.local", "Teacher@123")


@pytest.fixture()
def parent(client):
    return _login(client, "parent1@oqc.local", "Parent@123")


@pytest.fixture()
def student(client):
    return _login(client, "student1@oqc.local", "Student@123")


@pytest.fixture()
def auditor(client):
    return _login(client, "auditor@oqc.local", "Auditor@123")


@pytest.fixture()
def supervisor(client):
    return _login(client, "supervisor@oqc.local", "Super@123")


def purge_cases(session, ids) -> None:
    """Delete cases a test created, with what hangs off them, so repeated runs leave nothing behind."""
    from app.models.core import Notification, RiskAlert
    from app.models.crm import Case, CaseComment, Feedback, ParentContact
    from app.models.ops import Task
    ids = list(ids)
    if not ids:
        return
    session.query(CaseComment).filter(CaseComment.case_id.in_(ids)).delete(synchronize_session=False)
    session.query(ParentContact).filter(ParentContact.case_id.in_(ids)).delete(synchronize_session=False)
    session.query(Feedback).filter(Feedback.case_id.in_(ids)).update({Feedback.case_id: None}, synchronize_session=False)
    session.query(Task).filter(Task.entity_type == "Case", Task.entity_id.in_(ids)).delete(synchronize_session=False)
    session.query(RiskAlert).filter(RiskAlert.entity_type == "Case", RiskAlert.entity_id.in_(ids)).delete(synchronize_session=False)
    for cid in ids:
        session.query(Notification).filter(Notification.link.like(f"%/cases/{cid}")).delete(synchronize_session=False)
    session.query(Case).filter(Case.repeat_of_id.in_(ids)).update({Case.repeat_of_id: None}, synchronize_session=False)
    session.query(Case).filter(Case.id.in_(ids)).delete(synchronize_session=False)
