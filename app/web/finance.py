"""Finance module web UI: subscriptions (9), discounts & scholarships (45), invoices (18), payments,
client ledger, accounts & P&L (19), expenses and currencies."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import require, csrf_protect
from app.core.templating import render
from app.core.utils import redirect, paginate, parse_date, parse_int, parse_float, parse_bool, month_key, month_bounds
from app.database import get_db
from app.models.academic import Course, Package
from app.models.core import User, AuditEvent, Department
from app.models.crm import Referral
from app.models.finance import (Account, Budget, Currency, DiscountRequest, ExchangeRateHistory, Expense,
                                FinancialPeriod, Invoice, InvoiceItem, JournalEntry, JournalLine, LedgerEntry,
                                Payment, Receipt, Scholarship, Subscription)
from app.models.people import Client, Student, Teacher
from app.services import accounting, billing

router = APIRouter(prefix="/finance", dependencies=[Depends(csrf_protect)])

UPLOAD_DIR = BASE_DIR / "storage" / "uploads"

ACCOUNT_TABS = [("chart", "Chart of accounts", "/finance/accounts"), ("journal", "Journal", "/finance/accounts/journal"),
                ("pnl", "P&L", "/finance/accounts/pnl"), ("cash", "Cash flow", "/finance/accounts/cash-flow"),
                ("aging", "Receivables aging", "/finance/accounts/aging"), ("payables", "Payables", "/finance/accounts/payables"),
                ("budget", "Budget vs actual", "/finance/accounts/budget"), ("forecast", "Forecast", "/finance/accounts/forecast"),
                ("close", "Financial close", "/finance/accounts/close"),
                ("consolidated", "Consolidated", "/finance/accounts/consolidated")]

SUB_TABS = [("overview", "Overview"), ("billing", "Billing history"), ("audit", "Audit trail")]


# ============================================================================ helpers
def _err(url: str, exc: Exception):
    return redirect(url, str(exc), "error")


def _sub(db: Session, id: int) -> Subscription:
    s = db.query(Subscription).get(id)
    if not s:
        raise HTTPException(404, "Subscription not found")
    return s


def _invoice(db: Session, id: int) -> Invoice:
    i = db.query(Invoice).get(id)
    if not i:
        raise HTTPException(404, "Invoice not found")
    return i


def _payment(db: Session, id: int) -> Payment:
    p = db.query(Payment).get(id)
    if not p:
        raise HTTPException(404, "Payment not found")
    return p


def _client(db: Session, id: int) -> Client:
    c = db.query(Client).get(id)
    if not c:
        raise HTTPException(404, "Client not found")
    return c


def _expense(db: Session, id: int) -> Expense:
    e = db.query(Expense).get(id)
    if not e:
        raise HTTPException(404, "Expense not found")
    return e


def _csv(rows: list[list], filename: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _period_options(count: int = 15) -> list[str]:
    today = date.today()
    out = []
    for i in range(count):
        y, m = today.year, today.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        out.append(f"{y:04d}-{m:02d}")
    return out


@router.get("", include_in_schema=False)
def finance_home(user: User = Depends(require("subscriptions.view", "billing.view", any_of=True))):
    return redirect("/finance/subscriptions")


# ============================================================================ Module 9 — subscriptions
@router.get("/subscriptions", include_in_schema=False)
def subscriptions_list(request: Request, page: int = 1, q: str = "", status: str = "", course_id: str = "",
                       package_id: str = "", teacher_id: str = "", currency: str = "",
                       db: Session = Depends(get_db), user: User = Depends(require("subscriptions.view"))):
    query = db.query(Subscription)
    if q:
        like = f"%{q}%"
        query = (query.join(Client, Subscription.client_id == Client.id)
                 .filter(or_(Client.full_name.ilike(like), Client.client_code.ilike(like),
                             Subscription.subscription_code.ilike(like))))
    if status:
        query = query.filter(Subscription.status == status)
    if course_id:
        query = query.filter(Subscription.course_id == int(course_id))
    if package_id:
        query = query.filter(Subscription.package_id == int(package_id))
    if teacher_id:
        query = query.filter(Subscription.teacher_id == int(teacher_id))
    if currency:
        query = query.filter(Subscription.currency == currency)
    pg = paginate(query.order_by(Subscription.created_at.desc()), page, 25)
    base = (f"/finance/subscriptions?q={q}&status={status}&course_id={course_id}&package_id={package_id}"
            f"&teacher_id={teacher_id}&currency={currency}")
    return render(request, "finance/subscriptions_list.html", {
        "user": user, "page": pg, "q": q, "status": status, "course_id": course_id, "package_id": package_id,
        "teacher_id": teacher_id, "currency": currency, "stats": billing.subscription_stats(db),
        "statuses": billing.SUBSCRIPTION_STATUSES, "base_url": base,
        "courses": [(c.id, c.name) for c in db.query(Course).order_by(Course.order).all()],
        "packages": [(p.id, p.name) for p in db.query(Package).order_by(Package.name).all()],
        "teachers": [(t.id, t.full_name) for t in db.query(Teacher).order_by(Teacher.full_name).all()],
        "currencies": [c.code for c in db.query(Currency).order_by(Currency.code).all()]})


@router.get("/subscriptions/report", include_in_schema=False)
def subscriptions_report(request: Request, group_by: str = "course", db: Session = Depends(get_db),
                         user: User = Depends(require("subscriptions.view"))):
    if group_by not in ("course", "package", "country", "teacher"):
        group_by = "course"
    rows = billing.subscription_report(db, group_by)
    return render(request, "finance/subscriptions_report.html", {
        "user": user, "rows": rows, "group_by": group_by, "base": billing.base_currency(db),
        "totals": {"count": sum(r["count"] for r in rows), "mrr": round(sum(r["mrr"] for r in rows), 2),
                   "cost": round(sum(r["cost"] for r in rows), 2),
                   "margin": round(sum(r["margin"] for r in rows), 2)},
        "labels": [r["key"] for r in rows], "values": [r["mrr"] for r in rows]})


def _subscription_form_context(db: Session) -> dict:
    clients = db.query(Client).filter(Client.status.in_(["active", "trial"])).order_by(Client.client_code).all()
    students = db.query(Student).filter(Student.status.in_(["active", "trial", "frozen"])).order_by(Student.student_code).all()
    packages = db.query(Package).filter(Package.is_active.is_(True), Package.is_trial.is_(False)).order_by(Package.name).all()
    teachers = db.query(Teacher).filter(Teacher.is_verified.is_(True), Teacher.status == "active").order_by(Teacher.full_name).all()
    scholarships = (db.query(Scholarship).filter(Scholarship.status == "approved").order_by(Scholarship.id.desc()).all())
    return {
        "clients": clients,
        "client_options": [(c.id, f"{c.client_code} — {c.full_name} ({c.country})") for c in clients],
        "students_by_client": {str(c.id): [{"id": s.id, "name": f"{s.student_code} — {s.full_name}",
                                            "teacher_id": s.teacher_id, "course_id": s.course_id} for s in c.students]
                               for c in clients},
        "client_meta": {str(c.id): {"country": c.country, "currency": c.currency} for c in clients},
        "packages": [{"id": p.id, "name": p.name, "price": float(p.price), "currency": p.currency,
                      "country": p.country or "", "spw": p.sessions_per_week, "minutes": p.session_minutes} for p in packages],
        "package_options": [(p.id, f"{p.name} — {p.currency} {float(p.price):,.0f}") for p in packages],
        "teacher_options": [(t.id, f"{t.teacher_code} — {t.full_name} (rate {float(t.per_class_rate):,.0f})") for t in teachers],
        "teacher_rates": {str(t.id): float(t.per_class_rate) for t in teachers},
        "scholarship_options": [(s.id, f"#{s.id} {s.client.client_code if s.client else ''} — "
                                       f"{s.scholarship_type.replace('_', ' ').title()} "
                                       f"{(str(int(s.coverage_pct)) + '%') if s.coverage_pct else (s.currency + ' ' + format(float(s.monthly_amount), ',.0f'))}")
                                for s in scholarships],
        "currencies": [c.code for c in db.query(Currency).order_by(Currency.code).all()],
        "thresholds": billing.discount_thresholds(db), "students": students,
        "rates": {c.code: float(c.rate_to_base) for c in db.query(Currency).all()},
        "base": billing.base_currency(db),
    }


@router.get("/subscriptions/new", include_in_schema=False)
def subscription_new(request: Request, client_id: str = "", student_id: str = "", db: Session = Depends(get_db),
                     user: User = Depends(require("subscriptions.add"))):
    ctx = _subscription_form_context(db)
    return render(request, "finance/subscription_form.html", {"user": user, **ctx, "client_id": client_id,
                                                              "student_id": student_id})


@router.post("/subscriptions/new", include_in_schema=False)
async def subscription_create(request: Request, db: Session = Depends(get_db),
                              user: User = Depends(require("subscriptions.add"))):
    form = await request.form()
    client = db.query(Client).get(parse_int(form.get("client_id"), 0) or 0)
    student = db.query(Student).get(parse_int(form.get("student_id"), 0) or 0)
    package = db.query(Package).get(parse_int(form.get("package_id"), 0) or 0) if form.get("package_id") else None
    teacher = db.query(Teacher).get(parse_int(form.get("teacher_id"), 0) or 0) if form.get("teacher_id") else None
    scholarship = db.query(Scholarship).get(parse_int(form.get("scholarship_id"), 0) or 0) if form.get("scholarship_id") else None
    if not client or not student:
        return redirect("/finance/subscriptions/new", "Select both a client and a student.", "error")
    if student.client_id != client.id:
        return redirect("/finance/subscriptions/new", "That student does not belong to the selected family.", "error")
    discount_pct = parse_float(form.get("discount_pct"), 0)
    rationale = (form.get("rationale") or "").strip()
    if discount_pct > 0 and not rationale:
        return redirect("/finance/subscriptions/new", "A rationale is required whenever a discount is applied.", "error")
    try:
        sub = billing.create_subscription(
            db, client=client, student=student, package=package,
            price=parse_float(form.get("price"), float(package.price) if package else 0),
            currency=(form.get("currency") or client.currency), discount_pct=discount_pct,
            teacher=teacher or student.teacher, user=user, rationale=rationale or None, scholarship=scholarship,
            start_date=parse_date(form.get("start_date"), date.today()),
            sessions_per_week=parse_int(form.get("sessions_per_week")) or None)
    except ValueError as exc:
        return _err("/finance/subscriptions/new", exc)
    db.commit()
    msg = (f"Subscription {sub.subscription_code} created."
           if sub.status == "active" else
           f"Subscription {sub.subscription_code} created and is awaiting discount approval.")
    return redirect(f"/finance/subscriptions/{sub.id}", msg, "success" if sub.status == "active" else "warning")


@router.get("/subscriptions/{id}", include_in_schema=False)
def subscription_detail(id: int, request: Request, tab: str = "overview", db: Session = Depends(get_db),
                        user: User = Depends(require("subscriptions.view"))):
    s = _sub(db, id)
    cost = float(s.teacher_cost_base or 0)
    floor = billing.teacher_cost_floor(db, s.teacher, s.sessions_per_week)
    ctx = {"user": user, "s": s, "tab": tab, "tabs": [(k, l, f"/finance/subscriptions/{s.id}?tab={k}") for k, l in SUB_TABS],
           "cost": cost, "floor": floor, "margin_pct": billing.margin_pct(float(s.price_in_base or 0), cost),
           "base": billing.base_currency(db), "balance": billing.client_balance(db, s.client) if s.client else 0.0,
           "discount_requests": db.query(DiscountRequest).filter(DiscountRequest.subscription_id == s.id).order_by(DiscountRequest.id.desc()).all(),
           "packages": db.query(Package).filter(Package.is_active.is_(True)).order_by(Package.name).all(),
           "today_d": date.today()}
    if tab == "billing":
        ctx["invoices"] = db.query(Invoice).filter(Invoice.subscription_id == s.id).order_by(Invoice.issue_date.desc()).all()
        ctx["payments"] = (db.query(Payment).filter(Payment.client_id == s.client_id)
                           .order_by(Payment.received_at.desc()).limit(25).all())
    elif tab == "audit":
        ctx["events"] = (db.query(AuditEvent).filter(AuditEvent.entity_type == "Subscription", AuditEvent.entity_id == s.id)
                         .order_by(AuditEvent.created_at.desc()).limit(100).all())
    return render(request, "finance/subscription_detail.html", ctx)


@router.post("/subscriptions/{id}/activate", include_in_schema=False)
async def subscription_activate(id: int, request: Request, db: Session = Depends(get_db),
                                user: User = Depends(require("subscriptions.update"))):
    s = _sub(db, id)
    try:
        billing.activate_subscription(db, s, user)
    except ValueError as exc:
        return _err(f"/finance/subscriptions/{id}", exc)
    db.commit()
    return redirect(f"/finance/subscriptions/{id}", f"{s.subscription_code} is active.")


@router.post("/subscriptions/{id}/freeze", include_in_schema=False)
async def subscription_freeze(id: int, request: Request, db: Session = Depends(get_db),
                              user: User = Depends(require("subscriptions.update"))):
    s = _sub(db, id)
    form = await request.form()
    try:
        billing.freeze_subscription(db, s, parse_date(form.get("freeze_start"), date.today()),
                                    parse_date(form.get("freeze_end")), user,
                                    (form.get("reason") or form.get("rationale") or "").strip())
    except ValueError as exc:
        return _err(f"/finance/subscriptions/{id}", exc)
    db.commit()
    return redirect(f"/finance/subscriptions/{id}", "Subscription frozen and the student marked frozen.", "warning")


@router.post("/subscriptions/{id}/unfreeze", include_in_schema=False)
async def subscription_unfreeze(id: int, request: Request, db: Session = Depends(get_db),
                                user: User = Depends(require("subscriptions.update"))):
    s = _sub(db, id)
    form = await request.form()
    try:
        billing.unfreeze_subscription(db, s, user, form.get("reason"))
    except ValueError as exc:
        return _err(f"/finance/subscriptions/{id}", exc)
    db.commit()
    return redirect(f"/finance/subscriptions/{id}", "Subscription resumed.")


@router.post("/subscriptions/{id}/cancel", include_in_schema=False)
async def subscription_cancel(id: int, request: Request, db: Session = Depends(get_db),
                              user: User = Depends(require("subscriptions.update"))):
    s = _sub(db, id)
    form = await request.form()
    try:
        billing.cancel_subscription(db, s, user, (form.get("reason") or form.get("rationale") or "").strip())
    except ValueError as exc:
        return _err(f"/finance/subscriptions/{id}", exc)
    db.commit()
    return redirect(f"/finance/subscriptions/{id}", "Subscription cancelled and the student marked cancelled.", "warning")


@router.post("/subscriptions/{id}/renew", include_in_schema=False)
async def subscription_renew(id: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("subscriptions.update"))):
    s = _sub(db, id)
    try:
        billing.renew_subscription(db, s, user)
    except ValueError as exc:
        return _err(f"/finance/subscriptions/{id}", exc)
    db.commit()
    return redirect(f"/finance/subscriptions/{id}", f"Renewed; next billing {s.next_billing_date}.")


@router.post("/subscriptions/{id}/change-package", include_in_schema=False)
async def subscription_change_package(id: int, request: Request, db: Session = Depends(get_db),
                                      user: User = Depends(require("subscriptions.update"))):
    s = _sub(db, id)
    form = await request.form()
    package = db.query(Package).get(parse_int(form.get("package_id"), 0) or 0)
    if not package:
        return redirect(f"/finance/subscriptions/{id}", "Choose a package.", "error")
    try:
        billing.change_package(db, s, package, user, (form.get("rationale") or "").strip(),
                               price=parse_float(form.get("price"), float(package.price)),
                               sessions_per_week=parse_int(form.get("sessions_per_week")) or None)
    except ValueError as exc:
        return _err(f"/finance/subscriptions/{id}", exc)
    db.commit()
    return redirect(f"/finance/subscriptions/{id}", "Package changed.")


@router.post("/subscriptions/{id}/invoice", include_in_schema=False)
async def subscription_invoice_now(id: int, request: Request, db: Session = Depends(get_db),
                                   user: User = Depends(require("billing.add"))):
    s = _sub(db, id)
    form = await request.form()
    start = parse_date(form.get("period_start"), s.next_billing_date or date.today())
    end = parse_date(form.get("period_end"), billing.add_months(start, 1) - timedelta(days=1))
    try:
        inv = billing.generate_invoice(db, s, start, end, user=user)
    except ValueError as exc:
        return _err(f"/finance/subscriptions/{id}", exc)
    db.commit()
    return redirect(f"/finance/invoices/{inv.id}", f"Invoice {inv.invoice_number} generated.")


# ============================================================================ Module 45 — discounts & scholarships
@router.get("/discounts", include_in_schema=False)
def discounts_queue(request: Request, status: str = "pending", tier: str | None = None, db: Session = Depends(get_db),
                    user: User = Depends(require("discounts.view"))):
    tiers = ["manager"] if (rbac.has_permission(user, "discounts.approve") and not rbac.is_ceo(user)) else ["manager", "ceo"]
    if tier is None:  # default the queue to the tier this approver can actually decide
        tier = "manager" if tiers == ["manager"] else ""
    query = db.query(DiscountRequest)
    if status:
        query = query.filter(DiscountRequest.status == status)
    if tier:
        query = query.filter(DiscountRequest.approver_tier == tier)
    rows = query.order_by(DiscountRequest.created_at.desc()).limit(200).all()
    counts = dict(db.query(DiscountRequest.status, func.count(DiscountRequest.id)).group_by(DiscountRequest.status).all())
    tier_counts = dict(db.query(DiscountRequest.approver_tier, func.count(DiscountRequest.id))
                       .filter(DiscountRequest.status == "pending").group_by(DiscountRequest.approver_tier).all())
    return render(request, "finance/discounts_queue.html", {
        "user": user, "rows": rows, "status": status, "my_tiers": tiers, "counts": counts, "tier": tier,
        "tier_counts": tier_counts,
        "thresholds": billing.discount_thresholds(db), "can_approve": rbac.has_permission(user, "discounts.approve"),
        "is_ceo": rbac.is_ceo(user), "tab": "queue"})


@router.post("/discounts/{id}/decide", include_in_schema=False)
async def discount_decide(id: int, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("discounts.approve"))):
    req = db.query(DiscountRequest).get(id)
    if not req:
        raise HTTPException(404, "Discount request not found")
    form = await request.form()
    approve = (form.get("decision") or "approve") == "approve"
    note = (form.get("note") or form.get("rationale") or "").strip()
    try:
        billing.approve_discount(db, req, user, approve, note)
    except ValueError as exc:
        return _err("/finance/discounts", exc)
    db.commit()
    return redirect("/finance/discounts", f"Discount {req.status}.", "success" if approve else "warning")


@router.get("/discounts/register", include_in_schema=False)
def discount_register(request: Request, weeks: int = 12, db: Session = Depends(get_db),
                      user: User = Depends(require("discounts.view"))):
    groups = billing.discount_register(db, weeks)
    return render(request, "finance/discounts_register.html", {
        "user": user, "groups": groups, "weeks": weeks, "tab": "register", "base": billing.base_currency(db),
        "thresholds": billing.discount_thresholds(db),
        "labels": [g["week"] for g in reversed(groups)], "values": [g["value"] for g in reversed(groups)]})


@router.get("/discounts/register.csv", include_in_schema=False)
def discount_register_csv(weeks: int = 26, db: Session = Depends(get_db),
                          user: User = Depends(require("discounts.export"))):
    rows = [["ISO week", "Requests", "Average %", "Value (base)", "Approved", "Rejected", "Pending"]]
    for g in billing.discount_register(db, weeks):
        rows.append([g["week"], g["count"], g["avg_pct"], g["value"], g["approved"], g["rejected"], g["pending"]])
    return _csv(rows, "discount-register.csv")


@router.get("/discounts/scholarships", include_in_schema=False)
def scholarships_list(request: Request, status: str = "", db: Session = Depends(get_db),
                      user: User = Depends(require("scholarships.view"))):
    query = db.query(Scholarship)
    if status:
        query = query.filter(Scholarship.status == status)
    rows = query.order_by(Scholarship.created_at.desc()).all()
    counts = dict(db.query(Scholarship.status, func.count(Scholarship.id)).group_by(Scholarship.status).all())
    awarded = round(sum(billing.convert_to_base(db, float(s.monthly_amount or 0), s.currency)
                        for s in rows if s.status == "approved"), 2)
    return render(request, "finance/scholarships.html", {
        "user": user, "rows": rows, "status": status, "counts": counts, "tab": "scholarships",
        "awarded_base": awarded, "base": billing.base_currency(db),
        "types": billing.SCHOLARSHIP_TYPES, "can_approve": rbac.has_permission(user, "scholarships.approve"),
        "clients": [(c.id, f"{c.client_code} — {c.full_name}") for c in db.query(Client).order_by(Client.client_code).all()],
        "students": [(s.id, f"{s.student_code} — {s.full_name}") for s in db.query(Student).order_by(Student.student_code).all()],
        "currencies": [c.code for c in db.query(Currency).order_by(Currency.code).all()]})


@router.post("/discounts/scholarships/new", include_in_schema=False)
async def scholarship_create(request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("scholarships.add"))):
    form = await request.form()
    client = db.query(Client).get(parse_int(form.get("client_id"), 0) or 0)
    if not client:
        return redirect("/finance/discounts/scholarships", "Select a family.", "error")
    student = db.query(Student).get(parse_int(form.get("student_id"), 0) or 0) if form.get("student_id") else None
    try:
        s = billing.request_scholarship(db, client, student, form.get("scholarship_type") or "need_based",
                                        parse_float(form.get("coverage_pct"), 0), parse_float(form.get("monthly_amount"), 0),
                                        form.get("currency") or client.currency, (form.get("reason") or "").strip(), user,
                                        parse_date(form.get("start_date"), date.today()), parse_date(form.get("end_date")))
    except ValueError as exc:
        return _err("/finance/discounts/scholarships", exc)
    db.commit()
    return redirect("/finance/discounts/scholarships", f"Support request recorded for {client.client_code}.")


@router.post("/discounts/scholarships/{id}/decide", include_in_schema=False)
async def scholarship_decide(id: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("scholarships.approve"))):
    s = db.query(Scholarship).get(id)
    if not s:
        raise HTTPException(404, "Scholarship not found")
    form = await request.form()
    try:
        billing.decide_scholarship(db, s, user, (form.get("decision") or "approve") == "approve",
                                   (form.get("note") or form.get("rationale") or "").strip())
    except ValueError as exc:
        return _err("/finance/discounts/scholarships", exc)
    db.commit()
    return redirect("/finance/discounts/scholarships", f"Scholarship {s.status}.")


# ============================================================================ Module 18 — invoices
@router.get("/invoices", include_in_schema=False)
def invoices_list(request: Request, page: int = 1, q: str = "", status: str = "", currency: str = "",
                  overdue_only: str = "", db: Session = Depends(get_db), user: User = Depends(require("billing.view"))):
    query = db.query(Invoice)
    if q:
        like = f"%{q}%"
        query = (query.join(Client, Invoice.client_id == Client.id)
                 .filter(or_(Invoice.invoice_number.ilike(like), Client.full_name.ilike(like), Client.client_code.ilike(like))))
    if status:
        query = query.filter(Invoice.status == status)
    if currency:
        query = query.filter(Invoice.currency == currency)
    if overdue_only:
        query = query.filter(Invoice.status == "overdue")
    pg = paginate(query.order_by(Invoice.issue_date.desc(), Invoice.id.desc()), page, 25)
    base = f"/finance/invoices?q={q}&status={status}&currency={currency}"
    return render(request, "finance/invoices_list.html", {
        "user": user, "page": pg, "q": q, "status": status, "currency": currency, "base_url": base,
        "stats": billing.invoice_stats(db), "statuses": billing.INVOICE_STATUSES,
        "currencies": [c.code for c in db.query(Currency).order_by(Currency.code).all()],
        "periods": _period_options(6), "today_d": date.today()})


@router.post("/invoices/generate", include_in_schema=False)
async def invoices_generate(request: Request, db: Session = Depends(get_db), user: User = Depends(require("billing.add"))):
    form = await request.form()
    period = form.get("period") or month_key()
    start, end = month_bounds(period)
    subs = (db.query(Subscription).filter(Subscription.status == "active",
                                          Subscription.next_billing_date.isnot(None),
                                          Subscription.next_billing_date <= end).all())
    created = skipped = failed = 0
    for s in subs:
        if billing.invoice_for_period(db, s, start):
            skipped += 1
            continue
        try:
            billing.generate_invoice(db, s, start, end, user=user)
            created += 1
        except ValueError:
            failed += 1
    db.commit()
    return redirect("/finance/invoices",
                    f"{period}: {created} invoice(s) generated, {skipped} already existed, {failed} skipped.",
                    "success" if created else "info")


@router.get("/invoices/{id}", include_in_schema=False)
def invoice_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("billing.view"))):
    inv = _invoice(db, id)
    return render(request, "finance/invoice_detail.html", {
        "user": user, "inv": inv, "payments": inv.payments,
        "ledger": db.query(LedgerEntry).filter(LedgerEntry.reference_type == "invoice", LedgerEntry.reference_id == inv.id).all(),
        "balance": billing.client_balance(db, inv.client), "methods": billing.PAYMENT_METHODS,
        "gateways": billing.GATEWAYS, "base": billing.base_currency(db), "today_d": date.today(),
        "events": db.query(AuditEvent).filter(AuditEvent.entity_type == "Invoice", AuditEvent.entity_id == inv.id)
                    .order_by(AuditEvent.created_at.desc()).limit(20).all()})


@router.get("/invoices/{id}/pdf", include_in_schema=False)
def invoice_pdf(id: int, db: Session = Depends(get_db), user: User = Depends(require("billing.view"))):
    inv = _invoice(db, id)
    path = billing.INVOICE_DIR / f"{inv.invoice_number}.pdf"
    if not path.exists():
        billing.generate_invoice_pdf(db, inv)
        db.commit()
    return FileResponse(str(path), media_type="application/pdf", filename=f"{inv.invoice_number}.pdf")


@router.post("/invoices/{id}/send", include_in_schema=False)
async def invoice_send(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("billing.update"))):
    inv = _invoice(db, id)
    try:
        billing.send_invoice(db, inv, user)
    except ValueError as exc:
        return _err(f"/finance/invoices/{id}", exc)
    db.commit()
    return redirect(f"/finance/invoices/{id}", f"{inv.invoice_number} sent to {inv.client.full_name}.")


@router.post("/invoices/{id}/reminder", include_in_schema=False)
async def invoice_reminder(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("billing.update"))):
    inv = _invoice(db, id)
    try:
        billing.send_reminder(db, inv, user)
    except ValueError as exc:
        return _err(f"/finance/invoices/{id}", exc)
    db.commit()
    return redirect(f"/finance/invoices/{id}", f"Reminder #{inv.reminder_count} sent by WhatsApp.")


@router.post("/invoices/{id}/overdue", include_in_schema=False)
async def invoice_overdue(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("billing.update"))):
    inv = _invoice(db, id)
    try:
        billing.mark_overdue(db, inv, user)
    except ValueError as exc:
        return _err(f"/finance/invoices/{id}", exc)
    db.commit()
    return redirect(f"/finance/invoices/{id}", f"{inv.invoice_number} marked overdue.", "warning")


@router.post("/invoices/{id}/void", include_in_schema=False)
async def invoice_void(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("billing.delete"))):
    inv = _invoice(db, id)
    form = await request.form()
    try:
        billing.void_invoice(db, inv, user, (form.get("rationale") or form.get("reason") or "").strip())
    except ValueError as exc:
        return _err(f"/finance/invoices/{id}", exc)
    db.commit()
    return redirect(f"/finance/invoices/{id}", f"{inv.invoice_number} voided.", "warning")


@router.post("/invoices/{id}/remarks", include_in_schema=False)
async def invoice_remarks(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("billing.update"))):
    inv = _invoice(db, id)
    form = await request.form()
    before = {"remarks": inv.remarks}
    inv.remarks = (form.get("remarks") or "").strip() or None
    log_action(db, user, "update", "billing", entity=inv, description=f"Remarks updated on {inv.invoice_number}",
               before=before, after={"remarks": inv.remarks}, request=request)
    db.commit()
    return redirect(f"/finance/invoices/{id}", "Remarks saved.")


@router.post("/invoices/{id}/payment", include_in_schema=False)
async def invoice_payment(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payments.add"))):
    inv = _invoice(db, id)
    form = await request.form()
    try:
        pay = billing.record_payment(db, inv.client, parse_float(form.get("amount"), 0), form.get("currency") or inv.currency,
                                     form.get("method") or "bank_transfer", form.get("reference"), user=user, invoice=inv,
                                     gateway=form.get("gateway") or None,
                                     received_at=datetime.combine(parse_date(form.get("received_at"), date.today()), datetime.min.time()),
                                     status=form.get("status") or "completed")
    except ValueError as exc:
        return _err(f"/finance/invoices/{id}", exc)
    db.commit()
    return redirect(f"/finance/invoices/{id}", f"Payment {pay.payment_number} recorded.")


# ============================================================================ payments
@router.get("/payments", include_in_schema=False)
def payments_list(request: Request, page: int = 1, q: str = "", method: str = "", gateway: str = "", status: str = "",
                  reconciled: str = "", date_from: str = "", date_to: str = "", db: Session = Depends(get_db),
                  user: User = Depends(require("payments.view"))):
    query = db.query(Payment)
    if q:
        like = f"%{q}%"
        query = (query.join(Client, Payment.client_id == Client.id)
                 .filter(or_(Payment.payment_number.ilike(like), Payment.reference.ilike(like),
                             Client.full_name.ilike(like), Client.client_code.ilike(like))))
    if method:
        query = query.filter(Payment.method == method)
    if gateway:
        query = query.filter(Payment.gateway == gateway)
    if status:
        query = query.filter(Payment.status == status)
    if reconciled:
        query = query.filter(Payment.reconciled.is_(reconciled == "yes"))
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(Payment.received_at >= datetime.combine(df, datetime.min.time()))
    if dt:
        query = query.filter(Payment.received_at <= datetime.combine(dt, datetime.max.time()))
    pg = paginate(query.order_by(Payment.received_at.desc(), Payment.id.desc()), page, 25)
    base = f"/finance/payments?q={q}&method={method}&gateway={gateway}&status={status}&reconciled={reconciled}"
    return render(request, "finance/payments_list.html", {
        "user": user, "page": pg, "q": q, "method": method, "gateway": gateway, "status": status,
        "reconciled": reconciled, "date_from": date_from, "date_to": date_to, "base_url": base,
        "stats": billing.payment_stats(db), "methods": billing.PAYMENT_METHODS, "gateways": billing.GATEWAYS,
        "statuses": ["pending", "completed", "failed", "refunded"]})


@router.get("/payments/new", include_in_schema=False)
def payment_new(request: Request, client_id: str = "", invoice_id: str = "", db: Session = Depends(get_db),
                user: User = Depends(require("payments.add"))):
    return render(request, "finance/payment_form.html", {
        "user": user, "client_id": client_id, "invoice_id": invoice_id,
        "clients": [(c.id, f"{c.client_code} — {c.full_name}") for c in db.query(Client).order_by(Client.client_code).all()],
        "invoices": db.query(Invoice).filter(Invoice.status.in_(["sent", "partial", "overdue"])).order_by(Invoice.due_date).all(),
        "methods": billing.PAYMENT_METHODS, "gateways": billing.GATEWAYS,
        "currencies": [c.code for c in db.query(Currency).order_by(Currency.code).all()], "today_d": date.today()})


@router.post("/payments/new", include_in_schema=False)
async def payment_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payments.add"))):
    form = await request.form()
    invoice = db.query(Invoice).get(parse_int(form.get("invoice_id"), 0) or 0) if form.get("invoice_id") else None
    client = db.query(Client).get(parse_int(form.get("client_id"), 0) or 0) if form.get("client_id") else (invoice.client if invoice else None)
    if not client:
        return redirect("/finance/payments/new", "Select the family that paid.", "error")
    try:
        pay = billing.record_payment(db, client, parse_float(form.get("amount"), 0),
                                     form.get("currency") or client.currency, form.get("method") or "bank_transfer",
                                     form.get("reference"), user=user, invoice=invoice, gateway=form.get("gateway") or None,
                                     received_at=datetime.combine(parse_date(form.get("received_at"), date.today()), datetime.min.time()),
                                     status=form.get("status") or "completed", notes=form.get("notes"))
    except ValueError as exc:
        return _err("/finance/payments/new", exc)
    db.commit()
    return redirect(f"/finance/payments/{pay.id}", f"Payment {pay.payment_number} recorded.")


@router.get("/payments/reconciliation", include_in_schema=False)
def payments_reconciliation(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payments.view"))):
    rows = (db.query(Payment).filter(Payment.status == "completed", Payment.reconciled.is_(False))
            .order_by(Payment.received_at).limit(200).all())
    totals = billing.daily_totals_by_method(db, 14)
    return render(request, "finance/reconciliation.html", {
        "user": user, "rows": rows, "totals": totals, "methods": billing.PAYMENT_METHODS,
        "base": billing.base_currency(db), "stats": billing.payment_stats(db),
        "unreconciled_base": round(sum(float(p.amount_in_base or 0) for p in rows), 2)})


@router.post("/payments/reconcile", include_in_schema=False)
async def payments_reconcile(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payments.update"))):
    form = await request.form()
    ids = [parse_int(v) for v in form.getlist("payment_ids")]
    reference = (form.get("bank_reference") or "").strip()
    done = 0
    for pid in ids:
        p = db.query(Payment).get(pid) if pid else None
        if p and not p.reconciled:
            billing.reconcile_payment(db, p, user, reference)
            done += 1
    db.commit()
    return redirect("/finance/payments/reconciliation", f"{done} payment(s) reconciled." if done else "Nothing selected.",
                    "success" if done else "info")


@router.get("/payments/failed", include_in_schema=False)
def payments_failed(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payments.view"))):
    from app.models.core import RiskAlert
    rows = db.query(Payment).filter(Payment.status == "failed").order_by(Payment.received_at.desc()).all()
    alerts = (db.query(RiskAlert).filter(RiskAlert.alert_type == "payment_failed")
              .order_by(RiskAlert.created_at.desc()).limit(50).all())
    return render(request, "finance/payments_failed.html", {"user": user, "rows": rows, "alerts": alerts})


@router.get("/payments/{id}", include_in_schema=False)
def payment_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payments.view"))):
    p = _payment(db, id)
    return render(request, "finance/payment_detail.html", {
        "user": user, "p": p, "receipt": p.receipt, "base": billing.base_currency(db),
        "journal": db.query(JournalEntry).filter(JournalEntry.reference_type.in_(["payment", "refund"]),
                                                 JournalEntry.reference_id == p.id).all(),
        "ledger": db.query(LedgerEntry).filter(LedgerEntry.reference_type == "payment", LedgerEntry.reference_id == p.id).all(),
        "events": db.query(AuditEvent).filter(AuditEvent.entity_type == "Payment", AuditEvent.entity_id == p.id)
                    .order_by(AuditEvent.created_at.desc()).limit(20).all()})


@router.get("/payments/{id}/receipt", include_in_schema=False)
def payment_receipt(id: int, db: Session = Depends(get_db), user: User = Depends(require("payments.view"))):
    p = _payment(db, id)
    if not p.receipt:
        raise HTTPException(404, "No receipt was issued for this payment")
    path = billing.RECEIPT_DIR / f"{p.receipt.receipt_number}.pdf"
    if not path.exists():
        billing.generate_receipt_pdf(db, p.receipt)
        db.commit()
    return FileResponse(str(path), media_type="application/pdf", filename=f"{p.receipt.receipt_number}.pdf")


@router.post("/payments/{id}/refund", include_in_schema=False)
async def payment_refund(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payments.approve"))):
    p = _payment(db, id)
    form = await request.form()
    try:
        billing.refund_payment(db, p, parse_float(form.get("amount"), float(p.amount)), user,
                               (form.get("rationale") or form.get("reason") or "").strip())
    except ValueError as exc:
        return _err(f"/finance/payments/{id}", exc)
    db.commit()
    return redirect(f"/finance/payments/{id}", "Refund processed and posted to the ledger and journal.", "warning")


@router.post("/payments/{id}/reconcile", include_in_schema=False)
async def payment_reconcile_one(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payments.update"))):
    p = _payment(db, id)
    form = await request.form()
    try:
        billing.reconcile_payment(db, p, user, (form.get("bank_reference") or "").strip())
    except ValueError as exc:
        return _err(f"/finance/payments/{id}", exc)
    db.commit()
    return redirect(f"/finance/payments/{id}", "Payment reconciled.")


@router.post("/payments/{id}/fail", include_in_schema=False)
async def payment_fail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payments.update"))):
    p = _payment(db, id)
    form = await request.form()
    billing.flag_failed_payment(db, p, user, (form.get("reason") or "Marked failed manually").strip())
    db.commit()
    return redirect(f"/finance/payments/{id}", "Payment flagged as failed; the billing rep has been alerted.", "warning")


# ============================================================================ client ledger
@router.get("/ledger", include_in_schema=False)
def ledger_index(request: Request, q: str = "", db: Session = Depends(get_db), user: User = Depends(require("ledger.view"))):
    rows = billing.client_balances(db, q)
    owing = [r for r in rows if r["balance"] > 0.01]
    return render(request, "finance/ledger_list.html", {
        "user": user, "rows": rows, "q": q, "base": billing.base_currency(db),
        "totals": {"owing": len(owing), "credit": len([r for r in rows if r["balance"] < -0.01]),
                   "owed_base": round(sum(r["balance"] * billing.get_rate(db, r["client"].currency) for r in owing), 2),
                   "overdue": sum(r["overdue"] for r in rows)}})


@router.get("/ledger/credits", include_in_schema=False)
def ledger_credits(request: Request, db: Session = Depends(get_db), user: User = Depends(require("ledger.view"))):
    data = billing.credits_report(db)
    referrals = db.query(Referral).filter(Referral.status == "credited").order_by(Referral.qualified_at.desc()).limit(50).all()
    return render(request, "finance/credits_report.html", {"user": user, **data, "referrals": referrals})


@router.get("/ledger/{client_id}", include_in_schema=False)
def ledger_statement(client_id: int, request: Request, entry_type: str = "", date_from: str = "", date_to: str = "",
                     db: Session = Depends(get_db), user: User = Depends(require("ledger.view"))):
    c = _client(db, client_id)
    entries = billing.client_statement(db, c)
    df, dt = parse_date(date_from), parse_date(date_to)
    shown = [e for e in entries
             if (not entry_type or e.entry_type == entry_type) and (not df or e.entry_date >= df) and (not dt or e.entry_date <= dt)]
    return render(request, "finance/ledger_statement.html", {
        "user": user, "c": c, "entries": shown, "all_count": len(entries), "entry_type": entry_type,
        "date_from": date_from, "date_to": date_to, "types": billing.LEDGER_TYPES,
        "balance": billing.client_balance(db, c), "credit": billing.available_credit(db, c),
        "currencies": [x.code for x in db.query(Currency).order_by(Currency.code).all()],
        "invoices": db.query(Invoice).filter(Invoice.client_id == c.id).order_by(Invoice.issue_date.desc()).limit(20).all(),
        "subscriptions": db.query(Subscription).filter(Subscription.client_id == c.id).all()})


@router.get("/ledger/{client_id}/export.csv", include_in_schema=False)
def ledger_statement_csv(client_id: int, db: Session = Depends(get_db), user: User = Depends(require("ledger.export"))):
    c = _client(db, client_id)
    rows = [["Date", "Type", "Description", "Debit", "Credit", "Currency", "Balance after"]]
    for e in billing.client_statement(db, c):
        rows.append([e.entry_date.isoformat(), e.entry_type, e.description, float(e.debit), float(e.credit),
                     e.currency, float(e.balance_after)])
    return _csv(rows, f"statement-{c.client_code}.csv")


@router.post("/ledger/{client_id}/adjustment", include_in_schema=False)
async def ledger_adjustment(client_id: int, request: Request, db: Session = Depends(get_db),
                            user: User = Depends(require("ledger.update"))):
    c = _client(db, client_id)
    form = await request.form()
    kind = form.get("kind") or "credit"
    amount = parse_float(form.get("amount"), 0)
    description = (form.get("description") or "").strip()
    rationale = (form.get("rationale") or "").strip()
    try:
        if kind == "credit":
            billing.post_credit(db, c, amount, form.get("currency") or c.currency, description or "Goodwill credit",
                                reference_type="manual", user=user)
        else:
            billing.post_adjustment(db, c, amount, form.get("currency") or c.currency, description or "Manual adjustment",
                                    user, rationale, direction="debit")
    except ValueError as exc:
        return _err(f"/finance/ledger/{client_id}", exc)
    db.commit()
    return redirect(f"/finance/ledger/{client_id}", f"{kind.title()} of {amount:,.2f} posted to the ledger.")


# ============================================================================ Module 19 — accounts
@router.get("/accounts", include_in_schema=False)
def accounts_chart(request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.view"))):
    accounting.ensure_chart_of_accounts(db)
    db.commit()
    accounts = db.query(Account).order_by(Account.code).all()
    balances = dict(db.query(JournalLine.account_id,
                             func.coalesce(func.sum(JournalLine.debit), 0) - func.coalesce(func.sum(JournalLine.credit), 0))
                    .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                    .filter(JournalEntry.status == "posted").group_by(JournalLine.account_id).all())
    tree = []
    for a in accounts:
        if a.parent_id is None:
            tree.append((a, [x for x in accounts if x.parent_id == a.id]))
    return render(request, "finance/accounts_chart.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "chart", "accounts": accounts, "tree": tree,
        "balances": {k: round(float(v), 2) for k, v in balances.items()}, "types": accounting.ACCOUNT_TYPES,
        "base": billing.base_currency(db),
        "parent_options": [(a.id, f"{a.code} {a.name}") for a in accounts]})


@router.post("/accounts/new", include_in_schema=False)
async def account_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.add"))):
    form = await request.form()
    code = (form.get("code") or "").strip()
    name = (form.get("name") or "").strip()
    if not code or not name:
        return redirect("/finance/accounts", "Account code and name are required.", "error")
    if db.query(Account).filter(Account.code == code).first():
        return redirect("/finance/accounts", f"Account {code} already exists.", "error")
    a = Account(code=code, name=name, account_type=form.get("account_type") or "expense",
                parent_id=parse_int(form.get("parent_id")) or None, description=form.get("description"), is_active=True)
    db.add(a)
    db.flush()
    log_action(db, user, "create", "accounts", entity=a, description=f"Account {code} {name} created", request=request)
    db.commit()
    return redirect("/finance/accounts", f"Account {code} added.")


@router.post("/accounts/{id}/edit", include_in_schema=False)
async def account_edit(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.update"))):
    a = db.query(Account).get(id)
    if not a:
        raise HTTPException(404, "Account not found")
    form = await request.form()
    before = snapshot(a)
    a.name = (form.get("name") or a.name).strip()
    a.account_type = form.get("account_type") or a.account_type
    a.description = form.get("description")
    a.is_active = parse_bool(form.get("is_active"))
    log_action(db, user, "update", "accounts", entity=a, description=f"Account {a.code} updated", before=before,
               after=snapshot(a), request=request)
    db.commit()
    return redirect("/finance/accounts", f"Account {a.code} updated.")


@router.get("/accounts/journal", include_in_schema=False)
def journal_list(request: Request, page: int = 1, q: str = "", period: str = "", db: Session = Depends(get_db),
                 user: User = Depends(require("accounts.view"))):
    query = db.query(JournalEntry)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(JournalEntry.entry_number.ilike(like), JournalEntry.description.ilike(like)))
    if period:
        query = query.filter(JournalEntry.period == period)
    pg = paginate(query.order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc()), page, 25)
    return render(request, "finance/journal_list.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "journal", "page": pg, "q": q, "period": period,
        "periods": _period_options(12), "base_url": f"/finance/accounts/journal?q={q}&period={period}",
        "accounts": db.query(Account).filter(Account.is_active.is_(True)).order_by(Account.code).all(),
        "base": billing.base_currency(db), "today_d": date.today()})


@router.post("/accounts/journal/new", include_in_schema=False)
async def journal_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.add"))):
    form = await request.form()
    codes = form.getlist("account_code")
    debits = form.getlist("debit")
    credits = form.getlist("credit")
    lines = []
    for i, code in enumerate(codes):
        if not code:
            continue
        d = parse_float(debits[i] if i < len(debits) else 0, 0)
        c = parse_float(credits[i] if i < len(credits) else 0, 0)
        if d or c:
            lines.append((code, d, c))
    try:
        je = accounting.post_journal(db, (form.get("description") or "Manual journal").strip(), lines,
                                     reference_type="manual", entry_date=parse_date(form.get("entry_date"), date.today()),
                                     user=user)
    except ValueError as exc:
        return _err("/finance/accounts/journal", exc)
    log_action(db, user, "create", "accounts", entity=je, description=f"Manual journal {je.entry_number} posted",
               rationale=form.get("rationale"), request=request)
    db.commit()
    return redirect("/finance/accounts/journal", f"Journal {je.entry_number} posted.")


@router.get("/accounts/journal/{id}", include_in_schema=False)
def journal_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.view"))):
    je = db.query(JournalEntry).get(id)
    if not je:
        raise HTTPException(404, "Journal entry not found")
    return render(request, "finance/journal_detail.html", {"user": user, "je": je, "base": billing.base_currency(db),
                                                           "closed": accounting.period_is_closed(db, je.period or "")})


@router.post("/accounts/journal/{id}/reverse", include_in_schema=False)
async def journal_reverse(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.approve"))):
    je = db.query(JournalEntry).get(id)
    if not je:
        raise HTTPException(404, "Journal entry not found")
    form = await request.form()
    rationale = (form.get("rationale") or "").strip()
    if not rationale:
        return redirect(f"/finance/accounts/journal/{id}", "A rationale is required to reverse a journal entry.", "error")
    try:
        rev = accounting.reverse_journal(db, je, user, rationale)
    except ValueError as exc:
        return _err(f"/finance/accounts/journal/{id}", exc)
    db.commit()
    return redirect(f"/finance/accounts/journal/{rev.id}", f"{je.entry_number} reversed by {rev.entry_number}.", "warning")


@router.get("/accounts/pnl", include_in_schema=False)
def accounts_pnl(request: Request, scope: str = "month", period: str = "", db: Session = Depends(get_db),
                 user: User = Depends(require("accounts.view"))):
    period = period or month_key()
    today = date.today()
    if scope == "ytd":
        start, end = date(today.year, 1, 1), today
        label = f"Year to date {today.year}"
    elif scope == "quarter":
        q = (today.month - 1) // 3
        start = date(today.year, q * 3 + 1, 1)
        end = today
        label = f"Q{q + 1} {today.year}"
    else:
        start, end = month_bounds(period)
        label = start.strftime("%B %Y")
    pl = accounting.profit_and_loss(db, start, end)
    months = []
    for i in range(5, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        s, e = month_bounds(f"{y:04d}-{m:02d}")
        p = accounting.profit_and_loss(db, s, e)
        months.append({"label": s.strftime("%b"), "income": p["total_income"], "expenses": p["total_expenses"], "net": p["net"]})
    return render(request, "finance/pnl.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "pnl", "pl": pl, "scope": scope, "period": period, "label": label,
        "periods": _period_options(12), "months": months, "base": billing.base_currency(db)})


@router.get("/accounts/cash-flow", include_in_schema=False)
def accounts_cash_flow(request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.view"))):
    rows = accounting.cash_flow(db, 6)
    return render(request, "finance/cash_flow.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "cash", "rows": rows, "base": billing.base_currency(db),
        "labels": [r["label"] for r in rows], "inflow": [r["inflow"] for r in rows],
        "outflow": [r["outflow"] for r in rows], "net": [r["net"] for r in rows]})


@router.get("/accounts/aging", include_in_schema=False)
def accounts_aging(request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.view"))):
    data = accounting.receivables_aging(db)
    return render(request, "finance/aging.html", {"user": user, "tabs": ACCOUNT_TABS, "tab": "aging", "data": data,
                                                  "base": data["currency"],
                                                  "labels": [b["label"] for b in data["buckets"].values()],
                                                  "values": [b["amount"] for b in data["buckets"].values()]})


@router.get("/accounts/payables", include_in_schema=False)
def accounts_payables(request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.view"))):
    rows = db.query(Expense).filter(Expense.status == "approved").order_by(Expense.expense_date).all()
    return render(request, "finance/payables.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "payables", "rows": rows, "base": billing.base_currency(db),
        "total": round(sum(float(e.amount_in_base or 0) for e in rows), 2)})


@router.get("/accounts/budget", include_in_schema=False)
def accounts_budget(request: Request, period: str = "", db: Session = Depends(get_db),
                    user: User = Depends(require("accounts.view"))):
    period = period or month_key()
    rows = accounting.budget_vs_actual(db, period)
    return render(request, "finance/budget.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "budget", "rows": rows, "period": period,
        "periods": _period_options(12), "base": billing.base_currency(db),
        "categories": list(accounting.EXPENSE_CATEGORIES.keys()),
        "departments": [(d.id, d.name) for d in db.query(Department).order_by(Department.name).all()],
        "totals": {"budgeted": round(sum(r["budgeted"] for r in rows), 2), "actual": round(sum(r["actual"] for r in rows), 2),
                   "variance": round(sum(r["variance"] for r in rows), 2)},
        "labels": [r["category"].replace("_", " ").title() for r in rows],
        "budgeted": [r["budgeted"] for r in rows], "actual": [r["actual"] for r in rows]})


@router.post("/accounts/budget/new", include_in_schema=False)
async def budget_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.add"))):
    form = await request.form()
    period = form.get("period") or month_key()
    category = form.get("category") or "other"
    amount = parse_float(form.get("amount"), 0)
    if amount <= 0:
        return redirect(f"/finance/accounts/budget?period={period}", "Budget amount must be greater than zero.", "error")
    dept_id = parse_int(form.get("department_id")) or None
    b = (db.query(Budget).filter(Budget.period == period, Budget.category == category,
                                 Budget.department_id == dept_id).first())
    if b:
        b.amount = amount
        b.notes = form.get("notes")
    else:
        b = Budget(period=period, category=category, department_id=dept_id, amount=amount,
                   currency=billing.base_currency(db), notes=form.get("notes"))
        db.add(b)
        db.flush()
    log_action(db, user, "update", "accounts", entity=b, description=f"Budget {category} {period} set to {amount:,.2f}",
               request=request)
    db.commit()
    return redirect(f"/finance/accounts/budget?period={period}", "Budget saved.")


@router.post("/accounts/budget/{id}/delete", include_in_schema=False)
async def budget_delete(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.delete"))):
    b = db.query(Budget).get(id)
    if not b:
        raise HTTPException(404, "Budget not found")
    period = b.period
    log_action(db, user, "delete", "accounts", entity=b, description=f"Budget {b.category} {b.period} deleted", request=request)
    db.delete(b)
    db.commit()
    return redirect(f"/finance/accounts/budget?period={period}", "Budget line removed.", "warning")


@router.get("/accounts/forecast", include_in_schema=False)
def accounts_forecast(request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.view"))):
    data = accounting.forecast(db, 3)
    return render(request, "finance/forecast.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "forecast", "data": data, "base": data["currency"],
        "labels": [r["label"] for r in data["history"]] + [r["label"] for r in data["rows"]],
        "revenue": [r["inflow"] for r in data["history"]] + [r["revenue"] for r in data["rows"]],
        "expenses": [r["outflow"] for r in data["history"]] + [r["expenses"] for r in data["rows"]]})


@router.get("/accounts/close", include_in_schema=False)
def accounts_close(request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.view"))):
    periods = db.query(FinancialPeriod).order_by(FinancialPeriod.period.desc()).all()
    options = _period_options(12)
    return render(request, "finance/close.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "close", "periods": periods, "options": options,
        "base": billing.base_currency(db), "current": month_key(),
        "can_close": rbac.has_permission(user, "accounts.approve")})


@router.post("/accounts/close", include_in_schema=False)
async def accounts_close_post(request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.approve"))):
    form = await request.form()
    period = form.get("period") or ""
    try:
        fp = accounting.close_period(db, period, user, (form.get("notes") or "").strip() or None)
    except ValueError as exc:
        return _err("/finance/accounts/close", exc)
    db.commit()
    return redirect("/finance/accounts/close",
                    f"{period} closed — revenue {float(fp.revenue_base):,.0f}, expenses {float(fp.expenses_base):,.0f}.")


@router.post("/accounts/reopen", include_in_schema=False)
async def accounts_reopen(request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.approve"))):
    form = await request.form()
    rationale = (form.get("rationale") or "").strip()
    if not rationale:
        return redirect("/finance/accounts/close", "A rationale is required to re-open a closed period.", "error")
    try:
        accounting.reopen_period(db, form.get("period") or "", user, rationale)
    except ValueError as exc:
        return _err("/finance/accounts/close", exc)
    db.commit()
    return redirect("/finance/accounts/close", "Period re-opened.", "warning")


@router.get("/accounts/consolidated", include_in_schema=False)
def accounts_consolidated(request: Request, period: str = "", db: Session = Depends(get_db),
                          user: User = Depends(require("accounts.view"))):
    period = period or month_key()
    data = accounting.consolidated_report(db, period)
    return render(request, "finance/consolidated.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "consolidated", "data": data, "period": period,
        "periods": _period_options(12), "base": data["base"]})


@router.get("/accounts/{id}", include_in_schema=False)
def account_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("accounts.view"))):
    a = db.query(Account).get(id)
    if not a:
        raise HTTPException(404, "Account not found")
    lines = (db.query(JournalLine).join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
             .filter(JournalLine.account_id == a.id).order_by(JournalEntry.entry_date.desc()).limit(200).all())
    total = round(sum(float(l.debit) - float(l.credit) for l in lines), 2)
    return render(request, "finance/account_detail.html", {"user": user, "a": a, "lines": lines, "total": total,
                                                           "base": billing.base_currency(db),
                                                           "types": accounting.ACCOUNT_TYPES})


# ============================================================================ expenses
@router.get("/expenses", include_in_schema=False)
def expenses_list(request: Request, page: int = 1, q: str = "", status: str = "", category: str = "",
                  department_id: str = "", db: Session = Depends(get_db), user: User = Depends(require("expenses.view"))):
    query = db.query(Expense)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Expense.expense_number.ilike(like), Expense.vendor.ilike(like), Expense.description.ilike(like)))
    if status:
        query = query.filter(Expense.status == status)
    if category:
        query = query.filter(Expense.category == category)
    if department_id:
        query = query.filter(Expense.department_id == int(department_id))
    pg = paginate(query.order_by(Expense.expense_date.desc(), Expense.id.desc()), page, 25)
    counts = dict(db.query(Expense.status, func.count(Expense.id)).group_by(Expense.status).all())
    start = date.today().replace(day=1)
    month_total = (db.query(func.coalesce(func.sum(Expense.amount_in_base), 0))
                   .filter(Expense.status.in_(["approved", "paid"]), Expense.expense_date >= start).scalar() or 0)
    # monthly by category (6 months)
    months, series = [], {}
    for i in range(5, -1, -1):
        y, m = date.today().year, date.today().month - i
        while m <= 0:
            y, m = y - 1, m + 12
        p = f"{y:04d}-{m:02d}"
        s, e = month_bounds(p)
        months.append(s.strftime("%b"))
        rows = (db.query(Expense.category, func.coalesce(func.sum(Expense.amount_in_base), 0))
                .filter(Expense.status.in_(["approved", "paid"]), Expense.expense_date >= s, Expense.expense_date <= e)
                .group_by(Expense.category).all())
        for cat, amt in rows:
            series.setdefault(cat, [0.0] * 6)[len(months) - 1] = round(float(amt), 2)
    return render(request, "finance/expenses_list.html", {
        "user": user, "page": pg, "q": q, "status": status, "category": category, "department_id": department_id,
        "counts": counts, "month_total": round(float(month_total), 2), "base": billing.base_currency(db),
        "categories": [(k, v[0]) for k, v in accounting.EXPENSE_CATEGORIES.items()],
        "departments": [(d.id, d.name) for d in db.query(Department).order_by(Department.name).all()],
        "statuses": ["pending", "approved", "rejected", "paid"], "months": months,
        "series": [{"label": accounting.EXPENSE_CATEGORIES.get(k, (k,))[0], "data": v} for k, v in series.items()],
        "base_url": f"/finance/expenses?q={q}&status={status}&category={category}&department_id={department_id}"})


@router.get("/expenses/new", include_in_schema=False)
def expense_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require("expenses.add"))):
    return render(request, "finance/expense_form.html", {
        "user": user, "categories": [(k, v[0]) for k, v in accounting.EXPENSE_CATEGORIES.items()],
        "departments": [(d.id, d.name) for d in db.query(Department).order_by(Department.name).all()],
        "currencies": [c.code for c in db.query(Currency).order_by(Currency.code).all()],
        "base": billing.base_currency(db), "today_d": date.today()})


@router.post("/expenses/new", include_in_schema=False)
async def expense_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("expenses.add"))):
    form = await request.form()
    receipt_path = None
    upload = form.get("receipt")
    if upload is not None and getattr(upload, "filename", ""):
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch for ch in upload.filename if ch.isalnum() or ch in "._-")[:80]
        name = f"expense-{datetime.utcnow():%Y%m%d%H%M%S}-{safe}"
        (UPLOAD_DIR / name).write_bytes(await upload.read())
        receipt_path = f"/storage/uploads/{name}"
    try:
        e = accounting.record_expense(db, form.get("category") or "other", parse_float(form.get("amount"), 0),
                                      form.get("currency") or billing.base_currency(db),
                                      parse_date(form.get("expense_date"), date.today()), user=user,
                                      department_id=parse_int(form.get("department_id")) or None,
                                      vendor=form.get("vendor"), description=form.get("description"),
                                      receipt_path=receipt_path)
    except ValueError as exc:
        return _err("/finance/expenses/new", exc)
    db.commit()
    return redirect(f"/finance/expenses/{e.id}", f"Expense {e.expense_number} submitted for approval.")


@router.get("/expenses/{id}", include_in_schema=False)
def expense_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("expenses.view"))):
    e = _expense(db, id)
    return render(request, "finance/expense_detail.html", {
        "user": user, "e": e, "base": billing.base_currency(db),
        "journal": db.query(JournalEntry).filter(JournalEntry.reference_type == "expense",
                                                 JournalEntry.reference_id == e.id).all(),
        "events": db.query(AuditEvent).filter(AuditEvent.entity_type == "Expense", AuditEvent.entity_id == e.id)
                    .order_by(AuditEvent.created_at.desc()).limit(20).all()})


@router.post("/expenses/{id}/approve", include_in_schema=False)
async def expense_approve(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("expenses.approve"))):
    e = _expense(db, id)
    form = await request.form()
    try:
        je = accounting.approve_expense(db, e, user, (form.get("note") or form.get("rationale") or "").strip() or None)
    except ValueError as exc:
        return _err(f"/finance/expenses/{id}", exc)
    db.commit()
    return redirect(f"/finance/expenses/{id}", f"Approved; journal {je.entry_number} posted.")


@router.post("/expenses/{id}/reject", include_in_schema=False)
async def expense_reject(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("expenses.approve"))):
    e = _expense(db, id)
    form = await request.form()
    note = (form.get("note") or form.get("rationale") or "").strip()
    if not note:
        return redirect(f"/finance/expenses/{id}", "A rejection reason is required.", "error")
    try:
        accounting.reject_expense(db, e, user, note)
    except ValueError as exc:
        return _err(f"/finance/expenses/{id}", exc)
    db.commit()
    return redirect(f"/finance/expenses/{id}", "Expense rejected.", "warning")


@router.post("/expenses/{id}/pay", include_in_schema=False)
async def expense_pay(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("expenses.execute"))):
    e = _expense(db, id)
    form = await request.form()
    try:
        accounting.pay_expense(db, e, user, (form.get("reference") or "").strip() or None)
    except ValueError as exc:
        return _err(f"/finance/expenses/{id}", exc)
    db.commit()
    return redirect(f"/finance/expenses/{id}", f"{e.expense_number} marked paid.")


# ============================================================================ currencies
@router.get("/currencies", include_in_schema=False)
def currencies_list(request: Request, db: Session = Depends(get_db), user: User = Depends(require("currencies.view"))):
    rows = db.query(Currency).order_by(Currency.is_base.desc(), Currency.code).all()
    usage = dict(db.query(Subscription.currency, func.count(Subscription.id))
                 .filter(Subscription.status == "active").group_by(Subscription.currency).all())
    history = (db.query(ExchangeRateHistory).order_by(ExchangeRateHistory.effective_at.desc()).limit(25).all())
    return render(request, "finance/currencies.html", {
        "user": user, "rows": rows, "usage": usage, "history": history, "base": billing.base_currency(db),
        "country_map": billing.country_currency_map(db),
        "can_edit": rbac.has_permission(user, "currencies.update")})


@router.post("/currencies/{code}/rate", include_in_schema=False)
async def currency_set_rate(code: str, request: Request, db: Session = Depends(get_db),
                            user: User = Depends(require("currencies.update"))):
    form = await request.form()
    try:
        billing.set_exchange_rate(db, code.upper(), parse_float(form.get("rate"), 0), user,
                                  source=form.get("source") or "manual")
    except ValueError as exc:
        return _err("/finance/currencies", exc)
    db.commit()
    return redirect("/finance/currencies", f"{code.upper()} rate updated.")


@router.get("/currencies/{code}", include_in_schema=False)
def currency_detail(code: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require("currencies.view"))):
    c = db.query(Currency).filter(Currency.code == code.upper()).first()
    if not c:
        raise HTTPException(404, "Currency not found")
    history = (db.query(ExchangeRateHistory).filter(ExchangeRateHistory.currency_code == c.code)
               .order_by(ExchangeRateHistory.effective_at).all())
    subs = db.query(Subscription).filter(Subscription.currency == c.code, Subscription.status == "active").count()
    invoiced = (db.query(func.coalesce(func.sum(Invoice.total), 0)).filter(Invoice.currency == c.code,
                                                                          Invoice.status != "void").scalar() or 0)
    return render(request, "finance/currency_detail.html", {
        "user": user, "c": c, "history": history, "subs": subs, "invoiced": float(invoiced),
        "base": billing.base_currency(db),
        "labels": [h.effective_at.strftime("%d %b") for h in history], "values": [float(h.rate_to_base) for h in history],
        "can_edit": rbac.has_permission(user, "currencies.update")})
