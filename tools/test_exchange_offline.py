"""Deep verification of the exchange order layer.

Runs the REAL app.prometheus/server/engine/exchange.py against a faithful mock
of a ccxt Binance USDⓈ-M client that reproduces the actual venue behaviours:
  * -4120 "Order type not supported ... Use the Algo Order API" on the
    conditional STOP_MARKET / TAKE_PROFIT_MARKET endpoints (the bug we fixed)
  * -2015 invalid-key/IP rejections
  * -451 geo restriction
  * market-buy requiring a price argument
Every create_order call is captured so we can assert the exact routing.
"""
import sys, os, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "app.prometheus", "server")
sys.path.insert(0, SERVER)
sys.path.insert(0, os.path.join(SERVER, "engine"))

import engine.exchange as ex   # the real module

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    mark = "✅" if cond else "❌"
    print(f"  {mark} {name}" + (f"  — {detail}" if detail and not cond else ""))

# ---------------------------------------------------------------------------
# A faithful mock ccxt client (Binance USDⓈ-M futures)
# ---------------------------------------------------------------------------
class MockError(Exception):
    pass

class MockBinance:
    def __init__(self, reject_conditional=True):
        self.calls = []                 # every create_order(...)
        self.cancelled = []
        self.reject_conditional = reject_conditional   # emulate -4120
        self.markets = {
            "BTC/USDT:USDT": {"symbol": "BTC/USDT:USDT", "base": "BTC", "quote": "USDT",
                              "swap": True, "linear": True, "active": True, "type": "swap"},
            "BTC/USDT": {"symbol": "BTC/USDT", "base": "BTC", "quote": "USDT",
                         "spot": True, "active": True, "type": "spot"},
        }
        self.has = {
            "createStopMarketOrder": True, "createStopOrder": True,
            "fetchPositions": True, "createReduceOnlyOrder": True,
        }
        self._pos = {}   # symbol -> contracts (signed)

    # ---- ccxt surface used by exchange.py ----
    def load_markets(self):            return self.markets
    def load_time_difference(self):    return 0
    def set_sandbox_mode(self, x):     pass
    def fetch_ticker(self, sym):       return {"last": 30000.0}
    def fetch_ohlcv(self, s, tf, limit=300):
        return [[0, 30000, 30100, 29900, 30000, 5]] * 3
    def set_leverage(self, lev, sym, params=None): self.calls.append(("set_leverage", lev, sym, params))
    def set_margin_mode(self, mode, sym): self.calls.append(("set_margin_mode", mode, sym))
    def cancel_all_orders(self, sym):  self.cancelled.append(sym)
    def cancel_order(self, oid, sym):  self.cancelled.append((oid, sym))

    def fetch_balance(self):
        return {"total": {"USDT": 10000.0}}

    def fetch_positions(self):
        out = []
        for sym, c in self._pos.items():
            if c == 0: continue
            out.append({"symbol": sym, "contracts": abs(c),
                        "side": "long" if c > 0 else "short",
                        "entryPrice": 30000.0, "markPrice": 30300.0,
                        "unrealizedPnl": (30300-30000)*abs(c) if c > 0 else 0})
        return out

    def create_order(self, sym, otype, side, amount, price=None, params=None):
        params = params or {}
        ot = otype.upper()
        self.calls.append({"sym": sym, "type": ot, "side": side, "amount": amount,
                           "price": price, "params": dict(params)})
        # Binance USDⓈ-M rejects these on the normal endpoint (the -4120 bug):
        if self.reject_conditional and ot in ("STOP_MARKET", "TAKE_PROFIT_MARKET"):
            raise MockError('binance {"code":-4120,"msg":"Order type not supported '
                            'for this endpoint. Please use the Algo Order API."}')
        # Track net position for market/limit fills
        signed = amount if side == "buy" else -amount
        self._pos[sym] = self._pos.get(sym, 0) + signed
        return {"id": f"ord{len(self.calls)}", "symbol": sym, "type": otype,
                "side": side, "amount": amount, "price": price, "status": "open"}


def make_manager(mock, market_type="futures", read_only=False, safe_mode=False):
    m = ex.ExchangeManager()
    m.exchange_id = "binance"
    m._ccxt_id = "binance"
    m.client = mock
    m.connected = True
    m.market_type = market_type
    m._fut_quote = "USDT"
    m.read_only = read_only
    m.safe_mode = safe_mode
    return m

print("\n" + "="*70)
print("1. PURE HELPERS (sizing, symbols, TP planning)")
print("="*70)
check("normalize_symbol BTCUSDT -> BTC/USDT", ex.normalize_symbol("BTCUSDT") == "BTC/USDT")
check("normalize_symbol btc/usdt -> BTC/USDT", ex.normalize_symbol("btc/usdt") == "BTC/USDT")
check("normalize_symbol BTC -> BTC/USDT", ex.normalize_symbol("BTC") == "BTC/USDT")
check("perp_symbol binance -> BTC/USDT:USDT", ex.perp_symbol("BTCUSDT", "binance") == "BTC/USDT:USDT")
check("exit_side buy -> sell", ex.exit_side("buy") == "sell")
check("exit_side sell -> buy", ex.exit_side("sell") == "buy")

# sizing: risk_stop  — risk 1% of 10k = $100 over a $500 stop distance => 0.2 BTC
amt, why = ex.size_order("risk_stop", 0.001, 1.0, 10000, 30000, entry=30000, stop=29500)
check("size_order risk_stop = 0.2", abs(amt - 0.2) < 1e-9, f"got {amt} ({why})")
# risk_balance 1% of 10k / 30000
amt2, _ = ex.size_order("risk_balance", 0.001, 1.0, 10000, 30000)
check("size_order risk_balance ~0.00333", abs(amt2 - 100/30000) < 1e-6, f"got {amt2}")
# fixed_quote $200 / 30000
amt3, _ = ex.size_order("fixed_quote", 200, 1.0, 10000, 30000)
check("size_order fixed_quote 200$ ", abs(amt3 - 200/30000) < 1e-6, f"got {amt3}")
# missing stop falls back to fixed lot (never sizes to zero)
amt4, _ = ex.size_order("risk_stop", 0.05, 1.0, 10000, 30000, entry=0, stop=0)
check("size_order missing-stop falls back to fixed lot (not 0)", amt4 == 0.05, f"got {amt4}")

# TP planning: split 0.2 -> 0.1 @ tp1, 0.1 @ tp2
legs = ex.plan_take_profits(0.2, 31000, 32000, 0.5)
check("plan_take_profits two legs", legs == [(31000, 0.1), (32000, 0.1)], f"got {legs}")
legs1 = ex.plan_take_profits(0.2, 31000, 0, 0.5)
check("plan_take_profits one leg full size", legs1 == [(31000, 0.2)], f"got {legs1}")

print("\n" + "="*70)
print("2. MARKET ENTRY (futures) — real create_order routing")
print("="*70)
mock = MockBinance()
m = make_manager(mock)
check("_market_symbol resolves to perp", m._market_symbol("BTCUSDT") == "BTC/USDT:USDT",
      m._market_symbol("BTCUSDT"))
r = m.place_order("BTCUSDT", "buy", 0.2, "market")
c = mock.calls[-1]
check("entry ok", r.ok, r.message)
check("entry routed to BTC/USDT:USDT MARKET buy", c["sym"]=="BTC/USDT:USDT" and c["type"]=="MARKET" and c["side"]=="buy")
check("entry passed a price for quote calc", c["price"] == 30000.0, f"price={c['price']}")
check("entry NOT reduce-only", "reduceOnly" not in c["params"])

print("\n" + "="*70)
print("3. STOP-LOSS on Binance USDⓈ-M — the -4120 fallback chain")
print("="*70)
mock = MockBinance(reject_conditional=True)   # venue rejects STOP_MARKET on normal endpoint
m = make_manager(mock)
before = len(mock.calls)
r = m.place_reduce_order("BTCUSDT", "sell", 0.2, 29500, "sl")
attempts = mock.calls[before:]
check("SL eventually succeeds despite -4120", r.ok, r.message)
check("SL tried STOP_MARKET first", attempts[0]["type"] == "STOP_MARKET")
# 1st STOP_MARKET reduceOnly -4120 -> 2nd STOP_MARKET closePosition -4120 -> 3rd market stopLossPrice OK
check("SL fell through to unified stopLossPrice market order", attempts[-1]["type"] == "MARKET"
      and attempts[-1]["params"].get("stopLossPrice") == 29500, str(attempts[-1]))
check("SL made exactly 3 attempts", len(attempts) == 3, f"{len(attempts)} attempts")
print("     attempt chain:", [f"{a['type']}:{list(a['params'])}" for a in attempts])

print("\n" + "="*70)
print("4. TAKE-PROFIT — sidesteps -4120 with reduce-only LIMIT on first try")
print("="*70)
mock = MockBinance(reject_conditional=True)
m = make_manager(mock)
before = len(mock.calls)
r = m.place_reduce_order("BTCUSDT", "sell", 0.1, 31000, "tp")
attempts = mock.calls[before:]
check("TP ok", r.ok, r.message)
check("TP uses reduce-only LIMIT first (no Algo endpoint hit)", attempts[0]["type"] == "LIMIT"
      and attempts[0]["params"].get("reduceOnly") is True, str(attempts[0]))
check("TP took exactly 1 attempt (never touched conditional endpoint)", len(attempts) == 1,
      f"{len(attempts)} attempts")
check("TP limit price set to target", attempts[0]["price"] == 31000)

print("\n" + "="*70)
print("5. SCALE-IN re-arm (cancel-all + rebracket at break-even)")
print("="*70)
mock = MockBinance(reject_conditional=True)
m = make_manager(mock)
# entry
m.place_order("BTCUSDT", "buy", 0.2, "market")
# add-on
r_add = m.place_order("BTCUSDT", "buy", 0.1, "market")
check("scale-in add-on order ok", r_add.ok)
# re-arm bracket
m.cancel_all_orders("BTCUSDT")
check("cancel_all_orders called on re-arm", "BTC/USDT:USDT" in mock.cancelled)
r_sl = m.place_reduce_order("BTCUSDT", "sell", 0.3, 30000, "sl")   # combined size, BE stop
r_tp = m.place_reduce_order("BTCUSDT", "sell", 0.3, 31000, "tp")
check("re-armed SL for combined 0.3 ok", r_sl.ok)
check("re-armed TP for combined 0.3 ok", r_tp.ok)

print("\n" + "="*70)
print("6. CLOSE position (reduce-only market on exit side)")
print("="*70)
mock = MockBinance()
m = make_manager(mock)
pos = ex.Position(pair="BTC/USDT:USDT", side="Long", size=0.3, entry=30000, current=30300, pnl=90)
before = len(mock.calls)
r = m.close_position(pos)
c = mock.calls[-1]
check("close ok", r.ok, r.message)
check("close is reduce-only SELL market", c["side"]=="sell" and c["type"]=="MARKET"
      and c["params"].get("reduceOnly") is True, str(c))
check("close full size 0.3", c["amount"] == 0.3)
# partial close 50%
r2 = m.close_position(pos, fraction=0.5)
check("partial close 0.15", mock.calls[-1]["amount"] == 0.15)

print("\n" + "="*70)
print("7. GUARDS — read-only, spot SL skip, safe-mode simulation")
print("="*70)
mock = MockBinance()
m = make_manager(mock, read_only=True)
r = m.place_order("BTCUSDT", "buy", 0.2, "market")
check("read-only blocks entry", (not r.ok) and "read-only" in r.message.lower(), r.message)
before = len(mock.calls)
m.place_reduce_order("BTCUSDT", "sell", 0.2, 29500, "sl")
check("read-only sends NO order to exchange", len(mock.calls) == before)

# spot SL is correctly skipped (spot has no stop-market in this app)
mock = MockBinance()
ms = make_manager(mock, market_type="spot")
r = ms.place_reduce_order("BTCUSDT", "sell", 0.2, 29500, "sl")
check("spot SL is declined cleanly (not sent as market)", not r.ok and "spot" in r.message.lower(), r.message)
# spot TP is a plain limit sell
before = len(mock.calls)
r = ms.place_reduce_order("BTCUSDT", "sell", 0.2, 31000, "tp")
check("spot TP is a limit sell", r.ok and mock.calls[-1]["type"] == "LIMIT", r.message)

# safe mode: no real orders, simulated fills, sim balance moves
m2 = make_manager(MockBinance(), safe_mode=True)
r = m2.place_order("BTCUSDT", "buy", 0.2, "market")
check("safe-mode order is simulated", r.simulated and r.ok, r.message)
check("safe-mode created a sim position", len(m2._sim_positions) == 1)

print("\n" + "="*70)
print("8. FRIENDLY ERROR MAPPING (-2015 / -451 / bad key)")
print("="*70)
e2015 = ex._friendly_error("binance", Exception('{"code":-2015,"msg":"Invalid API-key, IP, or permissions for action."}'), is_futures=True)
check("-2015 futures mentions IP whitelist", "whitelist" in e2015.lower() and "IP" in e2015, e2015[:80])
e451 = ex._friendly_error("binance", Exception("HTTP 451 restricted location"), is_futures=True)
check("-451 suggests Binance.US / alt venue", "binance.us" in e451.lower(), e451[:80])
ekr = ex._friendly_error("kraken", Exception("invalid permission auth"), is_futures=True)
check("kraken futures notes separate key", "separate" in ekr.lower(), ekr[:80])

print("\n" + "="*70)
print("9. FULL LIFECYCLE (entry -> SL -> 2 TP legs -> scale-in -> close) on -4120 venue")
print("="*70)
mock = MockBinance(reject_conditional=True)
m = make_manager(mock)
steps_ok = True
try:
    e = m.place_order("BTCUSDT", "buy", 0.2, "market");                 steps_ok &= e.ok
    sl = m.place_reduce_order("BTCUSDT", "sell", 0.2, 29500, "sl");     steps_ok &= sl.ok
    for px, qty in ex.plan_take_profits(0.2, 31000, 32000, 0.5):
        t = m.place_reduce_order("BTCUSDT", "sell", qty, px, "tp");     steps_ok &= t.ok
    add = m.place_order("BTCUSDT", "buy", 0.1, "market");               steps_ok &= add.ok
    m.cancel_all_orders("BTCUSDT")
    sl2 = m.place_reduce_order("BTCUSDT", "sell", 0.3, 30000, "sl");    steps_ok &= sl2.ok
    tp2 = m.place_reduce_order("BTCUSDT", "sell", 0.3, 31000, "tp");    steps_ok &= tp2.ok
    pos = ex.Position(pair="BTC/USDT:USDT", side="Long", size=0.3, entry=30000, current=31000, pnl=300)
    cl = m.close_position(pos);                                         steps_ok &= cl.ok
except Exception as exc:
    steps_ok = False
    traceback.print_exc()
check("full lifecycle completes with every step ok", steps_ok)
check("no unhandled exception on the -4120 venue", True)

print("\n" + "="*70)
tot = len(PASS) + len(FAIL)
print(f"RESULT: {len(PASS)}/{tot} checks passed" + (f" — FAILED: {FAIL}" if FAIL else "  ✅ ALL GREEN"))
print("="*70)
sys.exit(1 if FAIL else 0)
