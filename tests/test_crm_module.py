"""End-to-end smoke test for the CRM & Growth module (Modules 3, 7, 32, 33, 34, 42, 43 + Section 7).

Run:  DATABASE_URL=sqlite:///./data/oqc_crm.db .venv/Scripts/python.exe tests/test_crm_module.py
"""
from __future__ import annotations

import sys
import time

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.crm import (Lead, Case, Feedback, Referral, Conversation, Message, Sequence, SequenceEnrollment,
                            Survey, Campaign, RetentionAction, MessageTemplate)
from app.models.core import WebhookDelivery
from app.models.finance import LedgerEntry
from app.models.people import Client, Student
from app.models.scheduling import Trial

FAILURES: list[str] = []
CHECKS = {"ok": 0}


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        CHECKS["ok"] += 1
    else:
        FAILURES.append(f"{label} :: {detail}")
        print(f"  FAIL {label} {detail}")


def login(client: TestClient, email: str, password: str) -> bool:
    r = client.post("/login", data={"username": email, "password": password}, follow_redirects=False)
    return r.status_code in (302, 303) or r.status_code == 200


def get(client: TestClient, url: str, expect: int = 200) -> object:
    r = client.get(url, follow_redirects=False)
    check(f"GET {url}", r.status_code == expect, f"got {r.status_code}")
    if r.status_code >= 500:
        print(r.text[:1500])
    return r


def post(client: TestClient, url: str, data: dict, expect=(303,)) -> object:
    r = client.post(url, data=data, follow_redirects=False)
    check(f"POST {url}", r.status_code in expect, f"got {r.status_code}")
    if r.status_code >= 500:
        print(r.text[:1500])
    return r


def main() -> int:
    db = SessionLocal()
    lead = db.query(Lead).filter(Lead.stage.notin_(["won", "lost"])).order_by(Lead.id).first()
    won_lead = db.query(Lead).filter(Lead.stage == "won", Lead.converted_client_id.is_(None)).order_by(Lead.id).first()
    if won_lead is None:
        won_lead = db.query(Lead).filter(Lead.converted_client_id.is_(None)).order_by(Lead.id.desc()).first()
    case = db.query(Case).order_by(Case.id).first()
    conv = db.query(Conversation).order_by(Conversation.id).first()
    seq = db.query(Sequence).order_by(Sequence.id).first()
    campaign = db.query(Campaign).order_by(Campaign.id).first()
    survey = db.query(Survey).filter(Survey.audience == "client").order_by(Survey.id).first()
    client_row = db.query(Client).filter(Client.status == "active").order_by(Client.id).first()
    non_amb = db.query(Client).filter(Client.is_ambassador.is_(False), Client.status == "active").order_by(Client.id).first()
    ref = db.query(Referral).filter(Referral.status.in_(["signed_up", "qualified", "lead"])).order_by(Referral.id).first()
    student = db.query(Student).filter(Student.status.in_(["active", "trial"])).order_by(Student.id).first()
    trial = db.query(Trial).order_by(Trial.id).first()
    action = db.query(RetentionAction).order_by(RetentionAction.id).first()
    tpl = db.query(MessageTemplate).order_by(MessageTemplate.id).first()
    dup_phone = db.query(Lead).filter(Lead.phone.isnot(None)).order_by(Lead.id).first().phone
    enrollment = db.query(SequenceEnrollment).filter(SequenceEnrollment.status == "active").order_by(SequenceEnrollment.id).first()
    failed_msg = db.query(Message).filter(Message.status == "failed").order_by(Message.id).first()
    pending_fb = db.query(Feedback).filter(Feedback.status == "pending", Feedback.is_confidential.is_(False)).order_by(Feedback.id).first()
    db.close()

    uniq = str(int(time.time()))[-6:]   # keep webhook contacts unique so the suite is re-runnable
    wa_phone = "44770" + uniq + "1"
    with TestClient(app) as client:
        print("== admin session ==")
        check("login admin", login(client, "admin@oqc.local", "Admin@12345"))

        print("-- GET pages")
        for url in ["/crm/leads", "/crm/leads?view=list", "/crm/leads?view=list&stage=won&source=&country=United+Kingdom",
                    "/crm/leads/kpis", "/crm/leads/kpis?days=30", "/crm/leads/new",
                    "/crm/campaigns", "/crm/campaigns/new", "/crm/marketing", "/crm/marketing?start=2026-06-01&end=2026-09-09",
                    "/crm/inbox", "/crm/inbox/failed", "/crm/inbox/templates", "/crm/sequences",
                    "/crm/referrals", "/crm/referrals/ambassadors",
                    "/trials", "/trials/analytics", "/trials/follow-ups", "/trials/new",
                    "/cases", "/cases?status=open_all", "/cases/trends", "/cases/new",
                    "/feedback", "/feedback/surveys", "/feedback/enps",
                    "/retention", "/retention/actions", "/retention/freezes", "/retention/analytics"]:
            get(client, url)
        get(client, "/crm/leads/export")
        get(client, "/crm/marketing/export?dim=campaign")
        if lead:
            get(client, f"/crm/leads/{lead.id}")
            get(client, f"/crm/leads/{lead.id}/edit")
        if campaign:
            get(client, f"/crm/campaigns/{campaign.id}")
        if seq:
            get(client, f"/crm/sequences/{seq.id}")
        if conv:
            get(client, f"/crm/inbox?c={conv.id}")
        if trial:
            get(client, f"/trials/{trial.id}")
        if case:
            get(client, f"/cases/{case.id}")
        if pending_fb:
            get(client, f"/feedback/{pending_fb.id}")

        print("-- POST forms")
        db = SessionLocal()
        before_leads = db.query(Lead).count()
        db.close()
        post(client, "/crm/leads/new", {"full_name": "Test Duplicate Family", "phone": dup_phone, "whatsapp": dup_phone,
                                        "country": "United Kingdom", "student_name": "Test Child", "student_age": "9",
                                        "students_count": "1", "stage": "new", "whatsapp_opt_in": "1",
                                        "notes": "Created by the CRM smoke test"})
        db = SessionLocal()
        new_lead = db.query(Lead).order_by(Lead.id.desc()).first()
        check("lead created", db.query(Lead).count() == before_leads + 1, f"{db.query(Lead).count()} vs {before_leads}")
        check("duplicate detected", new_lead.is_duplicate_of_id is not None, "is_duplicate_of_id is null")
        check("lead scored", (new_lead.score or 0) > 0, f"score={new_lead.score}")
        check("lead auto-assigned", new_lead.assigned_to_id is not None, "no assignee")
        new_lead_id = new_lead.id
        db.close()

        post(client, f"/crm/leads/{new_lead_id}/activity", {"activity_type": "call", "note": "Called the family, booked a trial."})
        post(client, f"/crm/leads/{new_lead_id}/rescore", {})
        post(client, f"/crm/leads/{new_lead_id}/sync-ghl", {})
        post(client, f"/crm/leads/{new_lead_id}/assign", {"mode": "round_robin"})
        post(client, f"/crm/leads/{new_lead_id}/follow-up", {"next_follow_up": "2026-09-20T10:00"})
        post(client, f"/crm/leads/{new_lead_id}/enroll", {"sequence_type": "lead_follow_up"})
        post(client, f"/crm/leads/{new_lead_id}/stage", {"stage": "contacted", "reason": "Spoke to the father"})
        post(client, f"/crm/leads/{new_lead_id}/schedule-trial", {"scheduled_at": "2026-09-15T18:30"})
        db = SessionLocal()
        check("trial created from lead", db.query(Trial).filter(Trial.lead_id == new_lead_id).count() == 1)
        db.close()
        post(client, f"/crm/leads/{new_lead_id}/edit", {"full_name": "Test Duplicate Family", "phone": dup_phone,
                                                        "country": "United Kingdom", "students_count": "1"})
        # lost reason enforcement
        r = client.post(f"/crm/leads/{new_lead_id}/stage", data={"stage": "lost", "reason": ""}, follow_redirects=False)
        check("lost without reason rejected", r.status_code == 303)
        db = SessionLocal()
        check("lead not marked lost", db.get(Lead, new_lead_id).stage != "lost")
        db.close()

        # convert a lead -> client + portal user + students
        db = SessionLocal()
        conv_lead = db.query(Lead).filter(Lead.converted_client_id.is_(None), Lead.id != new_lead_id).order_by(Lead.id.desc()).first()
        conv_lead_id = conv_lead.id
        clients_before = db.query(Client).count()
        students_before = db.query(Student).count()
        db.close()
        post(client, f"/crm/leads/{conv_lead_id}/convert", {"relationship": "father"})
        db = SessionLocal()
        cl = db.get(Lead, conv_lead_id)
        check("lead converted", cl.converted_client_id is not None and cl.stage == "won", f"stage={cl.stage}")
        check("client created", db.query(Client).count() == clients_before + 1)
        check("students created", db.query(Student).count() > students_before)
        new_client = db.get(Client, cl.converted_client_id)
        check("portal user created", new_client.user_id is not None)
        db.close()

        # campaigns
        post(client, "/crm/campaigns/new", {"name": "Smoke test campaign", "platform": "meta", "budget": "1000",
                                            "currency": "GBP", "status": "active", "country": "United Kingdom",
                                            "offer": "Free week", "start_date": "2026-08-01"})
        db = SessionLocal()
        camp = db.query(Campaign).order_by(Campaign.id.desc()).first()
        camp_id = camp.id
        db.close()
        post(client, f"/crm/campaigns/{camp_id}/metrics", {"date": "2026-09-01", "impressions": "5000", "clicks": "180",
                                                           "leads": "20", "spend": "150.50", "conversions": "5", "revenue": "400"})
        post(client, f"/crm/campaigns/{camp_id}/edit", {"name": "Smoke test campaign", "platform": "meta", "status": "paused",
                                                        "budget": "1200", "currency": "GBP"})

        # inbox
        if conv:
            post(client, f"/crm/inbox/{conv.id}/send", {"body": "Assalamu Alaikum, following up from the smoke test."})
            post(client, f"/crm/inbox/{conv.id}/send", {"template_name": tpl.name if tpl else "", "body": ""})
            post(client, f"/crm/inbox/{conv.id}/simulate", {"body": "Wa Alaikum Assalam, that works for us."})
            post(client, f"/crm/inbox/{conv.id}/note", {"text": "Internal: prefers Urdu."})
            post(client, f"/crm/inbox/{conv.id}/tags", {"tags": "hot, evening"})
            post(client, f"/crm/inbox/{conv.id}/status", {"status": "pending"})
            post(client, f"/crm/inbox/{conv.id}/assign", {"assigned_to_id": ""})
            post(client, f"/crm/inbox/{conv.id}/opt-in", {"opted_in": "0"})
            post(client, f"/crm/inbox/{conv.id}/opt-in", {"opted_in": "1"})
        if failed_msg:
            post(client, f"/crm/inbox/failed/{failed_msg.id}/retry", {})
        post(client, "/crm/inbox/templates/new", {"name": "smoke_test_tpl", "category": "follow_up", "language": "en",
                                                  "body": "Assalamu Alaikum {{name}}, smoke test template.", "is_approved": "1"})
        db = SessionLocal()
        st_tpl = db.query(MessageTemplate).filter(MessageTemplate.name == "smoke_test_tpl").first()
        st_tpl_id = st_tpl.id if st_tpl else None
        db.close()
        if st_tpl_id:
            post(client, f"/crm/inbox/templates/{st_tpl_id}/edit", {"body": "Updated smoke test template.", "category": "reminder", "language": "en"})
            post(client, f"/crm/inbox/templates/{st_tpl_id}/delete", {})

        # sequences
        post(client, "/crm/sequences/new", {"name": "Smoke test sequence", "sequence_type": "lead_follow_up",
                                            "steps": '[{"day": 0, "template": "lead_first_touch"}]', "is_active": "1"})
        db = SessionLocal()
        sq = db.query(Sequence).order_by(Sequence.id.desc()).first()
        sq_id = sq.id
        db.close()
        post(client, f"/crm/sequences/{sq_id}/edit", {"name": "Smoke test sequence", "sequence_type": "lead_follow_up",
                                                      "steps": '[{"day": 0, "template": "lead_first_touch"}, {"day": 3, "body": "Follow up"}]',
                                                      "is_active": "1"})
        post(client, f"/crm/sequences/{sq_id}/enroll", {"contact_type": "lead", "contact_id": str(new_lead_id)})
        post(client, "/crm/sequences/run", {})
        if enrollment:
            post(client, f"/crm/sequences/enrollments/{enrollment.id}/stop", {"reason": "Smoke test"})

        # trials
        post(client, "/trials/new", {"student_name": "Smoke Trial Child", "scheduled_at": "2026-09-18T17:00"})
        db = SessionLocal()
        tr = db.query(Trial).order_by(Trial.id.desc()).first()
        tr_id = tr.id
        db.close()
        post(client, f"/trials/{tr_id}/schedule", {"scheduled_at": "2026-09-19T17:30"})
        post(client, f"/trials/{tr_id}/outcome", {"status": "attended", "outcome": "Parent happy",
                                                  "teacher_feedback": "Bright student", "follow_up_date": "2026-09-20"})
        post(client, f"/trials/{tr_id}/follow-up", {"follow_up_date": "2026-09-22", "note": "Called, awaiting decision"})
        post(client, f"/trials/{tr_id}/convert", {})

        # cases
        db = SessionLocal()
        cases_before = db.query(Case).count()
        cid = client_row.id if client_row else None
        db.close()
        post(client, "/cases/new", {"case_type": "complaint", "title": "Smoke test: teacher was late twice",
                                    "description": "The teacher joined ten minutes late on Monday and Wednesday, class time was lost.",
                                    "client_id": str(cid or ""), "source": "portal"})
        db = SessionLocal()
        new_case = db.query(Case).order_by(Case.id.desc()).first()
        check("case opened", db.query(Case).count() == cases_before + 1)
        check("case numbered", (new_case.case_number or "").startswith("CS-"), new_case.case_number or "")
        check("case AI classified", bool(new_case.category), "no category")
        check("case SLA set", new_case.sla_due_at is not None and new_case.sla_hours > 0)
        check("case auto-assigned", new_case.assigned_to_id is not None)
        new_case_id = new_case.id
        db.close()
        post(client, f"/cases/{new_case_id}/comment", {"text": "Internal note from the smoke test.", "is_internal": "1"})
        post(client, f"/cases/{new_case_id}/comment", {"text": "We are reviewing the recording, jazakAllah khair."})
        post(client, f"/cases/{new_case_id}/task", {"title": "Call the family back", "due_date": "2026-09-12"})
        post(client, f"/cases/{new_case_id}/assign", {"assigned_to_id": "", "priority": "high"})
        post(client, f"/cases/{new_case_id}/escalate", {"rationale": "Repeat punctuality complaint for this teacher."})
        r = client.post(f"/cases/{new_case_id}/status", data={"status": "resolved"}, follow_redirects=False)
        check("resolve without resolution rejected", r.status_code == 303)
        db = SessionLocal()
        check("case not resolved yet", db.get(Case, new_case_id).status != "resolved")
        db.close()
        post(client, f"/cases/{new_case_id}/status", {"status": "resolved", "resolution": "Teacher counselled and time made up.",
                                                      "root_cause": "Teacher schedule overlap"})
        db = SessionLocal()
        check("case resolved", db.get(Case, new_case_id).status == "resolved")
        db.close()

        # feedback + public survey
        post(client, "/feedback/surveys/new", {"name": "Smoke test survey", "trigger": "manual", "audience": "client",
                                               "questions": '[{"key":"nps","type":"nps","text":"Recommend us?"},{"key":"comment","type":"text","text":"Why?"}]',
                                               "is_active": "1"})
        db = SessionLocal()
        sv = db.query(Survey).order_by(Survey.id.desc()).first()
        sv_id = sv.id
        db.close()
        post(client, f"/feedback/surveys/{sv_id}/edit", {"name": "Smoke test survey", "trigger": "manual", "audience": "client",
                                                         "questions": '[{"key":"nps","type":"nps","text":"Recommend us?"}]', "is_active": "1"})
        fb_before = None
        if survey and client_row:
            db = SessionLocal()
            fb_before = db.query(Feedback).count()
            db.close()
            post(client, "/feedback/send", {"survey_id": str(survey.id), "client_id": str(client_row.id)})
            db = SessionLocal()
            sent_fb = db.query(Feedback).order_by(Feedback.id.desc()).first()
            check("survey sent", db.query(Feedback).count() == fb_before + 1)
            check("survey token signed", bool(sent_fb.token) and "." in sent_fb.token)
            token = sent_fb.token
            db.close()

            print("-- public survey (no auth)")
            with TestClient(app) as anon:
                get(anon, f"/survey/{token}")
                r = anon.post(f"/survey/{token}", data={"nps": "3", "rating": "1", "comment": "The teacher was late and nobody called us back."},
                              follow_redirects=False)
                check(f"POST /survey/{{token}}", r.status_code == 303, f"got {r.status_code}")
                get(anon, "/survey/not-a-real-token.deadbeef", 404)
            db = SessionLocal()
            fb2 = db.query(Feedback).filter(Feedback.token == token).first()
            check("feedback submitted", fb2.status in ("submitted", "routed"), fb2.status)
            check("negative routed to a case", fb2.case_id is not None, "no case_id")
            if fb2.case_id:
                routed = db.get(Case, fb2.case_id)
                check("routed case is a QA feedback case", routed.source == "feedback" and routed.case_type == "feedback")
            db.close()
        if pending_fb:
            post(client, f"/feedback/{pending_fb.id}/resolve", {"rationale": "Called the family."})

        # referrals
        if non_amb:
            post(client, f"/crm/referrals/invite/{non_amb.id}", {"force": "1", "rationale": "CEO approved early ambassador invite."})
            db = SessionLocal()
            c2 = db.get(Client, non_amb.id)
            check("ambassador invited", c2.is_ambassador is True)
            check("referral code issued", bool(c2.referral_code))
            db.close()
        post(client, "/crm/referrals/new", {"ambassador_client_id": str(non_amb.id) if non_amb else "", "referred_name": "Smoke Referral"})
        if ref:
            db = SessionLocal()
            r0 = db.get(Referral, ref.id)
            amb_id, referred_id = r0.ambassador_client_id, r0.referred_client_id
            ledger_before_amb = db.query(LedgerEntry).filter(LedgerEntry.client_id == amb_id).count()
            ledger_before_ref = db.query(LedgerEntry).filter(LedgerEntry.client_id == referred_id).count() if referred_id else 0
            db.close()
            post(client, f"/crm/referrals/{ref.id}/qualify", {"rationale": "Referred family completed their first paid month."})
            db = SessionLocal()
            r1 = db.get(Referral, ref.id)
            check("referral credited", r1.status == "credited", r1.status)
            check("ambassador ledger credit", db.query(LedgerEntry).filter(LedgerEntry.client_id == amb_id).count() == ledger_before_amb + 1)
            if referred_id:
                check("referred ledger credit", db.query(LedgerEntry).filter(LedgerEntry.client_id == referred_id).count() == ledger_before_ref + 1)
                check("both ledger ids stored", r1.ambassador_credit_ledger_id and r1.referred_credit_ledger_id)
            check("credit amount positive", float(r1.credit_amount or 0) > 0)
            db.close()
        db = SessionLocal()
        other_ref = db.query(Referral).filter(Referral.status == "ask").order_by(Referral.id).first()
        other_ref_id = other_ref.id if other_ref else None
        db.close()
        if other_ref_id:
            post(client, f"/crm/referrals/{other_ref_id}/status", {"status": "lead"})

        # retention
        if student:
            post(client, f"/retention/students/{student.id}/recompute", {})
            db = SessionLocal()
            s2 = db.get(Student, student.id)
            check("risk computed", s2.risk_computed_at is not None and s2.risk_level in ("low", "medium", "high"))
            check("risk factors stored", isinstance(s2.risk_factors, dict) and "_signals" in (s2.risk_factors or {}))
            db.close()
            post(client, "/retention/actions/new", {"student_id": str(student.id), "action_type": "pre_leave_offer",
                                                    "scheduled_at": "2026-09-15T10:00", "notes": "Smoke test offer"})
        if action:
            post(client, f"/retention/actions/{action.id}/status", {"status": "succeeded", "outcome": "Family retained after the call."})
        post(client, "/retention/cohort-calls", {"level": "medium"})
        post(client, "/retention/freezes/schedule", {})
        post(client, "/retention/recompute", {})

        print("-- REST API")
        for url in ["/api/v1/crm/leads", "/api/v1/crm/leads/stats", "/api/v1/crm/cases", "/api/v1/crm/cases/trends",
                    "/api/v1/crm/feedback/summary", "/api/v1/crm/referrals", "/api/v1/crm/referrals/dashboard",
                    "/api/v1/crm/trials", "/api/v1/crm/trials/stats", "/api/v1/crm/marketing", "/api/v1/crm/campaigns",
                    "/api/v1/crm/retention/risk", "/api/v1/crm/retention/dashboard", "/api/v1/crm/conversations",
                    "/api/v1/crm/closers/kpis"]:
            get(client, url)
        if conv:
            get(client, f"/api/v1/crm/conversations/{conv.id}/messages")
        r = client.post("/api/v1/crm/leads", json={"full_name": "API Lead Test", "phone": "+44770" + uniq + "5",
                                                   "country": "United Kingdom", "source": "Website"})
        check("POST /api/v1/crm/leads", r.status_code == 201, f"got {r.status_code} {r.text[:200]}")

        print("-- inbound webhooks (public)")
        db = SessionLocal()
        wh_before = db.query(WebhookDelivery).filter(WebhookDelivery.direction == "in").count()
        msg_before = db.query(Message).count()
        lead_before = db.query(Lead).count()
        db.close()
        with TestClient(app) as anon:
            r = anon.get("/api/v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=oqc-verify&hub.challenge=12345")
            check("GET whatsapp verify", r.status_code == 200 and r.text == "12345", f"{r.status_code} {r.text[:80]}")
            r = anon.get("/api/v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=12345")
            check("GET whatsapp verify rejects bad token", r.status_code == 403)
            payload = {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "contacts": [{"wa_id": wa_phone, "profile": {"name": "Webhook Family"}}],
                "messages": [{"from": wa_phone, "id": "wamid.test." + uniq, "timestamp": "1789000000", "type": "text",
                              "text": {"body": "Assalamu Alaikum, I want Quran classes for my son."}}]}}]}]}
            r = anon.post("/api/v1/webhooks/whatsapp", json=payload)
            check("POST webhooks/whatsapp", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
            if r.status_code == 200:
                check("whatsapp created a lead", r.json()["detail"]["leads_created"] == 1, str(r.json()))
            r = anon.post("/api/v1/webhooks/ghl", json={"contact": {"id": "ghl_test_" + uniq, "firstName": "Ghl", "lastName": "Contact",
                                                                    "email": f"ghl.contact{uniq}@example.com", "phone": "+44770" + uniq + "2",
                                                                    "country": "United Kingdom"}})
            check("POST webhooks/ghl", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
            r = anon.post("/api/v1/webhooks/meta-lead", json={"campaign_id": "cmp_meta_uk_01", "field_data": [
                {"name": "full_name", "values": ["Meta Lead Family"]}, {"name": "email", "values": [f"meta.lead{uniq}@example.com"]},
                {"name": "phone_number", "values": ["+44770" + uniq + "3"]}, {"name": "country", "values": ["United Kingdom"]}]})
            check("POST webhooks/meta-lead", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
            if r.status_code == 200:
                check("meta lead attributed to campaign", r.json()["detail"]["campaign"] is not None, str(r.json()))
            r = anon.post("/api/v1/webhooks/n8n", json={"event": "lead.created", "name": "N8N Family", "phone": "+44770" + uniq + "4"})
            check("POST webhooks/n8n", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        db = SessionLocal()
        check("webhook deliveries logged", db.query(WebhookDelivery).filter(WebhookDelivery.direction == "in").count() >= wh_before + 6)
        check("inbound message stored", db.query(Message).count() > msg_before)
        check("webhook leads created", db.query(Lead).count() >= lead_before + 3)
        db.close()

    print("== closer scoping ==")
    with TestClient(app) as closer:
        check("login closer", login(closer, "closer@oqc.local", "Closer@123"))
        get(closer, "/crm/leads")
        get(closer, "/crm/leads?view=list")
        get(closer, "/crm/inbox")
        get(closer, "/trials")
        r = closer.get("/retention", follow_redirects=False)
        check("closer denied retention", r.status_code == 403, f"got {r.status_code}")
        db = SessionLocal()
        from app.models.core import User
        u = db.query(User).filter(User.email == "closer@oqc.local").first()
        foreign = db.query(Lead).filter(Lead.assigned_to_id.isnot(None), Lead.assigned_to_id != u.id).order_by(Lead.id).first()
        db.close()
        if foreign:
            r = closer.get(f"/crm/leads/{foreign.id}", follow_redirects=False)
            check("closer cannot open another closer's lead", r.status_code == 404, f"got {r.status_code}")

    print("== jobs ==")
    db = SessionLocal()
    from app.services import jobs_crm
    for jid, fn, _mins in jobs_crm.JOBS:
        try:
            res = fn(db)
            db.commit()
            print(f"  job {jid}: {res}")
            CHECKS["ok"] += 1
        except Exception as exc:
            db.rollback()
            FAILURES.append(f"job {jid} :: {exc}")
            print(f"  FAIL job {jid}: {exc}")
    db.close()

    print(f"\n{CHECKS['ok']} checks passed, {len(FAILURES)} failures")
    for f in FAILURES:
        print("  - " + f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
