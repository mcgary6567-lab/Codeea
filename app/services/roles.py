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

# Roles HR (holders of users.assign) may give without holding every permission inside them. Kept as a Setting so
# a superuser can change it on the Permissions page; this is only the starting list.
DELEGATED_KEY = "hr_assignable_roles"
DEFAULT_DELEGATED = ["teacher", "supervisor", "academic_coordinator", "billing_rep", "lead_generator", "lead_closer",
                     "qa_officer", "hr_officer", "auditor"]  # Accountant (full access to the books) is left for a superuser to add
NEVER_DELEGATED = {"super_admin", "system_admin"}


def delegated_slugs(db: Session) -> list[str]:
    row = db.query(Setting).filter(Setting.key == DELEGATED_KEY).first()
    value = row.value if row else None
    if isinstance(value, dict) and isinstance(value.get("value"), list):
        return [s for s in value["value"] if s not in NEVER_DELEGATED]
    return list(DEFAULT_DELEGATED)


def set_delegated_slugs(db: Session, slugs: list[str]) -> list[str]:
    clean = sorted({s for s in slugs if s and s not in NEVER_DELEGATED})
    row = db.query(Setting).filter(Setting.key == DELEGATED_KEY).first()
    if row is None:
        row = Setting(key=DELEGATED_KEY, group="system", is_editable=False,
                      description="Roles HR may assign to staff without holding every permission in them.")
        db.add(row)
    row.value = {"value": clean}
    db.flush()
    return clean


def is_delegated(db: Optional[Session], actor: User, role: Optional[Role]) -> bool:
    """True when the actor may give this role through HR delegation (users.assign + role on the list)."""
    if db is None or role is None or is_wildcard_role(role) or role.slug in NEVER_DELEGATED:
        return False
    return rbac.has_permission(actor, "users.assign") and role.slug in delegated_slugs(db)


# ----------------------------------------------------------------------------- what someone holds
def held_permissions(user: Optional[User]) -> set[str]:
    """Every module.action the user effectively holds (role + extra grants - denials)."""
    if user is None or not user.is_active:
        return set()
    if user.is_superuser:
        return set(sys_svc.all_permission_strings())
    granted = sys_svc.expand_permissions(list(user.role.permissions if user.role else []) + list(user.extra_permissions or []))
    return granted - sys_svc.expand_permissions(user.denied_permissions or [])


def effective_permissions(user: User) -> set[str]:
    """What the account grants (role + extra - denied), whether or not it is active today."""
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


# Accounts fall into three audiences. A role may only move an account within its audience: a family's
# portal login must never become a staff login by a mis-tick, and a staff account never a family one.
AUDIENCES = {"admin": "staff", "teacher": "staff", "auditor": "staff", "client": "family", "student": "student"}
AUDIENCE_LABEL = {"staff": "a staff account", "family": "a family (parent) account", "student": "a student account"}


def audience(portal: Optional[str]) -> str:
    return AUDIENCES.get(portal or "admin", "staff")


def same_audience(target: User, role: Optional[Role]) -> bool:
    """A user with no role yet can receive any role; otherwise the role must serve the same audience."""
    if role is None or target.role is None:
        return True
    return audience(target.role.portal) == audience(role.portal)


def can_assign(actor: User, target: User, role: Optional[Role]) -> Optional[str]:
    """None when the actor may give ``role`` to ``target``; otherwise the reason it is refused."""
    if target.id == actor.id:
        return "You cannot change your own role. Ask another administrator."
    if role is None:
        return None
    if getattr(target, "role", None) is not None and not same_audience(target, role):
        return (f"{target.full_name or target.email} is {AUDIENCE_LABEL[audience(target.role.portal)]}; the {role.name} "
                f"role is for {AUDIENCE_LABEL[audience(role.portal)].replace('a ', '', 1).replace('an ', '', 1)}s.")
    if is_wildcard_role(role) and not actor.is_superuser:
        return f"Only a superuser can assign the {role.name} role."
    from sqlalchemy.orm import object_session
    session = object_session(actor) or object_session(role)
    missing = set() if is_delegated(session, actor, role) else escalation(actor, sys_svc.expand_permissions(role.permissions or []))
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
    as they set it. A run with no record yet grants any default a role lacks and withdraws nothing.
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
        elif first_run and "*" not in (role.permissions or []):
            # No record yet. Before this sync existed every deploy reset built-in roles to the code defaults, so a
            # default the role lacks can only be one the code added since: grant it. Nothing is withdrawn on a
            # first run, because without a record a missing default cannot be told apart from a manual removal.
            missing = [p for p in defaults if p not in (role.permissions or [])]
            if missing:
                role.permissions = list(role.permissions or []) + missing
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


# ============================================================================= access levels
# HR staff do not tick a 9-action x 78-module matrix. They pick one of four levels per module, and the
# levels translate to permissions here. The matrix stays available as the advanced editor.
LEVELS = ["none", "view", "edit", "full"]
LEVEL_LABELS = {"none": "No access", "view": "View", "edit": "Edit", "full": "Full", "partial": "Partial"}
# What each level grants.
LEVEL_ACTIONS: dict[str, frozenset[str]] = {
    "none": frozenset(),
    "view": frozenset({"view", "export"}),
    "edit": frozenset({"view", "export", "add", "update", "assign"}),
    "full": frozenset(rbac.ACTIONS),
}
# What must be held for a level to be recognised. Export and assign are granted with a level but are not
# required to recognise it: most roles hold plain ``*.view`` or ``x.view`` without export, and calling
# those "No access" would contradict the pages they can open.
LEVEL_CORE: dict[str, frozenset[str]] = {
    "none": frozenset(),
    "view": frozenset({"view"}),
    "edit": frozenset({"view", "add", "update"}),
    "full": frozenset(rbac.ACTIONS),
}
ACTION_WORDS = {"view": "open", "export": "export", "add": "create", "update": "edit", "approve": "approve",
                "delete": "delete", "execute": "run", "assign": "assign", "configure": "configure"}
OTHER_AREA = "Other"


def _holds(perms: Iterable[str], perm: str) -> bool:
    """True when ``perm`` is in ``perms`` (an expanded set or a list of patterns with wildcards)."""
    if perm in perms:
        return True
    return any(rbac._matches(p, perm) for p in perms if "*" in p)


def module_actions(perms: Iterable[str], module: str) -> set[str]:
    """The actions on ``module`` that ``perms`` covers."""
    perms = perms if isinstance(perms, (set, frozenset)) else list(perms or [])
    return {a for a in rbac.ACTIONS if _holds(perms, f"{module}.{a}")}


def level_of_actions(actions: Iterable[str]) -> str:
    acts = set(actions)
    for level in reversed(LEVELS):
        if LEVEL_CORE[level] <= acts:
            return level
    return "none"


def module_level(perms: Iterable[str], module: str) -> str:
    """The highest level (none | view | edit | full) ``perms`` holds on ``module``."""
    return level_of_actions(module_actions(perms, module))


def level_rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else 0


def _portal_of(obj) -> str:
    if obj is None:
        return "admin"
    if isinstance(obj, str):
        return obj
    if isinstance(obj, User):
        return obj.role.portal if obj.role else "admin"
    return getattr(obj, "portal", None) or "admin"


def _nav_source(portal: str) -> list[dict]:
    from app.core import nav
    return {"teacher": nav.TEACHER_NAV, "client": nav.CLIENT_NAV, "student": nav.STUDENT_NAV}.get(portal, nav.ADMIN_NAV)


def _section_items(section: dict) -> list[dict]:
    """The section's pages, one per URL (the same page can sit in two groups)."""
    seen, out = set(), []
    items = [it for g in section.get("groups", []) for it in g["items"]] if section.get("groups") else section["items"]
    for it in items:
        if it["url"] not in seen:
            seen.add(it["url"])
            out.append(it)
    return out


def _module_of(perm: str) -> str:
    return perm.partition(".")[0]


def area_modules(user_or_role=None) -> list[dict]:
    """Modules grouped by the menu area they serve, for the portal of ``user_or_role`` (or a portal name).

    A module belongs to the area whose pages use it most (ties go to the area listed first); modules that no
    menu page uses are listed under "Other". Returns ``[{"slug", "label", "modules": [{"module", "label"}]}]``.
    """
    sections = _nav_source(_portal_of(user_or_role))
    uses: dict[str, list[int]] = {}
    for i, section in enumerate(sections):
        for it in _section_items(section):
            m = _module_of(it["perm"])
            if m in rbac.MODULES:
                uses.setdefault(m, [0] * len(sections))[i] += 1
    home: dict[str, int] = {m: max(range(len(counts)), key=lambda i: (counts[i], -i)) for m, counts in uses.items()}
    out = []
    for i, section in enumerate(sections):
        mods = [{"module": m, "label": rbac.MODULES[m]} for m in rbac.MODULES if home.get(m) == i]
        if mods:
            out.append({"slug": section["slug"], "label": section["label"], "modules": mods})
    rest = [{"module": m, "label": rbac.MODULES[m]} for m in rbac.MODULES if m not in home]
    if rest:
        out.append({"slug": "other", "label": OTHER_AREA, "modules": rest})
    return out


def nav_modules(user_or_role=None) -> list[str]:
    """Modules some menu page uses, in catalogue order: the rows of the simple access editor."""
    return [m["module"] for a in area_modules(user_or_role) if a["label"] != OTHER_AREA for m in a["modules"]]


def _area_level(page_levels: list[str]) -> str:
    """The level most of the area's pages reach (the median page); "partial" when under half can be opened."""
    if not any(lv != "none" for lv in page_levels):
        return "none"
    ranked = sorted(page_levels, key=level_rank, reverse=True)
    median = ranked[(len(ranked) - 1) // 2]
    return median if median != "none" else "partial"


def access_summary(permissions: Iterable[str], portal: str = "admin") -> list[dict]:
    """For each menu area: the pages ``permissions`` can open and the level held on each module it uses.

    ``permissions`` is a role's pattern list or a user's expanded set (``held_permissions``). Each entry is
    ``{"slug", "label", "pages", "total", "level", "counts", "modules": [...], "page_list": [...]}``.
    """
    perms = permissions if isinstance(permissions, (set, frozenset)) else list(permissions or [])
    out = []
    for section in _nav_source(portal):
        items = _section_items(section)
        page_list, levels, mods = [], [], {}
        for it in items:
            m = _module_of(it["perm"])
            opens = _holds(perms, it["perm"])
            lv = module_level(perms, m) if m in rbac.MODULES else "none"
            if opens and lv == "none":
                lv = "view"
            page_level = lv if opens else "none"
            levels.append(page_level)
            page_list.append({"label": it["label"], "url": it["url"], "opens": opens, "level": page_level})
            entry = mods.setdefault(m, {"module": m, "label": rbac.MODULES.get(m, m), "level": lv if opens else module_level(perms, m),
                                        "pages": 0, "total": 0})
            entry["total"] += 1
            entry["pages"] += 1 if opens else 0
        counts = {lv: levels.count(lv) for lv in LEVELS}
        pages = sum(1 for p in page_list if p["opens"])
        opened = [lv for lv in levels if lv != "none"]
        out.append({"slug": section["slug"], "label": section["label"], "pages": pages, "total": len(items),
                    "level": _area_level(levels), "open_level": _area_level(opened) if opened else "none",
                    "counts": counts, "modules": list(mods.values()), "page_list": page_list})
    return out


def summary_sentence(summary: list[dict]) -> str:
    """'Online Academics (32 pages, edit), Billing Management (5 pages, view)' for the areas that open."""
    parts = []
    for a in summary:
        if a["pages"]:
            word = LEVEL_LABELS.get(a["open_level"], a["open_level"]).lower()
            parts.append(f"{a['label']} ({a['pages']} page{'s' if a['pages'] != 1 else ''}, {word})")
    return ", ".join(parts) if parts else "no menu pages"


def levels_for(perms: Iterable[str], modules: Iterable[str]) -> dict[str, str]:
    perms = perms if isinstance(perms, (set, frozenset)) else list(perms or [])
    return {m: module_level(perms, m) for m in modules}


def apply_levels(current: Iterable[str], chosen_levels: dict[str, str]) -> set[str]:
    """``current`` (patterns or an expanded set) with each chosen module set to exactly its level's actions."""
    out = set(sys_svc.expand_permissions(current))
    for module, level in chosen_levels.items():
        if module not in rbac.MODULES or level not in LEVEL_ACTIONS:
            continue
        out -= {f"{module}.{a}" for a in rbac.ACTIONS}
        out |= {f"{module}.{a}" for a in LEVEL_ACTIONS[level]}
    return out


def customise(role_permissions: Iterable[str], chosen_levels: dict[str, str],
              current: Optional[Iterable[str]] = None) -> tuple[list[str], list[str]]:
    """The (extra, denied) permission lists that turn a role into the chosen levels.

    ``chosen_levels`` holds only the modules the user changed; every other module stays exactly as
    ``current`` has it (default: the role itself, so untouched modules follow the role). The result
    satisfies ``role ∪ extra − denied == apply_levels(current, chosen_levels)``.
    """
    role = sys_svc.expand_permissions(role_permissions or [])
    start = sys_svc.expand_permissions(current) if current is not None else role
    target = apply_levels(start, chosen_levels)
    extra, denied = target - role, role - target
    return (sys_svc.compress_permissions(extra) if extra else [],
            sys_svc.compress_permissions(denied) if denied else [])


def changed_levels(baseline: dict[str, str], submitted: dict[str, str]) -> dict[str, str]:
    """Only the modules whose submitted level differs from what the form was pre-set to."""
    return {m: lv for m, lv in submitted.items() if lv in LEVEL_ACTIONS and baseline.get(m, "none") != lv}


def levels_from_form(form, modules: Iterable[str], prefix: str = "lvl") -> dict[str, str]:
    """Read ``lvl__<module>`` radio values; unknown modules and levels are ignored."""
    out = {}
    for m in modules:
        v = form.get(f"{prefix}__{m}")
        if v in LEVEL_ACTIONS:
            out[m] = v
    return out


def describe_overrides(user: User) -> dict[str, list[str]]:
    """A user's customisations in words: {"extra": ["Students: create, edit"], "removed": ["Audit Log: open"]}."""
    role = sys_svc.expand_permissions(user.role.permissions if user.role else [])
    extra = sys_svc.expand_permissions(user.extra_permissions or []) - role
    removed = role & sys_svc.expand_permissions(user.denied_permissions or [])
    out: dict[str, list[str]] = {"extra": [], "removed": []}
    for key, perms in (("extra", extra), ("removed", removed)):
        for m in rbac.MODULES:
            acts = [a for a in rbac.ACTIONS if f"{m}.{a}" in perms]
            if acts:
                words = "everything" if len(acts) == len(rbac.ACTIONS) else ", ".join(ACTION_WORDS[a] for a in acts)
                out[key].append(f"{rbac.MODULES[m]}: {words}")
    return out


# ============================================================================= role lists
# Which application area each built-in role belongs to on the grouped Roles page (custom roles: Configuration).
ROLE_APPS = {
    "super_admin": "Configuration", "system_admin": "Configuration", "hod_technology": "Configuration",
    "auditor": "Configuration",
    "hod_finance": "Accounts", "accountant": "Accounts",
    "billing_rep": "Billing Management", "hod_marketing": "Billing Management", "lead_generator": "Billing Management",
    "lead_closer": "Billing Management",
    "hod_people": "Human Resource", "hr_officer": "Human Resource",
    "hod_academics": "Online Academics", "academic_coordinator": "Online Academics", "manager": "Online Academics",
    "supervisor": "Online Academics", "teacher": "Online Academics", "hod_qa": "Online Academics",
    "qa_officer": "Online Academics", "academy_manager": "Online Academics",
    "head_of_admissions": "Billing Management",
    "client": "Client Portal", "student": "Client Portal",
}


def role_application(role: Role) -> str:
    return ROLE_APPS.get(role.slug, "Configuration")


def grouped_roles(db: Session, app: str = "", q: str = "") -> dict:
    """Roles grouped by application area, with active-user counts, for the ERP-style Roles page."""
    from sqlalchemy import func
    counts = dict(db.query(User.role_id, func.count(User.id)).filter(User.is_active.is_(True))
                  .group_by(User.role_id).all())
    all_roles = db.query(Role).order_by(Role.name).all()
    rows = []
    for r in all_roles:
        application = role_application(r)
        if app and application != app:
            continue
        if q and q.lower() not in (r.name or "").lower() and q.lower() not in (r.slug or "").lower():
            continue
        rows.append({"r": r, "app": application, "users": counts.get(r.id, 0),
                     "status": "active" if r.permissions else "inactive"})
    rows.sort(key=lambda x: (x["app"], x["r"].name))
    groups: list[dict] = []
    for row in rows:
        if not groups or groups[-1]["app"] != row["app"]:
            groups.append({"app": row["app"], "rows": []})
        groups[-1]["rows"].append(row)
    by_app: dict[str, int] = {}
    for r in all_roles:
        by_app[role_application(r)] = by_app.get(role_application(r), 0) + 1
    return {"groups": groups, "by_app": by_app,
            "stats": {"total": len(all_roles), "assigned": sum(v for k, v in counts.items() if k is not None),
                      "system": sum(1 for r in all_roles if r.is_system), "apps": len(by_app)}}


def staff_roles(db: Session) -> list[Role]:
    """Roles for staff accounts (admin, teacher and auditor portals), by name."""
    return [r for r in db.query(Role).order_by(Role.name).all() if audience(r.portal) == "staff"]


def assignable_staff_roles(db: Session, actor: User, target: Optional[User] = None) -> list[Role]:
    """Staff roles ``actor`` may give ``target`` (a new account when None), applying :func:`can_assign`."""
    probe = target if target is not None else User(id=-1, email="", is_superuser=False)
    return [r for r in staff_roles(db) if can_assign(actor, probe, r) is None]
