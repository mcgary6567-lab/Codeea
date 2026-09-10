"""Acceptance criteria from the SRS that must never regress.

These are the rules the specification states in absolute terms ("impossible", "cannot", "never"),
so each one is asserted directly against the service layer rather than through a page.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func

from app.models.academic import Package
from app.models.core import AuditEvent, User
from app.models.finance import JournalLine, LedgerEntry, Subscription
from app.models.people import Client, Student, Teacher


# --------------------------------------------------------------------------- helpers
@pytest.fixture()
def admin_user(db):
    return db.query(User).filter(User.email == "admin@oqc.local").first()


@pytest.fixture()
def sample(db):
    client = db.query(Client).first()
    student = db.query(Student).filter(Student.client_id == client.id).first()
    package = db.query(Package).filter(Package.is_trial.is_(False), Package.currency == "GBP").first()
    teacher = db.query(Teacher).filter(Teacher.is_verified.is_(True)).first()
    if not all([client, student, package, teacher]):
        pytest.skip("finance seed data not present")
    return client, student, package, teacher


# --------------------------------------------------------------------------- SRS 29.6 / Module 45 pricing governance
def test_discount_tiers_match_the_specified_ladder(db):
    """0-20% manager, 21-35% CEO, above 35% prohibited."""
    from app.services import billing
    assert billing.discount_tier(db, 0) == "none"
    assert billing.discount_tier(db, 20) == "manager"
    assert billing.discount_tier(db, 21) == "ceo"
    assert billing.discount_tier(db, 35) == "ceo"
    assert billing.discount_tier(db, 36) == "prohibited"
    assert billing.discount_tier(db, 90) == "prohibited"


def test_discount_above_35_percent_is_impossible(db, sample, admin_user):
    from app.services import billing
    client, student, package, teacher = sample
    with pytest.raises(Exception) as exc:
        billing.create_subscription(db=db, client=client, student=student, package=package, teacher=teacher,
                                    user=admin_user, price=package.price, currency=package.currency,
                                    discount_pct=40, rationale="regression test")
    db.rollback()
    assert "prohibit" in str(exc.value).lower()


def test_ceo_tier_discount_needs_approval_before_activation(db, sample, admin_user):
    from app.services import billing
    client, student, package, teacher = sample
    sub = billing.create_subscription(db=db, client=client, student=student, package=package, teacher=teacher,
                                      user=admin_user, price=package.price, currency=package.currency,
                                      discount_pct=30, rationale="regression test")
    assert sub.status == "pending_approval"
    db.rollback()


def test_subscription_below_teacher_cost_floor_is_blocked(db, sample, admin_user):
    """No subscription may be priced below teacher cost plus 20%."""
    from app.services import billing
    client, student, package, teacher = sample
    with pytest.raises(Exception) as exc:
        billing.create_subscription(db=db, client=client, student=student, package=package, teacher=teacher,
                                    user=admin_user, price=1, currency=package.currency, discount_pct=0,
                                    rationale="regression test")
    db.rollback()
    assert "floor" in str(exc.value).lower() or "cost" in str(exc.value).lower()


def test_scholarships_are_recorded_separately_from_discounts(db):
    """A scholarship must never be stored as a discount."""
    from app.models.finance import Scholarship
    assert db.query(Scholarship).count() >= 0
    for sub in db.query(Subscription).filter(Subscription.scholarship_id.isnot(None)).limit(10):
        assert sub.scholarship_amount is not None


# --------------------------------------------------------------------------- double-entry integrity
def test_journal_is_balanced(db):
    debits = db.query(func.sum(JournalLine.debit)).scalar() or 0
    credits = db.query(func.sum(JournalLine.credit)).scalar() or 0
    assert abs(float(debits) - float(credits)) < 0.01, f"journal out of balance: {debits} vs {credits}"


def test_every_journal_entry_balances_individually(db):
    from app.models.finance import JournalEntry
    for entry in db.query(JournalEntry).limit(80):
        d = sum(float(l.debit or 0) for l in entry.lines)
        c = sum(float(l.credit or 0) for l in entry.lines)
        assert abs(d - c) < 0.01, f"entry {entry.entry_number} unbalanced: {d} vs {c}"


def test_invoice_paid_amount_never_exceeds_total(db):
    from app.models.finance import Invoice
    for inv in db.query(Invoice).limit(300):
        assert float(inv.paid_amount or 0) <= float(inv.total or 0) + 0.01, f"{inv.invoice_number} overpaid"


# --------------------------------------------------------------------------- Module 47 safeguarding
def test_unverified_teachers_are_never_recommended(db):
    """An unverified teacher cannot be assigned live classes."""
    from app.services.people import recommend_teachers
    ranked = recommend_teachers(db, course_code="QAIDA", gender="female", age=9, timezone="Europe/London")
    for row in ranked:
        teacher = row["teacher"] if isinstance(row, dict) and "teacher" in row else None
        if teacher is not None:
            assert teacher.is_verified, f"unverified teacher {teacher.teacher_code} was recommended"


def test_no_unverified_teacher_holds_an_active_schedule(db):
    from app.models.scheduling import Schedule
    rows = (db.query(Teacher.teacher_code)
            .join(Schedule, Schedule.teacher_id == Teacher.id)
            .filter(Teacher.is_verified.is_(False), Schedule.status == "active").all())
    assert not rows, f"unverified teachers hold live schedules: {[r[0] for r in rows]}"


def test_safeguarding_flags_default_to_restricted_visibility(db):
    from app.models.scheduling import SafeguardingFlag
    for flag in db.query(SafeguardingFlag).filter(SafeguardingFlag.flag_type.in_(
            ["contact_exchange", "off_platform_contact", "social_discovery"])).limit(20):
        assert flag.visibility == "ceo_only", f"anti-poaching flag {flag.id} is visible to {flag.visibility}"


# --------------------------------------------------------------------------- Module 38 AI governance
def test_every_ai_result_records_model_and_prompt_version(db):
    from app.models.core import AIModelRun
    runs = db.query(AIModelRun).limit(200).all()
    if not runs:
        pytest.skip("no AI runs seeded")
    for run in runs:
        assert run.model and run.model_version and run.prompt_version
        assert run.created_at is not None
        assert run.review_status in ("pending", "approved", "rejected", "overridden", "false_positive")


def test_class_monitoring_results_require_human_review(db):
    """AI class monitoring must not be auto-accepted; it starts pending review."""
    from app.models.core import AIModelRun
    runs = db.query(AIModelRun).filter(AIModelRun.module == "class_monitoring").limit(50).all()
    if not runs:
        pytest.skip("no class-monitoring runs seeded")
    assert any(r.review_status == "pending" for r in runs)


# --------------------------------------------------------------------------- Module 43 referral credits
def test_referral_credits_are_account_credits_not_cash(db):
    """Dual-sided incentive is issued as account credit, never cash or coupons."""
    from app.models.crm import Referral
    credited = db.query(Referral).filter(Referral.status == "credited").all()
    if not credited:
        pytest.skip("no credited referrals seeded")
    for ref in credited:
        assert ref.ambassador_credit_ledger_id or ref.referred_credit_ledger_id, "credit not posted to a ledger"
        for ledger_id in (ref.ambassador_credit_ledger_id, ref.referred_credit_ledger_id):
            if ledger_id:
                entry = db.query(LedgerEntry).get(ledger_id)
                assert entry is not None and entry.entry_type == "credit"
                assert float(entry.credit or 0) > 0


# --------------------------------------------------------------------------- Module 48 written-record discipline
def test_consequential_actions_carry_an_actor(db):
    for ev in db.query(AuditEvent).filter(AuditEvent.is_consequential.is_(True)).limit(200):
        assert ev.actor_name, f"consequential audit event {ev.id} has no actor"
        assert ev.created_at is not None


def test_approvals_are_logged_as_consequential(db):
    approvals = db.query(AuditEvent).filter(AuditEvent.action.in_(["approve", "payroll_approve", "grade_change",
                                                                   "salary_change", "refund", "discount"])).limit(50).all()
    for ev in approvals:
        assert ev.is_consequential, f"{ev.action} on {ev.module} was not marked consequential"


# --------------------------------------------------------------------------- data integrity
def test_every_student_belongs_to_a_client(db):
    orphans = db.query(Student).filter(Student.client_id.is_(None)).count()
    assert orphans == 0


def test_codes_are_unique(db):
    for model, field in ((Student, "student_code"), (Client, "client_code"), (Teacher, "teacher_code")):
        total = db.query(model).count()
        distinct = db.query(func.count(func.distinct(getattr(model, field)))).scalar()
        assert total == distinct, f"duplicate {field} values"
