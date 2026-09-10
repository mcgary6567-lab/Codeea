"""Section 13 / Module 40 - Transformation OS.

The institution's own build tracker: which internal system is at what maturity, who owns each role,
how people transition between roles, the six-month development journeys, and the weekly operating
rhythm (trajectory meetings, reporting deadlines, department scorecard accountability).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import parse_date, parse_int, redirect
from app.database import get_db
from app.models.core import Department, Role, Setting, User
from app.models.ops import (KPI, Decision, DepartmentScorecard, Task, TransformationItem, TransitionRecord,
                            TrajectoryMeeting)
from app.models.people import DevelopmentPlan, Employee
from app.services import kpi as kpi_svc

router = APIRouter(prefix="/transformation", dependencies=[Depends(csrf_protect)])

STATES = [("planned", "Planned", "slate"), ("alpha", "Alpha", "amber"),
          ("beta", "Beta", "indigo"), ("full_launch", "Full launch", "emerald")]
STATE_KEYS = [s[0] for s in STATES]
TRANSITION_STATUSES = ["planned", "in_progress", "completed"]
MEETING_STATUSES = ["scheduled", "held", "cancelled"]

DEFAULT_AGENDA = [
    "Review of last week's decisions and actions (owner by owner)",
    "Department scorecards: score, highlights, risks",
    "Institutional health score and red KPIs",
    "Student growth, retention and churn trajectory",
    "Finance: collections, receivables, payroll ratio",
    "Quality: QA sampling, AI monitoring flags, corrective actions",
    "Transformation OS: systems moving alpha -> beta -> full launch",
    "People: transitions, development plans, AI fluency",
    "New decisions (owner + rationale recorded before the meeting ends)",
]

TABS = [("tracker", "OS master tracker", "/transformation"),
        ("roles", "Role architecture", "/transformation/roles"),
        ("transitions", "Transitions", "/transformation/transitions"),
        ("development", "Development plans", "/transformation/development"),
        ("governance", "Operating rhythm", "/transformation/governance"),
        ("meetings", "Trajectory meetings", "/transformation/meetings")]


# ------------------------------------------------------------------------------------------------ helpers
def _setting(db: Session, key: str, default=None):
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row and row.value is not None else default


def _set_setting(db: Session, key: str, value, group: str = "governance", description: str = "") -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if not row:
        row = Setting(key=key, group=group, description=description or key)
        db.add(row)
    row.value = value


def _opts(db: Session) -> dict:
    return {
        "departments": [(d.id, d.name) for d in db.query(Department).order_by(Department.name)],
        "users": [(u.id, u.full_name) for u in db.query(User).filter(User.is_active.is_(True)).order_by(User.full_name)],
        "employees": [(e.id, f"{e.employee_code} · {e.full_name}") for e in
                      db.query(Employee).order_by(Employee.employee_code).limit(400)],
        "states": [(s[0], s[1]) for s in STATES],
        "transition_statuses": TRANSITION_STATUSES,
        "meeting_statuses": MEETING_STATUSES,
    }


def _scorecard_summary(db: Session, period: str) -> list[dict]:
    out = []
    for d in db.query(Department).filter(Department.is_active.is_(True)).order_by(Department.name):
        sc = (db.query(DepartmentScorecard)
              .filter(DepartmentScorecard.department_id == d.id, DepartmentScorecard.period == period).first())
        out.append({"department": d, "submission": sc,
                    "status": sc.status if sc else "missing",
                    "score": sc.score if sc else None,
                    "band": kpi_svc.health_band(sc.score if sc else None)})
    return out


# ================================================================================================ OS master tracker
@router.get("", include_in_schema=False)
def tracker(request: Request, department_id: int | None = None, state: str = "", owner_id: int | None = None,
            db: Session = Depends(get_db), user: User = Depends(require("transformation.view"))):
    q = db.query(TransformationItem)
    if department_id:
        q = q.filter(TransformationItem.department_id == department_id)
    if state in STATE_KEYS:
        q = q.filter(TransformationItem.state == state)
    if owner_id:
        q = q.filter(TransformationItem.owner_id == owner_id)
    items = q.order_by(TransformationItem.target_date.is_(None), TransformationItem.target_date,
                       TransformationItem.system_name).all()
    board = {k: [i for i in items if i.state == k] for k in STATE_KEYS}
    counts = {k: len(v) for k, v in board.items()}
    today = date.today()
    overdue = [i for i in items if i.target_date and i.target_date < today and i.state != "full_launch"]
    avg = round(sum(i.progress_pct for i in items) / len(items), 1) if items else 0
    period = kpi_svc.period_key()
    dept_progress = []
    for d in db.query(Department).filter(Department.is_active.is_(True)).order_by(Department.name):
        rows = [i for i in items if i.department_id == d.id]
        dept_progress.append({"name": d.name,
                              "pct": int(round(sum(r.progress_pct for r in rows) / len(rows))) if rows else 0,
                              "count": len(rows),
                              "launched": len([r for r in rows if r.state == "full_launch"])})
    return render(request, "transformation/tracker.html", {
        "user": user, "tab": "tracker", "tabs": TABS, "items": items, "board": board, "states": STATES,
        "counts": counts, "overdue": overdue, "avg_progress": avg, "today": today, "opts": _opts(db),
        "department_id": department_id, "state": state, "owner_id": owner_id,
        "dept_progress": dept_progress, "scorecards": _scorecard_summary(db, period), "period": period,
        "period_label": kpi_svc.period_label(period),
        "launched": counts.get("full_launch", 0), "total": len(items)})


@router.post("/items/create", include_in_schema=False)
async def create_item(request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("transformation.add"))):
    f = await request.form()
    name = (f.get("system_name") or "").strip()
    if not name:
        return redirect("/transformation", "A system name is required.", "error")
    item = TransformationItem(system_name=name, description=f.get("description") or None,
                              department_id=parse_int(f.get("department_id")),
                              owner_id=parse_int(f.get("owner_id")) or user.id,
                              state=(f.get("state") if f.get("state") in STATE_KEYS else "planned"),
                              progress_pct=max(0, min(100, parse_int(f.get("progress_pct"), 0) or 0)),
                              target_date=parse_date(f.get("target_date")), notes=f.get("notes") or None)
    db.add(item)
    db.flush()
    log_action(db, user, "create", "transformation", entity=item,
               description=f"Transformation item created: {item.system_name} ({item.state})", request=request)
    db.commit()
    return redirect("/transformation", "System added to the OS master tracker.")


@router.post("/items/{item_id}/edit", include_in_schema=False)
async def edit_item(item_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("transformation.update"))):
    item = db.query(TransformationItem).filter(TransformationItem.id == item_id).first()
    if not item:
        return redirect("/transformation", "Item not found.", "error")
    f = await request.form()
    before = {"state": item.state, "progress_pct": item.progress_pct, "owner_id": item.owner_id,
              "target_date": item.target_date}
    item.system_name = (f.get("system_name") or item.system_name).strip()
    item.description = f.get("description") or None
    item.department_id = parse_int(f.get("department_id"))
    item.owner_id = parse_int(f.get("owner_id"), item.owner_id)
    if f.get("state") in STATE_KEYS:
        item.state = f.get("state")
    item.progress_pct = max(0, min(100, parse_int(f.get("progress_pct"), item.progress_pct) or 0))
    item.target_date = parse_date(f.get("target_date"))
    item.notes = f.get("notes") or None
    log_action(db, user, "update", "transformation", entity=item,
               description=f"Transformation item updated: {item.system_name}", before=before,
               after={"state": item.state, "progress_pct": item.progress_pct, "owner_id": item.owner_id,
                      "target_date": item.target_date}, request=request)
    db.commit()
    return redirect("/transformation", "System updated.")


@router.post("/items/{item_id}/state", include_in_schema=False)
async def move_item(item_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("transformation.update"))):
    item = db.query(TransformationItem).filter(TransformationItem.id == item_id).first()
    if not item:
        return redirect("/transformation", "Item not found.", "error")
    f = await request.form()
    new_state = f.get("state") or ""
    if new_state not in STATE_KEYS:
        return redirect("/transformation", "Unknown state.", "error")
    before = {"state": item.state, "progress_pct": item.progress_pct}
    item.state = new_state
    if new_state == "full_launch":
        item.progress_pct = 100
    elif item.progress_pct == 0 and new_state != "planned":
        item.progress_pct = {"alpha": 25, "beta": 60}.get(new_state, item.progress_pct)
    log_action(db, user, "update", "transformation", entity=item,
               description=f"{item.system_name}: {before['state']} -> {new_state}", before=before,
               after={"state": item.state, "progress_pct": item.progress_pct},
               rationale=f.get("rationale") or f.get("reason"), request=request)
    if item.owner_id and item.owner_id != user.id:
        notify(db, item.owner_id, f"{item.system_name} moved to {new_state.replace('_', ' ')}",
               f"{user.full_name} changed the state of your system in the OS master tracker.",
               event_type="transformation_state", link="/transformation")
    db.commit()
    return redirect("/transformation", f"{item.system_name} moved to {new_state.replace('_', ' ')}.")


@router.post("/items/{item_id}/delete", include_in_schema=False)
async def delete_item(item_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("transformation.delete"))):
    item = db.query(TransformationItem).filter(TransformationItem.id == item_id).first()
    if not item:
        return redirect("/transformation", "Item not found.", "error")
    f = await request.form()
    log_action(db, user, "delete", "transformation", entity=item,
               description=f"Transformation item deleted: {item.system_name}",
               rationale=f.get("rationale") or f.get("reason"), request=request)
    db.delete(item)
    db.commit()
    return redirect("/transformation", "System removed from the tracker.")


# ================================================================================================ role architecture
@router.get("/roles", include_in_schema=False)
def role_architecture(request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("transformation.view"))):
    kpis = db.query(KPI).filter(KPI.is_active.is_(True)).order_by(KPI.name).all()
    slugs = sorted({k.role_slug for k in kpis if k.role_slug})
    by_slug: dict[str, list] = {s: [k for k in kpis if k.role_slug == s] for s in slugs}
    user_counts = dict(db.query(User.role_id, func.count(User.id)).filter(User.is_active.is_(True))
                       .group_by(User.role_id).all())
    rows = []
    for role in db.query(Role).order_by(Role.name):
        slug = role.slug
        owned = {slug} if slug in by_slug else set()
        for family, families in kpi_svc.DEPT_ROLE_SLUGS.items():  # HODs inherit their department's slug family
            if slug in (f"hod_{family}", family):
                owned |= {s for s in families if s in by_slug}
        if slug in ("super_admin", "manager"):
            owned |= {s for s in ("ceo", "manager") if s in by_slug}
        mapped = [k for s in sorted(owned) for k in by_slug.get(s, [])]
        rows.append({"role": role, "people": user_counts.get(role.id, 0), "kpis": mapped, "owned": sorted(owned),
                     "slug_counts": {s: (len(by_slug.get(s, [])) if s in owned else 0) for s in slugs},
                     "covered": bool(mapped), "permissions": len(role.permissions or [])})
    unmapped = [s for s in slugs if not any(r["role"].slug == s for r in rows)]
    return render(request, "transformation/roles.html", {
        "user": user, "tab": "roles", "tabs": TABS, "rows": rows, "slugs": slugs, "by_slug": by_slug,
        "unmapped": unmapped, "kpi_total": len(kpis),
        "covered": len([r for r in rows if r["covered"]]), "roles_total": len(rows)})


# ================================================================================================ transitions
@router.get("/transitions", include_in_schema=False)
def transitions(request: Request, status: str = "", db: Session = Depends(get_db),
                user: User = Depends(require("transformation.view"))):
    q = db.query(TransitionRecord)
    if status:
        q = q.filter(TransitionRecord.status == status)
    items = q.order_by(TransitionRecord.start_date.is_(None), TransitionRecord.start_date.desc(),
                       TransitionRecord.id.desc()).all()
    emp = {e.id: e for e in db.query(Employee)}
    rows = []
    for t in items:
        checklist = t.checklist if isinstance(t.checklist, list) else []
        done = len([c for c in checklist if isinstance(c, dict) and c.get("done")])
        rows.append({"t": t, "from": emp.get(t.from_employee_id), "to": emp.get(t.to_employee_id),
                     "checklist": checklist, "done": done, "total": len(checklist),
                     "pct": int(round(100 * done / len(checklist))) if checklist else 0})
    return render(request, "transformation/transitions.html", {
        "user": user, "tab": "transitions", "tabs": TABS, "rows": rows, "status": status, "opts": _opts(db),
        "statuses": TRANSITION_STATUSES, "today": date.today(),
        "counts": {s: len([r for r in rows if r["t"].status == s]) for s in TRANSITION_STATUSES}})


def _parse_checklist(raw: str, existing: list | None = None) -> list:
    raw = (raw or "").strip()
    if not raw:
        return existing or []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            out = []
            for row in data:
                if isinstance(row, dict):
                    out.append({"item": str(row.get("item") or row.get("task") or "")[:200],
                                "done": bool(row.get("done"))})
                else:
                    out.append({"item": str(row)[:200], "done": False})
            return out
    except (ValueError, TypeError):
        pass
    return [{"item": line.strip("- ").strip(), "done": False} for line in raw.splitlines() if line.strip()]


@router.post("/transitions/create", include_in_schema=False)
async def create_transition(request: Request, db: Session = Depends(get_db),
                            user: User = Depends(require("transformation.add"))):
    f = await request.form()
    title = (f.get("role_title") or "").strip()
    if not title:
        return redirect("/transformation/transitions", "A role title is required.", "error")
    t = TransitionRecord(role_title=title, from_employee_id=parse_int(f.get("from_employee_id")),
                         to_employee_id=parse_int(f.get("to_employee_id")),
                         handover_notes=f.get("handover_notes") or None,
                         checklist=_parse_checklist(f.get("checklist")),
                         status=(f.get("status") if f.get("status") in TRANSITION_STATUSES else "planned"),
                         start_date=parse_date(f.get("start_date"), date.today()))
    db.add(t)
    db.flush()
    log_action(db, user, "create", "transformation", entity=t,
               description=f"Transition record created for {t.role_title}", request=request)
    db.commit()
    return redirect("/transformation/transitions", "Transition record created.")


@router.post("/transitions/{transition_id}/edit", include_in_schema=False)
async def edit_transition(transition_id: int, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("transformation.update"))):
    t = db.query(TransitionRecord).filter(TransitionRecord.id == transition_id).first()
    if not t:
        return redirect("/transformation/transitions", "Transition not found.", "error")
    f = await request.form()
    before = {"status": t.status, "to_employee_id": t.to_employee_id, "checklist": t.checklist}
    t.role_title = (f.get("role_title") or t.role_title).strip()
    t.from_employee_id = parse_int(f.get("from_employee_id"))
    t.to_employee_id = parse_int(f.get("to_employee_id"))
    t.handover_notes = f.get("handover_notes") or None
    t.checklist = _parse_checklist(f.get("checklist"), t.checklist)
    t.start_date = parse_date(f.get("start_date"), t.start_date)
    if f.get("status") in TRANSITION_STATUSES:
        t.status = f.get("status")
    t.completed_at = date.today() if t.status == "completed" else None
    log_action(db, user, "update", "transformation", entity=t,
               description=f"Transition updated: {t.role_title} ({t.status})", before=before,
               after={"status": t.status, "to_employee_id": t.to_employee_id, "checklist": t.checklist},
               request=request)
    db.commit()
    return redirect("/transformation/transitions", "Transition updated.")


@router.post("/transitions/{transition_id}/check", include_in_schema=False)
async def toggle_check(transition_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("transformation.update"))):
    t = db.query(TransitionRecord).filter(TransitionRecord.id == transition_id).first()
    if not t:
        return redirect("/transformation/transitions", "Transition not found.", "error")
    f = await request.form()
    idx = parse_int(f.get("index"))
    checklist = list(t.checklist or [])
    if idx is None or idx < 0 or idx >= len(checklist):
        return redirect("/transformation/transitions", "Checklist item not found.", "error")
    row = dict(checklist[idx]) if isinstance(checklist[idx], dict) else {"item": str(checklist[idx]), "done": False}
    row["done"] = not row.get("done")
    checklist[idx] = row
    t.checklist = checklist
    if all(isinstance(c, dict) and c.get("done") for c in checklist) and checklist:
        t.status = "completed"
        t.completed_at = date.today()
    log_action(db, user, "update", "transformation", entity=t,
               description=f"Handover checklist item '{row.get('item')}' marked {'done' if row['done'] else 'open'}",
               request=request)
    db.commit()
    return redirect("/transformation/transitions", "Checklist updated.")


# ================================================================================================ development plans
@router.get("/development", include_in_schema=False)
def development(request: Request, department_id: int | None = None, db: Session = Depends(get_db),
                user: User = Depends(require("transformation.view"))):
    q = db.query(DevelopmentPlan).filter(DevelopmentPlan.employee_id.is_(None))
    if department_id:
        q = q.filter(DevelopmentPlan.department_id == department_id)
    plans = q.order_by(DevelopmentPlan.start_date.desc(), DevelopmentPlan.id.desc()).all()
    rows = []
    for p in plans:
        goals = p.goals if isinstance(p.goals, list) else []
        done = len([g for g in goals if isinstance(g, dict) and g.get("status") in ("done", "achieved")])
        rows.append({"p": p, "goals": sorted([g for g in goals if isinstance(g, dict)],
                                             key=lambda g: g.get("month") or 0),
                     "done": done, "total": len(goals)})
    # AI fluency across employees (individual plans)
    fluency = (db.query(DevelopmentPlan.ai_fluency_level, func.count(DevelopmentPlan.id))
               .filter(DevelopmentPlan.employee_id.isnot(None)).group_by(DevelopmentPlan.ai_fluency_level).all())
    fluency_map = {int(lvl or 1): int(n) for lvl, n in fluency}
    fluency_labels = [f"Level {i}" for i in range(1, 6)]
    fluency_data = [fluency_map.get(i, 0) for i in range(1, 6)]
    dept_fluency = []
    for d in db.query(Department).filter(Department.is_active.is_(True)).order_by(Department.name):
        avg = (db.query(func.avg(DevelopmentPlan.ai_fluency_level))
               .filter(DevelopmentPlan.department_id == d.id).scalar())
        dept_fluency.append({"name": d.name, "avg": round(float(avg), 2) if avg is not None else 0})
    individual = (db.query(DevelopmentPlan).filter(DevelopmentPlan.employee_id.isnot(None))
                  .order_by(DevelopmentPlan.ai_fluency_level.desc(), DevelopmentPlan.id.desc()).limit(20).all())
    return render(request, "transformation/development.html", {
        "user": user, "tab": "development", "tabs": TABS, "rows": rows, "opts": _opts(db),
        "department_id": department_id, "fluency_labels": fluency_labels, "fluency_data": fluency_data,
        "dept_fluency": dept_fluency, "individual": individual, "today": date.today(),
        "avg_fluency": round(sum(i * fluency_map.get(i, 0) for i in range(1, 6)) / sum(fluency_data), 2)
        if sum(fluency_data) else 0,
        "plan_count": len(rows), "individual_count": sum(fluency_data)})


@router.post("/development/create", include_in_schema=False)
async def create_plan(request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("transformation.add"))):
    f = await request.form()
    title = (f.get("title") or "").strip()
    if not title:
        return redirect("/transformation/development", "A plan title is required.", "error")
    goals = []
    for m in range(1, 7):
        text = (f.get(f"goal_{m}") or "").strip()
        if text:
            goals.append({"month": m, "goal": text, "status": "planned"})
    start = parse_date(f.get("start_date"), date.today())
    p = DevelopmentPlan(employee_id=None, department_id=parse_int(f.get("department_id")), title=title,
                        goals=goals, ai_fluency_level=max(1, min(5, parse_int(f.get("ai_fluency_level"), 1) or 1)),
                        start_date=start, end_date=start + timedelta(days=182), progress_pct=0,
                        status="active", owner_id=parse_int(f.get("owner_id")) or user.id)
    db.add(p)
    db.flush()
    log_action(db, user, "create", "transformation", entity=p,
               description=f"Department development plan created: {p.title} ({len(goals)} monthly goals)",
               request=request)
    db.commit()
    return redirect("/transformation/development", "Six-month development plan created.")


@router.post("/development/{plan_id}/edit", include_in_schema=False)
async def edit_plan(plan_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("transformation.update"))):
    p = db.query(DevelopmentPlan).filter(DevelopmentPlan.id == plan_id).first()
    if not p:
        return redirect("/transformation/development", "Plan not found.", "error")
    f = await request.form()
    before = {"progress_pct": p.progress_pct, "status": p.status, "ai_fluency_level": p.ai_fluency_level}
    p.title = (f.get("title") or p.title).strip()
    p.department_id = parse_int(f.get("department_id"), p.department_id)
    p.ai_fluency_level = max(1, min(5, parse_int(f.get("ai_fluency_level"), p.ai_fluency_level) or 1))
    p.progress_pct = max(0, min(100, parse_int(f.get("progress_pct"), p.progress_pct) or 0))
    p.status = f.get("status") or p.status
    goals = []
    for m in range(1, 7):
        text = (f.get(f"goal_{m}") or "").strip()
        if text:
            goals.append({"month": m, "goal": text,
                          "status": f.get(f"goal_status_{m}") or "planned"})
    if goals:
        p.goals = goals
    log_action(db, user, "update", "transformation", entity=p, description=f"Development plan updated: {p.title}",
               before=before, after={"progress_pct": p.progress_pct, "status": p.status,
                                     "ai_fluency_level": p.ai_fluency_level}, request=request)
    db.commit()
    return redirect("/transformation/development", "Development plan updated.")


# ================================================================================================ operating rhythm
@router.get("/governance", include_in_schema=False)
def governance(request: Request, db: Session = Depends(get_db),
               user: User = Depends(require("transformation.view"))):
    morning = (_setting(db, "report_deadline_morning", {"time": "10:00"}) or {}).get("time", "10:00")
    afternoon = (_setting(db, "report_deadline_afternoon", {"time": "17:00"}) or {}).get("time", "17:00")
    agenda = _setting(db, "trajectory_agenda", None) or DEFAULT_AGENDA
    period = kpi_svc.period_key()
    today = date.today()
    upcoming = (db.query(TrajectoryMeeting).filter(TrajectoryMeeting.meeting_date >= today)
                .order_by(TrajectoryMeeting.meeting_date).first())
    from app.models.ops import DailyReport
    week_start = today - timedelta(days=today.weekday())
    submitted = db.query(DailyReport).filter(DailyReport.report_date >= week_start).count()
    late = db.query(DailyReport).filter(DailyReport.report_date >= week_start, DailyReport.is_late.is_(True)).count()
    reporting_users = len([u for u in db.query(User).filter(User.is_active.is_(True)) if u.portal in ("admin", "teacher")])
    expected = reporting_users * 2 * ((today - week_start).days + 1)
    return render(request, "transformation/governance.html", {
        "user": user, "tab": "governance", "tabs": TABS, "morning": morning, "afternoon": afternoon,
        "agenda": agenda, "default_agenda": DEFAULT_AGENDA, "meeting": upcoming, "today": today,
        "scorecards": _scorecard_summary(db, period), "period": period,
        "period_label": kpi_svc.period_label(period),
        "submitted": submitted, "late": late, "expected": expected,
        "compliance": round(100 * submitted / expected, 1) if expected else 0,
        "reporting_users": reporting_users, "week_start": week_start,
        "can_configure": rbac.has_permission(user, "transformation.update")})


@router.post("/governance/rhythm", include_in_schema=False)
async def save_rhythm(request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("transformation.update"))):
    f = await request.form()
    before = {"morning": _setting(db, "report_deadline_morning"), "afternoon": _setting(db, "report_deadline_afternoon")}
    morning = (f.get("morning") or "10:00").strip()[:5]
    afternoon = (f.get("afternoon") or "17:00").strip()[:5]
    _set_setting(db, "report_deadline_morning", {"time": morning}, description="Morning structured report deadline")
    _set_setting(db, "report_deadline_afternoon", {"time": afternoon}, description="Afternoon structured report deadline")
    agenda = [line.strip("- ").strip() for line in (f.get("agenda") or "").splitlines() if line.strip()]
    if agenda:
        _set_setting(db, "trajectory_agenda", agenda, description="Weekly trajectory meeting agenda template")
    log_action(db, user, "configure", "transformation",
               description=f"Operating rhythm updated (morning {morning}, afternoon {afternoon}, "
                           f"{len(agenda)} agenda items)", before=before,
               after={"morning": morning, "afternoon": afternoon}, request=request)
    db.commit()
    return redirect("/transformation/governance", "Operating rhythm saved. Reporting deadlines apply from today.")


# ================================================================================================ trajectory meetings
@router.get("/meetings", include_in_schema=False)
def meetings(request: Request, db: Session = Depends(get_db),
             user: User = Depends(require("transformation.view"))):
    today = date.today()
    items = db.query(TrajectoryMeeting).order_by(TrajectoryMeeting.meeting_date.desc()).limit(40).all()
    names = {u.id: u.full_name for u in db.query(User)}
    rows = []
    for m in items:
        rows.append({"m": m, "chair": names.get(m.chaired_by_id),
                     "decisions": db.query(Decision).filter(Decision.meeting_id == m.id).count(),
                     "actions": db.query(Task).filter(Task.entity_type == "TrajectoryMeeting",
                                                      Task.entity_id == m.id).count(),
                     "attendees": len(m.attendees or [])})
    upcoming = [r for r in rows if r["m"].meeting_date >= today]
    return render(request, "transformation/meetings.html", {
        "user": user, "tab": "meetings", "tabs": TABS, "rows": rows, "today": today, "opts": _opts(db),
        "agenda_default": "\n".join(_setting(db, "trajectory_agenda", None) or DEFAULT_AGENDA),
        "upcoming": upcoming, "held": len([r for r in rows if r["m"].meeting_date < today]),
        "total_decisions": sum(r["decisions"] for r in rows),
        "total_actions": sum(r["actions"] for r in rows)})


@router.post("/meetings/create", include_in_schema=False)
async def create_meeting(request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("transformation.add"))):
    f = await request.form()
    attendees = [a.strip() for a in (f.get("attendees") or "").replace("\n", ",").split(",") if a.strip()]
    m = TrajectoryMeeting(meeting_date=parse_date(f.get("meeting_date"), date.today()),
                          title=(f.get("title") or "Weekly Trajectory Meeting").strip(),
                          agenda=f.get("agenda") or "\n".join(DEFAULT_AGENDA), attendees=attendees,
                          notes=f.get("notes") or None, chaired_by_id=parse_int(f.get("chaired_by_id")) or user.id,
                          status=(f.get("status") if f.get("status") in MEETING_STATUSES else "scheduled"))
    db.add(m)
    db.flush()
    log_action(db, user, "create", "transformation", entity=m,
               description=f"Trajectory meeting scheduled for {m.meeting_date}", request=request)
    db.commit()
    return redirect(f"/transformation/meetings/{m.id}", "Trajectory meeting created.")


@router.get("/meetings/{meeting_id}", include_in_schema=False)
def meeting_detail(meeting_id: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("transformation.view"))):
    m = db.query(TrajectoryMeeting).filter(TrajectoryMeeting.id == meeting_id).first()
    if not m:
        return redirect("/transformation/meetings", "Meeting not found.", "error")
    decisions = db.query(Decision).filter(Decision.meeting_id == m.id).order_by(Decision.id.desc()).all()
    actions = (db.query(Task).filter(Task.entity_type == "TrajectoryMeeting", Task.entity_id == m.id)
               .order_by(Task.due_date.is_(None), Task.due_date).all())
    period = kpi_svc.period_key()
    return render(request, "transformation/meeting_detail.html", {
        "user": user, "tab": "meetings", "tabs": TABS, "m": m, "decisions": decisions, "actions": actions,
        "opts": _opts(db), "today": date.today(),
        "agenda_lines": [line for line in (m.agenda or "").splitlines() if line.strip()],
        "scorecards": _scorecard_summary(db, period), "period_label": kpi_svc.period_label(period),
        "categories": ["operational", "academic", "financial", "hr", "strategic"],
        "done_actions": len([a for a in actions if a.status == "done"])})


@router.post("/meetings/{meeting_id}/edit", include_in_schema=False)
async def edit_meeting(meeting_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("transformation.update"))):
    m = db.query(TrajectoryMeeting).filter(TrajectoryMeeting.id == meeting_id).first()
    if not m:
        return redirect("/transformation/meetings", "Meeting not found.", "error")
    f = await request.form()
    before = {"status": m.status, "notes": (m.notes or "")[:200]}
    m.title = (f.get("title") or m.title).strip()
    m.meeting_date = parse_date(f.get("meeting_date"), m.meeting_date)
    m.agenda = f.get("agenda") or m.agenda
    m.notes = f.get("notes") or m.notes
    if f.get("attendees") is not None:
        m.attendees = [a.strip() for a in (f.get("attendees") or "").replace("\n", ",").split(",") if a.strip()]
    m.chaired_by_id = parse_int(f.get("chaired_by_id"), m.chaired_by_id)
    if f.get("status") in MEETING_STATUSES:
        m.status = f.get("status")
    log_action(db, user, "update", "transformation", entity=m,
               description=f"Trajectory meeting updated ({m.meeting_date})", before=before,
               after={"status": m.status, "notes": (m.notes or "")[:200]}, request=request)
    db.commit()
    return redirect(f"/transformation/meetings/{m.id}", "Meeting minutes saved.")


@router.post("/meetings/{meeting_id}/decision", include_in_schema=False)
async def meeting_decision(meeting_id: int, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require("decisions.add"))):
    m = db.query(TrajectoryMeeting).filter(TrajectoryMeeting.id == meeting_id).first()
    if not m:
        return redirect("/transformation/meetings", "Meeting not found.", "error")
    f = await request.form()
    title = (f.get("title") or "").strip()
    rationale = (f.get("rationale") or "").strip()
    owner_id = parse_int(f.get("owner_id"))
    if not title or not rationale or not owner_id:
        return redirect(f"/transformation/meetings/{m.id}",
                        "A decision needs a title, a named owner and a written rationale.", "error")
    d = Decision(title=title, category=f.get("category") or "operational", rationale=rationale, owner_id=owner_id,
                 decided_by_id=user.id, meeting_id=m.id, department_id=parse_int(f.get("department_id")),
                 status="decided", due_date=parse_date(f.get("due_date")))
    db.add(d)
    db.flush()
    notify(db, owner_id, "You own a new decision", f"{d.title} — recorded in the trajectory meeting of {m.meeting_date}.",
           event_type="decision_owner", link=f"/decisions/{d.id}")
    log_action(db, user, "create", "decisions", entity=d, description=f"Decision recorded in meeting: {d.title}",
               rationale=rationale, request=request)
    db.commit()
    return redirect(f"/transformation/meetings/{m.id}", "Decision recorded with an owner and rationale.")


@router.post("/meetings/{meeting_id}/action", include_in_schema=False)
async def meeting_action(meeting_id: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("tasks.add"))):
    m = db.query(TrajectoryMeeting).filter(TrajectoryMeeting.id == meeting_id).first()
    if not m:
        return redirect("/transformation/meetings", "Meeting not found.", "error")
    f = await request.form()
    title = (f.get("title") or "").strip()
    if not title:
        return redirect(f"/transformation/meetings/{m.id}", "An action needs a title.", "error")
    assignee_id = parse_int(f.get("assignee_id")) or user.id
    assignee = db.query(User).filter(User.id == assignee_id).first()
    t = Task(title=title, description=f"Action from the trajectory meeting of {m.meeting_date}.",
             assignee_id=assignee_id, creator_id=user.id,
             department_id=assignee.department_id if assignee else None,
             priority=f.get("priority") or "high", status="todo",
             due_date=parse_date(f.get("due_date"), m.meeting_date + timedelta(days=7)),
             entity_type="TrajectoryMeeting", entity_id=m.id)
    db.add(t)
    db.flush()
    if assignee_id != user.id:
        notify(db, assignee_id, "New action from the trajectory meeting", title,
               event_type="task_assigned", link=f"/tasks/{t.id}")
    log_action(db, user, "create", "transformation", entity=t,
               description=f"Meeting action created: {title}", request=request)
    db.commit()
    return redirect(f"/transformation/meetings/{m.id}", "Action created and assigned as a task.")
