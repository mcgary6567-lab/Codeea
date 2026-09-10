"""Module 25 — AI class monitoring: metrics, human review (approve / override / false positive), teacher trends and AI cost."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import paginate, parse_date, parse_float, parse_int, redirect
from app.database import get_db
from app.models.core import AIModelRun, User
from app.models.scheduling import AIClassAnalysis, ClassSession, QAReview, SafeguardingFlag
from app.services import qa as qa_svc
from app.services import scheduling as svc

router = APIRouter(prefix="/ai-monitoring", dependencies=[Depends(csrf_protect)])

RISK_LEVELS = ["low", "medium", "high"]
REVIEW_STATUSES = ["pending", "approved", "overridden", "false_positive"]
OVERRIDE_FIELDS = ["camera_presence_pct", "punctuality_minutes", "duration_compliance_pct", "active_teaching_pct",
                   "student_engagement_score", "curriculum_coverage_pct", "overall_score"]

DISCLAIMER = ("AI output is advisory. No disciplinary or contractual action is ever taken on an AI score alone — "
              "a human reviewer must approve, override or dismiss every finding first.")


def _get(db: Session, id: int) -> AIClassAnalysis:
    an = db.get(AIClassAnalysis, id)
    if not an:
        raise HTTPException(404, "AI analysis not found")
    return an


@router.get("", include_in_schema=False)
def list_analyses(request: Request, page: int = 1, teacher_id: int | None = None, risk: str = "",
                  review_status: str = "", date_from: str = "", date_to: str = "", flagged: str = "",
                  db: Session = Depends(get_db), user: User = Depends(require("ai_monitoring.view"))):
    query = db.query(AIClassAnalysis).join(ClassSession, ClassSession.id == AIClassAnalysis.session_id)
    teacher_ids = svc.scoped_teacher_ids(db, user)
    if teacher_ids is not None:
        query = query.filter(AIClassAnalysis.teacher_id.in_(teacher_ids or [-1]))
    if teacher_id:
        query = query.filter(AIClassAnalysis.teacher_id == teacher_id)
    if risk:
        query = query.filter(AIClassAnalysis.risk_level == risk)
    if review_status:
        query = query.filter(AIClassAnalysis.review_status == review_status)
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(ClassSession.date >= df)
    if dt:
        query = query.filter(ClassSession.date <= dt)
    if flagged == "1":
        query = query.filter(AIClassAnalysis.contact_exchange_detected.is_(True))
    pg = paginate(query.order_by(ClassSession.date.desc(), AIClassAnalysis.id.desc()), page, 30)
    counts = dict(db.query(AIClassAnalysis.risk_level, func.count(AIClassAnalysis.id)).group_by(AIClassAnalysis.risk_level).all())
    pending = db.query(func.count(AIClassAnalysis.id)).filter(AIClassAnalysis.review_status == "pending").scalar() or 0
    avg = db.query(func.avg(AIClassAnalysis.overall_score)).scalar() or 0
    base = (f"/ai-monitoring?teacher_id={teacher_id or ''}&risk={risk}&review_status={review_status}"
            f"&date_from={date_from}&date_to={date_to}&flagged={flagged}")
    return render(request, "ai_monitoring/list.html", {
        "user": user, "page": pg, "teacher_id": teacher_id, "risk": risk, "review_status": review_status,
        "date_from": df, "date_to": dt, "flagged": flagged, "base_url": base, "disclaimer": DISCLAIMER,
        "risk_levels": RISK_LEVELS, "review_statuses": REVIEW_STATUSES,
        "teacher_options": [(t.id, t.full_name) for t in svc.teachers_for(db, user)],
        "stats": {"high": counts.get("high", 0), "medium": counts.get("medium", 0), "low": counts.get("low", 0),
                  "pending": pending, "avg": round(float(avg), 1)},
        "usage": qa_svc.ai_usage_by_month(db)})


@router.get("/teachers", include_in_schema=False)
def teacher_trends(request: Request, db: Session = Depends(get_db), user: User = Depends(require("ai_monitoring.view"))):
    teacher_ids = svc.scoped_teacher_ids(db, user)
    weekly = qa_svc.teacher_ai_trends(db, teacher_ids, weeks=8)
    monthly = qa_svc.teacher_ai_trends(db, teacher_ids, weeks=26)
    monthly_by_id = {r["teacher"].id: r for r in monthly if r["teacher"]}
    for row in weekly:
        m = monthly_by_id.get(row["teacher"].id if row["teacher"] else 0)
        row["monthly"] = m["monthly"] if m else []
    return render(request, "ai_monitoring/teachers.html", {
        "user": user, "rows": weekly, "usage": qa_svc.ai_usage_by_month(db), "disclaimer": DISCLAIMER})


@router.get("/{id}", include_in_schema=False)
def detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("ai_monitoring.view"))):
    an = _get(db, id)
    run = db.get(AIModelRun, an.model_run_id) if an.model_run_id else None
    flags = db.query(SafeguardingFlag).filter(SafeguardingFlag.session_id == an.session_id).all()
    reviews = db.query(QAReview).filter(QAReview.session_id == an.session_id).all()
    return render(request, "ai_monitoring/detail.html", {
        "user": user, "an": an, "s": an.session, "run": run, "flags": flags, "reviews": reviews,
        "override_fields": OVERRIDE_FIELDS, "disclaimer": DISCLAIMER})


@router.post("/{id}/review", include_in_schema=False)
async def review(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("ai_monitoring.update"))):
    an = _get(db, id)
    form = await request.form()
    action = form.get("action") or ""
    note = (form.get("note") or form.get("reason") or "").strip()
    if action not in ("approved", "overridden", "false_positive"):
        return redirect(f"/ai-monitoring/{an.id}", "Choose approve, override or false positive.", "error")
    if action in ("overridden", "false_positive") and not note:
        return redirect(f"/ai-monitoring/{an.id}", "A note is required when overriding or dismissing an AI finding.", "error")
    corrections = {}
    if action == "overridden":
        for f in OVERRIDE_FIELDS:
            raw = form.get(f"corrected_{f}")
            if raw not in (None, ""):
                corrections[f] = int(parse_float(raw)) if f == "punctuality_minutes" else parse_float(raw)
        if form.get("corrected_risk_level") in RISK_LEVELS:
            corrections["risk_level"] = form.get("corrected_risk_level")
    try:
        qa_svc.review_analysis(db, an, user, action, note=note, corrections=corrections or None, request=request)
    except ValueError as exc:
        return redirect(f"/ai-monitoring/{an.id}", str(exc), "error")
    db.commit()
    label = {"approved": "approved", "overridden": "overridden", "false_positive": "dismissed as a false positive"}[action]
    return redirect(f"/ai-monitoring/{an.id}", f"AI analysis {label}.")


@router.post("/{id}/feedback", include_in_schema=False)
async def feedback(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("ai_monitoring.update"))):
    an = _get(db, id)
    form = await request.form()
    sent = qa_svc.send_ai_feedback(db, an, user, message=(form.get("message") or "").strip() or None, request=request)
    db.commit()
    if not sent:
        return redirect(f"/ai-monitoring/{an.id}", "This teacher has no portal account to notify.", "error")
    return redirect(f"/ai-monitoring/{an.id}", "Feedback sent to the teacher.")


@router.post("/{id}/queue-qa", include_in_schema=False)
async def queue_qa(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.add", "ai_monitoring.update", any_of=True))):
    an = _get(db, id)
    r = qa_svc.queue_qa_review(db, an.session, "risk_based", ai_analysis=an)
    log_action(db, user, "create", "qa", entity=r, description=f"QA review queued from AI monitoring for session #{an.session_id}", request=request)
    db.commit()
    return redirect(f"/qa/{r.id}", "QA review queued for a human check of this class.")
