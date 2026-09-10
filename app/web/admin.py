"""System Administration console (Modules 22, 23, 35, 36, 37, 39, 48).

Users & roles, organisation settings, notifications, integration hub, API & webhooks,
security centre, backups & DR, data migration and the immutable audit log.
Every mutation is audit-logged and flash-redirected; consequential actions require a rationale.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings as cfg
from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import PermissionDenied, csrf_protect, require
from app.core.notify import dispatch, notify
from app.core.security import (generate_api_key, generate_totp_secret, hash_password, password_strength_errors)
from app.core.templating import render
from app.core.utils import paginate, parse_bool, parse_date, parse_int, redirect
from app.database import get_db
from app.models.core import (ApiKey, AuditEvent, BackupRecord, Branch, CommunicationPreference, Department,
                             Integration, Notification, NotificationTemplate, Organization, Role, SecurityIncident,
                             Setting, User, UserSession, Webhook, WebhookDelivery)
from app.models.ops import MigrationJob
from app.models.people import Client
from app.services import migration as mig
from app.services import system as sys_svc

router = APIRouter(prefix="/admin", dependencies=[Depends(csrf_protect)])

PORTALS = ["admin", "teacher", "client", "student"]
LANGUAGES = [("en", "English"), ("ur", "Urdu")]


# ============================================================================= helpers
def _org(db: Session) -> Organization:
    org = db.query(Organization).first()
    if not org:
        org = Organization(name="Online Quran College")
        db.add(org)
        db.flush()
    return org


def _need_rationale(form, field: str = "rationale") -> str:
    return (form.get(field) or form.get("reason") or "").strip()


def _roles(db: Session) -> list[Role]:
    return db.query(Role).order_by(Role.portal, Role.name).all()


def _departments(db: Session) -> list[Department]:
    return db.query(Department).order_by(Department.name).all()


def _branches(db: Session) -> list[Branch]:
    return db.query(Branch).order_by(Branch.name).all()


@router.get("", include_in_schema=False)
def admin_home(user: User = Depends(require("users.view", "settings.view", any_of=True))):
    return redirect("/admin/users")


# ============================================================================= USERS
@router.get("/users", include_in_schema=False)
def users_list(request: Request, page: int = 1, q: str = "", role: str = "", department: str = "", branch: str = "",
               status: str = "", twofa: str = "", db: Session = Depends(get_db),
               user: User = Depends(require("users.view"))):
    qry = db.query(User).outerjoin(Role, User.role_id == Role.id)
    if q:
        like = f"%{q.strip()}%"
        qry = qry.filter(or_(User.full_name.ilike(like), User.email.ilike(like), User.username.ilike(like), User.phone.ilike(like)))
    if role:
        qry = qry.filter(Role.slug == role)
    if department:
        qry = qry.filter(User.department_id == parse_int(department))
    if branch:
        qry = qry.filter(User.branch_id == parse_int(branch))
    if status == "active":
        qry = qry.filter(User.is_active.is_(True))
    elif status == "inactive":
        qry = qry.filter(User.is_active.is_(False))
    elif status == "locked":
        qry = qry.filter(User.locked_until.isnot(None), User.locked_until > datetime.utcnow())
    if twofa == "1":
        qry = qry.filter(User.two_factor_enabled.is_(True))
    elif twofa == "0":
        qry = qry.filter(User.two_factor_enabled.is_(False))
    pg = paginate(qry.order_by(User.full_name), page, 30)
    base = f"/admin/users?q={q}&role={role}&department={department}&branch={branch}&status={status}&twofa={twofa}"
    return render(request, "admin/users_list.html", {
        "user": user, "page": pg, "q": q, "role": role, "department": department, "branch": branch,
        "status": status, "twofa": twofa, "stats": sys_svc.user_stats(db), "base_url": base,
        "roles": _roles(db), "departments": _departments(db), "branches": _branches(db)})


@router.get("/users/new", include_in_schema=False)
def user_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require("users.add"))):
    return render(request, "admin/user_form.html", {
        "user": user, "obj": None, "roles": _roles(db), "departments": _departments(db), "branches": _branches(db),
        "timezones": sys_svc.TIMEZONES, "languages": LANGUAGES})


@router.post("/users/new", include_in_schema=False)
async def user_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("users.add"))):
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    full_name = (form.get("full_name") or "").strip()
    if not email or "@" not in email or not full_name:
        return redirect("/admin/users/new", "A valid email address and full name are required.", "error")
    if db.query(User).filter(func.lower(User.email) == email).first():
        return redirect("/admin/users/new", f"A user with the email {email} already exists.", "error")
    raw = (form.get("password") or "").strip()
    generated = False
    if not raw:
        raw = sys_svc.generate_temp_password()
        generated = True
    errs = password_strength_errors(raw)
    if errs:
        return redirect("/admin/users/new", "Password too weak: " + ", ".join(errs), "error")
    obj = User(
        email=email, username=sys_svc.username_from_email(db, email), full_name=full_name,
        hashed_password=hash_password(raw), role_id=parse_int(form.get("role_id")),
        department_id=parse_int(form.get("department_id")), branch_id=parse_int(form.get("branch_id")),
        phone=(form.get("phone") or "").strip() or None,
        timezone=form.get("timezone") or cfg.DEFAULT_TIMEZONE, language=form.get("language") or "en",
        allowed_ips=(form.get("allowed_ips") or "").strip() or None,
        is_active=True, must_change_password=parse_bool(form.get("must_change_password")) or generated)
    db.add(obj)
    db.flush()
    log_action(db, user, "create", "users", entity=obj, description=f"Created user {obj.email} ({obj.role.name if obj.role else 'no role'})",
               after=snapshot(obj), request=request)
    notify(db, obj, "Welcome to Online Quran College OS",
           "Your account has been created. Sign in and change your password on first use.",
           event_type="account_created", link="/dashboard")
    db.commit()
    msg = f"User created. Temporary password (shown once, copy it now): {raw}" if generated else "User created."
    return redirect(f"/admin/users/{obj.id}", msg)


def _get_user(db: Session, user_id: int) -> User:
    obj = db.get(User, user_id)
    if not obj:
        raise PermissionDenied("users.view")
    return obj


@router.get("/users/{user_id}", include_in_schema=False)
def user_detail(user_id: int, request: Request, tab: str = "profile", db: Session = Depends(get_db),
                user: User = Depends(require("users.view"))):
    obj = _get_user(db, user_id)
    sessions = (db.query(UserSession).filter(UserSession.user_id == obj.id)
                .order_by(UserSession.created_at.desc()).limit(25).all())
    logins = (db.query(AuditEvent).filter(AuditEvent.actor_id == obj.id, AuditEvent.module == "security")
              .order_by(AuditEvent.created_at.desc()).limit(30).all())
    activity = (db.query(AuditEvent).filter(AuditEvent.actor_id == obj.id)
                .order_by(AuditEvent.created_at.desc()).limit(30).all())
    incidents = (db.query(SecurityIncident).filter(SecurityIncident.user_id == obj.id)
                 .order_by(SecurityIncident.created_at.desc()).limit(20).all())
    role_perms = sys_svc.expand_permissions(obj.role.permissions if obj.role else [])
    return render(request, "admin/user_detail.html", {
        "user": user, "obj": obj, "tab": tab, "sessions": sessions, "logins": logins, "activity": activity,
        "incidents": incidents, "linked": sys_svc.linked_records(db, obj), "role_perms": role_perms,
        "extra": set(obj.extra_permissions or []), "denied": set(obj.denied_permissions or []),
        "extra_expanded": sys_svc.expand_permissions(obj.extra_permissions or []),
        "denied_expanded": sys_svc.expand_permissions(obj.denied_permissions or []),
        "roles": _roles(db), "departments": _departments(db), "branches": _branches(db),
        "timezones": sys_svc.TIMEZONES, "languages": LANGUAGES, "now": datetime.utcnow()})


@router.post("/users/{user_id}/edit", include_in_schema=False)
async def user_edit(user_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("users.update"))):
    obj = _get_user(db, user_id)
    form = await request.form()
    before = snapshot(obj)
    old_role = obj.role_id
    obj.full_name = (form.get("full_name") or obj.full_name).strip()
    new_email = (form.get("email") or obj.email).strip().lower()
    if new_email != obj.email:
        if db.query(User).filter(func.lower(User.email) == new_email, User.id != obj.id).first():
            return redirect(f"/admin/users/{obj.id}", "That email address is already in use.", "error")
        obj.email = new_email
        obj.username = sys_svc.username_from_email(db, new_email, exclude_id=obj.id)
    obj.role_id = parse_int(form.get("role_id"), obj.role_id)
    obj.department_id = parse_int(form.get("department_id"))
    obj.branch_id = parse_int(form.get("branch_id"))
    obj.phone = (form.get("phone") or "").strip() or None
    obj.timezone = form.get("timezone") or obj.timezone
    obj.language = form.get("language") or obj.language
    obj.allowed_ips = (form.get("allowed_ips") or "").strip() or None
    obj.must_change_password = parse_bool(form.get("must_change_password"))
    rationale = _need_rationale(form)
    if old_role != obj.role_id:
        log_action(db, user, "role_change", "users", entity=obj, severity="warning", consequential=True,
                   rationale=rationale or "Role changed from the user administration console",
                   description=f"Role changed for {obj.email}", before=before, after=snapshot(obj), request=request)
    else:
        log_action(db, user, "update", "users", entity=obj, description=f"Updated user {obj.email}",
                   before=before, after=snapshot(obj), request=request)
    db.commit()
    return redirect(f"/admin/users/{obj.id}", "User updated.")


@router.post("/users/{user_id}/permissions", include_in_schema=False)
async def user_permissions(user_id: int, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require("users.configure"))):
    obj = _get_user(db, user_id)
    form = await request.form()
    rationale = _need_rationale(form)
    if not rationale:
        return redirect(f"/admin/users/{obj.id}?tab=permissions", "A rationale is required to change permission overrides.", "error")
    before = {"extra_permissions": list(obj.extra_permissions or []), "denied_permissions": list(obj.denied_permissions or [])}
    role_perms = sys_svc.expand_permissions(obj.role.permissions if obj.role else [])
    extra_checked = sys_svc.matrix_from_form(form, "x") - role_perms
    denied_checked = sys_svc.matrix_from_form(form, "d")
    obj.extra_permissions = sys_svc.compress_permissions(extra_checked) if extra_checked else []
    obj.denied_permissions = sys_svc.compress_permissions(denied_checked) if denied_checked else []
    after = {"extra_permissions": obj.extra_permissions, "denied_permissions": obj.denied_permissions}
    log_action(db, user, "permission_change", "users", entity=obj, severity="warning", consequential=True,
               rationale=rationale, description=f"Permission overrides updated for {obj.email}",
               before=before, after=after, request=request)
    db.commit()
    return redirect(f"/admin/users/{obj.id}?tab=permissions", "Permission overrides saved.")


@router.post("/users/{user_id}/status", include_in_schema=False)
async def user_status(user_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("users.update"))):
    obj = _get_user(db, user_id)
    form = await request.form()
    rationale = _need_rationale(form)
    if not rationale:
        return redirect(f"/admin/users/{obj.id}", "A rationale is required to change account status.", "error")
    activate = parse_bool(form.get("activate"))
    if obj.id == user.id and not activate:
        return redirect(f"/admin/users/{obj.id}", "You cannot deactivate your own account.", "error")
    before = snapshot(obj, ["is_active"])
    obj.is_active = activate
    if not activate:
        for s in db.query(UserSession).filter(UserSession.user_id == obj.id, UserSession.revoked.is_(False)):
            s.revoked = True
    log_action(db, user, "update" if activate else "revoke", "users", entity=obj, severity="warning", consequential=True,
               rationale=rationale, description=("Reactivated" if activate else "Deactivated") + f" user {obj.email}",
               before=before, after=snapshot(obj, ["is_active"]), request=request)
    db.commit()
    return redirect(f"/admin/users/{obj.id}", "Account reactivated." if activate else "Account deactivated and sessions revoked.")


@router.post("/users/{user_id}/reset-password", include_in_schema=False)
async def user_reset_password(user_id: int, request: Request, db: Session = Depends(get_db),
                              user: User = Depends(require("users.update"))):
    obj = _get_user(db, user_id)
    form = await request.form()
    raw = (form.get("password") or "").strip() or sys_svc.generate_temp_password()
    errs = password_strength_errors(raw)
    if errs:
        return redirect(f"/admin/users/{obj.id}", "Password too weak: " + ", ".join(errs), "error")
    obj.hashed_password = hash_password(raw)
    obj.must_change_password = True
    obj.failed_login_attempts = 0
    obj.locked_until = None
    log_action(db, user, "update", "users", entity=obj, severity="warning", consequential=True,
               rationale=_need_rationale(form) or "Password reset requested by the user",
               description=f"Password reset for {obj.email}", request=request)
    notify(db, obj, "Your password was reset",
           "A system administrator reset your password. You will be asked to choose a new one at next sign-in.",
           event_type="security_password_reset", link="/login")
    db.commit()
    return redirect(f"/admin/users/{obj.id}", f"Password reset. Temporary password (shown once): {raw}")


@router.post("/users/{user_id}/unlock", include_in_schema=False)
async def user_unlock(user_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("users.update"))):
    obj = _get_user(db, user_id)
    form = await request.form()
    before = snapshot(obj, ["locked_until", "failed_login_attempts"])
    obj.locked_until = None
    obj.failed_login_attempts = 0
    for inc in db.query(SecurityIncident).filter(SecurityIncident.user_id == obj.id,
                                                 SecurityIncident.incident_type.in_(["lockout", "brute_force"]),
                                                 SecurityIncident.status == "open"):
        inc.status = "resolved"
        inc.resolved_by_id = user.id
        inc.resolved_at = datetime.utcnow()
    log_action(db, user, "update", "users", entity=obj, severity="warning", consequential=True,
               rationale=_need_rationale(form) or "Account unlocked by administrator",
               description=f"Unlocked {obj.email} (cleared lockout and failed attempts)",
               before=before, after=snapshot(obj, ["locked_until", "failed_login_attempts"]), request=request)
    db.commit()
    return redirect(f"/admin/users/{obj.id}", "Account unlocked.")


@router.post("/users/{user_id}/reset-2fa", include_in_schema=False)
async def user_reset_2fa(user_id: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("users.configure"))):
    obj = _get_user(db, user_id)
    form = await request.form()
    rationale = _need_rationale(form)
    if not rationale:
        return redirect(f"/admin/users/{obj.id}", "A rationale is required to reset two-factor authentication.", "error")
    before = snapshot(obj, ["two_factor_enabled"])
    action = form.get("action") or "reset"
    if action == "enable":
        obj.two_factor_secret = generate_totp_secret()
        obj.two_factor_enabled = True
        msg = "Two-factor enrolment secret issued. The user must re-enrol from their profile."
    else:
        obj.two_factor_enabled = False
        obj.two_factor_secret = None
        msg = "Two-factor authentication reset. The user must enrol again."
    log_action(db, user, "override", "security", entity=obj, severity="critical", consequential=True, rationale=rationale,
               description=f"Two-factor {action} for {obj.email}", before=before,
               after=snapshot(obj, ["two_factor_enabled"]), request=request)
    notify(db, obj, "Two-factor authentication changed", msg, event_type="security_2fa_compliance", link="/profile")
    db.commit()
    return redirect(f"/admin/users/{obj.id}", msg)


@router.post("/users/{user_id}/sessions/revoke", include_in_schema=False)
async def user_revoke_sessions(user_id: int, request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require("security.execute"))):
    obj = _get_user(db, user_id)
    form = await request.form()
    sid = parse_int(form.get("session_id"))
    q = db.query(UserSession).filter(UserSession.user_id == obj.id, UserSession.revoked.is_(False))
    if sid:
        q = q.filter(UserSession.id == sid)
    n = 0
    for s in q.all():
        s.revoked = True
        n += 1
    log_action(db, user, "revoke", "security", entity=obj, severity="warning", consequential=True,
               rationale=_need_rationale(form) or "Session revoked from the user administration console",
               description=f"Revoked {n} session(s) for {obj.email}", request=request)
    db.commit()
    return redirect(f"/admin/users/{obj.id}?tab=sessions", f"{n} session(s) revoked.")


# ============================================================================= ROLES
@router.get("/roles", include_in_schema=False)
def roles_list(request: Request, db: Session = Depends(get_db), user: User = Depends(require("roles.view"))):
    roles = _roles(db)
    counts = sys_svc.role_user_counts(db)
    rows = [{"role": r, "users": counts.get(r.id, 0), "perms": len(sys_svc.expand_permissions(r.permissions or []))}
            for r in roles]
    return render(request, "admin/roles_list.html", {
        "user": user, "rows": rows, "total_perms": len(sys_svc.all_permission_strings()),
        "modules": rbac.MODULES, "actions": rbac.ACTIONS})


@router.get("/roles/new", include_in_schema=False)
def role_new(request: Request, clone: int | None = None, db: Session = Depends(get_db),
             user: User = Depends(require("roles.add"))):
    source = db.get(Role, clone) if clone else None
    return render(request, "admin/role_form.html", {"user": user, "source": source, "roles": _roles(db), "portals": PORTALS})


@router.post("/roles/new", include_in_schema=False)
async def role_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("roles.add"))):
    form = await request.form()
    name = (form.get("name") or "").strip()
    slug = (form.get("slug") or "").strip().lower().replace(" ", "_")
    if not name or not slug:
        return redirect("/admin/roles/new", "Name and slug are required.", "error")
    if db.query(Role).filter(Role.slug == slug).first():
        return redirect("/admin/roles/new", f"A role with the slug '{slug}' already exists.", "error")
    source = db.get(Role, parse_int(form.get("clone_from"))) if form.get("clone_from") else None
    obj = Role(name=name, slug=slug, description=(form.get("description") or "").strip() or None,
               portal=form.get("portal") or "admin", is_system=False,
               permissions=list(source.permissions or []) if source else [])
    db.add(obj)
    db.flush()
    log_action(db, user, "create", "roles", entity=obj, severity="warning", consequential=True,
               rationale=_need_rationale(form) or f"New custom role created{' by cloning ' + source.name if source else ''}",
               description=f"Created role {obj.name} ({obj.slug})", after=snapshot(obj), request=request)
    db.commit()
    return redirect(f"/admin/roles/{obj.id}", "Role created. Review the permission matrix below.")


@router.get("/roles/permission-test", include_in_schema=False)
def role_permission_test(request: Request, test_user_id: int | None = None, perm: str = "",
                         db: Session = Depends(get_db), user: User = Depends(require("roles.view"))):
    target = db.get(User, test_user_id) if test_user_id else None
    result = sys_svc.explain_permission(target, perm) if (target and perm) else None
    return render(request, "admin/permission_test.html", {
        "user": user, "target": target, "perm": perm, "result": result,
        "users": db.query(User).order_by(User.full_name).limit(400).all(),
        "permissions": sys_svc.all_permission_strings()})


@router.get("/roles/compare", include_in_schema=False)
def role_compare(request: Request, a: int | None = None, b: int | None = None, db: Session = Depends(get_db),
                 user: User = Depends(require("roles.view"))):
    roles = _roles(db)
    ra = db.get(Role, a) if a else None
    rb = db.get(Role, b) if b else None
    rows = []
    if ra and rb:
        pa, pb = sys_svc.expand_permissions(ra.permissions or []), sys_svc.expand_permissions(rb.permissions or [])
        for module in rbac.MODULES:
            for action in rbac.ACTIONS:
                p = f"{module}.{action}"
                in_a, in_b = p in pa, p in pb
                if in_a or in_b:
                    rows.append({"perm": p, "module": module, "action": action, "a": in_a, "b": in_b, "same": in_a == in_b})
    return render(request, "admin/role_compare.html", {"user": user, "roles": roles, "ra": ra, "rb": rb, "rows": rows,
                                                       "diff": sum(1 for r in rows if not r["same"])})


@router.get("/roles/{role_id}", include_in_schema=False)
def role_detail(role_id: int, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require("roles.view"))):
    obj = db.get(Role, role_id)
    if not obj:
        raise PermissionDenied("roles.view")
    counts = sys_svc.role_user_counts(db)
    return render(request, "admin/role_detail.html", {
        "user": user, "obj": obj, "checked": sys_svc.expand_permissions(obj.permissions or []),
        "user_count": counts.get(obj.id, 0), "portals": PORTALS,
        "members": db.query(User).filter(User.role_id == obj.id).order_by(User.full_name).limit(50).all()})


@router.post("/roles/{role_id}", include_in_schema=False)
async def role_save(role_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("roles.configure"))):
    obj = db.get(Role, role_id)
    if not obj:
        raise PermissionDenied("roles.configure")
    form = await request.form()
    rationale = _need_rationale(form)
    if not rationale:
        return redirect(f"/admin/roles/{obj.id}", "A rationale is required to change a role's permissions.", "error")
    before = snapshot(obj)
    obj.name = (form.get("name") or obj.name).strip()
    obj.description = (form.get("description") or "").strip() or None
    obj.portal = form.get("portal") or obj.portal
    checked = sys_svc.matrix_from_form(form, "p")
    obj.permissions = sys_svc.compress_permissions(checked)
    log_action(db, user, "permission_change", "roles", entity=obj, severity="critical", consequential=True,
               rationale=rationale, description=f"Updated permission matrix for role {obj.name} ({len(checked)} permissions)",
               before=before, after=snapshot(obj), request=request)
    db.commit()
    return redirect(f"/admin/roles/{obj.id}", f"Role saved with {len(checked)} permissions.")


@router.post("/roles/{role_id}/delete", include_in_schema=False)
async def role_delete(role_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("roles.delete"))):
    obj = db.get(Role, role_id)
    if not obj:
        raise PermissionDenied("roles.delete")
    form = await request.form()
    if obj.is_system:
        return redirect(f"/admin/roles/{obj.id}", "System roles can be edited but never deleted.", "error")
    if db.query(func.count(User.id)).filter(User.role_id == obj.id).scalar():
        return redirect(f"/admin/roles/{obj.id}", "Reassign the users on this role before deleting it.", "error")
    log_action(db, user, "delete", "roles", entity=obj, severity="critical", consequential=True,
               rationale=_need_rationale(form) or "Custom role removed", description=f"Deleted role {obj.name}",
               before=snapshot(obj), request=request)
    db.delete(obj)
    db.commit()
    return redirect("/admin/roles", "Role deleted.")


# ============================================================================= SETTINGS
@router.get("/settings", include_in_schema=False)
def settings_page(request: Request, tab: str = "organization", db: Session = Depends(get_db),
                  user: User = Depends(require("settings.view"))):
    from app.core.scheduler import JOB_STATUS, discover_jobs
    jobs = []
    known = {jid: minutes for jid, _fn, minutes in discover_jobs()}
    for jid, minutes in sorted(known.items()):
        st = JOB_STATUS.get(jid, {})
        jobs.append({"id": jid, "interval": st.get("interval", minutes), "last_run": st.get("last_run"),
                     "status": st.get("status", "scheduled"), "result": st.get("result")})
    grouped: dict[str, list[Setting]] = {}
    for s in db.query(Setting).order_by(Setting.group, Setting.key).all():
        grouped.setdefault(s.group or "general", []).append(s)
    return render(request, "admin/settings.html", {
        "user": user, "tab": tab, "org": _org(db), "branches": _branches(db), "departments": _departments(db),
        "grouped": grouped, "env": sys_svc.env_summary(), "jobs": jobs, "timezones": sys_svc.TIMEZONES,
        "staff": db.query(User).join(Role, User.role_id == Role.id).filter(Role.portal == "admin", User.is_active.is_(True)).order_by(User.full_name).all(),
        "currencies": ["PKR", "GBP", "USD", "EUR", "AUD", "CAD", "SAR", "AED"]})


@router.post("/settings/organization", include_in_schema=False)
async def settings_org(request: Request, db: Session = Depends(get_db), user: User = Depends(require("settings.update"))):
    org = _org(db)
    form = await request.form()
    before = snapshot(org)
    org.name = (form.get("name") or org.name).strip()
    org.legal_name = (form.get("legal_name") or "").strip() or None
    org.base_currency = (form.get("base_currency") or org.base_currency).strip().upper()[:3]
    org.timezone = form.get("timezone") or org.timezone
    org.email = (form.get("email") or "").strip() or None
    org.phone = (form.get("phone") or "").strip() or None
    org.website = (form.get("website") or "").strip() or None
    upload = form.get("logo")
    if upload is not None and getattr(upload, "filename", ""):
        content = await upload.read()
        if content:
            sys_svc.save_logo(db, org, upload.filename, content, user)
    log_action(db, user, "update", "settings", entity=org, description="Updated organisation profile",
               before=before, after=snapshot(org), request=request)
    db.commit()
    return redirect("/admin/settings?tab=organization", "Organisation profile saved.")


@router.post("/settings/branches", include_in_schema=False)
async def settings_branch(request: Request, db: Session = Depends(get_db), user: User = Depends(require("settings.update"))):
    form = await request.form()
    bid = parse_int(form.get("id"))
    obj = db.get(Branch, bid) if bid else None
    if form.get("delete") and obj:
        if db.query(func.count(User.id)).filter(User.branch_id == obj.id).scalar():
            return redirect("/admin/settings?tab=branches", "Move the users off this branch first.", "error")
        log_action(db, user, "delete", "settings", entity=obj, severity="warning", consequential=True,
                   rationale=_need_rationale(form) or "Branch removed", description=f"Deleted branch {obj.name}",
                   before=snapshot(obj), request=request)
        db.delete(obj)
        db.commit()
        return redirect("/admin/settings?tab=branches", "Branch deleted.")
    name = (form.get("name") or "").strip()
    code = (form.get("code") or "").strip().upper()
    if not name or not code:
        return redirect("/admin/settings?tab=branches", "Branch name and code are required.", "error")
    before = snapshot(obj) if obj else None
    if not obj:
        obj = Branch(name=name, code=code)
        db.add(obj)
    obj.name, obj.code = name, code
    obj.country = (form.get("country") or "Pakistan").strip()
    obj.timezone = form.get("timezone") or cfg.DEFAULT_TIMEZONE
    obj.is_active = parse_bool(form.get("is_active"))
    db.flush()
    log_action(db, user, "update" if before else "create", "settings", entity=obj,
               description=f"{'Updated' if before else 'Created'} branch {obj.name}", before=before,
               after=snapshot(obj), request=request)
    db.commit()
    return redirect("/admin/settings?tab=branches", "Branch saved.")


@router.post("/settings/departments", include_in_schema=False)
async def settings_department(request: Request, db: Session = Depends(get_db), user: User = Depends(require("settings.update"))):
    form = await request.form()
    did = parse_int(form.get("id"))
    obj = db.get(Department, did) if did else None
    if form.get("delete") and obj:
        if db.query(func.count(User.id)).filter(User.department_id == obj.id).scalar():
            return redirect("/admin/settings?tab=departments", "Move the users off this department first.", "error")
        log_action(db, user, "delete", "settings", entity=obj, severity="warning", consequential=True,
                   rationale=_need_rationale(form) or "Department removed", description=f"Deleted department {obj.name}",
                   before=snapshot(obj), request=request)
        db.delete(obj)
        db.commit()
        return redirect("/admin/settings?tab=departments", "Department deleted.")
    name = (form.get("name") or "").strip()
    code = (form.get("code") or "").strip().lower()
    if not name or not code:
        return redirect("/admin/settings?tab=departments", "Department name and code are required.", "error")
    before = snapshot(obj) if obj else None
    if not obj:
        obj = Department(name=name, code=code)
        db.add(obj)
    obj.name, obj.code = name, code
    obj.description = (form.get("description") or "").strip() or None
    obj.hod_user_id = parse_int(form.get("hod_user_id"))
    obj.is_active = parse_bool(form.get("is_active"))
    db.flush()
    log_action(db, user, "update" if before else "create", "settings", entity=obj,
               description=f"{'Updated' if before else 'Created'} department {obj.name}", before=before,
               after=snapshot(obj), request=request)
    db.commit()
    return redirect("/admin/settings?tab=departments", "Department saved.")


@router.post("/settings/values", include_in_schema=False)
async def settings_values(request: Request, db: Session = Depends(get_db), user: User = Depends(require("settings.configure"))):
    form = await request.form()
    group = form.get("group") or "general"
    changed = []
    for s in db.query(Setting).filter(Setting.group == group).all():
        before = json.loads(json.dumps(s.value, default=str)) if s.value is not None else None
        value = s.value
        if isinstance(value, dict):
            new_value = dict(value)
            for key, old in value.items():
                field = f"s_{s.id}__{key}"
                if isinstance(old, bool):
                    new_value[key] = parse_bool(form.get(field))
                elif field in form:
                    new_value[key] = sys_svc.coerce_like(old, form.get(field))
            s.value = new_value
        else:
            field = f"s_{s.id}__value"
            if field in form or isinstance(value, bool):
                s.value = sys_svc.coerce_like(value, form.get(field))
        if s.value != before:
            changed.append(s.key)
    if changed:
        log_action(db, user, "update", "settings", entity_type="Setting", severity="warning", consequential=True,
                   rationale=_need_rationale(form) or f"Configuration change in the '{group}' group",
                   description=f"Updated settings: {', '.join(changed[:20])}", request=request)
    db.commit()
    return redirect(f"/admin/settings?tab=settings&group={group}",
                    f"{len(changed)} setting(s) saved." if changed else "No changes detected.")


@router.post("/settings/jobs/{job_id}/run", include_in_schema=False)
async def settings_run_job(job_id: str, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require("settings.execute"))):
    from app.core.scheduler import run_job_now
    try:
        status = run_job_now(job_id)
    except KeyError:
        return redirect("/admin/settings?tab=jobs", f"Unknown job '{job_id}'.", "error")
    log_action(db, user, "execute", "settings", entity_type="Job", description=f"Ran background job {job_id} on demand",
               after={"status": status.get("status"), "result": str(status.get("result"))[:300]}, request=request)
    db.commit()
    return redirect("/admin/settings?tab=jobs", f"Job {job_id} finished: {status.get('status')} — {str(status.get('result'))[:160]}")


# ============================================================================= NOTIFICATIONS
@router.get("/notifications", include_in_schema=False)
def notifications_page(request: Request, tab: str = "templates", page: int = 1, channel: str = "", status: str = "",
                       event: str = "", start: str = "", end: str = "", db: Session = Depends(get_db),
                       user: User = Depends(require("notifications.view"))):
    templates = db.query(NotificationTemplate).order_by(NotificationTemplate.event_type, NotificationTemplate.channel).all()
    log_q = db.query(Notification).outerjoin(User, Notification.user_id == User.id)
    if channel:
        log_q = log_q.filter(Notification.channel == channel)
    if status:
        log_q = log_q.filter(Notification.status == status)
    if event:
        log_q = log_q.filter(Notification.event_type == event)
    d1, d2 = parse_date(start), parse_date(end)
    if d1:
        log_q = log_q.filter(Notification.created_at >= datetime.combine(d1, datetime.min.time()))
    if d2:
        log_q = log_q.filter(Notification.created_at <= datetime.combine(d2, datetime.max.time()))
    pg = paginate(log_q.order_by(Notification.created_at.desc()), page, 40)
    counts = dict(db.query(Notification.status, func.count(Notification.id)).group_by(Notification.status).all())
    prefs = (db.query(CommunicationPreference.channel, CommunicationPreference.opted_in, func.count(CommunicationPreference.id))
             .group_by(CommunicationPreference.channel, CommunicationPreference.opted_in).all())
    pref_rows: dict[str, dict] = {}
    for ch, opted, n in prefs:
        row = pref_rows.setdefault(ch, {"channel": ch, "in": 0, "out": 0})
        row["in" if opted else "out"] += n
    return render(request, "admin/notifications.html", {
        "user": user, "tab": tab, "templates": templates, "page": pg, "counts": counts,
        "channel": channel, "status": status, "event": event, "start": start, "end": end,
        "events": [e[0] for e in db.query(Notification.event_type).distinct().order_by(Notification.event_type).all()],
        "triggers": sys_svc.TRIGGER_CATALOGUE, "pref_rows": sorted(pref_rows.values(), key=lambda r: r["channel"]),
        "roles": _roles(db), "departments": _departments(db),
        "base_url": f"/admin/notifications?tab=log&channel={channel}&status={status}&event={event}&start={start}&end={end}",
        "total_prefs": db.query(func.count(CommunicationPreference.id)).scalar() or 0})


@router.get("/notifications/templates/new", include_in_schema=False)
def template_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require("notifications.add"))):
    return render(request, "admin/notification_template.html", {"user": user, "obj": None, "preview": None,
                                                               "triggers": sys_svc.TRIGGER_CATALOGUE})


@router.get("/notifications/templates/{tpl_id}", include_in_schema=False)
def template_detail(tpl_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("notifications.view"))):
    obj = db.get(NotificationTemplate, tpl_id)
    if not obj:
        raise PermissionDenied("notifications.view")
    preview = {"subject": sys_svc.render_placeholders(obj.subject, sys_svc.SAMPLE_VARS),
               "body": sys_svc.render_placeholders(obj.body, sys_svc.SAMPLE_VARS),
               "placeholders": sys_svc.placeholders_in(obj.subject + " " + obj.body)}
    return render(request, "admin/notification_template.html", {"user": user, "obj": obj, "preview": preview,
                                                               "triggers": sys_svc.TRIGGER_CATALOGUE})


@router.post("/notifications/templates/save", include_in_schema=False)
async def template_save(request: Request, db: Session = Depends(get_db), user: User = Depends(require("notifications.update"))):
    form = await request.form()
    tid = parse_int(form.get("id"))
    obj = db.get(NotificationTemplate, tid) if tid else None
    before = snapshot(obj) if obj else None
    if not obj:
        obj = NotificationTemplate(event_type="", channel="in_app", subject="", body="")
        db.add(obj)
    obj.event_type = (form.get("event_type") or "").strip()
    obj.channel = form.get("channel") or "in_app"
    obj.language = form.get("language") or "en"
    obj.subject = (form.get("subject") or "").strip()
    obj.body = form.get("body") or ""
    obj.is_active = parse_bool(form.get("is_active"))
    if not obj.event_type or not obj.subject:
        return redirect("/admin/notifications?tab=templates", "Event type and subject are required.", "error")
    db.flush()
    log_action(db, user, "update" if before else "create", "notifications", entity=obj,
               description=f"{'Updated' if before else 'Created'} notification template {obj.event_type}/{obj.channel}",
               before=before, after=snapshot(obj), request=request)
    db.commit()
    return redirect(f"/admin/notifications/templates/{obj.id}", "Template saved.")


@router.post("/notifications/templates/{tpl_id}/delete", include_in_schema=False)
async def template_delete(tpl_id: int, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("notifications.delete"))):
    obj = db.get(NotificationTemplate, tpl_id)
    if not obj:
        raise PermissionDenied("notifications.delete")
    form = await request.form()
    log_action(db, user, "delete", "notifications", entity=obj, severity="warning", consequential=True,
               rationale=_need_rationale(form) or "Template retired",
               description=f"Deleted notification template {obj.event_type}/{obj.channel}", before=snapshot(obj), request=request)
    db.delete(obj)
    db.commit()
    return redirect("/admin/notifications?tab=templates", "Template deleted.")


@router.post("/notifications/log/{note_id}/retry", include_in_schema=False)
async def notification_retry(note_id: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("notifications.execute"))):
    n = db.get(Notification, note_id)
    if not n:
        raise PermissionDenied("notifications.execute")
    dispatch(db, n)
    log_action(db, user, "execute", "notifications", entity=n,
               description=f"Retried {n.channel} notification '{n.title}' (now {n.status})", request=request)
    db.commit()
    return redirect("/admin/notifications?tab=log", f"Retry finished with status '{n.status}'.")


@router.post("/notifications/broadcast", include_in_schema=False)
async def notifications_broadcast(request: Request, db: Session = Depends(get_db),
                                  user: User = Depends(require("notifications.execute"))):
    form = await request.form()
    target = form.get("target") or ""
    title = (form.get("title") or "").strip()
    body = (form.get("body") or "").strip()
    channels = [c for c in ("in_app", "email", "whatsapp") if parse_bool(form.get("ch_" + c))] or ["in_app"]
    if not target or not title:
        return redirect("/admin/notifications?tab=broadcast", "Choose an audience and enter a title.", "error")
    n = sys_svc.send_broadcast(db, user, target, title, body, channels, link=(form.get("link") or "").strip() or None)
    log_action(db, user, "execute", "notifications", entity_type="Broadcast", severity="warning", consequential=True,
               rationale=_need_rationale(form) or f"Broadcast to {target}",
               description=f"Broadcast '{title}' sent to {n} user(s) on {', '.join(channels)}",
               after={"target": target, "channels": channels, "recipients": n}, request=request)
    db.commit()
    return redirect("/admin/notifications?tab=broadcast", f"Broadcast queued for {n} recipient(s) on {', '.join(channels)}.")


# ============================================================================= INTEGRATIONS
@router.get("/integrations", include_in_schema=False)
def integrations_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require("integrations.view"))):
    from app.services.integrations import PROVIDERS, get_integration
    cards = []
    for provider, name in PROVIDERS.items():
        integ = get_integration(db, provider)
        meta = sys_svc.PROVIDER_META.get(provider, {})
        cards.append({"integ": integ, "meta": meta, "name": name, "provider": provider,
                      "inbound": sys_svc.INBOUND_WEBHOOKS.get(provider)})
    db.commit()
    failed = (db.query(WebhookDelivery).filter(WebhookDelivery.status.in_(["failed", "dead"]))
              .order_by(WebhookDelivery.created_at.desc()).limit(40).all())
    inbound = (db.query(WebhookDelivery).filter(WebhookDelivery.direction == "in")
               .order_by(WebhookDelivery.created_at.desc()).limit(30).all())
    labels = [c["name"] for c in cards]
    return render(request, "admin/integrations.html", {
        "user": user, "cards": cards, "failed": failed, "inbound": inbound,
        "chart_labels": labels, "chart_calls": [c["integ"].calls_today or 0 for c in cards],
        "chart_failures": [c["integ"].failures_today or 0 for c in cards],
        "healthy": sum(1 for c in cards if c["integ"].health == "healthy"),
        "degraded": sum(1 for c in cards if c["integ"].health in ("degraded", "down")),
        "calls": sum(c["integ"].calls_today or 0 for c in cards),
        "failures": sum(c["integ"].failures_today or 0 for c in cards)})


@router.get("/integrations/{provider}", include_in_schema=False)
def integration_detail(provider: str, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("integrations.view"))):
    from app.services.integrations import PROVIDERS, get_integration
    if provider not in PROVIDERS:
        raise PermissionDenied("integrations.view")
    integ = get_integration(db, provider)
    db.commit()
    return render(request, "admin/integration_detail.html", {
        "user": user, "integ": integ, "provider": provider, "meta": sys_svc.PROVIDER_META.get(provider, {}),
        "inbound": sys_svc.INBOUND_WEBHOOKS.get(provider), "base_url": cfg.BASE_URL,
        "recent": db.query(WebhookDelivery).order_by(WebhookDelivery.created_at.desc()).limit(15).all()})


@router.post("/integrations/{provider}/config", include_in_schema=False)
async def integration_config(provider: str, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("integrations.configure"))):
    from app.services.integrations import get_integration
    integ = get_integration(db, provider)
    form = await request.form()
    before = snapshot(integ)
    config = dict(integ.config or {})
    for key, _label in sys_svc.PROVIDER_META.get(provider, {}).get("fields", []):
        if key in form:
            config[key] = (form.get(key) or "").strip()
    integ.config = config
    log_action(db, user, "update", "integrations", entity=integ, severity="warning", consequential=True,
               rationale=_need_rationale(form) or "Integration configuration updated (non-secret fields)",
               description=f"Updated configuration for {integ.name}", before=before, after=snapshot(integ), request=request)
    db.commit()
    return redirect(f"/admin/integrations/{provider}", "Configuration saved. Secrets remain in the .env file.")


@router.post("/integrations/{provider}/test", include_in_schema=False)
async def integration_test(provider: str, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require("integrations.execute"))):
    form = await request.form()
    try:
        result = sys_svc.test_integration(db, provider, (form.get("target") or "").strip())
        ok = True
    except Exception as exc:
        result, ok = {"error": f"{type(exc).__name__}: {exc}"}, False
    log_action(db, user, "execute", "integrations", entity_type="Integration", description=f"Test connection for {provider}",
               after={"ok": ok, "result": json.dumps(result, default=str)[:400]}, severity="info" if ok else "warning",
               request=request)
    db.commit()
    text = json.dumps(result, default=str)[:220]
    return redirect(f"/admin/integrations/{provider}", ("Test succeeded: " if ok else "Test failed: ") + text,
                    "success" if ok else "error")


@router.post("/integrations/health-check", include_in_schema=False)
async def integrations_health(request: Request, db: Session = Depends(get_db),
                              user: User = Depends(require("integrations.execute"))):
    from app.services.integrations import health_check_all
    health_check_all(db)
    log_action(db, user, "execute", "integrations", entity_type="Integration",
               description="Ran a health check across all providers", request=request)
    db.commit()
    return redirect("/admin/integrations", "Health check completed for every provider.")


@router.post("/integrations/counters/reset", include_in_schema=False)
async def integrations_reset(request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("integrations.execute"))):
    n = sys_svc.reset_integration_counters(db)
    log_action(db, user, "execute", "integrations", entity_type="Integration",
               description=f"Reset daily call/failure counters for {n} provider(s)", request=request)
    db.commit()
    return redirect("/admin/integrations", f"Daily counters reset for {n} provider(s).")


@router.post("/integrations/deliveries/{delivery_id}/retry", include_in_schema=False)
async def integration_delivery_retry(delivery_id: int, request: Request, db: Session = Depends(get_db),
                                     user: User = Depends(require("integrations.execute"))):
    from app.services.integrations import deliver_pending_webhooks
    d = db.get(WebhookDelivery, delivery_id)
    if not d:
        raise PermissionDenied("integrations.execute")
    d.status = "pending"
    d.next_retry_at = datetime.utcnow()
    db.commit()
    deliver_pending_webhooks(db, limit=5)
    db.refresh(d)
    log_action(db, user, "execute", "integrations", entity=d, description=f"Retried webhook delivery #{d.id} ({d.event})",
               request=request)
    db.commit()
    return redirect("/admin/integrations", f"Delivery #{d.id} retried — status '{d.status}'.")


# ============================================================================= API KEYS & WEBHOOKS
@router.get("/api", include_in_schema=False)
def api_page(request: Request, tab: str = "keys", db: Session = Depends(get_db),
             user: User = Depends(require("api_keys.view"))):
    keys = db.query(ApiKey).order_by(ApiKey.is_active.desc(), ApiKey.created_at.desc()).all()
    hooks = db.query(Webhook).order_by(Webhook.name).all()
    deliveries = (db.query(WebhookDelivery).filter(WebhookDelivery.direction == "out")
                  .order_by(WebhookDelivery.created_at.desc()).limit(60).all())
    return render(request, "admin/api.html", {
        "user": user, "tab": tab, "keys": keys, "hooks": hooks, "deliveries": deliveries,
        "events": sys_svc.EVENT_CATALOGUE, "base_url": cfg.BASE_URL,
        "staff": db.query(User).join(Role, User.role_id == Role.id).filter(Role.portal == "admin").order_by(User.full_name).all(),
        "success": sum(1 for d in deliveries if d.status == "success"),
        "failed": db.query(func.count(WebhookDelivery.id)).filter(WebhookDelivery.status.in_(["failed", "dead"])).scalar() or 0,
        "active_keys": sum(1 for k in keys if k.is_active)})


@router.post("/api/keys/new", include_in_schema=False)
async def api_key_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("api_keys.add"))):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return redirect("/admin/api", "A key name is required.", "error")
    raw, prefix, key_hash = generate_api_key()
    scopes = [s.strip() for s in (form.get("scopes") or "").replace("\n", ",").split(",") if s.strip()]
    obj = ApiKey(name=name, prefix=prefix, key_hash=key_hash, owner_id=parse_int(form.get("owner_id")) or user.id,
                 scopes=scopes, rate_limit_per_minute=parse_int(form.get("rate_limit"), 120) or 120,
                 expires_at=(datetime.combine(parse_date(form.get("expires_at")), datetime.max.time())
                             if parse_date(form.get("expires_at")) else None), is_active=True)
    db.add(obj)
    db.flush()
    log_action(db, user, "create", "api_keys", entity=obj, severity="warning", consequential=True,
               rationale=_need_rationale(form) or "New API key issued",
               description=f"Issued API key '{obj.name}' (prefix {obj.prefix}, {len(scopes)} scope(s))",
               after=snapshot(obj), request=request)
    db.commit()
    return redirect("/admin/api", f"API key created. Copy it now, it is never shown again: {raw}")


@router.post("/api/keys/{key_id}/revoke", include_in_schema=False)
async def api_key_revoke(key_id: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("api_keys.delete"))):
    obj = db.get(ApiKey, key_id)
    if not obj:
        raise PermissionDenied("api_keys.delete")
    form = await request.form()
    obj.is_active = False
    log_action(db, user, "revoke", "api_keys", entity=obj, severity="critical", consequential=True,
               rationale=_need_rationale(form) or "API key revoked", description=f"Revoked API key '{obj.name}' ({obj.prefix})",
               request=request)
    db.commit()
    return redirect("/admin/api", "API key revoked.")


@router.get("/api/webhooks/new", include_in_schema=False)
def webhook_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require("webhooks.add"))):
    return render(request, "admin/webhook_form.html", {"user": user, "obj": None, "events": sys_svc.EVENT_CATALOGUE,
                                                       "deliveries": []})


@router.get("/api/webhooks/{hook_id}", include_in_schema=False)
def webhook_detail(hook_id: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("webhooks.view"))):
    obj = db.get(Webhook, hook_id)
    if not obj:
        raise PermissionDenied("webhooks.view")
    deliveries = (db.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == obj.id)
                  .order_by(WebhookDelivery.created_at.desc()).limit(50).all())
    return render(request, "admin/webhook_form.html", {"user": user, "obj": obj, "events": sys_svc.EVENT_CATALOGUE,
                                                       "deliveries": deliveries})


@router.post("/api/webhooks/save", include_in_schema=False)
async def webhook_save(request: Request, db: Session = Depends(get_db), user: User = Depends(require("webhooks.add"))):
    form = await request.form()
    hid = parse_int(form.get("id"))
    obj = db.get(Webhook, hid) if hid else None
    before = snapshot(obj) if obj else None
    name = (form.get("name") or "").strip()
    url = (form.get("url") or "").strip()
    if not name or not url.startswith("http"):
        return redirect("/admin/api?tab=webhooks", "A name and an http(s) URL are required.", "error")
    if not obj:
        obj = Webhook(name=name, url=url)
        db.add(obj)
    obj.name, obj.url = name, url
    obj.events = form.getlist("events") if hasattr(form, "getlist") else []
    obj.secret = (form.get("secret") or "").strip() or None
    obj.is_active = parse_bool(form.get("is_active"))
    db.flush()
    log_action(db, user, "update" if before else "create", "webhooks", entity=obj, severity="warning", consequential=True,
               rationale=_need_rationale(form) or "Outbound webhook configuration",
               description=f"{'Updated' if before else 'Created'} webhook {obj.name} -> {obj.url}",
               before=before, after=snapshot(obj), request=request)
    db.commit()
    return redirect(f"/admin/api/webhooks/{obj.id}", "Webhook saved.")


@router.post("/api/webhooks/{hook_id}/delete", include_in_schema=False)
async def webhook_delete(hook_id: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("webhooks.delete"))):
    obj = db.get(Webhook, hook_id)
    if not obj:
        raise PermissionDenied("webhooks.delete")
    form = await request.form()
    log_action(db, user, "delete", "webhooks", entity=obj, severity="warning", consequential=True,
               rationale=_need_rationale(form) or "Webhook removed", description=f"Deleted webhook {obj.name}",
               before=snapshot(obj), request=request)
    db.delete(obj)
    db.commit()
    return redirect("/admin/api?tab=webhooks", "Webhook deleted.")


@router.post("/api/webhooks/{hook_id}/test", include_in_schema=False)
async def webhook_test(hook_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("webhooks.execute"))):
    from app.services.integrations import deliver_pending_webhooks
    obj = db.get(Webhook, hook_id)
    if not obj:
        raise PermissionDenied("webhooks.execute")
    d = WebhookDelivery(webhook_id=obj.id, direction="out", event="system.test",
                        payload={"message": "Test event from the OQC admin console", "actor": user.email,
                                 "sent_at": datetime.utcnow().isoformat()},
                        status="pending", next_retry_at=datetime.utcnow())
    db.add(d)
    db.commit()
    deliver_pending_webhooks(db, limit=5)
    db.refresh(d)
    log_action(db, user, "execute", "webhooks", entity=obj,
               description=f"Sent test event to {obj.name} — delivery #{d.id} status '{d.status}'", request=request)
    db.commit()
    return redirect(f"/admin/api/webhooks/{obj.id}", f"Test event sent. Delivery #{d.id} status: {d.status}.")


@router.post("/api/webhooks/deliveries/{delivery_id}/retry", include_in_schema=False)
async def webhook_delivery_retry(delivery_id: int, request: Request, db: Session = Depends(get_db),
                                 user: User = Depends(require("webhooks.execute"))):
    from app.services.integrations import deliver_pending_webhooks
    d = db.get(WebhookDelivery, delivery_id)
    if not d:
        raise PermissionDenied("webhooks.execute")
    d.status = "pending"
    d.next_retry_at = datetime.utcnow()
    db.commit()
    deliver_pending_webhooks(db, limit=5)
    db.refresh(d)
    log_action(db, user, "execute", "webhooks", entity=d, description=f"Retried delivery #{d.id} ({d.event})", request=request)
    db.commit()
    return redirect("/admin/api?tab=deliveries", f"Delivery #{d.id} retried — status '{d.status}'.")


# ============================================================================= SECURITY
@router.get("/security", include_in_schema=False)
def security_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require("security.view"))):
    data = sys_svc.security_dashboard(db)
    recent = (db.query(SecurityIncident).order_by(SecurityIncident.created_at.desc()).limit(12).all())
    return render(request, "admin/security.html", {"user": user, "d": data, "recent": recent,
                                                   "policy": {"attempts": cfg.MAX_LOGIN_ATTEMPTS, "minutes": cfg.LOCKOUT_MINUTES}})


@router.get("/security/incidents", include_in_schema=False)
def security_incidents(request: Request, page: int = 1, status: str = "", severity: str = "", incident_type: str = "",
                       db: Session = Depends(get_db), user: User = Depends(require("security.view"))):
    q = db.query(SecurityIncident)
    if status:
        q = q.filter(SecurityIncident.status == status)
    if severity:
        q = q.filter(SecurityIncident.severity == severity)
    if incident_type:
        q = q.filter(SecurityIncident.incident_type == incident_type)
    pg = paginate(q.order_by(SecurityIncident.created_at.desc()), page, 40)
    types = [t[0] for t in db.query(SecurityIncident.incident_type).distinct().order_by(SecurityIncident.incident_type).all()]
    return render(request, "admin/security_incidents.html", {
        "user": user, "page": pg, "status": status, "severity": severity, "incident_type": incident_type, "types": types,
        "base_url": f"/admin/security/incidents?status={status}&severity={severity}&incident_type={incident_type}"})


@router.post("/security/incidents/{incident_id}/resolve", include_in_schema=False)
async def security_incident_resolve(incident_id: int, request: Request, db: Session = Depends(get_db),
                                    user: User = Depends(require("security.update"))):
    inc = db.get(SecurityIncident, incident_id)
    if not inc:
        raise PermissionDenied("security.update")
    form = await request.form()
    note = _need_rationale(form, "note") or _need_rationale(form)
    before = snapshot(inc)
    inc.status = form.get("status") or "resolved"
    inc.resolved_by_id = user.id
    inc.resolved_at = datetime.utcnow()
    if note:
        inc.description = ((inc.description or "") + f"\n[{datetime.utcnow():%Y-%m-%d %H:%M}] {user.full_name}: {note}").strip()
    log_action(db, user, "update", "security", entity=inc, severity="warning", consequential=True,
               rationale=note or "Incident triaged and closed",
               description=f"Resolved security incident #{inc.id} ({inc.incident_type})",
               before=before, after=snapshot(inc), request=request)
    db.commit()
    return redirect("/admin/security/incidents", "Incident resolved.")


@router.get("/security/sessions", include_in_schema=False)
def security_sessions(request: Request, page: int = 1, only_active: str = "1", db: Session = Depends(get_db),
                      user: User = Depends(require("security.view"))):
    q = db.query(UserSession).join(User, UserSession.user_id == User.id)
    if only_active == "1":
        q = q.filter(UserSession.revoked.is_(False), UserSession.expires_at > datetime.utcnow())
    pg = paginate(q.order_by(UserSession.last_seen_at.desc()), page, 40)
    return render(request, "admin/security_sessions.html", {
        "user": user, "page": pg, "only_active": only_active, "now": datetime.utcnow(),
        "base_url": f"/admin/security/sessions?only_active={only_active}"})


@router.post("/security/sessions/revoke", include_in_schema=False)
async def security_sessions_revoke(request: Request, db: Session = Depends(get_db),
                                   user: User = Depends(require("security.execute"))):
    form = await request.form()
    sid = parse_int(form.get("session_id"))
    q = db.query(UserSession).filter(UserSession.revoked.is_(False))
    scope = "all active sessions"
    if sid:
        q = q.filter(UserSession.id == sid)
        scope = f"session #{sid}"
    n = 0
    for s in q.all():
        if s.user_id == user.id and not sid:
            continue
        s.revoked = True
        n += 1
    log_action(db, user, "revoke", "security", entity_type="UserSession", severity="critical", consequential=True,
               rationale=_need_rationale(form) or "Session revocation from the Security Center",
               description=f"Revoked {n} session(s) ({scope})", request=request)
    db.commit()
    return redirect("/admin/security/sessions", f"{n} session(s) revoked.")


@router.get("/security/logins", include_in_schema=False)
def security_logins(request: Request, page: int = 1, q: str = "", db: Session = Depends(get_db),
                    user: User = Depends(require("security.view"))):
    qry = db.query(AuditEvent).filter(AuditEvent.module == "security",
                                      AuditEvent.action.in_(["login", "logout", "login_failed", "lockout"]))
    if q:
        qry = qry.filter(or_(AuditEvent.actor_name.ilike(f"%{q}%"), AuditEvent.ip.ilike(f"%{q}%"),
                             AuditEvent.description.ilike(f"%{q}%")))
    pg = paginate(qry.order_by(AuditEvent.created_at.desc()), page, 50)
    return render(request, "admin/security_logins.html", {"user": user, "page": pg, "q": q,
                                                          "base_url": f"/admin/security/logins?q={q}"})


@router.get("/security/policy", include_in_schema=False)
def security_policy(request: Request, db: Session = Depends(get_db), user: User = Depends(require("security.view"))):
    return render(request, "admin/security_policy.html", {
        "user": user, "policy": {"attempts": cfg.MAX_LOGIN_ATTEMPTS, "minutes": cfg.LOCKOUT_MINUTES,
                                 "session_hours": cfg.SESSION_HOURS, "token_minutes": cfg.ACCESS_TOKEN_MINUTES},
        "force_2fa": bool(sys_svc.get_setting_value(db, "security_force_2fa_privileged", False)),
        "non_compliant": sys_svc.privileged_users_without_2fa(db),
        "privileged_roles": sorted(sys_svc.PRIVILEGED_ROLES),
        "with_ips": db.query(User).filter(User.allowed_ips.isnot(None), User.allowed_ips != "").order_by(User.full_name).all(),
        "all_users": db.query(User).join(Role, User.role_id == Role.id).filter(Role.portal == "admin").order_by(User.full_name).all()})


@router.post("/security/policy", include_in_schema=False)
async def security_policy_save(request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require("security.configure"))):
    form = await request.form()
    enabled = parse_bool(form.get("force_2fa"))
    before = bool(sys_svc.get_setting_value(db, "security_force_2fa_privileged", False))
    sys_svc.set_setting(db, "security_force_2fa_privileged", enabled, group="security",
                        description="Privileged roles must enrol in two-factor authentication.")
    notified = 0
    if enabled and parse_bool(form.get("notify")):
        for u in sys_svc.privileged_users_without_2fa(db):
            notify(db, u, "Two-factor authentication is now required",
                   "Your role is privileged. Please enrol in two-factor authentication from your profile page.",
                   event_type="security_2fa_compliance", link="/profile")
            notified += 1
    log_action(db, user, "update", "security", entity_type="Setting", severity="critical", consequential=True,
               rationale=_need_rationale(form) or "Two-factor policy change for privileged roles",
               description=f"Force 2FA for privileged roles set to {enabled}; {notified} user(s) notified",
               before={"security_force_2fa_privileged": before}, after={"security_force_2fa_privileged": enabled},
               request=request)
    db.commit()
    return redirect("/admin/security/policy", f"Policy saved. {notified} non-compliant user(s) notified.")


@router.post("/security/ip-allowlist", include_in_schema=False)
async def security_ip_allowlist(request: Request, db: Session = Depends(get_db),
                                user: User = Depends(require("security.configure"))):
    form = await request.form()
    target = db.get(User, parse_int(form.get("user_id")))
    if not target:
        return redirect("/admin/security/policy", "Select a user.", "error")
    before = snapshot(target, ["allowed_ips"])
    target.allowed_ips = (form.get("allowed_ips") or "").strip() or None
    log_action(db, user, "update", "security", entity=target, severity="warning", consequential=True,
               rationale=_need_rationale(form) or "IP allowlist maintenance",
               description=f"IP allowlist for {target.email} set to {target.allowed_ips or '(none)'}",
               before=before, after=snapshot(target, ["allowed_ips"]), request=request)
    db.commit()
    return redirect("/admin/security/policy", "IP allowlist updated.")


@router.get("/security/masking", include_in_schema=False)
def security_masking(request: Request, db: Session = Depends(get_db), user: User = Depends(require("security.view"))):
    return render(request, "admin/security_masking.html", {"user": user, "policy": sys_svc.MASKING_POLICY})


@router.get("/security/retention", include_in_schema=False)
def security_retention(request: Request, db: Session = Depends(get_db), user: User = Depends(require("security.view"))):
    rows = []
    for key, label, group, default, note in sys_svc.RETENTION_KEYS:
        rows.append({"key": key, "label": label, "group": group, "note": note,
                     "value": sys_svc.get_setting_value(db, key, default)})
    churned = (db.query(Client).filter(Client.status.in_(["churned", "inactive", "cancelled"]))
               .order_by(Client.full_name).limit(300).all())
    return render(request, "admin/security_retention.html", {
        "user": user, "rows": rows, "churned": churned,
        "clients": db.query(Client).order_by(Client.full_name).limit(400).all()})


@router.post("/security/retention", include_in_schema=False)
async def security_retention_save(request: Request, db: Session = Depends(get_db),
                                  user: User = Depends(require("security.configure"))):
    form = await request.form()
    changed = []
    for key, label, group, default, _note in sys_svc.RETENTION_KEYS:
        if key in form:
            value = parse_int(form.get(key), default)
            sys_svc.set_setting(db, key, value, group=group, description=label)
            changed.append(f"{key}={value}")
    log_action(db, user, "update", "security", entity_type="Setting", severity="warning", consequential=True,
               rationale=_need_rationale(form) or "Data retention policy update",
               description="Retention policy updated: " + ", ".join(changed), request=request)
    db.commit()
    return redirect("/admin/security/retention", "Retention policy saved.")


@router.post("/security/anonymise", include_in_schema=False)
async def security_anonymise(request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("security.execute"))):
    form = await request.form()
    client = db.get(Client, parse_int(form.get("client_id")))
    rationale = _need_rationale(form)
    if not client:
        return redirect("/admin/security/retention", "Select a client to anonymise.", "error")
    if not rationale:
        return redirect("/admin/security/retention", "A rationale is required to anonymise personal data.", "error")
    result = sys_svc.anonymise_client(db, client, user, rationale, request=request)
    db.commit()
    return redirect("/admin/security/retention",
                    f"Client anonymised along with {result['students']} student record(s). The action is recorded in the audit log.")


@router.get("/security/export", include_in_schema=False)
def security_export_form(request: Request, db: Session = Depends(get_db), user: User = Depends(require("security.view"))):
    return render(request, "admin/security_export.html", {
        "user": user, "clients": db.query(Client).order_by(Client.full_name).limit(500).all()})


@router.post("/security/export", include_in_schema=False)
async def security_export(request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("security.export"))):
    form = await request.form()
    client = db.get(Client, parse_int(form.get("client_id")))
    if not client:
        return redirect("/admin/security/export", "Select a client.", "error")
    path = sys_svc.export_client_data(db, client, user)
    log_action(db, user, "export", "security", entity=client, severity="warning", consequential=True,
               rationale=_need_rationale(form) or "Subject access request",
               description=f"Subject access export generated for {client.client_code} ({path.name})", request=request)
    db.commit()
    return FileResponse(str(path), filename=path.name, media_type="application/zip")


# ============================================================================= BACKUPS
@router.get("/backups", include_in_schema=False)
def backups_page(request: Request, page: int = 1, db: Session = Depends(get_db),
                 user: User = Depends(require("backups.view"))):
    pg = paginate(db.query(BackupRecord).order_by(BackupRecord.created_at.desc()), page, 30)
    total_bytes = db.query(func.coalesce(func.sum(BackupRecord.size_bytes), 0)).scalar() or 0
    tested = db.query(func.count(BackupRecord.id)).filter(BackupRecord.restore_tested.is_(True)).scalar() or 0
    latest = db.query(BackupRecord).order_by(BackupRecord.created_at.desc()).first()
    return render(request, "admin/backups.html", {
        "user": user, "page": pg, "base_url": "/admin/backups", "total_bytes": total_bytes, "tested": tested,
        "latest": latest, "retention": sys_svc.get_setting_value(db, "backup_retention_count", 14),
        "targets": sys_svc.get_setting(db, "dr_targets") or {"rpo_hours": 24, "rto_hours": 4,
                                                             "offsite": "rclone sync to S3 nightly"},
        "engine": "SQLite" if cfg.is_sqlite else "PostgreSQL", "backup_dir": str(sys_svc.BACKUP_DIR)})


@router.post("/backups/create", include_in_schema=False)
async def backups_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("backups.execute"))):
    form = await request.form()
    rec = sys_svc.create_backup(db, user, backup_type="manual")
    log_action(db, user, "execute", "backups", entity=rec, severity="warning", consequential=True,
               rationale=_need_rationale(form) or "Manual backup requested",
               description=f"Created backup {rec.filename} ({rec.size_bytes // 1024} KB)", request=request)
    removed = sys_svc.cleanup_backups(db)
    db.commit()
    return redirect("/admin/backups", f"Backup {rec.filename} created ({rec.size_bytes // 1024} KB). {removed} old backup(s) pruned.")


@router.post("/backups/{backup_id}/restore-test", include_in_schema=False)
async def backups_restore_test(backup_id: int, request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require("backups.execute"))):
    rec = db.get(BackupRecord, backup_id)
    if not rec:
        raise PermissionDenied("backups.execute")
    result = sys_svc.restore_test(db, rec)
    log_action(db, user, "execute", "backups", entity=rec, severity="info" if result.get("ok") else "critical",
               consequential=True, rationale="Scheduled restore verification",
               description=f"Restore test for {rec.filename}: {'passed' if result.get('ok') else 'FAILED'} "
                           f"({result.get('tables', 0)} tables, {result.get('rows', 0)} rows)",
               after={k: v for k, v in result.items() if k != "detail"}, request=request)
    db.commit()
    return redirect("/admin/backups", ("Restore test passed: " if result.get("ok") else "Restore test FAILED: ")
                    + f"{result.get('tables', 0)} tables, {result.get('rows', 0)} rows.",
                    "success" if result.get("ok") else "error")


@router.get("/backups/{backup_id}/download", include_in_schema=False)
def backups_download(backup_id: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("backups.export"))):
    rec = db.get(BackupRecord, backup_id)
    if not rec or not Path(rec.path).exists():
        return redirect("/admin/backups", "That backup file is no longer on disk.", "error")
    log_action(db, user, "export", "backups", entity=rec, severity="warning", consequential=True,
               rationale="Backup downloaded for off-site storage", description=f"Downloaded backup {rec.filename}",
               request=request)
    db.commit()
    return FileResponse(rec.path, filename=rec.filename, media_type="application/zip")


@router.post("/backups/{backup_id}/delete", include_in_schema=False)
async def backups_delete(backup_id: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("backups.delete"))):
    rec = db.get(BackupRecord, backup_id)
    if not rec:
        raise PermissionDenied("backups.delete")
    form = await request.form()
    name = rec.filename
    log_action(db, user, "delete", "backups", entity=rec, severity="warning", consequential=True,
               rationale=_need_rationale(form) or "Backup removed", description=f"Deleted backup {name}",
               before=snapshot(rec), request=request)
    sys_svc.delete_backup(db, rec)
    db.commit()
    return redirect("/admin/backups", f"Backup {name} deleted.")


@router.post("/backups/settings", include_in_schema=False)
async def backups_settings(request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require("backups.configure"))):
    form = await request.form()
    keep = parse_int(form.get("backup_retention_count"), 14) or 14
    sys_svc.set_setting(db, "backup_retention_count", keep, group="backups",
                        description="How many backup archives to keep before pruning.")
    sys_svc.set_setting(db, "dr_targets", {"rpo_hours": parse_int(form.get("rpo_hours"), 24) or 24,
                                           "rto_hours": parse_int(form.get("rto_hours"), 4) or 4,
                                           "offsite": (form.get("offsite") or "").strip()},
                        group="backups", description="Disaster recovery objectives (RPO/RTO) and off-site strategy.")
    log_action(db, user, "update", "backups", entity_type="Setting", severity="warning", consequential=True,
               rationale=_need_rationale(form) or "DR target / retention change",
               description=f"Backup retention set to {keep}; DR targets updated", request=request)
    db.commit()
    return redirect("/admin/backups", "Backup and DR settings saved.")


@router.get("/backups/runbook", include_in_schema=False)
def backups_runbook(request: Request, db: Session = Depends(get_db), user: User = Depends(require("backups.view"))):
    return render(request, "admin/dr_runbook.html", {
        "user": user, "steps": sys_svc.DR_RUNBOOK,
        "targets": sys_svc.get_setting(db, "dr_targets") or {"rpo_hours": 24, "rto_hours": 4, "offsite": ""},
        "engine": "SQLite" if cfg.is_sqlite else "PostgreSQL"})


# ============================================================================= MIGRATION
@router.get("/migration", include_in_schema=False)
def migration_page(request: Request, page: int = 1, db: Session = Depends(get_db),
                   user: User = Depends(require("migration.view"))):
    pg = paginate(db.query(MigrationJob).order_by(MigrationJob.created_at.desc()), page, 25)
    return render(request, "admin/migration_list.html", {
        "user": user, "page": pg, "base_url": "/admin/migration", "entities": mig.ENTITIES,
        "imported": db.query(func.coalesce(func.sum(MigrationJob.records_imported), 0)).scalar() or 0,
        "skipped": db.query(func.coalesce(func.sum(MigrationJob.records_skipped), 0)).scalar() or 0,
        "jobs_total": db.query(func.count(MigrationJob.id)).scalar() or 0})


@router.get("/migration/template/{entity}.csv", include_in_schema=False)
def migration_template(entity: str, user: User = Depends(require("migration.view"))):
    if entity not in mig.ENTITIES:
        raise PermissionDenied("migration.view")
    return Response(mig.template_csv(entity), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="oqc-{entity}-template.csv"'})


@router.get("/migration/checklist", include_in_schema=False)
def migration_checklist(request: Request, db: Session = Depends(get_db), user: User = Depends(require("migration.view"))):
    items = mig.checklist(db)
    phases: dict[str, list] = {}
    for i in items:
        phases.setdefault(i["phase"], []).append(i)
    return render(request, "admin/migration_checklist.html", {
        "user": user, "phases": phases, "done": sum(1 for i in items if i["done"]), "total": len(items)})


@router.post("/migration/checklist", include_in_schema=False)
async def migration_checklist_save(request: Request, db: Session = Depends(get_db),
                                   user: User = Depends(require("migration.update"))):
    form = await request.form()
    state = {item["key"]: parse_bool(form.get("chk_" + item["key"])) for item in mig.DEFAULT_CHECKLIST}
    sys_svc.set_setting(db, "migration_checklist", {"items": state}, group="migration",
                        description="Legacy ERP cutover checklist (SRS Section 20).")
    log_action(db, user, "update", "migration", entity_type="Setting",
               description=f"Cutover checklist updated ({sum(1 for v in state.values() if v)}/{len(state)} complete)",
               after=state, request=request)
    db.commit()
    return redirect("/admin/migration/checklist", "Checklist saved.")


@router.post("/migration/upload", include_in_schema=False)
async def migration_upload(request: Request, db: Session = Depends(get_db), user: User = Depends(require("migration.add"))):
    form = await request.form()
    entity = form.get("entity") or ""
    if entity not in mig.ENTITIES:
        return redirect("/admin/migration", "Choose an entity to import.", "error")
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        return redirect("/admin/migration", "Attach a CSV or XLSX file.", "error")
    content = await upload.read()
    if not content:
        return redirect("/admin/migration", "The uploaded file is empty.", "error")
    path = mig.save_upload(upload.filename, content)
    headers, rows = mig.parse_file(path)
    if not headers:
        return redirect("/admin/migration", "Could not read any columns from that file.", "error")
    job = MigrationJob(name=(form.get("name") or f"{mig.ENTITIES[entity]['label']} import {date.today():%d %b %Y}").strip(),
                       entity=entity, source_system=(form.get("source_system") or "legacy_erp").strip(),
                       file_path=str(path), status="uploaded", records_total=len(rows),
                       field_mapping=mig.suggest_mapping(entity, headers), created_by_id=user.id, errors=[])
    db.add(job)
    db.flush()
    log_action(db, user, "create", "migration", entity=job,
               description=f"Uploaded {upload.filename} for {entity} ({len(rows)} rows)", request=request)
    db.commit()
    return redirect(f"/admin/migration/{job.id}", f"File uploaded: {len(rows)} data rows detected.")


@router.get("/migration/{job_id}", include_in_schema=False)
def migration_job(job_id: int, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require("migration.view"))):
    job = db.get(MigrationJob, job_id)
    if not job:
        raise PermissionDenied("migration.view")
    headers, rows = mig.parse_file(job.file_path or "", limit=20)
    step = {"uploaded": 2, "validated": 3, "imported": 4, "reconciled": 5, "failed": 3}.get(job.status, 1)
    return render(request, "admin/migration_job.html", {
        "user": user, "job": job, "headers": headers, "rows": rows, "step": step,
        "columns": mig.entity_columns(job.entity), "mapping": job.field_mapping or {},
        "entity_meta": mig.ENTITIES.get(job.entity, {}),
        "report": mig.reconciliation(db, job) if job.status in ("imported", "reconciled") else None,
        "errors": (job.errors or [])[:200]})


@router.post("/migration/{job_id}/mapping", include_in_schema=False)
async def migration_mapping(job_id: int, request: Request, db: Session = Depends(get_db),
                            user: User = Depends(require("migration.update"))):
    job = db.get(MigrationJob, job_id)
    if not job:
        raise PermissionDenied("migration.update")
    form = await request.form()
    mapping = {}
    for target, _label, _t, _req in mig.entity_columns(job.entity):
        value = (form.get("map_" + target) or "").strip()
        if value:
            mapping[target] = value
    job.field_mapping = mapping
    log_action(db, user, "update", "migration", entity=job, description=f"Field mapping saved ({len(mapping)} columns mapped)",
               after=mapping, request=request)
    db.commit()
    return redirect(f"/admin/migration/{job.id}", f"Mapping saved for {len(mapping)} column(s).")


@router.post("/migration/{job_id}/validate", include_in_schema=False)
async def migration_validate(job_id: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("migration.execute"))):
    job = db.get(MigrationJob, job_id)
    if not job:
        raise PermissionDenied("migration.execute")
    result = mig.validate_job(db, job)
    log_action(db, user, "execute", "migration", entity=job,
               description=f"Validated {result['rows']} row(s): {result['errors']} error(s), "
                           f"{result['warnings']} warning(s), {result['duplicates']} duplicate(s)",
               after=result, request=request)
    db.commit()
    return redirect(f"/admin/migration/{job.id}",
                    f"Validation complete: {result['rows']} rows, {result['errors']} error(s), {result['duplicates']} duplicate(s).",
                    "success" if not result["errors"] else "warning")


@router.post("/migration/{job_id}/import", include_in_schema=False)
async def migration_import(job_id: int, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require("migration.execute"))):
    job = db.get(MigrationJob, job_id)
    if not job:
        raise PermissionDenied("migration.execute")
    form = await request.form()
    rationale = _need_rationale(form)
    if not rationale:
        return redirect(f"/admin/migration/{job.id}", "A rationale is required before importing legacy data.", "error")
    result = mig.import_job(db, job, user)
    job.status = "reconciled"
    log_action(db, user, "create", "migration", entity=job, severity="warning", consequential=True, rationale=rationale,
               description=f"Imported {result['imported']} {job.entity} record(s); {result['skipped']} skipped, "
                           f"{result['duplicates']} duplicate(s)", after=result, request=request)
    notify(db, user, "Migration completed",
           f"{result['imported']} {job.entity} record(s) imported, {result['skipped']} skipped.",
           event_type="migration_completed", link=f"/admin/migration/{job.id}")
    db.commit()
    return redirect(f"/admin/migration/{job.id}",
                    f"Import finished: {result['imported']} imported, {result['skipped']} skipped, {result['duplicates']} duplicates.")


@router.get("/migration/{job_id}/errors.csv", include_in_schema=False)
def migration_errors(job_id: int, db: Session = Depends(get_db), user: User = Depends(require("migration.view"))):
    job = db.get(MigrationJob, job_id)
    if not job:
        raise PermissionDenied("migration.view")
    return Response(mig.errors_csv(job), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="migration-{job.id}-errors.csv"'})


@router.post("/migration/{job_id}/delete", include_in_schema=False)
async def migration_delete(job_id: int, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require("migration.delete"))):
    job = db.get(MigrationJob, job_id)
    if not job:
        raise PermissionDenied("migration.delete")
    form = await request.form()
    log_action(db, user, "delete", "migration", entity=job, severity="warning", consequential=True,
               rationale=_need_rationale(form) or "Migration job removed",
               description=f"Deleted migration job '{job.name}' (imported records are NOT removed)",
               before=snapshot(job), request=request)
    db.delete(job)
    db.commit()
    return redirect("/admin/migration", "Migration job deleted. Imported records were left untouched.")


# ============================================================================= AUDIT
def _audit_query(db: Session, actor: str, module: str, action: str, entity_type: str, entity_id: str,
                 severity: str, consequential: str, start: str, end: str, q: str):
    qry = db.query(AuditEvent)
    if actor:
        qry = qry.filter(or_(AuditEvent.actor_name.ilike(f"%{actor}%"), AuditEvent.actor_id == parse_int(actor)))
    if module:
        qry = qry.filter(AuditEvent.module == module)
    if action:
        qry = qry.filter(AuditEvent.action == action)
    if entity_type:
        qry = qry.filter(AuditEvent.entity_type == entity_type)
    if entity_id:
        qry = qry.filter(AuditEvent.entity_id == parse_int(entity_id))
    if severity:
        qry = qry.filter(AuditEvent.severity == severity)
    if consequential == "1":
        qry = qry.filter(AuditEvent.is_consequential.is_(True))
    d1, d2 = parse_date(start), parse_date(end)
    if d1:
        qry = qry.filter(AuditEvent.created_at >= datetime.combine(d1, datetime.min.time()))
    if d2:
        qry = qry.filter(AuditEvent.created_at <= datetime.combine(d2, datetime.max.time()))
    if q:
        qry = qry.filter(or_(AuditEvent.description.ilike(f"%{q}%"), AuditEvent.rationale.ilike(f"%{q}%")))
    return qry


@router.get("/audit", include_in_schema=False)
def audit_list(request: Request, page: int = 1, actor: str = "", module: str = "", action: str = "",
               entity_type: str = "", entity_id: str = "", severity: str = "", consequential: str = "",
               start: str = "", end: str = "", q: str = "", db: Session = Depends(get_db),
               user: User = Depends(require("audit.view"))):
    qry = _audit_query(db, actor, module, action, entity_type, entity_id, severity, consequential, start, end, q)
    pg = paginate(qry.order_by(AuditEvent.created_at.desc()), page, 50)
    stats = sys_svc.audit_stats(db, 30)
    params = (f"actor={actor}&module={module}&action={action}&entity_type={entity_type}&entity_id={entity_id}"
              f"&severity={severity}&consequential={consequential}&start={start}&end={end}&q={q}")
    return render(request, "admin/audit_list.html", {
        "user": user, "page": pg, "stats": stats, "base_url": "/admin/audit?" + params, "params": params,
        "actor": actor, "module": module, "action": action, "entity_type": entity_type, "entity_id": entity_id,
        "severity": severity, "consequential": consequential, "start": start, "end": end, "q": q,
        "modules": [m[0] for m in db.query(AuditEvent.module).distinct().order_by(AuditEvent.module).all()],
        "actions": [a[0] for a in db.query(AuditEvent.action).distinct().order_by(AuditEvent.action).all()],
        "entity_types": [e[0] for e in db.query(AuditEvent.entity_type).distinct().order_by(AuditEvent.entity_type).all() if e[0]]})


@router.get("/audit/export.csv", include_in_schema=False)
def audit_export(request: Request, actor: str = "", module: str = "", action: str = "", entity_type: str = "",
                 entity_id: str = "", severity: str = "", consequential: str = "", start: str = "", end: str = "",
                 q: str = "", db: Session = Depends(get_db),
                 user: User = Depends(require("audit.export", "reports.export", any_of=True))):
    qry = _audit_query(db, actor, module, action, entity_type, entity_id, severity, consequential, start, end, q)
    events = qry.order_by(AuditEvent.created_at.desc()).limit(20000).all()
    log_action(db, user, "export", "audit", entity_type="AuditEvent", severity="warning", consequential=True,
               rationale="Audit log export from the admin console",
               description=f"Exported {len(events)} audit event(s) to CSV", request=request)
    db.commit()
    return Response(sys_svc.audit_csv(events), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="oqc-audit-{date.today():%Y%m%d}.csv"'})


@router.get("/audit/entity/{etype}/{eid}", include_in_schema=False)
def audit_entity(etype: str, eid: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require("audit.view"))):
    events = (db.query(AuditEvent).filter(AuditEvent.entity_type == etype, AuditEvent.entity_id == eid)
              .order_by(AuditEvent.created_at.desc()).limit(200).all())
    return render(request, "admin/audit_entity.html", {"user": user, "etype": etype, "eid": eid, "events": events})


@router.get("/audit/{event_id}", include_in_schema=False)
def audit_detail(event_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require("audit.view"))):
    ev = db.get(AuditEvent, event_id)
    if not ev:
        raise PermissionDenied("audit.view")
    related = []
    if ev.entity_type and ev.entity_id:
        related = (db.query(AuditEvent).filter(AuditEvent.entity_type == ev.entity_type,
                                               AuditEvent.entity_id == ev.entity_id, AuditEvent.id != ev.id)
                   .order_by(AuditEvent.created_at.desc()).limit(15).all())
    return render(request, "admin/audit_detail.html", {
        "user": user, "ev": ev, "diff": sys_svc.audit_diff(ev.before_data, ev.after_data), "related": related})
