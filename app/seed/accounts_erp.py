"""Accounts ERP-parity seed (docs/AUDIT_ACCOUNTS_CONFIG.md).

Builds the accounts heads so the tree is complete for assets, liabilities, equity, income and expenses, sets the
sort order and opening balances, posts the opening-balance voucher, then writes roughly 120 Journal, Payment and
Receipt Vouchers over the last six months - salaries, rent, internet, marketing and staff advances on the payment
side, family receipts on the receipt side, accruals, depreciation and prepaid amortisation as journals. Most are
posted, a few are left in draft and two are cancelled, each neutralised by a contra voucher.

Idempotent: the opening voucher is the marker, so a second run only tops up the chart structure.
The run finishes by asserting that the trial balance balances.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core.utils import month_bounds, month_key
from app.models.core import User
from app.models.erp import BeneficiaryAccount
from app.models.finance import Account, Currency, FinancialPeriod, JournalEntry
from app.models.people import Client
from app.services import accounting

SEED = 20260914

# code, name, type, parent code, sort no
HEADS = [
    ("1", "Assets", "asset", None, 100),
    ("11", "Current Assets", "asset", "1", 110),
    ("15", "Fixed Assets", "asset", "1", 150),
    ("2", "Liabilities", "liability", None, 200),
    ("3", "Equity", "equity", None, 300),
    ("4", "Income", "income", None, 400),
    ("5", "Expenses", "expense", None, 500),
    ("50", "Payroll Expenses", "expense", "5", 510),
    ("52", "Operating Expenses", "expense", "5", 520),
]

# code, name, type, parent head code, sort no
EXTRA_ACCOUNTS = [
    ("1200", "Prepaid Expenses", "asset", "11", 115),
    ("1300", "Staff Advances", "asset", "11", 116),
    ("1500", "Office Equipment", "asset", "15", 151),
    ("1590", "Accumulated Depreciation", "asset", "15", 152),
    ("2300", "Accrued Expenses", "liability", "2", 230),
    ("2400", "Tax Payable", "liability", "2", 240),
    ("3100", "Retained Earnings", "equity", "3", 310),
    ("4200", "Registration & Admission Fees", "income", "4", 420),
    ("5700", "Office Rent", "expense", "52", 570),
    ("5800", "Depreciation", "expense", "52", 580),
]

# existing chart account -> (head code, sort no)
PARENTS = {
    "1000": ("11", 111), "1010": ("11", 112), "1100": ("11", 113),
    "2000": ("2", 210), "2100": ("2", 220), "2200": ("2", 225),
    "3000": ("3", 305),
    "4000": ("4", 410), "4100": ("4", 415),
    "5000": ("50", 511), "5100": ("50", 512),
    "5200": ("52", 521), "5300": ("52", 530), "5400": ("52", 540), "5500": ("52", 550),
    "5600": ("52", 560), "5900": ("52", 590),
}

# Opening balances in the base currency, in the direction the account naturally carries. They are sized so the
# college still holds cash after six months of vouchers, payroll and expenses.
# Assets 6,930,000 = accumulated depreciation 250,000 + liabilities 750,000 + equity 5,930,000.
OPENING = {
    "1000": 400000, "1010": 4500000, "1100": 600000, "1200": 180000, "1500": 1250000, "1590": -250000,
    "2000": 350000, "2100": 300000, "2200": 100000, "3000": 5930000,
}

MARKETING_VENDORS = ["Meta Platforms Ireland", "Google Ads", "TikTok Ads UK"]
UTILITY_VENDORS = ["PTCL Business Fibre", "K-Electric", "Nayatel"]
LANDLORD = "Al-Falah Properties (Gulshan office)"


def _months(n: int = 6) -> list[tuple[date, date]]:
    today = date.today()
    out = []
    for i in range(n - 1, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        out.append(month_bounds(f"{y:04d}-{m:02d}"))
    return out


def _user(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


def _account(db: Session, code: str) -> Account | None:
    return db.query(Account).filter(Account.code == code).first()


def _ensure_structure(db: Session) -> None:
    """Create the heads, the missing accounts, and set every account's head, sort order and opening balance."""
    for code, name, typ, parent_code, sort_no in HEADS:
        head = _account(db, code)
        if head is None:
            head = Account(code=code, name=name, account_type=typ, is_active=True)
            db.add(head)
        head.name = name
        head.account_type = typ
        head.is_head = True
        head.is_postable = False
        head.sort_no = sort_no
        head.opening_balance = 0
        head.description = head.description or f"{name} head"
        db.flush()
    for code, _, _, parent_code, _ in HEADS:
        if parent_code:
            head, parent = _account(db, code), _account(db, parent_code)
            if head is not None and parent is not None:
                head.parent_id = parent.id
    db.flush()

    for code, name, typ, parent_code, sort_no in EXTRA_ACCOUNTS:
        acct = _account(db, code)
        if acct is None:
            acct = Account(code=code, name=name, account_type=typ, is_active=True)
            db.add(acct)
            db.flush()
        acct.name = name
        acct.account_type = typ
        acct.is_head = False
        acct.is_postable = True
        acct.sort_no = sort_no
        parent = _account(db, parent_code)
        acct.parent_id = parent.id if parent else None
    db.flush()

    for code, (parent_code, sort_no) in PARENTS.items():
        acct = _account(db, code)
        parent = _account(db, parent_code)
        if acct is None or parent is None:
            continue
        acct.parent_id = parent.id
        acct.sort_no = sort_no
        acct.is_head = False
        acct.is_postable = True
    for code, amount in OPENING.items():
        acct = _account(db, code)
        if acct is not None:
            acct.opening_balance = amount
    db.flush()


def _opening_voucher(db: Session, user: User, on: date) -> JournalEntry | None:
    """One Journal Voucher carrying the opening balances into the ledger."""
    existing = (db.query(JournalEntry)
                .filter(JournalEntry.voucher_number.isnot(None), JournalEntry.reference_type == "opening_balance")
                .first())
    if existing:
        return existing
    lines = []
    for code, amount in OPENING.items():
        acct = _account(db, code)
        if acct is None or not amount:
            continue
        natural_debit = acct.account_type in ("asset", "expense")
        if (natural_debit and amount > 0) or (not natural_debit and amount < 0):
            lines.append((acct.id, abs(amount), 0, "Opening balance"))
        else:
            lines.append((acct.id, 0, abs(amount), "Opening balance"))
    return accounting.create_voucher(db, "journal", on, "Opening balances brought forward", lines, user=user,
                                     status="posted", reference_type="opening_balance")


def _beneficiaries(db: Session) -> dict:
    rows = db.query(BeneficiaryAccount).filter(BeneficiaryAccount.status == "active").all()
    bank = next((b for b in rows if "bank" in (b.payment_mode or "").lower()), None)
    cash = next((b for b in rows if "cash" in (b.payment_mode or "").lower()), None)
    gateway = next((b for b in rows if "gateway" in (b.payment_mode or "").lower()), None)
    return {"bank": bank, "cash": cash, "gateway": gateway or bank}


def _client_label(c: Client) -> str:
    return f"{c.client_code} {c.full_name}"


def _day(rnd: random.Random, start: date, end: date, low: int = 1, high: int = 27) -> date:
    cap = min(end, date.today())
    d = start + timedelta(days=rnd.randint(low, high) - 1)
    return min(d, cap) if cap >= start else start


def _seed_vouchers(db: Session, rnd: random.Random, user: User, clients: list) -> dict:
    ben = _beneficiaries(db)
    bank, cash, gateway = ben["bank"], ben["cash"], ben["gateway"]
    gbp = db.query(Currency).filter(Currency.code == "GBP").first()
    gbp_rate = float(gbp.rate_to_base) if gbp else 1.0
    counts = {"payment": 0, "receipt": 0, "journal": 0, "draft": 0, "cancelled": 0, "reversal": 0}
    posted_for_cancel: list[JournalEntry] = []

    def acc(code: str) -> int:
        a = _account(db, code)
        return a.id if a else None

    for idx, (start, end) in enumerate(_months(6)):
        label = start.strftime("%B %Y")
        last_month = idx == 5

        # ------------------------------------------------------------------ payment vouchers
        payments = [
            ("5000", rnd.randint(780000, 960000), "other", "Teaching faculty (bulk payroll)", bank,
             f"Teacher salaries for {label}"),
            ("5100", rnd.randint(430000, 560000), "other", "Administrative staff (bulk payroll)", bank,
             f"Staff salaries for {label}"),
            ("5700", 120000, "vendor", LANDLORD, bank, f"Office rent for {label}"),
            ("5400", rnd.randint(38000, 52000), "vendor", rnd.choice(UTILITY_VENDORS), bank,
             f"Internet and utilities for {label}"),
            ("5200", rnd.randint(58000, 92000), "vendor", MARKETING_VENDORS[0], gateway,
             f"Marketing spend for {label}"),
            ("5200", rnd.randint(26000, 48000), "vendor", rnd.choice(MARKETING_VENDORS[1:]), gateway,
             f"Campaign spend for {label}"),
            ("1300", rnd.choice([25000, 30000, 40000, 50000]), "other", "Staff salary advance", cash or bank,
             f"Salary advance disbursed in {label}"),
        ]
        for i, (code, amount, ptype, pname, beneficiary, narration) in enumerate(payments):
            status = "draft" if (last_month and i == 5) else "posted"
            settle = accounting.beneficiary_ledger_account(
                db, beneficiary, (beneficiary.payment_mode if beneficiary else "Bank"))
            v = accounting.create_voucher(
                db, "payment", _day(rnd, start, end, 2, 26), narration,
                [(acc(code), amount, 0, narration), (settle.id, 0, amount, "Funds released")],
                user=user, status=status, party_type=ptype, party_name=pname,
                payment_mode=(beneficiary.payment_mode if beneficiary else "Bank"),
                beneficiary_account_id=beneficiary.id if beneficiary else None,
                reference_no=f"PAY/{start.strftime('%y%m')}/{i + 1:03d}")
            counts["payment"] += 1
            if status == "draft":
                counts["draft"] += 1
            elif idx == 1 and i == 3:
                posted_for_cancel.append(v)

        # ------------------------------------------------------------------ receipt vouchers
        for i in range(9):
            client = rnd.choice(clients) if clients else None
            if i == 7:
                code, narration = "4200", f"Registration and admission fee received in {label}"
                amount = rnd.choice([15000, 20000, 25000])
            elif i == 8:
                code, narration = "1100", f"Outstanding tuition receivable settled in {label}"
                amount = rnd.randint(30000, 55000)
            else:
                code, narration = "4000", f"Tuition fee received in {label}"
                amount = rnd.randint(55000, 175000)
            currency, rate = "PKR", 1.0
            entered = amount
            if i == 2 and gbp_rate > 1:
                currency, rate = "GBP", gbp_rate
                entered = round(amount / gbp_rate, 2)
            beneficiary = gateway if i % 3 else bank
            settle = accounting.beneficiary_ledger_account(
                db, beneficiary, (beneficiary.payment_mode if beneficiary else "Bank"))
            status = "draft" if (last_month and i == 8) else "posted"
            v = accounting.create_voucher(
                db, "receipt", _day(rnd, start, end, 1, 27), narration,
                [(settle.id, entered, 0, "Funds received"), (acc(code), 0, entered, narration)],
                user=user, status=status, currency=currency, exchange_rate=rate,
                party_type="client" if client else "other",
                party_id=client.id if client else None,
                party_name=_client_label(client) if client else "Walk-in",
                payment_mode=(beneficiary.payment_mode if beneficiary else "Bank"),
                beneficiary_account_id=beneficiary.id if beneficiary else None,
                reference_no=f"RCP/{start.strftime('%y%m')}/{i + 1:03d}")
            counts["receipt"] += 1
            if status == "draft":
                counts["draft"] += 1
            elif idx == 3 and i == 4:
                posted_for_cancel.append(v)

        # ------------------------------------------------------------------ journal vouchers
        depreciation = 20833
        accrual = rnd.randint(35000, 60000)
        tax = rnd.randint(18000, 30000)
        journals = [
            ([("5800", depreciation, 0), ("1590", 0, depreciation)],
             f"Depreciation on office equipment for {label}"),
            ([("5400", accrual, 0), ("2300", 0, accrual)],
             f"Accrued utilities and internet not yet invoiced, {label}"),
            ([("5300", 30000, 0), ("1200", 0, 30000)],
             f"Prepaid software licences amortised for {label}"),
            ([("5900", tax, 0), ("2400", 0, tax)],
             f"Provision for taxes and statutory charges, {label}"),
        ]
        for i, (lines, narration) in enumerate(journals):
            status = "draft" if (last_month and i == 3) else "posted"
            accounting.create_voucher(
                db, "journal", _day(rnd, start, end, 24, 28), narration,
                [(acc(code), d, c, narration) for code, d, c in lines],
                user=user, status=status, reference_no=f"JRN/{start.strftime('%y%m')}/{i + 1:03d}")
            counts["journal"] += 1
            if status == "draft":
                counts["draft"] += 1
        db.flush()

    for v in posted_for_cancel[:2]:
        reversal = accounting.cancel_voucher(db, v, user, "Raised against the wrong account during the month-end review.")
        counts["cancelled"] += 1
        if reversal is not None:
            counts["reversal"] += 1
    db.flush()
    return counts


def _reopen(db: Session, periods: list[str], user: User) -> list[str]:
    reopened = []
    for period in periods:
        if accounting.period_is_closed(db, period):
            accounting.reopen_period(db, period, user, "Historical vouchers loaded during the accounts data build.")
            reopened.append(period)
    db.flush()
    return reopened


def _reclose(db: Session, periods: list[str], user: User) -> None:
    for period in periods:
        fp = db.query(FinancialPeriod).filter(FinancialPeriod.period == period).first()
        if fp and fp.status == "closed":
            continue
        try:
            accounting.close_period(db, period, user, "Month-end close re-run after the voucher history was loaded.")
        except ValueError:
            continue
    db.flush()


def run(db: Session) -> None:
    rnd = random.Random(SEED)
    admin = _user(db, "admin@oqc.local")
    if not admin:
        return
    accountant = _user(db, "accountant@oqc.local") or admin

    accounting.ensure_chart_of_accounts(db)
    _ensure_structure(db)
    db.commit()

    months = _months(6)
    already = (db.query(JournalEntry)
               .filter(JournalEntry.voucher_number.isnot(None),
                       JournalEntry.reference_type == "opening_balance").first())
    if already is None:
        periods = [month_key(s) for s, _ in months]
        reopened = _reopen(db, periods, admin)
        opening_date = months[0][0] - timedelta(days=1)
        _opening_voucher(db, admin, opening_date)
        clients = db.query(Client).order_by(Client.id).all()
        counts = _seed_vouchers(db, rnd, accountant, clients)
        db.flush()
        _reclose(db, reopened, admin)
        db.commit()
        print(f"    accounts_erp: {counts['payment']} payment, {counts['receipt']} receipt and "
              f"{counts['journal']} journal vouchers ({counts['draft']} draft, {counts['cancelled']} cancelled "
              f"with {counts['reversal']} reversal(s))")

    # ---------------------------------------------------------------- prove the books balance
    wide_start, wide_end = date(2000, 1, 1), date.today() + timedelta(days=365)
    tb = accounting.trial_balance(db, wide_start, wide_end, include_empty=True)
    bs = accounting.balance_sheet(db, wide_end)
    if not tb["balanced"]:
        raise RuntimeError(
            "accounts_erp seed FAILED: the trial balance does not balance - debits "
            f"{tb['total_debit']:,.2f} vs credits {tb['total_credit']:,.2f} "
            f"(difference {tb['difference']:,.2f}). No voucher may be posted unbalanced.")
    if not bs["balanced"]:
        raise RuntimeError(
            "accounts_erp seed FAILED: the balance sheet does not balance - assets "
            f"{bs['total_assets']:,.2f} vs liabilities plus equity {bs['total_liabilities_equity']:,.2f} "
            f"(difference {bs['difference']:,.2f}).")
    print(f"    accounts_erp: trial balance BALANCES (debit = credit = {tb['total_debit']:,.2f} "
          f"{tb['currency']}); balance sheet BALANCES (assets = liabilities + equity = "
          f"{bs['total_assets']:,.2f} {bs['currency']})")
