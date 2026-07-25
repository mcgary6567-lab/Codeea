"""Prometheus Web — FastAPI backend.

Exposes the desktop app's capabilities over HTTP + WebSocket: auth, exchange
connect, manual trading, positions, settings, guardrails, TradingView webhooks,
built-in strategy, backtesting and analytics — multi-user, one server.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

# Put the trading engine (flat-import modules) on the path BEFORE importing it.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "engine"))

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import backtest as bt
import strategy as strat

from . import security, store, webpush
from .config_web import PUBLIC_URL, LICENCE_PRICE
from .session import (get_session, public_ohlcv, public_ohlcv_days, public_prices,
                      live_stats, session_snapshot_if_live)

_TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
               "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400}


def _public_client_tf_seconds(tf: str) -> int:
    return _TF_SECONDS.get(tf, 3600)

store.init_db()
app = FastAPI(title="Prometheus Web", version="1.0.0")

WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")


@app.on_event("startup")
def _restore_bots() -> None:
    """Bring every entitled user's bot back online after a restart/deploy, so
    trading is genuinely 24/7 and does NOT wait for someone to open the web app.
    Each session auto-reconnects its saved keys (and auto-starts the strategy)."""
    import threading

    def _boot() -> None:
        for u in store.all_users():
            try:
                if not u.get("keys_blob"):
                    continue                      # no exchange keys → nothing to run
                if not store.entitlement(u)["ok"]:
                    continue                      # expired/suspended → don't trade
                get_session(u["id"])              # creates + background-reconnects the bot
            except Exception:
                pass
    threading.Thread(target=_boot, name="restore-bots", daemon=True).start()


# --- auth helpers -----------------------------------------------------------
def current_user(authorization: str = Header(default="")) -> dict:
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    data = security.read_token(token)
    if not data:
        raise HTTPException(401, "invalid or missing token")
    user = store.get_user(int(data["sub"]))
    if not user:
        raise HTTPException(401, "user not found")
    if int(data.get("tv", 0)) != int(user.get("token_version", 0) or 0):
        raise HTTPException(401, "session expired \u2014 please sign in again")
    store.touch(user["id"])
    return user


def require_entitled(user: dict) -> None:
    ent = store.entitlement(user)
    if not ent["ok"]:
        raise HTTPException(402, f"access {ent['status']} — an active licence is required")


def require_admin(user: dict = Depends(current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(403, "admin only")
    return user


async def body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def _ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    return xff.split(",")[0].strip() if xff else (request.client.host if request.client else "")


# --- auth routes ------------------------------------------------------------
@app.post("/api/register")
async def register(request: Request):
    d = await body(request)
    email, pw = d.get("email", "").strip(), d.get("password", "")
    if not email or len(pw) < 6:
        raise HTTPException(400, "email and a 6+ char password required")
    try:
        user = store.create_user(email, pw, d.get("first_name", ""), d.get("last_name", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    ip = _ip(request)
    store.record_login(user["id"], ip, request.headers.get("user-agent", ""))
    _geolocate_user_async(user["id"], ip)   # fill country for the social-proof popup
    return {"token": security.make_token(user["id"], user["email"], user.get("token_version", 0)), "email": user["email"]}


@app.post("/api/login")
async def login(request: Request):
    d = await body(request)
    user = store.get_user_by_email(d.get("email", ""))
    if not user or not security.verify_password(d.get("password", ""), user["pw_hash"]):
        raise HTTPException(401, "bad credentials")
    if user.get("totp_enabled"):
        code = str(d.get("totp", "")).strip()
        if not code:
            return {"need_2fa": True}
        if not security.verify_totp(user.get("totp_secret", ""), code):
            raise HTTPException(401, "invalid 2FA code")
    store.record_login(user["id"], _ip(request), request.headers.get("user-agent", ""))
    return {"token": security.make_token(user["id"], user["email"], user.get("token_version", 0)), "email": user["email"]}


# --- password reset ---------------------------------------------------------
@app.post("/api/forgot")
async def forgot(request: Request):
    from . import mailer
    d = await body(request)
    user = store.get_user_by_email(d.get("email", ""))
    # Always return ok (don't reveal whether an email is registered).
    if user:
        raw = store.create_reset(user["id"])
        link = f"{PUBLIC_URL or ''}/?reset={raw}"
        ok, msg = mailer.send_email(
            user["email"], "Reset your Prometheus password",
            f"Reset your password (valid 1 hour):\n\n{link}\n\nIf you didn't request this, ignore this email.")
        if not ok:
            return {"ok": True, "note": "email not configured on the server — contact support"}
    return {"ok": True}


@app.post("/api/reset")
async def reset(request: Request):
    d = await body(request)
    pw = d.get("password", "")
    if len(pw) < 6:
        raise HTTPException(400, "password must be 6+ characters")
    uid = store.consume_reset(d.get("token", ""))
    if not uid:
        raise HTTPException(400, "invalid or expired reset link")
    store.update_password(uid, pw)
    store.bump_token_version(uid)          # invalidate every other existing session
    u = store.get_user(uid)
    return {"token": security.make_token(u["id"], u["email"], u.get("token_version", 0)), "email": u["email"]}


# --- 2FA (TOTP) -------------------------------------------------------------
@app.post("/api/2fa/setup")
def twofa_setup(user: dict = Depends(current_user)):
    secret = security.new_totp_secret()
    store.set_totp_secret(user["id"], secret)          # stored, not yet enabled
    uri = security.totp_uri(secret, user["email"])
    return {"secret": secret, "uri": uri, "qr": security.totp_qr_data_uri(uri)}


@app.post("/api/2fa/enable")
async def twofa_enable(request: Request, user: dict = Depends(current_user)):
    d = await body(request)
    if not security.verify_totp(user.get("totp_secret", ""), str(d.get("code", ""))):
        raise HTTPException(400, "wrong code — re-scan and try again")
    store.set_totp_enabled(user["id"], True)
    return {"ok": True}


@app.post("/api/2fa/disable")
async def twofa_disable(request: Request, user: dict = Depends(current_user)):
    d = await body(request)
    if not security.verify_password(d.get("password", ""), user["pw_hash"]):
        raise HTTPException(400, "wrong password")
    store.set_totp_enabled(user["id"], False)
    store.set_totp_secret(user["id"], "")
    return {"ok": True}


# --- state ------------------------------------------------------------------
def _webhook_url(user: dict) -> str:
    base = PUBLIC_URL or ""
    return f"{base}/webhook/{user['webhook_token']}"


def full_state(user: dict) -> dict:
    """Snapshot + per-user fields — used by BOTH /api/state and the WebSocket so
    live updates never drop the licence/access status (fixes the stale banner)."""
    s = get_session(user["id"])
    snap = s.snapshot()
    snap["email"] = user["email"]
    snap["first_name"] = user.get("first_name", "")
    snap["last_name"] = user.get("last_name", "")
    snap["name"] = (f"{user.get('first_name', '')} {user.get('last_name', '')}").strip() or user["email"].split("@")[0]
    snap["is_admin"] = bool(user.get("is_admin"))
    snap["totp_enabled"] = bool(user.get("totp_enabled"))
    snap["webhook_url"] = _webhook_url(user)
    snap["has_keys"] = store.load_keys(user["id"]) is not None
    snap["announcement"] = store.active_broadcast()
    _allow = bool(user.get("allow_custom"))
    snap["allow_custom"] = _allow
    snap["strategy_managed"] = not _allow
    snap["custom_requested"] = bool(user.get("custom_requested"))
    if not _allow:
        _gs = store.get_global_strategy()
        snap["managed_strategy"] = {"params": _gs.get("params", {}), "timeframe": _gs.get("timeframe", "15m"), "symbols": _gs.get("symbols", "BTC/USDT"), "execution": _gs.get("execution", {})}
    snap["access"] = store.entitlement(user)
    return snap


@app.get("/api/state")
def state(user: dict = Depends(current_user)):
    return full_state(user)


@app.post("/api/account")
async def account(request: Request, user: dict = Depends(current_user)):
    d = await body(request)
    if not security.verify_password(d.get("current_password", ""), user["pw_hash"]):
        raise HTTPException(400, "current password is incorrect")
    if d.get("first_name") is not None or d.get("last_name") is not None:
        store.update_name(user["id"], d.get("first_name", user.get("first_name", "")),
                          d.get("last_name", user.get("last_name", "")))
    new_email = (d.get("new_email") or "").strip().lower()
    new_password = d.get("new_password") or ""
    if new_email and new_email != user["email"]:
        if store.get_user_by_email(new_email):
            raise HTTPException(400, "that email is already in use")
        store.update_email(user["id"], new_email)
    if new_password:
        if len(new_password) < 6:
            raise HTTPException(400, "new password must be 6+ characters")
        store.update_password(user["id"], new_password)
        store.bump_token_version(user["id"])   # sign out other sessions on password change
    u = store.get_user(user["id"])
    return {"ok": True, "token": security.make_token(u["id"], u["email"], u.get("token_version", 0)), "email": u["email"]}


@app.post("/api/keys")
async def save_keys(request: Request, user: dict = Depends(current_user)):
    d = await body(request)
    if not d.get("exchange") or not d.get("api_key") or not d.get("api_secret"):
        raise HTTPException(400, "exchange, api_key, api_secret required")
    store.save_keys(user["id"], {
        "exchange": d["exchange"], "market_type": d.get("market_type", "spot"),
        "api_key": d["api_key"], "api_secret": d["api_secret"], "password": d.get("password", ""),
    })
    return {"ok": True}


@app.post("/api/connect")
def connect(user: dict = Depends(current_user)):
    require_entitled(user)
    s = get_session(user["id"])
    try:
        s.connect()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/disconnect")
def disconnect(user: dict = Depends(current_user)):
    get_session(user["id"]).disconnect()
    return {"ok": True}


@app.post("/api/keys/clear")
def clear_keys(user: dict = Depends(current_user)):
    # Stop trading, drop the live connection, then wipe the stored encrypted keys.
    get_session(user["id"]).disconnect()
    store.clear_keys(user["id"])
    return {"ok": True}


# --- trading ----------------------------------------------------------------
@app.post("/api/trade")
async def trade(request: Request, user: dict = Depends(current_user)):
    d = await body(request)
    side = d.get("side", "").lower()
    if side not in ("buy", "sell"):
        raise HTTPException(400, "side must be buy/sell")
    require_entitled(user)
    s = get_session(user["id"])
    if not s.connected:
        raise HTTPException(400, "not connected")
    size = d.get("size")
    if size not in (None, ""):
        try:
            size = float(size)
        except (TypeError, ValueError):
            raise HTTPException(400, "size must be a number")
    else:
        size = None

    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0
    return s.place_manual(side, d.get("symbol", "BTC/USDT"), size,
                          sl=_f(d.get("sl")), tp1=_f(d.get("tp1")),
                          tp_partial=_f(d.get("tp_partial")), entry=_f(d.get("entry")),
                          order_type=str(d.get("order_type") or ""))


@app.get("/api/trade/suggest")
def trade_suggest(symbol: str = "BTC/USDT", side: str = "buy", user: dict = Depends(current_user)):
    return get_session(user["id"]).suggest_trade(symbol, side)


@app.post("/api/close")
async def close(request: Request, user: dict = Depends(current_user)):
    d = await body(request)
    try:
        fraction = float(d.get("fraction", 1.0))
    except (TypeError, ValueError):
        raise HTTPException(400, "fraction must be a number")
    return get_session(user["id"]).close_position(d.get("symbol", ""), fraction)


@app.post("/api/close_all")
def close_all(user: dict = Depends(current_user)):
    return get_session(user["id"]).close_all()


# --- settings & strategy ----------------------------------------------------
@app.post("/api/settings")
async def settings(request: Request, user: dict = Depends(current_user)):
    d = await body(request)
    get_session(user["id"]).set_settings(d)
    return {"ok": True}


@app.post("/api/log/clear")
def log_clear(user: dict = Depends(current_user)):
    get_session(user["id"]).clear_log()
    return {"ok": True}


@app.post("/api/telegram/test")
async def telegram_test(request: Request, user: dict = Depends(current_user)):
    from .session import TraderSession
    d = await body(request)
    s = get_session(user["id"])
    token = d.get("token") or s.settings.get("telegram_token", "")
    chat = d.get("chat") or s.settings.get("telegram_chat", "")
    ok, msg = TraderSession.telegram_test(token, chat)
    return {"ok": ok, "message": msg}


@app.post("/api/strategy")
async def strategy_ctl(request: Request, user: dict = Depends(current_user)):
    d = await body(request)
    s = get_session(user["id"])
    msg = ""
    wants_params = d.get("params") is not None or d.get("symbols") is not None or d.get("timeframe") is not None
    if wants_params and user.get("allow_custom"):
        patch = {}
        if d.get("symbols") is not None:
            patch["strategy_symbols"] = d["symbols"]
        if d.get("timeframe") is not None:
            patch["strategy_timeframe"] = d["timeframe"]
        if d.get("params") is not None:
            patch["strategy_params"] = d["params"]
        s.set_settings(patch)
    elif wants_params:
        # Managed plan: strategy parameters are locked, so don't pretend they saved.
        msg = ("Your strategy is professionally managed — its parameters can't be changed on the "
               "managed plan, so those weren't saved. Request ⭐ Custom Strategy access to edit them. "
               "(Your sizing, risk & alert settings on the Settings tab do save normally.)")
    if d.get("enable") is True:
        require_entitled(user)
        s.set_settings({"strategy_enabled": True})   # persist so it auto-starts on connect
        if s.connected:
            s.start_strategy()
        else:
            # Not an error — enabled now, will auto-start the moment an exchange connects.
            msg = "Strategy is ON — it will start automatically as soon as you connect an exchange."
    elif d.get("enable") is False:
        s.set_settings({"strategy_enabled": False})
        s.stop_strategy()
    return {"ok": True, "strategy_on": s.strategy_on,
            "strategy_enabled": s.settings.get("strategy_enabled"), "message": msg}


# --- backtest ---------------------------------------------------------------
@app.post("/api/backtest")
async def backtest(request: Request, user: dict = Depends(current_user)):
    d = await body(request)
    exch = d.get("exchange", "binance")
    symbol = d.get("symbol", "BTC/USDT")
    tf = d.get("timeframe", "1h")
    days = float(d.get("days", 0) or 0)
    if days > 0:
        candles = public_ohlcv_days(exch, symbol, tf, days)
    else:
        candles = public_ohlcv(exch, symbol, tf, min(int(d.get("limit", 1000)), 1500))
    if len(candles) < 60:
        raise HTTPException(400, "not enough candle data (need ccxt + connectivity, or a longer range)")
    # Base the backtest on the user's SAVED strategy params (so it reflects the
    # real strategy), then let any params in the request override them.
    params = strat.StrategyParams()
    saved = get_session(user["id"]).settings.get("strategy_params") or {}
    for src in (saved, d.get("params") or {}):
        for k, v in src.items():
            if hasattr(params, k):
                try:
                    setattr(params, k, type(getattr(params, k))(v))
                except (TypeError, ValueError):
                    setattr(params, k, v)
    cfg = bt.BacktestConfig()
    if d.get("start_equity"):
        cfg.start_equity = float(d["start_equity"])
    if d.get("fee_pct") is not None:
        cfg.fee_pct = float(d["fee_pct"])
    if d.get("risk_pct") is not None:
        cfg.risk_pct = float(d["risk_pct"])
    if d.get("allow_short") is not None:
        cfg.allow_short = bool(d["allow_short"])
    # Daily loss / profit circuit breaker — default to the user's live guardrail so the
    # backtest behaves like the real bot (which stops trading after the daily limit).
    _bs = get_session(user["id"]).settings
    cfg.daily_loss_pct = float(d.get("daily_loss_pct", _bs.get("daily_loss_pct", 0)) or 0)
    cfg.daily_profit_pct = float(d.get("daily_profit_pct", _bs.get("daily_profit_pct", 0)) or 0)
    # Scale-in / pyramiding — model it in the backtest using the user's live settings
    # (request overrides let the simulator preview it without changing saved config).
    cfg.scale_in = bool(d.get("scale_in", _bs.get("scale_in", False)))
    cfg.scale_in_trigger = float(d.get("scale_in_trigger", _bs.get("scale_in_trigger", 40)) or 40)
    cfg.scale_in_size = float(d.get("scale_in_size", _bs.get("scale_in_size", 50)) or 50)
    cfg.scale_in_be = bool(d.get("scale_in_be", _bs.get("scale_in_be", True)))
    cfg.weekend_pause = bool(d.get("weekend_pause", _bs.get("weekend_pause", False)))
    cfg.weekend_tz = float(d.get("weekend_tz", _bs.get("weekend_tz", 0)) or 0)
    try:
        cfg.bar_seconds = float(_public_client_tf_seconds(tf))
    except Exception:
        pass
    # News filter: if the user runs with news protection ON (news_trading == False),
    # skip backtest entries near known high-impact events. Coverage is limited to the
    # current week (historical economic-calendar data isn't available), so long
    # backtests are largely unaffected — it mainly matches live behaviour for recent runs.
    news_on = d.get("news_filter")
    if news_on is None:
        news_on = not get_session(user["id"]).settings.get("news_trading", True)
    if news_on:
        from . import news
        w = news.WINDOW_SEC * 1000
        cfg.news_windows = tuple((int(t * 1000 - w), int(t * 1000 + w)) for t in news.event_times())
    result = bt.run_backtest(candles, params, cfg)
    period = {
        "from": int(candles[0][0]), "to": int(candles[-1][0]),
        "days": round((candles[-1][0] - candles[0][0]) / 86_400_000, 1),
    }
    import math

    def _finite(v):
        if isinstance(v, float):
            if math.isinf(v):
                return 999999.0 if v > 0 else -999999.0   # JSON has no Infinity -> 500
            if math.isnan(v):
                return 0.0
            return round(v, 4)
        return v
    summary = {k: _finite(v) for k, v in result.stats.items()}
    return {
        "symbol": symbol, "timeframe": tf, "exchange": exch, "candles": len(candles),
        "start_equity": cfg.start_equity, "period": period,
        "buy_hold_pct": round(bt.buy_hold_return(candles), 2),
        "summary": summary,
        "news_filter": bool(cfg.news_windows),
        "trades": [_bt_trade(t) for t in result.trades],
        "equity": [[int(ts), round(eq, 2)] for ts, eq in result.equity],
    }


def _bt_trade(t) -> dict:
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in vars(t).items()}


# --- chart ------------------------------------------------------------------
@app.get("/api/candles")
def candles(symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 300,
            user: dict = Depends(current_user)):
    s = get_session(user["id"])
    data = s.chart_data(symbol, timeframe, min(int(limit), 1000))
    if not data["candles"]:
        raise HTTPException(400, "no candle data (needs ccxt + connectivity)")
    return data


# --- admin ------------------------------------------------------------------
@app.get("/api/admin/users")
def admin_users(admin: dict = Depends(require_admin)):
    live = live_stats()["per_user"]
    users = store.list_users()
    for u in users:
        u["live"] = live.get(u["id"], {"connected": False, "strategy_on": False})
    return {"users": users}


@app.get("/api/admin/stats")
def admin_stats(admin: dict = Depends(require_admin)):
    stats = store.platform_stats(LICENCE_PRICE)
    stats["live"] = {k: v for k, v in live_stats().items() if k != "per_user"}
    stats["revenue_real"] = store.revenue_stats()
    return stats


@app.get("/api/admin/user/{uid}")
def admin_user_detail(uid: int, admin: dict = Depends(require_admin)):
    detail = store.user_detail(uid)
    if not detail:
        raise HTTPException(404, "user not found")
    detail["live"] = session_snapshot_if_live(uid)
    return detail


@app.post("/api/admin/action")
async def admin_action(request: Request, admin: dict = Depends(require_admin)):
    d = await body(request)
    try:
        uid = int(d.get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    action = d.get("action", "")
    target = store.get_user(uid)
    if not target:
        raise HTTPException(404, "user not found")
    extra = {}
    detail = ""
    if action == "suspend":
        store.set_active(uid, False)
    elif action == "activate":
        store.set_active(uid, True)
    elif action == "grant":
        if d.get("lifetime"):
            store.grant_licence(uid, lifetime=True); detail = "lifetime"
        else:
            store.grant_licence(uid, days=float(d.get("days", 30))); detail = f"{d.get('days', 30)}d"
        amt = float(d.get("amount", 0) or 0)
        if amt > 0:
            store.record_sale(uid, amt, d.get("method", "manual"), "licence")
            detail += f" ${amt:g}"
    elif action == "extend_trial":
        store.extend_trial(uid, float(d.get("days", 7))); detail = f"{d.get('days', 7)}d"
    elif action == "revoke":
        store.revoke_licence(uid)
    elif action == "set_expiry":
        store.set_licence_until(uid, float(d.get("ts", 0) or 0)); detail = str(int(float(d.get("ts", 0) or 0)))
    elif action == "note":
        store.set_admin_note(uid, d.get("note", ""))
    elif action == "edit":
        em = (d.get("email") or "").strip().lower()
        if em:
            other = store.get_user_by_email(em)
            if other and other["id"] != uid:
                raise HTTPException(400, "email already in use")
        store.admin_update_user(uid, email=em or None,
                                first_name=d.get("first_name"), last_name=d.get("last_name"))
        detail = em or "profile"
    elif action == "record_sale":
        store.record_sale(uid, float(d.get("amount", 0) or 0), d.get("method", "manual"), d.get("note", ""))
        detail = f"${d.get('amount', 0)}"
    elif action == "make_admin":
        store.set_admin(uid, True)
    elif action == "remove_admin":
        admins = [u for u in store.list_users() if u.get("is_admin")]
        if len(admins) <= 1 and target.get("is_admin"):
            raise HTTPException(400, "cannot remove the last admin")
        store.set_admin(uid, False)
    elif action == "delete":
        if uid == admin["id"]:
            raise HTTPException(400, "you cannot delete your own account")
        admins = [u for u in store.list_users() if u.get("is_admin")]
        if len(admins) <= 1 and target.get("is_admin"):
            raise HTTPException(400, "cannot delete the last admin")
        store.delete_user(uid)
        store.record_audit(admin["email"], "delete", uid, target["email"])
        return {"ok": True, "deleted": True}
    elif action == "unlock_strategy":
        store.set_allow_custom(uid, True)
        try:
            get_session(uid).notify("\u2705 Custom-strategy access approved \u2014 you can now edit your Bot Setting.")
        except Exception:
            pass
    elif action == "lock_strategy":
        store.set_allow_custom(uid, False)
    elif action == "deny_custom":
        store.clear_custom_request(uid)
    elif action == "reset_link":
        raw = store.create_reset(uid, ttl_seconds=3600)
        base = PUBLIC_URL or ""
        extra["reset_url"] = f"{base}/?reset={raw}" if base else f"/?reset={raw}"
        extra["expires_minutes"] = 60
    else:
        raise HTTPException(400, "unknown action")
    store.record_audit(admin["email"], action, uid, detail)
    # never return secrets to the admin client (settings holds the Telegram token / webhook passphrase)
    _hide = ("pw_hash", "keys_blob", "totp_secret", "webhook_token", "webhook_passphrase", "settings")
    resp = {"ok": True, "user": {k: v for k, v in store.get_user(uid).items() if k not in _hide}}
    resp.update(extra)
    return resp


# --- admin: broadcasts, audit -----------------------------------------------
def _broadcast_telegram(message: str):
    import urllib.request as _u, urllib.parse as _p
    for token, chat in store.all_telegram_targets():
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = _p.urlencode({"chat_id": chat, "text": message}).encode()
            _u.urlopen(_u.Request(url, data=data), timeout=8)
        except Exception:
            continue


@app.post("/api/admin/broadcast")
async def admin_broadcast(request: Request, admin: dict = Depends(require_admin)):
    d = await body(request)
    msg = (d.get("message") or "").strip()
    if not msg:
        raise HTTPException(400, "message required")
    store.deactivate_broadcasts()
    bid = store.add_broadcast(msg, d.get("level", "info"))
    store.record_audit(admin["email"], "broadcast", None, msg[:80])
    if d.get("notify"):
        import threading as _t
        webpush.push_to_all("\U0001F4E2 " + msg)
        _t.Thread(target=_broadcast_telegram, args=("\U0001F4E2 " + msg,), daemon=True).start()
    return {"ok": True, "id": bid}


@app.get("/api/admin/broadcasts")
def admin_broadcasts(admin: dict = Depends(require_admin)):
    return {"broadcasts": store.recent_broadcasts(15)}


@app.post("/api/admin/broadcast_off")
def admin_broadcast_off(admin: dict = Depends(require_admin)):
    store.deactivate_broadcasts()
    store.record_audit(admin["email"], "broadcast_off")
    return {"ok": True}


@app.get("/api/admin/audit")
def admin_audit(admin: dict = Depends(require_admin)):
    return {"audit": store.recent_audit(30)}


@app.get("/api/admin/social_proof")
def admin_social_proof_get(admin: dict = Depends(require_admin)):
    return {"enabled": (store.kv_get("social_proof_on") or "1") == "1",
            "count": len(store.social_proof_sales(50))}


@app.post("/api/admin/social_proof")
async def admin_social_proof_set(request: Request, admin: dict = Depends(require_admin)):
    d = await body(request)
    on = bool(d.get("enabled"))
    store.kv_set("social_proof_on", "1" if on else "0")
    store.record_audit(admin["email"], "social_proof", None, "on" if on else "off")
    return {"ok": True, "enabled": on}


# --- customer: request custom-strategy access -------------------------------
@app.post("/api/strategy/request")
async def strategy_request(request: Request, user: dict = Depends(current_user)):
    # Custom Strategy (VIP) is a paid-customer perk — only an active licence may
    # request it. This backs up the UI (which hides the offer from everyone else)
    # so a trial / expired account can't request via a crafted call.
    ent = store.entitlement(user)
    if ent.get("status") != "licensed":
        raise HTTPException(402, "Custom Strategy is available to licensed customers only.")
    d = await body(request)
    store.request_custom(user["id"], str(d.get("reason", "")))
    return {"ok": True}


# --- admin: global bot strategy + per-customer unlock -----------------------
@app.get("/api/admin/strategy")
def admin_get_strategy(admin: dict = Depends(require_admin)):
    return store.get_global_strategy()


@app.post("/api/admin/strategy")
async def admin_set_strategy(request: Request, admin: dict = Depends(require_admin)):
    d = await body(request)
    params = {}
    for k, v in (d.get("params") or {}).items():
        try:
            params[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    # global execution/risk config pushed to all managed customers (whitelisted keys)
    _EXEC_STR = {"sizing_mode", "order_type", "margin_mode"}
    _EXEC_BOOL = {"auto_bracket", "scale_in", "scale_in_be", "weekend_pause"}
    _EXEC_NUM = {"risk_percent", "fixed_size", "fixed_quote", "leverage", "tp1_fraction",
                 "max_open", "daily_loss_pct", "daily_profit_pct", "cooldown", "dedupe",
                 "scale_in_trigger", "scale_in_size"}
    execution = {}
    for k, v in (d.get("execution") or {}).items():
        k = str(k)
        if k in _EXEC_STR:
            execution[k] = str(v)
        elif k in _EXEC_BOOL:
            execution[k] = bool(v)
        elif k in _EXEC_NUM:
            try:
                execution[k] = float(v)
            except (TypeError, ValueError):
                continue
    g = store.set_global_strategy(params, str(d.get("timeframe", "15m")),
                                  str(d.get("symbols", "BTC/USDT")), execution)
    store.record_audit(admin["email"], "strategy", None, f"v{g['version']} {g['timeframe']} {g['symbols']}")
    return g


@app.get("/api/admin/strategy_requests")
def admin_strategy_requests(admin: dict = Depends(require_admin)):
    return {"requests": store.pending_custom_requests()}


# --- live prices (dashboard strip) -----------------------------------------
POPULAR = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]


@app.get("/api/prices")
def prices(symbols: str = "", user: dict = Depends(current_user)):
    syms = [s.strip() for s in symbols.split(",") if s.strip()] or POPULAR
    s = get_session(user["id"])
    return public_prices(s.exchange_id or "binance", syms)


# --- analytics --------------------------------------------------------------
@app.get("/api/pnl_modes")
def pnl_modes(user: dict = Depends(current_user)):
    return store.pnl_by_mode(user["id"])


@app.get("/api/analytics")
def analytics(since: float = 0, until: float = 0, user: dict = Depends(current_user)):
    sv = since or None
    uv = until or None
    return {
        "stats": store.analytics(user["id"], sv, uv),
        "equity": store.equity_curve(user["id"]),
        "trades": store.recent_trades(user["id"], 500),
    }


@app.get("/api/dashboard")
def dashboard(user: dict = Depends(current_user)):
    """Summary KPIs for the pro dashboard: today's realized, 30-day stats, an
    equity sparkline and the day's starting equity (for the equity delta)."""
    now = time.time()
    day_start = now - (now % 86400.0)          # UTC midnight
    eq = store.equity_curve(user["id"], 120)
    spark = [round((e["balance"] or 0) + (e["pnl"] or 0), 2) for e in eq]
    day_eq = None
    for e in eq:                               # first equity point recorded today
        if e["ts"] >= day_start:
            day_eq = round((e["balance"] or 0) + (e["pnl"] or 0), 2)
            break
    return {
        "today": store.trade_stats(user["id"], day_start),
        "stats30": store.trade_stats(user["id"], now - 30 * 86400.0),
        "spark": spark[-60:],
        "day_start_equity": day_eq,
    }


# --- TradingView webhook (public, token in path) ----------------------------
@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    user = store.user_by_webhook(token)
    if not user:
        raise HTTPException(404, "unknown webhook token")
    try:
        payload = await request.json()
    except Exception:
        raw = (await request.body()).decode(errors="ignore").strip()
        try:
            payload = json.loads(raw)
        except Exception:
            raise HTTPException(400, "body is not JSON")
    ent = store.entitlement(user)
    if not ent["ok"]:
        get_session(user["id"]).log(f"Webhook ignored — access {ent['status']}", "warn")
        return {"ok": False, "message": f"access {ent['status']}"}
    s = get_session(user["id"])
    if not s.connected:
        s.log("Webhook received but not connected — ignored", "warn")
        return {"ok": False, "message": "not connected"}
    return s.handle_signal(payload, source="webhook")


# --- live WebSocket ---------------------------------------------------------
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    token = websocket.query_params.get("token", "")
    data = security.read_token(token)
    if not data:
        await websocket.close(code=4401)
        return
    uid = int(data["sub"])
    tv = int(data.get("tv", 0))
    try:
        while True:
            user = store.get_user(uid)
            # Close if the user is gone, suspended, or the token was revoked
            # (e.g. "sign out everywhere" / password change bumps token_version).
            if (not user or not user.get("active", 1)
                    or int(user.get("token_version", 0) or 0) != tv):
                await websocket.close(code=4401)
                return
            # Full state (incl. live access/licence) so the client never loses it.
            await websocket.send_text(json.dumps(full_state(user), default=str))
            await asyncio.sleep(1.5)
    except WebSocketDisconnect:
        return
    except Exception:
        return


# --- server outbound IP (for exchange API IP-whitelisting) ------------------
_SERVER_IP = {"ip": "", "t": 0.0}


def _fetch_public_ip() -> str:
    import urllib.request as _u
    for url in ("https://checkip.amazonaws.com", "https://api.ipify.org",
                "https://ifconfig.me/ip", "https://icanhazip.com"):
        try:
            req = _u.Request(url, headers={"User-Agent": "curl/8"})
            with _u.urlopen(req, timeout=4) as r:
                ip = r.read().decode().strip()
            if ip and 6 <= len(ip) <= 45 and " " not in ip and "\n" not in ip:
                return ip
        except Exception:
            continue
    return ""


def _server_ip() -> str:
    now = time.time()
    if _SERVER_IP["ip"] and now - _SERVER_IP["t"] < 3600:
        return _SERVER_IP["ip"]
    ip = _fetch_public_ip()
    if ip:
        _SERVER_IP["ip"] = ip
        _SERVER_IP["t"] = now
    return _SERVER_IP["ip"]


def _warm_server_ip():
    ip = _fetch_public_ip()
    if ip:
        _SERVER_IP["ip"] = ip
        _SERVER_IP["t"] = time.time()


import threading as _threading
_threading.Thread(target=_warm_server_ip, daemon=True).start()


# --- geolocation for the social-proof popup ("from United States") ----------
def _is_public_ip(ip: str) -> bool:
    if not ip or ip in ("127.0.0.1", "::1", "localhost"):
        return False
    for p in ("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.",
              "172.2", "172.30.", "172.31.", "169.254.", "fc", "fd", "fe80"):
        if ip.startswith(p):
            return False
    return True


def _geolocate_country(ip: str) -> str:
    """Best-effort country NAME from an IP. Tries several free providers so a
    single one being down/rate-limited doesn't lose the location. Degrades to
    "" (the popup then just omits the location)."""
    if not _is_public_ip(ip):
        return ""
    import urllib.request as _u, json as _j

    def _txt(url):
        req = _u.Request(url, headers={"User-Agent": "curl/8"})
        with _u.urlopen(req, timeout=4) as r:
            return r.read().decode().strip()

    def _ok(name):
        return isinstance(name, str) and name and "<" not in name and 2 <= len(name) <= 56

    # Providers that return a full country NAME (not a 2-letter code).
    providers = [
        lambda: _txt(f"https://ipapi.co/{ip}/country_name/"),
        lambda: (_j.loads(_txt(f"https://ipwho.is/{ip}")) or {}).get("country", ""),
        lambda: (_j.loads(_txt(f"http://ip-api.com/json/{ip}?fields=country")) or {}).get("country", ""),
        lambda: _txt(f"https://get.geojs.io/v1/ip/country/full/{ip}"),
    ]
    for fn in providers:
        try:
            name = fn()
            if _ok(name):
                return name.strip()
        except Exception:
            continue
    return ""


def _geolocate_user_async(user_id: int, ip: str) -> None:
    def _run():
        c = _geolocate_country(ip)
        if c:
            store.set_country(user_id, c)
    _threading.Thread(target=_run, daemon=True).start()


@app.get("/api/server_ip")
def server_ip(user: dict = Depends(current_user)):
    return {"ip": _server_ip()}


@app.get("/api/social_proof")
def social_proof():
    """Public: recent real purchases for the dashboard + marketing-site popup.
    No auth, no secrets. CORS-open so the static marketing sites can read it."""
    if (store.kv_get("social_proof_on") or "1") != "1":
        payload = {"enabled": False, "sales": []}
    else:
        now = time.time()
        out = [{"name": s["name"], "country": s["country"],
                "ago": int(max(0, now - (s["ts"] or now)))}
               for s in store.social_proof_sales(12)]
        payload = {"enabled": True, "sales": out}
    return JSONResponse(payload, headers={"Access-Control-Allow-Origin": "*",
                                          "Cache-Control": "public, max-age=30"})


# --- no-cache for app JS/CSS so deploys always load fresh code --------------
@app.get("/static/app.js")
def _static_app_js():
    return FileResponse(os.path.join(WEB_DIR, "app.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/static/style.css")
def _static_style_css():
    return FileResponse(os.path.join(WEB_DIR, "style.css"), media_type="text/css",
                        headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/static/admin.js")
def _static_admin_js():
    return FileResponse(os.path.join(WEB_DIR, "admin.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache, must-revalidate"})


# --- security: login history + logout everywhere ----------------------------
@app.get("/api/security/logins")
def security_logins(user: dict = Depends(current_user)):
    return {"logins": store.recent_logins(user["id"], 10)}


@app.post("/api/security/logout_all")
def security_logout_all(user: dict = Depends(current_user)):
    tv = store.bump_token_version(user["id"])
    return {"ok": True, "token": security.make_token(user["id"], user["email"], tv)}


# --- web push ---------------------------------------------------------------
@app.get("/api/push/vapid")
def push_vapid(user: dict = Depends(current_user)):
    return {"key": webpush.public_key()}


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request, user: dict = Depends(current_user)):
    d = await body(request)
    sub = d.get("subscription") or d
    ep = (sub or {}).get("endpoint")
    if not ep:
        raise HTTPException(400, "no endpoint")
    store.add_push_sub(user["id"], ep, json.dumps(sub))
    return {"ok": True}


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(request: Request, user: dict = Depends(current_user)):
    d = await body(request)
    ep = d.get("endpoint")
    if ep:
        store.remove_push_sub(ep)
    return {"ok": True}


# --- service worker (served at root so its scope is the whole app) ----------
@app.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(WEB_DIR, "sw.js"), media_type="application/javascript")


@app.get("/manifest.webmanifest")
def web_manifest():
    return FileResponse(os.path.join(WEB_DIR, "manifest.webmanifest"), media_type="application/manifest+json")


# --- static frontend --------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


@app.get("/admin")
def admin_page():
    return FileResponse(os.path.join(WEB_DIR, "admin.html"))


@app.get("/healthz")
def healthz():
    return {"ok": True}


if os.path.isdir(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# --- daily PnL summary scheduler (Telegram + optional email) ----------------
def _summary_loop():
    from . import mailer
    from .session import TraderSession
    while True:
        try:
            now = time.gmtime()
            today = time.strftime("%Y-%m-%d", now)
            hour = int(os.environ.get("PROMETHEUS_SUMMARY_HOUR", "23"))
            if now.tm_hour >= hour:
                for u in store.all_users():
                    if u.get("last_summary") == today:
                        continue
                    s = json.loads(u["settings"] or "{}")
                    if s.get("daily_summary", True):
                        pnl, n, wins = store.realized_pnl_since(u["id"], time.time() - 86400)
                        if n > 0:
                            msg = (f"📊 Daily recap — {n} trade(s), {wins} win(s), "
                                   f"realized PnL {pnl:+.2f} USDT")
                            tok, chat = s.get("telegram_token", ""), s.get("telegram_chat", "")
                            if tok and chat:
                                TraderSession._tg_send(tok, chat, msg)
                            if mailer.configured():
                                mailer.send_email(u["email"], "Your Prometheus daily recap", msg)
                    store.mark_summary(u["id"], today)
        except Exception:
            pass
        time.sleep(900)   # re-check every 15 minutes


_threading.Thread(target=_summary_loop, daemon=True).start()
