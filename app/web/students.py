"""Students (Module 7 + Module 44 teacher-match): list, create with teacher recommendation, detail tabs, status changes, teacher change."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import require, csrf_protect, get_current_user, PermissionDenied
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import redirect, paginate, parse_date, parse_int, parse_bool
from app.database import get_db
from app.models.academic import Course, Division, Evaluation, MonthlyTest, Certificate, DorSchedule, LessonPlan
from app.models.core import User, AuditEvent
from app.models.crm import Case, RetentionAction
from app.models.finance import Subscription, Invoice
from app.models.people import Client, Student, Teacher, Leave
from app.models.scheduling import Schedule, ClassSession, Attendance, TeacherMatch
from app.services import people as svc
from app.services.classes import student_attendance_pct

router = APIRouter(prefix="/students", dependencies=[Depends(csrf_protect)])

STATUSES = ["trial", "active", "frozen", "cancelled", "graduated", "free"]
TABS = [("overview", "Overview"), ("schedule", "Schedule & Classes"), ("attendance", "Attendance"), ("progress", "Progress"), ("evaluations", "Evaluations & Tests"),
        ("leaves", "Leaves"), ("billing", "Billing"), ("cases", "Cases"), ("certificates", "Certificates"), ("matches", "Teacher match"),
        ("retention", "Retention"), ("audit", "Audit")]


def _get(db: Session, id: int) -> Student:
    s = db.query(Student).get(id)
    if not s:
        raise HTTPException(404, "Student not found")
    return s


def _form_context(db: Session) -> dict:
    courses = db.query(Course).filter(Course.is_active.is_(True)).order_by(Course.order).all()
    divisions = db.query(Division).order_by(Division.course_id, Division.order).all()
    clients = db.query(Client).filter(Client.status != "churned").order_by(Client.full_name).all()
    return {"courses": courses, "course_options": [(c.id, f"{c.code} - {c.name}") for c in courses],
            "divisions": [{"id": d.id, "course_id": d.course_id, "name": d.name} for d in divisions],
            "client_options": [(c.id, f"{c.client_code} - {c.full_name} ({c.country})") for c in clients],
            "countries": svc.COUNTRY_NAMES, "timezones": svc.TIMEZONES, "statuses": STATUSES, "languages": ["English", "Urdu", "Arabic", "Bengali", "Other"]}


@router.get("", include_in_schema=False)
def list_students(request: Request, page: int = 1, q: str = "", status: str = "", course: str = "", teacher: str = "", risk: str = "",
                  db: Session = Depends(get_db), user: User = Depends(require("students.view"))):
    query = db.query(Student)
    if q:
        like = f"%{q}%"
        query = query.join(Client, Student.client_id == Client.id).filter(or_(Student.full_name.ilike(like), Student.student_code.ilike(like), Client.full_name.ilike(like), Client.client_code.ilike(like)))
    if status:
        query = query.filter(Student.status == status)
    if course:
        query = query.filter(Student.course_id == int(course))
    if teacher:
        query = query.filter(Student.teacher_id == int(teacher))
    if risk:
        query = query.filter(Student.risk_level == risk)
    pg = paginate(query.order_by(Student.created_at.desc()), page, 25)
    counts = dict(db.query(Student.status, func.count(Student.id)).group_by(Student.status).all())
    stats = {k: counts.get(k, 0) for k in STATUSES}
    stats["total"] = sum(counts.values())
    stats["high_risk"] = db.query(func.count(Student.id)).filter(Student.risk_level == "high", Student.status.in_(["active", "trial"])).scalar() or 0
    courses = db.query(Course).order_by(Course.order).all()
    teachers = db.query(Teacher).order_by(Teacher.full_name).all()
    base = f"/students?q={q}&status={status}&course={course}&teacher={teacher}&risk={risk}"
    return render(request, "students/list.html", {"user": user, "page": pg, "q": q, "status": status, "course": course, "teacher": teacher, "risk": risk,
                                                  "stats": stats, "statuses": STATUSES, "course_options": [(c.id, c.name) for c in courses],
                                                  "teacher_options": [(t.id, t.full_name) for t in teachers], "base_url": base})


def _recommend_from_params(db: Session, params) -> tuple[list[dict], dict]:
    course = db.query(Course).get(int(params.get("course_id"))) if params.get("course_id") else None
    age = parse_int(params.get("age"))
    dob = parse_date(params.get("date_of_birth"))
    if age is None and dob:
        age = svc.age_from_dob(dob)
    tz = params.get("timezone") or "Europe/London"
    gender = params.get("gender") or "male"
    ranked = svc.recommend_teachers(db, course.code if course else None, gender, age, tz, preferred_shift=params.get("preferred_shift") or None)
    return ranked, {"course": course, "age": age, "tz": tz, "gender": gender}


@router.get("/new", include_in_schema=False)
def new_student(request: Request, db: Session = Depends(get_db), user: User = Depends(require("students.add"))):
    params = request.query_params
    ranked, meta = ([], {}) if not params.get("course_id") else _recommend_from_params(db, params)
    client = db.query(Client).get(int(params["client_id"])) if params.get("client_id") else None
    values = {k: params.get(k, "") for k in ("client_id", "full_name", "gender", "date_of_birth", "age", "course_id", "division_id", "level", "timezone",
                                            "preferred_language", "status", "preferred_shift", "notes", "teacher_id", "new_client_name", "new_client_email",
                                            "new_client_phone", "new_client_country", "sabaq_position")}
    if client and not values["timezone"]:
        values["timezone"] = client.timezone
    return render(request, "students/form.html", {"user": user, **_form_context(db), "mode": "new", "values": values, "ranked": ranked, "meta": meta,
                                                  "student": None, "client": client})


def _read_student_form(form) -> dict:
    return {"full_name": form.get("full_name"), "gender": form.get("gender"), "date_of_birth": parse_date(form.get("date_of_birth")), "age": form.get("age"),
            "course_id": form.get("course_id"), "division_id": form.get("division_id"), "level": form.get("level"), "timezone": form.get("timezone"),
            "preferred_language": form.get("preferred_language"), "status": form.get("status"), "notes": form.get("notes"),
            "guardian_consent": parse_bool(form.get("guardian_consent")), "sabaq_position": form.get("sabaq_position")}


@router.post("/new", include_in_schema=False)
async def create_student(request: Request, db: Session = Depends(get_db), user: User = Depends(require("students.add"))):
    form = await request.form()
    data = _read_student_form(form)
    if not (data["full_name"] or "").strip():
        return redirect("/students/new", "Student name is required.", "error")
    client = db.query(Client).get(int(form["client_id"])) if form.get("client_id") else None
    if not client:
        if not (form.get("new_client_name") or "").strip():
            return redirect("/students/new", "Select an existing client or enter the new parent's name.", "error")
        if not rbac.has_permission(user, "clients.add"):
            raise PermissionDenied("clients.add")
        client, _pwd = svc.create_client_with_portal(db, {"full_name": form.get("new_client_name"), "email": form.get("new_client_email"),
                                                          "phone": form.get("new_client_phone"), "country": form.get("new_client_country") or "United Kingdom",
                                                          "timezone": form.get("timezone"), "consent_given": parse_bool(form.get("guardian_consent")),
                                                          "status": "trial", "source": "Manual"}, user, request=request, with_portal=True)
    # teacher decision
    ranked, _meta = _recommend_from_params(db, form)
    teacher_id = parse_int(form.get("teacher_id"))
    teacher = db.query(Teacher).get(teacher_id) if teacher_id else None
    if teacher and not teacher.is_verified:
        return redirect("/students/new", f"{teacher.full_name} is not verified and cannot be assigned live classes.", "error")
    top_id = ranked[0]["teacher_id"] if ranked else None
    override_reason = (form.get("override_reason") or "").strip()
    overridden = bool(teacher and top_id and teacher.id != top_id)
    if overridden and not override_reason:
        return redirect("/students/new?" + "&".join(f"{k}={v}" for k, v in form.items() if k != "override_reason"),
                        "You chose a teacher other than the top recommendation: an override reason is required.", "error")
    s = svc.create_student(db, client, data, user, request=request, with_portal=parse_bool(form.get("create_portal")))
    if teacher or ranked:
        svc.assign_teacher(db, s, teacher, user, reason=override_reason or None, overridden=overridden, ranked=ranked, request=request)
        if teacher and teacher.user_id:
            notify(db, teacher.user_id, "New student assigned", f"{s.full_name} ({s.course.name if s.course else 'course TBD'}) has been enrolled with you.",
                   event_type="teacher_change", link="/teacher/students")
    if client.user_id:
        notify(db, client.user_id, f"{s.full_name} enrolled", f"Welcome! {s.full_name} is enrolled ({s.status}). Teacher: {teacher.full_name if teacher else 'to be assigned'}.",
               event_type="enrolment", link="/portal")
    db.commit()
    return redirect(f"/students/{s.id}", f"Student {s.student_code} created" + (f" and matched with {teacher.full_name}." if teacher else "."))


@router.get("/{id}", include_in_schema=False)
def student_detail(id: int, request: Request, tab: str = "overview", db: Session = Depends(get_db), user: User = Depends(require("students.view"))):
    s = _get(db, id)
    today = date.today()
    ctx: dict = {"user": user, "s": s, "tab": tab, "tabs": [(k, l, f"/students/{s.id}?tab={k}") for k, l in TABS],
                 "attendance_pct": student_attendance_pct(db, s.id, 30), "statuses": STATUSES,
                 "show_contact": rbac.has_permission(user, "clients.update") or rbac.is_management(user)}
    if tab == "overview":
        ctx["next_session"] = db.query(ClassSession).filter(ClassSession.student_id == s.id, ClassSession.status == "pending", ClassSession.scheduled_start >= datetime.utcnow() - timedelta(hours=5)).order_by(ClassSession.scheduled_start).first()
        ctx["schedules"] = db.query(Schedule).filter(Schedule.student_id == s.id, Schedule.status == "active").all()
        ctx["dor"] = db.query(DorSchedule).filter(DorSchedule.student_id == s.id).order_by(DorSchedule.period.desc()).first()
        ctx["progress"] = svc.student_progress_summary(db, s)
        ctx["last_match"] = db.query(TeacherMatch).filter(TeacherMatch.student_id == s.id).order_by(TeacherMatch.created_at.desc()).first()
        ctx["open_cases"] = db.query(Case).filter(Case.student_id == s.id, Case.status.in_(["open", "in_progress", "waiting", "escalated"])).count()
        ctx["recent_audit"] = db.query(AuditEvent).filter(AuditEvent.entity_type == "Student", AuditEvent.entity_id == s.id).order_by(AuditEvent.created_at.desc()).limit(6).all()
    elif tab == "schedule":
        ctx["schedules"] = db.query(Schedule).filter(Schedule.student_id == s.id).order_by(Schedule.status, Schedule.start_time).all()
        ctx["upcoming"] = db.query(ClassSession).filter(ClassSession.student_id == s.id, ClassSession.date >= today).order_by(ClassSession.scheduled_start).limit(30).all()
        ctx["history"] = db.query(ClassSession).filter(ClassSession.student_id == s.id, ClassSession.date < today).order_by(ClassSession.scheduled_start.desc()).limit(60).all()
        counts = dict(db.query(ClassSession.status, func.count(ClassSession.id)).filter(ClassSession.student_id == s.id).group_by(ClassSession.status).all())
        ctx["session_counts"] = counts
    elif tab == "attendance":
        since = today - timedelta(days=90)
        ctx["rows"] = db.query(Attendance).filter(Attendance.student_id == s.id, Attendance.date >= since).order_by(Attendance.date.desc()).all()
        ctx["pct_30"] = student_attendance_pct(db, s.id, 30)
        ctx["pct_90"] = student_attendance_pct(db, s.id, 90)
        # weekly chart (last 8 weeks)
        labels, present, absent = [], [], []
        for w in range(7, -1, -1):
            start = today - timedelta(days=today.weekday() + 7 * w)
            end = start + timedelta(days=6)
            rows = [r for r in ctx["rows"] if start <= r.date <= end]
            labels.append(start.strftime("%d %b"))
            present.append(sum(1 for r in rows if r.student_status in ("present", "late")))
            absent.append(sum(1 for r in rows if r.student_status == "absent"))
        ctx["chart"] = {"labels": labels, "present": present, "absent": absent}
    elif tab == "progress":
        ctx["progress"] = svc.student_progress_summary(db, s)
        ctx["plans"] = db.query(LessonPlan).filter(LessonPlan.student_id == s.id).order_by(LessonPlan.plan_date.desc()).limit(20).all()
    elif tab == "evaluations":
        ctx["evaluations"] = db.query(Evaluation).filter(Evaluation.student_id == s.id).order_by(Evaluation.date.desc()).all()
        ctx["tests"] = db.query(MonthlyTest).filter(MonthlyTest.student_id == s.id).order_by(MonthlyTest.period.desc()).all()
    elif tab == "leaves":
        ctx["leaves"] = db.query(Leave).filter(Leave.person_type == "student", Leave.student_id == s.id).order_by(Leave.start_date.desc()).all()
        ctx["can_approve"] = rbac.has_permission(user, "leaves.approve")
    elif tab == "billing":
        ctx["subscriptions"] = db.query(Subscription).filter(Subscription.student_id == s.id).order_by(Subscription.created_at.desc()).all()
        ctx["invoices"] = db.query(Invoice).filter(Invoice.student_id == s.id).order_by(Invoice.issue_date.desc()).limit(50).all()
        ctx["balance"] = svc.client_balance(db, s.client) if s.client else 0
    elif tab == "cases":
        ctx["cases"] = db.query(Case).filter(Case.student_id == s.id).order_by(Case.created_at.desc()).all()
    elif tab == "certificates":
        ctx["certificates"] = db.query(Certificate).filter(Certificate.student_id == s.id).order_by(Certificate.issued_at.desc()).all()
    elif tab == "matches":
        ctx["matches"] = db.query(TeacherMatch).filter(TeacherMatch.student_id == s.id).order_by(TeacherMatch.created_at.desc()).all()
        ctx["teacher_names"] = {t.id: t.full_name for t in db.query(Teacher)}
        ctx["user_names"] = {u.id: u.full_name for u in db.query(User)}
    elif tab == "retention":
        ctx["actions"] = db.query(RetentionAction).filter(RetentionAction.student_id == s.id).order_by(RetentionAction.created_at.desc()).all()
    elif tab == "audit":
        ctx["events"] = db.query(AuditEvent).filter(AuditEvent.entity_type == "Student", AuditEvent.entity_id == s.id).order_by(AuditEvent.created_at.desc()).limit(200).all()
    return render(request, "students/detail.html", ctx)


@router.get("/{id}/edit", include_in_schema=False)
def edit_student(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("students.update"))):
    s = _get(db, id)
    values = {"client_id": s.client_id, "full_name": s.full_name, "gender": s.gender, "date_of_birth": s.date_of_birth.isoformat() if s.date_of_birth else "",
              "age": s.age or "", "course_id": s.course_id or "", "division_id": s.division_id or "", "level": s.level or "", "timezone": s.timezone,
              "preferred_language": s.preferred_language, "status": s.status, "notes": s.notes or "", "teacher_id": s.teacher_id or "",
              "sabaq_position": s.sabaq_position or "", "preferred_shift": ""}
    return render(request, "students/form.html", {"user": user, **_form_context(db), "mode": "edit", "values": values, "ranked": [], "meta": {}, "student": s, "client": s.client})


@router.post("/{id}/edit", include_in_schema=False)
async def update_student(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("students.update"))):
    s = _get(db, id)
    form = await request.form()
    data = _read_student_form(form)
    before = snapshot(s)
    s.full_name = (data["full_name"] or s.full_name).strip()
    s.gender = data["gender"] or s.gender
    s.date_of_birth = data["date_of_birth"]
    age = parse_int(data["age"])
    if age is None and s.date_of_birth:
        age = svc.age_from_dob(s.date_of_birth)
    s.age = age
    s.is_minor = age is None or age < 18
    s.course_id = parse_int(data["course_id"])
    s.division_id = parse_int(data["division_id"])
    div = db.query(Division).get(s.division_id) if s.division_id else None
    s.level = data["level"] or (div.name if div else None)
    s.timezone = data["timezone"] or s.timezone
    s.preferred_language = data["preferred_language"] or s.preferred_language
    s.notes = data["notes"] or None
    s.sabaq_position = data["sabaq_position"] or s.sabaq_position
    s.guardian_consent = data["guardian_consent"]
    s.dor_quota_met = parse_bool(form.get("dor_quota_met")) if form.get("dor_quota_met") is not None else s.dor_quota_met
    if form.get("client_id") and int(form["client_id"]) != s.client_id and rbac.has_permission(user, "students.assign"):
        s.client_id = int(form["client_id"])
    if s.user:
        s.user.full_name = s.full_name
        s.user.timezone = s.timezone
    log_action(db, user, "update", "students", entity=s, description=f"Student {s.student_code} updated", before=before, after=snapshot(s), request=request)
    db.commit()
    return redirect(f"/students/{s.id}", "Student updated.")


@router.post("/{id}/status", include_in_schema=False)
async def change_status(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("students.update"))):
    s = _get(db, id)
    form = await request.form()
    new_status = form.get("status")
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if new_status not in STATUSES:
        return redirect(f"/students/{s.id}", "Invalid status.", "error")
    if not reason:
        return redirect(f"/students/{s.id}", "A reason is required for status changes.", "error")
    if new_status == s.status:
        return redirect(f"/students/{s.id}", f"Student is already {new_status}.", "info")
    svc.student_status_change(db, s, new_status, user, reason, request=request)
    if new_status in ("cancelled", "frozen"):
        try:
            from app.services.retention import on_student_status_change  # type: ignore
            on_student_status_change(db, s, new_status, reason)
        except Exception:
            pass
    db.commit()
    return redirect(f"/students/{s.id}", f"Student marked {new_status}. Parent notified.")


@router.get("/{id}/change-teacher", include_in_schema=False)
def change_teacher_page(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("students.assign", "students.update", any_of=True))):
    s = _get(db, id)
    ranked = svc.recommend_teachers(db, s.course.code if s.course else None, s.gender, s.age, s.timezone, exclude_ids=[s.teacher_id] if s.teacher_id else ())
    return render(request, "students/change_teacher.html", {"user": user, "s": s, "ranked": ranked})


@router.post("/{id}/change-teacher", include_in_schema=False)
async def change_teacher(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("students.assign", "students.update", any_of=True))):
    s = _get(db, id)
    form = await request.form()
    teacher = db.query(Teacher).get(parse_int(form.get("teacher_id")) or 0)
    reason = (form.get("reason") or "").strip()
    if not teacher:
        return redirect(f"/students/{s.id}/change-teacher", "Choose a teacher.", "error")
    if not reason:
        return redirect(f"/students/{s.id}/change-teacher", "A reason for the teacher change is required.", "error")
    if not teacher.is_verified:
        return redirect(f"/students/{s.id}/change-teacher", f"{teacher.full_name} is not verified and cannot be assigned live classes.", "error")
    old = s.teacher
    ranked = svc.recommend_teachers(db, s.course.code if s.course else None, s.gender, s.age, s.timezone, exclude_ids=[s.teacher_id] if s.teacher_id else ())
    try:
        svc.assign_teacher(db, s, teacher, user, reason=reason, ranked=ranked, request=request)
    except ValueError as exc:
        return redirect(f"/students/{s.id}/change-teacher", str(exc), "error")
    changed = svc.propagate_teacher_change(db, s, old, teacher, user, reason)
    log_action(db, user, "teacher_change", "students", entity=s, rationale=reason, consequential=True, request=request,
               description=f"Teacher changed {old.full_name if old else 'none'} -> {teacher.full_name}; propagated to {changed['schedules']} schedules, {changed['sessions']} sessions, {changed['subscriptions']} subscriptions")
    db.commit()
    return redirect(f"/students/{s.id}?tab=matches", f"Teacher changed to {teacher.full_name}. Old/new teacher and parent notified; {changed['sessions']} upcoming sessions moved.")


@router.post("/{id}/leaves/{leave_id}/{action}", include_in_schema=False)
async def leave_action(id: int, leave_id: int, action: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.approve"))):
    s = _get(db, id)
    lv = db.query(Leave).filter(Leave.id == leave_id, Leave.student_id == s.id).first()
    if not lv or action not in ("approve", "reject"):
        raise HTTPException(404)
    form = await request.form()
    lv.status = "approved" if action == "approve" else "rejected"
    lv.approved_by_id = user.id
    lv.approved_at = datetime.utcnow()
    log_action(db, user, action, "leaves", entity=lv, description=f"Student leave {lv.start_date}..{lv.end_date} {lv.status}", rationale=form.get("reason"), request=request)
    if lv.status == "approved":
        for cs in db.query(ClassSession).filter(ClassSession.student_id == s.id, ClassSession.status == "pending", ClassSession.date >= lv.start_date, ClassSession.date <= lv.end_date):
            from app.services.classes import set_status
            set_status(db, cs, "leave", user, reason="Approved student leave", request=request, notify_parties=False)
    if s.client and s.client.user_id:
        notify(db, s.client.user_id, f"Leave request {lv.status}", f"Leave for {s.full_name} from {lv.start_date} to {lv.end_date} was {lv.status}.", event_type="leave", link="/portal/leaves")
    if s.teacher and s.teacher.user_id:
        notify(db, s.teacher.user_id, f"Student leave {lv.status}", f"{s.full_name}: {lv.start_date} to {lv.end_date}.", event_type="leave", link="/teacher/students")
    db.commit()
    return redirect(f"/students/{s.id}?tab=leaves", f"Leave {lv.status}.")


@router.post("/{id}/leaves", include_in_schema=False)
async def add_leave(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.add"))):
    s = _get(db, id)
    form = await request.form()
    start, end = parse_date(form.get("start_date")), parse_date(form.get("end_date"))
    if not start or not end or end < start:
        return redirect(f"/students/{s.id}?tab=leaves", "Valid start and end dates are required.", "error")
    lv = Leave(person_type="student", student_id=s.id, leave_type=form.get("leave_type") or "vacation", start_date=start, end_date=end, reason=form.get("reason"),
               status="pending", requested_by_id=user.id)
    db.add(lv)
    db.flush()
    log_action(db, user, "create", "leaves", entity=lv, description=f"Student leave requested for {s.student_code}", request=request)
    db.commit()
    return redirect(f"/students/{s.id}?tab=leaves", "Leave request recorded (pending approval).")
