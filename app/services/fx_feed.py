"""Automatic currency rates (the old ERP's "ERP Auto Currency Rate List", Fusion gap report M06.02.02).

Once a day, and on demand from Accounts › Setup › Currency Rates, the rate of every active currency against the base
currency is fetched from a public rate provider and stored with its history (source "api"). A currency someone set
by hand (``manual_override``) is left alone until a feed rate is applied to it again.

The provider is the free ExchangeRate-API open endpoint by default; Configuration › Integrations › "fx" can point it
elsewhere (``config["url"]`` with ``{base}`` in it) as long as the answer has the same shape:
``{"result": "success", "rates": {"GBP": 0.0028, ...}}`` where each rate is units of that currency per 1 base.
If the provider cannot be reached nothing changes and the failure shows on the integration's health.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models.core import User
from app.models.finance import Currency, ExchangeRateHistory
from app.services import billing
from app.services import integrations

log = logging.getLogger("oqc.fx")

PROVIDER = "fx"
DEFAULT_URL = "https://open.er-api.com/v6/latest/{base}"


def feed_url(db: Session, base: str) -> str:
    integ = integrations.get_integration(db, PROVIDER)
    url = str((integ.config or {}).get("url") or DEFAULT_URL)
    return url.replace("{base}", base)


def fetch(db: Session, base: str, timeout: float = 15.0) -> dict[str, float]:
    """Units of each currency per 1 ``base`` from the provider. Raises RuntimeError when the answer is unusable."""
    url = feed_url(db, base)
    resp = httpx.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if str(data.get("result", "success")).lower() != "success":
        raise RuntimeError(f"rate provider said: {data.get('error-type') or data.get('result')}")
    rates = data.get("rates") or data.get("conversion_rates") or {}
    if not isinstance(rates, dict) or not rates:
        raise RuntimeError("rate provider returned no rates")
    return {str(k).upper(): float(v) for k, v in rates.items() if isinstance(v, (int, float)) and v > 0}


def run(db: Session, user: Optional[User] = None) -> dict:
    """Fetch and apply. Returns {"ok", "updated", "skipped", "missing", "error"}; the caller commits."""
    base = db.query(Currency).filter(Currency.is_base.is_(True)).first()
    if not base:
        return {"ok": False, "updated": [], "skipped": [], "missing": [], "error": "no base currency is set"}
    try:
        per_base = fetch(db, base.code)
    except Exception as exc:  # network, HTTP or shape problems: nothing changes
        integrations._record(db, PROVIDER, False, error=str(exc))
        log.warning("currency feed failed: %s", exc)
        return {"ok": False, "updated": [], "skipped": [], "missing": [], "error": str(exc)[:200]}
    updated, skipped, missing = [], [], []
    for c in db.query(Currency).filter(Currency.is_base.is_(False), Currency.is_active.is_(True)).order_by(Currency.code):
        if c.manual_override:
            skipped.append(c.code)
            continue
        units = per_base.get(c.code)
        if not units:
            missing.append(c.code)
            continue
        billing.set_exchange_rate(db, c.code, round(1.0 / units, 6), user, source="api")
        updated.append(c.code)
    integrations._record(db, PROVIDER, True)
    return {"ok": True, "updated": updated, "skipped": skipped, "missing": missing, "error": None}


def recent(db: Session, limit: int = 200) -> list[ExchangeRateHistory]:
    """The rates the feed applied, newest first: the ERP's Auto Currency Rate List."""
    return (db.query(ExchangeRateHistory).filter(ExchangeRateHistory.source == "api")
            .order_by(ExchangeRateHistory.effective_at.desc(), ExchangeRateHistory.id.desc()).limit(limit).all())


def job(db: Session) -> str:
    """Daily scheduler entry (app/services/jobs_finance.py)."""
    result = run(db)
    db.commit()
    if not result["ok"]:
        return f"currency feed failed: {result['error']}"
    return f"{len(result['updated'])} currency rate(s) updated at {datetime.utcnow():%H:%M} UTC"
