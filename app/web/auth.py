"""Web auth: login (with 2FA), logout, profile, notifications, global search."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.core import rbac
from app.core.audit import log_action
from app.core.deps import get_current_user, get_optional_user, csrf_protect, client_ip
from app.core.nav import home_for
from app.core.security import (SESSION_COOKIE, create_access_token, verify_password, hash_password,
                               password_strength_errors, generate_totp_secret, verify_totp, totp_uri, sign_value, verify_signed)
from app.core.templating import render
from app.core.utils import redirect, paginate
from app.database import get_db
from app.models.core import User, UserSession, Notification, SecurityIncident
from app.models.people import Client, Student, Teacher, Employee
from app.models.crm import Lead, Case
from app.models.finance import Invoice

router = APIRouter(dependencies=[Depends(csrf_protect)])

DEMO_ACCOUNTS = [
    ("admin@oqc.local", "Admin@12345", "Super Admin / CEO"),
    ("manager@oqc.local", "Manager@123", "Manager"),
    ("supervisor@oqc.local", "Super@123", "Supervisor"),
    ("teacher1@oqc.local", "Teacher@123", "Teacher"),
    ("parent1@oqc.local", "Parent@123", "Client / Parent"),
    ("student1@oqc.local", "Student@123", "Student"),
    ("finance@oqc.local", "Finance@123", "HOD Finance"),
    ("hr@oqc.local", "People@123", "HOD People & Culture"),
    ("qa@oqc.local", "Quality@123", "HOD QA"),
    ("marketing@oqc.local", "Market@123", "HOD Marketing"),
    ("academics@oqc.local", "Academ@123", "HOD Academics"),
    ("closer@oqc.local", "Closer@123", "Lead Closer"),
    ("billing@oqc.local", "Billing@123", "Billing Representative"),
    ("auditor@oqc.local", "Auditor@123", "External Auditor"),
]


def _start_session(db: Session, user: User, request: Request, remember: bool) -> RedirectResponse:
    hours = settings.SESSION_HOURS * (14 if remember else 1)
    jti = secrets.token_hex(16)
    db.add(UserSession(user_id=user.id, token_jti=jti, ip=client_ip(request), user_agent=request.headers.get("user-agent", "")[:300],
                       expires_at=datetime.utcnow() + timedelta(hours=hours)))
    user.last_login_at = datetime.utcnow()
    user.last_login_ip = client_ip(request)
    user.failed_login_attempts = 0
    user.locked_until = None
    log_action(db, user, "login", "security", entity=user, description="User signed in", request=request)
    db.commit()
    token = create_access_token(user.id, jti=jti, minutes=hours * 60)
    return token, hours


@router.get("/login", include_in_schema=False)
def login_page(request: Request, next: str = "", db: Session = Depends(get_db), user=Depends(get_optional_user)):
    if user:
        return RedirectResponse(home_for(user), status_code=303)
    return render(request, "auth/login.html", {"next": next, "stage": "login", "demo_accounts": DEMO_ACCOUNTS})


@router.post("/login", include_in_schema=False)
async def login_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    next_url = form.get("next") or ""
    ctx = {"next": next_url, "demo_accounts": DEMO_ACCOUNTS}

    # ---- stage 2: TOTP
    if form.get("pending"):
        user_id = verify_signed(form.get("pending", ""), "totp-pending")
        user = db.query(User).get(int(user_id)) if user_id else None
        if not user or not verify_totp(user.two_factor_secret or "", form.get("code", "")):
            db.add(SecurityIncident(incident_type="2fa_failed", severity="medium", ip=client_ip(request), user_id=user.id if user else None,
                                    description="Invalid 2FA code"))
            db.commit()
            return render(request, "auth/login.html", {**ctx, "stage": "totp", "pending": form.get("pending"), "error": "Invalid code. Try again."})
        token, hours = _start_session(db, user, request, bool(form.get("remember")))
        resp = RedirectResponse(next_url or home_for(user), status_code=303)
        resp.set_cookie(SESSION_COOKIE, token, max_age=hours * 3600, httponly=True, samesite="lax", secure=settings.APP_ENV == "production")
        return resp

    # ---- stage 1: credentials
    username = (form.get("username") or "").strip().lower()
    password = form.get("password") or ""
    user = db.query(User).filter(or_(User.email == username, User.username == username)).first()
    generic_error = "Invalid credentials."
    if not user:
        db.add(SecurityIncident(incident_type="login_failed", severity="low", ip=client_ip(request), description=f"Unknown user {username}"))
        db.commit()
        return render(request, "auth/login.html", {**ctx, "stage": "login", "username": username, "error": generic_error})
    if user.locked_until and user.locked_until > datetime.utcnow():
        mins = int((user.locked_until - datetime.utcnow()).total_seconds() // 60) + 1
        return render(request, "auth/login.html", {**ctx, "stage": "login", "username": username, "error": f"Account locked. Try again in {mins} minute(s)."})
    if not user.is_active:
        return render(request, "auth/login.html", {**ctx, "stage": "login", "username": username, "error": "This account is disabled. Contact the administrator."})
    if not verify_password(password, user.hashed_password):
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        if user.failed_login_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            user.locked_until = datetime.utcnow() + timedelta(minutes=settings.LOCKOUT_MINUTES)
            db.add(SecurityIncident(incident_type="lockout", severity="high", ip=client_ip(request), user_id=user.id,
                                    description=f"Account locked after {user.failed_login_attempts} failed attempts"))
            log_action(db, None, "lockout", "security", entity=user, severity="warning", description="Account locked", request=request)
        else:
            db.add(SecurityIncident(incident_type="login_failed", severity="low", ip=client_ip(request), user_id=user.id, description="Wrong password"))
        db.commit()
        return render(request, "auth/login.html", {**ctx, "stage": "login", "username": username, "error": generic_error})
    if user.two_factor_enabled and user.two_factor_secret:
        pending = sign_value(str(user.id), "totp-pending")
        return render(request, "auth/login.html", {**ctx, "stage": "totp", "pending": pending})
    token, hours = _start_session(db, user, request, bool(form.get("remember")))
    resp = RedirectResponse(next_url or home_for(user), status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, max_age=hours * 3600, httponly=True, samesite="lax", secure=settings.APP_ENV == "production")
    if user.must_change_password:
        resp = RedirectResponse("/profile?tab=password", status_code=303)
        resp.set_cookie(SESSION_COOKIE, token, max_age=hours * 3600, httponly=True, samesite="lax", secure=settings.APP_ENV == "production")
    return resp


@router.get("/logout", include_in_schema=False)
@router.post("/logout", include_in_schema=False)
def logout(request: Request, db: Session = Depends(get_db), user=Depends(get_optional_user)):
    jti = getattr(request.state, "session_jti", None)
    if jti:
        sess = db.query(UserSession).filter(UserSession.token_jti == jti).first()
        if sess:
            sess.revoked = True
    if user:
        log_action(db, user, "logout", "security", entity=user, request=request)
    db.commit()
    resp = redirect("/login", "You have been signed out.", "info")
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ----------------------------------------------------------------------------- profile
@router.get("/profile", include_in_schema=False)
def profile(request: Request, tab: str = "profile", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sessions = db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked.is_(False),
                                            UserSession.expires_at > datetime.utcnow()).order_by(UserSession.last_seen_at.desc()).all()
    totp_secret = None
    if tab == "2fa" and not user.two_factor_enabled:
        totp_secret = request.query_params.get("secret") or generate_totp_secret()
    return render(request, "auth/profile.html", {"user": user, "tab": tab, "sessions": sessions, "totp_secret": totp_secret,
                                                 "totp_uri": totp_uri(totp_secret, user.email) if totp_secret else None,
                                                 "current_jti": getattr(request.state, "session_jti", None)})


@router.post("/profile", include_in_schema=False)
async def profile_update(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    form = await request.form()
    action = form.get("action")
    if action == "profile":
        user.full_name = form.get("full_name", user.full_name).strip() or user.full_name
        user.phone = form.get("phone") or None
        user.timezone = form.get("timezone") or user.timezone
        user.language = form.get("language") or "en"
        log_action(db, user, "update", "users", entity=user, description="Updated own profile", request=request)
        db.commit()
        return redirect("/profile", "Profile updated.")
    if action == "password":
        if not verify_password(form.get("current_password", ""), user.hashed_password):
            return redirect("/profile?tab=password", "Current password is incorrect.", "error")
        new = form.get("new_password", "")
        errs = password_strength_errors(new)
        if errs or new != form.get("confirm_password"):
            return redirect("/profile?tab=password", "Password requirements: " + ", ".join(errs or ["passwords must match"]), "error")
        user.hashed_password = hash_password(new)
        user.must_change_password = False
        # revoke other sessions
        jti = getattr(request.state, "session_jti", None)
        for s in db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.token_jti != jti):
            s.revoked = True
        log_action(db, user, "password_change", "security", entity=user, severity="warning", request=request)
        db.commit()
        return redirect("/profile", "Password changed. Other sessions were signed out.")
    if action == "enable_2fa":
        secret = form.get("secret", "")
        if not verify_totp(secret, form.get("code", "")):
            return redirect(f"/profile?tab=2fa&secret={secret}", "Code did not match. Scan the QR again and retry.", "error")
        user.two_factor_secret = secret
        user.two_factor_enabled = True
        log_action(db, user, "enable_2fa", "security", entity=user, severity="warning", request=request)
        db.commit()
        return redirect("/profile?tab=2fa", "Two-factor authentication enabled.")
    if action == "disable_2fa":
        if not verify_password(form.get("password", ""), user.hashed_password):
            return redirect("/profile?tab=2fa", "Password incorrect.", "error")
        user.two_factor_enabled = False
        user.two_factor_secret = None
        log_action(db, user, "disable_2fa", "security", entity=user, severity="warning", request=request)
        db.commit()
        return redirect("/profile?tab=2fa", "Two-factor authentication disabled.", "warning")
    if action == "revoke_session":
        sess = db.query(UserSession).filter(UserSession.id == int(form.get("session_id", 0)), UserSession.user_id == user.id).first()
        if sess:
            sess.revoked = True
            db.commit()
        return redirect("/profile?tab=sessions", "Session revoked.")
    return redirect("/profile")


# ----------------------------------------------------------------------------- notifications
@router.get("/notifications", include_in_schema=False)
def notifications(request: Request, page: int = 1, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(Notification).filter(Notification.user_id == user.id, Notification.channel == "in_app").order_by(Notification.created_at.desc())
    pg = paginate(q, page, 30)
    return render(request, "auth/notifications.html", {"user": user, "page": pg})


@router.post("/notifications/read", include_in_schema=False)
async def notifications_read(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    form = await request.form()
    nid = form.get("id")
    q = db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read.is_(False))
    if nid:
        q = q.filter(Notification.id == int(nid))
    for n in q:
        n.is_read = True
        n.status = "read"
    db.commit()
    target = form.get("next") or "/notifications"
    return RedirectResponse(target, status_code=303)


# ----------------------------------------------------------------------------- global search
@router.get("/search", include_in_schema=False)
def search(request: Request, q: str = "", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = q.strip()
    results: dict[str, list] = {}
    like = f"%{q}%"
    if q:
        if rbac.has_permission(user, "students.view"):
            results["Students"] = [(s.full_name, f"{s.student_code} · {s.status}", f"/students/{s.id}") for s in
                                   db.query(Student).filter(or_(Student.full_name.ilike(like), Student.student_code.ilike(like))).limit(8)]
        if rbac.has_permission(user, "clients.view"):
            results["Clients"] = [(c.full_name, f"{c.client_code} · {c.country}", f"/clients/{c.id}") for c in
                                  db.query(Client).filter(or_(Client.full_name.ilike(like), Client.client_code.ilike(like), Client.email.ilike(like), Client.phone.ilike(like))).limit(8)]
        if rbac.has_permission(user, "teachers.view"):
            results["Teachers"] = [(t.full_name, t.teacher_code, f"/teachers/{t.id}") for t in
                                   db.query(Teacher).filter(or_(Teacher.full_name.ilike(like), Teacher.teacher_code.ilike(like))).limit(8)]
        if rbac.has_permission(user, "leads.view"):
            results["Leads"] = [(l.full_name, f"{l.lead_code} · {l.stage}", f"/crm/leads/{l.id}") for l in
                                db.query(Lead).filter(or_(Lead.full_name.ilike(like), Lead.lead_code.ilike(like), Lead.phone.ilike(like), Lead.email.ilike(like))).limit(8)]
        if rbac.has_permission(user, "billing.view"):
            results["Invoices"] = [(i.invoice_number, f"{i.currency} {i.total} · {i.status}", f"/finance/invoices/{i.id}") for i in
                                   db.query(Invoice).filter(Invoice.invoice_number.ilike(like)).limit(8)]
        if rbac.has_permission(user, "cases.view"):
            results["Cases"] = [(c.title, f"{c.case_number} · {c.status}", f"/cases/{c.id}") for c in
                                db.query(Case).filter(or_(Case.title.ilike(like), Case.case_number.ilike(like))).limit(8)]
        if rbac.has_permission(user, "employees.view"):
            results["Employees"] = [(e.full_name, f"{e.employee_code} · {e.designation}", f"/hr/employees/{e.id}") for e in
                                    db.query(Employee).filter(or_(Employee.full_name.ilike(like), Employee.employee_code.ilike(like))).limit(8)]
        if rbac.has_permission(user, "users.view"):
            results["Users"] = [(u.full_name, u.email, f"/admin/users/{u.id}") for u in
                                db.query(User).filter(or_(User.full_name.ilike(like), User.email.ilike(like))).limit(8)]
    results = {k: v for k, v in results.items() if v}
    return render(request, "auth/search.html", {"user": user, "q": q, "results": results})
