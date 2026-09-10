"""People: clients/parents, students, employees, teachers, HR, payroll."""
from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, Date, Text, ForeignKey, JSON, Float, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, PKMixin, TimestampMixin


# --------------------------------------------------------------------------- Clients & Students
class Household(Base, PKMixin, TimestampMixin):
    __tablename__ = "households"
    name: Mapped[str] = mapped_column(String(150))
    country: Mapped[Optional[str]] = mapped_column(String(80))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    clients = relationship("Client", back_populates="household")


class Client(Base, PKMixin, TimestampMixin):
    """Parent / guardian / paying customer."""
    __tablename__ = "clients"
    client_code: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # C-00001 (masked identity)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), unique=True)
    household_id: Mapped[Optional[int]] = mapped_column(ForeignKey("households.id", ondelete="SET NULL"))
    full_name: Mapped[str] = mapped_column(String(150))
    email: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    whatsapp: Mapped[Optional[str]] = mapped_column(String(50))
    country: Mapped[str] = mapped_column(String(80), default="United Kingdom")
    city: Mapped[Optional[str]] = mapped_column(String(80))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/London")
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    address: Mapped[Optional[str]] = mapped_column(String(300))
    relationship_to_student: Mapped[Optional[str]] = mapped_column(String(50))  # father, mother, self, guardian
    status: Mapped[str] = mapped_column(String(20), default="active")  # trial | active | inactive | churned
    lead_id: Mapped[Optional[int]] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"))
    source: Mapped[Optional[str]] = mapped_column(String(60))
    billing_rep_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    whatsapp_opt_in: Mapped[bool] = mapped_column(Boolean, default=True)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    referral_code: Mapped[Optional[str]] = mapped_column(String(20), unique=True)
    is_ambassador: Mapped[bool] = mapped_column(Boolean, default=False)
    ambassador_invited_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    ghl_contact_id: Mapped[Optional[str]] = mapped_column(String(80))
    joined_at: Mapped[date] = mapped_column(Date, default=date.today)

    user = relationship("User", foreign_keys=[user_id])
    household = relationship("Household", back_populates="clients")
    students = relationship("Student", back_populates="client")
    billing_rep = relationship("User", foreign_keys=[billing_rep_id])

    @property
    def masked_phone(self) -> str:
        p = self.phone or ""
        return ("*" * max(0, len(p) - 3)) + p[-3:] if p else "-"


class Student(Base, PKMixin, TimestampMixin):
    __tablename__ = "students"
    student_code: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # S-00001
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), unique=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    full_name: Mapped[str] = mapped_column(String(150))
    gender: Mapped[str] = mapped_column(String(10), default="male")
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date)
    age: Mapped[Optional[int]] = mapped_column(Integer)
    is_minor: Mapped[bool] = mapped_column(Boolean, default=True)
    course_id: Mapped[Optional[int]] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    division_id: Mapped[Optional[int]] = mapped_column(ForeignKey("divisions.id", ondelete="SET NULL"))
    level: Mapped[Optional[str]] = mapped_column(String(60))
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="trial", index=True)  # trial | active | frozen | cancelled | graduated | free
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/London")
    preferred_language: Mapped[str] = mapped_column(String(20), default="English")
    join_date: Mapped[date] = mapped_column(Date, default=date.today)
    cancelled_at: Mapped[Optional[date]] = mapped_column(Date)
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(200))
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_level: Mapped[str] = mapped_column(String(10), default="low")  # low | medium | high
    risk_factors: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_computed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    current_lesson_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lessons.id", ondelete="SET NULL"))
    sabaq_position: Mapped[Optional[str]] = mapped_column(String(120))  # e.g. "Juz 1, Surah Al-Baqarah 1-20"
    dor_quota_met: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    guardian_consent: Mapped[bool] = mapped_column(Boolean, default=False)

    client = relationship("Client", back_populates="students")
    user = relationship("User", foreign_keys=[user_id])
    course = relationship("Course")
    division = relationship("Division")
    teacher = relationship("Teacher", back_populates="students", foreign_keys=[teacher_id])


# --------------------------------------------------------------------------- Employees & Teachers
class Employee(Base, PKMixin, TimestampMixin):
    __tablename__ = "employees"
    employee_code: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # E-00001
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), unique=True)
    full_name: Mapped[str] = mapped_column(String(150))
    designation: Mapped[str] = mapped_column(String(100))
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    branch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("branches.id", ondelete="SET NULL"))
    manager_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    gender: Mapped[str] = mapped_column(String(10), default="male")
    email: Mapped[Optional[str]] = mapped_column(String(200))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    cnic: Mapped[Optional[str]] = mapped_column(String(30))  # sensitive – masked in UI
    address: Mapped[Optional[str]] = mapped_column(String(300))
    join_date: Mapped[date] = mapped_column(Date, default=date.today)
    probation_end: Mapped[Optional[date]] = mapped_column(Date)
    employment_type: Mapped[str] = mapped_column(String(20), default="full_time")  # full_time | part_time | contract
    shift: Mapped[str] = mapped_column(String(20), default="morning")  # morning | evening | night
    shift_start: Mapped[Optional[str]] = mapped_column(String(5))  # "09:00"
    shift_end: Mapped[Optional[str]] = mapped_column(String(5))
    base_salary: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    salary_band: Mapped[Optional[str]] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | probation | on_leave | resigned | terminated
    is_teacher: Mapped[bool] = mapped_column(Boolean, default=False)
    background_check_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | verified | failed
    background_check_date: Mapped[Optional[date]] = mapped_column(Date)
    mfa_enforced: Mapped[bool] = mapped_column(Boolean, default=True)
    device_name: Mapped[Optional[str]] = mapped_column(String(80))  # OQC-DEPT-NNN convention
    documents: Mapped[dict] = mapped_column(JSON, default=dict)
    exit_date: Mapped[Optional[date]] = mapped_column(Date)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(200))

    user = relationship("User", foreign_keys=[user_id])
    department = relationship("Department")
    branch = relationship("Branch")
    manager = relationship("Employee", remote_side="Employee.id")
    teacher = relationship("Teacher", back_populates="employee", uselist=False)


class Teacher(Base, PKMixin, TimestampMixin):
    __tablename__ = "teachers"
    teacher_code: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # T-00001
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), unique=True)
    employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), unique=True)
    full_name: Mapped[str] = mapped_column(String(150))
    gender: Mapped[str] = mapped_column(String(10), default="male")
    qualifications: Mapped[Optional[str]] = mapped_column(Text)
    courses: Mapped[list] = mapped_column(JSON, default=list)  # course codes they can teach
    languages: Mapped[list] = mapped_column(JSON, default=lambda: ["English", "Urdu"])
    shift: Mapped[str] = mapped_column(String(20), default="evening")
    availability: Mapped[dict] = mapped_column(JSON, default=dict)  # {"mon": ["18:00-23:00"], ...}
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Karachi")
    max_classes_per_day: Mapped[int] = mapped_column(Integer, default=12)
    supervisor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    per_class_rate: Mapped[float] = mapped_column(Numeric(10, 2), default=0)  # teacher cost per class (PKR)
    grade: Mapped[str] = mapped_column(String(2), default="B")  # A | B | C
    grade_computed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    grade_inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    qa_score_avg: Mapped[float] = mapped_column(Float, default=0)
    punctuality_score: Mapped[float] = mapped_column(Float, default=100)
    retention_rate: Mapped[float] = mapped_column(Float, default=100)
    rating: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | on_leave | inactive
    bio: Mapped[Optional[str]] = mapped_column(Text)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)  # background check gate for live classes

    user = relationship("User", foreign_keys=[user_id])
    employee = relationship("Employee", back_populates="teacher")
    supervisor = relationship("User", foreign_keys=[supervisor_id])
    students = relationship("Student", back_populates="teacher", foreign_keys="Student.teacher_id")


# --------------------------------------------------------------------------- HR
class RecruitmentRequest(Base, PKMixin, TimestampMixin):
    __tablename__ = "recruitment_requests"
    title: Mapped[str] = mapped_column(String(120))
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    requested_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    positions: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | approved | in_progress | filled | cancelled
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    target_date: Mapped[Optional[date]] = mapped_column(Date)

    department = relationship("Department")
    candidates = relationship("Candidate", back_populates="request")


class Candidate(Base, PKMixin, TimestampMixin):
    __tablename__ = "candidates"
    request_id: Mapped[Optional[int]] = mapped_column(ForeignKey("recruitment_requests.id", ondelete="SET NULL"))
    full_name: Mapped[str] = mapped_column(String(150))
    email: Mapped[Optional[str]] = mapped_column(String(200))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    gender: Mapped[Optional[str]] = mapped_column(String(10))
    applied_for: Mapped[str] = mapped_column(String(120))
    source: Mapped[Optional[str]] = mapped_column(String(60))
    stage: Mapped[str] = mapped_column(String(30), default="applied")  # applied | screening | interview | demo | offer | hired | rejected
    score: Mapped[Optional[float]] = mapped_column(Float)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    resume_path: Mapped[Optional[str]] = mapped_column(String(300))
    hired_employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))

    request = relationship("RecruitmentRequest", back_populates="candidates")
    interviews = relationship("Interview", back_populates="candidate")


class Interview(Base, PKMixin, TimestampMixin):
    __tablename__ = "interviews"
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"))
    interviewer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime)
    interview_type: Mapped[str] = mapped_column(String(30), default="screening")  # screening | technical | demo_class | final
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    score: Mapped[Optional[float]] = mapped_column(Float)
    feedback: Mapped[Optional[str]] = mapped_column(Text)

    candidate = relationship("Candidate", back_populates="interviews")
    interviewer = relationship("User")


class OnboardingTask(Base, PKMixin, TimestampMixin):
    __tablename__ = "onboarding_tasks"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(150))
    category: Mapped[str] = mapped_column(String(40), default="general")  # accounts | documents | training | equipment | policy
    status: Mapped[str] = mapped_column(String(20), default="pending")
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    assigned_to_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    employee = relationship("Employee")


class ProvisioningRecord(Base, PKMixin, TimestampMixin):
    """Module 49: one-action onboarding / offboarding provisioning."""
    __tablename__ = "provisioning_records"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(20))  # onboard | offboard
    items: Mapped[list] = mapped_column(JSON, default=list)  # [{"system":"workspace","status":"done"}, ...]
    status: Mapped[str] = mapped_column(String(20), default="completed")
    performed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    employee = relationship("Employee")


class HRAttendance(Base, PKMixin, TimestampMixin):
    __tablename__ = "hr_attendance"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    session: Mapped[str] = mapped_column(String(10), default="am")  # am | pm (twice-daily attendance)
    check_in: Mapped[Optional[datetime]] = mapped_column(DateTime)
    check_out: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="present")  # present | absent | late | leave | half_day | holiday
    late_minutes: Mapped[int] = mapped_column(Integer, default=0)
    correction_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    correction_reason: Mapped[Optional[str]] = mapped_column(Text)
    correction_status: Mapped[Optional[str]] = mapped_column(String(20))  # pending | approved | rejected
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    ip: Mapped[Optional[str]] = mapped_column(String(64))

    employee = relationship("Employee")


class Leave(Base, PKMixin, TimestampMixin):
    """Leave for employees (person_type=employee) and students (person_type=student)."""
    __tablename__ = "leaves"
    person_type: Mapped[str] = mapped_column(String(10), index=True)  # employee | student
    employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    leave_type: Mapped[str] = mapped_column(String(30), default="casual")  # casual | sick | annual | emergency | vacation | exam
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected | cancelled
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    requested_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    substitute_teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    reminder_sent_start: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_sent_end: Mapped[bool] = mapped_column(Boolean, default=False)
    post_leave_absence_flagged: Mapped[bool] = mapped_column(Boolean, default=False)

    employee = relationship("Employee")
    student = relationship("Student")
    approved_by = relationship("User", foreign_keys=[approved_by_id])


class Violation(Base, PKMixin, TimestampMixin):
    __tablename__ = "violations"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    violation_type: Mapped[str] = mapped_column(String(60))  # late, absent, misconduct, policy, missed_class
    severity: Mapped[str] = mapped_column(String(20), default="minor")  # minor | major | critical
    description: Mapped[Optional[str]] = mapped_column(Text)
    action_taken: Mapped[Optional[str]] = mapped_column(String(200))
    reported_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    date: Mapped[date] = mapped_column(Date, default=date.today)
    status: Mapped[str] = mapped_column(String(20), default="open")
    deduction_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

    employee = relationship("Employee")


class Grievance(Base, PKMixin, TimestampMixin):
    """Confidential HR grievance channel — bypasses department heads."""
    __tablename__ = "grievances"
    employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    submitted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=False)
    category: Mapped[str] = mapped_column(String(60), default="workplace")
    subject: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | investigating | resolved | closed
    handler_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution: Mapped[Optional[str]] = mapped_column(Text)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    sla_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class SalaryAdvance(Base, PKMixin, TimestampMixin):
    __tablename__ = "salary_advances"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    reason: Mapped[Optional[str]] = mapped_column(Text)
    installments: Mapped[int] = mapped_column(Integer, default=1)
    remaining: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected | paid | settled
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    employee = relationship("Employee")


class Bonus(Base, PKMixin, TimestampMixin):
    __tablename__ = "bonuses"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    bonus_type: Mapped[str] = mapped_column(String(40), default="performance")  # performance | referral | eid | retention | other
    reason: Mapped[Optional[str]] = mapped_column(Text)
    period: Mapped[Optional[str]] = mapped_column(String(7))  # YYYY-MM
    status: Mapped[str] = mapped_column(String(20), default="pending")
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    employee = relationship("Employee")


class SalaryStructure(Base, PKMixin, TimestampMixin):
    __tablename__ = "salary_structures"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), unique=True)
    basic: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    allowances: Mapped[dict] = mapped_column(JSON, default=dict)  # {"internet": 2000, "medical": 1500}
    deductions: Mapped[dict] = mapped_column(JSON, default=dict)  # {"tax": 0, "provident_fund": 0}
    per_class_rate: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    absence_deduction_per_day: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    late_deduction_per_instance: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    effective_from: Mapped[date] = mapped_column(Date, default=date.today)

    employee = relationship("Employee")


class PayrollRun(Base, PKMixin, TimestampMixin):
    __tablename__ = "payroll_runs"
    period: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | pending_approval | approved | paid
    total_gross: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    total_deductions: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    total_net: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    generated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    payslips = relationship("Payslip", back_populates="payroll_run", cascade="all, delete-orphan")


class Payslip(Base, PKMixin, TimestampMixin):
    __tablename__ = "payslips"
    payroll_run_id: Mapped[int] = mapped_column(ForeignKey("payroll_runs.id", ondelete="CASCADE"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    basic: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    class_pay: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    classes_taught: Mapped[int] = mapped_column(Integer, default=0)
    allowances: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    bonus: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    deductions: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    advance_deduction: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    attendance_deduction: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    gross: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    net: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    pdf_path: Mapped[Optional[str]] = mapped_column(String(300))

    payroll_run = relationship("PayrollRun", back_populates="payslips")
    employee = relationship("Employee")


class TrainingAssignment(Base, PKMixin, TimestampMixin):
    """Ustaadh Lab — teacher development & promotion gates (Module 46)."""
    __tablename__ = "training_assignments"
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(150))
    category: Mapped[str] = mapped_column(String(40), default="tajweed")  # tajweed | methodology | engagement | technology | conduct
    description: Mapped[Optional[str]] = mapped_column(Text)
    assigned_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="assigned")  # assigned | in_progress | completed | failed
    score: Mapped[Optional[float]] = mapped_column(Float)
    is_promotion_gate: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    teacher = relationship("Teacher")


class DevelopmentPlan(Base, PKMixin, TimestampMixin):
    """Six-month growth journeys, AI fluency tracking (Section 13)."""
    __tablename__ = "development_plans"
    employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(150))
    goals: Mapped[list] = mapped_column(JSON, default=list)  # [{"goal":..., "month":1, "status":...}]
    ai_fluency_level: Mapped[int] = mapped_column(Integer, default=1)  # 1-5
    start_date: Mapped[date] = mapped_column(Date, default=date.today)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    employee = relationship("Employee")
    department = relationship("Department")
