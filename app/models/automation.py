"""CRM automation: tags on contacts, and workflows that run step by step when something happens.

Modelled on the GoHighLevel rollout the college's customers asked for: a workflow has a trigger (an event
such as a lead being created or a payment received), optional filters, and an ordered list of steps
(send a message, add a tag, wait, branch, notify staff, create a task, move a lead). A run is one contact
going through one workflow; it waits between steps and the scheduler advances it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, PKMixin, TimestampMixin

TAG_CATEGORIES = [("source", "Source"), ("market", "Market"), ("status", "Status"), ("course", "Course"),
                  ("temperature", "Temperature"), ("consent", "Consent"), ("pipeline", "Pipeline"), ("other", "Other")]

CONTACT_TYPES = ["lead", "client", "student"]

# Every event a workflow can start from. The second value is the contact the run is about.
WORKFLOW_TRIGGERS = {
    "lead.created": ("New lead created (form, WhatsApp, registration or manual entry)", "lead"),
    "lead.stage_changed": ("Lead moved to a pipeline stage", "lead"),
    "lead.lost": ("Lead marked lost / stale", "lead"),
    "lead.stale": ("Lead with no activity for N days (daily check)", "lead"),
    "lead.converted": ("Lead converted to a client (enrolled)", "client"),
    "trial.scheduled": ("Trial / demo class booked", "lead"),
    "trial.attended": ("Trial / demo class attended", "lead"),
    "trial.no_show": ("Trial / demo class no-show", "lead"),
    "payment.first": ("First payment received from a family", "client"),
    "payment.received": ("Any payment received", "client"),
    "subscription.frozen": ("Subscription frozen", "client"),
    "subscription.resumed": ("Subscription resumed after a freeze", "client"),
    "subscription.cancelled": ("Subscription cancelled", "client"),
    "course.completed": ("Course completed (certificate issued)", "student"),
    "feedback.submitted": ("Survey / NPS answer submitted", "client"),
    "parent.contacted": ("A conversation with the family was recorded", "client"),
    "student.at_risk": ("Student flagged at risk", "student"),
    "student.recommendation": ("Teacher recorded a recommendation about the student", "student"),
    "student.inactive": ("Student with no attended class for N days (daily check)", "student"),
    "class.student_absent": ("Student absent from a class", "student"),
    "tag.added": ("A tag added to a contact", "any"),
    "manual": ("Started by hand from the contact's page", "any"),
}

# The step kinds a workflow may contain, with the fields each one takes (used by the editor and validation).
STEP_KINDS = {
    "wait": {"label": "Wait", "fields": ["hours", "days"]},
    "wait_until": {"label": "Wait until a date on the record", "fields": ["field"]},
    "send_whatsapp": {"label": "Send WhatsApp", "fields": ["template", "body"]},
    "send_email": {"label": "Send email", "fields": ["subject", "body"]},
    "send_sms": {"label": "Send SMS", "fields": ["body"]},
    "notify_staff": {"label": "Notify staff", "fields": ["to", "title", "body"]},
    "add_tag": {"label": "Add tag", "fields": ["tag"]},
    "remove_tag": {"label": "Remove tag", "fields": ["tag"]},
    "move_stage": {"label": "Move lead to stage", "fields": ["stage", "reason"]},
    "create_task": {"label": "Create task for staff", "fields": ["to", "title", "due_days"]},
    "enroll_sequence": {"label": "Enrol in a WhatsApp sequence", "fields": ["sequence_type"]},
    "condition": {"label": "If / else", "fields": ["field", "op", "value", "then", "else"]},
    "webhook": {"label": "Send an outbound webhook event", "fields": ["event"]},
    "exit": {"label": "Stop this workflow", "fields": ["reason"]},
}

WORKFLOW_CATEGORIES = [("acquisition", "Acquisition"), ("onboarding", "Onboarding"), ("engagement", "Engagement"),
                       ("retention", "Retention"), ("alumni", "Alumni & Upsell"), ("compliance", "Compliance"), ("internal", "Internal")]

RUN_STATUSES = ["active", "waiting", "completed", "stopped", "failed"]


class Tag(Base, PKMixin, TimestampMixin):
    __tablename__ = "tags"
    name: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(20), default="other", index=True)
    color: Mapped[str] = mapped_column(String(20), default="slate")
    description: Mapped[Optional[str]] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    contacts = relationship("ContactTag", back_populates="tag", cascade="all, delete-orphan")


class ContactTag(Base, PKMixin):
    __tablename__ = "contact_tags"
    __table_args__ = (UniqueConstraint("contact_type", "contact_id", "tag_id", name="uq_contact_tag"),)
    contact_type: Mapped[str] = mapped_column(String(20), index=True)  # lead | client | student
    contact_id: Mapped[int] = mapped_column(Integer, index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)
    added_by: Mapped[str] = mapped_column(String(40), default="system")  # system | workflow:<code> | <user email>
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tag = relationship("Tag", back_populates="contacts")


class Workflow(Base, PKMixin, TimestampMixin):
    __tablename__ = "workflows"
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # AUTO-003
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(20), default="acquisition", index=True)
    pipeline: Mapped[Optional[str]] = mapped_column(String(60))  # "Student Acquisition" / "Leave & Freeze" / ...
    stage: Mapped[Optional[str]] = mapped_column(String(60))  # the pipeline stage this workflow serves, if any
    trigger: Mapped[str] = mapped_column(String(40), index=True)
    trigger_filter: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"stage": "contacted"} / {"nps_min": 9}
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{"kind": "send_whatsapp", "template": "..."}, {"kind": "wait", "days": 1}, ...]
    exit_on: Mapped[dict] = mapped_column(JSON, default=dict)  # {"lead_stages": ["won", "lost"], "reply": true, "client_statuses": [...]}
    run_once_per_contact: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    needs: Mapped[Optional[str]] = mapped_column(String(200))  # what must be configured before this fully works (e.g. "SMS provider")
    sort_no: Mapped[int] = mapped_column(Integer, default=0)

    runs = relationship("WorkflowRun", back_populates="workflow", cascade="all, delete-orphan")


class WorkflowRun(Base, PKMixin):
    __tablename__ = "workflow_runs"
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    contact_type: Mapped[str] = mapped_column(String(20), index=True)
    contact_id: Mapped[int] = mapped_column(Integer, index=True)
    trigger_event: Mapped[str] = mapped_column(String(40))
    context: Mapped[dict] = mapped_column(JSON, default=dict)  # the event payload the run started with
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    stop_reason: Mapped[Optional[str]] = mapped_column(String(200))
    log: Mapped[list] = mapped_column(JSON, default=list)  # [{"step": 0, "kind": "...", "at": iso, "result": "..."}]
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    workflow = relationship("Workflow", back_populates="runs")


class AutomationEvent(Base, PKMixin):
    """Every event offered to the engine, whether or not a workflow picked it up."""
    __tablename__ = "automation_events"
    event: Mapped[str] = mapped_column(String(40), index=True)
    contact_type: Mapped[str] = mapped_column(String(20))
    contact_id: Mapped[int] = mapped_column(Integer, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    runs_started: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
