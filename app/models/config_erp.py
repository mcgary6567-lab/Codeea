"""Configuration models that mirror the college's existing ERP.

See docs/AUDIT_ACCOUNTS_CONFIG.md for the audit these serve. Lookups and branch properties come first
because the rest of the system reads from them: the attendance grace periods decide when a late arrival
attracts a fine, and the advance invoice days decide how far ahead billing runs.
"""
from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, Date, Text, ForeignKey, JSON, Float, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, PKMixin, TimestampMixin

LOOKUP_APPS = ["Online Academics", "Billing Management", "Human Resource", "Accounts", "Configuration", "Client Portal"]
TICKET_TYPES = ["Bug", "New Feature", "Change Request", "Question", "Data Fix"]
TICKET_PRIORITIES = ["low", "normal", "urgent", "very_urgent"]
TICKET_STATUSES = ["pending", "in_progress", "resolved", "rejected", "closed"]
VOUCHER_TYPES = ["journal", "payment", "receipt"]


class Lookup(Base, PKMixin, TimestampMixin):
    """A configurable value list, for example the designation list or the leaving reasons.

    Code is the stable key the application reads; description is what the configuration screen shows.
    """
    __tablename__ = "lookups"
    app: Mapped[str] = mapped_column(String(40), default="Configuration", index=True)
    code: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(250))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)  # code is referenced in code; do not delete
    status: Mapped[str] = mapped_column(String(20), default="active")
    sort_no: Mapped[int] = mapped_column(Integer, default=0)

    values = relationship("LookupValue", back_populates="lookup", cascade="all, delete-orphan",
                          order_by="LookupValue.sort_no")


class LookupValue(Base, PKMixin, TimestampMixin):
    __tablename__ = "lookup_values"
    lookup_id: Mapped[int] = mapped_column(ForeignKey("lookups.id", ondelete="CASCADE"), index=True)
    value: Mapped[str] = mapped_column(String(80))       # stored on the record
    label: Mapped[str] = mapped_column(String(150))      # shown to the user
    label_urdu: Mapped[Optional[str]] = mapped_column(String(200))
    amount: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))  # a fine, a bonus, a fee where relevant
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active")
    sort_no: Mapped[int] = mapped_column(Integer, default=0)

    lookup = relationship("Lookup", back_populates="values")


class PaymentGateway(Base, PKMixin, TimestampMixin):
    """A gateway the college collects through, with the fee it charges."""
    __tablename__ = "payment_gateways"
    name: Mapped[str] = mapped_column(String(120))
    gateway_company: Mapped[str] = mapped_column(String(60))  # Stripe | PayPal | Wise | ...
    default_transaction_fee_pct: Mapped[float] = mapped_column(Float, default=0)
    fixed_fee: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    currency: Mapped[Optional[str]] = mapped_column(String(3))
    beneficiary_account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("beneficiary_accounts.id", ondelete="SET NULL"))
    live_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="active")
    notes: Mapped[Optional[str]] = mapped_column(Text)

    beneficiary_account = relationship("BeneficiaryAccount")

    def fee_on(self, amount: float) -> float:
        return round(float(amount) * (self.default_transaction_fee_pct or 0) / 100 + float(self.fixed_fee or 0), 2)


class WhatsAppSender(Base, PKMixin, TimestampMixin):
    """One connected WhatsApp number, with its own send throttle."""
    __tablename__ = "whatsapp_senders"
    description: Mapped[str] = mapped_column(String(120))
    number: Mapped[str] = mapped_column(String(30))
    api_status: Mapped[str] = mapped_column(String(20), default="disconnected")  # connected | disconnected
    live_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    last_message_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=10)
    messages_per_cycle: Mapped[int] = mapped_column(Integer, default=1)
    purpose: Mapped[str] = mapped_column(String(40), default="general")  # general | academics | billing | marketing
    status: Mapped[str] = mapped_column(String(20), default="active")
    qr_token: Mapped[Optional[str]] = mapped_column(String(120))
    qr_refreshed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class SupportTicket(Base, PKMixin, TimestampMixin):
    """A request raised to whoever maintains the software."""
    __tablename__ = "support_tickets"
    ticket_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # TKT-00001
    subject: Mapped[str] = mapped_column(String(200))
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    message: Mapped[str] = mapped_column(Text)
    whatsapp_no: Mapped[Optional[str]] = mapped_column(String(30))
    ticket_type: Mapped[str] = mapped_column(String(30), default="Bug")
    module: Mapped[str] = mapped_column(String(60), default="Online Academics")
    priority: Mapped[str] = mapped_column(String(20), default="normal", index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    developer_remarks: Mapped[Optional[str]] = mapped_column(Text)
    raised_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    department = relationship("Department")
    raised_by = relationship("User")


class OtpConfiguration(Base, PKMixin, TimestampMixin):
    """How one-time passwords behave. One row; the per-user token state lives on the user."""
    __tablename__ = "otp_configurations"
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    channel: Mapped[str] = mapped_column(String(20), default="email")  # email | whatsapp | sms
    code_length: Mapped[int] = mapped_column(Integer, default=6)
    validity_minutes: Mapped[int] = mapped_column(Integer, default=10)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    resend_after_seconds: Mapped[int] = mapped_column(Integer, default=60)
    required_for_roles: Mapped[list] = mapped_column(JSON, default=list)  # role slugs that must use it
    whatsapp_sender_id: Mapped[Optional[int]] = mapped_column(ForeignKey("whatsapp_senders.id", ondelete="SET NULL"))

    whatsapp_sender = relationship("WhatsAppSender")


# --------------------------------------------------------------------------- Confido Agents (Configuration)
# Their tenth Configuration card. A desktop agent on a staff machine records calls and takes screenshots; the
# portal shows how many licences are allowed and consumed, the devices that have checked in, and the screens
# captured. The agent program is their vendor's; these rows are what an agent reports through our API.
AGENT_DEVICE_STATUSES = ["online", "offline", "blocked"]


class AgentLicense(Base, PKMixin, TimestampMixin):
    __tablename__ = "agent_licenses"
    license_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    issued_to_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    issued_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active | revoked
    notes: Mapped[Optional[str]] = mapped_column(String(300))

    issued_to = relationship("User", foreign_keys=[issued_to_user_id])
    devices = relationship("AgentDevice", back_populates="license")


class AgentDevice(Base, PKMixin, TimestampMixin):
    __tablename__ = "agent_devices"
    license_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agent_licenses.id", ondelete="SET NULL"))
    employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), index=True)
    machine_name: Mapped[str] = mapped_column(String(120))
    machine_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    os_name: Mapped[Optional[str]] = mapped_column(String(80))
    agent_version: Mapped[Optional[str]] = mapped_column(String(30))
    ip_address: Mapped[Optional[str]] = mapped_column(String(64))
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(20), default="offline", index=True)  # online | offline | blocked

    license = relationship("AgentLicense", back_populates="devices")
    employee = relationship("Employee")


class AgentScreenshot(Base, PKMixin, TimestampMixin):
    __tablename__ = "agent_screenshots"
    device_id: Mapped[int] = mapped_column(ForeignKey("agent_devices.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    image_path: Mapped[str] = mapped_column(String(300))
    note: Mapped[Optional[str]] = mapped_column(String(200))

    device = relationship("AgentDevice")
    employee = relationship("Employee")
