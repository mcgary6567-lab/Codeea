"""REST API: academic module — courses, curriculum, progress, lesson plans, evaluations,
monthly tests / result cards and certificates.

Mounted at /api/v1/academics. Auth: Bearer token (POST /api/v1/auth/login) or X-API-Key.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.deps import get_user_context, require, UserContext
from app.database import get_db
from app.models.academic import (Book, Certificate, Chapter, Course, Division, DorSchedule, Evaluation, Lesson,
                                 LessonPlan, MonthlyTest, Package)
from app.models.core import User
from app.models.people import Student
from app.services import academic as svc

router = APIRouter(prefix="/academics", tags=["academics"])


# ------------------------------------------------------------------ schemas
class DivisionOut(BaseModel):
    id: int
    name: str
    order: int
    expected_weeks: Optional[int] = None


class PackageOut(BaseModel):
    id: int
    name: str
    course_id: Optional[int] = None
    sessions_per_week: int
    session_minutes: int
    price: float
    currency: str
    country: Optional[str] = None
    billing_cycle: str
    is_active: bool
    is_trial: bool


class CourseOut(BaseModel):
    id: int
    code: str
    name: str
    arabic_name: Optional[str] = None
    urdu_name: Optional[str] = None
    description: Optional[str] = None
    default_session_minutes: int
    completion_target_months: Optional[int] = None
    is_active: bool
    divisions: list[DivisionOut] = []
    packages: list[PackageOut] = []


class LessonOut(BaseModel):
    id: int
    title: str
    arabic_text: Optional[str] = None
    translation: Optional[str] = None
    objectives: Optional[str] = None
    tajweed_notes: Optional[str] = None
    expected_minutes: int
    order: int
    surah_number: Optional[int] = None
    ayah_from: Optional[int] = None
    ayah_to: Optional[int] = None


class ChapterOut(BaseModel):
    id: int
    title: str
    arabic_title: Optional[str] = None
    order: int
    lessons: list[LessonOut] = []


class BookOut(BaseModel):
    id: int
    title: str
    arabic_title: Optional[str] = None
    order: int
    chapters: list[ChapterOut] = []


class CurriculumOut(BaseModel):
    course: CourseOut
    version: Optional[str] = None
    books: list[BookOut] = []
    lesson_count: int


class ProgressRowOut(BaseModel):
    lesson_id: int
    lesson: str
    status: str
    progress_type: str
    score: Optional[float] = None
    completed_at: Optional[str] = None


class ProgressOut(BaseModel):
    student_id: int
    student_code: str
    full_name: str
    course: Optional[str] = None
    completed: int
    in_progress: int
    revision: int
    total: int
    pct: float
    current_lesson: Optional[str] = None
    current_lesson_id: Optional[int] = None
    sabaq_position: Optional[str] = None
    dor_quota_met: bool
    rows: list[ProgressRowOut] = []


class ProgressIn(BaseModel):
    lesson_id: int
    status: str = Field("completed", pattern="^(not_started|in_progress|completed|revision)$")
    progress_type: str = Field("sabaq", pattern="^(sabaq|sabqi|dor)$")
    score: Optional[float] = None
    notes: Optional[str] = None


class LessonPlanOut(BaseModel):
    id: int
    student_id: int
    student: str
    teacher_id: Optional[int] = None
    plan_date: date
    plan_type: str
    lesson_id: Optional[int] = None
    planned_content: str
    delivered_content: Optional[str] = None
    sabaq: Optional[str] = None
    sabqi: Optional[str] = None
    dor: Optional[str] = None
    status: str
    variance_pct: Optional[float] = None
    ai_recommendation: Optional[str] = None
    reviewed: bool = False


class LessonPlanIn(BaseModel):
    student_id: int
    plan_date: Optional[date] = None
    plan_type: str = Field("daily", pattern="^(daily|weekly)$")
    lesson_id: Optional[int] = None
    planned_content: str
    sabaq: Optional[str] = None
    sabqi: Optional[str] = None
    dor: Optional[str] = None
    next_objectives: Optional[str] = None


class DeliverIn(BaseModel):
    delivered_content: str
    teacher_notes: Optional[str] = None
    variance_pct: Optional[float] = None


class EvaluationOut(BaseModel):
    id: int
    student_id: int
    student: str
    evaluation_type: str
    date: date
    score: Optional[float] = None
    max_score: float
    result: str
    criteria: dict = {}
    teacher_comment: Optional[str] = None
    academic_comment: Optional[str] = None


class EvaluationIn(BaseModel):
    student_id: int
    evaluation_type: str = Field("manual", pattern="^(manual|weekly|monthly|level_completion|trial)$")
    date: Optional[date] = None
    criteria: dict = {}
    score: Optional[float] = None
    max_score: float = 100
    result: Optional[str] = None
    teacher_comment: Optional[str] = None
    academic_comment: Optional[str] = None


class MonthlyTestOut(BaseModel):
    id: int
    student_id: int
    student: str
    student_code: str
    period: str
    status: str
    score: Optional[float] = None
    max_score: float
    percentage: Optional[float] = None
    previous_percentage: Optional[float] = None
    improvement_pct: Optional[float] = None
    grade: Optional[str] = None
    teacher_remarks: Optional[str] = None
    teacher_remarks_urdu: Optional[str] = None
    result_card_url: Optional[str] = None
    delivered_at: Optional[str] = None
    delivery_channels: list = []
    questions: list = []


class ScoreIn(BaseModel):
    scores: dict[str, float]
    teacher_remarks: str
    teacher_remarks_urdu: Optional[str] = None


class GenerateIn(BaseModel):
    period: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}$")


class DorOut(BaseModel):
    id: int
    student_id: int
    period: str
    quota: int
    completed: int
    quota_met: bool
    items: list = []


class CertificateOut(BaseModel):
    id: int
    certificate_number: str
    student_id: int
    student: str
    course: Optional[str] = None
    title: str
    description: Optional[str] = None
    issued_at: date
    generation: str
    is_revoked: bool
    verification_count: int
    verification_url: str
    pdf_url: Optional[str] = None


class CertificateIn(BaseModel):
    student_id: int
    title: str
    course_id: Optional[int] = None
    description: Optional[str] = None
    issued_at: Optional[date] = None


class VerifyOut(BaseModel):
    certificate_number: str
    valid: bool
    reason: Optional[str] = None
    student: Optional[str] = None
    course: Optional[str] = None
    title: Optional[str] = None
    issued_at: Optional[date] = None
    verification_count: int = 0


# ------------------------------------------------------------------ helpers
def _scope(user: User, ctx: UserContext) -> Optional[list[int]]:
    if user.is_superuser or rbac.is_management(user):
        return None
    if ctx.teacher or ctx.client or ctx.student:
        return list(ctx.student_ids)
    return None


def _student(db: Session, sid: int, user: User, ctx: UserContext) -> Student:
    s = db.get(Student, sid)
    if not s:
        raise HTTPException(404, "Student not found")
    scope = _scope(user, ctx)
    if scope is not None and s.id not in scope:
        raise HTTPException(403, "Student is outside your scope")
    return s


def _course_out(c: Course) -> CourseOut:
    return CourseOut(id=c.id, code=c.code, name=c.name, arabic_name=c.arabic_name, urdu_name=c.urdu_name,
                     description=c.description, default_session_minutes=c.default_session_minutes,
                     completion_target_months=c.completion_target_months, is_active=c.is_active,
                     divisions=[DivisionOut(id=d.id, name=d.name, order=d.order, expected_weeks=d.expected_weeks) for d in c.divisions],
                     packages=[_package_out(p) for p in c.packages])


def _package_out(p: Package) -> PackageOut:
    return PackageOut(id=p.id, name=p.name, course_id=p.course_id, sessions_per_week=p.sessions_per_week,
                      session_minutes=p.session_minutes, price=float(p.price or 0), currency=p.currency,
                      country=p.country, billing_cycle=p.billing_cycle, is_active=p.is_active, is_trial=p.is_trial)


def _lesson_out(l: Lesson) -> LessonOut:
    return LessonOut(id=l.id, title=l.title, arabic_text=l.arabic_text, translation=l.translation,
                     objectives=l.objectives, tajweed_notes=l.tajweed_notes, expected_minutes=l.expected_minutes,
                     order=l.order, surah_number=l.surah_number, ayah_from=l.ayah_from, ayah_to=l.ayah_to)


def _plan_out(p: LessonPlan) -> LessonPlanOut:
    return LessonPlanOut(id=p.id, student_id=p.student_id, student=p.student.full_name if p.student else "",
                         teacher_id=p.teacher_id, plan_date=p.plan_date, plan_type=p.plan_type, lesson_id=p.lesson_id,
                         planned_content=p.planned_content, delivered_content=p.delivered_content, sabaq=p.sabaq,
                         sabqi=p.sabqi, dor=p.dor, status=p.status, variance_pct=p.variance_pct,
                         ai_recommendation=p.ai_recommendation, reviewed=bool(p.reviewed_at))


def _eval_out(e: Evaluation) -> EvaluationOut:
    return EvaluationOut(id=e.id, student_id=e.student_id, student=e.student.full_name if e.student else "",
                         evaluation_type=e.evaluation_type, date=e.date, score=e.score, max_score=e.max_score,
                         result=e.result, criteria=e.criteria or {}, teacher_comment=e.teacher_comment,
                         academic_comment=e.academic_comment)


def _test_out(t: MonthlyTest) -> MonthlyTestOut:
    return MonthlyTestOut(id=t.id, student_id=t.student_id, student=t.student.full_name if t.student else "",
                          student_code=t.student.student_code if t.student else "", period=t.period, status=t.status,
                          score=t.score, max_score=t.max_score, percentage=t.percentage,
                          previous_percentage=t.previous_percentage, improvement_pct=t.improvement_pct, grade=t.grade,
                          teacher_remarks=t.teacher_remarks, teacher_remarks_urdu=t.teacher_remarks_urdu,
                          result_card_url=f"/{t.result_card_path}" if t.result_card_path else None,
                          delivered_at=t.delivered_at.isoformat() if t.delivered_at else None,
                          delivery_channels=list(t.delivery_channels or []), questions=list(t.questions or []))


def _cert_out(c: Certificate) -> CertificateOut:
    return CertificateOut(id=c.id, certificate_number=c.certificate_number, student_id=c.student_id,
                          student=c.student.full_name if c.student else "", course=c.course.name if c.course else None,
                          title=c.title, description=c.description, issued_at=c.issued_at, generation=c.generation,
                          is_revoked=c.is_revoked, verification_count=c.verification_count,
                          verification_url=svc.verification_url(c),
                          pdf_url=f"/{c.pdf_path}" if c.pdf_path else None)


# ------------------------------------------------------------------ courses & curriculum
@router.get("/courses", response_model=list[CourseOut])
def api_courses(active_only: bool = True, db: Session = Depends(get_db), user: User = Depends(require("courses.view"))):
    q = db.query(Course)
    if active_only:
        q = q.filter(Course.is_active.is_(True))
    return [_course_out(c) for c in q.order_by(Course.order, Course.id).all()]


@router.get("/courses/{course_id}", response_model=CourseOut)
def api_course(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("courses.view"))):
    c = db.get(Course, course_id)
    if not c:
        raise HTTPException(404, "Course not found")
    return _course_out(c)


@router.get("/packages", response_model=list[PackageOut])
def api_packages(country: str = "", db: Session = Depends(get_db), user: User = Depends(require("packages.view"))):
    q = db.query(Package).filter(Package.is_active.is_(True))
    if country:
        q = q.filter(Package.country == country)
    return [_package_out(p) for p in q.order_by(Package.course_id, Package.price).all()]


@router.get("/courses/{course_id}/curriculum", response_model=CurriculumOut)
def api_curriculum(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("curriculum.view"))):
    c = db.get(Course, course_id)
    if not c:
        raise HTTPException(404, "Course not found")
    from app.models.academic import CurriculumVersion
    ver = (db.query(CurriculumVersion).filter(CurriculumVersion.course_id == c.id,
                                              CurriculumVersion.is_current.is_(True)).first())
    books = db.query(Book).filter(Book.course_id == c.id).order_by(Book.order, Book.id).all()
    out_books = [BookOut(id=b.id, title=b.title, arabic_title=b.arabic_title, order=b.order,
                         chapters=[ChapterOut(id=ch.id, title=ch.title, arabic_title=ch.arabic_title, order=ch.order,
                                              lessons=[_lesson_out(l) for l in ch.lessons]) for ch in b.chapters])
                 for b in books]
    return CurriculumOut(course=_course_out(c), version=ver.version if ver else None, books=out_books,
                         lesson_count=sum(len(ch.lessons) for b in out_books for ch in b.chapters))


@router.get("/lessons/{lesson_id}", response_model=LessonOut)
def api_lesson(lesson_id: int, db: Session = Depends(get_db), user: User = Depends(require("curriculum.view"))):
    l = db.get(Lesson, lesson_id)
    if not l:
        raise HTTPException(404, "Lesson not found")
    return _lesson_out(l)


# ------------------------------------------------------------------ progress
@router.get("/students/{student_id}/progress", response_model=ProgressOut)
def api_progress(student_id: int, db: Session = Depends(get_db),
                 user: User = Depends(require("students.view", "curriculum.view", any_of=True)),
                 ctx: UserContext = Depends(get_user_context)):
    s = _student(db, student_id, user, ctx)
    summary = svc.student_progress_summary(db, s)
    pm = svc.progress_map(db, s.id)
    rows = []
    for les in svc.course_lessons(db, s.course_id):
        p = pm.get(les.id)
        if not p:
            continue
        rows.append(ProgressRowOut(lesson_id=les.id, lesson=les.title, status=p.status, progress_type=p.progress_type,
                                   score=p.score, completed_at=p.completed_at.isoformat() if p.completed_at else None))
    return ProgressOut(student_id=s.id, student_code=s.student_code, full_name=s.full_name,
                       course=s.course.name if s.course else None, completed=summary["completed"],
                       in_progress=summary["in_progress"], revision=summary["revision"], total=summary["total"],
                       pct=summary["pct"], current_lesson=summary["current_lesson_title"],
                       current_lesson_id=summary["current_lesson"].id if summary["current_lesson"] else None,
                       sabaq_position=s.sabaq_position, dor_quota_met=s.dor_quota_met, rows=rows)


@router.post("/students/{student_id}/progress", response_model=ProgressOut)
def api_record_progress(student_id: int, body: ProgressIn, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("lesson_plans.update", "curriculum.update", "students.update", any_of=True)),
                        ctx: UserContext = Depends(get_user_context)):
    s = _student(db, student_id, user, ctx)
    les = db.get(Lesson, body.lesson_id)
    if not les:
        raise HTTPException(404, "Lesson not found")
    try:
        svc.record_progress(db, s, les, body.status, user, progress_type=body.progress_type, score=body.score,
                            notes=body.notes, request=request)
    except svc.DorQuotaBlocked as exc:
        raise HTTPException(409, str(exc))
    except svc.AcademicError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return api_progress(student_id, db, user, ctx)


@router.get("/students/{student_id}/recommendation")
def api_recommendation(student_id: int, db: Session = Depends(get_db),
                       user: User = Depends(require("lesson_plans.view")), ctx: UserContext = Depends(get_user_context)):
    s = _student(db, student_id, user, ctx)
    text = svc.recommend_lesson(db, s, None, user)
    db.commit()
    return {"student_id": s.id, "recommendation": text,
            "current_lesson": svc.lesson_path(svc.current_lesson_for(db, s))}


@router.get("/students/{student_id}/dor", response_model=list[DorOut])
def api_dor(student_id: int, db: Session = Depends(get_db), user: User = Depends(require("monthly_tests.view")),
            ctx: UserContext = Depends(get_user_context)):
    s = _student(db, student_id, user, ctx)
    rows = db.query(DorSchedule).filter(DorSchedule.student_id == s.id).order_by(DorSchedule.period.desc()).all()
    return [DorOut(id=d.id, student_id=d.student_id, period=d.period, quota=d.quota, completed=d.completed,
                   quota_met=d.quota_met, items=list(d.items or [])) for d in rows]


# ------------------------------------------------------------------ lesson plans
@router.get("/lesson-plans", response_model=list[LessonPlanOut])
def api_plans(student_id: int = 0, teacher_id: int = 0, status: str = "", limit: int = 100,
              db: Session = Depends(get_db), user: User = Depends(require("lesson_plans.view")),
              ctx: UserContext = Depends(get_user_context)):
    q = db.query(LessonPlan)
    scope = _scope(user, ctx)
    if scope is not None:
        q = q.filter(LessonPlan.student_id.in_(scope or [0]))
    if student_id:
        q = q.filter(LessonPlan.student_id == student_id)
    if teacher_id:
        q = q.filter(LessonPlan.teacher_id == teacher_id)
    if status:
        q = q.filter(LessonPlan.status == status)
    return [_plan_out(p) for p in q.order_by(LessonPlan.plan_date.desc()).limit(min(limit, 500)).all()]


@router.post("/lesson-plans", response_model=LessonPlanOut, status_code=201)
def api_plan_create(body: LessonPlanIn, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("lesson_plans.add")), ctx: UserContext = Depends(get_user_context)):
    s = _student(db, body.student_id, user, ctx)
    from app.core.audit import log_action
    p = LessonPlan(student_id=s.id, teacher_id=s.teacher_id, plan_date=body.plan_date or date.today(),
                   plan_type=body.plan_type, lesson_id=body.lesson_id or s.current_lesson_id,
                   planned_content=body.planned_content, sabaq=body.sabaq, sabqi=body.sabqi, dor=body.dor,
                   next_objectives=body.next_objectives, status="planned")
    db.add(p)
    db.flush()
    log_action(db, user, "create", "lesson_plans", entity=p, request=request,
               description=f"Lesson plan created via API for {s.full_name} on {p.plan_date}")
    db.commit()
    return _plan_out(p)


@router.post("/lesson-plans/{plan_id}/deliver", response_model=LessonPlanOut)
def api_plan_deliver(plan_id: int, body: DeliverIn, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("lesson_plans.update")), ctx: UserContext = Depends(get_user_context)):
    p = db.get(LessonPlan, plan_id)
    if not p:
        raise HTTPException(404, "Lesson plan not found")
    _student(db, p.student_id, user, ctx)
    svc.mark_plan_delivered(db, p, body.delivered_content, body.teacher_notes, user, body.variance_pct, request=request)
    db.commit()
    return _plan_out(p)


# ------------------------------------------------------------------ evaluations
@router.get("/evaluations", response_model=list[EvaluationOut])
def api_evaluations(student_id: int = 0, result: str = "", limit: int = 100, db: Session = Depends(get_db),
                    user: User = Depends(require("evaluations.view")), ctx: UserContext = Depends(get_user_context)):
    q = db.query(Evaluation)
    scope = _scope(user, ctx)
    if scope is not None:
        q = q.filter(Evaluation.student_id.in_(scope or [0]))
    if student_id:
        q = q.filter(Evaluation.student_id == student_id)
    if result:
        q = q.filter(Evaluation.result == result)
    return [_eval_out(e) for e in q.order_by(Evaluation.date.desc()).limit(min(limit, 500)).all()]


@router.post("/evaluations", response_model=EvaluationOut, status_code=201)
def api_evaluation_create(body: EvaluationIn, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("evaluations.add")), ctx: UserContext = Depends(get_user_context)):
    s = _student(db, body.student_id, user, ctx)
    from app.core.audit import log_action
    criteria = {k: max(0.0, min(10.0, float(body.criteria.get(k, 0) or 0))) for k in svc.EVAL_CRITERIA}
    score = body.score if body.score is not None else svc.evaluation_score(criteria)
    result = body.result or ("pass" if score >= body.max_score * 0.55 else "fail")
    e = Evaluation(student_id=s.id, teacher_id=s.teacher_id, evaluation_type=body.evaluation_type,
                   date=body.date or date.today(), score=score, max_score=body.max_score, result=result,
                   criteria=criteria, teacher_comment=body.teacher_comment, academic_comment=body.academic_comment)
    db.add(e)
    db.flush()
    log_action(db, user, "create", "evaluations", entity=e, request=request,
               description=f"Evaluation created via API for {s.full_name}: {score}/{body.max_score} ({result})")
    db.commit()
    return _eval_out(e)


# ------------------------------------------------------------------ monthly tests
@router.get("/monthly-tests", response_model=list[MonthlyTestOut])
def api_tests(period: str = "", student_id: int = 0, status: str = "", limit: int = 200,
              db: Session = Depends(get_db), user: User = Depends(require("monthly_tests.view")),
              ctx: UserContext = Depends(get_user_context)):
    q = db.query(MonthlyTest)
    scope = _scope(user, ctx)
    if scope is not None:
        q = q.filter(MonthlyTest.student_id.in_(scope or [0]))
    if period:
        q = q.filter(MonthlyTest.period == period)
    if student_id:
        q = q.filter(MonthlyTest.student_id == student_id)
    if status:
        q = q.filter(MonthlyTest.status == status)
    return [_test_out(t) for t in q.order_by(MonthlyTest.period.desc(), MonthlyTest.id.desc()).limit(min(limit, 500)).all()]


@router.get("/monthly-tests/{test_id}", response_model=MonthlyTestOut)
def api_test(test_id: int, db: Session = Depends(get_db), user: User = Depends(require("monthly_tests.view")),
             ctx: UserContext = Depends(get_user_context)):
    t = db.get(MonthlyTest, test_id)
    if not t:
        raise HTTPException(404, "Monthly test not found")
    _student(db, t.student_id, user, ctx)
    return _test_out(t)


@router.post("/monthly-tests/generate")
def api_generate_tests(body: GenerateIn, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("monthly_tests.add"))):
    period = body.period or svc.period_of()
    created = svc.generate_tests_for_period(db, period, user, request=request)
    db.commit()
    return {"period": period, "created": created}


@router.post("/monthly-tests/{test_id}/score", response_model=MonthlyTestOut)
def api_score_test(test_id: int, body: ScoreIn, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("monthly_tests.update")), ctx: UserContext = Depends(get_user_context)):
    t = db.get(MonthlyTest, test_id)
    if not t:
        raise HTTPException(404, "Monthly test not found")
    _student(db, t.student_id, user, ctx)
    if not body.teacher_remarks.strip():
        raise HTTPException(400, "teacher_remarks is required")
    svc.score_monthly_test(db, t, body.scores, body.teacher_remarks, body.teacher_remarks_urdu, user, request=request)
    db.commit()
    return _test_out(t)


@router.post("/monthly-tests/{test_id}/deliver", response_model=MonthlyTestOut)
def api_deliver_test(test_id: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("monthly_tests.update"))):
    t = db.get(MonthlyTest, test_id)
    if not t:
        raise HTTPException(404, "Monthly test not found")
    try:
        svc.deliver_result_card(db, t, user, request=request)
    except svc.AcademicError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return _test_out(t)


@router.get("/reports/improvement")
def api_improvement(period: str = "", teacher_id: int = 0, db: Session = Depends(get_db),
                    user: User = Depends(require("monthly_tests.view")), ctx: UserContext = Depends(get_user_context)):
    period = period or svc.previous_period(svc.period_of())
    rep = svc.improvement_report(db, period, teacher_id or None, _scope(user, ctx))
    return {"period": period, "n": rep["n"], "avg_pct": rep["avg_pct"], "avg_improvement": rep["avg_imp"],
            "declines": rep["declines"], "grade_distribution": rep["grade_dist"],
            "teachers": [{"teacher": r["teacher"].full_name if r["teacher"] else None, "n": r["n"],
                          "avg_pct": r["avg_pct"], "avg_improvement": r["avg_imp"], "declines": r["declines"]}
                         for r in rep["teachers"]],
            "classes": [{"label": c["label"], "n": c["n"], "avg_pct": c["avg_pct"], "avg_improvement": c["avg_imp"]}
                        for c in rep["classes"]]}


@router.get("/reports/syllabus-compliance")
def api_compliance(course_id: int = 0, teacher_id: int = 0, db: Session = Depends(get_db),
                   user: User = Depends(require("curriculum.view")), ctx: UserContext = Depends(get_user_context)):
    rows = svc.syllabus_compliance(db, course_id or None, teacher_id or None, _scope(user, ctx))
    return [{"student_id": r["student"].id, "student_code": r["student"].student_code, "student": r["student"].full_name,
             "course": r["course"].name if r["course"] else None,
             "teacher": r["teacher"].full_name if r["teacher"] else None,
             "completed": r["completed"], "expected": r["expected"], "total": r["total"], "gap": r["gap"],
             "compliance": r["compliance"], "delivery_rate": r["delivery_rate"], "status": r["status"]} for r in rows]


# ------------------------------------------------------------------ certificates
@router.get("/certificates", response_model=list[CertificateOut])
def api_certificates(student_id: int = 0, limit: int = 100, db: Session = Depends(get_db),
                     user: User = Depends(require("certificates.view")), ctx: UserContext = Depends(get_user_context)):
    q = db.query(Certificate)
    scope = _scope(user, ctx)
    if scope is not None:
        q = q.filter(Certificate.student_id.in_(scope or [0]))
    if student_id:
        q = q.filter(Certificate.student_id == student_id)
    return [_cert_out(c) for c in q.order_by(Certificate.issued_at.desc()).limit(min(limit, 500)).all()]


@router.post("/certificates", response_model=CertificateOut, status_code=201)
def api_certificate_create(body: CertificateIn, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require("certificates.add")), ctx: UserContext = Depends(get_user_context)):
    s = _student(db, body.student_id, user, ctx)
    course = db.get(Course, body.course_id) if body.course_id else s.course
    cert = svc.issue_certificate(db, s, course, body.title, user, generation="manual", description=body.description,
                                 issued_at=body.issued_at or date.today(), request=request)
    db.commit()
    return _cert_out(cert)


@router.get("/certificates/verify/{certificate_number}", response_model=VerifyOut)
def api_verify(certificate_number: str, db: Session = Depends(get_db)):
    """Public certificate verification (no authentication required)."""
    number = (certificate_number or "").strip().upper()
    c = db.query(Certificate).filter(Certificate.certificate_number == number).first()
    if not c:
        return VerifyOut(certificate_number=number, valid=False, reason="No certificate with this number was issued.")
    c.verification_count = (c.verification_count or 0) + 1
    db.commit()
    return VerifyOut(certificate_number=c.certificate_number, valid=not c.is_revoked,
                     reason="This certificate has been revoked." if c.is_revoked else None,
                     student=c.student.full_name if c.student else None,
                     course=c.course.name if c.course else None, title=c.title, issued_at=c.issued_at,
                     verification_count=c.verification_count)
