"""Core platform entities: organization, users, roles, audit, notifications, integrations, AI governance."""
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, Text, ForeignKey, JSON, Float, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, PKMixin, TimestampMixin


class Organization(Base, PKMixin, TimestampMixin):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(200), default="Online Quran College")
    legal_name: Mapped[Optional[str]] = mapped_column(String(200))
    base_currency: Mapped[str] = mapped_column(String(3), default="PKR")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Karachi")
    email: Mapped[Optional[str]] = mapped_column(String(200))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    website: Mapped[Optional[str]] = mapped_column(String(200))
    logo_url: Mapped[Optional[str]] = mapped_column(String(300))
    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # morning/afternoon report deadlines, etc.


class Branch(Base, PKMixin, TimestampMixin):
    __tablename__ = "branches"
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(20), unique=True)
    country: Mapped[str] = mapped_column(String(80), default="Pakistan")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Karachi")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Department(Base, PKMixin, TimestampMixin):
    __tablename__ = "departments"
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(30), unique=True)  # people, finance, academics, qa, technology, marketing
    description: Mapped[Optional[str]] = mapped_column(Text)
    hod_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    hod = relationship("User", foreign_keys=[hod_user_id], post_update=True)


class Role(Base, PKMixin, TimestampMixin):
    __tablename__ = "roles"
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    portal: Mapped[str] = mapped_column(String(20), default="admin")  # admin | teacher | client | student | auditor
    permissions: Mapped[list] = mapped_column(JSON, default=list)  # ["students.view", "billing.*", "*.view"]

    users = relationship("User", back_populates="role")


class User(Base, PKMixin, TimestampMixin):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[Optional[int]] = mapped_column(ForeignKey("roles.id"))
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id"))
    branch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("branches.id"))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Karachi")
    language: Mapped[str] = mapped_column(String(5), default="en")  # en | ur
    avatar_url: Mapped[Optional[str]] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    two_factor_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    two_factor_secret: Mapped[Optional[str]] = mapped_column(String(64))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_login_ip: Mapped[Optional[str]] = mapped_column(String(64))
    allowed_ips: Mapped[Optional[str]] = mapped_column(String(500))  # comma separated allowlist (optional)
    extra_permissions: Mapped[list] = mapped_column(JSON, default=list)
    denied_permissions: Mapped[list] = mapped_column(JSON, default=list)

    role = relationship("Role", back_populates="users")
    department = relationship("Department", foreign_keys=[department_id])
    branch = relationship("Branch")

    @property
    def role_slug(self) -> str:
        return self.role.slug if self.role else "guest"

    @property
    def portal(self) -> str:
        return self.role.portal if self.role else "admin"

    @property
    def initials(self) -> str:
        parts = (self.full_name or "?").split()
        return "".join(p[0].upper() for p in parts[:2])


class UserSession(Base, PKMixin):
    __tablename__ = "user_sessions"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    user = relationship("User")


class AuditEvent(Base, PKMixin):
    """Immutable audit trail (Module 48). Never updated or deleted by application code."""
    __tablename__ = "audit_events"
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    actor_name: Mapped[Optional[str]] = mapped_column(String(150))
    action: Mapped[str] = mapped_column(String(80), index=True)  # create/update/delete/approve/login/export/...
    module: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(80))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    description: Mapped[Optional[str]] = mapped_column(Text)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    before_data: Mapped[Optional[dict]] = mapped_column(JSON)
    after_data: Mapped[Optional[dict]] = mapped_column(JSON)
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(20), default="info")  # info | warning | critical
    is_consequential: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    actor = relationship("User")


class Notification(Base, PKMixin):
    __tablename__ = "notifications"
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="in_app")  # in_app | email | whatsapp | push
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[Optional[str]] = mapped_column(Text)
    link: Mapped[Optional[str]] = mapped_column(String(300))
    recipient_address: Mapped[Optional[str]] = mapped_column(String(200))  # email / phone for external channels
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | sent | delivered | failed | read
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User")


class NotificationTemplate(Base, PKMixin, TimestampMixin):
    __tablename__ = "notification_templates"
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="in_app")
    language: Mapped[str] = mapped_column(String(5), default="en")
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CommunicationPreference(Base, PKMixin, TimestampMixin):
    __tablename__ = "communication_preferences"
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(20))
    opted_in: Mapped[bool] = mapped_column(Boolean, default=True)
    consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Setting(Base, PKMixin, TimestampMixin):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), unique=True)
    value: Mapped[Optional[dict]] = mapped_column(JSON)
    group: Mapped[str] = mapped_column(String(40), default="general")
    description: Mapped[Optional[str]] = mapped_column(String(300))


class ApiKey(Base, PKMixin, TimestampMixin):
    __tablename__ = "api_keys"
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(12), index=True)
    key_hash: Mapped[str] = mapped_column(String(255))
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=120)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    owner = relationship("User")


class Integration(Base, PKMixin, TimestampMixin):
    __tablename__ = "integrations"
    provider: Mapped[str] = mapped_column(String(50), unique=True)  # whatsapp, ghl, n8n, zoom, google, meta_ads, google_ads, payment, smtp, slack, openproject, ai
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="not_configured")  # not_configured | simulated | connected | error
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # non-secret config; secrets live in env
    health: Mapped[str] = mapped_column(String(20), default="unknown")  # healthy | degraded | down | unknown
    last_health_check_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    calls_today: Mapped[int] = mapped_column(Integer, default=0)
    failures_today: Mapped[int] = mapped_column(Integer, default=0)


class Webhook(Base, PKMixin, TimestampMixin):
    __tablename__ = "webhooks"
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(500))
    events: Mapped[list] = mapped_column(JSON, default=list)
    secret: Mapped[Optional[str]] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_status: Mapped[Optional[str]] = mapped_column(String(20))
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class WebhookDelivery(Base, PKMixin):
    __tablename__ = "webhook_deliveries"
    webhook_id: Mapped[Optional[int]] = mapped_column(ForeignKey("webhooks.id", ondelete="CASCADE"))
    direction: Mapped[str] = mapped_column(String(10), default="out")  # out | in
    event: Mapped[str] = mapped_column(String(80))
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | success | failed | dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response_code: Mapped[Optional[int]] = mapped_column(Integer)
    response_body: Mapped[Optional[str]] = mapped_column(Text)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    webhook = relationship("Webhook")


class FileAsset(Base, PKMixin, TimestampMixin):
    __tablename__ = "file_assets"
    filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    entity_type: Mapped[Optional[str]] = mapped_column(String(60))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)


class SecurityIncident(Base, PKMixin, TimestampMixin):
    __tablename__ = "security_incidents"
    incident_type: Mapped[str] = mapped_column(String(60))  # brute_force, lockout, ip_blocked, permission_denied, suspicious_export
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    description: Mapped[Optional[str]] = mapped_column(Text)
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="open")
    resolved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    user = relationship("User", foreign_keys=[user_id])


class BackupRecord(Base, PKMixin):
    __tablename__ = "backup_records"
    filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    backup_type: Mapped[str] = mapped_column(String(20), default="manual")  # manual | scheduled
    status: Mapped[str] = mapped_column(String(20), default="completed")
    restore_tested: Mapped[bool] = mapped_column(Boolean, default=False)
    restore_tested_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AIModelRun(Base, PKMixin):
    """AI governance record: every AI output is traceable (Module 38)."""
    __tablename__ = "ai_model_runs"
    module: Mapped[str] = mapped_column(String(60), index=True)  # class_monitoring, lead_scoring, churn, complaint_classification, lesson_recommendation, insights
    task: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(40), default="simulated")
    model: Mapped[str] = mapped_column(String(80), default="simulated-v1")
    model_version: Mapped[str] = mapped_column(String(40), default="1.0")
    prompt_version: Mapped[str] = mapped_column(String(40), default="1.0")
    entity_type: Mapped[Optional[str]] = mapped_column(String(60))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    input_summary: Mapped[Optional[str]] = mapped_column(Text)
    output: Mapped[Optional[dict]] = mapped_column(JSON)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    review_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected | overridden | false_positive
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    review_note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    reviewed_by = relationship("User")


class RiskAlert(Base, PKMixin, TimestampMixin):
    """Operational / executive alerts (missed classes, anomalies, safeguarding, integration failures)."""
    __tablename__ = "risk_alerts"
    alert_type: Mapped[str] = mapped_column(String(60), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="medium")  # low | medium | high | critical
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[Optional[str]] = mapped_column(Text)
    entity_type: Mapped[Optional[str]] = mapped_column(String(60))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    visibility: Mapped[str] = mapped_column(String(30), default="ops")  # ops | management | ceo_only
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | acknowledged | resolved | dismissed
    acknowledged_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(30), default="system")  # system | ai | manual
