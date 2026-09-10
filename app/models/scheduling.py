"""Scheduling, live classes, attendance, recordings, AI monitoring, QA, trials, calls."""
from datetime import datetime, date, time
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, Date, Time, Text, ForeignKey, JSON, Float, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, PKMixin, TimestampMixin

SESSION_STATUSES = ["pending", "started", "done", "missed", "absent", "leave", "cancelled", "rescheduled", "free"]


class Shift(Base, PKMixin, TimestampMixin):
    __tablename__ = "shifts"
    name: Mapped[str] = mapped_column(String(60))  # Morning, Evening, Night
    group: Mapped[str] = mapped_column(String(20), default="morning")  # morning | night
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    manager_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    supervisor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    manager = relationship("User", foreign_keys=[manager_id])
    supervisor = relationship("User", foreign_keys=[supervisor_id])


class Schedule(Base, PKMixin, TimestampMixin):
    """Recurring class schedule for a student with a teacher (30-minute slots on a 24h grid)."""
    __tablename__ = "schedules"
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)
    subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))
    course_id: Mapped[Optional[int]] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    days_of_week: Mapped[list] = mapped_column(JSON, default=list)  # [0..6] Monday=0
    start_time: Mapped[time] = mapped_column(Time)  # in org timezone (Asia/Karachi)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Karachi")
    start_date: Mapped[date] = mapped_column(Date, default=date.today)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    shift_id: Mapped[Optional[int]] = mapped_column(ForeignKey("shifts.id", ondelete="SET NULL"))
    room_name: Mapped[Optional[str]] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | paused | ended
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    student = relationship("Student")
    teacher = relationship("Teacher")
    course = relationship("Course")
    shift = relationship("Shift")
    sessions = relationship("ClassSession", back_populates="schedule")


class ClassSession(Base, PKMixin, TimestampMixin):
    __tablename__ = "class_sessions"
    schedule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schedules.id", ondelete="SET NULL"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[Optional[int]] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    date: Mapped[date] = mapped_column(Date, index=True)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    scheduled_start: Mapped[datetime] = mapped_column(DateTime, index=True)  # naive UTC-ish org time
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)
    room_name: Mapped[Optional[str]] = mapped_column(String(120))
    join_url: Mapped[Optional[str]] = mapped_column(String(400))
    teacher_joined_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    teacher_left_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    student_joined_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    student_left_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    actual_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    teacher_late_minutes: Mapped[int] = mapped_column(Integer, default=0)
    status_changed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status_reason: Mapped[Optional[str]] = mapped_column(Text)
    rescheduled_to_id: Mapped[Optional[int]] = mapped_column(ForeignKey("class_sessions.id", ondelete="SET NULL"))
    lesson_plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lesson_plans.id", ondelete="SET NULL"))
    lesson_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lessons.id", ondelete="SET NULL"))
    teacher_notes: Mapped[Optional[str]] = mapped_column(Text)
    student_feedback: Mapped[Optional[str]] = mapped_column(Text)
    student_rating: Mapped[Optional[int]] = mapped_column(Integer)
    reminder_sent_teacher: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_sent_student: Mapped[bool] = mapped_column(Boolean, default=False)
    substitute_for_teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))

    schedule = relationship("Schedule", back_populates="sessions")
    student = relationship("Student")
    teacher = relationship("Teacher", foreign_keys=[teacher_id])
    course = relationship("Course")
    lesson = relationship("Lesson")
    recording = relationship("Recording", back_populates="session", uselist=False)
    ai_analysis = relationship("AIClassAnalysis", back_populates="session", uselist=False)


class Attendance(Base, PKMixin, TimestampMixin):
    __tablename__ = "attendance"
    session_id: Mapped[int] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    date: Mapped[date] = mapped_column(Date, index=True)
    student_status: Mapped[str] = mapped_column(String(20), default="present")  # present | absent | late | leave
    teacher_status: Mapped[str] = mapped_column(String(20), default="present")  # present | absent | late
    marked_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    remarks: Mapped[Optional[str]] = mapped_column(Text)

    session = relationship("ClassSession")
    student = relationship("Student")


class Recording(Base, PKMixin, TimestampMixin):
    __tablename__ = "recordings"
    session_id: Mapped[int] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), unique=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(500))
    external_url: Mapped[Optional[str]] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(20), default="platform")  # platform | zoom | upload
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="available")  # processing | available | analysed | expired | deleted
    retention_until: Mapped[Optional[date]] = mapped_column(Date)
    transcript: Mapped[Optional[str]] = mapped_column(Text)
    access_count: Mapped[int] = mapped_column(Integer, default=0)

    session = relationship("ClassSession", back_populates="recording")
    access_logs = relationship("RecordingAccessLog", back_populates="recording")


class RecordingAccessLog(Base, PKMixin):
    """Safeguarding: every recording access is logged (Module 47)."""
    __tablename__ = "recording_access_logs"
    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    purpose: Mapped[Optional[str]] = mapped_column(String(200))
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    accessed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    recording = relationship("Recording", back_populates="access_logs")
    user = relationship("User")


class AIClassAnalysis(Base, PKMixin, TimestampMixin):
    """Module 25: AI class monitoring output for one session."""
    __tablename__ = "ai_class_analyses"
    session_id: Mapped[int] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), unique=True)
    recording_id: Mapped[Optional[int]] = mapped_column(ForeignKey("recordings.id", ondelete="SET NULL"))
    model_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ai_model_runs.id", ondelete="SET NULL"))
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"), index=True)
    camera_presence_pct: Mapped[float] = mapped_column(Float, default=0)
    punctuality_minutes: Mapped[int] = mapped_column(Integer, default=0)  # + late, - early
    duration_compliance_pct: Mapped[float] = mapped_column(Float, default=0)
    active_teaching_pct: Mapped[float] = mapped_column(Float, default=0)
    idle_pct: Mapped[float] = mapped_column(Float, default=0)
    student_engagement_score: Mapped[float] = mapped_column(Float, default=0)
    curriculum_coverage_pct: Mapped[float] = mapped_column(Float, default=0)
    tone_flags: Mapped[list] = mapped_column(JSON, default=list)
    conduct_flags: Mapped[list] = mapped_column(JSON, default=list)
    contact_exchange_detected: Mapped[bool] = mapped_column(Boolean, default=False)  # anti-poaching signal
    overall_score: Mapped[float] = mapped_column(Float, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    recommended_feedback: Mapped[Optional[str]] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(10), default="low")
    review_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | overridden | false_positive
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    review_note: Mapped[Optional[str]] = mapped_column(Text)

    session = relationship("ClassSession", back_populates="ai_analysis")
    teacher = relationship("Teacher")


class QAReview(Base, PKMixin, TimestampMixin):
    __tablename__ = "qa_reviews"
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("class_sessions.id", ondelete="SET NULL"), index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    ai_analysis_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ai_class_analyses.id", ondelete="SET NULL"))
    sample_type: Mapped[str] = mapped_column(String(20), default="random")  # random | risk_based | scheduled | complaint | re_evaluation
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | in_review | completed | approved
    tajweed_score: Mapped[Optional[float]] = mapped_column(Float)
    methodology_score: Mapped[Optional[float]] = mapped_column(Float)
    engagement_score: Mapped[Optional[float]] = mapped_column(Float)
    punctuality_score: Mapped[Optional[float]] = mapped_column(Float)
    environment_score: Mapped[Optional[float]] = mapped_column(Float)
    professionalism_score: Mapped[Optional[float]] = mapped_column(Float)
    overall_score: Mapped[Optional[float]] = mapped_column(Float)
    strengths: Mapped[Optional[str]] = mapped_column(Text)
    weaknesses: Mapped[Optional[str]] = mapped_column(Text)
    comments: Mapped[Optional[str]] = mapped_column(Text)
    teacher_feedback_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    re_evaluation_of_id: Mapped[Optional[int]] = mapped_column(ForeignKey("qa_reviews.id", ondelete="SET NULL"))
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    session = relationship("ClassSession")
    teacher = relationship("Teacher")
    reviewer = relationship("User", foreign_keys=[reviewer_id])
    corrective_actions = relationship("CorrectiveAction", back_populates="qa_review")


class CorrectiveAction(Base, PKMixin, TimestampMixin):
    __tablename__ = "corrective_actions"
    qa_review_id: Mapped[Optional[int]] = mapped_column(ForeignKey("qa_reviews.id", ondelete="SET NULL"))
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(Text)
    assigned_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | in_progress | closed | overdue
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    closure_note: Mapped[Optional[str]] = mapped_column(Text)

    qa_review = relationship("QAReview", back_populates="corrective_actions")
    teacher = relationship("Teacher")


class Trial(Base, PKMixin, TimestampMixin):
    __tablename__ = "trials"
    lead_id: Mapped[Optional[int]] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), index=True)
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    course_id: Mapped[Optional[int]] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("class_sessions.id", ondelete="SET NULL"))
    student_name: Mapped[str] = mapped_column(String(150))
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(20), default="requested")  # requested | scheduled | attended | no_show | converted | lost
    outcome: Mapped[Optional[str]] = mapped_column(String(200))
    teacher_feedback: Mapped[Optional[str]] = mapped_column(Text)
    follow_up_date: Mapped[Optional[date]] = mapped_column(Date)
    follow_up_count: Mapped[int] = mapped_column(Integer, default=0)
    converted_subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))
    closer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    lead = relationship("Lead")
    teacher = relationship("Teacher")
    course = relationship("Course")
    student = relationship("Student")
    session = relationship("ClassSession")


class TeacherMatch(Base, PKMixin, TimestampMixin):
    """Module 44: teacher-match at enrolment with logged reason and override."""
    __tablename__ = "teacher_matches"
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    recommended_teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    chosen_teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    ranked_candidates: Mapped[list] = mapped_column(JSON, default=list)  # [{"teacher_id":1,"score":92,"reasons":[...]}]
    match_score: Mapped[Optional[float]] = mapped_column(Float)
    match_reason: Mapped[Optional[str]] = mapped_column(Text)
    overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    override_reason: Mapped[Optional[str]] = mapped_column(Text)
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    survived_90_days: Mapped[Optional[bool]] = mapped_column(Boolean)

    student = relationship("Student")
    recommended_teacher = relationship("Teacher", foreign_keys=[recommended_teacher_id])
    chosen_teacher = relationship("Teacher", foreign_keys=[chosen_teacher_id])


class ReminderLog(Base, PKMixin):
    __tablename__ = "reminder_logs"
    reminder_type: Mapped[str] = mapped_column(String(40), index=True)  # class_teacher, class_student, leave_start, leave_end, post_leave_absence, teacher_change, invoice_due, payment_failed
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    entity_type: Mapped[Optional[str]] = mapped_column(String(40))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(20), default="in_app")
    status: Mapped[str] = mapped_column(String(20), default="sent")
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CallLog(Base, PKMixin):
    """In-platform masked calling (Module 31)."""
    __tablename__ = "call_logs"
    caller_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    callee_type: Mapped[str] = mapped_column(String(20))  # client | lead | teacher | employee
    callee_id: Mapped[int] = mapped_column(Integer)
    callee_masked: Mapped[Optional[str]] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(20), default="voip")  # voip | whatsapp
    direction: Mapped[str] = mapped_column(String(10), default="outbound")
    status: Mapped[str] = mapped_column(String(20), default="completed")  # completed | missed | failed | busy
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    caller = relationship("User")


class SafeguardingFlag(Base, PKMixin, TimestampMixin):
    """Module 47: anti-poaching / conduct signals. Visible to CEO/GM only by default."""
    __tablename__ = "safeguarding_flags"
    flag_type: Mapped[str] = mapped_column(String(40))  # contact_exchange | off_platform_contact | social_discovery | conduct | recording_access
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("class_sessions.id", ondelete="SET NULL"))
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    evidence: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="ai")  # ai | manual | system
    ai_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ai_model_runs.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | investigating | confirmed | dismissed
    visibility: Mapped[str] = mapped_column(String(20), default="ceo_only")
    handled_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution: Mapped[Optional[str]] = mapped_column(Text)

    teacher = relationship("Teacher")
    student = relationship("Student")
