"""Teachers (Module 45): roster, one-action creation (user + employee + teacher), verification gate, performance."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import require, csrf_protect
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import redirect, Page, parse_int, parse_float, parse_bool, parse_date, month_key, month_bounds
from app.database import get_db
from app.models.academic import Course
from app.models.core import User, AuditEvent, Role
from app.models.crm import Case
from app.models.people import Teacher, Employee, Student, SalaryStructure, Payslip, TrainingAssignment, Leave
from app.models.scheduling import ClassSession, Schedule, QAReview, CorrectiveAction, AIClassAnalysis, TeacherMatch
from app.services import people as svc
from app.services.classes import teacher_stats

router = APIRouter(prefix="/teachers", dependencies=[Depends(csrf_protect)])

TABS = [("overview", "Overview"), ("students", "Students"), ("schedule", "Schedule"), ("performance", "Performance"),
        ("qa", "QA reviews"), ("training", "Training"), ("income", "Income & payslips"), ("audit", "Audit")]
SHIFTS = ["morning", "evening", "night"]
GRADES = ["A", "B", "C"]
STATUSES = ["active", "on_leave", "inactive"]


def _get(db: Session, id: int) -> Teacher:
    t = db.query(Teacher).get(id)
    if not t:
        raise HTTPException(404, "Teacher not found")
    return t


def _form_context(db: Session, t: Teacher | None = None) -> dict:
    courses = db.query(Course).filter(Course.is_active.is_(True)).order_by(Course.order).all()
    sups = (db.query(User).join(Role, User.role_id == Role.id)
            .filter(Role.slug.in_(["supervisor", "manager", "hod_academics", "academic_coordinator", "hod_qa"]), User.is_active.is_(True)).all())
    return {"courses": courses, "shifts": SHIFTS, "grades": GRADES, "statuses": STATUSES, "weekdays": svc.WEEKDAYS,
            "supervisor_options": [(u.id, f"{u.full_name} — {u.role.name if u.role else ''}") for u in sups],
            "languages": ["English", "Urdu", "Arabic", "Bengali", "Pashto", "French"], "t": t}


@router.get("", include_in_schema=False)
def list_teachers(request: Request, page: int = 1, q: str = "", shift: str = "", gender: str = "", grade: str = "",
                  course: str = "", verified: str = "", db: Session = Depends(get_db),
                  user: User = Depends(require("teachers.view"))):
    query = db.query(Teacher)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Teacher.full_name.ilike(like), Teacher.teacher_code.ilike(like)))
    if shift:
        query = query.filter(Teacher.shift == shift)
    if gender:
        query = query.filter(Teacher.gender == gender)
    if grade:
        query = query.filter(Teacher.grade == grade)
    if verified in ("yes", "no"):
        query = query.filter(Teacher.is_verified.is_(verified == "yes"))
    rows = query.order_by(Teacher.full_name).all()
    if course:
        rows = [t for t in rows if course in (t.courses or [])]
    per, page = 25, max(1, page)
    start = (page - 1) * per
    pg = Page(rows[start:start + per], len(rows), page, per)
    all_t = db.query(Teacher).all()
    loads = dict(db.query(Student.teacher_id, func.count(Student.id))
                 .filter(Student.status.in_(["active", "trial", "free"])).group_by(Student.teacher_id).all())
    qa_scores = [t.qa_score_avg for t in all_t if t.qa_score_avg]
    stats = {"total": len(all_t), "verified": sum(1 for t in all_t if t.is_verified),
             "unverified": sum(1 for t in all_t if not t.is_verified),
             "grade_a": sum(1 for t in all_t if t.grade == "A"), "grade_b": sum(1 for t in all_t if t.grade == "B"),
             "grade_c": sum(1 for t in all_t if t.grade == "C"),
             "avg_qa": round(sum(qa_scores) / len(qa_scores), 1) if qa_scores else 0.0,
             "students": sum(loads.values())}
    courses = db.query(Course).order_by(Course.order).all()
    base = f"/teachers?q={q}&shift={shift}&gender={gender}&grade={grade}&course={course}&verified={verified}"
    return render(request, "teachers/list.html", {"user": user, "page": pg, "q": q, "shift": shift, "gender": gender,
                                                  "grade": grade, "course": course, "verified": verified, "stats": stats,
                                                  "shifts": SHIFTS, "grades": GRADES, "loads": loads,
                                                  "course_options": [(c.code, c.name) for c in courses], "base_url": base})


@router.get("/new", include_in_schema=False)
def new_teacher(request: Request, db: Session = Depends(get_db), user: User = Depends(require("teachers.add"))):
    return render(request, "teachers/form.html", {"user": user, **_form_context(db), "mode": "new"})


def _read_form(form) -> dict:
    availability = {}
    for d in svc.WEEKDAYS:
        rng = (form.get(f"avail_{d}") or "").strip()
        if rng:
            availability[d] = [rng]
    return {"full_name": form.get("full_name"), "email": form.get("email"), "phone": form.get("phone"),
            "gender": form.get("gender") or "male", "cnic": form.get("cnic"), "qualifications": form.get("qualifications"),
            "courses": form.getlist("courses"), "languages": form.getlist("languages") or ["English", "Urdu"],
            "shift": form.get("shift") or "evening", "availability": availability,
            "max_classes_per_day": parse_int(form.get("max_classes_per_day"), 12),
            "supervisor_id": form.get("supervisor_id"), "per_class_rate": parse_float(form.get("per_class_rate"), 250),
            "base_salary": parse_float(form.get("base_salary"), 0), "grade": form.get("grade") or "B",
            "bio": form.get("bio"), "join_date": parse_date(form.get("join_date")) or date.today(),
            "employment_type": form.get("employment_type") or "full_time"}


@router.post("/new", include_in_schema=False)
async def create_teacher(request: Request, db: Session = Depends(get_db), user: User = Depends(require("teachers.add"))):
    form = await request.form()
    data = _read_form(form)
    if not (data["full_name"] or "").strip():
        return redirect("/teachers/new", "Full name is required.", "error")
    try:
        t, pwd = svc.create_teacher_full(db, data, user, request=request)
    except ValueError as exc:
        return redirect("/teachers/new", str(exc), "error")
    db.commit()
    return render(request, "teachers/created.html", {"user": user, "t": t, "password": pwd, "login_email": t.user.email if t.user else data["email"]})


@router.get("/{id}", include_in_schema=False)
def teacher_detail(id: int, request: Request, tab: str = "overview", db: Session = Depends(get_db),
                   user: User = Depends(require("teachers.view"))):
    t = _get(db, id)
    today = date.today()
    ctx: dict = {"user": user, "t": t, "tab": tab, "tabs": [(k, l, f"/teachers/{t.id}?tab={k}") for k, l in TABS],
                 "can_approve": rbac.has_permission(user, "teachers.approve"), "grades": GRADES, "statuses": STATUSES,
                 "load": db.query(func.count(Student.id)).filter(Student.teacher_id == t.id, Student.status.in_(["active", "trial", "free"])).scalar() or 0,
                 "stats30": teacher_stats(db, t.id, today - timedelta(days=30))}
    if tab == "overview":
        ctx["emp"] = t.employee
        ctx["open_actions"] = db.query(func.count(CorrectiveAction.id)).filter(CorrectiveAction.teacher_id == t.id, CorrectiveAction.status.in_(["open", "in_progress", "overdue"])).scalar() or 0
        ctx["complaints"] = db.query(func.count(Case.id)).filter(Case.teacher_id == t.id).scalar() or 0
        ctx["recent_audit"] = db.query(AuditEvent).filter(AuditEvent.entity_type == "Teacher", AuditEvent.entity_id == t.id).order_by(AuditEvent.created_at.desc()).limit(6).all()
        ctx["matches"] = db.query(func.count(TeacherMatch.id)).filter(TeacherMatch.chosen_teacher_id == t.id).scalar() or 0
    elif tab == "students":
        ctx["students"] = db.query(Student).filter(Student.teacher_id == t.id).order_by(Student.status, Student.full_name).all()
    elif tab == "schedule":
        ctx["schedules"] = db.query(Schedule).filter(Schedule.teacher_id == t.id, Schedule.status == "active").order_by(Schedule.start_time).all()
        ctx["today_sessions"] = db.query(ClassSession).filter(ClassSession.teacher_id == t.id, ClassSession.date == today).order_by(ClassSession.start_time).all()
    elif tab == "performance":
        ctx["stats90"] = teacher_stats(db, t.id, today - timedelta(days=90))
        labels, done, missed = [], [], []
        for w in range(7, -1, -1):
            start = today - timedelta(days=today.weekday() + 7 * w)
            end = start + timedelta(days=6)
            rows = db.query(ClassSession.status, func.count(ClassSession.id)).filter(
                ClassSession.teacher_id == t.id, ClassSession.date >= start, ClassSession.date <= end).group_by(ClassSession.status).all()
            d = dict(rows)
            labels.append(start.strftime("%d %b"))
            done.append(d.get("done", 0))
            missed.append(d.get("missed", 0))
        ctx["chart"] = {"labels": labels, "done": done, "missed": missed}
        ai_rows = db.query(func.avg(AIClassAnalysis.overall_score)).filter(AIClassAnalysis.teacher_id == t.id).scalar()
        ctx["ai_score"] = round(float(ai_rows), 1) if ai_rows else None
        ctx["complaints"] = db.query(Case).filter(Case.teacher_id == t.id).order_by(Case.created_at.desc()).limit(20).all()
        ctx["actions"] = db.query(CorrectiveAction).filter(CorrectiveAction.teacher_id == t.id).order_by(CorrectiveAction.created_at.desc()).limit(20).all()
    elif tab == "qa":
        ctx["reviews"] = db.query(QAReview).filter(QAReview.teacher_id == t.id).order_by(QAReview.created_at.desc()).limit(50).all()
        ctx["analyses"] = db.query(AIClassAnalysis).filter(AIClassAnalysis.teacher_id == t.id).order_by(AIClassAnalysis.created_at.desc()).limit(20).all()
    elif tab == "training":
        ctx["training"] = db.query(TrainingAssignment).filter(TrainingAssignment.teacher_id == t.id).order_by(TrainingAssignment.created_at.desc()).all()
    elif tab == "income":
        start, end = month_bounds(month_key())
        classes_done = db.query(func.count(ClassSession.id)).filter(ClassSession.teacher_id == t.id, ClassSession.status == "done",
                                                                    ClassSession.date >= start, ClassSession.date <= end).scalar() or 0
        rate = float(t.per_class_rate or 0)
        ctx["income"] = {"period": month_key(), "classes": classes_done, "rate": rate, "class_pay": round(classes_done * rate, 2)}
        ctx["structure"] = db.query(SalaryStructure).filter(SalaryStructure.employee_id == t.employee_id).first() if t.employee_id else None
        ctx["payslips"] = db.query(Payslip).filter(Payslip.employee_id == t.employee_id).order_by(Payslip.created_at.desc()).limit(12).all() if t.employee_id else []
    elif tab == "audit":
        ctx["events"] = (db.query(AuditEvent).filter(or_((AuditEvent.entity_type == "Teacher") & (AuditEvent.entity_id == t.id),
                                                         (AuditEvent.entity_type == "Employee") & (AuditEvent.entity_id == (t.employee_id or -1))))
                         .order_by(AuditEvent.created_at.desc()).limit(200).all())
    return render(request, "teachers/detail.html", ctx)


@router.get("/{id}/edit", include_in_schema=False)
def edit_teacher(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("teachers.update"))):
    t = _get(db, id)
    return render(request, "teachers/form.html", {"user": user, **_form_context(db, t), "mode": "edit"})


@router.post("/{id}/edit", include_in_schema=False)
async def update_teacher(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("teachers.update"))):
    t = _get(db, id)
    form = await request.form()
    data = _read_form(form)
    before = snapshot(t)
    t.full_name = (data["full_name"] or t.full_name).strip()
    t.gender = data["gender"]
    t.qualifications = data["qualifications"] or None
    t.courses = data["courses"]
    t.languages = data["languages"]
    t.shift = data["shift"]
    t.availability = data["availability"] or t.availability
    t.max_classes_per_day = data["max_classes_per_day"] or 12
    t.supervisor_id = parse_int(data["supervisor_id"])
    t.per_class_rate = data["per_class_rate"]
    t.bio = data["bio"] or None
    t.status = form.get("status") or t.status
    if form.get("grade") and form.get("grade") != t.grade and rbac.has_permission(user, "teachers.approve"):
        t.grade = form.get("grade")
        t.grade_computed_at = datetime.utcnow()
    if t.user:
        t.user.full_name = t.full_name
        t.user.phone = data["phone"] or t.user.phone
    if t.employee:
        t.employee.full_name = t.full_name
        t.employee.gender = t.gender
        t.employee.phone = data["phone"] or t.employee.phone
        t.employee.shift = t.shift
    log_action(db, user, "update", "teachers", entity=t, description=f"Teacher {t.teacher_code} updated",
               before=before, after=snapshot(t), request=request)
    db.commit()
    return redirect(f"/teachers/{t.id}", "Teacher updated.")


@router.post("/{id}/verify", include_in_schema=False)
async def verify_teacher(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("teachers.approve"))):
    t = _get(db, id)
    form = await request.form()
    rationale = (form.get("rationale") or form.get("reason") or "").strip()
    if not rationale:
        return redirect(f"/teachers/{t.id}", "A rationale is required to change the safeguarding verification state.", "error")
    make_verified = parse_bool(form.get("verified"))
    before = {"is_verified": t.is_verified, "background_check_status": t.employee.background_check_status if t.employee else None}
    t.is_verified = make_verified
    if t.employee:
        t.employee.background_check_status = "verified" if make_verified else "pending"
        t.employee.background_check_date = date.today() if make_verified else None
        if make_verified and t.employee.status == "probation":
            t.employee.status = "active"
    if not make_verified:
        n = db.query(func.count(Student.id)).filter(Student.teacher_id == t.id).scalar() or 0
        if n:
            log_action(db, user, "safeguarding_access", "teachers", entity=t, severity="warning", request=request,
                       description=f"Verification withdrawn while {n} students are still assigned — reassignment required.")
    log_action(db, user, "approve" if make_verified else "revoke", "teachers", entity=t, rationale=rationale,
               description=f"Teacher {t.teacher_code} background check {'VERIFIED' if make_verified else 'WITHDRAWN'}",
               before=before, after={"is_verified": make_verified}, request=request, severity="warning", consequential=True)
    if t.user_id:
        notify(db, t.user_id, "Verification status updated",
               "Your background check has been verified — you can now be assigned live classes." if make_verified
               else "Your verification has been withdrawn. You cannot be assigned live classes until it is restored.",
               event_type="verification", link="/teacher")
    db.commit()
    return redirect(f"/teachers/{t.id}", f"Teacher {'verified' if make_verified else 'un-verified'}.",
                    "success" if make_verified else "warning")


@router.post("/{id}/status", include_in_schema=False)
async def change_status(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("teachers.update"))):
    t = _get(db, id)
    form = await request.form()
    new_status = form.get("status")
    reason = (form.get("reason") or "").strip()
    if new_status not in STATUSES:
        return redirect(f"/teachers/{t.id}", "Invalid status.", "error")
    if not reason:
        return redirect(f"/teachers/{t.id}", "A reason is required.", "error")
    before = {"status": t.status}
    t.status = new_status
    log_action(db, user, "status_change", "teachers", entity=t, rationale=reason, before=before, after={"status": new_status},
               description=f"Teacher {t.teacher_code} status {before['status']} -> {new_status}", request=request, consequential=True)
    db.commit()
    return redirect(f"/teachers/{t.id}", f"Teacher marked {new_status}.")


@router.post("/{id}/training", include_in_schema=False)
async def assign_training(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("teacher_dev.add"))):
    t = _get(db, id)
    form = await request.form()
    title = (form.get("title") or "").strip()
    if not title:
        return redirect(f"/teachers/{t.id}?tab=training", "A title is required.", "error")
    ta = TrainingAssignment(teacher_id=t.id, title=title, category=form.get("category") or "tajweed",
                            description=form.get("description") or None, assigned_by_id=user.id,
                            due_date=parse_date(form.get("due_date")), status="assigned",
                            is_promotion_gate=parse_bool(form.get("is_promotion_gate")))
    db.add(ta)
    db.flush()
    log_action(db, user, "create", "teacher_dev", entity=ta, description=f"Training '{title}' assigned to {t.teacher_code}", request=request)
    if t.user_id:
        notify(db, t.user_id, "New training assigned", f"{title} — due {ta.due_date or 'no deadline'}", event_type="training", link="/teacher/training")
    db.commit()
    return redirect(f"/teachers/{t.id}?tab=training", "Training assigned.")
