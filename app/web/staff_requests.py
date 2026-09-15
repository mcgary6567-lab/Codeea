"""ERP "Employment Management" staff request lists (docs/AUDIT_HUMAN_RESOURCE.md, Level 3).

Employee Requests, Staff Bonuses, Advance Requests, Complaints, Downloads and the general Attachment
store. Every list follows the platform's ERP request pattern (see app/web/requests.py): Approval Status
tiles that filter the list, a filter bar, a bordered table carrying the ERP's own column labels, a Create
action and an "Actions -> Change Status" bulk form. Every mutation is audited with the remarks as the
rationale, and the employee's user is notified when a decision is made.

Staff Violations and the Employee Record itself live in app/web/hr.py alongside the rest of the employee
routes; they use the helpers defined here so all eight pages look and behave the same.

Confidentiality: a complaint marked secret is readable only by the People & Culture roles (the
``grievances.view`` permission). The confidential grievance channel at /hr/grievances is unchanged and is
still the right place for anything a member of staff wants kept from their own management chain.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.core import rbac
from app.core.audit import log_action
from app.core.deps import PermissionDenied, UserContext, csrf_protect, get_user_context, require
from app.core.notify import notify
from app.core.templating import label as status_label
from app.core.templating import render
from app.core.utils import month_key, paginate, parse_bool, parse_date, parse_float, parse_int, redirect
from app.database import get_db
from app.models.core import User
from app.models.hr_erp import (EMPLOYEE_REQUEST_TYPES, STAFF_COMPLAINT_TYPES, Attachment, BonusType, EmployeeRequest,
                               HRDownload, StaffComplaint)
from app.models.people import Bonus, Employee, SalaryAdvance

router = APIRouter(prefix="/hr", dependencies=[Depends(csrf_protect)])

STATUSES = ["pending", "approved", "rejected", "cancelled"]
TILES = [("Pending", "pending", "clock"), ("Approved", "approved", "check-circle-2"),
         ("Rejected", "rejected", "x-circle"), ("Cancelled", "cancelled", "ban")]
SHIFTS = ["morning", "evening", "night"]
ATTACHMENT_ENTITIES = ["employee", "candidate", "payroll", "client", "student", "policy", "other"]
# Row highlight rules, as the ERP's own reports carry them.
ROW_HIGHLIGHT = {"pending": "#fffbeb", "rejected": "#fef2f2", "cancelled": "#f8fafc"}

GROUP_TABS = [
    ("employees", "Employee Record", "/hr/employees"),
    ("requests", "Employee Requests", "/hr/requests"),
    ("violations", "Staff Violations", "/hr/violations"),
    ("bonuses", "Staff Bonuses", "/hr/bonuses"),
    ("advances", "Advance Requests", "/hr/advances"),
    ("complaints", "Complaints", "/hr/complaints"),
    ("downloads", "Downloads", "/hr/downloads"),
    ("attachments", "Attachments", "/hr/attachments"),
]

ATTACHMENT_DIR = BASE_DIR / "storage" / "attachments"


# ----------------------------------------------------------------------------- shared helpers
def badge(value: str, kind: str = "request") -> dict:
    """A cell rendered as a status badge rather than as text."""
    return {"value": value, "label": status_label(value, kind)}


def d(value) -> str:
    return value.strftime("%d %b %Y") if value else ""


def dt(value) -> str:
    return value.strftime("%d %b %Y, %H:%M") if value else ""


def money(value) -> str:
    return f"{float(value or 0):,.0f}"


def shift_name(e: Employee | None) -> str:
    return (e.shift or "").title() if e else ""


def employee_name(e: Employee | None) -> str:
    return f"{e.employee_code} - {e.full_name}" if e else ""


def employee_options(db: Session, only_active: bool = True) -> list[tuple[int, str]]:
    q = db.query(Employee)
    if only_active:
        q = q.filter(Employee.status.in_(["active", "probation", "on_leave"]))
    return [(e.id, f"{e.employee_code} - {e.full_name}") for e in q.order_by(Employee.full_name)]


def status_counts(db: Session, model, status_col, *filters) -> dict:
    q = db.query(status_col, func.count(model.id))
    for f in filters:
        q = q.filter(f)
    rows = dict(q.group_by(status_col).all())
    out = {s: rows.get(s, 0) for s in STATUSES}
    out["total"] = sum(rows.values())
    return out


def employee_search(query, model, q: str):
    like = f"%{q}%"
    return query.join(Employee, model.employee_id == Employee.id).filter(
        or_(Employee.full_name.ilike(like), Employee.employee_code.ilike(like)))


def notify_employee(db: Session, e: Employee | None, title: str, body: str, link: str) -> None:
    if e is not None and e.user_id:
        notify(db, e.user_id, title, body, event_type="hr_request", link=link)


def filters_from(q: str = "", status: str = "", employee: str = "", shift: str = "", **extra) -> dict:
    f = {"q": q, "status": status, "employee": employee, "shift": shift, "request_type": "", "complaint_type": "",
         "violation_type_id": "", "bonus_type_id": "", "acceptance_from": "", "acceptance_to": "",
         "date_from": "", "date_to": ""}
    f.update({k: v for k, v in extra.items() if v is not None})
    return f


def base_url_for(path: str, f: dict) -> str:
    parts = [f"{k}={v}" for k, v in f.items() if v]
    return path + ("?" + "&".join(parts) if parts else "")


def render_list(request: Request, user: User, db: Session, kind: str, meta: dict, pg, counts: dict, rows: list[dict],
                f: dict, path: str, can_add: bool, can_change: bool, extra: dict | None = None):
    tile_base = base_url_for(path, {k: v for k, v in f.items() if k != "status"})
    ctx = {
        "user": user, "kind": kind, "meta": meta, "page": pg, "rows": rows, "counts": counts, "tiles": TILES,
        "filters": f, "base_path": path, "base_url": base_url_for(path, f),
        "tile_url": tile_base + ("&" if "?" in tile_base else "?") + "status=",
        "status_options": [(s, status_label(s, "request")) for s in STATUSES],
        "statuses": STATUSES, "group_tabs": GROUP_TABS, "can_add": can_add, "can_change": can_change,
        "employee_options": employee_options(db), "shift_options": SHIFTS,
        "request_types": EMPLOYEE_REQUEST_TYPES, "complaint_types": STAFF_COMPLAINT_TYPES,
        "today_iso": date.today().isoformat(), "period_default": month_key(),
        "change_status_action": f"{path}/change-status", "create_action": f"{path}/new",
        "sub_tabs": None, "sub_tab": None, "can_pick_employee": True, "type_amounts": {},
    }
    ctx.update(extra or {})
    return render(request, "hr/erp_list.html", ctx)


def decide_guard(user: User, url: str, status: str, perm: str, ids: list[int], remarks: str):
    """Common validation for a bulk Change Status post. Returns a redirect response, or None when valid."""
    if status not in STATUSES:
        return redirect(url, "Choose the new approval status.", "error")
    if not ids:
        return redirect(url, "Select at least one row first.", "error")
    if status == "approved" and not rbac.has_permission(user, perm):
        return redirect(url, "You do not have permission to approve these records.", "error")
    if not remarks:
        return redirect(url, "Remarks are required for a status change.", "error")
    return None


# ============================================================================== EMPLOYEE REQUESTS
REQUEST_META = {
    "title": "Employee Requests", "back": "/home/hr",
    "subtitle": "General requests raised by staff — certificates, equipment, shift changes and the rest.",
    "headers": ["ID", "Employee", "Shift", "Date", "Req Type", "Description", "HR Remarks", "Status"],
    "filters": ["employee", "shift", "request_type", "dates"], "create_label": "Create Employee Request",
    "remarks_field": "hr_remarks", "remarks_label": "HR Remarks", "search_hint": "Employee / description",
}


@router.get("/requests", include_in_schema=False)
def requests_list(request: Request, page: int = 1, q: str = "", status: str = "", employee: str = "", shift: str = "",
                  request_type: str = "", date_from: str = "", date_to: str = "", db: Session = Depends(get_db),
                  user: User = Depends(require("employees.view"))):
    f = filters_from(q, status, employee, shift, request_type=request_type, date_from=date_from, date_to=date_to)
    query = db.query(EmployeeRequest)
    if status:
        query = query.filter(EmployeeRequest.status == status)
    if parse_int(employee):
        query = query.filter(EmployeeRequest.employee_id == int(employee))
    if request_type:
        query = query.filter(EmployeeRequest.request_type == request_type)
    if shift:
        query = query.filter(EmployeeRequest.employee_id.in_(
            [r[0] for r in db.query(Employee.id).filter(Employee.shift == shift)] or [-1]))
    if q:
        query = employee_search(query, EmployeeRequest, q)
    df, dtt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(EmployeeRequest.request_date >= df)
    if dtt:
        query = query.filter(EmployeeRequest.request_date <= dtt)
    pg = paginate(query.order_by(EmployeeRequest.id.desc()), page, 25)
    rows = [{"id": r.id, "status": r.status, "highlight": ROW_HIGHLIGHT.get(r.status),
             "cells": [r.id, employee_name(r.employee), shift_name(r.employee), d(r.request_date), r.request_type,
                       r.description, r.hr_remarks, badge(r.status)]} for r in pg.items]
    counts = status_counts(db, EmployeeRequest, EmployeeRequest.status)
    return render_list(request, user, db, "requests", REQUEST_META, pg, counts, rows, f, "/hr/requests",
                       can_add=rbac.has_permission(user, "employees.add"),
                       can_change=rbac.has_permission(user, "employees.update") or rbac.has_permission(user, "employees.approve"))


@router.post("/requests/new", include_in_schema=False)
async def request_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.add"))):
    form = await request.form()
    e = db.get(Employee, parse_int(form.get("employee_id")) or 0)
    description = (form.get("description") or "").strip()
    if not e or not description:
        return redirect("/hr/requests", "Choose the employee and describe the request.", "error")
    rtype = form.get("request_type") or "Other"
    r = EmployeeRequest(employee_id=e.id, request_date=parse_date(form.get("request_date")) or date.today(),
                        request_type=rtype if rtype in EMPLOYEE_REQUEST_TYPES else "Other",
                        description=description, status="pending")
    db.add(r)
    db.flush()
    log_action(db, user, "create", "employees", entity=r, request=request, rationale=description,
               description=f"Employee request ({r.request_type}) raised for {e.employee_code}")
    db.commit()
    return redirect("/hr/requests?status=pending", f"Employee request raised for {e.full_name}.")


@router.post("/requests/change-status", include_in_schema=False)
async def request_change_status(request: Request, db: Session = Depends(get_db),
                                user: User = Depends(require("employees.update", "employees.approve", any_of=True))):
    form = await request.form()
    status = (form.get("status") or "").strip()
    remarks = (form.get("hr_remarks") or form.get("remarks") or "").strip()
    ids = [int(v) for v in form.getlist("ids") if str(v).isdigit()]
    bad = decide_guard(user, "/hr/requests", status, "employees.approve", ids, remarks)
    if bad is not None:
        return bad
    n = 0
    for r in db.query(EmployeeRequest).filter(EmployeeRequest.id.in_(ids)).all():
        before = r.status
        r.status = status
        r.hr_remarks = remarks
        r.decided_by_id = user.id
        r.decided_at = datetime.utcnow()
        log_action(db, user, "approve" if status == "approved" else "status_change", "employees", entity=r,
                   description=f"Employee request #{r.id} ({r.request_type}) moved from {before} to {status}",
                   rationale=remarks, before={"status": before}, after={"status": status}, request=request,
                   consequential=True)
        notify_employee(db, r.employee, f"Your {r.request_type} request is {status}",
                        f"People & Culture marked request #{r.id} {status}. {remarks}", "/hr/me")
        n += 1
    db.commit()
    return redirect(f"/hr/requests?status={status}", f"{n} request(s) moved to {status}.")


# ============================================================================== STAFF BONUSES
BONUS_META = {
    "title": "Staff Bonuses", "back": "/home/hr",
    "subtitle": "Bonuses raised against the bonus-type catalogue. Approving records the acceptance date.",
    "headers": ["ID", "Emp Name", "Shift", "Bonus Type", "Bonus Type Status", "Bonus Amount", "Remarks",
                "Created At", "Update Date", "Acceptance Date", "Status"],
    "filters": ["employee", "shift", "bonus_type", "acceptance"], "create_label": "Create Staff Bonus",
    "search_hint": "Employee / code",
}


@router.get("/bonuses", include_in_schema=False)
def bonuses_list(request: Request, page: int = 1, q: str = "", status: str = "", employee: str = "", shift: str = "",
                 bonus_type_id: str = "", acceptance_from: str = "", acceptance_to: str = "",
                 db: Session = Depends(get_db), user: User = Depends(require("payroll.view"))):
    f = filters_from(q, status, employee, shift, bonus_type_id=bonus_type_id, acceptance_from=acceptance_from,
                     acceptance_to=acceptance_to)
    query = db.query(Bonus)
    if status:
        query = query.filter(Bonus.status == status)
    if parse_int(employee):
        query = query.filter(Bonus.employee_id == int(employee))
    if parse_int(bonus_type_id):
        query = query.filter(Bonus.bonus_type_id == int(bonus_type_id))
    if shift:
        query = query.filter(Bonus.employee_id.in_(
            [r[0] for r in db.query(Employee.id).filter(Employee.shift == shift)] or [-1]))
    if q:
        query = employee_search(query, Bonus, q)
    af, at = parse_date(acceptance_from), parse_date(acceptance_to)
    if af:
        query = query.filter(Bonus.acceptance_date >= af)
    if at:
        query = query.filter(Bonus.acceptance_date <= at)
    pg = paginate(query.order_by(Bonus.id.desc()), page, 25)
    rows = []
    for b in pg.items:
        cat = b.bonus_catalogue
        rows.append({"id": b.id, "status": b.status, "highlight": ROW_HIGHLIGHT.get(b.status),
                     "cells": [b.id, employee_name(b.employee), shift_name(b.employee),
                               cat.description if cat else (b.bonus_type or "").title(),
                               badge(cat.status if cat else "active"), money(b.amount), b.reason,
                               dt(b.created_at), dt(b.updated_at), d(b.acceptance_date), badge(b.status)]})
    counts = status_counts(db, Bonus, Bonus.status)
    types = db.query(BonusType).filter(BonusType.status == "active").order_by(BonusType.sort_no).all()
    extra = {"bonus_type_options": [(t.id, f"{t.description} ({money(t.bonus_amount)})") for t in types],
             "type_amounts": {str(t.id): float(t.bonus_amount or 0) for t in types}}
    return render_list(request, user, db, "bonuses", BONUS_META, pg, counts, rows, f, "/hr/bonuses",
                       can_add=rbac.has_permission(user, "payroll.add"),
                       can_change=rbac.has_permission(user, "payroll.update") or rbac.has_permission(user, "payroll.approve"),
                       extra=extra)


@router.post("/bonuses/new", include_in_schema=False)
async def bonus_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.add"))):
    form = await request.form()
    e = db.get(Employee, parse_int(form.get("employee_id")) or 0)
    bt = db.get(BonusType, parse_int(form.get("bonus_type_id")) or 0)
    if not e or not bt:
        return redirect("/hr/bonuses", "Choose the employee and the bonus type.", "error")
    amount = parse_float(form.get("amount"), 0) or float(bt.bonus_amount or 0)
    if amount <= 0:
        return redirect("/hr/bonuses", "The bonus amount must be greater than zero.", "error")
    b = Bonus(employee_id=e.id, amount=amount, currency=e.currency or "PKR", bonus_type="performance",
              bonus_type_id=bt.id, reason=(form.get("reason") or "").strip() or bt.description,
              period=form.get("period") or month_key(), status="pending")
    db.add(b)
    db.flush()
    log_action(db, user, "create", "payroll", entity=b, request=request, rationale=b.reason,
               description=f"Staff bonus of {money(amount)} proposed for {e.employee_code} ({bt.description})")
    db.commit()
    return redirect("/hr/bonuses?status=pending", f"Bonus proposed for {e.full_name}.")


@router.post("/bonuses/change-status", include_in_schema=False)
async def bonus_change_status(request: Request, db: Session = Depends(get_db),
                              user: User = Depends(require("payroll.update", "payroll.approve", any_of=True))):
    form = await request.form()
    status = (form.get("status") or "").strip()
    remarks = (form.get("remarks") or "").strip()
    ids = [int(v) for v in form.getlist("ids") if str(v).isdigit()]
    bad = decide_guard(user, "/hr/bonuses", status, "payroll.approve", ids, remarks)
    if bad is not None:
        return bad
    n = 0
    for b in db.query(Bonus).filter(Bonus.id.in_(ids)).all():
        before = b.status
        b.status = status
        b.decided_at = datetime.utcnow()
        b.reason = ((b.reason + " | ") if b.reason else "") + remarks
        if status == "approved":
            b.acceptance_date = date.today()
            b.approved_by_id = user.id
            # An approved bonus is owed to them: a debit on their Account Ledger, written once.
            from app.services.hr import post_bonus_ledger
            post_bonus_ledger(db, b, user)
        log_action(db, user, "approve" if status == "approved" else "status_change", "payroll", entity=b,
                   description=f"Staff bonus #{b.id} ({money(b.amount)}) moved from {before} to {status}",
                   rationale=remarks, before={"status": before},
                   after={"status": status, "acceptance_date": str(b.acceptance_date or "")},
                   request=request, consequential=True)
        notify_employee(db, b.employee, f"Bonus {status}",
                        f"Your bonus of {money(b.amount)} was {status}. {remarks}", "/hr/me?tab=payslips")
        n += 1
    db.commit()
    return redirect(f"/hr/bonuses?status={status}", f"{n} bonus(es) moved to {status}.")


# ============================================================================== ADVANCE REQUESTS
ADVANCE_META = {
    "title": "Advance Requests", "back": "/home/hr",
    "subtitle": "Salary advances and the instalments they are recovered in. Approving opens the recovery balance.",
    "headers": ["ID", "Employee", "Shift", "Request Date", "Amount", "No Of Installments", "Remarks", "Hr Remarks",
                "Status"],
    "filters": ["employee", "shift", "dates"], "create_label": "Create Advance Request",
    "remarks_field": "hr_remarks", "remarks_label": "Hr Remarks", "search_hint": "Employee / code",
}


@router.get("/advances", include_in_schema=False)
def advances_list(request: Request, page: int = 1, q: str = "", status: str = "", employee: str = "", shift: str = "",
                  date_from: str = "", date_to: str = "", db: Session = Depends(get_db),
                  user: User = Depends(require("payroll.view"))):
    f = filters_from(q, status, employee, shift, date_from=date_from, date_to=date_to)
    query = db.query(SalaryAdvance)
    if status:
        query = query.filter(SalaryAdvance.status == status)
    if parse_int(employee):
        query = query.filter(SalaryAdvance.employee_id == int(employee))
    if shift:
        query = query.filter(SalaryAdvance.employee_id.in_(
            [r[0] for r in db.query(Employee.id).filter(Employee.shift == shift)] or [-1]))
    if q:
        query = employee_search(query, SalaryAdvance, q)
    df, dtt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(SalaryAdvance.request_date >= df)
    if dtt:
        query = query.filter(SalaryAdvance.request_date <= dtt)
    pg = paginate(query.order_by(SalaryAdvance.id.desc()), page, 25)
    rows = [{"id": a.id, "status": a.status, "highlight": ROW_HIGHLIGHT.get(a.status),
             "cells": [a.id, employee_name(a.employee), shift_name(a.employee), d(a.request_date), money(a.amount),
                       a.installments, a.reason, a.hr_remarks, badge(a.status)]} for a in pg.items]
    counts = status_counts(db, SalaryAdvance, SalaryAdvance.status)
    return render_list(request, user, db, "advances", ADVANCE_META, pg, counts, rows, f, "/hr/advances",
                       can_add=rbac.has_permission(user, "payroll.add"),
                       can_change=rbac.has_permission(user, "payroll.update") or rbac.has_permission(user, "payroll.approve"))


@router.post("/advances/new", include_in_schema=False)
async def advance_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.add"))):
    form = await request.form()
    e = db.get(Employee, parse_int(form.get("employee_id")) or 0)
    amount = parse_float(form.get("amount"), 0)
    if not e or amount <= 0:
        return redirect("/hr/advances", "Choose the employee and an amount greater than zero.", "error")
    a = SalaryAdvance(employee_id=e.id, amount=amount, currency=e.currency or "PKR",
                      reason=(form.get("reason") or "").strip() or None,
                      installments=max(1, parse_int(form.get("installments"), 1) or 1), remaining=0,
                      status="pending", request_date=parse_date(form.get("request_date")) or date.today())
    db.add(a)
    db.flush()
    log_action(db, user, "create", "payroll", entity=a, request=request, rationale=a.reason,
               description=f"Advance of {money(amount)} requested for {e.employee_code} in {a.installments} instalment(s)")
    db.commit()
    return redirect("/hr/advances?status=pending", f"Advance request created for {e.full_name}.")


@router.post("/advances/change-status", include_in_schema=False)
async def advance_change_status(request: Request, db: Session = Depends(get_db),
                                user: User = Depends(require("payroll.update", "payroll.approve", any_of=True))):
    form = await request.form()
    status = (form.get("status") or "").strip()
    remarks = (form.get("hr_remarks") or form.get("remarks") or "").strip()
    ids = [int(v) for v in form.getlist("ids") if str(v).isdigit()]
    bad = decide_guard(user, "/hr/advances", status, "payroll.approve", ids, remarks)
    if bad is not None:
        return bad
    n = 0
    for a in db.query(SalaryAdvance).filter(SalaryAdvance.id.in_(ids)).all():
        before = a.status
        a.status = status
        a.hr_remarks = remarks
        a.decided_at = datetime.utcnow()
        if status == "approved":
            a.remaining = float(a.amount or 0)
            a.approved_by_id = user.id
            # Paid out: a credit on their Account Ledger. Each instalment recovered by a payroll run
            # posts the matching debit, so the member of staff can watch the advance come back.
            from app.services.hr import post_advance_ledger
            post_advance_ledger(db, a, user)
        log_action(db, user, "approve" if status == "approved" else "status_change", "payroll", entity=a,
                   description=f"Advance #{a.id} ({money(a.amount)}) moved from {before} to {status}",
                   rationale=remarks, before={"status": before},
                   after={"status": status, "remaining": float(a.remaining or 0)}, request=request, consequential=True)
        notify_employee(db, a.employee, f"Salary advance {status}",
                        f"Your advance of {money(a.amount)} was {status}. {remarks}", "/hr/me?tab=payslips")
        n += 1
    db.commit()
    return redirect(f"/hr/advances?status={status}", f"{n} advance(s) moved to {status}.")


# ============================================================================== COMPLAINTS
COMPLAINT_META = {
    "title": "Complaints", "back": "/home/hr",
    "subtitle": "The ERP's open staff complaint list. A secret complaint is readable only by People & Culture.",
    "headers": ["ID", "Employee", "Complaint Type", "Title", "Description", "Admin Response", "Status", "Secret",
                "Created At"],
    "filters": ["employee", "complaint_type"], "create_label": "Create Complaint",
    "remarks_field": "remarks", "remarks_label": "Remarks", "search_hint": "Title / employee",
    "note": "Anything you want kept from your own management chain belongs in the confidential grievance channel "
            "at /hr/grievances, which is handled by the Head of People & Culture alone.",
}
COMPLAINT_TABS = [("open", "Non Secret", "/hr/complaints?tab=open"), ("secret", "Secret", "/hr/complaints?tab=secret")]


def _can_read_secret(user: User) -> bool:
    return rbac.has_permission(user, "grievances.view")


@router.get("/complaints", include_in_schema=False)
def complaints_list(request: Request, tab: str = "open", page: int = 1, q: str = "", status: str = "",
                    employee: str = "", complaint_type: str = "", db: Session = Depends(get_db),
                    ctx: UserContext = Depends(get_user_context)):
    user = ctx.user
    if not rbac.has_permission(user, "portal_self.view") and not rbac.has_permission(user, "employees.view"):
        raise PermissionDenied("portal_self.view")
    secret = tab == "secret"
    if secret and not _can_read_secret(user):
        raise PermissionDenied("grievances.view")
    # ``tab`` travels with the filters so the status tiles and the pager stay on the tab you are reading.
    f = filters_from(q, status, employee, "", complaint_type=complaint_type, tab=tab)
    query = db.query(StaffComplaint).filter(StaffComplaint.is_secret.is_(secret))
    all_staff = rbac.has_permission(user, "employees.view")
    if not all_staff:  # a member of staff sees only their own complaints
        query = query.filter(StaffComplaint.employee_id == (ctx.employee_id or -1))
    if status:
        query = query.filter(StaffComplaint.status == status)
    if parse_int(employee):
        query = query.filter(StaffComplaint.employee_id == int(employee))
    if complaint_type:
        query = query.filter(StaffComplaint.complaint_type == complaint_type)
    if q:
        query = query.filter(StaffComplaint.title.ilike(f"%{q}%"))
    pg = paginate(query.order_by(StaffComplaint.id.desc()), page, 25)
    rows = [{"id": c.id, "status": c.status, "highlight": ROW_HIGHLIGHT.get(c.status),
             "cells": [c.id, employee_name(c.employee), c.complaint_type, c.title, c.description, c.admin_response,
                       badge(c.status), "Yes" if c.is_secret else "No", dt(c.created_at)]} for c in pg.items]
    count_filters = [StaffComplaint.is_secret.is_(secret)]
    if not all_staff:
        count_filters.append(StaffComplaint.employee_id == (ctx.employee_id or -1))
    counts = status_counts(db, StaffComplaint, StaffComplaint.status, *count_filters)
    meta = dict(COMPLAINT_META)
    meta["title"] = "Complaints — Secret" if secret else "Complaints"
    tabs = [t for t in COMPLAINT_TABS if t[0] == "open" or _can_read_secret(user)]
    extra = {"sub_tabs": tabs, "sub_tab": tab, "can_pick_employee": all_staff}
    return render_list(request, user, db, "complaints", meta, pg, counts, rows, f, "/hr/complaints",
                       can_add=rbac.has_permission(user, "portal_self.view") or all_staff,
                       can_change=rbac.has_permission(user, "employees.update") or rbac.has_permission(user, "employees.approve"),
                       extra=extra)


@router.post("/complaints/new", include_in_schema=False)
async def complaint_create(request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    user = ctx.user
    if not rbac.has_permission(user, "portal_self.view") and not rbac.has_permission(user, "employees.add"):
        raise PermissionDenied("portal_self.view")
    form = await request.form()
    e = None
    if rbac.has_permission(user, "employees.view") and parse_int(form.get("employee_id")):
        e = db.get(Employee, int(form.get("employee_id")))
    e = e or ctx.employee
    title = (form.get("title") or "").strip()
    if not e or not title:
        return redirect("/hr/complaints", "A complaint needs an employee record and a title.", "error")
    ctype = form.get("complaint_type") or "HR"
    c = StaffComplaint(employee_id=e.id, complaint_type=ctype if ctype in STAFF_COMPLAINT_TYPES else "Other",
                       title=title[:200], description=(form.get("description") or "").strip() or None,
                       is_secret=parse_bool(form.get("is_secret")), status="pending")
    db.add(c)
    db.flush()
    log_action(db, user, "create", "employees", entity=c, request=request, rationale=c.description,
               description=f"Staff complaint '{c.title}' raised for {e.employee_code}"
                           + (" (secret)" if c.is_secret else ""))
    db.commit()
    tab = "secret" if c.is_secret and _can_read_secret(user) else "open"
    # The self portal's Complaints tab posts here too, and asks to be sent back to itself.
    nxt = (form.get("next") or "").strip()
    if nxt.startswith("/hr/me"):
        return redirect(nxt, "Complaint recorded.")
    return redirect(f"/hr/complaints?tab={tab}&status=pending", "Complaint recorded.")


@router.post("/complaints/change-status", include_in_schema=False)
async def complaint_change_status(request: Request, db: Session = Depends(get_db),
                                  user: User = Depends(require("employees.update", "employees.approve", any_of=True))):
    form = await request.form()
    tab = form.get("tab") or "open"
    url = f"/hr/complaints?tab={tab}"
    status = (form.get("status") or "").strip()
    remarks = (form.get("remarks") or "").strip()
    response = (form.get("admin_response") or "").strip()
    ids = [int(v) for v in form.getlist("ids") if str(v).isdigit()]
    bad = decide_guard(user, url, status, "employees.approve", ids, remarks)
    if bad is not None:
        return bad
    n = 0
    for c in db.query(StaffComplaint).filter(StaffComplaint.id.in_(ids)).all():
        if c.is_secret and not _can_read_secret(user):
            continue  # a secret complaint is never decided by anyone outside People & Culture
        before = c.status
        c.status = status
        c.admin_response = response or remarks
        c.decided_by_id = user.id
        c.decided_at = datetime.utcnow()
        log_action(db, user, "approve" if status == "approved" else "status_change", "employees", entity=c,
                   description=f"Staff complaint #{c.id} moved from {before} to {status}", rationale=remarks,
                   before={"status": before}, after={"status": status}, request=request, consequential=True)
        notify_employee(db, c.employee, f"Your complaint is {status}",
                        f"'{c.title}' was marked {status}. {c.admin_response or ''}", "/hr/complaints")
        n += 1
    db.commit()
    return redirect(f"{url}&status={status}", f"{n} complaint(s) moved to {status}.")


# ============================================================================== DOWNLOADS
@router.get("/downloads", include_in_schema=False)
def downloads_list(request: Request, page: int = 1, q: str = "", status: str = "", category: str = "",
                   db: Session = Depends(get_db), user: User = Depends(require("portal_self.view", "employees.view", any_of=True))):
    query = db.query(HRDownload)
    if status:
        query = query.filter(HRDownload.status == status)
    if category:
        query = query.filter(HRDownload.category == category)
    if q:
        query = query.filter(HRDownload.description.ilike(f"%{q}%"))
    if not rbac.has_permission(user, "employees.view"):
        query = query.filter(HRDownload.status == "active")
    pg = paginate(query.order_by(HRDownload.id.desc()), page, 25)
    counts = {"active": db.query(func.count(HRDownload.id)).filter(HRDownload.status == "active").scalar() or 0,
              "inactive": db.query(func.count(HRDownload.id)).filter(HRDownload.status != "active").scalar() or 0}
    counts["total"] = counts["active"] + counts["inactive"]
    return render(request, "hr/downloads.html", {
        "user": user, "page": pg, "q": q, "status": status, "category": category, "counts": counts,
        "group_tabs": GROUP_TABS, "categories": ["policy", "form", "handbook", "template", "other"],
        "base_url": f"/hr/downloads?q={q}&status={status}&category={category}",
        "can_edit": rbac.has_permission(user, "employees.update")})


@router.post("/downloads/new", include_in_schema=False)
async def download_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.update"))):
    form = await request.form()
    description = (form.get("description") or "").strip()
    if not description:
        return redirect("/hr/downloads", "A description is required.", "error")
    dl = HRDownload(description=description[:200], link=(form.get("link") or "").strip() or None,
                    category=form.get("category") or "policy", status="active")
    db.add(dl)
    db.flush()
    log_action(db, user, "create", "employees", entity=dl, request=request,
               description=f"HR download published: {dl.description}")
    db.commit()
    return redirect("/hr/downloads", "Document published to staff.")


@router.post("/downloads/{id}/edit", include_in_schema=False)
async def download_edit(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.update"))):
    dl = db.get(HRDownload, id)
    if not dl:
        raise HTTPException(404, "Document not found")
    form = await request.form()
    before = {"description": dl.description, "link": dl.link, "category": dl.category}
    dl.description = (form.get("description") or dl.description)[:200]
    dl.link = (form.get("link") or "").strip() or None
    dl.category = form.get("category") or dl.category
    log_action(db, user, "update", "employees", entity=dl, request=request, before=before,
               after={"description": dl.description, "link": dl.link, "category": dl.category},
               description=f"HR download updated: {dl.description}")
    db.commit()
    return redirect("/hr/downloads", "Document updated.")


@router.post("/downloads/{id}/toggle", include_in_schema=False)
async def download_toggle(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.update"))):
    dl = db.get(HRDownload, id)
    if not dl:
        raise HTTPException(404, "Document not found")
    before = dl.status
    dl.status = "inactive" if dl.status == "active" else "active"
    log_action(db, user, "status_change", "employees", entity=dl, request=request, before={"status": before},
               after={"status": dl.status}, description=f"HR download '{dl.description}' -> {dl.status}")
    db.commit()
    return redirect("/hr/downloads", f"Document marked {dl.status}.")


# ============================================================================== ATTACHMENTS
@router.get("/attachments", include_in_schema=False)
def attachments_list(request: Request, page: int = 1, q: str = "", status: str = "", entity_type: str = "",
                     db: Session = Depends(get_db), user: User = Depends(require("employees.view"))):
    query = db.query(Attachment)
    if status:
        query = query.filter(Attachment.status == status)
    if entity_type:
        query = query.filter(Attachment.entity_type == entity_type)
    if q:
        query = query.filter(or_(Attachment.title.ilike(f"%{q}%"), Attachment.file_name.ilike(f"%{q}%")))
    pg = paginate(query.order_by(Attachment.id.desc()), page, 25)
    counts = {"active": db.query(func.count(Attachment.id)).filter(Attachment.status == "active").scalar() or 0,
              "inactive": db.query(func.count(Attachment.id)).filter(Attachment.status != "active").scalar() or 0}
    counts["total"] = counts["active"] + counts["inactive"]
    by_entity = dict(db.query(Attachment.entity_type, func.count(Attachment.id)).group_by(Attachment.entity_type).all())
    return render(request, "hr/attachments.html", {
        "user": user, "page": pg, "q": q, "status": status, "entity_type": entity_type, "counts": counts,
        "by_entity": by_entity, "entities": ATTACHMENT_ENTITIES, "group_tabs": GROUP_TABS,
        "base_url": f"/hr/attachments?q={q}&status={status}&entity_type={entity_type}",
        "can_edit": rbac.has_permission(user, "employees.update"),
        "can_add": rbac.has_permission(user, "employees.add")})


@router.post("/attachments/new", include_in_schema=False)
async def attachment_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.add"))):
    form = await request.form()
    title = (form.get("title") or "").strip()
    link = (form.get("link") or "").strip()
    upload = form.get("file")
    has_file = upload is not None and getattr(upload, "filename", "")
    if not title or (not link and not has_file):
        return redirect("/hr/attachments", "Give the attachment a title and either a link or a file.", "error")
    a = Attachment(entity_type=form.get("entity_type") or "other", entity_id=parse_int(form.get("entity_id")) or None,
                   title=title[:200], link=link or None, uploaded_by_id=user.id, status="active")
    if has_file:
        ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch for ch in upload.filename if ch.isalnum() or ch in "._-")[:80] or "attachment"
        name = f"{datetime.utcnow():%Y%m%d%H%M%S}-{safe}"
        data = await upload.read()
        (ATTACHMENT_DIR / name).write_bytes(data)
        a.file_name = upload.filename[:200]
        a.file_path = f"/storage/attachments/{name}"
        a.content_type = (getattr(upload, "content_type", None) or "application/octet-stream")[:80]
        a.size_bytes = len(data)
    db.add(a)
    db.flush()
    log_action(db, user, "create", "employees", entity=a, request=request,
               description=f"Attachment '{a.title}' uploaded against {a.entity_type}"
                           + (f" #{a.entity_id}" if a.entity_id else ""))
    db.commit()
    return redirect("/hr/attachments", f"Attachment '{a.title}' stored.")


@router.post("/attachments/{id}/toggle", include_in_schema=False)
async def attachment_toggle(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.update"))):
    a = db.get(Attachment, id)
    if not a:
        raise HTTPException(404, "Attachment not found")
    before = a.status
    a.status = "inactive" if a.status == "active" else "active"
    log_action(db, user, "status_change", "employees", entity=a, request=request, before={"status": before},
               after={"status": a.status}, description=f"Attachment '{a.title}' -> {a.status}")
    db.commit()
    return redirect("/hr/attachments", f"Attachment marked {a.status}.")
