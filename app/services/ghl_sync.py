"""Two-way GoHighLevel sync (docs/GHL_INTEGRATION.md).

GHL runs lead generation, marketing, the sales pipeline and nurturing; the ERP owns families, students and everything
academic. This module keeps the two in step without manual entry.

Outbound. Every lifecycle event the ERP already emits (``automation.emit``, ``integrations.emit_event``) and every
ERP tag change is turned into GHL operations by ``EVENT_MAP``: upsert the contact (with the ERP codes as custom
fields), add or remove tags, move the pipeline opportunity. Operations are queued as ``GhlSyncJob`` rows and sent
by the worker (``process``) with retries and backoff; after ``MAX_ATTEMPTS`` failures a job is *dead*, raises an
integration alert and waits for a manual retry. An identical pending job is never queued twice.

Identifiers. The GHL contact id is stored on the Lead and carried to the Client when the lead enrols; GHL receives
the ERP codes (lead, family, students) as custom fields. GHL's own upsert de-duplicates by email and phone.

Inbound. GHL contact webhooks update leads (GHL owns a lead's contact details). For a lead that has become a
family the ERP owns the record: differing details are logged as a *conflict* for review rather than overwritten.
Opportunity-stage webhooks move the lead's ERP stage. Inbound processing sets ``db.info["ghl_inbound"]``, so the
change it causes is not echoed back to GHL.

Without GHL_API_KEY every call is simulated: the queue, the log and the ids behave the same, nothing leaves the
server.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import log_action
from app.core.notify import notify
from app.models.core import RiskAlert, Role, User
from app.models.crm import GhlSyncJob, Lead
from app.models.people import Client, Student

log = logging.getLogger("oqc.ghl")

API_BASE = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"
MAX_ATTEMPTS = 6
BACKOFF_MINUTES = [2, 5, 15, 60, 240, 720]
BATCH = 40  # GHL allows 100 requests per 10 seconds per location; the worker runs every two minutes
ENABLED_KEY = "enabled"  # Integration.config["enabled"] == "no" pauses the outbound queue

# ERP stage -> the stage names GHL pipelines commonly use (overridable per stage in the integration's stage map)
STAGE_NAMES = {"new": "New Lead", "contacted": "Contacted", "trial_scheduled": "Demo Booked", "trial_done": "Demo Completed",
               "negotiation": "Admission Review", "payment_pending": "Payment Pending", "won": "Enrolled", "lost": "Lost"}

# event -> operations. Each operation is (kind, options); "{key}" in a tag is filled from the event payload.
EVENT_MAP: dict[str, list[tuple[str, dict]]] = {
    "lead.created": [("upsert_contact", {})],
    "lead.stage_changed": [("opportunity", {"stage": "{to}"})],
    "lead.lost": [("opportunity", {"status": "lost"}), ("add_tags", {"tags": ["erp:lost"]})],
    "lead.converted": [("upsert_contact", {}), ("add_tags", {"tags": ["erp:enrolled"]}), ("opportunity", {"stage": "won", "status": "won"})],
    "trial.scheduled": [("add_tags", {"tags": ["trial:scheduled"]})],
    "trial.attended": [("add_tags", {"tags": ["trial:attended"]}), ("remove_tags", {"tags": ["trial:scheduled"]})],
    "trial.no_show": [("add_tags", {"tags": ["trial:no-show"]}), ("remove_tags", {"tags": ["trial:scheduled"]})],
    "payment.first": [("add_tags", {"tags": ["erp:paying"]})],
    "subscription.frozen": [("add_tags", {"tags": ["erp:frozen"]})],
    "subscription.resumed": [("remove_tags", {"tags": ["erp:frozen"]})],
    "subscription.cancelled": [("add_tags", {"tags": ["erp:cancelled"]})],
    "course.completed": [("add_tags", {"tags": ["erp:course-completed"]})],
    "student.at_risk": [("add_tags", {"tags": ["erp:at-risk"]})],
    "tag.added": [("add_tags", {"tags": ["{tag}"]})],
    "tag.removed": [("remove_tags", {"tags": ["{tag}"]})],
    "referral.lead": [("add_tags", {"tags": ["referral:referred"]}), ("add_note", {"note": "Referred by an ERP family ({code})."})],
    "referral.ambassador": [("add_tags", {"tags": ["erp:ambassador"]})],
    "referral.credited": [("add_tags", {"tags": ["referral:credited"]})],
    "complaint.opened": [("add_tags", {"tags": ["erp:complaint-open"]})],
    "complaint.closed": [("remove_tags", {"tags": ["erp:complaint-open"]}), ("add_tags", {"tags": ["erp:complaint-resolved"]})],
}
EVENT_LABELS = {"lead.created": "New lead in the ERP", "lead.stage_changed": "Lead moved to a stage", "lead.lost": "Lead lost",
                "lead.converted": "Lead enrolled as a family", "trial.scheduled": "Trial booked", "trial.attended": "Trial attended",
                "trial.no_show": "Trial no-show", "payment.first": "First payment", "subscription.frozen": "Subscription frozen",
                "subscription.resumed": "Subscription resumed", "subscription.cancelled": "Subscription cancelled",
                "course.completed": "Course completed", "student.at_risk": "Student at risk", "tag.added": "ERP tag added",
                "tag.removed": "ERP tag removed", "referral.lead": "Referred person added as a lead",
                "referral.ambassador": "Family referred someone", "referral.credited": "Referral credit applied",
                "complaint.opened": "Complaint opened", "complaint.closed": "Complaint closed",
                "feedback.submitted": "Feedback (promoter / detractor)"}


# ============================================================================ configuration
def integration(db: Session):
    from app.services.integrations import get_integration
    return get_integration(db, "ghl")


def config(db: Session) -> dict:
    return dict(integration(db).config or {})


def is_live() -> bool:
    return bool(settings.GHL_API_KEY)


def location_id(db: Session) -> str:
    return (config(db).get("location_id") or os.environ.get("GHL_LOCATION_ID") or "").strip()


def enabled(db: Session) -> bool:
    """Off until an administrator turns it on: the demo data seeded on every deploy must never reach a real GHL
    account, and sending contacts to GHL is a decision, not a default."""
    return str(config(db).get(ENABLED_KEY, "no")).strip().lower() in ("yes", "on", "true", "1")


def stage_map(db: Session) -> dict:
    """ERP stage -> GHL pipeline stage id, from the integration's "stage_map" (JSON) plus won/lost stage ids."""
    cfg = config(db)
    out: dict = {}
    raw = cfg.get("stage_map") or ""
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict):
                out.update({str(k): str(v) for k, v in parsed.items() if v})
        except ValueError:
            log.warning("ghl stage_map is not valid JSON")
    if cfg.get("won_stage_id"):
        out.setdefault("won", cfg["won_stage_id"])
    if cfg.get("lost_stage_id"):
        out.setdefault("lost", cfg["lost_stage_id"])
    return out


# ============================================================================ contacts
def resolve(db: Session, contact_type: str, contact_id: int) -> tuple[Optional[str], Optional[int]]:
    """The ERP record a GHL contact belongs to: a lead, or the family (a student resolves to their family)."""
    if contact_type == "lead":
        lead = db.get(Lead, contact_id)
        if lead and lead.converted_client_id:
            return "client", lead.converted_client_id
        return ("lead", contact_id) if lead else (None, None)
    if contact_type == "client":
        return ("client", contact_id) if db.get(Client, contact_id) else (None, None)
    if contact_type == "student":
        st = db.get(Student, contact_id)
        return ("client", st.client_id) if st else (None, None)
    return None, None


def _record(db: Session, entity_type: str, entity_id: int):
    return db.get(Lead if entity_type == "lead" else Client, entity_id)


def contact_payload(db: Session, entity_type: str, entity_id: int) -> dict:
    """The GHL contact for an ERP lead or family: identity, the ERP codes and status as custom fields."""
    rec = _record(db, entity_type, entity_id)
    if rec is None:
        return {}
    name = (rec.full_name or "").strip()
    first, _, last = name.partition(" ")
    body = {"locationId": location_id(db), "firstName": first or name, "lastName": last, "name": name,
            "email": rec.email or None, "phone": (getattr(rec, "whatsapp", None) or rec.phone or None),
            "country": rec.country or None, "source": (config(db).get("source_tag") or "OQC ERP")}
    fields = []
    if entity_type == "lead":
        fields += [("erp_lead_code", rec.lead_code), ("erp_stage", rec.stage)]
    else:
        students = [s for s in rec.students]
        fields += [("erp_client_code", rec.client_code), ("erp_status", rec.status),
                   ("erp_students", ", ".join(f"{s.full_name} ({s.student_code})" for s in students)),
                   ("erp_courses", ", ".join(sorted({s.course.name for s in students if s.course}))),
                   ("erp_teachers", ", ".join(sorted({s.teacher.full_name for s in students if s.teacher})))]
    if str(config(db).get("custom_fields", "yes")).lower() not in ("no", "off", "false"):
        body["customFields"] = [{"key": k, "field_value": v} for k, v in fields if v]
    return {k: v for k, v in body.items() if v not in (None, "")}


def contact_id_of(db: Session, entity_type: str, entity_id: int) -> Optional[str]:
    rec = _record(db, entity_type, entity_id)
    return getattr(rec, "ghl_contact_id", None) if rec else None


def _store_contact_id(db: Session, entity_type: str, entity_id: int, ghl_id: str) -> None:
    rec = _record(db, entity_type, entity_id)
    if rec is not None and ghl_id and rec.ghl_contact_id != ghl_id:
        rec.ghl_contact_id = ghl_id
        if entity_type == "client":
            lead = db.query(Lead).filter(Lead.converted_client_id == entity_id).first()
            if lead is not None and not lead.ghl_contact_id:
                lead.ghl_contact_id = ghl_id


# ============================================================================ the GHL API
class GhlError(Exception):
    pass


def _request(method: str, path: str, body: Optional[dict] = None) -> dict:
    """One call to the GHL API v2. Simulated (no network) without GHL_API_KEY."""
    if not is_live():
        digest = hashlib.sha1(json.dumps([method, path, body], sort_keys=True, default=str).encode()).hexdigest()[:10]
        return {"simulated": True, "id": f"ghl_sim_{digest}", "contact": {"id": f"ghl_sim_{digest}"}}
    import httpx
    try:
        r = httpx.request(method, API_BASE + path, json=body, timeout=20,
                          headers={"Authorization": f"Bearer {settings.GHL_API_KEY}", "Version": API_VERSION, "Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise GhlError(f"network: {exc}") from exc
    if r.status_code == 429:
        raise GhlError("rate limited by GHL (429)")
    if r.status_code >= 400:
        raise GhlError(f"HTTP {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except ValueError:
        return {}


def api_upsert_contact(body: dict) -> str:
    data = _request("POST", "/contacts/upsert", body)
    ghl_id = (data.get("contact") or {}).get("id") or data.get("id")
    if not ghl_id:
        raise GhlError("GHL did not return a contact id")
    return str(ghl_id)


def api_add_tags(contact_id: str, tags: list[str]) -> dict:
    return _request("POST", f"/contacts/{contact_id}/tags", {"tags": tags})


def api_remove_tags(contact_id: str, tags: list[str]) -> dict:
    return _request("DELETE", f"/contacts/{contact_id}/tags", {"tags": tags})


def api_add_note(contact_id: str, text: str) -> dict:
    return _request("POST", f"/contacts/{contact_id}/notes", {"body": text})


def api_opportunity(db: Session, contact_id: str, name: str, stage_id: Optional[str], status: Optional[str]) -> dict:
    pipeline = (config(db).get("pipeline_id") or "").strip()
    if not pipeline:
        return {"skipped": "no pipeline configured"}
    body = {"pipelineId": pipeline, "locationId": location_id(db), "contactId": contact_id, "name": name}
    if stage_id:
        body["pipelineStageId"] = stage_id
    if status:
        body["status"] = status
    return _request("POST", "/opportunities/upsert", body)


# ============================================================================ queueing
def _key(entity_type: str, entity_id: int, operation: str, data: dict) -> str:
    return hashlib.sha1(json.dumps([entity_type, entity_id, operation, data], sort_keys=True, default=str).encode()).hexdigest()


def enqueue(db: Session, entity_type: str, entity_id: int, operation: str, data: Optional[dict] = None, event: Optional[str] = None) -> Optional[GhlSyncJob]:
    data = data or {}
    key = _key(entity_type, entity_id, operation, data)
    if db.query(GhlSyncJob.id).filter(GhlSyncJob.dedupe_key == key, GhlSyncJob.status.in_(["pending", "failed"])).first():
        return None
    job = GhlSyncJob(direction="out", entity_type=entity_type, entity_id=entity_id, operation=operation, event=event, payload=data,
                     status="pending", next_attempt_at=datetime.utcnow(), dedupe_key=key,
                     ghl_contact_id=contact_id_of(db, entity_type, entity_id))
    db.add(job)
    db.flush()
    return job


def _fill(value: str, payload: dict) -> str:
    try:
        return value.format(**{k: str(v) for k, v in payload.items()})
    except (KeyError, IndexError, ValueError):
        return value


def on_event(db: Session, event: str, contact_type: str, contact_id: int, payload: Optional[dict] = None) -> int:
    """Called for every ERP lifecycle event. Queues the GHL operations EVENT_MAP lists for it. Returns jobs queued."""
    if db.info.get("ghl_inbound") or not enabled(db):
        return 0
    payload = dict(payload or {})
    if event == "feedback.submitted":
        nps = payload.get("nps")
        ops = ([("add_tags", {"tags": ["erp:promoter"]})] if nps is not None and int(nps) >= 9 else
               [("add_tags", {"tags": ["erp:detractor"]})] if payload.get("negative") else [])
    else:
        ops = EVENT_MAP.get(event, [])
    if not ops:
        return 0
    entity_type, entity_id = resolve(db, contact_type, contact_id)
    if entity_type is None:
        return 0
    n = 0
    for kind, opts in ops:
        data = {}
        if "tags" in opts:
            data["tags"] = [t for t in (_fill(t, payload) for t in opts["tags"]) if t and "{" not in t]
            if not data["tags"]:
                continue
        if "stage" in opts:
            data["stage"] = _fill(opts["stage"], payload)
        if "status" in opts:
            data["status"] = opts["status"]
        if "note" in opts:
            data["note"] = _fill(opts["note"], payload)
        if enqueue(db, entity_type, entity_id, kind, data, event=event) is not None:
            n += 1
    return n


def on_webhook_event(db: Session, event: str, payload: dict) -> int:
    """Platform events that are not automation events: complaints and referrals."""
    if db.info.get("ghl_inbound") or not enabled(db):
        return 0
    from app.models.crm import Case, Referral
    n = 0
    if event in ("case.opened", "case.status_changed") and payload.get("case_id"):
        case = db.get(Case, payload["case_id"])
        if case is not None and case.case_type == "complaint" and (case.client_id or case.student_id):
            kind = "complaint.opened" if event == "case.opened" else ("complaint.closed" if case.status == "closed" else None)
            if kind:
                n += on_event(db, kind, "client" if case.client_id else "student", case.client_id or case.student_id, {})
    elif event in ("referral.mentioned", "referral.credited") and payload.get("referral_id"):
        ref = db.get(Referral, payload["referral_id"])
        if ref is not None:
            n += on_event(db, "referral.credited" if event == "referral.credited" else "referral.ambassador", "client", ref.ambassador_client_id, {})
            if event == "referral.mentioned" and ref.referred_lead_id:
                n += on_event(db, "referral.lead", "lead", ref.referred_lead_id, {"code": ref.referral_code})
    return n


# ============================================================================ the worker
def _contact_for(db: Session, job: GhlSyncJob) -> str:
    ghl_id = contact_id_of(db, job.entity_type, job.entity_id)
    if not ghl_id:
        body = contact_payload(db, job.entity_type, job.entity_id)
        if not body:
            raise GhlError("the ERP record no longer exists")
        ghl_id = api_upsert_contact(body)
        _store_contact_id(db, job.entity_type, job.entity_id, ghl_id)
    return ghl_id


def run_job(db: Session, job: GhlSyncJob) -> dict:
    op, data = job.operation, job.payload or {}
    if op == "upsert_contact":
        body = contact_payload(db, job.entity_type, job.entity_id)
        if not body:
            raise GhlError("the ERP record no longer exists")
        ghl_id = api_upsert_contact(body)
        _store_contact_id(db, job.entity_type, job.entity_id, ghl_id)
        job.ghl_contact_id = ghl_id
        return {"contact_id": ghl_id}
    ghl_id = _contact_for(db, job)
    job.ghl_contact_id = ghl_id
    if op == "add_tags":
        return api_add_tags(ghl_id, data.get("tags") or [])
    if op == "remove_tags":
        return api_remove_tags(ghl_id, data.get("tags") or [])
    if op == "add_note":
        return api_add_note(ghl_id, data.get("note") or "")
    if op == "opportunity":
        rec = _record(db, job.entity_type, job.entity_id)
        stages = stage_map(db)
        stage = data.get("stage")
        stage_id = stages.get(stage) if stage else None
        if stage and not stage_id and not data.get("status"):
            return {"skipped": f"no GHL stage mapped for '{stage}'"}
        name = f"{rec.full_name if rec else 'OQC'} · {getattr(rec, 'lead_code', None) or getattr(rec, 'client_code', '')}"
        return api_opportunity(db, ghl_id, name, stage_id, data.get("status"))
    raise GhlError(f"unknown operation {op}")


def process(db: Session, limit: int = BATCH, now: Optional[datetime] = None) -> dict:
    """Send due jobs in order. Failures back off; after MAX_ATTEMPTS a job is dead and raises an alert."""
    now = now or datetime.utcnow()
    if not enabled(db):
        return {"skipped": "sync paused"}
    db.flush()  # the session does not autoflush: make jobs queued in this transaction visible
    jobs = (db.query(GhlSyncJob).filter(GhlSyncJob.direction == "out", GhlSyncJob.status.in_(["pending", "failed"]),
                                        or_(GhlSyncJob.next_attempt_at.is_(None), GhlSyncJob.next_attempt_at <= now))
            .order_by(GhlSyncJob.id).limit(limit).all())
    sent = failed = dead = 0
    from app.services.integrations import _record as record_call
    for job in jobs:
        job.attempts = (job.attempts or 0) + 1
        try:
            with db.begin_nested():
                result = run_job(db, job)
            job.status, job.response, job.last_error = ("skipped" if result.get("skipped") else "sent"), result, None
            job.simulated, job.processed_at, job.next_attempt_at = not is_live(), now, None
            record_call(db, "ghl", True, simulated=not is_live())
            sent += 1
        except Exception as exc:  # GhlError, or a bug: either way the job stays visible and retryable
            job.last_error = str(exc)[:500]
            record_call(db, "ghl", False, str(exc))
            if job.attempts >= MAX_ATTEMPTS:
                job.status, job.next_attempt_at = "dead", None
                dead += 1
                _alert_dead(db, job)
            else:
                job.status = "failed"
                job.next_attempt_at = now + timedelta(minutes=BACKOFF_MINUTES[min(job.attempts - 1, len(BACKOFF_MINUTES) - 1)])
                failed += 1
    db.flush()
    return {"processed": len(jobs), "sent": sent, "failed": failed, "dead": dead, "simulated": not is_live()}


def _alert_dead(db: Session, job: GhlSyncJob) -> None:
    db.add(RiskAlert(alert_type="ghl_sync_dead", severity="high", title=f"GHL sync failed {MAX_ATTEMPTS} times ({job.operation})",
                     message=f"{job.entity_type} #{job.entity_id}: {job.last_error}", entity_type="GhlSyncJob", entity_id=job.id,
                     visibility="ops", source="system"))
    for u in (db.query(User).join(Role, Role.id == User.role_id)
              .filter(User.is_active.is_(True), Role.slug.in_(["system_admin", "hod_technology"])).all()):
        notify(db, u, "GHL sync needs attention", f"A {job.operation} for {job.entity_type} #{job.entity_id} failed {MAX_ATTEMPTS} times: "
               f"{(job.last_error or '')[:150]}", event_type="integration", link="/admin/integrations/ghl/sync?status=dead")


def retry(db: Session, job: GhlSyncJob, user: Optional[User] = None) -> None:
    if job.direction != "out" or job.status not in ("failed", "dead", "skipped"):
        raise ValueError("Only failed, dead or skipped outbound jobs can be retried.")
    job.status, job.attempts, job.next_attempt_at, job.last_error = "pending", 0, datetime.utcnow(), None
    log_action(db, user, "update", "integrations", entity=job, description=f"GHL sync job #{job.id} queued again")


def sync_now(db: Session, entity_type: str, entity_id: int, user: Optional[User] = None) -> dict:
    """Push one record to GHL immediately (the "Sync to GHL" button)."""
    job = enqueue(db, entity_type, entity_id, "upsert_contact", {"manual": True}, event="manual")
    if job is None:
        job = (db.query(GhlSyncJob).filter(GhlSyncJob.entity_type == entity_type, GhlSyncJob.entity_id == entity_id,
                                           GhlSyncJob.operation == "upsert_contact", GhlSyncJob.status.in_(["pending", "failed"]))
               .order_by(GhlSyncJob.id.desc()).first())
    job.attempts = (job.attempts or 0) + 1
    try:
        result = run_job(db, job)
        job.status, job.response, job.processed_at, job.simulated, job.last_error = "sent", result, datetime.utcnow(), not is_live(), None
    except Exception as exc:
        job.status, job.last_error = "failed", str(exc)[:500]
        job.next_attempt_at = datetime.utcnow() + timedelta(minutes=BACKOFF_MINUTES[0])
    log_action(db, user, "sync", "integrations", entity=job, description=f"Sync to GHL: {entity_type} #{entity_id} ({job.status})")
    return {"status": job.status, "contact_id": job.ghl_contact_id, "error": job.last_error}


# ============================================================================ inbound
COMPARED_FIELDS = ("full_name", "email", "phone")


def _inbound_job(db: Session, operation: str, entity_type: str, entity_id: Optional[int], payload: dict, status: str = "received",
                 ghl_id: Optional[str] = None, error: Optional[str] = None) -> GhlSyncJob:
    job = GhlSyncJob(direction="in", entity_type=entity_type, entity_id=entity_id, operation=operation, payload=payload, status=status,
                     ghl_contact_id=ghl_id, processed_at=datetime.utcnow(), last_error=error, attempts=1)
    db.add(job)
    db.flush()
    return job


def handle_contact(db: Session, contact: dict) -> tuple[GhlSyncJob, Optional[Lead], bool]:
    """A GHL contact webhook. Leads take GHL's details; an enrolled family keeps the ERP's, and differences are
    queued as a conflict for review. Returns (log row, lead, created)."""
    from app.services import crm as crm_svc
    ghl_id = str(contact.get("id") or contact.get("contact_id") or "") or None
    name = contact.get("name") or " ".join(x for x in (contact.get("firstName") or contact.get("first_name"),
                                                       contact.get("lastName") or contact.get("last_name")) if x)
    incoming = {"full_name": (name or "").strip() or None, "email": (contact.get("email") or "").strip() or None,
                "phone": (contact.get("phone") or "").strip() or None}
    client = db.query(Client).filter(Client.ghl_contact_id == ghl_id).first() if ghl_id else None
    lead = db.query(Lead).filter(Lead.ghl_contact_id == ghl_id).first() if ghl_id else None
    if lead is not None and lead.converted_client_id and client is None:
        client = db.get(Client, lead.converted_client_id)
    db.info["ghl_inbound"] = True
    try:
        if client is not None:
            current = {"full_name": client.full_name, "email": client.email, "phone": client.whatsapp or client.phone}
            diff = {}
            for field, value in incoming.items():
                mine = current.get(field)
                if not value or not mine:
                    continue
                same = (crm_svc.digits(value) == crm_svc.digits(mine)) if field == "phone" else value.strip().lower() == mine.strip().lower()
                if not same:
                    diff[field] = {"erp": mine, "ghl": value}
            if diff:
                return _inbound_job(db, "contact", "client", client.id, {"contact": contact, "diff": diff}, status="conflict", ghl_id=ghl_id), lead, False
            return _inbound_job(db, "contact", "client", client.id, {"contact": contact}, ghl_id=ghl_id), lead, False
        lead, created = crm_svc.upsert_lead_from_ghl(db, contact)
        return _inbound_job(db, "contact", "lead", lead.id, {"contact": contact, "created": created}, ghl_id=ghl_id), lead, created
    finally:
        db.info.pop("ghl_inbound", None)


def _erp_stage(db: Session, payload: dict) -> Optional[str]:
    stage_id = str(payload.get("pipeline_stage_id") or payload.get("pipelineStageId") or payload.get("stage_id") or "")
    for erp, ghl in stage_map(db).items():
        if stage_id and str(ghl) == stage_id:
            return erp
    status = (payload.get("status") or "").lower()
    if status in ("won", "lost"):
        return status
    name = (payload.get("pipeline_stage") or payload.get("stage_name") or payload.get("pipelineStageName") or "").strip().lower()
    for erp, label in STAGE_NAMES.items():
        if name and (name == label.lower() or name == erp.replace("_", " ")):
            return erp
    return None


def handle_opportunity(db: Session, payload: dict) -> GhlSyncJob:
    """A GHL opportunity / pipeline-stage webhook: move the lead to the matching ERP stage (not echoed back)."""
    from app.services import crm as crm_svc
    ghl_id = str(payload.get("contact_id") or payload.get("contactId") or (payload.get("contact") or {}).get("id") or "") or None
    lead = db.query(Lead).filter(Lead.ghl_contact_id == ghl_id).first() if ghl_id else None
    stage = _erp_stage(db, payload)
    if lead is None:
        return _inbound_job(db, "stage", "lead", None, payload, status="skipped", ghl_id=ghl_id, error="no ERP lead for this GHL contact")
    if stage is None:
        return _inbound_job(db, "stage", "lead", lead.id, payload, status="skipped", ghl_id=ghl_id, error="GHL stage not mapped to an ERP stage")
    if lead.stage == stage or (lead.converted_client_id and stage != "won"):
        return _inbound_job(db, "stage", "lead", lead.id, payload, status="skipped", ghl_id=ghl_id,
                            error="already at this stage" if lead.stage == stage else "the lead has enrolled; the ERP owns it")
    db.info["ghl_inbound"] = True
    try:
        crm_svc.move_stage(db, lead, stage, None, reason="Moved in GoHighLevel")
    except ValueError as exc:
        return _inbound_job(db, "stage", "lead", lead.id, payload, status="skipped", ghl_id=ghl_id, error=str(exc)[:300])
    finally:
        db.info.pop("ghl_inbound", None)
    return _inbound_job(db, "stage", "lead", lead.id, {**payload, "erp_stage": stage}, ghl_id=ghl_id)


def resolve_conflict(db: Session, job: GhlSyncJob, user: User, choice: str, request=None) -> None:
    """keep_erp: GHL is updated from the ERP record. accept_ghl: the family record takes GHL's values."""
    if job.status != "conflict":
        raise ValueError("This entry is not a conflict.")
    if choice not in ("keep_erp", "accept_ghl"):
        raise ValueError("Choose which side to keep.")
    client = db.get(Client, job.entity_id) if job.entity_type == "client" else None
    if client is None:
        raise ValueError("The family no longer exists.")
    diff = (job.payload or {}).get("diff") or {}
    before = {k: v["erp"] for k, v in diff.items()}
    if choice == "accept_ghl":
        for field, values in diff.items():
            if field == "phone":
                client.whatsapp = values["ghl"]
                client.phone = client.phone or values["ghl"]
            elif field in ("full_name", "email"):
                setattr(client, field, values["ghl"])
    else:
        enqueue(db, "client", client.id, "upsert_contact", {"conflict": job.id}, event="conflict.keep_erp")
    job.status, job.resolution, job.resolved_by_id = "resolved", choice, user.id
    log_action(db, user, "update", "integrations", entity=client, request=request, consequential=True,
               description=f"GHL conflict #{job.id} resolved: {'took the GHL values' if choice == 'accept_ghl' else 'kept the ERP values'}",
               before=before, after={k: v["ghl"] if choice == "accept_ghl" else v["erp"] for k, v in diff.items()})


# ============================================================================ reporting
def stats(db: Session) -> dict:
    since = datetime.utcnow() - timedelta(hours=24)
    rows = dict(db.query(GhlSyncJob.status, func.count(GhlSyncJob.id)).group_by(GhlSyncJob.status).all())
    return {"pending": rows.get("pending", 0), "failed": rows.get("failed", 0), "dead": rows.get("dead", 0),
            "conflicts": rows.get("conflict", 0),
            "sent_24h": db.query(GhlSyncJob).filter(GhlSyncJob.status == "sent", GhlSyncJob.processed_at >= since).count(),
            "inbound_24h": db.query(GhlSyncJob).filter(GhlSyncJob.direction == "in", GhlSyncJob.created_at >= since).count(),
            "live": is_live(), "enabled": enabled(db), "location": location_id(db), "pipeline": config(db).get("pipeline_id") or "",
            "stages_mapped": len(stage_map(db))}


def last_sync(db: Session, entity_type: str, entity_id: int) -> Optional[GhlSyncJob]:
    return (db.query(GhlSyncJob).filter(GhlSyncJob.entity_type == entity_type, GhlSyncJob.entity_id == entity_id)
            .order_by(GhlSyncJob.id.desc()).first())
