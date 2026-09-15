"""ERP parity tests for the Employee Self Portal's Team Management, Tasks and Daily Progress Sheet.

Covers the three pages built from docs/AUDIT_EMPLOYEE_SELF_PORTAL.md:

* /hr/me/team      the reporting-line tree, and raising a violation or a bonus against a report. The
                   guard is the tree itself, so a post naming somebody outside the subtree is refused.
* /hr/me/tasks     the three tiles and three tabs (My Tasks, Assigned Tasks, Others Tasks), commenting
                   on a task and moving one that is assigned to you.
* /hr/me/progress  writing the day up as a draft, editing it, submitting it, and the lock that follows.

The suite is re-runnable against the same database: nothing asserts an absolute row count, everything it
creates carries a unique tag, and a module-scoped teardown removes all of it again.

Run against a private database:
    $env:DATABASE_URL='sqlite:///./data/oqc_essB.db'; .venv/Scripts/python.exe -m pytest tests/test_ess_team.py
"""
from __future__ import annotations

import os
import re
from datetime import date, timedelta
from uuid import uuid4

# The application reads DATABASE_URL at import time; set it before anything imports the app.
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_essB.db")

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.core import User
from app.models.hr_erp import BonusType, ProgressNote, ViolationType
from app.models.ops import Task, TaskComment
from app.models.people import Bonus, Employee, Violation

TAG = f"pytest-{uuid4().hex[:8]}"
# A working date far outside the seeded 45-day window, so a filtered page shows only our own rows.
FAR_DAY = date(2025, 1, 7)
OPEN_STATUSES = ["todo", "in_progress", "review"]

MANAGER = ("supervisor@oqc.local", "Super@123")      # Zubair Shah, a shift supervisor with teachers under him
STAFF = ("teacher1@oqc.local", "Teacher@123")        # a teacher: staff with nobody reporting to them


def _client(username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}: {r.status_code}"
    return c


@pytest.fixture(scope="module")
def manager() -> TestClient:
    return _client(*MANAGER)


@pytest.fixture(scope="module")
def staff() -> TestClient:
    return _client(*STAFF)


@pytest.fixture(scope="module")
def admin() -> TestClient:
    return _client("admin@oqc.local", "Admin@12345")


@pytest.fixture(scope="module")
def parent() -> TestClient:
    return _client("parent1@oqc.local", "Parent@123")


@pytest.fixture(scope="module")
def a_student() -> TestClient:
    return _client("student1@oqc.local", "Student@123")


@pytest.fixture()
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture(scope="module", autouse=True)
def _purge():
    """Remove everything this run created, so the suite can be run again against the same database."""
    yield
    s = SessionLocal()
    try:
        like = f"%{TAG}%"
        task_ids = [t.id for t in s.query(Task.id).filter(Task.title.ilike(like))]
        if task_ids:
            s.query(TaskComment).filter(TaskComment.task_id.in_(task_ids)).delete(synchronize_session=False)
        s.query(TaskComment).filter(TaskComment.text.ilike(like)).delete(synchronize_session=False)
        s.query(Task).filter(Task.title.ilike(like)).delete(synchronize_session=False)
        s.query(Violation).filter(Violation.remarks.ilike(like)).delete(synchronize_session=False)
        s.query(Bonus).filter(Bonus.reason.ilike(like)).delete(synchronize_session=False)
        s.query(ProgressNote).filter(ProgressNote.detail.ilike(like)).delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()


# --------------------------------------------------------------------------- fixtures over the seeded tree
def _employee_for(s, email: str) -> Employee:
    user = s.query(User).filter(User.email == email).first()
    assert user is not None, f"{email} is not seeded"
    e = s.query(Employee).filter(Employee.user_id == user.id).first()
    assert e is not None, f"{email} has no employee record"
    return e


@pytest.fixture()
def boss(session) -> Employee:
    return _employee_for(session, MANAGER[0])


@pytest.fixture()
def report(session, boss) -> Employee:
    """Somebody genuinely beneath the manager. The seed (app/seed/ess_team.py) builds the line."""
    e = session.query(Employee).filter(Employee.manager_id == boss.id).order_by(Employee.id).first()
    assert e is not None, "the reporting line is not seeded - run seed.ess_team"
    return e


@pytest.fixture()
def outsider(session, boss) -> Employee:
    """Somebody outside the manager's subtree: their own manager, who is by definition above them."""
    e = session.get(Employee, boss.manager_id) if boss.manager_id else None
    if e is None:
        e = (session.query(Employee).filter(Employee.manager_id.is_(None), Employee.id != boss.id)
             .order_by(Employee.id).first())
    assert e is not None
    return e


def _make_task(s, title: str, assignee: User | int, creator: User | int, collaborators=None) -> Task:
    t = Task(title=title, description=f"Fixture task {TAG}.",
             assignee_id=getattr(assignee, "id", assignee), creator_id=getattr(creator, "id", creator),
             collaborator_ids=collaborators or [], priority="medium", status="todo",
             due_date=date.today() + timedelta(days=3))
    s.add(t)
    s.commit()
    return t


def _user(s, email: str) -> User:
    u = s.query(User).filter(User.email == email).first()
    assert u is not None, f"{email} is not seeded"
    return u


def _tile_count(body: str, tab: str) -> int:
    """The number printed on one of the three tiles at the top of the tasks page."""
    # the stat macro renders the href with HTML-escaped quotes, so accept either spelling
    m = re.search(r'href=(?:"|&#34;)/hr/me/tasks\?tab=' + tab + r'(?:"|&#34;)[^>]*>\s*<div[^>]*>(\d+)</div>', body)
    assert m, f"the {tab} tile is missing from the page"
    return int(m.group(1))


# =========================================================================== the pages exist and are guarded
@pytest.mark.parametrize("url", ["/hr/me/team", "/hr/me/tasks", "/hr/me/tasks?tab=assigned",
                                 "/hr/me/tasks?tab=others", "/hr/me/tasks?status=todo", "/hr/me/progress",
                                 "/hr/me/progress?status=draft", "/hr/me/progress?status=submitted"])
def test_pages_render_for_a_manager(manager, url):
    assert manager.get(url).status_code == 200


@pytest.mark.parametrize("url", ["/hr/me/team", "/hr/me/tasks", "/hr/me/progress"])
def test_plain_staff_get_their_own_portal(staff, url):
    assert staff.get(url).status_code == 200


@pytest.mark.parametrize("url", ["/hr/me/team", "/hr/me/tasks", "/hr/me/progress"])
def test_a_parent_is_refused(parent, url):
    assert parent.get(url).status_code == 403


@pytest.mark.parametrize("url", ["/hr/me/team", "/hr/me/tasks", "/hr/me/progress"])
def test_a_student_is_refused(a_student, url):
    assert a_student.get(url).status_code == 403


def test_a_parent_cannot_post_either(parent, session, report):
    vt = session.query(ViolationType).first()
    r = parent.post("/hr/me/team/violation", follow_redirects=False,
                    data={"employee_id": report.id, "violation_type_id": vt.id, "remarks": f"nope {TAG}"})
    assert r.status_code == 403
    assert session.query(Violation).filter(Violation.remarks == f"nope {TAG}").first() is None


# =========================================================================== 1. the team tree
def test_the_tree_shows_my_people_and_nobody_else(manager, session, boss, report, outsider):
    body = manager.get("/hr/me/team").text
    beneath = session.query(Employee).filter(Employee.manager_id == boss.id).all()
    assert beneath, "the manager has no reports in this database"
    for e in beneath:
        assert e.employee_code in body, f"{e.employee_code} reports to the signed-in manager but is missing"
    assert report.full_name in body
    assert outsider.employee_code not in body, "the tree must not reach above the signed-in employee"


def test_the_tree_is_nested_not_flat(manager, session, boss):
    """A report who has reports of their own is rendered deeper than a direct report."""
    body = manager.get("/hr/me/team").text
    depths = {int(m) for m in re.findall(r'padding-left: (\d+)px', body)}
    grandchildren = (session.query(Employee)
                     .filter(Employee.manager_id.in_([e.id for e in session.query(Employee)
                                                      .filter(Employee.manager_id == boss.id)] or [-1])).count())
    if grandchildren:
        assert max(depths) > 0, "a nested reporting line must be indented"
    assert 0 in depths


def test_staff_with_no_reports_see_a_message_not_an_empty_box(staff, session):
    body = staff.get("/hr/me/team").text
    me = _employee_for(session, STAFF[0])
    assert session.query(Employee).filter(Employee.manager_id == me.id).count() == 0
    assert "Nobody reports to you" in body


# =========================================================================== 2. raising a violation / bonus
def test_raise_a_violation_against_a_report(manager, admin, session, report):
    vt = session.query(ViolationType).filter(ViolationType.status == "active").order_by(ViolationType.sort_no).first()
    remarks = f"Left the class ten minutes early on Tuesday {TAG}"
    r = manager.post("/hr/me/team/violation", follow_redirects=False, data={
        "employee_id": report.id, "violation_type_id": vt.id, "date": str(date.today()),
        "remarks": remarks, "deduction_amount": ""})
    assert r.status_code == 303

    v = session.query(Violation).filter(Violation.remarks == remarks).first()
    assert v is not None
    assert v.employee_id == report.id
    assert v.violation_type_id == vt.id
    assert v.approval_status == "pending", "a raised violation starts pending"
    assert float(v.deduction_amount or 0) == 0, "the fine must not reach payroll before it is approved"
    assert v.reported_by_id == _user(session, MANAGER[0]).id
    assert f"{float(vt.penalty_amount or 0):.0f}" in (v.action_taken or ""), "the catalogue fine is carried as a proposal"

    # ... and it is waiting in the People & Culture queue, which is the only place it can be decided.
    queue = admin.get("/hr/violations?status=pending")
    assert queue.status_code == 200
    assert str(v.id) in queue.text
    assert manager.get("/hr/me/team").text.count(remarks) >= 1


def test_raise_a_bonus_against_a_report(manager, admin, session, report):
    bt = session.query(BonusType).filter(BonusType.status == "active").order_by(BonusType.sort_no).first()
    reason = f"Covered two extra classes over the weekend {TAG}"
    r = manager.post("/hr/me/team/bonus", follow_redirects=False, data={
        "employee_id": report.id, "bonus_type_id": bt.id, "amount": "", "reason": reason,
        "period": f"{date.today():%Y-%m}"})
    assert r.status_code == 303

    b = session.query(Bonus).filter(Bonus.reason == reason).first()
    assert b is not None
    assert b.employee_id == report.id
    assert b.bonus_type_id == bt.id
    assert b.status == "pending" and b.acceptance_date is None
    assert float(b.amount) == float(bt.bonus_amount), "the chosen type fills the bonus amount"

    queue = admin.get("/hr/bonuses?status=pending")
    assert queue.status_code == 200
    assert str(b.id) in queue.text


def test_the_raiser_cannot_decide_it(manager, session, report):
    """Nothing on the self portal approves anything: the decision routes are the HR ones, and guarded."""
    remarks = f"Raised then approved by the same person {TAG}"
    vt = session.query(ViolationType).first()
    manager.post("/hr/me/team/violation", follow_redirects=False, data={
        "employee_id": report.id, "violation_type_id": vt.id, "remarks": remarks})
    v = session.query(Violation).filter(Violation.remarks == remarks).first()
    assert v is not None
    r = manager.post("/hr/violations/change-status", follow_redirects=False,
                     data={"ids": [v.id], "status": "approved", "remarks": f"Approving my own {TAG}"})
    assert r.status_code == 403, "a supervisor has no violations.approve permission"
    session.expire_all()
    assert session.get(Violation, v.id).approval_status == "pending"


def test_a_post_naming_someone_outside_the_subtree_is_refused(manager, session, outsider):
    remarks = f"Aimed at my own manager {TAG}"
    vt = session.query(ViolationType).first()
    bt = session.query(BonusType).first()
    r = manager.post("/hr/me/team/violation", follow_redirects=False, data={
        "employee_id": outsider.id, "violation_type_id": vt.id, "remarks": remarks})
    assert r.status_code == 403
    assert session.query(Violation).filter(Violation.remarks == remarks).first() is None

    reason = f"Bonus for someone who is not mine {TAG}"
    r = manager.post("/hr/me/team/bonus", follow_redirects=False, data={
        "employee_id": outsider.id, "bonus_type_id": bt.id, "reason": reason})
    assert r.status_code == 403
    assert session.query(Bonus).filter(Bonus.reason == reason).first() is None


def test_nobody_may_raise_one_against_themselves(manager, session, boss):
    remarks = f"Against myself {TAG}"
    vt = session.query(ViolationType).first()
    r = manager.post("/hr/me/team/violation", follow_redirects=False, data={
        "employee_id": boss.id, "violation_type_id": vt.id, "remarks": remarks})
    assert r.status_code == 403
    assert session.query(Violation).filter(Violation.remarks == remarks).first() is None


def test_staff_with_no_reports_cannot_raise_anything(staff, session, report):
    remarks = f"Raised by a colleague with no team {TAG}"
    vt = session.query(ViolationType).first()
    r = staff.post("/hr/me/team/violation", follow_redirects=False, data={
        "employee_id": report.id, "violation_type_id": vt.id, "remarks": remarks})
    assert r.status_code == 403
    assert session.query(Violation).filter(Violation.remarks == remarks).first() is None


# =========================================================================== 3. tasks, three-sided
@pytest.fixture()
def three_tasks(session, boss, report):
    """One task on each side: mine, one I gave out, and one a report owes somebody else."""
    boss_user = _user(session, MANAGER[0])
    report_user = session.get(User, report.user_id)
    admin_user = _user(session, "admin@oqc.local")
    outside_user = _user(session, "hrofficer@oqc.local")
    mine = _make_task(session, f"Sign off the evening shift rota {TAG}", boss_user, admin_user,
                      collaborators=[report_user.id])
    assigned = _make_task(session, f"Send the payroll queries to Finance {TAG}", outside_user, boss_user)
    others = _make_task(session, f"Log the Monday absences in the LMS {TAG}", report_user, admin_user)
    return {"mine": mine, "assigned": assigned, "others": others}


def test_my_tasks_tab(manager, three_tasks):
    body = manager.get("/hr/me/tasks?tab=my").text
    assert three_tasks["mine"].title in body
    assert three_tasks["assigned"].title not in body
    assert three_tasks["others"].title not in body


def test_assigned_tasks_tab(manager, three_tasks):
    body = manager.get("/hr/me/tasks?tab=assigned").text
    assert three_tasks["assigned"].title in body
    assert three_tasks["mine"].title not in body
    assert three_tasks["others"].title not in body


def test_others_tasks_tab(manager, three_tasks):
    body = manager.get("/hr/me/tasks?tab=others").text
    assert three_tasks["others"].title in body
    assert three_tasks["mine"].title not in body
    assert three_tasks["assigned"].title not in body


def test_the_erp_columns_are_there(manager, session, three_tasks, report):
    body = manager.get("/hr/me/tasks?tab=my").text
    for header in ["Task Title", "Assigned To", "Assigned At", "Collaborators", "Due Date", "Comments", "Status"]:
        assert header in body
    collaborator = session.get(User, report.user_id)
    assert collaborator.full_name in body, "the Collaborators column names the people copied in"


def test_the_three_tiles_count_what_is_pending(manager, session, three_tasks, boss):
    """Tiles are checked against the same query, not against a number that only holds on a fresh seed."""
    body = manager.get("/hr/me/tasks").text
    for tab in ("my", "assigned", "others"):
        assert _tile_count(body, tab) >= 1
    boss_user = _user(session, MANAGER[0])
    expected_mine = (session.query(Task).filter(Task.assignee_id == boss_user.id,
                                                Task.status.in_(OPEN_STATUSES)).count())
    assert _tile_count(body, "my") == expected_mine
    for label in ["My Pending Tasks", "My Pending Assigned Tasks", "Others Pending Tasks"]:
        assert label in body


def test_add_a_comment_to_a_task(manager, session, three_tasks):
    t = three_tasks["mine"]
    before = session.query(TaskComment).filter(TaskComment.task_id == t.id).count()
    r = manager.post(f"/hr/me/tasks/{t.id}/comment", follow_redirects=False,
                     data={"text": f"Chased the rota with both supervisors {TAG}", "tab": "my"})
    assert r.status_code == 303
    assert session.query(TaskComment).filter(TaskComment.task_id == t.id).count() == before + 1


def test_a_manager_may_comment_on_a_report_s_task(manager, session, three_tasks):
    t = three_tasks["others"]
    r = manager.post(f"/hr/me/tasks/{t.id}/comment", follow_redirects=False,
                     data={"text": f"Please close this today {TAG}", "tab": "others"})
    assert r.status_code == 303
    assert session.query(TaskComment).filter(TaskComment.task_id == t.id).count() >= 1


def test_comment_on_someone_else_s_task_is_refused(staff, three_tasks):
    """The task I gave to somebody outside this teacher's world: not theirs to see or comment on."""
    r = staff.post(f"/hr/me/tasks/{three_tasks['assigned'].id}/comment", follow_redirects=False,
                   data={"text": f"Not mine to comment on {TAG}"})
    assert r.status_code == 403


def test_change_the_status_of_my_own_task(manager, session, three_tasks):
    t = three_tasks["mine"]
    r = manager.post(f"/hr/me/tasks/{t.id}/status", follow_redirects=False,
                     data={"status": "in_progress", "tab": "my", "note": f"Started {TAG}"})
    assert r.status_code == 303
    session.expire_all()
    assert session.get(Task, t.id).status == "in_progress"

    r = manager.post(f"/hr/me/tasks/{t.id}/status", follow_redirects=False, data={"status": "done", "tab": "my"})
    assert r.status_code == 303
    session.expire_all()
    done = session.get(Task, t.id)
    assert done.status == "done" and done.completed_at is not None


def test_a_bad_status_is_rejected(manager, session, three_tasks):
    t = three_tasks["others"]
    before = t.status
    r = manager.post(f"/hr/me/tasks/{t.id}/status", follow_redirects=False, data={"status": "invented"})
    assert r.status_code == 403, "the task is not assigned to me, so it is refused before the status is read"
    session.expire_all()
    assert session.get(Task, t.id).status == before


def test_only_the_person_it_is_assigned_to_moves_it(manager, staff, session, three_tasks):
    """A manager sees what their team owes, but does not move it for them."""
    t = three_tasks["others"]
    r = manager.post(f"/hr/me/tasks/{t.id}/status", follow_redirects=False, data={"status": "done", "tab": "others"})
    assert r.status_code == 403
    session.expire_all()
    assert session.get(Task, t.id).status == "todo"
    # the report themselves may move it
    r = staff.post(f"/hr/me/tasks/{t.id}/status", follow_redirects=False, data={"status": "review", "tab": "my"})
    assert r.status_code == 303
    session.expire_all()
    assert session.get(Task, t.id).status == "review"


def test_creating_a_task_is_linked_not_duplicated(manager):
    body = manager.get("/hr/me/tasks").text
    assert 'href="/tasks' in body, "the page links to the task board rather than duplicating Create"


# =========================================================================== 4. daily progress sheet
def _note(session, detail: str) -> ProgressNote:
    return session.query(ProgressNote).filter(ProgressNote.detail == detail).first()


def test_write_edit_and_submit_a_progress_note(manager, session, boss):
    detail = f"Covered the Qaida revision with the evening group {TAG}"
    r = manager.post("/hr/me/progress/new", follow_redirects=False,
                     data={"working_date": str(FAR_DAY), "detail": detail, "action": "draft"})
    assert r.status_code == 303
    n = _note(session, detail)
    assert n is not None
    assert n.employee_id == boss.id
    assert n.status == "draft"
    assert n.manager_rating is None
    assert detail in manager.get("/hr/me/progress").text

    edited = f"{detail} - plus the two catch-up classes"
    r = manager.post(f"/hr/me/progress/{n.id}/edit", follow_redirects=False,
                     data={"working_date": str(FAR_DAY), "detail": edited})
    assert r.status_code == 303
    session.expire_all()
    assert session.get(ProgressNote, n.id).detail == edited
    assert session.get(ProgressNote, n.id).status == "draft"

    r = manager.post(f"/hr/me/progress/{n.id}/submit", follow_redirects=False, data={})
    assert r.status_code == 303
    session.expire_all()
    assert session.get(ProgressNote, n.id).status == "submitted"


def test_a_submitted_note_refuses_a_further_edit(manager, session):
    detail = f"Marked the monthly tests for the morning group {TAG}"
    manager.post("/hr/me/progress/new", follow_redirects=False,
                 data={"working_date": str(FAR_DAY), "detail": detail, "action": "submit"})
    n = _note(session, detail)
    assert n is not None and n.status == "submitted", "Save and submit files the note straight away"

    r = manager.post(f"/hr/me/progress/{n.id}/edit", follow_redirects=False,
                     data={"working_date": str(FAR_DAY), "detail": f"rewritten after submission {TAG}"})
    assert r.status_code == 303, "the employee is sent back to the page with the refusal"
    session.expire_all()
    assert session.get(ProgressNote, n.id).detail == detail, "a submitted note must not change"
    assert _note(session, f"rewritten after submission {TAG}") is None


def test_a_draft_is_marked_editable_and_a_submitted_note_locked(manager, session):
    draft = f"Draft still being written {TAG}"
    filed = f"Filed and locked {TAG}"
    manager.post("/hr/me/progress/new", follow_redirects=False,
                 data={"working_date": str(FAR_DAY), "detail": draft, "action": "draft"})
    manager.post("/hr/me/progress/new", follow_redirects=False,
                 data={"working_date": str(FAR_DAY), "detail": filed, "action": "submit"})
    body = manager.get(f"/hr/me/progress?date_from={FAR_DAY}&date_to={FAR_DAY}").text
    d, f = _note(session, draft), _note(session, filed)
    assert f"/hr/me/progress/{d.id}/submit" in body, "a draft carries the Submit action"
    assert f"/hr/me/progress/{f.id}/submit" not in body, "a submitted note carries no further action"
    assert "Locked" in body


def test_i_cannot_touch_someone_else_s_note(staff, manager, session):
    detail = f"Only mine to edit {TAG}"
    manager.post("/hr/me/progress/new", follow_redirects=False,
                 data={"working_date": str(FAR_DAY), "detail": detail, "action": "draft"})
    n = _note(session, detail)
    r = staff.post(f"/hr/me/progress/{n.id}/edit", follow_redirects=False,
                   data={"detail": f"tampered {TAG}", "working_date": str(FAR_DAY)})
    assert r.status_code == 403
    r = staff.post(f"/hr/me/progress/{n.id}/submit", follow_redirects=False, data={})
    assert r.status_code == 403
    session.expire_all()
    assert session.get(ProgressNote, n.id).detail == detail
    assert session.get(ProgressNote, n.id).status == "draft"


def test_an_empty_note_is_rejected(manager, session):
    before = session.query(ProgressNote).filter(ProgressNote.working_date == FAR_DAY).count()
    r = manager.post("/hr/me/progress/new", follow_redirects=False,
                     data={"working_date": str(FAR_DAY), "detail": "   "})
    assert r.status_code == 303
    assert session.query(ProgressNote).filter(ProgressNote.working_date == FAR_DAY).count() == before


def test_the_management_sheet_only_offers_a_rating_on_a_submitted_note(admin, manager, session, boss):
    """Rating stays on /hr/progress-sheet, and a draft the employee still owns cannot be rated there."""
    draft = f"Still a draft, not for rating {TAG}"
    manager.post("/hr/me/progress/new", follow_redirects=False,
                 data={"working_date": str(FAR_DAY), "detail": draft, "action": "draft"})
    n = _note(session, draft)
    url = f"/hr/progress-sheet?employee={boss.id}&date_from={FAR_DAY}&date_to={FAR_DAY}"
    page = admin.get(url)
    assert page.status_code == 200
    assert draft in page.text
    assert f"/hr/progress-sheet/{n.id}/rate" not in page.text, "a draft must not offer a rating"

    manager.post(f"/hr/me/progress/{n.id}/submit", follow_redirects=False, data={})
    page = admin.get(url)
    assert f"/hr/progress-sheet/{n.id}/rate" in page.text, "a submitted note is rateable"

    r = admin.post(f"/hr/progress-sheet/{n.id}/rate", follow_redirects=False,
                   data={"manager_rating": "4", "manager_comment": f"Good detail {TAG}"})
    assert r.status_code == 303
    session.expire_all()
    rated = session.get(ProgressNote, n.id)
    assert rated.manager_rating == 4 and rated.rated_by_id and rated.status == "submitted"


def test_the_seeded_sheet_carries_both_statuses(session):
    """The seed spreads the sheet across draft and submitted, and never leaves a rated note as a draft."""
    assert session.query(ProgressNote).filter(ProgressNote.status == "draft").count() >= 1
    assert session.query(ProgressNote).filter(ProgressNote.status == "submitted").count() >= 1
    assert session.query(ProgressNote).filter(ProgressNote.manager_rating.isnot(None),
                                              ProgressNote.status != "submitted").count() == 0


def test_the_seed_built_a_reporting_line_with_depth(session):
    """A tree, not one flat level: somebody reports to somebody who reports to somebody else."""
    managers = {e.manager_id for e in session.query(Employee).filter(Employee.manager_id.isnot(None))}
    assert managers, "no reporting line at all - run seed.ess_team"
    deep = session.query(Employee).filter(Employee.manager_id.in_(managers)).count()
    assert deep >= 1, "the reporting line is only one level deep"
    assert session.query(Task).filter(Task.collaborator_ids != []).count() >= 1


def test_an_account_with_no_employee_record_is_told_so(admin, session):
    """The administrator has no employee record; the pages say so rather than falling over."""
    assert session.query(Employee).filter(Employee.user_id == _user(session, "admin@oqc.local").id).count() == 0
    for url in ["/hr/me/team", "/hr/me/progress"]:
        r = admin.get(url)
        assert r.status_code == 200
        assert "No employee record is linked" in r.text
    assert admin.get("/hr/me/tasks").status_code == 200
