"""Background jobs for the Finance module (discovered by app/core/scheduler.py).

    JOBS = [("job id", callable(db), interval_minutes), ...]

Every job is idempotent: invoices are keyed on (subscription, period), reminders are de-duplicated
through ReminderLog and KPI snapshots are keyed on (kpi, period).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.notify import notify
from app.core.utils import month_key
from app.models.core import User, RiskAlert
from app.models.finance import Invoice, Payment, Subscription
from app.models.ops import KPI, KPIValue
from app.models.scheduling import ReminderLog
from app.services import billing

log = logging.getLogger("oqc.jobs.finance")

# stage key -> days relative to the due date (negative = before due)
REMINDER_CADENCE = [("pre_3", -3), ("due", 0), ("post_3", 3), ("post_7", 7), ("post_14", 14)]


def _system_user(db: Session) -> User | None:
    return db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()


def _already_reminded(db: Session, invoice: Invoice, stage: str) -> bool:
    return db.query(ReminderLog.id).filter(ReminderLog.reminder_type == f"invoice_{stage}",
                                           ReminderLog.entity_type == "Invoice",
                                           ReminderLog.entity_id == invoice.id).first() is not None


# --------------------------------------------------------------------------- invoicing
def daily_invoice_generation(db: Session) -> dict:
    """Issue invoices for active subscriptions whose next billing date falls within the next 3 days."""
    user = _system_user(db)
    horizon = date.today() + timedelta(days=3)
    subs = (db.query(Subscription)
            .filter(Subscription.status == "active", Subscription.next_billing_date.isnot(None),
                    Subscription.next_billing_date <= horizon).all())
    created, skipped, failed = 0, 0, 0
    for sub in subs:
        period_start = sub.next_billing_date
        period_end = billing.add_months(period_start, 1) - timedelta(days=1)
        if billing.invoice_for_period(db, sub, period_start):
            sub.next_billing_date = billing.add_months(period_start, 1)
            skipped += 1
            continue
        try:
            billing.generate_invoice(db, sub, period_start, period_end, user=user)
            created += 1
        except Exception as exc:  # pragma: no cover
            failed += 1
            log.warning("invoice generation failed for %s: %s", sub.subscription_code, exc)
    return {"subscriptions": len(subs), "created": created, "skipped": skipped, "failed": failed}


def overdue_and_reminders(db: Session) -> dict:
    """Mark invoices overdue and run the reminder cadence (3 days before due, on due, 3/7/14 days after)."""
    today = date.today()
    marked, reminders = 0, 0
    open_invoices = db.query(Invoice).filter(Invoice.status.in_(["sent", "partial", "overdue"])).all()
    for inv in open_invoices:
        if not inv.due_date:
            continue
        if inv.status in ("sent", "partial") and inv.due_date < today:
            inv.status = "overdue"
            marked += 1
        offset = (today - inv.due_date).days
        stage = next((name for name, days in REMINDER_CADENCE if days == offset), None)
        if not stage or _already_reminded(db, inv, stage):
            continue
        client = inv.client
        if not client:
            continue
        when = "is due today" if offset == 0 else (f"is due in {-offset} days" if offset < 0 else f"is {offset} days overdue")
        body = (f"Assalamu Alaikum {client.full_name}, invoice {inv.invoice_number} for "
                f"{inv.currency} {float(inv.balance):,.2f} {when}. JazakAllah Khair for your support.")
        billing._notify_client(db, client, "invoice_reminder", f"Payment reminder — {inv.invoice_number}", body,
                               link="/portal/billing")
        db.add(ReminderLog(reminder_type=f"invoice_{stage}", user_id=client.user_id, entity_type="Invoice",
                           entity_id=inv.id, channel="whatsapp", status="sent"))
        inv.reminder_count = (inv.reminder_count or 0) + 1
        inv.last_reminder_at = datetime.utcnow()
        reminders += 1
        rep = client.billing_rep
        for target in ([rep] if rep else billing._billing_reps(db))[:3]:
            if target:
                notify(db, target.id, f"Invoice {inv.invoice_number} {when}",
                       f"{client.client_code} · {client.full_name} · {inv.currency} {float(inv.balance):,.2f}",
                       event_type="invoice_reminder", link=f"/finance/invoices/{inv.id}")
                db.add(ReminderLog(reminder_type=f"invoice_{stage}_rep", user_id=target.id, entity_type="Invoice",
                                   entity_id=inv.id, channel="in_app", status="sent"))
    return {"checked": len(open_invoices), "marked_overdue": marked, "reminders": reminders}


# --------------------------------------------------------------------------- payments
def failed_payment_alerts(db: Session) -> dict:
    """Raise a RiskAlert and notify the billing rep for failed payments that have not been alerted yet."""
    failed = db.query(Payment).filter(Payment.status == "failed").order_by(Payment.id.desc()).limit(200).all()
    existing = {a.entity_id for a in db.query(RiskAlert).filter(RiskAlert.alert_type == "payment_failed",
                                                                RiskAlert.entity_type == "Payment").all()}
    created = 0
    for p in failed:
        if p.id in existing:
            continue
        billing.flag_failed_payment(db, p, _system_user(db), "Automatic detection of a failed payment")
        created += 1
    return {"failed": len(failed), "alerts_created": created}


# --------------------------------------------------------------------------- KPIs & renewals
def mrr_snapshot(db: Session) -> dict:
    """Write the monthly MRR KPI value (base currency) when a KPI with code 'mrr' exists."""
    kpi = db.query(KPI).filter(KPI.code == "mrr").first()
    if not kpi:
        return {"skipped": "no KPI with code 'mrr'"}
    period = month_key()
    value = billing.mrr_in_base(db)
    row = db.query(KPIValue).filter(KPIValue.kpi_id == kpi.id, KPIValue.period == period,
                                    KPIValue.entity_type.is_(None)).first()
    if row:
        row.value = value
        row.target = kpi.target
    else:
        db.add(KPIValue(kpi_id=kpi.id, period=period, value=value, target=kpi.target, source="system"))
    return {"period": period, "mrr": value}


def auto_renew_subscriptions(db: Session) -> dict:
    """Roll subscriptions that passed their end date forward when auto-renew is on; expire the rest."""
    today = date.today()
    user = _system_user(db)
    rows = (db.query(Subscription)
            .filter(Subscription.status == "active", Subscription.end_date.isnot(None), Subscription.end_date < today).all())
    renewed, expired = 0, 0
    for sub in rows:
        if sub.auto_renew:
            billing.renew_subscription(db, sub, user)
            renewed += 1
        else:
            sub.status = "expired"
            expired += 1
    return {"checked": len(rows), "renewed": renewed, "expired": expired}


def unfreeze_due_subscriptions(db: Session) -> dict:
    """Resume frozen subscriptions whose freeze window has ended."""
    today = date.today()
    user = _system_user(db)
    rows = (db.query(Subscription)
            .filter(Subscription.status == "frozen", Subscription.freeze_end.isnot(None),
                    Subscription.freeze_end < today).all())
    for sub in rows:
        billing.unfreeze_subscription(db, sub, user, note="Freeze window ended (automatic)")
    return {"resumed": len(rows)}


JOBS = [
    ("finance_daily_invoicing", daily_invoice_generation, 720),
    ("finance_overdue_reminders", overdue_and_reminders, 240),
    ("finance_failed_payments", failed_payment_alerts, 60),
    ("finance_mrr_snapshot", mrr_snapshot, 1440),
    ("finance_auto_renew", auto_renew_subscriptions, 720),
    ("finance_unfreeze", unfreeze_due_subscriptions, 720),
]
