"""Spread the ERP status vocabulary across the demo data.

The original seed only produced a narrow set of statuses (families active / trial / churned,
subscriptions active / frozen), so the ERP status tiles — On Leave, Pass Out, Black List,
Cancelled, Completed — rendered as zero on every list and dashboard. This runs last and moves a
realistic slice of the demo records onto the wider vocabulary that ``app.core.templating.STATUS_LABELS``
renders, so every tile shows a believable number.

Idempotent: it only ever converts records that are still on the narrow vocabulary, and stops as soon
as the targets below are met.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.finance import Subscription
from app.models.people import Client, Student

# target counts, chosen to look like a small college's book of business
CLIENT_TARGETS = {"on_leave": 4, "pass_out": 3, "inactive": 2}      # inactive renders as "Black List"
STUDENT_TARGETS = {"graduated": 4, "free": 3}                        # graduated renders as "Pass Out"
SUBSCRIPTION_TARGETS = {"cancelled": 6, "expired": 4}                # expired renders as "Completed"

CLIENT_LEAVE_REMARKS = [
    "Family travelling for Hajj, classes resume next month.",
    "Paused for school exams at the parents' request.",
    "Relocating; asked to hold the slots for four weeks.",
    "Ramadan break agreed with the academic manager.",
]
CANCEL_REASONS = [
    "Family moved to a different time zone and could not keep the slot.",
    "Student joined a local madrasah.",
    "Fees not settled after three reminders.",
    "Parent requested a pause that ran past the freeze window.",
    "Teacher change requested twice; family chose to stop.",
    "Course completed early, no follow-on enrolment.",
]


def _convert(db: Session, model, from_statuses: list[str], to_status: str, want: int, apply=None) -> int:
    """Move up to ``want`` records onto ``to_status``, counting what is already there."""
    have = db.query(func.count(model.id)).filter(model.status == to_status).scalar() or 0
    if have >= want:
        return 0
    rows = (db.query(model).filter(model.status.in_(from_statuses))
            .order_by(model.id.desc()).limit(want - have).all())
    for i, row in enumerate(rows):
        row.status = to_status
        if apply is not None:
            apply(row, i)
    # The session runs with autoflush off, so flush here: without it the next conversion's query
    # still sees these rows on their old status and would pick the very same ones again.
    db.flush()
    return len(rows)


def run(db: Session) -> None:
    today = date.today()
    moved: dict[str, int] = {}

    def on_leave(c: Client, i: int) -> None:
        c.status_remarks = CLIENT_LEAVE_REMARKS[i % len(CLIENT_LEAVE_REMARKS)]

    def passed_out(c: Client, i: int) -> None:
        c.status_remarks = "Course completed; certificate issued."

    def blacklisted(c: Client, i: int) -> None:
        c.status_remarks = "Repeated non-payment after escalation."

    moved["clients on_leave"] = _convert(db, Client, ["active"], "on_leave", CLIENT_TARGETS["on_leave"], on_leave)
    moved["clients pass_out"] = _convert(db, Client, ["active"], "pass_out", CLIENT_TARGETS["pass_out"], passed_out)
    moved["clients black_list"] = _convert(db, Client, ["churned"], "inactive", CLIENT_TARGETS["inactive"], blacklisted)

    def graduated(s: Student, i: int) -> None:
        s.drop_date = None

    def freed(s: Student, i: int) -> None:
        s.teacher_id = None

    moved["students pass_out"] = _convert(db, Student, ["active"], "graduated", STUDENT_TARGETS["graduated"], graduated)
    moved["students free"] = _convert(db, Student, ["active"], "free", STUDENT_TARGETS["free"], freed)

    def cancelled(s: Subscription, i: int) -> None:
        s.cancelled_at = today - timedelta(days=7 * (i + 1))
        s.cancel_reason = CANCEL_REASONS[i % len(CANCEL_REASONS)]
        s.auto_renew = False

    def completed(s: Subscription, i: int) -> None:
        s.completion_date = today - timedelta(days=14 * (i + 1))
        s.auto_renew = False

    moved["subscriptions cancelled"] = _convert(db, Subscription, ["active"], "cancelled",
                                                SUBSCRIPTION_TARGETS["cancelled"], cancelled)
    moved["subscriptions completed"] = _convert(db, Subscription, ["active"], "expired",
                                                SUBSCRIPTION_TARGETS["expired"], completed)

    # a family that is on leave should not still look like it is being taught
    for c in db.query(Client).filter(Client.status == "on_leave").all():
        for s in c.students:
            if s.status == "active":
                s.status = "frozen"

    db.flush()
    changed = ", ".join(f"{k} +{v}" for k, v in moved.items() if v)
    print(f"    erp_statuses: {changed or 'nothing to convert (already spread)'}")
