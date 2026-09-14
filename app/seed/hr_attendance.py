"""ERP Time and Attendance Management seed (docs/AUDIT_HUMAN_RESOURCE.md, Level 3).

Adds the four things the attendance pages need history for:

* **Holidays** - the coming year of Pakistan public holidays, including a multi-day Eid break. Leave
  approval skips these days, so the entitlement maths is visibly correct out of the box.
* **Leave assignments** - a casual entitlement for every active member of staff plus sick and annual for
  some, with ``consumed`` recomputed from the leaves already approved by the ``hr`` seed.
* **Attendance change requests** - about forty over the last sixty days, spread across pending, approved,
  rejected and cancelled. The approved ones really did rewrite the attendance row they name.
* **Progress notes** - about a hundred and twenty on recent working days, half of them rated by a manager.

Idempotent: re-running only tops up what is missing. Deterministic (fixed random seed).
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models.core import User
from app.models.hr_erp import AttendanceChangeRequest, Holiday, LeaveEntitlement, ProgressNote
from app.models.people import Employee, HRAttendance, Leave
from app.services import hr as svc

rnd = random.Random(20260914)

CHANGE_REQUESTS = 40
PROGRESS_NOTES = 120
LOOKBACK_DAYS = 60

# Pakistan public holidays for the coming year. Lunar dates are the expected ones and move with the
# moon sighting, which is why each carries a note.
HOLIDAYS = [
    ("Iqbal Day", (2026, 11, 9), None, None),
    ("Quaid-e-Azam Day", (2026, 12, 25), None, None),
    ("Kashmir Solidarity Day", (2027, 2, 5), None, None),
    ("Eid ul Fitr", (2027, 3, 9), (2027, 3, 11), "Three-day Eid break. Lunar date, subject to moon sighting."),
    ("Pakistan Day", (2027, 3, 23), None, None),
    ("Labour Day", (2027, 5, 1), None, None),
    ("Eid ul Adha", (2027, 5, 17), None, "Lunar date, subject to moon sighting."),
    ("Youm-e-Takbir", (2027, 5, 28), None, None),
    ("Ashura (10th Muharram)", (2027, 6, 15), None, "Lunar date, subject to moon sighting."),
    ("Independence Day", (2027, 8, 14), None, None),
]

ENTITLEMENTS = [  # (leave type, days, every Nth employee gets it)
    ("casual", 12, 1),
    ("sick", 8, 3),
    ("annual", 14, 4),
]

CHANGE_REASONS = [
    "The fingerprint reader did not register my morning punch; the supervisor saw me at my desk.",
    "Marked absent although I taught the full shift from the backup connection.",
    "I forgot to log out when the last class over-ran, so the shift shows as open.",
    "Approved half day for a hospital appointment, recorded as a full absence.",
    "Power outage at home; I logged in from the office fifteen minutes later than the app shows.",
    "Was on approved leave that day, but the sheet shows me absent.",
    "Check-in registered twice and the later one was kept.",
    "Attended the Tajweed workshop off-site, which is duty time, not absence.",
]
HR_REMARKS = {
    "approved": ["Supervisor confirmed the shift was worked. Attendance corrected.",
                 "Backup connection log checked; correction applied.",
                 "Approved leave on file for this date; row rewritten."],
    "rejected": ["No supporting record of the shift being worked. Request declined.",
                 "The device log shows no activity in this window.",
                 "Raised outside the seven-day correction window."],
    "cancelled": ["Withdrawn by the employee after checking their own record.",
                  "Duplicate of an earlier request for the same date."],
}
PROGRESS_DETAILS = [
    "Completed all six scheduled Hifz classes and updated every sabaq record.",
    "Cleared the Qaida assessment backlog for the beginner batch.",
    "Ran two trial classes; both families asked for a regular slot.",
    "Covered a colleague's evening shift on top of my own timetable.",
    "Recorded and uploaded revision audio for the weak-recitation group.",
    "Called nine families about attendance and logged the outcomes.",
    "Prepared the monthly progress cards for my whole class list.",
    "Finished the safeguarding refresher and signed the acknowledgement.",
    "Reconciled the week's class register against the teacher reports.",
    "Sat in on a QA review and applied the two corrections suggested.",
    "Drafted the Tajweed drill sheet now used by the junior teachers.",
    "Resolved the three open parent complaints assigned to me.",
    "Onboarded two new students, including the first lesson plan.",
    "Updated the lesson bank with fifteen new Tarjuma exercises.",
    "Handled the shift handover and chased every missing attendance row.",
    "Reviewed the month's payroll inputs for my department and flagged two errors.",
    "Completed the device audit for my team and closed the ticket.",
    "Trained a new joiner on the class platform and the recording etiquette.",
]
MANAGER_COMMENTS = [
    "Consistent and well documented.", "Good recovery on a difficult day.",
    "Needs to record detail earlier in the day.", "Excellent initiative, noted for the review.",
    "Solid, but follow the handover checklist next time.", "Exactly what the role needs.",
]


def _hr_user(db: Session) -> User | None:
    return (db.query(User).filter(User.email == "hr@oqc.local").first()
            or db.query(User).filter(User.email == "admin@oqc.local").first())


def _seed_holidays(db: Session) -> int:
    made = 0
    for name, start, end, note in HOLIDAYS:
        d = date(*start)
        if db.query(Holiday).filter(Holiday.name == name, Holiday.holiday_date == d).first():
            continue
        db.add(Holiday(name=name, holiday_date=d, end_date=date(*end) if end else None, shift_group="all",
                       is_paid=True, status="active", notes=note))
        made += 1
    db.flush()
    return made


def _seed_entitlements(db: Session, staff: list[Employee]) -> int:
    expiry = date(date.today().year, 12, 31)
    made = 0
    for i, emp in enumerate(staff):
        for leave_type, days, every in ENTITLEMENTS:
            if i % every:
                continue
            exists = (db.query(LeaveEntitlement)
                      .filter(LeaveEntitlement.employee_id == emp.id, LeaveEntitlement.leave_type == leave_type,
                              LeaveEntitlement.expiry_date == expiry).first())
            if exists:
                continue
            db.add(LeaveEntitlement(employee_id=emp.id, leave_type=leave_type, total_assigned=float(days),
                                    consumed=0.0, expiry_date=expiry, status="active",
                                    notes=f"{date.today().year} allocation"))
            made += 1
    db.flush()
    for emp in staff:  # consumed must mirror the leaves the hr seed already approved
        svc.sync_entitlement_consumption(db, emp)
    return made


def _apply_approved(db: Session, req: AttendanceChangeRequest, emp: Employee, user: User | None) -> None:
    """Mirror of svc.decide_change_request's rewrite, without the notification storm a seed would cause."""
    row = db.get(HRAttendance, req.attendance_id) if req.attendance_id else None
    if row is None:
        row = HRAttendance(employee_id=req.employee_id, date=req.attendance_date, session=req.session,
                           status=req.new_status)
        db.add(row)
        db.flush()
        req.attendance_id = row.id
    row.status = req.new_status
    row.check_in = datetime.combine(req.attendance_date, req.new_check_in) if req.new_check_in else None
    row.check_out = datetime.combine(req.attendance_date, req.new_check_out) if req.new_check_out else None
    if row.status in svc.WORKED_STATUSES:
        svc.recompute_late_minutes(emp, row)
    else:
        row.late_minutes = 0
    row.correction_requested = False
    row.correction_status = "approved"
    row.approved_by_id = user.id if user else None


def _seed_change_requests(db: Session, staff: list[Employee], user: User | None) -> int:
    have = db.query(AttendanceChangeRequest).count()
    if have >= CHANGE_REQUESTS:
        return 0
    today = date.today()
    window = today - timedelta(days=LOOKBACK_DAYS)
    emap = {e.id: e for e in staff}
    rows = (db.query(HRAttendance)
            .filter(HRAttendance.date >= window, HRAttendance.date <= today,
                    HRAttendance.employee_id.in_(list(emap.keys()) or [-1]),
                    HRAttendance.status.in_(["absent", "late", "leave"]))
            .order_by(HRAttendance.id).all())
    rnd.shuffle(rows)
    statuses = (["approved"] * 14) + (["rejected"] * 8) + (["cancelled"] * 5) + (["pending"] * 13)
    made = 0
    for row in rows:
        if have + made >= CHANGE_REQUESTS:
            break
        emp = emap.get(row.employee_id)
        if emp is None:
            continue
        if db.query(AttendanceChangeRequest).filter(
                AttendanceChangeRequest.employee_id == emp.id,
                AttendanceChangeRequest.attendance_date == row.date,
                AttendanceChangeRequest.session == row.session).first():
            continue
        status = statuses[made % len(statuses)]
        start_min = svc.expected_checkin_minutes(emp, row.session) % (24 * 60)
        new_in = time((start_min // 60) % 24, rnd.choice([0, 3, 5, 8]))
        out_min = (start_min + int(svc.session_duty_hours(emp) * 60) + rnd.choice([0, 10, 20])) % (24 * 60)
        new_out = time(out_min // 60, out_min % 60)
        req = AttendanceChangeRequest(
            employee_id=emp.id, attendance_id=row.id, attendance_date=row.date, session=row.session,
            old_status=row.status,
            old_check_in=row.check_in.time() if row.check_in else None,
            old_check_out=row.check_out.time() if row.check_out else None,
            new_status="present", new_check_in=new_in, new_check_out=new_out,
            user_remarks=rnd.choice(CHANGE_REASONS), status=status,
            request_date=min(today, row.date + timedelta(days=rnd.randint(1, 4))))
        if status == "pending":
            row.correction_requested = True
            row.correction_status = "pending"
            row.correction_reason = req.user_remarks
        else:
            req.hr_remarks = rnd.choice(HR_REMARKS[status])
            req.decided_by_id = user.id if user else None
            req.decided_at = datetime.combine(req.request_date, time(11, 0)) + timedelta(days=1)
        db.add(req)
        db.flush()
        if status == "approved":
            _apply_approved(db, req, emp, user)
        elif status in ("rejected", "cancelled"):
            row.correction_requested = False
            row.correction_status = status
        made += 1
    db.flush()
    return made


def _seed_progress(db: Session, staff: list[Employee], user: User | None) -> int:
    have = db.query(ProgressNote).count()
    if have >= PROGRESS_NOTES:
        return 0
    today = date.today()
    days = [today - timedelta(days=n) for n in range(0, 45)]
    days = [d for d in days if d.weekday() != 6]
    managers = [e for e in staff if not e.is_teacher] or staff
    made = 0
    while have + made < PROGRESS_NOTES and days and staff:
        emp = staff[made % len(staff)]
        day = days[rnd.randrange(len(days))]
        if db.query(ProgressNote).filter(ProgressNote.employee_id == emp.id,
                                         ProgressNote.working_date == day).first():
            made += 1
            continue
        note = ProgressNote(employee_id=emp.id, working_date=day, detail=rnd.choice(PROGRESS_DETAILS),
                            created_by_id=emp.user_id or (user.id if user else None))
        if made % 2 == 0:  # about half carry a manager rating
            note.manager_rating = rnd.choice([3, 3, 4, 4, 4, 5, 5, 2])
            note.manager_comment = rnd.choice(MANAGER_COMMENTS)
            mgr = managers[made % len(managers)]
            note.rated_by_id = mgr.user_id or (user.id if user else None)
            note.rated_at = datetime.combine(day, time(18, 30))
        db.add(note)
        made += 1
    db.flush()
    return made


def run(db: Session) -> None:
    staff = (db.query(Employee).filter(Employee.status.in_(["active", "probation", "on_leave"]))
             .order_by(Employee.id).all())
    if not staff:
        print("  - hr_attendance: no employees yet, skipped")
        return
    user = _hr_user(db)
    holidays = _seed_holidays(db)
    entitlements = _seed_entitlements(db, staff)
    changes = _seed_change_requests(db, staff, user)
    progress = _seed_progress(db, staff, user)
    db.flush()
    print(f"    holidays +{holidays} ({db.query(Holiday).count()}), leave assignments +{entitlements} "
          f"({db.query(LeaveEntitlement).count()}), attendance change requests +{changes} "
          f"({db.query(AttendanceChangeRequest).count()}), progress notes +{progress} "
          f"({db.query(ProgressNote).count()})")
