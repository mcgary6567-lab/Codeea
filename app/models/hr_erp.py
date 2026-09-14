"""Human Resource models that mirror the college's existing ERP.

See docs/AUDIT_HUMAN_RESOURCE.md for the page-by-page audit these serve. The pieces are wired to each
other: a violation is raised against a ViolationType and inherits its standard penalty; a bonus against
a BonusType; a leave request draws down a LeaveEntitlement; an approved AttendanceChangeRequest rewrites
the HRAttendance row it names; a hired JobApplication becomes a Candidate and then an Employee.

Our confidential grievance channel (app.models.people.Grievance) is kept as it is. StaffComplaint here is
the ERP's open complaint list; a complaint marked secret is still restricted to the People & Culture
roles, so nothing that belongs in the confidential channel becomes readable by a line manager.
"""
from datetime import datetime, date, time
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, Date, Time, Text, ForeignKey, JSON, Float, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, PKMixin, TimestampMixin

APPROVAL_STATUSES = ["pending", "approved", "rejected", "cancelled"]
EMPLOYEE_TYPES = ["Academics", "Admin", "Marketing"]
DESIGNATIONS = [
    "Academic Excellence", "Academic Manager", "Accountant", "Admission Incharge", "Ayyah", "CEO", "COO",
    "Director", "Finance Head", "IT Head", "Manager", "Observer", "P&C Head", "QA Observer", "Supervisor",
    "Supporting Staff", "Teacher On-Site", "Teacher Remote", "Trainer",
]
EMPLOYEE_REQUEST_TYPES = [
    "Salary Certificate", "Experience Letter", "Equipment", "Shift Change", "Designation Review",
    "Document Correction", "Resignation", "Other",
]
STAFF_COMPLAINT_TYPES = ["HR", "Academics", "Management", "Facility", "Payroll", "Other"]
PAYROLL_STATUSES = ["pending", "generated", "posted", "cancelled"]
APPLICATION_STATUSES = [
    "applied", "on_hold", "initial_selected", "pre_selected", "marked_1st_interview", "marked_2nd_interview",
    "marked_final_interview", "selected", "rejected", "hired",
]


# --------------------------------------------------------------------------- catalogues
class ViolationType(Base, PKMixin, TimestampMixin):
    """An offence and the fine it normally carries, so violations are consistent between managers."""
    __tablename__ = "violation_types"
    description: Mapped[str] = mapped_column(Text)
    description_urdu: Mapped[Optional[str]] = mapped_column(Text)
    penalty_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    severity: Mapped[str] = mapped_column(String(20), default="minor")  # minor | major | critical
    status: Mapped[str] = mapped_column(String(20), default="active")
    sort_no: Mapped[int] = mapped_column(Integer, default=0)


class BonusType(Base, PKMixin, TimestampMixin):
    __tablename__ = "bonus_types"
    description: Mapped[str] = mapped_column(Text)
    description_urdu: Mapped[Optional[str]] = mapped_column(Text)
    bonus_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")
    sort_no: Mapped[int] = mapped_column(Integer, default=0)


class Holiday(Base, PKMixin, TimestampMixin):
    """A non-working day. Attendance generation skips these, and leave spanning one does not consume it."""
    __tablename__ = "holidays"
    name: Mapped[str] = mapped_column(String(120))
    holiday_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date)  # set for a multi-day holiday
    shift_group: Mapped[str] = mapped_column(String(20), default="all")  # all | morning | night
    is_paid: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    notes: Mapped[Optional[str]] = mapped_column(Text)

    @property
    def last_day(self) -> date:
        return self.end_date or self.holiday_date


class Grade(Base, PKMixin, TimestampMixin):
    """A salary grade and the allowances that come with it."""
    __tablename__ = "grades"
    name: Mapped[str] = mapped_column(String(60))
    description: Mapped[Optional[str]] = mapped_column(Text)
    basic_min: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    basic_max: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    allowances: Mapped[dict] = mapped_column(JSON, default=dict)  # {"internet": 2000, "medical": 1500}
    status: Mapped[str] = mapped_column(String(20), default="active")


class HRDownload(Base, PKMixin, TimestampMixin):
    """A document published to staff (HR policy, forms)."""
    __tablename__ = "hr_downloads"
    description: Mapped[str] = mapped_column(String(200))
    link: Mapped[Optional[str]] = mapped_column(String(500))
    file_path: Mapped[Optional[str]] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(40), default="policy")
    status: Mapped[str] = mapped_column(String(20), default="active")


class Attachment(Base, PKMixin, TimestampMixin):
    """A file attached to any record, kept in one place so every module can use it."""
    __tablename__ = "attachments"
    entity_type: Mapped[str] = mapped_column(String(40), index=True)  # employee | candidate | payroll | client | ...
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(200))
    file_name: Mapped[Optional[str]] = mapped_column(String(200))
    file_path: Mapped[Optional[str]] = mapped_column(String(300))
    link: Mapped[Optional[str]] = mapped_column(String(500))
    content_type: Mapped[Optional[str]] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="active")

    uploaded_by = relationship("User")


# --------------------------------------------------------------------------- entitlement and attendance
class LeaveEntitlement(Base, PKMixin, TimestampMixin):
    """How many days of a leave type an employee holds this year, and how many are used."""
    __tablename__ = "leave_entitlements"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    leave_type: Mapped[str] = mapped_column(String(30), default="casual")
    total_assigned: Mapped[float] = mapped_column(Float, default=0)
    consumed: Mapped[float] = mapped_column(Float, default=0)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active")
    notes: Mapped[Optional[str]] = mapped_column(Text)

    employee = relationship("Employee")

    @property
    def remaining(self) -> float:
        return round(float(self.total_assigned or 0) - float(self.consumed or 0), 2)


class AttendanceChangeRequest(Base, PKMixin, TimestampMixin):
    """A member of staff asks to correct a punch. Approving rewrites the attendance row it names."""
    __tablename__ = "attendance_change_requests"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    attendance_id: Mapped[Optional[int]] = mapped_column(ForeignKey("hr_attendance.id", ondelete="SET NULL"))
    attendance_date: Mapped[date] = mapped_column(Date, index=True)
    session: Mapped[str] = mapped_column(String(10), default="am")
    old_status: Mapped[Optional[str]] = mapped_column(String(20))
    old_check_in: Mapped[Optional[time]] = mapped_column(Time)
    old_check_out: Mapped[Optional[time]] = mapped_column(Time)
    new_status: Mapped[str] = mapped_column(String(20), default="present")
    new_check_in: Mapped[Optional[time]] = mapped_column(Time)
    new_check_out: Mapped[Optional[time]] = mapped_column(Time)
    user_remarks: Mapped[Optional[str]] = mapped_column(Text)
    hr_remarks: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    request_date: Mapped[date] = mapped_column(Date, default=date.today)
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    employee = relationship("Employee")
    decided_by = relationship("User")


class ProgressNote(Base, PKMixin, TimestampMixin):
    """What an employee did on a working day, and what their manager thought of it."""
    __tablename__ = "progress_notes"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    working_date: Mapped[date] = mapped_column(Date, index=True)
    detail: Mapped[str] = mapped_column(Text)
    manager_rating: Mapped[Optional[int]] = mapped_column(Integer)  # 1-5
    manager_comment: Mapped[Optional[str]] = mapped_column(Text)
    rated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    rated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    employee = relationship("Employee")
    rated_by = relationship("User", foreign_keys=[rated_by_id])


# --------------------------------------------------------------------------- staff requests
class EmployeeRequest(Base, PKMixin, TimestampMixin):
    """A general request from a member of staff to HR."""
    __tablename__ = "employee_requests"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    request_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    request_type: Mapped[str] = mapped_column(String(60), default="Other")
    description: Mapped[Optional[str]] = mapped_column(Text)
    hr_remarks: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    employee = relationship("Employee")
    decided_by = relationship("User")


class StaffComplaint(Base, PKMixin, TimestampMixin):
    """The ERP's open staff complaint list.

    A complaint marked secret stays visible only to the People & Culture roles. Anything a member of
    staff wants kept from their own management chain belongs in the confidential grievance channel
    (app.models.people.Grievance), which this does not replace.
    """
    __tablename__ = "staff_complaints"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    complaint_type: Mapped[str] = mapped_column(String(40), default="HR")
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    admin_response: Mapped[Optional[str]] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    about_employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    employee = relationship("Employee", foreign_keys=[employee_id])
    about_employee = relationship("Employee", foreign_keys=[about_employee_id])
    decided_by = relationship("User")


# --------------------------------------------------------------------------- recruitment
class InterviewPanel(Base, PKMixin, TimestampMixin):
    __tablename__ = "interview_panels"
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[Optional[str]] = mapped_column(Text)
    member_ids: Mapped[list] = mapped_column(JSON, default=list)  # user ids
    status: Mapped[str] = mapped_column(String(20), default="active")


class JobApplication(Base, PKMixin, TimestampMixin):
    """An application against a vacancy, moving through the ERP's ten-step pipeline."""
    __tablename__ = "job_applications"
    request_id: Mapped[Optional[int]] = mapped_column(ForeignKey("recruitment_requests.id", ondelete="SET NULL"), index=True)
    candidate_id: Mapped[Optional[int]] = mapped_column(ForeignKey("candidates.id", ondelete="SET NULL"))
    application_type: Mapped[str] = mapped_column(String(20), default="profile")  # profile | non_profile
    full_name: Mapped[str] = mapped_column(String(150))
    father_name: Mapped[Optional[str]] = mapped_column(String(150))
    gender: Mapped[Optional[str]] = mapped_column(String(10))
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    application_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    nic_number: Mapped[Optional[str]] = mapped_column(String(30))
    cell_no: Mapped[Optional[str]] = mapped_column(String(50))
    email: Mapped[Optional[str]] = mapped_column(String(200))
    qualification: Mapped[Optional[str]] = mapped_column(String(200))
    experience_years: Mapped[Optional[float]] = mapped_column(Float)
    expected_salary: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    city: Mapped[Optional[str]] = mapped_column(String(80))
    source: Mapped[Optional[str]] = mapped_column(String(60))
    panel_id: Mapped[Optional[int]] = mapped_column(ForeignKey("interview_panels.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(30), default="applied", index=True)
    remarks: Mapped[Optional[str]] = mapped_column(Text)
    hired_employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    resume_path: Mapped[Optional[str]] = mapped_column(String(300))

    request = relationship("RecruitmentRequest")
    candidate = relationship("Candidate")
    department = relationship("Department")
    panel = relationship("InterviewPanel")
