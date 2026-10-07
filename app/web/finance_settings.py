"""Accounts › Setup — the money settings that used to sit under Configuration (docs/MODULE_STRUCTURE.md).

    /finance/currency-rates            every currency with its rate to base, when and by whom it was last set,
                                       Add Manual Currency Rate, a quick per-currency Set Rate, activate /
                                       deactivate, the (simulated) ERP Currency Rates feed and the
                                       country -> default currency table
    /finance/currency-rates/{code}     one currency's rate history, chart and exposure, with a manual rate form
    /finance/payment-gateways          the gateway catalogue with its default transaction fee

The older Billing list at /finance/currencies was a second copy of the first two screens; it now redirects here
(app.web.finance), and the /config/... addresses these pages used to live at redirect here as well
(app.web.company_config). Currency Rates is guarded by currencies.view / currencies.update; Payment Gateways by
payments.view / payments.configure. Every mutation writes an audit event and redirects (303) with a flash message.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import parse_bool, parse_date, parse_float, parse_int, redirect
from app.database import get_db
from app.models.config_erp import PaymentGateway
from app.models.core import User
from app.models.erp import BeneficiaryAccount
from app.models.finance import Currency, ExchangeRateHistory, Invoice, Subscription
from app.services import billing
from app.web.company_config import STATUSES, _get, _rationale, _status_field, _toggle

router = APIRouter(prefix="/finance", dependencies=[Depends(csrf_protect)])

RATES = "/finance/currency-rates"
GATEWAYS = "/finance/payment-gateways"
CURRENCY_MODULE = "currencies"
GATEWAY_MODULE = "payments"
GATEWAY_COMPANIES = ["Stripe", "PayPal", "Wise", "Payoneer", "Bank Alfalah", "Other"]
RATE_SOURCES = [("manual", "Manual override"), ("api", "Rate feed")]


# =============================================================================== currency rates
def _rate_rows(db: Session) -> tuple[list[dict], Optional[Currency]]:
    base = db.query(Currency).filter(Currency.is_base.is_(True)).first()
    usage = dict(db.query(Subscription.currency, func.count(Subscription.id))
                 .filter(Subscription.status == "active").group_by(Subscription.currency).all())
    rows = []
    for c in db.query(Currency).order_by(Currency.is_base.desc(), Currency.code).all():
        last = (db.query(ExchangeRateHistory).filter(ExchangeRateHistory.currency_code == c.code)
                .order_by(ExchangeRateHistory.effective_at.desc(), ExchangeRateHistory.id.desc()).first())
        rows.append({"c": c, "last": last, "subs": usage.get(c.code, 0),
                     "history_count": db.query(func.count(ExchangeRateHistory.id)).filter(
                         ExchangeRateHistory.currency_code == c.code).scalar() or 0})
    return rows, base


def _currency(db: Session, code: str) -> Currency:
    currency = db.query(Currency).filter(Currency.code == (code or "").upper()).first()
    if not currency:
        raise HTTPException(404, "Currency not found")
    return currency


@router.get("/currency-rates", include_in_schema=False)
def currency_rates(request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require("currencies.view"))):
    rows, base = _rate_rows(db)
    history = (db.query(ExchangeRateHistory).order_by(ExchangeRateHistory.effective_at.desc(),
                                                      ExchangeRateHistory.id.desc()).limit(25).all())
    setters = {u.id: u.full_name for u in db.query(User).all()} if history else {}
    return render(request, "finance/currency_rates.html", {
        "user": user, "rows": rows, "base": base, "history": history, "setters": setters,
        "today": date.today().isoformat(), "sources": RATE_SOURCES,
        "country_map": billing.country_currency_map(db),
        "can_edit": rbac.has_permission(user, "currencies.update"),
        "stats": {"total": len(rows), "active": sum(1 for r in rows if r["c"].is_active),
                  "manual": sum(1 for r in rows if r["c"].manual_override),
                  "history": db.query(func.count(ExchangeRateHistory.id)).scalar() or 0}})


@router.post("/currency-rates/manual", include_in_schema=False)
async def currency_rate_manual(request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require("currencies.update"))):
    """Add Manual Currency Rate — writes a history row and moves the currency onto the new rate."""
    form = await request.form()
    code = (form.get("code") or "").strip().upper()[:3]
    rate = parse_float(form.get("rate_to_base"), 0.0)
    effective = parse_date(form.get("effective_at"), date.today())
    note = (form.get("note") or "").strip()
    currency = db.query(Currency).filter(Currency.code == code).first()
    if not currency:
        return redirect(RATES, f"Unknown currency '{code}'.", "error")
    if rate <= 0:
        return redirect(RATES, "The rate must be greater than zero.", "error")
    if currency.is_base and abs(rate - 1) > 1e-9:
        return redirect(RATES, f"{code} is the base currency; its rate is always 1.", "error")
    before = snapshot(currency)
    currency.rate_to_base = rate
    if parse_bool(form.get("manual_override")):
        currency.manual_override = True
    db.add(ExchangeRateHistory(currency_code=code, rate_to_base=rate, source="manual", set_by_id=user.id,
                               effective_at=datetime.combine(effective, datetime.min.time())))
    log_action(db, user, "update", CURRENCY_MODULE, entity=currency, severity="warning", consequential=True,
               rationale=note or _rationale(form) or "Manual currency rate added",
               description=f"Manual rate for {code} set to {rate} effective {effective.isoformat()}"
                           + (f" — {note}" if note else ""),
               before=before, after=snapshot(currency), request=request)
    db.commit()
    return redirect(RATES, f"Manual rate for {code} saved ({rate} to base, effective {effective.isoformat()}).")


@router.post("/currency-rates/refresh", include_in_schema=False)
async def currency_rates_refresh(request: Request, db: Session = Depends(get_db),
                                 user: User = Depends(require("currencies.update"))):
    """Refresh Rates — a SIMULATED feed. No external provider is called anywhere in this build."""
    base = db.query(Currency).filter(Currency.is_base.is_(True)).first()
    now = datetime.utcnow()
    updated, skipped = [], []
    for c in db.query(Currency).filter(Currency.is_base.is_(False)).order_by(Currency.code).all():
        if c.manual_override:
            skipped.append(c.code)
            continue
        # Deterministic drift so the simulation is reproducible: +/- up to 1.2% keyed off the code and the day.
        seed = sum(ord(ch) for ch in c.code) + now.timetuple().tm_yday
        drift = ((seed % 25) - 12) / 1000.0
        new_rate = round(max(0.000001, float(c.rate_to_base or 1) * (1 + drift)), 6)
        c.rate_to_base = new_rate
        db.add(ExchangeRateHistory(currency_code=c.code, rate_to_base=new_rate, source="simulated_feed",
                                   set_by_id=user.id, effective_at=now))
        updated.append(c.code)
    log_action(db, user, "execute", CURRENCY_MODULE, entity_type="Currency", severity="warning", consequential=True,
               rationale="Simulated ERP Currency Rates feed run from the Currency Rates screen",
               description=f"Simulated feed updated {len(updated)} currencies ({', '.join(updated) or 'none'})"
                           + (f"; left alone (manual override): {', '.join(skipped)}" if skipped else ""),
               after={"base": base.code if base else None, "updated": updated, "skipped": skipped}, request=request)
    db.commit()
    msg = (f"Simulated ERP feed: {len(updated)} currency rate(s) updated and recorded in history. "
           "No external rate provider was contacted — these figures are generated locally.")
    if skipped:
        msg += f" Left alone because they are set manually: {', '.join(skipped)}."
    return redirect(RATES, msg, "info")


def set_rate(db: Session, user: User, code: str, form) -> tuple[bool, str]:
    """The quick Set Rate the Billing currency list had: one rate, recorded as a manual override or as a
    feed rate (which hands the currency back to the feed). Shared with the old /finance/currencies/{code}/rate."""
    source = (form.get("source") or "manual").strip()
    source = source if source in dict(RATE_SOURCES) else "manual"
    try:
        billing.set_exchange_rate(db, (code or "").upper(), parse_float(form.get("rate"), 0), user, source=source)
    except ValueError as exc:
        db.rollback()
        return False, str(exc)
    db.commit()
    return True, f"{code.upper()} rate updated."


@router.post("/currency-rates/{code}/rate", include_in_schema=False)
async def currency_set_rate(code: str, request: Request, db: Session = Depends(get_db),
                            user: User = Depends(require("currencies.update"))):
    form = await request.form()
    ok, message = set_rate(db, user, code, form)
    back = form.get("back") or RATES
    back = back if back.startswith(RATES) else RATES
    return redirect(back, message, "success" if ok else "error")


@router.post("/currency-rates/{code}/toggle", include_in_schema=False)
async def currency_toggle(code: str, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("currencies.update"))):
    """Activate or deactivate a currency. An inactive currency drops out of the currency pickers on new
    invoices, receipts and vouchers; records already in it keep it. The base currency is always active."""
    currency = _currency(db, code)
    form = await request.form()
    back = form.get("back") or RATES
    back = back if back.startswith(RATES) else RATES
    if currency.is_base:
        return redirect(back, f"{currency.code} is the base currency and cannot be deactivated.", "error")
    before = snapshot(currency)
    currency.is_active = not currency.is_active
    state = "activated" if currency.is_active else "deactivated"
    in_use = db.query(func.count(Subscription.id)).filter(Subscription.currency == currency.code,
                                                          Subscription.status == "active").scalar() or 0
    log_action(db, user, "status_change", CURRENCY_MODULE, entity=currency, severity="warning", consequential=True,
               rationale=_rationale(form) or f"Currency {state} from the Currency Rates screen",
               description=f"Currency {currency.code} {state}", before=before, after=snapshot(currency), request=request)
    db.commit()
    msg = f"{currency.code} {state}."
    if not currency.is_active and in_use:
        msg += f" {in_use} active subscription(s) still bill in {currency.code}; they are not changed."
    return redirect(back, msg, "warning" if (not currency.is_active and in_use) else "success")


@router.get("/currency-rates/{code}", include_in_schema=False)
def currency_rate_history(code: str, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require("currencies.view"))):
    currency = _currency(db, code)
    history = (db.query(ExchangeRateHistory).filter(ExchangeRateHistory.currency_code == currency.code)
               .order_by(ExchangeRateHistory.effective_at.desc(), ExchangeRateHistory.id.desc()).limit(200).all())
    setters = {u.id: u.full_name for u in db.query(User).all()}
    base = db.query(Currency).filter(Currency.is_base.is_(True)).first()
    subs = db.query(func.count(Subscription.id)).filter(Subscription.currency == currency.code,
                                                        Subscription.status == "active").scalar() or 0
    invoiced = (db.query(func.coalesce(func.sum(Invoice.total), 0))
                .filter(Invoice.currency == currency.code, Invoice.status != "void").scalar() or 0)
    return render(request, "finance/currency_history.html", {
        "user": user, "currency": currency, "history": history, "setters": setters, "base": base,
        "subs": subs, "invoiced": float(invoiced), "sources": RATE_SOURCES,
        "can_edit": rbac.has_permission(user, "currencies.update"), "today": date.today().isoformat()})


# =============================================================================== payment gateways
def _gateway_perms(user: User) -> dict:
    can = rbac.has_permission(user, "payments.configure")
    return {"can_add": can, "can_edit": can}


@router.get("/payment-gateways", include_in_schema=False)
def payment_gateways(request: Request, status: str = "", company: str = "", q: str = "", sample: float = 100.0,
                     db: Session = Depends(get_db), user: User = Depends(require("payments.view"))):
    query = db.query(PaymentGateway)
    if status:
        query = query.filter(PaymentGateway.status == status)
    if company:
        query = query.filter(PaymentGateway.gateway_company == company)
    if q:
        query = query.filter(func.lower(PaymentGateway.name).like(f"%{q.lower()}%"))
    rows = query.order_by(PaymentGateway.id).all()
    accounts = db.query(BeneficiaryAccount).filter(BeneficiaryAccount.status == "active").order_by(
        BeneficiaryAccount.account_name).all()
    currencies = [c.code for c in db.query(Currency).order_by(Currency.code).all()]
    return render(request, "finance/payment_gateways.html", {
        "user": user, "rows": rows, "status": status, "company": company, "q": q, "sample": sample,
        "statuses": STATUSES, "companies": GATEWAY_COMPANIES, "currencies": currencies,
        "accounts": [(a.id, f"{a.account_name} ({a.category})") for a in accounts],
        "stats": {"total": db.query(func.count(PaymentGateway.id)).scalar() or 0,
                  "active": db.query(func.count(PaymentGateway.id)).filter(PaymentGateway.status == "active").scalar() or 0,
                  "live": db.query(func.count(PaymentGateway.id)).filter(PaymentGateway.live_mode.is_(True)).scalar() or 0,
                  "test": db.query(func.count(PaymentGateway.id)).filter(PaymentGateway.live_mode.is_(False)).scalar() or 0},
        **_gateway_perms(user)})


def _apply_gateway(g: PaymentGateway, form) -> None:
    g.name = (form.get("name") or g.name or "").strip()
    g.gateway_company = (form.get("gateway_company") or g.gateway_company or "Other").strip()
    g.default_transaction_fee_pct = parse_float(form.get("default_transaction_fee_pct"), g.default_transaction_fee_pct or 0)
    g.fixed_fee = parse_float(form.get("fixed_fee"), float(g.fixed_fee or 0))
    g.currency = (form.get("currency") or "").strip().upper()[:3] or None
    g.beneficiary_account_id = parse_int(form.get("beneficiary_account_id"))
    g.live_mode = parse_bool(form.get("live_mode"))
    g.status = _status_field(form, g.status or "active")
    g.notes = (form.get("notes") or "").strip() or None


@router.post("/payment-gateways/new", include_in_schema=False)
async def gateway_create(request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("payments.configure"))):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return redirect(GATEWAYS, "A gateway name is required.", "error")
    g = PaymentGateway(name=name, gateway_company="Other", status="active")
    _apply_gateway(g, form)
    db.add(g)
    db.flush()
    log_action(db, user, "create", GATEWAY_MODULE, entity=g, description=f"Payment gateway {g.name} created",
               after=snapshot(g), request=request)
    db.commit()
    return redirect(GATEWAYS, f"Payment gateway '{g.name}' created.")


@router.post("/payment-gateways/{gid}/edit", include_in_schema=False)
async def gateway_edit(gid: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("payments.configure"))):
    g = _get(db, PaymentGateway, gid, "Payment gateway")
    form = await request.form()
    before = snapshot(g)
    _apply_gateway(g, form)
    log_action(db, user, "update", GATEWAY_MODULE, entity=g, description=f"Payment gateway {g.name} updated",
               before=before, after=snapshot(g), request=request)
    db.commit()
    return redirect(GATEWAYS, f"Payment gateway '{g.name}' saved.")


@router.post("/payment-gateways/{gid}/toggle", include_in_schema=False)
async def gateway_toggle(gid: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require("payments.configure"))):
    g = _get(db, PaymentGateway, gid, "Payment gateway")
    return _toggle(db, user, request, g, f"Gateway '{g.name}'", GATEWAYS, module=GATEWAY_MODULE)
