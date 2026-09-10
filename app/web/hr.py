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
from app.services import hr as svc
from app.services import payroll as pay

router = APIRouter(prefix="/hr", dependencies=[Depends(csrf_protect)])

EMPLOYEE_TABS = [("overview", "Overview"), ("attendance", "Attendance"), ("leaves", "Leaves"), ("salary", "Salary"),
                 ("payslips", "Payslips"), ("violations", "Violations"), ("comp", "Bonuses & Advances"),
                 ("onboarding", "Onboarding"), ("provisioning", "Provisioning"), ("development", "Development"),
                 ("documents", "Documents"), ("audit", "Audit")]
ME_TABS = [("overview", "Overview"), ("attendance", "My attendance"), ("leaves", "My leaves"), ("payslips", "My payslips"),
           ("violations", "My record"), ("development", "My development"), ("grievance", "Raise a grievance")]
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
@router.get("/employees", include_in_schema=False)
def employees_list(request: Request, page: int = 1, q: str = "", status: str = "", department: str = "", shift: str = "",
                   db: Session = Depends(get_db), user: User = Depends(require("employees.view"))):
    query = db.query(Employee)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Employee.full_name.ilike(like), Employee.employee_code.ilike(like),
                                 Employee.email.ilike(like), Employee.designation.ilike(like)))
    if status:
        query = query.filter(Employee.status == status)
    if department:
        query = query.filter(Employee.department_id == parse_int(department))
    if shift:
        query = query.filter(Employee.shift == shift)
    pg = paginate(query.order_by(Employee.employee_code), page, 25)
    kpis = svc.hr_kpis(db)
    today = date.today()
    unverified = db.query(func.count(Teacher.id)).filter(Teacher.is_verified.is_(False)).scalar() or 0
    stats = {"headcount": kpis["headcount"], "probation": db.query(func.count(Employee.id)).filter(Employee.status == "probation").scalar() or 0,
             "on_leave": kpis["on_leave_today"], "unverified": unverified, "departments": len(kpis["by_department"]),
             "by_department": kpis["by_department"]}
    base = f"/hr/employees?q={q}&status={status}&department={department}&shift={shift}"
    return render(request, "hr/employees_list.html", {"user": user, "page": pg, "q": q, "status": status, "department": department,
                                                      "shift": shift, "stats": stats, "statuses": EMP_STATUSES, "shifts": SHIFTS,
                                                      "departments": _departments(db), "base_url": base,
                                                      "show_sensitive": can_see_sensitive(user)})


@router.get("/employees/export.csv", include_in_schema=False)
def employees_export(db: Session = Depends(get_db), user: User = Depends(require("employees.view"))):
    rows = []
    for e in db.query(Employee).order_by(Employee.employee_code):
        rows.append([e.employee_code, e.full_name, e.designation, e.department.name if e.department else "", e.employment_type,
                     e.shift, e.status, e.join_date, e.probation_end or "", e.salary_band or "", float(e.base_salary or 0),
                     e.background_check_status, e.device_name or ""])
    return _csv(rows, ["Code", "Name", "Designation", "Department", "Type", "Shift", "Status", "Joined", "Probation end",
                       "Band", "Base salary", "Background check", "Device"], "employees.csv")


@router.get("/employees/new", include_in_schema=False)
def employee_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.add"))):
    return render(request, "hr/employee_form.html", {"user": user, "mode": "new", "e": None, "departments": _departments(db),
                                                     "branches": db.query(Branch).all(), "managers": _employee_options(db),
                                                     "statuses": EMP_STATUSES, "types": EMPLOYMENT_TYPES, "shifts": SHIFTS})


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
    return render(request, "hr/employee_form.html", {"user": user, "mode": "edit", "e": e, "departments": _departments(db),
                                                     "branches": db.query(Branch).all(),
                                                     "managers": [o for o in _employee_options(db) if o[0] != e.id],
                                                     "statuses": EMP_STATUSES, "types": EMPLOYMENT_TYPES, "shifts": SHIFTS})


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
def me(request: Request, tab: str = "overview", month: str = "", db: Session = Depends(get_db),
       ctx: UserContext = Depends(get_user_context)):
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
    data.update({"summary": svc.attendance_summary(db, e.id, start, end), "grid": sorted(grid.items(), reverse=True),
                 "today_rows": today_rows, "kpis": svc.employee_kpis(db, e, month),
                 "leaves": db.query(Leave).filter(Leave.person_type == "employee", Leave.employee_id == e.id).order_by(Leave.start_date.desc()).limit(30).all(),
                 "balance": svc.leave_balance(db, e),
                 "payslips": db.query(Payslip).filter(Payslip.employee_id == e.id).order_by(Payslip.id.desc()).limit(12).all(),
                 "violations": db.query(Violation).filter(Violation.employee_id == e.id).order_by(Violation.date.desc()).all(),
                 "plan": db.query(DevelopmentPlan).filter(DevelopmentPlan.employee_id == e.id).order_by(DevelopmentPlan.id.desc()).first(),
                 "trainings": (db.query(TrainingAssignment).filter(TrainingAssignment.teacher_id == e.teacher.id)
                               .order_by(TrainingAssignment.id.desc()).all() if e.teacher else []),
                 "my_grievances": db.query(Grievance).filter(Grievance.submitted_by_id == user.id).order_by(Grievance.id.desc()).all()})
    return render(request, "hr/me.html", data)


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


# ============================================================================== ATTENDANCE
@router.get("/attendance", include_in_schema=False)
def attendance_board(request: Request, tab: str = "today", day: str = "", month: str = "", employee_id: int = 0,
                     db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.view"))):
    d = parse_date(day) or date.today()
    month = _month_param(month)
    start, end = month_bounds(month)
    tabs = [("today", "Today's board", "/hr/attendance?tab=today"), ("monthly", "Monthly grid", "/hr/attendance?tab=monthly"),
            ("corrections", "Corrections", "/hr/attendance?tab=corrections"), ("late", "Late report", "/hr/attendance?tab=late")]
    ctx: dict = {"user": user, "tab": tab, "tabs": tabs, "day": d, "month": month, "employee_id": employee_id,
                 "employees": _employee_options(db), "statuses": ["present", "late", "absent", "leave", "half_day", "holiday"]}
    employees = db.query(Employee).filter(Employee.status.in_(["active", "probation", "on_leave"])).order_by(Employee.full_name).all()
    if tab == "today":
        rows = db.query(HRAttendance).filter(HRAttendance.date == d).all()
        by_emp: dict = {}
        for r in rows:
            by_emp.setdefault(r.employee_id, {})[r.session] = r
        board = [{"employee": e, "am": by_emp.get(e.id, {}).get("am"), "pm": by_emp.get(e.id, {}).get("pm")} for e in employees]
        counts = {"present": 0, "late": 0, "absent": 0, "leave": 0, "missing": 0}
        for b in board:
            for s in ("am", "pm"):
                r = b[s]
                if r is None:
                    counts["missing"] += 1
                else:
                    counts[r.status] = counts.get(r.status, 0) + 1
        ctx["board"] = board
        ctx["counts"] = counts
    elif tab == "monthly":
        emp = db.query(Employee).get(employee_id) if employee_id else (employees[0] if employees else None)
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
                     r.late_minutes or 0, r.correction_status or ""])
    return _csv(rows, ["Date", "Code", "Employee", "Session", "Status", "Check in", "Check out", "Late minutes", "Correction"],
                f"attendance-{month}.csv")


@router.post("/attendance/mark-absent", include_in_schema=False)
async def attendance_mark_absent(request: Request, db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.update"))):
    form = await request.form()
    d = parse_date(form.get("day")) or date.today()
    n = svc.mark_absent_for_missing(db, d, user)
    db.commit()
    return redirect(f"/hr/attendance?tab=today&day={d}", f"{n} missing session(s) closed off for {d}.", "warning" if n else "info")


@router.post("/attendance/set", include_in_schema=False)
async def attendance_set(request: Request, db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.update"))):
    form = await request.form()
    e = _emp(db, parse_int(form.get("employee_id"), 0) or 0)
    d = parse_date(form.get("day")) or date.today()
    svc.set_attendance_status(db, e, d, form.get("session") or "am", form.get("status") or "present", user,
                              note=form.get("note"), request=request)
    db.commit()
    return redirect(form.get("next") or f"/hr/attendance?tab=today&day={d}", f"{e.full_name} marked {form.get('status')} for {d}.")


@router.post("/attendance/corrections/{id}", include_in_schema=False)
async def attendance_correction_decide(id: int, request: Request, db: Session = Depends(get_db),
                                       user: User = Depends(require("hr_attendance.approve"))):
    row = db.query(HRAttendance).get(id)
    if not row:
        raise HTTPException(404, "Attendance record not found")
    form = await request.form()
    approve = form.get("decision") == "approve"
    svc.decide_correction(db, row, approve, user, new_status=form.get("new_status"), note=form.get("note"), request=request)
    db.commit()
    return redirect("/hr/attendance?tab=corrections", f"Correction {'approved' if approve else 'rejected'}.")


# ============================================================================== LEAVES
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
    base = f"/hr/leaves?status={status}&leave_type={leave_type}&q={q}"
    return render(request, "hr/leaves.html", {"user": user, "page": pg, "status": status, "leave_type": leave_type, "q": q,
                                              "counts": counts, "upcoming": upcoming, "on_leave_today": on_leave_today,
                                              "leave_types": LEAVE_TYPES, "statuses": ["pending", "approved", "rejected", "cancelled"],
                                              "base_url": base, "leave_days": svc.leave_days, "today_date": today})


@router.get("/leaves/new", include_in_schema=False)
def leave_new(request: Request, employee_id: int = 0, db: Session = Depends(get_db), user: User = Depends(require("leaves.add"))):
    return render(request, "hr/leave_form.html", {"user": user, "employees": _employee_options(db), "leave_types": LEAVE_TYPES,
                                                  "employee_id": employee_id})


@router.post("/leaves/new", include_in_schema=False)
async def leave_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.add"))):
    form = await request.form()
    e = _emp(db, parse_int(form.get("employee_id"), 0) or 0)
    start = parse_date(form.get("start_date"))
    end = parse_date(form.get("end_date")) or start
    if not start:
        return redirect("/hr/leaves/new", "A start date is required.", "error")
    sub = db.query(Teacher).get(parse_int(form.get("substitute_teacher_id"), 0) or 0) if form.get("substitute_teacher_id") else None
    try:
        l = svc.request_leave(db, e, form.get("leave_type") or "casual", start, end, form.get("reason"), user,
                              substitute_teacher=sub, request=request)
    except ValueError as exc:
        return redirect("/hr/leaves/new", str(exc), "error")
    db.commit()
    return redirect(f"/hr/leaves/{l.id}", "Leave request created.")


@router.get("/leaves/{id}", include_in_schema=False)
def leave_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.view"))):
    l = db.query(Leave).get(id)
    if not l or l.person_type != "employee":
        raise HTTPException(404, "Leave not found")
    e = l.employee
    subs = []
    if e and e.teacher:
        subs = svc.substitute_suggestions(db, e.teacher, l.start_date, l.end_date)
    return render(request, "hr/leave_detail.html", {"user": user, "l": l, "e": e, "subs": subs,
                                                    "balance": svc.leave_balance(db, e) if e else {},
                                                    "days": svc.leave_days(l),
                                                    "history": db.query(Leave).filter(Leave.person_type == "employee",
                                                                                      Leave.employee_id == l.employee_id,
                                                                                      Leave.id != l.id)
                                                    .order_by(Leave.start_date.desc()).limit(10).all()})


@router.post("/leaves/{id}/decide", include_in_schema=False)
async def leave_decide(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.approve"))):
    l = db.query(Leave).get(id)
    if not l or l.person_type != "employee":
        raise HTTPException(404, "Leave not found")
    form = await request.form()
    approve = form.get("decision") == "approve"
    sub_id = parse_int(form.get("substitute_teacher_id"), 0)
    if sub_id:
        l.substitute_teacher_id = sub_id
    svc.decide_leave(db, l, approve, user, note=form.get("note"), request=request)
    db.commit()
    return redirect(f"/hr/leaves/{l.id}", f"Leave {'approved' if approve else 'rejected'}.")


# ============================================================================== RECRUITMENT
@router.get("/recruitment", include_in_schema=False)
def recruitment(request: Request, tab: str = "requests", status: str = "", db: Session = Depends(get_db),
                user: User = Depends(require("recruitment.view"))):
    tabs = [("requests", "Requests", "/hr/recruitment?tab=requests"), ("pipeline", "Candidate pipeline", "/hr/recruitment?tab=pipeline"),
            ("sourcing", "Sourcing report", "/hr/recruitment?tab=sourcing")]
    reqs = db.query(RecruitmentRequest).order_by(RecruitmentRequest.created_at.desc()).all()
    kpis = svc.hr_kpis(db)
    candidates = db.query(Candidate).order_by(Candidate.created_at.desc()).all()
    by_stage = {s: [c for c in candidates if c.stage == s] for s in STAGES}
    sourcing: dict = {}
    for c in candidates:
        src = c.source or "Unknown"
        row = sourcing.setdefault(src, {"source": src, "total": 0, "hired": 0, "rejected": 0, "in_process": 0})
        row["total"] += 1
        if c.stage == "hired":
            row["hired"] += 1
        elif c.stage == "rejected":
            row["rejected"] += 1
        else:
            row["in_process"] += 1
    for row in sourcing.values():
        row["conversion"] = round(100 * row["hired"] / row["total"], 1) if row["total"] else 0.0
    stats = {"open": sum(1 for r in reqs if r.status in ("open", "approved", "in_progress")), "requests": len(reqs),
             "candidates": len(candidates), "hired": len(by_stage["hired"]), "time_to_hire": kpis["hiring_days"],
             "positions": sum(r.positions for r in reqs if r.status in ("open", "approved", "in_progress"))}
    return render(request, "hr/recruitment.html", {"user": user, "tab": tab, "tabs": tabs, "requests": reqs, "stats": stats,
                                                   "by_stage": by_stage, "stages": STAGES,
                                                   "sourcing": sorted(sourcing.values(), key=lambda r: -r["total"]),
                                                   "departments": _departments(db)})


@router.post("/recruitment/new", include_in_schema=False)
async def recruitment_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("recruitment.add"))):
    form = await request.form()
    title = (form.get("title") or "").strip()
    if not title:
        return redirect("/hr/recruitment", "A job title is required.", "error")
    r = RecruitmentRequest(title=title, department_id=parse_int(form.get("department_id")), requested_by_id=user.id,
                           positions=parse_int(form.get("positions"), 1) or 1, description=form.get("description"),
                           status="open", target_date=parse_date(form.get("target_date")))
    db.add(r)
    db.flush()
    log_action(db, user, "create", "recruitment", entity=r, description=f"Recruitment request '{title}' ({r.positions} position(s))", request=request)
    for u in svc.hr_notify_users(db):
        notify(db, u, "Recruitment request raised", f"{title} — {r.positions} position(s).", event_type="recruitment", link=f"/hr/recruitment/{r.id}")
    db.commit()
    return redirect(f"/hr/recruitment/{r.id}", "Recruitment request created.")


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
    c = db.query(Candidate).get(id)
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
    c = db.query(Candidate).get(id)
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
    c = db.query(Candidate).get(id)
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
    iv = db.query(Interview).get(id)
    if not iv:
        raise HTTPException(404, "Interview not found")
    form = await request.form()
    iv.score = parse_float(form.get("score"), 0)
    iv.feedback = form.get("feedback")
    iv.status = form.get("status") or "completed"
    scores = [i.score for i in db.query(Interview).filter(Interview.candidate_id == iv.candidate_id, Interview.score.isnot(None))]
    if scores:
        iv.candidate.score = round(sum(scores) / len(scores), 1)
    log_action(db, user, "update", "recruitment", entity=iv, description=f"Interview feedback recorded for {iv.candidate.full_name} (score {iv.score})",
               rationale=iv.feedback, request=request)
    db.commit()
    return redirect(f"/hr/recruitment/candidates/{iv.candidate_id}", "Interview feedback saved.")


@router.post("/recruitment/candidates/{id}/hire", include_in_schema=False)
async def candidate_hire(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("employees.add"))):
    c = db.query(Candidate).get(id)
    if not c:
        raise HTTPException(404, "Candidate not found")
    if c.hired_employee_id:
        return redirect(f"/hr/recruitment/candidates/{c.id}", "This candidate has already been hired.", "warning")
    form = await request.form()
    is_teacher = "teacher" in (c.applied_for or "").lower()
    data = {"full_name": c.full_name, "designation": form.get("designation") or c.applied_for,
            "department_id": form.get("department_id"), "gender": c.gender or "male", "email": c.email, "phone": c.phone,
            "join_date": parse_date(form.get("join_date")) or date.today(), "employment_type": form.get("employment_type") or "full_time",
            "shift": form.get("shift") or "evening", "base_salary": form.get("base_salary"), "is_teacher": is_teacher,
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
        log_action(db, user, "create", "teachers", entity=t, description=f"Teacher profile {t.teacher_code} created from candidate {c.full_name} (unverified)", request=request)
    c.hired_employee_id = emp.id
    c.stage = "hired"
    if c.request:
        filled = db.query(func.count(Candidate.id)).filter(Candidate.request_id == c.request_id, Candidate.stage == "hired").scalar() or 0
        c.request.status = "filled" if filled >= (c.request.positions or 1) else "in_progress"
    log_action(db, user, "hire", "recruitment", entity=c, description=f"{c.full_name} hired as {emp.designation} ({emp.employee_code})",
               after={"employee_code": emp.employee_code}, consequential=True, request=request)
    db.commit()
    msg = f"{c.full_name} hired as {emp.employee_code}."
    if pwd:
        msg += f" Temporary password: {pwd}"
    return redirect(f"/hr/employees/{emp.id}", msg)


@router.get("/recruitment/{id}", include_in_schema=False)
def recruitment_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("recruitment.view"))):
    r = db.query(RecruitmentRequest).get(id)
    if not r:
        raise HTTPException(404, "Recruitment request not found")
    return render(request, "hr/recruitment_detail.html", {"user": user, "r": r, "stages": STAGES,
                                                          "candidates": db.query(Candidate).filter(Candidate.request_id == r.id).order_by(Candidate.created_at.desc()).all(),
                                                          "departments": _departments(db)})


@router.post("/recruitment/{id}/approve", include_in_schema=False)
async def recruitment_approve(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("recruitment.approve"))):
    r = db.query(RecruitmentRequest).get(id)
    if not r:
        raise HTTPException(404, "Recruitment request not found")
    form = await request.form()
    before = {"status": r.status}
    decision = form.get("decision") or "approve"
    r.status = {"approve": "approved", "cancel": "cancelled", "close": "filled"}.get(decision, "approved")
    if decision == "approve":
        r.approved_by_id = user.id
    log_action(db, user, "approve" if decision == "approve" else "status_change", "recruitment", entity=r,
               description=f"Recruitment request '{r.title}' -> {r.status}", rationale=form.get("note"),
               before=before, after={"status": r.status}, request=request)
    if r.requested_by_id:
        notify(db, r.requested_by_id, f"Recruitment request {r.status}", f"'{r.title}' is now {r.status}.",
               event_type="recruitment", link=f"/hr/recruitment/{r.id}")
    db.commit()
    return redirect(f"/hr/recruitment/{r.id}", f"Request {r.status}.")


# ============================================================================== PAYROLL
@router.get("/payroll", include_in_schema=False)
def payroll_list(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.view"))):
    runs = db.query(PayrollRun).order_by(PayrollRun.period.desc()).all()
    counts = {r.id: len(r.payslips) for r in runs}
    last = date.today().replace(day=1) - timedelta(days=1)
    stats = {"runs": len(runs), "paid": sum(1 for r in runs if r.status == "paid"),
             "pending": sum(1 for r in runs if r.status in ("draft", "pending_approval")),
             "last_net": float(runs[0].total_net) if runs else 0.0,
             "advances": db.query(func.count(SalaryAdvance.id)).filter(SalaryAdvance.status == "pending").scalar() or 0,
             "bonuses": db.query(func.count(Bonus.id)).filter(Bonus.status == "pending").scalar() or 0}
    return render(request, "hr/payroll_list.html", {"user": user, "runs": runs, "counts": counts, "stats": stats,
                                                    "suggested_period": last.strftime("%Y-%m"),
                                                    "can_approve": pay.can_approve_payroll(user)})


@router.post("/payroll/generate", include_in_schema=False)
async def payroll_generate(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.add"))):
    form = await request.form()
    period = _month_param(form.get("period"))
    try:
        run = pay.generate_payroll(db, period, user, request=request)
    except ValueError as exc:
        return redirect("/hr/payroll", str(exc), "error")
    db.commit()
    return redirect(f"/hr/payroll/{run.id}", f"Draft payroll for {period} generated: {len(run.payslips)} payslip(s).")


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
    a = db.query(SalaryAdvance).get(id)
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
    b = db.query(Bonus).get(id)
    if not b:
        raise HTTPException(404, "Bonus not found")
    form = await request.form()
    svc.decide_bonus_or_advance(db, b, form.get("decision") == "approve", user, note=form.get("note"), request=request)
    db.commit()
    return redirect("/hr/payroll/bonuses", f"Bonus {b.status}.")


@router.post("/payroll/payslips/{id}/adjust", include_in_schema=False)
async def payslip_adjust(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.update"))):
    ps = db.query(Payslip).get(id)
    if not ps:
        raise HTTPException(404, "Payslip not found")
    form = await request.form()
    try:
        pay.adjust_payslip(db, ps, parse_float(form.get("amount"), 0), form.get("reason") or "", user, request=request)
    except ValueError as exc:
        return redirect(f"/hr/payroll/{ps.payroll_run_id}", str(exc), "error")
    db.commit()
    return redirect(f"/hr/payroll/{ps.payroll_run_id}", "Payslip adjusted.")


@router.get("/payroll/{id}", include_in_schema=False)
def payroll_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.view"))):
    run = db.query(PayrollRun).get(id)
    if not run:
        raise HTTPException(404, "Payroll run not found")
    slips = sorted(run.payslips, key=lambda p: (not (p.details or {}).get("is_teacher"), p.employee.full_name if p.employee else ""))
    return render(request, "hr/payroll_detail.html", {"user": user, "run": run, "slips": slips,
                                                      "can_approve": pay.can_approve_payroll(user),
                                                      "summary": pay.payroll_summary(db, run.period)})


@router.get("/payroll/{id}/export.csv", include_in_schema=False)
def payroll_export(id: int, db: Session = Depends(get_db), user: User = Depends(require("payroll.view"))):
    run = db.query(PayrollRun).get(id)
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


@router.post("/payroll/{id}/submit", include_in_schema=False)
async def payroll_submit(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.update"))):
    run = db.query(PayrollRun).get(id)
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
    run = db.query(PayrollRun).get(id)
    if not run:
        raise HTTPException(404, "Payroll run not found")
    form = await request.form()
    try:
        pay.approve_payroll(db, run, user, rationale=form.get("rationale") or "", request=request)
    except (ValueError, PermissionError) as exc:
        return redirect(f"/hr/payroll/{run.id}", str(exc), "error")
    db.commit()
    return redirect(f"/hr/payroll/{run.id}", f"Payroll {run.period} approved and posted to the ledger.")


@router.post("/payroll/{id}/pay", include_in_schema=False)
async def payroll_pay(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.execute"))):
    run = db.query(PayrollRun).get(id)
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
    ps = db.query(Payslip).get(id)
    if not ps:
        raise HTTPException(404, "Payslip not found")
    own = ctx.employee is not None and ps.employee_id == ctx.employee.id
    if not own and not rbac.has_permission(ctx.user, "payroll.view"):
        raise PermissionDenied("payroll.view")
    return render(request, "hr/payslip.html", {"user": ctx.user, "ps": ps, "own": own})


@router.get("/payslips/{id}/pdf", include_in_schema=False)
def payslip_download(id: int, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    ps = db.query(Payslip).get(id)
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


# ============================================================================== VIOLATIONS
@router.get("/violations", include_in_schema=False)
def violations_list(request: Request, page: int = 1, status: str = "", violation_type: str = "", severity: str = "", q: str = "",
                    db: Session = Depends(get_db), user: User = Depends(require("violations.view"))):
    query = db.query(Violation)
    if status:
        query = query.filter(Violation.status == status)
    if violation_type:
        query = query.filter(Violation.violation_type == violation_type)
    if severity:
        query = query.filter(Violation.severity == severity)
    if q:
        query = query.join(Employee, Violation.employee_id == Employee.id).filter(
            or_(Employee.full_name.ilike(f"%{q}%"), Employee.employee_code.ilike(f"%{q}%")))
    pg = paginate(query.order_by(Violation.date.desc(), Violation.id.desc()), page, 25)
    since = date.today() - timedelta(days=30)
    stats = {"open": db.query(func.count(Violation.id)).filter(Violation.status == "open").scalar() or 0,
             "month": db.query(func.count(Violation.id)).filter(Violation.date >= since).scalar() or 0,
             "critical": db.query(func.count(Violation.id)).filter(Violation.severity == "critical", Violation.status == "open").scalar() or 0,
             "deductions": float(db.query(func.coalesce(func.sum(Violation.deduction_amount), 0)).filter(Violation.date >= since).scalar() or 0)}
    base = f"/hr/violations?status={status}&violation_type={violation_type}&severity={severity}&q={q}"
    return render(request, "hr/violations.html", {"user": user, "page": pg, "status": status, "violation_type": violation_type,
                                                  "severity": severity, "q": q, "stats": stats, "types": VIOLATION_TYPES,
                                                  "severities": SEVERITIES, "statuses": ["open", "closed"], "base_url": base,
                                                  "employees": _employee_options(db),
                                                  "repeat": svc.repeat_offenders(db)})


@router.post("/violations/new", include_in_schema=False)
async def violation_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("violations.add"))):
    form = await request.form()
    e = _emp(db, parse_int(form.get("employee_id"), 0) or 0)
    deduction = parse_float(form.get("deduction_amount"), 0)
    v = svc.record_violation(db, e, form.get("violation_type") or "policy", form.get("severity") or "minor",
                             form.get("description"), user, action_taken=form.get("action_taken"), deduction_amount=deduction,
                             day=parse_date(form.get("date")) or date.today(), request=request)
    db.commit()
    return redirect(form.get("next") or "/hr/violations", f"Violation #{v.id} recorded for {e.full_name}.", "warning")


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
