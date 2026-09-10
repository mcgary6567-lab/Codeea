"""Finance seed: chart of accounts, subscriptions with the discount ladder, six months of invoices, payments,
receipts, ledger credits, expenses, budgets, exchange-rate history and closed financial periods.

Idempotent: students that already have a subscription are skipped and nothing is duplicated.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.utils import month_bounds, month_key
from app.models.academic import Package
from app.models.core import User, Department
from app.models.finance import (Budget, Currency, DiscountRequest, ExchangeRateHistory, Expense, FinancialPeriod,
                                Invoice, LedgerEntry, Payment, Scholarship, Subscription)
from app.models.people import Client, Student
from app.services import accounting, billing

SEED = 20260101

# Payroll is owned by the HR module (it posts its own payroll journals), so it is deliberately absent here.
EXPENSE_PLAN = [  # category, vendor, min, max, per-month count
    ("marketing", "Meta Platforms Ireland", 26000, 48000, 2),
    ("marketing", "Google Ads", 15000, 32000, 1),
    ("software", "Zoom Video Communications", 9000, 14000, 1),
    ("software", "GoHighLevel", 12000, 18000, 1),
    ("internet", "PTCL Business Fibre", 18000, 26000, 1),
    ("utilities", "K-Electric", 15000, 26000, 1),
    ("office", "Office supplies & maintenance", 8000, 18000, 1),
    ("bank_charges", "Bank transaction charges", 1500, 5000, 1),
]

BUDGET_PLAN = [("marketing", 105000), ("software", 30000), ("internet", 24000), ("utilities", 24000),
               ("office", 16000), ("bank_charges", 4000)]


def _months(n: int = 6) -> list[tuple[date, date]]:
    """Last ``n`` months as (start, end), oldest first, ending with the current month."""
    today = date.today()
    out = []
    for i in range(n - 1, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        out.append(month_bounds(f"{y:04d}-{m:02d}"))
    return out


def _user(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


def _pick_package(packages: list[Package], client: Client) -> Package:
    for p in packages:
        if p.country == client.country:
            return p
    for p in packages:
        if p.currency == client.currency:
            return p
    return packages[0]


def _fit_sessions(db: Session, teacher, price_in_base: float, wanted: int) -> int:
    """Largest sessions/week (down to 2) that keeps the price above the teacher-cost floor."""
    for spw in range(int(wanted), 1, -1):
        if price_in_base >= billing.teacher_cost_floor(db, teacher, spw):
            return spw
    return 2


# --------------------------------------------------------------------------- exchange rates
def _seed_rates(db: Session, rnd: random.Random, admin: User | None) -> None:
    if db.query(ExchangeRateHistory).count():
        return
    today = date.today()
    for c in db.query(Currency).order_by(Currency.code).all():
        base_rate = float(c.rate_to_base)
        for i in range(6, 0, -1):
            drift = 1.0 if c.is_base else (1 + rnd.uniform(-0.04, 0.04))
            db.add(ExchangeRateHistory(currency_code=c.code, rate_to_base=round(base_rate * drift, 6),
                                       source="api", set_by_id=admin.id if admin else None,
                                       effective_at=datetime.combine(today - timedelta(days=30 * i), datetime.min.time())))
        db.add(ExchangeRateHistory(currency_code=c.code, rate_to_base=base_rate, source="seed",
                                   set_by_id=admin.id if admin else None, effective_at=datetime.utcnow()))
    db.flush()


# --------------------------------------------------------------------------- scholarships
def _seed_scholarships(db: Session, rnd: random.Random, clients: list[Client], hod_finance: User | None,
                       admin: User | None) -> list[Scholarship]:
    existing = db.query(Scholarship).all()
    if existing:
        return [s for s in existing if s.status == "approved"]
    picks = [c for c in clients if c.students][:24]
    out = []
    specs = [("orphan", 50.0, "Orphaned learner supported by the college endowment.", True),
             ("need_based", 35.0, "Family income reduced after redundancy; support agreed for two terms.", True),
             ("hafiz", 25.0, "Hifz student of outstanding consistency recommended by the academic HOD.", False)]
    for i, (kind, pct, reason, approve) in enumerate(specs):
        client = picks[(i * 7) % len(picks)] if picks else None
        if not client:
            break
        student = client.students[0] if client.students else None
        s = billing.request_scholarship(db, client, student, kind, pct, 0, client.currency, reason, hod_finance)
        if approve:
            billing.decide_scholarship(db, s, admin, True, "Approved by the CEO after a confidential review.")
            out.append(s)
    db.flush()
    return out


# --------------------------------------------------------------------------- subscriptions
def _seed_subscriptions(db: Session, rnd: random.Random, admin: User, manager: User | None, closer: User | None,
                        scholarships: list[Scholarship]) -> list[Subscription]:
    packages = (db.query(Package).filter(Package.is_active.is_(True), Package.is_trial.is_(False))
                .order_by(Package.price).all())
    if not packages:
        return []
    have = {s.student_id for s in db.query(Subscription.student_id).all()}
    students = (db.query(Student).filter(Student.status.in_(["active", "frozen", "trial"]))
                .order_by(Student.id).all())
    todo = [s for s in students if s.id not in have]
    schol_by_client = {s.client_id: s for s in scholarships}

    # discount plan: 5 approved manager-tier, 2 pending manager, 1 pending CEO
    approved_idx = {2, 6, 11, 17, 23}
    pending_mgr_idx = {4, 9}
    pending_ceo_idx = {14}

    created = []
    for i, st in enumerate(todo):
        client = st.client
        if not client:
            continue
        pkg = _pick_package(packages, client)
        price = float(pkg.price)
        currency = pkg.currency
        teacher = st.teacher
        price_base = billing.convert_to_base(db, price, currency)
        spw = _fit_sessions(db, teacher, price_base, pkg.sessions_per_week)
        discount, rationale, schol = 0.0, None, None
        if i in approved_idx:
            discount = rnd.choice([10.0, 12.5, 15.0, 20.0])
            rationale = rnd.choice(["Second sibling enrolling on the same schedule.",
                                    "Long-standing family returning after a pause.",
                                    "Annual prepayment agreed with the family."])
        elif i in pending_mgr_idx:
            discount = rnd.choice([10.0, 18.0])
            rationale = "Family requested help with the monthly fee while a parent is between jobs."
        elif i in pending_ceo_idx:
            discount = 30.0
            rationale = "Three siblings enrolling together; closer requested an exceptional rate."
        if client.id in schol_by_client and discount == 0:
            schol = schol_by_client.pop(client.id)
        # keep the final price above the floor after the discount
        while discount > 0 and billing.convert_to_base(db, price * (1 - discount / 100.0), currency) < \
                billing.teacher_cost_floor(db, teacher, spw) and spw > 2:
            spw -= 1
        try:
            sub = billing.create_subscription(db, client=client, student=st, package=pkg, price=price, currency=currency,
                                              discount_pct=discount, teacher=teacher,
                                              user=(closer if discount else admin), rationale=rationale,
                                              scholarship=schol, sessions_per_week=spw,
                                              start_date=max(st.join_date, date.today() - timedelta(days=210)))
        except ValueError:
            continue
        if st.status == "frozen":
            sub.status = "frozen"
            sub.freeze_start = date.today() - timedelta(days=20)
            sub.freeze_end = date.today() + timedelta(days=25)
        created.append(sub)
        if i in approved_idx:
            req = (db.query(DiscountRequest).filter(DiscountRequest.subscription_id == sub.id,
                                                    DiscountRequest.status == "pending").first())
            if req:
                billing.approve_discount(db, req, manager or admin, True,
                                         "Within the manager threshold; margin remains above the floor.")
    db.flush()
    return created


# --------------------------------------------------------------------------- billing history
def _seed_billing(db: Session, rnd: random.Random, billing_user: User, admin: User) -> None:
    if db.query(Invoice).count():
        return
    months = _months(6)
    subs = db.query(Subscription).filter(Subscription.status.in_(["active", "frozen"])).order_by(Subscription.id).all()

    # a few families carry credit before the first invoice so credit application is visible
    for sub in subs[:4]:
        billing.post_credit(db, sub.client, round(float(sub.price) * 0.25, 2), sub.currency,
                            "Ambassador referral reward — one free week", reference_type="referral", user=admin)

    overdue_left, partial_left, void_left = 8, 10, 3
    for sub in subs:
        for k, (ps, pe) in enumerate(months):
            if ps < sub.start_date.replace(day=1):
                continue
            try:
                inv = billing.generate_invoice(db, sub, ps, pe, user=billing_user, issue_date=ps)
            except ValueError:
                continue
            roll = rnd.random()
            if void_left and k not in (len(months) - 1,) and roll > 0.985:
                billing.void_invoice(db, inv, admin, "Issued twice after a package change; corrected invoice re-sent.")
                void_left -= 1
                continue
            if k == len(months) - 1:  # current month
                if roll < 0.55:
                    billing.record_payment(db, sub.client, float(inv.total), inv.currency,
                                           rnd.choice(["card", "bank_transfer", "stripe", "paypal"]),
                                           f"REF{rnd.randint(100000, 999999)}", user=billing_user, invoice=inv,
                                           gateway=rnd.choice([None, "stripe", "paypal"]),
                                           received_at=datetime.combine(min(date.today(), inv.due_date), datetime.min.time()))
                continue
            if partial_left and roll < 0.09:
                billing.record_payment(db, sub.client, round(float(inv.total) * rnd.uniform(0.35, 0.6), 2), inv.currency,
                                       "bank_transfer", f"REF{rnd.randint(100000, 999999)}", user=billing_user,
                                       invoice=inv, received_at=datetime.combine(inv.due_date, datetime.min.time()))
                partial_left -= 1
                continue
            if overdue_left and roll < 0.155:
                inv.status = "overdue"
                inv.reminder_count = rnd.randint(1, 3)
                inv.last_reminder_at = datetime.combine(inv.due_date + timedelta(days=3), datetime.min.time())
                overdue_left -= 1
                continue
            paid_on = inv.due_date - timedelta(days=rnd.randint(0, 5))
            billing.record_payment(db, sub.client, float(inv.total), inv.currency,
                                   rnd.choice(["card", "bank_transfer", "stripe", "wise", "paypal"]),
                                   f"REF{rnd.randint(100000, 999999)}", user=billing_user, invoice=inv,
                                   gateway=rnd.choice([None, "stripe", "paypal", "wise"]),
                                   received_at=datetime.combine(paid_on, datetime.min.time()))
        db.flush()

    # reconcile the older payments so the reconciliation queue is realistic
    cutoff = datetime.utcnow() - timedelta(days=45)
    for p in db.query(Payment).filter(Payment.status == "completed", Payment.received_at < cutoff).all():
        p.reconciled = True
        p.reconciled_at = p.received_at + timedelta(days=2)

    # one failed gateway payment to feed the failed-payments queue
    open_inv = db.query(Invoice).filter(Invoice.status.in_(["overdue", "sent"])).first()
    if open_inv:
        billing.record_payment(db, open_inv.client, float(open_inv.total), open_inv.currency, "card",
                               f"CH{rnd.randint(100000, 999999)}", user=billing_user, invoice=None, gateway="stripe",
                               received_at=datetime.utcnow() - timedelta(days=2), status="failed",
                               notes="Card declined by the issuing bank (insufficient funds).")
    db.flush()


def _freeze_a_few(db: Session, admin: User) -> None:
    """Two families pause over the school holidays so the frozen workflow has real data."""
    if db.query(Subscription).filter(Subscription.status == "frozen").count():
        return
    subs = db.query(Subscription).filter(Subscription.status == "active").order_by(Subscription.id).all()
    reasons = ["Family travelling for Umrah; classes paused for four weeks.",
               "Student sitting school exams; parents asked to pause until the term ends."]
    for sub, reason in zip(subs[5:9:3], reasons):
        billing.freeze_subscription(db, sub, date.today() - timedelta(days=8), date.today() + timedelta(days=22),
                                    admin, reason)
    db.flush()


def _seed_credits(db: Session, rnd: random.Random, clients: list[Client], admin: User) -> None:
    if db.query(LedgerEntry).filter(LedgerEntry.entry_type == "credit").count() >= 8:
        return
    reasons = [("Ambassador referral reward — one free week", "referral"),
               ("Goodwill credit after a rescheduled week of classes", "manual"),
               ("Service recovery: teacher change mid-month", "manual"),
               ("Referral credit for the newly enrolled family", "referral")]
    picked = [c for c in clients if c.students][8:20]
    for i, c in enumerate(picked[:8]):
        text, ref = reasons[i % len(reasons)]
        billing.post_credit(db, c, round(rnd.uniform(8, 30), 2), c.currency, text, reference_type=ref, user=admin)
    db.flush()


# --------------------------------------------------------------------------- expenses & budgets
def _seed_expenses(db: Session, rnd: random.Random, admin: User, accountant: User | None) -> None:
    if db.query(Expense).count():
        return
    finance_dept = db.query(Department).filter(Department.code == "finance").first()
    depts = db.query(Department).order_by(Department.id).all()
    dept_for = {"marketing": db.query(Department).filter(Department.code == "marketing").first(),
                "software": db.query(Department).filter(Department.code == "technology").first(),
                "internet": db.query(Department).filter(Department.code == "technology").first(),
                "utilities": finance_dept, "office": finance_dept, "bank_charges": finance_dept}
    months = _months(6)
    submitter = accountant or admin
    for mi, (ps, pe) in enumerate(months):
        current_month = mi == len(months) - 1
        for category, vendor, lo, hi, count in EXPENSE_PLAN:
            for n in range(count):
                day = min(pe.day, rnd.randint(2, 26))
                when = date(ps.year, ps.month, day)
                dept = dept_for.get(category) or (depts[0] if depts else None)
                e = accounting.record_expense(db, category, round(rnd.uniform(lo, hi), 2),
                                              billing.base_currency(db), when, user=submitter,
                                              department_id=dept.id if dept else None, vendor=vendor,
                                              description=f"{vendor} — {ps:%B %Y}")
                roll = rnd.random()
                if current_month and roll < 0.35:
                    continue  # leave pending in the open period
                if roll > 0.96:
                    accounting.reject_expense(db, e, admin, "Duplicate submission; already captured in payroll.")
                    continue
                accounting.approve_expense(db, e, admin, "Within budget and supported by the vendor invoice.")
                if rnd.random() < 0.75:
                    accounting.pay_expense(db, e, admin, f"TT{rnd.randint(100000, 999999)}")
        db.flush()


def _seed_budgets(db: Session, rnd: random.Random) -> None:
    base = billing.base_currency(db)
    months = _months(3)
    for ps, _ in months:
        period = month_key(ps)
        for category, amount in BUDGET_PLAN:
            if db.query(Budget).filter(Budget.period == period, Budget.category == category,
                                       Budget.department_id.is_(None)).first():
                continue
            db.add(Budget(period=period, category=category, department_id=None,
                          amount=round(amount * rnd.uniform(0.95, 1.12), 2), currency=base,
                          notes="Departmental plan approved in the monthly finance review."))
    db.flush()


def _close_periods(db: Session, admin: User) -> None:
    months = _months(6)
    for ps, _ in months:  # make sure every recent month exists on the close page
        accounting.get_period(db, month_key(ps))
    # close only the two oldest months: later seeds (payroll) still post into the recent ones,
    # and the close workflow stays demonstrable on the months that remain open
    for ps, _ in months[:2]:
        period = month_key(ps)
        fp = db.query(FinancialPeriod).filter(FinancialPeriod.period == period).first()
        if fp and fp.status == "closed":
            continue
        try:
            accounting.close_period(db, period, admin, "Month-end close reviewed with the HOD Finance.")
        except ValueError:
            continue
    db.flush()


# --------------------------------------------------------------------------- entry point
def run(db: Session) -> None:
    rnd = random.Random(SEED)
    admin = _user(db, "admin@oqc.local")
    manager = _user(db, "manager@oqc.local")
    closer = _user(db, "closer@oqc.local")
    billing_user = _user(db, "billing@oqc.local") or admin
    accountant = _user(db, "accountant@oqc.local")
    hod_finance = _user(db, "finance@oqc.local")
    if not admin:
        return

    accounting.ensure_chart_of_accounts(db)
    db.flush()
    _seed_rates(db, rnd, admin)

    clients = db.query(Client).order_by(Client.id).all()
    scholarships = _seed_scholarships(db, rnd, clients, hod_finance, admin)
    _seed_subscriptions(db, rnd, admin, manager, closer, scholarships)
    db.commit()

    _seed_billing(db, rnd, billing_user, admin)
    db.commit()

    _freeze_a_few(db, admin)
    _seed_credits(db, rnd, clients, admin)
    _seed_expenses(db, rnd, admin, accountant)
    _seed_budgets(db, rnd)
    db.commit()

    _close_periods(db, admin)
    db.commit()
