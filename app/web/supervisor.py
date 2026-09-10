"""Module 15 — Supervisor live monitoring: real-time board, quick interventions, masked calling and coverage reports."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.security import mask
from app.core.templating import render
from app.core.utils import parse_date, parse_int, redirect
from app.database import get_db
from app.models.core import RiskAlert, User
from app.models.people import Client, Employee, Leave, Student, Teacher
from app.models.scheduling import CallLog, ClassSession, Schedule, Shift
from app.services import classes as class_svc
from app.services import scheduling as svc

router = APIRouter(prefix="/supervisor", dependencies=[Depends(csrf_protect)])

IMMINENT_MINUTES = 30
BOARD_COLUMNS = [
    ("pending", "Starting soon", "clock", "amber"),
    ("started", "In progress", "play-circle", "indigo"),
    ("done", "Done", "check-circle-2", "emerald"),
    ("missed", "Missed", "user-x", "rose"),
    ("absent", "Absent", "user-minus", "orange"),
    ("leave", "On leave", "palmtree", "violet"),
    ("upcoming", "Later today", "calendar", "slate"),
]


def _board(db: Session, user: User, day: date, shift_id: int | None, q: str = "") -> dict:
    teacher_ids = svc.scoped_teacher_ids(db, user)
    now = svc.org_now()
    rows = svc.sessions_query(db, day=day, teacher_ids=teacher_ids, shift_id=shift_id, q=q).order_by(ClassSession.scheduled_start).all()
    cols: dict[str, list] = {k: [] for k, _, _, _ in BOARD_COLUMNS}
    for s in rows:
        if s.status == "pending":
            key = "pending" if s.scheduled_start <= now + timedelta(minutes=IMMINENT_MINUTES) else "upcoming"
        elif s.status in cols:
            key = s.status
        else:
            continue
        cols[key].append(s)
    return {"columns": [{"key": k, "label": l, "icon": i, "color": c, "items": cols[k]} for k, l, i, c in BOARD_COLUMNS],
            "counts": {k: len(v) for k, v in cols.items()}, "now": now, "total": len(rows)}


def _side_panels(db: Session, user: User, day: date) -> dict:
    teacher_ids = svc.scoped_teacher_ids(db, user)
    absent_q = svc.sessions_query(db, day=day, status="absent", teacher_ids=teacher_ids)
    cancelled_q = svc.sessions_query(db, day=day, status="cancelled", teacher_ids=teacher_ids)
    leave_q = (db.query(Leave).join(Employee, Employee.id == Leave.employee_id)
               .filter(Leave.person_type == "employee", Leave.status == "approved", Employee.is_teacher.is_(True),
                       Leave.start_date <= day, Leave.end_date >= day))
    alerts = db.query(RiskAlert).filter(RiskAlert.status.in_(["open", "acknowledged"]), RiskAlert.visibility == "ops")
    return {"absent_today": absent_q.order_by(ClassSession.scheduled_start).all(),
            "cancelled_today": cancelled_q.order_by(ClassSession.scheduled_start).all(),
            "teachers_on_leave": leave_q.all(),
            "alerts": alerts.order_by(RiskAlert.created_at.desc()).limit(10).all()}


@router.get("", include_in_schema=False)
def board(request: Request, day: str = "", shift_id: int | None = None, q: str = "",
          db: Session = Depends(get_db), user: User = Depends(require("supervisor.view"))):
    the_day = parse_date(day, date.today())
    ctx = {"user": user, "sel_date": the_day, "shift_id": shift_id, "q": q,
           "shift_options": [(s.id, s.name) for s in db.query(Shift).filter(Shift.is_active.is_(True)).order_by(Shift.start_time)],
           "scope": "My supervised teachers" if user.role_slug == "supervisor" else "All teachers",
           "board": _board(db, user, the_day, shift_id, q), **_side_panels(db, user, the_day)}
    return render(request, "supervisor/board.html", ctx)


@router.get("/partial/board", include_in_schema=False)
def board_partial(request: Request, day: str = "", shift_id: int | None = None, q: str = "",
                  db: Session = Depends(get_db), user: User = Depends(require("supervisor.view"))):
    the_day = parse_date(day, date.today())
    return render(request, "supervisor/_board.html", {
        "user": user, "sel_date": the_day, "shift_id": shift_id, "q": q,
        "board": _board(db, user, the_day, shift_id, q), "refreshed_at": datetime.utcnow()})


@router.post("/session/{session_id}/status", include_in_schema=False)
async def session_status(session_id: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("supervisor.update", "classes.update", any_of=True))):
    s = db.get(ClassSession, session_id)
    if not s:
        return redirect("/supervisor", "Class not found.", "error")
    teacher_ids = svc.scoped_teacher_ids(db, user)
    if teacher_ids is not None and s.teacher_id not in teacher_ids:
        return redirect("/supervisor", "That class is outside your supervision scope.", "error")
    form = await request.form()
    status = form.get("status") or ""
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    from app.models.scheduling import SESSION_STATUSES
    if status not in SESSION_STATUSES:
        return redirect("/supervisor", "Invalid status.", "error")
    if status in ("missed", "absent", "cancelled") and not reason:
        return redirect("/supervisor", f"A reason is required to mark a class {status}.", "error")
    class_svc.set_status(db, s, status, user, reason=reason or None, request=request)
    db.commit()
    return redirect(form.get("back") or "/supervisor", f"Class marked {status}.")


@router.post("/call", include_in_schema=False)
async def make_call(request: Request, db: Session = Depends(get_db), user: User = Depends(require("calling.execute", "supervisor.update", any_of=True))):
    form = await request.form()
    callee_type = form.get("callee_type") or "client"
    callee_id = parse_int(form.get("callee_id")) or 0
    number = ""
    label = f"{callee_type} #{callee_id}"
    if callee_type == "client":
        c = db.get(Client, callee_id)
        number, label = (c.phone or c.whatsapp or "") if c else "", (c.full_name if c else label)
    elif callee_type == "teacher":
        t = db.get(Teacher, callee_id)
        number = (t.employee.phone if t and t.employee else "") or ""
        label = t.full_name if t else label
    log = CallLog(caller_id=user.id, callee_type=callee_type, callee_id=callee_id, callee_masked=mask(number, keep=4),
                  channel=form.get("channel") or "voip", direction="outbound", status="completed",
                  duration_seconds=0, notes=form.get("notes") or f"Supervisor call about {form.get('context') or 'a class issue'}")
    db.add(log)
    db.flush()
    log_action(db, user, "call", "calling", entity=log, description=f"Masked call placed to {label} ({log.callee_masked})",
               rationale=form.get("notes"), request=request)
    db.commit()
    return redirect(form.get("back") or "/supervisor", f"Call logged to {label} on the masked number {log.callee_masked}.")


# ----------------------------------------------------------------------------- teachers
@router.get("/teachers", include_in_schema=False)
def teachers_view(request: Request, day: str = "", teacher_id: int | None = None, db: Session = Depends(get_db),
                  user: User = Depends(require("supervisor.view"))):
    the_day = parse_date(day, date.today())
    teachers = svc.teachers_for(db, user)
    if teacher_id:
        teachers = [t for t in teachers if t.id == teacher_id]
    since = the_day - timedelta(days=30)
    rows = []
    for t in teachers:
        stats = class_svc.teacher_stats(db, t.id, since)
        today = svc.teacher_day_stats(db, t.id, the_day)
        rows.append({"teacher": t, "stats": stats, "today": today,
                     "students": db.query(func.count(Student.id)).filter(Student.teacher_id == t.id, Student.status.in_(["active", "trial"])).scalar() or 0})
    rows.sort(key=lambda r: (-r["stats"]["missed"], -r["stats"]["total"]))
    return render(request, "supervisor/teachers.html", {
        "user": user, "sel_date": the_day, "rows": rows, "teacher_id": teacher_id,
        "teacher_options": [(t.id, t.full_name) for t in svc.teachers_for(db, user)]})


# ----------------------------------------------------------------------------- weekly coverage
@router.get("/weekly", include_in_schema=False)
def weekly(request: Request, week_start: str = "", db: Session = Depends(get_db), user: User = Depends(require("supervisor.view"))):
    today = date.today()
    start = parse_date(week_start, today - timedelta(days=today.weekday()))
    end = start + timedelta(days=6)
    teacher_ids = svc.scoped_teacher_ids(db, user)
    q = db.query(ClassSession.date, ClassSession.status, func.count(ClassSession.id)).filter(
        ClassSession.date >= start, ClassSession.date <= end)
    if teacher_ids is not None:
        q = q.filter(ClassSession.teacher_id.in_(teacher_ids or [-1]))
    agg: dict[date, dict[str, int]] = {}
    for d, status, n in q.group_by(ClassSession.date, ClassSession.status):
        agg.setdefault(d, {})[status] = n
    days = []
    tot_planned = tot_done = 0
    for i in range(7):
        d = start + timedelta(days=i)
        row = agg.get(d, {})
        planned = sum(row.values()) - row.get("cancelled", 0) - row.get("leave", 0) - row.get("rescheduled", 0)
        done = row.get("done", 0)
        tot_planned += planned
        tot_done += done
        days.append({"date": d, "planned": planned, "done": done, "missed": row.get("missed", 0), "absent": row.get("absent", 0),
                     "pending": row.get("pending", 0), "coverage": round(100 * done / planned, 1) if planned else 0.0})
    response_times = svc.missed_response_times(db, start)
    avg_response = round(sum(response_times) / len(response_times), 1) if response_times else 0.0
    per_teacher = []
    for t in svc.teachers_for(db, user):
        stats = class_svc.teacher_stats(db, t.id, start)
        if stats["total"]:
            per_teacher.append({"teacher": t, **stats})
    per_teacher.sort(key=lambda r: r["completion_rate"])
    return render(request, "supervisor/weekly.html", {
        "user": user, "start": start, "end": end,
        "prev_week": start - timedelta(days=7), "next_week": start + timedelta(days=7),
        "days": days, "coverage": round(100 * tot_done / tot_planned, 1) if tot_planned else 0.0,
        "tot_planned": tot_planned, "tot_done": tot_done, "avg_response": avg_response,
        "response_count": len(response_times), "per_teacher": per_teacher})
