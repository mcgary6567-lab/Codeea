"""Complaints, requests & cases (Module 32): AI classification, SLA countdown, escalation, resolution workflow, trends."""
from __future__ import annotations

from datetime import datetime, date, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import require, csrf_protect, get_user_context, UserContext
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import redirect, paginate, parse_date, parse_int, parse_bool
from app.database import get_db
from app.models.core import User, Department, Role, AuditEvent
from app.models.crm import Case, CaseComment, Feedback
from app.services.crm import OPEN_CASE_STATUSES
from app.models.ops import Task
from app.models.people import Client, Student, Teacher
from app.services import crm as svc

router = APIRouter(prefix="/cases", dependencies=[Depends(csrf_protect)])


def _form_ctx(db: Session) -> dict:
    staff = (db.query(User).join(Role, Role.id == User.role_id)
             .filter(User.is_active.is_(True), Role.portal == "admin").order_by(User.full_name).all())
    departments = db.query(Department).filter(Department.is_active.is_(True)).order_by(Department.name).all()
    clients = db.query(Client).order_by(Client.full_name).limit(500).all()
    students = db.query(Student).order_by(Student.full_name).limit(600).all()
    teachers = db.query(Teacher).order_by(Teacher.full_name).all()
    return {"types": svc.CASE_TYPES, "priorities": svc.PRIORITIES, "statuses": svc.CASE_STATUSES,
            "departments": departments, "staff": staff, "clients": clients, "students": students, "teachers": teachers,
            "department_options": [(d.id, d.name) for d in departments],
            "staff_options": [(u.id, u.full_name) for u in staff],
            "client_options": [(c.id, f"{c.full_name} ({c.client_code})") for c in clients],
            "student_options": [(s.id, f"{s.full_name} ({s.student_code})") for s in students],
            "teacher_options": [(t.id, t.full_name) for t in teachers]}


def _get(db: Session, id: int) -> Case:
    c = db.get(Case, id)
    if not c:
        raise HTTPException(404, "Case not found")
    return c


def sla_state(c: Case) -> dict:
    if not c.sla_due_at:
        return {"label": "No SLA", "level": "slate", "hours": None}
    remaining = (c.sla_due_at - datetime.utcnow()).total_seconds() / 3600
    if c.status in ("resolved", "closed"):
        return {"label": "Met" if not c.sla_breached else "Breached", "level": "emerald" if not c.sla_breached else "rose", "hours": round(remaining, 1)}
    if c.sla_breached or remaining <= 0:
        return {"label": f"Breached {abs(round(remaining, 1))}h", "level": "rose", "hours": round(remaining, 1)}
    if remaining <= 4:
        return {"label": f"{round(remaining, 1)}h left", "level": "amber", "hours": round(remaining, 1)}
    return {"label": f"{round(remaining, 1)}h left", "level": "emerald", "hours": round(remaining, 1)}


@router.get("", include_in_schema=False)
def list_cases(request: Request, page: int = 1, q: str = "", case_type: str = "", status: str = "", priority: str = "",
               department: str = "", assignee: str = "", client: str = "", breached: str = "",
               db: Session = Depends(get_db), user: User = Depends(require("cases.view"))):
    query = db.query(Case)
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
    pg = paginate(query.order_by(Case.created_at.desc()), page, 25)
    counts = dict(db.query(Case.status, func.count(Case.id)).group_by(Case.status).all())
    stats = {"open": sum(counts.get(s, 0) for s in OPEN_CASE_STATUSES), "resolved": counts.get("resolved", 0) + counts.get("closed", 0),
             "breached": db.query(func.count(Case.id)).filter(Case.sla_breached.is_(True), Case.status.in_(list(OPEN_CASE_STATUSES))).scalar() or 0,
             "urgent": db.query(func.count(Case.id)).filter(Case.priority == "urgent", Case.status.in_(list(OPEN_CASE_STATUSES))).scalar() or 0,
             "mine": db.query(func.count(Case.id)).filter(Case.assigned_to_id == user.id, Case.status.in_(list(OPEN_CASE_STATUSES))).scalar() or 0,
             "total": sum(counts.values())}
    return render(request, "cases/list.html", {"user": user, "page": pg, "q": q, "case_type": case_type, "status": status, "priority": priority,
                                               "department": department, "assignee": assignee, "client": client, "breached": breached,
                                               "stats": stats, "sla_state": sla_state, **_form_ctx(db),
                                               "base_url": (f"/cases?q={q}&case_type={case_type}&status={status}&priority={priority}"
                                                            f"&department={department}&assignee={assignee}&client={client}&breached={breached}")})


@router.get("/trends", include_in_schema=False)
def trends(request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.view"))):
    return render(request, "cases/trends.html", {"user": user, "t": svc.case_trends(db)})


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
    case = svc.open_case(db, form.get("case_type") or "complaint", title, form.get("description"), client=client, student=student,
                         teacher=teacher, raised_by_user=user, source=form.get("source") or "portal",
                         priority=form.get("priority") or None, department_code=dept.code if dept else None,
                         assigned_to=assignee, actor=user, request=request)
    db.commit()
    return redirect(f"/cases/{case.id}",
                    f"Case {case.case_number} opened - AI classified as {case.category} / {case.priority}, SLA {case.sla_hours}h.")


@router.get("/{id}", include_in_schema=False)
def case_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.view"))):
    c = _get(db, id)
    tasks = db.query(Task).filter(Task.entity_type == "Case", Task.entity_id == c.id).order_by(Task.id.desc()).all()
    events = db.query(AuditEvent).filter(AuditEvent.entity_type == "Case", AuditEvent.entity_id == c.id).order_by(AuditEvent.created_at.desc()).limit(30).all()
    feedback = db.query(Feedback).filter(Feedback.case_id == c.id).all()
    return render(request, "cases/detail.html", {"user": user, "c": c, "sla": sla_state(c), "tasks": tasks, "events": events,
                                                 "feedback": feedback, **_form_ctx(db)})


@router.post("/{id}/comment", include_in_schema=False)
async def add_comment(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.update"))):
    c = _get(db, id)
    form = await request.form()
    text = (form.get("text") or "").strip()
    if not text:
        return redirect(f"/cases/{c.id}", "The comment cannot be empty.", "error")
    internal = parse_bool(form.get("is_internal"))
    db.add(CaseComment(case_id=c.id, user_id=user.id, text=text, is_internal=internal))
    if not internal:
        cu = svc.case_client_user(c)
        contact = c.client or (c.student.client if c.student else None)
        if cu:
            notify(db, cu, f"Update on case {c.case_number}", text[:300], event_type="case_update", link="/portal/cases",
                   channels=("in_app", "whatsapp"), recipient_address=contact.whatsapp if contact else None)
    log_action(db, user, "comment", "cases", entity=c, description=f"{'Internal' if internal else 'Client-visible'} comment on {c.case_number}")
    db.commit()
    return redirect(f"/cases/{c.id}", "Comment added." if internal else "Comment added and sent to the family.")


@router.post("/{id}/assign", include_in_schema=False)
async def assign(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.assign", "cases.update", any_of=True))):
    c = _get(db, id)
    form = await request.form()
    before = {"assigned_to_id": c.assigned_to_id, "department_id": c.department_id}
    uid = parse_int(form.get("assigned_to_id"))
    c.assigned_to_id = uid
    if form.get("department_id"):
        c.department_id = parse_int(form.get("department_id"))
    if form.get("priority") in svc.PRIORITIES:
        c.priority = form.get("priority")
    if uid:
        notify(db, uid, f"Case assigned to you: {c.case_number}", f"{c.title} [{c.priority}]", event_type="case_assigned", link=f"/cases/{c.id}")
    db.add(CaseComment(case_id=c.id, user_id=user.id, is_internal=True, text=f"Reassigned to user #{uid or 'unassigned'}."))
    log_action(db, user, "assign", "cases", entity=c, description=f"{c.case_number} reassigned", before=before,
               after={"assigned_to_id": c.assigned_to_id, "department_id": c.department_id}, request=request)
    db.commit()
    return redirect(f"/cases/{c.id}", "Case reassigned.")


@router.post("/{id}/escalate", include_in_schema=False)
async def escalate(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.update"))):
    c = _get(db, id)
    form = await request.form()
    reason = (form.get("rationale") or form.get("reason") or "").strip()
    if not reason:
        return redirect(f"/cases/{c.id}", "A rationale is required to escalate a case.", "error")
    target = db.get(User, parse_int(form.get("escalated_to_id"))) if form.get("escalated_to_id") else None
    svc.escalate_case(db, c, user, reason, target, request=request)
    db.commit()
    return redirect(f"/cases/{c.id}", "Case escalated and a risk alert has been raised.", "warning")


@router.post("/{id}/status", include_in_schema=False)
async def change_status(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.update"))):
    c = _get(db, id)
    form = await request.form()
    try:
        svc.change_case_status(db, c, form.get("status") or "", user, resolution=form.get("resolution"), root_cause=form.get("root_cause"),
                               note=form.get("note"), request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(f"/cases/{c.id}", str(exc), "error")
    db.commit()
    return redirect(f"/cases/{c.id}", f"Case marked {c.status.replace('_', ' ')}.")


@router.post("/{id}/task", include_in_schema=False)
async def create_task(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.update"))):
    c = _get(db, id)
    form = await request.form()
    title = (form.get("title") or "").strip() or f"Action for case {c.case_number}"
    t = Task(title=title, description=form.get("description") or c.title, assignee_id=parse_int(form.get("assignee_id")) or c.assigned_to_id,
             creator_id=user.id, department_id=c.department_id, priority=c.priority, status="todo",
             due_date=parse_date(form.get("due_date")) or (c.sla_due_at.date() if c.sla_due_at else date.today() + timedelta(days=2)),
             entity_type="Case", entity_id=c.id)
    db.add(t)
    db.flush()
    db.add(CaseComment(case_id=c.id, user_id=user.id, is_internal=True, text=f"Linked task created: {title}"))
    log_action(db, user, "create", "tasks", entity=t, description=f"Task linked to case {c.case_number}", request=request)
    db.commit()
    return redirect(f"/cases/{c.id}", "Linked task created.")
