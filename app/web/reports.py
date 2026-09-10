"""Module 26 - Reports & exports.

A catalogue of parameterised reports built by ``app.services.reports``. Every report renders as an HTML
table with totals and exports to XLSX or CSV; every export writes a ReportRun and an audit event.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import paginate, parse_date, parse_int, redirect
from app.database import get_db
from app.models.academic import Course
from app.models.core import Department, User
from app.models.ops import ReportRun
from app.models.people import Teacher
from app.services import reports as svc

router = APIRouter(prefix="/reports", dependencies=[Depends(csrf_protect)])

PRESETS = [("7", "Last 7 days"), ("30", "Last 30 days"), ("90", "Last 90 days"), ("365", "Last 12 months")]


def _params(start: str, end: str, department_id, teacher_id, course_id) -> dict:
    d_end = parse_date(end, date.today())
    d_start = parse_date(start, d_end - timedelta(days=29))
    if d_start > d_end:
        d_start, d_end = d_end, d_start
    return {"start": d_start, "end": d_end, "department_id": department_id, "teacher_id": teacher_id,
            "course_id": course_id}


def _opts(db: Session) -> dict:
    return {
        "departments": [(d.id, d.name) for d in db.query(Department).order_by(Department.name)],
        "teachers": [(t.id, f"{t.teacher_code} · {t.full_name}") for t in db.query(Teacher).order_by(Teacher.teacher_code)],
        "courses": [(c.id, c.name) for c in db.query(Course).order_by(Course.name)],
        "presets": PRESETS,
    }


def _qs(p: dict) -> str:
    return (f"start={p['start']}&end={p['end']}&department_id={p['department_id'] or ''}"
            f"&teacher_id={p['teacher_id'] or ''}&course_id={p['course_id'] or ''}")


# ================================================================================================ catalogue
@router.get("", include_in_schema=False)
def catalogue(request: Request, q: str = "", group: str = "", db: Session = Depends(get_db),
              user: User = Depends(require("reports.view"))):
    items = svc.REPORTS
    if q:
        needle = q.lower()
        items = [r for r in items if needle in r["name"].lower() or needle in r["description"].lower()]
    if group:
        items = [r for r in items if r["group"] == group]
    groups: dict[str, list] = {}
    for r in items:
        groups.setdefault(r["group"], []).append(r)
    ordered = [(g, groups[g]) for g in svc.GROUPS if g in groups]
    runs = db.query(ReportRun).order_by(ReportRun.generated_at.desc()).limit(12).all()
    names = {u.id: u.full_name for u in db.query(User)}
    return render(request, "reports/catalogue.html", {
        "user": user, "groups": ordered, "q": q, "group": group, "all_groups": svc.GROUPS,
        "total": len(svc.REPORTS), "shown": len(items), "runs": runs, "names": names,
        "param_labels": svc.PARAM_LABELS,
        "exports_30": db.query(ReportRun).filter(
            ReportRun.generated_at >= date.today() - timedelta(days=30)).count()})


@router.get("/runs", include_in_schema=False)
def runs(request: Request, page: int = 1, db: Session = Depends(get_db),
         user: User = Depends(require("reports.view"))):
    pg = paginate(db.query(ReportRun).order_by(ReportRun.generated_at.desc(), ReportRun.id.desc()), page, 30)
    names = {u.id: u.full_name for u in db.query(User)}
    return render(request, "reports/runs.html", {"user": user, "page": pg, "names": names,
                                                 "reports_by_key": svc.REPORTS_BY_KEY})


# ================================================================================================ one report
@router.get("/{key}", include_in_schema=False)
def report(key: str, request: Request, start: str = "", end: str = "", department_id: int | None = None,
           teacher_id: int | None = None, course_id: int | None = None, page: int = 1,
           db: Session = Depends(get_db), user: User = Depends(require("reports.view"))):
    meta = svc.REPORTS_BY_KEY.get(key)
    if not meta:
        return redirect("/reports", "Unknown report.", "error")
    p = _params(start, end, department_id, teacher_id, course_id)
    out = svc.build(db, key, p)
    rows = out["rows"]
    per_page = 100
    total = len(rows)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    window = rows[(page - 1) * per_page: page * per_page]
    return render(request, "reports/report.html", {
        "user": user, "meta": meta, "headers": out["headers"], "rows": window, "totals": out["totals"],
        "note": out.get("note"), "p": p, "qs": _qs(p), "opts": _opts(db), "total": total, "page_no": page,
        "pages": pages, "per_page": per_page, "param_labels": svc.PARAM_LABELS,
        "start_index": (page - 1) * per_page + 1 if total else 0,
        "end_index": min(total, page * per_page),
        "related": [r for r in svc.REPORTS if r["group"] == meta["group"] and r["key"] != key]})


@router.get("/{key}/export", include_in_schema=False)
def export(key: str, request: Request, format: str = "xlsx", start: str = "", end: str = "",
           department_id: int | None = None, teacher_id: int | None = None, course_id: int | None = None,
           db: Session = Depends(get_db), user: User = Depends(require("reports.export"))):
    from fastapi.responses import FileResponse
    meta = svc.REPORTS_BY_KEY.get(key)
    if not meta:
        return redirect("/reports", "Unknown report.", "error")
    fmt = "csv" if format == "csv" else "xlsx"
    p = _params(start, end, department_id, teacher_id, course_id)
    out = svc.build(db, key, p)
    if not out["headers"]:
        return redirect(f"/reports/{key}", out.get("note") or "This report produced no columns to export.", "error")
    name = f"{meta['key']}-{p['start']}-to-{p['end']}"
    if fmt == "csv":
        path = svc.export_csv(out["headers"], out["rows"], name, totals=out["totals"])
        media = "text/csv"
    else:
        path = svc.export_xlsx(out["headers"], out["rows"], name, sheet=meta["name"][:31], totals=out["totals"])
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    svc.log_run(db, user, key, meta["name"], p, fmt, path, len(out["rows"]))
    log_action(db, user, "export", "reports",
               description=f"{meta['name']} exported as {fmt.upper()} ({len(out['rows'])} rows, "
                           f"{p['start']} to {p['end']})", request=request)
    db.commit()
    return FileResponse(path, filename=f"{name}.{fmt}", media_type=media)
