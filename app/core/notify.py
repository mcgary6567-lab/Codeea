"""Notification service: in-app always; email / WhatsApp dispatched through integration adapters (simulated when not configured)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.core import Notification, User, Integration

log = logging.getLogger("oqc.notify")


def notify(
    db: Session,
    user: Optional[User | int],
    title: str,
    body: str = "",
    event_type: str = "general",
    link: Optional[str] = None,
    channels: Iterable[str] = ("in_app",),
    recipient_address: Optional[str] = None,
    commit: bool = False,
) -> list[Notification]:
    """Create notification records for a user on one or more channels and dispatch external ones."""
    user_id = user.id if isinstance(user, User) else user
    created = []
    for ch in channels:
        n = Notification(user_id=user_id, channel=ch, event_type=event_type, title=title, body=body,
                         link=link, recipient_address=recipient_address, status="queued")
        db.add(n)
        created.append(n)
    db.flush()
    for n in created:
        dispatch(db, n)
    if commit:
        db.commit()
    return created


def notify_many(db: Session, users: Iterable[User | int], title: str, body: str = "", **kw) -> None:
    for u in users:
        notify(db, u, title, body, **kw)


def dispatch(db: Session, n: Notification) -> None:
    """Send through the channel adapter. External channels run in simulation mode unless credentials exist."""
    n.attempts += 1
    try:
        if n.channel == "in_app":
            n.status = "delivered"
        elif n.channel == "email":
            from app.services.integrations import send_email
            send_email(db, n.recipient_address or "", n.title, n.body or "")
            n.status = "sent"
        elif n.channel == "whatsapp":
            from app.services.integrations import send_whatsapp
            send_whatsapp(db, n.recipient_address or "", f"*{n.title}*\n{n.body or ''}")
            n.status = "sent"
        elif n.channel == "push":
            n.status = "sent"  # PWA push placeholder
        n.sent_at = datetime.utcnow()
    except Exception as exc:  # pragma: no cover
        log.warning("notification %s failed: %s", n.id, exc)
        n.status = "failed"
        n.error = str(exc)[:500]


def unread_count(db: Session, user_id: int) -> int:
    return db.query(Notification).filter(Notification.user_id == user_id, Notification.channel == "in_app",
                                         Notification.is_read.is_(False)).count()
