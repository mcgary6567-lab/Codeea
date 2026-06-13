"""Real-time price feed for open-position symbols.

Two backends, chosen automatically:

  * **WebSocket** (preferred) via ``ccxt.pro`` — ``watch_tickers`` pushes ticks
    as they happen, on its own asyncio loop in a daemon thread.
  * **REST poll** (fallback) — fast ``fetch_tickers`` every
    ``PRICE_POLL_INTERVAL`` seconds using a public (keyless) client. Used when
    ccxt.pro / websockets aren't available or the WS stream errors out.

The feed only ever requests the symbols you currently hold, so it stays well
within rate limits. On every update it calls ``on_prices({symbol: price})`` —
the backend caches those and recomputes PnL locally for an instant UI refresh,
independent of the slower REST position refresh.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional

try:
    import ccxt

    CCXT_AVAILABLE = True
except ImportError:
    ccxt = None
    CCXT_AVAILABLE = False

try:  # ccxt.pro ships inside ccxt>=4 but needs websocket extras to actually run
    import ccxt.pro as ccxtpro  # noqa: F401

    CCXTPRO_AVAILABLE = True
except Exception:  # noqa: BLE001 - any import/runtime issue => no websockets
    ccxtpro = None
    CCXTPRO_AVAILABLE = False

from config import PRICE_POLL_INTERVAL
from exchange import market_ccxt_spec


class PriceFeed:
    def __init__(
        self,
        exchange_id: str,
        on_prices: Callable[[Dict[str, float]], None],
        log: Callable[[str], None],
        market_type: str = "spot",
    ) -> None:
        self.exchange_id = exchange_id
        self.on_prices = on_prices
        self.log = log
        # Build the data client for the right market so futures (swap) tickers
        # resolve — and so Kraken/Coinbase use their dedicated futures class.
        self._ccxt_id, self._default_type = market_ccxt_spec(exchange_id, market_type)

        self._symbols: List[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.mode = "off"

    # -- public API ---------------------------------------------------------
    def set_symbols(self, symbols: List[str]) -> None:
        with self._lock:
            self._symbols = list(dict.fromkeys(symbols))  # de-dupe, keep order

    def _current_symbols(self) -> List[str]:
        with self._lock:
            return list(self._symbols)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not CCXT_AVAILABLE:
            self.mode = "off"
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -- worker -------------------------------------------------------------
    def _run(self) -> None:
        if CCXTPRO_AVAILABLE:
            try:
                self.mode = "websocket"
                self.log("Price feed: WebSocket (ccxt.pro)")
                self._run_ws()
                return
            except Exception as exc:  # noqa: BLE001 - fall back to REST
                self.log(f"Price feed: WS unavailable ({exc}); using REST poll")
        self.mode = "rest"
        self.log("Price feed: REST poll")
        self._run_rest()

    # -- REST fallback ------------------------------------------------------
    def _run_rest(self) -> None:
        client = getattr(ccxt, self._ccxt_id)({
            "enableRateLimit": True, "options": {"defaultType": self._default_type}})
        while not self._stop.is_set():
            symbols = self._current_symbols()
            if symbols:
                prices = self._rest_tickers(client, symbols)
                if prices:
                    self.on_prices(prices)
            self._stop.wait(PRICE_POLL_INTERVAL)

    def _rest_tickers(self, client, symbols: List[str]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        try:
            if client.has.get("fetchTickers"):
                tickers = client.fetch_tickers(symbols)
                for sym, t in tickers.items():
                    if t.get("last") is not None:
                        out[sym] = float(t["last"])
                return out
        except Exception:  # noqa: BLE001 - fall through to per-symbol
            pass
        for sym in symbols:
            try:
                out[sym] = float(client.fetch_ticker(sym)["last"])
            except Exception:  # noqa: BLE001 - skip a bad symbol, keep going
                continue
        return out

    # -- WebSocket (ccxt.pro) ----------------------------------------------
    def _run_ws(self) -> None:
        import asyncio

        async def loop() -> None:
            client = getattr(ccxtpro, self._ccxt_id)({
                "enableRateLimit": True, "options": {"defaultType": self._default_type}})
            try:
                while not self._stop.is_set():
                    symbols = self._current_symbols()
                    if not symbols:
                        await asyncio.sleep(0.5)
                        continue
                    try:
                        tickers = await client.watch_tickers(symbols)
                    except Exception:  # noqa: BLE001 - bubble up to REST fallback
                        raise
                    prices = {
                        s: float(t["last"])
                        for s, t in tickers.items()
                        if t.get("last") is not None
                    }
                    if prices:
                        self.on_prices(prices)
            finally:
                try:
                    await client.close()
                except Exception:  # noqa: BLE001
                    pass

        asyncio.run(loop())
