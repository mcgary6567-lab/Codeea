"""Counters for the Academic Home launchpad (mirrors the ERP: pending client requests + today's class status)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm import Case
from app.models.erp import TimeChangeRequest, TeacherChangeRequest, ReferredContact
from app.models.people import Leave, Student
from app.models.scheduling import ClassSession


def pending_requests(db: Session) -> list[dict]:
    """Client's Pending Requests row. Each entry links to the request list filtered on Pending."""
    def n(q):
        return q.count()
    return [
        {"label": "Client Complaints", "value": n(db.query(Case).filter(Case.case_type == "complaint", Case.approval_status == "pending")),
         "href": "/requests/complaints?status=pending"},
        {"label": "Leave Applications", "value": n(db.query(Leave).filter(Leave.person_type == "student", Leave.status == "pending")),
         "href": "/requests/leaves?status=pending"},
        {"label": "New References", "value": n(db.query(ReferredContact).filter(ReferredContact.status == "pending")),
         "href": "/requests/references?status=pending"},
        {"label": "Teacher Change Request", "value": n(db.query(TeacherChangeRequest).filter(TeacherChangeRequest.status == "pending")),
         "href": "/requests/teacher-change?status=pending"},
        {"label": "Time Change Request", "value": n(db.query(TimeChangeRequest).filter(TimeChangeRequest.status == "pending")),
         "href": "/requests/time-change?status=pending"},
    ]


def todays_class_status(db: Session, day: date | None = None) -> list[dict]:
    """Today's Class Status Summary row. Each entry links to the class schedule filtered on that status."""
    day = day or date.today()
    counts = {s: c for s, c in db.query(ClassSession.status, func.count(ClassSession.id))
              .filter(ClassSession.date == day).group_by(ClassSession.status)}
    free = db.query(Student).filter(Student.status == "free").count()
    rows = [("Free Students", free, "/students?status=free")]
    for label, key in [("Pending Classes", "pending"), ("Done Classes", "done"), ("Missed Classes", "missed"),
                       ("Student Absent", "absent"), ("Student On-leave", "leave"), ("Cancelled Classes", "cancelled"),
                       ("Rescheduled Classes", "rescheduled")]:
        rows.append((label, counts.get(key, 0), f"/classes?date={day.isoformat()}&status={key}"))
    return [{"label": l, "value": v, "href": h} for l, v, h in rows]


# =============================================================================== Human Resource Home
# Their HR Home carries four panels above the group cards (docs/AUDIT_HUMAN_RESOURCE.md, Level 1): Gender
# Distribution, Contract Ends (Within 2 months), Today's Attendance Status per shift, and Pending Requests.
# Each function returns plain dicts the way pending_requests() does, so the template and the tests read
# the same figures. Imports stay local so this block can be appended without touching the header.
CONTRACT_WINDOW_DAYS = 60
SHIFT_ORDER = ["morning", "evening", "night"]


def _live_employees(db: Session):
    from app.models.people import Employee
    from app.services.hr_dashboards import LEFT_STATUSES
    return db.query(Employee).filter(Employee.status.notin_(LEFT_STATUSES))


def hr_gender_distribution(db: Session) -> list[dict]:
    """Total Male / Total Female / Total Employee over live employees, each linking to the Employee Record."""
    from app.models.people import Employee
    live = _live_employees(db)
    male = live.filter(Employee.gender == "male").count()
    female = live.filter(Employee.gender == "female").count()
    return [
        {"label": "Total Male", "value": male, "href": "/hr/employees?gender=male"},
        {"label": "Total Female", "value": female, "href": "/hr/employees?gender=female"},
        {"label": "Total Employee", "value": live.count(), "href": "/hr/employees"},
    ]


def hr_contract_ends(db: Session, days: int = CONTRACT_WINDOW_DAYS, today: date | None = None) -> list[dict]:
    """Contract Ends (Within 2 months): live employees whose contract_end_date falls in the next ``days`` days."""
    from datetime import timedelta
    from app.models.people import Employee
    today = today or date.today()
    rows = (_live_employees(db)
            .filter(Employee.contract_end_date.isnot(None), Employee.contract_end_date >= today,
                    Employee.contract_end_date <= today + timedelta(days=days))
            .order_by(Employee.contract_end_date, Employee.employee_code).all())
    return [{"employee": e, "label": f"{e.employee_code}-{e.full_name}", "start": e.join_date,
             "end": e.contract_end_date, "remaining": (e.contract_end_date - today).days,
             "href": f"/hr/employees/{e.id}"} for e in rows]


def hr_todays_attendance(db: Session, day: date | None = None) -> dict:
    """Today's Attendance Status, one block per shift: Total, Absent, Present, On Leave, Late Coming.

    Figures come from hr_dashboards.attendance_dashboard for the one day, folded to one verdict per
    employee (on leave beats present beats absent; late is counted alongside). When nobody has punched
    yet today the panel falls back to the last recorded working day, exactly as the Daily Attendance
    board does, and says so through ``fallback``.
    """
    from app.models.people import HRAttendance
    from app.services.hr_dashboards import attendance_dashboard
    day = day or date.today()
    fallback = False
    if not db.query(HRAttendance.id).filter(HRAttendance.date == day).first():
        last = db.query(func.max(HRAttendance.date)).filter(HRAttendance.date <= day).scalar()
        if last:
            day, fallback = last, True
    dash = attendance_dashboard(db, day, day)
    by_emp = {r["employee"].id: r for r in dash["by_employee"] if r["employee"] is not None}
    live = _live_employees(db).all()
    shifts = [s for s in SHIFT_ORDER if any((e.shift or "") == s for e in live)]
    blocks = []
    for shift in shifts:
        staff = [e for e in live if (e.shift or "") == shift]
        present = absent = leave = late = 0
        for e in staff:
            r = by_emp.get(e.id)
            if r is None:
                continue
            if r["leave"] > 0:
                leave += 1
            elif r["present"] > 0:
                present += 1
            elif r["absent"] > 0:
                absent += 1
            if r["late"] > 0:
                late += 1
        base = f"/hr/attendance?tab=daily&day={day.isoformat()}&shift={shift}"
        blocks.append({"shift": shift, "label": shift.title(), "counts": [
            {"label": "Total", "value": len(staff), "href": base},
            {"label": "Absent", "value": absent, "href": base + "&type=absent"},
            {"label": "Present", "value": present, "href": base + "&type=present"},
            {"label": "On Leave", "value": leave, "href": base + "&type=leave"},
            {"label": "Late Coming", "value": late, "href": base + "&type=late"},
        ]})
    return {"day": day, "fallback": fallback, "shifts": blocks}


def hr_pending_requests(db: Session) -> list[dict]:
    """Pending Requests row: seven queues, each the count of pending rows linking to that queue on Pending."""
    from app.models.hr_erp import AttendanceChangeRequest, EmployeeRequest, StaffComplaint
    from app.models.people import Bonus, Leave, SalaryAdvance, Violation

    def n(q):
        return q.count()
    return [
        {"label": "Attendance Change", "value": n(db.query(AttendanceChangeRequest).filter(AttendanceChangeRequest.status == "pending")),
         "href": "/hr/attendance/change-requests?status=pending"},
        {"label": "Complaints", "value": n(db.query(StaffComplaint).filter(StaffComplaint.status == "pending")),
         "href": "/hr/complaints?status=pending"},
        {"label": "Leave Request", "value": n(db.query(Leave).filter(Leave.person_type == "employee", Leave.status == "pending")),
         "href": "/hr/leaves?status=pending"},
        {"label": "Emp Requests", "value": n(db.query(EmployeeRequest).filter(EmployeeRequest.status == "pending")),
         "href": "/hr/requests?status=pending"},
        {"label": "Violations", "value": n(db.query(Violation).filter(Violation.approval_status == "pending")),
         "href": "/hr/violations?status=pending"},
        {"label": "Advances", "value": n(db.query(SalaryAdvance).filter(SalaryAdvance.status == "pending")),
         "href": "/hr/advances?status=pending"},
        {"label": "Bonuses", "value": n(db.query(Bonus).filter(Bonus.status == "pending")),
         "href": "/hr/bonuses?status=pending"},
    ]


def hr_home(db: Session) -> dict:
    """Everything the HR Home template needs, in the order the panels appear."""
    return {"gender": hr_gender_distribution(db), "contract_ends": hr_contract_ends(db),
            "attendance": hr_todays_attendance(db), "hr_pending": hr_pending_requests(db),
            "contract_window_days": CONTRACT_WINDOW_DAYS}
