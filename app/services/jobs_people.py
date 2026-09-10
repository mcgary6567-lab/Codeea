"""Background jobs for the People & Portals module.

Registered automatically by app/core/scheduler.py through the ``JOBS`` list.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.people import Student
from app.models.scheduling import TeacherMatch
from app.services.people import age_from_dob

SURVIVAL_DAYS = 90


def evaluate_teacher_match_survival(db: Session) -> dict:
    """Module 44 feedback loop: 90 days after a match, record whether the pairing survived.

    A match survived when the student is still with the same teacher and has not been
    cancelled. The flag feeds the matcher's quality reporting and override review.
    """
    cutoff = datetime.utcnow() - timedelta(days=SURVIVAL_DAYS)
    rows = (db.query(TeacherMatch)
            .filter(TeacherMatch.survived_90_days.is_(None), TeacherMatch.created_at <= cutoff)
            .order_by(TeacherMatch.id).limit(500).all())
    survived = failed = 0
    for m in rows:
        if not m.chosen_teacher_id:
            m.survived_90_days = False
            failed += 1
            continue
        s = db.query(Student).get(m.student_id)
        if not s:
            m.survived_90_days = False
            failed += 1
            continue
        newer = (db.query(TeacherMatch)
                 .filter(TeacherMatch.student_id == m.student_id, TeacherMatch.id > m.id,
                         TeacherMatch.created_at <= m.created_at + timedelta(days=SURVIVAL_DAYS))
                 .first())
        ok = (s.teacher_id == m.chosen_teacher_id and s.status not in ("cancelled",) and newer is None)
        m.survived_90_days = bool(ok)
        if ok:
            survived += 1
        else:
            failed += 1
    return {"evaluated": len(rows), "survived": survived, "not_survived": failed}


def refresh_student_ages(db: Session) -> dict:
    """Keep Student.age and Student.is_minor in step with the date of birth."""
    changed = 0
    rows = db.query(Student).filter(Student.date_of_birth.isnot(None)).all()
    for s in rows:
        age = age_from_dob(s.date_of_birth)
        if age is None:
            continue
        minor = age < 18
        if s.age != age or s.is_minor != minor:
            s.age, s.is_minor = age, minor
            changed += 1
    return {"checked": len(rows), "updated": changed}


JOBS = [
    ("people.teacher_match_survival", evaluate_teacher_match_survival, 24 * 60),
    ("people.refresh_student_ages", refresh_student_ages, 24 * 60),
]
