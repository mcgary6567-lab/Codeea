"""Built-in strategy runner — generates entries with no TradingView needed.

Runs on its own daemon thread with a public (keyless) ccxt client, exactly like
:class:`pricefeed.PriceFeed`, so it never touches the backend's authenticated
client (the single-threaded-I/O rule in ``backend.py`` is preserved). Every
poll it fetches recent candles for each watched symbol, drops the still-forming
candle, replays :func:`strategy.evaluate`, and on a freshly-confirmed BUY calls
``on_signal`` with the *same* normalised payload a TradingView webhook would
produce (``source="strategy"``) — so the trade flows through the identical
guardrail → sizing → order → bracket pipeline.

State is rebuilt from candle history on every poll (the engine is pure), so a
restart mid-setup loses nothing. A per-symbol "last seen candle" timestamp
ensures each closed candle is evaluated exactly once, and symbols are *primed*
on first sight so the bot never chases a signal that closed before it started.
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Optional

try:
    import ccxt

    CCXT_AVAILABLE = True
except ImportError:
    ccxt = None
    CCXT_AVAILABLE = False

import strategy
from config import (
    QUOTE_CURRENCY,
    STRATEGY_CANDLE_LIMIT,
    STRATEGY_POLL_INTERVAL,
)
from exchange import normalize_symbol


class StrategyRunner:
    def __init__(
        self,
        on_signal: Callable[[dict], None],
        log: Callable[[str], None],
    ) -> None:
        self.on_signal = on_signal
        self.log = log

        self._lock = threading.Lock()
        self._cfg: dict = {
            "enabled": False,
            "symbols": [],
            "timeframe": "5m",
            "params": strategy.StrategyParams(),
            "exchange_id": None,
            "market_type": "spot",
        }
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.running = False

        self._client = None
        self._client_key = None          # (exchange_id, market_type) the client is for
        self._last_ts: Dict[str, int] = {}   # symbol -> newest closed candle seen
        self._primed: set = set()        # symbols whose first candle has been seen
        self._fail_count = 0

    # -- public API ---------------------------------------------------------
    def configure(self, **cfg) -> None:
        """Update config from the GUI. Clears per-symbol priming when the symbol
        set or timeframe changes so a new selection re-primes (never fires on a
        candle that closed before the change)."""
        with self._lock:
            old_syms = list(self._cfg.get("symbols") or [])
            old_tf = self._cfg.get("timeframe")
            self._cfg.update(cfg)
            new_syms = list(self._cfg.get("symbols") or [])
            if new_syms != old_syms or self._cfg.get("timeframe") != old_tf:
                self._primed.clear()
                self._last_ts.clear()

    def _snapshot(self) -> dict:
        with self._lock:
            cfg = dict(self._cfg)
        cfg["symbols"] = list(cfg.get("symbols") or [])
        return cfg

    def start(self) -> None:
        if self.running:
            return
        if not CCXT_AVAILABLE:
            self.log("Built-in strategy: ccxt not available")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.running = True
        self.log("Built-in strategy: started")

    def stop(self) -> None:
        if not self.running:
            return
        self._stop.set()
        self.running = False
        self.log("Built-in strategy: stopped")

    # -- worker -------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            cfg = self._snapshot()
            if cfg["enabled"] and cfg["symbols"] and cfg["exchange_id"]:
                try:
                    self._ensure_client(cfg["exchange_id"], cfg["market_type"])
                    for sym in cfg["symbols"]:
                        if self._stop.is_set():
                            break
                        self._step(sym, cfg)
                    self._fail_count = 0
                except Exception as exc:  # noqa: BLE001 - keep the loop alive
                    self._fail_count += 1
                    if self._fail_count in (1, 5, 20):   # log sparingly
                        self.log(f"Built-in strategy: error ({exc})")
            self._stop.wait(STRATEGY_POLL_INTERVAL)

    def _ensure_client(self, exchange_id: str, market_type: str) -> None:
        key = (exchange_id, market_type)
        if self._client is not None and self._client_key == key:
            return
        params = {
            "enableRateLimit": True,
            "options": {"defaultType": "swap" if market_type == "futures" else "spot"},
        }
        self._client = getattr(ccxt, exchange_id)(params)
        self._client_key = key

    def _market_symbol(self, symbol: str, market_type: str) -> str:
        sym = normalize_symbol(symbol)
        if market_type != "futures" or ":" in sym:
            return sym
        base, _, quote = sym.partition("/")
        return f"{base}/{quote}:{quote}"

    def _step(self, sym: str, cfg: dict) -> None:
        market_sym = self._market_symbol(sym, cfg["market_type"])
        ohlcv = self._client.fetch_ohlcv(market_sym, cfg["timeframe"],
                                         limit=STRATEGY_CANDLE_LIMIT)
        # Drop the still-forming candle so we only ever act on closed bars.
        closed: List[list] = ohlcv[:-1] if len(ohlcv) > 1 else []
        if len(closed) < 2:
            return
        newest_ts = int(closed[-1][0])

        # Prime on first sight: adopt the current candle without firing, so a
        # restart never chases a signal that had already closed.
        if sym not in self._primed:
            self._primed.add(sym)
            self._last_ts[sym] = newest_ts
            return

        # Evaluate each freshly-closed candle exactly once.
        if self._last_ts.get(sym, 0) >= newest_ts:
            return
        self._last_ts[sym] = newest_ts

        sig = strategy.evaluate(closed, cfg["params"], ticker=sym)
        if sig is None:
            return

        self.log(f"Built-in strategy: BUY {sym} @ {sig.entry:g} "
                 f"(TP1 {sig.tp1:g} / TP2 {sig.tp2:g}"
                 + (f", SL {sig.sl:g}" if sig.sl else "") + ")")
        self.on_signal({
            "action": "buy",
            "event": "",
            "ticker": sym,
            "size": None,
            "entry": sig.entry,
            "sl": sig.sl,
            "tp1": sig.tp1,
            "tp2": sig.tp2,
            "price": sig.entry,
            "source": "strategy",
        })
