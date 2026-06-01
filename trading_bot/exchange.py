"""Exchange connectivity via ccxt.

Wraps the five supported exchanges behind one interface so the GUI doesn't care
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

from config import QUOTE_CURRENCY


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

    * ``fixed``        – the fixed lot, unchanged.
    * ``risk_balance`` – spend ``risk_percent`` % of balance at ``price``.
    * ``risk_stop``    – risk ``risk_percent`` % of balance across the
      ``entry``→``stop`` distance: ``amount = risk_amount / |entry - stop|``.
      This is the proper per-trade risk model and pairs with the indicator's SL.

    Any mode falls back to the fixed lot when its required inputs are missing,
    so a trade is never sized to zero by accident.
    """
    risk_amount = balance * (risk_percent / 100.0)
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

        if not CCXT_AVAILABLE:
            # Demo/simulation mode: pretend we connected.
            self.connected = True
            return

        if exchange_id not in ccxt.exchanges:
            raise ExchangeError(f"Unknown exchange: {exchange_id}")

        klass = getattr(ccxt, exchange_id)
        params = {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap" if self.is_futures else "spot",
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
            raise ExchangeError(str(exc)) from exc

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
            sym = f"{asset}/{QUOTE_CURRENCY}"
            price = self._last_price(sym)
            if price <= 0 or amt * price < 1.0:   # no market / dust
                continue
            entry = float(self._spot_entries.get(asset, 0.0))
            pnl = (price - entry) * amt if entry > 0 else 0.0
            out.append(Position(pair=sym, side="Long", size=amt,
                                entry=entry or price, current=price, pnl=pnl))
        return out

    def total_pnl(self, positions: List[Position]) -> float:
        return sum(p.pnl for p in positions)

    # -- pricing ------------------------------------------------------------
    def _last_price(self, symbol: str) -> float:
        # Real price whenever a client exists (works in Safe Mode too).
        if not self.client:
            return 0.0
        try:
            return float(self.client.fetch_ticker(normalize_symbol(symbol))["last"])
        except Exception:  # noqa: BLE001
            return 0.0

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
        sym = normalize_symbol(symbol)
        notes = []
        if margin_mode in ("cross", "isolated"):
            try:
                self.client.set_margin_mode(margin_mode, sym)
                notes.append(f"margin={margin_mode}")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"margin set failed ({exc})")
        if leverage and leverage > 0:
            try:
                self.client.set_leverage(leverage, sym)
                notes.append(f"leverage={leverage}x")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"leverage set failed ({exc})")
        return "; ".join(notes)

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

        sym = normalize_symbol(symbol)
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
            order = self.client.create_order(sym, order_type, side, amount, price, params)
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

        sym = normalize_symbol(symbol)
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

    def close_position(self, position: Position) -> OrderResult:
        """Flatten a position with a reduce-only market order on the exit side."""
        side = exit_side("buy" if position.side == "Long" else "sell")
        return self.place_market_order(position.pair, side, position.size, reduce_only=True)

    def cancel_order(self, order_id: str, symbol: str) -> OrderResult:
        """Cancel a single open order (used to move a stop). Best-effort."""
        sym = normalize_symbol(symbol)
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
