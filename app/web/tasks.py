"""Module 29 - Tasks, Projects, Sprints & Milestones (accountability engine)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import PermissionDenied, csrf_protect, require
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import paginate, parse_date, parse_float, parse_int, redirect
from app.database import get_db
from app.models.core import Department, User
from app.models.ops import KPI, Milestone, Project, Sprint, Task, TaskComment
from app.models.people import Employee

router = APIRouter(prefix="/tasks", dependencies=[Depends(csrf_protect)])

BOARD_COLUMNS = [("todo", "To do", "slate"), ("in_progress", "In progress", "indigo"),
                 ("review", "Review", "violet"), ("done", "Done", "emerald")]
STATUSES = ["todo", "in_progress", "review", "done", "cancelled"]
PRIORITIES = ["low", "medium", "high", "urgent"]
RECURRENCES = [("", "None"), ("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly")]


# ------------------------------------------------------------------------------------------------ helpers
def _team_user_ids(db: Session, user: User) -> list[int]:
    """Users reporting to me (via Employee.manager_id) plus my own department."""
    ids = {user.id}
    emp = db.query(Employee).filter(Employee.user_id == user.id).first()
    if emp:
        for r in db.query(Employee).filter(Employee.manager_id == emp.id):
            if r.user_id:
                ids.add(r.user_id)
    if user.department_id:
        for r in db.query(User.id).filter(User.department_id == user.department_id):
            ids.add(r[0])
    return list(ids)


def _can_see_all(user: User) -> bool:
    return rbac.is_management(user) or rbac.has_permission(user, "tasks.assign")


def _visible_view(user: User, view: str) -> str:
    if view not in ("my", "team", "all"):
        view = "my"
    if view == "all" and not _can_see_all(user):
        return "my"
    return view


def _user_options(db: Session) -> list[tuple]:
    return [(u.id, u.full_name) for u in db.query(User).filter(User.is_active.is_(True)).order_by(User.full_name)]


def _form_options(db: Session) -> dict:
    return {
        "users": _user_options(db),
        "projects": [(p.id, p.name) for p in db.query(Project).order_by(Project.name)],
        "sprints": [(s.id, f"{s.project.name if s.project else ''} · {s.name}") for s in db.query(Sprint).order_by(Sprint.start_date.desc()).limit(60)],
        "milestones": [(m.id, f"{m.project.name if m.project else ''} · {m.title}") for m in db.query(Milestone).order_by(Milestone.due_date.desc()).limit(60)],
        "departments": [(d.id, d.name) for d in db.query(Department).order_by(Department.name)],
        "kpis": [(k.id, f"{k.code} — {k.name}") for k in db.query(KPI).filter(KPI.is_active.is_(True)).order_by(KPI.name)],
        "tasks": [(t.id, t.title[:70]) for t in db.query(Task).filter(Task.status != "done").order_by(Task.id.desc()).limit(120)],
        "priorities": PRIORITIES, "statuses": STATUSES, "recurrences": RECURRENCES,
    }


def _scoped_query(db: Session, user: User, view: str):
    q = db.query(Task)
    if view == "my":
        q = q.filter(or_(Task.assignee_id == user.id, Task.creator_id == user.id))
    elif view == "team":
        q = q.filter(Task.assignee_id.in_(_team_user_ids(db, user)))
    return q


def _apply_filters(q, assignee_id, project_id, priority, status, department_id, due, overdue, qtext):
    if assignee_id:
        q = q.filter(Task.assignee_id == assignee_id)
    if project_id:
        q = q.filter(Task.project_id == project_id)
    if priority:
        q = q.filter(Task.priority == priority)
    if status:
        q = q.filter(Task.status == status)
    if department_id:
        q = q.filter(Task.department_id == department_id)
    if due:
        today = date.today()
        if due == "today":
            q = q.filter(Task.due_date == today)
        elif due == "week":
            q = q.filter(Task.due_date >= today, Task.due_date <= today + timedelta(days=7))
        elif due == "no_date":
            q = q.filter(Task.due_date.is_(None))
    if overdue:
        q = q.filter(Task.due_date < date.today(), Task.status.in_(["todo", "in_progress", "review"]))
    if qtext:
        like = f"%{qtext}%"
        q = q.filter(or_(Task.title.ilike(like), Task.description.ilike(like)))
    return q


def _workload(db: Session, user: User) -> dict:
    base = db.query(Task).filter(Task.assignee_id == user.id)
    open_q = base.filter(Task.status.in_(["todo", "in_progress", "review"]))
    today = date.today()
    by_status = {s: base.filter(Task.status == s).count() for s in STATUSES}
    est = db.query(func.sum(Task.estimate_hours)).filter(Task.assignee_id == user.id,
                                                         Task.status.in_(["todo", "in_progress", "review"])).scalar()
    done_30 = base.filter(Task.status == "done", Task.completed_at >= datetime.utcnow() - timedelta(days=30)).count()
    avg_score = db.query(func.avg(Task.assessment_score)).filter(Task.assignee_id == user.id,
                                                                 Task.assessment_score.isnot(None)).scalar()
    return {
        "open": open_q.count(), "overdue": open_q.filter(Task.due_date < today).count(),
        "today": open_q.filter(Task.due_date == today).count(),
        "week": open_q.filter(Task.due_date >= today, Task.due_date <= today + timedelta(days=7)).count(),
        "escalated": open_q.filter(Task.escalated.is_(True)).count(),
        "by_status": by_status, "estimate_hours": round(float(est or 0), 1), "done_30": done_30,
        "avg_score": round(float(avg_score), 2) if avg_score is not None else None,
        "upcoming": open_q.filter(Task.due_date.isnot(None)).order_by(Task.due_date).limit(6).all(),
    }


def _guard(user: User, task: Task, write: bool = False) -> None:
    if _can_see_all(user):
        return
    if task.assignee_id == user.id or task.creator_id == user.id:
        return
    raise PermissionDenied("tasks.view")


def _recalc_project(db: Session, project_id: int | None) -> None:
    if not project_id:
        return
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        return
    total = db.query(Task).filter(Task.project_id == p.id, Task.status != "cancelled").count()
    done = db.query(Task).filter(Task.project_id == p.id, Task.status == "done").count()
    p.progress_pct = int(round(100 * done / total)) if total else 0


# ================================================================================================ projects
@router.get("/projects", include_in_schema=False)
def projects(request: Request, q: str = "", status: str = "", db: Session = Depends(get_db),
             user: User = Depends(require("tasks.view"))):
    query = db.query(Project)
    if q:
        query = query.filter(Project.name.ilike(f"%{q}%"))
    if status:
        query = query.filter(Project.status == status)
    items = query.order_by(Project.status, Project.name).all()
    rows = []
    for p in items:
        total = db.query(Task).filter(Task.project_id == p.id, Task.status != "cancelled").count()
        done = db.query(Task).filter(Task.project_id == p.id, Task.status == "done").count()
        overdue = db.query(Task).filter(Task.project_id == p.id, Task.status.in_(["todo", "in_progress", "review"]),
                                        Task.due_date < date.today()).count()
        rows.append({"p": p, "total": total, "done": done, "overdue": overdue,
                     "pct": int(round(100 * done / total)) if total else 0,
                     "sprints": db.query(Sprint).filter(Sprint.project_id == p.id).count(),
                     "milestones": db.query(Milestone).filter(Milestone.project_id == p.id).count()})
    return render(request, "tasks/projects.html", {
        "user": user, "rows": rows, "q": q, "status": status, "opts": _form_options(db),
        "statuses": ["planning", "active", "on_hold", "completed", "cancelled"]})


@router.post("/projects/create", include_in_schema=False)
async def create_project(request: Request, db: Session = Depends(get_db), user: User = Depends(require("tasks.add"))):
    f = await request.form()
    name = (f.get("name") or "").strip()
    if not name:
        return redirect("/tasks/projects", "Project name is required.", "error")
    p = Project(name=name, code=(f.get("code") or "").strip() or None, description=f.get("description") or None,
                department_id=parse_int(f.get("department_id")), owner_id=parse_int(f.get("owner_id")) or user.id,
                status=f.get("status") or "active", priority=f.get("priority") or "medium",
                start_date=parse_date(f.get("start_date")), end_date=parse_date(f.get("end_date")))
    db.add(p)
    db.flush()
    log_action(db, user, "create", "tasks", entity=p, description=f"Project created: {p.name}", request=request)
    db.commit()
    return redirect(f"/tasks/projects/{p.id}", "Project created.")


@router.get("/projects/{project_id}", include_in_schema=False)
def project_detail(project_id: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("tasks.view"))):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        return redirect("/tasks/projects", "Project not found.", "error")
    tasks = db.query(Task).filter(Task.project_id == p.id).order_by(Task.status, Task.due_date).all()
    total = len([t for t in tasks if t.status != "cancelled"])
    done = len([t for t in tasks if t.status == "done"])
    sprints = db.query(Sprint).filter(Sprint.project_id == p.id).order_by(Sprint.start_date.desc()).all()
    milestones = db.query(Milestone).filter(Milestone.project_id == p.id).order_by(Milestone.due_date).all()
    board = {c[0]: [t for t in tasks if t.status == c[0]] for c in BOARD_COLUMNS}
    return render(request, "tasks/project_detail.html", {
        "user": user, "p": p, "tasks": tasks, "board": board, "columns": BOARD_COLUMNS, "sprints": sprints,
        "milestones": milestones, "pct": int(round(100 * done / total)) if total else 0, "total": total, "done": done,
        "opts": _form_options(db),
        "achieved": len([m for m in milestones if m.status == "achieved"]),
        "missed": len([m for m in milestones if m.status == "missed"])})


@router.post("/projects/{project_id}/edit", include_in_schema=False)
async def edit_project(project_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("tasks.update"))):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        return redirect("/tasks/projects", "Project not found.", "error")
    f = await request.form()
    before = {"name": p.name, "status": p.status}
    p.name = (f.get("name") or p.name).strip()
    p.description = f.get("description") or p.description
    p.status = f.get("status") or p.status
    p.priority = f.get("priority") or p.priority
    p.owner_id = parse_int(f.get("owner_id"), p.owner_id)
    p.department_id = parse_int(f.get("department_id"), p.department_id)
    p.start_date = parse_date(f.get("start_date"), p.start_date)
    p.end_date = parse_date(f.get("end_date"), p.end_date)
    log_action(db, user, "update", "tasks", entity=p, description=f"Project updated: {p.name}",
               before=before, after={"name": p.name, "status": p.status}, request=request)
    db.commit()
    return redirect(f"/tasks/projects/{p.id}", "Project updated.")


@router.get("/projects/{project_id}/sprints", include_in_schema=False)
def sprint_board(project_id: int, request: Request, sprint_id: int | None = None, db: Session = Depends(get_db),
                 user: User = Depends(require("tasks.view"))):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        return redirect("/tasks/projects", "Project not found.", "error")
    sprints = db.query(Sprint).filter(Sprint.project_id == p.id).order_by(Sprint.start_date.desc()).all()
    active = None
    if sprint_id:
        active = next((s for s in sprints if s.id == sprint_id), None)
    if active is None:
        active = next((s for s in sprints if s.status == "active"), None) or (sprints[0] if sprints else None)
    tasks = db.query(Task).filter(Task.sprint_id == active.id).all() if active else []
    board = {c[0]: [t for t in tasks if t.status == c[0]] for c in BOARD_COLUMNS}
    done = len([t for t in tasks if t.status == "done"])
    return render(request, "tasks/sprints.html", {
        "user": user, "p": p, "sprints": sprints, "active": active, "board": board, "columns": BOARD_COLUMNS,
        "tasks": tasks, "done": done, "pct": int(round(100 * done / len(tasks))) if tasks else 0,
        "opts": _form_options(db)})


@router.post("/projects/{project_id}/sprints/create", include_in_schema=False)
async def create_sprint(project_id: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("tasks.add"))):
    f = await request.form()
    start = parse_date(f.get("start_date"), date.today())
    s = Sprint(project_id=project_id, name=(f.get("name") or "Sprint").strip(), goal=f.get("goal") or None,
               start_date=start, end_date=parse_date(f.get("end_date"), start + timedelta(days=13)),
               status=f.get("status") or "planned")
    db.add(s)
    db.flush()
    log_action(db, user, "create", "tasks", entity=s, description=f"Sprint created: {s.name}", request=request)
    db.commit()
    return redirect(f"/tasks/projects/{project_id}/sprints?sprint_id={s.id}", "Sprint created.")


@router.post("/sprints/{sprint_id}/status", include_in_schema=False)
async def sprint_status(sprint_id: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("tasks.update"))):
    s = db.query(Sprint).filter(Sprint.id == sprint_id).first()
    if not s:
        return redirect("/tasks/projects", "Sprint not found.", "error")
    f = await request.form()
    before = s.status
    s.status = f.get("status") or s.status
    if s.status == "active":
        for other in db.query(Sprint).filter(Sprint.project_id == s.project_id, Sprint.id != s.id, Sprint.status == "active"):
            other.status = "completed"
    log_action(db, user, "update", "tasks", entity=s, description=f"Sprint {s.name}: {before} -> {s.status}", request=request)
    db.commit()
    return redirect(f"/tasks/projects/{s.project_id}/sprints?sprint_id={s.id}", f"Sprint marked {s.status}.")


@router.post("/projects/{project_id}/milestones/create", include_in_schema=False)
async def create_milestone(project_id: int, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require("tasks.add"))):
    f = await request.form()
    m = Milestone(project_id=project_id, title=(f.get("title") or "Milestone").strip(),
                  due_date=parse_date(f.get("due_date")), status="pending")
    db.add(m)
    db.flush()
    log_action(db, user, "create", "tasks", entity=m, description=f"Milestone created: {m.title}", request=request)
    db.commit()
    return redirect(f"/tasks/projects/{project_id}", "Milestone created.")


@router.post("/milestones/{milestone_id}/status", include_in_schema=False)
async def milestone_status(milestone_id: int, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require("tasks.update"))):
    m = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not m:
        return redirect("/tasks/projects", "Milestone not found.", "error")
    f = await request.form()
    before = m.status
    m.status = f.get("status") or "achieved"
    m.achieved_at = date.today() if m.status == "achieved" else None
    log_action(db, user, "update", "tasks", entity=m, description=f"Milestone {m.title}: {before} -> {m.status}",
               request=request)
    db.commit()
    return redirect(f"/tasks/projects/{m.project_id}", f"Milestone marked {m.status}.")


# ================================================================================================ task board
@router.get("", include_in_schema=False)
def task_list(request: Request, view: str = "my", layout: str = "board", page: int = 1, q: str = "",
              assignee_id: int | None = None, project_id: int | None = None, priority: str = "",
              status: str = "", department_id: int | None = None, due: str = "", overdue: int = 0,
              db: Session = Depends(get_db), user: User = Depends(require("tasks.view"))):
    view = _visible_view(user, view)
    base = _apply_filters(_scoped_query(db, user, view), assignee_id, project_id, priority, status,
                          department_id, due, overdue, q)
    board = {}
    if layout == "board":
        for key, _label, _c in BOARD_COLUMNS:
            board[key] = base.filter(Task.status == key).order_by(Task.due_date.is_(None), Task.due_date,
                                                                  Task.priority.desc()).limit(60).all()
    pg = paginate(base.order_by(Task.status, Task.due_date.is_(None), Task.due_date, Task.id.desc()), page, 30)
    counts = {key: _apply_filters(_scoped_query(db, user, view), assignee_id, project_id, priority, "",
                                  department_id, due, overdue, q).filter(Task.status == key).count()
              for key, _l, _c in BOARD_COLUMNS}
    qs = (f"view={view}&layout={layout}&q={q}&priority={priority}&status={status}&due={due}"
          f"&overdue={overdue or ''}&assignee_id={assignee_id or ''}&project_id={project_id or ''}"
          f"&department_id={department_id or ''}")
    return render(request, "tasks/list.html", {
        "user": user, "view": view, "layout": layout, "board": board, "columns": BOARD_COLUMNS, "page": pg,
        "counts": counts, "q": q, "priority": priority, "status": status, "due": due, "overdue": overdue,
        "assignee_id": assignee_id, "project_id": project_id, "department_id": department_id,
        "opts": _form_options(db), "workload": _workload(db, user), "can_all": _can_see_all(user), "qs": qs,
        "today": date.today()})


@router.post("/create", include_in_schema=False)
async def create_task(request: Request, db: Session = Depends(get_db), user: User = Depends(require("tasks.add"))):
    f = await request.form()
    title = (f.get("title") or "").strip()
    if not title:
        return redirect("/tasks", "A task title is required.", "error")
    assignee_id = parse_int(f.get("assignee_id")) or user.id
    dept_id = parse_int(f.get("department_id"))
    if not dept_id:
        assignee = db.query(User).filter(User.id == assignee_id).first()
        dept_id = assignee.department_id if assignee else None
    t = Task(title=title, description=f.get("description") or None, project_id=parse_int(f.get("project_id")),
             sprint_id=parse_int(f.get("sprint_id")), milestone_id=parse_int(f.get("milestone_id")),
             assignee_id=assignee_id, creator_id=user.id, department_id=dept_id,
             priority=f.get("priority") or "medium", status="todo", due_date=parse_date(f.get("due_date")),
             estimate_hours=parse_float(f.get("estimate_hours")) or None,
             recurrence=(f.get("recurrence") or None) or None, depends_on_id=parse_int(f.get("depends_on_id")),
             kpi_id=parse_int(f.get("kpi_id")), entity_type=(f.get("entity_type") or None),
             entity_id=parse_int(f.get("entity_id")))
    db.add(t)
    db.flush()
    _recalc_project(db, t.project_id)
    if t.assignee_id and t.assignee_id != user.id:
        notify(db, t.assignee_id, "New task assigned", f"{t.title}" + (f" (due {t.due_date})" if t.due_date else ""),
               event_type="task_assigned", link=f"/tasks/{t.id}")
    log_action(db, user, "create", "tasks", entity=t, description=f"Task created: {t.title}", request=request)
    db.commit()
    return redirect(f"/tasks/{t.id}", "Task created.")


@router.get("/{task_id}", include_in_schema=False)
def task_detail(task_id: int, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require("tasks.view"))):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        return redirect("/tasks", "Task not found.", "error")
    _guard(user, t)
    blocked = bool(t.depends_on and t.depends_on.status != "done")
    blocking = db.query(Task).filter(Task.depends_on_id == t.id).all()
    kpi = db.query(KPI).filter(KPI.id == t.kpi_id).first() if t.kpi_id else None
    can_assess = user.id == t.creator_id or rbac.is_management(user)
    return render(request, "tasks/detail.html", {
        "user": user, "t": t, "blocked": blocked, "blocking": blocking, "kpi": kpi, "opts": _form_options(db),
        "comments": db.query(TaskComment).filter(TaskComment.task_id == t.id).order_by(TaskComment.created_at).all(),
        "can_assess": can_assess, "columns": BOARD_COLUMNS, "today": date.today()})


@router.post("/{task_id}/edit", include_in_schema=False)
async def edit_task(task_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("tasks.update"))):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        return redirect("/tasks", "Task not found.", "error")
    _guard(user, t, write=True)
    f = await request.form()
    before = {"title": t.title, "assignee_id": t.assignee_id, "priority": t.priority, "due_date": t.due_date}
    old_project = t.project_id
    t.title = (f.get("title") or t.title).strip()
    t.description = f.get("description") or None
    t.project_id = parse_int(f.get("project_id"))
    t.sprint_id = parse_int(f.get("sprint_id"))
    t.milestone_id = parse_int(f.get("milestone_id"))
    t.assignee_id = parse_int(f.get("assignee_id"), t.assignee_id)
    t.department_id = parse_int(f.get("department_id"))
    t.priority = f.get("priority") or t.priority
    t.due_date = parse_date(f.get("due_date"))
    t.estimate_hours = parse_float(f.get("estimate_hours")) or None
    t.recurrence = (f.get("recurrence") or None) or None
    dep = parse_int(f.get("depends_on_id"))
    t.depends_on_id = dep if dep != t.id else None
    t.kpi_id = parse_int(f.get("kpi_id"))
    t.entity_type = f.get("entity_type") or None
    t.entity_id = parse_int(f.get("entity_id"))
    if t.due_date and t.due_date >= date.today():
        t.escalated = False
    _recalc_project(db, old_project)
    _recalc_project(db, t.project_id)
    if t.assignee_id != before["assignee_id"] and t.assignee_id:
        notify(db, t.assignee_id, "Task reassigned to you", t.title, event_type="task_assigned", link=f"/tasks/{t.id}")
    log_action(db, user, "update", "tasks", entity=t, description=f"Task updated: {t.title}", before=before,
               after={"title": t.title, "assignee_id": t.assignee_id, "priority": t.priority, "due_date": t.due_date},
               request=request)
    db.commit()
    return redirect(f"/tasks/{t.id}", "Task updated.")


@router.post("/{task_id}/move", include_in_schema=False)
async def move_task(task_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("tasks.update"))):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        return redirect("/tasks", "Task not found.", "error")
    _guard(user, t, write=True)
    f = await request.form()
    new_status = f.get("status") or "todo"
    back = f.get("back") or f"/tasks/{t.id}"
    if new_status not in STATUSES:
        return redirect(back, "Unknown status.", "error")
    if new_status == "in_progress" and t.depends_on and t.depends_on.status != "done":
        return redirect(back, f"Blocked: dependency '{t.depends_on.title}' is not done yet.", "error")
    if new_status == "done":
        return await _complete(db, user, t, f, request, back)
    before = t.status
    t.status = new_status
    if new_status != "done":
        t.completed_at = None
    log_action(db, user, "update", "tasks", entity=t, description=f"Task status {before} -> {new_status}", request=request)
    _recalc_project(db, t.project_id)
    db.commit()
    return redirect(back, f"Task moved to {new_status.replace('_', ' ')}.")


async def _complete(db: Session, user: User, t: Task, f, request: Request, back: str):
    before = t.status
    t.status = "done"
    t.completed_at = datetime.utcnow()
    t.escalated = False
    score = parse_int(f.get("assessment_score"))
    if score and 1 <= score <= 5 and (user.id == t.creator_id or rbac.is_management(user)):
        t.assessment_score = score
    if t.milestone_id:
        m = db.query(Milestone).filter(Milestone.id == t.milestone_id).first()
        if m and m.status == "pending":
            remaining = db.query(Task).filter(Task.milestone_id == m.id, Task.status.notin_(["done", "cancelled"])).count()
            if remaining == 0:
                m.status = "achieved" if (not m.due_date or date.today() <= m.due_date) else "missed"
                m.achieved_at = date.today()
    _recalc_project(db, t.project_id)
    if t.creator_id and t.creator_id != user.id:
        notify(db, t.creator_id, "Task completed", f"{t.title} was completed by {user.full_name}.",
               event_type="task_completed", link=f"/tasks/{t.id}")
    log_action(db, user, "update", "tasks", entity=t,
               description=f"Task completed ({before} -> done)" + (f", assessment {t.assessment_score}/5" if t.assessment_score else ""),
               request=request)
    db.commit()
    return redirect(back, "Task completed.")


@router.post("/{task_id}/complete", include_in_schema=False)
async def complete_task(task_id: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("tasks.update"))):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        return redirect("/tasks", "Task not found.", "error")
    _guard(user, t, write=True)
    f = await request.form()
    if t.depends_on and t.depends_on.status != "done":
        return redirect(f"/tasks/{t.id}", f"Blocked: dependency '{t.depends_on.title}' is not done yet.", "error")
    return await _complete(db, user, t, f, request, f.get("back") or f"/tasks/{t.id}")


@router.post("/{task_id}/comment", include_in_schema=False)
async def comment_task(task_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("tasks.view"))):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        return redirect("/tasks", "Task not found.", "error")
    _guard(user, t)
    f = await request.form()
    text = (f.get("text") or "").strip()
    if not text:
        return redirect(f"/tasks/{t.id}", "Comment cannot be empty.", "error")
    db.add(TaskComment(task_id=t.id, user_id=user.id, text=text))
    for uid in {t.assignee_id, t.creator_id} - {user.id, None}:
        notify(db, uid, "New task comment", f"{user.full_name} commented on '{t.title}'.",
               event_type="task_comment", link=f"/tasks/{t.id}")
    log_action(db, user, "create", "tasks", entity=t, description="Comment added", request=request)
    db.commit()
    return redirect(f"/tasks/{t.id}", "Comment added.")


@router.post("/{task_id}/delete", include_in_schema=False)
async def delete_task(task_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("tasks.delete"))):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        return redirect("/tasks", "Task not found.", "error")
    f = await request.form()
    project_id = t.project_id
    log_action(db, user, "delete", "tasks", entity=t, description=f"Task deleted: {t.title}",
               rationale=f.get("rationale") or f.get("reason"), request=request)
    db.query(Task).filter(Task.depends_on_id == t.id).update({"depends_on_id": None})
    db.delete(t)
    db.flush()
    _recalc_project(db, project_id)
    db.commit()
    return redirect("/tasks", "Task deleted.")
