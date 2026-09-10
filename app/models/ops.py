"""Operations: tasks/projects, KPIs, transformation OS, decisions, reports, migration."""
from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, Date, Text, ForeignKey, JSON, Float, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, PKMixin, TimestampMixin


class Project(Base, PKMixin, TimestampMixin):
    __tablename__ = "projects"
    name: Mapped[str] = mapped_column(String(150))
    code: Mapped[Optional[str]] = mapped_column(String(20))
    description: Mapped[Optional[str]] = mapped_column(Text)
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="active")  # planning | active | on_hold | completed | cancelled
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)

    department = relationship("Department")
    owner = relationship("User")
    tasks = relationship("Task", back_populates="project")
    sprints = relationship("Sprint", back_populates="project")
    milestones = relationship("Milestone", back_populates="project")


class Sprint(Base, PKMixin, TimestampMixin):
    __tablename__ = "sprints"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    goal: Mapped[Optional[str]] = mapped_column(Text)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="planned")  # planned | active | completed

    project = relationship("Project", back_populates="sprints")


class Milestone(Base, PKMixin, TimestampMixin):
    __tablename__ = "milestones"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(150))
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | achieved | missed
    achieved_at: Mapped[Optional[date]] = mapped_column(Date)

    project = relationship("Project", back_populates="milestones")


class Task(Base, PKMixin, TimestampMixin):
    __tablename__ = "tasks"
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), index=True)
    sprint_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sprints.id", ondelete="SET NULL"))
    milestone_id: Mapped[Optional[int]] = mapped_column(ForeignKey("milestones.id", ondelete="SET NULL"))
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    creator_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    priority: Mapped[str] = mapped_column(String(10), default="medium", index=True)  # low | medium | high | urgent
    status: Mapped[str] = mapped_column(String(20), default="todo", index=True)  # todo | in_progress | review | done | cancelled
    due_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    estimate_hours: Mapped[Optional[float]] = mapped_column(Float)
    recurrence: Mapped[Optional[str]] = mapped_column(String(20))  # daily | weekly | monthly | None
    depends_on_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    kpi_id: Mapped[Optional[int]] = mapped_column(ForeignKey("kpis.id", ondelete="SET NULL"))
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    escalated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    assessment_score: Mapped[Optional[int]] = mapped_column(Integer)  # 1-5 quality assessment on completion
    entity_type: Mapped[Optional[str]] = mapped_column(String(40))  # link to any record (case, lead, student...)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)

    project = relationship("Project", back_populates="tasks")
    assignee = relationship("User", foreign_keys=[assignee_id])
    creator = relationship("User", foreign_keys=[creator_id])
    department = relationship("Department")
    depends_on = relationship("Task", remote_side="Task.id")
    comments = relationship("TaskComment", back_populates="task", cascade="all, delete-orphan")


class TaskComment(Base, PKMixin):
    __tablename__ = "task_comments"
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task = relationship("Task", back_populates="comments")
    user = relationship("User")


class KPI(Base, PKMixin, TimestampMixin):
    __tablename__ = "kpis"
    name: Mapped[str] = mapped_column(String(150))
    code: Mapped[str] = mapped_column(String(60), unique=True)
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    role_slug: Mapped[Optional[str]] = mapped_column(String(60))  # teacher | supervisor | manager | hr | ...
    description: Mapped[Optional[str]] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(20), default="%")  # % | count | currency | minutes | score
    target: Mapped[Optional[float]] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(10), default="higher")  # higher | lower
    frequency: Mapped[str] = mapped_column(String(20), default="monthly")  # daily | weekly | monthly
    formula_key: Mapped[Optional[str]] = mapped_column(String(60))  # system-computed KPIs reference a formula key
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    department = relationship("Department")
    values = relationship("KPIValue", back_populates="kpi", order_by="KPIValue.period")


class KPIValue(Base, PKMixin):
    __tablename__ = "kpi_values"
    kpi_id: Mapped[int] = mapped_column(ForeignKey("kpis.id", ondelete="CASCADE"), index=True)
    period: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM or YYYY-WW or YYYY-MM-DD
    value: Mapped[float] = mapped_column(Float)
    target: Mapped[Optional[float]] = mapped_column(Float)
    entity_type: Mapped[Optional[str]] = mapped_column(String(40))  # teacher | department | supervisor
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    entered_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(20), default="system")  # system | manual
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    kpi = relationship("KPI", back_populates="values")


class TransformationItem(Base, PKMixin, TimestampMixin):
    """OS Master Tracker / System build tracker (Section 13)."""
    __tablename__ = "transformation_items"
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    system_name: Mapped[str] = mapped_column(String(150))
    description: Mapped[Optional[str]] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), default="alpha")  # planned | alpha | beta | full_launch
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    target_date: Mapped[Optional[date]] = mapped_column(Date)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    department = relationship("Department")
    owner = relationship("User")


class TransitionRecord(Base, PKMixin, TimestampMixin):
    __tablename__ = "transition_records"
    role_title: Mapped[str] = mapped_column(String(120))
    from_employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    to_employee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    handover_notes: Mapped[Optional[str]] = mapped_column(Text)
    checklist: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="planned")  # planned | in_progress | completed
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    completed_at: Mapped[Optional[date]] = mapped_column(Date)


class TrajectoryMeeting(Base, PKMixin, TimestampMixin):
    """Weekly trajectory meeting: agenda, decisions, owners, actions."""
    __tablename__ = "trajectory_meetings"
    meeting_date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(150), default="Weekly Trajectory Meeting")
    agenda: Mapped[Optional[str]] = mapped_column(Text)
    attendees: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    chaired_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="scheduled")

    decisions = relationship("Decision", back_populates="meeting")


class Decision(Base, PKMixin, TimestampMixin):
    """Module 48: written-record discipline — every consequential decision has an owner and rationale."""
    __tablename__ = "decisions"
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), default="operational")  # operational | academic | financial | hr | strategic
    rationale: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    meeting_id: Mapped[Optional[int]] = mapped_column(ForeignKey("trajectory_meetings.id", ondelete="SET NULL"))
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="decided")  # proposed | decided | implemented | reversed
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    outcome: Mapped[Optional[str]] = mapped_column(Text)
    entity_type: Mapped[Optional[str]] = mapped_column(String(40))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)

    owner = relationship("User", foreign_keys=[owner_id])
    meeting = relationship("TrajectoryMeeting", back_populates="decisions")


class DepartmentScorecard(Base, PKMixin, TimestampMixin):
    __tablename__ = "department_scorecards"
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id", ondelete="CASCADE"))
    period: Mapped[str] = mapped_column(String(7), index=True)
    score: Mapped[float] = mapped_column(Float, default=0)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    highlights: Mapped[Optional[str]] = mapped_column(Text)
    risks: Mapped[Optional[str]] = mapped_column(Text)
    submitted_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="draft")

    department = relationship("Department")


class DailyReport(Base, PKMixin, TimestampMixin):
    """Morning / afternoon structured reporting with configurable deadlines (report-by-chat-to-zero)."""
    __tablename__ = "daily_reports"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    report_date: Mapped[date] = mapped_column(Date, index=True)
    slot: Mapped[str] = mapped_column(String(10), default="morning")  # morning | afternoon
    content: Mapped[dict] = mapped_column(JSON, default=dict)  # structured fields
    summary: Mapped[Optional[str]] = mapped_column(Text)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_late: Mapped[bool] = mapped_column(Boolean, default=False)

    user = relationship("User")


class ReportRun(Base, PKMixin):
    __tablename__ = "report_runs"
    name: Mapped[str] = mapped_column(String(120))
    report_type: Mapped[str] = mapped_column(String(60))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    format: Mapped[str] = mapped_column(String(10), default="xlsx")
    file_path: Mapped[Optional[str]] = mapped_column(String(300))
    generated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    row_count: Mapped[int] = mapped_column(Integer, default=0)


class MigrationJob(Base, PKMixin, TimestampMixin):
    __tablename__ = "migration_jobs"
    name: Mapped[str] = mapped_column(String(150))
    entity: Mapped[str] = mapped_column(String(40))  # clients | students | leads | teachers | invoices | payments
    source_system: Mapped[str] = mapped_column(String(60), default="legacy_erp")
    file_path: Mapped[Optional[str]] = mapped_column(String(300))
    field_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="uploaded")  # uploaded | validated | imported | failed | reconciled
    records_total: Mapped[int] = mapped_column(Integer, default=0)
    records_imported: Mapped[int] = mapped_column(Integer, default=0)
    records_skipped: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_found: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
