"""The two Configuration gaps from the 17 September parity walk: QA Feedback Questions and Confido Agents.

Self-contained: defaults DATABASE_URL to the private database data/oqc_parB.db (seed it first with
    $env:DATABASE_URL='sqlite:///./data/oqc_parB.db'; .venv/Scripts/python.exe seed.py --reset
) so the shared development database is never locked. Because tests/conftest.py imports the app before this
module, set the variable in the shell when running under pytest.

Re-runnable against the same database: everything this module creates (questions, feedbacks, licences, devices,
screenshots and the uploaded files) is purged before and after the run, no assertion counts rows that only a
fresh seed would hold, and the branch property it lowers is restored. ASCII output only.

Run:  $env:DATABASE_URL='sqlite:///./data/oqc_parB.db'; .venv/Scripts/python.exe -m pytest tests/test_parity_config.py -q
"""
from __future__ import annotations

import io
import os
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_parB.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.config_erp import AgentDevice, AgentLicense, AgentScreenshot  # noqa: E402
from app.models.core import AuditEvent, Setting, User  # noqa: E402
from app.models.crm import Case, Feedback  # noqa: E402
from app.models.erp import FeedbackQuestion  # noqa: E402
from app.models.people import Client  # noqa: E402
from app.services.jobs_system import agents_mark_offline  # noqa: E402
from app.web.company_config import (AGENT_LICENSES_SETTING, AGENT_SCREENSHOT_DIR, agent_licenses_allowed,  # noqa: E402
                                    agent_licenses_consumed)
from app.web.feedback import active_questions  # noqa: E402

ACAD = "/academics/config"
CFG = "/config"
API = "/api/agents"

TAG = "[pytest parity]"                       # every question / note / comment this module creates carries it
MACHINE_PREFIX = "PYTEST-MACHINE-"
LICENSE_NOTE = "pytest parity licence"

GET_PAGES = [
    f"{ACAD}", f"{ACAD}/feedback-questions", f"{ACAD}/feedback-questions?status=active",
    f"{ACAD}/feedback-questions?status=inactive", f"{ACAD}/feedback-questions?applies_to=client",
    f"{ACAD}/feedback-questions?applies_to=student", f"{ACAD}/feedback-questions?applies_to=staff",
    f"{ACAD}/feedback-questions?answer_type=rating&q=teacher",
    f"{CFG}", f"{CFG}/agents", f"{CFG}/agents?tab=licenses", f"{CFG}/agents?tab=devices",
    f"{CFG}/agents?tab=devices&status=online", f"{CFG}/agents?tab=devices&status=offline",
    f"{CFG}/agents?tab=devices&status=blocked", f"{CFG}/agents?tab=devices&q=SEED",
    f"{CFG}/agents?tab=screenshots", f"{CFG}/agents?tab=screenshots&date_from=2020-01-01&date_to=2099-12-31",
    f"{CFG}/agents?tab=nonsense", "/qa/feedbacks",
]


# --------------------------------------------------------------------------- fixtures
def _purge(db) -> None:
    """Remove everything this module creates, including the files the screenshot API stored."""
    for fb in db.query(Feedback).filter(Feedback.comment.like(f"{TAG}%")).all():
        if fb.case_id:
            case = db.get(Case, fb.case_id)
            if case:
                db.delete(case)
        db.delete(fb)
    for q in db.query(FeedbackQuestion).filter(FeedbackQuestion.question.like(f"{TAG}%")).all():
        db.delete(q)
    devices = db.query(AgentDevice).filter(AgentDevice.machine_id.like(f"{MACHINE_PREFIX}%")).all()
    for d in devices:
        for s in db.query(AgentScreenshot).filter(AgentScreenshot.device_id == d.id).all():
            db.delete(s)
        shutil.rmtree(settings.storage_dir / AGENT_SCREENSHOT_DIR / str(d.id), ignore_errors=True)
        db.delete(d)
    for lic in db.query(AgentLicense).filter(AgentLicense.notes == LICENSE_NOTE).all():
        db.delete(lic)
    db.commit()


@pytest.fixture(scope="module", autouse=True)
def _schema():
    init_db()
    db = SessionLocal()
    try:
        _purge(db)
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        _purge(db)
    finally:
        db.close()


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _login(username: str, password: str) -> TestClient:
    c = TestClient(app)
    c.__enter__()
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}: {r.status_code}"
    return c


@pytest.fixture(scope="module")
def admin():
    c = _login("admin@oqc.local", "Admin@12345")
    yield c
    c.__exit__(None, None, None)


@pytest.fixture(scope="module")
def billing():
    c = _login("billing@oqc.local", "Billing@123")
    yield c
    c.__exit__(None, None, None)


@pytest.fixture(scope="module")
def teacher():
    c = _login("teacher1@oqc.local", "Teacher@123")
    yield c
    c.__exit__(None, None, None)


@pytest.fixture(scope="module")
def parent():
    c = _login("parent1@oqc.local", "Parent@123")
    yield c
    c.__exit__(None, None, None)


@pytest.fixture(scope="module")
def anon():
    c = TestClient(app)
    c.__enter__()
    yield c
    c.__exit__(None, None, None)


def _post(client: TestClient, url: str, data: dict | None = None) -> str:
    r = client.post(url, data=data or {}, follow_redirects=False)
    assert r.status_code == 303, f"POST {url} -> {r.status_code}\n{r.text[:600]}"
    return r.headers.get("location", "")


def _flash(client: TestClient, location: str) -> str:
    """Follow a redirect and return the page, whose flash message the assertions read."""
    r = client.get(location)
    assert r.status_code == 200, f"GET {location} -> {r.status_code}"
    return r.text


def _question(db, **kw) -> FeedbackQuestion:
    return db.query(FeedbackQuestion).filter_by(**kw).first()


def _set_allowed(db, value):
    s = db.query(Setting).filter(Setting.key == AGENT_LICENSES_SETTING).first()
    assert s is not None, "the agent_licenses_allowed branch property must be seeded"
    s.value = {"value": value}
    db.commit()


def _png() -> bytes:
    """The smallest PNG worth uploading: the seed's generator, one pixel wide."""
    from app.seed.parity_config import _png_bytes
    return _png_bytes(8, 6)


# =========================================================================== seed expectations
def test_seed_loaded_questions_and_agents(db):
    assert db.query(FeedbackQuestion).filter(FeedbackQuestion.applies_to == "client", FeedbackQuestion.status == "active").count() >= 1
    assert db.query(FeedbackQuestion).filter(FeedbackQuestion.applies_to == "student").count() >= 1
    assert db.query(FeedbackQuestion).filter(FeedbackQuestion.applies_to == "staff").count() >= 1
    assert db.query(FeedbackQuestion).filter(FeedbackQuestion.question_urdu.isnot(None)).count() >= 1
    assert db.query(Setting).filter(Setting.key == AGENT_LICENSES_SETTING).first() is not None
    assert db.query(AgentLicense).filter(AgentLicense.status == "revoked").count() >= 1
    statuses = {s for (s,) in db.query(AgentDevice.status).distinct().all()}
    assert {"offline", "blocked"} <= statuses
    # The seed leaves two devices online with a heartbeat a few minutes old; once ten minutes have passed the
    # offline job (run by the scheduler or by the test below) is right to show them offline, so accept either.
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    aged = db.query(AgentDevice).filter(AgentDevice.machine_id.like("SEED-%"), AgentDevice.status == "offline",
                                        AgentDevice.last_seen_at < cutoff).count()
    assert "online" in statuses or aged >= 1
    assert db.query(AgentScreenshot).count() >= 30
    assert db.query(AgentDevice).filter(AgentDevice.employee_id.isnot(None)).count() >= 5
    placeholder = settings.storage_dir / "agent_screenshots" / "seed" / "placeholder.png"
    assert placeholder.exists() and placeholder.stat().st_size > 0


# =========================================================================== every GET
@pytest.mark.parametrize("path", GET_PAGES)
def test_every_page_renders_for_admin(admin, path):
    r = admin.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"


def test_index_cards_carry_the_new_entries(admin):
    assert f"{ACAD}/feedback-questions" in admin.get(ACAD).text
    assert f"{CFG}/agents" in admin.get(CFG).text


def test_agents_tabs_show_their_own_content(admin, db):
    licences = admin.get(f"{CFG}/agents?tab=licenses").text
    assert "Allowed" in licences and "Consumed" in licences and "Generate License" in licences and "Download" in licences
    devices = admin.get(f"{CFG}/agents?tab=devices").text
    seeded = db.query(AgentDevice).filter(AgentDevice.machine_id.like("SEED-%")).first()
    assert seeded is not None and seeded.machine_id in devices
    shots = admin.get(f"{CFG}/agents?tab=screenshots").text
    # Captures of staff screens are served through the guarded route, never from the open static mount.
    assert "/config/agents/screenshots/" in shots and "/storage/agent_screenshots/" not in shots


# =========================================================================== feedback questions
def test_question_create_edit_toggle_and_move(admin, db):
    loc = _post(admin, f"{ACAD}/feedback-questions/new", {
        "question": f"{TAG} Was the class link sent on time?", "question_urdu": "کیا کلاس کا لنک وقت پر بھیجا گیا؟",
        "answer_type": "yes_no", "applies_to": "client", "is_required": "1", "status": "active"})
    assert loc.startswith(f"{ACAD}/feedback-questions")
    q = _question(db, question=f"{TAG} Was the class link sent on time?")
    assert q is not None and q.answer_type == "yes_no" and q.is_required is True
    assert q.sort_no >= 1, "a blank sort number takes the next slot in the audience"
    first_sort = q.sort_no

    _post(admin, f"{ACAD}/feedback-questions/new", {
        "question": f"{TAG} Rate the reminder messages", "answer_type": "rating", "applies_to": "client", "status": "active"})
    db.expire_all()
    q2 = _question(db, question=f"{TAG} Rate the reminder messages")
    assert q2.sort_no == first_sort + 1

    # inline edit
    _post(admin, f"{ACAD}/feedback-questions/{q.id}/edit", {
        "question": f"{TAG} Was the class link sent on time (edited)?", "question_urdu": "کیا کلاس کا لنک وقت پر بھیجا گیا؟",
        "answer_type": "text", "applies_to": "client", "sort_no": q.sort_no, "status": "active"})
    db.expire_all()
    q = db.get(FeedbackQuestion, q.id)
    assert q.question.endswith("(edited)?") and q.answer_type == "text" and q.is_required is False

    # a blank question is refused
    r = admin.post(f"{ACAD}/feedback-questions/new", data={"question": "   ", "applies_to": "staff"}, follow_redirects=False)
    assert r.status_code == 303
    assert _question(db, question="") is None

    # toggle
    _post(admin, f"{ACAD}/feedback-questions/{q.id}/toggle")
    db.expire_all()
    assert db.get(FeedbackQuestion, q.id).status == "inactive"
    _post(admin, f"{ACAD}/feedback-questions/{q.id}/toggle")
    db.expire_all()
    assert db.get(FeedbackQuestion, q.id).status == "active"

    # move: q2 sits below q; moving it up swaps the two
    _post(admin, f"{ACAD}/feedback-questions/{q2.id}/move", {"direction": "up"})
    db.expire_all()
    q, q2 = db.get(FeedbackQuestion, q.id), db.get(FeedbackQuestion, q2.id)
    assert q2.sort_no < q.sort_no
    _post(admin, f"{ACAD}/feedback-questions/{q2.id}/move", {"direction": "down"})
    db.expire_all()
    q, q2 = db.get(FeedbackQuestion, q.id), db.get(FeedbackQuestion, q2.id)
    assert q.sort_no < q2.sort_no

    page = admin.get(f"{ACAD}/feedback-questions?applies_to=client").text
    assert "(edited)?" in page and "کیا کلاس کا لنک" in page


def test_feedback_form_renders_active_client_questions_in_order(admin, db):
    _post(admin, f"{ACAD}/feedback-questions/new", {
        "question": f"{TAG} Form question A", "answer_type": "rating", "applies_to": "client", "is_required": "1", "sort_no": "990"})
    _post(admin, f"{ACAD}/feedback-questions/new", {
        "question": f"{TAG} Form question B", "answer_type": "text", "applies_to": "client", "sort_no": "991"})
    _post(admin, f"{ACAD}/feedback-questions/new", {
        "question": f"{TAG} Form question retired", "answer_type": "yes_no", "applies_to": "client", "sort_no": "992", "status": "inactive"})
    _post(admin, f"{ACAD}/feedback-questions/new", {
        "question": f"{TAG} Staff-only question", "answer_type": "rating", "applies_to": "staff", "sort_no": "993"})
    page = admin.get("/qa/feedbacks").text
    a, b = page.find(f"{TAG} Form question A"), page.find(f"{TAG} Form question B")
    assert a > 0 and b > a, "active client questions render in sort order"
    assert f"{TAG} Form question retired" not in page
    assert f"{TAG} Staff-only question" not in page
    qa = _question(db, question=f"{TAG} Form question A")
    assert f'name="fq_{qa.id}"' in page


def test_feedback_submitted_through_the_form_stores_answers_by_question_id(admin, db):
    qa = _question(db, question=f"{TAG} Form question A")
    qb = _question(db, question=f"{TAG} Form question B")
    client = db.query(Client).filter(Client.status.in_(["active", "regular", "trial"])).order_by(Client.id).first()
    assert client is not None
    base = {"client_id": client.id, "date": date.today().isoformat(), "rating": "4"}
    # every other active client question (the seed's included) answered per its type, so only qa decides the outcome
    others = {}
    for q in active_questions(db, "client"):
        if q.id in (qa.id, qb.id):
            continue
        others[f"fq_{q.id}"] = {"rating": "3", "yes_no": "yes"}.get(q.answer_type, "fine")

    # the required rating question left blank is refused and nothing is stored
    before = db.query(Feedback).filter(Feedback.comment.like(f"{TAG}%")).count()
    loc = _post(admin, "/qa/feedbacks/new", {**base, **others, "comment": f"{TAG} missing answer", f"fq_{qb.id}": "some text"})
    assert "Please answer the required question" in _flash(admin, loc)
    assert db.query(Feedback).filter(Feedback.comment.like(f"{TAG}%")).count() == before

    # answered: stored keyed by question id, the overall rating and comment kept
    loc = _post(admin, "/qa/feedbacks/new", {**base, **others, "comment": f"{TAG} answered", f"fq_{qa.id}": "5",
                                             f"fq_{qb.id}": "Please send the link earlier"})
    assert loc.startswith("/qa/feedbacks")
    assert "Please answer" not in _flash(admin, loc)
    fb = db.query(Feedback).filter(Feedback.comment == f"{TAG} answered").order_by(Feedback.id.desc()).first()
    assert fb is not None and fb.rating == 4 and fb.feedback_source == "manual"
    assert fb.answers[str(qa.id)] == 5
    assert fb.answers[str(qb.id)] == "Please send the link earlier"
    assert fb.answers.get("entered_by"), "the existing entered-by note is kept alongside the answers"

    # the detail page shows each question with its answer
    detail = admin.get(f"/feedback/{fb.id}").text
    assert f"{TAG} Form question A" in detail and "5 / 5" in detail
    assert "Please send the link earlier" in detail


# =========================================================================== licences
def test_generate_up_to_the_limit_and_refused_past_it(admin, db):
    allowed_before = agent_licenses_allowed(db)
    consumed = agent_licenses_consumed(db)
    limit = consumed + 2
    _set_allowed(db, limit)
    try:
        for i in range(2):
            loc = _post(admin, f"{CFG}/agents/licenses/generate", {"notes": LICENSE_NOTE, "rationale": "pytest"})
            assert loc.startswith(f"{CFG}/agents?tab=licenses")
        db.expire_all()
        assert agent_licenses_consumed(db) == limit
        created = db.query(AgentLicense).filter(AgentLicense.notes == LICENSE_NOTE).all()
        assert len(created) == 2 and all(len(l.license_key) >= 20 and l.status == "active" for l in created)
        assert len({l.license_key for l in created}) == 2
        assert created[0].issued_by_id == db.query(User).filter(User.email == "admin@oqc.local").first().id

        loc = _post(admin, f"{CFG}/agents/licenses/generate", {"notes": LICENSE_NOTE})
        text = _flash(admin, loc)
        assert "consumed" in text and "Revoke one" in text
        db.expire_all()
        assert db.query(AgentLicense).filter(AgentLicense.notes == LICENSE_NOTE).count() == 2, "nothing past the limit"
        assert agent_licenses_consumed(db) == limit
        page = admin.get(f"{CFG}/agents?tab=licenses").text
        assert f">{limit}<" in page   # the Consumed tile
    finally:
        _set_allowed(db, allowed_before)


def test_key_is_masked_reveal_is_audited_and_download_body_equals_the_key(admin, db):
    lic = db.query(AgentLicense).filter(AgentLicense.notes == LICENSE_NOTE).order_by(AgentLicense.id).first()
    assert lic is not None
    page = admin.get(f"{CFG}/agents?tab=licenses").text
    assert lic.license_key not in page and lic.license_key[-4:] in page

    before = db.query(AuditEvent).filter(AuditEvent.module == "configuration", AuditEvent.action == "view",
                                         AuditEvent.entity_type == "AgentLicense", AuditEvent.entity_id == lic.id).count()
    loc = _post(admin, f"{CFG}/agents/licenses/{lic.id}/reveal")
    assert f"reveal={lic.id}" in loc
    assert lic.license_key in _flash(admin, loc)
    after = db.query(AuditEvent).filter(AuditEvent.module == "configuration", AuditEvent.action == "view",
                                        AuditEvent.entity_type == "AgentLicense", AuditEvent.entity_id == lic.id).count()
    assert after == before + 1, "revealing a licence key must be written to the audit log"

    r = admin.get(f"{CFG}/agents/licenses/{lic.id}/download")
    assert r.status_code == 200
    assert r.text == lic.license_key
    assert ".lic" in r.headers.get("content-disposition", "")
    assert db.query(AuditEvent).filter(AuditEvent.module == "configuration", AuditEvent.action == "export",
                                       AuditEvent.entity_type == "AgentLicense", AuditEvent.entity_id == lic.id).count() >= 1


# =========================================================================== the agent API
def test_heartbeat_registers_then_updates_a_device(anon, db):
    lic = db.query(AgentLicense).filter(AgentLicense.notes == LICENSE_NOTE, AgentLicense.status == "active").order_by(AgentLicense.id).first()
    machine = f"{MACHINE_PREFIX}01"
    r = anon.post(f"{API}/heartbeat", json={"license_key": lic.license_key, "machine_id": machine, "machine_name": "PYTEST-PC",
                                            "os_name": "Windows 11 Pro", "agent_version": "4.7.11"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["registered"] is True and body["status"] == "online"
    device = db.query(AgentDevice).filter(AgentDevice.machine_id == machine).first()
    assert device is not None and device.license_id == lic.id and device.machine_name == "PYTEST-PC"
    assert device.last_seen_at is not None and device.ip_address
    first_seen = device.last_seen_at

    r = anon.post(f"{API}/heartbeat", data={"license_key": lic.license_key, "machine_id": machine, "machine_name": "PYTEST-PC-RENAMED",
                                            "agent_version": "4.7.12"})
    assert r.status_code == 200, r.text
    assert r.json()["registered"] is False
    db.expire_all()
    device = db.query(AgentDevice).filter(AgentDevice.machine_id == machine).first()
    assert device.machine_name == "PYTEST-PC-RENAMED" and device.agent_version == "4.7.12"
    assert device.last_seen_at >= first_seen and device.status == "online"
    assert db.query(AgentDevice).filter(AgentDevice.machine_id == machine).count() == 1

    # missing fields are a 400, an unknown key a 403, and neither writes a device
    assert anon.post(f"{API}/heartbeat", json={"license_key": lic.license_key}).status_code == 400
    r = anon.post(f"{API}/heartbeat", json={"license_key": "not-a-licence", "machine_id": f"{MACHINE_PREFIX}ghost"})
    assert r.status_code == 403
    assert db.query(AgentDevice).filter(AgentDevice.machine_id == f"{MACHINE_PREFIX}ghost").first() is None


def test_screenshot_upload_stores_a_row_and_the_file(anon, admin, db):
    lic = db.query(AgentLicense).filter(AgentLicense.notes == LICENSE_NOTE, AgentLicense.status == "active").order_by(AgentLicense.id).first()
    machine = f"{MACHINE_PREFIX}01"
    device = db.query(AgentDevice).filter(AgentDevice.machine_id == machine).first()
    r = anon.post(f"{API}/screenshot", data={"license_key": lic.license_key, "machine_id": machine, "note": f"{TAG} capture"},
                  files={"image": ("shot.png", io.BytesIO(_png()), "image/png")})
    assert r.status_code == 201, r.text
    body = r.json()
    shot = db.get(AgentScreenshot, body["screenshot_id"])
    assert shot is not None and shot.device_id == device.id and shot.note == f"{TAG} capture"
    assert shot.image_path.startswith(f"{AGENT_SCREENSHOT_DIR}/{device.id}/")
    path = settings.storage_dir / shot.image_path
    assert path.exists() and path.read_bytes() == _png()

    # served the same way every other upload is
    # The static path is closed for staff-screen captures, even to someone who knows the address.
    assert anon.get(f"/storage/{shot.image_path}").status_code == 404
    # Anonymous callers are sent to sign in by the guarded route; a permitted user gets the bytes.
    assert anon.get(f"/config/agents/screenshots/{shot.id}/image", follow_redirects=False).status_code in (302, 303, 401, 403)
    served = admin.get(f"/config/agents/screenshots/{shot.id}/image")
    assert served.status_code == 200 and served.content == _png()

    # a non-image is refused and writes nothing
    r = anon.post(f"{API}/screenshot", data={"license_key": lic.license_key, "machine_id": machine},
                  files={"image": ("notes.txt", io.BytesIO(b"hello"), "text/plain")})
    assert r.status_code == 415
    # an unregistered machine must heartbeat first
    r = anon.post(f"{API}/screenshot", data={"license_key": lic.license_key, "machine_id": f"{MACHINE_PREFIX}new"},
                  files={"image": ("shot.png", io.BytesIO(_png()), "image/png")})
    assert r.status_code == 404
    assert db.query(AgentScreenshot).filter(AgentScreenshot.device_id == device.id).count() == 1


def test_revoke_blocks_the_device_and_the_agent_gets_403(admin, anon, db):
    lic = db.query(AgentLicense).filter(AgentLicense.notes == LICENSE_NOTE, AgentLicense.status == "active").order_by(AgentLicense.id).first()
    machine = f"{MACHINE_PREFIX}01"
    _post(admin, f"{CFG}/agents/licenses/{lic.id}/revoke", {"rationale": "pytest"})
    db.expire_all()
    lic = db.get(AgentLicense, lic.id)
    device = db.query(AgentDevice).filter(AgentDevice.machine_id == machine).first()
    assert lic.status == "revoked" and device.status == "blocked"
    seen = device.last_seen_at
    r = anon.post(f"{API}/heartbeat", json={"license_key": lic.license_key, "machine_id": machine, "machine_name": "PYTEST-PC"})
    assert r.status_code == 403
    r = anon.post(f"{API}/screenshot", data={"license_key": lic.license_key, "machine_id": machine},
                  files={"image": ("shot.png", io.BytesIO(_png()), "image/png")})
    assert r.status_code == 403
    db.expire_all()
    device = db.query(AgentDevice).filter(AgentDevice.machine_id == machine).first()
    assert device.status == "blocked" and device.last_seen_at == seen, "a refused call writes nothing"
    assert db.query(AgentScreenshot).filter(AgentScreenshot.device_id == device.id).count() == 1
    # revoking again is a no-op, and the seat is free again
    _post(admin, f"{CFG}/agents/licenses/{lic.id}/revoke")
    assert agent_licenses_consumed(db) == db.query(AgentLicense).filter(AgentLicense.status == "active").count()


def test_block_and_unblock_a_device(admin, anon, db):
    lic = db.query(AgentLicense).filter(AgentLicense.notes == LICENSE_NOTE, AgentLicense.status == "active").order_by(AgentLicense.id).first()
    assert lic is not None, "the second pytest licence is still active"
    machine = f"{MACHINE_PREFIX}02"
    assert anon.post(f"{API}/heartbeat", json={"license_key": lic.license_key, "machine_id": machine, "machine_name": "PYTEST-PC-2"}).status_code == 201
    device = db.query(AgentDevice).filter(AgentDevice.machine_id == machine).first()
    _post(admin, f"{CFG}/agents/devices/{device.id}/block")
    db.expire_all()
    assert db.get(AgentDevice, device.id).status == "blocked"
    assert anon.post(f"{API}/heartbeat", json={"license_key": lic.license_key, "machine_id": machine}).status_code == 403
    _post(admin, f"{CFG}/agents/devices/{device.id}/unblock")
    db.expire_all()
    assert db.get(AgentDevice, device.id).status == "offline"
    assert anon.post(f"{API}/heartbeat", json={"license_key": lic.license_key, "machine_id": machine}).status_code == 200
    db.expire_all()
    assert db.get(AgentDevice, device.id).status == "online"
    page = admin.get(f"{CFG}/agents?tab=devices&q={machine}").text
    assert machine in page and "PYTEST-PC-2" in page


def test_offline_job_marks_stale_devices(db):
    device = db.query(AgentDevice).filter(AgentDevice.machine_id == f"{MACHINE_PREFIX}02").first()
    fresh = db.query(AgentDevice).filter(AgentDevice.machine_id == f"{MACHINE_PREFIX}01").first()
    device.status, device.last_seen_at = "online", datetime.utcnow() - timedelta(minutes=11)
    fresh.status, fresh.last_seen_at = "online", datetime.utcnow() - timedelta(minutes=2)
    db.commit()
    # the job is right to age every stale device, the seeded ones included; remember them so the run leaves
    # the database as it found it
    others = {d.id: d.status for d in db.query(AgentDevice).filter(AgentDevice.status == "online",
                                                                  ~AgentDevice.machine_id.like(f"{MACHINE_PREFIX}%")).all()}
    result = agents_mark_offline(db)
    db.commit()
    assert "marked offline" in result
    db.expire_all()
    assert db.get(AgentDevice, device.id).status == "offline"
    assert db.get(AgentDevice, fresh.id).status == "online"
    fresh.status = "blocked"   # put the revoked-licence device back the way the revoke left it
    for did, status in others.items():
        db.get(AgentDevice, did).status = status
    db.commit()


# =========================================================================== permissions
def test_billing_rep_can_view_but_not_change(billing, db):
    assert billing.get(f"{ACAD}/feedback-questions").status_code == 200
    r = billing.post(f"{ACAD}/feedback-questions/new", data={"question": f"{TAG} billing"}, follow_redirects=False)
    assert r.status_code == 403
    assert billing.get(f"{CFG}/agents").status_code == 403, "billing has no settings.view"
    r = billing.post(f"{CFG}/agents/licenses/generate", data={"notes": LICENSE_NOTE}, follow_redirects=False)
    assert r.status_code == 403, f"billing must not generate a licence (got {r.status_code})"


def test_teacher_is_refused_both_pages(teacher):
    assert teacher.get(f"{ACAD}/feedback-questions").status_code == 403
    assert teacher.get(f"{CFG}/agents").status_code == 403
    assert teacher.post(f"{CFG}/agents/licenses/generate", data={}, follow_redirects=False).status_code == 403


def test_parent_is_refused_by_the_agent_api(parent, db):
    lic = db.query(AgentLicense).filter(AgentLicense.notes == LICENSE_NOTE).order_by(AgentLicense.id.desc()).first()
    assert parent.get(f"{CFG}/agents").status_code == 403
    r = parent.post(f"{API}/heartbeat", json={"license_key": "guess", "machine_id": f"{MACHINE_PREFIX}parent"})
    assert r.status_code == 403
    r = parent.post(f"{API}/screenshot", data={"license_key": "guess", "machine_id": f"{MACHINE_PREFIX}parent"},
                    files={"image": ("shot.png", io.BytesIO(_png()), "image/png")})
    assert r.status_code == 403
    assert db.query(AgentDevice).filter(AgentDevice.machine_id == f"{MACHINE_PREFIX}parent").first() is None
    assert lic is not None  # a session cookie never stands in for a licence key
