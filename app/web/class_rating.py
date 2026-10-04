"""Class ratings from the portals: a student or family rates an attended class 1-5 with a short comment.

POST /portal/classes/{id}/rate writes ``ClassSession.student_rating`` / ``student_feedback`` when the class belongs
to the signed-in student, or to a child of the signed-in family, and has been held (status ``done``). The form
partial lives in ``student_portal/classes.html`` and ``portal/schedule.html``; the teacher sees the ratings on
``/teacher/qa`` and the QA dashboard flags ratings of 2 or less.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import UserContext, csrf_protect, get_user_context, require
from app.core.utils import parse_int, redirect
from app.database import get_db
from app.models.core import User
from app.models.scheduling import ClassSession

router = APIRouter(prefix="/portal/classes", dependencies=[Depends(csrf_protect)])

RATEABLE_STATUSES = ("done",)
MAX_COMMENT = 500


def session_for_viewer(db: Session, ctx: UserContext, session_id: int) -> ClassSession:
    """The class when the viewer is its student or the family it belongs to; 404 otherwise (no existence leak)."""
    cs = db.get(ClassSession, session_id)
    if not cs:
        raise HTTPException(404, "Class not found")
    if ctx.student and cs.student_id == ctx.student.id:
        return cs
    if ctx.client and cs.student and cs.student.client_id == ctx.client.id:
        return cs
    raise HTTPException(404, "Class not found")


def _back(request: Request, form, ctx: UserContext) -> str:
    nxt = (form.get("next") or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return "/student/classes" if ctx.student else "/portal/schedule"


@router.post("/{id}/rate", include_in_schema=False)
async def rate_class(id: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("portal_student.view", "portal_client.view", any_of=True)),
                     ctx: UserContext = Depends(get_user_context)):
    form = await request.form()
    back = _back(request, form, ctx)
    cs = session_for_viewer(db, ctx, id)
    if cs.status not in RATEABLE_STATUSES:
        return redirect(back, "Only a class that has been held can be rated.", "error")
    rating = parse_int(form.get("rating"))
    if not rating or rating < 1 or rating > 5:
        return redirect(back, "Choose a rating from 1 to 5 stars.", "error")
    comment = (form.get("comment") or "").strip()[:MAX_COMMENT] or None
    before = {"student_rating": cs.student_rating, "student_feedback": cs.student_feedback}
    cs.student_rating = rating
    cs.student_feedback = comment
    who = "student" if ctx.student and cs.student_id == ctx.student.id else "family"
    log_action(db, user, "update", "classes", entity=cs, request=request,
               description=f"Class #{cs.id} on {cs.date} rated {rating}/5 by the {who}" + (" with a comment" if comment else ""),
               before=before, after={"student_rating": rating, "student_feedback": comment, "rated_at": datetime.utcnow().isoformat()},
               severity="warning" if rating <= 2 else "info")
    if rating <= 2 and cs.teacher and getattr(cs.teacher, "supervisor_id", None):
        from app.core.notify import notify
        notify(db, cs.teacher.supervisor_id, "Low class rating",
               f"{cs.student.student_code if cs.student else 'A student'} rated the class on {cs.date} with {cs.teacher.full_name} {rating}/5.",
               event_type="qa_flag", link="/qa/dashboard")
    db.commit()
    return redirect(back, "Thank you - your rating has been recorded." if rating >= 3 else
                    "Thank you - your rating has been recorded and passed to the quality team.")
