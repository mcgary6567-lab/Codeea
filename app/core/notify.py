"""Notification service: in-app always; email / WhatsApp dispatched through integration adapters (simulated when not configured)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models.core import Notification, User

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


SKIPPED_BY_PREFERENCE = "Channel switched off in the recipient's communication preferences"


def channel_opted_out(db: Session, user_id: Optional[int], channel: str) -> bool:
    """True when the recipient switched ``channel`` off (a CommunicationPreference row with opted_in False), held
    either against their user or against the family record their login belongs to. in_app is never opted out."""
    if not user_id or channel == "in_app":
        return False
    from sqlalchemy import or_
    from app.models.core import CommunicationPreference
    owners = [CommunicationPreference.user_id == user_id]
    try:
        from app.models.people import Client
        client_ids = [cid for (cid,) in db.query(Client.id).filter(Client.user_id == user_id).all()]
    except Exception:  # pragma: no cover - people module unavailable
        client_ids = []
    if client_ids:
        owners.append(CommunicationPreference.client_id.in_(client_ids))
    # the most recent decision wins when both a user-level and a family-level row exist
    pref = (db.query(CommunicationPreference).filter(or_(*owners), CommunicationPreference.channel == channel)
            .order_by(CommunicationPreference.updated_at.desc().nullslast(), CommunicationPreference.id.desc()).first())
    return pref is not None and not pref.opted_in


def dispatch(db: Session, n: Notification) -> None:
    """Send through the channel adapter. External channels run in simulation mode unless credentials exist.
    A channel the recipient switched off in their communication preferences is skipped (status ``skipped``)."""
    n.attempts += 1
    try:
        if channel_opted_out(db, n.user_id, n.channel):
            n.status = "skipped"
            n.error = SKIPPED_BY_PREFERENCE
            return
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
