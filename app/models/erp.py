"""ERP-parity models.

These mirror the functional areas of the college's existing ERP (Online Academics):
session slots, academic configuration, client contacts/credentials, client requests, class management
(arrangements, reschedule approvals, class queries, activities), billing additions, and the QA call pipeline.
See docs/AUDIT_ACADEMICS.md for the page-by-page audit these models serve.
"""
from datetime import datetime, date, time
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, Date, Time, Text, ForeignKey, JSON, Float, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, PKMixin, TimestampMixin

REQUEST_STATUSES = ["pending", "approved", "rejected", "cancelled"]
SESSION_CATEGORIES = ["30 Minutes", "45 Minutes"]
COURSE_METHODS = ["one_on_one", "group"]
LANGUAGES = ["Arabic", "Chinese", "English", "French", "Japanese", "Pashto", "Punjabi", "Sindhi", "Urdu"]
LEDGER_ADDITION_TYPES = ["Teacher Gift", "Leave Discount", "Referral Bonus", "Late Fee", "Adjustment", "Penalty"]
CLASS_QUERY_TYPES = ["Family want to talk with manager", "Facing Tech Issue", "Book / material issue", "Other"]
CALL_SOURCES = ["AGENT", "TEAMS", "ZOOM"]
QA_REVIEW_STATUSES = ["pending", "in_progress", "completed", "flagged", "rejected"]


# --------------------------------------------------------------------------- Academic configuration
class SessionSlot(Base, PKMixin, TimestampMixin):
    """A bookable class time ("07:00 AM - 07:30 AM"), 48 half-hour slots per category, org timezone (Asia/Karachi)."""
    __tablename__ = "session_slots"
    category: Mapped[str] = mapped_column(String(20), default="30 Minutes", index=True)  # 30 Minutes | 45 Minutes
    label: Mapped[str] = mapped_column(String(40))  # "07:00 AM - 07:30 AM"
    start_time: Mapped[time] = mapped_column(Time, index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | inactive
    sort_no: Mapped[float] = mapped_column(Float, default=0)

    @property
    def end_time(self) -> time:
        total = self.start_time.hour * 60 + self.start_time.minute + (self.duration_minutes or 30)
        return time((total // 60) % 24, total % 60)

    @property
    def utc_label(self) -> str:
        """Start time expressed in UTC (Pakistan is UTC+5, no DST)."""
        total = (self.start_time.hour * 60 + self.start_time.minute - 300) % (24 * 60)
        return time(total // 60, total % 60).strftime("%I:%M %p")


class ClientAcademicGroup(Base, PKMixin, TimestampMixin):
    """Morning / Night billing-and-academic groups with a representative (shift manager)."""
    __tablename__ = "client_academic_groups"
    name: Mapped[str] = mapped_column(String(80))
    pseudo_name: Mapped[Optional[str]] = mapped_column(String(80))  # "Morning Manager"
    shift_group: Mapped[str] = mapped_column(String(20), default="morning")  # morning | night
    representative_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="active")

    representative = relationship("User")


class TeamsUser(Base, PKMixin, TimestampMixin):
    """MS Teams account mapping for staff and clients."""
    __tablename__ = "teams_users"
    person_type: Mapped[str] = mapped_column(String(10), index=True)  # staff | client
    employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    teams_email: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[Optional[str]] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(20), default="active")

    employee = relationship("Employee")
    client = relationship("Client")


class InvoiceAdditionType(Base, PKMixin, TimestampMixin):
    """Invoice Addition List: named discount / charge / tax lines."""
    __tablename__ = "invoice_addition_types"
    addition_type: Mapped[str] = mapped_column(String(20), default="discount")  # discount | charge | tax
    description: Mapped[str] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(20), default="active")


class InvoiceAdditionRule(Base, PKMixin, TimestampMixin):
    """Invoice Additions Master: when and how an addition is applied to generated invoices."""
    __tablename__ = "invoice_addition_rules"
    level: Mapped[str] = mapped_column(String(20), default="subscription")  # subscription | client | global
    from_date: Mapped[date] = mapped_column(Date, default=date.today)
    to_date: Mapped[Optional[date]] = mapped_column(Date)
    addition_type_id: Mapped[int] = mapped_column(ForeignKey("invoice_addition_types.id", ondelete="CASCADE"))
    implementation_type: Mapped[str] = mapped_column(String(20), default="fixed")  # fixed | percent
    amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")
    auto_assigned: Mapped[bool] = mapped_column(Boolean, default=False)
    subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscriptions.id", ondelete="CASCADE"))
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))

    addition_type = relationship("InvoiceAdditionType")
    subscription = relationship("Subscription")
    client = relationship("Client")


class BeneficiaryAccount(Base, PKMixin, TimestampMixin):
    """Receipt Beneficiary Accounts: where money is received."""
    __tablename__ = "beneficiary_accounts"
    payment_mode: Mapped[str] = mapped_column(String(40))  # Online Payment Gateway | Bank | Cash
    category: Mapped[str] = mapped_column(String(60))  # Stripe | PayPal | UBL | Meezan Bank | Wise | ...
    account_name: Mapped[str] = mapped_column(String(120))
    account_details: Mapped[Optional[str]] = mapped_column(String(300))
    is_auto: Mapped[bool] = mapped_column(Boolean, default=False)  # gateway receipts arrive automatically
    status: Mapped[str] = mapped_column(String(20), default="active")


class AssessmentDefinition(Base, PKMixin, TimestampMixin):
    __tablename__ = "assessment_definitions"
    book_id: Mapped[Optional[int]] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(150))
    passing_marks: Mapped[float] = mapped_column(Float, default=0)
    total_marks: Mapped[float] = mapped_column(Float, default=100)
    status: Mapped[str] = mapped_column(String(20), default="active")

    book = relationship("Book")
    questions = relationship("QuestionBankItem", back_populates="assessment")


class QuestionBankItem(Base, PKMixin, TimestampMixin):
    __tablename__ = "question_bank"
    assessment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assessment_definitions.id", ondelete="SET NULL"))
    book_id: Mapped[Optional[int]] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[Optional[str]] = mapped_column(Text)
    question_type: Mapped[str] = mapped_column(String(20), default="oral")  # oral | written | recitation | mcq
    marks: Mapped[float] = mapped_column(Float, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active")

    assessment = relationship("AssessmentDefinition", back_populates="questions")
    book = relationship("Book")


# --------------------------------------------------------------------------- Client detail grids
class ClientContact(Base, PKMixin, TimestampMixin):
    __tablename__ = "client_contacts"
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    contact_type: Mapped[str] = mapped_column(String(20), default="phone")  # phone | whatsapp | email | skype | other
    detail: Mapped[str] = mapped_column(String(200))
    remarks: Mapped[Optional[str]] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")

    client = relationship("Client")


class ClientCredential(Base, PKMixin, TimestampMixin):
    """Third-party class-tool logins kept for a family (Zoom / Teams / Skype). Secret shown masked."""
    __tablename__ = "client_credentials"
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    credential_type: Mapped[str] = mapped_column(String(20), default="zoom")  # zoom | teams | skype | portal | other
    login: Mapped[str] = mapped_column(String(200))
    secret: Mapped[Optional[str]] = mapped_column(String(200))
    remarks: Mapped[Optional[str]] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")

    client = relationship("Client")


# --------------------------------------------------------------------------- Client requests (approval workflow)
class TimeChangeRequest(Base, PKMixin, TimestampMixin):
    __tablename__ = "time_change_requests"
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))
    current_slot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("session_slots.id", ondelete="SET NULL"))
    new_slot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("session_slots.id", ondelete="SET NULL"))
    days: Mapped[list] = mapped_column(JSON, default=list)  # [0..6]
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    decision_remarks: Mapped[Optional[str]] = mapped_column(Text)
    requested_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    client = relationship("Client")
    student = relationship("Student")
    subscription = relationship("Subscription")
    current_slot = relationship("SessionSlot", foreign_keys=[current_slot_id])
    new_slot = relationship("SessionSlot", foreign_keys=[new_slot_id])
    decided_by = relationship("User", foreign_keys=[decided_by_id])


class TeacherChangeRequest(Base, PKMixin, TimestampMixin):
    __tablename__ = "teacher_change_requests"
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))
    current_teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    new_teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    decision_remarks: Mapped[Optional[str]] = mapped_column(Text)
    requested_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    client = relationship("Client")
    student = relationship("Student")
    subscription = relationship("Subscription")
    current_teacher = relationship("Teacher", foreign_keys=[current_teacher_id])
    new_teacher = relationship("Teacher", foreign_keys=[new_teacher_id])
    decided_by = relationship("User", foreign_keys=[decided_by_id])


class ReferredContact(Base, PKMixin, TimestampMixin):
    """Refer New Contact: a family recommends someone; approval creates a lead."""
    __tablename__ = "referred_contacts"
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    email: Mapped[Optional[str]] = mapped_column(String(200))
    contact_no: Mapped[Optional[str]] = mapped_column(String(50))
    description: Mapped[Optional[str]] = mapped_column(Text)
    reference_type: Mapped[str] = mapped_column(String(30), default="family")  # family | friend | colleague | community | other
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    lead_id: Mapped[Optional[int]] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"))
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    decision_remarks: Mapped[Optional[str]] = mapped_column(Text)

    client = relationship("Client")
    lead = relationship("Lead")
    decided_by = relationship("User", foreign_keys=[decided_by_id])


# --------------------------------------------------------------------------- Class management
class ClassArrangement(Base, PKMixin, TimestampMixin):
    """Substitute cover: classes of from_teacher between the dates are taken by to_teacher."""
    __tablename__ = "class_arrangements"
    from_teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)
    to_teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)
    from_date: Mapped[date] = mapped_column(Date, index=True)
    to_date: Mapped[date] = mapped_column(Date)
    session_category: Mapped[Optional[str]] = mapped_column(String(20))
    slot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("session_slots.id", ondelete="SET NULL"))
    reason: Mapped[Optional[str]] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active | inactive
    is_auto: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    from_teacher = relationship("Teacher", foreign_keys=[from_teacher_id])
    to_teacher = relationship("Teacher", foreign_keys=[to_teacher_id])
    slot = relationship("SessionSlot")


class RescheduleRequest(Base, PKMixin, TimestampMixin):
    """Reschedule with approval: old vs new working date / session / teacher."""
    __tablename__ = "reschedule_requests"
    session_id: Mapped[int] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), index=True)
    subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))
    old_date: Mapped[date] = mapped_column(Date)
    new_date: Mapped[date] = mapped_column(Date)
    old_start_time: Mapped[time] = mapped_column(Time)
    new_start_time: Mapped[time] = mapped_column(Time)
    old_teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    new_teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    reason: Mapped[Optional[str]] = mapped_column(String(200))
    comments: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    requested_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    new_session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("class_sessions.id", ondelete="SET NULL"))

    session = relationship("ClassSession", foreign_keys=[session_id])
    new_session = relationship("ClassSession", foreign_keys=[new_session_id])
    subscription = relationship("Subscription")
    old_teacher = relationship("Teacher", foreign_keys=[old_teacher_id])
    new_teacher = relationship("Teacher", foreign_keys=[new_teacher_id])
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])


class ClassQuery(Base, PKMixin, TimestampMixin):
    """Raised by a teacher during/after a class ("Family want to talk with manager", "Facing Tech Issue")."""
    __tablename__ = "class_queries"
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("class_sessions.id", ondelete="SET NULL"), index=True)
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"), index=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    query_type: Mapped[str] = mapped_column(String(60), default="Facing Tech Issue")
    detail: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending | closed
    response: Mapped[Optional[str]] = mapped_column(Text)
    closed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    session = relationship("ClassSession")
    teacher = relationship("Teacher")
    student = relationship("Student")


class ClassActivity(Base, PKMixin, TimestampMixin):
    """What was covered in a class (page number + remarks), manual or automatic."""
    __tablename__ = "class_activities"
    session_id: Mapped[int] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"), index=True)
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    activity_type: Mapped[str] = mapped_column(String(20), default="manual")  # manual | auto
    book_id: Mapped[Optional[int]] = mapped_column(ForeignKey("books.id", ondelete="SET NULL"))
    page_no: Mapped[Optional[str]] = mapped_column(String(40))
    remarks: Mapped[Optional[str]] = mapped_column(Text)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    session = relationship("ClassSession")
    student = relationship("Student")
    book = relationship("Book")


# --------------------------------------------------------------------------- Billing
class LedgerAddition(Base, PKMixin, TimestampMixin):
    """Manual ledger movement for a client (Teacher Gift, Leave Discount ...). Confirming posts a LedgerEntry."""
    __tablename__ = "ledger_additions"
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    currency_rate: Mapped[float] = mapped_column(Numeric(14, 6), default=1)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    lc_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)  # in base currency
    addition_type: Mapped[str] = mapped_column(String(40), default="Adjustment")
    effect: Mapped[str] = mapped_column(String(10), default="minus")  # add (client owes more) | minus (credit)
    addition_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending | confirmed | cancelled
    reference_employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    billing_rep_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    remarks: Mapped[Optional[str]] = mapped_column(Text)
    ledger_entry_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ledger_entries.id", ondelete="SET NULL"))
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    client = relationship("Client")
    reference_employee = relationship("Employee")
    billing_rep = relationship("User", foreign_keys=[billing_rep_id])


# --------------------------------------------------------------------------- Quality: call pipeline
class CallRecord(Base, PKMixin, TimestampMixin):
    """A class call recording pulled from Agent / Teams / Zoom, mapped to a class session for QA review."""
    __tablename__ = "call_records"
    source: Mapped[str] = mapped_column(String(10), default="ZOOM", index=True)  # AGENT | TEAMS | ZOOM
    platform: Mapped[Optional[str]] = mapped_column(String(40))
    source_name: Mapped[Optional[str]] = mapped_column(String(150))  # meeting topic / caller id
    external_id: Mapped[Optional[str]] = mapped_column(String(120))
    recording_date: Mapped[date] = mapped_column(Date, index=True)
    employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"), index=True)
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("class_sessions.id", ondelete="SET NULL"), index=True)
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=0)
    meeting_status: Mapped[str] = mapped_column(String(20), default="ended")  # ended | in_progress | missed
    recording_url: Mapped[Optional[str]] = mapped_column(String(500))
    review_state: Mapped[str] = mapped_column(String(20), default="unmapped", index=True)  # unmapped | mapped | queued | in_review | reviewed
    qa_review_id: Mapped[Optional[int]] = mapped_column(ForeignKey("qa_reviews.id", ondelete="SET NULL"))

    employee = relationship("Employee")
    teacher = relationship("Teacher")
    session = relationship("ClassSession")
    qa_review = relationship("QAReview", foreign_keys=[qa_review_id])


class QAReviewParameter(Base, PKMixin, TimestampMixin):
    """Rated parameters on a QA review form (Engagement, Tajweed Accuracy, Adab, Lesson Planning ...)."""
    __tablename__ = "qa_review_parameters"
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[Optional[str]] = mapped_column(String(200))
    weight: Mapped[float] = mapped_column(Float, default=1)
    max_rating: Mapped[int] = mapped_column(Integer, default=5)
    sort_no: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")


class QAIssueType(Base, PKMixin, TimestampMixin):
    __tablename__ = "qa_issue_types"
    name: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(20), default="normal")  # normal | critical
    description: Mapped[Optional[str]] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")
