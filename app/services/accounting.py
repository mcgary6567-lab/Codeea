"""Accounting service (Module 19): chart of accounts, double-entry journal, P&L, cash flow, aging, budgets,
expenses, forecasting and financial close. All journal amounts are stored in the base currency (PKR)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func
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
