"""PUBLIC inbound webhooks: WhatsApp Cloud API, GoHighLevel, Meta lead forms, n8n.

Every request is persisted as a ``WebhookDelivery(direction="in")`` before it is processed so nothing is lost.
These endpoints are intentionally unauthenticated (providers sign/verify by other means); they are rate-limited
by the provider and validated defensively.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.database import get_db
from app.models.core import WebhookDelivery, Setting
from app.models.crm import Campaign, Lead, LeadSource
from app.services import crm as svc

log = logging.getLogger("oqc.webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _log_delivery(db: Session, event: str, payload: Any, status: str = "success", response: str = "") -> WebhookDelivery:
    d = WebhookDelivery(webhook_id=None, direction="in", event=event,
                        payload=payload if isinstance(payload, dict) else {"body": payload},
                        status=status, attempts=1, response_code=200, response_body=response[:500] or None,
                        created_at=datetime.utcnow())
    db.add(d)
    db.flush()
    return d


async def _json(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {"items": data}


class WebhookAck(BaseModel):
    ok: bool = True
    event: str
    delivery_id: int
    detail: dict = {}


# ----------------------------------------------------------------------------- WhatsApp Cloud API
@router.get("/whatsapp", include_in_schema=True)
def whatsapp_verify(request: Request, db: Session = Depends(get_db)):
    """Meta verification handshake: echo hub.challenge when hub.verify_token matches."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge") or ""
    expected = svc.setting(db, "whatsapp_verify_token", "oqc-verify") or "oqc-verify"
    _log_delivery(db, "whatsapp.verify", dict(params), status="success" if token == expected else "failed")
    db.commit()
    if mode == "subscribe" and token == expected:
        return Response(content=challenge, media_type="text/plain")
    return Response(content="verification failed", media_type="text/plain", status_code=403)


@router.post("/whatsapp", response_model=WebhookAck)
async def whatsapp_inbound(request: Request, db: Session = Depends(get_db)):
    payload = await _json(request)
    delivery = _log_delivery(db, "whatsapp.inbound", payload)
    created_leads, messages, statuses = 0, 0, 0
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            contacts = {c.get("wa_id"): (c.get("profile", {}) or {}).get("name") for c in (value.get("contacts") or [])}
            for msg in value.get("messages", []) or []:
                phone = msg.get("from") or ""
                body = ((msg.get("text") or {}).get("body")) or msg.get("type") or "(media message)"
                name = contacts.get(phone)
                when = (datetime.fromtimestamp(int(msg["timestamp"]), tz=timezone.utc).replace(tzinfo=None)
                        if str(msg.get("timestamp", "")).isdigit() else None)
                conv, m, made = svc.receive_message(db, phone, body, name=name, external_id=msg.get("id"), when=when)
                messages += 1
                created_leads += 1 if made else 0
            for st in value.get("statuses", []) or []:
                from app.models.crm import Message
                row = db.query(Message).filter(Message.external_id == st.get("id")).first()
                if row and st.get("status") in ("sent", "delivered", "read", "failed"):
                    row.status = st["status"]
                    statuses += 1
    delivery.response_body = f"messages={messages} leads={created_leads} statuses={statuses}"
    db.commit()
    return WebhookAck(event="whatsapp.inbound", delivery_id=delivery.id,
                      detail={"messages": messages, "leads_created": created_leads, "status_updates": statuses})


# ----------------------------------------------------------------------------- GoHighLevel
@router.post("/ghl", response_model=WebhookAck)
async def ghl_inbound(request: Request, db: Session = Depends(get_db)):
    payload = await _json(request)
    delivery = _log_delivery(db, "ghl.contact", payload)
    contact = payload.get("contact") or payload
    lead, created = svc.upsert_lead_from_ghl(db, contact)
    delivery.response_body = f"lead={lead.lead_code} created={created}"
    log_action(db, None, "sync", "leads", entity=lead, description=f"GHL webhook {'created' if created else 'updated'} {lead.lead_code}")
    db.commit()
    return WebhookAck(event="ghl.contact", delivery_id=delivery.id, detail={"lead_id": lead.id, "lead_code": lead.lead_code, "created": created})


# ----------------------------------------------------------------------------- Meta lead ads
@router.post("/meta-lead", response_model=WebhookAck)
async def meta_lead(request: Request, db: Session = Depends(get_db)):
    payload = await _json(request)
    delivery = _log_delivery(db, "meta.lead", payload)
    fields = payload.get("field_data") or payload.get("fields") or []
    data = {f.get("name"): (f.get("values") or [None])[0] for f in fields if isinstance(f, dict)}
    data.update({k: v for k, v in payload.items() if isinstance(v, (str, int)) and k not in ("field_data", "fields")})
    name = data.get("full_name") or data.get("name") or "Meta lead"
    campaign = None
    cid = data.get("campaign_id") or payload.get("campaign_id")
    if cid:
        campaign = db.query(Campaign).filter(Campaign.external_id == str(cid)).first()
    if not campaign and (data.get("campaign_name") or payload.get("campaign_name")):
        campaign = db.query(Campaign).filter(Campaign.name == (data.get("campaign_name") or payload.get("campaign_name"))).first()
    src = db.query(LeadSource).filter(LeadSource.name == "Meta Ads").first()
    lead, dups = svc.create_lead(db, {"full_name": name, "email": data.get("email"), "phone": data.get("phone_number") or data.get("phone"),
                                      "whatsapp": data.get("phone_number") or data.get("phone"), "country": data.get("country"),
                                      "student_name": data.get("student_name"), "source_id": src.id if src else None,
                                      "campaign_id": campaign.id if campaign else None, "generator_id": None,
                                      "notes": "Created from a Meta lead form"}, actor=None)
    delivery.response_body = f"lead={lead.lead_code} duplicates={len(dups)}"
    db.commit()
    return WebhookAck(event="meta.lead", delivery_id=delivery.id,
                      detail={"lead_id": lead.id, "lead_code": lead.lead_code, "duplicates": len(dups),
                              "campaign": campaign.name if campaign else None})


# ----------------------------------------------------------------------------- n8n / generic
@router.post("/n8n", response_model=WebhookAck)
async def n8n_inbound(request: Request, db: Session = Depends(get_db)):
    payload = await _json(request)
    event = payload.get("event") or "n8n.event"
    delivery = _log_delivery(db, event, payload)
    handled = {}
    if event == "lead.created":
        src = db.query(LeadSource).filter(LeadSource.name == "GHL").first()
        lead, _ = svc.create_lead(db, {"full_name": payload.get("name") or "Automation lead", "email": payload.get("email"),
                                       "phone": payload.get("phone"), "whatsapp": payload.get("phone"), "country": payload.get("country"),
                                       "source_id": src.id if src else None, "generator_id": None,
                                       "notes": "Created by an n8n automation"}, actor=None)
        handled = {"lead_id": lead.id, "lead_code": lead.lead_code}
    delivery.response_body = str(handled)[:200] or "acknowledged"
    db.commit()
    return WebhookAck(event=event, delivery_id=delivery.id, detail=handled)
