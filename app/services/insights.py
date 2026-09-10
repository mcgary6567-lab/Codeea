"""AI executive insights and anomaly detection for the CEO Command Center (Module 1 + Module 38).

* ``executive_insights(db, period)`` -> list of insight dicts produced through ``ai_gateway`` (module ``insights``)
  so every insight carries model / prompt version, confidence and human-review status.
* ``detect_anomalies(db, period)`` -> computed anomalies (revenue drop, missed-rate spike, complaint spike ...)
  which are materialised as ``RiskAlert`` rows, idempotently, at most once per day per anomaly key.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.core import AIModelRun, RiskAlert, User
from app.services import kpi as kpi_svc
from app.services.ai_gateway import ai

# insight type -> (badge colour, lucide icon, label)
INSIGHT_TYPES = {
    "risk": ("rose", "triangle-alert", "Risk"),
    "finance": ("amber", "wallet", "Finance"),
    "growth": ("sky", "trending-up", "Growth"),
    "retention": ("violet", "heart-pulse", "Retention"),
    "quality": ("indigo", "shield-check", "Quality"),
    "people": ("emerald", "users", "People"),
    "ok": ("emerald", "check-circle-2", "All clear"),
    "opportunity": ("teal", "sparkles", "Opportunity"),
}


def insight_style(kind: str) -> dict:
    color, icon, label = INSIGHT_TYPES.get(kind, ("slate", "lightbulb", kind.replace("_", " ").title()))
    return {"color": color, "icon": icon, "label": label}


def _payload(db: Session, period) -> dict:
    m = kpi_svc.executive_metrics(db, period.start, period.end)
    health = kpi_svc.health_score(m)
    rec = {
        "period": period.key, "period_label": period.label,
        "revenue": m["revenue"], "expenses": m["expenses"], "net": m["net"], "mrr": m["mrr"],
        "active_students": m["active_students"], "new_students": m["new_students"], "churn_rate": m["churn_rate"],
        "new_leads": m["new_leads"], "lead_conversion": m["lead_conversion"], "trial_conversion": m["trial_conversion"],
        "class_completion": m["class_completion"], "missed_rate": m["missed_rate"], "teacher_utilization": m["teacher_utilization"],
        "qa_avg": m["qa_avg"], "ai_avg": m["ai_avg"], "nps": m["nps"], "collection_rate": m["collection_rate"] or 100,
        "overdue_count": m["overdue_invoices"], "payroll_ratio": m["payroll_ratio"], "roi": m["roi"], "cpl": m["cpl"],
        "high_risk_students": m["high_risk_students"], "open_cases": m["open_cases"], "complaints": m["complaints"],
        "sla_compliance": m["sla_compliance"], "referral_pct": m["referral_pct"], "health_score": health["score"],
    }
    return {"metrics": {k: v for k, v in rec.items() if v is not None}, "period": period.key, "entity_type": "Period"}


def latest_run(db: Session, period_key: str) -> Optional[AIModelRun]:
    """Most recent stored insights run for this period (matched on the stored payload)."""
    for run in db.query(AIModelRun).filter(AIModelRun.module == "insights").order_by(AIModelRun.created_at.desc()).limit(40):
        if f'"period": "{period_key}"' in (run.input_summary or ""):
            return run
    return None


def _items_from_run(run: AIModelRun) -> list[dict]:
    out = (run.output or {}) if run else {}
    items = out.get("insights") or []
    result = []
    for i, it in enumerate(items):
        kind = (it.get("type") or "ok") if isinstance(it, dict) else "ok"
        text = (it.get("text") or "") if isinstance(it, dict) else str(it)
        result.append({"type": kind, "text": text, "index": i, **insight_style(kind)})
    return result


def executive_insights(db: Session, period, force: bool = False, user: Optional[User] = None) -> dict:
    """Return ``{"items": [...], "run": AIModelRun|None, ...}`` for the CEO insights panel.

    Reuses the most recent stored run for the period unless ``force`` is set (the "Regenerate" button)."""
    run = None if force else latest_run(db, period.key)
    if run is None:
        try:
            _result, run = ai(db, module="insights", task="executive_summary", payload=_payload(db, period))
        except Exception:
            run = None
    items = _items_from_run(run) if run else []
    if not items:
        items = [{"type": "ok", "text": "Not enough data in this period to generate executive insights yet.", "index": 0,
                  **insight_style("ok")}]
    return {
        "items": items, "run": run,
        "model": getattr(run, "model", None), "provider": getattr(run, "provider", None),
        "model_version": getattr(run, "model_version", None), "prompt_version": getattr(run, "prompt_version", None),
        "confidence": getattr(run, "confidence", None), "review_status": getattr(run, "review_status", None),
        "generated_at": getattr(run, "created_at", None),
    }


# ------------------------------------------------------------------------------------------------- anomalies

def _existing_today(db: Session, alert_type: str) -> Optional[RiskAlert]:
    start = datetime.combine(date.today(), datetime.min.time())
    return db.query(RiskAlert).filter(RiskAlert.alert_type == alert_type, RiskAlert.created_at >= start).first()


def compute_anomalies(db: Session, period) -> list[dict]:
    """Pure computation - no writes. Each anomaly: key, title, message, severity, visibility."""
    m = kpi_svc.executive_metrics(db, period.start, period.end)
    prev = m.get("previous") or {}
    out: list[dict] = []

    d_rev = (m.get("deltas") or {}).get("revenue")
    if d_rev is not None and d_rev <= -20:
        out.append({"key": "revenue_drop", "title": f"Revenue down {abs(d_rev):.1f}% vs the previous period",
                    "message": f"Collected revenue fell from {prev.get('revenue', 0):,.0f} to {m['revenue']:,.0f} (base currency). "
                               "Check billing follow-up, cancellations and failed payments.",
                    "severity": "critical", "visibility": "ceo_only", "metric": m["revenue"]})

    cur_missed, prev_missed = m.get("missed_rate") or 0, prev.get("missed_rate") or 0
    if cur_missed >= 6 and (prev_missed == 0 or cur_missed >= prev_missed * 1.4):
        out.append({"key": "missed_rate_spike", "title": f"Missed-class rate spiked to {cur_missed}%",
                    "message": f"{m['sessions_missed']} missed classes out of {m['sessions_total']} terminal sessions "
                               f"(previous period {prev_missed}%). Supervisor coverage needs review.",
                    "severity": "high", "visibility": "management", "metric": cur_missed})

    cur_c, prev_c = m.get("complaints") or 0, prev.get("complaints") or 0
    if cur_c >= 3 and cur_c >= max(prev_c * 1.5, prev_c + 2):
        out.append({"key": "complaint_spike", "title": f"Complaint volume spiked to {cur_c} cases",
                    "message": f"{cur_c} complaints raised this period versus {prev_c} in the previous one. "
                               "QA and Operations should review the root causes.",
                    "severity": "high", "visibility": "management", "metric": cur_c})

    cr = m.get("collection_rate")
    if cr is not None and cr < 70:
        out.append({"key": "collection_low", "title": f"Collection rate down to {cr}%",
                    "message": f"{m['overdue_invoices']} overdue invoices and {m['receivables']:,.0f} outstanding receivables.",
                    "severity": "high", "visibility": "management", "metric": cr})

    if m.get("churn_base") and (m.get("churn_rate") or 0) >= 6:
        out.append({"key": "churn_spike", "title": f"Churn at {m['churn_rate']}% this period",
                    "message": f"{m['churned']} students cancelled out of a base of {m['churn_base']}. Retention play required.",
                    "severity": "high", "visibility": "management", "metric": m["churn_rate"]})

    pr = m.get("payroll_ratio")
    if pr is not None and pr > 60:
        out.append({"key": "payroll_ratio_high", "title": f"Payroll is {pr}% of revenue",
                    "message": "Payroll cost ratio is above the 40% guideline; review teacher utilisation and pricing.",
                    "severity": "medium", "visibility": "ceo_only", "metric": pr})

    if m.get("qa_avg") is not None and m["qa_avg"] < 70:
        out.append({"key": "qa_low", "title": f"QA average dropped to {m['qa_avg']}",
                    "message": "Average QA review score is below the 80 target. Assign Ustaadh Lab training for grade-C teachers.",
                    "severity": "medium", "visibility": "management", "metric": m["qa_avg"]})

    if m.get("active_students") and m.get("high_risk_students", 0) > max(5, 0.1 * m["active_students"]):
        out.append({"key": "risk_students", "title": f"{m['high_risk_students']} students at high churn risk",
                    "message": "More than 10% of the active roster is flagged high risk. Supervisors must call this week.",
                    "severity": "high", "visibility": "management", "metric": m["high_risk_students"]})

    return out


def detect_anomalies(db: Session, period=None, create_alerts: bool = True) -> dict:
    """Compute anomalies and materialise them as RiskAlerts (at most once per day per anomaly key)."""
    period = period or kpi_svc.resolve_period("this_month")
    found = compute_anomalies(db, period)
    created = []
    if create_alerts:
        for a in found:
            alert_type = f"anomaly_{a['key']}"
            if _existing_today(db, alert_type):
                continue
            alert = RiskAlert(alert_type=alert_type, severity=a["severity"], title=a["title"], message=a["message"],
                              entity_type="Period", visibility=a["visibility"], status="open", source="system")
            db.add(alert)
            created.append(alert)
        db.flush()
    return {"period": period.key, "anomalies": found, "created": len(created)}


def open_alerts(db: Session, user: User, limit: int = 12) -> list[RiskAlert]:
    from app.core import rbac
    q = db.query(RiskAlert).filter(RiskAlert.status.in_(["open", "acknowledged"]))
    if not rbac.is_ceo(user):
        q = q.filter(RiskAlert.visibility != "ceo_only")
    if not rbac.is_management(user):
        q = q.filter(RiskAlert.visibility == "ops")
    return q.order_by(RiskAlert.created_at.desc()).limit(limit).all()
