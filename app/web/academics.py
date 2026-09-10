"""Academic module web routes: courses & packages, curriculum, lesson plans, evaluations,
monthly tests & result cards (Module 41), certificates, shared Arabic lesson view (Module 28), progress.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import csrf_protect, get_current_user, get_user_context, require, PermissionDenied, UserContext
from app.core.templating import render
from app.core.utils import paginate, parse_bool, parse_date, parse_float, parse_int, redirect
from app.database import get_db
from app.models.academic import (Book, Certificate, Chapter, Course, CurriculumVersion, Division, DorSchedule,
                                 Evaluation, Lesson, LessonAnnotation, LessonPlan, MonthlyTest, Package,
                                 StudentProgress)
from app.models.core import AuditEvent, User
from app.models.people import Client, Student, Teacher
from app.services import academic as svc

router = APIRouter(prefix="/academics", dependencies=[Depends(csrf_protect)])

PLAN_STATUSES = ["planned", "delivered", "partially_delivered", "not_delivered"]
EVAL_TYPES = ["manual", "weekly", "monthly", "level_completion", "trial"]
PROGRESS_STATUSES = ["not_started", "in_progress", "completed", "revision"]
PROGRESS_TYPES = ["sabaq", "sabqi", "dor"]
ANNOTATION_TYPES = ["highlight", "mistake", "tajweed", "note"]
COUNTRIES = ["United Kingdom", "United States", "Canada", "Australia", "Pakistan", "United Arab Emirates",
             "Saudi Arabia", "Germany", "France", "South Africa"]
CURRENCIES = ["GBP", "USD", "EUR", "CAD", "AUD", "PKR"]


# =============================================================================== helpers
def _scope_ids(user: User, ctx: UserContext) -> Optional[list[int]]:
    """Student ids the user may see. ``None`` means unrestricted."""
    if user.is_superuser or rbac.is_management(user):
        return None
    if ctx.teacher or ctx.client or ctx.student:
        return list(ctx.student_ids)
    return None


def _check_student(student: Student, scope: Optional[list[int]]) -> None:
    if scope is not None and student.id not in scope:
        raise PermissionDenied("students.view (out of scope)")


def _get(db: Session, model, id: int, label: str):
    obj = db.get(model, id)
    if not obj:
        raise HTTPException(404, f"{label} not found")
    return obj


def _student_options(db: Session, scope: Optional[list[int]]) -> list[tuple[int, str]]:
    q = db.query(Student).filter(Student.status.in_(["active", "trial", "frozen"]))
    if scope is not None:
        q = q.filter(Student.id.in_(scope or [0]))
    return [(s.id, f"{s.student_code} — {s.full_name}") for s in q.order_by(Student.full_name).limit(500)]


def _teacher_options(db: Session) -> list[tuple[int, str]]:
    return [(t.id, t.full_name) for t in db.query(Teacher).order_by(Teacher.full_name)]


def _course_options(db: Session) -> list[tuple[int, str]]:
    return [(c.id, f"{c.code} — {c.name}") for c in db.query(Course).order_by(Course.order, Course.id)]


def _lesson_options(db: Session, course_id: Optional[int]) -> list[tuple[int, str]]:
    return [(l.id, svc.lesson_path(l)) for l in svc.course_lessons(db, course_id)]


# =============================================================================== courses, divisions & packages
@router.get("", include_in_schema=False)
def academics_home(user: User = Depends(get_current_user)):
    return redirect("/academics/courses")


@router.get("/courses", include_in_schema=False)
def courses_page(request: Request, q: str = "", status: str = "", db: Session = Depends(get_db),
                 user: User = Depends(require("courses.view"))):
    query = db.query(Course)
    if q:
        query = query.filter(or_(Course.name.ilike(f"%{q}%"), Course.code.ilike(f"%{q}%")))
    if status == "active":
        query = query.filter(Course.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(Course.is_active.is_(False))
    courses = query.order_by(Course.order, Course.id).all()
    student_counts = dict(db.query(Student.course_id, func.count(Student.id))
                          .filter(Student.status.in_(["active", "trial"])).group_by(Student.course_id).all())
    lesson_counts = dict(db.query(Book.course_id, func.count(Lesson.id))
                         .join(Chapter, Chapter.book_id == Book.id).join(Lesson, Lesson.chapter_id == Chapter.id)
                         .group_by(Book.course_id).all())
    packages = db.query(Package).order_by(Package.course_id, Package.country, Package.price).all()
    stats = {"courses": db.query(func.count(Course.id)).scalar() or 0,
             "active": db.query(func.count(Course.id)).filter(Course.is_active.is_(True)).scalar() or 0,
             "divisions": db.query(func.count(Division.id)).scalar() or 0,
             "packages": db.query(func.count(Package.id)).filter(Package.is_active.is_(True)).scalar() or 0,
             "lessons": db.query(func.count(Lesson.id)).scalar() or 0}
    return render(request, "academics/courses.html", {
        "user": user, "courses": courses, "packages": packages, "stats": stats, "q": q, "status": status,
        "student_counts": student_counts, "lesson_counts": lesson_counts, "course_options": _course_options(db),
        "countries": COUNTRIES, "currencies": CURRENCIES,
        "can_edit": rbac.has_permission(user, "courses.update"), "can_add": rbac.has_permission(user, "courses.add"),
        "can_price": rbac.has_permission(user, "packages.update") or rbac.has_permission(user, "courses.update")})


@router.post("/courses/new", include_in_schema=False)
async def course_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("courses.add"))):
    form = await request.form()
    code = (form.get("code") or "").strip().upper()
    name = (form.get("name") or "").strip()
    if not code or not name:
        return redirect("/academics/courses", "Course code and name are required.", "error")
    if db.query(Course.id).filter(Course.code == code).first():
        return redirect("/academics/courses", f"A course with code {code} already exists.", "error")
    c = Course(code=code, name=name, arabic_name=form.get("arabic_name") or None, urdu_name=form.get("urdu_name") or None,
               description=form.get("description") or None,
               default_session_minutes=parse_int(form.get("default_session_minutes"), 30) or 30,
               completion_target_months=parse_int(form.get("completion_target_months")),
               order=parse_int(form.get("order"), 0) or 0, is_active=True)
    db.add(c)
    db.flush()
    db.add(CurriculumVersion(course_id=c.id, version="1.0", is_current=True, notes="Course created",
                             published_by_id=user.id, published_at=datetime.utcnow()))
    log_action(db, user, "create", "courses", entity=c, description=f"Course {c.code} — {c.name} created",
               after=snapshot(c), request=request)
    db.commit()
    return redirect(f"/academics/courses/{c.id}", f"Course {c.code} created.")


@router.get("/courses/{cid}", include_in_schema=False)
def course_detail(cid: int, request: Request, tab: str = "divisions", db: Session = Depends(get_db),
                  user: User = Depends(require("courses.view"))):
    c = _get(db, Course, cid, "Course")
    books = db.query(Book).filter(Book.course_id == c.id).order_by(Book.order, Book.id).all()
    lessons = svc.course_lessons(db, c.id)
    students = db.query(Student).filter(Student.course_id == c.id).order_by(Student.full_name).limit(200).all()
    versions = db.query(CurriculumVersion).filter(CurriculumVersion.course_id == c.id).order_by(CurriculumVersion.id.desc()).all()
    events = (db.query(AuditEvent).filter(AuditEvent.entity_type == "Course", AuditEvent.entity_id == c.id)
              .order_by(AuditEvent.created_at.desc()).limit(50).all())
    return render(request, "academics/course_detail.html", {
        "user": user, "c": c, "tab": tab, "books": books, "lessons": lessons, "students": students, "versions": versions,
        "events": events, "packages": db.query(Package).filter(Package.course_id == c.id).order_by(Package.country).all(),
        "countries": COUNTRIES, "currencies": CURRENCIES,
        "tabs": [(k, l, f"/academics/courses/{c.id}?tab={k}") for k, l in
                 [("divisions", "Divisions"), ("packages", "Packages & Pricing"), ("curriculum", "Curriculum"),
                  ("students", "Students"), ("versions", "Versions"), ("audit", "Audit")]],
        "can_edit": rbac.has_permission(user, "courses.update")})


@router.post("/courses/{cid}/edit", include_in_schema=False)
async def course_edit(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("courses.update"))):
    c = _get(db, Course, cid, "Course")
    form = await request.form()
    before = snapshot(c)
    c.name = (form.get("name") or c.name).strip()
    c.arabic_name = form.get("arabic_name") or None
    c.urdu_name = form.get("urdu_name") or None
    c.description = form.get("description") or None
    c.default_session_minutes = parse_int(form.get("default_session_minutes"), c.default_session_minutes) or 30
    c.completion_target_months = parse_int(form.get("completion_target_months"), c.completion_target_months)
    c.order = parse_int(form.get("order"), c.order) or 0
    log_action(db, user, "update", "courses", entity=c, description=f"Course {c.code} updated", before=before,
               after=snapshot(c), request=request)
    db.commit()
    return redirect(f"/academics/courses/{c.id}", "Course updated.")


@router.post("/courses/{cid}/toggle", include_in_schema=False)
async def course_toggle(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("courses.update"))):
    c = _get(db, Course, cid, "Course")
    form = await request.form()
    before = snapshot(c)
    c.is_active = not c.is_active
    log_action(db, user, "update", "courses", entity=c, severity="warning" if not c.is_active else "info",
               description=f"Course {c.code} {'activated' if c.is_active else 'deactivated'}",
               rationale=form.get("reason") or form.get("rationale"), before=before, after=snapshot(c), request=request)
    db.commit()
    return redirect(f"/academics/courses/{c.id}", f"Course {'activated' if c.is_active else 'deactivated'}.")


@router.post("/courses/{cid}/divisions", include_in_schema=False)
async def division_add(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("courses.update"))):
    c = _get(db, Course, cid, "Course")
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return redirect(f"/academics/courses/{c.id}", "Division name is required.", "error")
    nxt = (db.query(func.max(Division.order)).filter(Division.course_id == c.id).scalar() or -1) + 1
    d = Division(course_id=c.id, name=name, description=form.get("description") or None, order=nxt,
                 expected_weeks=parse_int(form.get("expected_weeks")))
    db.add(d)
    db.flush()
    log_action(db, user, "create", "courses", entity=d, description=f"Division '{d.name}' added to {c.code}", request=request)
    db.commit()
    return redirect(f"/academics/courses/{c.id}", f"Division '{name}' added.")


@router.post("/courses/{cid}/divisions/{did}/edit", include_in_schema=False)
async def division_edit(cid: int, did: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("courses.update"))):
    d = db.query(Division).filter(Division.id == did, Division.course_id == cid).first()
    if not d:
        raise HTTPException(404, "Division not found")
    form = await request.form()
    before = snapshot(d)
    d.name = (form.get("name") or d.name).strip()
    d.description = form.get("description") or None
    d.expected_weeks = parse_int(form.get("expected_weeks"), d.expected_weeks)
    log_action(db, user, "update", "courses", entity=d, description=f"Division '{d.name}' updated", before=before,
               after=snapshot(d), request=request)
    db.commit()
    return redirect(f"/academics/courses/{cid}", "Division updated.")


@router.post("/courses/{cid}/divisions/{did}/move", include_in_schema=False)
async def division_move(cid: int, did: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("courses.update"))):
    form = await request.form()
    direction = form.get("direction") or "up"
    divisions = db.query(Division).filter(Division.course_id == cid).order_by(Division.order, Division.id).all()
    ids = [d.id for d in divisions]
    if did not in ids:
        raise HTTPException(404, "Division not found")
    i = ids.index(did)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(divisions):
        divisions[i], divisions[j] = divisions[j], divisions[i]
        for k, d in enumerate(divisions):
            d.order = k
        log_action(db, user, "update", "courses", entity=divisions[j], description=f"Division order changed ({direction})", request=request)
        db.commit()
        return redirect(f"/academics/courses/{cid}", "Division order updated.")
    return redirect(f"/academics/courses/{cid}", "Division is already at the end.", "info")


@router.post("/courses/{cid}/divisions/{did}/delete", include_in_schema=False)
async def division_delete(cid: int, did: int, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("courses.delete"))):
    d = db.query(Division).filter(Division.id == did, Division.course_id == cid).first()
    if not d:
        raise HTTPException(404, "Division not found")
    if db.query(Student.id).filter(Student.division_id == d.id).first():
        return redirect(f"/academics/courses/{cid}", "Students are still enrolled in this division.", "error")
    name = d.name
    log_action(db, user, "delete", "courses", entity=d, description=f"Division '{name}' deleted", before=snapshot(d), request=request)
    db.delete(d)
    db.commit()
    return redirect(f"/academics/courses/{cid}", f"Division '{name}' deleted.")


def _read_package(form) -> dict:
    return {"name": (form.get("name") or "").strip(), "course_id": parse_int(form.get("course_id")),
            "sessions_per_week": parse_int(form.get("sessions_per_week"), 3) or 3,
            "session_minutes": parse_int(form.get("session_minutes"), 30) or 30,
            "price": parse_float(form.get("price")), "currency": (form.get("currency") or "GBP").upper()[:3],
            "country": form.get("country") or None, "billing_cycle": form.get("billing_cycle") or "monthly",
            "description": form.get("description") or None, "is_trial": parse_bool(form.get("is_trial"))}


@router.post("/packages/new", include_in_schema=False)
async def package_create(request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("courses.update", "packages.view", any_of=True))):
    form = await request.form()
    data = _read_package(form)
    if not data["name"]:
        return redirect("/academics/courses?tab=packages", "Package name is required.", "error")
    p = Package(**data, is_active=True)
    db.add(p)
    db.flush()
    log_action(db, user, "create", "packages", entity=p, after=snapshot(p), request=request,
               description=f"Package '{p.name}' created ({p.currency} {p.price} / {p.country or 'global'})")
    db.commit()
    return redirect("/academics/courses#packages", f"Package '{p.name}' created.")


@router.post("/packages/{pid}/edit", include_in_schema=False)
async def package_edit(pid: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("courses.update", "packages.view", any_of=True))):
    p = _get(db, Package, pid, "Package")
    form = await request.form()
    before = snapshot(p)
    for k, v in _read_package(form).items():
        if k == "name" and not v:
            continue
        setattr(p, k, v)
    log_action(db, user, "update", "packages", entity=p, before=before, after=snapshot(p), request=request,
               description=f"Package '{p.name}' updated to {p.currency} {p.price} ({p.country or 'global'})",
               rationale=form.get("reason") or form.get("rationale"), consequential=True)
    db.commit()
    return redirect("/academics/courses#packages", "Package updated.")


@router.post("/packages/{pid}/toggle", include_in_schema=False)
async def package_toggle(pid: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("courses.update", "packages.view", any_of=True))):
    p = _get(db, Package, pid, "Package")
    p.is_active = not p.is_active
    log_action(db, user, "update", "packages", entity=p, request=request,
               description=f"Package '{p.name}' {'activated' if p.is_active else 'deactivated'}")
    db.commit()
    return redirect("/academics/courses#packages", f"Package {'activated' if p.is_active else 'deactivated'}.")


# =============================================================================== curriculum
@router.get("/curriculum", include_in_schema=False)
def curriculum_page(request: Request, course_id: int = 0, q: str = "", db: Session = Depends(get_db),
                    user: User = Depends(require("curriculum.view"))):
    courses = db.query(Course).order_by(Course.order, Course.id).all()
    if not course_id and courses:
        course_id = courses[0].id
    course = db.get(Course, course_id) if course_id else None
    books = db.query(Book).filter(Book.course_id == course_id).order_by(Book.order, Book.id).all() if course else []
    tree = []
    ql = (q or "").strip().lower()
    total_lessons = total_minutes = 0
    for b in books:
        chapters = []
        for ch in b.chapters:
            lessons = [l for l in ch.lessons if not ql or ql in (l.title or "").lower() or ql in (l.arabic_text or "").lower()]
            total_lessons += len(lessons)
            total_minutes += sum(l.expected_minutes or 0 for l in lessons)
            if lessons or not ql:
                chapters.append({"chapter": ch, "lessons": lessons})
        if chapters:
            tree.append({"book": b, "chapters": chapters,
                         "lessons": sum(len(c["lessons"]) for c in chapters)})
    versions = db.query(CurriculumVersion).filter(CurriculumVersion.course_id == course_id).order_by(CurriculumVersion.id.desc()).all() if course else []
    current = next((v for v in versions if v.is_current), None)
    return render(request, "academics/curriculum.html", {
        "user": user, "courses": courses, "course": course, "course_id": course_id, "tree": tree, "q": q,
        "versions": versions, "current_version": current,
        "stats": {"books": len(books), "chapters": sum(len(t["chapters"]) for t in tree), "lessons": total_lessons,
                  "hours": round(total_minutes / 60, 1),
                  "students": db.query(func.count(Student.id)).filter(Student.course_id == course_id).scalar() or 0},
        "can_edit": rbac.has_permission(user, "curriculum.update"),
        "can_add": rbac.has_permission(user, "curriculum.add"),
        "can_delete": rbac.has_permission(user, "curriculum.delete")})


@router.get("/curriculum/compliance", include_in_schema=False)
def curriculum_compliance(request: Request, course_id: int = 0, teacher_id: int = 0, db: Session = Depends(get_db),
                          user: User = Depends(require("curriculum.view")), ctx: UserContext = Depends(get_user_context)):
    scope = _scope_ids(user, ctx)
    rows = svc.syllabus_compliance(db, course_id or None, teacher_id or None, scope)
    summary = {"n": len(rows),
               "on_track": sum(1 for r in rows if r["status"] == "on_track"),
               "behind": sum(1 for r in rows if r["status"] == "behind"),
               "at_risk": sum(1 for r in rows if r["status"] == "at_risk"),
               "avg_compliance": round(sum(r["compliance"] for r in rows) / len(rows), 1) if rows else 0.0,
               "avg_delivery": round(sum(r["delivery_rate"] for r in rows) / len(rows), 1) if rows else 0.0}
    by_course: dict[str, dict] = {}
    for r in rows:
        d = by_course.setdefault(r["course"].name if r["course"] else "—", {"label": r["course"].name if r["course"] else "—", "n": 0, "sum": 0.0})
        d["n"] += 1
        d["sum"] += r["compliance"]
    chart = {"labels": list(by_course), "data": [round(d["sum"] / d["n"], 1) for d in by_course.values()]}
    rows.sort(key=lambda r: r["compliance"])
    return render(request, "academics/compliance.html", {
        "user": user, "rows": rows, "summary": summary, "chart": chart, "course_id": course_id, "teacher_id": teacher_id,
        "course_options": _course_options(db), "teacher_options": _teacher_options(db)})


@router.post("/curriculum/books", include_in_schema=False)
async def book_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("curriculum.add"))):
    form = await request.form()
    course_id = parse_int(form.get("course_id"))
    title = (form.get("title") or "").strip()
    if not course_id or not title:
        return redirect("/academics/curriculum", "Course and book title are required.", "error")
    nxt = (db.query(func.max(Book.order)).filter(Book.course_id == course_id).scalar() or -1) + 1
    b = Book(course_id=course_id, title=title, arabic_title=form.get("arabic_title") or None,
             description=form.get("description") or None, order=parse_int(form.get("order"), nxt))
    db.add(b)
    db.flush()
    log_action(db, user, "create", "curriculum", entity=b, description=f"Book '{b.title}' added", request=request)
    db.commit()
    return redirect(f"/academics/curriculum?course_id={course_id}", f"Book '{title}' added.")


@router.post("/curriculum/books/{bid}/edit", include_in_schema=False)
async def book_edit(bid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("curriculum.update"))):
    b = _get(db, Book, bid, "Book")
    form = await request.form()
    before = snapshot(b)
    b.title = (form.get("title") or b.title).strip()
    b.arabic_title = form.get("arabic_title") or None
    b.description = form.get("description") or None
    b.order = parse_int(form.get("order"), b.order) or 0
    log_action(db, user, "update", "curriculum", entity=b, description=f"Book '{b.title}' updated", before=before,
               after=snapshot(b), request=request)
    db.commit()
    return redirect(f"/academics/curriculum?course_id={b.course_id}", "Book updated.")


@router.post("/curriculum/books/{bid}/delete", include_in_schema=False)
async def book_delete(bid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("curriculum.delete"))):
    b = _get(db, Book, bid, "Book")
    course_id, title = b.course_id, b.title
    log_action(db, user, "delete", "curriculum", entity=b, description=f"Book '{title}' deleted with its chapters and lessons",
               before=snapshot(b), severity="warning", request=request)
    db.delete(b)
    db.commit()
    return redirect(f"/academics/curriculum?course_id={course_id}", f"Book '{title}' deleted.")


@router.post("/curriculum/chapters", include_in_schema=False)
async def chapter_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("curriculum.add"))):
    form = await request.form()
    book = _get(db, Book, parse_int(form.get("book_id")) or 0, "Book")
    title = (form.get("title") or "").strip()
    if not title:
        return redirect(f"/academics/curriculum?course_id={book.course_id}", "Chapter title is required.", "error")
    nxt = (db.query(func.max(Chapter.order)).filter(Chapter.book_id == book.id).scalar() or -1) + 1
    ch = Chapter(book_id=book.id, title=title, arabic_title=form.get("arabic_title") or None, order=parse_int(form.get("order"), nxt))
    db.add(ch)
    db.flush()
    log_action(db, user, "create", "curriculum", entity=ch, description=f"Chapter '{ch.title}' added to '{book.title}'", request=request)
    db.commit()
    return redirect(f"/academics/curriculum?course_id={book.course_id}", f"Chapter '{title}' added.")


@router.post("/curriculum/chapters/{chid}/edit", include_in_schema=False)
async def chapter_edit(chid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("curriculum.update"))):
    ch = _get(db, Chapter, chid, "Chapter")
    form = await request.form()
    before = snapshot(ch)
    ch.title = (form.get("title") or ch.title).strip()
    ch.arabic_title = form.get("arabic_title") or None
    ch.order = parse_int(form.get("order"), ch.order) or 0
    log_action(db, user, "update", "curriculum", entity=ch, description=f"Chapter '{ch.title}' updated", before=before,
               after=snapshot(ch), request=request)
    db.commit()
    return redirect(f"/academics/curriculum?course_id={ch.book.course_id}", "Chapter updated.")


@router.post("/curriculum/chapters/{chid}/delete", include_in_schema=False)
async def chapter_delete(chid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("curriculum.delete"))):
    ch = _get(db, Chapter, chid, "Chapter")
    course_id, title = ch.book.course_id, ch.title
    log_action(db, user, "delete", "curriculum", entity=ch, description=f"Chapter '{title}' deleted with its lessons",
               before=snapshot(ch), severity="warning", request=request)
    db.delete(ch)
    db.commit()
    return redirect(f"/academics/curriculum?course_id={course_id}", f"Chapter '{title}' deleted.")


def _read_lesson(form) -> dict:
    return {"title": (form.get("title") or "").strip(), "arabic_text": form.get("arabic_text") or None,
            "translation": form.get("translation") or None, "objectives": form.get("objectives") or None,
            "tajweed_notes": form.get("tajweed_notes") or None,
            "expected_minutes": parse_int(form.get("expected_minutes"), 30) or 30,
            "surah_number": parse_int(form.get("surah_number")), "ayah_from": parse_int(form.get("ayah_from")),
            "ayah_to": parse_int(form.get("ayah_to"))}


@router.get("/curriculum/lessons/{lid}", include_in_schema=False)
def lesson_detail(lid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("curriculum.view"))):
    les = _get(db, Lesson, lid, "Lesson")
    learners = (db.query(StudentProgress).filter(StudentProgress.lesson_id == les.id)
                .order_by(StudentProgress.updated_at.desc()).limit(30).all())
    counts = dict(db.query(StudentProgress.status, func.count(StudentProgress.id))
                  .filter(StudentProgress.lesson_id == les.id).group_by(StudentProgress.status).all())
    return render(request, "academics/lesson_detail.html", {
        "user": user, "les": les, "path": svc.lesson_path(les), "lines": svc.lesson_words(les), "legend": svc.TAJWEED_LEGEND,
        "learners": learners, "counts": counts, "course": les.chapter.book.course if les.chapter and les.chapter.book else None,
        "prev": svc.prev_lesson_before(db, les), "next": svc.next_lesson_after(db, les),
        "can_edit": rbac.has_permission(user, "curriculum.update"),
        "can_delete": rbac.has_permission(user, "curriculum.delete")})


@router.post("/curriculum/lessons", include_in_schema=False)
async def lesson_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("curriculum.add"))):
    form = await request.form()
    ch = _get(db, Chapter, parse_int(form.get("chapter_id")) or 0, "Chapter")
    data = _read_lesson(form)
    if not data["title"]:
        return redirect(f"/academics/curriculum?course_id={ch.book.course_id}", "Lesson title is required.", "error")
    nxt = (db.query(func.max(Lesson.order)).filter(Lesson.chapter_id == ch.id).scalar() or -1) + 1
    les = Lesson(chapter_id=ch.id, order=parse_int(form.get("order"), nxt), **data)
    db.add(les)
    db.flush()
    log_action(db, user, "create", "curriculum", entity=les, description=f"Lesson '{les.title}' added to '{ch.title}'", request=request)
    db.commit()
    return redirect(f"/academics/curriculum/lessons/{les.id}", f"Lesson '{les.title}' created.")


@router.post("/curriculum/lessons/{lid}/edit", include_in_schema=False)
async def lesson_edit(lid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("curriculum.update"))):
    les = _get(db, Lesson, lid, "Lesson")
    form = await request.form()
    before = snapshot(les)
    for k, v in _read_lesson(form).items():
        if k == "title" and not v:
            continue
        setattr(les, k, v)
    les.order = parse_int(form.get("order"), les.order) or 0
    log_action(db, user, "update", "curriculum", entity=les, description=f"Lesson '{les.title}' updated", before=before,
               after=snapshot(les), request=request)
    db.commit()
    return redirect(f"/academics/curriculum/lessons/{les.id}", "Lesson updated.")


@router.post("/curriculum/lessons/{lid}/delete", include_in_schema=False)
async def lesson_delete(lid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("curriculum.delete"))):
    les = _get(db, Lesson, lid, "Lesson")
    course_id = les.chapter.book.course_id
    title = les.title
    db.query(StudentProgress).filter(StudentProgress.lesson_id == les.id).delete(synchronize_session=False)
    log_action(db, user, "delete", "curriculum", entity=les, description=f"Lesson '{title}' deleted", before=snapshot(les),
               severity="warning", request=request)
    db.delete(les)
    db.commit()
    return redirect(f"/academics/curriculum?course_id={course_id}", f"Lesson '{title}' deleted.")


@router.post("/curriculum/publish", include_in_schema=False)
async def curriculum_publish(request: Request, db: Session = Depends(get_db), user: User = Depends(require("curriculum.approve", "curriculum.update", any_of=True))):
    form = await request.form()
    course_id = parse_int(form.get("course_id"))
    version = (form.get("version") or "").strip()
    if not course_id or not version:
        return redirect("/academics/curriculum", "Course and version number are required.", "error")
    if db.query(CurriculumVersion.id).filter(CurriculumVersion.course_id == course_id, CurriculumVersion.version == version).first():
        return redirect(f"/academics/curriculum?course_id={course_id}", f"Version {version} already exists for this course.", "error")
    for v in db.query(CurriculumVersion).filter(CurriculumVersion.course_id == course_id):
        v.is_current = False
    cv = CurriculumVersion(course_id=course_id, version=version, is_current=True, notes=form.get("notes") or None,
                           published_by_id=user.id, published_at=datetime.utcnow())
    db.add(cv)
    db.flush()
    log_action(db, user, "approve", "curriculum", entity=cv, consequential=True, request=request,
               description=f"Curriculum version {version} published for course {course_id}",
               rationale=form.get("notes") or "Curriculum publish")
    db.commit()
    return redirect(f"/academics/curriculum?course_id={course_id}", f"Curriculum version {version} published.")


@router.post("/curriculum/versions/{vid}/rollback", include_in_schema=False)
async def curriculum_rollback(vid: int, request: Request, db: Session = Depends(get_db),
                              user: User = Depends(require("curriculum.approve", "curriculum.update", any_of=True))):
    cv = _get(db, CurriculumVersion, vid, "Curriculum version")
    form = await request.form()
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if not reason:
        return redirect(f"/academics/curriculum?course_id={cv.course_id}", "A reason is required to roll back a curriculum version.", "error")
    current = db.query(CurriculumVersion).filter(CurriculumVersion.course_id == cv.course_id, CurriculumVersion.is_current.is_(True)).first()
    for v in db.query(CurriculumVersion).filter(CurriculumVersion.course_id == cv.course_id):
        v.is_current = False
    cv.is_current = True
    log_action(db, user, "override", "curriculum", entity=cv, consequential=True, severity="warning", request=request,
               description=f"Curriculum rolled back to version {cv.version} (from {current.version if current else 'none'})",
               rationale=reason, before={"current": current.version if current else None}, after={"current": cv.version})
    db.commit()
    return redirect(f"/academics/curriculum?course_id={cv.course_id}", f"Curriculum rolled back to version {cv.version}.")


# =============================================================================== lesson plans
@router.get("/lesson-plans", include_in_schema=False)
def lesson_plans(request: Request, page: int = 1, student: str = "", teacher: str = "", status: str = "",
                 date_from: str = "", date_to: str = "", db: Session = Depends(get_db),
                 user: User = Depends(require("lesson_plans.view")), ctx: UserContext = Depends(get_user_context)):
    scope = _scope_ids(user, ctx)
    q = db.query(LessonPlan)
    if scope is not None:
        q = q.filter(LessonPlan.student_id.in_(scope or [0]))
    if student:
        q = q.filter(LessonPlan.student_id == int(student))
    if teacher:
        q = q.filter(LessonPlan.teacher_id == int(teacher))
    if status:
        q = q.filter(LessonPlan.status == status)
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        q = q.filter(LessonPlan.plan_date >= df)
    if dt:
        q = q.filter(LessonPlan.plan_date <= dt)
    pg = paginate(q.order_by(LessonPlan.plan_date.desc(), LessonPlan.id.desc()), page, 25)
    base_q = db.query(LessonPlan)
    if scope is not None:
        base_q = base_q.filter(LessonPlan.student_id.in_(scope or [0]))
    count_q = db.query(LessonPlan.status, func.count(LessonPlan.id))
    if scope is not None:
        count_q = count_q.filter(LessonPlan.student_id.in_(scope or [0]))
    counts = dict(count_q.group_by(LessonPlan.status).all())
    avg_var = base_q.filter(LessonPlan.variance_pct.isnot(None)).with_entities(func.avg(LessonPlan.variance_pct)).scalar()
    stats = {"total": sum(counts.values()), "planned": counts.get("planned", 0), "delivered": counts.get("delivered", 0),
             "partial": counts.get("partially_delivered", 0), "not_delivered": counts.get("not_delivered", 0),
             "avg_variance": round(avg_var or 0, 1),
             "unreviewed": base_q.filter(LessonPlan.reviewed_at.is_(None), LessonPlan.status != "planned").count()}
    base = f"/academics/lesson-plans?student={student}&teacher={teacher}&status={status}&date_from={date_from}&date_to={date_to}"
    return render(request, "academics/lesson_plans.html", {
        "user": user, "page": pg, "stats": stats, "statuses": PLAN_STATUSES, "student": student, "teacher": teacher,
        "status": status, "date_from": date_from, "date_to": date_to, "base_url": base,
        "student_options": _student_options(db, scope), "teacher_options": _teacher_options(db),
        "can_add": rbac.has_permission(user, "lesson_plans.add"),
        "can_review": rbac.has_permission(user, "lesson_plans.approve") or rbac.is_management(user)})


@router.get("/lesson-plans/variance", include_in_schema=False)
def plan_variance_report(request: Request, date_from: str = "", date_to: str = "", teacher: str = "",
                         db: Session = Depends(get_db), user: User = Depends(require("lesson_plans.view")),
                         ctx: UserContext = Depends(get_user_context)):
    scope = _scope_ids(user, ctx)
    dt = parse_date(date_to) or date.today()
    df = parse_date(date_from) or (dt - timedelta(days=60))
    rep = svc.variance_report(db, df, dt, int(teacher) if teacher else None, scope)
    chart = {"labels": [r["teacher"].full_name if r["teacher"] else "Unassigned" for r in rep["rows"]],
             "variance": [r["avg_variance"] for r in rep["rows"]],
             "delivery": [r["delivery_rate"] for r in rep["rows"]]}
    return render(request, "academics/plan_variance.html", {
        "user": user, "rep": rep, "chart": chart, "date_from": df.isoformat(), "date_to": dt.isoformat(),
        "teacher": teacher, "teacher_options": _teacher_options(db)})


@router.get("/lesson-plans/new", include_in_schema=False)
def lesson_plan_new(request: Request, student_id: int = 0, recommend: int = 0, db: Session = Depends(get_db),
                    user: User = Depends(require("lesson_plans.add")), ctx: UserContext = Depends(get_user_context)):
    scope = _scope_ids(user, ctx)
    student = db.get(Student, student_id) if student_id else None
    if student:
        _check_student(student, scope)
    ai_text = None
    lesson = svc.current_lesson_for(db, student) if student else None
    if student and recommend:
        ai_text = svc.recommend_lesson(db, student, None, user)
        db.commit()
    return render(request, "academics/lesson_plan_form.html", {
        "user": user, "mode": "new", "plan": None, "student": student, "lesson": lesson, "ai_text": ai_text,
        "student_options": _student_options(db, scope), "teacher_options": _teacher_options(db),
        "lesson_options": _lesson_options(db, student.course_id) if student else [],
        "today_iso": date.today().isoformat(),
        "progress": svc.student_progress_summary(db, student) if student else None})


@router.post("/lesson-plans/new", include_in_schema=False)
async def lesson_plan_create(request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("lesson_plans.add")), ctx: UserContext = Depends(get_user_context)):
    form = await request.form()
    scope = _scope_ids(user, ctx)
    student = db.get(Student, parse_int(form.get("student_id")) or 0)
    if not student:
        return redirect("/academics/lesson-plans/new", "Select a student.", "error")
    _check_student(student, scope)
    plan_date = parse_date(form.get("plan_date")) or date.today()
    planned = (form.get("planned_content") or "").strip()
    if not planned:
        return redirect(f"/academics/lesson-plans/new?student_id={student.id}", "Planned content is required.", "error")
    plan = LessonPlan(student_id=student.id, teacher_id=parse_int(form.get("teacher_id")) or student.teacher_id,
                      plan_date=plan_date, plan_type=form.get("plan_type") or "daily",
                      lesson_id=parse_int(form.get("lesson_id")) or (student.current_lesson_id or None),
                      planned_content=planned, sabaq=(form.get("sabaq") or None), sabqi=(form.get("sabqi") or None),
                      dor=(form.get("dor") or None), next_objectives=form.get("next_objectives") or None,
                      teacher_notes=form.get("teacher_notes") or None,
                      ai_recommendation=form.get("ai_recommendation") or None, status="planned")
    db.add(plan)
    db.flush()
    log_action(db, user, "create", "lesson_plans", entity=plan, request=request,
               description=f"{plan.plan_type.title()} lesson plan created for {student.full_name} on {plan_date}")
    db.commit()
    return redirect(f"/academics/lesson-plans/{plan.id}", "Lesson plan created.")


@router.get("/lesson-plans/{pid}", include_in_schema=False)
def lesson_plan_detail(pid: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("lesson_plans.view")), ctx: UserContext = Depends(get_user_context)):
    plan = _get(db, LessonPlan, pid, "Lesson plan")
    _check_student(plan.student, _scope_ids(user, ctx))
    history = (db.query(LessonPlan).filter(LessonPlan.student_id == plan.student_id, LessonPlan.id != plan.id)
               .order_by(LessonPlan.plan_date.desc()).limit(10).all())
    events = (db.query(AuditEvent).filter(AuditEvent.entity_type == "LessonPlan", AuditEvent.entity_id == plan.id)
              .order_by(AuditEvent.created_at.desc()).limit(30).all())
    return render(request, "academics/lesson_plan_detail.html", {
        "user": user, "plan": plan, "history": history, "events": events,
        "progress": svc.student_progress_summary(db, plan.student),
        "can_update": rbac.has_permission(user, "lesson_plans.update"),
        "can_review": rbac.has_permission(user, "lesson_plans.approve") or rbac.is_management(user)})


@router.post("/lesson-plans/{pid}/deliver", include_in_schema=False)
async def lesson_plan_deliver(pid: int, request: Request, db: Session = Depends(get_db),
                              user: User = Depends(require("lesson_plans.update")), ctx: UserContext = Depends(get_user_context)):
    plan = _get(db, LessonPlan, pid, "Lesson plan")
    _check_student(plan.student, _scope_ids(user, ctx))
    form = await request.form()
    delivered = (form.get("delivered_content") or "").strip()
    if not delivered:
        return redirect(f"/academics/lesson-plans/{plan.id}", "Describe what was actually delivered.", "error")
    override = form.get("variance_pct")
    svc.mark_plan_delivered(db, plan, delivered, form.get("teacher_notes"), user,
                            variance_pct=parse_float(override) if override not in (None, "") else None, request=request)
    db.commit()
    return redirect(f"/academics/lesson-plans/{plan.id}", f"Marked {plan.status.replace('_', ' ')} — variance {plan.variance_pct}%.")


@router.post("/lesson-plans/{pid}/review", include_in_schema=False)
async def lesson_plan_review(pid: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("lesson_plans.approve", "lesson_plans.update", any_of=True))):
    plan = _get(db, LessonPlan, pid, "Lesson plan")
    form = await request.form()
    comment = (form.get("review_comment") or "").strip()
    if not comment:
        return redirect(f"/academics/lesson-plans/{plan.id}", "A review comment is required.", "error")
    before = snapshot(plan)
    plan.reviewed_by_id = user.id
    plan.reviewed_at = datetime.utcnow()
    plan.review_comment = comment
    log_action(db, user, "approve", "lesson_plans", entity=plan, before=before, after=snapshot(plan), request=request,
               description=f"Lesson plan {plan.plan_date} reviewed for {plan.student.full_name}", rationale=comment)
    if plan.teacher and plan.teacher.user_id:
        from app.core.notify import notify
        notify(db, plan.teacher.user_id, "Lesson plan reviewed",
               f"{plan.student.full_name} — {plan.plan_date}: {comment}", event_type="lesson_plan",
               link=f"/academics/lesson-plans/{plan.id}")
    db.commit()
    return redirect(f"/academics/lesson-plans/{plan.id}", "Review saved and the teacher notified.")


@router.post("/lesson-plans/{pid}/recommend", include_in_schema=False)
async def lesson_plan_recommend(pid: int, request: Request, db: Session = Depends(get_db),
                                user: User = Depends(require("lesson_plans.update"))):
    plan = _get(db, LessonPlan, pid, "Lesson plan")
    text = svc.recommend_lesson(db, plan.student, plan, user)
    log_action(db, user, "execute", "lesson_plans", entity=plan, request=request,
               description=f"AI lesson recommendation generated for {plan.student.full_name}")
    db.commit()
    return redirect(f"/academics/lesson-plans/{plan.id}", "AI recommendation generated: " + text[:120])


@router.post("/lesson-plans/{pid}/delete", include_in_schema=False)
async def lesson_plan_delete(pid: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("lesson_plans.delete"))):
    plan = _get(db, LessonPlan, pid, "Lesson plan")
    log_action(db, user, "delete", "lesson_plans", entity=plan, before=snapshot(plan), request=request,
               description=f"Lesson plan {plan.plan_date} for {plan.student.full_name} deleted")
    db.delete(plan)
    db.commit()
    return redirect("/academics/lesson-plans", "Lesson plan deleted.")


# =============================================================================== evaluations
@router.get("/evaluations", include_in_schema=False)
def evaluations(request: Request, page: int = 1, student: str = "", teacher: str = "", type: str = "", result: str = "",
                db: Session = Depends(get_db), user: User = Depends(require("evaluations.view")),
                ctx: UserContext = Depends(get_user_context)):
    scope = _scope_ids(user, ctx)
    q = db.query(Evaluation)
    if scope is not None:
        q = q.filter(Evaluation.student_id.in_(scope or [0]))
    if student:
        q = q.filter(Evaluation.student_id == int(student))
    if teacher:
        q = q.filter(Evaluation.teacher_id == int(teacher))
    if type:
        q = q.filter(Evaluation.evaluation_type == type)
    if result:
        q = q.filter(Evaluation.result == result)
    pg = paginate(q.order_by(Evaluation.date.desc(), Evaluation.id.desc()), page, 25)
    scoped = db.query(Evaluation)
    if scope is not None:
        scoped = scoped.filter(Evaluation.student_id.in_(scope or [0]))
    total = scoped.count()
    stats = {"total": total, "passed": scoped.filter(Evaluation.result == "pass").count(),
             "failed": scoped.filter(Evaluation.result == "fail").count(),
             "pending": scoped.filter(Evaluation.result == "pending").count(),
             "avg": round(scoped.with_entities(func.avg(Evaluation.score)).scalar() or 0, 1)}
    base = f"/academics/evaluations?student={student}&teacher={teacher}&type={type}&result={result}"
    return render(request, "academics/evaluations.html", {
        "user": user, "page": pg, "stats": stats, "types": EVAL_TYPES, "results": ["pass", "fail", "pending"],
        "student": student, "teacher": teacher, "type": type, "result": result, "base_url": base,
        "criteria": svc.EVAL_CRITERIA, "student_options": _student_options(db, scope),
        "teacher_options": _teacher_options(db), "today_iso": date.today().isoformat(),
        "can_add": rbac.has_permission(user, "evaluations.add")})


@router.post("/evaluations/new", include_in_schema=False)
async def evaluation_create(request: Request, db: Session = Depends(get_db),
                            user: User = Depends(require("evaluations.add")), ctx: UserContext = Depends(get_user_context)):
    form = await request.form()
    student = db.get(Student, parse_int(form.get("student_id")) or 0)
    if not student:
        return redirect("/academics/evaluations", "Select a student.", "error")
    _check_student(student, _scope_ids(user, ctx))
    criteria = {}
    for key in svc.EVAL_CRITERIA:
        v = parse_float(form.get(f"criteria_{key}"), 0.0)
        criteria[key] = max(0.0, min(10.0, v))
    score = parse_float(form.get("score")) if form.get("score") else svc.evaluation_score(criteria)
    max_score = parse_float(form.get("max_score"), 100.0) or 100.0
    result = form.get("result") or ("pass" if score >= max_score * 0.55 else "fail")
    ev = Evaluation(student_id=student.id, teacher_id=parse_int(form.get("teacher_id")) or student.teacher_id,
                    evaluation_type=form.get("evaluation_type") or "manual",
                    date=parse_date(form.get("date")) or date.today(), score=score, max_score=max_score,
                    result=result, criteria=criteria, teacher_comment=form.get("teacher_comment") or None,
                    academic_comment=form.get("academic_comment") or None,
                    reviewed_by_id=user.id if form.get("academic_comment") else None)
    db.add(ev)
    db.flush()
    log_action(db, user, "create", "evaluations", entity=ev, request=request, after=snapshot(ev),
               description=f"{ev.evaluation_type.replace('_', ' ').title()} evaluation for {student.full_name}: {score}/{max_score} ({result})")
    if ev.evaluation_type == "level_completion" and result == "pass" and rbac.has_permission(user, "certificates.add"):
        svc.issue_certificate(db, student, student.course, f"Level Completion — {student.level or (student.course.name if student.course else 'Course')}",
                              user, generation="automatic", description="Awarded on passing the level completion evaluation.", request=request)
    from app.core.notify import notify
    if student.client and student.client.user_id:
        notify(db, student.client.user_id, f"New evaluation for {student.full_name}",
               f"{ev.evaluation_type.replace('_', ' ').title()} evaluation: {score}/{max_score} — {result.upper()}."
               + (f"\n{ev.teacher_comment}" if ev.teacher_comment else ""), event_type="evaluation", link="/portal/progress")
    db.commit()
    return redirect(f"/academics/evaluations/{ev.id}", "Evaluation recorded and the guardian notified.")


@router.get("/evaluations/{eid}", include_in_schema=False)
def evaluation_detail(eid: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("evaluations.view")), ctx: UserContext = Depends(get_user_context)):
    ev = _get(db, Evaluation, eid, "Evaluation")
    _check_student(ev.student, _scope_ids(user, ctx))
    history = (db.query(Evaluation).filter(Evaluation.student_id == ev.student_id)
               .order_by(Evaluation.date.desc(), Evaluation.id.desc()).limit(20).all())
    chart = {"labels": [e.date.strftime("%d %b") for e in reversed(history)],
             "scores": [e.score or 0 for e in reversed(history)]}
    return render(request, "academics/evaluation_detail.html", {
        "user": user, "ev": ev, "history": history, "chart": chart, "criteria": svc.EVAL_CRITERIA,
        "types": EVAL_TYPES, "progress": svc.student_progress_summary(db, ev.student),
        "can_update": rbac.has_permission(user, "evaluations.update")})


@router.post("/evaluations/{eid}/edit", include_in_schema=False)
async def evaluation_edit(eid: int, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("evaluations.update"))):
    ev = _get(db, Evaluation, eid, "Evaluation")
    form = await request.form()
    before = snapshot(ev)
    criteria = dict(ev.criteria or {})
    for key in svc.EVAL_CRITERIA:
        if form.get(f"criteria_{key}") not in (None, ""):
            criteria[key] = max(0.0, min(10.0, parse_float(form.get(f"criteria_{key}"))))
    ev.criteria = criteria
    ev.score = parse_float(form.get("score"), ev.score or svc.evaluation_score(criteria))
    ev.result = form.get("result") or ev.result
    ev.teacher_comment = form.get("teacher_comment") or ev.teacher_comment
    if form.get("academic_comment"):
        ev.academic_comment = form.get("academic_comment")
        ev.reviewed_by_id = user.id
    log_action(db, user, "update", "evaluations", entity=ev, before=before, after=snapshot(ev), request=request,
               consequential=True, rationale=form.get("academic_comment") or form.get("teacher_comment"),
               description=f"Evaluation for {ev.student.full_name} updated to {ev.score} ({ev.result})")
    db.commit()
    return redirect(f"/academics/evaluations/{ev.id}", "Evaluation updated.")


@router.post("/evaluations/{eid}/delete", include_in_schema=False)
async def evaluation_delete(eid: int, request: Request, db: Session = Depends(get_db),
                            user: User = Depends(require("evaluations.delete"))):
    ev = _get(db, Evaluation, eid, "Evaluation")
    log_action(db, user, "delete", "evaluations", entity=ev, before=snapshot(ev), request=request,
               description=f"Evaluation for {ev.student.full_name} deleted")
    db.delete(ev)
    db.commit()
    return redirect("/academics/evaluations", "Evaluation deleted.")


# =============================================================================== monthly tests (Module 41)
@router.get("/monthly-tests", include_in_schema=False)
def monthly_tests(request: Request, page: int = 1, period: str = "", status: str = "", student: str = "",
                  teacher: str = "", grade: str = "", db: Session = Depends(get_db),
                  user: User = Depends(require("monthly_tests.view")), ctx: UserContext = Depends(get_user_context)):
    scope = _scope_ids(user, ctx)
    period = period or svc.previous_period(svc.period_of())
    q = db.query(MonthlyTest).filter(MonthlyTest.period == period)
    if scope is not None:
        q = q.filter(MonthlyTest.student_id.in_(scope or [0]))
    if status:
        q = q.filter(MonthlyTest.status == status)
    if student:
        q = q.filter(MonthlyTest.student_id == int(student))
    if teacher:
        q = q.filter(MonthlyTest.teacher_id == int(teacher))
    if grade:
        q = q.filter(MonthlyTest.grade == grade)
    pg = paginate(q.order_by(MonthlyTest.percentage.desc().nullslast(), MonthlyTest.id.desc()), page, 25)
    scoped = db.query(MonthlyTest).filter(MonthlyTest.period == period)
    if scope is not None:
        scoped = scoped.filter(MonthlyTest.student_id.in_(scope or [0]))
    stats = {"total": scoped.count(), "scored": scoped.filter(MonthlyTest.percentage.isnot(None)).count(),
             "delivered": scoped.filter(MonthlyTest.delivered_at.isnot(None)).count(),
             "pending": scoped.filter(MonthlyTest.percentage.is_(None)).count(),
             "avg": round(scoped.with_entities(func.avg(MonthlyTest.percentage)).scalar() or 0, 1),
             "declines": scoped.filter(MonthlyTest.improvement_pct < -10).count()}
    periods = [p[0] for p in db.query(MonthlyTest.period).distinct().order_by(MonthlyTest.period.desc()).limit(24).all()]
    for p in (svc.period_of(), svc.previous_period(svc.period_of())):
        if p not in periods:
            periods.append(p)
    periods = sorted(set(periods), reverse=True)
    base = f"/academics/monthly-tests?period={period}&status={status}&student={student}&teacher={teacher}&grade={grade}"
    return render(request, "academics/monthly_tests.html", {
        "user": user, "page": pg, "stats": stats, "period": period, "period_label": svc.period_label(period),
        "periods": periods, "status": status, "student": student, "teacher": teacher, "grade": grade,
        "statuses": ["generated", "scored", "card_generated", "delivered"], "grades": ["A", "B", "C", "D"],
        "base_url": base, "student_options": _student_options(db, scope), "teacher_options": _teacher_options(db),
        "can_generate": rbac.has_permission(user, "monthly_tests.add"),
        "can_score": rbac.has_permission(user, "monthly_tests.update")})


@router.get("/monthly-tests/reports", include_in_schema=False)
def monthly_test_reports(request: Request, period: str = "", teacher: str = "", db: Session = Depends(get_db),
                         user: User = Depends(require("monthly_tests.view")), ctx: UserContext = Depends(get_user_context)):
    scope = _scope_ids(user, ctx)
    period = period or svc.previous_period(svc.period_of())
    rep = svc.improvement_report(db, period, int(teacher) if teacher else None, scope)
    trend_periods = [svc.shift_period(period, -i) for i in range(5, -1, -1)]
    trend = []
    for p in trend_periods:
        q = db.query(func.avg(MonthlyTest.percentage)).filter(MonthlyTest.period == p, MonthlyTest.percentage.isnot(None))
        if scope is not None:
            q = q.filter(MonthlyTest.student_id.in_(scope or [0]))
        trend.append(round(q.scalar() or 0, 1))
    periods = sorted({p[0] for p in db.query(MonthlyTest.period).distinct().all()} | {period}, reverse=True)
    return render(request, "academics/test_reports.html", {
        "user": user, "rep": rep, "period": period, "period_label": svc.period_label(period), "periods": periods,
        "teacher": teacher, "teacher_options": _teacher_options(db),
        "trend": {"labels": [svc.period_label(p) for p in trend_periods], "data": trend},
        "teacher_chart": {"labels": [r["teacher"].full_name if r["teacher"] else "—" for r in rep["teachers"]][:15],
                          "avg": [r["avg_pct"] for r in rep["teachers"]][:15],
                          "imp": [r["avg_imp"] for r in rep["teachers"]][:15]},
        "class_chart": {"labels": [c["label"] for c in rep["classes"]][:15],
                        "avg": [c["avg_pct"] for c in rep["classes"]][:15],
                        "imp": [c["avg_imp"] for c in rep["classes"]][:15]}})


@router.post("/monthly-tests/generate", include_in_schema=False)
async def monthly_tests_generate(request: Request, db: Session = Depends(get_db),
                                 user: User = Depends(require("monthly_tests.add"))):
    form = await request.form()
    period = (form.get("period") or svc.period_of()).strip()
    try:
        created = svc.generate_tests_for_period(db, period, user, request=request)
    except Exception as exc:
        return redirect("/academics/monthly-tests", f"Could not generate tests: {exc}", "error")
    db.commit()
    return redirect(f"/academics/monthly-tests?period={period}",
                    f"{created} test(s) generated for {svc.period_label(period)}." if created
                    else f"All active students already have a test for {svc.period_label(period)}.",
                    "success" if created else "info")


@router.get("/monthly-tests/{tid}", include_in_schema=False)
def monthly_test_detail(tid: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("monthly_tests.view")), ctx: UserContext = Depends(get_user_context)):
    t = _get(db, MonthlyTest, tid, "Monthly test")
    _check_student(t.student, _scope_ids(user, ctx))
    history = (db.query(MonthlyTest).filter(MonthlyTest.student_id == t.student_id)
               .order_by(MonthlyTest.period.desc()).limit(12).all())
    dor = (db.query(DorSchedule).filter(DorSchedule.student_id == t.student_id)
           .order_by(DorSchedule.period.desc()).first())
    card_exists = bool(t.result_card_path and (BASE_DIR / t.result_card_path).exists())
    chart = {"labels": [svc.period_label(h.period) for h in reversed(history)],
             "data": [h.percentage or 0 for h in reversed(history)]}
    return render(request, "academics/monthly_test_detail.html", {
        "user": user, "t": t, "history": history, "dor": dor, "chart": chart, "card_exists": card_exists,
        "period_label": svc.period_label(t.period), "progress": svc.student_progress_summary(db, t.student),
        "can_score": rbac.has_permission(user, "monthly_tests.update"),
        "can_deliver": rbac.has_permission(user, "monthly_tests.execute") or rbac.has_permission(user, "monthly_tests.update"),
        "can_override": rbac.has_permission(user, "monthly_tests.approve") or rbac.is_management(user)
                        or rbac.has_permission(user, "monthly_tests.update")})


@router.post("/monthly-tests/{tid}/score", include_in_schema=False)
async def monthly_test_score(tid: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("monthly_tests.update")), ctx: UserContext = Depends(get_user_context)):
    t = _get(db, MonthlyTest, tid, "Monthly test")
    _check_student(t.student, _scope_ids(user, ctx))
    form = await request.form()
    scores = {}
    for i in range(len(t.questions or [])):
        v = form.get(f"score_{i}")
        if v not in (None, ""):
            scores[str(i)] = parse_float(v)
    if not scores:
        return redirect(f"/academics/monthly-tests/{t.id}", "Enter at least one question score.", "error")
    remarks = (form.get("teacher_remarks") or "").strip()
    if not remarks:
        return redirect(f"/academics/monthly-tests/{t.id}", "Teacher remarks are required on the result card.", "error")
    svc.score_monthly_test(db, t, scores, remarks, form.get("teacher_remarks_urdu") or None, user, request=request)
    db.commit()
    msg = f"Scored {t.percentage}% (grade {t.grade})."
    if t.improvement_pct is not None:
        msg += f" Improvement {t.improvement_pct:+.1f} pts."
        if t.improvement_pct < -10:
            msg += " A retention cohort call has been scheduled."
    return redirect(f"/academics/monthly-tests/{t.id}", msg + " Result card and next dor schedule generated.")


@router.post("/monthly-tests/{tid}/deliver", include_in_schema=False)
async def monthly_test_deliver(tid: int, request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require("monthly_tests.update"))):
    t = _get(db, MonthlyTest, tid, "Monthly test")
    try:
        svc.deliver_result_card(db, t, user, request=request)
    except svc.AcademicError as exc:
        return redirect(f"/academics/monthly-tests/{t.id}", str(exc), "error")
    db.commit()
    return redirect(f"/academics/monthly-tests/{t.id}",
                    f"Result card delivered via {', '.join(t.delivery_channels) or 'in-app'}.")


@router.get("/monthly-tests/{tid}/card", include_in_schema=False)
def monthly_test_card(tid: int, db: Session = Depends(get_db), user: User = Depends(require("monthly_tests.view")),
                      ctx: UserContext = Depends(get_user_context)):
    t = _get(db, MonthlyTest, tid, "Monthly test")
    _check_student(t.student, _scope_ids(user, ctx))
    if not t.result_card_path or not (BASE_DIR / t.result_card_path).exists():
        svc.generate_result_card_pdf(db, t)
        db.commit()
    path = BASE_DIR / t.result_card_path
    if not path.exists():
        raise HTTPException(404, "Result card not available")
    return FileResponse(str(path), media_type="application/pdf",
                        filename=f"result-card-{t.student.student_code}-{t.period}.pdf")


@router.post("/monthly-tests/{tid}/dor/{index}", include_in_schema=False)
async def dor_item_toggle(tid: int, index: int, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("monthly_tests.update"))):
    t = _get(db, MonthlyTest, tid, "Monthly test")
    form = await request.form()
    sched = (db.query(DorSchedule).filter(DorSchedule.student_id == t.student_id)
             .order_by(DorSchedule.period.desc()).first())
    if not sched:
        return redirect(f"/academics/monthly-tests/{t.id}", "No dor schedule for this student yet.", "error")
    svc.mark_dor_item(db, sched, index, parse_bool(form.get("done")), user, request=request)
    db.commit()
    return redirect(f"/academics/monthly-tests/{t.id}", f"Dor progress: {sched.completed}/{sched.quota}.")


@router.post("/monthly-tests/{tid}/dor-override", include_in_schema=False)
async def dor_override_from_test(tid: int, request: Request, db: Session = Depends(get_db),
                                 user: User = Depends(require("monthly_tests.update"))):
    t = _get(db, MonthlyTest, tid, "Monthly test")
    form = await request.form()
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    try:
        svc.override_dor_quota(db, t.student, user, reason, request=request)
    except svc.AcademicError as exc:
        return redirect(f"/academics/monthly-tests/{t.id}", str(exc), "error")
    db.commit()
    return redirect(f"/academics/monthly-tests/{t.id}", "Dor quota override recorded — new sabaq is unblocked.")


# =============================================================================== certificates
@router.get("/certificates", include_in_schema=False)
def certificates(request: Request, page: int = 1, q: str = "", course: str = "", generation: str = "",
                 revoked: str = "", db: Session = Depends(get_db), user: User = Depends(require("certificates.view")),
                 ctx: UserContext = Depends(get_user_context)):
    scope = _scope_ids(user, ctx)
    query = db.query(Certificate)
    if scope is not None:
        query = query.filter(Certificate.student_id.in_(scope or [0]))
    if q:
        like = f"%{q}%"
        query = query.join(Student, Certificate.student_id == Student.id).filter(
            or_(Certificate.certificate_number.ilike(like), Certificate.title.ilike(like),
                Student.full_name.ilike(like), Student.student_code.ilike(like)))
    if course:
        query = query.filter(Certificate.course_id == int(course))
    if generation:
        query = query.filter(Certificate.generation == generation)
    if revoked == "yes":
        query = query.filter(Certificate.is_revoked.is_(True))
    elif revoked == "no":
        query = query.filter(Certificate.is_revoked.is_(False))
    pg = paginate(query.order_by(Certificate.issued_at.desc(), Certificate.id.desc()), page, 25)
    scoped = db.query(Certificate)
    if scope is not None:
        scoped = scoped.filter(Certificate.student_id.in_(scope or [0]))
    stats = {"total": scoped.count(), "automatic": scoped.filter(Certificate.generation == "automatic").count(),
             "manual": scoped.filter(Certificate.generation == "manual").count(),
             "revoked": scoped.filter(Certificate.is_revoked.is_(True)).count(),
             "verifications": scoped.with_entities(func.sum(Certificate.verification_count)).scalar() or 0,
             "this_year": scoped.filter(Certificate.certificate_number.like(f"OQC-{date.today().year}-%")).count()}
    base = f"/academics/certificates?q={q}&course={course}&generation={generation}&revoked={revoked}"
    return render(request, "academics/certificates.html", {
        "user": user, "page": pg, "stats": stats, "q": q, "course": course, "generation": generation, "revoked": revoked,
        "base_url": base, "course_options": _course_options(db), "student_options": _student_options(db, scope),
        "today_iso": date.today().isoformat(), "next_number": svc.next_certificate_number(db),
        "can_add": rbac.has_permission(user, "certificates.add"),
        "can_revoke": rbac.has_permission(user, "certificates.delete") or rbac.has_permission(user, "certificates.approve")})


@router.post("/certificates/new", include_in_schema=False)
async def certificate_create(request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("certificates.add")), ctx: UserContext = Depends(get_user_context)):
    form = await request.form()
    student = db.get(Student, parse_int(form.get("student_id")) or 0)
    title = (form.get("title") or "").strip()
    if not student or not title:
        return redirect("/academics/certificates", "Student and certificate title are required.", "error")
    _check_student(student, _scope_ids(user, ctx))
    course = db.get(Course, parse_int(form.get("course_id")) or 0) if form.get("course_id") else student.course
    cert = svc.issue_certificate(db, student, course, title, user, generation="manual",
                                 description=form.get("description") or None,
                                 issued_at=parse_date(form.get("issued_at")) or date.today(), request=request)
    db.commit()
    return redirect(f"/academics/certificates/{cert.id}", f"Certificate {cert.certificate_number} issued.")


@router.get("/certificates/{cid}", include_in_schema=False)
def certificate_detail(cid: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("certificates.view")), ctx: UserContext = Depends(get_user_context)):
    cert = _get(db, Certificate, cid, "Certificate")
    _check_student(cert.student, _scope_ids(user, ctx))
    events = (db.query(AuditEvent).filter(AuditEvent.entity_type == "Certificate", AuditEvent.entity_id == cert.id)
              .order_by(AuditEvent.created_at.desc()).limit(30).all())
    return render(request, "academics/certificate_detail.html", {
        "user": user, "cert": cert, "events": events, "verify_url": svc.verification_url(cert),
        "pdf_exists": bool(cert.pdf_path and (BASE_DIR / cert.pdf_path).exists()),
        "others": db.query(Certificate).filter(Certificate.student_id == cert.student_id, Certificate.id != cert.id)
                    .order_by(Certificate.issued_at.desc()).all(),
        "can_revoke": rbac.has_permission(user, "certificates.delete") or rbac.has_permission(user, "certificates.approve"),
        "can_update": rbac.has_permission(user, "certificates.update")})


@router.get("/certificates/{cid}/download", include_in_schema=False)
def certificate_download(cid: int, db: Session = Depends(get_db), user: User = Depends(require("certificates.view")),
                         ctx: UserContext = Depends(get_user_context)):
    cert = _get(db, Certificate, cid, "Certificate")
    _check_student(cert.student, _scope_ids(user, ctx))
    if not cert.pdf_path or not (BASE_DIR / cert.pdf_path).exists():
        svc.generate_certificate_pdf(db, cert)
        db.commit()
    path = BASE_DIR / cert.pdf_path
    if not path.exists():
        raise HTTPException(404, "Certificate PDF not available")
    return FileResponse(str(path), media_type="application/pdf", filename=f"{cert.certificate_number}.pdf")


@router.post("/certificates/{cid}/regenerate", include_in_schema=False)
async def certificate_regenerate(cid: int, request: Request, db: Session = Depends(get_db),
                                 user: User = Depends(require("certificates.update"))):
    cert = _get(db, Certificate, cid, "Certificate")
    svc.generate_certificate_pdf(db, cert)
    log_action(db, user, "update", "certificates", entity=cert, request=request,
               description=f"Certificate PDF regenerated for {cert.certificate_number}")
    db.commit()
    return redirect(f"/academics/certificates/{cert.id}", "Certificate PDF regenerated.")


@router.post("/certificates/{cid}/revoke", include_in_schema=False)
async def certificate_revoke(cid: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("certificates.delete", "certificates.approve", any_of=True))):
    cert = _get(db, Certificate, cid, "Certificate")
    form = await request.form()
    try:
        svc.revoke_certificate(db, cert, user, (form.get("reason") or form.get("rationale") or ""), request=request)
    except svc.AcademicError as exc:
        return redirect(f"/academics/certificates/{cert.id}", str(exc), "error")
    db.commit()
    return redirect(f"/academics/certificates/{cert.id}", f"Certificate {cert.certificate_number} revoked.")


# =============================================================================== shared Arabic lesson view (Module 28)
def _arabic_view_access(db: Session, user: User, ctx: UserContext, student: Student) -> None:
    """arabic_view.view, or the student, or their guardian, or their teacher."""
    if rbac.has_permission(user, "arabic_view.view") and (_scope_ids(user, ctx) is None or student.id in ctx.student_ids):
        return
    if ctx.student and ctx.student.id == student.id:
        return
    if ctx.client and student.client_id == ctx.client.id:
        return
    if ctx.teacher and student.teacher_id == ctx.teacher.id:
        return
    raise PermissionDenied("arabic_view.view")


def _view_lesson(db: Session, student: Student, lesson_id: Optional[int]) -> Optional[Lesson]:
    if lesson_id:
        les = db.get(Lesson, lesson_id)
        if les:
            return les
    return svc.current_lesson_for(db, student)


@router.get("/arabic-view/{student_id}", include_in_schema=False)
def arabic_view(student_id: int, request: Request, lesson_id: int = 0, db: Session = Depends(get_db),
                user: User = Depends(get_current_user), ctx: UserContext = Depends(get_user_context)):
    student = _get(db, Student, student_id, "Student")
    _arabic_view_access(db, user, ctx, student)
    les = _view_lesson(db, student, lesson_id or None)
    lessons = svc.course_lessons(db, student.course_id)
    pm = svc.progress_map(db, student.id)
    history = [{"lesson": l, "status": pm[l.id].status, "at": pm[l.id].completed_at or pm[l.id].started_at}
               for l in lessons if l.id in pm][-25:]
    return render(request, "academics/arabic_view.html", {
        "user": user, "s": student, "les": les, "path": svc.lesson_path(les), "lines": svc.lesson_words(les),
        "legend": svc.TAJWEED_LEGEND, "annotations": svc.annotations_for(db, student.id, les.id if les else None),
        "prev": svc.prev_lesson_before(db, les), "next": svc.next_lesson_after(db, les), "history": list(reversed(history)),
        "annotation_types": ANNOTATION_TYPES, "progress": svc.student_progress_summary(db, student),
        "can_annotate": bool(ctx.teacher or rbac.has_permission(user, "arabic_view.add")
                             or rbac.has_permission(user, "arabic_view.update"))})


@router.get("/arabic-view/{student_id}/annotations.json", include_in_schema=False)
def arabic_annotations(student_id: int, lesson_id: int = 0, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user), ctx: UserContext = Depends(get_user_context)):
    student = _get(db, Student, student_id, "Student")
    _arabic_view_access(db, user, ctx, student)
    les = _view_lesson(db, student, lesson_id or None)
    return JSONResponse({"lesson_id": les.id if les else None, "lesson": svc.lesson_path(les),
                         "annotations": svc.annotations_for(db, student.id, les.id if les else None),
                         "server_time": datetime.utcnow().isoformat()})


@router.post("/arabic-view/{student_id}/annotate", include_in_schema=False)
async def arabic_annotate(student_id: int, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user), ctx: UserContext = Depends(get_user_context)):
    student = _get(db, Student, student_id, "Student")
    _arabic_view_access(db, user, ctx, student)
    if not (ctx.teacher or rbac.has_permission(user, "arabic_view.add") or rbac.has_permission(user, "arabic_view.update")):
        raise PermissionDenied("arabic_view.add")
    try:
        body = await request.json()
    except Exception:
        form = await request.form()
        body = dict(form)
    lesson_id = parse_int(body.get("lesson_id"))
    les = _view_lesson(db, student, lesson_id)
    atype = body.get("annotation_type") or "highlight"
    if atype not in ANNOTATION_TYPES:
        return JSONResponse({"ok": False, "error": "Invalid annotation type"}, status_code=400)
    word_index = parse_int(body.get("word_index"))
    existing = (db.query(LessonAnnotation)
                .filter(LessonAnnotation.student_id == student.id, LessonAnnotation.lesson_id == (les.id if les else None),
                        LessonAnnotation.word_index == word_index, LessonAnnotation.author_id == user.id,
                        LessonAnnotation.annotation_type == atype).first())
    if existing and not (body.get("note") or "").strip():
        db.delete(existing)
        log_action(db, user, "delete", "arabic_view", entity_type="LessonAnnotation", entity_id=existing.id,
                   description=f"Annotation cleared on word {word_index} for {student.full_name}", request=request)
        db.commit()
        return JSONResponse({"ok": True, "removed": True,
                             "annotations": svc.annotations_for(db, student.id, les.id if les else None)})
    ann = existing or LessonAnnotation(student_id=student.id, lesson_id=les.id if les else None, author_id=user.id,
                                       word_index=word_index, annotation_type=atype)
    ann.color = body.get("color") or {"highlight": "#facc15", "mistake": "#f43f5e", "tajweed": "#7c3aed", "note": "#0ea5e9"}[atype]
    ann.note = (body.get("note") or None)
    if not existing:
        db.add(ann)
    db.flush()
    log_action(db, user, "update" if existing else "create", "arabic_view", entity=ann, request=request,
               description=f"{atype.title()} annotation on word {word_index} of '{les.title if les else '—'}' for {student.full_name}")
    db.commit()
    return JSONResponse({"ok": True, "id": ann.id,
                         "annotations": svc.annotations_for(db, student.id, les.id if les else None)})


# =============================================================================== progress
@router.get("/progress/{student_id}", include_in_schema=False)
def progress_page(student_id: int, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require("students.view", "curriculum.view", any_of=True)),
                  ctx: UserContext = Depends(get_user_context)):
    s = _get(db, Student, student_id, "Student")
    _check_student(s, _scope_ids(user, ctx))
    summary = svc.student_progress_summary(db, s)
    books = svc.book_breakdown(db, s)
    evaluations = db.query(Evaluation).filter(Evaluation.student_id == s.id).order_by(Evaluation.date.desc()).limit(20).all()
    tests = db.query(MonthlyTest).filter(MonthlyTest.student_id == s.id).order_by(MonthlyTest.period.desc()).limit(12).all()
    plans = db.query(LessonPlan).filter(LessonPlan.student_id == s.id).order_by(LessonPlan.plan_date.desc()).limit(15).all()
    dor = db.query(DorSchedule).filter(DorSchedule.student_id == s.id).order_by(DorSchedule.period.desc()).first()
    timeline = []
    for e in evaluations:
        timeline.append({"date": e.date, "kind": "evaluation", "title": f"{e.evaluation_type.replace('_', ' ').title()} evaluation",
                         "detail": f"{e.score}/{e.max_score} — {e.result}", "badge": e.result, "url": f"/academics/evaluations/{e.id}"})
    for t in tests:
        timeline.append({"date": svc.month_bounds(t.period)[1], "kind": "test", "title": f"Monthly test — {svc.period_label(t.period)}",
                         "detail": f"{t.percentage}% (grade {t.grade})" if t.percentage is not None else "Not scored yet",
                         "badge": t.status, "url": f"/academics/monthly-tests/{t.id}"})
    for p in plans[:8]:
        timeline.append({"date": p.plan_date, "kind": "plan", "title": f"{p.plan_type.title()} lesson plan",
                         "detail": (p.delivered_content or p.planned_content or "")[:120], "badge": p.status,
                         "url": f"/academics/lesson-plans/{p.id}"})
    timeline.sort(key=lambda x: x["date"] or date.min, reverse=True)
    counts = {k: sum(1 for pr in svc.progress_map(db, s.id).values() if pr.progress_type == k) for k in PROGRESS_TYPES}
    return render(request, "academics/progress.html", {
        "user": user, "s": s, "summary": summary, "books": books, "timeline": timeline[:40], "dor": dor,
        "type_counts": counts, "statuses": PROGRESS_STATUSES, "types": PROGRESS_TYPES,
        "lesson_options": _lesson_options(db, s.course_id),
        "chart": {"labels": [b["book"].title for b in books], "data": [b["pct"] for b in books]},
        "can_update": rbac.has_permission(user, "lesson_plans.update") or rbac.has_permission(user, "curriculum.update")
                      or rbac.has_permission(user, "students.update"),
        "can_override": rbac.has_permission(user, "monthly_tests.update") or rbac.is_management(user)})


@router.post("/progress/{student_id}/lesson", include_in_schema=False)
async def progress_mark(student_id: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require("lesson_plans.update", "curriculum.update", "students.update", any_of=True)),
                        ctx: UserContext = Depends(get_user_context)):
    s = _get(db, Student, student_id, "Student")
    _check_student(s, _scope_ids(user, ctx))
    form = await request.form()
    les = db.get(Lesson, parse_int(form.get("lesson_id")) or 0)
    status = form.get("status") or "in_progress"
    if not les:
        return redirect(f"/academics/progress/{s.id}", "Select a lesson.", "error")
    try:
        svc.record_progress(db, s, les, status, user, progress_type=form.get("progress_type") or "sabaq",
                            score=parse_float(form.get("score")) if form.get("score") else None,
                            notes=form.get("notes") or None, request=request)
    except svc.DorQuotaBlocked as exc:
        return redirect(f"/academics/progress/{s.id}", str(exc), "warning")
    except svc.AcademicError as exc:
        return redirect(f"/academics/progress/{s.id}", str(exc), "error")
    db.commit()
    return redirect(f"/academics/progress/{s.id}", f"'{les.title}' marked {status.replace('_', ' ')}.")


@router.post("/progress/{student_id}/dor-override", include_in_schema=False)
async def progress_dor_override(student_id: int, request: Request, db: Session = Depends(get_db),
                                user: User = Depends(require("monthly_tests.update", "students.update", any_of=True))):
    s = _get(db, Student, student_id, "Student")
    form = await request.form()
    try:
        svc.override_dor_quota(db, s, user, (form.get("reason") or form.get("rationale") or ""), request=request)
    except svc.AcademicError as exc:
        return redirect(f"/academics/progress/{s.id}", str(exc), "error")
    db.commit()
    return redirect(f"/academics/progress/{s.id}", "Dor quota override recorded (audited).")
