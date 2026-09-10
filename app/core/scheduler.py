"""Background jobs (APScheduler). Modules register jobs by creating ``app/services/jobs_<name>.py``
exposing ``JOBS = [("job id", callable(db), interval_minutes), ...]``. Each callable receives a fresh Session."""
from __future__ import annotations

import importlib
import logging
import pkgutil
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.database import SessionLocal

log = logging.getLogger("oqc.scheduler")
_scheduler: BackgroundScheduler | None = None
JOB_STATUS: dict[str, dict] = {}


def _wrap(job_id: str, fn):
    def run():
        db = SessionLocal()
        started = datetime.utcnow()
        try:
            result = fn(db)
            db.commit()
            JOB_STATUS[job_id] = {"last_run": started, "status": "ok", "result": result}
        except Exception as exc:  # pragma: no cover
            db.rollback()
            log.exception("job %s failed", job_id)
            JOB_STATUS[job_id] = {"last_run": started, "status": "error", "result": str(exc)[:300]}
        finally:
            db.close()
    return run


def discover_jobs() -> list[tuple[str, object, int]]:
    import app.services as services_pkg
    jobs: list[tuple[str, object, int]] = []
    for mod_info in pkgutil.iter_modules(services_pkg.__path__):
        if not mod_info.name.startswith("jobs_"):
            continue
        try:
            mod = importlib.import_module(f"app.services.{mod_info.name}")
            jobs.extend(getattr(mod, "JOBS", []))
        except Exception:
            log.exception("failed to load jobs from %s", mod_info.name)
    return jobs


def start_scheduler() -> None:
    global _scheduler
    if _scheduler:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    for job_id, fn, minutes in discover_jobs():
        _scheduler.add_job(_wrap(job_id, fn), "interval", minutes=minutes, id=job_id, replace_existing=True, max_instances=1, coalesce=True)
        JOB_STATUS.setdefault(job_id, {"last_run": None, "status": "scheduled", "result": None, "interval": minutes})
        JOB_STATUS[job_id]["interval"] = minutes
    _scheduler.start()
    log.info("scheduler started with %d jobs", len(JOB_STATUS))


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def run_job_now(job_id: str) -> dict:
    for jid, fn, _ in discover_jobs():
        if jid == job_id:
            _wrap(jid, fn)()
            return JOB_STATUS.get(jid, {})
    raise KeyError(job_id)
