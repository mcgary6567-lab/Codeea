"""Background job for the GoHighLevel sync queue (discovered by app/core/scheduler.py).

    JOBS = [("job id", callable(db), interval_minutes), ...]
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.services import ghl_sync


def sync_worker(db: Session) -> dict:
    """Send the queued GHL changes that are due (retries back off; dead jobs raise an alert)."""
    return ghl_sync.process(db)


JOBS = [
    ("ghl_sync_worker", sync_worker, 2),
]
