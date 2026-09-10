"""Client / parent portal (/portal). Strictly scoped to ctx.client and ctx.student_ids."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import require, csrf_protect, get_user_context, UserContext, PermissionDenied
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import redirect, next_code, parse_date, parse_int, parse_bool, parse_float
from app.database import get_db
from app.models.academic import Certificate, Evaluation, MonthlyTest
from app.models.core import User, Notification, CommunicationPreference, Setting
from app.models.crm import Case, CaseComment, Feedback, Referral
from app.models.finance import Invoice, LedgerEntry, Payment, Subscription, Receipt
from app.models.people import Client, Student, Leave
from app.models.scheduling import ClassSession, Attendance, Schedule
from app.services import people as svc
from app.services.classes import student_attendance_pct

router = APIRouter(prefix="/portal", dependencies=[Depends(csrf_protect)])


def me(ctx: UserContext) -> Client:
    if not ctx.client:
        raise PermissionDenied("portal_client.view (no client profile linked to this login)")
    return ctx.client


def my_students(db: Session, c: Client) -> list[Student]:
    return db.query(Student).filter(Student.client_id == c.id).order_by(Student.full_name).all()


def scoped_student(db: Session, ctx: UserContext, student_id) -> Student | None:
    """Return the requested child, 404 if it does not belong to this parent. None when no id is given."""
    sid = parse_int(student_id)
    if not sid:
        return None
    if sid not in (ctx.student_ids or []):
        raise HTTPException(404, "Student not found")
    s = db.query(Student).filter(Student.id == sid, Student.client_id == ctx.client_id).first()
    if not s:
        raise HTTPException(404, "Student not found")
    return s


def _ids(ctx: UserContext) -> list[int]:
    return ctx.student_ids or [-1]


# --------------------------------------------------------------------------- home
@router.get("", include_in_schema=False)
def home(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_client.view")),
         ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    kids = my_students(db, c)
    cards = []
    for s in kids:
        nxt = (db.query(ClassSession).filter(ClassSession.student_id == s.id, ClassSession.status.in_(["pending", "started"]),
                                             ClassSession.scheduled_start >= datetime.utcnow() - timedelta(hours=3))
               .order_by(ClassSession.scheduled_start).first())
        cards.append({"s": s, "next": nxt, "next_local": svc.to_client_tz(nxt.scheduled_start, c.timezone) if nxt else None,
                      "attendance": student_attendance_pct(db, s.id, 30),
                      "progress": svc.student_progress_summary(db, s).get("pct", 0)})
    notes = db.query(Notification).filter(Notification.user_id == user.id, Notification.channel == "in_app").order_by(Notification.created_at.desc()).limit(8).all()
    pending_surveys = db.query(func.count(Feedback.id)).filter(Feedback.client_id == c.id, Feedback.status == "pending").scalar() or 0
    open_cases = db.query(func.count(Case.id)).filter(Case.client_id == c.id, Case.status.in_(["open", "in_progress", "waiting", "escalated"])).scalar() or 0
    return render(request, "portal/home.html", {
        "user": user, "c": c, "cards": cards, "notifications": notes, "balance": svc.client_balance(db, c),
        "pending_surveys": pending_surveys, "open_cases": open_cases,
        "overdue": db.query(func.count(Invoice.id)).filter(Invoice.client_id == c.id, Invoice.status == "overdue").scalar() or 0})


# --------------------------------------------------------------------------- schedule / attendance / progress
@router.get("/schedule", include_in_schema=False)
def schedule(request: Request, student_id: str = "", week: int = 0, db: Session = Depends(get_db),
             user: User = Depends(require("portal_client.view")), ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    child = scoped_student(db, ctx, student_id)
    ids = [child.id] if child else _ids(ctx)
    today = date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week)
    rows = (db.query(ClassSession).filter(ClassSession.student_id.in_(ids), ClassSession.date >= monday,
                                          ClassSession.date <= monday + timedelta(days=6))
            .order_by(ClassSession.scheduled_start).all())
    items = [{"cs": r, "local": svc.to_client_tz(r.scheduled_start, c.timezone),
              "can_join": r.status in ("pending", "started")} for r in rows]
    schedules = db.query(Schedule).filter(Schedule.student_id.in_(ids), Schedule.status == "active").all()
    return render(request, "portal/schedule.html", {"user": user, "c": c, "items": items, "children": my_students(db, c),
                                                    "child": child, "week": week, "monday": monday,
                                                    "schedules": schedules, "org_tz": svc.ORG_TZ})


@router.get("/attendance", include_in_schema=False)
def attendance(request: Request, student_id: str = "", db: Session = Depends(get_db),
               user: User = Depends(require("portal_client.view")), ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    child = scoped_student(db, ctx, student_id)
    kids = [child] if child else my_students(db, c)
    since = date.today() - timedelta(days=90)
    blocks = []
    for s in kids:
        rows = db.query(Attendance).filter(Attendance.student_id == s.id, Attendance.date >= since).order_by(Attendance.date.desc()).all()
        blocks.append({"s": s, "rows": rows, "pct30": student_attendance_pct(db, s.id, 30),
                       "pct90": student_attendance_pct(db, s.id, 90),
                       "present": sum(1 for r in rows if r.student_status in ("present", "late")),
                       "absent": sum(1 for r in rows if r.student_status == "absent"),
                       "leave": sum(1 for r in rows if r.student_status == "leave")})
    return render(request, "portal/attendance.html", {"user": user, "c": c, "blocks": blocks, "children": my_students(db, c), "child": child})


@router.get("/progress", include_in_schema=False)
def progress(request: Request, student_id: str = "", db: Session = Depends(get_db),
             user: User = Depends(require("portal_client.view")), ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    child = scoped_student(db, ctx, student_id)
    kids = [child] if child else my_students(db, c)
    blocks = []
    for s in kids:
        blocks.append({"s": s, "p": svc.student_progress_summary(db, s),
                       "evaluations": db.query(Evaluation).filter(Evaluation.student_id == s.id).order_by(Evaluation.date.desc()).limit(8).all()})
    return render(request, "portal/progress.html", {"user": user, "c": c, "blocks": blocks, "children": my_students(db, c), "child": child})


@router.get("/result-cards", include_in_schema=False)
def result_cards(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_client.view")),
                 ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    rows = (db.query(MonthlyTest).filter(MonthlyTest.student_id.in_(_ids(ctx)))
            .order_by(MonthlyTest.period.desc(), MonthlyTest.id.desc()).all())
    return render(request, "portal/result_cards.html", {"user": user, "c": c, "rows": rows,
                                                        "names": {s.id: s.full_name for s in my_students(db, c)}})


@router.get("/certificates", include_in_schema=False)
def certificates(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_client.view")),
                 ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    rows = db.query(Certificate).filter(Certificate.student_id.in_(_ids(ctx)), Certificate.is_revoked.is_(False)).order_by(Certificate.issued_at.desc()).all()
    return render(request, "portal/certificates.html", {"user": user, "c": c, "rows": rows,
                                                        "names": {s.id: s.full_name for s in my_students(db, c)}})


# --------------------------------------------------------------------------- leaves
@router.get("/leaves", include_in_schema=False)
def leaves(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_client.view")),
           ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    rows = db.query(Leave).filter(Leave.person_type == "student", Leave.student_id.in_(_ids(ctx))).order_by(Leave.start_date.desc()).all()
    return render(request, "portal/leaves.html", {"user": user, "c": c, "rows": rows, "children": my_students(db, c)})


@router.post("/leaves", include_in_schema=False)
async def request_leave(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_client.view")),
                        ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    form = await request.form()
    s = scoped_student(db, ctx, form.get("student_id"))
    if not s:
        return redirect("/portal/leaves", "Choose which child the leave is for.", "error")
    start, end = parse_date(form.get("start_date")), parse_date(form.get("end_date"))
    if not start or not end or end < start:
        return redirect("/portal/leaves", "Please give a valid start and end date.", "error")
    lv = Leave(person_type="student", student_id=s.id, leave_type=form.get("leave_type") or "vacation",
               start_date=start, end_date=end, reason=(form.get("reason") or "").strip() or None,
               status="pending", requested_by_id=user.id)
    db.add(lv)
    db.flush()
    log_action(db, user, "create", "leaves", entity=lv, request=request,
               description=f"Parent requested student leave for {s.student_code}: {start}..{end}")
    if s.teacher and s.teacher.user_id:
        notify(db, s.teacher.user_id, f"Leave requested for {s.full_name}",
               f"{start} to {end}. {(lv.reason or '')}", event_type="leave", link="/teacher/students")
    db.commit()
    return redirect("/portal/leaves", "Leave request submitted. You will be notified once it is approved.")


# --------------------------------------------------------------------------- billing
@router.get("/billing", include_in_schema=False)
def billing(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_client.view")),
            ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    subs = db.query(Subscription).filter(Subscription.client_id == c.id).order_by(Subscription.created_at.desc()).all()
    invoices = db.query(Invoice).filter(Invoice.client_id == c.id).order_by(Invoice.issue_date.desc()).limit(60).all()
    payments = db.query(Payment).filter(Payment.client_id == c.id).order_by(Payment.received_at.desc()).limit(40).all()
    ledger = db.query(LedgerEntry).filter(LedgerEntry.client_id == c.id).order_by(LedgerEntry.entry_date.desc(), LedgerEntry.id.desc()).limit(120).all()
    receipts = {}
    if payments:
        for r in db.query(Receipt).filter(Receipt.payment_id.in_([p.id for p in payments])):
            receipts[r.payment_id] = r
    return render(request, "portal/billing.html", {
        "user": user, "c": c, "subs": subs, "invoices": invoices, "payments": payments, "ledger": ledger,
        "receipts": receipts, "balance": svc.client_balance(db, c),
        "names": {s.id: s.full_name for s in my_students(db, c)},
        "outstanding": sum(float(i.total) - float(i.paid_amount) for i in invoices if i.status in ("sent", "partial", "overdue"))})


@router.get("/billing/pay", include_in_schema=False)
def pay(request: Request, invoice_id: str = "", db: Session = Depends(get_db),
        user: User = Depends(require("portal_client.view")), ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    inv = None
    iid = parse_int(invoice_id)
    if iid:
        inv = db.query(Invoice).filter(Invoice.id == iid, Invoice.client_id == c.id).first()
        if not inv:
            raise HTTPException(404, "Invoice not found")
    def setting(key, default):
        row = db.query(Setting).filter(Setting.key == key).first()
        return row.value if row and row.value is not None else default
    return render(request, "portal/pay.html", {
        "user": user, "c": c, "invoice": inv, "balance": svc.client_balance(db, c),
        "bank": setting("bank_details", {"account_name": "Online Quran College", "bank": "Meezan Bank",
                                         "iban": "PK00MEZN0000000000000000", "swift": "MEZNPKKA"}),
        "methods": setting("payment_methods", ["Bank transfer", "Wise", "PayPal", "Stripe card link"])})


# --------------------------------------------------------------------------- cases
@router.get("/cases", include_in_schema=False)
def cases(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_client.view")),
          ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    rows = db.query(Case).filter(Case.client_id == c.id).order_by(Case.created_at.desc()).all()
    return render(request, "portal/cases.html", {"user": user, "c": c, "rows": rows, "children": my_students(db, c)})


@router.get("/cases/{cid}", include_in_schema=False)
def case_detail(cid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_client.view")),
                ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    case = db.query(Case).filter(Case.id == cid, Case.client_id == c.id).first()
    if not case:
        raise HTTPException(404, "Case not found")
    comments = db.query(CaseComment).filter(CaseComment.case_id == case.id, CaseComment.is_internal.is_(False)).order_by(CaseComment.created_at).all()
    return render(request, "portal/case_detail.html", {"user": user, "c": c, "case": case, "comments": comments})


def _sla_hours(db: Session, case_type: str) -> int:
    row = db.query(Setting).filter(Setting.key == "case_sla_hours").first()
    val = row.value if row else None
    if isinstance(val, dict):
        return int(val.get(case_type, val.get("complaint", 48)))
    if isinstance(val, (int, float, str)):
        try:
            return int(val)
        except (TypeError, ValueError):
            pass
    return 48


@router.post("/cases", include_in_schema=False)
async def open_case(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_client.view")),
                    ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    form = await request.form()
    title = (form.get("title") or "").strip()
    if not title:
        return redirect("/portal/cases", "Please give your request a short title.", "error")
    student = scoped_student(db, ctx, form.get("student_id"))
    case_type = form.get("case_type") or "request"
    case = None
    try:
        from app.services.crm import open_case as _open
        case = _open(db, case_type, title, form.get("description"), client=c, student=student,
                     raised_by_user=user, source="portal", actor=user, request=request)
    except Exception:
        hours = _sla_hours(db, case_type)
        case = Case(case_number=next_code(db, Case, "case_number", "CS-"), case_type=case_type, title=title[:200],
                    description=form.get("description"), raised_by_type="client", raised_by_user_id=user.id,
                    client_id=c.id, student_id=student.id if student else None,
                    teacher_id=student.teacher_id if student else None, priority="medium", status="open",
                    sla_hours=hours, sla_due_at=datetime.utcnow() + timedelta(hours=hours), source="portal")
        db.add(case)
        db.flush()
        log_action(db, user, "create", "cases", entity=case, request=request,
                   description=f"Case {case.case_number} opened from the parent portal ({case_type})")
    db.commit()
    return redirect(f"/portal/cases/{case.id}", f"Case {case.case_number} opened. We will respond within {case.sla_hours} hours.")


@router.post("/cases/{cid}/comment", include_in_schema=False)
async def case_comment(cid: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("portal_client.view")), ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    case = db.query(Case).filter(Case.id == cid, Case.client_id == c.id).first()
    if not case:
        raise HTTPException(404, "Case not found")
    form = await request.form()
    text = (form.get("text") or "").strip()
    if not text:
        return redirect(f"/portal/cases/{case.id}", "Write a message first.", "error")
    db.add(CaseComment(case_id=case.id, user_id=user.id, text=text, is_internal=False))
    if case.status == "waiting":
        case.status = "in_progress"
    log_action(db, user, "update", "cases", entity=case, request=request, description=f"Parent replied on {case.case_number}")
    if case.assigned_to_id:
        notify(db, case.assigned_to_id, f"Parent replied on {case.case_number}", text[:200],
               event_type="case_comment", link=f"/cases/{case.id}")
    db.commit()
    return redirect(f"/portal/cases/{case.id}", "Message sent.")


# --------------------------------------------------------------------------- feedback
@router.get("/feedback", include_in_schema=False)
def feedback(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_client.view")),
             ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    pending = db.query(Feedback).filter(Feedback.client_id == c.id, Feedback.status == "pending").order_by(Feedback.sent_at.desc()).all()
    history = db.query(Feedback).filter(Feedback.client_id == c.id, Feedback.status != "pending").order_by(Feedback.submitted_at.desc()).limit(20).all()
    return render(request, "portal/feedback.html", {"user": user, "c": c, "pending": pending, "history": history,
                                                    "names": {s.id: s.full_name for s in my_students(db, c)}})


@router.post("/feedback/{fid}", include_in_schema=False)
async def submit_feedback(fid: int, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("portal_client.view")), ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    fb = db.query(Feedback).filter(Feedback.id == fid, Feedback.client_id == c.id).first()
    if not fb:
        raise HTTPException(404, "Survey not found")
    form = await request.form()
    nps = parse_int(form.get("nps"))
    rating = parse_int(form.get("rating"))
    comment = (form.get("comment") or "").strip() or None
    try:
        from app.services.crm import submit_feedback as _submit
        _submit(db, fb, nps, rating, comment)
    except Exception:
        fb.nps, fb.rating, fb.comment = nps, rating, comment
        fb.submitted_at = datetime.utcnow()
        fb.status = "submitted"
        fb.is_negative = (nps is not None and nps <= 6) or (rating is not None and rating <= 2)
        fb.sentiment = "negative" if fb.is_negative else ("positive" if (nps or 0) >= 9 or (rating or 0) >= 4 else "neutral")
        if fb.is_negative:
            try:
                from app.services.crm import route_negative_feedback
                route_negative_feedback(db, fb)
            except Exception:
                qa_hod = db.query(User).filter(User.email == "qa@oqc.local").first()
                hours = _sla_hours(db, "feedback")
                case = Case(case_number=next_code(db, Case, "case_number", "CS-"), case_type="feedback",
                            title=f"Negative feedback from {c.full_name} (NPS {nps if nps is not None else '-'})",
                            description=comment or "Low score submitted without a comment.", raised_by_type="client",
                            raised_by_user_id=user.id, client_id=c.id, student_id=fb.student_id, teacher_id=fb.teacher_id,
                            category="teaching_quality", priority="high", status="open",
                            assigned_to_id=qa_hod.id if qa_hod else None, sla_hours=hours,
                            sla_due_at=datetime.utcnow() + timedelta(hours=hours), source="feedback")
                db.add(case)
                db.flush()
                fb.case_id = case.id
                fb.status = "routed"
                if qa_hod:
                    notify(db, qa_hod, f"Negative feedback routed: {case.case_number}", comment or "No comment provided.",
                           event_type="case_assigned", link=f"/cases/{case.id}")
    log_action(db, user, "create", "feedback", entity=fb, request=request,
               description=f"Parent feedback submitted (NPS {nps}, rating {rating}, {fb.sentiment})")
    db.commit()
    msg = "JazakAllahu khairan for your feedback."
    if fb.is_negative:
        msg += " We are sorry it has not met your expectations — our quality team has been alerted and will contact you."
    return redirect("/portal/feedback", msg, "success" if not fb.is_negative else "warning")


# --------------------------------------------------------------------------- referrals
@router.get("/referrals", include_in_schema=False)
def referrals(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_client.view")),
              ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    if not c.referral_code:
        c.referral_code = f"REF{c.id:05d}"
        db.commit()
    rows = db.query(Referral).filter(Referral.ambassador_client_id == c.id).order_by(Referral.created_at.desc()).all()
    credits = db.query(LedgerEntry).filter(LedgerEntry.client_id == c.id, LedgerEntry.entry_type == "credit").order_by(LedgerEntry.entry_date.desc()).all()
    from app.config import settings as app_settings
    return render(request, "portal/referrals.html", {
        "user": user, "c": c, "rows": rows, "credits": credits,
        "link": f"{app_settings.BASE_URL}/register?ref={c.referral_code}",
        "total_credit": round(sum(float(x.credit or 0) for x in credits), 2),
        "qualified": sum(1 for r in rows if r.status in ("qualified", "credited"))})


# --------------------------------------------------------------------------- profile
@router.get("/profile", include_in_schema=False)
def profile(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_client.view")),
            ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    prefs = {p.channel: p for p in db.query(CommunicationPreference).filter(CommunicationPreference.client_id == c.id)}
    return render(request, "portal/profile.html", {"user": user, "c": c, "prefs": prefs, "children": my_students(db, c),
                                                   "timezones": svc.TIMEZONES, "countries": svc.COUNTRY_NAMES})


@router.post("/profile", include_in_schema=False)
async def update_profile(request: Request, db: Session = Depends(get_db), user: User = Depends(require("portal_client.view")),
                         ctx: UserContext = Depends(get_user_context)):
    c = me(ctx)
    form = await request.form()
    before = {"phone": c.phone, "whatsapp": c.whatsapp, "timezone": c.timezone, "whatsapp_opt_in": c.whatsapp_opt_in,
              "city": c.city, "address": c.address}
    c.phone = (form.get("phone") or "").strip() or c.phone
    c.whatsapp = (form.get("whatsapp") or "").strip() or c.whatsapp
    c.city = (form.get("city") or "").strip() or None
    c.address = (form.get("address") or "").strip() or None
    if form.get("timezone") in svc.TIMEZONES:
        c.timezone = form.get("timezone")
    c.whatsapp_opt_in = parse_bool(form.get("whatsapp_opt_in"))
    c.preferences = {**(c.preferences or {}),
                     "preferred_contact_time": form.get("preferred_contact_time") or (c.preferences or {}).get("preferred_contact_time"),
                     "preferred_language": form.get("preferred_language") or (c.preferences or {}).get("preferred_language")}
    svc.upsert_comm_prefs(db, c, {"whatsapp": c.whatsapp_opt_in, "email": parse_bool(form.get("email_opt_in")), "in_app": True})
    if user:
        user.timezone = c.timezone
        user.phone = c.phone
    log_action(db, user, "update", "portal_client", entity=c, request=request,
               description=f"Parent updated their own profile ({c.client_code})", before=before,
               after={"phone": c.phone, "whatsapp": c.whatsapp, "timezone": c.timezone, "whatsapp_opt_in": c.whatsapp_opt_in})
    db.commit()
    return redirect("/portal/profile", "Your details have been updated.")
