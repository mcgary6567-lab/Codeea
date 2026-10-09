"""The attention layer (docs/ATTENTION.md): Detect → Analyse → Alert → Assign, with the decision left to staff.

A daily check reads across attendance, lateness, progress and pace, teacher changes, complaints, assessments,
follow-ups, parent conversations, retention risk and recommendations, and raises one ``AttentionItem`` per student,
family or teacher that needs a person:

* every finding is a rule with its evidence, so staff can see exactly why an item exists;
* the item carries a plain-language summary (written by the AI gateway when a provider is configured, otherwise put
  together from the same facts) and recommended actions with an owner and a due date;
* nothing happens until a person approves the actions (tasks are created), dismisses the item with a reason, or
  snoozes it. Items refresh on every check and resolve themselves when the signals clear.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.notify import notify
from app.models.academic import Evaluation, LessonPlan, MonthlyTest, StudentProgress, StudentRecommendation, TeacherAssignment
from app.models.core import Role, User
from app.models.crm import Case, Feedback, ParentContact
from app.models.erp import ClassActivity
from app.models.ops import AttentionItem, Task
from app.models.people import Client, Student, Teacher
from app.models.scheduling import Attendance, ClassSession, QAReview

LEVEL_HIGH, LEVEL_MEDIUM = 60, 30
OPEN_TASK = ("todo", "in_progress", "review")
LIVE = ("open", "actioned", "snoozed")
DISMISS_MEMORY_DAYS = 30

# signal -> the actions it suggests (owner: academy_manager | teacher | supervisor | head_of_admissions | qa)
ACTIONS = {
    "attendance": ("call_attendance", "Call the family about attendance", "academy_manager", 2, "high"),
    "absences": ("call_attendance", "Call the family about attendance", "academy_manager", 2, "high"),
    "lateness": ("call_lateness", "Talk to the family about arriving on time", "academy_manager", 3, "medium"),
    "progress": ("revision_plan", "Ask the teacher for a catch-up plan", "teacher", 5, "medium"),
    "pace": ("revision_plan", "Ask the teacher for a catch-up plan", "teacher", 5, "medium"),
    "assessment": ("reassess", "Re-assess the weak areas within two weeks", "teacher", 14, "medium"),
    "teacher_changes": ("teacher_fit", "Review whether the current teacher is the right fit", "academy_manager", 5, "medium"),
    "complaints": ("manager_call", "Manager call with the family", "academy_manager", 1, "high"),
    "parent_upset": ("manager_call", "Manager call with the family", "academy_manager", 1, "high"),
    "follow_ups": ("chase_follow_ups", "Chase the overdue follow-ups", "academy_manager", 1, "high"),
    "retention": ("retention_call", "Retention call with the family", "academy_manager", 2, "high"),
    "recommendation": ("act_on_recommendation", "Act on the teacher's open recommendation", "academy_manager", 2, "medium"),
    "referral": ("ambassador_invite", "Invite the family to the ambassador programme", "head_of_admissions", 7, "low"),
    "teacher_missed": ("supervisor_review", "Supervisor review with the teacher", "supervisor", 3, "high"),
    "teacher_late": ("supervisor_review", "Supervisor review with the teacher", "supervisor", 3, "high"),
    "teacher_complaints": ("qa_review", "QA review of the teacher's recent classes", "qa", 5, "high"),
    "teacher_quality": ("qa_review", "QA review of the teacher's recent classes", "qa", 5, "high"),
    "teacher_students": ("supervisor_review", "Supervisor review with the teacher", "supervisor", 3, "medium"),
}


def today() -> date:
    from app.services.people import org_now
    return org_now().date()


def _sig(key: str, label: str, detail: str, weight: int) -> dict:
    return {"key": key, "label": label, "detail": detail, "weight": weight}


def _windows(now: date) -> list[tuple[date, date]]:
    """Three rolling 30-day windows, oldest first: 61-90, 31-60 and the last 30 days."""
    return [(now - timedelta(days=89), now - timedelta(days=60)), (now - timedelta(days=59), now - timedelta(days=30)),
            (now - timedelta(days=29), now)]


# ============================================================================ students
def student_signals(db: Session, s: Student, now: Optional[date] = None) -> list[dict]:
    """The rule-based findings for one student, each with its evidence."""
    from app.services import journey
    now = now or today()
    wins = _windows(now)
    sessions = (db.query(ClassSession).filter(ClassSession.student_id == s.id, ClassSession.date >= wins[0][0], ClassSession.date <= now)
                .all())
    att = {a.session_id: a for a in db.query(Attendance).filter(Attendance.session_id.in_([x.id for x in sessions] or [-1]))}
    per = []
    for start, end in wins:
        held = [x for x in sessions if start <= x.date <= end and x.status in ("done", "absent")]
        absent = [x for x in held if x.status == "absent" or (att.get(x.id) and att[x.id].student_status == "absent")]
        attended = [x for x in held if x not in absent]
        late = [x for x in attended if journey._student_late(x, att.get(x.id))]
        per.append({"held": len(held), "absent": len(absent), "late": len(late),
                    "pct": round(100.0 * len(attended) / len(held), 1) if held else None})
    out: list[dict] = []
    p0, p1, p2 = (w["pct"] for w in per)
    if None not in (p0, p1, p2) and p2 < p1 < p0 and p2 < 85:
        out.append(_sig("attendance", "Attendance has declined for two consecutive months", f"{p0:g}% → {p1:g}% → {p2:g}%", 30))
    elif None not in (p1, p2) and p2 <= p1 - 15 and p2 < 80:
        out.append(_sig("attendance", "Attendance dropped sharply this month", f"{p1:g}% → {p2:g}%", 25))
    if per[2]["absent"] >= 3:
        out.append(_sig("absences", "Frequent absences", f"{per[2]['absent']} absences in the last 30 days", 20))
    if per[2]["late"] >= 3 and per[2]["late"] > per[1]["late"]:
        out.append(_sig("lateness", "Late arrivals are increasing", f"{per[1]['late']} → {per[2]['late']} late arrivals", 15))

    # progress: completed lessons plus classes whose content was recorded, last 30 days against the 30 before
    def progress_in(start: date, end: date) -> int:
        lo, hi = datetime.combine(start, time.min), datetime.combine(end, time.max)
        done = db.query(func.count(StudentProgress.id)).filter(StudentProgress.student_id == s.id, StudentProgress.status == "completed",
                                                               StudentProgress.completed_at >= lo, StudentProgress.completed_at <= hi).scalar() or 0
        acts = (db.query(func.count(func.distinct(ClassActivity.session_id))).join(ClassSession, ClassSession.id == ClassActivity.session_id)
                .filter(ClassSession.student_id == s.id, ClassSession.date >= start, ClassSession.date <= end).scalar() or 0)
        plans = db.query(func.count(LessonPlan.id)).filter(LessonPlan.student_id == s.id, LessonPlan.plan_date >= start,
                                                           LessonPlan.plan_date <= end, LessonPlan.status != "planned").scalar() or 0
        return done + max(acts, plans)
    before, recent = progress_in(*wins[1]), progress_in(*wins[2])
    if before >= 4 and recent * 2 <= before and per[2]["held"] >= 4:
        out.append(_sig("progress", "Progress is slowing down", f"{before} lessons/classes with progress before, {recent} in the last 30 days", 20))
    if s.course and s.course.completion_target_months and s.join_date:
        from app.services import academic as acad
        pct = acad.student_progress_summary(db, s)["pct"]
        expected = min(100.0, 100.0 * max(0, (now - s.join_date).days / 30.4) / s.course.completion_target_months)
        if pct + 15 < expected:
            out.append(_sig("pace", "Syllabus is behind the expected pace", f"{pct:g}% completed, {expected:.0f}% expected by now", 15))
    changes = (db.query(func.count(TeacherAssignment.id)).filter(TeacherAssignment.student_id == s.id, TeacherAssignment.previous_teacher_id.isnot(None),
                                                                 TeacherAssignment.start_date >= now - timedelta(days=90)).scalar() or 0)
    if changes >= 2:
        out.append(_sig("teacher_changes", "Frequent teacher changes", f"{changes} teacher changes in 90 days", 15))
    family_cases = db.query(Case).filter(Case.case_type == "complaint", or_(Case.student_id == s.id, Case.client_id == s.client_id),
                                         Case.created_at >= datetime.combine(now - timedelta(days=90), time.min)).all()
    reopened = [c for c in family_cases if (c.reopen_count or 0) > 0 and c.status != "closed"]
    if len(family_cases) >= 2 or reopened:
        out.append(_sig("complaints", "Repeated or reopened complaints",
                        f"{len(family_cases)} complaints in 90 days" + (f", {len(reopened)} reopened and still open" if reopened else ""), 20))
    last_eval = (db.query(Evaluation).filter(Evaluation.student_id == s.id).order_by(Evaluation.date.desc(), Evaluation.id.desc()).first())
    tests = (db.query(MonthlyTest).filter(MonthlyTest.student_id == s.id, MonthlyTest.percentage.isnot(None))
             .order_by(MonthlyTest.period.desc()).limit(2).all())
    weak = []
    if last_eval and last_eval.date >= now - timedelta(days=60):
        wrong = sum(1 for a in last_eval.answers if a.result in ("incorrect", "not_attempted"))
        if last_eval.result == "fail":
            weak.append(f"last assessment failed ({last_eval.score:g}/{last_eval.max_score:g})" if last_eval.score is not None else "last assessment failed")
        elif last_eval.answers and wrong / len(last_eval.answers) >= 0.4:
            weak.append(f"{wrong} of {len(last_eval.answers)} questions wrong in the last assessment")
    if len(tests) == 2 and tests[0].percentage + 10 <= tests[1].percentage:
        weak.append(f"monthly test fell {tests[1].percentage:g}% → {tests[0].percentage:g}%")
    if weak:
        out.append(_sig("assessment", "Assessment weaknesses", "; ".join(weak), 15))
    overdue = (db.query(Task).filter(or_((Task.entity_type == "Student") & (Task.entity_id == s.id), (Task.entity_type == "Client") & (Task.entity_id == s.client_id)),
                                     Task.status.in_(OPEN_TASK), Task.due_date < now).count())
    if overdue:
        out.append(_sig("follow_ups", "Follow-ups are overdue", f"{overdue} overdue follow-up(s) for this student or family", 15))
    last_pc = (db.query(ParentContact).filter(or_(ParentContact.student_id == s.id, ParentContact.client_id == s.client_id),
                                              ParentContact.contacted_at >= datetime.combine(now - timedelta(days=30), time.min))
               .order_by(ParentContact.contacted_at.desc()).first())
    if last_pc and last_pc.sentiment in ("upset", "concerned"):
        fu = last_pc.follow_up_task
        if fu is None or fu.status in OPEN_TASK:
            out.append(_sig("parent_upset", "The family was " + last_pc.sentiment + " on the last call",
                            f"{last_pc.contacted_at:%d %b}: {(last_pc.issue or last_pc.parent_response or last_pc.summary or '')[:90]}", 20))
    if s.risk_level == "high":
        out.append(_sig("retention", "High retention risk", f"risk score {s.risk_score:.0f}", 20))
    stale_rec = (db.query(StudentRecommendation).join(Task, Task.id == StudentRecommendation.task_id)
                 .filter(StudentRecommendation.student_id == s.id, Task.status.in_(OPEN_TASK),
                         StudentRecommendation.created_at <= datetime.combine(now - timedelta(days=7), time.min)).count())
    if stale_rec:
        out.append(_sig("recommendation", "A teacher's recommendation has been open for over a week", f"{stale_rec} open", 10))
    return out


# ============================================================================ families (opportunities)
def family_signals(db: Session, c: Client, now: Optional[date] = None) -> list[dict]:
    """Referral opportunity: a settled, happy family that is not yet an ambassador."""
    now = now or today()
    if c.is_ambassador or not c.joined_at or (now - c.joined_at).days < 60:
        return []
    if not any(s.status in ("active", "trial") for s in c.students):
        return []
    if db.query(Case.id).filter(Case.client_id == c.id, Case.case_type == "complaint", Case.status != "closed").first():
        return []
    reasons = []
    promoter = (db.query(Feedback).filter(Feedback.client_id == c.id, Feedback.nps >= 9,
                                          Feedback.submitted_at >= datetime.combine(now - timedelta(days=180), time.min)).first())
    if promoter:
        reasons.append(f"scored us {promoter.nps}/10")
    praised = (db.query(StudentRecommendation).join(Student, Student.id == StudentRecommendation.student_id)
               .filter(Student.client_id == c.id, StudentRecommendation.kind == "progressing_well",
                       StudentRecommendation.created_at >= datetime.combine(now - timedelta(days=90), time.min)).first())
    if praised:
        reasons.append("a teacher reported the child is progressing well")
    happy = (db.query(ParentContact).filter(ParentContact.client_id == c.id, ParentContact.sentiment == "positive",
                                            ParentContact.contacted_at >= datetime.combine(now - timedelta(days=60), time.min)).first())
    if happy:
        reasons.append("positive on a recent call")
    if not reasons:
        return []
    return [_sig("referral", "Referral opportunity", f"With us {(now - c.joined_at).days // 30} months; " + ", ".join(reasons), 35)]


# ============================================================================ teachers (recurring operational problems)
def teacher_signals(db: Session, t: Teacher, now: Optional[date] = None) -> list[dict]:
    now = now or today()
    since = now - timedelta(days=30)
    out = []
    missed = db.query(func.count(ClassSession.id)).filter(ClassSession.teacher_id == t.id, ClassSession.status == "missed",
                                                          ClassSession.date >= since, ClassSession.date <= now).scalar() or 0
    if missed >= 3:
        out.append(_sig("teacher_missed", "Missed classes", f"{missed} classes missed in 30 days", 30))
    late = db.query(func.count(ClassSession.id)).filter(ClassSession.teacher_id == t.id, ClassSession.status == "done",
                                                        ClassSession.teacher_late_minutes > 5, ClassSession.date >= since).scalar() or 0
    if late >= 5:
        out.append(_sig("teacher_late", "Frequent late starts", f"{late} classes started late in 30 days", 20))
    complaints = (db.query(func.count(Case.id)).filter(Case.case_type == "complaint", or_(Case.teacher_id == t.id, Case.against_employee_id == t.employee_id),
                                                       Case.created_at >= datetime.combine(now - timedelta(days=90), time.min)).scalar() or 0)
    if complaints >= 2:
        out.append(_sig("teacher_complaints", "Recurring complaints", f"{complaints} complaints in 90 days", 25))
    ratings = [r for (r,) in db.query(QAReview.overall_rating).filter(QAReview.teacher_id == t.id, QAReview.overall_rating.isnot(None),
                                                                      QAReview.created_at >= datetime.combine(now - timedelta(days=60), time.min))]
    if len(ratings) >= 2 and sum(ratings) / len(ratings) < 3:
        out.append(_sig("teacher_quality", "Low QA ratings", f"average {sum(ratings) / len(ratings):.1f}/5 over {len(ratings)} reviews", 20))
    flagged = (db.query(func.count(AttentionItem.id)).join(Student, (AttentionItem.subject_type == "student") & (AttentionItem.subject_id == Student.id))
               .filter(Student.teacher_id == t.id, AttentionItem.status.in_(LIVE), AttentionItem.kind == "concern").scalar() or 0)
    if flagged >= 3:
        out.append(_sig("teacher_students", "Several of the teacher's students need attention", f"{flagged} students flagged", 15))
    return out


# ============================================================================ items
def _level(score: float) -> str:
    return "high" if score >= LEVEL_HIGH else ("medium" if score >= LEVEL_MEDIUM else "low")


def recommended_for(signals: list[dict]) -> list[dict]:
    out, seen = [], set()
    for sg in signals:
        spec = ACTIONS.get(sg["key"])
        if spec and spec[0] not in seen:
            seen.add(spec[0])
            out.append({"key": spec[0], "label": spec[1], "owner": spec[2], "due_days": spec[3], "priority": spec[4]})
    return out


def qualifies(kind: str, signals: list[dict]) -> bool:
    if kind == "opportunity":
        return bool(signals)
    score = sum(s["weight"] for s in signals)
    return score >= LEVEL_MEDIUM or len(signals) >= 2


def summarise(db: Session, subject_type: str, name: str, signals: list[dict]) -> tuple[str, str, Optional[int]]:
    """One plain sentence about the item. The AI gateway writes it when a provider is configured; otherwise it is put
    together from the findings. Either way it only states what the findings say."""
    from app.services.ai_gateway import ai
    result, run = ai(db, "attention_summary", "summarise_attention",
                     {"subject": subject_type, "name": name, "signals": [{"label": s["label"], "detail": s["detail"]} for s in signals]})
    text = (result.get("summary") or "").strip()
    return text, ("rules" if run.provider == "simulated" else "ai"), run.id


def _upsert(db: Session, subject_type: str, subject_id: int, name: str, kind: str, signals: list[dict], now: datetime) -> Optional[AttentionItem]:
    live = (db.query(AttentionItem).filter(AttentionItem.subject_type == subject_type, AttentionItem.subject_id == subject_id,
                                           AttentionItem.kind == kind, AttentionItem.status.in_(LIVE))
            .order_by(AttentionItem.id.desc()).first())
    if not qualifies(kind, signals):
        if live is not None:
            live.status, live.resolved_at = "resolved", now
            live.decision_note = (live.decision_note or "") + " Resolved: the signals cleared."
        return None
    keys = sorted(s["key"] for s in signals)
    if live is None:
        dismissed = (db.query(AttentionItem).filter(AttentionItem.subject_type == subject_type, AttentionItem.subject_id == subject_id,
                                                    AttentionItem.kind == kind, AttentionItem.status == "dismissed",
                                                    AttentionItem.decided_at >= now - timedelta(days=DISMISS_MEMORY_DAYS))
                     .order_by(AttentionItem.id.desc()).first())
        if dismissed is not None and set(keys) <= {s["key"] for s in dismissed.signals}:
            return None  # a person already looked at exactly this and decided; only something new re-raises it
        live = AttentionItem(subject_type=subject_type, subject_id=subject_id, kind=kind, first_detected_at=now, status="open")
        db.add(live)
    elif live.status == "actioned" and set(keys) - {s["key"] for s in live.signals}:
        live.status = "open"  # something new appeared after the actions were approved
    elif live.status == "snoozed" and live.snoozed_until and live.snoozed_until <= now.date():
        live.status, live.snoozed_until = "open", None
    score = float(sum(s["weight"] for s in signals))
    # rewrite the summary whenever a finding or its evidence changed, so the sentence never contradicts the findings
    changed = sorted((x["key"], x["detail"]) for x in (live.signals or [])) != sorted((x["key"], x["detail"]) for x in signals) or not live.summary
    live.signals, live.score, live.level, live.last_detected_at = signals, score, _level(score), now
    live.recommended = recommended_for(signals)
    if changed:
        live.summary, live.summary_source, live.ai_run_id = summarise(db, subject_type, name, signals)
    db.flush()
    return live


def run_detection(db: Session, now: Optional[datetime] = None) -> dict:
    """The daily check over every active student, family and teacher."""
    now = now or datetime.utcnow()
    day = today()
    counts = {"students": 0, "families": 0, "teachers": 0, "open": 0}
    for s in db.query(Student).filter(Student.status.in_(["active", "trial", "frozen"])).all():
        counts["students"] += 1
        _upsert(db, "student", s.id, s.full_name, "concern", student_signals(db, s, day), now)
    for c in db.query(Client).filter(Client.status.in_(["active", "regular"])).all():
        counts["families"] += 1
        _upsert(db, "family", c.id, c.full_name, "opportunity", family_signals(db, c, day), now)
    for t in db.query(Teacher).filter(Teacher.status == "active").all():
        counts["teachers"] += 1
        _upsert(db, "teacher", t.id, t.full_name, "concern", teacher_signals(db, t, day), now)
    counts["open"] = db.query(AttentionItem).filter(AttentionItem.status == "open").count()
    counts["high"] = db.query(AttentionItem).filter(AttentionItem.status == "open", AttentionItem.level == "high").count()
    return counts


# ============================================================================ decisions
def subject_of(db: Session, item: AttentionItem):
    model = {"student": Student, "family": Client, "teacher": Teacher}[item.subject_type]
    return db.get(model, item.subject_id)


def _owner(db: Session, item: AttentionItem, role: str) -> Optional[User]:
    from app.services import contacts, journey
    subject = subject_of(db, item)
    if role == "teacher" and isinstance(subject, Student) and subject.teacher and subject.teacher.user_id:
        return db.get(User, subject.teacher.user_id)
    if role == "supervisor" and isinstance(subject, Teacher) and subject.supervisor_id:
        return db.get(User, subject.supervisor_id)
    if role == "head_of_admissions":
        return contacts.referral_owner(db)
    if role == "qa":
        for slug in ("hod_qa", "qa_officer"):
            u = (db.query(User).join(Role, Role.id == User.role_id).filter(User.is_active.is_(True), Role.slug == slug).order_by(User.id).first())
            if u:
                return u
    if isinstance(subject, Student):
        return journey.academic_owner(db, subject)
    return (db.query(User).join(Role, Role.id == User.role_id).filter(User.is_active.is_(True), Role.slug.in_(["academy_manager", "hod_academics"]))
            .order_by(User.id).first())


def approve(db: Session, item: AttentionItem, user: User, keys: list[str], note: Optional[str] = None, request=None) -> list[Task]:
    """Create the chosen recommended actions as tasks with owners and due dates."""
    if item.status not in ("open", "snoozed"):
        raise ValueError("This item has already been decided.")
    chosen = [a for a in (item.recommended or []) if a["key"] in set(keys)]
    if not chosen:
        raise ValueError("Choose at least one action.")
    subject = subject_of(db, item)
    name = getattr(subject, "full_name", f"#{item.subject_id}")
    entity = {"student": "Student", "family": "Client", "teacher": "Teacher"}[item.subject_type]
    tasks = []
    for a in chosen:
        owner = _owner(db, item, a["owner"])
        t = Task(title=f"{a['label']}: {name}"[:200], description=(item.summary or "") + (f"\n\n{note}" if note else ""),
                 assignee_id=owner.id if owner else None, creator_id=user.id, priority=a["priority"], status="todo",
                 due_date=today() + timedelta(days=a["due_days"]), entity_type=entity, entity_id=item.subject_id)
        db.add(t)
        db.flush()
        tasks.append(t)
        if owner and owner.id != user.id:
            notify(db, owner, f"Needs attention: {name}", a["label"], event_type="task_assigned", link=f"/tasks/{t.id}")
    item.status, item.decided_by_id, item.decided_at = "actioned", user.id, datetime.utcnow()
    item.decision_note = (note or "").strip() or None
    item.task_ids = list(item.task_ids or []) + [t.id for t in tasks]
    log_action(db, user, "approve", "attention", entity=item, request=request,
               description=f"Approved {len(tasks)} action(s) for {name}: " + "; ".join(a["label"] for a in chosen))
    return tasks


def dismiss(db: Session, item: AttentionItem, user: User, reason: str, request=None) -> None:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Give the reason for dismissing it.")
    if item.status in ("dismissed", "resolved"):
        raise ValueError("This item is already closed.")
    item.status, item.decided_by_id, item.decided_at, item.decision_note = "dismissed", user.id, datetime.utcnow(), reason
    log_action(db, user, "update", "attention", entity=item, request=request, rationale=reason,
               description=f"Dismissed the attention item for {item.subject_type} #{item.subject_id}")


def snooze(db: Session, item: AttentionItem, user: User, days: int, request=None) -> None:
    if item.status not in ("open",):
        raise ValueError("Only an open item can be snoozed.")
    days = max(1, min(int(days or 7), 30))
    item.status, item.snoozed_until = "snoozed", today() + timedelta(days=days)
    log_action(db, user, "update", "attention", entity=item, request=request, description=f"Snoozed for {days} day(s)")


# ============================================================================ who sees what
def visible(db: Session, user: User, query):
    """Management sees everything. A supervisor sees their teachers' students and teachers. Teacher items (performance
    and complaints) are for management only."""
    if user.is_superuser or rbac.is_management(user):
        return query
    if user.role_slug == "supervisor":
        tids = [i for (i,) in db.query(Teacher.id).filter(Teacher.supervisor_id == user.id)] or [-1]
        sids = [i for (i,) in db.query(Student.id).filter(Student.teacher_id.in_(tids))] or [-1]
        return query.filter(or_((AttentionItem.subject_type == "student") & AttentionItem.subject_id.in_(sids),
                                (AttentionItem.subject_type == "teacher") & AttentionItem.subject_id.in_(tids)))
    return query.filter(AttentionItem.subject_type != "teacher")


def for_student(db: Session, student_id: int) -> Optional[AttentionItem]:
    return (db.query(AttentionItem).filter(AttentionItem.subject_type == "student", AttentionItem.subject_id == student_id,
                                           AttentionItem.status.in_(["open", "snoozed"])).order_by(AttentionItem.id.desc()).first())


def stats(db: Session, user: User) -> dict:
    base = visible(db, user, db.query(AttentionItem))
    return {"open": base.filter(AttentionItem.status == "open").count(),
            "high": base.filter(AttentionItem.status == "open", AttentionItem.level == "high").count(),
            "students": base.filter(AttentionItem.status == "open", AttentionItem.subject_type == "student").count(),
            "teachers": base.filter(AttentionItem.status == "open", AttentionItem.subject_type == "teacher").count(),
            "opportunities": base.filter(AttentionItem.status == "open", AttentionItem.kind == "opportunity").count(),
            "actioned": base.filter(AttentionItem.status == "actioned").count()}


def check_student(db: Session, s: Student, now: Optional[datetime] = None) -> Optional[AttentionItem]:
    return _upsert(db, "student", s.id, s.full_name, "concern", student_signals(db, s, today()), now or datetime.utcnow())


def check_family(db: Session, c: Client, now: Optional[datetime] = None) -> Optional[AttentionItem]:
    return _upsert(db, "family", c.id, c.full_name, "opportunity", family_signals(db, c, today()), now or datetime.utcnow())


def check_teacher(db: Session, t: Teacher, now: Optional[datetime] = None) -> Optional[AttentionItem]:
    return _upsert(db, "teacher", t.id, t.full_name, "concern", teacher_signals(db, t, today()), now or datetime.utcnow())
