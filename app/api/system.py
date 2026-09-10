"""REST API: system administration (status, users, roles, settings, integrations, backups, audit).

Authenticate with a bearer token (POST /api/v1/auth/login) or an ``X-API-Key`` header.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings as cfg
from app.core import rbac
from app.core.audit import log_action
from app.core.deps import get_current_user, require
from app.database import get_db
from app.models.core import (ApiKey, AuditEvent, BackupRecord, Integration, Role, Setting, User, UserSession,
                             Webhook, WebhookDelivery)
from app.models.ops import MigrationJob
from app.services import system as sys_svc

router = APIRouter(prefix="/system", tags=["system"])


# ----------------------------------------------------------------------------- schemas
class JobStatusOut(BaseModel):
    id: str
    interval_minutes: int
    status: str
    last_run: Optional[datetime] = None
    result: Optional[str] = None


class IntegrationOut(BaseModel):
    provider: str
    name: str
    status: str
    health: str
    last_health_check_at: Optional[datetime] = None
    calls_today: int = 0
    failures_today: int = 0
    last_error: Optional[str] = None


class SystemStatusOut(BaseModel):
    app: str
    version: str
    environment: str
    database: str
    server_time: datetime
    jobs: list[JobStatusOut]
    integrations: list[IntegrationOut]
    counts: dict[str, int]
    backups: dict[str, Any]


class UserOut(BaseModel):
    id: int
    email: str
    username: str
    full_name: str
    role: Optional[str] = None
    portal: str
    department: Optional[str] = None
    branch: Optional[str] = None
    is_active: bool
    two_factor_enabled: bool
    last_login_at: Optional[datetime] = None


class RoleOut(BaseModel):
    id: int
    name: str
    slug: str
    portal: str
    is_system: bool
    description: Optional[str] = None
    permissions: list[str]
    user_count: int = 0


class SettingOut(BaseModel):
    key: str
    group: str
    value: Any = None
    description: Optional[str] = None


class AuditEventOut(BaseModel):
    id: int
    created_at: datetime
    actor: Optional[str] = None
    action: str
    module: str
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    description: Optional[str] = None
    rationale: Optional[str] = None
    severity: str
    is_consequential: bool
    ip: Optional[str] = None


class BackupOut(BaseModel):
    id: int
    filename: str
    size_bytes: int
    backup_type: str
    status: str
    restore_tested: bool
    created_at: datetime


class MigrationJobOut(BaseModel):
    id: int
    name: str
    entity: str
    status: str
    records_total: int
    records_imported: int
    records_skipped: int
    duplicates_found: int


class InboundAck(BaseModel):
    received: bool = True
    delivery_id: Optional[int] = None
    event: Optional[str] = None


class PermissionCheckOut(BaseModel):
    user_id: int
    permission: str
    allowed: bool
    reasons: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------------- helpers
def _user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, username=u.username, full_name=u.full_name,
                   role=u.role.slug if u.role else None, portal=u.portal,
                   department=u.department.name if u.department else None,
                   branch=u.branch.name if u.branch else None, is_active=u.is_active,
                   two_factor_enabled=u.two_factor_enabled, last_login_at=u.last_login_at)


def _audit_out(e: AuditEvent) -> AuditEventOut:
    return AuditEventOut(id=e.id, created_at=e.created_at, actor=e.actor_name, action=e.action, module=e.module,
                         entity_type=e.entity_type, entity_id=e.entity_id, description=e.description,
                         rationale=e.rationale, severity=e.severity, is_consequential=e.is_consequential, ip=e.ip)


# ----------------------------------------------------------------------------- endpoints
@router.get("/status", response_model=SystemStatusOut, summary="Platform status, background jobs and integration health")
def system_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.core.scheduler import JOB_STATUS, discover_jobs
    jobs = []
    known = {jid: minutes for jid, _fn, minutes in discover_jobs()}
    for jid, minutes in sorted(known.items()):
        st = JOB_STATUS.get(jid, {})
        jobs.append(JobStatusOut(id=jid, interval_minutes=int(st.get("interval") or minutes),
                                 status=str(st.get("status") or "scheduled"), last_run=st.get("last_run"),
                                 result=(str(st.get("result"))[:300] if st.get("result") is not None else None)))
    integrations = [IntegrationOut(provider=i.provider, name=i.name, status=i.status, health=i.health,
                                   last_health_check_at=i.last_health_check_at, calls_today=i.calls_today or 0,
                                   failures_today=i.failures_today or 0, last_error=i.last_error)
                    for i in db.query(Integration).order_by(Integration.provider).all()]
    latest = db.query(BackupRecord).order_by(BackupRecord.created_at.desc()).first()
    counts = {
        "users": db.query(func.count(User.id)).scalar() or 0,
        "active_users": db.query(func.count(User.id)).filter(User.is_active.is_(True)).scalar() or 0,
        "roles": db.query(func.count(Role.id)).scalar() or 0,
        "active_sessions": db.query(func.count(UserSession.id)).filter(
            UserSession.revoked.is_(False), UserSession.expires_at > datetime.utcnow()).scalar() or 0,
        "api_keys": db.query(func.count(ApiKey.id)).filter(ApiKey.is_active.is_(True)).scalar() or 0,
        "webhooks": db.query(func.count(Webhook.id)).filter(Webhook.is_active.is_(True)).scalar() or 0,
        "failed_deliveries": db.query(func.count(WebhookDelivery.id)).filter(
            WebhookDelivery.status.in_(["failed", "dead"])).scalar() or 0,
        "audit_events": db.query(func.count(AuditEvent.id)).scalar() or 0,
    }
    return SystemStatusOut(
        app=cfg.APP_NAME, version="1.1.0", environment=cfg.APP_ENV,
        database="sqlite" if cfg.is_sqlite else "postgresql", server_time=datetime.utcnow(),
        jobs=jobs, integrations=integrations, counts=counts,
        backups={"total": db.query(func.count(BackupRecord.id)).scalar() or 0,
                 "latest": latest.filename if latest else None,
                 "latest_at": latest.created_at.isoformat() if latest else None,
                 "restore_tested": bool(latest.restore_tested) if latest else False})


@router.get("/users", response_model=list[UserOut], summary="List platform users")
def list_users(q: str = "", role: str = "", active: Optional[bool] = None,
               limit: int = Query(50, le=500), offset: int = 0,
               db: Session = Depends(get_db), user: User = Depends(require("users.view"))):
    qry = db.query(User).outerjoin(Role, User.role_id == Role.id)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(User.full_name.ilike(like), User.email.ilike(like), User.username.ilike(like)))
    if role:
        qry = qry.filter(Role.slug == role)
    if active is not None:
        qry = qry.filter(User.is_active.is_(active))
    return [_user_out(u) for u in qry.order_by(User.full_name).offset(offset).limit(limit).all()]


@router.get("/users/{user_id}", response_model=UserOut, summary="Fetch a single user")
def get_user(user_id: int, db: Session = Depends(get_db), user: User = Depends(require("users.view"))):
    obj = db.get(User, user_id)
    if not obj:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_out(obj)


@router.get("/users/{user_id}/permissions/{permission}", response_model=PermissionCheckOut,
            summary="Test whether a user holds a permission, with the reason chain")
def check_permission(user_id: int, permission: str, db: Session = Depends(get_db),
                     user: User = Depends(require("roles.view"))):
    obj = db.get(User, user_id)
    if not obj:
        raise HTTPException(status_code=404, detail="User not found")
    result = sys_svc.explain_permission(obj, permission)
    return PermissionCheckOut(user_id=user_id, permission=permission, allowed=result["allowed"], reasons=result["steps"])


@router.get("/roles", response_model=list[RoleOut], summary="List roles and their permission patterns")
def list_roles(db: Session = Depends(get_db), user: User = Depends(require("roles.view"))):
    counts = sys_svc.role_user_counts(db)
    return [RoleOut(id=r.id, name=r.name, slug=r.slug, portal=r.portal, is_system=r.is_system,
                    description=r.description, permissions=list(r.permissions or []), user_count=counts.get(r.id, 0))
            for r in db.query(Role).order_by(Role.name).all()]


@router.get("/permissions", response_model=dict, summary="Permission catalogue (modules x actions)")
def list_permissions(user: User = Depends(require("roles.view"))):
    return {"modules": rbac.MODULES, "actions": rbac.ACTIONS, "permissions": sys_svc.all_permission_strings()}


@router.get("/settings", response_model=list[SettingOut], summary="Configuration settings")
def list_settings(group: str = "", db: Session = Depends(get_db), user: User = Depends(require("settings.view"))):
    qry = db.query(Setting)
    if group:
        qry = qry.filter(Setting.group == group)
    return [SettingOut(key=s.key, group=s.group, value=s.value, description=s.description)
            for s in qry.order_by(Setting.group, Setting.key).all()]


@router.get("/integrations", response_model=list[IntegrationOut], summary="Integration health")
def list_integrations(db: Session = Depends(get_db), user: User = Depends(require("integrations.view"))):
    return [IntegrationOut(provider=i.provider, name=i.name, status=i.status, health=i.health,
                           last_health_check_at=i.last_health_check_at, calls_today=i.calls_today or 0,
                           failures_today=i.failures_today or 0, last_error=i.last_error)
            for i in db.query(Integration).order_by(Integration.provider).all()]


@router.get("/audit", response_model=list[AuditEventOut], summary="Query the immutable audit log")
def list_audit(module: str = "", action: str = "", entity_type: str = "", entity_id: Optional[int] = None,
               consequential: Optional[bool] = None, limit: int = Query(100, le=1000), offset: int = 0,
               db: Session = Depends(get_db), user: User = Depends(require("audit.view"))):
    qry = db.query(AuditEvent)
    if module:
        qry = qry.filter(AuditEvent.module == module)
    if action:
        qry = qry.filter(AuditEvent.action == action)
    if entity_type:
        qry = qry.filter(AuditEvent.entity_type == entity_type)
    if entity_id is not None:
        qry = qry.filter(AuditEvent.entity_id == entity_id)
    if consequential is not None:
        qry = qry.filter(AuditEvent.is_consequential.is_(consequential))
    return [_audit_out(e) for e in qry.order_by(AuditEvent.created_at.desc()).offset(offset).limit(limit).all()]


@router.get("/backups", response_model=list[BackupOut], summary="Backup archives")
def list_backups(limit: int = Query(50, le=200), db: Session = Depends(get_db),
                 user: User = Depends(require("backups.view"))):
    return [BackupOut(id=b.id, filename=b.filename, size_bytes=b.size_bytes, backup_type=b.backup_type,
                      status=b.status, restore_tested=b.restore_tested, created_at=b.created_at)
            for b in db.query(BackupRecord).order_by(BackupRecord.created_at.desc()).limit(limit).all()]


@router.post("/backups", response_model=BackupOut, summary="Create a backup now")
def create_backup(request: Request, db: Session = Depends(get_db), user: User = Depends(require("backups.execute"))):
    rec = sys_svc.create_backup(db, user, backup_type="manual")
    log_action(db, user, "execute", "backups", entity=rec, severity="warning", consequential=True,
               rationale="Backup requested through the REST API", description=f"Created backup {rec.filename}",
               request=request)
    db.commit()
    return BackupOut(id=rec.id, filename=rec.filename, size_bytes=rec.size_bytes, backup_type=rec.backup_type,
                     status=rec.status, restore_tested=rec.restore_tested, created_at=rec.created_at)


@router.get("/migration-jobs", response_model=list[MigrationJobOut], summary="Data migration jobs")
def list_migration_jobs(db: Session = Depends(get_db), user: User = Depends(require("migration.view"))):
    return [MigrationJobOut(id=j.id, name=j.name, entity=j.entity, status=j.status, records_total=j.records_total,
                            records_imported=j.records_imported, records_skipped=j.records_skipped,
                            duplicates_found=j.duplicates_found)
            for j in db.query(MigrationJob).order_by(MigrationJob.created_at.desc()).limit(100).all()]


@router.post("/webhooks/inbound/{source}", response_model=InboundAck,
             summary="Generic inbound webhook receiver (n8n and other automation tools)")
async def inbound_webhook(source: str, request: Request, db: Session = Depends(get_db)):
    """Records the payload as an inbound WebhookDelivery. Protect with the shared secret in the integration config."""
    integ = db.query(Integration).filter(Integration.provider == source).first()
    expected = (integ.config or {}).get("inbound_secret") if integ else None
    if expected and request.headers.get("X-OQC-Secret") != expected:
        raise HTTPException(status_code=401, detail="Invalid inbound secret")
    try:
        payload = await request.json()
    except Exception:
        payload = {"raw": (await request.body()).decode("utf-8", errors="replace")[:2000]}
    event = str(payload.get("event") or f"{source}.inbound") if isinstance(payload, dict) else f"{source}.inbound"
    delivery = WebhookDelivery(webhook_id=None, direction="in", event=event,
                               payload=payload if isinstance(payload, dict) else {"data": payload},
                               status="success", attempts=1, response_code=200)
    db.add(delivery)
    if integ:
        integ.calls_today = (integ.calls_today or 0) + 1
        integ.last_health_check_at = datetime.utcnow()
    db.commit()
    return InboundAck(received=True, delivery_id=delivery.id, event=event)
