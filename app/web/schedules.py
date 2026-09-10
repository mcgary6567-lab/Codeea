"""Module 8 — Schedules: 24h/30-minute grid, conflict-checked creation, teacher & student views,
pause/end, bulk teacher change (teacher-change propagation) and shift administration."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.audit import log_action, snapshot
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import paginate, parse_bool, parse_date, parse_int, redirect
from app.database import get_db
from app.models.academic import Course
from app.models.core import User
from app.models.finance import Subscription
from app.models.people import Student, Teacher
from app.models.scheduling import ClassSession, Schedule, Shift
from app.services import classes as class_svc
from app.services import scheduling as svc

router = APIRouter(prefix="/schedules", dependencies=[Depends(csrf_protect)])

SCHEDULE_STATUSES = ["active", "paused", "ended"]
DAY_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _get(db: Session, id: int) -> Schedule:
    s = db.get(Schedule, id)
    if not s:
        raise HTTPException(404, "Schedule not found")
    return s


def _base_context(db: Session, user: User) -> dict:
    return {
        "teachers": svc.teachers_for(db, user),
        "verified_teacher_options": [(t.id, f"{t.full_name} · {t.shift.title()} shift · grade {t.grade}")
                                     for t in svc.teachers_for(db, user, verified_only=True)],
        "course_options": [(c.id, c.name) for c in db.query(Course).filter(Course.is_active.is_(True)).order_by(Course.order)],
        "shift_options": [(s.id, f"{s.name} ({s.start_time.strftime('%H:%M')}-{s.end_time.strftime('%H:%M')})")
                          for s in db.query(Shift).filter(Shift.is_active.is_(True)).order_by(Shift.start_time)],
        "slot_options": [(t.strftime("%H:%M"), t.strftime("%H:%M")) for t in class_svc.DAY_SLOTS],
        "duration_options": [(d, f"{d} minutes") for d in svc.DURATIONS],
        "days": list(enumerate(DAY_FULL)),
        "course_colors": svc.COURSE_COLORS,
    }


# ----------------------------------------------------------------------------- list / grids
@router.get("", include_in_schema=False)
def list_schedules(request: Request, view: str = "list", page: int = 1, q: str = "", status: str = "active",
                   teacher_id: int | None = None, student_id: int | None = None, course_id: int | None = None,
                   shift_id: int | None = None, free: int = 0, db: Session = Depends(get_db),
                   user: User = Depends(require("schedules.view"))):
    teacher_ids = svc.scoped_teacher_ids(db, user)
    query = db.query(Schedule)
    if teacher_ids is not None:
        query = query.filter(Schedule.teacher_id.in_(teacher_ids or [-1]))
    if status:
        query = query.filter(Schedule.status == status)
    if teacher_id:
        query = query.filter(Schedule.teacher_id == teacher_id)
    if student_id:
        query = query.filter(Schedule.student_id == student_id)
    if course_id:
        query = query.filter(Schedule.course_id == course_id)
    if shift_id:
        query = query.filter(Schedule.shift_id == shift_id)
    if q:
        like = f"%{q}%"
        query = query.join(Student, Student.id == Schedule.student_id).filter(
            or_(Student.full_name.ilike(like), Student.student_code.ilike(like)))

    ctx = {"user": user, "view": view, "q": q, "status": status, "teacher_id": teacher_id, "student_id": student_id,
           "course_id": course_id, "shift_id": shift_id, "free": free,
           "statuses": SCHEDULE_STATUSES, "day_names": svc.DAY_NAMES, **_base_context(db, user)}
    ctx["teacher_options"] = [(t.id, t.full_name) for t in ctx["teachers"]]
    ctx["student_options"] = [(s.id, f"{s.student_code} · {s.full_name}") for s in
                              db.query(Student).filter(Student.status.in_(["active", "trial", "free"])).order_by(Student.full_name).limit(500)]
    counts = dict(db.query(Schedule.status, func.count(Schedule.id)).group_by(Schedule.status).all())
    ctx["stats"] = {"active": counts.get("active", 0), "paused": counts.get("paused", 0), "ended": counts.get("ended", 0),
                    "trial": db.query(func.count(Schedule.id)).filter(Schedule.is_trial.is_(True), Schedule.status == "active").scalar() or 0}

    if free:
        scheduled = db.query(Schedule.student_id).filter(Schedule.status == "active")
        fq = db.query(Student).filter(Student.status.in_(["active", "trial", "free"]), Student.id.notin_(scheduled))
        if teacher_ids is not None:
            fq = fq.filter(Student.teacher_id.in_(teacher_ids or [-1]))
        ctx["free_students"] = fq.order_by(Student.full_name).limit(200).all()
        ctx["view"] = "free"
        return render(request, "schedules/list.html", ctx)

    if view == "teacher":
        sel = teacher_id or (ctx["teachers"][0].id if ctx["teachers"] else None)
        ctx["selected_teacher"] = db.get(Teacher, sel) if sel else None
        ctx["grid"] = svc.week_grid(db, teacher_id=sel) if sel else None
        ctx["teacher_id"] = sel
    elif view == "student":
        sel = student_id
        ctx["selected_student"] = db.get(Student, sel) if sel else None
        ctx["grid"] = svc.week_grid(db, student_id=sel) if sel else None
    else:
        ctx["page"] = paginate(query.order_by(Schedule.start_time, Schedule.id), page, 30)
        ctx["base_url"] = f"/schedules?view=list&q={q}&status={status}&teacher_id={teacher_id or ''}&course_id={course_id or ''}"
    return render(request, "schedules/list.html", ctx)


# ----------------------------------------------------------------------------- shifts
@router.get("/shifts", include_in_schema=False)
def shifts_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require("schedules.view"))):
    shifts = db.query(Shift).order_by(Shift.start_time).all()
    staff = db.query(User).filter(User.is_active.is_(True)).order_by(User.full_name).all()
    load = dict(db.query(Schedule.shift_id, func.count(Schedule.id)).filter(Schedule.status == "active").group_by(Schedule.shift_id).all())
    return render(request, "schedules/shifts.html", {
        "user": user, "shifts": shifts, "load": load,
        "staff_options": [(u.id, f"{u.full_name} — {u.role.name if u.role else 'User'}") for u in staff],
        "groups": ["morning", "night"]})


@router.post("/shifts/new", include_in_schema=False)
async def create_shift(request: Request, db: Session = Depends(get_db), user: User = Depends(require("schedules.add"))):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return redirect("/schedules/shifts", "Shift name is required.", "error")
    try:
        st = svc._parse_time(form.get("start_time") or "09:00")
        en = svc._parse_time(form.get("end_time") or "17:00")
    except ValueError:
        return redirect("/schedules/shifts", "Invalid shift times.", "error")
    sh = Shift(name=name, group=form.get("group") or "morning", start_time=st, end_time=en,
               manager_id=parse_int(form.get("manager_id")), supervisor_id=parse_int(form.get("supervisor_id")), is_active=True)
    db.add(sh)
    db.flush()
    log_action(db, user, "create", "schedules", entity=sh, description=f"Shift {sh.name} created", request=request)
    db.commit()
    return redirect("/schedules/shifts", f"Shift {sh.name} created.")


@router.post("/shifts/{sid}/edit", include_in_schema=False)
async def edit_shift(sid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("schedules.update"))):
    sh = db.get(Shift, sid)
    if not sh:
        raise HTTPException(404, "Shift not found")
    form = await request.form()
    before = snapshot(sh)
    sh.name = (form.get("name") or sh.name).strip()
    sh.group = form.get("group") or sh.group
    try:
        sh.start_time = svc._parse_time(form.get("start_time") or sh.start_time.strftime("%H:%M"))
        sh.end_time = svc._parse_time(form.get("end_time") or sh.end_time.strftime("%H:%M"))
    except ValueError:
        return redirect("/schedules/shifts", "Invalid shift times.", "error")
    sh.manager_id = parse_int(form.get("manager_id"))
    sh.supervisor_id = parse_int(form.get("supervisor_id"))
    sh.is_active = parse_bool(form.get("is_active"))
    log_action(db, user, "update", "schedules", entity=sh, description=f"Shift {sh.name} updated", before=before,
               after=snapshot(sh), request=request)
    db.commit()
    return redirect("/schedules/shifts", "Shift updated.")


# ----------------------------------------------------------------------------- bulk teacher change
@router.get("/bulk-teacher-change", include_in_schema=False)
def bulk_form(request: Request, from_teacher_id: int | None = None, db: Session = Depends(get_db),
              user: User = Depends(require("schedules.assign", "schedules.update", any_of=True))):
    ctx = _base_context(db, user)
    ctx.update({"user": user, "from_teacher_id": from_teacher_id,
                "teacher_options": [(t.id, f"{t.full_name} ({db.query(func.count(Schedule.id)).filter(Schedule.teacher_id == t.id, Schedule.status == 'active').scalar()} schedules)")
                                    for t in ctx["teachers"]]})
    return render(request, "schedules/bulk_teacher.html", ctx)


@router.post("/bulk-teacher-change", include_in_schema=False)
async def bulk_apply(request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("schedules.assign", "schedules.update", any_of=True))):
    form = await request.form()
    frm = db.get(Teacher, parse_int(form.get("from_teacher_id")) or 0)
    to = db.get(Teacher, parse_int(form.get("to_teacher_id")) or 0)
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if not frm or not to:
        return redirect("/schedules/bulk-teacher-change", "Select both the current and the new teacher.", "error")
    if not reason:
        return redirect(f"/schedules/bulk-teacher-change?from_teacher_id={frm.id}", "A reason is required for a teacher change.", "error")
    start = parse_date(form.get("start_date"), date.today())
    end = parse_date(form.get("end_date"))
    try:
        result = svc.bulk_teacher_change(db, user, frm, to, start, end, reason, request=request)
    except ValueError as exc:
        return redirect(f"/schedules/bulk-teacher-change?from_teacher_id={frm.id}", str(exc), "error")
    db.commit()
    msg = (f"{result['sessions']} session(s) and {result['schedules']} schedule(s) moved from {frm.full_name} to {to.full_name}; "
           f"parents and teachers notified.")
    if result["skipped"]:
        msg += f" Skipped {len(result['skipped'])} slot(s) due to conflicts: {', '.join(result['skipped'][:4])}."
    return redirect("/schedules?view=list", msg, "warning" if result["skipped"] else "success")


# ----------------------------------------------------------------------------- create
@router.get("/new", include_in_schema=False)
def new_schedule(request: Request, student_id: int | None = None, teacher_id: int | None = None, day: int | None = None,
                 slot: str = "", db: Session = Depends(get_db), user: User = Depends(require("schedules.add"))):
    ctx = _base_context(db, user)
    students = db.query(Student).filter(Student.status.in_(["active", "trial", "free"])).order_by(Student.full_name).all()
    ctx.update({"user": user, "mode": "new", "schedule": None,
                "student_options": [(s.id, f"{s.student_code} · {s.full_name} ({s.status})") for s in students],
                "subscription_options": [], "prefill": {"student_id": student_id, "teacher_id": teacher_id,
                                                        "days": [day] if day is not None else [], "start_time": slot}})
    return render(request, "schedules/form.html", ctx)


def _read_days(form) -> list[int]:
    out = []
    for d in range(7):
        if form.get(f"day_{d}"):
            out.append(d)
    return out


@router.post("/new", include_in_schema=False)
async def create_schedule(request: Request, db: Session = Depends(get_db), user: User = Depends(require("schedules.add"))):
    form = await request.form()
    student_id = parse_int(form.get("student_id"))
    teacher_id = parse_int(form.get("teacher_id"))
    back = f"/schedules/new?student_id={student_id or ''}&teacher_id={teacher_id or ''}"
    if not student_id or not teacher_id:
        return redirect(back, "Select both a student and a teacher.", "error")
    try:
        sch = svc.create_schedule(
            db, user, student_id=student_id, teacher_id=teacher_id, days=_read_days(form),
            start_time=form.get("start_time") or "17:00", duration=parse_int(form.get("duration"), 30) or 30,
            course_id=parse_int(form.get("course_id")), subscription_id=parse_int(form.get("subscription_id")),
            shift_id=parse_int(form.get("shift_id")), start_date=parse_date(form.get("start_date"), date.today()),
            end_date=parse_date(form.get("end_date")), is_trial=parse_bool(form.get("is_trial")),
            notes=form.get("notes"), request=request)
    except ValueError as exc:
        return redirect(back, str(exc), "error")
    db.commit()
    return redirect(f"/schedules/{sch.id}", f"Schedule created and the next 14 days of classes were generated.")


# ----------------------------------------------------------------------------- detail / edit
@router.get("/{id}", include_in_schema=False)
def schedule_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("schedules.view"))):
    sch = _get(db, id)
    upcoming = (db.query(ClassSession).filter(ClassSession.schedule_id == sch.id, ClassSession.date >= date.today())
                .order_by(ClassSession.scheduled_start).limit(20).all())
    history = (db.query(ClassSession).filter(ClassSession.schedule_id == sch.id, ClassSession.date < date.today())
               .order_by(ClassSession.scheduled_start.desc()).limit(20).all())
    counts = dict(db.query(ClassSession.status, func.count(ClassSession.id))
                  .filter(ClassSession.schedule_id == sch.id).group_by(ClassSession.status).all())
    from app.models.core import AuditEvent
    events = (db.query(AuditEvent).filter(AuditEvent.entity_type == "Schedule", AuditEvent.entity_id == sch.id)
              .order_by(AuditEvent.created_at.desc()).limit(30).all())
    return render(request, "schedules/detail.html", {
        "user": user, "s": sch, "upcoming": upcoming, "history": history, "counts": counts, "events": events,
        "day_label": svc.day_label(sch.days_of_week), "grid": svc.week_grid(db, student_id=sch.student_id),
        "day_names": svc.DAY_NAMES, "course_colors": svc.COURSE_COLORS})


@router.get("/{id}/edit", include_in_schema=False)
def edit_schedule_form(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("schedules.update"))):
    sch = _get(db, id)
    ctx = _base_context(db, user)
    subs = db.query(Subscription).filter(Subscription.student_id == sch.student_id).order_by(Subscription.id.desc()).all()
    ctx.update({"user": user, "mode": "edit", "schedule": sch,
                "student_options": [(sch.student_id, f"{sch.student.student_code} · {sch.student.full_name}" if sch.student else str(sch.student_id))],
                "subscription_options": [(s.id, f"{s.subscription_code} · {s.status}") for s in subs],
                "prefill": {"student_id": sch.student_id, "teacher_id": sch.teacher_id, "days": sch.days_of_week or [],
                            "start_time": sch.start_time.strftime("%H:%M")}})
    return render(request, "schedules/form.html", ctx)


@router.post("/{id}/edit", include_in_schema=False)
async def edit_schedule(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("schedules.update"))):
    sch = _get(db, id)
    form = await request.form()
    try:
        svc.update_schedule(db, user, sch, teacher_id=parse_int(form.get("teacher_id")) or sch.teacher_id,
                            days=_read_days(form), start_time=form.get("start_time") or sch.start_time.strftime("%H:%M"),
                            duration=parse_int(form.get("duration"), sch.duration_minutes) or 30,
                            course_id=parse_int(form.get("course_id")), subscription_id=parse_int(form.get("subscription_id")),
                            shift_id=parse_int(form.get("shift_id")), start_date=parse_date(form.get("start_date")),
                            end_date=parse_date(form.get("end_date")), notes=form.get("notes"),
                            reason=form.get("reason") or "Schedule maintenance", request=request)
    except ValueError as exc:
        return redirect(f"/schedules/{sch.id}/edit", str(exc), "error")
    db.commit()
    return redirect(f"/schedules/{sch.id}", "Schedule updated; future pending classes were regenerated.")


@router.post("/{id}/status", include_in_schema=False)
async def change_status(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("schedules.update"))):
    sch = _get(db, id)
    form = await request.form()
    status = form.get("status") or ""
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if status not in SCHEDULE_STATUSES:
        return redirect(f"/schedules/{sch.id}", "Invalid schedule status.", "error")
    if not reason:
        return redirect(f"/schedules/{sch.id}", "A reason is required to pause, end or resume a schedule.", "error")
    try:
        n = svc.change_schedule_status(db, user, sch, status, reason, request=request)
    except ValueError as exc:
        return redirect(f"/schedules/{sch.id}", str(exc), "error")
    db.commit()
    verb = {"active": "resumed", "paused": "paused", "ended": "ended"}[status]
    return redirect(f"/schedules/{sch.id}", f"Schedule {verb}; {n} future class(es) affected.", "warning" if status != "active" else "success")


@router.post("/{id}/generate", include_in_schema=False)
async def generate_now(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("schedules.update"))):
    sch = _get(db, id)
    form = await request.form()
    days = parse_int(form.get("days"), 14) or 14
    created = class_svc.generate_sessions(db, sch, date.today(), date.today() + timedelta(days=days))
    log_action(db, user, "create", "schedules", entity=sch, description=f"{len(created)} sessions generated for the next {days} days", request=request)
    db.commit()
    return redirect(f"/schedules/{sch.id}", f"{len(created)} class session(s) generated.")
