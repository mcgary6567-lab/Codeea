"""Regression tests for the platform gaps closed from guidebuild/audit_missing.md (findings 1, 6, 23, 26, 28, 30, 31, 36).

Covered, each through the real route or service:
* inbound lead webhooks (ghl / meta-lead / n8n) verify an HMAC-SHA256 signature or the shared token from
  integrations.inbound_secret, accept with a warning when nothing is configured outside production and refuse
  with 503 in production; the Meta GET handshake still answers hub.challenge;
* the public payment webhook (X-Signature) records and confirms a gateway payment once per reference through the
  same helper the signed-in finance route uses;
* the Integration Hub inbound URL table only names routes that exist, and the Zoom route turns a
  recording.completed event into the CallRecord the QA pipeline reviews;
* the simulated call sync is refused in production without live Zoom credentials;
* configurable lookups feed the leave / complaint / request / referral / payment-mode selects and the POST
  handlers accept any configured value;
* /admin/settings can create a key; review_link reaches the automation placeholders; the SMS provider is a setting;
* a student or family rates an attended class, the teacher sees it and the QA dashboard flags 2 stars or less.

Re-runnable against the same database: every row the module creates is removed again (payments with their ledger,
journal, receipt, notifications and automation traces; call records; leaves; lookup values; settings; webhook
deliveries), and every value it changes (integration configs, ratings, invoice totals) is restored.

Run twice:
    .venv/Scripts/python.exe -m pytest tests/test_gaps_platform.py -q -p no:warnings
"""
from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from datetime import date, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR, settings
from app.database import SessionLocal
from app.main import app
from app.models.automation import AutomationEvent, ContactTag, Workflow, WorkflowRun
from app.models.config_erp import Lookup, LookupValue
from app.models.core import AuditEvent, Notification, Setting, WebhookDelivery
from app.models.crm import Conversation, Message
from app.models.erp import CallRecord
from app.models.finance import Invoice, JournalEntry, JournalLine, LedgerEntry, Payment, Receipt
from app.models.ops import Task
from app.models.people import Client, Leave, Student, Teacher
from app.models.scheduling import ClassSession
from app.seed import automation as seed_automation
from app.seed import config_erp as seed_config
from app.services import automation, billing, calls as calls_svc, integrations, lookups, system as sys_svc

TAG = "GAPTEST"
API = "/api/v1/webhooks"


# --------------------------------------------------------------------------- helpers
def _sig(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _body(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _post(c: TestClient, path: str, payload: dict, headers: dict | None = None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    return c.post(path, content=_body(payload), headers=h)


def _max_id(session, model) -> int:
    from sqlalchemy import func
    return session.query(func.coalesce(func.max(model.id), 0)).scalar() or 0


def _all_paths(root, prefix: str = "") -> set[str]:
    """Every route path. FastAPI >= 0.141 keeps included routers as lazy ``_IncludedRouter`` entries whose routes
    live on ``original_router`` under ``include_context.prefix`` (the API routers under /api/v1)."""
    out: set[str] = set()
    for r in getattr(root, "routes", []):
        orig = getattr(r, "original_router", None)
        if orig is not None:
            out |= _all_paths(orig, prefix + (getattr(getattr(r, "include_context", None), "prefix", "") or ""))
            continue
        path = getattr(r, "path", None)
        if path is not None:
            out.add(prefix + path)
    return out


@contextmanager
def _as(email: str, password: str):
    """A TestClient of its own signed in as ``email`` (the conftest fixtures share one client, so a test that
    needs a student, a parent, a teacher and an admin at once must open separate sessions)."""
    with TestClient(app) as c:
        r = c.post("/login", data={"username": email, "password": password}, follow_redirects=False)
        assert r.status_code == 303, f"login failed for {email}: {r.status_code}"
        yield c


class _Secret:
    """Set (or clear) a provider's inbound webhook secret on its Integration row for the duration of a test."""

    def __init__(self, provider: str, secret: str | None):
        self.provider, self.secret, self.before = provider, secret, None

    def __enter__(self):
        s = SessionLocal()
        try:
            integ = integrations.get_integration(s, self.provider)
            self.before = dict(integ.config or {})
            self.before_status = integ.status
            cfg = {k: v for k, v in self.before.items() if k not in ("webhook_secret", "inbound_secret", "app_secret")}
            if self.secret:
                cfg["webhook_secret"] = self.secret
            integ.config = cfg
            s.commit()
        finally:
            s.close()
        return self

    def __exit__(self, *exc):
        s = SessionLocal()
        try:
            integ = integrations.get_integration(s, self.provider)
            integ.config = self.before
            integ.status = self.before_status
            s.commit()
        finally:
            s.close()


@pytest.fixture(autouse=True)
def _no_env_secrets(monkeypatch):
    """The tests control the secret through the Integration row; a developer's .env must not interfere."""
    for name in ("GHL_WEBHOOK_SECRET", "N8N_WEBHOOK_SECRET", "META_APP_SECRET", "WHATSAPP_APP_SECRET",
                 "PAYMENT_WEBHOOK_SECRET", "ZOOM_WEBHOOK_SECRET", "ZOOM_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
        if hasattr(settings, name):
            monkeypatch.setattr(settings, name, "")


@pytest.fixture(scope="module", autouse=True)
def _seed_and_purge():
    """Bring the dev database up to the seed this build added (idempotent), then remove what the module created."""
    s = SessionLocal()
    try:
        seed_config._seed_lookups(s)
        seed_config._seed_branch_properties(s)
        seed_automation.seed_workflows(s)
        s.commit()
        delivery_mark = _max_id(s, WebhookDelivery)
    finally:
        s.close()
    lookups.invalidate()
    yield
    s = SessionLocal()
    try:
        s.query(WebhookDelivery).filter(WebhookDelivery.id > delivery_mark).delete(synchronize_session=False)
        s.query(CallRecord).filter(CallRecord.external_id.like(f"zoom-{TAG.lower()}-%")).delete(synchronize_session=False)
        s.query(Setting).filter(Setting.key.like("gap_test_%")).delete(synchronize_session=False)
        s.query(Setting).filter(Setting.key == "sms_provider", Setting.description == TAG).delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()
    lookups.invalidate()


# =========================================================================== finding 28: inbound URL table
def test_inbound_webhook_table_only_names_real_routes():
    paths = _all_paths(app)
    for provider, path in sys_svc.INBOUND_WEBHOOKS.items():
        assert path in paths, f"{provider} advertises {path}, which is not a route"
    assert sys_svc.INBOUND_WEBHOOKS["meta_ads"] == f"{API}/meta-lead"
    assert sys_svc.INBOUND_WEBHOOKS["payment"] == f"{API}/payment"
    assert sys_svc.INBOUND_WEBHOOKS["zoom"] == f"{API}/zoom"


# =========================================================================== finding 30: lead webhook verification
def test_signature_forms_meta_prefix_and_zoom_v0():
    from app.api.webhooks import _signature_matches
    secret, body = "s3cret", b'{"a":1}'

    class R:
        headers = {"x-zm-request-timestamp": "1700000000"}

    hexsig = _sig(secret, body)
    assert _signature_matches(secret, body, hexsig, R())
    assert _signature_matches(secret, body, "sha256=" + hexsig.upper(), R())
    v0 = _sig(secret, b"v0:1700000000:" + body)
    assert _signature_matches(secret, body, "v0=" + v0, R())
    assert not _signature_matches(secret, body, "sha256=" + "0" * 64, R())
    assert not _signature_matches(secret, body, "", R())


def test_webhook_without_secret_accepted_outside_production(client, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    with _Secret("n8n", None):
        r = _post(client, f"{API}/n8n", {"event": f"{TAG}.ping"})
    assert r.status_code == 200 and r.json()["event"] == f"{TAG}.ping"


def test_webhook_without_secret_refused_in_production(client, monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    with _Secret("n8n", None):
        r = _post(client, f"{API}/n8n", {"event": f"{TAG}.ping"})
    assert r.status_code == 503 and r.json()["detail"] == "webhook secret not configured"


def test_webhook_signature_or_token_required_when_secret_configured(client):
    secret = f"{TAG}-{uuid4().hex}"
    payload = {"event": f"{TAG}.ping"}
    with _Secret("n8n", secret), _Secret("ghl", secret), _Secret("meta_ads", secret):
        assert _post(client, f"{API}/n8n", payload).status_code == 401
        assert _post(client, f"{API}/n8n", payload, {"X-Webhook-Signature": "0" * 64}).status_code == 401
        assert _post(client, f"{API}/n8n", payload, {"X-Webhook-Token": "wrong"}).status_code == 401
        ok = _post(client, f"{API}/n8n", payload, {"X-Webhook-Signature": _sig(secret, _body(payload))})
        assert ok.status_code == 200 and ok.json()["ok"]
        ok = _post(client, f"{API}/n8n", payload, {"X-Webhook-Token": secret})
        assert ok.status_code == 200
        # the lead-creating routes refuse before touching the pipeline
        ghl = {"contact": {"id": f"{TAG}-ghl", "firstName": TAG, "email": f"{TAG.lower()}@example.com"}}
        assert _post(client, f"{API}/ghl", ghl, {"X-Webhook-Signature": "deadbeef"}).status_code == 401
        meta = {"field_data": [{"name": "full_name", "values": [TAG]}]}
        assert _post(client, f"{API}/meta-lead", meta, {"X-Hub-Signature-256": "sha256=" + "f" * 64}).status_code == 401
    s = SessionLocal()
    try:
        from app.models.crm import Lead
        assert s.query(Lead).filter(Lead.full_name == TAG).count() == 0
        rejected = s.query(WebhookDelivery).filter(WebhookDelivery.event.in_(["n8n.rejected", "ghl.rejected", "meta_ads.rejected"]),
                                                   WebhookDelivery.status == "failed").count()
        assert rejected >= 5
    finally:
        s.close()


def test_meta_get_handshake_still_echoes_challenge(client):
    s = SessionLocal()
    try:
        token = sys_svc.get_setting_value(s, "whatsapp_verify_token", "oqc-verify") or "oqc-verify"
    finally:
        s.close()
    r = client.get(f"{API}/whatsapp?hub.mode=subscribe&hub.verify_token={token}&hub.challenge={TAG}-777")
    assert r.status_code == 200 and r.text == f"{TAG}-777"
    assert client.get(f"{API}/whatsapp?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=1").status_code == 403


# =========================================================================== finding 6: public payment webhook
def _open_invoice(s):
    for inv in s.query(Invoice).filter(Invoice.status.in_(billing.INVOICE_OPEN_SET)).order_by(Invoice.id).all():
        if float(inv.total) - float(inv.paid_amount) >= 1.0 and inv.client and inv.client.user_id:
            return inv
    pytest.skip("no open invoice with a balance in the seeded database")


def _purge_payment(s, payment_id: int, client_id: int, user_id: int | None, marks: dict) -> None:
    for rec in s.query(Receipt).filter(Receipt.payment_id == payment_id).all():
        for base in (Path(rec.pdf_path or ""), BASE_DIR / (rec.pdf_path or "")):
            try:
                if rec.pdf_path and base.is_file():
                    base.unlink()
            except OSError:
                pass
        s.delete(rec)
    je_ids = [i for (i,) in s.query(JournalEntry.id).filter(JournalEntry.reference_type == "payment", JournalEntry.reference_id == payment_id)]
    if je_ids:
        s.query(JournalLine).filter(JournalLine.entry_id.in_(je_ids)).delete(synchronize_session=False)
        s.query(JournalEntry).filter(JournalEntry.id.in_(je_ids)).delete(synchronize_session=False)
    s.query(LedgerEntry).filter(LedgerEntry.reference_type == "payment", LedgerEntry.reference_id == payment_id).delete(synchronize_session=False)
    s.query(Payment).filter(Payment.id == payment_id).delete(synchronize_session=False)
    for model in (WorkflowRun, AutomationEvent, ContactTag):
        s.query(model).filter(model.contact_type == "client", model.contact_id == client_id, model.id > marks[model.__name__]).delete(synchronize_session=False)
    s.query(Task).filter(Task.entity_type == "client", Task.entity_id == client_id, Task.id > marks["Task"]).delete(synchronize_session=False)
    conv_ids = [i for (i,) in s.query(Conversation.id).filter(Conversation.client_id == client_id)]
    if conv_ids:
        s.query(Message).filter(Message.conversation_id.in_(conv_ids), Message.id > marks["Message"]).delete(synchronize_session=False)
        s.query(Conversation).filter(Conversation.id.in_(conv_ids), Conversation.id > marks["Conversation"]).delete(synchronize_session=False)
    if user_id:
        s.query(Notification).filter(Notification.user_id == user_id, Notification.id > marks["Notification"]).delete(synchronize_session=False)


def test_public_payment_webhook_verifies_signature_and_is_idempotent(client):
    secret = f"{TAG}-pay-{uuid4().hex}"
    s = SessionLocal()
    try:
        inv = _open_invoice(s)
        inv_id, inv_no, client_id, user_id = inv.id, inv.invoice_number, inv.client_id, inv.client.user_id
        snapshot = {"paid_amount": float(inv.paid_amount), "status": inv.status, "paid_at": inv.paid_at,
                    "confirmed_at": inv.confirmed_at, "confirmed_by_id": inv.confirmed_by_id}
        marks = {m.__name__: _max_id(s, m) for m in (WorkflowRun, AutomationEvent, ContactTag, Task, Message, Conversation, Notification)}
    finally:
        s.close()
    reference = f"{TAG}-ref-{uuid4().hex[:12]}"
    payload = {"invoice_number": inv_no, "amount": 1.0, "reference": reference, "gateway": "stripe", "method": "card"}
    payment_id = None
    try:
        with _Secret("payment", secret):
            assert _post(client, f"{API}/payment", payload).status_code == 401
            assert _post(client, f"{API}/payment", payload, {"X-Signature": "0" * 64}).status_code == 401
            good = {"X-Signature": _sig(secret, _body(payload))}
            r1 = _post(client, f"{API}/payment", payload, good)
            assert r1.status_code == 201, r1.text
            d1 = r1.json()
            assert d1["created"] is True and d1["status"] in billing.PAYMENT_CONFIRMED_SET and d1["reference"] == reference
            r2 = _post(client, f"{API}/payment", payload, good)
            assert r2.status_code == 200 and r2.json()["created"] is False
            assert r2.json()["payment_number"] == d1["payment_number"]
            bad = dict(payload, invoice_number="INV-DOES-NOT-EXIST", reference=reference + "x")
            assert _post(client, f"{API}/payment", bad, {"X-Signature": _sig(secret, _body(bad))}).status_code == 404
            assert _post(client, f"{API}/payment", {"x": 1}, {"X-Signature": _sig(secret, _body({"x": 1}))}).status_code == 422
        s = SessionLocal()
        try:
            pays = s.query(Payment).filter(Payment.reference == reference).all()
            assert len(pays) == 1
            payment_id = pays[0].id
            assert pays[0].invoice_id == inv_id and billing.is_confirmed_payment(pays[0].status)
            assert s.query(Receipt).filter(Receipt.payment_id == payment_id).count() == 1
            assert s.query(LedgerEntry).filter(LedgerEntry.reference_type == "payment", LedgerEntry.reference_id == payment_id).count() == 1
            inv = s.get(Invoice, inv_id)
            assert round(float(inv.paid_amount), 2) == round(snapshot["paid_amount"] + 1.0, 2)
            assert s.query(WebhookDelivery).filter(WebhookDelivery.event == "payment.webhook", WebhookDelivery.response_code == 201).count() >= 1
        finally:
            s.close()
    finally:
        s = SessionLocal()
        try:
            if payment_id is None:
                payment_id = next((p.id for p in s.query(Payment).filter(Payment.reference == reference)), None)
            if payment_id is not None:
                _purge_payment(s, payment_id, client_id, user_id, marks)
            inv = s.get(Invoice, inv_id)
            for k, v in snapshot.items():
                setattr(inv, k, v)
            s.commit()
        finally:
            s.close()


def test_signed_in_finance_webhook_still_uses_shared_helper():
    from app.api import finance as finance_api
    assert callable(finance_api.apply_gateway_payment)
    assert "/api/v1/finance/payment-webhook" in _all_paths(app)


# =========================================================================== finding 28/36: Zoom route and sync gate
def _teacher_session_without_call(s, teacher_id: int):
    taken = s.query(CallRecord.session_id).filter(CallRecord.session_id.isnot(None))
    return (s.query(ClassSession).filter(ClassSession.teacher_id == teacher_id, ClassSession.status == "done",
                                         ClassSession.id.notin_(taken), ClassSession.scheduled_start.isnot(None))
            .order_by(ClassSession.date.desc()).first())


def test_zoom_webhook_handshake_and_recording_completed(client):
    secret = f"{TAG}-zoom-{uuid4().hex}"
    s = SessionLocal()
    try:
        teacher = s.query(Teacher).join(Teacher.user).filter(Teacher.user.has(email="teacher1@oqc.local")).first()
        assert teacher is not None
        cs = _teacher_session_without_call(s, teacher.id)
        assert cs is not None, "teacher1 needs a done class without a call record"
        start_utc = cs.scheduled_start.replace(tzinfo=ZoneInfo("Asia/Karachi")).astimezone(timezone.utc)
        teacher_id, host_email = teacher.id, "teacher1@oqc.local"
    finally:
        s.close()
    uid = f"{TAG.lower()}-{uuid4().hex[:10]}"
    event = {"event": "recording.completed", "payload": {"account_id": "acc", "object": {
        "uuid": uid, "id": 987654321, "topic": f"{TAG} Quran class", "host_email": host_email,
        "start_time": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "duration": 30, "share_url": "https://zoom.us/rec/share/x",
        "recording_files": [{"recording_type": "shared_screen_with_speaker_view", "file_type": "MP4",
                             "play_url": "https://zoom.us/rec/play/abc", "download_url": "https://zoom.us/rec/download/abc"}]}}}
    with _Secret("zoom", secret):
        r = _post(client, f"{API}/zoom", {"event": "endpoint.url_validation", "payload": {"plainToken": "abc123"}},
                  {"X-Webhook-Token": secret})
        assert r.status_code == 200 and r.json() == {"plainToken": "abc123", "encryptedToken": _sig(secret, b"abc123")}
        assert _post(client, f"{API}/zoom", event).status_code == 401
        r1 = _post(client, f"{API}/zoom", event, {"X-Webhook-Token": secret})
        assert r1.status_code == 200, r1.text
        d1 = r1.json()["detail"]
        assert d1["created"] is True and d1["teacher_id"] == teacher_id and d1["session_id"] and d1["review_state"] == "mapped"
        ts = "1700000000"
        zoom_sig = "v0=" + _sig(secret, f"v0:{ts}:".encode() + _body(event))
        r2 = _post(client, f"{API}/zoom", event, {"x-zm-signature": zoom_sig, "x-zm-request-timestamp": ts})
        assert r2.status_code == 200 and r2.json()["detail"]["created"] is False and r2.json()["detail"]["call_id"] == d1["call_id"]
    s = SessionLocal()
    try:
        call = s.get(CallRecord, d1["call_id"])
        assert call.source == "ZOOM" and call.external_id == f"zoom-{uid}" and call.recording_url == "https://zoom.us/rec/play/abc"
        assert call.duration_minutes == 30 and call.session_id == d1["session_id"]
        mapped = s.get(ClassSession, call.session_id)
        assert abs((mapped.scheduled_start - call.start_time).total_seconds()) <= 45 * 60
        assert s.query(CallRecord).filter(CallRecord.external_id == f"zoom-{uid}").count() == 1
        s.query(CallRecord).filter(CallRecord.external_id == f"zoom-{uid}").delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()


def test_simulated_call_sync_gated_in_production(admin, monkeypatch):
    s = SessionLocal()
    try:
        before = s.query(CallRecord).count()
        assert calls_svc.simulated_sync_allowed(s) is True   # development: the demo pull works
    finally:
        s.close()
    monkeypatch.setattr(settings, "APP_ENV", "production")
    with _Secret("zoom", None):
        s = SessionLocal()
        try:
            integ = integrations.get_integration(s, "zoom")
            integ.status = "not_configured"
            s.commit()
            assert integrations.has_live_credentials(s, "zoom") is False
            assert calls_svc.simulated_sync_allowed(s) is False
            with pytest.raises(ValueError):
                calls_svc.sync_calls(s, None)
            s.rollback()
        finally:
            s.close()
        r = admin.post("/qa/calls/sync", data={"days": "7"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/qa/calls")
        page = admin.get("/qa/calls")
        assert "simulated pull is disabled in production" in page.text
        s = SessionLocal()
        try:
            assert s.query(CallRecord).count() == before
            # live credentials re-enable the pull even in production
            integ = integrations.get_integration(s, "zoom")
            integ.config = {**(integ.config or {}), "webhook_secret": "live"}
            s.commit()
            assert calls_svc.simulated_sync_allowed(s) is True
        finally:
            s.close()


# =========================================================================== finding 23: lookups feed the selects
def test_lookup_lists_render_in_the_forms(admin, client):
    assert '<option value="casual"' in admin.get("/requests/leaves").text
    assert ">Casual<" in admin.get("/leaves/students").text
    assert "Designation Review" in admin.get("/hr/requests").text and "Payroll" in admin.get("/hr/complaints").text
    assert "Community / Masjid" in admin.get("/requests/references").text
    assert "Teaching Quality" in admin.get("/requests/complaints").text
    assert "Online Payment Gateway" in admin.get("/finance/receipts/new").text
    assert "My local masjid" in client.get("/register").text


def test_configured_lookup_value_is_accepted_and_unknown_falls_back(admin):
    s = SessionLocal()
    try:
        lk = s.query(Lookup).filter(Lookup.code == "leave_type").first()
        assert lk is not None
        s.add(LookupValue(lookup_id=lk.id, value="gap_test_type", label="Gap Test Leave", sort_no=99, status="active"))
        student = s.query(Student).join(Student.user).filter(Student.user.has(email="student1@oqc.local")).first()
        sid = student.id
        leave_mark = _max_id(s, Leave)
        s.commit()
    finally:
        s.close()
    lookups.invalidate()
    try:
        assert "Gap Test Leave" in admin.get("/leaves/students").text
        s = SessionLocal()
        try:
            assert lookups.is_valid(s, "leave_type", "gap_test_type")
        finally:
            s.close()
        day = (date.today() + timedelta(days=200)).isoformat()
        r = admin.post("/leaves/students/new", data={"student_id": sid, "start_date": day, "end_date": day,
                                                     "leave_type": "gap_test_type", "reason": TAG}, follow_redirects=False)
        assert r.status_code == 303
        r = admin.post("/leaves/students/new", data={"student_id": sid, "start_date": day, "end_date": day,
                                                     "leave_type": "not-configured", "reason": TAG}, follow_redirects=False)
        assert r.status_code == 303
        s = SessionLocal()
        try:
            rows = s.query(Leave).filter(Leave.id > leave_mark, Leave.reason == TAG).order_by(Leave.id).all()
            assert [lv.leave_type for lv in rows] == ["gap_test_type", "casual"]
        finally:
            s.close()
    finally:
        s = SessionLocal()
        try:
            s.query(Leave).filter(Leave.id > leave_mark, Leave.reason == TAG).delete(synchronize_session=False)
            s.query(LookupValue).filter(LookupValue.value == "gap_test_type").delete(synchronize_session=False)
            s.commit()
        finally:
            s.close()
        lookups.invalidate()


def test_staff_request_type_uses_configured_lookup():
    from app.web.staff_requests import _configured, request_type_options
    from app.models.hr_erp import EMPLOYEE_REQUEST_TYPES
    s = SessionLocal()
    try:
        values = [v for v, _ in request_type_options(s)]
        assert "Designation Review" in values and "Shift Change" in values
        assert _configured(s, "hr_employee_request_type", EMPLOYEE_REQUEST_TYPES, "Shift Change", "Other") == "Shift Change"
        assert _configured(s, "hr_employee_request_type", EMPLOYEE_REQUEST_TYPES, "Nonsense", "Other") == "Other"
    finally:
        s.close()


# =========================================================================== finding 26: add a setting
def test_admin_can_add_a_setting_and_it_is_audited(admin):
    key = f"gap_test_{uuid4().hex[:8]}"
    s = SessionLocal()
    try:
        audit_mark = _max_id(s, AuditEvent)   # the audit trail is immutable; SQLite reuses a deleted setting's id
    finally:
        s.close()
    r = admin.post("/admin/settings/new", data={"key": key, "value": "30", "group": "testing", "description": TAG,
                                                "rationale": "regression test"}, follow_redirects=False)
    assert r.status_code == 303 and "group=testing" in r.headers["location"]
    s = SessionLocal()
    try:
        row = s.query(Setting).filter(Setting.key == key).first()
        assert row is not None and row.value == {"value": 30} and row.group == "testing" and row.value_type == "number"
        assert sys_svc.get_setting_value(s, key) == 30
        assert s.query(AuditEvent).filter(AuditEvent.id > audit_mark, AuditEvent.entity_type == "Setting",
                                          AuditEvent.entity_id == row.id, AuditEvent.action == "create").count() == 1
    finally:
        s.close()
    # duplicates and malformed keys are refused without creating anything
    assert admin.post("/admin/settings/new", data={"key": key, "value": "1"}, follow_redirects=False).status_code == 303
    assert admin.post("/admin/settings/new", data={"key": "Bad Key!", "value": "1"}, follow_redirects=False).status_code == 303
    s = SessionLocal()
    try:
        assert s.query(Setting).filter(Setting.key == key).count() == 1
        assert s.query(Setting).filter(Setting.key.in_(["bad key!", "Bad Key!"])).count() == 0
        assert key in admin.get("/admin/settings?tab=settings").text
        s.query(Setting).filter(Setting.key == key).delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()


def test_settings_seed_covers_keys_the_code_reads():
    from app.seed.core import SETTINGS
    assert any(k == "whatsapp_verify_token" for k, *_ in SETTINGS)
    retention = {k: default for k, _l, _g, default, _n in sys_svc.RETENTION_KEYS}
    assert retention["retention_sessions_days"] == 30 and retention["retention_notifications_days"] == 180
    s = SessionLocal()
    try:
        for key in ("whatsapp_verify_token", "retention_sessions_days", "retention_notifications_days", "review_link"):
            assert s.query(Setting).filter(Setting.key == key).count() == 1, key
    finally:
        s.close()


# =========================================================================== finding 31: review link + SMS setting
def test_review_link_setting_reaches_the_automation_placeholders():
    s = SessionLocal()
    try:
        row = s.query(Setting).filter(Setting.key == "review_link").first()
        assert row is not None and row.group == "general"
        before = row.value
        client = s.query(Client).filter(Client.user_id.isnot(None)).first()
        row.value = {"value": ""}
        s.flush()
        ctx = automation.render_context(s, "client", client)
        assert ctx["review_link"] == automation.REVIEW_LINK_FALLBACK and "link" in ctx
        row.value = {"value": "https://g.page/r/oqc-review"}
        s.flush()
        assert automation.render_context(s, "client", client)["review_link"] == "https://g.page/r/oqc-review"
        wf = s.query(Workflow).filter(Workflow.code == "AUTO-018").first()
        assert wf is not None
        bodies = " ".join(st.get("body", "") for st in wf.steps if isinstance(st, dict))
        assert "{{review_link}}" in bodies and "{{link}}" not in bodies
        spec = next(w for w in seed_automation.WORKFLOWS if w["code"] == "AUTO-018")
        assert "{{review_link}}" in spec["steps"][-1]["body"]
        assert seed_automation.seed_workflows(s) == 0   # idempotent
        row.value = before
        s.commit()
    finally:
        s.close()


def test_sms_step_reads_the_sms_provider_setting(monkeypatch):
    monkeypatch.setattr(automation, "has_tag", lambda *a, **k: True)
    s = SessionLocal()
    try:
        client = s.query(Client).filter(Client.user_id.isnot(None)).first()
        s.query(Setting).filter(Setting.key == "sms_provider").delete(synchronize_session=False)
        s.flush()
        assert automation._send_sms(s, "client", client, {}, {}).startswith("skipped: no SMS provider")
        s.add(Setting(key="sms_provider", value={"value": "twilio"}, group="integrations", description=TAG))
        s.flush()
        assert automation._send_sms(s, "client", client, {}, {}) == "sms queued"
        s.query(Setting).filter(Setting.key == "sms_provider", Setting.description == TAG).delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()


# =========================================================================== finding 1: class ratings
def _student_sessions(s, email: str):
    student = s.query(Student).join(Student.user).filter(Student.user.has(email=email)).first()
    done = (s.query(ClassSession).filter(ClassSession.student_id == student.id, ClassSession.status == "done")
            .order_by(ClassSession.student_rating.isnot(None), ClassSession.date.desc()).first())
    pending = s.query(ClassSession).filter(ClassSession.student_id == student.id, ClassSession.status == "pending").first()
    other = s.query(ClassSession).filter(ClassSession.student_id != student.id, ClassSession.status == "done").first()
    return student, done, pending, other


def test_student_and_family_rate_a_class_and_qa_sees_it():
    with _as("student1@oqc.local", "Student@123") as student, _as("parent1@oqc.local", "Parent@123") as parent, \
            _as("teacher1@oqc.local", "Teacher@123") as teacher, _as("admin@oqc.local", "Admin@12345") as admin:
        _rate_and_check(student, parent, teacher, admin)


def _rate_and_check(student, parent, teacher, admin):
    s = SessionLocal()
    try:
        st, done, pending, other = _student_sessions(s, "student1@oqc.local")
        assert done is not None and other is not None
        snap = {"id": done.id, "rating": done.student_rating, "feedback": done.student_feedback, "date": done.date,
                "teacher_id": done.teacher_id}
        pending_id = pending.id if pending else None
        other_id = other.id
        notif_mark = _max_id(s, Notification)
    finally:
        s.close()
    try:
        r = student.post(f"/portal/classes/{snap['id']}/rate", data={"rating": "4", "comment": f"{TAG} clear tajweed", "next": "/student/classes"},
                         follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/student/classes"
        s = SessionLocal()
        try:
            cs = s.get(ClassSession, snap["id"])
            assert cs.student_rating == 4 and cs.student_feedback == f"{TAG} clear tajweed"
        finally:
            s.close()
        page = student.get("/student/classes")
        assert f'data-session="{snap["id"]}"' in page.text and "rating-shown" in page.text
        # another student's class is invisible, a bad rating is refused, a class not yet held cannot be rated
        assert student.post(f"/portal/classes/{other_id}/rate", data={"rating": "5"}, follow_redirects=False).status_code == 404
        assert student.post(f"/portal/classes/{snap['id']}/rate", data={"rating": "9"}, follow_redirects=False).status_code == 303
        if pending_id:
            student.post(f"/portal/classes/{pending_id}/rate", data={"rating": "5"}, follow_redirects=False)
        s = SessionLocal()
        try:
            assert s.get(ClassSession, snap["id"]).student_rating == 4
            if pending_id:
                assert s.get(ClassSession, pending_id).student_rating is None
        finally:
            s.close()
        # the family re-rates the same class low; teacher and QA see it
        r = parent.post(f"/portal/classes/{snap['id']}/rate", data={"rating": "2", "comment": f"{TAG} too short",
                                                                   "next": "/portal/schedule"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/portal/schedule"
        s = SessionLocal()
        try:
            cs = s.get(ClassSession, snap["id"])
            assert cs.student_rating == 2 and cs.student_feedback == f"{TAG} too short"
            audits = s.query(AuditEvent).filter(AuditEvent.entity_type == "ClassSession", AuditEvent.entity_id == cs.id,
                                                AuditEvent.description.like("%rated%")).count()
            assert audits >= 2
        finally:
            s.close()
        tp = teacher.get("/teacher/qa")
        assert tp.status_code == 200 and f'data-rated-session="{snap["id"]}"' in tp.text and f"{TAG} too short" in tp.text
        qs = f"date_from={(snap['date'] - timedelta(days=1)).isoformat()}&date_to={(snap['date'] + timedelta(days=1)).isoformat()}"
        dash = admin.get(f"/qa/dashboard?{qs}")
        assert dash.status_code == 200 and f'data-low-rating="{snap["id"]}"' in dash.text
    finally:
        s = SessionLocal()
        try:
            cs = s.get(ClassSession, snap["id"])
            cs.student_rating, cs.student_feedback = snap["rating"], snap["feedback"]
            s.query(Notification).filter(Notification.id > notif_mark, Notification.title == "Low class rating").delete(synchronize_session=False)
            s.commit()
        finally:
            s.close()


def test_rating_route_requires_a_portal_login(admin):
    with TestClient(app) as anon_client:
        anon = anon_client.post("/portal/classes/1/rate", data={"rating": "5"}, follow_redirects=False)
    assert anon.status_code in (303, 307, 401, 403), anon.text[:300]
    # staff without a student or family profile cannot rate on anyone's behalf
    s = SessionLocal()
    try:
        cs = s.query(ClassSession).filter(ClassSession.status == "done").first()
        cs_id, before = cs.id, cs.student_rating
    finally:
        s.close()
    r = admin.post(f"/portal/classes/{cs_id}/rate", data={"rating": "5"}, follow_redirects=False)
    assert r.status_code in (403, 404)
    s = SessionLocal()
    try:
        assert s.get(ClassSession, cs_id).student_rating == before
    finally:
        s.close()
