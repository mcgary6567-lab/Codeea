"""Accounting service (Module 19): chart of accounts, double-entry journal, P&L, cash flow, aging, budgets,
expenses, forecasting and financial close. All journal amounts are stored in the base currency (PKR)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.audit import log_action, snapshot
from app.core.utils import next_code, month_key, month_bounds
from app.models.core import User, Department
from app.models.finance import (Account, JournalEntry, JournalLine, Expense, Budget, FinancialPeriod, Invoice,
                                Subscription, Currency)

# code, name, type
CHART = [
    ("1000", "Cash", "asset"),
    ("1010", "Bank", "asset"),
    ("1100", "Accounts Receivable", "asset"),
    ("2000", "Accounts Payable", "liability"),
    ("2100", "Salaries Payable", "liability"),
    ("2200", "Client Credits", "liability"),
    ("3000", "Owner's Equity", "equity"),
    ("4000", "Tuition Revenue", "income"),
    ("4100", "Trial/Other Income", "income"),
    ("5000", "Teacher Salaries", "expense"),
    ("5100", "Staff Salaries", "expense"),
    ("5200", "Marketing", "expense"),
    ("5300", "Software & Subscriptions", "expense"),
    ("5400", "Internet & Utilities", "expense"),
    ("5500", "Office & Admin", "expense"),
    ("5600", "Bank Charges", "expense"),
    ("5900", "Other Expenses", "expense"),
]

ACCOUNT_TYPES = ["asset", "liability", "equity", "income", "expense"]

# expense category -> (label, account code)
EXPENSE_CATEGORIES = {
    "teacher_salaries": ("Teacher salaries", "5000"),
    "staff_salaries": ("Staff salaries", "5100"),
    "marketing": ("Marketing & ads", "5200"),
    "software": ("Software & subscriptions", "5300"),
    "internet": ("Internet & telecom", "5400"),
    "utilities": ("Utilities", "5400"),
    "office": ("Office & admin", "5500"),
    "bank_charges": ("Bank charges", "5600"),
    "other": ("Other", "5900"),
}

CASH_ACCOUNTS = ("1000", "1010")


# ----------------------------------------------------------------------------- helpers
def base_currency(db: Session) -> str:
    c = db.query(Currency).filter(Currency.is_base.is_(True)).first()
    return c.code if c else "PKR"


def ensure_chart_of_accounts(db: Session) -> dict[str, Account]:
    """Create the standard chart of accounts if missing. Returns {code: Account}."""
    existing = {a.code: a for a in db.query(Account).all()}
    for code, name, typ in CHART:
        if code not in existing:
            a = Account(code=code, name=name, account_type=typ, is_active=True)
            db.add(a)
            existing[code] = a
    db.flush()
    return existing


def get_account(db: Session, code: str) -> Account:
    a = db.query(Account).filter(Account.code == code).first()
    if not a:
        ensure_chart_of_accounts(db)
        a = db.query(Account).filter(Account.code == code).first()
    if not a:
        raise ValueError(f"Account {code} does not exist")
    return a


def account_for_category(db: Session, category: str) -> Account:
    code = EXPENSE_CATEGORIES.get(category, EXPENSE_CATEGORIES["other"])[1]
    return get_account(db, code)


def period_is_closed(db: Session, period: str) -> bool:
    fp = db.query(FinancialPeriod).filter(FinancialPeriod.period == period).first()
    return bool(fp and fp.status == "closed")


def get_period(db: Session, period: str) -> FinancialPeriod:
    fp = db.query(FinancialPeriod).filter(FinancialPeriod.period == period).first()
    if not fp:
        fp = FinancialPeriod(period=period, status="open")
        db.add(fp)
        db.flush()
    return fp


# ----------------------------------------------------------------------------- journal
def post_journal(db: Session, description: str, lines: list, reference_type: Optional[str] = None,
                 reference_id: Optional[int] = None, currency: Optional[str] = None, entry_date: Optional[date] = None,
                 user: Optional[User] = None, status: str = "posted") -> JournalEntry:
    """Post a balanced journal entry. ``lines`` = [(account_code_or_id, debit, credit), ...]."""
    entry_date = entry_date or date.today()
    if not lines:
        raise ValueError("A journal entry needs at least one line")
    total_debit = round(sum(float(l[1] or 0) for l in lines), 2)
    total_credit = round(sum(float(l[2] or 0) for l in lines), 2)
    if abs(total_debit - total_credit) > 0.01:
        raise ValueError(f"Journal entry is not balanced (debit {total_debit:.2f} vs credit {total_credit:.2f})")
    if total_debit <= 0:
        raise ValueError("Journal entry total must be greater than zero")
    period = month_key(entry_date)
    if period_is_closed(db, period):
        raise ValueError(f"Period {period} is closed; journal entries cannot be dated inside it")
    je = JournalEntry(entry_number=next_code(db, JournalEntry, "entry_number", "JE-", 6), entry_date=entry_date,
                      description=description[:250], reference_type=reference_type, reference_id=reference_id,
                      currency=currency or base_currency(db), total=total_debit, status=status,
                      created_by_id=user.id if user else None, period=period)
    db.add(je)
    db.flush()
    for acct, debit, credit in lines:
        account = get_account(db, acct) if isinstance(acct, str) else db.query(Account).get(int(acct))
        if account is None:
            raise ValueError(f"Unknown account {acct}")
        memo = None
        db.add(JournalLine(entry_id=je.id, account_id=account.id, debit=round(float(debit or 0), 2),
                           credit=round(float(credit or 0), 2), memo=memo))
    db.flush()
    return je


def reverse_journal(db: Session, entry: JournalEntry, user: Optional[User], rationale: str) -> JournalEntry:
    lines = [(l.account.code, float(l.credit or 0), float(l.debit or 0)) for l in entry.lines]
    rev = post_journal(db, f"Reversal of {entry.entry_number}: {entry.description}", lines, reference_type="reversal",
                       reference_id=entry.id, currency=entry.currency, user=user)
    entry.status = "reversed"
    log_action(db, user, "reverse", "accounts", entity=entry, description=f"Reversed {entry.entry_number} with {rev.entry_number}",
               rationale=rationale, consequential=True)
    return rev


def _line_rows(db: Session, start: date, end: date):
    return (db.query(Account.code, Account.name, Account.account_type,
                     func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
            .join(JournalLine, JournalLine.account_id == Account.id)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .filter(JournalEntry.status == "posted", JournalEntry.entry_date >= start, JournalEntry.entry_date <= end)
            .group_by(Account.code, Account.name, Account.account_type).order_by(Account.code).all())


# ----------------------------------------------------------------------------- reports
def profit_and_loss(db: Session, period_start: date, period_end: date) -> dict:
    income, expenses = [], []
    for code, name, typ, debit, credit in _line_rows(db, period_start, period_end):
        debit, credit = float(debit), float(credit)
        if typ == "income":
            amt = round(credit - debit, 2)
            if amt:
                income.append({"code": code, "name": name, "amount": amt})
        elif typ == "expense":
            amt = round(debit - credit, 2)
            if amt:
                expenses.append({"code": code, "name": name, "amount": amt})
    total_income = round(sum(r["amount"] for r in income), 2)
    total_expenses = round(sum(r["amount"] for r in expenses), 2)
    net = round(total_income - total_expenses, 2)
    return {"period_start": period_start, "period_end": period_end, "income": income, "expenses": expenses,
            "total_income": total_income, "total_expenses": total_expenses, "net": net,
            "margin_pct": round(100.0 * net / total_income, 1) if total_income else 0.0, "currency": base_currency(db)}


def balance_sheet_snapshot(db: Session, as_of: Optional[date] = None) -> dict:
    as_of = as_of or date.today()
    rows = _line_rows(db, date(2000, 1, 1), as_of)
    out = {"asset": [], "liability": [], "equity": []}
    for code, name, typ, debit, credit in rows:
        debit, credit = float(debit), float(credit)
        if typ in ("asset",):
            out["asset"].append({"code": code, "name": name, "amount": round(debit - credit, 2)})
        elif typ in ("liability", "equity"):
            out[typ].append({"code": code, "name": name, "amount": round(credit - debit, 2)})
    return out


def cash_flow(db: Session, months: int = 6) -> list[dict]:
    """Monthly cash inflow/outflow from movements on cash & bank accounts (base currency)."""
    today = date.today()
    out = []
    for i in range(months - 1, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            y -= 1
            m += 12
        period = f"{y:04d}-{m:02d}"
        start, end = month_bounds(period)
        row = (db.query(func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
               .join(JournalEntry, JournalEntry.id == JournalLine.entry_id).join(Account, Account.id == JournalLine.account_id)
               .filter(JournalEntry.status == "posted", JournalEntry.entry_date >= start, JournalEntry.entry_date <= end,
                       Account.code.in_(CASH_ACCOUNTS)).first())
        inflow, outflow = float(row[0]), float(row[1])
        out.append({"period": period, "label": start.strftime("%b %Y"), "inflow": round(inflow, 2), "outflow": round(outflow, 2),
                    "net": round(inflow - outflow, 2)})
    running = 0.0
    for r in out:
        running += r["net"]
        r["cumulative"] = round(running, 2)
    return out


def receivables_aging(db: Session) -> dict:
    """Bucket open invoice balances (base currency) by days past due."""
    from app.services.billing import get_rate
    today = date.today()
    buckets = {"current": {"label": "Not yet due", "amount": 0.0, "count": 0, "invoices": []},
               "1_30": {"label": "1-30 days", "amount": 0.0, "count": 0, "invoices": []},
               "31_60": {"label": "31-60 days", "amount": 0.0, "count": 0, "invoices": []},
               "60_plus": {"label": "60+ days", "amount": 0.0, "count": 0, "invoices": []}}
    rates: dict[str, float] = {}
    q = db.query(Invoice).filter(Invoice.status.in_(["sent", "partial", "overdue"]))
    for inv in q.all():
        bal = inv.balance
        if bal <= 0:
            continue
        rate = rates.setdefault(inv.currency, get_rate(db, inv.currency))
        days = (today - inv.due_date).days if inv.due_date else 0
        key = "current" if days <= 0 else ("1_30" if days <= 30 else ("31_60" if days <= 60 else "60_plus"))
        b = buckets[key]
        b["amount"] += bal * rate
        b["count"] += 1
        b["invoices"].append(inv)
    for b in buckets.values():
        b["amount"] = round(b["amount"], 2)
    total = round(sum(b["amount"] for b in buckets.values()), 2)
    return {"buckets": buckets, "total": total, "count": sum(b["count"] for b in buckets.values()), "currency": base_currency(db)}


def budget_vs_actual(db: Session, period: str) -> list[dict]:
    start, end = month_bounds(period)
    budgets = db.query(Budget).filter(Budget.period == period).order_by(Budget.category).all()
    actual_rows = (db.query(Expense.category, Expense.department_id, func.coalesce(func.sum(Expense.amount_in_base), 0))
                   .filter(Expense.status.in_(["approved", "paid"]), Expense.expense_date >= start, Expense.expense_date <= end)
                   .group_by(Expense.category, Expense.department_id).all())
    actuals = defaultdict(float)
    for cat, dept_id, amt in actual_rows:
        actuals[(cat, dept_id)] += float(amt)
        actuals[(cat, None)] += 0  # keep key
    out, seen = [], set()
    for b in budgets:
        key = (b.category, b.department_id)
        actual = actuals.get(key, 0.0)
        if b.department_id is None:  # department-agnostic budget: sum all departments for the category
            actual = sum(v for (c, d), v in actuals.items() if c == b.category)
        seen.add(key)
        out.append({"budget": b, "category": b.category, "department": b.department.name if b.department else "All departments",
                    "budgeted": float(b.amount), "actual": round(actual, 2), "variance": round(float(b.amount) - actual, 2),
                    "used_pct": round(100.0 * actual / float(b.amount), 1) if float(b.amount) else 0.0})
    for (cat, dept_id), amt in actuals.items():
        if (cat, dept_id) in seen or amt <= 0:
            continue
        if any(b.category == cat and b.department_id is None for b in budgets):
            continue
        dept = db.query(Department).get(dept_id) if dept_id else None
        out.append({"budget": None, "category": cat, "department": dept.name if dept else "All departments", "budgeted": 0.0,
                    "actual": round(amt, 2), "variance": round(-amt, 2), "used_pct": 0.0})
    return out


def forecast(db: Session, months: int = 3) -> dict:
    """Simple projection: MRR (active subscriptions, base currency) vs average monthly expenses of the last 3 months."""
    from app.services.billing import mrr_in_base
    mrr = mrr_in_base(db)
    cf = cash_flow(db, 3)
    today = date.today()
    exp_rows = []
    for i in range(3, 0, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            y -= 1
            m += 12
        s, e = month_bounds(f"{y:04d}-{m:02d}")
        pl = profit_and_loss(db, s, e)
        exp_rows.append(pl["total_expenses"])
    avg_exp = round(sum(exp_rows) / len(exp_rows), 2) if exp_rows else 0.0
    growth = 0.02  # assumed monthly growth
    rows = []
    rev, exp = mrr, avg_exp
    for i in range(1, months + 1):
        y, m = today.year, today.month + i
        while m > 12:
            y += 1
            m -= 12
        rev = round(rev * (1 + growth), 2)
        exp = round(exp * 1.01, 2)
        rows.append({"period": f"{y:04d}-{m:02d}", "label": date(y, m, 1).strftime("%b %Y"), "revenue": rev, "expenses": exp,
                     "net": round(rev - exp, 2)})
    return {"mrr": mrr, "avg_expenses": avg_exp, "growth_pct": growth * 100, "rows": rows, "history": cf, "currency": base_currency(db)}


# ----------------------------------------------------------------------------- expenses
def record_expense(db: Session, category: str, amount: float, currency: str, expense_date: date, user: Optional[User] = None,
                   department_id: Optional[int] = None, vendor: Optional[str] = None, description: Optional[str] = None,
                   receipt_path: Optional[str] = None, account_id: Optional[int] = None, status: str = "pending") -> Expense:
    from app.services.billing import convert_to_base
    if amount <= 0:
        raise ValueError("Expense amount must be greater than zero")
    account = db.query(Account).get(account_id) if account_id else account_for_category(db, category)
    e = Expense(expense_number=next_code(db, Expense, "expense_number", "EXP-"), category=category, account_id=account.id if account else None,
                department_id=department_id, vendor=vendor, description=description, amount=round(amount, 2), currency=currency,
                amount_in_base=round(convert_to_base(db, amount, currency), 2), expense_date=expense_date, status=status,
                submitted_by_id=user.id if user else None, receipt_path=receipt_path)
    db.add(e)
    db.flush()
    log_action(db, user, "create", "expenses", entity=e, description=f"Expense {e.expense_number} {currency} {amount:.2f} ({category})")
    return e


def approve_expense(db: Session, expense: Expense, user: Optional[User], note: Optional[str] = None,
                    entry_date: Optional[date] = None) -> JournalEntry:
    if expense.status not in ("pending",):
        raise ValueError(f"Expense is already {expense.status}")
    account = expense.account or account_for_category(db, expense.category)
    je = post_journal(db, f"Expense {expense.expense_number}: {expense.vendor or expense.category}",
                      [(account.code, float(expense.amount_in_base), 0), ("1010", 0, float(expense.amount_in_base))],
                      reference_type="expense", reference_id=expense.id, entry_date=entry_date or expense.expense_date, user=user)
    expense.status = "approved"
    expense.approved_by_id = user.id if user else None
    log_action(db, user, "approve", "expenses", entity=expense, description=f"Approved {expense.expense_number}; journal {je.entry_number}",
               rationale=note, consequential=True)
    return je


def reject_expense(db: Session, expense: Expense, user: Optional[User], note: str) -> None:
    if expense.status != "pending":
        raise ValueError(f"Expense is already {expense.status}")
    expense.status = "rejected"
    expense.approved_by_id = user.id if user else None
    log_action(db, user, "reject", "expenses", entity=expense, description=f"Rejected {expense.expense_number}", rationale=note, consequential=True)


def pay_expense(db: Session, expense: Expense, user: Optional[User], reference: Optional[str] = None) -> None:
    if expense.status != "approved":
        raise ValueError("Only approved expenses can be marked as paid")
    expense.status = "paid"
    if reference:
        expense.description = ((expense.description or "") + f"\nPaid ref: {reference}").strip()
    log_action(db, user, "pay", "expenses", entity=expense, description=f"Marked {expense.expense_number} as paid")


# ----------------------------------------------------------------------------- close
def close_period(db: Session, period: str, user: Optional[User], notes: Optional[str] = None) -> FinancialPeriod:
    fp = get_period(db, period)
    if fp.status == "closed":
        raise ValueError(f"Period {period} is already closed")
    start, end = month_bounds(period)
    pl = profit_and_loss(db, start, end)
    payroll = sum(r["amount"] for r in pl["expenses"] if r["code"] in ("5000", "5100"))
    fp.revenue_base = pl["total_income"]
    fp.expenses_base = pl["total_expenses"]
    fp.payroll_base = round(payroll, 2)
    fp.status = "closed"
    fp.closed_by_id = user.id if user else None
    fp.closed_at = datetime.utcnow()
    fp.notes = notes
    log_action(db, user, "close_period", "accounts", entity=fp, description=f"Closed {period}: revenue {pl['total_income']:.2f}, expenses {pl['total_expenses']:.2f}",
               rationale=notes, after=snapshot(fp), consequential=True)
    return fp


def reopen_period(db: Session, period: str, user: Optional[User], rationale: str) -> FinancialPeriod:
    fp = get_period(db, period)
    if fp.status != "closed":
        raise ValueError(f"Period {period} is not closed")
    fp.status = "open"
    log_action(db, user, "reopen_period", "accounts", entity=fp, description=f"Re-opened {period}", rationale=rationale, consequential=True)
    return fp


def consolidated_report(db: Session, period: str) -> dict:
    """Revenue / expenses per transaction currency with the exchange rates used, consolidated in base."""
    from app.models.finance import Payment
    start, end = month_bounds(period)
    rates = {c.code: float(c.rate_to_base) for c in db.query(Currency).all()}
    pay_rows = (db.query(Payment.currency, func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0), func.coalesce(func.sum(Payment.amount_in_base), 0))
                .filter(Payment.status == "completed", Payment.received_at >= datetime.combine(start, datetime.min.time()),
                        Payment.received_at <= datetime.combine(end, datetime.max.time())).group_by(Payment.currency).all())
    inv_rows = (db.query(Invoice.currency, func.count(Invoice.id), func.coalesce(func.sum(Invoice.total), 0), func.coalesce(func.sum(Invoice.total_in_base), 0))
                .filter(Invoice.status != "void", Invoice.issue_date >= start, Invoice.issue_date <= end).group_by(Invoice.currency).all())
    exp_rows = (db.query(Expense.currency, func.count(Expense.id), func.coalesce(func.sum(Expense.amount), 0), func.coalesce(func.sum(Expense.amount_in_base), 0))
                .filter(Expense.status.in_(["approved", "paid"]), Expense.expense_date >= start, Expense.expense_date <= end).group_by(Expense.currency).all())
    def pack(rows):
        return [{"currency": c, "count": n, "amount": float(a), "base": float(b), "rate": rates.get(c, 1.0)} for c, n, a, b in rows]
    return {"period": period, "rates": rates, "base": base_currency(db), "payments": pack(pay_rows), "invoices": pack(inv_rows),
            "expenses": pack(exp_rows), "pl": profit_and_loss(db, start, end)}


# =============================================================================================================
# ERP parity - Accounts area (docs/AUDIT_ACCOUNTS_CONFIG.md).
#
# A voucher IS a journal entry with a type: every Journal, Payment and Receipt Voucher writes JournalLine rows,
# so the ledger, the trial balance and the statements all read one set of lines. Everything below is appended;
# no signature above this banner has changed.
# =============================================================================================================

VOUCHER_TYPES = ["journal", "payment", "receipt"]
VOUCHER_PREFIXES = {"journal": "JV-", "payment": "PV-", "receipt": "RV-"}
VOUCHER_LABELS = {"journal": "Journal Voucher", "payment": "Payment Voucher", "receipt": "Receipt Voucher"}
VOUCHER_STATUSES = ["draft", "posted", "cancelled"]
VOUCHER_STATUS_LABELS = {"draft": "Draft", "posted": "Posted", "cancelled": "Cancelled", "reversed": "Reversed"}
PAYMENT_MODES = ["Bank", "Cash", "Online Payment Gateway", "Cheque", "Wire Transfer", "Mobile Wallet"]
PARTY_TYPES = [("client", "Client / Family"), ("employee", "Employee"), ("vendor", "Vendor"), ("other", "Other")]

# Payables ageing buckets, oldest last.
AGEING_BUCKETS = [("current", "Current"), ("d30", "1-30 Days"), ("d60", "31-60 Days"),
                  ("d90", "61-90 Days"), ("d90plus", "Over 90 Days")]


# ------------------------------------------------------------------------------------------------- ledger set
def ledger_entry_filter():
    """SQL condition selecting the entries whose lines actually sit in the ledger.

    A cancelled voucher that had already been posted keeps its lines: cancelling posts a contra entry rather
    than deleting, so both sides must be counted or the ledger would stop balancing.
    """
    return or_(JournalEntry.status == "posted",
               and_(JournalEntry.status.in_(("cancelled", "reversed")), JournalEntry.posted_at.isnot(None)))


def account_movements(db: Session, start: Optional[date] = None, end: Optional[date] = None) -> dict[int, dict]:
    """{account_id: {"debit": x, "credit": y}} over the (inclusive) range, base currency."""
    q = (db.query(JournalLine.account_id,
                  func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
         .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
         .filter(ledger_entry_filter()))
    if start:
        q = q.filter(JournalEntry.entry_date >= start)
    if end:
        q = q.filter(JournalEntry.entry_date <= end)
    return {aid: {"debit": round(float(d), 2), "credit": round(float(c), 2)}
            for aid, d, c in q.group_by(JournalLine.account_id).all()}


def account_openings(db: Session, before: Optional[date]) -> dict[int, float]:
    """{account_id: signed opening balance (debit positive)} for everything dated before ``before``."""
    if not before:
        return {}
    mv = account_movements(db, None, before - timedelta(days=1))
    return {aid: round(v["debit"] - v["credit"], 2) for aid, v in mv.items()}


def natural_balance(account_type: str, debit: float, credit: float) -> float:
    """Balance in the direction the account normally carries (assets/expenses debit, the rest credit)."""
    if account_type in ("asset", "expense"):
        return round(debit - credit, 2)
    return round(credit - debit, 2)


def titleize_type(value: str) -> str:
    return {"asset": "Assets", "liability": "Liabilities", "equity": "Equity", "income": "Income",
            "expense": "Expenses"}.get(value, str(value).title())


def account_has_postings(db: Session, account: Account) -> bool:
    return bool(db.query(JournalLine.id).filter(JournalLine.account_id == account.id).first())


def postable_accounts(db: Session, account_type: str = "", head_id: Optional[int] = None) -> list:
    q = db.query(Account).filter(Account.is_head.is_(False))
    if account_type:
        q = q.filter(Account.account_type == account_type)
    if head_id:
        q = q.filter(Account.parent_id == head_id)
    return q.order_by(Account.account_type, Account.sort_no, Account.code).all()


def head_accounts(db: Session, account_type: str = "") -> list:
    q = db.query(Account).filter(Account.is_head.is_(True))
    if account_type:
        q = q.filter(Account.account_type == account_type)
    return q.order_by(Account.account_type, Account.sort_no, Account.code).all()


def beneficiary_ledger_account(db: Session, beneficiary=None, payment_mode: str = "") -> Account:
    """The chart-of-accounts account a beneficiary account (bank / gateway / cash) settles through."""
    mode = (payment_mode or (getattr(beneficiary, "payment_mode", "") or "")).lower()
    return get_account(db, "1000" if "cash" in mode else "1010")


# ------------------------------------------------------------------------------------------------- vouchers
def next_voucher_number(db: Session, voucher_type: str) -> str:
    return next_code(db, JournalEntry, "voucher_number", VOUCHER_PREFIXES.get(voucher_type, "JV-"), 5)


def validate_voucher_lines(lines: list) -> tuple:
    """``lines`` = [(account, debit, credit, memo), ...]. Returns (total_debit, total_credit) or raises."""
    priced = [l for l in lines if round(float(l[1] or 0), 2) or round(float(l[2] or 0), 2)]
    if len(priced) < 2:
        raise ValueError("A voucher needs at least two lines carrying an amount")
    for l in priced:
        if round(float(l[1] or 0), 2) and round(float(l[2] or 0), 2):
            raise ValueError("A line may carry a debit or a credit, not both")
    total_debit = round(sum(float(l[1] or 0) for l in priced), 2)
    total_credit = round(sum(float(l[2] or 0) for l in priced), 2)
    if abs(total_debit - total_credit) > 0.01:
        raise ValueError(f"Voucher is not balanced: debit {total_debit:,.2f} vs credit {total_credit:,.2f}")
    if total_debit <= 0:
        raise ValueError("Voucher total must be greater than zero")
    return total_debit, total_credit


def create_voucher(db: Session, voucher_type: str, entry_date: date, description: str, lines: list,
                   user: Optional[User] = None, currency: Optional[str] = None, exchange_rate: float = 1.0,
                   party_type: Optional[str] = None, party_id: Optional[int] = None, party_name: Optional[str] = None,
                   payment_mode: Optional[str] = None, beneficiary_account_id: Optional[int] = None,
                   reference_no: Optional[str] = None, status: str = "draft",
                   reference_type: Optional[str] = None, reference_id: Optional[int] = None) -> JournalEntry:
    """Create a Journal / Payment / Receipt Voucher. ``lines`` = [(account_code_or_id, debit, credit, memo)]
    in the voucher currency; they are stored in base currency using ``exchange_rate``."""
    if voucher_type not in VOUCHER_TYPES:
        raise ValueError(f"Unknown voucher type {voucher_type}")
    if status not in ("draft", "posted"):
        raise ValueError(f"A voucher cannot be created as {status}")
    rate = round(float(exchange_rate or 1) or 1, 6)
    validate_voucher_lines(lines)
    priced = [l for l in lines if round(float(l[1] or 0), 2) or round(float(l[2] or 0), 2)]
    base_lines = [(l[0], round(float(l[1] or 0) * rate, 2), round(float(l[2] or 0) * rate, 2)) for l in priced]
    memos = [(l[3] if len(l) > 3 else None) for l in priced]
    je = post_journal(db, description, base_lines, reference_type=reference_type or voucher_type,
                      reference_id=reference_id, currency=currency or base_currency(db), entry_date=entry_date,
                      user=user, status=status)
    for line, memo in zip(je.lines, memos):
        line.memo = str(memo)[:200] if memo else None
    je.voucher_type = voucher_type
    je.voucher_number = next_voucher_number(db, voucher_type)
    je.party_type = party_type or None
    je.party_id = party_id
    je.party_name = party_name[:150] if party_name else None
    je.payment_mode = payment_mode or None
    je.beneficiary_account_id = beneficiary_account_id
    je.reference_no = reference_no[:120] if reference_no else None
    je.exchange_rate = rate
    if status == "posted":
        je.posted_by_id = user.id if user else None
        je.posted_at = datetime.utcnow()
    db.flush()
    return je


def adopt_as_voucher(db: Session, entry: JournalEntry, voucher_type: str, user: Optional[User] = None,
                     party_type: Optional[str] = None, party_id: Optional[int] = None,
                     party_name: Optional[str] = None, payment_mode: Optional[str] = None,
                     beneficiary_account_id: Optional[int] = None, reference_no: Optional[str] = None,
                     exchange_rate: float = 1.0) -> JournalEntry:
    """Stamp a journal entry that another service posted (a receipt applied to an invoice by app.services.billing,
    for example) as a voucher, so it appears in the voucher register instead of being posted twice."""
    if entry.voucher_number:
        return entry
    entry.voucher_type = voucher_type
    entry.voucher_number = next_voucher_number(db, voucher_type)
    entry.party_type = party_type or entry.party_type
    entry.party_id = party_id if party_id is not None else entry.party_id
    entry.party_name = (party_name[:150] if party_name else entry.party_name)
    entry.payment_mode = payment_mode or entry.payment_mode
    entry.beneficiary_account_id = beneficiary_account_id or entry.beneficiary_account_id
    entry.reference_no = (reference_no[:120] if reference_no else entry.reference_no)
    entry.exchange_rate = round(float(exchange_rate or 1) or 1, 6)
    if entry.status == "posted" and entry.posted_at is None:
        entry.posted_by_id = user.id if user else None
        entry.posted_at = datetime.utcnow()
    db.flush()
    return entry


def post_voucher(db: Session, entry: JournalEntry, user: Optional[User] = None) -> JournalEntry:
    """Draft -> posted. Re-checks the balance and refuses a closed period."""
    if entry.status != "draft":
        raise ValueError(f"A {entry.status} voucher cannot be posted")
    lines = [(l.account_id, float(l.debit or 0), float(l.credit or 0), l.memo) for l in entry.lines]
    validate_voucher_lines(lines)
    period = entry.period or month_key(entry.entry_date)
    if period_is_closed(db, period):
        raise ValueError(f"Period {period} is closed; this voucher cannot be posted into it")
    entry.status = "posted"
    entry.posted_by_id = user.id if user else None
    entry.posted_at = datetime.utcnow()
    db.flush()
    return entry


def cancel_voucher(db: Session, entry: JournalEntry, user: Optional[User], reason: str):
    """Cancel a voucher. A posted voucher is neutralised by a contra voucher that references the original -
    nothing is ever deleted. Returns the reversal (None when a draft was cancelled)."""
    if not reason:
        raise ValueError("A reason is required to cancel a voucher")
    if entry.status == "cancelled":
        raise ValueError("This voucher is already cancelled")
    reversal = None
    if entry.status == "posted":
        period = entry.period or month_key(entry.entry_date)
        if period_is_closed(db, period):
            raise ValueError(f"Period {period} is closed; the reversal cannot be dated inside it")
        rate = float(entry.exchange_rate or 1) or 1
        lines = [(l.account_id, round(float(l.credit or 0) / rate, 2), round(float(l.debit or 0) / rate, 2),
                  f"Reversal of {entry.voucher_number or entry.entry_number}") for l in entry.lines]
        reversal = create_voucher(
            db, entry.voucher_type if entry.voucher_type in VOUCHER_TYPES else "journal", entry.entry_date,
            f"Reversal of {entry.voucher_number or entry.entry_number}: {entry.description}", lines, user=user,
            currency=entry.currency, exchange_rate=rate, party_type=entry.party_type, party_id=entry.party_id,
            party_name=entry.party_name, payment_mode=entry.payment_mode,
            beneficiary_account_id=entry.beneficiary_account_id, reference_no=entry.voucher_number,
            status="posted", reference_type="reversal", reference_id=entry.id)
    entry.status = "cancelled"
    entry.cancel_reason = reason[:200]
    db.flush()
    return reversal


def voucher_status_counts(db: Session) -> dict:
    rows = (db.query(JournalEntry.status, func.count(JournalEntry.id))
            .filter(JournalEntry.voucher_number.isnot(None)).group_by(JournalEntry.status).all())
    counts = {s: int(n) for s, n in rows}
    counts["total"] = sum(counts.values())
    for key in VOUCHER_STATUSES:
        counts.setdefault(key, 0)
    return counts


def voucher_type_counts(db: Session) -> dict:
    rows = (db.query(JournalEntry.voucher_type, func.count(JournalEntry.id))
            .filter(JournalEntry.voucher_number.isnot(None)).group_by(JournalEntry.voucher_type).all())
    return {t or "journal": int(n) for t, n in rows}


# ------------------------------------------------------------------------------------------------- tree view
def accounts_tree(db: Session, as_of: Optional[date] = None) -> dict:
    """The chart of accounts drawn from parent_id, each node carrying its own balance and the roll-up of its
    children."""
    as_of = as_of or date.today()
    accounts = db.query(Account).order_by(Account.sort_no, Account.code).all()
    mv = account_movements(db, None, as_of)
    by_parent = defaultdict(list)
    ids = {a.id for a in accounts}
    for a in accounts:
        by_parent[a.parent_id if a.parent_id in ids else None].append(a)

    def build(account, depth, ancestors):
        m = mv.get(account.id, {"debit": 0.0, "credit": 0.0})
        own = natural_balance(account.account_type, m["debit"], m["credit"])
        children = [build(c, depth + 1, ancestors + [account.id]) for c in by_parent.get(account.id, [])]
        return {"account": account, "depth": depth, "own": own, "debit": m["debit"], "credit": m["credit"],
                "children": children, "child_count": len(children), "ancestors": ancestors,
                "total": round(own + sum(c["total"] for c in children), 2)}

    roots = [build(a, 0, []) for a in by_parent.get(None, [])]
    flat = []

    def walk(nodes):
        for n in nodes:
            flat.append(n)
            walk(n["children"])

    walk(roots)
    totals = defaultdict(float)
    for n in roots:
        totals[n["account"].account_type] += n["total"]
    return {"nodes": roots, "flat": flat, "as_of": as_of, "count": len(accounts),
            "totals": {k: round(v, 2) for k, v in totals.items()}, "currency": base_currency(db)}


# ------------------------------------------------------------------------------------------------- reports
def ledger_report(db: Session, account: Account, start: date, end: date) -> dict:
    """Ledger Report: every line on one account in the range with a running balance."""
    opening = account_openings(db, start).get(account.id, 0.0)
    rows_q = (db.query(JournalLine, JournalEntry)
              .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
              .filter(ledger_entry_filter(), JournalLine.account_id == account.id,
                      JournalEntry.entry_date >= start, JournalEntry.entry_date <= end)
              .order_by(JournalEntry.entry_date, JournalEntry.id, JournalLine.id).all())
    running = opening
    rows = []
    total_debit = total_credit = 0.0
    for line, entry in rows_q:
        debit, credit = round(float(line.debit or 0), 2), round(float(line.credit or 0), 2)
        running = round(running + debit - credit, 2)
        total_debit = round(total_debit + debit, 2)
        total_credit = round(total_credit + credit, 2)
        rows.append({"entry": entry, "line": line, "date": entry.entry_date,
                     "voucher_number": entry.voucher_number or entry.entry_number,
                     "voucher_type": entry.voucher_type or "journal", "description": entry.description,
                     "memo": line.memo, "party": entry.party_name, "reference": entry.reference_no,
                     "debit": debit, "credit": credit, "balance": running})
    signed = 1 if account.account_type in ("asset", "expense") else -1
    return {"account": account, "start": start, "end": end, "rows": rows,
            "opening": opening, "closing": running, "total_debit": total_debit, "total_credit": total_credit,
            "opening_natural": round(opening * signed, 2), "closing_natural": round(running * signed, 2),
            "currency": base_currency(db)}


def _account_rows(db: Session, start: Optional[date], end: date, account_type: str = "",
                  head_id: Optional[int] = None, include_empty: bool = True) -> list:
    accounts = db.query(Account).order_by(Account.account_type, Account.sort_no, Account.code).all()
    mv = account_movements(db, start, end)
    opening = account_openings(db, start) if start else {}
    out = []
    for a in accounts:
        m = mv.get(a.id, {"debit": 0.0, "credit": 0.0})
        op = opening.get(a.id, 0.0)
        touched = bool(m["debit"] or m["credit"] or op)
        if a.is_head and not touched:
            continue  # a head is not postable: it only shows up if something was posted to it
        if account_type and a.account_type != account_type:
            continue
        if head_id and a.parent_id != head_id:
            continue
        if not include_empty and not touched:
            continue
        closing = round(op + m["debit"] - m["credit"], 2)
        signed = 1 if a.account_type in ("asset", "expense") else -1
        out.append({"account": a, "code": a.code, "name": a.name, "type": a.account_type,
                    "head": a.parent.name if a.parent else "", "opening": op, "debit": m["debit"],
                    "credit": m["credit"], "closing": closing,
                    "opening_natural": round(op * signed, 2), "closing_natural": round(closing * signed, 2)})
    return out


def trial_balance(db: Session, start: date, end: date, include_empty: bool = False) -> dict:
    """Trial Balance: every postable account with its debit and credit totals for the range and its closing
    balance, grouped by account type. The debit and credit columns must be equal."""
    rows = _account_rows(db, start, end, include_empty=include_empty)
    groups = []
    for typ in ACCOUNT_TYPES:
        grows = [r for r in rows if r["type"] == typ]
        if not grows:
            continue
        groups.append({"type": typ, "label": titleize_type(typ), "rows": grows,
                       "opening": round(sum(r["opening"] for r in grows), 2),
                       "debit": round(sum(r["debit"] for r in grows), 2),
                       "credit": round(sum(r["credit"] for r in grows), 2),
                       "closing": round(sum(r["closing"] for r in grows), 2)})
    total_debit = round(sum(r["debit"] for r in rows), 2)
    total_credit = round(sum(r["credit"] for r in rows), 2)
    difference = round(total_debit - total_credit, 2)
    return {"start": start, "end": end, "rows": rows, "groups": groups,
            "total_opening": round(sum(r["opening"] for r in rows), 2),
            "total_debit": total_debit, "total_credit": total_credit,
            "total_closing": round(sum(r["closing"] for r in rows), 2),
            "difference": difference, "balanced": abs(difference) < 0.01, "currency": base_currency(db)}


def _statement_section(db: Session, start: date, end: date) -> dict:
    mv = account_movements(db, start, end)
    accounts = {a.id: a for a in db.query(Account).all()}
    income, expense = [], []
    for aid, m in mv.items():
        a = accounts.get(aid)
        if a is None or a.account_type not in ("income", "expense"):
            continue
        amount = natural_balance(a.account_type, m["debit"], m["credit"])
        row = {"account": a, "code": a.code, "name": a.name, "amount": amount,
               "head": a.parent.name if a.parent else titleize_type(a.account_type)}
        (income if a.account_type == "income" else expense).append(row)
    income.sort(key=lambda r: r["code"])
    expense.sort(key=lambda r: r["code"])
    total_income = round(sum(r["amount"] for r in income), 2)
    total_expense = round(sum(r["amount"] for r in expense), 2)
    return {"income": income, "expense": expense, "total_income": total_income, "total_expense": total_expense,
            "net": round(total_income - total_expense, 2)}


def _group_rows(rows: list) -> list:
    grouped = {}
    for r in rows:
        g = grouped.setdefault(r["head"], {"head": r["head"], "rows": [], "amount": 0.0})
        g["rows"].append(r)
        g["amount"] = round(g["amount"] + r["amount"], 2)
    return sorted(grouped.values(), key=lambda g: g["head"])


def income_statement(db: Session, start: date, end: date) -> dict:
    """Income Statement for the range, by account and by group, against the previous period of equal length."""
    current = _statement_section(db, start, end)
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    previous = _statement_section(db, prev_start, prev_end)
    prev_income = {r["code"]: r["amount"] for r in previous["income"]}
    prev_expense = {r["code"]: r["amount"] for r in previous["expense"]}
    for r in current["income"]:
        r["previous"] = prev_income.get(r["code"], 0.0)
        r["delta"] = round(r["amount"] - r["previous"], 2)
    for r in current["expense"]:
        r["previous"] = prev_expense.get(r["code"], 0.0)
        r["delta"] = round(r["amount"] - r["previous"], 2)
    return {"start": start, "end": end, "prev_start": prev_start, "prev_end": prev_end,
            "income": current["income"], "expense": current["expense"],
            "income_groups": _group_rows(current["income"]), "expense_groups": _group_rows(current["expense"]),
            "total_income": current["total_income"], "total_expense": current["total_expense"], "net": current["net"],
            "previous": previous,
            "income_delta": round(current["total_income"] - previous["total_income"], 2),
            "expense_delta": round(current["total_expense"] - previous["total_expense"], 2),
            "net_delta": round(current["net"] - previous["net"], 2),
            "margin_pct": round(100.0 * current["net"] / current["total_income"], 1) if current["total_income"] else 0.0,
            "currency": base_currency(db)}


def balance_sheet(db: Session, as_of: Optional[date] = None) -> dict:
    """Balance Sheet as at a date. Retained earnings are derived from income less expense to that date and the
    accounting equation is checked explicitly."""
    as_of = as_of or date.today()
    mv = account_movements(db, None, as_of)
    accounts = {a.id: a for a in db.query(Account).all()}
    sections = {"asset": [], "liability": [], "equity": []}
    income_total = expense_total = 0.0
    for aid, m in mv.items():
        a = accounts.get(aid)
        if a is None:
            continue
        amount = natural_balance(a.account_type, m["debit"], m["credit"])
        if a.account_type in sections:
            if amount:
                sections[a.account_type].append({"account": a, "code": a.code, "name": a.name, "amount": amount,
                                                 "head": a.parent.name if a.parent else titleize_type(a.account_type)})
        elif a.account_type == "income":
            income_total = round(income_total + amount, 2)
        else:
            expense_total = round(expense_total + amount, 2)
    for rows in sections.values():
        rows.sort(key=lambda r: r["code"])
    retained = round(income_total - expense_total, 2)
    total_assets = round(sum(r["amount"] for r in sections["asset"]), 2)
    total_liabilities = round(sum(r["amount"] for r in sections["liability"]), 2)
    total_equity = round(sum(r["amount"] for r in sections["equity"]), 2)
    total_equity_with_earnings = round(total_equity + retained, 2)
    total_liab_equity = round(total_liabilities + total_equity_with_earnings, 2)
    difference = round(total_assets - total_liab_equity, 2)
    return {"as_of": as_of, "assets": sections["asset"], "liabilities": sections["liability"],
            "equity": sections["equity"], "asset_groups": _group_rows(sections["asset"]),
            "liability_groups": _group_rows(sections["liability"]), "equity_groups": _group_rows(sections["equity"]),
            "income_total": income_total, "expense_total": expense_total, "retained_earnings": retained,
            "total_assets": total_assets, "total_liabilities": total_liabilities, "total_equity": total_equity,
            "total_equity_with_earnings": total_equity_with_earnings, "total_liabilities_equity": total_liab_equity,
            "difference": difference, "balanced": abs(difference) < 0.01, "currency": base_currency(db)}


def _ageing_key(days: int) -> str:
    if days <= 0:
        return "current"
    if days <= 30:
        return "d30"
    if days <= 60:
        return "d60"
    if days <= 90:
        return "d90"
    return "d90plus"


def _empty_buckets() -> dict:
    return {key: 0.0 for key, _ in AGEING_BUCKETS}


def payables_summary(db: Session, as_of: Optional[date] = None) -> dict:
    """Payables Summary: what is owed, by vendor and by account, aged into current / 30 / 60 / 90 days."""
    as_of = as_of or date.today()
    rows = (db.query(Expense).filter(Expense.status == "approved", Expense.expense_date <= as_of)
            .order_by(Expense.expense_date).all())
    vendors = {}
    by_account = {}
    totals = _empty_buckets()
    grand = 0.0
    for e in rows:
        amount = round(float(e.amount_in_base or 0), 2)
        if amount <= 0:
            continue
        days = (as_of - e.expense_date).days if e.expense_date else 0
        key = _ageing_key(days)
        vendor = (e.vendor or "Unnamed vendor").strip()
        v = vendors.setdefault(vendor, {"vendor": vendor, "count": 0, "total": 0.0, "buckets": _empty_buckets(),
                                        "oldest": e.expense_date})
        v["count"] += 1
        v["total"] = round(v["total"] + amount, 2)
        v["buckets"][key] = round(v["buckets"][key] + amount, 2)
        if e.expense_date and (v["oldest"] is None or e.expense_date < v["oldest"]):
            v["oldest"] = e.expense_date
        acct = e.account
        label = f"{acct.code} {acct.name}" if acct else (e.category or "Unclassified").replace("_", " ").title()
        a = by_account.setdefault(label, {"account": label, "count": 0, "total": 0.0, "buckets": _empty_buckets()})
        a["count"] += 1
        a["total"] = round(a["total"] + amount, 2)
        a["buckets"][key] = round(a["buckets"][key] + amount, 2)
        totals[key] = round(totals[key] + amount, 2)
        grand = round(grand + amount, 2)
    mv = account_movements(db, None, as_of)
    ledger_rows = []
    for acct in db.query(Account).filter(Account.account_type == "liability").order_by(Account.code).all():
        m = mv.get(acct.id)
        if not m:
            continue
        amount = natural_balance("liability", m["debit"], m["credit"])
        if amount:
            ledger_rows.append({"account": acct, "code": acct.code, "name": acct.name, "amount": amount})
    return {"as_of": as_of, "vendors": sorted(vendors.values(), key=lambda v: -v["total"]),
            "accounts": sorted(by_account.values(), key=lambda a: -a["total"]),
            "ledger_rows": ledger_rows, "ledger_total": round(sum(r["amount"] for r in ledger_rows), 2),
            "buckets": AGEING_BUCKETS, "totals": totals, "total": grand, "count": len(rows),
            "currency": base_currency(db)}


def account_wise_summary(db: Session, start: date, end: date, account_type: str = "",
                         head_id: Optional[int] = None) -> dict:
    """Account Wise Summary: opening, debits, credits and closing for every account in the range."""
    rows = _account_rows(db, start, end, account_type=account_type, head_id=head_id, include_empty=True)
    return {"start": start, "end": end, "rows": rows, "account_type": account_type, "head_id": head_id,
            "total_opening": round(sum(r["opening"] for r in rows), 2),
            "total_debit": round(sum(r["debit"] for r in rows), 2),
            "total_credit": round(sum(r["credit"] for r in rows), 2),
            "total_closing": round(sum(r["closing"] for r in rows), 2),
            "currency": base_currency(db)}


def approved_advances(db: Session, status: str = "") -> dict:
    """Approved Advances: staff salary advances that are approved or still outstanding, with the instalment
    plan, what has been recovered and what is left. Reads the HR SalaryAdvance model lazily."""
    from app.models.people import SalaryAdvance  # local import: the HR module owns this model
    q = db.query(SalaryAdvance)
    if status:
        q = q.filter(SalaryAdvance.status == status)
    else:
        q = q.filter(SalaryAdvance.status.in_(["approved", "paid", "settled"]))
    rows = []
    for adv in q.order_by(SalaryAdvance.request_date.desc(), SalaryAdvance.id.desc()).all():
        amount = round(float(adv.amount or 0), 2)
        balance = round(float(adv.remaining or 0), 2)
        recovered = round(amount - balance, 2)
        installments = int(adv.installments or 1) or 1
        emp = adv.employee
        rows.append({"advance": adv, "employee": emp,
                     "employee_name": emp.full_name if emp else "-",
                     "employee_code": getattr(emp, "employee_code", "") if emp else "",
                     "designation": getattr(emp, "designation", "") if emp else "",
                     "request_date": adv.request_date, "amount": amount, "currency": adv.currency or "PKR",
                     "installments": installments,
                     "per_installment": round(amount / installments, 2) if installments else amount,
                     "recovered": recovered, "balance": balance, "status": adv.status,
                     "recovered_pct": round(100.0 * recovered / amount, 1) if amount else 0.0,
                     "reason": adv.reason})
    return {"rows": rows, "total": round(sum(r["amount"] for r in rows), 2),
            "recovered": round(sum(r["recovered"] for r in rows), 2),
            "outstanding": round(sum(r["balance"] for r in rows), 2),
            "count": len(rows), "currency": base_currency(db)}
