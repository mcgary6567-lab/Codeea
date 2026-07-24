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
    for i, ev in strategy.evaluate_all_crossover(candles, params):
        act = ev["act"]
        if act == "enter":
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
                   "init_risk": risk, "realized": 0.0, "fees": _fee(qty * entry, cfg), "scaled": False}
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
            equity += net
            res.trades.append(BacktestTrade(
                side=cur["side"], entry_i=cur["entry_i"], exit_i=i,
                entry_ts=ts[cur["entry_i"]], exit_ts=ts[i], entry=cur["entry"], exit=px,
                qty=cur["qty"], pnl=net, fees=fees, funding=fund,
                r=(net / (cur["init_risk"] * cur["qty"])) if cur["init_risk"] > 0 and cur["qty"] > 0 else 0.0,
                reason=ev.get("reason", "exit"), scaled=cur["scaled"]))
            res.equity.append((ts[i], equity))
            cur = {}

    res.stats = _summarise(res.trades, cfg.start_equity, res.equity)
    if isinstance(res.stats, dict):
        res.stats["news_skipped"] = news_skipped
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
