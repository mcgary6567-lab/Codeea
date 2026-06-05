"""Built-in strategy runner — generates entries with no TradingView needed.

Runs on its own daemon thread with a public (keyless) ccxt client, exactly like
:class:`pricefeed.PriceFeed`, so it never touches the backend's authenticated
client (the single-threaded-I/O rule in ``backend.py`` is preserved). Every
poll it fetches recent candles for each watched symbol, drops the still-forming
candle, replays :func:`strategy.evaluate_crossover`, and emits the resulting
entry / scale-out / exit instructions via ``on_signal`` with the *same*
normalised payload a TradingView webhook would produce (``source="strategy"``)
— so trades flow through the identical guardrail → sizing → order → bracket
pipeline.

State is rebuilt from candle history on every poll (the engine is pure), so a
restart mid-setup loses nothing. A per-symbol "last seen candle" timestamp
ensures each closed candle is evaluated exactly once, and symbols are *primed*
on first sight so the bot never chases a signal that closed before it started.
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

import licence
import strategy
from config import (
    LICENCE_GRACE_SECONDS,
    LICENCE_RECHECK_INTERVAL,
    LICENCE_RETRY_INTERVAL,
    QUOTE_CURRENCY,
    STRATEGY_CANDLE_LIMIT,
    STRATEGY_POLL_INTERVAL,
)
from exchange import market_ccxt_spec, normalize_symbol, resolve_market_symbol


class StrategyRunner:
    def __init__(
        self,
        on_signal: Callable[[dict], None],
        log: Callable[[str], None],
        get_token: Callable[[], str] = lambda: "",
        get_verify_url: Callable[[], str] = lambda: "",
        get_positions: Callable[[], dict] = lambda: {},
    ) -> None:
        self.on_signal = on_signal
        self.log = log
        # Licence gate: the built-in strategy only trades with a valid token
        # (same subscription token as the cloud feed).
        self.get_token = get_token
        self.get_verify_url = get_verify_url
        # Live exchange positions {base-symbol: "long"/"short"} — used to keep the
        # runner's intended state reconciled with reality (adopt existing positions
        # on restart; drop ones closed externally by a stop).
        self.get_positions = get_positions
        self._intended: Dict[str, str] = {}   # symbol -> "flat"/"long"/"short"
        self._absent: Dict[str, int] = {}     # consecutive polls a held position was unseen
        self._ABSENT_DROP = 3                 # drop intent after this many absent polls
        self._licensed: Optional[bool] = None     # tri-state: None/True/False
        self._last_ok_ts = 0.0                     # last successful verification
        self._next_verify_ts = 0.0                 # when to re-check

        self._lock = threading.Lock()
        self._cfg: dict = {
            "enabled": False,
            "symbols": [],
            "timeframe": "5m",
            "params": strategy.StrategyParams(),
            "exchange_id": None,
            "market_type": "spot",
        }
        self._warned_spot_short = False      # one-shot "shorts need futures" notice
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
        # A settings change may include a corrected token — re-verify promptly.
        self._next_verify_ts = 0.0

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
        self._next_verify_ts = 0.0        # verify the licence on the first cycle
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
                # Licence gate first — no valid token, no signals.
                if self._ensure_licensed():
                    try:
                        self._ensure_client(cfg["exchange_id"], cfg["market_type"])
                        live = self._safe_positions()
                        for sym in cfg["symbols"]:
                            if self._stop.is_set():
                                break
                            self._reconcile(sym, live)
                            self._step(sym, cfg)
                        self._fail_count = 0
                    except Exception as exc:  # noqa: BLE001 - keep the loop alive
                        self._fail_count += 1
                        if self._fail_count in (1, 5, 20):   # log sparingly
                            self.log(f"Built-in strategy: error ({exc})")
            self._stop.wait(STRATEGY_POLL_INTERVAL)

    # -- licence gate -------------------------------------------------------
    def licensed(self) -> Optional[bool]:
        """Last known licence state (None until first verified) — for the GUI."""
        return self._licensed

    def _ensure_licensed(self) -> bool:
        """Verify the licence on a schedule; return whether trading is allowed.

        Between checks the cached decision is honoured. A server *rejection* is a
        hard deny; a server *outage* is tolerated for ``LICENCE_GRACE_SECONDS``
        after the last good check so a transient blip doesn't strand a paying
        user mid-session."""
        now = time.time()
        if now >= self._next_verify_ts:
            self._verify_licence(now)
        return self._licensed is True

    def _verify_licence(self, now: float) -> None:
        prev = self._licensed
        status, msg, expires_at = licence.verify(self.get_verify_url(), self.get_token())
        if status == "ok":
            self._licensed = True
            self._last_ok_ts = now
            self._next_verify_ts = now + LICENCE_RECHECK_INTERVAL
            if prev is not True:
                extra = ""
                if expires_at:
                    days = max(0, int((expires_at - now) / 86400))
                    extra = f" (expires in ~{days}d)"
                self.log(f"Built-in strategy: licence verified{extra}")
        elif status == "error":
            within_grace = self._last_ok_ts and (now - self._last_ok_ts) < LICENCE_GRACE_SECONDS
            self._licensed = bool(within_grace)
            self._next_verify_ts = now + LICENCE_RETRY_INTERVAL
            if prev is True and not within_grace:
                self.log(f"Built-in strategy: paused — {msg}")
        else:  # rejected
            self._licensed = False
            # Re-check fairly soon so a fixed/renewed token resumes promptly.
            self._next_verify_ts = now + LICENCE_RETRY_INTERVAL
            if prev is not False:
                self.log(f"Built-in strategy: disabled — {msg}")

    def _ensure_client(self, exchange_id: str, market_type: str) -> None:
        key = (exchange_id, market_type)
        if self._client is not None and self._client_key == key:
            return
        # Use the same class/defaultType resolution as the live order path so the
        # candle feed works on Kraken/Coinbase futures and on Binance fapi.
        ccxt_id, default_type = market_ccxt_spec(exchange_id, market_type)
        self._client = getattr(ccxt, ccxt_id)({
            "enableRateLimit": True,
            "options": {"defaultType": default_type},
        })
        self._client_key = key

    def _step(self, sym: str, cfg: dict) -> None:
        market_sym = resolve_market_symbol(self._client, sym, cfg["market_type"],
                                           cfg["exchange_id"])
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
        self._emit_ma(closed, sym, cfg)

    def _emit_ma(self, closed: List[list], sym: str, cfg: dict) -> None:
        """Translate the newest candle's crossover instructions into the same
        signal payloads a webhook would send.

        Entries become buy/sell + a swing stop; an RSI-extreme scale-out becomes a
        partial close; an EMA-trail/stop exit becomes a full close. Shorts need a
        Futures market (spot can't short), so they're skipped on spot with a
        one-shot notice."""
        futures = cfg.get("market_type") == "futures"
        intended = self._intended.get(sym, "flat")
        for ev in strategy.evaluate_crossover(closed, cfg["params"], ticker=sym):
            act = ev["act"]
            if act == "enter":
                side = ev["side"]
                if side == "short" and not futures:
                    if not self._warned_spot_short:
                        self.log("MA strategy: short skipped — switch Market to "
                                 "Futures to trade shorts.")
                        self._warned_spot_short = True
                    continue
                if intended != "flat":
                    continue                 # already hold a position (reconciled) — don't stack
                action = "buy" if side == "long" else "sell"
                self.log(f"MA strategy: {action.upper()} ({side}) {sym} "
                         f"@ {ev['entry']:g}, SL {ev['sl']:g}")
                self.on_signal({
                    "action": action, "event": "", "ticker": sym, "size": None,
                    "entry": ev["entry"], "sl": ev["sl"], "tp1": None, "tp2": None,
                    "price": ev["entry"], "source": "strategy",
                })
                intended = side
            elif act == "scale_out":
                if intended == "flat":
                    continue                 # nothing held to de-risk
                self.log(f"MA strategy: scale out {int(ev['fraction'] * 100)}% {sym} "
                         f"(RSI extreme)")
                self.on_signal({"event": "scale_out", "ticker": sym,
                                "fraction": ev["fraction"], "source": "strategy"})
            elif act == "exit":
                if intended == "flat":
                    continue                 # nothing held to close
                self.log(f"MA strategy: exit {sym} (EMA trail / stop)")
                self.on_signal({"event": "exit", "ticker": sym, "source": "strategy"})
                intended = "flat"
        self._intended[sym] = intended

    @staticmethod
    def _poskey(sym: str) -> str:
        return normalize_symbol(str(sym).split(":")[0])

    def _safe_positions(self) -> dict:
        try:
            return self.get_positions() or {}
        except Exception:  # noqa: BLE001
            return {}

    def _reconcile(self, sym: str, live: dict) -> None:
        """Keep the runner's intended state in sync with the real exchange.

        *Adopt* an existing position immediately (e.g. after a restart, or one
        opened manually) so the runner manages its exits. *Drop* one only after a
        short grace window of absence — the backend reflects a freshly-opened
        position a poll or two late, so we must not flatten our own new entry."""
        actual = live.get(self._poskey(sym))     # "long" / "short" / None
        cur = self._intended.get(sym, "flat")
        if actual in ("long", "short"):
            self._absent[sym] = 0
            if cur == "flat":
                self._intended[sym] = actual
                self.log(f"MA strategy: adopted existing {actual} {sym} position")
        elif cur != "flat":
            self._absent[sym] = self._absent.get(sym, 0) + 1
            if self._absent[sym] >= self._ABSENT_DROP:   # confirmed closed externally
                self._intended[sym] = "flat"
                self._absent[sym] = 0
                self.log(f"MA strategy: {sym} position closed externally — reset")
        else:
            self._absent[sym] = 0
