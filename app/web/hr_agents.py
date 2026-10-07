"""Human Resource › HR Configurations › Confido Agents (moved from Configuration; docs/MODULE_STRUCTURE.md).

The desktop recording agent's side of staff monitoring, in three tabs:

    /hr/confido-agents?tab=licenses      licences the agent installs with: generate (up to the branch property
                                         Agent Licenses Allowed), reveal and download (both audited), revoke
    /hr/confido-agents?tab=devices       machines that have checked in, with block / unblock
    /hr/confido-agents?tab=screenshots   the captures, served only by /hr/confido-agents/screenshots/{id}/image

The agent itself talks to /api/agents/heartbeat and /api/agents/screenshot (app.web.api_agents), which read the
constants below. Viewing needs staff_monitoring.view; every change needs staff_monitoring.configure. The old
/config/agents addresses redirect here (app.web.company_config).
"""
from __future__ import annotations

import secrets
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import paginate, parse_date, parse_int, redirect
from app.database import get_db
from app.models.config_erp import AGENT_DEVICE_STATUSES, AgentDevice, AgentLicense, AgentScreenshot
from app.models.core import Role, User
from app.models.people import Employee
from app.web.company_config import _get, _rationale

router = APIRouter(prefix="/hr", dependencies=[Depends(csrf_protect)])

BASE = "/hr/confido-agents"
MODULE = "staff_monitoring"

# Their Recording Agent page, three tabs. The licence count the branch may consume is the branch property
# agent_licenses_allowed; a licence is consumed while it is active.
AGENT_TABS = [("licenses", "User Licenses"), ("devices", "Devices list"), ("screenshots", "Agents Screen Shorts")]
AGENT_LICENSES_SETTING = "agent_licenses_allowed"
AGENT_LICENSES_DEFAULT = 5
AGENT_SCREENSHOT_DIR = "agent_screenshots"   # under storage/; served only by agent_screenshot_image below
AGENT_OFFLINE_AFTER_MINUTES = 10


# =============================================================================== helpers
def agent_licenses_allowed(db: Session) -> int:
    from app.services.system import get_setting_value
    return parse_int(get_setting_value(db, AGENT_LICENSES_SETTING, AGENT_LICENSES_DEFAULT), AGENT_LICENSES_DEFAULT) or 0


def agent_licenses_consumed(db: Session) -> int:
    return db.query(func.count(AgentLicense.id)).filter(AgentLicense.status == "active").scalar() or 0


def _employee_options(db: Session) -> list[tuple[int, str]]:
    rows = db.query(Employee).filter(Employee.status.in_(("active", "probation", "on_leave"))).order_by(Employee.full_name).all()
    return [(e.id, f"{e.employee_code} — {e.full_name}") for e in rows]


def _agents_back(tab: str = "licenses", **params) -> str:
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v not in (None, "", 0))
    return f"{BASE}?tab={tab}" + (f"&{qs}" if qs else "")


def _license(db: Session, lid: int) -> AgentLicense:
    return _get(db, AgentLicense, lid, "Agent licence")


# =============================================================================== the page
@router.get("/confido-agents", include_in_schema=False)
def agents_page(request: Request, tab: str = "licenses", status: str = "", employee_id: Optional[int] = None, q: str = "",
                date_from: str = "", date_to: str = "", reveal: int = 0, page: int = 1,
                db: Session = Depends(get_db), user: User = Depends(require("staff_monitoring.view"))):
    tab = tab if tab in [k for k, _ in AGENT_TABS] else "licenses"
    allowed, consumed = agent_licenses_allowed(db), agent_licenses_consumed(db)
    device_counts = dict(db.query(AgentDevice.status, func.count(AgentDevice.id)).group_by(AgentDevice.status).all())
    ctx: dict = {
        "user": user, "tab": tab, "tabs": AGENT_TABS, "status": status, "employee_id": employee_id, "q": q,
        "date_from": date_from, "date_to": date_to, "reveal": reveal,
        "stats": {"allowed": allowed, "consumed": consumed, "available": max(0, allowed - consumed),
                  "revoked": db.query(func.count(AgentLicense.id)).filter(AgentLicense.status == "revoked").scalar() or 0,
                  "online": device_counts.get("online", 0), "offline": device_counts.get("offline", 0),
                  "blocked": device_counts.get("blocked", 0),
                  "screenshots": db.query(func.count(AgentScreenshot.id)).scalar() or 0},
        "employee_options": _employee_options(db), "device_statuses": [(s, s.title()) for s in AGENT_DEVICE_STATUSES],
        "offline_after": AGENT_OFFLINE_AFTER_MINUTES, "today": date.today().isoformat(),
        "can_configure": rbac.has_permission(user, "staff_monitoring.configure"),
        "can_setup": rbac.has_permission(user, "settings.view")}
    if tab == "licenses":
        rows = db.query(AgentLicense).order_by(AgentLicense.status, AgentLicense.issued_at.desc(), AgentLicense.id.desc()).all()
        ctx["rows"] = rows
        ctx["user_options"] = [(u.id, f"{u.full_name} ({u.email})") for u in
                               db.query(User).join(Role, Role.id == User.role_id)
                               .filter(User.is_active.is_(True), Role.portal.in_(("admin", "teacher"))).order_by(User.full_name).all()]
    elif tab == "devices":
        query = db.query(AgentDevice)
        if status in AGENT_DEVICE_STATUSES:
            query = query.filter(AgentDevice.status == status)
        if employee_id:
            query = query.filter(AgentDevice.employee_id == employee_id)
        if q:
            like = f"%{q.lower()}%"
            query = query.filter(func.lower(AgentDevice.machine_name).like(like) | func.lower(AgentDevice.machine_id).like(like)
                                 | func.lower(func.coalesce(AgentDevice.ip_address, "")).like(like))
        ctx["rows"] = query.order_by(AgentDevice.status, AgentDevice.last_seen_at.desc().nullslast(), AgentDevice.id).all()
        ctx["shot_counts"] = dict(db.query(AgentScreenshot.device_id, func.count(AgentScreenshot.id)).group_by(AgentScreenshot.device_id).all())
    else:
        query = db.query(AgentScreenshot)
        if employee_id:
            query = query.filter(AgentScreenshot.employee_id == employee_id)
        df, dt = parse_date(date_from), parse_date(date_to)
        if df:
            query = query.filter(AgentScreenshot.captured_at >= datetime.combine(df, datetime.min.time()))
        if dt:
            query = query.filter(AgentScreenshot.captured_at <= datetime.combine(dt, datetime.max.time()))
        ctx["page"] = paginate(query.order_by(AgentScreenshot.captured_at.desc(), AgentScreenshot.id.desc()), page, 24)
        ctx["shown"] = ctx["page"].total
    return render(request, "hr/confido_agents.html", ctx)


# =============================================================================== licences
@router.post("/confido-agents/licenses/generate", include_in_schema=False)
async def agent_license_generate(request: Request, db: Session = Depends(get_db),
                                 user: User = Depends(require("staff_monitoring.configure"))):
    form = await request.form()
    allowed, consumed = agent_licenses_allowed(db), agent_licenses_consumed(db)
    if consumed + 1 > allowed:
        return redirect(_agents_back("licenses"),
                        f"All {allowed} licences are consumed ({consumed} active). Revoke one or raise "
                        f"'Agent Licenses Allowed' under Setup before generating another.", "error")
    expires = parse_date(form.get("expires_at"))
    issued_to = db.get(User, parse_int(form.get("issued_to_user_id")) or 0) if form.get("issued_to_user_id") else None
    lic = AgentLicense(license_key=secrets.token_urlsafe(24), issued_to_user_id=issued_to.id if issued_to else None,
                       issued_by_id=user.id, issued_at=datetime.utcnow(),
                       expires_at=datetime.combine(expires, datetime.max.time().replace(microsecond=0)) if expires else None,
                       status="active", notes=(form.get("notes") or "").strip() or None)
    db.add(lic)
    db.flush()
    log_action(db, user, "create", MODULE, entity=lic, severity="warning", consequential=True,
               rationale=_rationale(form) or "Recording agent licence generated",
               description=f"Agent licence #{lic.id} generated for {issued_to.full_name if issued_to else 'unassigned'} "
                           f"({consumed + 1} of {allowed} consumed)", request=request)
    db.commit()
    return redirect(_agents_back("licenses"), f"Licence #{lic.id} generated ({consumed + 1} of {allowed} consumed). "
                                              "Reveal or download it to install the agent.")


@router.post("/confido-agents/licenses/{lid}/reveal", include_in_schema=False)
async def agent_license_reveal(lid: int, request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require("staff_monitoring.view"))):
    """Show a licence key in the clear. Like a secret branch property, the reveal itself is audited."""
    lic = _license(db, lid)
    form = await request.form()
    log_action(db, user, "view", MODULE, entity=lic, severity="warning", consequential=True,
               rationale=_rationale(form) or "Agent licence key revealed on screen",
               description=f"Revealed agent licence #{lic.id}", request=request)
    db.commit()
    return redirect(_agents_back("licenses", reveal=lic.id), f"Licence #{lic.id} revealed — this is audited.")


@router.get("/confido-agents/licenses/{lid}/download", include_in_schema=False)
def agent_license_download(lid: int, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require("staff_monitoring.view"))):
    """The key as a small .lic text file the agent installer reads. Downloading exposes the key, so it is audited."""
    lic = _license(db, lid)
    log_action(db, user, "export", MODULE, entity=lic, severity="warning", consequential=True,
               rationale="Agent licence file downloaded", description=f"Downloaded agent licence #{lic.id} as a .lic file",
               request=request)
    db.commit()
    return Response(content=lic.license_key, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="oqc-agent-{lic.id}.lic"',
                             "Cache-Control": "no-store"})


@router.post("/confido-agents/licenses/{lid}/revoke", include_in_schema=False)
async def agent_license_revoke(lid: int, request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require("staff_monitoring.configure"))):
    lic = _license(db, lid)
    form = await request.form()
    if lic.status == "revoked":
        return redirect(_agents_back("licenses"), f"Licence #{lic.id} is already revoked.", "warning")
    before = snapshot(lic)
    lic.status = "revoked"
    blocked = 0
    for device in lic.devices:
        if device.status != "blocked":
            device.status = "blocked"
            blocked += 1
    log_action(db, user, "status_change", MODULE, entity=lic, severity="warning", consequential=True,
               rationale=_rationale(form) or "Recording agent licence revoked",
               description=f"Agent licence #{lic.id} revoked; {blocked} device(s) blocked",
               before=before, after=snapshot(lic), request=request)
    db.commit()
    return redirect(_agents_back("licenses"), f"Licence #{lic.id} revoked and {blocked} device(s) blocked.")


# =============================================================================== devices
@router.post("/confido-agents/devices/{did}/block", include_in_schema=False)
async def agent_device_block(did: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require("staff_monitoring.configure"))):
    device = _get(db, AgentDevice, did, "Agent device")
    form = await request.form()
    before = snapshot(device)
    device.status = "blocked"
    log_action(db, user, "status_change", MODULE, entity=device, severity="warning", consequential=True,
               rationale=_rationale(form) or "Recording agent device blocked",
               description=f"Agent device {device.machine_name} ({device.machine_id}) blocked",
               before=before, after=snapshot(device), request=request)
    db.commit()
    return redirect(_agents_back("devices"), f"{device.machine_name} blocked; its heartbeats and screenshots are refused.")


@router.post("/confido-agents/devices/{did}/unblock", include_in_schema=False)
async def agent_device_unblock(did: int, request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require("staff_monitoring.configure"))):
    device = _get(db, AgentDevice, did, "Agent device")
    form = await request.form()
    if device.license and device.license.status != "active":
        return redirect(_agents_back("devices"), f"{device.machine_name} is on a revoked licence; issue a new licence instead.", "error")
    before = snapshot(device)
    device.status = "offline"   # online again on its next heartbeat
    log_action(db, user, "status_change", MODULE, entity=device, severity="warning", consequential=True,
               rationale=_rationale(form) or "Recording agent device unblocked",
               description=f"Agent device {device.machine_name} ({device.machine_id}) unblocked",
               before=before, after=snapshot(device), request=request)
    db.commit()
    return redirect(_agents_back("devices"), f"{device.machine_name} unblocked; it shows online at its next heartbeat.")


# =============================================================================== screenshots
@router.get("/confido-agents/screenshots/{sid}/image", include_in_schema=False)
def agent_screenshot_image(sid: int, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require("staff_monitoring.view"))):
    """The capture itself. Staff-screen captures are not served from the open static mount: this route
    checks the permission and refuses any path that resolves outside the screenshots folder."""
    shot = db.get(AgentScreenshot, sid)
    if shot is None:
        raise HTTPException(status_code=404, detail="Screenshot not found.")
    root = (BASE_DIR / "storage" / AGENT_SCREENSHOT_DIR).resolve()
    path = (BASE_DIR / "storage" / shot.image_path).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot file is missing.")
    return FileResponse(str(path))
