"""ERP "Client Requests" (docs/AUDIT_ACADEMICS.md 3.3).

Five approval lists that share one layout: Leave Applications, Time Change Requests, Teacher Change Requests,
Refer New Contacts and Complaints. Each page has the Approval Status tiles (Pending / Approved / Rejected /
Cancelled), a filter bar, a bordered table with the ERP's column labels, a Create form for staff and the
"Actions -> Change Status" bulk form (row checkboxes + new status + remarks) posting to
``/requests/<kind>/change-status``. Every status change is audited with the remarks as the rationale and the
family is notified in-app.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.notify import notify
from app.core.templating import label as status_label, render
from app.core.utils import next_code, paginate, parse_bool, parse_date, parse_int, redirect
from app.database import get_db
from app.models.core import User
from app.models.crm import Case, Lead, LeadSource
from app.models.erp import ReferredContact, SessionSlot, TeacherChangeRequest, TimeChangeRequest
from app.models.finance import Subscription
from app.models.people import Client, Leave, Student, Teacher
from app.models.scheduling import ClassSession, Schedule

router = APIRouter(prefix="/requests", dependencies=[Depends(csrf_protect)])

STATUSES = ["pending", "approved", "rejected", "cancelled"]
TILES = [("Pending", "pending", "clock"), ("Approved", "approved", "check-circle-2"),
         ("Rejected", "rejected", "x-circle"), ("Cancelled", "cancelled", "ban")]
LEAVE_TYPES = ["casual", "sick", "vacation", "emergency", "exam"]
REFERENCE_TYPES = ["family", "friend", "colleague", "community", "other"]
COMPLAINT_TYPES = ["Teacher", "Timing", "Billing", "Technical", "Behaviour", "Other"]

# kind -> page metadata. ``headers`` are the ERP's column labels before the trailing Status / Created At pair.
KINDS = {
    "leaves": {"title": "Leave Applications", "icon": "calendar-off",
               "subtitle": "Student leave applied by the family. Approving records the approver and the approval time.",
               "headers": ["Leave ID", "Leave For All", "Student", "Shift", "From Date", "To Date", "Description"]},
    "time-change": {"title": "Time Change Requests", "icon": "clock",
                    "subtitle": "Families asking to move a subscription to another session time.",
                    "headers": ["ID", "Client", "Shift", "Student", "Subscription ID", "Current Session", "New Session",
                                "Description", "Days"]},
    "teacher-change": {"title": "Teacher Change Requests", "icon": "user-cog",
                       "subtitle": "Families asking for a different teacher on a subscription.",
                       "headers": ["ID", "Client", "Shift", "Student", "Subscription ID", "Current Teacher",
                                   "New Teacher", "Description"]},
    "references": {"title": "Refer New Contacts", "icon": "gift",
                   "subtitle": "Contacts recommended by our families. Approving creates a lead credited to the referrer.",
                   "headers": ["ID", "Client", "Name", "Shift", "Email", "Contact No", "Description", "Reference Type"]},
    "complaints": {"title": "Complaints", "icon": "life-buoy",
                   "subtitle": "Complaints raised by families. Approving publishes the company response and resolves the case.",
                   "headers": ["ID", "Client", "Shift", "Complaint Type", "Title", "Description", "Company Response"]},
}


# --------------------------------------------------------------------------- helpers
def _d(value) -> str:
    return value.strftime("%d %b %Y") if value else ""


def _shift(client: Client | None) -> str:
    return (client.shift or "").title() if client else ""


def _days_label(days) -> str:
    from app.services.scheduling import day_label
    return day_label(days or [])


def _slot_label(slot: SessionSlot | None) -> str:
    return slot.label if slot else ""


def _client_options(db: Session) -> list[tuple[int, str]]:
    return [(c.id, f"{c.client_code} - {c.full_name}") for c in
            db.query(Client).order_by(Client.full_name).limit(500)]


def _student_options(db: Session) -> list[tuple[int, str]]:
    return [(s.id, f"{s.student_code} - {s.full_name}") for s in
            db.query(Student).order_by(Student.full_name).limit(800)]


def _subscription_options(db: Session) -> list[tuple[int, str]]:
    rows = (db.query(Subscription).filter(Subscription.status.notin_(["cancelled", "expired"]))
            .order_by(Subscription.id.desc()).limit(400).all())
    return [(s.id, f"{s.subscription_code} - {s.student.full_name if s.student else ''}") for s in rows]


def _slot_options(db: Session) -> list[tuple[int, str]]:
    rows = (db.query(SessionSlot).filter(SessionSlot.status == "active")
            .order_by(SessionSlot.category, SessionSlot.start_time).all())
    return [(s.id, f"{s.category}: {s.label}") for s in rows]


def _teacher_options(db: Session) -> list[tuple[int, str]]:
    return [(t.id, f"{t.teacher_code} - {t.full_name}") for t in
            db.query(Teacher).filter(Teacher.status == "active").order_by(Teacher.full_name).all()]


def _options(db: Session) -> dict:
    return {"client_options": _client_options(db), "student_options": _student_options(db),
            "subscription_options": _subscription_options(db), "slot_options": _slot_options(db),
            "teacher_options": _teacher_options(db), "leave_types": LEAVE_TYPES,
            "reference_types": REFERENCE_TYPES, "complaint_types": COMPLAINT_TYPES,
            "day_options": [(0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri"), (5, "Sat"), (6, "Sun")]}


def _notify_client(db: Session, client: Client | None, title: str, body: str, link: str) -> None:
    if client and client.user_id:
        notify(db, client.user_id, title, body, event_type="request_status", link=link)


def _counts(db: Session, model, status_col, *filters) -> dict:
    q = db.query(status_col, func.count(model.id))
    for f in filters:
        q = q.filter(f)
    rows = dict(q.group_by(status_col).all())
    out = {s: rows.get(s, 0) for s in STATUSES}
    out["total"] = sum(rows.values())
    return out


def _render(request: Request, user: User, db: Session, kind: str, pg, counts: dict, rows: list[dict],
            filters: dict, base_url: str, extra: dict | None = None):
    meta = KINDS[kind]
    ctx = {"user": user, "kind": kind, "meta": meta, "page": pg, "rows": rows, "counts": counts,
           "tiles": TILES, "statuses": STATUSES, "filters": filters, "base_url": base_url,
           "today_iso": date.today().isoformat(),
           "can_add": rbac.has_permission(user, "requests.add"),
           "can_change": rbac.has_permission(user, "requests.update") or rbac.has_permission(user, "requests.approve"),
           "kind_tabs": [(k, v["title"], f"/requests/{k}") for k, v in KINDS.items()],
           "status_options": [(s, status_label(s, "request")) for s in STATUSES]}
    ctx.update(_options(db))
    ctx.update(extra or {})
    return render(request, "requests/list.html", ctx)


def _filters(q: str, status: str, client: str, student: str, date_from: str, date_to: str) -> dict:
    return {"q": q, "status": status, "client": client, "student": student, "date_from": date_from, "date_to": date_to}


def _base(kind: str, f: dict) -> str:
    return (f"/requests/{kind}?status={f['status']}&client={f['client']}&student={f['student']}"
            f"&date_from={f['date_from']}&date_to={f['date_to']}&q={f['q']}")


# =============================================================================== Leave Applications
@router.get("/leaves", include_in_schema=False)
def leaves(request: Request, page: int = 1, status: str = "", client: str = "", student: str = "",
           date_from: str = "", date_to: str = "", q: str = "", db: Session = Depends(get_db),
           user: User = Depends(require("requests.view"))):
    f = _filters(q, status, client, student, date_from, date_to)
    query = db.query(Leave).filter(Leave.person_type == "student")
    if status:
        query = query.filter(Leave.status == status)
    if parse_int(student):
        query = query.filter(Leave.student_id == int(student))
    if parse_int(client):
        query = query.filter(Leave.student_id.in_(
            [r[0] for r in db.query(Student.id).filter(Student.client_id == int(client))] or [-1]))
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(Leave.end_date >= df)
    if dt:
        query = query.filter(Leave.start_date <= dt)
    pg = paginate(query.order_by(Leave.id.desc()), page, 25)
    rows = []
    for lv in pg.items:
        st = lv.student
        rows.append({"id": lv.id, "status": lv.status, "created_at": lv.created_at,
                     "cells": [f"LV-{lv.id:05d}", "Yes" if lv.leave_for_all else "No",
                               st.full_name if st else "", _shift(st.client if st else None),
                               _d(lv.start_date), _d(lv.end_date), lv.reason]})
    counts = _counts(db, Leave, Leave.status, Leave.person_type == "student")
    return _render(request, user, db, "leaves", pg, counts, rows, f, _base("leaves", f))


@router.post("/leaves/new", include_in_schema=False)
async def leave_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("requests.add"))):
    form = await request.form()
    st = db.get(Student, parse_int(form.get("student_id")) or 0)
    start, end = parse_date(form.get("start_date")), parse_date(form.get("end_date"))
    if not st or not start or not end or end < start:
        return redirect("/requests/leaves", "Select a student and a valid date range.", "error")
    lv = Leave(person_type="student", student_id=st.id, leave_type=form.get("leave_type") or "casual",
               start_date=start, end_date=end, reason=(form.get("reason") or "").strip() or None,
               leave_detail=(form.get("leave_detail") or "").strip() or None,
               leave_for_all=parse_bool(form.get("leave_for_all")),
               apply_date=parse_date(form.get("apply_date")) or date.today(),
               status="pending", requested_by_id=user.id)
    db.add(lv)
    db.flush()
    log_action(db, user, "create", "requests", entity=lv, request=request,
               description=f"Leave application created for {st.full_name} ({start} to {end})", rationale=lv.reason)
    db.commit()
    return redirect("/requests/leaves?status=pending", f"Leave application created for {st.full_name}.")


# =============================================================================== Time Change Requests
@router.get("/time-change", include_in_schema=False)
def time_change(request: Request, page: int = 1, status: str = "", client: str = "", student: str = "",
                date_from: str = "", date_to: str = "", q: str = "", db: Session = Depends(get_db),
                user: User = Depends(require("requests.view"))):
    f = _filters(q, status, client, student, date_from, date_to)
    query = db.query(TimeChangeRequest)
    if status:
        query = query.filter(TimeChangeRequest.status == status)
    if parse_int(client):
        query = query.filter(TimeChangeRequest.client_id == int(client))
    if parse_int(student):
        query = query.filter(TimeChangeRequest.student_id == int(student))
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(func.date(TimeChangeRequest.created_at) >= df)
    if dt:
        query = query.filter(func.date(TimeChangeRequest.created_at) <= dt)
    pg = paginate(query.order_by(TimeChangeRequest.id.desc()), page, 25)
    rows = []
    for r in pg.items:
        rows.append({"id": r.id, "status": r.status, "created_at": r.created_at,
                     "cells": [r.id, r.client.full_name if r.client else "", _shift(r.client),
                               r.student.full_name if r.student else "",
                               r.subscription.subscription_code if r.subscription else "",
                               _slot_label(r.current_slot), _slot_label(r.new_slot), r.description,
                               _days_label(r.days)]})
    counts = _counts(db, TimeChangeRequest, TimeChangeRequest.status)
    return _render(request, user, db, "time-change", pg, counts, rows, f, _base("time-change", f))


@router.post("/time-change/new", include_in_schema=False)
async def time_change_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("requests.add"))):
    form = await request.form()
    sub = db.get(Subscription, parse_int(form.get("subscription_id")) or 0)
    if not sub:
        return redirect("/requests/time-change", "Select the subscription to move.", "error")
    days = [int(d) for d in form.getlist("days") if str(d).isdigit()]
    req = TimeChangeRequest(client_id=sub.client_id, student_id=sub.student_id, subscription_id=sub.id,
                            current_slot_id=sub.slot_id, new_slot_id=parse_int(form.get("new_slot_id")),
                            days=days or list(sub.days_of_week or []),
                            description=(form.get("description") or "").strip() or None,
                            status="pending", requested_by_id=user.id)
    db.add(req)
    db.flush()
    log_action(db, user, "create", "requests", entity=req, request=request, rationale=req.description,
               description=f"Time change request created for {sub.subscription_code}")
    db.commit()
    return redirect("/requests/time-change?status=pending", "Time change request created.")


# =============================================================================== Teacher Change Requests
@router.get("/teacher-change", include_in_schema=False)
def teacher_change(request: Request, page: int = 1, status: str = "", client: str = "", student: str = "",
                   date_from: str = "", date_to: str = "", q: str = "", db: Session = Depends(get_db),
                   user: User = Depends(require("requests.view"))):
    f = _filters(q, status, client, student, date_from, date_to)
    query = db.query(TeacherChangeRequest)
    if status:
        query = query.filter(TeacherChangeRequest.status == status)
    if parse_int(client):
        query = query.filter(TeacherChangeRequest.client_id == int(client))
    if parse_int(student):
        query = query.filter(TeacherChangeRequest.student_id == int(student))
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(func.date(TeacherChangeRequest.created_at) >= df)
    if dt:
        query = query.filter(func.date(TeacherChangeRequest.created_at) <= dt)
    pg = paginate(query.order_by(TeacherChangeRequest.id.desc()), page, 25)
    rows = []
    for r in pg.items:
        rows.append({"id": r.id, "status": r.status, "created_at": r.created_at,
                     "cells": [r.id, r.client.full_name if r.client else "", _shift(r.client),
                               r.student.full_name if r.student else "",
                               r.subscription.subscription_code if r.subscription else "",
                               r.current_teacher.full_name if r.current_teacher else "",
                               r.new_teacher.full_name if r.new_teacher else "", r.description]})
    counts = _counts(db, TeacherChangeRequest, TeacherChangeRequest.status)
    return _render(request, user, db, "teacher-change", pg, counts, rows, f, _base("teacher-change", f))


@router.post("/teacher-change/new", include_in_schema=False)
async def teacher_change_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("requests.add"))):
    form = await request.form()
    sub = db.get(Subscription, parse_int(form.get("subscription_id")) or 0)
    if not sub:
        return redirect("/requests/teacher-change", "Select the subscription to move.", "error")
    req = TeacherChangeRequest(client_id=sub.client_id, student_id=sub.student_id, subscription_id=sub.id,
                               current_teacher_id=sub.teacher_id, new_teacher_id=parse_int(form.get("new_teacher_id")),
                               description=(form.get("description") or "").strip() or None,
                               status="pending", requested_by_id=user.id)
    db.add(req)
    db.flush()
    log_action(db, user, "create", "requests", entity=req, request=request, rationale=req.description,
               description=f"Teacher change request created for {sub.subscription_code}")
    db.commit()
    return redirect("/requests/teacher-change?status=pending", "Teacher change request created.")


# =============================================================================== Refer New Contacts
@router.get("/references", include_in_schema=False)
def references(request: Request, page: int = 1, status: str = "", client: str = "", student: str = "",
               date_from: str = "", date_to: str = "", q: str = "", db: Session = Depends(get_db),
               user: User = Depends(require("requests.view"))):
    f = _filters(q, status, client, student, date_from, date_to)
    query = db.query(ReferredContact)
    if status:
        query = query.filter(ReferredContact.status == status)
    if parse_int(client):
        query = query.filter(ReferredContact.client_id == int(client))
    if q:
        query = query.filter(ReferredContact.name.ilike(f"%{q}%"))
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(func.date(ReferredContact.created_at) >= df)
    if dt:
        query = query.filter(func.date(ReferredContact.created_at) <= dt)
    pg = paginate(query.order_by(ReferredContact.id.desc()), page, 25)
    rows = []
    for r in pg.items:
        rows.append({"id": r.id, "status": r.status, "created_at": r.created_at,
                     "cells": [r.id, r.client.full_name if r.client else "", r.name, _shift(r.client),
                               r.email, r.contact_no, r.description, (r.reference_type or "").title()]})
    counts = _counts(db, ReferredContact, ReferredContact.status)
    return _render(request, user, db, "references", pg, counts, rows, f, _base("references", f))


@router.post("/references/new", include_in_schema=False)
async def reference_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("requests.add"))):
    form = await request.form()
    c = db.get(Client, parse_int(form.get("client_id")) or 0)
    name = (form.get("name") or "").strip()
    if not c or not name:
        return redirect("/requests/references", "Choose the referring family and give the contact a name.", "error")
    r = ReferredContact(client_id=c.id, name=name[:150], email=(form.get("email") or "").strip() or None,
                        contact_no=(form.get("contact_no") or "").strip() or None,
                        description=(form.get("description") or "").strip() or None,
                        reference_type=form.get("reference_type") or "family", status="pending")
    db.add(r)
    db.flush()
    log_action(db, user, "create", "requests", entity=r, request=request, rationale=r.description,
               description=f"Referred contact {name} recorded for {c.full_name}")
    db.commit()
    return redirect("/requests/references?status=pending", f"Referred contact {name} recorded.")


# =============================================================================== Complaints
@router.get("/complaints", include_in_schema=False)
def complaints(request: Request, page: int = 1, status: str = "", client: str = "", student: str = "",
               date_from: str = "", date_to: str = "", q: str = "", db: Session = Depends(get_db),
               user: User = Depends(require("requests.view"))):
    f = _filters(q, status, client, student, date_from, date_to)
    query = db.query(Case).filter(Case.case_type == "complaint")
    if status:
        query = query.filter(Case.approval_status == status)
    if parse_int(client):
        query = query.filter(Case.client_id == int(client))
    if parse_int(student):
        query = query.filter(Case.student_id == int(student))
    if q:
        query = query.filter(Case.title.ilike(f"%{q}%"))
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(func.date(Case.created_at) >= df)
    if dt:
        query = query.filter(func.date(Case.created_at) <= dt)
    pg = paginate(query.order_by(Case.id.desc()), page, 25)
    rows = []
    for k in pg.items:
        rows.append({"id": k.id, "status": k.approval_status, "created_at": k.created_at, "link": f"/cases/{k.id}",
                     "cells": [k.case_number, k.client.full_name if k.client else "", _shift(k.client),
                               k.complaint_type or "", k.title, k.description, k.company_response]})
    counts = _counts(db, Case, Case.approval_status, Case.case_type == "complaint")
    return _render(request, user, db, "complaints", pg, counts, rows, f, _base("complaints", f))


@router.post("/complaints/new", include_in_schema=False)
async def complaint_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("requests.add"))):
    form = await request.form()
    c = db.get(Client, parse_int(form.get("client_id")) or 0)
    title = (form.get("title") or "").strip()
    if not c or not title:
        return redirect("/requests/complaints", "Choose the family and give the complaint a title.", "error")
    sid = parse_int(form.get("student_id"))
    k = Case(case_number=next_code(db, Case, "case_number", "CS-"), case_type="complaint", title=title[:200],
             description=(form.get("description") or "").strip() or None, raised_by_type="client",
             raised_by_user_id=user.id, client_id=c.id, student_id=sid or None, priority="medium", status="open",
             source="staff", complaint_type=form.get("complaint_type") or "Other", approval_status="pending")
    db.add(k)
    db.flush()
    log_action(db, user, "create", "requests", entity=k, request=request, rationale=k.description,
               description=f"Complaint {k.case_number} recorded for {c.full_name}")
    db.commit()
    return redirect("/requests/complaints?status=pending", f"Complaint {k.case_number} recorded.")


# =============================================================================== Change Status (bulk)
def _apply_leave(db: Session, user: User, lv: Leave, status: str, remarks: str, request: Request) -> Client | None:
    lv.status = status
    if status == "approved":
        lv.approved_by_id = user.id
        lv.approved_at = datetime.utcnow()
    st = lv.student
    return st.client if st else None


def _apply_time_change(db: Session, user: User, r: TimeChangeRequest, status: str, remarks: str, request: Request) -> Client | None:
    r.status = status
    r.decided_by_id = user.id
    r.decided_at = datetime.utcnow()
    r.decision_remarks = remarks or None
    if status == "approved":
        from app.services import scheduling as sched_svc  # lazy: cross-module service
        sub = r.subscription
        if sub is not None:
            if r.new_slot_id:
                sub.slot_id = r.new_slot_id
            if r.days:
                sub.days_of_week = list(r.days)
            sch = db.get(Schedule, sub.schedule_id) if sub.schedule_id else None
            if sch is not None:
                slot = db.get(SessionSlot, r.new_slot_id) if r.new_slot_id else None
                if slot is not None:
                    sch.start_time = slot.start_time
                    sch.duration_minutes = slot.duration_minutes or sch.duration_minutes
                if r.days:
                    sch.days_of_week = list(r.days)
                try:
                    sched_svc.regenerate_future_sessions(db, sch)
                except Exception:  # keep the approval even when session regeneration is unavailable
                    pass
    return r.client


def _apply_teacher_change(db: Session, user: User, r: TeacherChangeRequest, status: str, remarks: str, request: Request) -> Client | None:
    r.status = status
    r.decided_by_id = user.id
    r.decided_at = datetime.utcnow()
    r.decision_remarks = remarks or None
    if status == "approved" and r.new_teacher_id:
        sub = r.subscription
        if sub is not None:
            sub.teacher_id = r.new_teacher_id
        if r.student is not None:
            r.student.teacher_id = r.new_teacher_id
        sch = db.get(Schedule, sub.schedule_id) if sub is not None and sub.schedule_id else None
        if sch is not None:
            sch.teacher_id = r.new_teacher_id
        if r.student_id:
            (db.query(ClassSession)
             .filter(ClassSession.student_id == r.student_id, ClassSession.status == "pending",
                     ClassSession.date >= date.today())
             .update({ClassSession.teacher_id: r.new_teacher_id}, synchronize_session=False))
    return r.client


def _apply_reference(db: Session, user: User, r: ReferredContact, status: str, remarks: str, request: Request) -> Client | None:
    r.status = status
    r.decided_by_id = user.id
    r.decided_at = datetime.utcnow()
    r.decision_remarks = remarks or None
    if status == "approved" and not r.lead_id:
        src = db.query(LeadSource).filter(LeadSource.name == "Referral").first()
        lead = Lead(lead_code=next_code(db, Lead, "lead_code", "L-"), full_name=r.name,
                    email=r.email, phone=r.contact_no, whatsapp=r.contact_no,
                    country=r.client.country if r.client else None,
                    source_id=src.id if src else None,
                    referral_code=r.client.referral_code if r.client else None,
                    stage="new", notes=f"Referred by {r.client.full_name if r.client else 'a family'}. {r.description or ''}".strip())
        db.add(lead)
        db.flush()
        r.lead_id = lead.id
    return r.client


def _apply_complaint(db: Session, user: User, k: Case, status: str, remarks: str, request: Request,
                     company_response: str = "") -> Client | None:
    k.approval_status = status
    if company_response:
        k.company_response = company_response
    if status == "approved":
        k.status = "resolved"
        k.resolution = k.company_response or remarks or None
        k.resolved_at = datetime.utcnow()
    return k.client


_MODELS = {"leaves": Leave, "time-change": TimeChangeRequest, "teacher-change": TeacherChangeRequest,
           "references": ReferredContact, "complaints": Case}
_APPLIERS = {"leaves": _apply_leave, "time-change": _apply_time_change, "teacher-change": _apply_teacher_change,
             "references": _apply_reference, "complaints": _apply_complaint}


@router.post("/{kind}/change-status", include_in_schema=False)
async def change_status(kind: str, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("requests.update", "requests.approve", any_of=True))):
    if kind not in KINDS:
        raise HTTPException(404, "Unknown request list")
    url = f"/requests/{kind}"
    form = await request.form()
    status = (form.get("status") or "").strip()
    remarks = (form.get("remarks") or "").strip()
    company_response = (form.get("company_response") or "").strip()
    ids = [int(v) for v in form.getlist("ids") if str(v).isdigit()]
    if status not in STATUSES:
        return redirect(url, "Choose the new approval status.", "error")
    if not ids:
        return redirect(url, "Select at least one row first.", "error")
    if status == "approved" and not rbac.has_permission(user, "requests.approve"):
        return redirect(url, "You do not have permission to approve client requests.", "error")
    if not remarks:
        return redirect(url, "Remarks are required for a status change.", "error")
    model = _MODELS[kind]
    query = db.query(model).filter(model.id.in_(ids))
    if kind == "leaves":
        query = query.filter(Leave.person_type == "student")
    elif kind == "complaints":
        query = query.filter(Case.case_type == "complaint")
    rows = query.all()
    apply = _APPLIERS[kind]
    label = KINDS[kind]["title"]
    n = 0
    for obj in rows:
        before = getattr(obj, "approval_status", None) if kind == "complaints" else obj.status
        if kind == "complaints":
            client = apply(db, user, obj, status, remarks, request, company_response)
        else:
            client = apply(db, user, obj, status, remarks, request)
        log_action(db, user, "approve" if status == "approved" else "status_change", "requests", entity=obj,
                   description=f"{label} #{obj.id} moved from {before} to {status}", rationale=remarks,
                   before={"status": before}, after={"status": status}, request=request, consequential=True)
        _notify_client(db, client, f"{label[:-1] if label.endswith('s') else label} {status}",
                       f"Your request #{obj.id} has been marked {status}. {remarks}", "/portal/requests")
        n += 1
    db.commit()
    return redirect(f"{url}?status={status}", f"{n} request(s) moved to {status}.")
