"""Historical backtester for the built-in EMA20 + RSI-50 crossover strategy.

Pure and deterministic: it replays the *same* lifecycle the live bot trades —
EMA/RSI crossover entries (confirmed by N strong green/red candles), an RSI-
extreme scale-out that moves the stop to breakeven, and an EMA-trail / swing-stop
exit — over a list of closed OHLCV candles, and tallies realized P&L into trades,
an equity curve and summary stats. Costs (taker fee per side + optional futures
funding) are modelled so results are net, or gross when both are zero.

The entry logic mirrors ``strategy._replay_crossover`` exactly (a test asserts
the entry bars/sides match); the exit adds the breakeven-after-scale-out the
live backend applies, which the pure signal engine doesn't model on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import strategy


@dataclass
class BacktestConfig:
    start_equity: float = 1_000.0    # starting balance (quote currency)
    size_pct: float = 100.0          # position notional as % of equity (no leverage)
    risk_pct: float = 0.0            # risk-% sizing off the stop distance (0 = use size_pct)
    fee_pct: float = 0.05            # taker fee % charged on each fill (entry/scale/exit)
    funding_pct_8h: float = 0.01     # futures funding % per 8h of holding (0 = off)
    apply_costs: bool = True         # master switch: False => gross (no fee/funding)
    bar_seconds: float = 3600.0      # candle length in seconds (for funding accrual)
    allow_short: bool = True         # shorts (futures); False => long-only


@dataclass
class BacktestTrade:
    side: str                 # "long" / "short"
    entry_i: int
    exit_i: int
    entry_ts: int
    exit_ts: int
    entry: float
    exit: float
    qty: float                # base-asset size at entry
    pnl: float                # net realized PnL (after fees + funding)
    fees: float
    funding: float
    r: float                  # R multiple = net pnl / initial risk (entry-stop)
    reason: str               # "ema" / "sl" / "reverse"
    scaled: bool              # did the RSI-extreme scale-out fire?


@dataclass
class BacktestResult:
    trades: List[BacktestTrade] = field(default_factory=list)
    equity: List[Tuple[int, float]] = field(default_factory=list)  # (ts, equity)
    stats: dict = field(default_factory=dict)


def _fee(notional: float, cfg: BacktestConfig) -> float:
    return abs(notional) * (cfg.fee_pct / 100.0) if cfg.apply_costs else 0.0


def run_backtest(candles, params: strategy.StrategyParams,
                 cfg: BacktestConfig) -> BacktestResult:
    """Replay the strategy over ``candles`` and return trades + equity + stats."""
    res = BacktestResult()
    n = len(candles)
    if n < strategy.crossover_need(params):
        res.stats = _summarise([], cfg.start_equity)
        res.equity = [(int(candles[0][0]) if candles else 0, cfg.start_equity)]
        return res

    ts = [int(c[0]) for c in candles]
    # Shared arrays — identical to the live engine (trend filter + ATR stop included).
    o, h, low, cl, ema, rsi, swing_low, swing_high, trend, atr, adx = strategy.crossover_arrays(
        candles, params)
    confirm = max(0, int(params.ma_confirm))
    min_body = max(0.0, params.ma_min_body)

    equity = cfg.start_equity
    res.equity = [(ts[0], equity)]
    # Open-position state.
    pos = "flat"
    entry = sl = qty = 0.0
    entry_i = 0
    init_risk = 0.0           # per-unit risk at entry (for R multiples)
    scaled = False
    open_fees = 0.0           # fees paid so far on the open trade (entry + scale)
    realized_partial = 0.0    # PnL already booked from the scale-out leg

    prev_ready = False
    green_run = red_run = 0

    def open_trade(side: str, i: int):
        nonlocal pos, entry, sl, qty, entry_i, init_risk, scaled, open_fees, realized_partial
        entry = cl[i]
        sl = strategy.crossover_stop(side, i, cl, swing_low, swing_high, atr, params)
        init_risk = abs(entry - sl)
        if cfg.risk_pct > 0 and init_risk > 0:
            qty = (equity * cfg.risk_pct / 100.0) / init_risk   # risk-% (ATR/swing) sizing
        else:
            qty = (equity * cfg.size_pct / 100.0) / entry if entry > 0 else 0.0
        notional = qty * entry
        scaled = False
        open_fees = _fee(notional, cfg)
        realized_partial = 0.0
        entry_i = i
        pos = side

    def funding_cost(exit_i: int) -> float:
        if not cfg.apply_costs or cfg.funding_pct_8h <= 0:
            return 0.0
        held = (exit_i - entry_i) * cfg.bar_seconds
        periods = held / (8.0 * 3600.0)
        return abs(qty * entry) * (cfg.funding_pct_8h / 100.0) * periods

    def close_trade(i: int, price: float, reason: str):
        nonlocal pos, equity, scaled
        # Remaining quantity after any scale-out.
        rem = qty * (1.0 - (params.ma_scale if scaled else 0.0))
        gross = (price - entry) * rem if pos == "long" else (entry - price) * rem
        fees = open_fees + _fee(rem * price, cfg)
        fund = funding_cost(i)
        net = realized_partial + gross - fees - fund
        equity += net
        res.trades.append(BacktestTrade(
            side=pos, entry_i=entry_i, exit_i=i, entry_ts=ts[entry_i], exit_ts=ts[i],
            entry=entry, exit=price, qty=qty, pnl=net, fees=fees, funding=fund,
            r=(net / (init_risk * qty)) if init_risk > 0 and qty > 0 else 0.0,
            reason=reason, scaled=scaled))
        res.equity.append((ts[i], equity))
        pos = "flat"

    for i in range(n):
        rng = h[i] - low[i]
        body = abs(cl[i] - o[i]) / rng if rng > 0 else 0.0
        strong = body >= min_body
        if cl[i] > o[i] and strong:
            green_run, red_run = green_run + 1, 0
        elif cl[i] < o[i] and strong:
            green_run, red_run = 0, red_run + 1
        else:
            green_run, red_run = 0, 0

        ready = ema[i] is not None and rsi[i] is not None
        if not ready:
            prev_ready = False
            continue

        long_aligned = cl[i] > ema[i] and rsi[i] > 50.0
        short_aligned = cl[i] < ema[i] and rsi[i] < 50.0
        trending = strategy.adx_ok(i, adx, params)
        enter_long = (prev_ready and long_aligned and green_run >= confirm
                      and strategy.trend_ok("long", i, cl, trend, params) and trending)
        enter_short = (prev_ready and short_aligned and red_run >= confirm and cfg.allow_short
                       and strategy.trend_ok("short", i, cl, trend, params) and trending)
        prev_ready = True

        # --- manage the open position (exit / scale) --- (TP is close-based, to
        # mirror strategy._replay_crossover exactly)
        tp1_r, tp2_r = max(0.0, params.ma_tp1_r), max(0.0, params.ma_tp2_r)
        if pos == "long":
            sl_eff = entry if scaled else sl     # breakeven after the scale-out
            tp2_hit = tp2_r > 0 and cl[i] >= entry + tp2_r * init_risk
            if low[i] <= sl_eff:
                close_trade(i, sl_eff, "sl")
            elif cl[i] < ema[i] or enter_short:
                close_trade(i, cl[i], "reverse" if enter_short else "ema")
            elif tp2_hit:
                close_trade(i, cl[i], "tp")
            elif not scaled and (rsi[i] >= params.ma_ob
                                 or (tp1_r > 0 and cl[i] >= entry + tp1_r * init_risk)):
                part = qty * params.ma_scale
                realized_partial += (cl[i] - entry) * part - _fee(part * cl[i], cfg)
                scaled = True
        elif pos == "short":
            sl_eff = entry if scaled else sl
            tp2_hit = tp2_r > 0 and cl[i] <= entry - tp2_r * init_risk
            if h[i] >= sl_eff:
                close_trade(i, sl_eff, "sl")
            elif cl[i] > ema[i] or enter_long:
                close_trade(i, cl[i], "reverse" if enter_long else "ema")
            elif tp2_hit:
                close_trade(i, cl[i], "tp")
            elif not scaled and (rsi[i] <= params.ma_os
                                 or (tp1_r > 0 and cl[i] <= entry - tp1_r * init_risk)):
                part = qty * params.ma_scale
                realized_partial += (entry - cl[i]) * part - _fee(part * cl[i], cfg)
                scaled = True

        # --- enter when flat (reversal opens the opposite leg on the same bar) ---
        if pos == "flat":
            if enter_long:
                open_trade("long", i)
            elif enter_short:
                open_trade("short", i)

    res.stats = _summarise(res.trades, cfg.start_equity, res.equity)
    return res


def _summarise(trades: List[BacktestTrade], start_equity: float,
               equity: Optional[List[Tuple[int, float]]] = None) -> dict:
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    net = sum(t.pnl for t in trades)
    fees = sum(t.fees for t in trades)
    funding = sum(t.funding for t in trades)
    # Max drawdown over the equity curve.
    max_dd = 0.0
    peak = start_equity
    for _, eq in (equity or []):
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100.0)
    end_equity = (equity[-1][1] if equity else start_equity)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(trades) * 100.0) if trades else 0.0,
        "longs": sum(1 for t in trades if t.side == "long"),
        "shorts": sum(1 for t in trades if t.side == "short"),
        "net_pnl": net,
        "return_pct": (net / start_equity * 100.0) if start_equity else 0.0,
        "fees": fees,
        "funding": funding,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0,
        "avg_r": (sum(t.r for t in trades) / len(trades)) if trades else 0.0,
        "best": max((t.pnl for t in trades), default=0.0),
        "worst": min((t.pnl for t in trades), default=0.0),
        "max_drawdown": max_dd,
        "end_equity": end_equity,
    }


def buy_hold_return(candles) -> float:
    """Percent return of simply holding from the first to the last close."""
    if len(candles) < 2:
        return 0.0
    first, last = float(candles[0][4]), float(candles[-1][4])
    return (last / first - 1.0) * 100.0 if first else 0.0


def optimize(candles, base: strategy.StrategyParams, cfg: BacktestConfig,
             grid: dict, metric: str = "net_pnl", top: int = 30) -> List[dict]:
    """Grid-search ``grid`` (param-name -> list of values) and return the top
    combos ranked by ``metric`` (each: ``{"overrides":..., "stats":...}``)."""
    import dataclasses
    import itertools
    keys = list(grid.keys())
    results: List[dict] = []
    for combo in itertools.product(*[grid[k] for k in keys]):
        overrides = dict(zip(keys, combo))
        r = run_backtest(candles, dataclasses.replace(base, **overrides), cfg)
        if r.stats.get("trades", 0) == 0:
            continue
        results.append({"overrides": overrides, "stats": r.stats})

    def key(x):
        v = x["stats"].get(metric, 0.0)
        return 1e18 if v == float("inf") else v
    results.sort(key=key, reverse=True)
    return results[:top]


def walk_forward(candles, base: strategy.StrategyParams, cfg: BacktestConfig,
                 grid: dict, metric: str = "net_pnl", split: float = 0.7,
                 top: int = 40) -> dict:
    """Robustness check against curve-fitting: optimize on the first ``split`` of
    the data (in-sample), then measure those best params on the held-out rest
    (out-of-sample). Returns ``{best, in_sample, out_sample, split}`` where the
    two are stats dicts. If the out-of-sample result collapses vs in-sample, the
    optimized params are likely overfit."""
    import dataclasses
    n = len(candles)
    k = max(1, int(n * split))
    in_c, out_c = candles[:k], candles[k:]
    results = optimize(in_c, base, cfg, grid, metric=metric, top=top)
    best = results[0]["overrides"] if results else {}
    bp = dataclasses.replace(base, **best)
    return {
        "best": best,
        "in_sample": run_backtest(in_c, bp, cfg).stats,
        "out_sample": run_backtest(out_c, bp, cfg).stats,
        "split": split,
    }


def trades_to_csv(trades: List[BacktestTrade]) -> str:
    """Render the trade list as CSV text (one row per closed trade)."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["#", "side", "entry_ts", "exit_ts", "entry", "exit", "qty",
                "pnl", "fees", "funding", "R", "reason", "scaled"])
    for k, t in enumerate(trades, 1):
        w.writerow([k, t.side, t.entry_ts, t.exit_ts, f"{t.entry:.8g}",
                    f"{t.exit:.8g}", f"{t.qty:.8g}", f"{t.pnl:.2f}", f"{t.fees:.4f}",
                    f"{t.funding:.4f}", f"{t.r:.3f}", t.reason, int(t.scaled)])
    return buf.getvalue()
