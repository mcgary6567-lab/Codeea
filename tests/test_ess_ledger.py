"""Employee Self Portal: Mark Attendance, the Attendance Sheet, and the Account Ledger.

Covers docs/AUDIT_EMPLOYEE_SELF_PORTAL.md: the dashboard's Today's Attendance panel and its one
Mark Attendance button, the Attendance Sheet's filters, columns and per-row Request to Change, the
management change-request queue picking those up, the Account Ledger with its opening balance and running
balance, per-month salary slip printing, and the four tabs the launchpad links (schedule, requests,
bonuses, complaints).

The suite is re-runnable against the same database. It never edits seeded rows: it creates two employees
of its own with their own logins, works entirely against those, and removes every row it made (ledger
lines, attendance, change requests, requests, complaints, advances, bonuses, violations, payroll, audit
events, notifications, sessions, users, employees) in a module-scoped teardown. Nothing asserts an
absolute count that only holds on a freshly seeded database.

Run against a private database:
    $env:DATABASE_URL='sqlite:///./data/oqc_essA.db'
    .venv/Scripts/python.exe -m pytest tests/test_ess_ledger.py
"""
from __future__ import annotations

import base64
import json
import os
import re
from datetime import date, datetime, time, timedelta
from urllib.parse import unquote
from uuid import uuid4

# The application reads DATABASE_URL at import time; set it before anything imports the app.
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_essA.db")

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.database import SessionLocal
from app.main import app
from app.models.core import AuditEvent, Notification, User, UserSession
from app.models.hr_erp import AttendanceChangeRequest, EmployeeLedgerEntry, EmployeeRequest, StaffComplaint
from app.models.people import (Bonus, Employee, HRAttendance, Leave, PayrollRun, Payslip, SalaryAdvance,
                               SalaryStructure, Violation)

TAG = f"pytest-{uuid4().hex[:8]}"
PASSWORD = "EssPortal@123"
TODAY = date.today()


# --------------------------------------------------------------------------- fixtures
def _client(username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}: {r.status_code}"
    return c


def _make_staff(s, suffix: str) -> int:
    """A plain member of staff with their own login, built so the suite never touches seeded people."""
    template = s.query(User).filter(User.email == "teacher1@oqc.local").first()
    assert template is not None, "the demo teacher account must exist"
    email = f"ess.{suffix}.{TAG}@oqc.local"
    u = User(email=email, username=email, full_name=f"ESS Tester {suffix.upper()} {TAG}",
             hashed_password=hash_password(PASSWORD), role_id=template.role_id,
             department_id=template.department_id, branch_id=template.branch_id, is_active=True)
    s.add(u)
    s.flush()
    e = Employee(employee_code=f"E-{TAG[-6:]}{suffix.upper()}", user_id=u.id,
                 full_name=u.full_name, designation="Supporting Staff", department_id=template.department_id,
                 branch_id=template.branch_id, email=email, join_date=TODAY - timedelta(days=400),
                 employment_type="full_time", shift="morning", shift_start="09:00", shift_end="17:00",
                 base_salary=50000, currency="PKR", status="active", duty_hours=8.0,
                 employee_type="Admin", shift_code="M")
    s.add(e)
    s.flush()
    return e.id


@pytest.fixture(scope="module")
def ids() -> dict:
    s = SessionLocal()
    try:
        data = {"a": _make_staff(s, "a"), "b": _make_staff(s, "b")}
        s.commit()
        a, b = s.get(Employee, data["a"]), s.get(Employee, data["b"])
        data["a_email"], data["b_email"] = a.email, b.email
        data["a_user"], data["b_user"] = a.user_id, b.user_id
        return data
    finally:
        s.close()


@pytest.fixture(scope="module")
def staff_a(ids) -> TestClient:
    return _client(ids["a_email"], PASSWORD)


@pytest.fixture(scope="module")
def staff_b(ids) -> TestClient:
    return _client(ids["b_email"], PASSWORD)


@pytest.fixture(scope="module")
def hr_admin() -> TestClient:
    return _client("admin@oqc.local", "Admin@12345")


@pytest.fixture()
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture(scope="module", autouse=True)
def _purge(ids):
    """Remove everything this run created, so the suite can be run again against the same database."""
    yield
    s = SessionLocal()
    try:
        emp_ids = [ids["a"], ids["b"]]
        user_ids = [ids["a_user"], ids["b_user"]]
        run_ids = [r.id for r in s.query(PayrollRun).filter(PayrollRun.description.ilike(f"%{TAG}%"))]
        if run_ids:
            s.query(Payslip).filter(Payslip.payroll_run_id.in_(run_ids)).delete(synchronize_session=False)
        for model, col in ((EmployeeLedgerEntry, EmployeeLedgerEntry.employee_id),
                           (AttendanceChangeRequest, AttendanceChangeRequest.employee_id),
                           (HRAttendance, HRAttendance.employee_id),
                           (EmployeeRequest, EmployeeRequest.employee_id),
                           (StaffComplaint, StaffComplaint.employee_id),
                           (SalaryAdvance, SalaryAdvance.employee_id),
                           (Bonus, Bonus.employee_id),
                           (Violation, Violation.employee_id),
                           (Leave, Leave.employee_id),
                           (SalaryStructure, SalaryStructure.employee_id),
                           (Payslip, Payslip.employee_id)):
            s.query(model).filter(col.in_(emp_ids)).delete(synchronize_session=False)
        if run_ids:
            s.query(PayrollRun).filter(PayrollRun.id.in_(run_ids)).delete(synchronize_session=False)
        # Ledger lines this run wrote against employees it did not create, and any line carrying the tag.
        s.query(EmployeeLedgerEntry).filter(
            (EmployeeLedgerEntry.description.ilike(f"%{TAG}%"))
            | (EmployeeLedgerEntry.reference_type == f"pytest-{TAG}")
            | (EmployeeLedgerEntry.voucher_ref.in_([f"PR-{r:05d}" for r in run_ids]) if run_ids else False)
        ).delete(synchronize_session=False)
        s.query(AuditEvent).filter(AuditEvent.actor_id.in_(user_ids)).delete(synchronize_session=False)
        s.query(Notification).filter(Notification.user_id.in_(user_ids)).delete(synchronize_session=False)
        s.query(UserSession).filter(UserSession.user_id.in_(user_ids)).delete(synchronize_session=False)
        s.query(Employee).filter(Employee.id.in_(emp_ids)).delete(synchronize_session=False)
        s.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        # Audit rows the administrator wrote about this run's rows, and anything carrying the tag.
        s.query(AuditEvent).filter(AuditEvent.description.ilike(f"%{TAG}%")).delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()


def _flash(response) -> str:
    """Flash messages travel base64-encoded in a cookie on the redirect, so a 303 carries its message."""
    raw = re.search(r'oqc_flash="?([^";]+)"?', response.headers.get("set-cookie", ""))
    if not raw:
        return ""
    try:
        return " ".join(m.get("message", "") for m in
                        json.loads(base64.urlsafe_b64decode(unquote(raw.group(1)).encode()).decode()))
    except Exception:  # pragma: no cover - the cookie shape is the app's, not ours
        return ""


# --------------------------------------------------------------------------- Mark Attendance
def test_mark_attendance_in_then_out_then_a_third_press(staff_a, session, ids):
    session.query(HRAttendance).filter(HRAttendance.employee_id == ids["a"],
                                       HRAttendance.date == TODAY).delete(synchronize_session=False)
    session.commit()

    page = staff_a.get("/hr/me")
    assert page.status_code == 200
    assert "Today's Attendance" in page.text
    assert "Working Date" in page.text and "Attendance Time" in page.text
    assert "Mark Attendance" in page.text

    r = staff_a.post("/hr/me/attendance/mark", follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    rows = session.query(HRAttendance).filter(HRAttendance.employee_id == ids["a"],
                                              HRAttendance.date == TODAY).all()
    assert len(rows) == 1, "the first press opens exactly one session"
    row = rows[0]
    assert row.check_in is not None and row.check_out is None
    assert row.ip, "the request IP is recorded on the row"
    first_in = row.check_in

    # Their panel shows the range with the end still open, e.g. "07:10AM -".
    body = staff_a.get("/hr/me").text
    assert f"{first_in:%I:%M%p} -" in body

    r = staff_a.post("/hr/me/attendance/mark", follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    row = session.get(HRAttendance, row.id)
    assert row.check_in == first_in, "the second press must not move the check-in"
    assert row.check_out is not None
    first_out = row.check_out

    r = staff_a.post("/hr/me/attendance/mark", follow_redirects=False)
    assert r.status_code == 303
    assert "already marked" in _flash(r), "the third press says so rather than changing anything"
    session.expire_all()
    row = session.get(HRAttendance, row.id)
    assert row.check_in == first_in and row.check_out == first_out, "a third press changes nothing"
    assert session.query(HRAttendance).filter(HRAttendance.employee_id == ids["a"],
                                              HRAttendance.date == TODAY).count() == 1


def test_mark_attendance_uses_the_shared_late_rule(session, ids):
    """Lateness comes from svc.recompute_late_minutes, and the grace period from the branch property."""
    from app.services import hr as svc
    e = session.get(Employee, ids["a"])
    grace = svc.grace_minutes(session)
    assert grace >= 0
    assert svc.sync_grace_minutes(session) == grace, "the module constant follows the branch property"

    session.query(HRAttendance).filter(HRAttendance.employee_id == e.id,
                                       HRAttendance.date == TODAY).delete(synchronize_session=False)
    session.flush()
    late_by = grace + 25
    when = datetime.combine(TODAY, time(9, 0)) + timedelta(minutes=late_by)
    row, action, _msg = svc.punch(session, e, None, ip="127.0.0.1", when=when)
    assert action == "in"
    assert row.late_minutes == late_by and row.status == "late"

    # The same row through the shared helper gives the same number: one late rule, not two.
    assert svc.recompute_late_minutes(e, row) == late_by
    session.rollback()


def test_another_employee_cannot_mark_attendance_for_you(staff_b, session, ids):
    """The Mark Attendance route acts on the signed-in person only; B's press never touches A's row."""
    session.query(HRAttendance).filter(HRAttendance.employee_id.in_([ids["a"], ids["b"]]),
                                       HRAttendance.date == TODAY).delete(synchronize_session=False)
    session.commit()
    r = staff_b.post("/hr/me/attendance/mark", follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    assert session.query(HRAttendance).filter(HRAttendance.employee_id == ids["b"],
                                              HRAttendance.date == TODAY).count() == 1
    assert session.query(HRAttendance).filter(HRAttendance.employee_id == ids["a"],
                                              HRAttendance.date == TODAY).count() == 0


# --------------------------------------------------------------------------- Attendance Sheet
def _seed_sheet(session, employee_id: int) -> dict:
    """Three known days: one worked short, one absent, one on leave."""
    session.query(HRAttendance).filter(HRAttendance.employee_id == employee_id).delete(synchronize_session=False)
    short_day = TODAY - timedelta(days=7)
    absent_day = TODAY - timedelta(days=6)
    leave_day = TODAY - timedelta(days=5)
    session.add_all([
        HRAttendance(employee_id=employee_id, date=short_day, session="am", status="present",
                     check_in=datetime.combine(short_day, time(9, 30)),
                     check_out=datetime.combine(short_day, time(12, 0))),
        HRAttendance(employee_id=employee_id, date=absent_day, session="am", status="absent"),
        HRAttendance(employee_id=employee_id, date=leave_day, session="am", status="leave"),
    ])
    session.commit()
    return {"short": short_day, "absent": absent_day, "leave": leave_day}


def test_attendance_sheet_columns_and_filters(staff_a, session, ids):
    days = _seed_sheet(session, ids["a"])
    base = (f"/hr/me?tab=attendance&date_from={days['short']}&date_to={days['leave']}")
    body = staff_a.get(base).text
    for header in ["Attendance Date", "Attendance Type", "Login Time", "Logout Time",
                   "Late Coming", "Duration Shortage", "Change"]:
        assert header in body, header
    assert "From Date" in body and "To Date" in body and "Attendance Type" in body
    assert "Request to Change" in body

    for kind, expect_day, other_days in (("present", days["short"], [days["absent"], days["leave"]]),
                                         ("absent", days["absent"], [days["short"], days["leave"]]),
                                         ("leave", days["leave"], [days["short"], days["absent"]])):
        r = staff_a.get(f"{base}&attendance_type={kind}")
        assert r.status_code == 200
        rows = session.query(HRAttendance).filter(
            HRAttendance.employee_id == ids["a"], HRAttendance.date >= days["short"],
            HRAttendance.date <= days["leave"]).all()
        by_day = {x.date: x for x in rows}
        assert by_day[expect_day] is not None
        from app.services import hr as svc
        sheet = svc.attendance_sheet(session, session.get(Employee, ids["a"]),
                                     days["short"], days["leave"], kind)
        assert [x["date"] for x in sheet] == [expect_day], kind
        assert all(d not in [x["date"] for x in sheet] for d in other_days)

    # A date range outside the seeded days returns nothing.
    far = TODAY - timedelta(days=900)
    r = staff_a.get(f"/hr/me?tab=attendance&date_from={far}&date_to={far}")
    assert r.status_code == 200
    assert "No attendance between" in r.text


def test_shortage_and_late_reuse_the_shared_helpers(session, ids):
    from app.services import hr as svc
    days = _seed_sheet(session, ids["a"])
    e = session.get(Employee, ids["a"])
    sheet = svc.attendance_sheet(session, e, days["short"], days["short"])
    assert len(sheet) == 1
    r = sheet[0]
    duty = svc.session_duty_hours(e)          # 8 duty hours -> 4 per session
    worked = svc.worked_hours(r["row"])       # 09:30 -> 12:00 is 2.5 hours
    assert r["worked_hours"] == worked
    assert r["shortage_minutes"] == int(round((duty - worked) * 60))
    assert r["shortage_minutes"] > 0, "a short session shows a positive Duration Shortage"
    assert r["late_minutes"] == 30, "09:30 against a 09:00 start is 30 minutes"

    # An early arrival reads as a negative Late Coming, as theirs does.
    row = r["row"]
    row.check_in = datetime.combine(days["short"], time(8, 45))
    row.check_out = datetime.combine(days["short"], time(14, 0))
    row.late_minutes = 0
    session.flush()
    early = svc.attendance_sheet(session, e, days["short"], days["short"])[0]
    assert early["late_minutes"] == -15
    assert early["shortage_minutes"] < 0, "working over the session's duty hours reads negative"
    session.rollback()


# --------------------------------------------------------------------------- Request to Change
def test_request_to_change_from_a_row_then_the_cell_reads_requested(staff_a, hr_admin, session, ids):
    days = _seed_sheet(session, ids["a"])
    day = days["short"]
    row = session.query(HRAttendance).filter(HRAttendance.employee_id == ids["a"],
                                             HRAttendance.date == day).first()
    base = f"/hr/me?tab=attendance&date_from={day}&date_to={day}"
    before = staff_a.get(base).text
    assert "Request to Change" in before and ">Requested<" not in before

    r = staff_a.post("/hr/me/attendance-change", follow_redirects=False, data={
        "attendance_id": row.id, "new_status": "present", "new_check_in": "09:00",
        "new_check_out": "13:00", "user_remarks": f"The fingerprint reader was down {TAG}"})
    assert r.status_code == 303

    req = (session.query(AttendanceChangeRequest)
           .filter(AttendanceChangeRequest.employee_id == ids["a"],
                   AttendanceChangeRequest.attendance_date == day).first())
    assert req is not None and req.status == "pending"
    assert req.new_check_in == time(9, 0) and req.new_check_out == time(13, 0)
    assert TAG in (req.user_remarks or "")

    after = staff_a.get(base).text
    assert ">Requested<" in after, "the Change cell reads Requested once one is pending"
    assert "Request to Change" not in after, "and the button is gone"

    # The management queue picks it up with no change to that page, and deciding it rewrites the row.
    queue = hr_admin.get("/hr/attendance/change-requests?status=pending")
    assert queue.status_code == 200
    assert str(req.id) in queue.text

    r = hr_admin.post("/hr/attendance/change-requests/change-status", follow_redirects=False, data={
        "ids": [req.id], "status": "approved", "hr_remarks": f"Reader outage confirmed {TAG}"})
    assert r.status_code == 303
    session.expire_all()
    req = session.get(AttendanceChangeRequest, req.id)
    assert req.status == "approved" and req.decided_by_id
    row = session.get(HRAttendance, row.id)
    assert row.check_in.time() == time(9, 0) and row.check_out.time() == time(13, 0)

    # Decided, so the row offers the button again.
    assert "Request to Change" in staff_a.get(base).text


# --------------------------------------------------------------------------- Account Ledger
def _ledger_lines(session, employee_id: int, lines: list[tuple]) -> None:
    """A known set of ledger lines: (day offset, description, debit, credit)."""
    session.query(EmployeeLedgerEntry).filter(
        EmployeeLedgerEntry.employee_id == employee_id).delete(synchronize_session=False)
    for i, (offset, description, debit, credit) in enumerate(lines, start=1):
        session.add(EmployeeLedgerEntry(
            employee_id=employee_id, entry_date=TODAY - timedelta(days=offset),
            voucher_ref=f"T-{TAG[-4:]}-{i:02d}", description=f"{description} {TAG}",
            debit=debit, credit=credit, currency="PKR", source="adjustment",
            reference_type=f"pytest-{TAG}", reference_id=i))
    session.commit()


KNOWN_LINES = [
    (40, "Salary for the opening month", 50000, 0),
    (35, "Salary advance paid", 0, 10000),
    (20, "Salary for the month", 50000, 0),
    (18, "Violation fine", 0, 1500),
    (15, "Advance instalment recovered", 5000, 0),
    (5, "Bonus", 8000, 0),
]


def test_ledger_page_columns_and_running_balance(staff_a, session, ids):
    _ledger_lines(session, ids["a"], KNOWN_LINES)
    r = staff_a.get("/hr/me/ledger")
    assert r.status_code == 200
    body = r.text
    for header in ["Srl.", "Date", "VID", "Description", "Amount Dr.", "Amount Cr.", "Balance"]:
        assert header in body, header
    assert "From Date" in body and "To Date" in body and "Search" in body and "Print" in body

    from app.services import hr as svc
    report = svc.employee_ledger(session, session.get(Employee, ids["a"]))
    assert report["opening"] == 0.0, "no range means no opening balance"
    assert [r_["srl"] for r_ in report["rows"]] == list(range(1, len(KNOWN_LINES) + 1))
    expected, running = [], 0.0
    for _o, _d, debit, credit in sorted(KNOWN_LINES, key=lambda x: -x[0]):
        running = round(running + debit - credit, 2)
        expected.append(running)
    assert [r_["balance"] for r_ in report["rows"]] == expected
    assert report["closing"] == expected[-1] == 101500.0
    assert report["total_debit"] == 113000.0 and report["total_credit"] == 11500.0
    assert all(r_["entry"].employee_id == ids["a"] for r_ in report["rows"])


def test_ledger_opening_balance_for_a_range(staff_a, session, ids):
    _ledger_lines(session, ids["a"], KNOWN_LINES)
    from app.services import hr as svc
    e = session.get(Employee, ids["a"])
    start = TODAY - timedelta(days=17)          # the first four lines fall before the range
    report = svc.employee_ledger(session, e, start, TODAY)
    # Everything before the range collapses into one figure, as /finance/ledger does it.
    assert report["opening"] == 50000 - 10000 + 50000 - 1500
    assert [r_["description"].split(TAG)[0].strip() for r_ in report["rows"]] == \
        ["Advance instalment recovered", "Bonus"]
    assert report["rows"][0]["balance"] == report["opening"] + 5000
    assert report["closing"] == report["opening"] + 5000 + 8000
    assert report["closing"] == 101500.0, "the closing balance does not depend on where the range starts"

    r = staff_a.get(f"/hr/me/ledger?date_from={start}&date_to={TODAY}")
    assert r.status_code == 200
    assert "Opening balance" in r.text and "Closing balance" in r.text

    r = staff_a.get("/hr/me/ledger?q=Bonus")
    assert r.status_code == 200
    assert "Bonus" in r.text


def test_ledger_print_view(staff_a, session, ids):
    _ledger_lines(session, ids["a"], KNOWN_LINES)
    r = staff_a.get("/hr/me/ledger?print_view=1")
    assert r.status_code == 200
    assert "Ledger Report" in r.text
    assert "window.print()" in r.text
    assert "print-only" in r.text


def test_one_employee_cannot_see_another_ledger(staff_a, staff_b, session, ids):
    _ledger_lines(session, ids["a"], KNOWN_LINES)
    _ledger_lines(session, ids["b"], [(3, "B only line", 777, 0)])
    a_body = staff_a.get("/hr/me/ledger").text
    b_body = staff_b.get("/hr/me/ledger").text
    assert "Salary for the month" in a_body and "B only line" not in a_body
    assert "B only line" in b_body and "Salary for the month" not in b_body
    from app.services import hr as svc
    assert svc.employee_ledger(session, session.get(Employee, ids["b"]))["closing"] == 777.0


# --------------------------------------------------------------------------- posting and idempotency
def _test_run(session, employee_id: int) -> PayrollRun:
    """A payroll run of this suite's own, carrying one payslip with every component on it."""
    run = PayrollRun(period=f"{TODAY:%Y-%m}", status="posted", description=f"ESS ledger run {TAG}",
                     total_gross=60000, total_deductions=9500, total_net=50500, currency="PKR")
    session.add(run)
    session.flush()
    ps = Payslip(payroll_run_id=run.id, employee_id=employee_id, basic=50000, class_pay=0, allowances=2000,
                 bonus=8000, deductions=1500, advance_deduction=5000, attendance_deduction=1000,
                 gross=60000, net=50500, currency="PKR", status="posted",
                 details={"bonuses": [], "violations": [], "advances": [], "is_teacher": False})
    session.add(ps)
    session.flush()
    return run


def test_posting_a_payroll_run_writes_the_ledger_lines(session, ids):
    from app.services import hr as svc
    from app.services import payroll as pay
    session.query(EmployeeLedgerEntry).filter(
        EmployeeLedgerEntry.employee_id == ids["a"]).delete(synchronize_session=False)
    run = _test_run(session, ids["a"])
    written = pay.post_run_to_employee_ledgers(session, run)
    assert written == 3, "salary earned, the deductions, and the advance instalment recovered"
    session.commit()

    report = svc.employee_ledger(session, session.get(Employee, ids["a"]))
    by_source = {r["source"]: r for r in report["rows"]}
    assert by_source["salary"]["debit"] == 60000.0, "gross, less the bonuses which carry their own line"
    assert by_source["adjustment"]["credit"] == 2500.0, "1500 structure + 1000 attendance"
    assert by_source["advance_recovery"]["debit"] == 5000.0
    assert all(r["vid"] == f"PR-{run.id:05d}" for r in report["rows"])
    assert report["closing"] == round(60000 - 2500 + 5000, 2)


def test_reposting_the_same_run_does_not_double_the_ledger(session, ids):
    from app.services import payroll as pay
    run = (session.query(PayrollRun).filter(PayrollRun.description.ilike(f"%{TAG}%"))
           .order_by(PayrollRun.id.desc()).first())
    assert run is not None, "the posting test must have created a run"
    before = session.query(EmployeeLedgerEntry).filter(EmployeeLedgerEntry.employee_id == ids["a"]).count()
    assert pay.post_run_to_employee_ledgers(session, run) == 0
    assert pay.post_run_to_employee_ledgers(session, run) == 0
    session.commit()
    after = session.query(EmployeeLedgerEntry).filter(EmployeeLedgerEntry.employee_id == ids["a"]).count()
    assert after == before


def test_approving_the_same_advance_twice_does_not_double_the_ledger(hr_admin, session, ids):
    e = session.get(Employee, ids["a"])
    reason = f"School fees {TAG}"
    r = hr_admin.post("/hr/advances/new", follow_redirects=False, data={
        "employee_id": e.id, "amount": "12000", "installments": "3", "reason": reason,
        "request_date": str(TODAY - timedelta(days=2))})
    assert r.status_code == 303
    adv = session.query(SalaryAdvance).filter(SalaryAdvance.reason == reason).first()
    assert adv is not None and adv.status == "pending"
    # Scope every ledger assertion to this employee: an id freed by an earlier run is handed straight back
    # by SQLite, so a seeded line for somebody else can carry the same reference id.
    assert session.query(EmployeeLedgerEntry).filter(
        EmployeeLedgerEntry.employee_id == e.id,
        EmployeeLedgerEntry.reference_type == "advance",
        EmployeeLedgerEntry.reference_id == adv.id).count() == 0, "a pending advance posts nothing"

    for attempt in (1, 2):
        r = hr_admin.post("/hr/advances/change-status", follow_redirects=False, data={
            "ids": [adv.id], "status": "approved", "hr_remarks": f"Approved, attempt {attempt} {TAG}"})
        assert r.status_code == 303
    session.expire_all()
    lines = session.query(EmployeeLedgerEntry).filter(
        EmployeeLedgerEntry.employee_id == e.id,
        EmployeeLedgerEntry.reference_type == "advance",
        EmployeeLedgerEntry.reference_id == adv.id).all()
    assert len(lines) == 1, "approving twice writes one credit"
    assert float(lines[0].credit) == 12000.0 and float(lines[0].debit) == 0.0
    assert lines[0].employee_id == ids["a"]


def test_approving_the_same_bonus_and_violation_twice_does_not_double(hr_admin, session, ids):
    from app.models.hr_erp import BonusType, ViolationType
    e = session.get(Employee, ids["a"])
    bt = session.query(BonusType).filter(BonusType.status == "active").order_by(BonusType.sort_no).first()
    vt = session.query(ViolationType).filter(ViolationType.status == "active").order_by(ViolationType.sort_no).first()
    assert bt is not None and vt is not None

    reason = f"Full attendance {TAG}"
    hr_admin.post("/hr/bonuses/new", follow_redirects=False, data={
        "employee_id": e.id, "bonus_type_id": bt.id, "amount": "", "reason": reason,
        "period": f"{TODAY:%Y-%m}"})
    b = session.query(Bonus).filter(Bonus.reason == reason).first()
    assert b is not None

    remarks = f"Late three times {TAG}"
    hr_admin.post("/hr/violations/new", follow_redirects=False, data={
        "employee_id": e.id, "violation_type_id": vt.id, "date": str(TODAY), "remarks": remarks})
    v = session.query(Violation).filter(Violation.remarks == remarks).first()
    assert v is not None

    for attempt in (1, 2):
        hr_admin.post("/hr/bonuses/change-status", follow_redirects=False, data={
            "ids": [b.id], "status": "approved", "remarks": f"Approved {attempt} {TAG}"})
        hr_admin.post("/hr/violations/change-status", follow_redirects=False, data={
            "ids": [v.id], "status": "approved", "remarks": f"Confirmed {attempt} {TAG}"})
    session.expire_all()
    bonus_lines = session.query(EmployeeLedgerEntry).filter(
        EmployeeLedgerEntry.reference_type == "bonus", EmployeeLedgerEntry.reference_id == b.id).all()
    violation_lines = session.query(EmployeeLedgerEntry).filter(
        EmployeeLedgerEntry.reference_type == "violation", EmployeeLedgerEntry.reference_id == v.id).all()
    assert len(bonus_lines) == 1 and float(bonus_lines[0].debit) == float(b.amount)
    assert len(violation_lines) == 1 and float(violation_lines[0].credit) == float(v.deduction_amount)


def test_the_seed_is_idempotent(session):
    """app/seed/ess_ledger.py back-fills history and creates nothing on a second run."""
    from app.seed import ess_ledger
    ess_ledger.run(session)
    session.commit()
    before = session.query(EmployeeLedgerEntry).count()
    ess_ledger.run(session)
    session.commit()
    assert session.query(EmployeeLedgerEntry).count() == before


# --------------------------------------------------------------------------- Salary Slips
def test_salary_slip_print_per_month(staff_a, staff_b, session, ids):
    run = (session.query(PayrollRun).filter(PayrollRun.description.ilike(f"%{TAG}%"))
           .order_by(PayrollRun.id.desc()).first())
    if run is None:
        run = _test_run(session, ids["a"])
        session.commit()
    ps = session.query(Payslip).filter(Payslip.payroll_run_id == run.id).first()

    listing = staff_a.get("/hr/me?tab=payslips")
    assert listing.status_code == 200
    for header in ["Month", "Description", "Net Salary"]:
        assert header in listing.text, header
    assert f"/hr/me/payslips/{ps.id}/print" in listing.text

    r = staff_a.get(f"/hr/me/payslips/{ps.id}/print?print_view=1")
    assert r.status_code == 200
    assert "Salary Slip" in r.text and run.period in r.text
    assert "Net Salary" in r.text and "window.print()" in r.text

    # Another member of staff has no business reading it.
    assert staff_b.get(f"/hr/me/payslips/{ps.id}/print").status_code == 403


# --------------------------------------------------------------------------- the launchpad's tabs
@pytest.mark.parametrize("tab", ["overview", "schedule", "attendance", "requests", "leaves", "payslips",
                                 "violations", "bonuses", "complaints", "development", "grievance"])
def test_every_tab_renders(staff_a, tab):
    r = staff_a.get(f"/hr/me?tab={tab}")
    assert r.status_code == 200
    assert "No employee record" not in r.text


def test_schedule_tab_for_a_non_teacher(staff_a):
    body = staff_a.get("/hr/me?tab=schedule").text
    assert "My working pattern" in body
    assert "Duty hours" in body and "Shift" in body


def test_schedule_tab_for_a_teacher():
    """A teacher gets their own upcoming classes, which is what theirs opens."""
    c = _client("teacher1@oqc.local", "Teacher@123")
    body = c.get("/hr/me?tab=schedule").text
    assert "Classes next 14 days" in body
    assert "My working pattern" not in body


def test_requests_tab_holds_the_three_lists_and_creates_an_advance(staff_a, session, ids):
    body = staff_a.get("/hr/me?tab=requests").text
    for block in ["Leave Requests", "Employee Requests", "Advance Requests",
                  "Create Leave Request", "Create Employee Request", "Create Advance Request"]:
        assert block in body, block
    assert "No Of Installments" in body and "Hr Remarks" in body

    r = staff_a.post("/hr/me/advance", follow_redirects=False, data={
        "request_date": str(TODAY), "amount": "4500", "installments": "2",
        "reason": f"Medical bill {TAG}"})
    assert r.status_code == 303
    a = session.query(SalaryAdvance).filter(SalaryAdvance.reason == f"Medical bill {TAG}").first()
    assert a is not None and a.employee_id == ids["a"] and a.status == "pending"
    assert a.installments == 2 and float(a.amount) == 4500.0
    assert a.hr_remarks is None, "Hr Remarks belongs to whoever decides it"
    assert f"Medical bill {TAG}" in staff_a.get("/hr/me?tab=requests").text


def test_requests_tab_rejects_a_zero_advance(staff_a, session, ids):
    before = session.query(SalaryAdvance).filter(SalaryAdvance.employee_id == ids["a"]).count()
    r = staff_a.post("/hr/me/advance", follow_redirects=False, data={
        "request_date": str(TODAY), "amount": "0", "installments": "1", "reason": f"Nothing {TAG}"})
    assert r.status_code == 303
    session.expire_all()
    assert session.query(SalaryAdvance).filter(SalaryAdvance.employee_id == ids["a"]).count() == before


def test_bonuses_tab_is_read_only_and_separate_from_violations(staff_a, session, ids):
    bonuses = staff_a.get("/hr/me?tab=bonuses").text
    violations = staff_a.get("/hr/me?tab=violations").text
    assert "Bonus Type" in bonuses and "Acceptance Date" in bonuses
    assert "Bonus Type" not in violations, "violations stay on their own tab"
    assert "Severity" in violations and "Deduction" in violations
    assert "Severity" not in bonuses
    # Read-only: no form posts from the bonuses tab.
    assert "<form method=\"post\"" not in bonuses


def test_complaints_tab_creates_and_lists_only_your_own(staff_a, staff_b, session, ids):
    body = staff_a.get("/hr/me?tab=complaints").text
    assert "Create Complaint" in body and "Complaint Type" in body
    assert "confidential grievance channel" in body and "tab=grievance" in body

    title = f"Broken headset {TAG}"
    r = staff_a.post("/hr/complaints/new", follow_redirects=False, data={
        "complaint_type": "Facility", "title": title, "description": f"Third one this term {TAG}",
        "next": "/hr/me?tab=complaints"})
    assert r.status_code == 303
    assert "/hr/me?tab=complaints" in r.headers.get("location", "")
    c = session.query(StaffComplaint).filter(StaffComplaint.title == title).first()
    assert c is not None and c.employee_id == ids["a"]
    assert title in staff_a.get("/hr/me?tab=complaints").text
    assert title not in staff_b.get("/hr/me?tab=complaints").text


def test_overview_points_at_the_ledger(staff_a):
    body = staff_a.get("/hr/me").text
    assert "/hr/me/ledger" in body
