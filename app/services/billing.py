"""Billing service (Modules 9, 18, 45): currencies, subscriptions, the discount ladder, scholarships,
invoices, payments, receipts and the client ledger.

Money rules
-----------
* Every transaction keeps its own currency; ``*_in_base`` columns hold the consolidated base-currency value.
* ``Currency.rate_to_base`` is "1 unit of this currency = X base currency units".
* Client ledger: ``debit`` increases what the family owes, ``credit`` decreases it.
  ``client_balance`` = sum(debit) - sum(credit); a negative balance is credit the family can spend.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.notify import notify
from app.core.utils import next_code, month_key
from app.models.core import User, Setting, NotificationTemplate, Organization, RiskAlert
from app.models.crm import Referral
from app.models.finance import (Currency, ExchangeRateHistory, Subscription, DiscountRequest, Scholarship, Invoice,
                                InvoiceItem, Payment, Receipt, LedgerEntry)
from app.models.people import Client, Student, Teacher

log = logging.getLogger("oqc.billing")

WEEKS_PER_MONTH = 4.33
INVOICE_TERMS_DAYS = 7
PAYMENT_METHODS = ["bank_transfer", "card", "paypal", "stripe", "cash", "wise", "other"]
GATEWAYS = ["stripe", "paypal", "wise", "manual", "gocardless"]
# ERP vocabulary (docs/AUDIT_ACADEMICS.md 3.7). Legacy values are still accepted: sent == pending, void == cancelled,
# completed == confirmed. Use the *_SET constants in queries so both spellings match.
INVOICE_STATUSES = ["draft", "pending", "confirmed", "partial", "paid", "overdue", "cancelled"]
INVOICE_FILTER_STATUSES = ["pending", "draft", "paid", "confirmed", "cancelled"]  # ERP filter order
INVOICE_PENDING_SET = ("pending", "sent")
INVOICE_CANCELLED_SET = ("cancelled", "void")
INVOICE_OPEN_SET = ("pending", "sent", "confirmed", "partial", "overdue")  # still collectable
INVOICE_OVERALL_PENDING_SET = ("pending", "sent", "overdue", "partial")  # ERP "Over All Pending" tile
PAYMENT_STATUSES = ["pending", "confirmed", "completed", "failed", "refunded", "cancelled"]
RECEIPT_STATUSES = ["pending", "confirmed", "cancelled"]
PAYMENT_CONFIRMED_SET = ("confirmed", "completed")
PAYMENT_CATEGORIES = ["Stripe", "PayPal", "Wise", "UBL", "Meezan Bank", "HBL", "Cash", "Other"]
SUBSCRIPTION_STATUSES = ["pending_approval", "active", "frozen", "cancelled", "expired"]
SUBSCRIPTION_REGULAR_SET = ("active", "regular")
SCHOLARSHIP_TYPES = ["need_based", "merit", "hafiz", "orphan", "staff"]
LEDGER_TYPES = ["charge", "payment", "credit", "refund", "adjustment", "scholarship"]
LEDGER_ADDITION_EFFECTS = [("add", "Add (client owes more)"), ("minus", "Minus (credit to client)")]
FEE_RECURRENCES = ["monthly", "quarterly", "half_yearly", "yearly", "per_class"]


def is_confirmed_payment(status: Optional[str]) -> bool:
    return (status or "") in PAYMENT_CONFIRMED_SET


def normalise_invoice_status(status: Optional[str]) -> str:
    """Map legacy spellings onto the ERP vocabulary."""
    s = (status or "").lower()
    return {"sent": "pending", "void": "cancelled"}.get(s, s)


def normalise_payment_status(status: Optional[str]) -> str:
    s = (status or "").lower()
    return {"completed": "confirmed"}.get(s, s)

INVOICE_DIR = BASE_DIR / "storage" / "invoices"
RECEIPT_DIR = BASE_DIR / "storage" / "receipts"


# ============================================================================ settings & currency
def setting(db: Session, key: str, default=None):
    s = db.query(Setting).filter(Setting.key == key).first()
    if not s or s.value is None:
        return default
    v = s.value
    if isinstance(v, dict) and "value" in v:
        return v["value"]
    return v


def base_currency(db: Session) -> str:
    c = db.query(Currency).filter(Currency.is_base.is_(True)).first()
    return c.code if c else "PKR"


def get_rate(db: Session, currency: str) -> float:
    """1 unit of ``currency`` expressed in base-currency units."""
    if not currency:
        return 1.0
    c = db.query(Currency).filter(Currency.code == currency).first()
    return float(c.rate_to_base) if c else 1.0


def convert_to_base(db: Session, amount: float, currency: str) -> float:
    return round(float(amount or 0) * get_rate(db, currency), 2)


def convert(db: Session, amount: float, from_currency: str, to_currency: str) -> float:
    to_rate = get_rate(db, to_currency) or 1.0
    return round(convert_to_base(db, amount, from_currency) / to_rate, 2)


def set_exchange_rate(db: Session, code: str, rate: float, user: Optional[User], source: str = "manual") -> Currency:
    c = db.query(Currency).filter(Currency.code == code).first()
    if not c:
        raise ValueError(f"Unknown currency {code}")
    if c.is_base and abs(rate - 1.0) > 1e-9:
        raise ValueError("The base currency rate is always 1.00")
    if rate <= 0:
        raise ValueError("Rate must be greater than zero")
    before = {"rate_to_base": float(c.rate_to_base)}
    c.rate_to_base = round(float(rate), 6)
    c.manual_override = source == "manual"
    db.add(ExchangeRateHistory(currency_code=code, rate_to_base=round(float(rate), 6), source=source,
                               set_by_id=user.id if user else None, effective_at=datetime.utcnow()))
    log_action(db, user, "update", "currencies", entity=c, description=f"Exchange rate {code} set to {rate:.4f} {base_currency(db)}",
               before=before, after={"rate_to_base": float(c.rate_to_base)}, consequential=True)
    return c


def country_currency_map(db: Session) -> dict[str, str]:
    from app.services.people import COUNTRY_INFO
    return {row[0]: row[2] for row in COUNTRY_INFO}


# ============================================================================ small helpers
def add_months(d: date, months: int = 1) -> date:
    y, m = d.year, d.month + months
    while m > 12:
        y, m = y + 1, m - 12
    while m < 1:
        y, m = y - 1, m + 12
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return date(y, m, day)


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _tpl(db: Session, event_type: str, channel: str, ctx: dict, fallback_subject: str, fallback_body: str) -> tuple[str, str]:
    t = (db.query(NotificationTemplate)
         .filter(NotificationTemplate.event_type == event_type, NotificationTemplate.channel == channel,
                 NotificationTemplate.is_active.is_(True)).first())
    subject, body = (t.subject, t.body) if t else (fallback_subject, fallback_body)
    for k, v in ctx.items():
        subject = subject.replace("{{" + k + "}}", str(v))
        body = body.replace("{{" + k + "}}", str(v))
    return subject, body


def _billing_reps(db: Session) -> list[User]:
    from app.models.core import Role
    return (db.query(User).join(Role, User.role_id == Role.id)
            .filter(Role.slug.in_(["billing_rep", "hod_finance", "accountant"]), User.is_active.is_(True)).all())


def _notify_client(db: Session, client: Client, event_type: str, title: str, body: str, link: Optional[str] = None,
                   whatsapp: bool = True) -> None:
    if not client or not client.user_id:
        return
    channels = ["in_app"]
    if whatsapp and client.whatsapp_opt_in and client.whatsapp:
        channels.append("whatsapp")
    notify(db, client.user_id, title, body, event_type=event_type, link=link, channels=tuple(channels),
           recipient_address=client.whatsapp or client.email)


# ============================================================================ pricing governance
def discount_tier(db: Session, pct: float) -> str:
    """"none" | "manager" | "ceo" | "prohibited" for a requested discount percentage."""
    pct = float(pct or 0)
    manager_max = float(setting(db, "discount_manager_max_pct", 20) or 20)
    ceo_max = float(setting(db, "discount_ceo_max_pct", 35) or 35)
    if pct <= 0:
        return "none"
    if pct <= manager_max:
        return "manager"
    if pct <= ceo_max:
        return "ceo"
    return "prohibited"


def discount_thresholds(db: Session) -> dict:
    return {"manager_max": float(setting(db, "discount_manager_max_pct", 20) or 20),
            "ceo_max": float(setting(db, "discount_ceo_max_pct", 35) or 35),
            "floor_margin": float(setting(db, "pricing_floor_margin_pct", 20) or 20)}


def teacher_monthly_cost(db: Session, teacher: Optional[Teacher], sessions_per_week: int) -> float:
    """Monthly teacher cost in base currency: per-class rate x sessions/week x 4.33."""
    rate = float(teacher.per_class_rate or 0) if teacher else 0.0
    return round(rate * float(sessions_per_week or 0) * WEEKS_PER_MONTH, 2)


def teacher_cost_floor(db: Session, teacher: Optional[Teacher], sessions_per_week: int) -> float:
    """Minimum acceptable monthly price in base currency = teacher cost + the configured margin."""
    margin = float(setting(db, "pricing_floor_margin_pct", 20) or 20)
    return round(teacher_monthly_cost(db, teacher, sessions_per_week) * (1 + margin / 100.0), 2)


def margin_pct(price_in_base: float, cost_in_base: float) -> float:
    price_in_base = float(price_in_base or 0)
    if price_in_base <= 0:
        return 0.0
    return round(100.0 * (price_in_base - float(cost_in_base or 0)) / price_in_base, 1)


def scholarship_amount_for(scholarship: Optional[Scholarship], list_price: float) -> float:
    if not scholarship:
        return 0.0
    if float(scholarship.monthly_amount or 0) > 0:
        return round(float(scholarship.monthly_amount), 2)
    return round(float(list_price or 0) * float(scholarship.coverage_pct or 0) / 100.0, 2)


# ============================================================================ subscriptions
def create_subscription(db: Session, client: Client, student: Student, package, price: float, currency: str,
                        discount_pct: float, teacher: Optional[Teacher], user: Optional[User],
                        rationale: Optional[str] = None, scholarship: Optional[Scholarship] = None,
                        start_date: Optional[date] = None, sessions_per_week: Optional[int] = None) -> Subscription:
    """Create a subscription, enforcing the discount ladder and the teacher-cost pricing floor.

    Discounts: 0% none · 1-20% manager approval · 21-35% CEO approval · >35% prohibited.
    Scholarships are recorded separately from discounts (dignified financial-aid path).
    """
    list_price = round(float(price or 0), 2)
    discount_pct = round(float(discount_pct or 0), 2)
    tier = discount_tier(db, discount_pct)
    if tier == "prohibited":
        raise ValueError("Discounts above 35% are prohibited")
    if list_price <= 0:
        raise ValueError("List price must be greater than zero")

    spw = int(sessions_per_week or (package.sessions_per_week if package else 3) or 3)
    minutes = int(package.session_minutes if package else 30) or 30
    currency = (currency or client.currency or "GBP").upper()
    start = start_date or date.today()

    discount_amount = round(list_price * discount_pct / 100.0, 2)
    schol_amount = scholarship_amount_for(scholarship, list_price)
    final_price = round(list_price - discount_amount - schol_amount, 2)
    if final_price <= 0:
        raise ValueError("The final price after discount and scholarship must be greater than zero")

    price_in_base = convert_to_base(db, final_price, currency)
    cost_base = teacher_monthly_cost(db, teacher, spw)
    floor = teacher_cost_floor(db, teacher, spw)
    if price_in_base < floor:
        raise ValueError("Price below teacher-cost floor (cost + 20%)")

    sub = Subscription(
        subscription_code=next_code(db, Subscription, "subscription_code", "SUB-"),
        client_id=client.id, student_id=student.id, package_id=package.id if package else None,
        course_id=(package.course_id if package and package.course_id else student.course_id),
        teacher_id=teacher.id if teacher else student.teacher_id, sessions_per_week=spw, session_minutes=minutes,
        list_price=list_price, discount_pct=discount_pct, discount_amount=discount_amount,
        scholarship_id=scholarship.id if scholarship else None, scholarship_amount=schol_amount,
        price=final_price, currency=currency, price_in_base=price_in_base, teacher_cost_base=cost_base,
        billing_cycle=(package.billing_cycle if package else "monthly"), start_date=start,
        next_billing_date=start, status="pending_approval" if tier in ("manager", "ceo") else "active",
        auto_renew=True, created_by_id=user.id if user else None, notes=rationale)
    db.add(sub)
    db.flush()

    if tier in ("manager", "ceo"):
        req = DiscountRequest(subscription_id=sub.id, client_id=client.id, discount_pct=discount_pct, approver_tier=tier,
                              requested_by_id=user.id if user else None, rationale=rationale, status="pending",
                              week=iso_week(start))
        db.add(req)
        db.flush()
        approvers = _discount_approvers(db, tier)
        for a in approvers:
            notify(db, a.id, f"Discount approval needed — {discount_pct:.0f}% for {student.full_name}",
                   f"{sub.subscription_code}: list {currency} {list_price:.2f} -> {currency} {final_price:.2f}. "
                   f"Requested by {user.full_name if user else 'system'}. Reason: {rationale or 'not given'}",
                   event_type="discount_request", link=f"/finance/discounts")
        log_action(db, user, "discount", "discounts", entity=req,
                   description=f"{discount_pct:.0f}% discount requested on {sub.subscription_code} ({tier} tier)",
                   rationale=rationale, consequential=True)

    log_action(db, user, "create", "subscriptions", entity=sub,
               description=(f"Subscription {sub.subscription_code} for {student.full_name}: {currency} {final_price:.2f}/month "
                            f"(list {list_price:.2f}, discount {discount_pct:.0f}%, scholarship {schol_amount:.2f})"),
               rationale=rationale, after=snapshot(sub), consequential=True)
    return sub


def _discount_approvers(db: Session, tier: str) -> list[User]:
    from app.models.core import Role
    slugs = ["super_admin"] if tier == "ceo" else ["manager", "hod_finance", "super_admin"]
    return db.query(User).join(Role, User.role_id == Role.id).filter(Role.slug.in_(slugs), User.is_active.is_(True)).all()


def approve_discount(db: Session, request: DiscountRequest, user: User, approve: bool, note: Optional[str] = None) -> DiscountRequest:
    """Approve or reject a discount request. Managers may only approve the manager tier."""
    if request.status != "pending":
        raise ValueError(f"This request was already {request.status}")
    if request.approver_tier == "ceo" and not rbac.is_ceo(user):
        raise ValueError("Discounts above the manager threshold can only be approved by the CEO")
    if not rbac.has_permission(user, "discounts.approve"):
        raise ValueError("You do not have permission to approve discounts")
    if not note:
        raise ValueError("An approval note / rationale is required")

    request.status = "approved" if approve else "rejected"
    request.approved_by_id = user.id
    request.decided_at = datetime.utcnow()
    request.decision_note = note
    sub = request.subscription
    if sub:
        if approve:
            sub.status = "active"
            sub.next_billing_date = sub.next_billing_date or sub.start_date
            if sub.student and sub.student.status in ("trial", "frozen"):
                sub.student.status = "active"
        else:
            sub.status = "cancelled"
            sub.cancelled_at = date.today()
            sub.cancel_reason = f"Discount rejected: {note}"[:200]
    if request.requested_by_id:
        notify(db, request.requested_by_id,
               f"Discount {request.status}: {request.discount_pct:.0f}%",
               f"{(sub.subscription_code + ' — ') if sub else ''}{user.full_name} {request.status} the request. Note: {note}",
               event_type="discount_decision", link=f"/finance/subscriptions/{sub.id}" if sub else "/finance/discounts")
    log_action(db, user, "approve" if approve else "reject", "discounts", entity=request,
               description=f"{request.discount_pct:.0f}% discount {request.status} ({request.approver_tier} tier)"
                           + (f" on {sub.subscription_code}" if sub else ""),
               rationale=note, consequential=True)
    return request


def activate_subscription(db: Session, sub: Subscription, user: Optional[User]) -> Subscription:
    pending = db.query(DiscountRequest).filter(DiscountRequest.subscription_id == sub.id, DiscountRequest.status == "pending").first()
    if pending:
        raise ValueError(f"A {pending.approver_tier}-tier discount approval is still pending")
    sub.status = "active"
    sub.freeze_start = sub.freeze_end = None
    sub.next_billing_date = sub.next_billing_date or date.today()
    if sub.student and sub.student.status in ("frozen", "trial"):
        sub.student.status = "active"
    log_action(db, user, "update", "subscriptions", entity=sub, description=f"{sub.subscription_code} activated")
    return sub


def freeze_subscription(db: Session, sub: Subscription, start: date, end: Optional[date], user: Optional[User], reason: str) -> Subscription:
    if sub.status not in ("active", "pending_approval"):
        raise ValueError(f"A {sub.status} subscription cannot be frozen")
    if not reason:
        raise ValueError("A reason is required to freeze a subscription")
    before = {"status": sub.status}
    sub.status = "frozen"
    sub.freeze_start = start
    sub.freeze_end = end
    if end:
        sub.next_billing_date = end + timedelta(days=1)
    if sub.student:
        sub.student.status = "frozen"
    log_action(db, user, "freeze", "subscriptions", entity=sub,
               description=f"{sub.subscription_code} frozen {start} to {end or 'open ended'}", rationale=reason,
               before=before, after={"status": "frozen"}, consequential=True)
    _notify_client(db, sub.client, "subscription_frozen", "Subscription paused",
                   f"{sub.student.full_name if sub.student else 'Your student'}'s classes are paused from {start}. "
                   f"We will resume on {end or 'your chosen date'} in sha Allah.", link="/portal/billing")
    return sub


def unfreeze_subscription(db: Session, sub: Subscription, user: Optional[User], note: Optional[str] = None) -> Subscription:
    if sub.status != "frozen":
        raise ValueError("This subscription is not frozen")
    sub.status = "active"
    sub.freeze_start = sub.freeze_end = None
    sub.next_billing_date = date.today()
    if sub.student:
        sub.student.status = "active"
    log_action(db, user, "update", "subscriptions", entity=sub, description=f"{sub.subscription_code} resumed", rationale=note)
    return sub


def cancel_subscription(db: Session, sub: Subscription, user: Optional[User], reason: str) -> Subscription:
    if sub.status == "cancelled":
        raise ValueError("This subscription is already cancelled")
    if not reason:
        raise ValueError("A cancellation reason is required")
    before = {"status": sub.status}
    sub.status = "cancelled"
    sub.cancelled_at = date.today()
    sub.cancel_reason = reason[:200]
    sub.auto_renew = False
    sub.end_date = sub.end_date or date.today()
    if sub.student:
        sub.student.status = "cancelled"
        sub.student.cancelled_at = date.today()
        sub.student.cancel_reason = reason[:200]
    log_action(db, user, "cancel", "subscriptions", entity=sub, description=f"{sub.subscription_code} cancelled",
               rationale=reason, before=before, after={"status": "cancelled"}, consequential=True)
    _notify_client(db, sub.client, "subscription_cancelled", "Subscription cancelled",
                   f"We have cancelled {sub.student.full_name if sub.student else 'the'} subscription {sub.subscription_code}. "
                   "The door remains open whenever you wish to return.", link="/portal/billing")
    return sub


def renew_subscription(db: Session, sub: Subscription, user: Optional[User]) -> Subscription:
    if sub.status not in ("active", "expired", "cancelled"):
        raise ValueError(f"A {sub.status} subscription cannot be renewed")
    sub.status = "active"
    sub.end_date = None
    sub.cancelled_at = None
    sub.auto_renew = True
    base = sub.next_billing_date or date.today()
    sub.next_billing_date = add_months(max(base, date.today()), 1)
    if sub.student and sub.student.status in ("cancelled", "frozen"):
        sub.student.status = "active"
    log_action(db, user, "update", "subscriptions", entity=sub,
               description=f"{sub.subscription_code} renewed; next billing {sub.next_billing_date}")
    return sub


def change_package(db: Session, sub: Subscription, package, user: Optional[User], rationale: str,
                   price: Optional[float] = None, sessions_per_week: Optional[int] = None) -> Subscription:
    if not rationale:
        raise ValueError("A rationale is required to change the package")
    before = snapshot(sub)
    spw = int(sessions_per_week or (package.sessions_per_week if package else sub.sessions_per_week))
    list_price = round(float(price if price is not None else (package.price if package else sub.list_price)), 2)
    discount_amount = round(list_price * float(sub.discount_pct or 0) / 100.0, 2)
    schol = round(float(sub.scholarship_amount or 0), 2)
    final = round(list_price - discount_amount - schol, 2)
    if final <= 0:
        raise ValueError("The new price must be greater than zero")
    price_base = convert_to_base(db, final, sub.currency)
    floor = teacher_cost_floor(db, sub.teacher, spw)
    if price_base < floor:
        raise ValueError("Price below teacher-cost floor (cost + 20%)")
    sub.package_id = package.id if package else None
    sub.course_id = (package.course_id if package and package.course_id else sub.course_id)
    sub.sessions_per_week = spw
    sub.session_minutes = int(package.session_minutes if package else sub.session_minutes)
    sub.list_price = list_price
    sub.discount_amount = discount_amount
    sub.price = final
    sub.price_in_base = price_base
    sub.teacher_cost_base = teacher_monthly_cost(db, sub.teacher, spw)
    log_action(db, user, "update", "subscriptions", entity=sub,
               description=f"{sub.subscription_code} package changed to {package.name if package else 'custom'} ({sub.currency} {final:.2f})",
               rationale=rationale, before=before, after=snapshot(sub), consequential=True)
    return sub


def mrr_in_base(db: Session) -> float:
    total = (db.query(func.coalesce(func.sum(Subscription.price_in_base), 0))
             .filter(Subscription.status == "active").scalar() or 0)
    return round(float(total), 2)


def subscription_stats(db: Session) -> dict:
    counts = dict(db.query(Subscription.status, func.count(Subscription.id)).group_by(Subscription.status).all())
    start_month = date.today().replace(day=1)
    churned = db.query(Subscription).filter(Subscription.status == "cancelled", Subscription.cancelled_at >= start_month).count()
    by_currency = (db.query(Subscription.currency, func.count(Subscription.id), func.coalesce(func.sum(Subscription.price), 0),
                            func.coalesce(func.sum(Subscription.price_in_base), 0))
                   .filter(Subscription.status == "active").group_by(Subscription.currency).all())
    return {"active": counts.get("active", 0), "pending": counts.get("pending_approval", 0), "frozen": counts.get("frozen", 0),
            "cancelled": counts.get("cancelled", 0), "churned_this_month": churned, "total": sum(counts.values()),
            "mrr": mrr_in_base(db), "base": base_currency(db),
            "by_currency": [{"currency": c, "count": n, "amount": float(a), "base": float(b)} for c, n, a, b in by_currency]}


# ============================================================================ ledger
def client_balance(db: Session, client: Client) -> float:
    row = (db.query(func.coalesce(func.sum(LedgerEntry.debit), 0), func.coalesce(func.sum(LedgerEntry.credit), 0))
           .filter(LedgerEntry.client_id == client.id).first())
    return round(float(row[0]) - float(row[1]), 2)


def available_credit(db: Session, client: Client) -> float:
    return round(max(0.0, -client_balance(db, client)), 2)


def client_statement(db: Session, client: Client) -> list[LedgerEntry]:
    return (db.query(LedgerEntry).filter(LedgerEntry.client_id == client.id)
            .order_by(LedgerEntry.entry_date, LedgerEntry.id).all())


def _post_ledger(db: Session, client: Client, entry_type: str, description: str, debit: float = 0, credit: float = 0,
                 currency: Optional[str] = None, reference_type: Optional[str] = None, reference_id: Optional[int] = None,
                 user: Optional[User] = None, entry_date: Optional[date] = None) -> LedgerEntry:
    balance = client_balance(db, client) + round(float(debit or 0), 2) - round(float(credit or 0), 2)
    e = LedgerEntry(client_id=client.id, entry_date=entry_date or date.today(), entry_type=entry_type,
                    description=description[:250], debit=round(float(debit or 0), 2), credit=round(float(credit or 0), 2),
                    currency=(currency or client.currency or "GBP"), balance_after=round(balance, 2),
                    reference_type=reference_type, reference_id=reference_id, created_by_id=user.id if user else None)
    db.add(e)
    db.flush()
    return e


def post_credit(db: Session, client: Client, amount: float, currency: str, description: str,
                reference_type: Optional[str] = None, reference_id: Optional[int] = None,
                user: Optional[User] = None) -> LedgerEntry:
    """Issue account credit to a family (referral reward, goodwill, service recovery)."""
    amount = round(float(amount or 0), 2)
    if amount <= 0:
        raise ValueError("Credit amount must be greater than zero")
    entry = _post_ledger(db, client, "credit", description, credit=amount, currency=currency or client.currency,
                         reference_type=reference_type or "manual", reference_id=reference_id, user=user)
    log_action(db, user, "create", "ledger", entity=entry,
               description=f"Credit {currency or client.currency} {amount:.2f} issued to {client.client_code}: {description}",
               rationale=description, consequential=True)
    _notify_client(db, client, "credit_issued", "Account credit applied",
                   f"A credit of {currency or client.currency} {amount:.2f} has been added to your account. {description}",
                   link="/portal/billing", whatsapp=False)
    return entry


def post_adjustment(db: Session, client: Client, amount: float, currency: str, description: str, user: Optional[User],
                    rationale: str, direction: str = "debit") -> LedgerEntry:
    """Manual ledger adjustment (consequential). ``direction`` debit = client owes more, credit = owes less."""
    amount = round(float(amount or 0), 2)
    if amount <= 0:
        raise ValueError("Adjustment amount must be greater than zero")
    if not rationale:
        raise ValueError("A rationale is required for a manual ledger adjustment")
    kwargs = {"debit": amount} if direction == "debit" else {"credit": amount}
    entry = _post_ledger(db, client, "adjustment", description, currency=currency or client.currency,
                         reference_type="manual", user=user, **kwargs)
    log_action(db, user, "update", "ledger", entity=entry,
               description=f"Manual {direction} adjustment {currency or client.currency} {amount:.2f} on {client.client_code}: {description}",
               rationale=rationale, consequential=True)
    return entry


def ledger_report(db: Session, client: Client, date_from: Optional[date] = None, date_to: Optional[date] = None,
                  entry_type: Optional[str] = None) -> dict:
    """ERP "Client Ledger Report": rows (Srl, Date, Transaction Type, Description, signed Amount, Balance) with a
    Previous Balance (opening balance + movements before ``date_from``), Total and In Words footer."""
    entries = client_statement(db, client)
    opening = round(float(client.opening_balance or 0), 2)
    previous = opening
    rows = []
    running = opening
    total = 0.0
    for e in entries:
        signed = round(float(e.debit or 0) - float(e.credit or 0), 2)
        if date_from and e.entry_date < date_from:
            previous = round(previous + signed, 2)
            running = previous
            continue
        if date_to and e.entry_date > date_to:
            continue
        if entry_type and e.entry_type != entry_type:
            continue
        running = round(running + signed, 2)
        total = round(total + signed, 2)
        rows.append({"srl": len(rows) + 1, "entry": e, "date": e.entry_date, "type": e.entry_type,
                     "description": e.description, "amount": signed, "balance": running})
    closing = round(previous + total, 2)
    currency = client.currency or "GBP"
    return {"client": client, "rows": rows, "opening_balance": opening, "previous_balance": previous, "total": total,
            "closing_balance": closing, "currency": currency, "base": base_currency(db),
            "closing_base": convert_to_base(db, closing, currency),
            "in_words": number_to_words(closing, currency),
            "in_words_base": number_to_words(convert_to_base(db, closing, currency), base_currency(db)),
            "date_from": date_from, "date_to": date_to, "all_count": len(entries)}


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
CURRENCY_WORDS = {"PKR": ("Rupees", "Paisa"), "GBP": ("Pounds", "Pence"), "USD": ("Dollars", "Cents"),
                  "EUR": ("Euros", "Cents"), "CAD": ("Dollars", "Cents"), "AUD": ("Dollars", "Cents"),
                  "AED": ("Dirhams", "Fils"), "SAR": ("Riyals", "Halalas"), "INR": ("Rupees", "Paise"),
                  "MYR": ("Ringgit", "Sen"), "NOK": ("Kroner", "Ore"), "SEK": ("Kronor", "Ore")}


def _words_below_thousand(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(f"{_ONES[n // 100]} Hundred")
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else ""))
    elif n > 0:
        parts.append(_ONES[n])
    return " ".join(parts)


def integer_to_words(n: int) -> str:
    """English words for a non-negative integer (short scale, up to trillions)."""
    n = int(abs(n))
    if n == 0:
        return "Zero"
    scales = [(10 ** 12, "Trillion"), (10 ** 9, "Billion"), (10 ** 6, "Million"), (1000, "Thousand")]
    parts = []
    for value, name in scales:
        if n >= value:
            parts.append(f"{_words_below_thousand(n // value)} {name}")
            n %= value
    if n:
        parts.append(_words_below_thousand(n))
    return " ".join(parts)


def number_to_words(amount: float, currency: str = "") -> str:
    """Amount in words for the ledger report footer, e.g. "Two Thousand Five Hundred Rupees and Fifty Paisa Only"."""
    value = round(abs(float(amount or 0)), 2)
    major = int(value)
    minor = int(round((value - major) * 100))
    unit, sub = CURRENCY_WORDS.get((currency or "").upper(), ((currency or "").upper() or "Units", "Cents"))
    text = f"{integer_to_words(major)} {unit}"
    if minor:
        text += f" and {integer_to_words(minor)} {sub}"
    if float(amount or 0) < 0:
        text = "Credit " + text
    return text + " Only"


# ============================================================================ ledger additions (ERP)
def create_ledger_addition(db: Session, client: Client, amount: float, currency: Optional[str], addition_type: str,
                           effect: str, addition_date: Optional[date], user: Optional[User],
                           reference_employee=None, billing_rep: Optional[User] = None, remarks: Optional[str] = None,
                           currency_rate: Optional[float] = None, lc_amount: Optional[float] = None, status: str = "pending"):
    """Record a Ledger Addition (Teacher Gift, Leave Discount, Referral Bonus, Late Fee, Adjustment, Penalty)."""
    from app.models.erp import LedgerAddition, LEDGER_ADDITION_TYPES
    amount = round(float(amount or 0), 2)
    if amount <= 0:
        raise ValueError("Amount must be greater than zero")
    if addition_type not in LEDGER_ADDITION_TYPES:
        raise ValueError(f"Unknown ledger addition type {addition_type}")
    effect = (effect or "minus").lower()
    if effect not in ("add", "minus"):
        raise ValueError("Effect must be Add or Minus")
    currency = (currency or client.currency or "GBP").upper()
    rate = float(currency_rate) if currency_rate else get_rate(db, currency)
    lc = round(float(lc_amount), 2) if lc_amount else round(amount * rate, 2)
    la = LedgerAddition(client_id=client.id, currency=currency, currency_rate=round(rate, 6), amount=amount, lc_amount=lc,
                        addition_type=addition_type, effect=effect, addition_date=addition_date or date.today(),
                        status="pending", reference_employee_id=reference_employee.id if reference_employee else None,
                        billing_rep_id=(billing_rep.id if billing_rep else (client.billing_rep_id or (user.id if user else None))),
                        remarks=remarks, created_by_id=user.id if user else None)
    db.add(la)
    db.flush()
    log_action(db, user, "create", "ledger", entity=la,
               description=f"Ledger addition #{la.id} {addition_type} {effect} {currency} {amount:.2f} for {client.client_code}",
               rationale=remarks)
    if status == "confirmed":
        post_ledger_addition(db, la, user)
    return la


def post_ledger_addition(db: Session, addition, user: Optional[User]):
    """Confirm a pending Ledger Addition: posts the LedgerEntry (Add = debit adjustment, Minus = credit)."""
    if addition.status != "pending":
        raise ValueError(f"A {addition.status} ledger addition cannot be confirmed")
    client = addition.client
    amount = round(float(addition.amount or 0), 2)
    desc = f"{addition.addition_type}" + (f": {addition.remarks}" if addition.remarks else "")
    if addition.effect == "add":
        entry = _post_ledger(db, client, "adjustment", desc, debit=amount, currency=addition.currency,
                             reference_type="ledger_addition", reference_id=addition.id, user=user,
                             entry_date=addition.addition_date)
    else:
        entry = _post_ledger(db, client, "credit", desc, credit=amount, currency=addition.currency,
                             reference_type="ledger_addition", reference_id=addition.id, user=user,
                             entry_date=addition.addition_date)
    addition.ledger_entry_id = entry.id
    addition.status = "confirmed"
    addition.confirmed_by_id = user.id if user else None
    addition.confirmed_at = datetime.utcnow()
    log_action(db, user, "approve", "ledger", entity=addition,
               description=f"Ledger addition #{addition.id} confirmed: {addition.effect} {addition.currency} {amount:.2f} "
                           f"({addition.addition_type}) posted to {client.client_code}",
               rationale=addition.remarks, before={"status": "pending"}, after={"status": "confirmed"}, consequential=True)
    if addition.effect == "minus":
        _notify_client(db, client, "credit_issued", "Account credit applied",
                       f"A credit of {addition.currency} {amount:.2f} ({addition.addition_type}) has been added to your account.",
                       link="/portal/billing", whatsapp=False)
    return addition


def cancel_ledger_addition(db: Session, addition, user: Optional[User], reason: str):
    """Cancel a Ledger Addition; a confirmed one is reversed with an opposite ledger entry."""
    if not reason:
        raise ValueError("A reason is required to cancel a ledger addition")
    if addition.status == "cancelled":
        raise ValueError("This ledger addition is already cancelled")
    before = {"status": addition.status}
    amount = round(float(addition.amount or 0), 2)
    if addition.status == "confirmed" and addition.ledger_entry_id:
        desc = f"Reversal of {addition.addition_type} #{addition.id}: {reason}"[:250]
        if addition.effect == "add":
            _post_ledger(db, addition.client, "credit", desc, credit=amount, currency=addition.currency,
                         reference_type="ledger_addition", reference_id=addition.id, user=user)
        else:
            _post_ledger(db, addition.client, "adjustment", desc, debit=amount, currency=addition.currency,
                         reference_type="ledger_addition", reference_id=addition.id, user=user)
    addition.status = "cancelled"
    addition.remarks = ((addition.remarks or "") + f"\nCancelled: {reason}").strip()
    log_action(db, user, "cancel", "ledger", entity=addition,
               description=f"Ledger addition #{addition.id} cancelled", rationale=reason, before=before,
               after={"status": "cancelled"}, consequential=True)
    return addition


def ledger_addition_tiles(db: Session) -> dict:
    from app.models.erp import LedgerAddition
    counts = dict(db.query(LedgerAddition.status, func.count(LedgerAddition.id)).group_by(LedgerAddition.status).all())
    return {"tile_pending": counts.get("pending", 0), "tile_confirmed": counts.get("confirmed", 0),
            "tile_cancelled": counts.get("cancelled", 0)}


def credits_report(db: Session) -> dict:
    rows = (db.query(LedgerEntry).filter(LedgerEntry.entry_type == "credit")
            .order_by(LedgerEntry.entry_date.desc(), LedgerEntry.id.desc()).limit(400).all())
    referral_ids = {r.ambassador_credit_ledger_id for r in db.query(Referral).all()} | \
                   {r.referred_credit_ledger_id for r in db.query(Referral).all()}
    total = round(sum(float(r.credit or 0) * get_rate(db, r.currency) for r in rows), 2)
    referral_total = round(sum(float(r.credit or 0) * get_rate(db, r.currency) for r in rows if r.id in referral_ids), 2)
    return {"entries": rows, "total_base": total, "referral_base": referral_total, "referral_ids": referral_ids,
            "base": base_currency(db)}


# ============================================================================ invoices
def next_invoice_number(db: Session, on: Optional[date] = None) -> str:
    year = (on or date.today()).year
    prefix = f"INV-{year}-"
    last = (db.query(Invoice.invoice_number).filter(Invoice.invoice_number.like(prefix + "%"))
            .order_by(Invoice.invoice_number.desc()).first())
    n = (int(last[0].rsplit("-", 1)[-1]) + 1) if last else 1
    while db.query(Invoice.id).filter(Invoice.invoice_number == f"{prefix}{n:05d}").first():
        n += 1
    return f"{prefix}{n:05d}"


def invoice_for_period(db: Session, sub: Subscription, period_start: date) -> Optional[Invoice]:
    return (db.query(Invoice).filter(Invoice.subscription_id == sub.id, Invoice.period_start == period_start,
                                     Invoice.status.notin_(INVOICE_CANCELLED_SET)).first())


def client_invoice_for_period(db: Session, client: Client, period_start: date, bulk_only: bool = True) -> Optional[Invoice]:
    """The (bulk) invoice already raised for a family for a billing period, if any."""
    q = db.query(Invoice).filter(Invoice.client_id == client.id, Invoice.period_start == period_start,
                                 Invoice.status.notin_(INVOICE_CANCELLED_SET))
    if bulk_only:
        q = q.filter(Invoice.is_bulk.is_(True))
    return q.order_by(Invoice.id).first()


def invoice_charge_entry(db: Session, invoice: Invoice) -> Optional[LedgerEntry]:
    return (db.query(LedgerEntry).filter(LedgerEntry.reference_type == "invoice", LedgerEntry.reference_id == invoice.id,
                                         LedgerEntry.entry_type == "charge").first())


def invoice_gross(invoice: Invoice) -> float:
    """Amount charged to the family ledger: total before account credit."""
    return round(float(invoice.subtotal or 0) - float(invoice.discount or 0) + float(invoice.tax or 0), 2)


def _recompute_invoice_totals(db: Session, inv: Invoice) -> None:
    inv.total = round(invoice_gross(inv) - float(inv.credit_applied or 0), 2)
    inv.total_in_base = convert_to_base(db, inv.total, inv.currency)


def active_addition_rules(db: Session, on: date, client_id: Optional[int] = None,
                          subscription_ids: Optional[list[int]] = None, auto_only: bool = True) -> list:
    """Active Invoice Additions Master rows applicable on ``on`` for a family / its subscriptions."""
    from app.models.erp import InvoiceAdditionRule
    rows = (db.query(InvoiceAdditionRule).filter(InvoiceAdditionRule.status == "active",
                                                 InvoiceAdditionRule.from_date <= on).all())
    out = []
    for r in rows:
        if r.to_date and r.to_date < on:
            continue
        if auto_only and not r.auto_assigned:
            continue
        if not r.addition_type or r.addition_type.status != "active":
            continue
        if r.level == "global":
            out.append(r)
        elif r.level == "client" and client_id and r.client_id == client_id:
            out.append(r)
        elif r.level == "subscription" and subscription_ids and r.subscription_id in subscription_ids:
            out.append(r)
    return out


def add_invoice_addition(db: Session, inv: Invoice, addition_type, amount: float, note: str = "") -> Optional[InvoiceItem]:
    """Append a discount / charge / tax line from an InvoiceAdditionType and update the invoice sums."""
    amount = round(abs(float(amount or 0)), 2)
    if amount <= 0 or addition_type is None:
        return None
    kind = (addition_type.addition_type or "charge").lower()
    label = f"{addition_type.description}" + (f" — {note}" if note else "")
    if kind == "discount":
        inv.discount = round(float(inv.discount or 0) + amount, 2)
        signed = -amount
    elif kind == "tax":
        inv.tax = round(float(inv.tax or 0) + amount, 2)
        signed = amount
    else:
        inv.subtotal = round(float(inv.subtotal or 0) + amount, 2)
        signed = amount
    item = InvoiceItem(invoice_id=inv.id, description=label[:250], quantity=1, unit_price=signed, amount=signed)
    db.add(item)
    db.flush()
    return item


def apply_addition_rules(db: Session, invoice: Invoice, subscription_ids: Optional[list[int]] = None) -> list[InvoiceItem]:
    """Apply the auto-assigned Invoice Additions Master rules (global / client / subscription level) to an invoice.

    Fixed rules add their amount; percent rules are a percentage of the subscription total. Returns the lines added.
    Totals are recomputed but the ledger charge is NOT touched (call before posting, or via confirm_invoice)."""
    if subscription_ids is None:
        subscription_ids = [invoice.subscription_id] if invoice.subscription_id else []
    rules = active_addition_rules(db, invoice.issue_date or date.today(), client_id=invoice.client_id,
                                  subscription_ids=subscription_ids)
    added: list[InvoiceItem] = []
    base_amount = float(invoice.subs_total or invoice.subtotal or 0)
    for r in rules:
        if r.implementation_type == "percent":
            amount = round(base_amount * float(r.amount or 0) / 100.0, 2)
            note = f"{float(r.amount):g}% ({r.level})"
        else:
            amount = round(float(r.amount or 0), 2)
            note = f"{r.level} rule"
        item = add_invoice_addition(db, invoice, r.addition_type, amount, note)
        if item:
            added.append(item)
    _recompute_invoice_totals(db, invoice)
    return added


def create_client_invoice(db: Session, client: Client, subscriptions: list[Subscription], period_start: date,
                          period_end: date, user: Optional[User] = None, issue_date: Optional[date] = None,
                          due_date: Optional[date] = None, status: str = "pending", is_bulk: bool = False,
                          additions: Optional[list[tuple]] = None, remarks: Optional[str] = None,
                          apply_rules: bool = True, notify_family: bool = True) -> Invoice:
    """Raise one invoice for a family covering one or more subscriptions (ERP "Create Single Invoice" / bulk).

    * one line per subscription (list price) plus its discount / scholarship lines -> subs_total / subs_discount
    * auto-assigned Invoice Additions Master rules and any manual ``additions`` [(InvoiceAdditionType, amount, note)]
    * available account credit is applied, the ledger charge is posted unless the invoice stays a draft
    """
    if not subscriptions:
        raise ValueError("Select at least one subscription to invoice")
    status = normalise_invoice_status(status) or "pending"
    if status not in ("draft", "pending", "confirmed"):
        raise ValueError("A new invoice can only be a draft, pending or confirmed")
    for s in subscriptions:
        if s.client_id != client.id:
            raise ValueError(f"{s.subscription_code} does not belong to {client.client_code}")
        if s.status == "cancelled":
            raise ValueError(f"{s.subscription_code} is cancelled and cannot be invoiced")
    currencies = {s.currency for s in subscriptions}
    if len(currencies) > 1:
        raise ValueError("All subscriptions on one invoice must share a currency")
    currency = currencies.pop() or client.currency or "GBP"
    issued = issue_date or date.today()
    due = due_date or issued + timedelta(days=INVOICE_TERMS_DAYS)

    inv = Invoice(invoice_number=next_invoice_number(db, issued), client_id=client.id,
                  student_id=subscriptions[0].student_id if len(subscriptions) == 1 else None,
                  subscription_id=subscriptions[0].id if len(subscriptions) == 1 else None,
                  issue_date=issued, due_date=due, period_start=period_start, period_end=period_end, currency=currency,
                  subtotal=0, discount=0, credit_applied=0, tax=0, total=0, paid_amount=0, total_in_base=0,
                  status=status, is_bulk=is_bulk, remarks=remarks or None,
                  billing_rep_id=(client.billing_rep_id or (user.id if user else None)),
                  sent_at=datetime.utcnow() if status != "draft" else None,
                  confirmed_by_id=user.id if (user and status == "confirmed") else None,
                  confirmed_at=datetime.utcnow() if status == "confirmed" else None)
    db.add(inv)
    db.flush()

    subs_total = subs_discount = 0.0
    for s in subscriptions:
        list_price = round(float(s.list_price or s.price or 0), 2)
        pkg_name = s.package.name if s.package else (s.course.name if s.course else "Quran tuition")
        who = f" — {s.student.full_name}" if s.student else ""
        db.add(InvoiceItem(invoice_id=inv.id, quantity=1, unit_price=list_price, amount=list_price,
                           description=(f"{pkg_name}{who} — {s.sessions_per_week} x {s.session_minutes} min/week — "
                                        f"{period_start:%d %b} to {period_end:%d %b %Y}")[:250]))
        subs_total += list_price
        if float(s.discount_amount or 0) > 0:
            d = round(float(s.discount_amount), 2)
            db.add(InvoiceItem(invoice_id=inv.id, description=f"Approved discount ({float(s.discount_pct or 0):.0f}%){who}"[:250],
                               quantity=1, unit_price=-d, amount=-d))
            subs_discount += d
        if float(s.scholarship_amount or 0) > 0:
            sa = round(float(s.scholarship_amount), 2)
            db.add(InvoiceItem(invoice_id=inv.id, description=f"Scholarship award (financial aid){who}"[:250],
                               quantity=1, unit_price=-sa, amount=-sa))
            subs_discount += sa
    inv.subs_total = round(subs_total, 2)
    inv.subs_discount = round(subs_discount, 2)
    inv.subs_tax = 0
    inv.subtotal = round(subs_total, 2)
    inv.discount = round(subs_discount, 2)
    inv.tax = 0
    db.flush()

    sub_ids = [s.id for s in subscriptions]
    if apply_rules:
        apply_addition_rules(db, inv, sub_ids)
    for entry in additions or []:
        atype, amount = entry[0], entry[1]
        note = entry[2] if len(entry) > 2 else ""
        add_invoice_addition(db, inv, atype, amount, note)

    gross = invoice_gross(inv)
    credit = round(min(available_credit(db, client), max(gross, 0.0)), 2)
    if credit > 0:
        inv.credit_applied = credit
        db.add(InvoiceItem(invoice_id=inv.id, description="Account credit applied", quantity=1, unit_price=-credit, amount=-credit))
    _recompute_invoice_totals(db, inv)
    db.flush()

    if status != "draft":
        _post_ledger(db, client, "charge", f"Invoice {inv.invoice_number} — {period_start:%b %Y}", debit=gross,
                     currency=currency, reference_type="invoice", reference_id=inv.id, user=user, entry_date=issued)
    for s in subscriptions:
        s.next_billing_date = add_months(period_start, 1)

    try:
        generate_invoice_pdf(db, inv)
    except Exception as exc:  # pragma: no cover - PDF must never block billing
        log.warning("invoice PDF failed for %s: %s", inv.invoice_number, exc)

    if status != "draft" and notify_family:
        subject, body = _tpl(db, "invoice_issued", "whatsapp",
                             {"number": inv.invoice_number, "name": client.full_name,
                              "amount": f"{currency} {float(inv.total):,.2f}", "due": inv.due_date.strftime("%d %b %Y"),
                              "link": "/portal/billing"},
                             f"Invoice {inv.invoice_number}",
                             f"Invoice {inv.invoice_number} for {currency} {float(inv.total):,.2f} is due {inv.due_date:%d %b %Y}.")
        _notify_client(db, client, "invoice_issued", subject, body, link="/portal/billing")
    log_action(db, user, "create", "billing", entity=inv,
               description=(f"Invoice {inv.invoice_number} ({'bulk' if is_bulk else 'single'}, {status}) raised for "
                            f"{client.client_code}: {len(subscriptions)} subscription(s), {currency} {float(inv.total):.2f}"
                            + (f", credit {credit:.2f} applied" if credit else "")),
               after=snapshot(inv))
    return inv


def generate_invoice(db: Session, subscription: Subscription, period_start: date, period_end: date,
                     user: Optional[User] = None, issue_date: Optional[date] = None, send: bool = True) -> Invoice:
    """Issue the monthly invoice for one subscription (legacy entry point; delegates to ``create_client_invoice``)."""
    existing = invoice_for_period(db, subscription, period_start)
    if existing:
        raise ValueError(f"Invoice {existing.invoice_number} already covers {period_start:%b %Y} for {subscription.subscription_code}")
    if subscription.status in ("cancelled",):
        raise ValueError("A cancelled subscription cannot be invoiced")
    return create_client_invoice(db, subscription.client, [subscription], period_start, period_end, user=user,
                                 issue_date=issue_date, status="pending" if send else "draft", is_bulk=False)


def bulk_generate_invoices(db: Session, user: Optional[User], period: str, shift: Optional[str] = None,
                           recurrence: Optional[str] = None, dry_run: bool = False) -> dict:
    """ERP "Generate Bulk Invoices": one invoice per family for every regular subscription due in ``period``.

    Idempotent per family + period (families that already hold a bulk invoice for the month are skipped) and
    filterable by the family's shift (morning / night) and fee recurrence. ``dry_run`` only counts.
    """
    from app.core.utils import month_bounds
    start, end = month_bounds(period)
    shift = (shift or "").lower()
    recurrence = (recurrence or "").lower()
    subs = (db.query(Subscription).join(Client, Subscription.client_id == Client.id)
            .filter(Subscription.status.in_(SUBSCRIPTION_REGULAR_SET),
                    Subscription.start_date <= end,
                    (Subscription.next_billing_date.is_(None)) | (Subscription.next_billing_date <= end))
            .order_by(Client.client_code, Subscription.id).all())
    by_client: dict[int, list[Subscription]] = defaultdict(list)
    for s in subs:
        c = s.client
        if not c:
            continue
        if shift and shift != "all" and (c.shift or "night") != shift:
            continue
        if recurrence and recurrence != "all" and (c.fee_recurrence or "monthly") != recurrence:
            continue
        if invoice_for_period(db, s, start):
            continue
        by_client[c.id].append(s)

    result = {"period": period, "start": start, "end": end, "clients": 0, "subscriptions": 0, "skipped": 0,
              "created": [], "total_base": 0.0, "dry_run": dry_run, "preview": []}
    for cid, group in by_client.items():
        client = group[0].client
        if client_invoice_for_period(db, client, start):
            result["skipped"] += 1
            continue
        # one invoice per currency (families almost always have one)
        by_ccy: dict[str, list[Subscription]] = defaultdict(list)
        for s in group:
            by_ccy[s.currency].append(s)
        for ccy, part in by_ccy.items():
            est = round(sum(float(s.price or 0) for s in part), 2)
            result["clients"] += 1
            result["subscriptions"] += len(part)
            result["preview"].append({"client": client, "currency": ccy, "count": len(part), "amount": est,
                                      "base": convert_to_base(db, est, ccy)})
            result["total_base"] = round(result["total_base"] + convert_to_base(db, est, ccy), 2)
            if dry_run:
                continue
            try:
                inv = create_client_invoice(db, client, part, start, end, user=user, issue_date=date.today(),
                                            status="pending", is_bulk=True)
                result["created"].append(inv)
            except ValueError as exc:
                log.warning("bulk invoice skipped for %s: %s", client.client_code, exc)
                result["skipped"] += 1
    if not dry_run:
        log_action(db, user, "execute", "billing", entity_type="Invoice",
                   description=(f"Bulk invoices generated for {period}: {len(result['created'])} created, "
                                f"{result['skipped']} skipped" + (f", shift {shift}" if shift and shift != 'all' else "")
                                + (f", recurrence {recurrence}" if recurrence and recurrence != 'all' else "")))
    return result


def confirm_invoice(db: Session, invoice: Invoice, user: Optional[User]) -> Invoice:
    """draft / pending -> confirmed; posts the ledger charge if it has not been posted yet."""
    status = normalise_invoice_status(invoice.status)
    if status not in ("draft", "pending"):
        raise ValueError(f"A {status} invoice cannot be confirmed")
    before = {"status": invoice.status}
    invoice.status = "confirmed"
    invoice.confirmed_by_id = user.id if user else None
    invoice.confirmed_at = datetime.utcnow()
    invoice.sent_at = invoice.sent_at or datetime.utcnow()
    posted = False
    if invoice_charge_entry(db, invoice) is None:
        _post_ledger(db, invoice.client, "charge", f"Invoice {invoice.invoice_number} — "
                     f"{invoice.period_start:%b %Y}" if invoice.period_start else f"Invoice {invoice.invoice_number}",
                     debit=invoice_gross(invoice), currency=invoice.currency, reference_type="invoice",
                     reference_id=invoice.id, user=user, entry_date=invoice.issue_date)
        posted = True
    log_action(db, user, "approve", "billing", entity=invoice,
               description=f"Invoice {invoice.invoice_number} confirmed" + (" (ledger charge posted)" if posted else ""),
               before=before, after={"status": "confirmed"}, consequential=True)
    return invoice


def cancel_invoice(db: Session, invoice: Invoice, user: Optional[User], reason: str) -> Invoice:
    """Any unpaid invoice -> cancelled; reverses the ledger charge with a credit entry."""
    if not reason:
        raise ValueError("A reason is required to cancel an invoice")
    if normalise_invoice_status(invoice.status) == "cancelled":
        raise ValueError("This invoice is already cancelled")
    if float(invoice.paid_amount or 0) > 0:
        raise ValueError("Refund the payments before cancelling this invoice")
    before = {"status": invoice.status}
    invoice.status = "cancelled"
    invoice.cancelled_at = datetime.utcnow()
    invoice.cancel_reason = reason[:200]
    invoice.remarks = ((invoice.remarks or "") + f"\nCancelled: {reason}").strip()
    if invoice_charge_entry(db, invoice) is not None:
        _post_ledger(db, invoice.client, "adjustment", f"Cancelled invoice {invoice.invoice_number}: {reason}"[:250],
                     credit=invoice_gross(invoice), currency=invoice.currency, reference_type="invoice",
                     reference_id=invoice.id, user=user)
    if float(invoice.credit_applied or 0) > 0:  # give the applied credit back to the family
        _post_ledger(db, invoice.client, "credit", f"Credit released from cancelled {invoice.invoice_number}",
                     credit=float(invoice.credit_applied), currency=invoice.currency, reference_type="invoice",
                     reference_id=invoice.id, user=user)
    log_action(db, user, "cancel", "billing", entity=invoice, description=f"Invoice {invoice.invoice_number} cancelled",
               rationale=reason, before=before, after={"status": "cancelled"}, consequential=True)
    return invoice


def send_invoice(db: Session, invoice: Invoice, user: Optional[User]) -> Invoice:
    if normalise_invoice_status(invoice.status) == "cancelled":
        raise ValueError("A cancelled invoice cannot be sent")
    if invoice.status == "draft":
        invoice.status = "pending"
        if invoice_charge_entry(db, invoice) is None:
            _post_ledger(db, invoice.client, "charge", f"Invoice {invoice.invoice_number}", debit=invoice_gross(invoice),
                         currency=invoice.currency, reference_type="invoice", reference_id=invoice.id, user=user,
                         entry_date=invoice.issue_date)
    elif invoice.status == "sent":
        invoice.status = "pending"
    invoice.sent_at = datetime.utcnow()
    subject, body = _tpl(db, "invoice_issued", "whatsapp",
                         {"number": invoice.invoice_number, "name": invoice.client.full_name,
                          "amount": f"{invoice.currency} {float(invoice.total):,.2f}",
                          "due": invoice.due_date.strftime("%d %b %Y"), "link": "/portal/billing"},
                         f"Invoice {invoice.invoice_number}", "Your invoice is ready.")
    _notify_client(db, invoice.client, "invoice_issued", subject, body, link="/portal/billing")
    log_action(db, user, "update", "billing", entity=invoice, description=f"Invoice {invoice.invoice_number} sent to the family")
    return invoice


def send_reminder(db: Session, invoice: Invoice, user: Optional[User], stage: str = "manual") -> Invoice:
    if invoice.status in ("paid", "draft") or normalise_invoice_status(invoice.status) == "cancelled":
        raise ValueError("This invoice does not need a reminder")
    invoice.reminder_count = (invoice.reminder_count or 0) + 1
    invoice.last_reminder_at = datetime.utcnow()
    days = (date.today() - invoice.due_date).days
    when = f"was due {days} day(s) ago" if days > 0 else f"is due on {invoice.due_date:%d %b %Y}"
    _notify_client(db, invoice.client, "invoice_reminder", f"Payment reminder — {invoice.invoice_number}",
                   f"Assalamu Alaikum {invoice.client.full_name}, invoice {invoice.invoice_number} for "
                   f"{invoice.currency} {float(invoice.balance):,.2f} {when}. JazakAllah Khair.", link="/portal/billing")
    log_action(db, user, "update", "billing", entity=invoice,
               description=f"Reminder #{invoice.reminder_count} sent for {invoice.invoice_number} ({stage})")
    return invoice


def mark_overdue(db: Session, invoice: Invoice, user: Optional[User] = None) -> Invoice:
    if invoice.status not in ("sent", "pending", "confirmed", "partial"):
        raise ValueError("Only pending, confirmed or partially paid invoices can be marked overdue")
    invoice.status = "overdue"
    log_action(db, user, "update", "billing", entity=invoice, description=f"{invoice.invoice_number} marked overdue")
    return invoice


def void_invoice(db: Session, invoice: Invoice, user: Optional[User], rationale: str) -> Invoice:
    """Legacy name: voiding is the ERP's Cancel."""
    return cancel_invoice(db, invoice, user, rationale)


def invoice_stats(db: Session) -> dict:
    open_rows = db.query(Invoice).filter(Invoice.status.in_(INVOICE_OPEN_SET)).all()
    outstanding = round(sum((float(i.total) - float(i.paid_amount)) * get_rate(db, i.currency) for i in open_rows), 2)
    today = date.today()
    overdue = [i for i in open_rows if i.due_date and i.due_date < today]
    start_month = today.replace(day=1)
    collected = (db.query(func.coalesce(func.sum(Payment.amount_in_base), 0))
                 .filter(Payment.status.in_(PAYMENT_CONFIRMED_SET),
                         Payment.received_at >= datetime.combine(start_month, datetime.min.time())).scalar() or 0)
    issued_month = (db.query(func.coalesce(func.sum(Invoice.total_in_base), 0))
                    .filter(Invoice.status.notin_(INVOICE_CANCELLED_SET), Invoice.issue_date >= start_month).scalar() or 0)
    rate = round(100.0 * float(collected) / float(issued_month), 1) if float(issued_month) else 0.0
    return {"outstanding": outstanding, "overdue_count": len(overdue), "base": base_currency(db),
            "overdue_amount": round(sum((float(i.total) - float(i.paid_amount)) * get_rate(db, i.currency) for i in overdue), 2),
            "collected_month": round(float(collected), 2), "issued_month": round(float(issued_month), 2),
            "collection_rate": min(rate, 999.0), "open_count": len(open_rows), **invoice_tiles(db)}


def invoice_tiles(db: Session) -> dict:
    """ERP Invoice List tiles: Confirmed / Over All Pending / Cancelled / Draft / Paid."""
    counts = dict(db.query(Invoice.status, func.count(Invoice.id)).group_by(Invoice.status).all())
    return {"tile_confirmed": counts.get("confirmed", 0),
            "tile_pending": sum(counts.get(s, 0) for s in INVOICE_OVERALL_PENDING_SET),
            "tile_cancelled": sum(counts.get(s, 0) for s in INVOICE_CANCELLED_SET),
            "tile_draft": counts.get("draft", 0), "tile_paid": counts.get("paid", 0)}


def receipt_tiles(db: Session) -> dict:
    counts = dict(db.query(Payment.status, func.count(Payment.id)).group_by(Payment.status).all())
    return {"tile_confirmed": sum(counts.get(s, 0) for s in PAYMENT_CONFIRMED_SET),
            "tile_pending": counts.get("pending", 0), "tile_cancelled": counts.get("cancelled", 0),
            "tile_failed": counts.get("failed", 0), "tile_refunded": counts.get("refunded", 0)}


def invoice_status_filter(status: str):
    """SQLAlchemy criterion for an ERP status filter value (accepts legacy spellings)."""
    s = (status or "").lower()
    if s in ("pending", "sent"):
        return Invoice.status.in_(INVOICE_PENDING_SET)
    if s in ("cancelled", "void"):
        return Invoice.status.in_(INVOICE_CANCELLED_SET)
    if s == "pending_all":
        return Invoice.status.in_(INVOICE_OVERALL_PENDING_SET)
    return Invoice.status == s


def payment_status_filter(status: str):
    s = (status or "").lower()
    if s in ("confirmed", "completed"):
        return Payment.status.in_(PAYMENT_CONFIRMED_SET)
    return Payment.status == s


# ============================================================================ payments
def _apply_to_invoice(db: Session, invoice: Invoice, amount: float) -> float:
    """Apply ``amount`` to an invoice, returning the unapplied remainder."""
    balance = round(float(invoice.total) - float(invoice.paid_amount), 2)
    applied = min(balance, amount)
    if applied <= 0:
        return amount
    invoice.paid_amount = round(float(invoice.paid_amount) + applied, 2)
    if round(float(invoice.paid_amount), 2) >= round(float(invoice.total), 2) - 0.01:
        invoice.status = "paid"
        invoice.paid_at = datetime.utcnow()
    else:
        invoice.status = "partial"
    return round(amount - applied, 2)


def record_payment(db: Session, client: Client, amount: float, currency: str, method: str, reference: Optional[str],
                   user: Optional[User] = None, invoice: Optional[Invoice] = None, gateway: Optional[str] = None,
                   received_at: Optional[datetime] = None, status: str = "completed",
                   notes: Optional[str] = None, receipt_date: Optional[date] = None,
                   receiver_name: Optional[str] = None, receiving_destination: Optional[str] = None,
                   description: Optional[str] = None, category: Optional[str] = None,
                   beneficiary_account=None, billing_rep: Optional[User] = None,
                   amount_in_base: Optional[float] = None) -> Payment:
    """Record a receipt. Confirmed / completed receipts are allocated (oldest invoice first when none is given),
    posted to the ledger and journal, get a receipt PDF and notify the family. ``pending`` receipts wait for
    ``confirm_payment``; ``failed`` ones raise a risk alert."""
    amount = round(float(amount or 0), 2)
    if amount <= 0:
        raise ValueError("Payment amount must be greater than zero")
    currency = (currency or client.currency or "GBP").upper()
    received_at = received_at or datetime.utcnow()
    status = (status or "completed").lower()
    if status not in PAYMENT_STATUSES:
        raise ValueError(f"Unknown receipt status {status}")
    if beneficiary_account is not None:
        category = category or beneficiary_account.category
        receiving_destination = receiving_destination or beneficiary_account.account_name
        gateway = gateway or (beneficiary_account.category.lower() if beneficiary_account.is_auto else gateway)

    pay = Payment(payment_number=next_code(db, Payment, "payment_number", "PAY-"), invoice_id=invoice.id if invoice else None,
                  client_id=client.id, amount=amount, currency=currency,
                  amount_in_base=round(float(amount_in_base), 2) if amount_in_base else convert_to_base(db, amount, currency),
                  method=method or "bank_transfer", gateway=gateway, reference=reference, status=status,
                  received_at=received_at, received_by_id=user.id if user else None, notes=notes,
                  receipt_date=receipt_date or received_at.date(), receiver_name=receiver_name,
                  receiving_destination=receiving_destination, description=description, category=category,
                  beneficiary_account_id=beneficiary_account.id if beneficiary_account is not None else None,
                  billing_rep_id=(billing_rep.id if billing_rep else (client.billing_rep_id or (user.id if user else None))))
    db.add(pay)
    db.flush()

    if status == "failed":
        flag_failed_payment(db, pay, user, notes or "Gateway reported a failure")
        log_action(db, user, "create", "payments", entity=pay,
                   description=f"Payment {pay.payment_number} recorded as failed for {client.client_code}")
        return pay
    if status not in PAYMENT_CONFIRMED_SET:
        log_action(db, user, "create", "payments", entity=pay,
                   description=f"Receipt {pay.payment_number} {currency} {amount:.2f} from {client.client_code} recorded as {status}")
        return pay
    _settle_payment(db, pay, user, invoice)
    return pay


def _settle_payment(db: Session, pay: Payment, user: Optional[User], invoice: Optional[Invoice] = None) -> list[Invoice]:
    """Allocate a confirmed receipt to invoices, post the ledger + journal, issue the receipt and notify."""
    client = pay.client
    amount = round(float(pay.amount), 2)
    currency = pay.currency
    method = pay.method or "bank_transfer"
    received_at = pay.received_at or datetime.utcnow()
    invoice = invoice if invoice is not None else pay.invoice
    remainder = amount
    touched: list[Invoice] = []
    if invoice is not None:
        remainder = _apply_to_invoice(db, invoice, remainder)
        touched.append(invoice)
    if remainder > 0:
        open_invoices = (db.query(Invoice)
                         .filter(Invoice.client_id == client.id, Invoice.status.in_(INVOICE_OPEN_SET),
                                 Invoice.currency == currency)
                         .order_by(Invoice.due_date, Invoice.id).all())
        for inv in open_invoices:
            if remainder <= 0:
                break
            if invoice is not None and inv.id == invoice.id:
                continue
            before = remainder
            remainder = _apply_to_invoice(db, inv, remainder)
            if remainder != before:
                touched.append(inv)
                if pay.invoice_id is None:
                    pay.invoice_id = inv.id
    if remainder > 0:
        notes_extra = f"Unallocated surplus kept as account credit: {currency} {remainder:.2f}"
        pay.notes = ((pay.notes or "") + "\n" + notes_extra).strip()

    _post_ledger(db, client, "payment",
                 f"Payment {pay.payment_number} ({method.replace('_', ' ')})"
                 + (f" ref {pay.reference}" if pay.reference else ""),
                 credit=amount, currency=currency, reference_type="payment", reference_id=pay.id, user=user,
                 entry_date=pay.receipt_date or received_at.date())

    from app.services import accounting
    cash_code = "1000" if method == "cash" else "1010"
    base_amount = float(pay.amount_in_base)
    accounting.post_journal(db, f"Payment {pay.payment_number} from {client.client_code}",
                            [(cash_code, base_amount, 0), ("4000", 0, base_amount)],
                            reference_type="payment", reference_id=pay.id, entry_date=received_at.date(), user=user)

    if pay.receipt is None:
        receipt = Receipt(receipt_number=next_code(db, Receipt, "receipt_number", "RCT-"), payment_id=pay.id,
                          issued_at=received_at, sent_to=client.email or client.whatsapp)
        db.add(receipt)
        db.flush()
        try:
            generate_receipt_pdf(db, receipt)
        except Exception as exc:  # pragma: no cover
            log.warning("receipt PDF failed for %s: %s", receipt.receipt_number, exc)

    subject, body = _tpl(db, "payment_received", "whatsapp",
                         {"name": client.full_name, "amount": f"{currency} {amount:,.2f}", "link": "/portal/billing"},
                         "Payment received", f"We received {currency} {amount:,.2f}. JazakAllah Khair.")
    _notify_client(db, client, "payment_received", subject, body, link="/portal/billing")
    log_action(db, user, "create", "payments", entity=pay,
               description=f"Payment {pay.payment_number} {currency} {amount:.2f} from {client.client_code} via {method}"
                           + (f"; applied to {', '.join(i.invoice_number for i in touched)}" if touched else ""))
    return touched


def confirm_payment(db: Session, payment: Payment, user: Optional[User]) -> Payment:
    """pending -> confirmed: applies the receipt to the invoice, posts the ledger and journal, issues the receipt."""
    if payment.status != "pending":
        raise ValueError(f"A {payment.status} receipt cannot be confirmed")
    before = {"status": payment.status}
    payment.status = "confirmed"
    payment.confirmed_by_id = user.id if user else None
    payment.confirmed_at = datetime.utcnow()
    touched = _settle_payment(db, payment, user, payment.invoice)
    log_action(db, user, "approve", "payments", entity=payment,
               description=f"Receipt {payment.payment_number} confirmed"
                           + (f"; applied to {', '.join(i.invoice_number for i in touched)}" if touched else ""),
               before=before, after={"status": "confirmed"}, consequential=True)
    return payment


def cancel_payment(db: Session, payment: Payment, user: Optional[User], reason: str) -> Payment:
    """pending / confirmed -> cancelled. A confirmed receipt is un-applied from its invoice and the ledger and
    journal postings are reversed."""
    if not reason:
        raise ValueError("A reason is required to cancel a receipt")
    if payment.status in ("cancelled", "refunded"):
        raise ValueError(f"This receipt is already {payment.status}")
    before = {"status": payment.status}
    was_confirmed = is_confirmed_payment(payment.status)
    amount = round(float(payment.amount), 2)
    payment.notes = ((payment.notes or "") + f"\nCancelled: {reason}").strip()
    if was_confirmed:
        inv = payment.invoice
        if inv is not None:
            inv.paid_amount = round(max(0.0, float(inv.paid_amount) - amount), 2)
            if float(inv.paid_amount) <= 0.01:
                inv.status = "overdue" if inv.due_date and inv.due_date < date.today() else "pending"
                inv.paid_at = None
            elif float(inv.paid_amount) < float(inv.total):
                inv.status = "partial"
                inv.paid_at = None
        _post_ledger(db, payment.client, "adjustment", f"Cancelled receipt {payment.payment_number}: {reason}"[:250],
                     debit=amount, currency=payment.currency, reference_type="payment", reference_id=payment.id, user=user)
        from app.services import accounting
        from app.models.finance import JournalEntry
        je = (db.query(JournalEntry).filter(JournalEntry.reference_type == "payment", JournalEntry.reference_id == payment.id,
                                            JournalEntry.status == "posted").first())
        if je is not None:
            accounting.reverse_journal(db, je, user, f"Receipt {payment.payment_number} cancelled: {reason}")
    payment.status = "cancelled"
    log_action(db, user, "cancel", "payments", entity=payment,
               description=f"Receipt {payment.payment_number} cancelled" + (" (ledger and journal reversed)" if was_confirmed else ""),
               rationale=reason, before=before, after={"status": "cancelled"}, consequential=True)
    return payment


def sync_gateway_receipts(db: Session, user: Optional[User], limit: int = 5) -> list[Payment]:
    """Simulated gateway pull: confirmed receipts for open invoices of families paying through an auto
    beneficiary account (Stripe / PayPal). Idempotent for an invoice: one gateway receipt per invoice."""
    from app.models.erp import BeneficiaryAccount
    auto = (db.query(BeneficiaryAccount).filter(BeneficiaryAccount.is_auto.is_(True), BeneficiaryAccount.status == "active")
            .order_by(BeneficiaryAccount.id).all())
    account = auto[0] if auto else None
    gateway = (account.category.lower() if account else "stripe")
    candidates = (db.query(Invoice).filter(Invoice.status.in_(INVOICE_OPEN_SET), Invoice.total > 0)
                  .order_by(Invoice.due_date, Invoice.id).all())
    created: list[Payment] = []
    for inv in candidates:
        if len(created) >= limit:
            break
        if db.query(Payment.id).filter(Payment.invoice_id == inv.id, Payment.gateway == gateway,
                                       Payment.status.in_(PAYMENT_CONFIRMED_SET)).first():
            continue
        balance = round(float(inv.total) - float(inv.paid_amount), 2)
        if balance <= 0:
            continue
        pay = record_payment(db, inv.client, balance, inv.currency, "stripe" if gateway == "stripe" else "paypal",
                             f"{gateway.upper()}-{inv.invoice_number.replace('INV-', '')}", user=user, invoice=inv,
                             gateway=gateway, status="confirmed", description=f"Gateway auto receipt for {inv.invoice_number}",
                             receiver_name=account.account_name if account else "Online Payment Gateway",
                             category=account.category if account else gateway.title(), beneficiary_account=account)
        pay.confirmed_by_id = user.id if user else None
        pay.confirmed_at = datetime.utcnow()
        created.append(pay)
    log_action(db, user, "execute", "payments", entity_type="Payment",
               description=f"Gateway receipts synced ({gateway}): {len(created)} confirmed receipt(s) pulled")
    return created


def flag_failed_payment(db: Session, payment: Payment, user: Optional[User], reason: str) -> RiskAlert:
    payment.status = "failed"
    payment.notes = ((payment.notes or "") + f"\nFailure: {reason}").strip()
    alert = RiskAlert(alert_type="payment_failed", severity="high",
                      title=f"Payment failed — {payment.client.client_code if payment.client else 'client'} {payment.currency} {float(payment.amount):.2f}",
                      message=f"{payment.payment_number} via {payment.method} failed: {reason}",
                      entity_type="Payment", entity_id=payment.id, visibility="ops", status="open", source="system")
    db.add(alert)
    db.flush()
    client = payment.client
    for rep in ([client.billing_rep] if client and client.billing_rep else []) or _billing_reps(db):
        if rep:
            subject, body = _tpl(db, "payment_failed", "in_app",
                                 {"number": payment.invoice.invoice_number if payment.invoice else payment.payment_number,
                                  "name": client.full_name if client else ""},
                                 "Payment failed", f"{payment.payment_number} failed: {reason}")
            notify(db, rep.id, subject, body, event_type="payment_failed", link=f"/finance/payments/{payment.id}")
    log_action(db, user, "update", "payments", entity=payment, description=f"{payment.payment_number} marked failed",
               rationale=reason, severity="warning")
    return alert


def refund_payment(db: Session, payment: Payment, amount: float, user: Optional[User], rationale: str) -> Payment:
    amount = round(float(amount or 0), 2)
    if not rationale:
        raise ValueError("A rationale is required for a refund")
    if not is_confirmed_payment(payment.status):
        raise ValueError(f"A {payment.status} payment cannot be refunded")
    if amount <= 0 or amount > float(payment.amount) + 0.01:
        raise ValueError("The refund amount must be positive and no more than the original payment")
    before = {"status": payment.status}
    full = amount >= float(payment.amount) - 0.01
    payment.status = "refunded" if full else payment.status
    payment.notes = ((payment.notes or "") + f"\nRefunded {payment.currency} {amount:.2f}: {rationale}").strip()

    inv = payment.invoice
    if inv:
        inv.paid_amount = round(max(0.0, float(inv.paid_amount) - amount), 2)
        if float(inv.paid_amount) <= 0.01:
            inv.status = "overdue" if inv.due_date and inv.due_date < date.today() else "pending"
            inv.paid_at = None
        elif float(inv.paid_amount) < float(inv.total):
            inv.status = "partial"
            inv.paid_at = None

    _post_ledger(db, payment.client, "refund", f"Refund of {payment.payment_number}: {rationale}"[:250], debit=amount,
                 currency=payment.currency, reference_type="payment", reference_id=payment.id, user=user)

    from app.services import accounting
    base_amount = convert_to_base(db, amount, payment.currency)
    cash_code = "1000" if payment.method == "cash" else "1010"
    accounting.post_journal(db, f"Refund of {payment.payment_number}", [("4000", base_amount, 0), (cash_code, 0, base_amount)],
                            reference_type="refund", reference_id=payment.id, user=user)
    log_action(db, user, "refund", "payments", entity=payment,
               description=f"Refunded {payment.currency} {amount:.2f} of {payment.payment_number}", rationale=rationale,
               before=before, after={"status": payment.status}, consequential=True)
    _notify_client(db, payment.client, "payment_refunded", "Refund processed",
                   f"A refund of {payment.currency} {amount:,.2f} has been processed for {payment.payment_number}.",
                   link="/portal/billing")
    return payment


def reconcile_payment(db: Session, payment: Payment, user: Optional[User], bank_reference: str) -> Payment:
    if payment.reconciled:
        raise ValueError("This payment is already reconciled")
    payment.reconciled = True
    payment.reconciled_at = datetime.utcnow()
    if bank_reference:
        payment.reference = (payment.reference or "") + (f" | bank: {bank_reference}" if payment.reference else f"bank: {bank_reference}")
    log_action(db, user, "update", "payments", entity=payment,
               description=f"{payment.payment_number} reconciled against bank reference {bank_reference}")
    return payment


def payment_stats(db: Session) -> dict:
    today = date.today()
    start_month = today.replace(day=1)
    completed = db.query(Payment).filter(Payment.status.in_(PAYMENT_CONFIRMED_SET))
    month_total = (completed.with_entities(func.coalesce(func.sum(Payment.amount_in_base), 0))
                   .filter(Payment.received_at >= datetime.combine(start_month, datetime.min.time())).scalar() or 0)
    unreconciled = db.query(Payment).filter(Payment.status.in_(PAYMENT_CONFIRMED_SET), Payment.reconciled.is_(False)).count()
    failed = db.query(Payment).filter(Payment.status == "failed").count()
    refunded = (db.query(func.coalesce(func.sum(Payment.amount_in_base), 0)).filter(Payment.status == "refunded").scalar() or 0)
    return {"month_total": round(float(month_total), 2), "unreconciled": unreconciled, "failed": failed,
            "refunded": round(float(refunded), 2), "base": base_currency(db),
            "count_month": completed.filter(Payment.received_at >= datetime.combine(start_month, datetime.min.time())).count(),
            **receipt_tiles(db)}


def daily_totals_by_method(db: Session, days: int = 14) -> list[dict]:
    since = datetime.combine(date.today() - timedelta(days=days), datetime.min.time())
    rows = (db.query(func.date(Payment.received_at), Payment.method, func.count(Payment.id),
                     func.coalesce(func.sum(Payment.amount_in_base), 0))
            .filter(Payment.status.in_(PAYMENT_CONFIRMED_SET), Payment.received_at >= since)
            .group_by(func.date(Payment.received_at), Payment.method).all())
    grouped: dict[str, dict] = defaultdict(lambda: {"day": "", "methods": {}, "total": 0.0, "count": 0})
    for day, method, n, amount in rows:
        key = str(day)[:10]
        g = grouped[key]
        g["day"] = key
        g["methods"][method] = round(float(amount), 2)
        g["total"] = round(g["total"] + float(amount), 2)
        g["count"] += int(n)
    return sorted(grouped.values(), key=lambda r: r["day"], reverse=True)


# ============================================================================ discounts & scholarships
def discount_register(db: Session, weeks: int = 12) -> list[dict]:
    cutoff = date.today() - timedelta(weeks=weeks)
    rows = (db.query(DiscountRequest).filter(DiscountRequest.created_at >= datetime.combine(cutoff, datetime.min.time()))
            .order_by(DiscountRequest.created_at.desc()).all())
    grouped: dict[str, dict] = {}
    for r in rows:
        key = r.week or iso_week(r.created_at.date())
        g = grouped.setdefault(key, {"week": key, "count": 0, "pct_sum": 0.0, "value": 0.0, "approved": 0, "rejected": 0,
                                     "pending": 0, "requests": []})
        g["count"] += 1
        g["pct_sum"] += float(r.discount_pct or 0)
        sub = r.subscription
        if sub:
            g["value"] += convert_to_base(db, float(sub.discount_amount or 0), sub.currency)
        g[r.status if r.status in ("approved", "rejected", "pending") else "pending"] += 1
        g["requests"].append(r)
    out = []
    for g in grouped.values():
        g["avg_pct"] = round(g["pct_sum"] / g["count"], 1) if g["count"] else 0.0
        g["value"] = round(g["value"], 2)
        out.append(g)
    return sorted(out, key=lambda g: g["week"], reverse=True)


def request_scholarship(db: Session, client: Client, student: Optional[Student], scholarship_type: str,
                        coverage_pct: float, monthly_amount: float, currency: str, reason: str,
                        user: Optional[User], start: Optional[date] = None, end: Optional[date] = None) -> Scholarship:
    if not reason:
        raise ValueError("Please record why this family is being supported")
    if coverage_pct <= 0 and monthly_amount <= 0:
        raise ValueError("Set either a coverage percentage or a monthly amount")
    s = Scholarship(client_id=client.id, student_id=student.id if student else None, scholarship_type=scholarship_type,
                    coverage_pct=round(float(coverage_pct or 0), 2), monthly_amount=round(float(monthly_amount or 0), 2),
                    currency=(currency or client.currency or "GBP"), reason=reason, start_date=start or date.today(),
                    end_date=end, status="pending", requested_by_id=user.id if user else None)
    db.add(s)
    db.flush()
    log_action(db, user, "create", "scholarships", entity=s,
               description=f"Scholarship requested for {client.client_code} ({scholarship_type}, {coverage_pct:.0f}%)",
               rationale=reason)
    return s


def decide_scholarship(db: Session, scholarship: Scholarship, user: Optional[User], approve: bool, note: str) -> Scholarship:
    if scholarship.status != "pending":
        raise ValueError(f"This scholarship is already {scholarship.status}")
    if not note:
        raise ValueError("A decision note is required")
    scholarship.status = "approved" if approve else "rejected"
    scholarship.approved_by_id = user.id if user else None
    scholarship.reason = ((scholarship.reason or "") + f"\nDecision: {note}").strip()
    log_action(db, user, "approve" if approve else "reject", "scholarships", entity=scholarship,
               description=f"Scholarship {scholarship.status} for client #{scholarship.client_id}", rationale=note,
               consequential=True)
    if approve and scholarship.client:
        _notify_client(db, scholarship.client, "scholarship_approved", "Your support request has been approved",
                       "Alhamdulillah — your family's learning support has been approved. Your next invoice will reflect it.",
                       link="/portal/billing", whatsapp=False)
    return scholarship


# ============================================================================ PDFs
def _org(db: Session) -> Organization:
    return db.query(Organization).first()


def _pdf_header(c, org, title: str, width: float, height: float):
    from reportlab.lib.units import mm
    c.setFillColorRGB(0.06, 0.46, 0.43)
    c.rect(0, height - 28 * mm, width, 28 * mm, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(18 * mm, height - 14 * mm, org.name if org else "Online Quran College")
    c.setFont("Helvetica", 8.5)
    c.drawString(18 * mm, height - 20 * mm, (org.email if org else "info@onlinequrancollege.local") +
                 "  ·  " + (org.website if org and org.website else "onlinequrancollege.local"))
    c.setFont("Helvetica-Bold", 15)
    c.drawRightString(width - 18 * mm, height - 16 * mm, title)
    c.setFillColorRGB(0, 0, 0)


def generate_invoice_pdf(db: Session, invoice: Invoice) -> str:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    INVOICE_DIR.mkdir(parents=True, exist_ok=True)
    path = INVOICE_DIR / f"{invoice.invoice_number}.pdf"
    org = _org(db)
    client = invoice.client
    width, height = A4
    c = pdfcanvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"Invoice {invoice.invoice_number}")
    _pdf_header(c, org, "INVOICE", width, height)

    y = height - 40 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(18 * mm, y, "BILL TO")
    c.drawRightString(width - 18 * mm, y, "INVOICE DETAILS")
    c.setFont("Helvetica", 9)
    left = [client.client_code, client.full_name, client.address or "", f"{client.city or ''} {client.country or ''}".strip(),
            client.email or ""]
    right = [f"Number: {invoice.invoice_number}", f"Issued: {invoice.issue_date:%d %b %Y}",
             f"Due: {invoice.due_date:%d %b %Y}", f"Currency: {invoice.currency}",
             f"Period: {invoice.period_start:%d %b} - {invoice.period_end:%d %b %Y}" if invoice.period_start and invoice.period_end else "",
             f"Student: {invoice.student.full_name}" if invoice.student else ""]
    for i, line in enumerate(left):
        if line:
            c.drawString(18 * mm, y - (5 + i * 4.6) * mm, str(line)[:60])
    for i, line in enumerate(right):
        if line:
            c.drawRightString(width - 18 * mm, y - (5 + i * 4.6) * mm, str(line)[:60])

    y = y - 40 * mm
    c.setFillColorRGB(0.94, 0.96, 0.96)
    c.rect(18 * mm, y - 2 * mm, width - 36 * mm, 8 * mm, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(20 * mm, y + 0.6 * mm, "DESCRIPTION")
    c.drawRightString(width - 55 * mm, y + 0.6 * mm, "QTY")
    c.drawRightString(width - 20 * mm, y + 0.6 * mm, f"AMOUNT ({invoice.currency})")
    y -= 8 * mm
    c.setFont("Helvetica", 9)
    for item in invoice.items:
        c.drawString(20 * mm, y, str(item.description)[:78])
        c.drawRightString(width - 55 * mm, y, f"{float(item.quantity):g}")
        c.drawRightString(width - 20 * mm, y, f"{float(item.amount):,.2f}")
        y -= 6 * mm
        if y < 60 * mm:
            c.showPage()
            y = height - 30 * mm

    y -= 4 * mm
    c.line(width - 90 * mm, y, width - 18 * mm, y)
    y -= 6 * mm
    rows = [("Subtotal", float(invoice.subtotal))]
    if float(invoice.discount or 0):
        rows.append(("Discount / scholarship", -float(invoice.discount)))
    if float(invoice.credit_applied or 0):
        rows.append(("Account credit applied", -float(invoice.credit_applied)))
    if float(invoice.tax or 0):
        rows.append(("Tax", float(invoice.tax)))
    rows.append(("TOTAL DUE", float(invoice.total)))
    if float(invoice.paid_amount or 0):
        rows.append(("Paid", -float(invoice.paid_amount)))
        rows.append(("Balance", float(invoice.balance)))
    for label, value in rows:
        bold = label in ("TOTAL DUE", "Balance")
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 9.5 if bold else 9)
        c.drawRightString(width - 45 * mm, y, label)
        c.drawRightString(width - 18 * mm, y, f"{invoice.currency} {value:,.2f}")
        y -= 5.5 * mm

    y -= 6 * mm
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(18 * mm, y, "PAYMENT DETAILS")
    c.setFont("Helvetica", 8.5)
    c.drawString(18 * mm, y - 5 * mm, "Bank: [Bank name] · Account: [Account number] · IBAN: [IBAN] · SWIFT: [SWIFT]")
    c.drawString(18 * mm, y - 9.5 * mm, f"Please quote {invoice.invoice_number} with your transfer. Cards and PayPal accepted in the family portal.")
    if invoice.remarks:
        c.drawString(18 * mm, y - 15 * mm, f"Remarks: {invoice.remarks[:110]}")
    c.setFont("Helvetica-Oblique", 7.5)
    c.drawCentredString(width / 2, 14 * mm, f"{org.legal_name if org and org.legal_name else 'Online Quran College'} · "
                                            f"Generated {datetime.utcnow():%d %b %Y %H:%M} UTC · Status: {invoice.status.upper()}")
    c.showPage()
    c.save()
    invoice.pdf_path = f"/storage/invoices/{invoice.invoice_number}.pdf"
    return invoice.pdf_path


def generate_receipt_pdf(db: Session, receipt: Receipt) -> str:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = RECEIPT_DIR / f"{receipt.receipt_number}.pdf"
    payment = receipt.payment
    client = payment.client
    org = _org(db)
    width, height = A4
    c = pdfcanvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"Receipt {receipt.receipt_number}")
    _pdf_header(c, org, "RECEIPT", width, height)

    y = height - 45 * mm
    c.setFont("Helvetica", 10)
    lines = [
        ("Receipt number", receipt.receipt_number),
        ("Payment number", payment.payment_number),
        ("Received from", f"{client.client_code} · {client.full_name}"),
        ("Amount", f"{payment.currency} {float(payment.amount):,.2f}"),
        ("Amount (base)", f"{base_currency(db)} {float(payment.amount_in_base):,.2f}"),
        ("Method", (payment.method or "").replace("_", " ").title() + (f" via {payment.gateway}" if payment.gateway else "")),
        ("Reference", payment.reference or "—"),
        ("Invoice", payment.invoice.invoice_number if payment.invoice else "On account"),
        ("Received at", f"{payment.received_at:%d %b %Y %H:%M}"),
    ]
    for label, value in lines:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(18 * mm, y, label)
        c.setFont("Helvetica", 10)
        c.drawString(65 * mm, y, str(value)[:70])
        y -= 7 * mm
    y -= 4 * mm
    c.setFillColorRGB(0.94, 0.98, 0.96)
    c.rect(18 * mm, y - 12 * mm, width - 36 * mm, 16 * mm, stroke=0, fill=1)
    c.setFillColorRGB(0.06, 0.46, 0.43)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(width / 2, y - 5 * mm, f"PAID · {payment.currency} {float(payment.amount):,.2f}")
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Oblique", 8)
    c.drawCentredString(width / 2, 16 * mm, "JazakAllah Khair for supporting your child's Quran journey.")
    c.drawCentredString(width / 2, 12 * mm, f"{org.legal_name if org and org.legal_name else 'Online Quran College'} · computer generated receipt")
    c.showPage()
    c.save()
    receipt.pdf_path = f"/storage/receipts/{receipt.receipt_number}.pdf"
    return receipt.pdf_path


# ============================================================================ reporting
def subscription_report(db: Session, group_by: str = "course") -> list[dict]:
    """Subscription economics grouped by course / package / country / teacher (consolidated in base currency)."""
    from app.models.academic import Course, Package
    subs = (db.query(Subscription).filter(Subscription.status.in_(["active", "frozen"])).all())
    buckets: dict[str, dict] = {}
    for s in subs:
        if group_by == "package":
            key = s.package.name if s.package else "No package"
        elif group_by == "country":
            key = s.client.country if s.client else "Unknown"
        elif group_by == "teacher":
            key = s.teacher.full_name if s.teacher else "Unassigned"
        else:
            key = s.course.name if s.course else "Unassigned"
        b = buckets.setdefault(key, {"key": key, "count": 0, "mrr": 0.0, "cost": 0.0, "currencies": set()})
        b["count"] += 1
        b["mrr"] += float(s.price_in_base or 0)
        b["cost"] += float(s.teacher_cost_base or 0)
        b["currencies"].add(s.currency)
    out = []
    for b in buckets.values():
        b["mrr"] = round(b["mrr"], 2)
        b["cost"] = round(b["cost"], 2)
        b["margin"] = round(b["mrr"] - b["cost"], 2)
        b["margin_pct"] = margin_pct(b["mrr"], b["cost"])
        b["currencies"] = ", ".join(sorted(b["currencies"]))
        b["avg"] = round(b["mrr"] / b["count"], 2) if b["count"] else 0.0
        out.append(b)
    return sorted(out, key=lambda r: r["mrr"], reverse=True)


def client_balances(db: Session, q: str = "") -> list[dict]:
    rows = (db.query(LedgerEntry.client_id, func.coalesce(func.sum(LedgerEntry.debit), 0),
                     func.coalesce(func.sum(LedgerEntry.credit), 0)).group_by(LedgerEntry.client_id).all())
    balances = {cid: round(float(d) - float(c), 2) for cid, d, c in rows}
    last_pay = dict(db.query(Payment.client_id, func.max(Payment.received_at))
                    .filter(Payment.status.in_(PAYMENT_CONFIRMED_SET)).group_by(Payment.client_id).all())
    overdue = dict(db.query(Invoice.client_id, func.count(Invoice.id))
                   .filter(Invoice.status == "overdue").group_by(Invoice.client_id).all())
    query = db.query(Client)
    if q:
        like = f"%{q}%"
        query = query.filter((Client.full_name.ilike(like)) | (Client.client_code.ilike(like)) | (Client.email.ilike(like)))
    out = []
    for c in query.order_by(Client.client_code).all():
        out.append({"client": c, "balance": balances.get(c.id, 0.0), "last_payment": last_pay.get(c.id),
                    "overdue": overdue.get(c.id, 0), "credit": max(0.0, -balances.get(c.id, 0.0))})
    return out
