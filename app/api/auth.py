"""REST API: authentication & identity (token login for mobile/PWA/integrations)."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import log_action
from app.core.deps import get_current_user, client_ip
from app.core.security import create_access_token, verify_password, verify_totp
from app.database import get_db
from app.models.core import User, UserSession

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str
    totp_code: str | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str | None
    portal: str
    permissions: list[str]
    two_factor_enabled: bool


@router.post("/login", response_model=TokenOut)
def api_login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    uname = body.username.strip().lower()
    user = db.query(User).filter(or_(User.email == uname, User.username == uname)).first()
    if not user or not user.is_active or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user.locked_until and user.locked_until > datetime.utcnow():
        raise HTTPException(status_code=423, detail="Account locked")
    if user.two_factor_enabled and not verify_totp(user.two_factor_secret or "", body.totp_code or ""):
        raise HTTPException(status_code=401, detail="Two-factor code required or invalid")
    jti = secrets.token_hex(16)
    minutes = settings.ACCESS_TOKEN_MINUTES
    db.add(UserSession(user_id=user.id, token_jti=jti, ip=client_ip(request), user_agent="api",
                       expires_at=datetime.utcnow() + timedelta(minutes=minutes)))
    user.last_login_at = datetime.utcnow()
    log_action(db, user, "login", "security", entity=user, description="API token issued", request=request)
    db.commit()
    return TokenOut(access_token=create_access_token(user.id, jti=jti, minutes=minutes), expires_in=minutes * 60)


@router.post("/logout")
def api_logout(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    jti = getattr(request.state, "session_jti", None)
    if jti:
        s = db.query(UserSession).filter(UserSession.token_jti == jti).first()
        if s:
            s.revoked = True
            db.commit()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def api_me(user: User = Depends(get_current_user)):
    perms = ["*"] if user.is_superuser else list((user.role.permissions if user.role else []) + (user.extra_permissions or []))
    return UserOut(id=user.id, email=user.email, full_name=user.full_name, role=user.role_slug, portal=user.portal,
                   permissions=perms, two_factor_enabled=user.two_factor_enabled)
