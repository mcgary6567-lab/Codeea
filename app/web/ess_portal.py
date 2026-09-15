"""Employee Self Portal: Team Management, three-sided Tasks and the Daily Progress Sheet.

Mirrors three pages of the college's existing ERP (docs/AUDIT_EMPLOYEE_SELF_PORTAL.md):

    /hr/me/team       the dashboard's "My Team" tree plus the Team Management sub-menu (Staff Violation,
                      Staff Bonuses) on one page: raise either against anyone genuinely beneath you.
    /hr/me/tasks      their employee-tasks page: three tiles, three tabs (My Tasks, Assigned Tasks,
                      Others Tasks) over the one Task model, with the ERP's own column list.
    /hr/me/progress   their emp-progress-sheet: write the day up, keep it as a draft, edit it, submit it.

Two rules run through the whole file:

* **The tree is the authority.** Who you may raise a violation or a bonus against is worked out by
  walking Employee.manager_id (app.services.hr_dashboards.subordinate_ids), never from a form field. A
  post naming somebody outside your own subtree is refused, not silently ignored.
* **One decision path.** Everything raised here lands *pending* in the People & Culture queues at
  /hr/violations and /hr/bonuses, and is decided there. Nothing on this page approves anything, so a
  manager can never decide their own proposal. Ratings likewise stay on /hr/progress-sheet.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import PermissionDenied, UserContext, csrf_protect, get_user_context, require
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import month_key, paginate, parse_date, parse_float, parse_int, redirect
from app.database import get_db
from app.models.core import User
from app.models.hr_erp import BonusType, ProgressNote, ViolationType
from app.models.ops import Task, TaskComment
from app.models.people import Bonus, Employee, Violation
from app.services import hr_dashboards as dash

router = APIRouter(prefix="/hr/me", dependencies=[Depends(csrf_protect)])

# Kept in step with app/web/hr.py: the HR queue reads the proposed fine back off action_taken when it
# approves the violation, so a fine agreed by the manager here survives through to payroll.
PROPOSED_FINE = "Proposed fine: "

TASK_STATUSES = ["todo", "in_progress", "review", "done", "cancelled"]
OPEN_TASK_STATUSES = ["todo", "in_progress", "review"]
TASK_TABS = [("my", "My Tasks"), ("assigned", "Assigned Tasks"), ("others", "Others Tasks")]
TASK_TILES = {"my": "My Pending Tasks", "assigned": "My Pending Assigned Tasks", "others": "Others Pending Tasks"}
TASK_HEADERS = ["ID", "Task Title", "Assigned To", "Assigned At", "Collaborators", "Due Date", "Comments", "Status"]
PROGRESS_STATUSES = ["draft", "submitted"]
ME_LINKS = [("team", "Team Management", "/hr/me/team"), ("tasks", "Tasks", "/hr/me/tasks"),
            ("progress", "Daily Progress Sheet", "/hr/me/progress")]


# ----------------------------------------------------------------------------- shared helpers
NO_RECORD = ("No employee record is linked to your account, so there is nothing to show here. "
             "People and Culture can link one from the Employee Record page.")


def _no_employee(url: str):
    return redirect(url, "No employee record is linked to your account.", "error")


def employee_label(e: Employee | None) -> str:
    return f"{e.employee_code} - {e.full_name}" if e else "-"


def _base(ctx: UserContext, active: str) -> dict:
    return {"user": ctx.user, "me": ctx.employee, "links": ME_LINKS, "active": active,
            "no_record": NO_RECORD if ctx.employee is None else None,
            "today_iso": date.today().isoformat()}


def _collaborator_ids(task: Task) -> list[int]:
    out = []
    for raw in (task.collaborator_ids or []):
        try:
            out.append(int(raw))
        except (TypeError, ValueError):
            continue
    return out


# ============================================================================== 1. TEAM MANAGEMENT
@router.get("/team", include_in_schema=False)
def team(request: Request, db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context),
         user: User = Depends(require("portal_self.view"))):
    """My Team as a nested tree, with Staff Violation and Staff Bonuses against anyone inside it."""
    me = ctx.employee
    tree = dash.reporting_tree(db, me)
    flat = dash.flatten_tree(tree)
    team_ids = [n["employee"].id for n in flat]

    raised_violations, raised_bonuses, pending_v, pending_b = [], [], 0, 0
    if team_ids:
        raised_violations = (db.query(Violation).filter(Violation.employee_id.in_(team_ids))
                             .order_by(Violation.id.desc()).limit(15).all())
        raised_bonuses = (db.query(Bonus).filter(Bonus.employee_id.in_(team_ids))
                          .order_by(Bonus.id.desc()).limit(15).all())
        pending_v = (db.query(func.count(Violation.id)).filter(
            Violation.employee_id.in_(team_ids), Violation.approval_status == "pending").scalar() or 0)
        pending_b = (db.query(func.count(Bonus.id)).filter(
            Bonus.employee_id.in_(team_ids), Bonus.status == "pending").scalar() or 0)

    v_types = db.query(ViolationType).filter(ViolationType.status == "active").order_by(ViolationType.sort_no).all()
    b_types = db.query(BonusType).filter(BonusType.status == "active").order_by(BonusType.sort_no).all()
    data = _base(ctx, "team")
    data.update({
        "tree": tree, "rows": flat, "directs": len(tree), "team_size": len(flat),
        "pending_violations": pending_v, "pending_bonuses": pending_b,
        "member_options": [(n["employee"].id, f"{employee_label(n['employee'])} - {n['employee'].designation}")
                           for n in flat],
        "violation_type_options": [(t.id, f"{t.description[:70]} ({float(t.penalty_amount or 0):,.0f} PKR)")
                                   for t in v_types],
        "bonus_type_options": [(t.id, f"{t.description[:70]} ({float(t.bonus_amount or 0):,.0f} PKR)")
                               for t in b_types],
        "violation_amounts": {str(t.id): float(t.penalty_amount or 0) for t in v_types},
        "bonus_amounts": {str(t.id): float(t.bonus_amount or 0) for t in b_types},
        "raised_violations": raised_violations, "raised_bonuses": raised_bonuses,
        "period_default": month_key(),
    })
    return render(request, "hr/ess_team.html", data)


def _report_or_403(db: Session, me: Employee, employee_id: int) -> Employee:
    """Walk the tree and refuse anyone not genuinely beneath me. The form field is never trusted."""
    if not employee_id or employee_id == me.id:
        raise PermissionDenied("ess_team.report (you may only raise this against your own reports)")
    if employee_id not in dash.subordinate_ids(db, me):
        raise PermissionDenied("ess_team.report (you may only raise this against your own reports)")
    target = db.get(Employee, employee_id)
    if target is None:
        raise PermissionDenied("ess_team.report (you may only raise this against your own reports)")
    return target


@router.post("/team/violation", include_in_schema=False)
async def team_violation(request: Request, db: Session = Depends(get_db),
                         ctx: UserContext = Depends(get_user_context),
                         user: User = Depends(require("portal_self.view"))):
    """Staff Violation against a report. Lands pending in /hr/violations; the fine is not applied yet."""
    if ctx.employee is None:
        return _no_employee("/hr/me")
    form = await request.form()
    target = _report_or_403(db, ctx.employee, parse_int(form.get("employee_id"), 0) or 0)
    cat = db.get(ViolationType, parse_int(form.get("violation_type_id"), 0) or 0)
    remarks = (form.get("remarks") or form.get("rationale") or "").strip()
    if cat is None or not remarks:
        return redirect("/hr/me/team", "Choose the offence and say what happened.", "error")
    fine = parse_float(form.get("deduction_amount"), -1.0)
    if fine is None or fine < 0:
        fine = float(cat.penalty_amount or 0)
    v = Violation(employee_id=target.id, violation_type="policy", severity=cat.severity or "minor",
                  description=cat.description, reported_by_id=user.id,
                  date=parse_date(form.get("date")) or date.today(), status="open",
                  deduction_amount=0, violation_type_id=cat.id, approval_status="pending", remarks=remarks)
    if fine > 0:
        v.action_taken = f"{PROPOSED_FINE}{fine:.0f} PKR"
    db.add(v)
    db.flush()
    log_action(db, user, "create", "violations", entity=v, request=request, rationale=remarks,
               description=f"Staff violation raised from the self portal against {target.employee_code} "
                           f"({(cat.description or '')[:60]}); pending a People and Culture decision",
               after={"approval_status": "pending", "proposed_fine": fine},
               consequential=True, severity="warning")
    if target.user_id:
        notify(db, target.user_id, "Violation raised",
               f"{ctx.employee.full_name} raised a violation against you on {v.date}. "
               f"It is pending a People and Culture decision.",
               event_type="violation", link="/hr/me?tab=violations")
    db.commit()
    return redirect("/hr/me/team",
                    f"Violation raised against {target.full_name}; it is pending with People and Culture, "
                    f"and the fine applies only once they approve it.", "warning")


@router.post("/team/bonus", include_in_schema=False)
async def team_bonus(request: Request, db: Session = Depends(get_db),
                     ctx: UserContext = Depends(get_user_context),
                     user: User = Depends(require("portal_self.view"))):
    """Staff Bonus against a report. Lands pending in /hr/bonuses, where the decision is taken."""
    if ctx.employee is None:
        return _no_employee("/hr/me")
    form = await request.form()
    target = _report_or_403(db, ctx.employee, parse_int(form.get("employee_id"), 0) or 0)
    cat = db.get(BonusType, parse_int(form.get("bonus_type_id"), 0) or 0)
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if cat is None or not reason:
        return redirect("/hr/me/team", "Choose the bonus type and say what it is for.", "error")
    amount = parse_float(form.get("amount"), 0) or float(cat.bonus_amount or 0)
    if amount <= 0:
        return redirect("/hr/me/team", "The bonus amount must be greater than zero.", "error")
    b = Bonus(employee_id=target.id, amount=amount, currency=target.currency or "PKR", bonus_type="performance",
              bonus_type_id=cat.id, reason=reason, period=form.get("period") or month_key(), status="pending")
    db.add(b)
    db.flush()
    log_action(db, user, "create", "payroll", entity=b, request=request, rationale=reason,
               description=f"Staff bonus of {amount:,.0f} raised from the self portal for "
                           f"{target.employee_code} ({(cat.description or '')[:60]}); pending approval",
               after={"status": "pending", "amount": amount}, consequential=True)
    if target.user_id:
        notify(db, target.user_id, "Bonus proposed",
               f"{ctx.employee.full_name} proposed a bonus of {amount:,.0f} {b.currency} for you. "
               f"It is pending a People and Culture decision.",
               event_type="hr_request", link="/hr/me?tab=bonuses")
    db.commit()
    return redirect("/hr/me/team",
                    f"Bonus of {amount:,.0f} proposed for {target.full_name}; People and Culture decide it.")


# ============================================================================== 2. TASKS (three-sided)
def _task_scope(db: Session, user: User, me: Employee | None, tab: str):
    """The three sides of the ERP's task page, each a query over the one Task model."""
    team_uids = (dash.team_user_ids(db, me) - {user.id}) if me is not None else set()
    if tab == "assigned":
        q = db.query(Task).filter(Task.creator_id == user.id, Task.assignee_id != user.id)
    elif tab == "others":
        q = db.query(Task).filter(Task.assignee_id.in_(team_uids or [-1]))
    else:
        q = db.query(Task).filter(Task.assignee_id == user.id)
    return q, team_uids


def _task_rows(db: Session, tasks: list[Task], user: User) -> list[dict]:
    """One row per task carrying the ERP's columns, with collaborator names and the comment count."""
    ids = [t.id for t in tasks]
    counts = dict(db.query(TaskComment.task_id, func.count(TaskComment.id))
                  .filter(TaskComment.task_id.in_(ids or [-1])).group_by(TaskComment.task_id).all())
    wanted: set[int] = set()
    for t in tasks:
        wanted.update(_collaborator_ids(t))
        wanted.update(uid for uid in (t.assignee_id, t.creator_id) if uid)
    names = dict(db.query(User.id, User.full_name).filter(User.id.in_(wanted or [-1])).all())
    return [{"task": t, "comments": counts.get(t.id, 0),
             "collaborators": [names.get(uid, f"User #{uid}") for uid in _collaborator_ids(t)],
             "assigned_to": names.get(t.assignee_id, "Unassigned"),
             "assigned_by": names.get(t.creator_id, "-"),
             "mine": t.assignee_id == user.id} for t in tasks]


@router.get("/tasks", include_in_schema=False)
def tasks(request: Request, tab: str = "my", page: int = 1, status: str = "", q: str = "",
          db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context),
          user: User = Depends(require("portal_self.view"))):
    """My Pending Tasks / My Pending Assigned Tasks / Others Pending Tasks, and the three tabs beneath."""
    tab = tab if tab in TASK_TILES else "my"
    me = ctx.employee
    base, team_uids = _task_scope(db, user, me, tab)
    query = base
    if status in TASK_STATUSES:
        query = query.filter(Task.status == status)
    if q:
        query = query.filter(Task.title.ilike(f"%{q}%"))
    pg = paginate(query.order_by(Task.status.in_(["done", "cancelled"]), Task.due_date.is_(None),
                                 Task.due_date, Task.id.desc()), page, 25)
    tiles = []
    for key, label in TASK_TABS:
        scoped, _ = _task_scope(db, user, me, key)
        tiles.append({"key": key, "label": TASK_TILES[key],
                      "count": scoped.filter(Task.status.in_(OPEN_TASK_STATUSES)).count(), "tab": label})
    data = _base(ctx, "tasks")
    data.update({
        "tab": tab, "tabs": [(k, l, f"/hr/me/tasks?tab={k}") for k, l in TASK_TABS], "page": pg,
        "rows": _task_rows(db, pg.items, user), "tiles": tiles, "headers": TASK_HEADERS,
        "filters": {"status": status, "q": q}, "statuses": TASK_STATUSES,
        "status_options": [(s, s.replace("_", " ").title()) for s in TASK_STATUSES],
        "base_url": f"/hr/me/tasks?tab={tab}&status={status}&q={q}",
        "team_size": len(team_uids), "today": date.today(),
    })
    return render(request, "hr/ess_tasks.html", data)


def _visible_task(db: Session, user: User, me: Employee | None, task_id: int) -> Task:
    """A task I own, gave out, collaborate on, or that one of my reports owns. Anything else is refused."""
    t = db.get(Task, task_id)
    if t is None:
        raise PermissionDenied("ess_tasks.view (no such task, or it is not one of yours)")
    team_uids = dash.team_user_ids(db, me) if me is not None else set()
    if user.id in ({t.assignee_id, t.creator_id} | set(_collaborator_ids(t))) or t.assignee_id in team_uids:
        return t
    raise PermissionDenied("ess_tasks.view (no such task, or it is not one of yours)")


@router.post("/tasks/{task_id}/status", include_in_schema=False)
async def task_status(task_id: int, request: Request, db: Session = Depends(get_db),
                      ctx: UserContext = Depends(get_user_context),
                      user: User = Depends(require("portal_self.view"))):
    """Move a task assigned to me. Only the person who owes the work may move it from this page."""
    t = _visible_task(db, user, ctx.employee, task_id)
    if t.assignee_id != user.id:
        raise PermissionDenied("ess_tasks.update (the task is not assigned to you)")
    form = await request.form()
    new_status = (form.get("status") or "").strip()
    back = f"/hr/me/tasks?tab={form.get('tab') or 'my'}"
    if new_status not in TASK_STATUSES:
        return redirect(back, "Choose a status for the task.", "error")
    before = t.status
    if before == new_status:
        return redirect(back, "The task is already at that status.", "info")
    t.status = new_status
    t.completed_at = datetime.utcnow() if new_status == "done" else None
    log_action(db, user, "update", "tasks", entity=t, request=request,
               description=f"Task #{t.id} moved from {before} to {new_status} from the self portal",
               rationale=(form.get("note") or "").strip() or None,
               before={"status": before}, after={"status": new_status})
    if t.creator_id and t.creator_id != user.id:
        notify(db, t.creator_id, "Task status changed",
               f"{user.full_name} moved '{t.title}' to {new_status.replace('_', ' ')}.",
               event_type="task_status", link=f"/tasks/{t.id}")
    db.commit()
    return redirect(back, f"Task #{t.id} moved to {new_status.replace('_', ' ')}.")


@router.post("/tasks/{task_id}/comment", include_in_schema=False)
async def task_comment(task_id: int, request: Request, db: Session = Depends(get_db),
                       ctx: UserContext = Depends(get_user_context),
                       user: User = Depends(require("portal_self.view"))):
    """The Comments column is a count; this is what makes it go up."""
    t = _visible_task(db, user, ctx.employee, task_id)
    form = await request.form()
    text = (form.get("text") or "").strip()
    back = f"/hr/me/tasks?tab={form.get('tab') or 'my'}"
    if not text:
        return redirect(back, "Write something before adding the comment.", "error")
    db.add(TaskComment(task_id=t.id, user_id=user.id, text=text))
    for uid in {t.assignee_id, t.creator_id} - {user.id, None}:
        notify(db, uid, "New task comment", f"{user.full_name} commented on '{t.title}'.",
               event_type="task_comment", link=f"/tasks/{t.id}")
    log_action(db, user, "create", "tasks", entity=t, request=request, rationale=text,
               description=f"Comment added to task #{t.id} from the self portal")
    db.commit()
    return redirect(back, "Comment added.")


# ============================================================================== 3. DAILY PROGRESS SHEET
@router.get("/progress", include_in_schema=False)
def progress(request: Request, page: int = 1, status: str = "", date_from: str = "", date_to: str = "",
             db: Session = Depends(get_db), ctx: UserContext = Depends(get_user_context),
             user: User = Depends(require("portal_self.view"))):
    """My own progress sheet: write the day up, keep it a draft, submit it. The rating is the manager's."""
    me_id = ctx.employee.id if ctx.employee is not None else -1
    query = db.query(ProgressNote).filter(ProgressNote.employee_id == me_id)
    if status in PROGRESS_STATUSES:
        query = query.filter(ProgressNote.status == status)
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(ProgressNote.working_date >= df)
    if dt:
        query = query.filter(ProgressNote.working_date <= dt)
    pg = paginate(query.order_by(ProgressNote.working_date.desc(), ProgressNote.id.desc()), page, 25)
    counts = {s: 0 for s in PROGRESS_STATUSES}
    for s, n in (db.query(ProgressNote.status, func.count(ProgressNote.id))
                 .filter(ProgressNote.employee_id == me_id).group_by(ProgressNote.status).all()):
        counts[s] = n
    rated = db.query(func.count(ProgressNote.id)).filter(
        ProgressNote.employee_id == me_id, ProgressNote.manager_rating.isnot(None)).scalar() or 0
    avg = db.query(func.avg(ProgressNote.manager_rating)).filter(ProgressNote.employee_id == me_id).scalar()
    data = _base(ctx, "progress")
    data.update({
        "page": pg, "counts": counts, "rated": rated, "avg": round(float(avg), 2) if avg is not None else 0,
        "filters": {"status": status, "date_from": date_from, "date_to": date_to},
        "status_options": [(s, s.title()) for s in PROGRESS_STATUSES],
        "base_url": f"/hr/me/progress?status={status}&date_from={date_from}&date_to={date_to}",
    })
    return render(request, "hr/ess_progress.html", data)


def _own_note(db: Session, me: Employee, note_id: int) -> ProgressNote:
    note = db.get(ProgressNote, note_id)
    if note is None or note.employee_id != me.id:
        raise PermissionDenied("portal_self.update (that progress note is not yours)")
    return note


@router.post("/progress/new", include_in_schema=False)
async def progress_new(request: Request, db: Session = Depends(get_db),
                       ctx: UserContext = Depends(get_user_context),
                       user: User = Depends(require("portal_self.view"))):
    """Create: saved as a draft, or submitted straight away if the employee asks for that."""
    if ctx.employee is None:
        return _no_employee("/hr/me")
    form = await request.form()
    detail = (form.get("detail") or "").strip()
    day = parse_date(form.get("working_date")) or date.today()
    if not detail:
        return redirect("/hr/me/progress", "Describe what you worked on.", "error")
    submit = (form.get("action") or "").lower() == "submit"
    note = ProgressNote(employee_id=ctx.employee.id, working_date=day, detail=detail,
                        status="submitted" if submit else "draft", created_by_id=user.id)
    db.add(note)
    db.flush()
    log_action(db, user, "create", "employees", entity=note, request=request, rationale=detail,
               description=f"Progress note for {day} {'submitted' if submit else 'saved as a draft'} by "
                           f"{ctx.employee.employee_code}")
    db.commit()
    return redirect("/hr/me/progress",
                    f"Progress for {day} {'submitted to your manager.' if submit else 'saved as a draft.'}")


@router.post("/progress/{note_id}/edit", include_in_schema=False)
async def progress_edit(note_id: int, request: Request, db: Session = Depends(get_db),
                        ctx: UserContext = Depends(get_user_context),
                        user: User = Depends(require("portal_self.view"))):
    """A draft may be rewritten as often as you like. Once submitted it is out of the employee's hands."""
    if ctx.employee is None:
        return _no_employee("/hr/me")
    note = _own_note(db, ctx.employee, note_id)
    if note.status != "draft":
        return redirect("/hr/me/progress",
                        f"Progress note #{note.id} has been submitted and can no longer be edited. "
                        f"Ask People and Culture if it has to be corrected.", "error")
    form = await request.form()
    detail = (form.get("detail") or "").strip()
    if not detail:
        return redirect("/hr/me/progress", "Describe what you worked on.", "error")
    before = {"detail": note.detail, "working_date": str(note.working_date)}
    note.detail = detail
    note.working_date = parse_date(form.get("working_date")) or note.working_date
    log_action(db, user, "update", "employees", entity=note, request=request, rationale=detail,
               description=f"Draft progress note #{note.id} edited by {ctx.employee.employee_code}",
               before=before, after={"detail": detail, "working_date": str(note.working_date)})
    db.commit()
    return redirect("/hr/me/progress", f"Draft for {note.working_date} updated.")


@router.post("/progress/{note_id}/submit", include_in_schema=False)
async def progress_submit(note_id: int, request: Request, db: Session = Depends(get_db),
                          ctx: UserContext = Depends(get_user_context),
                          user: User = Depends(require("portal_self.view"))):
    """Submitting hands the day to the manager: it locks for the employee and becomes rateable."""
    if ctx.employee is None:
        return _no_employee("/hr/me")
    note = _own_note(db, ctx.employee, note_id)
    if note.status == "submitted":
        return redirect("/hr/me/progress", f"Progress note #{note.id} was already submitted.", "info")
    note.status = "submitted"
    log_action(db, user, "update", "employees", entity=note, request=request, rationale=note.detail,
               description=f"Progress note #{note.id} for {note.working_date} submitted for rating by "
                           f"{ctx.employee.employee_code}",
               before={"status": "draft"}, after={"status": "submitted"})
    manager = note.employee.manager if note.employee else None
    if manager is not None and manager.user_id:
        notify(db, manager.user_id, "Progress sheet submitted",
               f"{ctx.employee.full_name} submitted their progress for {note.working_date}.",
               event_type="hr_progress", link="/hr/progress-sheet")
    db.commit()
    return redirect("/hr/me/progress", f"Progress for {note.working_date} submitted; your manager can rate it now.")
