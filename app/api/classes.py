"""REST API: schedules, class sessions, attendance, QA and AI monitoring (mounted at /api/v1)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import require
from app.database import get_db
from app.models.core import User
from app.models.people import Teacher
from app.models.scheduling import (AIClassAnalysis, Attendance, ClassSession, QAReview, Schedule, SESSION_STATUSES)
from app.services import classes as class_svc
from app.services import qa as qa_svc
from app.services import scheduling as sched_svc

router = APIRouter(prefix="/classes", tags=["classes"])


# ----------------------------------------------------------------------------- schemas
class ScheduleOut(BaseModel):
    id: int
    student_id: int
    teacher_id: int
    course_id: Optional[int] = None
    days_of_week: list[int] = []
    start_time: time
    duration_minutes: int
    status: str
    is_trial: bool
    shift_id: Optional[int] = None
    room_name: Optional[str] = None
    start_date: date
    end_date: Optional[date] = None

    class Config:
        from_attributes = True


class ScheduleIn(BaseModel):
    student_id: int
    teacher_id: int
    days_of_week: list[int] = Field(default_factory=list, description="0=Monday .. 6=Sunday")
    start_time: str = Field(description="HH:MM on a 30-minute boundary")
    duration_minutes: int = 30
    course_id: Optional[int] = None
    subscription_id: Optional[int] = None
    shift_id: Optional[int] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_trial: bool = False
    notes: Optional[str] = None


class SessionOut(BaseModel):
    id: int
    schedule_id: Optional[int] = None
    student_id: int
    teacher_id: int
    course_id: Optional[int] = None
    date: date
    start_time: time
    end_time: time
    scheduled_start: datetime
    duration_minutes: int
    status: str
    is_trial: bool
    room_name: Optional[str] = None
    join_url: Optional[str] = None
    teacher_joined_at: Optional[datetime] = None
    student_joined_at: Optional[datetime] = None
    teacher_late_minutes: int = 0
    actual_duration_minutes: Optional[int] = None
    status_reason: Optional[str] = None

    class Config:
        from_attributes = True


class StatusIn(BaseModel):
    status: str
    reason: Optional[str] = None


class AttendanceOut(BaseModel):
    id: int
    session_id: int
    student_id: int
    date: date
    student_status: str
    teacher_status: str
    remarks: Optional[str] = None

    class Config:
        from_attributes = True


class AnalysisOut(BaseModel):
    id: int
    session_id: int
    teacher_id: Optional[int] = None
    overall_score: float
    camera_presence_pct: float
    active_teaching_pct: float
    student_engagement_score: float
    curriculum_coverage_pct: float
    punctuality_minutes: int
    risk_level: str
    review_status: str
    confidence: float
    contact_exchange_detected: bool
    summary: Optional[str] = None

    class Config:
        from_attributes = True


class QAReviewOut(BaseModel):
    id: int
    session_id: Optional[int] = None
    teacher_id: int
    sample_type: str
    status: str
    overall_score: Optional[float] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CountersOut(BaseModel):
    date: date
    pending: int = 0
    started: int = 0
    done: int = 0
    missed: int = 0
    absent: int = 0
    leave: int = 0
    cancelled: int = 0
    rescheduled: int = 0
    free: int = 0
    total: int = 0
    free_students: int = 0


# ----------------------------------------------------------------------------- schedules
@router.get("/schedules", response_model=list[ScheduleOut], summary="List recurring schedules")
def api_schedules(status: str = "active", teacher_id: Optional[int] = None, student_id: Optional[int] = None,
                  limit: int = Query(50, le=200), offset: int = 0,
                  db: Session = Depends(get_db), user: User = Depends(require("schedules.view"))):
    q = db.query(Schedule)
    ids = sched_svc.scoped_teacher_ids(db, user)
    if ids is not None:
        q = q.filter(Schedule.teacher_id.in_(ids or [-1]))
    if status:
        q = q.filter(Schedule.status == status)
    if teacher_id:
        q = q.filter(Schedule.teacher_id == teacher_id)
    if student_id:
        q = q.filter(Schedule.student_id == student_id)
    return q.order_by(Schedule.id).offset(offset).limit(limit).all()


@router.post("/schedules", response_model=ScheduleOut, status_code=201, summary="Create a schedule (conflict-checked)")
def api_create_schedule(payload: ScheduleIn, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("schedules.add"))):
    try:
        sch = sched_svc.create_schedule(
            db, user, student_id=payload.student_id, teacher_id=payload.teacher_id, days=payload.days_of_week,
            start_time=payload.start_time, duration=payload.duration_minutes, course_id=payload.course_id,
            subscription_id=payload.subscription_id, shift_id=payload.shift_id, start_date=payload.start_date,
            end_date=payload.end_date, is_trial=payload.is_trial, notes=payload.notes, request=request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return sch


@router.post("/schedules/{schedule_id}/generate", summary="Generate sessions for a schedule")
def api_generate(schedule_id: int, days: int = Query(14, ge=1, le=90), db: Session = Depends(get_db),
                 user: User = Depends(require("schedules.update"))):
    sch = db.get(Schedule, schedule_id)
    if not sch:
        raise HTTPException(404, "Schedule not found")
    created = class_svc.generate_sessions(db, sch, date.today(), date.today() + timedelta(days=days))
    db.commit()
    return {"schedule_id": sch.id, "created": len(created), "horizon_days": days}


# ----------------------------------------------------------------------------- sessions
@router.get("/sessions", response_model=list[SessionOut], summary="List class sessions")
def api_sessions(day: Optional[date] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
                 status: str = "", teacher_id: Optional[int] = None, student_id: Optional[int] = None,
                 course_id: Optional[int] = None, shift_id: Optional[int] = None,
                 limit: int = Query(50, le=200), offset: int = 0,
                 db: Session = Depends(get_db), user: User = Depends(require("classes.view"))):
    ids = sched_svc.scoped_teacher_ids(db, user)
    q = sched_svc.sessions_query(db, day=day, date_from=date_from, date_to=date_to, status=status,
                                 teacher_id=teacher_id, student_id=student_id, course_id=course_id,
                                 shift_id=shift_id, teacher_ids=ids)
    return q.order_by(ClassSession.scheduled_start.desc()).offset(offset).limit(limit).all()


@router.get("/sessions/{session_id}", response_model=SessionOut, summary="Get one class session")
def api_session(session_id: int, db: Session = Depends(get_db), user: User = Depends(require("classes.view"))):
    s = db.get(ClassSession, session_id)
    if not s:
        raise HTTPException(404, "Class session not found")
    ids = sched_svc.scoped_teacher_ids(db, user)
    if ids is not None and s.teacher_id not in ids:
        raise HTTPException(404, "Class session not found")
    return s


@router.post("/sessions/{session_id}/status", response_model=SessionOut, summary="Change a session status")
def api_status(session_id: int, payload: StatusIn, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require("classes.update"))):
    s = db.get(ClassSession, session_id)
    if not s:
        raise HTTPException(404, "Class session not found")
    if payload.status not in SESSION_STATUSES:
        raise HTTPException(422, f"status must be one of {SESSION_STATUSES}")
    if payload.status in ("missed", "absent", "cancelled", "leave") and not payload.reason:
        raise HTTPException(422, "reason is required for this status")
    class_svc.set_status(db, s, payload.status, user, reason=payload.reason, request=request)
    db.commit()
    return s


@router.get("/sessions/{session_id}/attendance", response_model=Optional[AttendanceOut], summary="Attendance for a session")
def api_attendance(session_id: int, db: Session = Depends(get_db), user: User = Depends(require("attendance.view"))):
    return db.query(Attendance).filter(Attendance.session_id == session_id).first()


@router.get("/sessions/{session_id}/analysis", response_model=Optional[AnalysisOut], summary="AI analysis for a session")
def api_analysis(session_id: int, db: Session = Depends(get_db), user: User = Depends(require("ai_monitoring.view"))):
    return db.query(AIClassAnalysis).filter(AIClassAnalysis.session_id == session_id).first()


@router.post("/sessions/{session_id}/analysis", response_model=AnalysisOut, summary="Run AI analysis for a session")
def api_run_analysis(session_id: int, force: bool = False, db: Session = Depends(get_db),
                     user: User = Depends(require("ai_monitoring.update"))):
    s = db.get(ClassSession, session_id)
    if not s:
        raise HTTPException(404, "Class session not found")
    an = qa_svc.run_ai_analysis(db, s, force=force)
    db.commit()
    return an


# ----------------------------------------------------------------------------- reporting
@router.get("/counters", response_model=CountersOut, summary="Live class counters for a day")
def api_counters(day: Optional[date] = None, shift_id: Optional[int] = None, db: Session = Depends(get_db),
                 user: User = Depends(require("dashboard.view", "classes.view", any_of=True))):
    d = day or date.today()
    ids = sched_svc.scoped_teacher_ids(db, user)
    counts = class_svc.counters_for_date(db, d, ids, shift_id)
    return CountersOut(date=d, **{k: v for k, v in counts.items() if k in CountersOut.model_fields})


@router.get("/teachers/{teacher_id}/stats", summary="Teacher execution stats")
def api_teacher_stats(teacher_id: int, days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db),
                      user: User = Depends(require("classes.view"))):
    t = db.get(Teacher, teacher_id)
    if not t:
        raise HTTPException(404, "Teacher not found")
    stats = class_svc.teacher_stats(db, teacher_id, date.today() - timedelta(days=days))
    return {"teacher_id": teacher_id, "teacher": t.full_name, "window_days": days, "qa_score_avg": t.qa_score_avg, **stats}


@router.get("/qa/reviews", response_model=list[QAReviewOut], summary="List QA reviews")
def api_qa_reviews(status: str = "", teacher_id: Optional[int] = None, limit: int = Query(50, le=200), offset: int = 0,
                   db: Session = Depends(get_db), user: User = Depends(require("qa.view"))):
    q = db.query(QAReview)
    if status:
        q = q.filter(QAReview.status == status)
    if teacher_id:
        q = q.filter(QAReview.teacher_id == teacher_id)
    return q.order_by(QAReview.id.desc()).offset(offset).limit(limit).all()
