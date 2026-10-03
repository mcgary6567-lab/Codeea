"""The Schedule Summary Report draws the ERP's grid: teachers down, session slots across, students in the cells."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.scheduling import Schedule
from app.services import classes as class_svc


@pytest.fixture(scope="module")
def admin() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": "admin@oqc.local", "password": "Admin@12345"}, follow_redirects=False)
    assert r.status_code == 303
    return c


def test_report_places_every_active_schedule_in_its_teacher_and_slot():
    db = SessionLocal()
    try:
        report = class_svc.schedule_summary(db, "30 Minutes", None)
        assert report["slot_count"] == 48
        placed = {}
        for row in report["rows"]:
            for cell in row["cells"]:
                for e in cell["entries"]:
                    placed[e["schedule"].id] = (row["teacher"].id, cell["slot"].start_time)
                    assert e["student_code"] and e["student_name"]
                    assert e["kind"] in {"regular", "trial", "group", "freeze"}
            assert row["busy"] + row["free"] == report["slot_count"]
        active = db.query(Schedule).filter(Schedule.status == "active").all()
        shown_teachers = {row["teacher"].id for row in report["rows"]}
        for sch in active:
            if sch.teacher_id in shown_teachers:
                teacher_id, slot_start = placed[sch.id]
                assert teacher_id == sch.teacher_id
                assert (slot_start.hour, slot_start.minute) == (sch.start_time.hour, sch.start_time.minute)
    finally:
        db.close()


def test_a_45_minute_grid_still_places_30_minute_starts():
    db = SessionLocal()
    try:
        report = class_svc.schedule_summary(db, "45 Minutes", None)
        assert report["slot_count"] > 0
        n = sum(len(c["entries"]) for row in report["rows"] for c in row["cells"])
        assert n == db.query(Schedule).filter(Schedule.status == "active", Schedule.teacher_id.in_([r["teacher"].id for r in report["rows"]] or [-1])).count()
    finally:
        db.close()


def test_page_shows_students_in_the_grid(admin):
    r = admin.get("/classes/schedule-summary")
    assert r.status_code == 200
    body = r.text
    assert "Schedule Summary Report" in body and "Session Category" in body and "Legend:" in body
    db = SessionLocal()
    try:
        sch = db.query(Schedule).filter(Schedule.status == "active").first()
        assert f"{sch.student.student_code}-{sch.student.full_name}" in body
        assert sch.student.client.full_name in body
    finally:
        db.close()
    r = admin.get("/classes/schedule-summary?session_category=45 Minutes&employee_id=2")
    assert r.status_code == 200
