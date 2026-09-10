"""Module 47 — Safeguarding & anti-poaching.

Anti-poaching material (contact exchange, off-platform contact, social discovery) is CEO/GM-only.
Managers with ``safeguarding.view`` see conduct flags only. Every access is audit-logged.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import PermissionDenied, csrf_protect, require, require_ceo
from app.core.templating import render
from app.core.utils import paginate, parse_date, parse_int, redirect
from app.database import get_db
from app.models.core import RiskAlert, User
from app.models.people import Student, Teacher
from app.models.scheduling import (AIClassAnalysis, ClassSession, Recording, RecordingAccessLog, SafeguardingFlag, Schedule)

router = APIRouter(prefix="/safeguarding", dependencies=[Depends(csrf_protect)])

FLAG_TYPES = ["contact_exchange", "off_platform_contact", "social_discovery", "conduct", "recording_access"]
ANTI_POACHING_TYPES = {"contact_exchange", "off_platform_contact", "social_discovery"}
STATUSES = ["open", "investigating", "confirmed", "dismissed"]
SEVERITIES = ["low", "medium", "high", "critical"]


def _visible_types(user: User) -> list[str] | None:
    """None = all types (CEO/GM). Otherwise the restricted list."""
    return None if rbac.is_ceo(user) else ["conduct", "recording_access"]


def _log_access(db: Session, user: User, request: Request, what: str, entity=None) -> None:
    log_action(db, user, "safeguarding_access", "safeguarding", entity=entity,
               description=f"Safeguarding record accessed: {what}", request=request, consequential=True, severity="warning")
    db.commit()


@router.get("", include_in_schema=False)
def list_flags(request: Request, page: int = 1, status: str = "", flag_type: str = "", severity: str = "",
               db: Session = Depends(get_db), user: User = Depends(require("safeguarding.view"))):
    allowed = _visible_types(user)
    query = db.query(SafeguardingFlag)
    if allowed is not None:
        query = query.filter(SafeguardingFlag.flag_type.in_(allowed))
    if status:
        query = query.filter(SafeguardingFlag.status == status)
    if flag_type:
        query = query.filter(SafeguardingFlag.flag_type == flag_type)
    if severity:
        query = query.filter(SafeguardingFlag.severity == severity)
    pg = paginate(query.order_by(SafeguardingFlag.status != "open", SafeguardingFlag.created_at.desc()), page, 30)
    counts = dict(db.query(SafeguardingFlag.status, func.count(SafeguardingFlag.id)).group_by(SafeguardingFlag.status).all())
    poaching = (db.query(func.count(SafeguardingFlag.id))
                .filter(SafeguardingFlag.flag_type.in_(list(ANTI_POACHING_TYPES))).scalar() or 0)
    _log_access(db, user, request, f"flag list ({'full' if allowed is None else 'conduct only'})")
    return render(request, "safeguarding/list.html", {
        "user": user, "page": pg, "status": status, "flag_type": flag_type, "severity": severity,
        "statuses": STATUSES, "severities": SEVERITIES,
        "flag_types": FLAG_TYPES if allowed is None else allowed,
        "restricted": allowed is not None, "ceo_only_count": poaching,
        "base_url": f"/safeguarding?status={status}&flag_type={flag_type}&severity={severity}",
        "stats": {"open": counts.get("open", 0), "investigating": counts.get("investigating", 0),
                  "confirmed": counts.get("confirmed", 0), "dismissed": counts.get("dismissed", 0)}})


@router.get("/recording-access", include_in_schema=False)
def recording_access(request: Request, page: int = 1, user_id: int | None = None, days: int = 30,
                     db: Session = Depends(get_db), user: User = Depends(require("safeguarding.view"))):
    since = datetime.utcnow() - timedelta(days=max(1, min(365, days)))
    query = db.query(RecordingAccessLog).filter(RecordingAccessLog.accessed_at >= since)
    if user_id:
        query = query.filter(RecordingAccessLog.user_id == user_id)
    pg = paginate(query.order_by(RecordingAccessLog.accessed_at.desc()), page, 40)
    top = (db.query(RecordingAccessLog.user_id, func.count(RecordingAccessLog.id))
           .filter(RecordingAccessLog.accessed_at >= since).group_by(RecordingAccessLog.user_id)
           .order_by(func.count(RecordingAccessLog.id).desc()).limit(10).all())
    users = {u.id: u for u in db.query(User).filter(User.id.in_([u for u, _ in top] or [-1]))}
    _log_access(db, user, request, "platform-wide recording access log")
    return render(request, "safeguarding/recording_access.html", {
        "user": user, "page": pg, "days": days, "user_id": user_id,
        "top": [{"user": users.get(uid), "count": n} for uid, n in top],
        "base_url": f"/safeguarding/recording-access?days={days}&user_id={user_id or ''}",
        "total": pg.total})


@router.get("/unverified", include_in_schema=False)
def unverified(request: Request, db: Session = Depends(get_db), user: User = Depends(require("safeguarding.view"))):
    rows = []
    for t in db.query(Teacher).filter(Teacher.is_verified.is_(False)).order_by(Teacher.full_name).all():
        schedules = db.query(func.count(Schedule.id)).filter(Schedule.teacher_id == t.id, Schedule.status == "active").scalar() or 0
        upcoming = (db.query(func.count(ClassSession.id))
                    .filter(ClassSession.teacher_id == t.id, ClassSession.status == "pending",
                            ClassSession.date >= date.today()).scalar() or 0)
        rows.append({"teacher": t, "schedules": schedules, "upcoming": upcoming,
                     "breach": schedules > 0 or upcoming > 0})
    alerts = (db.query(RiskAlert).filter(RiskAlert.alert_type == "unverified_teacher")
              .order_by(RiskAlert.created_at.desc()).limit(20).all())
    _log_access(db, user, request, "unverified teacher gate report")
    return render(request, "safeguarding/unverified.html", {
        "user": user, "rows": rows, "alerts": alerts, "breaches": sum(1 for r in rows if r["breach"])})


@router.post("/unverified/scan", include_in_schema=False)
def unverified_scan(request: Request, db: Session = Depends(get_db), user: User = Depends(require("safeguarding.update", "safeguarding.view", any_of=True))):
    from app.services.jobs_classes import unverified_teacher_gate_job
    from app.services.scheduling import set_setting
    set_setting(db, "job_unverified_gate_last_run", {"date": "", "at": ""})
    result = unverified_teacher_gate_job(db)
    log_action(db, user, "execute", "safeguarding", description=f"Unverified teacher gate scan: {result}", request=request)
    db.commit()
    return redirect("/safeguarding/unverified", f"Gate scan complete — {result}.")


@router.get("/policy", include_in_schema=False)
def policy(request: Request, db: Session = Depends(get_db), user: User = Depends(require("safeguarding.view"))):
    masked_calls = db.query(func.count(RecordingAccessLog.id)).scalar() or 0
    return render(request, "safeguarding/policy.html", {"user": user, "access_events": masked_calls})


@router.post("/report", include_in_schema=False)
async def report_concern(request: Request, db: Session = Depends(get_db), user: User = Depends(require("safeguarding.add", "safeguarding.view", any_of=True))):
    form = await request.form()
    evidence = (form.get("evidence") or "").strip()
    if not evidence:
        return redirect("/safeguarding", "Describe the concern before submitting it.", "error")
    flag_type = form.get("flag_type") if form.get("flag_type") in FLAG_TYPES else "conduct"
    fl = SafeguardingFlag(flag_type=flag_type, severity=form.get("severity") if form.get("severity") in SEVERITIES else "medium",
                          teacher_id=parse_int(form.get("teacher_id")), student_id=parse_int(form.get("student_id")),
                          session_id=parse_int(form.get("session_id")), evidence=evidence, source="manual", status="open",
                          visibility="ceo_only" if flag_type in ANTI_POACHING_TYPES else "management")
    db.add(fl)
    db.flush()
    db.add(RiskAlert(alert_type="safeguarding_report", severity=fl.severity,
                     title=f"Safeguarding concern reported ({flag_type.replace('_', ' ')})",
                     message=evidence[:300], entity_type="SafeguardingFlag", entity_id=fl.id,
                     visibility=fl.visibility, status="open", source="manual"))
    log_action(db, user, "create", "safeguarding", entity=fl, description=f"Safeguarding concern reported ({flag_type})",
               rationale=evidence[:400], request=request, consequential=True, severity="warning")
    db.commit()
    return redirect("/safeguarding", "Concern recorded and escalated to the safeguarding lead.")


@router.get("/{id}", include_in_schema=False)
def flag_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("safeguarding.view"))):
    fl = db.get(SafeguardingFlag, id)
    if not fl:
        raise HTTPException(404, "Safeguarding flag not found")
    allowed = _visible_types(user)
    if allowed is not None and fl.flag_type not in allowed:
        log_action(db, user, "access_denied", "safeguarding", entity=fl, severity="warning", consequential=True,
                   description=f"Blocked access to a CEO-only safeguarding flag (#{fl.id})", request=request)
        db.commit()
        raise PermissionDenied("safeguarding (CEO only)")
    _log_access(db, user, request, f"flag #{fl.id} ({fl.flag_type})", entity=fl)
    an = db.query(AIClassAnalysis).filter(AIClassAnalysis.session_id == fl.session_id).first() if fl.session_id else None
    return render(request, "safeguarding/detail.html", {
        "user": user, "f": fl, "an": an, "s": db.get(ClassSession, fl.session_id) if fl.session_id else None,
        "statuses": STATUSES,
        "history": (db.query(SafeguardingFlag).filter(SafeguardingFlag.teacher_id == fl.teacher_id, SafeguardingFlag.id != fl.id)
                    .order_by(SafeguardingFlag.created_at.desc()).limit(10).all() if fl.teacher_id else [])})


@router.post("/{id}/status", include_in_schema=False)
async def set_flag_status(id: int, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("safeguarding.update", "safeguarding.approve", any_of=True))):
    fl = db.get(SafeguardingFlag, id)
    if not fl:
        raise HTTPException(404, "Safeguarding flag not found")
    allowed = _visible_types(user)
    if allowed is not None and fl.flag_type not in allowed:
        raise PermissionDenied("safeguarding (CEO only)")
    form = await request.form()
    status = form.get("status") or ""
    resolution = (form.get("resolution") or form.get("reason") or "").strip()
    if status not in STATUSES:
        return redirect(f"/safeguarding/{fl.id}", "Invalid status.", "error")
    if status in ("confirmed", "dismissed") and not resolution:
        return redirect(f"/safeguarding/{fl.id}", "A resolution note is required to confirm or dismiss a flag.", "error")
    before = {"status": fl.status, "resolution": fl.resolution}
    fl.status = status
    fl.handled_by_id = user.id
    if resolution:
        fl.resolution = resolution
    for a in db.query(RiskAlert).filter(RiskAlert.entity_type == "SafeguardingFlag", RiskAlert.entity_id == fl.id).all():
        a.status = "resolved" if status in ("confirmed", "dismissed") else "acknowledged"
        a.acknowledged_by_id = user.id
    log_action(db, user, "safeguarding_access", "safeguarding", entity=fl,
               description=f"Safeguarding flag #{fl.id} marked {status}", rationale=resolution, before=before,
               after={"status": status, "resolution": resolution}, request=request, consequential=True, severity="warning")
    db.commit()
    return redirect(f"/safeguarding/{fl.id}", f"Flag marked {status}.")
