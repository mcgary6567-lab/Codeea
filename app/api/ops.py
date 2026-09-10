"""REST API: operations - tasks, projects, KPIs, decisions, daily reports and the command-center summary.

Mounted at /api/v1/ops. Auth: Bearer token (POST /api/v1/auth/login) or X-API-Key.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import require
from app.core.notify import notify
from app.database import get_db
from app.models.core import Department, User
from app.models.ops import (KPI, DailyReport, Decision, DepartmentScorecard, KPIValue, Milestone, Project, Sprint,
                            Task, TrajectoryMeeting, TransformationItem)
from app.services import kpi as kpi_svc

router = APIRouter(prefix="/ops", tags=["ops"])


# ------------------------------------------------------------------ schemas
class TaskOut(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    status: str
    priority: str
    project_id: Optional[int] = None
    sprint_id: Optional[int] = None
    assignee_id: Optional[int] = None
    assignee: Optional[str] = None
    creator_id: Optional[int] = None
    department_id: Optional[int] = None
    due_date: Optional[date] = None
    completed_at: Optional[datetime] = None
    estimate_hours: Optional[float] = None
    recurrence: Optional[str] = None
    depends_on_id: Optional[int] = None
    kpi_id: Optional[int] = None
    escalated: bool = False
    assessment_score: Optional[int] = None
    is_overdue: bool = False


class TaskIn(BaseModel):
    title: str
    description: Optional[str] = None
    assignee_id: Optional[int] = None
    project_id: Optional[int] = None
    sprint_id: Optional[int] = None
    department_id: Optional[int] = None
    priority: str = Field("medium", pattern="^(low|medium|high|urgent)$")
    due_date: Optional[date] = None
    estimate_hours: Optional[float] = None
    recurrence: Optional[str] = Field(None, pattern="^(daily|weekly|monthly)$")
    depends_on_id: Optional[int] = None
    kpi_id: Optional[int] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None


class TaskPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(todo|in_progress|review|done|cancelled)$")
    priority: Optional[str] = Field(None, pattern="^(low|medium|high|urgent)$")
    assignee_id: Optional[int] = None
    due_date: Optional[date] = None
    estimate_hours: Optional[float] = None
    assessment_score: Optional[int] = Field(None, ge=1, le=5)


class ProjectOut(BaseModel):
    id: int
    name: str
    code: Optional[str] = None
    description: Optional[str] = None
    status: str
    priority: str
    owner_id: Optional[int] = None
    department_id: Optional[int] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    progress_pct: int
    tasks_total: int = 0
    tasks_done: int = 0
    sprints: int = 0
    milestones: int = 0


class ProjectIn(BaseModel):
    name: str
    code: Optional[str] = None
    description: Optional[str] = None
    department_id: Optional[int] = None
    owner_id: Optional[int] = None
    status: str = Field("active", pattern="^(planning|active|on_hold|completed|cancelled)$")
    priority: str = Field("medium", pattern="^(low|medium|high|urgent)$")
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class KPIOut(BaseModel):
    id: int
    code: str
    name: str
    description: Optional[str] = None
    unit: str
    target: Optional[float] = None
    direction: str
    frequency: str
    role_slug: Optional[str] = None
    department_id: Optional[int] = None
    formula_key: Optional[str] = None
    is_active: bool
    period: Optional[str] = None
    value: Optional[float] = None
    rag: Optional[str] = None
    source: Optional[str] = None


class KPIValueIn(BaseModel):
    period: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    value: float
    target: Optional[float] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None


class KPIValueOut(BaseModel):
    period: str
    value: float
    target: Optional[float] = None
    source: str
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    created_at: Optional[datetime] = None


class DecisionOut(BaseModel):
    id: int
    title: str
    category: str
    rationale: str
    status: str
    owner_id: Optional[int] = None
    owner: Optional[str] = None
    decided_by_id: Optional[int] = None
    department_id: Optional[int] = None
    meeting_id: Optional[int] = None
    due_date: Optional[date] = None
    outcome: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    created_at: Optional[datetime] = None
    is_overdue: bool = False


class DecisionIn(BaseModel):
    title: str
    rationale: str = Field(..., min_length=10)
    owner_id: int
    category: str = Field("operational", pattern="^(operational|academic|financial|hr|strategic)$")
    status: str = Field("decided", pattern="^(proposed|decided)$")
    department_id: Optional[int] = None
    meeting_id: Optional[int] = None
    due_date: Optional[date] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None


class DecisionStatusIn(BaseModel):
    status: str = Field(..., pattern="^(proposed|decided|implemented|reversed)$")
    outcome: Optional[str] = None
    rationale: Optional[str] = None


class DailyReportOut(BaseModel):
    id: int
    user_id: int
    user: Optional[str] = None
    department_id: Optional[int] = None
    report_date: date
    slot: str
    content: dict = {}
    summary: Optional[str] = None
    submitted_at: Optional[datetime] = None
    is_late: bool = False


class DailyReportIn(BaseModel):
    slot: str = Field("morning", pattern="^(morning|afternoon)$")
    report_date: Optional[date] = None
    content: dict = Field(default_factory=dict)
    summary: Optional[str] = None


# ------------------------------------------------------------------ serialisers
def _task_out(t: Task) -> TaskOut:
    return TaskOut(
        id=t.id, title=t.title, description=t.description, status=t.status, priority=t.priority,
        project_id=t.project_id, sprint_id=t.sprint_id, assignee_id=t.assignee_id,
        assignee=t.assignee.full_name if t.assignee else None, creator_id=t.creator_id,
        department_id=t.department_id, due_date=t.due_date, completed_at=t.completed_at,
        estimate_hours=t.estimate_hours, recurrence=t.recurrence, depends_on_id=t.depends_on_id,
        kpi_id=t.kpi_id, escalated=bool(t.escalated), assessment_score=t.assessment_score,
        is_overdue=bool(t.due_date and t.due_date < date.today() and t.status in ("todo", "in_progress", "review")))


def _project_out(db: Session, p: Project) -> ProjectOut:
    total = db.query(Task).filter(Task.project_id == p.id, Task.status != "cancelled").count()
    done = db.query(Task).filter(Task.project_id == p.id, Task.status == "done").count()
    return ProjectOut(id=p.id, name=p.name, code=p.code, description=p.description, status=p.status,
                      priority=p.priority, owner_id=p.owner_id, department_id=p.department_id,
                      start_date=p.start_date, end_date=p.end_date, progress_pct=p.progress_pct,
                      tasks_total=total, tasks_done=done,
                      sprints=db.query(Sprint).filter(Sprint.project_id == p.id).count(),
                      milestones=db.query(Milestone).filter(Milestone.project_id == p.id).count())


def _kpi_out(db: Session, k: KPI, period: Optional[str] = None) -> KPIOut:
    p = period or kpi_svc.period_key()
    row = kpi_svc.kpi_row(db, k, p, live=True)
    return KPIOut(id=k.id, code=k.code, name=k.name, description=k.description, unit=k.unit, target=k.target,
                  direction=k.direction, frequency=k.frequency, role_slug=k.role_slug,
                  department_id=k.department_id, formula_key=k.formula_key, is_active=k.is_active,
                  period=p, value=row["value"], rag=row["rag"], source=row["source"])


def _decision_out(d: Decision) -> DecisionOut:
    return DecisionOut(id=d.id, title=d.title, category=d.category, rationale=d.rationale, status=d.status,
                       owner_id=d.owner_id, owner=d.owner.full_name if d.owner else None,
                       decided_by_id=d.decided_by_id, department_id=d.department_id, meeting_id=d.meeting_id,
                       due_date=d.due_date, outcome=d.outcome, entity_type=d.entity_type, entity_id=d.entity_id,
                       created_at=d.created_at,
                       is_overdue=bool(d.due_date and d.due_date < date.today()
                                       and d.status in ("proposed", "decided") and not d.outcome))


def _report_out(r: DailyReport) -> DailyReportOut:
    return DailyReportOut(id=r.id, user_id=r.user_id, user=r.user.full_name if r.user else None,
                          department_id=r.department_id, report_date=r.report_date, slot=r.slot,
                          content=r.content or {}, summary=r.summary, submitted_at=r.submitted_at,
                          is_late=bool(r.is_late))


# ================================================================== tasks
@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(status: str = "", assignee_id: Optional[int] = None, project_id: Optional[int] = None,
               priority: str = "", department_id: Optional[int] = None, overdue: bool = False, q: str = "",
               mine: bool = False, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db), user: User = Depends(require("tasks.view"))):
    query = db.query(Task)
    if mine:
        query = query.filter(or_(Task.assignee_id == user.id, Task.creator_id == user.id))
    if status:
        query = query.filter(Task.status == status)
    if assignee_id:
        query = query.filter(Task.assignee_id == assignee_id)
    if project_id:
        query = query.filter(Task.project_id == project_id)
    if priority:
        query = query.filter(Task.priority == priority)
    if department_id:
        query = query.filter(Task.department_id == department_id)
    if overdue:
        query = query.filter(Task.due_date < date.today(), Task.status.in_(["todo", "in_progress", "review"]))
    if q:
        query = query.filter(Task.title.ilike(f"%{q}%"))
    rows = query.order_by(Task.due_date.is_(None), Task.due_date, Task.id.desc()).offset(offset).limit(limit).all()
    return [_task_out(t) for t in rows]


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(payload: TaskIn, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require("tasks.add"))):
    assignee_id = payload.assignee_id or user.id
    assignee = db.query(User).filter(User.id == assignee_id).first()
    if assignee is None:
        raise HTTPException(status_code=400, detail="Unknown assignee")
    t = Task(title=payload.title, description=payload.description, project_id=payload.project_id,
             sprint_id=payload.sprint_id, assignee_id=assignee_id, creator_id=user.id,
             department_id=payload.department_id or assignee.department_id, priority=payload.priority,
             status="todo", due_date=payload.due_date, estimate_hours=payload.estimate_hours,
             recurrence=payload.recurrence, depends_on_id=payload.depends_on_id, kpi_id=payload.kpi_id,
             entity_type=payload.entity_type, entity_id=payload.entity_id)
    db.add(t)
    db.flush()
    if assignee_id != user.id:
        notify(db, assignee_id, "New task assigned", t.title, event_type="task_assigned", link=f"/tasks/{t.id}")
    log_action(db, user, "create", "tasks", entity=t, description=f"Task created via API: {t.title}", request=request)
    db.commit()
    return _task_out(t)


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(require("tasks.view"))):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_out(t)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def patch_task(task_id: int, payload: TaskPatch, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require("tasks.update"))):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    before = {"status": t.status, "priority": t.priority, "assignee_id": t.assignee_id, "due_date": t.due_date}
    data = payload.model_dump(exclude_unset=True)
    if data.get("status") == "done" and t.depends_on and t.depends_on.status != "done":
        raise HTTPException(status_code=409, detail=f"Blocked by dependency #{t.depends_on_id}")
    for field, value in data.items():
        setattr(t, field, value)
    if data.get("status") == "done":
        t.completed_at = datetime.utcnow()
        t.escalated = False
    elif "status" in data:
        t.completed_at = None
    log_action(db, user, "update", "tasks", entity=t, description=f"Task updated via API: {t.title}",
               before=before, after={"status": t.status, "priority": t.priority, "assignee_id": t.assignee_id,
                                     "due_date": t.due_date}, request=request)
    db.commit()
    return _task_out(t)


@router.post("/tasks/{task_id}/complete", response_model=TaskOut)
def complete_task(task_id: int, request: Request, assessment_score: Optional[int] = Query(None, ge=1, le=5),
                  db: Session = Depends(get_db), user: User = Depends(require("tasks.update"))):
    t = db.query(Task).filter(Task.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    if t.depends_on and t.depends_on.status != "done":
        raise HTTPException(status_code=409, detail=f"Blocked by dependency #{t.depends_on_id}")
    t.status = "done"
    t.completed_at = datetime.utcnow()
    t.escalated = False
    if assessment_score:
        t.assessment_score = assessment_score
    log_action(db, user, "update", "tasks", entity=t, description="Task completed via API", request=request)
    db.commit()
    return _task_out(t)


# ================================================================== projects
@router.get("/projects", response_model=list[ProjectOut])
def list_projects(status: str = "", db: Session = Depends(get_db), user: User = Depends(require("tasks.view"))):
    q = db.query(Project)
    if status:
        q = q.filter(Project.status == status)
    return [_project_out(db, p) for p in q.order_by(Project.name).all()]


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectIn, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("tasks.add"))):
    p = Project(name=payload.name, code=payload.code, description=payload.description,
                department_id=payload.department_id, owner_id=payload.owner_id or user.id, status=payload.status,
                priority=payload.priority, start_date=payload.start_date, end_date=payload.end_date)
    db.add(p)
    db.flush()
    log_action(db, user, "create", "tasks", entity=p, description=f"Project created via API: {p.name}", request=request)
    db.commit()
    return _project_out(db, p)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(require("tasks.view"))):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_out(db, p)


# ================================================================== KPIs
@router.get("/kpis", response_model=list[KPIOut])
def list_kpis(period: str = "", role: str = "", department_id: Optional[int] = None, active: bool = True,
              db: Session = Depends(get_db), user: User = Depends(require("kpis.view"))):
    q = db.query(KPI)
    if active:
        q = q.filter(KPI.is_active.is_(True))
    if role:
        q = q.filter(KPI.role_slug == role)
    if department_id:
        q = q.filter(KPI.department_id == department_id)
    p = period if len(period) == 7 else kpi_svc.period_key()
    return [_kpi_out(db, k, p) for k in q.order_by(KPI.role_slug, KPI.name).all()]


@router.get("/kpis/{code}", response_model=KPIOut)
def get_kpi(code: str, period: str = "", db: Session = Depends(get_db), user: User = Depends(require("kpis.view"))):
    k = db.query(KPI).filter(KPI.code == code).first()
    if not k:
        raise HTTPException(status_code=404, detail="KPI not found")
    return _kpi_out(db, k, period if len(period) == 7 else None)


@router.get("/kpis/{code}/history", response_model=list[KPIValueOut])
def kpi_history(code: str, months: int = Query(12, ge=1, le=36), db: Session = Depends(get_db),
                user: User = Depends(require("kpis.view"))):
    k = db.query(KPI).filter(KPI.code == code).first()
    if not k:
        raise HTTPException(status_code=404, detail="KPI not found")
    periods = kpi_svc.last_n_periods(months)
    rows = (db.query(KPIValue).filter(KPIValue.kpi_id == k.id, KPIValue.period.in_(periods))
            .order_by(KPIValue.period).all())
    return [KPIValueOut(period=v.period, value=v.value, target=v.target, source=v.source,
                        entity_type=v.entity_type, entity_id=v.entity_id, created_at=v.created_at) for v in rows]


@router.post("/kpis/{code}/values", response_model=KPIValueOut, status_code=201)
def record_kpi_value(code: str, payload: KPIValueIn, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("kpis.update"))):
    k = db.query(KPI).filter(KPI.code == code).first()
    if not k:
        raise HTTPException(status_code=404, detail="KPI not found")
    row = kpi_svc.upsert_value(db, k, payload.period, payload.value, source="manual",
                               entity_type=payload.entity_type, entity_id=payload.entity_id,
                               entered_by_id=user.id, target=payload.target)
    log_action(db, user, "update", "kpis", entity=k,
               description=f"KPI value recorded via API: {k.code} = {payload.value} ({payload.period})",
               request=request)
    db.commit()
    return KPIValueOut(period=row.period, value=row.value, target=row.target, source=row.source,
                       entity_type=row.entity_type, entity_id=row.entity_id, created_at=row.created_at)


# ================================================================== decisions
@router.get("/decisions", response_model=list[DecisionOut])
def list_decisions(status: str = "", category: str = "", owner_id: Optional[int] = None,
                   department_id: Optional[int] = None, overdue: bool = False,
                   limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                   db: Session = Depends(get_db), user: User = Depends(require("decisions.view"))):
    q = db.query(Decision)
    if status:
        q = q.filter(Decision.status == status)
    if category:
        q = q.filter(Decision.category == category)
    if owner_id:
        q = q.filter(Decision.owner_id == owner_id)
    if department_id:
        q = q.filter(Decision.department_id == department_id)
    if overdue:
        q = q.filter(Decision.due_date < date.today(), Decision.status.in_(["proposed", "decided"]),
                     or_(Decision.outcome.is_(None), Decision.outcome == ""))
    rows = q.order_by(Decision.created_at.desc()).offset(offset).limit(limit).all()
    return [_decision_out(d) for d in rows]


@router.post("/decisions", response_model=DecisionOut, status_code=201)
def create_decision(payload: DecisionIn, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("decisions.add"))):
    if not db.query(User).filter(User.id == payload.owner_id).first():
        raise HTTPException(status_code=400, detail="Unknown owner")
    d = Decision(title=payload.title, category=payload.category, rationale=payload.rationale,
                 owner_id=payload.owner_id, decided_by_id=user.id if payload.status == "decided" else None,
                 department_id=payload.department_id, meeting_id=payload.meeting_id, status=payload.status,
                 due_date=payload.due_date, entity_type=payload.entity_type, entity_id=payload.entity_id)
    db.add(d)
    db.flush()
    log_action(db, user, "create", "decisions", entity=d, description=f"Decision recorded via API: {d.title}",
               rationale=d.rationale, request=request)
    db.commit()
    return _decision_out(d)


@router.get("/decisions/{decision_id}", response_model=DecisionOut)
def get_decision(decision_id: int, db: Session = Depends(get_db), user: User = Depends(require("decisions.view"))):
    d = db.query(Decision).filter(Decision.id == decision_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Decision not found")
    return _decision_out(d)


@router.post("/decisions/{decision_id}/status", response_model=DecisionOut)
def set_decision_status(decision_id: int, payload: DecisionStatusIn, request: Request,
                        db: Session = Depends(get_db), user: User = Depends(require("decisions.update"))):
    d = db.query(Decision).filter(Decision.id == decision_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Decision not found")
    if payload.status == "reversed" and not (payload.rationale and len(payload.rationale) >= 10):
        raise HTTPException(status_code=422, detail="Reversing a decision requires a written rationale")
    if payload.status == "implemented" and not (payload.outcome or d.outcome):
        raise HTTPException(status_code=422, detail="Record the outcome before marking a decision implemented")
    before = {"status": d.status, "outcome": d.outcome}
    d.status = payload.status
    if payload.outcome:
        d.outcome = payload.outcome
    if payload.status == "reversed":
        d.outcome = ((d.outcome + "\n\n") if d.outcome else "") + \
                    f"Reversed on {date.today().isoformat()}: {payload.rationale}"
    if payload.status == "decided" and not d.decided_by_id:
        d.decided_by_id = user.id
    log_action(db, user, "override" if payload.status == "reversed" else "approve", "decisions", entity=d,
               description=f"Decision {before['status']} -> {d.status} via API", rationale=payload.rationale,
               before=before, after={"status": d.status, "outcome": d.outcome},
               severity="warning" if payload.status == "reversed" else "info", request=request)
    db.commit()
    return _decision_out(d)


# ================================================================== daily reports
@router.get("/daily-reports", response_model=list[DailyReportOut])
def list_daily_reports(start: Optional[date] = None, end: Optional[date] = None, slot: str = "",
                       user_id: Optional[int] = None, late_only: bool = False,
                       limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
                       user: User = Depends(require("daily_reports.view"))):
    from app.core import rbac
    d_end = end or date.today()
    d_start = start or (d_end - timedelta(days=13))
    q = db.query(DailyReport).filter(DailyReport.report_date >= d_start, DailyReport.report_date <= d_end)
    target = user_id or user.id
    if target != user.id and not (rbac.is_management(user) or rbac.has_permission(user, "daily_reports.approve")):
        raise HTTPException(status_code=403, detail="You may only read your own daily reports")
    if user_id or not (rbac.is_management(user) or rbac.has_permission(user, "daily_reports.approve")):
        q = q.filter(DailyReport.user_id == target)
    if slot:
        q = q.filter(DailyReport.slot == slot)
    if late_only:
        q = q.filter(DailyReport.is_late.is_(True))
    rows = q.order_by(DailyReport.report_date.desc(), DailyReport.slot).limit(limit).all()
    return [_report_out(r) for r in rows]


@router.post("/daily-reports", response_model=DailyReportOut, status_code=201)
def submit_daily_report(payload: DailyReportIn, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("daily_reports.add"))):
    from app.services import jobs_ops as ops_jobs
    now = ops_jobs.org_now()
    report_date = payload.report_date or now.date()
    if report_date > now.date():
        raise HTTPException(status_code=422, detail="Cannot file a report for a future day")
    if not payload.content:
        raise HTTPException(status_code=422, detail="A report needs at least one field")
    rec = (db.query(DailyReport).filter(DailyReport.user_id == user.id, DailyReport.report_date == report_date,
                                        DailyReport.slot == payload.slot).first())
    created = rec is None
    if rec is None:
        rec = DailyReport(user_id=user.id, report_date=report_date, slot=payload.slot)
        db.add(rec)
    rec.department_id = user.department_id
    rec.content = payload.content
    rec.summary = (payload.summary or next(iter(payload.content.values()), ""))[:500] or None
    rec.submitted_at = datetime.utcnow()
    rec.is_late = now > datetime.combine(report_date, ops_jobs.deadline_time(db, payload.slot))
    db.flush()
    log_action(db, user, "create" if created else "update", "daily_reports", entity=rec,
               description=f"{payload.slot} report submitted via API for {report_date}"
                           + (" (late)" if rec.is_late else ""), request=request)
    db.commit()
    return _report_out(rec)


# ================================================================== command center
@router.get("/command-center")
def command_center(period: str = "this_month", start: Optional[date] = None, end: Optional[date] = None,
                   db: Session = Depends(get_db), user: User = Depends(require("command_center.view"))) -> dict[str, Any]:
    """The executive summary behind /command-center as JSON."""
    from app.core import rbac
    from app.services import insights as insight_svc
    p = kpi_svc.resolve_period(period, start, end)
    m = kpi_svc.executive_metrics(db, p.start, p.end)
    health = kpi_svc.health_score(m)
    today = date.today()
    scorecards = []
    for dept in db.query(Department).filter(Department.is_active.is_(True)).order_by(Department.name):
        metrics = kpi_svc.department_metrics(db, dept, p.month, live=False)
        sc = (db.query(DepartmentScorecard)
              .filter(DepartmentScorecard.department_id == dept.id, DepartmentScorecard.period == p.month).first())
        scorecards.append({"department": dept.name, "code": dept.code, "score": metrics["score"],
                           "band": metrics["band"][0], "counts": metrics["counts"],
                           "submitted": bool(sc and sc.status == "submitted")})
    open_tasks = db.query(Task).filter(Task.status.in_(["todo", "in_progress", "review"]))
    decisions_open = db.query(Decision).filter(Decision.status.in_(["proposed", "decided"]))
    meeting = (db.query(TrajectoryMeeting).filter(TrajectoryMeeting.meeting_date >= today)
               .order_by(TrajectoryMeeting.meeting_date).first())
    transformation = dict(db.query(TransformationItem.state, func.count(TransformationItem.id))
                          .group_by(TransformationItem.state).all())
    reports_today = db.query(DailyReport).filter(DailyReport.report_date == today).count()
    return {
        "period": {"key": p.key, "label": p.label, "start": p.start, "end": p.end, "month": p.month},
        "health": {"score": health["score"], "band": health["band"], "subs": health["subs"],
                   "summary": health["summary"]},
        "metrics": {k: v for k, v in m.items() if not isinstance(v, dict)},
        "funnel": m.get("funnel", {}),
        "deltas": m.get("deltas", {}),
        "series": kpi_svc.monthly_series(db, 6),
        "scorecards": scorecards,
        "tasks": {"open": open_tasks.count(),
                  "overdue": open_tasks.filter(Task.due_date < today).count(),
                  "escalated": open_tasks.filter(Task.escalated.is_(True)).count()},
        "decisions": {"open": decisions_open.count(),
                      "overdue": decisions_open.filter(Decision.due_date < today).count()},
        "transformation": {"by_state": transformation, "total": sum(transformation.values())},
        "daily_reports": {"submitted_today": reports_today,
                          "late_today": db.query(DailyReport).filter(DailyReport.report_date == today,
                                                                     DailyReport.is_late.is_(True)).count()},
        "next_meeting": {"id": meeting.id, "date": meeting.meeting_date, "title": meeting.title} if meeting else None,
        "alerts": [{"id": a.id, "type": a.alert_type, "title": a.title, "severity": a.severity,
                    "status": a.status} for a in insight_svc.open_alerts(db, user, 10)],
        "anomalies": [a for a in insight_svc.compute_anomalies(db, p)
                      if a.get("visibility") != "ceo_only" or rbac.is_ceo(user)],
    }
