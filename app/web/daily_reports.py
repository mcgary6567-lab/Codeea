"""Structured daily reporting (report-by-chat-to-zero).

Two slots a day - morning and afternoon - with fields that change by department. Submissions are timed
against the configurable deadline in Asia/Karachi, so lateness is a fact rather than an opinion.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import PermissionDenied, csrf_protect, require
from app.core.templating import render
from app.core.utils import paginate, parse_date, parse_int, redirect
from app.database import get_db
from app.models.core import Department, User
from app.models.ops import DailyReport
from app.services import jobs_ops as ops_jobs

router = APIRouter(prefix="/daily-reports", dependencies=[Depends(csrf_protect)])

SLOTS = [("morning", "Morning plan"), ("afternoon", "Afternoon delivery")]

BASE_FIELDS = {
    "morning": [
        {"name": "priorities", "label": "Top three priorities today", "type": "textarea",
         "placeholder": "1. …\n2. …\n3. …"},
        {"name": "capacity", "label": "Capacity / availability", "type": "text",
         "placeholder": "e.g. full day, 2 meetings, on leave from 15:00"},
        {"name": "blockers", "label": "Blockers I need help with", "type": "textarea",
         "placeholder": "What will stop me, and who can unblock it"},
    ],
    "afternoon": [
        {"name": "completed", "label": "What I completed", "type": "textarea",
         "placeholder": "Outcomes, not activity"},
        {"name": "missed", "label": "What slipped and why", "type": "textarea",
         "placeholder": "Be specific — this is the record"},
        {"name": "tomorrow", "label": "First thing tomorrow", "type": "text"},
    ],
}

DEPT_FIELDS = {
    "academics": {
        "morning": [{"name": "classes_planned", "label": "Classes planned", "type": "number"},
                    {"name": "lesson_plans", "label": "Lesson plans prepared", "type": "number"}],
        "afternoon": [{"name": "classes_delivered", "label": "Classes delivered", "type": "number"},
                      {"name": "students_at_risk", "label": "Students needing follow-up", "type": "text"},
                      {"name": "curriculum_notes", "label": "Curriculum / progress notes", "type": "textarea"}],
    },
    "operations": {
        "morning": [{"name": "sessions_scheduled", "label": "Sessions scheduled today", "type": "number"},
                    {"name": "coverage_gaps", "label": "Known coverage gaps", "type": "text"}],
        "afternoon": [{"name": "missed_handled", "label": "Missed classes handled", "type": "number"},
                      {"name": "escalations", "label": "Escalations raised", "type": "text"},
                      {"name": "parent_calls", "label": "Parent conversations", "type": "number"}],
    },
    "finance": {
        "morning": [{"name": "invoices_due", "label": "Invoices due for follow-up", "type": "number"},
                    {"name": "collection_target", "label": "Collection target today", "type": "text"}],
        "afternoon": [{"name": "payments_collected", "label": "Payments collected", "type": "text"},
                      {"name": "overdue_followups", "label": "Overdue follow-ups made", "type": "number"},
                      {"name": "finance_risks", "label": "Cash / receivable risks", "type": "textarea"}],
    },
    "marketing": {
        "morning": [{"name": "leads_to_contact", "label": "Leads to contact", "type": "number"},
                    {"name": "campaigns_live", "label": "Campaigns live", "type": "text"}],
        "afternoon": [{"name": "leads_contacted", "label": "Leads contacted", "type": "number"},
                      {"name": "trials_booked", "label": "Trials booked", "type": "number"},
                      {"name": "conversion_notes", "label": "Objections heard today", "type": "textarea"}],
    },
    "people": {
        "morning": [{"name": "interviews", "label": "Interviews scheduled", "type": "number"},
                    {"name": "attendance_exceptions", "label": "Attendance exceptions to chase", "type": "text"}],
        "afternoon": [{"name": "onboarding", "label": "Onboarding steps completed", "type": "number"},
                      {"name": "people_risks", "label": "People risks / grievances", "type": "textarea"}],
    },
    "qa": {
        "morning": [{"name": "reviews_planned", "label": "Reviews planned", "type": "number"}],
        "afternoon": [{"name": "reviews_done", "label": "Reviews completed", "type": "number"},
                      {"name": "flags_raised", "label": "Flags raised", "type": "number"},
                      {"name": "corrective_actions", "label": "Corrective actions opened / closed", "type": "textarea"}],
    },
    "technology": {
        "morning": [{"name": "deployments", "label": "Planned deployments", "type": "text"},
                    {"name": "open_incidents", "label": "Open incidents", "type": "number"}],
        "afternoon": [{"name": "incidents_closed", "label": "Incidents closed", "type": "number"},
                      {"name": "integration_health", "label": "Integration health notes", "type": "textarea"}],
    },
}


def _dept_code(db: Session, user: User) -> str:
    if user.department_id:
        d = db.query(Department).filter(Department.id == user.department_id).first()
        if d:
            return d.code
    if user.role_slug == "teacher":
        return "academics"
    return "operations"


def _fields(code: str, slot: str) -> list[dict]:
    return BASE_FIELDS[slot] + DEPT_FIELDS.get(code, {}).get(slot, [])


def _deadlines(db: Session) -> dict:
    return {s: ops_jobs.deadline_time(db, s) for s, _ in SLOTS}


def _reporting_users(db: Session) -> list[User]:
    return [u for u in db.query(User).filter(User.is_active.is_(True)).order_by(User.full_name)
            if u.portal in ("admin", "teacher")]


def _can_see_team(user: User) -> bool:
    return rbac.is_management(user) or rbac.has_permission(user, "daily_reports.approve") \
        or rbac.has_permission(user, "daily_reports.export")


def _late_for(db: Session, report_date: date, slot: str, submitted_at: datetime) -> bool:
    deadline = datetime.combine(report_date, ops_jobs.deadline_time(db, slot))
    return submitted_at > deadline


# ================================================================================================ my report
@router.get("", include_in_schema=False)
def my_report(request: Request, day: str = "", db: Session = Depends(get_db),
              user: User = Depends(require("daily_reports.view"))):
    now = ops_jobs.org_now()
    report_date = parse_date(day, now.date()) or now.date()
    code = _dept_code(db, user)
    deadlines = _deadlines(db)
    rows = []
    for slot, label in SLOTS:
        rec = (db.query(DailyReport).filter(DailyReport.user_id == user.id, DailyReport.report_date == report_date,
                                            DailyReport.slot == slot).first())
        dl = deadlines[slot]
        rows.append({"slot": slot, "label": label, "record": rec, "fields": _fields(code, slot),
                     "deadline": dl.strftime("%H:%M"),
                     "past_deadline": report_date < now.date() or (report_date == now.date() and now.time() > dl),
                     "content": (rec.content if rec and isinstance(rec.content, dict) else {})})
    since = report_date - timedelta(days=29)
    mine = (db.query(DailyReport).filter(DailyReport.user_id == user.id, DailyReport.report_date >= since)
            .order_by(DailyReport.report_date.desc(), DailyReport.slot).all())
    working_days = len({r.report_date for r in mine}) or 1
    streak = 0
    d = report_date
    while True:
        got = [r for r in mine if r.report_date == d]
        if len(got) < 2:
            break
        streak += 1
        d -= timedelta(days=1)
    return render(request, "daily_reports/index.html", {
        "user": user, "rows": rows, "report_date": report_date, "today": now.date(), "now": now,
        "dept_code": code, "mine": mine[:20], "submitted_30": len(mine),
        "late_30": len([r for r in mine if r.is_late]), "streak": streak, "working_days": working_days,
        "can_team": _can_see_team(user),
        "prev_day": (report_date - timedelta(days=1)).isoformat(),
        "next_day": (report_date + timedelta(days=1)).isoformat() if report_date < now.date() else None})


@router.post("/submit", include_in_schema=False)
async def submit(request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require("daily_reports.add"))):
    f = await request.form()
    slot = f.get("slot") if f.get("slot") in ("morning", "afternoon") else "morning"
    now = ops_jobs.org_now()
    report_date = parse_date(f.get("report_date"), now.date()) or now.date()
    if report_date > now.date():
        return redirect("/daily-reports", "You cannot file a report for a future day.", "error")
    code = _dept_code(db, user)
    content = {}
    for field in _fields(code, slot):
        value = (f.get(field["name"]) or "").strip()
        if value:
            content[field["name"]] = value
    if not content:
        return redirect(f"/daily-reports?day={report_date.isoformat()}",
                        "A report needs at least one field filled in — an empty report is not a report.", "error")
    rec = (db.query(DailyReport).filter(DailyReport.user_id == user.id, DailyReport.report_date == report_date,
                                        DailyReport.slot == slot).first())
    created = rec is None
    if rec is None:
        rec = DailyReport(user_id=user.id, report_date=report_date, slot=slot)
        db.add(rec)
    before = {"content": rec.content, "is_late": rec.is_late} if not created else None
    rec.department_id = user.department_id
    rec.content = content
    rec.summary = (f.get("summary") or content.get("priorities") or content.get("completed") or "")[:500] or None
    rec.submitted_at = datetime.utcnow()
    rec.is_late = _late_for(db, report_date, slot, now)
    db.flush()
    log_action(db, user, "create" if created else "update", "daily_reports", entity=rec,
               description=f"{slot.title()} report {'submitted' if created else 'updated'} for {report_date}"
                           + (" (late)" if rec.is_late else ""),
               before=before, after={"fields": list(content.keys()), "is_late": rec.is_late}, request=request)
    db.commit()
    msg = f"{slot.title()} report submitted."
    if rec.is_late:
        msg += f" Recorded as late — the deadline was {ops_jobs.deadline_time(db, slot).strftime('%H:%M')}."
    return redirect(f"/daily-reports?day={report_date.isoformat()}", msg, "warning" if rec.is_late else "success")


# ================================================================================================ history
@router.get("/history", include_in_schema=False)
def history(request: Request, page: int = 1, start: str = "", end: str = "", slot: str = "", late: int = 0,
            user_id: int | None = None, db: Session = Depends(get_db),
            user: User = Depends(require("daily_reports.view"))):
    d_end = parse_date(end, date.today())
    d_start = parse_date(start, d_end - timedelta(days=29))
    target_id = user.id
    if user_id and user_id != user.id:
        if not _can_see_team(user):
            raise PermissionDenied("daily_reports.approve")
        target_id = user_id
    q = db.query(DailyReport).filter(DailyReport.user_id == target_id, DailyReport.report_date >= d_start,
                                     DailyReport.report_date <= d_end)
    if slot:
        q = q.filter(DailyReport.slot == slot)
    if late:
        q = q.filter(DailyReport.is_late.is_(True))
    pg = paginate(q.order_by(DailyReport.report_date.desc(), DailyReport.slot), page, 30)
    target = db.query(User).filter(User.id == target_id).first()
    qs = f"start={d_start}&end={d_end}&slot={slot}&late={late or ''}&user_id={user_id or ''}"
    return render(request, "daily_reports/history.html", {
        "user": user, "page": pg, "start": d_start, "end": d_end, "slot": slot, "late": late,
        "target": target, "user_id": user_id, "qs": qs, "can_team": _can_see_team(user),
        "users": [(u.id, u.full_name) for u in _reporting_users(db)] if _can_see_team(user) else [],
        "total_late": q.filter(DailyReport.is_late.is_(True)).count() if not late else pg.total,
        "slots": [s for s, _ in SLOTS]})


# ================================================================================================ team compliance
def _team_rows(db: Session, d_start: date, d_end: date, department_id: int | None) -> dict:
    users = _reporting_users(db)
    if department_id:
        users = [u for u in users if u.department_id == department_id]
    user_ids = [u.id for u in users] or [-1]
    records = (db.query(DailyReport).filter(DailyReport.user_id.in_(user_ids),
                                            DailyReport.report_date >= d_start, DailyReport.report_date <= d_end).all())
    by_user: dict[int, list] = {}
    for r in records:
        by_user.setdefault(r.user_id, []).append(r)
    days = [d_start + timedelta(days=i) for i in range((d_end - d_start).days + 1)]
    working = [d for d in days if d.weekday() != 6]  # Sunday off
    expected_per_user = len(working) * 2
    depts = {d.id: d for d in db.query(Department)}
    rows, dept_totals = [], {}
    for u in users:
        recs = by_user.get(u.id, [])
        submitted = len(recs)
        late = len([r for r in recs if r.is_late])
        missing = max(0, expected_per_user - submitted)
        dept = depts.get(u.department_id)
        dept_name = dept.name if dept else "Unassigned"
        rows.append({"user": u, "department": dept_name, "submitted": submitted, "late": late, "missing": missing,
                     "on_time": submitted - late, "expected": expected_per_user,
                     "compliance": round(100 * submitted / expected_per_user, 1) if expected_per_user else 0,
                     "last": max((r.report_date for r in recs), default=None)})
        t = dept_totals.setdefault(dept_name, {"department": dept_name, "submitted": 0, "late": 0, "missing": 0,
                                               "expected": 0, "people": 0})
        t["submitted"] += submitted
        t["late"] += late
        t["missing"] += missing
        t["expected"] += expected_per_user
        t["people"] += 1
    for t in dept_totals.values():
        t["compliance"] = round(100 * t["submitted"] / t["expected"], 1) if t["expected"] else 0
        t["on_time_pct"] = round(100 * (t["submitted"] - t["late"]) / t["submitted"], 1) if t["submitted"] else 0
    totals = {"submitted": sum(r["submitted"] for r in rows), "late": sum(r["late"] for r in rows),
              "missing": sum(r["missing"] for r in rows), "expected": sum(r["expected"] for r in rows),
              "people": len(rows)}
    totals["compliance"] = round(100 * totals["submitted"] / totals["expected"], 1) if totals["expected"] else 0
    return {"rows": sorted(rows, key=lambda r: (r["compliance"], -r["late"])),
            "departments": sorted(dept_totals.values(), key=lambda t: t["department"]),
            "totals": totals, "working_days": len(working), "expected_per_user": expected_per_user}


@router.get("/team", include_in_schema=False)
def team(request: Request, start: str = "", end: str = "", department_id: int | None = None,
         db: Session = Depends(get_db), user: User = Depends(require("daily_reports.view"))):
    if not _can_see_team(user):
        raise PermissionDenied("daily_reports.approve")
    d_end = parse_date(end, date.today())
    d_start = parse_date(start, d_end - timedelta(days=13))
    data = _team_rows(db, d_start, d_end, department_id)
    deadlines = _deadlines(db)
    return render(request, "daily_reports/team.html", {
        "user": user, "start": d_start, "end": d_end, "department_id": department_id,
        "departments": [(d.id, d.name) for d in db.query(Department).order_by(Department.name)],
        "morning": deadlines["morning"].strftime("%H:%M"), "afternoon": deadlines["afternoon"].strftime("%H:%M"),
        "today": date.today(), "can_team": True, "rows": data["rows"], "dept_summary": data["departments"],
        "totals": data["totals"], "working_days": data["working_days"],
        "expected_per_user": data["expected_per_user"],
        "labels": [t["department"] for t in data["departments"]],
        "series_compliance": [t["compliance"] for t in data["departments"]],
        "series_late": [t["late"] for t in data["departments"]],
        "qs": f"start={d_start}&end={d_end}&department_id={department_id or ''}"})


@router.get("/export", include_in_schema=False)
def export(request: Request, start: str = "", end: str = "", department_id: int | None = None, scope: str = "team",
           db: Session = Depends(get_db), user: User = Depends(require("daily_reports.view"))):
    from fastapi.responses import FileResponse
    from app.services import reports as report_svc
    d_end = parse_date(end, date.today())
    d_start = parse_date(start, d_end - timedelta(days=13))
    if scope == "team" and not _can_see_team(user):
        scope = "mine"
    if scope == "team":
        data = _team_rows(db, d_start, d_end, department_id)
        headers = ["Employee", "Department", "Expected", "Submitted", "On time", "Late", "Missing", "Compliance %",
                   "Last report"]
        rows = [[r["user"].full_name, r["department"], r["expected"], r["submitted"], r["on_time"], r["late"],
                 r["missing"], r["compliance"], r["last"].isoformat() if r["last"] else ""] for r in data["rows"]]
        t = data["totals"]
        totals = ["TOTAL", f"{t['people']} people", t["expected"], t["submitted"],
                  t["submitted"] - t["late"], t["late"], t["missing"], t["compliance"], ""]
        name = f"daily-report-compliance-{d_start}-to-{d_end}"
    else:
        recs = (db.query(DailyReport).filter(DailyReport.user_id == user.id, DailyReport.report_date >= d_start,
                                             DailyReport.report_date <= d_end)
                .order_by(DailyReport.report_date.desc(), DailyReport.slot).all())
        headers = ["Date", "Slot", "Late", "Submitted at", "Summary", "Fields"]
        rows = [[r.report_date, r.slot, "yes" if r.is_late else "no", r.submitted_at, r.summary or "",
                 "; ".join(f"{k}: {v}" for k, v in (r.content or {}).items())] for r in recs]
        totals = None
        name = f"my-daily-reports-{d_start}-to-{d_end}"
    path = report_svc.export_csv(headers, rows, name, totals=totals)
    report_svc.log_run(db, user, "daily_reports", f"Daily reports ({scope}) {d_start} to {d_end}",
                       {"start": d_start, "end": d_end, "department_id": department_id, "scope": scope},
                       "csv", path, len(rows))
    log_action(db, user, "export", "daily_reports",
               description=f"Daily report CSV export ({scope}, {d_start} to {d_end}, {len(rows)} rows)",
               request=request)
    db.commit()
    return FileResponse(path, filename=f"{name}.csv", media_type="text/csv")
