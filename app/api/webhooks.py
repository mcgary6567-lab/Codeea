"""PUBLIC inbound webhooks: WhatsApp Cloud API, GoHighLevel, Meta lead forms, n8n, the payment gateway and Zoom.

Every request is persisted as a ``WebhookDelivery(direction="in")`` before it is processed so nothing is lost.
The endpoints carry no platform login; the caller proves itself with the provider's inbound secret
(``integrations.inbound_secret``: the Integration row's configuration, then the environment) in one of two ways:

* an HMAC-SHA256 hex digest of the raw request body in the provider's signature header
  (``X-Hub-Signature-256: sha256=<hex>`` for Meta, ``X-Signature`` for the payment gateway,
  ``X-Webhook-Signature`` for GoHighLevel, n8n and Zoom; Zoom's own ``x-zm-signature`` is also understood), or
* the shared secret itself in ``X-Webhook-Token``.

Comparisons are constant-time. With no secret configured anywhere the request is refused with 503 in
production and accepted with a logged warning elsewhere, so a development box keeps working.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import log_action
from app.database import get_db
from app.models.core import WebhookDelivery
from app.models.crm import Campaign, LeadSource
from app.services import crm as svc
from app.services import integrations

log = logging.getLogger("oqc.webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# provider -> the header its signature arrives in (the shared token is always X-Webhook-Token)
SIGNATURE_HEADERS = {
    "meta_ads": "X-Hub-Signature-256",
    "whatsapp": "X-Hub-Signature-256",
    "ghl": "X-Webhook-Signature",
    "n8n": "X-Webhook-Signature",
    "payment": "X-Signature",
    "zoom": "X-Webhook-Signature",
}
TOKEN_HEADER = "X-Webhook-Token"


def _hex_digest(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _signature_matches(secret: str, body: bytes, presented: str, request: Request) -> bool:
    """``presented`` may be a bare hex digest, ``sha256=<hex>`` (Meta) or ``v0=<hex>`` over Zoom's
    ``v0:{timestamp}:{body}`` message."""
    presented = (presented or "").strip()
    if not presented:
        return False
    if presented.lower().startswith("v0="):
        ts = request.headers.get("x-zm-request-timestamp", "")
        expected = _hex_digest(secret, f"v0:{ts}:".encode("utf-8") + body)
        return hmac.compare_digest(presented[3:].lower(), expected)
    if presented.lower().startswith("sha256="):
        presented = presented[7:]
    return hmac.compare_digest(presented.lower(), _hex_digest(secret, body))


async def verify_inbound(request: Request, db: Session, provider: str) -> bytes:
    """Authenticate an inbound webhook and return the raw body it was signed over.

    Raises 401 when a secret is configured and neither the signature nor the shared token matches, and 503 when
    no secret is configured anywhere while running in production.
    """
    body = await request.body()
    secret = integrations.inbound_secret(db, provider)
    if not secret:
        if settings.APP_ENV == "production":
            _log_delivery(db, f"{provider}.rejected", {"reason": "webhook secret not configured"}, status="failed",
                          response="503 webhook secret not configured")
            db.commit()
            raise HTTPException(status_code=503, detail="webhook secret not configured")
        log.warning("inbound %s webhook accepted without verification: no secret configured (APP_ENV=%s)",
                    provider, settings.APP_ENV)
        return body
    token = request.headers.get(TOKEN_HEADER, "")
    if token and hmac.compare_digest(token.strip(), secret):
        return body
    sig_header = SIGNATURE_HEADERS.get(provider, "X-Webhook-Signature")
    presented = request.headers.get(sig_header, "") or request.headers.get("x-zm-signature", "")
    if _signature_matches(secret, body, presented, request):
        return body
    _log_delivery(db, f"{provider}.rejected", {"reason": "signature mismatch", "header": sig_header,
                                               "signature_present": bool(presented), "token_present": bool(token)},
                  status="failed", response="401 invalid webhook signature")
    db.commit()
    raise HTTPException(status_code=401, detail="invalid webhook signature")


def _parse_body(body: bytes) -> dict:
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {"items": data}


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
    """GoHighLevel contact webhook. Signed with the GHL inbound secret (X-Webhook-Signature / X-Webhook-Token)."""
    payload = _parse_body(await verify_inbound(request, db, "ghl"))
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
    """Meta lead-form webhook. Signed with the app secret (X-Hub-Signature-256: sha256=<hex>) or X-Webhook-Token."""
    payload = _parse_body(await verify_inbound(request, db, "meta_ads"))
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
    """n8n automation webhook. Signed with the n8n inbound secret (X-Webhook-Signature / X-Webhook-Token)."""
    payload = _parse_body(await verify_inbound(request, db, "n8n"))
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


# ----------------------------------------------------------------------------- payment gateway
@router.post("/payment", status_code=201,
             summary="Payment gateway callback (public; X-Signature = HMAC-SHA256 of the body with PAYMENT_WEBHOOK_SECRET)")
async def payment_inbound(request: Request, db: Session = Depends(get_db)):
    """The gateway posts ``{invoice_number, amount, currency?, reference, method?, gateway?, status?, received_at?}``.

    The secret is the payment Integration's ``webhook_secret`` or ``PAYMENT_WEBHOOK_SECRET``. The payment is
    recorded and confirmed through the same service as the signed-in ``/api/v1/finance/payment-webhook`` route,
    idempotently on ``reference``: a repeat delivery answers 200 with the existing payment instead of 201.
    """
    from pydantic import ValidationError
    from app.api.finance import WebhookIn, apply_gateway_payment
    data = _parse_body(await verify_inbound(request, db, "payment"))
    delivery = _log_delivery(db, "payment.webhook", data)
    try:
        payload = WebhookIn(**data)
    except ValidationError as exc:
        delivery.status, delivery.response_body, delivery.response_code = "failed", f"422 {str(exc)[:400]}", 422
        db.commit()
        raise HTTPException(status_code=422, detail="invoice_number, amount and reference are required")
    try:
        payment, created = apply_gateway_payment(db, payload, None, request=request, source="Gateway webhook")
    except HTTPException as exc:
        delivery.status, delivery.response_body, delivery.response_code = "failed", f"{exc.status_code} {exc.detail}", exc.status_code
        db.commit()
        raise
    delivery.response_body = f"payment={payment.payment_number} status={payment.status} created={created}"
    delivery.response_code = 201 if created else 200
    db.commit()
    return JSONResponse(status_code=201 if created else 200,
                        content={"ok": True, "event": "payment.webhook", "delivery_id": delivery.id, "created": created,
                                 "payment_number": payment.payment_number, "status": payment.status,
                                 "invoice_number": payload.invoice_number, "reference": payload.reference,
                                 "amount": float(payment.amount), "currency": payment.currency})


# ----------------------------------------------------------------------------- Zoom recordings
@router.post("/zoom", summary="Zoom event webhook (public; shared token or signature with ZOOM_WEBHOOK_SECRET)")
async def zoom_inbound(request: Request, db: Session = Depends(get_db)):
    """Zoom's ``endpoint.url_validation`` handshake is answered with the HMAC of ``plainToken``; a
    ``recording.completed`` event creates the CallRecord the QA call pipeline reviews (mapped to the teacher's
    class when the host email and start time match one). Other events are acknowledged and stored."""
    from app.services import calls as calls_svc
    payload = _parse_body(await verify_inbound(request, db, "zoom"))
    event = str(payload.get("event") or "zoom.event")
    inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    if event == "endpoint.url_validation":
        plain = str(inner.get("plainToken") or payload.get("plainToken") or "")
        secret = integrations.inbound_secret(db, "zoom")
        _log_delivery(db, "zoom.url_validation", {"plainToken": plain})
        db.commit()
        return {"plainToken": plain, "encryptedToken": _hex_digest(secret, plain.encode("utf-8")) if secret else ""}
    delivery = _log_delivery(db, event, payload)
    handled: dict = {}
    if event == "recording.completed":
        obj = inner.get("object") if isinstance(inner.get("object"), dict) else (payload.get("object") if isinstance(payload.get("object"), dict) else payload)
        call, created = calls_svc.ingest_zoom_recording(db, obj or {}, None, request=request)
        handled = {"call_id": call.id, "created": created, "session_id": call.session_id, "teacher_id": call.teacher_id,
                   "review_state": call.review_state}
    delivery.response_body = (str(handled) if handled else "acknowledged")[:500]
    db.commit()
    return WebhookAck(event=event, delivery_id=delivery.id, detail=handled)
