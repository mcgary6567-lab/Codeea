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

# Put the trading engine (flat-import modules) on the path BEFORE importing it.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "engine"))

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import backtest as bt
import strategy as strat

from . import security, store
from .config_web import PUBLIC_URL
from .session import get_session, public_ohlcv, public_prices

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
        user = store.create_user(email, pw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"token": security.make_token(user["id"], user["email"]), "email": user["email"]}


@app.post("/api/login")
async def login(request: Request):
    d = await body(request)
    user = store.get_user_by_email(d.get("email", ""))
    if not user or not security.verify_password(d.get("password", ""), user["pw_hash"]):
        raise HTTPException(401, "bad credentials")
    return {"token": security.make_token(user["id"], user["email"]), "email": user["email"]}


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
    snap["is_admin"] = bool(user.get("is_admin"))
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
    limit = min(int(d.get("limit", 1000)), 1500)
    candles = public_ohlcv(exch, symbol, tf, limit)
    if len(candles) < 60:
        raise HTTPException(400, "not enough candle data (need ccxt + connectivity)")
    params = strat.StrategyParams()
    for k, v in (d.get("params") or {}).items():
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
    result = bt.run_backtest(candles, params, cfg)
    summary = {k: (round(v, 4) if isinstance(v, float) and v not in (float("inf"), float("-inf")) else v)
               for k, v in result.stats.items()}
    return {
        "symbol": symbol, "timeframe": tf, "candles": len(candles),
        "start_equity": cfg.start_equity,
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
