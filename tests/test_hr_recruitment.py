"""Recruitment and Hiring, Benefits, Financial Management, HR Configurations and the HR Dashboards.

Runs against a private database:

    $env:DATABASE_URL='sqlite:///./data/oqc_hrC.db'; .venv/Scripts/python.exe seed.py --reset
    .venv/Scripts/python.exe -m pytest tests/test_hr_recruitment.py

Every test is re-runnable against the same database: anything that must be created fresh (a payroll month,
an application, a panel) is given a name or a period that is chosen by looking at what is already there.
"""
from __future__ import annotations

import itertools
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_hrC.db")

from datetime import date, datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.hr_erp import (APPLICATION_STATUSES, BonusType, Grade, Holiday, InterviewPanel, JobApplication,  # noqa: E402
                               ViolationType)
from app.models.core import Department, User  # noqa: E402
from app.models.people import (Bonus, Candidate, Employee, Interview, PayrollRun, Payslip, RecruitmentRequest,  # noqa: E402
                               SalaryStructure, Violation)
from app.models.scheduling import Shift  # noqa: E402
from app.services import hr_dashboards as hrd  # noqa: E402
from app.services import payroll as pay  # noqa: E402


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _login(username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}"
    return c


def _purge_test_artifacts() -> None:
    """Remove everything a run of this module creates, so it can be run again against the same database."""
    from app.models.people import OnboardingTask, Teacher

    s = SessionLocal()
    try:
        names = ["Test Candidate %"]
        cands = [c for n in names for c in s.query(Candidate).filter(Candidate.full_name.like(n)).all()]
        apps_ = [a for n in names for a in s.query(JobApplication).filter(JobApplication.full_name.like(n)).all()]
        emp_ids = {c.hired_employee_id for c in cands if c.hired_employee_id}
        emp_ids |= {a.hired_employee_id for a in apps_ if a.hired_employee_id}
        emps = [e for n in names for e in s.query(Employee).filter(Employee.full_name.like(n)).all()]
        emp_ids |= {e.id for e in emps}

        for c in cands:
            s.query(Interview).filter(Interview.candidate_id == c.id).delete(synchronize_session=False)
        for a in apps_:
            s.delete(a)
        for c in cands:
            s.delete(c)
        s.flush()

        user_ids = set()
        for eid in emp_ids:
            e = s.get(Employee, eid)
            if e is None:
                continue
            if e.user_id:
                user_ids.add(e.user_id)
            s.query(Teacher).filter(Teacher.employee_id == e.id).delete(synchronize_session=False)
            s.query(SalaryStructure).filter(SalaryStructure.employee_id == e.id).delete(synchronize_session=False)
            s.query(Payslip).filter(Payslip.employee_id == e.id).delete(synchronize_session=False)
            s.query(OnboardingTask).filter(OnboardingTask.employee_id == e.id).delete(synchronize_session=False)
            s.delete(e)
        s.flush()
        for uid in user_ids:
            u = s.get(User, uid)
            if u is not None:
                s.delete(u)
        # The payroll tests open a month, generate it, post it or cancel it. Left behind they fill the
        # payroll list with test months, which is what an administrator opens the page to read.
        runs = (s.query(PayrollRun)
                .filter(PayrollRun.description.in_(["First"])
                        | PayrollRun.description.like("Test payroll %")
                        | PayrollRun.description.like("Cancelled test %")).all())
        for run in runs:
            s.query(Payslip).filter(Payslip.payroll_run_id == run.id).delete(synchronize_session=False)
            s.delete(run)
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@pytest.fixture(scope="module", autouse=True)
def _clean_module():
    _purge_test_artifacts()
    yield
    _purge_test_artifacts()


@pytest.fixture(scope="module")
def admin():
    return _login("admin@oqc.local", "Admin@12345")


@pytest.fixture(scope="module")
def hr():
    return _login("hr@oqc.local", "People@123")


_COUNTER = itertools.count(1)


def _tag() -> str:
    """A unique suffix so a re-run - and each test - creates its own rows."""
    return datetime.utcnow().strftime("%y%m%d%H%M%S") + f"{next(_COUNTER):03d}"


def _free_period(db) -> str:
    """A month with no payroll run yet (looking forward, so every employee is in scope)."""
    y, m = date.today().year + 1, 1
    taken = {p for (p,) in db.query(PayrollRun.period).all()}
    while f"{y}-{m:02d}" in taken:
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return f"{y}-{m:02d}"


# --------------------------------------------------------------------------- pages
@pytest.mark.parametrize("url", [
    "/hr/recruitment", "/hr/recruitment?report=primary", "/hr/recruitment?report=summary",
    "/hr/recruitment?report=summary_dept", "/hr/recruitment?tab=pipeline", "/hr/recruitment?tab=sourcing",
    "/hr/recruitment?status=approved", "/hr/recruitment?job_type=full_time", "/hr/recruitment?q=Quran",
    "/hr/recruitment/summary",
    "/hr/applications", "/hr/applications?status=applied", "/hr/applications?status=hired",
    "/hr/applications?application_type=profile", "/hr/applications?application_type=non_profile",
    "/hr/applications?from_date=2020-01-01&to_date=2030-12-31", "/hr/applications?q=a",
    "/hr/interview-panels", "/hr/interview-panels?status=active", "/hr/interview-panels?status=inactive",
    "/hr/interviews", "/hr/interviews?status=scheduled", "/hr/interviews?status=completed",
    "/hr/interviews?from_date=2020-01-01&to_date=2030-12-31",
    "/hr/candidates", "/hr/candidates?q=a", "/hr/candidates?skill=Tajweed", "/hr/candidates?city=Lahore",
    "/hr/candidates?qualification=MA", "/hr/candidates?status=hired",
    "/hr/onboarding", "/hr/onboarding?state=open", "/hr/onboarding?state=complete", "/hr/onboarding?state=none",
    "/hr/payroll", "/hr/payroll?status=pending", "/hr/payroll?status=generated", "/hr/payroll?status=posted",
    "/hr/payroll/teachers", "/hr/payroll/bands", "/hr/payroll/advances", "/hr/payroll/bonuses",
    "/hr/config", "/hr/config/departments", "/hr/config/departments?status=active", "/hr/config/shifts",
    "/hr/config/shifts?group=morning", "/hr/config/holidays", "/hr/config/holidays?status=active",
    "/hr/config/violation-types", "/hr/config/violation-types?severity=minor", "/hr/config/bonus-types",
    "/hr/config/grades", "/hr/config/grades?status=active", "/hr/config/users", "/hr/config/users?active=yes",
    "/hr/dashboards", "/hr/dashboards/employees", "/hr/dashboards/attendance", "/hr/dashboards/financial",
    "/hr/dashboards/financial?months=12",
])
def test_pages(admin, url):
    assert admin.get(url).status_code == 200, url


def test_detail_pages(admin, db):
    req = db.query(RecruitmentRequest).order_by(RecruitmentRequest.id).first()
    appn = db.query(JobApplication).order_by(JobApplication.id).first()
    run = db.query(PayrollRun).order_by(PayrollRun.id.desc()).first()
    assert admin.get(f"/hr/recruitment/{req.id}").status_code == 200
    assert admin.get(f"/hr/applications/{appn.id}").status_code == 200
    assert admin.get(f"/hr/payroll/{run.id}").status_code == 200
    assert admin.get(f"/hr/payroll/{run.id}/export.csv").status_code == 200


def test_people_role_reaches_the_config_and_dashboards(hr):
    for url in ["/hr/config", "/hr/config/grades", "/hr/config/holidays", "/hr/config/users",
                "/hr/dashboards", "/hr/dashboards/employees", "/hr/dashboards/financial",
                "/hr/applications", "/hr/interview-panels"]:
        assert hr.get(url).status_code == 200, url


def test_manager_cannot_configure_hr():
    c = _login("manager@oqc.local", "Manager@123")
    assert c.get("/hr/config").status_code == 403
    assert c.get("/hr/config/grades").status_code == 403


def test_seeded_pipeline_is_dense(db):
    total = db.query(JobApplication).count()
    assert total >= 55, f"expected the seed to load ~60 applications, found {total}"
    present = {s for (s,) in db.query(JobApplication.status).distinct()}
    assert set(APPLICATION_STATUSES) <= present, f"pipeline steps missing: {set(APPLICATION_STATUSES) - present}"
    assert db.query(InterviewPanel).count() >= 3
    assert db.query(Interview).count() >= 20
    assert db.query(JobApplication).filter(JobApplication.hired_employee_id.isnot(None)).count() >= 1


# --------------------------------------------------------------------------- job requisitions
def test_create_and_edit_requisition(admin, db):
    title = f"Quran Teacher (test {_tag()})"
    r = admin.post("/hr/recruitment/new", data={
        "title": title, "positions": "3", "job_type": "part_time", "job_categories": ["Quran", "Tajweed"],
        "skills": "Tajweed, Hifz", "job_location": "Remote", "start_date": str(date.today()),
        "end_date": str(date.today() + timedelta(days=60)), "description": "Created by the test suite.",
    }, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    req = db.query(RecruitmentRequest).filter(RecruitmentRequest.title == title).first()
    assert req is not None
    assert req.positions == 3 and req.job_type == "part_time"
    assert req.job_categories == ["Quran", "Tajweed"] and req.skills == ["Tajweed", "Hifz"]
    assert req.job_location == "Remote" and req.end_date is not None

    r = admin.post(f"/hr/recruitment/{req.id}/edit", data={
        "title": title, "positions": "5", "job_type": "full_time", "job_categories_text": "Quran, Hifz",
        "skills": "Tajweed", "job_location": "Head Office - Lahore", "status": "in_progress",
    }, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    req = db.get(RecruitmentRequest, req.id)
    assert req.positions == 5 and req.status == "in_progress" and req.job_categories == ["Quran", "Hifz"]


# --------------------------------------------------------------------------- applications
def _make_application(admin, db, status: str = "applied") -> JobApplication:
    tag = _tag()
    req = db.query(RecruitmentRequest).filter(RecruitmentRequest.title.like("%Quran Teacher%")).first() \
        or db.query(RecruitmentRequest).first()
    name = f"Test Candidate {tag}"
    r = admin.post("/hr/applications/new", data={
        "full_name": name, "father_name": "Abdul Rehman", "gender": "female", "request_id": str(req.id),
        "application_type": "profile", "application_date": str(date.today()), "nic_number": "3520111111111",
        "cell_no": "+92 300 1111111", "email": f"test.candidate.{tag}@example.com",
        "qualification": "MA Islamic Studies", "experience_years": "3", "expected_salary": "35000",
        "city": "Lahore", "source": "Website", "remarks": "Created by the test suite.",
    }, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    a = db.query(JobApplication).filter(JobApplication.full_name == name).first()
    assert a is not None and a.status == "applied"
    return a


def test_add_application_manually(admin, db):
    a = _make_application(admin, db)
    assert a.department_id is not None, "the department should default to the job's department"
    assert admin.get(f"/hr/applications/{a.id}").status_code == 200


def test_process_moves_to_the_next_step(admin, db):
    a = _make_application(admin, db)
    r = admin.post("/hr/applications/process", data={"ids": [str(a.id)], "remark": "Shortlisted by the test suite."},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    a = db.get(JobApplication, a.id)
    assert a.status == "initial_selected"
    assert "Shortlisted by the test suite." in (a.remarks or "")


def test_process_many_at_once(admin, db):
    first, second = _make_application(admin, db), _make_application(admin, db)
    r = admin.post("/hr/applications/process",
                   data={"ids": [str(first.id), str(second.id)], "status": "on_hold", "remark": "Batch on hold."},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(JobApplication, first.id).status == "on_hold"
    assert db.get(JobApplication, second.id).status == "on_hold"


def test_interview_step_needs_a_panel(admin, db):
    a = _make_application(admin, db)
    r = admin.post("/hr/applications/process",
                   data={"ids": [str(a.id)], "status": "marked_1st_interview", "remark": "No panel chosen."},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(JobApplication, a.id).status == "applied", "an interview step must not be set without a panel"

    panel = db.query(InterviewPanel).filter(InterviewPanel.status == "active").first()
    r = admin.post("/hr/applications/process",
                   data={"ids": [str(a.id)], "status": "marked_1st_interview", "panel_id": str(panel.id),
                         "remark": "Panel assigned."}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    a = db.get(JobApplication, a.id)
    assert a.status == "marked_1st_interview" and a.panel_id == panel.id


def test_hiring_creates_the_employee_and_links_it(admin, db):
    a = _make_application(admin, db)
    dept = db.query(Department).filter(Department.code == "academics").first()
    before = db.query(Employee).count()
    r = admin.post("/hr/applications/process", data={
        "ids": [str(a.id)], "status": "hired", "remark": "Offer accepted.", "department_id": str(dept.id),
        "join_date": str(date.today()), "base_salary": "35000", "shift": "night",
    }, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    a = db.get(JobApplication, a.id)
    assert a.status == "hired"
    assert a.hired_employee_id, "hiring must link the application to the new employee"
    assert a.candidate_id, "hiring must put the person in the candidate database"
    emp = db.get(Employee, a.hired_employee_id)
    assert emp is not None and emp.full_name == a.full_name
    assert db.query(Employee).count() == before + 1
    assert db.get(Candidate, a.candidate_id).stage == "hired"
    assert admin.get(f"/hr/employees/{emp.id}").status_code == 200


# --------------------------------------------------------------------------- panels and interviews
def test_panel_create_edit_toggle(admin, db):
    name = f"Test Panel {_tag()}"
    members = [u.id for u in db.query(User).filter(User.email.in_(["hr@oqc.local", "academics@oqc.local"])).all()]
    r = admin.post("/hr/interview-panels/new", data={
        "name": name, "description": "Created by the test suite.", "member_ids": [str(m) for m in members],
    }, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    p = db.query(InterviewPanel).filter(InterviewPanel.name == name).first()
    assert p is not None and sorted(p.member_ids) == sorted(members) and p.status == "active"

    r = admin.post(f"/hr/interview-panels/{p.id}/edit", data={
        "name": name, "description": "Edited.", "member_ids": [str(members[0])], "status": "active",
    }, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(InterviewPanel, p.id).member_ids == [members[0]]

    assert admin.post(f"/hr/interview-panels/{p.id}/toggle", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(InterviewPanel, p.id).status == "inactive"
    assert admin.post(f"/hr/interview-panels/{p.id}/toggle", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(InterviewPanel, p.id).status == "active"


def test_schedule_an_interview_and_record_the_outcome(admin, db):
    a = _make_application(admin, db)
    panel = db.query(InterviewPanel).filter(InterviewPanel.status == "active",
                                            InterviewPanel.member_ids != []).first()
    when = date.today() + timedelta(days=3)
    r = admin.post("/hr/interviews/new", data={
        "application_id": str(a.id), "panel_id": str(panel.id), "interview_date": str(when),
        "interview_time": "11:30", "interview_type": "screening", "location": "Head Office - Lahore",
    }, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    a = db.get(JobApplication, a.id)
    assert a.panel_id == panel.id
    assert a.status == "marked_1st_interview", "scheduling a screening interview marks the 1st interview step"
    rows = db.query(Interview).filter(Interview.candidate_id == a.candidate_id).all()
    assert len(rows) == len(panel.member_ids), "one interview row per panel member"
    assert all("Head Office - Lahore" in (iv.feedback or "") for iv in rows)

    iv = rows[0]
    r = admin.post(f"/hr/interviews/{iv.id}/outcome",
                   data={"status": "completed", "score": "8.5", "feedback": "Clear recitation, calm manner."},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    iv = db.get(Interview, iv.id)
    assert iv.status == "completed" and float(iv.score) == 8.5
    assert "Clear recitation" in iv.feedback and "Head Office - Lahore" in iv.feedback
    assert admin.get("/hr/interviews").status_code == 200


# --------------------------------------------------------------------------- HR configurations
def test_department_create_edit_toggle(admin, db):
    name = f"Test Department {_tag()}"
    assert admin.post("/hr/config/departments/new", data={"name": name, "code": f"test_{_tag()}"},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    d = db.query(Department).filter(Department.name == name).first()
    assert d is not None and d.is_active
    assert admin.post(f"/hr/config/departments/{d.id}/edit",
                      data={"name": name + " (edited)", "status": "active"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Department, d.id).name.endswith("(edited)")
    assert admin.post(f"/hr/config/departments/{d.id}/toggle", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Department, d.id).is_active is False


def test_shift_create_edit_toggle(admin, db):
    name = f"Test Shift {_tag()}"
    assert admin.post("/hr/config/shifts/new", data={"name": name, "group": "night", "start_time": "20:00",
                                                     "end_time": "04:00"}, follow_redirects=False).status_code == 303
    db.expire_all()
    sh = db.query(Shift).filter(Shift.name == name).first()
    assert sh is not None and sh.group == "night"
    assert admin.post(f"/hr/config/shifts/{sh.id}/edit", data={"name": name, "group": "night", "start_time": "21:00",
                                                               "end_time": "05:00", "status": "active"},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Shift, sh.id).start_time.hour == 21
    assert admin.post(f"/hr/config/shifts/{sh.id}/toggle", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Shift, sh.id).is_active is False
    assert admin.get("/schedules/shifts").status_code == 200


def test_holiday_create_edit_toggle(admin, db):
    name = f"Test Holiday {_tag()}"
    day = date.today() + timedelta(days=400 + db.query(Holiday).count())
    assert admin.post("/hr/config/holidays/new", data={"name": name, "holiday_date": str(day), "shift_group": "all",
                                                       "is_paid": "1"}, follow_redirects=False).status_code == 303
    db.expire_all()
    h = db.query(Holiday).filter(Holiday.name == name).first()
    assert h is not None and h.is_paid and h.shift_group == "all" and h.status == "active"
    assert admin.post(f"/hr/config/holidays/{h.id}/edit",
                      data={"name": name, "holiday_date": str(day), "end_date": str(day + timedelta(days=2)),
                            "shift_group": "morning", "status": "active"}, follow_redirects=False).status_code == 303
    db.expire_all()
    h = db.get(Holiday, h.id)
    assert h.end_date == day + timedelta(days=2) and h.shift_group == "morning" and h.is_paid is False
    assert h.last_day == h.end_date
    assert admin.post(f"/hr/config/holidays/{h.id}/toggle", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Holiday, h.id).status == "inactive"


def test_violation_and_bonus_type_catalogues(admin, db):
    tag = _tag()
    assert admin.post("/hr/config/violation-types/new",
                      data={"description": f"Test offence {tag}", "penalty_amount": "750", "severity": "major"},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    vt = db.query(ViolationType).filter(ViolationType.description == f"Test offence {tag}").first()
    assert vt is not None and float(vt.penalty_amount) == 750 and vt.severity == "major"
    assert admin.post(f"/hr/config/violation-types/{vt.id}/edit",
                      data={"description": f"Test offence {tag}", "penalty_amount": "900", "severity": "minor",
                            "status": "active"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert float(db.get(ViolationType, vt.id).penalty_amount) == 900
    assert admin.post(f"/hr/config/violation-types/{vt.id}/toggle", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(ViolationType, vt.id).status == "inactive"

    assert admin.post("/hr/config/bonus-types/new",
                      data={"description": f"Test bonus {tag}", "bonus_amount": "2500"},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    bt = db.query(BonusType).filter(BonusType.description == f"Test bonus {tag}").first()
    assert bt is not None and float(bt.bonus_amount) == 2500
    assert admin.post(f"/hr/config/bonus-types/{bt.id}/toggle", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(BonusType, bt.id).status == "inactive"


def test_grade_create_and_assignment_fills_the_salary_structure(admin, db):
    name = f"Test Grade {_tag()}"
    assert admin.post("/hr/config/grades/new", data={
        "name": name, "description": "Created by the test suite.", "basic_min": "30000", "basic_max": "50000",
        "allow_key": ["internet", "medical"], "allow_value": ["2000", "1500"],
    }, follow_redirects=False).status_code == 303
    db.expire_all()
    g = db.query(Grade).filter(Grade.name == name).first()
    assert g is not None and g.allowances == {"internet": 2000.0, "medical": 1500.0}

    emp = db.query(Employee).filter(Employee.status.in_(["active", "probation"])).order_by(Employee.id).first()
    assert admin.post(f"/hr/config/grades/{g.id}/assign", data={"employee_id": str(emp.id)},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    emp = db.get(Employee, emp.id)
    structure = db.query(SalaryStructure).filter(SalaryStructure.employee_id == emp.id).first()
    assert emp.grade_id == g.id
    assert structure is not None
    assert structure.allowances.get("internet") == 2000.0 and structure.allowances.get("medical") == 1500.0
    assert 30000 <= float(structure.basic) <= 50000, "the basic is clamped to the grade's range"

    assert admin.post(f"/hr/config/grades/{g.id}/toggle", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Grade, g.id).status == "inactive"


# --------------------------------------------------------------------------- payroll
def test_payroll_run_lifecycle(admin, db):
    """Create -> generate (approved violation deducted, approved bonus added) -> post (locked)."""
    period = _free_period(db)
    start = date.fromisoformat(period + "-01")
    emp = db.query(Employee).filter(Employee.status.in_(["active", "probation"]),
                                    Employee.is_teacher.is_(False)).order_by(Employee.id).first()
    approved_violation = Violation(employee_id=emp.id, violation_type="policy", severity="minor",
                                   description=f"Test deduction {period}", date=start + timedelta(days=3),
                                   deduction_amount=1234, status="closed", approval_status="approved")
    pending_violation = Violation(employee_id=emp.id, violation_type="policy", severity="minor",
                                  description=f"Test pending {period}", date=start + timedelta(days=4),
                                  deduction_amount=999, status="open", approval_status="pending")
    approved_bonus = Bonus(employee_id=emp.id, amount=4321, bonus_type="performance", period=period,
                           status="approved", reason=f"Test bonus {period}")
    db.add_all([approved_violation, pending_violation, approved_bonus])
    db.commit()

    r = admin.post("/hr/payroll/new", data={"period": period, "description": f"Test payroll {period}"},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    run = db.query(PayrollRun).filter(PayrollRun.period == period).order_by(PayrollRun.id.desc()).first()
    assert run is not None and run.status == "pending" and run.description == f"Test payroll {period}"

    r = admin.post(f"/hr/payroll/{run.id}/generate", follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    run = db.get(PayrollRun, run.id)
    assert run.status == "generated"
    assert len(run.payslips) > 0
    slip = db.query(Payslip).filter(Payslip.payroll_run_id == run.id, Payslip.employee_id == emp.id).first()
    assert slip is not None
    amounts = [v["amount"] for v in (slip.details or {}).get("violations", [])]
    assert 1234 in amounts, "an approved violation in the month is deducted"
    assert 999 not in amounts, "a violation still awaiting approval is not deducted"
    assert float(slip.bonus) >= 4321, "an approved bonus for the month is added"
    bonus_ids = [b["id"] for b in (slip.details or {}).get("bonuses", [])]
    assert approved_bonus.id in bonus_ids
    assert abs(float(slip.net) - (float(slip.gross) - float(slip.deductions) - float(slip.attendance_deduction)
                                  - float(slip.advance_deduction))) < 0.02
    assert admin.get(f"/hr/payroll/{run.id}").status_code == 200

    components = pay.erp_run_components(run)
    assert components["payslips"] == len(run.payslips)
    assert components["violations"] >= 1234 and components["bonuses"] >= 4321

    r = admin.post(f"/hr/payroll/{run.id}/post", data={"rationale": "Reviewed by the test suite."},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    run = db.get(PayrollRun, run.id)
    assert run.status == "posted" and run.approved_at is not None
    assert pay.erp_run_is_locked(run)

    # a posted run is locked: neither regeneration nor a payslip adjustment changes it
    net_before = float(run.total_net)
    assert admin.post(f"/hr/payroll/{run.id}/generate", follow_redirects=False).status_code == 303
    db.expire_all()
    run = db.get(PayrollRun, run.id)
    assert run.status == "posted" and abs(float(run.total_net) - net_before) < 0.02

    slip = db.query(Payslip).filter(Payslip.payroll_run_id == run.id).first()
    gross_before = float(slip.gross)
    assert admin.post(f"/hr/payroll/payslips/{slip.id}/adjust", data={"amount": "500", "reason": "Should be refused."},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    assert abs(float(db.get(Payslip, slip.id).gross) - gross_before) < 0.02

    assert admin.post(f"/hr/payroll/{run.id}/cancel", data={"reason": "Should be refused."},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(PayrollRun, run.id).status == "posted"


def test_payroll_can_be_cancelled_before_it_is_posted(admin, db):
    period = _free_period(db)
    assert admin.post("/hr/payroll/new", data={"period": period, "description": f"Cancelled test {period}"},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    run = db.query(PayrollRun).filter(PayrollRun.period == period).order_by(PayrollRun.id.desc()).first()
    assert admin.post(f"/hr/payroll/{run.id}/cancel", data={"reason": "Raised in error."},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(PayrollRun, run.id).status == "cancelled"


def test_payroll_month_is_unique_while_it_is_open(admin, db):
    period = _free_period(db)
    assert admin.post("/hr/payroll/new", data={"period": period, "description": "First"},
                      follow_redirects=False).status_code == 303
    assert admin.post("/hr/payroll/new", data={"period": period, "description": "Second"},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(PayrollRun).filter(PayrollRun.period == period).count() == 1


def test_seeded_payroll_uses_the_erp_vocabulary(db):
    periods = []
    d = date.today().replace(day=1)
    for _ in range(6):
        d = (d - timedelta(days=1)).replace(day=1)
        periods.append(d.strftime("%Y-%m"))
    runs = db.query(PayrollRun).filter(PayrollRun.period.in_(periods)).all()
    assert len(runs) >= 6, "the seed should produce a payroll run for each of the last six months"
    for r in runs:
        assert r.status in pay.ERP_PAYROLL_STATUSES, f"{r.period} is {r.status}"
        assert r.description, f"{r.period} has no description"
    assert any(r.status == "posted" for r in runs)


# --------------------------------------------------------------------------- dashboards and summaries
def test_employee_dashboard_numbers(db):
    d = hrd.employee_dashboard(db)
    assert d["headcount"] > 0 and d["total"] >= d["headcount"]
    assert len(d["months"]) == 12 and len(d["joiners"]) == 12 and len(d["leavers"]) == 12
    assert sum(r["count"] for r in d["by_department"]) == d["headcount"]
    assert sum(r["count"] for r in d["by_gender"]) == d["headcount"]
    assert d["avg_tenure_days"] > 0 and d["avg_tenure_years"] > 0
    for key in ("by_designation", "by_employee_type", "by_shift", "by_status"):
        assert d[key], key


def test_attendance_dashboard_numbers(db):
    end = date.today()
    d = hrd.attendance_dashboard(db, end - timedelta(days=45), end)
    t = d["totals"]
    assert t["sessions"] > 0
    assert t["present"] + t["absent"] + t["leave"] + t["half_day"] + t["holiday"] <= t["sessions"]
    assert 0 <= t["attendance_pct"] <= 100
    assert t["shortage_hours"] >= 0
    assert d["by_day"] and d["by_employee"]
    assert all(0 <= r["attendance_pct"] <= 100 for r in d["by_employee"])


def test_financial_dashboard_numbers(db):
    d = hrd.financial_dashboard(db, 6)
    assert len(d["months"]) == 6 and len(d["cost_by_month"]) == 6
    assert d["total_cost"] > 0
    assert abs(sum(r["amount"] for r in d["by_department"]) - d["total_cost"]) < max(1.0, 0.02 * d["total_cost"])
    assert d["bonuses_total"] >= 0 and d["violations_total"] >= 0 and d["advances_outstanding"] >= 0


def test_recruitment_summary_numbers(db):
    rows = hrd.recruitment_summary(db)
    assert rows
    total_apps = sum(r["applications"] for r in rows)
    assert total_apps >= 55
    for r in rows:
        assert sum(r["counts"].values()) == r["applications"]
        assert r["hires"] == r["counts"]["hired"]
    funnel = hrd.application_funnel(db)
    assert sum(funnel.values()) == db.query(JobApplication).count()
