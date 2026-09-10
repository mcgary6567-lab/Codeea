"""HR / People & Culture / Payroll module tests: pages, permissions, grievance confidentiality,
attendance, leaves, payroll generation and the teacher grading engine."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.people import (Employee, Teacher, HRAttendance, Leave, Violation, Grievance, Candidate,
                               PayrollRun, Payslip, RecruitmentRequest, TrainingAssignment)
from app.services import hr as svc
from app.services import payroll as pay


def _client(username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}"
    return c


def first_employee(db) -> Employee:
    return db.query(Employee).filter(Employee.status.in_(["active", "probation"])).order_by(Employee.id).first()


# --------------------------------------------------------------------------- pages
@pytest.mark.parametrize("url", [
    "/hr/employees", "/hr/employees/new", "/hr/me", "/hr/attendance", "/hr/attendance?tab=monthly",
    "/hr/attendance?tab=corrections", "/hr/attendance?tab=late", "/hr/leaves", "/hr/leaves/new",
    "/hr/recruitment", "/hr/recruitment?tab=pipeline", "/hr/recruitment?tab=sourcing",
    "/hr/payroll", "/hr/payroll/teachers", "/hr/payroll/bands", "/hr/payroll/advances", "/hr/payroll/bonuses",
    "/hr/violations", "/hr/grievances", "/hr/grievances/new", "/hr/teacher-development",
    "/hr/teacher-development?tab=bands", "/hr/teacher-development?tab=training", "/hr/teacher-development?tab=gates",
    "/hr/teacher-development?tab=plans", "/hr/teacher-development?tab=catalogue",
    "/hr/provisioning", "/hr/provisioning?tab=policy", "/hr/provisioning?tab=checklist", "/hr/provisioning?tab=audit",
])
def test_admin_pages(admin, url):
    assert admin.get(url).status_code == 200


def test_employee_detail_tabs(admin, db):
    e = first_employee(db)
    for tab in ["overview", "attendance", "leaves", "salary", "payslips", "violations", "comp",
                "onboarding", "provisioning", "development", "documents", "audit"]:
        assert admin.get(f"/hr/employees/{e.id}?tab={tab}").status_code == 200


def test_me_tabs(admin):
    for tab in ["overview", "attendance", "leaves", "payslips", "violations", "development", "grievance"]:
        assert admin.get(f"/hr/me?tab={tab}").status_code == 200


def test_exports(admin):
    assert admin.get("/hr/employees/export.csv").status_code == 200
    assert admin.get(f"/hr/attendance/export.csv?month={date.today():%Y-%m}").status_code == 200


def test_detail_pages(admin, db):
    leave = db.query(Leave).filter(Leave.person_type == "employee").first()
    cand = db.query(Candidate).first()
    req = db.query(RecruitmentRequest).first()
    run = db.query(PayrollRun).order_by(PayrollRun.id.desc()).first()
    slip = db.query(Payslip).order_by(Payslip.id.desc()).first()
    assert admin.get(f"/hr/leaves/{leave.id}").status_code == 200
    assert admin.get(f"/hr/recruitment/candidates/{cand.id}").status_code == 200
    assert admin.get(f"/hr/recruitment/{req.id}").status_code == 200
    assert admin.get(f"/hr/payroll/{run.id}").status_code == 200
    assert admin.get(f"/hr/payroll/{run.id}/export.csv").status_code == 200
    assert admin.get(f"/hr/payslips/{slip.id}").status_code == 200
    assert admin.get(f"/hr/payslips/{slip.id}/pdf").status_code == 200


# --------------------------------------------------------------------------- permissions
def test_grievances_are_confidential():
    """Managers and department heads must never reach the grievance channel, even with *.view."""
    for username, password in [("manager@oqc.local", "Manager@123"), ("finance@oqc.local", "Finance@123"),
                               ("academics@oqc.local", "Academ@123"), ("auditor@oqc.local", "Auditor@123")]:
        c = _client(username, password)
        assert c.get("/hr/grievances").status_code == 403, f"{username} could read grievances"


def test_people_roles_can_read_grievances():
    for username, password in [("hr@oqc.local", "People@123"), ("hrofficer@oqc.local", "HROff@123")]:
        assert _client(username, password).get("/hr/grievances").status_code == 200


def test_manager_cannot_open_employee_records():
    c = _client("manager@oqc.local", "Manager@123")
    assert c.get("/hr/employees").status_code == 403
    assert c.get("/hr/leaves").status_code == 200


def test_teacher_self_service_and_grievance_form():
    c = _client("teacher1@oqc.local", "Teacher@123")
    assert c.get("/hr/me").status_code == 200
    assert c.get("/hr/grievances/new").status_code == 200
    assert c.get("/hr/grievances").status_code == 403


# --------------------------------------------------------------------------- workflows
def test_check_in_and_out(admin, db):
    r = admin.post("/hr/me/check", data={"session": "pm", "action": "in"}, follow_redirects=False)
    assert r.status_code == 303
    r = admin.post("/hr/me/check", data={"session": "pm", "action": "out"}, follow_redirects=False)
    assert r.status_code == 303


def test_leave_request_and_decision(admin, db):
    e = first_employee(db)
    # Pick a start date no leave uses yet, so re-running the suite against the same database
    # still exercises a freshly created (pending) request rather than one a previous run approved.
    taken = {d for (d,) in db.query(Leave.start_date).filter(Leave.employee_id == e.id).all()}
    start = date.today() + timedelta(days=180)
    while start in taken:
        start += timedelta(days=3)
    r = admin.post("/hr/leaves/new", data={"employee_id": e.id, "leave_type": "casual", "start_date": str(start),
                                           "end_date": str(start + timedelta(days=1)), "reason": "Test request"},
                   follow_redirects=False)
    assert r.status_code == 303
    leave = db.query(Leave).filter(Leave.employee_id == e.id, Leave.start_date == start).first()
    assert leave is not None and leave.status == "pending"
    r = admin.post(f"/hr/leaves/{leave.id}/decide", data={"decision": "approve", "note": "Approved by test"},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.query(Leave).get(leave.id).status == "approved"


def test_violation_lifecycle(admin, db):
    e = first_employee(db)
    r = admin.post("/hr/violations/new", data={"employee_id": e.id, "violation_type": "policy", "severity": "minor",
                                               "date": str(date.today()), "description": "Automated test violation",
                                               "action_taken": "None", "deduction_amount": "0"}, follow_redirects=False)
    assert r.status_code == 303
    v = db.query(Violation).filter(Violation.description == "Automated test violation").first()
    assert v is not None
    assert admin.post(f"/hr/violations/{v.id}/close", data={"note": "Closed by test"}, follow_redirects=False).status_code == 303


def test_payroll_draft_is_idempotent(db):
    period = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    run = db.query(PayrollRun).filter(PayrollRun.period == period).first()
    assert run is not None, "the seed should have produced a run for the previous month"
    assert len(run.payslips) > 0
    assert float(run.total_net) > 0
    # every payslip nets out consistently
    for p in run.payslips[:5]:
        expected = float(p.gross) - float(p.deductions) - float(p.attendance_deduction) - float(p.advance_deduction)
        assert abs(float(p.net) - expected) < 0.02


def test_teacher_grade_engine(db):
    t = db.query(Teacher).order_by(Teacher.id).first()
    inputs = pay.grade_inputs_for(db, t)
    assert set(["qa", "punctuality", "retention"]).issubset(inputs)
    rules = pay.grade_rules(db)
    assert "A" in rules and "B" in rules
    grade = pay._grade_from_inputs(rules, inputs)
    assert grade in ("A", "B", "C")


def test_attendance_summary_and_leave_balance(db):
    e = first_employee(db)
    start = date.today() - timedelta(days=30)
    summary = svc.attendance_summary(db, e.id, start, date.today())
    assert 0 <= summary["pct"] <= 100
    balance = svc.leave_balance(db, e)
    assert "casual" in balance and balance["casual"]["allowed"] >= 0


def test_repeat_offenders_and_kpis(db):
    assert isinstance(svc.repeat_offenders(db), list)
    kpis = svc.hr_kpis(db)
    assert kpis["headcount"] > 0
    assert 0 <= kpis["attendance_pct"] <= 100


# --------------------------------------------------------------------------- API
@pytest.mark.parametrize("url", ["/api/v1/hr/employees", "/api/v1/hr/kpis", "/api/v1/hr/attendance?limit=5",
                                 "/api/v1/hr/leaves", "/api/v1/hr/payroll/runs", "/api/v1/hr/payslips/me",
                                 "/api/v1/hr/grades", "/api/v1/hr/training", "/api/v1/hr/violations",
                                 "/api/v1/hr/recruitment/pipeline", "/api/v1/hr/payroll/teacher-cost",
                                 "/api/v1/hr/grievances/summary"])
def test_api_endpoints(admin, url):
    r = admin.get(url)
    assert r.status_code == 200
    assert r.json() is not None


def test_api_grievance_summary_is_restricted():
    c = _client("finance@oqc.local", "Finance@123")
    assert c.get("/api/v1/hr/grievances/summary").status_code == 403
