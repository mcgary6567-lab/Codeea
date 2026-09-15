"""Employee Self Portal seed: the reporting line, progress-sheet statuses and task collaborators.

Three pages of the portal only come alive once the data behind them has shape
(docs/AUDIT_EMPLOYEE_SELF_PORTAL.md):

* **My Team** needs Employee.manager_id to describe a real hierarchy rather than one flat level, so this
  builds head -> manager -> supervisor -> teacher, four deep, and hangs every officer off the head of
  their own department.
* **The Daily Progress Sheet** needs ProgressNote.status spread across draft and submitted, because only
  a submitted note can be rated and only a draft can still be edited.
* **Tasks** needs Task.collaborator_ids filled on a share of rows so the Collaborators column has
  something in it, and needs the people beneath a manager to own a few open tasks so the Others Tasks
  tab is not empty.

Idempotent and deterministic: a manager is only set where one is missing, the progress statuses follow a
fixed rule on the note id, collaborators are only written to tasks that have none, and the demo tasks are
matched by title before they are created. Running it twice changes nothing the second time.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.core import User
from app.models.hr_erp import ProgressNote
from app.models.ops import Task
from app.models.people import Employee

ACTIVE = ["active", "probation", "on_leave"]

# The academics chain, which is what gives the tree its depth. Matched on the designation prefix so it
# survives the exact wording of a seeded title ("Operations Manager (Morning Shift)" and the rest).
HEAD_PREFIX = "Head of"
ACADEMICS_HEAD = "Head of Academics"
CHIEF_DESIGNATION = "Chief Executive"

# Demo tasks owned by teachers, so a supervisor's "Others Tasks" tab has rows. (title, days from today)
TEAM_TASKS = [
    ("Record the weekly Tajweed recitation review for the evening group", 2),
    ("Chase the two students who missed Monday's class and log the reason", 1),
    ("Update the lesson plan for Surah Al-Mulk before the monthly test", 5),
    ("Return the corrected monthly test papers to the coordinator", 3),
    ("Confirm next month's class timings with each parent on WhatsApp", 7),
    ("Write up the Qaida progress notes for the three new joiners", 4),
]


def _heads(db: Session) -> dict[int, Employee]:
    """Head of <department> for each department id."""
    out: dict[int, Employee] = {}
    for e in db.query(Employee).filter(Employee.status.in_(ACTIVE)).order_by(Employee.id):
        if (e.designation or "").startswith(HEAD_PREFIX) and e.department_id and e.department_id not in out:
            out[e.department_id] = e
    return out


def _by_designation(staff: list[Employee], *needles: str) -> list[Employee]:
    return [e for e in staff if any(n.lower() in (e.designation or "").lower() for n in needles)]


def _reporting_line(db: Session) -> tuple[int, int]:
    """Give everyone without a manager one, deepest chain first. Returns (linked, already set)."""
    staff = db.query(Employee).filter(Employee.status.in_(ACTIVE)).order_by(Employee.id).all()
    already = sum(1 for e in staff if e.manager_id)
    heads = _heads(db)
    academics = next((e for e in staff if (e.designation or "").startswith(ACADEMICS_HEAD)), None)
    managers = _by_designation(staff, "Operations Manager")
    supervisors = _by_designation(staff, "Supervisor")
    coordinators = _by_designation(staff, "Coordinator")
    teachers = [e for e in staff if e.is_teacher]

    plan: dict[int, int] = {}

    def assign(child: Employee | None, parent: Employee | None) -> None:
        if child is None or parent is None or child.id == parent.id:
            return
        plan[child.id] = parent.id

    # Operations managers answer to Academics; a supervisor answers to the manager on their own shift.
    for m in managers:
        assign(m, academics)
    night_manager = next((m for m in managers if (m.shift or "") == "night" or "Night" in (m.designation or "")), None)
    day_manager = next((m for m in managers if m is not night_manager), None)
    for s in supervisors:
        night = (s.shift or "") == "night" or "Night" in (s.designation or "")
        assign(s, (night_manager or day_manager) if night else (day_manager or night_manager))
    for c in coordinators:
        assign(c, day_manager or academics)
    # Teachers answer to the supervisor covering their shift, falling back to the other one.
    night_sup = next((s for s in supervisors if (s.shift or "") == "night" or "Night" in (s.designation or "")), None)
    day_sup = next((s for s in supervisors if s is not night_sup), None)
    for t in teachers:
        night = (t.shift or "") == "night"
        assign(t, (night_sup or day_sup) if night else (day_sup or night_sup))
    # Everybody else hangs off the head of their own department.
    for e in staff:
        if e.id in plan or (e.designation or "").startswith(HEAD_PREFIX):
            continue
        assign(e, heads.get(e.department_id) if e.department_id else None)
    # The heads answer to the chief executive, so the tree has one root the way theirs does rather than a
    # row of unconnected heads. The chief executive reports to nobody.
    chief = next((e for e in staff if (e.designation or "").startswith(CHIEF_DESIGNATION)), None)
    if chief is not None:
        plan.pop(chief.id, None)
        for e in staff:
            if e.id != chief.id and (e.designation or "").startswith(HEAD_PREFIX):
                assign(e, chief)

    linked = 0
    for e in staff:
        if e.manager_id or e.id not in plan:
            continue
        e.manager_id = plan[e.id]
        linked += 1
    db.flush()
    return linked, already


def _progress_statuses(db: Session) -> tuple[int, int, int]:
    """Spread the progress sheet across draft and submitted. Rated notes must be submitted."""
    changed = 0
    for n in db.query(ProgressNote).filter(ProgressNote.status == "draft").order_by(ProgressNote.id):
        # A rated note can only ever have been a submitted one; of the rest, two in three are submitted
        # and the remainder stay drafts their author is still free to edit. Nothing is ever pushed back
        # from submitted to draft: that would hand an employee back work their manager already has.
        if n.manager_rating is not None or n.id % 3:
            n.status = "submitted"
            changed += 1
    db.flush()
    drafts = db.query(ProgressNote).filter(ProgressNote.status == "draft").count()
    submitted = db.query(ProgressNote).filter(ProgressNote.status == "submitted").count()
    return changed, drafts, submitted


def _collaborators(db: Session) -> int:
    """Copy one or two colleagues in on a share of the tasks, as the ERP's Collaborators column shows."""
    pool = [u.id for u in db.query(User).filter(User.is_active.is_(True)).order_by(User.id)
            if (u.role_slug or "") not in ("client", "student")]
    if len(pool) < 3:
        return 0
    filled = 0
    for t in db.query(Task).order_by(Task.id):
        if t.collaborator_ids or t.id % 3:
            continue
        picks = [pool[t.id % len(pool)], pool[(t.id + 7) % len(pool)]]
        ids = [p for p in dict.fromkeys(picks) if p != t.assignee_id]
        if not ids:
            continue
        t.collaborator_ids = ids
        filled += 1
    db.flush()
    return filled


def _team_tasks(db: Session) -> int:
    """A handful of open tasks owned by teachers, so a supervisor's Others Tasks tab has rows."""
    teachers = [e for e in db.query(Employee).filter(Employee.status.in_(ACTIVE), Employee.is_teacher.is_(True))
                .order_by(Employee.id) if e.user_id]
    if not teachers:
        return 0
    made = 0
    for i, (title, due_in) in enumerate(TEAM_TASKS):
        if db.query(Task).filter(Task.title == title).first():
            continue
        owner = teachers[i % len(teachers)]
        manager = db.get(Employee, owner.manager_id) if owner.manager_id else None
        db.add(Task(title=title, description="Raised by the shift supervisor from the Employee Self Portal.",
                    assignee_id=owner.user_id,
                    creator_id=(manager.user_id if manager and manager.user_id else owner.user_id),
                    department_id=owner.department_id, priority="medium" if i % 2 else "high",
                    status="todo" if i % 2 else "in_progress", due_date=date.today() + timedelta(days=due_in),
                    estimate_hours=1.5))
        made += 1
    db.flush()
    return made


def run(db: Session) -> None:
    linked, already = _reporting_line(db)
    db.flush()
    changed, drafts, submitted = _progress_statuses(db)
    tasks = _team_tasks(db)
    collabs = _collaborators(db)
    db.flush()
    depth = db.query(Employee).filter(Employee.manager_id.isnot(None)).count()
    print(f"    ess_team: reporting line +{linked} ({depth} of {db.query(Employee).count()} employees have a "
          f"manager, {already} already did), progress notes {drafts} draft / {submitted} submitted "
          f"({changed} set), team tasks +{tasks}, collaborators on +{collabs} task(s)")
