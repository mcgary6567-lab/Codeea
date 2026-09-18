"""ERP parity tests for the Human Resource gaps closed in the parity walk.

Covers the HR Home counters (gender, contract ends, today's attendance per shift, pending requests), the
Attendance Summary pivot with its Search Options, CSV and print view, the Staff Notices list with its four
tiles and the self portal's Notifications page, the bell notification a live notice sends, and the
permission boundaries around all of it.

The suite is re-runnable against the same database: nothing here asserts an absolute count that only
holds on a fresh seed, everything it creates carries a unique tag and is removed in a module-scoped
teardown, and the employees whose contract dates it borrows are restored before the test ends.

Run against a private database:
    $env:DATABASE_URL='sqlite:///./data/oqc_parA.db'; .venv/Scripts/python.exe -m pytest tests/test_parity_hr.py
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from uuid import uuid4

# The application reads DATABASE_URL at import time; set it before anything imports the app.
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_parA.db")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import or_

from app.database import SessionLocal
from app.main import app
from app.models.core import Notification
from app.models.hr_erp import AttendanceChangeRequest, EmployeeRequest, StaffComplaint, StaffNotice
from app.models.people import Bonus, Employee, HRAttendance, Leave, SalaryAdvance, Violation
from app.services import erp_home
from app.web.ess_portal import live_notices_for
from app.web.hr import attendance_pivot, notice_state, notice_state_filter

TAG = f"pytest-{uuid4().hex[:8]}"
LEFT = ["resigned", "terminated"]


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


@pytest.fixture(scope="module")
def a_parent() -> TestClient:
    return _client("parent1@oqc.local", "Parent@123")


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
        s.query(Notification).filter(Notification.title.ilike(like)).delete(synchronize_session=False)
        s.query(StaffNotice).filter(StaffNotice.title.ilike(like)).delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()


def live_employees(s):
    return s.query(Employee).filter(Employee.status.notin_(LEFT))


def teacher_employee(s) -> Employee:
    from app.models.core import User
    u = s.query(User).filter(User.email == "teacher1@oqc.local").first()
    e = s.query(Employee).filter(Employee.user_id == u.id).first()
    assert e is not None, "teacher1 must be linked to an employee record"
    return e


# --------------------------------------------------------------------------- seed
def test_seed_gives_the_panels_something_to_show(session):
    today = date.today()
    assert live_employees(session).filter(Employee.contract_end_date.isnot(None)).count() >= 8
    assert (live_employees(session).filter(Employee.contract_end_date >= today,
                                           Employee.contract_end_date <= today + timedelta(days=60)).count() >= 4)
    assert session.query(StaffNotice).count() >= 10
    states = {notice_state(n, today) for n in session.query(StaffNotice)}
    assert {"live", "scheduled", "expired", "inactive"} <= states
    assert session.query(StaffNotice).filter(StaffNotice.audience != "all").count() >= 3


# --------------------------------------------------------------------------- HR Home
def test_hr_home_renders_every_panel(hr_admin):
    body = hr_admin.get("/home/hr").text
    for label in ["Gender Distribution", "Total Male", "Total Female", "Total Employee",
                  "Contract Ends (Within 2 months)", "Start Date", "End Date", "Remaining Days",
                  "Attendance Status", "Late Coming", "On Leave",
                  "Pending Requests", "Attendance Change", "Complaints", "Leave Request", "Emp Requests",
                  "Violations", "Advances", "Bonuses"]:
        assert label in body, label
    for href in ["/hr/employees?gender=male", "/hr/employees?gender=female", "/hr/employees",
                 "/hr/attendance/change-requests?status=pending", "/hr/complaints?status=pending",
                 "/hr/leaves?status=pending", "/hr/requests?status=pending", "/hr/violations?status=pending",
                 "/hr/advances?status=pending", "/hr/bonuses?status=pending"]:
        assert f'href="{href}"' in body, href


def test_gender_figures_equal_a_direct_count(session):
    got = {c["label"]: c["value"] for c in erp_home.hr_gender_distribution(session)}
    live = live_employees(session)
    assert got["Total Male"] == live.filter(Employee.gender == "male").count()
    assert got["Total Female"] == live.filter(Employee.gender == "female").count()
    assert got["Total Employee"] == live.count()


def test_pending_request_figures_equal_a_direct_count(session):
    got = {c["label"]: c["value"] for c in erp_home.hr_pending_requests(session)}
    assert got["Attendance Change"] == session.query(AttendanceChangeRequest).filter(AttendanceChangeRequest.status == "pending").count()
    assert got["Complaints"] == session.query(StaffComplaint).filter(StaffComplaint.status == "pending").count()
    assert got["Leave Request"] == session.query(Leave).filter(Leave.person_type == "employee", Leave.status == "pending").count()
    assert got["Emp Requests"] == session.query(EmployeeRequest).filter(EmployeeRequest.status == "pending").count()
    assert got["Violations"] == session.query(Violation).filter(Violation.approval_status == "pending").count()
    assert got["Advances"] == session.query(SalaryAdvance).filter(SalaryAdvance.status == "pending").count()
    assert got["Bonuses"] == session.query(Bonus).filter(Bonus.status == "pending").count()


def test_todays_attendance_figures_equal_a_direct_count(session):
    panel = erp_home.hr_todays_attendance(session)
    day = panel["day"]
    assert day <= date.today()
    assert panel["shifts"], "at least one shift block"
    live = live_employees(session).all()
    for block in panel["shifts"]:
        staff = [e for e in live if (e.shift or "") == block["shift"]]
        got = {c["label"]: c["value"] for c in block["counts"]}
        present = absent = leave = late = 0
        for e in staff:
            rows = session.query(HRAttendance).filter(HRAttendance.employee_id == e.id, HRAttendance.date == day).all()
            if not rows:
                continue
            statuses = [r.status for r in rows]
            if "leave" in statuses:
                leave += 1
            elif any(s in ("present", "late") for s in statuses):
                present += 1
            elif "absent" in statuses:
                absent += 1
            if any(r.status == "late" or (r.late_minutes or 0) > 0 for r in rows):
                late += 1
        assert got["Total"] == len(staff), block["shift"]
        assert got["Present"] == present, block["shift"]
        assert got["Absent"] == absent, block["shift"]
        assert got["On Leave"] == leave, block["shift"]
        assert got["Late Coming"] == late, block["shift"]
        for c in block["counts"]:
            assert c["href"].startswith(f"/hr/attendance?tab=daily&day={day.isoformat()}&shift={block['shift']}")


def test_attendance_figure_links_open_the_daily_board_filtered(hr_admin, session):
    panel = erp_home.hr_todays_attendance(session)
    block = panel["shifts"][0]
    for c in block["counts"]:
        assert hr_admin.get(c["href"]).status_code == 200, c["href"]


def test_contract_table_lists_only_ends_within_60_days(hr_admin, session):
    today = date.today()
    employees = live_employees(session).order_by(Employee.id).limit(3).all()
    assert len(employees) == 3
    original = {e.id: e.contract_end_date for e in employees}
    soon, later, past = employees
    try:
        soon.contract_end_date = today + timedelta(days=30)
        later.contract_end_date = today + timedelta(days=90)
        past.contract_end_date = today - timedelta(days=5)
        session.commit()
        rows = erp_home.hr_contract_ends(session)
        by_id = {r["employee"].id: r for r in rows}
        assert soon.id in by_id and by_id[soon.id]["remaining"] == 30
        assert by_id[soon.id]["start"] == soon.join_date and by_id[soon.id]["end"] == soon.contract_end_date
        assert by_id[soon.id]["label"] == f"{soon.employee_code}-{soon.full_name}"
        assert later.id not in by_id, "90 days out is beyond the two-month window"
        assert past.id not in by_id, "a contract that already ended is not an upcoming end"
        assert all(0 <= r["remaining"] <= 60 for r in rows)
        assert [r["end"] for r in rows] == sorted(r["end"] for r in rows)
        body = hr_admin.get("/home/hr").text
        assert f'href="/hr/employees/{soon.id}"' in body
        assert f"{soon.employee_code}-{soon.full_name}" in body
    finally:
        for e in employees:
            session.get(Employee, e.id).contract_end_date = original[e.id]
        session.commit()


def test_contract_table_empty_state(session):
    far = date.today() + timedelta(days=3650)
    assert erp_home.hr_contract_ends(session, today=far) == []


def test_contract_end_on_the_employee_record(hr_admin, session):
    e = live_employees(session).order_by(Employee.id.desc()).first()
    original = e.contract_end_date
    target = date.today() + timedelta(days=200)
    try:
        r = hr_admin.post(f"/hr/employees/{e.id}/edit", follow_redirects=False, data={
            "full_name": e.full_name, "designation": e.designation, "gender": e.gender, "shift": e.shift,
            "employment_type": e.employment_type, "status": e.status, "currency": e.currency or "PKR",
            "contract_end_date": target.isoformat(), "rationale": f"Contract renewed {TAG}"})
        assert r.status_code == 303
        session.expire_all()
        assert session.get(Employee, e.id).contract_end_date == target
        body = hr_admin.get(f"/hr/employees/{e.id}").text
        assert "Contract ends" in body
        assert 'name="contract_end_date"' in hr_admin.get(f"/hr/employees/{e.id}/edit").text
        assert 'name="contract_end_date"' in hr_admin.get("/hr/employees/new").text
    finally:
        session.get(Employee, e.id).contract_end_date = original
        session.commit()


# --------------------------------------------------------------------------- Attendance Summary
@pytest.mark.parametrize("qs", [
    "", "view=flat", "shift=morning", "employee_type=Academics", "attendance_type=late", "attendance_type=absent",
    "attendance_type=present&view=flat", "designation=Teacher%20Remote", "department=1",
    "date_from=2026-08-01&date_to=2026-08-31", "date_from=2026-09-10&date_to=2026-09-01",  # reversed dates swap
    "format=csv", "view=flat&format=csv", "print_view=1", "view=nonsense",
])
def test_summary_under_each_filter(hr_admin, qs):
    r = hr_admin.get(f"/hr/attendance/summary?{qs}")
    assert r.status_code == 200
    if "format=csv" in qs:
        assert r.headers["content-type"].startswith("text/csv")
        first = r.text.splitlines()[0]
        assert ("Total Sessions" in first) or ("Attendance Type" in first)
    else:
        assert "Attendance Summary" in r.text
        assert 'href="/hr/attendance/report?' in r.text, "the summary links to the Attendance Report"
        for label in ["From Date", "To Date", "Attendance Type", "Shift", "Employee Type", "Designation",
                      "Department", "Employee"]:
            assert label in r.text, label
    if "print_view=1" in qs:
        assert "window.print()" in r.text


def test_summary_by_employee_filter_and_pivot_figures(hr_admin, session):
    today = date.today()
    start = today - timedelta(days=29)
    e = (session.query(Employee).filter(Employee.id == session.query(HRAttendance.employee_id)
                                        .filter(HRAttendance.date >= start).limit(1).scalar()).first())
    assert e is not None
    rows, totals = attendance_pivot(session, start, today, [e])
    assert len(rows) == 1 and rows[0]["employee"].id == e.id
    r = rows[0]
    mine = session.query(HRAttendance).filter(HRAttendance.employee_id == e.id, HRAttendance.date >= start,
                                              HRAttendance.date <= today).all()
    assert r["sessions"] == len(mine)
    assert r["present"] == sum(1 for a in mine if a.status in ("present", "late"))
    assert r["absent"] == sum(1 for a in mine if a.status == "absent")
    assert r["leave"] == sum(1 for a in mine if a.status == "leave")
    assert r["late"] == sum(1 for a in mine if a.status == "late" or (a.late_minutes or 0) > 0)
    assert totals["sessions"] == r["sessions"] and totals["present"] == r["present"]
    body = hr_admin.get(f"/hr/attendance/summary?employee={e.id}").text
    assert e.employee_code in body and e.full_name in body
    csv_text = hr_admin.get(f"/hr/attendance/summary?employee={e.id}&format=csv").text
    line = next(l for l in csv_text.splitlines() if l.startswith(e.employee_code))
    assert line.split(",")[6:11] == [str(r["present"]), str(r["absent"]), str(r["leave"]), str(r["late"]), str(r["sessions"])]


def test_summary_pivot_type_filter_keeps_only_matching_employees(session):
    today = date.today()
    start = today - timedelta(days=29)
    staff = live_employees(session).all()
    rows, _ = attendance_pivot(session, start, today, staff, "absent")
    assert all(r["absent"] > 0 for r in rows)
    rows, _ = attendance_pivot(session, start, today, staff, "late")
    assert all(r["late"] > 0 for r in rows)


def test_report_links_back_to_the_summary(hr_admin):
    # The report page is untouched; the summary carries the "Attendance Report" button their page has.
    assert 'href="/hr/attendance/report?' in hr_admin.get("/hr/attendance/summary").text


# --------------------------------------------------------------------------- Staff Notices
def test_notice_tiles_equal_a_direct_count(hr_admin, session):
    today = date.today()
    body = hr_admin.get("/hr/notices").text
    for state in ["live", "scheduled", "expired", "inactive"]:
        expected = notice_state_filter(session.query(StaffNotice), state, today).count()
        by_python = sum(1 for n in session.query(StaffNotice) if notice_state(n, today) == state)
        assert expected == by_python, state
        assert f'href="/hr/notices?status={state}"' in body
        page = hr_admin.get(f"/hr/notices?status={state}").text
        assert page.count("editNotice") // 2 <= max(expected, 1)  # rows on the page never exceed the tile


def test_notice_create_edit_toggle(hr_admin, session):
    title = f"Staff meeting on Thursday {TAG}"
    r = hr_admin.post("/hr/notices/new", follow_redirects=False, data={
        "title": title, "description": "All morning-shift staff in the main hall at 11.", "link": "https://oqc.local/meet",
        "start_date": date.today().isoformat(), "end_date": (date.today() + timedelta(days=7)).isoformat(),
        "audience": "all", "status": "active"})
    assert r.status_code == 303
    n = session.query(StaffNotice).filter(StaffNotice.title == title).first()
    assert n is not None and n.status == "active" and n.audience == "all" and n.is_live()
    assert notice_state(n) == "live"
    assert title in hr_admin.get("/hr/notices?status=live").text
    assert title in hr_admin.get(f"/hr/notices?q={TAG}").text

    r = hr_admin.post(f"/hr/notices/{n.id}/edit", follow_redirects=False, data={
        "title": title, "description": "Moved to the small hall.", "link": "", "audience": "Admin",
        "start_date": (date.today() + timedelta(days=3)).isoformat(), "end_date": "", "status": "active"})
    assert r.status_code == 303
    session.expire_all()
    n = session.get(StaffNotice, n.id)
    assert n.audience == "Admin" and n.link is None and n.end_date is None and n.description == "Moved to the small hall."
    assert notice_state(n) == "scheduled"
    assert title in hr_admin.get("/hr/notices?status=scheduled&audience=Admin").text

    assert hr_admin.post(f"/hr/notices/{n.id}/toggle", follow_redirects=False).status_code == 303
    session.expire_all()
    n = session.get(StaffNotice, n.id)
    assert n.status == "inactive" and notice_state(n) == "inactive"
    assert hr_admin.post(f"/hr/notices/{n.id}/toggle", follow_redirects=False).status_code == 303
    session.expire_all()
    assert session.get(StaffNotice, n.id).status == "active"


def test_notice_validation(hr_admin, session):
    before = session.query(StaffNotice).count()
    assert hr_admin.post("/hr/notices/new", follow_redirects=False, data={"title": "", "audience": "all"}).status_code == 303
    assert hr_admin.post("/hr/notices/new", follow_redirects=False, data={
        "title": f"Ends before it starts {TAG}", "start_date": "2026-09-20", "end_date": "2026-09-10"}).status_code == 303
    assert session.query(StaffNotice).count() == before, "neither invalid form created a row"
    assert hr_admin.post("/hr/notices/999999/edit", follow_redirects=False, data={"title": "x"}).status_code == 404


def test_live_notice_rings_the_bell_of_its_audience(people_head, session):
    title = f"Academics: monthly test papers due {TAG}"
    teacher = teacher_employee(session)
    audience = teacher.employee_type or "Academics"
    r = people_head.post("/hr/notices/new", follow_redirects=False, data={
        "title": title, "description": "Hand the corrected papers to the coordinator by Friday.",
        "start_date": date.today().isoformat(), "audience": audience, "status": "active"})
    assert r.status_code == 303
    n = session.query(StaffNotice).filter(StaffNotice.title == title).first()
    assert n is not None and n.created_by_id is not None
    recipients = {uid for (uid,) in session.query(Notification.user_id).filter(
        Notification.title == title, Notification.channel == "in_app")}
    expected = {uid for (uid,) in live_employees(session).filter(
        Employee.user_id.isnot(None), Employee.employee_type == audience).with_entities(Employee.user_id)}
    assert recipients == expected and teacher.user_id in recipients
    outside = live_employees(session).filter(Employee.user_id.isnot(None), Employee.employee_type != audience).first()
    if outside is not None:
        assert outside.user_id not in recipients
    bell = session.query(Notification).filter(Notification.title == title, Notification.user_id == teacher.user_id).first()
    assert bell.link == "/hr/me/notices" and bell.is_read is False and bell.status == "delivered"


def test_scheduled_or_inactive_notice_does_not_ring_the_bell(hr_admin, session):
    for suffix, extra in [("scheduled", {"start_date": (date.today() + timedelta(days=5)).isoformat(), "status": "active"}),
                          ("inactive", {"start_date": date.today().isoformat(), "status": "inactive"})]:
        title = f"Quiet notice ({suffix}) {TAG}"
        assert hr_admin.post("/hr/notices/new", follow_redirects=False,
                             data={"title": title, "audience": "all", **extra}).status_code == 303
        assert session.query(StaffNotice).filter(StaffNotice.title == title).count() == 1
        assert session.query(Notification).filter(Notification.title == title).count() == 0


# --------------------------------------------------------------------------- self portal
def test_self_portal_shows_only_live_notices_for_my_audience(hr_admin, a_teacher, session):
    teacher = teacher_employee(session)
    mine = teacher.employee_type
    other = next(a for a in ["Academics", "Admin", "Marketing"] if a != mine)
    today = date.today().isoformat()
    cases = {
        "all_live": (f"For everyone {TAG}", {"audience": "all", "start_date": today, "status": "active"}),
        "mine_live": (f"For {mine} {TAG}", {"audience": mine, "start_date": today, "status": "active"}),
        "other_live": (f"For {other} only {TAG}", {"audience": other, "start_date": today, "status": "active"}),
        "inactive": (f"Switched off {TAG}", {"audience": "all", "start_date": today, "status": "inactive"}),
        "expired": (f"Long gone {TAG}", {"audience": "all", "start_date": (date.today() - timedelta(days=30)).isoformat(),
                                         "end_date": (date.today() - timedelta(days=1)).isoformat(), "status": "active"}),
        "scheduled": (f"Not yet {TAG}", {"audience": "all", "start_date": (date.today() + timedelta(days=2)).isoformat(),
                                         "status": "active"}),
    }
    for title, data in cases.values():
        assert hr_admin.post("/hr/notices/new", follow_redirects=False, data={"title": title, **data}).status_code == 303

    body = a_teacher.get("/hr/me/notices").text
    assert body.count("<title>") == 1
    for key, (title, _) in cases.items():
        if key in ("all_live", "mine_live"):
            assert title in body, key
        else:
            assert title not in body, key
    for col in ["ID", "Title", "Description", "Dated"]:
        assert col in body

    shown = live_notices_for(session, teacher)
    titles = [n.title for n in shown]
    assert cases["all_live"][0] in titles and cases["mine_live"][0] in titles
    assert not any(t in titles for t in [cases[k][0] for k in ("other_live", "inactive", "expired", "scheduled")])
    assert all(n.is_live() and n.audience in ("all", mine) for n in shown)
    assert [n.start_date for n in shown] == sorted((n.start_date for n in shown), reverse=True), "newest first"


def test_self_portal_without_an_employee_record_sees_only_all_staff_notices(session):
    shown = live_notices_for(session, None)
    assert all(n.audience == "all" and n.is_live() for n in shown)


# --------------------------------------------------------------------------- permissions
def test_teacher_cannot_write_notices(a_teacher, session):
    assert a_teacher.get("/hr/me/notices").status_code == 200
    assert a_teacher.post("/hr/notices/new", follow_redirects=False,
                          data={"title": f"Teacher tries to publish {TAG}", "audience": "all"}).status_code == 403
    assert session.query(StaffNotice).filter(StaffNotice.title.ilike(f"%Teacher tries to publish {TAG}%")).count() == 0
    n = session.query(StaffNotice).first()
    assert a_teacher.post(f"/hr/notices/{n.id}/edit", follow_redirects=False, data={"title": n.title}).status_code == 403
    assert a_teacher.post(f"/hr/notices/{n.id}/toggle", follow_redirects=False).status_code == 403
    assert a_teacher.get("/hr/notices").status_code == 403
    assert a_teacher.get("/hr/attendance/summary").status_code == 403


def test_people_head_can_read_and_write(people_head):
    assert people_head.get("/home/hr").status_code == 200
    assert people_head.get("/hr/notices").status_code == 200
    assert people_head.get("/hr/attendance/summary").status_code == 200


@pytest.mark.parametrize("url", ["/hr/notices", "/hr/me/notices", "/hr/attendance/summary",
                                 "/hr/attendance/summary?format=csv"])
def test_parent_is_refused_everywhere(a_parent, url):
    assert a_parent.get(url, follow_redirects=False).status_code == 403


def test_parent_has_no_hr_home(a_parent):
    assert a_parent.get("/home/hr", follow_redirects=False).status_code in (403, 404)


def test_parent_cannot_write_notices(a_parent, session):
    n = session.query(StaffNotice).first()
    assert a_parent.post("/hr/notices/new", follow_redirects=False, data={"title": f"Parent {TAG}"}).status_code == 403
    assert a_parent.post(f"/hr/notices/{n.id}/toggle", follow_redirects=False).status_code == 403
