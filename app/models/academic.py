"""Academic: courses, curriculum, lesson plans, evaluations, monthly tests, certificates."""
from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, Date, Text, ForeignKey, JSON, Float, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, PKMixin, TimestampMixin


class Course(Base, PKMixin, TimestampMixin):
    __tablename__ = "courses"
    code: Mapped[str] = mapped_column(String(20), unique=True)  # QAIDA, NAZRA, HIFZ, TAJWEED, TARJUMA
    name: Mapped[str] = mapped_column(String(120))
    arabic_name: Mapped[Optional[str]] = mapped_column(String(120))
    urdu_name: Mapped[Optional[str]] = mapped_column(String(120))
    description: Mapped[Optional[str]] = mapped_column(Text)
    default_session_minutes: Mapped[int] = mapped_column(Integer, default=30)
    completion_target_months: Mapped[Optional[int]] = mapped_column(Integer)
    order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    divisions = relationship("Division", back_populates="course", order_by="Division.order")
    books = relationship("Book", back_populates="course", order_by="Book.order")
    packages = relationship("Package", back_populates="course")


class Division(Base, PKMixin, TimestampMixin):
    """Level / division within a course (e.g. Hifz Juz 1-5)."""
    __tablename__ = "divisions"
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[Optional[str]] = mapped_column(Text)
    order: Mapped[int] = mapped_column(Integer, default=0)
    expected_weeks: Mapped[Optional[int]] = mapped_column(Integer)

    course = relationship("Course", back_populates="divisions")


class Package(Base, PKMixin, TimestampMixin):
    __tablename__ = "packages"
    name: Mapped[str] = mapped_column(String(120))
    course_id: Mapped[Optional[int]] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    sessions_per_week: Mapped[int] = mapped_column(Integer, default=5)
    session_minutes: Mapped[int] = mapped_column(Integer, default=30)
    price: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    country: Mapped[Optional[str]] = mapped_column(String(80))  # country-specific pricing
    billing_cycle: Mapped[str] = mapped_column(String(20), default="monthly")
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)

    course = relationship("Course", back_populates="packages")


class CurriculumVersion(Base, PKMixin, TimestampMixin):
    __tablename__ = "curriculum_versions"
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(20), default="1.0")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    published_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    course = relationship("Course")


class Book(Base, PKMixin, TimestampMixin):
    __tablename__ = "books"
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(150))
    arabic_title: Mapped[Optional[str]] = mapped_column(String(150))
    description: Mapped[Optional[str]] = mapped_column(Text)
    order: Mapped[int] = mapped_column(Integer, default=0)

    course = relationship("Course", back_populates="books")
    chapters = relationship("Chapter", back_populates="book", order_by="Chapter.order", cascade="all, delete-orphan")


class Chapter(Base, PKMixin, TimestampMixin):
    __tablename__ = "chapters"
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(150))
    arabic_title: Mapped[Optional[str]] = mapped_column(String(150))
    order: Mapped[int] = mapped_column(Integer, default=0)

    book = relationship("Book", back_populates="chapters")
    lessons = relationship("Lesson", back_populates="chapter", order_by="Lesson.order", cascade="all, delete-orphan")


class Lesson(Base, PKMixin, TimestampMixin):
    __tablename__ = "lessons"
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(150))
    arabic_text: Mapped[Optional[str]] = mapped_column(Text)  # Quranic text with tashkeel
    translation: Mapped[Optional[str]] = mapped_column(Text)
    objectives: Mapped[Optional[str]] = mapped_column(Text)
    tajweed_notes: Mapped[Optional[str]] = mapped_column(Text)
    expected_minutes: Mapped[int] = mapped_column(Integer, default=30)
    order: Mapped[int] = mapped_column(Integer, default=0)
    surah_number: Mapped[Optional[int]] = mapped_column(Integer)
    ayah_from: Mapped[Optional[int]] = mapped_column(Integer)
    ayah_to: Mapped[Optional[int]] = mapped_column(Integer)

    chapter = relationship("Chapter", back_populates="lessons")


class StudentProgress(Base, PKMixin, TimestampMixin):
    __tablename__ = "student_progress"
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="not_started")  # not_started | in_progress | completed | revision
    progress_type: Mapped[str] = mapped_column(String(10), default="sabaq")  # sabaq (new) | sabqi (recent) | dor (revision)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    score: Mapped[Optional[float]] = mapped_column(Float)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    student = relationship("Student")
    lesson = relationship("Lesson")


class LessonPlan(Base, PKMixin, TimestampMixin):
    __tablename__ = "lesson_plans"
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"), index=True)
    session_id: Mapped[Optional[int]] = mapped_column(
        # use_alter: lesson_plans <-> class_sessions reference each other.
        ForeignKey("class_sessions.id", ondelete="SET NULL", use_alter=True, name="fk_lesson_plans_session_id"))
    plan_date: Mapped[date] = mapped_column(Date, index=True)
    plan_type: Mapped[str] = mapped_column(String(20), default="daily")  # daily | weekly
    lesson_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lessons.id", ondelete="SET NULL"))
    planned_content: Mapped[str] = mapped_column(Text)
    delivered_content: Mapped[Optional[str]] = mapped_column(Text)
    sabaq: Mapped[Optional[str]] = mapped_column(String(200))
    sabqi: Mapped[Optional[str]] = mapped_column(String(200))
    dor: Mapped[Optional[str]] = mapped_column(String(200))
    teacher_notes: Mapped[Optional[str]] = mapped_column(Text)
    next_objectives: Mapped[Optional[str]] = mapped_column(Text)
    ai_recommendation: Mapped[Optional[str]] = mapped_column(Text)
    ai_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ai_model_runs.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="planned")  # planned | delivered | partially_delivered | not_delivered
    variance_pct: Mapped[Optional[float]] = mapped_column(Float)  # planned vs delivered
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    review_comment: Mapped[Optional[str]] = mapped_column(Text)

    student = relationship("Student")
    teacher = relationship("Teacher")
    lesson = relationship("Lesson")


class Evaluation(Base, PKMixin, TimestampMixin):
    __tablename__ = "evaluations"
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    evaluation_type: Mapped[str] = mapped_column(String(30), default="manual")  # manual | weekly | monthly | level_completion | trial
    date: Mapped[date] = mapped_column(Date, default=date.today)
    score: Mapped[Optional[float]] = mapped_column(Float)
    max_score: Mapped[float] = mapped_column(Float, default=100)
    result: Mapped[str] = mapped_column(String(10), default="pending")  # pass | fail | pending
    criteria: Mapped[dict] = mapped_column(JSON, default=dict)  # {"tajweed": 8, "fluency": 7, "memorisation": 9}
    teacher_comment: Mapped[Optional[str]] = mapped_column(Text)
    academic_comment: Mapped[Optional[str]] = mapped_column(Text)
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    student = relationship("Student")
    teacher = relationship("Teacher")


class MonthlyTest(Base, PKMixin, TimestampMixin):
    """Module 41: Monthly Test & Result-Card automation."""
    __tablename__ = "monthly_tests"
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    teacher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teachers.id", ondelete="SET NULL"))
    period: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    generated_from: Mapped[dict] = mapped_column(JSON, default=dict)  # curriculum position snapshot
    questions: Mapped[list] = mapped_column(JSON, default=list)  # [{"item":..., "max":10, "score":null}]
    score: Mapped[Optional[float]] = mapped_column(Float)
    max_score: Mapped[float] = mapped_column(Float, default=100)
    percentage: Mapped[Optional[float]] = mapped_column(Float)
    previous_percentage: Mapped[Optional[float]] = mapped_column(Float)
    improvement_pct: Mapped[Optional[float]] = mapped_column(Float)
    grade: Mapped[Optional[str]] = mapped_column(String(5))
    teacher_remarks: Mapped[Optional[str]] = mapped_column(Text)
    teacher_remarks_urdu: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="generated")  # generated | scored | card_generated | delivered
    result_card_path: Mapped[Optional[str]] = mapped_column(String(300))
    card_generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    delivery_channels: Mapped[list] = mapped_column(JSON, default=list)
    scored_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    student = relationship("Student")
    teacher = relationship("Teacher")


class DorSchedule(Base, PKMixin, TimestampMixin):
    """Spaced-repetition revision schedule generated after each monthly test."""
    __tablename__ = "dor_schedules"
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    monthly_test_id: Mapped[Optional[int]] = mapped_column(ForeignKey("monthly_tests.id", ondelete="SET NULL"))
    items: Mapped[list] = mapped_column(JSON, default=list)  # [{"content":..., "due":"YYYY-MM-DD", "done":false}]
    quota: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    quota_met: Mapped[bool] = mapped_column(Boolean, default=False)
    override_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    override_reason: Mapped[Optional[str]] = mapped_column(Text)
    override_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    student = relationship("Student")


class Certificate(Base, PKMixin, TimestampMixin):
    __tablename__ = "certificates"
    certificate_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[Optional[int]] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    issued_at: Mapped[date] = mapped_column(Date, default=date.today)
    issued_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    generation: Mapped[str] = mapped_column(String(10), default="manual")  # manual | automatic
    pdf_path: Mapped[Optional[str]] = mapped_column(String(300))
    verification_count: Mapped[int] = mapped_column(Integer, default=0)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    student = relationship("Student")
    course = relationship("Course")


class LessonAnnotation(Base, PKMixin, TimestampMixin):
    """Shared Arabic lesson view: synchronized annotations (Module 28)."""
    __tablename__ = "lesson_annotations"
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"))
    lesson_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    word_index: Mapped[Optional[int]] = mapped_column(Integer)
    annotation_type: Mapped[str] = mapped_column(String(30), default="highlight")  # highlight | mistake | tajweed | note
    color: Mapped[Optional[str]] = mapped_column(String(20))
    note: Mapped[Optional[str]] = mapped_column(Text)
