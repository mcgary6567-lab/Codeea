"""Per-user trading session — the web equivalent of the desktop backend.

One :class:`TraderSession` per logged-in user holds that user's ccxt connection,
guardrails, settings, positions and log, plus a background poll loop. Manual
trades, TradingView webhooks and the built-in strategy all funnel through the
same guardrail -> sizing -> order -> bracket pipeline, exactly like the app.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from collections import deque

import exchange as ex          # engine module (on sys.path)
import strategy as strat
from guardrails import Guardrails

from . import store
from .config_web import POLL_INTERVAL

# Keyless public ccxt clients for candle data (backtest / built-in strategy),
# cached per exchange id so we don't rebuild them each poll.
_PUBLIC: dict = {}


def public_ohlcv(exchange_id: str, symbol: str, timeframe: str, limit: int = 300) -> list:
    if not ex.CCXT_AVAILABLE:
        return []
    try:
        client = _PUBLIC.get(exchange_id)
        if client is None:
            import ccxt
            if exchange_id not in ccxt.exchanges:
                return []
            client = getattr(ccxt, exchange_id)({"enableRateLimit": True})
            _PUBLIC[exchange_id] = client
        return client.fetch_ohlcv(symbol, timeframe, limit=limit)
    except Exception:
        return []


# Cached public ticker prices (per exchange) to power the dashboard price strip.
_PRICE_CACHE: dict = {}


def public_prices(exchange_id: str, symbols: list) -> dict:
    """Last price + 24h % change for a few symbols (cached ~8s)."""
    import time as _t
    key = exchange_id or "binance"
    now = _t.time()
    cached = _PRICE_CACHE.get(key)
    if cached and now - cached["t"] < 8:
        base = cached["data"]
    else:
        base = {}
        if ex.CCXT_AVAILABLE:
            try:
                client = _PUBLIC.get(key)
                if client is None:
                    import ccxt
                    if key in ccxt.exchanges:
                        client = getattr(ccxt, key)({"enableRateLimit": True})
                        _PUBLIC[key] = client
                if client is not None:
                    for s, t in (client.fetch_tickers(symbols) or {}).items():
                        base[s] = {"price": t.get("last"), "pct": t.get("percentage")}
            except Exception:
                base = {}
        _PRICE_CACHE[key] = {"t": now, "data": base}
    return {s: base.get(s, base.get(ex.normalize_symbol(s), {})) for s in symbols}


DEFAULT_SETTINGS = {
    "sizing_mode": "risk_stop",      # fixed | fixed_quote | risk_balance | risk_stop
    "fixed_size": 0.003,
    "fixed_quote": 25.0,
    "risk_percent": 1.0,
    "order_type": "market",          # market | limit
    "leverage": 0,
    "margin_mode": "",               # "" | cross | isolated
    "tp1_fraction": 0.5,
    "auto_bracket": True,
    "safe_mode": False,              # LIVE trading by default
    "read_only": False,
    "max_open": 4,
    "daily_loss": 0.0,
    "daily_profit": 0.0,
    "cooldown": 0,
    "dedupe": 5,
    "webhook_passphrase": "",
    "telegram_token": "",
    "telegram_chat": "",
    "strategy_filter": "Prometheus",
    "strategy_enabled": True,        # built-in strategy ON by default
    "strategy_symbols": "BTC/USDT",
    "strategy_timeframe": "15m",
    "strategy_params": {},           # overrides StrategyParams fields
    "move_be_on_tp1": False,
}


class TraderSession:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.em = ex.ExchangeManager()
        self.guard = Guardrails()
        self.settings = {**DEFAULT_SETTINGS, **(store.load_settings(user_id) or {})}
        self.positions: list = []
        self.balance = 0.0
        self.pnl = 0.0
        self.connected = False
        self.exchange_id = ""
        self.market_type = "spot"
        self.log_ring: deque = deque(maxlen=400)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._poll: threading.Thread | None = None
        self._last_equity_snap = 0.0
        # built-in strategy
        self.strategy_on = False
        self._strat: threading.Thread | None = None
        self._strat_primed: dict = {}
        self._apply_guardrails()

    # -- logging + notifications --------------------------------------------
    def log(self, msg: str, level: str = "info") -> None:
        self.log_ring.appendleft({"ts": time.time(), "level": level, "msg": msg})

    def notify(self, msg: str) -> None:
        """Best-effort Telegram push (per-user bot token + chat id)."""
        token = self.settings.get("telegram_token", "").strip()
        chat = self.settings.get("telegram_chat", "").strip()
        if not token or not chat:
            return
        threading.Thread(target=self._tg_send, args=(token, chat, msg), daemon=True).start()

    @staticmethod
    def _tg_send(token: str, chat: str, msg: str) -> None:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": chat, "text": f"🟦 Prometheus\n{msg}"}).encode()
            urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=8)
        except Exception:
            pass

    # -- settings ------------------------------------------------------------
    def _apply_guardrails(self) -> None:
        s = self.settings
        self.guard.configure(
            max_open=s.get("max_open", 0), daily_loss=s.get("daily_loss", 0),
            cooldown=s.get("cooldown", 0), dedupe=s.get("dedupe", 0),
            daily_profit=s.get("daily_profit", 0),
        )

    def set_settings(self, patch: dict) -> None:
        with self._lock:
            self.settings.update(patch or {})
            store.save_settings(self.user_id, self.settings)
            self._apply_guardrails()
            self.settings["safe_mode"] = False
            self.em.safe_mode = False
            self.em.read_only = bool(self.settings.get("read_only", False))
        self.log(f"Settings updated ({', '.join((patch or {}).keys())})")

    def _params(self) -> "strat.StrategyParams":
        p = strat.StrategyParams()
        for k, v in (self.settings.get("strategy_params") or {}).items():
            if hasattr(p, k):
                setattr(p, k, v)
        return p

    # -- connection ----------------------------------------------------------
    def connect(self) -> str:
        keys = store.load_keys(self.user_id)
        if not keys:
            raise ValueError("no API keys saved")
        self.settings["safe_mode"] = False   # live trading only (safe mode removed)
        with self._lock:
            self.em.connect(
                keys["exchange"], keys.get("api_key", ""), keys.get("api_secret", ""),
                password=keys.get("password", ""),
                safe_mode=False,
                read_only=bool(self.settings.get("read_only", False)),
                market_type=keys.get("market_type", "spot"),
            )
            self.connected = self.em.connected
            self.exchange_id = keys["exchange"]
            self.market_type = keys.get("market_type", "spot")
        self.log(f"Connected to {self.exchange_id} ({self.market_type})"
                 + (" — SAFE MODE" if self.settings.get("safe_mode") else ""), "ok")
        self.notify(f"Connected to {self.exchange_id} ({self.market_type})"
                    + (" — SAFE MODE" if self.settings.get("safe_mode") else ""))
        self._ensure_poll()
        self.refresh()
        # Built-in strategy is ON by default — auto-start it once connected.
        if self.settings.get("strategy_enabled", True):
            self.start_strategy()
        return "connected"

    def disconnect(self) -> None:
        with self._lock:
            self.stop_strategy()
            self.em.disconnect()
            self.connected = False
        self.log("Disconnected")

    def _ensure_poll(self) -> None:
        if self._poll and self._poll.is_alive():
            return
        self._stop.clear()
        self._poll = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll.start()

    def _poll_loop(self) -> None:
        while not self._stop.is_set() and self.connected:
            try:
                self.refresh()
            except Exception as exc:  # noqa: BLE001
                self.log(f"refresh error: {exc}", "warn")
            self._stop.wait(POLL_INTERVAL)

    def refresh(self) -> None:
        if not self.connected:
            return
        with self._lock:
            try:
                self.balance = self.em.fetch_balance()
            except Exception:
                pass
            self.positions = self.em.fetch_positions()
            self.pnl = self.em.total_pnl(self.positions)
        now = time.time()
        if now - self._last_equity_snap > 60:
            self._last_equity_snap = now
            store.record_equity(self.user_id, self.balance, self.pnl)
        self.guard.update_equity(self.balance + self.pnl)

    # -- pricing / sizing ----------------------------------------------------
    def get_price(self, symbol: str) -> float:
        c = self.em.fetch_ohlcv(symbol, "1m", 2)
        if c:
            return float(c[-1][4])
        c = public_ohlcv(self.exchange_id or "binance", ex.normalize_symbol(symbol), "1m", 2)
        return float(c[-1][4]) if c else 0.0

    def _size(self, price: float, entry: float = 0.0, stop: float = 0.0):
        s = self.settings
        mode = s.get("sizing_mode", "risk_stop")
        fixed = s.get("fixed_quote") if mode == "fixed_quote" else s.get("fixed_size")
        return ex.size_order(mode, float(fixed or 0), float(s.get("risk_percent", 1)),
                             self.balance, price, entry, stop)

    def _open_pairs(self) -> set:
        return {p.pair for p in self.positions}

    # -- trade pipeline ------------------------------------------------------
    def _apply_lev_margin(self, symbol: str) -> None:
        lev = int(self.settings.get("leverage", 0) or 0)
        mm = self.settings.get("margin_mode", "")
        if (lev or mm) and self.market_type == "futures":
            try:
                self.em.apply_leverage_margin(symbol, lev, mm)
            except Exception:
                pass

    def place_manual(self, side: str, symbol: str, size: float | None = None) -> dict:
        symbol = ex.normalize_symbol(symbol)
        price = self.get_price(symbol)
        order_type = self.settings.get("order_type", "market")
        limit_px = price if order_type == "limit" else None
        if size and size > 0:
            amount = float(size)
            reason = "manual size"
        else:
            amount, reason = self._size(price)
        if amount <= 0:
            return {"ok": False, "message": "size resolved to 0"}
        self._apply_lev_margin(symbol)
        res = self.em.place_order(symbol, side, amount, order_type, limit_px)
        store.record_trade(self.user_id, symbol=symbol, side=side, kind="manual",
                           status="filled" if res.ok else "rejected",
                           amount=amount, price=price, note=res.message)
        self.log(f"Manual {side.upper()} {symbol}: {res.message} [{reason}]",
                 "ok" if res.ok else "error")
        self.refresh()
        return {"ok": res.ok, "message": res.message, "simulated": res.simulated}

    def handle_signal(self, payload: dict, source: str = "webhook") -> dict:
        """Entry point for TradingView webhooks AND the built-in strategy."""
        action = str(payload.get("action", "")).lower()
        if action not in ("buy", "sell"):
            return {"ok": False, "message": "action must be buy/sell"}
        symbol = ex.normalize_symbol(str(payload.get("symbol", "")))
        if not symbol:
            return {"ok": False, "message": "missing symbol"}

        # webhook passphrase gate
        need = self.settings.get("webhook_passphrase", "")
        if source == "webhook" and need and str(payload.get("passphrase", "")) != need:
            self.log(f"Webhook rejected: bad passphrase for {symbol}", "warn")
            return {"ok": False, "message": "bad passphrase"}

        # strategy-tag filter
        filt = (self.settings.get("strategy_filter") or "").strip()
        tag = str(payload.get("comment", "")).strip()
        if source == "webhook" and filt and tag and tag != filt:
            return {"ok": False, "message": f"ignored (strategy {tag} != {filt})"}

        entry = float(payload.get("entry", 0) or 0)
        sl = float(payload.get("sl", 0) or 0)
        tp1 = float(payload.get("tp1", 0) or 0)
        tp2 = float(payload.get("tp2", 0) or 0)
        side = action

        allowed, reason = self.guard.check_entry(symbol, side, self._open_pairs())
        self.guard.record_signal(symbol, side)  # mark seen (for the next dedupe window)
        if not allowed:
            self.log(f"Blocked {side.upper()} {symbol}: {reason}", "warn")
            store.record_trade(self.user_id, symbol=symbol, side=side, kind="signal",
                               status="blocked", note=reason)
            return {"ok": False, "message": f"blocked: {reason}"}

        price = entry if entry > 0 else self.get_price(symbol)
        amount, reason = self._size(price, entry, sl)
        if amount <= 0:
            return {"ok": False, "message": "size 0"}

        self._apply_lev_margin(symbol)
        order_type = self.settings.get("order_type", "market")
        limit_px = price if order_type == "limit" else None
        res = self.em.place_order(symbol, side, amount, order_type, limit_px)
        store.record_trade(self.user_id, symbol=symbol, side=side, kind="entry",
                           status="filled" if res.ok else "rejected",
                           amount=amount, price=price, note=f"{source}: {res.message}")
        self.log(f"{source} {side.upper()} {symbol} x{amount}: {res.message} [{reason}]",
                 "ok" if res.ok else "error")
        if not res.ok:
            return {"ok": False, "message": res.message}

        self.notify(f"{side.upper()} {symbol} x{amount} @ ~{price:g}"
                    + (f" | SL {sl:g}" if sl else "") + (f" TP {tp1:g}/{tp2:g}" if tp1 else ""))
        self.guard.record_entry(symbol)

        # bracket: reduce-only SL + scaled TP legs
        if self.settings.get("auto_bracket", True):
            xside = ex.exit_side(side)
            if sl > 0:
                r = self.em.place_reduce_order(symbol, xside, amount, sl, "sl")
                self.log(f"SL {symbol} @ {sl}: {r.message}", "ok" if r.ok else "warn")
            for px, qty in ex.plan_take_profits(amount, tp1, tp2, float(self.settings.get("tp1_fraction", 0.5))):
                r = self.em.place_reduce_order(symbol, xside, qty, px, "tp")
                self.log(f"TP {symbol} {qty} @ {px}: {r.message}", "ok" if r.ok else "warn")
        self.refresh()
        return {"ok": True, "message": res.message, "amount": amount}

    def close_position(self, symbol: str, fraction: float = 1.0) -> dict:
        for p in self.positions:
            if p.pair == symbol or ex.normalize_symbol(p.pair) == ex.normalize_symbol(symbol):
                res = self.em.close_position(p, fraction)
                store.record_trade(self.user_id, symbol=p.pair, side="close", kind="close",
                                   status="filled" if res.ok else "rejected",
                                   amount=p.size * fraction, price=p.current, pnl=p.pnl,
                                   note=res.message)
                self.log(f"Close {p.pair}: {res.message}", "ok" if res.ok else "error")
                self.notify(f"Closed {p.pair} | PnL {p.pnl:+.4f}")
                self.refresh()
                return {"ok": res.ok, "message": res.message}
        return {"ok": False, "message": "position not found"}

    def close_all(self) -> dict:
        n = 0
        for p in list(self.positions):
            r = self.close_position(p.pair)
            n += 1 if r.get("ok") else 0
        self.log(f"PANIC close-all: {n} position(s) closed", "warn")
        return {"ok": True, "message": f"closed {n}"}

    # -- chart data (candles + strategy overlays) ----------------------------
    def chart_data(self, symbol: str, tf: str, limit: int = 300) -> dict:
        raw = self.em.fetch_ohlcv(symbol, tf, limit) or public_ohlcv(
            self.exchange_id or "binance", ex.normalize_symbol(symbol), tf, limit)
        if len(raw) < 40:
            return {"candles": [], "fast": [], "slow": [], "trend": [], "markers": []}
        closed = raw[:-1]
        params = self._params()
        o, h, low, cl, fast, slow, trend = strat.crossover_arrays(closed, params)
        rnd = lambda arr: [None if v is None else round(v, 6) for v in arr]
        markers = []
        for i, ev in strat.evaluate_all_crossover(closed, params, symbol):
            if ev.get("act") == "enter":
                entry, sl = float(ev["entry"]), float(ev["sl"])
                risk = abs(entry - sl)
                sign = 1 if ev["side"] == "long" else -1
                markers.append({
                    "i": i, "type": "enter", "side": ev["side"], "price": entry, "sl": sl,
                    "tp1": entry + sign * risk * params.tp1_r if params.tp1_r else None,
                    "tp2": entry + sign * risk * params.tp2_r if params.tp2_r else None,
                })
            else:
                markers.append({"i": i, "type": "exit", "side": ev.get("side"),
                                "price": cl[i], "reason": ev.get("reason", "")})
        return {
            "symbol": symbol, "timeframe": tf,
            "candles": [[int(c[0]), c[1], c[2], c[3], c[4], c[5]] for c in closed],
            "fast": rnd(fast), "slow": rnd(slow), "trend": rnd(trend),
            "fast_ema": params.fast_ema, "slow_ema": params.slow_ema, "trend_ema": params.trend_ema,
            "markers": markers,
        }

    # -- built-in strategy ---------------------------------------------------
    def start_strategy(self) -> None:
        if self.strategy_on:
            return
        self.strategy_on = True
        self._strat_primed = {}
        self._strat = threading.Thread(target=self._strategy_loop, daemon=True)
        self._strat.start()
        self.log("Built-in strategy ENABLED", "ok")

    def stop_strategy(self) -> None:
        if self.strategy_on:
            self.log("Built-in strategy disabled")
        self.strategy_on = False

    def _strategy_loop(self) -> None:
        while self.strategy_on and self.connected:
            # Stop trading if the licence/trial lapsed mid-session.
            ent = store.entitlement(store.get_user(self.user_id) or {})
            if not ent["ok"]:
                self.log(f"Strategy stopped — access {ent['status']}", "warn")
                self.strategy_on = False
                break
            symbols = [s.strip() for s in str(self.settings.get("strategy_symbols", "")).split(",") if s.strip()]
            tf = self.settings.get("strategy_timeframe", "1h")
            params = self._params()
            for sym in symbols:
                try:
                    self._strategy_step(sym, tf, params)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"strategy {sym} error: {exc}", "warn")
            for _ in range(15):
                if not self.strategy_on:
                    break
                time.sleep(1)

    def _strategy_step(self, symbol: str, tf: str, params) -> None:
        raw = self.em.fetch_ohlcv(symbol, tf, 300) or public_ohlcv(
            self.exchange_id or "binance", ex.normalize_symbol(symbol), tf, 300)
        if len(raw) < 60:
            return
        closed = raw[:-1]                       # drop the live forming candle
        last_ts = closed[-1][0]
        primed = self._strat_primed.get(symbol)
        if primed is None:                       # first sight: prime, don't trade
            self._strat_primed[symbol] = last_ts
            return
        if last_ts == primed:
            return                               # no new closed candle yet
        self._strat_primed[symbol] = last_ts
        for evt in strat.evaluate_crossover(closed, params, symbol):
            if evt.get("act") != "enter":
                continue
            side = "buy" if evt["side"] == "long" else "sell"
            entry = float(evt.get("entry", 0))
            sl = float(evt.get("sl", 0))
            risk = abs(entry - sl)
            sign = 1 if side == "buy" else -1
            tp1 = entry + sign * params.tp1_r * risk if params.tp1_r else 0
            tp2 = entry + sign * params.tp2_r * risk if params.tp2_r else 0
            self.handle_signal({"action": side, "symbol": symbol, "entry": entry,
                                "sl": sl, "tp1": tp1, "tp2": tp2, "comment": "Prometheus"},
                               source="strategy")

    # -- snapshot for the UI -------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "connected": self.connected,
            "exchange": self.exchange_id,
            "market_type": self.market_type,
            "safe_mode": bool(self.settings.get("safe_mode", True)),
            "read_only": bool(self.settings.get("read_only", False)),
            "balance": round(self.balance, 2),
            "pnl": round(self.pnl, 2),
            "strategy_on": self.strategy_on,
            "strategy_enabled": bool(self.settings.get("strategy_enabled", True)),
            "guard_tripped": self.guard.tripped,
            "positions": [
                {"pair": p.pair, "side": p.side, "size": p.size, "entry": p.entry,
                 "current": p.current, "pnl": round(p.pnl, 4), "status": p.status}
                for p in self.positions
            ],
            "log": list(self.log_ring)[:120],
            "settings": self.settings,
        }


# --- session registry -------------------------------------------------------
_SESSIONS: dict = {}
_REG_LOCK = threading.RLock()


def get_session(user_id: int) -> TraderSession:
    with _REG_LOCK:
        s = _SESSIONS.get(user_id)
        if s is None:
            s = TraderSession(user_id)
            _SESSIONS[user_id] = s
        return s
