"""Retention & churn services (Module 33 + 29.11).

Risk scoring per student (AI ``churn`` module), automatic win-back / cohort-call actions,
freeze reactivation outreach and cohort analytics. Reusable by the web router, API, jobs and seed.
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.notify import notify
from app.models.academic import MonthlyTest
from app.models.core import User, RiskAlert
from app.models.crm import Case, RetentionAction, SequenceEnrollment, Sequence

OPEN_CASE_STATUSES = ("open", "in_progress", "waiting", "escalated")
from app.models.finance import Invoice, Subscription
from app.models.people import Client, Student
from app.models.scheduling import ClassSession
from app.services.ai_gateway import ai

ACTION_TYPES = ["win_back_sequence", "pre_leave_offer", "freeze_outreach", "cohort_call", "teacher_change", "discount_offer", "call"]
ACTION_STATUSES = ["scheduled", "in_progress", "completed", "succeeded", "failed"]
RISK_LEVELS = ["low", "medium", "high"]


# ----------------------------------------------------------------------------- signals
def _attendance_pct(db: Session, student_id: int, days: int = 30) -> float:
    from app.services.classes import student_attendance_pct
    return student_attendance_pct(db, student_id, days)


def _missed_last_14d(db: Session, student_id: int) -> int:
    since = date.today() - timedelta(days=14)
    return db.query(func.count(ClassSession.id)).filter(ClassSession.student_id == student_id, ClassSession.date >= since,
                                                        ClassSession.status.in_(["absent", "missed"])).scalar() or 0


def _test_trend(db: Session, student_id: int) -> float:
    rows = (db.query(MonthlyTest.improvement_pct).filter(MonthlyTest.student_id == student_id, MonthlyTest.improvement_pct.isnot(None))
            .order_by(MonthlyTest.period.desc()).limit(2).all())
    vals = [float(r[0]) for r in rows if r[0] is not None]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def _open_complaints(db: Session, student: Student) -> int:
    q = db.query(func.count(Case.id)).filter(Case.status.in_(OPEN_CASE_STATUSES))
    if student.client_id:
        q = q.filter((Case.student_id == student.id) | (Case.client_id == student.client_id))
    else:
        q = q.filter(Case.student_id == student.id)
    return q.scalar() or 0


def _overdue_invoices(db: Session, student: Student) -> int:
    if not student.client_id:
        return 0
    return db.query(func.count(Invoice.id)).filter(Invoice.client_id == student.client_id, Invoice.status == "overdue").scalar() or 0


def _is_frozen(db: Session, student: Student) -> bool:
    if student.status == "frozen":
        return True
    return bool(db.query(func.count(Subscription.id)).filter(Subscription.student_id == student.id, Subscription.status == "frozen").scalar())


def risk_signals(db: Session, student: Student) -> dict:
    return {"student_id": student.id, "attendance_pct_30d": _attendance_pct(db, student.id), "missed_last_14d": _missed_last_14d(db, student.id),
            "test_trend": _test_trend(db, student.id), "open_complaints": _open_complaints(db, student), "overdue_invoices": _overdue_invoices(db, student),
            "is_frozen": _is_frozen(db, student), "tenure_days": (date.today() - (student.join_date or date.today())).days,
            "status": student.status}


# ----------------------------------------------------------------------------- scoring
def compute_risk(db: Session, student: Student, actor: Optional[User] = None, act: bool = True) -> dict:
    """Compute and store a student's churn risk. Triggers win-back / cohort-call actions on new high or medium risk."""
    payload = risk_signals(db, student)
    result, run = ai(db, "churn", "score_student", payload, entity=student)
    before_level = student.risk_level
    score = float(result.get("score") or 0)
    level = result.get("level") or ("high" if score >= 65 else ("medium" if score >= 40 else "low"))
    factors = dict(result.get("factors") or {})
    factors["_signals"] = payload
    factors["_recommended_action"] = result.get("recommended_action")
    factors["_confidence"] = result.get("confidence")
    student.risk_score = score
    student.risk_level = level
    student.risk_factors = factors
    student.risk_computed_at = datetime.utcnow()
    created_action = None
    if act and student.status in ("active", "trial", "frozen"):
        created_action = _act_on_risk(db, student, level, before_level, score, actor)
    return {"score": score, "level": level, "factors": factors, "signals": payload, "action": created_action,
            "recommended_action": result.get("recommended_action"), "run_id": run.id}


def _has_open_action(db: Session, student_id: int, action_type: str) -> bool:
    return bool(db.query(func.count(RetentionAction.id)).filter(RetentionAction.student_id == student_id, RetentionAction.action_type == action_type,
                                                                RetentionAction.status.in_(["scheduled", "in_progress"])).scalar())


def _active_enrollment(db: Session, sequence_type: str, student_id: int) -> Optional[SequenceEnrollment]:
    return (db.query(SequenceEnrollment).join(Sequence).filter(Sequence.sequence_type == sequence_type, SequenceEnrollment.contact_type == "student",
                                                               SequenceEnrollment.contact_id == student_id, SequenceEnrollment.status == "active").first())


def _act_on_risk(db: Session, student: Student, level: str, before_level: Optional[str], score: float, actor: Optional[User]) -> Optional[RetentionAction]:
    from app.services.crm import enroll_sequence, users_with_role
    if level == "high":
        if _active_enrollment(db, "win_back", student.id) or _has_open_action(db, student.id, "win_back_sequence"):
            return None
        enrollment = enroll_sequence(db, "win_back", "student", student.id, enrolled_by="system")
        owners = users_with_role(db, "supervisor") or users_with_role(db, "manager")
        action = RetentionAction(student_id=student.id, client_id=student.client_id, action_type="win_back_sequence", trigger="risk_score",
                                 risk_score_at_trigger=score, status="scheduled", scheduled_at=datetime.utcnow(),
                                 owner_id=owners[0].id if owners else None, sequence_enrollment_id=enrollment.id if enrollment else None,
                                 notes=f"Auto-created: risk moved {before_level or 'unknown'} -> high (score {score}).")
        db.add(action)
        db.flush()
        db.add(RiskAlert(alert_type="student_churn_risk", severity="high", title=f"High churn risk: {student.full_name}",
                         message=f"Risk score {score}. Win-back sequence enrolled; supervisor call required this week.",
                         entity_type="Student", entity_id=student.id, visibility="management", source="ai"))
        for u in (users_with_role(db, "supervisor") + users_with_role(db, "manager"))[:4]:
            notify(db, u, f"High churn risk: {student.full_name}", f"Risk score {score}. A win-back sequence has been enrolled - please call the family.",
                   event_type="retention_risk", link=f"/retention?student={student.id}")
        log_action(db, actor, "create", "retention", entity=action, description=f"Win-back action created for {student.student_code} (risk {score})",
                   after={"level": level, "score": score})
        return action
    if level == "medium" and before_level != "medium":
        if _has_open_action(db, student.id, "cohort_call"):
            return None
        owners = users_with_role(db, "supervisor")
        action = RetentionAction(student_id=student.id, client_id=student.client_id, action_type="cohort_call", trigger="risk_score",
                                 risk_score_at_trigger=score, status="scheduled", scheduled_at=datetime.utcnow() + timedelta(days=3),
                                 owner_id=owners[0].id if owners else None, notes=f"Monthly cohort call - medium risk (score {score}).")
        db.add(action)
        db.flush()
        return action
    return None


def recompute_all(db: Session, actor: Optional[User] = None, statuses: tuple = ("active", "trial", "frozen"), limit: Optional[int] = None) -> dict:
    q = db.query(Student).filter(Student.status.in_(list(statuses))).order_by(Student.id)
    if limit:
        q = q.limit(limit)
    counts = {"low": 0, "medium": 0, "high": 0, "total": 0, "actions": 0}
    for s in q.all():
        res = compute_risk(db, s, actor)
        counts[res["level"]] = counts.get(res["level"], 0) + 1
        counts["total"] += 1
        if res.get("action") is not None:
            counts["actions"] += 1
    return counts


# ----------------------------------------------------------------------------- freeze handling (29.11)
def schedule_freeze_outreach(db: Session, actor: Optional[User] = None) -> int:
    """Frozen subscriptions get a reactivation outreach action 7 days before the freeze ends."""
    from app.services.crm import users_with_role
    created = 0
    horizon = date.today() + timedelta(days=7)
    subs = db.query(Subscription).filter(Subscription.status == "frozen", Subscription.freeze_end.isnot(None), Subscription.freeze_end <= horizon).all()
    owners = users_with_role(db, "supervisor")
    for sub in subs:
        if _has_open_action(db, sub.student_id, "freeze_outreach"):
            continue
        when = datetime.combine(sub.freeze_end - timedelta(days=7), datetime.min.time().replace(hour=10))
        db.add(RetentionAction(student_id=sub.student_id, client_id=sub.client_id, action_type="freeze_outreach", trigger="freeze",
                               status="scheduled", scheduled_at=when, owner_id=owners[0].id if owners else None,
                               notes=f"Freeze ends {sub.freeze_end.isoformat()} - confirm reactivation and re-book the slot."))
        created += 1
    if created:
        log_action(db, actor, "create", "retention", description=f"{created} freeze reactivation outreach actions scheduled", entity_type="RetentionAction")
    return created


def frozen_students(db: Session) -> list[dict]:
    rows = []
    subs = db.query(Subscription).filter(Subscription.status == "frozen").all()
    frozen_ids = {s.student_id for s in subs}
    for st in db.query(Student).filter(Student.status == "frozen").all():
        frozen_ids.add(st.id)
    for sid in sorted(frozen_ids):
        st = db.get(Student, sid)
        if not st:
            continue
        sub = next((s for s in subs if s.student_id == sid), None)
        action = (db.query(RetentionAction).filter(RetentionAction.student_id == sid, RetentionAction.action_type == "freeze_outreach")
                  .order_by(RetentionAction.id.desc()).first())
        freeze_end = sub.freeze_end if (sub and sub.freeze_end) else (
            (action.scheduled_at.date() + timedelta(days=7)) if (action and action.scheduled_at) else None)
        rows.append({"student": st, "subscription": sub, "freeze_end": freeze_end, "action": action,
                     "days_left": (freeze_end - date.today()).days if freeze_end else None})
    return rows


def bulk_cohort_calls(db: Session, owner: Optional[User], actor: Optional[User], level: str = "medium", when: Optional[datetime] = None) -> int:
    """COO monthly cohort-call bulk scheduling for every student at a given risk level."""
    when = when or (datetime.utcnow() + timedelta(days=2))
    created = 0
    for st in db.query(Student).filter(Student.risk_level == level, Student.status.in_(["active", "trial"])).all():
        if _has_open_action(db, st.id, "cohort_call"):
            continue
        db.add(RetentionAction(student_id=st.id, client_id=st.client_id, action_type="cohort_call", trigger="risk_score",
                               risk_score_at_trigger=st.risk_score, status="scheduled", scheduled_at=when,
                               owner_id=owner.id if owner else None, notes=f"Monthly {level}-risk cohort call."))
        created += 1
    log_action(db, actor, "create", "retention", description=f"{created} cohort calls scheduled for {level}-risk students",
               entity_type="RetentionAction", after={"level": level, "count": created})
    return created


# ----------------------------------------------------------------------------- analytics
def risk_board(db: Session, level: str = "", teacher_id: Optional[int] = None, q: str = "") -> list[dict]:
    query = db.query(Student).filter(Student.status.in_(["active", "trial", "frozen"]))
    if level:
        query = query.filter(Student.risk_level == level)
    if teacher_id:
        query = query.filter(Student.teacher_id == teacher_id)
    if q:
        query = query.filter(Student.full_name.ilike(f"%{q}%"))
    rows = []
    for st in query.order_by(Student.risk_score.desc()).limit(300).all():
        f = st.risk_factors or {}
        sig = f.get("_signals") or {}
        rows.append({"student": st, "score": st.risk_score or 0, "level": st.risk_level or "low",
                     "factors": [k for k in f.keys() if not k.startswith("_")],
                     "attendance": sig.get("attendance_pct_30d"), "test_trend": sig.get("test_trend"),
                     "complaints": sig.get("open_complaints"), "overdue": sig.get("overdue_invoices"),
                     "frozen": sig.get("is_frozen"), "computed_at": st.risk_computed_at})
    return rows


def retention_stats(db: Session) -> dict:
    counts = dict(db.query(Student.risk_level, func.count(Student.id)).filter(Student.status.in_(["active", "trial", "frozen"])).group_by(Student.risk_level).all())
    actions = dict(db.query(RetentionAction.status, func.count(RetentionAction.id)).group_by(RetentionAction.status).all())
    saved = actions.get("succeeded", 0)
    finished = saved + actions.get("failed", 0)
    active_total = db.query(func.count(Student.id)).filter(Student.status.in_(["active", "trial"])).scalar() or 0
    cancelled_30 = db.query(func.count(Student.id)).filter(Student.status == "cancelled",
                                                           Student.cancelled_at >= date.today() - timedelta(days=30)).scalar() or 0
    return {"high": counts.get("high", 0), "medium": counts.get("medium", 0), "low": counts.get("low", 0),
            "actions": actions, "open_actions": actions.get("scheduled", 0) + actions.get("in_progress", 0),
            "save_rate": round(100 * saved / finished, 1) if finished else 0.0, "saved": saved,
            "active_students": active_total, "cancelled_30d": cancelled_30,
            "churn_rate_30d": round(100 * cancelled_30 / (active_total + cancelled_30), 1) if (active_total + cancelled_30) else 0.0,
            "frozen": db.query(func.count(Student.id)).filter(Student.status == "frozen").scalar() or 0}


def cohort_analysis(db: Session, months: int = 8) -> list[dict]:
    """Retention by join-month cohort at 30 / 60 / 90 days."""
    students = db.query(Student).all()
    today = date.today()
    cohorts: dict[str, list[Student]] = {}
    start_month = date(today.year, today.month, 1)
    labels = []
    for i in range(months - 1, -1, -1):
        y, m = start_month.year, start_month.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        labels.append(f"{y:04d}-{m:02d}")
    for s in students:
        if not s.join_date:
            continue
        k = s.join_date.strftime("%Y-%m")
        if k in labels:
            cohorts.setdefault(k, []).append(s)
    out = []
    for k in labels:
        group = cohorts.get(k, [])
        row = {"cohort": k, "size": len(group)}
        for d in (30, 60, 90):
            eligible = [s for s in group if (today - s.join_date).days >= d]
            retained = [s for s in eligible if s.status != "cancelled" or (s.cancelled_at and (s.cancelled_at - s.join_date).days >= d)]
            row[f"d{d}"] = round(100 * len(retained) / len(eligible), 1) if eligible else None
            row[f"d{d}_n"] = len(eligible)
        out.append(row)
    return out


def churn_reasons(db: Session, days: int = 365) -> list[tuple[str, int]]:
    since = date.today() - timedelta(days=days)
    rows = db.query(Student.cancel_reason, func.count(Student.id)).filter(Student.status == "cancelled", Student.cancelled_at >= since,
                                                                          Student.cancel_reason.isnot(None)).group_by(Student.cancel_reason).all()
    return sorted([(r[0], r[1]) for r in rows], key=lambda x: -x[1])


def monthly_churn(db: Session, months: int = 6) -> dict:
    today = date.today().replace(day=1)
    labels, joined, left = [], [], []
    for i in range(months - 1, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        ms = date(y, m, 1)
        me = date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
        labels.append(ms.strftime("%b %Y"))
        joined.append(db.query(func.count(Student.id)).filter(Student.join_date >= ms, Student.join_date <= me).scalar() or 0)
        left.append(db.query(func.count(Student.id)).filter(Student.cancelled_at >= ms, Student.cancelled_at <= me).scalar() or 0)
    return {"labels": labels, "joined": joined, "left": left}


def retention_dashboard(db: Session) -> dict:
    return {"stats": retention_stats(db), "cohorts": cohort_analysis(db), "reasons": churn_reasons(db), "monthly": monthly_churn(db)}
