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

from config import LOG_FILE, MAX_REFRESH_FAILURES, POLL_INTERVAL, QUOTE_CURRENCY
from exchange import ExchangeManager, Position, recompute_pnl
from pricefeed import PriceFeed


class Backend:
    def __init__(self) -> None:
        self.exchange = ExchangeManager()
        self.command_queue: "queue.Queue[dict]" = queue.Queue()
        self.ui_queue: "queue.Queue[dict]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Sizing settings, updated by the GUI via the "settings" command.
        self.fixed_size = 0.001
        self.risk_based = False
        self.risk_percent = 1.0

        # Real-time state shared with the price feed thread (guarded by _lock).
        self._lock = threading.Lock()
        self._price_cache: dict = {}
        self._last_positions: list[Position] = []
        self._last_balance: float = 0.0
        self._open_pairs: set = set()
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
                self.risk_based = cmd.get("risk_based", self.risk_based)
                self.risk_percent = cmd.get("risk_percent", self.risk_percent)
                self.exchange.safe_mode = cmd.get("safe_mode", self.exchange.safe_mode)
                self.exchange.read_only = cmd.get("read_only", self.exchange.read_only)
            elif action == "trade":
                self._do_trade(cmd)
            elif action == "refresh":
                self._refresh()
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
        symbol = cmd["symbol"]
        side = cmd["side"]
        source = cmd.get("source", "manual")

        balance = self.exchange.fetch_balance()
        size = cmd.get("size")
        if size is None:
            size = self.exchange.compute_amount(
                symbol, self.fixed_size, self.risk_based, self.risk_percent, balance
            )
        result = self.exchange.place_market_order(symbol, side, float(size))

        status = "Filled" if result.ok else "Rejected"
        if result.simulated:
            status = "Simulated"
        self.log(
            result.message,
            signal=side.upper(),
            pair=result.pair or symbol,
            status=status,
        )
        self._emit("order", ok=result.ok, source=source, message=result.message)
        self._refresh()

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

        # Keep the price feed subscribed to exactly the symbols we hold.
        if self.price_feed:
            self.price_feed.set_symbols(list(new_pairs))

        self._emit_account()

    def _on_prices(self, prices: dict) -> None:
        """Called from the price-feed thread on every tick/poll. No REST here."""
        with self._lock:
            self._price_cache.update(prices)
        self._emit_account()

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
        """Log a 'Closed' row for any position that vanished since last refresh."""
        with self._lock:
            closed = self._open_pairs - new_pairs
        for pair in sorted(closed):
            self.log(f"Position {pair} closed", signal="CLOSE", pair=pair, status="Closed")

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
