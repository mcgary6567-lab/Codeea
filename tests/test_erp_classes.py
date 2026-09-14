"""WP-3 Class Management (ERP parity, docs/AUDIT_ACADEMICS.md 3.6 / 3.10 / 3.11 / 3.12).

Every page renders (200) for admin, teacher and supervisor, every filter and tile variant renders, and every
mutation redirects (303) with its effect verified in the database: an applied arrangement really reassigns
sessions, setting it In-Active really reverts them, an approved reschedule really creates the new class,
auto-arrange really creates arrangement rows, activities stamp activity_updated_at, and trials convert / drop.

Self-contained: defaults DATABASE_URL to the private database data/oqc_wpB.db (seed it first with
    $env:DATABASE_URL='sqlite:///./data/oqc_wpB.db'; .venv/Scripts/python.exe seed.py --reset
) so the shared development database is never locked. tests/conftest.py imports the app before this module
under pytest, so set the variable in the shell when running with pytest.

Run:  $env:DATABASE_URL='sqlite:///./data/oqc_wpB.db'; .venv/Scripts/python.exe tests/test_erp_classes.py
ASCII output only (the Windows console is cp1252).
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_wpB.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

import logging  # noqa: E402

logging.getLogger("httpx").setLevel(logging.WARNING)

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.core import User  # noqa: E402
from app.models.erp import ClassActivity, ClassArrangement, ClassQuery, RescheduleRequest, SessionSlot  # noqa: E402
from app.models.finance import Subscription  # noqa: E402
from app.models.people import Employee, Leave, Teacher  # noqa: E402
from app.models.scheduling import ClassSession  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0
TODAY = date.today()


def login(email: str, password: str) -> TestClient:
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", data={"username": email, "password": password})
    assert r.status_code in (200, 302, 303), f"login {email} -> {r.status_code}"
    return c


def check(client: TestClient, method: str, url: str, expect, data=None, label: str = ""):
    global CHECKS
    CHECKS += 1
    r = client.get(url) if method == "GET" else client.post(url, data=data or {})
    allowed = expect if isinstance(expect, (list, tuple, set)) else [expect]
    if r.status_code not in allowed:
        body = r.text[:300].replace("\n", " ") if r.status_code >= 400 else ""
        FAILURES.append(f"{method} {url} -> {r.status_code} (expected {expect}) {label} {body}")
    return r


def expect(condition: bool, message: str):
    global CHECKS
    CHECKS += 1
    if not condition:
        FAILURES.append("ASSERT: " + message)


# ----------------------------------------------------------------------------- pages
def test_pages_render_for_admin():
    c = login("admin@oqc.local", "Admin@12345")
    db = SessionLocal()
    slot = db.query(SessionSlot).filter(SessionSlot.category == "30 Minutes").order_by(SessionSlot.start_time).first()
    teacher = db.query(Teacher).filter(Teacher.is_verified.is_(True)).order_by(Teacher.id).first()
    session = db.query(ClassSession).order_by(ClassSession.id.desc()).first()
    course_id = session.course_id if session else None
    student_id = session.student_id if session else None
    db.close()

    frm, to = TODAY - timedelta(days=30), TODAY + timedelta(days=14)
    base = f"/classes?date_from={frm}&date_to={to}"
    check(c, "GET", "/classes", 200)
    check(c, "GET", base, 200, label="date range")
    # every status tile variant
    for st in ["pending", "available", "started", "done", "missed", "absent", "leave", "cancelled", "rescheduled"]:
        check(c, "GET", f"{base}&status={st}", 200, label=f"tile {st}")
    # every filter
    check(c, "GET", f"{base}&course_id={course_id or ''}", 200, label="course filter")
    check(c, "GET", f"{base}&session_category=30 Minutes", 200, label="category filter")
    check(c, "GET", f"{base}&session_category=45 Minutes", 200, label="category 45 filter")
    check(c, "GET", f"{base}&slot_id={slot.id if slot else ''}", 200, label="session filter")
    check(c, "GET", f"{base}&student_id={student_id or ''}", 200, label="student filter")
    check(c, "GET", f"{base}&teacher_id={teacher.id if teacher else ''}", 200, label="teacher filter")
    check(c, "GET", f"{base}&course_method=group", 200, label="method group")
    check(c, "GET", f"{base}&course_method=one_on_one", 200, label="method one_on_one")
    check(c, "GET", f"{base}&done_by_teacher_id={teacher.id if teacher else ''}", 200, label="done-by filter")
    check(c, "GET", f"{base}&trial=1", 200, label="trial filter")
    check(c, "GET", f"{base}&q=a", 200, label="search")
    check(c, "GET", "/classes/day", 200)

    # arrangements
    check(c, "GET", "/classes/arrangements", 200)
    for st in ("active", "inactive"):
        check(c, "GET", f"/classes/arrangements?status={st}", 200, label=f"arrangement tile {st}")
    check(c, "GET", f"/classes/arrangements?date_from={frm}&date_to={to}&session_category=30 Minutes"
                    f"&slot_id={slot.id if slot else ''}&from_teacher_id={teacher.id if teacher else ''}"
                    f"&to_teacher_id={teacher.id if teacher else ''}", 200, label="arrangement filters")

    # rescheduled
    check(c, "GET", "/classes/rescheduled", 200)
    for st in ("pending", "approved", "rejected", "cancelled"):
        check(c, "GET", f"/classes/rescheduled?status={st}", 200, label=f"reschedule tile {st}")
    check(c, "GET", f"/classes/rescheduled?date_from={frm}&date_to={to}&teacher_id={teacher.id if teacher else ''}",
          200, label="reschedule filters")

    # status summary
    check(c, "GET", "/classes/status-summary", 200)
    check(c, "GET", f"/classes/status-summary?date={TODAY}&employee_id={teacher.id if teacher else ''}"
                    f"&session_category=30 Minutes&slot_id={slot.id if slot else ''}", 200, label="summary filters")

    # queries
    check(c, "GET", "/classes/queries", 200)
    for st in ("pending", "closed"):
        check(c, "GET", f"/classes/queries?status={st}", 200, label=f"query tile {st}")
    check(c, "GET", f"/classes/queries?teacher_id={teacher.id if teacher else ''}&date_from={frm}&date_to={to}",
          200, label="query filters")

    # schedule summary + absent teachers
    check(c, "GET", "/classes/schedule-summary", 200)
    check(c, "GET", f"/classes/schedule-summary?employee_id={teacher.id if teacher else ''}&session_category=45 Minutes",
          200, label="schedule summary filters")
    check(c, "GET", f"/classes/absent-teachers?date_from={frm}&date_to={to}", 200)

    # running trials
    check(c, "GET", "/trials/running", 200)
    check(c, "GET", "/trials/running?shift=morning", 200, label="trial shift filter")
    check(c, "GET", f"/trials/running?teacher_id={teacher.id if teacher else ''}&course_id={course_id or ''}",
          200, label="trial filters")

    # portals
    check(c, "GET", "/teacher/online-class", 200)
    check(c, "GET", f"/teacher/online-class?teacher_id={teacher.id if teacher else ''}", 200, label="admin teacher_id")
    check(c, "GET", "/supervisor", 200)
    check(c, "GET", "/hod", 200)
    for tab in ("academics", "hr", "billing", "progress"):
        check(c, "GET", f"/hod?tab={tab}&date_from={frm}&date_to={TODAY}", 200, label=f"hod tab {tab}")

    if session:
        check(c, "GET", f"/classes/{session.id}", 200, label="class detail")


# ----------------------------------------------------------------------------- arrangements
def test_arrangement_applies_and_reverts():
    c = login("admin@oqc.local", "Admin@12345")
    db = SessionLocal()
    start = TODAY + timedelta(days=1)
    end = start + timedelta(days=6)
    row = (db.query(ClassSession).filter(ClassSession.status == "pending", ClassSession.date >= start,
                                         ClassSession.date <= end).order_by(ClassSession.scheduled_start).first())
    if row is None:
        db.close()
        FAILURES.append("no pending future class to build an arrangement on")
        return
    frm = row.teacher_id
    to = (db.query(Teacher).filter(Teacher.is_verified.is_(True), Teacher.status == "active", Teacher.id != frm)
          .order_by(Teacher.id).first())
    db.close()
    if to is None:
        FAILURES.append("no second verified teacher for the arrangement test")
        return
    r = check(c, "POST", "/classes/arrangements/new", 303, data={
        "from_teacher_id": frm, "to_teacher_id": to.id, "from_date": str(start), "to_date": str(end),
        "session_category": "30 Minutes", "reason": "Automated test: cover while the teacher is away.",
        "back": "/classes/arrangements"}, label="create arrangement")
    check(c, "POST", "/classes/arrangements/new", 303, data={
        "from_teacher_id": frm, "to_teacher_id": frm, "from_date": str(start), "to_date": str(end),
        "reason": "same teacher"}, label="invalid arrangement redirects with an error")
    db = SessionLocal()
    arr = (db.query(ClassArrangement).filter(ClassArrangement.from_teacher_id == frm,
                                             ClassArrangement.to_teacher_id == to.id, ClassArrangement.is_auto.is_(False))
           .order_by(ClassArrangement.id.desc()).first())
    expect(arr is not None, "the arrangement row was not created")
    if arr is None:
        db.close()
        return
    moved = db.query(ClassSession).filter(ClassSession.arrangement_id == arr.id).all()
    expect(arr.applied_count > 0, f"arrangement #{arr.id} applied_count is {arr.applied_count}")
    expect(len(moved) == arr.applied_count, "moved sessions do not match applied_count")
    expect(all(m.teacher_id == to.id and m.substitute_for_teacher_id == frm for m in moved),
           "reassigned sessions do not point at the covering teacher")
    future_ids = [m.id for m in moved]
    db.close()
    print(f"  arrangement #{arr.id}: {arr.applied_count} class(es) reassigned to {to.full_name}")

    check(c, "POST", f"/classes/arrangements/{arr.id}/status", 303,
          data={"status": "inactive", "reason": "Automated test: the original teacher is back.",
                "back": "/classes/arrangements"}, label="set arrangement inactive")
    db = SessionLocal()
    arr2 = db.get(ClassArrangement, arr.id)
    expect(arr2.status == "inactive", "the arrangement was not set In-Active")
    reverted = db.query(ClassSession).filter(ClassSession.id.in_(future_ids), ClassSession.teacher_id == frm).count()
    expect(reverted > 0, "no class went back to the original teacher when the arrangement was set In-Active")
    print(f"  arrangement #{arr.id} In-Active: {reverted} class(es) returned to the original teacher")
    db.close()


def test_auto_arrange_creates_rows():
    """Give a teacher an approved leave over classes they still have, then run Auto Arrangement."""
    c = login("admin@oqc.local", "Admin@12345")
    db = SessionLocal()
    start = TODAY + timedelta(days=8)
    end = start + timedelta(days=3)
    row = (db.query(ClassSession).filter(ClassSession.status == "pending", ClassSession.date >= start,
                                         ClassSession.date <= end).order_by(ClassSession.scheduled_start).first())
    if row is None:
        db.close()
        FAILURES.append("no pending class in the auto-arrange window")
        return
    teacher = db.get(Teacher, row.teacher_id)
    emp = db.get(Employee, teacher.employee_id) if teacher and teacher.employee_id else None
    if emp is None:
        db.close()
        FAILURES.append("the teacher has no employee record for the leave")
        return
    existing = (db.query(Leave).filter(Leave.person_type == "employee", Leave.employee_id == emp.id,
                                       Leave.start_date == start).first())
    if existing is None:
        db.add(Leave(person_type="employee", employee_id=emp.id, leave_type="casual", start_date=start,
                     end_date=end, reason="Automated test leave", status="approved"))
        db.commit()
    before = db.query(ClassArrangement).filter(ClassArrangement.is_auto.is_(True)).count()
    db.close()
    check(c, "POST", "/classes/auto-arrange", 303,
          data={"date_from": str(start), "date_to": str(end)}, label="auto arrange")
    db = SessionLocal()
    after = db.query(ClassArrangement).filter(ClassArrangement.is_auto.is_(True)).count()
    created = (db.query(ClassArrangement).filter(ClassArrangement.is_auto.is_(True), ClassArrangement.from_date >= start)
               .order_by(ClassArrangement.id.desc()).all())
    db.close()
    expect(after > before, f"auto arrange created no rows ({before} -> {after})")
    if created:
        print(f"  auto arrange: {after - before} arrangement(s) created, "
              f"{sum(a.applied_count or 0 for a in created)} class(es) covered")


# ----------------------------------------------------------------------------- reschedule
def test_reschedule_request_and_approval():
    c = login("admin@oqc.local", "Admin@12345")
    db = SessionLocal()
    row = (db.query(ClassSession).filter(ClassSession.status == "pending", ClassSession.date > TODAY)
           .order_by(ClassSession.scheduled_start.desc()).first())
    slot = (db.query(SessionSlot).filter(SessionSlot.category == "30 Minutes")
            .order_by(SessionSlot.start_time.desc()).first())
    db.close()
    if row is None:
        FAILURES.append("no pending future class to reschedule")
        return
    new_date = row.date + timedelta(days=3)
    check(c, "POST", "/classes/reschedule", 303, data={
        "session_id": row.id, "new_date": str(new_date), "new_slot_id": slot.id if slot else "",
        "reason": "Automated test: family asked for a later session.", "back": "/classes"}, label="raise reschedule")
    check(c, "POST", "/classes/reschedule", 303,
          data={"session_id": row.id, "reason": "no date"}, label="reschedule without a date redirects")
    db = SessionLocal()
    rr = (db.query(RescheduleRequest).filter(RescheduleRequest.session_id == row.id, RescheduleRequest.status == "pending")
          .order_by(RescheduleRequest.id.desc()).first())
    db.close()
    expect(rr is not None, "the reschedule request was not created")
    if rr is None:
        return
    check(c, "POST", "/classes/rescheduled/update-status", 303,
          data={"request_ids": rr.id, "status": "approved", "comments": "Automated test approval.",
                "back": "/classes/rescheduled"}, label="approve reschedule")
    db = SessionLocal()
    rr2 = db.get(RescheduleRequest, rr.id)
    old = db.get(ClassSession, rr.session_id)
    new = db.get(ClassSession, rr2.new_session_id) if rr2.new_session_id else None
    expect(rr2.status == "approved", f"the request is {rr2.status}, expected approved")
    expect(new is not None, "approving the reschedule did not create the new class")
    if new is not None:
        expect(new.date == rr2.new_date, "the new class is not on the new working date")
        expect(new.start_time == rr2.new_start_time, "the new class is not in the new session")
        expect(old.status == "rescheduled", f"the old class is {old.status}, expected rescheduled")
        expect(old.rescheduled_to_id == new.id, "rescheduled_to_id was not linked")
        print(f"  reschedule #{rr.id} approved: class #{old.id} -> new class #{new.id} on {new.date}")
    db.close()
    # rejection path
    db = SessionLocal()
    other = (db.query(ClassSession).filter(ClassSession.status == "pending", ClassSession.date > TODAY,
                                           ClassSession.id != row.id).order_by(ClassSession.id.desc()).first())
    db.close()
    if other is not None:
        check(c, "POST", "/classes/reschedule", 303,
              data={"session_id": other.id, "new_date": str(other.date + timedelta(days=2)),
                    "reason": "Automated test: to be rejected."}, label="raise second reschedule")
        db = SessionLocal()
        rr3 = (db.query(RescheduleRequest).filter(RescheduleRequest.session_id == other.id,
                                                  RescheduleRequest.status == "pending")
               .order_by(RescheduleRequest.id.desc()).first())
        db.close()
        if rr3:
            check(c, "POST", "/classes/rescheduled/update-status", 303,
                  data={"request_ids": rr3.id, "status": "rejected", "comments": "Automated test rejection."},
                  label="reject reschedule")
            db = SessionLocal()
            expect(db.get(RescheduleRequest, rr3.id).status == "rejected", "the request was not rejected")
            db.close()


# ----------------------------------------------------------------------------- queries, availability, activity
def test_class_query_and_availability():
    c = login("admin@oqc.local", "Admin@12345")
    db = SessionLocal()
    row = (db.query(ClassSession).filter(ClassSession.status == "pending", ClassSession.date >= TODAY)
           .order_by(ClassSession.scheduled_start).first())
    db.close()
    if row is None:
        FAILURES.append("no pending class for the query / availability test")
        return
    check(c, "POST", "/classes/queries/new", 303, data={
        "session_id": row.id, "query_type": "Facing Tech Issue",
        "detail": "Automated test: the family cannot hear the teacher.", "back": "/classes/queries"},
        label="create class query")
    check(c, "POST", "/classes/queries/new", 303,
          data={"query_type": "nope", "detail": "x"}, label="invalid query type redirects")
    db = SessionLocal()
    cq = db.query(ClassQuery).filter(ClassQuery.session_id == row.id).order_by(ClassQuery.id.desc()).first()
    db.close()
    expect(cq is not None, "the class query was not created")
    if cq is not None:
        check(c, "POST", f"/classes/queries/{cq.id}/close", 303,
              data={"response": "Automated test: resolved with the family."}, label="close class query")
        check(c, "POST", f"/classes/queries/{cq.id}/close", 303, data={}, label="close without a response redirects")
        db = SessionLocal()
        cq2 = db.get(ClassQuery, cq.id)
        expect(cq2.status == "closed" and cq2.response, "the class query was not closed with a response")
        db.close()

    check(c, "POST", f"/classes/{row.id}/available", 303, data={"back": f"/classes/{row.id}"},
          label="mark teacher available")
    db = SessionLocal()
    s2 = db.get(ClassSession, row.id)
    expect(s2.status == "available", f"the class is {s2.status}, expected available")
    expect(s2.teacher_available_at is not None, "teacher_available_at was not stamped")
    db.close()
    print(f"  class #{row.id} marked Teacher is Available")


def test_existing_class_routes_still_work():
    c = login("admin@oqc.local", "Admin@12345")
    db = SessionLocal()
    row = (db.query(ClassSession).filter(ClassSession.status == "pending", ClassSession.date > TODAY + timedelta(days=2))
           .order_by(ClassSession.scheduled_start.desc()).first())
    db.close()
    if row is None:
        FAILURES.append("no pending class for the legacy-route test")
        return
    check(c, "GET", f"/classes/{row.id}", 200, label="detail")
    check(c, "POST", f"/classes/{row.id}/notes", 303, data={"teacher_notes": "Automated test note."}, label="notes")
    check(c, "POST", f"/classes/{row.id}/status", 303,
          data={"status": "cancelled", "reason": "Automated test cancellation."}, label="status")
    db = SessionLocal()
    expect(db.get(ClassSession, row.id).status == "cancelled", "the status route did not change the class")
    db.close()
    check(c, "POST", "/classes/bulk-status", 303,
          data={"session_ids": row.id, "status": "pending", "reason": "Automated test restore", "back": "/classes"},
          label="bulk status")
    db = SessionLocal()
    expect(db.get(ClassSession, row.id).status == "pending", "bulk-status did not change the class")
    db.close()
    start = TODAY + timedelta(days=20)
    check(c, "POST", "/classes/generate", 303,
          data={"start_date": str(start), "end_date": str(start + timedelta(days=3))}, label="generate")


# ----------------------------------------------------------------------------- teacher portal
def test_teacher_online_class():
    db = SessionLocal()
    u = db.query(User).filter(User.email == "teacher1@oqc.local").first()
    t = db.query(Teacher).filter(Teacher.user_id == u.id).first() if u else None
    db.close()
    if t is None:
        FAILURES.append("teacher1@oqc.local has no teacher profile")
        return
    c = login("teacher1@oqc.local", "Teacher@123")
    check(c, "GET", "/teacher/online-class", 200)
    check(c, "GET", f"/teacher/online-class?day={TODAY}", 200, label="teacher day filter")
    db = SessionLocal()
    row = (db.query(ClassSession).filter(ClassSession.teacher_id == t.id, ClassSession.date >= TODAY,
                                         ClassSession.status.in_(["pending", "available", "started"]))
           .order_by(ClassSession.scheduled_start).first())
    if row is None:
        row = (db.query(ClassSession).filter(ClassSession.teacher_id == t.id)
               .order_by(ClassSession.scheduled_start.desc()).first())
    db.close()
    if row is None:
        FAILURES.append("teacher1 has no classes at all")
        return
    check(c, "GET", f"/teacher/online-class?session_id={row.id}", 200, label="class detail panel")
    check(c, "POST", f"/teacher/online-class/{row.id}/activity", 303,
          data={"page_no": "42", "remarks": "Automated test: revision of the previous lesson.",
                "back": "/teacher/online-class"}, label="manual activity")
    db = SessionLocal()
    s2 = db.get(ClassSession, row.id)
    acts = db.query(ClassActivity).filter(ClassActivity.session_id == row.id).count()
    db.close()
    expect(acts > 0, "the manual activity row was not created")
    expect(s2.activity_updated_at is not None, "activity_updated_at was not stamped")
    check(c, "POST", f"/teacher/online-class/{row.id}/activity", 303, data={}, label="empty activity redirects")
    check(c, "POST", f"/teacher/online-class/{row.id}/query", 303,
          data={"query_type": "Family want to talk with manager",
                "detail": "Automated test: the father asked for a call."}, label="teacher raises a query")
    db = SessionLocal()
    expect(db.query(ClassQuery).filter(ClassQuery.session_id == row.id, ClassQuery.teacher_id == t.id).count() > 0,
           "the teacher's class query was not created")
    db.close()
    if s2.status in ("pending", "available", "started"):
        check(c, "POST", f"/teacher/online-class/{row.id}/status", 303,
              data={"action": "available"}, label="teacher marks available")
        check(c, "POST", f"/teacher/online-class/{row.id}/status", 303,
              data={"action": "done"}, label="teacher marks done")
        db = SessionLocal()
        s3 = db.get(ClassSession, row.id)
        expect(s3.status == "done", f"the class is {s3.status}, expected done")
        expect(s3.done_by_teacher_id == t.id, "done_by_teacher_id was not recorded")
        db.close()
        print(f"  teacher portal: class #{row.id} marked Done by {t.full_name}")
    check(c, "POST", f"/teacher/online-class/{row.id}/status", 303,
          data={"action": "missed"}, label="missed without a reason redirects")
    # a teacher must not reach another teacher's class
    db = SessionLocal()
    other = db.query(ClassSession).filter(ClassSession.teacher_id != t.id).order_by(ClassSession.id.desc()).first()
    db.close()
    if other is not None:
        check(c, "POST", f"/teacher/online-class/{other.id}/activity", 404,
              data={"page_no": "1"}, label="other teacher's class is out of scope")


# ----------------------------------------------------------------------------- trials
def test_running_trials_actions():
    c = login("admin@oqc.local", "Admin@12345")
    db = SessionLocal()
    subs = db.query(Subscription).filter(Subscription.status == "trial").order_by(Subscription.id).all()
    ids = [s.id for s in subs]
    db.close()
    if len(ids) < 2:
        FAILURES.append(f"not enough trial subscriptions to exercise convert / drop ({len(ids)})")
        return
    check(c, "POST", f"/trials/running/{ids[0]}/convert", 303,
          data={"reason": "Automated test: family continued after the trial."}, label="convert to regular")
    db = SessionLocal()
    s = db.get(Subscription, ids[0])
    expect(s.status == "regular", f"subscription {s.subscription_code} is {s.status}, expected regular")
    db.close()
    check(c, "POST", f"/trials/running/{ids[1]}/drop", 303, data={}, label="drop without a reason redirects")
    check(c, "POST", f"/trials/running/{ids[1]}/drop", 303,
          data={"reason": "Automated test: family did not continue."}, label="drop trial")
    db = SessionLocal()
    s2 = db.get(Subscription, ids[1])
    expect(s2.status == "cancelled" and s2.cancel_reason, "the dropped trial was not cancelled with a reason")
    db.close()
    print(f"  running trials: {ids[0]} converted, {ids[1]} dropped")


# ----------------------------------------------------------------------------- supervisor / hod
def test_supervisor_and_hod():
    s = login("supervisor@oqc.local", "Super@123")
    for p in ["/supervisor", "/supervisor/partial/board", "/supervisor/teachers", "/supervisor/weekly",
              "/hod", "/hod?tab=hr", "/hod?tab=billing", "/hod?tab=progress",
              "/classes", "/classes/arrangements", "/classes/rescheduled", "/classes/status-summary",
              "/classes/queries", "/classes/schedule-summary", "/trials/running"]:
        check(s, "GET", p, 200, label="supervisor")
    body = s.get("/supervisor").text
    for tile in ["Pending", "Done", "Missed", "Student Absent", "Student On Leave", "Cancelled", "Reschedule Classes"]:
        if tile not in body:
            FAILURES.append(f"supervisor board is missing the ERP tile '{tile}'")
    for panel in ["Pending Classes", "Marked Available", "Started Classes", "Done Classes", "Missed Classes",
                  "Absent Students Classes", "Student On Leave Classes", "Upcoming Classes", "Class Queries",
                  "Upcoming Trials"]:
        if panel not in body:
            FAILURES.append(f"supervisor board is missing the ERP panel '{panel}'")


def main() -> None:
    print(f"ERP class management tests on {os.environ['DATABASE_URL']}")
    for fn in [test_pages_render_for_admin, test_arrangement_applies_and_reverts, test_auto_arrange_creates_rows,
               test_reschedule_request_and_approval, test_class_query_and_availability,
               test_existing_class_routes_still_work, test_teacher_online_class, test_running_trials_actions,
               test_supervisor_and_hod]:
        print(f"- {fn.__name__}")
        fn()
    print(f"\n{CHECKS} checks run, {len(FAILURES)} failure(s)")
    for f in FAILURES:
        print("  FAIL:", f)
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
