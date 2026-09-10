"""CRM & growth: leads, campaigns, WhatsApp inbox, sequences, referrals, feedback, cases, retention."""
from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, Date, Text, ForeignKey, JSON, Float, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, PKMixin, TimestampMixin

LEAD_STAGES = ["new", "contacted", "trial_scheduled", "trial_done", "negotiation", "won", "lost"]


class Campaign(Base, PKMixin, TimestampMixin):
    __tablename__ = "campaigns"
    name: Mapped[str] = mapped_column(String(150))
    platform: Mapped[str] = mapped_column(String(30), default="meta")  # meta | google | organic | referral | whatsapp | email | other
    objective: Mapped[Optional[str]] = mapped_column(String(100))
    budget: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    spend: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    country: Mapped[Optional[str]] = mapped_column(String(80))
    offer: Mapped[Optional[str]] = mapped_column(String(150))
    utm_source: Mapped[Optional[str]] = mapped_column(String(80))
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(120))
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active")  # draft | active | paused | ended
    external_id: Mapped[Optional[str]] = mapped_column(String(80))

    metrics = relationship("CampaignMetric", back_populates="campaign")


class CampaignMetric(Base, PKMixin):
    __tablename__ = "campaign_metrics"
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    leads: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[float] = mapped_column(Numeric(12, 2), default=0)

    campaign = relationship("Campaign", back_populates="metrics")


class LeadSource(Base, PKMixin, TimestampMixin):
    __tablename__ = "lead_sources"
    name: Mapped[str] = mapped_column(String(80), unique=True)  # WhatsApp, Website, Meta Ads, Google Ads, Referral, Manual, GHL
    source_type: Mapped[str] = mapped_column(String(30), default="inbound")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Lead(Base, PKMixin, TimestampMixin):
    __tablename__ = "leads"
    lead_code: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # L-00001
    full_name: Mapped[str] = mapped_column(String(150))
    email: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    whatsapp: Mapped[Optional[str]] = mapped_column(String(50))
    country: Mapped[Optional[str]] = mapped_column(String(80))
    timezone: Mapped[Optional[str]] = mapped_column(String(64))
    student_name: Mapped[Optional[str]] = mapped_column(String(150))
    student_age: Mapped[Optional[int]] = mapped_column(Integer)
    students_count: Mapped[int] = mapped_column(Integer, default=1)
    course_interest_id: Mapped[Optional[int]] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    preferred_time: Mapped[Optional[str]] = mapped_column(String(100))
    source_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lead_sources.id", ondelete="SET NULL"))
    campaign_id: Mapped[Optional[int]] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"))
    referral_code: Mapped[Optional[str]] = mapped_column(String(20))
    stage: Mapped[str] = mapped_column(String(30), default="new", index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)  # 0-100 AI/heuristic lead score
    score_factors: Mapped[dict] = mapped_column(JSON, default=dict)
    generator_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assigned_to_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    next_follow_up: Mapped[Optional[datetime]] = mapped_column(DateTime)
    follow_up_count: Mapped[int] = mapped_column(Integer, default=0)
    last_contacted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    lost_reason: Mapped[Optional[str]] = mapped_column(String(200))
    converted_client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
    converted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_duplicate_of_id: Mapped[Optional[int]] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"))
    ghl_contact_id: Mapped[Optional[str]] = mapped_column(String(80))
    whatsapp_opt_in: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    source = relationship("LeadSource")
    campaign = relationship("Campaign")
    course_interest = relationship("Course")
    assigned_to = relationship("User", foreign_keys=[assigned_to_id])
    generator = relationship("User", foreign_keys=[generator_id])
    activities = relationship("LeadActivity", back_populates="lead", order_by="LeadActivity.created_at.desc()")


class LeadActivity(Base, PKMixin):
    __tablename__ = "lead_activities"
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    activity_type: Mapped[str] = mapped_column(String(30))  # note | call | whatsapp | email | stage_change | trial | assignment
    note: Mapped[Optional[str]] = mapped_column(Text)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    lead = relationship("Lead", back_populates="activities")
    user = relationship("User")


class Conversation(Base, PKMixin, TimestampMixin):
    """Shared WhatsApp inbox thread."""
    __tablename__ = "conversations"
    channel: Mapped[str] = mapped_column(String(20), default="whatsapp")
    contact_type: Mapped[str] = mapped_column(String(20))  # lead | client
    lead_id: Mapped[Optional[int]] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), index=True)
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), index=True)
    contact_name: Mapped[str] = mapped_column(String(150))
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50))
    assigned_to_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | pending | closed
    tags: Mapped[list] = mapped_column(JSON, default=list)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    last_message_preview: Mapped[Optional[str]] = mapped_column(String(200))
    external_thread_id: Mapped[Optional[str]] = mapped_column(String(120))

    lead = relationship("Lead")
    client = relationship("Client")
    assigned_to = relationship("User")
    messages = relationship("Message", back_populates="conversation", order_by="Message.created_at", cascade="all, delete-orphan")
    notes = relationship("InternalNote", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base, PKMixin):
    __tablename__ = "messages"
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(10))  # in | out
    body: Mapped[str] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column(String(20), default="text")  # text | template | media | system
    template_name: Mapped[Optional[str]] = mapped_column(String(80))
    sender_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="sent")  # queued | sent | delivered | read | failed
    external_id: Mapped[Optional[str]] = mapped_column(String(120))
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    conversation = relationship("Conversation", back_populates="messages")
    sender = relationship("User")


class InternalNote(Base, PKMixin):
    __tablename__ = "internal_notes"
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="notes")
    user = relationship("User")


class MessageTemplate(Base, PKMixin, TimestampMixin):
    __tablename__ = "message_templates"
    name: Mapped[str] = mapped_column(String(80), unique=True)
    channel: Mapped[str] = mapped_column(String(20), default="whatsapp")
    category: Mapped[str] = mapped_column(String(40), default="follow_up")
    language: Mapped[str] = mapped_column(String(5), default="en")
    body: Mapped[str] = mapped_column(Text)  # supports {{name}}, {{student}}, {{link}}
    is_approved: Mapped[bool] = mapped_column(Boolean, default=True)


class Sequence(Base, PKMixin, TimestampMixin):
    """Automated follow-up / win-back sequences."""
    __tablename__ = "sequences"
    name: Mapped[str] = mapped_column(String(120))
    sequence_type: Mapped[str] = mapped_column(String(30), default="lead_follow_up")  # lead_follow_up | trial_follow_up | win_back | pre_leave_offer | freeze_reactivation | payment_reminder
    channel: Mapped[str] = mapped_column(String(20), default="whatsapp")
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{"day":0,"template":"...","body":"..."}]
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    enrollments = relationship("SequenceEnrollment", back_populates="sequence")


class SequenceEnrollment(Base, PKMixin, TimestampMixin):
    __tablename__ = "sequence_enrollments"
    sequence_id: Mapped[int] = mapped_column(ForeignKey("sequences.id", ondelete="CASCADE"), index=True)
    contact_type: Mapped[str] = mapped_column(String(20))  # lead | client | student
    contact_id: Mapped[int] = mapped_column(Integer, index=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | completed | stopped | replied
    stop_reason: Mapped[Optional[str]] = mapped_column(String(120))
    enrolled_by: Mapped[str] = mapped_column(String(20), default="system")

    sequence = relationship("Sequence", back_populates="enrollments")


class Referral(Base, PKMixin, TimestampMixin):
    """Module 43: Ambassador programme ledger (asks → leads → sign-ups → credits)."""
    __tablename__ = "referrals"
    ambassador_client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    referral_code: Mapped[str] = mapped_column(String(20), index=True)
    referred_name: Mapped[Optional[str]] = mapped_column(String(150))
    referred_phone: Mapped[Optional[str]] = mapped_column(String(50))
    referred_lead_id: Mapped[Optional[int]] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"))
    referred_client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="invited")  # invited | ask | lead | signed_up | qualified | credited | expired
    invited_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    qualified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    credit_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    credit_currency: Mapped[str] = mapped_column(String(3), default="GBP")
    ambassador_credit_ledger_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ledger_entries.id", ondelete="SET NULL"))
    referred_credit_ledger_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ledger_entries.id", ondelete="SET NULL"))
    ghl_source_tag: Mapped[Optional[str]] = mapped_column(String(80))
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))  # Supervisor owns weekly counts
    notes: Mapped[Optional[str]] = mapped_column(Text)

    ambassador = relationship("Client", foreign_keys=[ambassador_client_id])
    referred_client = relationship("Client", foreign_keys=[referred_client_id])
    referred_lead = relationship("Lead")


class Survey(Base, PKMixin, TimestampMixin):
    __tablename__ = "surveys"
    name: Mapped[str] = mapped_column(String(120))
    trigger: Mapped[str] = mapped_column(String(40))  # post_ptm | post_result_card | tenure_30 | tenure_90 | tenure_180 | staff_enps | manual
    audience: Mapped[str] = mapped_column(String(20), default="client")  # client | student | staff
    questions: Mapped[list] = mapped_column(JSON, default=list)  # [{"key":"nps","type":"nps","text":...}, {"key":"comment","type":"text"}]
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Feedback(Base, PKMixin, TimestampMixin):
    """Module 42: Voice of Customer / staff eNPS responses."""
    __tablename__ = "feedback"
    survey_id: Mapped[Optional[int]] = mapped_column(ForeignKey("surveys.id", ondelete="SET NULL"))
    trigger: Mapped[str] = mapped_column(String(40), default="manual")
    respondent_type: Mapped[str] = mapped_column(String(20))  # client | student | staff
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), index=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    nps: Mapped[Optional[int]] = mapped_column(Integer)  # 0-10
    rating: Mapped[Optional[int]] = mapped_column(Integer)  # 1-5
    comment: Mapped[Optional[str]] = mapped_column(Text)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    sentiment: Mapped[str] = mapped_column(String(10), default="neutral")  # positive | neutral | negative
    is_negative: Mapped[bool] = mapped_column(Boolean, default=False)
    case_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cases.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | submitted | routed | resolved
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    token: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    is_confidential: Mapped[bool] = mapped_column(Boolean, default=False)  # staff eNPS → P&C and CEO only

    client = relationship("Client")
    student = relationship("Student")
    teacher = relationship("Teacher")


class Case(Base, PKMixin, TimestampMixin):
    """Module 32: unified complaints / requests / tickets with SLA."""
    __tablename__ = "cases"
    case_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # CS-00001
    case_type: Mapped[str] = mapped_column(String(30), default="complaint")  # complaint | request | feedback | technical | billing | schedule_change | teacher_change
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    raised_by_type: Mapped[str] = mapped_column(String(20), default="client")  # client | student | staff | system
    raised_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), index=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    category: Mapped[Optional[str]] = mapped_column(String(60))  # ai classification: teaching_quality, punctuality, billing, technical, behaviour, schedule
    priority: Mapped[str] = mapped_column(String(10), default="medium", index=True)  # low | medium | high | urgent
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open | in_progress | waiting | resolved | closed | escalated
    assigned_to_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    sla_hours: Mapped[int] = mapped_column(Integer, default=48)
    sla_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    sla_breached: Mapped[bool] = mapped_column(Boolean, default=False)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    escalated_to_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution: Mapped[Optional[str]] = mapped_column(Text)
    root_cause: Mapped[Optional[str]] = mapped_column(String(200))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    ai_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ai_model_runs.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(20), default="portal")  # portal | whatsapp | feedback | staff | phone

    client = relationship("Client")
    student = relationship("Student")
    teacher = relationship("Teacher")
    assigned_to = relationship("User", foreign_keys=[assigned_to_id])
    department = relationship("Department")
    comments = relationship("CaseComment", back_populates="case", order_by="CaseComment.created_at", cascade="all, delete-orphan")


class CaseComment(Base, PKMixin):
    __tablename__ = "case_comments"
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(Text)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    case = relationship("Case", back_populates="comments")
    user = relationship("User")


class RetentionAction(Base, PKMixin, TimestampMixin):
    """Module 33 / 29.11: pre-emptive retention, freeze handling, win-back."""
    __tablename__ = "retention_actions"
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
    action_type: Mapped[str] = mapped_column(String(40))  # win_back_sequence | pre_leave_offer | freeze_outreach | cohort_call | teacher_change | discount_offer | call
    trigger: Mapped[str] = mapped_column(String(40), default="risk_score")  # risk_score | freeze | cancellation_request | test_decline | manual
    risk_score_at_trigger: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="scheduled")  # scheduled | in_progress | completed | succeeded | failed
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    outcome: Mapped[Optional[str]] = mapped_column(String(200))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    sequence_enrollment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sequence_enrollments.id", ondelete="SET NULL"))

    student = relationship("Student")
    owner = relationship("User")
