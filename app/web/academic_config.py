"""WP-1 — Academic Configuration (mirrors the ERP's "Academic Configuration" group, audit §3.13).

Sessions · Courses · Packages · Define Books · Change Staff Sorting · Invoice Addition List · Invoice Additions Master ·
Receipt Beneficiary Accounts · Client Academic Groups · MS Team Users · Question Bank · Define Assessment.
Every list page: status tiles, filter bar, bordered table with the ERP column labels, Create (modal), inline edit,
status toggle; every mutation writes an audit event and redirects with a flash message.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import parse_bool, parse_date, parse_float, parse_int, redirect
from app.database import get_db
from app.models.academic import Book, Course, Package
from app.models.core import User
from app.models.erp import (SESSION_CATEGORIES, AssessmentDefinition, BeneficiaryAccount, ClientAcademicGroup,
                            InvoiceAdditionRule, InvoiceAdditionType, QuestionBankItem, SessionSlot, TeamsUser)
from app.models.finance import Subscription
from app.models.people import Client, Employee, Student

router = APIRouter(prefix="/academics/config", dependencies=[Depends(csrf_protect)])

BASE = "/academics/config"
MODULE = "academic_config"
STATUSES = [("active", "Active"), ("inactive", "Inactive")]
COURSE_TYPES = ["Islamic Courses", "Academics Tutoring"]
ADDITION_TYPES = [("discount", "Discount"), ("charge", "Charge"), ("tax", "Tax")]
RULE_LEVELS = [("subscription", "Subscription"), ("client", "Client"), ("global", "Global")]
IMPLEMENTATION_TYPES = [("fixed", "Fixed"), ("percent", "Percent")]
PAYMENT_MODES = ["Online Payment Gateway", "Bank", "Cash"]
PAYMENT_CATEGORIES = ["Stripe", "PayPal", "UBL", "Meezan Bank", "Wise", "HBL", "Bank Alfalah", "Cash"]
SHIFT_GROUPS = [("morning", "Morning"), ("night", "Night")]
QUESTION_TYPES = [("oral", "Oral"), ("written", "Written"), ("recitation", "Recitation"), ("mcq", "MCQ")]
DURATIONS = [(30, "30 Minutes"), (45, "45 Minutes"), (60, "60 Minutes")]
CURRENCIES = ["GBP", "USD", "EUR", "CAD", "AUD", "PKR"]

CARDS = [  # title, url, icon, blurb
    ("Sessions", f"{BASE}/sessions", "clock", "Bookable class times per category"),
    ("Courses", f"{BASE}/courses", "book-open", "Type, fee, attendance, curriculum link"),
    ("Packages", f"{BASE}/packages", "package", "Minimum / maximum days per week"),
    ("Define Books", f"{BASE}/books", "book", "Internal and public books, bulk upload"),
    ("Change Staff Sorting", f"{BASE}/staff-sorting", "arrow-up-down", "Order used across teacher lists"),
    ("Invoice Addition List", f"{BASE}/invoice-additions", "list-plus", "Named discounts, charges and taxes"),
    ("Invoice Additions Master", f"{BASE}/invoice-addition-rules", "sliders-horizontal", "When and how additions apply"),
    ("Receipt Beneficiary Accounts", f"{BASE}/beneficiary-accounts", "landmark", "Where money is received"),
    ("Client Academic Groups", f"{BASE}/client-groups", "users-round", "Morning / Night groups and representatives"),
    ("MS Team Users", f"{BASE}/teams-users", "monitor", "Teams accounts for staff and clients"),
    ("Question Bank", f"{BASE}/question-bank", "help-circle", "Questions per book and assessment"),
    ("Define Assessment", f"{BASE}/assessments", "file-check", "Assessments with passing and total marks"),
]


# =============================================================================== helpers
def _get(db: Session, model, id: int, label: str):
    obj = db.get(model, id)
    if not obj:
        raise HTTPException(404, f"{label} not found")
    return obj


def _perms(user: User) -> dict:
    return {"can_add": rbac.has_permission(user, f"{MODULE}.add"),
            "can_edit": rbac.has_permission(user, f"{MODULE}.update"),
            "can_configure": rbac.has_permission(user, f"{MODULE}.configure")}


def _status_counts(db: Session, model) -> dict:
    counts = dict(db.query(model.status, func.count(model.id)).group_by(model.status).all())
    total = sum(counts.values())
    return {"total": total, "active": counts.get("active", 0), "inactive": counts.get("inactive", 0)}


def _toggle_status(db: Session, user: User, request: Request, obj, label: str, back: str):
    before = snapshot(obj)
    obj.status = "inactive" if obj.status == "active" else "active"
    log_action(db, user, "status_change", MODULE, entity=obj, description=f"{label} marked {obj.status}",
               before=before, after=snapshot(obj), request=request)
    db.commit()
    return redirect(back, f"{label} marked {obj.status}.")


def _parse_time(value: Optional[str], default: time = time(7, 0)) -> time:
    if not value:
        return default
    value = value.strip()
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
        try:
            return datetime.strptime(value.upper(), fmt).time()
        except ValueError:
            continue
    return default


def slot_label(start: time, duration: int) -> str:
    """ERP session label: "07:00 AM - 07:30 AM"."""
    total = start.hour * 60 + start.minute + duration
    end = time((total // 60) % 24, total % 60)
    return f"{start.strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}"


def _course_options(db: Session) -> list[tuple[int, str]]:
    return [(c.id, f"{c.code} — {c.name}") for c in db.query(Course).order_by(Course.order, Course.id)]


def _book_options(db: Session) -> list[tuple[int, str]]:
    return [(b.id, f"{b.title} ({b.course.code})" if b.course else b.title) for b in db.query(Book).order_by(Book.title)]


def _status_field(form, default: str = "active") -> str:
    v = (form.get("status") or default).strip().lower()
    return v if v in ("active", "inactive") else default


# =============================================================================== index
@router.get("", include_in_schema=False)
def index(request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    counts = {
        f"{BASE}/sessions": db.query(func.count(SessionSlot.id)).scalar() or 0,
        f"{BASE}/courses": db.query(func.count(Course.id)).scalar() or 0,
        f"{BASE}/packages": db.query(func.count(Package.id)).scalar() or 0,
        f"{BASE}/books": db.query(func.count(Book.id)).scalar() or 0,
        f"{BASE}/staff-sorting": db.query(func.count(Employee.id)).filter(Employee.status == "active").scalar() or 0,
        f"{BASE}/invoice-additions": db.query(func.count(InvoiceAdditionType.id)).scalar() or 0,
        f"{BASE}/invoice-addition-rules": db.query(func.count(InvoiceAdditionRule.id)).scalar() or 0,
        f"{BASE}/beneficiary-accounts": db.query(func.count(BeneficiaryAccount.id)).scalar() or 0,
        f"{BASE}/client-groups": db.query(func.count(ClientAcademicGroup.id)).scalar() or 0,
        f"{BASE}/teams-users": db.query(func.count(TeamsUser.id)).scalar() or 0,
        f"{BASE}/question-bank": db.query(func.count(QuestionBankItem.id)).scalar() or 0,
        f"{BASE}/assessments": db.query(func.count(AssessmentDefinition.id)).scalar() or 0,
    }
    cards = [{"title": t, "url": u, "icon": i, "blurb": b, "count": counts.get(u, 0)} for t, u, i, b in CARDS]
    return render(request, "academic_config/index.html", {"user": user, "cards": cards})


# =============================================================================== 1. Sessions
@router.get("/sessions", include_in_schema=False)
def sessions_page(request: Request, category: str = "", status: str = "", q: str = "",
                  db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    query = db.query(SessionSlot)
    if category:
        query = query.filter(SessionSlot.category == category)
    if status:
        query = query.filter(SessionSlot.status == status)
    if q:
        query = query.filter(SessionSlot.label.ilike(f"%{q}%"))
    slots = query.order_by(SessionSlot.category, SessionSlot.sort_no, SessionSlot.start_time).all()
    stats = _status_counts(db, SessionSlot)
    by_cat = dict(db.query(SessionSlot.category, func.count(SessionSlot.id)).group_by(SessionSlot.category).all())
    return render(request, "academic_config/sessions.html", {
        "user": user, "slots": slots, "stats": stats, "by_cat": by_cat, "category": category, "status": status, "q": q,
        "categories": SESSION_CATEGORIES, "statuses": STATUSES, "durations": DURATIONS,
        "next_sort": (db.query(func.max(SessionSlot.sort_no)).filter(SessionSlot.category == (category or "30 Minutes")).scalar() or 0) + 1,
        **_perms(user)})


def _apply_slot(slot: SessionSlot, form) -> None:
    slot.category = (form.get("category") or slot.category or "30 Minutes").strip()
    slot.start_time = _parse_time(form.get("start_time"), slot.start_time or time(7, 0))
    slot.duration_minutes = parse_int(form.get("duration_minutes"), slot.duration_minutes or 30)
    label = (form.get("label") or "").strip()
    slot.label = label or slot_label(slot.start_time, slot.duration_minutes)
    slot.status = _status_field(form, slot.status or "active")
    slot.sort_no = parse_float(form.get("sort_no"), slot.sort_no or 0)


@router.post("/sessions/new", include_in_schema=False)
async def session_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.add"))):
    form = await request.form()
    slot = SessionSlot(category="30 Minutes", start_time=time(7, 0), duration_minutes=30, status="active", sort_no=0)
    _apply_slot(slot, form)
    dup = db.query(SessionSlot).filter(SessionSlot.category == slot.category, SessionSlot.start_time == slot.start_time).first()
    if dup:
        return redirect(f"{BASE}/sessions?category={slot.category}", f"A {slot.category} session starting {slot.label[:8]} already exists.", "error")
    db.add(slot)
    db.flush()
    log_action(db, user, "create", MODULE, entity=slot, description=f"Session {slot.label} ({slot.category}) created",
               after=snapshot(slot), request=request)
    db.commit()
    return redirect(f"{BASE}/sessions?category={slot.category}", f"Session {slot.label} created.")


@router.post("/sessions/{sid}/edit", include_in_schema=False)
async def session_edit(sid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    slot = _get(db, SessionSlot, sid, "Session")
    form = await request.form()
    before = snapshot(slot)
    _apply_slot(slot, form)
    log_action(db, user, "update", MODULE, entity=slot, description=f"Session {slot.label} updated", before=before,
               after=snapshot(slot), request=request)
    db.commit()
    return redirect(f"{BASE}/sessions?category={slot.category}", f"Session {slot.label} updated.")


@router.post("/sessions/{sid}/toggle", include_in_schema=False)
async def session_toggle(sid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    slot = _get(db, SessionSlot, sid, "Session")
    return _toggle_status(db, user, request, slot, f"Session {slot.label}", f"{BASE}/sessions?category={slot.category}")


# =============================================================================== 2. Courses
@router.get("/courses", include_in_schema=False)
def courses_page(request: Request, q: str = "", course_type: str = "", status: str = "",
                 db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    query = db.query(Course)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Course.name.ilike(like), Course.code.ilike(like)))
    if course_type:
        query = query.filter(Course.course_type == course_type)
    if status == "active":
        query = query.filter(Course.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(Course.is_active.is_(False))
    courses = query.order_by(Course.order, Course.id).all()
    total = db.query(func.count(Course.id)).scalar() or 0
    active = db.query(func.count(Course.id)).filter(Course.is_active.is_(True)).scalar() or 0
    by_type = dict(db.query(Course.course_type, func.count(Course.id)).group_by(Course.course_type).all())
    return render(request, "academic_config/courses.html", {
        "user": user, "courses": courses, "q": q, "course_type": course_type, "status": status,
        "stats": {"total": total, "active": active, "inactive": total - active,
                  "islamic": by_type.get("Islamic Courses", 0), "tutoring": by_type.get("Academics Tutoring", 0)},
        "course_types": COURSE_TYPES, "statuses": STATUSES, **_perms(user)})


def _apply_course(c: Course, form) -> None:
    c.name = (form.get("name") or c.name).strip()
    c.course_type = form.get("course_type") if form.get("course_type") in COURSE_TYPES else (c.course_type or "Islamic Courses")
    c.fee = parse_float(form.get("fee"), float(c.fee or 0))
    c.attendance_required = parse_bool(form.get("attendance_required"))
    c.curriculum_link = (form.get("curriculum_link") or "").strip() or None
    c.is_active = _status_field(form, "active" if c.is_active else "inactive") == "active"
    if form.get("arabic_name") is not None:
        c.arabic_name = (form.get("arabic_name") or "").strip() or c.arabic_name
    if form.get("description") is not None:
        c.description = (form.get("description") or "").strip() or c.description


@router.post("/courses/new", include_in_schema=False)
async def course_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.add"))):
    form = await request.form()
    code = (form.get("code") or "").strip().upper().replace(" ", "_")[:20]
    name = (form.get("name") or "").strip()
    if not code or not name:
        return redirect(f"{BASE}/courses", "Course code and name are required.", "error")
    if db.query(Course).filter(Course.code == code).first():
        return redirect(f"{BASE}/courses", f"A course with code {code} already exists.", "error")
    c = Course(code=code, name=name, order=(db.query(func.max(Course.order)).scalar() or 0) + 1,
               default_session_minutes=parse_int(form.get("default_session_minutes"), 30) or 30)
    _apply_course(c, form)
    db.add(c)
    db.flush()
    log_action(db, user, "create", MODULE, entity=c, description=f"Course {c.code} — {c.name} created", after=snapshot(c), request=request)
    db.commit()
    return redirect(f"{BASE}/courses", f"Course {c.name} created.")


@router.post("/courses/{cid}/edit", include_in_schema=False)
async def course_edit(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    c = _get(db, Course, cid, "Course")
    form = await request.form()
    before = snapshot(c)
    _apply_course(c, form)
    log_action(db, user, "update", MODULE, entity=c, description=f"Course {c.code} updated", before=before, after=snapshot(c), request=request)
    db.commit()
    return redirect(f"{BASE}/courses", f"Course {c.name} updated.")


@router.post("/courses/{cid}/toggle", include_in_schema=False)
async def course_toggle(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    c = _get(db, Course, cid, "Course")
    before = snapshot(c)
    c.is_active = not c.is_active
    log_action(db, user, "status_change", MODULE, entity=c, description=f"Course {c.code} {'activated' if c.is_active else 'deactivated'}",
               before=before, after=snapshot(c), request=request)
    db.commit()
    return redirect(f"{BASE}/courses", f"Course {c.name} {'activated' if c.is_active else 'deactivated'}.")


# =============================================================================== 3. Packages
@router.get("/packages", include_in_schema=False)
def packages_page(request: Request, q: str = "", status: str = "", course_id: Optional[int] = None,
                  db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    query = db.query(Package)
    if q:
        query = query.filter(Package.name.ilike(f"%{q}%"))
    if status == "active":
        query = query.filter(Package.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(Package.is_active.is_(False))
    if course_id:
        query = query.filter(Package.course_id == course_id)
    packages = query.order_by(Package.max_days, Package.min_days, Package.name).all()
    total = db.query(func.count(Package.id)).scalar() or 0
    active = db.query(func.count(Package.id)).filter(Package.is_active.is_(True)).scalar() or 0
    trial = db.query(func.count(Package.id)).filter(Package.is_trial.is_(True)).scalar() or 0
    return render(request, "academic_config/packages.html", {
        "user": user, "packages": packages, "q": q, "status": status, "course_id": course_id,
        "stats": {"total": total, "active": active, "inactive": total - active, "trial": trial},
        "course_options": _course_options(db), "currencies": CURRENCIES, "statuses": STATUSES, **_perms(user)})


def _apply_package(p: Package, form) -> None:
    p.name = (form.get("name") or p.name).strip()
    p.description = (form.get("description") or "").strip() or None
    p.min_days = max(1, parse_int(form.get("min_days"), p.min_days or 1) or 1)
    p.max_days = max(p.min_days, parse_int(form.get("max_days"), p.max_days or p.min_days) or p.min_days)
    p.sessions_per_week = parse_int(form.get("sessions_per_week"), p.max_days) or p.max_days
    p.session_minutes = parse_int(form.get("session_minutes"), p.session_minutes or 30) or 30
    p.price = parse_float(form.get("price"), float(p.price or 0))
    p.currency = (form.get("currency") or p.currency or "GBP").upper()[:3]
    p.course_id = parse_int(form.get("course_id"))
    p.is_trial = parse_bool(form.get("is_trial"))
    p.is_active = _status_field(form, "active" if p.is_active else "inactive") == "active"


@router.post("/packages/new", include_in_schema=False)
async def package_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.add"))):
    form = await request.form()
    if not (form.get("name") or "").strip():
        return redirect(f"{BASE}/packages", "Package description is required.", "error")
    p = Package(name=form.get("name").strip(), min_days=1, max_days=5, sessions_per_week=5, session_minutes=30, price=0, currency="GBP", is_active=True)
    _apply_package(p, form)
    db.add(p)
    db.flush()
    log_action(db, user, "create", MODULE, entity=p, description=f"Package {p.name} created", after=snapshot(p), request=request)
    db.commit()
    return redirect(f"{BASE}/packages", f"Package {p.name} created.")


@router.post("/packages/{pid}/edit", include_in_schema=False)
async def package_edit(pid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    p = _get(db, Package, pid, "Package")
    form = await request.form()
    before = snapshot(p)
    _apply_package(p, form)
    log_action(db, user, "update", MODULE, entity=p, description=f"Package {p.name} updated", before=before, after=snapshot(p),
               rationale=(form.get("reason") or None), request=request)
    db.commit()
    return redirect(f"{BASE}/packages", f"Package {p.name} updated.")


@router.post("/packages/{pid}/toggle", include_in_schema=False)
async def package_toggle(pid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    p = _get(db, Package, pid, "Package")
    before = snapshot(p)
    p.is_active = not p.is_active
    log_action(db, user, "status_change", MODULE, entity=p, description=f"Package {p.name} {'activated' if p.is_active else 'deactivated'}",
               before=before, after=snapshot(p), request=request)
    db.commit()
    return redirect(f"{BASE}/packages", f"Package {p.name} {'activated' if p.is_active else 'deactivated'}.")


# =============================================================================== 4. Define Books
BOOK_TEMPLATE_HEADERS = ["course_code", "title", "arabic_title", "sort_no", "is_public"]


@router.get("/books", include_in_schema=False)
def books_page(request: Request, tab: str = "internal", q: str = "", course_id: Optional[int] = None, status: str = "",
               db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    tab = "public" if tab == "public" else "internal"
    query = db.query(Book).filter(Book.is_public.is_(tab == "public"))
    if q:
        query = query.filter(or_(Book.title.ilike(f"%{q}%"), Book.arabic_title.ilike(f"%{q}%")))
    if course_id:
        query = query.filter(Book.course_id == course_id)
    if status:
        query = query.filter(Book.status == status)
    books = query.order_by(Book.order, Book.title).all()
    stats = _status_counts(db, Book)
    stats["public"] = db.query(func.count(Book.id)).filter(Book.is_public.is_(True)).scalar() or 0
    stats["internal"] = stats["total"] - stats["public"]
    return render(request, "academic_config/books.html", {
        "user": user, "books": books, "tab": tab, "q": q, "course_id": course_id, "status": status, "stats": stats,
        "tabs": [("internal", f"Internal Books ({stats['internal']})", f"{BASE}/books?tab=internal"),
                 ("public", f"Public Books ({stats['public']})", f"{BASE}/books?tab=public")],
        "course_options": _course_options(db), "statuses": STATUSES, **_perms(user)})


@router.get("/books/template.csv", include_in_schema=False)
def books_template(db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(BOOK_TEMPLATE_HEADERS)
    first = db.query(Course).order_by(Course.order).first()
    w.writerow([first.code if first else "QAIDA", "Sample Book Title", "", "1", "0"])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="books_template.csv"'})


def _apply_book(b: Book, form) -> None:
    b.title = (form.get("title") or b.title).strip()
    b.arabic_title = (form.get("arabic_title") or "").strip() or None
    b.order = parse_int(form.get("sort_no"), b.order or 0) or 0
    b.is_public = parse_bool(form.get("is_public"))
    b.status = _status_field(form, b.status or "active")
    if form.get("course_id"):
        b.course_id = parse_int(form.get("course_id"), b.course_id)


@router.post("/books/new", include_in_schema=False)
async def book_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.add"))):
    form = await request.form()
    course_id = parse_int(form.get("course_id"))
    title = (form.get("title") or "").strip()
    if not course_id or not title:
        return redirect(f"{BASE}/books", "Course and book name are required.", "error")
    b = Book(course_id=course_id, title=title, order=0, is_public=False, status="active")
    _apply_book(b, form)
    db.add(b)
    db.flush()
    log_action(db, user, "create", MODULE, entity=b, description=f"Book {b.title} created", after=snapshot(b), request=request)
    db.commit()
    return redirect(f"{BASE}/books?tab={'public' if b.is_public else 'internal'}", f"Book {b.title} created.")


@router.post("/books/upload", include_in_schema=False)
async def books_upload(request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.add"))):
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return redirect(f"{BASE}/books", "Choose a CSV file to upload.", "error")
    raw = await upload.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    courses = {c.code.upper(): c for c in db.query(Course)}
    created, skipped, errors = 0, 0, []
    for n, row in enumerate(reader, start=2):
        code = (row.get("course_code") or "").strip().upper()
        title = (row.get("title") or "").strip()
        if not code or not title:
            errors.append(f"line {n}: course_code and title are required")
            continue
        course = courses.get(code)
        if not course:
            errors.append(f"line {n}: unknown course code {code}")
            continue
        if db.query(Book).filter(Book.course_id == course.id, Book.title == title).first():
            skipped += 1
            continue
        b = Book(course_id=course.id, title=title, arabic_title=(row.get("arabic_title") or "").strip() or None,
                 order=parse_int((row.get("sort_no") or "").strip(), 0) or 0, is_public=parse_bool((row.get("is_public") or "").strip()),
                 status="active")
        db.add(b)
        created += 1
    db.flush()
    log_action(db, user, "import", MODULE, entity_type="Book", description=f"Book upload: {created} created, {skipped} skipped, {len(errors)} errors",
               after={"created": created, "skipped": skipped, "errors": errors[:20]}, request=request)
    db.commit()
    msg = f"Upload finished: {created} book(s) created, {skipped} already existed."
    if errors:
        return redirect(f"{BASE}/books", msg + " " + "; ".join(errors[:3]), "warning")
    return redirect(f"{BASE}/books", msg)


@router.post("/books/{bid}/edit", include_in_schema=False)
async def book_edit(bid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    b = _get(db, Book, bid, "Book")
    form = await request.form()
    before = snapshot(b)
    _apply_book(b, form)
    log_action(db, user, "update", MODULE, entity=b, description=f"Book {b.title} updated", before=before, after=snapshot(b), request=request)
    db.commit()
    return redirect(f"{BASE}/books?tab={'public' if b.is_public else 'internal'}", f"Book {b.title} updated.")


@router.post("/books/{bid}/toggle", include_in_schema=False)
async def book_toggle(bid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    b = _get(db, Book, bid, "Book")
    return _toggle_status(db, user, request, b, f"Book {b.title}", f"{BASE}/books?tab={'public' if b.is_public else 'internal'}")


# =============================================================================== 5. Change Staff Sorting
def _sorted_staff(db: Session) -> list[Employee]:
    return db.query(Employee).filter(Employee.status == "active").order_by(Employee.sort_no, Employee.full_name).all()


@router.get("/staff-sorting", include_in_schema=False)
def staff_sorting_page(request: Request, q: str = "", department_id: Optional[int] = None, shift: str = "",
                       db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    from app.models.core import Department
    staff = _sorted_staff(db)
    if q:
        ql = q.lower()
        staff = [e for e in staff if ql in (e.full_name or "").lower() or ql in (e.employee_code or "").lower() or ql in (e.designation or "").lower()]
    if department_id:
        staff = [e for e in staff if e.department_id == department_id]
    if shift:
        staff = [e for e in staff if e.shift == shift]
    all_active = db.query(func.count(Employee.id)).filter(Employee.status == "active").scalar() or 0
    teachers = db.query(func.count(Employee.id)).filter(Employee.status == "active", Employee.is_teacher.is_(True)).scalar() or 0
    unsorted = db.query(func.count(Employee.id)).filter(Employee.status == "active", Employee.sort_no == 0).scalar() or 0
    return render(request, "academic_config/staff_sorting.html", {
        "user": user, "staff": staff, "q": q, "department_id": department_id, "shift": shift,
        "stats": {"active": all_active, "teachers": teachers, "staff": all_active - teachers, "unsorted": unsorted},
        "department_options": [(d.id, d.name) for d in db.query(Department).order_by(Department.name)],
        "shift_options": [("morning", "Morning"), ("evening", "Evening"), ("night", "Night")], **_perms(user)})


@router.post("/staff-sorting/save", include_in_schema=False)
async def staff_sorting_save(request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    form = await request.form()
    changed = []
    for key, value in form.multi_items():
        if not key.startswith("sort_"):
            continue
        eid = parse_int(key[5:])
        emp = db.get(Employee, eid) if eid else None
        if not emp:
            continue
        new = parse_int(value, emp.sort_no) or 0
        if new != (emp.sort_no or 0):
            changed.append((emp.employee_code, emp.sort_no, new))
            emp.sort_no = new
    if changed:
        log_action(db, user, "update", MODULE, entity_type="Employee", description=f"Staff sorting changed for {len(changed)} employee(s)",
                   after={"changes": [{"code": c, "from": f, "to": t} for c, f, t in changed]}, request=request)
        db.commit()
        return redirect(f"{BASE}/staff-sorting", f"Sort order saved for {len(changed)} employee(s).")
    return redirect(f"{BASE}/staff-sorting", "No sort numbers were changed.", "info")


@router.post("/staff-sorting/{eid}/move", include_in_schema=False)
async def staff_sorting_move(eid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    emp = _get(db, Employee, eid, "Employee")
    form = await request.form()
    direction = form.get("direction") or "up"
    staff = _sorted_staff(db)
    # normalise so every active employee has a unique sequential sort number
    for i, e in enumerate(staff, start=1):
        e.sort_no = i
    idx = next((i for i, e in enumerate(staff) if e.id == emp.id), None)
    if idx is None:
        return redirect(f"{BASE}/staff-sorting", "Only active employees can be re-ordered.", "error")
    swap = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap < len(staff):
        other = staff[swap]
        emp.sort_no, other.sort_no = other.sort_no, emp.sort_no
        log_action(db, user, "update", MODULE, entity=emp, description=f"{emp.full_name} moved {direction} (swapped with {other.full_name})",
                   after={"sort_no": emp.sort_no, "swapped_with": other.employee_code}, request=request)
    db.commit()
    return redirect(f"{BASE}/staff-sorting", f"{emp.full_name} moved {direction}.")


# =============================================================================== 6. Invoice Addition List
@router.get("/invoice-additions", include_in_schema=False)
def invoice_additions_page(request: Request, q: str = "", addition_type: str = "", status: str = "",
                           db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    query = db.query(InvoiceAdditionType)
    if q:
        query = query.filter(InvoiceAdditionType.description.ilike(f"%{q}%"))
    if addition_type:
        query = query.filter(InvoiceAdditionType.addition_type == addition_type)
    if status:
        query = query.filter(InvoiceAdditionType.status == status)
    rows = query.order_by(InvoiceAdditionType.addition_type, InvoiceAdditionType.description).all()
    stats = _status_counts(db, InvoiceAdditionType)
    by_type = dict(db.query(InvoiceAdditionType.addition_type, func.count(InvoiceAdditionType.id)).group_by(InvoiceAdditionType.addition_type).all())
    stats.update({"discount": by_type.get("discount", 0), "charge": by_type.get("charge", 0), "tax": by_type.get("tax", 0)})
    rules = dict(db.query(InvoiceAdditionRule.addition_type_id, func.count(InvoiceAdditionRule.id)).group_by(InvoiceAdditionRule.addition_type_id).all())
    return render(request, "academic_config/invoice_additions.html", {
        "user": user, "rows": rows, "stats": stats, "q": q, "addition_type": addition_type, "status": status, "rules": rules,
        "addition_types": ADDITION_TYPES, "statuses": STATUSES, **_perms(user)})


def _apply_addition(t: InvoiceAdditionType, form) -> None:
    t.description = (form.get("description") or t.description).strip()
    at = form.get("addition_type")
    t.addition_type = at if at in ("discount", "charge", "tax") else (t.addition_type or "discount")
    t.status = _status_field(form, t.status or "active")


@router.post("/invoice-additions/new", include_in_schema=False)
async def invoice_addition_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.add"))):
    form = await request.form()
    if not (form.get("description") or "").strip():
        return redirect(f"{BASE}/invoice-additions", "Description is required.", "error")
    t = InvoiceAdditionType(description=form.get("description").strip(), addition_type="discount", status="active")
    _apply_addition(t, form)
    db.add(t)
    db.flush()
    log_action(db, user, "create", MODULE, entity=t, description=f"Invoice addition {t.description} ({t.addition_type}) created", after=snapshot(t), request=request)
    db.commit()
    return redirect(f"{BASE}/invoice-additions", f"Invoice addition {t.description} created.")


@router.post("/invoice-additions/{tid}/edit", include_in_schema=False)
async def invoice_addition_edit(tid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    t = _get(db, InvoiceAdditionType, tid, "Invoice addition")
    form = await request.form()
    before = snapshot(t)
    _apply_addition(t, form)
    log_action(db, user, "update", MODULE, entity=t, description=f"Invoice addition {t.description} updated", before=before, after=snapshot(t), request=request)
    db.commit()
    return redirect(f"{BASE}/invoice-additions", f"Invoice addition {t.description} updated.")


@router.post("/invoice-additions/{tid}/toggle", include_in_schema=False)
async def invoice_addition_toggle(tid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    t = _get(db, InvoiceAdditionType, tid, "Invoice addition")
    return _toggle_status(db, user, request, t, f"Invoice addition {t.description}", f"{BASE}/invoice-additions")


# =============================================================================== 7. Invoice Additions Master
def _rule_context(db: Session) -> dict:
    subs = (db.query(Subscription).join(Student, Student.id == Subscription.student_id)
            .filter(Subscription.status.in_(["active", "trial", "frozen", "pending_approval"]))
            .order_by(Subscription.subscription_code).limit(400).all())
    return {
        "type_options": [(t.id, f"{t.description} ({t.addition_type})") for t in
                         db.query(InvoiceAdditionType).filter(InvoiceAdditionType.status == "active").order_by(InvoiceAdditionType.description)],
        "subscription_options": [(s.id, f"{s.subscription_code} — {s.student.full_name if s.student else ''}") for s in subs],
        "client_options": [(c.id, f"{c.client_code} — {c.full_name}") for c in
                           db.query(Client).filter(Client.status.in_(["active", "trial"])).order_by(Client.client_code).limit(500)],
        "levels": RULE_LEVELS, "implementation_types": IMPLEMENTATION_TYPES, "statuses": STATUSES,
    }


@router.get("/invoice-addition-rules", include_in_schema=False)
def invoice_rules_page(request: Request, level: str = "", status: str = "", addition_type_id: Optional[int] = None,
                       db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    query = db.query(InvoiceAdditionRule)
    if level:
        query = query.filter(InvoiceAdditionRule.level == level)
    if status:
        query = query.filter(InvoiceAdditionRule.status == status)
    if addition_type_id:
        query = query.filter(InvoiceAdditionRule.addition_type_id == addition_type_id)
    rules = query.order_by(InvoiceAdditionRule.level, InvoiceAdditionRule.from_date.desc(), InvoiceAdditionRule.id).all()
    stats = _status_counts(db, InvoiceAdditionRule)
    by_level = dict(db.query(InvoiceAdditionRule.level, func.count(InvoiceAdditionRule.id)).group_by(InvoiceAdditionRule.level).all())
    stats.update({"subscription": by_level.get("subscription", 0), "client": by_level.get("client", 0), "global": by_level.get("global", 0),
                  "auto": db.query(func.count(InvoiceAdditionRule.id)).filter(InvoiceAdditionRule.auto_assigned.is_(True)).scalar() or 0})
    return render(request, "academic_config/invoice_addition_rules.html", {
        "user": user, "rules": rules, "stats": stats, "level": level, "status": status, "addition_type_id": addition_type_id,
        "today": date.today(), **_rule_context(db), **_perms(user)})


def _apply_rule(r: InvoiceAdditionRule, form) -> Optional[str]:
    level = form.get("level")
    r.level = level if level in ("subscription", "client", "global") else (r.level or "global")
    r.addition_type_id = parse_int(form.get("addition_type_id"), r.addition_type_id)
    if not r.addition_type_id:
        return "Pick an entry from the Invoice Addition List."
    r.from_date = parse_date(form.get("from_date"), r.from_date or date.today())
    r.to_date = parse_date(form.get("to_date"))
    if r.to_date and r.to_date < r.from_date:
        return "To Date must not be before From Date."
    it = form.get("implementation_type")
    r.implementation_type = it if it in ("fixed", "percent") else (r.implementation_type or "fixed")
    r.amount = parse_float(form.get("amount"), float(r.amount or 0))
    if r.implementation_type == "percent" and not (0 <= r.amount <= 100):
        return "Percent amount must be between 0 and 100."
    r.status = _status_field(form, r.status or "active")
    r.auto_assigned = parse_bool(form.get("auto_assigned"))
    r.subscription_id = parse_int(form.get("subscription_id")) if r.level == "subscription" else None
    r.client_id = parse_int(form.get("client_id")) if r.level == "client" else None
    if r.level == "subscription" and not r.subscription_id:
        return "Pick the subscription this rule applies to."
    if r.level == "client" and not r.client_id:
        return "Pick the client this rule applies to."
    return None


@router.post("/invoice-addition-rules/new", include_in_schema=False)
async def invoice_rule_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.add"))):
    form = await request.form()
    r = InvoiceAdditionRule(level="global", from_date=date.today(), implementation_type="fixed", amount=0, status="active")
    err = _apply_rule(r, form)
    if err:
        return redirect(f"{BASE}/invoice-addition-rules", err, "error")
    db.add(r)
    db.flush()
    log_action(db, user, "create", MODULE, entity=r, description=f"Invoice addition rule #{r.id} ({r.level}, {r.implementation_type} {r.amount}) created",
               after=snapshot(r), rationale=(form.get("reason") or None), request=request)
    db.commit()
    return redirect(f"{BASE}/invoice-addition-rules", "Invoice addition rule created.")


@router.post("/invoice-addition-rules/{rid}/edit", include_in_schema=False)
async def invoice_rule_edit(rid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    r = _get(db, InvoiceAdditionRule, rid, "Invoice addition rule")
    form = await request.form()
    before = snapshot(r)
    err = _apply_rule(r, form)
    if err:
        db.rollback()
        return redirect(f"{BASE}/invoice-addition-rules", err, "error")
    log_action(db, user, "update", MODULE, entity=r, description=f"Invoice addition rule #{r.id} updated", before=before, after=snapshot(r),
               rationale=(form.get("reason") or None), request=request)
    db.commit()
    return redirect(f"{BASE}/invoice-addition-rules", "Invoice addition rule updated.")


@router.post("/invoice-addition-rules/{rid}/toggle", include_in_schema=False)
async def invoice_rule_toggle(rid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    r = _get(db, InvoiceAdditionRule, rid, "Invoice addition rule")
    return _toggle_status(db, user, request, r, f"Invoice addition rule #{r.id}", f"{BASE}/invoice-addition-rules")


# =============================================================================== 8. Receipt Beneficiary Accounts
@router.get("/beneficiary-accounts", include_in_schema=False)
def beneficiary_accounts_page(request: Request, payment_mode: str = "", status: str = "", q: str = "",
                              db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    query = db.query(BeneficiaryAccount)
    if payment_mode:
        query = query.filter(BeneficiaryAccount.payment_mode == payment_mode)
    if status:
        query = query.filter(BeneficiaryAccount.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(BeneficiaryAccount.account_name.ilike(like), BeneficiaryAccount.category.ilike(like)))
    rows = query.order_by(BeneficiaryAccount.payment_mode, BeneficiaryAccount.category, BeneficiaryAccount.account_name).all()
    stats = _status_counts(db, BeneficiaryAccount)
    by_mode = dict(db.query(BeneficiaryAccount.payment_mode, func.count(BeneficiaryAccount.id)).group_by(BeneficiaryAccount.payment_mode).all())
    stats.update({"gateway": by_mode.get("Online Payment Gateway", 0), "bank": by_mode.get("Bank", 0), "cash": by_mode.get("Cash", 0)})
    return render(request, "academic_config/beneficiary_accounts.html", {
        "user": user, "rows": rows, "stats": stats, "payment_mode": payment_mode, "status": status, "q": q,
        "payment_modes": PAYMENT_MODES, "categories": PAYMENT_CATEGORIES, "statuses": STATUSES, **_perms(user)})


def _apply_account(a: BeneficiaryAccount, form) -> None:
    pm = form.get("payment_mode")
    a.payment_mode = pm if pm in PAYMENT_MODES else (a.payment_mode or "Bank")
    a.category = (form.get("category") or a.category or "").strip()
    a.account_name = (form.get("account_name") or a.account_name).strip()
    a.account_details = (form.get("account_details") or "").strip() or None
    a.is_auto = parse_bool(form.get("is_auto"))
    a.status = _status_field(form, a.status or "active")


@router.post("/beneficiary-accounts/new", include_in_schema=False)
async def beneficiary_account_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.add"))):
    form = await request.form()
    if not (form.get("account_name") or "").strip():
        return redirect(f"{BASE}/beneficiary-accounts", "Beneficiary account name is required.", "error")
    a = BeneficiaryAccount(payment_mode="Bank", category="", account_name=form.get("account_name").strip(), status="active")
    _apply_account(a, form)
    db.add(a)
    db.flush()
    log_action(db, user, "create", MODULE, entity=a, description=f"Beneficiary account {a.account_name} created", after=snapshot(a), request=request)
    db.commit()
    return redirect(f"{BASE}/beneficiary-accounts", f"Beneficiary account {a.account_name} created.")


@router.post("/beneficiary-accounts/{aid}/edit", include_in_schema=False)
async def beneficiary_account_edit(aid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    a = _get(db, BeneficiaryAccount, aid, "Beneficiary account")
    form = await request.form()
    before = snapshot(a)
    _apply_account(a, form)
    log_action(db, user, "update", MODULE, entity=a, description=f"Beneficiary account {a.account_name} updated", before=before, after=snapshot(a), request=request)
    db.commit()
    return redirect(f"{BASE}/beneficiary-accounts", f"Beneficiary account {a.account_name} updated.")


@router.post("/beneficiary-accounts/{aid}/toggle", include_in_schema=False)
async def beneficiary_account_toggle(aid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    a = _get(db, BeneficiaryAccount, aid, "Beneficiary account")
    return _toggle_status(db, user, request, a, f"Beneficiary account {a.account_name}", f"{BASE}/beneficiary-accounts")


# =============================================================================== 9. Client Academic Groups
def _representative_options(db: Session) -> list[tuple[int, str]]:
    from app.models.core import Role
    users = (db.query(User).join(Role, Role.id == User.role_id)
             .filter(User.is_active.is_(True), Role.portal == "admin").order_by(User.full_name).all())
    return [(u.id, f"{u.full_name} — {u.role.name if u.role else 'User'}") for u in users]


@router.get("/client-groups", include_in_schema=False)
def client_groups_page(request: Request, status: str = "", shift_group: str = "",
                       db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    query = db.query(ClientAcademicGroup)
    if status:
        query = query.filter(ClientAcademicGroup.status == status)
    if shift_group:
        query = query.filter(ClientAcademicGroup.shift_group == shift_group)
    rows = query.order_by(ClientAcademicGroup.shift_group, ClientAcademicGroup.name).all()
    stats = _status_counts(db, ClientAcademicGroup)
    by_shift = dict(db.query(ClientAcademicGroup.shift_group, func.count(ClientAcademicGroup.id)).group_by(ClientAcademicGroup.shift_group).all())
    stats.update({"morning": by_shift.get("morning", 0), "night": by_shift.get("night", 0)})
    return render(request, "academic_config/client_groups.html", {
        "user": user, "rows": rows, "stats": stats, "status": status, "shift_group": shift_group,
        "representative_options": _representative_options(db), "shift_groups": SHIFT_GROUPS, "statuses": STATUSES, **_perms(user)})


def _apply_group(g: ClientAcademicGroup, form) -> None:
    g.name = (form.get("name") or g.name).strip()
    g.pseudo_name = (form.get("pseudo_name") or "").strip() or None
    sg = form.get("shift_group")
    g.shift_group = sg if sg in ("morning", "night") else (g.shift_group or "morning")
    g.representative_id = parse_int(form.get("representative_id"))
    g.status = _status_field(form, g.status or "active")


@router.post("/client-groups/new", include_in_schema=False)
async def client_group_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.add"))):
    form = await request.form()
    if not (form.get("name") or "").strip():
        return redirect(f"{BASE}/client-groups", "Group name is required.", "error")
    g = ClientAcademicGroup(name=form.get("name").strip(), shift_group="morning", status="active")
    _apply_group(g, form)
    db.add(g)
    db.flush()
    log_action(db, user, "create", MODULE, entity=g, description=f"Client academic group {g.name} created", after=snapshot(g), request=request)
    db.commit()
    return redirect(f"{BASE}/client-groups", f"Group {g.name} created.")


@router.post("/client-groups/{gid}/edit", include_in_schema=False)
async def client_group_edit(gid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    g = _get(db, ClientAcademicGroup, gid, "Client academic group")
    form = await request.form()
    before = snapshot(g)
    _apply_group(g, form)
    log_action(db, user, "update", MODULE, entity=g, description=f"Client academic group {g.name} updated", before=before, after=snapshot(g), request=request)
    db.commit()
    return redirect(f"{BASE}/client-groups", f"Group {g.name} updated.")


@router.post("/client-groups/{gid}/toggle", include_in_schema=False)
async def client_group_toggle(gid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    g = _get(db, ClientAcademicGroup, gid, "Client academic group")
    return _toggle_status(db, user, request, g, f"Group {g.name}", f"{BASE}/client-groups")


# =============================================================================== 10. MS Teams Users
@router.get("/teams-users", include_in_schema=False)
def teams_users_page(request: Request, tab: str = "staff", q: str = "", status: str = "",
                     db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    tab = "client" if tab in ("client", "clients") else "staff"
    query = db.query(TeamsUser).filter(TeamsUser.person_type == tab)
    if status:
        query = query.filter(TeamsUser.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(TeamsUser.teams_email.ilike(like), TeamsUser.display_name.ilike(like)))
    rows = query.order_by(TeamsUser.display_name, TeamsUser.teams_email).all()
    stats = _status_counts(db, TeamsUser)
    by_type = dict(db.query(TeamsUser.person_type, func.count(TeamsUser.id)).group_by(TeamsUser.person_type).all())
    stats.update({"staff": by_type.get("staff", 0), "client": by_type.get("client", 0)})
    return render(request, "academic_config/teams_users.html", {
        "user": user, "rows": rows, "stats": stats, "tab": tab, "q": q, "status": status,
        "tabs": [("staff", f"Staff ({stats['staff']})", f"{BASE}/teams-users?tab=staff"),
                 ("client", f"Clients ({stats['client']})", f"{BASE}/teams-users?tab=client")],
        "employee_options": [(e.id, f"{e.employee_code} — {e.full_name}") for e in
                             db.query(Employee).filter(Employee.status == "active").order_by(Employee.full_name)],
        "client_options": [(c.id, f"{c.client_code} — {c.full_name}") for c in
                           db.query(Client).filter(Client.status.in_(["active", "trial"])).order_by(Client.client_code).limit(500)],
        "statuses": STATUSES, **_perms(user)})


def _apply_teams(t: TeamsUser, form, db: Session) -> Optional[str]:
    pt = form.get("person_type") or t.person_type or "staff"
    t.person_type = "client" if pt == "client" else "staff"
    if t.person_type == "staff":
        t.employee_id = parse_int(form.get("employee_id"), t.employee_id)
        t.client_id = None
        if not t.employee_id:
            return "Pick the employee."
    else:
        t.client_id = parse_int(form.get("client_id"), t.client_id)
        t.employee_id = None
        if not t.client_id:
            return "Pick the client."
    t.teams_email = (form.get("teams_email") or t.teams_email or "").strip().lower()
    if not t.teams_email or "@" not in t.teams_email:
        return "A valid Teams email is required."
    t.display_name = (form.get("display_name") or "").strip() or None
    if not t.display_name:
        person = db.get(Employee, t.employee_id) if t.employee_id else db.get(Client, t.client_id)
        t.display_name = person.full_name if person else None
    t.status = _status_field(form, t.status or "active")
    return None


@router.post("/teams-users/new", include_in_schema=False)
async def teams_user_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.add"))):
    form = await request.form()
    t = TeamsUser(person_type="staff", teams_email="", status="active")
    err = _apply_teams(t, form, db)
    back = f"{BASE}/teams-users?tab={t.person_type}"
    if err:
        return redirect(back, err, "error")
    if db.query(TeamsUser).filter(TeamsUser.teams_email == t.teams_email).first():
        return redirect(back, f"{t.teams_email} is already mapped.", "error")
    db.add(t)
    db.flush()
    log_action(db, user, "create", MODULE, entity=t, description=f"Teams user {t.teams_email} ({t.person_type}) created", after=snapshot(t), request=request)
    db.commit()
    return redirect(back, f"Teams user {t.teams_email} created.")


@router.post("/teams-users/{tid}/edit", include_in_schema=False)
async def teams_user_edit(tid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    t = _get(db, TeamsUser, tid, "Teams user")
    form = await request.form()
    before = snapshot(t)
    err = _apply_teams(t, form, db)
    back = f"{BASE}/teams-users?tab={t.person_type}"
    if err:
        db.rollback()
        return redirect(back, err, "error")
    log_action(db, user, "update", MODULE, entity=t, description=f"Teams user {t.teams_email} updated", before=before, after=snapshot(t), request=request)
    db.commit()
    return redirect(back, f"Teams user {t.teams_email} updated.")


@router.post("/teams-users/{tid}/toggle", include_in_schema=False)
async def teams_user_toggle(tid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    t = _get(db, TeamsUser, tid, "Teams user")
    return _toggle_status(db, user, request, t, f"Teams user {t.teams_email}", f"{BASE}/teams-users?tab={t.person_type}")


# =============================================================================== 11. Question Bank
def _assessment_options(db: Session) -> list[tuple[int, str]]:
    return [(a.id, a.title) for a in db.query(AssessmentDefinition).order_by(AssessmentDefinition.title)]


@router.get("/question-bank", include_in_schema=False)
def question_bank_page(request: Request, book_id: Optional[int] = None, assessment_id: Optional[int] = None,
                       question_type: str = "", status: str = "", q: str = "",
                       db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    query = db.query(QuestionBankItem)
    if book_id:
        query = query.filter(QuestionBankItem.book_id == book_id)
    if assessment_id:
        query = query.filter(QuestionBankItem.assessment_id == assessment_id)
    if question_type:
        query = query.filter(QuestionBankItem.question_type == question_type)
    if status:
        query = query.filter(QuestionBankItem.status == status)
    if q:
        query = query.filter(QuestionBankItem.question.ilike(f"%{q}%"))
    rows = query.order_by(QuestionBankItem.assessment_id, QuestionBankItem.id).all()
    stats = _status_counts(db, QuestionBankItem)
    by_type = dict(db.query(QuestionBankItem.question_type, func.count(QuestionBankItem.id)).group_by(QuestionBankItem.question_type).all())
    stats.update({k: by_type.get(k, 0) for k, _ in QUESTION_TYPES})
    return render(request, "academic_config/question_bank.html", {
        "user": user, "rows": rows, "stats": stats, "book_id": book_id, "assessment_id": assessment_id, "question_type": question_type,
        "status": status, "q": q, "book_options": _book_options(db), "assessment_options": _assessment_options(db),
        "question_types": QUESTION_TYPES, "statuses": STATUSES, **_perms(user)})


def _apply_question(item: QuestionBankItem, form, db: Session) -> Optional[str]:
    item.question = (form.get("question") or item.question or "").strip()
    if not item.question:
        return "Question text is required."
    item.answer = (form.get("answer") or "").strip() or None
    qt = form.get("question_type")
    item.question_type = qt if qt in dict(QUESTION_TYPES) else (item.question_type or "oral")
    item.marks = parse_float(form.get("marks"), float(item.marks or 1)) or 1
    item.assessment_id = parse_int(form.get("assessment_id"))
    item.book_id = parse_int(form.get("book_id"))
    if not item.book_id and item.assessment_id:
        a = db.get(AssessmentDefinition, item.assessment_id)
        item.book_id = a.book_id if a else None
    item.status = _status_field(form, item.status or "active")
    return None


def _question_back(form, item: QuestionBankItem) -> str:
    nxt = form.get("next") or ""
    if nxt.startswith(BASE):
        return nxt
    return f"{BASE}/question-bank" + (f"?assessment_id={item.assessment_id}" if item.assessment_id else "")


@router.post("/question-bank/new", include_in_schema=False)
async def question_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.add"))):
    form = await request.form()
    item = QuestionBankItem(question="", question_type="oral", marks=1, status="active")
    err = _apply_question(item, form, db)
    if err:
        return redirect(f"{BASE}/question-bank", err, "error")
    db.add(item)
    db.flush()
    log_action(db, user, "create", MODULE, entity=item, description=f"Question #{item.id} created", after=snapshot(item), request=request)
    db.commit()
    return redirect(_question_back(form, item), "Question added to the bank.")


@router.post("/question-bank/{qid}/edit", include_in_schema=False)
async def question_edit(qid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    item = _get(db, QuestionBankItem, qid, "Question")
    form = await request.form()
    before = snapshot(item)
    err = _apply_question(item, form, db)
    if err:
        db.rollback()
        return redirect(f"{BASE}/question-bank", err, "error")
    log_action(db, user, "update", MODULE, entity=item, description=f"Question #{item.id} updated", before=before, after=snapshot(item), request=request)
    db.commit()
    return redirect(_question_back(form, item), "Question updated.")


@router.post("/question-bank/{qid}/toggle", include_in_schema=False)
async def question_toggle(qid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    item = _get(db, QuestionBankItem, qid, "Question")
    form = await request.form()
    return _toggle_status(db, user, request, item, f"Question #{item.id}", _question_back(form, item))


# =============================================================================== 12. Define Assessment
@router.get("/assessments", include_in_schema=False)
def assessments_page(request: Request, book_id: Optional[int] = None, status: str = "", q: str = "",
                     db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    query = db.query(AssessmentDefinition)
    if book_id:
        query = query.filter(AssessmentDefinition.book_id == book_id)
    if status:
        query = query.filter(AssessmentDefinition.status == status)
    if q:
        query = query.filter(AssessmentDefinition.title.ilike(f"%{q}%"))
    rows = query.order_by(AssessmentDefinition.created_at.desc(), AssessmentDefinition.id.desc()).all()
    stats = _status_counts(db, AssessmentDefinition)
    stats["questions"] = db.query(func.count(QuestionBankItem.id)).filter(QuestionBankItem.assessment_id.isnot(None)).scalar() or 0
    qcounts = dict(db.query(QuestionBankItem.assessment_id, func.count(QuestionBankItem.id)).group_by(QuestionBankItem.assessment_id).all())
    return render(request, "academic_config/assessments.html", {
        "user": user, "rows": rows, "stats": stats, "book_id": book_id, "status": status, "q": q, "qcounts": qcounts,
        "book_options": _book_options(db), "statuses": STATUSES, **_perms(user)})


def _apply_assessment(a: AssessmentDefinition, form) -> Optional[str]:
    a.title = (form.get("title") or a.title or "").strip()
    if not a.title:
        return "Assessment title is required."
    a.book_id = parse_int(form.get("book_id"))
    a.total_marks = parse_float(form.get("total_marks"), float(a.total_marks or 100)) or 100
    a.passing_marks = parse_float(form.get("passing_marks"), float(a.passing_marks or 0))
    if a.passing_marks > a.total_marks:
        return "Passing marks cannot exceed total marks."
    a.status = _status_field(form, a.status or "active")
    return None


@router.post("/assessments/new", include_in_schema=False)
async def assessment_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.add"))):
    form = await request.form()
    a = AssessmentDefinition(title="", passing_marks=0, total_marks=100, status="active")
    err = _apply_assessment(a, form)
    if err:
        return redirect(f"{BASE}/assessments", err, "error")
    db.add(a)
    db.flush()
    log_action(db, user, "create", MODULE, entity=a, description=f"Assessment {a.title} created", after=snapshot(a), request=request)
    db.commit()
    return redirect(f"{BASE}/assessments/{a.id}", f"Assessment {a.title} created.")


@router.get("/assessments/{aid}", include_in_schema=False)
def assessment_detail(aid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    a = _get(db, AssessmentDefinition, aid, "Assessment")
    questions = db.query(QuestionBankItem).filter(QuestionBankItem.assessment_id == a.id).order_by(QuestionBankItem.id).all()
    marks = sum(float(q.marks or 0) for q in questions if q.status == "active")
    return render(request, "academic_config/assessment_detail.html", {
        "user": user, "a": a, "questions": questions, "marks_total": marks,
        "book_options": _book_options(db), "question_types": QUESTION_TYPES, "statuses": STATUSES, **_perms(user)})


@router.post("/assessments/{aid}/edit", include_in_schema=False)
async def assessment_edit(aid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    a = _get(db, AssessmentDefinition, aid, "Assessment")
    form = await request.form()
    before = snapshot(a)
    err = _apply_assessment(a, form)
    if err:
        db.rollback()
        return redirect(f"{BASE}/assessments/{a.id}", err, "error")
    log_action(db, user, "update", MODULE, entity=a, description=f"Assessment {a.title} updated", before=before, after=snapshot(a), request=request)
    db.commit()
    back = form.get("next") if (form.get("next") or "").startswith(BASE) else f"{BASE}/assessments/{a.id}"
    return redirect(back, f"Assessment {a.title} updated.")


@router.post("/assessments/{aid}/toggle", include_in_schema=False)
async def assessment_toggle(aid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.update"))):
    a = _get(db, AssessmentDefinition, aid, "Assessment")
    form = await request.form()
    back = form.get("next") if (form.get("next") or "").startswith(BASE) else f"{BASE}/assessments"
    return _toggle_status(db, user, request, a, f"Assessment {a.title}", back)
