"""REST API for CRM & Growth: leads, trials, cases, feedback, referrals, retention, marketing analytics."""
from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core.deps import require, get_current_user
from app.core.utils import parse_date
from app.database import get_db
from app.models.core import User
from app.models.crm import (LEAD_STAGES, Lead, LeadSource, Campaign, Conversation, Message, Sequence, SequenceEnrollment,
                            Referral, Survey, Feedback, Case, RetentionAction)
from app.models.people import Client, Student
from app.models.scheduling import Trial
from app.services import crm as svc
from app.services import retention as retention_svc

router = APIRouter(prefix="/crm", tags=["crm"])


# ============================================================================= schemas
class LeadOut(BaseModel):
    id: int
    lead_code: str
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    stage: str
    score: int
    source: Optional[str] = None
    campaign: Optional[str] = None
    assigned_to: Optional[str] = None
    student_name: Optional[str] = None
    students_count: int = 1
    next_follow_up: Optional[datetime] = None
    converted_client_id: Optional[int] = None
    is_duplicate_of_id: Optional[int] = None
    created_at: Optional[datetime] = None


class LeadIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    email: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    country: Optional[str] = None
    student_name: Optional[str] = None
    student_age: Optional[int] = None
    students_count: int = 1
    preferred_time: Optional[str] = None
    source: Optional[str] = None
    campaign_id: Optional[int] = None
    referral_code: Optional[str] = None
    notes: Optional[str] = None


class LeadCreated(BaseModel):
    lead: LeadOut
    duplicates: list[str] = []


class StageIn(BaseModel):
    stage: str
    reason: Optional[str] = None


class CaseOut(BaseModel):
    id: int
    case_number: str
    case_type: str
    title: str
    category: Optional[str] = None
    priority: str
    status: str
    sla_due_at: Optional[datetime] = None
    sla_breached: bool = False
    assigned_to: Optional[str] = None
    client_id: Optional[int] = None
    created_at: Optional[datetime] = None


class CaseIn(BaseModel):
    case_type: str = "complaint"
    title: str = Field(min_length=3, max_length=200)
    description: Optional[str] = None
    client_id: Optional[int] = None
    student_id: Optional[int] = None
    priority: Optional[str] = None
    source: str = "portal"


class TrialOut(BaseModel):
    id: int
    student_name: str
    status: str
    scheduled_at: Optional[datetime] = None
    teacher: Optional[str] = None
    lead_id: Optional[int] = None
    client_id: Optional[int] = None
    outcome: Optional[str] = None


class ReferralOut(BaseModel):
    id: int
    referral_code: str
    status: str
    ambassador_client_id: int
    referred_name: Optional[str] = None
    referred_client_id: Optional[int] = None
    credit_amount: float = 0
    credit_currency: str = "GBP"


class RiskOut(BaseModel):
    student_id: int
    student_code: str
    full_name: str
    risk_score: float
    risk_level: str
    computed_at: Optional[datetime] = None


# ============================================================================= serializers
def _lead_out(l: Lead) -> LeadOut:
    return LeadOut(id=l.id, lead_code=l.lead_code, full_name=l.full_name, email=l.email, phone=l.phone, country=l.country, stage=l.stage,
                   score=l.score or 0, source=l.source.name if l.source else None, campaign=l.campaign.name if l.campaign else None,
                   assigned_to=l.assigned_to.full_name if l.assigned_to else None, student_name=l.student_name,
                   students_count=l.students_count or 1, next_follow_up=l.next_follow_up, converted_client_id=l.converted_client_id,
                   is_duplicate_of_id=l.is_duplicate_of_id, created_at=l.created_at)


def _case_out(c: Case) -> CaseOut:
    return CaseOut(id=c.id, case_number=c.case_number, case_type=c.case_type, title=c.title, category=c.category, priority=c.priority,
                   status=c.status, sla_due_at=c.sla_due_at, sla_breached=bool(c.sla_breached),
                   assigned_to=c.assigned_to.full_name if c.assigned_to else None, client_id=c.client_id, created_at=c.created_at)


# ============================================================================= leads
@router.get("/leads", response_model=list[LeadOut])
def api_leads(stage: str = "", q: str = "", limit: int = Query(50, le=200), offset: int = 0,
              db: Session = Depends(get_db), user: User = Depends(require("leads.view"))):
    query = db.query(Lead)
    if stage:
        query = query.filter(Lead.stage == stage)
    if q:
        query = query.filter(or_(Lead.full_name.ilike(f"%{q}%"), Lead.lead_code.ilike(f"%{q}%"), Lead.email.ilike(f"%{q}%"), Lead.phone.ilike(f"%{q}%")))
    return [_lead_out(l) for l in query.order_by(Lead.created_at.desc()).offset(offset).limit(limit).all()]


@router.get("/leads/stats")
def api_lead_stats(db: Session = Depends(get_db), user: User = Depends(require("leads.view"))):
    return svc.lead_stats(db, db.query(Lead))


@router.get("/leads/{lead_id}", response_model=LeadOut)
def api_lead(lead_id: int, db: Session = Depends(get_db), user: User = Depends(require("leads.view"))):
    l = db.get(Lead, lead_id)
    if not l:
        raise HTTPException(404, "Lead not found")
    return _lead_out(l)


@router.post("/leads", response_model=LeadCreated, status_code=201)
def api_create_lead(body: LeadIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.add"))):
    src = db.query(LeadSource).filter(LeadSource.name == body.source).first() if body.source else None
    data = body.model_dump()
    data.pop("source", None)
    data["source_id"] = src.id if src else None
    lead, dups = svc.create_lead(db, data, user, request=request)
    db.commit()
    return LeadCreated(lead=_lead_out(lead), duplicates=[d.lead_code for d in dups])


@router.post("/leads/{lead_id}/stage", response_model=LeadOut)
def api_lead_stage(lead_id: int, body: StageIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leads.update"))):
    l = db.get(Lead, lead_id)
    if not l:
        raise HTTPException(404, "Lead not found")
    try:
        svc.move_stage(db, l, body.stage, user, body.reason, request=request)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return _lead_out(l)


@router.post("/leads/{lead_id}/convert")
def api_convert_lead(lead_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clients.add"))):
    l = db.get(Lead, lead_id)
    if not l:
        raise HTTPException(404, "Lead not found")
    client, pwd = svc.convert_lead_to_client(db, l, user, request=request)
    db.commit()
    return {"client_id": client.id, "client_code": client.client_code, "temp_password": pwd,
            "students": [s.student_code for s in client.students]}


@router.get("/closers/kpis")
def api_closer_kpis(days: int = 90, db: Session = Depends(get_db), user: User = Depends(require("leads.view"))):
    rows = svc.closer_kpis(db, date.today() - timedelta(days=days))
    return [{**{k: v for k, v in r.items() if k != "user"}, "user": r["user"].full_name, "user_id": r["user"].id} for r in rows]


# ============================================================================= marketing / campaigns
@router.get("/marketing")
def api_marketing(start: str = "", end: str = "", db: Session = Depends(get_db), user: User = Depends(require("marketing.view"))):
    e = parse_date(end) or date.today()
    s = parse_date(start) or (e - timedelta(days=89))
    return svc.marketing_dashboard(db, s, e)


@router.get("/campaigns")
def api_campaigns(db: Session = Depends(get_db), user: User = Depends(require("campaigns.view"))):
    return [{"id": c.id, "name": c.name, "platform": c.platform, "status": c.status, "budget": float(c.budget or 0),
             "currency": c.currency, **svc.campaign_totals(db, c)} for c in db.query(Campaign).order_by(Campaign.id.desc()).limit(100).all()]


# ============================================================================= trials
@router.get("/trials", response_model=list[TrialOut])
def api_trials(status: str = "", limit: int = Query(50, le=200), db: Session = Depends(get_db), user: User = Depends(require("trials.view"))):
    q = db.query(Trial)
    if status:
        q = q.filter(Trial.status == status)
    return [TrialOut(id=t.id, student_name=t.student_name, status=t.status, scheduled_at=t.scheduled_at,
                     teacher=t.teacher.full_name if t.teacher else None, lead_id=t.lead_id, client_id=t.client_id, outcome=t.outcome)
            for t in q.order_by(Trial.id.desc()).limit(limit).all()]


@router.get("/trials/stats")
def api_trial_stats(db: Session = Depends(get_db), user: User = Depends(require("trials.view"))):
    return {**svc.trial_stats(db), "analytics": svc.trial_analytics(db)}


# ============================================================================= cases
@router.get("/cases", response_model=list[CaseOut])
def api_cases(status: str = "", priority: str = "", limit: int = Query(50, le=200), offset: int = 0,
              db: Session = Depends(get_db), user: User = Depends(require("cases.view"))):
    q = db.query(Case)
    if status:
        q = q.filter(Case.status == status)
    if priority:
        q = q.filter(Case.priority == priority)
    return [_case_out(c) for c in q.order_by(Case.created_at.desc()).offset(offset).limit(limit).all()]


@router.post("/cases", response_model=CaseOut, status_code=201)
def api_open_case(body: CaseIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("cases.add"))):
    client = db.get(Client, body.client_id) if body.client_id else None
    student = db.get(Student, body.student_id) if body.student_id else None
    case = svc.open_case(db, body.case_type, body.title, body.description, client=client, student=student, raised_by_user=user,
                         source=body.source, priority=body.priority, actor=user, request=request)
    db.commit()
    return _case_out(case)


@router.get("/cases/trends")
def api_case_trends(db: Session = Depends(get_db), user: User = Depends(require("cases.view"))):
    return svc.case_trends(db)


@router.get("/cases/{case_id}", response_model=CaseOut)
def api_case(case_id: int, db: Session = Depends(get_db), user: User = Depends(require("cases.view"))):
    c = db.get(Case, case_id)
    if not c:
        raise HTTPException(404, "Case not found")
    return _case_out(c)


# ============================================================================= feedback
@router.get("/feedback/summary")
def api_feedback_summary(period: str = "month", db: Session = Depends(get_db), user: User = Depends(require("feedback.view"))):
    return svc.feedback_summary(db, period)


@router.get("/feedback")
def api_feedback(limit: int = Query(50, le=200), db: Session = Depends(get_db), user: User = Depends(require("feedback.view"))):
    rows = db.query(Feedback).filter(Feedback.is_confidential.is_(False)).order_by(Feedback.created_at.desc()).limit(limit).all()
    return [{"id": f.id, "trigger": f.trigger, "nps": f.nps, "rating": f.rating, "sentiment": f.sentiment, "status": f.status,
             "is_negative": f.is_negative, "case_id": f.case_id, "comment": f.comment,
             "client": f.client.full_name if f.client else None, "submitted_at": f.submitted_at} for f in rows]


# ============================================================================= referrals
@router.get("/referrals", response_model=list[ReferralOut])
def api_referrals(status: str = "", limit: int = Query(50, le=200), db: Session = Depends(get_db), user: User = Depends(require("referrals.view"))):
    q = db.query(Referral)
    if status:
        q = q.filter(Referral.status == status)
    return [ReferralOut(id=r.id, referral_code=r.referral_code, status=r.status, ambassador_client_id=r.ambassador_client_id,
                        referred_name=r.referred_name, referred_client_id=r.referred_client_id,
                        credit_amount=float(r.credit_amount or 0), credit_currency=r.credit_currency)
            for r in q.order_by(Referral.id.desc()).limit(limit).all()]


@router.get("/referrals/dashboard")
def api_referral_dashboard(db: Session = Depends(get_db), user: User = Depends(require("referrals.view"))):
    return svc.referral_dashboard(db)


# ============================================================================= retention
@router.get("/retention/risk", response_model=list[RiskOut])
def api_risk(level: str = "", limit: int = Query(100, le=500), db: Session = Depends(get_db), user: User = Depends(require("retention.view"))):
    q = db.query(Student).filter(Student.status.in_(["active", "trial", "frozen"]))
    if level:
        q = q.filter(Student.risk_level == level)
    return [RiskOut(student_id=s.id, student_code=s.student_code, full_name=s.full_name, risk_score=float(s.risk_score or 0),
                    risk_level=s.risk_level or "low", computed_at=s.risk_computed_at)
            for s in q.order_by(Student.risk_score.desc()).limit(limit).all()]


@router.post("/retention/students/{student_id}/recompute")
def api_recompute(student_id: int, db: Session = Depends(get_db), user: User = Depends(require("retention.update"))):
    st = db.get(Student, student_id)
    if not st:
        raise HTTPException(404, "Student not found")
    res = retention_svc.compute_risk(db, st, user)
    db.commit()
    return {"student_id": st.id, "score": res["score"], "level": res["level"], "signals": res["signals"]}


@router.get("/retention/dashboard")
def api_retention_dashboard(db: Session = Depends(get_db), user: User = Depends(require("retention.view"))):
    return retention_svc.retention_dashboard(db)


# ============================================================================= inbox
@router.get("/conversations")
def api_conversations(status: str = "", limit: int = Query(50, le=200), db: Session = Depends(get_db), user: User = Depends(require("inbox.view"))):
    q = db.query(Conversation)
    if status:
        q = q.filter(Conversation.status == status)
    return [{"id": c.id, "contact_name": c.contact_name, "contact_phone": c.contact_phone, "status": c.status, "tags": c.tags,
             "unread": c.unread_count, "last_message_at": c.last_message_at, "preview": c.last_message_preview,
             "lead_id": c.lead_id, "client_id": c.client_id} for c in q.order_by(Conversation.last_message_at.desc()).limit(limit).all()]


@router.get("/conversations/{cid}/messages")
def api_messages(cid: int, db: Session = Depends(get_db), user: User = Depends(require("inbox.view"))):
    conv = db.get(Conversation, cid)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return [{"id": m.id, "direction": m.direction, "body": m.body, "status": m.status, "type": m.message_type,
             "template": m.template_name, "created_at": m.created_at, "error": m.error} for m in conv.messages]
