"""Academic services: curriculum navigation, progress, lesson plans, evaluations, monthly tests (Module 41),
result cards, dor (revision) schedules, certificates, AI lesson recommendation, Tajweed colouring (Module 28).

Contract used by other modules (import lazily inside functions):
    generate_monthly_test(db, student, period) -> MonthlyTest
    score_monthly_test(db, test, scores, remarks, remarks_urdu, user) -> MonthlyTest
    generate_result_card_pdf(db, test) -> str
    deliver_result_card(db, test, user) -> None
    generate_dor_schedule(db, test) -> DorSchedule
    override_dor_quota(db, student, user, reason)
    recommend_lesson(db, student) -> str
    record_progress(db, student, lesson, status, user, progress_type="sabaq")
    issue_certificate(db, student, course, title, user, generation="manual") -> Certificate
    student_progress_summary(db, student) -> dict
"""
from __future__ import annotations

import html
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings, BASE_DIR
from app.core.audit import log_action, snapshot
from app.core.notify import notify
from app.core.utils import month_bounds
from app.models.academic import (Book, Certificate, Chapter, Course, CurriculumVersion, Division, DorSchedule, Evaluation,
                                 Lesson, LessonAnnotation, LessonPlan, MonthlyTest, StudentProgress)
from app.models.core import RiskAlert, Setting, User
from app.models.people import Client, Student, Teacher

log = logging.getLogger("oqc.academic")

CERT_DIR = BASE_DIR / "storage" / "certificates"
CARD_DIR = BASE_DIR / "storage" / "result_cards"
ORG_NAME = "Online Quran College"
ORG_NAME_UR = "آن لائن قرآن کالج"
BRAND = "#0f766e"

EVAL_CRITERIA = ["tajweed", "fluency", "memorisation", "understanding"]
GRADE_BANDS = [(85, "A"), (70, "B"), (55, "C"), (0, "D")]


class AcademicError(Exception):
    """Raised for business-rule violations (e.g. dor quota not met)."""


class DorQuotaBlocked(AcademicError):
    pass


# =============================================================================== periods
def period_of(d: Optional[date] = None) -> str:
    d = d or date.today()
    return d.strftime("%Y-%m")


def shift_period(period: str, months: int) -> str:
    y, m = [int(x) for x in period.split("-")]
    idx = y * 12 + (m - 1) + months
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def previous_period(period: str) -> str:
    return shift_period(period, -1)


def next_period(period: str) -> str:
    return shift_period(period, 1)


def period_label(period: str) -> str:
    try:
        return date(int(period[:4]), int(period[5:7]), 1).strftime("%B %Y")
    except Exception:
        return period


def grade_for(pct: Optional[float]) -> Optional[str]:
    if pct is None:
        return None
    for lo, g in GRADE_BANDS:
        if pct >= lo:
            return g
    return "D"


def setting_value(db: Session, key: str, default):
    s = db.query(Setting).filter(Setting.key == key).first()
    if not s or s.value is None:
        return default
    v = s.value
    if isinstance(v, dict) and "value" in v:
        return v["value"]
    return v


# =============================================================================== curriculum navigation
def course_lessons(db: Session, course_id: Optional[int]) -> list[Lesson]:
    """All lessons of a course in curriculum order (book → chapter → lesson)."""
    if not course_id:
        return []
    return (db.query(Lesson).join(Chapter, Lesson.chapter_id == Chapter.id).join(Book, Chapter.book_id == Book.id)
            .filter(Book.course_id == course_id)
            .order_by(Book.order, Book.id, Chapter.order, Chapter.id, Lesson.order, Lesson.id).all())


def lesson_path(lesson: Optional[Lesson]) -> str:
    if not lesson:
        return "—"
    ch = lesson.chapter
    bk = ch.book if ch else None
    parts = [bk.title if bk else None, ch.title if ch else None, lesson.title]
    return " › ".join(p for p in parts if p)


def lesson_course_id(lesson: Optional[Lesson]) -> Optional[int]:
    return lesson.chapter.book.course_id if lesson and lesson.chapter and lesson.chapter.book else None


def progress_map(db: Session, student_id: int) -> dict[int, StudentProgress]:
    rows = db.query(StudentProgress).filter(StudentProgress.student_id == student_id).order_by(StudentProgress.id).all()
    out: dict[int, StudentProgress] = {}
    for r in rows:
        out[r.lesson_id] = r  # last row wins
    return out


def current_lesson_for(db: Session, student: Student) -> Optional[Lesson]:
    """Student's current lesson; falls back to first not-completed lesson, then first lesson of the course."""
    if student.current_lesson_id:
        les = db.get(Lesson, student.current_lesson_id)
        if les:
            return les
    lessons = course_lessons(db, student.course_id)
    if not lessons:
        return None
    pm = progress_map(db, student.id)
    for les in lessons:
        p = pm.get(les.id)
        if not p or p.status != "completed":
            return les
    return lessons[-1]


def next_lesson_after(db: Session, lesson: Optional[Lesson]) -> Optional[Lesson]:
    if not lesson:
        return None
    lessons = course_lessons(db, lesson_course_id(lesson))
    ids = [l.id for l in lessons]
    if lesson.id not in ids:
        return None
    i = ids.index(lesson.id)
    return lessons[i + 1] if i + 1 < len(lessons) else None


def prev_lesson_before(db: Session, lesson: Optional[Lesson]) -> Optional[Lesson]:
    if not lesson:
        return None
    lessons = course_lessons(db, lesson_course_id(lesson))
    ids = [l.id for l in lessons]
    if lesson.id not in ids:
        return None
    i = ids.index(lesson.id)
    return lessons[i - 1] if i > 0 else None


def student_progress_summary(db: Session, student: Student) -> dict:
    lessons = course_lessons(db, student.course_id)
    pm = progress_map(db, student.id)
    completed = sum(1 for l in lessons if pm.get(l.id) and pm[l.id].status == "completed")
    in_progress = sum(1 for l in lessons if pm.get(l.id) and pm[l.id].status == "in_progress")
    revision = sum(1 for l in lessons if pm.get(l.id) and pm[l.id].status == "revision")
    total = len(lessons)
    cur = current_lesson_for(db, student)
    last_eval = (db.query(Evaluation).filter(Evaluation.student_id == student.id)
                 .order_by(Evaluation.date.desc(), Evaluation.id.desc()).first())
    last_test = (db.query(MonthlyTest).filter(MonthlyTest.student_id == student.id, MonthlyTest.score.isnot(None))
                 .order_by(MonthlyTest.period.desc()).first())
    dor = (db.query(DorSchedule).filter(DorSchedule.student_id == student.id, DorSchedule.period == period_of())
           .order_by(DorSchedule.id.desc()).first())
    return {
        "completed": completed, "in_progress": in_progress, "revision": revision, "total": total,
        "pct": round(100.0 * completed / total, 1) if total else 0.0,
        "current_lesson": cur, "current_lesson_title": lesson_path(cur),
        "sabaq_position": student.sabaq_position,
        "last_evaluation": last_eval, "last_test": last_test,
        "dor_quota_met": student.dor_quota_met, "dor_schedule": dor,
    }


def book_breakdown(db: Session, student: Student) -> list[dict]:
    """Per book → per chapter counts for progress bars."""
    pm = progress_map(db, student.id)
    out = []
    books = db.query(Book).filter(Book.course_id == student.course_id).order_by(Book.order, Book.id).all() if student.course_id else []
    for b in books:
        chapters = []
        b_total = b_done = b_prog = b_rev = 0
        for ch in b.chapters:
            rows = []
            c_done = c_prog = c_rev = 0
            for les in ch.lessons:
                p = pm.get(les.id)
                st = p.status if p else "not_started"
                rows.append({"lesson": les, "status": st, "progress": p})
                c_done += st == "completed"
                c_prog += st == "in_progress"
                c_rev += st == "revision"
            total = len(ch.lessons)
            chapters.append({"chapter": ch, "lessons": rows, "total": total, "completed": c_done, "in_progress": c_prog, "revision": c_rev,
                             "pct": round(100.0 * c_done / total, 1) if total else 0.0})
            b_total += total
            b_done += c_done
            b_prog += c_prog
            b_rev += c_rev
        out.append({"book": b, "chapters": chapters, "total": b_total, "completed": b_done, "in_progress": b_prog, "revision": b_rev,
                    "pct": round(100.0 * b_done / b_total, 1) if b_total else 0.0})
    return out


# =============================================================================== progress
def record_progress(db: Session, student: Student, lesson: Lesson, status: str, user: Optional[User],
                    progress_type: str = "sabaq", score: Optional[float] = None, notes: Optional[str] = None,
                    enforce_dor: bool = True, request=None) -> StudentProgress:
    """Upsert a StudentProgress row and move the student's current lesson pointer.

    Business rule (Module 41): a *new* sabaq (status in_progress, progress_type sabaq) is blocked while the student's
    monthly dor quota is not met, unless a supervisor override has set ``Student.dor_quota_met``.
    """
    if status not in ("not_started", "in_progress", "completed", "revision"):
        raise AcademicError(f"Invalid status {status}")
    if (enforce_dor and progress_type == "sabaq" and status == "in_progress" and not student.dor_quota_met
            and not (student.current_lesson_id == lesson.id)):
        raise DorQuotaBlocked("New sabaq is blocked until this month's dor (revision) quota is met. A supervisor may override with a rationale.")
    row = (db.query(StudentProgress).filter(StudentProgress.student_id == student.id, StudentProgress.lesson_id == lesson.id)
           .order_by(StudentProgress.id.desc()).first())
    before = snapshot(row) if row else None
    now = datetime.utcnow()
    if not row:
        row = StudentProgress(student_id=student.id, lesson_id=lesson.id, teacher_id=student.teacher_id)
        db.add(row)
    row.status = status
    row.progress_type = progress_type
    if score is not None:
        row.score = score
    if notes:
        row.notes = notes
    if status == "in_progress" and not row.started_at:
        row.started_at = now
    if status == "completed":
        row.completed_at = row.completed_at or now
        row.started_at = row.started_at or now
    if status == "not_started":
        row.started_at = None
        row.completed_at = None
    db.flush()
    # move pointer
    if status == "in_progress":
        student.current_lesson_id = lesson.id
        student.sabaq_position = lesson_path(lesson)
    elif status == "completed" and student.current_lesson_id in (None, lesson.id):
        nxt = next_lesson_after(db, lesson)
        student.current_lesson_id = nxt.id if nxt else lesson.id
        student.sabaq_position = lesson_path(nxt) if nxt else f"{lesson_path(lesson)} (course complete)"
    if user is not None:
        log_action(db, user, "progress_update", "curriculum", entity=row, description=f"{student.full_name}: {lesson.title} → {status} ({progress_type})",
                   before=before, after=snapshot(row), request=request)
    if status == "completed" and user is not None:
        _maybe_auto_certificate(db, student, user)
    return row


def _maybe_auto_certificate(db: Session, student: Student, user: User) -> Optional[Certificate]:
    """Auto-issue a course completion certificate when every lesson of the course is completed."""
    lessons = course_lessons(db, student.course_id)
    if not lessons:
        return None
    pm = progress_map(db, student.id)
    if not all(pm.get(l.id) and pm[l.id].status == "completed" for l in lessons):
        return None
    course = db.get(Course, student.course_id)
    title = f"Certificate of Completion — {course.name}"
    exists = db.query(Certificate).filter(Certificate.student_id == student.id, Certificate.course_id == course.id,
                                          Certificate.title == title, Certificate.is_revoked.is_(False)).first()
    if exists:
        return exists
    return issue_certificate(db, student, course, title, user, generation="automatic",
                             description=f"Successfully completed the full {course.name} curriculum with Online Quran College.")


# =============================================================================== lesson plans
def compute_variance(planned: str, delivered: str) -> float:
    """Percentage of planned content NOT delivered (0 = fully delivered, 100 = nothing). Token overlap heuristic."""
    p = set(re.findall(r"[\w\u0600-\u06FF]+", (planned or "").lower()))
    d = set(re.findall(r"[\w\u0600-\u06FF]+", (delivered or "").lower()))
    if not p:
        return 0.0
    if not d:
        return 100.0
    covered = len(p & d) / len(p)
    return round(max(0.0, min(100.0, (1 - covered) * 100)), 1)


def status_for_variance(v: float) -> str:
    if v >= 95:
        return "not_delivered"
    if v > 25:
        return "partially_delivered"
    return "delivered"


def mark_plan_delivered(db: Session, plan: LessonPlan, delivered_content: str, teacher_notes: Optional[str], user: User,
                        variance_pct: Optional[float] = None, request=None) -> LessonPlan:
    before = snapshot(plan)
    plan.delivered_content = delivered_content
    if teacher_notes:
        plan.teacher_notes = teacher_notes
    plan.variance_pct = variance_pct if variance_pct is not None else compute_variance(plan.planned_content, delivered_content)
    plan.status = status_for_variance(plan.variance_pct)
    log_action(db, user, "deliver", "lesson_plans", entity=plan, description=f"Lesson plan {plan.plan_date} marked {plan.status} (variance {plan.variance_pct}%)",
               before=before, after=snapshot(plan), request=request)
    return plan


def recommend_lesson(db: Session, student: Student, plan: Optional[LessonPlan] = None, user: Optional[User] = None) -> str:
    """AI lesson recommendation (module lesson_recommendation). Stores the run on the plan when given."""
    from app.services.ai_gateway import ai
    summary = student_progress_summary(db, student)
    cur = summary["current_lesson"]
    last_done = None
    pm = progress_map(db, student.id)
    for les in reversed(course_lessons(db, student.course_id)):
        p = pm.get(les.id)
        if p and p.status == "completed":
            last_done = les
            break
    last_eval = summary["last_evaluation"]
    focus = "makhaarij and madd rules"
    if last_eval and last_eval.criteria:
        weakest = min(last_eval.criteria.items(), key=lambda kv: float(kv[1] or 0))
        focus = {"tajweed": "Tajweed accuracy (noon sakinah & madd)", "fluency": "reading fluency and pace", "memorisation": "retention of recent sabaq",
                 "understanding": "meaning and translation"}.get(weakest[0], focus)
    payload = {"student_id": student.id, "course": student.course.name if student.course else None, "level": student.level,
               "last_lesson": last_done.title if last_done else None, "next_lesson": cur.title if cur else None, "focus": focus,
               "progress_pct": summary["pct"], "dor_quota_met": student.dor_quota_met,
               "last_test_pct": summary["last_test"].percentage if summary["last_test"] else None}
    result, run = ai(db, module="lesson_recommendation", task="recommend_next_lesson", payload=payload, entity=plan or student)
    text = result.get("recommendation") or "Continue with the current lesson; revise the previous sabaq for five minutes first."
    split = result.get("suggested_duration_split")
    if split:
        text += " Suggested split — " + ", ".join(f"{k}: {v} min" for k, v in split.items()) + "."
    if plan is not None:
        plan.ai_recommendation = text
        plan.ai_run_id = run.id
    return text


# =============================================================================== evaluations
def evaluation_score(criteria: dict) -> float:
    vals = [float(v) for v in criteria.values() if v not in (None, "")]
    return round(sum(vals) / len(vals) * 10, 1) if vals else 0.0  # criteria are 0-10 → percent


# =============================================================================== monthly tests (Module 41)
def _questions_for(db: Session, student: Student) -> tuple[list[dict], dict]:
    lessons = course_lessons(db, student.course_id)
    pm = progress_map(db, student.id)
    done = [l for l in lessons if pm.get(l.id) and pm[l.id].status == "completed"]
    cur = current_lesson_for(db, student)
    recent = done[-4:]
    questions: list[dict] = []
    for les in recent:
        questions.append({"item": f"Sabqi — {les.title}", "type": "sabqi", "lesson_id": les.id, "max": 10, "score": None})
    if cur:
        questions.append({"item": f"Sabaq — {cur.title}", "type": "sabaq", "lesson_id": cur.id, "max": 10, "score": None})
    older = done[:-4]
    for les in older[-2:]:
        questions.append({"item": f"Dor (revision) — {les.title}", "type": "dor", "lesson_id": les.id, "max": 10, "score": None})
    code = student.course.code if student.course else ""
    questions.append({"item": "Tajweed accuracy (makhaarij, noon sakinah, madd)", "type": "tajweed", "max": 10, "score": None})
    questions.append({"item": "Fluency & pace of recitation", "type": "fluency", "max": 10, "score": None})
    if code in ("HIFZ",):
        questions.append({"item": "Memorisation retention (random ayah prompt)", "type": "memorisation", "max": 10, "score": None})
    if code in ("TARJUMA", "ISLAMIC"):
        questions.append({"item": "Understanding & explanation of meaning", "type": "understanding", "max": 10, "score": None})
    if not recent and not cur:
        questions.insert(0, {"item": "Placement recitation (teacher's choice)", "type": "sabaq", "max": 10, "score": None})
    snapshot_ = {"course": student.course.name if student.course else None, "course_code": code,
                 "division": student.division.name if student.division else None, "level": student.level,
                 "sabaq_position": student.sabaq_position, "current_lesson": cur.title if cur else None,
                 "current_lesson_id": cur.id if cur else None,
                 "last_lessons": [l.title for l in recent], "completed_count": len(done), "total_lessons": len(lessons),
                 "teacher": student.teacher.full_name if student.teacher else None}
    return questions, snapshot_


def generate_monthly_test(db: Session, student: Student, period: str) -> MonthlyTest:
    """Create (idempotently) the monthly test for a student/period from their curriculum position."""
    existing = db.query(MonthlyTest).filter(MonthlyTest.student_id == student.id, MonthlyTest.period == period).first()
    if existing:
        return existing
    questions, snap = _questions_for(db, student)
    prev = (db.query(MonthlyTest).filter(MonthlyTest.student_id == student.id, MonthlyTest.period < period, MonthlyTest.percentage.isnot(None))
            .order_by(MonthlyTest.period.desc()).first())
    t = MonthlyTest(student_id=student.id, teacher_id=student.teacher_id, period=period, generated_from=snap, questions=questions,
                    max_score=float(sum(q["max"] for q in questions)), previous_percentage=prev.percentage if prev else None, status="generated")
    db.add(t)
    db.flush()
    return t


def generate_tests_for_period(db: Session, period: str, user: Optional[User] = None, request=None) -> int:
    created = 0
    students = db.query(Student).filter(Student.status.in_(["active", "trial"])).all()
    for s in students:
        if db.query(MonthlyTest.id).filter(MonthlyTest.student_id == s.id, MonthlyTest.period == period).first():
            continue
        generate_monthly_test(db, s, period)
        created += 1
    if user is not None:
        log_action(db, user, "generate", "monthly_tests", entity_type="MonthlyTest", description=f"Generated {created} monthly tests for {period}", request=request)
    return created


def score_monthly_test(db: Session, test: MonthlyTest, scores: dict[str, float], remarks: str, remarks_urdu: Optional[str],
                       user: Optional[User], generate_card: bool = True, request=None, scored_at: Optional[datetime] = None) -> MonthlyTest:
    """Record per-question scores; compute percentage, grade, improvement vs previous month; generate result card + next dor schedule;
    route declining students to retention. ``scores`` keys are question indexes (str or int)."""
    before = snapshot(test)
    qs = [dict(q) for q in (test.questions or [])]
    total = 0.0
    for i, q in enumerate(qs):
        raw = scores.get(str(i), scores.get(i))
        if raw in (None, ""):
            continue
        val = max(0.0, min(float(q.get("max", 10)), float(raw)))
        q["score"] = val
        total += val
    test.questions = qs
    test.max_score = float(sum(float(q.get("max", 10)) for q in qs)) or 100.0
    test.score = round(total, 1)
    test.percentage = round(100.0 * total / test.max_score, 1)
    test.grade = grade_for(test.percentage)
    prev = (db.query(MonthlyTest).filter(MonthlyTest.student_id == test.student_id, MonthlyTest.period < test.period,
                                         MonthlyTest.percentage.isnot(None), MonthlyTest.id != test.id)
            .order_by(MonthlyTest.period.desc()).first())
    test.previous_percentage = prev.percentage if prev else None
    test.improvement_pct = round(test.percentage - prev.percentage, 1) if prev else None
    test.teacher_remarks = remarks
    test.teacher_remarks_urdu = remarks_urdu
    test.status = "scored"
    test.scored_at = scored_at or datetime.utcnow()
    db.flush()
    student = test.student
    # evaluation-style progress: mark sabaq question lessons
    if user is not None:
        log_action(db, user, "grade_change", "monthly_tests", entity=test, before=before, after=snapshot(test),
                   description=f"Scored {student.full_name} {test.period}: {test.percentage}% ({test.grade})",
                   rationale=remarks or None, consequential=True, request=request)
    if generate_card:
        try:
            generate_result_card_pdf(db, test)
        except Exception as exc:  # pragma: no cover
            log.warning("result card generation failed for test %s: %s", test.id, exc)
    generate_dor_schedule(db, test)
    if test.improvement_pct is not None and test.improvement_pct < -10:
        _route_decline_to_retention(db, test)
    return test


def _route_decline_to_retention(db: Session, test: MonthlyTest) -> None:
    student = test.student
    try:
        from app.models.crm import RetentionAction
        exists = db.query(RetentionAction).filter(RetentionAction.student_id == student.id, RetentionAction.trigger == "test_decline",
                                                  RetentionAction.status.in_(["scheduled", "in_progress"])).first()
        if not exists:
            db.add(RetentionAction(student_id=student.id, client_id=student.client_id, action_type="cohort_call", trigger="test_decline",
                                   risk_score_at_trigger=student.risk_score, status="scheduled",
                                   scheduled_at=datetime.utcnow() + timedelta(days=2),
                                   notes=f"Monthly test {test.period}: {test.percentage}% ({test.improvement_pct:+.1f} pts vs previous month)."))
    except Exception as exc:  # pragma: no cover
        log.warning("retention routing unavailable: %s", exc)
    db.add(RiskAlert(alert_type="test_decline", severity="medium" if test.improvement_pct > -20 else "high",
                     title=f"Declining test score: {student.full_name} ({student.student_code})",
                     message=f"{period_label(test.period)} result {test.percentage}% — {test.improvement_pct:+.1f} points vs previous month. Cohort call scheduled.",
                     entity_type="Student", entity_id=student.id, visibility="ops", source="system"))
    if student.teacher and student.teacher.user_id:
        notify(db, student.teacher.user_id, f"Score decline: {student.full_name}",
               f"{period_label(test.period)}: {test.percentage}% ({test.improvement_pct:+.1f} pts). Retention cohort call has been scheduled.",
               event_type="test_decline", link=f"/academics/monthly-tests/{test.id}")


def deliver_result_card(db: Session, test: MonthlyTest, user: Optional[User], request=None) -> None:
    """Send the result card to the guardian (in-app + WhatsApp) and the student (in-app)."""
    if test.percentage is None:
        raise AcademicError("Score the test before delivering the result card.")
    if not test.result_card_path or not (BASE_DIR / test.result_card_path).exists():
        generate_result_card_pdf(db, test)
    student = test.student
    client: Optional[Client] = student.client
    link = f"/{test.result_card_path}" if test.result_card_path else f"/academics/monthly-tests/{test.id}"
    title = f"Result card — {student.full_name} — {period_label(test.period)}"
    body = (f"{student.full_name} scored {test.percentage}% (grade {test.grade}) in the {period_label(test.period)} monthly test."
            + (f" Improvement vs last month: {test.improvement_pct:+.1f} pts." if test.improvement_pct is not None else "")
            + (f"\nTeacher remarks: {test.teacher_remarks}" if test.teacher_remarks else ""))
    channels: list[str] = []
    if client and client.user_id:
        chans = ["in_app"] + (["whatsapp"] if client.whatsapp and client.whatsapp_opt_in else [])
        notify(db, client.user_id, title, body, event_type="result_card", link=link, channels=tuple(chans), recipient_address=client.whatsapp)
        channels.extend(chans)
    if student.user_id:
        notify(db, student.user_id, title, body, event_type="result_card", link=link, channels=("in_app",))
        channels.append("in_app_student")
    test.delivered_at = datetime.utcnow()
    test.delivery_channels = channels
    test.status = "delivered"
    if user is not None:
        log_action(db, user, "deliver", "monthly_tests", entity=test, description=f"Result card delivered via {', '.join(channels) or 'no channel'}", request=request)


def generate_dor_schedule(db: Session, test: MonthlyTest) -> DorSchedule:
    """Spaced-repetition revision plan for the month after the test, built from completed lessons (weakest first)."""
    student = test.student
    target = next_period(test.period)
    existing = db.query(DorSchedule).filter(DorSchedule.student_id == student.id, DorSchedule.period == target).first()
    if existing:
        existing.monthly_test_id = existing.monthly_test_id or test.id
        return existing
    quota = int(setting_value(db, "dor_quota_default", 8) or 8)
    lessons = course_lessons(db, student.course_id)
    pm = progress_map(db, student.id)
    done = [l for l in lessons if pm.get(l.id) and pm[l.id].status == "completed"]
    # weak items from the test first (score < 70% of max), then most recent completed lessons, then older ones
    weak_ids = [q.get("lesson_id") for q in (test.questions or []) if q.get("lesson_id") and q.get("score") is not None and q["score"] < 0.7 * float(q.get("max", 10))]
    ordered: list[Lesson] = [l for l in done if l.id in weak_ids] + [l for l in reversed(done) if l.id not in weak_ids]
    start, end = month_bounds(target)
    intervals = [1, 3, 7, 14, 21, 28]
    items: list[dict] = []
    if not ordered:
        items.append({"content": "Free recitation revision of the current lesson", "lesson_id": None, "due": start.isoformat(), "done": False, "interval_days": 1})
    i = 0
    while len(items) < max(quota, 1) and ordered:
        les = ordered[i % len(ordered)]
        rep = i // len(ordered)
        offset = intervals[min(rep, len(intervals) - 1)] + (i % len(ordered)) * max(1, 27 // max(1, min(len(ordered), quota)))
        due = min(end, start + timedelta(days=min(27, offset)))
        items.append({"content": f"Dor — {les.title}" + (f" ({les.chapter.title})" if les.chapter else ""), "lesson_id": les.id,
                      "due": due.isoformat(), "done": False, "interval_days": intervals[min(rep, len(intervals) - 1)]})
        i += 1
    items.sort(key=lambda x: x["due"])
    sched = DorSchedule(student_id=student.id, period=target, monthly_test_id=test.id, items=items, quota=len(items), completed=0, quota_met=len(items) == 0)
    db.add(sched)
    db.flush()
    if target == period_of() and not sched.quota_met:
        student.dor_quota_met = False
    return sched


def mark_dor_item(db: Session, sched: DorSchedule, index: int, done: bool, user: Optional[User], request=None) -> DorSchedule:
    items = [dict(x) for x in (sched.items or [])]
    if 0 <= index < len(items):
        items[index]["done"] = done
        items[index]["done_at"] = datetime.utcnow().isoformat() if done else None
    sched.items = items
    sched.completed = sum(1 for x in items if x.get("done"))
    sched.quota_met = sched.completed >= sched.quota or bool(sched.override_by_id)
    if sched.period == period_of():
        sched.student.dor_quota_met = sched.quota_met
        # keep the flag also when a revision progress row would help reports
    if done and items[index].get("lesson_id") and sched.student:
        les = db.get(Lesson, items[index]["lesson_id"])
        if les:
            record_progress(db, sched.student, les, "revision" if not (progress_map(db, sched.student_id).get(les.id) and progress_map(db, sched.student_id)[les.id].status == "completed") else "completed",
                            None, progress_type="dor", enforce_dor=False)
    if user is not None:
        log_action(db, user, "update", "monthly_tests", entity=sched, description=f"Dor item {index + 1} marked {'done' if done else 'pending'} ({sched.completed}/{sched.quota})", request=request)
    return sched


def override_dor_quota(db: Session, student: Student, user: User, reason: str, request=None) -> None:
    """Teacher/supervisor override: unblock new sabaq although revision quota is not met. Audited as consequential."""
    if not (reason or "").strip():
        raise AcademicError("A rationale is required to override the dor quota.")
    before = {"dor_quota_met": student.dor_quota_met}
    student.dor_quota_met = True
    sched = db.query(DorSchedule).filter(DorSchedule.student_id == student.id, DorSchedule.period == period_of()).order_by(DorSchedule.id.desc()).first()
    if sched:
        sched.override_by_id = user.id
        sched.override_reason = reason
        sched.override_at = datetime.utcnow()
        sched.quota_met = True
    log_action(db, user, "override", "monthly_tests", entity=sched or student, entity_type="DorSchedule" if sched else "Student",
               description=f"Dor quota override for {student.full_name} ({student.student_code})", rationale=reason,
               before=before, after={"dor_quota_met": True}, severity="warning", consequential=True, request=request)


def refresh_dor_flags(db: Session) -> int:
    """Recompute Student.dor_quota_met from the current month's schedule. Returns number of blocked students."""
    period = period_of()
    blocked = 0
    students = db.query(Student).filter(Student.status.in_(["active", "trial"])).all()
    for s in students:
        sched = db.query(DorSchedule).filter(DorSchedule.student_id == s.id, DorSchedule.period == period).order_by(DorSchedule.id.desc()).first()
        met = True if not sched else (sched.quota_met or bool(sched.override_by_id) or sched.completed >= sched.quota)
        if sched and not sched.quota_met and met:
            sched.quota_met = True
        s.dor_quota_met = met
        blocked += not met
    return blocked


# =============================================================================== reports
def improvement_report(db: Session, period: str, teacher_id: Optional[int] = None, student_ids: Optional[Iterable[int]] = None) -> dict:
    q = db.query(MonthlyTest).filter(MonthlyTest.period == period, MonthlyTest.percentage.isnot(None))
    if teacher_id:
        q = q.filter(MonthlyTest.teacher_id == teacher_id)
    if student_ids is not None:
        q = q.filter(MonthlyTest.student_id.in_(list(student_ids)))
    tests = q.all()
    per_student = []
    per_teacher: dict[int, dict] = {}
    per_class: dict[str, dict] = {}
    for t in tests:
        s = t.student
        per_student.append({"test": t, "student": s, "teacher": t.teacher, "course": s.course.name if s and s.course else "—",
                            "division": s.division.name if s and s.division else "—"})
        if t.teacher_id:
            d = per_teacher.setdefault(t.teacher_id, {"teacher": t.teacher, "n": 0, "sum_pct": 0.0, "sum_imp": 0.0, "n_imp": 0, "declines": 0, "grades": {"A": 0, "B": 0, "C": 0, "D": 0}})
            d["n"] += 1
            d["sum_pct"] += t.percentage or 0
            if t.improvement_pct is not None:
                d["sum_imp"] += t.improvement_pct
                d["n_imp"] += 1
                d["declines"] += t.improvement_pct < -10
            d["grades"][t.grade or "D"] += 1
        key = f"{s.course.name if s and s.course else 'Unassigned'} · {s.division.name if s and s.division else 'General'}"
        c = per_class.setdefault(key, {"label": key, "n": 0, "sum_pct": 0.0, "sum_imp": 0.0, "n_imp": 0, "declines": 0})
        c["n"] += 1
        c["sum_pct"] += t.percentage or 0
        if t.improvement_pct is not None:
            c["sum_imp"] += t.improvement_pct
            c["n_imp"] += 1
            c["declines"] += t.improvement_pct < -10
    for d in list(per_teacher.values()) + list(per_class.values()):
        d["avg_pct"] = round(d["sum_pct"] / d["n"], 1) if d["n"] else 0
        d["avg_imp"] = round(d["sum_imp"] / d["n_imp"], 1) if d["n_imp"] else 0
    per_student.sort(key=lambda r: (r["test"].improvement_pct if r["test"].improvement_pct is not None else 0))
    teachers = sorted(per_teacher.values(), key=lambda d: -d["avg_imp"])
    classes = sorted(per_class.values(), key=lambda d: d["label"])
    n = len(tests)
    return {"tests": tests, "students": per_student, "teachers": teachers, "classes": classes, "n": n,
            "avg_pct": round(sum(t.percentage for t in tests) / n, 1) if n else 0,
            "avg_imp": round(sum(t.improvement_pct for t in tests if t.improvement_pct is not None) / max(1, sum(1 for t in tests if t.improvement_pct is not None)), 1) if n else 0,
            "declines": sum(1 for t in tests if (t.improvement_pct or 0) < -10),
            "grade_dist": {g: sum(1 for t in tests if t.grade == g) for g in "ABCD"}}


def variance_report(db: Session, date_from: date, date_to: date, teacher_id: Optional[int] = None, student_ids: Optional[Iterable[int]] = None) -> dict:
    q = db.query(LessonPlan).filter(LessonPlan.plan_date >= date_from, LessonPlan.plan_date <= date_to)
    if teacher_id:
        q = q.filter(LessonPlan.teacher_id == teacher_id)
    if student_ids is not None:
        q = q.filter(LessonPlan.student_id.in_(list(student_ids)))
    plans = q.all()
    by_teacher: dict[int, dict] = {}
    for p in plans:
        d = by_teacher.setdefault(p.teacher_id or 0, {"teacher": p.teacher, "planned": 0, "delivered": 0, "partial": 0, "not_delivered": 0, "sum_var": 0.0, "n_var": 0, "reviewed": 0})
        d["planned"] += 1
        if p.status == "delivered":
            d["delivered"] += 1
        elif p.status == "partially_delivered":
            d["partial"] += 1
        elif p.status == "not_delivered":
            d["not_delivered"] += 1
        if p.variance_pct is not None:
            d["sum_var"] += p.variance_pct
            d["n_var"] += 1
        d["reviewed"] += bool(p.reviewed_at)
    rows = []
    for d in by_teacher.values():
        d["avg_variance"] = round(d["sum_var"] / d["n_var"], 1) if d["n_var"] else 0
        d["delivery_rate"] = round(100.0 * (d["delivered"] + d["partial"]) / d["planned"], 1) if d["planned"] else 0
        rows.append(d)
    rows.sort(key=lambda d: d["avg_variance"])
    n = len(plans)
    delivered = [p for p in plans if p.variance_pct is not None]
    return {"rows": rows, "n": n, "delivered": sum(1 for p in plans if p.status == "delivered"),
            "partial": sum(1 for p in plans if p.status == "partially_delivered"), "not_delivered": sum(1 for p in plans if p.status == "not_delivered"),
            "pending": sum(1 for p in plans if p.status == "planned"),
            "avg_variance": round(sum(p.variance_pct for p in delivered) / len(delivered), 1) if delivered else 0}


def syllabus_compliance(db: Session, course_id: Optional[int] = None, teacher_id: Optional[int] = None, student_ids: Optional[Iterable[int]] = None) -> list[dict]:
    """Planned vs delivered per student/course: expected lessons by now (join date & course target) vs completed, and lesson-plan delivery."""
    q = db.query(Student).filter(Student.status.in_(["active", "trial", "frozen"]))
    if course_id:
        q = q.filter(Student.course_id == course_id)
    if teacher_id:
        q = q.filter(Student.teacher_id == teacher_id)
    if student_ids is not None:
        q = q.filter(Student.id.in_(list(student_ids)))
    rows = []
    lesson_cache: dict[int, int] = {}
    today = date.today()
    for s in q.order_by(Student.course_id, Student.full_name).all():
        if not s.course_id:
            continue
        total = lesson_cache.setdefault(s.course_id, len(course_lessons(db, s.course_id)))
        pm = progress_map(db, s.id)
        completed = sum(1 for p in pm.values() if p.status == "completed")
        months = (s.course.completion_target_months or 12) if s.course else 12
        elapsed_days = max(0, (today - (s.join_date or today)).days)
        expected = min(total, round(total * elapsed_days / (months * 30.4))) if total else 0
        plans = db.query(LessonPlan).filter(LessonPlan.student_id == s.id).all()
        planned_n = len(plans)
        delivered_n = sum(1 for p in plans if p.status in ("delivered", "partially_delivered"))
        compliance = round(100.0 * completed / expected, 1) if expected else (100.0 if completed else 0.0)
        rows.append({"student": s, "course": s.course, "teacher": s.teacher, "total": total, "completed": completed, "expected": expected,
                     "gap": completed - expected, "compliance": min(compliance, 150.0), "plans": planned_n, "delivered_plans": delivered_n,
                     "delivery_rate": round(100.0 * delivered_n / planned_n, 1) if planned_n else 0.0,
                     "status": "on_track" if completed >= expected else ("behind" if completed >= expected * 0.7 else "at_risk")})
    return rows


# =============================================================================== certificates
def next_certificate_number(db: Session, year: Optional[int] = None) -> str:
    year = year or date.today().year
    prefix = f"OQC-{year}-"
    n = db.query(func.count(Certificate.id)).filter(Certificate.certificate_number.like(prefix + "%")).scalar() or 0
    while True:
        n += 1
        num = f"{prefix}{n:05d}"
        if not db.query(Certificate.id).filter(Certificate.certificate_number == num).first():
            return num


def verification_url(cert: Certificate) -> str:
    return f"{settings.BASE_URL.rstrip('/')}/verify/{cert.certificate_number}"


def issue_certificate(db: Session, student: Student, course: Optional[Course], title: str, user: Optional[User], generation: str = "manual",
                      description: Optional[str] = None, issued_at: Optional[date] = None, request=None, generate_pdf: bool = True) -> Certificate:
    cert = Certificate(certificate_number=next_certificate_number(db, (issued_at or date.today()).year), student_id=student.id,
                       course_id=course.id if course else None, title=title, description=description, issued_at=issued_at or date.today(),
                       issued_by_id=user.id if user else None, generation=generation)
    db.add(cert)
    db.flush()
    if generate_pdf:
        try:
            generate_certificate_pdf(db, cert)
        except Exception as exc:  # pragma: no cover
            log.warning("certificate pdf failed: %s", exc)
    if user is not None:
        log_action(db, user, "issue", "certificates", entity=cert, description=f"Issued {cert.certificate_number} to {student.full_name}: {title} ({generation})",
                   after=snapshot(cert), request=request)
    client = student.client
    link = f"/{cert.pdf_path}" if cert.pdf_path else f"/verify/{cert.certificate_number}"
    if client and client.user_id:
        notify(db, client.user_id, f"Certificate issued: {student.full_name}", f"{title} — certificate number {cert.certificate_number}. Verify at {verification_url(cert)}",
               event_type="certificate", link=link, channels=("in_app",))
    if student.user_id:
        notify(db, student.user_id, "You earned a certificate!", f"{title} — {cert.certificate_number}", event_type="certificate", link=link)
    return cert


def revoke_certificate(db: Session, cert: Certificate, user: User, reason: str, request=None) -> None:
    if not (reason or "").strip():
        raise AcademicError("A rationale is required to revoke a certificate.")
    before = snapshot(cert)
    cert.is_revoked = True
    log_action(db, user, "revoke", "certificates", entity=cert, description=f"Revoked {cert.certificate_number}", rationale=reason,
               before=before, after=snapshot(cert), severity="warning", consequential=True, request=request)


# =============================================================================== PDF: fonts + Arabic/Urdu shaping
_FONT_CANDIDATES = [
    ("OQCArabic", r"C:\Windows\Fonts\arial.ttf"), ("OQCArabic", r"C:\Windows\Fonts\tahoma.ttf"), ("OQCArabic", r"C:\Windows\Fonts\segoeui.ttf"),
    ("OQCArabic", "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"), ("OQCArabic", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ("OQCArabic", "/usr/share/fonts/truetype/freefont/FreeSerif.ttf"), ("OQCArabic", "/System/Library/Fonts/Supplemental/Arial.ttf"),
]
_font_state: dict = {"checked": False, "name": None, "bold": None}


def unicode_font() -> Optional[str]:
    """Register (once) a system TTF that covers Arabic presentation forms. Returns font name or None (English-only fallback)."""
    if _font_state["checked"]:
        return _font_state["name"]
    _font_state["checked"] = True
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        for name, path in _FONT_CANDIDATES:
            if os.path.exists(path):
                pdfmetrics.registerFont(TTFont(name, path))
                _font_state["name"] = name
                bold = path.replace("arial.ttf", "arialbd.ttf").replace("tahoma.ttf", "tahomabd.ttf").replace("segoeui.ttf", "segoeuib.ttf")
                if bold != path and os.path.exists(bold):
                    pdfmetrics.registerFont(TTFont(name + "-Bold", bold))
                    _font_state["bold"] = name + "-Bold"
                break
    except Exception as exc:  # pragma: no cover
        log.warning("unicode font registration failed: %s", exc)
    return _font_state["name"]


# Presentation forms: (isolated, final, initial, medial) — None where the letter does not join.
_SHAPES: dict[str, tuple] = {
    "\u0621": ("\uFE80", None, None, None), "\u0622": ("\uFE81", "\uFE82", None, None), "\u0623": ("\uFE83", "\uFE84", None, None),
    "\u0624": ("\uFE85", "\uFE86", None, None), "\u0625": ("\uFE87", "\uFE88", None, None), "\u0626": ("\uFE89", "\uFE8A", "\uFE8B", "\uFE8C"),
    "\u0627": ("\uFE8D", "\uFE8E", None, None), "\u0628": ("\uFE8F", "\uFE90", "\uFE91", "\uFE92"), "\u0629": ("\uFE93", "\uFE94", None, None),
    "\u062A": ("\uFE95", "\uFE96", "\uFE97", "\uFE98"), "\u062B": ("\uFE99", "\uFE9A", "\uFE9B", "\uFE9C"), "\u062C": ("\uFE9D", "\uFE9E", "\uFE9F", "\uFEA0"),
    "\u062D": ("\uFEA1", "\uFEA2", "\uFEA3", "\uFEA4"), "\u062E": ("\uFEA5", "\uFEA6", "\uFEA7", "\uFEA8"), "\u062F": ("\uFEA9", "\uFEAA", None, None),
    "\u0630": ("\uFEAB", "\uFEAC", None, None), "\u0631": ("\uFEAD", "\uFEAE", None, None), "\u0632": ("\uFEAF", "\uFEB0", None, None),
    "\u0633": ("\uFEB1", "\uFEB2", "\uFEB3", "\uFEB4"), "\u0634": ("\uFEB5", "\uFEB6", "\uFEB7", "\uFEB8"), "\u0635": ("\uFEB9", "\uFEBA", "\uFEBB", "\uFEBC"),
    "\u0636": ("\uFEBD", "\uFEBE", "\uFEBF", "\uFEC0"), "\u0637": ("\uFEC1", "\uFEC2", "\uFEC3", "\uFEC4"), "\u0638": ("\uFEC5", "\uFEC6", "\uFEC7", "\uFEC8"),
    "\u0639": ("\uFEC9", "\uFECA", "\uFECB", "\uFECC"), "\u063A": ("\uFECD", "\uFECE", "\uFECF", "\uFED0"), "\u0641": ("\uFED1", "\uFED2", "\uFED3", "\uFED4"),
    "\u0642": ("\uFED5", "\uFED6", "\uFED7", "\uFED8"), "\u0643": ("\uFED9", "\uFEDA", "\uFEDB", "\uFEDC"), "\u0644": ("\uFEDD", "\uFEDE", "\uFEDF", "\uFEE0"),
    "\u0645": ("\uFEE1", "\uFEE2", "\uFEE3", "\uFEE4"), "\u0646": ("\uFEE5", "\uFEE6", "\uFEE7", "\uFEE8"), "\u0647": ("\uFEE9", "\uFEEA", "\uFEEB", "\uFEEC"),
    "\u0648": ("\uFEED", "\uFEEE", None, None), "\u0649": ("\uFEEF", "\uFEF0", None, None), "\u064A": ("\uFEF1", "\uFEF2", "\uFEF3", "\uFEF4"),
    # Urdu / Persian letters (Forms-A)
    "\u067E": ("\uFB56", "\uFB57", "\uFB58", "\uFB59"), "\u0679": ("\uFB66", "\uFB67", "\uFB68", "\uFB69"), "\u0686": ("\uFB7A", "\uFB7B", "\uFB7C", "\uFB7D"),
    "\u0688": ("\uFB88", "\uFB89", None, None), "\u0691": ("\uFB8C", "\uFB8D", None, None), "\u0698": ("\uFB8A", "\uFB8B", None, None),
    "\u06A9": ("\uFB8E", "\uFB8F", "\uFB90", "\uFB91"), "\u06AF": ("\uFB92", "\uFB93", "\uFB94", "\uFB95"), "\u06BA": ("\uFB9E", "\uFB9F", None, None),
    "\u06BE": ("\uFBAA", "\uFBAB", "\uFBAC", "\uFBAD"), "\u06C1": ("\uFBA6", "\uFBA7", "\uFBA8", "\uFBA9"), "\u06CC": ("\uFBFC", "\uFBFD", "\uFBFE", "\uFBFF"),
    "\u06D2": ("\uFBAE", "\uFBAF", None, None), "\u06D3": ("\uFBB0", "\uFBB1", None, None), "\u06C0": ("\uFBA4", "\uFBA5", None, None),
    "\u06C2": ("\uFBA6", "\uFBA7", "\uFBA8", "\uFBA9"),
}
_LAM_ALEF = {"\u0622": ("\uFEF5", "\uFEF6"), "\u0623": ("\uFEF7", "\uFEF8"), "\u0625": ("\uFEF9", "\uFEFA"), "\u0627": ("\uFEFB", "\uFEFC")}
_MARKS = set("\u064B\u064C\u064D\u064E\u064F\u0650\u0651\u0652\u0653\u0654\u0655\u0656\u0670\u06D6\u06D7\u06D8\u06D9\u06DA\u06DB\u06DC\u06DF\u06E0\u06E1\u06E2\u06E3\u06E4\u06E5\u06E6\u06E7\u06E8\u06EA\u06EB\u06EC\u06ED\u0640")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")


def _shape_word(word: str) -> str:
    """Contextual shaping of one Arabic/Urdu word into presentation forms (visual order, right-to-left reversed)."""
    letters = [c for c in word if c not in _MARKS]  # marks are transparent for joining
    # build list of (base, marks) units
    units: list[list[str]] = []
    for c in word:
        if c in _MARKS and units:
            units[-1].append(c)
        else:
            units.append([c])
    # lam-alef ligature merge
    merged: list[list[str]] = []
    i = 0
    while i < len(units):
        u = units[i]
        if u[0] == "\u0644" and i + 1 < len(units) and units[i + 1][0] in _LAM_ALEF:
            merged.append(["LA" + units[i + 1][0]] + u[1:] + units[i + 1][1:])
            i += 2
        else:
            merged.append(u)
            i += 1
    units = merged

    def joins_prev(u) -> bool:  # can this unit connect to the previous one (has final/medial)?
        b = u[0]
        if b.startswith("LA"):
            return True
        s = _SHAPES.get(b)
        return bool(s and s[1])

    def joins_next(u) -> bool:  # can this unit connect to the next one (has initial/medial)?
        b = u[0]
        if b.startswith("LA"):
            return False
        s = _SHAPES.get(b)
        return bool(s and s[2])

    out: list[str] = []
    for idx, u in enumerate(units):
        b = u[0]
        prev_join = idx > 0 and joins_next(units[idx - 1]) and joins_prev(u)
        next_join = idx + 1 < len(units) and joins_next(u) and joins_prev(units[idx + 1])
        if b.startswith("LA"):
            iso, fin = _LAM_ALEF[b[2:]]
            glyph = fin if prev_join else iso
        else:
            s = _SHAPES.get(b)
            if not s:
                glyph = b
            elif prev_join and next_join and s[3]:
                glyph = s[3]
            elif prev_join and s[1]:
                glyph = s[1]
            elif next_join and s[2]:
                glyph = s[2]
            else:
                glyph = s[0]
        out.append(glyph + "".join(u[1:]))
    return "".join(reversed(out))


def shape_rtl(text: str) -> str:
    """Convert a logical-order Arabic/Urdu string into visual order for reportlab (no bidi engine available).
    Words are reversed; Arabic words are shaped; Latin/number words keep their order. Brackets are mirrored."""
    if not text:
        return ""
    words = text.split(" ")
    visual = []
    for w in reversed(words):
        if _ARABIC_RE.search(w):
            visual.append(_shape_word(w))
        else:
            visual.append(w.translate(str.maketrans("()[]", ")(][")))
    return " ".join(visual)


def is_rtl(text: str) -> bool:
    return bool(text and _ARABIC_RE.search(text))


# =============================================================================== PDF: result card
def _pdf_common(c, width, height, title_en: str, title_ur: str, ufont: Optional[str]):
    from reportlab.lib import colors
    brand = colors.HexColor(BRAND)
    c.setFillColor(brand)
    c.rect(0, height - 26 * 2.83, width, 26 * 2.83, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, height - 40, ORG_NAME)
    c.setFont("Helvetica", 9)
    c.drawString(40, height - 54, "Digital Operating System · Academic Records")
    if ufont:
        c.setFont(ufont, 15)
        c.drawRightString(width - 40, height - 42, shape_rtl(ORG_NAME_UR))
        c.setFont(ufont, 10)
        c.drawRightString(width - 40, height - 58, shape_rtl(title_ur))
    c.setFillColor(brand)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, height - 96, title_en)


def generate_result_card_pdf(db: Session, test: MonthlyTest) -> str:
    """Branded bilingual (English + Urdu) monthly result card. Returns storage-relative path."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    student = test.student
    fname = f"result_card_{student.student_code}_{test.period}.pdf"
    path = CARD_DIR / fname
    ufont = unicode_font()
    ubold = _font_state.get("bold") or ufont
    width, height = A4
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"Result card {student.full_name} {test.period}")
    _pdf_common(c, width, height, f"Monthly Result Card — {period_label(test.period)}", "ماہانہ رزلٹ کارڈ", ufont)
    brand = colors.HexColor(BRAND)
    grey = colors.HexColor("#475569")
    line = colors.HexColor("#cbd5e1")

    def bilingual(y, label_en, label_ur, value, x=40, colw=250):
        c.setFillColor(grey)
        c.setFont("Helvetica", 8)
        c.drawString(x, y + 11, label_en.upper())
        if ufont:
            c.setFont(ufont, 9)
            c.drawRightString(x + colw, y + 11, shape_rtl(label_ur))
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x, y - 2, str(value or "—"))

    y = height - 130
    bilingual(y, "Student", "طالب علم", f"{student.full_name} ({student.student_code})")
    bilingual(y, "Course", "کورس", (student.course.name if student.course else "—") + (f" · {student.division.name}" if student.division else ""), x=310)
    y -= 34
    bilingual(y, "Teacher", "استاد", test.teacher.full_name if test.teacher else "—")
    bilingual(y, "Guardian", "سرپرست", student.client.full_name if student.client else "—", x=310)
    y -= 34
    bilingual(y, "Period", "مہینہ", period_label(test.period))
    bilingual(y, "Current position", "موجودہ سبق", (test.generated_from or {}).get("sabaq_position") or student.sabaq_position or "—", x=310)
    y -= 30
    c.setStrokeColor(line)
    c.line(40, y, width - 40, y)
    # questions table
    y -= 18
    c.setFillColor(brand)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "#")
    c.drawString(60, y, "Assessment item")
    c.drawString(390, y, "Type")
    c.drawRightString(480, y, "Max")
    c.drawRightString(550, y, "Score")
    if ufont:
        c.setFont(ufont, 9)
        c.drawRightString(380, y, shape_rtl("سوالات"))
    y -= 6
    c.line(40, y, width - 40, y)
    c.setFillColor(colors.black)
    for i, q in enumerate(test.questions or [], start=1):
        y -= 16
        if y < 150:
            c.showPage()
            y = height - 60
        c.setFont("Helvetica", 9)
        c.drawString(40, y, str(i))
        c.drawString(60, y, (q.get("item") or "")[:70])
        c.drawString(390, y, str(q.get("type", "")).title())
        c.drawRightString(480, y, f"{float(q.get('max', 10)):.0f}")
        c.setFont("Helvetica-Bold", 9)
        sc = q.get("score")
        c.drawRightString(550, y, "—" if sc is None else f"{float(sc):.1f}")
    y -= 10
    c.line(40, y, width - 40, y)
    # summary boxes
    y -= 70
    boxes = [("TOTAL", "کل نمبر", f"{test.score or 0:.1f} / {test.max_score:.0f}"),
             ("PERCENTAGE", "فیصد", f"{test.percentage or 0:.1f}%"),
             ("GRADE", "گریڈ", test.grade or "—"),
             ("PREVIOUS", "پچھلا مہینہ", f"{test.previous_percentage:.1f}%" if test.previous_percentage is not None else "—"),
             ("IMPROVEMENT", "بہتری", f"{test.improvement_pct:+.1f} pts" if test.improvement_pct is not None else "n/a")]
    bw = (width - 80) / len(boxes)
    for i, (en, ur, val) in enumerate(boxes):
        x = 40 + i * bw
        c.setFillColor(colors.HexColor("#f0fdfa"))
        c.setStrokeColor(brand)
        c.roundRect(x + 3, y, bw - 6, 56, 6, stroke=1, fill=1)
        c.setFillColor(grey)
        c.setFont("Helvetica", 7)
        c.drawString(x + 10, y + 44, en)
        if ufont:
            c.setFont(ufont, 8)
            c.drawRightString(x + bw - 10, y + 44, shape_rtl(ur))
        c.setFillColor(brand if en != "IMPROVEMENT" else (colors.HexColor("#059669") if (test.improvement_pct or 0) >= 0 else colors.HexColor("#e11d48")))
        c.setFont("Helvetica-Bold", 15 if en != "TOTAL" else 12)
        c.drawString(x + 10, y + 16, val)
    # remarks
    y -= 30
    c.setFillColor(brand)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "Teacher's remarks")
    if ufont:
        c.setFont(ufont, 10)
        c.drawRightString(width - 40, y, shape_rtl("استاد کے تاثرات"))
    y -= 16
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9.5)
    for ln in _wrap(test.teacher_remarks or "No remarks recorded.", 105):
        c.drawString(40, y, ln)
        y -= 13
    if test.teacher_remarks_urdu and ufont:
        y -= 4
        c.setFont(ufont, 11)
        for ln in _wrap(test.teacher_remarks_urdu, 80):
            c.drawRightString(width - 40, y, shape_rtl(ln))
            y -= 17
    # footer
    c.setStrokeColor(line)
    c.line(40, 70, width - 40, 70)
    c.setFillColor(grey)
    c.setFont("Helvetica", 8)
    c.drawString(40, 56, f"Generated {datetime.utcnow().strftime('%d %b %Y %H:%M')} UTC · Grade bands: A ≥ 85 · B ≥ 70 · C ≥ 55 · D < 55")
    c.drawString(40, 44, "Improvement is measured in percentage points against the previous month's test (institutional KPI).")
    if ufont:
        c.setFont(ufont, 9)
        c.drawRightString(width - 40, 52, shape_rtl("محنت اور دعا کے ساتھ آگے بڑھتے رہیں"))
    c.showPage()
    c.save()
    rel = f"storage/result_cards/{fname}"
    test.result_card_path = rel
    test.card_generated_at = datetime.utcnow()
    if test.status in ("generated", "scored"):
        test.status = "card_generated"
    return rel


def _wrap(text: str, width: int) -> list[str]:
    out: list[str] = []
    for para in (text or "").splitlines() or [""]:
        words = para.split()
        line = ""
        for w in words:
            if len(line) + len(w) + 1 > width and line:
                out.append(line)
                line = w
            else:
                line = (line + " " + w).strip()
        out.append(line)
    return out or [""]


# =============================================================================== PDF: certificate
def generate_certificate_pdf(db: Session, cert: Certificate) -> str:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    student = cert.student
    fname = f"{cert.certificate_number}.pdf"
    path = CERT_DIR / fname
    ufont = unicode_font()
    width, height = landscape(A4)
    c = canvas.Canvas(str(path), pagesize=landscape(A4))
    c.setTitle(f"Certificate {cert.certificate_number}")
    brand = colors.HexColor(BRAND)
    gold = colors.HexColor("#b45309")
    # borders
    c.setStrokeColor(brand)
    c.setLineWidth(6)
    c.rect(22, 22, width - 44, height - 44)
    c.setStrokeColor(gold)
    c.setLineWidth(1.5)
    c.rect(34, 34, width - 68, height - 68)
    c.setLineWidth(0.5)
    c.rect(40, 40, width - 80, height - 80)
    # corner ornaments
    for (x, y) in [(52, 52), (width - 52, 52), (52, height - 52), (width - 52, height - 52)]:
        c.setFillColor(gold)
        c.circle(x, y, 5, stroke=0, fill=1)
    # header
    if ufont:
        c.setFillColor(brand)
        c.setFont(ufont, 20)
        c.drawCentredString(width / 2, height - 82, shape_rtl("بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"))
    c.setFillColor(brand)
    c.setFont("Helvetica-Bold", 26)
    c.drawCentredString(width / 2, height - 118, ORG_NAME.upper())
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica", 10)
    c.drawCentredString(width / 2, height - 134, "ONLINE QURAN & ISLAMIC EDUCATION  ·  EST. FOR THE SERVICE OF THE HOLY QURAN")
    c.setFillColor(gold)
    c.setFont("Helvetica-Bold", 34)
    c.drawCentredString(width / 2, height - 190, cert.title.split("—")[0].strip().upper() if "—" in cert.title else "CERTIFICATE")
    c.setFillColor(colors.HexColor("#334155"))
    c.setFont("Helvetica-Oblique", 13)
    c.drawCentredString(width / 2, height - 222, "This is to certify that")
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 30)
    c.drawCentredString(width / 2, height - 262, student.full_name)
    c.setStrokeColor(gold)
    c.setLineWidth(1)
    c.line(width / 2 - 200, height - 272, width / 2 + 200, height - 272)
    c.setFillColor(colors.HexColor("#334155"))
    c.setFont("Helvetica", 13)
    course_txt = cert.course.name if cert.course else "the prescribed programme"
    c.drawCentredString(width / 2, height - 298, f"has successfully completed  {course_txt}")
    c.setFont("Helvetica", 11.5)
    desc = cert.description or cert.title
    yy = height - 320
    for ln in _wrap(desc, 110)[:3]:
        c.drawCentredString(width / 2, yy, ln)
        yy -= 16
    if ufont and cert.course and cert.course.urdu_name:
        c.setFont(ufont, 13)
        c.drawCentredString(width / 2, yy - 4, shape_rtl(f"{cert.course.urdu_name} کی کامیاب تکمیل پر یہ سند دی جاتی ہے"))
    # footer blocks
    base = 92
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(70, base + 14, "Date of issue")
    c.setFont("Helvetica", 10)
    c.drawString(70, base, cert.issued_at.strftime("%d %B %Y") if cert.issued_at else "—")
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(width / 2, base + 14, "Certificate number")
    c.setFont("Courier-Bold", 12)
    c.drawCentredString(width / 2, base, cert.certificate_number)
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(width - 70, base + 14, "Head of Academics")
    c.setFont("Helvetica-Oblique", 10)
    c.drawRightString(width - 70, base, "Online Quran College")
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.line(width - 230, base + 28, width - 70, base + 28)
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica", 8)
    c.drawCentredString(width / 2, 56, f"Verify authenticity at {verification_url(cert)}  ·  Student ID {student.student_code}  ·  Generated {datetime.utcnow().strftime('%d %b %Y')}")
    c.showPage()
    c.save()
    cert.pdf_path = f"storage/certificates/{fname}"
    return cert.pdf_path


# =============================================================================== Tajweed colouring (Module 28)
_VOWELS = set("\u064E\u064F\u0650\u064B\u064C\u064D\u0651")
_TANWEEN = set("\u064B\u064C\u064D")
_THROAT = set("ءأإهعحغخ")
_IDGHAM = set("يرملون")
_QALQALA = set("قطبجد")
_HAMZA = set("ءأإؤئ")
_STOP_SIGNS = set("\u06D6\u06D7\u06D8\u06D9\u06DA\u06DB\u06DC\u06DD\u06DE\u06E9\u06DF\u0670\u06E5\u06E6\u06E7\u06E8\u06EA\u06EB\u06EC\u06ED")


def _units(word: str) -> list[tuple[str, str]]:
    units: list[list[str]] = []
    for ch in word:
        if ch in _MARKS and units:
            units[-1].append(ch)
        else:
            units.append([ch])
    return [(u[0], "".join(u[1:])) for u in units]


def tajweed_word_html(word: str, next_word: Optional[str] = None) -> str:
    """Approximate Tajweed colouring: returns inner HTML with <span class="tj-*"> around affected letters."""
    units = _units(word)
    nxt_units = _units(next_word) if next_word else []
    parts: list[str] = []
    for i, (base, marks) in enumerate(units):
        cls = None
        following = units[i + 1][0] if i + 1 < len(units) else (nxt_units[0][0] if nxt_units else None)
        following_marks = units[i + 1][1] if i + 1 < len(units) else (nxt_units[0][1] if nxt_units else "")
        has_vowel = any(m in _VOWELS for m in marks)
        # sakin: carries sukoon, or (in imlaa'i text where the mark is often dropped) carries no vowel at all -
        # this includes word-final letters such as the noon of min / an / in, where ikhfa/idgham still apply
        sakin = ("\u0652" in marks) or (not has_vowel and base not in "اوى")
        tanween = any(m in _TANWEEN for m in marks)
        if base in "نم" and "\u0651" in marks:
            cls = "tj-ghunna"
        elif base == "ن" and sakin or tanween:
            if following == "ب":
                cls = "tj-iqlab"
            elif following in _IDGHAM:
                cls = "tj-idgham"
            elif following in _THROAT or following is None:
                cls = None  # izhar
            elif following and following.strip():
                cls = "tj-ikhfa"
        elif base == "م" and sakin and following in ("ب", "م"):
            cls = "tj-ikhfa" if following == "ب" else "tj-idgham"
        elif base in _QALQALA and "\u0652" in marks:
            cls = "tj-qalqala"
        elif "\u0653" in marks or base == "آ" or ("\u0670" in marks):
            cls = "tj-madd"
        elif base in "اوى" and not has_vowel and following in _HAMZA:
            cls = "tj-madd"
        elif base in "اوي" and i > 0 and not has_vowel and "\u0651" in following_marks:
            cls = "tj-madd"
        txt = html.escape(base + marks)
        parts.append(f'<span class="{cls}">{txt}</span>' if cls else txt)
    return "".join(parts)


def lesson_words(lesson: Optional[Lesson]) -> list[dict]:
    """Split a lesson's Arabic text into ayah lines and indexed words, ready for the shared view."""
    if not lesson or not lesson.arabic_text:
        return []
    lines = []
    idx = 0
    for n, raw in enumerate([ln for ln in lesson.arabic_text.splitlines() if ln.strip()], start=1):
        toks = raw.split()
        words = []
        for j, tok in enumerate(toks):
            if all(ch in _STOP_SIGNS or ch in "۝۞" for ch in tok):
                words.append({"index": None, "text": tok, "html": html.escape(tok), "sign": True})
                continue
            nxt = toks[j + 1] if j + 1 < len(toks) else None
            words.append({"index": idx, "text": tok, "html": tajweed_word_html(tok, nxt), "sign": False})
            idx += 1
        ayah_no = (lesson.ayah_from + n - 1) if lesson.ayah_from else n
        lines.append({"n": n, "ayah": ayah_no, "words": words, "text": raw})
    return lines


TAJWEED_LEGEND = [("tj-ghunna", "Ghunna (nasalisation on نّ / مّ)"), ("tj-ikhfa", "Ikhfa (hidden noon/meem)"), ("tj-idgham", "Idgham (merging)"),
                  ("tj-iqlab", "Iqlab (noon → meem before ب)"), ("tj-qalqala", "Qalqala (echo on ق ط ب ج د)"), ("tj-madd", "Madd (prolongation)")]


def annotations_for(db: Session, student_id: int, lesson_id: Optional[int]) -> list[dict]:
    q = db.query(LessonAnnotation).filter(LessonAnnotation.student_id == student_id)
    q = q.filter(LessonAnnotation.lesson_id == lesson_id) if lesson_id else q.filter(LessonAnnotation.lesson_id.is_(None))
    rows = q.order_by(LessonAnnotation.id).all()
    out = []
    for a in rows:
        author = db.get(User, a.author_id) if a.author_id else None
        out.append({"id": a.id, "word_index": a.word_index, "type": a.annotation_type, "color": a.color, "note": a.note,
                    "author": author.full_name if author else "—", "author_id": a.author_id, "at": a.created_at.isoformat() if a.created_at else None})
    return out
