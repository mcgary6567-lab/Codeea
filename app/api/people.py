"""REST API: clients, students, teachers and the Module 44 teacher matcher."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import require, get_current_user
from app.core.utils import parse_int
from app.database import get_db
from app.models.academic import Course
from app.models.core import User
from app.models.people import Client, Student, Teacher
from app.models.scheduling import TeacherMatch
from app.services import people as svc
from app.services.classes import student_attendance_pct

router = APIRouter(prefix="/people", tags=["people"])


# --------------------------------------------------------------------------- schemas
class ClientOut(BaseModel):
    id: int
    client_code: str
    full_name: str
    email: Optional[str] = None
    phone_masked: Optional[str] = None
    country: str
    city: Optional[str] = None
    timezone: str
    currency: str
    status: str
    relationship_to_student: Optional[str] = None
    whatsapp_opt_in: bool
    consent_given: bool
    referral_code: Optional[str] = None
    students: int = 0
    balance: float = 0.0
    joined_at: Optional[date] = None


class StudentOut(BaseModel):
    id: int
    student_code: str
    full_name: str
    gender: str
    age: Optional[int] = None
    is_minor: bool
    status: str
    timezone: str
    level: Optional[str] = None
    course: Optional[str] = None
    client_code: Optional[str] = None
    teacher_code: Optional[str] = None
    teacher_name: Optional[str] = None
    risk_level: str
    risk_score: float
    attendance_30d: Optional[float] = None
    sabaq_position: Optional[str] = None
    join_date: Optional[date] = None


class TeacherOut(BaseModel):
    id: int
    teacher_code: str
    full_name: str
    gender: str
    shift: str
    grade: str
    courses: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    is_verified: bool
    status: str
    qa_score_avg: float
    students: int = 0
    per_class_rate: float = 0.0


class MatchCandidate(BaseModel):
    teacher_id: int
    teacher_code: Optional[str] = None
    name: str
    gender: Optional[str] = None
    shift: Optional[str] = None
    grade: Optional[str] = None
    load: int = 0
    score: float
    reasons: list[str] = Field(default_factory=list)


class MatchOut(BaseModel):
    id: int
    student_id: int
    recommended_teacher_id: Optional[int] = None
    chosen_teacher_id: Optional[int] = None
    match_score: Optional[float] = None
    match_reason: Optional[str] = None
    overridden: bool
    override_reason: Optional[str] = None
    survived_90_days: Optional[bool] = None
    created_at: Optional[datetime] = None


class StudentCreateIn(BaseModel):
    client_id: int
    full_name: str
    gender: str = "male"
    age: Optional[int] = None
    date_of_birth: Optional[date] = None
    course_id: Optional[int] = None
    division_id: Optional[int] = None
    timezone: Optional[str] = None
    status: str = "trial"
    teacher_id: Optional[int] = None
    override_reason: Optional[str] = None
    auto_match: bool = True


class StatusIn(BaseModel):
    status: str
    reason: str


# --------------------------------------------------------------------------- serialisers
def _client_out(db: Session, c: Client) -> ClientOut:
    return ClientOut(id=c.id, client_code=c.client_code, full_name=c.full_name, email=c.email,
                     phone_masked=c.masked_phone, country=c.country, city=c.city, timezone=c.timezone,
                     currency=c.currency, status=c.status, relationship_to_student=c.relationship_to_student,
                     whatsapp_opt_in=c.whatsapp_opt_in, consent_given=c.consent_given, referral_code=c.referral_code,
                     students=len(c.students), balance=svc.client_balance(db, c), joined_at=c.joined_at)


def _student_out(db: Session, s: Student, with_attendance: bool = False) -> StudentOut:
    return StudentOut(id=s.id, student_code=s.student_code, full_name=s.full_name, gender=s.gender, age=s.age,
                      is_minor=s.is_minor, status=s.status, timezone=s.timezone, level=s.level,
                      course=s.course.name if s.course else None,
                      client_code=s.client.client_code if s.client else None,
                      teacher_code=s.teacher.teacher_code if s.teacher else None,
                      teacher_name=s.teacher.full_name if s.teacher else None,
                      risk_level=s.risk_level, risk_score=float(s.risk_score or 0),
                      attendance_30d=student_attendance_pct(db, s.id, 30) if with_attendance else None,
                      sabaq_position=s.sabaq_position, join_date=s.join_date)


def _teacher_out(db: Session, t: Teacher) -> TeacherOut:
    return TeacherOut(id=t.id, teacher_code=t.teacher_code, full_name=t.full_name, gender=t.gender, shift=t.shift,
                      grade=t.grade, courses=list(t.courses or []), languages=list(t.languages or []),
                      is_verified=t.is_verified, status=t.status, qa_score_avg=float(t.qa_score_avg or 0),
                      students=svc.teacher_load(db, t.id), per_class_rate=float(t.per_class_rate or 0))


# --------------------------------------------------------------------------- clients
@router.get("/clients", response_model=list[ClientOut])
def list_clients(q: str = "", status: str = "", country: str = "", limit: int = Query(50, le=200), offset: int = 0,
                 db: Session = Depends(get_db), user: User = Depends(require("clients.view"))):
    query = db.query(Client)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Client.full_name.ilike(like), Client.client_code.ilike(like), Client.email.ilike(like)))
    if status:
        query = query.filter(Client.status == status)
    if country:
        query = query.filter(Client.country == country)
    rows = query.order_by(Client.id).offset(offset).limit(limit).all()
    return [_client_out(db, c) for c in rows]


@router.get("/clients/{client_id}", response_model=ClientOut)
def get_client(client_id: int, db: Session = Depends(get_db), user: User = Depends(require("clients.view"))):
    c = db.query(Client).get(client_id)
    if not c:
        raise HTTPException(404, "Client not found")
    return _client_out(db, c)


@router.get("/clients/{client_id}/students", response_model=list[StudentOut])
def client_students(client_id: int, db: Session = Depends(get_db), user: User = Depends(require("clients.view"))):
    c = db.query(Client).get(client_id)
    if not c:
        raise HTTPException(404, "Client not found")
    return [_student_out(db, s) for s in c.students]


# --------------------------------------------------------------------------- students
@router.get("/students", response_model=list[StudentOut])
def list_students(q: str = "", status: str = "", teacher_id: Optional[int] = None, course_id: Optional[int] = None,
                  risk: str = "", limit: int = Query(50, le=200), offset: int = 0,
                  db: Session = Depends(get_db), user: User = Depends(require("students.view"))):
    query = db.query(Student)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Student.full_name.ilike(like), Student.student_code.ilike(like)))
    if status:
        query = query.filter(Student.status == status)
    if teacher_id:
        query = query.filter(Student.teacher_id == teacher_id)
    if course_id:
        query = query.filter(Student.course_id == course_id)
    if risk:
        query = query.filter(Student.risk_level == risk)
    rows = query.order_by(Student.id).offset(offset).limit(limit).all()
    return [_student_out(db, s) for s in rows]


@router.get("/students/{student_id}", response_model=StudentOut)
def get_student(student_id: int, db: Session = Depends(get_db), user: User = Depends(require("students.view"))):
    s = db.query(Student).get(student_id)
    if not s:
        raise HTTPException(404, "Student not found")
    return _student_out(db, s, with_attendance=True)


@router.post("/students", response_model=StudentOut, status_code=201)
def create_student(body: StudentCreateIn, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("students.add"))):
    client = db.query(Client).get(body.client_id)
    if not client:
        raise HTTPException(404, "Client not found")
    course = db.query(Course).get(body.course_id) if body.course_id else None
    s = svc.create_student(db, client, body.model_dump(), user, request=request)
    ranked = svc.recommend_teachers(db, course.code if course else None, body.gender, body.age or s.age,
                                    body.timezone or client.timezone) if body.auto_match else []
    teacher = db.query(Teacher).get(body.teacher_id) if body.teacher_id else (
        db.query(Teacher).get(ranked[0]["teacher_id"]) if ranked else None)
    if teacher and not teacher.is_verified:
        raise HTTPException(400, "Teacher is not verified and cannot be assigned live classes.")
    if teacher and ranked and teacher.id != ranked[0]["teacher_id"] and not body.override_reason:
        raise HTTPException(400, "override_reason is required when the top recommendation is not chosen.")
    if teacher:
        svc.assign_teacher(db, s, teacher, user, reason=body.override_reason, ranked=ranked, request=request)
    db.commit()
    db.refresh(s)
    return _student_out(db, s)


@router.post("/students/{student_id}/status", response_model=StudentOut)
def change_status(student_id: int, body: StatusIn, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require("students.update"))):
    s = db.query(Student).get(student_id)
    if not s:
        raise HTTPException(404, "Student not found")
    if body.status not in ("trial", "active", "frozen", "cancelled", "graduated", "free"):
        raise HTTPException(400, "Invalid status")
    if not (body.reason or "").strip():
        raise HTTPException(400, "A reason is required for a status change")
    svc.student_status_change(db, s, body.status, user, body.reason, request=request)
    db.commit()
    db.refresh(s)
    return _student_out(db, s)


@router.get("/students/{student_id}/matches", response_model=list[MatchOut])
def student_matches(student_id: int, db: Session = Depends(get_db), user: User = Depends(require("students.view"))):
    rows = db.query(TeacherMatch).filter(TeacherMatch.student_id == student_id).order_by(TeacherMatch.created_at.desc()).all()
    return [MatchOut(id=m.id, student_id=m.student_id, recommended_teacher_id=m.recommended_teacher_id,
                     chosen_teacher_id=m.chosen_teacher_id, match_score=m.match_score, match_reason=m.match_reason,
                     overridden=m.overridden, override_reason=m.override_reason, survived_90_days=m.survived_90_days,
                     created_at=m.created_at) for m in rows]


# --------------------------------------------------------------------------- teachers
@router.get("/teachers", response_model=list[TeacherOut])
def list_teachers(q: str = "", shift: str = "", grade: str = "", verified: Optional[bool] = None,
                  limit: int = Query(50, le=200), offset: int = 0,
                  db: Session = Depends(get_db), user: User = Depends(require("teachers.view"))):
    query = db.query(Teacher)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Teacher.full_name.ilike(like), Teacher.teacher_code.ilike(like)))
    if shift:
        query = query.filter(Teacher.shift == shift)
    if grade:
        query = query.filter(Teacher.grade == grade)
    if verified is not None:
        query = query.filter(Teacher.is_verified.is_(verified))
    rows = query.order_by(Teacher.id).offset(offset).limit(limit).all()
    return [_teacher_out(db, t) for t in rows]


@router.get("/teachers/{teacher_id}", response_model=TeacherOut)
def get_teacher(teacher_id: int, db: Session = Depends(get_db), user: User = Depends(require("teachers.view"))):
    t = db.query(Teacher).get(teacher_id)
    if not t:
        raise HTTPException(404, "Teacher not found")
    return _teacher_out(db, t)


@router.get("/teachers/{teacher_id}/students", response_model=list[StudentOut])
def teacher_students(teacher_id: int, db: Session = Depends(get_db), user: User = Depends(require("teachers.view"))):
    rows = db.query(Student).filter(Student.teacher_id == teacher_id).order_by(Student.full_name).all()
    return [_student_out(db, s) for s in rows]


# --------------------------------------------------------------------------- matcher
@router.get("/teacher-match", response_model=list[MatchCandidate])
def teacher_match(course_code: Optional[str] = None, gender: str = "male", age: Optional[int] = None,
                  timezone: str = "Europe/London", preferred_shift: Optional[str] = None,
                  limit: int = Query(10, le=50), db: Session = Depends(get_db),
                  user: User = Depends(require("students.view"))):
    """Module 44: ranked, verified-only teacher shortlist for a prospective enrolment."""
    ranked = svc.recommend_teachers(db, course_code, gender, age, timezone, preferred_shift=preferred_shift)
    return [MatchCandidate(teacher_id=r["teacher_id"], teacher_code=r["code"], name=r["name"], gender=r["gender"],
                           shift=r["shift"], grade=r["grade"], load=r["load"], score=r["score"], reasons=r["reasons"])
            for r in ranked[:limit]]
