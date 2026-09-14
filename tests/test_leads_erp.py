"""ERP parity tests for Billing Management → Verify Leads and Lead Closers (docs/AUDIT_BILLING.md).

Covers the verification queue (every tile, every filter, the three decisions, the bulk Change Status,
the conversion that fills the Client Code column, and leads that arrive from the external marketing
tool with no contact details), and the Lead Closers catalogue (create, inline edit, activate /
deactivate), plus the permission boundary.

The suite is re-runnable against the same database. Everything it creates carries a unique tag and is
removed again in a module-scoped teardown — the leads, their activities, the lead closers, and the
clients, students and portal logins that the conversion tests create. The immutable trails (audit
events, AI model runs, webhook deliveries) are deliberately left alone; they are append-only records,
not module data, and nothing reads them by count.

Run against a private database:
    $env:DATABASE_URL='sqlite:///./data/oqc_billB.db'; .venv/Scripts/python.exe -m pytest tests/test_leads_erp.py
"""
from __future__ import annotations

import os
from uuid import uuid4

# The application reads DATABASE_URL at import time; set it before anything imports the app.
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_billB.db")

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.core import User
from app.models.crm import Conversation, Lead, LeadActivity, LeadSource
from app.models.erp import LeadCloser
from app.models.people import Client, Student

TAG = f"pytest-{uuid4().hex[:8]}"
VERIFY = "/crm/leads/verify"
CLOSERS = "/finance/lead-closers"
STATUSES = ["unverified", "forwarded", "verified", "rejected", "converted"]


def _client(username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}: {r.status_code}"
    return c


@pytest.fixture(scope="module")
def admin() -> TestClient:
    return _client("admin@oqc.local", "Admin@12345")


@pytest.fixture(scope="module")
def closer() -> TestClient:
    return _client("closer@oqc.local", "Closer@123")


@pytest.fixture(scope="module")
def a_teacher() -> TestClient:
    return _client("teacher1@oqc.local", "Teacher@123")


@pytest.fixture(scope="module")
def a_parent() -> TestClient:
    return _client("parent1@oqc.local", "Parent@123")


@pytest.fixture()
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture(scope="module", autouse=True)
def _purge():
    """Remove everything this run created, so the suite can be run again against the same database."""
    yield
    s = SessionLocal()
    try:
        like = f"%{TAG}%"
        lead_ids = [i for (i,) in s.query(Lead.id).filter(Lead.full_name.ilike(like)).all()]
        if lead_ids:
            # A conversion leaves a client, its students and a portal login behind. Clients go before
            # leads (Client.lead_id points at the lead); Lead.converted_client_id is SET NULL on the way.
            clients = s.query(Client).filter(Client.lead_id.in_(lead_ids)).all()
            user_ids = [c.user_id for c in clients if c.user_id]
            for c in clients:
                s.query(Student).filter(Student.client_id == c.id).delete(synchronize_session=False)
            s.flush()
            for c in clients:
                s.delete(c)
            s.flush()
            if user_ids:
                s.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
            s.query(Conversation).filter(Conversation.lead_id.in_(lead_ids)).delete(synchronize_session=False)
            s.query(LeadActivity).filter(LeadActivity.lead_id.in_(lead_ids)).delete(synchronize_session=False)
            s.query(Lead).filter(Lead.id.in_(lead_ids)).delete(synchronize_session=False)
        s.query(LeadCloser).filter(LeadCloser.name.ilike(like)).delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()


# --------------------------------------------------------------------------- helpers
def make_lead(admin: TestClient, session, label: str, **extra) -> Lead:
    """Create a lead through the application, tagged so the teardown can find it again."""
    data = {"full_name": f"{label} {TAG}", "country": "United Kingdom", "preferred_time": "Weekend mornings",
            "students_count": "1", "stage": "new", "student_name": f"Child {TAG}"}
    data.update(extra)
    r = admin.post("/crm/leads/new", data=data, follow_redirects=False)
    assert r.status_code == 303, r.status_code
    lead_id = int(r.headers["location"].rsplit("/", 1)[-1])
    session.expire_all()
    lead = session.get(Lead, lead_id)
    assert lead is not None
    return lead


def contactable(label: str) -> dict:
    tail = uuid4().hex[:6]
    return {"phone": f"+4470000{tail[:5]}", "whatsapp": f"+4470000{tail[:5]}",
            "email": f"{label.lower()}.{tail}@example.com"}


def decide(admin: TestClient, lead_id: int, decision: str, remarks: str):
    return admin.post(f"{VERIFY}/{lead_id}/decision", follow_redirects=False,
                      data={"decision": decision, "remarks": remarks})


def admin_user(session) -> User:
    return session.query(User).filter(User.email == "admin@oqc.local").first()


# --------------------------------------------------------------------------- seed
def test_seed_fills_every_tile(session):
    """Every tile on the queue has rows — asserted as "at least one", not an exact count."""
    for status in STATUSES:
        assert session.query(Lead).filter(Lead.verification_status == status).count() >= 1, status


def test_seed_creates_lead_closers(session):
    assert session.query(LeadCloser).count() >= 3
    assert session.query(LeadCloser).filter(LeadCloser.pseudo_name.isnot(None)).count() >= 3
    assert session.query(LeadCloser).filter(LeadCloser.representative_id.isnot(None)).count() >= 3


def test_seed_has_marketing_tool_leads(session):
    tool = (session.query(Lead)
            .filter(Lead.ghl_contact_id.isnot(None), Lead.phone.is_(None),
                    Lead.whatsapp.is_(None), Lead.email.is_(None)).all())
    assert len(tool) >= 3
    assert all(l.notes for l in tool), "a marketing-tool lead carries its source note"


def test_converted_leads_carry_a_client_code(session):
    converted = session.query(Lead).filter(Lead.verification_status == "converted").all()
    assert converted
    for l in converted:
        assert l.converted_client_id and l.converted_at
        assert session.get(Client, l.converted_client_id) is not None


# --------------------------------------------------------------------------- pages
@pytest.mark.parametrize("status", STATUSES)
def test_every_tile_loads(admin, status):
    assert admin.get(f"{VERIFY}?vstatus={status}").status_code == 200


@pytest.mark.parametrize("query", [
    "", "?q=a", "?start=2026-01-01&end=2026-12-31", "?shift=Morning", "?shift=Night",
    "?country=United+Kingdom", "?country=United+States", "?vstatus=verified&shift=Night",
    "?q=GHL-77", "?page=2", "?start=2026-06-01", "?end=2026-12-31",
])
def test_every_filter_loads(admin, query):
    assert admin.get(VERIFY + query).status_code == 200


def test_how_came_to_us_filter(admin, session):
    source = session.query(LeadSource).order_by(LeadSource.id).first()
    assert source is not None
    assert admin.get(f"{VERIFY}?source={source.id}").status_code == 200


def test_queue_shows_the_erp_columns(admin):
    body = admin.get(VERIFY).text
    for header in ["Guardian Name", "Client Code", "Converted At", "Mobile No", "Whatsapp No", "Shift",
                   "Referred By", "How Came To Us", "Followup Date", "Extra Query", "Create At"]:
        assert header in body, header


def test_queue_shows_incomplete_leads_plainly(admin, session):
    tool = session.query(Lead).filter(Lead.ghl_contact_id.ilike("GHL-77%")).first()
    assert tool is not None
    body = admin.get(f"{VERIFY}?q={tool.ghl_contact_id}").text
    assert "Incomplete" in body and "Not supplied" in body
    assert tool.ghl_contact_id in body


def test_a_lead_closer_may_read_the_queue(closer):
    assert closer.get(VERIFY).status_code == 200
    assert closer.get(CLOSERS).status_code == 200


# --------------------------------------------------------------------------- permissions
@pytest.mark.parametrize("url", [VERIFY, f"{VERIFY}?vstatus=verified", CLOSERS, f"{CLOSERS}?status=active"])
def test_teacher_is_refused(a_teacher, url):
    assert a_teacher.get(url).status_code == 403


@pytest.mark.parametrize("url", [VERIFY, CLOSERS])
def test_parent_is_refused(a_parent, url):
    assert a_parent.get(url).status_code == 403


def test_teacher_cannot_decide_or_edit_closers(a_teacher, session):
    lead = session.query(Lead).order_by(Lead.id).first()
    assert a_teacher.post(f"{VERIFY}/{lead.id}/decision", follow_redirects=False,
                          data={"decision": "verify", "remarks": "no"}).status_code == 403
    assert a_teacher.post(f"{VERIFY}/change-status", follow_redirects=False,
                          data={"ids": [lead.id], "verification_status": "verified", "remarks": "no"}).status_code == 403
    assert a_teacher.post(f"{CLOSERS}/new", follow_redirects=False, data={"name": f"Nope {TAG}"}).status_code == 403


# --------------------------------------------------------------------------- decisions
def test_verify_stamps_the_verifier_and_the_time(admin, session):
    lead = make_lead(admin, session, "Verifiable", **contactable("verifiable"))
    assert lead.verification_status == "unverified"
    remarks = f"Guardian confirmed the mobile and the shift {TAG}"
    assert decide(admin, lead.id, "verify", remarks).status_code == 303

    session.expire_all()
    lead = session.get(Lead, lead.id)
    assert lead.verification_status == "verified"
    assert lead.verifier_remarks == remarks
    assert lead.verified_by_id == admin_user(session).id
    assert lead.verified_at is not None


def test_forward_to_verifier(admin, session):
    lead = make_lead(admin, session, "Forwardable", **contactable("forwardable"))
    assert decide(admin, lead.id, "forward", f"Passed to the verifier {TAG}").status_code == 303
    session.expire_all()
    lead = session.get(Lead, lead.id)
    assert lead.verification_status == "forwarded"
    assert lead.verified_by_id and lead.verified_at


def test_reject_records_the_remarks(admin, session):
    lead = make_lead(admin, session, "Rejectable", **contactable("rejectable"))
    remarks = f"Number does not ring after three attempts {TAG}"
    assert decide(admin, lead.id, "reject", remarks).status_code == 303
    session.expire_all()
    lead = session.get(Lead, lead.id)
    assert lead.verification_status == "rejected"
    assert lead.verifier_remarks == remarks
    assert lead.verified_by_id == admin_user(session).id


def test_a_decision_needs_remarks_and_a_decision(admin, session):
    lead = make_lead(admin, session, "Undecided", **contactable("undecided"))
    assert decide(admin, lead.id, "verify", "").status_code == 303        # no remarks
    assert decide(admin, lead.id, "shrug", "anything").status_code == 303  # not a decision
    session.expire_all()
    assert session.get(Lead, lead.id).verification_status == "unverified"


def test_an_incomplete_lead_cannot_be_verified_but_can_be_rejected(admin, session):
    """A lead from the marketing tool has no contact details; it may still be closed."""
    lead = make_lead(admin, session, "FromTheTool")
    assert not (lead.phone or lead.whatsapp or lead.email)

    assert decide(admin, lead.id, "verify", f"Trying to verify a blank record {TAG}").status_code == 303
    session.expire_all()
    assert session.get(Lead, lead.id).verification_status == "unverified"

    assert decide(admin, lead.id, "reject", f"No contact details ever supplied {TAG}").status_code == 303
    session.expire_all()
    lead = session.get(Lead, lead.id)
    assert lead.verification_status == "rejected"
    assert TAG in lead.verifier_remarks


# --------------------------------------------------------------------------- bulk change
def test_bulk_change_applies_to_every_selected_row(admin, session):
    leads = [make_lead(admin, session, f"Bulk{n}", **contactable(f"bulk{n}")) for n in range(3)]
    ids = [l.id for l in leads]
    remarks = f"Batch handed to the verifier {TAG}"
    r = admin.post(f"{VERIFY}/change-status", follow_redirects=False,
                   data={"ids": ids, "verification_status": "forwarded", "remarks": remarks})
    assert r.status_code == 303

    session.expire_all()
    for lead_id in ids:
        row = session.get(Lead, lead_id)
        assert row.verification_status == "forwarded", lead_id
        assert row.verifier_remarks == remarks
        assert row.verified_by_id == admin_user(session).id
        assert row.verified_at is not None


def test_bulk_change_validates_its_input(admin, session):
    lead = make_lead(admin, session, "Untouched", **contactable("untouched"))
    bad = [
        {"ids": [lead.id], "verification_status": "", "remarks": "x"},                 # no status
        {"ids": [lead.id], "verification_status": "converted", "remarks": "x"},        # conversion is not a decision
        {"ids": [], "verification_status": "verified", "remarks": "x"},                # nothing selected
        {"ids": [lead.id], "verification_status": "verified", "remarks": ""},          # no remarks
    ]
    for data in bad:
        assert admin.post(f"{VERIFY}/change-status", follow_redirects=False, data=data).status_code == 303
    session.expire_all()
    assert session.get(Lead, lead.id).verification_status == "unverified"


def test_bulk_change_leaves_converted_rows_alone(admin, session):
    converted = session.query(Lead).filter(Lead.verification_status == "converted").order_by(Lead.id).first()
    assert converted is not None
    before_remarks = converted.verifier_remarks
    r = admin.post(f"{VERIFY}/change-status", follow_redirects=False,
                   data={"ids": [converted.id], "verification_status": "rejected", "remarks": f"try it {TAG}"})
    assert r.status_code == 303
    session.expire_all()
    row = session.get(Lead, converted.id)
    assert row.verification_status == "converted"
    assert row.verifier_remarks == before_remarks


# --------------------------------------------------------------------------- conversion
def test_conversion_needs_a_verified_lead(admin, session):
    lead = make_lead(admin, session, "TooEarly", **contactable("tooearly"))
    r = admin.post(f"{VERIFY}/{lead.id}/convert", follow_redirects=False, data={"relationship": "father"})
    assert r.status_code == 303
    assert r.headers["location"] == VERIFY
    session.expire_all()
    assert session.get(Lead, lead.id).converted_client_id is None


def test_converting_a_verified_lead_fills_the_client_code(admin, session):
    contact = contactable("convertible")
    lead = make_lead(admin, session, "Convertible", **contact)
    assert decide(admin, lead.id, "verify", f"Checked and ready {TAG}").status_code == 303

    r = admin.post(f"{VERIFY}/{lead.id}/convert", follow_redirects=False,
                   data={"email": contact["email"], "relationship": "father"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/clients/")

    session.expire_all()
    lead = session.get(Lead, lead.id)
    assert lead.verification_status == "converted"
    assert lead.converted_client_id and lead.converted_at
    client = session.get(Client, lead.converted_client_id)
    assert client is not None and client.lead_id == lead.id
    assert session.query(Student).filter(Student.client_id == client.id).count() >= 1

    # the Client Code column now shows the code the lead became
    body = admin.get(f"{VERIFY}?vstatus=converted&q={lead.lead_code}").text
    assert client.client_code in body


def test_converting_from_the_lead_record_still_works_and_says_it_was_unverified(admin, session):
    """The existing conversion is not blocked for an unverified lead — the confirmation says so."""
    contact = contactable("straightthrough")
    lead = make_lead(admin, session, "StraightThrough", **contact)
    assert lead.verification_status == "unverified"

    r = admin.post(f"/crm/leads/{lead.id}/convert", follow_redirects=True,
                   data={"email": contact["email"], "relationship": "mother"})
    assert r.status_code == 200
    assert "had not been verified" in r.text

    session.expire_all()
    lead = session.get(Lead, lead.id)
    assert lead.converted_client_id and lead.converted_at
    assert lead.verification_status == "converted"


# --------------------------------------------------------------------------- lead closers
def test_lead_closer_create_edit_and_toggle(admin, session):
    rep = session.query(User).filter(User.email == "marketing@oqc.local").first()
    name = f"Closer One {TAG}"
    r = admin.post(f"{CLOSERS}/new", follow_redirects=False,
                   data={"name": name, "pseudo_name": "Br. One", "representative_id": rep.id if rep else "",
                         "status": "active"})
    assert r.status_code == 303
    row = session.query(LeadCloser).filter(LeadCloser.name == name).first()
    assert row is not None and row.status == "active" and row.pseudo_name == "Br. One"
    if rep:
        assert row.representative_id == rep.id

    assert admin.post(f"{CLOSERS}/{row.id}/edit", follow_redirects=False,
                      data={"name": name, "pseudo_name": "Br. One Edited", "representative_id": "",
                            "status": "active"}).status_code == 303
    session.expire_all()
    row = session.get(LeadCloser, row.id)
    assert row.pseudo_name == "Br. One Edited" and row.representative_id is None

    assert admin.post(f"{CLOSERS}/{row.id}/toggle", follow_redirects=False).status_code == 303
    session.expire_all()
    assert session.get(LeadCloser, row.id).status == "inactive"
    assert admin.post(f"{CLOSERS}/{row.id}/toggle", follow_redirects=False).status_code == 303
    session.expire_all()
    assert session.get(LeadCloser, row.id).status == "active"


def test_lead_closer_needs_a_name_and_refuses_a_duplicate(admin, session):
    before = session.query(LeadCloser).count()
    assert admin.post(f"{CLOSERS}/new", follow_redirects=False, data={"name": "  "}).status_code == 303
    session.expire_all()
    assert session.query(LeadCloser).count() == before

    name = f"Closer Two {TAG}"
    assert admin.post(f"{CLOSERS}/new", follow_redirects=False, data={"name": name}).status_code == 303
    assert admin.post(f"{CLOSERS}/new", follow_redirects=False, data={"name": name.lower()}).status_code == 303
    session.expire_all()
    assert session.query(LeadCloser).filter(LeadCloser.name.ilike(f"%{name}%")).count() == 1


def test_lead_closer_filters(admin, session):
    rep = session.query(LeadCloser).filter(LeadCloser.representative_id.isnot(None)).first()
    assert admin.get(f"{CLOSERS}?q=Closer").status_code == 200
    assert admin.get(f"{CLOSERS}?status=inactive").status_code == 200
    if rep:
        assert admin.get(f"{CLOSERS}?representative={rep.representative_id}").status_code == 200


def test_lead_closer_page_shows_the_erp_columns(admin):
    body = admin.get(CLOSERS).text
    for header in ["Closer Name", "Sudo Name", "Representative Name", "Status"]:
        assert header in body, header
