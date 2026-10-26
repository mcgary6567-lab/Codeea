"""Record scope for supervisors and teachers (decided 27 Oct 2026).

A supervisor sees only the teachers they supervise and those teachers' students and families; a teacher only
themselves and their own students. Everyone else (management, admin, HR, billing...) is unrestricted.

``teachers(db, user)`` is the set of teacher ids in scope, or None for unrestricted. The ``*_filter`` helpers turn
it into a SQLAlchemy condition for a list query; ``check_*`` refuse a single record out of scope.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import false, select, true
from sqlalchemy.orm import Session

from app.core.deps import PermissionDenied
from app.models.core import User
from app.models.people import Student


def teachers(db: Session, user: User) -> Optional[set[int]]:
    from app.services.scheduling import scoped_teacher_ids
    ids = scoped_teacher_ids(db, user)
    return None if ids is None else set(ids)


def _students_select(scope: set[int]):
    return select(Student.id).where(Student.teacher_id.in_(scope or {-1}))


def _clients_select(scope: set[int]):
    return select(Student.client_id).where(Student.teacher_id.in_(scope or {-1}))


def teacher_filter(column, scope: Optional[set[int]]):
    """Condition for a teacher-id column (true when unrestricted)."""
    if scope is None:
        return true()
    return column.in_(scope) if scope else false()


def student_filter(column, scope: Optional[set[int]]):
    """Condition for a student-id column: students taught by a teacher in scope."""
    return true() if scope is None else column.in_(_students_select(scope))


def client_filter(column, scope: Optional[set[int]]):
    """Condition for a client (family) id column: families with a student taught by a teacher in scope."""
    return true() if scope is None else column.in_(_clients_select(scope))


def student_in_scope(db: Session, scope: Optional[set[int]], student_id: Optional[int]) -> bool:
    if scope is None:
        return True
    if not student_id:
        return False
    st = db.get(Student, student_id)
    return bool(st and st.teacher_id in scope)


def client_in_scope(db: Session, scope: Optional[set[int]], client_id: Optional[int]) -> bool:
    if scope is None:
        return True
    if not client_id:
        return False
    return db.query(Student.id).filter(Student.client_id == client_id, Student.teacher_id.in_(scope or {-1})).first() is not None


def check_teacher(scope: Optional[set[int]], teacher_id: Optional[int], perm: str = "out of scope") -> None:
    if scope is not None and teacher_id not in scope:
        raise PermissionDenied(perm)


def check_student(db: Session, scope: Optional[set[int]], student_id: Optional[int], perm: str = "out of scope") -> None:
    if not student_in_scope(db, scope, student_id):
        raise PermissionDenied(perm)


def check_client(db: Session, scope: Optional[set[int]], client_id: Optional[int], perm: str = "out of scope") -> None:
    if not client_in_scope(db, scope, client_id):
        raise PermissionDenied(perm)
