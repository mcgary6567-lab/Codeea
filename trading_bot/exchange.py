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


class ExchangeManager:
    """Stateful wrapper around a single ccxt exchange client."""

    def __init__(self) -> None:
        self.client = None
        self.exchange_id: Optional[str] = None
        self.connected: bool = False
        self.read_only: bool = False
        self.safe_mode: bool = False
        # Simulated state used when safe_mode is on or ccxt is missing.
        self._sim_balance: float = 10_000.0
        self._sim_positions: List[Position] = []

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
    ) -> None:
        self.exchange_id = exchange_id
        self.read_only = read_only
        self.safe_mode = safe_mode

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
            "options": {"defaultType": "swap"},  # prefer futures for positions
        }
        # OKX / KuCoin / Bitget require an API passphrase.
        if password:
            params["password"] = password

        self.client = klass(params)
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
        """Return total quote-currency balance (USDT)."""
        if not self.connected:
            return 0.0
        if self.safe_mode or not CCXT_AVAILABLE:
            return self._sim_balance
        try:
            bal = self.client.fetch_balance()
            total = bal.get("total", {})
            return float(total.get(QUOTE_CURRENCY, 0.0) or 0.0)
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"balance: {exc}") from exc

    def fetch_positions(self) -> List[Position]:
        if not self.connected:
            return []
        if self.safe_mode or not CCXT_AVAILABLE:
            return list(self._sim_positions)
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

    def total_pnl(self, positions: List[Position]) -> float:
        return sum(p.pnl for p in positions)

    # -- sizing -------------------------------------------------------------
    def compute_amount(
        self,
        symbol: str,
        fixed_size: float,
        risk_based: bool,
        risk_percent: float,
        balance: float,
    ) -> float:
        """Return the base-asset quantity for an order.

        Fixed: use ``fixed_size`` directly.
        Risk-based: risk ``risk_percent`` % of balance, converted to base units
        at the current price.
        """
        if not risk_based:
            return fixed_size
        price = self._last_price(symbol)
        if price <= 0:
            return fixed_size
        quote_to_spend = balance * (risk_percent / 100.0)
        return round(quote_to_spend / price, 8)

    def _last_price(self, symbol: str) -> float:
        if self.safe_mode or not CCXT_AVAILABLE or not self.client:
            return 0.0
        try:
            return float(self.client.fetch_ticker(symbol)["last"])
        except Exception:  # noqa: BLE001
            return 0.0

    # -- order placement (the single choke point) ---------------------------
    def place_market_order(
        self, symbol: str, side: str, amount: float, reduce_only: bool = False
    ) -> OrderResult:
        side = side.lower()
        if side not in ("buy", "sell"):
            return OrderResult(False, f"Invalid side: {side}")

        if self.read_only:
            return OrderResult(
                False, "Read-only mode is ON — order blocked", pair=symbol, side=side
            )
        if not self.connected:
            return OrderResult(False, "Not connected", pair=symbol, side=side)

        sym = normalize_symbol(symbol)

        # Safe Mode / no-ccxt => simulate the fill and update sim state.
        if self.safe_mode or not CCXT_AVAILABLE:
            self._simulate_fill(sym, side, amount)
            return OrderResult(
                True,
                f"SIMULATED {side.upper()} {amount} {sym}",
                pair=sym,
                side=side,
                simulated=True,
            )

        try:
            params = {"reduceOnly": True} if reduce_only else {}
            order = self.client.create_order(sym, "market", side, amount, None, params)
            return OrderResult(
                True,
                f"{side.upper()} {amount} {sym} filled",
                pair=sym,
                side=side,
                raw=order,
            )
        except Exception as exc:  # noqa: BLE001
            return OrderResult(False, f"Order rejected: {exc}", pair=sym, side=side)

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
