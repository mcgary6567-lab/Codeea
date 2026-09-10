"""Provider-agnostic AI gateway (Section 9 / Module 38).

Every call records an ``AIModelRun`` (model, version, prompt version, confidence, cost, review status) so that
outputs are auditable and human-reviewable. When ``AI_PROVIDER=simulated`` (default on localhost) the gateway
returns deterministic heuristic results so all workflows function end-to-end without external API keys.

Usage:
    from app.services.ai_gateway import ai
    result, run = ai(db, module="lead_scoring", task="score_lead", payload={...}, entity=lead)
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.core import AIModelRun

PROMPT_VERSIONS = {
    "class_monitoring": "2.1", "lead_scoring": "1.3", "churn": "1.2", "complaint_classification": "1.1",
    "lesson_recommendation": "1.4", "insights": "1.0", "qa_recommendation": "1.1", "transcription": "1.0",
    "anomaly": "1.0", "sentiment": "1.0",
}


def _seed(payload: dict) -> int:
    return int(hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:8], 16)


def _rng(payload: dict, lo: float, hi: float, salt: str = "") -> float:
    s = _seed({**payload, "_salt": salt})
    return lo + (s % 10_000) / 10_000 * (hi - lo)


# ----------------------------------------------------------------------------- simulated task handlers
def _sim_class_monitoring(p: dict) -> dict:
    late = int(_rng(p, 0, 9, "late"))
    duration = int(p.get("actual_duration") or p.get("duration") or 30)
    planned = int(p.get("duration") or 30)
    camera = round(_rng(p, 72, 100, "cam"), 1)
    teaching = round(_rng(p, 60, 96, "teach"), 1)
    engagement = round(_rng(p, 55, 95, "eng"), 1)
    coverage = round(_rng(p, 50, 100, "cov"), 1)
    tone_flags = [] if _rng(p, 0, 1, "tone") > 0.12 else ["raised_voice"]
    conduct_flags = [] if _rng(p, 0, 1, "conduct") > 0.06 else ["off_topic_conversation"]
    contact_exchange = _rng(p, 0, 1, "poach") < 0.03
    overall = round((camera * 0.15 + teaching * 0.3 + engagement * 0.25 + coverage * 0.2 + max(0, 100 - late * 8) * 0.1), 1)
    risk = "high" if (overall < 60 or contact_exchange or conduct_flags) else ("medium" if overall < 75 or tone_flags else "low")
    return {
        "camera_presence_pct": camera, "punctuality_minutes": late, "duration_compliance_pct": round(min(100, duration / planned * 100), 1),
        "active_teaching_pct": teaching, "idle_pct": round(100 - teaching, 1), "student_engagement_score": engagement,
        "curriculum_coverage_pct": coverage, "tone_flags": tone_flags, "conduct_flags": conduct_flags,
        "contact_exchange_detected": contact_exchange, "overall_score": overall, "risk_level": risk,
        "summary": f"Teacher was {'on time' if late <= 2 else f'{late} min late'}; camera on {camera:.0f}% of the class; "
                   f"{teaching:.0f}% active teaching with {engagement:.0f}% student engagement. Curriculum coverage {coverage:.0f}%.",
        "recommended_feedback": ("Excellent session — keep the pacing." if overall >= 85 else
                                 "Increase student recitation time and confirm the planned lesson is covered fully." if overall >= 70 else
                                 "Punctuality and engagement need attention. Supervisor follow-up recommended."),
    }


def _sim_lead_scoring(p: dict) -> dict:
    score = 35
    factors = {}
    if p.get("country") in ("United Kingdom", "United States", "Canada", "Australia"):
        score += 20; factors["high_value_country"] = 20
    if p.get("source") in ("Referral", "WhatsApp"):
        score += 15; factors["warm_source"] = 15
    if p.get("has_whatsapp"):
        score += 8; factors["whatsapp_reachable"] = 8
    if p.get("students_count", 1) > 1:
        score += 10; factors["multiple_students"] = 10
    if p.get("preferred_time"):
        score += 5; factors["stated_availability"] = 5
    if p.get("trial_attended"):
        score += 15; factors["trial_attended"] = 15
    score = min(99, score + int(_rng(p, 0, 8, "ls")))
    return {"score": score, "factors": factors, "recommendation": "Call within 1 hour" if score >= 70 else "Standard follow-up sequence"}


def _sim_churn(p: dict) -> dict:
    score = 10.0
    factors = {}
    att = float(p.get("attendance_pct_30d", 90))
    if att < 60:
        score += 35; factors["low_attendance"] = 35
    elif att < 80:
        score += 18; factors["declining_attendance"] = 18
    missed = int(p.get("missed_last_14d", 0))
    if missed >= 3:
        score += 15; factors["recent_misses"] = 15
    if p.get("test_trend", 0) < 0:
        score += 15; factors["declining_test_scores"] = 15
    if p.get("open_complaints", 0) > 0:
        score += 15; factors["open_complaints"] = 15
    if p.get("overdue_invoices", 0) > 0:
        score += 12; factors["payment_risk"] = 12
    if p.get("is_frozen"):
        score += 20; factors["frozen"] = 20
    if p.get("tenure_days", 100) < 45:
        score += 8; factors["early_tenure"] = 8
    score = min(98.0, round(score, 1))
    level = "high" if score >= 65 else ("medium" if score >= 40 else "low")
    return {"score": score, "level": level, "factors": factors,
            "recommended_action": {"high": "win_back_sequence", "medium": "cohort_call", "low": None}[level]}


def _sim_complaint(p: dict) -> dict:
    text = (p.get("text") or "").lower()
    cats = [("billing", ["invoice", "payment", "charge", "refund", "fee"]), ("punctuality", ["late", "on time", "missed", "absent", "no show"]),
            ("teaching_quality", ["tajweed", "teach", "explain", "quality", "progress"]), ("technical", ["audio", "video", "connection", "link", "zoom", "internet"]),
            ("behaviour", ["rude", "shout", "behav", "attitude"]), ("schedule", ["reschedul", "time change", "slot", "timing"])]
    best, hits = "general", 0
    for cat, kws in cats:
        n = sum(1 for k in kws if k in text)
        if n > hits:
            best, hits = cat, n
    urgent = any(k in text for k in ("urgent", "immediately", "cancel", "leave the college"))
    return {"category": best, "priority": "urgent" if urgent else ("high" if hits >= 2 else "medium"),
            "sentiment": "negative" if hits or urgent else "neutral", "suggested_department": {"billing": "finance", "technical": "technology", "teaching_quality": "qa"}.get(best, "operations")}


def _sim_lesson_recommendation(p: dict) -> dict:
    return {"recommendation": (f"Revise {p.get('last_lesson', 'the previous lesson')} for 5 minutes (dor), then introduce "
                               f"{p.get('next_lesson', 'the next lesson')} focusing on {p.get('focus', 'makhaarij and madd rules')}. "
                               f"Reserve the final 5 minutes for the student's own recitation."),
            "suggested_duration_split": {"dor": 5, "sabqi": 8, "sabaq": 12, "recitation": 5}}


def _sim_sentiment(p: dict) -> dict:
    text = (p.get("text") or "").lower()
    neg = sum(k in text for k in ("not", "bad", "poor", "late", "unhappy", "disappoint", "worst", "complain"))
    pos = sum(k in text for k in ("great", "excellent", "happy", "mashallah", "jazak", "good", "love", "best"))
    s = "positive" if pos > neg else ("negative" if neg > pos else "neutral")
    return {"sentiment": s, "confidence": round(0.6 + 0.1 * abs(pos - neg), 2)}


def _sim_insights(p: dict) -> dict:
    items = []
    m = p.get("metrics", {})
    if m.get("missed_rate", 0) > 5:
        items.append({"type": "risk", "text": f"Missed-class rate is {m['missed_rate']}% — above the 5% threshold. Review teacher punctuality in the evening shift."})
    if m.get("collection_rate", 100) < 85:
        items.append({"type": "finance", "text": f"Collection rate {m['collection_rate']}% — {m.get('overdue_count', 0)} overdue invoices need billing follow-up."})
    if m.get("trial_conversion", 0) < 30:
        items.append({"type": "growth", "text": f"Trial-to-paid conversion is {m['trial_conversion']}%. Closer follow-up within 24h of trial improves conversion."})
    if m.get("high_risk_students", 0) > 0:
        items.append({"type": "retention", "text": f"{m['high_risk_students']} students are at high churn risk. Win-back sequences are enrolled automatically; supervisors should call this week."})
    if m.get("qa_avg", 100) < 75:
        items.append({"type": "quality", "text": f"Average QA score {m['qa_avg']} — assign Ustaadh Lab training for grade-C teachers."})
    if not items:
        items.append({"type": "ok", "text": "All institutional health indicators are within target ranges this period."})
    return {"insights": items}


_SIM = {
    "class_monitoring": _sim_class_monitoring, "lead_scoring": _sim_lead_scoring, "churn": _sim_churn,
    "complaint_classification": _sim_complaint, "lesson_recommendation": _sim_lesson_recommendation,
    "sentiment": _sim_sentiment, "insights": _sim_insights, "qa_recommendation": _sim_class_monitoring,
    "anomaly": lambda p: {"anomalies": []}, "transcription": lambda p: {"transcript": "[simulated transcript — configure AI_PROVIDER for real transcription]"},
}


def ai(db: Session, module: str, task: str, payload: dict, entity: Any = None, commit: bool = False) -> tuple[dict, AIModelRun]:
    """Run an AI task. Returns (result, AIModelRun). Confidence is always included in the result."""
    t0 = time.time()
    provider = settings.AI_PROVIDER
    if provider == "simulated" or not settings.AI_API_KEY:
        handler = _SIM.get(module) or (lambda p: {"note": "no simulated handler"})
        result = handler(payload)
        provider, model = "simulated", f"oqc-heuristic-{module}"
        cost, tin, tout = 0.0, 0, 0
    else:  # pragma: no cover - real provider hook (Anthropic / OpenAI compatible endpoint)
        result, model, cost, tin, tout = _call_provider(module, task, payload)
    confidence = result.get("confidence") or round(_rng(payload, 0.72, 0.97, module), 2)
    result["confidence"] = confidence
    run = AIModelRun(module=module, task=task, provider=provider, model=model, model_version="1.0",
                     prompt_version=PROMPT_VERSIONS.get(module, "1.0"),
                     entity_type=entity.__class__.__name__ if entity is not None else payload.get("entity_type"),
                     entity_id=getattr(entity, "id", None) if entity is not None else payload.get("entity_id"),
                     input_summary=json.dumps(payload, default=str)[:2000], output=result, confidence=confidence,
                     tokens_in=tin, tokens_out=tout, cost_usd=cost, latency_ms=int((time.time() - t0) * 1000),
                     review_status="pending" if module in ("class_monitoring", "complaint_classification", "churn") else "approved")
    db.add(run)
    db.flush()
    if commit:
        db.commit()
    return result, run


def _call_provider(module: str, task: str, payload: dict):  # pragma: no cover
    """Real-provider call. Uses an OpenAI-compatible chat endpoint (works for most gateways). Falls back to simulation on error."""
    import httpx
    prompt = f"You are the Online Quran College AI assistant. Task: {task} (module {module}). Return strict JSON.\nInput: {json.dumps(payload, default=str)}"
    try:
        r = httpx.post("https://api.openai.com/v1/chat/completions", timeout=30,
                       headers={"Authorization": f"Bearer {settings.AI_API_KEY}"},
                       json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}})
        r.raise_for_status()
        data = r.json()
        content = json.loads(data["choices"][0]["message"]["content"])
        usage = data.get("usage", {})
        return content, data.get("model", "gpt-4o-mini"), usage.get("total_tokens", 0) * 0.00000015, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    except Exception:
        handler = _SIM.get(module) or (lambda p: {})
        return handler(payload), "fallback-simulated", 0.0, 0, 0


def review_run(db: Session, run: AIModelRun, user, status: str, note: Optional[str] = None) -> None:
    run.review_status = status
    run.reviewed_by_id = user.id
    run.reviewed_at = datetime.utcnow()
    run.review_note = note
