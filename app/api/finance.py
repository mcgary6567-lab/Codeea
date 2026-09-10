"""REST API: finance — subscriptions, invoices, payments, ledger, expenses, currencies and the payment webhook."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import require, get_current_user
from app.core.utils import month_bounds
from app.database import get_db
from app.models.academic import Package
from app.models.core import User
from app.models.finance import Currency, Expense, Invoice, LedgerEntry, Payment, Scholarship, Subscription
from app.models.people import Client, Student, Teacher
from app.services import accounting, billing

router = APIRouter(prefix="/finance", tags=["finance"])


# --------------------------------------------------------------------------- schemas
class SubscriptionOut(BaseModel):
    id: int
    code: str
    client_code: str
    student: str
    package: Optional[str] = None
    course: Optional[str] = None
    teacher: Optional[str] = None
    sessions_per_week: int
    list_price: float
    discount_pct: float
    scholarship_amount: float
    price: float
    currency: str
    price_in_base: float
    teacher_cost_base: float
    margin_pct: float
    status: str
    start_date: date
    next_billing_date: Optional[date] = None


class SubscriptionIn(BaseModel):
    client_id: int
    student_id: int
    package_id: Optional[int] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    discount_pct: float = 0
    teacher_id: Optional[int] = None
    scholarship_id: Optional[int] = None
    sessions_per_week: Optional[int] = None
    start_date: Optional[date] = None
    rationale: Optional[str] = None


class InvoiceOut(BaseModel):
    id: int
    number: str
    client_code: str
    student: Optional[str] = None
    issue_date: date
    due_date: date
    currency: str
    subtotal: float
    discount: float
    credit_applied: float
    total: float
    paid_amount: float
    balance: float
    total_in_base: float
    status: str
    pdf_url: Optional[str] = None


class PaymentOut(BaseModel):
    id: int
    number: str
    client_code: str
    invoice: Optional[str] = None
    amount: float
    currency: str
    amount_in_base: float
    method: str
    gateway: Optional[str] = None
    reference: Optional[str] = None
    status: str
    reconciled: bool
    received_at: datetime
    receipt: Optional[str] = None


class PaymentIn(BaseModel):
    client_id: Optional[int] = None
    invoice_number: Optional[str] = None
    amount: float = Field(gt=0)
    currency: Optional[str] = None
    method: str = "bank_transfer"
    gateway: Optional[str] = None
    reference: Optional[str] = None
    received_at: Optional[datetime] = None


class LedgerEntryOut(BaseModel):
    id: int
    entry_date: date
    entry_type: str
    description: str
    debit: float
    credit: float
    currency: str
    balance_after: float


class LedgerOut(BaseModel):
    client_code: str
    client: str
    currency: str
    balance: float
    available_credit: float
    entries: list[LedgerEntryOut]


class ExpenseOut(BaseModel):
    id: int
    number: str
    category: str
    vendor: Optional[str] = None
    amount: float
    currency: str
    amount_in_base: float
    expense_date: date
    status: str


class ExpenseIn(BaseModel):
    category: str = "other"
    amount: float = Field(gt=0)
    currency: Optional[str] = None
    expense_date: Optional[date] = None
    vendor: Optional[str] = None
    description: Optional[str] = None
    department_id: Optional[int] = None


class CurrencyOut(BaseModel):
    code: str
    name: str
    symbol: str
    rate_to_base: float
    is_base: bool
    is_active: bool
    manual_override: bool


class RateIn(BaseModel):
    rate: float = Field(gt=0)
    source: str = "manual"


class WebhookIn(BaseModel):
    invoice_number: str
    amount: float = Field(gt=0)
    currency: Optional[str] = None
    reference: str
    method: str = "card"
    gateway: str = "stripe"
    status: str = "completed"
    received_at: Optional[datetime] = None


# --------------------------------------------------------------------------- serialisers
def _sub_out(db: Session, s: Subscription) -> SubscriptionOut:
    return SubscriptionOut(
        id=s.id, code=s.subscription_code, client_code=s.client.client_code if s.client else "",
        student=s.student.full_name if s.student else "", package=s.package.name if s.package else None,
        course=s.course.name if s.course else None, teacher=s.teacher.full_name if s.teacher else None,
        sessions_per_week=s.sessions_per_week, list_price=float(s.list_price), discount_pct=float(s.discount_pct or 0),
        scholarship_amount=float(s.scholarship_amount or 0), price=float(s.price), currency=s.currency,
        price_in_base=float(s.price_in_base or 0), teacher_cost_base=float(s.teacher_cost_base or 0),
        margin_pct=billing.margin_pct(float(s.price_in_base or 0), float(s.teacher_cost_base or 0)),
        status=s.status, start_date=s.start_date, next_billing_date=s.next_billing_date)


def _inv_out(i: Invoice) -> InvoiceOut:
    return InvoiceOut(id=i.id, number=i.invoice_number, client_code=i.client.client_code if i.client else "",
                      student=i.student.full_name if i.student else None, issue_date=i.issue_date, due_date=i.due_date,
                      currency=i.currency, subtotal=float(i.subtotal), discount=float(i.discount),
                      credit_applied=float(i.credit_applied), total=float(i.total), paid_amount=float(i.paid_amount),
                      balance=float(i.balance), total_in_base=float(i.total_in_base or 0), status=i.status,
                      pdf_url=i.pdf_path)


def _pay_out(p: Payment) -> PaymentOut:
    return PaymentOut(id=p.id, number=p.payment_number, client_code=p.client.client_code if p.client else "",
                      invoice=p.invoice.invoice_number if p.invoice else None, amount=float(p.amount),
                      currency=p.currency, amount_in_base=float(p.amount_in_base or 0), method=p.method,
                      gateway=p.gateway, reference=p.reference, status=p.status, reconciled=bool(p.reconciled),
                      received_at=p.received_at, receipt=p.receipt.pdf_path if p.receipt else None)


def _exp_out(e: Expense) -> ExpenseOut:
    return ExpenseOut(id=e.id, number=e.expense_number, category=e.category, vendor=e.vendor, amount=float(e.amount),
                      currency=e.currency, amount_in_base=float(e.amount_in_base or 0), expense_date=e.expense_date,
                      status=e.status)


# --------------------------------------------------------------------------- subscriptions
@router.get("/subscriptions", response_model=list[SubscriptionOut])
def api_subscriptions(status: str = "", client_id: Optional[int] = None, limit: int = Query(50, le=200),
                      offset: int = 0, db: Session = Depends(get_db), user: User = Depends(require("subscriptions.view"))):
    q = db.query(Subscription)
    if status:
        q = q.filter(Subscription.status == status)
    if client_id:
        q = q.filter(Subscription.client_id == client_id)
    return [_sub_out(db, s) for s in q.order_by(Subscription.id.desc()).offset(offset).limit(limit).all()]


@router.get("/subscriptions/{id}", response_model=SubscriptionOut)
def api_subscription(id: int, db: Session = Depends(get_db), user: User = Depends(require("subscriptions.view"))):
    s = db.query(Subscription).get(id)
    if not s:
        raise HTTPException(404, "Subscription not found")
    return _sub_out(db, s)


@router.post("/subscriptions", response_model=SubscriptionOut, status_code=201)
def api_create_subscription(payload: SubscriptionIn, db: Session = Depends(get_db),
                            user: User = Depends(require("subscriptions.add"))):
    client = db.query(Client).get(payload.client_id)
    student = db.query(Student).get(payload.student_id)
    if not client or not student or student.client_id != client.id:
        raise HTTPException(400, "Client and student do not match")
    package = db.query(Package).get(payload.package_id) if payload.package_id else None
    teacher = db.query(Teacher).get(payload.teacher_id) if payload.teacher_id else student.teacher
    scholarship = db.query(Scholarship).get(payload.scholarship_id) if payload.scholarship_id else None
    try:
        s = billing.create_subscription(db, client, student, package,
                                        payload.price if payload.price is not None else float(package.price if package else 0),
                                        payload.currency or client.currency, payload.discount_pct, teacher, user,
                                        rationale=payload.rationale, scholarship=scholarship,
                                        start_date=payload.start_date, sessions_per_week=payload.sessions_per_week)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return _sub_out(db, s)


# --------------------------------------------------------------------------- invoices
@router.get("/invoices", response_model=list[InvoiceOut])
def api_invoices(status: str = "", client_id: Optional[int] = None, limit: int = Query(50, le=200), offset: int = 0,
                 db: Session = Depends(get_db), user: User = Depends(require("billing.view"))):
    q = db.query(Invoice)
    if status:
        q = q.filter(Invoice.status == status)
    if client_id:
        q = q.filter(Invoice.client_id == client_id)
    return [_inv_out(i) for i in q.order_by(Invoice.id.desc()).offset(offset).limit(limit).all()]


@router.get("/invoices/{id}", response_model=InvoiceOut)
def api_invoice(id: int, db: Session = Depends(get_db), user: User = Depends(require("billing.view"))):
    i = db.query(Invoice).get(id)
    if not i:
        raise HTTPException(404, "Invoice not found")
    return _inv_out(i)


@router.post("/invoices/generate", response_model=list[InvoiceOut])
def api_generate_invoices(period: str, db: Session = Depends(get_db), user: User = Depends(require("billing.add"))):
    try:
        start, end = month_bounds(period)
    except Exception:
        raise HTTPException(400, "period must look like YYYY-MM")
    out = []
    subs = (db.query(Subscription).filter(Subscription.status == "active",
                                          Subscription.next_billing_date.isnot(None),
                                          Subscription.next_billing_date <= end).all())
    for s in subs:
        if billing.invoice_for_period(db, s, start):
            continue
        try:
            out.append(_inv_out(billing.generate_invoice(db, s, start, end, user=user)))
        except ValueError:
            continue
    db.commit()
    return out


# --------------------------------------------------------------------------- payments
@router.get("/payments", response_model=list[PaymentOut])
def api_payments(status: str = "", client_id: Optional[int] = None, limit: int = Query(50, le=200), offset: int = 0,
                 db: Session = Depends(get_db), user: User = Depends(require("payments.view"))):
    q = db.query(Payment)
    if status:
        q = q.filter(Payment.status == status)
    if client_id:
        q = q.filter(Payment.client_id == client_id)
    return [_pay_out(p) for p in q.order_by(Payment.id.desc()).offset(offset).limit(limit).all()]


@router.post("/payments", response_model=PaymentOut, status_code=201)
def api_create_payment(payload: PaymentIn, db: Session = Depends(get_db), user: User = Depends(require("payments.add"))):
    invoice = db.query(Invoice).filter(Invoice.invoice_number == payload.invoice_number).first() if payload.invoice_number else None
    client = db.query(Client).get(payload.client_id) if payload.client_id else (invoice.client if invoice else None)
    if not client:
        raise HTTPException(400, "client_id or a known invoice_number is required")
    try:
        p = billing.record_payment(db, client, payload.amount, payload.currency or (invoice.currency if invoice else client.currency),
                                   payload.method, payload.reference, user=user, invoice=invoice, gateway=payload.gateway,
                                   received_at=payload.received_at)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return _pay_out(p)


@router.post("/payment-webhook", response_model=PaymentOut, status_code=201)
def api_payment_webhook(payload: WebhookIn, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Gateway callback: record a payment against an invoice number + gateway reference (idempotent on reference)."""
    invoice = db.query(Invoice).filter(Invoice.invoice_number == payload.invoice_number).first()
    if not invoice:
        raise HTTPException(404, f"Unknown invoice {payload.invoice_number}")
    existing = db.query(Payment).filter(Payment.reference == payload.reference).first()
    if existing:
        return _pay_out(existing)
    try:
        p = billing.record_payment(db, invoice.client, payload.amount, payload.currency or invoice.currency,
                                   payload.method, payload.reference, user=user, invoice=invoice, gateway=payload.gateway,
                                   received_at=payload.received_at, status=payload.status,
                                   notes=f"Gateway webhook from {payload.gateway}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    log_action(db, user, "create", "payments", entity=p,
               description=f"Gateway webhook payment {p.payment_number} for {invoice.invoice_number}", request=request)
    db.commit()
    return _pay_out(p)


# --------------------------------------------------------------------------- ledger
@router.get("/ledger/{client_id}", response_model=LedgerOut)
def api_ledger(client_id: int, db: Session = Depends(get_db), user: User = Depends(require("ledger.view"))):
    c = db.query(Client).get(client_id)
    if not c:
        raise HTTPException(404, "Client not found")
    entries = [LedgerEntryOut(id=e.id, entry_date=e.entry_date, entry_type=e.entry_type, description=e.description,
                              debit=float(e.debit), credit=float(e.credit), currency=e.currency,
                              balance_after=float(e.balance_after)) for e in billing.client_statement(db, c)]
    return LedgerOut(client_code=c.client_code, client=c.full_name, currency=c.currency,
                     balance=billing.client_balance(db, c), available_credit=billing.available_credit(db, c),
                     entries=entries)


# --------------------------------------------------------------------------- expenses
@router.get("/expenses", response_model=list[ExpenseOut])
def api_expenses(status: str = "", category: str = "", limit: int = Query(50, le=200), offset: int = 0,
                 db: Session = Depends(get_db), user: User = Depends(require("expenses.view"))):
    q = db.query(Expense)
    if status:
        q = q.filter(Expense.status == status)
    if category:
        q = q.filter(Expense.category == category)
    return [_exp_out(e) for e in q.order_by(Expense.id.desc()).offset(offset).limit(limit).all()]


@router.post("/expenses", response_model=ExpenseOut, status_code=201)
def api_create_expense(payload: ExpenseIn, db: Session = Depends(get_db), user: User = Depends(require("expenses.add"))):
    try:
        e = accounting.record_expense(db, payload.category, payload.amount, payload.currency or billing.base_currency(db),
                                      payload.expense_date or date.today(), user=user, department_id=payload.department_id,
                                      vendor=payload.vendor, description=payload.description)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return _exp_out(e)


# --------------------------------------------------------------------------- currencies
@router.get("/currencies", response_model=list[CurrencyOut])
def api_currencies(db: Session = Depends(get_db), user: User = Depends(require("currencies.view"))):
    return [CurrencyOut(code=c.code, name=c.name, symbol=c.symbol or "", rate_to_base=float(c.rate_to_base),
                        is_base=bool(c.is_base), is_active=bool(c.is_active), manual_override=bool(c.manual_override))
            for c in db.query(Currency).order_by(Currency.code).all()]


@router.post("/currencies/{code}/rate", response_model=CurrencyOut)
def api_set_rate(code: str, payload: RateIn, db: Session = Depends(get_db),
                 user: User = Depends(require("currencies.update"))):
    try:
        c = billing.set_exchange_rate(db, code.upper(), payload.rate, user, source=payload.source)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return CurrencyOut(code=c.code, name=c.name, symbol=c.symbol or "", rate_to_base=float(c.rate_to_base),
                       is_base=bool(c.is_base), is_active=bool(c.is_active), manual_override=bool(c.manual_override))
