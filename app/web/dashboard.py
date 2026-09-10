"""Module 2 — Academic Home Dashboard: live class counters, today's timeline, pending work and risk alerts."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.database import get_db
from app.models.academic import MonthlyTest
from app.models.core import RiskAlert, User
from app.models.crm import Case
from app.models.finance import DiscountRequest
from app.models.people import Leave, Student, Teacher
from app.models.scheduling import ClassSession, QAReview, Schedule, Shift
from app.services import classes as class_svc
from app.services import scheduling as sched_svc

router = APIRouter(dependencies=[Depends(csrf_protect)])

COUNTER_CARDS = [
    ("pending", "Pending", "clock", "amber"),
    ("started", "Started", "play-circle", "indigo"),
    ("done", "Done", "check-circle-2", "emerald"),
    ("missed", "Missed", "user-x", "rose"),
    ("absent", "Absent", "user-minus", "orange"),
    ("leave", "On Leave", "palmtree", "violet"),
    ("cancelled", "Cancelled", "ban", "slate"),
    ("rescheduled", "Rescheduled", "calendar-clock", "sky"),
]


def _free_students(db: Session, teacher_ids: list[int] | None) -> int:
    """Enrolled students with no active recurring schedule (unassigned teaching slots)."""
    scheduled = db.query(Schedule.student_id).filter(Schedule.status == "active")
    q = db.query(func.count(Student.id)).filter(Student.status.in_(["active", "trial", "free"]), Student.id.notin_(scheduled))
    if teacher_ids is not None:
        q = q.filter(Student.teacher_id.in_(teacher_ids or [-1]))
    return q.scalar() or 0


def _trend(db: Session, teacher_ids: list[int] | None, days: int = 7) -> dict:
    today = date.today()
    start = today - timedelta(days=days - 1)
    q = (db.query(ClassSession.date, ClassSession.status, func.count(ClassSession.id))
         .filter(ClassSession.date >= start, ClassSession.date <= today))
    if teacher_ids is not None:
        q = q.filter(ClassSession.teacher_id.in_(teacher_ids or [-1]))
    agg: dict[date, dict[str, int]] = {}
    for d, status, n in q.group_by(ClassSession.date, ClassSession.status):
        agg.setdefault(d, {})[status] = n
    labels, done, missed, absent = [], [], [], []
    for i in range(days):
        d = start + timedelta(days=i)
        row = agg.get(d, {})
        labels.append(d.strftime("%a %d"))
        done.append(row.get("done", 0))
        missed.append(row.get("missed", 0))
        absent.append(row.get("absent", 0))
    return {"labels": labels, "done": done, "missed": missed, "absent": absent}


def _pending_panel(db: Session, user: User, teacher_ids: list[int] | None) -> list[dict]:
    student_leaves = db.query(func.count(Leave.id)).filter(Leave.person_type == "student", Leave.status == "pending").scalar() or 0
    staff_leaves = db.query(func.count(Leave.id)).filter(Leave.person_type == "employee", Leave.status == "pending").scalar() or 0
    discounts = db.query(func.count(DiscountRequest.id)).filter(DiscountRequest.status == "pending").scalar() or 0
    cases = dict(db.query(Case.priority, func.count(Case.id))
                 .filter(Case.status.in_(["open", "in_progress", "waiting", "escalated"])).group_by(Case.priority).all())
    unscored = db.query(func.count(MonthlyTest.id)).filter(MonthlyTest.status == "generated").scalar() or 0
    unverified = db.query(func.count(Teacher.id)).filter(Teacher.is_verified.is_(False), Teacher.status != "inactive").scalar() or 0
    qa_queue = db.query(func.count(QAReview.id)).filter(QAReview.status.in_(["queued", "in_review"])).scalar() or 0
    items = [
        {"label": "Student leave requests", "count": student_leaves, "url": "/leaves/students?status=pending", "icon": "calendar-off", "color": "violet"},
        {"label": "Staff leave requests", "count": staff_leaves, "url": "/hr/leaves?status=pending", "icon": "plane", "color": "sky"},
        {"label": "Discount approvals", "count": discounts, "url": "/finance/discounts?status=pending", "icon": "percent", "color": "amber"},
        {"label": "Open cases — urgent", "count": cases.get("urgent", 0), "url": "/cases?status=open&priority=urgent", "icon": "life-buoy", "color": "rose"},
        {"label": "Open cases — high", "count": cases.get("high", 0), "url": "/cases?status=open&priority=high", "icon": "life-buoy", "color": "orange"},
        {"label": "Open cases — medium/low", "count": cases.get("medium", 0) + cases.get("low", 0), "url": "/cases?status=open", "icon": "life-buoy", "color": "slate"},
        {"label": "Unscored monthly tests", "count": unscored, "url": "/academics/monthly-tests?status=generated", "icon": "file-badge", "color": "indigo"},
        {"label": "Unverified teachers", "count": unverified, "url": "/teachers?verified=0", "icon": "shield-alert", "color": "rose"},
        {"label": "QA reviews in queue", "count": qa_queue, "url": "/qa?status=queued", "icon": "shield-check", "color": "emerald"},
    ]
    return [i for i in items if i["count"] or i["label"].startswith("QA")]


def _alerts(db: Session, user: User, limit: int = 8) -> list[RiskAlert]:
    q = db.query(RiskAlert).filter(RiskAlert.status.in_(["open", "acknowledged"]))
    if not rbac.is_ceo(user):
        q = q.filter(RiskAlert.visibility != "ceo_only")
    if not rbac.is_management(user):
        q = q.filter(RiskAlert.visibility == "ops")
    return q.order_by(RiskAlert.created_at.desc()).limit(limit).all()


def _scope(db: Session, user: User) -> tuple[list[int] | None, str]:
    ids = sched_svc.scoped_teacher_ids(db, user)
    if ids is None:
        return None, "All teachers"
    if user.role_slug == "supervisor":
        return ids, f"My {len(ids)} supervised teacher(s)"
    return ids, "My classes"


@router.get("/dashboard", include_in_schema=False)
def dashboard(request: Request, day: str = "", shift_id: int | None = None, db: Session = Depends(get_db),
              user: User = Depends(require("dashboard.view"))):
    from app.core.utils import parse_date
    today = parse_date(day, date.today())
    teacher_ids, scope_label = _scope(db, user)
    counters = class_svc.counters_for_date(db, today, teacher_ids, shift_id)
    counters["free_students"] = _free_students(db, teacher_ids)
    cards = []
    for key, label, icon, color in COUNTER_CARDS:
        cards.append({"key": key, "label": label, "icon": icon, "color": color, "value": counters.get(key, 0),
                      "url": f"/classes?date={today.isoformat()}&status={key}" + (f"&shift_id={shift_id}" if shift_id else "")})
    cards.append({"key": "free", "label": "Free Students", "icon": "user-round-search", "color": "slate",
                  "value": counters["free_students"], "url": "/schedules?free=1"})
    ctx = {
        "user": user, "sel_date": today, "counters": counters, "cards": cards, "scope_label": scope_label,
        "shift_id": shift_id,
        "shift_options": [(s.id, s.name) for s in db.query(Shift).filter(Shift.is_active.is_(True)).order_by(Shift.start_time)],
        "timeline": sched_svc.timeline_for_day(db, today, teacher_ids),
        "pending": _pending_panel(db, user, teacher_ids),
        "trend": _trend(db, teacher_ids),
        "alerts": _alerts(db, user),
        "attendance_pct": _attendance_pct(db, teacher_ids),
        "now": sched_svc.org_now(),
    }
    return render(request, "dashboard/index.html", ctx)


def _attendance_pct(db: Session, teacher_ids: list[int] | None, days: int = 30) -> float:
    since = date.today() - timedelta(days=days)
    q = db.query(ClassSession.status, func.count(ClassSession.id)).filter(
        ClassSession.date >= since, ClassSession.date <= date.today(),
        ClassSession.status.in_(["done", "absent", "missed", "leave"]))
    if teacher_ids is not None:
        q = q.filter(ClassSession.teacher_id.in_(teacher_ids or [-1]))
    rows = dict(q.group_by(ClassSession.status).all())
    total = sum(rows.values())
    if not total:
        return 0.0
    return round(100 * (rows.get("done", 0)) / total, 1)


@router.get("/dashboard/partial/live", include_in_schema=False)
def dashboard_live(request: Request, day: str = "", shift_id: int | None = None, db: Session = Depends(get_db),
                   user: User = Depends(require("dashboard.view"))):
    from app.core.utils import parse_date
    today = parse_date(day, date.today())
    teacher_ids, _ = _scope(db, user)
    counters = class_svc.counters_for_date(db, today, teacher_ids, shift_id)
    return render(request, "dashboard/_live.html", {
        "user": user, "sel_date": today, "counters": counters, "now": sched_svc.org_now(),
        "timeline": sched_svc.timeline_for_day(db, today, teacher_ids),
        "refreshed_at": datetime.utcnow()})
