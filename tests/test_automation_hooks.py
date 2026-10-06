"""The places the application emits automation events, and the seeded catalogue picking them up.

Each hook is driven the way the application drives it: leads through crm.create_lead / move_stage /
convert_lead_to_client, trials through the real /trials routes with a signed-in admin, payments and
subscriptions through the billing service, certificates through academic.issue_certificate, surveys through
crm.submit_feedback. The assertions are about which seeded workflow (AUTO-003, P1-S2, AUTO-010, P1-S8,
AUTO-005, AUTO-007, AUTO-012, P2-FREEZE, P2-RESUME, P3-CANCEL, AUTO-017, AUTO-019, AUTO-011) started a run
for the contact the test created, and what its first steps did. The seed itself is checked for idempotence.

The suite is re-runnable against the same database. Every lead, family, student, trial, payment,
subscription, certificate and survey answer is the module's own (carrying its tag), and a module-scoped
teardown removes them with everything the engine produced for them (runs, events, tags, tasks, messages,
sequence enrollments, cases) and every notification raised while the module ran. Nothing asserts an
absolute count of seeded rows. Other modules leave contact_tags / automation_events behind for leads they
deleted and SQLite reuses those ids, so event queries are watermarked per test and the orphans on an id
about to be reused are cleared first.

Run:
    .venv/Scripts/python.exe -m pytest tests/test_automation_hooks.py -q -p no:warnings
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func

from app.core.utils import next_code
from app.database import SessionLocal
from app.main import app
from app.models.academic import Certificate
from app.models.automation import AutomationEvent, ContactTag, Tag, Workflow, WorkflowRun
from app.models.core import Notification, User
from app.models.crm import Case, CaseComment, Conversation, Feedback, Lead, LeadActivity, Message, SequenceEnrollment
from app.models.finance import JournalEntry, JournalLine, LedgerEntry, Payment, Receipt, Subscription
from app.models.ops import Task
from app.models.people import Client, Student
from app.models.scheduling import ClassSession, Trial
from app.services import academic, billing, crm
from app.services import automation as auto
from app.services.people import org_now  # task due dates follow the college's calendar

TAG = f"TEST-{uuid4().hex[:8]}"
MADE: dict[str, set[int]] = {"lead": set(), "client": set(), "student": set()}
START: dict[str, int] = {}

REPLY_EXIT_BUG = ("app/services/automation.py:232 (_should_exit) filters on Conversation.contact_id, which does not exist "
                  "(Conversation has lead_id / client_id); every exit_on.reply workflow (P1-S2, AUTO-007, AUTO-008) raises "
                  "AttributeError out of emit() and into the hook that emitted the event")


def _reply_exit_triggers() -> set[str]:
    """Triggers whose active workflows exit on a reply, as this database has them (a seeded row may predate the spec)."""
    session = SessionLocal()
    try:
        return {trigger for (trigger, exit_on) in session.query(Workflow.trigger, Workflow.exit_on).filter(Workflow.is_active.is_(True)).all()
                if (exit_on or {}).get("reply")}
    finally:
        session.close()


REPLY_EXIT_TRIGGERS = _reply_exit_triggers()


# --------------------------------------------------------------------------- fixtures
def _client(username: str, password: str) -> TestClient:
    c = TestClient(app)  # no context manager: the lifespan (and its scheduler) stays off
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}: {r.status_code}"
    return c


@pytest.fixture(scope="module")
def admin() -> TestClient:
    return _client("admin@oqc.local", "Admin@12345")


@pytest.fixture()
def s():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _max_id(s, model) -> int:
    return s.query(func.max(model.id)).scalar() or 0


@pytest.fixture(autouse=True)
def _watermarks(s):
    START.update(event=_max_id(s, AutomationEvent), notification=_max_id(s, Notification), task=_max_id(s, Task), run=_max_id(s, WorkflowRun))
    yield


@pytest.fixture(scope="module", autouse=True)
def _purge():
    """Remove everything this run created, so the suite can be run again against the same database."""
    first = SessionLocal()
    try:
        notification_mark = _max_id(first, Notification)
        case_mark = _max_id(first, Case)
    finally:
        first.close()
    yield
    session = SessionLocal()
    try:
        like = f"%{TAG}%"
        lead_ids = set(MADE["lead"]) | {i for (i,) in session.query(Lead.id).filter(Lead.full_name.ilike(like)).all()}
        client_ids = set(MADE["client"]) | {i for (i,) in session.query(Client.id).filter(Client.full_name.ilike(like)).all()}
        if lead_ids:
            client_ids |= {i for (i,) in session.query(Client.id).filter(Client.lead_id.in_(lead_ids)).all()}
        student_ids = set(MADE["student"]) | {i for (i,) in session.query(Student.id).filter(Student.full_name.ilike(like)).all()}
        if client_ids:
            student_ids |= {i for (i,) in session.query(Student.id).filter(Student.client_id.in_(client_ids)).all()}
        for ctype, ids in (("lead", lead_ids), ("client", client_ids), ("student", student_ids)):
            if not ids:
                continue
            for model in (WorkflowRun, AutomationEvent, ContactTag, SequenceEnrollment):
                session.query(model).filter(model.contact_type == ctype, model.contact_id.in_(ids)).delete(synchronize_session=False)
            session.query(Task).filter(Task.entity_type == ctype, Task.entity_id.in_(ids)).delete(synchronize_session=False)
        conv_ids = set()
        if lead_ids:
            conv_ids |= {i for (i,) in session.query(Conversation.id).filter(Conversation.lead_id.in_(lead_ids)).all()}
        if client_ids:
            conv_ids |= {i for (i,) in session.query(Conversation.id).filter(Conversation.client_id.in_(client_ids)).all()}
        if conv_ids:
            session.query(Message).filter(Message.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
            session.query(Conversation).filter(Conversation.id.in_(conv_ids)).delete(synchronize_session=False)
        if client_ids:
            pay_ids = [i for (i,) in session.query(Payment.id).filter(Payment.client_id.in_(client_ids)).all()]
            if pay_ids:
                session.query(Receipt).filter(Receipt.payment_id.in_(pay_ids)).delete(synchronize_session=False)
                je_ids = [i for (i,) in session.query(JournalEntry.id).filter(JournalEntry.reference_type == "payment",
                                                                              JournalEntry.reference_id.in_(pay_ids)).all()]
                if je_ids:
                    session.query(JournalLine).filter(JournalLine.entry_id.in_(je_ids)).delete(synchronize_session=False)
                    session.query(JournalEntry).filter(JournalEntry.id.in_(je_ids)).delete(synchronize_session=False)
                session.query(Payment).filter(Payment.id.in_(pay_ids)).delete(synchronize_session=False)
            session.query(LedgerEntry).filter(LedgerEntry.client_id.in_(client_ids)).delete(synchronize_session=False)
            session.query(Subscription).filter(Subscription.client_id.in_(client_ids)).delete(synchronize_session=False)
            session.query(Feedback).filter(Feedback.client_id.in_(client_ids)).delete(synchronize_session=False)
            case_ids = [i for (i,) in session.query(Case.id).filter(Case.client_id.in_(client_ids), Case.id > case_mark).all()]
            if case_ids:
                session.query(CaseComment).filter(CaseComment.case_id.in_(case_ids)).delete(synchronize_session=False)
                session.query(Case).filter(Case.id.in_(case_ids)).delete(synchronize_session=False)
        if student_ids:
            session.query(Feedback).filter(Feedback.student_id.in_(student_ids)).delete(synchronize_session=False)
            session.query(Certificate).filter(Certificate.student_id.in_(student_ids)).delete(synchronize_session=False)
            session.query(ClassSession).filter(ClassSession.student_id.in_(student_ids)).delete(synchronize_session=False)
            session.query(Trial).filter(Trial.student_id.in_(student_ids)).delete(synchronize_session=False)
            session.query(Student).filter(Student.id.in_(student_ids)).delete(synchronize_session=False)
        if lead_ids:
            session.query(Trial).filter(Trial.lead_id.in_(lead_ids)).delete(synchronize_session=False)
            session.query(LeadActivity).filter(LeadActivity.lead_id.in_(lead_ids)).delete(synchronize_session=False)
        if client_ids:
            user_ids = [u for (u,) in session.query(Client.user_id).filter(Client.id.in_(client_ids), Client.user_id.isnot(None)).all()]
            session.query(Client).filter(Client.id.in_(client_ids)).delete(synchronize_session=False)
            if user_ids:
                session.query(Notification).filter(Notification.user_id.in_(user_ids)).delete(synchronize_session=False)
                session.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        if lead_ids:
            session.query(Lead).filter(Lead.id.in_(lead_ids)).delete(synchronize_session=False)
        # Every notification raised while this module ran was caused by it (nothing else writes meanwhile).
        session.query(Notification).filter(Notification.id > notification_mark).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()


# --------------------------------------------------------------------------- helpers
def admin_user(s) -> User:
    return s.query(User).filter(User.email == "admin@oqc.local").first()


def wf(s, code: str) -> Workflow:
    w = s.query(Workflow).filter(Workflow.code == code).first()
    assert w is not None, f"seeded workflow {code} must exist (run seed.py)"
    return w


def runs_of(s, code: str, contact_type: str, contact_id: int) -> list[WorkflowRun]:
    w = wf(s, code)
    return (s.query(WorkflowRun).filter(WorkflowRun.workflow_id == w.id, WorkflowRun.contact_type == contact_type,
                                        WorkflowRun.contact_id == contact_id).order_by(WorkflowRun.id).all())


def events_of(s, event: str, contact_type: str, contact_id: int) -> list[AutomationEvent]:
    return (s.query(AutomationEvent).filter(AutomationEvent.event == event, AutomationEvent.contact_type == contact_type,
                                            AutomationEvent.contact_id == contact_id, AutomationEvent.id > START["event"])
            .order_by(AutomationEvent.id).all())


def _clear_orphans_on_next_id(s, contact_type: str, model) -> None:
    """The id SQLite will hand the next row may still carry tags / events / runs of a contact another module deleted."""
    nxt = _max_id(s, model) + 1
    for m in (ContactTag, AutomationEvent, WorkflowRun, SequenceEnrollment):
        s.query(m).filter(m.contact_type == contact_type, m.contact_id == nxt).delete(synchronize_session=False)


def make_lead(s, label: str, **extra) -> Lead:
    """A lead through crm.create_lead, exactly as the registration form and the CRM do it. Committed."""
    tail = uuid4().hex[:6]
    data = {"full_name": f"{TAG} {label}", "phone": f"+4477{tail[:5]}1", "whatsapp": f"+4477{tail[:5]}1", "email": f"{label.lower()}.{tail}@example.com",
            "country": "United Kingdom", "student_name": f"Child {label}", "students_count": 1, "stage": "new", "preferred_time": "Evenings"}
    data.update(extra)
    _clear_orphans_on_next_id(s, "lead", Lead)
    lead, _dups = crm.create_lead(s, data, actor=admin_user(s))
    s.commit()
    MADE["lead"].add(lead.id)
    return lead


def make_family(s, label: str, **client_extra) -> tuple[Client, Student]:
    """A family with one active student, built on the models (families are normally born from a conversion). Committed."""
    tail = uuid4().hex[:6]
    closer = s.query(User).filter(User.email == "closer@oqc.local").first()
    _clear_orphans_on_next_id(s, "client", Client)
    fields = dict(client_code=next_code(s, Client, "client_code", "C-"), full_name=f"{TAG} {label}", email=f"{label.lower()}.{tail}@example.com",
                  phone=f"+4478{tail[:5]}1", whatsapp=f"+4478{tail[:5]}1", country="United Kingdom", currency="GBP", status="active",
                  billing_rep_id=closer.id if closer else None, whatsapp_opt_in=True)
    fields.update(client_extra)
    client = Client(**fields)
    s.add(client)
    s.flush()
    _clear_orphans_on_next_id(s, "student", Student)
    student = Student(student_code=next_code(s, Student, "student_code", "S-"), client_id=client.id, full_name=f"{TAG} {label} child", age=9, status="active")
    s.add(student)
    s.commit()
    MADE["client"].add(client.id)
    MADE["student"].add(student.id)
    return client, student


# --------------------------------------------------------------------------- leads (app/services/crm.py)
def test_create_lead_starts_new_lead_notification(s):
    lead = make_lead(s, "Newlead")
    assert lead.assigned_to_id, "round-robin gave the lead to a closer"
    ev = events_of(s, "lead.created", "lead", lead.id)
    assert len(ev) == 1 and ev[0].payload["stage"] == "new" and ev[0].payload["country"] == "United Kingdom" and ev[0].runs_started >= 1
    runs = runs_of(s, "AUTO-003", "lead", lead.id)
    assert len(runs) == 1
    run = runs[0]
    assert run.trigger_event == "lead.created"
    assert run.log[0]["kind"] == "add_tag" and "status:new-lead" in run.log[0]["result"]
    assert auto.has_tag(s, "lead", lead.id, "status:new-lead")
    assert [e["kind"] for e in run.log[:4]] == ["add_tag", "notify_staff", "send_whatsapp", "wait"]
    assert run.status == "waiting" and run.current_step == 4, "parked on the 48-hour wait"
    assert run.next_run_at == run.started_at + timedelta(hours=48)
    rep_alert = (s.query(Notification).filter(Notification.user_id == lead.assigned_to_id, Notification.event_type == "automation",
                                              Notification.link == f"/crm/leads/{lead.id}", Notification.id > START["notification"]).all())
    assert len(rep_alert) == 1 and rep_alert[0].title == f"New lead: {lead.full_name.split()[0]}"
    assert "Child Newlead" in rep_alert[0].body
    assert run.log[2]["result"].startswith("whatsapp sent"), "the first-touch template went out (simulated adapter)"


def test_create_lead_without_a_phone_still_runs_and_skips_the_whatsapp(s):
    lead = make_lead(s, "Nophone", phone=None, whatsapp=None, email=None)
    (run,) = runs_of(s, "AUTO-003", "lead", lead.id)
    assert run.log[2]["kind"] == "send_whatsapp" and run.log[2]["result"] == "skipped: no phone number"
    assert run.status == "waiting"


def test_move_stage_to_contacted_starts_demo_booking_pending(s):
    lead = make_lead(s, "Contacted")
    crm.move_stage(s, lead, "contacted", admin_user(s), "Called the family")
    s.commit()
    ev = events_of(s, "lead.stage_changed", "lead", lead.id)
    assert len(ev) == 1 and ev[0].payload["to"] == "contacted"
    runs = runs_of(s, "P1-S2", "lead", lead.id)
    assert len(runs) == 1 and runs[0].trigger_event == "lead.stage_changed"
    assert runs[0].log[0]["kind"] == "send_whatsapp" and runs[0].status == "waiting"


def test_move_stage_to_lost_starts_reactivation_and_cleanup(s):
    lead = make_lead(s, "Lostlead")
    assert auto.has_tag(s, "lead", lead.id, "status:new-lead")
    (welcome,) = runs_of(s, "AUTO-003", "lead", lead.id)
    crm.move_stage(s, lead, "lost", admin_user(s), "Chose another academy")
    s.commit()
    assert lead.stage == "lost" and lead.lost_reason == "Chose another academy"
    lost = events_of(s, "lead.lost", "lead", lead.id)
    assert len(lost) == 1 and lost[0].payload["reason"] == "Chose another academy"
    assert len(events_of(s, "lead.stage_changed", "lead", lead.id)) == 1

    (react,) = runs_of(s, "AUTO-010", "lead", lead.id)
    assert react.log[0]["kind"] == "add_tag" and "status:stale" in react.log[0]["result"]
    assert react.status == "waiting" and react.next_run_at == react.started_at + timedelta(days=30)

    (cleanup,) = runs_of(s, "P1-S8", "lead", lead.id)
    assert cleanup.status == "completed"
    assert [e["kind"] for e in cleanup.log] == ["remove_tag"] * 5 + ["add_tag"]
    assert cleanup.log[0]["result"] == "tag status:new-lead removed"
    assert auto.has_tag(s, "lead", lead.id, "status:stale")
    assert not auto.has_tag(s, "lead", lead.id, "status:new-lead")

    # The New Lead Notification run that was waiting for its 48-hour check stops: the lead is lost.
    s.refresh(welcome)
    assert welcome.status == "waiting"
    auto.advance_run(s, welcome, now=welcome.next_run_at)
    s.commit()
    assert welcome.status == "stopped" and welcome.stop_reason == "Lead is lost"


def test_convert_lead_starts_the_enrolment_welcome_for_the_new_family(s):
    lead = make_lead(s, "Convert")
    client, temp_password = crm.convert_lead_to_client(s, lead, admin_user(s))
    s.commit()
    MADE["client"].add(client.id)
    MADE["student"].update(st.id for st in client.students)
    assert lead.stage == "won" and lead.converted_client_id == client.id
    ev = events_of(s, "lead.converted", "client", client.id)
    assert len(ev) == 1 and ev[0].payload["lead_id"] == lead.id and ev[0].payload["students"] == 1
    (welcome,) = runs_of(s, "AUTO-011", "client", client.id)
    assert welcome.log[0]["kind"] == "add_tag" and "status:enrolled" in welcome.log[0]["result"]
    assert auto.has_tag(s, "client", client.id, "status:enrolled")
    assert welcome.status == "waiting"
    # Conversion records the family's consent (consent_given=True), so the compliance workflow adds the consent tags.
    assert client.consent_given is True
    (consent,) = runs_of(s, "COMP-CONSENT", "client", client.id)
    assert consent.status == "completed"
    assert [e["kind"] for e in consent.log] == ["condition", "add_tag", "add_tag"]
    assert consent.log[0]["result"].endswith("yes, continue")
    assert auto.has_tag(s, "client", client.id, "consent:email") and auto.has_tag(s, "client", client.id, "consent:whatsapp")
    assert len(runs_of(s, "AUTO-013", "client", client.id)) == 1 and len(runs_of(s, "AUTO-016", "client", client.id)) == 1


# --------------------------------------------------------------------------- trials (app/web/trials.py)
def test_scheduling_a_trial_emits_trial_scheduled_and_confirms_the_demo(s, admin):
    lead = make_lead(s, "Trialbook")
    trial = crm.create_trial(s, f"Child Trialbook {TAG}", lead=lead, actor=admin_user(s))
    s.commit()
    assert trial.status == "requested"
    when = datetime(2026, 11, 2, 10, 0)
    r = admin.post(f"/trials/{trial.id}/schedule", data={"scheduled_at": when.isoformat(timespec="minutes")}, follow_redirects=False)
    assert r.status_code == 303, r.status_code
    s.expire_all()
    assert trial.status == "scheduled" and trial.scheduled_at == when
    assert lead.stage == "trial_scheduled"
    ev = events_of(s, "trial.scheduled", "lead", lead.id)
    assert len(ev) == 1 and ev[0].payload["trial_id"] == trial.id and ev[0].payload["date"] == "02 Nov 2026 10:00"
    (confirm,) = runs_of(s, "AUTO-005", "lead", lead.id)
    assert confirm.status == "completed"
    assert [e["kind"] for e in confirm.log] == ["add_tag", "remove_tag", "send_whatsapp", "send_email"]
    assert confirm.log[2]["result"].startswith("whatsapp sent") and confirm.log[3]["result"].startswith("email sent")
    assert auto.has_tag(s, "lead", lead.id, "status:demo-booked")
    assert not auto.has_tag(s, "lead", lead.id, "status:new-lead")
    msg = (s.query(Message).join(Conversation).filter(Conversation.lead_id == lead.id, Message.direction == "out")
           .order_by(Message.id.desc()).first())
    assert "02 Nov 2026 10:00" in msg.body, "the confirmation carries the booked date"


def test_rescheduling_runs_the_confirmation_again(s, admin):
    """AUTO-005 is not run-once: a second booking confirms again once the first run has finished."""
    lead = make_lead(s, "Rebook")
    trial = crm.create_trial(s, f"Child Rebook {TAG}", lead=lead, actor=admin_user(s))
    s.commit()
    for day in (3, 4):
        r = admin.post(f"/trials/{trial.id}/schedule", data={"scheduled_at": f"2026-11-0{day}T10:00"}, follow_redirects=False)
        assert r.status_code == 303
    s.expire_all()
    assert len(events_of(s, "trial.scheduled", "lead", lead.id)) == 2
    assert len(runs_of(s, "AUTO-005", "lead", lead.id)) == 2


def test_trial_attended_starts_post_demo_nurture(s, admin):
    lead = make_lead(s, "Attended")
    trial = crm.create_trial(s, f"Child Attended {TAG}", lead=lead, actor=admin_user(s), scheduled_at=datetime(2026, 11, 5, 10, 0))
    s.commit()
    r = admin.post(f"/trials/{trial.id}/outcome", data={"status": "attended", "outcome": "Loved it"}, follow_redirects=False)
    assert r.status_code == 303, r.status_code
    s.expire_all()
    assert trial.status == "attended" and lead.stage == "trial_done"
    ev = events_of(s, "trial.attended", "lead", lead.id)
    assert len(ev) == 1 and ev[0].payload["outcome"] == "Loved it"
    (nurture,) = runs_of(s, "AUTO-007", "lead", lead.id)
    assert nurture.log[0]["kind"] == "add_tag" and "status:demo-completed" in nurture.log[0]["result"]
    assert nurture.status == "waiting"
    assert auto.has_tag(s, "lead", lead.id, "status:demo-completed")


def test_trial_no_show_emits_its_own_event(s, admin):
    lead = make_lead(s, "Noshow")
    trial = crm.create_trial(s, f"Child Noshow {TAG}", lead=lead, actor=admin_user(s), scheduled_at=datetime(2026, 11, 6, 10, 0))
    s.commit()
    r = admin.post(f"/trials/{trial.id}/outcome", data={"status": "no_show"}, follow_redirects=False)
    assert r.status_code == 303, r.status_code
    s.expire_all()
    assert trial.status == "no_show"
    ev = events_of(s, "trial.no_show", "lead", lead.id)
    assert len(ev) == 1 and ev[0].payload["trial_id"] == trial.id
    assert events_of(s, "trial.attended", "lead", lead.id) == []


# --------------------------------------------------------------------------- billing (app/services/billing.py)
def test_first_confirmed_payment_fires_payment_first_once(s):
    client, _student = make_family(s, "Payer")
    admin = admin_user(s)
    pending = billing.record_payment(s, client, 40, "GBP", "bank_transfer", f"{TAG}-1", user=admin, status="pending")
    s.commit()
    assert events_of(s, "payment.received", "client", client.id) == [], "a pending receipt is not money received"

    billing.confirm_payment(s, pending, admin)
    s.commit()
    received = events_of(s, "payment.received", "client", client.id)
    first = events_of(s, "payment.first", "client", client.id)
    assert len(received) == 1 and len(first) == 1
    assert first[0].payload["first"] is True and first[0].payload["amount"] == "GBP 40.00" and first[0].payload["payment_number"] == pending.payment_number
    (portal,) = runs_of(s, "AUTO-012", "client", client.id)
    assert portal.status == "completed"
    assert [e["kind"] for e in portal.log] == ["add_tag", "send_email", "send_whatsapp"]
    assert auto.has_tag(s, "client", client.id, "status:enrolled")

    billing.record_payment(s, client, 40, "GBP", "bank_transfer", f"{TAG}-2", user=admin, status="completed")
    s.commit()
    received = events_of(s, "payment.received", "client", client.id)
    assert len(received) == 2 and received[1].payload["first"] is False
    assert len(events_of(s, "payment.first", "client", client.id)) == 1, "payment.first fires once per family"
    assert len(runs_of(s, "AUTO-012", "client", client.id)) == 1


def test_freeze_resume_and_cancel_run_the_leave_and_freeze_pipeline(s):
    client, student = make_family(s, "Freezer")
    admin = admin_user(s)
    sub = billing.create_subscription(s, client, student, None, 40.0, "GBP", 0, None, admin, rationale=f"{TAG} subscription")
    s.commit()
    assert sub.status == "active"

    today = date.today()
    billing.freeze_subscription(s, sub, today, today + timedelta(days=10), admin, "Family travelling")
    s.commit()
    assert sub.status == "frozen" and student.status == "frozen"
    ev = events_of(s, "subscription.frozen", "client", client.id)
    assert len(ev) == 1 and ev[0].payload["subscription"] == sub.subscription_code and ev[0].payload["reason"] == "Family travelling"
    (frozen,) = runs_of(s, "P2-FREEZE", "client", client.id)
    assert frozen.log[0]["kind"] == "add_tag" and "status:frozen" in frozen.log[0]["result"]
    assert frozen.log[1]["result"].startswith("whatsapp sent")
    assert frozen.status == "waiting" and frozen.next_run_at == frozen.started_at + timedelta(days=10)
    assert auto.has_tag(s, "client", client.id, "status:frozen")

    billing.unfreeze_subscription(s, sub, admin, "Back home")
    s.commit()
    assert sub.status == "active" and student.status == "active"
    assert len(events_of(s, "subscription.resumed", "client", client.id)) == 1
    (resumed,) = runs_of(s, "P2-RESUME", "client", client.id)
    assert resumed.status == "completed"
    assert [e["kind"] for e in resumed.log] == ["remove_tag", "add_tag", "send_whatsapp", "send_email", "create_task"]
    assert not auto.has_tag(s, "client", client.id, "status:frozen")
    assert auto.has_tag(s, "client", client.id, "status:enrolled")
    teacher_task = s.query(Task).filter(Task.entity_type == "client", Task.entity_id == client.id, Task.id > START["task"]).all()
    assert len(teacher_task) == 1 and teacher_task[0].assignee_id is None, "no teacher on the student yet, so the task is unassigned"

    billing.cancel_subscription(s, sub, admin, "Moving abroad")
    s.commit()
    assert sub.status == "cancelled" and student.status == "cancelled"
    ev = events_of(s, "subscription.cancelled", "client", client.id)
    assert len(ev) == 1 and ev[0].payload["reason"] == "Moving abroad"
    (cancelled,) = runs_of(s, "P3-CANCEL", "client", client.id)
    assert [e["kind"] for e in cancelled.log] == ["add_tag", "remove_tag", "send_whatsapp", "create_task", "wait"]
    assert cancelled.status == "waiting" and cancelled.next_run_at == cancelled.started_at + timedelta(days=30)
    assert auto.has_tag(s, "client", client.id, "status:cancelled")
    assert not auto.has_tag(s, "client", client.id, "status:enrolled")
    winback = (s.query(Task).filter(Task.entity_type == "client", Task.entity_id == client.id, Task.id > START["task"],
                                    Task.title.like("Win-back call%")).one())
    assert winback.assignee_id == client.billing_rep_id and winback.due_date == org_now().date() + timedelta(days=3)


# --------------------------------------------------------------------------- academics (app/services/academic.py)
def test_issuing_a_certificate_starts_the_completion_celebration(s):
    client, student = make_family(s, "Graduate")
    cert = academic.issue_certificate(s, student, None, f"{TAG} Completion", admin_user(s), generate_pdf=False)
    s.commit()
    ev = events_of(s, "course.completed", "student", student.id)
    assert len(ev) == 1 and ev[0].payload["certificate"] == cert.certificate_number and ev[0].payload["title"] == f"{TAG} Completion"
    (celebrate,) = runs_of(s, "AUTO-017", "student", student.id)
    assert celebrate.log[0]["kind"] == "add_tag" and "status:alumni" in celebrate.log[0]["result"]
    assert celebrate.log[1]["result"].startswith("whatsapp sent"), "the student's messages go to the family"
    assert celebrate.log[2]["result"].startswith("email sent")
    assert celebrate.status == "waiting" and celebrate.next_run_at == celebrate.started_at + timedelta(days=3)
    assert auto.has_tag(s, "student", student.id, "status:alumni")
    assert len(runs_of(s, "AUTO-018", "student", student.id)) == 1, "the review request waits its seven days"
    assert len(runs_of(s, "P3-ALUMNI", "student", student.id)) == 1
    family_msg = (s.query(Message).join(Conversation).filter(Conversation.client_id == client.id, Message.direction == "out")
                  .order_by(Message.id.desc()).first())
    assert student.full_name in family_msg.body and cert.certificate_number in family_msg.body


# --------------------------------------------------------------------------- feedback (crm.submit_feedback)
def test_promoter_feedback_starts_the_referral_programme(s):
    client, _student = make_family(s, "Promoter")
    fb = Feedback(respondent_type="client", client_id=client.id, trigger="manual", status="pending", sent_at=datetime.utcnow())
    s.add(fb)
    s.flush()
    crm.submit_feedback(s, fb, 10, None, None)
    s.commit()
    assert fb.status == "submitted" and fb.sentiment == "positive" and not fb.is_negative
    ev = events_of(s, "feedback.submitted", "client", client.id)
    assert len(ev) == 1 and ev[0].payload["nps"] == 10 and ev[0].payload["negative"] is False
    (referral,) = runs_of(s, "AUTO-019", "client", client.id)
    assert referral.status == "completed"
    assert [e["kind"] for e in referral.log] == ["add_tag", "send_whatsapp"]
    assert auto.has_tag(s, "client", client.id, "temp:hot")


def test_detractor_feedback_does_not_start_the_referral_programme(s):
    client, _student = make_family(s, "Detractor")
    fb = Feedback(respondent_type="client", client_id=client.id, trigger="manual", status="pending", sent_at=datetime.utcnow())
    s.add(fb)
    s.flush()
    crm.submit_feedback(s, fb, 5, None, None)
    s.commit()
    assert fb.is_negative and fb.case_id, "a low score is routed to a QA case"
    ev = events_of(s, "feedback.submitted", "client", client.id)
    assert len(ev) == 1 and ev[0].payload["nps"] == 5 and ev[0].payload["negative"] is True
    assert runs_of(s, "AUTO-019", "client", client.id) == []
    assert not auto.has_tag(s, "client", client.id, "temp:hot")


def test_feedback_from_a_student_reaches_the_family(s):
    client, student = make_family(s, "Studentvoice")
    fb = Feedback(respondent_type="student", student_id=student.id, trigger="manual", status="pending", sent_at=datetime.utcnow())
    s.add(fb)
    s.flush()
    crm.submit_feedback(s, fb, 9, None, None)
    s.commit()
    assert len(events_of(s, "feedback.submitted", "client", client.id)) == 1
    assert len(runs_of(s, "AUTO-019", "client", client.id)) == 1, "NPS 9 is still a promoter"


# --------------------------------------------------------------------------- seed (app/seed/automation.py)
def test_seed_is_idempotent_and_leaves_edited_workflows_alone():
    """Run the seed twice inside one rolled-back session: no duplicate tag or workflow, staff edits kept."""
    from app.seed import automation as seed

    session = SessionLocal()
    try:
        seed.run(session)
        tags_after_first = session.query(func.count(Tag.id)).scalar()
        workflows_after_first = session.query(func.count(Workflow.id)).scalar()
        assert workflows_after_first >= len(seed.WORKFLOWS)
        assert tags_after_first >= len(seed.TAGS)

        edited = session.query(Workflow).filter(Workflow.code == "AUTO-003").one()
        custom_steps = [{"kind": "add_tag", "tag": f"{TAG}-edited"}, {"kind": "exit", "reason": "edited by staff"}]
        edited.steps = custom_steps
        edited.name = f"{TAG} renamed"
        edited.is_active = False
        session.flush()

        seed.run(session)
        assert session.query(func.count(Tag.id)).scalar() == tags_after_first
        assert session.query(func.count(Workflow.id)).scalar() == workflows_after_first
        for code in ("AUTO-003", "AUTO-020", "P1-S2", "P1-S8", "P2-FREEZE", "P2-RESUME", "P3-CANCEL", "P3-ALUMNI", "COMP-CONSENT"):
            assert session.query(Workflow).filter(Workflow.code == code).count() == 1, code
        for name in ("status:new-lead", "market:uk", "consent:email", "unsubscribed", "objection:price"):
            assert session.query(Tag).filter(Tag.name == name).count() == 1, name
        dupes = session.query(func.lower(Tag.name)).group_by(func.lower(Tag.name)).having(func.count(Tag.id) > 1).all()
        assert dupes == []

        session.refresh(edited)
        assert edited.steps == custom_steps and edited.name == f"{TAG} renamed" and edited.is_active is False
    finally:
        session.rollback()
        session.close()


def test_seeded_catalogue_validates_and_matches_the_plan(s):
    from app.seed import automation as seed

    for spec in seed.WORKFLOWS:
        assert auto.validate_steps(spec["steps"]) == [], spec["code"]
    expected = {"AUTO-003": "lead.created", "AUTO-005": "trial.scheduled", "AUTO-007": "trial.attended", "AUTO-010": "lead.lost",
                "AUTO-012": "payment.first", "AUTO-017": "course.completed", "AUTO-019": "feedback.submitted", "AUTO-020": "lead.stale",
                "P1-S2": "lead.stage_changed", "P1-S8": "lead.lost", "P2-FREEZE": "subscription.frozen", "P2-RESUME": "subscription.resumed",
                "P3-CANCEL": "subscription.cancelled", "COMP-CONSENT": "lead.converted"}
    for code, trigger in expected.items():
        assert wf(s, code).trigger == trigger, code
    assert wf(s, "P1-S2").trigger_filter == {"stage": "contacted"}
    assert wf(s, "AUTO-019").trigger_filter == {"nps_min": 9}
