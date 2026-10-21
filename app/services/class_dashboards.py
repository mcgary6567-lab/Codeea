"""Class dashboards the old ERP had and OQC lacked (Fusion gap report, 21 October 2026).

- Class Management dashboard (``/class-management-dashboard`` in the ERP)
- Supervisors Management dashboard (``/supervisor-wise-dashboard``)
- Daily Performance dashboard (``/daily-performance-dashboard``)
- Student Attendance Monitoring (``/student-attendance-monitoring``)

Like ``app/services/dashboards.py`` every function is pure: a Session plus filter values in, a plain dict out, so
the routes only render and the tests call these directly. Date filters on DateTime columns use a timestamp range
(never a date-to-string comparison, which PostgreSQL refuses).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.academic import Course
from app.models.core import User
from app.models.crm import Case, Lead
from app.models.finance import Invoice, Payment
from app.models.people import Client, Employee, HRAttendance, Student, Teacher
from app.models.scheduling import ClassSession, QAReview, Trial
from app.services.dashboards import _base_code, _chart, _pct, _rates, _shift_of, _to_base

STATUS_LABELS = {
    "pending": "Pending", "available": "Teacher Available", "started": "Started", "done": "Done",
    "missed": "Missed", "absent": "Student Absent", "leave": "Student On Leave", "cancelled": "Cancelled",
    "rescheduled": "Rescheduled", "free": "Free",
}
# Rates count only classes with an outcome: still-pending, cancelled, moved or freed classes are left out.
DECIDED = ("done", "missed", "absent", "leave")
SHORT_MINUTES = 20
LATE_MINUTES = 5
DAILY_TABS = [("all", "Show All"), ("academics", "Academics"), ("marketing", "Marketing"),
              ("billing", "Billing"), ("hr", "HR")]


def _window(date_from: Optional[date], date_to: Optional[date]) -> tuple[date, date]:
    today = date.today()
    end = date_to or today
    start = date_from or date(end.year, end.month, 1)
    if start > end:
        start, end = end, start
    return start, end


def _day_range(start: date, end: date) -> tuple[datetime, datetime]:
    return datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)


def _supervisor_teacher_ids(db: Session, supervisor_id: Optional[int]) -> Optional[set[int]]:
    if not supervisor_id:
        return None
    return {t.id for t in db.query(Teacher).filter(Teacher.supervisor_id == supervisor_id).all()}


def _sessions(db: Session, start: date, end: date, teacher_id: Optional[int] = None,
              teacher_ids: Optional[set[int]] = None, course_id: Optional[int] = None) -> list[ClassSession]:
    q = db.query(ClassSession).filter(ClassSession.date >= start, ClassSession.date <= end)
    if teacher_id:
        q = q.filter(ClassSession.teacher_id == teacher_id)
    if teacher_ids is not None:
        q = q.filter(ClassSession.teacher_id.in_(list(teacher_ids) or [-1]))
    if course_id:
        q = q.filter(ClassSession.course_id == course_id)
    return q.order_by(ClassSession.date, ClassSession.start_time).all()


def _rates_of(rows: list[ClassSession]) -> dict:
    """Shared class figures: counts per status plus completion, attendance, late and short."""
    c = Counter(s.status or "pending" for s in rows)
    due = sum(c[st] for st in DECIDED)
    held = c["done"]
    done_rows = [s for s in rows if s.status == "done"]
    late = sum(1 for s in done_rows if (s.teacher_late_minutes or 0) > LATE_MINUTES)
    short = sum(1 for s in done_rows if s.actual_duration_minutes is not None
                and s.actual_duration_minutes <= SHORT_MINUTES)
    avg_late = round(sum(max(0, s.teacher_late_minutes or 0) for s in done_rows) / len(done_rows), 1) if done_rows else 0.0
    return {
        "total": len(rows), "due": due, **{k: c.get(k, 0) for k in STATUS_LABELS},
        "completion": _pct(held, due), "attendance": _pct(held, held + c["absent"]),
        "late": late, "short": short, "avg_late": avg_late, "trial": sum(1 for s in rows if s.is_trial),
    }


def options(db: Session) -> dict:
    teachers = [(t.id, f"{t.teacher_code} - {t.full_name}") for t in db.query(Teacher).order_by(Teacher.teacher_code)]
    sup_ids = {t.supervisor_id for t in db.query(Teacher).all() if t.supervisor_id}
    supervisors = [(u.id, u.full_name) for u in
                   db.query(User).filter(User.id.in_(sup_ids or {-1})).order_by(User.full_name).all()]
    courses = [(c.id, c.name) for c in db.query(Course).order_by(Course.name).all()]
    return {"teachers": teachers, "supervisors": supervisors, "courses": courses,
            "shifts": [("morning", "Morning"), ("night", "Night")], "daily_tabs": DAILY_TABS}


# ============================================================================ Class Management dashboard
def class_management(db: Session, date_from: Optional[date] = None, date_to: Optional[date] = None,
                     teacher_id: Optional[int] = None, supervisor_id: Optional[int] = None,
                     course_id: Optional[int] = None, shift: str = "") -> dict:
    start, end = _window(date_from, date_to)
    rows = _sessions(db, start, end, teacher_id=teacher_id, teacher_ids=_supervisor_teacher_ids(db, supervisor_id),
                     course_id=course_id)
    students = {s.id: s for s in db.query(Student).filter(Student.id.in_({r.student_id for r in rows} or {-1}))}
    clients = {c.id: c for c in db.query(Client).filter(
        Client.id.in_({s.client_id for s in students.values()} or {-1}))}
    if shift:
        def shift_of(r):
            st = students.get(r.student_id)
            return _shift_of(clients.get(st.client_id) if st else None)
        rows = [r for r in rows if shift_of(r) == shift]
    teachers = {t.id: t for t in db.query(Teacher).all()}
    users = {u.id: u.full_name for u in db.query(User).all()}
    courses = {c.id: c.name for c in db.query(Course).all()}

    per_teacher: dict[int, list] = defaultdict(list)
    for r in rows:
        per_teacher[r.teacher_id].append(r)
    table = []
    for tid, items in per_teacher.items():
        t = teachers.get(tid)
        table.append({"teacher_id": tid, "teacher": f"{t.teacher_code} - {t.full_name}" if t else "—",
                      "supervisor": users.get(t.supervisor_id, "—") if t else "—", **_rates_of(items)})
    table.sort(key=lambda x: (-x["missed"], x["completion"], x["teacher"]))

    days = []
    cur = start
    while cur <= end and len(days) < 62:
        days.append(cur)
        cur += timedelta(days=1)
    by_day = {d: Counter() for d in days}
    for r in rows:
        if r.date in by_day:
            by_day[r.date][r.status] += 1

    def teacher_series(status: str) -> dict:
        return _chart({row["teacher"].split(" - ", 1)[-1]: row[status] for row in table if row[status]})

    return {
        "filters": {"date_from": start, "date_to": end},
        "tiles": _rates_of(rows),
        "charts": {
            "status": _chart({STATUS_LABELS.get(k, k): v for k, v in Counter(r.status for r in rows).items()}),
            "days": {"labels": [d.strftime("%d %b") for d in days],
                     "done": [by_day[d]["done"] for d in days], "missed": [by_day[d]["missed"] for d in days],
                     "absent": [by_day[d]["absent"] for d in days], "leave": [by_day[d]["leave"] for d in days]},
            "teacher_done": teacher_series("done"), "teacher_missed": teacher_series("missed"),
            "course": _chart(Counter(courses.get(r.course_id, "No course") for r in rows)),
            "supervisor": _chart(Counter(
                users.get(teachers[r.teacher_id].supervisor_id, "No supervisor")
                if r.teacher_id in teachers else "No supervisor" for r in rows)),
            "hour": _chart(Counter(f"{r.start_time.hour:02d}:00" for r in rows if r.start_time), sort=False, limit=0),
        },
        "teachers": table,
    }


# ============================================================================ Supervisors Management dashboard
def supervisors_dashboard(db: Session, date_from: Optional[date] = None, date_to: Optional[date] = None,
                          supervisor_id: Optional[int] = None) -> dict:
    start, end = _window(date_from, date_to)
    lo, hi = _day_range(start, end)
    teachers = db.query(Teacher).all()
    users = {u.id: u.full_name for u in db.query(User).all()}
    groups: dict[Optional[int], list[Teacher]] = defaultdict(list)
    for t in teachers:
        groups[t.supervisor_id].append(t)
    if supervisor_id:
        groups = {supervisor_id: groups.get(supervisor_id, [])}

    sessions = _sessions(db, start, end)
    by_teacher: dict[int, list] = defaultdict(list)
    for s in sessions:
        by_teacher[s.teacher_id].append(s)
    active_students = Counter(s.teacher_id for s in db.query(Student).filter(Student.status.in_(("active", "regular"))))
    reviews = db.query(QAReview).filter(QAReview.reviewed_at >= lo, QAReview.reviewed_at < hi).all()
    complaints = db.query(Case).filter(Case.case_type == "complaint", Case.created_at >= lo,
                                       Case.created_at < hi).all()

    rows = []
    for sup_id, items in groups.items():
        ids = {t.id for t in items}
        cls = [s for tid in ids for s in by_teacher.get(tid, [])]
        scores = [float(r.overall_rating or r.overall_score or 0) for r in reviews
                  if r.teacher_id in ids and (r.overall_rating or r.overall_score)]
        rows.append({
            "supervisor_id": sup_id, "supervisor": users.get(sup_id, "No supervisor") if sup_id else "No supervisor",
            "teachers": len(items), "active_teachers": sum(1 for t in items if t.status == "active"),
            "verified": sum(1 for t in items if t.is_verified),
            "students": sum(active_students.get(tid, 0) for tid in ids),
            "qa_reviews": len(scores), "qa_avg": round(sum(scores) / len(scores), 2) if scores else None,
            "complaints": sum(1 for c in complaints if c.teacher_id in ids),
            "open_complaints": sum(1 for c in complaints if c.teacher_id in ids and c.status not in ("resolved", "closed")),
            **_rates_of(cls),
        })
    rows.sort(key=lambda r: (r["supervisor_id"] is None, r["supervisor"]))
    named = [r for r in rows if r["supervisor_id"]]
    all_cls = [s for s in sessions if supervisor_id is None or s.teacher_id in {t.id for t in groups.get(supervisor_id, [])}]
    totals = _rates_of(all_cls)
    return {
        "filters": {"date_from": start, "date_to": end},
        "tiles": {"supervisors": len(named), "teachers": sum(r["teachers"] for r in rows),
                  "students": sum(r["students"] for r in rows), "completion": totals["completion"],
                  "missed": totals["missed"], "complaints": sum(r["complaints"] for r in rows)},
        "rows": rows,
        "charts": {
            "done": _chart({r["supervisor"]: r["done"] for r in rows}),
            "missed": _chart({r["supervisor"]: r["missed"] for r in rows}),
            "completion": {"labels": [r["supervisor"] for r in rows], "data": [r["completion"] for r in rows]},
            "students": _chart({r["supervisor"]: r["students"] for r in rows}),
        },
    }


# ============================================================================ Daily Performance dashboard
def _day_figures(db: Session, day: date, rates: dict) -> dict:
    lo, hi = _day_range(day, day)
    sessions = _sessions(db, day, day)
    leads = db.query(Lead).filter(Lead.created_at >= lo, Lead.created_at < hi).count()
    converted = db.query(Lead).filter(Lead.converted_at >= lo, Lead.converted_at < hi).count()
    trials = db.query(Trial).filter(Trial.scheduled_at >= lo, Trial.scheduled_at < hi).all()
    invoices = [i for i in db.query(Invoice).filter(Invoice.issue_date == day).all() if i.status != "cancelled"]
    payments = [p for p in db.query(Payment).filter(Payment.received_at >= lo, Payment.received_at < hi).all()
                if p.status not in ("failed", "cancelled", "refunded")]
    att = db.query(HRAttendance).filter(HRAttendance.date == day).all()
    by_emp: dict[int, set] = defaultdict(set)
    for a in att:
        by_emp[a.employee_id].add(a.status)

    def staff(status: str) -> int:
        return sum(1 for statuses in by_emp.values() if status in statuses)

    return {
        "classes": _rates_of(sessions),
        "marketing": {"leads": leads, "converted": converted, "trials": len(trials),
                      "trials_attended": sum(1 for t in trials if t.status in ("attended", "converted"))},
        "billing": {"invoices": len(invoices),
                    "invoiced": round(sum(float(i.total_in_base or 0) or _to_base(rates, i.total, i.currency)
                                          for i in invoices), 2),
                    "receipts": len(payments),
                    "received": round(sum(float(p.amount_in_base or 0) or _to_base(rates, p.amount, p.currency)
                                          for p in payments), 2)},
        "hr": {"marked": len(by_emp), "present": staff("present"), "late": staff("late"), "absent": staff("absent"),
               "leave": staff("leave"),
               "staff": db.query(Employee).filter(Employee.status.in_(("active", "probation"))).count()},
        "sessions": sessions,
    }


def daily_performance(db: Session, day: Optional[date] = None, department: str = "all") -> dict:
    day = day or date.today()
    department = department if department in {k for k, _ in DAILY_TABS} else "all"
    rates = _rates(db)
    today = _day_figures(db, day, rates)
    before = _day_figures(db, day - timedelta(days=1), rates)

    def delta(now: float, then: float) -> Optional[float]:
        return round(100.0 * (now - then) / then, 1) if then else None

    teachers = {t.id: t for t in db.query(Teacher).all()}
    per_teacher: dict[int, list] = defaultdict(list)
    for s in today["sessions"]:
        per_teacher[s.teacher_id].append(s)
    teacher_rows = []
    for tid, items in per_teacher.items():
        t = teachers.get(tid)
        r = _rates_of(items)
        first = min((s.teacher_joined_at for s in items if s.teacher_joined_at), default=None)
        teacher_rows.append({"teacher_id": tid, "teacher": f"{t.teacher_code} - {t.full_name}" if t else "—",
                             "first_join": first, **r})
    teacher_rows.sort(key=lambda x: (-x["missed"], -x["late"], x["teacher"]))
    today.pop("sessions")
    before.pop("sessions")
    return {
        "day": day, "department": department, "base": _base_code(db), "today": today, "before": before,
        "delta": {
            "done": delta(today["classes"]["done"], before["classes"]["done"]),
            "missed": delta(today["classes"]["missed"], before["classes"]["missed"]),
            "leads": delta(today["marketing"]["leads"], before["marketing"]["leads"]),
            "received": delta(today["billing"]["received"], before["billing"]["received"]),
            "present": delta(today["hr"]["present"], before["hr"]["present"]),
        },
        "teachers": teacher_rows,
        "chart": {"labels": [STATUS_LABELS[k] for k in STATUS_LABELS if today["classes"][k]],
                  "data": [today["classes"][k] for k in STATUS_LABELS if today["classes"][k]]},
    }


# ============================================================================ Student Attendance Monitoring
def student_attendance(db: Session, date_from: Optional[date] = None, date_to: Optional[date] = None,
                       teacher_id: Optional[int] = None, supervisor_id: Optional[int] = None,
                       threshold: int = 75, flagged_only: bool = False, q: str = "",
                       scope: Optional[list[int]] = None) -> dict:
    """One row per student with classes in the window: present, absent, on leave, missed by the teacher, the
    attendance rate, the current run of absences and the last class attended. A student is flagged when the
    rate is under ``threshold`` or the latest three due classes were all absences. ``scope`` limits the rows to
    those teachers (``scheduling.scoped_teacher_ids``)."""
    start, end = _window(date_from or (date.today() - timedelta(days=29)), date_to)
    teacher_ids = _supervisor_teacher_ids(db, supervisor_id)
    if scope is not None:  # a supervisor or teacher sees only their own teachers' students
        teacher_ids = set(scope) if teacher_ids is None else teacher_ids & set(scope)
    rows = _sessions(db, start, end, teacher_id=teacher_id, teacher_ids=teacher_ids)
    by_student: dict[int, list] = defaultdict(list)
    for s in rows:
        by_student[s.student_id].append(s)
    students = {s.id: s for s in db.query(Student).filter(Student.id.in_(set(by_student) or {-1})).all()}
    clients = {c.id: c for c in db.query(Client).filter(
        Client.id.in_({s.client_id for s in students.values()} or {-1})).all()}
    teachers = {t.id: t for t in db.query(Teacher).all()}
    needle = (q or "").strip().lower()

    out = []
    for sid, items in by_student.items():
        st = students.get(sid)
        if not st:
            continue
        client = clients.get(st.client_id)
        if needle and needle not in f"{st.student_code} {st.full_name} {client.full_name if client else ''}".lower():
            continue
        c = Counter(s.status for s in items)
        present, absent = c["done"], c["absent"]
        streak = 0
        for s in sorted(items, key=lambda x: (x.date, x.start_time), reverse=True):
            if s.status in ("absent",):
                streak += 1
            elif s.status in ("done", "leave"):
                break
        last = max((s.date for s in items if s.status == "done"), default=None)
        rate = _pct(present, present + absent)
        flagged = (present + absent > 0 and rate < threshold) or streak >= 3
        if flagged_only and not flagged:
            continue
        t = teachers.get(st.teacher_id) if st.teacher_id else None
        out.append({"student": st, "client": client, "teacher": t, "scheduled": len(items),
                    "present": present, "absent": absent, "leave": c["leave"], "missed": c["missed"],
                    "cancelled": c["cancelled"] + c["rescheduled"], "rate": rate, "streak": streak,
                    "last_attended": last, "flagged": flagged})
    out.sort(key=lambda r: (not r["flagged"], r["rate"], -r["streak"], r["student"].full_name))
    marked = sum(r["present"] + r["absent"] for r in out)
    return {
        "filters": {"date_from": start, "date_to": end, "threshold": threshold},
        "rows": out,
        "tiles": {"students": len(out), "flagged": sum(1 for r in out if r["flagged"]),
                  "present": sum(r["present"] for r in out), "absent": sum(r["absent"] for r in out),
                  "leave": sum(r["leave"] for r in out),
                  "rate": _pct(sum(r["present"] for r in out), marked)},
    }
