"""Feedback & Voice of Customer (Module 42): surveys, NPS, sentiment, negative routing and the PUBLIC survey page."""
from __future__ import annotations

import json
from datetime import datetime, date, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import require, csrf_protect, get_optional_user
from app.core.security import verify_signed
from app.core.templating import render
from app.core.utils import redirect, paginate, parse_date, parse_int, parse_bool
from app.database import get_db
from app.models.core import User
from app.models.crm import Survey, Feedback, Case
from app.models.people import Client, Student, Employee, Teacher
from app.services import crm as svc

router = APIRouter(dependencies=[Depends(csrf_protect)])

SENTIMENTS = ["positive", "neutral", "negative"]
AUDIENCES = ["client", "student", "staff"]
DEFAULT_QUESTIONS = [{"key": "nps", "type": "nps", "text": "How likely are you to recommend Online Quran College to a friend or family member?"},
                     {"key": "rating", "type": "rating", "text": "How would you rate the classes this month?"},
                     {"key": "comment", "type": "text", "text": "What could we do better?"}]


def can_see_confidential(user: User) -> bool:
    return bool(user and (user.is_superuser or user.role_slug in ("super_admin", "hod_people")))


def _nps_distribution(rows) -> list[int]:
    dist = [0] * 11
    for r in rows:
        if r.nps is not None and 0 <= r.nps <= 10:
            dist[r.nps] += 1
    return dist


def _monthly_nps(db: Session, months: int = 6, confidential: bool = False) -> dict:
    today = date.today().replace(day=1)
    labels, scores, counts = [], [], []
    for i in range(months - 1, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        ms = date(y, m, 1)
        me = date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
        rows = (db.query(Feedback).filter(Feedback.submitted_at >= datetime.combine(ms, datetime.min.time()),
                                          Feedback.submitted_at <= datetime.combine(me, datetime.max.time()),
                                          Feedback.is_confidential.is_(confidential), Feedback.nps.isnot(None)).all())
        labels.append(ms.strftime("%b %Y"))
        counts.append(len(rows))
        if rows:
            p = sum(1 for r in rows if r.nps >= 9)
            d = sum(1 for r in rows if r.nps <= 6)
            scores.append(round(100 * (p - d) / len(rows)))
        else:
            scores.append(0)
    return {"labels": labels, "scores": scores, "counts": counts}


# ============================================================================= dashboard / responses
@router.get("/feedback", include_in_schema=False)
def feedback_list(request: Request, page: int = 1, q: str = "", sentiment: str = "", status: str = "", survey: str = "",
                  trigger: str = "", db: Session = Depends(get_db), user: User = Depends(require("feedback.view"))):
    query = db.query(Feedback).filter(Feedback.is_confidential.is_(False))
    if q:
        query = query.filter(Feedback.comment.ilike(f"%{q}%"))
    if sentiment:
        query = query.filter(Feedback.sentiment == sentiment)
    if status:
        query = query.filter(Feedback.status == status)
    if survey:
        query = query.filter(Feedback.survey_id == int(survey))
    if trigger:
        query = query.filter(Feedback.trigger == trigger)
    pg = paginate(query.order_by(Feedback.created_at.desc()), page, 25)
    all_rows = db.query(Feedback).filter(Feedback.is_confidential.is_(False), Feedback.status.in_(["submitted", "routed", "resolved"])).all()
    summary = svc.feedback_summary(db, "month")
    open_neg = sum(1 for r in all_rows if r.is_negative and r.status != "resolved")
    return render(request, "feedback/list.html", {
        "user": user, "page": pg, "q": q, "sentiment": sentiment, "status": status, "survey": survey, "trigger": trigger,
        "summary": summary, "dist": _nps_distribution(all_rows), "trend": _monthly_nps(db),
        "sentiments": SENTIMENTS, "statuses": ["pending", "submitted", "routed", "resolved"], "triggers": svc.SURVEY_TRIGGERS,
        "surveys": db.query(Survey).order_by(Survey.name).all(), "open_negative": open_neg,
        "resolved_negative": sum(1 for r in all_rows if r.is_negative and r.status == "resolved"),
        "can_enps": can_see_confidential(user),
        "clients": db.query(Client).filter(Client.status.in_(["active", "trial"])).order_by(Client.full_name).limit(500).all(),
        "base_url": f"/feedback?q={q}&sentiment={sentiment}&status={status}&survey={survey}&trigger={trigger}"})


@router.get("/feedback/surveys", include_in_schema=False)
def surveys(request: Request, db: Session = Depends(get_db), user: User = Depends(require("feedback.view"))):
    rows = db.query(Survey).order_by(Survey.trigger, Survey.name).all()
    counts = dict(db.query(Feedback.survey_id, func.count(Feedback.id)).group_by(Feedback.survey_id).all())
    responded = dict(db.query(Feedback.survey_id, func.count(Feedback.id)).filter(Feedback.status.in_(["submitted", "routed", "resolved"]))
                     .group_by(Feedback.survey_id).all())
    return render(request, "feedback/surveys.html", {"user": user, "surveys": rows, "counts": counts, "responded": responded,
                                                     "triggers": svc.SURVEY_TRIGGERS, "audiences": AUDIENCES,
                                                     "default_questions": json.dumps(DEFAULT_QUESTIONS, indent=2)})


@router.post("/feedback/surveys/new", include_in_schema=False)
async def create_survey(request: Request, db: Session = Depends(get_db), user: User = Depends(require("feedback.add", "feedback.update", any_of=True))):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return redirect("/feedback/surveys", "A survey name is required.", "error")
    try:
        questions = json.loads(form.get("questions") or "[]")
        assert isinstance(questions, list)
    except Exception:
        return redirect("/feedback/surveys", "Questions must be a JSON array.", "error")
    s = Survey(name=name, trigger=form.get("trigger") or "manual", audience=form.get("audience") or "client",
               questions=questions or DEFAULT_QUESTIONS, is_active=parse_bool(form.get("is_active")))
    db.add(s)
    db.flush()
    log_action(db, user, "create", "feedback", entity=s, description=f"Survey '{s.name}' created ({s.trigger})", request=request)
    db.commit()
    return redirect("/feedback/surveys", f"Survey '{name}' created.")


@router.post("/feedback/surveys/{sid}/edit", include_in_schema=False)
async def edit_survey(sid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("feedback.update"))):
    s = db.get(Survey, sid)
    if not s:
        raise HTTPException(404, "Survey not found")
    form = await request.form()
    before = snapshot(s)
    try:
        questions = json.loads(form.get("questions") or "[]")
        assert isinstance(questions, list)
    except Exception:
        return redirect("/feedback/surveys", "Questions must be a JSON array.", "error")
    s.name = (form.get("name") or s.name).strip()
    s.trigger = form.get("trigger") or s.trigger
    s.audience = form.get("audience") or s.audience
    s.questions = questions or s.questions
    s.is_active = parse_bool(form.get("is_active"))
    log_action(db, user, "update", "feedback", entity=s, description=f"Survey '{s.name}' updated", before=before, after=snapshot(s), request=request)
    db.commit()
    return redirect("/feedback/surveys", "Survey updated.")


@router.post("/feedback/send", include_in_schema=False)
async def send_survey(request: Request, db: Session = Depends(get_db), user: User = Depends(require("feedback.add", "feedback.update", any_of=True))):
    form = await request.form()
    s = db.get(Survey, parse_int(form.get("survey_id"))) if form.get("survey_id") else None
    if not s:
        return redirect("/feedback", "Select a survey to send.", "error")
    client = db.get(Client, parse_int(form.get("client_id"))) if form.get("client_id") else None
    student = db.get(Student, parse_int(form.get("student_id"))) if form.get("student_id") else None
    employee = db.get(Employee, parse_int(form.get("employee_id"))) if form.get("employee_id") else None
    if not (client or student or employee):
        return redirect("/feedback", "Select a family or staff member.", "error")
    fb = svc.send_survey(db, s, client=client, student=student, employee=employee, actor=user)
    db.commit()
    return redirect("/feedback", f"Survey sent - link /survey/{fb.token}")


@router.get("/feedback/enps", include_in_schema=False)
def enps(request: Request, db: Session = Depends(get_db), user: User = Depends(require("feedback.view"))):
    if not can_see_confidential(user):
        from app.core.deps import PermissionDenied
        raise PermissionDenied("feedback.confidential")
    rows = db.query(Feedback).filter(Feedback.is_confidential.is_(True)).order_by(Feedback.created_at.desc()).limit(300).all()
    submitted = [r for r in rows if r.status in ("submitted", "routed", "resolved")]
    summary = svc.feedback_summary(db, "quarter")
    return render(request, "feedback/enps.html", {"user": user, "rows": submitted, "summary": summary,
                                                  "dist": _nps_distribution(submitted), "trend": _monthly_nps(db, confidential=True),
                                                  "pending": sum(1 for r in rows if r.status == "pending"),
                                                  "employees": db.query(Employee).filter(Employee.status.in_(["active", "probation"])).order_by(Employee.full_name).all(),
                                                  "surveys": db.query(Survey).filter(Survey.audience == "staff").all()})


@router.get("/feedback/{fid}", include_in_schema=False)
def feedback_detail(fid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("feedback.view"))):
    fb = db.get(Feedback, fid)
    if not fb:
        raise HTTPException(404, "Feedback not found")
    if fb.is_confidential and not can_see_confidential(user):
        from app.core.deps import PermissionDenied
        raise PermissionDenied("feedback.confidential")
    case = db.get(Case, fb.case_id) if fb.case_id else None
    return render(request, "feedback/detail.html", {"user": user, "fb": fb, "case": case, "survey": db.get(Survey, fb.survey_id) if fb.survey_id else None})


@router.post("/feedback/{fid}/resolve", include_in_schema=False)
async def resolve_feedback(fid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("feedback.update"))):
    fb = db.get(Feedback, fid)
    if not fb:
        raise HTTPException(404, "Feedback not found")
    form = await request.form()
    fb.status = "resolved"
    log_action(db, user, "update", "feedback", entity=fb, description=f"Feedback #{fb.id} marked resolved",
               rationale=form.get("rationale") or form.get("note"), request=request)
    db.commit()
    return redirect(f"/feedback/{fb.id}", "Feedback marked resolved.")


# ============================================================================= PUBLIC survey (no auth)
def _load_feedback(db: Session, token: str) -> Feedback:
    raw = verify_signed(token, "survey")
    if not raw:
        raise HTTPException(404, "This survey link is invalid or has expired.")
    fb = db.get(Feedback, int(raw)) if raw.isdigit() else None
    if not fb or fb.token != token:
        raise HTTPException(404, "This survey link is invalid or has expired.")
    return fb


@router.get("/survey/{token}", include_in_schema=False)
def public_survey(token: str, request: Request, db: Session = Depends(get_db)):
    fb = _load_feedback(db, token)
    survey = db.get(Survey, fb.survey_id) if fb.survey_id else None
    ctx = {"user": None, "fb": fb, "survey": survey, "token": token,
           "questions": (survey.questions if survey and survey.questions else DEFAULT_QUESTIONS),
           "student": fb.student.full_name if fb.student else None,
           "done": fb.status in ("submitted", "routed", "resolved")}
    return render(request, "feedback/public.html", ctx)


@router.post("/survey/{token}", include_in_schema=False)
async def public_survey_submit(token: str, request: Request, db: Session = Depends(get_db)):
    fb = _load_feedback(db, token)
    if fb.status in ("submitted", "routed", "resolved"):
        return redirect(f"/survey/{token}", "You have already submitted this survey. JazakAllah Khair.", "info")
    form = await request.form()
    answers = {k: v for k, v in form.items() if k not in ("nps", "rating", "comment")}
    svc.submit_feedback(db, fb, parse_int(form.get("nps")), parse_int(form.get("rating")), form.get("comment"), answers)
    log_action(db, None, "submit", "feedback", entity=fb, description=f"Public survey response #{fb.id} ({fb.sentiment})", request=request)
    db.commit()
    return redirect(f"/survey/{token}", "JazakAllah Khair - your feedback has been recorded.")
