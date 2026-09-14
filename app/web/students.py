"""Students (Module 7 + Module 44 teacher-match + ERP "Student Management"): Student List (with the Free Students saved
report), Student Referred List, On Leave Students, Student Form (Basic Detail / View Subscriptions), status changes, teacher change."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import require, csrf_protect, get_current_user, PermissionDenied
from app.core.notify import notify
from app.core.templating import render, label as status_label
from app.core.utils import redirect, paginate, parse_date, parse_int, parse_bool
from app.database import get_db
from app.models.academic import Course, Division, Evaluation, MonthlyTest, Certificate, DorSchedule, LessonPlan
from app.models.core import User, AuditEvent
from app.models.crm import Case, RetentionAction
from app.models.erp import ReferredContact
from app.models.finance import Subscription, Invoice
from app.models.people import Client, Student, Teacher, Leave
from app.models.scheduling import Schedule, ClassSession, Attendance, TeacherMatch
from app.services import people as svc
from app.services.classes import student_attendance_pct

router = APIRouter(prefix="/students", dependencies=[Depends(csrf_protect)])

STATUSES = ["trial", "active", "frozen", "cancelled", "graduated", "free"]
# ERP tile vocabulary -> internal status (the list filter accepts either)
STATUS_ALIASES = {"trail": "trial", "regular": "active", "drop_out": "cancelled", "dropout": "cancelled", "pass_out": "graduated",
                  "blacklist": "black_list"}
TILES = [("Trial", "trial", "flask-conical"), ("Regular", "active", "user-check"), ("Drop Out", "cancelled", "user-x"),
         ("Black List", "black_list", "ban"), ("On Leave", "on_leave", "plane")]
FAMILY_STATUSES = [("trial", "Trial"), ("active", "Regular"), ("churned", "Drop Out"), ("inactive", "Black List"), ("on_leave", "On Leave")]
SHIFTS = [("morning", "Morning"), ("night", "Night")]
REPORTS = [("primary", "Primary Report"), ("free", "Free Students List")]
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TABS = [("overview", "Student Form"), ("basic", "Basic Detail"), ("subscriptions", "View Subscriptions"), ("schedule", "Schedule & Classes"),
        ("attendance", "Attendance"), ("progress", "Progress"), ("evaluations", "Evaluations & Tests"),
        ("leaves", "Leaves"), ("billing", "Billing"), ("cases", "Cases"), ("certificates", "Certificates"), ("matches", "Teacher match"),
        ("retention", "Retention"), ("audit", "Audit")]


def _get(db: Session, id: int) -> Student:
    s = db.query(Student).get(id)
    if not s:
        raise HTTPException(404, "Student not found")
    return s


def norm_status(value: str) -> str:
    v = (value or "").strip().lower().replace(" ", "_")
    return STATUS_ALIASES.get(v, v)


def _form_context(db: Session) -> dict:
    courses = db.query(Course).filter(Course.is_active.is_(True)).order_by(Course.order).all()
    divisions = db.query(Division).order_by(Division.course_id, Division.order).all()
    clients = db.query(Client).filter(Client.status != "churned").order_by(Client.full_name).all()
    return {"courses": courses, "course_options": [(c.id, f"{c.code} - {c.name}") for c in courses],
            "divisions": [{"id": d.id, "course_id": d.course_id, "name": d.name} for d in divisions],
            "client_options": [(c.id, f"{c.client_code} - {c.full_name} ({c.country})") for c in clients],
            "countries": svc.COUNTRY_NAMES, "timezones": svc.TIMEZONES, "statuses": [(s, status_label(s, "student")) for s in STATUSES],
            "languages": ["English", "Urdu", "Arabic", "Bengali", "Other"]}


# --------------------------------------------------------------------------- list helpers
def _on_leave_student_ids(db: Session) -> set[int]:
    """Students "On Leave": frozen, or an approved leave covering today (leave_for_all covers the whole family)."""
    today = date.today()
    ids = {r[0] for r in db.query(Student.id).filter(Student.status == "frozen")}
    rows = db.query(Leave).filter(Leave.person_type == "student", Leave.status == "approved",
                                  Leave.start_date <= today, Leave.end_date >= today).all()
    for lv in rows:
        if lv.student_id:
            ids.add(lv.student_id)
        if lv.leave_for_all and lv.student and lv.student.client_id:
            ids |= {r[0] for r in db.query(Student.id).filter(Student.client_id == lv.student.client_id)}
    return ids


def _black_list_student_ids(db: Session) -> set[int]:
    ids = {r[0] for r in db.query(Student.id).filter(Student.status == "black_list")}
    ids |= {r[0] for r in db.query(Student.id).join(Client, Student.client_id == Client.id).filter(Client.status == "inactive")}
    return ids


def _subscription_counts(db: Session, student_ids: list[int]) -> dict[int, int]:
    if not student_ids:
        return {}
    return dict(db.query(Subscription.student_id, func.count(Subscription.id)).filter(Subscription.student_id.in_(student_ids))
                .group_by(Subscription.student_id).all())


def _tile_counts(db: Session) -> dict:
    counts = dict(db.query(Student.status, func.count(Student.id)).group_by(Student.status).all())
    return {"trial": counts.get("trial", 0), "active": counts.get("active", 0), "cancelled": counts.get("cancelled", 0),
            "black_list": len(_black_list_student_ids(db)), "on_leave": len(_on_leave_student_ids(db)),
            "free": counts.get("free", 0), "graduated": counts.get("graduated", 0), "total": sum(counts.values()),
            "high_risk": db.query(func.count(Student.id)).filter(Student.risk_level == "high", Student.status.in_(["active", "trial"])).scalar() or 0}


def _apply_student_filters(db: Session, query, q: str, status: str, family_status: str, shift: str, teacher: str, course: str, gender: str,
                           client: str, report: str):
    query = query.join(Client, Student.client_id == Client.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Student.full_name.ilike(like), Student.student_code.ilike(like), Student.legacy_code.ilike(like),
                                 Student.email.ilike(like), Client.full_name.ilike(like), Client.client_code.ilike(like)))
    st = norm_status(status)
    if st == "on_leave":
        query = query.filter(Student.id.in_(list(_on_leave_student_ids(db)) or [-1]))
    elif st == "black_list":
        query = query.filter(Student.id.in_(list(_black_list_student_ids(db)) or [-1]))
    elif st:
        query = query.filter(Student.status == st)
    fs = (family_status or "").strip().lower().replace(" ", "_")
    fs = {"regular": "active", "drop_out": "churned", "black_list": "inactive", "trail": "trial"}.get(fs, fs)
    if fs == "on_leave":
        from app.web.clients import _on_leave_client_ids
        query = query.filter(Client.id.in_(list(_on_leave_client_ids(db)) or [-1]))
    elif fs:
        query = query.filter(Client.status == fs)
    if shift in ("morning", "night"):
        query = query.filter(Client.shift == shift)
    if parse_int(teacher):
        query = query.filter(Student.teacher_id == int(teacher))
    if parse_int(course):
        query = query.filter(Student.course_id == int(course))
    if gender in ("male", "female"):
        query = query.filter(Student.gender == gender)
    if parse_int(client):
        query = query.filter(Student.client_id == int(client))
    if report == "free":
        # ERP saved report "Free Students List": Family Status in (Regular, Trial), Status = Active, Total Subscriptions = 0
        sub_ids = db.query(Subscription.student_id).distinct()
        query = query.filter(Client.status.in_(["active", "trial"]), Student.status == "active", ~Student.id.in_(sub_ids))
    return query


# --------------------------------------------------------------------------- Student List
@router.get("", include_in_schema=False)
def list_students(request: Request, page: int = 1, q: str = "", status: str = "", family_status: str = "", shift: str = "", course: str = "",
                  teacher: str = "", gender: str = "", client: str = "", report: str = "primary", risk: str = "",
                  db: Session = Depends(get_db), user: User = Depends(require("students.view"))):
    report = report if report in ("primary", "free") else "primary"
    query = _apply_student_filters(db, db.query(Student), q, status, family_status, shift, teacher, course, gender, client, report)
    if risk in ("low", "medium", "high"):
        query = query.filter(Student.risk_level == risk)
    pg = paginate(query.order_by(Student.created_at.desc()), page, 25)
    courses = db.query(Course).order_by(Course.order).all()
    teachers = db.query(Teacher).order_by(Teacher.full_name).all()
    clients = db.query(Client).order_by(Client.full_name).all()
    base = (f"/students?q={q}&status={status}&family_status={family_status}&shift={shift}&course={course}&teacher={teacher}"
            f"&gender={gender}&client={client}&report={report}&risk={risk}")
    return render(request, "students/list.html", {"user": user, "page": pg, "q": q, "status": status, "family_status": family_status, "shift": shift,
                                                  "course": course, "teacher": teacher, "gender": gender, "client": client, "report": report, "risk": risk,
                                                  "stats": _tile_counts(db), "tiles": TILES, "reports": REPORTS,
                                                  "status_options": [(k, l) for l, k, _ in TILES] + [("free", "Free"), ("graduated", "Pass Out")],
                                                  "family_status_options": FAMILY_STATUSES, "shifts": SHIFTS,
                                                  "course_options": [(c.id, c.name) for c in courses],
                                                  "teacher_options": [(t.id, t.full_name) for t in teachers],
                                                  "client_options": [(c.id, f"{c.client_code} - {c.full_name}") for c in clients],
                                                  "sub_counts": _subscription_counts(db, [s.id for s in pg.items]), "base_url": base})


# --------------------------------------------------------------------------- Student Referred List (static path before /{id})
@router.get("/referred", include_in_schema=False)
def referred_students(request: Request, page: int = 1, date_from: str = "", date_to: str = "", q: str = "",
                      db: Session = Depends(get_db), user: User = Depends(require("students.view"))):
    df, dt = parse_date(date_from), parse_date(date_to)
    query = db.query(Student).join(Client, Student.client_id == Client.id).filter(Student.referred_by.isnot(None), Student.referred_by != "")
    if df:
        query = query.filter(Student.join_date >= df)
    if dt:
        query = query.filter(Student.join_date <= dt)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Student.full_name.ilike(like), Student.referred_by.ilike(like), Client.full_name.ilike(like)))
    pg = paginate(query.order_by(Student.join_date.desc(), Student.id.desc()), page, 30)
    all_rows = query.order_by(None).all()
    pivot: dict[str, dict] = {}
    for s in all_rows:
        key = (s.referred_by or "").strip()
        row = pivot.setdefault(key, {"referrer": key, "students": 0, "active": 0, "trial": 0, "cancelled": 0, "other": 0})
        row["students"] += 1
        bucket = s.status if s.status in ("active", "trial", "cancelled") else "other"
        row[bucket] += 1
    pivot_rows = sorted(pivot.values(), key=lambda r: (-r["students"], r["referrer"]))
    # "Refer New Contact" requests raised by families in the same window
    rq = db.query(ReferredContact)
    if df:
        rq = rq.filter(ReferredContact.created_at >= datetime.combine(df, datetime.min.time()))
    if dt:
        rq = rq.filter(ReferredContact.created_at <= datetime.combine(dt, datetime.max.time()))
    contacts = rq.order_by(ReferredContact.created_at.desc()).limit(100).all()
    contact_pivot: dict[str, int] = {}
    for r in contacts:
        name = r.client.full_name if r.client else "-"
        contact_pivot[name] = contact_pivot.get(name, 0) + 1
    stats = {"students": len(all_rows), "referrers": len(pivot), "contacts": len(contacts),
             "converted": sum(1 for r in contacts if r.status == "approved")}
    return render(request, "students/referred.html", {"user": user, "page": pg, "date_from": date_from, "date_to": date_to, "q": q, "stats": stats,
                                                      "pivot": pivot_rows, "contacts": contacts,
                                                      "contact_pivot": sorted(contact_pivot.items(), key=lambda x: -x[1]),
                                                      "base_url": f"/students/referred?date_from={date_from}&date_to={date_to}&q={q}"})


# --------------------------------------------------------------------------- On Leave Students
@router.get("/on-leave", include_in_schema=False)
def on_leave_students(request: Request, page: int = 1, shift: str = "", q: str = "", db: Session = Depends(get_db),
                      user: User = Depends(require("students.view"))):
    today = date.today()
    leaves = (db.query(Leave).filter(Leave.person_type == "student", Leave.status == "approved", Leave.start_date <= today, Leave.end_date >= today)
              .order_by(Leave.end_date).all())
    by_student: dict[int, Leave] = {}
    for lv in leaves:
        if lv.student_id and lv.student_id not in by_student:
            by_student[lv.student_id] = lv
        if lv.leave_for_all and lv.student and lv.student.client_id:
            for (sid,) in db.query(Student.id).filter(Student.client_id == lv.student.client_id):
                by_student.setdefault(sid, lv)
    ids = set(by_student) | {r[0] for r in db.query(Student.id).filter(Student.status == "frozen")}
    query = db.query(Student).join(Client, Student.client_id == Client.id).filter(Student.id.in_(list(ids) or [-1]))
    if shift in ("morning", "night"):
        query = query.filter(Client.shift == shift)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Student.full_name.ilike(like), Student.student_code.ilike(like), Client.full_name.ilike(like)))
    pg = paginate(query.order_by(Client.shift, Student.full_name), page, 30)
    upcoming = db.query(func.count(Leave.id)).filter(Leave.person_type == "student", Leave.status == "approved", Leave.start_date > today).scalar() or 0
    stats = {"on_leave": len(ids), "frozen": db.query(func.count(Student.id)).filter(Student.status == "frozen").scalar() or 0,
             "approved_today": len(by_student), "upcoming": upcoming,
             "pending": db.query(func.count(Leave.id)).filter(Leave.person_type == "student", Leave.status == "pending").scalar() or 0}
    return render(request, "students/on_leave.html", {"user": user, "page": pg, "leaves": by_student, "shift": shift, "q": q, "shifts": SHIFTS,
                                                      "stats": stats, "base_url": f"/students/on-leave?shift={shift}&q={q}"})


# --------------------------------------------------------------------------- create (teacher match)
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


FORM_KEYS = ("client_id", "full_name", "gender", "date_of_birth", "age", "course_id", "division_id", "level", "timezone",
             "preferred_language", "status", "preferred_shift", "notes", "teacher_id", "new_client_name", "new_client_email",
             "new_client_phone", "new_client_country", "sabaq_position", "email", "trial_days", "legacy_code", "drop_date", "referred_by", "grade")


@router.get("/new", include_in_schema=False)
def new_student(request: Request, db: Session = Depends(get_db), user: User = Depends(require("students.add"))):
    params = request.query_params
    ranked, meta = ([], {}) if not params.get("course_id") else _recommend_from_params(db, params)
    client = db.query(Client).get(int(params["client_id"])) if params.get("client_id") else None
    values = {k: params.get(k, "") for k in FORM_KEYS}
    if client and not values["timezone"]:
        values["timezone"] = client.timezone
    if not values["trial_days"]:
        values["trial_days"] = "3"
    return render(request, "students/form.html", {"user": user, **_form_context(db), "mode": "new", "values": values, "ranked": ranked, "meta": meta,
                                                  "student": None, "client": client})


def _read_student_form(form) -> dict:
    return {"full_name": form.get("full_name"), "gender": form.get("gender"), "date_of_birth": parse_date(form.get("date_of_birth")), "age": form.get("age"),
            "course_id": form.get("course_id"), "division_id": form.get("division_id"), "level": form.get("level"), "timezone": form.get("timezone"),
            "preferred_language": form.get("preferred_language"), "status": norm_status(form.get("status")), "notes": form.get("notes"),
            "guardian_consent": parse_bool(form.get("guardian_consent")), "sabaq_position": form.get("sabaq_position"),
            # ERP Basic Detail
            "email": (form.get("email") or "").strip().lower() or None, "trial_days": parse_int(form.get("trial_days")),
            "legacy_code": (form.get("legacy_code") or "").strip() or None, "drop_date": parse_date(form.get("drop_date")),
            "referred_by": (form.get("referred_by") or "").strip() or None, "grade": (form.get("grade") or "").strip() or None}


def _apply_erp_fields(s: Student, data: dict) -> None:
    s.email = data.get("email")
    if data.get("trial_days") is not None:
        s.trial_days = max(0, int(data["trial_days"]))
    s.legacy_code = data.get("legacy_code")
    s.drop_date = data.get("drop_date")
    s.referred_by = data.get("referred_by")
    s.grade = data.get("grade")


@router.post("/new", include_in_schema=False)
async def create_student(request: Request, db: Session = Depends(get_db), user: User = Depends(require("students.add"))):
    form = await request.form()
    data = _read_student_form(form)
    if not (data["full_name"] or "").strip():
        return redirect("/students/new", "Student name is required.", "error")
    if data["status"] not in STATUSES:
        data["status"] = "trial"
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
        client.lead_added_by_id = user.id
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
    _apply_erp_fields(s, data)
    if teacher or ranked:
        svc.assign_teacher(db, s, teacher, user, reason=override_reason or None, overridden=overridden, ranked=ranked, request=request)
        if teacher and teacher.user_id:
            notify(db, teacher.user_id, "New student assigned", f"{s.full_name} ({s.course.name if s.course else 'course TBD'}) has been enrolled with you.",
                   event_type="teacher_change", link="/teacher/students")
    if client.user_id:
        notify(db, client.user_id, f"{s.full_name} enrolled", f"Welcome! {s.full_name} is enrolled ({status_label(s.status, 'student')}). Teacher: {teacher.full_name if teacher else 'to be assigned'}.",
               event_type="enrolment", link="/portal")
    db.commit()
    return redirect(f"/students/{s.id}", f"Student {s.student_code} created" + (f" and matched with {teacher.full_name}." if teacher else "."))


# --------------------------------------------------------------------------- Student Form (detail)
@router.get("/{id}", include_in_schema=False)
def student_detail(id: int, request: Request, tab: str = "overview", db: Session = Depends(get_db), user: User = Depends(require("students.view"))):
    s = _get(db, id)
    if tab == "basic":
        return redirect(f"/students/{s.id}/edit")
    today = date.today()
    ctx: dict = {"user": user, "s": s, "tab": tab, "tabs": [(k, l, f"/students/{s.id}/edit" if k == "basic" else f"/students/{s.id}?tab={k}") for k, l in TABS],
                 "attendance_pct": student_attendance_pct(db, s.id, 30), "statuses": [(x, status_label(x, "student")) for x in STATUSES],
                 "show_contact": rbac.has_permission(user, "clients.update") or rbac.is_management(user),
                 "change_log_url": f"/admin/audit/entity/Student/{s.id}" if rbac.has_permission(user, "audit.view") else f"/students/{s.id}?tab=audit",
                 "subscriptions_count": db.query(func.count(Subscription.id)).filter(Subscription.student_id == s.id).scalar() or 0}
    if tab == "overview":
        ctx["next_session"] = db.query(ClassSession).filter(ClassSession.student_id == s.id, ClassSession.status == "pending", ClassSession.scheduled_start >= datetime.utcnow() - timedelta(hours=5)).order_by(ClassSession.scheduled_start).first()
        ctx["schedules"] = db.query(Schedule).filter(Schedule.student_id == s.id, Schedule.status == "active").all()
        ctx["dor"] = db.query(DorSchedule).filter(DorSchedule.student_id == s.id).order_by(DorSchedule.period.desc()).first()
        ctx["progress"] = svc.student_progress_summary(db, s)
        ctx["last_match"] = db.query(TeacherMatch).filter(TeacherMatch.student_id == s.id).order_by(TeacherMatch.created_at.desc()).first()
        ctx["open_cases"] = db.query(Case).filter(Case.student_id == s.id, Case.status.in_(["open", "in_progress", "waiting", "escalated"])).count()
        ctx["recent_audit"] = db.query(AuditEvent).filter(AuditEvent.entity_type == "Student", AuditEvent.entity_id == s.id).order_by(AuditEvent.created_at.desc()).limit(6).all()
    elif tab == "subscriptions":
        subs = db.query(Subscription).filter(Subscription.student_id == s.id).order_by(Subscription.created_at.desc()).all()
        sched_ids = [x.schedule_id for x in subs if x.schedule_id] or [-1]
        schedules = {sc.id: sc for sc in db.query(Schedule).filter(Schedule.id.in_(sched_ids))}
        by_sub = {sc.subscription_id: sc for sc in db.query(Schedule).filter(Schedule.subscription_id.in_([x.id for x in subs] or [-1]))}
        rows = []
        for sub in subs:
            sch = schedules.get(sub.schedule_id) or by_sub.get(sub.id)
            if sub.slot:
                session_label = sub.slot.label
            elif sch:
                session_label = sch.start_time.strftime("%I:%M %p")
            else:
                session_label = "-"
            days = sub.days_of_week or (sch.days_of_week if sch else []) or []
            rows.append({"sub": sub, "session": session_label, "days": ", ".join(DAY_NAMES[d] for d in days if 0 <= int(d) < 7) or "-"})
        ctx["sub_rows"] = rows
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


# --------------------------------------------------------------------------- Basic Detail (edit)
@router.get("/{id}/edit", include_in_schema=False)
def edit_student(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("students.update"))):
    s = _get(db, id)
    values = {"client_id": s.client_id, "full_name": s.full_name, "gender": s.gender, "date_of_birth": s.date_of_birth.isoformat() if s.date_of_birth else "",
              "age": s.age or "", "course_id": s.course_id or "", "division_id": s.division_id or "", "level": s.level or "", "timezone": s.timezone,
              "preferred_language": s.preferred_language, "status": s.status, "notes": s.notes or "", "teacher_id": s.teacher_id or "",
              "sabaq_position": s.sabaq_position or "", "preferred_shift": "", "email": s.email or "", "trial_days": s.trial_days,
              "legacy_code": s.legacy_code or "", "drop_date": s.drop_date.isoformat() if s.drop_date else "", "referred_by": s.referred_by or "",
              "grade": s.grade or "", "join_date": s.join_date.isoformat() if s.join_date else ""}
    return render(request, "students/form.html", {"user": user, **_form_context(db), "mode": "edit", "values": values, "ranked": [], "meta": {}, "student": s,
                                                  "client": s.client,
                                                  "change_log_url": f"/admin/audit/entity/Student/{s.id}" if rbac.has_permission(user, "audit.view") else f"/students/{s.id}?tab=audit"})


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
    if parse_date(form.get("join_date")):
        s.join_date = parse_date(form.get("join_date"))
    _apply_erp_fields(s, data)
    if form.get("client_id") and int(form["client_id"]) != s.client_id and rbac.has_permission(user, "students.assign"):
        s.client_id = int(form["client_id"])
    if s.user:
        s.user.full_name = s.full_name
        s.user.timezone = s.timezone
    # Status change from the Basic Detail form requires a reason (same rule as the status modal)
    new_status = data["status"] if data["status"] in STATUSES else s.status
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if new_status != s.status:
        if not reason:
            return redirect(f"/students/{s.id}/edit", "A reason is required when the student status changes.", "error")
        svc.student_status_change(db, s, new_status, user, reason, request=request)
        if new_status == "cancelled" and not s.drop_date:
            s.drop_date = date.today()
    log_action(db, user, "update", "students", entity=s, description=f"Student {s.student_code} updated (Basic Detail)", before=before, after=snapshot(s),
               rationale=reason or None, request=request)
    db.commit()
    return redirect(f"/students/{s.id}", "Student updated.")


@router.post("/{id}/status", include_in_schema=False)
async def change_status(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("students.update"))):
    s = _get(db, id)
    form = await request.form()
    new_status = norm_status(form.get("status"))
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if new_status not in STATUSES:
        return redirect(f"/students/{s.id}", "Invalid status.", "error")
    if not reason:
        return redirect(f"/students/{s.id}", "A reason is required for status changes.", "error")
    if new_status == s.status:
        return redirect(f"/students/{s.id}", f"Student is already {status_label(new_status, 'student')}.", "info")
    svc.student_status_change(db, s, new_status, user, reason, request=request)
    if new_status == "cancelled":
        s.drop_date = s.drop_date or date.today()
    elif new_status in ("active", "trial", "free"):
        s.drop_date = None
    if new_status in ("cancelled", "frozen"):
        try:
            from app.services.retention import on_student_status_change  # type: ignore
            on_student_status_change(db, s, new_status, reason)
        except Exception:
            pass
    db.commit()
    return redirect(f"/students/{s.id}", f"Student marked {status_label(new_status, 'student')}. Parent notified.")


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
               status="pending", requested_by_id=user.id, apply_date=date.today(), leave_for_all=parse_bool(form.get("leave_for_all")),
               leave_detail=(form.get("leave_detail") or "").strip() or None)
    db.add(lv)
    db.flush()
    log_action(db, user, "create", "leaves", entity=lv, description=f"Student leave requested for {s.student_code}", request=request)
    db.commit()
    return redirect(f"/students/{s.id}?tab=leaves", "Leave request recorded (pending approval).")
