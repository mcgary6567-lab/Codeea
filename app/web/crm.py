"""CRM & Growth web routes: leads pipeline (M3), campaigns, marketing analytics (M34),
shared WhatsApp inbox (S7), automation sequences, ambassador referrals (M43)."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, date, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import require, csrf_protect
from app.core.templating import render
from app.core.utils import redirect, paginate, parse_date, parse_datetime, parse_int, parse_float, parse_bool
from app.database import get_db
from app.models.academic import Course
from app.models.core import User, Role, Integration, AuditEvent
from app.models.crm import (LEAD_STAGES, Lead, LeadActivity, LeadSource, Campaign, CampaignMetric, Conversation, Message, InternalNote,
                            MessageTemplate, Sequence, SequenceEnrollment, Referral, Case, Feedback)
from app.models.people import Client, Student, Teacher
from app.models.scheduling import Trial
from app.services import crm as svc

router = APIRouter(prefix="/crm", dependencies=[Depends(csrf_protect)])

PLATFORMS = ["meta", "google", "organic", "referral", "whatsapp", "email", "other"]
CAMPAIGN_STATUSES = ["draft", "active", "paused", "ended"]
CONV_STATUSES = ["open", "pending", "closed"]
TEMPLATE_CATEGORIES = ["follow_up", "welcome", "reminder", "billing", "retention", "referral", "feedback", "trial"]


# ============================================================================= helpers
def scope_leads(query, user: User):
    """lead_generator sees the leads they generated; lead_closer sees assigned + unassigned."""
    slug = getattr(user, "role_slug", None)
    if user.is_superuser or slug not in ("lead_generator", "lead_closer"):
        return query
    if slug == "lead_generator":
        return query.filter(or_(Lead.generator_id == user.id, Lead.assigned_to_id == user.id))
    return query.filter(or_(Lead.assigned_to_id == user.id, Lead.assigned_to_id.is_(None)))


def _lead(db: Session, id: int, user: User) -> Lead:
    lead = db.get(Lead, id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    slug = getattr(user, "role_slug", None)
    if not user.is_superuser and slug == "lead_generator" and lead.generator_id != user.id and lead.assigned_to_id != user.id:
        raise HTTPException(404, "Lead not found")
    if not user.is_superuser and slug == "lead_closer" and lead.assigned_to_id not in (None, user.id):
        raise HTTPException(404, "Lead not found")
    return lead


def _opts(rows, label: str = "name") -> list[tuple]:
    return [(r.id, getattr(r, label, None) or getattr(r, "full_name", str(r.id))) for r in rows]


def _lead_form_ctx(db: Session) -> dict:
    sources = db.query(LeadSource).filter(LeadSource.is_active.is_(True)).order_by(LeadSource.name).all()
    campaigns = db.query(Campaign).order_by(Campaign.name).all()
    courses = db.query(Course).order_by(Course.name).all()
    closers = svc.closers(db)
    generators = svc.users_with_role(db, "lead_generator") + svc.users_with_role(db, "hod_marketing")
    teachers = db.query(Teacher).filter(Teacher.status == "active").order_by(Teacher.full_name).all()
    return {"sources": sources, "campaigns": campaigns, "courses": courses, "closers": closers, "generators": generators,
            "teachers": teachers, "countries": list(svc.COUNTRY_CURRENCY.keys()), "stages": LEAD_STAGES,
            "source_options": _opts(sources), "campaign_options": _opts(campaigns), "course_options": _opts(courses),
            "closer_options": _opts(closers, "full_name"), "generator_options": _opts(generators, "full_name"),
            "teacher_options": _opts(teachers, "full_name")}


def _csv_response(filename: str, headers: list[str], rows: list[list]) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ============================================================================= LEADS (Module 3)
@router.get("/leads", include_in_schema=False)
def leads_board(request: Request, view: str = "kanban", page: int = 1, q: str = "", stage: str = "", source: str = "", campaign: str = "",
                assigned: str = "", generator: str = "", country: str = "", min_score: str = "", start: str = "", end: str = "",
                db: Session = Depends(get_db), user: User = Depends(require("leads.view"))):
    base = scope_leads(db.query(Lead), user)
    if q:
        like = f"%{q}%"
        base = base.filter(or_(Lead.full_name.ilike(like), Lead.lead_code.ilike(like), Lead.email.ilike(like), Lead.phone.ilike(like),
                               Lead.student_name.ilike(like), Lead.whatsapp.ilike(like)))
    if source:
        base = base.filter(Lead.source_id == int(source))
    if campaign:
        base = base.filter(Lead.campaign_id == int(campaign))
    if assigned == "unassigned":
        base = base.filter(Lead.assigned_to_id.is_(None))
    elif assigned:
        base = base.filter(Lead.assigned_to_id == int(assigned))
    if generator:
        base = base.filter(Lead.generator_id == int(generator))
    if country:
        base = base.filter(Lead.country == country)
    if min_score:
        base = base.filter(Lead.score >= int(min_score))
    d1, d2 = parse_date(start), parse_date(end)
    if d1:
        base = base.filter(Lead.created_at >= datetime.combine(d1, datetime.min.time()))
    if d2:
        base = base.filter(Lead.created_at <= datetime.combine(d2, datetime.max.time()))
    stats = svc.lead_stats(db, base)
    ctx = {"user": user, "view": view, "q": q, "stage": stage, "source": source, "campaign": campaign, "assigned": assigned,
           "generator": generator, "country": country, "min_score": min_score, "start": start, "end": end, "stats": stats,
           "stages": LEAD_STAGES, **_lead_form_ctx(db)}
    qs = (f"?view={view}&q={q}&stage={stage}&source={source}&campaign={campaign}&assigned={assigned}&generator={generator}"
          f"&country={country}&min_score={min_score}&start={start}&end={end}")
    ctx["base_url"] = "/crm/leads" + qs
    if view == "list":
        listing = base.filter(Lead.stage == stage) if stage else base
        ctx["page"] = paginate(listing.order_by(Lead.created_at.desc()), page, 30)
    else:
        columns = []
        for s in LEAD_STAGES:
            items = base.filter(Lead.stage == s).order_by(Lead.score.desc(), Lead.created_at.desc()).limit(25).all()
            columns.append({"stage": s, "count": stats["by_stage"].get(s, 0), "items": items})
        ctx["columns"] = columns
    return render(request, "crm/leads.html", ctx)


@router.get("/leads/kpis", include_in_schema=False)
def lead_kpis(request: Request, days: int = 90, db: Session = Depends(get_db), user: User = Depends(require("leads.view"))):
    since = date.today() - timedelta(days=max(7, days))
    kpis = svc.closer_kpis(db, since)
    leads = db.query(Lead).filter(Lead.created_at >= datetime.combine(since, datetime.min.time())).all()
    lost = {}
    for l in leads:
        if l.stage == "lost":
            lost[l.lost_reason or "Not recorded"] = lost.get(l.lost_reason or "Not recorded", 0) + 1
    lost_rows = sorted(lost.items(), key=lambda x: -x[1])
    spend = float(db.query(func.coalesce(func.sum(CampaignMetric.spend), 0)).filter(CampaignMetric.date >= since).scalar() or 0)
    won = sum(1 for l in leads if l.stage == "won")
    src_rows = svc._count_by(leads, lambda l: l.source.name if l.source else "Unknown")
    return render(request, "crm/lead_kpis.html", {"user": user, "kpis": kpis, "days": days, "lost_rows": lost_rows, "since": since,
                                                  "spend": round(spend, 2), "leads": len(leads), "won": won,
                                                  "cpl": round(spend / len(leads), 2) if leads else 0.0,
                                                  "cac": round(spend / won, 2) if won else 0.0,
                                                  "source_labels": [k for k, _ in src_rows], "source_values": [v for _, v in src_rows]})


@router.get("/leads/export", include_in_schema=False)
def leads_export(request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.export", "leads.view", any_of=True))):
    rows = []
    for l in scope_leads(db.query(Lead), user).order_by(Lead.created_at.desc()).limit(5000).all():
        rows.append([l.lead_code, l.full_name, l.email or "", l.phone or "", l.country or "", l.stage, l.score,
                     l.source.name if l.source else "", l.campaign.name if l.campaign else "",
                     l.assigned_to.full_name if l.assigned_to else "", l.created_at.strftime("%Y-%m-%d") if l.created_at else "",
                     l.lost_reason or ""])
    log_action(db, user, "export", "leads", description=f"Exported {len(rows)} leads to CSV", request=request)
    db.commit()
    return _csv_response("leads.csv", ["Code", "Name", "Email", "Phone", "Country", "Stage", "Score", "Source", "Campaign", "Owner", "Created", "Lost reason"], rows)


@router.get("/leads/new", include_in_schema=False)
def new_lead(request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.add"))):
    return render(request, "crm/lead_form.html", {"user": user, "mode": "new", "lead": None, **_lead_form_ctx(db)})


@router.post("/leads/new", include_in_schema=False)
async def create_lead(request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.add"))):
    form = await request.form()
    name = (form.get("full_name") or "").strip()
    if not name:
        return redirect("/crm/leads/new", "Full name is required.", "error")
    data = {"full_name": name, "email": (form.get("email") or "").strip().lower() or None, "phone": form.get("phone"),
            "whatsapp": form.get("whatsapp"), "country": form.get("country"), "student_name": form.get("student_name"),
            "student_age": parse_int(form.get("student_age")), "students_count": parse_int(form.get("students_count"), 1) or 1,
            "course_interest_id": parse_int(form.get("course_interest_id")), "preferred_time": form.get("preferred_time"),
            "source_id": parse_int(form.get("source_id")), "campaign_id": parse_int(form.get("campaign_id")),
            "referral_code": form.get("referral_code"), "stage": form.get("stage") or "new",
            "assigned_to_id": parse_int(form.get("assigned_to_id")), "notes": form.get("notes"),
            "next_follow_up": parse_datetime(form.get("next_follow_up")), "whatsapp_opt_in": parse_bool(form.get("whatsapp_opt_in") or "1")}
    lead, dups = svc.create_lead(db, data, user, auto_assign=not data["assigned_to_id"], request=request)
    db.commit()
    if dups:
        return redirect(f"/crm/leads/{lead.id}",
                        f"Lead {lead.lead_code} created but flagged as a possible duplicate of {dups[0].lead_code} ({dups[0].full_name}).", "warning")
    return redirect(f"/crm/leads/{lead.id}", f"Lead {lead.lead_code} created and scored {lead.score}/100.")


@router.get("/leads/{id}", include_in_schema=False)
def lead_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.view"))):
    lead = _lead(db, id, user)
    conv = db.query(Conversation).filter(Conversation.lead_id == lead.id).order_by(Conversation.id.desc()).first()
    trials = db.query(Trial).filter(Trial.lead_id == lead.id).order_by(Trial.created_at.desc()).all()
    referral = db.query(Referral).filter(Referral.referred_lead_id == lead.id).first()
    dup = db.get(Lead, lead.is_duplicate_of_id) if lead.is_duplicate_of_id else None
    enrollments = (db.query(SequenceEnrollment).filter(SequenceEnrollment.contact_type == "lead", SequenceEnrollment.contact_id == lead.id)
                   .order_by(SequenceEnrollment.id.desc()).all())
    return render(request, "crm/lead_detail.html", {"user": user, "lead": lead, "conv": conv, "trials": trials, "referral": referral,
                                                    "duplicate_of": dup, "enrollments": enrollments,
                                                    "activities": lead.activities, "sequences": db.query(Sequence).filter(Sequence.is_active.is_(True)).all(),
                                                    **_lead_form_ctx(db)})


@router.get("/leads/{id}/edit", include_in_schema=False)
def edit_lead(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.update"))):
    lead = _lead(db, id, user)
    return render(request, "crm/lead_form.html", {"user": user, "mode": "edit", "lead": lead, **_lead_form_ctx(db)})


@router.post("/leads/{id}/edit", include_in_schema=False)
async def update_lead(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.update"))):
    lead = _lead(db, id, user)
    form = await request.form()
    before = snapshot(lead)
    for f in ("full_name", "email", "phone", "whatsapp", "country", "student_name", "preferred_time", "notes"):
        v = (form.get(f) or "").strip()
        setattr(lead, f, v or None)
    lead.full_name = lead.full_name or before["full_name"]
    lead.student_age = parse_int(form.get("student_age"))
    lead.students_count = parse_int(form.get("students_count"), 1) or 1
    lead.course_interest_id = parse_int(form.get("course_interest_id"))
    lead.source_id = parse_int(form.get("source_id"))
    lead.campaign_id = parse_int(form.get("campaign_id"))
    lead.referral_code = (form.get("referral_code") or "").strip().upper() or None
    lead.next_follow_up = parse_datetime(form.get("next_follow_up"))
    lead.whatsapp_opt_in = parse_bool(form.get("whatsapp_opt_in"))
    if lead.referral_code:
        svc.link_referral(db, lead)
    log_action(db, user, "update", "leads", entity=lead, description=f"Lead {lead.lead_code} updated", before=before, after=snapshot(lead), request=request)
    db.commit()
    return redirect(f"/crm/leads/{lead.id}", "Lead updated.")


@router.post("/leads/{id}/stage", include_in_schema=False)
async def lead_stage(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.update"))):
    lead = _lead(db, id, user)
    form = await request.form()
    stage = form.get("stage") or ""
    reason = form.get("reason") or form.get("lost_reason") or ""
    try:
        svc.move_stage(db, lead, stage, user, reason or None, request=request)
    except ValueError as exc:
        return redirect(f"/crm/leads/{lead.id}", str(exc), "error")
    if stage == "trial_done":
        svc.enroll_sequence(db, "trial_follow_up", "lead", lead.id, enrolled_by=user.email)
    db.commit()
    nxt = form.get("next") or f"/crm/leads/{lead.id}"
    return redirect(nxt, f"{lead.lead_code} moved to {stage.replace('_', ' ')}.")


@router.post("/leads/{id}/assign", include_in_schema=False)
async def lead_assign(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.assign", "leads.update", any_of=True))):
    lead = _lead(db, id, user)
    form = await request.form()
    if form.get("mode") == "round_robin":
        target = svc.round_robin_assign(db, lead, user)
        db.commit()
        return redirect(f"/crm/leads/{lead.id}", f"Round-robin assigned to {target.full_name}." if target else "No lead closers available.", "success" if target else "warning")
    uid = parse_int(form.get("assigned_to_id"))
    target = db.get(User, uid) if uid else None
    svc.assign_lead(db, lead, target, user)
    db.commit()
    return redirect(f"/crm/leads/{lead.id}", f"Lead assigned to {target.full_name}." if target else "Lead unassigned.")


@router.post("/leads/{id}/activity", include_in_schema=False)
async def lead_activity(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.update"))):
    lead = _lead(db, id, user)
    form = await request.form()
    note = (form.get("note") or "").strip()
    if not note:
        return redirect(f"/crm/leads/{lead.id}", "The note cannot be empty.", "error")
    kind = form.get("activity_type") or "note"
    svc.add_activity(db, lead, kind, note, user)
    if form.get("next_follow_up"):
        lead.next_follow_up = parse_datetime(form.get("next_follow_up"))
    log_action(db, user, "update", "leads", entity=lead, description=f"{kind.title()} logged on {lead.lead_code}")
    db.commit()
    return redirect(f"/crm/leads/{lead.id}", "Activity logged.")


@router.post("/leads/{id}/follow-up", include_in_schema=False)
async def lead_follow_up(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.update"))):
    lead = _lead(db, id, user)
    form = await request.form()
    lead.next_follow_up = parse_datetime(form.get("next_follow_up"))
    log_action(db, user, "update", "leads", entity=lead, description=f"Next follow-up set for {lead.lead_code}")
    db.commit()
    return redirect(f"/crm/leads/{lead.id}", "Next follow-up saved.")


@router.post("/leads/{id}/rescore", include_in_schema=False)
async def lead_rescore(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.update"))):
    lead = _lead(db, id, user)
    result = svc.score_lead(db, lead)
    log_action(db, user, "score", "leads", entity=lead, description=f"AI rescored {lead.lead_code} to {lead.score}", after={"score": lead.score})
    db.commit()
    return redirect(f"/crm/leads/{lead.id}", f"AI score refreshed: {lead.score}/100 - {result.get('recommendation')}.")


@router.post("/leads/{id}/sync-ghl", include_in_schema=False)
async def lead_sync_ghl(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.update"))):
    lead = _lead(db, id, user)
    svc.sync_ghl(db, lead, user)
    db.commit()
    return redirect(f"/crm/leads/{lead.id}", f"Synced to GoHighLevel (contact {lead.ghl_contact_id}).")


@router.post("/leads/{id}/enroll", include_in_schema=False)
async def lead_enroll(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.update"))):
    lead = _lead(db, id, user)
    form = await request.form()
    stype = form.get("sequence_type") or "lead_follow_up"
    e = svc.enroll_sequence(db, stype, "lead", lead.id, enrolled_by=user.email)
    db.commit()
    return redirect(f"/crm/leads/{lead.id}", f"Enrolled in the {stype.replace('_', ' ')} sequence." if e else "No active sequence of that type.", "success" if e else "warning")


@router.post("/leads/{id}/schedule-trial", include_in_schema=False)
async def lead_schedule_trial(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("trials.add", "leads.update", any_of=True))):
    lead = _lead(db, id, user)
    form = await request.form()
    when = parse_datetime(form.get("scheduled_at"))
    if not when:
        return redirect(f"/crm/leads/{lead.id}", "A trial date and time is required.", "error")
    teacher = db.get(Teacher, parse_int(form.get("teacher_id"))) if form.get("teacher_id") else None
    course = db.get(Course, lead.course_interest_id) if lead.course_interest_id else None
    tr = svc.create_trial(db, lead.student_name or lead.full_name, lead=lead, teacher=teacher, course=course, scheduled_at=when, actor=user, request=request)
    if lead.stage in ("new", "contacted"):
        svc.move_stage(db, lead, "trial_scheduled", user, "Trial booked", request=request)
    db.commit()
    return redirect(f"/trials/{tr.id}", f"Trial scheduled for {when.strftime('%d %b %Y %H:%M')}.")


@router.post("/leads/{id}/convert", include_in_schema=False)
async def lead_convert(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.add", "leads.update", any_of=True))):
    lead = _lead(db, id, user)
    form = await request.form()
    if lead.converted_client_id:
        return redirect(f"/clients/{lead.converted_client_id}", "This lead has already been converted.", "warning")
    client, pwd = svc.convert_lead_to_client(db, lead, user, {"email": form.get("email"), "relationship": form.get("relationship")}, request=request)
    db.commit()
    msg = f"Client {client.client_code} created from {lead.lead_code}."
    if pwd:
        msg += f" Portal login: {client.user.email if client.user else client.email} / {pwd}"
    return redirect(f"/clients/{client.id}", msg)


# ============================================================================= CAMPAIGNS
@router.get("/campaigns", include_in_schema=False)
def campaigns(request: Request, page: int = 1, q: str = "", platform: str = "", status: str = "",
              db: Session = Depends(get_db), user: User = Depends(require("campaigns.view"))):
    query = db.query(Campaign)
    if q:
        query = query.filter(or_(Campaign.name.ilike(f"%{q}%"), Campaign.utm_campaign.ilike(f"%{q}%"), Campaign.offer.ilike(f"%{q}%")))
    if platform:
        query = query.filter(Campaign.platform == platform)
    if status:
        query = query.filter(Campaign.status == status)
    pg = paginate(query.order_by(Campaign.id.desc()), page, 25)
    totals = {c.id: svc.campaign_totals(db, c) for c in pg.items}
    agg = {"spend": round(sum(t["spend"] for t in totals.values()), 2), "leads": sum(t["leads"] for t in totals.values()),
           "won": sum(t["conversions"] for t in totals.values()), "revenue": round(sum(t["revenue"] for t in totals.values()), 2)}
    agg["cpl"] = round(agg["spend"] / agg["leads"], 2) if agg["leads"] else 0.0
    agg["cac"] = round(agg["spend"] / agg["won"], 2) if agg["won"] else 0.0
    return render(request, "crm/campaigns.html", {"user": user, "page": pg, "q": q, "platform": platform, "status": status, "totals": totals,
                                                  "agg": agg, "platforms": PLATFORMS, "statuses": CAMPAIGN_STATUSES,
                                                  "base_url": f"/crm/campaigns?q={q}&platform={platform}&status={status}"})


@router.get("/campaigns/new", include_in_schema=False)
def new_campaign(request: Request, db: Session = Depends(get_db), user: User = Depends(require("campaigns.add"))):
    return render(request, "crm/campaign_form.html", {"user": user, "mode": "new", "c": None, "platforms": PLATFORMS,
                                                      "statuses": CAMPAIGN_STATUSES, "countries": list(svc.COUNTRY_CURRENCY.keys())})


def _campaign_from_form(form) -> dict:
    return {"name": (form.get("name") or "").strip(), "platform": form.get("platform") or "meta", "objective": form.get("objective") or None,
            "budget": parse_float(form.get("budget")), "spend": parse_float(form.get("spend")), "currency": (form.get("currency") or "USD")[:3].upper(),
            "country": form.get("country") or None, "offer": form.get("offer") or None, "utm_source": form.get("utm_source") or None,
            "utm_campaign": form.get("utm_campaign") or None, "start_date": parse_date(form.get("start_date")),
            "end_date": parse_date(form.get("end_date")), "status": form.get("status") or "active", "external_id": form.get("external_id") or None}


@router.post("/campaigns/new", include_in_schema=False)
async def create_campaign(request: Request, db: Session = Depends(get_db), user: User = Depends(require("campaigns.add"))):
    form = await request.form()
    data = _campaign_from_form(form)
    if not data["name"]:
        return redirect("/crm/campaigns/new", "A campaign name is required.", "error")
    c = Campaign(**data)
    db.add(c)
    db.flush()
    log_action(db, user, "create", "campaigns", entity=c, description=f"Campaign '{c.name}' created ({c.platform})", request=request)
    db.commit()
    return redirect(f"/crm/campaigns/{c.id}", f"Campaign '{c.name}' created.")


@router.get("/campaigns/{id}", include_in_schema=False)
def campaign_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("campaigns.view"))):
    c = db.get(Campaign, id)
    if not c:
        raise HTTPException(404, "Campaign not found")
    metrics = db.query(CampaignMetric).filter(CampaignMetric.campaign_id == c.id).order_by(CampaignMetric.date.desc()).limit(90).all()
    chrono = list(reversed(metrics))
    leads = db.query(Lead).filter(Lead.campaign_id == c.id).order_by(Lead.created_at.desc()).limit(100).all()
    return render(request, "crm/campaign_detail.html", {"user": user, "c": c, "metrics": metrics, "leads": leads,
                                                        "totals": svc.campaign_totals(db, c),
                                                        "labels": [m.date.strftime("%d %b") for m in chrono],
                                                        "spend_series": [float(m.spend or 0) for m in chrono],
                                                        "leads_series": [m.leads or 0 for m in chrono],
                                                        "platforms": PLATFORMS, "statuses": CAMPAIGN_STATUSES,
                                                        "countries": list(svc.COUNTRY_CURRENCY.keys())})


@router.post("/campaigns/{id}/edit", include_in_schema=False)
async def update_campaign(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("campaigns.update"))):
    c = db.get(Campaign, id)
    if not c:
        raise HTTPException(404, "Campaign not found")
    form = await request.form()
    before = snapshot(c)
    for k, v in _campaign_from_form(form).items():
        if k == "name" and not v:
            continue
        setattr(c, k, v)
    log_action(db, user, "update", "campaigns", entity=c, description=f"Campaign '{c.name}' updated", before=before, after=snapshot(c), request=request)
    db.commit()
    return redirect(f"/crm/campaigns/{c.id}", "Campaign updated.")


@router.post("/campaigns/{id}/metrics", include_in_schema=False)
async def campaign_metrics(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("campaigns.update"))):
    c = db.get(Campaign, id)
    if not c:
        raise HTTPException(404, "Campaign not found")
    form = await request.form()
    d = parse_date(form.get("date")) or date.today()
    m = db.query(CampaignMetric).filter(CampaignMetric.campaign_id == c.id, CampaignMetric.date == d).first()
    if not m:
        m = CampaignMetric(campaign_id=c.id, date=d)
        db.add(m)
    m.impressions = parse_int(form.get("impressions"), 0) or 0
    m.clicks = parse_int(form.get("clicks"), 0) or 0
    m.leads = parse_int(form.get("leads"), 0) or 0
    m.spend = parse_float(form.get("spend"))
    m.conversions = parse_int(form.get("conversions"), 0) or 0
    m.revenue = parse_float(form.get("revenue"))
    db.flush()
    total = db.query(func.coalesce(func.sum(CampaignMetric.spend), 0)).filter(CampaignMetric.campaign_id == c.id).scalar()
    c.spend = float(total or 0)
    log_action(db, user, "update", "campaigns", entity=c, description=f"Daily metrics recorded for {d.isoformat()}", after={"date": d.isoformat()}, request=request)
    db.commit()
    return redirect(f"/crm/campaigns/{c.id}", f"Metrics saved for {d.strftime('%d %b %Y')}.")


# ============================================================================= MARKETING ANALYTICS (Module 34)
def _range(start: str, end: str) -> tuple[date, date]:
    e = parse_date(end) or date.today()
    s = parse_date(start) or (e - timedelta(days=89))
    return s, e


@router.get("/marketing", include_in_schema=False)
def marketing(request: Request, start: str = "", end: str = "", db: Session = Depends(get_db), user: User = Depends(require("marketing.view"))):
    s, e = _range(start, end)
    data = svc.marketing_dashboard(db, s, e)
    return render(request, "crm/marketing.html", {"user": user, "d": data, "start": s.isoformat(), "end": e.isoformat()})


@router.get("/marketing/export", include_in_schema=False)
def marketing_export(request: Request, start: str = "", end: str = "", dim: str = "source",
                     db: Session = Depends(get_db), user: User = Depends(require("marketing.view"))):
    s, e = _range(start, end)
    data = svc.marketing_dashboard(db, s, e)
    rows = data.get(f"by_{dim}") or data["by_source"]
    log_action(db, user, "export", "marketing", description=f"Marketing attribution export by {dim} ({s} to {e})", request=request)
    db.commit()
    return _csv_response(f"marketing_{dim}_{s}_{e}.csv",
                         [dim.title(), "Leads", "Contacted", "Trials", "Won", "Spend", "Revenue", "CPL", "CAC", "Conversion %", "Trial conv %", "ROAS"],
                         [[r["key"], r["leads"], r["contacted"], r["trials"], r["won"], r["spend"], r["revenue"], r["cpl"], r["cac"],
                           r["conversion"], r["trial_conversion"], r["roas"]] for r in rows])


# ============================================================================= WHATSAPP INBOX (Section 7)
def _integration_health(db: Session) -> Integration:
    from app.services.integrations import get_integration
    return get_integration(db, "whatsapp")


@router.get("/inbox/failed", include_in_schema=False)
def inbox_failed(request: Request, db: Session = Depends(get_db), user: User = Depends(require("inbox.view"))):
    msgs = db.query(Message).filter(Message.status == "failed").order_by(Message.created_at.desc()).limit(200).all()
    return render(request, "crm/inbox_failed.html", {"user": user, "messages": msgs, "integration": _integration_health(db)})


@router.post("/inbox/failed/{mid}/retry", include_in_schema=False)
async def inbox_retry(mid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("inbox.update"))):
    msg = db.get(Message, mid)
    if not msg:
        raise HTTPException(404, "Message not found")
    svc.retry_message(db, msg, user)
    db.commit()
    return redirect("/crm/inbox/failed", f"Retry {'succeeded' if msg.status == 'sent' else 'failed again'}.",
                    "success" if msg.status == "sent" else "error")


@router.get("/inbox/templates", include_in_schema=False)
def inbox_templates(request: Request, db: Session = Depends(get_db), user: User = Depends(require("inbox.view"))):
    return render(request, "crm/inbox_templates.html", {"user": user, "templates": db.query(MessageTemplate).order_by(MessageTemplate.category, MessageTemplate.name).all(),
                                                        "categories": TEMPLATE_CATEGORIES})


@router.post("/inbox/templates/new", include_in_schema=False)
async def create_template(request: Request, db: Session = Depends(get_db), user: User = Depends(require("inbox.update"))):
    form = await request.form()
    name = (form.get("name") or "").strip()
    body = (form.get("body") or "").strip()
    if not name or not body:
        return redirect("/crm/inbox/templates", "Name and body are required.", "error")
    if db.query(MessageTemplate).filter(MessageTemplate.name == name).first():
        return redirect("/crm/inbox/templates", "A template with that name already exists.", "error")
    t = MessageTemplate(name=name, body=body, category=form.get("category") or "follow_up", language=form.get("language") or "en",
                        channel="whatsapp", is_approved=parse_bool(form.get("is_approved")))
    db.add(t)
    db.flush()
    log_action(db, user, "create", "inbox", entity=t, description=f"Message template '{t.name}' created", request=request)
    db.commit()
    return redirect("/crm/inbox/templates", f"Template '{name}' created.")


@router.post("/inbox/templates/{tid}/edit", include_in_schema=False)
async def edit_template(tid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("inbox.update"))):
    t = db.get(MessageTemplate, tid)
    if not t:
        raise HTTPException(404, "Template not found")
    form = await request.form()
    before = snapshot(t)
    t.body = (form.get("body") or t.body).strip()
    t.category = form.get("category") or t.category
    t.language = form.get("language") or t.language
    t.is_approved = parse_bool(form.get("is_approved"))
    log_action(db, user, "update", "inbox", entity=t, description=f"Message template '{t.name}' updated", before=before, after=snapshot(t), request=request)
    db.commit()
    return redirect("/crm/inbox/templates", "Template updated.")


@router.post("/inbox/templates/{tid}/delete", include_in_schema=False)
async def delete_template(tid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("inbox.delete", "inbox.update", any_of=True))):
    t = db.get(MessageTemplate, tid)
    if not t:
        raise HTTPException(404, "Template not found")
    log_action(db, user, "delete", "inbox", entity=t, description=f"Message template '{t.name}' deleted", before=snapshot(t), request=request)
    db.delete(t)
    db.commit()
    return redirect("/crm/inbox/templates", "Template deleted.", "warning")


@router.get("/inbox", include_in_schema=False)
def inbox(request: Request, c: int = 0, status: str = "", owner: str = "", tag: str = "", q: str = "",
          db: Session = Depends(get_db), user: User = Depends(require("inbox.view"))):
    query = db.query(Conversation)
    if status:
        query = query.filter(Conversation.status == status)
    if owner == "mine":
        query = query.filter(Conversation.assigned_to_id == user.id)
    elif owner == "unassigned":
        query = query.filter(Conversation.assigned_to_id.is_(None))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Conversation.contact_name.ilike(like), Conversation.contact_phone.ilike(like), Conversation.last_message_preview.ilike(like)))
    convs = query.order_by(Conversation.last_message_at.desc(), Conversation.id.desc()).limit(200).all()
    if tag:
        convs = [x for x in convs if tag in (x.tags or [])]
    current = db.get(Conversation, c) if c else (convs[0] if convs else None)
    agents = (db.query(User).join(Role, Role.id == User.role_id)
              .filter(Role.slug.in_(["lead_closer", "lead_generator", "billing_rep", "hod_marketing", "supervisor", "manager"]),
                      User.is_active.is_(True)).order_by(User.full_name).all())
    ctx = {"user": user, "conversations": convs, "current": current, "status": status, "owner": owner, "tag": tag, "q": q,
           "statuses": CONV_STATUSES, "integration": _integration_health(db),
           "templates": db.query(MessageTemplate).filter(MessageTemplate.is_approved.is_(True)).order_by(MessageTemplate.name).all(),
           "agents": agents, "agent_options": _opts(agents, "full_name"),
           "all_tags": sorted({t for x in convs for t in (x.tags or [])}),
           "counts": {s: db.query(func.count(Conversation.id)).filter(Conversation.status == s).scalar() or 0 for s in CONV_STATUSES},
           "failed_count": db.query(func.count(Message.id)).filter(Message.status == "failed").scalar() or 0}
    if current:
        current.unread_count = 0
        db.commit()
        ctype, contact = svc.conversation_contact(db, current)
        ctx.update({"messages": current.messages, "notes": db.query(InternalNote).filter(InternalNote.conversation_id == current.id).order_by(InternalNote.created_at.desc()).all(),
                    "contact_type": ctype, "contact": contact, "opted_in": svc.opted_in(db, current)})
    return render(request, "crm/inbox.html", ctx)


def _conv(db: Session, cid: int) -> Conversation:
    conv = db.get(Conversation, cid)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return conv


@router.post("/inbox/{cid}/send", include_in_schema=False)
async def inbox_send(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("inbox.add", "inbox.update", any_of=True))):
    conv = _conv(db, cid)
    form = await request.form()
    body = (form.get("body") or "").strip()
    tname = form.get("template_name") or None
    if tname and not body:
        body = svc.render_template_body(svc.template_body(db, tname, ""), svc.message_context(db, conv))
    if not body:
        return redirect(f"/crm/inbox?c={conv.id}", "Message body is empty.", "error")
    msg = svc.send_message(db, conv, body, user, template_name=tname, message_type="template" if tname else "text")
    log_action(db, user, "send", "inbox", entity=conv, description=f"WhatsApp message to {conv.contact_name} ({msg.status})")
    db.commit()
    return redirect(f"/crm/inbox?c={conv.id}", "Message sent." if msg.status != "failed" else f"Message failed: {msg.error}",
                    "success" if msg.status != "failed" else "error")


@router.post("/inbox/{cid}/simulate", include_in_schema=False)
async def inbox_simulate(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("inbox.update"))):
    conv = _conv(db, cid)
    form = await request.form()
    body = (form.get("body") or "").strip() or "JazakAllah Khair, that works for us."
    svc.receive_message(db, conv.contact_phone or "", body, conv=conv)
    log_action(db, user, "simulate", "inbox", entity=conv, description="Simulated inbound WhatsApp reply (dev tool)")
    db.commit()
    return redirect(f"/crm/inbox?c={conv.id}", "Inbound reply simulated; active sequences stopped.")


@router.post("/inbox/{cid}/status", include_in_schema=False)
async def inbox_status(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("inbox.update"))):
    conv = _conv(db, cid)
    form = await request.form()
    before = conv.status
    conv.status = form.get("status") or conv.status
    log_action(db, user, "status_change", "inbox", entity=conv, description=f"Conversation {before} -> {conv.status}", before={"status": before},
               after={"status": conv.status}, request=request)
    db.commit()
    return redirect(f"/crm/inbox?c={conv.id}", f"Conversation marked {conv.status}.")


@router.post("/inbox/{cid}/assign", include_in_schema=False)
async def inbox_assign(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("inbox.assign", "inbox.update", any_of=True))):
    conv = _conv(db, cid)
    form = await request.form()
    uid = parse_int(form.get("assigned_to_id"))
    conv.assigned_to_id = uid
    if uid:
        from app.core.notify import notify
        notify(db, uid, f"Conversation assigned: {conv.contact_name}", conv.last_message_preview or "", event_type="inbox_assigned", link=f"/crm/inbox?c={conv.id}")
    log_action(db, user, "assign", "inbox", entity=conv, description=f"Conversation assigned to user {uid or 'nobody'}", request=request)
    db.commit()
    return redirect(f"/crm/inbox?c={conv.id}", "Conversation assigned.")


@router.post("/inbox/{cid}/tags", include_in_schema=False)
async def inbox_tags(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("inbox.update"))):
    conv = _conv(db, cid)
    form = await request.form()
    tags = [t.strip().lower() for t in (form.get("tags") or "").split(",") if t.strip()]
    before = conv.tags
    conv.tags = tags
    log_action(db, user, "update", "inbox", entity=conv, description="Conversation tags updated", before={"tags": before}, after={"tags": tags})
    db.commit()
    return redirect(f"/crm/inbox?c={conv.id}", "Tags updated.")


@router.post("/inbox/{cid}/note", include_in_schema=False)
async def inbox_note(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("inbox.update"))):
    conv = _conv(db, cid)
    form = await request.form()
    text = (form.get("text") or "").strip()
    if not text:
        return redirect(f"/crm/inbox?c={conv.id}", "The internal note cannot be empty.", "error")
    db.add(InternalNote(conversation_id=conv.id, user_id=user.id, text=text))
    log_action(db, user, "create", "inbox", entity=conv, description="Internal note added to conversation")
    db.commit()
    return redirect(f"/crm/inbox?c={conv.id}", "Internal note added (not visible to the contact).")


@router.post("/inbox/{cid}/opt-in", include_in_schema=False)
async def inbox_opt_in(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("inbox.update"))):
    conv = _conv(db, cid)
    form = await request.form()
    opted = parse_bool(form.get("opted_in"))
    svc.set_opt_in(db, conv, opted, user)
    db.commit()
    return redirect(f"/crm/inbox?c={conv.id}", f"Contact opted {'in to' if opted else 'out of'} WhatsApp messages.",
                    "success" if opted else "warning")


# ============================================================================= SEQUENCES
@router.get("/sequences", include_in_schema=False)
def sequences(request: Request, db: Session = Depends(get_db), user: User = Depends(require("sequences.view"))):
    seqs = db.query(Sequence).order_by(Sequence.sequence_type, Sequence.name).all()
    counts = dict(db.query(SequenceEnrollment.sequence_id, func.count(SequenceEnrollment.id)).group_by(SequenceEnrollment.sequence_id).all())
    active = dict(db.query(SequenceEnrollment.sequence_id, func.count(SequenceEnrollment.id)).filter(SequenceEnrollment.status == "active")
                  .group_by(SequenceEnrollment.sequence_id).all())
    status_counts = dict(db.query(SequenceEnrollment.status, func.count(SequenceEnrollment.id)).group_by(SequenceEnrollment.status).all())
    return render(request, "crm/sequences.html", {"user": user, "sequences": seqs, "counts": counts, "active": active,
                                                  "status_counts": status_counts, "types": svc.SEQUENCE_TYPES})


@router.post("/sequences/new", include_in_schema=False)
async def create_sequence(request: Request, db: Session = Depends(get_db), user: User = Depends(require("sequences.add"))):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return redirect("/crm/sequences", "A sequence name is required.", "error")
    try:
        steps = json.loads(form.get("steps") or "[]")
        assert isinstance(steps, list)
    except Exception:
        return redirect("/crm/sequences", "Steps must be a JSON array, e.g. [{\"day\":0,\"template\":\"lead_follow_up\"}].", "error")
    s = Sequence(name=name, sequence_type=form.get("sequence_type") or "lead_follow_up", channel="whatsapp", steps=steps,
                 is_active=parse_bool(form.get("is_active")))
    db.add(s)
    db.flush()
    log_action(db, user, "create", "sequences", entity=s, description=f"Sequence '{s.name}' created with {len(steps)} steps", request=request)
    db.commit()
    return redirect(f"/crm/sequences/{s.id}", f"Sequence '{name}' created.")


@router.post("/sequences/run", include_in_schema=False)
async def run_sequences_now(request: Request, db: Session = Depends(get_db), user: User = Depends(require("sequences.execute", "sequences.update", any_of=True))):
    result = svc.run_sequences(db)
    log_action(db, user, "execute", "sequences", description=f"Sequence runner executed manually: {result}", request=request)
    db.commit()
    return redirect("/crm/sequences", f"Runner finished: {result['sent']} sent, {result['completed']} completed, {result['stopped']} stopped.")


@router.post("/sequences/enrollments/{eid}/stop", include_in_schema=False)
async def stop_enrollment(eid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("sequences.update"))):
    e = db.get(SequenceEnrollment, eid)
    if not e:
        raise HTTPException(404, "Enrollment not found")
    form = await request.form()
    e.status = "stopped"
    e.stop_reason = form.get("reason") or "Stopped manually"
    e.next_run_at = None
    log_action(db, user, "update", "sequences", entity=e, description=f"Enrollment {e.id} stopped", rationale=e.stop_reason, request=request)
    db.commit()
    return redirect(form.get("next") or f"/crm/sequences/{e.sequence_id}", "Enrollment stopped.", "warning")


@router.get("/sequences/{id}", include_in_schema=False)
def sequence_detail(id: int, request: Request, status: str = "", page: int = 1, db: Session = Depends(get_db), user: User = Depends(require("sequences.view"))):
    s = db.get(Sequence, id)
    if not s:
        raise HTTPException(404, "Sequence not found")
    q = db.query(SequenceEnrollment).filter(SequenceEnrollment.sequence_id == s.id)
    if status:
        q = q.filter(SequenceEnrollment.status == status)
    pg = paginate(q.order_by(SequenceEnrollment.id.desc()), page, 30)
    contacts = {}
    for e in pg.items:
        ctype, contact = svc.resolve_enrollment_contact(db, e)
        contacts[e.id] = contact
    return render(request, "crm/sequence_detail.html", {"user": user, "s": s, "page": pg, "status": status, "contacts": contacts,
                                                        "steps_json": json.dumps(s.steps or [], indent=2), "types": svc.SEQUENCE_TYPES,
                                                        "leads": db.query(Lead).filter(Lead.stage.notin_(["won", "lost"])).order_by(Lead.created_at.desc()).limit(200).all(),
                                                        "students": db.query(Student).filter(Student.status.in_(["active", "trial", "frozen"])).order_by(Student.full_name).limit(300).all(),
                                                        "base_url": f"/crm/sequences/{s.id}?status={status}"})


@router.post("/sequences/{id}/edit", include_in_schema=False)
async def edit_sequence(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("sequences.update"))):
    s = db.get(Sequence, id)
    if not s:
        raise HTTPException(404, "Sequence not found")
    form = await request.form()
    before = snapshot(s)
    try:
        steps = json.loads(form.get("steps") or "[]")
        assert isinstance(steps, list)
    except Exception:
        return redirect(f"/crm/sequences/{s.id}", "Steps must be a valid JSON array.", "error")
    s.name = (form.get("name") or s.name).strip()
    s.sequence_type = form.get("sequence_type") or s.sequence_type
    s.steps = steps
    s.is_active = parse_bool(form.get("is_active"))
    log_action(db, user, "update", "sequences", entity=s, description=f"Sequence '{s.name}' updated ({len(steps)} steps)", before=before,
               after=snapshot(s), request=request)
    db.commit()
    return redirect(f"/crm/sequences/{s.id}", "Sequence saved.")


@router.post("/sequences/{id}/enroll", include_in_schema=False)
async def enroll_in_sequence(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("sequences.update"))):
    s = db.get(Sequence, id)
    if not s:
        raise HTTPException(404, "Sequence not found")
    form = await request.form()
    ctype = form.get("contact_type") or "lead"
    cid = parse_int(form.get("contact_id"))
    if not cid:
        return redirect(f"/crm/sequences/{s.id}", "Select a contact to enroll.", "error")
    e = svc.enroll_sequence(db, s.sequence_type, ctype, cid, enrolled_by=user.email, sequence=s)
    log_action(db, user, "create", "sequences", entity=e, description=f"Manual enrollment of {ctype} {cid} in '{s.name}'", request=request)
    db.commit()
    return redirect(f"/crm/sequences/{s.id}", "Contact enrolled." if e else "Could not enroll.", "success" if e else "error")


# ============================================================================= REFERRALS / AMBASSADORS (Module 43)
@router.get("/referrals", include_in_schema=False)
def referrals(request: Request, status: str = "", page: int = 1, db: Session = Depends(get_db), user: User = Depends(require("referrals.view"))):
    data = svc.referral_dashboard(db)
    q = db.query(Referral)
    if status:
        q = q.filter(Referral.status == status)
    pg = paginate(q.order_by(Referral.created_at.desc()), page, 25)
    return render(request, "crm/referrals.html", {"user": user, "d": data, "page": pg, "status": status, "statuses": svc.REFERRAL_STATUSES,
                                                  "base_url": f"/crm/referrals?status={status}"})


@router.get("/referrals/ambassadors", include_in_schema=False)
def ambassadors(request: Request, page: int = 1, q: str = "", eligible: str = "", db: Session = Depends(get_db),
                user: User = Depends(require("referrals.view"))):
    query = db.query(Client).filter(Client.status.in_(["active", "trial"]))
    if q:
        query = query.filter(or_(Client.full_name.ilike(f"%{q}%"), Client.client_code.ilike(f"%{q}%"), Client.referral_code.ilike(f"%{q}%")))
    if eligible == "ambassadors":
        query = query.filter(Client.is_ambassador.is_(True))
    pg = paginate(query.order_by(Client.is_ambassador.desc(), Client.joined_at), page, 25)
    elig = {c.id: svc.ambassador_eligibility(db, c) for c in pg.items}
    counts = dict(db.query(Referral.ambassador_client_id, func.count(Referral.id)).group_by(Referral.ambassador_client_id).all())
    credited = dict(db.query(Referral.ambassador_client_id, func.count(Referral.id)).filter(Referral.status == "credited")
                    .group_by(Referral.ambassador_client_id).all())
    return render(request, "crm/referral_ambassadors.html", {"user": user, "page": pg, "q": q, "eligible": eligible, "elig": elig,
                                                             "counts": counts, "credited": credited,
                                                             "base_url": f"/crm/referrals/ambassadors?q={q}&eligible={eligible}"})


@router.post("/referrals/invite/{client_id}", include_in_schema=False)
async def invite_ambassador(client_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("referrals.update", "referrals.add", any_of=True))):
    c = db.get(Client, client_id)
    if not c:
        raise HTTPException(404, "Client not found")
    form = await request.form()
    force = parse_bool(form.get("force"))
    rationale = form.get("rationale") or form.get("reason")
    if force and not (rationale or "").strip():
        return redirect("/crm/referrals/ambassadors", "A rationale is required to override the ambassador eligibility gate.", "error")
    ok, elig = svc.invite_ambassador(db, c, user, force=force, rationale=rationale, request=request)
    db.commit()
    if not ok:
        return redirect("/crm/referrals/ambassadors", f"{c.full_name} is not eligible yet: " + "; ".join(elig["reasons"]), "warning")
    return redirect("/crm/referrals/ambassadors", f"{c.full_name} invited as an ambassador (code {c.referral_code}).")


@router.post("/referrals/{rid}/qualify", include_in_schema=False)
async def qualify_referral(rid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("referrals.approve", "referrals.update", any_of=True))):
    ref = db.get(Referral, rid)
    if not ref:
        raise HTTPException(404, "Referral not found")
    form = await request.form()
    try:
        svc.qualify_referral(db, ref, user, rationale=form.get("rationale") or form.get("reason"), request=request)
    except ValueError as exc:
        db.rollback()
        return redirect("/crm/referrals", str(exc), "error")
    db.commit()
    return redirect("/crm/referrals",
                    f"Referral credited: {ref.credit_currency} {float(ref.credit_amount):.2f} account credit posted to BOTH families.")


@router.post("/referrals/{rid}/status", include_in_schema=False)
async def referral_status(rid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("referrals.update"))):
    ref = db.get(Referral, rid)
    if not ref:
        raise HTTPException(404, "Referral not found")
    form = await request.form()
    new = form.get("status") or ref.status
    if new not in svc.REFERRAL_STATUSES:
        return redirect("/crm/referrals", "Invalid referral status.", "error")
    before = ref.status
    ref.status = new
    if form.get("referred_name"):
        ref.referred_name = form.get("referred_name")
    if form.get("notes"):
        ref.notes = form.get("notes")
    log_action(db, user, "status_change", "referrals", entity=ref, description=f"Referral {ref.id} {before} -> {new}",
               before={"status": before}, after={"status": new}, request=request)
    db.commit()
    return redirect("/crm/referrals", f"Referral moved to {new.replace('_', ' ')}.")


@router.post("/referrals/new", include_in_schema=False)
async def new_referral(request: Request, db: Session = Depends(get_db), user: User = Depends(require("referrals.add"))):
    form = await request.form()
    cid = parse_int(form.get("ambassador_client_id"))
    c = db.get(Client, cid) if cid else None
    if not c:
        return redirect("/crm/referrals", "Select an ambassador family.", "error")
    svc.ensure_referral_code(c)
    sup = svc.user_by_email(db, "supervisor@oqc.local")
    ref = Referral(ambassador_client_id=c.id, referral_code=c.referral_code, referred_name=form.get("referred_name") or None,
                   referred_phone=form.get("referred_phone") or None, status="ask", invited_at=datetime.utcnow(),
                   owner_id=sup.id if sup else None, notes=form.get("notes") or None)
    db.add(ref)
    db.flush()
    log_action(db, user, "create", "referrals", entity=ref, description=f"Referral ask logged for {c.client_code}", request=request)
    db.commit()
    return redirect("/crm/referrals", "Referral ask recorded.")
