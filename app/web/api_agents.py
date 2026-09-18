"""Confido Agents — the two calls the desktop recording agent makes (docs/AUDIT_PARITY_WALK_2026-09-17.md).

    POST /api/agents/heartbeat    licence key + machine id/name/os/version -> the device is registered or updated,
                                  marked online, and stamped with the time and the request IP
    POST /api/agents/screenshot   licence key + machine id + an image -> stored under storage/agent_screenshots and
                                  recorded against the device

Both are authenticated by the licence key alone (the agent has no user session), so there is no CSRF dependency
here: an unknown, revoked, expired or blocked key is refused with 403 and nothing is written. The agent program
itself is the vendor's; this is the side of the conversation the platform holds. Staff manage licences, devices
and captures on /config/agents (app.web.company_config).
"""
from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import client_ip
from app.database import get_db
from app.models.config_erp import AgentDevice, AgentLicense, AgentScreenshot
from app.models.people import Employee
from app.web.company_config import AGENT_SCREENSHOT_DIR

router = APIRouter(prefix="/api/agents", tags=["agents"])

MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}


async def _payload(request: Request) -> dict:
    """The agent may post JSON or a form; multipart uploads are always a form."""
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, "Malformed JSON body")
        return data if isinstance(data, dict) else {}
    form = await request.form()
    return dict(form)


def _text(data: dict, key: str, limit: int = 120) -> Optional[str]:
    value = data.get(key)
    if value is None or not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] or None


def authenticate(db: Session, license_key: Optional[str], machine_id: Optional[str]) -> tuple[AgentLicense, Optional[AgentDevice]]:
    """The licence behind a key, and the device already registered for the machine (if any).

    Refuses with 403 when the key is unknown, revoked or expired, or when the machine has been blocked.
    """
    if not license_key or not machine_id:
        raise HTTPException(400, "license_key and machine_id are required")
    lic = db.query(AgentLicense).filter(AgentLicense.license_key == license_key).first()
    if not lic or lic.status != "active":
        raise HTTPException(403, "Licence not accepted")
    if lic.expires_at and lic.expires_at < datetime.utcnow():
        raise HTTPException(403, "Licence expired")
    device = db.query(AgentDevice).filter(AgentDevice.machine_id == machine_id).first()
    if device and device.status == "blocked":
        raise HTTPException(403, "This device is blocked")
    return lic, device


def _employee_for(db: Session, lic: AgentLicense, data: dict) -> Optional[int]:
    """The employee a device belongs to: the one the agent names, else the staff member the licence was issued to."""
    raw = data.get("employee_id")
    try:
        eid = int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        eid = None
    if eid and db.get(Employee, eid):
        return eid
    if lic.issued_to_user_id:
        emp = db.query(Employee).filter(Employee.user_id == lic.issued_to_user_id).first()
        if emp:
            return emp.id
    return None


def _device_out(device: AgentDevice) -> dict:
    return {"device_id": device.id, "machine_id": device.machine_id, "machine_name": device.machine_name,
            "status": device.status, "employee_id": device.employee_id, "license_id": device.license_id,
            "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None}


@router.post("/heartbeat")
async def heartbeat(request: Request, db: Session = Depends(get_db)):
    data = await _payload(request)
    lic, device = authenticate(db, _text(data, "license_key", 64), _text(data, "machine_id"))
    machine_name = _text(data, "machine_name") or _text(data, "machine_id")
    created = device is None
    if created:
        device = AgentDevice(machine_id=_text(data, "machine_id"), machine_name=machine_name, status="offline")
        db.add(device)
    device.license_id = lic.id
    device.machine_name = machine_name or device.machine_name
    device.os_name = _text(data, "os_name", 80) or device.os_name
    device.agent_version = _text(data, "agent_version", 30) or device.agent_version
    device.ip_address = (client_ip(request) or None) or device.ip_address
    device.last_seen_at = datetime.utcnow()
    device.status = "online"
    if not device.employee_id:
        device.employee_id = _employee_for(db, lic, data)
    db.commit()
    return JSONResponse({"ok": True, "registered": created, **_device_out(device)}, status_code=201 if created else 200)


@router.post("/screenshot")
async def screenshot(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    data = dict(form)
    lic, device = authenticate(db, _text(data, "license_key", 64), _text(data, "machine_id"))
    if device is None:
        raise HTTPException(404, "Device not registered; send a heartbeat first")
    upload = form.get("image") or form.get("file")
    if upload is None or isinstance(upload, str) or not getattr(upload, "filename", None):
        raise HTTPException(400, "An image file is required (field 'image')")
    ext = IMAGE_TYPES.get((upload.content_type or "").split(";")[0].strip().lower())
    if not ext:
        ext = Path(upload.filename).suffix.lower()
        if ext == ".jpeg":
            ext = ".jpg"
        if ext not in IMAGE_TYPES.values():
            raise HTTPException(415, "Only PNG, JPEG, WebP or GIF screenshots are accepted")
    content = await upload.read()
    if not content:
        raise HTTPException(400, "The image is empty")
    if len(content) > MAX_SCREENSHOT_BYTES:
        raise HTTPException(413, "The image is larger than 8 MB")
    captured_at = datetime.utcnow()
    raw_when = _text(data, "captured_at", 40)
    if raw_when:
        try:
            captured_at = datetime.fromisoformat(raw_when.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    rel_dir = Path(AGENT_SCREENSHOT_DIR) / str(device.id) / captured_at.strftime("%Y-%m")
    target_dir = settings.storage_dir / rel_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{captured_at.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}{ext}"
    (target_dir / filename).write_bytes(content)
    shot = AgentScreenshot(device_id=device.id, employee_id=device.employee_id, captured_at=captured_at,
                           image_path=(rel_dir / filename).as_posix(), note=_text(data, "note", 200))
    db.add(shot)
    device.last_seen_at = datetime.utcnow()
    device.status = "online"
    device.ip_address = (client_ip(request) or None) or device.ip_address
    db.commit()
    return JSONResponse({"ok": True, "screenshot_id": shot.id, "device_id": device.id, "image_path": shot.image_path,
                         "captured_at": shot.captured_at.isoformat(), "bytes": len(content)}, status_code=201)
