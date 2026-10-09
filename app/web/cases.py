"""Complaints, requests & cases (Module 32): AI classification, SLA countdown, escalation ladder, investigation findings,
parent confirmation, evidence and management intelligence (docs/COMPLAINTS.md)."""
from __future__ import annotations

from datetime import datetime, date, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import require, csrf_protect
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import redirect, paginate, parse_date, parse_int, parse_bool
from app.database import get_db
from app.models.core import User, Department, Role, AuditEvent
from app.models.crm import Case, CaseComment, Feedback, PARENT_CONTACT_CHANNELS, PARENT_SATISFACTION
from app.models.hr_erp import Attachment
from app.services.crm import OPEN_CASE_STATUSES, WORKFLOW_ONLY_STATUSES
from app.models.ops import Task
from app.models.people import Client, Employee, Student, Teacher
from app.services import complaints as cx
from app.services import crm as svc
from app.services import lookups

router = APIRouter(prefix="/cases", dependencies=[Depends(csrf_protect)])


def _form_ctx(db: Session, case: Case | None = None) -> dict:
    departments = db.query(Department).filter(Department.is_active.is_(True)).order_by(Department.name).all()
    clients = db.query(Client).order_by(Client.full_name).limit(500).all()
    students = db.query(Student).order_by(Student.full_name).limit(600).all()
    teachers = db.query(Teacher).order_by(Teacher.full_name).all()
    employees = db.query(Employee).filter(Employee.status != "terminated").order_by(Employee.full_name).all()
    return {"types": svc.CASE_TYPES, "priorities": svc.PRIORITIES,
            "statuses": [(s, cx.STATUS_LABELS.get(s, s)) for s in svc.CASE_STATUSES],
            "departments": departments,
            "department_options": [(d.id, d.name) for d in departments],
            "staff_options": cx.staff_options(db, case),
            "client_options": [(c.id, f"{c.full_name} ({c.client_code})") for c in clients],
            "student_options": [(s.id, f"{s.full_name} ({s.student_code})") for s in students],
            "teacher_options": [(t.id, t.full_name) for t in teachers],
            "employee_options": [(e.id, f"{e.full_name}" + (f" · {e.designation}" if getattr(e, "designation", None) else "")) for e in employees],
            "complaint_types": lookups.labels(db, "family_complaint_type", fallback=["Teaching Quality", "Teacher Punctuality", "Billing",
                                                                                   "Class Timing", "Technical", "Behaviour", "Other"]),
            "root_causes": lookups.labels(db, cx.ROOT_CAUSE_LOOKUP, fallback=cx.ROOT_CAUSE_FALLBACK),
            "finding_outcomes": cx.FINDING_OUTCOMES, "severities": cx.SEVERITIES,
            "channels": [(c, cx.CHANNEL_LABELS[c]) for c in PARENT_CONTACT_CHANNELS if c != "portal"],
            "satisfaction_options": [(s, cx.SATISFACTION_LABELS[s]) for s in PARENT_SATISFACTION],
            "status_label": cx.status_label}


def _get(db: Session, id: int, user: User) -> Case:
    """The case, if this user may see it. A case they may not see is a 404, so its existence is not disclosed."""
    c = db.get(Case, id)
    if not c or not cx.can_view(db, user, c):
        raise HTTPException(404, "Case not found")
    return c


def sla_state(c: Case) -> dict:
    if not c.sla_due_at:
        return {"label": "No SLA", "level": "slate", "hours": None}
    remaining = (c.sla_due_at - datetime.utcnow()).total_seconds() / 3600
    if c.status in ("resolved", "closed", "pending_confirmation"):
        return {"label": "Met" if not c.sla_breached else "Breached", "level": "emerald" if not c.sla_breached else "rose", "hours": round(remaining, 1)}
    if c.sla_breached or remaining <= 0:
        return {"label": f"Breached {abs(round(remaining, 1))}h", "level": "rose", "hours": round(remaining, 1)}
    if remaining <= 4:
        return {"label": f"{round(remaining, 1)}h left", "level": "amber", "hours": round(remaining, 1)}
    return {"label": f"{round(remaining, 1)}h left", "level": "emerald", "hours": round(remaining, 1)}


def _status_choices(c: Case) -> list[tuple[str, str]]:
    """Statuses the plain status form may move to. Findings, resolution, confirmation and reopening have forms of their
    own; a complaint with a family never closes from here."""
    out = []
    for s in svc.CASE_STATUSES:
        if s == c.status or s in WORKFLOW_ONLY_STATUSES:
            continue
        if cx.is_complaint(c) and s in ("resolved", "closed"):
            continue
        out.append((s, cx.STATUS_LABELS.get(s, s)))
    return out


# =============================================================================== list
@router.get("", include_in_schema=False)
def list_cases(request: Request, page: int = 1, q: str = "", case_type: str = "", status: str = "", priority: str = "",
               department: str = "", assignee: str = "", client: str = "", breached: str = "", against: str = "",
               db: Session = Depends(get_db), user: User = Depends(require("cases.view"))):
    base = cx.visible(db, user, db.query(Case))
    query = base
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Case.title.ilike(like), Case.case_number.ilike(like), Case.description.ilike(like)))
    if case_type:
        query = query.filter(Case.case_type == case_type)
    if status == "open_all":
        query = query.filter(Case.status.in_(list(OPEN_CASE_STATUSES)))
    elif status:
        query = query.filter(Case.status == status)
    if priority:
        query = query.filter(Case.priority == priority)
    if department:
        query = query.filter(Case.department_id == int(department))
    if assignee == "mine":
        query = query.filter(Case.assigned_to_id == user.id)
    elif assignee:
        query = query.filter(Case.assigned_to_id == int(assignee))
    if client:
        query = query.filter(Case.client_id == int(client))
    if breached:
        query = query.filter(Case.sla_breached.is_(True))
    if parse_int(against):
        query = query.filter(Case.against_employee_id == int(against))
    pg = paginate(query.order_by(Case.created_at.desc()), page, 25)
    counts = dict(base.with_entities(Case.status, func.count(Case.id)).group_by(Case.status).all())
    open_states = list(OPEN_CASE_STATUSES)
    stats = {"open": sum(counts.get(s, 0) for s in open_states), "resolved": counts.get("resolved", 0) + counts.get("closed", 0),
             "confirming": counts.get("pending_confirmation", 0),
             "breached": base.filter(Case.sla_breached.is_(True), Case.status.in_(open_states)).count(),
             "urgent": base.filter(Case.priority == "urgent", Case.status.in_(open_states)).count(),
             "mine": base.filter(Case.assigned_to_id == user.id, Case.status.in_(open_states)).count(),
             "total": sum(counts.values())}
    return render(request, "cases/list.html", {"user": user, "page": pg, "q": q, "case_type": case_type, "status": status, "priority": priority,
                                               "department": department, "assignee": assignee, "client": client, "breached": breached,
                                               "against": against, "stats": stats, "sla_state": sla_state, **_form_ctx(db),
                                               "base_url": (f"/cases?q={q}&case_type={case_type}&status={status}&priority={priority}"
                                                            f"&department={department}&assignee={assignee}&client={client}&breached={breached}"
                                                            f"&against={against}")})


# =============================================================================== intelligence
@router.get("/trends", include_in_schema=False)
def trends(request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.view"))):
    data = cx.intelligence(db, user)
    summary = cx.management_summary(db, user, data)
    all_cases = cx.visible(db, user, db.query(Case)).all()
    db.commit()
    return render(request, "cases/trends.html", {"user": user, "t": svc.case_trends(db, cases=all_cases), "x": data,
                                                 "summary": summary, "status_label": cx.status_label})


# =============================================================================== settings
@router.get("/settings", include_in_schema=False)
def settings_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.configure"))):
    roles = db.query(Role).filter(Role.portal == "admin").order_by(Role.name).all()
    steps = cx.ladder(db)
    by_slug = {r.slug: r for r in roles}
    holders = {slug: cx._users_with_role(db, slug) for slug in steps}
    return render(request, "cases/settings.html", {"user": user, "roles": roles, "steps": steps, "by_slug": by_slug, "holders": holders,
                                                   "days": cx.confirmation_days(db), "attempts": cx.unreachable_attempts(db)})


@router.post("/settings", include_in_schema=False)
async def settings_save(request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.configure"))):
    if not user.is_superuser:
        # The ladder decides who sees every complaint, and its own members hold cases.configure: only a superuser
        # may change it, so nobody can take a step above them off the ladder.
        return redirect("/cases/settings", "Only a superuser can change the complaint settings.", "error")
    form = await request.form()
    rationale = (form.get("rationale") or "").strip()
    if not rationale:
        return redirect("/cases/settings", "A rationale is required.", "error")
    before = {"ladder": cx.ladder(db), "days": cx.confirmation_days(db), "attempts": cx.unreachable_attempts(db)}
    steps = [form.get(f"step_{i}") or "" for i in range(1, 7)]
    try:
        cx.save_settings(db, [s for s in steps if s], parse_int(form.get("days"), 2) or 2, parse_int(form.get("attempts"), 3) or 3)
    except ValueError as exc:
        db.rollback()
        return redirect("/cases/settings", str(exc), "error")
    after = {"ladder": cx.ladder(db), "days": cx.confirmation_days(db), "attempts": cx.unreachable_attempts(db)}
    log_action(db, user, "update", "cases", description="Complaint settings changed (escalation ladder, confirmation)",
               before=before, after=after, rationale=rationale, request=request, consequential=True)
    db.commit()
    return redirect("/cases/settings", "Complaint settings saved.")


# =============================================================================== create
@router.get("/new", include_in_schema=False)
def new_case(request: Request, client_id: int = 0, student_id: int = 0, db: Session = Depends(get_db), user: User = Depends(require("cases.add"))):
    return render(request, "cases/form.html", {"user": user, "client_id": client_id, "student_id": student_id, **_form_ctx(db)})


@router.post("/new", include_in_schema=False)
async def create_case(request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.add"))):
    form = await request.form()
    title = (form.get("title") or "").strip()
    if not title:
        return redirect("/cases/new", "A case title is required.", "error")
    client = db.get(Client, parse_int(form.get("client_id"))) if form.get("client_id") else None
    student = db.get(Student, parse_int(form.get("student_id"))) if form.get("student_id") else None
    teacher = db.get(Teacher, parse_int(form.get("teacher_id"))) if form.get("teacher_id") else None
    dept = db.get(Department, parse_int(form.get("department_id"))) if form.get("department_id") else None
    assignee = db.get(User, parse_int(form.get("assigned_to_id"))) if form.get("assigned_to_id") else None
    against = db.get(Employee, parse_int(form.get("against_employee_id"))) if form.get("against_employee_id") else None
    if against is not None and against.user_id == user.id:
        return redirect("/cases/new", "You cannot open a complaint about yourself; ask a manager to record it.", "error")
    case = svc.open_case(db, form.get("case_type") or "complaint", title, form.get("description"), client=client, student=student,
                         teacher=teacher, raised_by_user=user, source=form.get("source") or "portal",
                         priority=form.get("priority") or None, department_code=dept.code if dept else None,
                         assigned_to=assignee, actor=user, request=request,
                         against_employee_id=against.id if against else None,
                         against_department_id=parse_int(form.get("against_department_id")) or None,
                         against_process=form.get("against_process"), incident_date=parse_date(form.get("incident_date")),
                         complaint_type=form.get("complaint_type"))
    db.commit()
    note = f" It repeats {case.repeat_of.case_number}." if case.repeat_of_id and case.repeat_of else ""
    return redirect(f"/cases/{case.id}",
                    f"Case {case.case_number} opened - {(case.category or 'general').replace('_', ' ')}, {case.priority} priority, response due in {case.sla_hours}h.{note}")


# =============================================================================== detail
@router.get("/{id}", include_in_schema=False)
def case_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.view"))):
    c = _get(db, id, user)
    tasks = db.query(Task).filter(Task.entity_type == "Case", Task.entity_id == c.id).order_by(Task.id.desc()).all()
    events = db.query(AuditEvent).filter(AuditEvent.entity_type == "Case", AuditEvent.entity_id == c.id).order_by(AuditEvent.created_at.desc()).limit(30).all()
    feedback = db.query(Feedback).filter(Feedback.case_id == c.id).all()
    if c.is_sensitive:
        log_action(db, user, "view", "cases", entity=c, request=request, description=f"Opened confidential complaint {c.case_number}")
        db.commit()
    risk = cx.escalation_risk(db, c) if c.status in OPEN_CASE_STATUSES else (0, [])
    next_level, next_person = cx.next_on_ladder(db, c) if cx.is_complaint(c) else (0, None)
    return render(request, "cases/detail.html", {
        "user": user, "c": c, "sla": sla_state(c), "tasks": tasks, "events": events, "feedback": feedback,
        "evidence": cx.evidence(db, c), "contacts": c.contacts, "risk": risk, "is_complaint": cx.is_complaint(c),
        "has_family": cx.has_family(c), "status_choices": _status_choices(c), "ladder_step": cx.ladder_step_name(db, c.escalation_level or 0),
        "next_step": cx.ladder_step_name(db, next_level) if next_person else None, "next_person": next_person,
        "attempts_needed": cx.unreachable_attempts(db), "can_close_unconfirmed": cx.is_case_manager(db, user) or rbac.is_management(user),
        "kind_style": {"decision": "violet", "finding": "indigo", "contact": "emerald", "status": "slate", "system": "slate"},
        **_form_ctx(db, c)})


# =============================================================================== actions
@router.post("/{id}/comment", include_in_schema=False)
async def add_comment(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.update"))):
    c = _get(db, id, user)
    form = await request.form()
    text = (form.get("text") or "").strip()
    if not text:
        return redirect(f"/cases/{c.id}", "The comment cannot be empty.", "error")
    decision = parse_bool(form.get("is_decision"))
    internal = True if decision else parse_bool(form.get("is_internal"))
    cx.add_entry(db, c, user, text, "decision" if decision else "comment", internal)
    if not internal:
        cu = svc.case_client_user(c)
        contact = c.client or (c.student.client if c.student else None)
        if cu:
            notify(db, cu, f"Update on case {c.case_number}", text[:300], event_type="case_update", link=f"/portal/cases/{c.id}",
                   channels=("in_app", "whatsapp"), recipient_address=contact.whatsapp if contact else None)
    log_action(db, user, "decision" if decision else "comment", "cases", entity=c, request=request, consequential=decision,
               description=f"{'Decision recorded' if decision else ('Internal' if internal else 'Client-visible') + ' comment'} on {c.case_number}")
    db.commit()
    if decision:
        return redirect(f"/cases/{c.id}", "Decision recorded.")
    return redirect(f"/cases/{c.id}", "Comment added." if internal else "Comment added and sent to the family.")


@router.post("/{id}/assign", include_in_schema=False)
async def assign(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.assign", "cases.update", any_of=True))):
    c = _get(db, id, user)
    form = await request.form()
    before = {"assigned_to_id": c.assigned_to_id, "department_id": c.department_id, "priority": c.priority}
    uid = parse_int(form.get("assigned_to_id"))
    if uid and uid in cx.subject_user_ids(db, c):
        return redirect(f"/cases/{c.id}", "A complaint cannot be assigned to the person it is about.", "error")
    c.assigned_to_id = uid
    if form.get("department_id"):
        c.department_id = parse_int(form.get("department_id"))
    if form.get("priority") in svc.PRIORITIES:
        c.priority = form.get("priority")
    if uid:
        notify(db, uid, f"Case assigned to you: {c.case_number}", f"{c.title} [{c.priority}]", event_type="case_assigned", link=f"/cases/{c.id}")
    assignee = db.get(User, uid) if uid else None
    cx.add_entry(db, c, user, f"Assigned to {assignee.full_name if assignee else 'nobody'}.", "status")
    log_action(db, user, "assign", "cases", entity=c, description=f"{c.case_number} reassigned", before=before,
               after={"assigned_to_id": c.assigned_to_id, "department_id": c.department_id, "priority": c.priority}, request=request)
    db.commit()
    return redirect(f"/cases/{c.id}", "Case reassigned.")


@router.post("/{id}/escalate", include_in_schema=False)
async def escalate(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.update"))):
    c = _get(db, id, user)
    form = await request.form()
    reason = (form.get("rationale") or form.get("reason") or "").strip()
    if not reason:
        return redirect(f"/cases/{c.id}", "A rationale is required to escalate a case.", "error")
    target = db.get(User, parse_int(form.get("escalated_to_id"))) if form.get("escalated_to_id") else None
    try:
        to = cx.escalate(db, c, user, reason, target, request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(f"/cases/{c.id}", str(exc), "error")
    db.commit()
    who = to.full_name if to else (c.escalated_to.full_name if c.escalated_to else "management")
    return redirect(f"/cases/{c.id}", f"Case escalated to {who}; a risk alert has been raised.", "warning")


@router.post("/{id}/status", include_in_schema=False)
async def change_status(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.update"))):
    c = _get(db, id, user)
    form = await request.form()
    try:
        svc.change_case_status(db, c, form.get("status") or "", user, resolution=form.get("resolution"), root_cause=form.get("root_cause"),
                               note=form.get("note"), request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(f"/cases/{c.id}", str(exc), "error")
    db.commit()
    return redirect(f"/cases/{c.id}", f"Case is now: {cx.status_label(c)}.")


@router.post("/{id}/findings", include_in_schema=False)
async def findings(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.update"))):
    c = _get(db, id, user)
    form = await request.form()
    try:
        cx.record_findings(db, c, user, form.get("investigation_finding") or "", form.get("finding_outcome") or "",
                           form.get("severity") or "", against_employee_id=parse_int(form.get("against_employee_id")) or None,
                           request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(f"/cases/{c.id}", str(exc), "error")
    db.commit()
    return redirect(f"/cases/{c.id}", "Investigation finding recorded.")


@router.post("/{id}/resolve", include_in_schema=False)
async def resolve(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.update"))):
    c = _get(db, id, user)
    form = await request.form()
    try:
        status = cx.resolve(db, c, user, form.get("resolution") or "", form.get("root_cause_category") or "",
                            root_cause=form.get("root_cause"), corrective_action=form.get("corrective_action"),
                            preventive_action=form.get("preventive_action"), request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(f"/cases/{c.id}", str(exc), "error")
    db.commit()
    if status == "pending_confirmation":
        return redirect(f"/cases/{c.id}", "Resolved. A follow-up to confirm with the family has been created; the complaint closes when they confirm.")
    return redirect(f"/cases/{c.id}", "Case resolved.")


@router.post("/{id}/contact", include_in_schema=False)
async def parent_contact(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.update"))):
    c = _get(db, id, user)
    form = await request.form()
    when = form.get("contacted_at")
    try:
        contacted_at = datetime.fromisoformat(when) if when else datetime.utcnow()
    except ValueError:
        contacted_at = datetime.utcnow()
    data = {k: form.get(k) for k in ("satisfaction", "channel", "parent_response", "feedback", "agreed_action", "recording_url", "transcript")}
    data.update({"contacted_at": contacted_at, "follow_up_date": parse_date(form.get("follow_up_date")),
                 "responsible_id": parse_int(form.get("responsible_id")) or None, "referral_mentioned": parse_bool(form.get("referral_mentioned"))})
    upload = form.get("recording")
    try:
        if upload is not None and getattr(upload, "filename", ""):
            att = await cx.save_evidence(db, c, user, upload, f"Call recording {contacted_at:%d %b %Y %H:%M}")
            data["recording_attachment_id"] = att.id
        pc = cx.record_contact(db, c, user, data, request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(f"/cases/{c.id}", str(exc), "error")
    db.commit()
    messages = {"closed": "Family confirmed. The complaint is closed.",
                "awaiting": "Recorded. Another confirmation attempt is scheduled for tomorrow.",
                "reopened": "Family not fully satisfied. The complaint is reopened.",
                "escalated": "Family not satisfied. The complaint is reopened and escalated."}
    return redirect(f"/cases/{c.id}", messages.get(pc.outcome, "Conversation recorded."), "warning" if pc.outcome in ("reopened", "escalated") else "success")


@router.post("/{id}/reopen", include_in_schema=False)
async def reopen(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.update"))):
    c = _get(db, id, user)
    form = await request.form()
    try:
        cx.reopen(db, c, user, form.get("reason") or "", request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(f"/cases/{c.id}", str(exc), "error")
    db.commit()
    return redirect(f"/cases/{c.id}", "Case reopened.", "warning")


@router.post("/{id}/close-unconfirmed", include_in_schema=False)
async def close_unconfirmed(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.update"))):
    c = _get(db, id, user)
    form = await request.form()
    try:
        cx.close_without_confirmation(db, c, user, form.get("reason") or "", request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(f"/cases/{c.id}", str(exc), "error")
    db.commit()
    return redirect(f"/cases/{c.id}", "Complaint closed without the family's confirmation.", "warning")


@router.post("/{id}/evidence", include_in_schema=False)
async def evidence_upload(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.update"))):
    c = _get(db, id, user)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        return redirect(f"/cases/{c.id}", "Choose a file to attach.", "error")
    try:
        att = await cx.save_evidence(db, c, user, upload, form.get("title") or "")
    except ValueError as exc:
        db.rollback()
        return redirect(f"/cases/{c.id}", str(exc), "error")
    db.commit()
    return redirect(f"/cases/{c.id}", f"Evidence '{att.title}' attached.")


@router.get("/{id}/evidence/{att_id}", include_in_schema=False)
def evidence_download(id: int, att_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.view"))):
    c = _get(db, id, user)
    att = db.get(Attachment, att_id)
    if att is None or att.entity_type != "case" or att.entity_id != c.id or att.status != "active":
        raise HTTPException(404, "File not found")
    path = cx.evidence_path(att)
    if path is None:
        raise HTTPException(404, "File not found")
    log_action(db, user, "download", "cases", entity=c, request=request, description=f"Downloaded evidence '{att.title}' from {c.case_number}")
    db.commit()
    inline = (att.content_type or "").startswith(("image/", "audio/", "video/", "application/pdf"))
    return FileResponse(path, media_type=att.content_type or "application/octet-stream", filename=att.file_name or path.name,
                        content_disposition_type="inline" if inline else "attachment")


@router.post("/{id}/task", include_in_schema=False)
async def create_task(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.update"))):
    c = _get(db, id, user)
    form = await request.form()
    title = (form.get("title") or "").strip() or f"Action for case {c.case_number}"
    assignee_id = parse_int(form.get("assignee_id")) or c.assigned_to_id
    if assignee_id and assignee_id in cx.subject_user_ids(db, c):
        return redirect(f"/cases/{c.id}", "Actions on a complaint cannot be given to the person it is about.", "error")
    t = Task(title=title, description=form.get("description") or c.title, assignee_id=assignee_id,
             creator_id=user.id, department_id=c.department_id, priority=c.priority, status="todo",
             due_date=parse_date(form.get("due_date")) or (c.sla_due_at.date() if c.sla_due_at else date.today() + timedelta(days=2)),
             entity_type="Case", entity_id=c.id)
    db.add(t)
    db.flush()
    db.add(CaseComment(case_id=c.id, user_id=user.id, is_internal=True, kind="status", text=f"Action assigned: {title}"))
    log_action(db, user, "create", "tasks", entity=t, description=f"Task linked to case {c.case_number}", request=request)
    if assignee_id and assignee_id != user.id:
        notify(db, assignee_id, f"Action on case {c.case_number}", title, event_type="task_assigned", link=f"/cases/{c.id}")
    db.commit()
    return redirect(f"/cases/{c.id}", "Action assigned.")
