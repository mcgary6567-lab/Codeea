"""Student academic journey pages (docs/STUDENT_JOURNEY.md): the timeline with a period selector, the monthly summary,
teacher and class-time history, syllabus progress, assessments with evidence, teacher recommendations, and the
Academy Manager's monthly board."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.deps import PermissionDenied, csrf_protect, require
from app.core.templating import render
from app.core.utils import parse_date, parse_int, redirect
from app.database import get_db
from app.models.academic import RECOMMENDATION_KINDS, StudentRecommendation
from app.models.core import User
from app.models.people import Student, Teacher
from app.services import journey as svc

router = APIRouter(dependencies=[Depends(csrf_protect)])

VIEWS = [("timeline", "Timeline"), ("summary", "Monthly summary"), ("teachers", "Teachers & class times"),
         ("syllabus", "Syllabus & progress"), ("assessments", "Assessments")]


def _student(db: Session, id: int, user: User) -> Student:
    s = db.get(Student, id)
    if not s:
        raise HTTPException(404, "Student not found")
    scope = svc.student_scope(db, user)
    if scope is not None and s.id not in scope:
        raise PermissionDenied("students.view (out of scope)")
    return s


def can_recommend(user: User) -> bool:
    return rbac.has_any(user, ["evaluations.add", "students.update", "portal_teacher.view"])


@router.get("/students/{id}/journey", include_in_schema=False)
def journey(id: int, request: Request, view: str = "timeline", period: str = "current", month: str = "", date_from: str = "",
            date_to: str = "", kinds: str = "", db: Session = Depends(get_db),
            user: User = Depends(require("students.view", "portal_teacher.view", any_of=True))):
    s = _student(db, id, user)
    view = view if view in dict(VIEWS) else "timeline"
    win = svc.resolve_period(db, s, period, month, parse_date(date_from), parse_date(date_to))
    if view == "summary" and win["period"] in ("range", "all"):
        win = svc.resolve_period(db, s, "month", win["end"].strftime("%Y-%m"))
    chosen = {k for k in kinds.split(",") if k in dict(svc.TIMELINE_KINDS)} if kinds else None
    ctx: dict = {"user": user, "s": s, "view": view, "views": VIEWS, "win": win, "periods": svc.PERIOD_CHOICES,
                 "stats": svc.period_stats(db, s, win["start"], win["end"]), "kinds": svc.TIMELINE_KINDS,
                 "chosen": chosen or {k for k, _ in svc.TIMELINE_KINDS}, "can_recommend": can_recommend(user),
                 "recommendation_kinds": RECOMMENDATION_KINDS, "time_label": svc.time_label, "days_label": svc.days_label,
                 "reason_labels": svc.REASON_LABELS, "change_labels": svc.CHANGE_LABELS, "kind_labels": svc.KIND_LABELS,
                 "recommendation_state": svc.recommendation_state, "result_labels": svc.RESULT_LABELS,
                 "query": f"period={win['period']}&month={win['month']}&date_from={date_from}&date_to={date_to}"}
    if view == "timeline":
        ctx["events"] = svc.timeline(db, s, win["start"], win["end"], user, chosen)
    elif view == "summary":
        ctx["m"] = svc.monthly_summary(db, s, win["month"], user)
    elif view == "teachers":
        ctx["history"] = svc.history(db, s)
    elif view == "syllabus":
        ctx["syl"] = svc.syllabus(db, s, win["start"], win["end"])
    elif view == "assessments":
        ctx["evaluations"] = svc.assessments(db, s, win["start"], win["end"])
        ctx["answer_summary"] = svc.answer_summary
    from app.services import attention as attention_svc
    from app.core import rbac as _rbac
    ctx["attention"] = attention_svc.for_student(db, s.id) if _rbac.has_permission(user, "attention.view") else None
    ctx["recent_recommendations"] = (db.query(StudentRecommendation).filter(StudentRecommendation.student_id == s.id)
                                     .order_by(StudentRecommendation.created_at.desc()).limit(6).all())
    db.commit()  # history reconstruction on first view
    return render(request, "students/journey.html", ctx)


@router.post("/students/{id}/recommendations", include_in_schema=False)
async def add_recommendation(id: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("students.view", "portal_teacher.view", any_of=True))):
    s = _student(db, id, user)
    if not can_recommend(user):
        raise PermissionDenied("evaluations.add")
    form = await request.form()
    back = form.get("next") or f"/students/{s.id}/journey"
    if not back.startswith("/"):
        back = f"/students/{s.id}/journey"
    teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first() or s.teacher
    try:
        rec = svc.recommend(db, s, form.get("kind") or "", form.get("note"), user, teacher=teacher, request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(back, str(exc), "error")
    db.commit()
    msg = {"task": f"Recorded. A follow-up was created: {rec.task.title if rec.task else ''}",
           "escalation": "Recorded and escalated: the same concern is still open from before.",
           "alert": "Recorded and flagged for attention."}.get(rec.action, "Recorded on the student's timeline.")
    return redirect(back, msg, "warning" if rec.action == "escalation" else "success")


@router.get("/academics/student-summary", include_in_schema=False)
def summary_board(request: Request, month: str = "", teacher: str = "", q: str = "", flagged: str = "",
                  db: Session = Depends(get_db), user: User = Depends(require("students.view", "portal_teacher.view", any_of=True))):
    period = month if len(month) == 7 else (svc.org_today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    try:
        svc.month_bounds(period)
    except ValueError:
        period = svc.org_today().strftime("%Y-%m")
    scope = svc.student_scope(db, user)
    rows = svc.summary_board(db, period, scope, parse_int(teacher) or None, q, bool(flagged))
    teachers = db.query(Teacher).filter(Teacher.status != "inactive").order_by(Teacher.full_name).all()
    totals = {"students": len(rows), "flagged": sum(1 for r in rows if r["flags"]),
              "held": sum(r["stats"]["held"] for r in rows), "attended": sum(r["stats"]["attended"] for r in rows),
              "absent": sum(r["stats"]["absent"] for r in rows), "late": sum(r["stats"]["late"] for r in rows),
              "lessons": sum(r["lessons_done"] for r in rows)}
    return render(request, "academics/student_summary.html", {
        "user": user, "rows": rows, "period": period, "label": svc.month_bounds(period)[0].strftime("%B %Y"), "teacher": teacher,
        "q": q, "flagged": flagged, "totals": totals, "teacher_options": [(t.id, t.full_name) for t in teachers]})
