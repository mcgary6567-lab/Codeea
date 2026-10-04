"""Quality Management call pipeline (docs/AUDIT_ACADEMICS.md 3.9).

Call recordings are pulled from Agent / Teams / Zoom (simulated here), mapped to class sessions,
queued for QA review, rated per parameter (1-5 stars) with an issue checklist, and rolled up into
the QA dashboard and the Teacher QA Performance report.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.models.core import User
from app.models.erp import CALL_SOURCES, CallRecord, QAIssueType, QAReviewParameter
from app.models.people import Teacher
from app.models.scheduling import ClassSession, QAReview

CALL_REVIEW_STATUSES = ["queued", "in_review", "completed", "flagged", "rejected"]
QUEUE_STATES = ["mapped", "queued"]
PLATFORMS = {"AGENT": "Agent Softphone", "TEAMS": "Microsoft Teams", "ZOOM": "Zoom Meetings"}
# Legacy 0-10 scorecard fields fed from the 1-5 star parameters when names match.
LEGACY_FIELD_FOR_PARAMETER = {
    "tajweed accuracy": "tajweed_score", "tajweed": "tajweed_score",
    "engagement": "engagement_score", "student engagement": "engagement_score",
    "adab": "professionalism_score", "professionalism": "professionalism_score",
    "lesson planning": "methodology_score", "methodology": "methodology_score",
    "punctuality": "punctuality_score", "environment": "environment_score",
}
DEFAULT_PARAMETERS = [
    ("Engagement", "Student kept active, questions asked, energy of the class", 1.0, 1),
    ("Tajweed Accuracy", "Correct makhaarij and tajweed rules applied and corrected", 1.5, 2),
    ("Adab", "Islamic manners, greeting, tone and respect", 1.0, 3),
    ("Lesson Planning", "Sabaq / sabqi / dor covered as planned", 1.0, 4),
    ("Punctuality", "Joined on time and used the full session", 0.75, 5),
    ("Environment", "Camera on, quiet background, professional setup", 0.75, 6),
]
DEFAULT_ISSUE_TYPES = [
    ("Engagement", "normal", "Student passive or teacher monologue"),
    ("Tajweed Accuracy", "normal", "Uncorrected tajweed mistakes"),
    ("Adab", "normal", "Manners / tone below the college standard"),
    ("Lesson Planning", "normal", "Lesson plan not followed"),
    ("Late Join", "critical", "Teacher joined more than 5 minutes late"),
    ("Missed Class", "critical", "Teacher did not attend the class"),
    ("Misconduct", "critical", "Behaviour requiring management attention"),
]


# ----------------------------------------------------------------------------- configuration helpers
def active_parameters(db: Session) -> list[QAReviewParameter]:
    return (db.query(QAReviewParameter).filter(QAReviewParameter.status == "active")
            .order_by(QAReviewParameter.sort_no, QAReviewParameter.id).all())


def active_issue_types(db: Session) -> list[QAIssueType]:
    return db.query(QAIssueType).filter(QAIssueType.status == "active").order_by(QAIssueType.severity.desc(), QAIssueType.name).all()


def compute_overall_rating(params: list[QAReviewParameter], scores: dict) -> Optional[float]:
    """Weighted mean of the 1-5 parameter ratings, normalised to a 5-star scale."""
    total = weight = 0.0
    by_name = {p.name: p for p in params}
    for name, raw in (scores or {}).items():
        if raw in (None, ""):
            continue
        p = by_name.get(name)
        w = float(p.weight) if p and p.weight else 1.0
        mx = float(p.max_rating) if p and p.max_rating else 5.0
        total += (float(raw) / mx) * 5.0 * w
        weight += w
    return round(total / weight, 2) if weight else None


def _source_for(session: ClassSession) -> str:
    """Deterministic platform pick: night-shift teachers mostly use the Agent softphone, others Teams/Zoom."""
    shift = (session.teacher.shift if session.teacher else "evening") or "evening"
    h = int(hashlib.md5(f"call-{session.id}".encode()).hexdigest(), 16) % 10
    if shift == "night":
        return "AGENT" if h < 6 else ("TEAMS" if h < 8 else "ZOOM")
    if shift == "morning":
        return "ZOOM" if h < 5 else ("TEAMS" if h < 8 else "AGENT")
    return "TEAMS" if h < 4 else ("ZOOM" if h < 8 else "AGENT")


def build_call_for_session(session: ClassSession, source: Optional[str] = None) -> CallRecord:
    src = source or _source_for(session)
    start = datetime.combine(session.date, session.start_time)
    if session.teacher_joined_at:
        start = session.teacher_joined_at
    duration = int(session.actual_duration_minutes or session.duration_minutes or 30)
    student = session.student.full_name if session.student else f"student #{session.student_id}"
    teacher = session.teacher
    return CallRecord(
        source=src, platform=PLATFORMS[src],
        source_name=(f"{teacher.full_name if teacher else 'Teacher'} - {student}" if src != "AGENT"
                     else f"+44 20 {7000 + session.id % 900:04d} {session.id % 10000:04d}"),
        external_id=f"{src.lower()}-{session.date.strftime('%Y%m%d')}-{session.id}",
        recording_date=session.date, employee_id=teacher.employee_id if teacher else None, teacher_id=session.teacher_id,
        session_id=session.id, start_time=start, end_time=start + timedelta(minutes=duration), duration_minutes=duration,
        meeting_status="ended", recording_url=f"https://recordings.example/{src.lower()}/{session.id}", review_state="mapped")


def build_unmatched_agent_call(teacher: Teacher, day: date, seq: int) -> CallRecord:
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=6 + (seq * 7) % 15, minutes=(seq * 13) % 60)
    duration = 10 + (seq * 11) % 35
    return CallRecord(source="AGENT", platform=PLATFORMS["AGENT"], source_name=f"+1 {300 + seq % 600:03d} {555:03d} {1000 + seq * 37 % 9000:04d}",
                      external_id=f"agent-{day.strftime('%Y%m%d')}-u{seq}", recording_date=day,
                      employee_id=teacher.employee_id if teacher else None, teacher_id=teacher.id if teacher else None,
                      session_id=None, start_time=start, end_time=start + timedelta(minutes=duration), duration_minutes=duration,
                      meeting_status="ended", recording_url=f"https://recordings.example/agent/u{day.strftime('%Y%m%d')}{seq}",
                      review_state="unmapped")


# ----------------------------------------------------------------------------- live recordings (Zoom webhook)
SIMULATED_SYNC_DISABLED = ("Connect the call provider in the Integration Hub first; "
                           "the simulated pull is disabled in production.")


def simulated_sync_allowed(db: Session) -> bool:
    """The fabricated Agent / Teams / Zoom pull only runs outside production, or once Zoom has live credentials."""
    from app.config import settings
    from app.services import integrations
    return settings.APP_ENV != "production" or integrations.has_live_credentials(db, "zoom")


def _parse_when(value) -> Optional[datetime]:
    """Zoom sends ISO-8601 UTC ("2026-10-04T09:00:00Z"); return it as naive college time like ClassSession keeps."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        from app.services.people import ORG_TZ, safe_zone
        dt = dt.astimezone(safe_zone(ORG_TZ)).replace(tzinfo=None)
    return dt


def _teacher_for_host(db: Session, email: str) -> Optional[Teacher]:
    email = (email or "").strip().lower()
    if not email:
        return None
    from app.models.people import Employee
    user = db.query(User).filter(func.lower(User.email) == email).first()
    if user:
        t = db.query(Teacher).filter(Teacher.user_id == user.id).first()
        if t:
            return t
    emp = db.query(Employee).filter(func.lower(Employee.email) == email).first()
    return db.query(Teacher).filter(Teacher.employee_id == emp.id).first() if emp else None


def _session_for_recording(db: Session, teacher: Optional[Teacher], start: datetime, window_minutes: int = 45) -> Optional[ClassSession]:
    taken = db.query(CallRecord.session_id).filter(CallRecord.session_id.isnot(None))
    q = (db.query(ClassSession).filter(ClassSession.status == "done", ClassSession.id.notin_(taken),
                                       ClassSession.scheduled_start >= start - timedelta(minutes=window_minutes),
                                       ClassSession.scheduled_start <= start + timedelta(minutes=window_minutes)))
    if teacher is None:
        return None
    return q.filter(ClassSession.teacher_id == teacher.id).order_by(ClassSession.scheduled_start).first()


def ingest_zoom_recording(db: Session, obj: dict, user: Optional[User] = None, request=None) -> tuple[CallRecord, bool]:
    """A Zoom ``recording.completed`` payload object becomes the CallRecord the simulated sync would have built.

    ``obj`` is Zoom's ``payload.object``: ``uuid``/``id``, ``topic``, ``host_email``, ``start_time`` (UTC ISO),
    ``duration`` (minutes), ``share_url`` and ``recording_files[]`` (``play_url`` / ``download_url``). The host's
    email finds the teacher (through their login or employee record) and the nearest done class within 45 minutes
    of the start maps the call; otherwise it lands in the un-matched queue. Idempotent on the meeting uuid/id.
    """
    meeting_id = str(obj.get("uuid") or obj.get("id") or "").strip()
    external_id = f"zoom-{meeting_id}"[:120] if meeting_id else None
    if external_id:
        existing = db.query(CallRecord).filter(CallRecord.source == "ZOOM", CallRecord.external_id == external_id).first()
        if existing:
            return existing, False
    start = _parse_when(obj.get("start_time")) or datetime.utcnow()
    duration = int(obj.get("duration") or 0)
    files = [f for f in (obj.get("recording_files") or []) if isinstance(f, dict)]
    if not duration and files:
        ends = [(_parse_when(f.get("recording_start")), _parse_when(f.get("recording_end"))) for f in files]
        spans = [int((e - s).total_seconds() // 60) for s, e in ends if s and e]
        duration = max(spans) if spans else 0
    duration = duration or 30
    url = next((f.get("play_url") or f.get("download_url") for f in files if f.get("play_url") or f.get("download_url")), None)
    url = url or obj.get("share_url") or None
    teacher = _teacher_for_host(db, obj.get("host_email") or "")
    session = _session_for_recording(db, teacher, start)
    if session and teacher is None:
        teacher = session.teacher
    rec = CallRecord(
        source="ZOOM", platform=PLATFORMS["ZOOM"], source_name=str(obj.get("topic") or "Zoom meeting")[:150],
        external_id=external_id, recording_date=start.date(),
        employee_id=teacher.employee_id if teacher else None, teacher_id=teacher.id if teacher else None,
        session_id=session.id if session else None, start_time=start, end_time=start + timedelta(minutes=duration),
        duration_minutes=duration, meeting_status="ended", recording_url=(url or "")[:500] or None,
        review_state="mapped" if session else "unmapped")
    db.add(rec)
    db.flush()
    log_action(db, user, "create", "qa", entity=rec,
               description=f"Zoom recording {external_id or '(no id)'} received"
                           + (f" and mapped to class session #{session.id}" if session else "; no matching class, queued as un-matched"),
               after={"teacher_id": rec.teacher_id, "session_id": rec.session_id, "duration": duration}, request=request)
    return rec, True


# ----------------------------------------------------------------------------- sync + mapping
def sync_calls(db: Session, user: Optional[User], days: int = 14, limit: int = 60, request=None) -> int:
    """Simulated pull from the call platforms: one CallRecord per recent done session without one, plus a few
    unmatched Agent calls. Returns the number of records created. Refused (ValueError) when
    :func:`simulated_sync_allowed` is false."""
    if not simulated_sync_allowed(db):
        raise ValueError(SIMULATED_SYNC_DISABLED)
    since = date.today() - timedelta(days=days)
    have = db.query(CallRecord.session_id).filter(CallRecord.session_id.isnot(None))
    sessions = (db.query(ClassSession).filter(ClassSession.status == "done", ClassSession.date >= since, ClassSession.date <= date.today(),
                                              ClassSession.id.notin_(have))
                .order_by(ClassSession.date.desc(), ClassSession.id.desc()).limit(limit).all())
    created = 0
    for s in sessions:
        db.add(build_call_for_session(s))
        created += 1
    # a handful of Agent calls the platform could not match to a class
    teachers = db.query(Teacher).filter(Teacher.status != "inactive").order_by(Teacher.id).all()
    if teachers:
        n_unmatched = max(1, min(4, created // 12)) if created else 2
        base_seq = (db.query(func.count(CallRecord.id)).scalar() or 0) + created
        for i in range(n_unmatched):
            t = teachers[(base_seq + i) % len(teachers)]
            day = date.today() - timedelta(days=(base_seq + i) % max(1, days))
            db.add(build_unmatched_agent_call(t, day, base_seq + i))
            created += 1
    db.flush()
    log_action(db, user, "execute", "qa", entity_type="CallRecord", description=f"Call sync: {created} recording(s) pulled from Agent/Teams/Zoom",
               after={"created": created, "days": days}, request=request)
    return created


def candidate_sessions_for(db: Session, call: CallRecord, window_days: int = 1) -> list[ClassSession]:
    """Done sessions on (or around) the recording date, preferring the call's teacher, without a call already."""
    taken = db.query(CallRecord.session_id).filter(CallRecord.session_id.isnot(None))
    q = db.query(ClassSession).filter(ClassSession.status == "done", ClassSession.id.notin_(taken),
                                      ClassSession.date >= call.recording_date - timedelta(days=window_days),
                                      ClassSession.date <= call.recording_date + timedelta(days=window_days))
    if call.teacher_id:
        q = q.filter(ClassSession.teacher_id == call.teacher_id)
    return q.order_by(ClassSession.date, ClassSession.start_time).limit(40).all()


def map_call(db: Session, call: CallRecord, session: ClassSession, user: User, request=None) -> CallRecord:
    before = {"session_id": call.session_id, "review_state": call.review_state}
    call.session_id = session.id
    call.teacher_id = session.teacher_id
    call.employee_id = session.teacher.employee_id if session.teacher else call.employee_id
    if call.review_state == "unmapped":
        call.review_state = "mapped"
    log_action(db, user, "update", "qa", entity=call, description=f"Call #{call.id} mapped to class session #{session.id}",
               before=before, after={"session_id": call.session_id, "review_state": call.review_state}, request=request)
    db.flush()
    return call


# ----------------------------------------------------------------------------- review lifecycle
def start_review(db: Session, call: CallRecord, user: User, request=None) -> QAReview:
    if call.qa_review_id:
        existing = db.get(QAReview, call.qa_review_id)
        if existing:
            if existing.status == "queued":
                existing.status = "in_review"
                existing.reviewer_id = existing.reviewer_id or user.id
                call.review_state = "in_review"
                db.flush()
            return existing
    teacher_id = call.teacher_id or (call.session.teacher_id if call.session else None)
    if not teacher_id:
        raise ValueError("The call has no teacher; map it to a class first.")
    r = QAReview(session_id=call.session_id, teacher_id=teacher_id, reviewer_id=user.id, sample_type="call", status="in_review",
                 call_record_id=call.id, parameter_scores={}, issues=[])
    db.add(r)
    db.flush()
    call.qa_review_id = r.id
    call.review_state = "in_review"
    log_action(db, user, "create", "qa", entity=r, description=f"QA call review #{r.id} started for call #{call.id} ({call.source})", request=request)
    db.flush()
    return r


def _apply_legacy_fields(review: QAReview, params: list[QAReviewParameter]) -> None:
    by_name = {p.name: p for p in params}
    for name, raw in (review.parameter_scores or {}).items():
        field = LEGACY_FIELD_FOR_PARAMETER.get(name.lower())
        if not field or raw in (None, ""):
            continue
        p = by_name.get(name)
        mx = float(p.max_rating) if p and p.max_rating else 5.0
        setattr(review, field, round(float(raw) / mx * 10.0, 1))
    if review.overall_rating is not None:
        review.overall_score = round(float(review.overall_rating) / 5.0 * 100.0, 1)


def save_review(db: Session, review: QAReview, user: User, scores: dict, issues: list, overall_rating: Optional[float],
                remarks: str, request=None, log: bool = True) -> QAReview:
    params = active_parameters(db)
    clean_scores = {}
    for p in params:
        v = scores.get(p.name)
        if v in (None, ""):
            continue
        clean_scores[p.name] = max(1, min(int(p.max_rating or 5), int(round(float(v)))))
    review.parameter_scores = clean_scores
    review.issues = issues or []
    auto = compute_overall_rating(params, clean_scores)
    review.overall_rating = round(max(1.0, min(5.0, float(overall_rating))), 2) if overall_rating not in (None, "") else auto
    review.remarks = (remarks or "").strip() or None
    review.reviewer_id = review.reviewer_id or user.id
    if review.status == "queued":
        review.status = "in_review"
    _apply_legacy_fields(review, params)
    if log:
        log_action(db, user, "update", "qa", entity=review, description=f"QA call review #{review.id} saved (rating {review.overall_rating})",
                   after={"parameter_scores": clean_scores, "issues": len(review.issues), "overall_rating": review.overall_rating}, request=request)
    db.flush()
    return review


def _call_of(db: Session, review: QAReview) -> Optional[CallRecord]:
    return db.get(CallRecord, review.call_record_id) if review.call_record_id else None


def complete_review(db: Session, review: QAReview, user: User, request=None) -> QAReview:
    if not review.parameter_scores:
        raise ValueError("Rate every parameter before completing the review.")
    review.status = "completed"
    review.reviewed_at = datetime.utcnow()
    review.completed_at = review.reviewed_at
    review.reviewer_id = review.reviewer_id or user.id
    call = _call_of(db, review)
    if call:
        call.review_state = "reviewed"
    critical = [i for i in (review.issues or []) if i.get("critical")]
    log_action(db, user, "update", "qa", entity=review,
               description=f"QA call review #{review.id} completed: {review.overall_rating} stars, {len(review.issues or [])} issue(s)"
                           + (f", {len(critical)} critical" if critical else ""),
               after={"overall_rating": review.overall_rating, "issues": review.issues}, request=request)
    from app.services.qa import update_teacher_qa_avg
    update_teacher_qa_avg(db, review.teacher_id)
    db.flush()
    return review


def flag_review(db: Session, review: QAReview, user: User, reason: str, request=None) -> QAReview:
    if not (reason or "").strip():
        raise ValueError("A reason is required to flag a review.")
    review.status = "flagged"
    review.reviewed_at = review.reviewed_at or datetime.utcnow()
    review.remarks = ((review.remarks or "") + f"\n[Flagged] {reason.strip()}").strip()
    call = _call_of(db, review)
    if call:
        call.review_state = "reviewed"
    log_action(db, user, "update", "qa", entity=review, description=f"QA call review #{review.id} flagged for management",
               rationale=reason, severity="warning", request=request)
    if review.teacher and review.teacher.supervisor_id:
        from app.core.notify import notify
        notify(db, review.teacher.supervisor_id, "QA review flagged", f"Review #{review.id} for {review.teacher.full_name} was flagged: {reason.strip()}",
               event_type="qa_flag", link=f"/qa/{review.id}")
    db.flush()
    return review


def reject_review(db: Session, review: QAReview, user: User, reason: str, request=None) -> QAReview:
    if not (reason or "").strip():
        raise ValueError("A reason is required to reject a review.")
    review.status = "rejected"
    review.reviewed_at = review.reviewed_at or datetime.utcnow()
    review.remarks = ((review.remarks or "") + f"\n[Rejected] {reason.strip()}").strip()
    call = _call_of(db, review)
    if call:
        call.review_state = "reviewed"
    log_action(db, user, "update", "qa", entity=review, description=f"QA call review #{review.id} rejected (not reviewable)",
               rationale=reason, request=request)
    db.flush()
    return review


# ----------------------------------------------------------------------------- reporting
def _range(dfrom: Optional[date], dto: Optional[date]) -> tuple[date, date]:
    dto = dto or date.today()
    dfrom = dfrom or (dto - timedelta(days=30))
    return dfrom, dto


def _reviews_in(db: Session, dfrom: date, dto: date, teacher_ids: Optional[list[int]] = None):
    q = db.query(QAReview).filter(QAReview.sample_type == "call",
                                  QAReview.created_at >= datetime.combine(dfrom, datetime.min.time()),
                                  QAReview.created_at <= datetime.combine(dto, datetime.max.time()))
    if teacher_ids is not None:
        q = q.filter(QAReview.teacher_id.in_(teacher_ids or [-1]))
    return q


def teacher_qa_performance(db: Session, dfrom: Optional[date], dto: Optional[date], teacher_id: Optional[int] = None,
                           teacher_ids: Optional[list[int]] = None) -> list[dict]:
    """Per-teacher rows: Calls, Reviewed, Avg Rating, Issues, Critical Issues, Score %."""
    dfrom, dto = _range(dfrom, dto)
    calls_q = db.query(CallRecord.teacher_id, func.count(CallRecord.id)).filter(
        CallRecord.recording_date >= dfrom, CallRecord.recording_date <= dto, CallRecord.teacher_id.isnot(None))
    if teacher_id:
        calls_q = calls_q.filter(CallRecord.teacher_id == teacher_id)
    if teacher_ids is not None:
        calls_q = calls_q.filter(CallRecord.teacher_id.in_(teacher_ids or [-1]))
    calls = dict(calls_q.group_by(CallRecord.teacher_id).all())
    rq = _reviews_in(db, dfrom, dto, teacher_ids).filter(QAReview.status.in_(["completed", "approved", "flagged"]))
    if teacher_id:
        rq = rq.filter(QAReview.teacher_id == teacher_id)
    per: dict[int, dict] = {}
    for r in rq.all():
        d = per.setdefault(r.teacher_id, {"reviewed": 0, "ratings": [], "issues": 0, "critical": 0})
        d["reviewed"] += 1
        if r.overall_rating is not None:
            d["ratings"].append(float(r.overall_rating))
        for i in (r.issues or []):
            d["issues"] += 1
            d["critical"] += 1 if i.get("critical") else 0
    ids = set(calls) | set(per)
    if teacher_id:
        ids.add(teacher_id)
    teachers = {t.id: t for t in db.query(Teacher).filter(Teacher.id.in_(ids or [-1])).all()}
    rows = []
    for tid in ids:
        t = teachers.get(tid)
        if not t:
            continue
        d = per.get(tid, {"reviewed": 0, "ratings": [], "issues": 0, "critical": 0})
        avg = round(sum(d["ratings"]) / len(d["ratings"]), 2) if d["ratings"] else None
        score = max(0.0, round((avg / 5.0 * 100.0) - 5.0 * d["critical"], 1)) if avg is not None else None
        rows.append({"teacher": t, "calls": calls.get(tid, 0), "reviewed": d["reviewed"], "avg_rating": avg,
                     "issues": d["issues"], "critical": d["critical"], "score_pct": score})
    rows.sort(key=lambda x: (-(x["score_pct"] if x["score_pct"] is not None else -1), x["teacher"].full_name))
    return rows


def qa_dashboard(db: Session, dfrom: Optional[date], dto: Optional[date], teacher_ids: Optional[list[int]] = None) -> dict:
    dfrom, dto = _range(dfrom, dto)
    cq = db.query(CallRecord).filter(CallRecord.recording_date >= dfrom, CallRecord.recording_date <= dto)
    if teacher_ids is not None:
        cq = cq.filter(CallRecord.teacher_id.in_(teacher_ids or [-1]))
    calls = cq.all()
    total = len(calls)
    unmatched_agent = sum(1 for c in calls if c.source == "AGENT" and c.session_id is None)
    zoom = sum(1 for c in calls if c.source == "ZOOM")
    mapped = sum(1 for c in calls if c.session_id is not None)
    pending = sum(1 for c in calls if c.review_state in ("mapped", "queued", "in_review"))
    reviewed = sum(1 for c in calls if c.review_state == "reviewed")
    by_source = {s: sum(1 for c in calls if c.source == s) for s in CALL_SOURCES}

    reviews = _reviews_in(db, dfrom, dto, teacher_ids).all()
    rated = [r for r in reviews if r.overall_rating is not None and r.status in ("completed", "approved")]
    avg_rating = round(sum(float(r.overall_rating) for r in rated) / len(rated), 2) if rated else 0.0
    status_counts = {k: 0 for k in CALL_REVIEW_STATUSES}
    for r in reviews:
        key = "completed" if r.status == "approved" else r.status
        status_counts[key] = status_counts.get(key, 0) + 1

    issue_counts: dict[str, int] = {}
    for r in reviews:
        for i in (r.issues or []):
            name = i.get("type") or "Other"
            issue_counts[name] = issue_counts.get(name, 0) + 1
    issue_breakdown = sorted(issue_counts.items(), key=lambda x: -x[1])

    lowest = sorted(rated, key=lambda r: (float(r.overall_rating), r.id))[:5]

    per_teacher: dict[int, list[float]] = {}
    pivot: dict[int, dict[str, list[float]]] = {}
    for r in rated:
        per_teacher.setdefault(r.teacher_id, []).append(float(r.overall_rating))
        for name, v in (r.parameter_scores or {}).items():
            pivot.setdefault(r.teacher_id, {}).setdefault(name, []).append(float(v))
    teachers = {t.id: t for t in db.query(Teacher).filter(Teacher.id.in_(list(set(per_teacher) | set(pivot)) or [-1])).all()}
    perf = sorted(((teachers[tid], round(sum(v) / len(v), 2), len(v)) for tid, v in per_teacher.items() if tid in teachers), key=lambda x: -x[1])
    params = [p.name for p in active_parameters(db)]
    extra = sorted({n for d in pivot.values() for n in d} - set(params))
    param_names = params + extra
    pivot_rows = []
    for tid, d in pivot.items():
        t = teachers.get(tid)
        if not t:
            continue
        pivot_rows.append({"teacher": t, "cells": [round(sum(d[n]) / len(d[n]), 1) if d.get(n) else None for n in param_names],
                           "avg": round(sum(per_teacher.get(tid, [0])) / max(1, len(per_teacher.get(tid, []))), 2)})
    pivot_rows.sort(key=lambda x: x["teacher"].full_name)

    def pct(n: int) -> float:
        return round(100.0 * n / total, 1) if total else 0.0

    return {"from": dfrom, "to": dto, "total_calls": total, "unmatched_agent": unmatched_agent, "zoom_calls": zoom, "avg_rating": avg_rating,
            "by_source": by_source, "mapped": mapped, "pending": pending, "reviewed": reviewed,
            "mapped_pct": pct(mapped), "pending_pct": pct(pending), "reviewed_pct": pct(reviewed),
            "issue_breakdown": issue_breakdown, "lowest": lowest, "teacher_performance": perf,
            "top": perf[:5], "bottom": list(reversed(perf[-5:])) if len(perf) > 5 else list(reversed(perf)),
            "status_counts": status_counts, "total_reviews": len(reviews), "param_names": param_names, "pivot": pivot_rows}
