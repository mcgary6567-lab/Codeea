"""CRM automation pages: the workflow catalogue, runs, the event log and contact tags.

The engine lives in app.services.automation; these routes only read it, edit the catalogue and let staff
start, stop or push a run by hand. Every workflow is shown in plain words (trigger sentence, numbered steps)
with the raw JSON underneath for the people who maintain them.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.audit import log_action, snapshot
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import paginate, parse_bool, parse_date, parse_int, redirect
from app.database import get_db
from app.models.automation import (CONTACT_TYPES, RUN_STATUSES, STEP_KINDS, TAG_CATEGORIES, WORKFLOW_CATEGORIES, WORKFLOW_TRIGGERS,
                                   AutomationEvent, ContactTag, Tag, Workflow, WorkflowRun)
from app.models.core import User
from app.models.crm import LEAD_STAGES, Lead
from app.models.people import Client, Student
from app.services import automation as auto

router = APIRouter(prefix="/crm", dependencies=[Depends(csrf_protect)])

ACQUISITION = "Student Acquisition"

# The Student Acquisition pipeline, stage by stage: (ERP label, internal lead stage, the events that mean "entered this stage").
PIPELINE_STAGES = [
    ("Form Submitted", "new", ["lead.created"]),
    ("Demo Booking Pending", "contacted", []),
    ("Demo Scheduled", "trial_scheduled", ["trial.scheduled"]),
    ("Demo Completed", "trial_done", ["trial.attended"]),
    ("Admission Review", "negotiation", []),
    ("Payment Pending", "payment_pending", []),
    ("Student Enrolled", "won", ["lead.converted", "payment.first"]),
    ("Stale / Lost", "lost", ["lead.lost", "lead.stale"]),
]

# Compact chip text per step kind for the list page.
STEP_SHORT = {"wait": "Wait", "wait_until": "Until", "send_whatsapp": "WhatsApp", "send_email": "Email", "send_sms": "SMS",
              "notify_staff": "Notify", "add_tag": "Tag +", "remove_tag": "Tag −", "move_stage": "Stage", "create_task": "Task",
              "enroll_sequence": "Sequence", "condition": "If", "webhook": "Webhook", "exit": "Stop"}

TAG_COLORS = ["sky", "indigo", "amber", "emerald", "violet", "rose", "orange", "teal", "slate", "brand"]

CONTACT_URLS = {"lead": "/crm/leads/{id}", "client": "/clients/{id}", "student": "/students/{id}"}

STEPS_EXAMPLE = json.dumps([
    {"kind": "send_whatsapp", "template": "lead_first_touch"},
    {"kind": "wait", "days": 2},
    {"kind": "condition", "field": "stage", "op": "eq", "value": "contacted", "then": "continue", "else": "exit"},
], indent=2)

RUN_STATUS_OPTIONS = [("running", "Running (active or waiting)")] + [(s, s.title()) for s in RUN_STATUSES]


# ----------------------------------------------------------------------------- words
def _who(to: Optional[str]) -> str:
    to = (to or "assigned").strip()
    if to == "assigned":
        return "the assigned rep"
    if to == "teacher":
        return "the teacher"
    if to.startswith("role:"):
        return f"everyone with the role {to[5:]}"
    if to.startswith("user:"):
        return to[5:]
    if to.startswith("hod:"):
        return f"the head of {to[4:]}"
    return to


def _short(text: Any, n: int = 90) -> str:
    s = str(text or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _branch(v: Any) -> str:
    if v in (None, "", "continue"):
        return "continue"
    if v == "exit":
        return "stop"
    if isinstance(v, str) and v.startswith("goto:"):
        return f"go to step {int(v[5:]) + 1}" if v[5:].isdigit() else v
    if isinstance(v, int):
        return f"go to step {v + 1}"
    return str(v)


def _duration(step: dict) -> str:
    parts = []
    days = float(step.get("days") or 0)
    hours = float(step.get("hours") or 0)
    if days:
        parts.append(f"{days:g} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours:g} hour{'s' if hours != 1 else ''}")
    return " and ".join(parts) or "no time"


OPS = {"eq": "is", "ne": "is not", "in": "is one of", "truthy": "is set", "falsy": "is not set", "gte": "is at least", "lte": "is at most"}


def describe_step(step: dict) -> str:
    """One step as a sentence a coordinator can read without knowing the JSON."""
    if not isinstance(step, dict):
        return str(step)
    k = step.get("kind")
    if k == "wait":
        return f"Wait {_duration(step)}"
    if k == "wait_until":
        before = step.get("hours_before")
        return f"Wait until {step.get('field') or 'the date'}" + (f", {before:g} hours before" if before else "")
    if k == "send_whatsapp":
        return f"Send WhatsApp: template {step['template']}" if step.get("template") else f"Send WhatsApp: “{_short(step.get('body'))}”"
    if k == "send_email":
        return f"Send email “{_short(step.get('subject') or 'Online Quran College', 70)}”"
    if k == "send_sms":
        return f"Send SMS: “{_short(step.get('body'))}”"
    if k == "notify_staff":
        return f"Notify {_who(step.get('to'))}: {_short(step.get('title') or 'the workflow name', 70)}"
    if k == "add_tag":
        return f"Add tag {step.get('tag')}"
    if k == "remove_tag":
        return f"Remove tag {step.get('tag')}"
    if k == "move_stage":
        return f"Move lead to stage {step.get('stage')}" + (f" ({step['reason']})" if step.get("reason") else "")
    if k == "create_task":
        due = step.get("due_days") or 1
        return f"Create task for {_who(step.get('to'))}: {_short(step.get('title') or 'the workflow name', 70)}, due in {due} day{'s' if str(due) != '1' else ''}"
    if k == "enroll_sequence":
        return f"Enrol in the WhatsApp sequence {step.get('sequence_type')}"
    if k == "condition":
        op = step.get("op", "eq")
        value = step.get("value")
        test = f"{step.get('field')} {OPS.get(op, op)}" + ("" if op in ("truthy", "falsy") else f" {value}")
        return f"If {test} then {_branch(step.get('then'))} else {_branch(step.get('else'))}"
    if k == "webhook":
        return f"Send webhook event {step.get('event') or '(workflow code)'}"
    if k == "exit":
        return "Stop this workflow" + (f" ({step['reason']})" if step.get("reason") else "")
    return f"Unknown step {k}"


def describe_filter(f: Optional[dict]) -> list[str]:
    f = f or {}
    out = []
    if "stage" in f:
        out.append(f"only when the stage is {f['stage']}")
    if "stages" in f:
        out.append("only when the stage is one of " + ", ".join(f["stages"] or []))
    if "tag" in f:
        out.append(f"only when the tag is {f['tag']}")
    if "nps_min" in f:
        out.append(f"only when the NPS score is at least {f['nps_min']}")
    if "nps_max" in f:
        out.append(f"only when the NPS score is at most {f['nps_max']}")
    if "level" in f:
        out.append(f"only when the level is {f['level']}")
    if "country" in f:
        out.append(f"only for contacts in {f['country']}")
    if "first" in f:
        out.append("only the first time" if f["first"] else "never the first time")
    return out


def describe_exit(ex: Optional[dict]) -> list[str]:
    ex = ex or {}
    out = []
    if ex.get("lead_stages"):
        out.append("the lead reaches " + ", ".join(ex["lead_stages"]))
    if ex.get("client_statuses"):
        out.append("the client becomes " + ", ".join(ex["client_statuses"]))
    if ex.get("student_statuses"):
        out.append("the student becomes " + ", ".join(ex["student_statuses"]))
    if ex.get("reply"):
        out.append("the contact replies")
    if ex.get("tags"):
        out.append("the contact is tagged " + ", ".join(ex["tags"]))
    return out


def trigger_label(trigger: str) -> str:
    return WORKFLOW_TRIGGERS.get(trigger, (trigger, "any"))[0]


def contact_url(contact_type: str, contact_id: int) -> str:
    return CONTACT_URLS.get(contact_type, "#").format(id=contact_id)


def summarise_payload(payload: Optional[dict]) -> str:
    bits = [f"{k}={v}" for k, v in (payload or {}).items() if isinstance(v, (str, int, float, bool)) and str(v) != ""]
    return _short("; ".join(bits), 120)


# ----------------------------------------------------------------------------- helpers
def _wf(db: Session, id: int) -> Workflow:
    wf = db.get(Workflow, id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return wf


def _run(db: Session, id: int) -> WorkflowRun:
    run = db.get(WorkflowRun, id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


def _tag(db: Session, id: int) -> Tag:
    t = db.get(Tag, id)
    if not t:
        raise HTTPException(404, "Tag not found")
    return t


def _json_field(form, name: str, expect: type, default):
    """Parse a JSON form field; raise ValueError with a message staff can act on."""
    raw = (form.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name.replace('_', ' ').capitalize()} is not valid JSON ({exc.msg} at position {exc.pos}).")
    if not isinstance(value, expect):
        raise ValueError(f"{name.replace('_', ' ').capitalize()} must be a JSON {'list' if expect is list else 'object'}.")
    return value


def _workflow_form(form) -> dict:
    """Validated workflow fields from the create/edit form. Raises ValueError listing every problem."""
    errors: list[str] = []
    data: dict = {}
    name = (form.get("name") or "").strip()
    if not name:
        errors.append("A name is required.")
    trigger = (form.get("trigger") or "").strip()
    if trigger not in WORKFLOW_TRIGGERS:
        errors.append("Choose a trigger.")
    category = form.get("category") or "acquisition"
    if category not in dict(WORKFLOW_CATEGORIES):
        category = "acquisition"
    for field, expect, default in (("trigger_filter", dict, {}), ("steps", list, []), ("exit_on", dict, {})):
        try:
            data[field] = _json_field(form, field, expect, default)
        except ValueError as exc:
            errors.append(str(exc))
    if "steps" in data:
        errors.extend(auto.validate_steps(data["steps"]))
    if errors:
        raise ValueError("; ".join(e.rstrip(".") for e in errors) + ".")
    data.update(name=name[:120], trigger=trigger, category=category, description=(form.get("description") or "").strip() or None,
                pipeline=(form.get("pipeline") or "").strip()[:60] or None, stage=(form.get("stage") or "").strip()[:60] or None,
                run_once_per_contact=parse_bool(form.get("run_once_per_contact")), needs=(form.get("needs") or "").strip()[:200] or None)
    return data


def _next_auto_code(db: Session) -> str:
    n = 0
    for (code,) in db.query(Workflow.code).filter(Workflow.code.like("AUTO-%")).all():
        m = re.match(r"AUTO-(\d+)$", code or "")
        if m:
            n = max(n, int(m.group(1)))
    return f"AUTO-{n + 1:03d}"


def _resolve_ref(db: Session, contact_type: str, ref: str):
    """A contact from its numeric id or its code (L-00001 / C-00001 / S-00001)."""
    ref = (ref or "").strip()
    if not ref or contact_type not in CONTACT_TYPES:
        return None
    if ref.isdigit():
        return auto.resolve_contact(db, contact_type, int(ref))
    model, col = {"lead": (Lead, Lead.lead_code), "client": (Client, Client.client_code), "student": (Student, Student.student_code)}[contact_type]
    return db.query(model).filter(func.upper(col) == ref.upper()).first()


def _contacts_for_runs(db: Session, runs: list[WorkflowRun]) -> dict:
    return {r.id: auto.resolve_contact(db, r.contact_type, r.contact_id) for r in runs}


def _start(db: Session, wf: Workflow, contact_type: str, contact, user: User, request: Request) -> WorkflowRun:
    run = auto.start_manually(db, wf, contact_type, contact.id, user)
    log_action(db, user, "execute", "automations", entity=run,
               description=f"Workflow {wf.code} started by hand for {contact_type} {auto.contact_label(contact_type, contact)}", request=request)
    db.commit()
    return run


def _day_bounds(value: str) -> tuple[Optional[datetime], Optional[datetime]]:
    """'today' or an ISO date -> [start, end) datetimes."""
    if value == "today":
        d = datetime.utcnow().date()
    else:
        d = parse_date(value)
    if not d:
        return None, None
    start = datetime.combine(d, datetime.min.time())
    return start, start + timedelta(days=1)


def _range_filter(q, column, start: str, end: str):
    s, _ = _day_bounds(start)
    if s:
        q = q.filter(column >= s)
    e0, e1 = _day_bounds(end)
    if e1:
        q = q.filter(column < e1)
    return q


# ============================================================================= WORKFLOWS
@router.get("/automations", include_in_schema=False)
def automations(request: Request, category: str = "", trigger: str = "", pipeline: str = "", status: str = "", q: str = "",
                db: Session = Depends(get_db), user: User = Depends(require("automations.view"))):
    query = db.query(Workflow)
    if category:
        query = query.filter(Workflow.category == category)
    if trigger:
        query = query.filter(Workflow.trigger == trigger)
    if pipeline:
        query = query.filter(Workflow.pipeline == pipeline)
    if status == "active":
        query = query.filter(Workflow.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(Workflow.is_active.is_(False))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Workflow.name.ilike(like), Workflow.code.ilike(like), Workflow.description.ilike(like), Workflow.stage.ilike(like)))
    workflows = query.order_by(Workflow.sort_no, Workflow.id).all()
    totals = dict(db.query(WorkflowRun.workflow_id, func.count(WorkflowRun.id)).group_by(WorkflowRun.workflow_id).all())
    running = dict(db.query(WorkflowRun.workflow_id, func.count(WorkflowRun.id)).filter(WorkflowRun.status.in_(["active", "waiting"]))
                   .group_by(WorkflowRun.workflow_id).all())
    pipelines = [p for (p,) in db.query(Workflow.pipeline).filter(Workflow.pipeline.isnot(None)).distinct().order_by(Workflow.pipeline).all()]
    acq = db.query(Workflow).filter(Workflow.pipeline == ACQUISITION).order_by(Workflow.sort_no, Workflow.id).all()
    pipeline_map = []
    for label, internal, events in PIPELINE_STAGES:
        serving = [w for w in acq if (w.trigger_filter or {}).get("stage") == internal or w.trigger in events or (w.stage or "").lower() == label.lower()
                   or internal in ((w.trigger_filter or {}).get("stages") or [])]
        pipeline_map.append({"label": label, "stage": internal, "workflows": serving})
    return render(request, "crm/automations.html", {
        "user": user, "workflows": workflows, "stats": auto.workflow_stats(db), "totals": totals, "running": running,
        "category": category, "trigger": trigger, "pipeline": pipeline, "status": status, "q": q,
        "categories": WORKFLOW_CATEGORIES, "triggers": [(k, v[0]) for k, v in WORKFLOW_TRIGGERS.items()], "pipelines": pipelines,
        "trigger_labels": {k: v[0] for k, v in WORKFLOW_TRIGGERS.items()}, "step_short": STEP_SHORT, "step_kinds": STEP_KINDS,
        "steps_example": STEPS_EXAMPLE, "next_code": _next_auto_code(db), "pipeline_map": pipeline_map, "acquisition": ACQUISITION,
    })


@router.post("/automations/new", include_in_schema=False)
async def create_workflow(request: Request, db: Session = Depends(get_db), user: User = Depends(require("automations.add"))):
    form = await request.form()
    try:
        data = _workflow_form(form)
    except ValueError as exc:
        return redirect("/crm/automations", str(exc), "error")
    code = (form.get("code") or "").strip().upper()[:20] or _next_auto_code(db)
    if db.query(Workflow).filter(Workflow.code == code).first():
        return redirect("/crm/automations", f"The code {code} is already used by another workflow.", "error")
    sort_no = (db.query(func.max(Workflow.sort_no)).scalar() or 0) + 1
    wf = Workflow(code=code, is_active=parse_bool(form.get("is_active")), sort_no=sort_no, **data)
    db.add(wf)
    db.flush()
    log_action(db, user, "create", "automations", entity=wf, description=f"Workflow {wf.code} '{wf.name}' created with {len(wf.steps or [])} steps", request=request)
    db.commit()
    return redirect(f"/crm/automations/{wf.id}", f"Workflow {wf.code} created.")


@router.post("/automations/run", include_in_schema=False)
async def run_due_now(request: Request, db: Session = Depends(get_db), user: User = Depends(require("automations.update"))):
    counts = auto.advance_due_runs(db)
    log_action(db, user, "execute", "automations", description=f"Due workflow steps run by hand: {counts}", request=request)
    db.commit()
    return redirect("/crm/automations", f"Runner finished: {counts['advanced']} advanced, {counts['waiting']} waiting, "
                                        f"{counts['completed']} completed, {counts['stopped']} stopped, {counts['failed']} failed.")


@router.post("/automations/start", include_in_schema=False)
async def start_workflow_for_contact(request: Request, db: Session = Depends(get_db), user: User = Depends(require("automations.update"))):
    """Start from a contact's page: workflow_id + contact_type + contact_id, back to `next`."""
    form = await request.form()
    back = form.get("next") or "/crm/automations"
    wf = db.get(Workflow, parse_int(form.get("workflow_id")) or 0)
    if not wf:
        return redirect(back, "Choose a workflow to start.", "error")
    ctype = form.get("contact_type") or "lead"
    contact = _resolve_ref(db, ctype, form.get("contact_id") or "")
    if not contact:
        return redirect(back, "That contact could not be found.", "error")
    run = _start(db, wf, ctype, contact, user, request)
    return redirect(back, f"Workflow {wf.code} started; the run is {run.status}.")


# ----------------------------------------------------------------------------- runs (static paths before /{id})
@router.get("/automations/runs", include_in_schema=False)
def runs(request: Request, workflow: str = "", status: str = "", contact_type: str = "", start: str = "", end: str = "", page: int = 1,
         db: Session = Depends(get_db), user: User = Depends(require("automations.view"))):
    q = db.query(WorkflowRun)
    wid = parse_int(workflow)
    if wid:
        q = q.filter(WorkflowRun.workflow_id == wid)
    if status == "running":
        q = q.filter(WorkflowRun.status.in_(["active", "waiting"]))
    elif status:
        q = q.filter(WorkflowRun.status == status)
    if contact_type:
        q = q.filter(WorkflowRun.contact_type == contact_type)
    q = _range_filter(q, WorkflowRun.started_at, start, end)
    pg = paginate(q.order_by(WorkflowRun.started_at.desc(), WorkflowRun.id.desc()), page, 40)
    workflows = db.query(Workflow).order_by(Workflow.sort_no, Workflow.id).all()
    base = f"/crm/automations/runs?workflow={workflow}&status={status}&contact_type={contact_type}&start={start}&end={end}"
    return render(request, "crm/automation_runs.html", {
        "user": user, "page": pg, "contacts": _contacts_for_runs(db, pg.items), "workflows": workflows, "workflow": workflow, "status": status,
        "wf_options": [(w.id, f"{w.code} {w.name}") for w in workflows],
        "contact_type": contact_type, "start": start, "end": end, "statuses": RUN_STATUS_OPTIONS, "contact_types": CONTACT_TYPES,
        "trigger_labels": {k: v[0] for k, v in WORKFLOW_TRIGGERS.items()}, "contact_url": contact_url, "contact_label": auto.contact_label,
        "base_url": base, "status_counts": dict(db.query(WorkflowRun.status, func.count(WorkflowRun.id)).group_by(WorkflowRun.status).all()),
    })


@router.get("/automations/runs/{run_id}", include_in_schema=False)
def run_detail(run_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("automations.view"))):
    run = _run(db, run_id)
    wf = run.workflow
    contact = auto.resolve_contact(db, run.contact_type, run.contact_id)
    steps = wf.steps or []
    timeline = []
    for entry in run.log or []:
        idx = entry.get("step")
        timeline.append({"step": idx, "kind": entry.get("kind"), "at": entry.get("at"), "result": entry.get("result"),
                         "words": describe_step(steps[idx]) if isinstance(idx, int) and 0 <= idx < len(steps) else STEP_KINDS.get(entry.get("kind"), {}).get("label", entry.get("kind"))})
    return render(request, "crm/automation_run.html", {
        "user": user, "run": run, "wf": wf, "contact": contact, "timeline": timeline, "steps": steps, "describe_step": describe_step,
        "contact_url": contact_url(run.contact_type, run.contact_id), "contact_label": auto.contact_label(run.contact_type, contact) if contact else f"{run.contact_type} #{run.contact_id}",
        "trigger_label": trigger_label(run.trigger_event), "step_kinds": STEP_KINDS, "payload": summarise_payload(run.context),
    })


@router.post("/automations/runs/{run_id}/stop", include_in_schema=False)
async def stop_run(run_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("automations.update"))):
    run = _run(db, run_id)
    form = await request.form()
    back = form.get("next") or f"/crm/automations/runs/{run.id}"
    if run.status not in ("active", "waiting"):
        return redirect(back, f"This run is already {run.status}.", "warning")
    reason = (form.get("rationale") or form.get("reason") or "").strip() or f"Stopped by {user.full_name}"
    run.status = "stopped"
    run.stop_reason = reason[:200]
    run.next_run_at = None
    run.finished_at = datetime.utcnow()
    entries = list(run.log or [])
    entries.append({"step": run.current_step, "kind": "exit", "at": run.finished_at.isoformat(timespec="seconds"), "result": f"stopped by {user.email}: {reason}"[:300]})
    run.log = entries[-200:]
    log_action(db, user, "cancel", "automations", entity=run, description=f"Run {run.id} of {run.workflow.code} stopped", rationale=reason, request=request)
    db.commit()
    return redirect(back, "Run stopped.", "warning")


@router.post("/automations/runs/{run_id}/advance", include_in_schema=False)
async def advance_now(run_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("automations.update"))):
    run = _run(db, run_id)
    form = await request.form()
    back = form.get("next") or f"/crm/automations/runs/{run.id}"
    if run.status not in ("active", "waiting"):
        return redirect(back, f"This run is {run.status} and cannot be advanced.", "warning")
    before = run.current_step
    auto.advance_run(db, run, now=datetime.utcnow(), force=True)
    log_action(db, user, "execute", "automations", entity=run,
               description=f"Run {run.id} of {run.workflow.code} advanced by hand from step {before + 1} to step {run.current_step + 1} ({run.status})", request=request)
    db.commit()
    return redirect(back, f"Run advanced; it is now {run.status} at step {min(run.current_step + 1, len(run.workflow.steps or []))} of {len(run.workflow.steps or [])}.")


# ----------------------------------------------------------------------------- events
@router.get("/automations/events", include_in_schema=False)
def events(request: Request, event: str = "", start: str = "", end: str = "", page: int = 1,
           db: Session = Depends(get_db), user: User = Depends(require("automations.view"))):
    q = db.query(AutomationEvent)
    if event:
        q = q.filter(AutomationEvent.event == event)
    q = _range_filter(q, AutomationEvent.created_at, start, end)
    pg = paginate(q.order_by(AutomationEvent.created_at.desc(), AutomationEvent.id.desc()), page, 50)
    contacts = {e.id: auto.resolve_contact(db, e.contact_type, e.contact_id) for e in pg.items}
    counts = dict(db.query(AutomationEvent.event, func.count(AutomationEvent.id)).group_by(AutomationEvent.event).all())
    return render(request, "crm/automation_events.html", {
        "user": user, "page": pg, "contacts": contacts, "event": event, "start": start, "end": end,
        "events": [(k, v[0]) for k, v in WORKFLOW_TRIGGERS.items() if k != "manual"], "trigger_labels": {k: v[0] for k, v in WORKFLOW_TRIGGERS.items()},
        "contact_url": contact_url, "contact_label": auto.contact_label, "summarise": summarise_payload, "counts": counts,
        "base_url": f"/crm/automations/events?event={event}&start={start}&end={end}",
    })


# ----------------------------------------------------------------------------- one workflow
@router.get("/automations/{id}", include_in_schema=False)
def workflow_detail(id: int, request: Request, tab: str = "runs", page: int = 1, db: Session = Depends(get_db), user: User = Depends(require("automations.view"))):
    wf = _wf(db, id)
    steps = wf.steps or []
    runs_q = db.query(WorkflowRun).filter(WorkflowRun.workflow_id == wf.id).order_by(WorkflowRun.started_at.desc(), WorkflowRun.id.desc())
    pg = paginate(runs_q, page, 40)
    status_counts = dict(db.query(WorkflowRun.status, func.count(WorkflowRun.id)).filter(WorkflowRun.workflow_id == wf.id).group_by(WorkflowRun.status).all())
    ev_q = db.query(AutomationEvent).filter(AutomationEvent.event == wf.trigger).order_by(AutomationEvent.created_at.desc(), AutomationEvent.id.desc())
    ev_page = paginate(ev_q, page if tab == "events" else 1, 40)
    ev_contacts = {e.id: auto.resolve_contact(db, e.contact_type, e.contact_id) for e in ev_page.items}
    return render(request, "crm/automation_detail.html", {
        "user": user, "wf": wf, "steps": steps, "step_words": [describe_step(s) for s in steps], "step_kinds": STEP_KINDS,
        "trigger_label": trigger_label(wf.trigger), "filter_words": describe_filter(wf.trigger_filter), "exit_words": describe_exit(wf.exit_on),
        "trigger_contact": WORKFLOW_TRIGGERS.get(wf.trigger, ("", "any"))[1],
        "trigger_labels": {k: v[0] for k, v in WORKFLOW_TRIGGERS.items()}, "triggers": [(k, v[0]) for k, v in WORKFLOW_TRIGGERS.items()],
        "categories": WORKFLOW_CATEGORIES, "contact_types": CONTACT_TYPES,
        "steps_json": json.dumps(steps, indent=2), "filter_json": json.dumps(wf.trigger_filter or {}, indent=2), "exit_json": json.dumps(wf.exit_on or {}, indent=2),
        "tab": tab, "page": pg, "contacts": _contacts_for_runs(db, pg.items), "status_counts": status_counts,
        "ev_page": ev_page, "ev_contacts": ev_contacts, "summarise": summarise_payload,
        "contact_url": contact_url, "contact_label": auto.contact_label,
        "tabs": [("runs", f"Runs ({pg.total})", f"/crm/automations/{wf.id}?tab=runs"), ("events", f"Events ({ev_page.total})", f"/crm/automations/{wf.id}?tab=events")],
        "base_url": f"/crm/automations/{wf.id}?tab={tab}",
    })


@router.post("/automations/{id}/edit", include_in_schema=False)
async def edit_workflow(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("automations.update"))):
    wf = _wf(db, id)
    form = await request.form()
    try:
        data = _workflow_form(form)
    except ValueError as exc:
        return redirect(f"/crm/automations/{wf.id}", f"Not saved. {exc}", "error")
    before = snapshot(wf)
    for k, v in data.items():
        setattr(wf, k, v)
    log_action(db, user, "update", "automations", entity=wf, description=f"Workflow {wf.code} '{wf.name}' edited ({len(wf.steps or [])} steps)",
               before=before, after=snapshot(wf), request=request)
    db.commit()
    return redirect(f"/crm/automations/{wf.id}", "Workflow saved.")


@router.post("/automations/{id}/toggle", include_in_schema=False)
async def toggle_workflow(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("automations.update"))):
    wf = _wf(db, id)
    form = await request.form()
    rationale = (form.get("rationale") or "").strip() or None
    wf.is_active = not wf.is_active
    word = "activated" if wf.is_active else "deactivated"
    log_action(db, user, "update", "automations", entity=wf, description=f"Workflow {wf.code} {word}", rationale=rationale,
               consequential=True, request=request)
    db.commit()
    note = "" if wf.is_active else " Runs still in progress stop the next time they are due."
    return redirect(form.get("next") or f"/crm/automations/{wf.id}", f"Workflow {wf.code} {word}.{note}", "success" if wf.is_active else "warning")


@router.post("/automations/{id}/start", include_in_schema=False)
async def start_workflow(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("automations.update"))):
    wf = _wf(db, id)
    form = await request.form()
    back = form.get("next") or f"/crm/automations/{wf.id}"
    ctype = form.get("contact_type") or "lead"
    contact = _resolve_ref(db, ctype, form.get("contact_ref") or form.get("contact_id") or "")
    if not contact:
        return redirect(back, "That contact could not be found. Enter the record's id or its code (L-00001, C-00001, S-00001).", "error")
    expected = WORKFLOW_TRIGGERS.get(wf.trigger, ("", "any"))[1]
    if expected not in ("any", ctype):
        return redirect(back, f"This workflow is written for a {expected}, not a {ctype}.", "error")
    run = _start(db, wf, ctype, contact, user, request)
    return redirect(f"/crm/automations/runs/{run.id}", f"Workflow started for {auto.contact_label(ctype, contact)}; the run is {run.status}.")


@router.post("/automations/{id}/duplicate", include_in_schema=False)
async def duplicate_workflow(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("automations.add"))):
    wf = _wf(db, id)
    base = wf.code[:14]
    code, n = f"{base}-COPY", 2
    while db.query(Workflow).filter(Workflow.code == code).first():
        code = f"{base}-COPY{n}"
        n += 1
    copy = Workflow(code=code[:20], name=f"{wf.name} (copy)"[:120], description=wf.description, category=wf.category, pipeline=wf.pipeline, stage=wf.stage,
                    trigger=wf.trigger, trigger_filter=dict(wf.trigger_filter or {}), steps=list(wf.steps or []), exit_on=dict(wf.exit_on or {}),
                    run_once_per_contact=wf.run_once_per_contact, is_active=False, needs=wf.needs, sort_no=(db.query(func.max(Workflow.sort_no)).scalar() or 0) + 1)
    db.add(copy)
    db.flush()
    log_action(db, user, "create", "automations", entity=copy, description=f"Workflow {copy.code} duplicated from {wf.code}", request=request)
    db.commit()
    return redirect(f"/crm/automations/{copy.id}", f"Copied to {copy.code}. The copy is switched off until you activate it.")


# ============================================================================= TAGS
@router.get("/tags", include_in_schema=False)
def tags(request: Request, category: str = "", status: str = "", q: str = "", db: Session = Depends(get_db), user: User = Depends(require("tags.view"))):
    query = db.query(Tag)
    if category:
        query = query.filter(Tag.category == category)
    if status == "active":
        query = query.filter(Tag.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(Tag.is_active.is_(False))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Tag.name.ilike(like), Tag.description.ilike(like)))
    rows = query.order_by(Tag.category, Tag.name).all()
    counts = dict(db.query(ContactTag.tag_id, func.count(ContactTag.id)).group_by(ContactTag.tag_id).all())
    per_category = dict(db.query(Tag.category, func.count(Tag.id)).group_by(Tag.category).all())
    return render(request, "crm/tags.html", {
        "user": user, "tags": rows, "counts": counts, "per_category": per_category, "categories": TAG_CATEGORIES, "colors": TAG_COLORS,
        "category": category, "status": status, "q": q, "category_labels": dict(TAG_CATEGORIES),
    })


@router.post("/tags/new", include_in_schema=False)
async def create_tag(request: Request, db: Session = Depends(get_db), user: User = Depends(require("tags.update"))):
    form = await request.form()
    name = (form.get("name") or "").strip()[:60]
    if not name:
        return redirect("/crm/tags", "A tag name is required.", "error")
    if db.query(Tag).filter(func.lower(Tag.name) == name.lower()).first():
        return redirect("/crm/tags", f"The tag {name} already exists.", "error")
    category = form.get("category") if form.get("category") in dict(TAG_CATEGORIES) else "other"
    color = form.get("color") if form.get("color") in TAG_COLORS else "slate"
    t = Tag(name=name, category=category, color=color, description=(form.get("description") or "").strip()[:200] or None, is_active=True)
    db.add(t)
    db.flush()
    log_action(db, user, "create", "tags", entity=t, description=f"Tag {t.name} created ({t.category})", request=request)
    db.commit()
    return redirect("/crm/tags", f"Tag {t.name} created.")


@router.post("/tags/apply", include_in_schema=False)
async def apply_tag(request: Request, db: Session = Depends(get_db), user: User = Depends(require("tags.update"))):
    form = await request.form()
    back = form.get("next") or "/crm/tags"
    ctype = form.get("contact_type") or ""
    cid = parse_int(form.get("contact_id"))
    name = (form.get("tag") or "").strip()[:60]
    if ctype not in CONTACT_TYPES or not cid or not auto.resolve_contact(db, ctype, cid):
        return redirect(back, "That contact could not be found.", "error")
    if not name:
        return redirect(back, "Type or choose a tag.", "error")
    added = auto.add_tag(db, ctype, cid, name, added_by=user.email)
    if added:
        log_action(db, user, "update", "tags", entity_type=ctype.capitalize(), entity_id=cid, description=f"Tag {name} added to {ctype} {cid}", request=request)
    db.commit()
    return redirect(back, f"Tag {name} added." if added else f"This {ctype} already carries {name}.", "success" if added else "warning")


@router.post("/tags/unapply", include_in_schema=False)
async def unapply_tag(request: Request, db: Session = Depends(get_db), user: User = Depends(require("tags.update"))):
    form = await request.form()
    back = form.get("next") or "/crm/tags"
    ctype = form.get("contact_type") or ""
    cid = parse_int(form.get("contact_id"))
    name = (form.get("tag") or "").strip()
    if ctype not in CONTACT_TYPES or not cid or not name:
        return redirect(back, "Nothing to remove.", "error")
    removed = auto.remove_tag(db, ctype, cid, name)
    if removed:
        log_action(db, user, "update", "tags", entity_type=ctype.capitalize(), entity_id=cid, description=f"Tag {name} removed from {ctype} {cid}", request=request)
    db.commit()
    return redirect(back, f"Tag {name} removed." if removed else f"This {ctype} did not carry {name}.", "success" if removed else "warning")


@router.get("/tags/{id}", include_in_schema=False)
def tag_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("tags.view"))):
    t = _tag(db, id)
    rows = []
    for ctype, contact in auto.contacts_with_tag(db, t):
        code = getattr(contact, "lead_code", None) or getattr(contact, "client_code", None) or getattr(contact, "student_code", None) or ""
        rows.append({"type": ctype, "code": code, "name": getattr(contact, "full_name", ""), "id": contact.id, "url": contact_url(ctype, contact.id),
                     "status": getattr(contact, "stage", None) or getattr(contact, "status", None)})
    by_type = {}
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    total = db.query(func.count(ContactTag.id)).filter(ContactTag.tag_id == t.id).scalar() or 0
    return render(request, "crm/tag_detail.html", {"user": user, "t": t, "rows": rows, "by_type": by_type, "total": total, "categories": TAG_CATEGORIES,
                                                  "colors": TAG_COLORS, "category_labels": dict(TAG_CATEGORIES)})


@router.post("/tags/{id}/edit", include_in_schema=False)
async def edit_tag(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("tags.update"))):
    t = _tag(db, id)
    form = await request.form()
    back = form.get("next") or "/crm/tags"
    name = (form.get("name") or "").strip()[:60]
    if not name:
        return redirect(back, "A tag name is required.", "error")
    clash = db.query(Tag).filter(func.lower(Tag.name) == name.lower(), Tag.id != t.id).first()
    if clash:
        return redirect(back, f"Another tag is already called {name}.", "error")
    before = snapshot(t)
    t.name = name
    if form.get("category") in dict(TAG_CATEGORIES):
        t.category = form.get("category")
    if form.get("color") in TAG_COLORS:
        t.color = form.get("color")
    t.description = (form.get("description") or "").strip()[:200] or None
    log_action(db, user, "update", "tags", entity=t, description=f"Tag {t.name} edited", before=before, after=snapshot(t), request=request)
    db.commit()
    return redirect(back, f"Tag {t.name} saved.")


@router.post("/tags/{id}/toggle", include_in_schema=False)
async def toggle_tag(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("tags.update"))):
    t = _tag(db, id)
    form = await request.form()
    t.is_active = not t.is_active
    word = "activated" if t.is_active else "deactivated"
    log_action(db, user, "update", "tags", entity=t, description=f"Tag {t.name} {word}", rationale=(form.get("rationale") or "").strip() or None, request=request)
    db.commit()
    return redirect(form.get("next") or "/crm/tags", f"Tag {t.name} {word}.", "success" if t.is_active else "warning")


@router.post("/tags/{id}/remove", include_in_schema=False)
async def remove_tag_from_contact(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("tags.update"))):
    t = _tag(db, id)
    form = await request.form()
    ctype = form.get("contact_type") or ""
    cid = parse_int(form.get("contact_id"))
    if ctype not in CONTACT_TYPES or not cid:
        return redirect(f"/crm/tags/{t.id}", "Nothing to remove.", "error")
    removed = auto.remove_tag(db, ctype, cid, t.name)
    if removed:
        log_action(db, user, "update", "tags", entity_type=ctype.capitalize(), entity_id=cid, description=f"Tag {t.name} removed from {ctype} {cid}", request=request)
    db.commit()
    return redirect(f"/crm/tags/{t.id}", f"Tag removed from the {ctype}." if removed else "That contact did not carry this tag.", "success" if removed else "warning")
