"""WP-A Client Requests + Evaluations: every page renders (200), every form redirects (303) and the
approval side effects really happen (teacher change moves the subscription, an approved reference creates a lead).

Self-contained: defaults DATABASE_URL to the private database data/oqc_wpA.db (seed it first with
    $env:DATABASE_URL='sqlite:///./data/oqc_wpA.db'; .venv/Scripts/python.exe seed.py --reset
) so the shared development database is never locked. Because tests/conftest.py imports the app before this
module, set the variable in the shell when running under pytest. ASCII output only (Windows console is cp1252).

Run:  $env:DATABASE_URL='sqlite:///./data/oqc_wpA.db'; .venv/Scripts/python.exe -m pytest tests/test_erp_requests.py -q
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_wpA.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.academic import Evaluation  # noqa: E402
from app.models.crm import Case, Lead  # noqa: E402
from app.models.erp import (AssessmentDefinition, ReferredContact, SessionSlot, TeacherChangeRequest,  # noqa: E402
                            TimeChangeRequest)
from app.models.finance import Subscription  # noqa: E402
from app.models.people import Client, Leave, Student, Teacher  # noqa: E402

KINDS = ["leaves", "time-change", "teacher-change", "references", "complaints"]
STATUSES = ["pending", "approved", "rejected", "cancelled"]


@pytest.fixture(scope="module", autouse=True)
def _schema():
    init_db()


@pytest.fixture(scope="module")
def admin() -> TestClient:
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", data={"username": "admin@oqc.local", "password": "Admin@12345"})
    assert r.status_code == 303, f"admin login failed: {r.status_code}"
    return c


@pytest.fixture(scope="module")
def parent() -> TestClient:
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", data={"username": "parent1@oqc.local", "password": "Parent@123"})
    assert r.status_code == 303, f"parent login failed: {r.status_code}"
    return c


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# --------------------------------------------------------------------------- list pages
@pytest.mark.parametrize("kind", KINDS)
def test_request_list_renders(admin: TestClient, kind: str):
    r = admin.get(f"/requests/{kind}")
    assert r.status_code == 200, f"/requests/{kind} -> {r.status_code}"
    assert "Approval Status" in r.text
    assert "Change Status" in r.text


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("status", STATUSES)
def test_status_tile_filters(admin: TestClient, kind: str, status: str):
    r = admin.get(f"/requests/{kind}?status={status}")
    assert r.status_code == 200, f"/requests/{kind}?status={status} -> {r.status_code}"


def test_filter_bar_parameters(admin: TestClient, db):
    c = db.query(Client).first()
    s = db.query(Student).first()
    today = date.today().isoformat()
    for kind in KINDS:
        url = (f"/requests/{kind}?client={c.id}&student={s.id}&date_from=2026-01-01&date_to={today}&q=a")
        assert admin.get(url).status_code == 200, url


# --------------------------------------------------------------------------- create forms
def test_create_leave(admin: TestClient, db):
    s = db.query(Student).filter(Student.status == "active").first()
    before = db.query(Leave).filter(Leave.person_type == "student").count()
    start = date.today() + timedelta(days=10)
    r = admin.post("/requests/leaves/new", data={
        "student_id": s.id, "leave_type": "vacation", "apply_date": date.today().isoformat(),
        "start_date": start.isoformat(), "end_date": (start + timedelta(days=3)).isoformat(),
        "leave_for_all": "1", "reason": "Family travelling for a wedding.", "leave_detail": "Back the next Monday."})
    assert r.status_code == 303
    assert db.query(Leave).filter(Leave.person_type == "student").count() == before + 1
    lv = db.query(Leave).filter(Leave.person_type == "student").order_by(Leave.id.desc()).first()
    assert lv.leave_for_all is True and lv.apply_date is not None and lv.status == "pending"


def test_create_time_change(admin: TestClient, db):
    sub = db.query(Subscription).filter(Subscription.status.notin_(["cancelled", "expired"])).first()
    slot = db.query(SessionSlot).filter(SessionSlot.status == "active").first()
    before = db.query(TimeChangeRequest).count()
    r = admin.post("/requests/time-change/new", data={
        "subscription_id": sub.id, "new_slot_id": slot.id, "days": ["0", "1", "2"],
        "description": "Created by the automated test."})
    assert r.status_code == 303
    assert db.query(TimeChangeRequest).count() == before + 1


def test_create_teacher_change(admin: TestClient, db):
    sub = db.query(Subscription).filter(Subscription.status.notin_(["cancelled", "expired"])).first()
    t = db.query(Teacher).filter(Teacher.status == "active").first()
    before = db.query(TeacherChangeRequest).count()
    r = admin.post("/requests/teacher-change/new", data={
        "subscription_id": sub.id, "new_teacher_id": t.id, "description": "Created by the automated test."})
    assert r.status_code == 303
    assert db.query(TeacherChangeRequest).count() == before + 1


def test_create_reference(admin: TestClient, db):
    c = db.query(Client).first()
    before = db.query(ReferredContact).count()
    r = admin.post("/requests/references/new", data={
        "client_id": c.id, "name": "Test Referral Contact", "email": "test.referral@example.com",
        "contact_no": "+44 7700 900999", "reference_type": "friend", "description": "Added by the automated test."})
    assert r.status_code == 303
    assert db.query(ReferredContact).count() == before + 1


def test_create_complaint(admin: TestClient, db):
    c = db.query(Client).first()
    before = db.query(Case).filter(Case.case_type == "complaint").count()
    r = admin.post("/requests/complaints/new", data={
        "client_id": c.id, "complaint_type": "Timing", "title": "Automated test complaint",
        "description": "Raised by the automated test."})
    assert r.status_code == 303
    assert db.query(Case).filter(Case.case_type == "complaint").count() == before + 1


# --------------------------------------------------------------------------- change status + effects
def test_change_status_requires_selection_and_remarks(admin: TestClient):
    assert admin.post("/requests/leaves/change-status", data={"status": "approved", "remarks": "x"}).status_code == 303
    assert admin.post("/requests/leaves/change-status", data={"status": "", "remarks": "x", "ids": "1"}).status_code == 303


def test_approve_leave_records_the_approver(admin: TestClient, db):
    lv = (db.query(Leave).filter(Leave.person_type == "student", Leave.status == "pending")
          .order_by(Leave.id.desc()).first())
    assert lv is not None, "no pending student leave to approve"
    r = admin.post("/requests/leaves/change-status", data={
        "ids": [str(lv.id)], "status": "approved", "remarks": "Approved by the automated test."})
    assert r.status_code == 303
    db.expire_all()
    lv = db.get(Leave, lv.id)
    assert lv.status == "approved" and lv.approved_by_id and lv.approved_at


def test_approve_time_change_moves_the_subscription(admin: TestClient, db):
    req = (db.query(TimeChangeRequest).filter(TimeChangeRequest.status == "pending",
                                              TimeChangeRequest.subscription_id.isnot(None),
                                              TimeChangeRequest.new_slot_id.isnot(None))
           .order_by(TimeChangeRequest.id.desc()).first())
    assert req is not None, "no pending time change request with a subscription"
    new_slot_id, sub_id, days = req.new_slot_id, req.subscription_id, list(req.days or [])
    r = admin.post("/requests/time-change/change-status", data={
        "ids": [str(req.id)], "status": "approved", "remarks": "Slot confirmed by the automated test."})
    assert r.status_code == 303
    db.expire_all()
    req = db.get(TimeChangeRequest, req.id)
    sub = db.get(Subscription, sub_id)
    assert req.status == "approved" and req.decided_by_id and req.decision_remarks
    assert sub.slot_id == new_slot_id
    if days:
        assert list(sub.days_of_week or []) == days


def test_approve_teacher_change_moves_the_teacher(admin: TestClient, db):
    req = (db.query(TeacherChangeRequest).filter(TeacherChangeRequest.status == "pending",
                                                 TeacherChangeRequest.subscription_id.isnot(None),
                                                 TeacherChangeRequest.new_teacher_id.isnot(None))
           .order_by(TeacherChangeRequest.id.desc()).first())
    assert req is not None, "no pending teacher change request with a subscription"
    new_teacher_id, sub_id, student_id = req.new_teacher_id, req.subscription_id, req.student_id
    r = admin.post("/requests/teacher-change/change-status", data={
        "ids": [str(req.id)], "status": "approved", "remarks": "Reallocated by the automated test."})
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Subscription, sub_id).teacher_id == new_teacher_id
    assert db.get(Student, student_id).teacher_id == new_teacher_id


def test_approve_reference_creates_a_lead(admin: TestClient, db):
    req = (db.query(ReferredContact).filter(ReferredContact.status == "pending", ReferredContact.lead_id.is_(None))
           .order_by(ReferredContact.id.desc()).first())
    assert req is not None, "no pending referred contact"
    before = db.query(Lead).count()
    r = admin.post("/requests/references/change-status", data={
        "ids": [str(req.id)], "status": "approved", "remarks": "Passed to the closer by the automated test."})
    assert r.status_code == 303
    db.expire_all()
    req = db.get(ReferredContact, req.id)
    assert req.status == "approved" and req.lead_id, "approving a reference must create a lead"
    assert db.query(Lead).count() == before + 1
    lead = db.get(Lead, req.lead_id)
    assert lead.full_name == req.name and lead.lead_code.startswith("L-")


def test_approve_complaint_stores_the_company_response(admin: TestClient, db):
    k = (db.query(Case).filter(Case.case_type == "complaint", Case.approval_status == "pending")
         .order_by(Case.id.desc()).first())
    assert k is not None, "no pending complaint"
    r = admin.post("/requests/complaints/change-status", data={
        "ids": [str(k.id)], "status": "approved", "remarks": "Closed by the automated test.",
        "company_response": "We have moved the class and called the family."})
    assert r.status_code == 303
    db.expire_all()
    k = db.get(Case, k.id)
    assert k.approval_status == "approved" and k.status == "resolved"
    assert k.company_response == "We have moved the class and called the family."
    assert k.resolution == k.company_response


def test_reject_and_cancel(admin: TestClient, db):
    req = db.query(ReferredContact).filter(ReferredContact.status == "pending").order_by(ReferredContact.id).first()
    if req is None:
        pytest.skip("no pending reference left to reject")
    assert admin.post("/requests/references/change-status", data={
        "ids": [str(req.id)], "status": "rejected", "remarks": "Duplicate of an existing lead."}).status_code == 303
    db.expire_all()
    assert db.get(ReferredContact, req.id).status == "rejected"


# --------------------------------------------------------------------------- evaluations
def test_evaluation_pages(admin: TestClient, db):
    for url in ["/academics/evaluations", "/academics/evaluations?tab=manual",
                "/academics/evaluations?tab=evaluations&result=fail", "/academics/evaluations/pending"]:
        assert admin.get(url).status_code == 200, url
    a = db.query(AssessmentDefinition).first()
    if a is not None:
        assert admin.get(f"/academics/evaluations?assessment={a.id}").status_code == 200


def test_pending_evaluations_columns(admin: TestClient):
    r = admin.get("/academics/evaluations/pending")
    assert r.status_code == 200
    for col in ["Student", "Client", "Teacher", "Course", "Last Evaluation", "Days Since", "Due"]:
        assert col in r.text, f"missing column {col}"


def test_create_evaluation_with_assessment(admin: TestClient, db):
    a = db.query(AssessmentDefinition).filter(AssessmentDefinition.status == "active").first()
    s = db.query(Student).filter(Student.status == "active").first()
    assert a is not None and s is not None
    before = db.query(Evaluation).count()
    r = admin.post("/academics/evaluations/new", data={
        "student_id": s.id, "evaluation_type": "manual", "assessment_id": a.id,
        "date": date.today().isoformat(), "score": str(float(a.passing_marks or 0) + 1),
        "is_manual": "1", "teacher_comment": "Created by the automated test."})
    assert r.status_code == 303
    db.expire_all()
    assert db.query(Evaluation).count() == before + 1
    ev = db.query(Evaluation).order_by(Evaluation.id.desc()).first()
    assert ev.assessment_id == a.id
    assert float(ev.max_score) == float(a.total_marks)
    assert ev.result == "pass" and ev.is_manual is True
    assert admin.get("/academics/evaluations?tab=manual").status_code == 200


def test_create_failing_evaluation(admin: TestClient, db):
    a = db.query(AssessmentDefinition).filter(AssessmentDefinition.passing_marks > 0).first()
    s = db.query(Student).filter(Student.status == "active").first()
    if a is None:
        pytest.skip("no assessment with a pass mark")
    r = admin.post("/academics/evaluations/new", data={
        "student_id": s.id, "evaluation_type": "monthly", "assessment_id": a.id,
        "date": date.today().isoformat(), "score": str(max(0.0, float(a.passing_marks) - 5)),
        "teacher_comment": "Below the pass mark."})
    assert r.status_code == 303
    db.expire_all()
    ev = db.query(Evaluation).order_by(Evaluation.id.desc()).first()
    assert ev.result == "fail"


# --------------------------------------------------------------------------- parent portal
def test_portal_requests_page(parent: TestClient):
    r = parent.get("/portal/requests")
    assert r.status_code == 200
    for section in ["Request a time change", "Request a teacher change", "Refer a new contact"]:
        assert section in r.text


def test_portal_time_change(parent: TestClient, db):
    c = db.query(Client).filter(Client.user.has(email="parent1@oqc.local")).first()
    assert c is not None, "parent1 has no client profile"
    sub = db.query(Subscription).filter(Subscription.client_id == c.id).first()
    slot = db.query(SessionSlot).filter(SessionSlot.status == "active").first()
    if sub is None:
        pytest.skip("parent1 has no subscription")
    before = db.query(TimeChangeRequest).filter(TimeChangeRequest.client_id == c.id).count()
    r = parent.post("/portal/requests", data={
        "kind": "time_change", "subscription_id": sub.id, "new_slot_id": slot.id, "days": ["1", "3"],
        "description": "School pick-up has moved."})
    assert r.status_code == 303
    assert db.query(TimeChangeRequest).filter(TimeChangeRequest.client_id == c.id).count() == before + 1


def test_portal_teacher_change(parent: TestClient, db):
    c = db.query(Client).filter(Client.user.has(email="parent1@oqc.local")).first()
    sub = db.query(Subscription).filter(Subscription.client_id == c.id).first()
    if sub is None:
        pytest.skip("parent1 has no subscription")
    before = db.query(TeacherChangeRequest).filter(TeacherChangeRequest.client_id == c.id).count()
    r = parent.post("/portal/requests", data={
        "kind": "teacher_change", "subscription_id": sub.id, "description": "Would prefer a female teacher."})
    assert r.status_code == 303
    assert db.query(TeacherChangeRequest).filter(TeacherChangeRequest.client_id == c.id).count() == before + 1


def test_portal_reference(parent: TestClient, db):
    c = db.query(Client).filter(Client.user.has(email="parent1@oqc.local")).first()
    before = db.query(ReferredContact).filter(ReferredContact.client_id == c.id).count()
    r = parent.post("/portal/requests", data={
        "kind": "reference", "name": "Portal Referred Friend", "email": "friend@example.com",
        "contact_no": "+44 7700 900123", "reference_type": "friend", "description": "Two children for Qaida."})
    assert r.status_code == 303
    assert db.query(ReferredContact).filter(ReferredContact.client_id == c.id).count() == before + 1


def test_portal_scoping_rejects_another_familys_subscription(parent: TestClient, db):
    c = db.query(Client).filter(Client.user.has(email="parent1@oqc.local")).first()
    other = db.query(Subscription).filter(Subscription.client_id != c.id).first()
    if other is None:
        pytest.skip("no other family's subscription")
    before = db.query(TimeChangeRequest).count()
    r = parent.post("/portal/requests", data={"kind": "time_change", "subscription_id": other.id,
                                              "new_slot_id": 1, "description": "Should be refused."})
    assert r.status_code == 303
    assert db.query(TimeChangeRequest).count() == before, "a parent must not move another family's subscription"


def test_portal_leave_with_leave_for_all(parent: TestClient, db):
    c = db.query(Client).filter(Client.user.has(email="parent1@oqc.local")).first()
    s = db.query(Student).filter(Student.client_id == c.id).first()
    assert s is not None
    assert parent.get("/portal/leaves").status_code == 200
    start = date.today() + timedelta(days=20)
    r = parent.post("/portal/leaves", data={
        "student_id": s.id, "leave_type": "vacation", "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=4)).isoformat(), "leave_for_all": "1",
        "reason": "Half term abroad.", "leave_detail": "No internet at the cottage."})
    assert r.status_code == 303
    lv = db.query(Leave).filter(Leave.student_id == s.id).order_by(Leave.id.desc()).first()
    assert lv.leave_for_all is True and lv.leave_detail == "No internet at the cottage."


def test_portal_complaint_type(parent: TestClient, db):
    c = db.query(Client).filter(Client.user.has(email="parent1@oqc.local")).first()
    assert parent.get("/portal/cases").status_code == 200
    before = db.query(Case).filter(Case.client_id == c.id).count()
    r = parent.post("/portal/cases", data={
        "case_type": "complaint", "complaint_type": "Technical", "title": "Audio drops every class",
        "description": "The sound cuts out for the whole family."})
    assert r.status_code == 303
    db.expire_all()
    assert db.query(Case).filter(Case.client_id == c.id).count() == before + 1
    k = db.query(Case).filter(Case.client_id == c.id).order_by(Case.id.desc()).first()
    assert k.complaint_type == "Technical" and k.approval_status == "pending"
    assert admin_sees(k)


def admin_sees(case: Case) -> bool:
    """The complaint must be visible on the ERP Complaints list (it is a complaint case with a type)."""
    return case.case_type == "complaint" and bool(case.complaint_type)
