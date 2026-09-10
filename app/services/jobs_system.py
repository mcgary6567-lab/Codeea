"""Background jobs for the System Administration module (backups, webhooks, integration health, retention).

Registered automatically by app.core.scheduler (JOBS = [(job_id, callable(db), interval_minutes)]).
Audit events are NEVER deleted by any job here - the audit trail is immutable.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.core import Notification, User, UserSession

log = logging.getLogger("oqc.jobs.system")


def nightly_backup(db: Session) -> str:
    """Create a scheduled backup and prune old ones per Setting 'backup_retention_count'."""
    from app.core.notify import notify
    from app.services import system as svc
    try:
        rec = svc.create_backup(db, actor=None, backup_type="scheduled")
    except Exception as exc:
        log.exception("nightly backup failed")
        for u in db.query(User).join(User.role).filter(User.is_active.is_(True)).all():
            if u.role_slug in ("system_admin", "hod_technology", "super_admin"):
                notify(db, u, "Nightly backup failed", f"{type(exc).__name__}: {str(exc)[:200]}",
                       event_type="backup_failed", link="/admin/backups")
        raise
    removed = svc.cleanup_backups(db)
    return f"backup {rec.filename} ({rec.size_bytes // 1024} KB), {removed} old backup(s) removed"


def deliver_webhooks(db: Session) -> str:
    from app.services.integrations import deliver_pending_webhooks
    n = deliver_pending_webhooks(db, limit=40)
    return f"{n} outbound webhook deliveries processed"


def integration_health(db: Session) -> str:
    """Hourly health refresh; daily counter reset at midnight UTC."""
    from app.services.integrations import health_check_all
    from app.services import system as svc
    health_check_all(db)
    reset = 0
    if datetime.utcnow().hour == 0:
        reset = svc.reset_integration_counters(db)
    return f"health checked; {reset} provider counters reset"


def retention_cleanup(db: Session) -> str:
    """Delete expired sessions and old read notifications. Audit events are retained forever."""
    from app.services import system as svc
    session_days = int(svc.get_setting_value(db, "retention_sessions_days", 30) or 30)
    notif_days = int(svc.get_setting_value(db, "retention_notifications_days", 180) or 180)
    now = datetime.utcnow()
    sessions = (db.query(UserSession)
                .filter(UserSession.expires_at < now - timedelta(days=session_days))
                .delete(synchronize_session=False))
    notifs = (db.query(Notification)
              .filter(Notification.created_at < now - timedelta(days=notif_days),
                      Notification.is_read.is_(True))
              .delete(synchronize_session=False))
    return f"{sessions} expired session(s) and {notifs} read notification(s) deleted; audit events retained"


JOBS = [
    ("system_nightly_backup", nightly_backup, 1440),
    ("system_webhook_delivery", deliver_webhooks, 2),
    ("system_integration_health", integration_health, 60),
    ("system_retention_cleanup", retention_cleanup, 1440),
]
