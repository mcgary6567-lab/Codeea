"""ERP billing parity seed (docs/AUDIT_ACADEMICS.md 3.7).

Brings the invoices and receipts written by earlier seeds onto the ERP vocabulary and fills the ERP-only
columns, then writes a realistic Ledger Additions history:

* invoice statuses ``sent`` -> ``pending`` and ``void`` -> ``cancelled``
* ``subs_total`` / ``subs_discount`` / ``subs_tax`` derived from the invoice items
* ``confirmed_at`` / ``confirmed_by_id`` on confirmed and paid invoices, ``is_bulk`` on the monthly run
* receipts get ``receipt_date``, ``receiver_name``, ``receiving_destination``, ``category``,
  ``beneficiary_account_id`` (looked up from the erp_config beneficiary accounts) and ``billing_rep_id``
* ~25 Ledger Additions over 90 days, types and effects spread, most confirmed (LedgerEntry posted),
  a few left pending or cancelled

Idempotent: every step only fills blanks and the ledger additions are created once.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.core import User
from app.models.erp import LEDGER_ADDITION_TYPES, BeneficiaryAccount, LedgerAddition
from app.models.finance import Invoice, InvoiceItem, Payment
from app.models.people import Client, Employee

SEED = 20260913
TARGET_ADDITIONS = 25

# payment method -> (beneficiary category, receiver name, fallback destination)
METHOD_MAP = {
    "stripe": ("Stripe", "Online Payment Gateway", "Stripe UK"),
    "card": ("Stripe", "Online Payment Gateway", "Stripe UK"),
    "paypal": ("PayPal", "Online Payment Gateway", "PayPal Manual"),
    "wise": ("Wise", "Billing Desk", "Wise UK"),
    "bank_transfer": ("UBL", "Billing Desk", "Company Account"),
    "cash": ("Cash", "Front Desk", "Cash by Admissions"),
    "other": ("Other", "Billing Desk", "Other"),
}

ADDITION_REMARKS = {
    "Teacher Gift": "Eid gift for the teacher, charged to the family on request.",
    "Leave Discount": "Approved leave discount for classes missed during the family's travel.",
    "Referral Bonus": "Referral reward credited after the referred family enrolled.",
    "Late Fee": "Late payment charge applied after the second reminder.",
    "Adjustment": "Balance adjustment agreed with the family during the monthly call.",
    "Penalty": "Charge-back handling fee passed on after a disputed card payment.",
}
# type -> effect (add = the family owes more, minus = credit to the family)
ADDITION_EFFECT = {"Teacher Gift": "add", "Leave Discount": "minus", "Referral Bonus": "minus",
                   "Late Fee": "add", "Adjustment": "minus", "Penalty": "add"}


def _user(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


# --------------------------------------------------------------------------- invoices
def _migrate_invoices(db: Session, billing_user: User | None) -> dict:
    out = {"status": 0, "subs": 0, "confirmed": 0, "bulk": 0}
    invoices = db.query(Invoice).all()
    items_by_invoice: dict[int, list[InvoiceItem]] = {}
    for item in db.query(InvoiceItem).all():
        items_by_invoice.setdefault(item.invoice_id, []).append(item)

    for inv in invoices:
        status = (inv.status or "").lower()
        if status == "sent":
            inv.status = "pending"
            out["status"] += 1
        elif status == "void":
            inv.status = "cancelled"
            if not inv.cancelled_at:
                inv.cancelled_at = datetime.combine(inv.issue_date, datetime.min.time())
            if not inv.cancel_reason:
                inv.cancel_reason = "Cancelled during the ERP migration (legacy void)."
            out["status"] += 1

        if not float(inv.subs_total or 0):
            items = items_by_invoice.get(inv.id, [])
            positive = sum(float(i.amount or 0) for i in items if float(i.amount or 0) > 0)
            negative = [i for i in items if float(i.amount or 0) < 0]
            discount = sum(-float(i.amount or 0) for i in negative
                           if "credit" not in (i.description or "").lower()
                           and "tax" not in (i.description or "").lower())
            tax = sum(float(i.amount or 0) for i in items if "tax" in (i.description or "").lower()
                      and float(i.amount or 0) > 0)
            if not positive and not discount:
                positive = float(inv.subtotal or inv.total or 0)
                discount = float(inv.discount or 0)
            inv.subs_total = round(positive, 2)
            inv.subs_discount = round(discount, 2)
            inv.subs_tax = round(tax or float(inv.tax or 0), 2)
            out["subs"] += 1

        if (inv.status or "") in ("confirmed", "paid") and not inv.confirmed_at:
            when = inv.paid_at or datetime.combine(inv.issue_date, datetime.min.time())
            inv.confirmed_at = when
            inv.confirmed_by_id = inv.confirmed_by_id or (billing_user.id if billing_user else None)
            out["confirmed"] += 1

        # the monthly billing run: a full calendar month raised on the 1st
        if not inv.is_bulk and inv.period_start and inv.period_end and inv.period_start.day == 1:
            next_month = (date(inv.period_start.year + 1, 1, 1) if inv.period_start.month == 12
                          else date(inv.period_start.year, inv.period_start.month + 1, 1))
            if inv.period_end == next_month - timedelta(days=1):
                inv.is_bulk = True
                out["bulk"] += 1
    db.flush()
    return out


# --------------------------------------------------------------------------- receipts
def _migrate_payments(db: Session, billing_user: User | None) -> int:
    accounts: dict[str, BeneficiaryAccount] = {}
    for a in db.query(BeneficiaryAccount).filter(BeneficiaryAccount.status == "active").all():
        accounts.setdefault(a.category, a)
    touched = 0
    for pay in db.query(Payment).all():
        category, receiver, destination = METHOD_MAP.get((pay.method or "other").lower(), METHOD_MAP["other"])
        account = accounts.get(category)  # degrade gracefully when erp_config has not run
        changed = False
        if not pay.receipt_date:
            pay.receipt_date = (pay.received_at or datetime.utcnow()).date()
            changed = True
        if not pay.category:
            pay.category = account.category if account else category
            changed = True
        if not pay.beneficiary_account_id and account is not None:
            pay.beneficiary_account_id = account.id
            changed = True
        if not pay.receiving_destination:
            pay.receiving_destination = account.account_name if account else destination
            changed = True
        if not pay.receiver_name:
            rep = pay.client.billing_rep if pay.client else None
            pay.receiver_name = receiver if receiver != "Billing Desk" else (
                rep.full_name if rep else (billing_user.full_name if billing_user else "Billing Desk"))
            changed = True
        if not pay.description:
            pay.description = (f"Fee receipt {pay.payment_number} - {pay.method or 'payment'}"
                               + (f" ref {pay.reference}" if pay.reference else ""))[:250]
            changed = True
        if not pay.billing_rep_id:
            pay.billing_rep_id = ((pay.client.billing_rep_id if pay.client else None)
                                  or (billing_user.id if billing_user else None))
            changed = True
        if changed:
            touched += 1
    db.flush()
    return touched


# --------------------------------------------------------------------------- ledger additions
def _seed_ledger_additions(db: Session, rnd: random.Random, billing_user: User | None, admin: User | None) -> int:
    existing = db.query(LedgerAddition).count()
    if existing >= TARGET_ADDITIONS:
        return 0
    clients = db.query(Client).filter(Client.students.any()).order_by(Client.id).all() or \
        db.query(Client).order_by(Client.id).all()
    if not clients:
        return 0
    employees = db.query(Employee).filter(Employee.is_teacher.is_(True)).order_by(Employee.id).all()
    from app.services import billing as billing_svc

    created = 0
    today = date.today()
    for n in range(existing, TARGET_ADDITIONS):
        client = clients[n % len(clients)]
        addition_type = LEDGER_ADDITION_TYPES[n % len(LEDGER_ADDITION_TYPES)]
        effect = ADDITION_EFFECT.get(addition_type, "minus")
        when = today - timedelta(days=rnd.randint(1, 90))
        amount = round(rnd.uniform(5, 45), 2)
        try:
            addition = billing_svc.create_ledger_addition(
                db, client, amount, client.currency, addition_type, effect, when,
                user=billing_user or admin,
                reference_employee=(employees[n % len(employees)] if employees and addition_type == "Teacher Gift" else None),
                billing_rep=billing_user, remarks=ADDITION_REMARKS.get(addition_type))
        except ValueError:
            continue
        created += 1
        # most are confirmed and posted; every 7th stays pending, every 11th is cancelled
        if n % 11 == 10:
            try:
                billing_svc.post_ledger_addition(db, addition, billing_user or admin)
                billing_svc.cancel_ledger_addition(db, addition, billing_user or admin,
                                                   "Raised against the wrong family; reversed the same day.")
            except ValueError:
                pass
        elif n % 7 != 6:
            try:
                billing_svc.post_ledger_addition(db, addition, billing_user or admin)
            except ValueError:
                pass
    db.flush()
    return created


def run(db: Session) -> None:
    rnd = random.Random(SEED)
    billing_user = _user(db, "billing@oqc.local") or _user(db, "finance@oqc.local")
    admin = _user(db, "admin@oqc.local")

    inv = _migrate_invoices(db, billing_user)
    receipts = _migrate_payments(db, billing_user)
    additions = _seed_ledger_additions(db, rnd, billing_user, admin)
    db.flush()
    print(f"    invoices: {inv['status']} status migrated, {inv['subs']} subs totals, "
          f"{inv['confirmed']} confirmed stamps, {inv['bulk']} marked bulk")
    print(f"    receipts: {receipts} updated with ERP receipt fields")
    print(f"    ledger additions: {additions} created "
          f"(total {db.query(LedgerAddition).count()})")
