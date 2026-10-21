"""Biometric attendance machines (the old ERP's "Attendance Machine List", Fusion gap report M03.04.01.01).

Two ways for punches to arrive:

1. **Push**: ZKTeco-style machines in ADMS ("iclock") mode post their attendance log to ``/iclock/cdata``
   (app/web/attendance_machines.py). Only machines registered here, active, by serial number are accepted.
2. **File**: the machine's own software exports the log (CSV or the tab-separated ATTLOG text) and HR uploads it.

Each punch is kept as an ``AttendancePunch``. A machine user ID is matched to the employee whose
``attendance_machine_id`` is that ID. For every employee and day touched, the earliest punch becomes the check-in
and the latest the check-out of that day's attendance row, and the late minutes come from the same rule payroll
uses (``hr.recompute_late_minutes``). A day already corrected by HR through a change request is not overwritten.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models.core import User
from app.models.hr_erp import AttendanceMachine, AttendancePunch
from app.models.people import Employee, HRAttendance

log = logging.getLogger("oqc.attendance_machines")

TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M:%S",
                "%m/%d/%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S")


def parse_time(raw: str) -> Optional[datetime]:
    raw = (raw or "").strip()
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def machine_by_serial(db: Session, serial: str) -> Optional[AttendanceMachine]:
    serial = (serial or "").strip()
    if not serial:
        return None
    return db.query(AttendanceMachine).filter(AttendanceMachine.serial_no == serial).first()


def parse_attlog(text: str) -> list[tuple[str, datetime, str]]:
    """ADMS ATTLOG lines: ``PIN<TAB>YYYY-MM-DD HH:MM:SS<TAB>status<TAB>verify...`` -> (pin, time, status)."""
    out = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) < 2:
            continue
        when = parse_time(parts[1])
        if parts[0] and when:
            out.append((parts[0], when, parts[2] if len(parts) > 2 else ""))
    return out


def parse_csv(text: str) -> tuple[list[tuple[str, datetime, str]], list[str]]:
    """A CSV export: a user-ID column (user id / pin / emp no / ac-no) and a time column (time / datetime /
    punch time), or separate date and time columns. Returns punches and the problems found."""
    reader = csv.DictReader(io.StringIO(text))
    heads = {h.strip().lower(): h for h in (reader.fieldnames or []) if h}

    def col(*names):
        for n in names:
            if n in heads:
                return heads[n]
        return None
    uid = col("user id", "userid", "user_id", "pin", "emp no", "emp_no", "ac-no", "ac no", "enroll no", "id")
    stamp = col("time", "datetime", "date time", "punch time", "checktime", "timestamp")
    day, clock = col("date"), col("clock", "punch", "time in")
    state = col("status", "state", "check type", "in/out")
    if not uid or not (stamp or (day and clock)):
        return [], ["The file needs a user ID column and a time column (or date and time columns)."]
    punches, problems = [], []
    for n, row in enumerate(reader, start=2):
        pin = str(row.get(uid) or "").strip()
        when = parse_time(row.get(stamp) or "") if stamp else parse_time(f"{row.get(day) or ''} {row.get(clock) or ''}")
        if not pin or not when:
            problems.append(f"row {n}: no user ID or unreadable time")
            continue
        punches.append((pin, when, str(row.get(state) or "").strip() if state else ""))
    return punches, problems


def record(db: Session, machine: Optional[AttendanceMachine], punches: Iterable[tuple[str, datetime, str]],
           source: str = "push") -> dict:
    """Store punches (duplicates ignored), then rebuild the attendance of every employee-day touched."""
    employees = {e.attendance_machine_id: e for e in
                 db.query(Employee).filter(Employee.attendance_machine_id.isnot(None)).all()}
    added = duplicates = unmatched = 0
    newest: Optional[datetime] = None
    touched: set[tuple[int, date]] = set()
    for pin, when, status in punches:
        q = db.query(AttendancePunch.id).filter(AttendancePunch.machine_user_id == pin, AttendancePunch.punched_at == when)
        q = q.filter(AttendancePunch.machine_id == machine.id) if machine else q.filter(AttendancePunch.machine_id.is_(None))
        if q.first():
            duplicates += 1
            continue
        emp = employees.get(pin)
        db.add(AttendancePunch(machine_id=machine.id if machine else None, machine_user_id=pin[:20],
                               employee_id=emp.id if emp else None, punched_at=when, punch_state=(status or "")[:10],
                               source=source))
        added += 1
        newest = when if newest is None or when > newest else newest
        if emp:
            touched.add((emp.id, when.date()))
        else:
            unmatched += 1
    if machine:
        machine.last_seen_at = datetime.utcnow()
        if newest is not None:
            machine.last_punch_at = max(newest, machine.last_punch_at) if machine.last_punch_at else newest
            machine.punches_total = (machine.punches_total or 0) + added
    db.flush()
    rebuilt = sum(1 for emp_id, day in touched if rebuild_day(db, emp_id, day))
    return {"added": added, "duplicates": duplicates, "unmatched": unmatched, "days": rebuilt}


def rebuild_day(db: Session, employee_id: int, day: date) -> bool:
    """Earliest punch = check-in, latest = check-out. Leaves a day HR corrected or marked leave/holiday alone."""
    from app.services import hr as hr_svc

    emp = db.get(Employee, employee_id)
    if not emp:
        return False
    lo, hi = datetime.combine(day, datetime.min.time()), datetime.combine(day + timedelta(days=1), datetime.min.time())
    times = sorted(t for (t,) in db.query(AttendancePunch.punched_at).filter(
        AttendancePunch.employee_id == employee_id, AttendancePunch.punched_at >= lo, AttendancePunch.punched_at < hi))
    if not times:
        return False
    rows = db.query(HRAttendance).filter(HRAttendance.employee_id == employee_id, HRAttendance.date == day).all()
    if any(r.correction_status == "approved" or r.status in ("leave", "holiday") for r in rows):
        return False
    first, last = times[0], times[-1]
    session = hr_svc.current_session(first, emp)
    row = next((r for r in rows if r.session == session), None) or (rows[0] if rows else None)
    if row is None:
        row = HRAttendance(employee_id=employee_id, date=day, session=session, status="present")
        db.add(row)
    row.check_in = first
    row.check_out = last if (last - first) >= timedelta(minutes=1) else None
    if row.status not in hr_svc.WORKED_STATUSES:
        row.status = "present"
    row.ip = "biometric"
    hr_svc.recompute_late_minutes(emp, row)
    db.flush()
    return True


def import_file(db: Session, machine: Optional[AttendanceMachine], filename: str, content: bytes,
                user: Optional[User]) -> dict:
    text = content.decode("utf-8-sig", errors="replace")
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    if "\t" in first_line and "," not in first_line:
        punches, problems = parse_attlog(text), []
    else:
        punches, problems = parse_csv(text)
    result = record(db, machine, punches, source="file")
    result["problems"] = problems[:20]
    result["read"] = len(punches)
    return result
