"""Clients / parents (Module 5 + ERP "Client Management"): Client List, Trial Client List, Clients Users List,
Client Form (Basic Detail / Contacts / Students / Credentials grids), portal login, deactivate/reactivate."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import require, csrf_protect
from app.core.templating import render, label as status_label
from app.core.utils import redirect, paginate, parse_bool, parse_date, parse_int, parse_float
from app.database import get_db
from app.models.core import User, AuditEvent, Notification, CommunicationPreference, Role
from app.models.crm import Case, Feedback, Conversation, Referral, Message, Lead
from app.models.erp import ClientContact, ClientCredential, ClientAcademicGroup
from app.models.finance import Subscription, Invoice, LedgerEntry, Payment
from app.models.people import Client, Student, Household, Leave
from app.models.scheduling import Trial
from app.services import people as svc

router = APIRouter(prefix="/clients", dependencies=[Depends(csrf_protect)])

# Tab order mirrors the ERP Client Form (Basic Detail / Contacts / Students / Credentials) followed by the
# platform's own tabs. "basic" links straight to the edit form.
TABS = [("overview", "Client Form"), ("basic", "Basic Detail"), ("contacts", "Contacts"), ("students", "Students"),
        ("credentials", "Credentials"), ("billing", "Billing & Ledger"), ("cases", "Cases"), ("feedback", "Feedback"),
        ("conversations", "Conversations"), ("referrals", "Referrals"), ("comms", "Communication log"), ("audit", "Audit trail")]
STATUSES = ["trial", "active", "on_leave", "pass_out", "inactive", "churned"]
# ERP tile vocabulary -> internal status. The list filter accepts either.
STATUS_ALIASES = {"trail": "trial", "regular": "active", "drop_out": "churned", "dropout": "churned", "black_list": "inactive",
                  "blacklist": "inactive", "frozen": "on_leave", "passed_out": "pass_out", "graduated": "pass_out"}
TILES = [("Trial", "trial", "flask-conical"), ("Regular", "active", "user-check"), ("Drop Out", "churned", "user-x"),
         ("Black List", "inactive", "ban"), ("On Leave", "on_leave", "plane")]
FEE_RECURRENCE = [("monthly", "Monthly"), ("quarterly", "Quarterly"), ("half_yearly", "Half Yearly"), ("yearly", "Yearly"), ("per_class", "Per Class")]
SHIFTS = [("morning", "Morning"), ("night", "Night")]
CONTACT_TYPES = ["phone", "whatsapp", "email", "skype", "other"]
CREDENTIAL_TYPES = ["zoom", "teams", "skype", "portal", "other"]


def can_see_contact(user: User) -> bool:
    return rbac.has_permission(user, "clients.update") or rbac.is_management(user)


def norm_status(value: str) -> str:
    v = (value or "").strip().lower().replace(" ", "_")
    return STATUS_ALIASES.get(v, v)


def _get(db: Session, id: int) -> Client:
    c = db.query(Client).get(id)
    if not c:
        raise HTTPException(404, "Client not found")
    return c


def _users_with_roles(db: Session, slugs: list[str]) -> list[User]:
    return (db.query(User).join(Role, User.role_id == Role.id).filter(Role.slug.in_(slugs), User.is_active.is_(True))
            .order_by(User.full_name).all())


def _managers(db: Session) -> list[User]:
    rows = _users_with_roles(db, ["hod_academics", "academic_manager", "manager", "supervisor", "super_admin", "ceo"])
    if not rows:
        rows = db.query(User).filter(User.is_active.is_(True), User.email.in_(["academics@oqc.local", "manager@oqc.local"])).all()
    return rows


def _form_context(db: Session, c: Client | None = None) -> dict:
    households = db.query(Household).order_by(Household.name).all()
    reps = _users_with_roles(db, ["billing_rep", "hod_finance", "accountant"])
    managers = _managers(db)
    groups = db.query(ClientAcademicGroup).filter(ClientAcademicGroup.status == "active").order_by(ClientAcademicGroup.name).all()
    others = db.query(Client).filter(Client.id != (c.id if c else -1)).order_by(Client.full_name).all()
    return {"countries": svc.COUNTRY_NAMES, "timezones": svc.TIMEZONES, "currencies": svc.CURRENCIES, "relationships": svc.RELATIONSHIPS,
            "statuses": [(s, status_label(s, "client")) for s in STATUSES], "households": households,
            "household_options": [(h.id, f"{h.name} - {h.country or ''}") for h in households],
            "billing_reps": reps, "billing_rep_options": [(u.id, u.full_name) for u in reps],
            "manager_options": [(u.id, u.full_name) for u in managers],
            "group_options": [(g.id, f"{g.name} ({g.shift_group})") for g in groups],
            "referrer_options": [(o.id, f"{o.client_code} - {o.full_name}") for o in others],
            "fee_recurrence": FEE_RECURRENCE, "shifts": SHIFTS,
            "sources": ["Website", "WhatsApp", "Meta Ads", "Google Ads", "Referral", "Manual", "GHL"], "client": c,
            "country_map": {k: {"timezone": v[1], "currency": v[2]} for k, v in svc.COUNTRY_MAP.items()}}


def _tile_counts(db: Session) -> dict:
    """Status tiles, counted exactly as the ERP does: a plain count of Client.status.

    Families whose children are away but whose own status is unchanged are not counted here — the
    ERP tile is a status filter (client-list?p41_status_id=...). Use /students/on-leave for the
    students who are actually on leave today.
    """
    counts = dict(db.query(Client.status, func.count(Client.id)).group_by(Client.status).all())
    return {"trial": counts.get("trial", 0), "active": counts.get("active", 0), "churned": counts.get("churned", 0),
            "inactive": counts.get("inactive", 0), "on_leave": counts.get("on_leave", 0) + counts.get("frozen", 0),
            "pass_out": counts.get("pass_out", 0) + counts.get("graduated", 0), "total": sum(counts.values())}


def _apply_filters(db: Session, query, q: str, status: str, shift: str, country: str, billing_rep: str, academic_manager: str,
                   date_from: str, date_to: str):
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Client.full_name.ilike(like), Client.client_code.ilike(like), Client.email.ilike(like),
                                 Client.phone.ilike(like), Client.city.ilike(like), Client.legacy_code.ilike(like), Client.state.ilike(like)))
    st = norm_status(status)
    if st == "on_leave":
        query = query.filter(Client.status.in_(["on_leave", "frozen"]))
    elif st == "pass_out":
        query = query.filter(Client.status.in_(["pass_out", "graduated"]))
    elif st:
        query = query.filter(Client.status == st)
    if shift:
        query = query.filter(Client.shift == shift)
    if country:
        query = query.filter(Client.country == country)
    if parse_int(billing_rep):
        query = query.filter(Client.billing_rep_id == int(billing_rep))
    if parse_int(academic_manager):
        query = query.filter(Client.academic_manager_id == int(academic_manager))
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(Client.joined_at >= df)
    if dt:
        query = query.filter(Client.joined_at <= dt)
    return query


def _row_extras(db: Session, clients: list[Client]) -> dict:
    ids = [c.id for c in clients] or [-1]
    student_counts = dict(db.query(Student.client_id, func.count(Student.id)).filter(Student.client_id.in_(ids)).group_by(Student.client_id).all())
    balances: dict[int, float] = {}
    for e in db.query(LedgerEntry).filter(LedgerEntry.client_id.in_(ids)).order_by(LedgerEntry.entry_date, LedgerEntry.id):
        balances[e.client_id] = float(e.balance_after or 0)
    return {"student_counts": student_counts, "balances": balances}


def _filter_options(db: Session) -> dict:
    countries = [r[0] for r in db.query(Client.country).distinct().order_by(Client.country) if r[0]]
    reps = _users_with_roles(db, ["billing_rep", "hod_finance", "accountant"])
    return {"countries": countries, "billing_rep_options": [(u.id, u.full_name) for u in reps],
            "manager_options": [(u.id, u.full_name) for u in _managers(db)], "shifts": SHIFTS,
            "status_options": [(k, l) for l, k, _ in TILES]}


# --------------------------------------------------------------------------- Client List
@router.get("", include_in_schema=False)
def list_clients(request: Request, page: int = 1, q: str = "", status: str = "", shift: str = "", country: str = "", billing_rep: str = "",
                 academic_manager: str = "", date_from: str = "", date_to: str = "", db: Session = Depends(get_db),
                 user: User = Depends(require("clients.view"))):
    query = _apply_filters(db, db.query(Client), q, status, shift, country, billing_rep, academic_manager, date_from, date_to)
    pg = paginate(query.order_by(Client.created_at.desc()), page, 25)
    base = (f"/clients?q={q}&status={status}&shift={shift}&country={country}&billing_rep={billing_rep}"
            f"&academic_manager={academic_manager}&date_from={date_from}&date_to={date_to}")
    return render(request, "clients/list.html", {"user": user, "page": pg, "q": q, "status": status, "shift": shift, "country": country,
                                                 "billing_rep": billing_rep, "academic_manager": academic_manager, "date_from": date_from,
                                                 "date_to": date_to, "stats": _tile_counts(db), "tiles": TILES, **_filter_options(db),
                                                 **_row_extras(db, pg.items), "show_contact": can_see_contact(user), "base_url": base})


# --------------------------------------------------------------------------- Trial Client List (static path before /{id})
@router.get("/trial", include_in_schema=False)
def trial_clients(request: Request, page: int = 1, q: str = "", shift: str = "", country: str = "", billing_rep: str = "",
                  academic_manager: str = "", date_from: str = "", date_to: str = "", db: Session = Depends(get_db),
                  user: User = Depends(require("clients.view"))):
    query = _apply_filters(db, db.query(Client), q, "trial", shift, country, billing_rep, academic_manager, date_from, date_to)
    pg = paginate(query.order_by(Client.joined_at.desc(), Client.id.desc()), page, 25)
    lead_ids = [c.lead_id for c in pg.items if c.lead_id] or [-1]
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_(lead_ids))}
    closers: dict[int, User] = {}
    for tr in db.query(Trial).filter(Trial.lead_id.in_(lead_ids), Trial.closer_id.isnot(None)):
        closers.setdefault(tr.lead_id, tr.closer_id)
    user_names = {u.id: u.full_name for u in db.query(User)}
    stats = _tile_counts(db)
    stats["morning"] = query.filter(Client.shift == "morning").count()
    stats["night"] = query.filter(Client.shift == "night").count()
    stats["converted"] = query.filter(Client.converted_at.isnot(None)).count()
    base = (f"/clients/trial?q={q}&shift={shift}&country={country}&billing_rep={billing_rep}&academic_manager={academic_manager}"
            f"&date_from={date_from}&date_to={date_to}")
    return render(request, "clients/trial.html", {"user": user, "page": pg, "q": q, "shift": shift, "country": country, "billing_rep": billing_rep,
                                                  "academic_manager": academic_manager, "date_from": date_from, "date_to": date_to, "stats": stats,
                                                  **_filter_options(db), **_row_extras(db, pg.items), "leads": leads, "closers": closers,
                                                  "user_names": user_names, "base_url": base})


# --------------------------------------------------------------------------- Clients Users List
@router.get("/users", include_in_schema=False)
def client_users(request: Request, page: int = 1, q: str = "", active: str = "", db: Session = Depends(get_db),
                 user: User = Depends(require("clients.view"))):
    query = db.query(Client).join(User, Client.user_id == User.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Client.full_name.ilike(like), Client.client_code.ilike(like), User.email.ilike(like), User.username.ilike(like)))
    if active == "yes":
        query = query.filter(User.is_active.is_(True))
    elif active == "no":
        query = query.filter(User.is_active.is_(False))
    pg = paginate(query.order_by(User.last_login_at.desc().nullslast(), Client.id), page, 25)
    total = db.query(func.count(Client.id)).filter(Client.user_id.isnot(None)).scalar() or 0
    stats = {"with_login": total, "without_login": db.query(func.count(Client.id)).filter(Client.user_id.is_(None)).scalar() or 0,
             "active": db.query(func.count(Client.id)).join(User, Client.user_id == User.id).filter(User.is_active.is_(True)).scalar() or 0,
             "logged_in_30d": db.query(func.count(Client.id)).join(User, Client.user_id == User.id)
             .filter(User.last_login_at >= datetime.utcnow() - timedelta(days=30)).scalar() or 0}
    return render(request, "clients/users.html", {"user": user, "page": pg, "q": q, "active": active, "stats": stats,
                                                  "base_url": f"/clients/users?q={q}&active={active}", "can_update": rbac.has_permission(user, "clients.update")})


# --------------------------------------------------------------------------- create
@router.get("/new", include_in_schema=False)
def new_client(request: Request, status: str = "", db: Session = Depends(get_db), user: User = Depends(require("clients.add"))):
    return render(request, "clients/form.html", {"user": user, **_form_context(db), "mode": "new", "preset_status": norm_status(status) or "trial"})


def _read_form(form) -> dict:
    prefs = {"preferred_contact_time": form.get("preferred_contact_time") or "", "preferred_channel": form.get("preferred_channel") or "whatsapp",
             "preferred_language": form.get("preferred_language") or "English", "invoice_delivery": form.get("invoice_delivery") or "email"}
    return {"full_name": form.get("full_name"), "email": form.get("email"), "phone": form.get("phone"), "whatsapp": form.get("whatsapp"),
            "country": form.get("country"), "city": form.get("city"), "timezone": form.get("timezone"), "currency": form.get("currency"),
            "address": form.get("address"), "relationship_to_student": form.get("relationship_to_student"), "status": norm_status(form.get("status")),
            "source": form.get("source"), "billing_rep_id": form.get("billing_rep_id"), "consent_given": parse_bool(form.get("consent_given")),
            "whatsapp_opt_in": parse_bool(form.get("whatsapp_opt_in")), "preferences": prefs, "notes": form.get("notes"),
            "household_id": form.get("household_id"), "household_name": form.get("household_name"),
            # ERP Basic Detail
            "joined_at": parse_date(form.get("joined_at")), "fee_recurrence": form.get("fee_recurrence") or "monthly",
            "state": (form.get("state") or "").strip() or None, "opening_balance": parse_float(form.get("opening_balance"), 0.0),
            "legacy_code": (form.get("legacy_code") or "").strip() or None, "shift": form.get("shift") or "night",
            "referred_by_client_id": parse_int(form.get("referred_by_client_id")), "status_remarks": (form.get("status_remarks") or "").strip() or None,
            "academic_manager_id": parse_int(form.get("academic_manager_id")), "academic_group_id": parse_int(form.get("academic_group_id"))}


def _apply_erp_fields(c: Client, data: dict) -> None:
    if data.get("joined_at"):
        c.joined_at = data["joined_at"]
    c.fee_recurrence = data.get("fee_recurrence") or c.fee_recurrence or "monthly"
    c.state = data.get("state")
    c.legacy_code = data.get("legacy_code")
    c.shift = data.get("shift") if data.get("shift") in ("morning", "night") else (c.shift or "night")
    c.referred_by_client_id = data.get("referred_by_client_id") if data.get("referred_by_client_id") != c.id else None
    c.status_remarks = data.get("status_remarks")
    c.academic_manager_id = data.get("academic_manager_id")
    c.academic_group_id = data.get("academic_group_id")


def _post_opening_balance(db: Session, c: Client, amount: float, user: User) -> None:
    """Opening balance -> one ledger entry (positive = family owes, negative = credit)."""
    amount = round(float(amount or 0), 2)
    c.opening_balance = amount
    if amount == 0:
        return
    try:
        from app.services.billing import post_adjustment
        post_adjustment(db, c, abs(amount), c.currency, "Opening balance (migrated from previous system)", user,
                        rationale="Opening balance entered on client creation", direction="debit" if amount > 0 else "credit")
    except Exception:
        e = LedgerEntry(client_id=c.id, entry_date=c.joined_at or date.today(), entry_type="adjustment",
                        description="Opening balance (migrated from previous system)", debit=amount if amount > 0 else 0,
                        credit=-amount if amount < 0 else 0, currency=c.currency, balance_after=amount, reference_type="manual",
                        created_by_id=user.id)
        db.add(e)
        db.flush()


@router.post("/new", include_in_schema=False)
async def create_client(request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.add"))):
    form = await request.form()
    data = _read_form(form)
    if not (data["full_name"] or "").strip():
        return redirect("/clients/new", "Client Name is required.", "error")
    if not data.get("country"):
        return redirect("/clients/new", "Country is required.", "error")
    if not data["consent_given"]:
        return redirect("/clients/new", "Consent to the safeguarding & data policy is required before a client record is created.", "error")
    if data["status"] not in STATUSES:
        data["status"] = "trial"
    c, pwd = svc.create_client_with_portal(db, data, user, request=request, with_portal=parse_bool(form.get("create_portal")))
    _apply_erp_fields(c, data)
    c.lead_added_by_id = user.id
    if c.status != "trial":
        c.converted_by_id, c.converted_at = user.id, datetime.utcnow()
    _post_opening_balance(db, c, data["opening_balance"], user)
    db.commit()
    if pwd:
        return render(request, "clients/portal_created.html", {"user": user, "client": c, "password": pwd, "login_email": c.user.email if c.user else c.email})
    return redirect(f"/clients/{c.id}", f"Client {c.client_code} created.")


# --------------------------------------------------------------------------- Client Form (detail)
@router.get("/{id}", include_in_schema=False)
def client_detail(id: int, request: Request, tab: str = "overview", db: Session = Depends(get_db), user: User = Depends(require("clients.view"))):
    c = _get(db, id)
    if tab == "basic":
        return redirect(f"/clients/{c.id}/edit")
    tabs = [(k, l, f"/clients/{c.id}/edit" if k == "basic" else f"/clients/{c.id}?tab={k}") for k, l in TABS]
    ctx: dict = {"user": user, "c": c, "tab": tab, "tabs": tabs, "show_contact": can_see_contact(user),
                 "balance": svc.client_balance(db, c), "students": c.students,
                 "change_log_url": f"/admin/audit/entity/Client/{c.id}" if rbac.has_permission(user, "audit.view") else f"/clients/{c.id}?tab=audit",
                 "contact_types": CONTACT_TYPES, "credential_types": CREDENTIAL_TYPES}
    if tab == "overview":
        ctx["prefs"] = db.query(CommunicationPreference).filter(CommunicationPreference.client_id == c.id).all()
        ctx["open_cases"] = db.query(Case).filter(Case.client_id == c.id, Case.status.in_(["open", "in_progress", "waiting", "escalated"])).count()
        ctx["overdue"] = db.query(Invoice).filter(Invoice.client_id == c.id, Invoice.status == "overdue").count()
        ctx["recent_audit"] = db.query(AuditEvent).filter(AuditEvent.entity_type == "Client", AuditEvent.entity_id == c.id).order_by(AuditEvent.created_at.desc()).limit(5).all()
        ctx["contacts"] = db.query(ClientContact).filter(ClientContact.client_id == c.id, ClientContact.status == "active").all()
        ctx["subscriptions_count"] = db.query(func.count(Subscription.id)).filter(Subscription.client_id == c.id).scalar() or 0
    elif tab == "contacts":
        ctx["contacts"] = db.query(ClientContact).filter(ClientContact.client_id == c.id).order_by(ClientContact.status, ClientContact.id).all()
    elif tab == "credentials":
        ctx["credentials"] = db.query(ClientCredential).filter(ClientCredential.client_id == c.id).order_by(ClientCredential.status, ClientCredential.id).all()
    elif tab == "billing":
        ctx["subscriptions"] = db.query(Subscription).filter(Subscription.client_id == c.id).order_by(Subscription.created_at.desc()).all()
        ctx["invoices"] = db.query(Invoice).filter(Invoice.client_id == c.id).order_by(Invoice.issue_date.desc()).limit(50).all()
        ctx["payments"] = db.query(Payment).filter(Payment.client_id == c.id).order_by(Payment.received_at.desc()).limit(20).all()
        ctx["ledger"] = db.query(LedgerEntry).filter(LedgerEntry.client_id == c.id).order_by(LedgerEntry.entry_date.desc(), LedgerEntry.id.desc()).limit(100).all()
    elif tab == "cases":
        ctx["cases"] = db.query(Case).filter(Case.client_id == c.id).order_by(Case.created_at.desc()).all()
    elif tab == "feedback":
        ctx["feedback"] = db.query(Feedback).filter(Feedback.client_id == c.id).order_by(Feedback.created_at.desc()).all()
    elif tab == "conversations":
        convs = db.query(Conversation).filter(Conversation.client_id == c.id).order_by(Conversation.last_message_at.desc()).all()
        ctx["conversations"] = convs
        ctx["messages"] = db.query(Message).filter(Message.conversation_id.in_([x.id for x in convs] or [-1])).order_by(Message.created_at.desc()).limit(40).all()
    elif tab == "referrals":
        ctx["referrals"] = db.query(Referral).filter(Referral.ambassador_client_id == c.id).order_by(Referral.created_at.desc()).all()
        ctx["referred_by"] = db.query(Referral).filter(Referral.referred_client_id == c.id).first()
    elif tab == "comms":
        ctx["notifications"] = (db.query(Notification).filter(Notification.user_id == c.user_id).order_by(Notification.created_at.desc()).limit(100).all()
                                if c.user_id else [])
    elif tab == "audit":
        sids = [s.id for s in c.students] or [-1]
        ctx["events"] = (db.query(AuditEvent).filter(or_((AuditEvent.entity_type == "Client") & (AuditEvent.entity_id == c.id),
                                                          (AuditEvent.entity_type == "Student") & (AuditEvent.entity_id.in_(sids)),
                                                          (AuditEvent.entity_type == "User") & (AuditEvent.entity_id == (c.user_id or -1))))
                         .order_by(AuditEvent.created_at.desc()).limit(200).all())
    return render(request, "clients/detail.html", ctx)


# --------------------------------------------------------------------------- masked value reveal (audited, like the ERP "View" link)
@router.get("/{id}/reveal/{field}", include_in_schema=False)
def reveal_field(id: int, field: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.view"))):
    c = _get(db, id)
    if field not in ("whatsapp", "phone", "email"):
        raise HTTPException(404)
    value = getattr(c, field) or "-"
    log_action(db, user, "reveal", "clients", entity=c, description=f"{field.title()} of {c.client_code} viewed unmasked",
               rationale="View link on client list", request=request, severity="warning")
    db.commit()
    return HTMLResponse(f'<span class="font-mono text-xs">{value}</span>')


@router.get("/{id}/credentials/{cid}/reveal", include_in_schema=False)
def reveal_credential(id: int, cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.update"))):
    c = _get(db, id)
    cred = db.query(ClientCredential).filter(ClientCredential.id == cid, ClientCredential.client_id == c.id).first()
    if not cred:
        raise HTTPException(404)
    log_action(db, user, "reveal", "clients", entity=cred, description=f"{cred.credential_type} password of {c.client_code} viewed",
               rationale="Reveal on credentials grid", request=request, severity="warning")
    db.commit()
    return HTMLResponse(f'<span class="font-mono text-xs">{cred.secret or "-"}</span>')


# --------------------------------------------------------------------------- edit (Basic Detail)
@router.get("/{id}/edit", include_in_schema=False)
def edit_client(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.update"))):
    c = _get(db, id)
    prefs = {p.channel: p.opted_in for p in db.query(CommunicationPreference).filter(CommunicationPreference.client_id == c.id)}
    return render(request, "clients/form.html", {"user": user, **_form_context(db, c), "mode": "edit", "comm_prefs": prefs,
                                                 "change_log_url": f"/admin/audit/entity/Client/{c.id}" if rbac.has_permission(user, "audit.view") else f"/clients/{c.id}?tab=audit"})


@router.post("/{id}/edit", include_in_schema=False)
async def update_client(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.update"))):
    c = _get(db, id)
    form = await request.form()
    data = _read_form(form)
    before = snapshot(c)
    new_status = data["status"] if data["status"] in STATUSES else c.status
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if new_status != c.status and not reason:
        return redirect(f"/clients/{c.id}/edit", "A reason is required when the client status changes.", "error")
    for f in ("full_name", "email", "phone", "whatsapp", "country", "city", "timezone", "currency", "address", "relationship_to_student", "source", "notes"):
        v = data.get(f)
        if f == "email":
            v = (v or "").strip().lower() or None
        setattr(c, f, v if v not in ("",) else None)
    c.full_name = c.full_name or before["full_name"]
    c.country = c.country or before["country"]
    c.timezone = c.timezone or before["timezone"]
    c.currency = c.currency or before["currency"]
    c.billing_rep_id = int(data["billing_rep_id"]) if data.get("billing_rep_id") else None
    _apply_erp_fields(c, data)
    if data["consent_given"] and not c.consent_given:
        c.consent_at = datetime.utcnow()
    c.consent_given = data["consent_given"]
    c.whatsapp_opt_in = data["whatsapp_opt_in"]
    c.preferences = {**(c.preferences or {}), **data["preferences"]}
    if data.get("household_id"):
        c.household_id = int(data["household_id"])
    elif data.get("household_name"):
        hh = Household(name=data["household_name"].strip(), country=c.country)
        db.add(hh)
        db.flush()
        c.household_id = hh.id
    svc.upsert_comm_prefs(db, c, {"whatsapp": c.whatsapp_opt_in, "email": parse_bool(form.get("email_opt_in")), "in_app": True})
    if c.user:
        c.user.full_name = c.full_name
        c.user.timezone = c.timezone
        c.user.phone = c.phone
    if new_status != c.status:
        old = c.status
        c.status = new_status
        if new_status == "trial" and old != "trial":
            c.converted_at, c.converted_by_id = None, None
        elif old == "trial" and new_status in ("active", "on_leave"):
            c.converted_at, c.converted_by_id = datetime.utcnow(), user.id
        if c.user:
            c.user.is_active = new_status not in ("inactive", "churned")
        log_action(db, user, "status_change", "clients", entity=c, description=f"Client {c.client_code} status {old} -> {new_status}",
                   rationale=reason, before={"status": old}, after={"status": new_status}, request=request, consequential=True)
    log_action(db, user, "update", "clients", entity=c, description=f"Client {c.client_code} updated (Basic Detail)", before=before, after=snapshot(c),
               rationale=reason or None, request=request)
    db.commit()
    return redirect(f"/clients/{c.id}", "Client updated.")


# --------------------------------------------------------------------------- Contacts grid
@router.post("/{id}/contacts", include_in_schema=False)
async def add_contact(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.update"))):
    c = _get(db, id)
    form = await request.form()
    detail = (form.get("detail") or "").strip()
    if not detail:
        return redirect(f"/clients/{c.id}?tab=contacts", "Contact Detail is required.", "error")
    row = ClientContact(client_id=c.id, contact_type=form.get("contact_type") if form.get("contact_type") in CONTACT_TYPES else "other",
                        detail=detail[:200], remarks=(form.get("remarks") or "").strip()[:200] or None, status="active")
    db.add(row)
    db.flush()
    log_action(db, user, "create", "clients", entity=row, description=f"Contact ({row.contact_type}) added to {c.client_code}", request=request)
    db.commit()
    return redirect(f"/clients/{c.id}?tab=contacts", "Contact added.")


@router.post("/{id}/contacts/{cid}", include_in_schema=False)
async def edit_contact(id: int, cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.update"))):
    c = _get(db, id)
    row = db.query(ClientContact).filter(ClientContact.id == cid, ClientContact.client_id == c.id).first()
    if not row:
        raise HTTPException(404)
    form = await request.form()
    before = snapshot(row)
    if form.get("toggle"):
        row.status = "inactive" if row.status == "active" else "active"
        msg = f"Contact marked {row.status}."
    else:
        if form.get("contact_type") in CONTACT_TYPES:
            row.contact_type = form.get("contact_type")
        row.detail = (form.get("detail") or row.detail).strip()[:200]
        row.remarks = (form.get("remarks") or "").strip()[:200] or None
        msg = "Contact updated."
    log_action(db, user, "update", "clients", entity=row, description=f"Contact row of {c.client_code} updated", before=before, after=snapshot(row), request=request)
    db.commit()
    return redirect(f"/clients/{c.id}?tab=contacts", msg)


# --------------------------------------------------------------------------- Credentials grid
@router.post("/{id}/credentials", include_in_schema=False)
async def add_credential(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.update"))):
    c = _get(db, id)
    form = await request.form()
    login = (form.get("login") or "").strip()
    if not login:
        return redirect(f"/clients/{c.id}?tab=credentials", "Login is required.", "error")
    row = ClientCredential(client_id=c.id, credential_type=form.get("credential_type") if form.get("credential_type") in CREDENTIAL_TYPES else "other",
                           login=login[:200], secret=(form.get("secret") or "").strip()[:200] or None,
                           remarks=(form.get("remarks") or "").strip()[:200] or None, status="active")
    db.add(row)
    db.flush()
    log_action(db, user, "create", "clients", entity=row, description=f"{row.credential_type} credential added to {c.client_code}", request=request, severity="warning")
    db.commit()
    return redirect(f"/clients/{c.id}?tab=credentials", "Credential added.")


@router.post("/{id}/credentials/{cid}", include_in_schema=False)
async def edit_credential(id: int, cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.update"))):
    c = _get(db, id)
    row = db.query(ClientCredential).filter(ClientCredential.id == cid, ClientCredential.client_id == c.id).first()
    if not row:
        raise HTTPException(404)
    form = await request.form()
    before = snapshot(row)
    before.pop("secret", None)
    if form.get("toggle"):
        row.status = "inactive" if row.status == "active" else "active"
        msg = f"Credential marked {row.status}."
    else:
        if form.get("credential_type") in CREDENTIAL_TYPES:
            row.credential_type = form.get("credential_type")
        row.login = (form.get("login") or row.login).strip()[:200]
        if (form.get("secret") or "").strip():
            row.secret = form.get("secret").strip()[:200]
        row.remarks = (form.get("remarks") or "").strip()[:200] or None
        msg = "Credential updated."
    after = snapshot(row)
    after.pop("secret", None)
    log_action(db, user, "update", "clients", entity=row, description=f"Credential row of {c.client_code} updated", before=before, after=after,
               request=request, severity="warning")
    db.commit()
    return redirect(f"/clients/{c.id}?tab=credentials", msg)


# --------------------------------------------------------------------------- Students grid quick-add
@router.post("/{id}/students/quick-add", include_in_schema=False)
async def quick_add_student(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("students.add"))):
    c = _get(db, id)
    form = await request.form()
    name = (form.get("full_name") or "").strip()
    if not name:
        return redirect(f"/clients/{c.id}?tab=students", "Student name is required.", "error")
    status = form.get("status") if form.get("status") in ("trial", "active", "free") else "trial"
    s = svc.create_student(db, c, {"full_name": name, "gender": form.get("gender") or "male", "date_of_birth": parse_date(form.get("date_of_birth")),
                                   "status": status, "timezone": c.timezone, "guardian_consent": c.consent_given}, user, request=request)
    s.trial_days = parse_int(form.get("trial_days"), 3) or 3
    s.email = (form.get("email") or "").strip().lower() or None
    s.referred_by = (form.get("referred_by") or "").strip() or None
    s.join_date = parse_date(form.get("join_date")) or date.today()
    db.commit()
    return redirect(f"/clients/{c.id}?tab=students", f"Student {s.student_code} added to {c.client_code}.")


# --------------------------------------------------------------------------- portal login / status
@router.post("/{id}/portal-login", include_in_schema=False)
async def create_portal_login(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.update"))):
    c = _get(db, id)
    try:
        u, pwd = svc.create_portal_user(db, c, user, request=request)
    except ValueError as exc:
        return redirect(f"/clients/{c.id}", str(exc), "error")
    db.commit()
    return render(request, "clients/portal_created.html", {"user": user, "client": c, "password": pwd, "login_email": u.email})


@router.post("/{id}/reset-portal-password", include_in_schema=False)
async def reset_portal_password(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.update"))):
    c = _get(db, id)
    if not c.user:
        return redirect(f"/clients/{c.id}", "This client has no portal login yet.", "error")
    from app.core.security import hash_password
    pwd = svc.temp_password()
    c.user.hashed_password = hash_password(pwd)
    c.user.must_change_password = True
    log_action(db, user, "password_reset", "clients", entity=c, description=f"Portal password reset for {c.client_code}", request=request, severity="warning")
    db.commit()
    return render(request, "clients/portal_created.html", {"user": user, "client": c, "password": pwd, "login_email": c.user.email, "reset": True})


@router.post("/{id}/deactivate", include_in_schema=False)
async def deactivate(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.update"))):
    c = _get(db, id)
    form = await request.form()
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if not reason:
        return redirect(f"/clients/{c.id}", "A reason is required for a status change.", "error")
    before = {"status": c.status}
    c.status = "churned" if form.get("mode") == "churned" else "inactive"
    c.status_remarks = reason[:200]
    if c.user:
        c.user.is_active = False
    log_action(db, user, "deactivate", "clients", entity=c, description=f"Client {c.client_code} marked {status_label(c.status, 'client')}", rationale=reason,
               before=before, after={"status": c.status}, request=request, consequential=True)
    db.commit()
    return redirect(f"/clients/{c.id}", f"Client marked {status_label(c.status, 'client')}; portal login disabled.", "warning")


@router.post("/{id}/reactivate", include_in_schema=False)
async def reactivate(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.update"))):
    c = _get(db, id)
    form = await request.form()
    before = {"status": c.status}
    c.status = "active"
    if c.user:
        c.user.is_active = True
    log_action(db, user, "reactivate", "clients", entity=c, description=f"Client {c.client_code} reactivated (Regular)", rationale=form.get("reason"),
               before=before, after={"status": "active"}, request=request)
    db.commit()
    return redirect(f"/clients/{c.id}", "Client reactivated and portal login enabled.")
