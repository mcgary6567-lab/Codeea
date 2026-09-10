"""Smoke test for the Finance module (subscriptions, discounts, invoices, payments, ledger, accounts,
expenses, currencies, REST API).

Run:  .venv/Scripts/python.exe tests/test_finance_module.py
ASCII output only (Windows console is cp1252).
"""
from __future__ import annotations

import base64
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.academic import Package  # noqa: E402
from app.models.finance import (Account, Currency, DiscountRequest, Expense, Invoice, JournalEntry, Payment,  # noqa: E402
                                Scholarship, Subscription)
from app.models.people import Client, Student  # noqa: E402
from app.services import billing  # noqa: E402

import logging  # noqa: E402

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("oqc.integrations").setLevel(logging.WARNING)
logging.getLogger("oqc").setLevel(logging.WARNING)

FAILURES: list[str] = []
CHECKS = 0


def login(email: str, password: str) -> TestClient:
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", data={"username": email, "password": password})
    assert r.status_code in (200, 302, 303), f"login {email} -> {r.status_code}"
    return c


def flash_of(r) -> str:
    raw = r.cookies.get("oqc_flash")
    if not raw:
        return ""
    try:
        return " | ".join(m.get("message", "") for m in json.loads(base64.urlsafe_b64decode(raw.encode()).decode()))
    except Exception:
        return ""


def check(client: TestClient, method: str, url: str, expect, data=None, label: str = "", json_body=None):
    global CHECKS
    CHECKS += 1
    if method == "GET":
        r = client.get(url)
    elif json_body is not None:
        r = client.post(url, json=json_body)
    else:
        r = client.post(url, data=data or {})
    expected = expect if isinstance(expect, (list, tuple, set)) else [expect]
    if r.status_code not in expected:
        body = r.text[:400].replace("\n", " ") if r.status_code >= 400 or r.status_code == 200 else ""
        FAILURES.append(f"{method} {url} -> {r.status_code} (expected {expect}) {label} {body}")
    return r


def expect_flash(r, needle: str, label: str):
    global CHECKS
    CHECKS += 1
    msg = flash_of(r)
    if needle.lower() not in msg.lower():
        FAILURES.append(f"{label}: expected flash containing '{needle}', got '{msg}'")


def main() -> int:
    global CHECKS
    db = SessionLocal()
    admin = login("admin@oqc.local", "Admin@12345")

    # ---------------------------------------------------------------- fixtures from the seeded database
    sub = db.query(Subscription).filter(Subscription.status == "active").order_by(Subscription.id).first()
    frozen = db.query(Subscription).filter(Subscription.status == "frozen").first()
    inv_open = db.query(Invoice).filter(Invoice.status.in_(["sent", "overdue", "partial"])).order_by(Invoice.id).first()
    inv_paid = db.query(Invoice).filter(Invoice.status == "paid").first()
    pay = db.query(Payment).filter(Payment.status == "completed").order_by(Payment.id.desc()).first()
    exp_pending = db.query(Expense).filter(Expense.status == "pending").first()
    account = db.query(Account).filter(Account.code == "4000").first()
    je = db.query(JournalEntry).order_by(JournalEntry.id.desc()).first()
    uk_client = (db.query(Client).filter(Client.country == "United Kingdom", Client.status == "active")
                 .order_by(Client.id).first())
    uk_student = uk_client.students[0] if uk_client and uk_client.students else None
    teacher = uk_student.teacher if uk_student else None
    pending_mgr = db.query(DiscountRequest).filter(DiscountRequest.status == "pending",
                                                   DiscountRequest.approver_tier == "manager").first()
    pending_ceo = db.query(DiscountRequest).filter(DiscountRequest.status == "pending",
                                                   DiscountRequest.approver_tier == "ceo").first()
    schol = db.query(Scholarship).first()
    # (pending discount requests are consumed by this test, so they are only asserted on a freshly seeded database)
    for name, obj in [("subscription", sub), ("open invoice", inv_open), ("payment", pay), ("uk student", uk_student),
                      ("account 4000", account), ("journal", je), ("scholarship", schol)]:
        if obj is None:
            FAILURES.append(f"seed fixture missing: {name}")
    if uk_student is None or sub is None:
        print("FATAL: seed data missing; run seed.py --reset first")
        return 1

    # ---------------------------------------------------------------- GET pages
    check(admin, "GET", "/finance", 303, label="finance home redirects to subscriptions")
    pages = [
        "/finance/subscriptions", "/finance/subscriptions?status=active",
        "/finance/subscriptions?status=pending_approval", "/finance/subscriptions?currency=GBP",
        "/finance/subscriptions/new", "/finance/subscriptions/report?group_by=course",
        "/finance/subscriptions/report?group_by=package", "/finance/subscriptions/report?group_by=country",
        "/finance/subscriptions/report?group_by=teacher",
        f"/finance/subscriptions/{sub.id}", f"/finance/subscriptions/{sub.id}?tab=billing",
        f"/finance/subscriptions/{sub.id}?tab=audit",
        "/finance/discounts", "/finance/discounts?status=approved", "/finance/discounts?status=",
        "/finance/discounts/register", "/finance/discounts/register.csv",
        "/finance/discounts/scholarships", "/finance/discounts/scholarships?status=approved",
        "/finance/invoices", "/finance/invoices?status=overdue", "/finance/invoices?q=INV",
        f"/finance/invoices/{inv_open.id}", f"/finance/invoices/{inv_open.id}/pdf",
        "/finance/payments", "/finance/payments?status=completed", "/finance/payments?reconciled=no",
        "/finance/payments/new", "/finance/payments/reconciliation", "/finance/payments/failed",
        f"/finance/payments/{pay.id}", f"/finance/payments/{pay.id}/receipt",
        "/finance/ledger", "/finance/ledger?q=C-", "/finance/ledger/credits",
        f"/finance/ledger/{sub.client_id}", f"/finance/ledger/{sub.client_id}?entry_type=payment",
        f"/finance/ledger/{sub.client_id}/export.csv",
        "/finance/accounts", f"/finance/accounts/{account.id}",
        "/finance/accounts/journal", f"/finance/accounts/journal/{je.id}",
        "/finance/accounts/pnl", "/finance/accounts/pnl?scope=quarter", "/finance/accounts/pnl?scope=ytd",
        "/finance/accounts/cash-flow", "/finance/accounts/aging", "/finance/accounts/payables",
        "/finance/accounts/budget", "/finance/accounts/forecast", "/finance/accounts/close",
        "/finance/accounts/consolidated",
        "/finance/expenses", "/finance/expenses?status=pending", "/finance/expenses/new",
        "/finance/currencies", "/finance/currencies/GBP", "/finance/currencies/PKR",
    ]
    if inv_paid:
        pages.append(f"/finance/invoices/{inv_paid.id}")
    if frozen:
        pages.append(f"/finance/subscriptions/{frozen.id}")
    if exp_pending:
        pages.append(f"/finance/expenses/{exp_pending.id}")
    for url in pages:
        check(admin, "GET", url, 200)

    # ---------------------------------------------------------------- API
    for url in ["/api/v1/finance/subscriptions", "/api/v1/finance/subscriptions?status=active",
                f"/api/v1/finance/subscriptions/{sub.id}", "/api/v1/finance/invoices",
                f"/api/v1/finance/invoices/{inv_open.id}", "/api/v1/finance/payments",
                f"/api/v1/finance/ledger/{sub.client_id}", "/api/v1/finance/expenses", "/api/v1/finance/currencies"]:
        check(admin, "GET", url, 200)

    # ---------------------------------------------------------------- subscription creation & the discount ladder
    base_form = {"client_id": uk_client.id, "student_id": uk_student.id, "currency": "GBP",
                 "sessions_per_week": 3, "price": 40, "teacher_id": teacher.id if teacher else "",
                 "start_date": date.today().isoformat()}

    r = check(admin, "POST", "/finance/subscriptions/new", 303,
              dict(base_form, discount_pct=10, rationale="Sibling discount agreed by the closer."),
              label="10% manager tier")
    expect_flash(r, "awaiting discount approval", "10% subscription")

    r = check(admin, "POST", "/finance/subscriptions/new", 303,
              dict(base_form, discount_pct=30, rationale="Three siblings; exceptional CEO-tier request."),
              label="30% ceo tier")
    expect_flash(r, "awaiting discount approval", "30% subscription")

    r = check(admin, "POST", "/finance/subscriptions/new", 303,
              dict(base_form, discount_pct=40, rationale="Testing the prohibited band."), label="40% prohibited")
    expect_flash(r, "prohibited", "40% subscription")

    r = check(admin, "POST", "/finance/subscriptions/new", 303,
              dict(base_form, currency="PKR", price=900, sessions_per_week=5, discount_pct=0),
              label="below the teacher-cost floor")
    expect_flash(r, "floor", "below-floor subscription")

    r = check(admin, "POST", "/finance/subscriptions/new", 303,
              dict(base_form, discount_pct=15, rationale=""), label="discount without a rationale")
    expect_flash(r, "rationale is required", "discount without a rationale")

    db.expire_all()
    new_mgr = (db.query(DiscountRequest).filter(DiscountRequest.status == "pending",
                                                DiscountRequest.approver_tier == "manager")
               .order_by(DiscountRequest.id.desc()).first())
    new_ceo = (db.query(DiscountRequest).filter(DiscountRequest.status == "pending",
                                                DiscountRequest.approver_tier == "ceo")
               .order_by(DiscountRequest.id.desc()).first())
    if not new_mgr or not new_ceo:
        FAILURES.append("discount requests were not created for the 10% / 30% subscriptions")

    # ---------------------------------------------------------------- manager may approve only the manager tier
    manager = login("manager@oqc.local", "Manager@123")
    for url in ["/finance/subscriptions", "/finance/discounts", "/finance/discounts/register",
                "/finance/invoices", f"/finance/ledger/{sub.client_id}"]:
        check(manager, "GET", url, 200, label="manager view")
    check(manager, "GET", "/finance/accounts", 403, label="manager has no accounts permission")

    if new_ceo:
        r = check(manager, "POST", f"/finance/discounts/{new_ceo.id}/decide", 303,
                  {"decision": "approve", "note": "Manager attempting a CEO-tier decision."},
                  label="manager on ceo tier")
        expect_flash(r, "CEO", "manager approving a CEO-tier discount")
    if new_mgr:
        r = check(manager, "POST", f"/finance/discounts/{new_mgr.id}/decide", 303,
                  {"decision": "approve", "note": "Within the manager threshold; margin protected."},
                  label="manager approves manager tier")
        expect_flash(r, "approved", "manager approving a manager-tier discount")
    if new_ceo:
        r = check(admin, "POST", f"/finance/discounts/{new_ceo.id}/decide", 303,
                  {"decision": "approve", "note": "CEO approval: three siblings, long-term family."},
                  label="ceo approves ceo tier")
        expect_flash(r, "approved", "CEO approving a CEO-tier discount")
    if pending_mgr:
        check(admin, "POST", f"/finance/discounts/{pending_mgr.id}/decide", 303,
              {"decision": "reject", "note": "Margin too thin for this package."}, label="reject a discount")

    db.expire_all()
    approved_sub = (db.query(Subscription).filter(Subscription.client_id == uk_client.id,
                                                  Subscription.status == "active")
                    .order_by(Subscription.id.desc()).first())

    # ---------------------------------------------------------------- scholarships
    check(admin, "POST", "/finance/discounts/scholarships/new", 303,
          {"client_id": uk_client.id, "student_id": uk_student.id, "scholarship_type": "need_based",
           "coverage_pct": 25, "monthly_amount": 0, "currency": "GBP", "start_date": date.today().isoformat(),
           "reason": "Household income reduced; support agreed for one term."}, label="new scholarship")
    db.expire_all()
    new_schol = db.query(Scholarship).filter(Scholarship.status == "pending").order_by(Scholarship.id.desc()).first()
    if new_schol:
        check(admin, "POST", f"/finance/discounts/scholarships/{new_schol.id}/decide", 303,
              {"decision": "approve", "note": "Approved by the CEO after a confidential review."},
              label="approve scholarship")

    # ---------------------------------------------------------------- invoices
    last_period = (db.query(func.max(Invoice.period_start)).filter(Invoice.subscription_id == sub.id).scalar()
                   or date.today().replace(day=1))
    if isinstance(last_period, str):
        last_period = date.fromisoformat(last_period)
    next_month = billing.add_months(max(last_period, date.today().replace(day=1)), 1)
    r = check(admin, "POST", f"/finance/subscriptions/{sub.id}/invoice", 303,
              {"period_start": next_month.isoformat(),
               "period_end": (billing.add_months(next_month, 1) - timedelta(days=1)).isoformat()},
              label="generate invoice now")
    expect_flash(r, "generated", "generate invoice now")
    db.expire_all()
    fresh_inv = db.query(Invoice).order_by(Invoice.id.desc()).first()

    check(admin, "POST", f"/finance/invoices/{fresh_inv.id}/send", 303, label="send invoice")
    check(admin, "POST", f"/finance/invoices/{fresh_inv.id}/reminder", 303, label="send reminder")
    check(admin, "POST", f"/finance/invoices/{fresh_inv.id}/remarks", 303,
          {"remarks": "Family asked for a bank-transfer reference."}, label="invoice remarks")
    check(admin, "POST", f"/finance/invoices/{fresh_inv.id}/overdue", 303, label="mark overdue")

    r = check(admin, "POST", f"/finance/invoices/{fresh_inv.id}/payment", 303,
              {"amount": float(fresh_inv.total), "currency": fresh_inv.currency, "method": "bank_transfer",
               "reference": "TEST-PAY-001", "received_at": date.today().isoformat(), "status": "completed"},
              label="record payment on an invoice")
    expect_flash(r, "recorded", "record payment")
    db.expire_all()
    new_pay = db.query(Payment).order_by(Payment.id.desc()).first()

    check(admin, "POST", f"/finance/payments/{new_pay.id}/refund", 303,
          {"amount": round(float(new_pay.amount) / 2, 2), "rationale": "Half month refunded after a teacher change."},
          label="refund")
    check(admin, "POST", f"/finance/payments/{new_pay.id}/reconcile", 303,
          {"bank_reference": "HSBC-STMT-0912"}, label="reconcile a payment")
    check(admin, "POST", "/finance/payments/reconcile", 303, label="bulk reconcile with nothing selected")

    check(admin, "POST", "/finance/payments/new", 303,
          {"client_id": uk_client.id, "amount": 5, "currency": "GBP", "method": "cash", "reference": "TEST-CASH",
           "received_at": date.today().isoformat(), "status": "completed", "notes": "Cash handed at the office."},
          label="standalone payment")
    check(admin, "POST", "/finance/invoices/generate", 303, {"period": date.today().strftime("%Y-%m")},
          label="bulk invoice generation")

    # a fresh invoice with no payments so the void path can be exercised
    void_month = billing.add_months(next_month, 2)
    check(admin, "POST", f"/finance/subscriptions/{sub.id}/invoice", 303,
          {"period_start": void_month.isoformat()}, label="invoice for the void test")
    db.expire_all()
    to_void = db.query(Invoice).order_by(Invoice.id.desc()).first()
    r = check(admin, "POST", f"/finance/invoices/{to_void.id}/void", 303,
              {"rationale": "Raised in error while testing the period roll-forward."}, label="void invoice")
    expect_flash(r, "voided", "void invoice")

    # ---------------------------------------------------------------- ledger
    r = check(admin, "POST", f"/finance/ledger/{uk_client.id}/adjustment", 303,
              {"kind": "credit", "amount": 12.5, "currency": "GBP", "description": "Referral reward credit",
               "rationale": "Ambassador referral qualified this month."}, label="post a credit")
    expect_flash(r, "posted", "credit")
    check(admin, "POST", f"/finance/ledger/{uk_client.id}/adjustment", 303,
          {"kind": "debit", "amount": 4.0, "currency": "GBP", "description": "Late payment administration fee",
           "rationale": "Agreed with the family after the third reminder."}, label="post an adjustment")

    # ---------------------------------------------------------------- accounts
    check(admin, "POST", "/finance/accounts/new", 303,
          {"code": "5750", "name": "Teacher training", "account_type": "expense",
           "description": "Ustaadh Lab and CPD spend"}, label="new account")
    db.expire_all()
    new_account = db.query(Account).filter(Account.code == "5750").first()
    if new_account:
        check(admin, "POST", f"/finance/accounts/{new_account.id}/edit", 303,
              {"name": "Teacher training & CPD", "account_type": "expense", "is_active": "1"}, label="edit account")

    r = check(admin, "POST", "/finance/accounts/journal/new", 303,
              {"entry_date": date.today().isoformat(), "description": "Accrual for the training programme",
               "account_code": ["5750", "2000"], "debit": ["25000", ""], "credit": ["", "25000"],
               "rationale": "Monthly accrual agreed in the finance review."}, label="manual journal")
    expect_flash(r, "posted", "manual journal")
    r = check(admin, "POST", "/finance/accounts/journal/new", 303,
              {"entry_date": date.today().isoformat(), "description": "Unbalanced entry",
               "account_code": ["5750", "2000"], "debit": ["25000", ""], "credit": ["", "10000"]},
              label="unbalanced journal is refused")
    expect_flash(r, "not balanced", "unbalanced journal")

    db.expire_all()
    manual_je = db.query(JournalEntry).filter(JournalEntry.reference_type == "manual").order_by(JournalEntry.id.desc()).first()
    if manual_je:
        check(admin, "POST", f"/finance/accounts/journal/{manual_je.id}/reverse", 303,
              {"rationale": "Accrual reversed after the invoice arrived."}, label="reverse a journal")

    period = date.today().replace(day=1)
    for _ in range(3):
        period = billing.add_months(period, -1)
    close_period = period.strftime("%Y-%m")
    r = check(admin, "POST", "/finance/accounts/close", 303,
              {"period": close_period, "notes": "Month-end close reviewed with the HOD Finance."},
              label="close a period")
    expect_flash(r, close_period, "close period")
    check(admin, "POST", "/finance/accounts/reopen", 303,
          {"period": close_period, "rationale": "A late supplier invoice needs posting."}, label="reopen a period")

    check(admin, "POST", "/finance/accounts/budget/new", 303,
          {"period": date.today().strftime("%Y-%m"), "category": "marketing", "amount": 120000,
           "notes": "Autumn intake campaign."}, label="budget line")

    # ---------------------------------------------------------------- expenses
    r = check(admin, "POST", "/finance/expenses/new", 303,
              {"category": "software", "amount": 15000, "currency": "PKR", "vendor": "Canva Pro",
               "expense_date": date.today().isoformat(), "description": "Design suite for the marketing team."},
              label="new expense")
    expect_flash(r, "submitted", "new expense")
    db.expire_all()
    new_expense = db.query(Expense).order_by(Expense.id.desc()).first()
    r = check(admin, "POST", f"/finance/expenses/{new_expense.id}/approve", 303,
              {"note": "Within the software budget."}, label="approve expense")
    expect_flash(r, "journal", "approve expense")
    check(admin, "POST", f"/finance/expenses/{new_expense.id}/pay", 303, {"reference": "TT-778812"},
          label="pay expense")
    if exp_pending:
        check(admin, "POST", f"/finance/expenses/{exp_pending.id}/reject", 303,
              {"note": "Duplicate of the vendor invoice already captured."}, label="reject expense")

    # ---------------------------------------------------------------- currencies
    cur = db.query(Currency).filter(Currency.code == "GBP").first()
    r = check(admin, "POST", "/finance/currencies/GBP/rate", 303, {"rate": round(float(cur.rate_to_base) + 1.5, 4)},
              label="set exchange rate")
    expect_flash(r, "updated", "set exchange rate")
    r = check(admin, "POST", "/finance/currencies/PKR/rate", 303, {"rate": 2.0}, label="base currency rate is fixed")
    expect_flash(r, "base currency", "base currency rate")

    # ---------------------------------------------------------------- subscription lifecycle
    if approved_sub:
        check(admin, "POST", f"/finance/subscriptions/{approved_sub.id}/freeze", 303,
              {"freeze_start": date.today().isoformat(),
               "freeze_end": (date.today() + timedelta(days=30)).isoformat(),
               "reason": "Family travelling for Umrah."}, label="freeze")
        check(admin, "POST", f"/finance/subscriptions/{approved_sub.id}/unfreeze", 303,
              {"reason": "Family returned early."}, label="unfreeze")
        check(admin, "POST", f"/finance/subscriptions/{approved_sub.id}/renew", 303, label="renew")
        pkg_row = db.query(Package).filter(Package.is_active.is_(True), Package.is_trial.is_(False)).first()
        check(admin, "POST", f"/finance/subscriptions/{approved_sub.id}/change-package", 303,
              {"package_id": pkg_row.id if pkg_row else approved_sub.package_id, "price": 45, "sessions_per_week": 3,
               "rationale": "Family upgraded to the intensive plan."}, label="change package")
        check(admin, "POST", f"/finance/subscriptions/{approved_sub.id}/cancel", 303,
              {"reason": "Family relocating; agreed to pause indefinitely."}, label="cancel")

    # ---------------------------------------------------------------- REST API mutations
    r = check(admin, "POST", "/api/v1/finance/subscriptions", [201, 400], label="API create subscription",
              json_body={"client_id": uk_client.id, "student_id": uk_student.id, "price": 40, "currency": "GBP",
                         "discount_pct": 0, "teacher_id": teacher.id if teacher else None, "sessions_per_week": 3})
    r = check(admin, "POST", "/api/v1/finance/subscriptions", 400, label="API rejects a prohibited discount",
              json_body={"client_id": uk_client.id, "student_id": uk_student.id, "price": 40, "currency": "GBP",
                         "discount_pct": 45, "teacher_id": teacher.id if teacher else None})
    check(admin, "POST", "/api/v1/finance/expenses", 201, label="API create expense",
          json_body={"category": "office", "amount": 4200, "currency": "PKR", "vendor": "Stationery"})
    db.expire_all()
    webhook_inv = db.query(Invoice).filter(Invoice.status.in_(["sent", "overdue", "partial"])).order_by(Invoice.id.desc()).first()
    if webhook_inv:
        body = {"invoice_number": webhook_inv.invoice_number, "amount": float(webhook_inv.balance) or 10.0,
                "currency": webhook_inv.currency, "reference": "WH-TEST-0001", "method": "card", "gateway": "stripe"}
        check(admin, "POST", "/api/v1/finance/payment-webhook", 201, label="payment webhook", json_body=body)
        check(admin, "POST", "/api/v1/finance/payment-webhook", 201, label="payment webhook is idempotent",
              json_body=body)
    check(admin, "POST", "/api/v1/finance/currencies/EUR/rate", 200, label="API set rate",
          json_body={"rate": 305.0, "source": "api"})

    # ---------------------------------------------------------------- other finance roles
    finance_user = login("finance@oqc.local", "Finance@123")
    for url in ["/finance/subscriptions", "/finance/discounts", "/finance/discounts/scholarships", "/finance/invoices",
                "/finance/payments", "/finance/payments/reconciliation", "/finance/ledger", "/finance/accounts",
                "/finance/accounts/pnl", "/finance/accounts/close", "/finance/expenses", "/finance/currencies"]:
        check(finance_user, "GET", url, 200, label="hod_finance")

    billing_rep = login("billing@oqc.local", "Billing@123")
    for url in ["/finance/invoices", "/finance/payments", "/finance/ledger", "/finance/subscriptions"]:
        check(billing_rep, "GET", url, 200, label="billing rep view")
    check(billing_rep, "GET", "/finance/discounts", 403, label="billing rep cannot see the discount queue")
    if new_ceo:
        check(billing_rep, "POST", f"/finance/discounts/{new_ceo.id}/decide", 403,
              {"decision": "approve", "note": "x"}, label="billing rep cannot approve discounts")

    accountant = login("accountant@oqc.local", "Account@123")
    for url in ["/finance/accounts", "/finance/accounts/journal", "/finance/accounts/aging", "/finance/expenses",
                "/finance/currencies"]:
        check(accountant, "GET", url, 200, label="accountant")

    # ---------------------------------------------------------------- artefacts on disk
    global CHECKS
    inv_pdfs = list((Path(__file__).resolve().parent.parent / "storage" / "invoices").glob("*.pdf"))
    rct_pdfs = list((Path(__file__).resolve().parent.parent / "storage" / "receipts").glob("*.pdf"))
    CHECKS += 2
    if len(inv_pdfs) < 50:
        FAILURES.append(f"expected many invoice PDFs, found {len(inv_pdfs)}")
    if len(rct_pdfs) < 50:
        FAILURES.append(f"expected many receipt PDFs, found {len(rct_pdfs)}")

    # ---------------------------------------------------------------- background jobs
    from app.services import jobs_finance
    for job_id, fn, _ in jobs_finance.JOBS:
        CHECKS += 1
        s = SessionLocal()
        try:
            fn(s)
            s.commit()
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(f"job {job_id} failed: {exc}")
        finally:
            s.close()

    db.close()
    print(f"finance module: {CHECKS} checks, {len(FAILURES)} failure(s)")
    print(f"  invoice PDFs: {len(inv_pdfs)}, receipt PDFs: {len(rct_pdfs)}")
    for f in FAILURES[:60]:
        print("  FAIL:", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
