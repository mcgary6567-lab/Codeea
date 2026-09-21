"""Exchange execution layer.

Turns a validated Dip2Green signal into real (or simulated) orders using CCXT,
so the same code works across Binance, Bybit, OKX, Kraken, KuCoin, Gate.io,
Bitget, MEXC, etc. Sizing is derived from the account balance and the
entry->stop distance, never hard-coded.
"""
import logging

import ccxt

from config import Config

log = logging.getLogger("bridge.broker")


class Broker:
    def __init__(self) -> None:
        self.dry_run = Config.DRY_RUN
        self.exchange = self._build_exchange()

    def _build_exchange(self):
        if self.dry_run and not (Config.API_KEY and Config.API_SECRET):
            # In dry-run with no keys we still want a client for market metadata.
            log.info("DRY_RUN with no API keys: using public client for sizing.")
        klass = getattr(ccxt, Config.EXCHANGE, None)
        if klass is None:
            raise SystemExit(f"Unknown CCXT exchange id: {Config.EXCHANGE!r}")
        ex = klass({
            "apiKey": Config.API_KEY,
            "secret": Config.API_SECRET,
            "password": Config.API_PASSWORD or None,
            "enableRateLimit": True,
            "options": {"defaultType": Config.MARKET_TYPE},
        })
        if Config.TESTNET:
            # Most CCXT exchanges expose a sandbox; not all do.
            try:
                ex.set_sandbox_mode(True)
                log.info("Sandbox/testnet mode ENABLED for %s", Config.EXCHANGE)
            except Exception:  # noqa: BLE001
                log.warning("%s has no sandbox in CCXT; leaving live endpoint.", Config.EXCHANGE)
        ex.load_markets()
        return ex

    # -- helpers --------------------------------------------------------------
    def _quote_balance(self, market) -> float:
        """Free balance of the quote currency (e.g. USDT for BTC/USDT)."""
        if self.dry_run and not Config.API_KEY:
            return 1000.0  # assume a nominal balance so sizing logs make sense
        bal = self.exchange.fetch_balance()
        quote = market["quote"]
        return float(bal.get("free", {}).get(quote, 0) or 0)

    def _position_size(self, market, entry: float, stop: float) -> float:
        """Risk RISK_PCT of quote balance over the entry->stop distance."""
        risk_per_unit = abs(entry - stop)
        if risk_per_unit <= 0:
            raise ValueError("entry and stop are equal; cannot size position")
        equity = self._quote_balance(market)
        risk_amount = equity * Config.RISK_PCT
        amount = risk_amount / risk_per_unit  # units of base currency
        notional = amount * entry
        if Config.MAX_NOTIONAL > 0 and notional > Config.MAX_NOTIONAL:
            amount = Config.MAX_NOTIONAL / entry
        return float(self.exchange.amount_to_precision(market["symbol"], amount))

    # -- main entry point -----------------------------------------------------
    def execute(self, sig: dict) -> dict:
        """sig keys: action(BUY/SELL), symbol, entry, sl, tp1, tp2."""
        symbol = self._normalize_symbol(sig["symbol"])
        market = self.exchange.market(symbol)
        side = "buy" if sig["action"] == "BUY" else "sell"
        exit_side = "sell" if side == "buy" else "buy"

        entry, sl, tp1, tp2 = (float(sig[k]) for k in ("entry", "sl", "tp1", "tp2"))
        amount = self._position_size(market, entry, sl)
        amt_tp1 = float(self.exchange.amount_to_precision(symbol, amount * Config.TP1_FRACTION))
        amt_tp2 = float(self.exchange.amount_to_precision(symbol, amount - amt_tp1))

        plan = {
            "symbol": symbol, "side": side, "amount": amount,
            "entry_type": Config.ENTRY_TYPE, "entry": entry,
            "sl": sl, "tp1": tp1, "tp2": tp2,
            "tp1_amount": amt_tp1, "tp2_amount": amt_tp2,
            "dry_run": self.dry_run,
        }
        log.info("PLAN %s", plan)

        if self.dry_run:
            return {"status": "dry_run", "plan": plan}

        orders = []
        # 1) Entry
        if Config.ENTRY_TYPE == "limit":
            orders.append(self.exchange.create_order(symbol, "limit", side, amount, entry))
        else:
            orders.append(self.exchange.create_order(symbol, "market", side, amount))

        reduce = {"reduceOnly": True}
        # 2) Stop-loss (full size) as a stop-market via unified triggerPrice.
        orders.append(self.exchange.create_order(
            symbol, "market", exit_side, amount, None,
            {**reduce, "triggerPrice": sl, "stopLossPrice": sl},
        ))
        # 3) Take-profits as reduce-only limits.
        if amt_tp1 > 0:
            orders.append(self.exchange.create_order(symbol, "limit", exit_side, amt_tp1, tp1, reduce))
        if amt_tp2 > 0:
            orders.append(self.exchange.create_order(symbol, "limit", exit_side, amt_tp2, tp2, reduce))

        ids = [o.get("id") for o in orders]
        log.info("LIVE orders placed: %s", ids)
        return {"status": "live", "order_ids": ids, "plan": plan}

    def _normalize_symbol(self, raw: str) -> str:
        """TradingView sends e.g. BTCUSDT; CCXT wants BTC/USDT."""
        raw = raw.upper().replace("-", "").replace("/", "")
        if raw in self.exchange.markets:  # already CCXT format somehow
            return raw
        # Try to match against loaded markets by stripping the '/'.
        for sym, m in self.exchange.markets.items():
            if (m["base"] + m["quote"]) == raw:
                return sym
        raise ValueError(f"Cannot map symbol {raw!r} to a market on {Config.EXCHANGE}")
