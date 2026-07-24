"""Exchange connectivity via ccxt.

Wraps every supported exchange behind one interface so the GUI doesn't care
which is selected. All network calls are funnelled through a single backend
worker thread (see ``backend.py``) so we never hit a ccxt client from two
threads at once.

Safe Mode (dry run) and Read-only mode are enforced *here* — the single choke
point for anything that could place a real order.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

try:
    import ccxt

    CCXT_AVAILABLE = True
except ImportError:  # Allows the GUI to launch for a demo without ccxt.
    ccxt = None
    CCXT_AVAILABLE = False

from config import (
    QUOTE_CURRENCY,
    SPOT_ONLY_EXCHANGES,
    futures_ccxt_id,
    futures_default_type,
    futures_quote,
)


def _friendly_error(exchange_id: str, exc: Exception, is_futures: bool = False) -> str:
    """Turn a raw ccxt error into actionable guidance for common cases."""
    msg = str(exc)
    low = msg.lower()
    if "451" in msg or "restricted location" in low or "eligibility" in low:
        if exchange_id == "binance":
            return ("Binance.com is blocked in your region. If you're in the US, "
                    "select 'Binance.US' in the exchange list; otherwise use Bybit, "
                    "OKX, KuCoin or Bitget.\n\n(" + msg + ")")
        return (f"{exchange_id} is blocked from your region/IP (HTTP 451). "
                "Try a different exchange or disable any VPN.\n\n(" + msg + ")")
    if "-2015" in msg or ("invalid api" in low and "permission" in low):
        if is_futures:
            return ("Futures rejected (code -2015). If Spot connects with this key, "
                    "your key and IP are already fine — Futures needs its own setup:\n"
                    "  1) Open a Futures account on the exchange (accept the agreement / "
                    "quiz) — until you do, the key's Futures permission does nothing.\n"
                    "  2) Enable the 'Futures' permission on the API key (re-create the "
                    "key if you turned it on after creating it).\n"
                    "  3) Make sure Futures is available in your region.\n\n(" + msg + ")")
        return ("Invalid API key, IP, or permissions. Enable Spot/Futures trading "
                "on the key and add your IP to its whitelist (use 'Show my IP').\n\n("
                + msg + ")")
    if exchange_id == "coinbase" and is_futures:
        return ("Coinbase futures run on Coinbase International Exchange "
                "(institutional / non-US). Use a Coinbase International API key, or "
                "switch to Coinbase Spot / Kraken futures.\n\n(" + msg + ")")
    if exchange_id == "kraken" and is_futures and ("auth" in low or "invalid" in low or "permission" in low):
        return ("Kraken Futures uses a SEPARATE API key from Kraken Spot — create "
                "one in the Kraken Futures dashboard (not the spot key) with trading "
                "permission.\n\n(" + msg + ")")
    return msg


@dataclass
class Position:
    pair: str
    side: str          # "Long" / "Short"
    size: float
    entry: float
    current: float
    pnl: float
    status: str = "Active"


@dataclass
class OrderResult:
    ok: bool
    message: str
    pair: str = ""
    side: str = ""
    order_type: str = "Market"
    simulated: bool = False
    raw: dict = field(default_factory=dict)


class ExchangeError(Exception):
    pass


def normalize_symbol(raw: str, default_quote: str = QUOTE_CURRENCY) -> str:
    """Turn 'BTCUSDT', 'btc/usdt', 'BTC' into a ccxt symbol 'BTC/USDT'."""
    s = raw.strip().upper().replace("-", "/").replace("_", "/")
    if "/" in s:
        return s
    # No separator: try to split a known quote suffix, else append default.
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH", "EUR"):
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[:-len(quote)]}/{quote}"
    return f"{s}/{default_quote}"


def market_ccxt_spec(exchange_id: str, market_type: str) -> tuple:
    """``(ccxt_class_id, defaultType)`` for a data/order client on this venue and
    market. Mirrors :class:`ExchangeManager` so charts, backtests, the strategy
    candle feed and the price feed all use the correct futures class
    (krakenfutures / coinbaseinternational) and the right ``defaultType`` instead
    of naively assuming the spot class with ``swap``."""
    futures = market_type == "futures" and exchange_id not in SPOT_ONLY_EXCHANGES
    if futures:
        cid = futures_ccxt_id(exchange_id)
        if not CCXT_AVAILABLE or cid in getattr(ccxt, "exchanges", []):
            return cid, futures_default_type(exchange_id)
        # Futures class isn't in this ccxt build (e.g. coinbaseinternational) —
        # degrade to spot data so callers don't crash.
    return exchange_id, "spot"


def perp_symbol(symbol: str, exchange_id: str) -> str:
    """``BASE/QUOTE:QUOTE`` perpetual for this venue, using the venue's futures
    quote (USDT default, Kraken=USD, Coinbase=USDC) — not the spot quote."""
    sym = normalize_symbol(symbol)
    if ":" in sym:
        return sym
    base = sym.partition("/")[0]
    q = futures_quote(exchange_id)
    return f"{base}/{q}:{q}"


def resolve_market_symbol(client, symbol: str, market_type: str,
                          exchange_id: str) -> str:
    """The venue's real market symbol for ``symbol``: spot stays ``BASE/QUOTE``;
    futures becomes the right perp, verified against loaded markets with a linear
    fallback (same adaptive logic the main order path uses)."""
    sym = normalize_symbol(symbol)
    if market_type != "futures" or exchange_id in SPOT_ONLY_EXCHANGES:
        return sym
    candidate = perp_symbol(sym, exchange_id)
    try:
        markets = getattr(client, "markets", None) or client.load_markets()
        if candidate in markets:
            return candidate
        base = sym.partition("/")[0]
        quote = futures_quote(exchange_id)
        for m in markets.values():
            if ((m.get("swap") or m.get("future")) and m.get("linear", True)
                    and m.get("base") == base and m.get("quote") == quote):
                return m["symbol"]
    except Exception:  # noqa: BLE001 - best effort; fall back to the candidate
        pass
    return candidate


def _rank_pairs(client, want_type: str, quotes: set, limit: int) -> List[str]:
    """Rank a client's pairs by 24h quote volume, keeping only ``want_type``
    markets quoted in ``quotes``; BTC/ETH floated to the front. Best-effort."""
    try:
        tickers = client.fetch_tickers()
    except Exception:  # noqa: BLE001
        return []
    rows = []
    for sym, t in tickers.items():
        m = client.markets.get(sym, {}) if hasattr(client, "markets") else {}
        if m.get("quote") not in quotes or m.get("type") != want_type:
            continue
        if not m.get("active", True):
            continue
        base = m.get("base", sym.split("/")[0])
        quote = m.get("quote", QUOTE_CURRENCY)
        try:
            vol = float(t.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        rows.append((f"{base}/{quote}", vol))
    rows.sort(key=lambda r: r[1], reverse=True)
    ordered = [s for s, _ in rows][:limit]
    for base in ("ETH", "BTC"):
        for i, s in enumerate(ordered):
            if s.startswith(base + "/"):
                ordered.insert(0, ordered.pop(i))
                break
    return ordered


def fetch_top_pairs(exchange_id: str, market_type: str = "spot", limit: int = 20) -> List[str]:
    """Keyless top-pairs lookup: build a *public* data client for this venue and
    market (right class/defaultType via market_ccxt_spec) and rank its pairs.
    Used to populate the symbol dropdowns when the exchange is changed but not yet
    connected. Returns [] on any failure (offline / class missing)."""
    if not CCXT_AVAILABLE:
        return []
    ccxt_id, default_type = market_ccxt_spec(exchange_id, market_type)
    if ccxt_id not in getattr(ccxt, "exchanges", []):
        return []
    futures = default_type != "spot"
    quotes = {futures_quote(exchange_id)} if futures else {"USDT", "USDC", "USD"}
    want_type = "swap" if futures else "spot"
    try:
        client = getattr(ccxt, ccxt_id)({
            "enableRateLimit": True, "options": {"defaultType": default_type}})
        client.load_markets()
    except Exception:  # noqa: BLE001
        return []
    return _rank_pairs(client, want_type, quotes, limit)


def recompute_pnl(positions: List[Position], prices: dict) -> List[Position]:
    """Return positions with ``current`` and ``pnl`` refreshed from ``prices``.

    Pure function (no I/O) so it is trivially testable. PnL is computed as
    ``(current - entry) * size`` for longs and ``(entry - current) * size`` for
    shorts. Symbols missing from ``prices`` keep their existing current price.
    """
    updated: List[Position] = []
    for p in positions:
        current = float(prices.get(p.pair, p.current) or p.current)
        if p.side == "Long":
            pnl = (current - p.entry) * p.size
        else:
            pnl = (p.entry - current) * p.size
        updated.append(
            Position(
                pair=p.pair, side=p.side, size=p.size, entry=p.entry,
                current=current, pnl=pnl, status=p.status,
            )
        )
    return updated


def size_order(
    mode: str,
    fixed_size: float,
    risk_percent: float,
    balance: float,
    price: float,
    entry: float = 0.0,
    stop: float = 0.0,
) -> tuple:
    """Pure sizing logic. Returns ``(amount, reason)``.

    * ``fixed``        – the fixed lot (in the base coin), unchanged.
    * ``fixed_quote``  – ``fixed_size`` is a USDT amount; buy that many dollars'
      worth: ``amount = fixed_size / price``.
    * ``risk_balance`` – spend ``risk_percent`` % of balance at ``price``.
    * ``risk_stop``    – risk ``risk_percent`` % of balance across the
      ``entry``→``stop`` distance: ``amount = risk_amount / |entry - stop|``.
      This is the proper per-trade risk model and pairs with the indicator's SL.

    Any mode falls back to the fixed lot when its required inputs are missing,
    so a trade is never sized to zero by accident.
    """
    risk_amount = balance * (risk_percent / 100.0)
    if mode == "fixed_quote":
        if price > 0 and fixed_size > 0:
            return round(fixed_size / price, 8), f"${fixed_size:g} / price {price:g}"
        return fixed_size, "fixed $: no price — using raw size"
    if mode == "risk_stop":
        ref = entry if entry > 0 else price
        dist = abs(ref - stop) if (ref > 0 and stop > 0) else 0.0
        if dist > 0 and risk_amount > 0:
            return round(risk_amount / dist, 8), f"risk {risk_percent}% / stop dist {dist:g}"
        return fixed_size, "risk_stop: missing entry/stop — using fixed lot"
    if mode == "risk_balance":
        if price > 0 and risk_amount > 0:
            return round(risk_amount / price, 8), f"risk {risk_percent}% of balance"
        return fixed_size, "risk_balance: no price — using fixed lot"
    return fixed_size, "fixed lot"


def plan_take_profits(amount: float, tp1: float, tp2: float, tp1_fraction: float) -> list:
    """Return a list of ``(price, qty)`` take-profit legs.

    Both TPs -> scale out ``tp1_fraction`` at TP1 and the remainder at TP2.
    One TP   -> the full amount at that level. None -> empty list.
    """
    legs = []
    if tp1 > 0 and tp2 > 0:
        q1 = round(amount * tp1_fraction, 8)
        q2 = round(amount - q1, 8)
        if q1 > 0:
            legs.append((tp1, q1))
        if q2 > 0:
            legs.append((tp2, q2))
    elif tp1 > 0:
        legs.append((tp1, amount))
    elif tp2 > 0:
        legs.append((tp2, amount))
    return legs


def exit_side(entry_side: str) -> str:
    """The side that reduces/closes a position opened with ``entry_side``."""
    return "sell" if entry_side.lower() == "buy" else "buy"


# Quote / stablecoin assets that are not treated as spot "positions".
STABLES = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USD", "USDD"}


class ExchangeManager:
    """Stateful wrapper around a single ccxt exchange client."""

    def __init__(self) -> None:
        self.client = None
        self.exchange_id: Optional[str] = None
        self.connected: bool = False
        self.read_only: bool = False
        self.safe_mode: bool = False
        self.market_type: str = "spot"        # "spot" or "futures"
        self._fut_quote: str = QUOTE_CURRENCY  # futures margin/quote ccy (per venue)
        self._spot_entries: dict = {}          # asset -> entry price (bot buys)
        # Simulated state used when safe_mode is on or ccxt is missing.
        self._sim_balance: float = 10_000.0
        self._sim_positions: List[Position] = []

    @property
    def is_futures(self) -> bool:
        return self.market_type == "futures"

    # -- connection ---------------------------------------------------------
    def connect(
        self,
        exchange_id: str,
        api_key: str,
        secret: str,
        password: str = "",
        testnet: bool = False,
        read_only: bool = False,
        safe_mode: bool = False,
        market_type: str = "spot",
    ) -> None:
        self.exchange_id = exchange_id
        self.read_only = read_only
        self.safe_mode = safe_mode
        self.market_type = "futures" if market_type == "futures" else "spot"
        # Spot-only platforms (e.g. Binance.US) have no futures product line.
        if exchange_id in SPOT_ONLY_EXCHANGES:
            self.market_type = "spot"
        # Margin/quote currency for this venue's linear futures (USDT-M default;
        # Kraken=USD, Coinbase=USDC) — used to build perp symbols / filter pairs.
        self._fut_quote = futures_quote(exchange_id)

        if not CCXT_AVAILABLE:
            # Demo/simulation mode: pretend we connected.
            self.connected = True
            return

        if exchange_id not in ccxt.exchanges:
            raise ExchangeError(f"Unknown exchange: {exchange_id}")

        # Some venues run futures on a SEPARATE ccxt class (Kraken -> krakenfutures,
        # Coinbase -> coinbaseinternational), not a defaultType switch on the spot
        # class. Pick the right class for the chosen market type.
        ccxt_id = futures_ccxt_id(exchange_id) if self.is_futures else exchange_id
        if ccxt_id not in ccxt.exchanges:
            if ccxt_id == "coinbaseinternational":
                raise ExchangeError(
                    "Coinbase futures need a Coinbase International Exchange account "
                    "(institutional / non-US). It isn't available in this build or "
                    "your region — use Coinbase Spot, or Kraken for futures.")
            raise ExchangeError(
                f"{exchange_id} futures are unavailable (no '{ccxt_id}' class in ccxt).")
        klass = getattr(ccxt, ccxt_id)
        self._ccxt_id = ccxt_id
        # ccxt keys USD-M futures differently per exchange: Binance uses "future"
        # (fapi); the unified-"swap" venues (Bybit/OKX/KuCoin/Bitget) and the
        # dedicated futures classes (krakenfutures/coinbaseinternational) use "swap".
        if self.is_futures:
            default_type = "future" if exchange_id in ("binance", "binanceus") else "swap"
        else:
            default_type = "spot"
        params = {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": default_type,
                "adjustForTimeDifference": True,  # fixes Binance -1021 clock errors
                "recvWindow": 10000,
            },
        }
        # OKX / KuCoin / Bitget require an API passphrase.
        if password:
            params["password"] = password

        self.client = klass(params)
        # Best-effort: sync local clock offset to the exchange server time.
        try:
            self.client.load_time_difference()
        except Exception:  # noqa: BLE001 - not all exchanges expose it
            pass
        if testnet:
            try:
                self.client.set_sandbox_mode(True)
            except Exception:
                raise ExchangeError(f"{exchange_id} has no testnet in ccxt")

        try:
            self.client.load_markets()
            # A private call validates the keys.
            self.client.fetch_balance()
        except Exception as exc:  # noqa: BLE001 - surface any ccxt error nicely
            # In Safe Mode we never send real orders anyway, so a failed live
            # validation (bad/placeholder keys, offline, geo-block) falls back
            # to pure simulation instead of blocking the user. Live trading
            # still requires a genuine, validated connection.
            if self.safe_mode:
                self.client = None
                self.connected = True
                return
            self.client = None
            raise ExchangeError(_friendly_error(exchange_id, exc, self.is_futures)) from exc

        self.connected = True

    def disconnect(self) -> None:
        self.client = None
        self.connected = False

    # -- account data -------------------------------------------------------
    def fetch_balance(self) -> float:
        """Return total quote-currency balance (USDT).

        Uses the REAL exchange balance whenever there's a live client — even in
        Safe Mode (read-only, no orders). Only falls back to a simulated balance
        when there is no real connection at all (bad keys / offline / no ccxt).
        """
        if not self.connected:
            return 0.0
        if self.client is None:
            return self._sim_balance
        try:
            total = self.client.fetch_balance().get("total", {})
            # Count cash held in any major stablecoin, not just USDT.
            cash = 0.0
            for a in ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USD"):
                try:
                    cash += float(total.get(a, 0) or 0)
                except (TypeError, ValueError):
                    continue
            return cash
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"balance: {exc}") from exc

    def fetch_positions(self) -> List[Position]:
        if not self.connected:
            return []
        # Safe Mode shows your simulated (paper) positions; without a live client
        # we're in pure simulation too.
        if self.safe_mode or self.client is None:
            return list(self._sim_positions)
        if not self.is_futures:
            return self._spot_positions()
        positions: List[Position] = []
        try:
            if self.client.has.get("fetchPositions"):
                for p in self.client.fetch_positions():
                    contracts = float(p.get("contracts") or 0)
                    if contracts == 0:
                        continue
                    entry = float(p.get("entryPrice") or 0)
                    mark = float(p.get("markPrice") or entry)
                    positions.append(
                        Position(
                            pair=p.get("symbol", "?"),
                            side="Long" if p.get("side") == "long" else "Short",
                            size=contracts,
                            entry=entry,
                            current=mark,
                            pnl=float(p.get("unrealizedPnl") or 0),
                        )
                    )
        except Exception:  # noqa: BLE001 - positions are best-effort
            pass
        return positions

    def _spot_positions(self) -> List[Position]:
        """Derive 'positions' from real spot balances (held base assets).

        Entry price is taken from what the bot recorded when it bought; if
        unknown (e.g. coins held before the bot), entry shows as the current
        price (PnL 0). Dust under ~$1 is ignored.
        """
        out: List[Position] = []
        try:
            totals = self.client.fetch_balance().get("total", {})
        except Exception:  # noqa: BLE001
            return out
        for asset, amt in totals.items():
            try:
                amt = float(amt or 0)
            except (TypeError, ValueError):
                continue
            if amt <= 0 or asset.upper() in STABLES:
                continue
            # Price against whichever fiat-stable quote this venue lists (Kraken
            # and Coinbase price many assets in USD/USDC, not USDT).
            sym, price = "", 0.0
            for q in (QUOTE_CURRENCY, "USD", "USDC"):
                sym = f"{asset}/{q}"
                price = self._last_price(sym)
                if price > 0:
                    break
            if price <= 0 or amt * price < 1.0:   # no market / dust
                continue
            entry = float(self._spot_entries.get(asset, 0.0))
            pnl = (price - entry) * amt if entry > 0 else 0.0
            out.append(Position(pair=sym, side="Long", size=amt,
                                entry=entry or price, current=price, pnl=pnl))
        return out

    def total_pnl(self, positions: List[Position]) -> float:
        return sum(p.pnl for p in positions)

    def check_key_safety(self):
        """Best-effort: warn if the API key has WITHDRAWAL permission enabled.

        Returns ``(warn: bool, message: str)``. Only a few exchanges expose key
        permissions via ccxt; for others we can't verify, so we don't warn.
        """
        if self.safe_mode or not self.client:
            return False, ""
        try:
            if self.exchange_id == "binance" and hasattr(self.client, "sapiGetAccountApiRestrictions"):
                r = self.client.sapiGetAccountApiRestrictions()
                if r.get("enableWithdrawals"):
                    return True, ("⚠ Your Binance API key has WITHDRAWAL permission "
                                  "enabled. Disable it now — the bot never needs it.")
        except Exception:  # noqa: BLE001 - non-fatal, best-effort
            pass
        return False, ""

    def top_pairs(self, limit: int = 20) -> List[str]:
        """The exchange's most-traded pairs (by 24h volume) for the current market
        type. Returns 'BASE/QUOTE' strings, BTC & ETH first. Best-effort.

        Quote is venue-aware: futures use this venue's margin currency (USDT-M by
        default, USD on Kraken, USDC on Coinbase); spot accepts the major fiat
        stable quotes (USDT/USDC/USD) so non-USDT venues still populate."""
        if not self.client:
            return []
        want_type = "swap" if self.is_futures else "spot"
        quotes = {self._fut_quote} if self.is_futures else {"USDT", "USDC", "USD"}
        return _rank_pairs(self.client, want_type, quotes, limit)

    # -- pricing ------------------------------------------------------------
    def _market_symbol(self, symbol: str) -> str:
        """Resolve to the symbol the exchange actually lists.

        Spot returns the plain ``BASE/QUOTE`` (e.g. ``BTC/USDT``). Futures prefers
        the linear-perpetual unified form ``BASE/QUOTE:QUOTE`` (e.g.
        ``BTC/USDT:USDT``), which is what ccxt expects for swap markets. Symbols
        that already carry a ``:settle`` suffix are passed through unchanged.
        """
        sym = normalize_symbol(symbol)
        if ":" in sym:                 # already a perp/settled symbol
            return sym
        if not self.is_futures:
            return sym                 # spot — plain BASE/QUOTE (unchanged)
        # Futures: build the venue's linear perp. Most venues are USDT-M, but
        # Kraken futures are USD-margined and Coinbase perps USDC-margined, so we
        # rebuild against this venue's futures quote rather than the spot quote.
        base, _, _ = sym.partition("/")
        fq = self._fut_quote
        perp = f"{base}/{fq}:{fq}"
        markets = getattr(self.client, "markets", None) if self.client else None
        if markets:
            if perp in markets:
                return perp
            if sym in markets:
                return sym
            # Adaptive: any linear perpetual on this base, whatever it settles in.
            for msym, m in markets.items():
                if (m.get("base") == base and m.get("swap")
                        and m.get("linear", True) and m.get("active", True)):
                    return msym
        return perp                    # best guess for a linear perpetual

    def _last_price(self, symbol: str) -> float:
        # Real price whenever a client exists (works in Safe Mode too).
        if not self.client:
            return 0.0
        try:
            return float(self.client.fetch_ticker(self._market_symbol(symbol))["last"])
        except Exception:  # noqa: BLE001
            return 0.0

    def fetch_ohlcv(self, symbol: str, timeframe: str = "5m", limit: int = 300) -> list:
        """Return recent OHLCV candles ``[[ts, o, h, l, c, v], ...]`` (public
        data; works in Safe Mode too). Empty list when there is no live client."""
        if not self.client:
            return []
        try:
            return self.client.fetch_ohlcv(self._market_symbol(symbol), timeframe, limit=limit)
        except Exception:  # noqa: BLE001 - candles are best-effort
            return []

    # -- leverage / margin --------------------------------------------------
    def apply_leverage_margin(self, symbol: str, leverage: int, margin_mode: str) -> str:
        """Best-effort set leverage and/or margin mode for ``symbol``.

        Returns a short note for the log. No-op in Safe Mode / without ccxt.
        Failures are swallowed (many spot markets / exchanges reject these).
        """
        if self.safe_mode or not CCXT_AVAILABLE or not self.client:
            return ""
        if not self.is_futures:
            return ""   # leverage/margin are futures-only
        sym = self._market_symbol(symbol)
        notes = []
        if margin_mode in ("cross", "isolated"):
            try:
                self.client.set_margin_mode(margin_mode, sym)
                notes.append(f"margin={margin_mode}")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"margin set failed ({exc})")
        if leverage and leverage > 0:
            # OKX / Bitget / KuCoin apply leverage per margin bucket, so the
            # margin mode must accompany the call or it lands in the wrong bucket.
            lev_params = {}
            if (margin_mode in ("cross", "isolated")
                    and self.exchange_id in ("okx", "bitget", "kucoin")):
                lev_params["marginMode"] = margin_mode
            try:
                self.client.set_leverage(leverage, sym, lev_params)
                notes.append(f"leverage={leverage}x")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"leverage set failed ({exc})")
        return "; ".join(notes)

    def _supports_trigger_orders(self) -> bool:
        """Whether this venue advertises reduce-only trigger (stop/take-profit)
        orders in ccxt. Used to avoid sending a 'market' order with a trigger
        param to a venue that would ignore it and fill immediately (closing the
        position). Unknown capability → attempt (preserve prior behaviour)."""
        has = getattr(self.client, "has", {}) or {}
        keys = ("createStopMarketOrder", "createStopOrder", "createTriggerOrder",
                "createStopLimitOrder", "createReduceOnlyOrder")
        flags = [has.get(k) for k in keys if k in has]
        return any(flags) if flags else True

    # -- order placement (the single choke point) ---------------------------
    def place_market_order(
        self, symbol: str, side: str, amount: float, reduce_only: bool = False
    ) -> OrderResult:
        """Convenience wrapper kept for callers that only need market orders."""
        return self.place_order(symbol, side, amount, "market", None, reduce_only)

    def place_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "market",
        price: float | None = None,
        reduce_only: bool = False,
    ) -> OrderResult:
        side = side.lower()
        order_type = (order_type or "market").lower()
        if side not in ("buy", "sell"):
            return OrderResult(False, f"Invalid side: {side}")
        if order_type == "limit" and not price:
            return OrderResult(False, "Limit order needs a price", pair=symbol, side=side)

        if self.read_only:
            return OrderResult(
                False, "Read-only mode is ON — order blocked", pair=symbol, side=side
            )
        if not self.connected:
            return OrderResult(False, "Not connected", pair=symbol, side=side)

        sym = self._market_symbol(symbol)
        at = f" @ {price}" if order_type == "limit" else ""

        # Safe Mode / no-ccxt => simulate the fill and update sim state.
        if self.safe_mode or not CCXT_AVAILABLE:
            self._simulate_fill(sym, side, amount)
            return OrderResult(
                True,
                f"SIMULATED {order_type.upper()} {side.upper()} {amount} {sym}{at}",
                pair=sym, side=side, order_type=order_type.capitalize(), simulated=True,
            )

        try:
            # reduceOnly is a futures concept; spot rejects it.
            params = {"reduceOnly": True} if (reduce_only and self.is_futures) else {}
            # Market orders: pass the current price so the exchange can compute the
            # quote (USDT) cost. Bitget/Binance spot MARKET BUY require this
            # ("requires the price argument for market buy orders"); for limit
            # orders we use the given limit price. Market sells ignore it.
            px = price if order_type == "limit" else self._last_price(sym)
            order = self.client.create_order(sym, order_type, side, amount, px, params)
            verb = "placed" if order_type == "limit" else "filled"
            # On spot, remember our entry price so positions show real PnL.
            if not self.is_futures and side == "buy":
                self._spot_entries[sym.split("/")[0]] = price or self._last_price(sym)
            return OrderResult(
                True,
                f"{order_type.upper()} {side.upper()} {amount} {sym}{at} {verb}",
                pair=sym, side=side, order_type=order_type.capitalize(), raw=order,
            )
        except Exception as exc:  # noqa: BLE001
            return OrderResult(False, f"Order rejected: {exc}", pair=sym, side=side)

    def place_reduce_order(
        self, symbol: str, side: str, amount: float, trigger_price: float, kind: str
    ) -> OrderResult:
        """Place a reduce-only protective order. ``kind`` is 'sl' or 'tp'.

        Uses ccxt's unified trigger params (``stopLossPrice`` /
        ``takeProfitPrice``), supported by the five target exchanges in recent
        ccxt. Exchange-specific quirks are caught and surfaced in the log.
        """
        if self.read_only:
            return OrderResult(False, "Read-only — protective order blocked", pair=symbol)

        sym = self._market_symbol(symbol)
        label = "SL" if kind == "sl" else "TP"

        if self.safe_mode or not CCXT_AVAILABLE or not self.client:
            return OrderResult(
                True, f"SIMULATED {label} {side} {amount} {sym} @ {trigger_price}",
                pair=sym, side=side, order_type=label, simulated=True,
            )
        # Spot: no reduce-only / trigger orders. TP = a plain limit sell; a hard
        # SL on spot would need a stop-limit (skipped — the strategy has no SL).
        if not self.is_futures:
            if kind == "sl":
                return OrderResult(False, "SL not placed on spot (spot has no stop market)",
                                   pair=sym, side=side, order_type=label)
            try:
                order = self.client.create_order(sym, "limit", side, amount, trigger_price)
                return OrderResult(True, f"TP limit {side} {amount} {sym} @ {trigger_price}",
                                   pair=sym, side=side, order_type=label, raw=order)
            except Exception as exc:  # noqa: BLE001
                return OrderResult(False, f"TP failed: {exc}", pair=sym, side=side, order_type=label)
        # Guard: if the venue doesn't support reduce-only trigger orders, do NOT
        # send a market order with a trigger param — it could fill immediately and
        # close the position. Warn instead so the user manages the stop manually.
        if not self._supports_trigger_orders():
            return OrderResult(
                False,
                f"{label} not auto-placed — {self.exchange_id} doesn't expose "
                f"reduce-only trigger orders here; set the stop on the exchange.",
                pair=sym, side=side, order_type=label)
        try:
            params = {"reduceOnly": True}
            params["stopLossPrice" if kind == "sl" else "takeProfitPrice"] = trigger_price
            order = self.client.create_order(sym, "market", side, amount, None, params)
            return OrderResult(
                True, f"{label} set {amount} {sym} @ {trigger_price}",
                pair=sym, side=side, order_type=label, raw=order,
            )
        except Exception as exc:  # noqa: BLE001
            return OrderResult(False, f"{label} failed: {exc}", pair=sym, side=side, order_type=label)

    def close_position(self, position: Position, fraction: float = 1.0) -> OrderResult:
        """Flatten a position (or ``fraction`` of it) with a reduce-only market
        order on the exit side. ``fraction`` in (0, 1] -> partial close."""
        side = exit_side("buy" if position.side == "Long" else "sell")
        frac = 1.0 if fraction >= 1.0 or fraction <= 0 else fraction
        amount = round(position.size * frac, 8)
        return self.place_market_order(position.pair, side, amount, reduce_only=True)

    def cancel_order(self, order_id: str, symbol: str) -> OrderResult:
        """Cancel a single open order (used to move a stop). Best-effort."""
        sym = self._market_symbol(symbol)
        if self.safe_mode or not CCXT_AVAILABLE or not self.client or not order_id:
            return OrderResult(True, f"SIMULATED cancel {order_id} {sym}", pair=sym, simulated=True)
        try:
            self.client.cancel_order(order_id, sym)
            return OrderResult(True, f"Cancelled order {order_id} {sym}", pair=sym)
        except Exception as exc:  # noqa: BLE001
            return OrderResult(False, f"Cancel failed: {exc}", pair=sym)

    # -- simulation helpers -------------------------------------------------
    def _simulate_fill(self, symbol: str, side: str, amount: float) -> None:
        price = self._last_price(symbol) or 100.0
        existing = next((p for p in self._sim_positions if p.pair == symbol), None)
        signed = amount if side == "buy" else -amount
        if existing:
            new_size = existing.size + (signed if existing.side == "Long" else -signed)
            if abs(new_size) < 1e-9:
                self._sim_positions.remove(existing)
            else:
                existing.size = abs(new_size)
                existing.side = "Long" if new_size > 0 else "Short"
        else:
            self._sim_positions.append(
                Position(
                    pair=symbol,
                    side="Long" if side == "buy" else "Short",
                    size=amount,
                    entry=price,
                    current=price,
                    pnl=0.0,
                )
            )
        cost = amount * price * 0.001  # pretend a tiny fee
        self._sim_balance -= cost

    def reset_paper(self, start_balance: float = 10_000.0) -> None:
        """Reset the simulated (paper) wallet: restore balance, clear demo positions."""
        self._sim_balance = float(start_balance)
        self._sim_positions = []
        self._spot_entries = {}
