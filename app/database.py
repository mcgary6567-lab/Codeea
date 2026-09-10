"""SQLAlchemy engine / session factory. SQLite locally, PostgreSQL in production."""
from datetime import datetime
from typing import Generator

from fastapi import Request

from sqlalchemy import create_engine, event, DateTime, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session

from app.config import settings, BASE_DIR

connect_args = {}
if settings.is_sqlite:
    (BASE_DIR / "data").mkdir(exist_ok=True)
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, pool_pre_ping=True, future=True)

if settings.is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PKMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)


def get_db(request: Request = None) -> Generator[Session, None, None]:
    """One session per request. The DBSessionMiddleware in app.main stores it on request.state.db."""
    existing = getattr(request.state, "db", None) if request is not None else None
    if existing is not None:
        yield existing
        return
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401  (registers all tables)
    Base.metadata.create_all(bind=engine)
