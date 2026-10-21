"""Interactive report endpoints used by the toolbar (app/static/js/report.js): saved views, Excel files, subscriptions."""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import csrf_protect, require_staff
from app.core.link_guard import can_open
from app.database import get_db
from app.models.core import User
from app.models.ops import ReportSubscription, ReportView
from app.services import ir

router = APIRouter(prefix="/ir", dependencies=[Depends(csrf_protect)])

MAX_EXPORT_ROWS = 200_000


async def _json(request: Request) -> dict:
    try:
        data = json.loads(await request.body() or b"{}")
    except ValueError:
        raise HTTPException(400, "Expected JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "Expected a JSON object")
    return data


def _path(raw) -> str:
    path = str(raw or "").split("?", 1)[0][:300]
    if not path.startswith("/") or path.startswith("//"):
        raise HTTPException(400, "Bad page path")
    return path


def _view(v: ReportView, user: User) -> dict:
    return {"id": v.id, "name": v.name, "config": v.config or {}, "shared": v.is_shared, "default": v.is_default,
            "mine": v.user_id == user.id, "owner": v.user.full_name if v.user else ""}


# ----------------------------------------------------------------------------- saved views
@router.get("/views", include_in_schema=False)
def list_views(path: str, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    path = _path(path)
    rows = (db.query(ReportView).filter(ReportView.path == path, or_(ReportView.user_id == user.id, ReportView.is_shared.is_(True)))
            .order_by(ReportView.name).all())
    return {"views": [_view(v, user) for v in rows]}


@router.post("/views", include_in_schema=False)
async def save_view(request: Request, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    data = await _json(request)
    path, name = _path(data.get("path")), str(data.get("name") or "").strip()[:120]
    if not name:
        raise HTTPException(400, "Give the report a name")
    if not can_open(user, path):
        raise HTTPException(403, "You cannot open that page")
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    v = db.query(ReportView).filter(ReportView.user_id == user.id, ReportView.path == path, ReportView.name == name).first()
    if v is None:
        v = ReportView(user_id=user.id, path=path, name=name)
        db.add(v)
    v.config, v.is_shared, v.is_default = config, bool(data.get("shared")), bool(data.get("default"))
    if v.is_default:  # one default per person and page
        db.query(ReportView).filter(ReportView.user_id == user.id, ReportView.path == path, ReportView.name != name
                                    ).update({ReportView.is_default: False})
    db.flush()
    log_action(db, user, "create", "reports", entity=v, description=f"Saved report view '{name}' on {path}", request=request)
    db.commit()
    return _view(v, user)


@router.post("/views/{view_id}/delete", include_in_schema=False)
def delete_view(view_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    v = db.get(ReportView, view_id)
    if v is None or v.user_id != user.id:
        raise HTTPException(404, "Report not found")
    db.delete(v)
    log_action(db, user, "delete", "reports", description=f"Deleted report view '{v.name}' on {v.path}", request=request)
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------------------- files
@router.post("/export", include_in_schema=False)
async def export(request: Request, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    """Turn the rows the toolbar collected (already limited to what this user can see) into an Excel or CSV file."""
    if not ir.can_export(db, user):
        raise HTTPException(403, "Exports are limited to admins")
    data = await _json(request)
    headers = [str(h)[:200] for h in (data.get("headers") or [])][:200]
    rows = [[str(c)[:5000] for c in (r or [])][:200] for r in (data.get("rows") or [])[:MAX_EXPORT_ROWS]]
    name = str(data.get("name") or "report")[:120]
    fmt = data.get("format") or "xlsx"
    log_action(db, user, "export", "reports", description=f"Exported {len(rows)} row(s) of '{name}' as {fmt}",
               request=request)
    db.commit()
    stamp = datetime.utcnow().strftime("%Y-%m-%d")
    if fmt == "csv":
        return Response(ir.csv_bytes(headers, rows), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{ir.slug(name)}-{stamp}.csv"'})
    return Response(ir.xlsx_bytes(name, headers, rows),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{ir.slug(name)}-{stamp}.xlsx"'})


# ----------------------------------------------------------------------------- subscriptions
def _sub(s: ReportSubscription) -> dict:
    return {"id": s.id, "name": s.name, "emails": s.emails, "frequency": s.frequency, "active": s.is_active,
            "next_run_at": s.next_run_at.isoformat(timespec="minutes") if s.next_run_at else None,
            "last_run_at": s.last_run_at.isoformat(timespec="minutes") if s.last_run_at else None,
            "last_status": s.last_status}


@router.get("/subscriptions", include_in_schema=False)
def list_subscriptions(path: str, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    rows = (db.query(ReportSubscription).filter(ReportSubscription.user_id == user.id, ReportSubscription.path == _path(path))
            .order_by(ReportSubscription.name).all())
    return {"subscriptions": [_sub(s) for s in rows]}


@router.post("/subscriptions", include_in_schema=False)
async def create_subscription(request: Request, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    data = await _json(request)
    path = _path(data.get("path"))
    if not can_open(user, path):
        raise HTTPException(403, "You cannot open that page")
    name = str(data.get("name") or "").strip()[:120]
    if not name:
        raise HTTPException(400, "Give the subscription a name")
    frequency = data.get("frequency") if data.get("frequency") in ir.FREQUENCIES else "weekly"
    try:
        emails = ir.clean_emails(str(data.get("emails") or user.email))
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    query = "&".join(p for p in str(data.get("query") or "").lstrip("?").split("&") if p and not p.startswith(("rows=", "page=")))[:1000]
    s = ReportSubscription(user_id=user.id, path=path, query=query or None, name=name, emails=", ".join(emails),
                           frequency=frequency, config=data.get("config") if isinstance(data.get("config"), dict) else {},
                           next_run_at=ir.next_run(frequency))
    db.add(s)
    db.flush()
    log_action(db, user, "create", "reports", entity=s, description=f"Subscribed {', '.join(emails)} to '{name}' ({frequency})",
               request=request)
    db.commit()
    return _sub(s)


def _own_sub(db: Session, sub_id: int, user: User) -> ReportSubscription:
    s = db.get(ReportSubscription, sub_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(404, "Subscription not found")
    return s


@router.post("/subscriptions/{sub_id}/send", include_in_schema=False)
def send_now(sub_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    s = _own_sub(db, sub_id, user)
    try:
        result = ir.send(db, s)
        s.last_status = f"Sent {result['rows']} row(s) as {result['file']}"
    except Exception as exc:
        s.last_status = f"Failed: {str(exc)[:250]}"
    s.last_run_at = datetime.utcnow()
    db.commit()
    return _sub(s)


@router.post("/subscriptions/{sub_id}/delete", include_in_schema=False)
def delete_subscription(sub_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    s = _own_sub(db, sub_id, user)
    db.delete(s)
    log_action(db, user, "delete", "reports", description=f"Removed the subscription '{s.name}'", request=request)
    db.commit()
    return {"ok": True}
