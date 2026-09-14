"""Class sessions (Module 3/9) and the live classroom (Module 24).

Every status transition goes through ``app.services.classes.set_status`` so audit, attendance and alerts stay consistent.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.core import rbac
from app.core.audit import log_action
from app.core.deps import PermissionDenied, UserContext, csrf_protect, get_user_context, require
from app.core.templating import render
from app.core.utils import back_url, paginate, parse_bool, parse_date, parse_datetime, parse_int, redirect
from app.database import get_db
from app.models.academic import Course, LessonPlan
from app.models.core import AuditEvent, User
from app.models.erp import (CLASS_QUERY_TYPES, ClassActivity, ClassArrangement, ClassQuery, RescheduleRequest,
                            SessionSlot)
from app.models.finance import Subscription
from app.models.people import Client, Employee, Student, Teacher
from app.models.scheduling import (Attendance, ClassSession, QAReview, Recording, Schedule, Shift, SESSION_STATUSES)
from app.services import arrangements as arr_svc
from app.services import classes as class_svc
from app.services import scheduling as svc

router = APIRouter(dependencies=[Depends(csrf_protect)])

ACTION_STATUSES = ["started", "done", "absent", "missed", "cancelled", "leave", "pending", "available"]
REASON_REQUIRED = {"missed", "absent", "cancelled", "rescheduled", "leave"}
# ERP "Status" filter on Class Schedules, in the ERP's own order and vocabulary.
CLASS_STATUS_OPTIONS = [("cancelled", "Cancelled"), ("done", "Done"), ("missed", "Missed"), ("pending", "Pending"),
                        ("started", "Started"), ("absent", "Student Absent"), ("leave", "Student On-Leave"),
                        ("available", "Teacher is Available")]
TILE_STATUSES = ["pending", "available", "started", "done", "missed", "absent", "leave", "cancelled", "rescheduled"]
ARRANGEMENT_STATUS_OPTIONS = [("active", "Active"), ("inactive", "In-Active")]


def _get(db: Session, id: int) -> ClassSession:
    s = db.get(ClassSession, id)
    if not s:
        raise HTTPException(404, "Class session not found")
    return s


def _filters(db: Session, user: User) -> dict:
    return {
        "status_options": SESSION_STATUSES,
        "teacher_options": [(t.id, t.full_name) for t in svc.teachers_for(db, user)],
        "course_options": [(c.id, c.name) for c in db.query(Course).filter(Course.is_active.is_(True)).order_by(Course.order)],
        "shift_options": [(s.id, s.name) for s in db.query(Shift).filter(Shift.is_active.is_(True)).order_by(Shift.start_time)],
    }


def _erp_filters(db: Session, user: User) -> dict:
    """Filter-bar options shared by every ERP class-management page (3.6)."""
    teachers = svc.teachers_for(db, user)
    return {
        "class_status_options": CLASS_STATUS_OPTIONS,
        "teacher_options": [(t.id, t.full_name) for t in teachers],
        # ClassSession.done_by_teacher_id / substitute_for_teacher_id have no relationship on the model
        "teacher_names": {t.id: t.full_name for t in db.query(Teacher).all()},
        "course_options": [(c.id, c.name) for c in db.query(Course).filter(Course.is_active.is_(True)).order_by(Course.order, Course.name)],
        "student_options": [(s.id, f"{s.full_name} ({s.student_code})") for s in
                            db.query(Student).filter(Student.status.in_(["active", "trial", "frozen"])).order_by(Student.full_name)],
        "slot_options": svc.slot_options(db),
        "category_options": svc.SLOT_CATEGORIES,
        "method_options": svc.COURSE_METHODS,
        "arrangement_status_options": ARRANGEMENT_STATUS_OPTIONS,
        "query_type_options": CLASS_QUERY_TYPES,
        "day_names": svc.DAY_NAMES,
    }


def _erp_query(db: Session, *, date_from=None, date_to=None, course_id=None, session_category="", slot_id=None,
               status="", student_id=None, teacher_id=None, course_method="", done_by_teacher_id=None,
               teacher_ids=None, q=""):
    """Class Schedules query with the ERP's filter set (3.6)."""
    qry = db.query(ClassSession)
    if date_from:
        qry = qry.filter(ClassSession.date >= date_from)
    if date_to:
        qry = qry.filter(ClassSession.date <= date_to)
    if course_id:
        qry = qry.filter(ClassSession.course_id == course_id)
    if session_category:
        qry = qry.filter(ClassSession.duration_minutes == svc.minutes_for_category(session_category))
    if slot_id:
        slot = db.get(SessionSlot, slot_id)
        if slot:
            qry = qry.filter(ClassSession.start_time == slot.start_time)
    if status:
        qry = qry.filter(ClassSession.status == status)
    if student_id:
        qry = qry.filter(ClassSession.student_id == student_id)
    if teacher_id:
        qry = qry.filter(ClassSession.teacher_id == teacher_id)
    if done_by_teacher_id:
        qry = qry.filter(ClassSession.done_by_teacher_id == done_by_teacher_id)
    if course_method:
        qry = qry.join(Subscription, Subscription.id == ClassSession.subscription_id).filter(
            Subscription.course_method == course_method)
    if teacher_ids is not None:
        qry = qry.filter(ClassSession.teacher_id.in_(teacher_ids or [-1]))
    if q:
        like = f"%{q}%"
        qry = qry.join(Student, Student.id == ClassSession.student_id).filter(
            Student.full_name.ilike(like) | Student.student_code.ilike(like) | ClassSession.room_name.ilike(like))
    return qry


# ----------------------------------------------------------------------------- Class Schedules (3.6)
@router.get("/classes", include_in_schema=False)
def list_classes(request: Request, page: int = 1, date: str = "", date_from: str = "", date_to: str = "", status: str = "",
                 teacher_id: int | None = None, student_id: int | None = None, course_id: int | None = None,
                 shift_id: int | None = None, slot_id: int | None = None, session_category: str = "",
                 course_method: str = "", done_by_teacher_id: int | None = None, trial: str = "", q: str = "",
                 db: Session = Depends(get_db), user: User = Depends(require("classes.view")),
                 ctx: UserContext = Depends(get_user_context)):
    from datetime import date as _date
    today = _date.today()
    day = parse_date(date)
    df = parse_date(date_from) or day or today
    dt = parse_date(date_to) or day or today
    if dt < df:
        df, dt = dt, df
    teacher_ids = svc.scoped_teacher_ids(db, user)
    common = dict(date_from=df, date_to=dt, course_id=course_id, session_category=session_category, slot_id=slot_id,
                  student_id=student_id, teacher_id=teacher_id, course_method=course_method,
                  done_by_teacher_id=done_by_teacher_id, teacher_ids=teacher_ids, q=q)
    counters = class_svc.counts_by_status(_erp_query(db, **common))
    query = _erp_query(db, status=status, **common)
    if trial:
        query = query.filter(ClassSession.is_trial.is_(parse_bool(trial)))
    pg = paginate(query.order_by(ClassSession.scheduled_start.desc(), ClassSession.id.desc()), page, 40)
    qs = (f"date_from={df}&date_to={dt}&course_id={course_id or ''}&session_category={session_category}"
          f"&slot_id={slot_id or ''}&student_id={student_id or ''}&teacher_id={teacher_id or ''}"
          f"&course_method={course_method}&done_by_teacher_id={done_by_teacher_id or ''}&trial={trial}&q={q}")
    return render(request, "classes/list.html", {
        "user": user, "page": pg, "counters": counters, "base_url": f"/classes?{qs}&status={status}", "tile_base": f"/classes?{qs}",
        "day": day, "date_from": df, "date_to": dt, "status": status, "teacher_id": teacher_id, "student_id": student_id,
        "course_id": course_id, "shift_id": shift_id, "slot_id": slot_id, "session_category": session_category,
        "course_method": course_method, "done_by_teacher_id": done_by_teacher_id, "trial": trial, "q": q,
        "date_value": date, "tile_statuses": TILE_STATUSES, "reason_required": sorted(REASON_REQUIRED),
        "status_options": SESSION_STATUSES, "shift_options": _filters(db, user)["shift_options"], **_erp_filters(db, user)})


@router.get("/classes/day", include_in_schema=False)
def day_view(request: Request, date: str = "", db: Session = Depends(get_db), user: User = Depends(require("classes.view"))):
    from datetime import date as _date
    day = parse_date(date, _date.today())
    teacher_ids = svc.scoped_teacher_ids(db, user)
    rows = svc.sessions_query(db, day=day, teacher_ids=teacher_ids).order_by(ClassSession.scheduled_start).all()
    by_teacher: dict[int, dict] = {}
    for s in rows:
        d = by_teacher.setdefault(s.teacher_id, {"teacher": s.teacher, "slots": {}})
        d["slots"].setdefault(s.start_time.strftime("%H:%M"), []).append(s)
    slots = sorted({s.start_time.strftime("%H:%M") for s in rows})
    return render(request, "classes/day.html", {
        "user": user, "day": day, "slots": slots, "prev_day": day - timedelta(days=1), "next_day": day + timedelta(days=1), "teachers": sorted(by_teacher.values(), key=lambda x: (x["teacher"].full_name if x["teacher"] else "")),
        "counters": class_svc.counters_for_date(db, day, teacher_ids), "total": len(rows)})


@router.post("/classes/bulk-status", include_in_schema=False)
async def bulk_status(request: Request, db: Session = Depends(get_db), user: User = Depends(require("classes.update"))):
    form = await request.form()
    ids = [int(i) for i in form.getlist("session_ids") if str(i).isdigit()]
    status = form.get("status") or ""
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    target = form.get("back") or "/classes"
    if not ids:
        return redirect(target, "Select at least one class.", "error")
    if status not in SESSION_STATUSES:
        return redirect(target, "Choose a valid status.", "error")
    if status in REASON_REQUIRED and not reason:
        return redirect(target, f"A reason is required to mark classes as {status}.", "error")
    teacher_ids = svc.scoped_teacher_ids(db, user)
    n = 0
    for s in db.query(ClassSession).filter(ClassSession.id.in_(ids)).all():
        if teacher_ids is not None and s.teacher_id not in teacher_ids:
            continue
        class_svc.set_status(db, s, status, user, reason=reason or None, request=request)
        n += 1
    db.commit()
    return redirect(target, f"{n} class(es) marked {status}.")


@router.post("/classes/generate", include_in_schema=False)
async def generate_range(request: Request, db: Session = Depends(get_db), user: User = Depends(require("classes.add", "schedules.update", any_of=True))):
    form = await request.form()
    start = parse_date(form.get("start_date"), date.today())
    end = parse_date(form.get("end_date"), date.today() + timedelta(days=14))
    if end < start:
        return redirect("/classes", "The end date must be on or after the start date.", "error")
    if (end - start).days > 120:
        return redirect("/classes", "Generate at most 120 days at a time.", "error")
    total = 0
    for sch in db.query(Schedule).filter(Schedule.status == "active").all():
        total += len(class_svc.generate_sessions(db, sch, start, end))
    log_action(db, user, "create", "classes", entity_type="Schedule", description=f"Generated {total} class sessions for {start} to {end}", request=request)
    db.commit()
    return redirect(f"/classes?date_from={start}&date_to={end}", f"{total} class session(s) generated for {start} to {end}.")


# ----------------------------------------------------------------------------- Class Schedules actions
@router.post("/classes/reschedule", include_in_schema=False)
async def request_reschedule(request: Request, db: Session = Depends(get_db), user: User = Depends(require("classes.update"))):
    """Action bar -> Reschedule: raises a pending RescheduleRequest for one class."""
    form = await request.form()
    back = form.get("back") or "/classes"
    s = db.get(ClassSession, parse_int(form.get("session_id")) or 0)
    if not s:
        return redirect(back, "Choose a class to reschedule.", "error")
    teacher_ids = svc.scoped_teacher_ids(db, user)
    if teacher_ids is not None and s.teacher_id not in teacher_ids:
        raise PermissionDenied("classes.update (out of scope)")
    new_date = parse_date(form.get("new_date"))
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if not new_date:
        return redirect(back, "Choose the new working date.", "error")
    slot = db.get(SessionSlot, parse_int(form.get("new_slot_id")) or 0) if form.get("new_slot_id") else None
    new_teacher_id = parse_int(form.get("new_teacher_id")) or None
    try:
        rr = arr_svc.create_reschedule_request(db, user, s, new_date, slot, None, new_teacher_id, reason, request=request)
    except ValueError as exc:
        return redirect(back, str(exc), "error")
    if s.teacher and s.teacher.user_id:
        from app.core.notify import notify
        notify(db, s.teacher.user_id, "Reschedule requested",
               f"Class with {s.student.full_name if s.student else 'a student'} on {s.date} is proposed for {new_date}. {reason}",
               event_type="class_status", link="/classes/rescheduled")
    db.commit()
    return redirect("/classes/rescheduled?status=pending", f"Reschedule request #{rr.id} raised and waiting for approval.")


@router.post("/classes/auto-arrange", include_in_schema=False)
async def auto_arrange(request: Request, db: Session = Depends(get_db), user: User = Depends(require("classes.update"))):
    """Action bar -> Auto Arrangement: cover every absent / on-leave teacher in a date range."""
    form = await request.form()
    start = parse_date(form.get("date_from"), date.today())
    end = parse_date(form.get("date_to"), start + timedelta(days=6))
    if end < start:
        return redirect("/classes/arrangements", "The To date must be on or after the From date.", "error")
    if (end - start).days > 60:
        return redirect("/classes/arrangements", "Auto-arrange at most 60 days at a time.", "error")
    created = arr_svc.auto_arrange(db, user, start, end, request=request)
    moved = sum(a.applied_count or 0 for a in created)
    db.commit()
    if not created:
        return redirect(f"/classes/arrangements?date_from={start}&date_to={end}",
                        "No absent or on-leave teacher with pending classes was found in that range.", "info")
    return redirect(f"/classes/arrangements?date_from={start}&date_to={end}",
                    f"{len(created)} automatic arrangement(s) created; {moved} class(es) reassigned.")


@router.get("/classes/absent-teachers", include_in_schema=False)
def absent_teachers(request: Request, date_from: str = "", date_to: str = "", db: Session = Depends(get_db),
                    user: User = Depends(require("classes.view"))):
    """Action bar -> View Absent Teachers: teachers on approved leave or marked absent in the range."""
    start = parse_date(date_from, date.today())
    end = parse_date(date_to, start + timedelta(days=6))
    rows = arr_svc.absent_teachers(db, start, end)
    return render(request, "classes/absent_teachers.html", {
        "user": user, "rows": rows, "date_from": start, "date_to": end,
        "total_pending": sum(r["pending"] for r in rows)})


# ----------------------------------------------------------------------------- Class Arrangements (3.6)
@router.get("/classes/arrangements", include_in_schema=False)
def arrangements(request: Request, page: int = 1, date_from: str = "", date_to: str = "", session_category: str = "",
                 slot_id: int | None = None, from_teacher_id: int | None = None, to_teacher_id: int | None = None,
                 status: str = "", db: Session = Depends(get_db), user: User = Depends(require("classes.view"))):
    today = date.today()
    df = parse_date(date_from, today - timedelta(days=30))
    dt = parse_date(date_to, today + timedelta(days=30))
    q = db.query(ClassArrangement).filter(ClassArrangement.from_date <= dt, ClassArrangement.to_date >= df)
    if session_category:
        q = q.filter(ClassArrangement.session_category == session_category)
    if slot_id:
        q = q.filter(ClassArrangement.slot_id == slot_id)
    if from_teacher_id:
        q = q.filter(ClassArrangement.from_teacher_id == from_teacher_id)
    if to_teacher_id:
        q = q.filter(ClassArrangement.to_teacher_id == to_teacher_id)
    if status:
        q = q.filter(ClassArrangement.status == status)
    pg = paginate(q.order_by(ClassArrangement.from_date.desc(), ClassArrangement.id.desc()), page, 30)
    all_rows = db.query(ClassArrangement).filter(ClassArrangement.from_date <= dt, ClassArrangement.to_date >= df).all()
    base = (f"/classes/arrangements?date_from={df}&date_to={dt}&session_category={session_category}&slot_id={slot_id or ''}"
            f"&from_teacher_id={from_teacher_id or ''}&to_teacher_id={to_teacher_id or ''}")
    return render(request, "classes/arrangements.html", {
        "user": user, "page": pg, "date_from": df, "date_to": dt, "session_category": session_category, "slot_id": slot_id,
        "from_teacher_id": from_teacher_id, "to_teacher_id": to_teacher_id, "status": status,
        "base_url": f"{base}&status={status}", "tile_base": base,
        "tiles": {"active": sum(1 for a in all_rows if a.status == "active"),
                  "inactive": sum(1 for a in all_rows if a.status != "active"),
                  "auto": sum(1 for a in all_rows if a.is_auto),
                  "applied": sum(a.applied_count or 0 for a in all_rows)},
        **_erp_filters(db, user)})


@router.post("/classes/arrangements/new", include_in_schema=False)
async def create_arrangement(request: Request, db: Session = Depends(get_db), user: User = Depends(require("classes.update"))):
    form = await request.form()
    back = form.get("back") or "/classes/arrangements"
    from_id, to_id = parse_int(form.get("from_teacher_id")), parse_int(form.get("to_teacher_id"))
    start = parse_date(form.get("from_date"))
    end = parse_date(form.get("to_date"), start)
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if not (from_id and to_id and start and end):
        return redirect(back, "From teacher, to teacher and the date range are all required.", "error")
    if end < start:
        return redirect(back, "The To date must be on or after the From date.", "error")
    if not reason:
        return redirect(back, "A reason is required for a class arrangement.", "error")
    arr = ClassArrangement(from_teacher_id=from_id, to_teacher_id=to_id, from_date=start, to_date=end,
                           session_category=form.get("session_category") or None,
                           slot_id=parse_int(form.get("slot_id")) or None, reason=reason[:200], status="active",
                           is_auto=False, created_by_id=user.id)
    db.add(arr)
    db.flush()
    try:
        moved = arr_svc.apply_arrangement(db, user, arr, request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(back, str(exc), "error")
    db.commit()
    return redirect(back, f"Arrangement #{arr.id} created; {moved} class(es) reassigned and both teachers notified.")


@router.post("/classes/arrangements/{aid}/status", include_in_schema=False)
async def arrangement_status(aid: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("classes.update"))):
    arr = db.get(ClassArrangement, aid)
    if not arr:
        raise HTTPException(404, "Arrangement not found")
    form = await request.form()
    back = form.get("back") or "/classes/arrangements"
    status = form.get("status") or ""
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if status not in ("active", "inactive"):
        return redirect(back, "Choose Active or In-Active.", "error")
    if status == arr.status:
        return redirect(back, f"Arrangement #{arr.id} is already {status}.", "info")
    if reason:
        arr.reason = reason[:200]
    if status == "inactive":
        n = arr_svc.revert_arrangement(db, user, arr, request=request)
        msg = f"Arrangement #{arr.id} set In-Active; {n} future class(es) returned to the original teacher."
    else:
        n = arr_svc.apply_arrangement(db, user, arr, request=request)
        msg = f"Arrangement #{arr.id} re-activated; {n} class(es) reassigned."
    db.commit()
    return redirect(back, msg)


# ----------------------------------------------------------------------------- Rescheduled Classes (3.6)
@router.get("/classes/rescheduled", include_in_schema=False)
def rescheduled(request: Request, page: int = 1, status: str = "", date_from: str = "", date_to: str = "",
                teacher_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(require("classes.view"))):
    df, dt = parse_date(date_from), parse_date(date_to)
    q = db.query(RescheduleRequest)
    if df:
        q = q.filter(RescheduleRequest.new_date >= df)
    if dt:
        q = q.filter(RescheduleRequest.new_date <= dt)
    if teacher_id:
        q = q.filter((RescheduleRequest.old_teacher_id == teacher_id) | (RescheduleRequest.new_teacher_id == teacher_id))
    tiles = {s: 0 for s in ("pending", "approved", "rejected", "cancelled")}
    for st, n in q.with_entities(RescheduleRequest.status, func.count(RescheduleRequest.id)).group_by(RescheduleRequest.status):
        tiles[st] = n
    if status:
        q = q.filter(RescheduleRequest.status == status)
    pg = paginate(q.order_by(RescheduleRequest.id.desc()), page, 30)
    base = f"/classes/rescheduled?date_from={date_from}&date_to={date_to}&teacher_id={teacher_id or ''}"
    return render(request, "classes/rescheduled.html", {
        "user": user, "page": pg, "status": status, "tiles": tiles, "date_from": df, "date_to": dt,
        "teacher_id": teacher_id, "base_url": f"{base}&status={status}", "tile_base": base,
        "approval_statuses": [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"),
                              ("cancelled", "Cancelled")], **_erp_filters(db, user)})


@router.post("/classes/rescheduled/update-status", include_in_schema=False)
async def update_reschedule_status(request: Request, db: Session = Depends(get_db),
                                   user: User = Depends(require("classes.update"))):
    form = await request.form()
    back = form.get("back") or "/classes/rescheduled"
    ids = [int(i) for i in form.getlist("request_ids") if str(i).isdigit()]
    status = form.get("status") or ""
    comments = (form.get("comments") or form.get("rationale") or "").strip()
    if not ids:
        return redirect(back, "Select at least one reschedule request.", "error")
    if status not in ("approved", "rejected", "cancelled"):
        return redirect(back, "Choose Approved, Rejected or Cancelled.", "error")
    if status != "approved" and not comments:
        return redirect(back, f"Comments are required to mark a request {status}.", "error")
    done, errors = 0, []
    for rr in db.query(RescheduleRequest).filter(RescheduleRequest.id.in_(ids)).all():
        try:
            arr_svc.decide_reschedule(db, user, rr, status, comments, request=request)
            done += 1
        except ValueError as exc:
            errors.append(f"#{rr.id}: {exc}")
    db.commit()
    msg = f"{done} reschedule request(s) marked {status}."
    if errors:
        return redirect(back, msg + " " + " ".join(errors), "warning" if done else "error")
    return redirect(back, msg)


# ----------------------------------------------------------------------------- Class Status Summary (3.6)
@router.get("/classes/status-summary", include_in_schema=False)
def status_summary(request: Request, date: str = "", employee_id: int | None = None, session_category: str = "",
                   slot_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(require("classes.view"))):
    from datetime import date as _date
    day = parse_date(date, _date.today())
    teacher_ids = svc.scoped_teacher_ids(db, user)
    if employee_id:
        teacher_ids = [employee_id] if (teacher_ids is None or employee_id in teacher_ids) else [-1]
    grid = class_svc.status_grid(db, day, teacher_ids, session_category or None, slot_id)
    return render(request, "classes/status_summary.html", {
        "user": user, "grid": grid, "day": day, "employee_id": employee_id, "session_category": session_category,
        "slot_id": slot_id, "highlights": class_svc.DURATION_HIGHLIGHTS,
        "base_url": f"/classes/status-summary?date={day}&employee_id={employee_id or ''}&session_category={session_category}&slot_id={slot_id or ''}",
        **_erp_filters(db, user)})


# ----------------------------------------------------------------------------- Class Queries (3.6)
@router.get("/classes/queries", include_in_schema=False)
def class_queries(request: Request, page: int = 1, status: str = "", teacher_id: int | None = None,
                  date_from: str = "", date_to: str = "", db: Session = Depends(get_db),
                  user: User = Depends(require("classes.view")), ctx: UserContext = Depends(get_user_context)):
    df, dt = parse_date(date_from), parse_date(date_to)
    teacher_ids = svc.scoped_teacher_ids(db, user)
    q = db.query(ClassQuery)
    if teacher_ids is not None:
        q = q.filter(ClassQuery.teacher_id.in_(teacher_ids or [-1]))
    if df:
        q = q.filter(func.date(ClassQuery.created_at) >= df)
    if dt:
        q = q.filter(func.date(ClassQuery.created_at) <= dt)
    if teacher_id:
        q = q.filter(ClassQuery.teacher_id == teacher_id)
    tiles = {"pending": 0, "closed": 0}
    for st, n in q.with_entities(ClassQuery.status, func.count(ClassQuery.id)).group_by(ClassQuery.status):
        tiles[st] = n
    if status:
        q = q.filter(ClassQuery.status == status)
    pg = paginate(q.order_by(ClassQuery.id.desc()), page, 30)
    base = f"/classes/queries?teacher_id={teacher_id or ''}&date_from={date_from}&date_to={date_to}"
    return render(request, "classes/queries.html", {
        "user": user, "page": pg, "status": status, "tiles": tiles, "teacher_id": teacher_id, "date_from": df, "date_to": dt,
        "base_url": f"{base}&status={status}", "tile_base": base,
        "session_options": [(s.id, f"#{s.id} {s.date} {s.start_time.strftime('%H:%M')} - {s.student.full_name if s.student else ''}")
                            for s in db.query(ClassSession).order_by(ClassSession.scheduled_start.desc()).limit(150)],
        **_erp_filters(db, user)})


@router.post("/classes/queries/new", include_in_schema=False)
async def create_query(request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("classes.update", "classes.view", any_of=True)),
                       ctx: UserContext = Depends(get_user_context)):
    form = await request.form()
    back = form.get("back") or "/classes/queries"
    session = db.get(ClassSession, parse_int(form.get("session_id")) or 0) if form.get("session_id") else None
    query_type = form.get("query_type") or ""
    detail = (form.get("detail") or "").strip()
    if query_type not in CLASS_QUERY_TYPES:
        return redirect(back, "Choose a class query type.", "error")
    if not detail:
        return redirect(back, "Describe the query.", "error")
    teacher_id = (ctx.teacher.id if ctx.teacher else None) or (session.teacher_id if session else None) or parse_int(form.get("teacher_id"))
    cq = ClassQuery(session_id=session.id if session else None, teacher_id=teacher_id,
                    student_id=session.student_id if session else None, query_type=query_type, detail=detail, status="pending")
    db.add(cq)
    db.flush()
    log_action(db, user, "create", "classes", entity=cq,
               description=f"Class query raised ({query_type})" + (f" for class #{session.id}" if session else ""),
               rationale=detail, request=request)
    for u in db.query(User).join(Teacher, Teacher.supervisor_id == User.id).filter(Teacher.id == teacher_id).distinct().all():
        from app.core.notify import notify
        notify(db, u.id, "Class query raised", f"{query_type}: {detail[:160]}", event_type="class_status", link="/classes/queries")
    db.commit()
    return redirect(back, f"Class query #{cq.id} raised.")


@router.post("/classes/queries/{qid}/close", include_in_schema=False)
async def close_query(qid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("classes.update"))):
    cq = db.get(ClassQuery, qid)
    if not cq:
        raise HTTPException(404, "Class query not found")
    form = await request.form()
    back = form.get("back") or "/classes/queries"
    response = (form.get("response") or form.get("rationale") or "").strip()
    if not response:
        return redirect(back, "A response is required to close a class query.", "error")
    cq.status = "closed"
    cq.response = response
    cq.closed_by_id = user.id
    cq.closed_at = datetime.utcnow()
    log_action(db, user, "status_change", "classes", entity=cq, description=f"Class query #{cq.id} closed",
               rationale=response, before={"status": "pending"}, after={"status": "closed"}, request=request)
    if cq.teacher and cq.teacher.user_id:
        from app.core.notify import notify
        notify(db, cq.teacher.user_id, "Class query closed", response[:300], event_type="class_status", link="/classes/queries")
    db.commit()
    return redirect(back, f"Class query #{cq.id} closed.")


# ----------------------------------------------------------------------------- Schedule Summary Report (3.6)
@router.get("/classes/schedule-summary", include_in_schema=False)
def schedule_summary(request: Request, employee_id: int | None = None, session_category: str = "30 Minutes",
                     db: Session = Depends(get_db), user: User = Depends(require("classes.view"))):
    teacher_ids = svc.scoped_teacher_ids(db, user)
    if employee_id:
        teacher_ids = [employee_id] if (teacher_ids is None or employee_id in teacher_ids) else [-1]
    report = class_svc.schedule_summary(db, session_category or None, teacher_ids)
    return render(request, "classes/schedule_summary.html", {
        "user": user, "report": report, "employee_id": employee_id, "session_category": session_category,
        "base_url": f"/classes/schedule-summary?employee_id={employee_id or ''}&session_category={session_category}",
        **_erp_filters(db, user)})


# ----------------------------------------------------------------------------- detail
@router.get("/classes/{id}", include_in_schema=False)
def class_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("classes.view")),
                 ctx: UserContext = Depends(get_user_context)):
    s = _get(db, id)
    teacher_ids = svc.scoped_teacher_ids(db, user)
    if teacher_ids is not None and s.teacher_id not in teacher_ids:
        raise PermissionDenied("classes.view (out of scope)")
    plan = db.query(LessonPlan).filter(LessonPlan.session_id == s.id).first()
    if not plan:
        plan = db.query(LessonPlan).filter(LessonPlan.student_id == s.student_id, LessonPlan.plan_date == s.date).first()
    events = (db.query(AuditEvent).filter(AuditEvent.entity_type == "ClassSession", AuditEvent.entity_id == s.id)
              .order_by(AuditEvent.created_at.desc()).limit(30).all())
    return render(request, "classes/detail.html", {
        "user": user, "s": s, "plan": plan, "events": events,
        "attendance": db.query(Attendance).filter(Attendance.session_id == s.id).first(),
        "reviews": db.query(QAReview).filter(QAReview.session_id == s.id).order_by(QAReview.id.desc()).all(),
        "rescheduled_to": db.get(ClassSession, s.rescheduled_to_id) if s.rescheduled_to_id else None,
        "action_statuses": ACTION_STATUSES, "reason_required": sorted(REASON_REQUIRED),
        "attendance_pct": class_svc.student_attendance_pct(db, s.student_id),
        "activities": db.query(ClassActivity).filter(ClassActivity.session_id == s.id).order_by(ClassActivity.id.desc()).all(),
        "queries": db.query(ClassQuery).filter(ClassQuery.session_id == s.id).order_by(ClassQuery.id.desc()).all(),
        "reschedules": db.query(RescheduleRequest).filter(RescheduleRequest.session_id == s.id).order_by(RescheduleRequest.id.desc()).all(),
        "highlight": class_svc.duration_highlight(s.actual_duration_minutes if s.status == "done" else None),
        **_erp_filters(db, user),
        "teacher_stats": class_svc.teacher_stats(db, s.teacher_id, date.today() - timedelta(days=30))})


@router.post("/classes/{id}/status", include_in_schema=False)
async def change_status(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("classes.update"))):
    s = _get(db, id)
    form = await request.form()
    status = form.get("status") or ""
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if status not in SESSION_STATUSES:
        return redirect(f"/classes/{s.id}", "Invalid status.", "error")
    if status in REASON_REQUIRED and not reason:
        return redirect(f"/classes/{s.id}", f"A reason is required to mark this class {status}.", "error")
    teacher_ids = svc.scoped_teacher_ids(db, user)
    if teacher_ids is not None and s.teacher_id not in teacher_ids:
        raise PermissionDenied("classes.update (out of scope)")
    class_svc.set_status(db, s, status, user, reason=reason or None, request=request)
    db.commit()
    return redirect(f"/classes/{s.id}", f"Class marked {status}.")


@router.post("/classes/{id}/reschedule", include_in_schema=False)
async def reschedule(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("classes.update"))):
    s = _get(db, id)
    form = await request.form()
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    when = parse_datetime(form.get("new_start"))
    if not when:
        d = parse_date(form.get("new_date"))
        t = form.get("new_time") or ""
        if d and t:
            try:
                when = datetime.combine(d, svc._parse_time(t))
            except ValueError:
                when = None
    if not when:
        return redirect(f"/classes/{s.id}", "Choose a new date and time for the class.", "error")
    if not reason:
        return redirect(f"/classes/{s.id}", "A reason is required to reschedule a class.", "error")
    try:
        new = svc.reschedule_session(db, user, s, when, reason, request=request)
    except ValueError as exc:
        return redirect(f"/classes/{s.id}", str(exc), "error")
    db.commit()
    return redirect(f"/classes/{new.id}", f"Class rescheduled to {when.strftime('%d %b %Y %H:%M')}; parties notified.")


@router.post("/classes/{id}/notes", include_in_schema=False)
async def save_notes(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("classes.update"))):
    s = _get(db, id)
    form = await request.form()
    s.teacher_notes = form.get("teacher_notes") or None
    log_action(db, user, "update", "classes", entity=s, description="Class notes updated", request=request)
    db.commit()
    return redirect(f"/classes/{s.id}", "Notes saved.")


@router.post("/classes/{id}/available", include_in_schema=False)
async def mark_teacher_available(id: int, request: Request, db: Session = Depends(get_db),
                                 user: User = Depends(require("classes.update"))):
    """"Teacher is Available": status -> available with teacher_available_at, the family is told the teacher is waiting."""
    s = _get(db, id)
    form = await request.form()
    back = form.get("back") or f"/classes/{s.id}"
    teacher_ids = svc.scoped_teacher_ids(db, user)
    if teacher_ids is not None and s.teacher_id not in teacher_ids:
        raise PermissionDenied("classes.update (out of scope)")
    try:
        class_svc.mark_available(db, s, user, request=request)
    except ValueError as exc:
        return redirect(back, str(exc), "error")
    db.commit()
    return redirect(back, "Marked Teacher is Available; the family has been notified.")


# ----------------------------------------------------------------------------- live classroom (Module 24)
def _classroom_access(db: Session, s: ClassSession, user: User, ctx: UserContext, request: Request) -> str:
    """Return the viewer side ('teacher' | 'student' | 'observer') or raise. Denied attempts are audit-logged."""
    if ctx.teacher:
        if s.teacher_id == ctx.teacher.id:
            return "teacher"
    elif ctx.student:
        if s.student_id == ctx.student.id:
            return "student"
    elif ctx.client:
        if s.student_id in (ctx.student_ids or []):
            return "student"
    elif rbac.has_permission(user, "classes.view"):
        return "observer"
    log_action(db, user, "access_denied", "classes", entity=s, severity="warning", consequential=True,
               description=f"Blocked classroom access to session #{s.id} ({s.room_name})", request=request)
    db.commit()
    raise PermissionDenied("classroom access")


@router.get("/classroom/{session_id}", include_in_schema=False)
def classroom(session_id: int, request: Request, db: Session = Depends(get_db),
              user: User = Depends(require("classes.view", "portal_teacher.view", "portal_client.view", "portal_student.view", any_of=True)),
              ctx: UserContext = Depends(get_user_context)):
    s = _get(db, session_id)
    side = _classroom_access(db, s, user, ctx, request)
    plan = db.query(LessonPlan).filter(LessonPlan.session_id == s.id).first()
    if not plan:
        plan = db.query(LessonPlan).filter(LessonPlan.student_id == s.student_id, LessonPlan.plan_date == s.date).first()
    recent = (db.query(ClassSession).filter(ClassSession.student_id == s.student_id, ClassSession.id != s.id,
                                            ClassSession.status == "done")
              .order_by(ClassSession.scheduled_start.desc()).limit(5).all())
    return render(request, "classroom/room.html", {
        "user": user, "s": s, "side": side, "plan": plan, "recent": recent,
        "provider": settings.VIDEO_PROVIDER, "jitsi_domain": settings.JITSI_DOMAIN,
        "display_name": user.full_name, "room": s.room_name or class_svc.room_name_for(s),
        "can_end": side == "teacher" or rbac.has_permission(user, "classes.update")})


@router.post("/classroom/{session_id}/join", include_in_schema=False)
def classroom_join(session_id: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("classes.view", "portal_teacher.view", "portal_client.view", "portal_student.view", any_of=True)),
                   ctx: UserContext = Depends(get_user_context)):
    s = _get(db, session_id)
    side = _classroom_access(db, s, user, ctx, request)
    if side in ("teacher", "student"):
        class_svc.mark_join(db, s, side)
        log_action(db, user, "join", "classes", entity=s, description=f"{side.title()} joined the classroom", request=request)
    db.commit()
    return {"ok": True, "side": side, "status": s.status,
            "teacher_joined_at": s.teacher_joined_at.isoformat() if s.teacher_joined_at else None,
            "late_minutes": s.teacher_late_minutes}


@router.post("/classroom/{session_id}/leave", include_in_schema=False)
def classroom_leave(session_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("classes.view", "portal_teacher.view", "portal_client.view", "portal_student.view", any_of=True)),
                    ctx: UserContext = Depends(get_user_context)):
    s = _get(db, session_id)
    side = _classroom_access(db, s, user, ctx, request)
    now = datetime.utcnow()
    if side == "teacher":
        s.teacher_left_at = now
        if s.teacher_joined_at:
            s.actual_duration_minutes = max(1, int((now - s.teacher_joined_at).total_seconds() // 60))
    elif side == "student":
        s.student_left_at = now
    db.commit()
    return {"ok": True, "side": side, "actual_duration_minutes": s.actual_duration_minutes}


@router.post("/classroom/{session_id}/lesson-plan", include_in_schema=False)
async def classroom_lesson_plan(session_id: int, request: Request, db: Session = Depends(get_db),
                                user: User = Depends(require("lesson_plans.add", "lesson_plans.update", "classes.update", any_of=True)),
                                ctx: UserContext = Depends(get_user_context)):
    s = _get(db, session_id)
    _classroom_access(db, s, user, ctx, request)
    form = await request.form()
    plan = db.query(LessonPlan).filter(LessonPlan.session_id == s.id).first()
    created = plan is None
    if created:
        plan = LessonPlan(student_id=s.student_id, teacher_id=s.teacher_id, session_id=s.id, plan_date=s.date,
                          plan_type="daily", planned_content="", lesson_id=s.lesson_id)
        db.add(plan)
    plan.planned_content = form.get("planned_content") or plan.planned_content or "—"
    plan.sabaq = form.get("sabaq") or None
    plan.sabqi = form.get("sabqi") or None
    plan.dor = form.get("dor") or None
    plan.teacher_notes = form.get("teacher_notes") or None
    plan.delivered_content = form.get("planned_content") or plan.delivered_content
    plan.status = "delivered" if s.status == "done" else "planned"
    db.flush()
    s.lesson_plan_id = plan.id
    if form.get("teacher_notes"):
        s.teacher_notes = form.get("teacher_notes")
    log_action(db, user, "create" if created else "update", "lesson_plans", entity=plan,
               description=f"Lesson plan {'created' if created else 'updated'} from the classroom for session #{s.id}", request=request)
    db.commit()
    return redirect(f"/classroom/{s.id}", "Lesson plan saved.")


@router.post("/classroom/{session_id}/end", include_in_schema=False)
async def classroom_end(session_id: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("classes.update", "classes.execute", any_of=True)),
                        ctx: UserContext = Depends(get_user_context)):
    s = _get(db, session_id)
    side = _classroom_access(db, s, user, ctx, request)
    if side == "student":
        raise PermissionDenied("classes.update")
    form = await request.form()
    class_svc.set_status(db, s, "done", user, reason=form.get("reason") or None, request=request)
    db.commit()
    return redirect(f"/classes/{s.id}", "Class ended and marked done.")
