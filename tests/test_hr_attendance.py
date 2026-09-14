"""ERP Time and Attendance Management tests (docs/AUDIT_HUMAN_RESOURCE.md, Level 3).

Covers Daily Attendance and its inline grid edit, Attendance Change Requests (including the rewrite an
approval performs), Leave Assignment and the entitlement wiring on leave approval, the Employees Progress
Sheet and the Attendance Report.

The suite is re-runnable against the same database: everything it creates is either keyed to fixed dates
far outside the seeded window (April 2027) or tagged with ``MARK`` and cleared by the ``fixtures`` fixture,
and every leave it opens is closed again so the overlap guard never blocks a later run.
"""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_hrB.db")

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: E402  (imported after DATABASE_URL is settled)
from app.models.hr_erp import AttendanceChangeRequest, Holiday, LeaveEntitlement, ProgressNote
from app.models.people import Employee, HRAttendance, Leave
from app.services import hr as svc

MARK = "pytest-hr-attendance"
DAY = date(2027, 4, 5)            # Monday, well outside the seeded attendance window
SELF_DAY = date(2027, 4, 6)       # Tuesday, used by the self-service posts
HOLIDAY_DAY = date(2027, 4, 7)    # Wednesday, made a holiday by the fixture
LEAVE_START, LEAVE_END = date(2027, 4, 5), date(2027, 4, 9)    # Mon-Fri, one holiday inside -> 4 working days
OVER_START, OVER_END = date(2027, 4, 12), date(2027, 4, 16)    # Mon-Fri, no holiday -> 5 working days


def _client(username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}"
    return c


@pytest.fixture()
def hr(client):
    r = client.post("/login", data={"username": "hr@oqc.local", "password": "People@123"}, follow_redirects=False)
    assert r.status_code == 303
    return client


@pytest.fixture()
def emp(db) -> Employee:
    return db.query(Employee).filter(Employee.status.in_(["active", "probation"])).order_by(Employee.id).first()


@pytest.fixture()
def fixtures(db, emp):
    """Reset everything this module writes, so a second run starts from the same place."""
    db.query(Leave).filter(Leave.person_type == "employee",
                           Leave.leave_type.in_(["pytest_leave", "pytest_over"])).delete(synchronize_session=False)
    db.query(ProgressNote).filter(ProgressNote.detail.like(f"%{MARK}%")).delete(synchronize_session=False)
    db.query(LeaveEntitlement).filter(LeaveEntitlement.notes == MARK).delete(synchronize_session=False)
    db.query(AttendanceChangeRequest).filter(
        AttendanceChangeRequest.attendance_date.in_([DAY, SELF_DAY])).delete(synchronize_session=False)
    if not db.query(Holiday).filter(Holiday.name == "Pytest Spring Break").first():
        db.add(Holiday(name="Pytest Spring Break", holiday_date=HOLIDAY_DAY, shift_group="all", status="active",
                       notes=MARK))
    db.commit()
    yield
    db.rollback()


def _entitlement(db, emp: Employee, leave_type: str, total: float) -> LeaveEntitlement:
    ent = LeaveEntitlement(employee_id=emp.id, leave_type=leave_type, total_assigned=total, consumed=0.0,
                           expiry_date=date(2027, 12, 31), status="active", notes=MARK)
    db.add(ent)
    db.commit()
    return ent


# --------------------------------------------------------------------------- pages
@pytest.mark.parametrize("url", [
    "/hr/attendance",
    "/hr/attendance?tab=daily",
    "/hr/attendance?tab=daily&shift=morning",
    "/hr/attendance?tab=daily&shift=night&day=2026-09-01",
    "/hr/attendance?tab=monthly",
    "/hr/attendance?tab=corrections",
    "/hr/attendance?tab=late",
    "/hr/attendance/change-requests",
    "/hr/attendance/change-requests?status=pending",
    "/hr/attendance/change-requests?status=approved",
    "/hr/attendance/change-requests?status=rejected",
    "/hr/attendance/change-requests?status=cancelled",
    "/hr/attendance/change-requests?shift=morning&q=a",
    "/hr/attendance/change-requests?date_from=2026-07-01&date_to=2026-12-31",
    "/hr/attendance/report",
    "/hr/attendance/report?date_from=2026-08-01&date_to=2026-09-14",
    "/hr/attendance/report?shift=morning&employee_type=Academics",
    "/hr/attendance/report?designation=Teacher%20Remote",
    "/hr/attendance/report?print_view=1",
    "/hr/leave-entitlements",
    "/hr/leave-entitlements?leave_type=casual&status=active",
    "/hr/leave-entitlements?shift=morning&q=a",
    "/hr/progress-sheet",
    "/hr/progress-sheet?rating=unrated",
    "/hr/progress-sheet?rating=4",
    "/hr/progress-sheet?date_from=2026-08-01&date_to=2026-09-14",
    "/hr/leaves",
    "/hr/leaves?status=approved",
    "/hr/leaves/new",
    "/hr/me?tab=attendance",
])
def test_pages(admin, url):
    assert admin.get(url).status_code == 200, url


def test_tiles_and_department_filter(admin, db):
    dept = db.query(Employee.department_id).filter(Employee.department_id.isnot(None)).first()
    assert admin.get(f"/hr/attendance/report?department={dept[0]}").status_code == 200
    for eid in [e.id for e in db.query(Employee).limit(1)]:
        assert admin.get(f"/hr/progress-sheet?employee={eid}").status_code == 200
        assert admin.get(f"/hr/leave-entitlements?employee={eid}").status_code == 200
        assert admin.get(f"/hr/attendance/change-requests?employee={eid}").status_code == 200


def test_report_csv(admin):
    r = admin.get("/hr/attendance/report?date_from=2026-08-01&date_to=2026-09-14&format=csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "Shortage Hours" in r.text.splitlines()[0]


def test_hr_officer_can_reach_the_group(hr):
    for url in ["/hr/attendance", "/hr/attendance/change-requests", "/hr/leave-entitlements", "/hr/progress-sheet"]:
        assert hr.get(url).status_code == 200, url


# --------------------------------------------------------------------------- Daily Attendance grid
def test_grid_row_saves_status_and_times(admin, db, emp, fixtures):
    r = admin.post("/hr/attendance/row", data={"employee_id": emp.id, "day": str(DAY), "session": "pm",
                                               "status": "present", "check_in": "13:00", "check_out": "17:30",
                                               "note": MARK}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    row = db.query(HRAttendance).filter(HRAttendance.employee_id == emp.id, HRAttendance.date == DAY,
                                        HRAttendance.session == "pm").one()
    assert row.check_in.strftime("%H:%M") == "13:00"
    assert row.check_out.strftime("%H:%M") == "17:30"
    assert row.status in ("present", "late")
    assert svc.worked_hours(row) == 4.5


def test_duration_and_shortage(db, emp):
    duty = svc.session_duty_hours(emp)
    row = HRAttendance(employee_id=emp.id, date=DAY, session="am", status="present",
                       check_in=datetime.combine(DAY, time(9, 0)),
                       check_out=datetime.combine(DAY, time(11, 0)))
    assert svc.worked_hours(row) == 2.0
    assert svc.shortage_hours(row, emp) == round(max(0.0, duty - 2.0), 2)
    row.status = "absent"
    assert svc.worked_hours(row) == 0.0 and svc.shortage_hours(row, emp) == 0.0


# --------------------------------------------------------------------------- Attendance Change Requests
def test_approved_change_request_rewrites_the_attendance_row(admin, db, emp, fixtures):
    admin.post("/hr/attendance/row", data={"employee_id": emp.id, "day": str(DAY), "session": "am",
                                           "status": "absent", "note": MARK}, follow_redirects=False)
    r = admin.post("/hr/attendance/change-requests/new",
                   data={"employee_id": emp.id, "attendance_date": str(DAY), "session": "am",
                         "new_status": "present", "new_check_in": "09:05", "new_check_out": "17:05",
                         "user_remarks": f"{MARK}: the reader missed my punch."}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    req = (db.query(AttendanceChangeRequest)
           .filter(AttendanceChangeRequest.employee_id == emp.id, AttendanceChangeRequest.attendance_date == DAY,
                   AttendanceChangeRequest.session == "am")
           .order_by(AttendanceChangeRequest.id.desc()).first())
    assert req is not None and req.status == "pending"
    assert req.old_status == "absent"                      # the old values were captured from the row
    assert db.get(HRAttendance, req.attendance_id).correction_requested is True

    r = admin.post("/hr/attendance/change-requests/change-status",
                   data={"ids": [req.id], "status": "approved", "hr_remarks": f"{MARK}: supervisor confirmed."},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    req = db.get(AttendanceChangeRequest, req.id)
    assert req.status == "approved" and req.decided_by_id and req.decided_at
    row = db.query(HRAttendance).filter(HRAttendance.employee_id == emp.id, HRAttendance.date == DAY,
                                        HRAttendance.session == "am").one()
    assert row.id == req.attendance_id
    assert row.status in ("present", "late")               # 'present', or 'late' when 09:05 is past their shift start
    assert row.check_in.strftime("%H:%M") == "09:05"
    assert row.check_out.strftime("%H:%M") == "17:05"
    assert row.correction_requested is False and row.correction_status == "approved"


def test_change_status_needs_a_selection_and_remarks(admin, db, emp, fixtures):
    r = admin.post("/hr/attendance/change-requests/change-status",
                   data={"status": "approved", "hr_remarks": "no rows picked"}, follow_redirects=False)
    assert r.status_code == 303
    r = admin.post("/hr/attendance/change-requests/change-status", data={"ids": [1], "status": "approved"},
                   follow_redirects=False)
    assert r.status_code == 303


def _teacher_employee(db) -> Employee:
    from app.models.core import User
    u = db.query(User).filter(User.email == "teacher1@oqc.local").one()
    return db.query(Employee).filter(Employee.user_id == u.id).one()


def test_staff_raise_their_own_change_request(admin, db, fixtures):
    teacher = _client("teacher1@oqc.local", "Teacher@123")
    assert teacher.get("/hr/me?tab=attendance").status_code == 200
    me = _teacher_employee(db)
    # Give the day a row first, so the request has old values to capture automatically.
    admin.post("/hr/attendance/row", data={"employee_id": me.id, "day": str(SELF_DAY), "session": "am",
                                           "status": "absent", "note": MARK}, follow_redirects=False)
    r = teacher.post("/hr/me/attendance-change",
                     data={"attendance_date": str(SELF_DAY), "session": "am", "new_status": "present",
                           "new_check_in": "09:00", "new_check_out": "17:00",
                           "user_remarks": f"{MARK}: I taught the whole shift."}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    req = (db.query(AttendanceChangeRequest)
           .filter(AttendanceChangeRequest.employee_id == me.id,
                   AttendanceChangeRequest.attendance_date == SELF_DAY)
           .order_by(AttendanceChangeRequest.id.desc()).first())
    assert req is not None and req.status == "pending"
    assert req.old_status == "absent"                              # captured from the existing row
    assert req.new_check_in == time(9, 0) and req.new_check_out == time(17, 0)


# --------------------------------------------------------------------------- Leave Assignment
def test_entitlement_create_edit_and_bulk_assign(admin, db, emp, fixtures):
    r = admin.post("/hr/leave-entitlements/new",
                   data={"employee_id": emp.id, "leave_type": "pytest_manual", "total_assigned": "9",
                         "consumed": "1", "expiry_date": "2027-12-31", "status": "active", "notes": MARK},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    ent = (db.query(LeaveEntitlement)
           .filter(LeaveEntitlement.employee_id == emp.id, LeaveEntitlement.leave_type == "pytest_manual")
           .order_by(LeaveEntitlement.id.desc()).first())
    assert ent is not None and float(ent.total_assigned) == 9.0 and ent.remaining == 8.0

    r = admin.post(f"/hr/leave-entitlements/{ent.id}/edit",
                   data={"leave_type": "pytest_manual", "total_assigned": "15", "consumed": "2",
                         "expiry_date": "2027-12-31", "status": "active", "notes": MARK}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    ent = db.get(LeaveEntitlement, ent.id)
    assert float(ent.total_assigned) == 15.0 and ent.remaining == 13.0

    active = db.query(Employee).filter(Employee.status.in_(["active", "probation", "on_leave"])).count()
    r = admin.post("/hr/leave-entitlements/bulk-assign",
                   data={"leave_type": "pytest_bulk", "total_assigned": "5", "expiry_date": "2027-12-31",
                         "notes": "pytest bulk assignment"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.query(LeaveEntitlement).filter(LeaveEntitlement.leave_type == "pytest_bulk").count() == active
    # Re-running skips everyone, so the count never doubles.
    assert admin.post("/hr/leave-entitlements/bulk-assign",
                      data={"leave_type": "pytest_bulk", "total_assigned": "5", "expiry_date": "2027-12-31"},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(LeaveEntitlement).filter(LeaveEntitlement.leave_type == "pytest_bulk").count() == active


# --------------------------------------------------------------------------- entitlement wiring on leave
def test_working_days_exclude_holidays_and_sundays(db, emp, fixtures):
    assert svc.working_days(db, LEAVE_START, LEAVE_END, emp) == 4      # five weekdays, one holiday
    assert svc.working_days(db, OVER_START, OVER_END, emp) == 5
    assert svc.is_working_day(db, HOLIDAY_DAY, emp) is False
    assert svc.is_working_day(db, date(2027, 4, 11), emp) is False     # Sunday


def _create_leave(admin, db, emp, leave_type: str, start: date, end: date) -> Leave:
    r = admin.post("/hr/leaves/new", data={"employee_id": emp.id, "leave_type": leave_type, "start_date": str(start),
                                           "end_date": str(end), "reason": f"{MARK} leave"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    l = (db.query(Leave).filter(Leave.person_type == "employee", Leave.employee_id == emp.id,
                                Leave.leave_type == leave_type, Leave.start_date == start)
         .order_by(Leave.id.desc()).first())
    assert l is not None and l.status == "pending"
    return l


def test_approval_consumes_the_entitlement_and_cancelling_gives_it_back(admin, db, emp, fixtures):
    ent = _entitlement(db, emp, "pytest_leave", 10)
    l = _create_leave(admin, db, emp, "pytest_leave", LEAVE_START, LEAVE_END)

    r = admin.post(f"/hr/leaves/{l.id}/decide", data={"decision": "approve", "note": "Cover arranged."},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    l, ent = db.get(Leave, l.id), db.get(LeaveEntitlement, ent.id)
    assert l.status == "approved"
    assert l.days_applied == 4.0            # the holiday inside the range was not consumed
    assert l.entitlement_id == ent.id
    assert float(ent.consumed) == 4.0 and ent.remaining == 6.0

    r = admin.post(f"/hr/leaves/{l.id}/decide", data={"decision": "cancel", "note": "Withdrawn."},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    l, ent = db.get(Leave, l.id), db.get(LeaveEntitlement, ent.id)
    assert l.status == "cancelled" and l.entitlement_id is None and l.days_applied is None
    assert float(ent.consumed) == 0.0


def test_reversing_an_approval_returns_the_days(admin, db, emp, fixtures):
    ent = _entitlement(db, emp, "pytest_leave", 10)
    l = _create_leave(admin, db, emp, "pytest_leave", LEAVE_START, LEAVE_END)
    assert admin.post(f"/hr/leaves/{l.id}/decide", data={"decision": "approve"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert float(db.get(LeaveEntitlement, ent.id).consumed) == 4.0
    assert admin.post(f"/hr/leaves/{l.id}/decide", data={"decision": "reject", "note": "Reversed."},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    assert float(db.get(LeaveEntitlement, ent.id).consumed) == 0.0
    assert db.get(Leave, l.id).status == "rejected"


def test_over_entitlement_needs_the_override(admin, db, emp, fixtures):
    ent = _entitlement(db, emp, "pytest_over", 2)
    l = _create_leave(admin, db, emp, "pytest_over", OVER_START, OVER_END)   # 5 working days against 2 left

    r = admin.post(f"/hr/leaves/{l.id}/decide", data={"decision": "approve", "note": "no override"},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Leave, l.id).status == "pending"              # refused
    assert float(db.get(LeaveEntitlement, ent.id).consumed) == 0.0

    r = admin.post(f"/hr/leaves/{l.id}/decide",
                   data={"decision": "approve", "override": "1",
                         "rationale": "Head of People approved unpaid carry-over for a family bereavement."},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    l, ent = db.get(Leave, l.id), db.get(LeaveEntitlement, ent.id)
    assert l.status == "approved" and l.days_applied == 5.0
    assert float(ent.consumed) == 5.0
    # leave the database as we found it so the overlap guard never blocks a later run
    assert admin.post(f"/hr/leaves/{l.id}/decide", data={"decision": "cancel", "note": "Test teardown."},
                      follow_redirects=False).status_code == 303


# --------------------------------------------------------------------------- Progress Sheet
def test_progress_create_and_rate(admin, db, emp, fixtures):
    r = admin.post("/hr/progress-sheet/new",
                   data={"employee_id": emp.id, "working_date": str(DAY),
                         "detail": f"{MARK}: closed the week's attendance exceptions."}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    note = (db.query(ProgressNote).filter(ProgressNote.employee_id == emp.id, ProgressNote.working_date == DAY)
            .order_by(ProgressNote.id.desc()).first())
    assert note is not None and note.manager_rating is None

    r = admin.post(f"/hr/progress-sheet/{note.id}/rate",
                   data={"manager_rating": "4", "manager_comment": f"{MARK} solid work"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    note = db.get(ProgressNote, note.id)
    assert note.manager_rating == 4 and note.rated_by_id is not None and note.rated_at is not None

    assert admin.post(f"/hr/progress-sheet/{note.id}/rate", data={"manager_rating": "9"},
                      follow_redirects=False).status_code == 303   # out of range is refused with a flash
    db.expire_all()
    assert db.get(ProgressNote, note.id).manager_rating == 4


def test_staff_record_their_own_progress(db, fixtures):
    teacher = _client("teacher1@oqc.local", "Teacher@123")
    before = db.query(ProgressNote).filter(ProgressNote.working_date == SELF_DAY).count()
    r = teacher.post("/hr/me/progress",
                     data={"working_date": str(SELF_DAY), "detail": f"{MARK}: six classes taught, records updated."},
                     follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.query(ProgressNote).filter(ProgressNote.working_date == SELF_DAY).count() == before + 1


# --------------------------------------------------------------------------- seed expectations
def test_seed_data(db):
    assert db.query(Holiday).filter(Holiday.status == "active").count() >= 10
    assert db.query(Holiday).filter(Holiday.end_date.isnot(None)).count() >= 1     # the multi-day Eid break
    assert db.query(LeaveEntitlement).count() >= 30
    assert db.query(AttendanceChangeRequest).count() >= 40
    assert db.query(ProgressNote).count() >= 120
    for status in ["pending", "approved", "rejected", "cancelled"]:
        assert db.query(AttendanceChangeRequest).filter(AttendanceChangeRequest.status == status).count() > 0
    rated = db.query(ProgressNote).filter(ProgressNote.manager_rating.isnot(None)).count()
    assert 30 <= rated <= db.query(ProgressNote).count()
