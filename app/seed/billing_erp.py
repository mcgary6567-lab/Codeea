"""Billing Management parity seed (docs/AUDIT_BILLING.md).

The Clients Financial Summary derives everything it shows except three fields that the college keeps on the
family itself, so this seed fills those three and nothing else:

* ``balance_limit`` - how far the family may run into debit before the billing desk chases them. Each limit
  is set against the family's *actual* ledger balance so the page is honest: roughly a third of the families
  who owe money are deliberately left over their limit (so the Exceeded tile and the Exceeded report are
  never empty), the rest sit comfortably inside it, and a few families are left on 0, meaning the college
  runs no credit control on them - those are never counted as exceeded.
* ``payment_day`` - the day of the month their invoice falls due.
* ``billing_remarks`` - the desk's own note, on the families where a note is worth having.

Idempotent: a family with a payment day already set has been through this seed and is left untouched, so
re-running creates and changes nothing.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.people import Client
from app.services import billing

# Limits the billing desk would actually agree, smallest first. A limit is snapped onto this ladder so the
# numbers on the page read like policy rather than arithmetic.
LADDER = [10, 25, 50, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000, 3000, 5000,
          7500, 10000, 15000, 20000, 30000, 50000, 75000, 100000]
# Where a family owes nothing yet, the limit is a plain default in their own currency.
DEFAULT_LIMIT = {"PKR": 20000.0, "GBP": 200.0, "USD": 250.0, "EUR": 250.0, "CAD": 300.0, "AUD": 300.0}
PAYMENT_DAYS = [1, 5, 10, 15, 20, 25, 28]

CHASE_REMARKS = [
    "Over the agreed limit since the summer term - call before the next invoice run.",
    "Two invoices outstanding; father asked for a catch-up plan over three months.",
    "Card keeps failing - ask for a bank transfer and confirm the receipt.",
    "Promised to clear the balance after the Eid holidays; follow up if it slips.",
]
STANDING_REMARKS = [
    "Pays once the salary clears; do not chase before the 5th.",
    "Father travels for work - message the mother's WhatsApp number.",
    "Bank transfer from Pakistan; allow three working days to land.",
    "Wants one invoice covering all the children, not one per student.",
    "Limit raised while the third child settles into the Hifz division.",
    "Sponsored family - part of the fee is met by the scholarship fund.",
]


def _snap_below(amount: float) -> float:
    """The largest ladder limit strictly below ``amount`` - a limit this family has genuinely run past."""
    below = [v for v in LADDER if v < amount]
    return float(below[-1]) if below else float(max(1, int(amount * 0.5)))  # whole units, never zero


def _snap_above(amount: float) -> float:
    """The smallest ladder limit comfortably above ``amount`` - a family well inside its limit."""
    target = max(amount * 1.5, 50.0)
    above = [v for v in LADDER if v >= target]
    return float(above[0]) if above else round(target, 2)


def run(db: Session) -> None:
    clients = db.query(Client).order_by(Client.client_code).all()
    todo = [c for c in clients if c.payment_day is None]
    if not todo:
        print("    billing_erp: balance limits, payment days and billing remarks already seeded; nothing to do")
        return

    balances = billing.client_balance_map(db, [c.id for c in todo])
    exceeded = within = uncontrolled = remarks = 0
    owing = 0  # index over the families that owe money, so the "every third" choice is stable
    for i, c in enumerate(todo):
        c.payment_day = PAYMENT_DAYS[i % len(PAYMENT_DAYS)]
        balance = round(balances.get(c.id, 0.0), 2)
        if balance > 0:
            over = owing % 3 == 0
            owing += 1
            if over:
                c.balance_limit = _snap_below(balance)
                c.billing_remarks = CHASE_REMARKS[exceeded % len(CHASE_REMARKS)]
                remarks += 1
                exceeded += 1
            else:
                c.balance_limit = _snap_above(balance)
                within += 1
        elif i % 7 == 0:
            c.balance_limit = 0            # no credit control agreed with this family
            uncontrolled += 1
        else:
            c.balance_limit = DEFAULT_LIMIT.get(c.currency or "GBP", 200.0)
            within += 1
        if c.billing_remarks is None and i % 4 == 1:
            c.billing_remarks = STANDING_REMARKS[(i // 4) % len(STANDING_REMARKS)]
            remarks += 1
    db.flush()
    print(f"    billing_erp: {len(todo)} families given a balance limit and a payment day "
          f"({exceeded} over their limit, {within} within, {uncontrolled} with no limit), {remarks} billing remarks")
