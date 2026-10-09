"""Needs Attention (docs/ATTENTION.md): what the daily check found, why, and the actions staff can approve."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import parse_int, redirect
from app.database import get_db
from app.models.core import User
from app.models.ops import AttentionItem
from app.services import attention as svc

router = APIRouter(prefix="/attention", dependencies=[Depends(csrf_protect)])

TABS = [("students", "Students"), ("teachers", "Teachers"), ("opportunities", "Referral opportunities"), ("decided", "Decided"),
        ("resolved", "Resolved")]


def _get(db: Session, id: int, user: User) -> AttentionItem:
    item = svc.visible(db, user, db.query(AttentionItem).filter(AttentionItem.id == id)).first()
    if not item:
        raise HTTPException(404, "Item not found")
    return item


def _subject(db: Session, item: AttentionItem) -> dict:
    s = svc.subject_of(db, item)
    if item.subject_type == "student":
        return {"name": s.full_name if s else "?", "url": f"/students/{item.subject_id}/journey",
                "meta": " · ".join(x for x in [s.student_code if s else "", s.course.name if s and s.course else "",
                                               f"teacher {s.teacher.full_name}" if s and s.teacher else ""] if x)}
    if item.subject_type == "family":
        return {"name": s.full_name if s else "?", "url": f"/clients/{item.subject_id}",
                "meta": " · ".join(x for x in [s.client_code if s else "", f"{len(s.students)} child(ren)" if s else ""] if x)}
    return {"name": s.full_name if s else "?", "url": f"/teachers/{item.subject_id}", "meta": s.employee_code if s and hasattr(s, "employee_code") else "Teacher"}


@router.get("", include_in_schema=False)
def board(request: Request, tab: str = "students", level: str = "", signal: str = "", db: Session = Depends(get_db),
          user: User = Depends(require("attention.view"))):
    q = svc.visible(db, user, db.query(AttentionItem))
    if tab == "teachers":
        q = q.filter(AttentionItem.subject_type == "teacher", AttentionItem.status.in_(["open", "snoozed"]))
    elif tab == "opportunities":
        q = q.filter(AttentionItem.kind == "opportunity", AttentionItem.status.in_(["open", "snoozed"]))
    elif tab == "decided":
        q = q.filter(AttentionItem.status.in_(["actioned", "dismissed"]))
    elif tab == "resolved":
        q = q.filter(AttentionItem.status == "resolved")
    else:
        tab = "students"
        q = q.filter(AttentionItem.subject_type == "student", AttentionItem.status.in_(["open", "snoozed"]))
    if level in ("high", "medium", "low"):
        q = q.filter(AttentionItem.level == level)
    order = (AttentionItem.decided_at.desc(), AttentionItem.id.desc()) if tab in ("decided", "resolved") else (AttentionItem.score.desc(), AttentionItem.id)
    items = q.order_by(*order).all()
    if signal:
        items = [i for i in items if any(sg["key"] == signal for sg in (i.signals or []))]
    subjects = {i.id: _subject(db, i) for i in items[:200]}
    signal_keys = sorted({sg["key"]: sg["label"] for i in items for sg in (i.signals or [])}.items(), key=lambda x: x[1])
    from app.services import jobs_attention
    from app.services import scheduling as sched_svc
    last_run = sched_svc.setting_value(db, jobs_attention.MARKER_KEY, None, field=None)
    return render(request, "attention/board.html", {
        "user": user, "items": items[:200], "total": len(items), "subjects": subjects, "tab": tab, "tabs": TABS, "level": level,
        "signal": signal, "signal_keys": signal_keys, "stats": svc.stats(db, user), "last_run": last_run,
        "can_decide": rbac.has_permission(user, "attention.update")})


@router.post("/run", include_in_schema=False)
async def run_now(request: Request, db: Session = Depends(get_db), user: User = Depends(require("attention.update"))):
    result = svc.run_detection(db)
    log_action(db, user, "execute", "attention", description=f"Attention check run by hand: {result}", request=request)
    db.commit()
    return redirect("/attention", f"Checked {result['students']} students, {result['families']} families and {result['teachers']} teachers: "
                                  f"{result['open']} open item(s), {result['high']} high.")


@router.post("/{id}/approve", include_in_schema=False)
async def approve(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("attention.update"))):
    item = _get(db, id, user)
    form = await request.form()
    back = form.get("next") or "/attention"
    try:
        tasks = svc.approve(db, item, user, form.getlist("actions"), form.get("note"), request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(back, str(exc), "error")
    db.commit()
    return redirect(back, f"{len(tasks)} action(s) created and assigned.")


@router.post("/{id}/dismiss", include_in_schema=False)
async def dismiss(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("attention.update"))):
    item = _get(db, id, user)
    form = await request.form()
    back = form.get("next") or "/attention"
    try:
        svc.dismiss(db, item, user, form.get("reason") or "", request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(back, str(exc), "error")
    db.commit()
    return redirect(back, "Dismissed. It will only come back if something new appears.")


@router.post("/{id}/snooze", include_in_schema=False)
async def snooze(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("attention.update"))):
    item = _get(db, id, user)
    form = await request.form()
    back = form.get("next") or "/attention"
    try:
        svc.snooze(db, item, user, parse_int(form.get("days")) or 7, request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(back, str(exc), "error")
    db.commit()
    return redirect(back, f"Snoozed until {item.snoozed_until:%d %b}.")
