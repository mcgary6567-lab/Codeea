"""One-off: append ERP-parity columns to existing models (run once; idempotent)."""
import io


def sub(path, old, new):
    s = io.open(path, encoding="utf-8").read()
    if new.strip() and new in s:
        print(f"  already applied: {path}")
        return
    assert old in s, (path, old[:60])
    s = s.replace(old, new, 1)
    io.open(path, "w", encoding="utf-8", newline="\n").write(s)
    print(f"  patched {path}")


sub("app/models/__init__.py",
    "from app.models.ops import *  # noqa: F401,F403\n",
    "from app.models.ops import *  # noqa: F401,F403\nfrom app.models.erp import *  # noqa: F401,F403\n")

# ---- people.py : Client
sub("app/models/people.py",
    '''    joined_at: Mapped[date] = mapped_column(Date, default=date.today)

    user = relationship("User", foreign_keys=[user_id])
    household = relationship("Household", back_populates="clients")
    students = relationship("Student", back_populates="client")
    billing_rep = relationship("User", foreign_keys=[billing_rep_id])
''',
    '''    joined_at: Mapped[date] = mapped_column(Date, default=date.today)
    # ERP parity (see docs/AUDIT_ACADEMICS.md 3.2)
    fee_recurrence: Mapped[str] = mapped_column(String(20), default="monthly")  # monthly | quarterly | half_yearly | yearly | per_class
    legacy_code: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    opening_balance: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    shift: Mapped[str] = mapped_column(String(20), default="night")  # morning | night (which staff shift serves the family)
    state: Mapped[Optional[str]] = mapped_column(String(80))
    referred_by_client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
    status_remarks: Mapped[Optional[str]] = mapped_column(String(200))
    academic_manager_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    academic_group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("client_academic_groups.id", ondelete="SET NULL"))
    lead_added_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    converted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    converted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    photo_path: Mapped[Optional[str]] = mapped_column(String(300))

    user = relationship("User", foreign_keys=[user_id])
    household = relationship("Household", back_populates="clients")
    students = relationship("Student", back_populates="client")
    billing_rep = relationship("User", foreign_keys=[billing_rep_id])
    academic_manager = relationship("User", foreign_keys=[academic_manager_id])
    referred_by = relationship("Client", remote_side="Client.id", foreign_keys=[referred_by_client_id])
    academic_group = relationship("ClientAcademicGroup")
''')

# ---- people.py : Student
sub("app/models/people.py",
    '''    notes: Mapped[Optional[str]] = mapped_column(Text)
    guardian_consent: Mapped[bool] = mapped_column(Boolean, default=False)

    client = relationship("Client", back_populates="students")
''',
    '''    notes: Mapped[Optional[str]] = mapped_column(Text)
    guardian_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    # ERP parity (see docs/AUDIT_ACADEMICS.md 3.4)
    email: Mapped[Optional[str]] = mapped_column(String(200))
    trial_days: Mapped[int] = mapped_column(Integer, default=3)
    legacy_code: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    drop_date: Mapped[Optional[date]] = mapped_column(Date)
    referred_by: Mapped[Optional[str]] = mapped_column(String(150))
    grade: Mapped[Optional[str]] = mapped_column(String(40))  # school grade / level label
    photo_path: Mapped[Optional[str]] = mapped_column(String(300))

    client = relationship("Client", back_populates="students")
''')

# ---- people.py : Employee
sub("app/models/people.py",
    '''    exit_date: Mapped[Optional[date]] = mapped_column(Date)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(200))

    user = relationship("User", foreign_keys=[user_id])
    department = relationship("Department")
''',
    '''    exit_date: Mapped[Optional[date]] = mapped_column(Date)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(200))
    father_name: Mapped[Optional[str]] = mapped_column(String(150))
    sort_no: Mapped[int] = mapped_column(Integer, default=0)  # "Change Staff Sorting": order in teacher lists

    user = relationship("User", foreign_keys=[user_id])
    department = relationship("Department")
''')

# ---- people.py : Leave
sub("app/models/people.py",
    '''    post_leave_absence_flagged: Mapped[bool] = mapped_column(Boolean, default=False)

    employee = relationship("Employee")
''',
    '''    post_leave_absence_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    leave_for_all: Mapped[bool] = mapped_column(Boolean, default=False)  # student leave applies to every student of the family
    apply_date: Mapped[Optional[date]] = mapped_column(Date)
    leave_detail: Mapped[Optional[str]] = mapped_column(Text)

    employee = relationship("Employee")
''')

# ---- academic.py : Course
sub("app/models/academic.py",
    '''    order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    divisions = relationship("Division", back_populates="course", order_by="Division.order")
''',
    '''    order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    course_type: Mapped[str] = mapped_column(String(40), default="Islamic Courses")  # Islamic Courses | Academics Tutoring
    fee: Mapped[float] = mapped_column(Numeric(12, 2), default=0)  # default monthly fee (base currency)
    attendance_required: Mapped[bool] = mapped_column(Boolean, default=True)
    curriculum_link: Mapped[Optional[str]] = mapped_column(String(300))

    divisions = relationship("Division", back_populates="course", order_by="Division.order")
''')

# ---- academic.py : Package
sub("app/models/academic.py",
    '''    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)

    course = relationship("Course", back_populates="packages")
''',
    '''    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)
    min_days: Mapped[int] = mapped_column(Integer, default=1)  # days per week the package allows
    max_days: Mapped[int] = mapped_column(Integer, default=5)

    course = relationship("Course", back_populates="packages")
''')

# ---- academic.py : Book
sub("app/models/academic.py",
    '''    description: Mapped[Optional[str]] = mapped_column(Text)
    order: Mapped[int] = mapped_column(Integer, default=0)

    course = relationship("Course", back_populates="books")
''',
    '''    description: Mapped[Optional[str]] = mapped_column(Text)
    order: Mapped[int] = mapped_column(Integer, default=0)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)  # Internal Books vs Public Books
    status: Mapped[str] = mapped_column(String(20), default="active")
    file_path: Mapped[Optional[str]] = mapped_column(String(300))

    course = relationship("Course", back_populates="books")
''')

# ---- academic.py : Evaluation
sub("app/models/academic.py",
    '''    academic_comment: Mapped[Optional[str]] = mapped_column(Text)
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    student = relationship("Student")
    teacher = relationship("Teacher")


class MonthlyTest''',
    '''    academic_comment: Mapped[Optional[str]] = mapped_column(Text)
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assessment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assessment_definitions.id", ondelete="SET NULL"))
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False)  # "Manual Evaluations" tab
    due_date: Mapped[Optional[date]] = mapped_column(Date)  # pending evaluations = past due without a score

    student = relationship("Student")
    teacher = relationship("Teacher")
    assessment = relationship("AssessmentDefinition")


class MonthlyTest''')

# ---- finance.py : Subscription
sub("app/models/finance.py",
    '''    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    client = relationship("Client")
    student = relationship("Student")
    package = relationship("Package")
    course = relationship("Course")
    teacher = relationship("Teacher")
    scholarship = relationship("Scholarship", foreign_keys=[scholarship_id])
''',
    '''    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    # ERP parity (see docs/AUDIT_ACADEMICS.md 3.5). Status vocabulary additionally allows
    # trial | regular | freeze | completed (labels in app.core.templating.STATUS_LABELS).
    slot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("session_slots.id", ondelete="SET NULL"), index=True)
    days_of_week: Mapped[list] = mapped_column(JSON, default=list)  # [0..6] Monday=0
    language: Mapped[str] = mapped_column(String(30), default="English")
    course_method: Mapped[str] = mapped_column(String(20), default="one_on_one")  # one_on_one | group
    session_category: Mapped[str] = mapped_column(String(20), default="30 Minutes")
    session_type: Mapped[str] = mapped_column(String(30), default="Job Time Session")
    trial_days: Mapped[int] = mapped_column(Integer, default=3)
    completion_date: Mapped[Optional[date]] = mapped_column(Date)
    remarks: Mapped[Optional[str]] = mapped_column(Text)
    schedule_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)  # schedules.id (no FK: avoids a cycle)
    supervisor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    books: Mapped[list] = mapped_column(JSON, default=list)  # course book ids chosen at creation
    follow_up_date: Mapped[Optional[date]] = mapped_column(Date, index=True)  # "Coming Follow Ups"

    client = relationship("Client")
    student = relationship("Student")
    package = relationship("Package")
    course = relationship("Course")
    teacher = relationship("Teacher")
    scholarship = relationship("Scholarship", foreign_keys=[scholarship_id])
    slot = relationship("SessionSlot")
    supervisor = relationship("User", foreign_keys=[supervisor_id])
''')

# ---- finance.py : Invoice
sub("app/models/finance.py",
    '''    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft | sent | partial | paid | overdue | void
    remarks: Mapped[Optional[str]] = mapped_column(Text)
''',
    '''    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft | pending | confirmed | partial | paid | overdue | cancelled (legacy: sent, void)
    remarks: Mapped[Optional[str]] = mapped_column(Text)
    subs_total: Mapped[float] = mapped_column(Numeric(12, 2), default=0)  # sum of subscription lines before additions
    subs_discount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    subs_tax: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    confirmed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(200))
    is_bulk: Mapped[bool] = mapped_column(Boolean, default=False)
''')

# ---- finance.py : Payment
sub("app/models/finance.py",
    '''    status: Mapped[str] = mapped_column(String(20), default="completed")  # pending | completed | failed | refunded
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
''',
    '''    status: Mapped[str] = mapped_column(String(20), default="completed")  # pending | confirmed | completed | failed | refunded | cancelled
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    # ERP "Receipts" fields (docs/AUDIT_ACADEMICS.md 3.7)
    receipt_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    receiver_name: Mapped[Optional[str]] = mapped_column(String(150))
    receiving_destination: Mapped[Optional[str]] = mapped_column(String(150))
    description: Mapped[Optional[str]] = mapped_column(String(250))
    category: Mapped[Optional[str]] = mapped_column(String(60))  # Stripe | PayPal | UBL | Meezan Bank | Wise | Cash ...
    beneficiary_account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("beneficiary_accounts.id", ondelete="SET NULL"))
    billing_rep_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
''')
sub("app/models/finance.py",
    '''    invoice = relationship("Invoice", back_populates="payments")
    client = relationship("Client")
    receipt = relationship("Receipt", back_populates="payment", uselist=False)
''',
    '''    invoice = relationship("Invoice", back_populates="payments")
    client = relationship("Client")
    receipt = relationship("Receipt", back_populates="payment", uselist=False)
    beneficiary_account = relationship("BeneficiaryAccount")
    billing_rep = relationship("User", foreign_keys=[billing_rep_id])
''')

# ---- scheduling.py : statuses, ClassSession, QAReview
sub("app/models/scheduling.py",
    'SESSION_STATUSES = ["pending", "started", "done", "missed", "absent", "leave", "cancelled", "rescheduled", "free"]',
    'SESSION_STATUSES = ["pending", "available", "started", "done", "missed", "absent", "leave", "cancelled", "rescheduled", "free"]')
sub("app/models/scheduling.py",
    '''    substitute_for_teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))

    schedule = relationship("Schedule", back_populates="sessions")
''',
    '''    substitute_for_teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    # ERP parity (docs/AUDIT_ACADEMICS.md 3.6)
    slot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("session_slots.id", ondelete="SET NULL"), index=True)
    subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True)
    teacher_available_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # "Teacher is Available" marker
    arrangement_id: Mapped[Optional[int]] = mapped_column(ForeignKey("class_arrangements.id", ondelete="SET NULL"))
    done_by_teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    activity_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # last class activity ("No Activity" highlight)

    schedule = relationship("Schedule", back_populates="sessions")
    slot = relationship("SessionSlot")
    subscription = relationship("Subscription")
    arrangement = relationship("ClassArrangement")
''')
sub("app/models/scheduling.py",
    '''    sample_type: Mapped[str] = mapped_column(String(20), default="random")  # random | risk_based | scheduled | complaint | re_evaluation
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | in_review | completed | approved
''',
    '''    sample_type: Mapped[str] = mapped_column(String(20), default="random")  # random | risk_based | scheduled | complaint | re_evaluation | call
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | in_review | completed | approved | flagged | rejected
    # ERP call-review fields (docs/AUDIT_ACADEMICS.md 3.9)
    call_record_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)  # call_records.id (no FK: avoids a cycle)
    overall_rating: Mapped[Optional[float]] = mapped_column(Float)  # 1-5 stars
    remarks: Mapped[Optional[str]] = mapped_column(Text)
    parameter_scores: Mapped[dict] = mapped_column(JSON, default=dict)  # {"Engagement": 4, "Tajweed Accuracy": 3, ...}
    issues: Mapped[list] = mapped_column(JSON, default=list)  # [{"type": "Adab", "critical": false, "note": "..."}]
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
''')

# ---- crm.py : Case, Feedback
sub("app/models/crm.py",
    '''    source: Mapped[str] = mapped_column(String(20), default="portal")  # portal | whatsapp | feedback | staff | phone

    client = relationship("Client")
    student = relationship("Student")
    teacher = relationship("Teacher")
    assigned_to = relationship("User", foreign_keys=[assigned_to_id])
''',
    '''    source: Mapped[str] = mapped_column(String(20), default="portal")  # portal | whatsapp | feedback | staff | phone
    # ERP "Complaints" request fields (docs/AUDIT_ACADEMICS.md 3.3)
    complaint_type: Mapped[Optional[str]] = mapped_column(String(60))  # Teacher | Timing | Billing | Technical | Behaviour | Other
    company_response: Mapped[Optional[str]] = mapped_column(Text)
    approval_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending | approved | rejected | cancelled

    client = relationship("Client")
    student = relationship("Student")
    teacher = relationship("Teacher")
    assigned_to = relationship("User", foreign_keys=[assigned_to_id])
''')
sub("app/models/crm.py",
    '''    is_confidential: Mapped[bool] = mapped_column(Boolean, default=False)  # staff eNPS → P&C and CEO only

    client = relationship("Client")
''',
    '''    is_confidential: Mapped[bool] = mapped_column(Boolean, default=False)  # staff eNPS → P&C and CEO only
    feedback_source: Mapped[str] = mapped_column(String(20), default="client_portal")  # manual | app | web_portal | client_portal
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("class_sessions.id", ondelete="SET NULL"))

    client = relationship("Client")
''')
print("done")
