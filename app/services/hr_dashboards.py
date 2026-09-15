"""Aggregations behind the Human Resource dashboards (audit §3 "Dashboards" card).

Three read-only dashboards, one function each, all pure: they take a Session (plus a date range where the
page has one) and return plain dicts, so the tests and the API can call them without going through a page.

    employee_dashboard(db)                  headcount splits, joiners / leavers by month, average tenure
    attendance_dashboard(db, start, end)    present / absent / leave / late by day and by employee
    financial_dashboard(db, months)         payroll cost by month and department, violations, bonuses, advances

Also here because it is the same kind of read-only roll-up:

    recruitment_summary(db)                 per vacancy: positions, applications by status, interviews, hires
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.core import Department
from app.models.hr_erp import APPLICATION_STATUSES, JobApplication
from app.models.people import (Bonus, Employee, HRAttendance, Interview, PayrollRun, Payslip, RecruitmentRequest,
                               SalaryAdvance, Violation)

ACTIVE_STATUSES = ["active", "probation", "on_leave"]
LEFT_STATUSES = ["resigned", "terminated"]
DEFAULT_DUTY_HOURS = 8.0


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _month_list(months: int, until: Optional[date] = None) -> list[str]:
    """The last ``months`` month keys (YYYY-MM), oldest first, ending with the month of ``until``."""
    d = (until or date.today()).replace(day=1)
    keys: list[str] = []
    for _ in range(max(1, months)):
        keys.append(d.strftime("%Y-%m"))
        d = (d - timedelta(days=1)).replace(day=1)
    return list(reversed(keys))


def _counts(rows, key) -> list[dict]:
    """[{label, count}] ordered by count desc, blanks folded into 'Not set'."""
    c = Counter((key(r) or "Not set") for r in rows)
    return [{"label": k, "count": v} for k, v in sorted(c.items(), key=lambda kv: (-kv[1], str(kv[0])))]


# =============================================================================== 1. employees
def employee_dashboard(db: Session) -> dict:
    employees = db.query(Employee).order_by(Employee.id).all()
    depts = {d.id: d.name for d in db.query(Department)}
    live = [e for e in employees if e.status not in LEFT_STATUSES]

    months = _month_list(12)
    joiners = {m: 0 for m in months}
    leavers = {m: 0 for m in months}
    for e in employees:
        if e.join_date:
            key = e.join_date.strftime("%Y-%m")
            if key in joiners:
                joiners[key] += 1
        if e.exit_date:
            key = e.exit_date.strftime("%Y-%m")
            if key in leavers:
                leavers[key] += 1

    today = date.today()
    tenures = [((e.exit_date or today) - e.join_date).days for e in employees if e.join_date]
    live_tenures = [(today - e.join_date).days for e in live if e.join_date]

    return {
        "headcount": len(live),
        "total": len(employees),
        "leavers_total": len(employees) - len(live),
        "by_department": _counts(live, lambda e: depts.get(e.department_id)),
        "by_designation": _counts(live, lambda e: e.designation),
        "by_employee_type": _counts(live, lambda e: e.employee_type),
        "by_shift": _counts(live, lambda e: (e.shift or "").title()),
        "by_gender": _counts(live, lambda e: (e.gender or "").title()),
        "by_status": _counts(employees, lambda e: e.status),
        "months": months,
        "joiners": [joiners[m] for m in months],
        "leavers": [leavers[m] for m in months],
        "avg_tenure_days": round(sum(tenures) / len(tenures), 1) if tenures else 0.0,
        "avg_tenure_years": round((sum(tenures) / len(tenures)) / 365.25, 2) if tenures else 0.0,
        "avg_tenure_live_years": round((sum(live_tenures) / len(live_tenures)) / 365.25, 2) if live_tenures else 0.0,
    }


# =============================================================================== 2. attendance
def attendance_dashboard(db: Session, start: date, end: date, department_id: Optional[int] = None) -> dict:
    """Present / absent / leave / late counts by day and by employee for a range, with shortage hours.

    Shortage is the unworked part of an employee's duty hours: attendance is recorded twice a day, so a
    session is expected to cover half of ``Employee.duty_hours`` (8 hours when it is not set).
    """
    q = db.query(HRAttendance).filter(HRAttendance.date >= start, HRAttendance.date <= end)
    rows = q.order_by(HRAttendance.date).all()
    employees = {e.id: e for e in db.query(Employee)}
    if department_id:
        rows = [r for r in rows if employees.get(r.employee_id) and employees[r.employee_id].department_id == department_id]

    by_day: dict[str, dict] = {}
    by_emp: dict[int, dict] = {}
    totals = {"present": 0, "absent": 0, "leave": 0, "late": 0, "half_day": 0, "holiday": 0, "sessions": 0,
              "shortage_hours": 0.0, "worked_hours": 0.0}
    for r in rows:
        emp = employees.get(r.employee_id)
        day = by_day.setdefault(r.date.isoformat(), {"day": r.date, "present": 0, "absent": 0, "leave": 0, "late": 0,
                                                     "shortage_hours": 0.0})
        row = by_emp.setdefault(r.employee_id, {
            "employee": emp, "code": emp.employee_code if emp else "", "name": emp.full_name if emp else "Unknown",
            "department": emp.department.name if emp and emp.department else "-", "present": 0, "absent": 0,
            "leave": 0, "late": 0, "sessions": 0, "shortage_hours": 0.0, "late_minutes": 0})
        status = r.status or "present"
        bucket = status if status in ("present", "absent", "leave", "half_day", "holiday") else "present"
        if status == "late":
            bucket = "present"
        totals["sessions"] += 1
        row["sessions"] += 1
        if bucket in totals:
            totals[bucket] += 1
        if bucket in day:
            day[bucket] += 1
        if bucket in row:
            row[bucket] += 1
        is_late = status == "late" or (r.late_minutes or 0) > 0
        if is_late:
            totals["late"] += 1
            day["late"] += 1
            row["late"] += 1
            row["late_minutes"] += int(r.late_minutes or 0)

        expected = (_f(emp.duty_hours) if emp and emp.duty_hours else DEFAULT_DUTY_HOURS) / 2.0
        worked = 0.0
        if r.check_in and r.check_out:
            worked = max(0.0, (r.check_out - r.check_in).total_seconds() / 3600.0)
        if bucket in ("absent",):
            shortage = expected
        elif bucket in ("leave", "holiday"):
            shortage = 0.0
        else:
            shortage = max(0.0, expected - worked)
        totals["worked_hours"] += worked
        totals["shortage_hours"] += shortage
        day["shortage_hours"] = round(day["shortage_hours"] + shortage, 2)
        row["shortage_hours"] = round(row["shortage_hours"] + shortage, 2)

    for row in by_emp.values():
        marked = row["present"] + row["absent"] + row["leave"] + row["half_day"] if "half_day" in row else row["present"] + row["absent"] + row["leave"]
        row["attendance_pct"] = round(100 * row["present"] / marked, 1) if marked else 0.0
    days = sorted(by_day.values(), key=lambda d: d["day"])
    people = sorted(by_emp.values(), key=lambda r: r["name"])
    totals["shortage_hours"] = round(totals["shortage_hours"], 2)
    totals["worked_hours"] = round(totals["worked_hours"], 1)
    totals["attendance_pct"] = round(100 * totals["present"] / totals["sessions"], 1) if totals["sessions"] else 0.0
    return {
        "start": start, "end": end, "totals": totals, "by_day": days, "by_employee": people,
        "worst_attendance": sorted([r for r in people if r["sessions"] >= 3], key=lambda r: (r["attendance_pct"], -r["absent"]))[:10],
        "most_late": sorted([r for r in people if r["late"]], key=lambda r: (-r["late"], -r["late_minutes"]))[:10],
        "highest_shortage": sorted([r for r in people if r["shortage_hours"]], key=lambda r: -r["shortage_hours"])[:10],
    }


# =============================================================================== 3. financial
def financial_dashboard(db: Session, months: int = 6) -> dict:
    keys = _month_list(months)
    runs = db.query(PayrollRun).filter(PayrollRun.period.in_(keys)).order_by(PayrollRun.period).all()
    run_by_period: dict[str, PayrollRun] = {}
    for r in runs:
        run_by_period[r.period] = r  # the latest run for a period wins (ids ascend)

    cost_by_month = {k: 0.0 for k in keys}
    for period, run in run_by_period.items():
        cost_by_month[period] = round(_f(run.total_net), 2)

    dept_cost: dict[str, float] = defaultdict(float)
    slips = db.query(Payslip).filter(Payslip.payroll_run_id.in_([r.id for r in run_by_period.values()] or [0])).all()
    for ps in slips:
        emp = ps.employee
        name = emp.department.name if emp and emp.department else "Unassigned"
        dept_cost[name] += _f(ps.net)

    start = date.fromisoformat(keys[0] + "-01")
    viol_rows = db.query(Violation).filter(Violation.date >= start).all()
    violations_by_month = {k: 0.0 for k in keys}
    violations_total = 0.0
    for v in viol_rows:
        if v.approval_status not in ("approved",) and v.status not in ("approved", "closed"):
            continue
        key = v.date.strftime("%Y-%m") if v.date else ""
        if key in violations_by_month:
            violations_by_month[key] = round(violations_by_month[key] + _f(v.deduction_amount), 2)
            violations_total += _f(v.deduction_amount)

    bonus_rows = db.query(Bonus).filter(Bonus.status == "approved").all()
    bonuses_by_month = {k: 0.0 for k in keys}
    bonuses_total = 0.0
    for b in bonus_rows:
        key = b.period or (b.created_at.strftime("%Y-%m") if b.created_at else "")
        if key in bonuses_by_month:
            bonuses_by_month[key] = round(bonuses_by_month[key] + _f(b.amount), 2)
            bonuses_total += _f(b.amount)

    advances = db.query(SalaryAdvance).filter(SalaryAdvance.status.in_(["approved", "paid"])).all()
    outstanding = round(sum(_f(a.remaining) for a in advances), 2)

    return {
        "months": keys,
        "cost_by_month": [cost_by_month[k] for k in keys],
        "runs": [{"period": k, "status": run_by_period[k].status if k in run_by_period else "pending",
                  "description": run_by_period[k].description if k in run_by_period else None,
                  "net": cost_by_month[k], "gross": _f(run_by_period[k].total_gross) if k in run_by_period else 0.0,
                  "deductions": _f(run_by_period[k].total_deductions) if k in run_by_period else 0.0,
                  "payslips": len(run_by_period[k].payslips) if k in run_by_period else 0,
                  "id": run_by_period[k].id if k in run_by_period else None} for k in keys],
        "by_department": sorted([{"label": k, "amount": round(v, 2)} for k, v in dept_cost.items()], key=lambda r: -r["amount"]),
        "violations_by_month": [violations_by_month[k] for k in keys],
        "violations_total": round(violations_total, 2),
        "bonuses_by_month": [bonuses_by_month[k] for k in keys],
        "bonuses_total": round(bonuses_total, 2),
        "advances_outstanding": outstanding,
        "advances_count": sum(1 for a in advances if _f(a.remaining) > 0),
        "total_cost": round(sum(cost_by_month.values()), 2),
    }


# =============================================================================== recruitment summary
def recruitment_summary(db: Session) -> list[dict]:
    """Per vacancy: positions, applications, by-status counts, interviews held, hires and time to hire."""
    requests = db.query(RecruitmentRequest).order_by(RecruitmentRequest.id.desc()).all()
    apps = db.query(JobApplication).all()
    by_request: dict[Optional[int], list[JobApplication]] = defaultdict(list)
    for a in apps:
        by_request[a.request_id].append(a)
    interviews = db.query(Interview).all()
    iv_by_candidate: dict[int, int] = Counter(i.candidate_id for i in interviews)
    employees = {e.id: e for e in db.query(Employee)}

    out: list[dict] = []
    for r in requests:
        rows = by_request.get(r.id, [])
        counts = {s: 0 for s in APPLICATION_STATUSES}
        for a in rows:
            if a.status in counts:
                counts[a.status] += 1
        hires = [a for a in rows if a.status == "hired"]
        held = sum(iv_by_candidate.get(a.candidate_id, 0) for a in rows if a.candidate_id)
        gaps = []
        for a in hires:
            emp = employees.get(a.hired_employee_id) if a.hired_employee_id else None
            if a.application_date and emp and emp.join_date:
                gaps.append((emp.join_date - a.application_date).days)
        out.append({
            "request": r, "id": r.id, "title": r.title, "positions": r.positions or 0,
            "department": r.department.name if r.department else "-", "status": r.status,
            "applications": len(rows), "counts": counts, "interviews": held, "hires": len(hires),
            "time_to_hire": round(sum(gaps) / len(gaps), 1) if gaps else None,
            "fill_pct": round(100 * len(hires) / (r.positions or 1), 1),
        })
    return out


def application_funnel(db: Session) -> dict[str, int]:
    """How many applications sit at each step of the ten-step pipeline."""
    counts = {s: 0 for s in APPLICATION_STATUSES}
    for (status, ) in db.query(JobApplication.status).all():
        if status in counts:
            counts[status] += 1
    return counts


# =============================================================================== 5. employee self portal
# The ERP's dashboard carries "My Team" as an expandable tree of the reporting line, and its Team
# Management pages let a manager raise a violation or a bonus against anyone beneath them. Both need the
# same walk of Employee.manager_id, so it lives here with the rest of the read-only roll-ups.
MAX_TREE_DEPTH = 12  # a guard, not a rule: it stops a cycle in manager_id from spinning forever


def _children_map(db: Session) -> dict[Optional[int], list[Employee]]:
    """Every employee grouped under the manager they report to, ordered as the ERP orders staff."""
    rows = db.query(Employee).order_by(Employee.sort_no, Employee.employee_code).all()
    out: dict[Optional[int], list[Employee]] = defaultdict(list)
    for e in rows:
        out[e.manager_id].append(e)
    return out


def reporting_tree(db: Session, root: Optional[Employee], include_left: bool = False) -> list[dict]:
    """The reporting line beneath ``root`` as nested nodes.

    Each node is {employee, depth, children}. ``root`` itself is not included — the ERP's tree shows the
    people under you. Employees who have left are dropped unless ``include_left`` is set, and a visited
    set keeps a bad manager_id loop from recursing for ever.
    """
    if root is None:
        return []
    children = _children_map(db)
    seen: set[int] = {root.id}

    def walk(parent_id: int, depth: int) -> list[dict]:
        if depth > MAX_TREE_DEPTH:
            return []
        nodes = []
        for e in children.get(parent_id, []):
            if e.id in seen:
                continue
            if not include_left and e.status in LEFT_STATUSES:
                continue
            seen.add(e.id)
            nodes.append({"employee": e, "depth": depth, "children": walk(e.id, depth + 1)})
        return nodes

    return walk(root.id, 0)


def flatten_tree(nodes: list[dict]) -> list[dict]:
    """The same nodes depth-first, so a template can render one row per person."""
    out: list[dict] = []
    for n in nodes:
        out.append(n)
        out.extend(flatten_tree(n["children"]))
    return out


def subordinate_ids(db: Session, root: Optional[Employee], include_left: bool = True) -> set[int]:
    """Employee ids genuinely beneath ``root``. The authority for "may I raise this against them?"."""
    return {n["employee"].id for n in flatten_tree(reporting_tree(db, root, include_left=include_left))}


def team_user_ids(db: Session, root: Optional[Employee], include_left: bool = True) -> set[int]:
    """The user accounts of everyone beneath ``root`` — what "Others Tasks" is scoped to."""
    ids = subordinate_ids(db, root, include_left=include_left)
    if not ids:
        return set()
    return {uid for (uid, ) in db.query(Employee.user_id).filter(Employee.id.in_(ids)) if uid}
