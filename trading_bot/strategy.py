"""Built-in strategy engine — EMA20 + RSI-50 crossover (long + short).

A pure, deterministic function over OHLCV candles so the bot can generate its
own entries without TradingView. Enter long when price closes above EMA20 with
RSI>50 (mirror for shorts), confirmed by N strong green/red candles; scale out
at RSI 70/30, trail/exit on the EMA, with a swing-based protective stop.

Design notes
------------
* **Pure & deterministic.** ``evaluate_crossover`` replays the whole position
  state machine from scratch over *closed* candles and returns the newest bar's
  instructions only — no hidden state, no I/O, trivially unit-testable.
* **Non-repaint.** The caller passes only confirmed/closed candles (the live
  forming candle is dropped upstream), so signals never repaint.
* RSI uses Wilder's smoothing; EMA is SMA-seeded. Divergence from a TradingView
  chart comes only from the candle data (exchange OHLCV vs TV aggregation),
  which is why the runner pulls candles from the same exchange the user trades.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class StrategyParams:
    """User-tunable inputs for the built-in strategy (EMA20 + RSI-50 crossover)."""

    rsi_len: int = 14                # RSI length (momentum centerline at 50)
    # EMA20 + RSI-50 crossover (long + short, dynamic exits).
    ma_len: int = 20                 # EMA length for the trend filter
    ma_ob: float = 70.0              # RSI level to scale a long out at
    ma_os: float = 30.0              # RSI level to scale a short out at
    ma_swing: int = 10               # swing lookback for the protective stop
    ma_sl_buf: float = 0.10          # extra % beyond the swing for the stop
    ma_scale: float = 0.5            # fraction closed on the RSI-extreme scale-out
    ma_confirm: int = 1              # consecutive green(buy)/red(sell) candles to confirm
    ma_min_body: float = 0.30        # each confirming candle's body must be >= this of range
    ma_trend_len: int = 0            # trend-filter EMA (HTF-style); 0 = off
    ma_atr_len: int = 14             # ATR length for the optional ATR stop
    ma_atr_mult: float = 0.0         # ATR stop multiple; 0 = use the swing low/high stop


# ---------------------------------------------------------------------------
# Indicator helpers (pure series math; None during warmup where Pine is `na`).
# ---------------------------------------------------------------------------
def rsi_series(closes: List[float], length: int) -> List[Optional[float]]:
    """Wilder's RSI (matches Pine ``ta.rsi``), seeded with an SMA of the first
    ``length`` changes then smoothed with alpha = 1/length."""
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if length <= 0 or n <= length:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        change = closes[i] - closes[i - 1]
        gains[i] = change if change > 0 else 0.0
        losses[i] = -change if change < 0 else 0.0

    def rsi_val(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        if ag == 0:
            return 0.0
        return 100.0 - 100.0 / (1.0 + ag / al)

    avg_gain = sum(gains[1:length + 1]) / length
    avg_loss = sum(losses[1:length + 1]) / length
    out[length] = rsi_val(avg_gain, avg_loss)
    for i in range(length + 1, n):
        avg_gain = (avg_gain * (length - 1) + gains[i]) / length
        avg_loss = (avg_loss * (length - 1) + losses[i]) / length
        out[i] = rsi_val(avg_gain, avg_loss)
    return out


def sma_series(values: List[float], length: int) -> List[Optional[float]]:
    """Simple moving average (matches Pine ``ta.sma``); None until ``length``
    values are available."""
    n = len(values)
    out: List[Optional[float]] = [None] * n
    if length <= 0:
        return out
    running = 0.0
    for i in range(n):
        running += values[i]
        if i >= length:
            running -= values[i - length]
        if i >= length - 1:
            out[i] = running / length
    return out


def lowest_series(lows: List[float], length: int) -> List[float]:
    """Lowest low over the trailing ``length`` bars, inclusive of the current
    bar (matches Pine ``ta.lowest``). Before ``length`` bars exist it falls back
    to the lowest of what's available — those are warmup bars the strategy never
    trades on, so it keeps the state machine free of None handling."""
    n = len(lows)
    out: List[float] = [0.0] * n
    for i in range(n):
        start = max(0, i - length + 1)
        out[i] = min(lows[start:i + 1])
    return out


def ema_series(values: List[float], length: int) -> List[Optional[float]]:
    """Exponential moving average (matches Pine ``ta.ema``): SMA-seeded over the
    first ``length`` values, then smoothed with alpha = 2/(length+1). ``None``
    until ``length`` values exist."""
    n = len(values)
    out: List[Optional[float]] = [None] * n
    if length <= 0 or n < length:
        return out
    seed = sum(values[:length]) / length
    out[length - 1] = seed
    k = 2.0 / (length + 1.0)
    prev = seed
    for i in range(length, n):
        prev = (values[i] - prev) * k + prev
        out[i] = prev
    return out


def atr_series(highs: List[float], lows: List[float], closes: List[float],
               length: int) -> List[Optional[float]]:
    """Average True Range (Wilder's smoothing). The first bar's true range is
    ``high - low`` (no prior close), then alpha = 1/length smoothing."""
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if length <= 0 or n < length:
        return out
    tr = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        pc = closes[i - 1]
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc))
    seed = sum(tr[0:length]) / length
    out[length - 1] = seed
    prev = seed
    for i in range(length, n):
        prev = (prev * (length - 1) + tr[i]) / length
        out[i] = prev
    return out


def highest_series(highs: List[float], length: int) -> List[float]:
    """Highest high over the trailing ``length`` bars, inclusive of the current
    bar (mirror of :func:`lowest_series`, used for short-side protective stops)."""
    n = len(highs)
    out: List[float] = [0.0] * n
    for i in range(n):
        start = max(0, i - length + 1)
        out[i] = max(highs[start:i + 1])
    return out


# ---------------------------------------------------------------------------
# Strategy B — "MA": EMA20 + RSI-50 crossover (long + short, dynamic exits).
# ---------------------------------------------------------------------------
def crossover_need(params: StrategyParams) -> int:
    """Minimum candles before the crossover engine can produce a signal."""
    extra = [params.ma_len, params.rsi_len, params.ma_swing]
    if params.ma_trend_len > 0:
        extra.append(params.ma_trend_len)
    if params.ma_atr_mult > 0:
        extra.append(params.ma_atr_len)
    return max(extra) + 2


def crossover_arrays(candles, params: StrategyParams):
    """Precompute every series the crossover engine/backtest need (shared, so the
    live engine and the backtester stay byte-for-byte identical)."""
    o = [float(c[1]) for c in candles]
    h = [float(c[2]) for c in candles]
    low = [float(c[3]) for c in candles]
    cl = [float(c[4]) for c in candles]
    ema = ema_series(cl, params.ma_len)
    rsi = rsi_series(cl, params.rsi_len)
    swing_low = lowest_series(low, params.ma_swing)
    swing_high = highest_series(h, params.ma_swing)
    trend = ema_series(cl, params.ma_trend_len) if params.ma_trend_len > 0 else None
    atr = atr_series(h, low, cl, params.ma_atr_len) if params.ma_atr_mult > 0 else None
    return o, h, low, cl, ema, rsi, swing_low, swing_high, trend, atr


def trend_ok(side: str, i: int, cl, trend, params: StrategyParams) -> bool:
    """Higher-timeframe-style filter: only allow entries aligned with the trend
    EMA (when enabled). During the trend EMA's warmup, no entry is allowed."""
    if params.ma_trend_len <= 0:
        return True
    tv = trend[i] if trend is not None else None
    if tv is None:
        return False
    return cl[i] > tv if side == "long" else cl[i] < tv


def crossover_stop(side: str, i: int, cl, swing_low, swing_high, atr,
                   params: StrategyParams) -> float:
    """Protective stop: ATR-based (entry ∓ ATR×mult) when enabled, else the
    swing low/high ± buffer."""
    if params.ma_atr_mult > 0 and atr is not None and atr[i] is not None:
        d = atr[i] * params.ma_atr_mult
        return cl[i] - d if side == "long" else cl[i] + d
    buf = params.ma_sl_buf / 100.0
    return swing_low[i] * (1.0 - buf) if side == "long" else swing_high[i] * (1.0 + buf)


def _replay_crossover(candles, params: StrategyParams, ticker: str = ""):
    """Replay the EMA20 + RSI-50 crossover state machine over CLOSED candles.

    Returns a list of ``(bar_index, event)`` instructions, where ``event`` is one
    of:
      * ``{"act":"enter","side":"long"|"short","entry":float,"sl":float,"ts":int}``
      * ``{"act":"scale_out","fraction":float,"side":...}``  (RSI hit 70/30)
      * ``{"act":"exit"}``                                   (close back across the
        EMA, or the swing stop was breached)

    Pure & deterministic: it simulates one position at a time, so a reversal
    (long→short on the same bar) emits an ``exit`` immediately followed by an
    ``enter``. A long needs the EMA/RSI alignment **plus** ``ma_confirm``
    consecutive green candles to confirm the BUY (a short needs that many red
    candles for the SELL) — mirroring Strategy A's green-sequence filter and
    keeping it non-repaint (only closed bars).
    """
    n = len(candles)
    if n < crossover_need(params):
        return []

    ts = [int(c[0]) for c in candles]
    o, h, low, cl, ema, rsi, swing_low, swing_high, trend, atr = crossover_arrays(candles, params)
    confirm = max(0, int(params.ma_confirm))   # in-direction candles to confirm an entry
    min_body = max(0.0, params.ma_min_body)    # each must have a body >= this of its range

    pos = "flat"            # flat / long / short
    scaled = False          # has the one-shot RSI scale-out fired for this position?
    sl: Optional[float] = None
    prev_ready = False
    green_run = 0           # consecutive *quality* green / red candles (entry confirmation)
    red_run = 0
    events = []

    for i in range(n):
        # Confirmation counters: a candle only counts if it's the right colour AND
        # has a strong enough body (|close-open| / range >= min_body), mirroring
        # Strategy A's body filter. Any other candle resets the run.
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
        # Entry needs alignment AND `confirm` candles in the trade direction AND
        # (when enabled) agreement with the higher-timeframe-style trend filter.
        enter_long = (prev_ready and long_aligned and green_run >= confirm
                      and trend_ok("long", i, cl, trend, params))
        enter_short = (prev_ready and short_aligned and red_run >= confirm
                       and trend_ok("short", i, cl, trend, params))

        # 1) Manage an open position first — exits/stops take priority.
        if pos == "long":
            if (sl is not None and low[i] <= sl) or cl[i] < ema[i] or enter_short:
                events.append((i, {"act": "exit"}))
                pos, scaled, sl = "flat", False, None
            elif not scaled and rsi[i] >= params.ma_ob:
                events.append((i, {"act": "scale_out", "fraction": params.ma_scale,
                                   "side": "long"}))
                scaled = True
        elif pos == "short":
            if (sl is not None and h[i] >= sl) or cl[i] > ema[i] or enter_long:
                events.append((i, {"act": "exit"}))
                pos, scaled, sl = "flat", False, None
            elif not scaled and rsi[i] <= params.ma_os:
                events.append((i, {"act": "scale_out", "fraction": params.ma_scale,
                                   "side": "short"}))
                scaled = True

        # 2) Enter when flat (after a same-bar exit this is the reversal leg).
        if pos == "flat":
            if enter_long:
                sl = crossover_stop("long", i, cl, swing_low, swing_high, atr, params)
                events.append((i, {"act": "enter", "side": "long",
                                   "entry": cl[i], "sl": sl, "ts": ts[i]}))
                pos, scaled = "long", False
            elif enter_short:
                sl = crossover_stop("short", i, cl, swing_low, swing_high, atr, params)
                events.append((i, {"act": "enter", "side": "short",
                                   "entry": cl[i], "sl": sl, "ts": ts[i]}))
                pos, scaled = "short", False

        prev_ready = True

    return events


def evaluate_crossover(candles, params: StrategyParams, ticker: str = ""):
    """Instructions for the NEWEST closed candle only (runner contract).

    Returns a list (possibly empty; two entries on a reversal) of the event
    dicts described in :func:`_replay_crossover`."""
    last = len(candles) - 1
    return [ev for (i, ev) in _replay_crossover(candles, params, ticker) if i == last]


def evaluate_all_crossover(candles, params: StrategyParams, ticker: str = ""):
    """Every crossover instruction over the whole candle list (for charting)."""
    return _replay_crossover(candles, params, ticker)


def entry_block_reason(candles, params: StrategyParams) -> str:
    """If the NEWEST closed bar had EMA/RSI alignment + the confirming candles for
    a direction but a *filter* vetoed the entry, return a short human reason;
    else "". Lets the runner explain why an apparently-valid setup didn't fire."""
    n = len(candles)
    if n < crossover_need(params):
        return ""
    o, h, low, cl, ema, rsi, _sl, _sh, trend, _atr = crossover_arrays(candles, params)
    i = n - 1
    if ema[i] is None or rsi[i] is None:
        return ""
    confirm = max(0, int(params.ma_confirm))
    min_body = max(0.0, params.ma_min_body)
    green = red = 0
    for j in range(n):
        rng = h[j] - low[j]
        body = abs(cl[j] - o[j]) / rng if rng > 0 else 0.0
        strong = body >= min_body
        if cl[j] > o[j] and strong:
            green, red = green + 1, 0
        elif cl[j] < o[j] and strong:
            green, red = 0, red + 1
        else:
            green, red = 0, 0
    long_aligned = cl[i] > ema[i] and rsi[i] > 50.0
    short_aligned = cl[i] < ema[i] and rsi[i] < 50.0
    for side, aligned, run in (("long", long_aligned, green), ("short", short_aligned, red)):
        if aligned and run >= confirm and not trend_ok(side, i, cl, trend, params):
            tv = trend[i] if trend is not None else None
            verb = "BUY" if side == "long" else "SELL"
            rel = "below" if side == "long" else "above"
            if tv is None:
                return f"{verb} held: trend EMA{params.ma_trend_len} still warming up"
            return (f"{verb} held by trend filter — price {cl[i]:g} {rel} "
                    f"EMA{params.ma_trend_len} {tv:g}")
    return ""
