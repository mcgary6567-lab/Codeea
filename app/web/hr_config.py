"""HR Configurations — the catalogues behind the Human Resource area (audit docs/AUDIT_HUMAN_RESOURCE.md).

Departments · Shift · Holidays · Users · Violation Types · Staff Bonus Types · Grades & Allowances.
Shaped exactly like app/web/academic_config.py: an index of cards, then one simple list page per catalogue
with status tiles, a filter bar, a bordered table using the ERP's column labels, Create in a modal, inline
edit and a status toggle. Every mutation writes an audit event and redirects with a flash message.

Guarded by ``employees.configure``, falling back to ``employees.update`` for the People & Culture roles
whose permission set predates the configure action.
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import parse_bool, parse_date, parse_float, parse_int, redirect
from app.database import get_db
from app.models.core import Department, Role, User
from app.models.hr_erp import BonusType, Grade, Holiday, ViolationType
from app.models.people import Bonus, Employee, Violation
from app.models.scheduling import Shift

router = APIRouter(prefix="/hr/config", dependencies=[Depends(csrf_protect)])

BASE = "/hr/config"
MODULE = "employees"
STATUSES = [("active", "Active"), ("inactive", "Inactive")]
SEVERITIES = [("minor", "Minor"), ("major", "Major"), ("critical", "Critical")]
SHIFT_GROUPS = [("morning", "Morning"), ("night", "Night")]
HOLIDAY_SHIFTS = [("all", "All Shifts"), ("morning", "Morning"), ("night", "Night")]

# employees.configure with employees.update as the fallback (see the module docstring).
config_guard = require("employees.configure", "employees.update", any_of=True)

CARDS = [  # title, url, icon, blurb
    ("Departments", f"{BASE}/departments", "building-2", "Departments staff belong to"),
    ("Shift", f"{BASE}/shifts", "clock", "Morning and night shifts with their times"),
    ("Holidays", f"{BASE}/holidays", "calendar-off", "Non-working days used by attendance and leave"),
    ("Violation Types", f"{BASE}/violation-types", "triangle-alert", "Offences and their standard penalty"),
    ("Staff Bonus Types", f"{BASE}/bonus-types", "gift", "Bonuses and their standard amount"),
    ("Grades & Allowances", f"{BASE}/grades", "layers", "Salary grades and the allowances they carry"),
    ("Users", f"{BASE}/users", "users", "Branch user accounts, roles and last sign-in"),
]


# =============================================================================== helpers
def _get(db: Session, model, id: int, label: str):
    obj = db.get(model, id)
    if not obj:
        raise HTTPException(404, f"{label} not found")
    return obj


def _perms(user: User) -> dict:
    can_configure = rbac.has_permission(user, f"{MODULE}.configure") or rbac.has_permission(user, f"{MODULE}.update")
    return {"can_add": can_configure, "can_edit": can_configure, "can_configure": can_configure}


def _status_field(form, default: str = "active") -> str:
    v = (form.get("status") or default).strip().lower()
    return v if v in ("active", "inactive") else default


def _status_counts(db: Session, model) -> dict:
    counts = dict(db.query(model.status, func.count(model.id)).group_by(model.status).all())
    return {"total": sum(counts.values()), "active": counts.get("active", 0), "inactive": counts.get("inactive", 0)}


def _toggle(db: Session, user: User, request: Request, obj, label: str, back: str):
    before = snapshot(obj)
    obj.status = "inactive" if obj.status == "active" else "active"
    log_action(db, user, "status_change", MODULE, entity=obj, description=f"{label} marked {obj.status}",
               before=before, after=snapshot(obj), request=request)
    db.commit()
    return redirect(back, f"{label} marked {obj.status}.")


def _parse_time(value: Optional[str], default: time) -> time:
    if not value:
        return default
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
        try:
            return datetime.strptime(value.strip().upper(), fmt).time()
        except ValueError:
            continue
    return default


def shift_code(sh: Shift) -> str:
    """The ERP prints a short code per shift; ours is derived from the group and the row (M1, N2...)."""
    return f"{(sh.group or sh.name or 'S')[0].upper()}{sh.id}"


def _allowances_from_form(form) -> dict:
    """Allowance name/amount pairs from the repeated ``allow_key`` / ``allow_value`` inputs."""
    out: dict[str, float] = {}
    for k, v in zip(form.getlist("allow_key"), form.getlist("allow_value")):
        key = (k or "").strip().lower().replace(" ", "_")
        if key:
            out[key] = parse_float(v, 0.0)
    return out


# =============================================================================== index
@router.get("", include_in_schema=False)
def index(request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    counts = {
        f"{BASE}/departments": db.query(func.count(Department.id)).scalar() or 0,
        f"{BASE}/shifts": db.query(func.count(Shift.id)).scalar() or 0,
        f"{BASE}/holidays": db.query(func.count(Holiday.id)).scalar() or 0,
        f"{BASE}/violation-types": db.query(func.count(ViolationType.id)).scalar() or 0,
        f"{BASE}/bonus-types": db.query(func.count(BonusType.id)).scalar() or 0,
        f"{BASE}/grades": db.query(func.count(Grade.id)).scalar() or 0,
        f"{BASE}/users": db.query(func.count(User.id)).scalar() or 0,
    }
    cards = [{"title": t, "url": u, "icon": i, "blurb": b, "count": counts.get(u, 0)} for t, u, i, b in CARDS]
    return render(request, "hr_config/index.html", {"user": user, "cards": cards})


# =============================================================================== 1. Departments
@router.get("/departments", include_in_schema=False)
def departments_page(request: Request, q: str = "", status: str = "", db: Session = Depends(get_db),
                     user: User = Depends(config_guard)):
    query = db.query(Department)
    if q:
        query = query.filter(Department.name.ilike(f"%{q}%"))
    if status == "active":
        query = query.filter(Department.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(Department.is_active.is_(False))
    rows = query.order_by(Department.name).all()
    headcount = dict(db.query(Employee.department_id, func.count(Employee.id))
                     .filter(Employee.status.notin_(["resigned", "terminated"])).group_by(Employee.department_id).all())
    total = db.query(func.count(Department.id)).scalar() or 0
    active = db.query(func.count(Department.id)).filter(Department.is_active.is_(True)).scalar() or 0
    return render(request, "hr_config/departments.html", {
        "user": user, "rows": rows, "q": q, "status": status, "statuses": STATUSES, "headcount": headcount,
        "stats": {"total": total, "active": active, "inactive": total - active}, **_perms(user)})


@router.post("/departments/new", include_in_schema=False)
async def department_create(request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return redirect(f"{BASE}/departments", "A department name is required.", "error")
    code = (form.get("code") or name).strip().lower().replace(" ", "_")[:30]
    if db.query(Department).filter(Department.code == code).first():
        return redirect(f"{BASE}/departments", f"A department with the code '{code}' already exists.", "error")
    d = Department(name=name, code=code, description=form.get("description") or None,
                   is_active=_status_field(form) == "active")
    db.add(d)
    db.flush()
    log_action(db, user, "create", MODULE, entity=d, description=f"Department {d.name} created", after=snapshot(d), request=request)
    db.commit()
    return redirect(f"{BASE}/departments", f"Department {d.name} created.")


@router.post("/departments/{did}/edit", include_in_schema=False)
async def department_edit(did: int, request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    d = _get(db, Department, did, "Department")
    form = await request.form()
    before = snapshot(d)
    d.name = (form.get("name") or d.name).strip()
    if form.get("description") is not None:
        d.description = form.get("description") or None
    d.is_active = _status_field(form, "active" if d.is_active else "inactive") == "active"
    log_action(db, user, "update", MODULE, entity=d, description=f"Department {d.name} updated", before=before,
               after=snapshot(d), request=request)
    db.commit()
    return redirect(f"{BASE}/departments", f"Department {d.name} updated.")


@router.post("/departments/{did}/toggle", include_in_schema=False)
def department_toggle(did: int, request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    d = _get(db, Department, did, "Department")
    before = snapshot(d)
    d.is_active = not d.is_active
    state = "active" if d.is_active else "inactive"
    log_action(db, user, "status_change", MODULE, entity=d, description=f"Department {d.name} marked {state}",
               before=before, after=snapshot(d), request=request)
    db.commit()
    return redirect(f"{BASE}/departments", f"Department {d.name} marked {state}.")


# =============================================================================== 2. Shift
@router.get("/shifts", include_in_schema=False)
def shifts_page(request: Request, q: str = "", group: str = "", status: str = "", db: Session = Depends(get_db),
                user: User = Depends(config_guard)):
    query = db.query(Shift)
    if q:
        query = query.filter(Shift.name.ilike(f"%{q}%"))
    if group:
        query = query.filter(Shift.group == group)
    if status == "active":
        query = query.filter(Shift.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(Shift.is_active.is_(False))
    rows = query.order_by(Shift.group, Shift.start_time).all()
    staff = dict(db.query(Employee.shift, func.count(Employee.id))
                 .filter(Employee.status.notin_(["resigned", "terminated"])).group_by(Employee.shift).all())
    total = db.query(func.count(Shift.id)).scalar() or 0
    active = db.query(func.count(Shift.id)).filter(Shift.is_active.is_(True)).scalar() or 0
    return render(request, "hr_config/shifts.html", {
        "user": user, "rows": rows, "q": q, "group": group, "status": status, "statuses": STATUSES,
        "groups": SHIFT_GROUPS, "staff": staff, "shift_code": shift_code,
        "stats": {"total": total, "active": active, "inactive": total - active}, **_perms(user)})


def _apply_shift(sh: Shift, form) -> None:
    sh.name = (form.get("name") or sh.name or "Morning").strip()
    sh.group = (form.get("group") or sh.group or "morning").strip().lower()
    sh.start_time = _parse_time(form.get("start_time"), sh.start_time or time(9, 0))
    sh.end_time = _parse_time(form.get("end_time"), sh.end_time or time(17, 0))
    sh.is_active = _status_field(form, "active" if sh.is_active else "inactive") == "active"


@router.post("/shifts/new", include_in_schema=False)
async def shift_create(request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    form = await request.form()
    if not (form.get("name") or "").strip():
        return redirect(f"{BASE}/shifts", "A shift name is required.", "error")
    sh = Shift(name="Morning", group="morning", start_time=time(9, 0), end_time=time(17, 0), is_active=True)
    _apply_shift(sh, form)
    db.add(sh)
    db.flush()
    log_action(db, user, "create", MODULE, entity=sh, description=f"Shift {sh.name} ({sh.group}) created",
               after=snapshot(sh), request=request)
    db.commit()
    return redirect(f"{BASE}/shifts", f"Shift {sh.name} created.")


@router.post("/shifts/{sid}/edit", include_in_schema=False)
async def shift_edit(sid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    sh = _get(db, Shift, sid, "Shift")
    form = await request.form()
    before = snapshot(sh)
    _apply_shift(sh, form)
    log_action(db, user, "update", MODULE, entity=sh, description=f"Shift {sh.name} updated", before=before,
               after=snapshot(sh), request=request)
    db.commit()
    return redirect(f"{BASE}/shifts", f"Shift {sh.name} updated.")


@router.post("/shifts/{sid}/toggle", include_in_schema=False)
def shift_toggle(sid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    sh = _get(db, Shift, sid, "Shift")
    before = snapshot(sh)
    sh.is_active = not sh.is_active
    state = "active" if sh.is_active else "inactive"
    log_action(db, user, "status_change", MODULE, entity=sh, description=f"Shift {sh.name} marked {state}",
               before=before, after=snapshot(sh), request=request)
    db.commit()
    return redirect(f"{BASE}/shifts", f"Shift {sh.name} marked {state}.")


# =============================================================================== 3. Holidays
@router.get("/holidays", include_in_schema=False)
def holidays_page(request: Request, q: str = "", shift_group: str = "", status: str = "", year: str = "",
                  db: Session = Depends(get_db), user: User = Depends(config_guard)):
    query = db.query(Holiday)
    if q:
        query = query.filter(Holiday.name.ilike(f"%{q}%"))
    if shift_group:
        query = query.filter(Holiday.shift_group == shift_group)
    if status:
        query = query.filter(Holiday.status == status)
    y = parse_int(year)
    if y:
        query = query.filter(Holiday.holiday_date >= date(y, 1, 1), Holiday.holiday_date <= date(y, 12, 31))
    rows = query.order_by(Holiday.holiday_date.desc()).all()
    years = sorted({d.year for (d,) in db.query(Holiday.holiday_date).all() if d}, reverse=True)
    upcoming = db.query(func.count(Holiday.id)).filter(Holiday.holiday_date >= date.today(),
                                                       Holiday.status == "active").scalar() or 0
    stats = _status_counts(db, Holiday)
    stats["upcoming"] = upcoming
    return render(request, "hr_config/holidays.html", {
        "user": user, "rows": rows, "q": q, "shift_group": shift_group, "status": status, "year": year,
        "years": [(str(v), str(v)) for v in years], "statuses": STATUSES, "shift_groups": HOLIDAY_SHIFTS,
        "stats": stats, "today": date.today(), **_perms(user)})


def _apply_holiday(h: Holiday, form) -> None:
    h.name = (form.get("name") or h.name or "Holiday").strip()
    h.holiday_date = parse_date(form.get("holiday_date")) or h.holiday_date or date.today()
    h.end_date = parse_date(form.get("end_date"))
    if h.end_date and h.end_date < h.holiday_date:
        h.end_date = None
    h.shift_group = (form.get("shift_group") or h.shift_group or "all").strip().lower()
    h.is_paid = parse_bool(form.get("is_paid"))
    h.notes = form.get("notes") or None
    h.status = _status_field(form, h.status or "active")


@router.post("/holidays/new", include_in_schema=False)
async def holiday_create(request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    form = await request.form()
    if not (form.get("name") or "").strip():
        return redirect(f"{BASE}/holidays", "A holiday name is required.", "error")
    h = Holiday(name="Holiday", holiday_date=date.today(), shift_group="all", is_paid=True, status="active")
    _apply_holiday(h, form)
    clash = db.query(Holiday).filter(Holiday.holiday_date == h.holiday_date, Holiday.shift_group == h.shift_group).first()
    if clash:
        return redirect(f"{BASE}/holidays", f"{clash.name} is already recorded on {h.holiday_date}.", "error")
    db.add(h)
    db.flush()
    log_action(db, user, "create", MODULE, entity=h, description=f"Holiday {h.name} on {h.holiday_date} created",
               after=snapshot(h), request=request)
    db.commit()
    return redirect(f"{BASE}/holidays", f"Holiday {h.name} created.")


@router.post("/holidays/{hid}/edit", include_in_schema=False)
async def holiday_edit(hid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    h = _get(db, Holiday, hid, "Holiday")
    form = await request.form()
    before = snapshot(h)
    _apply_holiday(h, form)
    log_action(db, user, "update", MODULE, entity=h, description=f"Holiday {h.name} updated", before=before,
               after=snapshot(h), request=request)
    db.commit()
    return redirect(f"{BASE}/holidays", f"Holiday {h.name} updated.")


@router.post("/holidays/{hid}/toggle", include_in_schema=False)
def holiday_toggle(hid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    h = _get(db, Holiday, hid, "Holiday")
    return _toggle(db, user, request, h, f"Holiday {h.name}", f"{BASE}/holidays")


# =============================================================================== 4. Violation Types
@router.get("/violation-types", include_in_schema=False)
def violation_types_page(request: Request, q: str = "", severity: str = "", status: str = "",
                         db: Session = Depends(get_db), user: User = Depends(config_guard)):
    query = db.query(ViolationType)
    if q:
        query = query.filter(ViolationType.description.ilike(f"%{q}%"))
    if severity:
        query = query.filter(ViolationType.severity == severity)
    if status:
        query = query.filter(ViolationType.status == status)
    rows = query.order_by(ViolationType.sort_no, ViolationType.id).all()
    used = dict(db.query(Violation.violation_type_id, func.count(Violation.id))
                .filter(Violation.violation_type_id.isnot(None)).group_by(Violation.violation_type_id).all())
    return render(request, "hr_config/violation_types.html", {
        "user": user, "rows": rows, "q": q, "severity": severity, "status": status, "statuses": STATUSES,
        "severities": SEVERITIES, "used": used, "stats": _status_counts(db, ViolationType),
        "next_sort": (db.query(func.max(ViolationType.sort_no)).scalar() or 0) + 1, **_perms(user)})


def _apply_violation_type(v: ViolationType, form) -> None:
    v.description = (form.get("description") or v.description or "").strip()
    v.description_urdu = form.get("description_urdu") or None
    v.penalty_amount = parse_float(form.get("penalty_amount"), float(v.penalty_amount or 0))
    v.severity = (form.get("severity") or v.severity or "minor").strip().lower()
    v.sort_no = parse_int(form.get("sort_no"), v.sort_no or 0) or 0
    v.status = _status_field(form, v.status or "active")


@router.post("/violation-types/new", include_in_schema=False)
async def violation_type_create(request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    form = await request.form()
    if not (form.get("description") or "").strip():
        return redirect(f"{BASE}/violation-types", "A description is required.", "error")
    v = ViolationType(description="", penalty_amount=0, severity="minor", status="active", sort_no=0)
    _apply_violation_type(v, form)
    db.add(v)
    db.flush()
    log_action(db, user, "create", MODULE, entity=v,
               description=f"Violation type '{v.description[:60]}' created (penalty {float(v.penalty_amount or 0):,.0f})",
               after=snapshot(v), request=request)
    db.commit()
    return redirect(f"{BASE}/violation-types", "Violation type created.")


@router.post("/violation-types/{vid}/edit", include_in_schema=False)
async def violation_type_edit(vid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    v = _get(db, ViolationType, vid, "Violation type")
    form = await request.form()
    before = snapshot(v)
    _apply_violation_type(v, form)
    log_action(db, user, "update", MODULE, entity=v, description=f"Violation type '{v.description[:60]}' updated",
               before=before, after=snapshot(v), request=request)
    db.commit()
    return redirect(f"{BASE}/violation-types", "Violation type updated.")


@router.post("/violation-types/{vid}/toggle", include_in_schema=False)
def violation_type_toggle(vid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    v = _get(db, ViolationType, vid, "Violation type")
    return _toggle(db, user, request, v, "Violation type", f"{BASE}/violation-types")


# =============================================================================== 5. Staff Bonus Types
@router.get("/bonus-types", include_in_schema=False)
def bonus_types_page(request: Request, q: str = "", status: str = "", db: Session = Depends(get_db),
                     user: User = Depends(config_guard)):
    query = db.query(BonusType)
    if q:
        query = query.filter(BonusType.description.ilike(f"%{q}%"))
    if status:
        query = query.filter(BonusType.status == status)
    rows = query.order_by(BonusType.sort_no, BonusType.id).all()
    used = dict(db.query(Bonus.bonus_type_id, func.count(Bonus.id))
                .filter(Bonus.bonus_type_id.isnot(None)).group_by(Bonus.bonus_type_id).all())
    return render(request, "hr_config/bonus_types.html", {
        "user": user, "rows": rows, "q": q, "status": status, "statuses": STATUSES, "used": used,
        "stats": _status_counts(db, BonusType),
        "next_sort": (db.query(func.max(BonusType.sort_no)).scalar() or 0) + 1, **_perms(user)})


def _apply_bonus_type(b: BonusType, form) -> None:
    b.description = (form.get("description") or b.description or "").strip()
    b.description_urdu = form.get("description_urdu") or None
    b.bonus_amount = parse_float(form.get("bonus_amount"), float(b.bonus_amount or 0))
    b.sort_no = parse_int(form.get("sort_no"), b.sort_no or 0) or 0
    b.status = _status_field(form, b.status or "active")


@router.post("/bonus-types/new", include_in_schema=False)
async def bonus_type_create(request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    form = await request.form()
    if not (form.get("description") or "").strip():
        return redirect(f"{BASE}/bonus-types", "A description is required.", "error")
    b = BonusType(description="", bonus_amount=0, status="active", sort_no=0)
    _apply_bonus_type(b, form)
    db.add(b)
    db.flush()
    log_action(db, user, "create", MODULE, entity=b,
               description=f"Bonus type '{b.description[:60]}' created (amount {float(b.bonus_amount or 0):,.0f})",
               after=snapshot(b), request=request)
    db.commit()
    return redirect(f"{BASE}/bonus-types", "Bonus type created.")


@router.post("/bonus-types/{bid}/edit", include_in_schema=False)
async def bonus_type_edit(bid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    b = _get(db, BonusType, bid, "Bonus type")
    form = await request.form()
    before = snapshot(b)
    _apply_bonus_type(b, form)
    log_action(db, user, "update", MODULE, entity=b, description=f"Bonus type '{b.description[:60]}' updated",
               before=before, after=snapshot(b), request=request)
    db.commit()
    return redirect(f"{BASE}/bonus-types", "Bonus type updated.")


@router.post("/bonus-types/{bid}/toggle", include_in_schema=False)
def bonus_type_toggle(bid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    b = _get(db, BonusType, bid, "Bonus type")
    return _toggle(db, user, request, b, "Bonus type", f"{BASE}/bonus-types")


# =============================================================================== 6. Grades & Allowances
@router.get("/grades", include_in_schema=False)
def grades_page(request: Request, q: str = "", status: str = "", db: Session = Depends(get_db),
                user: User = Depends(config_guard)):
    query = db.query(Grade)
    if q:
        query = query.filter(Grade.name.ilike(f"%{q}%"))
    if status:
        query = query.filter(Grade.status == status)
    rows = query.order_by(Grade.name).all()
    assigned = dict(db.query(Employee.grade_id, func.count(Employee.id))
                    .filter(Employee.grade_id.isnot(None)).group_by(Employee.grade_id).all())
    employees = [(e.id, f"{e.employee_code} — {e.full_name}") for e in
                 db.query(Employee).filter(Employee.status.notin_(["resigned", "terminated"]))
                 .order_by(Employee.full_name).all()]
    return render(request, "hr_config/grades.html", {
        "user": user, "rows": rows, "q": q, "status": status, "statuses": STATUSES, "assigned": assigned,
        "employees": employees, "stats": _status_counts(db, Grade), **_perms(user)})


def _apply_grade(g: Grade, form) -> None:
    g.name = (form.get("name") or g.name or "").strip()
    g.description = form.get("description") or None
    g.basic_min = parse_float(form.get("basic_min"), float(g.basic_min or 0))
    g.basic_max = parse_float(form.get("basic_max"), float(g.basic_max or 0))
    allowances = _allowances_from_form(form)
    if allowances or form.getlist("allow_key"):
        g.allowances = allowances
    g.status = _status_field(form, g.status or "active")


@router.post("/grades/new", include_in_schema=False)
async def grade_create(request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return redirect(f"{BASE}/grades", "A grade name is required.", "error")
    if db.query(Grade).filter(func.lower(Grade.name) == name.lower()).first():
        return redirect(f"{BASE}/grades", f"Grade {name} already exists.", "error")
    g = Grade(name=name, basic_min=0, basic_max=0, allowances={}, status="active")
    _apply_grade(g, form)
    db.add(g)
    db.flush()
    log_action(db, user, "create", MODULE, entity=g,
               description=f"Grade {g.name} created ({float(g.basic_min or 0):,.0f}-{float(g.basic_max or 0):,.0f})",
               after=snapshot(g), request=request)
    db.commit()
    return redirect(f"{BASE}/grades", f"Grade {g.name} created.")


@router.post("/grades/{gid}/edit", include_in_schema=False)
async def grade_edit(gid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    g = _get(db, Grade, gid, "Grade")
    form = await request.form()
    before = snapshot(g)
    _apply_grade(g, form)
    log_action(db, user, "update", MODULE, entity=g, description=f"Grade {g.name} updated", before=before,
               after=snapshot(g), request=request)
    db.commit()
    return redirect(f"{BASE}/grades", f"Grade {g.name} updated.")


@router.post("/grades/{gid}/toggle", include_in_schema=False)
def grade_toggle(gid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    g = _get(db, Grade, gid, "Grade")
    return _toggle(db, user, request, g, f"Grade {g.name}", f"{BASE}/grades")


@router.post("/grades/{gid}/assign", include_in_schema=False)
async def grade_assign(gid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(config_guard)):
    """Assigning a grade to an employee fills their allowances on the salary structure."""
    g = _get(db, Grade, gid, "Grade")
    form = await request.form()
    emp = db.get(Employee, parse_int(form.get("employee_id"), 0) or 0)
    if emp is None:
        return redirect(f"{BASE}/grades", "Choose an employee to assign this grade to.", "error")
    from app.services.payroll import erp_grade_allowances
    structure = erp_grade_allowances(db, emp, g, user, request=request)
    db.commit()
    return redirect(f"{BASE}/grades", f"{emp.full_name} moved to grade {g.name}: basic "
                                      f"{float(structure.basic or 0):,.0f} with {len(structure.allowances or {})} allowance(s).")


# =============================================================================== 7. Users (branch user list)
@router.get("/users", include_in_schema=False)
def users_page(request: Request, q: str = "", role: str = "", active: str = "", db: Session = Depends(get_db),
               user: User = Depends(config_guard)):
    query = db.query(User).join(Role, User.role_id == Role.id, isouter=True)
    if q:
        query = query.filter(User.full_name.ilike(f"%{q}%") | User.email.ilike(f"%{q}%"))
    if role:
        query = query.filter(Role.slug == role)
    if active == "yes":
        query = query.filter(User.is_active.is_(True))
    elif active == "no":
        query = query.filter(User.is_active.is_(False))
    staff_portals = ["admin", "teacher"]
    rows = [u for u in query.order_by(User.full_name).all() if (u.role.portal if u.role else "admin") in staff_portals]
    employees = {e.user_id: e for e in db.query(Employee).filter(Employee.user_id.isnot(None))}
    roles = [(r.slug, r.name) for r in db.query(Role).filter(Role.portal.in_(staff_portals)).order_by(Role.name)]
    stats = {"total": len(rows), "active": sum(1 for u in rows if u.is_active),
             "inactive": sum(1 for u in rows if not u.is_active),
             "never": sum(1 for u in rows if not u.last_login_at)}
    return render(request, "hr_config/users.html", {
        "user": user, "rows": rows, "q": q, "role": role, "active": active, "roles": roles,
        "employees": employees, "stats": stats,
        "actives": [("yes", "Active"), ("no", "In-active")], **_perms(user)})
