"""Smoke test for the Accounts area of the ERP: accounts heads, chart of accounts, tree view, Journal / Payment /
Receipt Vouchers and the seven reports (ledger, trial balance, income statement, balance sheet, payables,
account wise, approved advances).

Run:  $env:DATABASE_URL='sqlite:///./data/oqc_acc.db'; .venv/Scripts/python.exe tests/test_accounts_erp.py
The suite is re-runnable against the same database: everything it creates is new, nothing it asserts depends on
a count that only holds on a freshly seeded database.
ASCII output only (Windows console is cp1252).
"""
from __future__ import annotations

import base64
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_acc.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.finance import Account, Invoice, JournalEntry, JournalLine  # noqa: E402
from app.services import accounting  # noqa: E402

import logging  # noqa: E402

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("oqc.integrations").setLevel(logging.WARNING)
logging.getLogger("oqc").setLevel(logging.WARNING)

FAILURES: list[str] = []
CHECKS = 0
TODAY = date.today()
MONTH_START = TODAY.replace(day=1)
WIDE_START, WIDE_END = date(2000, 1, 1), TODAY + timedelta(days=365)


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


def check(client: TestClient, method: str, url: str, expect, data=None, label: str = ""):
    global CHECKS
    CHECKS += 1
    r = client.get(url) if method == "GET" else client.post(url, data=data or {})
    expected = expect if isinstance(expect, (list, tuple, set)) else [expect]
    if r.status_code not in expected:
        body = r.text[:300].replace("\n", " ")
        FAILURES.append(f"{method} {url} -> {r.status_code} (expected {expect}) {label} {body}")
    return r


def expect_flash(r, needle: str, label: str):
    global CHECKS
    CHECKS += 1
    msg = flash_of(r)
    if needle.lower() not in msg.lower():
        FAILURES.append(f"{label}: expected flash containing '{needle}', got '{msg}'")


def assert_true(cond: bool, label: str):
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(label)


def balances_of(db, account_id: int) -> float:
    mv = accounting.account_movements(db)
    m = mv.get(account_id, {"debit": 0.0, "credit": 0.0})
    return round(m["debit"] - m["credit"], 2)


def free_code(db, prefix: str) -> str:
    n = 1
    while db.query(Account).filter(Account.code == f"{prefix}{n}").first():
        n += 1
    return f"{prefix}{n}"


def main() -> int:
    global CHECKS
    db = SessionLocal()
    admin = login("admin@oqc.local", "Admin@12345")
    accountant = login("accountant@oqc.local", "Account@123")

    # ------------------------------------------------------------------ fixtures from the seeded database
    expense_account = db.query(Account).filter(Account.code == "5500").first()
    income_account = db.query(Account).filter(Account.code == "4000").first()
    bank_account = db.query(Account).filter(Account.code == "1010").first()
    cash_account = db.query(Account).filter(Account.code == "1000").first()
    head = db.query(Account).filter(Account.is_head.is_(True)).order_by(Account.code).first()
    posted_voucher = (db.query(JournalEntry).filter(JournalEntry.voucher_number.isnot(None),
                                                    JournalEntry.status == "posted")
                      .order_by(JournalEntry.id).first())
    draft_voucher = (db.query(JournalEntry).filter(JournalEntry.voucher_number.isnot(None),
                                                   JournalEntry.status == "draft").first())
    for name, obj in [("account 5500", expense_account), ("account 4000", income_account),
                      ("account 1010", bank_account), ("an accounts head", head),
                      ("a posted voucher", posted_voucher)]:
        if obj is None:
            FAILURES.append(f"seed fixture missing: {name}")
    if expense_account is None or posted_voucher is None or head is None:
        print("FATAL: accounts seed data missing; run seed.py --reset first")
        return 1

    # ------------------------------------------------------------------ GET every page
    pages = [
        "/finance/accounts", "/finance/accounts?account_type=expense", "/finance/accounts?postable=yes",
        "/finance/accounts?postable=no", f"/finance/accounts?head_id={head.id}", "/finance/accounts?q=bank",
        f"/finance/accounts/{expense_account.id}",
        "/finance/accounts/heads", "/finance/accounts/heads?account_type=asset",
        "/finance/accounts/heads?status=active", "/finance/accounts/heads?q=assets",
        "/finance/accounts/tree", f"/finance/accounts/tree?as_of={TODAY}", "/finance/accounts/tree?print_view=1",
        "/finance/accounts/vouchers", "/finance/accounts/vouchers?type=journal",
        "/finance/accounts/vouchers?type=payment", "/finance/accounts/vouchers?type=receipt",
        "/finance/accounts/vouchers?status=draft", "/finance/accounts/vouchers?status=posted",
        "/finance/accounts/vouchers?status=cancelled", "/finance/accounts/vouchers?q=PV-",
        f"/finance/accounts/vouchers?date_from={MONTH_START}&date_to={TODAY}",
        f"/finance/accounts/vouchers?account_id={bank_account.id}", "/finance/accounts/vouchers?party=C-",
        "/finance/accounts/vouchers?currency=PKR",
        "/finance/accounts/vouchers/new?type=journal", "/finance/accounts/vouchers/new?type=payment",
        "/finance/accounts/vouchers/new?type=receipt", "/finance/accounts/vouchers/new?type=nonsense",
        f"/finance/accounts/vouchers/{posted_voucher.id}",
        f"/finance/accounts/vouchers/{posted_voucher.id}?print_view=1",
        # reports
        "/finance/accounts/reports/ledger", f"/finance/accounts/reports/ledger?account_id={bank_account.id}",
        f"/finance/accounts/reports/ledger?account_id={bank_account.id}&date_from=2020-01-01&date_to={TODAY}",
        f"/finance/accounts/reports/ledger?account_id={bank_account.id}&format=csv",
        f"/finance/accounts/reports/ledger?account_id={bank_account.id}&print_view=1",
        "/finance/accounts/reports/trial-balance",
        f"/finance/accounts/reports/trial-balance?date_from=2020-01-01&date_to={TODAY}",
        "/finance/accounts/reports/trial-balance?include_empty=1",
        "/finance/accounts/reports/trial-balance?format=csv", "/finance/accounts/reports/trial-balance?print_view=1",
        "/finance/accounts/reports/income-statement",
        f"/finance/accounts/reports/income-statement?date_from={MONTH_START}&date_to={TODAY}",
        "/finance/accounts/reports/income-statement?format=csv",
        "/finance/accounts/reports/income-statement?print_view=1",
        "/finance/accounts/reports/balance-sheet", f"/finance/accounts/reports/balance-sheet?date_to={TODAY}",
        "/finance/accounts/reports/balance-sheet?format=csv", "/finance/accounts/reports/balance-sheet?print_view=1",
        "/finance/accounts/reports/payables", "/finance/accounts/reports/payables?format=csv",
        "/finance/accounts/reports/payables?print_view=1",
        "/finance/accounts/reports/account-wise", "/finance/accounts/reports/account-wise?account_type=expense",
        f"/finance/accounts/reports/account-wise?head_id={head.id}",
        "/finance/accounts/reports/account-wise?format=csv", "/finance/accounts/reports/account-wise?print_view=1",
        "/finance/accounts/reports/approved-advances",
        "/finance/accounts/reports/approved-advances?date_from=2020-01-01&date_to=" + str(TODAY),
        "/finance/accounts/reports/approved-advances?status=approved",
        "/finance/accounts/reports/approved-advances?format=csv",
        "/finance/accounts/reports/approved-advances?print_view=1",
        # the pages that were already there must keep working
        "/finance/accounts/journal", "/finance/accounts/pnl", "/finance/accounts/cash-flow",
        "/finance/accounts/aging", "/finance/accounts/payables", "/finance/accounts/budget",
        "/finance/accounts/forecast", "/finance/accounts/close", "/finance/accounts/consolidated",
    ]
    for url in pages:
        check(admin, "GET", url, 200, label="admin page")
    if draft_voucher is not None:
        check(admin, "GET", f"/finance/accounts/vouchers/{draft_voucher.id}", 200, label="draft voucher")

    for url in ["/finance/accounts", "/finance/accounts/heads", "/finance/accounts/tree",
                "/finance/accounts/vouchers", "/finance/accounts/vouchers/new?type=payment",
                "/finance/accounts/reports/trial-balance", "/finance/accounts/reports/balance-sheet",
                "/finance/accounts/reports/approved-advances"]:
        check(accountant, "GET", url, 200, label="accountant")

    # CSV really is a CSV
    r = check(admin, "GET", "/finance/accounts/reports/trial-balance?format=csv", 200, label="tb csv")
    assert_true("text/csv" in r.headers.get("content-type", ""), "trial balance csv is not text/csv")
    assert_true("Trial Balance Report" in r.text, "trial balance csv has no header row")

    # ------------------------------------------------------------------ Accounts Heads: create, edit, toggle
    code = free_code(db, "T9")
    r = check(admin, "POST", "/finance/accounts/heads/new", 303,
              {"code": code, "name": "Test Head", "account_type": "expense", "sort_no": "999"}, "create head")
    expect_flash(r, "added", "create head")
    db.expire_all()
    new_head = db.query(Account).filter(Account.code == code).first()
    assert_true(new_head is not None and new_head.is_head and not new_head.is_postable,
                "created head is not flagged as a non-postable head")
    if new_head is not None:
        r = check(admin, "POST", f"/finance/accounts/heads/{new_head.id}/edit", 303,
                  {"name": "Test Head Renamed", "account_type": "expense", "sort_no": "998"}, "edit head")
        db.expire_all()
        assert_true(db.query(Account).get(new_head.id).name == "Test Head Renamed", "head rename did not stick")
        check(admin, "POST", f"/finance/accounts/heads/{new_head.id}/toggle", 303, {}, "toggle head")
        db.expire_all()
        assert_true(db.query(Account).get(new_head.id).is_active is False, "head toggle did not deactivate")
        check(admin, "POST", f"/finance/accounts/heads/{new_head.id}/toggle", 303, {}, "toggle head back")

    # an account that already carries postings cannot become a head
    assert_true(accounting.account_has_postings(db, bank_account), "1010 Bank should carry postings in the seed")
    r = check(admin, "POST", f"/finance/accounts/{bank_account.id}/mark-head", 303,
              {"is_head": "1", "back": "/finance/accounts", "rationale": "test"}, "mark posted account as head")
    expect_flash(r, "cannot become a head", "mark posted account as head")
    db.expire_all()
    assert_true(db.query(Account).get(bank_account.id).is_head is False,
                "an account with postings was allowed to become a head")

    # ------------------------------------------------------------------ Journal Voucher: refusals then success
    def voucher_count() -> int:
        return db.query(JournalEntry).filter(JournalEntry.voucher_number.isnot(None)).count()

    db.expire_all()
    before_count = voucher_count()
    r = check(admin, "POST", "/finance/accounts/vouchers/new", 303, {
        "type": "journal", "action": "post", "entry_date": TODAY.isoformat(), "currency": "PKR",
        "exchange_rate": "1", "description": "Unbalanced test journal",
        "account_id": [str(expense_account.id), str(bank_account.id)],
        "debit": ["5000", ""], "credit": ["", "4000"], "memo": ["", ""],
    }, "unbalanced journal voucher")
    expect_flash(r, "not balanced", "unbalanced journal voucher")
    db.expire_all()
    assert_true(voucher_count() == before_count, "an unbalanced voucher was saved anyway")

    r = check(admin, "POST", "/finance/accounts/vouchers/new", 303, {
        "type": "journal", "action": "post", "entry_date": TODAY.isoformat(), "currency": "PKR",
        "exchange_rate": "1", "description": "Single line test journal",
        "account_id": [str(expense_account.id), str(bank_account.id)],
        "debit": ["5000", ""], "credit": ["", ""], "memo": ["", ""],
    }, "single line journal voucher")
    expect_flash(r, "at least two lines", "single line journal voucher")
    db.expire_all()
    assert_true(voucher_count() == before_count, "a one-line voucher was saved anyway")

    exp_before = balances_of(db, expense_account.id)
    bank_before = balances_of(db, bank_account.id)
    r = check(admin, "POST", "/finance/accounts/vouchers/new", 303, {
        "type": "journal", "action": "post", "entry_date": TODAY.isoformat(), "currency": "PKR",
        "exchange_rate": "1", "description": "Balanced test journal voucher",
        "account_id": [str(expense_account.id), str(bank_account.id)],
        "debit": ["5000", ""], "credit": ["", "5000"], "memo": ["office supplies", "paid from bank"],
        "rationale": "Regression test",
    }, "balanced journal voucher")
    expect_flash(r, "posted", "balanced journal voucher")
    db.expire_all()
    jv = (db.query(JournalEntry).filter(JournalEntry.description == "Balanced test journal voucher")
          .order_by(JournalEntry.id.desc()).first())
    assert_true(jv is not None and jv.voucher_type == "journal" and (jv.voucher_number or "").startswith("JV-"),
                "the journal voucher did not get a JV- number")
    if jv is not None:
        assert_true(jv.status == "posted" and jv.posted_at is not None and jv.posted_by_id is not None,
                    "the posted journal voucher was not stamped with who posted it and when")
        assert_true(len(jv.lines) == 2, "the journal voucher did not keep both lines")
        assert_true(round(balances_of(db, expense_account.id) - exp_before, 2) == 5000.0,
                    "the debited account did not move by 5000")
        assert_true(round(balances_of(db, bank_account.id) - bank_before, 2) == -5000.0,
                    "the credited account did not move by -5000")
        check(admin, "GET", f"/finance/accounts/vouchers/{jv.id}", 200, label="new journal voucher detail")

    # ------------------------------------------------------------------ Payment Voucher moves both accounts
    exp_before = balances_of(db, expense_account.id)
    bank_before = balances_of(db, bank_account.id)
    r = check(admin, "POST", "/finance/accounts/vouchers/new", 303, {
        "type": "payment", "action": "post", "entry_date": TODAY.isoformat(), "currency": "PKR",
        "exchange_rate": "1", "description": "Test payment voucher to a vendor",
        "party_type": "vendor", "party_name": "Regression Vendor Ltd", "payment_mode": "Bank",
        "reference_no": "TEST/PV/1", "amount": "7500", "account_id": str(expense_account.id),
        "memo": "stationery", "rationale": "Regression test",
    }, "payment voucher")
    expect_flash(r, "posted", "payment voucher")
    db.expire_all()
    pv = (db.query(JournalEntry).filter(JournalEntry.description == "Test payment voucher to a vendor")
          .order_by(JournalEntry.id.desc()).first())
    assert_true(pv is not None and (pv.voucher_number or "").startswith("PV-"),
                "the payment voucher did not get a PV- number")
    if pv is not None:
        assert_true(pv.party_name == "Regression Vendor Ltd" and pv.payment_mode == "Bank"
                    and pv.reference_no == "TEST/PV/1",
                    "the payment voucher did not record the party, mode and reference")
        assert_true(round(balances_of(db, expense_account.id) - exp_before, 2) == 7500.0,
                    "the payment voucher did not debit the expense account")
        assert_true(round(balances_of(db, bank_account.id) - bank_before, 2) == -7500.0,
                    "the payment voucher did not credit the bank account")

    # cash mode settles through 1000 Cash
    cash_before = balances_of(db, cash_account.id)
    check(admin, "POST", "/finance/accounts/vouchers/new", 303, {
        "type": "payment", "action": "post", "entry_date": TODAY.isoformat(), "currency": "PKR",
        "exchange_rate": "1", "description": "Test cash payment voucher", "party_type": "other",
        "party_name": "Petty cash", "payment_mode": "Cash", "amount": "1200",
        "account_id": str(expense_account.id),
    }, "cash payment voucher")
    db.expire_all()
    assert_true(round(balances_of(db, cash_account.id) - cash_before, 2) == -1200.0,
                "a Cash payment voucher did not credit 1000 Cash")

    # ------------------------------------------------------------------ Receipt Voucher: draft then post
    r = check(admin, "POST", "/finance/accounts/vouchers/new", 303, {
        "type": "receipt", "action": "draft", "entry_date": TODAY.isoformat(), "currency": "PKR",
        "exchange_rate": "1", "description": "Test receipt voucher held as draft", "party_type": "other",
        "party_name": "Walk-in family", "payment_mode": "Bank", "amount": "9000",
        "account_id": str(income_account.id),
    }, "draft receipt voucher")
    expect_flash(r, "draft", "draft receipt voucher")
    db.expire_all()
    rv = (db.query(JournalEntry).filter(JournalEntry.description == "Test receipt voucher held as draft")
          .order_by(JournalEntry.id.desc()).first())
    assert_true(rv is not None and (rv.voucher_number or "").startswith("RV-") and rv.status == "draft",
                "the receipt voucher was not saved as a draft with an RV- number")
    if rv is not None:
        income_before = balances_of(db, income_account.id)
        assert_true(rv.posted_at is None, "a draft voucher was stamped as posted")
        r = check(admin, "POST", f"/finance/accounts/vouchers/{rv.id}/post", 303,
                  {"rationale": "Regression test"}, "post the draft receipt voucher")
        expect_flash(r, "posted", "post the draft receipt voucher")
        db.expire_all()
        rv = db.query(JournalEntry).get(rv.id)
        assert_true(rv.status == "posted" and rv.posted_at is not None and rv.posted_by_id is not None,
                    "posting the draft did not stamp posted_by / posted_at")
        assert_true(round(balances_of(db, income_account.id) - income_before, 2) == -9000.0,
                    "posting the draft receipt did not credit the income account")
        r = check(admin, "POST", f"/finance/accounts/vouchers/{rv.id}/post", 303, {}, "re-post a posted voucher")
        expect_flash(r, "cannot be posted", "re-post a posted voucher")

    # ------------------------------------------------------------------ a receipt applied to an invoice
    inv = (db.query(Invoice).filter(Invoice.status.in_(["sent", "pending", "confirmed", "partial", "overdue"]))
           .order_by(Invoice.id).first())
    if inv is not None:
        paid_before = float(inv.paid_amount or 0)
        amount = min(round(float(inv.total) - paid_before, 2), 25.0)
        if amount > 0:
            r = check(admin, "POST", "/finance/accounts/vouchers/new", 303, {
                "type": "receipt", "action": "post", "entry_date": TODAY.isoformat(), "currency": inv.currency,
                "exchange_rate": "1", "description": "Test receipt applied to an invoice",
                "party_type": "client", "client_id": str(inv.client_id), "payment_mode": "Bank",
                "amount": f"{amount:.2f}", "account_id": str(income_account.id),
                "invoice_id": str(inv.id), "reference_no": "TEST/RV/INV",
            }, "receipt applied to an invoice")
            expect_flash(r, "applied to", "receipt applied to an invoice")
            db.expire_all()
            inv = db.query(Invoice).get(inv.id)
            assert_true(round(float(inv.paid_amount or 0) - paid_before, 2) == round(amount, 2),
                        "applying a receipt voucher to an invoice did not increase the amount paid")
            applied = (db.query(JournalEntry).filter(JournalEntry.voucher_type == "receipt",
                                                     JournalEntry.reference_type == "payment")
                       .order_by(JournalEntry.id.desc()).first())
            assert_true(applied is not None and (applied.voucher_number or "").startswith("RV-"),
                        "the billing journal was not adopted as a receipt voucher")

    # ------------------------------------------------------------------ cancelling a posted voucher
    tb_before = accounting.trial_balance(db, WIDE_START, WIDE_END, include_empty=True)
    assert_true(tb_before["balanced"], "the seeded trial balance does not balance")
    if pv is not None:
        r = check(admin, "POST", f"/finance/accounts/vouchers/{pv.id}/cancel", 303, {}, "cancel without a reason")
        expect_flash(r, "reason is required", "cancel without a reason")
        r = check(admin, "POST", f"/finance/accounts/vouchers/{pv.id}/cancel", 303,
                  {"reason": "Posted against the wrong vendor during the regression test."}, "cancel a voucher")
        expect_flash(r, "cancelled", "cancel a voucher")
        db.expire_all()
        pv = db.query(JournalEntry).get(pv.id)
        assert_true(pv.status == "cancelled" and pv.cancel_reason, "the cancelled voucher kept no reason")
        reversal = (db.query(JournalEntry).filter(JournalEntry.reference_type == "reversal",
                                                  JournalEntry.reference_id == pv.id).first())
        assert_true(reversal is not None, "cancelling a posted voucher did not write a reversal")
        if reversal is not None:
            assert_true(reversal.status == "posted" and (reversal.voucher_number or "").startswith("PV-"),
                        "the reversal is not a posted voucher of the same type")
            assert_true(round(float(reversal.total), 2) == round(float(pv.total), 2),
                        "the reversal total does not match the original")
            rev_lines = {(l.account_id, round(float(l.debit), 2), round(float(l.credit), 2)) for l in reversal.lines}
            orig_lines = {(l.account_id, round(float(l.credit), 2), round(float(l.debit), 2)) for l in pv.lines}
            assert_true(rev_lines == orig_lines, "the reversal is not the mirror image of the original")
            check(admin, "GET", f"/finance/accounts/vouchers/{reversal.id}", 200, label="reversal detail")
        # cancelling a posted voucher must leave the ledger balanced and the accounts back where they were
        db.expire_all()
        tb_after = accounting.trial_balance(db, WIDE_START, WIDE_END, include_empty=True)
        assert_true(tb_after["balanced"],
                    f"the trial balance stopped balancing after a cancellation (difference {tb_after['difference']})")
        r = check(admin, "POST", f"/finance/accounts/vouchers/{pv.id}/cancel", 303,
                  {"reason": "again"}, "cancel a cancelled voucher")
        expect_flash(r, "already cancelled", "cancel a cancelled voucher")

    # cancelling a draft writes no reversal
    check(admin, "POST", "/finance/accounts/vouchers/new", 303, {
        "type": "journal", "action": "draft", "entry_date": TODAY.isoformat(), "currency": "PKR",
        "exchange_rate": "1", "description": "Draft journal voucher to cancel",
        "account_id": [str(expense_account.id), str(bank_account.id)],
        "debit": ["100", ""], "credit": ["", "100"], "memo": ["", ""],
    }, "draft journal voucher")
    db.expire_all()
    dv = (db.query(JournalEntry).filter(JournalEntry.description == "Draft journal voucher to cancel")
          .order_by(JournalEntry.id.desc()).first())
    if dv is not None:
        check(admin, "POST", f"/finance/accounts/vouchers/{dv.id}/cancel", 303,
              {"reason": "Raised in error"}, "cancel a draft voucher")
        db.expire_all()
        dv = db.query(JournalEntry).get(dv.id)
        assert_true(dv.status == "cancelled", "the draft voucher was not cancelled")
        assert_true(db.query(JournalEntry).filter(JournalEntry.reference_type == "reversal",
                                                  JournalEntry.reference_id == dv.id).first() is None,
                    "cancelling a draft wrote a reversal it should not have")

    # ------------------------------------------------------------------ the statements themselves
    db.expire_all()
    tb = accounting.trial_balance(db, WIDE_START, WIDE_END, include_empty=True)
    assert_true(tb["balanced"], f"trial balance out by {tb['difference']} on the seeded data")
    assert_true(round(tb["total_debit"], 2) == round(tb["total_credit"], 2),
                "trial balance debit and credit totals differ")
    assert_true(tb["total_debit"] > 0, "the trial balance has no movement at all")
    month_tb = accounting.trial_balance(db, MONTH_START, TODAY)
    assert_true(month_tb["balanced"], "the current month trial balance does not balance")

    bs = accounting.balance_sheet(db, TODAY)
    assert_true(bs["balanced"],
                f"balance sheet out by {bs['difference']}: assets {bs['total_assets']} vs "
                f"liabilities + equity {bs['total_liabilities_equity']}")
    assert_true(round(bs["retained_earnings"], 2) == round(bs["income_total"] - bs["expense_total"], 2),
                "retained earnings are not income less expense")

    inc = accounting.income_statement(db, MONTH_START, TODAY)
    assert_true(round(inc["net"], 2) == round(inc["total_income"] - inc["total_expense"], 2),
                "the income statement net is not income less expense")
    assert_true(inc["prev_end"] < inc["start"], "the comparison period is not before the reported period")

    led = accounting.ledger_report(db, bank_account, date(2020, 1, 1), TODAY)
    running = led["opening"]
    ok = True
    for row in led["rows"]:
        running = round(running + row["debit"] - row["credit"], 2)
        if abs(running - row["balance"]) > 0.01:
            ok = False
            break
    assert_true(ok, "the ledger running balance does not add up")
    assert_true(abs(led["closing"] - round(led["opening"] + led["total_debit"] - led["total_credit"], 2)) < 0.01,
                "the ledger closing balance is not opening + debits - credits")

    aw = accounting.account_wise_summary(db, MONTH_START, TODAY)
    assert_true(round(aw["total_debit"], 2) == round(aw["total_credit"], 2),
                "the account wise summary debits and credits differ")

    pay = accounting.payables_summary(db, TODAY)
    assert_true(round(sum(v["total"] for v in pay["vendors"]), 2) == round(pay["total"], 2),
                "the payables vendor totals do not add up to the grand total")
    assert_true(round(sum(pay["totals"][k] for k, _ in pay["buckets"]), 2) == round(pay["total"], 2),
                "the payables ageing buckets do not add up to the grand total")

    adv = accounting.approved_advances(db)
    assert_true(all(round(r["recovered"] + r["balance"], 2) == round(r["amount"], 2) for r in adv["rows"]),
                "an advance does not split into recovered plus balance")

    tree = accounting.accounts_tree(db, TODAY)
    assert_true(tree["count"] > 0 and tree["nodes"], "the accounts tree is empty")
    assert_true(any(n["children"] for n in tree["nodes"]), "the accounts tree has no hierarchy")
    for node in tree["flat"]:
        if node["children"]:
            expected = round(node["own"] + sum(c["total"] for c in node["children"]), 2)
            if abs(expected - node["total"]) > 0.01:
                FAILURES.append(f"tree roll-up wrong on {node['account'].code}")
                CHECKS += 1
                break

    # heads really are excluded from the postable set
    heads_with_postings = [h for h in accounting.head_accounts(db) if accounting.account_has_postings(db, h)]
    assert_true(not heads_with_postings,
                f"accounts heads carry postings: {[h.code for h in heads_with_postings]}")

    # ------------------------------------------------------------------ permissions
    parent = login("parent1@oqc.local", "Parent@123")
    check(parent, "GET", "/finance/accounts/vouchers", [302, 303, 403], label="a parent cannot see the vouchers")
    check(parent, "GET", "/finance/accounts/reports/trial-balance", [302, 303, 403],
          label="a parent cannot see the trial balance")

    db.close()
    print(f"accounts ERP: {CHECKS} checks, {len(FAILURES)} failure(s)")
    print(f"  trial balance: debit {tb['total_debit']:,.2f} vs credit {tb['total_credit']:,.2f} -> "
          f"{'BALANCED' if tb['balanced'] else 'OUT OF BALANCE'}")
    print(f"  balance sheet: assets {bs['total_assets']:,.2f} vs liabilities + equity "
          f"{bs['total_liabilities_equity']:,.2f} -> {'BALANCED' if bs['balanced'] else 'OUT OF BALANCE'}")
    for f in FAILURES[:60]:
        print("  FAIL:", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())


# --------------------------------------------------------------------------- pytest entry point
# The file above is written to run as a script so it can be pointed at a private database. Without this
# wrapper pytest collects the module, finds no test function and silently runs none of the checks.
def test_accounts_erp_suite():
    assert main() == 0, "see the printed FAIL lines above"
