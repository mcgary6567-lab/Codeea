"""Root redirect and alerts inbox (shared by all admin roles)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import get_current_user, get_optional_user, require, csrf_protect
from app.core.nav import home_for
from app.core.templating import render
from app.core.utils import redirect, paginate
from app.database import get_db
from app.models.core import RiskAlert, User

router = APIRouter(dependencies=[Depends(csrf_protect)])


@router.get("/", include_in_schema=False)
def root(user=Depends(get_optional_user)):
    return RedirectResponse(home_for(user) if user else "/login", status_code=303)


@router.get("/alerts", include_in_schema=False)
def alerts(request: Request, status: str = "open", severity: str = "", page: int = 1,
           db: Session = Depends(get_db), user: User = Depends(require("dashboard.view"))):
    q = db.query(RiskAlert)
    if not rbac.is_ceo(user):
        q = q.filter(RiskAlert.visibility != "ceo_only")
    if not rbac.is_management(user):
        q = q.filter(RiskAlert.visibility == "ops")
    if status:
        q = q.filter(RiskAlert.status == status)
    if severity:
        q = q.filter(RiskAlert.severity == severity)
    pg = paginate(q.order_by(RiskAlert.created_at.desc()), page, 30)
    return render(request, "alerts.html", {"user": user, "page": pg, "status": status, "severity": severity})


@router.post("/alerts/{alert_id}/{action}", include_in_schema=False)
def alert_action(alert_id: int, action: str, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require("dashboard.update"))):
    a = db.query(RiskAlert).get(alert_id)
    if a:
        if action == "acknowledge":
            a.status, a.acknowledged_by_id = "acknowledged", user.id
        elif action == "resolve":
            a.status, a.resolved_at = "resolved", datetime.utcnow()
        elif action == "dismiss":
            a.status = "dismissed"
        log_action(db, user, action, "alerts", entity=a, description=a.title, request=request)
        db.commit()
    return redirect("/alerts", f"Alert {action}d.")
