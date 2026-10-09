"""Integration adapters (WhatsApp Cloud API, GHL, email SMTP, n8n webhooks, AI gateway).

Every adapter runs in *simulation mode* when credentials are missing so the whole platform works on localhost.
Calls are logged to the Integration health table either way.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import time
from datetime import datetime
from email.mime.text import MIMEText
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.core import Integration, WebhookDelivery, Webhook

log = logging.getLogger("oqc.integrations")

PROVIDERS = {
    "whatsapp": "WhatsApp Cloud API",
    "ghl": "GoHighLevel",
    "n8n": "n8n Automation",
    "zoom": "Zoom Recordings",
    "google": "Google Workspace",
    "meta_ads": "Meta Ads",
    "google_ads": "Google Ads",
    "payment": "Payment Gateway",
    "smtp": "Email (SMTP)",
    "slack": "Slack",
    "openproject": "OpenProject",
    "ai": "AI Provider Gateway",
    "video": "Video Classroom (WebRTC)",
}


def get_integration(db: Session, provider: str) -> Integration:
    integ = db.query(Integration).filter(Integration.provider == provider).first()
    if not integ:
        integ = Integration(provider=provider, name=PROVIDERS.get(provider, provider), status="not_configured")
        db.add(integ)
        db.flush()
    return integ


def _record(db: Session, provider: str, ok: bool, error: Optional[str] = None, simulated: bool = False) -> None:
    integ = get_integration(db, provider)
    integ.calls_today = (integ.calls_today or 0) + 1
    integ.last_health_check_at = datetime.utcnow()
    if ok:
        integ.health = "healthy"
        integ.status = "simulated" if simulated else "connected"
        integ.last_error = None
    else:
        integ.failures_today = (integ.failures_today or 0) + 1
        integ.health = "degraded" if integ.failures_today < 5 else "down"
        integ.status = "error"
        integ.last_error = (error or "")[:500]


# ----------------------------------------------------------------------------- inbound webhook secrets
# Where each provider's inbound webhook secret / shared token is looked for, in order: the Integration row's
# configuration (saved on /admin/integrations/<provider>), then the environment (.env) under these names.
# Nothing is added to app.config: a name that is not a Settings field is read straight from the environment.
INBOUND_SECRET_SOURCES: dict[str, dict] = {
    "whatsapp": {"config": ["app_secret", "webhook_secret"], "env": ["META_APP_SECRET", "WHATSAPP_APP_SECRET"]},
    "meta_ads": {"config": ["app_secret", "webhook_secret"], "env": ["META_APP_SECRET"]},
    "ghl": {"config": ["webhook_secret"], "env": ["GHL_WEBHOOK_SECRET"]},
    "n8n": {"config": ["webhook_secret", "inbound_secret"], "env": ["N8N_WEBHOOK_SECRET"]},
    "payment": {"config": ["webhook_secret"], "env": ["PAYMENT_WEBHOOK_SECRET"]},
    "zoom": {"config": ["webhook_secret"], "env": ["ZOOM_WEBHOOK_SECRET", "ZOOM_CLIENT_SECRET"]},
}


def inbound_secret(db: Session, provider: str) -> str:
    """The secret an inbound webhook from ``provider`` must be signed with (or present as a shared token).

    Returns "" when nothing is configured anywhere, so the caller decides between rejecting (production) and
    accepting with a warning (development).
    """
    spec = INBOUND_SECRET_SOURCES.get(provider, {"config": ["webhook_secret"], "env": []})
    integ = db.query(Integration).filter(Integration.provider == provider).first()
    config = (integ.config or {}) if integ else {}
    for key in spec["config"]:
        val = str(config.get(key) or "").strip()
        if val:
            return val
    for name in spec["env"]:
        val = str(getattr(settings, name, "") or os.environ.get(name, "") or "").strip()
        if val:
            return val
    return ""


def has_live_credentials(db: Session, provider: str) -> bool:
    """True when the provider has real credentials (a configured inbound secret, or a connected Integration row)."""
    if inbound_secret(db, provider):
        return True
    integ = db.query(Integration).filter(Integration.provider == provider).first()
    return bool(integ and integ.status == "connected")


# ----------------------------------------------------------------------------- WhatsApp Cloud API
def _catalogue_sender(db: Session, purpose: str):
    """The connected WhatsApp Number from Configuration > WhatsApp Senders whose throttle allows a send now."""
    try:
        from app.web.company_config import pick_sender   # lazy: the catalogue lives with its configuration screen
        return pick_sender(db, purpose or "general")
    except Exception as exc:  # the catalogue must never stop a message going out
        log.debug("sender catalogue unavailable: %s", exc)
        return None


def send_whatsapp(db: Session, to: str, body: str, template: Optional[str] = None, purpose: str = "general") -> dict:
    """Send one WhatsApp message.

    Every send goes through the sender catalogue first: the active, connected WhatsApp Number for ``purpose``
    (general | academics | billing | marketing) whose throttle allows a message now is used, its
    ``last_message_sent_at`` is stamped, and the result carries ``sender`` / ``sender_id`` so the caller can
    record which number spoke. With no catalogue entry ready the env-configured WHATSAPP_PHONE_ID is used.
    """
    sender = _catalogue_sender(db, purpose)
    phone_id = settings.WHATSAPP_PHONE_ID
    sender_info = {}
    if sender is not None:
        sender.last_message_sent_at = datetime.utcnow()
        sender_info = {"sender": sender.number, "sender_id": sender.id, "sender_purpose": sender.purpose}
    if not settings.WHATSAPP_TOKEN or not phone_id:
        log.info("[SIMULATED WhatsApp] to=%s via=%s body=%s", to, sender_info.get("sender") or "env", body[:80])
        _record(db, "whatsapp", True, simulated=True)
        return {"simulated": True, "id": f"wamid.sim.{int(time.time()*1000)}", **sender_info}
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    payload: dict[str, Any] = {"messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "text", "text": {"body": body}}
    if template:
        payload = {"messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "template",
                   "template": {"name": template, "language": {"code": "en"}}}
    try:
        r = httpx.post(url, json=payload, headers={"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}, timeout=15)
        r.raise_for_status()
        _record(db, "whatsapp", True)
        out = r.json()
        if isinstance(out, dict):
            out.update(sender_info)
        return out
    except Exception as exc:
        _record(db, "whatsapp", False, str(exc))
        raise


# ----------------------------------------------------------------------------- Email
def send_email(db: Session, to: str, subject: str, body: str, html: bool = False) -> dict:
    if not settings.SMTP_HOST:
        log.info("[SIMULATED Email] to=%s subject=%s", to, subject)
        _record(db, "smtp", True, simulated=True)
        return {"simulated": True}
    msg = MIMEText(body, "html" if html else "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, settings.SMTP_FROM, to
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as s:
            s.starttls()
            if settings.SMTP_USER:
                s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            s.send_message(msg)
        _record(db, "smtp", True)
        return {"sent": True}
    except Exception as exc:
        _record(db, "smtp", False, str(exc))
        raise


# ----------------------------------------------------------------------------- GoHighLevel
def ghl_upsert_contact(db: Session, contact: dict) -> dict:
    if not settings.GHL_API_KEY:
        log.info("[SIMULATED GHL] upsert %s", contact.get("email") or contact.get("phone"))
        _record(db, "ghl", True, simulated=True)
        return {"simulated": True, "id": f"ghl_sim_{abs(hash(json.dumps(contact, sort_keys=True))) % 10**8}"}
    try:
        r = httpx.post("https://services.leadconnectorhq.com/contacts/upsert", json=contact,
                       headers={"Authorization": f"Bearer {settings.GHL_API_KEY}", "Version": "2021-07-28"}, timeout=15)
        r.raise_for_status()
        _record(db, "ghl", True)
        return r.json().get("contact", r.json())
    except Exception as exc:
        _record(db, "ghl", False, str(exc))
        raise


# ----------------------------------------------------------------------------- Outbound webhooks (n8n etc.)
def emit_event(db: Session, event: str, payload: dict) -> None:
    """Queue an outbound webhook delivery for every active webhook subscribed to ``event``, and the matching
    GoHighLevel changes (complaints, referrals; app.services.ghl_sync)."""
    try:
        from app.services import ghl_sync
        with db.begin_nested():
            ghl_sync.on_webhook_event(db, event, payload)
    except Exception:  # pragma: no cover - a sync problem never blocks the event
        log.exception("could not queue GHL sync for %s", event)
    hooks = db.query(Webhook).filter(Webhook.is_active.is_(True)).all()
    for hook in hooks:
        if hook.events and event not in hook.events and "*" not in hook.events:
            continue
        db.add(WebhookDelivery(webhook_id=hook.id, direction="out", event=event, payload=payload, status="pending",
                               next_retry_at=datetime.utcnow()))


def deliver_pending_webhooks(db: Session, limit: int = 20) -> int:
    """Attempt pending deliveries with exponential backoff. Returns number processed."""
    from datetime import timedelta
    q = db.query(WebhookDelivery).filter(WebhookDelivery.status.in_(["pending", "failed"]),
                                         WebhookDelivery.direction == "out",
                                         WebhookDelivery.next_retry_at <= datetime.utcnow()).limit(limit)
    n = 0
    for d in q.all():
        n += 1
        d.attempts += 1
        hook = d.webhook
        if hook is None:
            d.status = "dead"
            continue
        try:
            r = httpx.post(hook.url, json={"event": d.event, "data": d.payload, "sent_at": datetime.utcnow().isoformat()},
                           headers={"X-OQC-Signature": hook.secret or ""}, timeout=10)
            d.response_code = r.status_code
            d.response_body = r.text[:500]
            d.status = "success" if r.status_code < 300 else "failed"
            hook.last_status = d.status
            hook.last_triggered_at = datetime.utcnow()
            _record(db, "n8n", d.status == "success", None if d.status == "success" else f"HTTP {r.status_code}")
        except Exception as exc:
            d.status = "failed"
            d.response_body = str(exc)[:500]
            _record(db, "n8n", False, str(exc))
        if d.status == "failed":
            if d.attempts >= 6:
                d.status = "dead"
            else:
                d.next_retry_at = datetime.utcnow() + timedelta(minutes=2 ** d.attempts)
    db.commit()
    return n


# ----------------------------------------------------------------------------- Video rooms
def build_join_url(room_name: str, display_name: str = "") -> str:
    if settings.VIDEO_PROVIDER == "jitsi":
        base = f"https://{settings.JITSI_DOMAIN}/{room_name}"
        return base + (f'#userInfo.displayName="{display_name}"' if display_name else "")
    return f"/classroom/{room_name}"


def health_check_all(db: Session) -> None:
    """Refresh status for every provider (configured vs simulated)."""
    configured = {
        "whatsapp": bool(settings.WHATSAPP_TOKEN),
        "ghl": bool(settings.GHL_API_KEY),
        "smtp": bool(settings.SMTP_HOST),
        "ai": settings.AI_PROVIDER != "simulated" and bool(settings.AI_API_KEY),
        "video": True,
    }
    for provider, name in PROVIDERS.items():
        integ = get_integration(db, provider)
        integ.name = name
        if integ.status == "error":
            continue
        if configured.get(provider):
            integ.status = "connected"
            integ.health = "healthy"
        elif provider in ("whatsapp", "ghl", "smtp", "ai", "n8n", "video"):
            integ.status = "simulated"
            integ.health = "healthy"
        integ.last_health_check_at = datetime.utcnow()
    db.commit()
