"""Teacher portal (/teacher). Everything is scoped to the signed-in teacher (ctx.teacher)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core.audit import log_action, snapshot
from app.core.deps import require, csrf_protect, get_user_context, UserContext, PermissionDenied, client_ip
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import (redirect, paginate, parse_date, parse_int, parse_float, parse_bool, month_key,
                            month_bounds, pct)
from app.database import get_db
from app.models.academic import Course, Evaluation, LessonPlan, MonthlyTest, Certificate, DorSchedule, Lesson
from app.models.core import User
from app.models.crm import Case
from app.models.ops import Task
from app.models.people import Student, Teacher, Leave, HRAttendance, TrainingAssignment, Violation, Payslip, SalaryStructure
from app.models.scheduling import (ClassSession, Schedule, Attendance, QAReview, CorrectiveAction, AIClassAnalysis, Trial)
from app.services import people as svc
from app.services.classes import set_status, mark_join, teacher_stats, student_attendance_pct

router = APIRouter(prefix="/teacher", dependencies=[Depends(csrf_protect)])

WEEKDAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def me(ctx: UserContext) -> Teacher:
    if not ctx.teacher:
        raise PermissionDenied("portal_teacher.view (no teacher profile linked to this login)")
    return ctx.teacher


def _session(db: Session, t: Teacher, sid: int) -> ClassSession:
    cs = db.query(ClassSession).filter(ClassSession.id == sid, ClassSession.teacher_id == t.id).first()
    if not cs:
        raise HTTPException(404, "Class not found")
    return cs


def _my_student(db: Session, t: Teacher, sid: int) -> Student:
    s = db.query(Student).filter(Student.id == sid, Student.teacher_id == t.id).first()
    if not s:
        raise HTTPException(404, "Student not found")
    return s


# --------------------------------------------------------------------------- dashboard
@router.get("", include_in_schema=False)
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_teacher.view")),
              ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    today = date.today()
    sessions = (db.query(ClassSession).filter(ClassSession.teacher_id == t.id, ClassSession.date == today)
                .order_by(ClassSession.start_time).all())
    counts: dict[str, int] = {}
    for cs in sessions:
        counts[cs.status] = counts.get(cs.status, 0) + 1
    student_ids = [s.id for s in db.query(Student.id).filter(Student.teacher_id == t.id)]
    plans_due = (db.query(ClassSession).filter(ClassSession.teacher_id == t.id, ClassSession.date == today,
                                               ClassSession.lesson_plan_id.is_(None)).count())
    unscored = (db.query(MonthlyTest).filter(MonthlyTest.teacher_id == t.id, MonthlyTest.score.is_(None)).count())
    open_actions = db.query(CorrectiveAction).filter(CorrectiveAction.teacher_id == t.id,
                                                     CorrectiveAction.status.in_(["open", "in_progress", "overdue"])).all()
    tasks = (db.query(Task).filter(Task.assignee_id == user.id, Task.status.in_(["todo", "in_progress", "review"]))
             .order_by(Task.due_date).limit(8).all())
    start, end = month_bounds(month_key())
    month = teacher_stats(db, t.id, start)
    trials = db.query(Trial).filter(Trial.teacher_id == t.id, Trial.status.in_(["requested", "scheduled"])).count()
    return render(request, "teacher_portal/dashboard.html", {
        "user": user, "t": t, "today_d": today, "sessions": sessions, "counts": counts,
        "students_total": len(student_ids), "plans_due": plans_due, "unscored": unscored,
        "open_actions": open_actions, "tasks": tasks, "month": month, "trials": trials,
        "high_risk": db.query(func.count(Student.id)).filter(Student.teacher_id == t.id, Student.risk_level == "high",
                                                             Student.status.in_(["active", "trial"])).scalar() or 0,
        "now": datetime.utcnow()})


@router.get("/sessions/{sid}/join", include_in_schema=False)
def join_session(sid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_teacher.view")),
                 ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    cs = _session(db, t, sid)
    mark_join(db, cs, "teacher")
    log_action(db, user, "join", "classes", entity=cs, description=f"Teacher joined class {cs.id}", request=request)
    db.commit()
    return RedirectResponse(cs.join_url or f"/classroom/{cs.id}", status_code=303)


@router.post("/sessions/{sid}/status", include_in_schema=False)
async def session_status(sid: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    cs = _session(db, t, sid)
    form = await request.form()
    action = form.get("action") or ""
    if action not in ("started", "done", "absent"):
        return redirect("/teacher", "Unsupported class action.", "error")
    if action == "done":
        notes = (form.get("teacher_notes") or "").strip()
        delivered = (form.get("delivered_content") or "").strip()
        cs.teacher_notes = notes or cs.teacher_notes
        set_status(db, cs, "done", user, reason=notes or None, request=request)
        plan = db.query(LessonPlan).filter(LessonPlan.session_id == cs.id).first()
        if not plan:
            plan = db.query(LessonPlan).filter(LessonPlan.student_id == cs.student_id, LessonPlan.plan_date == cs.date).first()
        if delivered:
            if not plan:
                plan = LessonPlan(student_id=cs.student_id, teacher_id=t.id, session_id=cs.id, plan_date=cs.date,
                                  planned_content=delivered, status="planned")
                db.add(plan)
                db.flush()
            plan.session_id = plan.session_id or cs.id
            try:
                from app.services.academic import mark_plan_delivered
                mark_plan_delivered(db, plan, delivered, notes or None, user, request=request)
            except Exception:
                plan.delivered_content = delivered
                plan.teacher_notes = notes or plan.teacher_notes
                plan.status = "delivered"
            cs.lesson_plan_id = plan.id
    else:
        set_status(db, cs, action, user, reason=(form.get("reason") or None), request=request)
    db.commit()
    labels = {"started": "Class started.", "done": "Class completed and the delivered lesson recorded.",
              "absent": "Student marked absent."}
    return redirect(request.headers.get("referer") or "/teacher", labels[action])


# --------------------------------------------------------------------------- schedule
@router.get("/schedule", include_in_schema=False)
def schedule(request: Request, week: int = 0, db: Session = Depends(get_db),
             user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    today = date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week)
    days = [monday + timedelta(days=i) for i in range(7)]
    rows = (db.query(ClassSession).filter(ClassSession.teacher_id == t.id, ClassSession.date >= days[0],
                                          ClassSession.date <= days[-1]).order_by(ClassSession.scheduled_start).all())
    grid: dict[str, dict[int, list]] = {}
    for cs in rows:
        slot = cs.start_time.strftime("%H:%M")
        grid.setdefault(slot, {}).setdefault((cs.date - monday).days, []).append(cs)
    slots = sorted(grid.keys()) or [f"{h:02d}:{m:02d}" for h in range(14, 22) for m in (0, 30)]
    schedules = db.query(Schedule).filter(Schedule.teacher_id == t.id, Schedule.status == "active").all()
    return render(request, "teacher_portal/schedule.html", {
        "user": user, "t": t, "week": week, "monday": monday, "days": days, "day_labels": WEEKDAY_LABELS,
        "slots": slots, "grid": grid, "today_d": today, "schedules": schedules, "total": len(rows)})


# --------------------------------------------------------------------------- classes
@router.get("/classes", include_in_schema=False)
def classes(request: Request, page: int = 1, status: str = "", db: Session = Depends(get_db),
            user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    q = db.query(ClassSession).filter(ClassSession.teacher_id == t.id)
    if status:
        q = q.filter(ClassSession.status == status)
    pg = paginate(q.order_by(ClassSession.scheduled_start.desc()), page, 40)
    since = date.today() - timedelta(days=30)
    stats = teacher_stats(db, t.id, since)
    return render(request, "teacher_portal/classes.html", {"user": user, "t": t, "page": pg, "status": status,
                                                           "stats": stats, "base_url": f"/teacher/classes?status={status}"})


@router.post("/classes/{sid}/notes", include_in_schema=False)
async def class_notes(sid: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    cs = _session(db, t, sid)
    form = await request.form()
    before = {"teacher_notes": cs.teacher_notes}
    cs.teacher_notes = (form.get("teacher_notes") or "").strip() or None
    log_action(db, user, "update", "classes", entity=cs, description=f"Teacher notes updated for class {cs.id}",
               before=before, after={"teacher_notes": cs.teacher_notes}, request=request)
    db.commit()
    return redirect("/teacher/classes", "Class notes saved.")


# --------------------------------------------------------------------------- students
@router.get("/students", include_in_schema=False)
def students(request: Request, q: str = "", status: str = "", db: Session = Depends(get_db),
             user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    query = db.query(Student).filter(Student.teacher_id == t.id)
    if q:
        query = query.filter(or_(Student.full_name.ilike(f"%{q}%"), Student.student_code.ilike(f"%{q}%")))
    if status:
        query = query.filter(Student.status == status)
    rows = query.order_by(Student.full_name).all()
    data = []
    for s in rows:
        last = (db.query(Attendance).filter(Attendance.student_id == s.id)
                .order_by(Attendance.date.desc()).first())
        data.append({"s": s, "attendance": student_attendance_pct(db, s.id, 30),
                     "progress": svc.student_progress_summary(db, s).get("pct", 0),
                     "last_attendance": last})
    return render(request, "teacher_portal/students.html", {"user": user, "t": t, "rows": data, "q": q, "status": status,
                                                            "statuses": ["trial", "active", "frozen", "free", "graduated", "cancelled"]})


# --------------------------------------------------------------------------- lesson plans
@router.get("/lesson-plans", include_in_schema=False)
def lesson_plans(request: Request, page: int = 1, student_id: int = 0, db: Session = Depends(get_db),
                 user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    q = db.query(LessonPlan).filter(LessonPlan.teacher_id == t.id)
    if student_id:
        q = q.filter(LessonPlan.student_id == student_id)
    pg = paginate(q.order_by(LessonPlan.plan_date.desc(), LessonPlan.id.desc()), page, 30)
    my_students = db.query(Student).filter(Student.teacher_id == t.id, Student.status.in_(["active", "trial", "free"])).order_by(Student.full_name).all()
    return render(request, "teacher_portal/lesson_plans.html", {
        "user": user, "t": t, "page": pg, "student_id": student_id,
        "student_options": [(s.id, f"{s.student_code} — {s.full_name}") for s in my_students],
        "today_d": date.today(), "base_url": f"/teacher/lesson-plans?student_id={student_id or ''}"})


@router.post("/lesson-plans", include_in_schema=False)
async def create_plan(request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    form = await request.form()
    s = _my_student(db, t, parse_int(form.get("student_id")) or 0)
    plan_date = parse_date(form.get("plan_date")) or date.today()
    planned = (form.get("planned_content") or "").strip()
    if not planned:
        return redirect("/teacher/lesson-plans", "Planned content is required.", "error")
    plan = LessonPlan(student_id=s.id, teacher_id=t.id, plan_date=plan_date, plan_type=form.get("plan_type") or "daily",
                      planned_content=planned, sabaq=form.get("sabaq") or None, sabqi=form.get("sabqi") or None,
                      dor=form.get("dor") or None, next_objectives=form.get("next_objectives") or None, status="planned")
    db.add(plan)
    db.flush()
    log_action(db, user, "create", "lesson_plans", entity=plan, request=request,
               description=f"Lesson plan for {s.student_code} on {plan_date}")
    db.commit()
    return redirect("/teacher/lesson-plans", "Lesson plan saved.")


@router.post("/lesson-plans/{pid}/deliver", include_in_schema=False)
async def deliver_plan(pid: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    plan = db.query(LessonPlan).filter(LessonPlan.id == pid, LessonPlan.teacher_id == t.id).first()
    if not plan:
        raise HTTPException(404, "Lesson plan not found")
    form = await request.form()
    delivered = (form.get("delivered_content") or "").strip()
    if not delivered:
        return redirect("/teacher/lesson-plans", "Describe what was actually delivered.", "error")
    try:
        from app.services.academic import mark_plan_delivered
        mark_plan_delivered(db, plan, delivered, form.get("teacher_notes") or None, user, request=request)
    except Exception:
        plan.delivered_content = delivered
        plan.teacher_notes = form.get("teacher_notes") or plan.teacher_notes
        plan.status = "delivered"
        log_action(db, user, "deliver", "lesson_plans", entity=plan, description="Lesson plan marked delivered", request=request)
    db.commit()
    return redirect("/teacher/lesson-plans", "Lesson plan marked delivered.")


@router.post("/lesson-plans/recommend", include_in_schema=False)
async def recommend_plan(request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    form = await request.form()
    s = _my_student(db, t, parse_int(form.get("student_id")) or 0)
    text = None
    try:
        from app.services.academic import recommend_lesson
        text = recommend_lesson(db, s, None, user)
    except Exception:
        try:
            from app.services.ai_gateway import ai
            res, _run = ai(db, "lesson_recommendation", "next_lesson",
                           {"student_id": s.id, "course": s.course.code if s.course else None,
                            "sabaq": s.sabaq_position, "level": s.level}, entity=s)
            text = res.get("recommendation") or res.get("summary")
        except Exception:
            text = f"Continue from {s.sabaq_position or 'the current position'}; revise the previous two lessons (sabqi) before new sabaq."
    db.commit()
    return redirect(f"/teacher/lesson-plans?student_id={s.id}&suggestion={(text or '')[:400]}", "AI recommendation ready.", "info")


# --------------------------------------------------------------------------- evaluations
@router.get("/evaluations", include_in_schema=False)
def evaluations(request: Request, page: int = 1, db: Session = Depends(get_db),
                user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    pg = paginate(db.query(Evaluation).filter(Evaluation.teacher_id == t.id).order_by(Evaluation.date.desc(), Evaluation.id.desc()), page, 30)
    my_students = db.query(Student).filter(Student.teacher_id == t.id).order_by(Student.full_name).all()
    return render(request, "teacher_portal/evaluations.html", {
        "user": user, "t": t, "page": pg, "today_d": date.today(),
        "student_options": [(s.id, f"{s.student_code} — {s.full_name}") for s in my_students],
        "base_url": "/teacher/evaluations"})


@router.post("/evaluations", include_in_schema=False)
async def create_evaluation(request: Request, db: Session = Depends(get_db),
                            user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    form = await request.form()
    s = _my_student(db, t, parse_int(form.get("student_id")) or 0)
    criteria = {k: parse_float(form.get(k)) for k in ("tajweed", "fluency", "memorisation", "attitude") if form.get(k)}
    score = round(sum(criteria.values()) / len(criteria) * 10, 1) if criteria else parse_float(form.get("score"))
    ev = Evaluation(student_id=s.id, teacher_id=t.id, evaluation_type=form.get("evaluation_type") or "weekly",
                    date=parse_date(form.get("date")) or date.today(), score=score, max_score=100,
                    result="pass" if (score or 0) >= 50 else "fail", criteria=criteria,
                    teacher_comment=form.get("teacher_comment") or None)
    db.add(ev)
    db.flush()
    log_action(db, user, "create", "evaluations", entity=ev, request=request,
               description=f"Evaluation for {s.student_code} ({ev.evaluation_type}) score {score}")
    if s.client and s.client.user_id:
        notify(db, s.client.user_id, f"New evaluation for {s.full_name}",
               f"{ev.evaluation_type.title()} evaluation scored {score}/100.", event_type="evaluation", link="/portal/progress")
    db.commit()
    return redirect("/teacher/evaluations", "Evaluation recorded and the parent notified.")


# --------------------------------------------------------------------------- monthly tests
@router.get("/monthly-tests", include_in_schema=False)
def monthly_tests(request: Request, period: str = "", db: Session = Depends(get_db),
                  user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    q = db.query(MonthlyTest).filter(MonthlyTest.teacher_id == t.id)
    if period:
        q = q.filter(MonthlyTest.period == period)
    rows = q.order_by(MonthlyTest.period.desc(), MonthlyTest.id.desc()).limit(200).all()
    periods = [p for (p,) in db.query(MonthlyTest.period).filter(MonthlyTest.teacher_id == t.id).distinct().order_by(MonthlyTest.period.desc())]
    dor = {d.student_id: d for d in db.query(DorSchedule).filter(DorSchedule.period == month_key())}
    blocked = db.query(Student).filter(Student.teacher_id == t.id, Student.dor_quota_met.is_(False)).all()
    return render(request, "teacher_portal/monthly_tests.html", {"user": user, "t": t, "rows": rows, "period": period,
                                                                 "periods": periods, "dor": dor, "blocked": blocked,
                                                                 "current_period": month_key()})


@router.get("/monthly-tests/{tid}", include_in_schema=False)
def score_form(tid: int, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    test = db.query(MonthlyTest).filter(MonthlyTest.id == tid, MonthlyTest.teacher_id == t.id).first()
    if not test:
        raise HTTPException(404, "Monthly test not found")
    return render(request, "teacher_portal/monthly_test_score.html", {"user": user, "t": t, "test": test,
                                                                      "student": db.query(Student).get(test.student_id)})


@router.post("/monthly-tests/{tid}/score", include_in_schema=False)
async def submit_scores(tid: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    test = db.query(MonthlyTest).filter(MonthlyTest.id == tid, MonthlyTest.teacher_id == t.id).first()
    if not test:
        raise HTTPException(404, "Monthly test not found")
    form = await request.form()
    scores = {}
    for i, q in enumerate(test.questions or []):
        raw = form.get(f"q{i}")
        if raw not in (None, ""):
            scores[str(i)] = parse_float(raw)
    remarks = (form.get("teacher_remarks") or "").strip()
    if not scores:
        return redirect(f"/teacher/monthly-tests/{test.id}", "Enter at least one score.", "error")
    try:
        from app.services.academic import score_monthly_test
        score_monthly_test(db, test, scores, remarks, form.get("teacher_remarks_urdu") or None, user, request=request)
    except Exception:
        qs = [dict(q) for q in (test.questions or [])]
        total = max_total = 0.0
        for i, q in enumerate(qs):
            if str(i) in scores:
                q["score"] = max(0.0, min(float(q.get("max", 10)), scores[str(i)]))
                total += q["score"]
            max_total += float(q.get("max", 10))
        test.questions = qs
        test.score = round(total, 1)
        test.max_score = max_total or 100
        test.percentage = round(100 * total / max_total, 1) if max_total else None
        prev = (db.query(MonthlyTest).filter(MonthlyTest.student_id == test.student_id, MonthlyTest.period < test.period,
                                             MonthlyTest.percentage.isnot(None)).order_by(MonthlyTest.period.desc()).first())
        test.previous_percentage = prev.percentage if prev else None
        test.improvement_pct = round((test.percentage or 0) - prev.percentage, 1) if prev and prev.percentage is not None else None
        p = test.percentage or 0
        test.grade = "A+" if p >= 90 else "A" if p >= 80 else "B" if p >= 70 else "C" if p >= 60 else "D" if p >= 50 else "F"
        test.teacher_remarks = remarks or None
        test.teacher_remarks_urdu = form.get("teacher_remarks_urdu") or None
        test.status = "scored"
        test.scored_at = datetime.utcnow()
        log_action(db, user, "update", "monthly_tests", entity=test, request=request,
                   description=f"Monthly test {test.period} scored {test.percentage}% (grade {test.grade})")
    db.commit()
    return redirect("/teacher/monthly-tests", f"Test scored: {test.percentage}% (grade {test.grade}).")


@router.post("/students/{sid}/dor-override", include_in_schema=False)
async def dor_override(sid: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    s = _my_student(db, t, sid)
    form = await request.form()
    reason = (form.get("reason") or "").strip()
    if not reason:
        return redirect("/teacher/monthly-tests", "A rationale is required to override the revision quota.", "error")
    try:
        from app.services.academic import override_dor_quota
        override_dor_quota(db, s, user, reason, request=request)
    except Exception:
        s.dor_quota_met = True
        log_action(db, user, "override", "monthly_tests", entity=s, rationale=reason, consequential=True, request=request,
                   description=f"Dor quota override for {s.student_code}")
    db.commit()
    return redirect("/teacher/monthly-tests", f"Revision quota overridden for {s.full_name} — the reason is on the audit trail.", "warning")


# --------------------------------------------------------------------------- trials
@router.get("/trials", include_in_schema=False)
def trials(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_teacher.view")),
           ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    rows = db.query(Trial).filter(Trial.teacher_id == t.id).order_by(Trial.scheduled_at.desc(), Trial.id.desc()).all()
    return render(request, "teacher_portal/trials.html", {"user": user, "t": t, "rows": rows})


@router.post("/trials/{trid}/feedback", include_in_schema=False)
async def trial_feedback(trid: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    tr = db.query(Trial).filter(Trial.id == trid, Trial.teacher_id == t.id).first()
    if not tr:
        raise HTTPException(404, "Trial not found")
    form = await request.form()
    tr.teacher_feedback = (form.get("teacher_feedback") or "").strip() or None
    outcome = form.get("outcome")
    if outcome in ("attended", "no_show"):
        tr.status = outcome
    tr.outcome = form.get("recommendation") or tr.outcome
    log_action(db, user, "update", "trials", entity=tr, request=request,
               description=f"Trial feedback for {tr.student_name} ({tr.status})")
    if tr.closer_id:
        notify(db, tr.closer_id, f"Trial feedback: {tr.student_name}",
               f"{t.full_name} reported '{tr.status}'. {tr.teacher_feedback or ''}", event_type="trial", link="/trials")
    db.commit()
    return redirect("/teacher/trials", "Trial feedback saved and the closer notified.")


# --------------------------------------------------------------------------- performance
@router.get("/performance", include_in_schema=False)
def performance(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_teacher.view")),
                ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    today = date.today()
    labels, done, missed = [], [], []
    for w in range(7, -1, -1):
        start = today - timedelta(days=today.weekday() + 7 * w)
        end = start + timedelta(days=6)
        d = dict(db.query(ClassSession.status, func.count(ClassSession.id))
                 .filter(ClassSession.teacher_id == t.id, ClassSession.date >= start, ClassSession.date <= end)
                 .group_by(ClassSession.status).all())
        labels.append(start.strftime("%d %b"))
        done.append(d.get("done", 0))
        missed.append(d.get("missed", 0))
    ai_avg = db.query(func.avg(AIClassAnalysis.overall_score)).filter(AIClassAnalysis.teacher_id == t.id).scalar()
    students = db.query(Student).filter(Student.teacher_id == t.id).all()
    active = [s for s in students if s.status in ("active", "trial", "free")]
    return render(request, "teacher_portal/performance.html", {
        "user": user, "t": t, "chart": {"labels": labels, "done": done, "missed": missed},
        "stats30": teacher_stats(db, t.id, today - timedelta(days=30)),
        "stats90": teacher_stats(db, t.id, today - timedelta(days=90)),
        "ai_score": round(float(ai_avg), 1) if ai_avg else None,
        "students_total": len(students), "students_active": len(active),
        "complaints": db.query(func.count(Case.id)).filter(Case.teacher_id == t.id).scalar() or 0,
        "high_risk": sum(1 for s in active if s.risk_level == "high")})


# --------------------------------------------------------------------------- QA
@router.get("/qa", include_in_schema=False)
def qa(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_teacher.view")),
       ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    reviews = (db.query(QAReview).filter(QAReview.teacher_id == t.id, QAReview.status.in_(["completed", "approved"]))
               .order_by(QAReview.created_at.desc()).limit(40).all())
    analyses = (db.query(AIClassAnalysis).filter(AIClassAnalysis.teacher_id == t.id)
                .order_by(AIClassAnalysis.created_at.desc()).limit(20).all())
    actions = db.query(CorrectiveAction).filter(CorrectiveAction.teacher_id == t.id).order_by(CorrectiveAction.status, CorrectiveAction.due_date).all()
    return render(request, "teacher_portal/qa.html", {"user": user, "t": t, "reviews": reviews, "analyses": analyses,
                                                      "actions": actions})


@router.post("/corrective-actions/{aid}/done", include_in_schema=False)
async def close_action(aid: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    ca = db.query(CorrectiveAction).filter(CorrectiveAction.id == aid, CorrectiveAction.teacher_id == t.id).first()
    if not ca:
        raise HTTPException(404, "Corrective action not found")
    form = await request.form()
    note = (form.get("closure_note") or "").strip()
    if not note:
        return redirect("/teacher/qa", "Describe what you changed before closing the action.", "error")
    try:
        from app.services.qa import close_corrective_action
        close_corrective_action(db, ca, user, note, request=request)
    except Exception:
        ca.status = "closed"
        ca.closed_at = datetime.utcnow()
        ca.closure_note = note
        log_action(db, user, "update", "qa", entity=ca, description="Corrective action closed by the teacher", request=request)
    db.commit()
    return redirect("/teacher/qa", "Corrective action marked done.")


# --------------------------------------------------------------------------- training
@router.get("/training", include_in_schema=False)
def training(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_teacher.view")),
             ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    rows = db.query(TrainingAssignment).filter(TrainingAssignment.teacher_id == t.id).order_by(TrainingAssignment.status, TrainingAssignment.due_date).all()
    return render(request, "teacher_portal/training.html", {"user": user, "t": t, "rows": rows})


@router.post("/training/{aid}/status", include_in_schema=False)
async def training_status(aid: int, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    ta = db.query(TrainingAssignment).filter(TrainingAssignment.id == aid, TrainingAssignment.teacher_id == t.id).first()
    if not ta:
        raise HTTPException(404, "Training not found")
    form = await request.form()
    new = form.get("status") or "in_progress"
    if new not in ("in_progress", "completed"):
        return redirect("/teacher/training", "Unsupported status.", "error")
    ta.status = new
    if new == "completed":
        ta.completed_at = datetime.utcnow()
    log_action(db, user, "update", "teacher_dev", entity=ta, request=request,
               description=f"Training '{ta.title}' marked {new} by the teacher")
    if new == "completed" and ta.assigned_by_id:
        notify(db, ta.assigned_by_id, "Training completed", f"{t.full_name} completed '{ta.title}'.",
               event_type="training", link=f"/teachers/{t.id}?tab=training")
    db.commit()
    return redirect("/teacher/training", f"Training marked {new}.")


# --------------------------------------------------------------------------- HR
@router.get("/hr", include_in_schema=False)
def hr(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_teacher.view")),
       ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    emp = ctx.employee or t.employee
    today = date.today()
    rows, leaves, violations, subs = [], [], [], []
    todays = {}
    if emp:
        rows = (db.query(HRAttendance).filter(HRAttendance.employee_id == emp.id, HRAttendance.date >= today - timedelta(days=30))
                .order_by(HRAttendance.date.desc(), HRAttendance.session).all())
        todays = {r.session: r for r in rows if r.date == today}
        leaves = db.query(Leave).filter(Leave.person_type == "employee", Leave.employee_id == emp.id).order_by(Leave.start_date.desc()).all()
        violations = db.query(Violation).filter(Violation.employee_id == emp.id).order_by(Violation.date.desc()).limit(20).all()
    try:
        from app.services.hr import substitute_suggestions
        subs = substitute_suggestions(db, t, today, today + timedelta(days=7))
    except Exception:
        subs = []
    present = sum(1 for r in rows if r.status in ("present", "late"))
    return render(request, "teacher_portal/hr.html", {
        "user": user, "t": t, "emp": emp, "rows": rows, "todays": todays, "leaves": leaves, "violations": violations,
        "subs": subs, "today_d": today,
        "summary": {"present": present, "late": sum(1 for r in rows if r.status == "late"),
                    "absent": sum(1 for r in rows if r.status == "absent"),
                    "pct": pct(present, len(rows)) if rows else 0.0}})


@router.post("/hr/check", include_in_schema=False)
async def hr_check(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_teacher.view")),
                   ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    emp = ctx.employee or t.employee
    if not emp:
        return redirect("/teacher/hr", "No employee record is linked to your login.", "error")
    form = await request.form()
    session = form.get("session") or "am"
    action = form.get("action") or "in"
    try:
        from app.services.hr import mark_attendance
        row = mark_attendance(db, emp, session, action, user, ip=client_ip(request), request=request)
        status = row.status
    except Exception:
        now = datetime.utcnow()
        row = db.query(HRAttendance).filter(HRAttendance.employee_id == emp.id, HRAttendance.date == date.today(),
                                            HRAttendance.session == session).first()
        if not row:
            row = HRAttendance(employee_id=emp.id, date=date.today(), session=session, status="present")
            db.add(row)
        if action == "in" and row.check_in is None:
            row.check_in = now
        elif action == "out":
            row.check_out = now
        db.flush()
        log_action(db, user, f"check_{action}", "hr_attendance", entity=row, request=request,
                   description=f"{emp.employee_code} {session.upper()} check-{action}")
        status = row.status
    db.commit()
    return redirect("/teacher/hr", f"{session.upper()} check-{action} recorded ({status}).")


@router.post("/hr/correction", include_in_schema=False)
async def hr_correction(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_teacher.view")),
                        ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    emp = ctx.employee or t.employee
    form = await request.form()
    row = db.query(HRAttendance).filter(HRAttendance.id == (parse_int(form.get("attendance_id")) or -1)).first()
    if not row or not emp or row.employee_id != emp.id:
        raise HTTPException(404, "Attendance record not found")
    reason = (form.get("reason") or "").strip()
    if not reason:
        return redirect("/teacher/hr", "A reason is required for a correction request.", "error")
    try:
        from app.services.hr import request_correction
        request_correction(db, row, reason, user, request=request)
    except Exception:
        row.correction_requested = True
        row.correction_reason = reason
        row.correction_status = "pending"
        log_action(db, user, "update", "hr_attendance", entity=row, rationale=reason, request=request,
                   description=f"Attendance correction requested for {row.date} {row.session}")
    db.commit()
    return redirect("/teacher/hr", "Correction request submitted to HR.")


@router.post("/hr/leave", include_in_schema=False)
async def hr_leave(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_teacher.view")),
                   ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    emp = ctx.employee or t.employee
    if not emp:
        return redirect("/teacher/hr", "No employee record is linked to your login.", "error")
    form = await request.form()
    start, end = parse_date(form.get("start_date")), parse_date(form.get("end_date"))
    if not start or not end or end < start:
        return redirect("/teacher/hr", "Valid start and end dates are required.", "error")
    sub = db.query(Teacher).get(parse_int(form.get("substitute_teacher_id")) or 0)
    try:
        from app.services.hr import request_leave
        request_leave(db, emp, form.get("leave_type") or "casual", start, end, form.get("reason"), user,
                      substitute_teacher=sub, request=request)
    except ValueError as exc:
        return redirect("/teacher/hr", str(exc), "error")
    except Exception:
        lv = Leave(person_type="employee", employee_id=emp.id, leave_type=form.get("leave_type") or "casual",
                   start_date=start, end_date=end, reason=form.get("reason"), status="pending",
                   requested_by_id=user.id, substitute_teacher_id=sub.id if sub else None)
        db.add(lv)
        db.flush()
        log_action(db, user, "create", "leaves", entity=lv, request=request, description=f"Leave requested {start}..{end}")
    db.commit()
    return redirect("/teacher/hr", "Leave request submitted for approval.")


# --------------------------------------------------------------------------- income
@router.get("/income", include_in_schema=False)
def income(request: Request, period: str = "", db: Session = Depends(get_db),
           user: User = Depends(require("portal_teacher.view")), ctx: UserContext = Depends(get_user_context)):
    t = me(ctx)
    period = period or month_key()
    try:
        start, end = month_bounds(period)
    except Exception:
        period = month_key()
        start, end = month_bounds(period)
    done = (db.query(func.count(ClassSession.id)).filter(ClassSession.teacher_id == t.id, ClassSession.status == "done",
                                                         ClassSession.date >= start, ClassSession.date <= end).scalar() or 0)
    missed = (db.query(func.count(ClassSession.id)).filter(ClassSession.teacher_id == t.id, ClassSession.status == "missed",
                                                           ClassSession.date >= start, ClassSession.date <= end).scalar() or 0)
    rate = float(t.per_class_rate or 0)
    emp = ctx.employee or t.employee
    structure = db.query(SalaryStructure).filter(SalaryStructure.employee_id == emp.id).first() if emp else None
    payslips = db.query(Payslip).filter(Payslip.employee_id == emp.id).order_by(Payslip.created_at.desc()).limit(12).all() if emp else []
    months = []
    for i in range(5, -1, -1):
        y, m = divmod((int(period[:4]) * 12 + int(period[5:7]) - 1) - i, 12)
        p = f"{y:04d}-{m + 1:02d}"
        s2, e2 = month_bounds(p)
        n = (db.query(func.count(ClassSession.id)).filter(ClassSession.teacher_id == t.id, ClassSession.status == "done",
                                                          ClassSession.date >= s2, ClassSession.date <= e2).scalar() or 0)
        months.append({"period": p, "classes": n, "pay": round(n * rate, 2)})
    return render(request, "teacher_portal/income.html", {
        "user": user, "t": t, "period": period, "done": done, "missed": missed, "rate": rate,
        "class_pay": round(done * rate, 2), "structure": structure, "payslips": payslips, "months": months})
