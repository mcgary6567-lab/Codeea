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
from app.models.erp import FEEDBACK_APPLIES_TO, FeedbackQuestion
from app.models.people import Client, Student, Employee, Teacher
from app.models.scheduling import ClassSession
from app.services import crm as svc

router = APIRouter(dependencies=[Depends(csrf_protect)])

SENTIMENTS = ["positive", "neutral", "negative"]
FEEDBACK_SOURCES = [("manual", "Manual"), ("app", "App"), ("web_portal", "Web Portal"), ("client_portal", "Client Portal")]
AUDIENCES = ["client", "student", "staff"]
DEFAULT_QUESTIONS = [{"key": "nps", "type": "nps", "text": "How likely are you to recommend Online Quran College to a friend or family member?"},
                     {"key": "rating", "type": "rating", "text": "How would you rate the classes this month?"},
                     {"key": "comment", "type": "text", "text": "What could we do better?"}]


def can_see_confidential(user: User) -> bool:
    return bool(user and (user.is_superuser or user.role_slug in ("super_admin", "hod_people")))


# ============================================================================= QA Feedback Questions (Academic Configuration)
def active_questions(db: Session, applies_to: str) -> list[FeedbackQuestion]:
    """The active catalogue questions a feedback form asks its audience, in Sort No order."""
    if applies_to not in FEEDBACK_APPLIES_TO:
        return []
    return (db.query(FeedbackQuestion)
            .filter(FeedbackQuestion.applies_to == applies_to, FeedbackQuestion.status == "active")
            .order_by(FeedbackQuestion.sort_no, FeedbackQuestion.id).all())


def question_field(fq: FeedbackQuestion) -> str:
    return f"fq_{fq.id}"


def collect_answers(form, questions: list[FeedbackQuestion]) -> tuple[dict, list[FeedbackQuestion]]:
    """Read the catalogue answers off a submitted form.

    Returns ``({question_id: answer}, [required questions left blank])``. Keys are the question ids as strings
    (JSON object keys are strings anyway); a rating is stored as an int, yes/no as "yes"/"no", text as the text.
    """
    answers: dict = {}
    missing: list[FeedbackQuestion] = []
    for fq in questions:
        raw = (form.get(question_field(fq)) or "").strip()
        value = None
        if fq.answer_type == "rating":
            n = parse_int(raw)
            value = n if n is not None and 1 <= n <= 5 else None
        elif fq.answer_type == "yes_no":
            value = raw.lower() if raw.lower() in ("yes", "no") else None
        else:
            value = raw or None
        if value is None:
            if fq.is_required:
                missing.append(fq)
            continue
        answers[str(fq.id)] = value
    return answers, missing


def question_answers(db: Session, fb: Feedback) -> tuple[list[tuple[FeedbackQuestion, object]], dict]:
    """Split Feedback.answers into (question, answer) pairs for catalogue questions and the remaining free keys."""
    raw = fb.answers or {}
    ids = [int(k) for k in raw if str(k).isdigit()]
    questions = {q.id: q for q in db.query(FeedbackQuestion).filter(FeedbackQuestion.id.in_(ids)).all()} if ids else {}
    pairs = [(questions[int(k)], raw[k]) for k in raw if str(k).isdigit() and int(k) in questions]
    pairs.sort(key=lambda p: (p[0].sort_no, p[0].id))
    other = {k: v for k, v in raw.items() if not (str(k).isdigit() and int(k) in questions)}
    return pairs, other


def missing_message(missing: list[FeedbackQuestion]) -> str:
    first = missing[0].question
    more = f" (and {len(missing) - 1} more)" if len(missing) > 1 else ""
    return f"Please answer the required question: {first}{more}"


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
    question_rows, other_answers = question_answers(db, fb)
    return render(request, "feedback/detail.html", {"user": user, "fb": fb, "case": case, "survey": db.get(Survey, fb.survey_id) if fb.survey_id else None,
                                                    "question_rows": question_rows, "other_answers": other_answers})


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


# ============================================================================= ERP: Client Feedbacks (Quality Management)
@router.get("/qa/feedbacks", include_in_schema=False)
def client_feedbacks(request: Request, page: int = 1, date_from: str = "", date_to: str = "", client_id: int | None = None,
                     feedback_source: str = "", rating: int | None = None, teacher_id: int | None = None, q: str = "",
                     db: Session = Depends(get_db), user: User = Depends(require("feedback.view"))):
    base_q = db.query(Feedback).filter(Feedback.is_confidential.is_(False), Feedback.respondent_type != "staff",
                                       Feedback.status.in_(["submitted", "routed", "resolved"]))
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        base_q = base_q.filter(Feedback.submitted_at >= datetime.combine(df, datetime.min.time()))
    if dt:
        base_q = base_q.filter(Feedback.submitted_at <= datetime.combine(dt, datetime.max.time()))
    if client_id:
        base_q = base_q.filter(Feedback.client_id == client_id)
    if feedback_source:
        base_q = base_q.filter(Feedback.feedback_source == feedback_source)
    if teacher_id:
        base_q = base_q.filter(Feedback.teacher_id == teacher_id)
    if q:
        base_q = base_q.filter(Feedback.comment.ilike(f"%{q}%"))
    query = base_q
    if rating:
        query = query.filter(Feedback.rating == rating)
    pg = paginate(query.order_by(Feedback.submitted_at.desc().nullslast(), Feedback.id.desc()), page, 25)
    rows = base_q.with_entities(Feedback.rating).all()
    rated = [r[0] for r in rows if r[0] is not None]
    stats = {"total": len(rows), "avg": round(sum(rated) / len(rated), 2) if rated else 0.0,
             "five": sum(1 for r in rated if r == 5), "low": sum(1 for r in rated if r <= 2)}
    filter_qs = (f"date_from={date_from}&date_to={date_to}&client_id={client_id or ''}&feedback_source={feedback_source}"
                 f"&teacher_id={teacher_id or ''}&q={q}")
    clients = db.query(Client).filter(Client.status.in_(["active", "trial", "regular", "on_leave", "frozen"])).order_by(Client.full_name).limit(600).all()
    students = db.query(Student).filter(Student.status.in_(["active", "trial", "frozen"])).order_by(Student.full_name).limit(800).all()
    recent_sessions = (db.query(ClassSession).filter(ClassSession.status == "done", ClassSession.date >= date.today() - timedelta(days=14))
                       .order_by(ClassSession.date.desc(), ClassSession.start_time.desc()).limit(300).all())
    return render(request, "feedback/client_feedbacks.html", {
        "user": user, "page": pg, "stats": stats, "date_from": df, "date_to": dt, "client_id": client_id, "feedback_source": feedback_source,
        "rating": rating, "teacher_id": teacher_id, "q": q, "sources": FEEDBACK_SOURCES, "ratings": [(n, "%d Star%s" % (n, "" if n == 1 else "s")) for n in range(1, 6)],
        "client_options": [(c.id, f"{c.client_code} - {c.full_name}") for c in clients],
        "student_options": [(s.id, f"{s.student_code} - {s.full_name}") for s in students],
        "teacher_options": [(t.id, t.full_name) for t in db.query(Teacher).filter(Teacher.status != "inactive").order_by(Teacher.full_name)],
        "session_options": [(s.id, f"#{s.id} {s.date.strftime('%d %b')} {s.start_time.strftime('%H:%M')} - {s.student.full_name if s.student else ''} / {s.teacher.full_name if s.teacher else ''}") for s in recent_sessions],
        "base_url": f"/qa/feedbacks?rating={rating or ''}&{filter_qs}", "filter_qs": filter_qs, "today_iso": date.today().isoformat(),
        "questions": active_questions(db, "client")})


@router.post("/qa/feedbacks/new", include_in_schema=False)
async def client_feedback_create(request: Request, db: Session = Depends(get_db),
                                 user: User = Depends(require("feedback.add", "feedback.update", any_of=True))):
    form = await request.form()
    client = db.get(Client, parse_int(form.get("client_id")) or 0)
    student = db.get(Student, parse_int(form.get("student_id")) or 0) if form.get("student_id") else None
    if not client and student:
        client = student.client
    if not client:
        return redirect("/qa/feedbacks", "Select the client who gave the feedback.", "error")
    if student and student.client_id != client.id:
        return redirect("/qa/feedbacks", "That student does not belong to the selected client.", "error")
    rating = parse_int(form.get("rating"))
    if not rating or rating < 1 or rating > 5:
        return redirect("/qa/feedbacks", "Choose a rating between 1 and 5 stars.", "error")
    answers, missing = collect_answers(form, active_questions(db, "client"))
    if missing:
        return redirect("/qa/feedbacks", missing_message(missing), "error")
    session = db.get(ClassSession, parse_int(form.get("session_id")) or 0) if form.get("session_id") else None
    teacher = db.get(Teacher, parse_int(form.get("teacher_id")) or 0) if form.get("teacher_id") else None
    if not teacher:
        teacher = (session.teacher if session else None) or (student.teacher if student else None)
    if not student and session:
        student = session.student
    fb = Feedback(trigger="manual", respondent_type="client", client_id=client.id, student_id=student.id if student else None,
                  teacher_id=teacher.id if teacher else None, session_id=session.id if session else None, feedback_source="manual",
                  status="pending", sent_at=datetime.utcnow(), is_confidential=False)
    db.add(fb)
    db.flush()
    when = parse_date(form.get("date"))
    submitted_at = datetime.combine(when, datetime.utcnow().time()) if when else datetime.utcnow()
    # submit_feedback sets sentiment / is_negative and routes ratings <= 2 to a QA complaint Case
    answers.update({"entered_by": user.full_name, "channel": "manual"})
    svc.submit_feedback(db, fb, None, rating, form.get("comment"), answers, submitted_at=submitted_at)
    log_action(db, user, "create", "feedback", entity=fb, after=snapshot(fb), request=request,
               description=f"Manual feedback #{fb.id} recorded for {client.full_name}: {rating}/5" + (f" (case #{fb.case_id} opened)" if fb.case_id else ""))
    db.commit()
    msg = f"Feedback recorded ({rating}/5)."
    if fb.case_id:
        msg += " A complaint case was opened for the QA team because the rating is 2 stars or lower."
    return redirect("/qa/feedbacks", msg, "warning" if fb.case_id else "success")


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
           "catalogue_questions": active_questions(db, fb.respondent_type or (survey.audience if survey else "client")),
           "student": fb.student.full_name if fb.student else None,
           "done": fb.status in ("submitted", "routed", "resolved")}
    return render(request, "feedback/public.html", ctx)


@router.post("/survey/{token}", include_in_schema=False)
async def public_survey_submit(token: str, request: Request, db: Session = Depends(get_db)):
    fb = _load_feedback(db, token)
    if fb.status in ("submitted", "routed", "resolved"):
        return redirect(f"/survey/{token}", "You have already submitted this survey. JazakAllah Khair.", "info")
    form = await request.form()
    survey = db.get(Survey, fb.survey_id) if fb.survey_id else None
    catalogue = active_questions(db, fb.respondent_type or (survey.audience if survey else "client"))
    # The survey link is the Survey row's own questions; the catalogue is asked alongside and stored when answered.
    # Required-ness is enforced by the page (the controls carry `required`), not refused here, so a survey
    # answered from an older link or a plain NPS post still lands.
    catalogue_answers, _missing = collect_answers(form, catalogue)
    answers = {k: v for k, v in form.items() if k not in ("nps", "rating", "comment") and not k.startswith("fq_")}
    answers.update(catalogue_answers)
    svc.submit_feedback(db, fb, parse_int(form.get("nps")), parse_int(form.get("rating")), form.get("comment"), answers)
    log_action(db, None, "submit", "feedback", entity=fb, description=f"Public survey response #{fb.id} ({fb.sentiment})", request=request)
    db.commit()
    return redirect(f"/survey/{token}", "JazakAllah Khair - your feedback has been recorded.")
