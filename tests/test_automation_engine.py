"""The CRM automation engine (app/services/automation.py) on its own: events in, workflow runs out.

Every test builds its own TEST- workflow and its own lead or family directly on the models, and switches
the seeded catalogue (AUTO-003 ... COMP-CONSENT) off inside its own rolled-back session, so the engine is
exercised on its own and the catalogue is never changed on disk. Time is driven through the ``now=``
parameters of emit / advance_run / advance_due_runs; nothing sleeps. The daily detectors are exercised
against records dated in the year 2000 so that no seeded lead or student is old enough (relative to that
clock) to be picked up alongside them.

The suite is re-runnable against the same database. Each test runs inside a session that is rolled back,
so nothing it creates survives; a module-scoped teardown additionally removes anything that carries the
module's tag, and switches back on any seeded workflow that was active when the module started, in case a
code path ever commits. Other modules delete their leads without their contact_tags, automation_events and
notifications, and SQLite hands the freed ids to the next lead, so every assertion here is scoped to rows
created after the test began (``START`` watermarks) and the contact helpers clear orphan tags on the id
they were given.

Run:
    .venv/Scripts/python.exe -m pytest tests/test_automation_engine.py -q -p no:warnings
"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func

from app.core.utils import next_code
from app.database import SessionLocal
from app.models.automation import AutomationEvent, ContactTag, Tag, Workflow, WorkflowRun
from app.models.core import Notification, User
from app.models.crm import Conversation, Lead, LeadActivity, Message
from app.models.ops import Task
from app.models.people import Client, Student
from app.models.scheduling import ClassSession
from app.services import automation as auto
from app.services import crm

TAG = f"TEST-{uuid4().hex[:8]}"
NOW = datetime(2026, 10, 3, 12, 0, 0)
Y2K = datetime(2000, 1, 1, 9, 0, 0)  # detector tests: older than anything the seed or another module creates

START: dict[str, int] = {}
_SEEDED_INACTIVE_AT_START: set[int] = set()

REPLY_EXIT_BUG = ("app/services/automation.py:232 (_should_exit) filters on Conversation.contact_id, which does not exist "
                  "(Conversation has lead_id / client_id); every exit_on.reply workflow raises AttributeError")


# --------------------------------------------------------------------------- fixtures
@pytest.fixture()
def s():
    """A session that is rolled back after the test: the engine never commits, so nothing persists."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _max_id(s, model) -> int:
    return s.query(func.max(model.id)).scalar() or 0


@pytest.fixture(autouse=True)
def _isolated(s):
    """Switch the seeded catalogue off for this test (rolled back afterwards) and take the id watermarks."""
    s.query(Workflow).filter(Workflow.is_active.is_(True), ~Workflow.code.like("TEST-%")).update({"is_active": False}, synchronize_session=False)
    s.flush()
    START.update(event=_max_id(s, AutomationEvent), run=_max_id(s, WorkflowRun), notification=_max_id(s, Notification),
                 task=_max_id(s, Task), message=_max_id(s, Message), activity=_max_id(s, LeadActivity))
    yield


@pytest.fixture(scope="module", autouse=True)
def _purge():
    """Safety net: remove anything carrying this module's tag, should a code path ever commit."""
    session = SessionLocal()
    try:
        _SEEDED_INACTIVE_AT_START.update(i for (i,) in session.query(Workflow.id).filter(Workflow.is_active.is_(False)).all())
    finally:
        session.close()
    yield
    session = SessionLocal()
    try:
        # Any seeded workflow that is off now but was on when we started was switched off by a leaked commit.
        session.query(Workflow).filter(Workflow.is_active.is_(False), ~Workflow.code.like("TEST-%"),
                                       Workflow.id.notin_(_SEEDED_INACTIVE_AT_START or {0})).update({"is_active": True}, synchronize_session=False)
        like = f"%{TAG}%"
        lead_ids = [i for (i,) in session.query(Lead.id).filter(Lead.full_name.ilike(like)).all()]
        client_ids = [i for (i,) in session.query(Client.id).filter(Client.full_name.ilike(like)).all()]
        student_ids = [i for (i,) in session.query(Student.id).filter(Student.full_name.ilike(like)).all()]
        for ctype, ids in (("lead", lead_ids), ("client", client_ids), ("student", student_ids)):
            if not ids:
                continue
            for model in (WorkflowRun, AutomationEvent, ContactTag):
                session.query(model).filter(model.contact_type == ctype, model.contact_id.in_(ids)).delete(synchronize_session=False)
            session.query(Task).filter(Task.entity_type == ctype, Task.entity_id.in_(ids)).delete(synchronize_session=False)
        if lead_ids:
            conv_ids = [i for (i,) in session.query(Conversation.id).filter(Conversation.lead_id.in_(lead_ids)).all()]
            if conv_ids:
                session.query(Message).filter(Message.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
                session.query(Conversation).filter(Conversation.id.in_(conv_ids)).delete(synchronize_session=False)
            session.query(LeadActivity).filter(LeadActivity.lead_id.in_(lead_ids)).delete(synchronize_session=False)
            session.query(Lead).filter(Lead.id.in_(lead_ids)).delete(synchronize_session=False)
        if student_ids:
            session.query(ClassSession).filter(ClassSession.student_id.in_(student_ids)).delete(synchronize_session=False)
            session.query(Student).filter(Student.id.in_(student_ids)).delete(synchronize_session=False)
        if client_ids:
            session.query(Client).filter(Client.id.in_(client_ids)).delete(synchronize_session=False)
        wf_ids = [i for (i,) in session.query(Workflow.id).filter(Workflow.code.like("TEST-%")).all()]
        if wf_ids:
            session.query(WorkflowRun).filter(WorkflowRun.workflow_id.in_(wf_ids)).delete(synchronize_session=False)
            session.query(Workflow).filter(Workflow.id.in_(wf_ids)).delete(synchronize_session=False)
        session.query(Tag).filter(Tag.name.like("TEST-%")).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()


# --------------------------------------------------------------------------- helpers
def closer(s) -> User:
    u = s.query(User).filter(User.email == "closer@oqc.local").first()
    assert u is not None, "the demo lead closer must exist"
    return u


def _clear_orphan_tags(s, contact_type: str, contact_id: int) -> None:
    """A freshly inserted row may have been handed the id of a contact another module deleted without its tags."""
    s.query(ContactTag).filter(ContactTag.contact_type == contact_type, ContactTag.contact_id == contact_id).delete(synchronize_session=False)


def make_lead(s, label: str = "Lead", **extra) -> Lead:
    """A lead built straight on the model, so no hook fires and no seeded workflow starts for it."""
    tail = uuid4().hex[:6]
    fields = dict(lead_code=next_code(s, Lead, "lead_code", "L-"), full_name=f"{TAG} {label}", phone=f"+4477{tail[:5]}0",
                  whatsapp=f"+4477{tail[:5]}0", email=f"{label.lower()}.{tail}@example.com", country="United Kingdom",
                  student_name=f"Child {label}", students_count=1, stage="new", assigned_to_id=closer(s).id, whatsapp_opt_in=True)
    fields.update(extra)
    lead = Lead(**fields)
    s.add(lead)
    s.flush()
    _clear_orphan_tags(s, "lead", lead.id)
    return lead


def make_family(s, label: str = "Family", **student_extra) -> tuple[Client, Student]:
    tail = uuid4().hex[:6]
    client = Client(client_code=next_code(s, Client, "client_code", "C-"), full_name=f"{TAG} {label}", email=f"{label.lower()}.{tail}@example.com",
                    phone=f"+4478{tail[:5]}0", whatsapp=f"+4478{tail[:5]}0", country="United Kingdom", currency="GBP", status="active",
                    billing_rep_id=closer(s).id, whatsapp_opt_in=True)
    s.add(client)
    s.flush()
    _clear_orphan_tags(s, "client", client.id)
    fields = dict(student_code=next_code(s, Student, "student_code", "S-"), client_id=client.id, full_name=f"{TAG} {label} child", age=9,
                  status="active")
    fields.update(student_extra)
    student = Student(**fields)
    s.add(student)
    s.flush()
    _clear_orphan_tags(s, "student", student.id)
    return client, student


_wf_counter = {"n": 0}


def make_workflow(s, steps: list, trigger: str = "manual", **extra) -> Workflow:
    _wf_counter["n"] += 1
    code = f"TEST-{uuid4().hex[:6].upper()}"[:20]
    assert not auto.validate_steps(steps), auto.validate_steps(steps)
    fields = dict(code=code, name=f"{TAG} workflow {_wf_counter['n']}", category="internal", trigger=trigger, trigger_filter={}, steps=steps,
                  exit_on={}, run_once_per_contact=True, is_active=True, sort_no=9000)
    fields.update(extra)
    wf = Workflow(**fields)
    s.add(wf)
    s.flush()
    return wf


def runs_of(s, wf: Workflow, contact_type: str, contact_id: int) -> list[WorkflowRun]:
    return (s.query(WorkflowRun).filter(WorkflowRun.workflow_id == wf.id, WorkflowRun.contact_type == contact_type,
                                        WorkflowRun.contact_id == contact_id).order_by(WorkflowRun.id).all())


def events_of(s, event: str, contact_type: str, contact_id: int) -> list[AutomationEvent]:
    """Events this test produced for the contact (older rows on a reused id belong to other modules)."""
    return (s.query(AutomationEvent).filter(AutomationEvent.event == event, AutomationEvent.contact_type == contact_type,
                                            AutomationEvent.contact_id == contact_id, AutomationEvent.id > START["event"])
            .order_by(AutomationEvent.id).all())


def notifications_for(s, user_id: int, link: str) -> list[Notification]:
    return (s.query(Notification).filter(Notification.user_id == user_id, Notification.event_type == "automation", Notification.link == link,
                                         Notification.id > START["notification"]).order_by(Notification.id).all())


def outbound_messages(s, lead: Lead) -> list[Message]:
    return (s.query(Message).join(Conversation).filter(Conversation.lead_id == lead.id, Message.direction == "out",
                                                       Message.id > START["message"]).order_by(Message.id).all())


def tag_name(label: str) -> str:
    return f"TEST-{label}-{uuid4().hex[:4]}"


# --------------------------------------------------------------------------- the run lifecycle
def test_run_executes_until_the_wait_then_finishes_when_due(s):
    lead = make_lead(s)
    t = tag_name("welcome")
    wf = make_workflow(s, [{"kind": "add_tag", "tag": t},
                           {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, welcome."},
                           {"kind": "wait", "days": 2},
                           {"kind": "notify_staff", "to": "assigned", "title": "Two days on: {{name}}"}])
    started = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert len(started) == 1
    run = started[0]
    assert run.workflow_id == wf.id and run.trigger_event == "manual" and run.started_at == NOW
    assert run.status == "waiting"
    assert run.current_step == 3, "the run parked on the wait and will resume at the step after it"
    assert run.next_run_at == NOW + timedelta(days=2)
    assert [e["kind"] for e in run.log] == ["add_tag", "send_whatsapp", "wait"]
    assert run.log[0]["result"] == f"tag {t} added"
    assert run.log[1]["result"].startswith("whatsapp sent"), run.log[1]
    assert auto.has_tag(s, "lead", lead.id, t)
    assert len(outbound_messages(s, lead)) == 1

    # Not due yet: nothing moves.
    auto.advance_due_runs(s, now=NOW + timedelta(days=1))
    assert run.status == "waiting" and len(run.log) == 3

    counts = auto.advance_due_runs(s, now=NOW + timedelta(days=2))
    assert counts["advanced"] >= 1 and counts["completed"] >= 1
    assert run.status == "completed"
    assert run.finished_at == NOW + timedelta(days=2)
    assert run.next_run_at is None
    assert len(run.log) == 4
    assert run.log[3]["kind"] == "notify_staff" and run.log[3]["result"].startswith("notified ")
    rows = notifications_for(s, lead.assigned_to_id, f"/crm/leads/{lead.id}")
    assert len(rows) == 1 and rows[0].title == f"Two days on: {lead.full_name.split()[0]}"


def test_run_once_per_contact_blocks_a_second_run(s):
    lead = make_lead(s)
    wf = make_workflow(s, [{"kind": "add_tag", "tag": tag_name("once")}], run_once_per_contact=True)
    assert len(auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)) == 1
    assert auto.emit(s, "manual", "lead", lead.id, {}, now=NOW + timedelta(hours=1)) == []
    assert len(runs_of(s, wf, "lead", lead.id)) == 1
    ev = events_of(s, "manual", "lead", lead.id)
    assert [e.runs_started for e in ev] == [1, 0], "the event is recorded either way, but only the first one started a run"


def test_repeatable_workflow_restarts_only_after_the_previous_run_finished(s):
    lead = make_lead(s)
    wf = make_workflow(s, [{"kind": "wait", "days": 1}, {"kind": "add_tag", "tag": tag_name("again")}], run_once_per_contact=False)
    first = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert len(first) == 1 and first[0].status == "waiting"
    assert auto.emit(s, "manual", "lead", lead.id, {}, now=NOW + timedelta(hours=2)) == [], "a live run blocks a second one"
    auto.advance_due_runs(s, now=NOW + timedelta(days=1))
    assert first[0].status == "completed"
    # The session has autoflush off and advance_due_runs does not flush (the scheduler commits after it);
    # flush as that commit would, so the next emit's "is a run still live?" query sees the finished status.
    s.flush()
    second = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW + timedelta(days=1, hours=1))
    assert len(second) == 1 and second[0].id != first[0].id
    assert len(runs_of(s, wf, "lead", lead.id)) == 2


def test_trigger_filter_stage_matches_only_that_stage(s):
    lead = make_lead(s)
    wf = make_workflow(s, [{"kind": "add_tag", "tag": tag_name("contacted")}], trigger="lead.stage_changed", trigger_filter={"stage": "contacted"})
    auto.emit(s, "lead.stage_changed", "lead", lead.id, {"from": "new", "to": "trial_scheduled"}, now=NOW)
    assert runs_of(s, wf, "lead", lead.id) == []
    auto.emit(s, "lead.stage_changed", "lead", lead.id, {"from": "new", "to": "contacted"}, now=NOW + timedelta(minutes=1))
    assert len(runs_of(s, wf, "lead", lead.id)) == 1
    ev = events_of(s, "lead.stage_changed", "lead", lead.id)
    assert [e.runs_started for e in ev] == [0, 1]


def test_trigger_contact_type_must_match_the_event(s):
    """A lead-level event never starts a run for a client, even if a workflow listens for it."""
    client, _student = make_family(s)
    wf = make_workflow(s, [{"kind": "add_tag", "tag": tag_name("wrong-type")}], trigger="lead.created")
    assert auto.emit(s, "lead.created", "client", client.id, {}, now=NOW) == []
    assert runs_of(s, wf, "client", client.id) == []


def test_condition_exit_stops_the_run(s):
    lead = make_lead(s, stage="contacted")
    never = tag_name("never")
    wf = make_workflow(s, [{"kind": "condition", "field": "stage", "op": "eq", "value": "contacted", "then": "exit", "else": "continue"},
                           {"kind": "add_tag", "tag": never}])
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.status == "completed"
    assert "Condition" in (run.stop_reason or "") and "stage eq contacted" in run.stop_reason
    assert len(run.log) == 1 and run.log[0]["kind"] == "condition" and run.log[0]["result"].endswith("yes, stop")
    assert not auto.has_tag(s, "lead", lead.id, never)
    assert run.finished_at == NOW


def test_condition_goto_jumps_over_steps(s):
    lead = make_lead(s, stage="contacted")
    skipped, landed = tag_name("skipped"), tag_name("landed")
    wf = make_workflow(s, [{"kind": "condition", "field": "stage", "op": "eq", "value": "contacted", "then": "goto:2", "else": "continue"},
                           {"kind": "add_tag", "tag": skipped},
                           {"kind": "add_tag", "tag": landed}])
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.status == "completed"
    assert [e["kind"] for e in run.log] == ["condition", "add_tag"]
    assert [e["step"] for e in run.log] == [0, 2]
    assert auto.has_tag(s, "lead", lead.id, landed)
    assert not auto.has_tag(s, "lead", lead.id, skipped)


def test_condition_can_read_tags_and_the_run_context(s):
    lead = make_lead(s)
    marker = tag_name("marker")
    auto.add_tag(s, "lead", lead.id, marker, emit_event=False)
    yes_tag, yes_ctx = tag_name("has-marker"), tag_name("ctx-ok")
    wf = make_workflow(s, [{"kind": "condition", "field": f"tags.{marker}", "op": "truthy", "value": "", "then": "continue", "else": "exit"},
                           {"kind": "add_tag", "tag": yes_tag},
                           {"kind": "condition", "field": "ctx.score", "op": "gte", "value": 50, "then": "continue", "else": "exit"},
                           {"kind": "add_tag", "tag": yes_ctx}])
    (run,) = auto.emit(s, "manual", "lead", lead.id, {"score": 72}, now=NOW)
    assert run.status == "completed" and run.stop_reason is None
    assert auto.has_tag(s, "lead", lead.id, yes_tag) and auto.has_tag(s, "lead", lead.id, yes_ctx)


def test_exit_on_lead_stage_stops_a_waiting_run(s):
    lead = make_lead(s)
    later = tag_name("later")
    wf = make_workflow(s, [{"kind": "wait", "days": 1}, {"kind": "add_tag", "tag": later}], exit_on={"lead_stages": ["won", "lost"]})
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.status == "waiting"
    lead.stage = "lost"
    s.flush()
    auto.advance_run(s, run, now=NOW + timedelta(days=1))
    assert run.status == "stopped"
    assert run.stop_reason == "Lead is lost"
    assert run.log[-1]["kind"] == "exit" and run.log[-1]["result"] == "Lead is lost"
    assert not auto.has_tag(s, "lead", lead.id, later)


def test_exit_on_reply_stops_when_the_contact_wrote_back(s):
    lead = make_lead(s)
    later = tag_name("after-reply")
    wf = make_workflow(s, [{"kind": "send_whatsapp", "body": "Hello {{name}}"}, {"kind": "wait", "days": 1}, {"kind": "add_tag", "tag": later}],
                       exit_on={"reply": True})
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.status == "waiting"
    conv = crm.get_or_create_conversation(s, "lead", lead)
    # An inbound message from before the run started is not a reply to it.
    s.add(Message(conversation_id=conv.id, direction="in", body="old", created_at=NOW - timedelta(days=3)))
    s.flush()
    auto.advance_run(s, run, now=NOW + timedelta(hours=6))
    assert run.status == "waiting", "an inbound message older than the run does not count as a reply"
    s.add(Message(conversation_id=conv.id, direction="in", body="Yes please", created_at=NOW + timedelta(hours=2)))
    s.flush()
    auto.advance_run(s, run, now=NOW + timedelta(days=1))
    assert run.status == "stopped"
    assert run.stop_reason == "Contact replied"
    assert not auto.has_tag(s, "lead", lead.id, later)


def test_exit_on_tag_stops_a_waiting_run(s):
    lead = make_lead(s)
    stop, later = tag_name("stop"), tag_name("after-tag")
    wf = make_workflow(s, [{"kind": "wait", "days": 1}, {"kind": "add_tag", "tag": later}], exit_on={"tags": [stop]})
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    auto.add_tag(s, "lead", lead.id, stop, emit_event=False)
    auto.advance_run(s, run, now=NOW + timedelta(days=1))
    assert run.status == "stopped" and run.stop_reason == f"Tagged {stop}"
    assert not auto.has_tag(s, "lead", lead.id, later)


def test_switched_off_workflow_stops_its_waiting_runs(s):
    lead = make_lead(s)
    wf = make_workflow(s, [{"kind": "wait", "days": 1}, {"kind": "add_tag", "tag": tag_name("off")}])
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.status == "waiting"
    wf.is_active = False
    s.flush()
    counts = auto.advance_due_runs(s, now=NOW + timedelta(days=1))
    assert counts["stopped"] >= 1
    assert run.status == "stopped" and run.stop_reason == "Workflow switched off"
    assert run.next_run_at is None and run.finished_at == NOW + timedelta(days=1)


def test_failing_step_marks_the_run_failed_without_raising(s):
    lead = make_lead(s)
    wf = make_workflow(s, [{"kind": "create_task", "to": "assigned", "title": "Broken", "due_days": "abc"},
                           {"kind": "add_tag", "tag": tag_name("unreached")}])
    started = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)  # must not raise
    assert len(started) == 1
    run = started[0]
    assert run.status == "failed"
    assert run.log[0]["kind"] == "create_task" and run.log[0]["result"].startswith("error:")
    assert "abc" in run.log[0]["result"]
    assert run.stop_reason and "abc" in run.stop_reason
    assert len(run.log) == 1, "the run stops at the failing step"
    assert run.finished_at == NOW
    # A later advance leaves a failed run alone.
    auto.advance_due_runs(s, now=NOW + timedelta(days=5))
    assert run.status == "failed" and len(run.log) == 1


def test_run_for_a_deleted_contact_is_stopped(s):
    lead = make_lead(s)
    wf = make_workflow(s, [{"kind": "wait", "days": 1}, {"kind": "add_tag", "tag": tag_name("ghost")}])
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    s.query(LeadActivity).filter(LeadActivity.lead_id == lead.id).delete(synchronize_session=False)
    s.delete(lead)
    s.flush()
    auto.advance_run(s, run, now=NOW + timedelta(days=1))
    assert run.status == "stopped" and run.stop_reason == "Contact no longer exists"


# --------------------------------------------------------------------------- the step kinds
def test_send_email_skips_unsubscribed_contacts_and_contacts_without_an_address(s):
    wf = make_workflow(s, [{"kind": "send_email", "subject": "Hello {{name}}", "body": "Body for {{student}}"}])
    unsub = make_lead(s, "Unsub")
    auto.add_tag(s, "lead", unsub.id, "unsubscribed", emit_event=False)
    (run,) = auto.emit(s, "manual", "lead", unsub.id, {}, now=NOW)
    assert run.status == "completed"
    assert run.log[0]["result"] == "skipped: contact unsubscribed from email"

    no_email = make_lead(s, "Noemail", email=None)
    (run2,) = auto.emit(s, "manual", "lead", no_email.id, {}, now=NOW)
    assert run2.log[0]["result"] == "skipped: no email address"

    fine = make_lead(s, "Mailable")
    (run3,) = auto.emit(s, "manual", "lead", fine.id, {}, now=NOW)
    assert run3.log[0]["result"].startswith("email sent")


def test_send_whatsapp_skips_when_not_opted_in(s):
    lead = make_lead(s, "Optout", whatsapp_opt_in=False)
    wf = make_workflow(s, [{"kind": "send_whatsapp", "body": "Hello {{name}}"}])
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.status == "completed"
    assert run.log[0]["result"] == "skipped: contact has not opted in to WhatsApp"
    assert outbound_messages(s, lead) == []


def test_send_whatsapp_skips_without_a_phone_number(s):
    lead = make_lead(s, "Nophone", phone=None, whatsapp=None)
    wf = make_workflow(s, [{"kind": "send_whatsapp", "template": "lead_first_touch"}])
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.log[0]["result"] == "skipped: no phone number"


def test_send_whatsapp_for_a_student_goes_to_the_family(s):
    client, student = make_family(s)
    wf = make_workflow(s, [{"kind": "send_whatsapp", "body": "{{student}} did well today, {{name}}."}])
    (run,) = auto.emit(s, "manual", "student", student.id, {}, now=NOW)
    assert run.log[0]["result"].startswith("whatsapp sent")
    msg = (s.query(Message).join(Conversation).filter(Conversation.client_id == client.id, Message.id > START["message"]).one())
    assert msg.body == f"{student.full_name} did well today, {client.full_name.split()[0]}."


def test_send_sms_is_recorded_as_skipped(s):
    wf = make_workflow(s, [{"kind": "send_sms", "body": "Hello {{name}}"}])
    no_consent = make_lead(s, "Nosms")
    (run,) = auto.emit(s, "manual", "lead", no_consent.id, {}, now=NOW)
    assert run.status == "completed"
    assert run.log[0]["result"] == "skipped: no SMS consent tag"

    consented = make_lead(s, "Smsok")
    auto.add_tag(s, "lead", consented.id, "consent:sms", emit_event=False)
    (run2,) = auto.emit(s, "manual", "lead", consented.id, {}, now=NOW)
    assert run2.status == "completed"
    assert run2.log[0]["result"].startswith("skipped: no SMS provider configured")


def test_add_tag_emits_tag_added_and_starts_a_tag_triggered_workflow(s):
    lead = make_lead(s)
    wf = make_workflow(s, [{"kind": "add_tag", "tag": tag_name("handled")}], trigger="tag.added", trigger_filter={"tag": "objection:price"})
    other = make_workflow(s, [{"kind": "add_tag", "tag": tag_name("other")}], trigger="tag.added", trigger_filter={"tag": "scholarship"})

    assert auto.add_tag(s, "lead", lead.id, "objection:price", added_by="pytest") is True

    def price_events():
        return [e for e in events_of(s, "tag.added", "lead", lead.id) if e.payload.get("tag") == "objection:price"]

    ev = price_events()
    assert len(ev) == 1 and ev[0].payload["category"] == "other" and ev[0].runs_started == 1
    assert len(runs_of(s, wf, "lead", lead.id)) == 1
    assert runs_of(s, other, "lead", lead.id) == [], "the filter keeps the other tag's workflow out"
    # The triggered workflow's own add_tag step emitted a tag.added of its own (for its TEST- tag).
    assert len(events_of(s, "tag.added", "lead", lead.id)) == 2

    # Adding the same tag again is a no-op: no second event, no second run.
    assert auto.add_tag(s, "lead", lead.id, "objection:price") is False
    assert len(price_events()) == 1
    assert len(runs_of(s, wf, "lead", lead.id)) == 1
    ct = s.query(ContactTag).join(Tag).filter(ContactTag.contact_type == "lead", ContactTag.contact_id == lead.id, Tag.name == "objection:price").one()
    assert ct.added_by == "pytest"


def test_add_tag_step_inside_a_run_also_fires_tag_added(s):
    lead = make_lead(s)
    first = make_workflow(s, [{"kind": "add_tag", "tag": "scholarship"}])
    chained = make_workflow(s, [{"kind": "add_tag", "tag": tag_name("chained")}], trigger="tag.added", trigger_filter={"tag": "scholarship"})
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.status == "completed"
    chained_runs = runs_of(s, chained, "lead", lead.id)
    assert len(chained_runs) == 1 and chained_runs[0].trigger_event == "tag.added"
    ct = s.query(ContactTag).join(Tag).filter(ContactTag.contact_type == "lead", ContactTag.contact_id == lead.id, Tag.name == "scholarship").one()
    assert ct.added_by == f"workflow:{first.code}"


def test_remove_tag_step(s):
    lead = make_lead(s)
    t = tag_name("gone")
    auto.add_tag(s, "lead", lead.id, t, emit_event=False)
    assert auto.has_tag(s, "lead", lead.id, t)
    wf = make_workflow(s, [{"kind": "remove_tag", "tag": t}, {"kind": "remove_tag", "tag": t}])
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.log[0]["result"] == f"tag {t} removed"
    assert run.log[1]["result"] == f"tag {t} not present"
    assert not auto.has_tag(s, "lead", lead.id, t)
    assert s.query(Tag).filter(Tag.name == t).count() == 1, "removing a tag from a contact does not delete the tag itself"


def test_create_task_step_creates_an_ops_task_linked_to_the_contact(s):
    lead = make_lead(s)
    wf = make_workflow(s, [{"kind": "create_task", "to": "assigned", "title": "Call {{name}}", "due_days": 3, "priority": "high"}])
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.status == "completed"
    task = s.query(Task).filter(Task.entity_type == "lead", Task.entity_id == lead.id, Task.id > START["task"]).one()
    assert task.assignee_id == lead.assigned_to_id
    assert task.title.startswith(f"Call {lead.full_name.split()[0]}: {lead.lead_code}")
    assert task.priority == "high" and task.status == "todo"
    from app.services.people import org_now
    assert task.due_date == org_now().date() + timedelta(days=3)  # the college's calendar, not the server's
    assert wf.code in (task.description or "")
    assert run.log[0]["result"] == f"task #{task.id} for {closer(s).full_name}"


def test_notify_staff_assigned_notifies_the_leads_rep(s):
    lead = make_lead(s)
    wf = make_workflow(s, [{"kind": "notify_staff", "to": "assigned", "title": "Look at {{name}}", "body": "{{student}} is waiting"}])
    before = s.query(func.count(Notification.id)).filter(Notification.user_id == lead.assigned_to_id).scalar()
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.status == "completed"
    rows = notifications_for(s, lead.assigned_to_id, f"/crm/leads/{lead.id}")
    assert len(rows) == 1
    assert rows[0].title == f"Look at {lead.full_name.split()[0]}"
    assert rows[0].body == "Child Lead is waiting"
    assert rows[0].channel == "in_app" and rows[0].status == "delivered"
    assert s.query(func.count(Notification.id)).filter(Notification.user_id == lead.assigned_to_id).scalar() == before + 1
    assert run.log[0]["result"] == f"notified {closer(s).full_name}"


def test_notify_staff_to_a_named_user_and_to_nobody(s):
    lead = make_lead(s)
    wf = make_workflow(s, [{"kind": "notify_staff", "to": "user:admin@oqc.local", "title": "Admin look"},
                           {"kind": "notify_staff", "to": "user:nobody@oqc.local", "title": "Nobody"}])
    admin = s.query(User).filter(User.email == "admin@oqc.local").first()
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.log[0]["result"] == f"notified {admin.full_name}"
    assert run.log[1]["result"] == "skipped: nobody to notify"
    assert len(notifications_for(s, admin.id, f"/crm/leads/{lead.id}")) == 1


def test_move_stage_step_moves_the_lead_and_records_an_activity(s):
    lead = make_lead(s)
    wf = make_workflow(s, [{"kind": "move_stage", "stage": "trial_scheduled", "reason": "Workflow test"}])
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.status == "completed"
    assert run.log[0]["result"] == "moved to trial_scheduled"
    assert lead.stage == "trial_scheduled"
    act = (s.query(LeadActivity).filter(LeadActivity.lead_id == lead.id, LeadActivity.activity_type == "stage_change",
                                        LeadActivity.id > START["activity"]).order_by(LeadActivity.id.desc()).first())
    assert act is not None and "new -> trial_scheduled" in act.note and "Workflow test" in act.note
    assert len(events_of(s, "lead.stage_changed", "lead", lead.id)) == 1, "the move goes through crm.move_stage, which emits the stage event"


def test_move_stage_step_on_a_bad_stage_is_skipped_not_fatal(s):
    lead = make_lead(s)
    wf = Workflow(code=f"TEST-{uuid4().hex[:6].upper()}", name=f"{TAG} bad stage", category="internal", trigger="manual", trigger_filter={},
                  steps=[{"kind": "move_stage", "stage": "nowhere"}], exit_on={}, run_once_per_contact=True, is_active=True, sort_no=9000)
    s.add(wf)
    s.flush()
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.status == "completed"
    assert run.log[0]["result"] == "skipped: unknown stage nowhere"
    assert lead.stage == "new"


def test_exit_step_finishes_the_run_with_its_reason(s):
    lead = make_lead(s)
    wf = make_workflow(s, [{"kind": "exit", "reason": "Nothing to do"}, {"kind": "add_tag", "tag": tag_name("after-exit")}])
    (run,) = auto.emit(s, "manual", "lead", lead.id, {}, now=NOW)
    assert run.status == "completed" and run.stop_reason == "Nothing to do"
    assert [e["kind"] for e in run.log] == ["exit"]


# --------------------------------------------------------------------------- validation
def test_validate_steps_rejects_unknown_kinds():
    errors = auto.validate_steps([{"kind": "teleport", "to": "mars"}])
    assert len(errors) == 1 and errors[0].startswith("Step 1: unknown kind") and "teleport" in errors[0]
    assert auto.validate_steps([{"kind": "add_tag", "tag": "ok"}, {"nokind": True}]) == ["Step 2: unknown kind None"]


def test_validate_steps_rejects_a_wait_without_a_duration():
    errors = auto.validate_steps([{"kind": "wait"}])
    assert errors == ["Step 1: wait needs hours or days"]
    assert auto.validate_steps([{"kind": "wait", "hours": 12}]) == []
    assert auto.validate_steps([{"kind": "wait", "days": 1}]) == []


def test_validate_steps_rejects_a_goto_outside_the_steps():
    steps = [{"kind": "condition", "field": "stage", "op": "eq", "value": "new", "then": "goto:5", "else": "continue"},
             {"kind": "add_tag", "tag": "x"}]
    errors = auto.validate_steps(steps)
    assert errors == ["Step 1: then goto 5 is outside the steps"]
    assert auto.validate_steps([{"kind": "condition", "field": "stage", "then": "goto:1"}, {"kind": "add_tag", "tag": "x"}]) == []
    assert auto.validate_steps([{"kind": "condition", "field": "stage", "else": "goto:nope"}]) == ["Step 1: else goto must be a step number"]


def test_validate_steps_catches_the_other_required_fields():
    errors = auto.validate_steps([{"kind": "send_whatsapp"}, {"kind": "send_email"}, {"kind": "add_tag"}, {"kind": "remove_tag"},
                                  {"kind": "move_stage", "stage": "nowhere"}, {"kind": "condition"}])
    assert len(errors) == 6
    assert auto.validate_steps("not a list") == ["Steps must be a list"]
    assert auto.validate_steps(["not a dict"]) == ["Step 1: unknown kind 'not a dict'"]


# --------------------------------------------------------------------------- tags API
def test_tag_helpers_are_case_insensitive_and_idempotent(s):
    lead = make_lead(s)
    t = tag_name("Case")
    created = auto.get_tag(s, t, category="other")
    assert created is not None and auto.get_tag(s, t.upper(), create=False) is created
    assert auto.get_tag(s, "   ") is None
    assert auto.add_tag(s, "lead", lead.id, t.lower(), emit_event=False) is True
    assert auto.add_tag(s, "lead", lead.id, t.upper(), emit_event=False) is False
    assert auto.has_tag(s, "lead", lead.id, t.upper())
    assert [x.name for x in auto.tags_for(s, "lead", lead.id)] == [t]
    assert auto.add_tag(s, "lead", lead.id, "", emit_event=False) is False, "a blank tag is never created"
    assert auto.remove_tag(s, "lead", lead.id, t) is True
    assert auto.remove_tag(s, "lead", lead.id, t) is False
    assert auto.remove_tag(s, "lead", lead.id, tag_name("never-created")) is False
    assert auto.tags_for(s, "lead", lead.id) == []


def test_start_manually_runs_the_workflow_regardless_of_trigger(s):
    lead = make_lead(s)
    t = tag_name("manual")
    wf = make_workflow(s, [{"kind": "add_tag", "tag": t}], trigger="payment.first")
    admin = s.query(User).filter(User.email == "admin@oqc.local").first()
    run = auto.start_manually(s, wf, "lead", lead.id, admin, now=NOW)
    assert run.status == "completed" and run.trigger_event == "manual"
    assert run.context == {"started_by": "admin@oqc.local"}
    assert auto.has_tag(s, "lead", lead.id, t)


# --------------------------------------------------------------------------- daily detectors
def test_detect_stale_leads_emits_once_per_lead(s):
    lead = make_lead(s, "Stale", phone=None, whatsapp=None, created_at=Y2K, last_contacted_at=None)
    clock = Y2K + timedelta(days=31)
    n = auto.detect_stale_leads(s, now=clock)
    assert n >= 1
    ev = events_of(s, "lead.stale", "lead", lead.id)
    assert len(ev) == 1
    assert ev[0].payload == {"days": auto.STALE_LEAD_DAYS, "stage": "new"} and ev[0].created_at == clock
    assert auto.detect_stale_leads(s, now=clock + timedelta(days=1)) == 0
    assert len(events_of(s, "lead.stale", "lead", lead.id)) == 1


def test_detect_stale_leads_starts_a_stale_workflow(s):
    lead = make_lead(s, "Stale2", phone=None, whatsapp=None, created_at=Y2K)
    wf = make_workflow(s, [{"kind": "add_tag", "tag": tag_name("cold")}], trigger="lead.stale")
    auto.detect_stale_leads(s, now=Y2K + timedelta(days=31))
    runs = runs_of(s, wf, "lead", lead.id)
    assert len(runs) == 1 and runs[0].status == "completed" and runs[0].context["stage"] == "new"


def test_detect_stale_leads_ignores_recently_contacted_and_closed_leads(s):
    fresh = make_lead(s, "Fresh", phone=None, whatsapp=None, created_at=Y2K, last_contacted_at=Y2K + timedelta(days=29))
    lost = make_lead(s, "Lost", phone=None, whatsapp=None, created_at=Y2K, stage="lost", lost_reason="moved")
    auto.detect_stale_leads(s, now=Y2K + timedelta(days=31))
    assert events_of(s, "lead.stale", "lead", fresh.id) == []
    assert events_of(s, "lead.stale", "lead", lost.id) == []


def test_detect_inactive_students_emits_at_most_once_in_thirty_days(s):
    client, student = make_family(s, "Quiet", created_at=Y2K)
    clock = Y2K + timedelta(days=20)
    assert auto.detect_inactive_students(s, now=clock) >= 1
    ev = events_of(s, "student.inactive", "student", student.id)
    assert len(ev) == 1 and ev[0].payload == {"days": auto.INACTIVE_STUDENT_DAYS}
    assert auto.detect_inactive_students(s, now=clock + timedelta(days=1)) == 0
    assert len(events_of(s, "student.inactive", "student", student.id)) == 1
    auto.detect_inactive_students(s, now=clock + timedelta(days=31))
    assert len(events_of(s, "student.inactive", "student", student.id)) == 2, "after thirty days the check speaks up again"


def test_detect_inactive_students_skips_a_student_with_a_recent_class(s):
    client, student = make_family(s, "Busy", created_at=Y2K)
    teacher_id = s.query(func.min(ClassSession.teacher_id)).scalar() or 1
    clock = Y2K + timedelta(days=20)
    when = clock - timedelta(days=3)
    s.add(ClassSession(student_id=student.id, teacher_id=teacher_id, date=when.date(), start_time=when.time(),
                       end_time=(when + timedelta(minutes=30)).time(), scheduled_start=when, duration_minutes=30, status="done"))
    s.flush()
    auto.detect_inactive_students(s, now=clock)
    assert events_of(s, "student.inactive", "student", student.id) == []


# --------------------------------------------------------------------------- stats
def test_workflow_stats_returns_the_dashboard_keys(s):
    stats = auto.workflow_stats(s)
    assert set(stats) == {"workflows", "active_workflows", "waiting", "today", "completed", "failed", "events_today"}
    assert all(isinstance(v, int) and v >= 0 for v in stats.values())
    assert stats["active_workflows"] <= stats["workflows"]
    lead = make_lead(s)
    make_workflow(s, [{"kind": "wait", "days": 1}])
    auto.emit(s, "manual", "lead", lead.id, {}, now=datetime.utcnow())
    after = auto.workflow_stats(s)
    assert after["workflows"] == stats["workflows"] + 1
    assert after["active_workflows"] == stats["active_workflows"] + 1
    assert after["waiting"] == stats["waiting"] + 1
    assert after["today"] == stats["today"] + 1
    assert after["events_today"] == stats["events_today"] + 1
