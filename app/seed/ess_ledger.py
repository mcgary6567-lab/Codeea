"""Back-fill the Employee Self Portal's Account Ledger from history the other seeds already created.

The ledger (docs/AUDIT_EMPLOYEE_SELF_PORTAL.md, `essledger`) is written by events, not by hand: a payroll
run being posted, an advance being approved and then recovered, a bonus being approved, a violation's fine
being confirmed. Those events all happened in `seed.hr` / `seed.hr_erp` / `seed.hr_recruitment` before this
page existed, so nothing would appear on the page for a freshly seeded database. This walks that history
and posts the lines the live routes would have posted.

Idempotent: every line is keyed on reference_type + reference_id + source and
`app.services.hr.post_ledger_entry` refuses to write a key that is already there, so running this twice
(or running it after the routes have already posted something) creates nothing.

Run order: after "hr", "hr_erp", "hr_attendance" and "hr_recruitment" — it reads what they made.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.hr_erp import EmployeeLedgerEntry
from app.models.people import Bonus, Employee, PayrollRun, SalaryAdvance, Violation

# A run only reaches an employee's account once it is committed: the ERP's "posted", and the older
# approved/paid vocabulary that the same table still carries.
POSTED_RUN_STATUSES = ("posted", "approved", "paid")
PAID_ADVANCE_STATUSES = ("approved", "paid", "settled")


def run(db: Session) -> None:
    from app.services import hr as hr_svc
    from app.services import payroll as pay

    before = db.query(EmployeeLedgerEntry).count()

    # 1. Advances first: the credit is dated when the advance was requested, before any recovery.
    advances = (db.query(SalaryAdvance)
                .filter(SalaryAdvance.status.in_(PAID_ADVANCE_STATUSES))
                .order_by(SalaryAdvance.request_date, SalaryAdvance.id).all())
    for a in advances:
        hr_svc.post_advance_ledger(db, a)

    # 2. Bonuses and violations, as their approvals would have posted them.
    for b in db.query(Bonus).filter(Bonus.status == "approved").order_by(Bonus.id).all():
        hr_svc.post_bonus_ledger(db, b)
    for v in (db.query(Violation).filter(Violation.approval_status == "approved")
              .order_by(Violation.date, Violation.id).all()):
        hr_svc.post_violation_ledger(db, v)

    # 3. Every committed payroll run: salary earned, the deductions, and each advance instalment recovered.
    runs = (db.query(PayrollRun).filter(PayrollRun.status.in_(POSTED_RUN_STATUSES))
            .order_by(PayrollRun.period, PayrollRun.id).all())
    for r in runs:
        pay.post_run_to_employee_ledgers(db, r)
        # A run that was paid clears from the ledger. Without this the balance is every salary ever
        # earned rather than what a member of staff is still waiting for.
        if r.status in ("paid", "posted"):
            pay.post_run_payment_to_employee_ledgers(db, r)

    db.flush()

    rows = db.query(EmployeeLedgerEntry).all()
    created = len(rows) - before
    by_source: dict[str, int] = {}
    balances: dict[int, float] = {}
    for e in rows:
        by_source[e.source] = by_source.get(e.source, 0) + 1
        balances[e.employee_id] = round(balances.get(e.employee_id, 0.0)
                                        + float(e.debit or 0) - float(e.credit or 0), 2)
    staff = db.query(Employee).count()
    detail = ", ".join(f"{k} {v}" for k, v in sorted(by_source.items())) or "none"
    owed = round(sum(v for v in balances.values() if v > 0), 0)
    print(f"    ess_ledger: {created} new ledger line(s), {len(rows)} total ({detail}) across "
          f"{len(balances)} of {staff} employees; {len(runs)} payroll run(s), {len(advances)} advance(s); "
          f"net owed to staff {owed:,.0f} PKR")
