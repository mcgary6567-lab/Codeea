"""GoHighLevel sync console (docs/GHL_INTEGRATION.md): status, what the ERP sends, the sync log with retries, and
conflicts to resolve. Part of Configuration › Integration Hub."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings as cfg
from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import paginate, parse_int, redirect
from app.database import get_db
from app.models.core import User
from app.models.crm import GHL_SYNC_STATUSES, GhlSyncJob, Lead
from app.models.people import Client
from app.services import ghl_sync as svc

router = APIRouter(prefix="/admin/integrations/ghl", dependencies=[Depends(csrf_protect)])


def _get(db: Session, id: int) -> GhlSyncJob:
    job = db.get(GhlSyncJob, id)
    if not job:
        raise HTTPException(404, "Sync entry not found")
    return job


def _label(db: Session, job: GhlSyncJob) -> tuple[str, str]:
    if job.entity_type == "lead":
        rec = db.get(Lead, job.entity_id) if job.entity_id else None
        return (f"{rec.full_name} ({rec.lead_code})" if rec else f"Lead #{job.entity_id}"), (f"/crm/leads/{rec.id}" if rec else "")
    rec = db.get(Client, job.entity_id) if job.entity_id else None
    return (f"{rec.full_name} ({rec.client_code})" if rec else f"Family #{job.entity_id}"), (f"/clients/{rec.id}" if rec else "")


@router.get("/sync", include_in_schema=False)
def console(request: Request, page: int = 1, status: str = "", direction: str = "", entity: str = "", q: str = "",
            db: Session = Depends(get_db), user: User = Depends(require("integrations.view"))):
    query = db.query(GhlSyncJob)
    if status:
        query = query.filter(GhlSyncJob.status == status)
    if direction in ("in", "out"):
        query = query.filter(GhlSyncJob.direction == direction)
    if entity in ("lead", "client"):
        query = query.filter(GhlSyncJob.entity_type == entity)
    if q:
        query = query.filter(GhlSyncJob.ghl_contact_id.ilike(f"%{q}%") | GhlSyncJob.operation.ilike(f"%{q}%") | GhlSyncJob.event.ilike(f"%{q}%"))
    pg = paginate(query.order_by(GhlSyncJob.id.desc()), page, 40)
    conflicts = db.query(GhlSyncJob).filter(GhlSyncJob.status == "conflict").order_by(GhlSyncJob.id.desc()).limit(20).all()
    labels = {j.id: _label(db, j) for j in list(pg.items) + conflicts}
    db.commit()
    return render(request, "admin/ghl_sync.html", {
        "user": user, "page": pg, "stats": svc.stats(db), "conflicts": conflicts, "labels": labels, "status": status,
        "direction": direction, "entity": entity, "q": q, "statuses": GHL_SYNC_STATUSES, "event_map": svc.EVENT_MAP,
        "event_labels": svc.EVENT_LABELS, "stage_names": svc.STAGE_NAMES, "stage_map": svc.stage_map(db), "base_url_site": cfg.BASE_URL,
        "max_attempts": svc.MAX_ATTEMPTS, "backoff": svc.BACKOFF_MINUTES,
        "base_url": f"/admin/integrations/ghl/sync?status={status}&direction={direction}&entity={entity}&q={q}"})


@router.post("/sync/run", include_in_schema=False)
async def run_now(request: Request, db: Session = Depends(get_db), user: User = Depends(require("integrations.configure"))):
    result = svc.process(db)
    log_action(db, user, "execute", "integrations", description=f"GHL sync run by hand: {result}", request=request)
    db.commit()
    if result.get("skipped"):
        return redirect("/admin/integrations/ghl/sync", "The sync is paused. Turn it on in the GoHighLevel settings first.", "warning")
    return redirect("/admin/integrations/ghl/sync",
                    f"Processed {result['processed']}: {result['sent']} sent, {result['failed']} to retry, {result['dead']} failed for good"
                    + (" (simulated: no GHL API key)" if result.get("simulated") else "") + ".")


@router.post("/sync/toggle", include_in_schema=False)
async def toggle(request: Request, db: Session = Depends(get_db), user: User = Depends(require("integrations.configure"))):
    form = await request.form()
    rationale = (form.get("rationale") or "").strip()
    if not rationale:
        return redirect("/admin/integrations/ghl/sync", "A rationale is required.", "error")
    integ = svc.integration(db)
    config = dict(integ.config or {})
    turning_on = not svc.enabled(db)
    config[svc.ENABLED_KEY] = "yes" if turning_on else "no"
    integ.config = config
    log_action(db, user, "update", "integrations", entity=integ, consequential=True, severity="warning", rationale=rationale,
               description=f"GoHighLevel sync turned {'on' if turning_on else 'off'}", request=request)
    db.commit()
    return redirect("/admin/integrations/ghl/sync", f"GoHighLevel sync is now {'on' if turning_on else 'off'}.")


@router.post("/sync/retry-failed", include_in_schema=False)
async def retry_failed(request: Request, db: Session = Depends(get_db), user: User = Depends(require("integrations.configure"))):
    n = 0
    for job in db.query(GhlSyncJob).filter(GhlSyncJob.direction == "out", GhlSyncJob.status.in_(["failed", "dead"])).all():
        svc.retry(db, job, user)
        n += 1
    db.commit()
    return redirect("/admin/integrations/ghl/sync", f"{n} job(s) queued again.")


@router.post("/sync/{id}/retry", include_in_schema=False)
async def retry_one(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("integrations.configure"))):
    job = _get(db, id)
    try:
        svc.retry(db, job, user)
    except ValueError as exc:
        return redirect("/admin/integrations/ghl/sync", str(exc), "error")
    db.commit()
    return redirect("/admin/integrations/ghl/sync", f"Job #{job.id} queued again.")


@router.post("/sync/{id}/resolve", include_in_schema=False)
async def resolve(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("integrations.configure"))):
    job = _get(db, id)
    form = await request.form()
    try:
        svc.resolve_conflict(db, job, user, form.get("choice") or "", request=request)
    except ValueError as exc:
        db.rollback()
        return redirect("/admin/integrations/ghl/sync", str(exc), "error")
    db.commit()
    return redirect("/admin/integrations/ghl/sync", "Conflict resolved.")


@router.post("/sync/push/{entity}/{entity_id}", include_in_schema=False)
async def push(entity: str, entity_id: int, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require("integrations.configure"))):
    if entity not in ("lead", "client"):
        raise HTTPException(404, "Unknown record type")
    res = svc.sync_now(db, entity, parse_int(entity_id) or 0, user)
    db.commit()
    back = request.headers.get("referer") or "/admin/integrations/ghl/sync"
    return redirect(back, f"Sync to GoHighLevel: {res['status']}" + (f" ({res['error']})" if res.get("error") else ""),
                    "success" if res["status"] == "sent" else "warning")
