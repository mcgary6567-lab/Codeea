"""ERP parity tests for the Billing Management "Clients Financial Summary" (docs/AUDIT_BILLING.md).

Covers the page and its saved reports, the tile filters, the CSV and print views, the Notify action and
the balance limit / payment day / billing remarks on the family — plus direct assertions on every derived
figure, made against a family whose ledger, invoices and receipts this module writes itself.

The suite is re-runnable against the same database: everything it creates carries a unique tag and is
removed again in a module-scoped teardown, and nothing it asserts depends on a count that only holds on a
freshly seeded database.

Run against a private database:
    $env:DATABASE_URL='sqlite:///./data/oqc_billA.db'; .venv/Scripts/python.exe -m pytest tests/test_billing_erp.py
"""
from __future__ import annotations

import os
from datetime import date, datetime
from uuid import uuid4

# The application reads DATABASE_URL at import time; set it before anything imports the app.
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_billA.db")

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.core.utils import FLASH_COOKIE, _decode_flash
from app.database import SessionLocal
from app.main import app
from app.models.config_erp import WhatsAppSender
from app.models.core import AuditEvent, CommunicationPreference, Notification, User
from app.models.finance import Invoice, LedgerEntry, Payment, Subscription
from app.models.people import Client, Student
from app.services import billing

TAG = f"pytest-{uuid4().hex[:8]}"
SUMMARY = "/finance/clients-summary"

# The known ledger the derived figures are asserted against (family A).
A_DEBIT, A_CREDIT = 500.0, 200.0
A_BALANCE = A_DEBIT - A_CREDIT          # 300.00
A_LIMIT = 100.0
A_EXCEEDED = A_BALANCE - A_LIMIT        # 200.00
A_PENDING = 300.0                       # one confirmed invoice of 500 with 200 paid
A_RECEIVED = 200.0                      # one confirmed receipt; the pending one does not count
A_INVOICED = 500.0                      # the cancelled invoice does not count
CHARGE_DAY = date(2026, 1, 10)
PAYMENT_DAY = date(2026, 2, 10)


def _client(username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}: {r.status_code}"
    return c


@pytest.fixture(scope="module")
def admin() -> TestClient:
    return _client("admin@oqc.local", "Admin@12345")


@pytest.fixture(scope="module")
def billing_desk() -> TestClient:
    return _client("billing@oqc.local", "Billing@123")


@pytest.fixture(scope="module")
def a_parent() -> TestClient:
    return _client("parent1@oqc.local", "Parent@123")


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


def _make_user(s, suffix: str, name: str) -> User:
    u = User(email=f"{TAG}-{suffix}@oqc.local", username=f"{TAG}-{suffix}", full_name=f"{name} {TAG}",
             hashed_password=hash_password(uuid4().hex), is_active=True)
    s.add(u)
    s.flush()
    return u


def _make_client(s, suffix: str, name: str, *, user: User | None, limit: float, opt_in: bool = True,
                 whatsapp: str | None = None) -> Client:
    c = Client(client_code=f"C-{TAG[-6:].upper()}{suffix}", full_name=f"{name} {TAG}",
               email=f"{TAG}-{suffix}@families.test", user_id=user.id if user else None,
               country="United Kingdom", currency="GBP", timezone="Europe/London", status="active",
               shift="night", joined_at=date(2025, 6, 1), fee_recurrence="monthly",
               balance_limit=limit, payment_day=12, billing_remarks=f"Test family {TAG}",
               whatsapp=whatsapp, whatsapp_opt_in=opt_in, consent_given=True)
    s.add(c)
    s.flush()
    return c


def _post_ledger(s, client: Client, entry_type: str, debit: float, credit: float, on: date) -> None:
    s.add(LedgerEntry(client_id=client.id, entry_date=on, entry_type=entry_type,
                      description=f"{entry_type} {TAG}", debit=debit, credit=credit, currency="GBP",
                      balance_after=0, reference_type="manual"))


@pytest.fixture(scope="module")
def families() -> dict:
    """Three families with a ledger we control, and a WhatsApp sender that is always ready to send.

    A — owes 300 against a limit of 100, has a portal account and is opted in to WhatsApp.
    B — owes the same 300 but has no limit agreed and no portal account.
    C — owes nothing, has a portal account, and has opted out of WhatsApp.
    """
    s = SessionLocal()
    try:
        user_a, user_c = _make_user(s, "a", "Family A"), _make_user(s, "c", "Family C")
        a = _make_client(s, "A", "Ahmed Household", user=user_a, limit=A_LIMIT, whatsapp="+441234000001")
        b = _make_client(s, "B", "Bashir Household", user=None, limit=0.0, whatsapp="+441234000002")
        c = _make_client(s, "C", "Chishti Household", user=user_c, limit=250.0, opt_in=False)
        for fam in (a, b):
            _post_ledger(s, fam, "charge", A_DEBIT, 0, CHARGE_DAY)
            _post_ledger(s, fam, "payment", 0, A_CREDIT, PAYMENT_DAY)
        s.add_all([
            Invoice(invoice_number=f"INV-{TAG}-1", client_id=a.id, issue_date=CHARGE_DAY,
                    due_date=date(2026, 1, 17), currency="GBP", subtotal=A_DEBIT, total=A_DEBIT,
                    paid_amount=A_CREDIT, status="confirmed", remarks=TAG),
            Invoice(invoice_number=f"INV-{TAG}-2", client_id=a.id, issue_date=date(2026, 1, 20),
                    due_date=date(2026, 1, 27), currency="GBP", subtotal=999, total=999,
                    status="cancelled", remarks=TAG),
            Payment(payment_number=f"PAY-{TAG}-1", client_id=a.id, amount=A_CREDIT, currency="GBP",
                    status="confirmed", received_at=datetime.combine(PAYMENT_DAY, datetime.min.time()),
                    method="bank_transfer", notes=TAG),
            Payment(payment_number=f"PAY-{TAG}-2", client_id=a.id, amount=50, currency="GBP",
                    status="pending", received_at=datetime(2026, 3, 1), method="card", notes=TAG),
        ])
        for n in (1, 2):
            s.add(Student(student_code=f"S-{TAG[-6:].upper()}{n}", client_id=a.id,
                          full_name=f"Child {n} {TAG}", status="active"))
        s.flush()
        student_id = s.query(Student).filter(Student.client_id == a.id).order_by(Student.id).first().id
        s.add_all([
            Subscription(subscription_code=f"SUB-{TAG}-1", client_id=a.id, student_id=student_id,
                         currency="GBP", price=100, status="active", notes=TAG),
            Subscription(subscription_code=f"SUB-{TAG}-2", client_id=a.id, student_id=student_id,
                         currency="GBP", price=100, status="cancelled", notes=TAG),
        ])
        s.add(WhatsAppSender(description=f"Billing sender {TAG}", number=f"+9230000{TAG[-4:]}",
                             purpose="billing", api_status="connected", status="active",
                             interval_seconds=0, messages_per_cycle=5, live_mode=False))
        s.commit()
        return {"a": a.id, "b": b.id, "c": c.id, "user_a": user_a.id, "user_c": user_c.id,
                "code_a": a.client_code}
    finally:
        s.close()


@pytest.fixture(scope="module", autouse=True)
def _purge(families):
    """Remove everything this run created, so the suite can be run again against the same database."""
    yield
    s = SessionLocal()
    try:
        like = f"%{TAG}%"
        ids = [families["a"], families["b"], families["c"]]
        user_ids = [families["user_a"], families["user_c"]]
        s.query(Payment).filter(Payment.client_id.in_(ids)).delete(synchronize_session=False)
        s.query(Invoice).filter(Invoice.client_id.in_(ids)).delete(synchronize_session=False)
        s.query(Subscription).filter(Subscription.client_id.in_(ids)).delete(synchronize_session=False)
        s.query(Student).filter(Student.client_id.in_(ids)).delete(synchronize_session=False)
        s.query(LedgerEntry).filter(LedgerEntry.client_id.in_(ids)).delete(synchronize_session=False)
        s.query(CommunicationPreference).filter(CommunicationPreference.client_id.in_(ids)).delete(synchronize_session=False)
        s.query(Notification).filter(Notification.user_id.in_(user_ids)).delete(synchronize_session=False)
        s.query(AuditEvent).filter(AuditEvent.entity_type == "Client",
                                   AuditEvent.entity_id.in_(ids)).delete(synchronize_session=False)
        s.query(WhatsAppSender).filter(WhatsAppSender.description.ilike(like)).delete(synchronize_session=False)
        s.query(Client).filter(Client.id.in_(ids)).delete(synchronize_session=False)
        s.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()


def _row(session, client_id: int, date_from=None, date_to=None) -> dict:
    c = session.get(Client, client_id)
    return billing.client_financial_summary(session, [c], date_from, date_to)[0]


def _flash(response) -> str:
    return " ".join(m["message"] for m in _decode_flash(response.cookies.get(FLASH_COOKIE)))


# --------------------------------------------------------------------------- derived figures
def test_every_derived_figure(session, families):
    r = _row(session, families["a"])
    assert r["balance"] == A_BALANCE
    assert r["balance_limit"] == A_LIMIT
    assert r["exceeded"] == A_EXCEEDED
    assert r["pending"] == A_PENDING, "only the collectable invoice counts, net of what was paid"
    assert r["received"] == A_RECEIVED, "the pending receipt is not money received"
    assert r["invoices_total"] == A_INVOICED, "the cancelled invoice is not an invoice"
    assert r["last_payment"].date() == PAYMENT_DAY
    assert r["students"] == 2
    assert r["regular_subscriptions"] == 1, "the cancelled subscription is not a regular subscription"
    assert r["payment_day"] == 12
    assert TAG in r["billing_remarks"]
    assert r["fee_recurrence"] == "monthly"
    assert r["rate"] == billing.get_rate(session, "GBP")


def test_a_family_with_no_limit_is_never_exceeded(session, families):
    b = _row(session, families["b"])
    assert b["balance"] == A_BALANCE and b["balance_limit"] == 0
    assert b["exceeded"] == 0.0
    assert billing.exceeded_amount(1_000_000, 0) == 0.0


def test_exceeded_never_goes_below_zero(session, families):
    c = _row(session, families["c"])
    assert c["balance"] == 0.0 and c["balance_limit"] == 250.0
    assert c["exceeded"] == 0.0
    assert billing.exceeded_amount(10, 250) == 0.0


def test_the_date_range_bounds_only_the_period_figures(session, families):
    inside = _row(session, families["a"], date(2026, 2, 1), date(2026, 2, 28))
    assert inside["received"] == A_RECEIVED and inside["invoices_total"] == 0.0
    january = _row(session, families["a"], date(2026, 1, 1), date(2026, 1, 31))
    assert january["received"] == 0.0 and january["invoices_total"] == A_INVOICED
    # Balance and pending are position figures: the window never changes them.
    assert january["balance"] == A_BALANCE and january["pending"] == A_PENDING


def test_saved_views_select_the_right_families(session, families):
    clients = session.query(Client).filter(Client.id.in_(list(families.values()))).all()
    rows = billing.client_financial_summary(session, clients)
    codes = lambda view: {r["client"].id for r in billing.apply_summary_view(rows, view)}  # noqa: E731
    assert codes("all") >= {families["a"], families["b"], families["c"]}
    assert families["a"] in codes("exceeded") and families["b"] not in codes("exceeded")
    assert families["b"] in codes("receivables") and families["c"] not in codes("receivables")
    assert families["a"] in codes("all-dues")


def test_tile_totals_are_converted_to_base(session, families):
    clients = session.query(Client).filter(Client.id.in_([families["a"], families["b"]])).all()
    rows = billing.client_financial_summary(session, clients)
    totals = billing.client_summary_totals(session, rows)
    rate = billing.get_rate(session, "GBP")
    assert totals["families"] == 2 and totals["exceeded_count"] == 1 and totals["with_dues"] == 2
    assert totals["balance"] == round(2 * A_BALANCE * rate, 2)
    assert totals["exceeded"] == round(A_EXCEEDED * rate, 2)
    assert totals["base"] == billing.base_currency(session)


def test_the_seed_left_families_on_both_sides_of_their_limit(session):
    seeded = session.query(Client).filter(Client.payment_day.isnot(None),
                                          ~Client.full_name.ilike(f"%{TAG}%")).all()
    assert len(seeded) >= 20, "app/seed/billing_erp.py should have given every family a payment day"
    rows = billing.client_financial_summary(session, seeded)
    assert sum(1 for r in rows if r["exceeded"] > 0) >= 3, "the Exceeded tile would be empty"
    assert sum(1 for r in rows if r["balance_limit"] > 0 and r["exceeded"] == 0) >= 5
    assert any(r["billing_remarks"] for r in rows)


# --------------------------------------------------------------------------- pages
@pytest.mark.parametrize("url", [
    SUMMARY,
    SUMMARY + "?view=all",
    SUMMARY + "?view=receivables",
    SUMMARY + "?view=exceeded",
    SUMMARY + "?view=all-dues",
    SUMMARY + "?view=nonsense-report",                     # unknown reports fall back to the primary one
    SUMMARY + "?status=active",
    SUMMARY + "?status=on_leave",
    SUMMARY + "?status=pass_out",
    SUMMARY + "?shift=morning",
    SUMMARY + "?shift=night&currency=GBP",
    SUMMARY + "?date_from=2026-01-01&date_to=2026-12-31",
    SUMMARY + "?q=nothing-matches-this",                   # the empty state
    SUMMARY + "?page=2",
    SUMMARY + "?print_view=1",
])
def test_pages(admin, url):
    assert admin.get(url).status_code == 200


def test_the_billing_desk_sees_the_page(billing_desk, families):
    body = billing_desk.get(f"{SUMMARY}?q={TAG}").text
    assert families["code_a"] in body
    assert "Balance Limit" in body and "Exceeded" in body and "Fee Recurrence" in body


def test_search_filters_to_one_family(admin, families):
    body = admin.get(f"{SUMMARY}?q={TAG}").text
    assert body.count("C-" + TAG[-6:].upper()) >= 3          # all three test families, none of the seeded ones


def test_the_exceeded_report_holds_the_over_limit_family_only(admin, families):
    exceeded = admin.get(f"{SUMMARY}?q={TAG}&view=exceeded").text
    assert f"C-{TAG[-6:].upper()}A" in exceeded
    assert f"C-{TAG[-6:].upper()}B" not in exceeded, "a family with no limit is never exceeded"
    assert f"C-{TAG[-6:].upper()}C" not in exceeded


def test_csv_export(admin, families):
    r = admin.get(f"{SUMMARY}?q={TAG}&format=csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "clients-financial-summary" in r.headers["content-disposition"]
    lines = r.text.splitlines()
    assert lines[1].startswith("ID,Client Name,Status,Reg Date,Shift Name,B.R,Students")
    assert lines[1].rstrip().endswith("Billing Remarks,Last Payment,Fee Recurrence")
    row = [line for line in lines if line.startswith(families["code_a"])][0]
    assert ",300.00,100.00,200.00,300.00,200.00,500.00," in row


def test_print_view_renders_the_print_header(admin):
    body = admin.get(f"{SUMMARY}?print_view=1").text
    assert "print-only" in body and "window.print()" in body


# --------------------------------------------------------------------------- permissions
@pytest.mark.parametrize("url", [SUMMARY, SUMMARY + "?view=exceeded", SUMMARY + "?format=csv"])
def test_a_parent_cannot_reach_the_summary(a_parent, url):
    assert a_parent.get(url).status_code == 403


@pytest.mark.parametrize("url", [SUMMARY, SUMMARY + "?view=exceeded", SUMMARY + "?format=csv"])
def test_a_teacher_cannot_reach_the_summary(a_teacher, url):
    assert a_teacher.get(url).status_code == 403


def test_neither_a_parent_nor_a_teacher_can_notify(a_parent, a_teacher, families):
    data = {"ids": [families["a"]], "subject": f"Pay up {TAG}", "body": "Now."}
    assert a_parent.post(f"{SUMMARY}/notify", data=data, follow_redirects=False).status_code == 403
    assert a_teacher.post(f"{SUMMARY}/notify", data=data, follow_redirects=False).status_code == 403


# --------------------------------------------------------------------------- notify
def _notices(s, user_id: int, channel: str | None = None) -> list[Notification]:
    q = s.query(Notification).filter(Notification.user_id == user_id,
                                     Notification.event_type == "billing_notice")
    if channel:
        q = q.filter(Notification.channel == channel)
    return q.order_by(Notification.id).all()


def test_notify_messages_each_family_and_writes_the_audit_trail(admin, session, families):
    before = len(_notices(session, families["user_a"]))
    r = admin.post(f"{SUMMARY}/notify", follow_redirects=False, data={
        "ids": [families["a"], families["c"]], "channel": "in_app",
        "subject": f"Outstanding balance {TAG}",
        "body": "Dear {{name}}, your balance is {{balance}} and your limit is {{limit}}.",
        "rationale": f"Monthly chase {TAG}"})
    assert r.status_code == 303
    assert "2 family(ies) notified." in _flash(r)
    session.expire_all()
    notices = _notices(session, families["user_a"])
    assert len(notices) == before + 1
    latest = notices[-1]
    assert latest.channel == "in_app" and latest.status == "delivered"
    assert TAG in latest.title
    assert "300.00" in latest.body and "100.00" in latest.body, "the placeholders are filled per family"
    assert _notices(session, families["user_c"]), "the second ticked family was messaged too"

    events = (session.query(AuditEvent).filter(AuditEvent.entity_type == "Client",
                                               AuditEvent.entity_id == families["a"],
                                               AuditEvent.action == "notify").all())
    assert events and any(TAG in (e.rationale or "") for e in events)


def test_notify_skips_families_with_no_account_and_whatsapp_opt_outs(admin, session, families):
    r = admin.post(f"{SUMMARY}/notify", follow_redirects=False, data={
        "ids": [families["a"], families["b"], families["c"]], "channel": "whatsapp",
        "subject": f"WhatsApp chase {TAG}", "body": "Please clear the balance this week.",
        "rationale": f"Over the limit {TAG}"})
    assert r.status_code == 303
    flash = _flash(r)
    assert "1 family(ies) notified." in flash
    assert "1 with no portal account" in flash
    assert "1 opted out of WhatsApp" in flash
    session.expire_all()
    assert _notices(session, families["user_a"], "whatsapp"), "the opted-in family got the WhatsApp copy"
    assert not _notices(session, families["user_c"], "whatsapp")


def test_notify_can_use_a_seeded_template(admin, session, families):
    from app.models.core import NotificationTemplate
    tpl = (session.query(NotificationTemplate)
           .filter(NotificationTemplate.event_type == "invoice_issued").first())
    assert tpl is not None, "core seed provides the notification templates"
    before = len(_notices(session, families["user_a"]))
    r = admin.post(f"{SUMMARY}/notify", follow_redirects=False,
                   data={"ids": [families["a"]], "template_id": tpl.id, "channel": "in_app",
                         "rationale": f"Template send {TAG}"})
    assert r.status_code == 303
    session.expire_all()
    assert len(_notices(session, families["user_a"])) == before + 1


def test_notify_without_a_selection_or_a_message_writes_nothing(admin, session, families):
    before = len(_notices(session, families["user_a"]))
    empty = admin.post(f"{SUMMARY}/notify", follow_redirects=False,
                       data={"subject": "Nobody", "body": "Nothing."})
    assert empty.status_code == 303 and "Tick at least one family" in _flash(empty)
    blank = admin.post(f"{SUMMARY}/notify", follow_redirects=False,
                       data={"ids": [families["a"]], "subject": "", "body": ""})
    assert blank.status_code == 303 and "Choose a template" in _flash(blank)
    session.expire_all()
    assert len(_notices(session, families["user_a"])) == before


# --------------------------------------------------------------------------- the three fields on the family
def test_the_client_form_carries_the_billing_fields(admin, families):
    body = admin.get(f"/clients/{families['a']}/edit").text
    assert "Balance Limit" in body and "Payment Day" in body and "Billing Remarks" in body
    detail = admin.get(f"/clients/{families['a']}").text
    assert "Balance Limit" in detail and "Payment Day" in detail


def test_editing_the_family_stores_the_limit_the_day_and_the_remarks(admin, session, families):
    c = session.get(Client, families["a"])
    r = admin.post(f"/clients/{c.id}/edit", follow_redirects=False, data={
        "full_name": c.full_name, "country": c.country, "currency": "GBP", "timezone": c.timezone,
        "status": c.status, "shift": c.shift, "fee_recurrence": "monthly", "consent_given": "1",
        "joined_at": c.joined_at.isoformat(), "balance_limit": "450.50", "payment_day": "7",
        "billing_remarks": f"Agreed a higher limit {TAG}"})
    assert r.status_code == 303
    session.expire_all()
    c = session.get(Client, families["a"])
    assert float(c.balance_limit) == 450.50 and c.payment_day == 7
    assert TAG in c.billing_remarks
    # and the summary re-derives Exceeded from the new limit
    assert _row(session, families["a"])["exceeded"] == 0.0

    r = admin.post(f"/clients/{c.id}/edit", follow_redirects=False, data={
        "full_name": c.full_name, "country": c.country, "currency": "GBP", "timezone": c.timezone,
        "status": c.status, "shift": c.shift, "fee_recurrence": "monthly", "consent_given": "1",
        "joined_at": c.joined_at.isoformat(), "balance_limit": str(A_LIMIT), "payment_day": "12",
        "billing_remarks": f"Test family {TAG}"})
    assert r.status_code == 303
    session.expire_all()
    assert _row(session, families["a"])["exceeded"] == A_EXCEEDED


def test_an_out_of_range_payment_day_is_dropped(admin, session, families):
    c = session.get(Client, families["c"])
    r = admin.post(f"/clients/{c.id}/edit", follow_redirects=False, data={
        "full_name": c.full_name, "country": c.country, "currency": "GBP", "timezone": c.timezone,
        "status": c.status, "shift": c.shift, "fee_recurrence": "monthly", "consent_given": "1",
        "joined_at": c.joined_at.isoformat(), "balance_limit": "-40", "payment_day": "44"})
    assert r.status_code == 303
    session.expire_all()
    c = session.get(Client, families["c"])
    assert c.payment_day is None
    assert float(c.balance_limit) == 0.0, "a negative limit is floored at nothing"
