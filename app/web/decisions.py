"""Module 48 - Decision register: written-record discipline.

Every consequential decision has a title, a named owner, a written rationale and a status trail
(proposed -> decided -> implemented, or reversed with a second rationale). Free chat is not a record.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import paginate, parse_date, parse_int, redirect
from app.database import get_db
from app.models.core import AuditEvent, Department, User
from app.models.ops import Decision, TrajectoryMeeting

router = APIRouter(prefix="/decisions", dependencies=[Depends(csrf_protect)])

CATEGORIES = ["operational", "academic", "financial", "hr", "strategic"]
STATUSES = ["proposed", "decided", "implemented", "reversed"]
OPEN_STATUSES = ["proposed", "decided"]
NEXT_STATUS = {"proposed": "decided", "decided": "implemented"}


def _opts(db: Session) -> dict:
    return {
        "users": [(u.id, u.full_name) for u in db.query(User).filter(User.is_active.is_(True)).order_by(User.full_name)],
        "departments": [(d.id, d.name) for d in db.query(Department).order_by(Department.name)],
        "meetings": [(m.id, f"{m.meeting_date} · {m.title}") for m in
                     db.query(TrajectoryMeeting).order_by(TrajectoryMeeting.meeting_date.desc()).limit(30)],
        "categories": CATEGORIES, "statuses": STATUSES,
    }


def _overdue_query(db: Session):
    """Decisions past their due date that still have no recorded outcome — the report SLA panel."""
    return (db.query(Decision).filter(Decision.due_date.isnot(None), Decision.due_date < date.today(),
                                      Decision.status.in_(OPEN_STATUSES),
                                      or_(Decision.outcome.is_(None), Decision.outcome == ""))
            .order_by(Decision.due_date))


# ================================================================================================ register
@router.get("", include_in_schema=False)
def register(request: Request, page: int = 1, q: str = "", category: str = "", status: str = "",
             owner_id: int | None = None, department_id: int | None = None, start: str = "", end: str = "",
             db: Session = Depends(get_db), user: User = Depends(require("decisions.view"))):
    query = db.query(Decision)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Decision.title.ilike(like), Decision.rationale.ilike(like)))
    if category:
        query = query.filter(Decision.category == category)
    if status:
        query = query.filter(Decision.status == status)
    if owner_id:
        query = query.filter(Decision.owner_id == owner_id)
    if department_id:
        query = query.filter(Decision.department_id == department_id)
    d_start, d_end = parse_date(start), parse_date(end)
    if d_start:
        query = query.filter(Decision.created_at >= datetime.combine(d_start, datetime.min.time()))
    if d_end:
        query = query.filter(Decision.created_at <= datetime.combine(d_end, datetime.max.time()))
    pg = paginate(query.order_by(Decision.created_at.desc(), Decision.id.desc()), page, 25)
    counts = dict(db.query(Decision.status, func.count(Decision.id)).group_by(Decision.status).all())
    by_category = dict(db.query(Decision.category, func.count(Decision.id)).group_by(Decision.category).all())
    overdue = _overdue_query(db).limit(12).all()
    since = datetime.utcnow() - timedelta(days=30)
    gaps = (db.query(AuditEvent.module, func.count(AuditEvent.id))
            .filter(AuditEvent.is_consequential.is_(True), AuditEvent.created_at >= since)
            .group_by(AuditEvent.module).order_by(func.count(AuditEvent.id).desc()).limit(8).all())
    qs = (f"q={q}&category={category}&status={status}&owner_id={owner_id or ''}"
          f"&department_id={department_id or ''}&start={start}&end={end}")
    return render(request, "decisions/list.html", {
        "user": user, "page": pg, "q": q, "category": category, "status": status, "owner_id": owner_id,
        "department_id": department_id, "start": start, "end": end, "opts": _opts(db), "counts": counts,
        "by_category": by_category, "overdue": overdue, "overdue_total": _overdue_query(db).count(),
        "gaps": gaps, "total": pg.total, "today": date.today(), "qs": qs,
        "dept_names": {d.id: d.name for d in db.query(Department)},
        "open_total": sum(counts.get(s, 0) for s in OPEN_STATUSES),
        "cat_labels": [c.replace("_", " ").title() for c in by_category.keys()],
        "cat_data": list(by_category.values())})


@router.post("/create", include_in_schema=False)
async def create_decision(request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("decisions.add"))):
    f = await request.form()
    title = (f.get("title") or "").strip()
    rationale = (f.get("rationale") or "").strip()
    owner_id = parse_int(f.get("owner_id"))
    if not title:
        return redirect("/decisions", "A decision needs a title.", "error")
    if not owner_id:
        return redirect("/decisions", "A decision without a named owner is not a decision.", "error")
    if len(rationale) < 10:
        return redirect("/decisions", "A written rationale is required (at least a sentence).", "error")
    status = f.get("status") if f.get("status") in ("proposed", "decided") else "decided"
    d = Decision(title=title, category=f.get("category") or "operational", rationale=rationale, owner_id=owner_id,
                 decided_by_id=user.id if status == "decided" else None,
                 meeting_id=parse_int(f.get("meeting_id")), department_id=parse_int(f.get("department_id")),
                 status=status, due_date=parse_date(f.get("due_date")),
                 entity_type=(f.get("entity_type") or None), entity_id=parse_int(f.get("entity_id")))
    db.add(d)
    db.flush()
    if owner_id != user.id:
        notify(db, owner_id, "You own a new decision", f"{d.title} ({d.category}).",
               event_type="decision_owner", link=f"/decisions/{d.id}")
    log_action(db, user, "create", "decisions", entity=d, description=f"Decision recorded: {d.title} [{d.status}]",
               rationale=rationale, request=request)
    db.commit()
    return redirect(f"/decisions/{d.id}", "Decision recorded.")


# ================================================================================================ accountability gaps
@router.get("/gaps", include_in_schema=False)
def gaps(request: Request, days: int = 30, module: str = "", db: Session = Depends(get_db),
         user: User = Depends(require("decisions.view"))):
    days = max(1, min(180, days or 30))
    since = datetime.utcnow() - timedelta(days=days)
    q = db.query(AuditEvent).filter(AuditEvent.is_consequential.is_(True), AuditEvent.created_at >= since)
    if module:
        q = q.filter(AuditEvent.module == module)
    events = q.order_by(AuditEvent.created_at.desc()).limit(400).all()
    documented = {(d.entity_type, d.entity_id) for d in
                  db.query(Decision.entity_type, Decision.entity_id).filter(Decision.entity_type.isnot(None))}
    groups: dict[str, dict] = {}
    for ev in events:
        g = groups.setdefault(ev.module, {"module": ev.module, "events": [], "documented": 0, "undocumented": 0})
        has_record = (ev.entity_type, ev.entity_id) in documented
        if has_record:
            g["documented"] += 1
        else:
            g["undocumented"] += 1
            if len(g["events"]) < 12:
                g["events"].append(ev)
    rows = sorted(groups.values(), key=lambda g: g["undocumented"], reverse=True)
    modules = sorted({ev.module for ev in events})
    total = len(events)
    undocumented = sum(g["undocumented"] for g in rows)
    return render(request, "decisions/gaps.html", {
        "user": user, "rows": rows, "days": days, "module": module, "modules": modules, "total": total,
        "undocumented": undocumented, "documented": total - undocumented, "opts": _opts(db),
        "coverage": round(100 * (total - undocumented) / total, 1) if total else 0,
        "labels": [g["module"].replace("_", " ").title() for g in rows],
        "data": [g["undocumented"] for g in rows]})


# ================================================================================================ detail
@router.get("/{decision_id}", include_in_schema=False)
def detail(decision_id: int, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require("decisions.view"))):
    d = db.query(Decision).filter(Decision.id == decision_id).first()
    if not d:
        return redirect("/decisions", "Decision not found.", "error")
    events = (db.query(AuditEvent)
              .filter(AuditEvent.entity_type == "Decision", AuditEvent.entity_id == d.id)
              .order_by(AuditEvent.created_at.desc()).all())
    linked = []
    if d.entity_type and d.entity_id:
        linked = (db.query(AuditEvent).filter(AuditEvent.entity_type == d.entity_type,
                                              AuditEvent.entity_id == d.entity_id,
                                              AuditEvent.is_consequential.is_(True))
                  .order_by(AuditEvent.created_at.desc()).limit(30).all())
    owner = db.query(User).filter(User.id == d.owner_id).first() if d.owner_id else None
    decided_by = db.query(User).filter(User.id == d.decided_by_id).first() if d.decided_by_id else None
    dept = db.query(Department).filter(Department.id == d.department_id).first() if d.department_id else None
    today = date.today()
    return render(request, "decisions/detail.html", {
        "user": user, "d": d, "events": events, "linked": linked, "owner": owner, "decided_by": decided_by,
        "dept": dept, "opts": _opts(db), "today": today, "statuses": STATUSES,
        "next_status": NEXT_STATUS.get(d.status),
        "overdue": bool(d.due_date and d.due_date < today and d.status in OPEN_STATUSES and not d.outcome),
        "can_update": rbac.has_permission(user, "decisions.update")})


@router.post("/{decision_id}/edit", include_in_schema=False)
async def edit_decision(decision_id: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("decisions.update"))):
    d = db.query(Decision).filter(Decision.id == decision_id).first()
    if not d:
        return redirect("/decisions", "Decision not found.", "error")
    f = await request.form()
    rationale = (f.get("rationale") or "").strip()
    owner_id = parse_int(f.get("owner_id"), d.owner_id)
    if not owner_id:
        return redirect(f"/decisions/{d.id}", "A decision must keep a named owner.", "error")
    if len(rationale) < 10:
        return redirect(f"/decisions/{d.id}", "The rationale cannot be emptied — decisions stay explainable.", "error")
    before = {"title": d.title, "owner_id": d.owner_id, "rationale": d.rationale, "due_date": d.due_date}
    d.title = (f.get("title") or d.title).strip()
    d.category = f.get("category") or d.category
    d.rationale = rationale
    d.owner_id = owner_id
    d.department_id = parse_int(f.get("department_id"))
    d.meeting_id = parse_int(f.get("meeting_id"))
    d.due_date = parse_date(f.get("due_date"))
    log_action(db, user, "update", "decisions", entity=d, description=f"Decision updated: {d.title}",
               rationale=rationale, before=before,
               after={"title": d.title, "owner_id": d.owner_id, "rationale": d.rationale, "due_date": d.due_date},
               request=request)
    db.commit()
    return redirect(f"/decisions/{d.id}", "Decision updated.")


@router.post("/{decision_id}/status", include_in_schema=False)
async def change_status(decision_id: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("decisions.update"))):
    d = db.query(Decision).filter(Decision.id == decision_id).first()
    if not d:
        return redirect("/decisions", "Decision not found.", "error")
    f = await request.form()
    new_status = f.get("status") or ""
    if new_status not in ("proposed", "decided", "implemented"):
        return redirect(f"/decisions/{d.id}", "Use the reversal form to reverse a decision.", "error")
    outcome = (f.get("outcome") or "").strip()
    if new_status == "implemented" and not outcome and not d.outcome:
        return redirect(f"/decisions/{d.id}", "Record the outcome before marking a decision implemented.", "error")
    before = {"status": d.status, "outcome": d.outcome}
    d.status = new_status
    if new_status == "decided" and not d.decided_by_id:
        d.decided_by_id = user.id
    if outcome:
        d.outcome = outcome
    log_action(db, user, "approve", "decisions", entity=d,
               description=f"Decision {before['status']} -> {new_status}: {d.title}",
               rationale=f.get("rationale") or d.rationale, before=before,
               after={"status": d.status, "outcome": d.outcome}, request=request)
    if d.owner_id and d.owner_id != user.id:
        notify(db, d.owner_id, f"Decision marked {new_status}", d.title, event_type="decision_status",
               link=f"/decisions/{d.id}")
    db.commit()
    return redirect(f"/decisions/{d.id}", f"Decision marked {new_status}.")


@router.post("/{decision_id}/outcome", include_in_schema=False)
async def record_outcome(decision_id: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("decisions.update"))):
    d = db.query(Decision).filter(Decision.id == decision_id).first()
    if not d:
        return redirect("/decisions", "Decision not found.", "error")
    f = await request.form()
    outcome = (f.get("outcome") or "").strip()
    if not outcome:
        return redirect(f"/decisions/{d.id}", "An outcome cannot be blank.", "error")
    before = {"outcome": d.outcome}
    d.outcome = outcome
    log_action(db, user, "update", "decisions", entity=d, description="Outcome recorded", before=before,
               after={"outcome": d.outcome}, request=request)
    db.commit()
    return redirect(f"/decisions/{d.id}", "Outcome recorded.")


@router.post("/{decision_id}/reverse", include_in_schema=False)
async def reverse_decision(decision_id: int, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require("decisions.update"))):
    d = db.query(Decision).filter(Decision.id == decision_id).first()
    if not d:
        return redirect("/decisions", "Decision not found.", "error")
    f = await request.form()
    rationale = (f.get("rationale") or f.get("reason") or "").strip()
    if len(rationale) < 10:
        return redirect(f"/decisions/{d.id}",
                        "A reversal needs its own written rationale — why the original decision no longer holds.",
                        "error")
    before = {"status": d.status, "outcome": d.outcome}
    d.status = "reversed"
    d.outcome = ((d.outcome + "\n\n") if d.outcome else "") + f"Reversed on {date.today().isoformat()}: {rationale}"
    log_action(db, user, "override", "decisions", entity=d, description=f"Decision reversed: {d.title}",
               rationale=rationale, before=before, after={"status": d.status, "outcome": d.outcome},
               severity="warning", request=request)
    for uid in {d.owner_id, d.decided_by_id} - {user.id, None}:
        notify(db, uid, "Decision reversed", f"{d.title} was reversed by {user.full_name}.",
               event_type="decision_reversed", link=f"/decisions/{d.id}")
    db.commit()
    return redirect(f"/decisions/{d.id}", "Decision reversed with a written rationale.")
