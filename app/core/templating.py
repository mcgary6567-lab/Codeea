"""Jinja2 templating with shared context (user, nav, flash, helpers)."""
from __future__ import annotations

import json
from contextvars import ContextVar
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.core import rbac
from app.core.nav import nav_for, home_for, breadcrumbs_for, section_for_path, sidebar_active_url, LIGHT, TILE, palette  # noqa: F401
from app.core.security import mask
from app.core.utils import pop_flash, money, humanize_delta, pct

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

STATUS_COLORS = {
    # generic
    "active": "emerald", "inactive": "slate", "pending": "amber", "approved": "emerald", "rejected": "rose",
    "draft": "slate", "open": "sky", "closed": "slate", "resolved": "emerald", "in_progress": "indigo",
    "completed": "emerald", "cancelled": "rose", "escalated": "rose", "paid": "emerald", "overdue": "rose",
    "partial": "amber", "sent": "sky", "void": "slate", "failed": "rose", "delivered": "emerald", "read": "emerald",
    # class sessions
    "started": "indigo", "done": "emerald", "missed": "rose", "absent": "orange", "leave": "violet",
    "rescheduled": "amber", "free": "sky",
    # students
    "trial": "sky", "frozen": "violet", "graduated": "indigo",
    # leads
    "new": "sky", "contacted": "indigo", "trial_scheduled": "violet", "trial_done": "amber", "negotiation": "orange",
    "won": "emerald", "lost": "rose",
    # priority
    "low": "slate", "medium": "sky", "high": "orange", "urgent": "rose", "critical": "rose",
    # misc
    "queued": "amber", "in_review": "indigo", "scored": "indigo", "generated": "sky", "card_generated": "violet",
    "verified": "emerald", "alpha": "amber", "beta": "indigo", "full_launch": "emerald", "planned": "slate",
    "todo": "slate", "review": "violet", "done_": "emerald", "acknowledged": "indigo", "dismissed": "slate",
    "healthy": "emerald", "degraded": "amber", "down": "rose", "unknown": "slate", "connected": "emerald",
    "simulated": "sky", "not_configured": "slate", "error": "rose", "A": "emerald", "B": "sky", "C": "amber",
    "positive": "emerald", "neutral": "slate", "negative": "rose", "converted": "emerald", "attended": "indigo",
    "no_show": "rose", "scheduled": "sky", "requested": "amber", "investigating": "amber", "confirmed": "rose",
    "qualified": "emerald", "credited": "emerald", "invited": "sky", "signed_up": "indigo", "blocked": "rose",
    "ended": "slate", "paused": "amber", "probation": "amber", "resigned": "slate", "terminated": "rose",
    "present": "emerald", "late": "amber", "half_day": "sky", "holiday": "slate", "expired": "slate",
    "succeeded": "emerald", "waiting": "amber", "reversed": "rose", "posted": "emerald", "implemented": "emerald",
    "decided": "indigo", "proposed": "amber", "achieved": "emerald", "processing": "amber",
    "analysed": "indigo", "overridden": "amber", "false_positive": "slate",
    # ERP vocabularies
    "regular": "emerald", "freeze": "violet", "on_leave": "violet", "drop_out": "rose", "black_list": "slate",
    "pass_out": "indigo", "available": "sky", "flagged": "orange", "unmapped": "slate", "mapped": "sky",
    "add": "emerald", "minus": "rose", "forward_to_verifier": "amber",
    # complaint lifecycle (app.services.complaints)
    "findings_recorded": "indigo", "pending_confirmation": "violet", "reopened": "orange",
    "minor": "slate", "moderate": "amber", "serious": "orange",
}

# Display labels that match the college's existing ERP vocabulary. Use ``{{ value|label('student') }}``.
STATUS_LABELS: dict[str, dict[str, str]] = {
    # Their task list reads Draft / Pending / Completed / Cancelled. Ours stores its own values; this only
    # changes the word on screen, never what is stored.
    "task": {"draft": "Draft", "todo": "Pending", "in_progress": "In Progress", "review": "In Review",
             "done": "Completed", "completed": "Completed", "cancelled": "Cancelled", "blocked": "Blocked"},
    "client": {"trial": "Trial", "active": "Regular", "regular": "Regular", "inactive": "Black List", "black_list": "Black List",
               "churned": "Drop Out", "drop_out": "Drop Out", "on_leave": "On Leave", "frozen": "On Leave", "pass_out": "Pass Out"},
    "student": {"trial": "Trial", "active": "Regular", "regular": "Regular", "frozen": "On Leave", "on_leave": "On Leave",
                "cancelled": "Drop Out", "drop_out": "Drop Out", "graduated": "Pass Out", "pass_out": "Pass Out",
                "black_list": "Black List", "free": "Free"},
    "subscription": {"pending_approval": "Pending Approval", "trial": "Trial", "active": "Regular", "regular": "Regular",
                     "frozen": "Freeze", "freeze": "Freeze", "cancelled": "Cancelled", "expired": "Completed", "completed": "Completed"},
    "invoice": {"draft": "Draft", "sent": "Pending", "pending": "Pending", "confirmed": "Confirmed", "partial": "Partially Paid",
                "paid": "Paid", "overdue": "Overdue", "void": "Cancelled", "cancelled": "Cancelled"},
    "receipt": {"pending": "Pending", "completed": "Confirmed", "confirmed": "Confirmed", "failed": "Failed",
                "refunded": "Refunded", "cancelled": "Cancelled"},
    "class": {"pending": "Pending", "available": "Teacher is Available", "started": "Started", "done": "Done", "missed": "Missed",
              "absent": "Student Absent", "leave": "Student On-Leave", "cancelled": "Cancelled", "rescheduled": "Rescheduled",
              "free": "Free"},
    "qa": {"queued": "Pending", "pending": "Pending", "in_review": "In-Progress", "in_progress": "In-Progress",
           "completed": "Completed", "approved": "Approved", "flagged": "Flagged", "rejected": "Rejected"},
    "request": {"pending": "Pending", "approved": "Approved", "rejected": "Rejected", "cancelled": "Cancelled"},
    "registration": {"new": "Pending", "contacted": "Forward to Verifier", "trial_scheduled": "Trial Scheduled",
                     "trial_done": "Trial Done", "negotiation": "Negotiation", "payment_pending": "Payment Pending", "won": "Converted", "lost": "Lost"},
}


def label(value: Any, kind: str = "") -> str:
    """ERP-style display label for a status value (falls back to Title Case)."""
    if value is None:
        return "-"
    table = STATUS_LABELS.get(kind, {})
    return table.get(str(value), titleize(value))


def status_color(value: Any) -> str:
    return STATUS_COLORS.get(str(value), "slate")


def badge_class(value: Any) -> str:
    c = status_color(value)
    return f"bg-{c}-50 text-{c}-700 ring-{c}-600/20 dark:bg-{c}-500/10 dark:text-{c}-300 dark:ring-{c}-500/30"


def fmt_date(value: Any, fmt: str = "%d %b %Y") -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    return value.strftime(fmt)


def fmt_datetime(value: Any) -> str:
    return fmt_date(value, "%d %b %Y, %H:%M")


def fmt_time(value: Any) -> str:
    if not value:
        return "—"
    return value.strftime("%H:%M") if hasattr(value, "strftime") else str(value)[:5]


def titleize(value: Any) -> str:
    return str(value or "").replace("_", " ").title()


def tojson_safe(value: Any) -> str:
    return json.dumps(value, default=str)


templates.env.filters.update({
    "date": fmt_date, "datetime": fmt_datetime, "time": fmt_time, "money": money, "titleize": titleize,
    "badge": badge_class, "status_color": status_color, "mask": mask, "ago": humanize_delta, "tojson_safe": tojson_safe,
    "pct": pct, "label": label,
})
def _asset_version() -> str:
    """A short fingerprint of the stylesheet and script, appended to their links as ?v=...

    Browsers cache /static files; without this a deploy that changes the stylesheet leaves every signed-in
    browser on the old one until its cache expires (the first sight of the Schedule Summary Report in
    production had white text on white because the new colour class had not reached the browser)."""
    import hashlib
    h = hashlib.md5(usedforsecurity=False)  # cache-busting fingerprint, not security
    static = Path(__file__).resolve().parents[1] / "static"
    for rel in ("css/tailwind.css", "css/app.css", "js/app.js"):
        f = static / rel
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:10]


ASSET_VERSION = _asset_version()

# Form fields drawn by the macros get ids unique within the page: the first "status" field is f_status, the next
# f_status_2, so every <label for> points at its own field even when a form appears twice (edit row and modal).
_FIELD_IDS: ContextVar[Optional[dict]] = ContextVar("oqc_field_ids", default=None)


def field_id(name: str) -> str:
    seen = _FIELD_IDS.get()
    if seen is None:
        seen = {}
        _FIELD_IDS.set(seen)
    n = seen.get(name, 0) + 1
    seen[name] = n
    return f"f_{name}" if n == 1 else f"f_{name}_{n}"


templates.env.globals.update({
    "asset_version": ASSET_VERSION,
    "app_name": settings.APP_NAME, "app_env": settings.APP_ENV, "base_url": settings.BASE_URL,
    "has_perm": rbac.has_permission, "is_ceo": rbac.is_ceo, "is_management": rbac.is_management,
    "today": date.today, "utcnow": datetime.utcnow, "MODULES": rbac.MODULES, "ACTIONS": rbac.ACTIONS,
    "TILE": TILE, "LIGHT": LIGHT, "palette": palette, "STATUS_LABELS": STATUS_LABELS, "field_id": field_id,
})


def _layout_globals() -> dict:
    """Values the shared layout needs that a page context must never shadow.

    Pages are free to pass their own ``today`` (some pass a date rather than the callable),
    so base.html uses these dedicated names instead.
    """
    return {"current_year": date.today().year, "current_date": date.today(), "current_time": datetime.utcnow()}


def render(request: Request, template: str, context: Optional[dict] = None, status_code: int = 200, **kw) -> HTMLResponse:
    """Render a template with the shared layout context. Pass ``user`` in context for authenticated pages."""
    _FIELD_IDS.set({})
    ctx: dict[str, Any] = {"request": request}
    ctx.update(context or {})
    ctx.update(kw)
    user = ctx.get("user") or getattr(request.state, "user", None)
    ctx["user"] = user
    ctx["nav"] = nav_for(user) if user else []
    ctx["home_url"] = home_for(user) if user else "/login"
    ctx["crumbs"] = breadcrumbs_for(user, request.url.path) if user else []
    ctx["flash_messages"] = pop_flash(request)
    ctx["current_path"] = request.url.path
    # The ERP keeps a sidebar of the current area's pages beside every page; this is what it lists.
    ctx["current_section"] = section_for_path(user, request.url.path) if user else None
    ctx["sidebar_active"] = sidebar_active_url(ctx["current_section"], request.url.path)
    # interactive reports (static/js/report.js): staff get the toolbar; a list page that has an import sheet gets
    # an "Import CSV" button when the user may run imports
    ctx["ir_staff"] = bool(user and getattr(user, "portal", "admin") not in ("client", "student"))
    ctx["ir_import"] = None
    if ctx["ir_staff"]:
        from app.services.migration import ENTITIES, entity_for_path
        entity = entity_for_path(request.url.path)
        if entity and rbac.has_permission(user, "migration.add"):
            ctx["ir_import"] = {"entity": entity, "label": ENTITIES[entity]["label"]}
        db_ir: Optional[Session] = getattr(request.state, "db", None)
        if db_ir is not None:
            from app.services.ir import can_export
            ctx["ir_no_export"] = not can_export(db_ir, user)
    ctx.update(_layout_globals())
    ctx.setdefault("favourites", [])
    ctx.setdefault("is_favourite", False)
    if ctx["ir_staff"]:
        db_fav: Optional[Session] = getattr(request.state, "db", None)
        if db_fav is not None:
            from app.web.favourites import for_user
            ctx["favourites"] = for_user(db_fav, user)
            ctx["is_favourite"] = any(f.path == request.url.path for f in ctx["favourites"])
    if user is not None and "unread_count" not in ctx:
        db: Optional[Session] = getattr(request.state, "db", None)
        if db is not None:
            from app.core.notify import unread_count
            ctx["unread_count"] = unread_count(db, user.id)
        else:
            ctx["unread_count"] = 0
    resp = templates.TemplateResponse(request, template, ctx, status_code=status_code)
    if user is not None and not getattr(user, "is_superuser", False):
        # links this user cannot open are dropped or turned into text (app/core/link_guard.py)
        from app.core.link_guard import filter_links
        body = filter_links(resp.body.decode("utf-8"), user).encode("utf-8")
        if body != resp.body:
            resp.body = body
            resp.headers["content-length"] = str(len(body))
    if ctx["flash_messages"]:
        resp.delete_cookie("oqc_flash")
    return resp
