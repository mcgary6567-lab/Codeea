"""Client contacts behind a one-time password, as the ERP's "OTP Page" keeps them (Configuration › OTP).

Each test switches OTP on for the run and puts the shared configuration back afterwards, so the module runs against
the development database any number of times. The code generator is pinned so the tests can type the code.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.config_erp import ContactRevealChallenge, OtpConfiguration
from app.models.core import AuditEvent, User
from app.models.people import Client
from app.services import contact_reveal as svc

CODE = "424242"


@pytest.fixture(scope="module")
def admin():
    c = TestClient(app)
    assert c.post("/login", data={"username": "admin@oqc.local", "password": "Admin@12345"}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def otp_on(db, monkeypatch):
    """OTP enabled for every role, a code each time, resend after 60 s; everything restored afterwards."""
    monkeypatch.setattr(svc, "new_code", lambda length: CODE)
    cfg = svc.config(db)
    created = cfg is None
    if created:
        cfg = OtpConfiguration(channel="email", code_length=6, validity_minutes=10, max_attempts=5, resend_after_seconds=60, required_for_roles=[])
        db.add(cfg)
        db.flush()
    saved = {k: getattr(cfg, k) for k in ("is_enabled", "required_for_roles", "reveal_window_minutes", "max_attempts", "resend_after_seconds", "channel")}
    me = db.query(User).filter(User.email == "admin@oqc.local").one()
    first_challenge = db.query(ContactRevealChallenge.id).order_by(ContactRevealChallenge.id.desc()).limit(1).scalar() or 0
    cfg.is_enabled, cfg.required_for_roles, cfg.reveal_window_minutes = True, [], 0
    cfg.max_attempts, cfg.resend_after_seconds, cfg.channel = 3, 60, "email"
    db.commit()
    yield cfg
    db.query(ContactRevealChallenge).filter(ContactRevealChallenge.user_id == me.id, ContactRevealChallenge.id > first_challenge).delete()
    cfg = svc.config(db)
    if created:
        db.delete(cfg)
    else:
        for k, v in saved.items():
            setattr(cfg, k, v)
    db.commit()


def _client(db) -> Client:
    return (db.query(Client).filter(Client.email.isnot(None), Client.phone.isnot(None), Client.email != "")
            .order_by(Client.id.desc()).first())


def _clear_challenges(db):
    me = db.query(User).filter(User.email == "admin@oqc.local").one()
    db.query(ContactRevealChallenge).filter(ContactRevealChallenge.user_id == me.id).delete()
    db.commit()


def test_with_otp_off_contacts_show_and_view_needs_no_code(admin, db):
    cfg = svc.config(db)
    if cfg and cfg.is_enabled:
        pytest.skip("OTP is switched on in this database")
    c = _client(db)
    assert c.email in admin.get(f"/clients/{c.id}").text
    r = admin.post(f"/clients/{c.id}/reveal/phone/start")
    assert r.json() == {"value": c.phone}


def test_with_otp_on_the_pages_show_masked_contacts_and_a_view_button(admin, db, otp_on):
    c = _client(db)
    for url in (f"/clients/{c.id}", f"/clients?q={c.client_code}", f"/clients/{c.id}/edit"):
        html = admin.get(url).text
        assert c.email not in html, f"{url} printed the email"
        assert c.phone not in html, f"{url} printed the cell"
        assert f'data-reveal="/clients/{c.id}/reveal/email"' in html, url
    assert admin.get(f"/clients/{c.id}/reveal/email").status_code == 403  # the old direct link needs the code too


def test_view_sends_a_code_and_opens_the_value_only_with_it(admin, db, otp_on):
    _clear_challenges(db)
    c = _client(db)
    start = admin.post(f"/clients/{c.id}/reveal/email/start").json()
    assert "value" not in start and start["challenge"] and start["label"] == "Primary Email"
    assert start["channel"] == "email" and "***" in start["sent_to"]
    wrong = admin.post(f"/clients/{c.id}/reveal/email/verify", data={"challenge": start["challenge"], "code": "000000"})
    assert wrong.status_code == 400 and "2 attempt(s) left" in wrong.json()["error"]
    other_field = admin.post(f"/clients/{c.id}/reveal/phone/verify", data={"challenge": start["challenge"], "code": CODE})
    assert other_field.status_code == 400 and "another contact" in other_field.json()["error"], "a code opens only its own contact"
    ok = admin.post(f"/clients/{c.id}/reveal/email/verify", data={"challenge": start["challenge"], "code": CODE})
    assert ok.json() == {"value": c.email}
    again = admin.post(f"/clients/{c.id}/reveal/email/verify", data={"challenge": start["challenge"], "code": CODE})
    assert again.status_code == 400, "a code is used once"
    events = [e.action for e in db.query(AuditEvent).filter(AuditEvent.entity_type == "Client", AuditEvent.entity_id == c.id)
              .order_by(AuditEvent.id.desc()).limit(6)]
    assert {"otp_sent", "otp_failed", "reveal"} <= set(events)


def test_a_new_code_waits_for_the_resend_time_and_wrong_codes_run_out(admin, db, otp_on):
    _clear_challenges(db)
    c = _client(db)
    first = admin.post(f"/clients/{c.id}/reveal/phone/start").json()
    too_soon = admin.post(f"/clients/{c.id}/reveal/phone/start")
    assert too_soon.status_code == 429 and "wait" in too_soon.json()["error"]
    assert too_soon.json()["challenge"] == first["challenge"], "the dialog reopens on the code already sent"
    elsewhere = admin.post(f"/clients/{c.id}/reveal/email/start")
    assert elsewhere.status_code == 429 and "challenge" not in elsewhere.json()
    for _ in range(3):
        admin.post(f"/clients/{c.id}/reveal/phone/verify", data={"challenge": first["challenge"], "code": "999999"})
    locked = admin.post(f"/clients/{c.id}/reveal/phone/verify", data={"challenge": first["challenge"], "code": CODE})
    assert locked.status_code == 400 and "Too many wrong codes" in locked.json()["error"]


def test_an_expired_code_is_refused(db, otp_on):
    _clear_challenges(db)
    me = db.query(User).filter(User.email == "admin@oqc.local").one()
    c = _client(db)
    ch = svc.start(db, me, c.id, "email", now=datetime.utcnow() - timedelta(minutes=30))
    db.commit()
    with pytest.raises(svc.RevealError, match="expired"):
        svc.verify(db, me, ch.id, CODE)


def test_the_reveal_window_keeps_contacts_open_after_one_code(admin, db, otp_on):
    _clear_challenges(db)
    c = _client(db)
    otp_on.reveal_window_minutes = 15
    db.commit()
    start = admin.post(f"/clients/{c.id}/reveal/email/start").json()
    assert admin.post(f"/clients/{c.id}/reveal/email/verify", data={"challenge": start["challenge"], "code": CODE}).json() == {"value": c.email}
    assert admin.post(f"/clients/{c.id}/reveal/phone/start").json() == {"value": c.phone}
    assert c.email in admin.get(f"/clients/{c.id}").text


def test_roles_not_ticked_need_no_code(admin, db, otp_on):
    otp_on.required_for_roles = ["teacher"]
    db.commit()
    c = _client(db)
    assert admin.post(f"/clients/{c.id}/reveal/phone/start").json() == {"value": c.phone}


def test_saving_the_edit_form_with_masked_contacts_keeps_them(admin, db, otp_on):
    from app.core.security import mask
    c = _client(db)
    stored = (c.email, c.phone, c.whatsapp)
    html = admin.get(f"/clients/{c.id}/edit").text
    assert f'value="{mask(c.email)}"' in html
    form = {"full_name": c.full_name, "email": mask(c.email), "phone": mask(c.phone), "whatsapp": mask(c.whatsapp) if c.whatsapp else "",
            "country": c.country, "timezone": c.timezone, "currency": c.currency, "status": c.status, "shift": c.shift,
            "fee_recurrence": c.fee_recurrence}
    admin.post(f"/clients/{c.id}/edit", data=form, follow_redirects=False)
    db.expire_all()
    c = db.get(Client, c.id)
    assert (c.email, c.phone) == stored[:2]
    assert c.whatsapp == stored[2] or (not stored[2] and not c.whatsapp)


def test_the_otp_setup_saves_the_reveal_window(admin, db, otp_on):
    page = admin.get("/config/otp?tab=setup").text
    assert 'name="reveal_window_minutes"' in page
    admin.post("/config/otp/setup", data={"is_enabled": "1", "channel": "email", "code_length": "6", "validity_minutes": "10",
                                          "max_attempts": "3", "resend_after_seconds": "60", "reveal_window_minutes": "20"},
               follow_redirects=False)
    db.expire_all()
    assert svc.config(db).reveal_window_minutes == 20
