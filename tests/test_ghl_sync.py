"""Two-way GoHighLevel sync (docs/GHL_INTEGRATION.md): the outbound queue with retries and dead-lettering, de-duplication,
the contact identifiers, inbound contacts with the conflict rule, inbound pipeline stages without echo, and the console.

Requested 9 Oct 2026 (item 14). Runs in simulation (no GHL_API_KEY): nothing leaves the machine.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.utils import next_code
from app.database import SessionLocal
from app.main import app
from app.models.core import AuditEvent, RiskAlert, Integration, Notification, WebhookDelivery
from app.models.automation import AutomationEvent, ContactTag, WorkflowRun
from app.models.crm import Case, CaseComment, GhlSyncJob, Lead, LeadActivity
from app.models.ops import Task
from app.models.people import Client, Student
from app.services import automation, ghl_sync
from app.services import crm as crm_svc

TAG = "ghltest" + uuid.uuid4().hex[:6]
SECRET = "whsec-" + uuid.uuid4().hex


def _admin() -> TestClient:
    c = TestClient(app)
    assert c.post("/login", data={"username": "admin@oqc.local", "password": "Admin@12345"}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture(scope="module")
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(scope="module")
def world(db):
    integ = ghl_sync.integration(db)
    saved = dict(integ.config or {})
    integ.config = {**saved, "enabled": "yes", "pipeline_id": "pipe-1", "location_id": "loc-1", "webhook_secret": SECRET,
                    "stage_map": '{"trial_scheduled": "stage-demo", "won": "stage-won"}'}
    client = Client(client_code=next_code(db, Client, "client_code", "C-"), full_name=f"{TAG} Family", email=f"{TAG}@example.com",
                    phone="+447700900888", whatsapp="+447700900888", country="United Kingdom", currency="GBP", status="active")
    db.add(client)
    db.flush()
    student = Student(student_code=next_code(db, Student, "student_code", "S-"), client_id=client.id, full_name=f"{TAG} Child", age=8, status="active")
    db.add(student)
    lead, _ = crm_svc.create_lead(db, {"full_name": f"{TAG} Lead", "email": f"{TAG}.lead@example.com", "phone": "+447700900999"}, auto_assign=False)
    db.commit()
    yield {"client": client, "student": student, "lead": lead}
    db.rollback()
    lead_ids = [l.id for l in db.query(Lead).filter(Lead.full_name.like(f"{TAG}%"))]
    case_ids = [c.id for c in db.query(Case).filter(Case.client_id == client.id)]
    job_ids = [j.id for j in db.query(GhlSyncJob).filter(
        ((GhlSyncJob.entity_type == "client") & (GhlSyncJob.entity_id == client.id)) |
        ((GhlSyncJob.entity_type == "lead") & GhlSyncJob.entity_id.in_(lead_ids or [-1])) | GhlSyncJob.ghl_contact_id.like(f"{TAG}%"))]
    db.query(RiskAlert).filter(RiskAlert.entity_type == "GhlSyncJob", RiskAlert.entity_id.in_(job_ids or [-1])).delete(synchronize_session=False)
    db.query(GhlSyncJob).filter(GhlSyncJob.id.in_(job_ids or [-1])).delete(synchronize_session=False)
    db.query(WebhookDelivery).filter(WebhookDelivery.event.like("ghl.%"), WebhookDelivery.created_at >= datetime.utcnow() - timedelta(hours=1)).delete(synchronize_session=False)
    for cid in case_ids:
        db.query(CaseComment).filter(CaseComment.case_id == cid).delete(synchronize_session=False)
        db.query(Task).filter(Task.entity_type == "Case", Task.entity_id == cid).delete(synchronize_session=False)
        db.query(Notification).filter(Notification.link.like(f"%/cases/{cid}")).delete(synchronize_session=False)
    db.query(Case).filter(Case.id.in_(case_ids or [-1])).delete(synchronize_session=False)
    for kind, ids in (("lead", lead_ids), ("client", [client.id]), ("student", [student.id])):
        db.query(ContactTag).filter(ContactTag.contact_type == kind, ContactTag.contact_id.in_(ids or [-1])).delete(synchronize_session=False)
        db.query(WorkflowRun).filter(WorkflowRun.contact_type == kind, WorkflowRun.contact_id.in_(ids or [-1])).delete(synchronize_session=False)
        db.query(AutomationEvent).filter(AutomationEvent.contact_type == kind, AutomationEvent.contact_id.in_(ids or [-1])).delete(synchronize_session=False)
    db.query(LeadActivity).filter(LeadActivity.lead_id.in_(lead_ids or [-1])).delete(synchronize_session=False)
    db.query(AuditEvent).filter(AuditEvent.entity_type == "Lead", AuditEvent.entity_id.in_(lead_ids or [-1])).delete(synchronize_session=False)
    db.query(Lead).filter(Lead.id.in_(lead_ids or [-1])).delete(synchronize_session=False)
    db.query(Student).filter(Student.id == student.id).delete(synchronize_session=False)
    db.query(Client).filter(Client.id == client.id).delete(synchronize_session=False)
    integ = db.query(Integration).filter(Integration.provider == "ghl").first()
    integ.config = saved
    db.commit()


def _jobs(db, entity_type, entity_id, **f):
    q = db.query(GhlSyncJob).filter(GhlSyncJob.entity_type == entity_type, GhlSyncJob.entity_id == entity_id)
    for k, v in f.items():
        q = q.filter(getattr(GhlSyncJob, k) == v)
    return q.order_by(GhlSyncJob.id).all()


# ------------------------------------------------------------------------------------------------ outbound
def test_sync_is_off_until_turned_on(db, world):
    integ = ghl_sync.integration(db)
    cfg = dict(integ.config)
    integ.config = {k: v for k, v in cfg.items() if k != "enabled"}
    assert not ghl_sync.enabled(db), "off by default: demo data must never reach a real GHL account"
    assert ghl_sync.on_event(db, "student.at_risk", "client", world["client"].id, {}) == 0
    integ.config = cfg
    db.commit()


def test_a_new_lead_is_upserted_and_gets_its_ghl_id(db, world):
    lead = world["lead"]
    jobs = _jobs(db, "lead", lead.id, operation="upsert_contact")
    assert jobs, "lead.created queues an upsert"
    out = ghl_sync.process(db)
    db.commit()
    assert out["simulated"] and out["sent"] >= 1
    db.refresh(lead)
    assert lead.ghl_contact_id and lead.ghl_contact_id.startswith("ghl_sim_")
    job = db.get(GhlSyncJob, jobs[0].id)
    assert job.status == "sent" and job.simulated and job.ghl_contact_id == lead.ghl_contact_id


def test_the_same_change_is_never_queued_twice(db, world):
    c = world["client"]
    first = ghl_sync.on_event(db, "student.at_risk", "student", world["student"].id, {})
    again = ghl_sync.on_event(db, "student.at_risk", "client", c.id, {})
    db.commit()
    assert first == 1 and again == 0, "a student resolves to the family, and the pending duplicate is skipped"
    job = _jobs(db, "client", c.id, operation="add_tags")[-1]
    assert job.payload == {"tags": ["erp:at-risk"]}


def test_a_family_carries_its_erp_codes_to_ghl(db, world):
    body = ghl_sync.contact_payload(db, "client", world["client"].id)
    fields = {f["key"]: f["field_value"] for f in body["customFields"]}
    assert fields["erp_client_code"] == world["client"].client_code and world["student"].student_code in fields["erp_students"]
    assert body["locationId"] == "loc-1" and body["phone"] == "+447700900888"


def test_erp_tags_are_mirrored_both_ways(db, world):
    c = world["client"]
    tag = automation.get_tag(db, "status:enrolled")
    assert tag is not None
    db.query(ContactTag).filter(ContactTag.contact_type == "client", ContactTag.contact_id == c.id).delete()  # ids get reused
    assert automation.add_tag(db, "client", c.id, "status:enrolled", "test")
    assert automation.remove_tag(db, "client", c.id, "status:enrolled")
    db.commit()
    ops = [(j.operation, j.payload.get("tags")) for j in _jobs(db, "client", c.id) if j.event in ("tag.added", "tag.removed")]
    assert ("add_tags", ["status:enrolled"]) in ops and ("remove_tags", ["status:enrolled"]) in ops


def test_stage_changes_move_the_opportunity_and_unmapped_stages_are_skipped(db, world):
    lead = world["lead"]
    crm_svc.move_stage(db, lead, "trial_scheduled", None, reason="test")
    crm_svc.move_stage(db, lead, "contacted", None, reason="test")
    db.commit()
    ghl_sync.process(db)
    db.commit()
    opp = {j.payload.get("stage"): j for j in _jobs(db, "lead", lead.id, operation="opportunity")}
    assert opp["trial_scheduled"].status == "sent"
    assert opp["contacted"].status == "skipped" and "no GHL stage mapped" in opp["contacted"].response.get("skipped", "")


def test_failures_back_off_then_die_with_an_alert_and_can_be_retried(db, world, monkeypatch):
    job = ghl_sync.enqueue(db, "client", world["client"].id, "add_note", {"note": f"{TAG} failing note"}, event="test")
    db.commit()

    def boom(*a, **k):
        raise ghl_sync.GhlError("HTTP 500: upstream down")
    monkeypatch.setattr(ghl_sync, "_request", boom)
    now = datetime.utcnow()
    ghl_sync.process(db, now=now)
    db.commit()
    db.refresh(job)
    assert job.status == "failed" and job.attempts == 1 and job.next_attempt_at >= now + timedelta(minutes=2)
    for i in range(ghl_sync.MAX_ATTEMPTS):
        now += timedelta(days=1)
        ghl_sync.process(db, now=now)
        db.commit()
    db.refresh(job)
    assert job.status == "dead" and job.attempts == ghl_sync.MAX_ATTEMPTS
    assert db.query(RiskAlert).filter(RiskAlert.entity_type == "GhlSyncJob", RiskAlert.entity_id == job.id).count() == 1
    monkeypatch.undo()
    ghl_sync.retry(db, job)
    ghl_sync.process(db, limit=10000)
    db.commit()
    db.refresh(job)
    assert job.status == "sent"


# ------------------------------------------------------------------------------------------------ inbound
def test_ghl_updates_a_lead_but_not_a_family_the_erp_owns(db, world):
    c, lead = world["client"], world["lead"]
    db.refresh(lead)
    j, l, created = ghl_sync.handle_contact(db, {"id": lead.ghl_contact_id, "firstName": f"{TAG}", "lastName": "Lead Renamed",
                                                 "email": f"{TAG}.lead@example.com", "phone": "+447700900999"})
    db.commit()
    assert j.status == "received" and l.id == lead.id and not created and lead.full_name == f"{TAG} Lead Renamed"
    c.ghl_contact_id = f"{TAG}-family"
    db.commit()
    j, _l, _c = ghl_sync.handle_contact(db, {"id": f"{TAG}-family", "name": f"{TAG} Family", "phone": "+447700111222"})
    db.commit()
    db.refresh(c)
    assert j.status == "conflict" and j.payload["diff"]["phone"] == {"erp": "+447700900888", "ghl": "+447700111222"}
    assert c.whatsapp == "+447700900888", "the family record is not overwritten"
    ghl_sync.resolve_conflict(db, j, db.query(Client).first() and _admin_user(db), "accept_ghl")
    db.commit()
    db.refresh(c)
    assert c.whatsapp == "+447700111222" and j.status == "resolved" and j.resolution == "accept_ghl"


def _admin_user(db):
    from app.models.core import User
    return db.query(User).filter(User.email == "admin@oqc.local").one()


def test_a_ghl_stage_change_moves_the_lead_without_echoing_back(db, world):
    lead = world["lead"]
    db.refresh(lead)
    before = len(_jobs(db, "lead", lead.id, operation="opportunity"))
    j = ghl_sync.handle_opportunity(db, {"contact_id": lead.ghl_contact_id, "pipeline_stage": "Demo Booked"})
    db.commit()
    db.refresh(lead)
    assert j.status == "received" and lead.stage == "trial_scheduled"
    assert len(_jobs(db, "lead", lead.id, operation="opportunity")) == before, "not echoed back to GHL"
    unknown = ghl_sync.handle_opportunity(db, {"contact_id": lead.ghl_contact_id, "pipeline_stage": "Something Else"})
    db.commit()
    assert unknown.status == "skipped"


def test_webhooks_require_the_secret(db, world):
    lead = world["lead"]
    db.refresh(lead)
    c = TestClient(app)
    body = {"contact_id": lead.ghl_contact_id, "pipeline_stage_id": "stage-won", "status": "won"}
    assert c.post("/api/v1/webhooks/ghl/opportunity", json=body).status_code == 401
    r = c.post("/api/v1/webhooks/ghl/opportunity", json={"contact_id": lead.ghl_contact_id, "pipeline_stage": "Contacted"},
               headers={"X-Webhook-Token": SECRET})
    assert r.status_code == 200 and r.json()["detail"]["erp_stage"] == "contacted"


# ------------------------------------------------------------------------------------------------ platform events
def test_complaints_tag_the_family_in_ghl(db, world):
    c = world["client"]
    case = crm_svc.open_case(db, "complaint", f"{TAG} complaint", "Teacher late", client=c, source="phone")
    db.commit()
    ops = [(j.event, j.payload.get("tags")) for j in _jobs(db, "client", c.id, operation="add_tags")]
    assert ("complaint.opened", ["erp:complaint-open"]) in ops
    assert case.case_number


# ------------------------------------------------------------------------------------------------ console
def test_console_and_controls(db, world):
    admin = _admin()
    page = admin.get("/admin/integrations/ghl/sync")
    assert page.status_code == 200 and "What the ERP sends to GHL" in page.text and "/api/v1/webhooks/ghl/opportunity" in page.text
    assert admin.post("/admin/integrations/ghl/sync/toggle", data={"rationale": ""}).status_code == 200
    assert ghl_sync.enabled(SessionLocal()), "a rationale is required to turn it off"
    r = admin.post(f"/admin/integrations/ghl/sync/push/client/{world['client'].id}", follow_redirects=False)
    assert r.status_code == 303
    billing = TestClient(app)
    assert billing.post("/login", data={"username": "billing@oqc.local", "password": "Billing@123"}, follow_redirects=False).status_code == 303
    assert billing.get("/admin/integrations/ghl/sync", follow_redirects=False).status_code in (302, 303, 403)
