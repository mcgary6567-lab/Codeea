"""FastAPI dependencies: DB session, current user (cookie or bearer or API key), permission guards, scoping context."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, joinedload

from app.core import rbac
from app.core.security import SESSION_COOKIE, decode_token, hash_api_key
from app.database import get_db
from app.models.core import User, UserSession, ApiKey
from app.models.people import Client, Student, Teacher, Employee


class NotAuthenticated(Exception):
    """Raised when no valid session; handler redirects web users to /login and returns 401 for API."""


class PermissionDenied(Exception):
    def __init__(self, perm: str = ""):
        self.perm = perm


@dataclass
class UserContext:
    """Record-level scoping info for the logged-in user."""
    user: User
    teacher: Optional[Teacher] = None
    client: Optional[Client] = None
    student: Optional[Student] = None
    employee: Optional[Employee] = None
    student_ids: list[int] = field(default_factory=list)  # students a client/teacher may see

    @property
    def teacher_id(self) -> Optional[int]:
        return self.teacher.id if self.teacher else None

    @property
    def client_id(self) -> Optional[int]:
        return self.client.id if self.client else None

    @property
    def student_id(self) -> Optional[int]:
        return self.student.id if self.student else None

    @property
    def employee_id(self) -> Optional[int]:
        return self.employee.id if self.employee else None


def _user_from_token(db: Session, token: str, request: Request) -> Optional[User]:
    payload = decode_token(token)
    if not payload:
        return None
    sess = db.query(UserSession).filter(UserSession.token_jti == payload.get("jti")).first()
    if not sess or sess.revoked or sess.expires_at < datetime.utcnow():
        return None
    user = db.query(User).options(joinedload(User.role)).filter(User.id == int(payload["sub"])).first()
    if not user or not user.is_active:
        return None
    if user.locked_until and user.locked_until > datetime.utcnow():
        return None
    # touch session at most once per 5 minutes
    if sess.last_seen_at < datetime.utcnow() - timedelta(minutes=5):
        sess.last_seen_at = datetime.utcnow()
        db.commit()
    request.state.session_jti = sess.token_jti
    return user


def _user_from_api_key(db: Session, raw: str, request: Request) -> Optional[User]:
    prefix = raw[:12]
    key = db.query(ApiKey).filter(ApiKey.prefix == prefix, ApiKey.is_active.is_(True)).first()
    if not key or key.key_hash != hash_api_key(raw):
        return None
    if key.expires_at and key.expires_at < datetime.utcnow():
        return None
    key.last_used_at = datetime.utcnow()
    db.commit()
    request.state.api_key = key
    return key.owner if key.owner and key.owner.is_active else None


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    auth = request.headers.get("Authorization", "")
    user: Optional[User] = None
    if auth.startswith("Bearer "):
        user = _user_from_token(db, auth[7:].strip(), request)
    elif auth.startswith("ApiKey ") or request.headers.get("X-API-Key"):
        raw = auth[7:].strip() if auth.startswith("ApiKey ") else request.headers.get("X-API-Key", "")
        user = _user_from_api_key(db, raw, request)
    if user is None:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            user = _user_from_token(db, token, request)
    if user is not None and user.allowed_ips:
        client_ip = request.client.host if request.client else ""
        allowed = [ip.strip() for ip in user.allowed_ips.split(",") if ip.strip()]
        if allowed and client_ip not in allowed:
            return None
    request.state.user = user
    return user


def get_current_user(user: Optional[User] = Depends(get_optional_user)) -> User:
    if user is None:
        raise NotAuthenticated()
    return user


def get_user_context(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UserContext:
    ctx = UserContext(user=user)
    ctx.teacher = db.query(Teacher).filter(Teacher.user_id == user.id).first()
    ctx.client = db.query(Client).filter(Client.user_id == user.id).first()
    ctx.student = db.query(Student).filter(Student.user_id == user.id).first()
    ctx.employee = db.query(Employee).filter(Employee.user_id == user.id).first()
    if ctx.client:
        ctx.student_ids = [s.id for s in ctx.client.students]
    elif ctx.teacher:
        ctx.student_ids = [s.id for s in db.query(Student.id).filter(Student.teacher_id == ctx.teacher.id)]
    elif ctx.student:
        ctx.student_ids = [ctx.student.id]
    return ctx


def require(*perms: str, any_of: bool = False):
    """Dependency factory: require(“students.view”) or require(“a.x”, “b.y”, any_of=True)."""
    def _dep(user: User = Depends(get_current_user)) -> User:
        checks = [rbac.has_permission(user, p) for p in perms]
        ok = any(checks) if any_of else all(checks)
        if not ok:
            raise PermissionDenied(", ".join(perms))
        return user
    return _dep


def require_ceo(user: User = Depends(get_current_user)) -> User:
    if not rbac.is_ceo(user):
        raise PermissionDenied("ceo_only")
    return user


def require_management(user: User = Depends(get_current_user)) -> User:
    if not rbac.is_management(user):
        raise PermissionDenied("management_only")
    return user


def csrf_protect(request: Request) -> None:
    """Origin / Sec-Fetch-Site based CSRF protection for cookie-authenticated form posts."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.headers.get("Authorization") or request.headers.get("X-API-Key"):
        return  # token-authenticated API calls are not CSRF-prone
    site = request.headers.get("Sec-Fetch-Site")
    if site and site not in ("same-origin", "same-site", "none"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-site request blocked")
    origin = request.headers.get("Origin")
    if origin:
        host = request.headers.get("Host", "")
        if host and host not in origin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-site request blocked")


def client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""
