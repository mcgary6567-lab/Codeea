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
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

import exchange as ex          # engine module (on sys.path)
import strategy as strat
from guardrails import Guardrails

from . import store, webpush, news
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


def _public_client(exchange_id: str):
    if not ex.CCXT_AVAILABLE:
        return None
    client = _PUBLIC.get(exchange_id)
    if client is None:
        import ccxt
        if exchange_id not in ccxt.exchanges:
            return None
        client = getattr(ccxt, exchange_id)({"enableRateLimit": True})
        _PUBLIC[exchange_id] = client
    return client


def public_ohlcv_days(exchange_id: str, symbol: str, timeframe: str, days: float,
                      max_candles: int = 8000) -> list:
    """Paginated candle history for the last ``days`` — walks ``since`` forward so
    date-range backtests aren't limited to one exchange page (~1000 candles)."""
    import time as _t
    client = _public_client(exchange_id)
    if client is None:
        return []
    try:
        tf_ms = client.parse_timeframe(timeframe) * 1000
    except Exception:
        tf_ms = 60_000
    now_ms = int(_t.time() * 1000)
    since = now_ms - int(days * 86_400_000)
    out: list = []
    for _ in range(40):                      # hard cap on requests
        try:
            batch = client.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        except Exception:
            break
        if not batch:
            break
        out.extend(batch)
        nxt = batch[-1][0] + tf_ms
        if nxt <= since or nxt >= now_ms or len(batch) < 1000 or len(out) >= max_candles:
            break
        since = nxt
    # de-dupe by timestamp (pagination overlaps can repeat the boundary candle)
    seen = set(); uniq = []
    for c in out:
        if c[0] not in seen:
            seen.add(c[0]); uniq.append(c)
    return uniq[:max_candles]


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


_GS_CACHE = {"v": None, "t": 0.0}


def _global_strategy() -> dict:
    """Admin-managed strategy, cached ~5s so the per-user loops share it cheaply."""
    now = time.time()
    if _GS_CACHE["v"] is not None and now - _GS_CACHE["t"] < 5:
        return _GS_CACHE["v"]
    g = store.get_global_strategy()
    _GS_CACHE["v"] = g
    _GS_CACHE["t"] = now
    return g


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
    "paper_mode": False,             # paper/dry-run: simulate orders, no real fills
    "read_only": False,
    "max_open": 3,
    "daily_loss": 0.0,               # absolute $ cap (fallback; % below overrides when > 0)
    "daily_profit": 0.0,
    "daily_loss_pct": 5.0,           # stop trading after -5% of the day's starting equity
    "daily_profit_pct": 0.0,         # 0 = off (let winners run)
    "cooldown": 0,
    "dedupe": 5,
    "webhook_passphrase": "",
    "telegram_token": "",
    "telegram_chat": "",
    "daily_summary": True,           # daily PnL recap via Telegram/email
    "alert_skips": False,            # Telegram alert when a strategy signal is blocked
    "news_trading": True,            # True = trade through news; False = pause ±1h around high-impact events
    "strategy_filter": "Trevolto",
    "strategy_enabled": True,        # built-in strategy ON by default
    "strategy_symbols": "BTC/USDT",
    "strategy_timeframe": "15m",
    "strategy_params": {},           # overrides StrategyParams fields
    "move_be_on_tp1": False,
}

# Execution/risk keys the admin can push globally to managed customers.
GLOBAL_EXEC_KEYS = (
    "sizing_mode", "fixed_size", "fixed_quote", "risk_percent", "order_type", "leverage",
    "margin_mode", "tp1_fraction", "auto_bracket", "max_open", "daily_loss_pct",
    "daily_profit_pct", "cooldown", "dedupe",
)
# Baseline managed config applied to EVERY managed customer (old + new) even before the
# admin customises it; the admin's saved global execution overrides these per key.
MANAGED_EXEC_DEFAULTS = {
    "sizing_mode": "risk_stop", "risk_percent": 1.0, "order_type": "market",
    "auto_bracket": True, "tp1_fraction": 0.5, "max_open": 3,
    "daily_loss_pct": 5.0, "daily_profit_pct": 0.0, "dedupe": 5,
}


class TraderSession:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.em = ex.ExchangeManager()
        self.guard = Guardrails()
        self.settings = {**DEFAULT_SETTINGS, **(store.load_settings(user_id) or {})}
        # One-time migration: the strategy was rewritten (EMA100 filter, new
        # params). Drop strategy params saved under the old schema so users pick
        # up the new correct defaults (trend EMA 100, 1:2 partial, etc.).
        if self.settings.get("strat_schema") != 6:
            self.settings["strategy_params"] = {}      # -> new StrategyParams defaults
            self.settings["strategy_timeframe"] = "15m"
            self.settings["strat_schema"] = 6
            store.save_settings(user_id, self.settings)
        self.positions: list = []
        self.balance = 0.0
        self.pnl = 0.0
        self.connected = False
        self.exchange_id = ""
        self.market_type = "spot"
        self.log_ring: deque = deque(maxlen=400)
        try:                                   # preload the persisted log so history survives restarts
            self.log_ring.extend(store.recent_activity(user_id, 400))   # newest-first
        except Exception:
            pass
        self.alert_ring: deque = deque(maxlen=50)   # in-app/browser alert feed
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._poll: threading.Thread | None = None
        self._last_equity_snap = 0.0
        # built-in strategy
        self.strategy_on = False
        self._strat: threading.Thread | None = None
        self._strat_primed: dict = {}
        self._strat_hold: dict = {}
        self._sig_alerted: dict = {}       # per-symbol: fired the >=85% alert this run
        self.signal: dict = None           # latest signal-strength meter {pct,state,side,symbol}
        self._news_alerted: str = ""       # event key we've already warned about (blackout)
        self._strat_version = None      # last logged "why held" reason per symbol
        self._apply_global_exec()       # managed users pick up the admin's global exec/risk
        self.key_withdraw_warn = False
        self._tg_offset = 0
        self._tg_thread = threading.Thread(target=self._tg_cmd_loop, daemon=True)
        self._tg_thread.start()

    # -- logging + notifications --------------------------------------------
    def _mode(self) -> str:
        return "paper" if self.settings.get("paper_mode") else "live"

    def log(self, msg: str, level: str = "info") -> None:
        ts = time.time()
        self.log_ring.appendleft({"ts": ts, "level": level, "msg": msg})
        try:                                   # persist so the log survives restarts
            store.record_log(self.user_id, ts, level, msg)
        except Exception:
            pass

    def clear_log(self) -> None:
        self.log_ring.clear()
        try:
            store.clear_activity(self.user_id)
        except Exception:
            pass

    def notify(self, msg: str) -> None:
        """Push an alert to the in-app feed, then best-effort Telegram."""
        self.alert_ring.appendleft({"ts": time.time(), "msg": msg})
        webpush.push_to_user(self.user_id, msg)
        token = self.settings.get("telegram_token", "").strip()
        chat = self.settings.get("telegram_chat", "").strip()
        if not token or not chat:
            return
        threading.Thread(target=self._tg_send, args=(token, chat, msg), daemon=True).start()

    @staticmethod
    def _tg_send(token: str, chat: str, msg: str) -> None:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": chat, "text": f"🔥 Trevolto\n{msg}"}).encode()
            urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=8)
        except Exception:
            pass

    @staticmethod
    def _looks_like_token(v: str) -> bool:
        """A Telegram bot token is <digits>:<~35 char secret> (from @BotFather)."""
        if ":" not in v:
            return False
        a, b = v.split(":", 1)
        return a.isdigit() and len(a) >= 6 and len(b) >= 30

    @staticmethod
    def telegram_test(token: str, chat: str) -> tuple:
        """Synchronous test send — returns (ok, message) so the UI can report."""
        token, chat = (token or "").strip(), (chat or "").strip()
        if not token or not chat:
            return False, "Enter both a bot token and a chat id."
        # Most common setup mistake: the bot token pasted into the Chat ID field.
        if not TraderSession._looks_like_token(token):
            if TraderSession._looks_like_token(chat):
                return False, ("Looks like the fields are swapped — your Chat ID contains the bot "
                               "token. Put the token (like 123456789:ABC…) in Bot token, and your "
                               "numeric Chat ID in Chat ID.")
            return False, ("That bot token doesn't look right. Copy the full token from @BotFather — "
                           "it looks like 123456789:ABCdef… and must include the part after the colon.")
        if ":" in chat:
            return False, ("Your Chat ID looks like a bot token. The Chat ID is a plain number "
                           "(e.g. 987654321) — message @userinfobot to get yours.")
        if chat == token.split(":", 1)[0]:
            return False, ("That Chat ID is your bot's own ID, so it's trying to message itself. "
                           "Use YOUR personal Chat ID — open Telegram, message @userinfobot, and it "
                           "replies with your id (a number like 987654321).")
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": chat,
                "text": "🔥 Trevolto — test message. Your Telegram alerts are working ✅",
            }).encode()
            with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10) as r:
                body = json.loads(r.read().decode())
            if body.get("ok"):
                return True, "Test message sent — check your Telegram."
            return False, f"Telegram error: {body.get('description', 'unknown')}"
        except urllib.error.HTTPError as e:
            try:
                desc = json.loads(e.read().decode()).get("description", str(e))
            except Exception:
                desc = str(e)
            low = desc.lower()
            if "not found" in low or "unauthorized" in low:
                return False, ("Bot token was rejected — it's wrong or was revoked in @BotFather. "
                               "Open @BotFather, copy the token again, and paste the full value.")
            if "chat not found" in low:
                return False, ("Chat not found — open a chat with your bot and send it any message "
                               "first, then use your numeric Chat ID (from @userinfobot).")
            if "forbidden" in low:
                return False, ("The bot can't message that chat. If your Chat ID is the bot's own id, "
                               "use YOUR id from @userinfobot instead. Otherwise open Telegram, start "
                               "a chat with your bot and send it any message first, then try again.")
            return False, f"Telegram rejected it: {desc}"
        except Exception as e:  # noqa: BLE001
            return False, f"Could not reach Telegram: {e}"

    # -- settings ------------------------------------------------------------
    def _apply_global_exec(self) -> None:
        """Managed customers (no custom access) run the admin's GLOBAL execution/risk
        config — overlay it onto settings in memory so all sizing/order/guardrail
        reads pick it up. Custom-access users keep their own settings."""
        try:
            if not (store.get_user(self.user_id) or {}).get("allow_custom"):
                gexec = {**MANAGED_EXEC_DEFAULTS, **((_global_strategy() or {}).get("execution") or {})}
                for k in GLOBAL_EXEC_KEYS:
                    if gexec.get(k) is not None:
                        self.settings[k] = gexec[k]
        except Exception:
            pass
        self._apply_guardrails()

    def _apply_guardrails(self) -> None:
        s = self.settings
        self.guard.configure(
            max_open=s.get("max_open", 0), daily_loss=s.get("daily_loss", 0),
            cooldown=s.get("cooldown", 0), dedupe=s.get("dedupe", 0),
            daily_profit=s.get("daily_profit", 0),
            daily_loss_pct=s.get("daily_loss_pct", 0),      # % of the day's starting equity
            daily_profit_pct=s.get("daily_profit_pct", 0),  # (scales with any balance)
        )

    def set_settings(self, patch: dict) -> None:
        with self._lock:
            self.settings.update(patch or {})
            store.save_settings(self.user_id, self.settings)
            self._apply_global_exec()       # managed users: global exec/risk wins over their edits
            paper = bool(self.settings.get("paper_mode", False))
            self.settings["safe_mode"] = paper
            self.em.safe_mode = paper
            self.em.read_only = bool(self.settings.get("read_only", False))
        self.log(f"Settings updated ({', '.join((patch or {}).keys())})")

    def reset_paper(self, start_balance: float = 10_000.0) -> None:
        """Reset the demo/paper wallet back to its starting balance and clear positions."""
        with self._lock:
            self.em.reset_paper(start_balance)
            if self.settings.get("paper_mode"):
                ps = self.em.paper_snapshot()
                self.balance = ps["balance"]
                self.positions = self.em.fetch_positions()
                self.pnl = ps["unrealized"]
        self.log(f"Demo wallet reset to ${start_balance:,.0f}")

    def _params(self) -> "strat.StrategyParams":
        # Use the SAME params the live runner uses, so the chart reflects what the
        # bot actually trades: custom users -> their saved params; managed users ->
        # the admin's global strategy.
        u = store.get_user(self.user_id) or {}
        if u.get("allow_custom"):
            src = self.settings.get("strategy_params") or {}
        else:
            src = (_global_strategy() or {}).get("params") or {}
        return self._strat_params(src)

    def _strat_params(self, src: dict) -> "strat.StrategyParams":
        p = strat.StrategyParams()
        for k, v in (src or {}).items():
            if not hasattr(p, k):
                continue
            cur = getattr(p, k)                 # coerce to the field's type — saved JSON may
            try:                                # deliver ints/floats/bools loosely (e.g. 9.0)
                if isinstance(cur, bool):
                    v = bool(v)
                elif isinstance(cur, int):
                    v = int(float(v))
                elif isinstance(cur, float):
                    v = float(v)
            except (TypeError, ValueError):
                continue
            setattr(p, k, v)
        return p

    # -- connection ----------------------------------------------------------
    def connect(self) -> str:
        keys = store.load_keys(self.user_id)
        if not keys:
            raise ValueError("no API keys saved")
        paper = bool(self.settings.get("paper_mode", False))
        self.settings["safe_mode"] = paper
        with self._lock:
            self.em.connect(
                keys["exchange"], keys.get("api_key", ""), keys.get("api_secret", ""),
                password=keys.get("password", ""),
                safe_mode=paper,
                read_only=bool(self.settings.get("read_only", False)),
                market_type=keys.get("market_type", "spot"),
            )
            self.connected = self.em.connected
            self.exchange_id = keys["exchange"]
            self.market_type = keys.get("market_type", "spot")
        tag = " — PAPER MODE (simulated)" if paper else " — LIVE"
        self.log(f"Connected to {self.exchange_id} ({self.market_type}){tag}", "ok")
        self.notify(f"Connected to {self.exchange_id} ({self.market_type}){tag}")
        self._ensure_poll()
        self.refresh()
        self._check_key_perms()
        # Built-in strategy is ON by default — auto-start it once connected.
        if self.settings.get("strategy_enabled", True):
            self.start_strategy()
        return "connected"

    def _auto_connect(self) -> None:
        """Reconnect saved keys in the background so the connection survives
        logout/login and server restarts (live sessions are held in memory)."""
        try:
            if not self.connected and store.load_keys(self.user_id):
                self.connect()
        except Exception as e:  # noqa: BLE001
            self.log(f"Auto-reconnect skipped — {e}", "warn")

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
            if self.settings.get("paper_mode"):
                # Demo mode: the dashboard reflects the self-contained paper wallet
                # (marked to market), not the real exchange balance.
                ps = self.em.paper_snapshot(self.em._last_price)
                self.balance = ps["balance"]
                self.pnl = ps["unrealized"]
                self.positions = self.em.fetch_positions()
            else:
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

    def place_manual(self, side: str, symbol: str, size: float | None = None,
                     sl: float = 0.0, tp1: float = 0.0, tp_partial: float = 0.0,
                     entry: float = 0.0, order_type: str = "") -> dict:
        """Manual order from the Order-panel modal — routes through handle_signal so it
        gets the same guardrails, sizing and reduce-only SL/TP bracket as the strategy."""
        return self.handle_signal({
            "action": side, "symbol": ex.normalize_symbol(symbol),
            "size": size, "entry": float(entry or 0), "sl": float(sl or 0),
            "tp1": float(tp1 or 0), "tp_partial": float(tp_partial or 0),
            "order_type": (order_type or ""), "force_bracket": True,
        }, source="manual")

    def suggest_trade(self, symbol: str, side: str) -> dict:
        """Strategy-derived entry / SL / TP / R:R / risk-based size for the manual-order
        modal — same stop logic the built-in strategy uses (EMA21 buffer + swing)."""
        symbol = ex.normalize_symbol(symbol)
        side = "buy" if str(side).lower() in ("buy", "long") else "sell"
        params = self._params()
        price = float(self.get_price(symbol) or 0.0)
        tf = self.settings.get("strategy_timeframe", "15m")
        sl = tp = 0.0
        raw = self.em.fetch_ohlcv(symbol, tf, 300) or public_ohlcv(
            self.exchange_id or "binance", symbol, tf, 300)
        if raw and len(raw) >= params.trend_ema + 3 and price > 0:
            closed = raw[:-1]
            o, h, low, cl, fast, slow, trend = strat.crossover_arrays(closed, params)
            i = len(closed) - 1
            if slow[i] is not None:
                buf = max(0.0, params.sl_ema_buffer_pct) / 100.0
                look = max(1, int(params.swing_lookback))
                if side == "buy":
                    sl = min(slow[i] * (1 - buf), min(low[max(0, i - look):i + 1]))
                    risk = price - sl
                    tp = price + params.tp_r * risk if risk > 0 else 0.0
                else:
                    sl = max(slow[i] * (1 + buf), max(h[max(0, i - look):i + 1]))
                    risk = sl - price
                    tp = price - params.tp_r * risk if risk > 0 else 0.0
        try:
            amount, _ = self._size(price, price, sl) if (sl and price) else (self._size(price)
                                                                             if price else (0.0, ""))
        except Exception:
            amount = 0.0
        return {"symbol": symbol, "side": side, "entry": round(price, 8),
                "sl": round(sl, 8) if sl else 0, "tp": round(tp, 8) if tp else 0,
                "rr": params.tp_r, "tp_partial": params.partial_pct,
                "size": round(amount, 8) if amount else 0,
                "order_type": self.settings.get("order_type", "market"),
                "risk_pct": float(self.settings.get("risk_percent", 1))}

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
        tp_partial = float(payload.get("tp_partial", 0) or 0)   # fraction closed at tp1 only
        side = action

        allowed, reason = self.guard.check_entry(symbol, side, self._open_pairs())
        self.guard.record_signal(symbol, side)  # mark seen (for the next dedupe window)
        if not allowed:
            self.log(f"Blocked {side.upper()} {symbol}: {reason}", "warn")
            store.record_trade(self.user_id, symbol=symbol, side=side, kind="signal",
                               status="blocked", note=reason, mode=self._mode())
            if source == "strategy" and self.settings.get("alert_skips", False):
                self.notify(f"⏭️ Skipped {side.upper()} {symbol} — {reason}")
            return {"ok": False, "message": f"blocked: {reason}"}

        price = entry if entry > 0 else self.get_price(symbol)
        amount, reason = self._size(price, entry, sl)
        _psz = payload.get("size")                       # manual orders may pass an explicit size
        if _psz not in (None, "", 0):
            try:
                amount, reason = float(_psz), "manual size"
            except (TypeError, ValueError):
                pass
        if amount <= 0:
            return {"ok": False, "message": "size 0"}

        self._apply_lev_margin(symbol)
        order_type = str(payload.get("order_type") or self.settings.get("order_type", "market"))
        limit_px = price if order_type == "limit" else None
        res = self.em.place_order(symbol, side, amount, order_type, limit_px)
        store.record_trade(self.user_id, symbol=symbol, side=side, kind="entry",
                           status="filled" if res.ok else "rejected",
                           amount=amount, price=price, note=f"{source}: {res.message}", mode=self._mode())
        self.log(f"{source} {side.upper()} {symbol} x{amount}: {res.message} [{reason}]",
                 "ok" if res.ok else "error")
        if not res.ok:
            if source == "strategy":            # signal fired but the order didn't go through
                self.notify(f"⚠️ {side.upper()} {symbol} signal fired but the order was rejected — "
                            f"{res.message}. Check your balance / API permissions.")
            return {"ok": False, "message": res.message}

        emoji = "🟢" if side == "buy" else "🔴"
        notional = amount * price
        rr = ""
        if sl and tp1 and price:
            risk = abs(price - sl)
            if risk > 0:
                rr = f" · R:R 1:{abs(tp1 - price) / risk:.1f}"
        self.notify(f"{emoji} {side.upper()} {symbol}\n"
                    f"Size {amount:g} (~${notional:,.0f})\n"
                    f"Entry ~{price:g}"
                    + (f" · SL {sl:g}" if sl else "")
                    + (f" · TP {tp1:g}" if tp1 else "")
                    + rr
                    + f"\n{source} · {self._mode()}")
        self.guard.record_entry(symbol)

        # bracket: reduce-only SL + scaled TP legs (manual orders force it via the modal)
        if self.settings.get("auto_bracket", True) or payload.get("force_bracket"):
            xside = ex.exit_side(side)
            if sl > 0:
                r = self.em.place_reduce_order(symbol, xside, amount, sl, "sl")
                self.log(f"SL {symbol} @ {sl}: {r.message}", "ok" if r.ok else "warn")
            if tp_partial > 0 and tp1 > 0:
                # strategy: reduce-only TP for a fraction at the 2R target; the rest
                # is held for the runner's counter-cross / trail exit.
                qty = round(amount * tp_partial, 8)
                r = self.em.place_reduce_order(symbol, xside, qty, tp1, "tp")
                self.log(f"TP {symbol} {qty} @ {tp1} ({int(tp_partial * 100)}%): {r.message}", "ok" if r.ok else "warn")
            else:
                for px, qty in ex.plan_take_profits(amount, tp1, tp2, float(self.settings.get("tp1_fraction", 0.5))):
                    r = self.em.place_reduce_order(symbol, xside, qty, px, "tp")
                    self.log(f"TP {symbol} {qty} @ {px}: {r.message}", "ok" if r.ok else "warn")
        self.refresh()
        return {"ok": True, "message": res.message, "amount": amount}

    def close_position(self, symbol: str, fraction: float = 1.0) -> dict:
        for p in self.positions:
            if p.pair == symbol or ex.normalize_symbol(p.pair) == ex.normalize_symbol(symbol):
                res = self.em.close_position(p, fraction)
                realized = float(p.pnl) * fraction
                if res.ok:
                    # feed realized PnL to the guardrail so the daily loss/profit limit works
                    if self.guard.record_realized(realized):
                        self.log(f"Daily {'profit' if realized > 0 else 'loss'} limit hit "
                                 f"({self.guard.daily_realized:+.2f}) — new entries halted for today", "warn")
                        self.notify(f"🛑 Daily {'profit target' if realized > 0 else 'loss limit'} hit "
                                    f"({self.guard.daily_realized:+.2f}) — the bot has stopped opening new trades "
                                    f"for the rest of the day. Open trades stay managed.")
                store.record_trade(self.user_id, symbol=p.pair, side="close", kind="close",
                                   status="filled" if res.ok else "rejected",
                                   amount=p.size * fraction, price=p.current, pnl=p.pnl,
                                   note=res.message, mode=self._mode())
                self.log(f"Close {p.pair}: {res.message}", "ok" if res.ok else "error")
                cemoji = "✅" if p.pnl >= 0 else "❌"
                self.notify(f"{cemoji} Closed {p.pair}\nPnL {p.pnl:+.4f} ({self._mode()})")
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
            act = ev.get("act")
            if act == "enter":
                markers.append({"i": i, "type": "enter", "side": ev["side"],
                                "price": float(ev["entry"]), "sl": float(ev["sl"]),
                                "tp1": float(ev["tp"]), "tp2": None})
            elif act == "exit":
                markers.append({"i": i, "type": "exit", "side": ev.get("side"),
                                "price": float(ev.get("price", cl[i])), "reason": ev.get("reason", "")})
            else:  # partial
                markers.append({"i": i, "type": "partial", "price": float(ev.get("price", cl[i]))})
        return {
            "symbol": symbol, "timeframe": tf,
            "candles": [[int(c[0]), c[1], c[2], c[3], c[4], c[5]] for c in closed],
            "fast": rnd(fast), "slow": rnd(slow), "trend": rnd(trend),
            "fast_ema": params.fast_ema, "slow_ema": params.slow_ema, "trend_ema": params.trend_ema,
            "markers": markers,
            "signal": strat.signal_strength(closed, params),
        }

    # -- built-in strategy ---------------------------------------------------
    def start_strategy(self) -> None:
        if self.strategy_on:
            return
        self.strategy_on = True
        self._strat_primed = {}
        self._strat_hold = {}
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
            _u = store.get_user(self.user_id) or {}
            ent = store.entitlement(_u)
            if not ent["ok"]:
                msg = ("Trial ended — trading stopped. Buy a licence to reactivate."
                       if ent["status"] == "expired" else f"Trading stopped — access {ent['status']}.")
                self.log(msg, "warn")
                self.notify(msg)
                self.strategy_on = False
                break
            if _u.get("allow_custom"):
                src = self.settings.get("strategy_params") or {}
                sym_csv = str(self.settings.get("strategy_symbols", "BTC/USDT"))
                tf = self.settings.get("strategy_timeframe", "15m")
            else:
                g = _global_strategy()
                if self._strat_version is not None and g.get("version") != self._strat_version:
                    self.log("\u2699\ufe0f Strategy updated \u2014 new settings are now live.", "ok")
                    self.notify("\u2699\ufe0f Your bot strategy was updated \u2014 new settings are now live.")
                    self._strat_primed = {}
                    self._apply_global_exec()   # pull the admin's new global exec/risk live
                self._strat_version = g.get("version")
                src = g.get("params") or {}
                sym_csv = str(g.get("symbols", "BTC/USDT"))
                tf = g.get("timeframe", "15m")
            symbols = [s.strip() for s in sym_csv.split(",") if s.strip()]
            params = self._strat_params(src)
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
        # High-impact news filter: when News-trading is OFF, hold new entries for
        # 1h before and after major macro releases (FOMC/CPI/NFP...). Exits are
        # never blocked — protecting open risk always takes priority.
        protect = not self.settings.get("news_trading", True)
        blackout = news.blackout() if protect else None
        acted = False
        for evt in strat.evaluate_crossover(closed, params, symbol):
            act = evt.get("act")
            if act == "enter":
                if blackout:
                    self._news_hold(symbol, evt["side"], blackout)
                    continue
                acted = True
                side = "buy" if evt["side"] == "long" else "sell"
                self.log(f"Signal {symbol} {side.upper()} — entry {float(evt['entry']):g}, "
                         f"SL {float(evt['sl']):g}, TP {float(evt['tp']):g} "
                         f"({int(params.partial_pct * 100)}% partial, rest trails EMA{params.slow_ema})")
                self.handle_signal({"action": side, "symbol": symbol,
                                    "entry": float(evt["entry"]), "sl": float(evt["sl"]),
                                    "tp1": float(evt["tp"]), "tp2": 0,
                                    "tp_partial": params.partial_pct, "comment": "Trevolto"},
                                   source="strategy")
            elif act == "exit":
                acted = True
                self.log(f"Strategy exit {symbol} — {evt.get('reason', 'exit')}", "ok")
                self.close_position(symbol)
        # Decision "why" line: when nothing fired, surface the reason it's holding
        # (throttled: only log when the reason changes for this symbol).
        if not acted:
            reason = strat.entry_block_reason(closed, params)
            if reason and self._strat_hold.get(symbol) != reason:
                self.log(f"{symbol}: no entry — {reason}", "warn")
            self._strat_hold[symbol] = reason
        # Signal-strength meter + early "get ready" alert when it reaches 85%.
        try:
            sig = strat.signal_strength(closed, params)
        except Exception:
            sig = {"pct": 0, "state": "", "side": ""}
        self.signal = dict(sig, symbol=symbol)
        pct, sd = int(sig.get("pct", 0)), sig.get("side", "")
        if pct >= 85 and sd and not acted and not blackout and not self._sig_alerted.get(symbol):
            self._sig_alerted[symbol] = True
            word = "BUY" if sd == "long" else "SELL"
            self.notify(f"⚡ {word} signal firing on {symbol} — strength {pct}%. "
                        f"A {'long' if sd == 'long' else 'short'} entry is lining up — the bot is getting ready to trade.")
        elif pct < 70:
            self._sig_alerted[symbol] = False

    def _news_hold(self, symbol: str, side: str, bl: dict) -> None:
        """Log (and alert once) when a fresh entry is held back by the news filter."""
        word = "BUY" if side == "long" else "SELL"
        cc = f" ({bl['country']})" if bl.get("country") else ""
        when = (f"in {bl['mins']}m" if bl["phase"] == "pre"
                else f"{abs(bl['mins'])}m ago")
        self.log(f"News filter: held {symbol} {word} — high-impact {bl['title']}{cc} {when}", "warn")
        key = f"{bl['title']}@{int(bl['ts'])}"
        if self._news_alerted != key:
            self._news_alerted = key
            self.notify(f"📰 New entries paused — high-impact news. {bl['title']}{cc} {when}. "
                        f"The bot is standing aside until 1h after the event to avoid the volatility spike; "
                        f"open trades and their stops/targets stay fully managed.")

    # -- snapshot for the UI -------------------------------------------------
    # -- API-key safety check -----------------------------------------------
    def _check_key_perms(self) -> None:
        """Best-effort: warn if the connected API key can withdraw (Binance-family)."""
        self.key_withdraw_warn = False
        try:
            client = getattr(self.em, "client", None)
            fn = getattr(client, "sapi_get_account_apirestrictions", None) or \
                getattr(client, "sapiGetAccountApiRestrictions", None)
            if fn:
                r = fn() or {}
                if r.get("enableWithdrawals"):
                    self.key_withdraw_warn = True
                    self.log("\u26a0\ufe0f This API key has WITHDRAWALS enabled \u2014 use a trade-only key.", "warn")
                    self.notify("\u26a0\ufe0f Security: your API key has withdrawals enabled. "
                                "For safety, use a trade-only key with withdrawals disabled.")
        except Exception:
            self.key_withdraw_warn = False

    # -- two-way Telegram control -------------------------------------------
    def _tg_cmd_loop(self) -> None:
        while not self._stop.is_set():
            token = self.settings.get("telegram_token", "").strip()
            chat = str(self.settings.get("telegram_chat", "")).strip()
            if not token or not chat:
                self._stop.wait(20); continue
            try:
                url = (f"https://api.telegram.org/bot{token}/getUpdates"
                       f"?timeout=20&offset={self._tg_offset}")
                with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
                    data = json.loads(r.read().decode())
                for upd in data.get("result", []):
                    self._tg_offset = upd["update_id"] + 1
                    m = upd.get("message") or upd.get("edited_message") or {}
                    text = (m.get("text") or "").strip()
                    frm = str((m.get("chat") or {}).get("id", ""))
                    if text.startswith("/") and frm == chat:
                        reply = self._tg_command(text)
                        if reply:
                            self._tg_send(token, chat, reply)
            except Exception:
                self._stop.wait(10)

    def _tg_command(self, text: str) -> str:
        cmd = text.split()[0].lstrip("/").split("@")[0].lower()
        if cmd in ("start", "help"):
            return ("Commands:\n/status \u2014 connection & strategy\n/pnl \u2014 realized PnL\n"
                    "/positions \u2014 open trades\n/pause \u2014 stop strategy\n"
                    "/resume \u2014 start strategy\n/closeall \u2014 close all positions")
        if cmd == "status":
            ent = store.entitlement(store.get_user(self.user_id) or {})
            strat_s = "running" if self.strategy_on else ("on (idle)" if self.settings.get("strategy_enabled") else "off")
            head = "\U0001F7E2 Connected" if self.connected else "\U0001F534 Disconnected"
            return (f"{head} {self.exchange_id or ''}\nStrategy: {strat_s}\n"
                    f"Balance: {self.balance:.2f} \u00b7 uPnL: {self.pnl:+.2f}\n"
                    f"Open: {len(self.positions)} \u00b7 {self._mode()} \u00b7 {ent['status']}")
        if cmd == "pnl":
            try:
                pnl, ntr, _w = store.realized_pnl_since(self.user_id, time.time() - 86400)
                pm = store.pnl_by_mode(self.user_id)
                return (f"Realized PnL (24h): {pnl:+.2f} over {ntr} trades\n"
                        f"Live all-time: {pm['live']['pnl']:+.2f} \u00b7 {pm['live']['win_rate']}% win")
            except Exception:
                return "PnL unavailable right now."
        if cmd == "positions":
            if not self.positions:
                return "No open positions."
            return "\n".join(f"{p.pair} {p.side} {p.size:g} @ {p.entry:g} \u00b7 PnL {p.pnl:+.4f}"
                             for p in self.positions)
        if cmd == "pause":
            self.stop_strategy()
            self.settings["strategy_enabled"] = False
            store.save_settings(self.user_id, self.settings)
            return "\u23f8\ufe0f Strategy paused."
        if cmd == "resume":
            self.settings["strategy_enabled"] = True
            store.save_settings(self.user_id, self.settings)
            if self.connected:
                self.start_strategy()
                return "\u25b6\ufe0f Strategy resumed."
            return "\u25b6\ufe0f Strategy enabled \u2014 connect your exchange to run it."
        if cmd == "closeall":
            r = self.close_all()
            return f"\U0001F9F9 {r.get('message', 'done')}"
        return "Unknown command. Send /help."

    def snapshot(self) -> dict:
        paper_on = bool(self.settings.get("paper_mode", False))
        # Demo mode: source balance / PnL / positions from the self-contained paper
        # wallet so it always shows (even before any exchange is connected).
        paper = self.em.paper_snapshot() if paper_on else None
        if paper is not None:
            bal, upnl = paper["balance"], paper["unrealized"]
            positions = [
                {"pair": p["pair"], "side": p["side"], "size": p["size"], "entry": p["entry"],
                 "current": p["current"], "pnl": p["pnl"], "status": "Active"}
                for p in paper["positions"]
            ]
        else:
            bal, upnl = round(self.balance, 2), round(self.pnl, 2)
            positions = [
                {"pair": p.pair, "side": p.side, "size": p.size, "entry": p.entry,
                 "current": p.current, "pnl": round(p.pnl, 4), "status": p.status}
                for p in self.positions
            ]
        return {
            "connected": self.connected,
            "exchange": self.exchange_id,
            "market_type": self.market_type,
            "safe_mode": bool(self.settings.get("safe_mode", False)),
            "paper_mode": paper_on,
            "paper": paper,
            "read_only": bool(self.settings.get("read_only", False)),
            "balance": bal,
            "pnl": upnl,
            "signal": self.signal,
            "news": news.status(protect=not self.settings.get("news_trading", True)),
            "strategy_on": self.strategy_on,
            "strategy_enabled": bool(self.settings.get("strategy_enabled", True)),
            "guard_tripped": self.guard.tripped,
            "positions": positions,
            "log": list(self.log_ring)[:120],
            "alerts": list(self.alert_ring)[:30],
            "key_withdraw_warn": getattr(self, "key_withdraw_warn", False),
            "settings": self.settings,
        }


# --- session registry -------------------------------------------------------
_SESSIONS: dict = {}
_REG_LOCK = threading.RLock()


def get_session(user_id: int) -> TraderSession:
    with _REG_LOCK:
        s = _SESSIONS.get(user_id)
        fresh = s is None
        if fresh:
            s = TraderSession(user_id)
            _SESSIONS[user_id] = s
    # A brand-new session (first request after login, or after a restart) with
    # saved keys auto-reconnects in the background so the user stays "Connected".
    if fresh and store.load_keys(user_id):
        threading.Thread(target=s._auto_connect, name=f"autoconnect-{user_id}", daemon=True).start()
    return s


def live_stats() -> dict:
    """Real-time counts across all in-memory sessions (admin dashboard)."""
    online = trading = 0
    per_user: dict = {}
    with _REG_LOCK:
        sessions = list(_SESSIONS.items())
    for uid, s in sessions:
        connected = bool(getattr(s, "connected", False))
        strat = bool(getattr(s, "strategy_on", False))
        if connected:
            online += 1
        if strat:
            trading += 1
        per_user[uid] = {
            "connected": connected,
            "strategy_on": strat,
            "exchange": getattr(s, "exchange_id", None),
            "open_positions": len(getattr(s, "positions", []) or []),
        }
    return {"online": online, "trading": trading, "per_user": per_user}


def session_snapshot_if_live(user_id: int):
    """Live snapshot for a user only if a session already exists (no spin-up)."""
    with _REG_LOCK:
        s = _SESSIONS.get(user_id)
    return s.snapshot() if s is not None else None
