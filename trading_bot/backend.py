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
import os
import queue
import threading
import time
from datetime import datetime
from typing import Optional

import history
from config import (
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

        # Execution settings.
        self.order_type = DEFAULT_ORDER_TYPE
        self.leverage = DEFAULT_LEVERAGE
        self.margin_mode = ""

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
                self.exchange.safe_mode = cmd.get("safe_mode", self.exchange.safe_mode)
                self.exchange.read_only = cmd.get("read_only", self.exchange.read_only)
                self.order_type = cmd.get("order_type", self.order_type)
                self.leverage = int(cmd.get("leverage", self.leverage) or 0)
                self.margin_mode = cmd.get("margin_mode", self.margin_mode)
                self.guardrails.configure(
                    max_open=cmd.get("max_open", self.guardrails.max_open_positions),
                    daily_loss=cmd.get("daily_loss", self.guardrails.daily_loss_limit),
                    cooldown=cmd.get("cooldown", self.guardrails.cooldown_seconds),
                    dedupe=cmd.get("dedupe", self.guardrails.dedupe_seconds),
                )
                self.notifier.configure(
                    sound=cmd.get("sound", self.notifier.sound_enabled),
                    token=cmd.get("telegram_token", self.notifier.telegram_token),
                    chat_id=cmd.get("telegram_chat_id", self.notifier.telegram_chat_id),
                )
            elif action == "close":
                self._do_close(cmd.get("pair"))
            elif action == "reset_daily":
                self.guardrails.reset_daily()
                self.log("Daily loss limit reset — trading re-enabled", status="OK")
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
        )
        self._emit("status", connected=True, exchange=cmd["exchange_id"])
        self.log(f"Connected to {cmd['exchange_id']}", status="Connected")
        self._fail_count = 0
        self._alerted = False

        # Start the real-time price feed (skip in Safe Mode sim — no live data).
        if not self.exchange.safe_mode and self.price_feed is None:
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

        # Place protective SL/TP from the alert payload.
        if result.ok and self.auto_bracket and (sl or tp1 or tp2):
            self._place_brackets(result.pair or symbol, side, size, sl, tp1, tp2, source)

        self._refresh()

    def _place_brackets(self, symbol, entry_side, size, sl, tp1, tp2, source) -> None:
        """Place reduce-only stop-loss and (scaled) take-profit orders."""
        ex_side = exit_side(entry_side)
        if sl > 0:
            r = self.exchange.place_reduce_order(symbol, ex_side, size, sl, "sl")
            self.log(r.message, signal="SL", pair=symbol, status="OK" if r.ok else "Rejected")
            history.record_trade(source, symbol, ex_side, "sl", size, sl,
                                 "Filled" if r.ok else "Rejected", message=r.message)
        for price, qty in plan_take_profits(size, tp1, tp2, TP1_SCALE_OUT):
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
            # Feed the daily-loss guardrail; trip & halt if the limit is breached.
            if self.guardrails.record_realized(pnl):
                msg = (
                    f"Daily loss limit hit ({self.guardrails.daily_realized:+.2f}). "
                    "New entries halted — reset in Guardrails to resume."
                )
                self.log(msg, status="Halted")
                self._emit("alert", level="error", message=msg)
                self.notifier.notify("Daily loss limit", msg, level="error")

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
