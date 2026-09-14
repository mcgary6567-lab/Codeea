"""Subscription Management - ERP parity (docs/AUDIT_ACADEMICS.md 3.5).

Create Subscription wizard, Faculty Allocation, All Subscriptions (with saved reports), Value Report,
Detail Report (CSV) and Cancelled Subscriptions. A Subscription is the central object: student + course + teacher +
session slot + days + language + method + package + status; creating or changing one keeps the recurring Schedule
and its generated ClassSessions in step (app.services.scheduling.sync_subscription_schedule).
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.audit import log_action, snapshot
from app.core.deps import csrf_protect, require
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import paginate, parse_date, parse_int, redirect
from app.database import get_db
from app.models.academic import Book, Course, Package
from app.models.core import AuditEvent, User
from app.models.erp import SessionSlot
from app.models.finance import Currency, Subscription
from app.models.people import Client, Student, Teacher
from app.models.scheduling import ClassSession
from app.services import scheduling as sched

router = APIRouter(prefix="/subscriptions", dependencies=[Depends(csrf_protect)])

DAY_NAMES = sched.DAY_NAMES
GRADES = ["", "Pre-school", "Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5", "Grade 6", "Grade 7", "Grade 8",
          "Grade 9", "Grade 10", "College", "Adult"]


# ----------------------------------------------------------------------------- helpers
def _get(db: Session, id: int) -> Subscription:
    s = db.get(Subscription, id)
    if not s:
        raise HTTPException(404, "Subscription not found")
    return s


def _options(db: Session) -> dict:
    return {
        "slot_options": sched.slot_options(db),
        "course_options": [(c.id, c.name) for c in db.query(Course).filter(Course.is_active.is_(True)).order_by(Course.order, Course.name)],
        "language_options": sched.LANGUAGES,
        "client_options": [(c.id, f"{c.full_name} ({c.client_code})") for c in db.query(Client).order_by(Client.full_name)],
        "student_options": [(s.id, f"{s.full_name} ({s.student_code})") for s in db.query(Student).order_by(Student.full_name)],
        "teacher_options": [(t.id, t.full_name) for t in db.query(Teacher).filter(Teacher.status != "inactive").order_by(Teacher.full_name)],
        "status_options": sched.SUBSCRIPTION_STATUS_OPTIONS,
        "supervisor_options": [(u.id, u.full_name) for u in db.query(User).join(Teacher, Teacher.supervisor_id == User.id)
                               .distinct().order_by(User.full_name)],
        "currency_options": [(c.code, c.code) for c in db.query(Currency).filter(Currency.is_active.is_(True)).order_by(Currency.code)],
        "country_options": [(c, c) for (c,) in db.query(Client.country).filter(Client.country.isnot(None)).distinct().order_by(Client.country)],
        "package_options": [(p.id, f"{p.name} - {p.currency} {float(p.price):,.0f}") for p in
                            db.query(Package).filter(Package.is_active.is_(True)).order_by(Package.price)],
        "method_options": sched.COURSE_METHODS,
        "category_options": sched.SLOT_CATEGORIES,
        "day_names": DAY_NAMES,
    }


def _read_days(form) -> list[int]:
    days = []
    for d in range(7):
        if form.get(f"day_{d}") in ("1", "on", "true"):
            days.append(d)
    for v in form.getlist("days") if hasattr(form, "getlist") else []:
        if str(v).isdigit() and int(v) not in days:
            days.append(int(v))
    return sorted(days)


def _query(db: Session, *, sub_id: str = "", slot_id: int | None = None, course_id: int | None = None, language: str = "",
           client_id: int | None = None, student_id: int | None = None, teacher_id: int | None = None, status: str = "",
           supervisor_id: int | None = None, country: str = "", currency: str = "", date_from=None, date_to=None):
    q = db.query(Subscription)
    if sub_id:
        like = f"%{sub_id.strip()}%"
        q = q.filter(or_(Subscription.subscription_code.ilike(like), Subscription.id == parse_int(sub_id.strip().replace("SUB-", ""), -1)))
    if slot_id:
        q = q.filter(Subscription.slot_id == slot_id)
    if course_id:
        q = q.filter(Subscription.course_id == course_id)
    if language:
        q = q.filter(Subscription.language == language)
    if client_id:
        q = q.filter(Subscription.client_id == client_id)
    if student_id:
        q = q.filter(Subscription.student_id == student_id)
    if teacher_id:
        q = q.filter(Subscription.teacher_id == teacher_id)
    if status:
        q = q.filter(Subscription.status.in_(sched.status_values(status)))
    if supervisor_id:
        q = q.join(Teacher, Teacher.id == Subscription.teacher_id, isouter=True).filter(
            or_(Subscription.supervisor_id == supervisor_id, Teacher.supervisor_id == supervisor_id))
    if country or currency:
        q = q.join(Client, Client.id == Subscription.client_id)
        if country:
            q = q.filter(Client.country == country)
        if currency:
            q = q.filter(Subscription.currency == currency)
    if date_from:
        q = q.filter(Subscription.start_date >= date_from)
    if date_to:
        q = q.filter(Subscription.start_date <= date_to)
    return q


def _tiles(db: Session) -> dict:
    live = db.query(Subscription).filter(Subscription.status.in_(sched.LIVE_SUBSCRIPTION_STATUSES))
    today = date.today()
    return {
        "clients": live.with_entities(func.count(func.distinct(Subscription.client_id))).scalar() or 0,
        "students": live.with_entities(func.count(func.distinct(Subscription.student_id))).scalar() or 0,
        "all": db.query(func.count(Subscription.id)).scalar() or 0,
        "follow_ups": db.query(func.count(Subscription.id)).filter(Subscription.follow_up_date >= today,
                                                                   Subscription.follow_up_date <= today + timedelta(days=7)).scalar() or 0,
    }


def _employee_summary(db: Session, rows: list[Subscription]) -> list[dict]:
    by: dict[int | None, dict] = {}
    for s in rows:
        b = by.setdefault(s.teacher_id, {"teacher": s.teacher, "regular": 0, "trial": 0, "cancelled": 0, "freeze": 0, "completed": 0,
                                         "total": 0, "value": 0.0, "cost": 0.0})
        b["total"] += 1
        key = {"active": "regular", "frozen": "freeze", "expired": "completed"}.get(s.status, s.status)
        if key in b:
            b[key] += 1
        if s.status in sched.RUNNING_SUBSCRIPTION_STATUSES:
            b["value"] += float(s.price_in_base or 0)
            b["cost"] += float(s.teacher_cost_base or 0)
    out = list(by.values())
    for b in out:
        b["value"] = round(b["value"], 2)
        b["cost"] = round(b["cost"], 2)
        b["margin"] = round(b["value"] - b["cost"], 2)
    out.sort(key=lambda b: (b["teacher"].full_name if b["teacher"] else "zz"))
    return out


def _base(db: Session) -> str:
    from app.services import billing
    return billing.base_currency(db)


# ----------------------------------------------------------------------------- Create Subscription (wizard)
@router.get("/new", include_in_schema=False)
def new_subscription(request: Request, student_id: int | None = None, language: str = "English", session_category: str = "30 Minutes",
                     session_type: str = "Job Time Session", course_method: str = "one_on_one", course_id: int | None = None,
                     grade: str = "", status: str = "trial", package_id: int | None = None, trial_days: int = 3, remarks: str = "",
                     teacher_id: int | None = None, slot_id: int | None = None, searched: str = "",
                     db: Session = Depends(get_db), user: User = Depends(require("subscriptions.add"))):
    days = [d for d in range(7) if request.query_params.get(f"day_{d}")]
    student = db.get(Student, student_id) if student_id else None
    course = db.get(Course, course_id) if course_id else (student.course if student else None)
    if not course_id and course:
        course_id = course.id
    slots = sched.slots_for(db, session_category)
    slot = db.get(SessionSlot, slot_id) if slot_id else None
    teachers, strict = ([], True)
    teacher_sessions = None
    highlighted = db.get(Teacher, teacher_id) if teacher_id else None
    if searched and slot and days:
        teachers, strict = sched.available_teachers(db, slot, days, course=course, language=language)
        if not highlighted and teachers:
            highlighted = teachers[0]
    if highlighted:
        teacher_sessions = sched.teacher_week_sessions(db, highlighted.id)
    books = db.query(Book).filter(Book.course_id == course.id, Book.status == "active").order_by(Book.order).all() if course else []
    chosen_books = [int(b) for b in request.query_params.getlist("books") if str(b).isdigit()]
    busy_slot_ids: set[int] = set()
    if searched and days and teachers:
        # slots where the highlighted teacher is already booked (for the Session Times panel)
        if highlighted:
            for sl in slots:
                if sched.teacher_slot_busy(db, highlighted.id, days, sl):
                    busy_slot_ids.add(sl.id)
    return render(request, "subscriptions/new.html", {
        "user": user, "student": student, "student_id": student_id, "language": language, "session_category": session_category,
        "session_type": session_type, "course_method": course_method, "course_id": course_id, "course": course, "grade": grade or (student.grade if student else ""),
        "gender": (student.gender if student else ""), "status": status, "package_id": package_id, "trial_days": trial_days,
        "remarks": remarks, "days": days, "searched": bool(searched), "slots": slots, "slot": slot, "slot_id": slot_id,
        "teachers": teachers, "strict": strict, "highlighted": highlighted, "teacher_sessions": teacher_sessions,
        "books": books, "chosen_books": chosen_books, "busy_slot_ids": busy_slot_ids, "grades": [g for g in GRADES if g],
        "session_type_options": sched.SESSION_TYPES, "price_preview": (
            sched.subscription_price(db, db.get(Package, package_id) if package_id else None, course, student.client.currency)
            if student and student.client else None),
        **_options(db)})


@router.post("/new", include_in_schema=False)
async def create_subscription(request: Request, db: Session = Depends(get_db), user: User = Depends(require("subscriptions.add"))):
    form = await request.form()
    student = db.get(Student, parse_int(form.get("student_id"))) if form.get("student_id") else None
    course = db.get(Course, parse_int(form.get("course_id"))) if form.get("course_id") else (student.course if student else None)
    package = db.get(Package, parse_int(form.get("package_id"))) if form.get("package_id") else None
    teacher = db.get(Teacher, parse_int(form.get("teacher_id"))) if form.get("teacher_id") else None
    slot = db.get(SessionSlot, parse_int(form.get("slot_id"))) if form.get("slot_id") else None
    days = _read_days(form)
    back = "/subscriptions/new?" + "&".join(f"{k}={v}" for k, v in form.items() if k not in ("teacher_id", "slot_id") and v) + "&searched=1"
    try:
        sub = sched.create_erp_subscription(
            db, user, student=student, course=course, package=package, teacher=teacher, slot=slot, days=days,
            language=form.get("language") or "English", course_method=form.get("course_method") or "one_on_one",
            session_category=form.get("session_category") or (slot.category if slot else "30 Minutes"),
            session_type=form.get("session_type") or "Job Time Session", status=form.get("status") or "trial",
            trial_days=parse_int(form.get("trial_days"), 3) or 3, remarks=(form.get("remarks") or "").strip() or None,
            books=[int(b) for b in form.getlist("books") if str(b).isdigit()], grade=(form.get("grade") or "").strip() or None,
            request=request)
    except ValueError as exc:
        return redirect(back, str(exc), "error")
    db.commit()
    return redirect(f"/subscriptions/{sub.id}", f"Subscription {sub.subscription_code} created; schedule and classes generated.")


# ----------------------------------------------------------------------------- Faculty Allocation
@router.get("/allocation", include_in_schema=False)
def allocation(request: Request, teacher_id: int | None = None, course_id: int | None = None, language: str = "", slot_id: int | None = None,
               client_id: int | None = None, student_id: int | None = None, day: int | None = None,
               db: Session = Depends(get_db), user: User = Depends(require("subscriptions.view"))):
    days = [d for d in range(7) if request.query_params.get(f"day_{d}")]
    q = _query(db, slot_id=slot_id, course_id=course_id, language=language, client_id=client_id, student_id=student_id,
               teacher_id=teacher_id).filter(Subscription.status.in_(sched.RUNNING_SUBSCRIPTION_STATUSES))
    rows = q.order_by(Subscription.teacher_id, Subscription.slot_id, Subscription.id).all()
    if days:
        rows = [s for s in rows if set(days) & set(int(d) for d in (s.days_of_week or []))]
    groups: dict[int | None, dict] = {}
    for s in rows:
        g = groups.setdefault(s.teacher_id, {"teacher": s.teacher, "rows": []})
        g["rows"].append(s)
    grouped = sorted(groups.values(), key=lambda g: g["teacher"].full_name if g["teacher"] else "zz")
    # occupancy grid: teacher x slot for the selected weekday (default today)
    grid_day = day if day is not None else date.today().weekday()
    teachers = db.query(Teacher).filter(Teacher.status != "inactive").order_by(Teacher.full_name).all()
    if teacher_id:
        teachers = [t for t in teachers if t.id == teacher_id]
    live = db.query(Subscription).filter(Subscription.status.in_(sched.RUNNING_SUBSCRIPTION_STATUSES), Subscription.slot_id.isnot(None)).all()
    occ: dict[tuple[int, int], Subscription] = {}
    used_slot_ids: set[int] = set()
    for s in live:
        if grid_day in [int(d) for d in (s.days_of_week or [])] and s.teacher_id:
            occ[(s.teacher_id, s.slot_id)] = s
            used_slot_ids.add(s.slot_id)
    slot_cols = [sl for sl in sched.slots_for(db) if sl.id in used_slot_ids]
    return render(request, "subscriptions/allocation.html", {
        "user": user, "grouped": grouped, "total": len(rows), "teacher_id": teacher_id, "course_id": course_id, "language": language,
        "slot_id": slot_id, "client_id": client_id, "student_id": student_id, "days": days, "grid_day": grid_day,
        "grid_teachers": teachers, "occ": occ, "slot_cols": slot_cols, **_options(db)})


# ----------------------------------------------------------------------------- Value report
@router.get("/value-report", include_in_schema=False)
def value_report(request: Request, report: str = "primary", db: Session = Depends(get_db), user: User = Depends(require("subscriptions.view"))):
    rows = (db.query(Subscription).filter(Subscription.status.in_(sched.RUNNING_SUBSCRIPTION_STATUSES))
            .order_by(Subscription.teacher_id, Subscription.id).all())
    base = _base(db)
    total_value = round(sum(float(s.price_in_base or 0) for s in rows), 2)
    total_cost = round(sum(float(s.teacher_cost_base or 0) for s in rows), 2)
    shifts: dict[str, dict] = {"morning": {"shift": "Morning", "subs": 0, "revenue": 0.0, "cost": 0.0},
                               "night": {"shift": "Night", "subs": 0, "revenue": 0.0, "cost": 0.0}}
    for s in rows:
        key = (s.client.shift if s.client and s.client.shift in shifts else "night")
        shifts[key]["subs"] += 1
        shifts[key]["revenue"] += float(s.price_in_base or 0)
        shifts[key]["cost"] += float(s.teacher_cost_base or 0)
    for v in shifts.values():
        v["revenue"] = round(v["revenue"], 2)
        v["cost"] = round(v["cost"], 2)
        v["profit"] = round(v["revenue"] - v["cost"], 2)
        v["margin_pct"] = round(100 * v["profit"] / v["revenue"], 1) if v["revenue"] else 0.0
    last = (db.query(AuditEvent).filter(AuditEvent.module == "subscriptions", AuditEvent.action == "recompute")
            .order_by(AuditEvent.created_at.desc()).first())
    return render(request, "subscriptions/value_report.html", {
        "user": user, "report": report if report in ("primary", "employee", "shift") else "primary", "rows": rows, "base": base,
        "total_value": total_value, "total_cost": total_cost, "total_margin": round(total_value - total_cost, 2),
        "employees": _employee_summary(db, rows), "shifts": list(shifts.values()), "last_regenerated": last})


@router.post("/value-report/regenerate", include_in_schema=False)
async def regenerate_value_report(request: Request, db: Session = Depends(get_db), user: User = Depends(require("subscriptions.view"))):
    from app.services import billing
    n = 0
    for s in db.query(Subscription).filter(Subscription.status.in_(sched.LIVE_SUBSCRIPTION_STATUSES)).all():
        s.price_in_base = billing.convert_to_base(db, float(s.price or 0), s.currency)
        s.teacher_cost_base = billing.teacher_monthly_cost(db, s.teacher, s.sessions_per_week or len(s.days_of_week or []) or 0)
        n += 1
    log_action(db, user, "recompute", "subscriptions", entity_type="Subscription",
               description=f"All Subscriptions Value Report regenerated: {n} subscription(s) revalued at current exchange rates", request=request)
    db.commit()
    return redirect("/subscriptions/value-report", f"Report regenerated: {n} subscription(s) revalued in {_base(db)}.")


# ----------------------------------------------------------------------------- Detail report (+ CSV)
@router.get("/detail-report", include_in_schema=False)
def detail_report(request: Request, supervisor_id: int | None = None, date_from: str = "", date_to: str = "", country: str = "",
                  currency: str = "", teacher_id: int | None = None, status: str = "", format: str = "",
                  db: Session = Depends(get_db), user: User = Depends(require("subscriptions.view"))):
    df, dt = parse_date(date_from), parse_date(date_to)
    rows = (_query(db, supervisor_id=supervisor_id, country=country, currency=currency, teacher_id=teacher_id, status=status,
                   date_from=df, date_to=dt).order_by(Subscription.start_date.desc(), Subscription.id.desc()).all())
    base = _base(db)
    by_currency: dict[str, dict] = {}
    for s in rows:
        b = by_currency.setdefault(s.currency, {"currency": s.currency, "count": 0, "amount": 0.0, "base": 0.0})
        b["count"] += 1
        b["amount"] += float(s.price or 0)
        b["base"] += float(s.price_in_base or 0)
    totals = sorted(by_currency.values(), key=lambda b: b["currency"])
    grand_base = round(sum(b["base"] for b in totals), 2)
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Subscription ID", "Client", "Country", "Currency", "Student", "Teacher", "Manager", "Course", "Session", "Days", "Status",
                    "Reg. Date", "Next Due Date", "Price", f"Price ({base})"])
        for s in rows:
            w.writerow([s.subscription_code, s.client.full_name if s.client else "", s.client.country if s.client else "", s.currency,
                        s.student.full_name if s.student else "", s.teacher.full_name if s.teacher else "",
                        (s.supervisor.full_name if s.supervisor else (s.teacher.supervisor.full_name if s.teacher and s.teacher.supervisor else "")),
                        s.course.name if s.course else "", s.slot.label if s.slot else "", sched.day_label(s.days_of_week),
                        s.status, s.start_date.isoformat() if s.start_date else "", s.next_billing_date.isoformat() if s.next_billing_date else "",
                        f"{float(s.price or 0):.2f}", f"{float(s.price_in_base or 0):.2f}"])
        w.writerow([])
        w.writerow(["Total", "", "", "", "", "", "", "", "", "", "", "", "", "", f"{grand_base:.2f}"])
        log_action(db, user, "export", "subscriptions", entity_type="Subscription", description=f"Subscription detail report exported ({len(rows)} rows)", request=request)
        db.commit()
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f"attachment; filename=subscription-detail-{date.today()}.csv"})
    return render(request, "subscriptions/detail_report.html", {
        "user": user, "rows": rows, "totals": totals, "grand_base": grand_base, "base": base, "supervisor_id": supervisor_id,
        "date_from": date_from, "date_to": date_to, "country": country, "currency": currency, "teacher_id": teacher_id, "status": status,
        "query_string": str(request.url.query), **_options(db)})


# ----------------------------------------------------------------------------- Cancelled subscriptions
@router.get("/cancelled", include_in_schema=False)
def cancelled(request: Request, page: int = 1, date_from: str = "", date_to: str = "", teacher_id: int | None = None,
              supervisor_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(require("subscriptions.view"))):
    df, dt = parse_date(date_from), parse_date(date_to)
    q = _query(db, teacher_id=teacher_id, supervisor_id=supervisor_id, status="cancelled")
    if df:
        q = q.filter(Subscription.cancelled_at >= df)
    if dt:
        q = q.filter(Subscription.cancelled_at <= dt)
    pg = paginate(q.order_by(Subscription.cancelled_at.desc(), Subscription.id.desc()), page, 40)
    this_month = db.query(func.count(Subscription.id)).filter(Subscription.status == "cancelled",
                                                             Subscription.cancelled_at >= date.today().replace(day=1)).scalar() or 0
    return render(request, "subscriptions/cancelled.html", {
        "user": user, "page": pg, "date_from": date_from, "date_to": date_to, "teacher_id": teacher_id, "supervisor_id": supervisor_id,
        "this_month": this_month, "base_url": f"/subscriptions/cancelled?date_from={date_from}&date_to={date_to}&teacher_id={teacher_id or ''}&supervisor_id={supervisor_id or ''}",
        **_options(db)})


# ----------------------------------------------------------------------------- All Subscriptions
@router.get("", include_in_schema=False)
def list_subscriptions(request: Request, page: int = 1, sub_id: str = "", slot_id: int | None = None, course_id: int | None = None,
                       language: str = "", client_id: int | None = None, student_id: int | None = None, teacher_id: int | None = None,
                       status: str = "", report: str = "primary", db: Session = Depends(get_db),
                       user: User = Depends(require("subscriptions.view"))):
    q = _query(db, sub_id=sub_id, slot_id=slot_id, course_id=course_id, language=language, client_id=client_id, student_id=student_id,
               teacher_id=teacher_id, status=status)
    report = report if report in ("primary", "employee", "followups") else "primary"
    today = date.today()
    if report == "followups":
        q = q.filter(Subscription.follow_up_date.isnot(None), Subscription.follow_up_date <= today + timedelta(days=7)).order_by(Subscription.follow_up_date)
    else:
        q = q.order_by(Subscription.id.desc())
    employees = _employee_summary(db, q.all()) if report == "employee" else []
    pg = paginate(q, page, 40) if report != "employee" else None
    base_url = (f"/subscriptions?sub_id={sub_id}&slot_id={slot_id or ''}&course_id={course_id or ''}&language={language}&client_id={client_id or ''}"
                f"&student_id={student_id or ''}&teacher_id={teacher_id or ''}&status={status}&report={report}")
    return render(request, "subscriptions/list.html", {
        "user": user, "page": pg, "tiles": _tiles(db), "sub_id": sub_id, "slot_id": slot_id, "course_id": course_id, "language": language,
        "client_id": client_id, "student_id": student_id, "teacher_id": teacher_id, "status": status, "report": report,
        "employees": employees, "base": _base(db), "base_url": base_url, "today": today, **_options(db)})


# ----------------------------------------------------------------------------- detail + actions
@router.get("/{id}", include_in_schema=False)
def subscription_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("subscriptions.view"))):
    s = _get(db, id)
    sch = sched.subscription_schedule(db, s)
    sessions_q = db.query(ClassSession).filter(or_(ClassSession.subscription_id == s.id,
                                                   ClassSession.schedule_id == (sch.id if sch else -1)))
    upcoming = sessions_q.filter(ClassSession.date >= date.today()).order_by(ClassSession.scheduled_start).limit(15).all()
    recent = sessions_q.filter(ClassSession.date < date.today()).order_by(ClassSession.scheduled_start.desc()).limit(15).all()
    counts = dict(sessions_q.with_entities(ClassSession.status, func.count(ClassSession.id)).group_by(ClassSession.status).all())
    events = (db.query(AuditEvent).filter(AuditEvent.entity_type == "Subscription", AuditEvent.entity_id == s.id)
              .order_by(AuditEvent.created_at.desc()).limit(30).all())
    books = db.query(Book).filter(Book.id.in_([int(b) for b in (s.books or [])] or [-1])).all()
    course_books = db.query(Book).filter(Book.course_id == s.course_id).order_by(Book.order).all() if s.course_id else []
    trial_end = (s.start_date + timedelta(days=s.trial_days or 3)) if s.start_date else None
    return render(request, "subscriptions/detail.html", {
        "user": user, "s": s, "sch": sch, "upcoming": upcoming, "recent": recent, "counts": counts, "events": events, "books": books,
        "course_books": course_books, "trial_end": trial_end, "base": _base(db),
        "verified_teacher_options": [(t.id, t.full_name) for t in db.query(Teacher).filter(Teacher.status == "active", Teacher.is_verified.is_(True)).order_by(Teacher.full_name)],
        "slot_options_cat": sched.slot_options(db, s.session_category or "30 Minutes") or sched.slot_options(db),
        "days_label": sched.day_label(s.days_of_week), "day_names": DAY_NAMES})


def _reason(form) -> str:
    return (form.get("reason") or form.get("rationale") or form.get("remarks") or "").strip()


@router.post("/{id}/freeze", include_in_schema=False)
async def freeze(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("subscriptions.update"))):
    s = _get(db, id)
    form = await request.form()
    reason = _reason(form)
    if s.status not in sched.RUNNING_SUBSCRIPTION_STATUSES:
        return redirect(f"/subscriptions/{s.id}", f"A {s.status} subscription cannot be frozen.", "error")
    if not reason:
        return redirect(f"/subscriptions/{s.id}", "A reason is required to freeze a subscription.", "error")
    before = {"status": s.status}
    s.status = "freeze"
    s.freeze_start = parse_date(form.get("freeze_start"), date.today())
    s.freeze_end = parse_date(form.get("freeze_end"))
    if s.freeze_end:
        s.next_billing_date = s.freeze_end + timedelta(days=1)
    if s.student:
        s.student.status = "frozen"
    n = sched.end_subscription_schedule(db, user, s, "paused", reason, request=request)
    log_action(db, user, "freeze", "subscriptions", entity=s, description=f"{s.subscription_code} frozen {s.freeze_start} to {s.freeze_end or 'open'}; {n} class(es) cancelled",
               rationale=reason, before=before, after={"status": "freeze"}, request=request, consequential=True)
    _notify_parties(db, s, "Subscription frozen", f"{s.subscription_code} is paused from {s.freeze_start}. {reason}")
    db.commit()
    return redirect(f"/subscriptions/{s.id}", f"Subscription frozen; {n} future class(es) cancelled.")


@router.post("/{id}/unfreeze", include_in_schema=False)
async def unfreeze(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("subscriptions.update"))):
    s = _get(db, id)
    form = await request.form()
    if s.status not in ("freeze", "frozen"):
        return redirect(f"/subscriptions/{s.id}", "This subscription is not frozen.", "error")
    reason = _reason(form) or "Resumed"
    s.status = "regular"
    s.freeze_start = s.freeze_end = None
    s.next_billing_date = date.today()
    if s.student:
        s.student.status = "active"
    sch = sched.sync_subscription_schedule(db, user, s, reason=reason, request=request)
    log_action(db, user, "update", "subscriptions", entity=s, description=f"{s.subscription_code} resumed (Regular); schedule #{sch.id if sch else '-'} reactivated",
               rationale=reason, before={"status": "freeze"}, after={"status": "regular"}, request=request)
    _notify_parties(db, s, "Subscription resumed", f"{s.subscription_code} classes resume from today. {reason}")
    db.commit()
    return redirect(f"/subscriptions/{s.id}", "Subscription resumed; classes regenerated.")


@router.post("/{id}/cancel", include_in_schema=False)
async def cancel(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("subscriptions.update"))):
    from app.services import billing
    s = _get(db, id)
    form = await request.form()
    reason = _reason(form)
    if not reason:
        return redirect(f"/subscriptions/{s.id}", "A cancellation reason is required.", "error")
    try:
        billing.cancel_subscription(db, s, user, reason)
    except ValueError as exc:
        return redirect(f"/subscriptions/{s.id}", str(exc), "error")
    n = sched.end_subscription_schedule(db, user, s, "ended", reason, request=request)
    if s.teacher and s.teacher.user_id:
        notify(db, s.teacher.user_id, "Subscription cancelled", f"{s.student.full_name if s.student else 'A student'} ({s.subscription_code}) was cancelled: {reason}",
               event_type="subscription", link="/teacher/online-class")
    db.commit()
    return redirect(f"/subscriptions/{s.id}", f"Subscription cancelled; {n} future class(es) cancelled.")


@router.post("/{id}/complete", include_in_schema=False)
async def complete(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("subscriptions.update"))):
    s = _get(db, id)
    form = await request.form()
    reason = _reason(form) or "Course completed"
    if s.status in ("cancelled", "completed", "expired"):
        return redirect(f"/subscriptions/{s.id}", f"A {s.status} subscription cannot be completed.", "error")
    before = {"status": s.status}
    s.status = "completed"
    s.completion_date = parse_date(form.get("completion_date"), date.today())
    s.end_date = s.completion_date
    s.auto_renew = False
    if s.student and s.student.status in ("active", "trial"):
        s.student.status = "graduated"
    n = sched.end_subscription_schedule(db, user, s, "ended", reason, request=request)
    log_action(db, user, "status_change", "subscriptions", entity=s, description=f"{s.subscription_code} completed on {s.completion_date}; {n} class(es) cancelled",
               rationale=reason, before=before, after={"status": "completed"}, request=request, consequential=True)
    _notify_parties(db, s, "Subscription completed", f"Congratulations - {s.student.full_name if s.student else 'the student'} completed {s.course.name if s.course else 'the course'}.")
    db.commit()
    return redirect(f"/subscriptions/{s.id}", "Subscription marked Completed.")


@router.post("/{id}/follow-up", include_in_schema=False)
async def set_follow_up(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("subscriptions.update"))):
    s = _get(db, id)
    form = await request.form()
    before = {"follow_up_date": s.follow_up_date.isoformat() if s.follow_up_date else None}
    s.follow_up_date = parse_date(form.get("follow_up_date"))
    note = _reason(form)
    if note:
        s.remarks = ((s.remarks or "") + f"\n[{date.today()}] Follow-up: {note}").strip()
    log_action(db, user, "update", "subscriptions", entity=s, description=f"{s.subscription_code} follow-up set to {s.follow_up_date or 'none'}",
               rationale=note or None, before=before, after={"follow_up_date": s.follow_up_date.isoformat() if s.follow_up_date else None}, request=request)
    db.commit()
    return redirect(f"/subscriptions/{s.id}", "Follow-up date saved.")


@router.post("/{id}/change-teacher", include_in_schema=False)
async def change_teacher(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("subscriptions.update"))):
    s = _get(db, id)
    form = await request.form()
    reason = _reason(form)
    teacher = db.get(Teacher, parse_int(form.get("teacher_id"))) if form.get("teacher_id") else None
    if not teacher or not teacher.is_verified:
        return redirect(f"/subscriptions/{s.id}", "Choose a verified teacher.", "error")
    if not reason:
        return redirect(f"/subscriptions/{s.id}", "A reason is required to change the teacher.", "error")
    if teacher.id == s.teacher_id:
        return redirect(f"/subscriptions/{s.id}", "That teacher already holds this subscription.", "error")
    old = s.teacher
    sch = sched.subscription_schedule(db, s)
    if s.slot and s.days_of_week and sched.teacher_slot_busy(db, teacher.id, [int(d) for d in s.days_of_week], s.slot, exclude_schedule_id=sch.id if sch else None):
        return redirect(f"/subscriptions/{s.id}", f"{teacher.full_name} already has a class in {s.slot.label} on those days.", "error")
    before = {"teacher_id": s.teacher_id}
    s.teacher_id = teacher.id
    s.supervisor_id = teacher.supervisor_id or s.supervisor_id
    if s.student:
        s.student.teacher_id = teacher.id
    from app.services import billing
    s.teacher_cost_base = billing.teacher_monthly_cost(db, teacher, s.sessions_per_week or len(s.days_of_week or []))
    try:
        sched.sync_subscription_schedule(db, user, s, reason=reason, request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(f"/subscriptions/{s.id}", str(exc), "error")
    log_action(db, user, "assign", "subscriptions", entity=s, description=f"{s.subscription_code} teacher changed {old.full_name if old else '-'} -> {teacher.full_name}",
               rationale=reason, before=before, after={"teacher_id": teacher.id}, request=request, consequential=True)
    for t in (old, teacher):
        if t and t.user_id:
            notify(db, t.user_id, "Teacher change", f"{s.student.full_name if s.student else 'Student'} ({s.subscription_code}) now with {teacher.full_name}. {reason}",
                   event_type="teacher_change", link="/teacher/online-class")
    _notify_parties(db, s, "Teacher changed", f"{s.student.full_name if s.student else 'Your student'} will now study with {teacher.full_name}. {reason}", teacher=False)
    db.commit()
    return redirect(f"/subscriptions/{s.id}", f"Teacher changed to {teacher.full_name}; future classes regenerated.")


@router.post("/{id}/change-slot", include_in_schema=False)
async def change_slot(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("subscriptions.update"))):
    s = _get(db, id)
    form = await request.form()
    reason = _reason(form)
    slot = db.get(SessionSlot, parse_int(form.get("slot_id"))) if form.get("slot_id") else None
    days = _read_days(form) or [int(d) for d in (s.days_of_week or [])]
    if not slot:
        return redirect(f"/subscriptions/{s.id}", "Choose a session time.", "error")
    if not reason:
        return redirect(f"/subscriptions/{s.id}", "A reason is required to change the session time.", "error")
    if not s.teacher_id:
        return redirect(f"/subscriptions/{s.id}", "Assign a teacher first.", "error")
    sch = sched.subscription_schedule(db, s)
    if sched.teacher_slot_busy(db, s.teacher_id, days, slot, exclude_schedule_id=sch.id if sch else None):
        return redirect(f"/subscriptions/{s.id}", f"{s.teacher.full_name if s.teacher else 'The teacher'} already has a class in {slot.label} on those days.", "error")
    before = {"slot": s.slot.label if s.slot else None, "days": s.days_of_week}
    s.slot_id = slot.id
    s.days_of_week = days
    s.sessions_per_week = len(days)
    s.session_minutes = slot.duration_minutes or 30
    s.session_category = slot.category
    try:
        sched.sync_subscription_schedule(db, user, s, reason=reason, request=request)
    except ValueError as exc:
        db.rollback()
        return redirect(f"/subscriptions/{s.id}", str(exc), "error")
    log_action(db, user, "schedule_change", "subscriptions", entity=s, description=f"{s.subscription_code} moved to {slot.label} on {sched.day_label(days)}",
               rationale=reason, before=before, after={"slot": slot.label, "days": days}, request=request, consequential=True)
    _notify_parties(db, s, "Class time changed", f"{s.student.full_name if s.student else 'Your student'}'s classes move to {slot.label} PKT on {sched.day_label(days)}. {reason}")
    db.commit()
    return redirect(f"/subscriptions/{s.id}", f"Session changed to {slot.label}; future classes regenerated.")


def _notify_parties(db: Session, s: Subscription, title: str, body: str, teacher: bool = True) -> None:
    if s.client and s.client.user_id:
        notify(db, s.client.user_id, title, body, event_type="subscription", link="/portal/schedule")
    if teacher and s.teacher and s.teacher.user_id:
        notify(db, s.teacher.user_id, title, body, event_type="subscription", link="/teacher/online-class")
