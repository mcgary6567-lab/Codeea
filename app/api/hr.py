"""REST API: HR / People & Culture / Payroll.

Mounted at /api/v1/hr. Auth: Bearer token (POST /api/v1/auth/login) or X-API-Key.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.deps import get_user_context, require, UserContext, PermissionDenied
from app.core.utils import month_key, month_bounds
from app.database import get_db
from app.models.core import User
from app.models.people import (Employee, Teacher, HRAttendance, Leave, Violation, Grievance, Candidate,
                               PayrollRun, Payslip, SalaryStructure, TrainingAssignment)
from app.services import hr as svc
from app.services import payroll as pay

router = APIRouter(prefix="/hr", tags=["hr"])


# ------------------------------------------------------------------ schemas
class EmployeeOut(BaseModel):
    id: int
    employee_code: str
    full_name: str
    designation: str
    department: Optional[str] = None
    employment_type: str
    shift: str
    status: str
    is_teacher: bool
    join_date: date
    probation_end: Optional[date] = None
    salary_band: Optional[str] = None
    background_check_status: str
    device_name: Optional[str] = None


class EmployeeIn(BaseModel):
    full_name: str
    designation: str = "Staff"
    department_id: Optional[int] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    gender: str = "male"
    join_date: Optional[date] = None
    employment_type: str = "full_time"
    shift: str = "morning"
    base_salary: float = 0
    is_teacher: bool = False
    create_login: bool = False


class AttendanceOut(BaseModel):
    id: int
    employee_id: int
    date: date
    session: str
    status: str
    check_in: Optional[datetime] = None
    check_out: Optional[datetime] = None
    late_minutes: int
    correction_status: Optional[str] = None


class CheckIn(BaseModel):
    session: str = Field("am", pattern="^(am|pm)$")
    action: str = Field("in", pattern="^(in|out)$")


class LeaveOut(BaseModel):
    id: int
    employee_id: Optional[int] = None
    leave_type: str
    start_date: date
    end_date: date
    days: int
    status: str
    reason: Optional[str] = None
    substitute_teacher_id: Optional[int] = None


class LeaveIn(BaseModel):
    employee_id: Optional[int] = None
    leave_type: str = "casual"
    start_date: date
    end_date: date
    reason: Optional[str] = None


class DecisionIn(BaseModel):
    approve: bool = True
    note: Optional[str] = None


class PayslipOut(BaseModel):
    id: int
    employee_id: int
    employee: Optional[str] = None
    period: str
    basic: float
    class_pay: float
    classes_taught: int
    allowances: float
    bonus: float
    deductions: float
    attendance_deduction: float
    advance_deduction: float
    gross: float
    net: float
    currency: str
    status: str


class PayrollRunOut(BaseModel):
    id: int
    period: str
    status: str
    total_gross: float
    total_deductions: float
    total_net: float
    currency: str
    payslips: int


class GradeOut(BaseModel):
    teacher_id: int
    teacher_code: str
    full_name: str
    grade: str
    inputs: dict
    computed_at: Optional[datetime] = None


class ViolationOut(BaseModel):
    id: int
    employee_id: int
    violation_type: str
    severity: str
    status: str
    date: date
    deduction_amount: float
    description: Optional[str] = None


class TrainingOut(BaseModel):
    id: int
    teacher_id: int
    title: str
    category: str
    status: str
    due_date: Optional[date] = None
    score: Optional[float] = None
    is_promotion_gate: bool


# ------------------------------------------------------------------ helpers
def _employee_out(e: Employee) -> EmployeeOut:
    return EmployeeOut(id=e.id, employee_code=e.employee_code, full_name=e.full_name, designation=e.designation,
                       department=e.department.name if e.department else None, employment_type=e.employment_type,
                       shift=e.shift, status=e.status, is_teacher=e.is_teacher, join_date=e.join_date,
                       probation_end=e.probation_end, salary_band=e.salary_band,
                       background_check_status=e.background_check_status, device_name=e.device_name)


def _payslip_out(p: Payslip) -> PayslipOut:
    return PayslipOut(id=p.id, employee_id=p.employee_id, employee=p.employee.full_name if p.employee else None,
                      period=p.payroll_run.period, basic=float(p.basic), class_pay=float(p.class_pay),
                      classes_taught=p.classes_taught, allowances=float(p.allowances), bonus=float(p.bonus),
                      deductions=float(p.deductions), attendance_deduction=float(p.attendance_deduction),
                      advance_deduction=float(p.advance_deduction), gross=float(p.gross), net=float(p.net),
                      currency=p.currency, status=p.status)


def _leave_out(l: Leave) -> LeaveOut:
    return LeaveOut(id=l.id, employee_id=l.employee_id, leave_type=l.leave_type, start_date=l.start_date,
                    end_date=l.end_date, days=svc.leave_days(l), status=l.status, reason=l.reason,
                    substitute_teacher_id=l.substitute_teacher_id)


# ------------------------------------------------------------------ employees
@router.get("/employees", response_model=list[EmployeeOut])
def list_employees(status: str = "", department_id: int = 0, limit: int = 100, db: Session = Depends(get_db),
                   user: User = Depends(require("employees.view"))):
    q = db.query(Employee)
    if status:
        q = q.filter(Employee.status == status)
    if department_id:
        q = q.filter(Employee.department_id == department_id)
    return [_employee_out(e) for e in q.order_by(Employee.employee_code).limit(min(500, limit))]


@router.post("/employees", response_model=EmployeeOut, status_code=201)
def create_employee(body: EmployeeIn, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("employees.add"))):
    data = body.model_dump()
    create_login = data.pop("create_login", False)
    emp, _pwd = svc.create_employee(db, data, user, create_login=create_login, request=request)
    db.commit()
    return _employee_out(emp)


@router.get("/employees/{id}", response_model=EmployeeOut)
def get_employee(id: int, db: Session = Depends(get_db), user: User = Depends(require("employees.view"))):
    e = db.query(Employee).get(id)
    if not e:
        raise HTTPException(404, "Employee not found")
    return _employee_out(e)


@router.get("/employees/{id}/kpis")
def employee_kpis(id: int, period: str = "", db: Session = Depends(get_db), user: User = Depends(require("employees.view"))):
    e = db.query(Employee).get(id)
    if not e:
        raise HTTPException(404, "Employee not found")
    return svc.employee_kpis(db, e, period or month_key())


@router.get("/kpis")
def hr_kpis(period: str = "", db: Session = Depends(get_db), user: User = Depends(require("employees.view"))):
    return svc.hr_kpis(db, period or month_key())


# ------------------------------------------------------------------ attendance
@router.get("/attendance", response_model=list[AttendanceOut])
def list_attendance(employee_id: int = 0, day: Optional[date] = None, month: str = "", limit: int = 200,
                    db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.view"))):
    q = db.query(HRAttendance)
    if employee_id:
        q = q.filter(HRAttendance.employee_id == employee_id)
    if day:
        q = q.filter(HRAttendance.date == day)
    elif month:
        start, end = month_bounds(month)
        q = q.filter(HRAttendance.date >= start, HRAttendance.date <= end)
    rows = q.order_by(HRAttendance.date.desc(), HRAttendance.session).limit(min(1000, limit)).all()
    return [AttendanceOut(id=r.id, employee_id=r.employee_id, date=r.date, session=r.session, status=r.status,
                          check_in=r.check_in, check_out=r.check_out, late_minutes=r.late_minutes or 0,
                          correction_status=r.correction_status) for r in rows]


@router.post("/attendance/check", response_model=AttendanceOut)
def check(body: CheckIn, request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    if ctx.employee is None:
        raise HTTPException(400, "No employee record is linked to this account")
    r = svc.mark_attendance(db, ctx.employee, body.session, body.action, ctx.user, request=request)
    db.commit()
    return AttendanceOut(id=r.id, employee_id=r.employee_id, date=r.date, session=r.session, status=r.status,
                         check_in=r.check_in, check_out=r.check_out, late_minutes=r.late_minutes or 0,
                         correction_status=r.correction_status)


# ------------------------------------------------------------------ leaves
@router.get("/leaves", response_model=list[LeaveOut])
def list_leaves(status: str = "", limit: int = 100, db: Session = Depends(get_db), user: User = Depends(require("leaves.view"))):
    q = db.query(Leave).filter(Leave.person_type == "employee")
    if status:
        q = q.filter(Leave.status == status)
    return [_leave_out(l) for l in q.order_by(Leave.start_date.desc()).limit(min(500, limit))]


@router.post("/leaves", response_model=LeaveOut, status_code=201)
def create_leave(body: LeaveIn, request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    emp = db.query(Employee).get(body.employee_id) if body.employee_id else ctx.employee
    if emp is None:
        raise HTTPException(400, "No employee to raise this leave for")
    if emp.id != (ctx.employee.id if ctx.employee else -1) and not rbac.has_permission(ctx.user, "leaves.add"):
        raise PermissionDenied("leaves.add")
    try:
        l = svc.request_leave(db, emp, body.leave_type, body.start_date, body.end_date, body.reason, ctx.user, request=request)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return _leave_out(l)


@router.post("/leaves/{id}/decide", response_model=LeaveOut)
def decide_leave(id: int, body: DecisionIn, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require("leaves.approve"))):
    l = db.query(Leave).get(id)
    if not l or l.person_type != "employee":
        raise HTTPException(404, "Leave not found")
    svc.decide_leave(db, l, body.approve, user, note=body.note, request=request)
    db.commit()
    return _leave_out(l)


# ------------------------------------------------------------------ payroll
@router.get("/payroll/runs", response_model=list[PayrollRunOut])
def payroll_runs(db: Session = Depends(get_db), user: User = Depends(require("payroll.view"))):
    return [PayrollRunOut(id=r.id, period=r.period, status=r.status, total_gross=float(r.total_gross),
                          total_deductions=float(r.total_deductions), total_net=float(r.total_net),
                          currency=r.currency, payslips=len(r.payslips))
            for r in db.query(PayrollRun).order_by(PayrollRun.period.desc())]


@router.post("/payroll/runs", response_model=PayrollRunOut, status_code=201)
def generate_run(period: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payroll.add"))):
    try:
        run = pay.generate_payroll(db, period, user, request=request)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return PayrollRunOut(id=run.id, period=run.period, status=run.status, total_gross=float(run.total_gross),
                         total_deductions=float(run.total_deductions), total_net=float(run.total_net),
                         currency=run.currency, payslips=len(run.payslips))


@router.post("/payroll/runs/{id}/approve", response_model=PayrollRunOut)
def approve_run(id: int, request: Request, rationale: str = "", db: Session = Depends(get_db),
                user: User = Depends(require("payroll.approve"))):
    run = db.query(PayrollRun).get(id)
    if not run:
        raise HTTPException(404, "Payroll run not found")
    try:
        pay.approve_payroll(db, run, user, rationale=rationale, request=request)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return PayrollRunOut(id=run.id, period=run.period, status=run.status, total_gross=float(run.total_gross),
                         total_deductions=float(run.total_deductions), total_net=float(run.total_net),
                         currency=run.currency, payslips=len(run.payslips))


@router.get("/payroll/runs/{id}/payslips", response_model=list[PayslipOut])
def run_payslips(id: int, db: Session = Depends(get_db), user: User = Depends(require("payroll.view"))):
    run = db.query(PayrollRun).get(id)
    if not run:
        raise HTTPException(404, "Payroll run not found")
    return [_payslip_out(p) for p in run.payslips]


@router.get("/payslips/me", response_model=list[PayslipOut])
def my_payslips(db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context)):
    if ctx.employee is None:
        return []
    return [_payslip_out(p) for p in db.query(Payslip).filter(Payslip.employee_id == ctx.employee.id).order_by(Payslip.id.desc()).limit(24)]


@router.get("/payroll/teacher-cost")
def teacher_cost(period: str = "", db: Session = Depends(get_db), user: User = Depends(require("payroll.view"))):
    rows = pay.teacher_cost_output(db, period or month_key())
    return [{"teacher_id": r["teacher"].id, "teacher_code": r["teacher"].teacher_code, "name": r["teacher"].full_name,
             "grade": r["grade"], "salary": r["salary"], "class_pay": r["class_pay"], "classes": r["classes"],
             "students": r["students"], "revenue": r["revenue"], "cost": r["cost"], "ratio": r["ratio"]} for r in rows]


# ------------------------------------------------------------------ teacher development
@router.get("/grades", response_model=list[GradeOut])
def grades(db: Session = Depends(get_db), user: User = Depends(require("teacher_dev.view"))):
    return [GradeOut(teacher_id=t.id, teacher_code=t.teacher_code, full_name=t.full_name, grade=t.grade or "B",
                     inputs=t.grade_inputs or {}, computed_at=t.grade_computed_at)
            for t in db.query(Teacher).order_by(Teacher.teacher_code)]


@router.post("/grades/recompute")
def recompute_grades(request: Request, teacher_id: int = 0, db: Session = Depends(get_db),
                     user: User = Depends(require("teacher_dev.update"))):
    if teacher_id:
        t = db.query(Teacher).get(teacher_id)
        if not t:
            raise HTTPException(404, "Teacher not found")
        grade = pay.compute_teacher_grade(db, t, user, request=request)
        db.commit()
        return {"teacher_id": t.id, "grade": grade}
    result = pay.recompute_all_grades(db, user, request=request)
    db.commit()
    return result


@router.get("/training", response_model=list[TrainingOut])
def training(teacher_id: int = 0, status: str = "", db: Session = Depends(get_db),
             user: User = Depends(require("teacher_dev.view"))):
    q = db.query(TrainingAssignment)
    if teacher_id:
        q = q.filter(TrainingAssignment.teacher_id == teacher_id)
    if status:
        q = q.filter(TrainingAssignment.status == status)
    return [TrainingOut(id=a.id, teacher_id=a.teacher_id, title=a.title, category=a.category, status=a.status,
                        due_date=a.due_date, score=a.score, is_promotion_gate=a.is_promotion_gate)
            for a in q.order_by(TrainingAssignment.id.desc()).limit(300)]


# ------------------------------------------------------------------ violations & recruitment
@router.get("/violations", response_model=list[ViolationOut])
def violations(employee_id: int = 0, status: str = "", db: Session = Depends(get_db),
               user: User = Depends(require("violations.view"))):
    q = db.query(Violation)
    if employee_id:
        q = q.filter(Violation.employee_id == employee_id)
    if status:
        q = q.filter(Violation.status == status)
    return [ViolationOut(id=v.id, employee_id=v.employee_id, violation_type=v.violation_type, severity=v.severity,
                         status=v.status, date=v.date, deduction_amount=float(v.deduction_amount or 0),
                         description=v.description) for v in q.order_by(Violation.date.desc()).limit(300)]


@router.get("/recruitment/pipeline")
def pipeline(db: Session = Depends(get_db), user: User = Depends(require("recruitment.view"))):
    rows = db.query(Candidate.stage, func.count(Candidate.id)).group_by(Candidate.stage).all()
    return {"stages": {s: n for s, n in rows}, "total": sum(n for _, n in rows),
            "time_to_hire_days": svc.hr_kpis(db)["hiring_days"]}


@router.get("/grievances/summary")
def grievances_summary(db: Session = Depends(get_db), user: User = Depends(require("grievances.view"))):
    """Aggregate counts only — the confidential content is never exposed through the API."""
    if not (user.is_superuser or user.role_slug in ("hod_people", "super_admin", "hr_officer")):
        raise PermissionDenied("grievances.confidential")
    rows = db.query(Grievance.status, func.count(Grievance.id)).group_by(Grievance.status).all()
    return {"by_status": {s: n for s, n in rows},
            "anonymous": db.query(func.count(Grievance.id)).filter(Grievance.is_anonymous.is_(True)).scalar() or 0,
            "sla_days": svc.GRIEVANCE_SLA_DAYS}
