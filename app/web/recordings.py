"""Recordings (Module 24/47): retention-aware library where every open is purpose-logged for safeguarding."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import PermissionDenied, UserContext, csrf_protect, get_user_context, require
from app.core.templating import render
from app.core.utils import paginate, parse_date, parse_int, redirect
from app.database import get_db
from app.models.core import User
from app.models.people import Student, Teacher
from app.models.scheduling import ClassSession, Recording, RecordingAccessLog
from app.services import qa as qa_svc
from app.services import scheduling as svc

router = APIRouter(prefix="/recordings", dependencies=[Depends(csrf_protect)])

MIN_PURPOSE = 5
SOURCES = ["platform", "zoom", "upload"]
REC_STATUSES = ["processing", "available", "analysed", "expired", "deleted"]


def _get(db: Session, id: int) -> Recording:
    r = db.get(Recording, id)
    if not r:
        raise HTTPException(404, "Recording not found")
    return r


def _may_access(db: Session, rec: Recording, user: User, ctx: UserContext) -> bool:
    """recordings.view, or the teacher who taught the class."""
    if rbac.has_permission(user, "recordings.view"):
        if ctx.teacher and not rbac.is_management(user) and user.role_slug == "teacher":
            return bool(rec.session and rec.session.teacher_id == ctx.teacher.id)
        return True
    return bool(ctx.teacher and rec.session and rec.session.teacher_id == ctx.teacher.id)


@router.get("", include_in_schema=False)
def list_recordings(request: Request, page: int = 1, status: str = "", source: str = "", teacher_id: int | None = None,
                    date_from: str = "", date_to: str = "", analysed: str = "", q: str = "",
                    db: Session = Depends(get_db), user: User = Depends(require("recordings.view")),
                    ctx: UserContext = Depends(get_user_context)):
    query = db.query(Recording).join(ClassSession, ClassSession.id == Recording.session_id)
    teacher_ids = svc.scoped_teacher_ids(db, user)
    if teacher_ids is not None:
        query = query.filter(ClassSession.teacher_id.in_(teacher_ids or [-1]))
    if status:
        query = query.filter(Recording.status == status)
    if source:
        query = query.filter(Recording.source == source)
    if teacher_id:
        query = query.filter(ClassSession.teacher_id == teacher_id)
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(ClassSession.date >= df)
    if dt:
        query = query.filter(ClassSession.date <= dt)
    if analysed == "1":
        query = query.filter(Recording.status == "analysed")
    elif analysed == "0":
        query = query.filter(Recording.status != "analysed")
    if q:
        like = f"%{q}%"
        query = query.join(Student, Student.id == ClassSession.student_id).filter(
            or_(Student.full_name.ilike(like), Student.student_code.ilike(like), ClassSession.room_name.ilike(like)))
    pg = paginate(query.order_by(ClassSession.date.desc(), Recording.id.desc()), page, 30)
    counts = dict(db.query(Recording.status, func.count(Recording.id)).group_by(Recording.status).all())
    expiring = (db.query(func.count(Recording.id)).filter(Recording.retention_until.isnot(None),
                                                          Recording.retention_until <= date.today() + timedelta(days=30),
                                                          Recording.status.notin_(["expired", "deleted"])).scalar() or 0)
    base = (f"/recordings?status={status}&source={source}&teacher_id={teacher_id or ''}&date_from={date_from}"
            f"&date_to={date_to}&analysed={analysed}&q={q}")
    return render(request, "recordings/list.html", {
        "user": user, "page": pg, "status": status, "source": source, "teacher_id": teacher_id, "q": q,
        "date_from": df, "date_to": dt, "analysed": analysed, "base_url": base,
        "statuses": REC_STATUSES, "sources": SOURCES,
        "teacher_options": [(t.id, t.full_name) for t in svc.teachers_for(db, user)],
        "stats": {"total": sum(counts.values()), "available": counts.get("available", 0),
                  "analysed": counts.get("analysed", 0), "expiring": expiring},
        "retention_days": qa_svc.retention_days(db)})


@router.get("/ingest", include_in_schema=False)
def ingest_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require("recordings.view"))):
    teacher_ids = svc.scoped_teacher_ids(db, user)
    q = (db.query(ClassSession).outerjoin(Recording, Recording.session_id == ClassSession.id)
         .filter(ClassSession.status == "done", Recording.id.is_(None),
                 ClassSession.date >= date.today() - timedelta(days=30)))
    if teacher_ids is not None:
        q = q.filter(ClassSession.teacher_id.in_(teacher_ids or [-1]))
    rows = q.order_by(ClassSession.date.desc()).limit(100).all()
    return render(request, "recordings/ingest.html", {"user": user, "rows": rows, "sources": SOURCES,
                                                      "retention_days": qa_svc.retention_days(db)})


@router.post("/ingest", include_in_schema=False)
async def ingest(request: Request, db: Session = Depends(get_db), user: User = Depends(require("recordings.add", "recordings.update", any_of=True))):
    form = await request.form()
    ids = [int(i) for i in form.getlist("session_id") if str(i).isdigit()]
    source = form.get("source") or "platform"
    made, skipped = 0, 0
    for sid in ids:
        s = db.get(ClassSession, sid)
        if not s:
            continue
        try:
            qa_svc.ingest_recording(db, s, user, source=source, request=request)
            made += 1
        except ValueError:
            skipped += 1
    db.commit()
    if not ids:
        return redirect("/recordings/ingest", "Select at least one completed class.", "error")
    return redirect("/recordings", f"{made} recording(s) ingested from {source}." + (f" {skipped} skipped." if skipped else ""))


@router.get("/{id}", include_in_schema=False)
def detail(id: int, request: Request, purpose: str = "", db: Session = Depends(get_db),
           user: User = Depends(require("recordings.view", "portal_teacher.view", any_of=True)),
           ctx: UserContext = Depends(get_user_context)):
    rec = _get(db, id)
    if not _may_access(db, rec, user, ctx):
        log_action(db, user, "access_denied", "recordings", entity=rec, severity="warning", consequential=True,
                   description=f"Blocked recording access (session #{rec.session_id})", request=request)
        db.commit()
        raise PermissionDenied("recordings.view")
    opened = len(purpose.strip()) >= MIN_PURPOSE
    if opened:
        qa_svc.log_recording_access(db, rec, user, purpose.strip(), request=request)
        db.commit()
    logs = (db.query(RecordingAccessLog).filter(RecordingAccessLog.recording_id == rec.id)
            .order_by(RecordingAccessLog.accessed_at.desc()).limit(50).all())
    return render(request, "recordings/detail.html", {
        "user": user, "r": rec, "s": rec.session, "opened": opened, "purpose": purpose.strip(),
        "logs": logs, "analysis": rec.session.ai_analysis if rec.session else None,
        "min_purpose": MIN_PURPOSE, "retention_days": qa_svc.retention_days(db)})


@router.post("/{id}/access", include_in_schema=False)
async def request_access(id: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("recordings.view", "portal_teacher.view", any_of=True)),
                         ctx: UserContext = Depends(get_user_context)):
    rec = _get(db, id)
    form = await request.form()
    purpose = (form.get("purpose") or form.get("reason") or "").strip()
    if len(purpose) < MIN_PURPOSE:
        return redirect(f"/recordings/{rec.id}", "State the purpose of the access before opening a recording.", "error")
    from urllib.parse import quote
    return redirect(f"/recordings/{rec.id}?purpose={quote(purpose)}", "Access logged for safeguarding.", "info")


@router.post("/{id}/analyse", include_in_schema=False)
async def analyse(id: int, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require("ai_monitoring.update", "recordings.update", any_of=True))):
    rec = _get(db, id)
    if not rec.session:
        return redirect(f"/recordings/{rec.id}", "This recording is not linked to a class session.", "error")
    an = qa_svc.run_ai_analysis(db, rec.session, force=True)
    log_action(db, user, "execute", "ai_monitoring", entity=an,
               description=f"AI analysis run for session #{rec.session_id} from the recording library", request=request)
    db.commit()
    return redirect(f"/ai-monitoring/{an.id}", "AI analysis completed — human review is required before any action.")
