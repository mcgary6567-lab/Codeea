"""Zoom: meeting links for classes and pulling cloud recordings (the old ERP's "Get Meeting URL" and "SYNC Calls").

Credentials are a Zoom Server-to-Server OAuth app, typed on Configuration › Integrations › Zoom (Account ID and
Client ID as configuration, the Client secret as an encrypted credential) or set in .env (ZOOM_ACCOUNT_ID,
ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET).
The app needs the scopes meeting:write:admin and recording:read:admin.

- :func:`create_meeting` makes a scheduled meeting hosted by the teacher's Zoom user (their login e-mail), with
  cloud recording on, and stores its join link on the class.
- :func:`pull_recordings` lists each teacher's cloud recordings for the last days and hands every meeting to
  ``calls.ingest_zoom_recording`` (the same path as the ``recording.completed`` webhook), so a meeting is never
  stored twice and is mapped to the class it belongs to.

Without credentials nothing is called: :func:`configured` is false and the screens say so.
"""
from __future__ import annotations

import base64
import logging
import os
import time
from datetime import date, timedelta
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models.core import User
from app.models.people import Teacher
from app.models.scheduling import ClassSession
from app.services import integrations

log = logging.getLogger("oqc.zoom")

API = "https://api.zoom.us/v2"
TOKEN_URL = "https://zoom.us/oauth/token"
_token_cache: dict[str, tuple[str, float]] = {}


def credentials(db: Session) -> dict:
    integ = integrations.get_integration(db, "zoom")
    cfg = integ.config or {}
    out = {}
    for key, env in (("account_id", "ZOOM_ACCOUNT_ID"), ("client_id", "ZOOM_CLIENT_ID")):
        out[key] = str(cfg.get(key) or os.environ.get(env, "") or "").strip()
    out["client_secret"] = integrations.secret(db, "zoom", "ZOOM_CLIENT_SECRET")
    return out


def check(db: Session) -> dict:
    """Test connection: get a token and read the account's owner user. Raises with Zoom's message on failure."""
    if not configured(db):
        missing = [k for k, v in credentials(db).items() if not v]
        raise RuntimeError("Zoom is not set up yet; missing: " + ", ".join(missing))
    _token_cache.clear()
    me = _call(db, "GET", "/users/me")
    integrations._record(db, "zoom", True)
    return {"connected": True, "account_user": me.get("email"), "account_id": me.get("account_id")}


def configured(db: Session) -> bool:
    return all(credentials(db).values())


def _token(db: Session) -> str:
    creds = credentials(db)
    cached = _token_cache.get(creds["client_id"])
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    basic = base64.b64encode(f"{creds['client_id']}:{creds['client_secret']}".encode()).decode()
    resp = httpx.post(TOKEN_URL, params={"grant_type": "account_credentials", "account_id": creds["account_id"]},
                      headers={"Authorization": f"Basic {basic}"}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    _token_cache[creds["client_id"]] = (token, time.time() + int(data.get("expires_in") or 3600))
    return token


def _call(db: Session, method: str, path: str, **kw) -> dict:
    resp = httpx.request(method, API + path, headers={"Authorization": f"Bearer {_token(db)}"}, timeout=30, **kw)
    if resp.status_code >= 400:
        raise RuntimeError(f"Zoom {resp.status_code}: {resp.text[:200]}")
    return resp.json() if resp.content else {}


def host_email(teacher: Optional[Teacher]) -> str:
    if teacher is None:
        return ""
    if teacher.user and teacher.user.email:
        return teacher.user.email
    return (teacher.employee.email if teacher.employee and teacher.employee.email else "") or ""


def create_meeting(db: Session, session: ClassSession) -> str:
    """A Zoom meeting for this class, hosted by its teacher; the join link is stored on the class and returned."""
    if not configured(db):
        raise RuntimeError("Zoom is not connected. Add the Server-to-Server app on Configuration › Integrations › Zoom.")
    email = host_email(session.teacher)
    if not email:
        raise RuntimeError("The teacher has no e-mail address to host the Zoom meeting.")
    student = session.student.full_name if session.student else "class"
    body = {"topic": f"{student} - {session.course.name if session.course else 'Class'}"[:200], "type": 2,
            "start_time": session.scheduled_start.strftime("%Y-%m-%dT%H:%M:%S"), "timezone": "Asia/Karachi",
            "duration": int(session.duration_minutes or 30),
            "settings": {"auto_recording": "cloud", "join_before_host": False, "waiting_room": True}}
    try:
        data = _call(db, "POST", f"/users/{email}/meetings", json=body)
    except Exception as exc:
        integrations._record(db, "zoom", False, error=str(exc))
        raise RuntimeError(str(exc)) from exc
    integrations._record(db, "zoom", True)
    session.join_url = (data.get("join_url") or "")[:400] or session.join_url
    db.flush()
    return session.join_url or ""


def pull_recordings(db: Session, user: Optional[User], days: int = 3, request=None) -> dict:
    """Every active teacher's cloud recordings from the last ``days`` days, ingested like webhook events."""
    from app.services import calls as calls_svc

    if not configured(db):
        raise RuntimeError("Zoom is not connected.")
    start, end = date.today() - timedelta(days=max(1, days)), date.today()
    created = seen = 0
    errors: list[str] = []
    for t in db.query(Teacher).filter(Teacher.status == "active").all():
        email = host_email(t)
        if not email:
            continue
        token = ""
        while True:
            params = {"from": start.isoformat(), "to": end.isoformat(), "page_size": 300}
            if token:
                params["next_page_token"] = token
            try:
                data = _call(db, "GET", f"/users/{email}/recordings", params=params)
            except Exception as exc:  # one teacher without a Zoom user must not stop the rest
                errors.append(f"{email}: {str(exc)[:80]}")
                break
            for meeting in data.get("meetings") or []:
                meeting.setdefault("host_email", email)
                _, new = calls_svc.ingest_zoom_recording(db, meeting, user, request=request)
                seen += 1
                created += 1 if new else 0
            token = data.get("next_page_token") or ""
            if not token:
                break
    integrations._record(db, "zoom", not errors or bool(seen), error="; ".join(errors)[:500] or None)
    return {"created": created, "seen": seen, "errors": errors}
