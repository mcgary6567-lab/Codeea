"""Values that SQLite stores happily but PostgreSQL rejects.

9 Oct 2026: the Render build failed with "integer out of range" because a class left running for hours gave its
simulated recording a size above 2^31 bytes. SQLite ignores integer widths, so the local suite never saw it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import BigInteger

from app.database import SessionLocal
from app.models.core import BackupRecord, FileAsset
from app.models.hr_erp import Attachment
from app.models.scheduling import ClassSession, Recording
from app.services import qa

INT32_MAX = 2**31 - 1


def test_file_size_columns_are_64_bit():
    for model in (Recording, BackupRecord, FileAsset, Attachment):
        assert isinstance(model.__table__.c.size_bytes.type, BigInteger), model.__name__


def test_a_class_left_running_does_not_make_an_impossible_recording():
    db = SessionLocal()
    try:
        s = (db.query(ClassSession).filter(ClassSession.status == "done", ~ClassSession.recording.has())
             .order_by(ClassSession.id).first())
        assert s is not None, "the seed should leave done classes without a recording"
        s.teacher_joined_at = datetime.utcnow() - timedelta(hours=9)
        s.actual_duration_minutes = 9 * 60  # the teacher forgot to end the class
        rec = qa.ingest_recording(db, s, None)
        assert rec.duration_seconds <= ((s.duration_minutes or 30) + 60) * 60
        assert rec.size_bytes <= INT32_MAX, "even a capped recording should fit; the column is 64-bit regardless"
    finally:
        db.rollback()
        db.close()
