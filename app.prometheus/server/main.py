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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import backtest as bt
import strategy as strat

from . import security, store
from .config_web import PUBLIC_URL
from .session import get_session, public_ohlcv, public_ohlcv_days, public_prices

_TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
               "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400}


def _public_client_tf_seconds(tf: str) -> int:
    return _TF_SECONDS.get(tf, 3600)

store.init_db()
app = FastAPI(title="Prometheus Web", version="1.0.0")

WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")


# --- auth helpers -----------------------------------------------------------
def current_user(authorization: str = Header(default="")) -> dict:
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    data = security.read_token(token)
    if not data:
        raise HTTPException(401, "invalid or missing token")
    user = store.get_user(int(data["sub"]))
    if not user:
        raise HTTPException(401, "user not found")
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
    return {"token": security.make_token(user["id"], user["email"]), "email": user["email"]}


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
    return {"token": security.make_token(user["id"], user["email"]), "email": user["email"]}


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
    u = store.get_user(uid)
    return {"token": security.make_token(u["id"], u["email"]), "email": u["email"]}


# --- 2FA (TOTP) -------------------------------------------------------------
@app.post("/api/2fa/setup")
def twofa_setup(user: dict = Depends(current_user)):
    secret = security.new_totp_secret()
    store.set_totp_secret(user["id"], secret)          # stored, not yet enabled
    return {"secret": secret, "uri": security.totp_uri(secret, user["email"])}


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
    u = store.get_user(user["id"])
    return {"ok": True, "token": security.make_token(u["id"], u["email"]), "email": u["email"]}


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
    return s.place_manual(side, d.get("symbol", "BTC/USDT"), d.get("size"))


@app.post("/api/close")
async def close(request: Request, user: dict = Depends(current_user)):
    d = await body(request)
    return get_session(user["id"]).close_position(d.get("symbol", ""), float(d.get("fraction", 1.0)))


@app.post("/api/close_all")
def close_all(user: dict = Depends(current_user)):
    return get_session(user["id"]).close_all()


# --- settings & strategy ----------------------------------------------------
@app.post("/api/settings")
async def settings(request: Request, user: dict = Depends(current_user)):
    d = await body(request)
    get_session(user["id"]).set_settings(d)
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
    if d.get("params") is not None or d.get("symbols") is not None or d.get("timeframe") is not None:
        patch = {}
        if d.get("symbols") is not None:
            patch["strategy_symbols"] = d["symbols"]
        if d.get("timeframe") is not None:
            patch["strategy_timeframe"] = d["timeframe"]
        if d.get("params") is not None:
            patch["strategy_params"] = d["params"]
        s.set_settings(patch)
    if d.get("enable") is True:
        require_entitled(user)
        s.set_settings({"strategy_enabled": True})   # persist so it auto-starts on connect
        if not s.connected:
            raise HTTPException(400, "connect an exchange first")
        s.start_strategy()
    elif d.get("enable") is False:
        s.set_settings({"strategy_enabled": False})
        s.stop_strategy()
    return {"ok": True, "strategy_on": s.strategy_on, "strategy_enabled": s.settings.get("strategy_enabled")}


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
    try:
        cfg.bar_seconds = float(_public_client_tf_seconds(tf))
    except Exception:
        pass
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
    data = s.chart_data(symbol, timeframe, min(int(limit), 500))
    if not data["candles"]:
        raise HTTPException(400, "no candle data (needs ccxt + connectivity)")
    return data


# --- admin ------------------------------------------------------------------
@app.get("/api/admin/users")
def admin_users(admin: dict = Depends(require_admin)):
    return {"users": store.list_users()}


@app.post("/api/admin/action")
async def admin_action(request: Request, admin: dict = Depends(require_admin)):
    d = await body(request)
    uid = int(d.get("user_id", 0))
    action = d.get("action", "")
    if not store.get_user(uid):
        raise HTTPException(404, "user not found")
    if action == "suspend":
        store.set_active(uid, False)
    elif action == "activate":
        store.set_active(uid, True)
    elif action == "grant":
        store.grant_licence(uid, float(d.get("days", 30)))
    elif action == "revoke":
        store.revoke_licence(uid)
    elif action == "make_admin":
        store.set_admin(uid, True)
    elif action == "remove_admin":
        store.set_admin(uid, False)
    else:
        raise HTTPException(400, "unknown action")
    return {"ok": True, "user": {k: v for k, v in store.get_user(uid).items()
                                 if k not in ("pw_hash", "keys_blob")}}


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
    try:
        while True:
            user = store.get_user(uid)
            if not user:
                await websocket.close(code=4401)
                return
            # Full state (incl. live access/licence) so the client never loses it.
            await websocket.send_text(json.dumps(full_state(user), default=str))
            await asyncio.sleep(1.5)
    except WebSocketDisconnect:
        return
    except Exception:
        return


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
    import threading  # noqa: F401
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


import threading as _threading  # noqa: E402
_threading.Thread(target=_summary_loop, daemon=True).start()
