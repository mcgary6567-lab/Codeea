"""Quality Management (ERP parity, docs/AUDIT_ACADEMICS.md 3.9) + Module 17 QA sampling, scored reviews,
corrective actions, HOD approval and trends.

Static ERP paths (/qa/dashboard, /qa/calls, /qa/calls/unmatched, /qa/queue, /qa/reviewed, /qa/teacher-performance,
/qa/config) are declared before the /qa/{id} review routes.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.audit import log_action, snapshot
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import paginate, parse_date, parse_float, parse_int, redirect
from app.database import get_db
from app.models.core import User
from app.models.erp import CALL_SOURCES, CallRecord, QAIssueType, QAReviewParameter
from app.models.people import Student, Teacher
from app.models.scheduling import AIClassAnalysis, ClassSession, CorrectiveAction, QAReview
from app.services import calls as calls_svc
from app.services import qa as svc
from app.services import scheduling as sched_svc

router = APIRouter(prefix="/qa", dependencies=[Depends(csrf_protect)])

REVIEW_STATUSES = ["queued", "in_review", "completed", "approved", "flagged", "rejected"]
CALL_REVIEW_STATUSES = [("queued", "Pending"), ("in_review", "In-Progress"), ("completed", "Completed"),
                        ("flagged", "Flagged"), ("rejected", "Rejected")]
SAMPLE_TYPES = ["random", "risk_based", "scheduled", "complaint", "re_evaluation", "call"]
ACTION_STATUSES = ["open", "in_progress", "overdue", "closed"]
REVIEW_STATES = [("unmapped", "Unmapped"), ("mapped", "Mapped"), ("queued", "Queued"), ("in_review", "In-Progress"), ("reviewed", "Reviewed")]


def _get(db: Session, id: int) -> QAReview:
    r = db.get(QAReview, id)
    if not r:
        raise HTTPException(404, "QA review not found")
    return r


def _get_call(db: Session, cid: int) -> CallRecord:
    c = db.get(CallRecord, cid)
    if not c:
        raise HTTPException(404, "Call record not found")
    return c


def _teacher_options(db: Session, user: User) -> list[tuple[int, str]]:
    return [(t.id, t.full_name) for t in sched_svc.teachers_for(db, user)]


def _period(date_from: str, date_to: str, default_days: int = 30) -> tuple[date, date]:
    dt = parse_date(date_to) or date.today()
    df = parse_date(date_from) or (dt - timedelta(days=default_days))
    return df, dt


def _scope_calls(query, teacher_ids: Optional[list[int]]):
    if teacher_ids is not None:
        query = query.filter(CallRecord.teacher_id.in_(teacher_ids or [-1]))
    return query


# ============================================================================= A. QA Dashboard
@router.get("/dashboard", include_in_schema=False)
def dashboard(request: Request, date_from: str = "", date_to: str = "", db: Session = Depends(get_db),
              user: User = Depends(require("qa.view"))):
    df, dt = _period(date_from, date_to, 30)
    teacher_ids = sched_svc.scoped_teacher_ids(db, user)
    d = calls_svc.qa_dashboard(db, df, dt, teacher_ids)
    return render(request, "qa/dashboard.html", {
        "user": user, "d": d, "date_from": df, "date_to": dt, "statuses": CALL_REVIEW_STATUSES,
        "issue_labels": [n for n, _ in d["issue_breakdown"]], "issue_values": [v for _, v in d["issue_breakdown"]],
        "perf_labels": [t.full_name for t, _, _ in d["teacher_performance"]],
        "perf_values": [avg for _, avg, _ in d["teacher_performance"]]})


# ============================================================================= C. Call Recordings
@router.get("/calls", include_in_schema=False)
def calls(request: Request, page: int = 1, date_from: str = "", date_to: str = "", teacher_id: int | None = None,
          source: str = "", review_state: str = "", q: str = "", db: Session = Depends(get_db),
          user: User = Depends(require("qa.view"))):
    teacher_ids = sched_svc.scoped_teacher_ids(db, user)
    query = _scope_calls(db.query(CallRecord), teacher_ids)
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(CallRecord.recording_date >= df)
    if dt:
        query = query.filter(CallRecord.recording_date <= dt)
    if teacher_id:
        query = query.filter(CallRecord.teacher_id == teacher_id)
    if source:
        query = query.filter(CallRecord.source == source.upper())
    if review_state:
        query = query.filter(CallRecord.review_state == review_state)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(CallRecord.source_name.ilike(like), CallRecord.external_id.ilike(like), CallRecord.recording_url.ilike(like)))
    pg = paginate(query.order_by(CallRecord.recording_date.desc(), CallRecord.start_time.desc(), CallRecord.id.desc()), page, 30)
    tiles_q = _scope_calls(db.query(CallRecord.source, func.count(CallRecord.id)), teacher_ids)
    if df:
        tiles_q = tiles_q.filter(CallRecord.recording_date >= df)
    if dt:
        tiles_q = tiles_q.filter(CallRecord.recording_date <= dt)
    by_source = dict(tiles_q.group_by(CallRecord.source).all())
    base = (f"/qa/calls?date_from={date_from}&date_to={date_to}&teacher_id={teacher_id or ''}&source={source}"
            f"&review_state={review_state}&q={q}")
    return render(request, "qa/calls.html", {
        "user": user, "page": pg, "date_from": df, "date_to": dt, "teacher_id": teacher_id, "source": source,
        "review_state": review_state, "q": q, "base_url": base, "sources": CALL_SOURCES, "review_states": REVIEW_STATES,
        "teacher_options": _teacher_options(db, user),
        "stats": {"total": sum(by_source.values()), **{s.lower(): by_source.get(s, 0) for s in CALL_SOURCES}},
        "filter_qs": f"date_from={date_from}&date_to={date_to}"})


@router.post("/calls/sync", include_in_schema=False)
async def calls_sync(request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.execute", "qa.add", any_of=True))):
    form = await request.form()
    days = max(1, min(60, parse_int(form.get("days"), 14) or 14))
    n = calls_svc.sync_calls(db, user, days=days, request=request)
    db.commit()
    return redirect("/qa/calls", f"{n} calls synced from Agent / Teams / Zoom." if n else "No new recordings were found on the call platforms.",
                    "success" if n else "info")


# ============================================================================= D. Agent Un-Matched Calls
@router.get("/calls/unmatched", include_in_schema=False)
def calls_unmatched(request: Request, page: int = 1, date_from: str = "", date_to: str = "", teacher_id: int | None = None,
                    db: Session = Depends(get_db), user: User = Depends(require("qa.view"))):
    teacher_ids = sched_svc.scoped_teacher_ids(db, user)
    query = _scope_calls(db.query(CallRecord).filter(CallRecord.session_id.is_(None)), teacher_ids)
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(CallRecord.recording_date >= df)
    if dt:
        query = query.filter(CallRecord.recording_date <= dt)
    if teacher_id:
        query = query.filter(CallRecord.teacher_id == teacher_id)
    pg = paginate(query.order_by(CallRecord.recording_date.desc(), CallRecord.id.desc()), page, 25)
    candidates = {c.id: calls_svc.candidate_sessions_for(db, c) for c in pg.items}
    total_unmatched = _scope_calls(db.query(func.count(CallRecord.id)).filter(CallRecord.session_id.is_(None)), teacher_ids).scalar() or 0
    agent_unmatched = _scope_calls(db.query(func.count(CallRecord.id)).filter(CallRecord.session_id.is_(None), CallRecord.source == "AGENT"), teacher_ids).scalar() or 0
    mapped = _scope_calls(db.query(func.count(CallRecord.id)).filter(CallRecord.review_state == "mapped"), teacher_ids).scalar() or 0
    return render(request, "qa/unmatched.html", {
        "user": user, "page": pg, "date_from": df, "date_to": dt, "teacher_id": teacher_id, "candidates": candidates,
        "teacher_options": _teacher_options(db, user),
        "base_url": f"/qa/calls/unmatched?date_from={date_from}&date_to={date_to}&teacher_id={teacher_id or ''}",
        "stats": {"unmatched": total_unmatched, "agent": agent_unmatched, "mapped": mapped}})


@router.post("/calls/{cid}/map", include_in_schema=False)
async def call_map(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.update", "qa.add", any_of=True))):
    call = _get_call(db, cid)
    form = await request.form()
    session = db.get(ClassSession, parse_int(form.get("session_id")) or 0)
    if not session:
        return redirect("/qa/calls/unmatched", "Pick the class session this recording belongs to.", "error")
    taken = db.query(CallRecord).filter(CallRecord.session_id == session.id, CallRecord.id != call.id).first()
    if taken:
        return redirect("/qa/calls/unmatched", f"Class session #{session.id} is already mapped to call #{taken.id}.", "error")
    calls_svc.map_call(db, call, session, user, request=request)
    db.commit()
    return redirect("/qa/calls/unmatched", f"Call #{call.id} mapped to class #{session.id}; it is now in the QA review queue.")


# ============================================================================= E. QA Review Queue
@router.get("/queue", include_in_schema=False)
def review_queue(request: Request, page: int = 1, source: str = "", teacher_id: int | None = None, date_from: str = "", date_to: str = "",
                 db: Session = Depends(get_db), user: User = Depends(require("qa.view"))):
    teacher_ids = sched_svc.scoped_teacher_ids(db, user)
    base_q = _scope_calls(db.query(CallRecord).filter(CallRecord.review_state.in_(calls_svc.QUEUE_STATES)), teacher_ids)
    query = base_q
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(CallRecord.recording_date >= df)
    if dt:
        query = query.filter(CallRecord.recording_date <= dt)
    if source:
        query = query.filter(CallRecord.source == source.upper())
    if teacher_id:
        query = query.filter(CallRecord.teacher_id == teacher_id)
    pg = paginate(query.order_by(CallRecord.recording_date, CallRecord.start_time, CallRecord.id), page, 30)
    by_source = dict(base_q.with_entities(CallRecord.source, func.count(CallRecord.id)).group_by(CallRecord.source).all())
    return render(request, "qa/queue.html", {
        "user": user, "page": pg, "source": source, "teacher_id": teacher_id, "date_from": df, "date_to": dt,
        "sources": CALL_SOURCES, "teacher_options": _teacher_options(db, user),
        "base_url": f"/qa/queue?source={source}&teacher_id={teacher_id or ''}&date_from={date_from}&date_to={date_to}",
        "stats": {**{s.lower(): by_source.get(s, 0) for s in CALL_SOURCES}, "total": sum(by_source.values())}})


@router.post("/calls/{cid}/start-review", include_in_schema=False)
async def start_review(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.add", "qa.update", any_of=True))):
    call = _get_call(db, cid)
    if call.review_state == "reviewed" and call.qa_review_id:
        return redirect(f"/qa/{call.qa_review_id}", "This call has already been reviewed.", "info")
    try:
        r = calls_svc.start_review(db, call, user, request=request)
    except ValueError as e:
        return redirect("/qa/queue", str(e), "error")
    db.commit()
    return redirect(f"/qa/{r.id}", f"Review #{r.id} started for call #{call.id} - rate each parameter and complete it.")


# ============================================================================= G. Reviewed Calls
@router.get("/reviewed", include_in_schema=False)
def reviewed(request: Request, page: int = 1, status: str = "", date_from: str = "", date_to: str = "", teacher_id: int | None = None,
             db: Session = Depends(get_db), user: User = Depends(require("qa.view"))):
    teacher_ids = sched_svc.scoped_teacher_ids(db, user)
    base_q = db.query(QAReview).filter(QAReview.sample_type == "call")
    if teacher_ids is not None:
        base_q = base_q.filter(QAReview.teacher_id.in_(teacher_ids or [-1]))
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        base_q = base_q.filter(QAReview.created_at >= datetime.combine(df, datetime.min.time()))
    if dt:
        base_q = base_q.filter(QAReview.created_at <= datetime.combine(dt, datetime.max.time()))
    if teacher_id:
        base_q = base_q.filter(QAReview.teacher_id == teacher_id)
    query = base_q
    if status == "completed":
        query = query.filter(QAReview.status.in_(["completed", "approved"]))
    elif status:
        query = query.filter(QAReview.status == status)
    pg = paginate(query.order_by(QAReview.reviewed_at.desc().nullslast(), QAReview.id.desc()), page, 30)
    rows = base_q.all()
    call_ids = [r.call_record_id for r in pg.items if r.call_record_id]
    calls_by_id = {c.id: c for c in db.query(CallRecord).filter(CallRecord.id.in_(call_ids or [-1])).all()}
    stats = {"completed": sum(1 for r in rows if r.status in ("completed", "approved")),
             "in_review": sum(1 for r in rows if r.status == "in_review"),
             "queued": sum(1 for r in rows if r.status == "queued"), "total": len(rows),
             "five": sum(1 for r in rows if r.overall_rating is not None and r.overall_rating >= 4.5 and r.status in ("completed", "approved")),
             "low": sum(1 for r in rows if r.overall_rating is not None and r.overall_rating < 2.5 and r.status in ("completed", "approved"))}
    return render(request, "qa/reviewed.html", {
        "user": user, "page": pg, "status": status, "date_from": df, "date_to": dt, "teacher_id": teacher_id, "stats": stats,
        "statuses": CALL_REVIEW_STATUSES, "calls_by_id": calls_by_id, "teacher_options": _teacher_options(db, user),
        "base_url": f"/qa/reviewed?status={status}&date_from={date_from}&date_to={date_to}&teacher_id={teacher_id or ''}",
        "filter_qs": f"date_from={date_from}&date_to={date_to}&teacher_id={teacher_id or ''}"})


# ============================================================================= H. Teacher QA Performance
@router.get("/teacher-performance", include_in_schema=False)
def teacher_performance(request: Request, date_from: str = "", date_to: str = "", teacher_id: int | None = None, overall: str = "",
                        db: Session = Depends(get_db), user: User = Depends(require("qa.view"))):
    df, dt = _period(date_from, date_to, 30)
    teacher_ids = sched_svc.scoped_teacher_ids(db, user)
    show_all = bool(overall)
    rows = calls_svc.teacher_qa_performance(db, df, dt, None if show_all else teacher_id, teacher_ids)
    if show_all:
        present = {r["teacher"].id for r in rows}
        for t in sched_svc.teachers_for(db, user):
            if t.id not in present:
                rows.append({"teacher": t, "calls": 0, "reviewed": 0, "avg_rating": None, "issues": 0, "critical": 0, "score_pct": None})
        rows.sort(key=lambda x: (-(x["score_pct"] if x["score_pct"] is not None else -1), x["teacher"].full_name))
    rated = [r for r in rows if r["avg_rating"] is not None]
    summary = {"teachers": len(rows), "calls": sum(r["calls"] for r in rows), "reviewed": sum(r["reviewed"] for r in rows),
               "avg": round(sum(r["avg_rating"] for r in rated) / len(rated), 2) if rated else 0.0,
               "critical": sum(r["critical"] for r in rows)}
    return render(request, "qa/teacher_performance.html", {
        "user": user, "rows": rows, "date_from": df, "date_to": dt, "teacher_id": teacher_id, "overall": show_all, "summary": summary,
        "teacher_options": _teacher_options(db, user),
        "chart_labels": [r["teacher"].full_name for r in rows if r["score_pct"] is not None],
        "chart_values": [r["score_pct"] for r in rows if r["score_pct"] is not None]})


# ============================================================================= I. Configurations
@router.get("/config", include_in_schema=False)
def config(request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.configure"))):
    params = db.query(QAReviewParameter).order_by(QAReviewParameter.sort_no, QAReviewParameter.id).all()
    issues = db.query(QAIssueType).order_by(QAIssueType.severity.desc(), QAIssueType.name).all()
    return render(request, "qa/config.html", {
        "user": user, "params": params, "issues": issues,
        "stats": {"params": len(params), "params_active": sum(1 for p in params if p.status == "active"),
                  "issues": len(issues), "critical": sum(1 for i in issues if i.severity == "critical" and i.status == "active")}})


def _param_from_form(form, p: QAReviewParameter) -> Optional[str]:
    name = (form.get("name") or "").strip()
    if not name:
        return "A parameter name is required."
    p.name = name[:80]
    p.description = (form.get("description") or "").strip()[:200] or None
    p.weight = max(0.1, min(10.0, parse_float(form.get("weight"), 1.0) or 1.0))
    p.max_rating = max(1, min(10, parse_int(form.get("max_rating"), 5) or 5))
    p.sort_no = parse_int(form.get("sort_no"), 0) or 0
    if form.get("status") in ("active", "inactive"):
        p.status = form.get("status")
    return None


@router.post("/config/parameters/new", include_in_schema=False)
async def param_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.configure"))):
    form = await request.form()
    p = QAReviewParameter(status="active")
    err = _param_from_form(form, p)
    if err:
        return redirect("/qa/config", err, "error")
    if db.query(QAReviewParameter).filter(func.lower(QAReviewParameter.name) == p.name.lower()).first():
        return redirect("/qa/config", f"A parameter named '{p.name}' already exists.", "error")
    db.add(p)
    db.flush()
    log_action(db, user, "create", "qa", entity=p, description=f"QA review parameter '{p.name}' created (weight {p.weight})", after=snapshot(p), request=request)
    db.commit()
    return redirect("/qa/config", f"Parameter '{p.name}' added.")


@router.post("/config/parameters/{pid}/edit", include_in_schema=False)
async def param_edit(pid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.configure"))):
    p = db.get(QAReviewParameter, pid)
    if not p:
        raise HTTPException(404, "Parameter not found")
    form = await request.form()
    before = snapshot(p)
    err = _param_from_form(form, p)
    if err:
        return redirect("/qa/config", err, "error")
    log_action(db, user, "update", "qa", entity=p, description=f"QA review parameter '{p.name}' updated", before=before, after=snapshot(p), request=request)
    db.commit()
    return redirect("/qa/config", f"Parameter '{p.name}' updated.")


@router.post("/config/parameters/{pid}/toggle", include_in_schema=False)
async def param_toggle(pid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.configure"))):
    p = db.get(QAReviewParameter, pid)
    if not p:
        raise HTTPException(404, "Parameter not found")
    before = p.status
    p.status = "inactive" if p.status == "active" else "active"
    log_action(db, user, "update", "qa", entity=p, description=f"QA review parameter '{p.name}' {p.status}", before={"status": before}, after={"status": p.status}, request=request)
    db.commit()
    return redirect("/qa/config", f"Parameter '{p.name}' is now {p.status}.")


def _issue_from_form(form, i: QAIssueType) -> Optional[str]:
    name = (form.get("name") or "").strip()
    if not name:
        return "An issue type name is required."
    i.name = name[:80]
    i.severity = "critical" if form.get("severity") == "critical" else "normal"
    i.description = (form.get("description") or "").strip()[:200] or None
    if form.get("status") in ("active", "inactive"):
        i.status = form.get("status")
    return None


@router.post("/config/issue-types/new", include_in_schema=False)
async def issue_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.configure"))):
    form = await request.form()
    i = QAIssueType(status="active")
    err = _issue_from_form(form, i)
    if err:
        return redirect("/qa/config", err, "error")
    if db.query(QAIssueType).filter(func.lower(QAIssueType.name) == i.name.lower()).first():
        return redirect("/qa/config", f"An issue type named '{i.name}' already exists.", "error")
    db.add(i)
    db.flush()
    log_action(db, user, "create", "qa", entity=i, description=f"QA issue type '{i.name}' created ({i.severity})", after=snapshot(i), request=request)
    db.commit()
    return redirect("/qa/config", f"Issue type '{i.name}' added.")


@router.post("/config/issue-types/{iid}/edit", include_in_schema=False)
async def issue_edit(iid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.configure"))):
    i = db.get(QAIssueType, iid)
    if not i:
        raise HTTPException(404, "Issue type not found")
    form = await request.form()
    before = snapshot(i)
    err = _issue_from_form(form, i)
    if err:
        return redirect("/qa/config", err, "error")
    log_action(db, user, "update", "qa", entity=i, description=f"QA issue type '{i.name}' updated", before=before, after=snapshot(i), request=request)
    db.commit()
    return redirect("/qa/config", f"Issue type '{i.name}' updated.")


@router.post("/config/issue-types/{iid}/toggle", include_in_schema=False)
async def issue_toggle(iid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.configure"))):
    i = db.get(QAIssueType, iid)
    if not i:
        raise HTTPException(404, "Issue type not found")
    before = i.status
    i.status = "inactive" if i.status == "active" else "active"
    log_action(db, user, "update", "qa", entity=i, description=f"QA issue type '{i.name}' {i.status}", before={"status": before}, after={"status": i.status}, request=request)
    db.commit()
    return redirect("/qa/config", f"Issue type '{i.name}' is now {i.status}.")


# ============================================================================= J. Legacy sampled-review list (Module 17)
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
        "teacher_options": _teacher_options(db, user),
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
        "teacher_options": _teacher_options(db, user),
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


# ============================================================================= F. Review detail + call review form
@router.get("/{id}", include_in_schema=False)
def review_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.view"))):
    r = _get(db, id)
    an = db.get(AIClassAnalysis, r.ai_analysis_id) if r.ai_analysis_id else (r.session.ai_analysis if r.session else None)
    history = (db.query(QAReview).filter(QAReview.teacher_id == r.teacher_id, QAReview.id != r.id,
                                         or_(QAReview.overall_score.isnot(None), QAReview.overall_rating.isnot(None)))
               .order_by(QAReview.completed_at.desc().nullslast(), QAReview.id.desc()).limit(10).all())
    call = db.get(CallRecord, r.call_record_id) if r.call_record_id else None
    params = calls_svc.active_parameters(db)
    issue_types = calls_svc.active_issue_types(db)
    selected_issues = {i.get("type"): i for i in (r.issues or []) if isinstance(i, dict)}
    is_call = r.sample_type == "call" or call is not None
    editable = is_call and r.status in ("queued", "in_review")
    return render(request, "qa/detail.html", {
        "user": user, "r": r, "s": r.session, "an": an, "history": history, "call": call, "is_call": is_call, "editable": editable,
        "params": params, "issue_types": issue_types, "selected_issues": selected_issues,
        "criteria": svc.QA_CRITERIA, "weights": svc.QA_WEIGHTS, "threshold": svc.APPROVAL_THRESHOLD,
        "actions": db.query(CorrectiveAction).filter(CorrectiveAction.qa_review_id == r.id).all(),
        "re_evaluations": db.query(QAReview).filter(QAReview.re_evaluation_of_id == r.id).all(),
        "needs_approval": r.status == "completed" and not is_call and (r.overall_score or 0) < svc.APPROVAL_THRESHOLD})


@router.post("/{id}/review", include_in_schema=False)
async def call_review_action(id: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("qa.add", "qa.update", any_of=True))):
    """Save / Complete / Flag / Reject a call review (1-5 star parameters + issue checklist)."""
    r = _get(db, id)
    form = await request.form()
    action = (form.get("action") or "save").lower()
    if r.status not in ("queued", "in_review"):
        return redirect(f"/qa/{r.id}", f"This review is {r.status} and can no longer be edited.", "error")
    params = calls_svc.active_parameters(db)
    scores = {p.name: form.get(f"param_{p.id}") for p in params}
    issues = []
    for it in calls_svc.active_issue_types(db):
        if form.get(f"issue_{it.id}"):
            issues.append({"type": it.name, "critical": it.severity == "critical", "note": (form.get(f"issue_note_{it.id}") or "").strip() or None})
    overall = parse_float(form.get("overall_rating")) if form.get("overall_rating") else None
    calls_svc.save_review(db, r, user, scores, issues, overall, form.get("remarks") or "", request=request, log=(action == "save"))
    reason = (form.get("reason") or "").strip()
    try:
        if action == "complete":
            missing = [p.name for p in params if p.name not in (r.parameter_scores or {})]
            if missing:
                db.commit()
                return redirect(f"/qa/{r.id}", "Rate every parameter before completing: " + ", ".join(missing), "error")
            calls_svc.complete_review(db, r, user, request=request)
            msg, level = f"Review #{r.id} completed - {r.overall_rating} / 5 stars.", "success"
        elif action == "flag":
            calls_svc.flag_review(db, r, user, reason, request=request)
            msg, level = f"Review #{r.id} flagged for management attention.", "warning"
        elif action == "reject":
            calls_svc.reject_review(db, r, user, reason, request=request)
            msg, level = f"Review #{r.id} rejected.", "info"
        else:
            msg, level = "Review saved (In-Progress).", "success"
    except ValueError as e:
        db.rollback()
        return redirect(f"/qa/{r.id}", str(e), "error")
    db.commit()
    return redirect(f"/qa/{r.id}", msg, level)


@router.post("/{id}/complete", include_in_schema=False)
async def complete(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("qa.add", "qa.update", any_of=True))):
    r = _get(db, id)
    form = await request.form()
    scores = {}
    for key, _ in svc.QA_CRITERIA:
        raw = form.get(f"{key}_score")
        if raw in (None, ""):
            return redirect(f"/qa/{r.id}", f"Score every criterion - {key} is missing.", "error")
        v = parse_float(raw)
        if v < 0 or v > 10:
            return redirect(f"/qa/{r.id}", "Scores must be between 0 and 10.", "error")
        scores[key] = v
    svc.complete_qa_review(db, r, scores, user, strengths=form.get("strengths") or "", weaknesses=form.get("weaknesses") or "",
                           comments=form.get("comments") or "", request=request)
    if form.get("send_feedback"):
        svc.send_qa_feedback(db, r, user, request=request)
    db.commit()
    msg = f"Review completed - overall {r.overall_score}/100."
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
    if r.call_record_id:
        call = db.get(CallRecord, r.call_record_id)
        if call:
            call.review_state = "in_review"
    log_action(db, user, "update", "qa", entity=r, description="QA review claimed for scoring", request=request)
    db.commit()
    return redirect(f"/qa/{r.id}", "Review claimed - it is now in review.")
