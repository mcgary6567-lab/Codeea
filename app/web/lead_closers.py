"""Lead Closers — the Billing Management > Configurations catalogue (docs/AUDIT_BILLING.md).

"The same shape as billing groups, for the people who close leads":
ID · Closer Name · Sudo Name · Representative Name · Status, with Create, inline edit and
activate / deactivate. Lives in its own router because `app/web/finance.py` belongs to the
billing module; both mount under the `/finance` prefix.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import parse_int, redirect
from app.database import get_db
from app.models.core import Role, User
from app.models.erp import LeadCloser

router = APIRouter(prefix="/finance", dependencies=[Depends(csrf_protect)])

BASE = "/finance/lead-closers"
MODULE = "leads"
STATUSES = [("active", "Active"), ("inactive", "In-Active")]


# ============================================================================== helpers
def _closer(db: Session, cid: int) -> LeadCloser:
    obj = db.get(LeadCloser, cid)
    if not obj:
        raise HTTPException(404, "Lead closer not found")
    return obj


def _representative_options(db: Session) -> list[tuple[int, str]]:
    """Staff who can be answerable for a book of leads: any active admin-portal user."""
    users = (db.query(User).join(Role, Role.id == User.role_id)
             .filter(User.is_active.is_(True), Role.portal == "admin").order_by(User.full_name).all())
    return [(u.id, f"{u.full_name} — {u.role.name if u.role else 'User'}") for u in users]


def _status_field(form, default: str = "active") -> str:
    v = (form.get("status") or default).strip().lower()
    return v if v in ("active", "inactive") else default


def _apply(c: LeadCloser, form) -> None:
    c.name = (form.get("name") or c.name).strip()
    c.pseudo_name = (form.get("pseudo_name") or "").strip() or None
    c.representative_id = parse_int(form.get("representative_id"))
    c.status = _status_field(form, c.status or "active")


# ============================================================================== list
@router.get("/lead-closers", include_in_schema=False)
def lead_closers_page(request: Request, q: str = "", status: str = "", representative: str = "",
                      db: Session = Depends(get_db), user: User = Depends(require(f"{MODULE}.view"))):
    query = db.query(LeadCloser)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(LeadCloser.name.ilike(like), LeadCloser.pseudo_name.ilike(like)))
    if status:
        query = query.filter(LeadCloser.status == status)
    if parse_int(representative):
        query = query.filter(LeadCloser.representative_id == int(representative))
    rows = query.order_by(LeadCloser.status, LeadCloser.name).all()

    counts = dict(db.query(LeadCloser.status, func.count(LeadCloser.id)).group_by(LeadCloser.status).all())
    stats = {"active": counts.get("active", 0), "inactive": counts.get("inactive", 0),
             "total": sum(counts.values()),
             "represented": db.query(func.count(LeadCloser.id)).filter(LeadCloser.representative_id.isnot(None)).scalar() or 0}
    return render(request, "crm/lead_closers.html", {
        "user": user, "rows": rows, "stats": stats, "q": q, "status": status, "representative": representative,
        "statuses": STATUSES, "representative_options": _representative_options(db),
        "can_edit": rbac.has_permission(user, f"{MODULE}.update")})


# ============================================================================== mutations
@router.post("/lead-closers/new", include_in_schema=False)
async def lead_closer_create(request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require(f"{MODULE}.update"))):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return redirect(BASE, "Closer name is required.", "error")
    if db.query(LeadCloser).filter(func.lower(LeadCloser.name) == name.lower()).first():
        return redirect(BASE, f"A lead closer called {name} already exists.", "error")
    c = LeadCloser(name=name, status="active")
    _apply(c, form)
    db.add(c)
    db.flush()
    log_action(db, user, "create", MODULE, entity=c, description=f"Lead closer {c.name} created",
               after=snapshot(c), request=request)
    db.commit()
    return redirect(BASE, f"Lead closer {c.name} created.")


@router.post("/lead-closers/{cid}/edit", include_in_schema=False)
async def lead_closer_edit(cid: int, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(require(f"{MODULE}.update"))):
    c = _closer(db, cid)
    form = await request.form()
    before = snapshot(c)
    _apply(c, form)
    log_action(db, user, "update", MODULE, entity=c, description=f"Lead closer {c.name} updated",
               before=before, after=snapshot(c), request=request)
    db.commit()
    return redirect(BASE, f"Lead closer {c.name} updated.")


@router.post("/lead-closers/{cid}/toggle", include_in_schema=False)
async def lead_closer_toggle(cid: int, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(require(f"{MODULE}.update"))):
    c = _closer(db, cid)
    before = snapshot(c)
    c.status = "inactive" if c.status == "active" else "active"
    log_action(db, user, "status_change", MODULE, entity=c, description=f"Lead closer {c.name} marked {c.status}",
               before=before, after=snapshot(c), request=request)
    db.commit()
    return redirect(BASE, f"Lead closer {c.name} marked {'Active' if c.status == 'active' else 'In-Active'}.")
