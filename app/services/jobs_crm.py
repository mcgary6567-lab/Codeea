"""Background jobs for CRM & Growth (sequences, case SLA, ambassador milestones, survey triggers, retention, follow-ups)."""
from __future__ import annotations

import logging
from datetime import datetime, date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.notify import notify
from app.models.academic import MonthlyTest
from app.models.crm import Lead, Survey, Feedback, Referral, Case
from app.models.people import Client, Student
from app.services import crm
from app.services import retention as retention_svc

log = logging.getLogger("oqc.jobs.crm")


# ----------------------------------------------------------------------------- sequences
def run_sequence_steps(db: Session) -> dict:
    return crm.run_sequences(db)


# ----------------------------------------------------------------------------- cases
def monitor_case_sla(db: Session) -> dict:
    return crm.case_sla_monitor(db)


# ----------------------------------------------------------------------------- ambassadors
def ambassador_auto_invite(db: Session, limit: int = 25) -> dict:
    """Daily: invite eligible families at the tenure milestone (gated on NPS + attendance)."""
    min_tenure = int(crm.setting(db, "ambassador_tenure_days", 60) or 60)
    cutoff = date.today() - timedelta(days=min_tenure)
    invited = skipped = 0
    q = (db.query(Client).filter(Client.is_ambassador.is_(False), Client.status.in_(["active", "trial"]), Client.joined_at <= cutoff)
         .order_by(Client.joined_at).limit(limit))
    for c in q.all():
        ok, elig = crm.invite_ambassador(db, c, actor=None)
        if ok:
            invited += 1
        else:
            skipped += 1
    return {"invited": invited, "skipped": skipped}


# ----------------------------------------------------------------------------- surveys
def _already_sent(db: Session, survey: Survey, client_id: int | None, student_id: int | None, days: int) -> bool:
    q = db.query(func.count(Feedback.id)).filter(Feedback.survey_id == survey.id, Feedback.sent_at >= datetime.utcnow() - timedelta(days=days))
    if student_id:
        q = q.filter(Feedback.student_id == student_id)
    elif client_id:
        q = q.filter(Feedback.client_id == client_id)
    return bool(q.scalar())


def survey_triggers(db: Session, limit: int = 40) -> dict:
    """Fire post_result_card and tenure_30/90/180 surveys."""
    sent = 0
    by_trigger = {s.trigger: s for s in db.query(Survey).filter(Survey.is_active.is_(True)).order_by(Survey.id).all()}
    # post result card: delivered in the last day
    survey = by_trigger.get("post_result_card")
    if survey:
        since = datetime.utcnow() - timedelta(days=1)
        for mt in db.query(MonthlyTest).filter(MonthlyTest.status == "delivered", MonthlyTest.delivered_at >= since).limit(limit).all():
            st = db.get(Student, mt.student_id)
            if not st or not st.client_id or _already_sent(db, survey, st.client_id, st.id, 20):
                continue
            crm.send_survey(db, survey, client=st.client, student=st, trigger="post_result_card")
            sent += 1
    # tenure milestones
    for days, key in ((30, "tenure_30"), (90, "tenure_90"), (180, "tenure_180")):
        survey = by_trigger.get(key)
        if not survey:
            continue
        target = date.today() - timedelta(days=days)
        for st in db.query(Student).filter(Student.status.in_(["active", "trial"]), Student.join_date == target).limit(limit).all():
            if not st.client_id or _already_sent(db, survey, st.client_id, st.id, days - 1):
                continue
            crm.send_survey(db, survey, client=st.client, student=st, trigger=key)
            sent += 1
    return {"sent": sent}


# ----------------------------------------------------------------------------- retention
def weekly_risk_recompute(db: Session) -> dict:
    counts = retention_svc.recompute_all(db)
    counts["freeze_outreach"] = retention_svc.schedule_freeze_outreach(db)
    return counts


# ----------------------------------------------------------------------------- leads
def lead_follow_up_reminders(db: Session, limit: int = 60) -> dict:
    """Remind closers about overdue follow-ups and leads never contacted within an hour."""
    now = datetime.utcnow()
    reminded = 0
    q = (db.query(Lead).filter(Lead.stage.notin_(["won", "lost"]), Lead.assigned_to_id.isnot(None), Lead.next_follow_up.isnot(None),
                               Lead.next_follow_up <= now, Lead.next_follow_up >= now - timedelta(hours=1)).limit(limit))
    for lead in q.all():
        notify(db, lead.assigned_to_id, f"Follow-up due: {lead.full_name}", f"{lead.lead_code} is at stage {lead.stage} with score {lead.score}.",
               event_type="lead_follow_up", link=f"/crm/leads/{lead.id}")
        reminded += 1
    stale = (db.query(Lead).filter(Lead.stage == "new", Lead.assigned_to_id.isnot(None), Lead.last_contacted_at.is_(None),
                                   Lead.created_at <= now - timedelta(hours=1), Lead.created_at >= now - timedelta(hours=2)).limit(limit))
    breached = 0
    for lead in stale.all():
        notify(db, lead.assigned_to_id, f"1-hour contact SLA missed: {lead.full_name}", f"{lead.lead_code} has not been contacted yet.",
               event_type="lead_sla", link=f"/crm/leads/{lead.id}")
        breached += 1
    return {"reminded": reminded, "sla_breached": breached}


JOBS = [
    ("crm_sequences", run_sequence_steps, 5),
    ("crm_case_sla", monitor_case_sla, 10),
    ("crm_lead_follow_ups", lead_follow_up_reminders, 60),
    ("crm_survey_triggers", survey_triggers, 60 * 24),
    ("crm_ambassador_invites", ambassador_auto_invite, 60 * 24),
    ("crm_risk_recompute", weekly_risk_recompute, 60 * 24 * 7),
]
