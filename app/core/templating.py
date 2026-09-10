"""Jinja2 templating with shared context (user, nav, flash, helpers)."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.core import rbac
from app.core.nav import nav_for, home_for
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
    "decided": "indigo", "proposed": "amber", "achieved": "emerald", "processing": "amber", "available": "emerald",
    "analysed": "indigo", "overridden": "amber", "false_positive": "slate",
}


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
    "pct": pct,
})
templates.env.globals.update({
    "app_name": settings.APP_NAME, "app_env": settings.APP_ENV, "base_url": settings.BASE_URL,
    "has_perm": rbac.has_permission, "is_ceo": rbac.is_ceo, "is_management": rbac.is_management,
    "today": date.today, "utcnow": datetime.utcnow, "MODULES": rbac.MODULES, "ACTIONS": rbac.ACTIONS,
})


def _layout_globals() -> dict:
    """Values the shared layout needs that a page context must never shadow.

    Pages are free to pass their own ``today`` (some pass a date rather than the callable),
    so base.html uses these dedicated names instead.
    """
    return {"current_year": date.today().year, "current_date": date.today(), "current_time": datetime.utcnow()}


def render(request: Request, template: str, context: Optional[dict] = None, status_code: int = 200, **kw) -> HTMLResponse:
    """Render a template with the shared layout context. Pass ``user`` in context for authenticated pages."""
    ctx: dict[str, Any] = {"request": request}
    ctx.update(context or {})
    ctx.update(kw)
    user = ctx.get("user") or getattr(request.state, "user", None)
    ctx["user"] = user
    ctx["nav"] = nav_for(user) if user else []
    ctx["home_url"] = home_for(user) if user else "/login"
    ctx["flash_messages"] = pop_flash(request)
    ctx["current_path"] = request.url.path
    ctx.update(_layout_globals())
    if user is not None and "unread_count" not in ctx:
        db: Optional[Session] = getattr(request.state, "db", None)
        if db is not None:
            from app.core.notify import unread_count
            ctx["unread_count"] = unread_count(db, user.id)
        else:
            ctx["unread_count"] = 0
    resp = templates.TemplateResponse(request, template, ctx, status_code=status_code)
    if ctx["flash_messages"]:
        resp.delete_cookie("oqc_flash")
    return resp
