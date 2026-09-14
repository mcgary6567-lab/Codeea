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
from app.models.erp import (BeneficiaryAccount, ClientAcademicGroup, InvoiceAdditionType, LedgerAddition,
                            LEDGER_ADDITION_TYPES)
from app.models.finance import (Account, Budget, Currency, DiscountRequest, ExchangeRateHistory, Expense,
                                FinancialPeriod, Invoice, InvoiceItem, JournalEntry, JournalLine, LedgerEntry,
                                Payment, Receipt, Scholarship, Subscription)
from app.models.people import Client, Employee, Student, Teacher
from app.services import accounting, billing

router = APIRouter(prefix="/finance", dependencies=[Depends(csrf_protect)])

UPLOAD_DIR = BASE_DIR / "storage" / "uploads"

ACCOUNT_TABS = [("chart", "Chart of accounts", "/finance/accounts"), ("heads", "Accounts heads", "/finance/accounts/heads"),
                ("tree", "Tree view", "/finance/accounts/tree"), ("vouchers", "Vouchers", "/finance/accounts/vouchers"),
                ("reports", "Reports", "/finance/accounts/reports/trial-balance"),
                ("journal", "Journal", "/finance/accounts/journal"),
                ("pnl", "P&L", "/finance/accounts/pnl"), ("cash", "Cash flow", "/finance/accounts/cash-flow"),
                ("aging", "Receivables aging", "/finance/accounts/aging"), ("payables", "Payables", "/finance/accounts/payables"),
                ("budget", "Budget vs actual", "/finance/accounts/budget"), ("forecast", "Forecast", "/finance/accounts/forecast"),
                ("close", "Financial close", "/finance/accounts/close"),
                ("consolidated", "Consolidated", "/finance/accounts/consolidated")]

# The ERP's Accounts > Reports group (docs/AUDIT_ACCOUNTS_CONFIG.md). Every one is computed from JournalLine.
REPORT_TABS = [("ledger", "Ledger Report", "/finance/accounts/reports/ledger"),
               ("trial-balance", "Trial Balance", "/finance/accounts/reports/trial-balance"),
               ("income-statement", "Income Statement", "/finance/accounts/reports/income-statement"),
               ("balance-sheet", "Balance Sheet", "/finance/accounts/reports/balance-sheet"),
               ("payables", "Payables Summary", "/finance/accounts/reports/payables"),
               ("account-wise", "Account Wise Summary", "/finance/accounts/reports/account-wise"),
               ("approved-advances", "Approved Advances", "/finance/accounts/reports/approved-advances")]

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


def _period_options(count: int = 15, ahead: int = 0) -> list[str]:
    today = date.today()
    out = []
    for i in range(-ahead, count):
        y, m = today.year, today.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        while m > 12:
            y, m = y + 1, m - 12
        out.append(f"{y:04d}-{m:02d}")
    return out


def _client_options(db: Session, statuses: tuple | None = None) -> list[tuple[int, str]]:
    q = db.query(Client)
    if statuses:
        q = q.filter(Client.status.in_(statuses))
    return [(c.id, f"{c.client_code} — {c.full_name}") for c in q.order_by(Client.client_code).all()]


def _currency_codes(db: Session, active_only: bool = True) -> list[str]:
    q = db.query(Currency)
    if active_only:
        q = q.filter(Currency.is_active.is_(True))
    return [c.code for c in q.order_by(Currency.code).all()]


def _rep_options(db: Session) -> list[tuple[int, str]]:
    reps = billing._billing_reps(db)
    if not reps:
        reps = db.query(User).filter(User.is_active.is_(True)).order_by(User.full_name).limit(50).all()
    return [(u.id, u.full_name) for u in reps]


def _shift_label(client: Client | None) -> str:
    if not client:
        return "—"
    return {"morning": "Morning", "night": "Night", "evening": "Evening"}.get(client.shift or "", (client.shift or "—").title())


def _date_range(query, column, date_from: str, date_to: str, is_datetime: bool = False):
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(column >= (datetime.combine(df, datetime.min.time()) if is_datetime else df))
    if dt:
        query = query.filter(column <= (datetime.combine(dt, datetime.max.time()) if is_datetime else dt))
    return query


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
def _invoice_form_context(db: Session) -> dict:
    """Options for the ERP "Create Single Invoice" modal."""
    clients = db.query(Client).filter(Client.status.in_(["active", "trial", "regular"])).order_by(Client.client_code).all()
    subs = (db.query(Subscription).filter(Subscription.status.in_(["active", "regular", "trial", "frozen", "pending_approval"]))
            .order_by(Subscription.client_id, Subscription.id).all())
    by_client: dict[str, list[dict]] = {}
    for s in subs:
        by_client.setdefault(str(s.client_id), []).append({
            "id": s.id, "label": f"{s.subscription_code} — {s.student.full_name if s.student else ''} — "
                                 f"{s.currency} {float(s.price or 0):,.2f}", "currency": s.currency})
    types = db.query(InvoiceAdditionType).filter(InvoiceAdditionType.status == "active").order_by(InvoiceAdditionType.description).all()
    return {"client_options": [(c.id, f"{c.client_code} — {c.full_name} ({c.currency})") for c in clients],
            "subs_by_client": by_client,
            "addition_types": [(t.id, f"{t.description} ({t.addition_type})") for t in types],
            "rep_options": _rep_options(db)}


@router.get("/invoices", include_in_schema=False)
def invoices_list(request: Request, page: int = 1, q: str = "", status: str = "", currency: str = "",
                  client_id: str = "", date_from: str = "", date_to: str = "", overdue_only: str = "",
                  db: Session = Depends(get_db), user: User = Depends(require("billing.view"))):
    query = db.query(Invoice)
    if q:
        like = f"%{q}%"
        query = (query.join(Client, Invoice.client_id == Client.id)
                 .filter(or_(Invoice.invoice_number.ilike(like), Client.full_name.ilike(like), Client.client_code.ilike(like))))
    if status:
        query = query.filter(billing.invoice_status_filter(status))
    if currency:
        query = query.filter(Invoice.currency == currency)
    if client_id and parse_int(client_id):
        query = query.filter(Invoice.client_id == int(client_id))
    query = _date_range(query, Invoice.issue_date, date_from, date_to)
    if overdue_only:
        query = query.filter(Invoice.status == "overdue")
    pg = paginate(query.order_by(Invoice.issue_date.desc(), Invoice.id.desc()), page, 25)
    base = (f"/finance/invoices?q={q}&status={status}&currency={currency}&client_id={client_id}"
            f"&date_from={date_from}&date_to={date_to}")
    return render(request, "finance/invoices_list.html", {
        "user": user, "page": pg, "q": q, "status": status, "currency": currency, "client_id": client_id,
        "date_from": date_from, "date_to": date_to, "base_url": base,
        "stats": billing.invoice_stats(db), "statuses": billing.INVOICE_FILTER_STATUSES,
        "currencies": _currency_codes(db), "clients": _client_options(db),
        "periods": _period_options(6, ahead=1), "today_d": date.today(), "base": billing.base_currency(db),
        "shifts": [("all", "All shifts"), ("morning", "Morning"), ("night", "Night")],
        "recurrences": [("all", "All")] + [(r, r.replace("_", " ").title()) for r in billing.FEE_RECURRENCES],
        **_invoice_form_context(db)})


@router.get("/invoices/bulk", include_in_schema=False)
def invoices_bulk_preview(request: Request, period: str = "", shift: str = "all", recurrence: str = "all",
                          db: Session = Depends(get_db), user: User = Depends(require("billing.add"))):
    """Generate Bulk Invoices: dry-run preview of what the POST would create."""
    period = period or month_key()
    try:
        preview = billing.bulk_generate_invoices(db, user, period, shift=shift, recurrence=recurrence, dry_run=True)
    except (ValueError, IndexError):
        return redirect("/finance/invoices", "Choose a valid billing period (YYYY-MM).", "error")
    return render(request, "finance/invoice_bulk.html", {
        "user": user, "period": period, "shift": shift, "recurrence": recurrence, "preview": preview,
        "periods": _period_options(6, ahead=1), "base": billing.base_currency(db),
        "shifts": [("all", "All shifts"), ("morning", "Morning"), ("night", "Night")],
        "recurrences": [("all", "All")] + [(r, r.replace("_", " ").title()) for r in billing.FEE_RECURRENCES]})


@router.post("/invoices/generate", include_in_schema=False)
async def invoices_generate(request: Request, db: Session = Depends(get_db), user: User = Depends(require("billing.add"))):
    form = await request.form()
    period = form.get("period") or month_key()
    shift = form.get("shift") or "all"
    recurrence = form.get("recurrence") or "all"
    if parse_bool(form.get("dry_run")):
        return redirect(f"/finance/invoices/bulk?period={period}&shift={shift}&recurrence={recurrence}")
    try:
        result = billing.bulk_generate_invoices(db, user, period, shift=shift, recurrence=recurrence, dry_run=False)
    except (ValueError, IndexError) as exc:
        return _err("/finance/invoices", exc)
    db.commit()
    created = len(result["created"])
    return redirect(f"/finance/invoices?date_from={date.today().isoformat()}" if created else "/finance/invoices",
                    f"{period}: {created} bulk invoice(s) generated for {result['clients']} famil{'y' if result['clients'] == 1 else 'ies'}, "
                    f"{result['skipped']} skipped (already invoiced).", "success" if created else "info")


@router.post("/invoices/new", include_in_schema=False)
async def invoice_create_single(request: Request, db: Session = Depends(get_db), user: User = Depends(require("billing.add"))):
    """ERP "Create Single Invoice": one family, one or more of its subscriptions, optional addition lines."""
    form = await request.form()
    client = db.query(Client).get(parse_int(form.get("client_id"), 0) or 0)
    if not client:
        return redirect("/finance/invoices", "Select the family to invoice.", "error")
    sub_ids = [parse_int(v) for v in form.getlist("subscription_ids") if parse_int(v)]
    subs = db.query(Subscription).filter(Subscription.id.in_(sub_ids)).all() if sub_ids else []
    if not subs:
        return redirect("/finance/invoices", "Select at least one subscription of that family.", "error")
    issue = parse_date(form.get("issue_date"), date.today())
    due = parse_date(form.get("due_date"), issue + timedelta(days=billing.INVOICE_TERMS_DAYS))
    period = form.get("period") or month_key(issue)
    try:
        start, end = month_bounds(period)
    except (ValueError, IndexError):
        return redirect("/finance/invoices", "Choose a valid billing period (YYYY-MM).", "error")
    additions = []
    for tid, amt in zip(form.getlist("addition_type_id"), form.getlist("addition_amount")):
        t = db.query(InvoiceAdditionType).get(parse_int(tid, 0) or 0) if tid else None
        if t and parse_float(amt, 0) > 0:
            additions.append((t, parse_float(amt, 0), "manual"))
    try:
        inv = billing.create_client_invoice(db, client, subs, start, end, user=user, issue_date=issue, due_date=due,
                                            status=form.get("status") or "pending", is_bulk=False, additions=additions,
                                            remarks=(form.get("remarks") or "").strip() or None)
        rep = parse_int(form.get("billing_rep_id"))
        if rep:
            inv.billing_rep_id = rep
    except ValueError as exc:
        return _err("/finance/invoices", exc)
    db.commit()
    return redirect(f"/finance/invoices/{inv.id}", f"Invoice {inv.invoice_number} created for {client.full_name}.")


@router.get("/invoices/{id}", include_in_schema=False)
def invoice_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("billing.view"))):
    inv = _invoice(db, id)
    return render(request, "finance/invoice_detail.html", {
        "user": user, "inv": inv, "payments": inv.payments, "status": billing.normalise_invoice_status(inv.status),
        "ledger": db.query(LedgerEntry).filter(LedgerEntry.reference_type == "invoice", LedgerEntry.reference_id == inv.id).all(),
        "balance": billing.client_balance(db, inv.client), "methods": billing.PAYMENT_METHODS,
        "gateways": billing.GATEWAYS, "base": billing.base_currency(db), "today_d": date.today(),
        "shift": _shift_label(inv.client),
        "beneficiaries": [(b.id, f"{b.category} — {b.account_name}") for b in
                          db.query(BeneficiaryAccount).filter(BeneficiaryAccount.status == "active").order_by(BeneficiaryAccount.category).all()],
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


@router.post("/invoices/{id}/confirm", include_in_schema=False)
async def invoice_confirm(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("billing.update"))):
    inv = _invoice(db, id)
    try:
        billing.confirm_invoice(db, inv, user)
    except ValueError as exc:
        return _err(f"/finance/invoices/{id}", exc)
    db.commit()
    return redirect(f"/finance/invoices/{id}", f"{inv.invoice_number} confirmed.")


@router.post("/invoices/{id}/cancel", include_in_schema=False)
@router.post("/invoices/{id}/void", include_in_schema=False)  # legacy name
async def invoice_cancel(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("billing.delete"))):
    inv = _invoice(db, id)
    form = await request.form()
    try:
        billing.cancel_invoice(db, inv, user, (form.get("reason") or form.get("rationale") or "").strip())
    except ValueError as exc:
        return _err(f"/finance/invoices/{id}", exc)
    db.commit()
    return redirect(f"/finance/invoices/{id}", f"{inv.invoice_number} cancelled.", "warning")


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
    ben = db.query(BeneficiaryAccount).get(parse_int(form.get("beneficiary_account_id"), 0) or 0) if form.get("beneficiary_account_id") else None
    try:
        pay = billing.record_payment(db, inv.client, parse_float(form.get("amount"), 0), form.get("currency") or inv.currency,
                                     form.get("method") or "bank_transfer", form.get("reference"), user=user, invoice=inv,
                                     gateway=form.get("gateway") or None,
                                     received_at=datetime.combine(parse_date(form.get("received_at"), date.today()), datetime.min.time()),
                                     status=form.get("status") or "confirmed", beneficiary_account=ben,
                                     receiver_name=(form.get("receiver_name") or user.full_name),
                                     description=(form.get("description") or None))
    except ValueError as exc:
        return _err(f"/finance/invoices/{id}", exc)
    db.commit()
    return redirect(f"/finance/invoices/{id}", f"Receipt {pay.payment_number} recorded.")


# ============================================================================ receipts (ERP Receipts page)
def _receipt_filters(db: Session) -> dict:
    return {"currencies": _currency_codes(db), "clients": _client_options(db), "methods": billing.PAYMENT_METHODS,
            "statuses": billing.RECEIPT_STATUSES,
            "beneficiaries": [(b.id, f"{b.category} — {b.account_name}" + (" (auto)" if b.is_auto else "")) for b in
                              db.query(BeneficiaryAccount).filter(BeneficiaryAccount.status == "active")
                              .order_by(BeneficiaryAccount.payment_mode, BeneficiaryAccount.category).all()]}


@router.get("/receipts", include_in_schema=False)
def receipts_list(request: Request, page: int = 1, q: str = "", status: str = "", currency: str = "", client_id: str = "",
                  method: str = "", beneficiary_account_id: str = "", date_from: str = "", date_to: str = "",
                  db: Session = Depends(get_db), user: User = Depends(require("payments.view"))):
    query = db.query(Payment)
    if q:
        like = f"%{q}%"
        query = (query.join(Client, Payment.client_id == Client.id)
                 .filter(or_(Payment.payment_number.ilike(like), Payment.reference.ilike(like),
                             Client.full_name.ilike(like), Client.client_code.ilike(like))))
    if status:
        query = query.filter(billing.payment_status_filter(status))
    if currency:
        query = query.filter(Payment.currency == currency)
    if client_id and parse_int(client_id):
        query = query.filter(Payment.client_id == int(client_id))
    if method:
        query = query.filter(Payment.method == method)
    if beneficiary_account_id and parse_int(beneficiary_account_id):
        query = query.filter(Payment.beneficiary_account_id == int(beneficiary_account_id))
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(func.coalesce(Payment.receipt_date, func.date(Payment.received_at)) >= df)
    if dt:
        query = query.filter(func.coalesce(Payment.receipt_date, func.date(Payment.received_at)) <= dt)
    pg = paginate(query.order_by(Payment.received_at.desc(), Payment.id.desc()), page, 25)
    base = (f"/finance/receipts?q={q}&status={status}&currency={currency}&client_id={client_id}&method={method}"
            f"&beneficiary_account_id={beneficiary_account_id}&date_from={date_from}&date_to={date_to}")
    return render(request, "finance/receipts_list.html", {
        "user": user, "page": pg, "q": q, "status": status, "currency": currency, "client_id": client_id,
        "method": method, "beneficiary_account_id": beneficiary_account_id, "date_from": date_from, "date_to": date_to,
        "base_url": base, "stats": billing.payment_stats(db), "base": billing.base_currency(db),
        "shift_label": _shift_label, **_receipt_filters(db)})


@router.get("/receipts/new", include_in_schema=False)
def receipt_new(request: Request, client_id: str = "", invoice_id: str = "", db: Session = Depends(get_db),
                user: User = Depends(require("payments.add"))):
    return render(request, "finance/receipt_form.html", {
        "user": user, "client_id": client_id, "invoice_id": invoice_id, "today_d": date.today(),
        "invoices": db.query(Invoice).filter(Invoice.status.in_(billing.INVOICE_OPEN_SET)).order_by(Invoice.due_date).all(),
        "gateways": billing.GATEWAYS, "categories": billing.PAYMENT_CATEGORIES, "rep_options": _rep_options(db),
        "rates": {c.code: float(c.rate_to_base) for c in db.query(Currency).all()},
        "client_meta": {str(c.id): {"currency": c.currency, "rep": c.billing_rep_id or ""} for c in db.query(Client).all()},
        "base": billing.base_currency(db), **_receipt_filters(db)})


@router.post("/receipts/new", include_in_schema=False)
async def receipt_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payments.add"))):
    form = await request.form()
    invoice = db.query(Invoice).get(parse_int(form.get("invoice_id"), 0) or 0) if form.get("invoice_id") else None
    client = db.query(Client).get(parse_int(form.get("client_id"), 0) or 0) if form.get("client_id") else (invoice.client if invoice else None)
    if not client:
        return redirect("/finance/receipts/new", "Select the family that paid.", "error")
    if invoice is not None and invoice.client_id != client.id:
        return redirect("/finance/receipts/new", "That invoice belongs to a different family.", "error")
    ben = db.query(BeneficiaryAccount).get(parse_int(form.get("beneficiary_account_id"), 0) or 0) if form.get("beneficiary_account_id") else None
    rep = db.query(User).get(parse_int(form.get("billing_rep_id"), 0) or 0) if form.get("billing_rep_id") else None
    received = parse_date(form.get("received_at"), None) or parse_date(form.get("receipt_date"), date.today())
    try:
        pay = billing.record_payment(
            db, client, parse_float(form.get("amount"), 0), form.get("currency") or client.currency,
            form.get("method") or "bank_transfer", (form.get("reference") or "").strip() or None, user=user, invoice=invoice,
            gateway=(form.get("gateway") or None), received_at=datetime.combine(received, datetime.min.time()),
            status=form.get("status") or "confirmed", notes=(form.get("notes") or None),
            receipt_date=parse_date(form.get("receipt_date"), date.today()),
            receiver_name=(form.get("receiver_name") or "").strip() or user.full_name,
            receiving_destination=(form.get("receiving_destination") or "").strip() or None,
            description=(form.get("description") or "").strip() or None, category=(form.get("category") or None),
            beneficiary_account=ben, billing_rep=rep,
            amount_in_base=parse_float(form.get("lc_amount"), 0) or None)
    except ValueError as exc:
        return _err("/finance/receipts/new", exc)
    db.commit()
    return redirect(f"/finance/payments/{pay.id}", f"Receipt {pay.payment_number} recorded ({pay.status}).")


@router.post("/receipts/sync-gateway", include_in_schema=False)
async def receipts_sync_gateway(request: Request, db: Session = Depends(get_db), user: User = Depends(require("payments.add"))):
    """Simulated Stripe / PayPal pull: confirmed receipts arrive automatically for open invoices."""
    created = billing.sync_gateway_receipts(db, user)
    db.commit()
    return redirect("/finance/receipts", f"Gateway sync complete: {len(created)} auto receipt(s) pulled."
                    if created else "Gateway sync complete: nothing new to pull.", "success" if created else "info")


@router.post("/receipts/{id}/confirm", include_in_schema=False)
async def receipt_confirm(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payments.approve"))):
    p = _payment(db, id)
    try:
        billing.confirm_payment(db, p, user)
    except ValueError as exc:
        return _err(f"/finance/payments/{id}", exc)
    db.commit()
    return redirect(f"/finance/payments/{id}", f"Receipt {p.payment_number} confirmed and applied.")


@router.post("/receipts/{id}/cancel", include_in_schema=False)
async def receipt_cancel(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("payments.approve"))):
    p = _payment(db, id)
    form = await request.form()
    try:
        billing.cancel_payment(db, p, user, (form.get("reason") or form.get("rationale") or "").strip())
    except ValueError as exc:
        return _err(f"/finance/payments/{id}", exc)
    db.commit()
    return redirect(f"/finance/payments/{id}", f"Receipt {p.payment_number} cancelled.", "warning")


# ============================================================================ ledger additions (ERP)
@router.get("/ledger-additions", include_in_schema=False)
def ledger_additions_list(request: Request, page: int = 1, status: str = "", client_id: str = "", addition_type: str = "",
                          effect: str = "", date_from: str = "", date_to: str = "", db: Session = Depends(get_db),
                          user: User = Depends(require("ledger.view"))):
    query = db.query(LedgerAddition)
    if status:
        query = query.filter(LedgerAddition.status == status)
    if client_id and parse_int(client_id):
        query = query.filter(LedgerAddition.client_id == int(client_id))
    if addition_type:
        query = query.filter(LedgerAddition.addition_type == addition_type)
    if effect:
        query = query.filter(LedgerAddition.effect == effect)
    query = _date_range(query, LedgerAddition.addition_date, date_from, date_to)
    pg = paginate(query.order_by(LedgerAddition.addition_date.desc(), LedgerAddition.id.desc()), page, 25)
    base = (f"/finance/ledger-additions?status={status}&client_id={client_id}&addition_type={addition_type}"
            f"&effect={effect}&date_from={date_from}&date_to={date_to}")
    employees = db.query(Employee).filter(Employee.status.in_(["active", "probation"])).order_by(Employee.full_name).all()
    return render(request, "finance/ledger_additions.html", {
        "user": user, "page": pg, "status": status, "client_id": client_id, "addition_type": addition_type, "effect": effect,
        "date_from": date_from, "date_to": date_to, "base_url": base, "tiles": billing.ledger_addition_tiles(db),
        "types": LEDGER_ADDITION_TYPES, "effects": billing.LEDGER_ADDITION_EFFECTS, "clients": _client_options(db),
        "currencies": _currency_codes(db), "rep_options": _rep_options(db), "today_d": date.today(),
        "employees": [(e.id, f"{e.employee_code} — {e.full_name}") for e in employees],
        "rates": {c.code: float(c.rate_to_base) for c in db.query(Currency).all()},
        "client_meta": {str(c.id): {"currency": c.currency, "rep": c.billing_rep_id or ""} for c in db.query(Client).all()},
        "base": billing.base_currency(db), "shift_label": _shift_label})


@router.post("/ledger-additions/new", include_in_schema=False)
async def ledger_addition_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require("ledger.add"))):
    form = await request.form()
    client = db.query(Client).get(parse_int(form.get("client_id"), 0) or 0)
    if not client:
        return redirect("/finance/ledger-additions", "Select a family.", "error")
    emp = db.query(Employee).get(parse_int(form.get("reference_employee_id"), 0) or 0) if form.get("reference_employee_id") else None
    rep = db.query(User).get(parse_int(form.get("billing_rep_id"), 0) or 0) if form.get("billing_rep_id") else None
    try:
        la = billing.create_ledger_addition(
            db, client, parse_float(form.get("amount"), 0), form.get("currency") or client.currency,
            form.get("addition_type") or "Adjustment", form.get("effect") or "minus",
            parse_date(form.get("addition_date"), date.today()), user, reference_employee=emp, billing_rep=rep,
            remarks=(form.get("remarks") or "").strip() or None,
            currency_rate=parse_float(form.get("currency_rate"), 0) or None,
            lc_amount=parse_float(form.get("lc_amount"), 0) or None, status=form.get("status") or "pending")
    except ValueError as exc:
        return _err("/finance/ledger-additions", exc)
    db.commit()
    return redirect("/finance/ledger-additions", f"Ledger addition #{la.id} ({la.addition_type}) recorded as {la.status}.")


@router.post("/ledger-additions/{id}/confirm", include_in_schema=False)
async def ledger_addition_confirm(id: int, request: Request, db: Session = Depends(get_db),
                                  user: User = Depends(require("ledger.approve", "ledger.update", any_of=True))):
    la = db.query(LedgerAddition).get(id)
    if not la:
        raise HTTPException(404, "Ledger addition not found")
    try:
        billing.post_ledger_addition(db, la, user)
    except ValueError as exc:
        return _err("/finance/ledger-additions", exc)
    db.commit()
    return redirect("/finance/ledger-additions", f"Ledger addition #{la.id} confirmed and posted to {la.client.client_code}.")


@router.post("/ledger-additions/{id}/cancel", include_in_schema=False)
async def ledger_addition_cancel(id: int, request: Request, db: Session = Depends(get_db),
                                 user: User = Depends(require("ledger.approve", "ledger.update", any_of=True))):
    la = db.query(LedgerAddition).get(id)
    if not la:
        raise HTTPException(404, "Ledger addition not found")
    form = await request.form()
    try:
        billing.cancel_ledger_addition(db, la, user, (form.get("reason") or form.get("rationale") or "").strip())
    except ValueError as exc:
        return _err("/finance/ledger-additions", exc)
    db.commit()
    return redirect("/finance/ledger-additions", f"Ledger addition #{la.id} cancelled.", "warning")


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
        query = query.filter(billing.payment_status_filter(status))
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
        "statuses": [("pending", "Pending"), ("confirmed", "Confirmed"), ("failed", "Failed"), ("refunded", "Refunded"),
                     ("cancelled", "Cancelled")]})


@router.get("/payments/new", include_in_schema=False)
def payment_new(request: Request, client_id: str = "", invoice_id: str = "", db: Session = Depends(get_db),
                user: User = Depends(require("payments.add"))):
    return render(request, "finance/payment_form.html", {
        "user": user, "client_id": client_id, "invoice_id": invoice_id,
        "clients": [(c.id, f"{c.client_code} — {c.full_name}") for c in db.query(Client).order_by(Client.client_code).all()],
        "invoices": db.query(Invoice).filter(Invoice.status.in_(billing.INVOICE_OPEN_SET)).order_by(Invoice.due_date).all(),
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
    rows = (db.query(Payment).filter(Payment.status.in_(billing.PAYMENT_CONFIRMED_SET), Payment.reconciled.is_(False))
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
        "status": billing.normalise_payment_status(p.status), "shift": _shift_label(p.client),
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
def ledger_index(request: Request, q: str = "", client_id: str = "", date_from: str = "", date_to: str = "",
                 entry_type: str = "", db: Session = Depends(get_db), user: User = Depends(require("ledger.view"))):
    """Client Ledger Report: pick an Account (family) to see the statement; otherwise the balances overview."""
    if client_id and parse_int(client_id):
        return _render_ledger_report(request, db, user, _client(db, int(client_id)), date_from, date_to, entry_type)
    rows = billing.client_balances(db, q)
    owing = [r for r in rows if r["balance"] > 0.01]
    return render(request, "finance/ledger_list.html", {
        "user": user, "rows": rows, "q": q, "base": billing.base_currency(db), "clients": _client_options(db),
        "date_from": date_from, "date_to": date_to,
        "totals": {"owing": len(owing), "credit": len([r for r in rows if r["balance"] < -0.01]),
                   "owed_base": round(sum(r["balance"] * billing.get_rate(db, r["client"].currency) for r in owing), 2),
                   "overdue": sum(r["overdue"] for r in rows)}})


def _render_ledger_report(request: Request, db: Session, user: User, c: Client, date_from: str, date_to: str,
                          entry_type: str = ""):
    df, dt = parse_date(date_from), parse_date(date_to)
    report = billing.ledger_report(db, c, df, dt, entry_type or None)
    return render(request, "finance/ledger_statement.html", {
        "user": user, "c": c, "report": report, "entries": [r["entry"] for r in report["rows"]],
        "all_count": report["all_count"], "entry_type": entry_type, "date_from": date_from, "date_to": date_to,
        "types": billing.LEDGER_TYPES, "balance": billing.client_balance(db, c), "credit": billing.available_credit(db, c),
        "clients": _client_options(db), "shift": _shift_label(c),
        "currencies": [x.code for x in db.query(Currency).order_by(Currency.code).all()],
        "invoices": db.query(Invoice).filter(Invoice.client_id == c.id).order_by(Invoice.issue_date.desc()).limit(20).all(),
        "subscriptions": db.query(Subscription).filter(Subscription.client_id == c.id).all(), "today_d": date.today()})


@router.get("/ledger/credits", include_in_schema=False)
def ledger_credits(request: Request, db: Session = Depends(get_db), user: User = Depends(require("ledger.view"))):
    data = billing.credits_report(db)
    referrals = db.query(Referral).filter(Referral.status == "credited").order_by(Referral.qualified_at.desc()).limit(50).all()
    return render(request, "finance/credits_report.html", {"user": user, **data, "referrals": referrals})


@router.get("/ledger/{client_id}", include_in_schema=False)
def ledger_statement(client_id: int, request: Request, entry_type: str = "", date_from: str = "", date_to: str = "",
                     db: Session = Depends(get_db), user: User = Depends(require("ledger.view"))):
    c = _client(db, client_id)
    return _render_ledger_report(request, db, user, c, date_from, date_to, entry_type)


@router.get("/ledger/{client_id}/export.csv", include_in_schema=False)
def ledger_statement_csv(client_id: int, date_from: str = "", date_to: str = "", db: Session = Depends(get_db),
                         user: User = Depends(require("ledger.export"))):
    c = _client(db, client_id)
    report = billing.ledger_report(db, c, parse_date(date_from), parse_date(date_to))
    rows = [["Srl", "Date", "Transaction Type", "Description", "Amount", "Balance", "Currency"],
            ["", "", "Previous Balance", "", "", report["previous_balance"], report["currency"]]]
    for r in report["rows"]:
        rows.append([r["srl"], r["date"].isoformat(), r["type"], r["description"], r["amount"], r["balance"], report["currency"]])
    rows.append(["", "", "Total", "", report["total"], report["closing_balance"], report["currency"]])
    rows.append(["", "", "In Words", report["in_words"], "", "", ""])
    return _csv(rows, f"ledger-{c.client_code}.csv")


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
def accounts_chart(request: Request, q: str = "", account_type: str = "", head_id: str = "", postable: str = "",
                   db: Session = Depends(get_db), user: User = Depends(require("accounts.view"))):
    accounting.ensure_chart_of_accounts(db)
    db.commit()
    all_accounts = db.query(Account).order_by(Account.account_type, Account.sort_no, Account.code).all()
    accounts = all_accounts
    if q:
        needle = q.lower()
        accounts = [a for a in accounts if needle in a.code.lower() or needle in a.name.lower()]
    if account_type:
        accounts = [a for a in accounts if a.account_type == account_type]
    head = parse_int(head_id)
    if head:
        accounts = [a for a in accounts if a.parent_id == head]
    if postable == "yes":
        accounts = [a for a in accounts if a.is_postable and not a.is_head]
    elif postable == "no":
        accounts = [a for a in accounts if a.is_head or not a.is_postable]
    balances = dict(db.query(JournalLine.account_id,
                             func.coalesce(func.sum(JournalLine.debit), 0) - func.coalesce(func.sum(JournalLine.credit), 0))
                    .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                    .filter(accounting.ledger_entry_filter()).group_by(JournalLine.account_id).all())
    tree = []
    for a in accounts:
        if a.parent_id is None:
            tree.append((a, [x for x in accounts if x.parent_id == a.id]))
    return render(request, "finance/accounts_chart.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "chart", "accounts": accounts, "tree": tree,
        "balances": {k: round(float(v), 2) for k, v in balances.items()}, "types": accounting.ACCOUNT_TYPES,
        "base": billing.base_currency(db), "q": q, "account_type": account_type, "head_id": head_id,
        "postable": postable, "heads": _head_options(db),
        "stats": {"total": len(all_accounts), "heads": len([a for a in all_accounts if a.is_head]),
                  "postable": len([a for a in all_accounts if a.is_postable and not a.is_head]),
                  "inactive": len([a for a in all_accounts if not a.is_active])},
        "parent_options": [(a.id, f"{a.code} {a.name}") for a in all_accounts]})


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
                parent_id=parse_int(form.get("parent_id")) or None, description=form.get("description"), is_active=True,
                is_head=parse_bool(form.get("is_head")), is_postable=not parse_bool(form.get("is_head")),
                sort_no=parse_int(form.get("sort_no"), 0) or 0,
                opening_balance=parse_float(form.get("opening_balance"), 0))
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
    if "parent_id" in form:
        parent = parse_int(form.get("parent_id")) or None
        a.parent_id = None if parent == a.id else parent
    if "sort_no" in form:
        a.sort_no = parse_int(form.get("sort_no"), a.sort_no or 0) or 0
    if "opening_balance" in form:
        a.opening_balance = parse_float(form.get("opening_balance"), float(a.opening_balance or 0))
    if "is_head" in form:
        make_head = parse_bool(form.get("is_head"))
        if make_head and not a.is_head and accounting.account_has_postings(db, a):
            return redirect("/finance/accounts",
                            f"{a.code} {a.name} already carries postings and cannot become a head.", "error")
        a.is_head = make_head
        a.is_postable = not make_head
    log_action(db, user, "update", "accounts", entity=a, description=f"Account {a.code} updated", before=before,
               after=snapshot(a), rationale=form.get("rationale") or form.get("reason"), request=request)
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


# ============================================================================ Accounts — ERP parity
# Setup (Accounts Heads · Chart of Accounts · Accounts Tree View), Transactions (Journal / Payment / Receipt
# Vouchers) and the statutory reports. See docs/AUDIT_ACCOUNTS_CONFIG.md.
def _account(db: Session, id: int) -> Account:
    a = db.query(Account).get(id)
    if not a:
        raise HTTPException(404, "Account not found")
    return a


def _voucher(db: Session, id: int) -> JournalEntry:
    v = db.query(JournalEntry).get(id)
    if not v:
        raise HTTPException(404, "Voucher not found")
    return v


def _account_options(db: Session, types: tuple | None = None, postable_only: bool = True) -> list[tuple[int, str]]:
    q = db.query(Account).filter(Account.is_active.is_(True))
    if postable_only:
        q = q.filter(Account.is_head.is_(False), Account.is_postable.is_(True))
    if types:
        q = q.filter(Account.account_type.in_(types))
    return [(a.id, f"{a.code} - {a.name}") for a in q.order_by(Account.account_type, Account.sort_no, Account.code).all()]


def _head_options(db: Session) -> list[tuple[int, str]]:
    return [(a.id, f"{a.code} - {a.name}") for a in accounting.head_accounts(db)]


def _beneficiary_options(db: Session) -> list[tuple[int, str]]:
    rows = db.query(BeneficiaryAccount).filter(BeneficiaryAccount.status == "active").order_by(
        BeneficiaryAccount.payment_mode, BeneficiaryAccount.account_name).all()
    return [(b.id, f"{b.account_name} ({b.category})") for b in rows]


def _employee_options(db: Session) -> list[tuple[int, str]]:
    rows = db.query(Employee).filter(Employee.status.in_(["active", "probation"])).order_by(Employee.full_name).all()
    return [(e.id, f"{e.employee_code} - {e.full_name}") for e in rows]


def _report_range(date_from: str, date_to: str) -> tuple[date, date]:
    end = parse_date(date_to) or date.today()
    start = parse_date(date_from) or month_bounds(month_key(end))[0]
    if start > end:
        start, end = end, start
    return start, end


def _voucher_ctx(db: Session, user: User, extra: dict) -> dict:
    ctx = {"user": user, "tabs": ACCOUNT_TABS, "base": billing.base_currency(db),
           "voucher_types": accounting.VOUCHER_TYPES, "voucher_labels": accounting.VOUCHER_LABELS,
           "voucher_statuses": accounting.VOUCHER_STATUSES}
    ctx.update(extra)
    return ctx


# ---------------------------------------------------------------------------- Setup 1: Accounts Heads
@router.get("/accounts/heads", include_in_schema=False)
def accounts_heads(request: Request, q: str = "", account_type: str = "", status: str = "",
                   db: Session = Depends(get_db), user: User = Depends(require("accounts.view"))):
    accounting.ensure_chart_of_accounts(db)
    db.commit()
    query = db.query(Account).filter(Account.is_head.is_(True))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Account.code.ilike(like), Account.name.ilike(like)))
    if account_type:
        query = query.filter(Account.account_type == account_type)
    if status:
        query = query.filter(Account.is_active.is_(status == "active"))
    heads = query.order_by(Account.account_type, Account.sort_no, Account.code).all()
    all_heads = db.query(Account).filter(Account.is_head.is_(True)).all()
    child_counts = dict(db.query(Account.parent_id, func.count(Account.id))
                        .filter(Account.parent_id.isnot(None)).group_by(Account.parent_id).all())
    by_type = {t: len([h for h in all_heads if h.account_type == t]) for t in accounting.ACCOUNT_TYPES}
    return render(request, "finance/accounts_heads.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "heads", "heads": heads, "q": q, "account_type": account_type,
        "status": status, "types": accounting.ACCOUNT_TYPES, "by_type": by_type, "child_counts": child_counts,
        "stats": {"total": len(all_heads), "active": len([h for h in all_heads if h.is_active]),
                  "inactive": len([h for h in all_heads if not h.is_active])},
        "parent_options": _head_options(db), "base": billing.base_currency(db),
        "next_sort": (db.query(func.max(Account.sort_no)).filter(Account.is_head.is_(True)).scalar() or 0) + 10,
        "can_add": rbac.has_permission(user, "accounts.add"),
        "can_edit": rbac.has_permission(user, "accounts.update")})


@router.post("/accounts/heads/new", include_in_schema=False)
async def accounts_head_create(request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require("accounts.add"))):
    form = await request.form()
    code = (form.get("code") or "").strip()
    name = (form.get("name") or "").strip()
    if not code or not name:
        return redirect("/finance/accounts/heads", "A head needs a code and a name.", "error")
    if db.query(Account).filter(Account.code == code).first():
        return redirect("/finance/accounts/heads", f"Account {code} already exists.", "error")
    head = Account(code=code, name=name, account_type=form.get("account_type") or "asset",
                   parent_id=parse_int(form.get("parent_id")) or None, description=form.get("description"),
                   is_head=True, is_postable=False, sort_no=parse_int(form.get("sort_no"), 0) or 0,
                   is_active=True, opening_balance=0)
    db.add(head)
    db.flush()
    log_action(db, user, "create", "accounts", entity=head, description=f"Accounts head {code} {name} created",
               after=snapshot(head), request=request)
    db.commit()
    return redirect("/finance/accounts/heads", f"Head {code} added.")


@router.post("/accounts/heads/{id}/edit", include_in_schema=False)
async def accounts_head_edit(id: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("accounts.update"))):
    head = _account(db, id)
    form = await request.form()
    before = snapshot(head)
    head.name = (form.get("name") or head.name).strip()
    head.account_type = form.get("account_type") or head.account_type
    head.parent_id = parse_int(form.get("parent_id")) or None
    head.sort_no = parse_int(form.get("sort_no"), head.sort_no or 0) or 0
    head.description = form.get("description") or head.description
    if head.parent_id == head.id:
        head.parent_id = None
    log_action(db, user, "update", "accounts", entity=head, description=f"Accounts head {head.code} updated",
               before=before, after=snapshot(head), request=request)
    db.commit()
    return redirect("/finance/accounts/heads", f"Head {head.code} updated.")


@router.post("/accounts/heads/{id}/toggle", include_in_schema=False)
async def accounts_head_toggle(id: int, request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require("accounts.update"))):
    head = _account(db, id)
    before = snapshot(head)
    head.is_active = not head.is_active
    log_action(db, user, "status_change", "accounts", entity=head,
               description=f"Accounts head {head.code} marked {'active' if head.is_active else 'inactive'}",
               before=before, after=snapshot(head), request=request)
    db.commit()
    return redirect("/finance/accounts/heads", f"Head {head.code} marked {'active' if head.is_active else 'inactive'}.")


@router.post("/accounts/{id}/mark-head", include_in_schema=False)
async def account_mark_head(id: int, request: Request, db: Session = Depends(get_db),
                            user: User = Depends(require("accounts.update"))):
    account = _account(db, id)
    form = await request.form()
    make_head = parse_bool(form.get("is_head"))
    back = form.get("back") or "/finance/accounts"
    if make_head and accounting.account_has_postings(db, account):
        return redirect(back, f"{account.code} {account.name} already carries postings and cannot become a head.",
                        "error")
    before = snapshot(account)
    account.is_head = make_head
    account.is_postable = not make_head
    log_action(db, user, "update", "accounts", entity=account,
               description=f"Account {account.code} marked {'a head' if make_head else 'postable'}",
               rationale=form.get("rationale") or form.get("reason"), before=before, after=snapshot(account),
               request=request)
    db.commit()
    return redirect(back, f"{account.code} is now {'a head' if make_head else 'postable'}.")


# ---------------------------------------------------------------------------- Setup 3: Accounts Tree View
@router.get("/accounts/tree", include_in_schema=False)
def accounts_tree(request: Request, as_of: str = "", print_view: int = 0, db: Session = Depends(get_db),
                  user: User = Depends(require("accounts.view"))):
    accounting.ensure_chart_of_accounts(db)
    db.commit()
    on = parse_date(as_of) or date.today()
    data = accounting.accounts_tree(db, on)
    return render(request, "finance/accounts_tree.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "tree", "data": data, "as_of": on.isoformat(),
        "base": data["currency"], "print_view": bool(print_view), "types": accounting.ACCOUNT_TYPES,
        "type_label": accounting.titleize_type})


# ---------------------------------------------------------------------------- Transactions: vouchers
@router.get("/accounts/vouchers", include_in_schema=False)
def vouchers_list(request: Request, page: int = 1, q: str = "", type: str = "", status: str = "",
                  date_from: str = "", date_to: str = "", account_id: str = "", party: str = "", currency: str = "",
                  db: Session = Depends(get_db), user: User = Depends(require("accounts.view"))):
    query = db.query(JournalEntry).filter(JournalEntry.voucher_number.isnot(None))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(JournalEntry.voucher_number.ilike(like), JournalEntry.description.ilike(like),
                                 JournalEntry.reference_no.ilike(like), JournalEntry.entry_number.ilike(like)))
    if type:
        query = query.filter(JournalEntry.voucher_type == type)
    if status:
        query = query.filter(JournalEntry.status == status)
    query = _date_range(query, JournalEntry.entry_date, date_from, date_to)
    if party:
        query = query.filter(JournalEntry.party_name.ilike(f"%{party}%"))
    if currency:
        query = query.filter(JournalEntry.currency == currency)
    acct_id = parse_int(account_id)
    if acct_id:
        query = query.filter(JournalEntry.id.in_(
            db.query(JournalLine.entry_id).filter(JournalLine.account_id == acct_id)))
    pg = paginate(query.order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc()), page, 25)
    qs = (f"q={q}&type={type}&status={status}&date_from={date_from}&date_to={date_to}"
          f"&account_id={account_id}&party={party}&currency={currency}")
    counts = accounting.voucher_status_counts(db)
    type_counts = accounting.voucher_type_counts(db)
    poster_ids = {v.posted_by_id for v in pg.items if v.posted_by_id}
    posters = ({u.id: u.full_name for u in db.query(User).filter(User.id.in_(poster_ids)).all()}
               if poster_ids else {})
    tabs = [("", f"All ({counts['total']})", f"/finance/accounts/vouchers?status={status}")]
    tabs += [(t, f"{accounting.VOUCHER_LABELS[t]} ({type_counts.get(t, 0)})",
              f"/finance/accounts/vouchers?type={t}&status={status}") for t in accounting.VOUCHER_TYPES]
    return render(request, "finance/vouchers_list.html", _voucher_ctx(db, user, {
        "tab": "vouchers", "page": pg, "q": q, "type": type, "status": status, "date_from": date_from,
        "date_to": date_to, "account_id": account_id, "party": party, "currency": currency, "counts": counts,
        "type_counts": type_counts, "type_tabs": tabs, "base_url": f"/finance/accounts/vouchers?{qs}", "qs": qs,
        "posters": posters,
        "accounts": _account_options(db, postable_only=False), "currencies": _currency_codes(db),
        "can_add": rbac.has_permission(user, "accounts.add")}))


@router.get("/accounts/vouchers/new", include_in_schema=False)
def voucher_new(request: Request, type: str = "journal", client_id: str = "", invoice_id: str = "",
                db: Session = Depends(get_db), user: User = Depends(require("accounts.add"))):
    if type not in accounting.VOUCHER_TYPES:
        type = "journal"
    base = billing.base_currency(db)
    inv = db.query(Invoice).get(parse_int(invoice_id)) if parse_int(invoice_id) else None
    return render(request, "finance/voucher_form.html", _voucher_ctx(db, user, {
        "tab": "vouchers", "type": type, "today_d": date.today(),
        "accounts": _account_options(db),
        "debit_accounts": _account_options(db, ("expense", "liability", "asset")),
        "credit_accounts": _account_options(db, ("income", "asset", "liability")),
        "beneficiaries": _beneficiary_options(db), "payment_modes": accounting.PAYMENT_MODES,
        "party_types": accounting.PARTY_TYPES, "clients": _client_options(db),
        "employees": _employee_options(db), "currencies": _currency_codes(db), "base_currency": base,
        "rates": {c.code: float(c.rate_to_base) for c in db.query(Currency).all()},
        "client_id": client_id, "invoice": inv,
        "open_invoices": [(i.id, f"{i.invoice_number} - {i.currency} {float(i.total) - float(i.paid_amount):,.2f}")
                          for i in db.query(Invoice).filter(Invoice.status.in_(["sent", "pending", "confirmed",
                                                                                "partial", "overdue"]))
                          .order_by(Invoice.due_date).limit(300).all()]}))


@router.post("/accounts/vouchers/new", include_in_schema=False)
async def voucher_create(request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("accounts.add"))):
    form = await request.form()
    vtype = (form.get("type") or "journal").strip()
    if vtype not in accounting.VOUCHER_TYPES:
        return redirect("/finance/accounts/vouchers", "Unknown voucher type.", "error")
    back = f"/finance/accounts/vouchers/new?type={vtype}"
    status = "draft" if (form.get("action") or "post") == "draft" else "posted"
    entry_date = parse_date(form.get("entry_date"), date.today())
    base = billing.base_currency(db)
    currency = (form.get("currency") or base).upper()
    rate = parse_float(form.get("exchange_rate"), 0) or billing.get_rate(db, currency)
    description = (form.get("description") or "").strip()
    reference_no = (form.get("reference_no") or "").strip() or None
    rationale = (form.get("rationale") or form.get("reason") or "").strip() or None

    party_type = (form.get("party_type") or "").strip() or None
    party_id, party_name = None, (form.get("party_name") or "").strip() or None
    if party_type == "client":
        c = db.query(Client).get(parse_int(form.get("client_id"))) if parse_int(form.get("client_id")) else None
        if not c:
            return redirect(back, "Choose the family this voucher is for.", "error")
        party_id, party_name = c.id, f"{c.client_code} {c.full_name}"
    elif party_type == "employee":
        e = db.query(Employee).get(parse_int(form.get("employee_id"))) if parse_int(form.get("employee_id")) else None
        if not e:
            return redirect(back, "Choose the employee this voucher is for.", "error")
        party_id, party_name = e.id, f"{e.employee_code} {e.full_name}"
    elif party_type in ("vendor", "other") and not party_name:
        return redirect(back, "Enter who was paid.", "error")

    beneficiary_id = parse_int(form.get("beneficiary_account_id")) or None
    beneficiary = db.query(BeneficiaryAccount).get(beneficiary_id) if beneficiary_id else None
    payment_mode = (form.get("payment_mode") or (beneficiary.payment_mode if beneficiary else "")) or None

    if vtype == "journal":
        codes = form.getlist("account_id")
        debits, credits, memos = form.getlist("debit"), form.getlist("credit"), form.getlist("memo")
        lines = []
        for i, aid in enumerate(codes):
            if not aid:
                continue
            d = parse_float(debits[i] if i < len(debits) else 0, 0)
            c = parse_float(credits[i] if i < len(credits) else 0, 0)
            if d or c:
                lines.append((parse_int(aid), d, c, memos[i] if i < len(memos) else None))
        if not description:
            description = "Journal voucher"
    else:
        amount = parse_float(form.get("amount"), 0)
        account_id = parse_int(form.get("account_id"))
        if amount <= 0:
            return redirect(back, "Enter an amount greater than zero.", "error")
        if not account_id:
            return redirect(back, "Choose the account to post against.", "error")
        try:
            settle = accounting.beneficiary_ledger_account(db, beneficiary, payment_mode or "")
        except ValueError as exc:
            return _err(back, exc)
        memo = (form.get("memo") or description or "").strip() or None
        if vtype == "payment":
            lines = [(account_id, amount, 0, memo), (settle.id, 0, amount, memo)]
            description = description or f"Payment to {party_name or 'vendor'}"
        else:
            lines = [(settle.id, amount, 0, memo), (account_id, 0, amount, memo)]
            description = description or f"Receipt from {party_name or 'client'}"

        # A receipt against a family may be applied to an outstanding invoice: the billing service owns that
        # allocation (ledger, receipt PDF, notification), so reuse it and adopt the journal it posts.
        invoice_id = parse_int(form.get("invoice_id"))
        if vtype == "receipt" and party_type == "client" and invoice_id:
            inv = db.query(Invoice).get(invoice_id)
            client = db.query(Client).get(party_id)
            if not inv or not client or inv.client_id != client.id:
                return redirect(back, "That invoice does not belong to the selected family.", "error")
            try:
                pay = billing.record_payment(
                    db, client, amount, currency, (payment_mode or "bank_transfer").lower().replace(" ", "_"),
                    reference_no, user=user, invoice=inv,
                    received_at=datetime.combine(entry_date, datetime.min.time()),
                    description=description, beneficiary_account=beneficiary, status="completed",
                    # The Receipts list shows where the money landed and who took it. A receipt raised
                    # through a voucher has the same columns to fill as one raised from the receipt form.
                    receipt_date=entry_date,
                    receiver_name=user.full_name or user.email,
                    receiving_destination=(beneficiary.account_name if beneficiary else payment_mode),
                    category=(beneficiary.category if beneficiary else payment_mode),
                    billing_rep=user)
            except ValueError as exc:
                return _err(back, exc)
            je = (db.query(JournalEntry).filter(JournalEntry.reference_type == "payment",
                                                JournalEntry.reference_id == pay.id)
                  .order_by(JournalEntry.id.desc()).first())
            if je is None:
                return redirect(back, "The receipt was recorded but no journal was posted.", "error")
            accounting.adopt_as_voucher(db, je, "receipt", user=user, party_type="client", party_id=client.id,
                                        party_name=party_name, payment_mode=payment_mode,
                                        beneficiary_account_id=beneficiary_id, reference_no=reference_no,
                                        exchange_rate=rate)
            log_action(db, user, "create", "accounts", entity=je,
                       description=f"Receipt voucher {je.voucher_number} {currency} {amount:,.2f} from {party_name}"
                                   f" applied to {inv.invoice_number}",
                       rationale=rationale, request=request)
            db.commit()
            return redirect(f"/finance/accounts/vouchers/{je.id}",
                            f"Receipt voucher {je.voucher_number} posted and applied to {inv.invoice_number}.")

    try:
        voucher = accounting.create_voucher(
            db, vtype, entry_date, description, lines, user=user, currency=currency, exchange_rate=rate,
            party_type=party_type, party_id=party_id, party_name=party_name, payment_mode=payment_mode,
            beneficiary_account_id=beneficiary_id, reference_no=reference_no, status=status)
    except ValueError as exc:
        return _err(back, exc)
    log_action(db, user, "create", "accounts", entity=voucher,
               description=f"{accounting.VOUCHER_LABELS[vtype]} {voucher.voucher_number} "
                           f"{currency} {float(voucher.total):,.2f} saved as {status}",
               rationale=rationale, after=snapshot(voucher), request=request,
               consequential=(status == "posted"))
    db.commit()
    return redirect(f"/finance/accounts/vouchers/{voucher.id}",
                    f"{accounting.VOUCHER_LABELS[vtype]} {voucher.voucher_number} {status}.")


@router.get("/accounts/vouchers/{id}", include_in_schema=False)
def voucher_detail(id: int, request: Request, print_view: int = 0, db: Session = Depends(get_db),
                   user: User = Depends(require("accounts.view"))):
    v = _voucher(db, id)
    reversal = (db.query(JournalEntry).filter(JournalEntry.reference_type == "reversal",
                                              JournalEntry.reference_id == v.id).first())
    original = (db.query(JournalEntry).get(v.reference_id)
                if v.reference_type == "reversal" and v.reference_id else None)
    events = (db.query(AuditEvent).filter(AuditEvent.entity_type == "JournalEntry", AuditEvent.entity_id == v.id)
              .order_by(AuditEvent.id.desc()).limit(20).all())
    rate = float(v.exchange_rate or 1) or 1
    return render(request, "finance/voucher_detail.html", _voucher_ctx(db, user, {
        "tab": "vouchers", "v": v, "reversal": reversal, "original": original, "events": events,
        "print_view": bool(print_view), "rate": rate,
        "amount_in_currency": round(float(v.total or 0) / rate, 2),
        "closed": accounting.period_is_closed(db, v.period or ""),
        "org": billing._org(db),
        "can_post": rbac.has_permission(user, "accounts.approve") or rbac.has_permission(user, "accounts.update"),
        "can_cancel": rbac.has_permission(user, "accounts.approve")}))


@router.post("/accounts/vouchers/{id}/post", include_in_schema=False)
async def voucher_post(id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("accounts.update"))):
    v = _voucher(db, id)
    form = await request.form()
    before = snapshot(v)
    try:
        accounting.post_voucher(db, v, user)
    except ValueError as exc:
        return _err(f"/finance/accounts/vouchers/{id}", exc)
    log_action(db, user, "post", "accounts", entity=v,
               description=f"{v.voucher_number} posted ({float(v.total):,.2f} {v.currency})",
               rationale=form.get("rationale") or form.get("reason"), before=before, after=snapshot(v),
               request=request, consequential=True)
    db.commit()
    return redirect(f"/finance/accounts/vouchers/{id}", f"{v.voucher_number} posted.")


@router.post("/accounts/vouchers/{id}/cancel", include_in_schema=False)
async def voucher_cancel(id: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("accounts.approve"))):
    v = _voucher(db, id)
    form = await request.form()
    reason = (form.get("reason") or form.get("rationale") or "").strip()
    if not reason:
        return redirect(f"/finance/accounts/vouchers/{id}", "A reason is required to cancel a voucher.", "error")
    before = snapshot(v)
    try:
        reversal = accounting.cancel_voucher(db, v, user, reason)
    except ValueError as exc:
        return _err(f"/finance/accounts/vouchers/{id}", exc)
    log_action(db, user, "cancel", "accounts", entity=v,
               description=f"{v.voucher_number} cancelled"
                           + (f"; reversed by {reversal.voucher_number}" if reversal else " (was a draft)"),
               rationale=reason, before=before, after=snapshot(v), request=request, consequential=True)
    db.commit()
    return redirect(f"/finance/accounts/vouchers/{id}",
                    f"{v.voucher_number} cancelled" + (f" and reversed by {reversal.voucher_number}." if reversal
                                                       else "."), "warning")


# ---------------------------------------------------------------------------- Reports
@router.get("/accounts/reports/ledger", include_in_schema=False)
def report_ledger(request: Request, account_id: str = "", date_from: str = "", date_to: str = "",
                  format: str = "", print_view: int = 0, db: Session = Depends(get_db),
                  user: User = Depends(require("accounts.view"))):
    start, end = _report_range(date_from, date_to)
    options = _account_options(db, postable_only=False)
    aid = parse_int(account_id) or (options[0][0] if options else None)
    account = db.query(Account).get(aid) if aid else None
    data = accounting.ledger_report(db, account, start, end) if account else None
    if format == "csv" and data:
        rows = [["Ledger Report", f"{account.code} {account.name}", f"{start} to {end}"],
                ["Date", "Voucher No", "Type", "Description", "Party", "Reference", "Debit", "Credit", "Balance"],
                ["", "", "", "Opening balance", "", "", "", "", f"{data['opening']:.2f}"]]
        rows += [[r["date"].isoformat(), r["voucher_number"], accounting.VOUCHER_LABELS.get(r["voucher_type"], "-"),
                  r["description"], r["party"] or "", r["reference"] or "", f"{r['debit']:.2f}",
                  f"{r['credit']:.2f}", f"{r['balance']:.2f}"] for r in data["rows"]]
        rows.append(["", "", "", "Closing balance", "", "", f"{data['total_debit']:.2f}",
                     f"{data['total_credit']:.2f}", f"{data['closing']:.2f}"])
        return _csv(rows, f"ledger-{account.code}-{start}-{end}.csv")
    return render(request, "finance/report_ledger.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "reports", "report_tabs": REPORT_TABS, "report": "ledger",
        "data": data, "account": account, "accounts": options, "account_id": str(aid or ""),
        "date_from": start.isoformat(), "date_to": end.isoformat(), "base": billing.base_currency(db),
        "print_view": bool(print_view),
        "qs": f"account_id={aid or ''}&date_from={start}&date_to={end}"})


@router.get("/accounts/reports/trial-balance", include_in_schema=False)
def report_trial_balance(request: Request, date_from: str = "", date_to: str = "", format: str = "",
                         include_empty: int = 0, print_view: int = 0, db: Session = Depends(get_db),
                         user: User = Depends(require("accounts.view"))):
    start, end = _report_range(date_from, date_to)
    data = accounting.trial_balance(db, start, end, include_empty=bool(include_empty))
    if format == "csv":
        rows = [["Trial Balance Report", f"{start} to {end}"],
                ["Account Code", "Account Name", "Type", "Head", "Opening", "Debit", "Credit", "Closing"]]
        rows += [[r["code"], r["name"], accounting.titleize_type(r["type"]), r["head"], f"{r['opening']:.2f}",
                  f"{r['debit']:.2f}", f"{r['credit']:.2f}", f"{r['closing']:.2f}"] for r in data["rows"]]
        rows.append(["", "TOTAL", "", "", f"{data['total_opening']:.2f}", f"{data['total_debit']:.2f}",
                     f"{data['total_credit']:.2f}", f"{data['total_closing']:.2f}"])
        return _csv(rows, f"trial-balance-{start}-{end}.csv")
    return render(request, "finance/report_trial_balance.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "reports", "report_tabs": REPORT_TABS, "report": "trial-balance",
        "data": data, "date_from": start.isoformat(), "date_to": end.isoformat(), "include_empty": include_empty,
        "base": data["currency"], "print_view": bool(print_view),
        "qs": f"date_from={start}&date_to={end}&include_empty={include_empty}"})


@router.get("/accounts/reports/income-statement", include_in_schema=False)
def report_income_statement(request: Request, date_from: str = "", date_to: str = "", format: str = "",
                            print_view: int = 0, db: Session = Depends(get_db),
                            user: User = Depends(require("accounts.view"))):
    start, end = _report_range(date_from, date_to)
    data = accounting.income_statement(db, start, end)
    if format == "csv":
        rows = [["Income Statement", f"{start} to {end}", f"compared with {data['prev_start']} to {data['prev_end']}"],
                ["Section", "Account Code", "Account Name", "Group", "Amount", "Previous", "Change"]]
        rows += [["Income", r["code"], r["name"], r["head"], f"{r['amount']:.2f}", f"{r['previous']:.2f}",
                  f"{r['delta']:.2f}"] for r in data["income"]]
        rows.append(["Income", "", "Total income", "", f"{data['total_income']:.2f}",
                     f"{data['previous']['total_income']:.2f}", f"{data['income_delta']:.2f}"])
        rows += [["Expense", r["code"], r["name"], r["head"], f"{r['amount']:.2f}", f"{r['previous']:.2f}",
                  f"{r['delta']:.2f}"] for r in data["expense"]]
        rows.append(["Expense", "", "Total expenses", "", f"{data['total_expense']:.2f}",
                     f"{data['previous']['total_expense']:.2f}", f"{data['expense_delta']:.2f}"])
        rows.append(["Net", "", "Net income", "", f"{data['net']:.2f}", f"{data['previous']['net']:.2f}",
                     f"{data['net_delta']:.2f}"])
        return _csv(rows, f"income-statement-{start}-{end}.csv")
    return render(request, "finance/report_income_statement.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "reports", "report_tabs": REPORT_TABS,
        "report": "income-statement", "data": data, "date_from": start.isoformat(), "date_to": end.isoformat(),
        "base": data["currency"], "print_view": bool(print_view), "qs": f"date_from={start}&date_to={end}"})


@router.get("/accounts/reports/balance-sheet", include_in_schema=False)
def report_balance_sheet(request: Request, date_from: str = "", date_to: str = "", format: str = "",
                         print_view: int = 0, db: Session = Depends(get_db),
                         user: User = Depends(require("accounts.view"))):
    start, end = _report_range(date_from, date_to)
    data = accounting.balance_sheet(db, end)
    if format == "csv":
        rows = [["Balance Sheet", f"as at {end}"], ["Section", "Account Code", "Account Name", "Amount"]]
        rows += [["Assets", r["code"], r["name"], f"{r['amount']:.2f}"] for r in data["assets"]]
        rows.append(["Assets", "", "Total assets", f"{data['total_assets']:.2f}"])
        rows += [["Liabilities", r["code"], r["name"], f"{r['amount']:.2f}"] for r in data["liabilities"]]
        rows.append(["Liabilities", "", "Total liabilities", f"{data['total_liabilities']:.2f}"])
        rows += [["Equity", r["code"], r["name"], f"{r['amount']:.2f}"] for r in data["equity"]]
        rows.append(["Equity", "", "Retained earnings", f"{data['retained_earnings']:.2f}"])
        rows.append(["Equity", "", "Total equity", f"{data['total_equity_with_earnings']:.2f}"])
        rows.append(["Check", "", "Liabilities + equity", f"{data['total_liabilities_equity']:.2f}"])
        rows.append(["Check", "", "Difference", f"{data['difference']:.2f}"])
        return _csv(rows, f"balance-sheet-{end}.csv")
    return render(request, "finance/report_balance_sheet.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "reports", "report_tabs": REPORT_TABS, "report": "balance-sheet",
        "data": data, "date_from": start.isoformat(), "date_to": end.isoformat(), "base": data["currency"],
        "print_view": bool(print_view), "qs": f"date_from={start}&date_to={end}"})


@router.get("/accounts/reports/payables", include_in_schema=False)
def report_payables(request: Request, date_from: str = "", date_to: str = "", format: str = "",
                    print_view: int = 0, db: Session = Depends(get_db),
                    user: User = Depends(require("accounts.view"))):
    start, end = _report_range(date_from, date_to)
    data = accounting.payables_summary(db, end)
    bucket_keys = [k for k, _ in data["buckets"]]
    if format == "csv":
        rows = [["Payables Summary", f"as at {end}"],
                ["Vendor", "Invoices", "Total"] + [lbl for _, lbl in data["buckets"]]]
        rows += [[v["vendor"], v["count"], f"{v['total']:.2f}"] + [f"{v['buckets'][k]:.2f}" for k in bucket_keys]
                 for v in data["vendors"]]
        rows.append(["TOTAL", data["count"], f"{data['total']:.2f}"] + [f"{data['totals'][k]:.2f}" for k in bucket_keys])
        rows.append([])
        rows.append(["Account", "Items", "Total"] + [lbl for _, lbl in data["buckets"]])
        rows += [[a["account"], a["count"], f"{a['total']:.2f}"] + [f"{a['buckets'][k]:.2f}" for k in bucket_keys]
                 for a in data["accounts"]]
        return _csv(rows, f"payables-summary-{end}.csv")
    return render(request, "finance/report_payables.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "reports", "report_tabs": REPORT_TABS, "report": "payables",
        "data": data, "bucket_keys": bucket_keys, "bucket_labels": [lbl for _, lbl in data["buckets"]],
        "date_from": start.isoformat(), "date_to": end.isoformat(),
        "base": data["currency"], "print_view": bool(print_view), "qs": f"date_from={start}&date_to={end}"})


@router.get("/accounts/reports/account-wise", include_in_schema=False)
def report_account_wise(request: Request, date_from: str = "", date_to: str = "", account_type: str = "",
                        head_id: str = "", format: str = "", print_view: int = 0, db: Session = Depends(get_db),
                        user: User = Depends(require("accounts.view"))):
    start, end = _report_range(date_from, date_to)
    data = accounting.account_wise_summary(db, start, end, account_type=account_type,
                                           head_id=parse_int(head_id) or None)
    if format == "csv":
        rows = [["Account Wise Summary", f"{start} to {end}"],
                ["Account Code", "Account Name", "Type", "Head", "Opening", "Debit", "Credit", "Closing"]]
        rows += [[r["code"], r["name"], accounting.titleize_type(r["type"]), r["head"], f"{r['opening']:.2f}",
                  f"{r['debit']:.2f}", f"{r['credit']:.2f}", f"{r['closing']:.2f}"] for r in data["rows"]]
        rows.append(["", "TOTAL", "", "", f"{data['total_opening']:.2f}", f"{data['total_debit']:.2f}",
                     f"{data['total_credit']:.2f}", f"{data['total_closing']:.2f}"])
        return _csv(rows, f"account-wise-{start}-{end}.csv")
    return render(request, "finance/report_account_wise.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "reports", "report_tabs": REPORT_TABS, "report": "account-wise",
        "data": data, "date_from": start.isoformat(), "date_to": end.isoformat(), "account_type": account_type,
        "head_id": head_id, "types": accounting.ACCOUNT_TYPES, "heads": _head_options(db),
        "base": data["currency"], "print_view": bool(print_view),
        "qs": f"date_from={start}&date_to={end}&account_type={account_type}&head_id={head_id}"})


@router.get("/accounts/reports/approved-advances", include_in_schema=False)
def report_approved_advances(request: Request, date_from: str = "", date_to: str = "", status: str = "",
                             format: str = "", print_view: int = 0, db: Session = Depends(get_db),
                             user: User = Depends(require("accounts.view"))):
    start, end = _report_range(date_from, date_to)
    data = accounting.approved_advances(db, status=status)
    rows = [r for r in data["rows"] if not r["request_date"] or start <= r["request_date"] <= end]
    totals = {"amount": round(sum(r["amount"] for r in rows), 2),
              "recovered": round(sum(r["recovered"] for r in rows), 2),
              "balance": round(sum(r["balance"] for r in rows), 2)}
    if format == "csv":
        out = [["Approved Advances", f"{start} to {end}"],
               ["Employee Code", "Employee", "Request Date", "Amount", "Currency", "Instalments",
                "Per Instalment", "Recovered", "Balance", "Status"]]
        out += [[r["employee_code"], r["employee_name"], r["request_date"].isoformat() if r["request_date"] else "",
                 f"{r['amount']:.2f}", r["currency"], r["installments"], f"{r['per_installment']:.2f}",
                 f"{r['recovered']:.2f}", f"{r['balance']:.2f}", r["status"]] for r in rows]
        out.append(["", "TOTAL", "", f"{totals['amount']:.2f}", "", "", "", f"{totals['recovered']:.2f}",
                    f"{totals['balance']:.2f}", ""])
        return _csv(out, f"approved-advances-{start}-{end}.csv")
    return render(request, "finance/report_advances.html", {
        "user": user, "tabs": ACCOUNT_TABS, "tab": "reports", "report_tabs": REPORT_TABS,
        "report": "approved-advances", "rows": rows, "totals": totals, "status": status,
        "statuses": ["approved", "paid", "settled"], "date_from": start.isoformat(), "date_to": end.isoformat(),
        "base": data["currency"], "print_view": bool(print_view),
        "qs": f"date_from={start}&date_to={end}&status={status}"})


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
