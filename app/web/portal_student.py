"""Student portal (/student). Scoped strictly to ctx.student."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.deps import require, csrf_protect, get_user_context, UserContext, PermissionDenied
from app.core.templating import render
from app.core.utils import paginate
from app.database import get_db
from app.models.academic import Certificate, Evaluation, MonthlyTest
from app.models.core import User, Notification
from app.models.people import Student
from app.models.scheduling import ClassSession, Attendance
from app.services import people as svc
from app.services.classes import student_attendance_pct

router = APIRouter(prefix="/student", dependencies=[Depends(csrf_protect)])


def me(ctx: UserContext) -> Student:
    if not ctx.student:
        raise PermissionDenied("portal_student.view (no student profile linked to this login)")
    return ctx.student


@router.get("", include_in_schema=False)
def home(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_student.view")),
         ctx: UserContext = Depends(get_user_context)):
    s = me(ctx)
    nxt = (db.query(ClassSession).filter(ClassSession.student_id == s.id, ClassSession.status.in_(["pending", "started"]),
                                         ClassSession.scheduled_start >= datetime.utcnow() - timedelta(hours=3))
           .order_by(ClassSession.scheduled_start).first())
    recent = (db.query(ClassSession).filter(ClassSession.student_id == s.id, ClassSession.status.in_(["done", "absent", "missed", "leave"]))
              .order_by(ClassSession.scheduled_start.desc()).limit(6).all())
    notes = db.query(Notification).filter(Notification.user_id == user.id, Notification.channel == "in_app").order_by(Notification.created_at.desc()).limit(6).all()
    return render(request, "student_portal/home.html", {
        "user": user, "s": s, "next": nxt, "next_local": svc.to_client_tz(nxt.scheduled_start, s.timezone) if nxt else None,
        "recent": recent, "notifications": notes,
        "attendance": student_attendance_pct(db, s.id, 30), "p": svc.student_progress_summary(db, s)})


@router.get("/classes", include_in_schema=False)
def classes(request: Request, page: int = 1, db: Session = Depends(get_db),
            user: User = Depends(require("portal_student.view")), ctx: UserContext = Depends(get_user_context)):
    s = me(ctx)
    pg = paginate(db.query(ClassSession).filter(ClassSession.student_id == s.id).order_by(ClassSession.scheduled_start.desc()), page, 30)
    items = [{"cs": c, "local": svc.to_client_tz(c.scheduled_start, s.timezone)} for c in pg.items]
    return render(request, "student_portal/classes.html", {"user": user, "s": s, "page": pg, "items": items,
                                                           "attendance": student_attendance_pct(db, s.id, 30),
                                                           "base_url": "/student/classes"})


@router.get("/progress", include_in_schema=False)
def progress(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_student.view")),
             ctx: UserContext = Depends(get_user_context)):
    s = me(ctx)
    evals = db.query(Evaluation).filter(Evaluation.student_id == s.id).order_by(Evaluation.date.desc()).limit(12).all()
    return render(request, "student_portal/progress.html", {"user": user, "s": s, "p": svc.student_progress_summary(db, s),
                                                            "evaluations": evals})


@router.get("/lesson", include_in_schema=False)
def lesson(request: Request, user: User = Depends(require("portal_student.view")), ctx: UserContext = Depends(get_user_context)):
    s = me(ctx)
    return RedirectResponse(f"/academics/arabic-view/{s.id}", status_code=303)


@router.get("/results", include_in_schema=False)
def results(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_student.view")),
            ctx: UserContext = Depends(get_user_context)):
    s = me(ctx)
    tests = db.query(MonthlyTest).filter(MonthlyTest.student_id == s.id).order_by(MonthlyTest.period.desc()).all()
    return render(request, "student_portal/results.html", {"user": user, "s": s, "tests": tests})


@router.get("/certificates", include_in_schema=False)
def certificates(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_student.view")),
                 ctx: UserContext = Depends(get_user_context)):
    s = me(ctx)
    rows = db.query(Certificate).filter(Certificate.student_id == s.id, Certificate.is_revoked.is_(False)).order_by(Certificate.issued_at.desc()).all()
    return render(request, "student_portal/certificates.html", {"user": user, "s": s, "rows": rows})
