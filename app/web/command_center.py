"""Module 1 - CEO / Executive Command Center: institutional health, executive KPIs, AI insights and anomalies."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import parse_date, redirect
from app.database import get_db
from app.models.core import Department, RiskAlert, User
from app.models.ops import Decision, DepartmentScorecard, Task, TrajectoryMeeting
from app.models.scheduling import ClassSession
from app.services import insights as insight_svc
from app.services import kpi as kpi_svc
from app.services import reports as report_svc

router = APIRouter(prefix="/command-center", dependencies=[Depends(csrf_protect)])


def current_period(period: str, start: str, end: str) -> kpi_svc.Period:
    return kpi_svc.resolve_period(period or "this_month", parse_date(start), parse_date(end))


def _today_status(db: Session) -> dict:
    rows = dict(db.query(ClassSession.status, func.count(ClassSession.id))
                .filter(ClassSession.date == date.today()).group_by(ClassSession.status).all())
    order = ["pending", "started", "done", "missed", "absent", "leave", "cancelled", "rescheduled"]
    labels = [s.title() for s in order if rows.get(s)]
    data = [rows[s] for s in order if rows.get(s)]
    return {"labels": labels, "data": data, "total": sum(rows.values()), "rows": rows}


def _scorecards(db: Session, period: str) -> list[dict]:
    out = []
    for dept in db.query(Department).filter(Department.is_active.is_(True)).order_by(Department.name):
        m = kpi_svc.department_metrics(db, dept, period, live=False)
        sc = db.query(DepartmentScorecard).filter(DepartmentScorecard.department_id == dept.id,
                                                  DepartmentScorecard.period == period).first()
        m["submission"] = sc
        out.append(m)
    return out


def _tiles(m: dict, cur: str = "PKR") -> list[dict]:
    d = m.get("deltas") or {}

    def fmt_money(v):
        return f"{cur} {v:,.0f}" if v is not None else "-"

    def fmt_pct(v):
        return f"{v}%" if v is not None else "-"

    tiles = [
        {"label": "Revenue", "value": fmt_money(m["revenue"]), "icon": "banknote", "color": "emerald",
         "delta": d.get("revenue"), "hint": "collected in period", "href": "/finance/payments"},
        {"label": "MRR", "value": fmt_money(m["mrr"]), "icon": "repeat", "color": "teal",
         "hint": f"{m['active_subscriptions']} active subscriptions", "href": "/finance/subscriptions"},
        {"label": "Active students", "value": f"{m['active_students']:,}", "icon": "graduation-cap", "color": "sky",
         "delta": d.get("active_students"), "hint": f"+{m['new_students']} joined", "href": "/students?status=active"},
        {"label": "New leads", "value": f"{m['new_leads']:,}", "icon": "funnel", "color": "indigo",
         "delta": d.get("new_leads"), "hint": f"{m['won_leads']} won", "href": "/crm/leads"},
        {"label": "Lead conversion", "value": fmt_pct(m["lead_conversion"]), "icon": "target", "color": "violet",
         "hint": f"trial {m['trial_conversion']}%", "href": "/crm/marketing"},
        {"label": "Churn", "value": fmt_pct(m["churn_rate"]), "icon": "user-minus", "color": "rose",
         "hint": f"{m['churned']} cancelled", "href": "/retention"},
        {"label": "Class completion", "value": fmt_pct(m["class_completion"]), "icon": "check-circle-2", "color": "emerald",
         "hint": f"{m['sessions_done']:,} of {m['sessions_total']:,}", "href": "/classes"},
        {"label": "Missed rate", "value": fmt_pct(m["missed_rate"]), "icon": "user-x", "color": "rose",
         "hint": f"{m['sessions_missed']} missed", "href": "/classes?status=missed"},
        {"label": "Teacher utilisation", "value": fmt_pct(m["teacher_utilization"]), "icon": "gauge", "color": "orange",
         "hint": f"{m['active_teachers']} active teachers", "href": "/teachers"},
        {"label": "QA average", "value": f"{m['qa_avg']}" if m["qa_avg"] is not None else "-", "icon": "shield-check",
         "color": "indigo", "hint": f"{m['qa_reviews']} reviews", "href": "/qa"},
        {"label": "AI average", "value": f"{m['ai_avg']}" if m["ai_avg"] is not None else "-", "icon": "brain-circuit",
         "color": "violet", "hint": "class monitoring", "href": "/ai-monitoring"},
        {"label": "Student NPS", "value": f"{m['nps']}" if m["nps"] is not None else "-", "icon": "message-square-heart",
         "color": "emerald", "hint": f"{m['nps_responses']} responses", "href": "/feedback"},
        {"label": "Payroll ratio", "value": fmt_pct(m["payroll_ratio"]), "icon": "users", "color": "amber",
         "hint": fmt_money(m["payroll"]), "href": "/hr/payroll"},
        {"label": "Marketing ROI", "value": fmt_pct(m["roi"]), "icon": "trending-up", "color": "sky",
         "hint": f"CPL {m['cpl'] or '-'} / CAC {m['cac'] or '-'}", "href": "/crm/marketing"},
        {"label": "Receivables", "value": fmt_money(m["receivables"]), "icon": "hourglass", "color": "amber",
         "hint": f"{m['overdue_invoices']} overdue invoices", "href": "/finance/invoices?status=overdue"},
        {"label": "Collection rate", "value": fmt_pct(m["collection_rate"]), "icon": "percent", "color": "emerald",
         "hint": "invoiced vs collected", "href": "/finance/invoices"},
        {"label": "Open cases", "value": f"{m['open_cases']:,}", "icon": "life-buoy", "color": "rose",
         "delta": d.get("complaints"), "hint": f"{m['complaints']} complaints", "href": "/cases"},
        {"label": "SLA compliance", "value": fmt_pct(m["sla_compliance"]), "icon": "timer", "color": "sky",
         "hint": f"{m['sla_breached_open']} breached open", "href": "/cases"},
        {"label": "High-risk students", "value": f"{m['high_risk_students']:,}", "icon": "heart-pulse", "color": "rose",
         "hint": "churn risk", "href": "/retention"},
        {"label": "Referral share", "value": fmt_pct(m["referral_pct"]), "icon": "gift", "color": "violet",
         "hint": f"{m['referred_adds']} of {m['gross_adds']} gross adds", "href": "/crm/referrals"},
    ]
    for tile in tiles:
        tile.setdefault("delta", None)   # the template compares delta directly
    return tiles


def _context(db: Session, user: User, p: kpi_svc.Period) -> dict:
    m = kpi_svc.executive_metrics(db, p.start, p.end)
    health = kpi_svc.health_score(m)
    series = kpi_svc.monthly_series(db, 6)
    funnel = m["funnel"]
    today = date.today()
    upcoming = (db.query(TrajectoryMeeting).filter(TrajectoryMeeting.meeting_date >= today)
                .order_by(TrajectoryMeeting.meeting_date).first())
    last_meeting = (db.query(TrajectoryMeeting).filter(TrajectoryMeeting.meeting_date < today)
                    .order_by(TrajectoryMeeting.meeting_date.desc()).first())
    open_decisions = (db.query(Decision).filter(Decision.status.in_(["proposed", "decided"]))
                      .order_by(Decision.due_date.is_(None), Decision.due_date).limit(8).all())
    overdue_decisions = db.query(Decision).filter(Decision.status.in_(["proposed", "decided"]),
                                                  Decision.due_date < today).count()
    return {
        "period": p, "period_choices": kpi_svc.PERIOD_CHOICES, "m": m, "health": health, "series": series,
        "tiles": _tiles(m), "funnel_labels": list(funnel.keys()), "funnel_data": list(funnel.values()),
        "today_status": _today_status(db), "scorecards": _scorecards(db, p.month),
        "alerts": insight_svc.open_alerts(db, user, 10),
        "anomalies": insight_svc.compute_anomalies(db, p),
        "meeting": upcoming, "last_meeting": last_meeting, "open_decisions": open_decisions,
        "overdue_decisions": overdue_decisions,
        "open_tasks": m["open_tasks"], "overdue_tasks": m["overdue_tasks"],
        "is_ceo": rbac.is_ceo(user),
    }


@router.get("", include_in_schema=False)
def command_center(request: Request, period: str = "this_month", start: str = "", end: str = "",
                   db: Session = Depends(get_db), user: User = Depends(require("command_center.view"))):
    p = current_period(period, start, end)
    ctx = _context(db, user, p)
    ctx["insights"] = insight_svc.executive_insights(db, p)
    ctx["user"] = user
    return render(request, "command_center/index.html", ctx)


@router.get("/partial/insights", include_in_schema=False)
def insights_partial(request: Request, period: str = "this_month", start: str = "", end: str = "",
                     db: Session = Depends(get_db), user: User = Depends(require("command_center.view"))):
    p = current_period(period, start, end)
    return render(request, "command_center/_insights.html",
                  {"user": user, "period": p, "insights": insight_svc.executive_insights(db, p)})


@router.post("/insights/regenerate", include_in_schema=False)
async def regenerate_insights(request: Request, db: Session = Depends(get_db),
                              user: User = Depends(require("command_center.view"))):
    form = await request.form()
    p = current_period(form.get("period", "this_month"), form.get("start"), form.get("end"))
    out = insight_svc.executive_insights(db, p, force=True, user=user)
    log_action(db, user, "execute", "command_center", entity=out.get("run"),
               description=f"Regenerated executive AI insights for {p.label}", request=request)
    db.commit()
    return redirect(f"/command-center?{p.qs}", "Executive insights regenerated.")


@router.post("/anomalies/scan", include_in_schema=False)
async def scan_anomalies(request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("command_center.view"))):
    form = await request.form()
    p = current_period(form.get("period", "this_month"), form.get("start"), form.get("end"))
    out = insight_svc.detect_anomalies(db, p)
    log_action(db, user, "execute", "command_center",
               description=f"Anomaly scan for {p.label}: {len(out['anomalies'])} found, {out['created']} alerts raised",
               request=request)
    db.commit()
    return redirect(f"/command-center?{p.qs}",
                    f"Anomaly scan complete - {len(out['anomalies'])} detected, {out['created']} new alert(s).")


@router.post("/alerts/{alert_id}/ack", include_in_schema=False)
async def ack_alert(alert_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("command_center.view"))):
    alert = db.query(RiskAlert).filter(RiskAlert.id == alert_id).first()
    if not alert:
        return redirect("/command-center", "Alert not found.", "error")
    if alert.visibility == "ceo_only" and not rbac.is_ceo(user):
        return redirect("/command-center", "You cannot access this alert.", "error")
    form = await request.form()
    alert.status = "resolved" if form.get("resolve") else "acknowledged"
    alert.acknowledged_by_id = user.id
    if alert.status == "resolved":
        alert.resolved_at = datetime.utcnow()
    log_action(db, user, "update", "command_center", entity=alert, description=f"Alert {alert.status}", request=request)
    db.commit()
    return redirect("/command-center", f"Alert {alert.status}.")


@router.get("/export", include_in_schema=False)
def export_executive(request: Request, period: str = "this_month", start: str = "", end: str = "",
                     db: Session = Depends(get_db), user: User = Depends(require("command_center.view"))):
    from fastapi.responses import FileResponse
    p = current_period(period, start, end)
    m = kpi_svc.executive_metrics(db, p.start, p.end)
    health = kpi_svc.health_score(m)
    rows = [["Institutional Health Score", health["score"], health["band"], "0-100 composite"]]
    for k, v in health["subs"].items():
        rows.append([f"  {k.title()} sub-score", v if v is not None else "-", health["sub_bands"][k][0],
                     "; ".join(health["notes"][k])])
    labels = [
        ("Revenue (base)", "revenue"), ("Expenses (base)", "expenses"), ("Payroll (base)", "payroll"), ("Net (base)", "net"),
        ("MRR (base)", "mrr"), ("Active students", "active_students"), ("New students", "new_students"),
        ("Churn %", "churn_rate"), ("New leads", "new_leads"), ("Lead conversion %", "lead_conversion"),
        ("Trials", "trials"), ("Trial conversion %", "trial_conversion"), ("Sessions done", "sessions_done"),
        ("Class completion %", "class_completion"), ("Missed rate %", "missed_rate"), ("Punctuality %", "punctuality"),
        ("Teacher utilisation %", "teacher_utilization"), ("QA average", "qa_avg"), ("AI average", "ai_avg"),
        ("Student NPS", "nps"), ("Staff eNPS", "enps"), ("Payroll ratio %", "payroll_ratio"),
        ("Marketing spend", "marketing_spend"), ("CPL", "cpl"), ("CAC", "cac"), ("Marketing ROI %", "roi"),
        ("Receivables", "receivables"), ("Collection rate %", "collection_rate"), ("Open cases", "open_cases"),
        ("Complaints", "complaints"), ("SLA compliance %", "sla_compliance"), ("High-risk students", "high_risk_students"),
        ("Referral share %", "referral_pct"), ("HR attendance %", "hr_attendance"), ("Open tasks", "open_tasks"),
        ("Overdue tasks", "overdue_tasks"),
    ]
    prev = m.get("previous") or {}
    for label, key in labels:
        rows.append([label, m.get(key) if m.get(key) is not None else "-", prev.get(key) if prev.get(key) is not None else "-",
                     (m.get("deltas") or {}).get(key, "")])
    headers = ["Metric", "Value", "Previous period", "Change %"]
    name = f"executive-report-{p.key}"
    path = report_svc.export_xlsx(headers, rows, name, sheet="Executive", totals=None)
    report_svc.log_run(db, user, "executive", f"Executive report - {p.label}", {"period": p.key}, "xlsx", path, len(rows))
    log_action(db, user, "export", "command_center", description=f"Executive report XLSX for {p.label}", request=request)
    db.commit()
    return FileResponse(path, filename=f"{name}.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
