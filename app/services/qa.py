"""QA, AI class monitoring and recordings service (Modules 17, 25, 47).

Rule: no disciplinary action is ever based solely on an AI score - every AI output is reviewed by a human.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.notify import notify
from app.models.academic import Course
from app.models.core import AIModelRun, RiskAlert, User
from app.models.people import Teacher
from app.models.scheduling import (ClassSession, Recording, RecordingAccessLog, AIClassAnalysis, QAReview, CorrectiveAction,
                                   SafeguardingFlag, Schedule, Shift)
from app.services.ai_gateway import ai, review_run

QA_WEIGHTS = {"tajweed": 0.25, "methodology": 0.20, "engagement": 0.20, "punctuality": 0.10, "environment": 0.10, "professionalism": 0.15}
QA_CRITERIA = [("tajweed", "Tajweed accuracy"), ("methodology", "Teaching methodology"), ("engagement", "Student engagement"),
               ("punctuality", "Punctuality & duration"), ("environment", "Class environment (camera, noise, setup)"),
               ("professionalism", "Professionalism & conduct")]
APPROVAL_THRESHOLD = 60.0  # reviews scoring below this are high-impact and need HOD approval


# ----------------------------------------------------------------------------- recordings
def retention_days(db: Session) -> int:
    from app.services.scheduling import setting_value
    return int(setting_value(db, "recording_retention_days", 365) or 365)


def _fake_transcript(session: ClassSession) -> str:
    student = session.student.full_name if session.student else "the student"
    course = session.course.name if session.course else "the lesson"
    return (f"[00:00] Teacher: Assalamu alaikum {student}, let us begin with revision of yesterday's sabqi.\n"
            f"[00:04] Student recites; teacher corrects makhaarij of the letter 'qaf'.\n"
            f"[00:11] Teacher: Now the new sabaq from {course}. Repeat after me, slowly.\n"
            f"[00:19] Student repeats three times; teacher notes the madd rule.\n"
            f"[00:26] Teacher summarises homework: dor of the last two pages.\n"
            f"[00:28] Teacher: JazakAllah khair, see you in the next class.\n"
            f"[simulated transcript - configure AI_PROVIDER for real transcription]")


def ingest_recording(db: Session, session: ClassSession, user: Optional[User] = None, source: str = "platform", request=None) -> Recording:
    """Simulated ingestion: creates a Recording row with a storage path for a done session."""
    if session.recording:
        return session.recording
    if session.status != "done":
        raise ValueError("Only completed (done) sessions can have recordings ingested.")
    duration = int((session.actual_duration_minutes or session.duration_minutes or 30) * 60)
    rec = Recording(session_id=session.id, source=source,
                    file_path=f"storage/recordings/{session.date.isoformat()}/{session.room_name or 'room'}-{session.id}.mp4",
                    duration_seconds=duration, size_bytes=duration * 180_000, status="available",
                    retention_until=session.date + timedelta(days=retention_days(db)), transcript=_fake_transcript(session))
    db.add(rec)
    db.flush()
    log_action(db, user, "create", "recordings", entity=rec, description=f"Recording ingested for session #{session.id} ({source})", request=request)
    return rec


def log_recording_access(db: Session, rec: Recording, user: User, purpose: str, request=None) -> RecordingAccessLog:
    ip = None
    if request is not None:
        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (request.client.host if request.client else None)
    entry = RecordingAccessLog(recording_id=rec.id, user_id=user.id, purpose=purpose[:200], ip=ip)
    db.add(entry)
    rec.access_count = (rec.access_count or 0) + 1
    log_action(db, user, "recording_access", "recordings", entity=rec, description=f"Opened recording of session #{rec.session_id}",
               rationale=purpose, request=request, consequential=True)
    db.flush()
    return entry


def purge_expired_recordings(db: Session) -> int:
    today = date.today()
    n = 0
    for rec in db.query(Recording).filter(Recording.retention_until.isnot(None), Recording.retention_until < today,
                                          Recording.status.notin_(["expired", "deleted"])).all():
        rec.status = "expired"
        n += 1
    db.flush()
    return n


# ----------------------------------------------------------------------------- AI analysis
def run_ai_analysis(db: Session, session: ClassSession, force: bool = False) -> AIClassAnalysis:
    """Analyse a session (recording metadata + timings) through the AI gateway and store metrics.
    Creates safeguarding / ops alerts on strong signals and queues a QA review for high-risk output."""
    existing = session.ai_analysis
    if existing and not force:
        return existing
    payload = {
        "session_id": session.id, "teacher_id": session.teacher_id, "student_id": session.student_id, "date": session.date.isoformat(),
        "start_time": session.start_time.strftime("%H:%M"), "duration": session.duration_minutes or 30,
        "actual_duration": session.actual_duration_minutes or session.duration_minutes or 30,
        "late_minutes": session.teacher_late_minutes or 0, "course": session.course.code if session.course else None,
        "recording_id": session.recording.id if session.recording else None,
        "recording_seconds": session.recording.duration_seconds if session.recording else 0,
    }
    result, run = ai(db, module="class_monitoring", task="analyse_recording", payload=payload, entity=session)
    an = existing or AIClassAnalysis(session_id=session.id)
    an.recording_id = session.recording.id if session.recording else None
    an.model_run_id = run.id
    an.teacher_id = session.teacher_id
    an.camera_presence_pct = float(result.get("camera_presence_pct", 0))
    an.punctuality_minutes = int(result.get("punctuality_minutes", session.teacher_late_minutes or 0))
    an.duration_compliance_pct = float(result.get("duration_compliance_pct", 0))
    an.active_teaching_pct = float(result.get("active_teaching_pct", 0))
    an.idle_pct = float(result.get("idle_pct", 0))
    an.student_engagement_score = float(result.get("student_engagement_score", 0))
    an.curriculum_coverage_pct = float(result.get("curriculum_coverage_pct", 0))
    an.tone_flags = list(result.get("tone_flags") or [])
    an.conduct_flags = list(result.get("conduct_flags") or [])
    an.contact_exchange_detected = bool(result.get("contact_exchange_detected"))
    an.overall_score = float(result.get("overall_score", 0))
    an.confidence = float(result.get("confidence", 0))
    an.summary = result.get("summary")
    an.recommended_feedback = result.get("recommended_feedback")
    an.risk_level = result.get("risk_level", "low")
    an.review_status = "pending"
    if not existing:
        db.add(an)
    db.flush()
    if session.recording and session.recording.status == "available":
        session.recording.status = "analysed"
    teacher_name = session.teacher.full_name if session.teacher else f"teacher #{session.teacher_id}"
    # safeguarding signals -> CEO-only flag + alert
    if an.contact_exchange_detected or an.conduct_flags:
        ftype = "contact_exchange" if an.contact_exchange_detected else "conduct"
        db.add(SafeguardingFlag(flag_type=ftype, severity="high" if an.contact_exchange_detected else "medium", session_id=session.id,
                                teacher_id=session.teacher_id, student_id=session.student_id,
                                evidence=(f"AI analysis of session #{session.id} on {session.date}: "
                                          + ("possible off-platform contact exchange detected. " if an.contact_exchange_detected else "")
                                          + (f"Conduct flags: {', '.join(an.conduct_flags)}. " if an.conduct_flags else "")
                                          + f"Confidence {an.confidence:.2f}. Human review required before any action."),
                                source="ai", ai_run_id=run.id, status="open", visibility="ceo_only"))
        db.add(RiskAlert(alert_type="safeguarding_signal", severity="high" if an.contact_exchange_detected else "medium",
                         title=f"Safeguarding signal ({ftype.replace('_', ' ')}) - {teacher_name}",
                         message=f"AI flagged session #{session.id} on {session.date}. Review in Safeguarding before any action.",
                         entity_type="ClassSession", entity_id=session.id, visibility="ceo_only", status="open", source="ai"))
    if an.punctuality_minutes > 10:
        db.add(RiskAlert(alert_type="teacher_late", severity="medium", title=f"Teacher late {an.punctuality_minutes} min - {teacher_name}",
                         message=f"AI punctuality check for session #{session.id} on {session.date} at {session.start_time.strftime('%H:%M')}.",
                         entity_type="ClassSession", entity_id=session.id, visibility="ops", status="open", source="ai"))
    if an.risk_level == "high":
        already = db.query(QAReview).filter(QAReview.session_id == session.id).first()
        if not already:
            queue_qa_review(db, session, "risk_based", ai_analysis=an)
    db.flush()
    return an


def review_analysis(db: Session, an: AIClassAnalysis, user: User, action: str, note: str = "", corrections: Optional[dict] = None,
                    request=None) -> AIClassAnalysis:
    """Human review: approve | override (with corrected values) | false_positive."""
    if action not in ("approved", "overridden", "false_positive"):
        raise ValueError("Invalid review action")
    before = {"review_status": an.review_status, "overall_score": an.overall_score, "risk_level": an.risk_level,
              "contact_exchange_detected": an.contact_exchange_detected}
    if action == "overridden" and corrections:
        for k, v in corrections.items():
            if v is None or not hasattr(an, k):
                continue
            setattr(an, k, v)
    if action == "false_positive":
        an.contact_exchange_detected = False
        an.conduct_flags = []
        an.tone_flags = []
        an.risk_level = "low"
        for fl in db.query(SafeguardingFlag).filter(SafeguardingFlag.session_id == an.session_id, SafeguardingFlag.source == "ai",
                                                    SafeguardingFlag.status.in_(["open", "investigating"])).all():
            fl.status = "dismissed"
            fl.handled_by_id = user.id
            fl.resolution = f"Dismissed as AI false positive by {user.full_name}: {note}"
    an.review_status = action
    an.reviewed_by_id = user.id
    an.reviewed_at = datetime.utcnow()
    an.review_note = note
    run = db.get(AIModelRun, an.model_run_id) if an.model_run_id else None
    if run:
        review_run(db, run, user, "approved" if action == "approved" else action, note)
    log_action(db, user, "override" if action != "approved" else "approve", "ai_monitoring", entity=an,
               description=f"AI analysis {action} for session #{an.session_id}", rationale=note, before=before,
               after={"review_status": an.review_status, "overall_score": an.overall_score, "risk_level": an.risk_level}, request=request)
    db.flush()
    return an


def send_ai_feedback(db: Session, an: AIClassAnalysis, user: User, message: Optional[str] = None, request=None) -> bool:
    teacher = an.teacher or (an.session.teacher if an.session else None)
    if not teacher or not teacher.user_id:
        return False
    body = message or an.recommended_feedback or an.summary or "Please review your recent class analysis."
    notify(db, teacher.user_id, f"Class feedback ({an.session.date if an.session else ''})", body, event_type="qa_feedback", link="/teacher/qa")
    log_action(db, user, "notify", "ai_monitoring", entity=an, description=f"Feedback sent to {teacher.full_name}", request=request)
    return True


# ----------------------------------------------------------------------------- QA reviews
def queue_qa_review(db: Session, session: Optional[ClassSession], sample_type: str, reviewer: Optional[User] = None,
                    teacher: Optional[Teacher] = None, ai_analysis: Optional[AIClassAnalysis] = None, re_evaluation_of: Optional[QAReview] = None) -> QAReview:
    teacher_id = session.teacher_id if session else (teacher.id if teacher else None)
    if not teacher_id:
        raise ValueError("A session or teacher is required to queue a QA review.")
    an = ai_analysis or (session.ai_analysis if session else None)
    r = QAReview(session_id=session.id if session else None, teacher_id=teacher_id, reviewer_id=reviewer.id if reviewer else None,
                 ai_analysis_id=an.id if an else None, sample_type=sample_type, status="in_review" if reviewer else "queued",
                 re_evaluation_of_id=re_evaluation_of.id if re_evaluation_of else None)
    db.add(r)
    db.flush()
    return r


def _reviewable_sessions(db: Session, days: int = 7):
    since = date.today() - timedelta(days=days)
    reviewed = db.query(QAReview.session_id).filter(QAReview.session_id.isnot(None))
    return (db.query(ClassSession).filter(ClassSession.status == "done", ClassSession.date >= since, ClassSession.date <= date.today(),
                                          ClassSession.id.notin_(reviewed)))


def sample_random(db: Session, n: int, reviewer: Optional[User] = None, days: int = 7) -> list[QAReview]:
    """Random sample of done sessions from the last N days without a QA review (prefers those with recordings)."""
    candidates = _reviewable_sessions(db, days).all()
    if not candidates:
        return []
    # deterministic-but-spread ordering: hash of id + today
    key = date.today().isoformat()
    candidates.sort(key=lambda s: (0 if s.recording else 1, hashlib.md5(f"{s.id}-{key}".encode()).hexdigest()))
    out = []
    seen_teachers: dict[int, int] = {}
    for s in candidates:
        if len(out) >= n:
            break
        if seen_teachers.get(s.teacher_id, 0) >= max(1, n // 3):
            continue
        seen_teachers[s.teacher_id] = seen_teachers.get(s.teacher_id, 0) + 1
        out.append(queue_qa_review(db, s, "random", reviewer))
    return out


def sample_risk_based(db: Session, n: int, reviewer: Optional[User] = None, days: int = 14) -> list[QAReview]:
    """Sessions whose AI analysis is medium/high risk, or taught by grade-C teachers."""
    c_teachers = [tid for (tid,) in db.query(Teacher.id).filter(Teacher.grade == "C")]
    q = _reviewable_sessions(db, days).outerjoin(AIClassAnalysis, AIClassAnalysis.session_id == ClassSession.id)
    q = q.filter((AIClassAnalysis.risk_level.in_(["medium", "high"])) | (ClassSession.teacher_id.in_(c_teachers or [-1])))
    rows = q.order_by(ClassSession.date.desc()).all()
    rows.sort(key=lambda s: (0 if (s.ai_analysis and s.ai_analysis.risk_level == "high") else 1, -s.id))
    return [queue_qa_review(db, s, "risk_based", reviewer) for s in rows[:n]]


def compute_overall(scores: dict) -> float:
    total, weight = 0.0, 0.0
    for k, w in QA_WEIGHTS.items():
        v = scores.get(k)
        if v is None:
            continue
        total += max(0.0, min(10.0, float(v))) * w
        weight += w
    return round(total / weight * 10, 1) if weight else 0.0


def complete_qa_review(db: Session, review: QAReview, scores: dict, user: User, strengths: str = "", weaknesses: str = "",
                       comments: str = "", request=None) -> QAReview:
    for k in QA_WEIGHTS:
        v = scores.get(k)
        setattr(review, f"{k}_score", None if v is None else max(0.0, min(10.0, float(v))))
    review.overall_score = compute_overall(scores)
    review.strengths, review.weaknesses, review.comments = strengths or None, weaknesses or None, comments or None
    review.reviewer_id = review.reviewer_id or user.id
    review.completed_at = datetime.utcnow()
    review.status = "completed"  # 'approved' only after HOD approval when high-impact (< threshold)
    log_action(db, user, "update", "qa", entity=review, description=f"QA review completed: overall {review.overall_score}",
               after={"overall": review.overall_score, **{k: scores.get(k) for k in QA_WEIGHTS}}, request=request)
    if review.overall_score >= APPROVAL_THRESHOLD:
        update_teacher_qa_avg(db, review.teacher_id)
    else:
        db.add(RiskAlert(alert_type="qa_low_score", severity="medium",
                         title=f"Low QA score {review.overall_score} - {review.teacher.full_name if review.teacher else review.teacher_id}",
                         message="High-impact review awaiting HOD QA approval before it counts toward the teacher's average.",
                         entity_type="QAReview", entity_id=review.id, visibility="management", source="system"))
    db.flush()
    return review


def approve_qa_review(db: Session, review: QAReview, user: User, note: str = "", request=None) -> QAReview:
    review.status = "approved"
    review.approved_by_id = user.id
    review.approved_at = datetime.utcnow()
    if note:
        review.comments = (review.comments or "") + f"\n[HOD approval] {note}"
    update_teacher_qa_avg(db, review.teacher_id)
    log_action(db, user, "approve", "qa", entity=review, description=f"High-impact QA review approved (score {review.overall_score})", rationale=note, request=request)
    db.flush()
    return review


def counted_reviews_query(db: Session, teacher_id: int):
    """Reviews that count toward the teacher average: completed >= threshold, or approved."""
    return db.query(QAReview).filter(QAReview.teacher_id == teacher_id, QAReview.overall_score.isnot(None),
                                     ((QAReview.status == "approved") | ((QAReview.status == "completed") & (QAReview.overall_score >= APPROVAL_THRESHOLD))))


def update_teacher_qa_avg(db: Session, teacher_id: int) -> float:
    rows = counted_reviews_query(db, teacher_id).order_by(QAReview.completed_at.desc().nullslast(), QAReview.id.desc()).limit(10).all()
    t = db.get(Teacher, teacher_id)
    if not t:
        return 0.0
    if rows:
        t.qa_score_avg = round(sum(r.overall_score for r in rows) / len(rows), 1)
    db.flush()
    return t.qa_score_avg


def add_corrective_action(db: Session, review: Optional[QAReview], teacher_id: int, description: str, due: Optional[date], user: User, request=None) -> CorrectiveAction:
    ca = CorrectiveAction(qa_review_id=review.id if review else None, teacher_id=teacher_id, description=description, assigned_by_id=user.id,
                          due_date=due, status="open")
    db.add(ca)
    db.flush()
    t = db.get(Teacher, teacher_id)
    if t and t.user_id:
        notify(db, t.user_id, "Corrective action assigned", f"{description}" + (f" (due {due})" if due else ""), event_type="qa_action", link="/teacher/qa")
    log_action(db, user, "create", "qa", entity=ca, description=f"Corrective action for teacher #{teacher_id}: {description[:80]}", request=request)
    return ca


def close_corrective_action(db: Session, ca: CorrectiveAction, user: User, note: str, request=None) -> CorrectiveAction:
    ca.status = "closed"
    ca.closed_at = datetime.utcnow()
    ca.closure_note = note
    log_action(db, user, "update", "qa", entity=ca, description="Corrective action closed", rationale=note, request=request)
    db.flush()
    return ca


def refresh_overdue_actions(db: Session) -> int:
    n = 0
    for ca in db.query(CorrectiveAction).filter(CorrectiveAction.status.in_(["open", "in_progress"]), CorrectiveAction.due_date < date.today()).all():
        ca.status = "overdue"
        n += 1
    db.flush()
    return n


def send_qa_feedback(db: Session, review: QAReview, user: User, message: Optional[str] = None, request=None) -> bool:
    t = review.teacher
    if not t or not t.user_id:
        return False
    body = message or (f"Overall {review.overall_score}/100. Strengths: {review.strengths or '-'}. Areas to improve: {review.weaknesses or '-'}. {review.comments or ''}")
    notify(db, t.user_id, "QA feedback on your class", body, event_type="qa_feedback", link="/teacher/qa")
    review.teacher_feedback_sent = True
    log_action(db, user, "notify", "qa", entity=review, description=f"QA feedback sent to {t.full_name}", request=request)
    db.flush()
    return True


def create_re_evaluation(db: Session, review: QAReview, session: Optional[ClassSession], user: User, request=None) -> QAReview:
    new = queue_qa_review(db, session, "re_evaluation", reviewer=user, teacher=review.teacher, re_evaluation_of=review)
    log_action(db, user, "create", "qa", entity=new, description=f"Re-evaluation queued for review #{review.id}", request=request)
    return new


# ----------------------------------------------------------------------------- trends
def qa_trends(db: Session, months: int = 6) -> dict:
    rows = (db.query(QAReview).filter(QAReview.overall_score.isnot(None), QAReview.status.in_(["completed", "approved"]))
            .order_by(QAReview.completed_at).all())
    by_teacher: dict[str, list] = {}
    by_supervisor: dict[str, list] = {}
    by_course: dict[str, list] = {}
    by_shift: dict[str, list] = {}
    by_month: dict[str, list] = {}
    users = {u.id: u.full_name for u in db.query(User).filter(User.id.in_([t.supervisor_id for t in db.query(Teacher) if t.supervisor_id] or [-1]))}
    sched_shift = {}
    for r in rows:
        t = r.teacher
        tname = t.full_name if t else f"#{r.teacher_id}"
        by_teacher.setdefault(tname, []).append(r.overall_score)
        sup = users.get(t.supervisor_id, "Unassigned") if t else "Unassigned"
        by_supervisor.setdefault(sup, []).append(r.overall_score)
        s = r.session
        cname = s.course.name if s and s.course else "Unknown"
        by_course.setdefault(cname, []).append(r.overall_score)
        shift = "Unknown"
        if s and s.schedule_id:
            if s.schedule_id not in sched_shift:
                sch = db.get(Schedule, s.schedule_id)
                sched_shift[s.schedule_id] = sch.shift.name if sch and sch.shift else (t.shift.title() if t else "Unknown")
            shift = sched_shift[s.schedule_id]
        elif t:
            shift = t.shift.title()
        by_shift.setdefault(shift, []).append(r.overall_score)
        m = (r.completed_at or r.created_at).strftime("%Y-%m")
        by_month.setdefault(m, []).append(r.overall_score)

    def avg(d: dict) -> list[tuple[str, float, int]]:
        return sorted(((k, round(sum(v) / len(v), 1), len(v)) for k, v in d.items()), key=lambda x: -x[1])

    months_sorted = sorted(by_month)[-months:]
    return {"by_teacher": avg(by_teacher), "by_supervisor": avg(by_supervisor), "by_course": avg(by_course), "by_shift": avg(by_shift),
            "by_month": [(m, round(sum(by_month[m]) / len(by_month[m]), 1), len(by_month[m])) for m in months_sorted],
            "total": len(rows), "overall_avg": round(sum(r.overall_score for r in rows) / len(rows), 1) if rows else 0.0}


def ai_usage_by_month(db: Session, months: int = 6) -> list[dict]:
    since = datetime.utcnow() - timedelta(days=31 * months)
    rows = db.query(AIModelRun).filter(AIModelRun.module == "class_monitoring", AIModelRun.created_at >= since).all()
    agg: dict[str, dict] = {}
    for r in rows:
        k = r.created_at.strftime("%Y-%m")
        a = agg.setdefault(k, {"month": k, "runs": 0, "cost": 0.0, "tokens": 0, "reviewed": 0})
        a["runs"] += 1
        a["cost"] += float(r.cost_usd or 0)
        a["tokens"] += (r.tokens_in or 0) + (r.tokens_out or 0)
        a["reviewed"] += 1 if r.review_status != "pending" else 0
    return [agg[k] for k in sorted(agg)]


def teacher_ai_trends(db: Session, teacher_ids: Optional[list[int]] = None, weeks: int = 8) -> list[dict]:
    since = date.today() - timedelta(weeks=weeks)
    q = (db.query(AIClassAnalysis).join(ClassSession, ClassSession.id == AIClassAnalysis.session_id).filter(ClassSession.date >= since))
    if teacher_ids is not None:
        q = q.filter(AIClassAnalysis.teacher_id.in_(teacher_ids or [-1]))
    rows = q.all()
    per: dict[int, dict] = {}
    for an in rows:
        d = per.setdefault(an.teacher_id, {"teacher": an.teacher, "scores": [], "weeks": {}, "months": {}, "flags": 0, "late": []})
        d["scores"].append(an.overall_score)
        d["late"].append(an.punctuality_minutes)
        wk = an.session.date.isocalendar()[1] if an.session else 0
        d["weeks"].setdefault(wk, []).append(an.overall_score)
        d["months"].setdefault(an.session.date.strftime("%Y-%m") if an.session else "", []).append(an.overall_score)
        d["flags"] += 1 if (an.conduct_flags or an.tone_flags or an.contact_exchange_detected) else 0
    out = []
    for tid, d in per.items():
        weeks_sorted = sorted(d["weeks"])
        out.append({"teacher": d["teacher"], "n": len(d["scores"]), "avg": round(sum(d["scores"]) / len(d["scores"]), 1),
                    "avg_late": round(sum(d["late"]) / len(d["late"]), 1), "flags": d["flags"],
                    "weekly": [round(sum(d["weeks"][w]) / len(d["weeks"][w]), 1) for w in weeks_sorted],
                    "week_labels": [f"W{w}" for w in weeks_sorted],
                    "monthly": [(m, round(sum(v) / len(v), 1)) for m, v in sorted(d["months"].items())]})
    out.sort(key=lambda x: -x["avg"])
    return out
