"""Module 17 — Quality Assurance: sampling, scored reviews, corrective actions, HOD approval and trends."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import paginate, parse_date, parse_float, parse_int, redirect
from app.database import get_db
from app.models.core import User
from app.models.people import Student, Teacher
from app.models.scheduling import AIClassAnalysis, ClassSession, CorrectiveAction, QAReview
from app.services import qa as svc
from app.services import scheduling as sched_svc

router = APIRouter(prefix="/qa", dependencies=[Depends(csrf_protect)])

REVIEW_STATUSES = ["queued", "in_review", "completed", "approved"]
SAMPLE_TYPES = ["random", "risk_based", "scheduled", "complaint", "re_evaluation"]
ACTION_STATUSES = ["open", "in_progress", "overdue", "closed"]


def _get(db: Session, id: int) -> QAReview:
    r = db.get(QAReview, id)
    if not r:
        raise HTTPException(404, "QA review not found")
    return r


@router.get("", include_in_schema=False)
def queue(request: Request, page: int = 1, status: str = "", sample_type: str = "", teacher_id: int | None = None,
          date_from: str = "", date_to: str = "", q: str = "", db: Session = Depends(get_db),
          user: User = Depends(require("qa.view"))):
    query = db.query(QAReview)
    teacher_ids = sched_svc.scoped_teacher_ids(db, user)
    if teacher_ids is not None:
        query = query.filter(QAReview.teacher_id.in_(teacher_ids or [-1]))
    if status:
        query = query.filter(QAReview.status == status)
    if sample_type:
        query = query.filter(QAReview.sample_type == sample_type)
    if teacher_id:
        query = query.filter(QAReview.teacher_id == teacher_id)
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(QAReview.created_at >= datetime.combine(df, datetime.min.time()))
    if dt:
        query = query.filter(QAReview.created_at <= datetime.combine(dt, datetime.max.time()))
    if q:
        like = f"%{q}%"
        query = query.join(Teacher, Teacher.id == QAReview.teacher_id).filter(
            or_(Teacher.full_name.ilike(like), Teacher.teacher_code.ilike(like)))
    pg = paginate(query.order_by(QAReview.status != "queued", QAReview.created_at.desc()), page, 30)
    counts = dict(db.query(QAReview.status, func.count(QAReview.id)).group_by(QAReview.status).all())
    avg = (db.query(func.avg(QAReview.overall_score)).filter(QAReview.overall_score.isnot(None),
                                                             QAReview.status.in_(["completed", "approved"])).scalar() or 0)
    needs_approval = (db.query(func.count(QAReview.id))
                      .filter(QAReview.status == "completed", QAReview.overall_score < svc.APPROVAL_THRESHOLD).scalar() or 0)
    open_actions = db.query(func.count(CorrectiveAction.id)).filter(CorrectiveAction.status.in_(["open", "in_progress", "overdue"])).scalar() or 0
    base = (f"/qa?status={status}&sample_type={sample_type}&teacher_id={teacher_id or ''}&date_from={date_from}"
            f"&date_to={date_to}&q={q}")
    return render(request, "qa/list.html", {
        "user": user, "page": pg, "status": status, "sample_type": sample_type, "teacher_id": teacher_id,
        "date_from": df, "date_to": dt, "q": q, "base_url": base, "statuses": REVIEW_STATUSES, "sample_types": SAMPLE_TYPES,
        "teacher_options": [(t.id, t.full_name) for t in sched_svc.teachers_for(db, user)],
        "threshold": svc.APPROVAL_THRESHOLD,
        "stats": {"queued": counts.get("queued", 0), "in_review": counts.get("in_review", 0),
                  "completed": counts.get("completed", 0) + counts.get("approved", 0),
                  "avg": round(float(avg), 1), "needs_approval": needs_approval, "open_actions": open_actions}})


# ----------------------------------------------------------------------------- sampling
@router.post("/sample/random", include_in_schema=False)
async def sample_random(request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.add"))):
    form = await request.form()
    n = max(1, min(50, parse_int(form.get("n"), 10) or 10))
    rows = svc.sample_random(db, n, reviewer=None, days=parse_int(form.get("days"), 7) or 7)
    log_action(db, user, "execute", "qa", description=f"Random QA sample drawn: {len(rows)} review(s) queued", request=request)
    db.commit()
    return redirect("/qa?status=queued", f"{len(rows)} class(es) sampled at random and queued for review."
                    if rows else "No unreviewed completed classes were available to sample.", "success" if rows else "warning")


@router.post("/sample/risk", include_in_schema=False)
async def sample_risk(request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.add"))):
    form = await request.form()
    n = max(1, min(50, parse_int(form.get("n"), 10) or 10))
    rows = svc.sample_risk_based(db, n, reviewer=None, days=parse_int(form.get("days"), 14) or 14)
    log_action(db, user, "execute", "qa", description=f"Risk-based QA sample drawn: {len(rows)} review(s) queued", request=request)
    db.commit()
    return redirect("/qa?status=queued&sample_type=risk_based",
                    f"{len(rows)} higher-risk class(es) queued for review." if rows else "No medium/high-risk classes were available to sample.",
                    "success" if rows else "warning")


@router.post("/schedule-review", include_in_schema=False)
async def schedule_review(request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.add"))):
    form = await request.form()
    teacher = db.get(Teacher, parse_int(form.get("teacher_id")) or 0)
    if not teacher:
        return redirect("/qa", "Select a teacher to schedule a review for.", "error")
    session = None
    if form.get("session_id"):
        session = db.get(ClassSession, parse_int(form.get("session_id")) or 0)
    if session is None:
        session = (db.query(ClassSession).filter(ClassSession.teacher_id == teacher.id, ClassSession.status == "done")
                   .order_by(ClassSession.scheduled_start.desc()).first())
    r = svc.queue_qa_review(db, session, "scheduled", reviewer=user, teacher=teacher)
    log_action(db, user, "create", "qa", entity=r, description=f"Scheduled QA review for {teacher.full_name}", request=request)
    db.commit()
    return redirect(f"/qa/{r.id}", f"Review scheduled for {teacher.full_name}.")


# ----------------------------------------------------------------------------- corrective actions
@router.get("/corrective-actions", include_in_schema=False)
def corrective_actions(request: Request, status: str = "", teacher_id: int | None = None, page: int = 1,
                       db: Session = Depends(get_db), user: User = Depends(require("qa.view"))):
    svc.refresh_overdue_actions(db)
    db.commit()
    query = db.query(CorrectiveAction)
    teacher_ids = sched_svc.scoped_teacher_ids(db, user)
    if teacher_ids is not None:
        query = query.filter(CorrectiveAction.teacher_id.in_(teacher_ids or [-1]))
    if status:
        query = query.filter(CorrectiveAction.status == status)
    if teacher_id:
        query = query.filter(CorrectiveAction.teacher_id == teacher_id)
    pg = paginate(query.order_by(CorrectiveAction.status == "closed", CorrectiveAction.due_date), page, 40)
    counts = dict(db.query(CorrectiveAction.status, func.count(CorrectiveAction.id)).group_by(CorrectiveAction.status).all())
    return render(request, "qa/corrective_actions.html", {
        "user": user, "page": pg, "status": status, "teacher_id": teacher_id,
        "statuses": ACTION_STATUSES, "base_url": f"/qa/corrective-actions?status={status}&teacher_id={teacher_id or ''}",
        "teacher_options": [(t.id, t.full_name) for t in sched_svc.teachers_for(db, user)],
        "stats": {"open": counts.get("open", 0) + counts.get("in_progress", 0), "overdue": counts.get("overdue", 0),
                  "closed": counts.get("closed", 0)}})


@router.post("/corrective-actions/{ca_id}/close", include_in_schema=False)
async def close_action(ca_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.update"))):
    ca = db.get(CorrectiveAction, ca_id)
    if not ca:
        raise HTTPException(404, "Corrective action not found")
    form = await request.form()
    note = (form.get("note") or form.get("reason") or "").strip()
    if not note:
        return redirect("/qa/corrective-actions", "A closure note is required.", "error")
    svc.close_corrective_action(db, ca, user, note, request=request)
    db.commit()
    return redirect("/qa/corrective-actions", "Corrective action closed.")


# ----------------------------------------------------------------------------- trends
@router.get("/trends", include_in_schema=False)
def trends(request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.view"))):
    data = svc.qa_trends(db)
    return render(request, "qa/trends.html", {"user": user, "t": data, "threshold": svc.APPROVAL_THRESHOLD})


# ----------------------------------------------------------------------------- review detail
@router.get("/{id}", include_in_schema=False)
def review_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.view"))):
    r = _get(db, id)
    if r.status == "queued" and r.reviewer_id is None:
        pass
    an = db.get(AIClassAnalysis, r.ai_analysis_id) if r.ai_analysis_id else (r.session.ai_analysis if r.session else None)
    history = (db.query(QAReview).filter(QAReview.teacher_id == r.teacher_id, QAReview.id != r.id,
                                         QAReview.overall_score.isnot(None))
               .order_by(QAReview.completed_at.desc().nullslast(), QAReview.id.desc()).limit(10).all())
    return render(request, "qa/detail.html", {
        "user": user, "r": r, "s": r.session, "an": an, "history": history,
        "criteria": svc.QA_CRITERIA, "weights": svc.QA_WEIGHTS, "threshold": svc.APPROVAL_THRESHOLD,
        "actions": db.query(CorrectiveAction).filter(CorrectiveAction.qa_review_id == r.id).all(),
        "re_evaluations": db.query(QAReview).filter(QAReview.re_evaluation_of_id == r.id).all(),
        "needs_approval": r.status == "completed" and (r.overall_score or 0) < svc.APPROVAL_THRESHOLD})


@router.post("/{id}/complete", include_in_schema=False)
async def complete(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.add", "qa.update", any_of=True))):
    r = _get(db, id)
    form = await request.form()
    scores = {}
    for key, _ in svc.QA_CRITERIA:
        raw = form.get(f"{key}_score")
        if raw in (None, ""):
            return redirect(f"/qa/{r.id}", f"Score every criterion — {key} is missing.", "error")
        v = parse_float(raw)
        if v < 0 or v > 10:
            return redirect(f"/qa/{r.id}", "Scores must be between 0 and 10.", "error")
        scores[key] = v
    svc.complete_qa_review(db, r, scores, user, strengths=form.get("strengths") or "", weaknesses=form.get("weaknesses") or "",
                           comments=form.get("comments") or "", request=request)
    if form.get("send_feedback"):
        svc.send_qa_feedback(db, r, user, request=request)
    db.commit()
    msg = f"Review completed — overall {r.overall_score}/100."
    if (r.overall_score or 0) < svc.APPROVAL_THRESHOLD:
        msg += " Below the threshold: HOD QA approval is required before it counts toward the teacher average."
    return redirect(f"/qa/{r.id}", msg, "warning" if (r.overall_score or 0) < svc.APPROVAL_THRESHOLD else "success")


@router.post("/{id}/approve", include_in_schema=False)
async def approve(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.approve"))):
    r = _get(db, id)
    form = await request.form()
    if r.status != "completed":
        return redirect(f"/qa/{r.id}", "Only a completed review can be approved.", "error")
    svc.approve_qa_review(db, r, user, note=(form.get("note") or form.get("reason") or "").strip(), request=request)
    db.commit()
    return redirect(f"/qa/{r.id}", "Review approved; it now counts toward the teacher's QA average.")


@router.post("/{id}/feedback", include_in_schema=False)
async def feedback(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.update"))):
    r = _get(db, id)
    form = await request.form()
    ok = svc.send_qa_feedback(db, r, user, message=(form.get("message") or "").strip() or None, request=request)
    db.commit()
    if not ok:
        return redirect(f"/qa/{r.id}", "This teacher has no portal account to notify.", "error")
    return redirect(f"/qa/{r.id}", "Feedback sent to the teacher.")


@router.post("/{id}/corrective-action", include_in_schema=False)
async def add_action(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.add", "qa.update", any_of=True))):
    r = _get(db, id)
    form = await request.form()
    description = (form.get("description") or "").strip()
    if not description:
        return redirect(f"/qa/{r.id}", "Describe the corrective action.", "error")
    svc.add_corrective_action(db, r, r.teacher_id, description, parse_date(form.get("due_date")), user, request=request)
    db.commit()
    return redirect(f"/qa/{r.id}", "Corrective action assigned and the teacher was notified.")


@router.post("/{id}/re-evaluate", include_in_schema=False)
async def re_evaluate(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.add"))):
    r = _get(db, id)
    new = svc.create_re_evaluation(db, r, r.session, user, request=request)
    db.commit()
    return redirect(f"/qa/{new.id}", f"Re-evaluation #{new.id} created and linked to review #{r.id}.")


@router.post("/{id}/claim", include_in_schema=False)
async def claim(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.update"))):
    r = _get(db, id)
    r.reviewer_id = user.id
    r.status = "in_review"
    log_action(db, user, "update", "qa", entity=r, description="QA review claimed for scoring", request=request)
    db.commit()
    return redirect(f"/qa/{r.id}", "Review claimed — it is now in review.")
