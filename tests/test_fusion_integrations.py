"""Fusion gap report, step 3 (21 Oct 2026): the integrations the old ERP had. Biometric attendance machines (push and
file), the automatic currency-rate feed, Zoom meeting links and the recording pull, and the SMS channel.
No network is used: the provider calls are replaced in each test.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.core import Integration
from app.models.finance import Currency, ExchangeRateHistory
from app.models.hr_erp import AttendanceMachine, AttendancePunch
from app.models.people import Employee, HRAttendance
from app.services import attendance_machines as am
from app.services import fx_feed, integrations, zoom

TAG = "fin" + uuid.uuid4().hex[:6]
DAY = date(2001, 2, 5)


def _login(email: str, password: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/login", data={"username": email, "password": password}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture(scope="module")
def admin():
    return _login("admin@oqc.local", "Admin@12345")


@pytest.fixture(scope="module")
def employee():
    s = SessionLocal()
    e = s.query(Employee).filter(Employee.status == "active", Employee.attendance_machine_id.is_(None)).first()
    eid = e.id
    s.close()
    yield eid
    s = SessionLocal()
    try:
        s.query(HRAttendance).filter(HRAttendance.employee_id == eid, HRAttendance.date >= DAY,
                                     HRAttendance.date <= date(2001, 2, 28)).delete(synchronize_session=False)
        s.query(AttendancePunch).filter(AttendancePunch.machine_user_id.like(f"{TAG[:6]}%")).delete(synchronize_session=False)
        s.query(AttendancePunch).filter(AttendancePunch.punched_at < datetime(2001, 3, 1)).delete(synchronize_session=False)
        s.query(AttendanceMachine).filter(AttendanceMachine.serial_no.like(f"{TAG}%")).delete(synchronize_session=False)
        s.get(Employee, eid).attendance_machine_id = None
        s.commit()
    finally:
        s.close()


# ------------------------------------------------------------------------------------------------ attendance machines
def test_a_registered_machine_pushes_punches_that_become_attendance(admin, employee):
    pin = "77" + TAG[-4:]
    serial = f"{TAG}SN1"
    r = admin.post("/hr/attendance-machines/new", data={"name": f"{TAG} gate", "serial_no": serial, "model": "K40"})
    assert "registered" in r.text
    assert "/hr/attendance-machines" in admin.get("/home/hr").text or admin.get("/hr/attendance-machines").status_code == 200

    machine = TestClient(app)
    assert machine.get("/iclock/cdata", params={"SN": "NOT-REGISTERED", "options": "all"}).status_code == 403
    hello = machine.get("/iclock/cdata", params={"SN": serial, "options": "all"})
    assert hello.status_code == 200 and f"GET OPTION FROM: {serial}" in hello.text

    s = SessionLocal()
    s.get(Employee, employee).attendance_machine_id = pin
    s.commit()
    s.close()
    log = f"{pin}\t2001-02-05 09:02:11\t0\t1\n{pin}\t2001-02-05 17:31:40\t1\t1\n999999\t2001-02-05 09:00:00\t0\t1\n"
    up = machine.post("/iclock/cdata", params={"SN": serial, "table": "ATTLOG"}, content=log)
    assert up.status_code == 200 and up.text.startswith("OK")
    again = machine.post("/iclock/cdata", params={"SN": serial, "table": "ATTLOG"}, content=log)
    assert again.status_code == 200

    s = SessionLocal()
    try:
        punches = s.query(AttendancePunch).filter(AttendancePunch.punched_at >= datetime(2001, 2, 5),
                                                  AttendancePunch.punched_at < datetime(2001, 2, 6)).all()
        assert len(punches) == 3, "the repeated upload adds nothing"
        row = s.query(HRAttendance).filter(HRAttendance.employee_id == employee, HRAttendance.date == DAY).one()
        assert row.check_in == datetime(2001, 2, 5, 9, 2, 11) and row.check_out == datetime(2001, 2, 5, 17, 31, 40)
        assert row.ip == "biometric"
        m = s.query(AttendanceMachine).filter(AttendanceMachine.serial_no == serial).one()
        assert m.punches_total == 3 and m.last_seen_at is not None
    finally:
        s.close()
    page = admin.get("/hr/attendance-machines?unmatched=1").text
    assert "999999" in page, "a punch from an unknown user waits to be linked"


def test_an_exported_log_is_uploaded_and_linking_applies_old_punches(admin, employee):
    pin = "88" + TAG[-4:]
    csv = f"AC-No,Name,Time\n{pin},X,2001-02-06 08:55:00\n{pin},X,2001-02-06 16:10:00\n,X,bad\n"
    r = admin.post("/hr/attendance-machines/upload", files={"file": ("log.csv", csv, "text/csv")})
    assert "2 punches read: 2 new" in r.text and "row 4" in r.text
    s = SessionLocal()
    try:
        assert not s.query(HRAttendance).filter(HRAttendance.employee_id == employee,
                                                HRAttendance.date == date(2001, 2, 6)).first()
        s.get(Employee, employee).attendance_machine_id = None
        s.commit()
    finally:
        s.close()
    linked = admin.post("/hr/attendance-machines/link", data={"employee_id": str(employee), "machine_user_id": pin})
    assert "1 attendance day(s) updated" in linked.text
    s = SessionLocal()
    try:
        row = s.query(HRAttendance).filter(HRAttendance.employee_id == employee, HRAttendance.date == date(2001, 2, 6)).one()
        assert row.check_in.hour == 8 and row.check_out.hour == 16
    finally:
        s.close()


def test_a_day_hr_corrected_is_not_overwritten_by_punches(employee):
    s = SessionLocal()
    try:
        s.add(HRAttendance(employee_id=employee, date=date(2001, 2, 7), session="am", status="leave"))
        s.flush()
        s.get(Employee, employee).attendance_machine_id = "99" + TAG[-4:]
        result = am.record(s, None, [("99" + TAG[-4:], datetime(2001, 2, 7, 9, 0), "0")], source="file")
        assert result["added"] == 1 and result["days"] == 0
        row = s.query(HRAttendance).filter(HRAttendance.employee_id == employee, HRAttendance.date == date(2001, 2, 7)).one()
        assert row.status == "leave" and row.check_in is None
    finally:
        s.rollback()
        s.close()


# ------------------------------------------------------------------------------------------------ currency feed
def test_the_feed_sets_rates_from_the_provider_and_skips_manual_ones(monkeypatch):
    s = SessionLocal()
    try:
        base = s.query(Currency).filter(Currency.is_base.is_(True)).one()
        others = s.query(Currency).filter(Currency.is_base.is_(False), Currency.is_active.is_(True)).all()
        manual = [c.code for c in others if c.manual_override]
        auto = [c.code for c in others if not c.manual_override]
        rates = {c.code: 0.005 for c in others}
        monkeypatch.setattr(fx_feed, "fetch", lambda db, b, timeout=15.0: rates)
        result = fx_feed.run(s, None)
        s.flush()
        assert result["ok"] and sorted(result["updated"]) == sorted(auto) and sorted(result["skipped"]) == sorted(manual)
        for code in auto:
            c = s.query(Currency).filter(Currency.code == code).one()
            assert abs(float(c.rate_to_base) - 200.0) < 1e-6, "1 unit = 1 / 0.005 base"
        latest = s.query(ExchangeRateHistory).order_by(ExchangeRateHistory.id.desc()).first()
        assert latest.source == "api"
        assert base.rate_to_base == 1
    finally:
        s.rollback()
        s.close()


def test_a_failed_feed_changes_nothing(monkeypatch):
    def boom(db, b, timeout=15.0):
        raise RuntimeError("offline")
    monkeypatch.setattr(fx_feed, "fetch", boom)
    s = SessionLocal()
    try:
        before = {c.code: float(c.rate_to_base) for c in s.query(Currency)}
        result = fx_feed.run(s, None)
        assert not result["ok"] and "offline" in result["error"]
        assert before == {c.code: float(c.rate_to_base) for c in s.query(Currency)}
        assert integrations.get_integration(s, "fx").last_error == "offline"
    finally:
        s.rollback()
        s.close()


def test_the_auto_rate_list_page_opens(admin):
    html = admin.get("/finance/currency-rates/auto").text
    assert "Auto Currency Rate List" in html and "open.er-api.com" in html
    assert "/finance/currency-rates/auto" in admin.get("/finance/currency-rates").text


# ------------------------------------------------------------------------------------------------ Zoom
def test_zoom_is_off_until_its_credentials_are_saved():
    s = SessionLocal()
    try:
        assert not zoom.configured(s) or all(zoom.credentials(s).values())
    finally:
        s.close()


def test_zoom_meeting_link_and_recording_pull(monkeypatch):
    from app.models.erp import CallRecord
    from app.models.scheduling import ClassSession
    s = SessionLocal()
    calls = []

    def fake_call(db, method, path, **kw):
        calls.append((method, path))
        if method == "POST":
            return {"join_url": f"https://zoom.us/j/{TAG}", "id": 1}
        return {"meetings": [{"uuid": f"{TAG}==", "topic": "Class", "start_time": "2001-02-05T13:00:00Z",
                              "duration": 30, "recording_files": [{"play_url": "https://zoom.us/rec/x"}]}]}
    monkeypatch.setattr(zoom, "configured", lambda db: True)
    monkeypatch.setattr(zoom, "_call", fake_call)
    try:
        cs = s.query(ClassSession).filter(ClassSession.status == "pending").first()
        url = zoom.create_meeting(s, cs)
        assert url == f"https://zoom.us/j/{TAG}" and cs.join_url == url
        assert calls[0][0] == "POST" and calls[0][1].endswith("/meetings")
        first = zoom.pull_recordings(s, None, days=2)
        second = zoom.pull_recordings(s, None, days=2)
        assert first["created"] >= 1 and second["created"] == 0, "a recording is stored once"
        assert s.query(CallRecord).filter(CallRecord.external_id == f"zoom-{TAG}==").count() == 1
    finally:
        s.rollback()
        s.close()


# ------------------------------------------------------------------------------------------------ SMS
def test_sms_is_simulated_without_a_gateway_and_sent_through_one(monkeypatch):
    s = SessionLocal()
    try:
        integ = integrations.get_integration(s, "sms")
        saved = integ.config
        integ.config = {}
        monkeypatch.delenv("SMS_GATEWAY_URL", raising=False)
        assert integrations.send_sms(s, "+923001234567", "hello")["simulated"]
        sent = {}

        class R:
            status_code, text = 200, "OK"

            def raise_for_status(self):
                return None

        def fake_get(url, timeout=15):
            sent["url"] = url
            return R()
        monkeypatch.setattr(integrations.httpx, "get", fake_get)
        monkeypatch.setenv("SMS_GATEWAY_URL", "https://sms.example/send?to={to}&msg={text}&from={sender}")
        integ.config = {"provider": "http", "sender": "OQC"}
        out = integrations.send_sms(s, "+923001234567", "Class at 5 pm")
        assert not out["simulated"] and "to=923001234567" in sent["url"] and "Class%20at%205%20pm" in sent["url"]
        integ.config = saved
    finally:
        s.rollback()
        s.close()
