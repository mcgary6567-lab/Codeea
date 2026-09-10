"""Scheduling / live operations seed (Modules 8, 15, 17, 24, 25, 47).

Creates shifts, recurring schedules for every active/trial student, 60 days of class-session history plus
14 days ahead, attendance, recordings with AI analyses, QA reviews with corrective actions, safeguarding
flags, risk alerts, student leaves, trials, call logs and reminder logs.

Idempotent: re-running only tops up what is missing. Deterministic (fixed random seed).
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models.core import RiskAlert, User
from app.models.crm import Lead
from app.models.people import Leave, Student, Teacher
from app.models.scheduling import (Attendance, CallLog, ClassSession, CorrectiveAction, QAReview, Recording,
                                   RecordingAccessLog, ReminderLog, SafeguardingFlag, Schedule, Shift, Trial)
from app.services import classes as class_svc
from app.services import qa as qa_svc

rnd = random.Random(20240917)

HISTORY_DAYS = 60
FUTURE_DAYS = 14

SHIFTS = [  # name, group, start, end, manager email, supervisor email
    ("Morning Shift", "morning", time(6, 0), time(14, 0), "manager@oqc.local", "supervisor@oqc.local"),
    ("Evening Shift", "morning", time(14, 0), time(22, 0), "manager@oqc.local", "supervisor@oqc.local"),
    ("Night Shift", "night", time(20, 0), time(4, 0), "manager2@oqc.local", "supervisor2@oqc.local"),
]

# half-hour slot windows (org time) usable for each teacher shift, inside the 15:00-23:00 teaching band
SHIFT_BAND = {"morning": (15, 18), "evening": (15, 22), "night": (20, 23)}

DAY_PATTERNS = [[0, 2, 4], [1, 3, 5], [0, 1, 2, 3, 4], [0, 2, 4], [0, 1, 2, 3, 4], [1, 3, 5]]

LESSON_NOTES = [
    "Sabaq recited fluently; makhaarij of qaf and 'ain corrected twice.",
    "Good revision of sabqi. Madd rules need more practice next class.",
    "Student was tired; covered dor only and set light homework.",
    "Excellent progress, moved to the next lesson ahead of plan.",
    "Ghunnah and ikhfa applied correctly throughout the recitation.",
    "Parent joined for the last five minutes to hear the progress report.",
]

CANCEL_REASONS = ["Parent requested cancellation (family event)", "Teacher internet outage", "Public holiday in student country",
                  "Rescheduled at parent request", "Power outage at teacher location"]
ABSENT_REASONS = ["Student did not join; parent informed later", "Student unwell", "Student at school event", "No response from student"]


def _get_shifts(db: Session) -> dict[str, Shift]:
    users = {u.email: u for u in db.query(User).filter(User.email.in_([s[4] for s in SHIFTS] + [s[5] for s in SHIFTS]))}
    out: dict[str, Shift] = {}
    for name, group, st, en, mgr, sup in SHIFTS:
        sh = db.query(Shift).filter(Shift.name == name).first()
        if not sh:
            sh = Shift(name=name, group=group, start_time=st, end_time=en,
                       manager_id=users[mgr].id if mgr in users else None,
                       supervisor_id=users[sup].id if sup in users else None, is_active=True)
            db.add(sh)
            db.flush()
        out[name.split()[0].lower()] = sh
    return out


def _pick_slot(db: Session, student: Student, teacher: Teacher, days: list[int]) -> time | None:
    lo, hi = SHIFT_BAND.get(teacher.shift, (15, 22))
    candidates = [time(h, m) for h in range(lo, hi) for m in (0, 30)]
    rnd.shuffle(candidates)
    for st in candidates:
        if not class_svc.has_conflict(db, teacher.id, days, st, 30, student_id=student.id):
            return st
    return None


def _build_schedules(db: Session, shifts: dict[str, Shift]) -> list[Schedule]:
    today = date.today()
    created: list[Schedule] = []
    students = (db.query(Student).filter(Student.status.in_(["active", "trial"]), Student.teacher_id.isnot(None))
                .order_by(Student.id).all())
    shift_by_group = {"morning": shifts.get("morning"), "evening": shifts.get("evening"), "night": shifts.get("night")}
    for i, s in enumerate(students):
        if db.query(Schedule).filter(Schedule.student_id == s.id).first():
            continue
        teacher = db.get(Teacher, s.teacher_id)
        if not teacher or not teacher.is_verified:
            continue
        days = DAY_PATTERNS[i % len(DAY_PATTERNS)]
        st = _pick_slot(db, s, teacher, days)
        if st is None:
            continue
        shift = shift_by_group.get(teacher.shift)
        start_date = max(s.join_date or today, today - timedelta(days=HISTORY_DAYS))
        sch = Schedule(student_id=s.id, teacher_id=teacher.id, course_id=s.course_id, days_of_week=days, start_time=st,
                       duration_minutes=30, timezone="Asia/Karachi", start_date=start_date, shift_id=shift.id if shift else None,
                       status="active", is_trial=(s.status == "trial"),
                       notes=f"{len(days)} classes per week - {s.level or 'foundation'} level.")
        sch.room_name = class_svc.room_name_for(sch)
        db.add(sch)
        db.flush()
        created.append(sch)
    return created


def _materialise(db: Session, schedules: list[Schedule]) -> int:
    today = date.today()
    n = 0
    for sch in schedules:
        n += len(class_svc.generate_sessions(db, sch, today - timedelta(days=HISTORY_DAYS), today + timedelta(days=FUTURE_DAYS)))
    return n


def _apply_history(db: Session) -> None:
    """Give past sessions realistic terminal statuses, join timestamps and attendance rows."""
    today = date.today()
    now = datetime.utcnow() + timedelta(hours=5)  # org time (Asia/Karachi)
    sessions = (db.query(ClassSession).filter(ClassSession.status == "pending", ClassSession.date <= today)
                .order_by(ClassSession.scheduled_start).all())
    att_rows = []
    for s in sessions:
        if s.date == today and s.scheduled_start > now - timedelta(minutes=20):
            if s.scheduled_start <= now + timedelta(minutes=10):
                # class in progress
                s.status = "started"
                s.teacher_joined_at = s.scheduled_start + timedelta(minutes=rnd.randint(0, 4))
                s.student_joined_at = s.teacher_joined_at + timedelta(minutes=rnd.randint(0, 3))
                s.teacher_late_minutes = max(0, int((s.teacher_joined_at - s.scheduled_start).total_seconds() // 60))
                s.status_changed_at = s.teacher_joined_at
            continue  # future / imminent -> stays pending
        roll = rnd.random()
        if roll < 0.86:
            status = "done"
        elif roll < 0.91:
            status = "missed"
        elif roll < 0.97:
            status = "absent"
        elif roll < 0.985:
            status = "cancelled"
        else:
            status = "rescheduled"
        s.status = status
        s.status_changed_at = s.scheduled_start + timedelta(minutes=35)
        if status == "done":
            late = rnd.choices([0, 0, 1, 2, 3, 5, 8, 12], weights=[30, 22, 14, 12, 9, 7, 4, 2])[0]
            s.teacher_joined_at = s.scheduled_start + timedelta(minutes=late)
            s.student_joined_at = s.teacher_joined_at + timedelta(minutes=rnd.randint(0, 4))
            s.teacher_late_minutes = late
            actual = max(18, min(s.duration_minutes + 4, s.duration_minutes - rnd.randint(-4, 6)))
            s.teacher_left_at = s.teacher_joined_at + timedelta(minutes=actual)
            s.student_left_at = s.teacher_left_at
            s.actual_duration_minutes = actual
            s.teacher_notes = rnd.choice(LESSON_NOTES)
            if rnd.random() < 0.18:
                s.student_rating = rnd.choice([4, 5, 5, 5, 3])
        elif status == "missed":
            s.status_reason = "Teacher did not start the class within the grace period"
        elif status == "absent":
            s.teacher_joined_at = s.scheduled_start + timedelta(minutes=rnd.randint(0, 3))
            s.teacher_left_at = s.teacher_joined_at + timedelta(minutes=10)
            s.status_reason = rnd.choice(ABSENT_REASONS)
        elif status == "cancelled":
            s.status_reason = rnd.choice(CANCEL_REASONS)
        elif status == "rescheduled":
            s.status_reason = "Rescheduled at parent request"
        if status in ("done", "missed", "absent"):
            att_rows.append(Attendance(
                session_id=s.id, student_id=s.student_id, teacher_id=s.teacher_id, date=s.date,
                student_status={"done": "present", "missed": "present", "absent": "absent"}[status],
                teacher_status={"done": "late" if s.teacher_late_minutes > 5 else "present", "missed": "absent", "absent": "present"}[status],
                remarks=s.status_reason))
    db.add_all(att_rows)
    db.flush()


def _rescheduled_links(db: Session) -> None:
    """Create the follow-up session for every 'rescheduled' record so the chain is navigable."""
    rows = db.query(ClassSession).filter(ClassSession.status == "rescheduled", ClassSession.rescheduled_to_id.is_(None)).all()
    for s in rows[:60]:
        new_start = s.scheduled_start + timedelta(days=rnd.randint(1, 3))
        new = ClassSession(schedule_id=s.schedule_id, student_id=s.student_id, teacher_id=s.teacher_id, course_id=s.course_id,
                           date=new_start.date(), start_time=new_start.time(),
                           end_time=(new_start + timedelta(minutes=s.duration_minutes)).time(), scheduled_start=new_start,
                           duration_minutes=s.duration_minutes, status="done" if new_start.date() < date.today() else "pending",
                           is_trial=s.is_trial, room_name=s.room_name, join_url=s.join_url,
                           status_reason=f"Rescheduled from {s.date} {s.start_time.strftime('%H:%M')}")
        if new.status == "done":
            new.teacher_joined_at = new_start
            new.student_joined_at = new_start + timedelta(minutes=1)
            new.teacher_left_at = new_start + timedelta(minutes=s.duration_minutes)
            new.actual_duration_minutes = s.duration_minutes
        db.add(new)
        db.flush()
        s.rescheduled_to_id = new.id
    db.flush()


def _leaves(db: Session) -> None:
    if db.query(Leave).filter(Leave.person_type == "student").count() >= 6:
        return
    today = date.today()
    students = db.query(Student).filter(Student.status == "active").order_by(Student.id).all()
    if not students:
        return
    approver = db.query(User).filter(User.email == "manager@oqc.local").first()
    specs = [(-42, -37, "vacation"), (-30, -26, "sick"), (-18, -15, "exam"), (-6, -3, "emergency"), (1, 4, "vacation"), (6, 9, "sick")]
    for i, (a, b, kind) in enumerate(specs):
        st = students[(i * 5) % len(students)]
        start, end = today + timedelta(days=a), today + timedelta(days=b)
        lv = Leave(person_type="student", student_id=st.id, leave_type=kind, start_date=start, end_date=end,
                   reason=f"Family {kind} - parent informed the supervisor in advance.", status="approved",
                   approved_by_id=approver.id if approver else None, approved_at=datetime.utcnow() - timedelta(days=max(1, -a)),
                   requested_by_id=st.client.user_id if st.client else None)
        db.add(lv)
        db.flush()
        for s in db.query(ClassSession).filter(ClassSession.student_id == st.id, ClassSession.date >= start,
                                               ClassSession.date <= end).all():
            s.status = "leave"
            s.status_reason = f"Approved {kind} leave #{lv.id}"
            s.status_changed_by_id = approver.id if approver else None
            s.status_changed_at = datetime.utcnow()
            if not db.query(Attendance).filter(Attendance.session_id == s.id).first():
                db.add(Attendance(session_id=s.id, student_id=s.student_id, teacher_id=s.teacher_id, date=s.date,
                                  student_status="leave", teacher_status="present"))
        # demonstrate the post-leave absence signal on the oldest leave
        if i == 0:
            nxt = (db.query(ClassSession).filter(ClassSession.student_id == st.id, ClassSession.date > end)
                   .order_by(ClassSession.scheduled_start).first())
            if nxt and nxt.date < today:
                nxt.status = "absent"
                nxt.status_reason = "Student did not return after leave"
    db.flush()


def _recordings_and_ai(db: Session) -> None:
    today = date.today()
    since = today - timedelta(days=14)
    done = (db.query(ClassSession).filter(ClassSession.status == "done", ClassSession.date >= since, ClassSession.date < today)
            .order_by(ClassSession.id).all())
    targets = [s for i, s in enumerate(done) if i % 5 in (0, 1)]  # ~40%
    made = 0
    for s in targets:
        if s.recording:
            continue
        try:
            qa_svc.ingest_recording(db, s, None, source=rnd.choice(["platform", "platform", "zoom"]))
        except ValueError:
            continue
        qa_svc.run_ai_analysis(db, s)
        made += 1
        if made % 40 == 0:
            db.flush()
    db.flush()
    # guarantee at least two anti-poaching demonstrations for the safeguarding module
    from app.models.scheduling import AIClassAnalysis
    flagged = db.query(AIClassAnalysis).filter(AIClassAnalysis.contact_exchange_detected.is_(True)).count()
    if flagged < 2:
        pool = db.query(AIClassAnalysis).order_by(AIClassAnalysis.id).limit(60).all()
        for an in pool[:2 - flagged] if pool else []:
            an.contact_exchange_detected = True
            an.risk_level = "high"
            an.summary = (an.summary or "") + " Possible exchange of personal contact details detected in the audio."
            sess = an.session
            db.add(SafeguardingFlag(flag_type="contact_exchange", severity="high", session_id=an.session_id,
                                    teacher_id=an.teacher_id, student_id=sess.student_id if sess else None,
                                    evidence=(f"AI analysis of session #{an.session_id}: phrases suggesting an off-platform contact "
                                              f"exchange were detected. Confidence {an.confidence:.2f}. Human review required before any action."),
                                    source="ai", ai_run_id=an.model_run_id, status="open", visibility="ceo_only"))
            db.add(RiskAlert(alert_type="safeguarding_signal", severity="high",
                             title=f"Safeguarding signal (contact exchange) - {an.teacher.full_name if an.teacher else an.teacher_id}",
                             message=f"AI flagged session #{an.session_id}. Review in Safeguarding before any action.",
                             entity_type="ClassSession", entity_id=an.session_id, visibility="ceo_only", status="open", source="ai"))
    db.flush()


def _qa_reviews(db: Session) -> None:
    if db.query(QAReview).count() >= 20:
        return
    reviewer = db.query(User).filter(User.email == "qaofficer@oqc.local").first()
    hod = db.query(User).filter(User.email == "qa@oqc.local").first()
    from app.models.scheduling import AIClassAnalysis
    analysed = (db.query(ClassSession).join(AIClassAnalysis, AIClassAnalysis.session_id == ClassSession.id)
                .order_by(ClassSession.date.desc()).limit(40).all())
    plain = (db.query(ClassSession).filter(ClassSession.status == "done", ClassSession.date >= date.today() - timedelta(days=20))
             .order_by(ClassSession.id.desc()).limit(60).all())
    pool = analysed + [s for s in plain if s not in analysed]
    seen: set[int] = set()
    made = 0
    for s in pool:
        if made >= 25 or s.id in seen:
            continue
        seen.add(s.id)
        an = s.ai_analysis
        sample = "risk_based" if (an and an.risk_level in ("medium", "high")) else "random"
        review = qa_svc.queue_qa_review(db, s, sample, reviewer=reviewer if made % 5 != 4 else None, ai_analysis=an)
        made += 1
        if made % 5 == 4:
            continue  # leave queued
        if made % 5 == 3:
            review.status = "in_review"
            continue
        base = (an.overall_score / 10.0) if an else rnd.uniform(6.0, 9.2)
        scores = {k: max(2.0, min(10.0, round(base + rnd.uniform(-1.4, 1.2), 1))) for k, _ in qa_svc.QA_CRITERIA}
        qa_svc.complete_qa_review(db, review, scores, reviewer or hod,
                                  strengths="Clear makhaarij correction, calm tone, good use of repetition.",
                                  weaknesses="Lesson-plan coverage and time management can improve." if base < 8.5 else "Minor pacing improvements only.",
                                  comments="Sampled through the QA queue. Feedback shared with the teacher and supervisor.")
        review.completed_at = datetime.utcnow() - timedelta(days=rnd.randint(0, 25))
        if review.overall_score is not None and review.overall_score < qa_svc.APPROVAL_THRESHOLD and hod and made % 2 == 0:
            qa_svc.approve_qa_review(db, review, hod, note="Reviewed with the supervisor; coaching plan agreed.")
        if made % 6 == 0:
            qa_svc.add_corrective_action(db, review, review.teacher_id,
                                         rnd.choice(["Complete the Ustaadh Lab module on lesson pacing.",
                                                     "Submit three recorded classes for punctuality follow-up.",
                                                     "Attend the Tajweed refresher clinic with the HOD Academics."]),
                                         date.today() + timedelta(days=rnd.choice([-6, 5, 12, 20])), reviewer or hod)
        if made % 4 == 0:
            qa_svc.send_qa_feedback(db, review, reviewer or hod)
    # one overdue corrective action for the board
    qa_svc.refresh_overdue_actions(db)
    db.flush()


def _alerts_and_flags(db: Session) -> None:
    today = date.today()
    if db.query(RiskAlert).filter(RiskAlert.alert_type == "missed_class").count() == 0:
        for s in (db.query(ClassSession).filter(ClassSession.status == "missed", ClassSession.date >= today - timedelta(days=7))
                  .order_by(ClassSession.date.desc()).limit(12).all()):
            created = datetime.utcnow() - timedelta(days=(today - s.date).days, minutes=rnd.randint(0, 300))
            a = RiskAlert(alert_type="missed_class", severity="high",
                          title=f"Class missed by teacher - {s.teacher.full_name if s.teacher else s.teacher_id}",
                          message=f"Student {s.student.full_name if s.student else s.student_id} at {s.start_time.strftime('%H:%M')} on {s.date}.",
                          entity_type="ClassSession", entity_id=s.id, visibility="ops", source="system",
                          status=rnd.choice(["open", "acknowledged", "resolved", "acknowledged"]))
            a.created_at = created
            a.updated_at = created + timedelta(minutes=rnd.randint(4, 90))
            db.add(a)
    if db.query(SafeguardingFlag).filter(SafeguardingFlag.source == "manual").count() == 0:
        ceo = db.query(User).filter(User.email == "admin@oqc.local").first()
        teachers = db.query(Teacher).order_by(Teacher.id).limit(6).all()
        specs = [("off_platform_contact", "high", "Parent reported the teacher asked to continue lessons privately over WhatsApp.", "investigating"),
                 ("social_discovery", "medium", "Teacher profile found advertising private Quran tuition to college families.", "open"),
                 ("conduct", "medium", "Supervisor observed raised voice during a class; recording reviewed.", "dismissed")]
        for i, (ftype, sev, evidence, status) in enumerate(specs):
            t = teachers[i % len(teachers)] if teachers else None
            db.add(SafeguardingFlag(flag_type=ftype, severity=sev, teacher_id=t.id if t else None, evidence=evidence,
                                    source="manual", status=status, visibility="ceo_only" if ftype != "conduct" else "management",
                                    handled_by_id=ceo.id if ceo and status != "open" else None,
                                    resolution="No evidence of solicitation found; teacher briefed on the policy." if status == "dismissed" else None))
    db.flush()


def _trials_calls_reminders(db: Session) -> None:
    today = date.today()
    if db.query(Trial).count() == 0:
        leads = db.query(Lead).order_by(Lead.id).limit(8).all()
        trial_students = db.query(Student).filter(Student.status == "trial").order_by(Student.id).limit(8).all()
        closer = db.query(User).filter(User.email == "closer@oqc.local").first()
        for i, st in enumerate(trial_students):
            sess = (db.query(ClassSession).filter(ClassSession.student_id == st.id).order_by(ClassSession.scheduled_start).first())
            status = ["attended", "converted", "scheduled", "no_show", "attended", "scheduled", "converted", "attended"][i % 8]
            db.add(Trial(lead_id=leads[i].id if i < len(leads) else None, client_id=st.client_id, student_id=st.id,
                         teacher_id=st.teacher_id, course_id=st.course_id, session_id=sess.id if sess else None,
                         student_name=st.full_name,
                         scheduled_at=datetime.combine(today - timedelta(days=rnd.randint(0, 20)), time(17, 0)),
                         status=status, outcome="Parent happy with the teacher" if status in ("attended", "converted") else None,
                         teacher_feedback="Student can already read the Qaida confidently; recommend Nazra." if status != "no_show" else None,
                         follow_up_date=today + timedelta(days=2), follow_up_count=rnd.randint(0, 3),
                         closer_id=closer.id if closer else None))
    if db.query(CallLog).count() == 0:
        sup = db.query(User).filter(User.email == "supervisor@oqc.local").first()
        for i in range(10):
            db.add(CallLog(caller_id=sup.id if sup else None, callee_type="client", callee_id=i + 1,
                           callee_masked=f"+44 7** *** *{rnd.randint(100, 999)}", channel="voip", direction="outbound",
                           status=rnd.choice(["completed", "completed", "missed", "busy"]), duration_seconds=rnd.randint(30, 420),
                           notes="Follow-up on a missed class; parent informed and class rescheduled.",
                           started_at=datetime.utcnow() - timedelta(days=rnd.randint(0, 10), minutes=rnd.randint(0, 900))))
    if db.query(ReminderLog).count() == 0:
        for s in db.query(ClassSession).filter(ClassSession.date == today).limit(40).all():
            if s.teacher and s.teacher.user_id:
                db.add(ReminderLog(reminder_type="class_teacher", user_id=s.teacher.user_id, entity_type="ClassSession",
                                   entity_id=s.id, channel="in_app"))
                s.reminder_sent_teacher = True
            if s.student and s.student.client and s.student.client.user_id:
                db.add(ReminderLog(reminder_type="class_student", user_id=s.student.client.user_id, entity_type="ClassSession",
                                   entity_id=s.id, channel="whatsapp"))
                s.reminder_sent_student = True
    if db.query(RecordingAccessLog).count() == 0:
        qao = db.query(User).filter(User.email == "qaofficer@oqc.local").first()
        for rec in db.query(Recording).order_by(Recording.id).limit(8).all():
            db.add(RecordingAccessLog(recording_id=rec.id, user_id=qao.id if qao else None,
                                      purpose="QA sampling review", ip="127.0.0.1",
                                      accessed_at=datetime.utcnow() - timedelta(days=rnd.randint(0, 9))))
            rec.access_count = (rec.access_count or 0) + 1
    db.flush()


def run(db: Session) -> None:
    shifts = _get_shifts(db)
    schedules = _build_schedules(db, shifts)
    existing = db.query(Schedule).filter(Schedule.status == "active").all()
    _materialise(db, schedules or existing)
    db.flush()
    _apply_history(db)
    _rescheduled_links(db)
    _leaves(db)
    db.commit()
    _recordings_and_ai(db)
    db.commit()
    _qa_reviews(db)
    _alerts_and_flags(db)
    _trials_calls_reminders(db)
    db.commit()
