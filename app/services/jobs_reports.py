"""Scheduled job: e-mail the report subscriptions that are due (app/services/ir.py)."""
from __future__ import annotations

from sqlalchemy.orm import Session


def report_subscriptions(db: Session) -> dict:
    from app.services import ir
    return ir.run_due(db)


JOBS = [("report_subscriptions", report_subscriptions, 30)]
