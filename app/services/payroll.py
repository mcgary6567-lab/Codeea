"""Payroll & teacher grading (Modules 21 / 46).

Public API (imported lazily by other modules):
    compute_teacher_grade(db, teacher)      -> str          Module 46 grading engine
    generate_payroll(db, period, user)      -> PayrollRun    idempotent draft generation
    submit_payroll / approve_payroll / mark_paid
    adjust_payslip(db, payslip, data, user, reason)
    payslip_pdf(db, payslip)                -> str (path)
    teacher_cost_output(db, period)         -> list[dict]
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.core.audit import log_action
from app.core.notify import notify
from app.core.utils import month_bounds, next_code
from app.models.core import User, Setting
from app.models.people import (Employee, Teacher, Student, HRAttendance, Leave, Violation, SalaryStructure,
                               SalaryAdvance, Bonus, PayrollRun, Payslip)

log = logging.getLogger("oqc.payroll")

PAYSLIP_DIR = BASE_DIR / "storage" / "payslips"
DEFAULT_GRADE_RULES = {"A": {"qa": 85, "punctuality": 95, "retention": 90}, "B": {"qa": 70, "punctuality": 85, "retention": 75}}
DEFAULT_BANDS = {"A": 45000, "B": 35000, "C": 27000}
APPROVER_ROLES = {"hod_finance", "hod_people", "super_admin"}
GRADE_WINDOW_DAYS = 90


def _setting(db: Session, key: str, default):
    s = db.query(Setting).filter(Setting.key == key).first()
    return s.value if s and s.value is not None else default


def grade_rules(db: Session) -> dict:
    rules = _setting(db, "teacher_grade_rules", DEFAULT_GRADE_RULES) or DEFAULT_GRADE_RULES
    return {k: {"qa": float(v.get("qa", 0)), "punctuality": float(v.get("punctuality", 0)), "retention": float(v.get("retention", 0))}
            for k, v in rules.items() if isinstance(v, dict)}


def salary_bands(db: Session) -> dict:
    bands = _setting(db, "salary_bands", DEFAULT_BANDS) or DEFAULT_BANDS
    return {k: float(v) for k, v in bands.items()}


def can_approve_payroll(user: Optional[User]) -> bool:
    return bool(user and (user.is_superuser or user.role_slug in APPROVER_ROLES))


# =============================================================================== Module 46: teacher grading
def grade_inputs_for(db: Session, teacher: Teacher) -> dict:
    """QA average, punctuality and retention over the last 90 days, with sensible fallbacks."""
    since = date.today() - timedelta(days=GRADE_WINDOW_DAYS)
    qa = None
    qa_n = 0
    try:
        from app.models.scheduling import QAReview
        rows = db.query(QAReview.overall_score).filter(QAReview.teacher_id == teacher.id,
                                                       QAReview.status.in_(["completed", "approved"]),
                                                       QAReview.overall_score.isnot(None),
                                                       QAReview.created_at >= datetime.combine(since, datetime.min.time())).all()
        scores = [float(s) for (s,) in rows if s is not None]
        qa_n = len(scores)
        if scores:
            qa = round(sum(scores) / len(scores), 1)
    except Exception:  # pragma: no cover - QA module may be absent
        qa = None
    if qa is None:
        qa = round(float(teacher.qa_score_avg or 0), 1)

    punctuality = None
    classes_done = 0
    try:
        from app.services.classes import teacher_stats
        st = teacher_stats(db, teacher.id, since=since)
        classes_done = st["done"]
        if st["done"]:
            punctuality = float(st["punctuality_rate"])
    except Exception:  # pragma: no cover
        punctuality = None
    if punctuality is None:
        punctuality = round(float(teacher.punctuality_score or 100), 1)

    active = db.query(func.count(Student.id)).filter(Student.teacher_id == teacher.id, Student.status.in_(["active", "trial", "free"])).scalar() or 0
    lost = db.query(func.count(Student.id)).filter(Student.teacher_id == teacher.id, Student.status == "cancelled",
                                                   Student.cancelled_at.isnot(None), Student.cancelled_at >= since).scalar() or 0
    if active + lost:
        retention = round(100.0 * active / (active + lost), 1)
    else:
        retention = round(float(teacher.retention_rate or 100), 1)

    return {"qa": qa, "qa_reviews": qa_n, "punctuality": punctuality, "classes_done": classes_done,
            "retention": retention, "students_active": active, "students_lost_90d": lost,
            "window_days": GRADE_WINDOW_DAYS, "computed_on": date.today().isoformat()}


def _grade_from_inputs(rules: dict, inputs: dict) -> str:
    for g in ("A", "B"):
        r = rules.get(g)
        if not r:
            continue
        if inputs["qa"] >= r["qa"] and inputs["punctuality"] >= r["punctuality"] and inputs["retention"] >= r["retention"]:
            return g
    return "C"


def compute_teacher_grade(db: Session, teacher: Teacher, user: Optional[User] = None, request=None) -> str:
    """Recompute a teacher's grade from QA / punctuality / retention and align the salary band.

    Never terminates or deactivates anybody — a drop in grade only changes the band and raises an audit event.
    """
    inputs = grade_inputs_for(db, teacher)
    rules = grade_rules(db)
    new_grade = _grade_from_inputs(rules, inputs)
    old_grade = teacher.grade or "B"
    teacher.grade = new_grade
    teacher.grade_inputs = {**inputs, "rules": rules, "previous_grade": old_grade}
    teacher.grade_computed_at = datetime.utcnow()
    teacher.qa_score_avg = inputs["qa"]
    teacher.punctuality_score = inputs["punctuality"]
    teacher.retention_rate = inputs["retention"]
    rationale = (f"QA {inputs['qa']} ({inputs['qa_reviews']} review(s)), punctuality {inputs['punctuality']}% "
                 f"over {inputs['classes_done']} class(es), retention {inputs['retention']}% "
                 f"({inputs['students_active']} active / {inputs['students_lost_90d']} lost in {GRADE_WINDOW_DAYS} days)")

    emp = teacher.employee
    if new_grade != old_grade:
        log_action(db, user, "grade_change", "teacher_dev", entity=teacher,
                   description=f"Teacher {teacher.teacher_code} grade {old_grade} -> {new_grade}", rationale=rationale,
                   before={"grade": old_grade}, after={"grade": new_grade, "inputs": inputs}, consequential=True, request=request)
        if teacher.user_id:
            notify(db, teacher.user_id, f"Your teaching grade is now {new_grade}",
                   f"{rationale}. Speak to your supervisor about the Ustaadh Lab plan for the next cycle.",
                   event_type="teacher_dev", link="/teacher/training")

    bands = salary_bands(db)
    if emp is not None:
        old_band = emp.salary_band
        if old_band != new_grade:
            emp.salary_band = new_grade
            structure = db.query(SalaryStructure).filter(SalaryStructure.employee_id == emp.id).first()
            new_basic = bands.get(new_grade)
            if structure is not None and new_basic is not None:
                old_basic = float(structure.basic or 0)
                if abs(old_basic - new_basic) > 0.01:
                    structure.basic = new_basic
                    structure.effective_from = date.today()
                    emp.base_salary = new_basic
                    log_action(db, user, "salary_change", "payroll", entity=structure, entity_type="SalaryStructure",
                               description=f"Band {old_band or '-'} -> {new_grade}: basic {old_basic:,.0f} -> {new_basic:,.0f} for {emp.employee_code}",
                               rationale=rationale, before={"basic": old_basic, "band": old_band},
                               after={"basic": new_basic, "band": new_grade}, consequential=True, request=request)
    db.flush()
    return new_grade


def recompute_all_grades(db: Session, user: Optional[User] = None, request=None) -> dict:
    changed, total = 0, 0
    for t in db.query(Teacher).filter(Teacher.status != "inactive").order_by(Teacher.id):
        before = t.grade
        after = compute_teacher_grade(db, t, user, request=request)
        total += 1
        if before != after:
            changed += 1
    return {"teachers": total, "changed": changed}


def promote_teacher(db: Session, teacher: Teacher, grade: str, user: User, rationale: str, request=None) -> Teacher:
    """Manual promotion through a promotion gate (consequential, always rationale-backed)."""
    old = teacher.grade
    teacher.grade = grade
    teacher.grade_computed_at = datetime.utcnow()
    teacher.grade_inputs = {**(teacher.grade_inputs or {}), "manual_override": True, "previous_grade": old, "by": user.full_name}
    emp = teacher.employee
    bands = salary_bands(db)
    if emp is not None:
        emp.salary_band = grade
        s = db.query(SalaryStructure).filter(SalaryStructure.employee_id == emp.id).first()
        if s is not None and grade in bands:
            before_basic = float(s.basic or 0)
            s.basic = bands[grade]
            s.effective_from = date.today()
            emp.base_salary = bands[grade]
            log_action(db, user, "salary_change", "payroll", entity=s, entity_type="SalaryStructure",
                       description=f"Promotion: basic {before_basic:,.0f} -> {bands[grade]:,.0f} for {emp.employee_code}",
                       rationale=rationale, before={"basic": before_basic}, after={"basic": bands[grade]}, consequential=True, request=request)
    log_action(db, user, "grade_change", "teacher_dev", entity=teacher, description=f"Teacher {teacher.teacher_code} promoted {old} -> {grade}",
               rationale=rationale, before={"grade": old}, after={"grade": grade}, consequential=True, request=request)
    if teacher.user_id:
        notify(db, teacher.user_id, f"Promotion to grade {grade}", rationale, event_type="teacher_dev", link="/teacher/training")
    db.flush()
    return teacher


# =============================================================================== payroll generation
def _f(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def attendance_deductions(db: Session, emp: Employee, structure: SalaryStructure, start: date, end: date) -> dict:
    rows = db.query(HRAttendance).filter(HRAttendance.employee_id == emp.id, HRAttendance.date >= start, HRAttendance.date <= end).all()
    per_day: dict[date, list[str]] = {}
    late_count = 0
    for r in rows:
        per_day.setdefault(r.date, []).append(r.status)
        if r.status == "late":
            late_count += 1
    absent_days = 0.0
    for day, statuses in per_day.items():
        n_absent = sum(1 for s in statuses if s == "absent")
        if not statuses:
            continue
        absent_days += n_absent / max(1, len(statuses))
    # leave days taken without an approved leave record (rejected requests that overlap the period)
    unapproved = db.query(Leave).filter(Leave.person_type == "employee", Leave.employee_id == emp.id, Leave.status == "rejected",
                                        Leave.start_date <= end, Leave.end_date >= start).all()
    unapproved_days = 0
    for l in unapproved:
        s = max(l.start_date, start)
        e = min(l.end_date, end)
        unapproved_days += max(0, (e - s).days + 1)
    day_rate = _f(structure.absence_deduction_per_day if structure else 0)
    late_rate = _f(structure.late_deduction_per_instance if structure else 0)
    total = round(absent_days * day_rate + unapproved_days * day_rate + late_count * late_rate, 2)
    return {"absent_days": round(absent_days, 2), "unapproved_leave_days": unapproved_days, "late_instances": late_count,
            "absence_rate": day_rate, "late_rate": late_rate, "total": total}


def _classes_done(db: Session, teacher_id: int, start: date, end: date) -> int:
    try:
        from app.models.scheduling import ClassSession
        return db.query(func.count(ClassSession.id)).filter(ClassSession.teacher_id == teacher_id, ClassSession.status == "done",
                                                            ClassSession.date >= start, ClassSession.date <= end).scalar() or 0
    except Exception:  # pragma: no cover - scheduling tables may be empty/absent
        return 0


def _release_advances(db: Session, run: PayrollRun) -> None:
    """Give back advance instalments booked by a draft run that is about to be replaced."""
    for ps in run.payslips:
        for entry in (ps.details or {}).get("advances", []):
            adv = db.query(SalaryAdvance).get(entry.get("id"))
            if adv is None:
                continue
            adv.remaining = _f(adv.remaining) + _f(entry.get("amount"))
            if adv.status == "settled" and _f(adv.remaining) > 0:
                adv.status = "approved"
    db.flush()


# A run may only be rebuilt while it is still open. Two vocabularies reach this table -- the original
# draft/pending_approval/approved/paid and the ERP's pending/generated/posted/cancelled -- so callers must
# ask this set rather than list the finished states themselves and fall behind when one is added.
REGENERABLE_STATUSES = ("draft", "pending_approval", "pending")


def generate_payroll(db: Session, period: str, user: Optional[User], request=None) -> PayrollRun:
    """Build (or rebuild) the open payroll run for ``period`` (YYYY-MM). Finished runs are never touched."""
    start, end = month_bounds(period)
    run = db.query(PayrollRun).filter(PayrollRun.period == period).order_by(PayrollRun.id.desc()).first()
    if run and run.status not in REGENERABLE_STATUSES:
        raise ValueError(f"Payroll for {period} is already {run.status} and cannot be regenerated")
    if run:
        _release_advances(db, run)
        for ps in list(run.payslips):
            db.delete(ps)
        db.flush()
    else:
        run = PayrollRun(period=period, status="draft", currency="PKR", generated_by_id=user.id if user else None)
        db.add(run)
        db.flush()
    run.status = "draft"
    run.generated_by_id = user.id if user else run.generated_by_id

    employees = db.query(Employee).filter(Employee.status.in_(["active", "probation", "on_leave"])).order_by(Employee.id).all()
    bands = salary_bands(db)
    total_gross = total_ded = total_net = 0.0
    for emp in employees:
        if emp.join_date and emp.join_date > end:
            continue
        if emp.exit_date and emp.exit_date < start:
            continue
        structure = db.query(SalaryStructure).filter(SalaryStructure.employee_id == emp.id).first()
        basic = _f(structure.basic if structure else emp.base_salary)
        teacher = emp.teacher
        if teacher is not None:
            band_basic = bands.get(emp.salary_band or teacher.grade or "B")
            if band_basic:
                basic = float(band_basic)
        allow_map = dict((structure.allowances if structure else None) or {})
        allowances = round(sum(_f(v) for v in allow_map.values()), 2)
        ded_map = dict((structure.deductions if structure else None) or {})
        other_deductions = round(sum(_f(v) for v in ded_map.values()), 2)

        classes = 0
        class_pay = 0.0
        if teacher is not None:
            rate = _f(structure.per_class_rate if structure and _f(structure.per_class_rate) else teacher.per_class_rate)
            classes = _classes_done(db, teacher.id, start, end)
            class_pay = round(classes * rate, 2)

        bonuses = db.query(Bonus).filter(Bonus.employee_id == emp.id, Bonus.status == "approved", Bonus.period == period).all()
        bonus_total = round(sum(_f(b.amount) for b in bonuses), 2)

        att = attendance_deductions(db, emp, structure, start, end)

        violations = db.query(Violation).filter(Violation.employee_id == emp.id, Violation.date >= start, Violation.date <= end).all()
        violation_deduction = round(sum(_f(v.deduction_amount) for v in violations), 2)
        other_deductions = round(other_deductions + violation_deduction, 2)

        advance_taken = 0.0
        advance_rows = []
        for adv in db.query(SalaryAdvance).filter(SalaryAdvance.employee_id == emp.id, SalaryAdvance.status.in_(["approved", "paid"]),
                                                  SalaryAdvance.remaining > 0).order_by(SalaryAdvance.id):
            instalment = round(_f(adv.amount) / max(1, adv.installments or 1), 2)
            take = min(instalment, _f(adv.remaining))
            if take <= 0:
                continue
            adv.remaining = round(_f(adv.remaining) - take, 2)
            if _f(adv.remaining) <= 0.01:
                adv.remaining = 0
                adv.status = "settled"
            advance_taken = round(advance_taken + take, 2)
            advance_rows.append({"id": adv.id, "amount": take, "of": _f(adv.amount)})

        gross = round(basic + class_pay + allowances + bonus_total, 2)
        deduction_total = round(other_deductions + att["total"] + advance_taken, 2)
        net = round(gross - deduction_total, 2)
        details = {
            "allowances": allow_map, "deductions": ded_map, "attendance": att, "advances": advance_rows,
            "bonuses": [{"id": b.id, "type": b.bonus_type, "amount": _f(b.amount)} for b in bonuses],
            "violations": [{"id": v.id, "type": v.violation_type, "amount": _f(v.deduction_amount)} for v in violations if _f(v.deduction_amount)],
            "designation": emp.designation, "department": emp.department.name if emp.department else None,
            "is_teacher": bool(teacher), "band": emp.salary_band, "period_start": start.isoformat(), "period_end": end.isoformat(),
        }
        ps = Payslip(payroll_run_id=run.id, employee_id=emp.id, basic=basic, class_pay=class_pay, classes_taught=classes,
                     allowances=allowances, bonus=bonus_total, deductions=other_deductions, advance_deduction=advance_taken,
                     attendance_deduction=att["total"], gross=gross, net=net, currency=emp.currency or "PKR",
                     details=details, status="draft")
        db.add(ps)
        total_gross += gross
        total_ded += deduction_total
        total_net += net
    run.total_gross = round(total_gross, 2)
    run.total_deductions = round(total_ded, 2)
    run.total_net = round(total_net, 2)
    db.flush()
    log_action(db, user, "generate", "payroll", entity=run, description=f"Payroll draft generated for {period}: {len(run.payslips)} payslip(s), net {run.total_net:,.0f} PKR",
               after={"period": period, "total_net": float(run.total_net), "payslips": len(run.payslips)}, request=request)
    return run


def recalc_run_totals(run: PayrollRun) -> None:
    run.total_gross = round(sum(_f(p.gross) for p in run.payslips), 2)
    run.total_net = round(sum(_f(p.net) for p in run.payslips), 2)
    run.total_deductions = round(sum(_f(p.deductions) + _f(p.advance_deduction) + _f(p.attendance_deduction) for p in run.payslips), 2)


def adjust_payslip(db: Session, ps: Payslip, amount: float, reason: str, user: User, request=None) -> Payslip:
    """Manual adjustment on a draft payslip. Positive amount = extra pay, negative = extra deduction."""
    if ps.payroll_run.status not in ("draft", "pending_approval"):
        raise ValueError("Only draft payroll runs can be adjusted")
    if not (reason or "").strip():
        raise ValueError("An adjustment reason is required")
    before = {"gross": _f(ps.gross), "net": _f(ps.net)}
    details = dict(ps.details or {})
    adjustments = list(details.get("adjustments", []))
    adjustments.append({"amount": round(float(amount), 2), "reason": reason.strip(), "by": user.full_name, "at": datetime.utcnow().isoformat()})
    details["adjustments"] = adjustments
    ps.details = details
    if amount >= 0:
        ps.bonus = round(_f(ps.bonus) + float(amount), 2)
        ps.gross = round(_f(ps.gross) + float(amount), 2)
    else:
        ps.deductions = round(_f(ps.deductions) + abs(float(amount)), 2)
    ps.net = round(_f(ps.gross) - _f(ps.deductions) - _f(ps.advance_deduction) - _f(ps.attendance_deduction), 2)
    recalc_run_totals(ps.payroll_run)
    log_action(db, user, "adjust", "payroll", entity=ps, description=f"Payslip adjusted by {float(amount):,.0f} for {ps.employee.employee_code}",
               rationale=reason, before=before, after={"gross": _f(ps.gross), "net": _f(ps.net)}, consequential=True, request=request)
    db.flush()
    return ps


def submit_payroll(db: Session, run: PayrollRun, user: User, request=None) -> PayrollRun:
    if run.status != "draft":
        raise ValueError(f"Run is {run.status}; only drafts can be submitted for approval")
    if not run.payslips:
        raise ValueError("This run has no payslips")
    run.status = "pending_approval"
    log_action(db, user, "submit", "payroll", entity=run, description=f"Payroll {run.period} submitted for approval ({run.total_net:,.0f} PKR net)", request=request)
    from app.services.hr import hr_notify_users
    approvers = {u.id for u in hr_notify_users(db)}
    from app.models.core import Role
    for u in db.query(User).join(Role, User.role_id == Role.id).filter(Role.slug == "hod_finance", User.is_active.is_(True)):
        approvers.add(u.id)
    for uid in approvers:
        notify(db, uid, "Payroll awaiting approval", f"Payroll for {run.period}: net {float(run.total_net):,.0f} PKR across {len(run.payslips)} payslip(s).",
               event_type="payroll", link=f"/hr/payroll/{run.id}")
    db.flush()
    return run


def approve_payroll(db: Session, run: PayrollRun, user: User, rationale: str = "", request=None) -> PayrollRun:
    if run.status not in ("draft", "pending_approval"):
        raise ValueError(f"Run is already {run.status}")
    if not can_approve_payroll(user):
        raise PermissionError("Only Finance / People heads or the CEO can approve payroll")
    before = {"status": run.status}
    run.status = "approved"
    run.approved_by_id = user.id
    run.approved_at = datetime.utcnow()
    for ps in run.payslips:
        ps.status = "approved"
    log_action(db, user, "payroll_approve", "payroll", entity=run,
               description=f"Payroll {run.period} approved: gross {float(run.total_gross):,.0f}, net {float(run.total_net):,.0f} PKR",
               rationale=rationale or f"Approved by {user.full_name}", before=before,
               after={"status": "approved", "total_net": float(run.total_net)}, consequential=True, severity="warning", request=request)
    _post_payroll_journal(db, run, user)
    db.flush()
    return run


def _post_payroll_journal(db: Session, run: PayrollRun, user: Optional[User]) -> None:
    """Debit teacher / staff salary expense, credit salaries payable. Silently skipped when accounting is unavailable."""
    try:
        from app.services.accounting import post_journal
    except Exception:  # pragma: no cover
        return
    teacher_net = round(sum(_f(p.net) for p in run.payslips if (p.details or {}).get("is_teacher")), 2)
    staff_net = round(sum(_f(p.net) for p in run.payslips if not (p.details or {}).get("is_teacher")), 2)
    total = round(teacher_net + staff_net, 2)
    if total <= 0:
        return
    lines = []
    if teacher_net > 0:
        lines.append(("5000", teacher_net, 0))
    if staff_net > 0:
        lines.append(("5100", staff_net, 0))
    lines.append(("2100", 0, total))
    start, end = month_bounds(run.period)
    for entry_date in (end, date.today()):  # fall back to today when the accounting period is already closed
        try:
            je = post_journal(db, f"Payroll {run.period}", lines, reference_type="payroll", reference_id=run.id,
                              currency=run.currency, entry_date=entry_date, user=user)
            run.notes = ((run.notes + "\n") if run.notes else "") + f"Journal entry {je.entry_number} posted ({entry_date})."
            return
        except Exception as exc:  # pragma: no cover - closed period, missing accounts, module absent
            log.warning("payroll journal not posted for %s on %s: %s", run.period, entry_date, exc)


def mark_paid(db: Session, run: PayrollRun, user: User, request=None) -> PayrollRun:
    if run.status != "approved":
        raise ValueError("Only an approved payroll run can be marked paid")
    run.status = "paid"
    run.paid_at = datetime.utcnow()
    for ps in run.payslips:
        ps.status = "paid"
        if ps.employee and ps.employee.user_id:
            notify(db, ps.employee.user_id, f"Salary paid — {run.period}",
                   f"Your net salary of {ps.currency} {_f(ps.net):,.0f} for {run.period} has been released.",
                   event_type="payroll", link=f"/hr/payslips/{ps.id}")
    post_run_payment_to_employee_ledgers(db, run, user)
    log_action(db, user, "pay", "payroll", entity=run, description=f"Payroll {run.period} marked paid ({float(run.total_net):,.0f} PKR)",
               after={"status": "paid"}, consequential=True, request=request)
    db.flush()
    return run


# =============================================================================== payslip PDF
def payslip_pdf(db: Session, ps: Payslip) -> str:
    """Render a payslip PDF into storage/payslips and return its path (relative to BASE_DIR)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    PAYSLIP_DIR.mkdir(parents=True, exist_ok=True)
    emp = ps.employee
    run = ps.payroll_run
    fname = f"payslip-{run.period}-{emp.employee_code}.pdf"
    path = PAYSLIP_DIR / fname
    width, height = A4
    c = canvas.Canvas(str(path), pagesize=A4)
    brand = colors.HexColor("#0d9488")
    c.setFillColor(brand)
    c.rect(0, height - 80, width, 80, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, height - 45, "Online Quran College")
    c.setFont("Helvetica", 10)
    c.drawString(40, height - 62, f"Payslip for {run.period}")
    c.drawRightString(width - 40, height - 45, emp.employee_code)
    c.drawRightString(width - 40, height - 62, f"Status: {ps.status.title()}")

    c.setFillColor(colors.black)
    y = height - 115
    c.setFont("Helvetica-Bold", 13)
    c.drawString(40, y, emp.full_name)
    c.setFont("Helvetica", 9)
    y -= 15
    c.setFillColor(colors.HexColor("#475569"))
    dept = emp.department.name if emp.department else "-"
    c.drawString(40, y, f"{emp.designation}  |  {dept}  |  Joined {emp.join_date}  |  Shift {emp.shift}")
    y -= 12
    det = ps.details or {}
    c.drawString(40, y, f"Period {det.get('period_start', '')} to {det.get('period_end', '')}  |  Bank/Cash payout in {ps.currency}")
    y -= 24

    def row(label, value, bold=False, color=colors.black):
        nonlocal y
        c.setFillColor(color)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10 if bold else 9.5)
        c.drawString(52, y, label)
        c.drawRightString(width - 52, y, f"{ps.currency} {value:,.2f}")
        y -= 15

    def section(title):
        nonlocal y
        c.setFillColor(colors.HexColor("#f1f5f9"))
        c.rect(40, y - 4, width - 80, 18, stroke=0, fill=1)
        c.setFillColor(colors.HexColor("#0f172a"))
        c.setFont("Helvetica-Bold", 10)
        c.drawString(52, y, title)
        y -= 22

    section("Earnings")
    row("Basic salary" + (f" (band {det.get('band')})" if det.get("band") else ""), _f(ps.basic))
    if _f(ps.class_pay):
        row(f"Class pay ({ps.classes_taught} classes delivered)", _f(ps.class_pay))
    for k, v in (det.get("allowances") or {}).items():
        row(f"Allowance — {str(k).replace('_', ' ').title()}", _f(v))
    if _f(ps.bonus):
        row("Bonus / adjustments", _f(ps.bonus))
    row("Gross pay", _f(ps.gross), bold=True)
    y -= 8

    section("Deductions")
    att = det.get("attendance") or {}
    for k, v in (det.get("deductions") or {}).items():
        row(f"{str(k).replace('_', ' ').title()}", _f(v), color=colors.HexColor("#be123c"))
    if _f(ps.attendance_deduction):
        row(f"Attendance ({att.get('absent_days', 0)} absent day(s), {att.get('late_instances', 0)} late)",
            _f(ps.attendance_deduction), color=colors.HexColor("#be123c"))
    if _f(ps.advance_deduction):
        row("Salary advance instalment", _f(ps.advance_deduction), color=colors.HexColor("#be123c"))
    total_ded = _f(ps.deductions) + _f(ps.attendance_deduction) + _f(ps.advance_deduction)
    row("Total deductions", total_ded, bold=True, color=colors.HexColor("#be123c"))
    y -= 10

    c.setFillColor(brand)
    c.rect(40, y - 8, width - 80, 30, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(52, y + 2, "NET PAY")
    c.drawRightString(width - 52, y + 2, f"{ps.currency} {_f(ps.net):,.2f}")
    y -= 40

    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica", 8)
    c.drawString(40, 60, "This payslip is generated by the Online Quran College Digital Operating System and is valid without a signature.")
    c.drawString(40, 48, f"Generated {datetime.utcnow().strftime('%d %b %Y %H:%M')} UTC. Queries: People & Culture (hr@oqc.local).")
    c.showPage()
    c.save()
    rel = os.path.relpath(path, BASE_DIR).replace("\\", "/")
    ps.pdf_path = rel
    db.flush()
    return rel


def generate_run_pdfs(db: Session, run: PayrollRun) -> int:
    n = 0
    for ps in run.payslips:
        try:
            payslip_pdf(db, ps)
            n += 1
        except Exception as exc:  # pragma: no cover
            log.warning("payslip pdf failed for %s: %s", ps.id, exc)
    return n


# =============================================================================== teacher cost vs output
def teacher_cost_output(db: Session, period: str) -> list[dict]:
    """Cost-to-output table: what each teacher costs versus the revenue their students generate."""
    start, end = month_bounds(period)
    run = db.query(PayrollRun).filter(PayrollRun.period == period).order_by(PayrollRun.id.desc()).first()
    slips = {p.employee_id: p for p in run.payslips} if run else {}
    bands = salary_bands(db)
    try:
        from app.models.finance import Subscription
        subs_ok = True
    except Exception:  # pragma: no cover
        subs_ok = False
    out = []
    for t in db.query(Teacher).order_by(Teacher.teacher_code):
        emp = t.employee
        ps = slips.get(emp.id) if emp else None
        structure = db.query(SalaryStructure).filter(SalaryStructure.employee_id == emp.id).first() if emp else None
        salary = _f(ps.basic) if ps else (_f(structure.basic) if structure else float(bands.get(t.grade or "B", 0)))
        classes = ps.classes_taught if ps else _classes_done(db, t.id, start, end)
        rate = _f(structure.per_class_rate if structure and _f(structure.per_class_rate) else t.per_class_rate)
        class_pay = _f(ps.class_pay) if ps else round(classes * rate, 2)
        students = db.query(func.count(Student.id)).filter(Student.teacher_id == t.id, Student.status.in_(["active", "trial"])).scalar() or 0
        revenue = 0.0
        if subs_ok:
            try:
                revenue = _f(db.query(func.coalesce(func.sum(Subscription.price_in_base), 0))
                             .filter(Subscription.status == "active", Subscription.student_id.in_(
                                 db.query(Student.id).filter(Student.teacher_id == t.id))).scalar())
            except Exception:  # pragma: no cover
                revenue = 0.0
        cost = round(salary + class_pay, 2)
        out.append({"teacher": t, "employee": emp, "salary": salary, "class_pay": class_pay, "cost": cost,
                    "classes": classes, "students": students, "revenue": round(revenue, 2),
                    "ratio": round(100 * cost / revenue, 1) if revenue else None,
                    "margin": round(revenue - cost, 2), "grade": t.grade})
    out.sort(key=lambda r: (r["ratio"] is None, -(r["ratio"] or 0)))
    return out


def payroll_summary(db: Session, period: str) -> dict:
    run = db.query(PayrollRun).filter(PayrollRun.period == period).order_by(PayrollRun.id.desc()).first()
    if not run:
        return {"run": None, "period": period, "payslips": 0, "gross": 0.0, "net": 0.0, "deductions": 0.0, "teachers": 0, "staff": 0}
    teachers = sum(1 for p in run.payslips if (p.details or {}).get("is_teacher"))
    return {"run": run, "period": period, "payslips": len(run.payslips), "gross": _f(run.total_gross), "net": _f(run.total_net),
            "deductions": _f(run.total_deductions), "teachers": teachers, "staff": len(run.payslips) - teachers}


# =============================================================================== ERP payroll (audit section 3, Financial Management)
# The college's ERP runs a payroll month as Pending -> Generated -> Posted, with Cancelled as the way out.
# These functions sit on top of the originals above (which keep the older draft / pending_approval /
# approved / paid words alive), so both vocabularies work against the same tables.
ERP_PAYROLL_STATUSES = ["pending", "generated", "posted", "cancelled"]
LEGACY_PAYROLL_STATUSES = ["draft", "pending_approval", "approved", "paid"]
ERP_STATUS_OF_LEGACY = {"draft": "pending", "pending_approval": "generated", "approved": "posted", "paid": "posted"}
LOCKED_PAYROLL_STATUSES = ("posted", "approved", "paid", "cancelled")


def erp_run_is_locked(run: PayrollRun) -> bool:
    """A posted (or cancelled) run is closed: no regeneration, no payslip adjustment."""
    return run.status in LOCKED_PAYROLL_STATUSES


def erp_status(run: PayrollRun) -> str:
    """The ERP word for a run however it was created."""
    return ERP_STATUS_OF_LEGACY.get(run.status, run.status)


def erp_create_run(db: Session, period: str, description: str, user: Optional[User], request=None) -> PayrollRun:
    """Create the Pending payroll for a month. One open run per month."""
    existing = db.query(PayrollRun).filter(PayrollRun.period == period).order_by(PayrollRun.id.desc()).first()
    if existing is not None and existing.status != "cancelled":
        raise ValueError(f"Payroll for {period} already exists ({erp_status(existing)})")
    run = PayrollRun(period=period, status="pending", description=(description or "").strip() or f"Payroll {period}",
                     currency="PKR", generated_by_id=user.id if user else None)
    db.add(run)
    db.flush()
    log_action(db, user, "create", "payroll", entity=run,
               description=f"Payroll run created for {period}: {run.description}",
               after={"period": period, "status": run.status, "description": run.description}, request=request)
    return run


def _erp_reconcile_payslip(db: Session, ps: Payslip, period: str) -> None:
    """Keep only approved violations as deductions, and pull in bonuses approved inside the month.

    ``generate_payroll`` deducts every violation dated in the month and adds bonuses tagged with the month.
    The ERP only fines an employee once the violation has been approved, and pays a bonus in the month it was
    accepted, so this narrows the one and widens the other.
    """
    start, end = month_bounds(period)
    details = dict(ps.details or {})

    kept: list[dict] = []
    dropped = 0.0
    for row in list(details.get("violations", [])):
        v = db.query(Violation).get(row.get("id")) if row.get("id") else None
        approved = v is not None and (v.approval_status == "approved"
                                      or (not v.approval_status and v.status in ("approved", "closed")))
        if approved:
            kept.append(row)
        else:
            dropped = round(dropped + _f(row.get("amount")), 2)
    if dropped:
        details["violations"] = kept
        ps.deductions = round(max(0.0, _f(ps.deductions) - dropped), 2)

    bonus_rows = list(details.get("bonuses", []))
    seen = {r.get("id") for r in bonus_rows}
    extra = 0.0
    for b in db.query(Bonus).filter(Bonus.employee_id == ps.employee_id, Bonus.status == "approved"):
        if b.id in seen or (b.period or "") == period:
            continue
        when = b.acceptance_date or (b.decided_at.date() if b.decided_at else None)
        if when is None and b.updated_at:
            when = b.updated_at.date()
        if when is None or not (start <= when <= end):
            continue
        extra = round(extra + _f(b.amount), 2)
        bonus_rows.append({"id": b.id, "type": b.bonus_type, "amount": _f(b.amount)})
    if extra:
        details["bonuses"] = bonus_rows
        ps.bonus = round(_f(ps.bonus) + extra, 2)
        ps.gross = round(_f(ps.gross) + extra, 2)

    if dropped or extra:
        ps.details = details
        ps.net = round(_f(ps.gross) - _f(ps.deductions) - _f(ps.advance_deduction) - _f(ps.attendance_deduction), 2)


def erp_generate_run(db: Session, run: PayrollRun, user: Optional[User], request=None) -> PayrollRun:
    """Build the payslips for a Pending run: basic, class pay, approved bonuses and approved violations."""
    if erp_run_is_locked(run):
        raise ValueError(f"Payroll {run.period} is {erp_status(run)} and can no longer be generated")
    latest = db.query(PayrollRun).filter(PayrollRun.period == run.period).order_by(PayrollRun.id.desc()).first()
    if latest is not None and latest.id != run.id:
        raise ValueError(f"A later payroll run exists for {run.period}; generate that one instead")
    description = run.description
    run.status = "draft"  # the word generate_payroll understands
    db.flush()
    built = generate_payroll(db, run.period, user, request=request)
    built.description = description or built.description
    db.flush()
    db.expire(built, ["payslips"])  # generate_payroll adds payslips through the session, not the collection
    for ps in built.payslips:
        _erp_reconcile_payslip(db, ps, built.period)
        ps.status = "generated"
    recalc_run_totals(built)
    built.status = "generated"
    comp = erp_run_components(built)
    log_action(db, user, "generate", "payroll", entity=built,
               description=(f"Payroll {built.period} generated: {len(built.payslips)} payslip(s), class pay "
                            f"{comp['class_pay']:,.0f}, bonuses {comp['bonuses']:,.0f}, violations "
                            f"{comp['violations']:,.0f}, net {_f(built.total_net):,.0f} PKR"),
               after={"status": "generated", "payslips": len(built.payslips), "total_net": _f(built.total_net)},
               request=request)
    db.flush()
    return built


def erp_post_run(db: Session, run: PayrollRun, user: User, rationale: str = "", request=None) -> PayrollRun:
    """Post (lock) a generated run: it is approved, journalled and read-only from then on."""
    if run.status in ("posted", "cancelled"):
        raise ValueError(f"Payroll {run.period} is already {erp_status(run)}")
    if not run.payslips:
        raise ValueError("Generate the run before posting it")
    before = {"status": run.status}
    run.status = "draft"  # approve_payroll only accepts the legacy words
    approve_payroll(db, run, user, rationale=rationale or f"Payroll {run.period} posted by {user.full_name}", request=request)
    run.status = "posted"
    run.paid_at = run.paid_at or datetime.utcnow()
    for ps in run.payslips:
        ps.status = "posted"
        if ps.employee and ps.employee.user_id:
            notify(db, ps.employee.user_id, f"Payslip available - {run.period}",
                   f"Your payslip for {run.period} has been posted: net {ps.currency} {_f(ps.net):,.0f}.",
                   event_type="payroll", link=f"/hr/payslips/{ps.id}")
    log_action(db, user, "payroll_post", "payroll", entity=run,
               description=f"Payroll {run.period} posted and locked ({_f(run.total_net):,.0f} PKR net)",
               rationale=rationale or None, before=before, after={"status": "posted"}, consequential=True,
               severity="warning", request=request)
    db.flush()
    return run


def erp_cancel_run(db: Session, run: PayrollRun, user: User, reason: str = "", request=None) -> PayrollRun:
    if run.status in ("posted", "approved", "paid"):
        raise ValueError(f"Payroll {run.period} is {erp_status(run)} and cannot be cancelled")
    before = {"status": run.status}
    run.status = "cancelled"
    for ps in run.payslips:
        ps.status = "cancelled"
    log_action(db, user, "status_change", "payroll", entity=run, description=f"Payroll {run.period} cancelled",
               rationale=reason or None, before=before, after={"status": "cancelled"}, consequential=True, request=request)
    db.flush()
    return run


def erp_run_components(run: PayrollRun) -> dict:
    """What a generated run is made of: basic, class pay, allowances, bonuses, violations, other deductions."""
    out = {"basic": 0.0, "class_pay": 0.0, "classes": 0, "allowances": 0.0, "bonuses": 0.0, "violations": 0.0,
           "attendance": 0.0, "advances": 0.0, "other_deductions": 0.0, "gross": 0.0, "net": 0.0, "payslips": 0}
    for ps in run.payslips:
        det = ps.details or {}
        out["payslips"] += 1
        out["basic"] += _f(ps.basic)
        out["class_pay"] += _f(ps.class_pay)
        out["classes"] += int(ps.classes_taught or 0)
        out["allowances"] += _f(ps.allowances)
        out["bonuses"] += _f(ps.bonus)
        violations = round(sum(_f(v.get("amount")) for v in det.get("violations", [])), 2)
        out["violations"] += violations
        out["other_deductions"] += round(_f(ps.deductions) - violations, 2)
        out["attendance"] += _f(ps.attendance_deduction)
        out["advances"] += _f(ps.advance_deduction)
        out["gross"] += _f(ps.gross)
        out["net"] += _f(ps.net)
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in out.items()}


def erp_grade_allowances(db: Session, employee: Employee, grade, user: Optional[User] = None, request=None) -> SalaryStructure:
    """Assigning a grade fills the employee's salary structure with that grade's allowances and basic range."""
    structure = db.query(SalaryStructure).filter(SalaryStructure.employee_id == employee.id).first()
    if structure is None:
        structure = SalaryStructure(employee_id=employee.id, basic=0, allowances={}, deductions={},
                                    currency=employee.currency or "PKR")
        db.add(structure)
        db.flush()
    before = {"basic": _f(structure.basic), "allowances": dict(structure.allowances or {})}
    allowances = dict(structure.allowances or {})
    allowances.update({str(k): _f(v) for k, v in (grade.allowances or {}).items()})
    structure.allowances = allowances
    basic = _f(structure.basic) or _f(employee.base_salary)
    low, high = _f(grade.basic_min), _f(grade.basic_max)
    if low and basic < low:
        basic = low
    if high and basic > high:
        basic = high
    structure.basic = round(basic, 2)
    employee.grade_id = grade.id
    if _f(employee.base_salary) <= 0:
        employee.base_salary = structure.basic
    log_action(db, user, "assign", "payroll", entity=structure, entity_type="SalaryStructure",
               description=(f"Grade {grade.name} assigned to {employee.employee_code}: basic "
                            f"{_f(structure.basic):,.0f}, {len(allowances)} allowance(s)"),
               before=before, after={"basic": _f(structure.basic), "allowances": allowances}, request=request)
    db.flush()
    return structure


# =============================================================================== employee account ledger
# The Employee Self Portal's Account Ledger (docs/AUDIT_EMPLOYEE_SELF_PORTAL.md) needs one line per thing
# that moved money for a person; the accounting journal posts a single entry for the whole run, so it
# cannot answer "what happened to my pay". Posting a run walks its payslips and writes those lines.
#
# Signs, as the model documents them: a debit is owed to the member of staff, a credit is paid or deducted.
#   * salary earned                   -> debit  (gross less the bonuses, which carry their own line)
#   * payroll deductions              -> credit (attendance and the structure's own deductions)
#   * an approved violation's fine    -> credit (posted by the approval; re-posting here is a no-op)
#   * an approved bonus               -> debit  (likewise)
#   * an advance instalment recovered -> debit  (it reverses the credit raised when the advance was paid)
#
# Every line is keyed on reference_type + reference_id + source, so re-posting a run, or approving the
# same advance twice, adds nothing.

def post_run_to_employee_ledgers(db: Session, run: PayrollRun, user: Optional[User] = None) -> int:
    """Write every payslip in a run onto its employee's Account Ledger. Idempotent; returns lines added."""
    from app.services import hr as hr_svc
    vid = f"PR-{run.id:05d}"
    entry_date = month_bounds(run.period)[1]
    written = 0
    for ps in run.payslips:
        emp = ps.employee
        if emp is None:
            continue
        det = ps.details or {}
        bonuses = det.get("bonuses") or []
        violations = det.get("violations") or []
        bonus_total = round(sum(_f(b.get("amount")) for b in bonuses), 2)
        violation_total = round(sum(_f(v.get("amount")) for v in violations), 2)
        earned = round(_f(ps.gross) - bonus_total, 2)
        # ps.deductions already carries the violation fines; those are listed one by one so a member of
        # staff can see each, and what is left is the structure's own deductions (tax, fund, loan...).
        other = round(_f(ps.deductions) - violation_total + _f(ps.attendance_deduction), 2)
        advance = round(_f(ps.advance_deduction), 2)
        lines = [
            ("salary", f"Salary for {run.period}" + (f" - {run.description}" if run.description else ""),
             max(0.0, earned), 0.0),
            ("adjustment", f"Payroll deductions for {run.period}", 0.0, max(0.0, other)),
            ("advance_recovery", f"Salary advance instalment recovered in {run.period}", advance, 0.0),
        ]
        for source, description, debit, credit in lines:
            if hr_svc.post_ledger_entry(db, emp, entry_date, description, source=source,
                                        reference_type="payslip", reference_id=ps.id, debit=debit,
                                        credit=credit, voucher_ref=vid, currency=ps.currency,
                                        user=user) is not None:
                written += 1
        # Bonuses and violations normally reach the ledger when they are approved; re-posting them from
        # the run costs nothing and back-fills anything approved before this page existed.
        for b in bonuses:
            row = db.get(Bonus, b["id"]) if b.get("id") else None
            if row is not None and hr_svc.post_bonus_ledger(db, row, user) is not None:
                written += 1
        for v in violations:
            row = db.get(Violation, v["id"]) if v.get("id") else None
            if row is not None and hr_svc.post_violation_ledger(db, row, user) is not None:
                written += 1
    db.flush()
    return written


def post_run_payment_to_employee_ledgers(db: Session, run: PayrollRun, user: Optional[User] = None) -> int:
    """Credit each payslip's net when the run is paid, so the ledger clears instead of only growing.

    Without this every salary earned stays on the ledger as owed for ever, and the balance a member of staff
    reads is the sum of their whole career rather than what they are still waiting for.
    """
    from app.services import hr as hr_svc
    vid = f"PR-{run.id:05d}"
    entry_date = run.paid_at.date() if run.paid_at else month_bounds(run.period)[1]
    written = 0
    for ps in run.payslips:
        if ps.employee is None:
            continue
        if hr_svc.post_ledger_entry(db, ps.employee, entry_date,
                                    f"Salary paid for {run.period}", source="payment",
                                    reference_type="payslip", reference_id=ps.id,
                                    credit=max(0.0, round(_f(ps.net), 2)), voucher_ref=vid,
                                    currency=ps.currency, user=user) is not None:
            written += 1
    db.flush()
    return written

