"""Background jobs for parent communication and referrals (discovered by app/core/scheduler.py).

    JOBS = [("job id", callable(db), interval_minutes), ...]
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.services import contacts


def follow_up_reminders(db: Session) -> dict:
    """Daily reminder for every due or overdue follow-up until it is done (one reminder per task per day)."""
    return contacts.remind_follow_ups(db)


def referral_follow_ups(db: Session) -> dict:
    """Referral rewards that became due, and referrals whose details were never collected."""
    return contacts.referral_follow_ups(db)


JOBS = [
    ("contacts_follow_up_reminders", follow_up_reminders, 60),
    ("contacts_referral_follow_ups", referral_follow_ups, 180),
]
