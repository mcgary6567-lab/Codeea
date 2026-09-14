"""ERP quality-management parity seed (docs/AUDIT_ACADEMICS.md 3.9).

Fills the call pipeline the ERP's Quality Management pages read:

* ~120 ``CallRecord`` rows over 45 days across AGENT / TEAMS / ZOOM, about 80% mapped onto done class
  sessions, ~15 unmatched AGENT calls and a spread of queued / in_review / reviewed states
* ~35 call ``QAReview`` rows with parameter scores, issues, an overall 1-5 rating, remarks and a reviewed
  timestamp, statuses spread over completed (the majority), in_review, queued, flagged and rejected
* ``feedback_source`` on the existing Feedback rows plus ~30 more rated responses over 60 days
* Evaluations gain ``is_manual`` on about a third, an ``assessment_id`` where an assessment exists, a due
  date on a few and a few Fail results
* QA Review Parameters / QA Issue Types only when the configuration seed has not already created them

Idempotent: each block checks its own target count before inserting.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.academic import Evaluation
from app.models.core import User
from app.models.crm import Feedback
from app.models.erp import AssessmentDefinition, CallRecord, QAIssueType, QAReviewParameter
from app.models.people import Teacher
from app.models.scheduling import ClassSession, QAReview

SEED = 20260914
TARGET_CALLS = 120
TARGET_REVIEWS = 35
TARGET_FEEDBACK = 30
UNMATCHED_AGENT_CALLS = 15

PARAMETERS = [("Engagement", "Student kept attentive and participating throughout the class", 1.0),
              ("Tajweed Accuracy", "Teacher's own recitation and correction of the student's Tajweed", 1.5),
              ("Adab", "Islamic manners, greeting, patience and respectful tone", 1.0),
              ("Lesson Planning", "Sabaq / sabqi / dor covered as planned and recorded", 1.0),
              ("Punctuality", "Joined on time and used the full session", 1.0),
              ("Environment", "Quiet background, camera on, proper dress and lighting", 0.5)]
ISSUE_TYPES = [("Engagement", "normal"), ("Tajweed Accuracy", "normal"), ("Adab", "normal"),
               ("Lesson Planning", "normal"), ("Late Join", "critical"), ("Missed Class", "critical"),
               ("Misconduct", "critical")]

PLATFORMS = {"AGENT": "Cloud Agent Dialer", "TEAMS": "Microsoft Teams", "ZOOM": "Zoom Meetings"}
REVIEW_STATUS_PLAN = (["completed"] * 20) + ["in_review"] * 5 + ["queued"] * 4 + ["flagged"] * 4 + ["rejected"] * 2
REMARKS = {
    "completed": ["Warm opening, clear Tajweed correction and the sabaq recorded before the call ended.",
                  "Lesson plan followed; student engaged throughout and homework confirmed with the parent.",
                  "Good adab and pace; recommend more repetition drills on the makharij.",
                  "Solid class. Camera on, quiet room, full thirty minutes used."],
    "in_review": ["Listening to the second half before scoring the Tajweed parameter."],
    "queued": ["Waiting in the review queue."],
    "flagged": ["Teacher joined six minutes late and the activity was not recorded - escalated to the supervisor.",
                "Background noise for most of the call; environment score pulled the review down."],
    "rejected": ["Recording is only two minutes long - not reviewable, asked the agent to re-sync.",
                 "Wrong class mapped to this recording; rejected and returned to the queue."],
}
FEEDBACK_SOURCES = ["manual", "app", "web_portal", "client_portal"]
FEEDBACK_COMMENTS = {
    5: ["Ustadh is excellent - my son looks forward to every class.",
        "Very punctual and patient, we can already hear the difference in recitation."],
    4: ["Happy with the progress; would like a little more homework.",
        "Good classes, occasionally starts a minute or two late."],
    3: ["Classes are fine but we would like a clearer monthly plan.",
        "Average month - two classes were rescheduled."],
    2: ["Too many missed classes this month, please look into it.",
        "Connection problems kept interrupting the lesson."],
    1: ["Teacher did not join twice this week and nobody informed us."],
}


def _user(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


def _seed_config(db: Session) -> tuple[int, int]:
    """Only when the configuration seed has not already written them."""
    params = issues = 0
    if not db.query(QAReviewParameter).count():
        for i, (name, desc, weight) in enumerate(PARAMETERS, start=1):
            db.add(QAReviewParameter(name=name, description=desc, weight=weight, max_rating=5, sort_no=i,
                                     status="active"))
            params += 1
    if not db.query(QAIssueType).count():
        for name, severity in ISSUE_TYPES:
            db.add(QAIssueType(name=name, severity=severity, status="active",
                               description=f"{'Critical' if severity == 'critical' else 'Coaching'} issue: {name.lower()}"))
            issues += 1
    db.flush()
    return params, issues


def _seed_calls(db: Session, rnd: random.Random) -> int:
    existing = db.query(CallRecord).count()
    if existing >= TARGET_CALLS:
        return 0
    cutoff = date.today() - timedelta(days=45)
    done = (db.query(ClassSession)
            .filter(ClassSession.status == "done", ClassSession.date >= cutoff)
            .order_by(ClassSession.date.desc(), ClassSession.id).all())
    teachers = db.query(Teacher).order_by(Teacher.id).all()
    if not done and not teachers:
        return 0
    used = {c.session_id for c in db.query(CallRecord).all() if c.session_id}
    pool = [s for s in done if s.id not in used]
    rnd.shuffle(pool)

    created = 0
    want = TARGET_CALLS - existing
    mapped_target = max(0, want - UNMATCHED_AGENT_CALLS)
    for n in range(want):
        mapped = n < mapped_target and n < len(pool)
        session = pool[n] if mapped else None
        source = "AGENT" if not mapped else ("TEAMS" if n % 3 == 0 else "ZOOM")
        if mapped and n % 7 == 0:
            source = "AGENT"
        when = session.date if session else (date.today() - timedelta(days=rnd.randint(0, 45)))
        teacher = session.teacher if session else (teachers[n % len(teachers)] if teachers else None)
        start_hour = session.start_time.hour if session else rnd.randint(7, 22)
        start_min = session.start_time.minute if session else rnd.choice([0, 30])
        start = datetime.combine(when, datetime.min.time()).replace(hour=start_hour, minute=start_min)
        duration = (session.actual_duration_minutes or session.duration_minutes or 30) if session \
            else rnd.choice([3, 8, 12, 27, 30])
        if mapped:
            state = "reviewed" if n % 5 == 0 else ("in_review" if n % 5 == 1 else ("queued" if n % 5 == 2 else "mapped"))
        else:
            state = "unmapped"
        db.add(CallRecord(
            source=source, platform=PLATFORMS[source],
            source_name=(f"{teacher.full_name} - class call" if teacher and mapped
                         else f"Agent call {rnd.randint(100, 999)}"),
            external_id=f"{source[:2]}-{when:%Y%m%d}-{rnd.randint(1000, 9999)}",
            recording_date=when,
            employee_id=(teacher.employee_id if teacher else None),
            teacher_id=(teacher.id if teacher else None),
            session_id=(session.id if session else None),
            start_time=start, end_time=start + timedelta(minutes=duration),
            duration_minutes=duration,
            meeting_status="ended" if duration >= 3 else "missed",
            recording_url=f"https://recordings.quran-college.org/{source.lower()}/{when:%Y%m%d}/{rnd.randint(10000, 99999)}.mp4",
            review_state=state))
        created += 1
    db.flush()
    return created


def _seed_reviews(db: Session, rnd: random.Random, reviewer: User | None) -> int:
    existing = db.query(QAReview).filter(QAReview.sample_type == "call").count()
    if existing >= TARGET_REVIEWS:
        return 0
    params = [p.name for p in db.query(QAReviewParameter).order_by(QAReviewParameter.sort_no).all()] \
        or [p[0] for p in PARAMETERS]
    issue_types = db.query(QAIssueType).all()
    reviewed_ids = {r.call_record_id for r in db.query(QAReview).all() if r.call_record_id}
    calls = [c for c in db.query(CallRecord).filter(CallRecord.session_id.isnot(None))
             .order_by(CallRecord.recording_date.desc()).all() if c.id not in reviewed_ids and c.teacher_id]
    if not calls:
        return 0

    created = 0
    for n in range(min(TARGET_REVIEWS - existing, len(calls))):
        call = calls[n]
        status = REVIEW_STATUS_PLAN[n % len(REVIEW_STATUS_PLAN)]
        low = status in ("flagged", "rejected")
        scores = {name: (rnd.randint(2, 3) if low else rnd.randint(3, 5)) for name in params}
        overall = round(sum(scores.values()) / len(scores), 1) if scores else 3.0
        issues = []
        if low or n % 4 == 0:
            picks = rnd.sample(issue_types, k=min(2, len(issue_types))) if issue_types else []
            for it in picks:
                issues.append({"type": it.name, "critical": it.severity == "critical",
                               "note": f"{it.name} observed during the call."})
        scored = status in ("completed", "flagged", "rejected", "in_review")
        reviewed_at = (datetime.combine(call.recording_date, datetime.min.time())
                       + timedelta(days=rnd.randint(0, 3), hours=rnd.randint(1, 8))) if scored else None
        review = QAReview(
            session_id=call.session_id, teacher_id=call.teacher_id,
            reviewer_id=reviewer.id if reviewer else None, sample_type="call", status=status,
            call_record_id=call.id, overall_rating=(overall if scored else None),
            remarks=rnd.choice(REMARKS[status]), parameter_scores=scores if scored else {},
            issues=issues, reviewed_at=reviewed_at,
            engagement_score=scores.get("Engagement"), tajweed_score=scores.get("Tajweed Accuracy"),
            punctuality_score=scores.get("Punctuality"), environment_score=scores.get("Environment"),
            methodology_score=scores.get("Lesson Planning"), professionalism_score=scores.get("Adab"),
            overall_score=(round(overall * 20, 1) if scored else None),
            completed_at=(reviewed_at if status == "completed" else None))
        db.add(review)
        db.flush()
        call.qa_review_id = review.id
        call.review_state = {"completed": "reviewed", "flagged": "reviewed", "rejected": "queued",
                             "in_review": "in_review", "queued": "queued"}[status]
        created += 1
    db.flush()
    return created


def _seed_feedback(db: Session, rnd: random.Random) -> tuple[int, int]:
    updated = 0
    rows = db.query(Feedback).order_by(Feedback.id).all()
    # "client_portal" is the column default, so the first run spreads it across the four ERP sources.
    # Once more than one source exists the spread has run and only blanks are filled (keeps re-runs no-ops).
    spread_done = len({fb.feedback_source for fb in rows if fb.feedback_source}) > 1
    for i, fb in enumerate(rows):
        if not fb.feedback_source or (not spread_done and fb.feedback_source == "client_portal"):
            fb.feedback_source = FEEDBACK_SOURCES[i % len(FEEDBACK_SOURCES)]
            updated += 1
    db.flush()

    created = 0
    existing_manual = db.query(Feedback).filter(Feedback.trigger == "erp_seed").count()
    if existing_manual >= TARGET_FEEDBACK:
        return updated, 0
    cutoff = date.today() - timedelta(days=60)
    sessions = (db.query(ClassSession)
                .filter(ClassSession.status == "done", ClassSession.date >= cutoff)
                .order_by(ClassSession.date.desc()).limit(400).all())
    if not sessions:
        return updated, 0
    rnd.shuffle(sessions)
    weights = [5, 5, 5, 4, 4, 3, 2, 1]
    for n in range(TARGET_FEEDBACK - existing_manual):
        sess = sessions[n % len(sessions)]
        student = sess.student
        if student is None:
            continue
        rating = weights[n % len(weights)]
        when = datetime.combine(sess.date, datetime.min.time()) + timedelta(hours=rnd.randint(1, 20))
        db.add(Feedback(
            trigger="erp_seed", respondent_type="client", client_id=student.client_id, student_id=student.id,
            teacher_id=sess.teacher_id, rating=rating, nps=min(10, rating * 2),
            comment=rnd.choice(FEEDBACK_COMMENTS[rating]),
            sentiment=("positive" if rating >= 4 else ("neutral" if rating == 3 else "negative")),
            is_negative=rating <= 2, status="submitted", submitted_at=when, sent_at=when,
            feedback_source=FEEDBACK_SOURCES[n % len(FEEDBACK_SOURCES)], session_id=sess.id))
        created += 1
    db.flush()
    return updated, created


def _seed_evaluations(db: Session, rnd: random.Random) -> int:
    evaluations = db.query(Evaluation).order_by(Evaluation.id).all()
    if not evaluations:
        return 0
    assessments = db.query(AssessmentDefinition).filter(AssessmentDefinition.status == "active").all()
    touched = 0
    for i, ev in enumerate(evaluations):
        changed = False
        if i % 3 == 0 and not ev.is_manual:
            ev.is_manual = True
            ev.evaluation_type = ev.evaluation_type or "manual"
            changed = True
        if assessments and ev.assessment_id is None:
            ev.assessment_id = assessments[i % len(assessments)].id
            changed = True
        if i % 9 == 0 and ev.due_date is None:
            ev.due_date = (ev.date or date.today()) + timedelta(days=30)
            changed = True
        if i % 11 == 0 and ev.result != "fail" and ev.score is not None:
            ev.result = "fail"
            ev.score = round(min(float(ev.score or 0), float(ev.max_score or 100) * 0.45), 1)
            ev.academic_comment = (ev.academic_comment
                                   or "Below the passing mark; a revision plan was agreed with the teacher.")
            changed = True
        if changed:
            touched += 1
    db.flush()
    return touched


def run(db: Session) -> None:
    rnd = random.Random(SEED)
    reviewer = _user(db, "qa@oqc.local") or _user(db, "qaofficer@oqc.local") or _user(db, "admin@oqc.local")

    params, issues = _seed_config(db)
    calls = _seed_calls(db, rnd)
    reviews = _seed_reviews(db, rnd, reviewer)
    fb_updated, fb_created = _seed_feedback(db, rnd)
    evaluations = _seed_evaluations(db, rnd)
    db.flush()
    if params or issues:
        print(f"    QA configuration: {params} parameters, {issues} issue types")
    print(f"    call records: {calls} created (total {db.query(CallRecord).count()})")
    print(f"    call reviews: {reviews} created "
          f"(total {db.query(QAReview).filter(QAReview.sample_type == 'call').count()})")
    print(f"    feedback: {fb_updated} sources set, {fb_created} new responses")
    print(f"    evaluations: {evaluations} updated")
