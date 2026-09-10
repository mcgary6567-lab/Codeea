"""Smoke test for the scheduling / live-ops / QA / AI-monitoring module.

Run:  .venv/Scripts/python.exe tests/test_scheduling_module.py
ASCII output only (Windows console is cp1252).
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def login(email: str, password: str) -> TestClient:
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", data={"username": email, "password": password})
    assert r.status_code in (200, 302, 303), f"login {email} -> {r.status_code}"
    return c


def check(client: TestClient, method: str, url: str, expect, data=None, label: str = "") -> object:
    global CHECKS
    CHECKS += 1
    if method == "GET":
        r = client.get(url)
    else:
        r = client.post(url, data=data or {})
    ok = r.status_code in (expect if isinstance(expect, (list, tuple, set)) else [expect])
    if not ok:
        body = ""
        if r.status_code >= 500 or r.status_code == 200:
            body = r.text[:400].replace("\n", " ")
        FAILURES.append(f"{method} {url} -> {r.status_code} (expected {expect}) {label} {body}")
    return r


def main() -> None:
    db = SessionLocal()
    from app.models.people import Student, Teacher
    from app.models.scheduling import (AIClassAnalysis, ClassSession, QAReview, Recording, SafeguardingFlag, Schedule)

    today = date.today()
    sess = db.query(ClassSession).filter(ClassSession.status == "done").first()
    pending = db.query(ClassSession).filter(ClassSession.status == "pending").order_by(ClassSession.id.desc()).first()
    sch = db.query(Schedule).filter(Schedule.status == "active").first()
    rec = db.query(Recording).first()
    an = db.query(AIClassAnalysis).first()
    review = db.query(QAReview).filter(QAReview.status.in_(["queued", "in_review"])).first()
    flag = db.query(SafeguardingFlag).first()
    teacher = db.query(Teacher).filter(Teacher.is_verified.is_(True)).first()
    student = db.query(Student).filter(Student.status == "active").first()
    free_student = None
    scheduled_ids = {s for (s,) in db.query(Schedule.student_id)}
    for s in db.query(Student).filter(Student.status.in_(["active", "trial"])).all():
        if s.id not in scheduled_ids:
            free_student = s
            break
    conflicting = db.query(Schedule).filter(Schedule.status == "active").first()
    db.close()

    # ------------------------------------------------------------------ admin
    a = login("admin@oqc.local", "Admin@12345")
    pages = [
        "/dashboard", f"/dashboard?day={today}", "/dashboard/partial/live",
        "/schedules", "/schedules?view=teacher", "/schedules?view=student",
        f"/schedules?view=student&student_id={student.id}", "/schedules?free=1",
        "/schedules/shifts", "/schedules/new", "/schedules/bulk-teacher-change",
        f"/schedules/{sch.id}", f"/schedules/{sch.id}/edit",
        "/classes", f"/classes?date={today}", "/classes?status=missed&date=",
        f"/classes?date_from={today - timedelta(days=7)}&date_to={today}",
        "/classes/day", f"/classes/day?date={today}",
        f"/classes/{sess.id}", f"/classroom/{pending.id}" if pending else "/classes",
        "/supervisor", "/supervisor/partial/board", "/supervisor/teachers", "/supervisor/weekly",
        "/leaves/students", "/leaves/students?status=pending", "/leaves/students/post-leave",
        "/recordings", f"/recordings/{rec.id}" if rec else "/recordings", "/recordings/ingest",
        "/ai-monitoring", f"/ai-monitoring/{an.id}" if an else "/ai-monitoring", "/ai-monitoring/teachers",
        "/qa", "/qa?status=queued", f"/qa/{review.id}" if review else "/qa",
        "/qa/corrective-actions", "/qa/trends",
        "/safeguarding", f"/safeguarding/{flag.id}" if flag else "/safeguarding",
        "/safeguarding/recording-access", "/safeguarding/unverified", "/safeguarding/policy",
        "/api/v1/classes/sessions?limit=5", "/api/v1/classes/schedules?limit=5",
        f"/api/v1/classes/sessions/{sess.id}", "/api/v1/classes/counters",
    ]
    for p in pages:
        check(a, "GET", p, 200)

    # ---- forms
    # conflicting schedule (same teacher + slot as an existing active schedule) -> error flash
    if conflicting:
        payload = {
            "student_id": str(free_student.id if free_student else conflicting.student_id),
            "teacher_id": str(conflicting.teacher_id),
            "start_time": conflicting.start_time.strftime("%H:%M"),
            "duration": "30", "start_date": str(today),
        }
        for d in (conflicting.days_of_week or [0]):
            payload[f"day_{d}"] = "1"
        r = check(a, "POST", "/schedules/new", 303, payload, "conflicting schedule")
        loc = r.headers.get("location", "")
        if "/schedules/new" not in loc:
            FAILURES.append(f"conflicting schedule was accepted (redirected to {loc})")
        else:
            print("  conflict detection: rejected as expected")

    # clean schedule (Sunday 23:30 is outside every seeded slot, so it must be accepted)
    target_student = free_student or student
    if target_student and teacher:
        payload = {"student_id": str(target_student.id), "teacher_id": str(teacher.id), "start_time": "23:30",
                   "duration": "30", "start_date": str(today), "day_6": "1"}
        r = check(a, "POST", "/schedules/new", 303, payload, "clean schedule")
        loc = r.headers.get("location", "")
        if "/schedules/new" in loc:
            FAILURES.append(f"clean schedule was rejected (redirected to {loc})")
        else:
            print(f"  clean schedule accepted -> {loc}")

    forms = [
        (f"/schedules/{sch.id}/generate", {"days": "14"}),
        (f"/schedules/{sch.id}/status", {"status": "paused", "reason": "Test pause"}),
        (f"/schedules/{sch.id}/status", {"status": "active", "reason": "Test resume"}),
        ("/schedules/shifts/new", {"name": "QA Test Shift", "group": "morning", "start_time": "05:00", "end_time": "09:00"}),
        ("/classes/generate", {"start_date": str(today), "end_date": str(today + timedelta(days=7))}),
        (f"/classes/{pending.id}/status", {"status": "started"}) if pending else None,
        (f"/classes/{pending.id}/notes", {"teacher_notes": "Automated smoke test note."}) if pending else None,
        ("/classes/bulk-status", {"session_ids": str(pending.id), "status": "cancelled", "reason": "Smoke test bulk"}) if pending else None,
        (f"/classroom/{sess.id}/lesson-plan", {"planned_content": "Revision of Surah Al-Fatiha", "sabaq": "p.12",
                                               "sabqi": "p.10-11", "dor": "Juz 1", "teacher_notes": "Good progress."}),
        ("/leaves/students/post-leave/scan", {}),
        ("/qa/sample/random", {"n": "3"}),
        ("/qa/sample/risk", {"n": "3"}),
        ("/recordings/ingest", {"session_id": str(sess.id)}),
    ]
    for item in forms:
        if item:
            check(a, "POST", item[0], 303, item[1])

    if rec:
        check(a, "POST", f"/recordings/{rec.id}/access", 303, {"purpose": "QA sampling review (smoke test)"})
        check(a, "POST", f"/recordings/{rec.id}/analyse", 303, {})
    if an:
        check(a, "POST", f"/ai-monitoring/{an.id}/review", 303, {"action": "approved", "note": "Reviewed by a human."})
        check(a, "POST", f"/ai-monitoring/{an.id}/feedback", 303, {"message": "Please keep the camera on."})
    if review:
        scores = {f"{k}_score": "8" for k in ("tajweed", "methodology", "engagement", "punctuality", "environment", "professionalism")}
        check(a, "POST", f"/qa/{review.id}/complete", 303, {**scores, "strengths": "Clear recitation.",
                                                            "weaknesses": "Pacing.", "comments": "Smoke test."})
        check(a, "POST", f"/qa/{review.id}/feedback", 303, {"message": "Thank you for the class."})
        check(a, "POST", f"/qa/{review.id}/corrective-action", 303,
              {"description": "Complete the pacing module.", "due_date": str(today + timedelta(days=10))})
        check(a, "POST", f"/qa/{review.id}/re-evaluate", 303, {})
    if flag:
        check(a, "POST", f"/safeguarding/{flag.id}/status", 303,
              {"status": "investigating", "resolution": "Assigned to the HOD for review."})
    check(a, "POST", "/safeguarding/report", 303,
          {"flag_type": "conduct", "severity": "medium", "evidence": "Smoke-test concern report."})
    if teacher:
        check(a, "POST", "/qa/schedule-review", 303, {"teacher_id": str(teacher.id)})
        check(a, "POST", "/supervisor/call", 303, {"callee_type": "teacher", "callee_id": str(teacher.id),
                                                   "notes": "Checked availability."})

    # ------------------------------------------------------------------ supervisor scoping
    s = login("supervisor@oqc.local", "Super@123")
    for p in ["/dashboard", "/supervisor", "/supervisor/partial/board", "/supervisor/teachers", "/supervisor/weekly",
              "/classes", "/schedules", "/leaves/students"]:
        check(s, "GET", p, 200)
    r = s.get("/supervisor/teachers")
    db = SessionLocal()
    from app.models.core import User as U
    sup = db.query(U).filter(U.email == "supervisor@oqc.local").first()
    mine = db.query(Teacher).filter(Teacher.supervisor_id == sup.id).all()
    others = db.query(Teacher).filter(Teacher.supervisor_id != sup.id).all()
    db.close()
    if mine and others:
        body = r.text
        if others[0].full_name in body and others[0].supervisor_id != sup.id:
            FAILURES.append("supervisor sees an unsupervised teacher on /supervisor/teachers")
        else:
            print(f"  supervisor scoping: {len(mine)} own teachers listed, others hidden")

    # ------------------------------------------------------------------ teacher classroom access
    db = SessionLocal()
    t1 = db.query(Teacher).filter(Teacher.user_id.isnot(None)).order_by(Teacher.id).first()
    u1 = db.query(U).filter(U.email == "teacher1@oqc.local").first()
    own = db.query(ClassSession).filter(ClassSession.teacher_id == t1.id).order_by(ClassSession.id.desc()).first()
    other = db.query(ClassSession).filter(ClassSession.teacher_id != t1.id).order_by(ClassSession.id.desc()).first()
    db.close()
    t = login("teacher1@oqc.local", "Teacher@123")
    if own:
        check(t, "GET", f"/classroom/{own.id}", 200, label="own classroom")
    if other:
        r = check(t, "GET", f"/classroom/{other.id}", [403, 404], label="other teacher classroom")
        print(f"  teacher access to another teacher's room -> {r.status_code}")

    # ------------------------------------------------------------------ report
    print(f"\n{CHECKS} checks run, {len(FAILURES)} failure(s)")
    for f in FAILURES:
        print("  FAIL:", f)
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
