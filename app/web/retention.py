"""Retention & churn (Module 33 + 29.11): risk board, retention actions kanban, freezes and cohort analytics."""
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
from app.models.core import User, Role
from app.models.crm import RetentionAction, SequenceEnrollment
from app.models.people import Client, Student, Teacher
from app.services import crm as crm_svc
from app.services import retention as svc

router = APIRouter(prefix="/retention", dependencies=[Depends(csrf_protect)])


def _owners(db: Session) -> list[User]:
    return (db.query(User).join(Role, Role.id == User.role_id)
            .filter(Role.slug.in_(["supervisor", "manager", "hod_academics", "hod_marketing", "lead_closer", "academic_coordinator"]),
                    User.is_active.is_(True)).order_by(User.full_name).all())


@router.get("", include_in_schema=False)
def board(request: Request, level: str = "", teacher: str = "", q: str = "", db: Session = Depends(get_db),
          user: User = Depends(require("retention.view"))):
    rows = svc.risk_board(db, level=level, teacher_id=int(teacher) if teacher else None, q=q)
    return render(request, "retention/board.html", {"user": user, "rows": rows, "level": level, "teacher": teacher, "q": q,
                                                    "stats": svc.retention_stats(db), "levels": svc.RISK_LEVELS,
                                                    "teachers": db.query(Teacher).order_by(Teacher.full_name).all(),
                                                    "owners": _owners(db), "action_types": svc.ACTION_TYPES,
                                                    "base_url": f"/retention?level={level}&teacher={teacher}&q={q}"})


@router.get("/actions", include_in_schema=False)
def actions(request: Request, status: str = "", action_type: str = "", owner: str = "", db: Session = Depends(get_db),
            user: User = Depends(require("retention.view"))):
    query = db.query(RetentionAction)
    if action_type:
        query = query.filter(RetentionAction.action_type == action_type)
    if owner == "mine":
        query = query.filter(RetentionAction.owner_id == user.id)
    elif owner:
        query = query.filter(RetentionAction.owner_id == int(owner))
    rows = query.order_by(RetentionAction.scheduled_at.desc(), RetentionAction.id.desc()).limit(500).all()
    columns = [{"status": s, "items": [r for r in rows if r.status == s]} for s in svc.ACTION_STATUSES]
    return render(request, "retention/actions.html", {"user": user, "columns": columns, "status": status, "action_type": action_type,
                                                      "owner": owner, "action_types": svc.ACTION_TYPES, "statuses": svc.ACTION_STATUSES,
                                                      "owners": _owners(db), "stats": svc.retention_stats(db),
                                                      "students": db.query(Student).filter(Student.status.in_(["active", "trial", "frozen"]))
                                                      .order_by(Student.full_name).limit(500).all()})


@router.get("/freezes", include_in_schema=False)
def freezes(request: Request, db: Session = Depends(get_db), user: User = Depends(require("retention.view"))):
    return render(request, "retention/freezes.html", {"user": user, "rows": svc.frozen_students(db), "stats": svc.retention_stats(db)})


@router.get("/analytics", include_in_schema=False)
def analytics(request: Request, db: Session = Depends(get_db), user: User = Depends(require("retention.view"))):
    return render(request, "retention/analytics.html", {"user": user, "d": svc.retention_dashboard(db)})


@router.post("/recompute", include_in_schema=False)
async def recompute_all(request: Request, db: Session = Depends(get_db), user: User = Depends(require("retention.execute", "retention.update", any_of=True))):
    counts = svc.recompute_all(db, user)
    log_action(db, user, "execute", "retention", description=f"Risk recomputed for {counts['total']} students", after=counts, request=request)
    db.commit()
    return redirect("/retention", f"Risk recomputed for {counts['total']} students: {counts['high']} high, {counts['medium']} medium, "
                                  f"{counts['low']} low ({counts['actions']} new actions).")


@router.post("/students/{sid}/recompute", include_in_schema=False)
async def recompute_one(sid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("retention.update"))):
    st = db.get(Student, sid)
    if not st:
        raise HTTPException(404, "Student not found")
    res = svc.compute_risk(db, st, user)
    log_action(db, user, "execute", "retention", entity=st, description=f"Risk recomputed for {st.student_code}: {res['level']} ({res['score']})",
               after={"score": res["score"], "level": res["level"]}, request=request)
    db.commit()
    return redirect(request.headers.get("referer") or "/retention",
                    f"{st.full_name}: risk {res['level']} ({res['score']}).")


@router.post("/actions/new", include_in_schema=False)
async def new_action(request: Request, db: Session = Depends(get_db), user: User = Depends(require("retention.add", "retention.update", any_of=True))):
    form = await request.form()
    st = db.get(Student, parse_int(form.get("student_id"))) if form.get("student_id") else None
    if not st:
        return redirect("/retention/actions", "Select a student.", "error")
    a = RetentionAction(student_id=st.id, client_id=st.client_id, action_type=form.get("action_type") or "call",
                        trigger=form.get("trigger") or "manual", risk_score_at_trigger=st.risk_score, status="scheduled",
                        scheduled_at=parse_datetime(form.get("scheduled_at")) or datetime.utcnow(),
                        owner_id=parse_int(form.get("owner_id")) or user.id, notes=form.get("notes") or None)
    db.add(a)
    db.flush()
    if a.action_type == "pre_leave_offer":
        e = crm_svc.enroll_sequence(db, "pre_leave_offer", "student", st.id, enrolled_by=user.email)
        a.sequence_enrollment_id = e.id if e else None
    if a.action_type == "win_back_sequence":
        e = crm_svc.enroll_sequence(db, "win_back", "student", st.id, enrolled_by=user.email)
        a.sequence_enrollment_id = e.id if e else None
    if a.owner_id and a.owner_id != user.id:
        notify(db, a.owner_id, f"Retention action assigned: {st.full_name}",
               f"{a.action_type.replace('_', ' ').title()} scheduled for {a.scheduled_at:%d %b %Y}.", event_type="retention_action", link="/retention/actions")
    log_action(db, user, "create", "retention", entity=a, description=f"{a.action_type} created for {st.student_code}", request=request)
    db.commit()
    return redirect("/retention/actions", "Retention action scheduled.")


@router.post("/actions/{aid}/status", include_in_schema=False)
async def action_status(aid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("retention.update"))):
    a = db.get(RetentionAction, aid)
    if not a:
        raise HTTPException(404, "Retention action not found")
    form = await request.form()
    status = form.get("status") or a.status
    if status not in svc.ACTION_STATUSES:
        return redirect("/retention/actions", "Invalid status.", "error")
    before = {"status": a.status}
    a.status = status
    a.outcome = (form.get("outcome") or a.outcome or "").strip() or None
    if form.get("notes"):
        a.notes = form.get("notes")
    if status in ("completed", "succeeded", "failed"):
        a.completed_at = datetime.utcnow()
        if status in ("succeeded", "failed") and not a.outcome:
            return redirect("/retention/actions", "An outcome is required when closing a retention action.", "error")
    log_action(db, user, "status_change", "retention", entity=a, description=f"Retention action {a.id} {before['status']} -> {status}",
               rationale=a.outcome, before=before, after={"status": status}, request=request)
    db.commit()
    return redirect("/retention/actions", f"Action marked {status.replace('_', ' ')}.")


@router.post("/cohort-calls", include_in_schema=False)
async def cohort_calls(request: Request, db: Session = Depends(get_db), user: User = Depends(require("retention.update"))):
    form = await request.form()
    owner = db.get(User, parse_int(form.get("owner_id"))) if form.get("owner_id") else user
    n = svc.bulk_cohort_calls(db, owner, user, level=form.get("level") or "medium", when=parse_datetime(form.get("scheduled_at")))
    db.commit()
    return redirect("/retention/actions", f"{n} cohort call(s) scheduled.")


@router.post("/freezes/schedule", include_in_schema=False)
async def freeze_schedule(request: Request, db: Session = Depends(get_db), user: User = Depends(require("retention.update"))):
    n = svc.schedule_freeze_outreach(db, user)
    db.commit()
    return redirect("/retention/freezes", f"{n} reactivation outreach action(s) scheduled 7 days before freeze end.")
