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

from config import LOG_FILE, POLL_INTERVAL, QUOTE_CURRENCY
from exchange import ExchangeManager


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

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
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
                self.exchange.disconnect()
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
        if not self.exchange.connected:
            return
        try:
            balance = self.exchange.fetch_balance()
            positions = self.exchange.fetch_positions()
            pnl = self.exchange.total_pnl(positions)
            self._emit(
                "account",
                balance=balance,
                pnl=pnl,
                positions=[p.__dict__ for p in positions],
            )
        except Exception as exc:  # noqa: BLE001
            self.log(f"Refresh failed: {exc}", status="Error")

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
