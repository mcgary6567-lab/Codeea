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
from app.models.people import Student, Teacher
from app.models.scheduling import (Attendance, ClassSession, QAReview, Recording, Schedule, Shift, SESSION_STATUSES)
from app.services import classes as class_svc
from app.services import scheduling as svc

router = APIRouter(dependencies=[Depends(csrf_protect)])

ACTION_STATUSES = ["started", "done", "absent", "missed", "cancelled", "leave", "pending"]
REASON_REQUIRED = {"missed", "absent", "cancelled", "rescheduled", "leave"}


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


# ----------------------------------------------------------------------------- list
@router.get("/classes", include_in_schema=False)
def list_classes(request: Request, page: int = 1, date: str = "", date_from: str = "", date_to: str = "", status: str = "",
                 teacher_id: int | None = None, student_id: int | None = None, course_id: int | None = None,
                 shift_id: int | None = None, trial: str = "", q: str = "", db: Session = Depends(get_db),
                 user: User = Depends(require("classes.view")), ctx: UserContext = Depends(get_user_context)):
    from datetime import date as _date
    day = parse_date(date, None if (date_from or date_to) else _date.today())
    df, dt = parse_date(date_from), parse_date(date_to)
    teacher_ids = svc.scoped_teacher_ids(db, user)
    query = svc.sessions_query(db, day=day, date_from=df, date_to=dt, status=status, teacher_id=teacher_id,
                               student_id=student_id, course_id=course_id, shift_id=shift_id,
                               trial=(parse_bool(trial) if trial else None), teacher_ids=teacher_ids, q=q)
    pg = paginate(query.order_by(ClassSession.scheduled_start.desc(), ClassSession.id.desc()), page, 40)
    counters = class_svc.counters_for_date(db, day or _date.today(), teacher_ids, shift_id)
    base = (f"/classes?date={date}&date_from={date_from}&date_to={date_to}&status={status}&teacher_id={teacher_id or ''}"
            f"&student_id={student_id or ''}&course_id={course_id or ''}&shift_id={shift_id or ''}&trial={trial}&q={q}")
    return render(request, "classes/list.html", {
        "user": user, "page": pg, "counters": counters, "base_url": base, "day": day, "date_from": df, "date_to": dt,
        "status": status, "teacher_id": teacher_id, "student_id": student_id, "course_id": course_id, "shift_id": shift_id,
        "trial": trial, "q": q, "date_value": date, "reason_required": sorted(REASON_REQUIRED), **_filters(db, user)})


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
