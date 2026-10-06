"""Role administration rules: who may create, edit and hand out which role.

Every screen that changes a role or a user's role goes through this module, so the rules hold the same way
from the user page, the role page and the bulk assign panel:

* **No privilege escalation.** An administrator can only grant permissions they hold themselves, whether by
  editing a role's matrix or by giving a user a role. Only a superuser may hand out a role that carries the
  ``*`` wildcard (Super Admin / CEO).
* **No self-service.** Nobody changes their own role; another administrator must do it. This stops an
  administrator locking themselves out by mistake and stops quiet self-promotion.
* **Wildcard roles keep their wildcard.** A role stored as ``["*"]`` is not re-saved from the matrix, because
  the matrix can only express today's modules and the role would silently lose every module added later.
* **Built-in roles are synchronised, not overwritten.** The seed runs on every deploy; it adds permissions
  the code introduced since the last deploy and removes ones the code withdrew, and leaves the
  administrator's own changes alone (see :func:`sync_system_roles`).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.notify import notify
from app.models.core import Role, Setting, User
from app.services import system as sys_svc

# Setting row that remembers which default permissions each built-in role was last given by the seed.
DEFAULTS_KEY = "system_role_defaults"


# ----------------------------------------------------------------------------- what someone holds
def held_permissions(user: Optional[User]) -> set[str]:
    """Every module.action the user effectively holds (role + extra grants - denials)."""
    if user is None or not user.is_active:
        return set()
    if user.is_superuser:
        return set(sys_svc.all_permission_strings())
    granted = sys_svc.expand_permissions(list(user.role.permissions if user.role else []) + list(user.extra_permissions or []))
    return granted - sys_svc.expand_permissions(user.denied_permissions or [])


def is_wildcard_role(role: Optional[Role]) -> bool:
    return bool(role) and "*" in (role.permissions or [])


def escalation(actor: User, permissions: Iterable[str]) -> set[str]:
    """The permissions in ``permissions`` the actor does not hold (empty set = no escalation)."""
    if actor.is_superuser:
        return set()
    return set(permissions) - held_permissions(actor)


def can_assign(actor: User, target: User, role: Optional[Role]) -> Optional[str]:
    """None when the actor may give ``role`` to ``target``; otherwise the reason it is refused."""
    if target.id == actor.id:
        return "You cannot change your own role. Ask another administrator."
    if role is None:
        return None
    if is_wildcard_role(role) and not actor.is_superuser:
        return f"Only a superuser can assign the {role.name} role."
    missing = escalation(actor, sys_svc.expand_permissions(role.permissions or []))
    if missing:
        return (f"The {role.name} role carries {len(missing)} permission(s) you do not hold "
                f"(for example {sorted(missing)[0]}), so you cannot assign it.")
    if target.is_superuser and not actor.is_superuser:
        return "Only a superuser can change the role of a superuser account."
    return None


# ----------------------------------------------------------------------------- slugs
def normalise_slug(raw: str) -> str:
    """Lower case, underscores, letters and digits only: "Front Desk-Officer" -> "front_desk_officer"."""
    slug = re.sub(r"[^a-z0-9]+", "_", (raw or "").strip().lower())
    return re.sub(r"_+", "_", slug).strip("_")[:60]


# ----------------------------------------------------------------------------- assigning
def assign_role(db: Session, actor: User, target: User, role: Optional[Role], rationale: str, request=None) -> Optional[str]:
    """Give ``target`` the ``role``. Returns None on success or the refusal reason (nothing is changed)."""
    reason = can_assign(actor, target, role)
    if reason:
        return reason
    if (target.role_id or None) == (role.id if role else None):
        return None
    old = target.role
    target.role_id = role.id if role else None
    db.flush()
    db.refresh(target)
    log_action(db, actor, "role_change", "users", entity=target, severity="warning", consequential=True,
               rationale=rationale or "Role changed by an administrator",
               description=f"Role for {target.email}: {old.name if old else 'none'} -> {role.name if role else 'none'}",
               before={"role": old.slug if old else None}, after={"role": role.slug if role else None}, request=request)
    notify(db, target, "Your role changed",
           f"Your role is now {role.name if role else 'none'}. Pages you can open have changed accordingly.",
           event_type="role_change", link="/home")
    return None


# ----------------------------------------------------------------------------- built-in roles
def _stored_defaults(db: Session) -> dict[str, list[str]]:
    row = db.query(Setting).filter(Setting.key == DEFAULTS_KEY).first()
    value = row.value if row else None
    return value if isinstance(value, dict) else {}


def sync_system_roles(db: Session) -> dict[str, Role]:
    """Create missing built-in roles and keep existing ones in step with the code, without undoing edits.

    For each role in rbac.ROLE_DEFINITIONS the seed remembers the default permission list it applied last
    time. On the next run it applies only the difference: permissions the code added are granted, and
    permissions the code removed are withdrawn. Anything an administrator added or removed by hand is left
    as they set it. The first run after this rule was introduced records the defaults without touching the
    roles, because the previous seed had already reset every role to them.
    """
    stored = _stored_defaults(db)
    first_run = not stored
    out: dict[str, Role] = {}
    for slug, spec in rbac.ROLE_DEFINITIONS.items():
        defaults = list(spec["permissions"])
        role = db.query(Role).filter(Role.slug == slug).first()
        if role is None:
            role = Role(slug=slug, name=spec["name"], portal=spec["portal"], permissions=defaults, is_system=True)
            db.add(role)
        elif not first_run and slug in stored:
            previous = set(stored[slug])
            current = list(role.permissions or [])
            if "*" not in current:
                added = [p for p in defaults if p not in previous and p not in current]
                withdrawn = previous - set(defaults)
                current = [p for p in current if p not in withdrawn] + added
                if current != list(role.permissions or []):
                    role.permissions = current
        elif not first_run and slug not in stored:
            # a role the code has just started defining, but which an administrator had created by hand
            role.permissions = sorted(set(role.permissions or []) | set(defaults))
        role.is_system = True
        out[slug] = role
        stored[slug] = defaults
    row = db.query(Setting).filter(Setting.key == DEFAULTS_KEY).first()
    if row is None:
        row = Setting(key=DEFAULTS_KEY, group="system", is_editable=False,
                      description="Default permissions last applied to each built-in role by the seed (do not edit).")
        db.add(row)
    row.value = dict(stored)
    db.flush()
    return out
