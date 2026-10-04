"""Background jobs for the Academic module (discovered by app/core/scheduler.py).

    JOBS = [("job id", callable(db), interval_minutes), ...]

Each callable gets a fresh Session and the scheduler commits afterwards.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.academic import MonthlyTest
from app.models.core import User
from app.services.academic import (deliver_result_card, generate_tests_for_period, period_of, refresh_dor_flags,
                                   generate_result_card_pdf)

log = logging.getLogger("oqc.jobs.academic")


def _system_user(db: Session):
    return db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()


CATCHUP_DAYS = 3
TESTS_MARKER_KEY = "job_monthly_test_generation_last_period"


def monthly_test_generation(db: Session, today: date | None = None) -> dict:
    """Due on the 1st of each month: generate the monthly test for every active/trial student.

    A 1st the scheduler missed (restart after both ticks) is caught up during the first days of the month; the
    generated period is kept as a Setting marker so the month is handled once, and generate_tests_for_period
    itself skips students that already hold a test for the period.
    """
    from app.services import scheduling as sched_svc
    today = today or date.today()
    period = period_of(today)
    if today.day > 1 + CATCHUP_DAYS:
        return {"skipped": "not the 1st (catch-up window passed)", "period": period}
    if sched_svc.setting_value(db, TESTS_MARKER_KEY, None, field="value") == period:
        return {"skipped": "already generated this month", "period": period, "created": 0}
    created = generate_tests_for_period(db, period, _system_user(db))
    sched_svc.set_setting(db, TESTS_MARKER_KEY, {"value": period, "at": datetime.utcnow().isoformat(), "created": created},
                          group="jobs", description="Last period the monthly test generation job handled")
    if created:
        log.info("monthly test generation: %s tests created for %s", created, period)
    return {"period": period, "created": created}


def auto_deliver_result_cards(db: Session) -> dict:
    """Deliver result cards that were scored more than 24h ago and never sent to the guardian."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    user = _system_user(db)
    pending = (db.query(MonthlyTest)
               .filter(MonthlyTest.percentage.isnot(None), MonthlyTest.delivered_at.is_(None),
                       MonthlyTest.scored_at.isnot(None), MonthlyTest.scored_at <= cutoff)
               .order_by(MonthlyTest.scored_at).limit(100).all())
    delivered = failed = 0
    for t in pending:
        try:
            if not t.result_card_path:
                generate_result_card_pdf(db, t)
            deliver_result_card(db, t, user)
            delivered += 1
        except Exception as exc:  # pragma: no cover
            failed += 1
            log.warning("auto-deliver failed for test %s: %s", t.id, exc)
    return {"delivered": delivered, "failed": failed, "pending": len(pending)}


def dor_quota_refresh(db: Session) -> dict:
    """Recompute Student.dor_quota_met from the current month's revision schedule."""
    blocked = refresh_dor_flags(db)
    return {"period": period_of(), "blocked_students": blocked}


JOBS = [
    ("academic_monthly_test_generation", monthly_test_generation, 720),
    ("academic_auto_deliver_cards", auto_deliver_result_cards, 120),
    ("academic_dor_quota_refresh", dor_quota_refresh, 360),
]
