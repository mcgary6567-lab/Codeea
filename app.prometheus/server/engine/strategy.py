"""Built-in strategy engine — EMA fast/slow crossover with a trend filter.

Faithful port of the "STRATEGY (EMA 9 / 21 cross)" indicator:

  * **Cross**   — Fast EMA (9) crossing the Slow EMA (21) is the trigger.
  * **Trend**   — Trend EMA (50) directional filter: only BUY above it / SELL
    below it (toggle with ``use_trend_filter``).
  * **Confirm** — ``confirm`` candles after the cross, each a strong candle in
    the trade direction (body-to-range ≥ ``min_body``). ``0`` = enter on the
    cross bar itself.
  * **Stop**    — the recent swing low (long) / swing high (short) — a pivot with
    ``swing_bars`` bars each side, searched up to ``swing_lookback`` bars back —
    plus a ``sl_buffer_pct`` buffer beyond it.
  * **Exit**    — opposite EMA cross (reverse). Take-profits (``tp1_r``/``tp2_r``
    R-multiples) and the stop are managed by the trading pipeline / backtester.

No RSI. Pure & deterministic over CLOSED candles only (non-repaint): the caller
drops the live forming candle, so signals never repaint.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class StrategyParams:
    fast_ema: int = 9            # Fast EMA period
    slow_ema: int = 21          # Slow EMA period
    trend_ema: int = 50         # Trend EMA period (directional filter)
    use_trend_filter: bool = True  # only BUY above / SELL below the trend EMA
    confirm: int = 1            # confirmation candles after the cross (0 = enter on cross)
    min_body: float = 0.4       # min body-to-range ratio of a valid confirmation candle
    sl_buffer_pct: float = 0.05  # stop-loss buffer beyond the swing point (% of price)
    swing_bars: int = 2         # bars each side that define a swing point
    swing_lookback: int = 40    # max bars to look back for the swing point
    tp1_r: float = 1.0          # take-profit 1 as an R multiple (0 = off)
    tp2_r: float = 2.0          # take-profit 2 as an R multiple (0 = off)


# ---------------------------------------------------------------------------
# Series helpers (pure; None during warmup where the value isn't defined yet).
# ---------------------------------------------------------------------------
def ema_series(values: List[float], length: int) -> List[Optional[float]]:
    """EMA seeded with an SMA of the first ``length`` values (matches Pine)."""
    n = len(values)
    out: List[Optional[float]] = [None] * n
    if length <= 0 or n < length:
        return out
    alpha = 2.0 / (length + 1.0)
    seed = sum(values[:length]) / length
    out[length - 1] = seed
    prev = seed
    for i in range(length, n):
        prev = values[i] * alpha + prev * (1 - alpha)
        out[i] = prev
    return out


def _pivot_low(lows: List[float], i: int, bars: int, lookback: int) -> Optional[float]:
    """Most recent confirmed swing low at or before bar ``i`` (a bar that is the
    lowest of the ``bars`` on each side), searched back up to ``lookback`` bars."""
    bars = max(1, bars)
    start = max(bars, i - lookback)
    for j in range(i - bars, start - 1, -1):
        seg = lows[j - bars:j + bars + 1]
        if len(seg) == 2 * bars + 1 and lows[j] == min(seg):
            return lows[j]
    return None


def _pivot_high(highs: List[float], i: int, bars: int, lookback: int) -> Optional[float]:
    bars = max(1, bars)
    start = max(bars, i - lookback)
    for j in range(i - bars, start - 1, -1):
        seg = highs[j - bars:j + bars + 1]
        if len(seg) == 2 * bars + 1 and highs[j] == max(seg):
            return highs[j]
    return None


def crossover_need(params: StrategyParams) -> int:
    """Minimum candles before the engine can produce a signal."""
    return max(params.slow_ema, params.trend_ema) + params.confirm + 2


def crossover_arrays(candles, params: StrategyParams):
    """Precompute the series the engine + backtester share: OHLC + the three EMAs."""
    o = [float(c[1]) for c in candles]
    h = [float(c[2]) for c in candles]
    low = [float(c[3]) for c in candles]
    cl = [float(c[4]) for c in candles]
    fast = ema_series(cl, params.fast_ema)
    slow = ema_series(cl, params.slow_ema)
    trend = ema_series(cl, params.trend_ema)
    return o, h, low, cl, fast, slow, trend


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
def _replay_crossover(candles, params: StrategyParams, ticker: str = ""):
    """Replay the EMA-cross state machine over CLOSED candles.

    Returns ``[(bar_index, event), ...]`` where event is one of:
      * ``{"act":"enter","side":"long"|"short","entry":float,"sl":float,"ts":int}``
      * ``{"act":"exit","side":...,"reason":"cross"|"reverse","ts":int}``
    """
    n = len(candles)
    if n < 3:
        return []
    ts = [int(c[0]) for c in candles]
    o, h, low, cl, fast, slow, trend = crossover_arrays(candles, params)
    confirm = max(0, int(params.confirm))
    min_body = max(0.0, float(params.min_body))
    buf = max(0.0, float(params.sl_buffer_pct)) / 100.0

    pos = "flat"                      # flat / long / short
    pending: Optional[dict] = None    # {"side","count","bar"} awaiting confirmation
    events = []

    for i in range(1, n):
        if None in (fast[i], slow[i], fast[i - 1], slow[i - 1]):
            continue
        cu = fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]   # bullish cross
        cd = fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]   # bearish cross

        # 1) exit an open position on the opposite cross
        if pos == "long" and cd:
            events.append((i, {"act": "exit", "side": "long", "reason": "cross", "ts": ts[i]}))
            pos = "flat"
        elif pos == "short" and cu:
            events.append((i, {"act": "exit", "side": "short", "reason": "cross", "ts": ts[i]}))
            pos = "flat"

        # 2) a fresh cross arms a confirmation window
        if cu:
            pending = {"side": "long", "count": 0, "bar": i}
        elif cd:
            pending = {"side": "short", "count": 0, "bar": i}

        # 3) evaluate the pending setup
        if pending is not None:
            side = pending["side"]
            # Keep the setup armed only while the EMA regime still agrees; cancel
            # only when the cross flips back (NOT on a single opposite candle — that
            # was over-suppressing longs in recoveries).
            regime = (fast[i] > slow[i]) if side == "long" else (fast[i] < slow[i])
            if not regime:
                pending = None
        if pending is not None:
            side = pending["side"]
            rng = h[i] - low[i]
            body = abs(cl[i] - o[i]) / rng if rng > 0 else 0.0
            strong = body >= min_body
            dir_ok = (cl[i] > o[i]) if side == "long" else (cl[i] < o[i])
            trend_ok = (not params.use_trend_filter) or (
                trend[i] is not None and (cl[i] > trend[i] if side == "long" else cl[i] < trend[i]))

            fire = False
            if confirm == 0 and pending["bar"] == i:
                fire = trend_ok                      # enter on the cross bar
            elif i > pending["bar"]:
                if strong and dir_ok:
                    pending["count"] += 1
                    if pending["count"] >= confirm:
                        fire = trend_ok
                else:
                    pending["count"] = 0             # streak broke; keep waiting

            if fire:
                if pos != "flat" and pos != side:    # reverse
                    events.append((i, {"act": "exit", "side": pos, "reason": "reverse", "ts": ts[i]}))
                    pos = "flat"
                if pos == "flat":
                    if side == "long":
                        sw = _pivot_low(low, i, params.swing_bars, params.swing_lookback)
                        base = sw if sw is not None else min(low[max(0, i - 5):i + 1])
                        sl = base * (1 - buf)
                    else:
                        sw = _pivot_high(h, i, params.swing_bars, params.swing_lookback)
                        base = sw if sw is not None else max(h[max(0, i - 5):i + 1])
                        sl = base * (1 + buf)
                    events.append((i, {"act": "enter", "side": side, "entry": cl[i],
                                       "sl": sl, "ts": ts[i]}))
                    pos = side
                    pending = None
    return events


def evaluate_crossover(candles, params: StrategyParams, ticker: str = ""):
    """Instructions for the NEWEST closed candle only (runner contract)."""
    last = len(candles) - 1
    return [ev for (i, ev) in _replay_crossover(candles, params, ticker) if i == last]


def evaluate_all_crossover(candles, params: StrategyParams, ticker: str = ""):
    """Every instruction over the whole candle list (for charting / backtests)."""
    return _replay_crossover(candles, params, ticker)


def entry_block_reason(candles, params: StrategyParams) -> str:
    """Short human reason the newest bar didn't enter (best-effort)."""
    n = len(candles)
    if n < crossover_need(params):
        return "warming up"
    o, h, low, cl, fast, slow, trend = crossover_arrays(candles, params)
    i = n - 1
    if fast[i] is None or slow[i] is None:
        return "EMAs warming up"
    if params.use_trend_filter and trend[i] is not None and slow[i] is not None:
        if fast[i] > slow[i] and cl[i] < trend[i]:
            return f"BUY held — price below trend EMA{params.trend_ema}"
        if fast[i] < slow[i] and cl[i] > trend[i]:
            return f"SELL held — price above trend EMA{params.trend_ema}"
    return ""
