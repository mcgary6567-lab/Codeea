"""A student's academic journey, from enrolment to today (docs/STUDENT_JOURNEY.md).

One place answers "what happened with this student" for any period: which teacher and class time they had and why it
changed, what was taught and completed, attendance, absences, holidays and late arrivals, assessments with the
evidence behind them, teacher recommendations and what they set in motion, parent contacts and complaints.

* Teacher and class-time history lives in ``TeacherAssignment`` rows that are never overwritten. A student without
  rows yet is reconstructed once from the teacher-match history (``ensure_history``).
* A teacher recommendation creates the follow-up itself (``recommend``): a task for the right person, an alert when
  it is urgent, an escalation when the same concern repeats, and an automation event for configured workflows.
* The monthly summary (``monthly_summary`` / ``summary_board``) gives the Academy Manager one view per student.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.notify import notify
from app.models.academic import (ANSWER_RESULTS, RECOMMENDATION_KINDS, TEACHER_CHANGE_REASONS, AssessmentAnswer, Evaluation,
                                 LessonPlan, MonthlyTest, StudentProgress, StudentRecommendation, TeacherAssignment)
from app.models.core import AuditEvent, RiskAlert, Role, User
from app.models.crm import Case, Feedback, ParentContact
from app.models.erp import ClassActivity, QuestionBankItem
from app.models.hr_erp import Holiday
from app.models.ops import Task
from app.models.people import Leave, Student, Teacher
from app.models.scheduling import Attendance, ClassSession, Schedule, TeacherMatch
from app.services import academic as acad

REASON_LABELS = dict(TEACHER_CHANGE_REASONS)
KIND_LABELS = dict(RECOMMENDATION_KINDS)
RESULT_LABELS = dict(ANSWER_RESULTS)
CHANGE_LABELS = {"enrolment": "Enrolled", "teacher_change": "Teacher changed", "time_change": "Class time changed",
                 "recorded": "Teacher (from earlier records)"}
DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LATE_GRACE_MINUTES = 5
NOT_HELD = ("cancelled", "rescheduled")


def org_today() -> date:
    from app.services.people import org_now
    return org_now().date()


# ============================================================================ periods
PERIOD_CHOICES = [("current", "Current month"), ("previous", "Previous month"), ("month", "Specific month"),
                  ("range", "Date range"), ("all", "Entire history")]


def month_bounds(period: str) -> tuple[date, date]:
    y, m = int(period[:4]), int(period[5:7])
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def resolve_period(db: Session, student: Student, period: str = "current", month: str = "", dfrom: Optional[date] = None,
                   dto: Optional[date] = None) -> dict:
    """The date window a view covers, with a label. Unknown input falls back to the current month."""
    today = org_today()
    if period == "previous":
        first = today.replace(day=1) - timedelta(days=1)
        start, end = month_bounds(first.strftime("%Y-%m"))
    elif period == "month" and len(month or "") == 7:
        try:
            start, end = month_bounds(month)
        except ValueError:
            start, end = month_bounds(today.strftime("%Y-%m"))
    elif period == "range" and dfrom and dto:
        start, end = min(dfrom, dto), max(dfrom, dto)
    elif period == "all":
        start, end = (student.join_date or today), today
        first_class = db.query(func.min(ClassSession.date)).filter(ClassSession.student_id == student.id).scalar()
        if first_class and first_class < start:
            start = first_class
    else:
        period = "current"
        start, end = month_bounds(today.strftime("%Y-%m"))
    if period in ("current", "previous", "month"):
        label = start.strftime("%B %Y")
    elif period == "all":
        label = f"Entire history ({start:%d %b %Y} to today)"
    else:
        label = f"{start:%d %b %Y} to {end:%d %b %Y}"
    return {"period": period, "start": start, "end": end, "label": label, "month": start.strftime("%Y-%m")}


# ============================================================================ teacher and class-time history
def _current_slot(db: Session, student: Student) -> tuple[Optional[str], list]:
    sch = (db.query(Schedule).filter(Schedule.student_id == student.id, Schedule.status == "active")
           .order_by(Schedule.start_date.desc(), Schedule.id.desc()).first())
    if sch is None:
        return None, []
    return (sch.start_time.strftime("%H:%M") if sch.start_time else None), list(sch.days_of_week or [])


def open_assignment(db: Session, student_id: int) -> Optional[TeacherAssignment]:
    return (db.query(TeacherAssignment).filter(TeacherAssignment.student_id == student_id, TeacherAssignment.end_date.is_(None))
            .order_by(TeacherAssignment.start_date.desc(), TeacherAssignment.id.desc()).first())


_UNSET = object()


def ensure_history(db: Session, student: Student, current_teacher_id=_UNSET) -> None:
    """Reconstruct a student's teacher history once, from the teacher-match decisions recorded at assignment time.

    ``current_teacher_id`` is the teacher the student had *before* a change being recorded now; pass it when the
    caller has already overwritten ``student.teacher_id``, so the reconstruction ends with the right teacher."""
    if db.query(TeacherAssignment.id).filter(TeacherAssignment.student_id == student.id).first() is not None:
        return
    current = student.teacher_id if current_teacher_id is _UNSET else current_teacher_id
    matches = (db.query(TeacherMatch).filter(TeacherMatch.student_id == student.id, TeacherMatch.chosen_teacher_id.isnot(None))
               .order_by(TeacherMatch.created_at, TeacherMatch.id).all())
    periods: list[tuple[int, date, Optional[str]]] = []
    for m in matches:
        if periods and periods[-1][0] == m.chosen_teacher_id:
            continue
        start = (m.created_at.date() if m.created_at else student.join_date) if periods else (student.join_date or m.created_at.date())
        periods.append((m.chosen_teacher_id, start, m.override_reason or m.match_reason))
    if current_teacher_id is not _UNSET:
        # the change being recorded may already have a match row; keep only the history up to the previous teacher
        while periods and periods[-1][0] != current:
            periods.pop()
    if not periods and current:
        periods.append((current, student.join_date or org_today(), None))
    if not periods:
        return
    if periods[-1][0] != current and current:
        periods.append((current, org_today(), None))
    class_time, days = _current_slot(db, student)
    prev: Optional[TeacherAssignment] = None
    for i, (teacher_id, start, reason) in enumerate(periods):
        last = i == len(periods) - 1
        row = TeacherAssignment(student_id=student.id, teacher_id=teacher_id, start_date=start,
                                class_time=class_time if last else None, days=days if last else [],
                                previous_teacher_id=prev.teacher_id if prev else None,
                                change_type="enrolment" if i == 0 else "recorded", reason=reason)
        if prev is not None:
            prev.end_date = max(prev.start_date, start)
        db.add(row)
        prev = row
    db.flush()


def record_assignment(db: Session, student: Student, teacher_id: Optional[int], user: Optional[User], change_type: str,
                      reason_category: Optional[str] = None, reason: Optional[str] = None, when: Optional[date] = None,
                      class_time: Optional[str] = None, days: Optional[list] = None,
                      previous_teacher_id=_UNSET) -> Optional[TeacherAssignment]:
    """Close the open period and start a new one. Nothing is overwritten. Returns the new row, or None when nothing
    changed. ``class_time``/``days`` default to the student's active schedule. Pass ``previous_teacher_id`` when the
    caller has already changed ``student.teacher_id``."""
    ensure_history(db, student, previous_teacher_id)
    when = when or org_today()
    if class_time is None and days is None:
        class_time, days = _current_slot(db, student)
    days = list(days or [])
    current = open_assignment(db, student.id)
    if current is not None and current.teacher_id == teacher_id and (current.class_time or None) == (class_time or None) \
            and sorted(current.days or []) == sorted(days):
        return None
    if reason_category and reason_category not in REASON_LABELS:
        reason_category = "other"
    if current is not None:
        current.end_date = max(current.start_date, when)
    row = TeacherAssignment(student_id=student.id, teacher_id=teacher_id, start_date=when, class_time=class_time, days=days,
                            previous_teacher_id=current.teacher_id if current else None,
                            previous_time=current.class_time if current else None,
                            previous_days=list(current.days or []) if current else [],
                            change_type=change_type if current or change_type != "teacher_change" else "enrolment",
                            reason_category=reason_category, reason=(reason or "").strip() or None,
                            changed_by_id=user.id if user else None)
    db.add(row)
    db.flush()
    return row


def history(db: Session, student: Student) -> list[TeacherAssignment]:
    ensure_history(db, student)
    return (db.query(TeacherAssignment).filter(TeacherAssignment.student_id == student.id)
            .order_by(TeacherAssignment.start_date.desc(), TeacherAssignment.id.desc()).all())


def days_label(days: Iterable) -> str:
    return ", ".join(DAY_SHORT[int(d)] for d in sorted(set(int(x) for x in (days or []))) if 0 <= int(d) < 7)


def time_label(hhmm: Optional[str]) -> str:
    if not hhmm:
        return "—"
    try:
        return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return hhmm


# ============================================================================ period figures
def _sessions(db: Session, student_id: int, start: date, end: date) -> list[ClassSession]:
    return (db.query(ClassSession).filter(ClassSession.student_id == student_id, ClassSession.date >= start, ClassSession.date <= end)
            .order_by(ClassSession.scheduled_start).all())


def _student_late(cs: ClassSession, att: Optional[Attendance]) -> bool:
    if att is not None and att.student_status == "late":
        return True
    return bool(cs.student_joined_at and cs.scheduled_start
                and cs.student_joined_at - cs.scheduled_start > timedelta(minutes=LATE_GRACE_MINUTES))


def holidays_in(db: Session, start: date, end: date) -> list[tuple[date, str]]:
    out: list[tuple[date, str]] = []
    for h in db.query(Holiday).filter(Holiday.status == "active", Holiday.shift_group == "all", Holiday.holiday_date <= end,
                                      or_(Holiday.end_date.is_(None), Holiday.end_date >= start), Holiday.holiday_date >= start - timedelta(days=60)):
        d = max(h.holiday_date, start)
        while d <= min(h.last_day, end):
            out.append((d, h.name))
            d += timedelta(days=1)
    return sorted(out)


def period_stats(db: Session, student: Student, start: date, end: date) -> dict:
    today = org_today()
    sessions = _sessions(db, student.id, start, end)
    att = {a.session_id: a for a in db.query(Attendance).filter(Attendance.session_id.in_([s.id for s in sessions] or [-1]))}
    held = [s for s in sessions if s.status not in NOT_HELD and s.date <= today]
    attended = [s for s in held if s.status == "done" and not (att.get(s.id) and att[s.id].student_status == "absent")]
    absent = [s for s in held if s.status == "absent" or (att.get(s.id) and att[s.id].student_status == "absent")]
    late = [s for s in attended if _student_late(s, att.get(s.id))]
    leave_days = sum(((min(lv.end_date, end) - max(lv.start_date, start)).days + 1)
                     for lv in db.query(Leave).filter(Leave.person_type == "student", Leave.student_id == student.id,
                                                      Leave.status == "approved", Leave.start_date <= end, Leave.end_date >= start))
    marked = len(attended) + len(absent)
    return {
        "scheduled": sum(1 for s in sessions if s.status not in NOT_HELD),
        "upcoming": sum(1 for s in sessions if s.status == "pending" and s.date > today),
        "held": len(held), "attended": len(attended), "absent": len(absent),
        "on_leave": sum(1 for s in held if s.status == "leave"), "leave_days": leave_days,
        "teacher_missed": sum(1 for s in held if s.status == "missed"),
        "cancelled": sum(1 for s in sessions if s.status in NOT_HELD),
        "late": len(late), "teacher_late": sum(1 for s in attended if (s.teacher_late_minutes or 0) > LATE_GRACE_MINUTES),
        "holidays": len(holidays_in(db, start, end)),
        "attendance_pct": round(100.0 * len(attended) / marked, 1) if marked else None,
    }


# ============================================================================ syllabus
def syllabus(db: Session, student: Student, start: date, end: date) -> dict:
    """What was taught and completed in the window, by whom, and where the student stands overall."""
    s_dt, e_dt = datetime.combine(start, time.min), datetime.combine(end, time.max)
    summary = acad.student_progress_summary(db, student)
    completed = (db.query(StudentProgress).filter(StudentProgress.student_id == student.id, StudentProgress.status == "completed",
                                                  StudentProgress.completed_at >= s_dt, StudentProgress.completed_at <= e_dt)
                 .order_by(StudentProgress.completed_at).all())
    started = (db.query(StudentProgress).filter(StudentProgress.student_id == student.id, StudentProgress.started_at >= s_dt,
                                                StudentProgress.started_at <= e_dt).count())
    teachers = {t.id: t.full_name for t in db.query(Teacher)}
    by_teacher: dict[str, int] = {}
    for p in completed:
        name = teachers.get(p.teacher_id, "Not recorded")
        by_teacher[name] = by_teacher.get(name, 0) + 1
    taught = taught_entries(db, student.id, start, end)
    course = student.course
    expected = None
    if course and course.completion_target_months and student.join_date:
        months = max(0.0, (min(end, org_today()) - student.join_date).days / 30.4)
        expected = round(min(100.0, 100.0 * months / course.completion_target_months), 1)
    monthly = []
    first = start.replace(day=1)
    months_back = max(1, min(12, (end.year - first.year) * 12 + end.month - first.month + 1))
    cursor = end.replace(day=1)
    for _ in range(months_back):
        ms, me = month_bounds(cursor.strftime("%Y-%m"))
        n = (db.query(func.count(StudentProgress.id)).filter(StudentProgress.student_id == student.id, StudentProgress.status == "completed",
                                                             StudentProgress.completed_at >= datetime.combine(ms, time.min),
                                                             StudentProgress.completed_at <= datetime.combine(me, time.max)).scalar() or 0)
        monthly.append((cursor.strftime("%b %Y"), n))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    monthly.reverse()
    return {
        "completed": [{"lesson": acad.lesson_path(p.lesson), "date": p.completed_at, "teacher": teachers.get(p.teacher_id, "—"),
                       "type": p.progress_type, "score": p.score} for p in completed],
        "started": started, "by_teacher": sorted(by_teacher.items(), key=lambda x: -x[1]), "taught": taught,
        "overall_pct": summary["pct"], "total": summary["total"], "done_total": summary["completed"],
        "remaining": max(0, summary["total"] - summary["completed"]),
        "stage": stage_label(student, summary), "expected_pct": expected,
        "behind": expected is not None and summary["pct"] + 15 < expected,
        "monthly": monthly,
    }


def stage_label(student: Student, summary: Optional[dict] = None) -> str:
    parts = [student.course.name if student.course else None, student.level,
             (summary or {}).get("current_lesson_title") if summary and summary.get("current_lesson") else None,
             student.sabaq_position]
    return " · ".join(p for p in parts if p and p != "—") or "Not recorded"


def taught_entries(db: Session, student_id: int, start: date, end: date) -> list[dict]:
    """What was taught on each class date: the class activity (book and page), the delivered lesson plan, the lesson
    linked to the class, and the teacher's notes."""
    sessions = {s.id: s for s in db.query(ClassSession).filter(ClassSession.student_id == student_id, ClassSession.date >= start,
                                                               ClassSession.date <= end, ClassSession.status == "done")}
    ids = list(sessions) or [-1]
    acts: dict[int, list[ClassActivity]] = {}
    for a in db.query(ClassActivity).filter(ClassActivity.session_id.in_(ids)).order_by(ClassActivity.id):
        acts.setdefault(a.session_id, []).append(a)
    plans = {p.session_id: p for p in db.query(LessonPlan).filter(LessonPlan.session_id.in_(ids))}
    teachers = {t.id: t.full_name for t in db.query(Teacher).filter(Teacher.id.in_({s.teacher_id for s in sessions.values()} or {-1}))}
    out = []
    for sid, s in sorted(sessions.items(), key=lambda kv: kv[1].scheduled_start or datetime.min):
        bits = []
        if s.lesson_id:
            bits.append(acad.lesson_path(s.lesson))
        for a in acts.get(sid, []):
            bits.append(" ".join(x for x in [a.book.title if a.book else None, f"page {a.page_no}" if a.page_no else None, a.remarks] if x))
        p = plans.get(sid)
        if p is not None:
            plan_bits = [f"Sabaq: {p.sabaq}" if p.sabaq else None, f"Sabqi: {p.sabqi}" if p.sabqi else None, f"Dor: {p.dor}" if p.dor else None,
                         p.delivered_content]
            bits.extend(b for b in plan_bits if b)
        if s.teacher_notes:
            bits.append(f"Note: {s.teacher_notes}")
        if bits:
            out.append({"date": s.date, "teacher": teachers.get(s.teacher_id, "—"), "text": "; ".join(bits), "session_id": sid})
    return out


# ============================================================================ timeline
TIMELINE_KINDS = [("class", "Classes"), ("progress", "Syllabus"), ("assessment", "Assessments"), ("teacher", "Teacher & time"),
                  ("recommendation", "Recommendations"), ("family", "Family & complaints"), ("status", "Status & leave")]
STYLE = {"class": ("sky", "video"), "progress": ("emerald", "book-open-check"), "assessment": ("indigo", "clipboard-check"),
         "teacher": ("violet", "user-cog"), "recommendation": ("amber", "lightbulb"), "family": ("rose", "phone-call"),
         "status": ("slate", "flag")}
CLASS_WORDS = {"done": "Attended", "absent": "Absent", "missed": "Teacher missed the class", "leave": "On leave",
               "cancelled": "Cancelled", "rescheduled": "Rescheduled", "pending": "Scheduled", "started": "In progress",
               "available": "Teacher available", "free": "Free class"}


def _ev(when, kind: str, title: str, detail: str = "", url: Optional[str] = None, badge: Optional[str] = None) -> dict:
    if isinstance(when, date) and not isinstance(when, datetime):
        when = datetime.combine(when, time(23, 59))
    color, icon = STYLE[kind]
    return {"at": when, "date": when.date(), "kind": kind, "title": title, "detail": detail, "url": url, "badge": badge,
            "color": color, "icon": icon}


def timeline(db: Session, student: Student, start: date, end: date, viewer: User, kinds: Optional[set] = None) -> list[dict]:
    """Everything that happened to the student in the window, newest first."""
    kinds = kinds or {k for k, _ in TIMELINE_KINDS}
    s_dt, e_dt = datetime.combine(start, time.min), datetime.combine(end, time.max)
    ev: list[dict] = []
    if "status" in kinds and student.join_date and start <= student.join_date <= end:
        ev.append(_ev(student.join_date, "status", "Enrolled", f"{student.course.name if student.course else ''} {student.level or ''}".strip()))
    if "teacher" in kinds:
        for a in history(db, student):
            if start <= a.start_date <= end:
                who = a.teacher.full_name if a.teacher else "No teacher"
                if a.change_type == "time_change":
                    title = f"Class time changed: {time_label(a.previous_time)} → {time_label(a.class_time)}"
                elif a.previous_teacher_id:
                    title = f"Teacher changed: {a.previous_teacher.full_name if a.previous_teacher else '—'} → {who}"
                else:
                    title = f"Teacher assigned: {who}"
                bits = [REASON_LABELS.get(a.reason_category or "", ""), a.reason or "",
                        f"time {time_label(a.previous_time)} → {time_label(a.class_time)}" if a.previous_time and a.previous_time != a.class_time and a.change_type != "time_change" else ""]
                ev.append(_ev(a.start_date, "teacher", title, " · ".join(b for b in bits if b)))
    if "class" in kinds:
        sessions = _sessions(db, student.id, start, end)
        att = {x.session_id: x for x in db.query(Attendance).filter(Attendance.session_id.in_([s.id for s in sessions] or [-1]))}
        taught = {t["session_id"]: t["text"] for t in taught_entries(db, student.id, start, end)}
        today = org_today()
        for s in sessions:
            if s.status == "pending" and s.date > today:
                continue
            a = att.get(s.id)
            word = CLASS_WORDS.get(s.status, s.status.title())
            if s.status == "done" and a is not None and a.student_status == "absent":
                word = "Absent"
            late = s.status == "done" and _student_late(s, a)
            title = f"{word}{' (late)' if late else ''} · {s.start_time.strftime('%I:%M %p').lstrip('0') if s.start_time else ''} with {s.teacher.full_name if s.teacher else '—'}"
            detail = taught.get(s.id, "")
            if s.status_reason and s.status != "done":
                detail = (detail + " · " if detail else "") + s.status_reason
            ev.append(_ev(s.scheduled_start or s.date, "class", title, detail, f"/classes/{s.id}",
                          "late" if late else ("done" if word == "Attended" else ("absent" if word == "Absent" else s.status))))
        for d, name in holidays_in(db, start, end):
            ev.append(_ev(d, "status", f"Holiday: {name}"))
    if "status" in kinds:
        for lv in db.query(Leave).filter(Leave.person_type == "student", Leave.student_id == student.id, Leave.start_date <= end,
                                         Leave.end_date >= start, Leave.status.in_(["approved", "pending"])):
            ev.append(_ev(lv.start_date, "status", f"Leave {lv.start_date:%d %b} – {lv.end_date:%d %b} ({lv.status})",
                          f"{lv.leave_type.title()} · {lv.reason or ''}".strip(" ·")))
        for e in db.query(AuditEvent).filter(AuditEvent.entity_type == "Student", AuditEvent.entity_id == student.id,
                                             AuditEvent.action.in_(["status_change"]), AuditEvent.created_at >= s_dt,
                                             AuditEvent.created_at <= e_dt):
            ev.append(_ev(e.created_at, "status", e.description or "Status changed", e.rationale or ""))
    if "progress" in kinds:
        for p in db.query(StudentProgress).filter(StudentProgress.student_id == student.id, StudentProgress.completed_at >= s_dt,
                                                  StudentProgress.completed_at <= e_dt, StudentProgress.status == "completed"):
            ev.append(_ev(p.completed_at, "progress", f"Completed: {acad.lesson_path(p.lesson)}",
                          f"{p.progress_type.title()}" + (f" · score {p.score:g}" if p.score is not None else ""), badge="completed"))
    if "assessment" in kinds:
        for e in db.query(Evaluation).filter(Evaluation.student_id == student.id, Evaluation.date >= start, Evaluation.date <= end):
            n = len(e.answers)
            ev.append(_ev(e.date, "assessment", f"{e.evaluation_type.replace('_', ' ').title()} assessment: {e.score:g}/{e.max_score:g}"
                          if e.score is not None else f"{e.evaluation_type.replace('_', ' ').title()} assessment",
                          " · ".join(x for x in [f"{n} question(s) recorded" if n else "", e.weaknesses or "", e.teacher_comment or ""] if x)[:300],
                          f"/academics/evaluations/{e.id}", e.result))
        for t in db.query(MonthlyTest).filter(MonthlyTest.student_id == student.id, MonthlyTest.period >= start.strftime("%Y-%m"),
                                              MonthlyTest.period <= end.strftime("%Y-%m")):
            ev.append(_ev(month_bounds(t.period)[1], "assessment", f"Monthly test {acad.period_label(t.period)}",
                          f"{t.percentage}% (grade {t.grade})" if t.percentage is not None else "Not scored yet",
                          f"/academics/monthly-tests/{t.id}", t.status))
    if "recommendation" in kinds:
        for r in db.query(StudentRecommendation).filter(StudentRecommendation.student_id == student.id,
                                                        StudentRecommendation.created_at >= s_dt, StudentRecommendation.created_at <= e_dt):
            ev.append(_ev(r.created_at, "recommendation", KIND_LABELS.get(r.kind, r.kind),
                          " · ".join(x for x in [r.note or "", f"by {r.teacher.full_name}" if r.teacher else "",
                                                 f"action: {r.task.title}" if r.task else ""] if x), badge=recommendation_state(r)))
    if "family" in kinds:
        from app.services import complaints as cx
        for c in cx.visible(db, viewer, db.query(Case).filter(or_(Case.student_id == student.id, Case.client_id == student.client_id),
                                                               Case.created_at >= s_dt, Case.created_at <= e_dt)):
            ev.append(_ev(c.created_at, "family", f"{c.case_type.title()} {c.case_number}: {c.title}", cx.status_label(c),
                          f"/cases/{c.id}", c.status))
        for pc in db.query(ParentContact).filter(or_(ParentContact.student_id == student.id, ParentContact.client_id == student.client_id),
                                                 ParentContact.contacted_at >= s_dt, ParentContact.contacted_at <= e_dt):
            if pc.case_id and not cx.can_view(db, viewer, pc.case):
                continue
            ev.append(_ev(pc.contacted_at, "family", f"Family contacted ({pc.channel})",
                          (pc.parent_response or pc.summary or "")[:250], f"/cases/{pc.case_id}" if pc.case_id else None, pc.satisfaction))
        for f in db.query(Feedback).filter(or_(Feedback.student_id == student.id, Feedback.client_id == student.client_id),
                                           Feedback.submitted_at >= s_dt, Feedback.submitted_at <= e_dt):
            ev.append(_ev(f.submitted_at, "family", "Parent feedback" + (f" · NPS {f.nps}" if f.nps is not None else ""),
                          (f.comment or "")[:250], badge=f.sentiment))
    ev.sort(key=lambda x: x["at"], reverse=True)
    return ev


# ============================================================================ assessments with evidence
def assessments(db: Session, student: Student, start: date, end: date) -> list[Evaluation]:
    return (db.query(Evaluation).filter(Evaluation.student_id == student.id, Evaluation.date >= start, Evaluation.date <= end)
            .order_by(Evaluation.date.desc(), Evaluation.id.desc()).all())


def save_evidence(db: Session, ev: Evaluation, form) -> int:
    """Store the question-by-question evidence and the judgement fields posted with an assessment form.

    Rows come as parallel lists ``q_text``, ``q_expected``, ``q_answer``, ``q_result``, ``q_marks``, ``q_max``,
    ``q_id``; a row without a question is ignored. When marks are given the score is recomputed from them."""
    def lst(name):
        return list(form.getlist(name)) if hasattr(form, "getlist") else list(form.get(name) or [])
    texts, expected, answers, results = lst("q_text"), lst("q_expected"), lst("q_answer"), lst("q_result")
    marks, maxes, qids = lst("q_marks"), lst("q_max"), lst("q_id")
    for field in ("stage", "strengths", "weaknesses", "recommendations", "parent_observations", "follow_up"):
        value = (form.get(field) or "").strip()
        if value:
            setattr(ev, field, value)
    if not any((t or "").strip() for t in texts):
        return 0
    for old in list(ev.answers):
        db.delete(old)
    db.flush()

    def num(seq, i):
        try:
            v = (seq[i] or "").strip()
            return float(v) if v != "" else None
        except (IndexError, ValueError):
            return None
    n = 0
    for i, text in enumerate(texts):
        text = (text or "").strip()
        if not text:
            continue
        result = results[i] if i < len(results) and results[i] in RESULT_LABELS else "correct"
        qid = None
        try:
            qid = int(qids[i]) if i < len(qids) and str(qids[i]).isdigit() else None
        except (IndexError, ValueError):
            qid = None
        db.add(AssessmentAnswer(evaluation_id=ev.id, question_id=qid if qid and db.get(QuestionBankItem, qid) else None, order=n,
                                question=text, expected_answer=(expected[i] if i < len(expected) else "") or None,
                                student_answer=(answers[i] if i < len(answers) else "") or None, result=result,
                                marks_awarded=num(marks, i), max_marks=num(maxes, i)))
        n += 1
    db.flush()
    db.refresh(ev)
    awarded = [a.marks_awarded for a in ev.answers if a.marks_awarded is not None and a.max_marks]
    total = sum(a.max_marks for a in ev.answers if a.marks_awarded is not None and a.max_marks)
    if awarded and total:
        ev.score = round(100.0 * sum(awarded) / total * (ev.max_score or 100) / 100.0, 1)
    return n


def answer_summary(ev: Evaluation) -> dict:
    counts = {k: 0 for k in RESULT_LABELS}
    for a in ev.answers:
        counts[a.result] = counts.get(a.result, 0) + 1
    return counts


# ============================================================================ recommendations
# kind -> (who acts, days to act, priority, task title). "owner" is the academic owner (Academy Manager); "teacher" the
# student's teacher. Kinds without an owner are recorded on the timeline only.
RECOMMENDATION_ACTIONS = {
    "late_arrivals": ("owner", 3, "medium", "Talk to the family about arriving on time"),
    "needs_revision": ("teacher", 7, "medium", "Plan extra revision"),
    "attendance": ("owner", 2, "high", "Attendance follow-up with the family"),
    "parental_support": ("owner", 3, "medium", "Ask the family for more support at home"),
    "progressing_well": (None, 0, "low", ""),
    "more_practice": ("teacher", 7, "low", "Set additional practice"),
    "contact_parent": ("owner", 1, "high", "Contact the family"),
    "other": (None, 0, "low", ""),
}
REPEAT_WINDOW_DAYS = 30


def academic_owner(db: Session, student: Student) -> Optional[User]:
    """Who follows up a student: an Academy Manager (the one with the fewest open tasks), else the teacher's
    supervisor, else the head of academics."""
    managers = (db.query(User).join(Role, Role.id == User.role_id)
                .filter(User.is_active.is_(True), Role.slug == "academy_manager").order_by(User.id).all())
    if managers:
        load = dict(db.query(Task.assignee_id, func.count(Task.id))
                    .filter(Task.assignee_id.in_([u.id for u in managers]), Task.status.in_(["todo", "in_progress", "review"]))
                    .group_by(Task.assignee_id).all())
        return min(managers, key=lambda u: (load.get(u.id, 0), u.id))
    if student.teacher and student.teacher.supervisor_id:
        sup = db.get(User, student.teacher.supervisor_id)
        if sup and sup.is_active:
            return sup
    return (db.query(User).join(Role, Role.id == User.role_id)
            .filter(User.is_active.is_(True), Role.slug == "hod_academics").order_by(User.id).first())


def recommendation_state(r: StudentRecommendation) -> str:
    if r.task is not None:
        return "done" if r.task.status in ("done", "completed") else ("cancelled" if r.task.status == "cancelled" else "open")
    return "recorded"


def recommend(db: Session, student: Student, kind: str, note: Optional[str], user: Optional[User], teacher: Optional[Teacher] = None,
              evaluation: Optional[Evaluation] = None, session: Optional[ClassSession] = None, request=None) -> StudentRecommendation:
    """Record a recommendation and create its follow-up: a task with a due date (reminded and escalated by the task
    job until done), an alert for urgent kinds, and an escalation when the same concern is still open from before."""
    if kind not in KIND_LABELS:
        raise ValueError("Choose a recommendation.")
    teacher = teacher or student.teacher
    rec = StudentRecommendation(student_id=student.id, teacher_id=teacher.id if teacher else None, kind=kind,
                                note=(note or "").strip() or None, evaluation_id=evaluation.id if evaluation else None,
                                session_id=session.id if session else None, created_by_id=user.id if user else None)
    db.add(rec)
    db.flush()
    who, days, priority, title = RECOMMENDATION_ACTIONS.get(kind, (None, 0, "low", ""))
    if who:
        assignee = (db.get(User, teacher.user_id) if teacher and teacher.user_id else None) if who == "teacher" else academic_owner(db, student)
        if assignee is None:
            assignee = academic_owner(db, student)
        task = Task(title=f"{title}: {student.full_name}"[:200],
                    description=f"{KIND_LABELS[kind]}." + (f" {rec.note}" if rec.note else "") + f" (recommended by {teacher.full_name if teacher else 'staff'})",
                    assignee_id=assignee.id if assignee else None, creator_id=user.id if user else None, priority=priority,
                    status="todo", due_date=org_today() + timedelta(days=days), entity_type="Student", entity_id=student.id)
        db.add(task)
        db.flush()
        rec.task_id, rec.action = task.id, "task"
        if assignee:
            notify(db, assignee, f"Follow-up: {student.full_name}", f"{KIND_LABELS[kind]}. {rec.note or ''}".strip(),
                   event_type="task_assigned", link=f"/students/{student.id}/journey")
    earlier = (db.query(StudentRecommendation).filter(StudentRecommendation.student_id == student.id, StudentRecommendation.kind == kind,
                                                      StudentRecommendation.id != rec.id,
                                                      StudentRecommendation.created_at >= datetime.utcnow() - timedelta(days=REPEAT_WINDOW_DAYS))
               .all())
    repeated = [r for r in earlier if recommendation_state(r) in ("open", "recorded")] if kind not in ("progressing_well", "other") else []
    if priority == "high" or repeated:
        alert = RiskAlert(alert_type="student_recommendation_repeat" if repeated else "student_recommendation",
                          severity="high" if repeated else "medium",
                          title=(f"Repeated concern for {student.full_name}: {KIND_LABELS[kind]}" if repeated
                                 else f"{KIND_LABELS[kind]}: {student.full_name}"),
                          message=rec.note or "", entity_type="Student", entity_id=student.id,
                          visibility="management" if repeated else "ops", source="system")
        db.add(alert)
        db.flush()
        rec.alert_id = alert.id
        if repeated:
            rec.action = "escalation"
            hod = (db.query(User).join(Role, Role.id == User.role_id)
                   .filter(User.is_active.is_(True), Role.slug == "hod_academics").order_by(User.id).first())
            if hod:
                notify(db, hod, f"Repeated concern: {student.full_name}", f"{KIND_LABELS[kind]} raised again while the earlier follow-up is open.",
                       event_type="escalation", link=f"/students/{student.id}/journey")
        elif rec.action == "none":
            rec.action = "alert"
    log_action(db, user, "create", "students", entity=student, request=request,
               description=f"Recommendation for {student.full_name}: {KIND_LABELS[kind]} ({rec.action})",
               after={"kind": kind, "action": rec.action, "task_id": rec.task_id})
    from app.services import automation
    automation.emit(db, "student.recommendation", "student", student.id, {"kind": kind, "recommendation_id": rec.id})
    return rec


def open_actions(db: Session, student: Student, viewer: User) -> dict:
    tasks = (db.query(Task).filter(Task.entity_type == "Student", Task.entity_id == student.id, Task.status.in_(["todo", "in_progress", "review"]))
             .order_by(Task.due_date).all())
    from app.services import complaints as cx
    cases = (cx.visible(db, viewer, db.query(Case).filter(or_(Case.student_id == student.id, Case.client_id == student.client_id),
                                                           cx.open_count_filter()))
             .order_by(Case.created_at.desc()).all())
    return {"tasks": tasks, "cases": cases}


# ============================================================================ monthly summary
def monthly_summary(db: Session, student: Student, period: str, viewer: User) -> dict:
    """Everything the Academy Manager needs about one student for one month, on one page."""
    start, end = month_bounds(period)
    stats = period_stats(db, student, start, end)
    syl = syllabus(db, student, start, end)
    evals = assessments(db, student, start, end)
    tests = db.query(MonthlyTest).filter(MonthlyTest.student_id == student.id, MonthlyTest.period == period).all()
    recs = (db.query(StudentRecommendation).filter(StudentRecommendation.student_id == student.id,
                                                   StudentRecommendation.created_at >= datetime.combine(start, time.min),
                                                   StudentRecommendation.created_at <= datetime.combine(end, time.max))
            .order_by(StudentRecommendation.created_at.desc()).all())
    observations = [n for n in [*(e.teacher_comment for e in evals), *(e.strengths for e in evals), *(e.weaknesses for e in evals)] if n]
    observations += [s.teacher_notes for s in db.query(ClassSession).filter(ClassSession.student_id == student.id, ClassSession.date >= start,
                                                                            ClassSession.date <= end, ClassSession.teacher_notes.isnot(None))]
    from app.services import complaints as cx
    concerns = (cx.visible(db, viewer, db.query(Case).filter(or_(Case.student_id == student.id, Case.client_id == student.client_id),
                                                              Case.created_at >= datetime.combine(start, time.min),
                                                              Case.created_at <= datetime.combine(end, time.max))).all())
    negative = (db.query(Feedback).filter(or_(Feedback.student_id == student.id, Feedback.client_id == student.client_id),
                                          Feedback.is_negative.is_(True), Feedback.submitted_at >= datetime.combine(start, time.min),
                                          Feedback.submitted_at <= datetime.combine(end, time.max)).all())
    return {"period": period, "label": start.strftime("%B %Y"), "start": start, "end": end, "stats": stats, "syllabus": syl,
            "evaluations": evals, "tests": tests, "recommendations": recs, "observations": observations[:12],
            "concerns": concerns, "negative_feedback": negative, "actions": open_actions(db, student, viewer),
            "flags": flags_for(stats, syl, evals, tests, recs)}


def flags_for(stats: dict, syl: dict, evals: list, tests: list, recs: list) -> list[str]:
    flags = []
    if stats.get("attendance_pct") is not None and stats["attendance_pct"] < 75:
        flags.append(f"Attendance {stats['attendance_pct']}%")
    if stats.get("absent", 0) >= 3:
        flags.append(f"{stats['absent']} absences")
    if stats.get("late", 0) >= 3:
        flags.append(f"{stats['late']} late arrivals")
    if stats.get("held", 0) >= 4 and not syl.get("completed") and not syl.get("taught"):
        flags.append("No progress recorded")
    if syl.get("behind"):
        flags.append(f"Behind expected pace ({syl['overall_pct']}% vs {syl['expected_pct']}%)")
    if any(e.result == "fail" for e in evals) or any((t.percentage or 100) < 50 for t in tests if t.percentage is not None):
        flags.append("Assessment below the pass mark")
    if any(recommendation_state(r) == "open" for r in recs):
        flags.append("Open teacher recommendation")
    return flags


def student_scope(db: Session, user: User) -> Optional[list[int]]:
    """Students a viewer may see in journey and summary pages; None = all."""
    if user.is_superuser or rbac.is_management(user):
        return None
    t = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    if t is not None:
        return [i for (i,) in db.query(Student.id).filter(Student.teacher_id == t.id)]
    if user.role_slug == "supervisor":
        tids = [i for (i,) in db.query(Teacher.id).filter(Teacher.supervisor_id == user.id)]
        return [i for (i,) in db.query(Student.id).filter(Student.teacher_id.in_(tids or [-1]))]
    if user.portal in ("client", "student"):
        from app.models.people import Client
        c = db.query(Client).filter(Client.user_id == user.id).first()
        if c is not None:
            return [s.id for s in c.students]
        s = db.query(Student).filter(Student.user_id == user.id).first()
        return [s.id] if s else []
    return None


def summary_board(db: Session, period: str, scope: Optional[list[int]], teacher_id: Optional[int] = None, q: str = "",
                  flagged_only: bool = False) -> list[dict]:
    """One row per student for the month, with the figures that matter and the flags that need attention.
    Built from grouped queries so a full college fits in one page load."""
    start, end = month_bounds(period)
    today = org_today()
    students = db.query(Student).filter(Student.status.in_(["active", "trial", "frozen"]))
    if scope is not None:
        students = students.filter(Student.id.in_(scope or [-1]))
    if teacher_id:
        students = students.filter(Student.teacher_id == teacher_id)
    if q:
        students = students.filter(or_(Student.full_name.ilike(f"%{q}%"), Student.student_code.ilike(f"%{q}%")))
    students = students.order_by(Student.full_name).all()
    ids = [s.id for s in students] or [-1]
    sess = (db.query(ClassSession.student_id, ClassSession.status, func.count(ClassSession.id))
            .filter(ClassSession.student_id.in_(ids), ClassSession.date >= start, ClassSession.date <= min(end, today))
            .group_by(ClassSession.student_id, ClassSession.status).all())
    by: dict[int, dict] = {}
    for sid, st, n in sess:
        by.setdefault(sid, {})[st] = n
    done = dict(db.query(StudentProgress.student_id, func.count(StudentProgress.id))
                .filter(StudentProgress.student_id.in_(ids), StudentProgress.status == "completed",
                        StudentProgress.completed_at >= datetime.combine(start, time.min),
                        StudentProgress.completed_at <= datetime.combine(end, time.max))
                .group_by(StudentProgress.student_id).all())
    evals: dict[int, list] = {}
    for e in db.query(Evaluation).filter(Evaluation.student_id.in_(ids), Evaluation.date >= start, Evaluation.date <= end):
        evals.setdefault(e.student_id, []).append(e)
    tests = {t.student_id: t for t in db.query(MonthlyTest).filter(MonthlyTest.student_id.in_(ids), MonthlyTest.period == period)}
    recs: dict[int, list] = {}
    for r in db.query(StudentRecommendation).filter(StudentRecommendation.student_id.in_(ids),
                                                    StudentRecommendation.created_at >= datetime.combine(start, time.min),
                                                    StudentRecommendation.created_at <= datetime.combine(end, time.max)):
        recs.setdefault(r.student_id, []).append(r)
    # late arrivals the same way as the journey page: an attendance mark of "late", or joining after the grace period
    late_ids: dict[int, set] = {}
    for sid, sess_id in (db.query(Attendance.student_id, Attendance.session_id)
                         .filter(Attendance.student_id.in_(ids), Attendance.student_status == "late",
                                 Attendance.date >= start, Attendance.date <= end)):
        late_ids.setdefault(sid, set()).add(sess_id)
    for sess_id, sid, sched_at, joined in (db.query(ClassSession.id, ClassSession.student_id, ClassSession.scheduled_start,
                                                    ClassSession.student_joined_at)
                                           .filter(ClassSession.student_id.in_(ids), ClassSession.status == "done",
                                                   ClassSession.date >= start, ClassSession.date <= end,
                                                   ClassSession.student_joined_at.isnot(None))):
        if sched_at and joined - sched_at > timedelta(minutes=LATE_GRACE_MINUTES):
            late_ids.setdefault(sid, set()).add(sess_id)
    # classes whose content was recorded (class activity, delivered lesson plan, linked lesson or teacher notes)
    taught: dict[int, set] = {}
    for sid, sess_id in (db.query(ClassSession.student_id, ClassActivity.session_id)
                         .join(ClassSession, ClassSession.id == ClassActivity.session_id)
                         .filter(ClassSession.student_id.in_(ids), ClassSession.date >= start, ClassSession.date <= end)):
        taught.setdefault(sid, set()).add(sess_id)
    for sid, sess_id in (db.query(LessonPlan.student_id, LessonPlan.session_id)
                         .filter(LessonPlan.student_id.in_(ids), LessonPlan.plan_date >= start, LessonPlan.plan_date <= end,
                                 LessonPlan.status != "planned")):
        taught.setdefault(sid, set()).add(sess_id or f"plan-{sid}-{len(taught.get(sid, ()))}")
    for sid, sess_id in (db.query(ClassSession.student_id, ClassSession.id)
                         .filter(ClassSession.student_id.in_(ids), ClassSession.status == "done", ClassSession.date >= start,
                                 ClassSession.date <= end, or_(ClassSession.lesson_id.isnot(None), ClassSession.teacher_notes.isnot(None)))):
        taught.setdefault(sid, set()).add(sess_id)
    open_tasks = dict(db.query(Task.entity_id, func.count(Task.id))
                      .filter(Task.entity_type == "Student", Task.entity_id.in_(ids), Task.status.in_(["todo", "in_progress", "review"]))
                      .group_by(Task.entity_id).all())
    rows = []
    for s in students:
        b = by.get(s.id, {})
        attended = b.get("done", 0)
        absent = b.get("absent", 0)
        marked = attended + absent
        stats = {"held": sum(n for st, n in b.items() if st not in NOT_HELD), "attended": max(0, attended), "absent": absent,
                 "late": len(late_ids.get(s.id, ())), "on_leave": b.get("leave", 0), "teacher_missed": b.get("missed", 0),
                 "attendance_pct": round(100.0 * max(0, attended) / marked, 1) if marked else None}
        syl = {"completed": [None] * done.get(s.id, 0), "taught": [None] * len(taught.get(s.id, ())), "behind": False,
               "overall_pct": None, "expected_pct": None}
        t = tests.get(s.id)
        flags = flags_for(stats, syl, evals.get(s.id, []), [t] if t else [], recs.get(s.id, []))
        if flagged_only and not flags:
            continue
        rows.append({"s": s, "stats": stats, "lessons_done": done.get(s.id, 0), "taught": len(taught.get(s.id, ())),
                     "evaluations": evals.get(s.id, []), "test": t,
                     "recommendations": len(recs.get(s.id, [])), "open_tasks": open_tasks.get(s.id, 0), "flags": flags})
    rows.sort(key=lambda r: (-len(r["flags"]), r["s"].full_name))
    return rows


def evidence_context(db: Session) -> dict:
    """What the evidence block on an assessment form needs."""
    bank = [{"id": q.id, "question": q.question, "answer": q.answer or "", "marks": q.marks, "assessment_id": q.assessment_id}
            for q in db.query(QuestionBankItem).filter(QuestionBankItem.status == "active").order_by(QuestionBankItem.id).limit(500)]
    return {"question_bank": bank, "answer_results": ANSWER_RESULTS, "recommendation_kinds": RECOMMENDATION_KINDS}


def apply_evidence_form(db: Session, ev: Evaluation, form, user: Optional[User], request=None) -> list[StudentRecommendation]:
    """Save the evidence posted with an assessment and create the follow-up for each ticked recommendation."""
    save_evidence(db, ev, form)
    kinds = list(form.getlist("rec_kind")) if hasattr(form, "getlist") else []
    note = (form.get("recommendations") or "").strip() or None
    student = ev.student or db.get(Student, ev.student_id)
    teacher = ev.teacher or (db.get(Teacher, ev.teacher_id) if ev.teacher_id else None)
    return [recommend(db, student, k, note, user, teacher=teacher, evaluation=ev, request=request) for k in dict.fromkeys(kinds) if k in KIND_LABELS]
