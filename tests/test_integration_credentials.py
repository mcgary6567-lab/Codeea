"""Credentials typed on Configuration › Integrations (22 Oct 2026): Zoom and SMS can be set up from the screen.
Values are sealed in the database, never shown again, used ahead of .env, and checked by Test connection.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.secret_box import seal, unseal
from app.database import SessionLocal
from app.main import app
from app.models.core import Integration
from app.services import integrations, zoom


def _login(email: str, password: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/login", data={"username": email, "password": password}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture(scope="module")
def admin():
    return _login("admin@oqc.local", "Admin@12345")


@pytest.fixture(autouse=True)
def _restore():
    s = SessionLocal()
    saved = {i.provider: (dict(i.secrets or {}), dict(i.config or {}), i.status, i.health, i.last_error)
             for i in s.query(Integration).filter(Integration.provider.in_(["zoom", "sms"]))}
    s.close()
    yield
    s = SessionLocal()
    try:
        for i in s.query(Integration).filter(Integration.provider.in_(["zoom", "sms"])):
            if i.provider in saved:
                i.secrets, i.config, i.status, i.health, i.last_error = saved[i.provider]
        s.commit()
    finally:
        s.close()


def test_seal_round_trip_and_tamper():
    token = seal("s3cret value")
    assert token.startswith("fernet:") and "s3cret" not in token
    assert unseal(token) == "s3cret value"
    assert unseal(token[:-4] + "AAAA") is None and unseal("plain") is None


def test_zoom_credentials_are_typed_on_the_screen_and_kept_sealed(admin, monkeypatch):
    for name in ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    page = admin.get("/admin/integrations/zoom").text
    assert "How to set it up" in page and "Client secret" in page and "Not set" in page
    admin.post("/admin/integrations/zoom/config", data={"account_id": "acc-123", "client_id": "cid-456"})
    r = admin.post("/admin/integrations/zoom/secrets", data={"ZOOM_CLIENT_SECRET": "zz-very-secret"})
    assert "Credentials saved: Client secret" in r.text
    assert "zz-very-secret" not in r.text and "Saved" in r.text
    s = SessionLocal()
    try:
        integ = s.query(Integration).filter(Integration.provider == "zoom").one()
        assert "zz-very-secret" not in str(integ.secrets) and str(integ.secrets["ZOOM_CLIENT_SECRET"]).startswith("fernet:")
        assert zoom.credentials(s) == {"account_id": "acc-123", "client_id": "cid-456", "client_secret": "zz-very-secret"}
        assert zoom.configured(s)
    finally:
        s.close()
    keep = admin.post("/admin/integrations/zoom/secrets", data={"ZOOM_CLIENT_SECRET": ""})
    assert "unchanged" in keep.text
    gone = admin.post("/admin/integrations/zoom/secrets", data={"clear_ZOOM_CLIENT_SECRET": "1"})
    assert "removed: Client secret" in gone.text
    s = SessionLocal()
    try:
        assert not zoom.configured(s)
    finally:
        s.close()


def test_zoom_test_connection_reports_what_is_missing_and_checks_the_account(admin, monkeypatch):
    for name in ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    s = SessionLocal()
    try:
        integ = s.query(Integration).filter(Integration.provider == "zoom").one()
        integ.config, integ.secrets = {}, {}
        s.commit()
    finally:
        s.close()
    r = admin.post("/admin/integrations/zoom/test", data={"target": ""})
    assert "Test failed" in r.text and "missing" in r.text
    monkeypatch.setattr(zoom, "configured", lambda db: True)
    monkeypatch.setattr(zoom, "_call", lambda db, method, path, **kw: {"email": "owner@college.test", "account_id": "acc"})
    ok = admin.post("/admin/integrations/zoom/test", data={"target": ""})
    assert "Test succeeded" in ok.text and "owner@college.test" in ok.text


def test_sms_gateway_url_from_the_screen_sends_a_real_message(admin, monkeypatch):
    monkeypatch.delenv("SMS_GATEWAY_URL", raising=False)
    admin.post("/admin/integrations/sms/config", data={"provider": "http", "sender": "OQC", "method": "GET"})
    admin.post("/admin/integrations/sms/secrets", data={"SMS_GATEWAY_URL": "https://gw.example/send?key=K1&to={to}&msg={text}&mask={sender}"})
    sent = {}

    class R:
        status_code, text = 200, "OK"

        def raise_for_status(self):
            return None

    def fake_get(url, timeout=15):
        sent["url"] = url
        return R()
    monkeypatch.setattr(integrations.httpx, "get", fake_get)
    r = admin.post("/admin/integrations/sms/test", data={"target": "+923001234567"})
    assert "Test succeeded" in r.text
    assert sent["url"].startswith("https://gw.example/send?key=K1&to=923001234567&msg=") and "mask=OQC" in sent["url"]
    assert "K1" not in admin.get("/admin/integrations/sms").text, "the saved URL carries the key and is never shown"
    no_number = admin.post("/admin/integrations/sms/test", data={"target": ""})
    assert "Test failed" in no_number.text and "mobile number" in no_number.text


def test_the_zoom_webhook_secret_typed_on_the_screen_signs_inbound_calls(admin, monkeypatch):
    monkeypatch.delenv("ZOOM_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("ZOOM_CLIENT_SECRET", raising=False)
    admin.post("/admin/integrations/zoom/secrets", data={"ZOOM_WEBHOOK_SECRET": "wh-123"})
    s = SessionLocal()
    try:
        assert integrations.inbound_secret(s, "zoom") == "wh-123"
    finally:
        s.close()


def test_only_integration_admins_can_change_credentials():
    hr = _login("hr@oqc.local", "People@123")
    r = hr.post("/admin/integrations/zoom/secrets", data={"ZOOM_CLIENT_SECRET": "x"}, follow_redirects=False)
    assert r.status_code in (302, 303, 403)
    s = SessionLocal()
    try:
        integ = s.query(Integration).filter(Integration.provider == "zoom").one()
        assert unseal((integ.secrets or {}).get("ZOOM_CLIENT_SECRET")) != "x"
    finally:
        s.close()
