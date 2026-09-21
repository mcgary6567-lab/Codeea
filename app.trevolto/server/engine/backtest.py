"""Historical backtester for the built-in EMA 9/21 crossover strategy.

Pure and deterministic: it replays the *same* signals the live bot trades — the
entry/exit events from ``strategy.evaluate_all_crossover`` — over a list of
closed OHLCV candles, adds SL / TP1-scale-out / TP2 management, and tallies
realized P&L into trades, an equity curve and summary stats. Costs (taker fee
per side + optional futures funding) are modelled so results are net.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import strategy

SCALE = 0.5   # fraction closed at TP1 (rest rides to TP2 / exit), stop → breakeven


@dataclass
class BacktestConfig:
    start_equity: float = 1_000.0
    size_pct: float = 100.0          # position notional as % of equity (no leverage)
    risk_pct: float = 0.0            # risk-% sizing off the stop distance (0 = use size_pct)
    fee_pct: float = 0.05            # taker fee % per fill
    funding_pct_8h: float = 0.01     # futures funding % per 8h held (0 = off)
    apply_costs: bool = True
    bar_seconds: float = 3600.0
    allow_short: bool = True
    news_windows: tuple = ()          # (start_ms, end_ms) blackout windows — skip entries inside
    daily_loss_pct: float = 0.0       # stop opening new trades once the day is down this % (0 = off)
    daily_profit_pct: float = 0.0     # stop opening new trades once the day is up this % (0 = off)
    scale_in: bool = False            # pyramid: add to a winner at scale_in_trigger% of the way to TP
    scale_in_trigger: float = 40.0    # % of the entry->TP distance that must be covered
    scale_in_size: float = 50.0       # add-on size as % of the original position
    scale_in_be: bool = True          # add-on protected by a break-even (entry) stop
    weekend_pause: bool = False       # skip entries that fall on Sat/Sun
    weekend_tz: float = 0.0           # hours offset from UTC for the weekend window (0 = UTC)


@dataclass
class BacktestTrade:
    side: str
    entry_i: int
    exit_i: int
    entry_ts: int
    exit_ts: int
    entry: float
    exit: float
    qty: float
    pnl: float
    fees: float
    funding: float
    r: float
    reason: str               # "sl" / "tp" / "cross" / "reverse"
    scaled: bool
    scaled_in: bool = False   # a pyramiding add-on was opened on this trade


@dataclass
class BacktestResult:
    trades: List[BacktestTrade] = field(default_factory=list)
    equity: List[Tuple[int, float]] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _fee(notional: float, cfg: BacktestConfig) -> float:
    return abs(notional) * (cfg.fee_pct / 100.0) if cfg.apply_costs else 0.0


def run_backtest(candles, params: strategy.StrategyParams, cfg: BacktestConfig) -> BacktestResult:
    """Tally the engine's own enter/partial/exit events into trades + equity."""
    res = BacktestResult()
    n = len(candles)
    if n < strategy.crossover_need(params):
        res.stats = _summarise([], cfg.start_equity)
        res.equity = [(int(candles[0][0]) if candles else 0, cfg.start_equity)]
        return res

    ts = [int(c[0]) for c in candles]
    equity = cfg.start_equity
    res.equity = [(ts[0], equity)]
    cur: dict = {}

    def funding(entry_i: int, exit_i: int, notional: float) -> float:
        if not cfg.apply_costs or cfg.funding_pct_8h <= 0:
            return 0.0
        periods = ((exit_i - entry_i) * cfg.bar_seconds) / (8.0 * 3600.0)
        return abs(notional) * (cfg.funding_pct_8h / 100.0) * periods

    news_skipped = 0
    day_skipped = 0
    weekend_skipped = 0
    scaled_in_count = 0
    day_key = None
    day_start_equity = equity         # equity at the start of the current calendar day (UTC)
    for i, ev in strategy.evaluate_all_crossover(candles, params):
        act = ev["act"]
        if act == "enter":
            # Daily loss / profit circuit breaker — mirrors the live guardrail: once the
            # day's realized P&L crosses the limit, stop opening NEW trades for that day
            # (open positions still close normally).
            d_key = ts[i] // 86_400_000
            if d_key != day_key:
                day_key = d_key
                day_start_equity = equity
            # Weekend pause — skip entries on Sat/Sun (epoch day 0 = Thu, so +3 aligns Mon=0).
            # weekend_tz shifts the day boundary so the weekend can follow local time.
            if cfg.weekend_pause and (((ts[i] + int(cfg.weekend_tz * 3_600_000)) // 86_400_000) + 3) % 7 >= 5:
                weekend_skipped += 1
                cur = {}
                continue
            if day_start_equity > 0 and (
                (cfg.daily_loss_pct > 0 and equity <= day_start_equity * (1 - cfg.daily_loss_pct / 100.0)) or
                (cfg.daily_profit_pct > 0 and equity >= day_start_equity * (1 + cfg.daily_profit_pct / 100.0))):
                day_skipped += 1
                cur = {}
                continue
            if cfg.news_windows and any(a <= ts[i] <= b for (a, b) in cfg.news_windows):
                news_skipped += 1     # news filter: no entries inside a high-impact window
                cur = {}
                continue
            if ev["side"] == "short" and not cfg.allow_short:
                cur = {}
                continue
            entry, sl = float(ev["entry"]), float(ev["sl"])
            risk = abs(entry - sl)
            if cfg.risk_pct > 0 and risk > 0:
                qty = (equity * cfg.risk_pct / 100.0) / risk
            else:
                qty = (equity * cfg.size_pct / 100.0) / entry if entry > 0 else 0.0
            cur = {"side": ev["side"], "entry": entry, "entry_i": i, "qty": qty, "rem": qty,
                   "tp": float(ev.get("tp", 0) or 0), "init_risk": risk,
                   "realized": 0.0, "fees": _fee(qty * entry, cfg), "scaled": False}
        elif act == "partial" and cur:
            part = cur["qty"] * float(ev.get("fraction", 0.5))
            px = float(ev["price"])
            gross = (px - cur["entry"]) * part if cur["side"] == "long" else (cur["entry"] - px) * part
            cur["realized"] += gross - _fee(part * px, cfg)
            cur["fees"] += _fee(part * px, cfg)
            cur["rem"] -= part
            cur["scaled"] = True
        elif act == "exit" and cur:
            px = float(ev["price"]); rem = cur["rem"]
            gross = (px - cur["entry"]) * rem if cur["side"] == "long" else (cur["entry"] - px) * rem
            fees = cur["fees"] + _fee(rem * px, cfg)
            fund = funding(cur["entry_i"], i, cur["qty"] * cur["entry"])
            net = cur["realized"] + gross - fees - fund
            qty_total = cur["qty"]
            scaled_in = False
            # Scale-in (pyramiding): once price is scale_in_trigger% of the way to TP, add a
            # smaller same-direction lot at that price, protected by a break-even stop. The
            # add-on rides to the trade's exit, or exits at break-even if price returns first.
            tp = cur.get("tp", 0.0)
            if cfg.scale_in and tp and tp != cur["entry"]:
                e, sd, ei = cur["entry"], cur["side"], cur["entry_i"]
                sign = 1.0 if sd == "long" else -1.0
                trig_px = e + sign * (cfg.scale_in_trigger / 100.0) * (tp - e)
                k = -1
                for m in range(ei + 1, i + 1):
                    if (sd == "long" and float(candles[m][2]) >= trig_px) or \
                       (sd == "short" and float(candles[m][3]) <= trig_px):
                        k = m
                        break
                if k >= 0:
                    add_qty = cur["qty"] * (cfg.scale_in_size / 100.0)
                    be_stop = e if cfg.scale_in_be else (e - sign * cur["init_risk"])
                    add_exit_px, add_exit_i = px, i         # default: rides to the trade exit
                    for m in range(k + 1, i + 1):
                        if (sd == "long" and float(candles[m][3]) <= be_stop) or \
                           (sd == "short" and float(candles[m][2]) >= be_stop):
                            add_exit_px, add_exit_i = be_stop, m
                            break
                    agross = (add_exit_px - trig_px) * add_qty if sd == "long" else (trig_px - add_exit_px) * add_qty
                    afees = _fee(add_qty * trig_px, cfg) + _fee(add_qty * add_exit_px, cfg)
                    afund = funding(k, add_exit_i, add_qty * trig_px)
                    net += agross - afees - afund
                    fees += afees
                    fund += afund
                    qty_total += add_qty
                    scaled_in = True
                    scaled_in_count += 1
            equity += net
            res.trades.append(BacktestTrade(
                side=cur["side"], entry_i=cur["entry_i"], exit_i=i,
                entry_ts=ts[cur["entry_i"]], exit_ts=ts[i], entry=cur["entry"], exit=px,
                qty=qty_total, pnl=net, fees=fees, funding=fund,
                r=(net / (cur["init_risk"] * cur["qty"])) if cur["init_risk"] > 0 and cur["qty"] > 0 else 0.0,
                reason=ev.get("reason", "exit"), scaled=cur["scaled"], scaled_in=scaled_in))
            res.equity.append((ts[i], equity))
            cur = {}

    res.stats = _summarise(res.trades, cfg.start_equity, res.equity)
    if isinstance(res.stats, dict):
        res.stats["news_skipped"] = news_skipped
        res.stats["day_skipped"] = day_skipped
        res.stats["weekend_skipped"] = weekend_skipped
        res.stats["scaled_in"] = scaled_in_count
    return res


def _summarise(trades: List[BacktestTrade], start_equity: float,
               equity: Optional[List[Tuple[int, float]]] = None) -> dict:
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    net = sum(t.pnl for t in trades)
    max_dd = 0.0
    peak = start_equity
    for _, eq in (equity or []):
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100.0)
    end_equity = (equity[-1][1] if equity else start_equity)
    return {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": (len(wins) / len(trades) * 100.0) if trades else 0.0,
        "longs": sum(1 for t in trades if t.side == "long"),
        "shorts": sum(1 for t in trades if t.side == "short"),
        "net_pnl": net, "return_pct": (net / start_equity * 100.0) if start_equity else 0.0,
        "fees": sum(t.fees for t in trades), "funding": sum(t.funding for t in trades),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0,
        "avg_r": (sum(t.r for t in trades) / len(trades)) if trades else 0.0,
        "best": max((t.pnl for t in trades), default=0.0),
        "worst": min((t.pnl for t in trades), default=0.0),
        "max_drawdown": max_dd, "end_equity": end_equity,
    }


def buy_hold_return(candles) -> float:
    if len(candles) < 2:
        return 0.0
    first, last = float(candles[0][4]), float(candles[-1][4])
    return (last / first - 1.0) * 100.0 if first else 0.0


def trades_to_csv(trades: List[BacktestTrade]) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["#", "side", "entry_ts", "exit_ts", "entry", "exit", "qty", "pnl", "fees", "funding", "R", "reason", "scaled"])
    for k, t in enumerate(trades, 1):
        w.writerow([k, t.side, t.entry_ts, t.exit_ts, f"{t.entry:.8g}", f"{t.exit:.8g}", f"{t.qty:.8g}",
                    f"{t.pnl:.2f}", f"{t.fees:.4f}", f"{t.funding:.4f}", f"{t.r:.3f}", t.reason, int(t.scaled)])
    return buf.getvalue()
