"""Finance: currencies, subscriptions, pricing governance, invoices, payments, ledger, accounts."""
from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, Date, Text, ForeignKey, JSON, Float, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, PKMixin, TimestampMixin


class Currency(Base, PKMixin, TimestampMixin):
    __tablename__ = "currencies"
    code: Mapped[str] = mapped_column(String(3), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    symbol: Mapped[str] = mapped_column(String(5), default="")
    rate_to_base: Mapped[float] = mapped_column(Numeric(14, 6), default=1)  # 1 unit of this = X base currency
    is_base: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False)


class ExchangeRateHistory(Base, PKMixin):
    __tablename__ = "exchange_rate_history"
    currency_code: Mapped[str] = mapped_column(String(3), index=True)
    rate_to_base: Mapped[float] = mapped_column(Numeric(14, 6))
    source: Mapped[str] = mapped_column(String(30), default="manual")
    set_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    effective_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Subscription(Base, PKMixin, TimestampMixin):
    __tablename__ = "subscriptions"
    subscription_code: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # SUB-00001
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    package_id: Mapped[Optional[int]] = mapped_column(ForeignKey("packages.id", ondelete="SET NULL"))
    course_id: Mapped[Optional[int]] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    sessions_per_week: Mapped[int] = mapped_column(Integer, default=5)
    session_minutes: Mapped[int] = mapped_column(Integer, default=30)
    list_price: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    discount_pct: Mapped[float] = mapped_column(Float, default=0)
    discount_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    scholarship_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scholarships.id", ondelete="SET NULL"))
    scholarship_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    price: Mapped[float] = mapped_column(Numeric(12, 2), default=0)  # final monthly price
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    price_in_base: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    teacher_cost_base: Mapped[float] = mapped_column(Numeric(12, 2), default=0)  # monthly teacher cost in base currency
    billing_cycle: Mapped[str] = mapped_column(String(20), default="monthly")
    start_date: Mapped[date] = mapped_column(Date, default=date.today)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    next_billing_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # pending_approval | active | frozen | cancelled | expired
    freeze_start: Mapped[Optional[date]] = mapped_column(Date)
    freeze_end: Mapped[Optional[date]] = mapped_column(Date)
    cancelled_at: Mapped[Optional[date]] = mapped_column(Date)
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(200))
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    client = relationship("Client")
    student = relationship("Student")
    package = relationship("Package")
    course = relationship("Course")
    teacher = relationship("Teacher")
    scholarship = relationship("Scholarship", foreign_keys=[scholarship_id])


class DiscountRequest(Base, PKMixin, TimestampMixin):
    """Module 45: discount ladder 0–20% manager, 21–35% CEO, >35% prohibited."""
    __tablename__ = "discount_requests"
    subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscriptions.id", ondelete="CASCADE"))
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
    discount_pct: Mapped[float] = mapped_column(Float)
    approver_tier: Mapped[str] = mapped_column(String(20))  # manager | ceo | prohibited
    requested_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected | blocked
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    decision_note: Mapped[Optional[str]] = mapped_column(Text)
    week: Mapped[Optional[str]] = mapped_column(String(10))  # ISO week for the weekly discount register

    subscription = relationship("Subscription")
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])


class Scholarship(Base, PKMixin, TimestampMixin):
    """Dignified financial-aid path, recorded distinctly from discounts."""
    __tablename__ = "scholarships"
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    scholarship_type: Mapped[str] = mapped_column(String(30), default="need_based")  # need_based | merit | hafiz | orphan | staff
    coverage_pct: Mapped[float] = mapped_column(Float, default=0)
    monthly_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    reason: Mapped[Optional[str]] = mapped_column(Text)
    start_date: Mapped[date] = mapped_column(Date, default=date.today)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected | ended
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    requested_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    client = relationship("Client")
    student = relationship("Student")


class Invoice(Base, PKMixin, TimestampMixin):
    __tablename__ = "invoices"
    invoice_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # INV-2026-00001
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))
    issue_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    period_start: Mapped[Optional[date]] = mapped_column(Date)
    period_end: Mapped[Optional[date]] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    subtotal: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    discount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    credit_applied: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    tax: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    total: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    paid_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    total_in_base: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft | sent | partial | paid | overdue | void
    remarks: Mapped[Optional[str]] = mapped_column(Text)
    billing_rep_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    pdf_path: Mapped[Optional[str]] = mapped_column(String(300))

    client = relationship("Client")
    student = relationship("Student")
    subscription = relationship("Subscription")
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="invoice")

    @property
    def balance(self) -> float:
        return float(self.total or 0) - float(self.paid_amount or 0)


class InvoiceItem(Base, PKMixin):
    __tablename__ = "invoice_items"
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"))
    description: Mapped[str] = mapped_column(String(250))
    quantity: Mapped[float] = mapped_column(Float, default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)

    invoice = relationship("Invoice", back_populates="items")


class Payment(Base, PKMixin, TimestampMixin):
    __tablename__ = "payments"
    payment_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # PAY-00001
    invoice_id: Mapped[Optional[int]] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    amount_in_base: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    method: Mapped[str] = mapped_column(String(30), default="bank_transfer")  # bank_transfer | card | paypal | stripe | cash | wise | other
    gateway: Mapped[Optional[str]] = mapped_column(String(30))
    reference: Mapped[Optional[str]] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="completed")  # pending | completed | failed | refunded
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    received_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False)
    reconciled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    invoice = relationship("Invoice", back_populates="payments")
    client = relationship("Client")
    receipt = relationship("Receipt", back_populates="payment", uselist=False)


class Receipt(Base, PKMixin):
    __tablename__ = "receipts"
    receipt_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # RCT-00001
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), unique=True)
    pdf_path: Mapped[Optional[str]] = mapped_column(String(300))
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_to: Mapped[Optional[str]] = mapped_column(String(200))

    payment = relationship("Payment", back_populates="receipt")


class LedgerEntry(Base, PKMixin):
    """Client account ledger: charges, payments, credits (referral), refunds, adjustments."""
    __tablename__ = "ledger_entries"
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    entry_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    entry_type: Mapped[str] = mapped_column(String(20))  # charge | payment | credit | refund | adjustment | scholarship
    description: Mapped[str] = mapped_column(String(250))
    debit: Mapped[float] = mapped_column(Numeric(12, 2), default=0)  # increases what client owes
    credit: Mapped[float] = mapped_column(Numeric(12, 2), default=0)  # decreases what client owes
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    balance_after: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    reference_type: Mapped[Optional[str]] = mapped_column(String(30))  # invoice | payment | referral | manual
    reference_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    client = relationship("Client")


class Account(Base, PKMixin, TimestampMixin):
    """Chart of accounts."""
    __tablename__ = "accounts"
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    account_type: Mapped[str] = mapped_column(String(20))  # asset | liability | equity | income | expense
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    parent = relationship("Account", remote_side="Account.id")


class JournalEntry(Base, PKMixin, TimestampMixin):
    __tablename__ = "journal_entries"
    entry_number: Mapped[str] = mapped_column(String(30), unique=True)
    entry_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    description: Mapped[str] = mapped_column(String(250))
    reference_type: Mapped[Optional[str]] = mapped_column(String(30))  # payment | expense | payroll | manual | refund
    reference_id: Mapped[Optional[int]] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    total: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="posted")  # draft | posted | reversed
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    period: Mapped[Optional[str]] = mapped_column(String(7), index=True)

    lines = relationship("JournalLine", back_populates="entry", cascade="all, delete-orphan")


class JournalLine(Base, PKMixin):
    __tablename__ = "journal_lines"
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    debit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    credit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    memo: Mapped[Optional[str]] = mapped_column(String(200))

    entry = relationship("JournalEntry", back_populates="lines")
    account = relationship("Account")


class Expense(Base, PKMixin, TimestampMixin):
    __tablename__ = "expenses"
    expense_number: Mapped[str] = mapped_column(String(30), unique=True)
    category: Mapped[str] = mapped_column(String(60))  # salaries | marketing | software | internet | office | utilities | other
    account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    vendor: Mapped[Optional[str]] = mapped_column(String(150))
    description: Mapped[Optional[str]] = mapped_column(Text)
    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    amount_in_base: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    expense_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected | paid
    submitted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    receipt_path: Mapped[Optional[str]] = mapped_column(String(300))

    department = relationship("Department")
    account = relationship("Account")


class Budget(Base, PKMixin, TimestampMixin):
    __tablename__ = "budgets"
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    category: Mapped[str] = mapped_column(String(60))
    period: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    notes: Mapped[Optional[str]] = mapped_column(Text)

    department = relationship("Department")


class FinancialPeriod(Base, PKMixin, TimestampMixin):
    __tablename__ = "financial_periods"
    period: Mapped[str] = mapped_column(String(7), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | closing | closed
    closed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    revenue_base: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    expenses_base: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    payroll_base: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text)
