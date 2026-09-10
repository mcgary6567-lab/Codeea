"""Clients / parents (Module 5): list, create/edit, detail tabs, portal login, deactivate/reactivate."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import require, csrf_protect
from app.core.templating import render
from app.core.utils import redirect, paginate, parse_bool
from app.database import get_db
from app.models.core import User, AuditEvent, Notification, CommunicationPreference, Role
from app.models.crm import Case, Feedback, Conversation, Referral, Message
from app.models.finance import Subscription, Invoice, LedgerEntry, Payment
from app.models.people import Client, Student, Household
from app.services import people as svc

router = APIRouter(prefix="/clients", dependencies=[Depends(csrf_protect)])

TABS = [("overview", "Overview"), ("students", "Students"), ("billing", "Billing & Ledger"), ("cases", "Cases"), ("feedback", "Feedback"),
        ("conversations", "Conversations"), ("referrals", "Referrals"), ("comms", "Communication log"), ("audit", "Audit trail")]
STATUSES = ["trial", "active", "inactive", "churned"]


def can_see_contact(user: User) -> bool:
    return rbac.has_permission(user, "clients.update") or rbac.is_management(user)


def _get(db: Session, id: int) -> Client:
    c = db.query(Client).get(id)
    if not c:
        raise HTTPException(404, "Client not found")
    return c


def _form_context(db: Session, c: Client | None = None) -> dict:
    households = db.query(Household).order_by(Household.name).all()
    reps = db.query(User).join(Role, User.role_id == Role.id).filter(Role.slug.in_(["billing_rep", "hod_finance", "accountant"]), User.is_active.is_(True)).all()
    return {"countries": svc.COUNTRY_NAMES, "timezones": svc.TIMEZONES, "currencies": svc.CURRENCIES, "relationships": svc.RELATIONSHIPS,
            "statuses": STATUSES, "households": households, "household_options": [(h.id, f"{h.name} - {h.country or ''}") for h in households],
            "billing_reps": reps, "billing_rep_options": [(u.id, u.full_name) for u in reps],
            "sources": ["Website", "WhatsApp", "Meta Ads", "Google Ads", "Referral", "Manual", "GHL"], "client": c,
            "country_map": {k: {"timezone": v[1], "currency": v[2]} for k, v in svc.COUNTRY_MAP.items()}}


@router.get("", include_in_schema=False)
def list_clients(request: Request, page: int = 1, q: str = "", status: str = "", country: str = "", db: Session = Depends(get_db),
                 user: User = Depends(require("clients.view"))):
    query = db.query(Client)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Client.full_name.ilike(like), Client.client_code.ilike(like), Client.email.ilike(like), Client.phone.ilike(like), Client.city.ilike(like)))
    if status:
        query = query.filter(Client.status == status)
    if country:
        query = query.filter(Client.country == country)
    pg = paginate(query.order_by(Client.created_at.desc()), page, 25)
    counts = dict(db.query(Client.status, func.count(Client.id)).group_by(Client.status).all())
    stats = {"active": counts.get("active", 0), "trial": counts.get("trial", 0), "churned": counts.get("churned", 0) + counts.get("inactive", 0),
             "countries": db.query(func.count(func.distinct(Client.country))).scalar() or 0, "total": sum(counts.values())}
    student_counts = dict(db.query(Student.client_id, func.count(Student.id)).group_by(Student.client_id).all())
    countries = [r[0] for r in db.query(Client.country).distinct().order_by(Client.country)]
    base = f"/clients?q={q}&status={status}&country={country}"
    return render(request, "clients/list.html", {"user": user, "page": pg, "q": q, "status": status, "country": country, "stats": stats,
                                                 "countries": countries, "statuses": STATUSES, "student_counts": student_counts,
                                                 "show_contact": can_see_contact(user), "base_url": base})


@router.get("/new", include_in_schema=False)
def new_client(request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.add"))):
    return render(request, "clients/form.html", {"user": user, **_form_context(db), "mode": "new"})


def _read_form(form) -> dict:
    prefs = {"preferred_contact_time": form.get("preferred_contact_time") or "", "preferred_channel": form.get("preferred_channel") or "whatsapp",
             "preferred_language": form.get("preferred_language") or "English", "invoice_delivery": form.get("invoice_delivery") or "email"}
    return {"full_name": form.get("full_name"), "email": form.get("email"), "phone": form.get("phone"), "whatsapp": form.get("whatsapp"),
            "country": form.get("country"), "city": form.get("city"), "timezone": form.get("timezone"), "currency": form.get("currency"),
            "address": form.get("address"), "relationship_to_student": form.get("relationship_to_student"), "status": form.get("status"),
            "source": form.get("source"), "billing_rep_id": form.get("billing_rep_id"), "consent_given": parse_bool(form.get("consent_given")),
            "whatsapp_opt_in": parse_bool(form.get("whatsapp_opt_in")), "preferences": prefs, "notes": form.get("notes"),
            "household_id": form.get("household_id"), "household_name": form.get("household_name")}


@router.post("/new", include_in_schema=False)
async def create_client(request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.add"))):
    form = await request.form()
    data = _read_form(form)
    if not (data["full_name"] or "").strip():
        return redirect("/clients/new", "Full name is required.", "error")
    if not data["consent_given"]:
        return redirect("/clients/new", "Consent to the safeguarding & data policy is required before a client record is created.", "error")
    c, pwd = svc.create_client_with_portal(db, data, user, request=request, with_portal=parse_bool(form.get("create_portal")))
    db.commit()
    if pwd:
        return render(request, "clients/portal_created.html", {"user": user, "client": c, "password": pwd, "login_email": c.user.email if c.user else c.email})
    return redirect(f"/clients/{c.id}", f"Client {c.client_code} created.")


@router.get("/{id}", include_in_schema=False)
def client_detail(id: int, request: Request, tab: str = "overview", db: Session = Depends(get_db), user: User = Depends(require("clients.view"))):
    c = _get(db, id)
    ctx: dict = {"user": user, "c": c, "tab": tab, "tabs": [(k, l, f"/clients/{c.id}?tab={k}") for k, l in TABS], "show_contact": can_see_contact(user),
                 "balance": svc.client_balance(db, c), "students": c.students}
    if tab == "overview":
        ctx["prefs"] = db.query(CommunicationPreference).filter(CommunicationPreference.client_id == c.id).all()
        ctx["open_cases"] = db.query(Case).filter(Case.client_id == c.id, Case.status.in_(["open", "in_progress", "waiting", "escalated"])).count()
        ctx["overdue"] = db.query(Invoice).filter(Invoice.client_id == c.id, Invoice.status == "overdue").count()
        ctx["recent_audit"] = db.query(AuditEvent).filter(AuditEvent.entity_type == "Client", AuditEvent.entity_id == c.id).order_by(AuditEvent.created_at.desc()).limit(5).all()
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


@router.get("/{id}/edit", include_in_schema=False)
def edit_client(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.update"))):
    c = _get(db, id)
    prefs = {p.channel: p.opted_in for p in db.query(CommunicationPreference).filter(CommunicationPreference.client_id == c.id)}
    return render(request, "clients/form.html", {"user": user, **_form_context(db, c), "mode": "edit", "comm_prefs": prefs})


@router.post("/{id}/edit", include_in_schema=False)
async def update_client(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.update"))):
    c = _get(db, id)
    form = await request.form()
    data = _read_form(form)
    before = snapshot(c)
    for f in ("full_name", "email", "phone", "whatsapp", "country", "city", "timezone", "currency", "address", "relationship_to_student", "status", "source", "notes"):
        v = data.get(f)
        if f == "email":
            v = (v or "").strip().lower() or None
        setattr(c, f, v if v not in ("",) else None)
    c.full_name = c.full_name or before["full_name"]
    c.country = c.country or before["country"]
    c.timezone = c.timezone or before["timezone"]
    c.currency = c.currency or before["currency"]
    c.status = c.status or before["status"]
    c.billing_rep_id = int(data["billing_rep_id"]) if data.get("billing_rep_id") else None
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
    log_action(db, user, "update", "clients", entity=c, description=f"Client {c.client_code} updated", before=before, after=snapshot(c), request=request)
    db.commit()
    return redirect(f"/clients/{c.id}", "Client updated.")


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
    reason = form.get("reason") or form.get("rationale") or ""
    before = {"status": c.status}
    c.status = "churned" if form.get("mode") == "churned" else "inactive"
    if c.user:
        c.user.is_active = False
    log_action(db, user, "deactivate", "clients", entity=c, description=f"Client {c.client_code} deactivated ({c.status})", rationale=reason,
               before=before, after={"status": c.status}, request=request, consequential=True)
    db.commit()
    return redirect(f"/clients/{c.id}", f"Client marked {c.status}; portal login disabled.", "warning")


@router.post("/{id}/reactivate", include_in_schema=False)
async def reactivate(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.update"))):
    c = _get(db, id)
    form = await request.form()
    before = {"status": c.status}
    c.status = "active"
    if c.user:
        c.user.is_active = True
    log_action(db, user, "reactivate", "clients", entity=c, description=f"Client {c.client_code} reactivated", rationale=form.get("reason"),
               before=before, after={"status": "active"}, request=request)
    db.commit()
    return redirect(f"/clients/{c.id}", "Client reactivated and portal login enabled.")
