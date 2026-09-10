"""Immutable audit logging (Section 15 / Module 48)."""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.core import AuditEvent, User

CONSEQUENTIAL_ACTIONS = {
    "approve", "reject", "refund", "discount", "salary_change", "grade_change", "schedule_change", "delete",
    "safeguarding_access", "recording_access", "payroll_approve", "override", "revoke", "role_change",
    "permission_change", "export", "offboard", "cancel", "freeze",
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def snapshot(obj: Any, fields: Optional[list[str]] = None) -> dict:
    """Serialize a SQLAlchemy model's column values for before/after audit data."""
    if obj is None:
        return {}
    cols = [c.name for c in obj.__table__.columns]
    if fields:
        cols = [c for c in cols if c in fields]
    return {c: _jsonable(getattr(obj, c, None)) for c in cols if c not in ("hashed_password", "two_factor_secret", "key_hash")}


def log_action(
    db: Session,
    actor: Optional[User],
    action: str,
    module: str,
    entity: Any = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    description: Optional[str] = None,
    rationale: Optional[str] = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    request: Optional[Request] = None,
    severity: str = "info",
    consequential: Optional[bool] = None,
    commit: bool = False,
) -> AuditEvent:
    if entity is not None:
        entity_type = entity_type or entity.__class__.__name__
        entity_id = entity_id if entity_id is not None else getattr(entity, "id", None)
    ip = None
    if request is not None:
        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (request.client.host if request.client else None)
    if consequential is None:
        consequential = action in CONSEQUENTIAL_ACTIONS or severity in ("warning", "critical")
    ev = AuditEvent(
        actor_id=actor.id if actor else None,
        actor_name=actor.full_name if actor else "system",
        action=action,
        module=module,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        rationale=rationale,
        before_data=_jsonable(before) if before else None,
        after_data=_jsonable(after) if after else None,
        ip=ip,
        severity=severity,
        is_consequential=consequential,
    )
    db.add(ev)
    if commit:
        db.commit()
    return ev
