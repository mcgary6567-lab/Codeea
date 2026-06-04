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

import contextlib
import os
import sys
import tempfile
import time
import unittest

# Make the flat trading_bot modules importable regardless of where pytest/
# unittest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchange import (  # noqa: E402
    ExchangeManager,
    Position,
    exit_side,
    normalize_symbol,
    plan_take_profits,
    recompute_pnl,
    size_order,
)
from guardrails import Guardrails  # noqa: E402
from webhook_server import parse_payload  # noqa: E402
from strategy import (  # noqa: E402
    StrategyParams,
    StrategySignal,
    atr_series,
    evaluate,
    evaluate_all,
    lowest_series,
    rsi_series,
    sma_series,
    track_outcomes,
)


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

    def test_fixed_quote_usdt(self):
        # $420 of BTC at price 70000 -> 0.006 BTC
        amt, _ = size_order("fixed_quote", 420, 1.0, 1000, 70000)
        self.assertAlmostEqual(amt, 0.006)
        # $100 at 100 -> 1.0 unit
        amt, _ = size_order("fixed_quote", 100, 1.0, 1000, 100)
        self.assertAlmostEqual(amt, 1.0)

    def test_fixed_quote_falls_back_without_price(self):
        amt, reason = size_order("fixed_quote", 100, 1.0, 1000, 0)
        self.assertEqual(amt, 100)
        self.assertIn("no price", reason)

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


class TestPartialClose(unittest.TestCase):
    def _sim_long(self, size=1.0):
        ex = ExchangeManager()
        ex.safe_mode = True
        ex.connected = True
        ex._sim_positions = [Position(pair="BTC/USDT", side="Long", size=size,
                                      entry=100.0, current=100.0, pnl=0.0)]
        return ex

    def test_partial_close_reduces_size(self):
        ex = self._sim_long(1.0)
        r = ex.close_position(ex._sim_positions[0], fraction=0.25)
        self.assertTrue(r.ok)
        # 25% of 1.0 sold -> 0.75 remains, still Long
        self.assertEqual(len(ex._sim_positions), 1)
        self.assertAlmostEqual(ex._sim_positions[0].size, 0.75)
        self.assertEqual(ex._sim_positions[0].side, "Long")

    def test_full_close_flattens(self):
        ex = self._sim_long(1.0)
        ex.close_position(ex._sim_positions[0], fraction=1.0)
        self.assertEqual(ex._sim_positions, [])

    def test_fraction_out_of_range_treated_as_full(self):
        ex = self._sim_long(2.0)
        ex.close_position(ex._sim_positions[0], fraction=0)
        self.assertEqual(ex._sim_positions, [])


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

    def test_loss_streak_cooldown(self):
        g = Guardrails()
        g.configure(max_open=0, daily_loss=0, cooldown=0, dedupe=0,
                    loss_streak=3, streak_cooldown=60)
        for _ in range(2):
            g.record_realized(-1.0, now=0.0)
        self.assertTrue(g.check_entry("BTC/USDT", "buy", set(), now=1.0)[0])  # 2 losses, ok
        g.record_realized(-1.0, now=0.0)                                      # 3rd -> pause
        self.assertFalse(g.check_entry("BTC/USDT", "buy", set(), now=10.0)[0])
        self.assertTrue(g.check_entry("BTC/USDT", "buy", set(), now=61.0)[0])  # after cooldown
        g.record_realized(2.0, now=62.0)                                      # a win resets streak

    def test_drawdown_halt(self):
        g = Guardrails()
        g.configure(max_open=0, daily_loss=0, cooldown=0, dedupe=0, max_drawdown=10.0)
        g.update_equity(1000.0, now=0.0)              # peak
        self.assertTrue(g.check_entry("BTC/USDT", "buy", set(), now=0.0)[0])
        g.update_equity(940.0, now=0.0)               # -6% -> ok
        self.assertTrue(g.check_entry("BTC/USDT", "buy", set(), now=0.0)[0])
        g.update_equity(890.0, now=0.0)               # -11% -> halt
        allowed, reason = g.check_entry("BTC/USDT", "buy", set(), now=0.0)
        self.assertFalse(allowed)
        self.assertIn("drawdown", reason.lower())

    def test_trading_hours_filter(self):
        import time as _t
        g = Guardrails()
        g.configure(max_open=0, daily_loss=0, cooldown=0, dedupe=0, start_hour=8, end_hour=20)
        # Build a timestamp at 03:00 and 12:00 local
        def at(hour):
            lt = _t.localtime()
            return _t.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, 0, 0, 0, 0, -1))
        self.assertFalse(g.check_entry("BTC/USDT", "buy", set(), now=at(3))[0])   # outside
        self.assertTrue(g.check_entry("BTC/USDT", "buy", set(), now=at(12))[0])   # inside

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

    def test_exit_event(self):
        # EMA-ribbon opposite-cross close (Strategy B, long-only mode)
        sig = parse_payload({"event": "exit", "symbol": "BTCUSDT", "strategy": "ema"})
        self.assertIsNotNone(sig)
        self.assertEqual(sig["event"], "exit")
        self.assertEqual(sig["ticker"], "BTCUSDT")

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

    def test_clear_respects_range(self):
        import sqlite3
        h = self.history
        recent = time.time()
        with sqlite3.connect(h.HISTORY_DB) as c:
            for ts, pnl in ((1000.0, 3.0), (recent, 7.0)):
                c.execute("INSERT INTO trades (ts,source,symbol,side,kind,size,price,status,pnl,message)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (ts, "s", "BTC/USDT", "sell", "close", 1, 1, "Closed", pnl, ""))
        self.assertEqual(h.stats()["closed"], 2)
        h.clear(since=recent - 60)            # delete only the recent one
        self.assertEqual(h.stats()["closed"], 1)
        self.assertAlmostEqual(h.stats()["realized_pnl"], 3.0)  # old one remains

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


class TestStrategyIndicators(unittest.TestCase):
    """The pure series helpers underpinning the built-in strategy engine."""

    def test_sma_warmup_then_average(self):
        out = sma_series([1, 2, 3, 4, 5], 3)
        self.assertIsNone(out[0])
        self.assertIsNone(out[1])
        self.assertAlmostEqual(out[2], 2.0)   # (1+2+3)/3
        self.assertAlmostEqual(out[3], 3.0)   # (2+3+4)/3
        self.assertAlmostEqual(out[4], 4.0)   # (3+4+5)/3

    def test_lowest_is_trailing_min_inclusive(self):
        out = lowest_series([5, 4, 6, 3, 7], 3)
        self.assertEqual(out[2], 4)           # min(5,4,6)
        self.assertEqual(out[3], 3)           # min(4,6,3)
        self.assertEqual(out[4], 3)           # min(6,3,7)

    def test_rsi_converges_high_on_uptrend_low_on_downtrend(self):
        up = rsi_series([float(x) for x in range(1, 60)], 14)
        self.assertGreater(up[-1], 99.0)      # only gains -> RSI 100
        down = rsi_series([float(60 - x) for x in range(1, 60)], 14)
        self.assertLess(down[-1], 1.0)        # only losses -> RSI 0

    def test_atr_warms_up_then_tracks_range(self):
        n = 30
        highs = [101.0] * n
        lows = [99.0] * n
        closes = [100.0] * n
        atr = atr_series(highs, lows, closes, 14)
        self.assertIsNone(atr[12])
        # constant 2-wide range -> ATR converges to 2.0
        self.assertAlmostEqual(atr[-1], 2.0, places=6)


def _candle(ts, o, h, l, c, v=100.0):
    return [ts, o, h, l, c, v]


def _build(warmup=40, tail=None):
    """A flat warmup (neutral candles, close==open) followed by ``tail`` candles.

    Neutral warmup keeps RSI/ATR well-defined without creating spurious dips or
    greens, so tests can drive the state machine deterministically."""
    candles = []
    for i in range(warmup):
        candles.append(_candle(i * 60000, 100.0, 100.5, 99.5, 100.0))
    base = warmup
    for j, c in enumerate(tail or []):
        c = list(c)
        c[0] = (base + j) * 60000
        candles.append(c)
    return candles


# Lenient custom thresholds so the *state machine* (not the RSI/ATR gating) is
# what's under test: any red new-low bar is a dip; any green passes.
LENIENT = dict(preset="Custom", cust_os=100, cust_atr=0.0, cust_vol=0.0,
               cust_look=10, min_body_ratio=0.3)


class TestStrategyEngine(unittest.TestCase):
    def test_dip_then_two_greens_fires_buy_on_last_candle(self):
        p = StrategyParams(green_n=2, max_wait=15, cooldown=5,
                           use_sl=True, sl_look=10, sl_buf=0.10,
                           rr_tp1=1.0, rr_tp2=3.0, **LENIENT)
        candles = _build(tail=[
            _candle(0, 100.0, 100.0, 97.0, 98.0),   # dip: red, new low 97
            _candle(0, 98.0, 99.0, 98.0, 99.0),     # green 1 (body=1, ratio 1.0)
            _candle(0, 99.0, 100.0, 99.0, 100.0),   # green 2 -> fires here
        ])
        sig = evaluate(candles, p, ticker="BTC/USDT")
        self.assertIsNotNone(sig)
        self.assertAlmostEqual(sig.entry, 100.0)        # close of signal bar
        # dipRef = locked dip low 97; tpRange = max(100-97, atr) = 3
        self.assertAlmostEqual(sig.tp1, 103.0)          # 100 + 3*1.0
        self.assertAlmostEqual(sig.tp2, 109.0)          # 100 + 3*3.0
        self.assertAlmostEqual(sig.sl, 97.0 * (1 - 0.10 / 100.0))

    def test_only_fires_on_the_newest_closed_candle(self):
        """A signal that completed on an earlier bar is not re-emitted."""
        p = StrategyParams(green_n=2, cooldown=5, **LENIENT)
        candles = _build(tail=[
            _candle(0, 100.0, 100.0, 97.0, 98.0),
            _candle(0, 98.0, 99.0, 98.0, 99.0),
            _candle(0, 99.0, 100.0, 99.0, 100.0),   # signal bar...
            _candle(0, 100.0, 100.5, 99.5, 100.0),  # ...followed by neutral bars
            _candle(0, 100.0, 100.5, 99.5, 100.0),
        ])
        self.assertIsNone(evaluate(candles, p, ticker="BTC/USDT"))

    def test_insufficient_greens_does_not_fire(self):
        p = StrategyParams(green_n=3, **LENIENT)   # needs 3, only 2 supplied
        candles = _build(tail=[
            _candle(0, 100.0, 100.0, 97.0, 98.0),
            _candle(0, 98.0, 99.0, 98.0, 99.0),
            _candle(0, 99.0, 100.0, 99.0, 100.0),
        ])
        self.assertIsNone(evaluate(candles, p, ticker="BTC/USDT"))

    def test_weak_green_body_is_rejected(self):
        """A doji-ish green (tiny body vs range) must not count as a confirmation."""
        p = StrategyParams(green_n=2, require_body_ratio=True, **{
            **LENIENT, "min_body_ratio": 0.5})
        candles = _build(tail=[
            _candle(0, 100.0, 100.0, 97.0, 98.0),       # dip
            _candle(0, 98.0, 99.0, 98.0, 99.0),         # strong green (ratio 1.0)
            _candle(0, 99.0, 102.0, 98.0, 99.2),        # weak green: body .2 / range 4
        ])
        # The weak green resets the count, so no 2-green sequence completes.
        self.assertIsNone(evaluate(candles, p, ticker="BTC/USDT"))

    def test_setup_expires_after_max_wait(self):
        p = StrategyParams(green_n=2, max_wait=3, **LENIENT)
        tail = [_candle(0, 100.0, 100.0, 97.0, 98.0)]      # dip
        tail += [_candle(0, 100.0, 100.5, 99.5, 100.0)] * 4  # 4 neutral > max_wait 3
        tail += [_candle(0, 98.0, 99.0, 98.0, 99.0),
                 _candle(0, 99.0, 100.0, 99.0, 100.0)]       # greens come too late
        candles = _build(tail=tail)
        self.assertIsNone(evaluate(candles, p, ticker="BTC/USDT"))

    def test_no_sl_when_disabled(self):
        p = StrategyParams(green_n=2, use_sl=False, **LENIENT)
        candles = _build(tail=[
            _candle(0, 100.0, 100.0, 97.0, 98.0),
            _candle(0, 98.0, 99.0, 98.0, 99.0),
            _candle(0, 99.0, 100.0, 99.0, 100.0),
        ])
        sig = evaluate(candles, p, ticker="BTC/USDT")
        self.assertIsNotNone(sig)
        self.assertIsNone(sig.sl)

    def test_too_few_candles_returns_none(self):
        p = StrategyParams(**LENIENT)
        self.assertIsNone(evaluate(_build(warmup=5), p, ticker="BTC/USDT"))


class TestStrategyEvaluateAll(unittest.TestCase):
    """evaluate_all powers the chart overlays: it must mark every dip + signal."""

    def test_marks_dip_and_signal_with_index(self):
        p = StrategyParams(green_n=2, cooldown=0, **LENIENT)
        candles = _build(tail=[
            _candle(0, 100.0, 100.0, 97.0, 98.0),   # idx 40 — dip
            _candle(0, 98.0, 99.0, 98.0, 99.0),     # idx 41 — green
            _candle(0, 99.0, 100.0, 99.0, 100.0),   # idx 42 — signal
        ])
        dips, sigs = evaluate_all(candles, p, ticker="BTC/USDT")
        self.assertIn(40, dips)
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0].index, 42)
        self.assertAlmostEqual(sigs[0].entry, 100.0)
        self.assertAlmostEqual(sigs[0].tp2, 109.0)

    def test_marks_multiple_signals(self):
        p = StrategyParams(green_n=2, cooldown=0, **LENIENT)
        candles = _build(tail=[
            _candle(0, 100.0, 100.0, 97.0, 98.0),   # 40 dip
            _candle(0, 98.0, 99.0, 98.0, 99.0),     # 41 green
            _candle(0, 99.0, 100.0, 99.0, 100.0),   # 42 signal
            _candle(0, 100.0, 100.0, 96.0, 97.0),   # 43 dip (new low)
            _candle(0, 97.0, 98.0, 97.0, 98.0),     # 44 green
            _candle(0, 98.0, 99.0, 98.0, 99.0),     # 45 signal
        ])
        dips, sigs = evaluate_all(candles, p, ticker="BTC/USDT")
        self.assertEqual([s.index for s in sigs], [42, 45])
        self.assertTrue({40, 43}.issubset(set(dips)))

    def test_empty_when_too_few_candles(self):
        dips, sigs = evaluate_all(_build(warmup=5), StrategyParams(**LENIENT))
        self.assertEqual((dips, sigs), ([], []))


class TestTrackOutcomes(unittest.TestCase):
    """Forward-scan that classifies each signal (WIN/LOSS/PART/OPEN/EXP) and
    records where TP1/TP2/SL were hit — drives the chart's outcome markers."""

    @staticmethod
    def _c(high, low):
        return [0, 100.0, high, low, 100.0, 100.0]   # only H/L matter here

    def _sig(self, sl=96.0):
        return StrategySignal(ts=0, entry=100.0, tp1=103.0, tp2=109.0, rsi=20.0,
                              sl=sl, index=2)

    def test_win_on_tp2(self):
        candles = [self._c(101, 99), self._c(101, 99), self._c(100, 100),
                   self._c(104, 100), self._c(110, 104)]
        oc = track_outcomes(candles, [self._sig()])[0]
        self.assertEqual(oc.status, "win")
        self.assertEqual(oc.tp1_index, 3)
        self.assertEqual(oc.tp2_index, 4)

    def test_loss_on_sl_before_tp1(self):
        candles = [self._c(101, 99), self._c(101, 99), self._c(100, 100),
                   self._c(101, 95)]
        oc = track_outcomes(candles, [self._sig(96.0)])[0]
        self.assertEqual(oc.status, "loss")
        self.assertEqual(oc.sl_index, 3)
        self.assertIsNone(oc.tp1_index)

    def test_part_when_sl_after_tp1(self):
        candles = [self._c(101, 99), self._c(101, 99), self._c(100, 100),
                   self._c(104, 100), self._c(101, 95)]
        oc = track_outcomes(candles, [self._sig(96.0)])[0]
        self.assertEqual(oc.status, "part")
        self.assertEqual(oc.tp1_index, 3)
        self.assertEqual(oc.sl_index, 4)

    def test_open_when_nothing_hit(self):
        candles = [self._c(101, 99), self._c(101, 99), self._c(100, 100),
                   self._c(101, 99), self._c(102, 99)]
        oc = track_outcomes(candles, [self._sig(96.0)])[0]
        self.assertEqual(oc.status, "open")

    def test_expired_after_window_without_tp1(self):
        candles = [self._c(101, 99), self._c(101, 99), self._c(100, 100),
                   self._c(101, 99), self._c(102, 99)]
        oc = track_outcomes(candles, [self._sig(sl=None)], track_window=2)[0]
        self.assertEqual(oc.status, "expired")

    def test_tp2_takes_priority_over_sl_on_the_same_bar(self):
        candles = [self._c(101, 99), self._c(101, 99), self._c(100, 100),
                   self._c(110, 95)]
        oc = track_outcomes(candles, [self._sig(96.0)])[0]
        self.assertEqual(oc.status, "win")


class TestLicence(unittest.TestCase):
    def test_verify_url_derived_from_relay(self):
        from licence import verify_url_from_relay
        self.assertEqual(verify_url_from_relay("https://h.x/poll.php"),
                         "https://h.x/verify.php")
        self.assertEqual(verify_url_from_relay("https://h.x/sub/poll.php?token=z"),
                         "https://h.x/sub/verify.php")
        self.assertEqual(verify_url_from_relay("https://h.x"),
                         "https://h.x/verify.php")

    def test_empty_token_is_rejected_without_network(self):
        import licence
        status, _, _ = licence.verify("https://h.x/verify.php", "")
        self.assertEqual(status, "rejected")

    def test_trial_url_derived_from_relay(self):
        from licence import trial_url_from_relay
        self.assertEqual(trial_url_from_relay("https://h.x/poll.php"),
                         "https://h.x/trial.php")
        self.assertEqual(trial_url_from_relay("https://h.x/sub/poll.php?t=z"),
                         "https://h.x/sub/trial.php")

    def test_machine_fingerprint_is_stable_hex(self):
        import licence
        a = licence.machine_fingerprint()
        b = licence.machine_fingerprint()
        self.assertEqual(a, b)                       # stable across calls
        self.assertEqual(len(a), 64)                 # sha256 hex
        int(a, 16)                                   # valid hex (raises otherwise)

    @contextlib.contextmanager
    def _fake_urlopen(self, payload, http_error_code=None):
        """Patch licence.urllib.request.urlopen to return ``payload`` JSON,
        optionally raising an HTTPError carrying that JSON body."""
        import io
        import json as _json
        import urllib.error
        import licence
        body = _json.dumps(payload).encode("utf-8")

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=None):
            if http_error_code is not None:
                raise urllib.error.HTTPError(
                    "u", http_error_code, "err", {}, io.BytesIO(body))
            return _Resp(body)

        orig = licence.urllib.request.urlopen
        licence.urllib.request.urlopen = fake
        try:
            yield
        finally:
            licence.urllib.request.urlopen = orig

    def test_start_trial_success_returns_token(self):
        import licence
        with self._fake_urlopen({"ok": True, "token": "abc123", "expires_at": 999, "trial": True}):
            status, _, token, exp = licence.start_trial("https://h.x/trial.php", "a@b.com", "m" * 64)
        self.assertEqual((status, token, exp), ("ok", "abc123", 999))

    def test_start_trial_already_used_maps_to_used(self):
        import licence
        with self._fake_urlopen({"ok": False, "code": "used", "error": "already used"},
                                http_error_code=403):
            status, msg, token, _ = licence.start_trial("https://h.x/trial.php", "a@b.com", "m" * 64)
        self.assertEqual(status, "used")
        self.assertEqual(token, "")

    def test_licence_status_reports_trial_flag(self):
        import licence
        with self._fake_urlopen({"ok": True, "expires_at": 123, "trial": True}):
            st = licence.licence_status("https://h.x/verify.php", "tok")
        self.assertEqual(st["status"], "ok")
        self.assertTrue(st["trial"])
        self.assertEqual(st["expires_at"], 123)


class TestStrategyLicenceGate(unittest.TestCase):
    """The built-in strategy must not trade without a valid licence, and must
    tolerate a transient server outage within the grace window."""

    def _runner(self):
        import strategy_runner
        return strategy_runner.StrategyRunner(
            on_signal=lambda p: None, log=lambda m: None,
            get_token=lambda: "tok", get_verify_url=lambda: "https://h.x/verify.php")

    def _patch(self, result):
        import licence
        licence.verify = lambda url, token, timeout=10.0: result

    def setUp(self):
        import licence
        self._orig = licence.verify

    def tearDown(self):
        import licence
        licence.verify = self._orig

    def test_valid_licence_allows_trading(self):
        r = self._runner()
        self._patch(("ok", "valid", 0))
        self.assertTrue(r._ensure_licensed())
        self.assertIs(r.licensed(), True)

    def test_rejected_licence_blocks_trading(self):
        r = self._runner()
        self._patch(("rejected", "invalid token", 0))
        self.assertFalse(r._ensure_licensed())
        self.assertIs(r.licensed(), False)

    def test_recovers_when_token_becomes_valid(self):
        r = self._runner()
        self._patch(("rejected", "invalid token", 0))
        self.assertFalse(r._ensure_licensed())
        self._patch(("ok", "valid", 0))
        r._next_verify_ts = 0.0                 # force an immediate re-check
        self.assertTrue(r._ensure_licensed())

    def test_grace_period_tolerates_outage(self):
        r = self._runner()
        self._patch(("ok", "valid", 0))
        self.assertTrue(r._ensure_licensed())   # establishes last-good time
        self._patch(("error", "unreachable", 0))
        r._next_verify_ts = 0.0
        self.assertTrue(r._ensure_licensed())   # within grace -> still allowed

    def test_outage_without_prior_success_is_denied(self):
        r = self._runner()
        self._patch(("error", "unreachable", 0))
        self.assertFalse(r._ensure_licensed())  # never verified -> fail closed


if __name__ == "__main__":
    unittest.main(verbosity=2)
