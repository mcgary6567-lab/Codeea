"""CRM & Growth services.

Lead scoring / duplicates / assignment / conversion, WhatsApp inbox messaging, automation sequences,
cases (complaints) with SLA, feedback & surveys, ambassador referrals, marketing analytics.
Everything here is reusable by web routers, REST API, background jobs and the seed.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, date, timedelta
from typing import Optional

from sqlalchemy import func, or_, and_
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import log_action
from app.core.notify import notify
from app.core.security import hash_password, sign_value
from app.core.utils import next_code
from app.models.academic import Course
from app.models.core import User, Role, Setting, Department, RiskAlert, CommunicationPreference, NotificationTemplate
from app.models.crm import (LEAD_STAGES, Lead, LeadActivity, LeadSource, Campaign, CampaignMetric, Conversation, Message,
                            InternalNote, MessageTemplate, Sequence, SequenceEnrollment, Referral, Survey, Feedback, Case,
                            CaseComment, RetentionAction)
from app.models.finance import Subscription, LedgerEntry, Invoice
from app.models.people import Client, Student, Teacher, Grievance, Employee
from app.models.scheduling import Trial
from app.services.ai_gateway import ai
from app.services.integrations import send_whatsapp, ghl_upsert_contact, emit_event

OPEN_CASE_STATUSES = ("open", "in_progress", "waiting", "escalated")
CASE_TYPES = ["complaint", "request", "feedback", "technical", "billing", "schedule_change", "teacher_change"]
CASE_STATUSES = ["open", "in_progress", "waiting", "resolved", "closed", "escalated"]
PRIORITIES = ["low", "medium", "high", "urgent"]
SEQUENCE_TYPES = ["lead_follow_up", "trial_follow_up", "win_back", "pre_leave_offer", "freeze_reactivation", "payment_reminder"]
SURVEY_TRIGGERS = ["post_ptm", "post_result_card", "tenure_30", "tenure_90", "tenure_180", "staff_enps", "manual"]
REFERRAL_STATUSES = ["invited", "ask", "lead", "signed_up", "qualified", "credited", "expired"]
CONTACT_ACTIVITY_TYPES = ("call", "whatsapp", "email")
COUNTRY_CURRENCY = {"United Kingdom": ("GBP", "Europe/London"), "United States": ("USD", "America/New_York"),
                    "Canada": ("CAD", "America/Toronto"), "Australia": ("AUD", "Australia/Sydney"),
                    "Pakistan": ("PKR", "Asia/Karachi"), "Ireland": ("EUR", "Europe/Dublin"), "Germany": ("EUR", "Europe/Berlin")}


# ----------------------------------------------------------------------------- helpers
def setting(db: Session, key: str, default=None):
    s = db.query(Setting).filter(Setting.key == key).first()
    if not s or s.value is None:
        return default
    v = s.value
    if isinstance(v, dict) and set(v.keys()) == {"value"}:
        return v["value"]
    return v


def digits(value: Optional[str]) -> str:
    return re.sub(r"\D", "", value or "")


def user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()


def users_with_role(db: Session, slug: str) -> list[User]:
    return db.query(User).join(Role, Role.id == User.role_id).filter(Role.slug == slug, User.is_active.is_(True)).order_by(User.id).all()


def department_by_code(db: Session, code: str) -> Optional[Department]:
    return db.query(Department).filter(Department.code == code).first()


def hod_for(db: Session, code: str) -> Optional[User]:
    d = department_by_code(db, code)
    return d.hod if d and d.hod else None


def render_template_body(body: str, ctx: dict) -> str:
    out = body or ""
    for k, v in ctx.items():
        out = out.replace("{{" + k + "}}", str(v if v is not None else ""))
        out = out.replace("{{ " + k + " }}", str(v if v is not None else ""))
    return out


def template_body(db: Session, name: str, fallback: str = "") -> str:
    t = db.query(MessageTemplate).filter(MessageTemplate.name == name).first()
    if t:
        return t.body
    nt = db.query(NotificationTemplate).filter(NotificationTemplate.event_type == name, NotificationTemplate.channel == "whatsapp").first()
    return nt.body if nt else fallback


# ----------------------------------------------------------------------------- leads
def find_duplicates(db: Session, phone: Optional[str] = None, email: Optional[str] = None, whatsapp: Optional[str] = None,
                    exclude_id: Optional[int] = None) -> list[Lead]:
    conds = []
    for raw in (phone, whatsapp):
        d = digits(raw)
        if len(d) >= 7:
            tail = d[-8:]
            conds.append(Lead.phone.like(f"%{tail}"))
            conds.append(Lead.whatsapp.like(f"%{tail}"))
    if email and email.strip():
        conds.append(func.lower(Lead.email) == email.strip().lower())
    if not conds:
        return []
    q = db.query(Lead).filter(or_(*conds))
    if exclude_id:
        q = q.filter(Lead.id != exclude_id)
    found = []
    for l in q.order_by(Lead.created_at.asc()).limit(10):
        # verify phone tails against digit-only forms (spaces inside numbers)
        ok = False
        if email and l.email and l.email.lower() == email.strip().lower():
            ok = True
        for raw in (phone, whatsapp):
            d = digits(raw)
            if len(d) >= 7 and (digits(l.phone).endswith(d[-8:]) or digits(l.whatsapp).endswith(d[-8:])):
                ok = True
        if ok:
            found.append(l)
    return found


def score_lead(db: Session, lead: Lead) -> dict:
    payload = {"country": lead.country, "source": lead.source.name if lead.source else None,
               "has_whatsapp": bool(lead.whatsapp or lead.phone), "students_count": lead.students_count or 1,
               "preferred_time": lead.preferred_time, "trial_attended": lead.stage in ("trial_done", "negotiation", "won"),
               "lead_id": lead.id, "course": lead.course_interest.code if lead.course_interest else None}
    result, run = ai(db, "lead_scoring", "score_lead", payload, entity=lead)
    lead.score = int(result.get("score", 0))
    factors = dict(result.get("factors") or {})
    factors["_recommendation"] = result.get("recommendation")
    factors["_confidence"] = result.get("confidence")
    factors["_scored_at"] = datetime.utcnow().isoformat(timespec="minutes")
    lead.score_factors = factors
    return result


def add_activity(db: Session, lead: Lead, activity_type: str, note: Optional[str], user: Optional[User] = None,
                 when: Optional[datetime] = None) -> LeadActivity:
    a = LeadActivity(lead_id=lead.id, activity_type=activity_type, note=note, user_id=user.id if user else None,
                     created_at=when or datetime.utcnow())
    db.add(a)
    if activity_type in CONTACT_ACTIVITY_TYPES:
        lead.last_contacted_at = a.created_at
        lead.follow_up_count = (lead.follow_up_count or 0) + 1
        if lead.stage == "new":
            lead.stage = "contacted"
    db.flush()
    return a


def closers(db: Session) -> list[User]:
    return users_with_role(db, "lead_closer")


def assign_lead(db: Session, lead: Lead, target: Optional[User], actor: Optional[User] = None, note: Optional[str] = None) -> None:
    before = lead.assigned_to_id
    lead.assigned_to_id = target.id if target else None
    add_activity(db, lead, "assignment", note or (f"Assigned to {target.full_name}" if target else "Unassigned"), actor)
    if target and target.id != before:
        notify(db, target, "New lead assigned", f"{lead.full_name} ({lead.lead_code}) from {lead.country or 'unknown'} - score {lead.score}.",
               event_type="lead_assigned", link=f"/crm/leads/{lead.id}")
    log_action(db, actor, "assign", "leads", entity=lead, description=f"Lead assigned to {target.full_name if target else 'nobody'}",
               before={"assigned_to_id": before}, after={"assigned_to_id": lead.assigned_to_id})


def round_robin_assign(db: Session, lead: Lead, actor: Optional[User] = None) -> Optional[User]:
    pool = closers(db)
    if not pool:
        return None
    loads = {u.id: 0 for u in pool}
    for uid, n in db.query(Lead.assigned_to_id, func.count(Lead.id)).filter(Lead.assigned_to_id.in_(list(loads)),
                                                                            Lead.stage.notin_(["won", "lost"])).group_by(Lead.assigned_to_id):
        loads[uid] = n
    target = sorted(pool, key=lambda u: (loads[u.id], u.id))[0]
    assign_lead(db, lead, target, actor, note=f"Round-robin assigned to {target.full_name}")
    return target


def move_stage(db: Session, lead: Lead, stage: str, user: Optional[User], reason: Optional[str] = None, request=None) -> None:
    if stage not in LEAD_STAGES:
        raise ValueError("Invalid stage")
    if stage == "lost" and not (reason or "").strip():
        raise ValueError("A lost reason is required")
    before = lead.stage
    lead.stage = stage
    if stage == "lost":
        lead.lost_reason = reason.strip()
    if stage == "contacted" and not lead.last_contacted_at:
        lead.last_contacted_at = datetime.utcnow()
    add_activity(db, lead, "stage_change", f"Stage {before} -> {stage}" + (f": {reason}" if reason else ""), user)
    log_action(db, user, "stage_change", "leads", entity=lead, description=f"{lead.lead_code} {before} -> {stage}", rationale=reason,
               before={"stage": before}, after={"stage": stage}, request=request, consequential=stage == "lost")
    emit_event(db, "lead.stage_changed", {"lead_id": lead.id, "lead_code": lead.lead_code, "from": before, "to": stage})


def create_lead(db: Session, data: dict, actor: Optional[User] = None, auto_assign: bool = True, request=None) -> tuple[Lead, list[Lead]]:
    """Create a lead with duplicate detection, referral linking, AI scoring and optional round-robin assignment."""
    dups = find_duplicates(db, data.get("phone"), data.get("email"), data.get("whatsapp"))
    lead = Lead(lead_code=next_code(db, Lead, "lead_code", "L-"), full_name=data["full_name"].strip(), email=(data.get("email") or None),
                phone=(data.get("phone") or None), whatsapp=(data.get("whatsapp") or data.get("phone") or None), country=data.get("country") or None,
                timezone=data.get("timezone") or COUNTRY_CURRENCY.get(data.get("country") or "", ("", None))[1],
                student_name=data.get("student_name") or None, student_age=data.get("student_age"), students_count=data.get("students_count") or 1,
                course_interest_id=data.get("course_interest_id"), preferred_time=data.get("preferred_time") or None,
                source_id=data.get("source_id"), campaign_id=data.get("campaign_id"), referral_code=(data.get("referral_code") or "").strip().upper() or None,
                stage=data.get("stage") or "new", generator_id=data.get("generator_id") if data.get("generator_id") is not None else (actor.id if actor else None),
                assigned_to_id=data.get("assigned_to_id"), notes=data.get("notes") or None, ghl_contact_id=data.get("ghl_contact_id"),
                whatsapp_opt_in=data.get("whatsapp_opt_in", True), next_follow_up=data.get("next_follow_up"),
                is_duplicate_of_id=dups[0].id if dups else None)
    if data.get("created_at"):
        lead.created_at = data["created_at"]
    db.add(lead)
    db.flush()
    score_lead(db, lead)
    add_activity(db, lead, "note", f"Lead created via {lead.source.name if lead.source else 'manual entry'}" + (" (possible duplicate)" if dups else ""), actor,
                 when=lead.created_at)
    if lead.referral_code:
        link_referral(db, lead)
    if auto_assign and not lead.assigned_to_id:
        round_robin_assign(db, lead, actor)
    log_action(db, actor, "create", "leads", entity=lead, description=f"Lead {lead.lead_code} created ({lead.full_name})", request=request,
               after={"stage": lead.stage, "score": lead.score, "duplicate_of": lead.is_duplicate_of_id})
    emit_event(db, "lead.created", {"lead_id": lead.id, "lead_code": lead.lead_code, "name": lead.full_name, "source": lead.source.name if lead.source else None})
    return lead, dups


def link_referral(db: Session, lead: Lead) -> Optional[Referral]:
    if not lead.referral_code:
        return None
    amb = db.query(Client).filter(func.upper(Client.referral_code) == lead.referral_code.upper()).first()
    if not amb:
        return None
    ref = db.query(Referral).filter(Referral.referred_lead_id == lead.id).first()
    if not ref:
        ref = Referral(ambassador_client_id=amb.id, referral_code=amb.referral_code, referred_name=lead.full_name, referred_phone=lead.phone,
                       referred_lead_id=lead.id, status="lead", invited_at=datetime.utcnow(), owner_id=(user_by_email(db, "supervisor@oqc.local") or User(id=None)).id,
                       ghl_source_tag=f"ref:{amb.referral_code}")
        db.add(ref)
        db.flush()
    if not lead.source_id:
        src = db.query(LeadSource).filter(LeadSource.name == "Referral").first()
        if src:
            lead.source_id = src.id
    return ref


def sync_ghl(db: Session, lead: Lead, actor: Optional[User] = None) -> dict:
    res = ghl_upsert_contact(db, {"firstName": lead.full_name.split()[0], "name": lead.full_name, "email": lead.email, "phone": lead.phone,
                                  "country": lead.country, "tags": [f"stage:{lead.stage}", f"source:{lead.source.name if lead.source else 'unknown'}"],
                                  "customFields": {"lead_code": lead.lead_code, "score": lead.score}})
    if res.get("id"):
        lead.ghl_contact_id = res["id"]
    add_activity(db, lead, "note", f"Synced to GoHighLevel (contact {lead.ghl_contact_id})", actor)
    log_action(db, actor, "sync", "leads", entity=lead, description=f"GHL sync for {lead.lead_code}")
    return res


def upsert_lead_from_ghl(db: Session, contact: dict) -> tuple[Lead, bool]:
    """Webhook upsert: match by ghl_contact_id, then phone/email; else create."""
    ghl_id = contact.get("id") or contact.get("contact_id")
    phone = contact.get("phone")
    email = contact.get("email")
    name = contact.get("name") or " ".join(x for x in (contact.get("firstName"), contact.get("lastName")) if x) or "GHL contact"
    lead = None
    if ghl_id:
        lead = db.query(Lead).filter(Lead.ghl_contact_id == str(ghl_id)).first()
    if not lead:
        dups = find_duplicates(db, phone, email)
        lead = dups[0] if dups else None
    created = False
    if lead:
        lead.ghl_contact_id = str(ghl_id) if ghl_id else lead.ghl_contact_id
        lead.full_name = name or lead.full_name
        lead.email = email or lead.email
        lead.phone = phone or lead.phone
        lead.country = contact.get("country") or lead.country
        add_activity(db, lead, "note", "Updated from GoHighLevel webhook", None)
    else:
        src = db.query(LeadSource).filter(LeadSource.name == "GHL").first()
        lead, _ = create_lead(db, {"full_name": name, "email": email, "phone": phone, "whatsapp": phone, "country": contact.get("country"),
                                   "source_id": src.id if src else None, "ghl_contact_id": str(ghl_id) if ghl_id else None,
                                   "notes": "Created from GoHighLevel webhook", "generator_id": None}, actor=None)
        created = True
    return lead, created


def convert_lead_to_client(db: Session, lead: Lead, user: Optional[User], overrides: Optional[dict] = None, request=None) -> tuple[Client, Optional[str]]:
    """Create Client + portal user + Student(s) from the lead (no re-entry), mark lead won, link conversations/trials/referrals."""
    overrides = overrides or {}
    if lead.converted_client_id:
        return db.get(Client, lead.converted_client_id), None
    role = db.query(Role).filter(Role.slug == "client").first()
    code = next_code(db, Client, "client_code", "C-")
    email = (overrides.get("email") or lead.email or f"{code.lower()}@clients.oqc.local").strip().lower()
    if db.query(User).filter(User.email == email).first():
        email = f"{code.lower()}.{email}"
    username = email.split("@")[0]
    if db.query(User).filter(User.username == username).first():
        username = f"{username}-{code.lower()}"
    temp_password = "Welcome@" + secrets.token_hex(3)
    portal_user = User(email=email, username=username, full_name=lead.full_name, hashed_password=hash_password(temp_password),
                       role_id=role.id if role else None, timezone=lead.timezone or "Europe/London", must_change_password=True, phone=lead.phone)
    db.add(portal_user)
    db.flush()
    currency, tz = COUNTRY_CURRENCY.get(lead.country or "", ("GBP", "Europe/London"))
    fam = (lead.full_name.split()[-1] if lead.full_name else "REF")[:3].upper()
    client = Client(client_code=code, user_id=portal_user.id, full_name=lead.full_name, email=email if "@clients.oqc.local" not in email else None,
                    phone=lead.phone, whatsapp=lead.whatsapp or lead.phone, country=lead.country or "United Kingdom", timezone=lead.timezone or tz,
                    currency=currency, relationship_to_student=overrides.get("relationship") or "father", status="trial", lead_id=lead.id,
                    source=lead.source.name if lead.source else "Manual", consent_given=True, consent_at=datetime.utcnow(),
                    whatsapp_opt_in=lead.whatsapp_opt_in, referral_code=f"REF{fam}{code[-3:]}", joined_at=date.today(), ghl_contact_id=lead.ghl_contact_id,
                    notes=f"Converted from lead {lead.lead_code}", preferences={"preferred_time": lead.preferred_time})
    db.add(client)
    db.flush()
    students = []
    n = max(1, int(lead.students_count or 1))
    for i in range(n):
        name = lead.student_name if (i == 0 and lead.student_name) else f"{lead.full_name.split()[0]} Student {i + 1}"
        age = lead.student_age if i == 0 else None
        st = Student(student_code=next_code(db, Student, "student_code", "S-"), client_id=client.id, full_name=name, age=age,
                     is_minor=(age or 10) < 18, course_id=lead.course_interest_id, status="trial", timezone=client.timezone,
                     join_date=date.today(), guardian_consent=True, risk_score=10, risk_level="low")
        db.add(st)
        db.flush()
        students.append(st)
    # lead bookkeeping
    lead.stage = "won"
    lead.converted_at = datetime.utcnow()
    lead.converted_client_id = client.id
    for conv in db.query(Conversation).filter(Conversation.lead_id == lead.id):
        conv.client_id = client.id
        conv.contact_type = "client"
        db.add(Message(conversation_id=conv.id, direction="out", body=f"[system] Lead converted to client {client.client_code}", message_type="system", status="delivered"))
    for tr in db.query(Trial).filter(Trial.lead_id == lead.id):
        tr.client_id = client.id
        tr.student_id = tr.student_id or students[0].id
        if tr.status == "attended":
            tr.status = "converted"
    if lead.referral_code:
        ref = link_referral(db, lead)
        if ref and ref.status in ("invited", "ask", "lead"):
            ref.status = "signed_up"
            ref.referred_client_id = client.id
            ref.referred_name = client.full_name
    add_activity(db, lead, "stage_change", f"Converted to client {client.client_code} with {n} student(s)", user)
    log_action(db, user, "convert", "leads", entity=lead, description=f"Lead {lead.lead_code} converted to client {client.client_code}",
               after={"client_id": client.id, "students": [s.student_code for s in students]}, request=request, consequential=True)
    log_action(db, user, "create", "clients", entity=client, description=f"Client created from lead {lead.lead_code}")
    if lead.assigned_to_id:
        notify(db, lead.assigned_to_id, "Lead converted", f"{lead.full_name} is now client {client.client_code}.", event_type="lead_converted", link=f"/clients/{client.id}")
    notify(db, portal_user, "Welcome to Online Quran College", f"Your family portal is ready. Sign in with {email} and your temporary password.",
           event_type="welcome", link="/portal", channels=("in_app", "whatsapp"), recipient_address=client.whatsapp)
    emit_event(db, "lead.converted", {"lead_id": lead.id, "client_id": client.id, "client_code": client.client_code})
    return client, temp_password


def lead_stats(db: Session, base_query) -> dict:
    week_ago = datetime.utcnow() - timedelta(days=7)
    rows = base_query.all()
    total = len(rows)
    won = sum(1 for l in rows if l.stage == "won")
    lost = sum(1 for l in rows if l.stage == "lost")
    new_week = sum(1 for l in rows if l.created_at and l.created_at >= week_ago)
    avg_score = round(sum(l.score or 0 for l in rows) / total, 1) if total else 0
    reasons: dict[str, int] = {}
    for l in rows:
        if l.stage == "lost" and l.lost_reason:
            reasons[l.lost_reason] = reasons.get(l.lost_reason, 0) + 1
    top = sorted(reasons.items(), key=lambda x: -x[1])[:3]
    overdue = sum(1 for l in rows if l.next_follow_up and l.next_follow_up < datetime.utcnow() and l.stage not in ("won", "lost"))
    return {"total": total, "won": won, "lost": lost, "new_week": new_week, "conversion": round(100 * won / (won + lost), 1) if (won + lost) else 0.0,
            "avg_score": avg_score, "top_lost": top, "overdue_follow_ups": overdue,
            "by_stage": {s: sum(1 for l in rows if l.stage == s) for s in LEAD_STAGES}}


def closer_kpis(db: Session, since: Optional[date] = None) -> list[dict]:
    since_dt = datetime.combine(since, datetime.min.time()) if since else datetime.utcnow() - timedelta(days=90)
    out = []
    for u in closers(db) + [x for x in users_with_role(db, "hod_marketing")]:
        leads = db.query(Lead).filter(Lead.assigned_to_id == u.id, Lead.created_at >= since_dt).all()
        n = len(leads)
        within = 0
        for l in leads:
            first = db.query(func.min(LeadActivity.created_at)).filter(LeadActivity.lead_id == l.id, LeadActivity.activity_type.in_(CONTACT_ACTIVITY_TYPES)).scalar()
            if first and l.created_at and (first - l.created_at) <= timedelta(hours=1):
                within += 1
        trials = db.query(Trial).filter(Trial.lead_id.in_([l.id for l in leads] or [-1])).count()
        won = [l for l in leads if l.stage == "won"]
        lost = sum(1 for l in leads if l.stage == "lost")
        days = [((l.converted_at or l.updated_at) - l.created_at).days for l in won if l.created_at]
        out.append({"user": u, "leads": n, "contacted_1h_pct": round(100 * within / n, 1) if n else 0.0, "trials": trials, "won": len(won), "lost": lost,
                    "conversion": round(100 * len(won) / (len(won) + lost), 1) if (len(won) + lost) else 0.0,
                    "avg_days_to_close": round(sum(days) / len(days), 1) if days else 0.0,
                    "open": sum(1 for l in leads if l.stage not in ("won", "lost"))})
    return out


# ----------------------------------------------------------------------------- conversations & messages
def contact_phone(contact) -> Optional[str]:
    return getattr(contact, "whatsapp", None) or getattr(contact, "phone", None)


def get_or_create_conversation(db: Session, contact_type: str, contact) -> Conversation:
    q = db.query(Conversation)
    conv = (q.filter(Conversation.lead_id == contact.id) if contact_type == "lead" else q.filter(Conversation.client_id == contact.id)).order_by(Conversation.id.desc()).first()
    if conv:
        return conv
    conv = Conversation(channel="whatsapp", contact_type=contact_type, lead_id=contact.id if contact_type == "lead" else getattr(contact, "lead_id", None),
                        client_id=contact.id if contact_type == "client" else None, contact_name=contact.full_name, contact_phone=contact_phone(contact),
                        assigned_to_id=getattr(contact, "assigned_to_id", None), status="open", tags=[contact_type])
    db.add(conv)
    db.flush()
    return conv


def conversation_contact(db: Session, conv: Conversation):
    if conv.client_id and conv.client:
        return "client", conv.client
    if conv.lead_id and conv.lead:
        return "lead", conv.lead
    return None, None


def message_context(db: Session, conv: Conversation) -> dict:
    ctype, contact = conversation_contact(db, conv)
    student = ""
    if ctype == "client" and contact.students:
        student = contact.students[0].full_name
    elif ctype == "lead":
        student = contact.student_name or "your child"
    return {"name": (contact.full_name.split()[0] if contact else conv.contact_name), "student": student, "link": settings.BASE_URL + "/portal",
            "teacher": "your teacher", "college": "Online Quran College"}


def opted_in(db: Session, conv: Conversation) -> bool:
    ctype, contact = conversation_contact(db, conv)
    return bool(getattr(contact, "whatsapp_opt_in", True)) if contact else True


def _touch_conversation(conv: Conversation, msg: Message) -> None:
    conv.last_message_at = msg.created_at
    conv.last_message_preview = (msg.body or "")[:200]
    if msg.direction == "in":
        conv.unread_count = (conv.unread_count or 0) + 1
        if conv.status == "closed":
            conv.status = "open"


def send_message(db: Session, conv: Conversation, body: str, user: Optional[User] = None, template_name: Optional[str] = None,
                 message_type: str = "text", when: Optional[datetime] = None, simulate_failure: bool = False) -> Message:
    msg = Message(conversation_id=conv.id, direction="out", body=body, message_type=message_type, template_name=template_name,
                  sender_id=user.id if user else None, status="queued", created_at=when or datetime.utcnow())
    db.add(msg)
    db.flush()
    if not opted_in(db, conv):
        msg.status = "failed"
        msg.error = "Contact has opted out of WhatsApp messages"
    elif not conv.contact_phone:
        msg.status = "failed"
        msg.error = "No phone number on file"
    elif simulate_failure:
        msg.status = "failed"
        msg.error = "Simulated provider error (rate limited)"
    else:
        try:
            res = send_whatsapp(db, conv.contact_phone, body, template=template_name if message_type == "template" else None)
            msg.external_id = res.get("id") or (res.get("messages", [{}])[0].get("id") if isinstance(res.get("messages"), list) else None)
            msg.status = "sent"
        except Exception as exc:
            msg.status = "failed"
            msg.error = str(exc)[:500]
    _touch_conversation(conv, msg)
    ctype, contact = conversation_contact(db, conv)
    if ctype == "lead" and msg.status != "failed":
        contact.last_contacted_at = msg.created_at
        contact.follow_up_count = (contact.follow_up_count or 0) + 1
        if contact.stage == "new":
            contact.stage = "contacted"
    return msg


def retry_message(db: Session, msg: Message, user: Optional[User] = None) -> Message:
    conv = msg.conversation
    if not opted_in(db, conv):
        msg.error = "Contact has opted out of WhatsApp messages"
        return msg
    try:
        res = send_whatsapp(db, conv.contact_phone or "", msg.body, template=msg.template_name if msg.message_type == "template" else None)
        msg.external_id = res.get("id")
        msg.status = "sent"
        msg.error = None
    except Exception as exc:
        msg.error = str(exc)[:500]
    log_action(db, user, "retry", "inbox", entity=msg, description=f"Retried message {msg.id} -> {msg.status}")
    return msg


def find_contact_by_phone(db: Session, phone: str):
    d = digits(phone)
    if len(d) < 7:
        return None, None
    tail = d[-8:]
    c = db.query(Client).filter(or_(Client.whatsapp.like(f"%{tail}"), Client.phone.like(f"%{tail}"))).first()
    if c:
        return "client", c
    l = db.query(Lead).filter(or_(Lead.whatsapp.like(f"%{tail}"), Lead.phone.like(f"%{tail}"))).order_by(Lead.created_at.desc()).first()
    if l:
        return "lead", l
    return None, None


def receive_message(db: Session, phone: str, body: str, name: Optional[str] = None, external_id: Optional[str] = None,
                    when: Optional[datetime] = None, conv: Optional[Conversation] = None) -> tuple[Conversation, Message, bool]:
    """Inbound WhatsApp message: append to the contact's conversation, auto-create a lead for unknown numbers, stop sequences."""
    created_lead = False
    if conv is None:
        ctype, contact = find_contact_by_phone(db, phone)
        if not contact:
            src = db.query(LeadSource).filter(LeadSource.name == "WhatsApp").first()
            contact, _ = create_lead(db, {"full_name": name or f"WhatsApp {phone[-4:]}", "phone": phone, "whatsapp": phone, "source_id": src.id if src else None,
                                          "notes": "Auto-created from inbound WhatsApp message", "generator_id": None}, actor=None)
            ctype, created_lead = "lead", True
        conv = get_or_create_conversation(db, ctype, contact)
    msg = Message(conversation_id=conv.id, direction="in", body=body, message_type="text", status="delivered", external_id=external_id,
                  created_at=when or datetime.utcnow())
    db.add(msg)
    db.flush()
    _touch_conversation(conv, msg)
    # stop active sequences for this contact (they replied)
    ctype, contact = conversation_contact(db, conv)
    if contact:
        ids = [contact.id]
        for e in db.query(SequenceEnrollment).filter(SequenceEnrollment.status == "active", SequenceEnrollment.contact_type == ctype,
                                                     SequenceEnrollment.contact_id.in_(ids)):
            e.status = "replied"
            e.stop_reason = "Contact replied"
        if ctype == "lead":
            add_activity(db, contact, "whatsapp", f"Inbound: {body[:120]}", None, when=msg.created_at)
    if conv.assigned_to_id:
        notify(db, conv.assigned_to_id, f"New WhatsApp reply from {conv.contact_name}", body[:140], event_type="inbox_reply", link=f"/crm/inbox?c={conv.id}")
    return conv, msg, created_lead


def set_opt_in(db: Session, conv: Conversation, opted: bool, user: Optional[User] = None) -> None:
    ctype, contact = conversation_contact(db, conv)
    if contact is None:
        return
    contact.whatsapp_opt_in = opted
    if ctype == "client":
        pref = db.query(CommunicationPreference).filter(CommunicationPreference.client_id == contact.id, CommunicationPreference.channel == "whatsapp").first()
        if not pref:
            pref = CommunicationPreference(client_id=contact.id, user_id=contact.user_id, channel="whatsapp")
            db.add(pref)
        pref.opted_in = opted
        pref.consent_at = datetime.utcnow()
    tags = [t for t in (conv.tags or []) if t != "opted_out"]
    if not opted:
        tags.append("opted_out")
    conv.tags = tags
    log_action(db, user, "update", "inbox", entity=conv, description=f"WhatsApp opt-{'in' if opted else 'out'} for {conv.contact_name}", consequential=not opted)


# ----------------------------------------------------------------------------- sequences
def enroll_sequence(db: Session, sequence_type: str, contact_type: str, contact_id: int, enrolled_by: str = "system",
                    sequence: Optional[Sequence] = None) -> Optional[SequenceEnrollment]:
    """Idempotent: one active enrollment per contact + sequence type."""
    seq = sequence or db.query(Sequence).filter(Sequence.sequence_type == sequence_type, Sequence.is_active.is_(True)).order_by(Sequence.id).first()
    if not seq:
        return None
    existing = (db.query(SequenceEnrollment).join(Sequence).filter(Sequence.sequence_type == seq.sequence_type, SequenceEnrollment.contact_type == contact_type,
                                                                   SequenceEnrollment.contact_id == contact_id, SequenceEnrollment.status == "active").first())
    if existing:
        return existing
    steps = seq.steps or []
    first_delay = int(steps[0].get("day", 0)) if steps else 0
    e = SequenceEnrollment(sequence_id=seq.id, contact_type=contact_type, contact_id=contact_id, current_step=0,
                           next_run_at=datetime.utcnow() + timedelta(days=first_delay), status="active" if steps else "completed", enrolled_by=enrolled_by)
    db.add(e)
    db.flush()
    return e


def resolve_enrollment_contact(db: Session, e: SequenceEnrollment):
    if e.contact_type == "lead":
        l = db.get(Lead, e.contact_id)
        return ("lead", l) if l else (None, None)
    if e.contact_type == "client":
        c = db.get(Client, e.contact_id)
        return ("client", c) if c else (None, None)
    if e.contact_type == "student":
        s = db.get(Student, e.contact_id)
        return ("client", s.client) if s and s.client else (None, None)
    return None, None


def run_sequences(db: Session, now: Optional[datetime] = None, limit: int = 200) -> dict:
    now = now or datetime.utcnow()
    sent = completed = stopped = 0
    q = db.query(SequenceEnrollment).filter(SequenceEnrollment.status == "active", SequenceEnrollment.next_run_at <= now).limit(limit)
    for e in q.all():
        seq = e.sequence
        steps = seq.steps or []
        if not seq.is_active:
            e.status, e.stop_reason = "stopped", "Sequence deactivated"
            stopped += 1
            continue
        if e.current_step >= len(steps):
            e.status = "completed"
            completed += 1
            continue
        ctype, contact = resolve_enrollment_contact(db, e)
        if not contact:
            e.status, e.stop_reason = "stopped", "Contact no longer exists"
            stopped += 1
            continue
        if ctype == "lead" and contact.stage in ("won", "lost"):
            e.status, e.stop_reason = "stopped", f"Lead {contact.stage}"
            stopped += 1
            continue
        step = steps[e.current_step]
        conv = get_or_create_conversation(db, ctype, contact)
        ctx = message_context(db, conv)
        body = step.get("body") or template_body(db, step.get("template") or "", "Assalamu Alaikum {{name}}, this is Online Quran College.")
        send_message(db, conv, render_template_body(body, ctx), None, template_name=step.get("template"),
                     message_type="template" if step.get("template") else "text")
        sent += 1
        e.current_step += 1
        if e.current_step >= len(steps):
            e.status, e.next_run_at = "completed", None
            completed += 1
        else:
            delta = int(steps[e.current_step].get("day", 0)) - int(step.get("day", 0))
            e.next_run_at = now + timedelta(days=max(0, delta))
    return {"sent": sent, "completed": completed, "stopped": stopped}


# ----------------------------------------------------------------------------- cases
DEPT_FOR_CASE_TYPE = {"billing": "finance", "technical": "technology", "schedule_change": "operations", "teacher_change": "operations",
                      "request": "operations", "feedback": "qa", "complaint": "qa"}
DEPT_FOR_AI = {"finance": "finance", "technology": "technology", "qa": "qa", "operations": "operations", "academics": "academics", "people": "people"}


def case_sla_hours(db: Session, case_type: str, priority: str) -> int:
    sla = setting(db, "case_sla_hours", {}) or {}
    if priority == "urgent" and sla.get("urgent"):
        return int(sla["urgent"])
    hours = int(sla.get(case_type, sla.get("complaint", 48)))
    if priority == "high":
        hours = max(4, hours // 2)
    return hours


def open_case(db: Session, case_type: str, title: str, description: Optional[str], client: Optional[Client] = None, student: Optional[Student] = None,
              teacher: Optional[Teacher] = None, raised_by_user: Optional[User] = None, source: str = "portal", priority: Optional[str] = None,
              department_code: Optional[str] = None, assigned_to: Optional[User] = None, actor: Optional[User] = None, created_at: Optional[datetime] = None,
              request=None) -> Case:
    """Number the case (CS-), AI-classify (category / priority / department), compute SLA, assign to department HOD, notify."""
    result, run = ai(db, "complaint_classification", "classify_case", {"text": f"{title}. {description or ''}", "case_type": case_type})
    category = result.get("category") or "general"
    priority = priority or result.get("priority") or "medium"
    if priority not in PRIORITIES:
        priority = "medium"
    dept_code = department_code or DEPT_FOR_AI.get(result.get("suggested_department") or "", None) or DEPT_FOR_CASE_TYPE.get(case_type, "operations")
    if case_type in ("billing", "technical"):
        dept_code = DEPT_FOR_CASE_TYPE[case_type]
    dept = department_by_code(db, dept_code)
    assignee = assigned_to or (dept.hod if dept and dept.hod else None) or user_by_email(db, "manager@oqc.local")
    hours = case_sla_hours(db, case_type, priority)
    now = created_at or datetime.utcnow()
    if student and not client:
        client = student.client
    if student and not teacher and student.teacher:
        teacher = student.teacher
    raised_type = "system" if source == "feedback" else ("staff" if source == "staff" else ("student" if student and not client else "client"))
    case = Case(case_number=next_code(db, Case, "case_number", "CS-"), case_type=case_type, title=title.strip()[:200], description=description,
                raised_by_type=raised_type, raised_by_user_id=raised_by_user.id if raised_by_user else None, client_id=client.id if client else None,
                student_id=student.id if student else None, teacher_id=teacher.id if teacher else None, department_id=dept.id if dept else None,
                category=category, priority=priority, status="open", assigned_to_id=assignee.id if assignee else None, sla_hours=hours,
                sla_due_at=now + timedelta(hours=hours), ai_run_id=run.id, source=source)
    case.created_at = now
    db.add(case)
    db.flush()
    run.entity_type, run.entity_id = "Case", case.id
    db.add(CaseComment(case_id=case.id, user_id=actor.id if actor else None, is_internal=True, created_at=now,
                       text=f"AI classification: {category} / {priority} -> {dept.name if dept else 'Operations'} (confidence {result.get('confidence')}). SLA {hours}h."))
    if assignee:
        notify(db, assignee, f"New {case_type.replace('_', ' ')} assigned: {case.case_number}", f"{title} [{priority}] - SLA {hours}h",
               event_type="case_assigned", link=f"/cases/{case.id}")
    log_action(db, actor, "create", "cases", entity=case, description=f"Case {case.case_number} opened ({case_type}, {priority})", request=request,
               after={"category": category, "priority": priority, "department": dept_code, "sla_hours": hours})
    emit_event(db, "case.opened", {"case_id": case.id, "case_number": case.case_number, "type": case_type, "priority": priority})
    return case


def case_client_user(case: Case) -> Optional[User]:
    if case.client and case.client.user:
        return case.client.user
    if case.student and case.student.client and case.student.client.user:
        return case.student.client.user
    return None


def change_case_status(db: Session, case: Case, status: str, user: Optional[User], resolution: Optional[str] = None, root_cause: Optional[str] = None,
                       note: Optional[str] = None, request=None) -> None:
    if status not in CASE_STATUSES:
        raise ValueError("Invalid status")
    if status in ("resolved", "closed") and case.status not in ("resolved", "closed"):
        if not (resolution or "").strip() or not (root_cause or "").strip():
            raise ValueError("Resolution and root cause are required to resolve a case")
    before = case.status
    case.status = status
    if resolution:
        case.resolution = resolution
    if root_cause:
        case.root_cause = root_cause
    if status == "resolved":
        case.resolved_at = datetime.utcnow()
    if status == "closed":
        case.closed_at = datetime.utcnow()
        case.resolved_at = case.resolved_at or datetime.utcnow()
    db.add(CaseComment(case_id=case.id, user_id=user.id if user else None, is_internal=True, text=f"Status {before} -> {status}" + (f": {note}" if note else "")))
    log_action(db, user, "status_change", "cases", entity=case, description=f"{case.case_number} {before} -> {status}", rationale=root_cause or note,
               before={"status": before}, after={"status": status}, request=request, consequential=status in ("resolved", "closed"))
    cu = case_client_user(case)
    if cu and status in ("resolved", "closed", "in_progress", "waiting"):
        contact = case.client or (case.student.client if case.student else None)
        notify(db, cu, f"Your case {case.case_number} is {status.replace('_', ' ')}", (resolution or note or case.title)[:300],
               event_type="case_status", link=f"/portal/cases", channels=("in_app", "whatsapp"), recipient_address=contact.whatsapp if contact else None)
    if status in ("resolved", "closed"):
        for fb in db.query(Feedback).filter(Feedback.case_id == case.id):
            fb.status = "resolved"
    emit_event(db, "case.status_changed", {"case_id": case.id, "status": status})


def escalate_case(db: Session, case: Case, user: Optional[User], reason: str, target: Optional[User] = None, request=None) -> None:
    target = target or (case.department.hod if case.department and case.department.hod else None) or user_by_email(db, "manager@oqc.local")
    case.escalated = True
    case.escalated_to_id = target.id if target else None
    if case.status in ("open", "in_progress", "waiting"):
        case.status = "escalated"
    if case.priority in ("low", "medium"):
        case.priority = "high"
    db.add(CaseComment(case_id=case.id, user_id=user.id if user else None, is_internal=True, text=f"Escalated to {target.full_name if target else 'management'}: {reason}"))
    db.add(RiskAlert(alert_type="case_escalated", severity="high" if case.priority != "urgent" else "critical", title=f"Case {case.case_number} escalated",
                     message=f"{case.title} - {reason}", entity_type="Case", entity_id=case.id, visibility="management", source="system" if user is None else "manual"))
    if target:
        notify(db, target, f"Case escalated to you: {case.case_number}", f"{case.title}. {reason}", event_type="case_escalated", link=f"/cases/{case.id}")
    log_action(db, user, "escalate", "cases", entity=case, description=f"{case.case_number} escalated", rationale=reason, request=request, consequential=True)


def case_sla_monitor(db: Session) -> dict:
    now = datetime.utcnow()
    breached = 0
    warned = 0
    for case in db.query(Case).filter(Case.status.in_(OPEN_CASE_STATUSES), Case.sla_due_at.isnot(None)):
        if case.sla_due_at <= now and not case.sla_breached:
            case.sla_breached = True
            breached += 1
            if case.assigned_to_id:
                notify(db, case.assigned_to_id, f"SLA breached: {case.case_number}", case.title, event_type="case_sla", link=f"/cases/{case.id}")
            if not case.escalated:
                escalate_case(db, case, None, "SLA breached - auto-escalated to department head")
        elif not case.sla_breached and now <= case.sla_due_at <= now + timedelta(hours=2):
            warned += 1
            if case.assigned_to_id:
                exists = db.query(func.count()).select_from(RiskAlert).filter(RiskAlert.entity_type == "Case", RiskAlert.entity_id == case.id,
                                                                              RiskAlert.alert_type == "case_sla_warning").scalar()
                if not exists:
                    db.add(RiskAlert(alert_type="case_sla_warning", severity="medium", title=f"Case {case.case_number} SLA due within 2h", message=case.title,
                                     entity_type="Case", entity_id=case.id, visibility="ops"))
                    notify(db, case.assigned_to_id, f"SLA at risk: {case.case_number}", "Due within 2 hours.", event_type="case_sla", link=f"/cases/{case.id}")
    return {"breached": breached, "warned": warned}


def case_trends(db: Session, months: int = 6) -> dict:
    cases = db.query(Case).all()
    by = lambda key: _count_by(cases, key)
    by_category = by(lambda c: c.category or "general")
    by_department = by(lambda c: c.department.name if c.department else "Unassigned")
    by_teacher = by(lambda c: c.teacher.full_name if c.teacher else None)
    by_type = by(lambda c: c.case_type)
    today = date.today().replace(day=1)
    labels = []
    for i in range(months - 1, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        labels.append(f"{y:04d}-{m:02d}")
    opened = {k: 0 for k in labels}
    resolved = {k: 0 for k in labels}
    for c in cases:
        k = c.created_at.strftime("%Y-%m") if c.created_at else None
        if k in opened:
            opened[k] += 1
        if c.resolved_at and c.resolved_at.strftime("%Y-%m") in resolved:
            resolved[c.resolved_at.strftime("%Y-%m")] += 1
    res_hours = [(c.resolved_at - c.created_at).total_seconds() / 3600 for c in cases if c.resolved_at and c.created_at]
    closed = [c for c in cases if c.status in ("resolved", "closed")]
    within = sum(1 for c in closed if not c.sla_breached and (not c.sla_due_at or (c.resolved_at or c.closed_at) <= c.sla_due_at))
    open_cases = [c for c in cases if c.status in OPEN_CASE_STATUSES]
    return {"by_category": by_category, "by_department": by_department, "by_teacher": by_teacher, "by_type": by_type, "labels": labels,
            "opened": [opened[k] for k in labels], "resolved": [resolved[k] for k in labels],
            "avg_resolution_hours": round(sum(res_hours) / len(res_hours), 1) if res_hours else 0.0,
            "sla_compliance": round(100 * within / len(closed), 1) if closed else 100.0, "total": len(cases), "open": len(open_cases),
            "breached_open": sum(1 for c in open_cases if c.sla_breached), "root_causes": by(lambda c: c.root_cause)}


def _count_by(rows, keyfn) -> list[tuple[str, int]]:
    d: dict[str, int] = {}
    for r in rows:
        k = keyfn(r)
        if k:
            d[k] = d.get(k, 0) + 1
    return sorted(d.items(), key=lambda x: -x[1])


# ----------------------------------------------------------------------------- feedback / surveys
def survey_link(fb: Feedback) -> str:
    return f"{settings.BASE_URL}/survey/{fb.token}"


def send_survey(db: Session, survey: Survey, client: Optional[Client] = None, student: Optional[Student] = None, employee: Optional[Employee] = None,
                actor: Optional[User] = None, trigger: Optional[str] = None, teacher: Optional[Teacher] = None, sent_at: Optional[datetime] = None) -> Feedback:
    respondent = survey.audience if survey.audience in ("client", "student", "staff") else "client"
    if student and not client:
        client = student.client
    if student and not teacher:
        teacher = student.teacher
    fb = Feedback(survey_id=survey.id, trigger=trigger or survey.trigger, respondent_type=respondent, client_id=client.id if client else None,
                  student_id=student.id if student else None, employee_id=employee.id if employee else None, teacher_id=teacher.id if teacher else None,
                  status="pending", sent_at=sent_at or datetime.utcnow(), is_confidential=(respondent == "staff"))
    db.add(fb)
    db.flush()
    fb.token = sign_value(str(fb.id), "survey")
    link = survey_link(fb)
    if respondent == "staff":
        if employee and employee.user_id:
            notify(db, employee.user_id, "Confidential staff pulse survey", f"Please share your anonymous feedback: {link}", event_type="survey", link=f"/survey/{fb.token}")
    elif client:
        body = render_template_body(template_body(db, "feedback_survey", "Assalamu Alaikum {{name}}, please share 1-minute feedback about {{student}}'s classes: {{link}}"),
                                    {"name": client.full_name.split()[0], "student": student.full_name if student else (client.students[0].full_name if client.students else "your child"), "link": link})
        conv = get_or_create_conversation(db, "client", client)
        send_message(db, conv, body, actor, template_name="feedback_survey", message_type="template", when=fb.sent_at)
        if client.user_id:
            notify(db, client.user_id, "We would love your feedback", body, event_type="survey", link=f"/survey/{fb.token}")
    log_action(db, actor, "send", "feedback", entity=fb, description=f"Survey '{survey.name}' sent to {client.full_name if client else (employee.full_name if employee else 'respondent')}")
    return fb


def submit_feedback(db: Session, fb: Feedback, nps: Optional[int], rating: Optional[int], comment: Optional[str], answers: Optional[dict] = None,
                    submitted_at: Optional[datetime] = None) -> Feedback:
    fb.nps = nps
    fb.rating = rating
    fb.comment = (comment or "").strip() or None
    fb.answers = answers or {}
    fb.submitted_at = submitted_at or datetime.utcnow()
    fb.status = "submitted"
    if fb.comment:
        res, run = ai(db, "sentiment", "feedback_sentiment", {"text": fb.comment, "feedback_id": fb.id}, entity=fb)
        fb.sentiment = res.get("sentiment", "neutral")
    else:
        fb.sentiment = "positive" if (nps or 0) >= 9 or (rating or 0) >= 4 else ("negative" if (nps is not None and nps <= 6) or (rating is not None and rating <= 2) else "neutral")
    fb.is_negative = (nps is not None and nps <= 6) or (rating is not None and rating <= 2) or fb.sentiment == "negative"
    if fb.is_negative:
        route_negative_feedback(db, fb)
    return fb


def route_negative_feedback(db: Session, fb: Feedback) -> Optional[Case]:
    """Negative client/student feedback -> Case for QA HOD with SLA. Staff feedback -> confidential Grievance (no Case)."""
    if fb.case_id:
        return db.get(Case, fb.case_id)
    survey = db.get(Survey, fb.survey_id) if fb.survey_id else None
    if fb.respondent_type == "staff" or (survey and survey.audience == "staff"):
        emp = db.get(Employee, fb.employee_id) if fb.employee_id else None
        handler = user_by_email(db, "hr@oqc.local")
        g = Grievance(employee_id=emp.id if emp else None, submitted_by_id=None, is_anonymous=True, category="staff_enps",
                      subject=f"Negative eNPS response ({fb.nps if fb.nps is not None else '-'}/10)", description=fb.comment or "No comment provided.",
                      status="open", handler_id=handler.id if handler else None, sla_due_at=datetime.utcnow() + timedelta(hours=72))
        db.add(g)
        fb.status = "routed"
        if handler:
            notify(db, handler, "Confidential: negative staff pulse response", "A confidential grievance has been opened from the eNPS survey.", event_type="grievance", link="/hr/grievances")
        return None
    client = db.get(Client, fb.client_id) if fb.client_id else None
    student = db.get(Student, fb.student_id) if fb.student_id else None
    teacher = db.get(Teacher, fb.teacher_id) if fb.teacher_id else None
    qa_hod = user_by_email(db, "qa@oqc.local")
    who = client.full_name if client else (student.full_name if student else "a respondent")
    case = open_case(db, "feedback", f"Negative feedback from {who} (NPS {fb.nps if fb.nps is not None else '-'}, rating {fb.rating if fb.rating is not None else '-'})",
                     fb.comment or "Low score submitted without a comment.", client=client, student=student, teacher=teacher, source="feedback",
                     priority="high" if (fb.nps is not None and fb.nps <= 3) else None, department_code="qa", assigned_to=qa_hod, created_at=fb.submitted_at)
    fb.case_id = case.id
    fb.status = "routed"
    return case


def feedback_summary(db: Session, period: str = "month") -> dict:
    """Command Center aggregate: NPS score, appreciation ratio, resolution rate, response rate, eNPS."""
    days = {"week": 7, "month": 30, "quarter": 90, "year": 365}.get(period, 30)
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.query(Feedback).filter(Feedback.status.in_(["submitted", "routed", "resolved"]), Feedback.submitted_at >= since).all()
    client_rows = [r for r in rows if not r.is_confidential]
    staff_rows = [r for r in rows if r.is_confidential]
    sent = db.query(func.count(Feedback.id)).filter(Feedback.sent_at >= since).scalar() or 0

    def nps_of(items):
        scored = [r.nps for r in items if r.nps is not None]
        if not scored:
            return None, 0, 0, 0
        p = sum(1 for n in scored if n >= 9)
        d = sum(1 for n in scored if n <= 6)
        return round(100 * (p - d) / len(scored)), p, len(scored) - p - d, d

    nps, prom, pas, det = nps_of(client_rows)
    enps, _, _, _ = nps_of(staff_rows)
    negative = [r for r in client_rows if r.is_negative]
    resolved = [r for r in negative if r.status == "resolved" or (r.case_id and (db.get(Case, r.case_id) or Case()).status in ("resolved", "closed"))]
    positive = sum(1 for r in client_rows if r.sentiment == "positive")
    return {"period": period, "responses": len(client_rows), "sent": sent, "response_rate": round(100 * len(rows) / sent, 1) if sent else 0.0,
            "nps": nps, "promoters": prom, "passives": pas, "detractors": det, "enps": enps, "staff_responses": len(staff_rows),
            "appreciation_ratio": round(100 * positive / len(client_rows), 1) if client_rows else 0.0,
            "negative": len(negative), "resolution_rate": round(100 * len(resolved) / len(negative), 1) if negative else 100.0,
            "avg_rating": round(sum(r.rating for r in client_rows if r.rating) / max(1, sum(1 for r in client_rows if r.rating)), 2)}


# ----------------------------------------------------------------------------- ambassadors & referrals
def ambassador_eligibility(db: Session, client: Client) -> dict:
    from app.services.classes import student_attendance_pct
    min_tenure = int(setting(db, "ambassador_tenure_days", 60) or 60)
    min_nps = int(setting(db, "ambassador_min_nps", 8) or 8)
    tenure = (date.today() - (client.joined_at or date.today())).days
    latest = (db.query(Feedback).filter(Feedback.client_id == client.id, Feedback.nps.isnot(None)).order_by(Feedback.submitted_at.desc()).first())
    negative = db.query(func.count(Feedback.id)).filter(Feedback.client_id == client.id, Feedback.is_negative.is_(True),
                                                        Feedback.submitted_at >= datetime.utcnow() - timedelta(days=90)).scalar() or 0
    nps_ok = (latest.nps >= min_nps) if latest else (negative == 0)
    att = max([student_attendance_pct(db, s.id) for s in client.students], default=0.0)
    att_ok = att >= 75
    reasons = []
    if tenure < min_tenure:
        reasons.append(f"tenure {tenure}d < {min_tenure}d")
    if not nps_ok:
        reasons.append(f"latest NPS {latest.nps if latest else '-'} < {min_nps}" if latest else "recent negative feedback")
    if not att_ok:
        reasons.append(f"attendance {att:.0f}% < 75%")
    if client.status not in ("active", "trial"):
        reasons.append(f"client status {client.status}")
    return {"eligible": not reasons, "tenure_days": tenure, "min_tenure": min_tenure, "nps": latest.nps if latest else None, "min_nps": min_nps,
            "nps_ok": nps_ok, "attendance": att, "attendance_ok": att_ok, "reasons": reasons}


def ensure_referral_code(client: Client) -> str:
    if not client.referral_code:
        fam = (client.full_name.split()[-1] if client.full_name else "REF")[:3].upper()
        client.referral_code = f"REF{fam}{client.id:03d}"
    return client.referral_code


def referral_link(client: Client) -> str:
    return f"{settings.BASE_URL}/register?ref={client.referral_code}"


def invite_ambassador(db: Session, client: Client, actor: Optional[User] = None, force: bool = False, rationale: Optional[str] = None,
                      request=None) -> tuple[bool, dict]:
    elig = ambassador_eligibility(db, client)
    if not elig["eligible"] and not force:
        return False, elig
    ensure_referral_code(client)
    client.is_ambassador = True
    client.ambassador_invited_at = datetime.utcnow()
    link = referral_link(client)
    body = render_template_body(template_body(db, "ambassador_invite", "Assalamu Alaikum {{name}}, share your link {{link}} - both families receive a free week when a new student enrols."),
                                {"name": client.full_name.split()[0], "link": link, "student": client.students[0].full_name if client.students else ""})
    conv = get_or_create_conversation(db, "client", client)
    send_message(db, conv, body, actor, template_name="ambassador_invite", message_type="template")
    sup = user_by_email(db, "supervisor@oqc.local")
    db.add(Referral(ambassador_client_id=client.id, referral_code=client.referral_code, status="invited", invited_at=datetime.utcnow(),
                    owner_id=sup.id if sup else None, notes="Ambassador invitation sent" + (" (override)" if force and not elig["eligible"] else "")))
    if client.user_id:
        notify(db, client.user_id, "You are invited to be an OQC Ambassador", body, event_type="ambassador_invite", link="/portal/referrals")
    log_action(db, actor, "invite", "referrals", entity=client, description=f"Ambassador invite for {client.client_code}", rationale=rationale,
               after={"eligibility": elig["reasons"], "forced": force}, request=request, consequential=force and not elig["eligible"])
    return True, elig


def referral_credit_amount(db: Session, referred_client: Optional[Client]) -> tuple[float, str]:
    weeks = float(setting(db, "referral_credit_weeks", 1) or 1)
    if referred_client:
        sub = db.query(Subscription).filter(Subscription.client_id == referred_client.id, Subscription.status == "active").order_by(Subscription.price.desc()).first()
        if sub and float(sub.price or 0) > 0:
            return round(float(sub.price) / 4.33 * weeks, 2), sub.currency or referred_client.currency or "GBP"
        from app.models.academic import Package
        base = db.query(Package).filter(Package.is_active.is_(True), Package.is_trial.is_(False))
        pkg = base.filter(Package.currency == (referred_client.currency or "GBP")).order_by(Package.price).first() or base.order_by(Package.price).first()
        if pkg and float(pkg.price or 0) > 0:
            return round(float(pkg.price) / 4.33 * weeks, 2), pkg.currency
    return round(10.0 * weeks, 2), "GBP"


def _post_credit_direct(db: Session, client: Client, amount: float, currency: str, description: str, reference_id: Optional[int], actor: Optional[User]) -> LedgerEntry:
    last = db.query(LedgerEntry).filter(LedgerEntry.client_id == client.id).order_by(LedgerEntry.id.desc()).first()
    bal = float(last.balance_after or 0) if last else 0.0
    e = LedgerEntry(client_id=client.id, entry_date=date.today(), entry_type="credit", description=description, debit=0, credit=amount, currency=currency,
                    balance_after=round(bal - amount, 2), reference_type="referral", reference_id=reference_id, created_by_id=actor.id if actor else None)
    db.add(e)
    db.flush()
    return e


def post_referral_credit(db: Session, client: Client, amount: float, currency: str, description: str, reference_id: Optional[int], actor: Optional[User]) -> LedgerEntry:
    try:
        from app.services.billing import post_credit  # type: ignore
    except Exception:
        return _post_credit_direct(db, client, amount, currency, description, reference_id, actor)
    try:
        entry = post_credit(db, client, amount, description, currency=currency, reference_type="referral", reference_id=reference_id, actor=actor)
        if isinstance(entry, LedgerEntry):
            return entry
    except Exception:
        pass
    return _post_credit_direct(db, client, amount, currency, description, reference_id, actor)


def referred_has_active_subscription(db: Session, ref: Referral) -> bool:
    if not ref.referred_client_id:
        return False
    return db.query(func.count(Subscription.id)).filter(Subscription.client_id == ref.referred_client_id, Subscription.status == "active").scalar() > 0


def qualify_referral(db: Session, ref: Referral, actor: Optional[User], rationale: Optional[str] = None, request=None) -> Referral:
    """Qualified referral -> dual-sided account credits (never cash or coupons) for ambassador and referred client."""
    if ref.status == "credited":
        return ref
    amb = ref.ambassador
    referred = ref.referred_client
    has_sub = referred_has_active_subscription(db, ref)
    if not has_sub and not (rationale or "").strip():
        raise ValueError("Referred client has no active subscription - a rationale is required for manual qualification")
    amount, currency = referral_credit_amount(db, referred)
    ref.status = "qualified"
    ref.qualified_at = datetime.utcnow()
    ref.credit_amount = amount
    ref.credit_currency = currency
    desc = f"Ambassador referral credit ({ref.referral_code})"
    e1 = post_referral_credit(db, amb, amount, currency, desc + f" - referred {referred.client_code if referred else ref.referred_name}", ref.id, actor)
    ref.ambassador_credit_ledger_id = e1.id
    if referred:
        e2 = post_referral_credit(db, referred, amount, currency, desc + f" - welcome credit from {amb.client_code}", ref.id, actor)
        ref.referred_credit_ledger_id = e2.id
    ref.status = "credited"
    if amb.user_id:
        notify(db, amb.user_id, "Referral credit applied", f"JazakAllah Khair! A credit of {currency} {amount:.2f} was added to your account for referring {referred.full_name if referred else ref.referred_name}.",
               event_type="referral_credit", link="/portal/billing", channels=("in_app", "whatsapp"), recipient_address=amb.whatsapp)
    if referred and referred.user_id:
        notify(db, referred.user_id, "Welcome credit applied", f"A welcome credit of {currency} {amount:.2f} was added to your account thanks to {amb.full_name}'s referral.",
               event_type="referral_credit", link="/portal/billing", channels=("in_app", "whatsapp"), recipient_address=referred.whatsapp)
    log_action(db, actor, "approve", "referrals", entity=ref, description=f"Referral {ref.id} qualified and credited {currency} {amount:.2f} to both clients",
               rationale=rationale or ("Referred client has an active subscription"), after={"amount": amount, "currency": currency, "ledger": [e1.id, ref.referred_credit_ledger_id]},
               request=request, consequential=True)
    emit_event(db, "referral.credited", {"referral_id": ref.id, "amount": amount, "currency": currency})
    return ref


def referral_dashboard(db: Session) -> dict:
    now = datetime.utcnow()
    week = now - timedelta(days=7)
    month = now - timedelta(days=30)
    refs = db.query(Referral).all()

    def count(status_set, since=None, field="created_at"):
        return sum(1 for r in refs if r.status in status_set and (since is None or (getattr(r, field) or r.created_at) >= since))

    stats = {}
    for label, since in (("week", week), ("month", month), ("all", None)):
        stats[label] = {"asks": count(("ask", "lead", "signed_up", "qualified", "credited"), since), "invited": count(("invited",), since),
                        "leads": count(("lead", "signed_up", "qualified", "credited"), since), "signups": count(("signed_up", "qualified", "credited"), since),
                        "qualified": count(("qualified", "credited"), since), "credited": count(("credited",), since, "qualified_at"),
                        "credit_total": round(sum(float(r.credit_amount or 0) for r in refs if r.status == "credited" and (since is None or (r.qualified_at or r.created_at) >= since)), 2)}
    new_clients = db.query(func.count(Client.id)).filter(Client.joined_at >= (date.today() - timedelta(days=30))).scalar() or 0
    referred_clients = db.query(func.count(Client.id)).filter(Client.joined_at >= (date.today() - timedelta(days=30)), Client.source == "Referral").scalar() or 0
    weekly = []
    for i in range(7, -1, -1):
        start = (now - timedelta(days=7 * i)).date()
        start = start - timedelta(days=start.weekday())
        end = start + timedelta(days=7)
        rows = [r for r in refs if start <= (r.created_at.date() if r.created_at else start) < end]
        weekly.append({"week": start.isoformat(), "asks": len(rows), "leads": sum(1 for r in rows if r.status in ("lead", "signed_up", "qualified", "credited")),
                       "signups": sum(1 for r in rows if r.status in ("signed_up", "qualified", "credited")), "credited": sum(1 for r in rows if r.status == "credited")})
    ambassadors = db.query(func.count(Client.id)).filter(Client.is_ambassador.is_(True)).scalar() or 0
    return {"stats": stats, "referral_pct": round(100 * referred_clients / new_clients, 1) if new_clients else 0.0, "new_clients_30d": new_clients,
            "referred_clients_30d": referred_clients, "weekly": weekly, "ambassadors": ambassadors,
            "by_status": {s: sum(1 for r in refs if r.status == s) for s in REFERRAL_STATUSES}}


# ----------------------------------------------------------------------------- campaigns & marketing analytics
def campaign_totals(db: Session, campaign: Campaign, start: Optional[date] = None, end: Optional[date] = None) -> dict:
    q = db.query(func.coalesce(func.sum(CampaignMetric.impressions), 0), func.coalesce(func.sum(CampaignMetric.clicks), 0),
                 func.coalesce(func.sum(CampaignMetric.leads), 0), func.coalesce(func.sum(CampaignMetric.spend), 0),
                 func.coalesce(func.sum(CampaignMetric.conversions), 0), func.coalesce(func.sum(CampaignMetric.revenue), 0)).filter(CampaignMetric.campaign_id == campaign.id)
    if start:
        q = q.filter(CampaignMetric.date >= start)
    if end:
        q = q.filter(CampaignMetric.date <= end)
    imp, clicks, leads_m, spend, conv, rev = q.one()
    lq = db.query(Lead).filter(Lead.campaign_id == campaign.id)
    if start:
        lq = lq.filter(Lead.created_at >= datetime.combine(start, datetime.min.time()))
    if end:
        lq = lq.filter(Lead.created_at <= datetime.combine(end, datetime.max.time()))
    crm_leads = lq.count()
    crm_won = lq.filter(Lead.stage == "won").count()
    spend = float(spend or 0)
    rev = float(rev or 0)
    leads_n = max(int(leads_m or 0), crm_leads)
    won_n = max(int(conv or 0), crm_won)
    return {"impressions": int(imp or 0), "clicks": int(clicks or 0), "leads": leads_n, "spend": round(spend, 2), "conversions": won_n, "revenue": round(rev, 2),
            "crm_leads": crm_leads, "crm_won": crm_won, "ctr": round(100 * int(clicks or 0) / int(imp), 2) if imp else 0.0,
            "cpl": round(spend / leads_n, 2) if leads_n else 0.0, "cac": round(spend / won_n, 2) if won_n else 0.0,
            "roi": round(100 * (rev - spend) / spend, 1) if spend else 0.0, "roas": round(rev / spend, 2) if spend else 0.0,
            "conversion": round(100 * won_n / leads_n, 1) if leads_n else 0.0}


def _won_revenue(db: Session, leads: list[Lead]) -> float:
    ids = [l.converted_client_id for l in leads if l.stage == "won" and l.converted_client_id]
    if not ids:
        return 0.0
    total = db.query(func.coalesce(func.sum(Subscription.price_in_base), 0)).filter(Subscription.client_id.in_(ids)).scalar()
    return round(float(total or 0), 2)


def marketing_dashboard(db: Session, start: date, end: date) -> dict:
    s_dt, e_dt = datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.max.time())
    leads = db.query(Lead).filter(Lead.created_at >= s_dt, Lead.created_at <= e_dt).all()
    trial_lead_ids = {t.lead_id for t in db.query(Trial.lead_id).filter(Trial.lead_id.isnot(None))}
    spend_by_campaign: dict[int, float] = {}
    for cid, sp in db.query(CampaignMetric.campaign_id, func.sum(CampaignMetric.spend)).filter(CampaignMetric.date >= start, CampaignMetric.date <= end).group_by(CampaignMetric.campaign_id):
        spend_by_campaign[cid] = float(sp or 0)
    leads_per_campaign: dict[int, int] = {}
    for l in leads:
        if l.campaign_id:
            leads_per_campaign[l.campaign_id] = leads_per_campaign.get(l.campaign_id, 0) + 1

    def dim_rows(keyfn):
        groups: dict[str, list[Lead]] = {}
        for l in leads:
            k = keyfn(l) or "Unknown"
            groups.setdefault(k, []).append(l)
        rows = []
        for k, ls in groups.items():
            spend = 0.0
            for l in ls:
                if l.campaign_id and leads_per_campaign.get(l.campaign_id):
                    spend += spend_by_campaign.get(l.campaign_id, 0.0) / leads_per_campaign[l.campaign_id]
            won = sum(1 for l in ls if l.stage == "won")
            contacted = sum(1 for l in ls if l.stage != "new" or l.last_contacted_at)
            trials = sum(1 for l in ls if l.id in trial_lead_ids or l.stage in ("trial_scheduled", "trial_done", "negotiation", "won"))
            rev = _won_revenue(db, ls)
            rows.append({"key": k, "leads": len(ls), "contacted": contacted, "trials": trials, "won": won, "spend": round(spend, 2), "revenue": rev,
                         "cpl": round(spend / len(ls), 2) if ls else 0.0, "cac": round(spend / won, 2) if won else 0.0,
                         "conversion": round(100 * won / len(ls), 1) if ls else 0.0, "trial_conversion": round(100 * won / trials, 1) if trials else 0.0,
                         "roas": round(rev / spend, 2) if spend else 0.0})
        return sorted(rows, key=lambda r: -r["leads"])

    by_source = dim_rows(lambda l: l.source.name if l.source else None)
    by_campaign = dim_rows(lambda l: l.campaign.name if l.campaign else "No campaign")
    by_country = dim_rows(lambda l: l.country)
    by_offer = dim_rows(lambda l: l.campaign.offer if l.campaign and l.campaign.offer else "No offer")
    total_spend = round(sum(spend_by_campaign.values()), 2)
    won = sum(1 for l in leads if l.stage == "won")
    contacted = sum(1 for l in leads if l.stage != "new" or l.last_contacted_at)
    trials = sum(1 for l in leads if l.id in trial_lead_ids or l.stage in ("trial_scheduled", "trial_done", "negotiation", "won"))
    revenue = _won_revenue(db, leads)
    # month over month (last 6 months, independent of range)
    mom = []
    today = date.today().replace(day=1)
    for i in range(5, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        ms = date(y, m, 1)
        me = date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
        mls = db.query(Lead).filter(Lead.created_at >= datetime.combine(ms, datetime.min.time()), Lead.created_at <= datetime.combine(me, datetime.max.time())).all()
        msp = float(db.query(func.coalesce(func.sum(CampaignMetric.spend), 0)).filter(CampaignMetric.date >= ms, CampaignMetric.date <= me).scalar() or 0)
        mw = sum(1 for l in mls if l.stage == "won")
        mom.append({"month": ms.strftime("%b %Y"), "leads": len(mls), "won": mw, "spend": round(msp, 2), "revenue": _won_revenue(db, mls),
                    "cpl": round(msp / len(mls), 2) if mls else 0.0, "cac": round(msp / mw, 2) if mw else 0.0})
    return {"start": start, "end": end, "leads": len(leads), "contacted": contacted, "trials": trials, "won": won, "spend": total_spend, "revenue": revenue,
            "cpl": round(total_spend / len(leads), 2) if leads else 0.0, "cac": round(total_spend / won, 2) if won else 0.0,
            "conversion": round(100 * won / len(leads), 1) if leads else 0.0, "trial_conversion": round(100 * won / trials, 1) if trials else 0.0,
            "by_source": by_source, "by_campaign": by_campaign, "by_country": by_country, "by_offer": by_offer, "mom": mom,
            "funnel": [("Leads", len(leads)), ("Contacted", contacted), ("Trial", trials), ("Won", won)]}


# ----------------------------------------------------------------------------- trials
def create_trial(db: Session, student_name: str, lead: Optional[Lead] = None, teacher: Optional[Teacher] = None, course: Optional[Course] = None,
                 scheduled_at: Optional[datetime] = None, actor: Optional[User] = None, student: Optional[Student] = None, client: Optional[Client] = None,
                 notes: Optional[str] = None, request=None) -> Trial:
    tr = Trial(lead_id=lead.id if lead else None, client_id=client.id if client else (lead.converted_client_id if lead else None),
               student_id=student.id if student else None, teacher_id=teacher.id if teacher else None,
               course_id=course.id if course else (lead.course_interest_id if lead else None), student_name=student_name,
               scheduled_at=scheduled_at, status="scheduled" if scheduled_at else "requested", closer_id=(lead.assigned_to_id if lead else None) or (actor.id if actor else None),
               notes=notes, follow_up_date=(scheduled_at.date() + timedelta(days=1)) if scheduled_at else None)
    db.add(tr)
    db.flush()
    if scheduled_at:
        schedule_trial_session(db, tr, actor)
    if lead:
        add_activity(db, lead, "trial", f"Trial {'scheduled for ' + scheduled_at.strftime('%d %b %Y %H:%M') if scheduled_at else 'requested'}"
                     + (f" with {teacher.full_name}" if teacher else ""), actor)
        if lead.stage in ("new", "contacted") and scheduled_at:
            lead.stage = "trial_scheduled"
    log_action(db, actor, "create", "trials", entity=tr, description=f"Trial for {student_name} ({tr.status})", request=request)
    if teacher and teacher.user_id and scheduled_at:
        notify(db, teacher.user_id, "Trial class scheduled", f"{student_name} on {scheduled_at.strftime('%d %b %Y %H:%M')}", event_type="trial", link="/teacher/trials")
    return tr


def schedule_trial_session(db: Session, tr: Trial, actor: Optional[User] = None) -> None:
    """Create a one-off ClassSession(is_trial=True) when a student + teacher exist; otherwise record the room in notes."""
    from app.models.scheduling import ClassSession
    from app.services.integrations import build_join_url
    if not tr.scheduled_at or not tr.teacher_id:
        return
    room = f"OQC-TRIAL-{tr.id:04d}"
    try:
        from app.services import scheduling as sched  # type: ignore
        helper = getattr(sched, "create_trial_session", None)
    except Exception:
        helper = None
    if helper and tr.student_id:
        try:
            sess = helper(db, tr, actor)
            if sess is not None:
                tr.session_id = sess.id
                return
        except Exception:
            pass
    if tr.student_id:
        st = tr.scheduled_at
        sess = ClassSession(student_id=tr.student_id, teacher_id=tr.teacher_id, course_id=tr.course_id, date=st.date(), start_time=st.time(),
                            end_time=(st + timedelta(minutes=30)).time(), scheduled_start=st, duration_minutes=30, status="pending", is_trial=True,
                            room_name=room, join_url=build_join_url(room, tr.student_name))
        db.add(sess)
        db.flush()
        tr.session_id = sess.id
    else:
        tr.notes = ((tr.notes or "") + f"\nRoom {room}: {build_join_url(room, tr.student_name)}").strip()


def trial_stats(db: Session) -> dict:
    rows = db.query(Trial).all()
    by = {s: sum(1 for t in rows if t.status == s) for s in ("requested", "scheduled", "attended", "no_show", "converted", "lost")}
    done = by["attended"] + by["no_show"] + by["converted"] + by["lost"]
    return {**by, "total": len(rows), "trial_to_paid": round(100 * by["converted"] / done, 1) if done else 0.0,
            "show_rate": round(100 * (by["attended"] + by["converted"] + by["lost"]) / done, 1) if done else 0.0,
            "follow_ups_due": sum(1 for t in rows if t.follow_up_date and t.follow_up_date <= date.today() and t.status in ("attended", "no_show"))}


def trial_analytics(db: Session) -> dict:
    rows = db.query(Trial).all()

    def group(keyfn):
        g: dict[str, list[Trial]] = {}
        for t in rows:
            g.setdefault(keyfn(t) or "Unknown", []).append(t)
        out = []
        for k, ts in g.items():
            done = [t for t in ts if t.status in ("attended", "no_show", "converted", "lost")]
            conv = sum(1 for t in ts if t.status == "converted")
            out.append({"key": k, "trials": len(ts), "attended": sum(1 for t in ts if t.status in ("attended", "converted", "lost")),
                        "no_show": sum(1 for t in ts if t.status == "no_show"), "converted": conv,
                        "rate": round(100 * conv / len(done), 1) if done else 0.0})
        return sorted(out, key=lambda r: -r["trials"])

    return {"by_teacher": group(lambda t: t.teacher.full_name if t.teacher else None), "by_course": group(lambda t: t.course.name if t.course else None),
            "by_source": group(lambda t: t.lead.source.name if t.lead and t.lead.source else "Direct")}
