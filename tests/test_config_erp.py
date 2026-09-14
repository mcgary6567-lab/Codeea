"""Configuration area: every page renders (200), every mutation redirects (303) and the effect is asserted.

Self-contained: defaults DATABASE_URL to the private database data/oqc_cfg.db (seed it first with
    $env:DATABASE_URL='sqlite:///./data/oqc_cfg.db'; .venv/Scripts/python.exe seed.py --reset
) so the shared development database is never locked. Because tests/conftest.py imports the app before this
module, set the variable in the shell when running under pytest.

Re-runnable: everything this module creates is removed first, and the assertions that would drift (rates,
throttles, two-factor flags) restore the value they found. ASCII output only (Windows console is cp1252).

Run:  $env:DATABASE_URL='sqlite:///./data/oqc_cfg.db'; .venv/Scripts/python.exe -m pytest tests/test_config_erp.py -q
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_cfg.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.config_erp import (Lookup, LookupValue, OtpConfiguration, PaymentGateway, SupportTicket,  # noqa: E402
                                   WhatsAppSender)
from app.models.core import AuditEvent, Role, Setting, User  # noqa: E402
from app.models.finance import Currency, ExchangeRateHistory  # noqa: E402
from app.services import lookups as lookup_service  # noqa: E402
from app.web.company_config import next_send_at, pick_sender, setting_value  # noqa: E402

BASE = "/config"

TEST_LOOKUP_CODE = "test_cfg_lookup"
TEST_GATEWAY = "Test Gateway (pytest)"
TEST_SENDER_NUMBER = "+92 300 9999001"
TEST_TICKET_SUBJECT = "Pytest ticket: confirm the configuration area"

GET_PAGES = [
    "", "/lookups", "/lookups?app=Human+Resource", "/lookups?status=active", "/lookups?q=designation",
    "/branch-properties", "/branch-properties?tab=general", "/branch-properties?tab=hr",
    "/branch-properties?tab=academics", "/branch-properties?tab=accounts", "/branch-properties?tab=billing",
    "/branch-properties?tab=hr&q=attendance",
    "/currency-rates", "/payment-gateways", "/payment-gateways?status=active",
    "/payment-gateways?company=Stripe&sample=250",
    "/whatsapp-senders", "/whatsapp-senders?api_status=connected", "/whatsapp-senders?purpose=academics",
    "/support-tickets", "/support-tickets?status=pending", "/support-tickets?priority=urgent",
    "/support-tickets?module=Human+Resource", "/support-tickets?ticket_type=Bug", "/support-tickets?q=result",
    "/otp", "/otp?tab=setup", "/otp?tab=users", "/otp?tab=users&token=enabled", "/otp?tab=users&token=disabled",
    "/roles", "/roles?app=Human+Resource", "/roles?q=officer",
]


# --------------------------------------------------------------------------- fixtures
def _purge(db) -> None:
    """Remove everything this module creates so the absolute assertions below can be re-run."""
    for lv in db.query(LookupValue).join(Lookup).filter(Lookup.code == TEST_LOOKUP_CODE).all():
        db.delete(lv)
    for lookup in db.query(Lookup).filter(Lookup.code == TEST_LOOKUP_CODE).all():
        db.delete(lookup)
    for g in db.query(PaymentGateway).filter(PaymentGateway.name == TEST_GATEWAY).all():
        db.delete(g)
    for s in db.query(WhatsAppSender).filter(WhatsAppSender.number == TEST_SENDER_NUMBER).all():
        db.delete(s)
    for t in db.query(SupportTicket).filter(SupportTicket.subject == TEST_TICKET_SUBJECT).all():
        db.delete(t)
    # a value added to the seeded designation list by an earlier run
    designations = db.query(Lookup).filter(Lookup.code == "hr_designation").first()
    if designations:
        for lv in db.query(LookupValue).filter(LookupValue.lookup_id == designations.id,
                                               LookupValue.value == "Pytest Designation").all():
            db.delete(lv)
    db.commit()
    lookup_service.invalidate()


@pytest.fixture(scope="module", autouse=True)
def _schema():
    init_db()
    db = SessionLocal()
    try:
        _purge(db)
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        _purge(db)
    finally:
        db.close()


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _login(username: str, password: str) -> TestClient:
    c = TestClient(app)
    c.__enter__()
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}: {r.status_code}"
    return c


@pytest.fixture(scope="module")
def admin():
    c = _login("admin@oqc.local", "Admin@12345")
    yield c
    c.__exit__(None, None, None)


@pytest.fixture(scope="module")
def billing():
    c = _login("billing@oqc.local", "Billing@123")
    yield c
    c.__exit__(None, None, None)


def _post(client: TestClient, url: str, data: dict | None = None) -> None:
    r = client.post(url, data=data or {}, follow_redirects=False)
    assert r.status_code == 303, f"POST {url} -> {r.status_code}\n{r.text[:600]}"


# =========================================================================== seed expectations
def test_seed_loaded_the_value_lists(db):
    codes = {c for (c,) in db.query(Lookup.code).all()}
    for expected in ["hr_designation", "hr_leaving_reason", "emp_document_type", "hr_allowance_type",
                     "hr_complaint_type", "hr_employee_request_type", "hr_how_came_to_us", "acc_financial_year",
                     "class_query_type", "reference_type", "family_complaint_type", "payment_mode", "language"]:
        assert expected in codes, f"lookup '{expected}' was not seeded"
    designations = db.query(Lookup).filter(Lookup.code == "hr_designation").first()
    assert designations.is_system and designations.app == "Human Resource"
    seeded = db.query(LookupValue).filter(LookupValue.lookup_id == designations.id,
                                          LookupValue.value != "Pytest Designation").count()
    assert seeded == 19, f"expected the 19 designations from the audit, found {seeded}"


def test_seed_loaded_branch_properties_gateways_senders_and_tickets(db):
    for key in ["secret_pin_code", "attendance_login_time_relaxation", "attendance_logout_time_relaxation",
                "default_ctc_value", "advance_invoice_generation_days", "online_registration_form_instructions",
                "online_registration_form_terms", "erp_report_header_image"]:
        s = db.query(Setting).filter(Setting.key == key).first()
        assert s is not None, f"branch property '{key}' was not seeded"
        assert s.label, f"branch property '{key}' has no label"
    assert db.query(Setting).filter(Setting.key == "secret_pin_code").first().is_secret
    assert db.query(Setting).filter(Setting.key == "attendance_login_time_relaxation").first().unit == "minutes"
    assert db.query(PaymentGateway).count() >= 2
    stripe = db.query(PaymentGateway).filter(PaymentGateway.gateway_company == "Stripe").first()
    assert stripe and abs(stripe.default_transaction_fee_pct - 2.99) < 1e-6
    assert db.query(WhatsAppSender).count() >= 2
    assert db.query(WhatsAppSender).filter(WhatsAppSender.api_status == "connected").count() >= 1
    assert db.query(WhatsAppSender).filter(WhatsAppSender.api_status == "disconnected").count() >= 1
    assert db.query(OtpConfiguration).count() == 1
    assert db.query(SupportTicket).count() >= 12
    statuses = {s for (s,) in db.query(SupportTicket.status).distinct().all()}
    assert len(statuses) >= 4, f"tickets should span the statuses, found {statuses}"


# =========================================================================== GET pages
@pytest.mark.parametrize("path", GET_PAGES)
def test_every_page_renders(admin, path):
    r = admin.get(BASE + path)
    assert r.status_code == 200, f"GET {BASE}{path} -> {r.status_code}\n{r.text[:600]}"


def test_lookup_detail_and_currency_history_render(admin, db):
    lookup = db.query(Lookup).filter(Lookup.code == "hr_designation").first()
    assert admin.get(f"{BASE}/lookups/{lookup.id}").status_code == 200
    assert admin.get(f"{BASE}/lookups/{lookup.id}?status=active&q=teacher").status_code == 200
    currency = db.query(Currency).first()
    assert admin.get(f"{BASE}/currency-rates/{currency.code}").status_code == 200
    ticket = db.query(SupportTicket).order_by(SupportTicket.id).first()
    assert admin.get(f"{BASE}/support-tickets/{ticket.id}").status_code == 200


def test_index_lists_every_configuration_card(admin):
    body = admin.get(BASE).text
    for fragment in ["/config/lookups", "/config/branch-properties", "/config/currency-rates",
                     "/config/payment-gateways", "/config/whatsapp-senders", "/config/support-tickets",
                     "/config/otp", "/config/roles"]:
        assert fragment in body, f"the Configuration index does not link to {fragment}"


# =========================================================================== lookups
def test_create_edit_toggle_a_lookup_and_its_values(admin, db):
    _post(admin, f"{BASE}/lookups/new", {"code": TEST_LOOKUP_CODE, "description": "Pytest Value List",
                                         "app": "Configuration", "status": "active", "sort_no": "99"})
    lookup = db.query(Lookup).filter(Lookup.code == TEST_LOOKUP_CODE).first()
    assert lookup and lookup.description == "Pytest Value List" and not lookup.is_system

    _post(admin, f"{BASE}/lookups/{lookup.id}/values/new",
          {"value": "alpha", "label": "Alpha", "amount": "250.50", "sort_no": "1", "status": "active"})
    _post(admin, f"{BASE}/lookups/{lookup.id}/values/new",
          {"value": "beta", "label": "Beta", "sort_no": "2", "status": "active"})
    db.expire_all()
    assert db.query(LookupValue).filter(LookupValue.lookup_id == lookup.id).count() == 2

    # the service reads it, with the amount and in sort order
    assert [o.value for o in lookup_service.values(db, TEST_LOOKUP_CODE)] == ["alpha", "beta"]
    assert lookup_service.amount_for(db, TEST_LOOKUP_CODE, "alpha") == 250.5
    assert lookup_service.labels(db, TEST_LOOKUP_CODE) == [("alpha", "Alpha"), ("beta", "Beta")]

    # reorder: beta moves above alpha
    beta = db.query(LookupValue).filter(LookupValue.lookup_id == lookup.id, LookupValue.value == "beta").first()
    _post(admin, f"{BASE}/lookups/{lookup.id}/values/{beta.id}/move", {"direction": "up"})
    db.expire_all()
    assert [o.value for o in lookup_service.values(db, TEST_LOOKUP_CODE)] == ["beta", "alpha"]

    # toggling a value takes it out of what the application reads
    _post(admin, f"{BASE}/lookups/{lookup.id}/values/{beta.id}/toggle")
    db.expire_all()
    assert [o.value for o in lookup_service.values(db, TEST_LOOKUP_CODE)] == ["alpha"]

    # editing a value is reflected straight away (the cache is invalidated on save)
    alpha = db.query(LookupValue).filter(LookupValue.lookup_id == lookup.id, LookupValue.value == "alpha").first()
    _post(admin, f"{BASE}/lookups/{lookup.id}/values/{alpha.id}/edit",
          {"value": "alpha", "label": "Alpha Renamed", "amount": "300", "sort_no": "1", "status": "active"})
    db.expire_all()
    assert lookup_service.labels(db, TEST_LOOKUP_CODE) == [("alpha", "Alpha Renamed")]
    assert lookup_service.amount_for(db, TEST_LOOKUP_CODE, "alpha") == 300.0

    # editing and toggling the lookup itself
    _post(admin, f"{BASE}/lookups/{lookup.id}/edit",
          {"code": TEST_LOOKUP_CODE, "description": "Pytest Value List (edited)", "app": "Accounts",
           "status": "active", "sort_no": "98"})
    db.expire_all()
    lookup = db.query(Lookup).filter(Lookup.code == TEST_LOOKUP_CODE).first()
    assert lookup.description == "Pytest Value List (edited)" and lookup.app == "Accounts"
    _post(admin, f"{BASE}/lookups/{lookup.id}/toggle")
    db.expire_all()
    assert db.get(Lookup, lookup.id).status == "inactive"
    assert lookup_service.values(db, TEST_LOOKUP_CODE) == []          # inactive lookups read as empty
    _post(admin, f"{BASE}/lookups/{lookup.id}/toggle")


def test_a_missing_lookup_never_breaks_a_caller(db):
    assert lookup_service.values(db, "no_such_lookup_at_all") == []
    fallback = lookup_service.labels(db, "no_such_lookup_at_all", fallback=["Morning", "Night"])
    assert fallback == [("Morning", "Morning"), ("Night", "Night")]
    pairs = lookup_service.values(db, "no_such_lookup_at_all", fallback=[("m", "Morning")])
    assert pairs[0].value == "m" and pairs[0].label == "Morning"
    assert lookup_service.amount_for(db, "no_such_lookup_at_all", "x", default=42) == 42


def test_a_system_lookup_keeps_its_code_and_cannot_be_deleted(admin, db):
    lookup = db.query(Lookup).filter(Lookup.code == "hr_designation").first()
    _post(admin, f"{BASE}/lookups/{lookup.id}/edit",
          {"code": "renamed_designations", "description": lookup.description, "app": lookup.app,
           "status": "active", "sort_no": str(lookup.sort_no)})
    db.expire_all()
    assert db.get(Lookup, lookup.id).code == "hr_designation", "a system lookup's code must not change"
    _post(admin, f"{BASE}/lookups/{lookup.id}/delete", {"rationale": "pytest"})
    db.expire_all()
    assert db.get(Lookup, lookup.id) is not None, "a system lookup must not be deleted"


def test_adding_a_designation_reaches_the_service(admin, db):
    lookup = db.query(Lookup).filter(Lookup.code == "hr_designation").first()
    _post(admin, f"{BASE}/lookups/{lookup.id}/values/new",
          {"value": "Pytest Designation", "label": "Pytest Designation", "sort_no": "99", "status": "active"})
    db.expire_all()
    assert "Pytest Designation" in lookup_service.option_values(db, "hr_designation")
    assert lookup_service.is_valid(db, "hr_designation", "Teacher Remote")


# =========================================================================== branch properties
def test_saving_a_branch_property_changes_it(admin, db):
    s = db.query(Setting).filter(Setting.key == "attendance_login_time_relaxation").first()
    original = setting_value(s)
    new_value = 25 if original != 25 else 15
    _post(admin, f"{BASE}/branch-properties/{s.id}/save",
          {"value": str(new_value), "tab": "hr", "rationale": "pytest"})
    db.expire_all()
    assert setting_value(db.get(Setting, s.id)) == new_value
    # restore, so the suite can be run again against the same database
    _post(admin, f"{BASE}/branch-properties/{s.id}/save",
          {"value": str(original), "tab": "hr", "rationale": "pytest restore"})
    db.expire_all()
    assert setting_value(db.get(Setting, s.id)) == original


def test_a_non_editable_branch_property_is_refused(admin, db):
    s = db.query(Setting).filter(Setting.is_editable.is_(False)).first()
    assert s is not None, "the seed should include at least one read-only branch property"
    before = setting_value(s)
    r = admin.post(f"{BASE}/branch-properties/{s.id}/save",
                   data={"value": "tampered", "tab": "general"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert setting_value(db.get(Setting, s.id)) == before, "a read-only property must not change"


def test_a_secret_branch_property_is_masked_and_revealing_it_is_audited(admin, db):
    s = db.query(Setting).filter(Setting.key == "secret_pin_code").first()
    body = admin.get(f"{BASE}/branch-properties?tab=general").text
    assert str(setting_value(s)) not in body, "a secret property must not render in the clear"
    assert "Reveal" in body
    before = db.query(AuditEvent).filter(AuditEvent.module == "configuration",
                                         AuditEvent.action == "view").count()
    _post(admin, f"{BASE}/branch-properties/{s.id}/reveal", {"tab": "general"})
    db.expire_all()
    after = db.query(AuditEvent).filter(AuditEvent.module == "configuration",
                                        AuditEvent.action == "view").count()
    assert after == before + 1, "revealing a secret must be written to the audit log"
    revealed = admin.get(f"{BASE}/branch-properties?tab=general&reveal={s.id}").text
    assert str(setting_value(s)) in revealed


def test_a_non_admin_cannot_reach_or_change_a_setting(billing, db):
    assert billing.get(f"{BASE}/branch-properties").status_code == 403
    s = db.query(Setting).filter(Setting.key == "default_ctc_value").first()
    before = setting_value(s)
    r = billing.post(f"{BASE}/branch-properties/{s.id}/save", data={"value": "1", "tab": "hr"},
                     follow_redirects=False)
    assert r.status_code == 403, f"billing should not be able to save a setting (got {r.status_code})"
    db.expire_all()
    assert setting_value(db.get(Setting, s.id)) == before


# =========================================================================== currency rates
def test_adding_a_manual_rate_writes_history_and_updates_the_currency(admin, db):
    currency = db.query(Currency).filter(Currency.is_base.is_(False), Currency.manual_override.is_(False)).first()
    assert currency is not None
    original = float(currency.rate_to_base)
    history_before = db.query(ExchangeRateHistory).filter(
        ExchangeRateHistory.currency_code == currency.code).count()
    new_rate = round(original * 1.05, 6)
    _post(admin, f"{BASE}/currency-rates/manual",
          {"code": currency.code, "rate_to_base": str(new_rate), "effective_at": date.today().isoformat(),
           "note": "pytest manual rate"})
    db.expire_all()
    assert abs(float(db.get(Currency, currency.id).rate_to_base) - new_rate) < 1e-6
    assert db.query(ExchangeRateHistory).filter(
        ExchangeRateHistory.currency_code == currency.code).count() == history_before + 1
    latest = (db.query(ExchangeRateHistory).filter(ExchangeRateHistory.currency_code == currency.code)
              .order_by(ExchangeRateHistory.id.desc()).first())
    assert latest.source == "manual"
    # put the rate back so the suite is re-runnable
    _post(admin, f"{BASE}/currency-rates/manual",
          {"code": currency.code, "rate_to_base": str(original), "effective_at": date.today().isoformat(),
           "note": "pytest restore"})
    db.expire_all()
    assert abs(float(db.get(Currency, currency.id).rate_to_base) - original) < 1e-6


def test_a_bad_manual_rate_is_rejected(admin, db):
    currency = db.query(Currency).filter(Currency.is_base.is_(False)).first()
    before = float(currency.rate_to_base)
    _post(admin, f"{BASE}/currency-rates/manual",
          {"code": currency.code, "rate_to_base": "0", "effective_at": date.today().isoformat()})
    _post(admin, f"{BASE}/currency-rates/manual",
          {"code": "ZZZ", "rate_to_base": "1.5", "effective_at": date.today().isoformat()})
    db.expire_all()
    assert abs(float(db.get(Currency, currency.id).rate_to_base) - before) < 1e-6


def test_the_simulated_feed_updates_every_non_base_currency(admin, db):
    codes = [c.code for c in db.query(Currency).filter(Currency.is_base.is_(False),
                                                       Currency.manual_override.is_(False)).all()]
    before = {c: db.query(ExchangeRateHistory).filter(ExchangeRateHistory.currency_code == c).count() for c in codes}
    _post(admin, f"{BASE}/currency-rates/refresh")
    db.expire_all()
    for code in codes:
        after = db.query(ExchangeRateHistory).filter(ExchangeRateHistory.currency_code == code).count()
        assert after == before[code] + 1, f"the feed did not record history for {code}"
    latest = db.query(ExchangeRateHistory).order_by(ExchangeRateHistory.id.desc()).first()
    assert latest.source == "simulated_feed"
    base = db.query(Currency).filter(Currency.is_base.is_(True)).first()
    assert abs(float(base.rate_to_base) - 1) < 1e-9, "the base currency must stay at 1"


# =========================================================================== payment gateways
def test_create_edit_and_toggle_a_payment_gateway(admin, db):
    _post(admin, f"{BASE}/payment-gateways/new",
          {"name": TEST_GATEWAY, "gateway_company": "Wise", "default_transaction_fee_pct": "1.5",
           "fixed_fee": "0.25", "currency": "GBP", "status": "active"})
    g = db.query(PaymentGateway).filter(PaymentGateway.name == TEST_GATEWAY).first()
    assert g is not None
    assert abs(g.fee_on(100) - 1.75) < 1e-6, "fee_on() should be 1.5% of 100 plus the 0.25 fixed fee"

    _post(admin, f"{BASE}/payment-gateways/{g.id}/edit",
          {"name": TEST_GATEWAY, "gateway_company": "Wise", "default_transaction_fee_pct": "2",
           "fixed_fee": "0.50", "currency": "USD", "status": "active", "live_mode": "1"})
    db.expire_all()
    g = db.get(PaymentGateway, g.id)
    assert g.live_mode and g.currency == "USD" and abs(g.fee_on(100) - 2.5) < 1e-6

    _post(admin, f"{BASE}/payment-gateways/{g.id}/toggle")
    db.expire_all()
    assert db.get(PaymentGateway, g.id).status == "inactive"


def test_the_gateway_list_shows_the_fee_on_a_sample_amount(admin):
    body = admin.get(f"{BASE}/payment-gateways?sample=1000").text
    assert "Fee On 1000.00" in body


# =========================================================================== WhatsApp senders
def test_connecting_and_disconnecting_a_sender(admin, db):
    _post(admin, f"{BASE}/whatsapp-senders/new",
          {"description": "Pytest Sender", "number": TEST_SENDER_NUMBER, "purpose": "marketing",
           "interval_seconds": "60", "messages_per_cycle": "2", "status": "active", "live_mode": "1"})
    s = db.query(WhatsAppSender).filter(WhatsAppSender.number == TEST_SENDER_NUMBER).first()
    assert s is not None and s.api_status == "disconnected" and not s.qr_token

    _post(admin, f"{BASE}/whatsapp-senders/{s.id}/connect")
    db.expire_all()
    s = db.get(WhatsAppSender, s.id)
    assert s.api_status == "connected" and s.qr_token and s.qr_refreshed_at

    _post(admin, f"{BASE}/whatsapp-senders/{s.id}/disconnect")
    db.expire_all()
    s = db.get(WhatsAppSender, s.id)
    assert s.api_status == "disconnected" and s.qr_token is None

    _post(admin, f"{BASE}/whatsapp-senders/{s.id}/edit",
          {"description": "Pytest Sender", "number": TEST_SENDER_NUMBER, "purpose": "marketing",
           "interval_seconds": "120", "messages_per_cycle": "4", "status": "active"})
    db.expire_all()
    assert db.get(WhatsAppSender, s.id).interval_seconds == 120
    _post(admin, f"{BASE}/whatsapp-senders/{s.id}/toggle")
    db.expire_all()
    assert db.get(WhatsAppSender, s.id).status == "inactive"


def test_next_send_at_spreads_messages_across_the_interval(db):
    s = WhatsAppSender(description="x", number="x", interval_seconds=60, messages_per_cycle=1)
    s.last_message_sent_at = None
    assert next_send_at(s) <= datetime.utcnow() + timedelta(seconds=1), "a sender that never sent is ready now"
    sent = datetime(2026, 1, 1, 12, 0, 0)
    s.last_message_sent_at = sent
    assert next_send_at(s) == sent + timedelta(seconds=60)
    s.messages_per_cycle = 4
    assert next_send_at(s) == sent + timedelta(seconds=15), "4 messages per 60s cycle is one every 15s"


def test_pick_sender_respects_the_throttle(admin, db):
    _post(admin, f"{BASE}/whatsapp-senders/new",
          {"description": "Pytest Sender", "number": TEST_SENDER_NUMBER, "purpose": "marketing",
           "interval_seconds": "3600", "messages_per_cycle": "1", "status": "active", "live_mode": "1"})
    s = db.query(WhatsAppSender).filter(WhatsAppSender.number == TEST_SENDER_NUMBER).first()
    _post(admin, f"{BASE}/whatsapp-senders/{s.id}/connect")
    db.expire_all()
    s = db.get(WhatsAppSender, s.id)
    s.status, s.purpose, s.interval_seconds, s.messages_per_cycle = "active", "marketing", 3600, 1
    db.commit()

    now = datetime.utcnow()
    s.last_message_sent_at = now                      # just sent: inside its own hour-long window
    db.commit()
    picked = pick_sender(db, "marketing", now)
    assert picked is None or picked.id != s.id, "a throttled sender must not be picked"

    s.last_message_sent_at = now - timedelta(hours=2)  # the interval has elapsed
    db.commit()
    picked = pick_sender(db, "marketing", now)
    assert picked is not None and picked.id == s.id, "the sender whose interval has elapsed should be picked"

    s.status = "inactive"                              # an inactive sender is never picked
    db.commit()
    picked = pick_sender(db, "marketing", now)
    assert picked is None or picked.id != s.id


def test_pick_sender_falls_back_to_a_general_sender(db):
    general = db.query(WhatsAppSender).filter(WhatsAppSender.purpose == "general",
                                              WhatsAppSender.api_status == "connected",
                                              WhatsAppSender.status == "active").first()
    if general is None:
        pytest.skip("no connected general sender is configured")
    was = general.last_message_sent_at
    general.last_message_sent_at = datetime.utcnow() - timedelta(days=1)
    db.commit()
    picked = pick_sender(db, "a_purpose_nobody_serves")
    assert picked is not None and picked.purpose == "general"
    general.last_message_sent_at = was
    db.commit()


# =========================================================================== support tickets
def test_any_member_of_staff_can_raise_a_ticket(billing, db):
    r = billing.post(f"{BASE}/support-tickets/new",
                     data={"subject": TEST_TICKET_SUBJECT, "message": "Raised by the billing representative.",
                           "ticket_type": "Question", "module": "Billing Management", "priority": "normal"},
                     follow_redirects=False)
    assert r.status_code == 303, f"a staff member must be able to raise a ticket (got {r.status_code})"
    t = db.query(SupportTicket).filter(SupportTicket.subject == TEST_TICKET_SUBJECT).first()
    assert t is not None and t.status == "pending"
    assert t.ticket_number.startswith("TKT-"), f"unexpected ticket number {t.ticket_number}"


def test_only_a_system_administrator_writes_developer_remarks(admin, billing, db):
    t = db.query(SupportTicket).filter(SupportTicket.subject == TEST_TICKET_SUBJECT).first()
    if t is None:
        billing.post(f"{BASE}/support-tickets/new",
                     data={"subject": TEST_TICKET_SUBJECT, "message": "Raised by the billing representative.",
                           "ticket_type": "Question", "module": "Billing Management", "priority": "normal"},
                     follow_redirects=False)
        t = db.query(SupportTicket).filter(SupportTicket.subject == TEST_TICKET_SUBJECT).first()

    r = billing.post(f"{BASE}/support-tickets/{t.id}/status",
                     data={"status": "closed", "developer_remarks": "billing should not be able to write this"},
                     follow_redirects=False)
    assert r.status_code == 403, f"billing must not change a ticket's status (got {r.status_code})"
    db.expire_all()
    assert db.get(SupportTicket, t.id).status == "pending"
    assert not (db.get(SupportTicket, t.id).developer_remarks or "")

    _post(admin, f"{BASE}/support-tickets/{t.id}/status",
          {"status": "resolved", "developer_remarks": "Answered in the ticket thread."})
    db.expire_all()
    t = db.get(SupportTicket, t.id)
    assert t.status == "resolved" and t.developer_remarks == "Answered in the ticket thread."
    assert t.resolved_at is not None


# =========================================================================== OTP
def test_otp_setup_saves(admin, db):
    cfg = db.query(OtpConfiguration).first()
    before = (cfg.is_enabled, cfg.channel, cfg.code_length, list(cfg.required_for_roles or []))
    _post(admin, f"{BASE}/otp/setup",
          {"is_enabled": "1", "channel": "whatsapp", "code_length": "8", "validity_minutes": "15",
           "max_attempts": "3", "resend_after_seconds": "90", "required_for_roles": "super_admin",
           "rationale": "pytest"})
    db.expire_all()
    cfg = db.query(OtpConfiguration).first()
    assert cfg.is_enabled and cfg.channel == "whatsapp" and cfg.code_length == 8
    assert cfg.validity_minutes == 15 and cfg.max_attempts == 3 and cfg.resend_after_seconds == 90
    assert "super_admin" in cfg.required_for_roles
    # restore
    data = {"channel": before[1], "code_length": str(before[2]), "validity_minutes": "10", "max_attempts": "5",
            "resend_after_seconds": "60"}
    if before[0]:
        data["is_enabled"] = "1"
    for slug in before[3]:
        data.setdefault("required_for_roles", slug)
    _post(admin, f"{BASE}/otp/setup", data)
    db.expire_all()
    assert db.query(OtpConfiguration).first().is_enabled == before[0]


def test_enabling_two_factor_for_a_user_sets_the_flag(admin, db):
    target = (db.query(User).join(Role, User.role_id == Role.id)
              .filter(Role.portal == "admin", User.is_active.is_(True), User.two_factor_enabled.is_(False))
              .order_by(User.id).first())
    assert target is not None, "expected at least one staff user without two-factor"
    uid = target.id
    _post(admin, f"{BASE}/otp/users/{uid}/toggle", {"rationale": "pytest"})
    db.expire_all()
    assert db.get(User, uid).two_factor_enabled is True
    events = db.query(AuditEvent).filter(AuditEvent.module == "configuration",
                                         AuditEvent.entity_type == "User",
                                         AuditEvent.entity_id == uid).count()
    assert events >= 1, "toggling two-factor must be audited"
    _post(admin, f"{BASE}/otp/users/{uid}/toggle", {"rationale": "pytest restore"})
    db.expire_all()
    assert db.get(User, uid).two_factor_enabled is False


# =========================================================================== roles
def test_roles_page_groups_by_application_and_does_not_edit(admin):
    body = admin.get(f"{BASE}/roles").text
    for application in ["Human Resource", "Online Academics", "Configuration"]:
        assert application in body
    assert "/admin/roles" in body, "the read-only roles view should link to the real editor"


# =========================================================================== audit
def test_every_mutation_is_audited(db):
    assert db.query(AuditEvent).filter(AuditEvent.module == "configuration").count() > 0
    actions = {a for (a,) in db.query(AuditEvent.action).filter(AuditEvent.module == "configuration").distinct().all()}
    for expected in ["create", "update", "status_change", "execute"]:
        assert expected in actions, f"no '{expected}' audit event was written by the configuration area"
