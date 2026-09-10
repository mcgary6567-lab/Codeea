"""Module 38 - AI governance.

Every AI output in the platform is written to ``AIModelRun`` with its model, prompt version, confidence,
tokens, cost and latency. This module is the human layer on top: a reviewable log, a pending-review queue,
usage and cost reporting, per-module confidence thresholds, and the model registry.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import paginate, parse_date, parse_float, parse_int, redirect
from app.database import get_db
from app.models.core import AIModelRun, Integration, Setting, User
from app.services import ai_gateway

router = APIRouter(prefix="/ai-governance", dependencies=[Depends(csrf_protect)])

REVIEW_STATUSES = [("pending", "Pending review"), ("approved", "Approved"), ("rejected", "Rejected"),
                   ("overridden", "Overridden"), ("false_positive", "False positive")]
REVIEW_ACTIONS = [("approved", "Approve", "check-circle-2", "success"),
                  ("rejected", "Reject", "x-circle", "danger"),
                  ("overridden", "Override", "pencil", "warning"),
                  ("false_positive", "False positive", "flag-off", "secondary")]
DEFAULT_THRESHOLDS = {"class_monitoring": 0.80, "lead_scoring": 0.70, "churn": 0.75,
                      "complaint_classification": 0.80, "lesson_recommendation": 0.65, "insights": 0.70,
                      "sentiment": 0.70, "qa_recommendation": 0.80, "transcription": 0.60, "anomaly": 0.70}
MODULE_LABELS = {"class_monitoring": "Class monitoring", "lead_scoring": "Lead scoring", "churn": "Churn prediction",
                 "complaint_classification": "Complaint classification", "lesson_recommendation": "Lesson recommendation",
                 "insights": "Executive insights", "sentiment": "Sentiment analysis",
                 "qa_recommendation": "QA recommendation", "transcription": "Transcription", "anomaly": "Anomaly detection"}

TABS = [("runs", "Run log", "/ai-governance"), ("queue", "Review queue", "/ai-governance/queue"),
        ("dashboard", "Usage & cost", "/ai-governance/dashboard"),
        ("policy", "Policy", "/ai-governance/policy"), ("registry", "Model registry", "/ai-governance/registry")]


def _thresholds(db: Session) -> dict:
    row = db.query(Setting).filter(Setting.key == "ai_thresholds").first()
    stored = row.value if row and isinstance(row.value, dict) else {}
    out = dict(DEFAULT_THRESHOLDS)
    for k, v in stored.items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _modules(db: Session) -> list[str]:
    return sorted({r[0] for r in db.query(AIModelRun.module).distinct()} | set(DEFAULT_THRESHOLDS))


def _month(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


# ================================================================================================ run log
@router.get("", include_in_schema=False)
def runs(request: Request, page: int = 1, module: str = "", provider: str = "", model: str = "",
         review_status: str = "", entity_type: str = "", entity_id: int | None = None,
         min_confidence: str = "", max_confidence: str = "", start: str = "", end: str = "",
         db: Session = Depends(get_db), user: User = Depends(require("ai_governance.view"))):
    q = db.query(AIModelRun)
    if module:
        q = q.filter(AIModelRun.module == module)
    if provider:
        q = q.filter(AIModelRun.provider == provider)
    if model:
        q = q.filter(AIModelRun.model == model)
    if review_status:
        q = q.filter(AIModelRun.review_status == review_status)
    if entity_type:
        q = q.filter(AIModelRun.entity_type == entity_type)
    if entity_id:
        q = q.filter(AIModelRun.entity_id == entity_id)
    lo, hi = parse_float(min_confidence, None) if min_confidence else None, parse_float(max_confidence, None) if max_confidence else None
    if lo is not None:
        q = q.filter(AIModelRun.confidence >= lo)
    if hi is not None:
        q = q.filter(AIModelRun.confidence <= hi)
    d_start, d_end = parse_date(start), parse_date(end)
    if d_start:
        q = q.filter(AIModelRun.created_at >= datetime.combine(d_start, datetime.min.time()))
    if d_end:
        q = q.filter(AIModelRun.created_at <= datetime.combine(d_end, datetime.max.time()))
    pg = paginate(q.order_by(AIModelRun.created_at.desc(), AIModelRun.id.desc()), page, 30)
    status_counts = dict(db.query(AIModelRun.review_status, func.count(AIModelRun.id))
                         .group_by(AIModelRun.review_status).all())
    total = db.query(AIModelRun).count()
    avg_conf = db.query(func.avg(AIModelRun.confidence)).scalar()
    thresholds = _thresholds(db)
    qs = (f"module={module}&provider={provider}&model={model}&review_status={review_status}"
          f"&entity_type={entity_type}&entity_id={entity_id or ''}&min_confidence={min_confidence}"
          f"&max_confidence={max_confidence}&start={start}&end={end}")
    return render(request, "ai_governance/runs.html", {
        "user": user, "tab": "runs", "tabs": TABS, "page": pg, "module": module, "provider": provider,
        "model": model, "review_status": review_status, "entity_type": entity_type, "entity_id": entity_id,
        "min_confidence": min_confidence, "max_confidence": max_confidence, "start": start, "end": end,
        "qs": qs, "modules": _modules(db), "thresholds": thresholds,
        "providers": sorted({r[0] for r in db.query(AIModelRun.provider).distinct() if r[0]}),
        "models": sorted({r[0] for r in db.query(AIModelRun.model).distinct() if r[0]}),
        "entity_types": sorted({r[0] for r in db.query(AIModelRun.entity_type).distinct() if r[0]}),
        "statuses": REVIEW_STATUSES, "status_counts": status_counts, "total": total,
        "avg_confidence": round(float(avg_conf), 2) if avg_conf is not None else None,
        "pending": status_counts.get("pending", 0), "labels": MODULE_LABELS})


# ================================================================================================ review queue
@router.get("/queue", include_in_schema=False)
def queue(request: Request, module: str = "", db: Session = Depends(get_db),
          user: User = Depends(require("ai_governance.view"))):
    q = db.query(AIModelRun).filter(AIModelRun.review_status == "pending")
    if module:
        q = q.filter(AIModelRun.module == module)
    items = q.order_by(AIModelRun.confidence.asc(), AIModelRun.created_at.desc()).limit(100).all()
    thresholds = _thresholds(db)
    rows = []
    for r in items:
        thr = thresholds.get(r.module, 0.7)
        rows.append({"r": r, "threshold": thr,
                     "below": r.confidence is not None and r.confidence < thr,
                     "age_days": (datetime.utcnow() - r.created_at).days})
    by_module = dict(db.query(AIModelRun.module, func.count(AIModelRun.id))
                     .filter(AIModelRun.review_status == "pending").group_by(AIModelRun.module).all())
    return render(request, "ai_governance/queue.html", {
        "user": user, "tab": "queue", "tabs": TABS, "rows": rows, "module": module, "modules": _modules(db),
        "by_module": by_module, "total": sum(by_module.values()), "labels": MODULE_LABELS,
        "below_threshold": len([r for r in rows if r["below"]]),
        "oldest": max([r["age_days"] for r in rows], default=0),
        "actions": REVIEW_ACTIONS, "can_review": rbac.has_permission(user, "ai_governance.update")})


# ================================================================================================ usage & cost
@router.get("/dashboard", include_in_schema=False)
def dashboard(request: Request, months: int = 6, db: Session = Depends(get_db),
              user: User = Depends(require("ai_governance.view"))):
    from app.services import kpi as kpi_svc
    months = max(1, min(24, months or 6))
    month_keys = kpi_svc.last_n_periods(months)
    since = kpi_svc.period_bounds(month_keys[0])[0]
    runs_all = (db.query(AIModelRun).filter(AIModelRun.created_at >= datetime.combine(since, datetime.min.time()))
                .all())
    by_module: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    monthly_cost = {k: 0.0 for k in month_keys}
    monthly_runs = {k: 0 for k in month_keys}
    for r in runs_all:
        mk = _month(r.created_at)
        cost = float(r.cost_usd or 0)
        m = by_module.setdefault(r.module, {"module": r.module, "runs": 0, "cost": 0.0, "tokens": 0,
                                            "latency": 0, "false_positive": 0, "reviewed": 0,
                                            "months": {k: 0 for k in month_keys}})
        m["runs"] += 1
        m["cost"] += cost
        m["tokens"] += (r.tokens_in or 0) + (r.tokens_out or 0)
        m["latency"] += r.latency_ms or 0
        if r.review_status == "false_positive":
            m["false_positive"] += 1
        if r.review_status != "pending":
            m["reviewed"] += 1
        if mk in m["months"]:
            m["months"][mk] += 1
        md = by_model.setdefault(r.model or "unknown", {"model": r.model or "unknown", "provider": r.provider,
                                                        "runs": 0, "cost": 0.0, "tokens": 0, "latency": 0})
        md["runs"] += 1
        md["cost"] += cost
        md["tokens"] += (r.tokens_in or 0) + (r.tokens_out or 0)
        md["latency"] += r.latency_ms or 0
        if mk in monthly_cost:
            monthly_cost[mk] += cost
            monthly_runs[mk] += 1
    for m in by_module.values():
        m["avg_latency"] = round(m["latency"] / m["runs"]) if m["runs"] else 0
        m["fp_rate"] = round(100 * m["false_positive"] / m["reviewed"], 1) if m["reviewed"] else 0.0
        m["cost"] = round(m["cost"], 4)
    for m in by_model.values():
        m["avg_latency"] = round(m["latency"] / m["runs"]) if m["runs"] else 0
        m["cost"] = round(m["cost"], 4)
    modules_sorted = sorted(by_module.values(), key=lambda x: x["runs"], reverse=True)
    return render(request, "ai_governance/dashboard.html", {
        "user": user, "tab": "dashboard", "tabs": TABS, "months": months, "month_keys": month_keys,
        "month_labels": [datetime.strptime(k, "%Y-%m").strftime("%b %y") for k in month_keys],
        "by_module": modules_sorted, "by_model": sorted(by_model.values(), key=lambda x: x["runs"], reverse=True),
        "monthly_cost": [round(monthly_cost[k], 4) for k in month_keys],
        "monthly_runs": [monthly_runs[k] for k in month_keys],
        "total_runs": len(runs_all), "labels": MODULE_LABELS,
        "total_cost": round(sum(float(r.cost_usd or 0) for r in runs_all), 4),
        "total_tokens": sum((r.tokens_in or 0) + (r.tokens_out or 0) for r in runs_all),
        "avg_latency": round(sum(r.latency_ms or 0 for r in runs_all) / len(runs_all)) if runs_all else 0,
        "fp_total": len([r for r in runs_all if r.review_status == "false_positive"]),
        "module_series": [{"label": MODULE_LABELS.get(m["module"], m["module"]),
                           "data": [m["months"][k] for k in month_keys]} for m in modules_sorted[:6]]})


# ================================================================================================ policy
@router.get("/policy", include_in_schema=False)
def policy(request: Request, db: Session = Depends(get_db), user: User = Depends(require("ai_governance.view"))):
    thresholds = _thresholds(db)
    stats = {}
    for module in thresholds:
        total = db.query(AIModelRun).filter(AIModelRun.module == module).count()
        below = db.query(AIModelRun).filter(AIModelRun.module == module,
                                            AIModelRun.confidence < thresholds[module]).count()
        reviewed = db.query(AIModelRun).filter(AIModelRun.module == module,
                                               AIModelRun.review_status != "pending").count()
        fp = db.query(AIModelRun).filter(AIModelRun.module == module,
                                         AIModelRun.review_status == "false_positive").count()
        stats[module] = {"total": total, "below": below, "reviewed": reviewed, "false_positive": fp,
                         "below_pct": round(100 * below / total, 1) if total else 0.0,
                         "fp_rate": round(100 * fp / reviewed, 1) if reviewed else 0.0}
    return render(request, "ai_governance/policy.html", {
        "user": user, "tab": "policy", "tabs": TABS, "thresholds": thresholds, "stats": stats,
        "labels": MODULE_LABELS, "defaults": DEFAULT_THRESHOLDS,
        "can_configure": rbac.has_permission(user, "ai_governance.configure")
        or rbac.has_permission(user, "ai_governance.update")})


@router.post("/policy", include_in_schema=False)
async def save_policy(request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require("ai_governance.update"))):
    f = await request.form()
    before = _thresholds(db)
    updated = {}
    for module in before:
        raw = f.get(f"threshold_{module}")
        if raw in (None, ""):
            updated[module] = before[module]
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = before[module]
        updated[module] = round(max(0.0, min(1.0, v)), 2)
    row = db.query(Setting).filter(Setting.key == "ai_thresholds").first()
    if not row:
        row = Setting(key="ai_thresholds", group="ai",
                      description="Minimum confidence per AI module before an output may be acted on without review")
        db.add(row)
    row.value = updated
    log_action(db, user, "configure", "ai_governance",
               description="AI confidence thresholds updated", before=before, after=updated,
               rationale=f.get("rationale") or f.get("reason"), request=request)
    db.commit()
    return redirect("/ai-governance/policy", "Confidence thresholds saved.")


# ================================================================================================ model registry
@router.get("/registry", include_in_schema=False)
def registry(request: Request, db: Session = Depends(get_db), user: User = Depends(require("ai_governance.view"))):
    integration = db.query(Integration).filter(Integration.provider == "ai").first()
    thresholds = _thresholds(db)
    rows = []
    for module, prompt_version in sorted(ai_gateway.PROMPT_VERSIONS.items()):
        q = db.query(AIModelRun).filter(AIModelRun.module == module)
        total = q.count()
        last = q.order_by(AIModelRun.created_at.desc()).first()
        models = sorted({r[0] for r in q.with_entities(AIModelRun.model).distinct() if r[0]})
        avg_conf = q.with_entities(func.avg(AIModelRun.confidence)).scalar()
        rows.append({"module": module, "label": MODULE_LABELS.get(module, module.replace("_", " ").title()),
                     "prompt_version": prompt_version, "runs": total, "last": last,
                     "models": models, "threshold": thresholds.get(module),
                     "avg_confidence": round(float(avg_conf), 2) if avg_conf is not None else None,
                     "model_version": last.model_version if last else "1.0",
                     "provider": last.provider if last else "simulated"})
    return render(request, "ai_governance/registry.html", {
        "user": user, "tab": "registry", "tabs": TABS, "rows": rows, "integration": integration,
        "total_runs": db.query(AIModelRun).count(),
        "providers": sorted({r["provider"] for r in rows}),
        "prompt_versions": ai_gateway.PROMPT_VERSIONS})


# ================================================================================================ detail + review
@router.get("/{run_id}", include_in_schema=False)
def detail(run_id: int, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require("ai_governance.view"))):
    r = db.query(AIModelRun).filter(AIModelRun.id == run_id).first()
    if not r:
        return redirect("/ai-governance", "AI run not found.", "error")
    thresholds = _thresholds(db)
    thr = thresholds.get(r.module, 0.7)
    try:
        pretty = json.dumps(r.output or {}, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        pretty = str(r.output)
    try:
        payload = json.dumps(json.loads(r.input_summary or "{}"), indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        payload = r.input_summary or ""
    siblings = (db.query(AIModelRun).filter(AIModelRun.entity_type == r.entity_type,
                                            AIModelRun.entity_id == r.entity_id, AIModelRun.id != r.id)
                .order_by(AIModelRun.created_at.desc()).limit(8).all()) if r.entity_type and r.entity_id else []
    reviewer = db.query(User).filter(User.id == r.reviewed_by_id).first() if r.reviewed_by_id else None
    return render(request, "ai_governance/detail.html", {
        "user": user, "tab": "runs", "tabs": TABS, "r": r, "pretty": pretty, "payload": payload,
        "threshold": thr, "below": r.confidence is not None and r.confidence < thr, "siblings": siblings,
        "reviewer": reviewer, "actions": REVIEW_ACTIONS, "labels": MODULE_LABELS,
        "cost": float(r.cost_usd or 0), "tokens": (r.tokens_in or 0) + (r.tokens_out or 0),
        "can_review": rbac.has_permission(user, "ai_governance.update"),
        "entity_link": _entity_link(r)})


def _entity_link(r: AIModelRun) -> str | None:
    mapping = {"ClassSession": "/classes/{id}", "Lead": "/crm/leads/{id}", "Student": "/students/{id}",
               "Case": "/cases/{id}", "Teacher": "/teachers/{id}", "Feedback": "/feedback"}
    tpl = mapping.get(r.entity_type or "")
    if tpl and r.entity_id:
        return tpl.replace("{id}", str(r.entity_id))
    return None


@router.post("/{run_id}/review", include_in_schema=False)
async def review(run_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require("ai_governance.update"))):
    r = db.query(AIModelRun).filter(AIModelRun.id == run_id).first()
    if not r:
        return redirect("/ai-governance", "AI run not found.", "error")
    f = await request.form()
    status = f.get("status") or ""
    valid = {s for s, _l, _i, _v in REVIEW_ACTIONS}
    if status not in valid:
        return redirect(f"/ai-governance/{r.id}", "Unknown review action.", "error")
    note = (f.get("note") or f.get("rationale") or "").strip() or None
    if status in ("rejected", "overridden") and not note:
        return redirect(f"/ai-governance/{r.id}",
                        "Rejecting or overriding an AI output requires a written reason.", "error")
    before = {"review_status": r.review_status, "review_note": r.review_note}
    ai_gateway.review_run(db, r, user, status, note)
    log_action(db, user, "approve" if status == "approved" else "override", "ai_governance", entity=r,
               description=f"AI run #{r.id} ({r.module}) marked {status}", rationale=note, before=before,
               after={"review_status": r.review_status, "review_note": r.review_note},
               severity="warning" if status in ("rejected", "overridden") else "info", request=request)
    db.commit()
    back = f.get("back") or f"/ai-governance/{r.id}"
    return redirect(back, f"AI run marked {status.replace('_', ' ')}.")
