"""Parent contact log (docs/PARENT_COMMUNICATION.md): record any conversation with a family, follow up what it
identified, raise it as a complaint, and catch referrals."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import paginate, parse_bool, parse_date, parse_int, redirect
from app.database import get_db
from app.models.core import Role, User
from app.models.crm import ISSUE_CATEGORIES, PARENT_CONTACT_CHANNELS, PARENT_CONTACT_PURPOSES, PARENT_SENTIMENTS, ParentContact
from app.models.hr_erp import Attachment
from app.models.ops import Task
from app.models.people import Client, Student
from app.services import contacts as svc

router = APIRouter(prefix="/parent-contacts", dependencies=[Depends(csrf_protect)])


def _get(db: Session, id: int, user: User) -> ParentContact:
    pc = db.get(ParentContact, id)
    if not pc or not svc.can_view(db, user, pc):
        raise HTTPException(404, "Conversation not found")
    return pc


def _staff_options(db: Session) -> list[tuple[int, str]]:
    staff = (db.query(User).join(Role, Role.id == User.role_id)
             .filter(User.is_active.is_(True), Role.portal == "admin").order_by(User.full_name).all())
    return [(u.id, u.full_name) for u in staff]


def _ctx(db: Session) -> dict:
    return {"purposes": [p for p in PARENT_CONTACT_PURPOSES if p[0] != "complaint_confirmation"], "purpose_labels": svc.PURPOSE_LABELS,
            "channels": [(c, svc.CHANNEL_LABELS[c]) for c in PARENT_CONTACT_CHANNELS if c != "portal"],
            "channel_labels": svc.CHANNEL_LABELS, "sentiments": PARENT_SENTIMENTS, "sentiment_labels": svc.SENTIMENT_LABELS,
            "issue_categories": ISSUE_CATEGORIES, "issue_labels": svc.ISSUE_LABELS, "relations": svc.RELATIONS,
            "staff_options": _staff_options(db), "follow_up_state": svc.follow_up_state, "referral_stage": svc.referral_stage}


@router.get("", include_in_schema=False)
def contact_list(request: Request, page: int = 1, q: str = "", purpose: str = "", staff: str = "", follow_up: str = "",
                 issue: str = "", referral: str = "", date_from: str = "", date_to: str = "", client: str = "",
                 db: Session = Depends(get_db), user: User = Depends(require("parent_contacts.view"))):
    query = svc.visible(db, user, db.query(ParentContact))
    if q:
        query = query.join(Client, Client.id == ParentContact.client_id).filter(or_(
            Client.full_name.ilike(f"%{q}%"), ParentContact.summary.ilike(f"%{q}%"), ParentContact.parent_response.ilike(f"%{q}%"),
            ParentContact.issue.ilike(f"%{q}%")))
    if purpose:
        query = query.filter(ParentContact.purpose == purpose)
    if parse_int(staff):
        query = query.filter(ParentContact.contacted_by_id == int(staff))
    if parse_int(client):
        query = query.filter(ParentContact.client_id == int(client))
    if issue:
        query = query.filter(ParentContact.issue.isnot(None))
    if referral:
        query = query.filter(ParentContact.referral_mentioned.is_(True))
    if follow_up in ("open", "overdue", "mine"):
        query = query.join(Task, Task.id == ParentContact.follow_up_task_id).filter(Task.status.in_(svc.OPEN_TASK))
        if follow_up == "overdue":
            query = query.filter(Task.due_date < svc.org_today())
        if follow_up == "mine":
            query = query.filter(Task.assignee_id == user.id)
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(ParentContact.contacted_at >= datetime.combine(df, datetime.min.time()))
    if dt:
        query = query.filter(ParentContact.contacted_at <= datetime.combine(dt, datetime.max.time()))
    pg = paginate(query.order_by(ParentContact.contacted_at.desc()), page, 30)
    return render(request, "parent_contacts/list.html", {
        "user": user, "page": pg, "stats": svc.stats(db, user), "q": q, "purpose": purpose, "staff": staff, "follow_up": follow_up,
        "issue": issue, "referral": referral, "date_from": date_from, "date_to": date_to, "client": client, **_ctx(db),
        "base_url": (f"/parent-contacts?q={q}&purpose={purpose}&staff={staff}&follow_up={follow_up}&issue={issue}&referral={referral}"
                     f"&date_from={date_from}&date_to={date_to}&client={client}")})


@router.get("/new", include_in_schema=False)
def new_contact(request: Request, client_id: int = 0, student_id: int = 0, purpose: str = "", db: Session = Depends(get_db),
                user: User = Depends(require("parent_contacts.add"))):
    family = db.get(Client, client_id) if client_id else None
    student = db.get(Student, student_id) if student_id else None
    if student and not family:
        family = student.client
    clients = db.query(Client).order_by(Client.full_name).limit(800).all()
    return render(request, "parent_contacts/form.html", {
        "user": user, "family": family, "student": student, "purpose": purpose or ("academic" if student else "general"),
        "client_options": [(c.id, f"{c.full_name} ({c.client_code})") for c in clients],
        "student_options": [(s.id, s.full_name) for s in family.students] if family else [],
        "students_by_client": {c.id: [(s.id, s.full_name) for s in c.students] for c in clients}, "now": datetime.utcnow(), **_ctx(db)})


@router.post("/new", include_in_schema=False)
async def create_contact(request: Request, db: Session = Depends(get_db), user: User = Depends(require("parent_contacts.add"))):
    form = await request.form()
    family = db.get(Client, parse_int(form.get("client_id")) or 0)
    if not family:
        return redirect("/parent-contacts/new", "Choose the family.", "error")
    student = db.get(Student, parse_int(form.get("student_id")) or 0) if form.get("student_id") else None
    when = form.get("contacted_at")
    try:
        contacted_at = datetime.fromisoformat(when) if when else datetime.utcnow()
    except ValueError:
        contacted_at = datetime.utcnow()
    data = {k: form.get(k) for k in ("purpose", "channel", "direction", "summary", "parent_response", "sentiment", "issue_category",
                                     "agreed_action", "recording_url", "transcript", "referred_name", "referred_phone", "referred_email",
                                     "referred_relation", "referral_note")}
    data["issue"] = form.get("issue") if parse_bool(form.get("has_issue")) else None
    if not data["issue"]:
        data["issue_category"] = None
    data.update({"contacted_at": contacted_at, "duration_minutes": parse_int(form.get("duration_minutes")) or None,
                 "follow_up_date": parse_date(form.get("follow_up_date")), "responsible_id": parse_int(form.get("responsible_id")) or None,
                 "referral_mentioned": parse_bool(form.get("referral_mentioned"))})
    back = f"/parent-contacts/new?client_id={family.id}" + (f"&student_id={student.id}" if student else "")
    try:
        pc = svc.record(db, family, user, data, student=student, request=request)
        upload = form.get("recording")
        if upload is not None and getattr(upload, "filename", ""):
            await svc.save_recording(db, pc, user, upload)
    except ValueError as exc:
        db.rollback()
        return redirect(back, str(exc), "error")
    db.commit()
    bits = ["Conversation recorded."]
    if pc.follow_up_task_id:
        bits.append(f"Follow-up created for {pc.responsible.full_name if pc.responsible else 'the responsible person'}, due {pc.follow_up_date:%d %b}.")
    if pc.referral_id:
        bits.append(f"Referral recorded ({svc.referral_stage(pc.referral)}).")
    return redirect(f"/parent-contacts/{pc.id}", " ".join(bits))


@router.get("/{id}", include_in_schema=False)
def contact_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("parent_contacts.view"))):
    pc = _get(db, id, user)
    recording = db.get(Attachment, pc.recording_attachment_id) if pc.recording_attachment_id else None
    history = (svc.visible(db, user, db.query(ParentContact).filter(ParentContact.client_id == pc.client_id, ParentContact.id != pc.id))
               .order_by(ParentContact.contacted_at.desc()).limit(10).all())
    referral_tasks = (db.query(Task).filter(Task.entity_type == "Referral", Task.entity_id == pc.referral_id).all() if pc.referral_id else [])
    return render(request, "parent_contacts/detail.html", {
        "user": user, "pc": pc, "recording": recording, "history": history, "referral_tasks": referral_tasks,
        "can_update": rbac.has_permission(user, "parent_contacts.update"),
        "can_raise": rbac.has_permission(user, "cases.add") and not pc.case_id, **_ctx(db)})


@router.post("/{id}/follow-up/done", include_in_schema=False)
async def follow_up_done(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("parent_contacts.update"))):
    pc = _get(db, id, user)
    form = await request.form()
    try:
        svc.complete_follow_up(db, pc, user, form.get("note") or "", request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(f"/parent-contacts/{pc.id}", str(exc), "error")
    db.commit()
    return redirect(f"/parent-contacts/{pc.id}", "Follow-up marked done.")


@router.post("/{id}/complaint", include_in_schema=False)
async def raise_complaint(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.add"))):
    pc = _get(db, id, user)
    try:
        case = svc.raise_complaint(db, pc, user, request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(f"/parent-contacts/{pc.id}", str(exc), "error")
    db.commit()
    return redirect(f"/cases/{case.id}", f"Complaint {case.case_number} opened from the conversation.")


@router.get("/{id}/recording", include_in_schema=False)
def recording(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("parent_contacts.view"))):
    pc = _get(db, id, user)
    att = db.get(Attachment, pc.recording_attachment_id) if pc.recording_attachment_id else None
    path = svc.recording_path(att) if att and att.entity_type == "parent_contact" and att.entity_id == pc.id else None
    if path is None:
        raise HTTPException(404, "Recording not found")
    log_action(db, user, "download", "parent_contacts", entity=pc, request=request, description="Opened the recording of a parent conversation")
    db.commit()
    return FileResponse(path, media_type=att.content_type or "application/octet-stream", filename=att.file_name or path.name,
                        content_disposition_type="inline")
