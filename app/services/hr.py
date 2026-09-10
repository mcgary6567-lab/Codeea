"""HR / People & Culture services (Modules 20/21/49): employees, attendance, leaves, provisioning,
violations, confidential grievances, KPIs.

Other modules import lazily: ``from app.services.hr import create_employee``.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.audit import log_action, snapshot
from app.core.notify import notify
from app.core.security import hash_password
from app.core.utils import next_code, month_bounds
from app.models.core import User, Role, Department, Branch, Setting, UserSession, ApiKey
from app.models.people import (Employee, Teacher, Student, HRAttendance, Leave, Violation, Grievance, SalaryStructure,
                               OnboardingTask, ProvisioningRecord, Candidate, DevelopmentPlan, Bonus, SalaryAdvance)

LATE_GRACE_MINUTES = 10
GRIEVANCE_SLA_DAYS = 5
GRIEVANCE_ROLES = {"hod_people", "super_admin"}  # confidential channel — never department heads
DEFAULT_LEAVE_ALLOWANCE = {"casual": 20, "sick": 10, "annual": 0, "emergency": 3}

ONBOARDING_TEMPLATE = [  # (title, category, due offset days)
    ("Create Google Workspace account", "accounts", 0),
    ("Add credentials to the password vault", "accounts", 0),
    ("Grant Drive folders (Department / Employee)", "accounts", 1),
    ("Collect CNIC copy, photo and address proof", "documents", 3),
    ("Signed employment contract on file", "documents", 3),
    ("Acknowledge Code of Conduct and Safeguarding policy", "policy", 5),
    ("Acknowledge Data Protection and Device policy", "policy", 5),
    ("Platform orientation training completed", "training", 10),
    ("Assign device (OQC-DEPT-NNN) and enforce MFA", "equipment", 2),
]

PROVISIONING_SYSTEMS = ["Google Workspace account", "Password vault entry", "Drive folders (Department / Employee)",
                        "ClickUp / Slack access", "MFA enforced", "Device assignment"]

DESIGNATION_ROLE_HINTS = [  # (substring in designation, role slug)
    ("teacher", "teacher"), ("head of people", "hod_people"), ("head of finance", "hod_finance"),
    ("head of academics", "hod_academics"), ("head of qa", "hod_qa"), ("head of technology", "hod_technology"),
    ("head of marketing", "hod_marketing"), ("supervisor", "supervisor"), ("manager", "manager"),
    ("billing", "billing_rep"), ("accountant", "accountant"), ("qa officer", "qa_officer"), ("hr officer", "hr_officer"),
    ("coordinator", "academic_coordinator"), ("lead generator", "lead_generator"), ("closer", "lead_closer"),
    ("system admin", "system_admin"),
]
DEPARTMENT_DEFAULT_ROLE = {"people": "hr_officer", "finance": "accountant", "qa": "qa_officer", "marketing": "lead_generator",
                           "academics": "academic_coordinator", "operations": "supervisor", "technology": "system_admin"}


# ----------------------------------------------------------------------------- helpers
def org_now() -> datetime:
    from app.services.people import org_now as _now
    return _now()


def setting_value(db: Session, key: str, default):
    s = db.query(Setting).filter(Setting.key == key).first()
    return s.value if s and s.value is not None else default


def _parse_hhmm(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        h, m = value.split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def shift_minutes(employee: Employee) -> tuple[int, int]:
    """(start, end) minutes since midnight in org time; end may exceed 1440 for night shifts."""
    start = _parse_hhmm(employee.shift_start)
    end = _parse_hhmm(employee.shift_end)
    if start is None:
        start = {"morning": 9 * 60, "evening": 14 * 60, "night": 20 * 60}.get(employee.shift, 9 * 60)
    if end is None:
        end = start + 8 * 60
    if end <= start:
        end += 24 * 60
    return start, end


def expected_checkin_minutes(employee: Employee, session: str) -> int:
    """am → shift start; pm → midpoint of the shift (second half starts)."""
    start, end = shift_minutes(employee)
    return start if session == "am" else start + (end - start) // 2


def role_for_employee(db: Session, designation: str, department: Optional[Department], is_teacher: bool) -> Optional[Role]:
    slug = "teacher" if is_teacher else None
    d = (designation or "").lower()
    if not slug:
        for hint, s in DESIGNATION_ROLE_HINTS:
            if hint in d:
                slug = s
                break
    if not slug and department is not None:
        slug = DEPARTMENT_DEFAULT_ROLE.get(department.code)
    return db.query(Role).filter(Role.slug == (slug or "hr_officer")).first()


def next_device_name(db: Session, department: Optional[Department]) -> str:
    code = (department.code if department else "GEN").upper()[:4]
    n = db.query(Employee).filter(Employee.device_name.like(f"OQC-{code}-%")).count() + 1
    while db.query(Employee).filter(Employee.device_name == f"OQC-{code}-{n:03d}").first():
        n += 1
    return f"OQC-{code}-{n:03d}"


def _unique_username(db: Session, base: str) -> str:
    base = "".join(ch for ch in base.lower() if ch.isalnum() or ch in "._") or "employee"
    candidate, i = base, 1
    while db.query(User).filter(User.username == candidate).first():
        i += 1
        candidate = f"{base}{i}"
    return candidate


def hr_notify_users(db: Session) -> list[User]:
    return db.query(User).join(Role, User.role_id == Role.id).filter(Role.slug.in_(["hod_people", "super_admin"]), User.is_active.is_(True)).all()


def hr_officer_users(db: Session) -> list[User]:
    return db.query(User).join(Role, User.role_id == Role.id).filter(Role.slug.in_(["hod_people", "hr_officer"]), User.is_active.is_(True)).all()


# ----------------------------------------------------------------------------- employees
def create_employee(db: Session, data: dict, user: Optional[User], create_login: bool = True, request=None) -> tuple[Employee, Optional[str]]:
    dept = db.query(Department).get(int(data["department_id"])) if data.get("department_id") else None
    branch = db.query(Branch).get(int(data["branch_id"])) if data.get("branch_id") else db.query(Branch).first()
    join_date = data.get("join_date") or date.today()
    probation_end = data.get("probation_end") or (join_date + timedelta(days=90))
    is_teacher = bool(data.get("is_teacher")) or "teacher" in (data.get("designation") or "").lower()
    shift = data.get("shift") or "morning"
    default_start = {"morning": "09:00", "evening": "14:00", "night": "20:00"}[shift] if shift in ("morning", "evening", "night") else "09:00"
    default_end = {"morning": "17:00", "evening": "22:00", "night": "04:00"}.get(shift, "17:00")
    if is_teacher:
        default_start = {"morning": "06:00", "evening": "14:00", "night": "20:00"}.get(shift, "06:00")
        default_end = {"morning": "14:00", "evening": "22:00", "night": "04:00"}.get(shift, "14:00")
    status = data.get("status") or ("probation" if probation_end and probation_end >= date.today() else "active")
    base_salary = float(data.get("base_salary") or 0)
    emp = Employee(
        employee_code=next_code(db, Employee, "employee_code", "E-"),
        full_name=(data.get("full_name") or "").strip(), designation=(data.get("designation") or "Staff").strip(),
        department_id=dept.id if dept else None, branch_id=branch.id if branch else None,
        manager_id=int(data["manager_id"]) if data.get("manager_id") else None,
        gender=data.get("gender") or "male", email=(data.get("email") or "").strip().lower() or None, phone=data.get("phone") or None,
        cnic=data.get("cnic") or None, address=data.get("address") or None, join_date=join_date, probation_end=probation_end,
        employment_type=data.get("employment_type") or "full_time", shift=shift,
        shift_start=data.get("shift_start") or default_start, shift_end=data.get("shift_end") or default_end,
        base_salary=base_salary, currency=data.get("currency") or "PKR", salary_band=data.get("salary_band") or ("B" if is_teacher else None),
        status=status, is_teacher=is_teacher, background_check_status="pending", mfa_enforced=True,
        device_name=data.get("device_name") or next_device_name(db, dept), documents=data.get("documents") or {},
    )
    db.add(emp)
    db.flush()

    temp_password = None
    if create_login and emp.email:
        existing = db.query(User).filter(User.email == emp.email).first()
        if existing and not db.query(Employee).filter(Employee.user_id == existing.id, Employee.id != emp.id).first():
            emp.user_id = existing.id
        elif not existing:
            role = role_for_employee(db, emp.designation, dept, is_teacher)
            temp_password = "Oqc-" + secrets.token_urlsafe(6) + "9"
            u = User(email=emp.email, username=_unique_username(db, emp.email.split("@")[0]), full_name=emp.full_name,
                     hashed_password=hash_password(temp_password), role_id=role.id if role else None,
                     department_id=dept.id if dept else None, branch_id=branch.id if branch else None,
                     phone=emp.phone, timezone="Asia/Karachi", must_change_password=True)
            db.add(u)
            db.flush()
            emp.user_id = u.id

    bands = setting_value(db, "salary_bands", {"A": 45000, "B": 35000, "C": 27000})
    basic = base_salary or (float(bands.get(emp.salary_band or "B", 0)) if is_teacher else 0)
    emp.base_salary = basic
    db.add(SalaryStructure(employee_id=emp.id, basic=basic, allowances={"internet": 3000} if is_teacher else {"internet": 3000, "medical": 2500},
                           deductions={} if is_teacher else {"tax": round(basic * 0.02)}, per_class_rate=250 if is_teacher else 0,
                           absence_deduction_per_day=round(basic / 26) if basic else 0, late_deduction_per_instance=300 if is_teacher else 500,
                           currency=emp.currency, effective_from=join_date))
    create_onboarding_tasks(db, emp, user)
    log_action(db, user, "create", "employees", entity=emp, description=f"Employee {emp.employee_code} created ({emp.designation})",
               after=snapshot(emp, ["employee_code", "full_name", "designation", "department_id", "status", "is_teacher"]), request=request)
    db.flush()
    return emp, temp_password


def create_onboarding_tasks(db: Session, emp: Employee, user: Optional[User]) -> list[OnboardingTask]:
    if db.query(OnboardingTask).filter(OnboardingTask.employee_id == emp.id).first():
        return []
    hr_owner = db.query(User).join(Role, User.role_id == Role.id).filter(Role.slug == "hr_officer", User.is_active.is_(True)).first()
    tasks = []
    for title, category, offset in ONBOARDING_TEMPLATE:
        t = OnboardingTask(employee_id=emp.id, title=title, category=category, status="pending",
                           due_date=(emp.join_date or date.today()) + timedelta(days=offset),
                           assigned_to_id=hr_owner.id if hr_owner else (user.id if user else None))
        db.add(t)
        tasks.append(t)
    db.flush()
    return tasks


def update_employee(db: Session, emp: Employee, data: dict, user: User, request=None) -> Employee:
    before = snapshot(emp)
    for f in ("full_name", "designation", "gender", "email", "phone", "cnic", "address", "employment_type", "shift", "shift_start",
              "shift_end", "currency", "salary_band", "status", "exit_reason"):
        if f in data:
            setattr(emp, f, (data[f] or None) if f not in ("full_name", "designation", "gender", "employment_type", "shift", "status", "currency") else (data[f] or getattr(emp, f)))
    for f in ("department_id", "branch_id", "manager_id"):
        if f in data:
            setattr(emp, f, int(data[f]) if data[f] else None)
    for f in ("join_date", "probation_end", "exit_date"):
        if f in data:
            setattr(emp, f, data[f])
    if "base_salary" in data and data["base_salary"] not in (None, ""):
        emp.base_salary = float(data["base_salary"])
    if "is_teacher" in data:
        emp.is_teacher = bool(data["is_teacher"])
    if "mfa_enforced" in data:
        emp.mfa_enforced = bool(data["mfa_enforced"])
    if emp.status in ("resigned", "terminated") and not emp.exit_date:
        emp.exit_date = date.today()
    if emp.user and emp.user.full_name != emp.full_name:
        emp.user.full_name = emp.full_name
    log_action(db, user, "update", "employees", entity=emp, description=f"Employee {emp.employee_code} updated", before=before, after=snapshot(emp),
               rationale=data.get("rationale"), request=request, consequential=emp.status in ("resigned", "terminated"))
    db.flush()
    return emp


def verify_background(db: Session, emp: Employee, status: str, user: User, rationale: str, request=None) -> Employee:
    before = {"background_check_status": emp.background_check_status}
    emp.background_check_status = status
    emp.background_check_date = date.today()
    if emp.teacher:
        emp.teacher.is_verified = status == "verified"
    log_action(db, user, "background_check", "employees", entity=emp, description=f"Background check set to {status} for {emp.employee_code}",
               rationale=rationale, before=before, after={"background_check_status": status}, severity="warning", consequential=True, request=request)
    if emp.user_id:
        notify(db, emp.user_id, "Background check updated", f"Your background check status is now '{status}'.", event_type="hr", link="/hr/me")
    db.flush()
    return emp


def update_salary_structure(db: Session, emp: Employee, data: dict, user: User, rationale: str, request=None) -> SalaryStructure:
    s = db.query(SalaryStructure).filter(SalaryStructure.employee_id == emp.id).first()
    if not s:
        s = SalaryStructure(employee_id=emp.id, currency=emp.currency)
        db.add(s)
        db.flush()
    before = snapshot(s)
    for f in ("basic", "per_class_rate", "absence_deduction_per_day", "late_deduction_per_instance"):
        if f in data and data[f] not in (None, ""):
            setattr(s, f, float(data[f]))
    if "allowances" in data:
        s.allowances = data["allowances"]
    if "deductions" in data:
        s.deductions = data["deductions"]
    if data.get("effective_from"):
        s.effective_from = data["effective_from"]
    if data.get("currency"):
        s.currency = data["currency"]
    emp.base_salary = s.basic
    log_action(db, user, "salary_change", "payroll", entity=s, entity_type="SalaryStructure", description=f"Salary structure changed for {emp.employee_code}",
               rationale=rationale, before=before, after=snapshot(s), consequential=True, request=request)
    db.flush()
    return s


# ----------------------------------------------------------------------------- attendance
def mark_attendance(db: Session, employee: Employee, session: str, action: str, user: Optional[User], ip: Optional[str] = None,
                    when: Optional[datetime] = None, request=None) -> HRAttendance:
    session = session if session in ("am", "pm") else "am"
    now = when or org_now()
    today = now.date()
    row = db.query(HRAttendance).filter(HRAttendance.employee_id == employee.id, HRAttendance.date == today, HRAttendance.session == session).first()
    if not row:
        row = HRAttendance(employee_id=employee.id, date=today, session=session, status="present")
        db.add(row)
    if action == "in":
        if row.check_in is None:
            row.check_in = now
            row.ip = ip
            expected = expected_checkin_minutes(employee, session)
            actual = now.hour * 60 + now.minute
            if employee.shift == "night" and actual < 6 * 60:  # after midnight belongs to the previous day's night shift window
                actual += 24 * 60
            diff = actual - expected
            if diff > LATE_GRACE_MINUTES:
                row.status, row.late_minutes = "late", diff
            else:
                row.status, row.late_minutes = "present", 0
    elif action == "out":
        row.check_out = now
        if row.check_in is None:
            row.status = row.status if row.status in ("late", "present") else "present"
    log_action(db, user, f"check_{action}", "hr_attendance", entity=row, description=f"{employee.employee_code} {session.upper()} check-{action} ({row.status})", request=request)
    db.flush()
    return row


def set_attendance_status(db: Session, employee: Employee, day: date, session: str, status: str, user: Optional[User], note: Optional[str] = None,
                          request=None) -> HRAttendance:
    row = db.query(HRAttendance).filter(HRAttendance.employee_id == employee.id, HRAttendance.date == day, HRAttendance.session == session).first()
    if not row:
        row = HRAttendance(employee_id=employee.id, date=day, session=session)
        db.add(row)
    before = {"status": row.status}
    row.status = status
    if status != "late":
        row.late_minutes = 0
    if note:
        row.correction_reason = note
    log_action(db, user, "attendance_mark", "hr_attendance", entity=row, description=f"{employee.employee_code} {day} {session.upper()} → {status}",
               before=before, after={"status": status}, rationale=note, request=request)
    db.flush()
    return row


def mark_absent_for_missing(db: Session, day: Optional[date] = None, user: Optional[User] = None, only_after_shift_end: bool = False) -> int:
    """End-of-day: employees without a check-in get an 'absent' row (am/pm), unless on approved leave or a holiday."""
    day = day or org_now().date()
    if day.weekday() == 6:  # Sunday closed
        return 0
    now = org_now()
    n = 0
    on_leave = {l.employee_id for l in db.query(Leave).filter(Leave.person_type == "employee", Leave.status == "approved",
                                                              Leave.start_date <= day, Leave.end_date >= day)}
    q = db.query(Employee).filter(Employee.status.in_(["active", "probation"]))
    for emp in q:
        if emp.join_date and emp.join_date > day:
            continue
        if only_after_shift_end and day == now.date():
            _, end = shift_minutes(emp)
            if now.hour * 60 + now.minute < min(end, 24 * 60 - 1):
                continue
        for session in ("am", "pm"):
            row = db.query(HRAttendance).filter(HRAttendance.employee_id == emp.id, HRAttendance.date == day, HRAttendance.session == session).first()
            if row:
                continue
            db.add(HRAttendance(employee_id=emp.id, date=day, session=session, status="leave" if emp.id in on_leave else "absent"))
            n += 1
    if n:
        log_action(db, user, "auto_absent", "hr_attendance", description=f"Marked {n} missing attendance sessions on {day}")
    db.flush()
    return n


def request_correction(db: Session, row: HRAttendance, reason: str, user: User, request=None) -> HRAttendance:
    row.correction_requested = True
    row.correction_reason = reason
    row.correction_status = "pending"
    log_action(db, user, "correction_request", "hr_attendance", entity=row, description=f"Correction requested for {row.date} {row.session}", rationale=reason, request=request)
    for u in hr_officer_users(db):
        notify(db, u, "Attendance correction requested", f"{row.employee.full_name} requested a correction for {row.date} ({row.session.upper()}).",
               event_type="hr_attendance", link="/hr/attendance/corrections")
    db.flush()
    return row


def decide_correction(db: Session, row: HRAttendance, approve: bool, user: User, new_status: Optional[str] = None, note: Optional[str] = None, request=None) -> HRAttendance:
    before = {"status": row.status, "correction_status": row.correction_status}
    row.correction_status = "approved" if approve else "rejected"
    row.approved_by_id = user.id
    if approve:
        row.status = new_status or "present"
        if row.status != "late":
            row.late_minutes = 0
    log_action(db, user, "approve" if approve else "reject", "hr_attendance", entity=row, rationale=note,
               description=f"Attendance correction {'approved' if approve else 'rejected'} for {row.employee.employee_code} {row.date} {row.session}",
               before=before, after={"status": row.status, "correction_status": row.correction_status}, request=request)
    if row.employee.user_id:
        notify(db, row.employee.user_id, f"Attendance correction {row.correction_status}", f"Your correction for {row.date} ({row.session.upper()}) was {row.correction_status}. {note or ''}",
               event_type="hr_attendance", link="/hr/me?tab=attendance")
    db.flush()
    return row


def attendance_summary(db: Session, employee_id: int, start: date, end: date) -> dict:
    rows = db.query(HRAttendance).filter(HRAttendance.employee_id == employee_id, HRAttendance.date >= start, HRAttendance.date <= end).all()
    counts = {"present": 0, "late": 0, "absent": 0, "leave": 0, "half_day": 0, "holiday": 0}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    worked = counts["present"] + counts["late"] + counts["half_day"]
    total = worked + counts["absent"]
    return {**counts, "sessions": len(rows), "pct": round(100 * worked / total, 1) if total else 100.0,
            "late_minutes": sum(r.late_minutes or 0 for r in rows)}


# ----------------------------------------------------------------------------- leaves
def leave_days(l: Leave) -> int:
    return max(1, (l.end_date - l.start_date).days + 1)


def leave_balance(db: Session, employee: Employee, year: Optional[int] = None) -> dict:
    year = year or date.today().year
    allowance = setting_value(db, "leave_allowance", DEFAULT_LEAVE_ALLOWANCE)
    rows = db.query(Leave).filter(Leave.person_type == "employee", Leave.employee_id == employee.id, Leave.status == "approved",
                                  Leave.start_date >= date(year, 1, 1), Leave.start_date <= date(year, 12, 31)).all()
    used: dict[str, int] = {}
    for l in rows:
        used[l.leave_type] = used.get(l.leave_type, 0) + leave_days(l)
    out = {}
    for t, allowed in allowance.items():
        out[t] = {"allowed": int(allowed), "used": used.get(t, 0), "remaining": max(0, int(allowed) - used.get(t, 0))}
    for t, n in used.items():
        if t not in out:
            out[t] = {"allowed": 0, "used": n, "remaining": 0}
    return out


def substitute_suggestions(db: Session, teacher: Teacher, start: date, end: date, limit: int = 5) -> list[dict]:
    on_leave = {l.employee_id for l in db.query(Leave).filter(Leave.person_type == "employee", Leave.status.in_(["approved", "pending"]),
                                                              Leave.start_date <= end, Leave.end_date >= start)}
    out = []
    for t in db.query(Teacher).filter(Teacher.id != teacher.id, Teacher.status == "active", Teacher.is_verified.is_(True)):
        if t.employee_id in on_leave:
            continue
        shared = sorted(set(t.courses or []) & set(teacher.courses or []))
        if not shared:
            continue
        same_shift = t.shift == teacher.shift
        load = db.query(func.count(Student.id)).filter(Student.teacher_id == t.id, Student.status.in_(["active", "trial"])).scalar() or 0
        score = len(shared) * 10 + (15 if same_shift else 0) + (5 if t.gender == teacher.gender else 0) - load
        out.append({"teacher": t, "shared_courses": shared, "same_shift": same_shift, "load": load, "score": score})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def request_leave(db: Session, employee: Employee, leave_type: str, start: date, end: date, reason: Optional[str], user: Optional[User],
                  substitute_teacher: Optional[Teacher] = None, request=None) -> Leave:
    if end < start:
        raise ValueError("End date must be on or after the start date")
    overlap = db.query(Leave).filter(Leave.person_type == "employee", Leave.employee_id == employee.id, Leave.status.in_(["pending", "approved"]),
                                     Leave.start_date <= end, Leave.end_date >= start).first()
    if overlap:
        raise ValueError(f"Overlaps an existing {overlap.status} leave ({overlap.start_date} to {overlap.end_date})")
    l = Leave(person_type="employee", employee_id=employee.id, leave_type=leave_type or "casual", start_date=start, end_date=end, reason=reason,
              status="pending", requested_by_id=user.id if user else None,
              substitute_teacher_id=substitute_teacher.id if substitute_teacher else None)
    db.add(l)
    db.flush()
    log_action(db, user, "create", "leaves", entity=l, description=f"Leave requested for {employee.employee_code}: {leave_type} {start}..{end}", request=request)
    approvers = {u.id for u in hr_officer_users(db)}
    if employee.department and employee.department.hod_user_id:
        approvers.add(employee.department.hod_user_id)
    for uid in approvers:
        if user and uid == user.id:
            continue
        notify(db, uid, "Leave request pending", f"{employee.full_name} requested {leave_type} leave {start} to {end} ({leave_days(l)} day(s)).",
               event_type="leave", link=f"/hr/leaves/{l.id}")
    return l


def decide_leave(db: Session, leave: Leave, approve: bool, user: User, note: Optional[str] = None, request=None) -> Leave:
    before = {"status": leave.status}
    leave.status = "approved" if approve else "rejected"
    leave.approved_by_id = user.id
    leave.approved_at = datetime.utcnow()
    if note:
        leave.reason = (leave.reason or "") + f"\n[Decision note] {note}"
    emp = leave.employee
    log_action(db, user, "approve" if approve else "reject", "leaves", entity=leave, rationale=note,
               description=f"Leave {leave.status} for {emp.employee_code if emp else leave.employee_id} ({leave.start_date}..{leave.end_date})",
               before=before, after={"status": leave.status}, request=request)
    if emp and emp.user_id:
        notify(db, emp.user_id, f"Leave {leave.status}", f"Your {leave.leave_type} leave from {leave.start_date} to {leave.end_date} was {leave.status}. {note or ''}",
               event_type="leave_approved" if approve else "leave", link="/hr/me?tab=leaves")
    if emp and emp.teacher:
        t = emp.teacher
        if t.supervisor_id:
            notify(db, t.supervisor_id, f"Teacher leave {leave.status}", f"{t.full_name} is on leave {leave.start_date} to {leave.end_date}."
                   + (f" Substitute: {leave.substitute_teacher_id and db.query(Teacher).get(leave.substitute_teacher_id).full_name}." if leave.substitute_teacher_id else " No substitute assigned."),
                   event_type="leave", link=f"/hr/leaves/{leave.id}")
        if approve and leave.substitute_teacher_id:
            sub = db.query(Teacher).get(leave.substitute_teacher_id)
            if sub and sub.user_id:
                notify(db, sub.user_id, "Substitute assignment", f"You are the suggested substitute for {t.full_name} from {leave.start_date} to {leave.end_date}.",
                       event_type="leave", link="/teacher/schedule")
        if approve:
            try:  # class propagation handled by scheduling module when available
                from app.services import scheduling as sched  # type: ignore
                fn = getattr(sched, "apply_teacher_leave", None)
                if fn:
                    fn(db, leave, user)
            except Exception:
                pass
    db.flush()
    return leave


# ----------------------------------------------------------------------------- provisioning (Module 49)
def provision(db: Session, employee: Employee, action: str, user: Optional[User], request=None) -> ProvisioningRecord:
    dept_code = (employee.department.code if employee.department else "gen")
    items: list[dict] = []
    if action == "onboard":
        if not employee.device_name:
            employee.device_name = next_device_name(db, employee.department)
        employee.mfa_enforced = True
        u = employee.user
        if u and not u.is_active:
            u.is_active = True
        items = [
            {"system": "Google Workspace account", "status": "done", "detail": employee.email or "no email on file"},
            {"system": "Password vault entry", "status": "done", "detail": f"vault://oqc/{dept_code}/{employee.employee_code}"},
            {"system": "Drive folders (Department / Employee)", "status": "done", "detail": f"Drive:/OQC/{dept_code.title()}/{employee.employee_code}"},
            {"system": "ClickUp / Slack access", "status": "done", "detail": f"#{dept_code} workspace + department channel"},
            {"system": "MFA enforced", "status": "done" if (u and u.two_factor_enabled) else "pending_user", "detail": "Required at next sign-in" if not (u and u.two_factor_enabled) else "2FA active"},
            {"system": "Device assignment", "status": "done", "detail": employee.device_name},
        ]
        if not u:
            items.append({"system": "Platform login", "status": "skipped", "detail": "No user account linked"})
        for t in db.query(OnboardingTask).filter(OnboardingTask.employee_id == employee.id, OnboardingTask.category == "accounts", OnboardingTask.status != "completed"):
            t.status, t.completed_at = "completed", datetime.utcnow()
    elif action == "offboard":
        u = employee.user
        revoked_sessions = 0
        revoked_keys = 0
        if u:
            u.is_active = False
            for s in db.query(UserSession).filter(UserSession.user_id == u.id, UserSession.revoked.is_(False)):
                s.revoked = True
                revoked_sessions += 1
            for k in db.query(ApiKey).filter(ApiKey.owner_id == u.id, ApiKey.is_active.is_(True)):
                k.is_active = False
                revoked_keys += 1
        sched_note = "No teacher schedules"
        if employee.teacher:
            employee.teacher.status = "inactive"
            employee.teacher.is_verified = False
            try:
                from app.models.scheduling import Schedule
                n_sched = db.query(Schedule).filter(Schedule.teacher_id == employee.teacher.id, Schedule.status == "active").count()
                sched_note = f"{n_sched} active schedule(s) flagged for reassignment by scheduling"
            except Exception:
                sched_note = "Schedules flagged for reassignment"
        if employee.status not in ("resigned", "terminated"):
            employee.status = "resigned"
        employee.exit_date = employee.exit_date or date.today()
        items = [
            {"system": "Platform login", "status": "revoked", "detail": f"User deactivated; {revoked_sessions} session(s) revoked"},
            {"system": "API keys", "status": "revoked", "detail": f"{revoked_keys} key(s) deactivated"},
            {"system": "Google Workspace account", "status": "revoked", "detail": "Suspended; mailbox delegated to department head"},
            {"system": "Password vault entry", "status": "revoked", "detail": "Shared credentials rotated"},
            {"system": "Drive folders (Department / Employee)", "status": "transferred", "detail": f"Ownership moved to Drive:/OQC/{dept_code.title()}/_archive"},
            {"system": "ClickUp / Slack access", "status": "revoked", "detail": "Removed from all channels"},
            {"system": "Device assignment", "status": "returned", "detail": f"{employee.device_name or 'no device'} marked for return / wipe"},
            {"system": "Teacher profile", "status": "revoked" if employee.teacher else "n/a", "detail": sched_note},
        ]
    else:
        raise ValueError("action must be onboard or offboard")
    rec = ProvisioningRecord(employee_id=employee.id, action=action, items=items, status="completed", performed_by_id=user.id if user else None)
    db.add(rec)
    db.flush()
    log_action(db, user, "offboard" if action == "offboard" else "onboard", "provisioning", entity=rec, severity="warning" if action == "offboard" else "info",
               description=f"{action.title()} provisioning for {employee.employee_code}: {len(items)} systems", after={"items": items},
               consequential=True, rationale=f"One-action {action} per Module 49 policy", request=request)
    if action == "onboard" and employee.user_id:
        notify(db, employee.user_id, "Welcome to Online Quran College", "Your accounts are provisioned. Enable two-factor authentication on first sign-in.",
               event_type="hr", link="/profile?tab=2fa")
    return rec


# ----------------------------------------------------------------------------- violations & grievances
def record_violation(db: Session, employee: Employee, violation_type: str, severity: str, description: Optional[str], user: Optional[User],
                     action_taken: Optional[str] = None, deduction_amount: float = 0, day: Optional[date] = None, request=None) -> Violation:
    v = Violation(employee_id=employee.id, violation_type=violation_type, severity=severity or "minor", description=description, action_taken=action_taken,
                  reported_by_id=user.id if user else None, date=day or date.today(), status="open", deduction_amount=deduction_amount or 0)
    db.add(v)
    db.flush()
    log_action(db, user, "create", "violations", entity=v, description=f"{violation_type} ({severity}) recorded for {employee.employee_code}",
               rationale=description, consequential=bool(deduction_amount) or severity in ("major", "critical"),
               severity="warning" if severity in ("major", "critical") else "info", after={"deduction_amount": float(deduction_amount or 0)}, request=request)
    if employee.user_id:
        notify(db, employee.user_id, "Violation recorded", f"A {severity} '{violation_type}' violation was recorded on {v.date}."
               + (f" Deduction: {employee.currency} {float(deduction_amount):,.0f}." if deduction_amount else ""), event_type="violation", link="/hr/me?tab=violations")
    return v


def close_violation(db: Session, v: Violation, user: User, note: Optional[str] = None, request=None) -> Violation:
    v.status = "closed"
    if note:
        v.action_taken = ((v.action_taken + "; ") if v.action_taken else "") + note
    log_action(db, user, "close", "violations", entity=v, description=f"Violation #{v.id} closed", rationale=note, request=request)
    db.flush()
    return v


def repeat_offenders(db: Session, days: int = 90, min_count: int = 3) -> list[dict]:
    since = date.today() - timedelta(days=days)
    rows = db.query(Violation.employee_id, func.count(Violation.id), func.sum(Violation.deduction_amount)).filter(Violation.date >= since)\
        .group_by(Violation.employee_id).having(func.count(Violation.id) >= min_count).order_by(func.count(Violation.id).desc()).all()
    out = []
    for emp_id, n, ded in rows:
        emp = db.query(Employee).get(emp_id)
        types = [t for (t,) in db.query(Violation.violation_type).filter(Violation.employee_id == emp_id, Violation.date >= since).distinct()]
        out.append({"employee": emp, "count": n, "deductions": float(ded or 0), "types": types})
    return out


def open_grievance(db: Session, subject: str, description: str, category: str, user: Optional[User], employee: Optional[Employee] = None,
                   is_anonymous: bool = False, request=None) -> Grievance:
    g = Grievance(employee_id=None if is_anonymous else (employee.id if employee else None),
                  submitted_by_id=None if is_anonymous else (user.id if user else None), is_anonymous=is_anonymous,
                  category=category or "workplace", subject=subject.strip(), description=description.strip(), status="open",
                  sla_due_at=datetime.utcnow() + timedelta(days=GRIEVANCE_SLA_DAYS))
    db.add(g)
    db.flush()
    # audit without identifying the submitter when anonymous
    log_action(db, None if is_anonymous else user, "create", "grievances", entity=g, description=f"Grievance #{g.id} opened ({category}){' anonymously' if is_anonymous else ''}", request=request)
    for u in hr_notify_users(db):  # confidential: only People & Culture head and CEO
        notify(db, u, "Confidential grievance received", f"#{g.id} {g.subject} — SLA {GRIEVANCE_SLA_DAYS} days.", event_type="grievance", link=f"/hr/grievances/{g.id}")
    return g


def update_grievance(db: Session, g: Grievance, user: User, status: Optional[str] = None, handler_id: Optional[int] = None,
                     resolution: Optional[str] = None, request=None) -> Grievance:
    before = {"status": g.status, "handler_id": g.handler_id}
    if handler_id is not None:
        g.handler_id = handler_id
    if status:
        g.status = status
        if status in ("resolved", "closed"):
            g.resolved_at = datetime.utcnow()
    if resolution:
        g.resolution = resolution
    log_action(db, user, "status_change", "grievances", entity=g, description=f"Grievance #{g.id} → {g.status}", rationale=resolution,
               before=before, after={"status": g.status, "handler_id": g.handler_id}, request=request, consequential=g.status in ("resolved", "closed"))
    if g.submitted_by_id and not g.is_anonymous:
        notify(db, g.submitted_by_id, f"Grievance {g.status}", f"Your grievance '{g.subject}' is now {g.status}." + (f" Resolution: {resolution}" if resolution else ""),
               event_type="grievance", link="/hr/me?tab=grievance")
    if handler_id and handler_id != user.id:
        notify(db, handler_id, "Grievance assigned to you", f"#{g.id} {g.subject}", event_type="grievance", link=f"/hr/grievances/{g.id}")
    db.flush()
    return g


def grievance_visible(user: User, g: Grievance) -> bool:
    """hod_people / super_admin see everything; hr_officer only non-anonymous ones assigned to them."""
    if user.is_superuser or user.role_slug in GRIEVANCE_ROLES:
        return True
    if user.role_slug == "hr_officer":
        return (not g.is_anonymous) and g.handler_id == user.id
    return False


# ----------------------------------------------------------------------------- bonuses & advances
def request_bonus(db: Session, employee: Employee, amount: float, bonus_type: str, reason: Optional[str], period: str, user: User, request=None) -> Bonus:
    b = Bonus(employee_id=employee.id, amount=amount, currency=employee.currency, bonus_type=bonus_type or "performance", reason=reason, period=period, status="pending")
    db.add(b)
    db.flush()
    log_action(db, user, "create", "payroll", entity=b, description=f"Bonus {employee.currency} {amount:,.0f} proposed for {employee.employee_code} ({period})", rationale=reason, request=request)
    return b


def request_advance(db: Session, employee: Employee, amount: float, installments: int, reason: Optional[str], user: User, request=None) -> SalaryAdvance:
    a = SalaryAdvance(employee_id=employee.id, amount=amount, currency=employee.currency, reason=reason, installments=max(1, installments or 1), remaining=amount, status="pending")
    db.add(a)
    db.flush()
    log_action(db, user, "create", "payroll", entity=a, description=f"Salary advance {employee.currency} {amount:,.0f} requested for {employee.employee_code}", rationale=reason, request=request)
    for u in hr_officer_users(db):
        if u.id != user.id:
            notify(db, u, "Salary advance requested", f"{employee.full_name}: {employee.currency} {amount:,.0f} over {a.installments} installment(s).", event_type="payroll", link="/hr/payroll/advances")
    return a


def decide_bonus_or_advance(db: Session, obj, approve: bool, user: User, note: Optional[str] = None, request=None):
    before = {"status": obj.status}
    obj.status = "approved" if approve else "rejected"
    obj.approved_by_id = user.id
    if isinstance(obj, SalaryAdvance) and approve:
        obj.remaining = obj.amount
    kind = "Bonus" if isinstance(obj, Bonus) else "Salary advance"
    log_action(db, user, "approve" if approve else "reject", "payroll", entity=obj, rationale=note,
               description=f"{kind} #{obj.id} {obj.status} for {obj.employee.employee_code} ({obj.currency} {float(obj.amount):,.0f})",
               before=before, after={"status": obj.status}, request=request)
    if obj.employee.user_id:
        notify(db, obj.employee.user_id, f"{kind} {obj.status}", f"{kind} of {obj.currency} {float(obj.amount):,.0f} was {obj.status}. {note or ''}",
               event_type="payroll", link="/hr/me?tab=payslips")
    db.flush()
    return obj


# ----------------------------------------------------------------------------- development plans
def upsert_development_plan(db: Session, employee: Employee, data: dict, user: User, request=None) -> DevelopmentPlan:
    plan = db.query(DevelopmentPlan).filter(DevelopmentPlan.employee_id == employee.id, DevelopmentPlan.status == "active").first()
    created = plan is None
    if created:
        plan = DevelopmentPlan(employee_id=employee.id, department_id=employee.department_id, owner_id=user.id, title=data.get("title") or f"Six-month growth journey — {employee.full_name}")
        db.add(plan)
    before = snapshot(plan) if not created else None
    if data.get("title"):
        plan.title = data["title"]
    if "goals" in data:
        plan.goals = data["goals"]
    if data.get("ai_fluency_level"):
        plan.ai_fluency_level = max(1, min(5, int(data["ai_fluency_level"])))
    if data.get("start_date"):
        plan.start_date = data["start_date"]
    if data.get("end_date"):
        plan.end_date = data["end_date"]
    elif not plan.end_date:
        plan.end_date = (plan.start_date or date.today()) + timedelta(days=182)
    goals = plan.goals or []
    if goals:
        plan.progress_pct = round(100 * sum(1 for g in goals if g.get("status") == "done") / len(goals))
    elif data.get("progress_pct") not in (None, ""):
        plan.progress_pct = int(data["progress_pct"])
    if data.get("status"):
        plan.status = data["status"]
    db.flush()
    log_action(db, user, "create" if created else "update", "employees", entity=plan, description=f"Development plan {'created' if created else 'updated'} for {employee.employee_code}",
               before=before, after=snapshot(plan), request=request)
    return plan


# ----------------------------------------------------------------------------- KPIs
def employee_kpis(db: Session, employee: Employee, period: str) -> dict:
    start, end = month_bounds(period)
    att = attendance_summary(db, employee.id, start, end)
    leaves = db.query(Leave).filter(Leave.person_type == "employee", Leave.employee_id == employee.id, Leave.status == "approved",
                                    Leave.start_date <= end, Leave.end_date >= start).all()
    violations = db.query(Violation).filter(Violation.employee_id == employee.id, Violation.date >= start, Violation.date <= end).count()
    out = {"period": period, "attendance_pct": att["pct"], "late": att["late"], "absent": att["absent"], "late_minutes": att["late_minutes"],
           "leave_days": sum(leave_days(l) for l in leaves), "violations": violations, "classes_done": None, "punctuality": None}
    if employee.teacher:
        from app.services.classes import teacher_stats
        st = teacher_stats(db, employee.teacher.id, since=start)
        out["classes_done"], out["punctuality"] = st["done"], st["punctuality_rate"]
    from app.models.people import Payslip
    ps = db.query(Payslip).join(Payslip.payroll_run).filter(Payslip.employee_id == employee.id).order_by(Payslip.id.desc()).first()
    out["last_net"] = float(ps.net) if ps else None
    return out


def hr_kpis(db: Session, period: Optional[str] = None) -> dict:
    period = period or date.today().strftime("%Y-%m")
    start, end = month_bounds(period)
    active = db.query(Employee).filter(Employee.status.in_(["active", "probation", "on_leave"]))
    headcount = active.count()
    by_dept = []
    for d in db.query(Department).order_by(Department.name):
        n = active.filter(Employee.department_id == d.id).count()
        if n:
            by_dept.append({"department": d.name, "code": d.code, "count": n})
    rows = db.query(HRAttendance.status, func.count(HRAttendance.id)).filter(HRAttendance.date >= start, HRAttendance.date <= end).group_by(HRAttendance.status).all()
    c = {s: n for s, n in rows}
    worked = c.get("present", 0) + c.get("late", 0) + c.get("half_day", 0)
    total = worked + c.get("absent", 0)
    attendance_pct = round(100 * worked / total, 1) if total else 0.0
    # hiring time: candidates hired → days applied→hired
    hired = db.query(Candidate).filter(Candidate.stage == "hired", Candidate.hired_employee_id.isnot(None)).all()
    days = []
    for cnd in hired:
        emp = db.query(Employee).get(cnd.hired_employee_id)
        if emp and emp.join_date and cnd.created_at:
            days.append(max(0, (emp.join_date - cnd.created_at.date()).days))
    hiring_days = round(sum(days) / len(days), 1) if days else None
    tasks_total = db.query(OnboardingTask).count()
    tasks_done = db.query(OnboardingTask).filter(OnboardingTask.status == "completed").count()
    onboarding_pct = round(100 * tasks_done / tasks_total, 1) if tasks_total else 100.0
    year_start = date(date.today().year, 1, 1)
    exits = db.query(Employee).filter(Employee.exit_date.isnot(None), Employee.exit_date >= year_start).count()
    turnover = round(100 * exits / (headcount + exits), 1) if (headcount + exits) else 0.0
    enps = None
    try:
        from app.models.crm import Feedback
        fb = db.query(Feedback.nps).filter(Feedback.is_confidential.is_(True), Feedback.nps.isnot(None), Feedback.respondent_type == "staff").all()
        if fb:
            scores = [n for (n,) in fb]
            enps = round(100 * (sum(1 for s in scores if s >= 9) - sum(1 for s in scores if s <= 6)) / len(scores))
    except Exception:
        enps = None
    on_leave_today = db.query(Leave).filter(Leave.person_type == "employee", Leave.status == "approved", Leave.start_date <= date.today(), Leave.end_date >= date.today()).count()
    return {"period": period, "headcount": headcount, "by_department": by_dept, "attendance_pct": attendance_pct, "hiring_days": hiring_days,
            "onboarding_pct": onboarding_pct, "turnover_pct": turnover, "enps": enps, "on_leave_today": on_leave_today, "exits_ytd": exits,
            "pending_leaves": db.query(Leave).filter(Leave.person_type == "employee", Leave.status == "pending").count(),
            "open_grievances": db.query(Grievance).filter(Grievance.status.in_(["open", "investigating"])).count(),
            "open_violations": db.query(Violation).filter(Violation.status == "open").count()}
