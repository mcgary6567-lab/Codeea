"""Regression tests for the HR / billing gap build (audit findings 3, 7, 8, 9, 12, 15, 16, 32, 33, 37, 42, 43).

Service-level tests run inside the ``db`` fixture's transaction, which is rolled back, so the suite can run twice
on the seeded development database; route tests only read.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import pytest

from app.config import settings
from app.core import notify as notify_mod
from app.models.core import CommunicationPreference, Notification, Setting, User
from app.models.finance import Invoice, Subscription
from app.models.hr_erp import Holiday
from app.models.people import Client, Employee, HRAttendance, Leave
from app.models.scheduling import ReminderLog
from app.services import accounting, billing, kpi
from app.services import hr as hr_svc
from app.services import jobs_academic, jobs_crm, jobs_finance, jobs_hr


# ----------------------------------------------------------------------------- helpers
def _set_prop(db, key: str, value, group: str = "billing") -> None:
    """Set a Branch Property inside the test transaction (rolled back by the fixture)."""
    s = db.query(Setting).filter(Setting.key == key).first()
    if s is None:
        s = Setting(key=key, group=group, label=key)
        db.add(s)
    s.value = {"value": value}
    db.flush()


def _drop_setting(db, key: str) -> None:
    s = db.query(Setting).filter(Setting.key == key).first()
    if s is not None:
        db.delete(s)
        db.flush()


def _next_weekday(start: date, weekday: int) -> date:
    d = start
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


# ============================================================================ finding 12: employee list masking
def test_employee_list_masks_contacts_without_permission(admin, monkeypatch):
    import app.web.hr as hr_web
    monkeypatch.setattr(hr_web, "can_see_sensitive", lambda user: False)
    r = admin.get("/hr/employees")
    assert r.status_code == 200
    body = r.text
    assert "/reveal/phone" in body and "/reveal/bank_account_no" in body
    # a raw phone of a listed employee is not in the page
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phones = [p for (p,) in db.query(Employee.phone).filter(Employee.phone.isnot(None)).limit(50) if p and len(p) > 6]
    finally:
        db.close()
    assert phones, "seed has employee phones"
    assert not any(p in body for p in phones)


def test_employee_list_shows_contacts_with_permission(admin):
    r = admin.get("/hr/employees")
    assert r.status_code == 200
    assert "/reveal/phone" not in r.text


def test_employee_reveal_is_audited(admin):
    from app.database import SessionLocal
    from app.models.core import AuditEvent
    db = SessionLocal()
    try:
        e = db.query(Employee).filter(Employee.phone.isnot(None)).first()
        before = db.query(AuditEvent).filter(AuditEvent.action == "reveal", AuditEvent.module == "employees").count()
    finally:
        db.close()
    r = admin.get(f"/hr/employees/{e.id}/reveal/phone")
    assert r.status_code == 200 and (e.phone or "-") in r.text
    assert admin.get(f"/hr/employees/{e.id}/reveal/base_salary").status_code == 404
    db = SessionLocal()
    try:
        after = db.query(AuditEvent).filter(AuditEvent.action == "reveal", AuditEvent.module == "employees").count()
        assert after == before + 1
        # purge the audit row so a second run of the suite sees the same baseline
        row = (db.query(AuditEvent).filter(AuditEvent.action == "reveal", AuditEvent.module == "employees")
               .order_by(AuditEvent.id.desc()).first())
        db.delete(row)
        db.commit()
    finally:
        db.close()


# ============================================================================ finding 7: gateway sync gate
def test_gateway_sync_disabled_in_production_without_live_gateway(db, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr("app.services.integrations.has_live_credentials", lambda db_, provider: False)
    assert billing.gateway_sync_allowed(db) is False
    with pytest.raises(ValueError, match="Connect a payment gateway in Configuration"):
        billing.sync_gateway_receipts(db, None, limit=0)


def test_gateway_sync_allowed_in_production_with_live_gateway(db, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr("app.services.integrations.has_live_credentials", lambda db_, provider: provider == "payment")
    assert billing.live_payment_gateway(db) is not None   # seeded "Stripe UK" is active + live
    assert billing.gateway_sync_allowed(db) is True


def test_gateway_sync_allowed_outside_production(db, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    assert billing.gateway_sync_allowed(db) is True
    assert billing.sync_gateway_receipts(db, None, limit=0) == []


# ============================================================================ finding 9: billing / accounts properties
def test_billing_property_defaults_reproduce_previous_behaviour(db):
    for key in (billing.PROP_INVOICE_DUE_DAYS, billing.PROP_LATE_FEE_PCT, billing.PROP_SEND_REMINDERS, billing.PROP_ADVANCE_DAYS,
                accounting.PROP_POSTING_LOCK_DAYS, accounting.PROP_FY_START_MONTH):
        _drop_setting(db, key)
    assert billing.invoice_due_days(db) == billing.INVOICE_TERMS_DAYS == 7
    assert billing.late_fee_pct(db) == 0
    assert billing.send_reminders_enabled(db) is True
    assert billing.advance_invoice_days(db) == 3
    assert accounting.posting_lock_days(db) == 0 and accounting.posting_lock_date(db) is None
    assert accounting.financial_year_start_month(db) == 1
    assert accounting.financial_year_start(db, date(2026, 10, 4)) == date(2026, 1, 1)


def test_billing_properties_are_read(db):
    _set_prop(db, billing.PROP_INVOICE_DUE_DAYS, 12)
    _set_prop(db, billing.PROP_LATE_FEE_PCT, 4)
    _set_prop(db, billing.PROP_SEND_REMINDERS, False)
    _set_prop(db, billing.PROP_ADVANCE_DAYS, 9, group="academics")
    assert billing.invoice_due_days(db) == 12
    assert billing.late_fee_pct(db) == 4
    assert billing.send_reminders_enabled(db) is False
    assert billing.advance_invoice_days(db) == 9


def test_invoice_due_date_uses_due_days(db):
    _set_prop(db, billing.PROP_INVOICE_DUE_DAYS, 11)
    sub = (db.query(Subscription).join(Client, Subscription.client_id == Client.id)
           .filter(Subscription.status == "active", Client.user_id.isnot(None)).order_by(Subscription.id).first())
    assert sub is not None
    start = date(2031, 1, 1)
    inv = billing.create_client_invoice(db, sub.client, [sub], start, date(2031, 1, 31), status="draft",
                                        issue_date=date(2031, 1, 1), apply_rules=False, notify_family=False)
    assert inv.due_date == date(2031, 1, 12)


def test_late_fee_posted_once_per_overdue_invoice(db):
    inv = (db.query(Invoice).filter(Invoice.status == "overdue", Invoice.total > 0)
           .order_by(Invoice.id).first())
    assert inv is not None
    balance = round(float(inv.total) - float(inv.paid_amount), 2)
    assert balance > 0
    assert billing.post_late_fee(db, inv, None, pct=0) is None
    addition = billing.post_late_fee(db, inv, None, pct=2.5)
    assert addition is not None and addition.addition_type == "Late Fee" and addition.effect == "add"
    assert float(addition.amount) == round(balance * 2.5 / 100, 2)
    assert addition.status == "confirmed" and addition.ledger_entry_id
    assert billing.late_fee_posted(db, inv) is True
    assert billing.post_late_fee(db, inv, None, pct=2.5) is None   # second run: nothing new


def test_reminder_job_honours_send_reminders_switch(db):
    _set_prop(db, billing.PROP_SEND_REMINDERS, False)
    _set_prop(db, billing.PROP_LATE_FEE_PCT, 0)
    out = jobs_finance.overdue_and_reminders(db)
    assert out["reminders"] == 0 and out["reminders_enabled"] is False and out["late_fees_posted"] == 0


def test_reminder_job_posts_late_fees_once(db):
    _set_prop(db, billing.PROP_SEND_REMINDERS, False)
    _set_prop(db, billing.PROP_LATE_FEE_PCT, 2.5)
    first = jobs_finance.overdue_and_reminders(db)
    assert first["late_fees_posted"] >= 1
    second = jobs_finance.overdue_and_reminders(db)
    assert second["late_fees_posted"] == 0


def test_posting_lock_refuses_back_dated_entries(db):
    accounts = accounting.ensure_chart_of_accounts(db)
    codes = list(accounts.keys())[:2]
    lines = [(codes[0], 10, 0), (codes[1], 0, 10)]
    _set_prop(db, accounting.PROP_POSTING_LOCK_DAYS, 5, group="accounts")
    with pytest.raises(ValueError, match="posting lock"):
        accounting.post_journal(db, "back-dated", lines, entry_date=date.today() - timedelta(days=10), status="draft")
    je = accounting.post_journal(db, "inside lock", lines, entry_date=date.today() - timedelta(days=2), status="draft")
    assert je.id
    _set_prop(db, accounting.PROP_POSTING_LOCK_DAYS, 0, group="accounts")
    je2 = accounting.post_journal(db, "no lock", lines, entry_date=date.today() - timedelta(days=400), status="draft")
    assert je2.id


def test_financial_year_start_month(db):
    _set_prop(db, accounting.PROP_FY_START_MONTH, 7, group="accounts")
    assert accounting.financial_year_start(db, date(2026, 10, 4)) == date(2026, 7, 1)
    assert accounting.financial_year_start(db, date(2026, 3, 1)) == date(2025, 7, 1)
    assert accounting.financial_year_bounds(db, date(2026, 10, 4)) == (date(2026, 7, 1), date(2027, 6, 30))
    assert accounting.year_to_date_range(db, date(2026, 10, 4)) == (date(2026, 7, 1), date(2026, 10, 4))
    p = kpi.resolve_period("ytd", today=date(2026, 10, 4), fy_start_month=accounting.financial_year_start_month(db))
    assert p.start == date(2026, 7, 1) and p.end == date(2026, 10, 4)
    assert kpi.resolve_period("ytd", today=date(2026, 10, 4)).start == date(2026, 1, 1)   # default unchanged


# ============================================================================ finding 15: holidays in absence marking
def test_mark_absent_skips_holidays(db):
    day = _next_weekday(date.today() + timedelta(days=40), 1)   # a future Tuesday with no attendance rows
    assert db.query(HRAttendance).filter(HRAttendance.date == day).count() == 0
    db.add(Holiday(name="Test holiday (all)", holiday_date=day, shift_group="all", status="active"))
    db.flush()
    assert hr_svc.mark_absent_for_missing(db, day) == 0
    assert db.query(HRAttendance).filter(HRAttendance.date == day).count() == 0


def test_mark_absent_skips_only_the_holiday_shift_group(db):
    day = _next_weekday(date.today() + timedelta(days=47), 1)
    assert db.query(HRAttendance).filter(HRAttendance.date == day).count() == 0
    db.add(Holiday(name="Morning shift holiday", holiday_date=day, shift_group="morning", status="active"))
    db.flush()
    n = hr_svc.mark_absent_for_missing(db, day)
    rows = db.query(HRAttendance).filter(HRAttendance.date == day).all()
    assert n == len(rows)
    shifts = {db.get(Employee, r.employee_id).shift for r in rows}
    assert "morning" not in shifts
    if db.query(Employee).filter(Employee.status.in_(["active", "probation"]), Employee.shift != "morning").count():
        assert rows


# ============================================================================ finding 16: contract-end reminders
def test_contract_end_reminders_fire_once_per_threshold(db):
    today = date.today()
    _drop_setting(db, jobs_hr.CONTRACT_MARKER_KEY)
    e30 = db.query(Employee).filter(Employee.status == "active", Employee.manager_id.isnot(None)).order_by(Employee.id).first()
    e7 = db.query(Employee).filter(Employee.status == "active", Employee.id != e30.id).order_by(Employee.id).first()
    for e in db.query(Employee).filter(Employee.contract_end_date.isnot(None)):
        e.contract_end_date = None
    e30.contract_end_date = today + timedelta(days=30)
    e7.contract_end_date = today + timedelta(days=7)
    db.flush()
    out = jobs_hr.contract_end_reminders(db, today)
    assert out["employees"] == 2 and out["notifications"] >= 2
    assert db.query(ReminderLog).filter(ReminderLog.reminder_type == "contract_end_30", ReminderLog.entity_id == e30.id).count() == 1
    assert db.query(ReminderLog).filter(ReminderLog.reminder_type == "contract_end_7", ReminderLog.entity_id == e7.id).count() == 1
    titles = [n.title for n in db.query(Notification).filter(Notification.event_type == "hr", Notification.title.like("Contract ends%"))]
    assert any(e30.full_name in t for t in titles)
    again = jobs_hr.contract_end_reminders(db, today)
    assert again.get("skipped") and again["notifications"] == 0
    assert db.query(ReminderLog).filter(ReminderLog.reminder_type.like("contract_end_%")).count() == 2


def test_contract_end_reminders_catch_up_a_missed_day(db):
    today = date.today()
    for e in db.query(Employee).filter(Employee.contract_end_date.isnot(None)):
        e.contract_end_date = None
    emp = db.query(Employee).filter(Employee.status == "active").order_by(Employee.id).first()
    emp.contract_end_date = today + timedelta(days=28)   # the 30-day mark was two days ago
    db.flush()
    _set_prop(db, jobs_hr.CONTRACT_MARKER_KEY, (today - timedelta(days=3)).isoformat(), group="jobs")
    out = jobs_hr.contract_end_reminders(db, today)
    assert out["employees"] == 1
    assert db.query(ReminderLog).filter(ReminderLog.reminder_type == "contract_end_30", ReminderLog.entity_id == emp.id).count() == 1
    # a run tomorrow does not repeat it
    out2 = jobs_hr.contract_end_reminders(db, today + timedelta(days=1))
    assert out2["employees"] == 0


# ============================================================================ finding 37: catch-up jobs run twice
def test_leave_reminders_catch_up_and_do_not_repeat(db):
    emp = db.query(Employee).filter(Employee.status == "active", Employee.user_id.isnot(None)).order_by(Employee.id).first()
    start = date.today() - timedelta(days=1)   # the "day before" tick was missed two days ago
    leave = Leave(person_type="employee", employee_id=emp.id, leave_type="casual", start_date=start,
                  end_date=start + timedelta(days=10), status="approved", reason="test")
    db.add(leave)
    db.flush()
    first = jobs_hr.leave_reminders(db)
    assert first["starting"] >= 1 and leave.reminder_sent_start is True
    second = jobs_hr.leave_reminders(db)
    assert second["starting"] == 0


def test_monthly_payroll_draft_runs_once_and_catches_up(db):
    _drop_setting(db, jobs_hr.PAYROLL_MARKER_KEY)
    assert "skipped" in jobs_hr.monthly_draft_payroll(db, date(2026, 10, 9))      # window passed
    first = jobs_hr.monthly_draft_payroll(db, date(2026, 10, 3))                 # a missed 1st, caught up on the 3rd
    assert first["period"] == "2026-09" and ("payslips" in first or "skipped" in first)
    second = jobs_hr.monthly_draft_payroll(db, date(2026, 10, 3))
    assert second["skipped"] == "already drafted this month"


def test_weekly_grades_run_once_per_week(db):
    _drop_setting(db, jobs_hr.GRADES_MARKER_KEY)
    first = jobs_hr.weekly_teacher_grades(db, date(2026, 10, 7))   # a Wednesday: the Monday tick was missed
    assert first["week"] == "2026-10-05" and "skipped" not in first
    second = jobs_hr.weekly_teacher_grades(db, date(2026, 10, 8))
    assert second.get("skipped")
    third = jobs_hr.weekly_teacher_grades(db, date(2026, 10, 12))  # next Monday runs again
    assert "skipped" not in third


def test_monthly_test_generation_runs_once_per_month(db):
    _drop_setting(db, jobs_academic.TESTS_MARKER_KEY)
    assert "skipped" in jobs_academic.monthly_test_generation(db, date(2026, 10, 20))
    first = jobs_academic.monthly_test_generation(db, date(2026, 10, 2))
    assert first["period"] == "2026-10" and "created" in first and "skipped" not in first
    second = jobs_academic.monthly_test_generation(db, date(2026, 10, 2))
    assert second["skipped"] == "already generated this month" and second["created"] == 0


def test_survey_triggers_catch_up_without_duplicates(db):
    first = jobs_crm.survey_triggers(db)
    second = jobs_crm.survey_triggers(db)
    assert first["sent"] >= 0 and second["sent"] == 0


# ============================================================================ finding 33: communication preferences
def test_notify_skips_channels_switched_off(db):
    parent = db.query(User).filter(User.email == "parent1@oqc.local").first()
    client = db.query(Client).filter(Client.user_id == parent.id).first()
    db.add(CommunicationPreference(user_id=parent.id, channel="email", opted_in=False, consent_at=datetime.utcnow()))
    db.add(CommunicationPreference(client_id=client.id, channel="whatsapp", opted_in=False, consent_at=datetime.utcnow()))
    db.flush()
    rows = notify_mod.notify(db, parent, "Preference test", "body", channels=("in_app", "email", "whatsapp"),
                             recipient_address=client.whatsapp or client.email)
    by_channel = {n.channel: n for n in rows}
    assert by_channel["in_app"].status == "delivered"
    assert by_channel["email"].status == "skipped" and by_channel["email"].error == notify_mod.SKIPPED_BY_PREFERENCE
    assert by_channel["whatsapp"].status == "skipped"
    # opting back in sends again
    for p in db.query(CommunicationPreference).filter(CommunicationPreference.user_id == parent.id, CommunicationPreference.channel == "email"):
        p.opted_in = True
    db.flush()
    rows = notify_mod.notify(db, parent, "Preference test 2", "body", channels=("email",), recipient_address=client.email)
    assert rows[0].status == "sent"


# ============================================================================ finding 8: family statement download
def test_portal_statement_csv_and_print(parent):
    page = parent.get("/portal/billing")
    assert page.status_code == 200
    assert "/portal/billing/statement.csv" in page.text and "/portal/billing/statement/print" in page.text
    r = parent.get("/portal/billing/statement.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("Srl,Date,Transaction Type,Description,Amount,Balance,Currency")
    assert "Previous Balance" in lines[1] and any("Total" in ln for ln in lines) and any("In Words" in ln for ln in lines)
    assert 'filename="statement-C-00001.csv"' in r.headers["content-disposition"]
    p = parent.get("/portal/billing/statement/print")
    assert p.status_code == 200 and "Account statement" in p.text and "In Words" in p.text


def test_portal_statement_matches_staff_ledger_report(db):
    parent = db.query(User).filter(User.email == "parent1@oqc.local").first()
    client = db.query(Client).filter(Client.user_id == parent.id).first()
    from app.web.portal_client import _statement_rows
    report = billing.ledger_report(db, client)
    rows = _statement_rows(report)
    assert len(rows) == len(report["rows"]) + 4
    assert rows[-2][5] == report["closing_balance"]


def test_portal_statement_requires_family_login(admin):
    r = admin.get("/portal/billing/statement.csv", follow_redirects=False)
    assert r.status_code in (302, 303, 403)


# ============================================================================ findings 3 and 32: broken links
def test_referred_list_links_resolve(admin):
    r = admin.get("/students/referred")
    assert r.status_code == 200
    assert not re.search(r'href="/requests/references/\d+"', r.text)
    assert admin.get("/requests/references?client=1&q=x").status_code == 200


def test_client_conversations_link_resolves(admin):
    from app.database import SessionLocal
    from app.models.crm import Conversation
    db = SessionLocal()
    try:
        cv = db.query(Conversation).filter(Conversation.client_id.isnot(None)).first()
    finally:
        db.close()
    assert cv is not None
    r = admin.get(f"/clients/{cv.client_id}?tab=conversations")
    assert r.status_code == 200
    assert not re.search(r'href="/crm/inbox/\d+"', r.text)
    assert f"/crm/inbox?c={cv.id}" in r.text
    assert admin.get(f"/crm/inbox?c={cv.id}").status_code == 200


# ============================================================================ findings 42 / 43: portal pickers and links
def test_family_change_request_form_offers_student_picker(parent):
    r = parent.get("/portal/requests")
    assert r.status_code == 200
    assert r.text.count('name="student_id"') >= 2
    # a child from another family is refused and nothing is written
    r = parent.post("/portal/requests", data={"kind": "teacher_change", "subscription_id": "1", "student_id": "999999",
                                              "description": "x"}, follow_redirects=False)
    assert r.status_code == 404


def test_teacher_income_links_to_payslip_print(teacher):
    r = teacher.get("/teacher/income")
    assert r.status_code == 200
    m = re.search(r'/hr/me/payslips/(\d+)/print\?print_view=1', r.text)
    assert m, "every payslip row links to its print view"
    assert teacher.get(m.group(0)).status_code == 200
