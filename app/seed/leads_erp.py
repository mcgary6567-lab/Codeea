"""Verify Leads + Lead Closers seed (docs/AUDIT_BILLING.md).

Gives the Billing Management lead pages something real to stand on:

* three or four **Lead Closers** built from the staff already seeded, with the short name their
  reports are signed with and the representative answerable for each book;
* a **verification status** spread across the leads written by ``seed.crm`` so every tile on
  ``/crm/leads/verify`` has rows — Unverified, Forward to Verifier, Verified, Rejected, Converted;
* a handful of leads as they arrive from the external marketing tool: a source note and that tool's
  own id, and no contact details at all;
* every lead that already became a family marked ``converted``, so the Client Code column fills in.

Idempotent. The closers are keyed by name, the marketing-tool leads by their tool id, and the spread
only touches leads no one has decided on yet (``verified_at`` is the mark of a decision).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.utils import next_code
from app.models.core import Role, User
from app.models.crm import Lead, LeadActivity, LeadSource
from app.models.erp import LeadCloser

# name -> (sudo name, status). Matched to a real seeded user by full name where one exists.
CLOSER_PSEUDONYMS = ["Br. Closer", "Sr. Closer", "Admissions Desk", "Night Desk"]

# Leads as the external marketing tool hands them over: a note, its own id, and nothing else.
TOOL_LEADS = [
    ("GHL-7741", "Imran Sadiq", "United Kingdom", "unverified",
     "Marketing tool: Facebook lead form 'Quran for Kids UK'. Contact details not supplied yet."),
    ("GHL-7742", "Ayesha Noor", "United States", "unverified",
     "Marketing tool: Instagram lead form 'Free Trial Class'. Contact details not supplied yet."),
    ("GHL-7743", "Yusuf Kareem", "Canada", "forwarded",
     "Marketing tool: landing page 'Tajweed Intensive'. Contact details not supplied yet."),
    ("GHL-7744", "Fatima Bilal", "Australia", "rejected",
     "Marketing tool: duplicate of an older enquiry, no contact details supplied."),
    ("GHL-7745", "Omar Haris", "United Kingdom", "rejected",
     "Marketing tool: test submission from the agency, no contact details supplied."),
]

# lead.id % 7 -> the status a lead nobody has looked at yet lands on. Deterministic, so a second
# run of the seed reaches the same answer.
SPREAD = {0: "unverified", 1: "unverified", 2: "unverified", 3: "forwarded",
          4: "verified", 5: "verified", 6: "rejected"}

REMARKS = {
    "forwarded": "Passed to the verifier: guardian reached, details still to be confirmed.",
    "verified": "Guardian confirmed the mobile, the shift and the student's age. Ready to convert.",
    "rejected": "No answer after three attempts and the number does not ring. Closed.",
}


def _admin_users(db: Session) -> list[User]:
    return (db.query(User).join(Role, Role.id == User.role_id)
            .filter(User.is_active.is_(True), Role.portal == "admin").order_by(User.id).all())


def _closer_users(db: Session) -> list[User]:
    closers = (db.query(User).join(Role, Role.id == User.role_id)
               .filter(User.is_active.is_(True), Role.slug == "lead_closer").order_by(User.id).all())
    if len(closers) >= 3:
        return closers[:4]
    pool = [u for u in _admin_users(db) if u not in closers]
    return (closers + pool)[:4]


def seed_lead_closers(db: Session) -> int:
    users = _closer_users(db)
    if not users:
        return 0
    reps = _admin_users(db)
    created = 0
    for i, u in enumerate(users):
        if db.query(LeadCloser).filter(func.lower(LeadCloser.name) == u.full_name.lower()).first():
            continue
        db.add(LeadCloser(name=u.full_name, pseudo_name=CLOSER_PSEUDONYMS[i % len(CLOSER_PSEUDONYMS)],
                          representative_id=(reps[i % len(reps)].id if reps else None),
                          # one in-active row so the Status column and its tile both have something to show
                          status="inactive" if i == len(users) - 1 and len(users) > 3 else "active"))
        created += 1
    db.flush()
    return created


def seed_tool_leads(db: Session) -> int:
    source = db.query(LeadSource).filter(LeadSource.name == "GHL").first() or \
        db.query(LeadSource).filter(LeadSource.name.ilike("%manual%")).first()
    created = 0
    for tool_id, name, country, status, note in TOOL_LEADS:
        if db.query(Lead).filter(Lead.ghl_contact_id == tool_id).first():
            continue
        when = datetime.utcnow() - timedelta(days=3 + created, hours=4)
        lead = Lead(lead_code=next_code(db, Lead, "lead_code", "L-"), full_name=name, country=country,
                    source_id=source.id if source else None, stage="new", score=15,
                    ghl_contact_id=tool_id, notes=note, whatsapp_opt_in=False,
                    created_at=when, updated_at=when, verification_status=status)
        if status in REMARKS:
            lead.verifier_remarks = REMARKS[status]
            lead.verified_at = when + timedelta(hours=6)
        db.add(lead)
        db.flush()
        db.add(LeadActivity(lead_id=lead.id, activity_type="note", created_at=when,
                            note=f"Arrived from the marketing tool as {tool_id} with no contact details."))
        created += 1
    db.flush()
    return created


def spread_verification(db: Session) -> dict:
    """Give every tile rows. Converted first, then a deterministic spread over the undecided rest."""
    verifier = (db.query(User).join(Role, Role.id == User.role_id)
                .filter(Role.slug.in_(["lead_closer", "hod_marketing"]), User.is_active.is_(True)).order_by(User.id).first())
    verifier = verifier or db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()
    touched = {"converted": 0, "unverified": 0, "forwarded": 0, "verified": 0, "rejected": 0}

    # A lead that already became a family leaves the queue carrying the client code it became.
    for lead in db.query(Lead).filter(Lead.converted_client_id.isnot(None)).all():
        if lead.verification_status != "converted":
            lead.verification_status = "converted"
            lead.verifier_remarks = lead.verifier_remarks or "Verified and converted to a family."
            lead.verified_by_id = lead.verified_by_id or (verifier.id if verifier else None)
            lead.verified_at = lead.verified_at or lead.converted_at or datetime.utcnow()
            touched["converted"] += 1
        if not lead.converted_at:
            lead.converted_at = (lead.created_at or datetime.utcnow()) + timedelta(days=5)

    # Everything nobody has decided on yet: verified_at is the mark of a decision, so a second run
    # leaves the first run's rows exactly where they were.
    undecided = (db.query(Lead).filter(Lead.converted_client_id.is_(None),
                                       Lead.verification_status == "unverified",
                                       Lead.verified_at.is_(None)).all())
    for lead in undecided:
        status = SPREAD[lead.id % 7]
        if status == "unverified":
            continue
        lead.verification_status = status
        lead.verifier_remarks = REMARKS[status]
        lead.verified_by_id = verifier.id if verifier else None
        lead.verified_at = (lead.last_contacted_at or lead.created_at or datetime.utcnow()) + timedelta(hours=9)
        touched[status] += 1
    db.flush()
    return touched


def run(db: Session) -> None:
    closers = seed_lead_closers(db)
    tool_leads = seed_tool_leads(db)
    spread_verification(db)
    db.flush()
    counts = dict(db.query(Lead.verification_status, func.count(Lead.id)).group_by(Lead.verification_status).all())
    tiles = " ".join(f"{s}={counts.get(s, 0)}" for s in ("unverified", "forwarded", "verified", "rejected", "converted"))
    print(f"    lead closers: +{closers} (total {db.query(func.count(LeadCloser.id)).scalar()}) | "
          f"marketing-tool leads: +{tool_leads} | verify queue: {tiles}")
