"""Parent communication and referral follow-up (docs/PARENT_COMMUNICATION.md).

Every conversation with a family, whatever it is about, is a ``ParentContact``: who spoke to them and how, what the
family said, how they felt, the issue it identified, what was agreed, and a recording or transcript where permitted.

* An issue or an agreed action becomes a follow-up task for the responsible person with a due date. The daily
  reminder job (``remind_follow_ups``) reminds them every day it is due or overdue until it is done; the existing
  overdue-task job escalates it.
* An issue can be raised as a complaint ticket in one step (app.services.complaints takes it from there).
* A family mentioning that they want to refer someone becomes a referral on the ambassador ledger: with contact
  details a lead is created, linked and assigned with a call task; without them a referral "ask" is recorded with a
  task to collect the details. When the referred family subscribes, ``referral_follow_ups`` marks the referral
  eligible and asks for the account credit to be approved. Credit is never posted automatically.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.core.audit import log_action
from app.core.notify import notify
from app.models.core import Role, User
from app.models.crm import (ISSUE_CATEGORIES, PARENT_CONTACT_CHANNELS, PARENT_CONTACT_PURPOSES, PARENT_SENTIMENTS, Case, LeadSource,
                            ParentContact, Referral)
from app.models.hr_erp import Attachment
from app.models.ops import Task
from app.models.people import Client, Student
from app.models.scheduling import ReminderLog

PURPOSE_LABELS = dict(PARENT_CONTACT_PURPOSES)
SENTIMENT_LABELS = dict(PARENT_SENTIMENTS)
ISSUE_LABELS = dict(ISSUE_CATEGORIES)
CHANNEL_LABELS = {"phone": "Phone call", "whatsapp": "WhatsApp", "video": "Video call", "meeting": "Meeting", "email": "Email",
                  "portal": "Parent portal"}
RELATIONS = [("family", "Family member"), ("friend", "Friend"), ("colleague", "Colleague"), ("community", "Community / masjid"),
             ("other", "Other")]
OPEN_TASK = ("todo", "in_progress", "review")
FOLLOW_UP_DAYS = 2
RECORDING_DIR = BASE_DIR / "storage" / "contact_recordings"
RECORDING_TYPES = (".mp3", ".m4a", ".wav", ".ogg", ".oga", ".webm", ".mp4", ".aac", ".pdf", ".txt", ".png", ".jpg", ".jpeg")
RECORDING_MAX_BYTES = 25 * 1024 * 1024


def org_today():
    from app.services.people import org_now
    return org_now().date()


def _role_users(db: Session, slug: str) -> list[User]:
    return (db.query(User).join(Role, Role.id == User.role_id)
            .filter(User.is_active.is_(True), Role.slug == slug).order_by(User.id).all())


def referral_owner(db: Session) -> Optional[User]:
    """Who looks after referrals: the Head of Admissions, else the marketing head, else a manager."""
    for slug in ("head_of_admissions", "hod_marketing", "manager"):
        people = _role_users(db, slug)
        if people:
            return people[0]
    return None


# ============================================================================ visibility
def visible(db: Session, user: User, query):
    """Contacts about a complaint follow the complaint's visibility (docs/COMPLAINTS.md); the rest are visible to
    holders of parent_contacts.view."""
    from app.services import complaints as cx
    clause = cx.visibility_clause(db, user)
    if clause is None:
        return query
    allowed_cases = db.query(Case.id).filter(clause)
    return query.filter(or_(ParentContact.case_id.is_(None), ParentContact.case_id.in_(allowed_cases)))


def can_view(db: Session, user: User, pc: ParentContact) -> bool:
    return visible(db, user, db.query(ParentContact.id).filter(ParentContact.id == pc.id)).first() is not None


# ============================================================================ recording a conversation
def _check(data: dict) -> None:
    if data.get("purpose") not in PURPOSE_LABELS:
        raise ValueError("Choose what the conversation was about.")
    if data.get("channel") not in PARENT_CONTACT_CHANNELS:
        raise ValueError("Choose how the family was contacted.")
    if not any((data.get(k) or "").strip() for k in ("summary", "parent_response")):
        raise ValueError("Write a short summary or what the family said.")
    if data.get("sentiment") and data["sentiment"] not in SENTIMENT_LABELS:
        raise ValueError("Unknown sentiment.")
    if data.get("issue_category") and data["issue_category"] not in ISSUE_LABELS:
        raise ValueError("Unknown issue category.")


def record(db: Session, client: Client, user: User, data: dict, student: Optional[Student] = None, request=None) -> ParentContact:
    """Record one conversation and create what it calls for: a follow-up task, a referral."""
    _check(data)
    if student is not None and student.client_id != client.id:
        raise ValueError("That student does not belong to this family.")
    issue = (data.get("issue") or "").strip() or None
    pc = ParentContact(client_id=client.id, student_id=student.id if student else None, purpose=data["purpose"], channel=data["channel"],
                       direction=data.get("direction") if data.get("direction") in ("inbound", "outbound") else "outbound",
                       contacted_at=data.get("contacted_at") or datetime.utcnow(), contacted_by_id=user.id,
                       duration_minutes=data.get("duration_minutes"), summary=(data.get("summary") or "").strip() or None,
                       parent_response=(data.get("parent_response") or "").strip() or None, sentiment=data.get("sentiment") or None,
                       issue=issue, issue_category=(data.get("issue_category") or ("other" if issue else None)),
                       agreed_action=(data.get("agreed_action") or "").strip() or None, follow_up_date=data.get("follow_up_date"),
                       responsible_id=data.get("responsible_id") or user.id, recording_url=(data.get("recording_url") or "").strip()[:500] or None,
                       transcript=(data.get("transcript") or "").strip() or None, referral_mentioned=bool(data.get("referral_mentioned")),
                       outcome="recorded")
    db.add(pc)
    db.flush()
    if pc.issue or pc.agreed_action:
        create_follow_up(db, pc, user)
    if pc.referral_mentioned:
        record_referral(db, pc, user, data.get("referred_name"), data.get("referred_phone"), data.get("referred_email"),
                        data.get("referred_relation"), data.get("referral_note"), request=request)
    log_action(db, user, "create", "parent_contacts", entity=pc, request=request,
               description=f"{CHANNEL_LABELS.get(pc.channel, pc.channel)} with {client.full_name} ({PURPOSE_LABELS[pc.purpose]})"
                           + (f"; issue: {pc.issue[:80]}" if pc.issue else ""),
               after={"purpose": pc.purpose, "issue": bool(pc.issue), "follow_up_task": pc.follow_up_task_id, "referral": pc.referral_id})
    from app.services import automation
    automation.emit(db, "parent.contacted", "client", client.id, {"contact_id": pc.id, "purpose": pc.purpose, "sentiment": pc.sentiment or "",
                                                                  "issue": bool(pc.issue), "referral": pc.referral_mentioned})
    return pc


def create_follow_up(db: Session, pc: ParentContact, user: Optional[User]) -> Task:
    """Issue → action → responsible person → due date. Reminded daily until done (``remind_follow_ups``)."""
    family = pc.client or db.get(Client, pc.client_id)
    what = pc.agreed_action or f"Resolve: {pc.issue}"
    due = pc.follow_up_date or (org_today() + timedelta(days=FOLLOW_UP_DAYS))
    urgent = pc.sentiment == "upset" or pc.issue_category in ("teacher", "behaviour")
    task = Task(title=f"Follow up with {family.full_name if family else 'the family'}: {what}"[:200],
                description=" ".join(x for x in [f"Issue: {pc.issue}." if pc.issue else "", f"Agreed: {pc.agreed_action}." if pc.agreed_action else "",
                                                 f"Family said: {pc.parent_response}" if pc.parent_response else ""] if x) or what,
                assignee_id=pc.responsible_id, creator_id=user.id if user else None, priority="high" if urgent else "medium",
                status="todo", due_date=due, entity_type="Student" if pc.student_id else "Client",
                entity_id=pc.student_id or pc.client_id)
    db.add(task)
    db.flush()
    pc.follow_up_task_id = task.id
    if pc.follow_up_date is None:
        pc.follow_up_date = due
    if pc.responsible_id and (user is None or pc.responsible_id != user.id):
        notify(db, pc.responsible_id, f"Follow-up assigned: {family.full_name if family else ''}", what[:200], event_type="task_assigned",
               link=f"/parent-contacts/{pc.id}")
    return task


def follow_up_state(pc: ParentContact) -> str:
    t = pc.follow_up_task
    if t is None:
        return "none"
    if t.status in ("done", "completed"):
        return "done"
    if t.status == "cancelled":
        return "cancelled"
    return "overdue" if t.due_date and t.due_date < org_today() else "open"


def complete_follow_up(db: Session, pc: ParentContact, user: User, note: str, request=None) -> None:
    note = (note or "").strip()
    if not note:
        raise ValueError("Write down what was done.")
    t = pc.follow_up_task
    if t is None or t.status not in OPEN_TASK:
        raise ValueError("There is no open follow-up on this conversation.")
    t.status, t.completed_at = "done", datetime.utcnow()
    t.description = ((t.description or "") + f"\n\nDone by {user.full_name} on {org_today():%d %b %Y}: {note}").strip()
    log_action(db, user, "update", "parent_contacts", entity=pc, request=request, description=f"Follow-up completed: {note[:120]}")


def raise_complaint(db: Session, pc: ParentContact, user: User, request=None) -> Case:
    """Turn the issue a conversation identified into a complaint ticket, with the family's words as the complaint."""
    if pc.case_id:
        raise ValueError("This conversation is already linked to a complaint.")
    from app.services import crm as crm_svc
    family = pc.client or db.get(Client, pc.client_id)
    student = pc.student or (db.get(Student, pc.student_id) if pc.student_id else None)
    title = (pc.issue or pc.summary or "Complaint raised in a conversation with the family")[:200]
    description = pc.parent_response or pc.summary or pc.issue
    case = crm_svc.open_case(db, "complaint", title, description, client=family, student=student, raised_by_user=user,
                             source="phone" if pc.channel == "phone" else ("whatsapp" if pc.channel == "whatsapp" else "staff"),
                             actor=user, request=request, incident_date=pc.contacted_at.date() if pc.contacted_at else None)
    pc.case_id = case.id
    log_action(db, user, "update", "parent_contacts", entity=pc, request=request, description=f"Raised as complaint {case.case_number}")
    return case


# ============================================================================ recordings
async def save_recording(db: Session, pc: ParentContact, user: User, upload) -> Attachment:
    name = getattr(upload, "filename", "") or ""
    suffix = Path(name).suffix.lower()
    if suffix not in RECORDING_TYPES:
        raise ValueError("Upload an audio or video recording (or a PDF / text transcript).")
    data = await upload.read()
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > RECORDING_MAX_BYTES:
        raise ValueError("Recordings must be 25 MB or smaller.")
    RECORDING_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{pc.id}-{datetime.utcnow():%Y%m%d%H%M%S%f}{suffix}"
    (RECORDING_DIR / stored).write_bytes(data)
    att = Attachment(entity_type="parent_contact", entity_id=pc.id, title=f"Recording {pc.contacted_at:%d %b %Y %H:%M}", file_name=name[:200],
                     file_path=f"contact_recordings/{stored}", content_type=(getattr(upload, "content_type", None) or "application/octet-stream")[:80],
                     size_bytes=len(data), uploaded_by_id=user.id, status="active")
    db.add(att)
    db.flush()
    pc.recording_attachment_id = att.id
    return att


def recording_path(att: Attachment) -> Optional[Path]:
    if not att.file_path or not att.file_path.startswith("contact_recordings/"):
        return None
    path = (RECORDING_DIR / att.file_path.split("/", 1)[1]).resolve()
    return path if path.parent == RECORDING_DIR.resolve() and path.exists() else None


# ============================================================================ referrals
def record_referral(db: Session, pc: ParentContact, user: Optional[User], name: Optional[str], phone: Optional[str], email: Optional[str],
                    relation: Optional[str], note: Optional[str], request=None) -> Referral:
    """A family said they want to refer someone. With a way to reach the person a lead is created on the referral
    ledger and given to a closer with a call task; without one a referral "ask" is recorded with a task to collect
    the details. Either way the referral cannot be lost."""
    from app.services import crm as crm_svc
    family = pc.client or db.get(Client, pc.client_id)
    crm_svc.ensure_referral_code(family)
    name, phone, email = (name or "").strip(), (phone or "").strip(), (email or "").strip()
    owner = referral_owner(db)
    relation_label = dict(RELATIONS).get(relation or "", "")
    context = " ".join(x for x in [f"Referred by {family.full_name} ({relation_label})." if relation_label else f"Referred by {family.full_name}.",
                                   (note or "").strip()] if x)
    if name and (phone or email):
        src = db.query(LeadSource).filter(LeadSource.name == "Referral").first()
        # A referred family has not asked to be messaged yet: no automatic WhatsApp until someone has spoken to them.
        lead, _dups = crm_svc.create_lead(db, {"full_name": name, "phone": phone or None, "whatsapp": phone or None, "email": email or None,
                                               "country": family.country, "referral_code": family.referral_code,
                                               "source_id": src.id if src else None, "notes": context, "whatsapp_opt_in": False},
                                          actor=user, request=request)
        ref = db.query(Referral).filter(Referral.referred_lead_id == lead.id).first() or crm_svc.link_referral(db, lead)
        if ref is None:  # the family's code could not be matched; record the referral directly
            ref = Referral(ambassador_client_id=family.id, referral_code=family.referral_code, referred_lead_id=lead.id, status="lead",
                           invited_at=datetime.utcnow())
            db.add(ref)
            db.flush()
        ref.referred_name, ref.referred_phone = name, phone or None
        ref.notes = context
        ref.owner_id = ref.owner_id or (owner.id if owner else None)
        caller = db.get(User, lead.assigned_to_id) if lead.assigned_to_id else owner
        task = Task(title=f"Call referred family: {name} (referred by {family.full_name})"[:200], description=context,
                    assignee_id=caller.id if caller else None, creator_id=user.id if user else None, priority="high", status="todo",
                    due_date=org_today() + timedelta(days=1), entity_type="Referral", entity_id=ref.id)
        db.add(task)
        db.flush()
        if caller:
            notify(db, caller, f"New referral to call: {name}", context[:200], event_type="referral", link=f"/crm/leads/{lead.id}")
    else:
        ref = Referral(ambassador_client_id=family.id, referral_code=family.referral_code, referred_name=name or None,
                       referred_phone=phone or None, status="ask", invited_at=datetime.utcnow(), owner_id=owner.id if owner else None,
                       notes=context)
        db.add(ref)
        db.flush()
        responsible = pc.responsible_id or (user.id if user else None)
        task = Task(title=f"Collect referral details from {family.full_name}"[:200],
                    description=f"{context} Ask for the referred person's name and phone or email, then add them as a lead.",
                    assignee_id=responsible, creator_id=user.id if user else None, priority="medium", status="todo",
                    due_date=org_today() + timedelta(days=2), entity_type="Referral", entity_id=ref.id)
        db.add(task)
        db.flush()
    pc.referral_id = ref.id
    log_action(db, user, "create", "referrals", entity=ref, request=request,
               description=f"Referral mentioned by {family.full_name}: {name or 'details to collect'} ({ref.status})")
    from app.services.integrations import emit_event
    emit_event(db, "referral.mentioned", {"referral_id": ref.id, "status": ref.status, "family": family.client_code})
    return ref


def referral_stage(ref: Referral) -> str:
    if ref.status == "credited":
        return "Credit applied"
    if ref.eligible_at and ref.status in ("signed_up", "qualified"):
        return "Eligible: credit to approve"
    return {"ask": "Mentioned: details to collect", "lead": "Lead created", "signed_up": "Signed up", "invited": "Invited",
            "qualified": "Qualified", "expired": "Expired"}.get(ref.status, ref.status.title())


# ============================================================================ jobs
def remind_follow_ups(db: Session) -> dict:
    """Remind the responsible person, once a day, of every follow-up that is due or overdue until it is done.
    Covers the follow-ups created by conversations, teacher recommendations and referrals."""
    today = org_today()
    start = datetime.combine(today, datetime.min.time())
    tasks = (db.query(Task).filter(Task.entity_type.in_(["Student", "Client", "Referral"]), Task.status.in_(OPEN_TASK),
                                   Task.due_date.isnot(None), Task.due_date <= today, Task.assignee_id.isnot(None)).all())
    reminded = 0
    for t in tasks:
        done_today = (db.query(ReminderLog.id).filter(ReminderLog.reminder_type == "follow_up_due", ReminderLog.entity_type == "Task",
                                                      ReminderLog.entity_id == t.id, ReminderLog.sent_at >= start).first())
        if done_today:
            continue
        late = (today - t.due_date).days
        notify(db, t.assignee_id, ("Follow-up due today" if late == 0 else f"Follow-up overdue by {late} day(s)") + f": {t.title[:120]}",
               t.description[:200] if t.description else "", event_type="follow_up_reminder", link=f"/tasks/{t.id}")
        db.add(ReminderLog(reminder_type="follow_up_due", user_id=t.assignee_id, entity_type="Task", entity_id=t.id, channel="in_app"))
        reminded += 1
    return {"reminded": reminded, "open_due": len(tasks)}


def referral_follow_ups(db: Session) -> dict:
    """Mark a referral eligible once the referred family has an active subscription, and ask for the account credit
    to be approved (it is never posted automatically). Remind the owner of referrals whose details were never
    collected, once a week."""
    from app.services import crm as crm_svc
    eligible = 0
    for ref in db.query(Referral).filter(Referral.status.in_(["signed_up", "qualified"]), Referral.eligible_at.is_(None)):
        if not crm_svc.referred_has_active_subscription(db, ref):
            continue
        ref.eligible_at = datetime.utcnow()
        owner = db.get(User, ref.owner_id) if ref.owner_id else referral_owner(db)
        who = ref.referred_client.full_name if ref.referred_client else (ref.referred_name or "the referred family")
        amb = ref.ambassador.full_name if ref.ambassador else "the family"
        db.add(Task(title=f"Approve referral credit: {amb} referred {who}"[:200],
                    description="The referred family has an active subscription. Approve the account credit for both families on CRM › Ambassadors.",
                    assignee_id=owner.id if owner else None, priority="medium", status="todo", due_date=org_today() + timedelta(days=3),
                    entity_type="Referral", entity_id=ref.id))
        if owner:
            notify(db, owner, "Referral reward due", f"{amb} referred {who}, who has subscribed. Approve the credit.", event_type="referral",
                   link="/crm/referrals?status=signed_up")
        eligible += 1
    week_ago = datetime.utcnow() - timedelta(days=7)
    stale = 0
    for ref in db.query(Referral).filter(Referral.status == "ask", Referral.created_at <= week_ago, Referral.owner_id.isnot(None)):
        sent = (db.query(ReminderLog.id).filter(ReminderLog.reminder_type == "referral_stale", ReminderLog.entity_type == "Referral",
                                                ReminderLog.entity_id == ref.id, ReminderLog.sent_at >= week_ago).first())
        if sent:
            continue
        notify(db, ref.owner_id, "Referral details still missing", f"{ref.ambassador.full_name if ref.ambassador else 'A family'} mentioned a referral "
               f"{(datetime.utcnow() - ref.created_at).days} days ago.", event_type="referral", link="/crm/referrals?status=ask")
        db.add(ReminderLog(reminder_type="referral_stale", user_id=ref.owner_id, entity_type="Referral", entity_id=ref.id, channel="in_app"))
        stale += 1
    return {"eligible": eligible, "stale_reminders": stale}


def stats(db: Session, user: User) -> dict:
    today = org_today()
    month_start = datetime.combine(today.replace(day=1), datetime.min.time())
    base = visible(db, user, db.query(ParentContact))
    open_tasks = (db.query(Task).join(ParentContact, ParentContact.follow_up_task_id == Task.id)
                  .filter(Task.status.in_(OPEN_TASK)))
    return {"this_month": base.filter(ParentContact.contacted_at >= month_start).count(),
            "issues": base.filter(ParentContact.issue.isnot(None), ParentContact.contacted_at >= month_start).count(),
            "open": open_tasks.count(), "overdue": open_tasks.filter(Task.due_date < today).count(),
            "referrals": base.filter(ParentContact.referral_mentioned.is_(True), ParentContact.contacted_at >= month_start).count(),
            "mine": open_tasks.filter(Task.assignee_id == user.id).count()}


def family_contacts(db: Session, user: User, client_id: int, limit: int = 100) -> list[ParentContact]:
    return (visible(db, user, db.query(ParentContact).filter(ParentContact.client_id == client_id))
            .order_by(ParentContact.contacted_at.desc()).limit(limit).all())


def count_by_client(db: Session, ids) -> dict:
    return dict(db.query(ParentContact.client_id, func.count(ParentContact.id)).filter(ParentContact.client_id.in_(list(ids) or [-1]))
                .group_by(ParentContact.client_id).all())
