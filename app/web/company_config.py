"""Configuration — the college ERP's "Configuration" area (docs/AUDIT_ACCOUNTS_CONFIG.md).

An index of cards and, behind them, the screens their staff already know:

    /config/lookups            every configurable value list, grouped by App, with its values
    /config/branch-properties  their Setup screen: named settings with a description and their own Save
    /config/currency-rates     Add Manual Currency Rate + a (simulated) ERP Currency Rates feed
    /config/payment-gateways   the gateway catalogue with its default transaction fee
    /config/whatsapp-senders   connected WhatsApp numbers with their send throttle and QR reconnection
    /config/support-tickets    requests raised to whoever maintains the software
    /config/otp                one-time password Setup and the per-user token status
    /config/roles              roles grouped by application area (read only; editing stays on /admin/roles)

Lookups and branch properties come first because the rest of the system reads them: the attendance grace
periods decide when a late arrival attracts a fine and the advance invoice days decide how far ahead billing
runs. Nothing here queries the lookup tables directly — `app.services.lookups` is the single reader, and every
mutation on this page invalidates its cache.

Every list has a filter bar, a bordered table with the ERP's column labels and a Create action; every mutation
writes an audit event and redirects (303) with a flash message.
"""
from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import PermissionDenied, csrf_protect, get_current_user, require
from app.core.templating import render
from app.core.utils import next_code, paginate, parse_bool, parse_date, parse_float, parse_int, redirect
from app.database import get_db
from app.models.config_erp import (LOOKUP_APPS, TICKET_PRIORITIES, TICKET_STATUSES, TICKET_TYPES, Lookup,
                                   LookupValue, OtpConfiguration, PaymentGateway, SupportTicket, WhatsAppSender)
from app.models.core import Department, Role, Setting, User
from app.models.erp import BeneficiaryAccount
from app.models.finance import Currency, ExchangeRateHistory
from app.services import lookups as lookup_service

router = APIRouter(prefix="/config", dependencies=[Depends(csrf_protect)])

BASE = "/config"
MODULE = "configuration"

STATUSES = [("active", "Active"), ("inactive", "Inactive")]
VALUE_TYPES = [("text", "Text"), ("number", "Number"), ("boolean", "Yes / No"), ("json", "JSON"),
               ("html", "HTML"), ("image", "Image URL")]
# Branch Properties tabs, exactly as their Setup screen shows them. Settings our own modules add carry
# other groups, and property_tabs() appends those, so nothing is reachable only under Show All.
PROPERTY_TABS = [("all", "Show All"), ("general", "General"), ("hr", "HR"), ("academics", "Academics"),
                 ("accounts", "Accounts"), ("billing", "Billing")]
OTP_CHANNELS = [("email", "Email"), ("whatsapp", "WhatsApp"), ("sms", "SMS")]
SENDER_PURPOSES = [("general", "General"), ("academics", "Academics"), ("billing", "Billing"),
                   ("marketing", "Marketing")]
GATEWAY_COMPANIES = ["Stripe", "PayPal", "Wise", "Payoneer", "Bank Alfalah", "Other"]
TICKET_MODULES = ["Online Academics", "Billing Management", "Human Resource", "Accounts", "Configuration",
                  "Client Portal", "Reports"]
PRIORITY_LABELS = {"low": "Low", "normal": "Normal", "urgent": "Urgent", "very_urgent": "Very Urgent"}
TICKET_STATUS_LABELS = {"pending": "Pending", "in_progress": "In Progress", "resolved": "Resolved",
                        "rejected": "Rejected", "closed": "Closed"}
# Which application area a role belongs to, mirroring the ERP's Roles grouping.
ROLE_APPS = {
    "super_admin": "Configuration", "system_admin": "Configuration", "hod_technology": "Configuration",
    "auditor": "Configuration",
    "hod_finance": "Accounts", "accountant": "Accounts",
    "billing_rep": "Billing Management", "hod_marketing": "Billing Management", "lead_generator": "Billing Management",
    "lead_closer": "Billing Management",
    "hod_people": "Human Resource", "hr_officer": "Human Resource",
    "hod_academics": "Online Academics", "academic_coordinator": "Online Academics", "manager": "Online Academics",
    "supervisor": "Online Academics", "teacher": "Online Academics", "hod_qa": "Online Academics",
    "qa_officer": "Online Academics",
    "client": "Client Portal", "student": "Client Portal",
}

CARDS = [  # title, url, icon, blurb
    ("Currency Rates", f"{BASE}/currency-rates", "coins", "Rates to base, manual entry and the ERP feed"),
    ("Roles", f"{BASE}/roles", "shield", "Roles by application with assigned users"),
    ("Lookups", f"{BASE}/lookups", "list-tree", "Every configurable value list, grouped by App"),
    ("Setup (Branch Properties)", f"{BASE}/branch-properties", "sliders-horizontal", "Named settings the college runs on"),
    ("Notification Templates", "/admin/notifications", "send", "Message templates per event and channel"),
    ("Support Ticket", f"{BASE}/support-tickets", "life-buoy", "Requests raised to whoever maintains the software"),
    ("WhatsApp Numbers", f"{BASE}/whatsapp-senders", "message-circle", "Connected senders with their throttle"),
    ("OTP Configuration", f"{BASE}/otp", "key-round", "One-time passwords: setup and per-user tokens"),
    ("Payment Gateways", f"{BASE}/payment-gateways", "credit-card", "Gateways with their default transaction fee"),
]


# =============================================================================== helpers
def _get(db: Session, model, id: int, label: str):
    obj = db.get(model, id)
    if not obj:
        raise HTTPException(404, f"{label} not found")
    return obj


def _perms(user: User) -> dict:
    return {"can_add": rbac.has_permission(user, "settings.update"),
            "can_edit": rbac.has_permission(user, "settings.update"),
            "can_configure": rbac.has_permission(user, "settings.configure"),
            "is_sysadmin": is_system_administrator(user)}


def is_system_administrator(user: User) -> bool:
    """Who may close a support ticket or write developer remarks."""
    return bool(user and (user.is_superuser or user.role_slug in ("super_admin", "system_admin", "hod_technology")
                          or rbac.has_permission(user, "settings.configure")))


def _status_field(form, default: str = "active") -> str:
    v = (form.get("status") or default).strip().lower()
    return v if v in ("active", "inactive") else default


def _toggle(db: Session, user: User, request: Request, obj, label: str, back: str):
    before = snapshot(obj)
    obj.status = "inactive" if obj.status == "active" else "active"
    log_action(db, user, "status_change", MODULE, entity=obj, description=f"{label} marked {obj.status}",
               before=before, after=snapshot(obj), request=request)
    db.commit()
    return redirect(back, f"{label} marked {obj.status}.")


def _rationale(form) -> str:
    return (form.get("rationale") or form.get("reason") or "").strip()


# ------------------------------------------------------------------ branch property values
def setting_value(s: Setting):
    """Unwrap the stored JSON into the single value the Branch Properties row edits."""
    v = s.value
    if isinstance(v, dict) and list(v.keys()) == ["value"]:
        return v["value"]
    return v


def effective_type(s: Setting) -> str:
    """The value type the screen edits with: what the row declares, corrected for what it actually holds."""
    value, vt = setting_value(s), (s.value_type or "text")
    if isinstance(value, (dict, list)):
        return "json"
    if vt != "text":
        return vt
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "text"


def coerce_setting(value_type: str, raw, current=None):
    """Turn a submitted form field into the value type the setting declares."""
    if value_type == "boolean":
        return parse_bool(raw)
    if value_type == "number":
        if raw in (None, ""):
            return current if isinstance(current, (int, float)) else 0
        num = parse_float(raw, 0.0)
        return int(num) if float(num).is_integer() else num
    if value_type == "json":
        if raw in (None, ""):
            return current
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            raise ValueError("That is not valid JSON.")
    return "" if raw is None else str(raw)


def store_setting(s: Setting, new_value) -> None:
    """Write the value back in the shape the row already uses ({"value": x} or a bare JSON value)."""
    if isinstance(s.value, dict) and list(s.value.keys()) == ["value"]:
        s.value = {"value": new_value}
    else:
        s.value = new_value


def _property_rows(db: Session, tab: str, q: str = "") -> list[Setting]:
    query = db.query(Setting)
    if tab and tab != "all":
        query = query.filter(Setting.group == tab)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(func.lower(func.coalesce(Setting.label, Setting.key)).like(like))
    return query.order_by(Setting.group, Setting.sort_no, Setting.key).all()


# ------------------------------------------------------------------ WhatsApp throttle
def next_send_at(sender: WhatsAppSender) -> datetime:
    """The earliest moment this sender may send again, from its interval and messages-per-cycle.

    A sender that has never sent is ready now. Otherwise the throttle spreads ``messages_per_cycle``
    messages evenly across ``interval_seconds``.
    """
    if sender is None:
        return datetime.utcnow()
    last = sender.last_message_sent_at
    if not last:
        return datetime.utcnow()
    per_cycle = max(1, int(sender.messages_per_cycle or 1))
    gap = float(sender.interval_seconds or 0) / per_cycle
    return last + timedelta(seconds=gap)


def sender_is_ready(sender: WhatsAppSender, now: Optional[datetime] = None) -> bool:
    return next_send_at(sender) <= (now or datetime.utcnow())


def pick_sender(db: Session, purpose: str = "general", now: Optional[datetime] = None) -> Optional[WhatsAppSender]:
    """The connected sender whose throttle allows the next message, or None if every one is throttled.

    Prefers a sender configured for ``purpose``; falls back to the general senders. Among the candidates
    that are ready it picks the one idle longest, so traffic spreads across the numbers.
    """
    now = now or datetime.utcnow()
    candidates = (db.query(WhatsAppSender)
                  .filter(WhatsAppSender.status == "active", WhatsAppSender.api_status == "connected")
                  .order_by(WhatsAppSender.id).all())
    for pool in ([s for s in candidates if s.purpose == purpose],
                 [s for s in candidates if s.purpose == "general"],
                 candidates):
        ready = [s for s in pool if sender_is_ready(s, now)]
        if ready:
            return min(ready, key=lambda s: (s.last_message_sent_at or datetime.min, s.id))
    return None


# =============================================================================== 1. index
@router.get("", include_in_schema=False)
def index(request: Request, db: Session = Depends(get_db), user: User = Depends(require("settings.view"))):
    counts = {
        f"{BASE}/currency-rates": db.query(func.count(Currency.id)).scalar() or 0,
        f"{BASE}/roles": db.query(func.count(Role.id)).scalar() or 0,
        f"{BASE}/lookups": db.query(func.count(Lookup.id)).scalar() or 0,
        f"{BASE}/branch-properties": db.query(func.count(Setting.id)).scalar() or 0,
        f"{BASE}/support-tickets": db.query(func.count(SupportTicket.id)).scalar() or 0,
        f"{BASE}/whatsapp-senders": db.query(func.count(WhatsAppSender.id)).scalar() or 0,
        f"{BASE}/otp": db.query(func.count(User.id)).filter(User.two_factor_enabled.is_(True)).scalar() or 0,
        f"{BASE}/payment-gateways": db.query(func.count(PaymentGateway.id)).scalar() or 0,
    }
    cards = [{"title": t, "url": u, "icon": i, "blurb": b, "count": counts.get(u)} for t, u, i, b in CARDS]
    return render(request, "company_config/index.html", {"user": user, "cards": cards})


# =============================================================================== 2. lookups
@router.get("/lookups", include_in_schema=False)
def lookups_page(request: Request, app: str = "", status: str = "", q: str = "",
                 db: Session = Depends(get_db), user: User = Depends(require("settings.view"))):
    query = db.query(Lookup)
    if app:
        query = query.filter(Lookup.app == app)
    if status:
        query = query.filter(Lookup.status == status)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(func.lower(Lookup.description).like(like) | func.lower(Lookup.code).like(like))
    rows = query.order_by(Lookup.app, Lookup.sort_no, Lookup.description).all()
    value_counts = dict(db.query(LookupValue.lookup_id, func.count(LookupValue.id))
                        .filter(LookupValue.status == "active").group_by(LookupValue.lookup_id).all())
    groups: list[dict] = []           # control break by App, exactly as theirs is
    for row in rows:
        if not groups or groups[-1]["app"] != row.app:
            groups.append({"app": row.app, "rows": []})
        groups[-1]["rows"].append(row)
    stats = {"total": db.query(func.count(Lookup.id)).scalar() or 0,
             "active": db.query(func.count(Lookup.id)).filter(Lookup.status == "active").scalar() or 0,
             "system": db.query(func.count(Lookup.id)).filter(Lookup.is_system.is_(True)).scalar() or 0,
             "value_total": db.query(func.count(LookupValue.id)).scalar() or 0}
    by_app = dict(db.query(Lookup.app, func.count(Lookup.id)).group_by(Lookup.app).all())
    return render(request, "company_config/lookups.html", {
        "user": user, "groups": groups, "value_counts": value_counts, "stats": stats, "by_app": by_app,
        "apps": LOOKUP_APPS, "statuses": STATUSES, "app": app, "status": status, "q": q,
        "next_sort": (db.query(func.max(Lookup.sort_no)).scalar() or 0) + 1, **_perms(user)})


def _apply_lookup(lookup: Lookup, form, creating: bool) -> None:
    lookup.app = (form.get("app") or lookup.app or "Configuration").strip()
    lookup.description = (form.get("description") or lookup.description or "").strip()
    lookup.notes = (form.get("notes") or "").strip() or None
    lookup.status = _status_field(form, lookup.status or "active")
    lookup.sort_no = parse_int(form.get("sort_no"), lookup.sort_no or 0)
    if creating or not lookup.is_system:
        code = (form.get("code") or lookup.code or "").strip().lower().replace(" ", "_")
        if code:
            lookup.code = code


@router.post("/lookups/new", include_in_schema=False)
async def lookup_create(request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("settings.update"))):
    form = await request.form()
    code = (form.get("code") or "").strip().lower().replace(" ", "_")
    description = (form.get("description") or "").strip()
    if not code or not description:
        return redirect(f"{BASE}/lookups", "A lookup needs a code and a description.", "error")
    if db.query(Lookup).filter(Lookup.code == code).first():
        return redirect(f"{BASE}/lookups", f"Lookup '{code}' already exists.", "error")
    lookup = Lookup(code=code, description=description, app="Configuration", status="active")
    _apply_lookup(lookup, form, creating=True)
    db.add(lookup)
    db.flush()
    log_action(db, user, "create", MODULE, entity=lookup, description=f"Lookup {lookup.code} created",
               after=snapshot(lookup), request=request)
    db.commit()
    lookup_service.invalidate(lookup.code)
    return redirect(f"{BASE}/lookups/{lookup.id}", f"Lookup '{lookup.description}' created.")


@router.post("/lookups/{lid}/edit", include_in_schema=False)
async def lookup_edit(lid: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("settings.update"))):
    lookup = _get(db, Lookup, lid, "Lookup")
    form = await request.form()
    before = snapshot(lookup)
    old_code = lookup.code
    submitted = (form.get("code") or "").strip().lower().replace(" ", "_")
    if lookup.is_system and submitted and submitted != lookup.code:
        return redirect(f"{BASE}/lookups/{lid}",
                        "This is a system lookup: the application reads it by code, so the code cannot change.", "error")
    _apply_lookup(lookup, form, creating=False)
    log_action(db, user, "update", MODULE, entity=lookup, description=f"Lookup {lookup.code} updated",
               before=before, after=snapshot(lookup), request=request)
    db.commit()
    lookup_service.invalidate(old_code)
    lookup_service.invalidate(lookup.code)
    return redirect(f"{BASE}/lookups/{lid}", "Lookup saved.")


@router.post("/lookups/{lid}/toggle", include_in_schema=False)
async def lookup_toggle(lid: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("settings.update"))):
    lookup = _get(db, Lookup, lid, "Lookup")
    resp = _toggle(db, user, request, lookup, f"Lookup '{lookup.description}'", f"{BASE}/lookups")
    lookup_service.invalidate(lookup.code)
    return resp


@router.post("/lookups/{lid}/delete", include_in_schema=False)
async def lookup_delete(lid: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("settings.configure"))):
    lookup = _get(db, Lookup, lid, "Lookup")
    if lookup.is_system:
        return redirect(f"{BASE}/lookups/{lid}",
                        "This is a system lookup: the application reads it by code, so it cannot be deleted. "
                        "Mark it inactive instead.", "error")
    code, description = lookup.code, lookup.description
    log_action(db, user, "delete", MODULE, entity=lookup, severity="warning", consequential=True,
               rationale=(await request.form()).get("rationale") or "Lookup removed",
               description=f"Lookup {code} deleted", before=snapshot(lookup), request=request)
    db.delete(lookup)
    db.commit()
    lookup_service.invalidate(code)
    return redirect(f"{BASE}/lookups", f"Lookup '{description}' deleted.")


@router.get("/lookups/{lid}", include_in_schema=False)
def lookup_detail(lid: int, request: Request, status: str = "", q: str = "",
                  db: Session = Depends(get_db), user: User = Depends(require("settings.view"))):
    lookup = _get(db, Lookup, lid, "Lookup")
    query = db.query(LookupValue).filter(LookupValue.lookup_id == lookup.id)
    if status:
        query = query.filter(LookupValue.status == status)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(func.lower(LookupValue.label).like(like) | func.lower(LookupValue.value).like(like))
    rows = query.order_by(LookupValue.sort_no, LookupValue.id).all()
    active = db.query(func.count(LookupValue.id)).filter(
        LookupValue.lookup_id == lookup.id, LookupValue.status == "active").scalar() or 0
    total = db.query(func.count(LookupValue.id)).filter(LookupValue.lookup_id == lookup.id).scalar() or 0
    return render(request, "company_config/lookup_detail.html", {
        "user": user, "lookup": lookup, "rows": rows, "status": status, "q": q, "statuses": STATUSES,
        "apps": LOOKUP_APPS, "stats": {"total": total, "active": active, "inactive": total - active},
        "next_sort": (db.query(func.max(LookupValue.sort_no)).filter(LookupValue.lookup_id == lookup.id).scalar() or 0) + 1,
        "preview": lookup_service.values(db, lookup.code, use_cache=False), **_perms(user)})


def _apply_value(lv: LookupValue, form) -> None:
    lv.value = (form.get("value") or lv.value or "").strip()
    lv.label = (form.get("label") or lv.label or lv.value).strip()
    lv.label_urdu = (form.get("label_urdu") or "").strip() or None
    amount = form.get("amount")
    lv.amount = parse_float(amount, 0.0) if amount not in (None, "") else None
    lv.status = _status_field(form, lv.status or "active")
    lv.sort_no = parse_int(form.get("sort_no"), lv.sort_no or 0)


@router.post("/lookups/{lid}/values/new", include_in_schema=False)
async def value_create(lid: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("settings.update"))):
    lookup = _get(db, Lookup, lid, "Lookup")
    form = await request.form()
    value = (form.get("value") or "").strip()
    if not value:
        return redirect(f"{BASE}/lookups/{lid}", "A value is required.", "error")
    if db.query(LookupValue).filter(LookupValue.lookup_id == lookup.id, LookupValue.value == value).first():
        return redirect(f"{BASE}/lookups/{lid}", f"'{value}' is already in this lookup.", "error")
    lv = LookupValue(lookup_id=lookup.id, value=value, label=value, status="active", sort_no=0)
    _apply_value(lv, form)
    db.add(lv)
    db.flush()
    log_action(db, user, "create", MODULE, entity=lv, description=f"Value '{lv.label}' added to {lookup.code}",
               after=snapshot(lv), request=request)
    db.commit()
    lookup_service.invalidate(lookup.code)
    return redirect(f"{BASE}/lookups/{lid}", f"Value '{lv.label}' added.")


@router.post("/lookups/{lid}/values/{vid}/edit", include_in_schema=False)
async def value_edit(lid: int, vid: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("settings.update"))):
    lookup = _get(db, Lookup, lid, "Lookup")
    lv = _get(db, LookupValue, vid, "Lookup value")
    form = await request.form()
    before = snapshot(lv)
    _apply_value(lv, form)
    log_action(db, user, "update", MODULE, entity=lv, description=f"Value '{lv.label}' of {lookup.code} updated",
               before=before, after=snapshot(lv), request=request)
    db.commit()
    lookup_service.invalidate(lookup.code)
    return redirect(f"{BASE}/lookups/{lid}", "Value saved.")


@router.post("/lookups/{lid}/values/{vid}/toggle", include_in_schema=False)
async def value_toggle(lid: int, vid: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("settings.update"))):
    lookup = _get(db, Lookup, lid, "Lookup")
    lv = _get(db, LookupValue, vid, "Lookup value")
    resp = _toggle(db, user, request, lv, f"Value '{lv.label}'", f"{BASE}/lookups/{lid}")
    lookup_service.invalidate(lookup.code)
    return resp


@router.post("/lookups/{lid}/values/{vid}/move", include_in_schema=False)
async def value_move(lid: int, vid: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("settings.update"))):
    """Reorder: swap this value's sort number with its neighbour in the given direction."""
    lookup = _get(db, Lookup, lid, "Lookup")
    lv = _get(db, LookupValue, vid, "Lookup value")
    form = await request.form()
    direction = (form.get("direction") or "up").strip().lower()
    rows = db.query(LookupValue).filter(LookupValue.lookup_id == lookup.id).order_by(
        LookupValue.sort_no, LookupValue.id).all()
    for i, row in enumerate(rows):  # renumber first so the swap is always well defined
        row.sort_no = i + 1
    idx = next((i for i, row in enumerate(rows) if row.id == lv.id), None)
    target = idx - 1 if direction == "up" else idx + 1
    if idx is None or target < 0 or target >= len(rows):
        db.commit()
        return redirect(f"{BASE}/lookups/{lid}", "Already at the end of the list.", "warning")
    rows[idx].sort_no, rows[target].sort_no = rows[target].sort_no, rows[idx].sort_no
    log_action(db, user, "update", MODULE, entity=lv, description=f"Value '{lv.label}' moved {direction} in {lookup.code}",
               after={"sort_no": lv.sort_no}, request=request)
    db.commit()
    lookup_service.invalidate(lookup.code)
    return redirect(f"{BASE}/lookups/{lid}", f"'{lv.label}' moved {direction}.")


# =============================================================================== 3. branch properties
def property_tabs(db: Session) -> list[tuple[str, str]]:
    """The ERP's tabs first, then every other group in use, so every setting sits under a tab."""
    known = {k for k, _ in PROPERTY_TABS}
    extra = sorted({g for (g,) in db.query(Setting.group).distinct().all() if g and g not in known})
    return PROPERTY_TABS + [(g, g.replace("_", " ").title()) for g in extra]


@router.get("/branch-properties", include_in_schema=False)
def branch_properties(request: Request, tab: str = "all", q: str = "", reveal: int = 0,
                      db: Session = Depends(get_db), user: User = Depends(require("settings.view"))):
    tabs = property_tabs(db)
    tab = tab if tab in [k for k, _ in tabs] else "all"
    rows = _property_rows(db, tab, q)
    by_group = dict(db.query(Setting.group, func.count(Setting.id)).group_by(Setting.group).all())
    items = []
    for s in rows:
        value = setting_value(s)
        vt = effective_type(s)   # older multi-key settings edit as JSON whatever they declare
        items.append({"s": s, "value": value, "vt": vt,
                      "text": json.dumps(value, indent=2, default=str) if vt == "json" else ("" if value is None else str(value)),
                      "revealed": bool(reveal and reveal == s.id)})
    return render(request, "company_config/branch_properties.html", {
        "user": user, "items": items, "tab": tab, "q": q, "tabs": tabs, "by_group": by_group,
        "reveal": reveal, "value_types": VALUE_TYPES,
        "stats": {"total": db.query(func.count(Setting.id)).scalar() or 0,
                  "editable": db.query(func.count(Setting.id)).filter(Setting.is_editable.is_(True)).scalar() or 0,
                  "secret": db.query(func.count(Setting.id)).filter(Setting.is_secret.is_(True)).scalar() or 0,
                  "shown": len(rows)},
        **_perms(user)})


@router.post("/branch-properties/{sid}/save", include_in_schema=False)
async def branch_property_save(sid: int, request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require("settings.configure"))):
    s = _get(db, Setting, sid, "Setting")
    form = await request.form()
    tab = (form.get("tab") or "all").strip()
    back = f"{BASE}/branch-properties?tab={tab}"
    if not s.is_editable:
        return redirect(back, f"'{s.label or s.key}' is not editable from this screen.", "error")
    before = snapshot(s)
    try:
        new_value = coerce_setting(effective_type(s), form.get("value"), setting_value(s))
    except ValueError as exc:
        return redirect(back, f"{s.label or s.key}: {exc}", "error")
    if new_value == setting_value(s):
        return redirect(back, "No change detected.", "warning")
    store_setting(s, new_value)
    log_action(db, user, "update", MODULE, entity=s, severity="warning", consequential=True,
               rationale=_rationale(form) or f"Branch property '{s.label or s.key}' changed",
               description=f"Branch property {s.key} set to {'********' if s.is_secret else new_value}",
               before=None if s.is_secret else before, after=None if s.is_secret else snapshot(s), request=request)
    db.commit()
    return redirect(back, f"'{s.label or s.key}' saved.")


@router.post("/branch-properties/{sid}/reveal", include_in_schema=False)
async def branch_property_reveal(sid: int, request: Request, db: Session = Depends(get_db),
                                 user: User = Depends(require("settings.view"))):
    """Show a secret setting in the clear. The reveal itself is written to the audit log."""
    s = _get(db, Setting, sid, "Setting")
    form = await request.form()
    tab = (form.get("tab") or "all").strip()
    if not s.is_secret:
        return redirect(f"{BASE}/branch-properties?tab={tab}", "That setting is not secret.", "warning")
    log_action(db, user, "view", MODULE, entity=s, severity="warning", consequential=True,
               rationale=_rationale(form) or "Secret branch property revealed on screen",
               description=f"Revealed the secret branch property {s.key}", request=request)
    db.commit()
    return redirect(f"{BASE}/branch-properties?tab={tab}&reveal={s.id}", f"'{s.label or s.key}' revealed — this is audited.")


# =============================================================================== 4. currency rates
def _rate_rows(db: Session) -> tuple[list[dict], Optional[Currency]]:
    base = db.query(Currency).filter(Currency.is_base.is_(True)).first()
    rows = []
    for c in db.query(Currency).order_by(Currency.is_base.desc(), Currency.code).all():
        last = (db.query(ExchangeRateHistory).filter(ExchangeRateHistory.currency_code == c.code)
                .order_by(ExchangeRateHistory.effective_at.desc(), ExchangeRateHistory.id.desc()).first())
        rows.append({"c": c, "last": last,
                     "history_count": db.query(func.count(ExchangeRateHistory.id)).filter(
                         ExchangeRateHistory.currency_code == c.code).scalar() or 0})
    return rows, base


@router.get("/currency-rates", include_in_schema=False)
def currency_rates(request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("currencies.view"))):
    rows, base = _rate_rows(db)
    history = (db.query(ExchangeRateHistory).order_by(ExchangeRateHistory.effective_at.desc(),
                                                      ExchangeRateHistory.id.desc()).limit(25).all())
    setters = {u.id: u.full_name for u in db.query(User).all()} if history else {}
    return render(request, "company_config/currency_rates.html", {
        "user": user, "rows": rows, "base": base, "history": history, "setters": setters,
        "today": date.today().isoformat(),
        "can_edit": rbac.has_permission(user, "currencies.update"),
        "stats": {"total": len(rows), "active": sum(1 for r in rows if r["c"].is_active),
                  "manual": sum(1 for r in rows if r["c"].manual_override),
                  "history": db.query(func.count(ExchangeRateHistory.id)).scalar() or 0}})


@router.post("/currency-rates/manual", include_in_schema=False)
async def currency_rate_manual(request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require("currencies.update"))):
    """Add Manual Currency Rate — writes a history row and moves the currency onto the new rate."""
    form = await request.form()
    code = (form.get("code") or "").strip().upper()[:3]
    rate = parse_float(form.get("rate_to_base"), 0.0)
    effective = parse_date(form.get("effective_at"), date.today())
    note = (form.get("note") or "").strip()
    currency = db.query(Currency).filter(Currency.code == code).first()
    if not currency:
        return redirect(f"{BASE}/currency-rates", f"Unknown currency '{code}'.", "error")
    if rate <= 0:
        return redirect(f"{BASE}/currency-rates", "The rate must be greater than zero.", "error")
    if currency.is_base and abs(rate - 1) > 1e-9:
        return redirect(f"{BASE}/currency-rates", f"{code} is the base currency; its rate is always 1.", "error")
    before = snapshot(currency)
    currency.rate_to_base = rate
    if parse_bool(form.get("manual_override")):
        currency.manual_override = True
    db.add(ExchangeRateHistory(currency_code=code, rate_to_base=rate, source="manual", set_by_id=user.id,
                               effective_at=datetime.combine(effective, datetime.min.time())))
    log_action(db, user, "update", MODULE, entity=currency, severity="warning", consequential=True,
               rationale=note or _rationale(form) or "Manual currency rate added",
               description=f"Manual rate for {code} set to {rate} effective {effective.isoformat()}"
                           + (f" — {note}" if note else ""),
               before=before, after=snapshot(currency), request=request)
    db.commit()
    return redirect(f"{BASE}/currency-rates", f"Manual rate for {code} saved ({rate} to base, effective {effective.isoformat()}).")


@router.post("/currency-rates/refresh", include_in_schema=False)
async def currency_rates_refresh(request: Request, db: Session = Depends(get_db),
                                 user: User = Depends(require("currencies.update"))):
    """Refresh Rates — a SIMULATED feed. No external provider is called anywhere in this build."""
    base = db.query(Currency).filter(Currency.is_base.is_(True)).first()
    now = datetime.utcnow()
    updated, skipped = [], []
    for c in db.query(Currency).filter(Currency.is_base.is_(False)).order_by(Currency.code).all():
        if c.manual_override:
            skipped.append(c.code)
            continue
        # Deterministic drift so the simulation is reproducible: +/- up to 1.2% keyed off the code and the day.
        seed = sum(ord(ch) for ch in c.code) + now.timetuple().tm_yday
        drift = ((seed % 25) - 12) / 1000.0
        new_rate = round(max(0.000001, float(c.rate_to_base or 1) * (1 + drift)), 6)
        c.rate_to_base = new_rate
        db.add(ExchangeRateHistory(currency_code=c.code, rate_to_base=new_rate, source="simulated_feed",
                                   set_by_id=user.id, effective_at=now))
        updated.append(c.code)
    log_action(db, user, "execute", MODULE, entity_type="Currency", severity="warning", consequential=True,
               rationale="Simulated ERP Currency Rates feed run from the Configuration screen",
               description=f"Simulated feed updated {len(updated)} currencies ({', '.join(updated) or 'none'})"
                           + (f"; left alone (manual override): {', '.join(skipped)}" if skipped else ""),
               after={"base": base.code if base else None, "updated": updated, "skipped": skipped}, request=request)
    db.commit()
    msg = (f"Simulated ERP feed: {len(updated)} currency rate(s) updated and recorded in history. "
           "No external rate provider was contacted — these figures are generated locally.")
    if skipped:
        msg += f" Left alone because they are set manually: {', '.join(skipped)}."
    return redirect(f"{BASE}/currency-rates", msg, "info")


@router.get("/currency-rates/{code}", include_in_schema=False)
def currency_rate_history(code: str, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("currencies.view"))):
    currency = db.query(Currency).filter(Currency.code == code.upper()).first()
    if not currency:
        raise HTTPException(404, "Currency not found")
    history = (db.query(ExchangeRateHistory).filter(ExchangeRateHistory.currency_code == currency.code)
               .order_by(ExchangeRateHistory.effective_at.desc(), ExchangeRateHistory.id.desc()).limit(200).all())
    setters = {u.id: u.full_name for u in db.query(User).all()}
    base = db.query(Currency).filter(Currency.is_base.is_(True)).first()
    return render(request, "company_config/currency_history.html", {
        "user": user, "currency": currency, "history": history, "setters": setters, "base": base,
        "can_edit": rbac.has_permission(user, "currencies.update"), "today": date.today().isoformat()})


# =============================================================================== 5. payment gateways
@router.get("/payment-gateways", include_in_schema=False)
def payment_gateways(request: Request, status: str = "", company: str = "", q: str = "", sample: float = 100.0,
                     db: Session = Depends(get_db), user: User = Depends(require("settings.view"))):
    query = db.query(PaymentGateway)
    if status:
        query = query.filter(PaymentGateway.status == status)
    if company:
        query = query.filter(PaymentGateway.gateway_company == company)
    if q:
        query = query.filter(func.lower(PaymentGateway.name).like(f"%{q.lower()}%"))
    rows = query.order_by(PaymentGateway.id).all()
    accounts = db.query(BeneficiaryAccount).filter(BeneficiaryAccount.status == "active").order_by(
        BeneficiaryAccount.account_name).all()
    currencies = [c.code for c in db.query(Currency).order_by(Currency.code).all()]
    return render(request, "company_config/payment_gateways.html", {
        "user": user, "rows": rows, "status": status, "company": company, "q": q, "sample": sample,
        "statuses": STATUSES, "companies": GATEWAY_COMPANIES, "currencies": currencies,
        "accounts": [(a.id, f"{a.account_name} ({a.category})") for a in accounts],
        "stats": {"total": db.query(func.count(PaymentGateway.id)).scalar() or 0,
                  "active": db.query(func.count(PaymentGateway.id)).filter(PaymentGateway.status == "active").scalar() or 0,
                  "live": db.query(func.count(PaymentGateway.id)).filter(PaymentGateway.live_mode.is_(True)).scalar() or 0,
                  "test": db.query(func.count(PaymentGateway.id)).filter(PaymentGateway.live_mode.is_(False)).scalar() or 0},
        **_perms(user)})


def _apply_gateway(g: PaymentGateway, form) -> None:
    g.name = (form.get("name") or g.name or "").strip()
    g.gateway_company = (form.get("gateway_company") or g.gateway_company or "Other").strip()
    g.default_transaction_fee_pct = parse_float(form.get("default_transaction_fee_pct"), g.default_transaction_fee_pct or 0)
    g.fixed_fee = parse_float(form.get("fixed_fee"), float(g.fixed_fee or 0))
    g.currency = (form.get("currency") or "").strip().upper()[:3] or None
    g.beneficiary_account_id = parse_int(form.get("beneficiary_account_id"))
    g.live_mode = parse_bool(form.get("live_mode"))
    g.status = _status_field(form, g.status or "active")
    g.notes = (form.get("notes") or "").strip() or None


@router.post("/payment-gateways/new", include_in_schema=False)
async def gateway_create(request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("settings.update"))):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return redirect(f"{BASE}/payment-gateways", "A gateway name is required.", "error")
    g = PaymentGateway(name=name, gateway_company="Other", status="active")
    _apply_gateway(g, form)
    db.add(g)
    db.flush()
    log_action(db, user, "create", MODULE, entity=g, description=f"Payment gateway {g.name} created",
               after=snapshot(g), request=request)
    db.commit()
    return redirect(f"{BASE}/payment-gateways", f"Payment gateway '{g.name}' created.")


@router.post("/payment-gateways/{gid}/edit", include_in_schema=False)
async def gateway_edit(gid: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("settings.update"))):
    g = _get(db, PaymentGateway, gid, "Payment gateway")
    form = await request.form()
    before = snapshot(g)
    _apply_gateway(g, form)
    log_action(db, user, "update", MODULE, entity=g, description=f"Payment gateway {g.name} updated",
               before=before, after=snapshot(g), request=request)
    db.commit()
    return redirect(f"{BASE}/payment-gateways", f"Payment gateway '{g.name}' saved.")


@router.post("/payment-gateways/{gid}/toggle", include_in_schema=False)
async def gateway_toggle(gid: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("settings.update"))):
    g = _get(db, PaymentGateway, gid, "Payment gateway")
    return _toggle(db, user, request, g, f"Gateway '{g.name}'", f"{BASE}/payment-gateways")


# =============================================================================== 6. WhatsApp senders
@router.get("/whatsapp-senders", include_in_schema=False)
def whatsapp_senders(request: Request, status: str = "", api_status: str = "", purpose: str = "",
                     db: Session = Depends(get_db), user: User = Depends(require("settings.view"))):
    query = db.query(WhatsAppSender)
    if status:
        query = query.filter(WhatsAppSender.status == status)
    if api_status:
        query = query.filter(WhatsAppSender.api_status == api_status)
    if purpose:
        query = query.filter(WhatsAppSender.purpose == purpose)
    rows = query.order_by(WhatsAppSender.id).all()
    now = datetime.utcnow()
    items = [{"s": s, "next_at": next_send_at(s), "ready": sender_is_ready(s, now)} for s in rows]
    return render(request, "company_config/whatsapp_senders.html", {
        "user": user, "items": items, "status": status, "api_status": api_status, "purpose": purpose,
        "statuses": STATUSES, "purposes": SENDER_PURPOSES,
        "api_statuses": [("connected", "Connected"), ("disconnected", "Disconnected")],
        "picked": pick_sender(db, purpose or "general", now), "now": now,
        "stats": {"total": db.query(func.count(WhatsAppSender.id)).scalar() or 0,
                  "connected": db.query(func.count(WhatsAppSender.id)).filter(WhatsAppSender.api_status == "connected").scalar() or 0,
                  "disconnected": db.query(func.count(WhatsAppSender.id)).filter(WhatsAppSender.api_status == "disconnected").scalar() or 0,
                  "live": db.query(func.count(WhatsAppSender.id)).filter(WhatsAppSender.live_mode.is_(True)).scalar() or 0},
        **_perms(user)})


def _apply_sender(s: WhatsAppSender, form) -> None:
    s.description = (form.get("description") or s.description or "").strip()
    s.number = (form.get("number") or s.number or "").strip()
    s.purpose = (form.get("purpose") or s.purpose or "general").strip()
    s.live_mode = parse_bool(form.get("live_mode"))
    s.interval_seconds = max(0, parse_int(form.get("interval_seconds"), s.interval_seconds or 10))
    s.messages_per_cycle = max(1, parse_int(form.get("messages_per_cycle"), s.messages_per_cycle or 1))
    s.status = _status_field(form, s.status or "active")


@router.post("/whatsapp-senders/new", include_in_schema=False)
async def sender_create(request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("settings.update"))):
    form = await request.form()
    description = (form.get("description") or "").strip()
    number = (form.get("number") or "").strip()
    if not description or not number:
        return redirect(f"{BASE}/whatsapp-senders", "A description and a WhatsApp number are required.", "error")
    if db.query(WhatsAppSender).filter(WhatsAppSender.number == number).first():
        return redirect(f"{BASE}/whatsapp-senders", f"{number} is already configured.", "error")
    s = WhatsAppSender(description=description, number=number, api_status="disconnected", status="active")
    _apply_sender(s, form)
    db.add(s)
    db.flush()
    log_action(db, user, "create", MODULE, entity=s, description=f"WhatsApp sender {s.number} created",
               after=snapshot(s), request=request)
    db.commit()
    return redirect(f"{BASE}/whatsapp-senders", f"WhatsApp sender '{s.description}' created — connect it to start sending.")


@router.post("/whatsapp-senders/{sid}/edit", include_in_schema=False)
async def sender_edit(sid: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("settings.update"))):
    s = _get(db, WhatsAppSender, sid, "WhatsApp sender")
    form = await request.form()
    before = snapshot(s)
    _apply_sender(s, form)
    log_action(db, user, "update", MODULE, entity=s, description=f"WhatsApp sender {s.number} updated",
               before=before, after=snapshot(s), request=request)
    db.commit()
    return redirect(f"{BASE}/whatsapp-senders", f"Sender '{s.description}' saved.")


@router.post("/whatsapp-senders/{sid}/toggle", include_in_schema=False)
async def sender_toggle(sid: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("settings.update"))):
    s = _get(db, WhatsAppSender, sid, "WhatsApp sender")
    return _toggle(db, user, request, s, f"Sender '{s.description}'", f"{BASE}/whatsapp-senders")


@router.post("/whatsapp-senders/{sid}/connect", include_in_schema=False)
async def sender_connect(sid: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("settings.configure"))):
    """Generate a QR token and mark the sender connected. SIMULATED — no WhatsApp session is opened."""
    s = _get(db, WhatsAppSender, sid, "WhatsApp sender")
    before = snapshot(s)
    s.qr_token = secrets.token_urlsafe(18)
    s.qr_refreshed_at = datetime.utcnow()
    s.api_status = "connected"
    log_action(db, user, "execute", MODULE, entity=s, severity="warning", consequential=True,
               rationale="WhatsApp sender connected from the Configuration screen (simulated)",
               description=f"Simulated connect for WhatsApp sender {s.number}; QR token generated",
               before=before, after=snapshot(s), request=request)
    db.commit()
    return redirect(f"{BASE}/whatsapp-senders",
                    f"'{s.description}' is connected and a QR token was generated. This is simulated — "
                    "no WhatsApp session was actually opened.", "info")


@router.post("/whatsapp-senders/{sid}/disconnect", include_in_schema=False)
async def sender_disconnect(sid: int, request: Request, db: Session = Depends(get_db),
                            user: User = Depends(require("settings.configure"))):
    s = _get(db, WhatsAppSender, sid, "WhatsApp sender")
    before = snapshot(s)
    s.qr_token = None
    s.qr_refreshed_at = None
    s.api_status = "disconnected"
    log_action(db, user, "execute", MODULE, entity=s, severity="warning", consequential=True,
               rationale="WhatsApp sender disconnected from the Configuration screen",
               description=f"WhatsApp sender {s.number} disconnected; QR token cleared",
               before=before, after=snapshot(s), request=request)
    db.commit()
    return redirect(f"{BASE}/whatsapp-senders", f"'{s.description}' disconnected and its QR token cleared.")


# =============================================================================== 7. support tickets
@router.get("/support-tickets", include_in_schema=False)
def support_tickets(request: Request, status: str = "", priority: str = "", module: str = "",
                    ticket_type: str = "", q: str = "", page: int = 1,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(SupportTicket)
    if status:
        query = query.filter(SupportTicket.status == status)
    if priority:
        query = query.filter(SupportTicket.priority == priority)
    if module:
        query = query.filter(SupportTicket.module == module)
    if ticket_type:
        query = query.filter(SupportTicket.ticket_type == ticket_type)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(func.lower(SupportTicket.subject).like(like)
                             | func.lower(SupportTicket.ticket_number).like(like))
    pg = paginate(query.order_by(SupportTicket.id.desc()), page, 25)
    counts = dict(db.query(SupportTicket.status, func.count(SupportTicket.id)).group_by(SupportTicket.status).all())
    return render(request, "company_config/support_tickets.html", {
        "user": user, "page": pg, "status": status, "priority": priority, "module": module,
        "ticket_type": ticket_type, "q": q, "counts": counts,
        "ticket_statuses": [(s, TICKET_STATUS_LABELS.get(s, s)) for s in TICKET_STATUSES],
        "priorities": [(p, PRIORITY_LABELS.get(p, p)) for p in TICKET_PRIORITIES],
        "ticket_types": TICKET_TYPES, "modules": TICKET_MODULES,
        "departments": [(d.id, d.name) for d in db.query(Department).order_by(Department.name).all()],
        "total": db.query(func.count(SupportTicket.id)).scalar() or 0,
        "can_manage": is_system_administrator(user)})


@router.post("/support-tickets/new", include_in_schema=False)
async def ticket_create(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Any member of staff may raise a ticket."""
    form = await request.form()
    subject = (form.get("subject") or "").strip()
    message = (form.get("message") or "").strip()
    if not subject or not message:
        return redirect(f"{BASE}/support-tickets", "A subject and a message are required.", "error")
    priority = (form.get("priority") or "normal").strip()
    ticket = SupportTicket(
        ticket_number=next_code(db, SupportTicket, "ticket_number", "TKT-"),
        subject=subject, message=message,
        department_id=parse_int(form.get("department_id")) or user.department_id,
        whatsapp_no=(form.get("whatsapp_no") or user.phone or "").strip() or None,
        ticket_type=(form.get("ticket_type") or "Bug").strip(),
        module=(form.get("module") or "Online Academics").strip(),
        priority=priority if priority in TICKET_PRIORITIES else "normal",
        status="pending", raised_by_id=user.id)
    db.add(ticket)
    db.flush()
    log_action(db, user, "create", MODULE, entity=ticket,
               description=f"Support ticket {ticket.ticket_number} raised: {ticket.subject}",
               after=snapshot(ticket), request=request)
    db.commit()
    return redirect(f"{BASE}/support-tickets/{ticket.id}", f"Ticket {ticket.ticket_number} raised.")


@router.post("/support-tickets/{tid}/status", include_in_schema=False)
async def ticket_status(tid: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Change a ticket's status and write the developer remarks. System administrators only."""
    if not is_system_administrator(user):
        raise PermissionDenied("settings.configure")
    ticket = _get(db, SupportTicket, tid, "Support ticket")
    form = await request.form()
    status = (form.get("status") or ticket.status).strip()
    if status not in TICKET_STATUSES:
        return redirect(f"{BASE}/support-tickets/{tid}", f"'{status}' is not a ticket status.", "error")
    before = snapshot(ticket)
    ticket.status = status
    remarks = (form.get("developer_remarks") or "").strip()
    if remarks:
        ticket.developer_remarks = remarks
    ticket.resolved_at = datetime.utcnow() if status in ("resolved", "closed") else None
    log_action(db, user, "status_change", MODULE, entity=ticket, severity="warning",
               rationale=remarks or _rationale(form) or f"Ticket moved to {status}",
               description=f"Ticket {ticket.ticket_number} marked {TICKET_STATUS_LABELS.get(status, status)}",
               before=before, after=snapshot(ticket), request=request)
    db.commit()
    return redirect(f"{BASE}/support-tickets/{tid}",
                    f"Ticket {ticket.ticket_number} marked {TICKET_STATUS_LABELS.get(status, status)}.")


@router.get("/support-tickets/{tid}", include_in_schema=False)
def ticket_detail(tid: int, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    ticket = _get(db, SupportTicket, tid, "Support ticket")
    return render(request, "company_config/ticket_detail.html", {
        "user": user, "t": ticket,
        "ticket_statuses": [(s, TICKET_STATUS_LABELS.get(s, s)) for s in TICKET_STATUSES],
        "priority_label": PRIORITY_LABELS.get(ticket.priority, ticket.priority),
        "status_label": TICKET_STATUS_LABELS.get(ticket.status, ticket.status),
        "can_manage": is_system_administrator(user)})


# =============================================================================== 8. OTP
def otp_config(db: Session) -> OtpConfiguration:
    cfg = db.query(OtpConfiguration).order_by(OtpConfiguration.id).first()
    if not cfg:
        cfg = OtpConfiguration(is_enabled=False, channel="email", required_for_roles=[])
        db.add(cfg)
        db.flush()
    return cfg


@router.get("/otp", include_in_schema=False)
def otp_page(request: Request, tab: str = "setup", q: str = "", token: str = "", page: int = 1,
             db: Session = Depends(get_db), user: User = Depends(require("settings.view"))):
    tab = tab if tab in ("setup", "users") else "setup"
    cfg = otp_config(db)
    db.commit()
    pg = None
    if tab == "users":
        query = (db.query(User).join(Role, User.role_id == Role.id)
                 .filter(Role.portal.in_(("admin", "teacher")), User.is_active.is_(True)))
        if q:
            like = f"%{q.lower()}%"
            query = query.filter(func.lower(User.full_name).like(like) | func.lower(User.email).like(like)
                                 | func.lower(User.username).like(like))
        if token == "enabled":
            query = query.filter(User.two_factor_enabled.is_(True))
        elif token == "disabled":
            query = query.filter(User.two_factor_enabled.is_(False))
        pg = paginate(query.order_by(User.full_name), page, 25)
    staff_total = (db.query(func.count(User.id)).join(Role, User.role_id == Role.id)
                   .filter(Role.portal.in_(("admin", "teacher")), User.is_active.is_(True)).scalar() or 0)
    enabled_total = (db.query(func.count(User.id)).join(Role, User.role_id == Role.id)
                     .filter(Role.portal.in_(("admin", "teacher")), User.is_active.is_(True),
                             User.two_factor_enabled.is_(True)).scalar() or 0)
    return render(request, "company_config/otp.html", {
        "user": user, "tab": tab, "cfg": cfg, "page": pg, "q": q, "token": token, "channels": OTP_CHANNELS,
        "roles": [(r.slug, r.name) for r in db.query(Role).filter(Role.portal == "admin").order_by(Role.name).all()],
        "senders": [(s.id, f"{s.description} ({s.number})") for s in
                    db.query(WhatsAppSender).filter(WhatsAppSender.status == "active").order_by(WhatsAppSender.id).all()],
        "stats": {"staff": staff_total, "enabled": enabled_total, "disabled": staff_total - enabled_total,
                  "required_roles": len(cfg.required_for_roles or [])},
        **_perms(user)})


@router.post("/otp/setup", include_in_schema=False)
async def otp_setup(request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("settings.configure"))):
    cfg = otp_config(db)
    form = await request.form()
    before = snapshot(cfg)
    cfg.is_enabled = parse_bool(form.get("is_enabled"))
    channel = (form.get("channel") or cfg.channel or "email").strip()
    cfg.channel = channel if channel in [c for c, _ in OTP_CHANNELS] else "email"
    cfg.code_length = min(10, max(4, parse_int(form.get("code_length"), cfg.code_length or 6)))
    cfg.validity_minutes = min(120, max(1, parse_int(form.get("validity_minutes"), cfg.validity_minutes or 10)))
    cfg.max_attempts = min(20, max(1, parse_int(form.get("max_attempts"), cfg.max_attempts or 5)))
    cfg.resend_after_seconds = min(900, max(10, parse_int(form.get("resend_after_seconds"), cfg.resend_after_seconds or 60)))
    cfg.required_for_roles = [r for r in form.getlist("required_for_roles") if r]
    cfg.whatsapp_sender_id = parse_int(form.get("whatsapp_sender_id"))
    log_action(db, user, "update", MODULE, entity=cfg, severity="warning", consequential=True,
               rationale=_rationale(form) or "One-time password configuration changed",
               description=f"OTP {'enabled' if cfg.is_enabled else 'disabled'} on the {cfg.channel} channel",
               before=before, after=snapshot(cfg), request=request)
    db.commit()
    return redirect(f"{BASE}/otp?tab=setup", "One-time password setup saved.")


@router.post("/otp/users/{uid}/toggle", include_in_schema=False)
async def otp_user_toggle(uid: int, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("settings.configure"))):
    target = _get(db, User, uid, "User")
    form = await request.form()
    before = snapshot(target)
    target.two_factor_enabled = not target.two_factor_enabled
    state = "enabled" if target.two_factor_enabled else "disabled"
    log_action(db, user, "update", MODULE, entity=target, severity="warning", consequential=True,
               rationale=_rationale(form) or f"Two-factor {state} from the OTP configuration screen",
               description=f"Two-factor authentication {state} for {target.full_name}",
               before=before, after=snapshot(target), request=request)
    db.commit()
    back = f"{BASE}/otp?tab=users"
    if form.get("q"):
        back += f"&q={form.get('q')}"
    return redirect(back, f"Two-factor {state} for {target.full_name}.")


# =============================================================================== 9. roles (read only)
@router.get("/roles", include_in_schema=False)
def roles_page(request: Request, app: str = "", q: str = "", db: Session = Depends(get_db),
               user: User = Depends(require("roles.view"))):
    counts = dict(db.query(User.role_id, func.count(User.id)).filter(User.is_active.is_(True))
                  .group_by(User.role_id).all())
    rows = []
    for r in db.query(Role).order_by(Role.name).all():
        application = ROLE_APPS.get(r.slug, "Configuration")
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
    for r in db.query(Role).all():
        key = ROLE_APPS.get(r.slug, "Configuration")
        by_app[key] = by_app.get(key, 0) + 1
    return render(request, "company_config/roles.html", {
        "user": user, "groups": groups, "apps": LOOKUP_APPS, "app": app, "q": q, "by_app": by_app,
        "stats": {"total": db.query(func.count(Role.id)).scalar() or 0,
                  "assigned": sum(counts.values()),
                  "system": db.query(func.count(Role.id)).filter(Role.is_system.is_(True)).scalar() or 0,
                  "apps": len(by_app)},
        "can_edit": rbac.has_permission(user, "roles.update")})
