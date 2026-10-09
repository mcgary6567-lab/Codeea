"""The CRM automation engine: events in, workflow runs out.

    emit(db, "lead.created", "lead", lead.id, {...})   # from the code that did the thing
    advance_due_runs(db)                                # every five minutes from the scheduler

A workflow is a trigger plus an ordered list of steps (see app.models.automation.STEP_KINDS). A run walks
the steps for one contact: sends go out at once, a wait parks the run until its time, a condition can
skip ahead or stop, and every step writes a line to the run's log so staff can see what happened and why.

Sends respect consent: WhatsApp needs the contact's opt-in, email needs an address and no "unsubscribed"
tag, SMS needs a provider (none is configured, so an SMS step is recorded as skipped rather than silently
dropped). Nothing here sends twice: a workflow marked run-once never starts a second run for a contact.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.automation import (STEP_KINDS, WORKFLOW_TRIGGERS, AutomationEvent, ContactTag, Tag, Workflow, WorkflowRun)
from app.models.core import User
from app.models.crm import LEAD_STAGES, Conversation, Lead, Message
from app.models.people import Client, Student

log = logging.getLogger("oqc.automation")

STALE_LEAD_DAYS = 7
INACTIVE_STUDENT_DAYS = 14


# ----------------------------------------------------------------------------- contacts
def _org_today() -> date:
    """The college's calendar day (Asia/Karachi), not the server's: due dates and {{date}} follow the college."""
    from app.services.people import org_now
    return org_now().date()


def resolve_contact(db: Session, contact_type: str, contact_id: int):
    model = {"lead": Lead, "client": Client, "student": Student}.get(contact_type)
    return db.get(model, contact_id) if model else None


def messaging_target(db: Session, contact_type: str, contact):
    """The record messages go to: a lead or a client (a student's messages go to the family)."""
    if contact_type == "student":
        return ("client", contact.client) if contact.client else (None, None)
    return contact_type, contact


def contact_label(contact_type: str, contact) -> str:
    code = getattr(contact, "lead_code", None) or getattr(contact, "client_code", None) or getattr(contact, "student_code", None) or ""
    return f"{code} {getattr(contact, 'full_name', '')}".strip()


REVIEW_LINK_FALLBACK = "reply to this message and we will send you the link"


def setting(db: Session, key: str, default=None):
    """A Setting row's scalar value ({"value": X} unwrapped), or ``default`` when the key is absent or empty."""
    from app.models.core import Setting
    s = db.query(Setting).filter(Setting.key == key).first()
    if not s or s.value is None:
        return default
    v = s.value
    if isinstance(v, dict) and set(v.keys()) == {"value"}:
        v = v["value"]
    return default if v in ("", None) else v


def render_context(db: Session, contact_type: str, contact, payload: Optional[dict] = None) -> dict:
    """Placeholders available to message bodies: {{name}}, {{student}}, {{teacher}}, {{course}}, {{link}},
    {{review_link}} (the ``review_link`` setting from Configuration -> Setup, or a "reply for the link" line while it
    is empty), {{amount}}, {{date}}."""
    payload = payload or {}
    first = (getattr(contact, "full_name", "") or "").split()[0] if getattr(contact, "full_name", None) else "there"
    student = ""
    teacher = "your teacher"
    course = ""
    if contact_type == "lead":
        student = contact.student_name or "your child"
        course = contact.course_interest.name if getattr(contact, "course_interest", None) else ""
    elif contact_type == "client":
        if contact.students:
            s = contact.students[0]
            student = s.full_name
            teacher = s.teacher.full_name if getattr(s, "teacher", None) else teacher
            course = s.course.name if getattr(s, "course", None) else ""
    elif contact_type == "student":
        student = contact.full_name
        first = (contact.client.full_name.split()[0] if contact.client and contact.client.full_name else first)
        teacher = contact.teacher.full_name if getattr(contact, "teacher", None) else teacher
        course = contact.course.name if getattr(contact, "course", None) else ""
    review_link = str(setting(db, "review_link", "") or "").strip()
    ctx = {"name": first, "student": student, "teacher": teacher, "course": course, "college": "Online Quran College",
           "link": settings.BASE_URL + "/portal", "review_link": review_link or REVIEW_LINK_FALLBACK,
           "amount": payload.get("amount", ""), "date": payload.get("date", _org_today().strftime("%d %b %Y"))}
    ctx.update({k: v for k, v in payload.items() if isinstance(v, (str, int, float))})
    return ctx


# ----------------------------------------------------------------------------- tags
def get_tag(db: Session, name: str, create: bool = True, category: str = "other") -> Optional[Tag]:
    name = (name or "").strip()
    if not name:
        return None
    t = db.query(Tag).filter(func.lower(Tag.name) == name.lower()).first()
    if not t and create:
        t = Tag(name=name, category=category)
        db.add(t)
        db.flush()
    return t


def tags_for(db: Session, contact_type: str, contact_id: int) -> list[Tag]:
    rows = (db.query(Tag).join(ContactTag, ContactTag.tag_id == Tag.id)
            .filter(ContactTag.contact_type == contact_type, ContactTag.contact_id == contact_id).order_by(Tag.category, Tag.name).all())
    return rows


def has_tag(db: Session, contact_type: str, contact_id: int, name: str) -> bool:
    return any(t.name.lower() == (name or "").lower() for t in tags_for(db, contact_type, contact_id))


def add_tag(db: Session, contact_type: str, contact_id: int, name: str, added_by: str = "system", emit_event: bool = True) -> bool:
    """Returns True when the tag was newly added."""
    t = get_tag(db, name)
    if not t:
        return False
    exists = db.query(ContactTag).filter(ContactTag.contact_type == contact_type, ContactTag.contact_id == contact_id, ContactTag.tag_id == t.id).first()
    if exists:
        return False
    db.add(ContactTag(contact_type=contact_type, contact_id=contact_id, tag_id=t.id, added_by=added_by[:40]))
    db.flush()
    if emit_event:
        emit(db, "tag.added", contact_type, contact_id, {"tag": t.name, "category": t.category})
    return True


def remove_tag(db: Session, contact_type: str, contact_id: int, name: str) -> bool:
    t = get_tag(db, name, create=False)
    if not t:
        return False
    n = db.query(ContactTag).filter(ContactTag.contact_type == contact_type, ContactTag.contact_id == contact_id, ContactTag.tag_id == t.id).delete()
    if n:
        _to_ghl(db, "tag.removed", contact_type, contact_id, {"tag": t.name})
    return bool(n)


def _to_ghl(db: Session, event: str, contact_type: str, contact_id: int, payload: dict) -> None:
    """Queue the matching GoHighLevel changes (app.services.ghl_sync). Never lets a sync problem break the business
    action that raised the event."""
    try:
        from app.services import ghl_sync
        with db.begin_nested():
            ghl_sync.on_event(db, event, contact_type, contact_id, payload)
    except Exception:  # pragma: no cover - logged, the event itself still stands
        import logging
        logging.getLogger("oqc.ghl").exception("could not queue GHL sync for %s", event)


def contacts_with_tag(db: Session, tag: Tag) -> list[tuple[str, Any]]:
    out = []
    for ct in db.query(ContactTag).filter(ContactTag.tag_id == tag.id).order_by(ContactTag.created_at.desc()).limit(500):
        c = resolve_contact(db, ct.contact_type, ct.contact_id)
        if c:
            out.append((ct.contact_type, c))
    return out


# ----------------------------------------------------------------------------- events -> runs
def _filter_matches(wf: Workflow, payload: dict, contact_type: str, contact) -> bool:
    f = wf.trigger_filter or {}
    if not f:
        return True
    if "stage" in f and payload.get("to", payload.get("stage")) != f["stage"]:
        return False
    if "stages" in f and payload.get("to", payload.get("stage")) not in (f["stages"] or []):
        return False
    if "tag" in f and (payload.get("tag") or "").lower() != str(f["tag"]).lower():
        return False
    if "nps_min" in f and (payload.get("nps") is None or int(payload["nps"]) < int(f["nps_min"])):
        return False
    if "nps_max" in f and (payload.get("nps") is None or int(payload["nps"]) > int(f["nps_max"])):
        return False
    if "level" in f and payload.get("level") != f["level"]:
        return False
    if "country" in f and (getattr(contact, "country", None) or "") != f["country"]:
        return False
    if "first" in f and bool(payload.get("first")) != bool(f["first"]):
        return False
    return True


def emit(db: Session, event: str, contact_type: str, contact_id: int, payload: Optional[dict] = None, now: Optional[datetime] = None) -> list[WorkflowRun]:
    """Record the event and start a run of every active workflow whose trigger and filter match."""
    now = now or datetime.utcnow()
    payload = dict(payload or {})
    ev = AutomationEvent(event=event, contact_type=contact_type, contact_id=contact_id, payload=payload, created_at=now)
    db.add(ev)
    _to_ghl(db, event, contact_type, contact_id, payload)
    contact = resolve_contact(db, contact_type, contact_id)
    started: list[WorkflowRun] = []
    if not contact:
        db.flush()
        return started
    for wf in db.query(Workflow).filter(Workflow.is_active.is_(True), Workflow.trigger == event).order_by(Workflow.sort_no, Workflow.id).all():
        expected = WORKFLOW_TRIGGERS.get(event, ("", "any"))[1]
        if expected not in ("any", contact_type):
            continue
        if not _filter_matches(wf, payload, contact_type, contact):
            continue
        if wf.run_once_per_contact:
            dup = db.query(WorkflowRun).filter(WorkflowRun.workflow_id == wf.id, WorkflowRun.contact_type == contact_type,
                                               WorkflowRun.contact_id == contact_id).first()
            if dup:
                continue
        else:
            live = db.query(WorkflowRun).filter(WorkflowRun.workflow_id == wf.id, WorkflowRun.contact_type == contact_type,
                                                WorkflowRun.contact_id == contact_id, WorkflowRun.status.in_(["active", "waiting"])).first()
            if live:
                continue
        run = WorkflowRun(workflow_id=wf.id, contact_type=contact_type, contact_id=contact_id, trigger_event=event, context=payload,
                          current_step=0, next_run_at=now, status="active", started_at=now)
        db.add(run)
        db.flush()
        advance_run(db, run, now)
        started.append(run)
    ev.runs_started = len(started)
    db.flush()
    return started


def start_manually(db: Session, wf: Workflow, contact_type: str, contact_id: int, user: Optional[User], now: Optional[datetime] = None) -> WorkflowRun:
    now = now or datetime.utcnow()
    run = WorkflowRun(workflow_id=wf.id, contact_type=contact_type, contact_id=contact_id, trigger_event="manual",
                      context={"started_by": user.email if user else "system"}, current_step=0, next_run_at=now, status="active", started_at=now)
    db.add(run)
    db.flush()
    advance_run(db, run, now)
    return run


# ----------------------------------------------------------------------------- running
def _log(run: WorkflowRun, step_no: int, kind: str, result: str, at: datetime) -> None:
    entries = list(run.log or [])
    entries.append({"step": step_no, "kind": kind, "at": at.isoformat(timespec="seconds"), "result": result[:300]})
    run.log = entries[-200:]


def _finish(run: WorkflowRun, status: str, reason: Optional[str], at: datetime) -> None:
    run.status = status
    run.stop_reason = (reason or "")[:200] or None
    run.next_run_at = None
    run.finished_at = at


def _should_exit(db: Session, run: WorkflowRun, wf: Workflow, contact_type: str, contact) -> Optional[str]:
    ex = wf.exit_on or {}
    if contact_type == "lead" and contact.stage in (ex.get("lead_stages") or []):
        return f"Lead is {contact.stage}"
    if contact_type == "client" and getattr(contact, "status", None) in (ex.get("client_statuses") or []):
        return f"Client is {contact.status}"
    if contact_type == "student" and getattr(contact, "status", None) in (ex.get("student_statuses") or []):
        return f"Student is {contact.status}"
    if ex.get("reply"):
        mtype, target = messaging_target(db, contact_type, contact)
        if target is not None:
            conv = db.query(Conversation).filter(Conversation.lead_id == target.id if mtype == "lead" else Conversation.client_id == target.id).first()
            if conv:
                replied = (db.query(Message).filter(Message.conversation_id == conv.id, Message.direction == "in",
                                                    Message.created_at > run.started_at).first())
                if replied:
                    return "Contact replied"
    for tag in ex.get("tags") or []:
        if has_tag(db, contact_type, contact.id, tag):
            return f"Tagged {tag}"
    return None


def _field_value(db: Session, contact_type: str, contact, field: str, run: WorkflowRun):
    """A dotted field on the contact, its tags, or the run context: stage, status, country, tags.<name>, ctx.<key>."""
    if field.startswith("tags."):
        return has_tag(db, contact_type, contact.id, field[5:])
    if field.startswith("ctx."):
        return (run.context or {}).get(field[4:])
    if field == "has_phone":
        return bool(getattr(contact, "phone", None) or getattr(contact, "whatsapp", None))
    if field == "has_email":
        return bool(getattr(contact, "email", None))
    if field == "trial_attended" and contact_type == "lead":
        from app.models.scheduling import Trial
        return db.query(Trial).filter(Trial.lead_id == contact.id, Trial.status.in_(["attended", "converted"])).first() is not None
    if field == "has_payment" and contact_type == "client":
        from app.models.finance import Payment
        return db.query(Payment).filter(Payment.client_id == contact.id, Payment.status.in_(["confirmed", "completed"])).first() is not None
    if field == "active_subscription":
        from app.models.finance import Subscription
        q = db.query(Subscription).filter(Subscription.status.in_(["active", "regular", "trial"]))
        q = q.filter(Subscription.client_id == contact.id) if contact_type == "client" else q.filter(Subscription.student_id == contact.id)
        return q.first() is not None
    v = getattr(contact, field, None)
    return v


def _condition_holds(db: Session, contact_type: str, contact, step: dict, run: WorkflowRun) -> bool:
    actual = _field_value(db, contact_type, contact, step.get("field", ""), run)
    op = step.get("op", "eq")
    expected = step.get("value")
    if op == "eq":
        return str(actual).lower() == str(expected).lower()
    if op == "ne":
        return str(actual).lower() != str(expected).lower()
    if op == "in":
        return str(actual).lower() in [str(x).lower() for x in (expected if isinstance(expected, list) else str(expected).split(","))]
    if op == "truthy":
        return bool(actual)
    if op == "falsy":
        return not actual
    if op in ("gte", "lte"):
        try:
            a, b = float(actual), float(expected)
        except (TypeError, ValueError):
            return False
        return a >= b if op == "gte" else a <= b
    return False


def _wait_until_at(db: Session, contact_type: str, contact, field: str) -> Optional[datetime]:
    """A moment on the contact's live record: trial_at (the booked demo), freeze_end, next_billing_date, follow_up,
    or any date/datetime column. Dates become 09:00 on that day."""
    if field == "trial_at":
        from app.models.scheduling import Trial
        q = db.query(Trial).filter(Trial.scheduled_at.isnot(None))
        q = q.filter(Trial.lead_id == contact.id) if contact_type == "lead" else q.filter(Trial.client_id == contact.id)
        t = q.order_by(Trial.scheduled_at.desc()).first()
        return t.scheduled_at if t else None
    d = _wait_until_date(contact_type, contact, field)
    if isinstance(d, datetime):
        return d
    return datetime.combine(d, datetime.min.time()) + timedelta(hours=9) if d else None


def _wait_until_date(contact_type: str, contact, field: str) -> Optional[date]:
    """A date on the contact's live record: freeze_end, leave_end, next_billing_date, follow_up."""
    if contact_type == "client":
        subs = [s for s in contact.subscriptions] if hasattr(contact, "subscriptions") else []
        if field == "freeze_end":
            ends = [s.freeze_end for s in subs if s.status in ("frozen", "freeze") and s.freeze_end]
            return min(ends) if ends else None
        if field == "next_billing_date":
            ds = [s.next_billing_date for s in subs if s.next_billing_date]
            return min(ds) if ds else None
    if contact_type == "lead" and field == "follow_up":
        return contact.next_follow_up.date() if contact.next_follow_up else None
    v = getattr(contact, field, None)
    return v if isinstance(v, (date, datetime)) else None


def _send_whatsapp(db: Session, contact_type: str, contact, step: dict, ctx: dict, wf: Workflow) -> str:
    from app.services import crm
    mtype, target = messaging_target(db, contact_type, contact)
    if target is None:
        return "skipped: no family record to message"
    if not getattr(target, "whatsapp_opt_in", True):
        return "skipped: contact has not opted in to WhatsApp"
    if not (getattr(target, "whatsapp", None) or getattr(target, "phone", None)):
        return "skipped: no phone number"
    conv = crm.get_or_create_conversation(db, mtype, target)
    body = step.get("body") or crm.template_body(db, step.get("template") or "", "")
    if not body:
        return "skipped: empty message"
    msg = crm.send_message(db, conv, crm.render_template_body(body, ctx), None, template_name=step.get("template"),
                           message_type="template" if step.get("template") else "text")
    return f"whatsapp {msg.status}" + (f": {msg.error}" if msg.error else "")


def _send_email(db: Session, contact_type: str, contact, step: dict, ctx: dict) -> str:
    from app.services import crm
    from app.services.integrations import send_email
    mtype, target = messaging_target(db, contact_type, contact)
    if target is None:
        return "skipped: no family record to message"
    email = getattr(target, "email", None)
    if not email:
        return "skipped: no email address"
    if has_tag(db, mtype, target.id, "unsubscribed"):
        return "skipped: contact unsubscribed from email"
    subject = crm.render_template_body(step.get("subject") or "Online Quran College", ctx)
    body = crm.render_template_body(step.get("body") or "", ctx)
    res = send_email(db, email, subject, body)
    return "email sent" + (" (simulated: no SMTP configured)" if res.get("simulated") else "")


def _send_sms(db: Session, contact_type: str, contact, step: dict, ctx: dict) -> str:
    mtype, target = messaging_target(db, contact_type, contact)
    if target is None:
        return "skipped: no family record to message"
    if not has_tag(db, mtype, target.id, "consent:sms"):
        return "skipped: no SMS consent tag"
    if not setting(db, "sms_provider", ""):
        return "skipped: no SMS provider configured (WhatsApp and email are live)"
    return "sms queued"


def _staff_recipients(db: Session, contact_type: str, contact, to: str) -> list[User]:
    from app.services import crm
    to = (to or "assigned").strip()
    if to == "assigned":
        uid = getattr(contact, "assigned_to_id", None)
        if contact_type == "student" and contact.client:
            uid = getattr(contact.client, "billing_rep_id", None) or uid
        if contact_type == "client":
            uid = getattr(contact, "billing_rep_id", None) or getattr(contact, "academic_manager_id", None)
        u = db.get(User, uid) if uid else None
        return [u] if u else [u for u in crm.closers(db)][:1]
    if to == "teacher":
        t = getattr(contact, "teacher", None)
        if t is None and contact_type == "client" and contact.students:
            t = getattr(contact.students[0], "teacher", None)
        return [t.user] if t is not None and getattr(t, "user", None) else []
    if to.startswith("role:"):
        return crm.users_with_role(db, to[5:])
    if to.startswith("user:"):
        u = crm.user_by_email(db, to[5:])
        return [u] if u else []
    if to.startswith("hod:"):
        u = crm.hod_for(db, to[4:])
        return [u] if u else []
    return []


def _notify_staff(db: Session, contact_type: str, contact, step: dict, ctx: dict, wf: Workflow) -> str:
    from app.core.notify import notify
    users = _staff_recipients(db, contact_type, contact, step.get("to", "assigned"))
    if not users:
        return "skipped: nobody to notify"
    from app.services import crm
    link = {"lead": f"/crm/leads/{contact.id}", "client": f"/clients/{contact.id}", "student": f"/students/{contact.id}"}[contact_type]
    title = crm.render_template_body(step.get("title") or wf.name, ctx)
    body = crm.render_template_body(step.get("body") or f"{contact_label(contact_type, contact)}", ctx)
    for u in users:
        notify(db, u, title, body, event_type="automation", link=link)
    return f"notified {', '.join(u.full_name for u in users)}"


def _create_task(db: Session, contact_type: str, contact, step: dict, ctx: dict, wf: Workflow) -> str:
    from app.models.ops import Task
    from app.services import crm
    users = _staff_recipients(db, contact_type, contact, step.get("to", "assigned"))
    assignee = users[0] if users else None
    title = crm.render_template_body(step.get("title") or wf.name, ctx)
    due = _org_today() + timedelta(days=int(step.get("due_days") or 1))
    t = Task(title=f"{title}: {contact_label(contact_type, contact)}"[:200], description=step.get("body") or f"Created by workflow {wf.code} {wf.name}.",
             assignee_id=assignee.id if assignee else None, priority=step.get("priority") or "medium", status="todo", due_date=due,
             entity_type=contact_type, entity_id=contact.id)
    db.add(t)
    db.flush()
    return f"task #{t.id} for {assignee.full_name if assignee else 'unassigned'}"


def _move_stage(db: Session, contact_type: str, contact, step: dict, wf: Workflow) -> str:
    if contact_type != "lead":
        return "skipped: not a lead"
    from app.services import crm
    stage = step.get("stage")
    if stage not in LEAD_STAGES:
        return f"skipped: unknown stage {stage}"
    if contact.stage == stage:
        return f"already {stage}"
    if contact.stage in ("won",):
        return "skipped: lead already won"
    crm.move_stage(db, contact, stage, None, step.get("reason") or f"Workflow {wf.code}")
    return f"moved to {stage}"


def execute_step(db: Session, run: WorkflowRun, wf: Workflow, step: dict, contact_type: str, contact, now: datetime) -> tuple[str, Optional[int]]:
    """Run one step. Returns (log line, next step index or None to use step+1); may set run.next_run_at to park."""
    kind = step.get("kind")
    ctx = render_context(db, contact_type, contact, run.context)
    if kind == "wait":
        delta = timedelta(hours=float(step.get("hours") or 0), days=float(step.get("days") or 0))
        run.next_run_at = now + delta
        run.status = "waiting"
        return f"waiting {delta}", None
    if kind == "wait_until":
        at = _wait_until_at(db, contact_type, contact, step.get("field") or "")
        if not at:
            return f"skipped: no {step.get('field')} date on the record", None
        when = at - timedelta(hours=float(step.get("hours_before") or 0))
        if when <= now:
            return f"{step.get('field')} already reached", None
        run.next_run_at = when
        run.status = "waiting"
        return f"waiting until {when:%d %b %Y %H:%M}", None
    if kind == "send_whatsapp":
        return _send_whatsapp(db, contact_type, contact, step, ctx, wf), None
    if kind == "send_email":
        return _send_email(db, contact_type, contact, step, ctx), None
    if kind == "send_sms":
        return _send_sms(db, contact_type, contact, step, ctx), None
    if kind == "notify_staff":
        return _notify_staff(db, contact_type, contact, step, ctx, wf), None
    if kind == "add_tag":
        added = add_tag(db, contact_type, contact.id, step.get("tag", ""), added_by=f"workflow:{wf.code}")
        return f"tag {step.get('tag')} {'added' if added else 'already present'}", None
    if kind == "remove_tag":
        removed = remove_tag(db, contact_type, contact.id, step.get("tag", ""))
        return f"tag {step.get('tag')} {'removed' if removed else 'not present'}", None
    if kind == "move_stage":
        return _move_stage(db, contact_type, contact, step, wf), None
    if kind == "create_task":
        return _create_task(db, contact_type, contact, step, ctx, wf), None
    if kind == "enroll_sequence":
        from app.services import crm
        mtype, target = messaging_target(db, contact_type, contact)
        if target is None:
            return "skipped: no family record", None
        e = crm.enroll_sequence(db, step.get("sequence_type", ""), mtype, target.id, enrolled_by=f"workflow:{wf.code}")
        return f"sequence {step.get('sequence_type')} {'enrolled' if e else 'not found'}", None
    if kind == "condition":
        holds = _condition_holds(db, contact_type, contact, step, run)
        branch = step.get("then") if holds else step.get("else")
        label = f"{step.get('field')} {step.get('op', 'eq')} {step.get('value')} -> {'yes' if holds else 'no'}"
        if branch in (None, "", "continue"):
            return label + ", continue", None
        if branch == "exit":
            _finish(run, "completed", f"Condition: {label}", now)
            return label + ", stop", -1
        if isinstance(branch, str) and branch.startswith("goto:"):
            return label + f", {branch}", int(branch[5:])
        if isinstance(branch, int):
            return label + f", goto {branch}", branch
        return label, None
    if kind == "webhook":
        from app.services.integrations import emit_event
        emit_event(db, step.get("event") or f"workflow.{wf.code}", {"contact_type": contact_type, "contact_id": contact.id, "workflow": wf.code, **(run.context or {})})
        return f"webhook {step.get('event')}", None
    if kind == "exit":
        _finish(run, "completed", step.get("reason") or "Exit step", now)
        return "exit", -1
    return f"unknown step kind {kind}", None


def advance_run(db: Session, run: WorkflowRun, now: Optional[datetime] = None, max_steps: int = 50, force: bool = False) -> WorkflowRun:
    """Execute steps until the run waits, finishes, or exits. Safe to call repeatedly: a run whose wait has not
    elapsed only has its exit rules checked, unless ``force`` (the Advance now button) is set."""
    now = now or datetime.utcnow()
    wf = run.workflow or db.get(Workflow, run.workflow_id)
    if run.status not in ("active", "waiting"):
        return run
    if not wf.is_active:
        _finish(run, "stopped", "Workflow switched off", now)
        return run
    contact = resolve_contact(db, run.contact_type, run.contact_id)
    if not contact:
        _finish(run, "stopped", "Contact no longer exists", now)
        return run
    try:
        reason = _should_exit(db, run, wf, run.contact_type, contact)
    except Exception as exc:  # a bad exit rule must not crash the request that emitted the event
        log.exception("workflow %s exit check failed", wf.code)
        _log(run, run.current_step, "exit", f"error: {exc}", now)
        _finish(run, "failed", str(exc), now)
        db.flush()
        return run
    if reason:
        _log(run, run.current_step, "exit", reason, now)
        _finish(run, "stopped", reason, now)
        return run
    if not force and run.status == "waiting" and run.next_run_at and run.next_run_at > now:
        return run
    steps = wf.steps or []
    run.status = "active"
    guard = 0
    while run.current_step < len(steps) and guard < max_steps:
        guard += 1
        step = steps[run.current_step]
        try:
            result, nxt = execute_step(db, run, wf, step, run.contact_type, contact, now)
        except Exception as exc:  # a broken step must not kill the scheduler or the request that emitted the event
            log.exception("workflow %s step %s failed", wf.code, run.current_step)
            _log(run, run.current_step, step.get("kind", "?"), f"error: {exc}", now)
            _finish(run, "failed", str(exc), now)
            return run
        _log(run, run.current_step, step.get("kind", "?"), result, now)
        if nxt == -1:  # finished by the step itself
            return run
        run.current_step = nxt if nxt is not None else run.current_step + 1
        if run.status == "waiting":
            return run
    if run.current_step >= len(steps):
        _finish(run, "completed", None, now)
    db.flush()
    return run


def advance_due_runs(db: Session, now: Optional[datetime] = None, limit: int = 300) -> dict:
    now = now or datetime.utcnow()
    due = (db.query(WorkflowRun).filter(WorkflowRun.status.in_(["active", "waiting"]), WorkflowRun.next_run_at.isnot(None),
                                        WorkflowRun.next_run_at <= now).order_by(WorkflowRun.next_run_at).limit(limit).all())
    counts = {"advanced": 0, "completed": 0, "stopped": 0, "failed": 0, "waiting": 0, "active": 0}
    for run in due:
        advance_run(db, run, now)
        counts["advanced"] += 1
        counts[run.status] = counts.get(run.status, 0) + 1
    return counts


# ----------------------------------------------------------------------------- daily detectors (events the data produces on its own)
def detect_stale_leads(db: Session, now: Optional[datetime] = None, days: int = STALE_LEAD_DAYS) -> int:
    """Leads still open with no activity for N days: emit lead.stale once per lead."""
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=days)
    n = 0
    for lead in db.query(Lead).filter(Lead.stage.notin_(["won", "lost"]), Lead.created_at <= cutoff).all():
        last = lead.last_contacted_at or lead.created_at
        if last and last > cutoff:
            continue
        already = db.query(AutomationEvent).filter(AutomationEvent.event == "lead.stale", AutomationEvent.contact_type == "lead",
                                                   AutomationEvent.contact_id == lead.id).first()
        if already:
            continue
        emit(db, "lead.stale", "lead", lead.id, {"days": days, "stage": lead.stage}, now)
        n += 1
    return n


def detect_inactive_students(db: Session, now: Optional[datetime] = None, days: int = INACTIVE_STUDENT_DAYS) -> int:
    """Active students with no attended class in N days: emit student.inactive, at most once every 30 days."""
    from app.models.scheduling import ClassSession
    now = now or datetime.utcnow()
    cutoff = (now - timedelta(days=days)).date()
    n = 0
    for s in db.query(Student).filter(Student.status.in_(["active", "regular"])).all():
        if (s.created_at or now) > now - timedelta(days=days):
            continue
        recent = (db.query(ClassSession).filter(ClassSession.student_id == s.id, ClassSession.status == "done", ClassSession.date >= cutoff).first())
        if recent:
            continue
        last = (db.query(AutomationEvent).filter(AutomationEvent.event == "student.inactive", AutomationEvent.contact_type == "student",
                                                 AutomationEvent.contact_id == s.id).order_by(AutomationEvent.created_at.desc()).first())
        if last and last.created_at > now - timedelta(days=30):
            continue
        emit(db, "student.inactive", "student", s.id, {"days": days}, now)
        n += 1
    return n


# ----------------------------------------------------------------------------- validation (used by the editor and the seed)
def validate_steps(steps: list) -> list[str]:
    errors = []
    if not isinstance(steps, list):
        return ["Steps must be a list"]
    for i, st in enumerate(steps):
        if not isinstance(st, dict) or st.get("kind") not in STEP_KINDS:
            errors.append(f"Step {i + 1}: unknown kind {st.get('kind') if isinstance(st, dict) else st!r}")
            continue
        k = st["kind"]
        if k == "wait" and not (st.get("hours") or st.get("days")):
            errors.append(f"Step {i + 1}: wait needs hours or days")
        if k in ("send_whatsapp",) and not (st.get("template") or st.get("body")):
            errors.append(f"Step {i + 1}: send_whatsapp needs a template or a body")
        if k == "send_email" and not st.get("body"):
            errors.append(f"Step {i + 1}: send_email needs a body")
        if k in ("add_tag", "remove_tag") and not st.get("tag"):
            errors.append(f"Step {i + 1}: {k} needs a tag")
        if k == "move_stage" and st.get("stage") not in LEAD_STAGES:
            errors.append(f"Step {i + 1}: stage must be one of {', '.join(LEAD_STAGES)}")
        if k == "condition" and not st.get("field"):
            errors.append(f"Step {i + 1}: condition needs a field")
        for key in ("then", "else"):
            v = st.get(key)
            if isinstance(v, bool):
                errors.append(f"Step {i + 1}: {key} must be continue, exit or goto:N")
                continue
            if isinstance(v, int) or (isinstance(v, str) and v.startswith("goto:")):
                try:
                    j = int(v[5:]) if isinstance(v, str) else v
                except ValueError:
                    errors.append(f"Step {i + 1}: {key} goto must be a step number")
                    continue
                if not (0 <= j < len(steps)):
                    errors.append(f"Step {i + 1}: {key} goto {j} is outside the steps")
                elif j <= i:
                    errors.append(f"Step {i + 1}: {key} goto {j} must jump forward, not back")
    return errors


def workflow_stats(db: Session) -> dict:
    today = datetime.utcnow().date()
    base = db.query(WorkflowRun)
    return {
        "workflows": db.query(func.count(Workflow.id)).scalar() or 0,
        "active_workflows": db.query(func.count(Workflow.id)).filter(Workflow.is_active.is_(True)).scalar() or 0,
        "waiting": base.filter(WorkflowRun.status.in_(["active", "waiting"])).count(),
        "today": base.filter(WorkflowRun.started_at >= datetime.combine(today, datetime.min.time())).count(),
        "completed": base.filter(WorkflowRun.status == "completed").count(),
        "failed": base.filter(WorkflowRun.status == "failed").count(),
        "events_today": db.query(func.count(AutomationEvent.id)).filter(AutomationEvent.created_at >= datetime.combine(today, datetime.min.time())).scalar() or 0,
    }


JOBS = [
    ("automation_advance_runs", lambda db: str(advance_due_runs(db)), 5),
    ("automation_stale_leads", lambda db: f"{detect_stale_leads(db)} stale lead(s)", 60 * 24),
    ("automation_inactive_students", lambda db: f"{detect_inactive_students(db)} inactive student(s)", 60 * 24),
]
