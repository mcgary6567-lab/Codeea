"""Unit tests for the bot's pure money-math, guardrails and signal parsing.

These cover the dangerous, exchange-independent logic — sizing, take-profit
splits, PnL sign, the guardrail entry gate (incl. the dedupe ordering that
once blocked every trade), webhook/relay payload parsing, and the daily
summary numbers. No GUI, no network, no exchange, no extra dependencies.

Run from the project root:

    python -m unittest discover -s trading_bot/tests
    # or from inside trading_bot/:  python -m unittest
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

# Make the flat trading_bot modules importable regardless of where pytest/
# unittest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchange import (  # noqa: E402
    Position,
    exit_side,
    normalize_symbol,
    plan_take_profits,
    recompute_pnl,
    size_order,
)
from guardrails import Guardrails  # noqa: E402
from webhook_server import parse_payload  # noqa: E402


class TestNormalizeSymbol(unittest.TestCase):
    def test_variants_normalize_to_ccxt(self):
        self.assertEqual(normalize_symbol("BTCUSDT"), "BTC/USDT")
        self.assertEqual(normalize_symbol("btc/usdt"), "BTC/USDT")
        self.assertEqual(normalize_symbol("ETH-USDC"), "ETH/USDC")
        self.assertEqual(normalize_symbol("sol_usdt"), "SOL/USDT")

    def test_bare_base_gets_default_quote(self):
        self.assertEqual(normalize_symbol("BTC"), "BTC/USDT")

    def test_already_separated_passthrough(self):
        self.assertEqual(normalize_symbol(" ada/usdt "), "ADA/USDT")


class TestFriendlyError(unittest.TestCase):
    def test_binance_451_points_to_binance_us(self):
        from exchange import _friendly_error
        exc = Exception("binance 451 Service unavailable from a restricted location "
                        "according to 'b. Eligibility'")
        msg = _friendly_error("binance", exc)
        self.assertIn("Binance.US", msg)

    def test_other_exchange_451_generic(self):
        from exchange import _friendly_error
        msg = _friendly_error("kucoin", Exception("HTTP 451 restricted location"))
        self.assertIn("451", msg)
        self.assertNotIn("Binance.US", msg)

    def test_2015_explains_permissions(self):
        from exchange import _friendly_error
        msg = _friendly_error("binance", Exception("binance -2015 Invalid API-key, IP, or permissions"))
        self.assertIn("whitelist", msg.lower())

    def test_unknown_error_passthrough(self):
        from exchange import _friendly_error
        msg = _friendly_error("bybit", Exception("some random failure"))
        self.assertEqual(msg, "some random failure")


class TestSizeOrder(unittest.TestCase):
    def test_fixed_mode_returns_fixed_lot(self):
        amt, _ = size_order("fixed", 0.05, 1.0, 1000, 100)
        self.assertEqual(amt, 0.05)

    def test_risk_balance(self):
        # 1% of 1000 = 10 spent at price 100 -> 0.1
        amt, _ = size_order("risk_balance", 0.05, 1.0, 1000, 100)
        self.assertAlmostEqual(amt, 0.1)

    def test_risk_balance_falls_back_when_no_price(self):
        amt, reason = size_order("risk_balance", 0.05, 1.0, 1000, 0)
        self.assertEqual(amt, 0.05)
        self.assertIn("fixed", reason)

    def test_risk_stop(self):
        # risk 10 over a 5-wide stop (100 -> 95) = 2.0 units
        amt, _ = size_order("risk_stop", 0.05, 1.0, 1000, 100, entry=100, stop=95)
        self.assertAlmostEqual(amt, 2.0)

    def test_risk_stop_falls_back_without_stop(self):
        amt, reason = size_order("risk_stop", 0.05, 1.0, 1000, 100, entry=100, stop=0)
        self.assertEqual(amt, 0.05)
        self.assertIn("fixed", reason)

    def test_never_divides_by_zero(self):
        # zero balance / price must not raise and must not size to zero
        amt, _ = size_order("risk_balance", 0.05, 1.0, 0, 0)
        self.assertEqual(amt, 0.05)


class TestPlanTakeProfits(unittest.TestCase):
    def test_both_tps_split_and_sum_to_amount(self):
        legs = plan_take_profits(1.0, 110, 120, 0.5)
        self.assertEqual(len(legs), 2)
        self.assertEqual(legs[0], (110, 0.5))
        self.assertEqual(legs[1], (120, 0.5))
        self.assertAlmostEqual(sum(q for _, q in legs), 1.0)

    def test_uneven_fraction_still_sums_to_amount(self):
        legs = plan_take_profits(1.0, 110, 120, 0.3)
        self.assertAlmostEqual(sum(q for _, q in legs), 1.0)
        self.assertAlmostEqual(legs[0][1], 0.3)
        self.assertAlmostEqual(legs[1][1], 0.7)

    def test_single_tp_takes_full_amount(self):
        self.assertEqual(plan_take_profits(2.0, 110, 0, 0.5), [(110, 2.0)])
        self.assertEqual(plan_take_profits(2.0, 0, 120, 0.5), [(120, 2.0)])

    def test_no_tp_returns_empty(self):
        self.assertEqual(plan_take_profits(2.0, 0, 0, 0.5), [])


class TestPnlAndExitSide(unittest.TestCase):
    def test_long_pnl_positive_when_price_rises(self):
        p = Position("BTC/USDT", "Long", 2.0, 100.0, 100.0, 0.0)
        out = recompute_pnl([p], {"BTC/USDT": 110.0})[0]
        self.assertAlmostEqual(out.pnl, 20.0)
        self.assertEqual(out.current, 110.0)

    def test_short_pnl_positive_when_price_falls(self):
        p = Position("BTC/USDT", "Short", 2.0, 100.0, 100.0, 0.0)
        out = recompute_pnl([p], {"BTC/USDT": 90.0})[0]
        self.assertAlmostEqual(out.pnl, 20.0)

    def test_missing_price_keeps_current(self):
        p = Position("ETH/USDT", "Long", 1.0, 50.0, 55.0, 0.0)
        out = recompute_pnl([p], {})[0]
        self.assertEqual(out.current, 55.0)

    def test_exit_side_is_opposite(self):
        self.assertEqual(exit_side("buy"), "sell")
        self.assertEqual(exit_side("SELL"), "buy")


class TestGuardrails(unittest.TestCase):
    def _gate(self, g, symbol, side, open_pairs, now):
        """Mirror the backend's entry gate: CHECK then RECORD."""
        allowed, reason = g.check_entry(symbol, side, open_pairs, now=now)
        g.record_signal(symbol, side, now=now)
        if allowed:
            g.record_entry(symbol, now=now)
        return allowed, reason

    def test_dedupe_blocks_only_the_duplicate(self):
        # Regression: with record-before-check, the FIRST signal was wrongly
        # blocked as a duplicate of itself. Correct order allows it.
        g = Guardrails()
        g.configure(max_open=0, daily_loss=0, cooldown=0, dedupe=5)
        self.assertTrue(self._gate(g, "BTC/USDT", "buy", set(), now=0.0)[0])
        self.assertFalse(self._gate(g, "BTC/USDT", "buy", set(), now=1.0)[0])
        self.assertTrue(self._gate(g, "BTC/USDT", "buy", set(), now=7.0)[0])

    def test_cooldown_blocks_same_symbol_within_window(self):
        g = Guardrails()
        g.configure(max_open=0, daily_loss=0, cooldown=10, dedupe=0)
        self.assertTrue(self._gate(g, "BTC/USDT", "buy", set(), now=0.0)[0])
        self.assertFalse(self._gate(g, "BTC/USDT", "buy", set(), now=5.0)[0])
        self.assertTrue(self._gate(g, "BTC/USDT", "buy", set(), now=11.0)[0])

    def test_max_open_blocks_new_symbol_but_allows_existing(self):
        g = Guardrails()
        g.configure(max_open=2, daily_loss=0, cooldown=0, dedupe=0)
        held = {"BTC/USDT", "ETH/USDT"}
        # new symbol blocked at cap
        self.assertFalse(g.check_entry("SOL/USDT", "buy", held)[0])
        # already-open symbol still allowed (adding/closing it)
        self.assertTrue(g.check_entry("BTC/USDT", "buy", held)[0])

    def test_daily_loss_limit_trips_and_halts(self):
        g = Guardrails()
        g.configure(max_open=0, daily_loss=50, cooldown=0, dedupe=0)
        self.assertTrue(g.check_entry("BTC/USDT", "buy", set())[0])
        tripped = g.record_realized(-60.0)
        self.assertTrue(tripped)
        self.assertEqual(g.trip_reason, "loss")
        allowed, reason = g.check_entry("BTC/USDT", "buy", set())
        self.assertFalse(allowed)
        self.assertIn("loss limit", reason)

    def test_daily_profit_target_trips(self):
        g = Guardrails()
        g.configure(max_open=0, daily_loss=0, cooldown=0, dedupe=0, daily_profit=100)
        self.assertTrue(g.record_realized(120.0))
        self.assertEqual(g.trip_reason, "profit")
        self.assertFalse(g.check_entry("BTC/USDT", "buy", set())[0])

    def test_reset_clears_trip(self):
        g = Guardrails()
        g.configure(max_open=0, daily_loss=50, cooldown=0, dedupe=0)
        g.record_realized(-60.0)
        g.reset_daily()
        self.assertTrue(g.check_entry("BTC/USDT", "buy", set())[0])


class TestParsePayload(unittest.TestCase):
    def test_entry_buy(self):
        sig = parse_payload({"action": "buy", "ticker": "BTCUSDT",
                             "tp1": "70000", "sl": "68000"})
        self.assertIsNotNone(sig)
        self.assertEqual(sig["action"], "buy")
        self.assertEqual(sig["ticker"], "BTCUSDT")
        self.assertEqual(sig["tp1"], 70000.0)
        self.assertEqual(sig["sl"], 68000.0)

    def test_side_alias_and_symbol_alias(self):
        sig = parse_payload({"side": "sell", "symbol": "ETHUSDT"})
        self.assertEqual(sig["action"], "sell")
        self.assertEqual(sig["ticker"], "ETHUSDT")

    def test_lifecycle_event(self):
        sig = parse_payload({"event": "tp1_hit", "ticker": "ETHUSDT", "entry": "3000"})
        self.assertEqual(sig["event"], "tp1_hit")
        self.assertEqual(sig["entry"], 3000.0)

    def test_invalid_returns_none(self):
        self.assertIsNone(parse_payload({"foo": "bar"}))
        self.assertIsNone(parse_payload({"action": "buy"}))          # no ticker
        self.assertIsNone(parse_payload({"ticker": "BTCUSDT"}))      # no action/event
        self.assertIsNone(parse_payload("not a dict"))

    def test_source_is_tagged(self):
        sig = parse_payload({"action": "buy", "ticker": "BTCUSDT"}, source="relay")
        self.assertEqual(sig["source"], "relay")


class TestDailySummary(unittest.TestCase):
    """summary_for_day reads SQLite; point history at a throwaway DB."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        import history
        self.history = history
        self._orig_db = history.HISTORY_DB
        history.HISTORY_DB = self._tmp.name
        history.init_db()

    def tearDown(self):
        self.history.HISTORY_DB = self._orig_db
        os.unlink(self._tmp.name)

    def test_counts_and_realized_pnl(self):
        h = self.history
        h.record_trade("manual", "BTC/USDT", "buy", "entry", 0.1, 100, "Filled")
        h.record_trade("system", "BTC/USDT", "sell", "close", 0.1, 110, "Closed", pnl=5.0)
        h.record_trade("system", "ETH/USDT", "sell", "close", 1.0, 90, "Closed", pnl=-2.0)
        day = time.strftime("%Y-%m-%d", time.localtime())
        s = h.summary_for_day(day)
        self.assertEqual(s["entries"], 1)
        self.assertEqual(s["closed"], 2)
        self.assertEqual(s["wins"], 1)
        self.assertEqual(s["losses"], 1)
        self.assertAlmostEqual(s["realized"], 3.0)
        self.assertAlmostEqual(s["best"], 5.0)
        self.assertAlmostEqual(s["worst"], -2.0)

    def test_clear_all_empties_history(self):
        h = self.history
        h.record_trade("manual", "BTC/USDT", "buy", "entry", 0.1, 100, "Filled")
        h.record_equity(1000.0, 0.0)
        self.assertGreater(h.stats()["total"], 0)
        h.clear_all()
        self.assertEqual(h.stats()["total"], 0)
        self.assertEqual(h.fetch_equity(), [])

    def test_stats_respects_time_range(self):
        import sqlite3
        h = self.history
        # Insert two closes at known timestamps (old vs now).
        old, recent = 1000.0, time.time()
        with sqlite3.connect(h.HISTORY_DB) as c:
            c.execute("INSERT INTO trades (ts,source,symbol,side,kind,size,price,status,pnl,message)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (old, "s", "BTC/USDT", "sell", "close", 1, 1, "Closed", 3.0, ""))
            c.execute("INSERT INTO trades (ts,source,symbol,side,kind,size,price,status,pnl,message)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (recent, "s", "BTC/USDT", "sell", "close", 1, 1, "Closed", 7.0, ""))
        self.assertEqual(h.stats()["closed"], 2)                      # all
        self.assertEqual(h.stats(since=recent - 60)["closed"], 1)     # only recent
        self.assertAlmostEqual(h.stats(since=recent - 60)["realized_pnl"], 7.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
