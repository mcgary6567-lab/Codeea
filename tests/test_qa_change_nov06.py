"""Subscriptions, Class Management and Billing from the 6 Nov 2026 QA change request (phases 2-4).

Pages keep the New ERP tiles and take the old ERP's filters, columns and reports. Tests run against the shared
development database and put back anything they change.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app

TAG = f"qa06-{uuid4().hex[:6]}"


@pytest.fixture(scope="module")
def admin():
    c = TestClient(app)
    assert c.post("/login", data={"username": "admin@oqc.local", "password": "Admin@12345"}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# =========================================================================== P2 Subscription Management
def test_value_report_has_the_erp_columns_and_filters(admin, db):
    from app.models.finance import Subscription
    html = admin.get("/subscriptions/value-report").text
    for col in ("Shift", "Manager", "Reg. Date", "Currency Rate", "Total Amount", "Disc in %", "Net Amount Local",
                "Subscription Cost", "Subscription Profit", "Package", "Status Remarks", "Country", "Next Due Date"):
        assert f">{col}<" in html, col
    for name in ("slot_id", "course_id", "client_id", "student_id", "teacher_id", "language", "status"):
        assert f'name="{name}"' in html, name
    from app.services import scheduling as sched
    s = (db.query(Subscription).filter(Subscription.teacher_id.isnot(None),
                                       Subscription.status.in_(sched.RUNNING_SUBSCRIPTION_STATUSES)).first())
    one = admin.get(f"/subscriptions/value-report?teacher_id={s.teacher_id}").text
    others = db.query(Subscription).filter(Subscription.teacher_id != s.teacher_id).all()
    assert s.subscription_code in one
    assert not any(f">{o.subscription_code}<" in one for o in others), "one filter alone narrows the report"


def test_all_subscriptions_filter_by_reg_date(admin, db):
    from app.models.finance import Subscription
    s = db.query(Subscription).order_by(Subscription.start_date).first()
    d = s.start_date.isoformat()
    inside = admin.get(f"/subscriptions?date_from={d}&date_to={d}").text
    assert s.subscription_code in inside
    later = (s.start_date - timedelta(days=3650)).isoformat()
    assert s.subscription_code not in admin.get(f"/subscriptions?date_from={later}&date_to={later}").text
    assert 'name="date_from"' in inside and 'name="date_to"' in inside


def test_every_filter_bar_gets_a_clear_button():
    js = open("app/static/js/app.js", encoding="utf-8").read()
    css = open("app/static/css/app.css", encoding="utf-8").read()
    assert "form.oqc-filters" in js and "oqc-clear" in js and ".oqc-clear" in css


# =========================================================================== P3 Class Management
def _s(status, d, h=10):
    return SimpleNamespace(status=status, date=d, start_time=time(h, 0))


def test_more_than_three_absences_in_a_row_is_critical():
    from app.services.class_dashboards import attendance_alert
    today = date.today()
    three = [_s("done", today - timedelta(days=5))] + [_s("absent", today - timedelta(days=i)) for i in range(3)]
    four = three + [_s("absent", today - timedelta(days=3))]
    assert attendance_alert(three)["level"] != "critical"
    a = attendance_alert(four)
    assert a["level"] == "critical" and a["reason"] == "4 consecutive absences"
    assert a["badges"][-4:] == ["A", "A", "A", "A"] and a["badges"][0] == "P"


def test_high_and_medium_alerts():
    from app.services.class_dashboards import attendance_alert
    today = date.today()
    mixed = [_s("absent" if i % 2 else "done", today - timedelta(days=i)) for i in range(10)]
    assert attendance_alert(mixed)["level"] == "high"
    leaves = [_s("done", today - timedelta(days=9))] + [_s("leave", today - timedelta(days=i)) for i in range(3)]
    assert attendance_alert(leaves) == {**attendance_alert(leaves), "level": "medium", "reason": "3 consecutive leaves"}
    assert attendance_alert([_s("done", today)])["level"] is None


def test_class_status_summary_is_employee_wise_over_a_range(admin, db):
    from app.models.scheduling import ClassSession
    from app.services import classes as class_svc
    last = db.query(ClassSession).order_by(ClassSession.date.desc()).first()
    df, dt = last.date - timedelta(days=30), last.date
    out = class_svc.status_summary(db, df, dt)
    n = db.query(ClassSession).filter(ClassSession.date >= df, ClassSession.date <= dt).count()
    assert sum(r["total"] for r in out["rows"]) == n == out["counts"]["total"]
    for r in out["rows"]:
        assert r["duration15"] + r["duration20"] + r["duration25"] + r["duration35"] <= r["done"]
    html = admin.get(f"/classes/status-summary?date_from={df}&date_to={dt}").text
    for col in ("Emp Name", "Absent Students", "On Leave Students", "No Activity", "Late Available", "Duration 15", "Duration 35"):
        assert col in html, col
    assert 'name="date_from"' in html and 'name="date_to"' in html
    assert admin.get(f"/classes/status-summary?date={dt}").status_code == 200, "the old one-day link still opens"


def test_class_schedules_show_the_erp_columns_and_open_the_student_report(admin, db):
    from app.models.scheduling import ClassSession
    c = db.query(ClassSession).order_by(ClassSession.date.desc()).first()
    html = admin.get(f"/classes?date_from={c.date}&date_to={c.date}").text
    for col in ("Shift", "Work Date", "Available At", "Class Started Time", "Class Ended Time", "Late Minutes"):
        assert f">{col}<" in html, col
    assert f"/classes/student-report/{c.student_id}?date_from={c.date}" in html
    report = admin.get(f"/classes/student-report/{c.student_id}?date_from={c.date - timedelta(days=30)}&date_to={c.date}")
    assert report.status_code == 200
    for part in ("Student Progress Report", "Attendance Summary", "Last 30 Days Attendance", "Subscriptions", "Evaluations", "Class History"):
        assert part in report.text, part


def test_a_flagged_student_is_resolved_with_remarks_and_reopened(admin, db):
    from app.models.people import Student
    from app.models.scheduling import StudentAttendanceAlert
    st = db.query(Student).order_by(Student.id).first()
    url = f"/classes/attendance-monitoring/{st.id}/alert"
    try:
        r = admin.post(url, data={"action": "resolve", "remarks": ""}, follow_redirects=False)
        db.expire_all()
        note = db.query(StudentAttendanceAlert).filter_by(student_id=st.id).first()
        assert note is None or note.status != "resolved", "remarks are required to resolve"
        admin.post(url, data={"action": "resolve", "remarks": f"{TAG} parent called"}, follow_redirects=False)
        db.expire_all()
        note = db.query(StudentAttendanceAlert).filter_by(student_id=st.id).one()
        assert note.status == "resolved" and note.resolved_at and TAG in note.remarks
        admin.post(url, data={"action": "reopen"}, follow_redirects=False)
        db.expire_all()
        assert db.query(StudentAttendanceAlert).filter_by(student_id=st.id).one().status == "open"
        page = admin.get("/classes/attendance-monitoring?tab=resolved").text
        for part in ("Critical", "High", "Medium", "Alert Level", "Attendance Badges", "Management Remarks", "Resolved"):
            assert part in page, part
    finally:
        db.query(StudentAttendanceAlert).filter_by(student_id=st.id).delete()
        db.commit()


def test_day_shift_generates_from_midnight_and_night_shift_from_noon(db, monkeypatch):
    from app.services import classes as class_svc
    from app.services import jobs_classes
    from app.services import scheduling as sched_svc
    calls = []
    monkeypatch.setattr(class_svc, "generate_all_sessions", lambda db, days, shift=None: calls.append(shift) or 0)
    keys = ["job_generate_sessions_morning_last_run", "job_generate_sessions_night_last_run"]
    saved = {k: sched_svc.setting_value(db, k, None, field=None) for k in keys}
    try:
        for k in keys:
            sched_svc.set_setting(db, k, {"date": "2000-01-01"}, group="jobs")
        day = date.today()
        jobs_classes.generate_sessions_job(db, now=datetime.combine(day, time(2, 0)))   # 07:00 in Pakistan
        assert calls == ["morning"]
        jobs_classes.generate_sessions_job(db, now=datetime.combine(day, time(4, 0)))   # 09:00: morning already ran
        assert calls == ["morning"]
        jobs_classes.generate_sessions_job(db, now=datetime.combine(day, time(8, 0)))   # 13:00: the night shift's turn
        assert calls == ["morning", "night"]
    finally:
        for k in keys:
            if saved[k] is not None:
                sched_svc.set_setting(db, k, saved[k], group="jobs")
        db.commit()


def test_schedule_summary_legend_has_five_distinct_colours(admin):
    html = admin.get("/classes/schedule-summary").text
    for kind in ("Regular", "Trial", "Group Class", "Freeze", "Free Session"):
        assert kind in html, kind
    assert "bg-teal-100" in html or "No teachers match" in html, "free sessions are teal as in the ERP"
    assert "Free/Total" in html


# =========================================================================== P4 Billing Management
def test_invoice_form_carries_the_erp_amount_fields(admin):
    html = admin.get("/finance/invoices").text
    for label in ("Currency Rate", "Total Amount", "Discount Amount", "Tax Amount", "Net Amount", "Description"):
        assert label in html, label
    assert 'x-model="picked"' in html


def test_receipts_tiles_and_form(admin):
    html = admin.get("/finance/receipts").text
    for tile in ("Confirmed", "Over All Pending", "Cancelled", "Failed", "Refunded", "Unreconciled",
                 "Received This Month (", "Total Received This Month"):
        assert tile in html, tile
    form = admin.get("/finance/receipts/new").text
    assert 'name="currency_rate"' in form and 'name="lc_amount"' in form and 'name="invoice_id"' in form


def test_client_ledger_has_account_dates_and_the_printable_report(admin, db):
    from app.models.people import Client
    page = admin.get("/finance/ledger").text
    assert 'name="client_id"' in page and 'name="date_from"' in page and "Families owing" in page
    c = db.query(Client).order_by(Client.id).first()
    report = admin.get(f"/finance/ledger?client_id={c.id}&date_from=2026-01-01&date_to={date.today()}").text
    for part in ("Ledger Report", "Previous Balance", "Transaction Type", "In Words", "window.print()"):
        assert part in report, part
    assert "Only Only" not in report
