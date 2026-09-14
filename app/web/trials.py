"""Trial management (Module 7): request -> schedule -> outcome -> convert, with trial-to-paid analytics."""
from __future__ import annotations

from datetime import datetime, date, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core.audit import log_action, snapshot
from app.core.deps import require, csrf_protect
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import redirect, paginate, parse_date, parse_datetime, parse_int
from app.database import get_db
from app.models.academic import Course
from app.models.core import User
from app.models.crm import Lead
from app.models.people import Client, Student, Teacher
from app.models.scheduling import Trial, ClassSession
from app.services import crm as svc

router = APIRouter(prefix="/trials", dependencies=[Depends(csrf_protect)])

STATUSES = ["requested", "scheduled", "attended", "no_show", "converted", "lost"]


def _form_ctx(db: Session) -> dict:
    teachers = db.query(Teacher).filter(Teacher.status == "active").order_by(Teacher.full_name).all()
    courses = db.query(Course).order_by(Course.name).all()
    leads = db.query(Lead).filter(Lead.stage.notin_(["won", "lost"])).order_by(Lead.created_at.desc()).limit(200).all()
    return {"teachers": teachers, "courses": courses, "leads": leads, "statuses": STATUSES,
            "teacher_options": [(t.id, t.full_name) for t in teachers],
            "course_options": [(c.id, c.name) for c in courses],
            "lead_options": [(l.id, f"{l.full_name} ({l.lead_code})") for l in leads]}


def _get(db: Session, id: int) -> Trial:
    t = db.get(Trial, id)
    if not t:
        raise HTTPException(404, "Trial not found")
    return t


@router.get("", include_in_schema=False)
def list_trials(request: Request, page: int = 1, q: str = "", status: str = "", teacher: str = "", start: str = "", end: str = "",
                db: Session = Depends(get_db), user: User = Depends(require("trials.view"))):
    query = db.query(Trial)
    if q:
        query = query.filter(Trial.student_name.ilike(f"%{q}%"))
    if status:
        query = query.filter(Trial.status == status)
    if teacher:
        query = query.filter(Trial.teacher_id == int(teacher))
    d1, d2 = parse_date(start), parse_date(end)
    if d1:
        query = query.filter(Trial.scheduled_at >= datetime.combine(d1, datetime.min.time()))
    if d2:
        query = query.filter(Trial.scheduled_at <= datetime.combine(d2, datetime.max.time()))
    pg = paginate(query.order_by(Trial.scheduled_at.desc(), Trial.id.desc()), page, 25)
    return render(request, "trials/list.html", {"user": user, "page": pg, "q": q, "status": status, "teacher": teacher, "start": start, "end": end,
                                                "stats": svc.trial_stats(db), **_form_ctx(db),
                                                "base_url": f"/trials?q={q}&status={status}&teacher={teacher}&start={start}&end={end}"})


@router.get("/analytics", include_in_schema=False)
def analytics(request: Request, db: Session = Depends(get_db), user: User = Depends(require("trials.view"))):
    a = svc.trial_analytics(db)
    return render(request, "trials/analytics.html", {"user": user, "a": a, "stats": svc.trial_stats(db)})


@router.get("/follow-ups", include_in_schema=False)
def follow_ups(request: Request, db: Session = Depends(get_db), user: User = Depends(require("trials.view"))):
    rows = (db.query(Trial).filter(Trial.status.in_(["attended", "no_show", "scheduled"]), Trial.follow_up_date.isnot(None),
                                   Trial.follow_up_date <= date.today() + timedelta(days=2)).order_by(Trial.follow_up_date).limit(200).all())
    return render(request, "trials/follow_ups.html", {"user": user, "rows": rows, "stats": svc.trial_stats(db)})


# ----------------------------------------------------------------------------- Running Trials (ERP 3.6)
@router.get("/running", include_in_schema=False)
def running_trials(request: Request, page: int = 1, q: str = "", client_id: int | None = None, teacher_id: int | None = None,
                   course_id: int | None = None, slot_id: int | None = None, shift: str = "",
                   db: Session = Depends(get_db), user: User = Depends(require("trials.view", "subscriptions.view", any_of=True))):
    """Trial subscriptions that are still running, with the calculated trial end date."""
    from app.models.finance import Subscription
    from app.services import scheduling as sched
    query = db.query(Subscription).filter(Subscription.status == "trial")
    if q:
        like = f"%{q.strip()}%"
        query = (query.join(Student, Student.id == Subscription.student_id)
                 .filter(or_(Student.full_name.ilike(like), Student.student_code.ilike(like),
                             Subscription.subscription_code.ilike(like))))
    if client_id:
        query = query.filter(Subscription.client_id == client_id)
    if teacher_id:
        query = query.filter(Subscription.teacher_id == teacher_id)
    if course_id:
        query = query.filter(Subscription.course_id == course_id)
    if slot_id:
        query = query.filter(Subscription.slot_id == slot_id)
    if shift:
        query = query.join(Client, Client.id == Subscription.client_id).filter(Client.shift == shift)
    pg = paginate(query.order_by(Subscription.start_date.desc(), Subscription.id.desc()), page, 30)
    rows = []
    for s in pg.items:
        end = s.start_date + timedelta(days=s.trial_days or 3) if s.start_date else None
        rows.append({"sub": s, "trial_end": end,
                     "days_left": (end - date.today()).days if end else None})
    all_trials = db.query(Subscription).filter(Subscription.status == "trial").all()
    ending = sum(1 for s in all_trials
                 if s.start_date and 0 <= ((s.start_date + timedelta(days=s.trial_days or 3)) - date.today()).days <= 2)
    base = (f"/trials/running?q={q}&client_id={client_id or ''}&teacher_id={teacher_id or ''}&course_id={course_id or ''}"
            f"&slot_id={slot_id or ''}&shift={shift}")
    return render(request, "trials/running.html", {
        "user": user, "page": pg, "rows": rows, "q": q, "client_id": client_id, "teacher_id": teacher_id,
        "course_id": course_id, "slot_id": slot_id, "shift": shift, "base_url": base,
        "tiles": {"running": len(all_trials), "ending": ending,
                  "clients": len({s.client_id for s in all_trials}), "students": len({s.student_id for s in all_trials})},
        "day_names": sched.DAY_NAMES,
        "slot_options": sched.slot_options(db),
        "client_options": [(c.id, f"{c.full_name} ({c.client_code})") for c in db.query(Client).order_by(Client.full_name)],
        "course_options": [(c.id, c.name) for c in db.query(Course).order_by(Course.name)],
        "teacher_options": [(t.id, t.full_name) for t in db.query(Teacher).filter(Teacher.status != "inactive").order_by(Teacher.full_name)]})


@router.post("/running/{sub_id}/convert", include_in_schema=False)
async def convert_trial_subscription(sub_id: int, request: Request, db: Session = Depends(get_db),
                                     user: User = Depends(require("subscriptions.update"))):
    """Convert to Regular: the trial subscription becomes a regular one and the schedule stops being a trial."""
    from app.models.finance import Subscription
    from app.services import scheduling as sched
    sub = db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "Subscription not found")
    form = await request.form()
    back = form.get("back") or "/trials/running"
    if sub.status != "trial":
        return redirect(back, f"Subscription {sub.subscription_code} is not on trial.", "error")
    reason = (form.get("reason") or form.get("rationale") or "").strip() or "Trial completed; family continuing on a regular subscription."
    before = {"status": sub.status}
    sub.status = "regular"
    if sub.student and sub.student.status == "trial":
        sub.student.status = "active"
    if sub.client and sub.client.status == "trial":
        sub.client.status = "active"
    sched.sync_subscription_schedule(db, user, sub, reason=reason, request=request)
    log_action(db, user, "convert", "subscriptions", entity=sub,
               description=f"Trial subscription {sub.subscription_code} converted to Regular", rationale=reason,
               before=before, after={"status": "regular"}, request=request, consequential=True)
    if sub.client and sub.client.user_id:
        notify(db, sub.client.user_id, "Trial converted",
               f"{sub.student.full_name if sub.student else 'Your child'} is now on a regular subscription.",
               event_type="subscription", link="/portal/subscriptions")
    if sub.teacher and sub.teacher.user_id:
        notify(db, sub.teacher.user_id, "Trial converted to regular",
               f"{sub.student.full_name if sub.student else 'A student'} continues as a regular student.",
               event_type="subscription", link="/teacher/students")
    db.commit()
    return redirect(back, f"{sub.subscription_code} converted to Regular.")


@router.post("/running/{sub_id}/drop", include_in_schema=False)
async def drop_trial_subscription(sub_id: int, request: Request, db: Session = Depends(get_db),
                                  user: User = Depends(require("subscriptions.update"))):
    """Drop: cancel the trial subscription with a reason and cancel its future classes."""
    from app.models.finance import Subscription
    from app.services import scheduling as sched
    sub = db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "Subscription not found")
    form = await request.form()
    back = form.get("back") or "/trials/running"
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if not reason:
        return redirect(back, "A reason is required to drop a trial.", "error")
    if sub.status != "trial":
        return redirect(back, f"Subscription {sub.subscription_code} is not on trial.", "error")
    before = {"status": sub.status}
    sub.status = "cancelled"
    sub.cancelled_at = date.today()
    sub.cancel_reason = reason[:200]
    sub.end_date = sub.end_date or date.today()
    cancelled = sched.end_subscription_schedule(db, user, sub, "ended", reason, request=request)
    log_action(db, user, "cancel", "subscriptions", entity=sub,
               description=f"Trial subscription {sub.subscription_code} dropped; {cancelled} future class(es) cancelled",
               rationale=reason, before=before, after={"status": "cancelled"}, request=request, consequential=True)
    if sub.teacher and sub.teacher.user_id:
        notify(db, sub.teacher.user_id, "Trial dropped",
               f"{sub.student.full_name if sub.student else 'A student'} dropped the trial. {reason}",
               event_type="subscription", link="/teacher/students")
    if sub.client and sub.client.user_id:
        notify(db, sub.client.user_id, "Trial closed", f"The trial has been closed. {reason}",
               event_type="subscription", link="/portal/subscriptions")
    db.commit()
    return redirect(back, f"{sub.subscription_code} dropped; {cancelled} future class(es) cancelled.")


@router.get("/new", include_in_schema=False)
def new_trial(request: Request, lead_id: int = 0, db: Session = Depends(get_db), user: User = Depends(require("trials.add"))):
    lead = db.get(Lead, lead_id) if lead_id else None
    return render(request, "trials/form.html", {"user": user, "lead": lead, **_form_ctx(db)})


@router.post("/new", include_in_schema=False)
async def create_trial(request: Request, db: Session = Depends(get_db), user: User = Depends(require("trials.add"))):
    form = await request.form()
    lead = db.get(Lead, parse_int(form.get("lead_id"))) if form.get("lead_id") else None
    name = (form.get("student_name") or "").strip() or (lead.student_name if lead else "") or (lead.full_name if lead else "")
    if not name:
        return redirect("/trials/new", "A student name (or a lead) is required.", "error")
    teacher = db.get(Teacher, parse_int(form.get("teacher_id"))) if form.get("teacher_id") else None
    course = db.get(Course, parse_int(form.get("course_id"))) if form.get("course_id") else None
    when = parse_datetime(form.get("scheduled_at"))
    tr = svc.create_trial(db, name, lead=lead, teacher=teacher, course=course, scheduled_at=when, actor=user,
                          notes=form.get("notes"), request=request)
    if lead and when and lead.stage in ("new", "contacted"):
        svc.move_stage(db, lead, "trial_scheduled", user, "Trial booked", request=request)
    db.commit()
    return redirect(f"/trials/{tr.id}", f"Trial created for {name} ({tr.status}).")


@router.get("/{id}", include_in_schema=False)
def trial_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("trials.view"))):
    t = _get(db, id)
    session = db.get(ClassSession, t.session_id) if t.session_id else None
    return render(request, "trials/detail.html", {"user": user, "t": t, "session": session, **_form_ctx(db)})


@router.post("/{id}/schedule", include_in_schema=False)
async def schedule(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("trials.update"))):
    t = _get(db, id)
    form = await request.form()
    when = parse_datetime(form.get("scheduled_at"))
    if not when:
        return redirect(f"/trials/{t.id}", "A date and time is required.", "error")
    before = snapshot(t)
    t.scheduled_at = when
    t.teacher_id = parse_int(form.get("teacher_id")) or t.teacher_id
    t.course_id = parse_int(form.get("course_id")) or t.course_id
    t.status = "scheduled"
    t.follow_up_date = when.date() + timedelta(days=1)
    svc.schedule_trial_session(db, t, user)
    if t.lead and t.lead.stage in ("new", "contacted"):
        svc.move_stage(db, t.lead, "trial_scheduled", user, "Trial booked", request=request)
    if t.teacher_id:
        teacher = db.get(Teacher, t.teacher_id)
        if teacher and teacher.user_id:
            notify(db, teacher.user_id, "Trial class scheduled", f"{t.student_name} on {when.strftime('%d %b %Y %H:%M')}",
                   event_type="trial", link="/teacher/trials")
    log_action(db, user, "schedule_change", "trials", entity=t, description=f"Trial for {t.student_name} scheduled {when.isoformat()}",
               before=before, after=snapshot(t), request=request)
    db.commit()
    return redirect(f"/trials/{t.id}", f"Trial scheduled for {when.strftime('%d %b %Y %H:%M')}.")


@router.post("/{id}/outcome", include_in_schema=False)
async def outcome(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("trials.update"))):
    t = _get(db, id)
    form = await request.form()
    status = form.get("status") or ""
    if status not in ("attended", "no_show", "lost"):
        return redirect(f"/trials/{t.id}", "Select attended, no-show or lost.", "error")
    before = {"status": t.status}
    t.status = status
    t.outcome = (form.get("outcome") or "").strip() or None
    t.teacher_feedback = (form.get("teacher_feedback") or "").strip() or t.teacher_feedback
    t.follow_up_date = parse_date(form.get("follow_up_date")) or (date.today() + timedelta(days=1))
    if t.lead:
        svc.add_activity(db, t.lead, "trial", f"Trial {status.replace('_', ' ')}: {t.outcome or t.teacher_feedback or 'recorded'}", user)
        if status == "attended" and t.lead.stage in ("new", "contacted", "trial_scheduled"):
            svc.move_stage(db, t.lead, "trial_done", user, "Trial attended", request=request)
        svc.enroll_sequence(db, "trial_follow_up", "lead", t.lead.id, enrolled_by=user.email)
    elif t.client_id:
        svc.enroll_sequence(db, "trial_follow_up", "client", t.client_id, enrolled_by=user.email)
    log_action(db, user, "status_change", "trials", entity=t, description=f"Trial outcome for {t.student_name}: {status}",
               before=before, after={"status": status}, request=request)
    db.commit()
    return redirect(f"/trials/{t.id}", f"Outcome recorded ({status.replace('_', ' ')}); follow-up sequence enrolled.")


@router.post("/{id}/follow-up", include_in_schema=False)
async def follow_up(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("trials.update"))):
    t = _get(db, id)
    form = await request.form()
    t.follow_up_date = parse_date(form.get("follow_up_date"))
    t.follow_up_count = (t.follow_up_count or 0) + 1
    if form.get("note") and t.lead:
        svc.add_activity(db, t.lead, "call", form.get("note"), user)
    log_action(db, user, "update", "trials", entity=t, description=f"Follow-up logged for trial {t.id}")
    db.commit()
    return redirect(f"/trials/{t.id}", "Follow-up logged.")


@router.post("/{id}/convert", include_in_schema=False)
async def convert(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("trials.update", "clients.add", any_of=True))):
    t = _get(db, id)
    form = await request.form()
    if not t.lead:
        t.status = "converted"
        log_action(db, user, "convert", "trials", entity=t, description=f"Trial {t.id} marked converted", request=request, consequential=True)
        db.commit()
        return redirect(f"/trials/{t.id}", "Trial marked converted.")
    if t.lead.converted_client_id:
        t.status = "converted"
        db.commit()
        return redirect(f"/clients/{t.lead.converted_client_id}", "The lead was already converted; trial linked.")
    client, pwd = svc.convert_lead_to_client(db, t.lead, user, {"email": form.get("email")}, request=request)
    t.status = "converted"
    t.client_id = client.id
    db.commit()
    msg = f"Client {client.client_code} created from trial."
    if pwd:
        msg += f" Portal login: {client.user.email if client.user else client.email} / {pwd}"
    return redirect(f"/clients/{client.id}", msg)
