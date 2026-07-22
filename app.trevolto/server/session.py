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

from . import store, webpush
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
    "max_open": 4,
    "daily_loss": 0.0,
    "daily_profit": 0.0,
    "cooldown": 0,
    "dedupe": 5,
    "webhook_passphrase": "",
    "telegram_token": "",
    "telegram_chat": "",
    "daily_summary": True,           # daily PnL recap via Telegram/email
    "alert_skips": False,            # Telegram alert when a strategy signal is blocked
    "strategy_filter": "Trevolto",
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
        self._strat_version = None      # last logged "why held" reason per symbol
        self._apply_guardrails()
        self.key_withdraw_warn = False
        self._tg_offset = 0
        self._tg_thread = threading.Thread(target=self._tg_cmd_loop, daemon=True)
        self._tg_thread.start()

    # -- logging + notifications --------------------------------------------
    def _mode(self) -> str:
        return "paper" if self.settings.get("paper_mode") else "live"

    def log(self, msg: str, level: str = "info") -> None:
        self.log_ring.appendleft({"ts": time.time(), "level": level, "msg": msg})

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
    def telegram_test(token: str, chat: str) -> tuple:
        """Synchronous test send — returns (ok, message) so the UI can report."""
        token, chat = (token or "").strip(), (chat or "").strip()
        if not token or not chat:
            return False, "Enter both a bot token and a chat id."
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
            return False, f"Telegram rejected it: {desc}"
        except Exception as e:  # noqa: BLE001
            return False, f"Could not reach Telegram: {e}"

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
            paper = bool(self.settings.get("paper_mode", False))
            self.settings["safe_mode"] = paper
            self.em.safe_mode = paper
            self.em.read_only = bool(self.settings.get("read_only", False))
        self.log(f"Settings updated ({', '.join((patch or {}).keys())})")

    def _params(self) -> "strat.StrategyParams":
        return self._strat_params(self.settings.get("strategy_params") or {})

    def _strat_params(self, src: dict) -> "strat.StrategyParams":
        p = strat.StrategyParams()
        for k, v in (src or {}).items():
            if hasattr(p, k):
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
                           amount=amount, price=price, note=res.message, mode=self._mode())
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
        if amount <= 0:
            return {"ok": False, "message": "size 0"}

        self._apply_lev_margin(symbol)
        order_type = self.settings.get("order_type", "market")
        limit_px = price if order_type == "limit" else None
        res = self.em.place_order(symbol, side, amount, order_type, limit_px)
        store.record_trade(self.user_id, symbol=symbol, side=side, kind="entry",
                           status="filled" if res.ok else "rejected",
                           amount=amount, price=price, note=f"{source}: {res.message}", mode=self._mode())
        self.log(f"{source} {side.upper()} {symbol} x{amount}: {res.message} [{reason}]",
                 "ok" if res.ok else "error")
        if not res.ok:
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

        # bracket: reduce-only SL + scaled TP legs
        if self.settings.get("auto_bracket", True):
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
        acted = False
        for evt in strat.evaluate_crossover(closed, params, symbol):
            act = evt.get("act")
            if act == "enter":
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
        if pct >= 85 and sd and not acted and not self._sig_alerted.get(symbol):
            self._sig_alerted[symbol] = True
            word = "BUY" if sd == "long" else "SELL"
            self.notify(f"⚡ {word} signal firing on {symbol} — strength {pct}%. "
                        f"A {'long' if sd == 'long' else 'short'} entry is lining up — the bot is getting ready to trade.")
        elif pct < 70:
            self._sig_alerted[symbol] = False

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
        return {
            "connected": self.connected,
            "exchange": self.exchange_id,
            "market_type": self.market_type,
            "safe_mode": bool(self.settings.get("safe_mode", False)),
            "paper_mode": bool(self.settings.get("paper_mode", False)),
            "read_only": bool(self.settings.get("read_only", False)),
            "balance": round(self.balance, 2),
            "pnl": round(self.pnl, 2),
            "signal": self.signal,
            "strategy_on": self.strategy_on,
            "strategy_enabled": bool(self.settings.get("strategy_enabled", True)),
            "guard_tripped": self.guard.tripped,
            "positions": [
                {"pair": p.pair, "side": p.side, "size": p.size, "entry": p.entry,
                 "current": p.current, "pnl": round(p.pnl, 4), "status": p.status}
                for p in self.positions
            ],
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
        if s is None:
            s = TraderSession(user_id)
            _SESSIONS[user_id] = s
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
