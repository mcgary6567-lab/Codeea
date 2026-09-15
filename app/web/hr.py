"""HR / People & Culture / Payroll (Modules 20, 21, 46, 49).

Employees, self-service, attendance, leaves, recruitment, payroll, violations, the confidential
grievance channel, teacher development (Ustaadh Lab) and one-action provisioning.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import require, csrf_protect, get_current_user, get_user_context, UserContext, PermissionDenied
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import redirect, paginate, parse_date, parse_int, parse_float, parse_bool, month_key, month_bounds, next_code
from app.database import get_db
from app.models.core import User, Role, Department, Branch, AuditEvent, Setting
from app.models.people import (Employee, Teacher, Student, HRAttendance, Leave, Violation, Grievance, SalaryStructure,
                               OnboardingTask, ProvisioningRecord, RecruitmentRequest, Candidate, Interview,
                               DevelopmentPlan, Bonus, SalaryAdvance, PayrollRun, Payslip, TrainingAssignment)
from app.models.hr_erp import (DESIGNATIONS, EMPLOYEE_REQUEST_TYPES, EMPLOYEE_TYPES, STAFF_COMPLAINT_TYPES,
                               EmployeeRequest, Grade, ViolationType)
from app.services import hr as svc
from app.services import payroll as pay
# Employment Management shares its list helpers with app/web/staff_requests.py so all eight ERP pages
# (Employee Record, Employee Requests, Staff Violations, Staff Bonuses, Advance Requests, Complaints,
# Downloads and Attachments) look and behave identically.
from app.web.staff_requests import (GROUP_TABS, ROW_HIGHLIGHT, badge as erp_badge, d as erp_date,
                                    decide_guard, dt as erp_datetime, employee_name, employee_search,
                                    filters_from, money as erp_money, notify_employee, render_list,
                                    shift_name, status_counts)

router = APIRouter(prefix="/hr", dependencies=[Depends(csrf_protect)])

EMPLOYEE_TABS = [("overview", "Overview"), ("attendance", "Attendance"), ("leaves", "Leaves"), ("salary", "Salary"),
                 ("payslips", "Payslips"), ("violations", "Violations"), ("comp", "Bonuses & Advances"),
                 ("onboarding", "Onboarding"), ("provisioning", "Provisioning"), ("development", "Development"),
                 ("documents", "Documents"), ("audit", "Audit")]
# The tab strip mirrors the fourteen cards of their Employee Self Portal (docs/AUDIT_EMPLOYEE_SELF_PORTAL.md).
# Tasks, Daily Progress Sheet and Team Management are pages of their own (/hr/me/tasks, /hr/me/progress,
# /hr/me/team); Notifications, Downloads and My Profile are platform-wide pages the launchpad links direct.
ME_TABS = [("overview", "Overview"), ("schedule", "My Schedule"), ("attendance", "Attendance Sheet"),
           ("requests", "Requests"), ("leaves", "My Leaves"), ("payslips", "Salary Slips"),
           ("violations", "Violations"), ("bonuses", "Bonuses"), ("complaints", "Complaints"),
           ("development", "My Development"), ("grievance", "Raise a Grievance")]
EMP_STATUSES = ["active", "probation", "on_leave", "resigned", "terminated"]
EMPLOYMENT_TYPES = ["full_time", "part_time", "contract"]
SHIFTS = ["morning", "evening", "night"]
LEAVE_TYPES = ["casual", "sick", "annual", "emergency", "vacation"]
VIOLATION_TYPES = ["late", "absent", "misconduct", "policy", "missed_class"]
SEVERITIES = ["minor", "major", "critical"]
STAGES = ["applied", "screening", "interview", "demo", "offer", "hired", "rejected"]
GRIEVANCE_CATEGORIES = ["workplace", "harassment", "pay", "management", "safeguarding", "facilities", "other"]
TRAINING_CATEGORIES = ["tajweed", "methodology", "engagement", "technology", "conduct"]
TRAINING_CATALOGUE = [
    ("Tajweed refresher — Makharij & Sifaat", "tajweed", "Six-week recitation clinic with weekly recorded submissions.", True),
    ("Advanced Hifz methodology", "methodology", "Sabaq / Sabqi / Manzil planning, revision cycles and memory anchoring.", True),
    ("Child engagement for online classes", "engagement", "Attention cycles, gamified drills and camera-on rapport for ages 5-12.", True),
    ("Classroom technology & platform mastery", "technology", "Class room tools, screen sharing, recording etiquette and connectivity fixes.", False),
    ("Professional conduct & safeguarding", "conduct", "Code of conduct, safeguarding red flags, escalation and documentation.", True),
    ("Parent communication & difficult conversations", "conduct", "Progress conversations, complaints handling and boundaries.", False),
    ("Tarjuma & Tafseer teaching essentials", "methodology", "Structuring meaning-based lessons for non-Arabic speakers.", False),
    ("AI fluency for teachers", "technology", "Using the platform's AI assistants for lesson plans and feedback, responsibly.", False),
    ("Qaida foundations for absolute beginners", "tajweed", "Letter recognition, joining and pace-setting for first-time learners.", False),
    ("Retention: spotting a disengaged student early", "engagement", "Risk signals, proactive outreach and recovery scripts.", False),
]
PROVISIONING_POLICY = [
    ("Google Drive is the single source of truth", "Every document lives in Drive:/OQC/<Department>/<Employee code>. Local copies are not records."),
    ("Device naming convention", "All issued devices are named OQC-DEPT-NNN and registered against the employee record."),
    ("Mandatory MFA", "Two-factor authentication is enforced for every platform account at first sign-in."),
    ("One action onboarding / offboarding", "A single action provisions or revokes Workspace, vault, Drive, ClickUp/Slack, MFA and device."),
    ("Offboarding within 24 hours", "Sessions and API keys are revoked immediately; Drive ownership transfers to the department archive."),
]


# ----------------------------------------------------------------------------- helpers
def _emp(db: Session, id: int) -> Employee:
    e = db.query(Employee).get(id)
    if not e:
        raise HTTPException(404, "Employee not found")
    return e


def can_see_sensitive(user: User) -> bool:
    return rbac.has_permission(user, "employees.update") or rbac.is_management(user)


def _kv_from_form(form, key_field: str, value_field: str) -> dict:
    out: dict[str, float] = {}
    keys = form.getlist(key_field)
    values = form.getlist(value_field)
    for k, v in zip(keys, values):
        k = (k or "").strip().lower().replace(" ", "_")
        if not k:
            continue
        out[k] = parse_float(v, 0.0)
    return out


def _csv(rows: list[list], headers: list[str], filename: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _departments(db: Session) -> list[Department]:
    return db.query(Department).order_by(Department.name).all()


def _employee_options(db: Session, only_active: bool = True) -> list[tuple[int, str]]:
    q = db.query(Employee)
    if only_active:
        q = q.filter(Employee.status.in_(["active", "probation", "on_leave"]))
    return [(e.id, f"{e.employee_code} — {e.full_name}") for e in q.order_by(Employee.full_name)]


def _month_param(value: str | None) -> str:
    if value and len(value) == 7 and value[4] == "-":
        return value
    return month_key()


# ============================================================================== EMPLOYEES
# The ERP's Employee Record columns, in the ERP's own order (docs/AUDIT_HUMAN_RESOURCE.md, Level 3).
EMPLOYEE_HEADERS = ["ID", "Name", "Designation", "Type", "Employment Type", "Status", "Department", "Gender",
                    "Date Of Birth", "Email", "Cell", "WhatsApp", "Shift Name", "Shift Code", "Basic Salary",
                    "Joining Date", "Leaving Date", "Father Name", "Mother Name", "Religion", "Blood Group",
                    "Bank Name", "Bank Account No", "Manager", "Check In Time", "Check Out Time", "Duty Hours",
                    "Leaving Reason", "Remarks"]
BULK_FIELDS = [("status", "Status"), ("department", "Department"), ("shift", "Shift"), ("designation", "Designation"),
               ("employee_type", "Type"), ("manager", "Manager"), ("grade", "Grade")]
INACTIVE_STATUSES = ["resigned", "terminated"]


def _grades(db: Session) -> list[Grade]:
    return db.query(Grade).filter(Grade.status == "active").order_by(Grade.name).all()


@router.get("/employees", include_in_schema=False)
def employees_list(request: Request, page: int = 1, q: str = "", status: str = "", department: str = "", shift: str = "",
                   designation: str = "", employee_type: str = "", gender: str = "",
                   db: Session = Depends(get_db), user: User = Depends(require("employees.view"))):
    query = db.query(Employee)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Employee.full_name.ilike(like), Employee.employee_code.ilike(like),
                                 Employee.email.ilike(like), Employee.designation.ilike(like)))
    if status == "in-active":
        query = query.filter(Employee.status.in_(INACTIVE_STATUSES))
    elif status:
        query = query.filter(Employee.status == status)
    if department:
        query = query.filter(Employee.department_id == parse_int(department))
    if shift:
        query = query.filter(Employee.shift == shift)
    if designation:
        query = query.filter(Employee.designation == designation)
    if employee_type:
        query = query.filter(Employee.employee_type == employee_type)
    if gender:
        query = query.filter(Employee.gender == gender)
    pg = paginate(query.order_by(Employee.employee_code), page, 25)
    show = can_see_sensitive(user)
    rows = []
    for e in pg.items:
        inactive = e.status in INACTIVE_STATUSES
        docs = e.documents or {}
        rows.append({"employee": e, "inactive": inactive,
                     "dob": docs.get("date_of_birth") or "", "remarks": docs.get("remarks") or "",
                     "email": e.email if show else (e.email or "")[:3] + "***",
                     "phone": e.phone if show else (e.phone or "")[:4] + "***",
                     "whatsapp": e.whatsapp if show else (e.whatsapp or "")[:4] + "***",
                     "bank_account_no": e.bank_account_no if show else "****"})
    counts = dict(db.query(Employee.status, func.count(Employee.id)).group_by(Employee.status).all())
    genders = dict(db.query(Employee.gender, func.count(Employee.id)).group_by(Employee.gender).all())
    stats = {"total": sum(counts.values()), "male": genders.get("male", 0), "female": genders.get("female", 0),
             "active": counts.get("active", 0), "probation": counts.get("probation", 0),
             "inactive": sum(counts.get(s, 0) for s in INACTIVE_STATUSES)}
    base = (f"/hr/employees?q={q}&status={status}&department={department}&shift={shift}&designation={designation}"
            f"&employee_type={employee_type}&gender={gender}")
    designations = sorted({d for (d,) in db.query(Employee.designation).distinct() if d} | set(DESIGNATIONS))
    return render(request, "hr/employees_list.html", {
        "user": user, "page": pg, "rows": rows, "q": q, "status": status, "department": department, "shift": shift,
        "designation": designation, "employee_type": employee_type, "gender": gender, "stats": stats,
        "statuses": EMP_STATUSES + ["in-active"], "shifts": SHIFTS, "designations": designations,
        "employee_types": EMPLOYEE_TYPES, "departments": _departments(db), "base_url": base,
        "headers": EMPLOYEE_HEADERS, "group_tabs": GROUP_TABS, "bulk_fields": BULK_FIELDS,
        "managers": _employee_options(db), "grades": _grades(db), "show_sensitive": show,
        "can_bulk": rbac.has_permission(user, "employees.update")})


@router.post("/employees/bulk-update", include_in_schema=False)
async def employees_bulk_update(request: Request, db: Session = Depends(get_db),
                                user: User = Depends(require("employees.update"))):
    """ERP "Apply Bulk Update": set one field on every ticked employee in a single audited action."""
    form = await request.form()
    ids = [int(v) for v in form.getlist("ids") if str(v).isdigit()]
    field = (form.get("field") or "").strip()
    value = (form.get("value") or "").strip()
    rationale = (form.get("rationale") or "").strip()
    if not ids:
        return redirect("/hr/employees", "Tick at least one employee first.", "error")
    if field not in dict(BULK_FIELDS):
        return redirect("/hr/employees", "Choose the field to update.", "error")
    if not value:
        return redirect("/hr/employees", "Choose the new value.", "error")
    if not rationale:
        return redirect("/hr/employees", "A bulk update must be justified with a rationale.", "error")
    employees = db.query(Employee).filter(Employee.id.in_(ids)).all()
    if not employees:
        return redirect("/hr/employees", "None of the selected employees could be found.", "error")
    label = value
    before = {}
    for e in employees:
        if field == "status":
            if value not in EMP_STATUSES:
                return redirect("/hr/employees", "Unknown employee status.", "error")
            before[e.employee_code] = e.status
            e.status = value
            if value in INACTIVE_STATUSES and not e.exit_date:
                e.exit_date = date.today()
        elif field == "department":
            dept = db.get(Department, parse_int(value) or 0)
            if not dept:
                return redirect("/hr/employees", "Unknown department.", "error")
            before[e.employee_code] = e.department.name if e.department else None
            e.department_id = dept.id
            label = dept.name
        elif field == "shift":
            if value not in SHIFTS:
                return redirect("/hr/employees", "Unknown shift.", "error")
            before[e.employee_code] = e.shift
            e.shift = value
            e.shift_code = {"morning": "M", "evening": "E", "night": "N"}[value]
        elif field == "designation":
            before[e.employee_code] = e.designation
            e.designation = value[:100]
        elif field == "employee_type":
            if value not in EMPLOYEE_TYPES:
                return redirect("/hr/employees", "Unknown employee type.", "error")
            before[e.employee_code] = e.employee_type
            e.employee_type = value
        elif field == "manager":
            mgr = db.get(Employee, parse_int(value) or 0)
            if not mgr:
                return redirect("/hr/employees", "Unknown manager.", "error")
            before[e.employee_code] = e.manager.full_name if e.manager else None
            e.manager_id = None if mgr.id == e.id else mgr.id
            label = mgr.full_name
        elif field == "grade":
            g = db.get(Grade, parse_int(value) or 0)
            if not g:
                return redirect("/hr/employees", "Unknown grade.", "error")
            before[e.employee_code] = e.grade_id
            e.grade_id = g.id
            label = g.name
    log_action(db, user, "update", "employees", entity_type="Employee", entity_id=employees[0].id,
               description=f"Bulk update: {dict(BULK_FIELDS)[field]} set to {label} for {len(employees)} employee(s) "
                           f"({', '.join(e.employee_code for e in employees)})",
               rationale=rationale, before=before, after={e.employee_code: label for e in employees},
               request=request, consequential=True)
    db.commit()
    return redirect("/hr/employees", f"{len(employees)} employee(s) updated: {dict(BULK_FIELDS)[field]} = {label}.")


@router.get("/employees/export.csv", include_in_schema=False)
def employees_export(db: Session = Depends(get_db), user: User = Depends(require("employees.view"))):
    rows = []
    for e in db.query(Employee).order_by(Employee.employee_code):
        rows.append([e.employee_code, e.full_name, e.designation, e.department.name if e.department else "", e.employment_type,
                     e.shift, e.status, e.join_date, e.probation_end or "", e.salary_band or "", float(e.base_salary or 0),
                     e.background_check_status, e.device_name or ""])
    return _csv(rows, ["Code", "Name", "Designation", "Department", "Type", "Shift", "Status", "Joined", "Probation end",
                       "Band", "Base salary", "Background check", "Device"], "employees.csv")


def _employee_form_context(db: Session, user: User, mode: str, e: Employee | None) -> dict:
    managers = _employee_options(db)
    if e is not None:
        managers = [o for o in managers if o[0] != e.id]
    return {"user": user, "mode": mode, "e": e, "departments": _departments(db), "branches": db.query(Branch).all(),
            "managers": managers, "statuses": EMP_STATUSES, "types": EMPLOYMENT_TYPES, "shifts": SHIFTS,
            "designations": DESIGNATIONS, "employee_types": EMPLOYEE_TYPES, "grades": _grades(db),
            "religions": ["Islam", "Christianity", "Hinduism", "Other"],
            "blood_groups": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"],
            "docs": (e.documents or {}) if e is not None else {}}


@router.get("/employees/new", include_in_schema=False)
def employee_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.add"))):
    return render(request, "hr/employee_form.html", _employee_form_context(db, user, "new", None))


def _apply_erp_employee_fields(db: Session, user: User, emp: Employee, form, request: Request) -> None:
    """Write the ERP Employee Record fields the shared HR service does not know about.

    Date of birth and free-text remarks have no column of their own, so they live in the employee's
    ``documents`` JSON; everything else is a real column appended for ERP parity.
    """
    fields = ("father_name", "mother_name", "religion", "blood_group", "bank_name", "bank_account_no",
              "shift_code", "whatsapp", "employee_type")
    before = {f: getattr(emp, f, None) for f in fields}
    before["duty_hours"] = emp.duty_hours
    before["grade_id"] = emp.grade_id
    for f in fields:
        if f in form:
            value = (form.get(f) or "").strip()
            if f == "employee_type":
                emp.employee_type = value if value in EMPLOYEE_TYPES else emp.employee_type
            else:
                setattr(emp, f, value or None)
    if not emp.shift_code:
        emp.shift_code = {"morning": "M", "evening": "E", "night": "N"}.get(emp.shift or "morning", "M")
    if "duty_hours" in form:
        emp.duty_hours = parse_float(form.get("duty_hours"), 0) or None
    if "grade_id" in form:
        emp.grade_id = parse_int(form.get("grade_id")) or None
    if "sort_no" in form:
        emp.sort_no = parse_int(form.get("sort_no"), 0) or 0
    docs = dict(emp.documents or {})
    if form.get("date_of_birth"):
        docs["date_of_birth"] = str(parse_date(form.get("date_of_birth")) or "")
    if "remarks" in form:
        docs["remarks"] = (form.get("remarks") or "").strip()
    emp.documents = docs
    after = {f: getattr(emp, f, None) for f in fields}
    after["duty_hours"] = emp.duty_hours
    after["grade_id"] = emp.grade_id
    if before != after:
        log_action(db, user, "update", "employees", entity=emp, request=request,
                   description=f"ERP employee record fields updated for {emp.employee_code}",
                   before=before, after=after, rationale=form.get("rationale") or None)


def _employee_form_data(form) -> dict:
    return {"full_name": form.get("full_name"), "designation": form.get("designation"), "department_id": form.get("department_id"),
            "branch_id": form.get("branch_id"), "manager_id": form.get("manager_id"), "gender": form.get("gender"),
            "email": form.get("email"), "phone": form.get("phone"), "cnic": form.get("cnic"), "address": form.get("address"),
            "join_date": parse_date(form.get("join_date")), "probation_end": parse_date(form.get("probation_end")),
            "employment_type": form.get("employment_type"), "shift": form.get("shift"), "shift_start": form.get("shift_start"),
            "shift_end": form.get("shift_end"), "base_salary": form.get("base_salary"), "currency": form.get("currency") or "PKR",
            "salary_band": form.get("salary_band"), "status": form.get("status"), "is_teacher": parse_bool(form.get("is_teacher")),
            "device_name": form.get("device_name")}


@router.post("/employees/new", include_in_schema=False)
async def employee_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.add"))):
    form = await request.form()
    data = _employee_form_data(form)
    if not (data["full_name"] or "").strip():
        return redirect("/hr/employees/new", "Full name is required.", "error")
    emp, pwd = svc.create_employee(db, data, user, create_login=parse_bool(form.get("create_login")), request=request)
    _apply_erp_employee_fields(db, user, emp, form, request)
    db.commit()
    msg = f"Employee {emp.employee_code} created with {len(svc.ONBOARDING_TEMPLATE)} onboarding tasks."
    if pwd:
        msg += f" Temporary password: {pwd}"
    return redirect(f"/hr/employees/{emp.id}", msg)


@router.get("/employees/{id}", include_in_schema=False)
def employee_detail(id: int, request: Request, tab: str = "overview", month: str = "", db: Session = Depends(get_db),
                    user: User = Depends(require("employees.view"))):
    e = _emp(db, id)
    month = _month_param(month)
    start, end = month_bounds(month)
    ctx: dict = {"user": user, "e": e, "tab": tab, "month": month,
                 "tabs": [(k, l, f"/hr/employees/{e.id}?tab={k}") for k, l in EMPLOYEE_TABS],
                 "show_sensitive": can_see_sensitive(user), "statuses": EMP_STATUSES}
    if tab == "overview":
        ctx["kpis"] = svc.employee_kpis(db, e, month)
        ctx["grade"] = db.get(Grade, e.grade_id) if e.grade_id else None
        ctx["recent_audit"] = db.query(AuditEvent).filter(AuditEvent.entity_type == "Employee", AuditEvent.entity_id == e.id)\
            .order_by(AuditEvent.created_at.desc()).limit(8).all()
        ctx["reports"] = db.query(Employee).filter(Employee.manager_id == e.id).all()
    elif tab == "attendance":
        rows = db.query(HRAttendance).filter(HRAttendance.employee_id == e.id, HRAttendance.date >= start, HRAttendance.date <= end)\
            .order_by(HRAttendance.date.desc(), HRAttendance.session).all()
        grid: dict = {}
        for r in rows:
            grid.setdefault(r.date, {})[r.session] = r
        ctx["att_rows"] = rows
        ctx["grid"] = sorted(grid.items(), reverse=True)
        ctx["summary"] = svc.attendance_summary(db, e.id, start, end)
    elif tab == "leaves":
        ctx["leaves"] = db.query(Leave).filter(Leave.person_type == "employee", Leave.employee_id == e.id)\
            .order_by(Leave.start_date.desc()).limit(50).all()
        ctx["balance"] = svc.leave_balance(db, e)
        ctx["leave_types"] = LEAVE_TYPES
    elif tab == "salary":
        ctx["structure"] = db.query(SalaryStructure).filter(SalaryStructure.employee_id == e.id).first()
    elif tab == "payslips":
        ctx["payslips"] = db.query(Payslip).filter(Payslip.employee_id == e.id).order_by(Payslip.id.desc()).limit(24).all()
    elif tab == "violations":
        ctx["violations"] = db.query(Violation).filter(Violation.employee_id == e.id).order_by(Violation.date.desc()).all()
        ctx["violation_types"] = VIOLATION_TYPES
        ctx["severities"] = SEVERITIES
    elif tab == "comp":
        ctx["bonuses"] = db.query(Bonus).filter(Bonus.employee_id == e.id).order_by(Bonus.id.desc()).all()
        ctx["advances"] = db.query(SalaryAdvance).filter(SalaryAdvance.employee_id == e.id).order_by(SalaryAdvance.id.desc()).all()
    elif tab == "onboarding":
        ctx["tasks"] = db.query(OnboardingTask).filter(OnboardingTask.employee_id == e.id).order_by(OnboardingTask.due_date, OnboardingTask.id).all()
    elif tab == "provisioning":
        ctx["records"] = db.query(ProvisioningRecord).filter(ProvisioningRecord.employee_id == e.id).order_by(ProvisioningRecord.id.desc()).all()
        ctx["policy"] = PROVISIONING_POLICY
    elif tab == "development":
        ctx["plan"] = db.query(DevelopmentPlan).filter(DevelopmentPlan.employee_id == e.id).order_by(DevelopmentPlan.id.desc()).first()
        ctx["trainings"] = (db.query(TrainingAssignment).filter(TrainingAssignment.teacher_id == e.teacher.id)
                            .order_by(TrainingAssignment.id.desc()).all() if e.teacher else [])
    elif tab == "audit":
        ctx["events"] = db.query(AuditEvent).filter(or_((AuditEvent.entity_type == "Employee") & (AuditEvent.entity_id == e.id),
                                                        (AuditEvent.entity_type == "SalaryStructure") & (AuditEvent.entity_id.in_(
                                                            db.query(SalaryStructure.id).filter(SalaryStructure.employee_id == e.id)))))\
            .order_by(AuditEvent.created_at.desc()).limit(200).all()
    return render(request, "hr/employee_detail.html", ctx)


@router.get("/employees/{id}/edit", include_in_schema=False)
def employee_edit(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.update"))):
    e = _emp(db, id)
    return render(request, "hr/employee_form.html", _employee_form_context(db, user, "edit", e))


@router.post("/employees/{id}/edit", include_in_schema=False)
async def employee_update(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.update"))):
    e = _emp(db, id)
    form = await request.form()
    data = _employee_form_data(form)
    data["exit_date"] = parse_date(form.get("exit_date"))
    data["exit_reason"] = form.get("exit_reason")
    data["mfa_enforced"] = parse_bool(form.get("mfa_enforced"))
    data["rationale"] = form.get("rationale")
    for f in ("join_date", "probation_end", "exit_date"):  # never null a date the form did not carry
        if data.get(f) is None:
            data.pop(f, None)
    svc.update_employee(db, e, data, user, request=request)
    _apply_erp_employee_fields(db, user, e, form, request)
    db.commit()
    return redirect(f"/hr/employees/{e.id}", "Employee updated.")


@router.post("/employees/{id}/background-check", include_in_schema=False)
async def employee_background(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.update"))):
    e = _emp(db, id)
    form = await request.form()
    rationale = (form.get("rationale") or "").strip()
    status = form.get("status") or "verified"
    if not rationale:
        return redirect(f"/hr/employees/{e.id}", "A rationale is required to change a background check result.", "error")
    svc.verify_background(db, e, status, user, rationale, request=request)
    db.commit()
    return redirect(f"/hr/employees/{e.id}", f"Background check marked {status}.")


@router.post("/employees/{id}/salary", include_in_schema=False)
async def employee_salary(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.update"))):
    e = _emp(db, id)
    form = await request.form()
    rationale = (form.get("rationale") or "").strip()
    if not rationale:
        return redirect(f"/hr/employees/{e.id}?tab=salary", "Salary changes require a rationale.", "error")
    data = {"basic": form.get("basic"), "per_class_rate": form.get("per_class_rate"),
            "absence_deduction_per_day": form.get("absence_deduction_per_day"),
            "late_deduction_per_instance": form.get("late_deduction_per_instance"),
            "allowances": _kv_from_form(form, "allow_key", "allow_value"),
            "deductions": _kv_from_form(form, "ded_key", "ded_value"),
            "effective_from": parse_date(form.get("effective_from")), "currency": form.get("currency")}
    svc.update_salary_structure(db, e, data, user, rationale, request=request)
    db.commit()
    return redirect(f"/hr/employees/{e.id}?tab=salary", "Salary structure updated.")


@router.post("/employees/{id}/onboarding/{task_id}", include_in_schema=False)
async def onboarding_toggle(id: int, task_id: int, request: Request, db: Session = Depends(get_db),
                            user: User = Depends(require("employees.update"))):
    e = _emp(db, id)
    t = db.query(OnboardingTask).get(task_id)
    if not t or t.employee_id != e.id:
        raise HTTPException(404, "Task not found")
    t.status = "pending" if t.status == "completed" else "completed"
    t.completed_at = datetime.utcnow() if t.status == "completed" else None
    log_action(db, user, "update", "employees", entity=t, description=f"Onboarding task '{t.title}' -> {t.status} for {e.employee_code}", request=request)
    db.commit()
    return redirect(f"/hr/employees/{e.id}?tab=onboarding", f"Task marked {t.status}.")


@router.post("/employees/{id}/onboarding", include_in_schema=False)
async def onboarding_add(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.update"))):
    e = _emp(db, id)
    form = await request.form()
    title = (form.get("title") or "").strip()
    if not title:
        return redirect(f"/hr/employees/{e.id}?tab=onboarding", "A task title is required.", "error")
    t = OnboardingTask(employee_id=e.id, title=title, category=form.get("category") or "general",
                       due_date=parse_date(form.get("due_date")), assigned_to_id=user.id)
    db.add(t)
    log_action(db, user, "create", "employees", entity=t, description=f"Onboarding task added for {e.employee_code}: {title}", request=request)
    db.commit()
    return redirect(f"/hr/employees/{e.id}?tab=onboarding", "Onboarding task added.")


@router.post("/employees/{id}/development-plan", include_in_schema=False)
async def employee_plan(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.update"))):
    e = _emp(db, id)
    form = await request.form()
    goals_raw = (form.get("goals") or "").strip()
    goals = None
    if goals_raw:
        try:
            goals = json.loads(goals_raw)
            if not isinstance(goals, list):
                raise ValueError
        except Exception:
            goals = [{"goal": line.strip(), "month": i + 1, "status": "todo"} for i, line in enumerate(goals_raw.splitlines()) if line.strip()]
    data = {"title": form.get("title"), "ai_fluency_level": form.get("ai_fluency_level"),
            "start_date": parse_date(form.get("start_date")), "end_date": parse_date(form.get("end_date")),
            "progress_pct": form.get("progress_pct"), "status": form.get("status")}
    if goals is not None:
        data["goals"] = goals
    svc.upsert_development_plan(db, e, data, user, request=request)
    db.commit()
    return redirect(f"/hr/employees/{e.id}?tab=development", "Development plan saved.")


@router.post("/employees/{id}/bonus", include_in_schema=False)
async def employee_bonus(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.add"))):
    e = _emp(db, id)
    form = await request.form()
    amount = parse_float(form.get("amount"), 0)
    if amount <= 0:
        return redirect(f"/hr/employees/{e.id}?tab=comp", "Bonus amount must be greater than zero.", "error")
    svc.request_bonus(db, e, amount, form.get("bonus_type") or "performance", form.get("reason"),
                      _month_param(form.get("period")), user, request=request)
    db.commit()
    return redirect(f"/hr/employees/{e.id}?tab=comp", "Bonus proposed and queued for approval.")


@router.post("/employees/{id}/advance", include_in_schema=False)
async def employee_advance(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.add"))):
    e = _emp(db, id)
    form = await request.form()
    amount = parse_float(form.get("amount"), 0)
    if amount <= 0:
        return redirect(f"/hr/employees/{e.id}?tab=comp", "Advance amount must be greater than zero.", "error")
    svc.request_advance(db, e, amount, parse_int(form.get("installments"), 1) or 1, form.get("reason"), user, request=request)
    db.commit()
    return redirect(f"/hr/employees/{e.id}?tab=comp", "Salary advance requested.")


@router.post("/employees/{id}/provision", include_in_schema=False)
async def employee_provision(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("provisioning.execute"))):
    e = _emp(db, id)
    form = await request.form()
    action = form.get("action") or "onboard"
    try:
        svc.provision(db, e, action, user, request=request)
    except ValueError as exc:
        return redirect(f"/hr/employees/{e.id}?tab=provisioning", str(exc), "error")
    db.commit()
    return redirect(f"/hr/employees/{e.id}?tab=provisioning", f"{action.title()} provisioning completed.", "warning" if action == "offboard" else "success")


# ============================================================================== SELF SERVICE
@router.get("/me", include_in_schema=False)
def me(request: Request, tab: str = "overview", month: str = "", date_from: str = "", date_to: str = "",
       attendance_type: str = "", db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    user = ctx.user
    e = ctx.employee
    month = _month_param(month)
    start, end = month_bounds(month)
    data: dict = {"user": user, "e": e, "tab": tab, "month": month, "tabs": [(k, l, f"/hr/me?tab={k}") for k, l in ME_TABS],
                  "leave_types": LEAVE_TYPES, "categories": GRIEVANCE_CATEGORIES}
    if e is None:
        return render(request, "hr/me.html", data)
    rows = db.query(HRAttendance).filter(HRAttendance.employee_id == e.id, HRAttendance.date >= start, HRAttendance.date <= end)\
        .order_by(HRAttendance.date.desc(), HRAttendance.session).all()
    grid: dict = {}
    for r in rows:
        grid.setdefault(r.date, {})[r.session] = r
    today_rows = {r.session: r for r in db.query(HRAttendance).filter(HRAttendance.employee_id == e.id, HRAttendance.date == date.today())}
    # Their Attendance Sheet filters are From Date / To Date / Attendance Type, defaulting to this month.
    sheet_from = parse_date(date_from) or start
    sheet_to = parse_date(date_to) or end
    if sheet_to < sheet_from:
        sheet_from, sheet_to = sheet_to, sheet_from
    atype = attendance_type if attendance_type in dict(svc.ESS_ATTENDANCE_TYPES) else ""
    data.update({"summary": svc.attendance_summary(db, e.id, start, end), "grid": sorted(grid.items(), reverse=True),
                 "today_rows": today_rows, "kpis": svc.employee_kpis(db, e, month),
                 # Today's Attendance panel and the one Mark Attendance button, as their dashboard has it.
                 "today_attendance": svc.attendance_window(db, e),
                 "sheet": svc.attendance_sheet(db, e, sheet_from, sheet_to, atype),
                 "sheet_from": sheet_from.isoformat(), "sheet_to": sheet_to.isoformat(),
                 "attendance_type": atype, "attendance_types": svc.ESS_ATTENDANCE_TYPES,
                 "change_statuses": [("present", "Present"), ("late", "Late Coming"), ("absent", "Absent"),
                                     ("leave", "On Leave"), ("half_day", "Half Day")],
                 "leaves": db.query(Leave).filter(Leave.person_type == "employee", Leave.employee_id == e.id).order_by(Leave.start_date.desc()).limit(30).all(),
                 "balance": svc.leave_balance(db, e),
                 "payslips": db.query(Payslip).filter(Payslip.employee_id == e.id).order_by(Payslip.id.desc()).limit(24).all(),
                 "violations": db.query(Violation).filter(Violation.employee_id == e.id).order_by(Violation.date.desc()).all(),
                 "bonuses": db.query(Bonus).filter(Bonus.employee_id == e.id).order_by(Bonus.id.desc()).all(),
                 "advances": db.query(SalaryAdvance).filter(SalaryAdvance.employee_id == e.id).order_by(SalaryAdvance.id.desc()).all(),
                 "complaints": _my_complaints(db, e),
                 "complaint_types": STAFF_COMPLAINT_TYPES,
                 "schedule": _my_schedule(db, e),
                 "plan": db.query(DevelopmentPlan).filter(DevelopmentPlan.employee_id == e.id).order_by(DevelopmentPlan.id.desc()).first(),
                 "trainings": (db.query(TrainingAssignment).filter(TrainingAssignment.teacher_id == e.teacher.id)
                               .order_by(TrainingAssignment.id.desc()).all() if e.teacher else []),
                 "my_grievances": db.query(Grievance).filter(Grievance.submitted_by_id == user.id).order_by(Grievance.id.desc()).all(),
                 # ERP Employee Requests raised by this member of staff, plus the vocabulary for the form.
                 "my_requests": db.query(EmployeeRequest).filter(EmployeeRequest.employee_id == e.id)
                                  .order_by(EmployeeRequest.id.desc()).limit(20).all(),
                 "request_types": EMPLOYEE_REQUEST_TYPES})
    return render(request, "hr/me.html", data)


def _my_complaints(db: Session, e: Employee) -> list:
    """The signed-in employee's own complaints (the confidential channel stays on the grievance tab)."""
    from app.models.hr_erp import StaffComplaint
    return (db.query(StaffComplaint).filter(StaffComplaint.employee_id == e.id)
            .order_by(StaffComplaint.id.desc()).limit(30).all())


def _my_schedule(db: Session, e: Employee) -> dict:
    """My Schedule: a teacher's own upcoming classes; for everyone else, their shift and duty hours.

    Theirs opens the teacher's Online Class page. A member of the admin or marketing staff has no classes,
    so rather than an empty grid they get the working pattern the attendance rules are measured against.
    """
    start, end = svc.shift_minutes(e)
    out: dict = {"is_teacher": e.teacher is not None, "sessions": [], "counts": {},
                 "shift": (e.shift or "").title() or "-",
                 "shift_window": f"{start // 60:02d}:{start % 60:02d} - {(end // 60) % 24:02d}:{end % 60:02d}",
                 "duty_hours": float(getattr(e, "duty_hours", None) or svc.DEFAULT_DUTY_HOURS),
                 "session_duty_hours": svc.session_duty_hours(e),
                 "holidays": svc.holidays_between(db, date.today(), date.today() + timedelta(days=60), e.shift)}
    if e.teacher is None:
        return out
    from app.models.scheduling import ClassSession
    today = date.today()
    rows = (db.query(ClassSession)
            .filter(ClassSession.teacher_id == e.teacher.id, ClassSession.date >= today,
                    ClassSession.date <= today + timedelta(days=14))
            .order_by(ClassSession.date, ClassSession.start_time).limit(60).all())
    out["sessions"] = rows
    out["counts"] = {"upcoming": len(rows), "today": sum(1 for r in rows if r.date == today)}
    return out


@router.post("/me/check", include_in_schema=False)
async def me_check(request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    if ctx.employee is None:
        return redirect("/hr/me", "No employee record is linked to your account.", "error")
    form = await request.form()
    action = "out" if form.get("action") == "out" else "in"
    session = form.get("session") or ("pm" if svc.org_now().hour >= 13 else "am")
    row = svc.mark_attendance(db, ctx.employee, session, action, ctx.user,
                              ip=request.client.host if request.client else None, request=request)
    db.commit()
    return redirect("/hr/me?tab=attendance", f"{session.upper()} check-{action} recorded at {row.check_in.strftime('%H:%M') if action == 'in' and row.check_in else (row.check_out.strftime('%H:%M') if row.check_out else '')} ({row.status}).")


@router.post("/me/correction", include_in_schema=False)
async def me_correction(request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    if ctx.employee is None:
        return redirect("/hr/me", "No employee record is linked to your account.", "error")
    form = await request.form()
    row = db.query(HRAttendance).get(parse_int(form.get("attendance_id"), 0) or 0)
    if not row or row.employee_id != ctx.employee.id:
        return redirect("/hr/me?tab=attendance", "Attendance record not found.", "error")
    reason = (form.get("reason") or "").strip()
    if not reason:
        return redirect("/hr/me?tab=attendance", "Please explain what needs correcting.", "error")
    svc.request_correction(db, row, reason, ctx.user, request=request)
    db.commit()
    return redirect("/hr/me?tab=attendance", "Correction request submitted to People & Culture.")


@router.post("/me/leave", include_in_schema=False)
async def me_leave(request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    if ctx.employee is None:
        return redirect("/hr/me", "No employee record is linked to your account.", "error")
    form = await request.form()
    start = parse_date(form.get("start_date"))
    end = parse_date(form.get("end_date")) or start
    if not start:
        return redirect("/hr/me?tab=leaves", "A start date is required.", "error")
    try:
        svc.request_leave(db, ctx.employee, form.get("leave_type") or "casual", start, end, form.get("reason"), ctx.user, request=request)
    except ValueError as exc:
        return redirect("/hr/me?tab=leaves", str(exc), "error")
    db.commit()
    return redirect("/hr/me?tab=leaves", "Leave request submitted.")


@router.post("/me/request", include_in_schema=False)
async def me_request(request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    """Staff raise their own ERP Employee Request (certificate, equipment, shift change...) from /hr/me."""
    if ctx.employee is None:
        return redirect("/hr/me", "No employee record is linked to your account.", "error")
    form = await request.form()
    description = (form.get("description") or "").strip()
    if not description:
        return redirect("/hr/me", "Describe what you are asking People & Culture for.", "error")
    rtype = form.get("request_type") or "Other"
    r = EmployeeRequest(employee_id=ctx.employee.id, request_date=date.today(),
                        request_type=rtype if rtype in EMPLOYEE_REQUEST_TYPES else "Other",
                        description=description, status="pending")
    db.add(r)
    db.flush()
    log_action(db, ctx.user, "create", "employees", entity=r, request=request, rationale=description,
               description=f"Employee request ({r.request_type}) raised by {ctx.employee.employee_code}")
    for u in svc.hr_notify_users(db):
        notify(db, u, "Employee request raised",
               f"{ctx.employee.full_name} asked for: {r.request_type}.", event_type="hr_request",
               link="/hr/requests?status=pending")
    db.commit()
    return redirect("/hr/me", "Your request was sent to People & Culture.")


@router.post("/me/grievance", include_in_schema=False)
async def me_grievance(request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    form = await request.form()
    subject = (form.get("subject") or "").strip()
    description = (form.get("description") or "").strip()
    if not subject or not description:
        return redirect("/hr/me?tab=grievance", "A subject and a description are required.", "error")
    svc.open_grievance(db, subject, description, form.get("category") or "workplace", ctx.user, employee=ctx.employee,
                       is_anonymous=parse_bool(form.get("is_anonymous")), request=request)
    db.commit()
    return redirect("/hr/me?tab=grievance", "Your grievance was sent confidentially to the Head of People & Culture.")


# ------------------------------------------------------------------ Mark Attendance (their dashboard button)
@router.post("/me/attendance/mark", include_in_schema=False)
async def me_mark_attendance(request: Request, db: Session = Depends(get_db),
                             ctx: UserContext = Depends(get_user_context)):
    """Their **Mark Attendance** button: first press of the day clocks in, the next clocks out, a third says so.

    The request IP is recorded on the attendance row, and the late figure comes from the shared
    recompute_late_minutes so payroll reads exactly what the member of staff reads.
    """
    if ctx.employee is None:
        return redirect("/hr/me", "No employee record is linked to your account.", "error")
    row, action, message = svc.punch(db, ctx.employee, ctx.user,
                                     ip=request.client.host if request.client else None, request=request)
    db.commit()
    return redirect("/hr/me", message, "success" if action != "none" else "warning")


# ------------------------------------------------------------------ Requests: self-service advance
@router.post("/me/advance", include_in_schema=False)
async def me_advance(request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    """Their Advance Request form: Request Date, Amount, No Of Installments, Remarks. Hr Remarks is theirs."""
    if ctx.employee is None:
        return redirect("/hr/me", "No employee record is linked to your account.", "error")
    form = await request.form()
    amount = parse_float(form.get("amount"), 0)
    if amount <= 0:
        return redirect("/hr/me?tab=requests", "The advance amount must be greater than zero.", "error")
    a = svc.request_advance(db, ctx.employee, amount, max(1, parse_int(form.get("installments"), 1) or 1),
                            (form.get("reason") or form.get("remarks") or "").strip() or None, ctx.user,
                            request=request)
    a.request_date = parse_date(form.get("request_date")) or date.today()
    db.commit()
    return redirect("/hr/me?tab=requests", f"Advance request for {erp_money(a.amount)} sent to People & Culture.")


# ------------------------------------------------------------------ Account Ledger
@router.get("/me/ledger", include_in_schema=False)
def me_ledger(request: Request, date_from: str = "", date_to: str = "", q: str = "", print_view: int = 0,
              db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    """Their Account Ledger: Srl., Date, VID, Description, Amount Dr., Amount Cr., Balance, with Print.

    Scoped to the signed-in member of staff; nobody reads anyone else's account from here.
    """
    e = ctx.employee
    if e is None:
        return render(request, "hr/me_ledger.html", {"user": ctx.user, "e": None, "report": None,
                                                     "date_from": date_from, "date_to": date_to, "q": q,
                                                     "print_view": bool(print_view), "qs": ""})
    df, dt_ = parse_date(date_from), parse_date(date_to)
    if df and dt_ and dt_ < df:
        df, dt_ = dt_, df
    report = svc.employee_ledger(db, e, df, dt_, q)
    qs = f"date_from={df or ''}&date_to={dt_ or ''}&q={q}"
    return render(request, "hr/me_ledger.html", {
        "user": ctx.user, "e": e, "report": report, "q": q,
        "date_from": df.isoformat() if df else "", "date_to": dt_.isoformat() if dt_ else "",
        "print_view": bool(print_view), "qs": qs})


# ------------------------------------------------------------------ Salary Slips: per-month print
@router.get("/me/payslips/{id}/print", include_in_schema=False)
def me_payslip_print(id: int, request: Request, print_view: int = 0, db: Session = Depends(get_db),
                     ctx: UserContext = Depends(get_user_context)):
    """One month's salary slip, laid out for paper. Their Salary Slips list prints a row this way."""
    ps = db.get(Payslip, id)
    if not ps:
        raise HTTPException(404, "Payslip not found")
    own = ctx.employee is not None and ps.employee_id == ctx.employee.id
    if not own and not rbac.has_permission(ctx.user, "payroll.view"):
        raise PermissionDenied("payroll.view")
    return render(request, "hr/me_payslip_print.html", {"user": ctx.user, "ps": ps, "own": own,
                                                        "e": ps.employee, "run": ps.payroll_run,
                                                        "print_view": bool(print_view)})


# ============================================================================== TIME AND ATTENDANCE MANAGEMENT
# The ERP's "Time and Attendance Management" group (docs/AUDIT_HUMAN_RESOURCE.md, Level 3): Daily Attendance,
# Attendance Change Requests, Leave Assignment, Leave Management, Employees Progress Sheet and the Attendance
# Report, plus the two self-service posts staff use from /hr/me. Lists follow the ERP request pattern used by
# app/web/requests.py: approval tiles, a filter bar, a bordered table with the ERP's column labels, Create and
# an "Actions -> Change Status" bulk form.
CHANGE_STATUSES = ["pending", "approved", "rejected", "cancelled"]
CHANGE_TILES = [("Pending", "pending", "clock"), ("Approved", "approved", "check-circle-2"),
                ("Rejected", "rejected", "x-circle"), ("Cancelled", "cancelled", "ban")]
ATTENDANCE_STATUSES = ["present", "absent", "late", "leave", "half_day", "holiday"]
ATTENDANCE_TYPE_LABELS = {"present": "Present", "late": "Late Coming", "absent": "Absent", "leave": "On Leave",
                          "half_day": "Half Day", "holiday": "Holiday"}
SHIFT_GROUPS = ["morning", "evening", "night"]
RATINGS = [(1, "1 - Needs attention"), (2, "2 - Below expectation"), (3, "3 - Meets expectation"),
           (4, "4 - Above expectation"), (5, "5 - Outstanding")]
# Jinja has no list comprehensions, so select options are built here.
ATTENDANCE_TYPE_OPTIONS = [(s, ATTENDANCE_TYPE_LABELS[s]) for s in ATTENDANCE_STATUSES]
APPROVAL_OPTIONS = [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"),
                    ("cancelled", "Cancelled")]


def _hhmm(value) -> str:
    """Parse an <input type=time> value into a datetime.time, or None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value[:5], "%H:%M").time()
    except ValueError:
        return None


def _at(day: date, value) -> datetime:
    t = _hhmm(value)
    return datetime.combine(day, t) if t else None


def _designations(db: Session) -> list[str]:
    from app.models.hr_erp import DESIGNATIONS
    seen = {d for (d,) in db.query(Employee.designation).distinct() if d}
    return sorted(seen | set(DESIGNATIONS))


def _employee_types() -> list[str]:
    from app.models.hr_erp import EMPLOYEE_TYPES
    return list(EMPLOYEE_TYPES)


def _staff_query(db: Session, shift: str = "", employee_type: str = "", designation: str = "",
                 department: str = "", employee_id: int = 0, only_active: bool = True):
    q = db.query(Employee)
    if only_active:
        q = q.filter(Employee.status.in_(["active", "probation", "on_leave"]))
    if shift:
        q = q.filter(Employee.shift == shift)
    if employee_type:
        q = q.filter(Employee.employee_type == employee_type)
    if designation:
        q = q.filter(Employee.designation == designation)
    if parse_int(department):
        q = q.filter(Employee.department_id == int(department))
    if employee_id:
        q = q.filter(Employee.id == employee_id)
    return q.order_by(Employee.full_name)


# ------------------------------------------------------------------ Daily Attendance + the existing tabs
@router.get("/attendance", include_in_schema=False)
def attendance_board(request: Request, tab: str = "daily", day: str = "", month: str = "", employee_id: int = 0,
                     shift: str = "", db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.view"))):
    d = parse_date(day) or date.today()
    month = _month_param(month)
    start, end = month_bounds(month)
    if tab == "today":
        tab = "daily"
    fallback = False
    if tab == "daily" and not day:
        # Nobody has punched yet today (early morning, a Sunday, a holiday): open on the last recorded
        # working day rather than an empty grid. An explicit date in the picker is always honoured.
        if not db.query(HRAttendance.id).filter(HRAttendance.date == d).first():
            last = (db.query(func.max(HRAttendance.date)).filter(HRAttendance.date <= d).scalar())
            if last:
                d, fallback = last, True
    tabs = [("daily", "Daily Attendance", "/hr/attendance?tab=daily"), ("monthly", "Monthly grid", "/hr/attendance?tab=monthly"),
            ("corrections", "Corrections", "/hr/attendance?tab=corrections"), ("late", "Late report", "/hr/attendance?tab=late")]
    ctx: dict = {"user": user, "tab": tab, "tabs": tabs, "day": d, "month": month, "employee_id": employee_id,
                 "shift": shift, "shifts": SHIFT_GROUPS, "employees": _employee_options(db),
                 "statuses": ATTENDANCE_STATUSES, "type_labels": ATTENDANCE_TYPE_LABELS,
                 "type_options": ATTENDANCE_TYPE_OPTIONS, "fallback": fallback, "today_date": date.today()}
    employees = db.query(Employee).filter(Employee.status.in_(["active", "probation", "on_leave"])).order_by(Employee.full_name).all()
    if tab == "daily":
        staff = _staff_query(db, shift=shift).all()
        ids = [e.id for e in staff]
        rows = (db.query(HRAttendance).filter(HRAttendance.date == d, HRAttendance.employee_id.in_(ids or [-1]))
                .all()) if ids else []
        by_emp: dict = {}
        for r in rows:
            by_emp.setdefault(r.employee_id, {})[r.session] = r
        grid = []
        for e in staff:
            for session in ("am", "pm"):
                r = by_emp.get(e.id, {}).get(session)
                if r is None:
                    continue
                grid.append({"row": r, "employee": e, "session": session,
                             "details": svc.employee_line(e),
                             "duration": svc.worked_hours(r), "shortage": svc.shortage_hours(r, e),
                             "duty": svc.session_duty_hours(e)})
        # The ERP's three pick-lists of staff for the day, each labelled "code - name - department - shift".
        absent_list, leave_list, present_list, unmarked = [], [], [], []
        on_leave_ids = {l.employee_id for l in db.query(Leave).filter(
            Leave.person_type == "employee", Leave.status == "approved", Leave.start_date <= d, Leave.end_date >= d)}
        for e in staff:
            sessions = by_emp.get(e.id, {})
            statuses = {r.status for r in sessions.values()}
            line = svc.employee_line(e)
            if not sessions:
                unmarked.append({"employee": e, "line": line})
            if "leave" in statuses or e.id in on_leave_ids:
                leave_list.append({"employee": e, "line": line})
            elif "absent" in statuses:
                absent_list.append({"employee": e, "line": line})
            elif statuses & {"present", "late", "half_day"}:
                present_list.append({"employee": e, "line": line})
        ctx.update({"grid": grid, "absent_list": absent_list, "leave_list": leave_list, "present_list": present_list,
                    "unmarked": unmarked, "staff_options": [(e.id, svc.employee_line(e)) for e in staff],
                    "is_holiday": not svc.is_working_day(db, d),
                    "holidays": svc.holidays_between(db, d, d),
                    "counts": {"present": len(present_list), "absent": len(absent_list), "leave": len(leave_list),
                               "late": sum(1 for g in grid if g["row"].status == "late"),
                               "shortage": sum(1 for g in grid if g["shortage"] > 0)}})
    elif tab == "monthly":
        emp = db.get(Employee, employee_id) if employee_id else (employees[0] if employees else None)
        ctx["employee"] = emp
        if emp:
            rows = db.query(HRAttendance).filter(HRAttendance.employee_id == emp.id, HRAttendance.date >= start,
                                                 HRAttendance.date <= end).all()
            by_day: dict = {}
            for r in rows:
                by_day.setdefault(r.date, {})[r.session] = r
            days = []
            d0 = start
            while d0 <= end:
                days.append((d0, by_day.get(d0, {})))
                d0 += timedelta(days=1)
            ctx["days"] = days
            ctx["summary"] = svc.attendance_summary(db, emp.id, start, end)
    elif tab == "corrections":
        ctx["corrections"] = db.query(HRAttendance).filter(HRAttendance.correction_requested.is_(True))\
            .order_by(HRAttendance.correction_status == "pending", HRAttendance.date.desc()).limit(200).all()
        ctx["pending_count"] = db.query(func.count(HRAttendance.id)).filter(HRAttendance.correction_status == "pending").scalar() or 0
    elif tab == "late":
        rows = db.query(HRAttendance.employee_id, func.count(HRAttendance.id), func.sum(HRAttendance.late_minutes))\
            .filter(HRAttendance.status == "late", HRAttendance.date >= start, HRAttendance.date <= end)\
            .group_by(HRAttendance.employee_id).order_by(func.count(HRAttendance.id).desc()).all()
        emap = {e.id: e for e in db.query(Employee)}
        ctx["late_rows"] = [{"employee": emap.get(eid), "count": n, "minutes": int(m or 0)} for eid, n, m in rows if emap.get(eid)]
    return render(request, "hr/attendance.html", ctx)


@router.get("/attendance/export.csv", include_in_schema=False)
def attendance_export(month: str = "", db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.view"))):
    month = _month_param(month)
    start, end = month_bounds(month)
    rows = []
    q = db.query(HRAttendance).filter(HRAttendance.date >= start, HRAttendance.date <= end).order_by(HRAttendance.date, HRAttendance.employee_id)
    emap = {e.id: e for e in db.query(Employee)}
    for r in q:
        e = emap.get(r.employee_id)
        rows.append([r.date, e.employee_code if e else r.employee_id, e.full_name if e else "", r.session, r.status,
                     r.check_in.strftime("%H:%M") if r.check_in else "", r.check_out.strftime("%H:%M") if r.check_out else "",
                     r.late_minutes or 0, svc.worked_hours(r), svc.shortage_hours(r, e), r.correction_status or ""])
    return _csv(rows, ["Date", "Code", "Employee", "Session", "Status", "Check in", "Check out", "Late minutes",
                       "Duration", "Shortage", "Correction"], f"attendance-{month}.csv")


@router.post("/attendance/mark-absent", include_in_schema=False)
async def attendance_mark_absent(request: Request, db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.update"))):
    form = await request.form()
    d = parse_date(form.get("day")) or date.today()
    n = svc.mark_absent_for_missing(db, d, user)
    db.commit()
    return redirect(f"/hr/attendance?tab=daily&day={d}", f"{n} missing session(s) closed off for {d}.", "warning" if n else "info")


@router.post("/attendance/set", include_in_schema=False)
async def attendance_set(request: Request, db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.update"))):
    form = await request.form()
    e = _emp(db, parse_int(form.get("employee_id"), 0) or 0)
    d = parse_date(form.get("day")) or date.today()
    svc.set_attendance_status(db, e, d, form.get("session") or "am", form.get("status") or "present", user,
                              note=form.get("note"), request=request)
    db.commit()
    return redirect(form.get("next") or f"/hr/attendance?tab=daily&day={d}", f"{e.full_name} marked {form.get('status')} for {d}.")


@router.post("/attendance/row", include_in_schema=False)
async def attendance_row_save(request: Request, db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.update"))):
    """Inline edit of one Daily Attendance grid row: type, login time and logout time in one audited POST."""
    form = await request.form()
    e = _emp(db, parse_int(form.get("employee_id"), 0) or 0)
    d = parse_date(form.get("day")) or date.today()
    session = form.get("session") or "am"
    status = form.get("status") or "present"
    if status not in ATTENDANCE_STATUSES:
        return redirect(form.get("next") or f"/hr/attendance?day={d}", "Choose a valid attendance type.", "error")
    row = svc.save_attendance_row(db, e, d, session, status, _at(d, form.get("check_in")), _at(d, form.get("check_out")),
                                  user, note=(form.get("note") or "").strip() or None, request=request)
    db.commit()
    return redirect(form.get("next") or f"/hr/attendance?tab=daily&day={d}",
                    f"{e.full_name} - {d} {session.upper()} saved as {row.status}.")


@router.post("/attendance/corrections/{id}", include_in_schema=False)
async def attendance_correction_decide(id: int, request: Request, db: Session = Depends(get_db),
                                       user: User = Depends(require("hr_attendance.approve"))):
    row = db.get(HRAttendance, id)
    if not row:
        raise HTTPException(404, "Attendance record not found")
    form = await request.form()
    approve = form.get("decision") == "approve"
    svc.decide_correction(db, row, approve, user, new_status=form.get("new_status"), note=form.get("note"), request=request)
    db.commit()
    return redirect("/hr/attendance?tab=corrections", f"Correction {'approved' if approve else 'rejected'}.")


# ------------------------------------------------------------------ Attendance Change Requests
def _change_request_model():
    from app.models.hr_erp import AttendanceChangeRequest
    return AttendanceChangeRequest


@router.get("/attendance/change-requests", include_in_schema=False)
def change_requests(request: Request, page: int = 1, status: str = "", employee: str = "", shift: str = "",
                    date_from: str = "", date_to: str = "", q: str = "", db: Session = Depends(get_db),
                    user: User = Depends(require("hr_attendance.view"))):
    ACR = _change_request_model()
    query = db.query(ACR)
    if status:
        query = query.filter(ACR.status == status)
    if parse_int(employee):
        query = query.filter(ACR.employee_id == int(employee))
    if shift:
        query = query.filter(ACR.employee_id.in_([e.id for e in _staff_query(db, shift=shift, only_active=False)] or [-1]))
    if q:
        query = query.filter(ACR.employee_id.in_(
            [e.id for e in db.query(Employee).filter(or_(Employee.full_name.ilike(f"%{q}%"),
                                                         Employee.employee_code.ilike(f"%{q}%")))] or [-1]))
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(ACR.attendance_date >= df)
    if dt:
        query = query.filter(ACR.attendance_date <= dt)
    pg = paginate(query.order_by(ACR.id.desc()), page, 25)
    counts = dict(db.query(ACR.status, func.count(ACR.id)).group_by(ACR.status).all())
    counts = {s: counts.get(s, 0) for s in CHANGE_STATUSES}
    base = (f"/hr/attendance/change-requests?status={status}&employee={employee}&shift={shift}"
            f"&date_from={date_from}&date_to={date_to}&q={q}")
    return render(request, "hr/attendance_change_requests.html", {
        "user": user, "page": pg, "counts": counts, "tiles": CHANGE_TILES, "statuses": CHANGE_STATUSES,
        "filters": {"status": status, "employee": employee, "shift": shift, "date_from": date_from,
                    "date_to": date_to, "q": q},
        "base_url": base, "employees": _employee_options(db), "shifts": SHIFT_GROUPS,
        "attendance_statuses": ATTENDANCE_STATUSES, "type_labels": ATTENDANCE_TYPE_LABELS,
        "type_options": ATTENDANCE_TYPE_OPTIONS, "approval_options": APPROVAL_OPTIONS,
        "today_iso": date.today().isoformat(), "employee_line": svc.employee_line,
        "can_add": rbac.has_permission(user, "hr_attendance.add"),
        "can_change": rbac.has_permission(user, "hr_attendance.update") or rbac.has_permission(user, "hr_attendance.approve")})


@router.post("/attendance/change-requests/new", include_in_schema=False)
async def change_request_create(request: Request, db: Session = Depends(get_db),
                                user: User = Depends(require("hr_attendance.add"))):
    form = await request.form()
    e = db.get(Employee, parse_int(form.get("employee_id"), 0) or 0)
    d = parse_date(form.get("attendance_date"))
    if not e or not d:
        return redirect("/hr/attendance/change-requests", "Choose an employee and the attendance date.", "error")
    svc.create_change_request(db, e, d, form.get("session") or "am", form.get("new_status") or "present",
                              _hhmm(form.get("new_check_in")), _hhmm(form.get("new_check_out")),
                              form.get("user_remarks"), user, request=request)
    db.commit()
    return redirect("/hr/attendance/change-requests?status=pending",
                    f"Attendance change request raised for {e.full_name} ({d}).")


@router.post("/attendance/change-requests/change-status", include_in_schema=False)
async def change_request_status(request: Request, db: Session = Depends(get_db),
                                user: User = Depends(require("hr_attendance.update", "hr_attendance.approve", any_of=True))):
    """Actions -> Change Status. Approving rewrites every attendance row the selected requests name."""
    ACR = _change_request_model()
    url = "/hr/attendance/change-requests"
    form = await request.form()
    status = (form.get("status") or "").strip()
    remarks = (form.get("hr_remarks") or form.get("remarks") or "").strip()
    ids = [int(v) for v in form.getlist("ids") if str(v).isdigit()]
    if status not in CHANGE_STATUSES:
        return redirect(url, "Choose the new approval status.", "error")
    if not ids:
        return redirect(url, "Select at least one row first.", "error")
    if status == "approved" and not rbac.has_permission(user, "hr_attendance.approve"):
        return redirect(url, "You do not have permission to approve attendance changes.", "error")
    if not remarks:
        return redirect(url, "HR remarks are required for a status change.", "error")
    n = 0
    for req in db.query(ACR).filter(ACR.id.in_(ids)).all():
        svc.decide_change_request(db, req, status, remarks, user, request=request)
        n += 1
    db.commit()
    return redirect(f"{url}?status={status}", f"{n} attendance change request(s) moved to {status}.")


@router.post("/me/attendance-change", include_in_schema=False)
async def me_attendance_change(request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    """Self-service: a member of staff asks HR to correct a punch. Old values are captured from the row itself."""
    if ctx.employee is None:
        return redirect("/hr/me", "No employee record is linked to your account.", "error")
    form = await request.form()
    row = db.get(HRAttendance, parse_int(form.get("attendance_id"), 0) or 0) if form.get("attendance_id") else None
    if row is not None and row.employee_id != ctx.employee.id:
        return redirect("/hr/me?tab=attendance", "Attendance record not found.", "error")
    d = row.date if row is not None else parse_date(form.get("attendance_date"))
    session = row.session if row is not None else (form.get("session") or "am")
    if not d:
        return redirect("/hr/me?tab=attendance", "Choose the attendance date to correct.", "error")
    remarks = (form.get("user_remarks") or form.get("reason") or "").strip()
    if not remarks:
        return redirect("/hr/me?tab=attendance", "Please explain what needs correcting.", "error")
    svc.create_change_request(db, ctx.employee, d, session, form.get("new_status") or "present",
                              _hhmm(form.get("new_check_in")), _hhmm(form.get("new_check_out")),
                              remarks, ctx.user, request=request)
    db.commit()
    return redirect("/hr/me?tab=attendance", "Attendance change request submitted to People & Culture.")


# ------------------------------------------------------------------ Leave Assignment (entitlements)
def _entitlement_model():
    from app.models.hr_erp import LeaveEntitlement
    return LeaveEntitlement


@router.get("/leave-entitlements", include_in_schema=False)
def leave_entitlements(request: Request, page: int = 1, employee: str = "", leave_type: str = "", status: str = "",
                       shift: str = "", q: str = "", db: Session = Depends(get_db),
                       user: User = Depends(require("employees.view"))):
    LE = _entitlement_model()
    query = db.query(LE)
    if parse_int(employee):
        query = query.filter(LE.employee_id == int(employee))
    if leave_type:
        query = query.filter(LE.leave_type == leave_type)
    if status:
        query = query.filter(LE.status == status)
    if shift:
        query = query.filter(LE.employee_id.in_([e.id for e in _staff_query(db, shift=shift, only_active=False)] or [-1]))
    if q:
        query = query.filter(LE.employee_id.in_(
            [e.id for e in db.query(Employee).filter(or_(Employee.full_name.ilike(f"%{q}%"),
                                                         Employee.employee_code.ilike(f"%{q}%")))] or [-1]))
    pg = paginate(query.order_by(LE.id.desc()), page, 25)
    totals = {"assigned": 0.0, "consumed": 0.0}
    for t, c in db.query(func.sum(LE.total_assigned), func.sum(LE.consumed)).filter(LE.status == "active").all():
        totals = {"assigned": round(float(t or 0), 1), "consumed": round(float(c or 0), 1)}
    base = f"/hr/leave-entitlements?employee={employee}&leave_type={leave_type}&status={status}&shift={shift}&q={q}"
    return render(request, "hr/leave_entitlements.html", {
        "user": user, "page": pg, "filters": {"employee": employee, "leave_type": leave_type, "status": status,
                                              "shift": shift, "q": q},
        "base_url": base, "employees": _employee_options(db), "leave_types": LEAVE_TYPES, "shifts": SHIFT_GROUPS,
        "statuses": ["active", "inactive", "expired"], "totals": totals,
        "active_count": db.query(func.count(LE.id)).filter(LE.status == "active").scalar() or 0,
        "year_end": date(date.today().year, 12, 31).isoformat(),
        "can_add": rbac.has_permission(user, "leaves.add"),
        "can_update": rbac.has_permission(user, "leaves.update")})


@router.post("/leave-entitlements/new", include_in_schema=False)
async def leave_entitlement_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.add"))):
    LE = _entitlement_model()
    form = await request.form()
    e = db.get(Employee, parse_int(form.get("employee_id"), 0) or 0)
    if not e:
        return redirect("/hr/leave-entitlements", "Choose the employee to assign leave to.", "error")
    ent = LE(employee_id=e.id, leave_type=form.get("leave_type") or "casual",
             total_assigned=parse_float(form.get("total_assigned"), 0.0),
             consumed=parse_float(form.get("consumed"), 0.0), expiry_date=parse_date(form.get("expiry_date")),
             status=form.get("status") or "active", notes=(form.get("notes") or "").strip() or None)
    db.add(ent)
    db.flush()
    log_action(db, user, "create", "leaves", entity=ent, request=request, rationale=ent.notes,
               description=f"{ent.total_assigned} {ent.leave_type} day(s) assigned to {e.employee_code} "
                           f"(expiry {ent.expiry_date or 'none'})")
    if e.user_id:
        notify(db, e.user_id, "Leave entitlement assigned",
               f"You have been assigned {ent.total_assigned} day(s) of {ent.leave_type} leave"
               + (f", expiring {ent.expiry_date}." if ent.expiry_date else "."),
               event_type="leave", link="/hr/me?tab=leaves")
    db.commit()
    return redirect("/hr/leave-entitlements", f"Leave assignment created for {e.full_name}.")


@router.post("/leave-entitlements/{id}/edit", include_in_schema=False)
async def leave_entitlement_edit(id: int, request: Request, db: Session = Depends(get_db),
                                 user: User = Depends(require("leaves.update"))):
    LE = _entitlement_model()
    ent = db.get(LE, id)
    if not ent:
        raise HTTPException(404, "Leave assignment not found")
    form = await request.form()
    before = snapshot(ent, ["leave_type", "total_assigned", "consumed", "expiry_date", "status"])
    ent.leave_type = form.get("leave_type") or ent.leave_type
    ent.total_assigned = parse_float(form.get("total_assigned"), float(ent.total_assigned or 0))
    if form.get("consumed") not in (None, ""):
        ent.consumed = parse_float(form.get("consumed"), float(ent.consumed or 0))
    ent.expiry_date = parse_date(form.get("expiry_date")) or ent.expiry_date
    ent.status = form.get("status") or ent.status
    notes = (form.get("notes") or "").strip()
    if notes:
        ent.notes = notes
    log_action(db, user, "update", "leaves", entity=ent, request=request, rationale=notes or None,
               description=f"Leave assignment #{ent.id} updated for "
                           f"{ent.employee.employee_code if ent.employee else ent.employee_id}",
               before=before, after=snapshot(ent, ["leave_type", "total_assigned", "consumed", "expiry_date", "status"]))
    db.commit()
    return redirect("/hr/leave-entitlements", "Leave assignment updated.")


@router.post("/leave-entitlements/bulk-assign", include_in_schema=False)
async def leave_entitlement_bulk(request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.add"))):
    """Assign one leave type to every active employee, skipping anyone who already holds it for the period."""
    form = await request.form()
    leave_type = form.get("leave_type") or "casual"
    total = parse_float(form.get("total_assigned"), 0.0)
    expiry = parse_date(form.get("expiry_date"))
    if total <= 0:
        return redirect("/hr/leave-entitlements", "Give the entitlement a positive number of days.", "error")
    created, skipped = svc.assign_entitlements(db, leave_type, total, expiry, user,
                                               notes=(form.get("notes") or "").strip() or None, request=request)
    db.commit()
    return redirect("/hr/leave-entitlements",
                    f"{created} {leave_type} entitlement(s) created; {skipped} employee(s) already held one.",
                    "success" if created else "info")


# ------------------------------------------------------------------ Employees Progress Sheet
def _progress_model():
    from app.models.hr_erp import ProgressNote
    return ProgressNote


@router.get("/progress-sheet", include_in_schema=False)
def progress_sheet(request: Request, page: int = 1, employee: str = "", date_from: str = "", date_to: str = "",
                   rating: str = "", db: Session = Depends(get_db), user: User = Depends(require("employees.view"))):
    PN = _progress_model()
    query = db.query(PN)
    if parse_int(employee):
        query = query.filter(PN.employee_id == int(employee))
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(PN.working_date >= df)
    if dt:
        query = query.filter(PN.working_date <= dt)
    if rating == "unrated":
        query = query.filter(PN.manager_rating.is_(None))
    elif parse_int(rating):
        query = query.filter(PN.manager_rating == int(rating))
    pg = paginate(query.order_by(PN.working_date.desc(), PN.id.desc()), page, 25)
    rated = db.query(func.count(PN.id)).filter(PN.manager_rating.isnot(None)).scalar() or 0
    total = db.query(func.count(PN.id)).scalar() or 0
    avg = db.query(func.avg(PN.manager_rating)).filter(PN.manager_rating.isnot(None)).scalar()
    base = f"/hr/progress-sheet?employee={employee}&date_from={date_from}&date_to={date_to}&rating={rating}"
    return render(request, "hr/progress_sheet.html", {
        "user": user, "page": pg, "filters": {"employee": employee, "date_from": date_from, "date_to": date_to,
                                              "rating": rating},
        "base_url": base, "employees": _employee_options(db), "ratings": RATINGS,
        "rating_options": [("unrated", "Not rated yet")] + [(str(v), l) for v, l in RATINGS],
        "stats": {"total": total, "rated": rated, "unrated": total - rated,
                  "avg": round(float(avg), 2) if avg is not None else 0},
        "today_iso": date.today().isoformat(), "employee_line": svc.employee_line,
        "can_add": rbac.has_permission(user, "employees.add") or rbac.has_permission(user, "employees.update"),
        "can_rate": rbac.has_permission(user, "employees.update")})


@router.post("/progress-sheet/new", include_in_schema=False)
async def progress_create(request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("employees.add", "employees.update", any_of=True))):
    form = await request.form()
    e = db.get(Employee, parse_int(form.get("employee_id"), 0) or 0)
    detail = (form.get("detail") or "").strip()
    d = parse_date(form.get("working_date")) or date.today()
    if not e or not detail:
        return redirect("/hr/progress-sheet", "Choose an employee and describe the day's progress.", "error")
    svc.add_progress_note(db, e, d, detail, user, request=request)
    db.commit()
    return redirect("/hr/progress-sheet", f"Progress recorded for {e.full_name} on {d}.")


@router.post("/progress-sheet/{id}/rate", include_in_schema=False)
async def progress_rate(id: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("employees.update"))):
    PN = _progress_model()
    note = db.get(PN, id)
    if not note:
        raise HTTPException(404, "Progress note not found")
    form = await request.form()
    rating = parse_int(form.get("manager_rating"), 0) or 0
    if rating < 1 or rating > 5:
        return redirect("/hr/progress-sheet", "Give the entry a rating between 1 and 5.", "error")
    svc.rate_progress_note(db, note, rating, form.get("manager_comment"), user, request=request)
    db.commit()
    return redirect("/hr/progress-sheet", f"Progress entry #{note.id} rated {rating}/5.")


@router.post("/me/progress", include_in_schema=False)
async def me_progress(request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    """Self-service: a member of staff records what they did today for their manager to rate."""
    if ctx.employee is None:
        return redirect("/hr/me", "No employee record is linked to your account.", "error")
    form = await request.form()
    detail = (form.get("detail") or "").strip()
    if not detail:
        return redirect("/hr/me", "Describe what you worked on.", "error")
    d = parse_date(form.get("working_date")) or date.today()
    svc.add_progress_note(db, ctx.employee, d, detail, ctx.user, request=request)
    db.commit()
    return redirect("/hr/me", f"Progress for {d} recorded.")


# ------------------------------------------------------------------ Attendance Report
@router.get("/attendance/report", include_in_schema=False)
def attendance_report(request: Request, date_from: str = "", date_to: str = "", employee: str = "", shift: str = "",
                      employee_type: str = "", designation: str = "", department: str = "", format: str = "",
                      print_view: int = 0, db: Session = Depends(get_db),
                      user: User = Depends(require("employees.view"))):
    """Attendance Summery Report: filters, a per-employee summary, a totals row, Print and CSV."""
    today = date.today()
    start = parse_date(date_from) or (today - timedelta(days=29))
    end = parse_date(date_to) or today
    if end < start:
        start, end = end, start
    staff = _staff_query(db, shift=shift, employee_type=employee_type, designation=designation,
                         department=department, employee_id=parse_int(employee, 0) or 0, only_active=False).all()
    rows = svc.attendance_report(db, start, end, [e.id for e in staff])
    totals = {"present": 0, "absent": 0, "leave": 0, "late": 0, "late_minutes": 0, "worked_hours": 0.0,
              "shortage_hours": 0.0, "sessions": 0}
    for r in rows:
        for k in totals:
            totals[k] = round(totals[k] + r[k], 2)
    if format == "csv":
        out = [[r["employee"].employee_code, r["employee"].full_name, r["employee"].designation,
                r["employee"].employee_type, (r["employee"].shift or "").title(),
                r["employee"].department.name if r["employee"].department else "", r["working_days"], r["present"],
                r["absent"], r["leave"], r["late"], r["late_minutes"], r["worked_hours"], r["shortage_hours"]]
               for r in rows]
        out.append(["", "TOTAL", "", "", "", "", "", totals["present"], totals["absent"], totals["leave"],
                    totals["late"], totals["late_minutes"], totals["worked_hours"], totals["shortage_hours"]])
        return _csv(out, ["Code", "Employee", "Designation", "Employee Type", "Shift", "Department", "Working Days",
                          "Present", "Absent", "Leave", "Late Count", "Late Minutes", "Worked Hours", "Shortage Hours"],
                    f"attendance-report-{start}-{end}.csv")
    qs = (f"date_from={start}&date_to={end}&employee={employee}&shift={shift}&employee_type={employee_type}"
          f"&designation={designation}&department={department}")
    return render(request, "hr/attendance_report.html", {
        "user": user, "rows": rows, "totals": totals, "start": start, "end": end,
        "filters": {"date_from": start.isoformat(), "date_to": end.isoformat(), "employee": employee, "shift": shift,
                    "employee_type": employee_type, "designation": designation, "department": department},
        "employees": _employee_options(db, only_active=False), "shifts": SHIFT_GROUPS,
        "employee_types": _employee_types(), "designations": _designations(db),
        "departments": [(d.id, d.name) for d in _departments(db)], "qs": qs, "print_view": bool(print_view),
        "working_days": svc.working_days(db, start, end)})


# ============================================================================== LEAVES (Leave Management)
@router.get("/leaves", include_in_schema=False)
def leaves_list(request: Request, page: int = 1, status: str = "", leave_type: str = "", q: str = "",
                db: Session = Depends(get_db), user: User = Depends(require("leaves.view"))):
    query = db.query(Leave).filter(Leave.person_type == "employee")
    if status:
        query = query.filter(Leave.status == status)
    if leave_type:
        query = query.filter(Leave.leave_type == leave_type)
    if q:
        query = query.join(Employee, Leave.employee_id == Employee.id).filter(
            or_(Employee.full_name.ilike(f"%{q}%"), Employee.employee_code.ilike(f"%{q}%")))
    pg = paginate(query.order_by(Leave.start_date.desc()), page, 25)
    counts = dict(db.query(Leave.status, func.count(Leave.id)).filter(Leave.person_type == "employee").group_by(Leave.status).all())
    today = date.today()
    upcoming = db.query(Leave).filter(Leave.person_type == "employee", Leave.status.in_(["approved", "pending"]),
                                      Leave.end_date >= today, Leave.start_date <= today + timedelta(days=30))\
        .order_by(Leave.start_date).limit(40).all()
    on_leave_today = db.query(Leave).filter(Leave.person_type == "employee", Leave.status == "approved",
                                            Leave.start_date <= today, Leave.end_date >= today).all()
    # Remaining entitlement for every row on this page, so the list shows the balance the ERP shows.
    rows, cache = [], {}
    for l in pg.items:
        key = (l.employee_id, l.leave_type)
        if key not in cache:
            cache[key] = svc.find_entitlement(db, l.employee, l.leave_type, l.start_date) if l.employee else None
        ent = cache[key]
        rows.append({"leave": l, "days": svc.leave_working_days(db, l), "entitlement": ent,
                     "remaining": ent.remaining if ent else None})
    base = f"/hr/leaves?status={status}&leave_type={leave_type}&q={q}"
    return render(request, "hr/leaves.html", {"user": user, "page": pg, "status": status, "leave_type": leave_type, "q": q,
                                              "counts": counts, "upcoming": upcoming, "on_leave_today": on_leave_today,
                                              "leave_types": LEAVE_TYPES, "statuses": ["pending", "approved", "rejected", "cancelled"],
                                              "base_url": base, "leave_days": svc.leave_days, "today_date": today,
                                              "rows": rows})


@router.get("/leaves/new", include_in_schema=False)
def leave_new(request: Request, employee_id: int = 0, db: Session = Depends(get_db), user: User = Depends(require("leaves.add"))):
    LE = _entitlement_model()
    balances: dict = {}
    for ent in db.query(LE).filter(LE.status == "active").all():
        balances.setdefault(str(ent.employee_id), []).append(
            {"type": ent.leave_type, "total": float(ent.total_assigned or 0), "remaining": ent.remaining,
             "expiry": ent.expiry_date.isoformat() if ent.expiry_date else ""})
    return render(request, "hr/leave_form.html", {"user": user, "employees": _employee_options(db), "leave_types": LEAVE_TYPES,
                                                  "employee_id": employee_id, "balances": balances})


@router.post("/leaves/new", include_in_schema=False)
async def leave_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.add"))):
    form = await request.form()
    e = _emp(db, parse_int(form.get("employee_id"), 0) or 0)
    start = parse_date(form.get("start_date"))
    end = parse_date(form.get("end_date")) or start
    if not start:
        return redirect("/hr/leaves/new", "A start date is required.", "error")
    sub = db.get(Teacher, parse_int(form.get("substitute_teacher_id"), 0) or 0) if form.get("substitute_teacher_id") else None
    try:
        l = svc.request_leave(db, e, form.get("leave_type") or "casual", start, end, form.get("reason"), user,
                              substitute_teacher=sub, request=request)
    except ValueError as exc:
        return redirect("/hr/leaves/new", str(exc), "error")
    db.commit()
    return redirect(f"/hr/leaves/{l.id}", "Leave request created.")


@router.get("/leaves/{id}", include_in_schema=False)
def leave_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.view"))):
    l = db.get(Leave, id)
    if not l or l.person_type != "employee":
        raise HTTPException(404, "Leave not found")
    e = l.employee
    subs = []
    if e and e.teacher:
        subs = svc.substitute_suggestions(db, e.teacher, l.start_date, l.end_date)
    check = svc.entitlement_check(db, l) if e else {"days": 0, "entitlement": None, "remaining": None, "fits": True, "over_by": 0}
    return render(request, "hr/leave_detail.html", {"user": user, "l": l, "e": e, "subs": subs,
                                                    "balance": svc.leave_balance(db, e) if e else {},
                                                    "days": svc.leave_days(l), "check": check,
                                                    "working": check["days"],
                                                    "holidays": svc.holidays_between(db, l.start_date, l.end_date,
                                                                                     e.shift if e else None),
                                                    "history": db.query(Leave).filter(Leave.person_type == "employee",
                                                                                      Leave.employee_id == l.employee_id,
                                                                                      Leave.id != l.id)
                                                    .order_by(Leave.start_date.desc()).limit(10).all()})


@router.post("/leaves/{id}/decide", include_in_schema=False)
async def leave_decide(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.approve"))):
    """Approve / reject / cancel. Approving draws the working days down from the matching entitlement;
    reversing or cancelling an approved leave gives them back."""
    l = db.get(Leave, id)
    if not l or l.person_type != "employee":
        raise HTTPException(404, "Leave not found")
    form = await request.form()
    decision = form.get("decision") or "approve"
    note = (form.get("note") or "").strip() or None
    sub_id = parse_int(form.get("substitute_teacher_id"), 0)
    if sub_id:
        l.substitute_teacher_id = sub_id
    if decision == "cancel":
        svc.cancel_leave(db, l, user, note=note, request=request)
        db.commit()
        return redirect(f"/hr/leaves/{l.id}", "Leave cancelled; any consumed entitlement days were returned.")
    approve = decision == "approve"
    if approve:
        check = svc.entitlement_check(db, l)
        override = parse_bool(form.get("override"))
        rationale = (form.get("rationale") or form.get("reason") or note or "").strip()
        if not check["fits"] and not override:
            return redirect(f"/hr/leaves/{l.id}",
                            f"{check['days']} working day(s) exceeds the remaining {check['remaining']} "
                            f"{l.leave_type} day(s). Tick Override with a reason to approve anyway.", "error")
        if not check["fits"] and not rationale:
            return redirect(f"/hr/leaves/{l.id}", "An override needs a reason for the audit trail.", "error")
        svc.decide_leave(db, l, True, user, note=note, request=request)
        info = svc.consume_entitlement(db, l, user, override=not check["fits"], rationale=rationale or None, request=request)
        db.commit()
        msg = f"Leave approved ({info['days']} working day(s) applied"
        msg += f", {info['entitlement'].remaining} {l.leave_type} day(s) left)." if info["entitlement"] else ")."
        return redirect(f"/hr/leaves/{l.id}", msg, "warning" if not check["fits"] else "success")
    svc.reverse_leave_approval(db, l, user, request=request)
    svc.decide_leave(db, l, False, user, note=note, request=request)
    db.commit()
    return redirect(f"/hr/leaves/{l.id}", "Leave rejected.")


# ============================================================================== RECRUITMENT AND HIRING
# Job Requisitions, Job Applications (the ERP's ten-step pipeline), Interview Panels, Schedule Interviews,
# Candidate Database, Onboarding and the Summary report. See docs/AUDIT_HUMAN_RESOURCE.md section 3.
from app.models.hr_erp import APPLICATION_STATUSES, InterviewPanel, JobApplication  # noqa: E402

JOB_TYPES = [("full_time", "Full Time"), ("part_time", "Part Time"), ("contract", "Contract"), ("visiting", "Visiting")]
JOB_CATEGORIES = ["Quran", "Tajweed", "Hifz", "Arabic", "Islamic Studies", "Academics", "Sales", "Administration",
                  "Human Resource", "Quality Assurance", "Training"]
JOB_LOCATIONS = ["Head Office - Lahore", "Head Office - Karachi", "Campus - Rawalpindi", "Remote"]
REQUISITION_STATUSES = ["open", "approved", "in_progress", "filled", "cancelled"]
REQUISITION_REPORTS = [("primary", "Primary"), ("summary", "Summary"), ("summary_dept", "Summary Department Wise")]
# The ERP's application vocabulary, in pipeline order. Internal values live in hr_erp.APPLICATION_STATUSES.
APPLICATION_LABELS = {
    "applied": "Applied", "on_hold": "On-Hold", "initial_selected": "Initial Selected",
    "pre_selected": "Pre-Selected", "marked_1st_interview": "Marked - 1st Interview",
    "marked_2nd_interview": "Marked - 2nd Interview", "marked_final_interview": "Marked - Final Interview",
    "selected": "Selected", "rejected": "Rejected", "hired": "Hired",
}
APPLICATION_NEXT = {
    "applied": "initial_selected", "on_hold": "initial_selected", "initial_selected": "pre_selected",
    "pre_selected": "marked_1st_interview", "marked_1st_interview": "marked_2nd_interview",
    "marked_2nd_interview": "marked_final_interview", "marked_final_interview": "selected", "selected": "hired",
}
INTERVIEW_STAGES = {"marked_1st_interview": "screening", "marked_2nd_interview": "technical",
                    "marked_final_interview": "final"}
APPLICATION_TYPES = [("profile", "Profile"), ("non_profile", "Non-Profile")]
INTERVIEW_TYPES = [("screening", "Screening"), ("technical", "Technical"), ("demo_class", "Demo Class"),
                   ("final", "Final")]
INTERVIEW_OUTCOMES = [("scheduled", "Scheduled"), ("completed", "Completed"), ("no_show", "No Show"),
                      ("cancelled", "Cancelled")]
GENDERS = [("male", "Male"), ("female", "Female")]
# An interview row carries its venue on the first line of `feedback` so a panel member's own notes can follow it.
IV_LOC = "LOC:"


def _requisition(db: Session, id: int) -> RecruitmentRequest:
    r = db.get(RecruitmentRequest, id)
    if not r:
        raise HTTPException(404, "Job requisition not found")
    return r


def _application(db: Session, id: int) -> JobApplication:
    a = db.get(JobApplication, id)
    if not a:
        raise HTTPException(404, "Job application not found")
    return a


def _dept_options(db: Session) -> list[tuple[int, str]]:
    return [(d.id, d.name) for d in _departments(db)]


def _job_options(db: Session) -> list[tuple[int, str]]:
    return [(r.id, r.title) for r in db.query(RecruitmentRequest).order_by(RecruitmentRequest.title).all()]


def _panel_options(db: Session, only_active: bool = True) -> list[tuple[int, str]]:
    q = db.query(InterviewPanel)
    if only_active:
        q = q.filter(InterviewPanel.status == "active")
    return [(p.id, p.name) for p in q.order_by(InterviewPanel.name).all()]


def _staff_users(db: Session) -> list[User]:
    return db.query(User).join(Role, User.role_id == Role.id)\
        .filter(Role.portal.in_(["admin", "teacher"]), User.is_active.is_(True)).order_by(User.full_name).all()


def _csv_list(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").replace("\n", ",").split(",") if p.strip()]


def _status_rank(status: str | None) -> int:
    """Where a status sits in the ten-step pipeline (-1 when it is not one of them)."""
    try:
        return APPLICATION_STATUSES.index(status or "applied")
    except ValueError:
        return -1


def _iv_location(iv: Interview) -> str:
    text = iv.feedback or ""
    if text.startswith(IV_LOC):
        return text.split("\n", 1)[0][len(IV_LOC):].strip()
    return ""


def _iv_notes(iv: Interview) -> str:
    text = iv.feedback or ""
    if text.startswith(IV_LOC):
        parts = text.split("\n", 1)
        return parts[1].strip() if len(parts) > 1 else ""
    return text


def _iv_write(iv: Interview, location: str, notes: str) -> None:
    location = (location or "").strip()
    notes = (notes or "").strip()
    iv.feedback = (f"{IV_LOC} {location}\n{notes}".strip() if location else notes) or None


def _apply_requisition(r: RecruitmentRequest, form) -> None:
    r.title = (form.get("title") or r.title or "").strip()
    r.department_id = parse_int(form.get("department_id")) or None
    r.positions = parse_int(form.get("positions"), r.positions or 1) or 1
    r.description = form.get("description") or r.description
    r.job_type = (form.get("job_type") or r.job_type or "full_time").strip()
    categories = form.getlist("job_categories") or _csv_list(form.get("job_categories_text"))
    if categories:
        r.job_categories = [c for c in categories if c]
    skills = _csv_list(form.get("skills"))
    if skills or form.get("skills") is not None:
        r.skills = skills
    r.job_location = (form.get("job_location") or r.job_location or "").strip() or None
    r.start_date = parse_date(form.get("start_date"), r.start_date)
    r.end_date = parse_date(form.get("end_date"), r.end_date)
    status = (form.get("status") or "").strip()
    if status in REQUISITION_STATUSES:
        r.status = status


@router.get("/recruitment", include_in_schema=False)
def recruitment(request: Request, report: str = "primary", tab: str = "", q: str = "", status: str = "",
                department_id: int = 0, job_type: str = "", db: Session = Depends(get_db),
                user: User = Depends(require("recruitment.view"))):
    """Job Requisitions, with the ERP's three saved reports: Primary, Summary, Summary Department Wise."""
    if report not in [k for k, _ in REQUISITION_REPORTS]:
        report = "primary"
    query = db.query(RecruitmentRequest)
    if q:
        query = query.filter(RecruitmentRequest.title.ilike(f"%{q}%"))
    if status:
        query = query.filter(RecruitmentRequest.status == status)
    if department_id:
        query = query.filter(RecruitmentRequest.department_id == department_id)
    if job_type:
        query = query.filter(RecruitmentRequest.job_type == job_type)
    rows = query.order_by(RecruitmentRequest.id.desc()).all()

    apps = db.query(JobApplication).all()
    per_job: dict[int, dict] = {}
    for a in apps:
        cell = per_job.setdefault(a.request_id or 0, {"applications": 0, "hired": 0})
        cell["applications"] += 1
        if a.status == "hired":
            cell["hired"] += 1

    counts = dict(db.query(RecruitmentRequest.status, func.count(RecruitmentRequest.id))
                  .group_by(RecruitmentRequest.status).all())
    stats = {"total": sum(counts.values()), "open": counts.get("open", 0) + counts.get("approved", 0),
             "in_progress": counts.get("in_progress", 0), "filled": counts.get("filled", 0),
             "cancelled": counts.get("cancelled", 0),
             "positions": sum(r.positions or 0 for r in rows),
             "applications": sum(c["applications"] for c in per_job.values()),
             "hired": sum(c["hired"] for c in per_job.values())}

    by_job = [{"id": r.id, "title": r.title, "department": r.department.name if r.department else "Unassigned",
               "positions": r.positions or 0, "applications": per_job.get(r.id, {}).get("applications", 0),
               "hired": per_job.get(r.id, {}).get("hired", 0), "status": r.status} for r in rows]
    dept_rows: dict[str, dict] = {}
    for row in by_job:
        cell = dept_rows.setdefault(row["department"], {"department": row["department"], "jobs": 0, "positions": 0,
                                                        "applications": 0, "hired": 0})
        cell["jobs"] += 1
        cell["positions"] += row["positions"]
        cell["applications"] += row["applications"]
        cell["hired"] += row["hired"]

    return render(request, "hr/recruitment.html", {
        "user": user, "rows": rows, "report": report, "tab": tab, "q": q, "status": status,
        "department_id": department_id, "job_type": job_type, "stats": stats, "per_job": per_job,
        "by_job": sorted(by_job, key=lambda r: -r["applications"]),
        "by_department": sorted(dept_rows.values(), key=lambda r: -r["applications"]),
        "reports": REQUISITION_REPORTS, "departments": _dept_options(db), "job_types": JOB_TYPES,
        "job_categories": JOB_CATEGORIES, "job_locations": JOB_LOCATIONS, "statuses": REQUISITION_STATUSES,
        "today": date.today()})


@router.post("/recruitment/new", include_in_schema=False)
async def recruitment_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("recruitment.add"))):
    form = await request.form()
    title = (form.get("title") or "").strip()
    if not title:
        return redirect("/hr/recruitment", "A job title is required.", "error")
    r = RecruitmentRequest(title=title, requested_by_id=user.id, positions=1, status="open",
                           target_date=parse_date(form.get("target_date")), job_categories=[], skills=[])
    _apply_requisition(r, form)
    r.target_date = r.target_date or r.end_date
    db.add(r)
    db.flush()
    log_action(db, user, "create", "recruitment", entity=r,
               description=f"Job requisition '{title}' ({r.positions} position(s), {r.job_type})",
               after=snapshot(r), request=request)
    for u in svc.hr_notify_users(db):
        notify(db, u, "Job requisition raised", f"{title} — {r.positions} position(s).", event_type="recruitment",
               link=f"/hr/recruitment/{r.id}")
    db.commit()
    return redirect(f"/hr/recruitment/{r.id}", "Job requisition created.")


@router.get("/recruitment/summary", include_in_schema=False)
def recruitment_summary_page(request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("recruitment.view"))):
    """Per vacancy: positions, applications, by-status counts, interviews held, hires and time to hire."""
    from app.services import hr_dashboards as hrd
    rows = hrd.recruitment_summary(db)
    funnel = hrd.application_funnel(db)
    totals = {"positions": sum(r["positions"] for r in rows), "applications": sum(r["applications"] for r in rows),
              "interviews": sum(r["interviews"] for r in rows), "hires": sum(r["hires"] for r in rows)}
    gaps = [r["time_to_hire"] for r in rows if r["time_to_hire"] is not None]
    totals["time_to_hire"] = round(sum(gaps) / len(gaps), 1) if gaps else None
    return render(request, "hr/recruitment_summary.html", {
        "user": user, "rows": rows, "totals": totals, "funnel": funnel, "statuses": APPLICATION_STATUSES,
        "labels": APPLICATION_LABELS})


# ------------------------------------------------------------------ job applications
@router.get("/applications", include_in_schema=False)
def applications_list(request: Request, page: int = 1, q: str = "", status: str = "", request_id: int = 0,
                      application_type: str = "", department_id: int = 0, from_date: str = "", to_date: str = "",
                      db: Session = Depends(get_db), user: User = Depends(require("recruitment.view"))):
    query = db.query(JobApplication)
    if q:
        query = query.filter(or_(JobApplication.full_name.ilike(f"%{q}%"), JobApplication.cell_no.ilike(f"%{q}%"),
                                 JobApplication.email.ilike(f"%{q}%"), JobApplication.nic_number.ilike(f"%{q}%")))
    if status:
        query = query.filter(JobApplication.status == status)
    if request_id:
        query = query.filter(JobApplication.request_id == request_id)
    if application_type:
        query = query.filter(JobApplication.application_type == application_type)
    if department_id:
        query = query.filter(JobApplication.department_id == department_id)
    start, end = parse_date(from_date), parse_date(to_date)
    if start:
        query = query.filter(JobApplication.application_date >= start)
    if end:
        query = query.filter(JobApplication.application_date <= end)
    pg = paginate(query.order_by(JobApplication.application_date.desc(), JobApplication.id.desc()), page, 30)
    counts = dict(db.query(JobApplication.status, func.count(JobApplication.id)).group_by(JobApplication.status).all())
    base = (f"/hr/applications?q={q}&status={status}&request_id={request_id or ''}&application_type={application_type}"
            f"&department_id={department_id or ''}&from_date={from_date}&to_date={to_date}")
    return render(request, "hr/recruitment_applications.html", {
        "user": user, "page": pg, "q": q, "status": status, "request_id": request_id, "base_url": base,
        "application_type": application_type, "department_id": department_id, "from_date": from_date, "to_date": to_date,
        "counts": counts, "total": sum(counts.values()), "statuses": APPLICATION_STATUSES, "labels": APPLICATION_LABELS,
        "status_options": [(s, APPLICATION_LABELS[s]) for s in APPLICATION_STATUSES],
        "next_status": APPLICATION_NEXT, "jobs": _job_options(db), "departments": _dept_options(db),
        "panels": _panel_options(db), "application_types": APPLICATION_TYPES, "genders": GENDERS,
        "today": date.today(), "can_process": rbac.has_permission(user, "recruitment.update")})


def _apply_application(a: JobApplication, form) -> None:
    a.full_name = (form.get("full_name") or a.full_name or "").strip()
    a.father_name = form.get("father_name") or a.father_name
    a.gender = (form.get("gender") or a.gender or "male").strip().lower()
    a.department_id = parse_int(form.get("department_id")) or a.department_id
    a.request_id = parse_int(form.get("request_id")) or a.request_id
    a.application_type = (form.get("application_type") or a.application_type or "profile").strip()
    a.application_date = parse_date(form.get("application_date"), a.application_date or date.today())
    a.nic_number = form.get("nic_number") or a.nic_number
    a.cell_no = form.get("cell_no") or a.cell_no
    a.email = (form.get("email") or a.email or "").strip().lower() or None
    a.qualification = form.get("qualification") or a.qualification
    a.experience_years = parse_float(form.get("experience_years"), float(a.experience_years or 0))
    a.expected_salary = parse_float(form.get("expected_salary"), float(a.expected_salary or 0))
    a.city = form.get("city") or a.city
    a.source = form.get("source") or a.source or "Website"
    a.remarks = form.get("remarks") or a.remarks


@router.post("/applications/new", include_in_schema=False)
async def application_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("recruitment.add"))):
    """Add Manually — the ERP's way of entering an application taken off-portal."""
    form = await request.form()
    if not (form.get("full_name") or "").strip():
        return redirect("/hr/applications", "The candidate's name is required.", "error")
    a = JobApplication(full_name="", application_date=date.today(), status="applied", application_type="profile")
    _apply_application(a, form)
    if not a.request_id:
        return redirect("/hr/applications", "Choose the job this application is against.", "error")
    job = db.get(RecruitmentRequest, a.request_id)
    if job is None:
        return redirect("/hr/applications", "That job requisition no longer exists.", "error")
    a.department_id = a.department_id or job.department_id
    db.add(a)
    db.flush()
    log_action(db, user, "create", "recruitment", entity=a,
               description=f"Job application from {a.full_name} for {job.title}",
               after=snapshot(a), request=request)
    db.commit()
    return redirect(f"/hr/applications/{a.id}", f"Application recorded for {a.full_name}.")


def _hire_candidate(db: Session, c: Candidate, form, user: User, request: Request,
                    defaults: dict | None = None) -> tuple[Employee, str | None]:
    """Turn a candidate into an employee (and a teacher profile where the role is a teaching one).

    ``defaults`` lets the job application supply the department and expected salary when the form does not.
    """
    d = defaults or {}
    is_teacher = "teacher" in (c.applied_for or "").lower()
    data = {"full_name": c.full_name, "designation": form.get("designation") or d.get("designation") or c.applied_for,
            "department_id": form.get("department_id") or d.get("department_id"),
            "gender": c.gender or "male", "email": c.email, "phone": c.phone,
            "join_date": parse_date(form.get("join_date")) or date.today(),
            "employment_type": form.get("employment_type") or d.get("employment_type") or "full_time",
            "shift": form.get("shift") or d.get("shift") or "evening",
            "base_salary": form.get("base_salary") or d.get("base_salary"), "is_teacher": is_teacher,
            "status": "probation"}
    emp, pwd = svc.create_employee(db, data, user, create_login=parse_bool(form.get("create_login")), request=request)
    if is_teacher:
        t = Teacher(teacher_code=next_code(db, Teacher, "teacher_code", "T-"), user_id=emp.user_id, employee_id=emp.id,
                    full_name=emp.full_name, gender=emp.gender, courses=[], shift=emp.shift, per_class_rate=250,
                    grade=emp.salary_band or "B", status="active", is_verified=False,
                    qualifications=(c.notes or "")[:400] or None)
        db.add(t)
        db.flush()
        emp.background_check_status = "pending"
        log_action(db, user, "create", "teachers", entity=t,
                   description=f"Teacher profile {t.teacher_code} created from candidate {c.full_name} (unverified)",
                   request=request)
    c.hired_employee_id = emp.id
    c.stage = "hired"
    if c.request:
        filled = db.query(func.count(Candidate.id)).filter(Candidate.request_id == c.request_id,
                                                           Candidate.stage == "hired").scalar() or 0
        c.request.status = "filled" if filled >= (c.request.positions or 1) else "in_progress"
    log_action(db, user, "hire", "recruitment", entity=c,
               description=f"{c.full_name} hired as {emp.designation} ({emp.employee_code})",
               after={"employee_code": emp.employee_code}, consequential=True, request=request)
    return emp, pwd


def _candidate_for_application(db: Session, a: JobApplication) -> Candidate:
    """Every application belongs to a person in the candidate database; create that person on first need."""
    if a.candidate_id and a.candidate:
        return a.candidate
    c = None
    if a.email:
        c = db.query(Candidate).filter(func.lower(Candidate.email) == a.email.lower()).first()
    if c is None:
        c = Candidate(request_id=a.request_id, full_name=a.full_name, email=a.email, phone=a.cell_no,
                      gender=a.gender, applied_for=a.request.title if a.request else "Staff",
                      source=a.source or "Website", stage="applied",
                      notes=" · ".join([p for p in [a.qualification, a.city,
                                                    (f"{a.experience_years:g} year(s) experience" if a.experience_years else None)] if p]) or None)
        db.add(c)
        db.flush()
    a.candidate_id = c.id
    return c


@router.post("/applications/process", include_in_schema=False)
async def applications_process(request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require("recruitment.update"))):
    """Move one or many applications to the next step of the pipeline, with a remark. Audited per application."""
    form = await request.form()
    ids = [parse_int(v, 0) or 0 for v in form.getlist("ids")]
    ids = [i for i in ids if i]
    back = form.get("next") or "/hr/applications"
    if not ids:
        return redirect(back, "Select at least one application to process.", "error")
    target = (form.get("status") or "").strip()
    if target and target not in APPLICATION_STATUSES:
        return redirect(back, "Unknown application status.", "error")
    remark = (form.get("remark") or "").strip()
    panel_id = parse_int(form.get("panel_id")) or None
    moved, skipped, hired = 0, [], []
    for aid in ids:
        a = db.get(JobApplication, aid)
        if a is None:
            continue
        new_status = target or APPLICATION_NEXT.get(a.status or "applied")
        if not new_status or new_status == a.status:
            skipped.append(f"{a.full_name} ({APPLICATION_LABELS.get(a.status, a.status)})")
            continue
        if new_status in INTERVIEW_STAGES and not (panel_id or a.panel_id):
            skipped.append(f"{a.full_name} — no interview panel chosen")
            continue
        if new_status == "hired" and a.hired_employee_id:
            skipped.append(f"{a.full_name} — already hired")
            continue
        if new_status == "hired" and not rbac.has_permission(user, "employees.add"):
            skipped.append(f"{a.full_name} — hiring needs the Employees add permission")
            continue
        before = {"status": a.status}
        if panel_id:
            a.panel_id = panel_id
        a.status = new_status
        if remark:
            a.remarks = ((a.remarks + "\n") if a.remarks else "") + \
                f"[{date.today()}] {APPLICATION_LABELS.get(new_status, new_status)}: {remark}"
        c = _candidate_for_application(db, a)
        default_stage = "interview" if new_status in INTERVIEW_STAGES else "screening"
        c.stage = {"hired": "hired", "rejected": "rejected", "selected": "offer"}.get(new_status, default_stage)
        if new_status == "hired":
            emp, _pwd = _hire_candidate(db, c, form, user, request,
                                        defaults={"department_id": a.department_id,
                                                  "base_salary": float(a.expected_salary or 0) or None,
                                                  "designation": a.request.title if a.request else None})
            a.hired_employee_id = emp.id
            hired.append(f"{a.full_name} -> {emp.employee_code}")
        log_action(db, user, "status_change", "recruitment", entity=a,
                   description=f"Application {a.full_name}: {APPLICATION_LABELS.get(before['status'], before['status'])} -> "
                               f"{APPLICATION_LABELS.get(new_status, new_status)}",
                   before=before, after={"status": new_status, "panel_id": a.panel_id}, rationale=remark or None,
                   consequential=new_status in ("hired", "rejected"), request=request)
        moved += 1
    db.commit()
    msg = f"{moved} application(s) processed." if moved else "Nothing to process."
    if hired:
        msg += " Hired: " + ", ".join(hired) + "."
    if skipped:
        msg += " Skipped: " + "; ".join(skipped[:4]) + ("…" if len(skipped) > 4 else "") + "."
    return redirect(back, msg, "success" if moved else "warning")


@router.get("/applications/{id}", include_in_schema=False)
def application_detail(id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("recruitment.view"))):
    a = _application(db, id)
    interviews = []
    if a.candidate_id:
        interviews = db.query(Interview).filter(Interview.candidate_id == a.candidate_id)\
            .order_by(Interview.scheduled_at.desc()).all()
    return render(request, "hr/recruitment_application_detail.html", {
        "user": user, "a": a, "interviews": interviews, "iv_location": _iv_location, "iv_notes": _iv_notes,
        "statuses": APPLICATION_STATUSES, "labels": APPLICATION_LABELS, "next_status": APPLICATION_NEXT,
        "status_options": [(s, APPLICATION_LABELS[s]) for s in APPLICATION_STATUSES],
        "panels": _panel_options(db), "departments": _dept_options(db),
        "employee": db.get(Employee, a.hired_employee_id) if a.hired_employee_id else None,
        "can_process": rbac.has_permission(user, "recruitment.update")})


# ------------------------------------------------------------------ interview panels
@router.get("/interview-panels", include_in_schema=False)
def interview_panels(request: Request, q: str = "", status: str = "", db: Session = Depends(get_db),
                     user: User = Depends(require("recruitment.view"))):
    query = db.query(InterviewPanel)
    if q:
        query = query.filter(InterviewPanel.name.ilike(f"%{q}%"))
    if status:
        query = query.filter(InterviewPanel.status == status)
    rows = query.order_by(InterviewPanel.name).all()
    users = {u.id: u for u in db.query(User)}
    counts = dict(db.query(InterviewPanel.status, func.count(InterviewPanel.id)).group_by(InterviewPanel.status).all())
    used = dict(db.query(JobApplication.panel_id, func.count(JobApplication.id))
                .filter(JobApplication.panel_id.isnot(None)).group_by(JobApplication.panel_id).all())
    return render(request, "hr/recruitment_panels.html", {
        "user": user, "rows": rows, "q": q, "status": status, "users": users, "used": used,
        "stats": {"total": sum(counts.values()), "active": counts.get("active", 0), "inactive": counts.get("inactive", 0)},
        "staff": [(u.id, f"{u.full_name} — {u.role.name if u.role else 'Staff'}") for u in _staff_users(db)],
        "statuses": [("active", "Active"), ("inactive", "In-active")],
        "can_edit": rbac.has_permission(user, "recruitment.update"),
        "can_add": rbac.has_permission(user, "recruitment.add")})


def _apply_panel(p: InterviewPanel, form) -> None:
    p.name = (form.get("name") or p.name or "").strip()
    p.description = form.get("description") or None
    members = [parse_int(v, 0) or 0 for v in form.getlist("member_ids")]
    p.member_ids = [m for m in members if m]
    status = (form.get("status") or p.status or "active").strip().lower()
    p.status = status if status in ("active", "inactive") else "active"


@router.post("/interview-panels/new", include_in_schema=False)
async def interview_panel_create(request: Request, db: Session = Depends(get_db),
                                 user: User = Depends(require("recruitment.add"))):
    form = await request.form()
    if not (form.get("name") or "").strip():
        return redirect("/hr/interview-panels", "A panel name is required.", "error")
    p = InterviewPanel(name="", member_ids=[], status="active")
    _apply_panel(p, form)
    db.add(p)
    db.flush()
    log_action(db, user, "create", "recruitment", entity=p,
               description=f"Interview panel '{p.name}' created with {len(p.member_ids)} member(s)",
               after=snapshot(p), request=request)
    db.commit()
    return redirect("/hr/interview-panels", f"Interview panel {p.name} created.")


@router.post("/interview-panels/{id}/edit", include_in_schema=False)
async def interview_panel_edit(id: int, request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require("recruitment.update"))):
    p = db.get(InterviewPanel, id)
    if not p:
        raise HTTPException(404, "Interview panel not found")
    form = await request.form()
    before = snapshot(p)
    _apply_panel(p, form)
    log_action(db, user, "update", "recruitment", entity=p, description=f"Interview panel '{p.name}' updated",
               before=before, after=snapshot(p), request=request)
    db.commit()
    return redirect("/hr/interview-panels", f"Interview panel {p.name} updated.")


@router.post("/interview-panels/{id}/toggle", include_in_schema=False)
def interview_panel_toggle(id: int, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require("recruitment.update"))):
    p = db.get(InterviewPanel, id)
    if not p:
        raise HTTPException(404, "Interview panel not found")
    before = snapshot(p)
    p.status = "inactive" if p.status == "active" else "active"
    log_action(db, user, "status_change", "recruitment", entity=p,
               description=f"Interview panel '{p.name}' marked {p.status}", before=before, after=snapshot(p),
               request=request)
    db.commit()
    return redirect("/hr/interview-panels", f"Interview panel {p.name} marked {p.status}.")


# ------------------------------------------------------------------ schedule interviews
@router.get("/interviews", include_in_schema=False)
def interviews_page(request: Request, q: str = "", status: str = "", panel_id: int = 0, from_date: str = "",
                    to_date: str = "", db: Session = Depends(get_db), user: User = Depends(require("recruitment.view"))):
    """Interviews grouped into one row per sitting, with the outcome and feedback of each panel member."""
    query = db.query(Interview)
    if status:
        query = query.filter(Interview.status == status)
    start, end = parse_date(from_date), parse_date(to_date)
    if start:
        query = query.filter(Interview.scheduled_at >= datetime.combine(start, datetime.min.time()))
    if end:
        query = query.filter(Interview.scheduled_at <= datetime.combine(end, datetime.max.time()))
    rows = query.order_by(Interview.scheduled_at.desc()).all()
    apps_by_candidate: dict[int, JobApplication] = {}
    for a in db.query(JobApplication).filter(JobApplication.candidate_id.isnot(None)).order_by(JobApplication.id):
        apps_by_candidate[a.candidate_id] = a
    panels = {p.id: p for p in db.query(InterviewPanel)}
    users = {u.id: u for u in db.query(User)}

    groups: dict[tuple, dict] = {}
    for iv in rows:
        c = iv.candidate
        app = apps_by_candidate.get(iv.candidate_id)
        panel = panels.get(app.panel_id) if app and app.panel_id else None
        if panel_id and (not panel or panel.id != panel_id):
            continue
        if q and q.lower() not in ((c.full_name if c else "") + " " + (panel.name if panel else "")).lower():
            continue
        key = (iv.candidate_id, iv.scheduled_at, iv.interview_type)
        g = groups.setdefault(key, {"candidate": c, "application": app, "panel": panel, "at": iv.scheduled_at,
                                    "type": iv.interview_type, "location": _iv_location(iv), "members": []})
        g["location"] = g["location"] or _iv_location(iv)
        g["members"].append({"iv": iv, "user": users.get(iv.interviewer_id), "notes": _iv_notes(iv)})
    ordered = sorted(groups.values(), key=lambda g: g["at"], reverse=True)
    counts = dict(db.query(Interview.status, func.count(Interview.id)).group_by(Interview.status).all())
    open_apps = db.query(JobApplication).filter(JobApplication.status.notin_(["hired", "rejected"]))\
        .order_by(JobApplication.full_name).all()
    return render(request, "hr/recruitment_interviews.html", {
        "user": user, "groups": ordered, "q": q, "status": status, "panel_id": panel_id, "from_date": from_date,
        "to_date": to_date, "counts": counts, "total": sum(counts.values()), "panels": _panel_options(db),
        "outcomes": INTERVIEW_OUTCOMES, "types": INTERVIEW_TYPES, "labels": APPLICATION_LABELS,
        "applications": [(a.id, f"{a.full_name} — {a.request.title if a.request else 'Vacancy'}") for a in open_apps],
        "today": date.today(), "can_edit": rbac.has_permission(user, "recruitment.update")})


@router.post("/interviews/new", include_in_schema=False)
async def interview_schedule(request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("recruitment.update"))):
    """Schedule an interview for an application against a panel: one row per panel member."""
    form = await request.form()
    a = db.get(JobApplication, parse_int(form.get("application_id"), 0) or 0)
    if a is None:
        return redirect("/hr/interviews", "Choose the application to interview.", "error")
    panel = db.get(InterviewPanel, parse_int(form.get("panel_id"), 0) or (a.panel_id or 0))
    if panel is None:
        return redirect("/hr/interviews", "An interview needs an interview panel.", "error")
    when = parse_date(form.get("interview_date")) or date.today()
    hh, _, mm = (form.get("interview_time") or "11:00").partition(":")
    try:
        at = datetime.combine(when, datetime.min.time().replace(hour=int(hh), minute=int(mm or 0)))
    except ValueError:
        at = datetime.combine(when, datetime.min.time().replace(hour=11))
    itype = (form.get("interview_type") or "screening").strip()
    location = (form.get("location") or "Head Office").strip()
    c = _candidate_for_application(db, a)
    a.panel_id = panel.id
    members = [m for m in (panel.member_ids or []) if db.get(User, m)] or [user.id]
    created = []
    for uid in members:
        iv = Interview(candidate_id=c.id, interviewer_id=uid, scheduled_at=at, interview_type=itype, status="scheduled")
        _iv_write(iv, location, "")
        db.add(iv)
        created.append(iv)
        notify(db, uid, "Interview scheduled",
               f"{a.full_name} ({a.request.title if a.request else 'vacancy'}) — {itype} on {at:%d %b %Y %H:%M} at {location}.",
               event_type="recruitment", link="/hr/interviews")
    db.flush()
    stage = {"screening": "marked_1st_interview", "technical": "marked_2nd_interview",
             "demo_class": "marked_2nd_interview", "final": "marked_final_interview"}.get(itype)
    if stage and a.status not in ("hired", "rejected") and _status_rank(stage) > _status_rank(a.status):
        before = {"status": a.status}
        a.status = stage
        log_action(db, user, "status_change", "recruitment", entity=a,
                   description=f"Application {a.full_name} moved to {APPLICATION_LABELS[stage]} by scheduling an interview",
                   before=before, after={"status": stage}, request=request)
    log_action(db, user, "create", "recruitment", entity=created[0] if created else None, entity_type="Interview",
               description=f"{itype} interview for {a.full_name} with panel '{panel.name}' on {at:%d %b %Y %H:%M} at {location}",
               after={"members": len(created), "panel": panel.name, "location": location}, request=request)
    db.commit()
    return redirect("/hr/interviews", f"Interview scheduled for {a.full_name} with {len(created)} panel member(s).")


@router.post("/interviews/{id}/outcome", include_in_schema=False)
async def interview_outcome(id: int, request: Request, db: Session = Depends(get_db),
                            user: User = Depends(require("recruitment.update"))):
    """Record one panel member's outcome, score and feedback for an interview."""
    iv = db.get(Interview, id)
    if not iv:
        raise HTTPException(404, "Interview not found")
    form = await request.form()
    before = {"status": iv.status, "score": iv.score}
    iv.status = (form.get("status") or "completed").strip()
    if form.get("score"):
        iv.score = parse_float(form.get("score"), 0)
    _iv_write(iv, _iv_location(iv), form.get("feedback") or _iv_notes(iv))
    scores = [i.score for i in db.query(Interview).filter(Interview.candidate_id == iv.candidate_id,
                                                          Interview.score.isnot(None))]
    if scores and iv.candidate:
        iv.candidate.score = round(sum(scores) / len(scores), 1)
    log_action(db, user, "update", "recruitment", entity=iv,
               description=f"Interview outcome for {iv.candidate.full_name if iv.candidate else 'candidate'}: "
                           f"{iv.status} (score {iv.score if iv.score is not None else '-'})",
               rationale=_iv_notes(iv) or None, before=before, after={"status": iv.status, "score": iv.score},
               request=request)
    db.commit()
    return redirect(form.get("next") or "/hr/interviews", "Interview outcome saved.")


# ------------------------------------------------------------------ candidate database
@router.get("/candidates", include_in_schema=False)
def candidate_database(request: Request, q: str = "", skill: str = "", qualification: str = "", city: str = "",
                       status: str = "", db: Session = Depends(get_db),
                       user: User = Depends(require("recruitment.view"))):
    """Everybody who has ever applied, with their applications and where they stand today."""
    apps = db.query(JobApplication).order_by(JobApplication.application_date.desc(), JobApplication.id.desc()).all()
    people: dict[str, dict] = {}
    for a in apps:
        key = (a.email or a.nic_number or a.cell_no or f"app-{a.id}").lower()
        row = people.setdefault(key, {"name": a.full_name, "father_name": a.father_name, "gender": a.gender,
                                      "email": a.email, "cell_no": a.cell_no, "nic": a.nic_number,
                                      "qualification": a.qualification, "experience": a.experience_years,
                                      "city": a.city, "candidate_id": a.candidate_id, "applications": [],
                                      "status": a.status, "skills": set(), "hired_employee_id": a.hired_employee_id})
        row["applications"].append(a)
        row["qualification"] = row["qualification"] or a.qualification
        row["city"] = row["city"] or a.city
        row["candidate_id"] = row["candidate_id"] or a.candidate_id
        row["hired_employee_id"] = row["hired_employee_id"] or a.hired_employee_id
        if a.request and a.request.skills:
            row["skills"].update(a.request.skills)
        if _status_rank(a.status) > _status_rank(row["status"]):
            row["status"] = a.status
    # candidates from the older pipeline that never produced an application row
    linked = {r["candidate_id"] for r in people.values() if r["candidate_id"]}
    for c in db.query(Candidate).order_by(Candidate.id.desc()).all():
        if c.id in linked:
            continue
        people[f"cand-{c.id}"] = {"name": c.full_name, "father_name": None, "gender": c.gender, "email": c.email,
                                  "cell_no": c.phone, "nic": None, "qualification": (c.notes or "")[:80] or None,
                                  "experience": None, "city": None, "candidate_id": c.id, "applications": [],
                                  "status": {"hired": "hired", "rejected": "rejected", "offer": "selected"}.get(c.stage, "applied"),
                                  "skills": set(), "hired_employee_id": c.hired_employee_id}
    rows = list(people.values())
    for r in rows:
        r["skills"] = sorted(r["skills"])
    if q:
        rows = [r for r in rows if q.lower() in (r["name"] or "").lower()]
    if skill:
        rows = [r for r in rows if any(skill.lower() in s.lower() for s in r["skills"])]
    if qualification:
        rows = [r for r in rows if qualification.lower() in (r["qualification"] or "").lower()]
    if city:
        rows = [r for r in rows if city.lower() in (r["city"] or "").lower()]
    if status:
        rows = [r for r in rows if r["status"] == status]
    rows.sort(key=lambda r: (r["name"] or "").lower())
    stats = {"people": len(rows), "applications": sum(len(r["applications"]) for r in rows),
             "hired": sum(1 for r in rows if r["status"] == "hired"),
             "in_process": sum(1 for r in rows if r["status"] not in ("hired", "rejected"))}
    return render(request, "hr/recruitment_candidates.html", {
        "user": user, "rows": rows, "q": q, "skill": skill, "qualification": qualification, "city": city,
        "status": status, "stats": stats, "statuses": APPLICATION_STATUSES, "labels": APPLICATION_LABELS,
        "status_options": [(s, APPLICATION_LABELS[s]) for s in APPLICATION_STATUSES]})


# ------------------------------------------------------------------ onboarding
@router.get("/onboarding", include_in_schema=False)
def onboarding_board(request: Request, q: str = "", state: str = "", db: Session = Depends(get_db),
                     user: User = Depends(require("employees.view", "provisioning.view", any_of=True))):
    """New hires and how far through their onboarding checklist they are."""
    tasks = db.query(OnboardingTask).order_by(OnboardingTask.due_date).all()
    by_emp: dict[int, list[OnboardingTask]] = {}
    for t in tasks:
        by_emp.setdefault(t.employee_id, []).append(t)
    cutoff = date.today() - timedelta(days=180)
    employees = db.query(Employee).filter(Employee.join_date >= cutoff).order_by(Employee.join_date.desc()).all()
    known = {e.id for e in employees}
    for eid in by_emp:
        if eid not in known:
            e = db.get(Employee, eid)
            if e is not None:
                employees.append(e)
    rows = []
    for e in employees:
        items = by_emp.get(e.id, [])
        done = sum(1 for t in items if t.status in ("completed", "done"))
        rows.append({"employee": e, "tasks": items, "done": done, "total": len(items),
                     "pct": round(100 * done / len(items)) if items else 0,
                     "overdue": sum(1 for t in items if t.status not in ("completed", "done") and t.due_date and t.due_date < date.today())})
    if q:
        rows = [r for r in rows if q.lower() in r["employee"].full_name.lower()]
    if state == "open":
        rows = [r for r in rows if r["total"] and r["pct"] < 100]
    elif state == "complete":
        rows = [r for r in rows if r["total"] and r["pct"] == 100]
    elif state == "none":
        rows = [r for r in rows if not r["total"]]
    rows.sort(key=lambda r: (r["employee"].join_date or date.min), reverse=True)
    stats = {"hires": len(rows), "open": sum(1 for r in rows if r["total"] and r["pct"] < 100),
             "complete": sum(1 for r in rows if r["total"] and r["pct"] == 100),
             "overdue": sum(r["overdue"] for r in rows)}
    return render(request, "hr/recruitment_onboarding.html", {
        "user": user, "rows": rows, "q": q, "state": state, "stats": stats,
        "states": [("open", "In progress"), ("complete", "Complete"), ("none", "No checklist")],
        "today": date.today(), "can_edit": rbac.has_permission(user, "employees.update")})


# ------------------------------------------------------------------ the older candidate pipeline (kept working)
@router.post("/recruitment/candidates/new", include_in_schema=False)
async def candidate_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("recruitment.add"))):
    form = await request.form()
    name = (form.get("full_name") or "").strip()
    if not name:
        return redirect("/hr/recruitment?tab=pipeline", "Candidate name is required.", "error")
    c = Candidate(request_id=parse_int(form.get("request_id")), full_name=name, email=form.get("email"), phone=form.get("phone"),
                  gender=form.get("gender"), applied_for=form.get("applied_for") or "Quran Teacher", source=form.get("source") or "Website",
                  stage="applied", notes=form.get("notes"), score=parse_float(form.get("score"), 0) or None)
    db.add(c)
    db.flush()
    log_action(db, user, "create", "recruitment", entity=c, description=f"Candidate {name} added for {c.applied_for}", request=request)
    db.commit()
    return redirect(f"/hr/recruitment/candidates/{c.id}", "Candidate added to the pipeline.")


@router.get("/recruitment/candidates/{id}", include_in_schema=False)
def candidate_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("recruitment.view"))):
    c = db.get(Candidate, id)
    if not c:
        raise HTTPException(404, "Candidate not found")
    interviewers = db.query(User).join(Role, User.role_id == Role.id)\
        .filter(Role.slug.in_(["hod_people", "hr_officer", "hod_academics", "hod_qa", "supervisor", "manager", "super_admin"]),
                User.is_active.is_(True)).order_by(User.full_name).all()
    return render(request, "hr/candidate_detail.html", {"user": user, "c": c, "stages": STAGES,
                                                        "interviews": db.query(Interview).filter(Interview.candidate_id == c.id).order_by(Interview.scheduled_at).all(),
                                                        "interviewers": [(u.id, u.full_name) for u in interviewers],
                                                        "departments": _departments(db)})


@router.post("/recruitment/candidates/{id}/stage", include_in_schema=False)
async def candidate_stage(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("recruitment.update"))):
    c = db.get(Candidate, id)
    if not c:
        raise HTTPException(404, "Candidate not found")
    form = await request.form()
    stage = form.get("stage") or "applied"
    if stage not in STAGES:
        return redirect(f"/hr/recruitment/candidates/{c.id}", "Unknown pipeline stage.", "error")
    before = {"stage": c.stage}
    c.stage = stage
    if form.get("score"):
        c.score = parse_float(form.get("score"), 0)
    if form.get("notes"):
        c.notes = ((c.notes + "\n") if c.notes else "") + f"[{date.today()}] {form.get('notes')}"
    log_action(db, user, "status_change", "recruitment", entity=c, description=f"Candidate {c.full_name}: {before['stage']} -> {stage}",
               before=before, after={"stage": stage}, rationale=form.get("notes"), request=request)
    db.commit()
    return redirect(form.get("next") or f"/hr/recruitment/candidates/{c.id}", f"Candidate moved to {stage}.")


@router.post("/recruitment/candidates/{id}/interview", include_in_schema=False)
async def candidate_interview(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("recruitment.update"))):
    c = db.get(Candidate, id)
    if not c:
        raise HTTPException(404, "Candidate not found")
    form = await request.form()
    when = parse_date(form.get("scheduled_date")) or date.today()
    hh, _, mm = (form.get("scheduled_time") or "10:00").partition(":")
    try:
        at = datetime.combine(when, datetime.min.time().replace(hour=int(hh), minute=int(mm or 0)))
    except ValueError:
        at = datetime.combine(when, datetime.min.time().replace(hour=10))
    iv = Interview(candidate_id=c.id, interviewer_id=parse_int(form.get("interviewer_id")) or user.id, scheduled_at=at,
                   interview_type=form.get("interview_type") or "screening", status="scheduled")
    db.add(iv)
    db.flush()
    if c.stage in ("applied", "screening"):
        c.stage = "interview" if iv.interview_type != "demo_class" else "demo"
    log_action(db, user, "create", "recruitment", entity=iv, description=f"{iv.interview_type} interview scheduled for {c.full_name} at {at:%d %b %H:%M}", request=request)
    if iv.interviewer_id:
        notify(db, iv.interviewer_id, "Interview scheduled", f"{c.full_name} ({c.applied_for}) — {iv.interview_type} on {at:%d %b %Y %H:%M}.",
               event_type="recruitment", link=f"/hr/recruitment/candidates/{c.id}")
    db.commit()
    return redirect(f"/hr/recruitment/candidates/{c.id}", "Interview scheduled.")


@router.post("/recruitment/interviews/{id}/feedback", include_in_schema=False)
async def interview_feedback(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("recruitment.update"))):
    iv = db.get(Interview, id)
    if not iv:
        raise HTTPException(404, "Interview not found")
    form = await request.form()
    iv.score = parse_float(form.get("score"), 0)
    _iv_write(iv, _iv_location(iv), form.get("feedback") or "")
    iv.status = form.get("status") or "completed"
    scores = [i.score for i in db.query(Interview).filter(Interview.candidate_id == iv.candidate_id, Interview.score.isnot(None))]
    if scores:
        iv.candidate.score = round(sum(scores) / len(scores), 1)
    log_action(db, user, "update", "recruitment", entity=iv, description=f"Interview feedback recorded for {iv.candidate.full_name} (score {iv.score})",
               rationale=_iv_notes(iv), request=request)
    db.commit()
    return redirect(f"/hr/recruitment/candidates/{iv.candidate_id}", "Interview feedback saved.")


@router.post("/recruitment/candidates/{id}/hire", include_in_schema=False)
async def candidate_hire(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.add"))):
    c = db.get(Candidate, id)
    if not c:
        raise HTTPException(404, "Candidate not found")
    if c.hired_employee_id:
        return redirect(f"/hr/recruitment/candidates/{c.id}", "This candidate has already been hired.", "warning")
    form = await request.form()
    emp, pwd = _hire_candidate(db, c, form, user, request)
    for a in db.query(JobApplication).filter(JobApplication.candidate_id == c.id):
        a.status = "hired"
        a.hired_employee_id = emp.id
    db.commit()
    msg = f"{c.full_name} hired as {emp.employee_code}."
    if pwd:
        msg += f" Temporary password: {pwd}"
    return redirect(f"/hr/employees/{emp.id}", msg)


@router.get("/recruitment/{id}", include_in_schema=False)
def recruitment_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("recruitment.view"))):
    r = _requisition(db, id)
    return render(request, "hr/recruitment_detail.html", {"user": user, "r": r, "stages": STAGES,
                                                          "candidates": db.query(Candidate).filter(Candidate.request_id == r.id).order_by(Candidate.created_at.desc()).all(),
                                                          "applications": db.query(JobApplication).filter(JobApplication.request_id == r.id).order_by(JobApplication.id.desc()).all(),
                                                          "labels": APPLICATION_LABELS,
                                                          "departments": _departments(db)})


@router.post("/recruitment/{id}/edit", include_in_schema=False)
async def recruitment_edit(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("recruitment.update"))):
    r = _requisition(db, id)
    form = await request.form()
    before = snapshot(r)
    _apply_requisition(r, form)
    log_action(db, user, "update", "recruitment", entity=r, description=f"Job requisition '{r.title}' updated",
               before=before, after=snapshot(r), request=request)
    db.commit()
    return redirect(form.get("next") or "/hr/recruitment", f"Job requisition '{r.title}' updated.")


@router.post("/recruitment/{id}/approve", include_in_schema=False)
async def recruitment_approve(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("recruitment.approve"))):
    r = _requisition(db, id)
    form = await request.form()
    before = {"status": r.status}
    decision = form.get("decision") or "approve"
    r.status = {"approve": "approved", "cancel": "cancelled", "close": "filled"}.get(decision, "approved")
    if decision == "approve":
        r.approved_by_id = user.id
    log_action(db, user, "approve" if decision == "approve" else "status_change", "recruitment", entity=r,
               description=f"Job requisition '{r.title}' -> {r.status}", rationale=form.get("note"),
               before=before, after={"status": r.status}, request=request)
    if r.requested_by_id:
        notify(db, r.requested_by_id, f"Job requisition {r.status}", f"'{r.title}' is now {r.status}.",
               event_type="recruitment", link=f"/hr/recruitment/{r.id}")
    db.commit()
    return redirect(f"/hr/recruitment/{r.id}", f"Request {r.status}.")


# ============================================================================== PAYROLL
@router.get("/payroll", include_in_schema=False)
def payroll_list(request: Request, status: str = "", year: str = "", db: Session = Depends(get_db),
                 user: User = Depends(require("payroll.view"))):
    """Payroll runs in the ERP's shape: Month · Description · Status · Detail."""
    query = db.query(PayrollRun)
    if status:
        query = query.filter(PayrollRun.status == status)
    if year:
        query = query.filter(PayrollRun.period.like(f"{year}-%"))
    runs = query.order_by(PayrollRun.period.desc(), PayrollRun.id.desc()).all()
    counts = {r.id: len(r.payslips) for r in runs}
    all_runs = db.query(PayrollRun).all()
    by_status = {"pending": 0, "generated": 0, "posted": 0, "cancelled": 0}
    for r in all_runs:
        by_status[pay.erp_status(r)] = by_status.get(pay.erp_status(r), 0) + 1
    last = date.today().replace(day=1) - timedelta(days=1)
    stats = {"runs": len(all_runs), **by_status,
             "last_net": float(runs[0].total_net) if runs else 0.0,
             "advances": db.query(func.count(SalaryAdvance.id)).filter(SalaryAdvance.status == "pending").scalar() or 0,
             "bonuses": db.query(func.count(Bonus.id)).filter(Bonus.status == "pending").scalar() or 0}
    years = sorted({r.period[:4] for r in all_runs if r.period}, reverse=True)
    return render(request, "hr/payroll_list.html", {
        "user": user, "runs": runs, "counts": counts, "stats": stats, "status": status, "year": year,
        "years": [(y, y) for y in years], "statuses": [(s, s.title()) for s in pay.ERP_PAYROLL_STATUSES],
        "suggested_period": last.strftime("%Y-%m"), "erp_status": pay.erp_status,
        "locked": pay.erp_run_is_locked, "can_approve": pay.can_approve_payroll(user)})


@router.post("/payroll/new", include_in_schema=False)
async def payroll_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.add"))):
    """Create a payroll run for a Month with a Description; it starts Pending."""
    form = await request.form()
    period = _month_param(form.get("period"))
    try:
        run = pay.erp_create_run(db, period, form.get("description") or "", user, request=request)
    except ValueError as exc:
        return redirect("/hr/payroll", str(exc), "error")
    db.commit()
    return redirect("/hr/payroll", f"Payroll {period} created. Generate it to build the payslips.")


@router.post("/payroll/generate", include_in_schema=False)
async def payroll_generate(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.add"))):
    """Create-and-generate in one step (kept for the older quick action and the API)."""
    form = await request.form()
    period = _month_param(form.get("period"))
    run = db.query(PayrollRun).filter(PayrollRun.period == period).order_by(PayrollRun.id.desc()).first()
    try:
        if run is None or run.status == "cancelled":
            run = pay.erp_create_run(db, period, form.get("description") or f"Payroll {period}", user, request=request)
        run = pay.erp_generate_run(db, run, user, request=request)
    except ValueError as exc:
        return redirect("/hr/payroll", str(exc), "error")
    db.commit()
    return redirect(f"/hr/payroll/{run.id}", f"Payroll for {period} generated: {len(run.payslips)} payslip(s).")


@router.get("/payroll/teachers", include_in_schema=False)
def payroll_teachers(request: Request, period: str = "", db: Session = Depends(get_db), user: User = Depends(require("payroll.view"))):
    period = _month_param(period)
    rows = pay.teacher_cost_output(db, period)
    totals = {"cost": sum(r["cost"] for r in rows), "revenue": sum(r["revenue"] for r in rows),
              "classes": sum(r["classes"] for r in rows), "students": sum(r["students"] for r in rows)}
    totals["ratio"] = round(100 * totals["cost"] / totals["revenue"], 1) if totals["revenue"] else None
    labels = [r["teacher"].full_name for r in rows]
    return render(request, "hr/payroll_teachers.html", {"user": user, "rows": rows, "period": period, "totals": totals,
                                                        "labels": labels, "cost_data": [r["cost"] for r in rows],
                                                        "revenue_data": [r["revenue"] for r in rows]})


@router.get("/payroll/bands", include_in_schema=False)
def payroll_bands(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.view"))):
    bands = pay.salary_bands(db)
    rules = pay.grade_rules(db)
    counts = dict(db.query(Teacher.grade, func.count(Teacher.id)).group_by(Teacher.grade).all())
    return render(request, "hr/payroll_bands.html", {"user": user, "bands": bands, "rules": rules, "counts": counts,
                                                     "can_configure": rbac.has_permission(user, "payroll.configure")})


@router.post("/payroll/bands", include_in_schema=False)
async def payroll_bands_save(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.configure"))):
    form = await request.form()
    bands = {}
    for k, v in zip(form.getlist("band_key"), form.getlist("band_value")):
        k = (k or "").strip().upper()
        if k:
            bands[k] = parse_float(v, 0)
    if not bands:
        return redirect("/hr/payroll/bands", "At least one band is required.", "error")
    s = db.query(Setting).filter(Setting.key == "salary_bands").first()
    before = dict(s.value or {}) if s else {}
    if not s:
        s = Setting(key="salary_bands", group="payroll", description="Base salary band by teacher grade (PKR)")
        db.add(s)
    s.value = bands
    log_action(db, user, "configure", "payroll", entity=s, entity_type="Setting", description="Salary bands updated",
               rationale=form.get("rationale") or "Band review", before=before, after=bands, consequential=True, request=request)
    db.commit()
    return redirect("/hr/payroll/bands", "Salary bands updated. New bands apply at the next grade computation.")


@router.get("/payroll/advances", include_in_schema=False)
def payroll_advances(request: Request, status: str = "", db: Session = Depends(get_db), user: User = Depends(require("payroll.view"))):
    q = db.query(SalaryAdvance)
    if status:
        q = q.filter(SalaryAdvance.status == status)
    rows = q.order_by(SalaryAdvance.id.desc()).all()
    return render(request, "hr/payroll_queue.html", {"user": user, "kind": "advances", "rows": rows, "status": status,
                                                     "employees": _employee_options(db),
                                                     "statuses": ["pending", "approved", "rejected", "settled"],
                                                     "outstanding": sum(float(r.remaining or 0) for r in rows if r.status == "approved")})


@router.post("/payroll/advances/new", include_in_schema=False)
async def advance_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.add"))):
    form = await request.form()
    e = _emp(db, parse_int(form.get("employee_id"), 0) or 0)
    amount = parse_float(form.get("amount"), 0)
    if amount <= 0:
        return redirect("/hr/payroll/advances", "Amount must be greater than zero.", "error")
    svc.request_advance(db, e, amount, parse_int(form.get("installments"), 1) or 1, form.get("reason"), user, request=request)
    db.commit()
    return redirect("/hr/payroll/advances", "Salary advance requested.")


@router.post("/payroll/advances/{id}/decide", include_in_schema=False)
async def advance_decide(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.approve"))):
    a = db.get(SalaryAdvance, id)
    if not a:
        raise HTTPException(404, "Advance not found")
    form = await request.form()
    svc.decide_bonus_or_advance(db, a, form.get("decision") == "approve", user, note=form.get("note"), request=request)
    db.commit()
    return redirect("/hr/payroll/advances", f"Advance {a.status}.")


@router.get("/payroll/bonuses", include_in_schema=False)
def payroll_bonuses(request: Request, status: str = "", db: Session = Depends(get_db), user: User = Depends(require("payroll.view"))):
    q = db.query(Bonus)
    if status:
        q = q.filter(Bonus.status == status)
    rows = q.order_by(Bonus.id.desc()).all()
    return render(request, "hr/payroll_queue.html", {"user": user, "kind": "bonuses", "rows": rows, "status": status,
                                                     "employees": _employee_options(db),
                                                     "statuses": ["pending", "approved", "rejected"],
                                                     "outstanding": sum(float(r.amount or 0) for r in rows if r.status == "approved")})


@router.post("/payroll/bonuses/new", include_in_schema=False)
async def bonus_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.add"))):
    form = await request.form()
    e = _emp(db, parse_int(form.get("employee_id"), 0) or 0)
    amount = parse_float(form.get("amount"), 0)
    if amount <= 0:
        return redirect("/hr/payroll/bonuses", "Amount must be greater than zero.", "error")
    svc.request_bonus(db, e, amount, form.get("bonus_type") or "performance", form.get("reason"), _month_param(form.get("period")), user, request=request)
    db.commit()
    return redirect("/hr/payroll/bonuses", "Bonus proposed.")


@router.post("/payroll/bonuses/{id}/decide", include_in_schema=False)
async def bonus_decide(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.approve"))):
    b = db.get(Bonus, id)
    if not b:
        raise HTTPException(404, "Bonus not found")
    form = await request.form()
    svc.decide_bonus_or_advance(db, b, form.get("decision") == "approve", user, note=form.get("note"), request=request)
    db.commit()
    return redirect("/hr/payroll/bonuses", f"Bonus {b.status}.")


@router.post("/payroll/payslips/{id}/adjust", include_in_schema=False)
async def payslip_adjust(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.update"))):
    ps = db.get(Payslip, id)
    if not ps:
        raise HTTPException(404, "Payslip not found")
    form = await request.form()
    if pay.erp_run_is_locked(ps.payroll_run):
        return redirect(f"/hr/payroll/{ps.payroll_run_id}", "This payroll run is posted and can no longer be changed.", "error")
    try:
        pay.adjust_payslip(db, ps, parse_float(form.get("amount"), 0), form.get("reason") or "", user, request=request)
    except ValueError as exc:
        return redirect(f"/hr/payroll/{ps.payroll_run_id}", str(exc), "error")
    db.commit()
    return redirect(f"/hr/payroll/{ps.payroll_run_id}", "Payslip adjusted.")


@router.get("/payroll/{id}", include_in_schema=False)
def payroll_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.view"))):
    run = db.get(PayrollRun, id)
    if not run:
        raise HTTPException(404, "Payroll run not found")
    slips = sorted(run.payslips, key=lambda p: (not (p.details or {}).get("is_teacher"), p.employee.full_name if p.employee else ""))
    return render(request, "hr/payroll_detail.html", {"user": user, "run": run, "slips": slips,
                                                      "can_approve": pay.can_approve_payroll(user),
                                                      "components": pay.erp_run_components(run),
                                                      "locked": pay.erp_run_is_locked(run),
                                                      "summary": pay.payroll_summary(db, run.period)})


@router.get("/payroll/{id}/export.csv", include_in_schema=False)
def payroll_export(id: int, db: Session = Depends(get_db), user: User = Depends(require("payroll.view"))):
    run = db.get(PayrollRun, id)
    if not run:
        raise HTTPException(404, "Payroll run not found")
    rows = []
    for p in run.payslips:
        e = p.employee
        rows.append([run.period, e.employee_code if e else "", e.full_name if e else "", e.designation if e else "",
                     float(p.basic), float(p.class_pay), p.classes_taught, float(p.allowances), float(p.bonus),
                     float(p.attendance_deduction), float(p.advance_deduction), float(p.deductions), float(p.gross),
                     float(p.net), p.currency, p.status])
    return _csv(rows, ["Period", "Code", "Employee", "Designation", "Basic", "Class pay", "Classes", "Allowances", "Bonus",
                       "Attendance deduction", "Advance", "Other deductions", "Gross", "Net", "Currency", "Status"],
                f"payroll-{run.period}.csv")


@router.post("/payroll/{id}/generate", include_in_schema=False)
async def payroll_run_generate(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.add"))):
    run = db.get(PayrollRun, id)
    if not run:
        raise HTTPException(404, "Payroll run not found")
    try:
        run = pay.erp_generate_run(db, run, user, request=request)
    except ValueError as exc:
        return redirect("/hr/payroll", str(exc), "error")
    db.commit()
    return redirect(f"/hr/payroll/{run.id}", f"Payroll {run.period} generated: {len(run.payslips)} payslip(s).")


@router.post("/payroll/{id}/post", include_in_schema=False)
async def payroll_post(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.approve"))):
    run = db.get(PayrollRun, id)
    if not run:
        raise HTTPException(404, "Payroll run not found")
    form = await request.form()
    try:
        pay.erp_post_run(db, run, user, rationale=form.get("rationale") or "", request=request)
    except (ValueError, PermissionError) as exc:
        return redirect(f"/hr/payroll/{run.id}", str(exc), "error")
    pay.generate_run_pdfs(db, run)
    pay.post_run_to_employee_ledgers(db, run, user)   # each payslip onto its employee's Account Ledger
    db.commit()
    return redirect(f"/hr/payroll/{run.id}", f"Payroll {run.period} posted and locked.")


@router.post("/payroll/{id}/cancel", include_in_schema=False)
async def payroll_cancel(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.approve"))):
    run = db.get(PayrollRun, id)
    if not run:
        raise HTTPException(404, "Payroll run not found")
    form = await request.form()
    try:
        pay.erp_cancel_run(db, run, user, reason=form.get("reason") or "", request=request)
    except ValueError as exc:
        return redirect(f"/hr/payroll/{run.id}", str(exc), "error")
    db.commit()
    return redirect("/hr/payroll", f"Payroll {run.period} cancelled.")


@router.post("/payroll/{id}/submit", include_in_schema=False)
async def payroll_submit(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.update"))):
    run = db.get(PayrollRun, id)
    if not run:
        raise HTTPException(404, "Payroll run not found")
    try:
        pay.submit_payroll(db, run, user, request=request)
    except ValueError as exc:
        return redirect(f"/hr/payroll/{run.id}", str(exc), "error")
    db.commit()
    return redirect(f"/hr/payroll/{run.id}", "Payroll submitted for approval.")


@router.post("/payroll/{id}/approve", include_in_schema=False)
async def payroll_approve(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.approve"))):
    run = db.get(PayrollRun, id)
    if not run:
        raise HTTPException(404, "Payroll run not found")
    form = await request.form()
    try:
        pay.approve_payroll(db, run, user, rationale=form.get("rationale") or "", request=request)
    except (ValueError, PermissionError) as exc:
        return redirect(f"/hr/payroll/{run.id}", str(exc), "error")
    pay.post_run_to_employee_ledgers(db, run, user)   # each payslip onto its employee's Account Ledger
    db.commit()
    return redirect(f"/hr/payroll/{run.id}", f"Payroll {run.period} approved and posted to the ledger.")


@router.post("/payroll/{id}/pay", include_in_schema=False)
async def payroll_pay(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.execute"))):
    run = db.get(PayrollRun, id)
    if not run:
        raise HTTPException(404, "Payroll run not found")
    try:
        pay.mark_paid(db, run, user, request=request)
    except ValueError as exc:
        return redirect(f"/hr/payroll/{run.id}", str(exc), "error")
    pay.generate_run_pdfs(db, run)
    db.commit()
    return redirect(f"/hr/payroll/{run.id}", "Payroll marked paid; payslips issued to every employee.")


@router.get("/payslips/{id}", include_in_schema=False)
def payslip_detail(id: int, request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    ps = db.get(Payslip, id)
    if not ps:
        raise HTTPException(404, "Payslip not found")
    own = ctx.employee is not None and ps.employee_id == ctx.employee.id
    if not own and not rbac.has_permission(ctx.user, "payroll.view"):
        raise PermissionDenied("payroll.view")
    return render(request, "hr/payslip.html", {"user": ctx.user, "ps": ps, "own": own})


@router.get("/payslips/{id}/pdf", include_in_schema=False)
def payslip_download(id: int, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    ps = db.get(Payslip, id)
    if not ps:
        raise HTTPException(404, "Payslip not found")
    own = ctx.employee is not None and ps.employee_id == ctx.employee.id
    if not own and not rbac.has_permission(ctx.user, "payroll.view"):
        raise PermissionDenied("payroll.view")
    path = BASE_DIR / (ps.pdf_path or "")
    if not ps.pdf_path or not path.exists():
        pay.payslip_pdf(db, ps)
        db.commit()
        path = BASE_DIR / ps.pdf_path
    return FileResponse(str(path), media_type="application/pdf", filename=path.name)


# ============================================================================== HR DASHBOARDS
DASHBOARD_CARDS = [
    ("Employee Management", "/hr/dashboards/employees", "users", "Headcount by department, designation, type, shift and gender"),
    ("Attendance Management", "/hr/dashboards/attendance", "clock", "Present, absent, leave and late by day and by employee"),
    ("Financial Management", "/hr/dashboards/financial", "banknote", "Payroll cost, violations, bonuses and advances"),
]


@router.get("/dashboards", include_in_schema=False)
def hr_dashboards(request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.view"))):
    return render(request, "hr/dashboard_index.html", {
        "user": user, "cards": [{"title": t, "url": u, "icon": i, "blurb": b} for t, u, i, b in DASHBOARD_CARDS]})


@router.get("/dashboards/employees", include_in_schema=False)
def hr_dashboard_employees(request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.view"))):
    from app.services import hr_dashboards as hrd
    return render(request, "hr/dashboard_employees.html", {"user": user, "d": hrd.employee_dashboard(db)})


@router.get("/dashboards/attendance", include_in_schema=False)
def hr_dashboard_attendance(request: Request, from_date: str = "", to_date: str = "", department_id: int = 0,
                            db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.view"))):
    from app.services import hr_dashboards as hrd
    end = parse_date(to_date) or date.today()
    start = parse_date(from_date) or (end - timedelta(days=29))
    if start > end:
        start, end = end, start
    return render(request, "hr/dashboard_attendance.html", {
        "user": user, "d": hrd.attendance_dashboard(db, start, end, department_id or None),
        "from_date": start.isoformat(), "to_date": end.isoformat(), "department_id": department_id,
        "departments": _dept_options(db)})


@router.get("/dashboards/financial", include_in_schema=False)
def hr_dashboard_financial(request: Request, months: int = 6, db: Session = Depends(get_db),
                           user: User = Depends(require("payroll.view"))):
    from app.services import hr_dashboards as hrd
    months = max(3, min(24, months or 6))
    return render(request, "hr/dashboard_financial.html", {"user": user, "d": hrd.financial_dashboard(db, months),
                                                           "months": months})


# ============================================================================== VIOLATIONS (ERP Staff Violations)
VIOLATION_META = {
    "title": "Staff Violations", "back": "/home/hr",
    "subtitle": "Offences raised against the Violation Types catalogue. Approving applies the fine, so payroll "
                "picks it up in the next run.",
    "headers": ["ID", "Employee", "Shift", "Penalty Description", "Penalty Amount", "Remarks", "Created By",
                "Created At", "Status"],
    "filters": ["employee", "shift", "violation_type", "dates"], "create_label": "Create Staff Violation",
    "remarks_field": "remarks", "remarks_label": "Remarks", "search_hint": "Employee / code",
}
# A violation carries the fine it will attract while it is still pending, but ``deduction_amount`` stays at
# zero until it is approved so that payroll never picks up an unapproved fine. The proposed figure (the
# catalogue's standard fine, or whatever the manager agreed instead) is kept on the record as free text.
PROPOSED_FINE = "Proposed fine: "


def _proposed_fine(v: Violation) -> float:
    text = v.action_taken or ""
    if text.startswith(PROPOSED_FINE):
        return parse_float(text[len(PROPOSED_FINE):].split(" ")[0], 0)
    return 0.0


@router.get("/violations", include_in_schema=False)
def violations_list(request: Request, page: int = 1, status: str = "", employee: str = "", shift: str = "",
                    violation_type_id: str = "", date_from: str = "", date_to: str = "", q: str = "",
                    db: Session = Depends(get_db), user: User = Depends(require("violations.view"))):
    f = filters_from(q, status, employee, shift, violation_type_id=violation_type_id, date_from=date_from,
                     date_to=date_to)
    query = db.query(Violation)
    if status:
        query = query.filter(Violation.approval_status == status)
    if parse_int(employee):
        query = query.filter(Violation.employee_id == int(employee))
    if parse_int(violation_type_id):
        query = query.filter(Violation.violation_type_id == int(violation_type_id))
    if shift:
        query = query.filter(Violation.employee_id.in_(
            [r[0] for r in db.query(Employee.id).filter(Employee.shift == shift)] or [-1]))
    if q:
        query = employee_search(query, Violation, q)
    df, dt_ = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(Violation.date >= df)
    if dt_:
        query = query.filter(Violation.date <= dt_)
    pg = paginate(query.order_by(Violation.date.desc(), Violation.id.desc()), page, 25)
    reporters = {u.id: u.full_name for u in db.query(User)}
    rows = []
    for v in pg.items:
        cat = v.violation_catalogue
        rows.append({"id": v.id, "status": v.approval_status, "highlight": ROW_HIGHLIGHT.get(v.approval_status),
                     "cells": [v.id, employee_name(v.employee), shift_name(v.employee),
                               cat.description if cat else (v.description or (v.violation_type or "").title()),
                               erp_money(v.deduction_amount or _proposed_fine(v) or (cat.penalty_amount if cat else 0)),
                               v.remarks or v.action_taken, reporters.get(v.reported_by_id, ""),
                               erp_datetime(v.created_at), erp_badge(v.approval_status)]})
    counts = status_counts(db, Violation, Violation.approval_status)
    types = db.query(ViolationType).filter(ViolationType.status == "active").order_by(ViolationType.sort_no).all()
    extra = {"violation_type_options": [(t.id, f"{t.description} ({erp_money(t.penalty_amount)})") for t in types],
             "type_amounts": {str(t.id): float(t.penalty_amount or 0) for t in types}}
    return render_list(request, user, db, "violations", VIOLATION_META, pg, counts, rows, f, "/hr/violations",
                       can_add=rbac.has_permission(user, "violations.add"),
                       can_change=rbac.has_permission(user, "violations.update") or rbac.has_permission(user, "violations.approve"),
                       extra=extra)


@router.post("/violations/new", include_in_schema=False)
async def violation_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("violations.add"))):
    form = await request.form()
    e = _emp(db, parse_int(form.get("employee_id"), 0) or 0)
    cat = db.get(ViolationType, parse_int(form.get("violation_type_id")) or 0)
    remarks = (form.get("remarks") or form.get("description") or "").strip() or None
    deduction = parse_float(form.get("deduction_amount"), 0)
    if cat is not None and not form.get("deduction_amount"):
        deduction = float(cat.penalty_amount or 0)
    v = svc.record_violation(db, e, form.get("violation_type") or "policy",
                             form.get("severity") or (cat.severity if cat else "minor"),
                             cat.description if cat else remarks, user, action_taken=form.get("action_taken"),
                             deduction_amount=0, day=parse_date(form.get("date")) or date.today(),
                             request=request)
    # The ERP keeps the fine pending until it is approved; the catalogue carries the standard amount.
    v.violation_type_id = cat.id if cat else None
    v.remarks = remarks
    v.approval_status = "pending"
    v.deduction_amount = 0
    if deduction > 0:
        v.action_taken = f"{PROPOSED_FINE}{deduction:.0f} PKR"
    db.commit()
    return redirect(form.get("next") or "/hr/violations?status=pending",
                    f"Violation #{v.id} recorded for {e.full_name}; the fine applies once it is approved.", "warning")


@router.post("/violations/change-status", include_in_schema=False)
async def violations_change_status(request: Request, db: Session = Depends(get_db),
                                   user: User = Depends(require("violations.update", "violations.approve", any_of=True))):
    """Bulk Change Status. Approving applies the catalogue's fine as the payroll deduction."""
    form = await request.form()
    status = (form.get("status") or "").strip()
    remarks = (form.get("remarks") or "").strip()
    ids = [int(v) for v in form.getlist("ids") if str(v).isdigit()]
    bad = decide_guard(user, "/hr/violations", status, "violations.approve", ids, remarks)
    if bad is not None:
        return bad
    n = 0
    for v in db.query(Violation).filter(Violation.id.in_(ids)).all():
        before = {"approval_status": v.approval_status, "deduction_amount": float(v.deduction_amount or 0)}
        v.approval_status = status
        v.remarks = remarks
        v.decided_by_id = user.id
        v.decided_at = datetime.utcnow()
        cat = v.violation_catalogue
        if status == "approved":
            if not float(v.deduction_amount or 0):
                v.deduction_amount = _proposed_fine(v) or (float(cat.penalty_amount or 0) if cat else 0)
            v.status = "open"
        else:
            v.deduction_amount = 0
            v.status = "closed"
        if status == "approved":
            # The fine lands on the employee's Account Ledger as a credit; keyed, so it is written once.
            svc.post_violation_ledger(db, v, user)
        log_action(db, user, "approve" if status == "approved" else "status_change", "violations", entity=v,
                   description=f"Staff violation #{v.id} moved from {before['approval_status']} to {status}"
                               + (f"; fine {erp_money(v.deduction_amount)} applied" if status == "approved" else ""),
                   rationale=remarks, before=before,
                   after={"approval_status": status, "deduction_amount": float(v.deduction_amount or 0)},
                   request=request, consequential=True, severity="warning" if status == "approved" else "info")
        notify_employee(db, v.employee, f"Violation {status}",
                        f"Violation #{v.id} was {status}. {remarks}"
                        + (f" Deduction: PKR {erp_money(v.deduction_amount)}." if status == "approved" else ""),
                        "/hr/me?tab=violations")
        n += 1
    db.commit()
    return redirect(f"/hr/violations?status={status}", f"{n} violation(s) moved to {status}.")


@router.post("/violations/{id}/close", include_in_schema=False)
async def violation_close(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("violations.update"))):
    v = db.query(Violation).get(id)
    if not v:
        raise HTTPException(404, "Violation not found")
    form = await request.form()
    svc.close_violation(db, v, user, note=form.get("note"), request=request)
    db.commit()
    return redirect(form.get("next") or "/hr/violations", f"Violation #{v.id} closed.")


# ============================================================================== GRIEVANCES (confidential)
def grievance_guard(user: User = Depends(require("grievances.view"))) -> User:
    """Confidential channel: only People & Culture head, the CEO, and HR officers (limited) may read grievances."""
    if not (user.is_superuser or user.role_slug in ("hod_people", "super_admin", "hr_officer")):
        raise PermissionDenied("grievances.confidential")
    return user


@router.get("/grievances", include_in_schema=False)
def grievances_list(request: Request, status: str = "", db: Session = Depends(get_db), user: User = Depends(grievance_guard)):
    q = db.query(Grievance)
    if status:
        q = q.filter(Grievance.status == status)
    rows = [g for g in q.order_by(Grievance.created_at.desc()).all() if svc.grievance_visible(user, g)]
    now = datetime.utcnow()
    stats = {"open": sum(1 for g in rows if g.status == "open"), "investigating": sum(1 for g in rows if g.status == "investigating"),
             "resolved": sum(1 for g in rows if g.status in ("resolved", "closed")),
             "breached": sum(1 for g in rows if g.sla_due_at and g.sla_due_at < now and g.status in ("open", "investigating")),
             "anonymous": sum(1 for g in rows if g.is_anonymous)}
    return render(request, "hr/grievances.html", {"user": user, "rows": rows, "status": status, "stats": stats, "now": now,
                                                  "statuses": ["open", "investigating", "resolved", "closed"],
                                                  "sla_days": svc.GRIEVANCE_SLA_DAYS})


@router.get("/grievances/new", include_in_schema=False)
def grievance_new(request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    if not rbac.has_permission(ctx.user, "grievances.add"):
        raise PermissionDenied("grievances.add")
    return render(request, "hr/grievance_form.html", {"user": ctx.user, "categories": GRIEVANCE_CATEGORIES,
                                                      "employee": ctx.employee, "sla_days": svc.GRIEVANCE_SLA_DAYS})


@router.post("/grievances/new", include_in_schema=False)
async def grievance_create(request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    if not rbac.has_permission(ctx.user, "grievances.add"):
        raise PermissionDenied("grievances.add")
    form = await request.form()
    subject = (form.get("subject") or "").strip()
    description = (form.get("description") or "").strip()
    if not subject or not description:
        return redirect("/hr/grievances/new", "A subject and a description are required.", "error")
    svc.open_grievance(db, subject, description, form.get("category") or "workplace", ctx.user, employee=ctx.employee,
                       is_anonymous=parse_bool(form.get("is_anonymous")), request=request)
    db.commit()
    target = "/hr/grievances" if (ctx.user.is_superuser or ctx.user.role_slug in ("hod_people", "hr_officer")) else "/hr/me?tab=grievance"
    return redirect(target, "Grievance submitted confidentially to the Head of People & Culture.")


@router.get("/grievances/{id}", include_in_schema=False)
def grievance_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(grievance_guard)):
    g = db.query(Grievance).get(id)
    if not g:
        raise HTTPException(404, "Grievance not found")
    if not svc.grievance_visible(user, g):
        raise PermissionDenied("grievances.confidential")
    handlers = db.query(User).join(Role, User.role_id == Role.id)\
        .filter(Role.slug.in_(["hod_people", "hr_officer", "super_admin"]), User.is_active.is_(True)).order_by(User.full_name).all()
    employee = db.query(Employee).get(g.employee_id) if g.employee_id and not g.is_anonymous else None
    return render(request, "hr/grievance_detail.html", {"user": user, "g": g, "employee": employee,
                                                        "handlers": [(u.id, u.full_name) for u in handlers],
                                                        "statuses": ["open", "investigating", "resolved", "closed"],
                                                        "sla_days": svc.GRIEVANCE_SLA_DAYS, "now": datetime.utcnow()})


@router.post("/grievances/{id}/update", include_in_schema=False)
async def grievance_update(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(grievance_guard)):
    g = db.query(Grievance).get(id)
    if not g:
        raise HTTPException(404, "Grievance not found")
    if not svc.grievance_visible(user, g):
        raise PermissionDenied("grievances.confidential")
    form = await request.form()
    svc.update_grievance(db, g, user, status=form.get("status"), handler_id=parse_int(form.get("handler_id")),
                         resolution=form.get("resolution"), request=request)
    db.commit()
    return redirect(f"/hr/grievances/{g.id}", "Grievance updated.")


# ============================================================================== TEACHER DEVELOPMENT (Module 46)
@router.get("/teacher-development", include_in_schema=False)
def teacher_development(request: Request, tab: str = "grading", db: Session = Depends(get_db),
                        user: User = Depends(require("teacher_dev.view"))):
    tabs = [("grading", "Grading board", "/hr/teacher-development?tab=grading"),
            ("bands", "Band mapping", "/hr/teacher-development?tab=bands"),
            ("training", "Training assignments", "/hr/teacher-development?tab=training"),
            ("gates", "Promotion gates", "/hr/teacher-development?tab=gates"),
            ("plans", "Development plans", "/hr/teacher-development?tab=plans"),
            ("catalogue", "Training catalogue", "/hr/teacher-development?tab=catalogue")]
    teachers = db.query(Teacher).order_by(Teacher.teacher_code).all()
    bands = pay.salary_bands(db)
    rules = pay.grade_rules(db)
    counts = {g: sum(1 for t in teachers if t.grade == g) for g in ("A", "B", "C")}
    ctx: dict = {"user": user, "tab": tab, "tabs": tabs, "teachers": teachers, "bands": bands, "rules": rules,
                 "counts": counts, "catalogue": TRAINING_CATALOGUE, "categories": TRAINING_CATEGORIES,
                 "teacher_options": [(t.id, f"{t.teacher_code} — {t.full_name}") for t in teachers],
                 "stale": sum(1 for t in teachers if not t.grade_computed_at or t.grade_computed_at < datetime.utcnow() - timedelta(days=14))}
    if tab == "grading":
        ctx["history"] = db.query(AuditEvent).filter(AuditEvent.action == "grade_change").order_by(AuditEvent.created_at.desc()).limit(30).all()
    elif tab in ("training", "gates"):
        q = db.query(TrainingAssignment)
        if tab == "gates":
            q = q.filter(TrainingAssignment.is_promotion_gate.is_(True))
        ctx["assignments"] = q.order_by(TrainingAssignment.due_date.is_(None), TrainingAssignment.due_date).all()
        if tab == "gates":
            gate_map: dict[int, dict] = {}
            for a in ctx["assignments"]:
                row = gate_map.setdefault(a.teacher_id, {"teacher": a.teacher, "total": 0, "done": 0, "failed": 0})
                row["total"] += 1
                if a.status == "completed":
                    row["done"] += 1
                elif a.status == "failed":
                    row["failed"] += 1
            for row in gate_map.values():
                row["pct"] = round(100 * row["done"] / row["total"]) if row["total"] else 0
                row["ready"] = row["total"] > 0 and row["done"] == row["total"]
            ctx["gates"] = sorted(gate_map.values(), key=lambda r: -r["pct"])
    elif tab == "plans":
        ctx["plans"] = db.query(DevelopmentPlan).order_by(DevelopmentPlan.id.desc()).limit(100).all()
    return render(request, "hr/teacher_development.html", ctx)


@router.post("/teacher-development/recompute", include_in_schema=False)
async def teacher_dev_recompute(request: Request, db: Session = Depends(get_db), user: User = Depends(require("teacher_dev.update"))):
    result = pay.recompute_all_grades(db, user, request=request)
    db.commit()
    return redirect("/hr/teacher-development", f"{result['teachers']} teacher(s) regraded; {result['changed']} grade change(s).")


@router.post("/teacher-development/{id}/recompute", include_in_schema=False)
async def teacher_dev_recompute_one(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("teacher_dev.update"))):
    t = db.query(Teacher).get(id)
    if not t:
        raise HTTPException(404, "Teacher not found")
    grade = pay.compute_teacher_grade(db, t, user, request=request)
    db.commit()
    return redirect("/hr/teacher-development", f"{t.full_name} is now grade {grade}.")


@router.post("/teacher-development/{id}/promote", include_in_schema=False)
async def teacher_dev_promote(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("teacher_dev.approve"))):
    t = db.query(Teacher).get(id)
    if not t:
        raise HTTPException(404, "Teacher not found")
    form = await request.form()
    rationale = (form.get("rationale") or "").strip()
    grade = (form.get("grade") or "A").upper()[:1]
    if not rationale:
        return redirect("/hr/teacher-development?tab=gates", "A promotion must be justified with a rationale.", "error")
    pay.promote_teacher(db, t, grade, user, rationale, request=request)
    db.commit()
    return redirect("/hr/teacher-development?tab=gates", f"{t.full_name} promoted to grade {grade}.")


@router.post("/teacher-development/training/new", include_in_schema=False)
async def training_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require("teacher_dev.add"))):
    form = await request.form()
    t = db.query(Teacher).get(parse_int(form.get("teacher_id"), 0) or 0)
    title = (form.get("title") or "").strip()
    if not t or not title:
        return redirect("/hr/teacher-development?tab=training", "A teacher and a course title are required.", "error")
    a = TrainingAssignment(teacher_id=t.id, title=title, category=form.get("category") or "tajweed",
                           description=form.get("description"), assigned_by_id=user.id,
                           due_date=parse_date(form.get("due_date")) or (date.today() + timedelta(days=30)),
                           status="assigned", is_promotion_gate=parse_bool(form.get("is_promotion_gate")))
    db.add(a)
    db.flush()
    log_action(db, user, "assign", "teacher_dev", entity=a, description=f"Training '{title}' assigned to {t.full_name}", request=request)
    if t.user_id:
        notify(db, t.user_id, "New training assigned", f"{title} — due {a.due_date}." + (" This is a promotion gate." if a.is_promotion_gate else ""),
               event_type="teacher_dev", link="/teacher/training")
    db.commit()
    return redirect("/hr/teacher-development?tab=training", "Training assigned.")


@router.post("/teacher-development/training/{id}/status", include_in_schema=False)
async def training_status(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("teacher_dev.update"))):
    a = db.query(TrainingAssignment).get(id)
    if not a:
        raise HTTPException(404, "Training assignment not found")
    form = await request.form()
    before = {"status": a.status, "score": a.score}
    a.status = form.get("status") or a.status
    if form.get("score"):
        a.score = parse_float(form.get("score"), 0)
    if a.status in ("completed", "failed"):
        a.completed_at = datetime.utcnow()
    log_action(db, user, "update", "teacher_dev", entity=a, description=f"Training '{a.title}' -> {a.status} for {a.teacher.full_name}",
               before=before, after={"status": a.status, "score": a.score}, request=request)
    db.commit()
    return redirect(form.get("next") or "/hr/teacher-development?tab=training", f"Training marked {a.status}.")


# ============================================================================== PROVISIONING (Module 49)
@router.get("/provisioning", include_in_schema=False)
def provisioning(request: Request, tab: str = "board", db: Session = Depends(get_db), user: User = Depends(require("provisioning.view"))):
    tabs = [("board", "Recent provisioning", "/hr/provisioning?tab=board"), ("policy", "Policy & MFA", "/hr/provisioning?tab=policy"),
            ("checklist", "Offboarding checklist", "/hr/provisioning?tab=checklist"), ("audit", "Access audit", "/hr/provisioning?tab=audit")]
    records = db.query(ProvisioningRecord).order_by(ProvisioningRecord.id.desc()).limit(60).all()
    employees = db.query(Employee).order_by(Employee.full_name).all()
    provisioned = {r.employee_id for r in db.query(ProvisioningRecord.employee_id).filter(ProvisioningRecord.action == "onboard")}
    stats = {"records": db.query(func.count(ProvisioningRecord.id)).scalar() or 0,
             "onboards": db.query(func.count(ProvisioningRecord.id)).filter(ProvisioningRecord.action == "onboard").scalar() or 0,
             "offboards": db.query(func.count(ProvisioningRecord.id)).filter(ProvisioningRecord.action == "offboard").scalar() or 0,
             "not_provisioned": sum(1 for e in employees if e.status in ("active", "probation") and e.id not in provisioned)}
    ctx: dict = {"user": user, "tab": tab, "tabs": tabs, "records": records, "stats": stats, "policy": PROVISIONING_POLICY,
                 "employee_options": _employee_options(db, only_active=False)}
    if tab == "policy":
        rows = []
        for e in employees:
            if e.status not in ("active", "probation", "on_leave"):
                continue
            u = e.user
            rows.append({"employee": e, "user": u, "mfa_enforced": e.mfa_enforced,
                         "two_factor": bool(u and u.two_factor_enabled), "device": e.device_name})
        ctx["mfa_rows"] = rows
        ctx["mfa_ok"] = sum(1 for r in rows if r["two_factor"])
    elif tab == "checklist":
        leavers = [e for e in employees if e.status in ("resigned", "terminated")]
        out = []
        for e in leavers:
            rec = db.query(ProvisioningRecord).filter(ProvisioningRecord.employee_id == e.id, ProvisioningRecord.action == "offboard")\
                .order_by(ProvisioningRecord.id.desc()).first()
            out.append({"employee": e, "record": rec, "done": bool(rec),
                        "user_active": bool(e.user and e.user.is_active)})
        ctx["leavers"] = out
    elif tab == "audit":
        emp_user_ids = {e.user_id for e in employees if e.user_id}
        orphan_users = db.query(User).join(Role, User.role_id == Role.id)\
            .filter(User.is_active.is_(True), Role.portal == "admin", ~User.id.in_(emp_user_ids or {-1})).all()
        ctx["orphan_users"] = orphan_users
        ctx["no_user"] = [e for e in employees if e.status in ("active", "probation") and not e.user_id]
        ctx["active_after_exit"] = [e for e in employees if e.status in ("resigned", "terminated") and e.user and e.user.is_active]
    return render(request, "hr/provisioning.html", ctx)


@router.post("/provisioning/run", include_in_schema=False)
async def provisioning_run(request: Request, db: Session = Depends(get_db), user: User = Depends(require("provisioning.execute"))):
    form = await request.form()
    e = _emp(db, parse_int(form.get("employee_id"), 0) or 0)
    action = form.get("action") or "onboard"
    if action == "offboard" and not (form.get("rationale") or "").strip():
        return redirect("/hr/provisioning", "Offboarding requires a reason for the audit trail.", "error")
    try:
        rec = svc.provision(db, e, action, user, request=request)
    except ValueError as exc:
        return redirect("/hr/provisioning", str(exc), "error")
    if action == "offboard" and form.get("rationale"):
        e.exit_reason = form.get("rationale")
    db.commit()
    return redirect("/hr/provisioning", f"{action.title()} completed for {e.full_name} ({len(rec.items)} systems).",
                    "warning" if action == "offboard" else "success")
