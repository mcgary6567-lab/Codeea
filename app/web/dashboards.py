"""ERP-parity dashboards (docs/AUDIT_ACADEMICS.md 3.14).

Seven read-only pages under ``/dashboards`` — the URLs are the ones listed in ``app/core/nav.py`` under the
"Dashboards" group. Every aggregation lives in ``app/services/dashboards.py``; this module only parses the
filter bar and renders.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import parse_date, parse_int, redirect
from app.database import get_db
from app.models.core import User
from app.services import dashboards as svc

router = APIRouter(prefix="/dashboards", dependencies=[Depends(csrf_protect)])


def _qs(**kw) -> str:
    """Query string for chart drill-downs / Reset links (empty values dropped)."""
    parts = [f"{k}={v}" for k, v in kw.items() if v not in (None, "", [])]
    return "&".join(parts)


@router.get("", include_in_schema=False)
def index(user: User = Depends(require("dashboards.view"))):
    return redirect("/dashboards/clients")


# ============================================================================ 1. Client Management
@router.get("/clients", include_in_schema=False)
def clients_dashboard(request: Request, date_from: str = "", date_to: str = "", client_id: str = "",
                      country: str = "", shift: str = "", status: str = "",
                      db: Session = Depends(get_db), user: User = Depends(require("dashboards.view"))):
    df, dt = parse_date(date_from), parse_date(date_to)
    cid = parse_int(client_id)
    data = svc.client_dashboard(db, date_from=df, date_to=dt, client_id=cid, country=country, shift=shift,
                                status=status)
    return render(request, "dashboards/clients.html", {
        "user": user, "d": data, "opts": svc.options(db), "date_from": date_from, "date_to": date_to,
        "client_id": cid, "country": country, "shift": shift, "status": status,
        "qs": _qs(date_from=date_from, date_to=date_to, client_id=client_id, country=country, shift=shift)})


# ============================================================================ 2/3. Subscriptions
def _subscription_page(request: Request, db: Session, user: User, amount: bool, date_from: str, date_to: str,
                       teacher_id: str, shift: str, status: str, country: str):
    df, dt = parse_date(date_from), parse_date(date_to)
    tid = parse_int(teacher_id)
    data = svc.subscription_dashboard(db, date_from=df, date_to=dt, teacher_id=tid, shift=shift, status=status,
                                      country=country, amount=amount)
    return render(request, "dashboards/subscriptions.html", {
        "user": user, "d": data, "opts": svc.options(db), "date_from": date_from, "date_to": date_to,
        "teacher_id": tid, "shift": shift, "status": status, "country": country, "amount": amount,
        "action": "/dashboards/subscriptions/amount" if amount else "/dashboards/subscriptions",
        "title": "Subscriptions (Amount)" if amount else "Subscriptions (Count)"})


@router.get("/subscriptions/amount", include_in_schema=False)
def subscriptions_amount(request: Request, date_from: str = "", date_to: str = "", teacher_id: str = "",
                         shift: str = "", status: str = "", country: str = "",
                         db: Session = Depends(get_db), user: User = Depends(require("dashboards.view"))):
    return _subscription_page(request, db, user, True, date_from, date_to, teacher_id, shift, status, country)


@router.get("/subscriptions", include_in_schema=False)
def subscriptions_count(request: Request, date_from: str = "", date_to: str = "", teacher_id: str = "",
                        shift: str = "", status: str = "", country: str = "",
                        db: Session = Depends(get_db), user: User = Depends(require("dashboards.view"))):
    return _subscription_page(request, db, user, False, date_from, date_to, teacher_id, shift, status, country)


# ============================================================================ 4. Billing Management
@router.get("/billing", include_in_schema=False)
def billing_dashboard(request: Request, date_from: str = "", date_to: str = "", teacher_id: str = "",
                      country: str = "", shift: str = "",
                      db: Session = Depends(get_db), user: User = Depends(require("dashboards.view"))):
    df, dt = parse_date(date_from), parse_date(date_to)
    tid = parse_int(teacher_id)
    data = svc.billing_dashboard(db, date_from=df, date_to=dt, teacher_id=tid, country=country, shift=shift)
    return render(request, "dashboards/billing.html", {
        "user": user, "d": data, "opts": svc.options(db),
        "date_from": date_from or data["filters"]["date_from"].isoformat(),
        "date_to": date_to or data["filters"]["date_to"].isoformat(),
        "teacher_id": tid, "country": country, "shift": shift})


# ============================================================================ 5. Monthly Performance
@router.get("/monthly-performance", include_in_schema=False)
def monthly_performance(request: Request, start: str = "", end: str = "", department: str = "all",
                        employee_ids: list[str] = Query(default=[]),
                        db: Session = Depends(get_db), user: User = Depends(require("dashboards.view"))):
    s, e = parse_date(start), parse_date(end)
    ids = [i for i in (parse_int(x) for x in employee_ids) if i]
    data = svc.monthly_performance(db, start=s, end=e, employee_ids=ids, department=department)
    return render(request, "dashboards/monthly_performance.html", {
        "user": user, "d": data, "opts": svc.options(db),
        "start": start or data["filters"]["start"].isoformat(),
        "end": end or data["filters"]["end"].isoformat(),
        "employee_ids": ids, "department": data["filters"]["department"]})


# ============================================================================ 6. Financial Summary
@router.get("/financial-summary", include_in_schema=False)
def financial_summary(request: Request, date_from: str = "", date_to: str = "", client_id: str = "",
                      shift: str = "", currency: str = "", group_id: str = "", country: str = "",
                      state: str = "", status: str = "",
                      db: Session = Depends(get_db), user: User = Depends(require("dashboards.view"))):
    df, dt = parse_date(date_from), parse_date(date_to)
    data = svc.financial_summary(db, date_from=df, date_to=dt, client_id=parse_int(client_id), shift=shift,
                                 currency=currency, group_id=parse_int(group_id), country=country, state=state,
                                 status=status)
    return render(request, "dashboards/financial_summary.html", {
        "user": user, "d": data, "opts": svc.options(db), "date_from": date_from, "date_to": date_to,
        "client_id": parse_int(client_id), "shift": shift, "currency": currency,
        "group_id": parse_int(group_id), "country": country, "state": state, "status": status})


# ============================================================================ 7. Monthly Performance Insights
@router.get("/monthly-insights", include_in_schema=False)
def monthly_insights(request: Request, month: str = "",
                     db: Session = Depends(get_db), user: User = Depends(require("dashboards.view"))):
    data = svc.monthly_insights(db, month=month or None)
    return render(request, "dashboards/monthly_insights.html", {"user": user, "d": data, "month": data["month"]})
