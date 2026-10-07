"""HR > Users & Access: staff sign-ins, the guided Create User, the simple user page and the Permissions overview.

The college's previous ERP made a user in one pass: Create User -> Select Employee -> Select Role -> the role's
access applies -> customise if required -> Save. These screens follow that path. Access is shown and changed in
four levels per module (No access / View / Edit / Full) instead of the 9-action matrix, which stays available as
the advanced option on /admin/users/{id} and /admin/roles/{id}.

Every role and access change goes through app.services.roles (can_assign, assign_role, escalation), so the rules
are the same here as on the advanced pages: nobody changes their own role or access, nobody grants what they do
not hold, only a superuser hands out a full-access role, and staff, family and student accounts never mix.
Family and student logins are managed from their own records and are not listed here.
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings as cfg
from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import csrf_protect, require
from app.core.notify import notify
from app.core.security import hash_password
from app.core.templating import render
from app.core.utils import paginate, parse_bool, parse_int, redirect
from app.database import get_db
from app.models.core import Department, Role, User
from app.models.people import Employee
from app.services import roles as role_svc
from app.services import system as sys_svc

router = APIRouter(prefix="/hr", dependencies=[Depends(csrf_protect)])

STAFF_PORTALS = ("admin", "teacher", "auditor")
ACTIVE_EMPLOYEE = ("active", "probation", "on_leave")
STATUSES = [("active", "Active"), ("inactive", "In-active"), ("never", "Never signed in"),
            ("unlinked", "No employee linked"), ("locked", "Locked out")]
SELF_REASON = "You cannot change your own role or access. Ask another administrator."


# ============================================================================= helpers
def _rationale(form) -> str:
    return (form.get("rationale") or form.get("reason") or "").strip()


def _staff_query(db: Session):
    """Staff accounts only: a role on the admin, teacher or auditor portal, or no role yet."""
    return (db.query(User).outerjoin(Role, User.role_id == Role.id)
            .filter(or_(User.role_id.is_(None), Role.portal.in_(STAFF_PORTALS))))


def _is_staff(u: User) -> bool:
    return u.role is None or role_svc.audience(u.role.portal) == "staff"


def _employee_of(db: Session, u: User) -> Employee | None:
    return db.query(Employee).filter(Employee.user_id == u.id).first()


def _editor_areas() -> list[dict]:
    """The rows of the simple access editor: modules a staff menu page uses, grouped by menu area."""
    return [a for a in role_svc.area_modules("admin") if a["label"] != role_svc.OTHER_AREA]


def _editor_modules() -> list[str]:
    return role_svc.nav_modules("admin")


def _opened(summary: list[dict]) -> list[dict]:
    return [{"label": a["label"], "pages": a["pages"], "total": a["total"],
             "level": role_svc.LEVEL_LABELS.get(a["open_level"], a["open_level"])} for a in summary if a["pages"]]


def _access_refusal(actor: User, target: User) -> str | None:
    """Why ``actor`` may not customise ``target``'s access (None when allowed)."""
    if target.id == actor.id:
        return SELF_REASON
    if target.is_superuser and not actor.is_superuser:
        return "Only a superuser can change the access of a superuser account."
    if target.is_superuser:
        return "A superuser account already has full access; there is nothing to customise."
    if role_svc.is_wildcard_role(target.role):
        return f"The {target.role.name} role has full access; there is nothing to customise."
    if not _is_staff(target):
        return "Family and student logins are managed from their own records."
    return None


def _roles_payload(roles: list[Role]) -> dict:
    """Everything the create page needs to switch the preview client-side, per role id."""
    modules = _editor_modules()
    out = {}
    for r in roles:
        summary = role_svc.access_summary(r.permissions or [], r.portal or "admin")
        out[str(r.id)] = {"name": r.name, "sentence": role_svc.summary_sentence(summary),
                          "areas": _opened(summary), "levels": role_svc.levels_for(r.permissions or [], modules)}
    return out


# ============================================================================= list
@router.get("/users", include_in_schema=False)
def users_page(request: Request, page: int = 1, q: str = "", role: str = "", department: str = "", status: str = "",
               db: Session = Depends(get_db), user: User = Depends(require("users.view"))):
    base = _staff_query(db).outerjoin(Employee, Employee.user_id == User.id)
    now = datetime.utcnow()
    stats = {
        "total": base.count(),
        "active": base.filter(User.is_active.is_(True)).count(),
        "inactive": base.filter(User.is_active.is_(False)).count(),
        "never": base.filter(User.last_login_at.is_(None)).count(),
        "unlinked": base.filter(Employee.id.is_(None)).count(),
    }
    qry = base
    if q:
        like = f"%{q.strip()}%"
        qry = qry.filter(or_(User.full_name.ilike(like), User.email.ilike(like), Employee.employee_code.ilike(like),
                             Employee.full_name.ilike(like)))
    if role == "none":
        qry = qry.filter(User.role_id.is_(None))
    elif role:
        qry = qry.filter(Role.slug == role)
    if department:
        dep = parse_int(department)
        qry = qry.filter(or_(Employee.department_id == dep, User.department_id == dep))
    if status == "active":
        qry = qry.filter(User.is_active.is_(True))
    elif status == "inactive":
        qry = qry.filter(User.is_active.is_(False))
    elif status == "never":
        qry = qry.filter(User.last_login_at.is_(None))
    elif status == "unlinked":
        qry = qry.filter(Employee.id.is_(None))
    elif status == "locked":
        qry = qry.filter(User.locked_until.isnot(None), User.locked_until > now)
    pg = paginate(qry.order_by(User.full_name), page, 30)
    ids = [u.id for u in pg.items]
    employees = {e.user_id: e for e in db.query(Employee).filter(Employee.user_id.in_(ids or [-1])).all()}
    role_options = [(r.slug, r.name) for r in role_svc.staff_roles(db)] + [("none", "No role")]
    return render(request, "hr_users/list.html", {
        "user": user, "page": pg, "q": q, "role": role, "department": department, "status": status,
        "stats": stats, "employees": employees, "role_options": role_options, "statuses": STATUSES, "now": now,
        "departments": [(d.id, d.name) for d in db.query(Department).order_by(Department.name).all()],
        "base_url": f"/hr/users?q={q}&role={role}&department={department}&status={status}"})


# ============================================================================= create
@router.get("/users/new", include_in_schema=False)
def user_new(request: Request, employee_id: int | None = None, db: Session = Depends(get_db),
             user: User = Depends(require("users.add"))):
    employees = (db.query(Employee).filter(Employee.user_id.is_(None), Employee.status.in_(ACTIVE_EMPLOYEE))
                 .order_by(Employee.full_name).all())
    roles = role_svc.assignable_staff_roles(db, user)
    return render(request, "hr_users/new.html", {
        "user": user, "employees": employees, "roles": roles, "preselect": employee_id,
        "roles_json": json.dumps(_roles_payload(roles)).replace("</", "<\\/"), "areas": _editor_areas(),
        "LEVELS": role_svc.LEVELS, "LEVEL_LABELS": role_svc.LEVEL_LABELS,
        "departments": [(d.id, d.name) for d in db.query(Department).order_by(Department.name).all()]})


@router.post("/users/new", include_in_schema=False)
async def user_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("users.add"))):
    form = await request.form()
    back = "/hr/users/new"
    not_employee = parse_bool(form.get("not_employee"))
    employee = None
    if not not_employee:
        employee = db.get(Employee, parse_int(form.get("employee_id")) or -1)
        if employee is None:
            return redirect(back, "Choose the employee this sign-in is for, or tick \"This person is not an employee\".", "error")
        if employee.user_id:
            return redirect(back, f"{employee.full_name} already has a user account.", "error")
        if employee.status not in ACTIVE_EMPLOYEE:
            return redirect(back, f"{employee.full_name} is not an active employee.", "error")
    full_name = (form.get("full_name") or (employee.full_name if employee else "")).strip()
    email = (form.get("email") or (employee.email if employee else "") or "").strip().lower()
    if not full_name or not email or "@" not in email:
        return redirect(back, "A full name and a valid email address are required.", "error")
    if db.query(User).filter(func.lower(User.email) == email).first():
        return redirect(back, f"A user with the email {email} already exists.", "error")

    chosen = db.get(Role, parse_int(form.get("role_id")) or -1)
    if chosen is None:
        return redirect(back, "Choose a role. The role decides what the new user can open.", "error")
    if role_svc.audience(chosen.portal) != "staff":
        return redirect(back, "Family and student logins are created from the client or student record.", "error")
    refused = role_svc.can_assign(user, User(id=-1, email=email, is_superuser=False), chosen)
    if refused:
        return redirect(back, refused, "error")

    extra, denied = [], []
    if parse_bool(form.get("customise")):
        modules = _editor_modules()
        changed = role_svc.changed_levels(role_svc.levels_for(chosen.permissions or [], modules),
                                          role_svc.levels_from_form(form, modules))
        extra, denied = role_svc.customise(chosen.permissions or [], changed)
        missing = role_svc.escalation(user, sys_svc.expand_permissions(extra))
        if missing:
            return redirect(back, f"You cannot give access you do not hold yourself ({len(missing)} permission(s), for example "
                            f"{sorted(missing)[0]}). Lower that level or ask an administrator who holds it.", "error")

    raw, generated, errs = sys_svc.choose_password(form.get("password"))
    if errs:
        return redirect(back, "Password too weak: " + ", ".join(errs) + ". Leave it blank to generate one.", "error")

    obj = User(email=email, username=sys_svc.username_from_email(db, email), full_name=full_name,
               hashed_password=hash_password(raw), role_id=chosen.id,
               department_id=(employee.department_id if employee else parse_int(form.get("department_id"))),
               branch_id=employee.branch_id if employee else None,
               phone=(form.get("phone") or (employee.phone if employee else "") or "").strip() or None,
               timezone=cfg.DEFAULT_TIMEZONE, language="en", is_active=True,
               must_change_password=parse_bool(form.get("must_change_password")) or generated,
               extra_permissions=extra, denied_permissions=denied)
    db.add(obj)
    db.flush()
    if employee is not None:
        employee.user_id = obj.id
    who = f"employee {employee.employee_code} ({employee.full_name})" if employee else "a non-employee"
    custom = f"; customised access: extra {extra or 'none'}, removed {denied or 'none'}" if (extra or denied) else ""
    log_action(db, user, "create", "users", entity=obj, severity="warning", consequential=True,
               rationale=_rationale(form) or "Created from HR Users",
               description=f"Created user {obj.email} for {who} with the {chosen.name} role{custom}",
               after=snapshot(obj), request=request)
    notify(db, obj, "Welcome to Online Quran College OS",
           f"Your account has been created with the {chosen.name} role. Sign in and choose your own password on first use.",
           event_type="account_created", link="/home")
    db.commit()
    msg = f"User created for {obj.full_name} with the {chosen.name} role."
    if extra or denied:
        msg += " Their access was customised."
    if generated:
        msg += f" Temporary password (shown once, copy it now): {raw}"
    return redirect(f"/hr/users/{obj.id}", msg)


# ============================================================================= user page
@router.get("/users/{user_id}", include_in_schema=False)
def user_page(user_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("users.view"))):
    obj = db.get(User, user_id)
    if obj is None:
        return redirect("/hr/users", "That user no longer exists.", "error")
    if not _is_staff(obj):
        return redirect(f"/admin/users/{obj.id}", "Family and student logins are managed from their own records; "
                        "this is the advanced account page.", "info")
    effective = role_svc.effective_permissions(obj)
    summary = role_svc.access_summary(effective, obj.portal)
    modules = _editor_modules()
    role_perms = obj.role.permissions if obj.role else []
    return render(request, "hr_users/detail.html", {
        "user": user, "obj": obj, "employee": _employee_of(db, obj), "now": datetime.utcnow(),
        "summary": summary, "areas_open": _opened(summary), "sentence": role_svc.summary_sentence(summary),
        "overrides": role_svc.describe_overrides(obj),
        "roles": [r for r in role_svc.assignable_staff_roles(db, user, obj) if r.id != obj.role_id],
        "role_refusal": SELF_REASON if obj.id == user.id else None,
        "access_refusal": _access_refusal(user, obj),
        "areas": _editor_areas(), "levels": role_svc.levels_for(effective, modules),
        "role_levels": role_svc.levels_for(role_perms, modules),
        "LEVELS": role_svc.LEVELS, "LEVEL_LABELS": role_svc.LEVEL_LABELS,
        "can_update": rbac.has_permission(user, "users.update"),
        "can_configure": rbac.has_permission(user, "users.configure")})


def _target(db: Session, user_id: int) -> User | None:
    obj = db.get(User, user_id)
    return obj if obj is not None and _is_staff(obj) else None


@router.post("/users/{user_id}/role", include_in_schema=False)
async def user_role(user_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("users.update"))):
    obj = _target(db, user_id)
    if obj is None:
        return redirect("/hr/users", "Only staff accounts are managed here.", "error")
    form = await request.form()
    back = f"/hr/users/{obj.id}"
    rationale = _rationale(form)
    if not rationale:
        return redirect(back, "Say why the role is changing. The reason is written to the audit log.", "error")
    role = db.get(Role, parse_int(form.get("role_id")) or -1)
    if role is None:
        return redirect(back, "Choose the new role.", "error")
    if role.id == obj.role_id:
        return redirect(back, f"{obj.full_name} already has the {role.name} role.", "info")
    refused = role_svc.assign_role(db, user, obj, role, rationale, request=request)
    if refused:
        return redirect(back, refused, "error")
    db.commit()
    tail = " Their access customisations still apply on top of it." if (obj.extra_permissions or obj.denied_permissions) else ""
    return redirect(back, f"{obj.full_name} now has the {role.name} role.{tail}")


@router.post("/users/{user_id}/access", include_in_schema=False)
async def user_access(user_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("users.configure"))):
    obj = db.get(User, user_id)
    if obj is None:
        return redirect("/hr/users", "That user no longer exists.", "error")
    back = f"/hr/users/{obj.id}"
    refused = _access_refusal(user, obj)
    if refused:
        return redirect(back, refused, "error")
    form = await request.form()
    rationale = _rationale(form)
    if not rationale:
        return redirect(back + "#access", "Say why the access is changing. The reason is written to the audit log.", "error")
    before = {"extra_permissions": list(obj.extra_permissions or []), "denied_permissions": list(obj.denied_permissions or [])}
    role_perms = obj.role.permissions if obj.role else []
    if parse_bool(form.get("reset")):
        if not before["extra_permissions"] and not before["denied_permissions"]:
            return redirect(back, f"{obj.full_name} already has exactly the role's access.", "info")
        obj.extra_permissions, obj.denied_permissions = [], []
        msg = f"{obj.full_name}'s access is back to the {obj.role.name if obj.role else 'role'}'s access."
    else:
        effective = role_svc.effective_permissions(obj)
        modules = _editor_modules()
        changed = role_svc.changed_levels(role_svc.levels_for(effective, modules), role_svc.levels_from_form(form, modules))
        if not changed:
            return redirect(back, "No access level was changed.", "info")
        missing = role_svc.escalation(user, role_svc.apply_levels(effective, changed) - effective)
        if missing:
            return redirect(back + "#access", f"You cannot give access you do not hold yourself ({len(missing)} permission(s), "
                            f"for example {sorted(missing)[0]}). Nothing was changed.", "error")
        extra, denied = role_svc.customise(role_perms, changed, current=effective)
        obj.extra_permissions, obj.denied_permissions = extra, denied
        words = ", ".join(f"{rbac.MODULES[m]} to {role_svc.LEVEL_LABELS[lv]}" for m, lv in changed.items())
        msg = f"Access updated for {obj.full_name}: {words}."
    after = {"extra_permissions": obj.extra_permissions, "denied_permissions": obj.denied_permissions}
    log_action(db, user, "permission_change", "users", entity=obj, severity="warning", consequential=True,
               rationale=rationale, description=f"Access customised for {obj.email} from HR Users",
               before=before, after=after, request=request)
    notify(db, obj, "Your access changed", "An administrator changed what you can open. Pages you can open have changed accordingly.",
           event_type="role_change", link="/home")
    db.commit()
    return redirect(back, msg)


@router.post("/users/{user_id}/status", include_in_schema=False)
async def user_status(user_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("users.update"))):
    obj = _target(db, user_id)
    if obj is None:
        return redirect("/hr/users", "Only staff accounts are managed here.", "error")
    form = await request.form()
    activate = parse_bool(form.get("activate"))
    refused = sys_svc.set_user_active(db, user, obj, activate, _rationale(form), request=request)
    if refused:
        return redirect(f"/hr/users/{obj.id}", refused, "error")
    db.commit()
    return redirect(f"/hr/users/{obj.id}", f"{obj.full_name} can sign in again." if activate
                    else f"{obj.full_name} is deactivated and signed out everywhere.")


@router.post("/users/{user_id}/reset-password", include_in_schema=False)
async def user_reset_password(user_id: int, request: Request, db: Session = Depends(get_db),
                              user: User = Depends(require("users.update"))):
    obj = _target(db, user_id)
    if obj is None:
        return redirect("/hr/users", "Only staff accounts are managed here.", "error")
    form = await request.form()
    raw, refused = sys_svc.reset_user_password(db, user, obj, form.get("password"), _rationale(form), request=request)
    if refused:
        return redirect(f"/hr/users/{obj.id}", refused, "error")
    db.commit()
    return redirect(f"/hr/users/{obj.id}", f"Password reset. Temporary password (shown once, copy it now): {raw}")


@router.post("/users/{user_id}/unlock", include_in_schema=False)
async def user_unlock(user_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("users.update"))):
    obj = _target(db, user_id)
    if obj is None:
        return redirect("/hr/users", "Only staff accounts are managed here.", "error")
    form = await request.form()
    sys_svc.unlock_user(db, user, obj, _rationale(form), request=request)
    db.commit()
    return redirect(f"/hr/users/{obj.id}", f"{obj.full_name} is unlocked and can sign in.")


# ============================================================================= permissions overview
def _overview_rows(db: Session) -> list[dict]:
    rows = []
    for r in role_svc.staff_roles(db):
        summary = role_svc.access_summary(r.permissions or [], "admin")
        rows.append({"role": r, "cells": summary})
    return rows


@router.get("/permissions", include_in_schema=False)
def permissions_page(request: Request, user_id: int | None = None, db: Session = Depends(get_db),
                     user: User = Depends(require("roles.view"))):
    from app.core import nav
    rows = _overview_rows(db)
    person, person_summary = None, None
    if user_id:
        person = db.get(User, user_id)
        if person is not None:
            person_summary = role_svc.access_summary(role_svc.held_permissions(person), person.portal)
    people = (_staff_query(db).filter(User.is_active.is_(True)).order_by(User.full_name).all())
    counts = dict(db.query(User.role_id, func.count(User.id)).filter(User.is_active.is_(True)).group_by(User.role_id).all())
    return render(request, "hr_users/permissions.html", {
        "user": user, "rows": rows, "areas": [s["label"] for s in nav.ADMIN_NAV], "counts": counts,
        "people": [(p.id, f"{p.full_name} ({p.email})") for p in people],
        "person": person, "person_summary": person_summary,
        "person_sentence": role_svc.summary_sentence(person_summary) if person_summary else "",
        "overrides": role_svc.describe_overrides(person) if person is not None else None,
        "LEVEL_LABELS": role_svc.LEVEL_LABELS,
        "delegated": set(role_svc.delegated_slugs(db)),
        "delegable_roles": [r for r in db.query(Role).filter(Role.portal.in_(["admin", "teacher", "auditor"])).order_by(Role.name)
                            if r.slug not in role_svc.NEVER_DELEGATED and not role_svc.is_wildcard_role(r)]})


@router.post("/permissions/delegation", include_in_schema=False)
async def permissions_delegation(request: Request, db: Session = Depends(get_db), user: User = Depends(require("roles.configure"))):
    """A superuser decides which roles HR may hand out."""
    if not user.is_superuser:
        return redirect("/hr/permissions#delegation", "Only a superuser can change which roles HR may assign.", "error")
    form = await request.form()
    rationale = (form.get("rationale") or "").strip()
    if not rationale:
        return redirect("/hr/permissions#delegation", "A rationale is required.", "error")
    before = role_svc.delegated_slugs(db)
    after = role_svc.set_delegated_slugs(db, form.getlist("slugs"))
    log_action(db, user, "permission_change", "roles", severity="warning", consequential=True, rationale=rationale,
               description="Changed the roles HR may assign", before={"roles": before}, after={"roles": after}, request=request)
    db.commit()
    return redirect("/hr/permissions#delegation", f"HR may now assign {len(after)} role(s).")
