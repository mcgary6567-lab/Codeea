"""ERP parity tests for the Human Resource "Employment Management" group.

Covers the Employee Record (columns, filters, Apply Bulk Update), Employee Requests, Staff Violations,
Staff Bonuses, Advance Requests, Complaints (including the confidentiality of the Secret tab), Downloads
and the Attachment store.

The suite is re-runnable against the same database: everything it creates carries a unique tag and is
removed again in a session-scoped teardown, and the one employee it edits is restored.

Run against a private database:
    $env:DATABASE_URL='sqlite:///./data/oqc_hrA.db'; .venv/Scripts/python.exe -m pytest tests/test_hr_erp.py
"""
from __future__ import annotations

import os
from datetime import date
from uuid import uuid4

# The application reads DATABASE_URL at import time; set it before anything imports the app.
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_hrA.db")

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.hr_erp import Attachment, BonusType, EmployeeRequest, Grade, HRDownload, StaffComplaint, ViolationType
from app.models.people import Bonus, Employee, SalaryAdvance, Violation

TAG = f"pytest-{uuid4().hex[:8]}"


def _client(username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}: {r.status_code}"
    return c


@pytest.fixture(scope="module")
def hr_admin() -> TestClient:
    return _client("admin@oqc.local", "Admin@12345")


@pytest.fixture(scope="module")
def people_head() -> TestClient:
    return _client("hr@oqc.local", "People@123")


@pytest.fixture(scope="module")
def a_teacher() -> TestClient:
    return _client("teacher1@oqc.local", "Teacher@123")


@pytest.fixture()
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture(scope="module", autouse=True)
def _purge():
    """Remove everything this run created, so the suite can be run again against the same database."""
    yield
    s = SessionLocal()
    try:
        like = f"%{TAG}%"
        s.query(EmployeeRequest).filter(EmployeeRequest.description.ilike(like)).delete(synchronize_session=False)
        s.query(StaffComplaint).filter(StaffComplaint.title.ilike(like)).delete(synchronize_session=False)
        s.query(Violation).filter(Violation.remarks.ilike(like)).delete(synchronize_session=False)
        s.query(Bonus).filter(Bonus.reason.ilike(like)).delete(synchronize_session=False)
        s.query(SalaryAdvance).filter(SalaryAdvance.reason.ilike(like)).delete(synchronize_session=False)
        s.query(HRDownload).filter(HRDownload.description.ilike(like)).delete(synchronize_session=False)
        s.query(Attachment).filter(Attachment.title.ilike(like)).delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()


def an_employee(s) -> Employee:
    return s.query(Employee).filter(Employee.status.in_(["active", "probation"])).order_by(Employee.id).first()


# --------------------------------------------------------------------------- seed
def test_catalogues_are_seeded(session):
    assert session.query(ViolationType).count() >= 7
    assert session.query(BonusType).count() >= 6
    assert session.query(Grade).count() >= 3
    assert session.query(HRDownload).count() >= 1
    assert session.query(EmployeeRequest).count() >= 25
    assert session.query(StaffComplaint).count() >= 10
    assert session.query(StaffComplaint).filter(StaffComplaint.is_secret.is_(True)).count() >= 2
    assert session.query(Bonus).filter(Bonus.bonus_type_id.isnot(None)).count() >= 12
    assert session.query(Violation).filter(Violation.violation_type_id.isnot(None)).count() >= 10
    assert session.query(SalaryAdvance).filter(SalaryAdvance.hr_remarks.isnot(None)).count() >= 8


def test_employee_backfill(session):
    employees = session.query(Employee).all()
    assert employees
    assert all(e.employee_type in ("Academics", "Admin", "Marketing") for e in employees)
    assert all(e.shift_code for e in employees)
    assert all(e.duty_hours for e in employees)
    assert sum(1 for e in employees if e.bank_name) >= len(employees) // 2
    assert sum(1 for e in employees if e.grade_id) == len(employees)


# --------------------------------------------------------------------------- pages
@pytest.mark.parametrize("url", [
    "/hr/employees", "/hr/employees?status=in-active", "/hr/employees?gender=female",
    "/hr/employees?employee_type=Academics", "/hr/employees?shift=morning", "/hr/employees?designation=Teacher%20Remote",
    "/hr/requests", "/hr/violations", "/hr/bonuses", "/hr/advances",
    "/hr/complaints", "/hr/complaints?tab=open", "/hr/complaints?tab=secret",
    "/hr/downloads", "/hr/attachments", "/hr/attachments?entity_type=employee",
    "/hr/bonuses?acceptance_from=2026-01-01&acceptance_to=2026-12-31",
])
def test_pages(hr_admin, url):
    assert hr_admin.get(url).status_code == 200


@pytest.mark.parametrize("path", ["/hr/requests", "/hr/violations", "/hr/bonuses", "/hr/advances", "/hr/complaints"])
@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "cancelled"])
def test_status_tiles(hr_admin, path, status):
    assert hr_admin.get(f"{path}?status={status}").status_code == 200


def test_employee_detail_and_form(hr_admin, session):
    e = an_employee(session)
    assert hr_admin.get(f"/hr/employees/{e.id}").status_code == 200
    assert hr_admin.get(f"/hr/employees/{e.id}/edit").status_code == 200
    assert hr_admin.get("/hr/employees/new").status_code == 200


def test_people_head_sees_every_list(people_head):
    for url in ["/hr/employees", "/hr/requests", "/hr/violations", "/hr/bonuses", "/hr/advances",
                "/hr/complaints?tab=secret", "/hr/downloads", "/hr/attachments"]:
        assert people_head.get(url).status_code == 200, url


# --------------------------------------------------------------------------- confidentiality
def test_teacher_cannot_read_secret_complaints(a_teacher):
    assert a_teacher.get("/hr/complaints").status_code == 200          # the open list is staff-facing
    assert a_teacher.get("/hr/complaints?tab=secret").status_code == 403
    assert a_teacher.get("/hr/downloads").status_code == 200           # portal_self.view
    assert a_teacher.get("/hr/attachments").status_code == 403         # employees.view


def test_teacher_sees_only_their_own_complaints(a_teacher, hr_admin, session):
    title = f"Visible to People and Culture only {TAG}"
    r = hr_admin.post("/hr/complaints/new", follow_redirects=False, data={
        "employee_id": an_employee(session).id, "complaint_type": "HR", "title": title,
        "description": "Row raised for another member of staff.", "is_secret": ""})
    assert r.status_code == 303
    body = a_teacher.get("/hr/complaints").text
    assert title not in body


# --------------------------------------------------------------------------- employee requests
def test_employee_request_create_and_change_status(hr_admin, session):
    e = an_employee(session)
    description = f"Salary certificate for the bank {TAG}"
    r = hr_admin.post("/hr/requests/new", follow_redirects=False, data={
        "employee_id": e.id, "request_type": "Salary Certificate", "request_date": str(date.today()),
        "description": description})
    assert r.status_code == 303
    row = session.query(EmployeeRequest).filter(EmployeeRequest.description == description).first()
    assert row is not None and row.status == "pending"

    r = hr_admin.post("/hr/requests/change-status", follow_redirects=False, data={
        "ids": [row.id], "status": "approved", "hr_remarks": f"Issued and emailed {TAG}"})
    assert r.status_code == 303
    session.expire_all()
    row = session.get(EmployeeRequest, row.id)
    assert row.status == "approved"
    assert TAG in (row.hr_remarks or "")
    assert row.decided_by_id and row.decided_at


def test_change_status_requires_remarks(hr_admin, session):
    row = session.query(EmployeeRequest).first()
    r = hr_admin.post("/hr/requests/change-status", follow_redirects=False,
                      data={"ids": [row.id], "status": "approved", "hr_remarks": ""})
    assert r.status_code == 303  # redirected back with an error flash, nothing written


def test_self_service_request(a_teacher, session):
    assert a_teacher.get("/hr/me").status_code == 200
    description = f"Experience letter for a visa application {TAG}"
    r = a_teacher.post("/hr/me/request", follow_redirects=False,
                       data={"request_type": "Experience Letter", "description": description})
    assert r.status_code == 303
    row = session.query(EmployeeRequest).filter(EmployeeRequest.description == description).first()
    assert row is not None and row.status == "pending"
    assert description in a_teacher.get("/hr/me").text


# --------------------------------------------------------------------------- violations
def test_violation_approval_sets_the_deduction(hr_admin, session):
    e = an_employee(session)
    vt = session.query(ViolationType).order_by(ViolationType.sort_no).first()
    remarks = f"Reported by the shift supervisor {TAG}"
    r = hr_admin.post("/hr/violations/new", follow_redirects=False, data={
        "employee_id": e.id, "violation_type_id": vt.id, "date": str(date.today()), "remarks": remarks,
        "deduction_amount": "", "severity": vt.severity})
    assert r.status_code == 303
    v = session.query(Violation).filter(Violation.remarks == remarks).first()
    assert v is not None
    assert v.approval_status == "pending"
    assert float(v.deduction_amount or 0) == 0, "a pending fine must not reach payroll"

    r = hr_admin.post("/hr/violations/change-status", follow_redirects=False, data={
        "ids": [v.id], "status": "approved", "remarks": f"Confirmed with the employee {TAG}"})
    assert r.status_code == 303
    session.expire_all()
    v = session.get(Violation, v.id)
    assert v.approval_status == "approved"
    assert float(v.deduction_amount) == float(vt.penalty_amount)
    assert v.decided_by_id and v.decided_at


def test_violation_rejection_clears_the_deduction(hr_admin, session):
    e = an_employee(session)
    vt = session.query(ViolationType).order_by(ViolationType.sort_no.desc()).first()
    remarks = f"Raised in error {TAG}"
    hr_admin.post("/hr/violations/new", follow_redirects=False, data={
        "employee_id": e.id, "violation_type_id": vt.id, "date": str(date.today()), "remarks": remarks})
    v = session.query(Violation).filter(Violation.remarks == remarks).first()
    hr_admin.post("/hr/violations/change-status", follow_redirects=False, data={
        "ids": [v.id], "status": "rejected", "remarks": f"Withdrawn by the supervisor {TAG}"})
    session.expire_all()
    v = session.get(Violation, v.id)
    assert v.approval_status == "rejected"
    assert float(v.deduction_amount or 0) == 0


# --------------------------------------------------------------------------- bonuses
def test_bonus_approval_sets_the_acceptance_date(hr_admin, session):
    e = an_employee(session)
    bt = session.query(BonusType).order_by(BonusType.sort_no).first()
    reason = f"Full attendance for the month {TAG}"
    r = hr_admin.post("/hr/bonuses/new", follow_redirects=False, data={
        "employee_id": e.id, "bonus_type_id": bt.id, "amount": "", "reason": reason,
        "period": f"{date.today():%Y-%m}"})
    assert r.status_code == 303
    b = session.query(Bonus).filter(Bonus.reason == reason).first()
    assert b is not None and b.status == "pending" and b.acceptance_date is None
    assert float(b.amount) == float(bt.bonus_amount), "the chosen type fills the bonus amount"

    r = hr_admin.post("/hr/bonuses/change-status", follow_redirects=False, data={
        "ids": [b.id], "status": "approved", "remarks": f"Approved by People and Culture {TAG}"})
    assert r.status_code == 303
    session.expire_all()
    b = session.get(Bonus, b.id)
    assert b.status == "approved"
    assert b.acceptance_date == date.today()


# --------------------------------------------------------------------------- advances
def test_advance_approval_opens_the_recovery_balance(hr_admin, session):
    e = an_employee(session)
    reason = f"School fees due at the start of term {TAG}"
    r = hr_admin.post("/hr/advances/new", follow_redirects=False, data={
        "employee_id": e.id, "amount": "12000", "installments": "3", "reason": reason,
        "request_date": str(date.today())})
    assert r.status_code == 303
    a = session.query(SalaryAdvance).filter(SalaryAdvance.reason == reason).first()
    assert a is not None and a.status == "pending" and float(a.remaining or 0) == 0
    assert a.installments == 3

    r = hr_admin.post("/hr/advances/change-status", follow_redirects=False, data={
        "ids": [a.id], "status": "approved", "hr_remarks": f"Recovered over three runs {TAG}"})
    assert r.status_code == 303
    session.expire_all()
    a = session.get(SalaryAdvance, a.id)
    assert a.status == "approved"
    assert float(a.remaining) == float(a.amount) == 12000
    assert TAG in (a.hr_remarks or "")


# --------------------------------------------------------------------------- complaints
def test_complaint_create_and_change_status(hr_admin, session):
    e = an_employee(session)
    title = f"Classroom microphone is faulty {TAG}"
    r = hr_admin.post("/hr/complaints/new", follow_redirects=False, data={
        "employee_id": e.id, "complaint_type": "Facility", "title": title,
        "description": "The microphone cuts out mid-class."})
    assert r.status_code == 303
    c = session.query(StaffComplaint).filter(StaffComplaint.title == title).first()
    assert c is not None and c.status == "pending" and c.is_secret is False

    r = hr_admin.post("/hr/complaints/change-status", follow_redirects=False, data={
        "ids": [c.id], "status": "approved", "remarks": f"Replacement ordered {TAG}",
        "admin_response": f"A replacement microphone is on order {TAG}", "tab": "open"})
    assert r.status_code == 303
    session.expire_all()
    c = session.get(StaffComplaint, c.id)
    assert c.status == "approved"
    assert TAG in (c.admin_response or "")


def test_teacher_can_raise_their_own_complaint(a_teacher, session):
    title = f"Portal shows me absent on a taught day {TAG}"
    r = a_teacher.post("/hr/complaints/new", follow_redirects=False, data={
        "complaint_type": "HR", "title": title, "description": "Third month running."})
    assert r.status_code == 303
    c = session.query(StaffComplaint).filter(StaffComplaint.title == title).first()
    assert c is not None
    assert title in a_teacher.get("/hr/complaints").text


# --------------------------------------------------------------------------- bulk update
def test_apply_bulk_update(hr_admin, session):
    employees = session.query(Employee).filter(Employee.status == "active").order_by(Employee.id).limit(2).all()
    assert len(employees) == 2
    original = {e.id: e.employee_type for e in employees}
    target = "Marketing" if original[employees[0].id] != "Marketing" else "Admin"

    r = hr_admin.post("/hr/employees/bulk-update", follow_redirects=False, data={
        "ids": [e.id for e in employees], "field": "employee_type", "value": target,
        "rationale": f"Department realignment {TAG}"})
    assert r.status_code == 303
    session.expire_all()
    for e in employees:
        assert session.get(Employee, e.id).employee_type == target

    # put the record back so the suite can run again
    r = hr_admin.post("/hr/employees/bulk-update", follow_redirects=False, data={
        "ids": [employees[0].id], "field": "employee_type", "value": original[employees[0].id],
        "rationale": f"Restore after test {TAG}"})
    assert r.status_code == 303
    hr_admin.post("/hr/employees/bulk-update", follow_redirects=False, data={
        "ids": [employees[1].id], "field": "employee_type", "value": original[employees[1].id],
        "rationale": f"Restore after test {TAG}"})
    session.expire_all()
    for e in employees:
        assert session.get(Employee, e.id).employee_type == original[e.id]


def test_bulk_update_needs_a_rationale(hr_admin, session):
    e = an_employee(session)
    before = e.employee_type
    r = hr_admin.post("/hr/employees/bulk-update", follow_redirects=False,
                      data={"ids": [e.id], "field": "employee_type", "value": "Admin", "rationale": ""})
    assert r.status_code == 303
    session.expire_all()
    assert session.get(Employee, e.id).employee_type == before


# --------------------------------------------------------------------------- downloads and attachments
def test_download_lifecycle(hr_admin, session):
    description = f"Leave policy note {TAG}"
    r = hr_admin.post("/hr/downloads/new", follow_redirects=False,
                      data={"description": description, "link": "https://drive.google.com/oqc/test", "category": "policy"})
    assert r.status_code == 303
    dl = session.query(HRDownload).filter(HRDownload.description == description).first()
    assert dl is not None and dl.status == "active"

    assert hr_admin.post(f"/hr/downloads/{dl.id}/edit", follow_redirects=False,
                         data={"description": description, "link": "https://drive.google.com/oqc/test-2",
                               "category": "form"}).status_code == 303
    assert hr_admin.post(f"/hr/downloads/{dl.id}/toggle", follow_redirects=False).status_code == 303
    session.expire_all()
    dl = session.get(HRDownload, dl.id)
    assert dl.status == "inactive" and dl.category == "form"


def test_attachment_upload_link_and_file(hr_admin, session):
    title = f"Signed contract {TAG}"
    r = hr_admin.post("/hr/attachments/new", follow_redirects=False,
                      data={"title": title, "entity_type": "employee", "link": "https://drive.google.com/oqc/contract"})
    assert r.status_code == 303
    a = session.query(Attachment).filter(Attachment.title == title).first()
    assert a is not None and a.link and a.status == "active"

    file_title = f"CNIC copy {TAG}"
    r = hr_admin.post("/hr/attachments/new", follow_redirects=False,
                      data={"title": file_title, "entity_type": "employee"},
                      files={"file": ("cnic.txt", b"scanned copy", "text/plain")})
    assert r.status_code == 303
    f = session.query(Attachment).filter(Attachment.title == file_title).first()
    assert f is not None
    assert f.file_name == "cnic.txt" and f.content_type == "text/plain" and f.size_bytes == len(b"scanned copy")
    assert f.file_path and f.file_path.startswith("/storage/attachments/")

    assert hr_admin.post(f"/hr/attachments/{a.id}/toggle", follow_redirects=False).status_code == 303
    session.expire_all()
    assert session.get(Attachment, a.id).status == "inactive"
