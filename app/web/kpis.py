"""Module 26 / Section 17 - KPI framework: catalogue, values, timelines, department and role scorecards."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import parse_float, parse_int, redirect
from app.database import get_db
from app.models.core import Department, User
from app.models.ops import KPI, DepartmentScorecard, KPIValue
from app.models.people import Teacher
from app.services import kpi as svc

router = APIRouter(prefix="/kpis", dependencies=[Depends(csrf_protect)])

UNITS = ["%", "count", "currency", "score", "days", "minutes", "ratio"]
FREQUENCIES = ["daily", "weekly", "monthly", "quarterly"]
DIRECTIONS = [("higher", "Higher is better"), ("lower", "Lower is better")]
ROLE_SLUGS = ["teacher", "supervisor", "manager", "hr", "academic", "qa", "finance", "marketing", "technology", "ceo"]


def _period(period: str) -> str:
    p = (period or "").strip()
    if len(p) == 7 and p[4] == "-":
        return p
    return svc.period_key()


def _opts(db: Session) -> dict:
    return {"departments": [(d.id, d.name) for d in db.query(Department).order_by(Department.name)],
            "users": [(u.id, u.full_name) for u in db.query(User).filter(User.is_active.is_(True)).order_by(User.full_name)],
            "roles": ROLE_SLUGS, "units": UNITS, "frequencies": FREQUENCIES, "directions": DIRECTIONS,
            "formulas": sorted(svc.FORMULAS.keys())}


# ================================================================================================ catalogue
@router.get("", include_in_schema=False)
def catalogue(request: Request, period: str = "", role: str = "", department_id: int | None = None,
              frequency: str = "", q: str = "", inactive: int = 0, db: Session = Depends(get_db),
              user: User = Depends(require("kpis.view"))):
    p = _period(period)
    query = db.query(KPI)
    if not inactive:
        query = query.filter(KPI.is_active.is_(True))
    if role:
        query = query.filter(KPI.role_slug == role)
    if department_id:
        query = query.filter(KPI.department_id == department_id)
    if frequency:
        query = query.filter(KPI.frequency == frequency)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(KPI.name.ilike(like), KPI.code.ilike(like)))
    kpis = query.order_by(KPI.role_slug, KPI.name).all()
    rows = [svc.kpi_row(db, k, p, live=True) for k in kpis]
    counts = {c: sum(1 for r in rows if r["rag"] == c) for c in ("green", "amber", "red", "grey", "blue")}
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["kpi"].role_slug or "general", []).append(r)
    return render(request, "kpis/catalogue.html", {
        "user": user, "rows": rows, "groups": groups, "counts": counts, "period": p,
        "period_label": svc.period_label(p), "periods": svc.last_n_periods(12), "role": role,
        "department_id": department_id, "frequency": frequency, "q": q, "inactive": inactive,
        "opts": _opts(db), "format_value": svc.format_value,
        "qs": f"period={p}&role={role}&frequency={frequency}&q={q}&department_id={department_id or ''}"})


@router.post("/create", include_in_schema=False)
async def create_kpi(request: Request, db: Session = Depends(get_db), user: User = Depends(require("kpis.add"))):
    f = await request.form()
    code = (f.get("code") or "").strip().lower().replace(" ", "_")
    name = (f.get("name") or "").strip()
    if not code or not name:
        return redirect("/kpis", "Code and name are required.", "error")
    if db.query(KPI).filter(KPI.code == code).first():
        return redirect("/kpis", f"A KPI with code '{code}' already exists.", "error")
    k = KPI(code=code, name=name, description=f.get("description") or None,
            department_id=parse_int(f.get("department_id")), role_slug=f.get("role_slug") or None,
            unit=f.get("unit") or "%", target=parse_float(f.get("target")) or None,
            direction=f.get("direction") or "higher", frequency=f.get("frequency") or "monthly",
            formula_key=(f.get("formula_key") or None) or None, is_custom=True, is_active=True,
            owner_id=parse_int(f.get("owner_id")) or user.id)
    db.add(k)
    db.flush()
    log_action(db, user, "create", "kpis", entity=k, description=f"Custom KPI created: {k.code}", request=request)
    db.commit()
    return redirect(f"/kpis/{k.id}", "KPI created.")


@router.post("/snapshot", include_in_schema=False)
async def snapshot(request: Request, db: Session = Depends(get_db), user: User = Depends(require("kpis.update"))):
    f = await request.form()
    p = _period(f.get("period"))
    out = svc.snapshot_kpis(db, p)
    log_action(db, user, "execute", "kpis",
               description=f"KPI snapshot for {p}: {out['stored']} stored, {out['skipped']} without data, "
                           f"{out['teacher_rows']} teacher rows", request=request)
    db.commit()
    return redirect(f"/kpis?period={p}",
                    f"Snapshot stored {out['stored']} KPI values ({out['teacher_rows']} per-teacher) for {svc.period_label(p)}.")


# ================================================================================================ scorecards
@router.get("/scorecards", include_in_schema=False)
def scorecards(request: Request, period: str = "", department_id: int | None = None,
               db: Session = Depends(get_db), user: User = Depends(require("kpis.view"))):
    p = _period(period)
    depts = db.query(Department).filter(Department.is_active.is_(True)).order_by(Department.name).all()
    cards = []
    for d in depts:
        if department_id and d.id != department_id:
            continue
        m = svc.department_metrics(db, d, p, live=True)
        m["submission"] = db.query(DepartmentScorecard).filter(DepartmentScorecard.department_id == d.id,
                                                               DepartmentScorecard.period == p).first()
        m["is_hod"] = (d.hod_user_id == user.id) or rbac.is_ceo(user)
        cards.append(m)
    return render(request, "kpis/scorecards.html", {
        "user": user, "cards": cards, "period": p, "period_label": svc.period_label(p),
        "periods": svc.last_n_periods(12), "departments": [(d.id, d.name) for d in depts],
        "department_id": department_id, "format_value": svc.format_value})


@router.post("/scorecards/submit", include_in_schema=False)
async def submit_scorecard(request: Request, db: Session = Depends(get_db), user: User = Depends(require("kpis.update"))):
    f = await request.form()
    p = _period(f.get("period"))
    dept_id = parse_int(f.get("department_id"))
    dept = db.query(Department).filter(Department.id == dept_id).first()
    if not dept:
        return redirect("/kpis/scorecards", "Department not found.", "error")
    if dept.hod_user_id != user.id and not rbac.is_ceo(user) and not rbac.has_permission(user, "kpis.approve"):
        return redirect("/kpis/scorecards", "Only the department HOD (or the CEO) can submit this scorecard.", "error")
    m = svc.department_metrics(db, dept, p, live=True)
    row = db.query(DepartmentScorecard).filter(DepartmentScorecard.department_id == dept.id,
                                               DepartmentScorecard.period == p).first()
    if not row:
        row = DepartmentScorecard(department_id=dept.id, period=p)
        db.add(row)
    before = {"status": row.status, "score": row.score}
    row.score = m["score"] or 0.0
    row.metrics = {r["kpi"].code: r["value"] for r in m["rows"] if r["value"] is not None}
    row.highlights = f.get("highlights") or None
    row.risks = f.get("risks") or None
    row.submitted_by_id = user.id
    row.submitted_at = datetime.utcnow()
    row.status = "submitted"
    log_action(db, user, "approve", "kpis", entity=row,
               description=f"{dept.name} scorecard submitted for {p} (score {row.score})", before=before,
               after={"status": row.status, "score": row.score}, request=request)
    ceo = db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()
    if ceo and ceo.id != user.id:
        notify(db, ceo, f"{dept.name} scorecard submitted",
               f"Score {row.score} for {svc.period_label(p)}.", event_type="scorecard_submitted",
               link=f"/kpis/scorecards?period={p}&department_id={dept.id}")
    db.commit()
    return redirect(f"/kpis/scorecards?period={p}&department_id={dept.id}", "Department scorecard submitted.")


@router.get("/roles", include_in_schema=False)
def role_scorecards(request: Request, period: str = "", role: str = "teacher", db: Session = Depends(get_db),
                    user: User = Depends(require("kpis.view"))):
    p = _period(period)
    role = role if role in ("teacher", "supervisor") else "teacher"
    kpis = db.query(KPI).filter(KPI.is_active.is_(True), KPI.role_slug == role).order_by(KPI.name).all()
    rows = []
    if role == "teacher":
        for t in db.query(Teacher).filter(Teacher.status == "active").order_by(Teacher.teacher_code):
            cells = [svc.kpi_row(db, k, p, t, live=True) for k in kpis]
            scores = [svc.rag_score(c["rag"]) for c in cells]
            scores = [s for s in scores if s is not None]
            score = round(sum(scores) / len(scores), 1) if scores else None
            rows.append({"entity": t, "name": t.full_name, "sub": t.teacher_code + (" · Grade " + t.grade if t.grade else ""),
                         "cells": cells, "score": score, "band": svc.health_band(score),
                         "url": f"/teachers/{t.id}"})
    else:
        sup_ids = [r[0] for r in db.query(Teacher.supervisor_id).filter(Teacher.supervisor_id.isnot(None)).distinct()]
        sups = db.query(User).filter(User.id.in_(sup_ids or [-1])).order_by(User.full_name).all()
        if not sups:
            sups = [u for u in db.query(User).filter(User.is_active.is_(True)) if u.role_slug == "supervisor"]
        for u in sups:
            team = db.query(Teacher).filter(Teacher.supervisor_id == u.id).count()
            cells = [svc.kpi_row(db, k, p, live=True) for k in kpis]
            scores = [svc.rag_score(c["rag"]) for c in cells]
            scores = [s for s in scores if s is not None]
            score = round(sum(scores) / len(scores), 1) if scores else None
            rows.append({"entity": u, "name": u.full_name, "sub": f"{team} teacher(s) supervised", "cells": cells,
                         "score": score, "band": svc.health_band(score), "url": "/supervisor"})
    return render(request, "kpis/roles.html", {
        "user": user, "role": role, "kpis": kpis, "rows": rows, "period": p, "period_label": svc.period_label(p),
        "periods": svc.last_n_periods(12), "format_value": svc.format_value})


# ================================================================================================ detail
@router.get("/{kpi_id}", include_in_schema=False)
def kpi_detail(kpi_id: int, request: Request, period: str = "", db: Session = Depends(get_db),
               user: User = Depends(require("kpis.view"))):
    k = db.query(KPI).filter(KPI.id == kpi_id).first()
    if not k:
        return redirect("/kpis", "KPI not found.", "error")
    p = _period(period)
    periods = svc.last_n_periods(12)
    stored = {v.period: v for v in db.query(KPIValue).filter(KPIValue.kpi_id == k.id, KPIValue.entity_type.is_(None))}
    values, targets = [], []
    for per in periods:
        v = stored.get(per)
        live = svc.compute_kpi(db, k, per) if k.formula_key else None
        values.append(live if live is not None else (v.value if v else None))
        targets.append(k.target)
    row = svc.kpi_row(db, k, p, live=True)
    entity_rows = []
    if k.role_slug == "teacher":
        for t in db.query(Teacher).filter(Teacher.status == "active").order_by(Teacher.teacher_code).limit(40):
            entity_rows.append({"name": t.full_name, "code": t.teacher_code, "row": svc.kpi_row(db, k, p, t, live=True)})
    history = (db.query(KPIValue).filter(KPIValue.kpi_id == k.id).order_by(KPIValue.period.desc(), KPIValue.id.desc())
               .limit(60).all())
    return render(request, "kpis/detail.html", {
        "user": user, "k": k, "period": p, "period_label": svc.period_label(p), "periods": periods,
        "labels": [svc.period_label(x) for x in periods], "values": values, "targets": targets, "row": row,
        "entity_rows": entity_rows, "history": history, "opts": _opts(db), "format_value": svc.format_value,
        "period_options": [(x, svc.period_label(x)) for x in periods]})


@router.post("/{kpi_id}/edit", include_in_schema=False)
async def edit_kpi(kpi_id: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("kpis.update"))):
    k = db.query(KPI).filter(KPI.id == kpi_id).first()
    if not k:
        return redirect("/kpis", "KPI not found.", "error")
    f = await request.form()
    before = {"target": k.target, "direction": k.direction, "is_active": k.is_active}
    k.name = (f.get("name") or k.name).strip()
    k.description = f.get("description") or None
    k.department_id = parse_int(f.get("department_id"))
    k.role_slug = f.get("role_slug") or None
    k.unit = f.get("unit") or k.unit
    k.target = parse_float(f.get("target")) if f.get("target") not in (None, "") else None
    k.direction = f.get("direction") or k.direction
    k.frequency = f.get("frequency") or k.frequency
    k.formula_key = (f.get("formula_key") or None) or None
    k.owner_id = parse_int(f.get("owner_id"), k.owner_id)
    k.is_active = f.get("is_active") in ("1", "on", "true")
    log_action(db, user, "update", "kpis", entity=k, description=f"KPI updated: {k.code}", before=before,
               after={"target": k.target, "direction": k.direction, "is_active": k.is_active}, request=request)
    db.commit()
    return redirect(f"/kpis/{k.id}", "KPI updated.")


@router.post("/{kpi_id}/value", include_in_schema=False)
async def manual_value(kpi_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("kpis.update"))):
    k = db.query(KPI).filter(KPI.id == kpi_id).first()
    if not k:
        return redirect("/kpis", "KPI not found.", "error")
    f = await request.form()
    p = _period(f.get("period"))
    if f.get("value") in (None, ""):
        return redirect(f"/kpis/{k.id}?period={p}", "A value is required.", "error")
    value = parse_float(f.get("value"))
    entity_type = f.get("entity_type") or None
    entity_id = parse_int(f.get("entity_id"))
    svc.upsert_value(db, k, p, value, source="manual", entity_type=entity_type, entity_id=entity_id,
                     entered_by_id=user.id, target=parse_float(f.get("target")) if f.get("target") else None)
    log_action(db, user, "update", "kpis", entity=k, description=f"Manual value {value} recorded for {k.code} / {p}",
               rationale=f.get("rationale") or f.get("reason"), request=request)
    db.commit()
    return redirect(f"/kpis/{k.id}?period={p}", "Manual KPI value recorded.")


@router.post("/{kpi_id}/remind", include_in_schema=False)
async def remind_owner(kpi_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("kpis.update"))):
    k = db.query(KPI).filter(KPI.id == kpi_id).first()
    if not k:
        return redirect("/kpis", "KPI not found.", "error")
    f = await request.form()
    p = _period(f.get("period"))
    target_user = k.owner_id or user.id
    notify(db, target_user, f"KPI entry due: {k.name}",
           f"The {k.frequency} value for {k.code} ({svc.period_label(p)}) has not been recorded yet.",
           event_type="kpi_deadline", link=f"/kpis/{k.id}?period={p}")
    log_action(db, user, "execute", "kpis", entity=k, description=f"KPI deadline reminder sent for {k.code}", request=request)
    db.commit()
    return redirect(f"/kpis/{k.id}?period={p}", "Reminder sent to the KPI owner.")
