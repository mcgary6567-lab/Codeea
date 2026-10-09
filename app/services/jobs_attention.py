"""Background job for the attention layer (discovered by app/core/scheduler.py).

    JOBS = [("job id", callable(db), interval_minutes), ...]
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.services import attention

MARKER_KEY = "job_attention_check_last_day"


def attention_check(db: Session) -> dict:
    """Once a day: re-check every active student, family and teacher (items refresh and resolve themselves)."""
    from app.services import scheduling as sched_svc
    day = attention.today().isoformat()
    if sched_svc.setting_value(db, MARKER_KEY, None, field="value") == day:
        return {"skipped": "already checked today", "day": day}
    result = attention.run_detection(db)
    sched_svc.set_setting(db, MARKER_KEY, {"value": day, "at": datetime.utcnow().isoformat(), **result}, group="jobs",
                          description="Last day the attention check ran")
    return {"day": day, **result}


JOBS = [
    ("attention_daily_check", attention_check, 60),
]
