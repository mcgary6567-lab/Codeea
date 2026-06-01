"""Backend worker thread.

All exchange I/O happens on ONE thread so the ccxt client is never touched
concurrently. The GUI and the webhook server communicate with it only through
thread-safe queues:

  * ``command_queue``  – trade/connect/disconnect commands flow IN
  * ``ui_queue``       – balance, positions, log lines flow OUT to the GUI

The GUI drains ``ui_queue`` from its Tk event loop via ``root.after`` so widget
updates always happen on the main thread.
"""

from __future__ import annotations

import csv
import json
import os
import queue
import threading
import time
from datetime import datetime
from typing import Optional

import history
from config import (
    STATE_FILE,
    DEFAULT_AUTO_BRACKET,
    DEFAULT_LEVERAGE,
    DEFAULT_ORDER_TYPE,
    LOG_FILE,
    MAX_REFRESH_FAILURES,
    POLL_INTERVAL,
    QUOTE_CURRENCY,
    TP1_SCALE_OUT,
)
from guardrails import Guardrails
from exchange import (
    ExchangeManager,
    Position,
    exit_side,
    normalize_symbol,
    plan_take_profits,
    recompute_pnl,
    size_order,
)
from notifications import Notifier
from pricefeed import PriceFeed

# Throttle equity-curve snapshots so the DB doesn't balloon.
_EQUITY_SNAPSHOT_INTERVAL = 60.0


class Backend:
    def __init__(self) -> None:
        self.exchange = ExchangeManager()
        self.command_queue: "queue.Queue[dict]" = queue.Queue()
        self.ui_queue: "queue.Queue[dict]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Sizing settings, updated by the GUI via the "settings" command.
        self.fixed_size = 0.001
        self.sizing_mode = "fixed"
        self.risk_percent = 1.0
        self.auto_bracket = DEFAULT_AUTO_BRACKET
        self.tp1_scale = TP1_SCALE_OUT       # fraction closed at TP1 (rest at TP2)

        # Execution settings.
        self.order_type = DEFAULT_ORDER_TYPE
        self.leverage = DEFAULT_LEVERAGE
        self.margin_mode = ""
        self.move_be = False                 # move stop to breakeven on TP1 event
        self._sl_orders: dict = {}           # symbol -> stop-loss order id (for BE move)
        self._entry_px: dict = {}            # symbol -> entry price (for BE move)

        # Auto-reconnect.
        self.auto_reconnect = True
        self._connect_cmd: Optional[dict] = None
        self._next_reconnect = 0.0

        # Trailing stop (close when price falls X% from the peak since entry).
        self.trailing_pct = 0.0
        self._peaks: dict = {}               # symbol -> peak price

        self._load_state()                   # crash recovery

        self.guardrails = Guardrails()
        self.notifier = Notifier()
        history.init_db()
        self._last_equity_ts = 0.0

        # Real-time state shared with the price feed thread (guarded by _lock).
        self._lock = threading.Lock()
        self._price_cache: dict = {}
        self._last_positions: list[Position] = []
        self._last_balance: float = 0.0
        self._open_pairs: set = set()
        # Symbol the user is eyeing in the manual-trade box; streamed even when
        # there is no open position in it.
        self._manual_symbol: str = ""
        self.price_feed: PriceFeed | None = None

        # Connection-health tracking for drop/rate-limit alerts.
        self._fail_count = 0
        self._alerted = False

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self.price_feed:
            self.price_feed.stop()
        self._stop.set()

    # -- public enqueue helpers --------------------------------------------
    def submit(self, command: dict) -> None:
        self.command_queue.put(command)

    def _emit(self, kind: str, **data) -> None:
        self.ui_queue.put({"kind": kind, **data})

    def log(self, message: str, signal: str = "", pair: str = "", status: str = "") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._emit("log", time=ts, message=message, signal=signal, pair=pair, status=status)
        self._append_log_file(ts, signal, pair, status, message)

    # -- the worker loop ----------------------------------------------------
    def _run(self) -> None:
        last_poll = 0.0
        while not self._stop.is_set():
            try:
                cmd = self.command_queue.get(timeout=0.5)
            except queue.Empty:
                cmd = None

            if cmd:
                self._handle_command(cmd)

            # Periodic refresh of balance + positions when connected.
            now = time.time()
            if self.exchange.connected and (now - last_poll) >= POLL_INTERVAL:
                last_poll = now
                self._refresh()

            # Auto-reconnect after the connection looks lost.
            if (self.auto_reconnect and self._alerted and self._connect_cmd
                    and now >= self._next_reconnect):
                self._next_reconnect = now + 15.0
                self._attempt_reconnect()

    def _attempt_reconnect(self) -> None:
        self.log("Connection lost — auto-reconnecting…", status="Reconnect")
        try:
            self.exchange.disconnect()
            self._do_connect(self._connect_cmd)
        except Exception as exc:  # noqa: BLE001
            self.log(f"Reconnect failed: {exc} (retrying in 15s)", status="Error")

    # -- crash recovery -----------------------------------------------------
    def _load_state(self) -> None:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as fh:
                st = json.load(fh)
            self.exchange._spot_entries = st.get("spot_entries", {})
            self._entry_px = st.get("entry_px", {})
            self._sl_orders = st.get("sl_orders", {})
            self._peaks = st.get("peaks", {})
        except Exception:  # noqa: BLE001 - first run / no state
            pass

    def _save_state(self) -> None:
        try:
            st = {"spot_entries": self.exchange._spot_entries, "entry_px": self._entry_px,
                  "sl_orders": self._sl_orders, "peaks": self._peaks}
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(st, fh)
            os.replace(tmp, STATE_FILE)
        except Exception:  # noqa: BLE001 - best-effort
            pass

    # -- trailing stop ------------------------------------------------------
    def _check_trailing(self, positions, prices) -> None:
        if self.trailing_pct <= 0:
            return
        live = {p.pair for p in positions}
        for sym in list(self._peaks):
            if sym not in live:
                self._peaks.pop(sym, None)   # position gone
        for p in positions:
            if p.side != "Long":
                continue
            cur = float(prices.get(p.pair, p.current) or p.current)
            if cur <= 0:
                continue
            peak = max(self._peaks.get(p.pair, p.entry or cur), cur)
            self._peaks[p.pair] = peak
            if peak > 0 and cur <= peak * (1 - self.trailing_pct / 100.0):
                self.log(f"Trailing stop hit on {p.pair} "
                         f"({cur:g} <= peak {peak:g} -{self.trailing_pct:g}%) — closing",
                         signal="TRAIL", pair=p.pair, status="Trailing")
                self.notifier.notify(f"Trailing stop {p.pair}", f"Closed at {cur:g}", level="error")
                self._do_close(p.pair)

    # -- manual SL/TP -------------------------------------------------------
    def _do_set_protection(self, cmd: dict) -> None:
        symbol = normalize_symbol(cmd.get("symbol", ""))
        sl = float(cmd.get("sl") or 0)
        tp = float(cmd.get("tp") or 0)
        with self._lock:
            pos = next((p for p in self._last_positions if p.pair == symbol), None)
        if not pos:
            self.log(f"No open position for {symbol}", status="Error")
            return
        ex_side = exit_side("buy" if pos.side == "Long" else "sell")
        if sl > 0:
            r = self.exchange.place_reduce_order(symbol, ex_side, pos.size, sl, "sl")
            self.log(r.message, signal="SL", pair=symbol, status="OK" if r.ok else "Rejected")
            if r.ok and isinstance(r.raw, dict):
                self._sl_orders[symbol] = r.raw.get("id")
        if tp > 0:
            r = self.exchange.place_reduce_order(symbol, ex_side, pos.size, tp, "tp")
            self.log(r.message, signal="TP", pair=symbol, status="OK" if r.ok else "Rejected")
        self._save_state()

    # -- command handling ---------------------------------------------------
    def _handle_command(self, cmd: dict) -> None:
        action = cmd.get("cmd")
        try:
            if action == "connect":
                self._do_connect(cmd)
            elif action == "disconnect":
                if self.price_feed:
                    self.price_feed.stop()
                    self.price_feed = None
                self.exchange.disconnect()
                with self._lock:
                    self._price_cache.clear()
                    self._last_positions = []
                    self._open_pairs = set()
                self._emit("status", connected=False, exchange=self.exchange.exchange_id)
                self.log("Disconnected from exchange")
            elif action == "settings":
                self.fixed_size = cmd.get("fixed_size", self.fixed_size)
                self.sizing_mode = cmd.get("sizing_mode", self.sizing_mode)
                self.risk_percent = cmd.get("risk_percent", self.risk_percent)
                self.auto_bracket = cmd.get("auto_bracket", self.auto_bracket)
                self.tp1_scale = float(cmd.get("tp1_scale", self.tp1_scale))
                self.auto_reconnect = cmd.get("auto_reconnect", self.auto_reconnect)
                self.exchange.safe_mode = cmd.get("safe_mode", self.exchange.safe_mode)
                self.exchange.read_only = cmd.get("read_only", self.exchange.read_only)
                self.order_type = cmd.get("order_type", self.order_type)
                self.leverage = int(cmd.get("leverage", self.leverage) or 0)
                self.margin_mode = cmd.get("margin_mode", self.margin_mode)
                self.move_be = cmd.get("move_be", self.move_be)
                self.trailing_pct = float(cmd.get("trailing_pct", self.trailing_pct))
                self.guardrails.configure(
                    max_open=cmd.get("max_open", self.guardrails.max_open_positions),
                    daily_loss=cmd.get("daily_loss", self.guardrails.daily_loss_limit),
                    daily_profit=cmd.get("daily_profit", self.guardrails.daily_profit_limit),
                    cooldown=cmd.get("cooldown", self.guardrails.cooldown_seconds),
                    dedupe=cmd.get("dedupe", self.guardrails.dedupe_seconds),
                )
                self.notifier.configure(
                    sound=cmd.get("sound", self.notifier.sound_enabled),
                    token=cmd.get("telegram_token", self.notifier.telegram_token),
                    chat_id=cmd.get("telegram_chat_id", self.notifier.telegram_chat_id),
                    desktop=cmd.get("desktop", self.notifier.desktop_enabled),
                )
            elif action == "close":
                self._do_close(cmd.get("pair"))
            elif action == "set_protection":
                self._do_set_protection(cmd)
            elif action == "reset_daily":
                self.guardrails.reset_daily()
                self.log("Daily loss limit reset — trading re-enabled", status="OK")
            elif action == "signal_event":
                self._do_signal_event(cmd)
            elif action == "trade":
                self._do_trade(cmd)
            elif action == "refresh":
                self._refresh()
            elif action == "watch":
                self._set_manual_symbol(cmd.get("symbol", ""))
        except Exception as exc:  # noqa: BLE001 - never kill the worker thread
            self.log(f"Error: {exc}", status="Error")

    def _do_connect(self, cmd: dict) -> None:
        self.log(f"Connecting to {cmd['exchange_id']}...")
        self.exchange.connect(
            exchange_id=cmd["exchange_id"],
            api_key=cmd["api_key"],
            secret=cmd["secret"],
            password=cmd.get("password", ""),
            testnet=cmd.get("testnet", False),
            read_only=cmd.get("read_only", False),
            safe_mode=cmd.get("safe_mode", False),
            market_type=cmd.get("market_type", "spot"),
        )
        mt = self.exchange.market_type
        self._connect_cmd = dict(cmd)        # remember for auto-reconnect
        self._emit("status", connected=True, exchange=cmd["exchange_id"])
        self.log(f"Connected to {cmd['exchange_id']} ({mt})", status="Connected")
        self._fail_count = 0
        self._alerted = False

        # Security check: warn if the API key can withdraw funds.
        warn, msg = self.exchange.check_key_safety()
        if warn:
            self.log(msg, status="WARNING")
            self._emit("alert", level="error", message=msg)
            self.notifier.notify("API key warning", msg, level="error")

        # Populate the Symbol dropdown with the exchange's most-traded pairs.
        pairs = self.exchange.top_pairs()
        if pairs:
            self._emit("pairs", pairs=pairs)

        # Start the real-time price feed (real prices, read-only — runs in Safe
        # Mode too so paper positions and the mark price are realistic).
        if self.price_feed is None:
            self.price_feed = PriceFeed(
                exchange_id=cmd["exchange_id"],
                on_prices=self._on_prices,
                log=lambda m: self._emit("log", time="", message=m, signal="", pair="", status=""),
            )
            self.price_feed.start()

        self._refresh()

    def _do_trade(self, cmd: dict) -> None:
        symbol = normalize_symbol(cmd["symbol"])
        side = cmd["side"]
        source = cmd.get("source", "manual")
        # Surface the indicator's pair in the GUI (auto-fill manual symbol).
        if source == "webhook":
            self._emit("signal_symbol", symbol=symbol)
        entry = float(cmd.get("entry") or 0)
        sl = float(cmd.get("sl") or 0)
        tp1 = float(cmd.get("tp1") or 0)
        tp2 = float(cmd.get("tp2") or 0)

        # --- guardrail gate (the entry checkpoint) ---
        self.guardrails.record_signal(symbol, side)
        with self._lock:
            open_pairs = set(self._open_pairs)
        allowed, reason = self.guardrails.check_entry(symbol, side, open_pairs)
        if not allowed:
            self.log(f"Blocked: {reason}", signal=side.upper(), pair=symbol, status="Blocked")
            self._emit("order", ok=False, source=source, message=f"Blocked: {reason}")
            self.notifier.notify(f"Trade blocked {symbol}", reason, level="error")
            return

        balance = self.exchange.fetch_balance()
        price = self._current_price(symbol)

        size = cmd.get("size")
        if size is None:
            size, sreason = size_order(
                self.sizing_mode, self.fixed_size, self.risk_percent,
                balance, price, entry=entry, stop=sl,
            )
            self.log(f"Sizing: {size:g} ({sreason})", pair=symbol)
        size = float(size)

        # --- leverage / margin mode (best-effort) ---
        lm_note = self.exchange.apply_leverage_margin(symbol, self.leverage, self.margin_mode)
        if lm_note:
            self.log(lm_note, pair=symbol)

        # --- order type / price ---
        order_type = (cmd.get("order_type") or self.order_type).lower()
        limit_price = cmd.get("limit_price")
        if order_type == "limit" and not limit_price:
            limit_price = entry or price  # use alert entry, else current mark
        result = self.exchange.place_order(symbol, side, size, order_type, limit_price)

        status = "Filled" if result.ok else "Rejected"
        if result.simulated:
            status = "Simulated"
        self.log(result.message, signal=side.upper(), pair=result.pair or symbol, status=status)
        history.record_trade(
            source, result.pair or symbol, side, "entry", size,
            price, status, message=result.message,
        )
        self._emit("order", ok=result.ok, source=source, message=result.message)
        self.notifier.notify(
            f"{side.upper()} {result.pair or symbol}",
            f"{result.message} (size {size:g}, via {source})",
            level="ok" if result.ok else "error",
        )
        if result.ok:
            self.guardrails.record_entry(symbol)
            self._entry_px[symbol] = entry or price  # for a later breakeven move

        # Place protective SL/TP from the alert payload.
        if result.ok and self.auto_bracket and (sl or tp1 or tp2):
            self._place_brackets(result.pair or symbol, side, size, sl, tp1, tp2, source)
        if result.ok:
            self._save_state()

        self._refresh()

    def _do_signal_event(self, cmd: dict) -> None:
        """Handle post-entry lifecycle events from the indicator.

        The bot's own reduce-only SL/TP bracket already executes exits on the
        exchange, so these events are mostly informational — except TP1, which
        can optionally trail the stop to breakeven (something the static bracket
        doesn't do on its own).
        """
        event = cmd.get("event", "")
        symbol = normalize_symbol(cmd.get("symbol", ""))
        if symbol:
            self._emit("signal_symbol", symbol=symbol)
        nice = {
            "tp1_hit": "TP1 hit", "tp2_hit": "TP2 hit",
            "sl_hit": "SL hit", "sl_after_partial": "SL after partial",
        }.get(event, event)
        self.log(f"Indicator event: {nice}", signal=event.upper()[:6], pair=symbol,
                 status="Event")
        self.notifier.notify(f"{nice} {symbol}", f"Indicator reported {event}",
                             level="ok" if event.startswith("tp") else "error")

        if event == "tp1_hit" and self.move_be:
            self._move_stop_to_breakeven(symbol, float(cmd.get("entry") or 0))

    def _move_stop_to_breakeven(self, symbol: str, entry_hint: float) -> None:
        """Cancel the existing stop and place a new reduce-only stop at entry."""
        entry = entry_hint or self._entry_px.get(symbol, 0.0)
        if entry <= 0:
            self.log("Breakeven skipped — unknown entry price", pair=symbol, status="Skipped")
            return
        with self._lock:
            pos = next((p for p in self._last_positions if p.pair == symbol), None)
        if not pos:
            self.log("Breakeven skipped — no open position", pair=symbol, status="Skipped")
            return
        # Cancel the old stop if we tracked it, then place a fresh one at entry.
        old_id = self._sl_orders.get(symbol)
        if old_id:
            self.exchange.cancel_order(old_id, symbol)
        ex_side = exit_side("buy" if pos.side == "Long" else "sell")
        r = self.exchange.place_reduce_order(symbol, ex_side, pos.size, entry, "sl")
        if r.ok and isinstance(r.raw, dict):
            self._sl_orders[symbol] = r.raw.get("id")
        self.log(f"Stop moved to breakeven @ {entry:g}: {r.message}",
                 signal="BE", pair=symbol, status="OK" if r.ok else "Rejected")
        self.notifier.notify(f"Breakeven {symbol}", f"Stop moved to {entry:g}",
                             level="ok" if r.ok else "error")

    def _place_brackets(self, symbol, entry_side, size, sl, tp1, tp2, source) -> None:
        """Place reduce-only stop-loss and (scaled) take-profit orders."""
        ex_side = exit_side(entry_side)
        if sl > 0:
            r = self.exchange.place_reduce_order(symbol, ex_side, size, sl, "sl")
            self.log(r.message, signal="SL", pair=symbol, status="OK" if r.ok else "Rejected")
            history.record_trade(source, symbol, ex_side, "sl", size, sl,
                                 "Filled" if r.ok else "Rejected", message=r.message)
            # Remember the SL order id so a TP1 event can move it to breakeven.
            if r.ok and isinstance(r.raw, dict):
                self._sl_orders[symbol] = r.raw.get("id")
        for price, qty in plan_take_profits(size, tp1, tp2, self.tp1_scale):
            r = self.exchange.place_reduce_order(symbol, ex_side, qty, price, "tp")
            self.log(r.message, signal="TP", pair=symbol, status="OK" if r.ok else "Rejected")
            history.record_trade(source, symbol, ex_side, "tp", qty, price,
                                 "Filled" if r.ok else "Rejected", message=r.message)

    def _do_close(self, pair: str) -> None:
        """Flatten a single open position by symbol (or all if pair is None)."""
        with self._lock:
            positions = list(self._last_positions)
        targets = positions if not pair else [p for p in positions if p.pair == pair]
        if not targets:
            self.log(f"No open position to close: {pair}", status="Error")
            return
        for p in targets:
            r = self.exchange.close_position(p)
            self.log(r.message, signal="CLOSE", pair=p.pair, status="OK" if r.ok else "Rejected")
            self.notifier.notify(f"Close {p.pair}", r.message, level="ok" if r.ok else "error")
        self._refresh()

    def _current_price(self, symbol: str) -> float:
        """Best-available current price: cached feed price, else a REST tick."""
        sym = normalize_symbol(symbol)
        with self._lock:
            cached = self._price_cache.get(sym)
        if cached:
            return float(cached)
        return self.exchange._last_price(sym)

    def _refresh(self) -> None:
        """Authoritative REST refresh of balance + positions (slow cadence)."""
        if not self.exchange.connected:
            return
        try:
            balance = self.exchange.fetch_balance()
            positions = self.exchange.fetch_positions()
        except Exception as exc:  # noqa: BLE001
            self._note_failure(exc)
            return

        # Successful fetch — clear any connection-lost alert.
        self._fail_count = 0
        if self._alerted:
            self._alerted = False
            self._emit("alert", level="ok", message="Connection restored")

        new_pairs = {p.pair for p in positions}
        self._detect_closed_positions(new_pairs)

        with self._lock:
            self._last_balance = balance
            self._last_positions = positions
            self._open_pairs = new_pairs
            # Seed the price cache with REST current prices so the first paint
            # is correct even before the first websocket tick arrives.
            for p in positions:
                self._price_cache.setdefault(p.pair, p.current)

        # Keep the price feed subscribed to held symbols + the manual symbol.
        self._update_feed_symbols()

        # Trailing stop (close on drawdown from the peak since entry).
        self._check_trailing(positions, dict(self._price_cache))

        # Periodic equity-curve snapshot for the analytics view.
        now = time.time()
        if now - self._last_equity_ts >= _EQUITY_SNAPSHOT_INTERVAL:
            self._last_equity_ts = now
            open_pnl = sum(
                p.pnl for p in recompute_pnl(positions, dict(self._price_cache))
            )
            history.record_equity(balance, open_pnl)

        self._emit_account()

    def _on_prices(self, prices: dict) -> None:
        """Called from the price-feed thread on every tick/poll. No REST here."""
        with self._lock:
            self._price_cache.update(prices)
        self._emit_account()
        self._emit_manual_ticker()

    def _set_manual_symbol(self, raw: str) -> None:
        """The GUI's manual-trade symbol changed — stream its mark price."""
        symbol = normalize_symbol(raw) if raw.strip() else ""
        with self._lock:
            self._manual_symbol = symbol
        self._update_feed_symbols()
        # Emit immediately so a cached price (if any) shows without waiting.
        self._emit_manual_ticker()

    def _update_feed_symbols(self) -> None:
        """Subscribe the feed to held symbols plus the manual-trade symbol."""
        if not self.price_feed:
            return
        with self._lock:
            symbols = set(self._open_pairs)
            if self._manual_symbol:
                symbols.add(self._manual_symbol)
        self.price_feed.set_symbols(list(symbols))

    def _emit_manual_ticker(self) -> None:
        """Push the manual symbol's latest price (or None) to the GUI."""
        with self._lock:
            symbol = self._manual_symbol
            price = self._price_cache.get(symbol) if symbol else None
        if symbol:
            self._emit("ticker", symbol=symbol, price=price)

    def _emit_account(self) -> None:
        """Recompute PnL locally from cached prices and push to the GUI."""
        with self._lock:
            balance = self._last_balance
            positions = list(self._last_positions)
            prices = dict(self._price_cache)
        recomputed = recompute_pnl(positions, prices)
        total_pnl = sum(p.pnl for p in recomputed)
        self._emit(
            "account",
            balance=balance,
            pnl=total_pnl,
            positions=[p.__dict__ for p in recomputed],
        )

    def _detect_closed_positions(self, new_pairs: set) -> None:
        """Log/record a 'Closed' entry (with realized PnL estimate) for any
        position that vanished since the last refresh."""
        with self._lock:
            closed = self._open_pairs - new_pairs
            # Snapshot of the just-closed positions (pre-overwrite) for PnL.
            prev = {p.pair: p for p in self._last_positions}
            prices = dict(self._price_cache)
        for pair in sorted(closed):
            p = prev.get(pair)
            # Realized PnL estimate from the last known mark price.
            pnl = 0.0
            if p:
                cur = float(prices.get(pair, p.current) or p.current)
                pnl = (cur - p.entry) * p.size if p.side == "Long" else (p.entry - cur) * p.size
            self.log(
                f"Position {pair} closed (PnL {pnl:+.2f})",
                signal="CLOSE", pair=pair, status="Closed",
            )
            history.record_trade("system", pair, p.side if p else "", "close",
                                 p.size if p else 0.0, p.current if p else 0.0,
                                 "Closed", pnl=pnl, message="position closed")
            self.notifier.notify(f"Closed {pair}", f"Realized PnL {pnl:+.2f}",
                                 level="ok" if pnl >= 0 else "error")
            # Feed the daily loss/profit guardrails; trip & halt if breached.
            if self.guardrails.record_realized(pnl):
                profit = self.guardrails.trip_reason == "profit"
                what = "profit target" if profit else "loss limit"
                msg = (
                    f"Daily {what} hit ({self.guardrails.daily_realized:+.2f}). "
                    "New entries halted — reset in Guardrails to resume."
                )
                self.log(msg, status="Halted")
                self._emit("alert", level="ok" if profit else "error", message=msg)
                self.notifier.notify(f"Daily {what}", msg, level="ok" if profit else "error")

    def _note_failure(self, exc: Exception) -> None:
        """Track consecutive REST failures; warn once when the link looks down."""
        self._fail_count += 1
        self.log(f"Refresh failed: {exc}", status="Error")
        if self._fail_count >= MAX_REFRESH_FAILURES and not self._alerted:
            self._alerted = True
            self._emit(
                "alert",
                level="error",
                message=f"Connection problem — {self._fail_count} consecutive "
                f"failures. Check network / API keys / rate limits.",
            )

    # -- trade log file -----------------------------------------------------
    def _append_log_file(self, ts, signal, pair, status, message) -> None:
        new = not os.path.exists(LOG_FILE)
        try:
            with open(LOG_FILE, "a", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                if new:
                    writer.writerow(["time", "signal", "pair", "status", "message"])
                writer.writerow([ts, signal, pair, status, message])
        except OSError:
            pass  # logging to disk is best-effort
