#!/usr/bin/env python3
"""Live exchange smoke-test — fires a REAL demo trade on Binance testnet.

Runs the app's own ExchangeManager (server/engine/exchange.py) end to end
against a real exchange endpoint so you can confirm order placement, the
reduce-only SL/TP bracket (the -4120 path), position read-back and close all
work with live keys — using fake testnet money.

USAGE (on the VPS / anywhere with open egress to the exchange):

    # 1) Get free testnet keys: https://testnet.binancefuture.com  (Futures)
    export EX_ID=binance          # or bybit / okx / bitget / kucoinfutures
    export EX_KEY=your_testnet_key
    export EX_SECRET=your_testnet_secret
    # export EX_PASSWORD=...      # only OKX / KuCoin / Bitget
    export EX_SYMBOL=BTC/USDT
    python3 tools/smoketest_exchange.py

By default it uses --testnet and a tiny size. Add --live to trade the real
venue (real money — only with a throwaway sub-account and a minimal size).
Nothing is left open: the position is closed and stray orders cancelled at
the end.
"""
import argparse, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "app.prometheus", "server")
sys.path.insert(0, os.path.abspath(SERVER))
sys.path.insert(0, os.path.abspath(os.path.join(SERVER, "engine")))

import engine.exchange as ex


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="hit the LIVE venue (real money)")
    ap.add_argument("--size", type=float, default=0.0, help="base size (default: ~min notional)")
    ap.add_argument("--market", default="futures", choices=["spot", "futures"])
    args = ap.parse_args()

    exid = os.environ.get("EX_ID", "binance")
    key = os.environ.get("EX_KEY", "")
    sec = os.environ.get("EX_SECRET", "")
    pw = os.environ.get("EX_PASSWORD", "")
    symbol = os.environ.get("EX_SYMBOL", "BTC/USDT")
    testnet = not args.live

    if not key or not sec:
        print("✗ Set EX_KEY and EX_SECRET (and EX_PASSWORD for OKX/KuCoin/Bitget).")
        return 2

    print(f"\n=== LIVE SMOKE TEST — {exid} {args.market} "
          f"({'TESTNET' if testnet else 'LIVE — REAL MONEY'}) ===")

    m = ex.ExchangeManager()
    try:
        m.connect(exid, key, sec, password=pw, testnet=testnet,
                  read_only=False, safe_mode=False, market_type=args.market)
    except ex.ExchangeError as e:
        print(f"✗ connect failed:\n{e}")
        return 1
    print("✓ connected & keys validated")

    bal = m.fetch_balance()
    print(f"✓ balance: {bal:.2f} USDT")

    price = m._last_price(symbol)
    if price <= 0:
        print("✗ could not read price")
        return 1
    print(f"✓ price {symbol}: {price:g}")

    # Size: default to ~$120 notional (safely above Binance's ~$100 futures min).
    size = args.size or round(120.0 / price, 6)
    sl = round(price * 0.97, 2)     # 3% below
    tp = round(price * 1.03, 2)     # 3% above
    xside = ex.exit_side("buy")

    print(f"\n→ MARKET BUY {size} {symbol} (~${size*price:,.0f})")
    r = m.place_order(symbol, "buy", size, "market")
    print(("  ✓ " if r.ok else "  ✗ ") + r.message)
    if not r.ok:
        return 1

    time.sleep(1.5)
    print(f"→ SL (reduce-only) @ {sl}")
    r = m.place_reduce_order(symbol, xside, size, sl, "sl")
    print(("  ✓ " if r.ok else "  ⚠ ") + r.message)

    print(f"→ TP (reduce-only) @ {tp}")
    r = m.place_reduce_order(symbol, xside, size, tp, "tp")
    print(("  ✓ " if r.ok else "  ⚠ ") + r.message)

    time.sleep(1.5)
    print("\n→ read positions back")
    for p in m.fetch_positions():
        print(f"  • {p.side} {p.size} {p.pair} entry {p.entry:g} mark {p.current:g} pnl {p.pnl:.4f}")

    print("\n→ cleanup: cancel orders + close position")
    m.cancel_all_orders(symbol)
    for p in m.fetch_positions():
        if p.pair.split(":")[0].replace("/", "") == symbol.replace("/", ""):
            c = m.close_position(p)
            print(("  ✓ " if c.ok else "  ✗ ") + c.message)

    print("\n=== DONE — full lifecycle exercised on a live endpoint ✓ ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
