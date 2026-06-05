"""Built-in strategy engine — a faithful Python port of the Pine indicator.

This is the *same* dip→green-sequence logic shipped in
``Prometheus_AI_Crypto_Bot.pine``, reimplemented as a pure function over OHLCV
candles so the Windows bot can generate its own entries without TradingView.

Design notes
------------
* **Pure & deterministic.** ``evaluate`` takes a list of *closed* candles plus
  the strategy parameters and replays the whole state machine from scratch,
  returning a signal only if the *newest* closed candle fires. No hidden state,
  no I/O — trivially unit-testable against hand-built fixtures (see the tests).
* **Non-repaint.** The caller passes only confirmed/closed candles (the live,
  still-forming candle is dropped upstream), mirroring Pine's
  ``barstate.isconfirmed`` / "Bar Close" alert mode.
* **Parity.** RSI/ATR use Wilder's smoothing (Pine ``ta.rma``); SMA, lowest and
  the body-ratio / volume / range filters match the Pine formulas line for
  line. The only source of divergence from a TradingView chart is the candle
  data itself (exchange OHLCV vs TradingView's aggregation), which is why the
  runner pulls candles from the same exchange the user trades on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class StrategyParams:
    """All user-tunable inputs, mirroring the Pine indicator's inputs."""

    preset: str = "Auto"             # Auto / BTC / ETH / Crypto / Custom
    rsi_len: int = 14
    atr_len: int = 14
    vol_len: int = 20
    # Custom-mode overrides (used only when preset == "Custom").
    cust_os: int = 30                # RSI oversold threshold
    cust_atr: float = 0.9            # range >= ATR * this
    cust_vol: float = 1.0            # volume >= avg * this
    cust_look: int = 10              # "recent low" lookback
    # Green confirmation sequence.
    green_n: int = 2                 # required green candles after the dip
    max_wait: int = 15               # bars to wait for the greens
    cooldown: int = 5                # bars between signals
    # Quality filter on each green candle.
    require_body_ratio: bool = True
    min_body_ratio: float = 0.3      # |close-open| / range must be >= this
    # Stop-loss (below recent liquidity).
    use_sl: bool = True
    sl_look: int = 10                # lowest-low lookback for the SL
    sl_buf: float = 0.10             # extra % below the liquidity low
    # Targets (multiples of the dip range, floored by ATR).
    rr_tp1: float = 1.0
    rr_tp2: float = 3.0
    # --- Strategy B ("MA"): EMA20 + RSI-50 crossover (long+short, dynamic exits).
    ma_len: int = 20                 # EMA length for the trend filter
    ma_ob: float = 70.0              # RSI level to scale a long out at
    ma_os: float = 30.0              # RSI level to scale a short out at
    ma_swing: int = 10               # swing lookback for the protective stop
    ma_sl_buf: float = 0.10          # extra % beyond the swing for the stop
    ma_scale: float = 0.5            # fraction closed on the RSI-extreme scale-out
    ma_confirm: int = 1              # consecutive green(buy)/red(sell) candles to confirm
    ma_min_body: float = 0.30        # each confirming candle's body must be >= this of range


@dataclass
class StrategySignal:
    """A confirmed BUY produced by the engine on a specific closed candle."""

    ts: int                          # candle open-time (ms) of the signal bar
    entry: float
    tp1: float
    tp2: float
    rsi: float
    sl: Optional[float] = None
    index: int = -1                  # bar index within the evaluated candle list
    side: str = "long"               # "long" / "short" (Strategy B can short)


@dataclass
class SignalOutcome:
    """What happened to a signal after it fired — for charting / review.

    Mirrors the Pine indicator's signal-tracking loop: scan forward from the
    entry bar and record where price first reached TP1 / TP2 / SL.
    ``status`` is ``open`` / ``win`` (TP2) / ``loss`` (SL, no TP1) /
    ``part`` (SL or window after TP1) / ``expired`` (window, no TP1)."""

    signal: StrategySignal
    status: str = "open"
    tp1_index: Optional[int] = None   # bar index where TP1 was first reached
    tp2_index: Optional[int] = None
    sl_index: Optional[int] = None


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


def atr_series(highs: List[float], lows: List[float], closes: List[float],
               length: int) -> List[Optional[float]]:
    """Average True Range via Wilder's smoothing (matches Pine ``ta.atr``).

    The first bar's true range is ``high - low`` (no prior close), exactly as
    Pine's ``ta.tr`` defines it."""
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if length <= 0 or n < length:
        return out
    tr = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        prev_close = closes[i - 1]
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - prev_close),
                    abs(lows[i] - prev_close))
    seed = sum(tr[0:length]) / length
    out[length - 1] = seed
    prev = seed
    for i in range(length, n):
        prev = (prev * (length - 1) + tr[i]) / length
        out[i] = prev
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


def highest_series(highs: List[float], length: int) -> List[float]:
    """Highest high over the trailing ``length`` bars, inclusive of the current
    bar (mirror of :func:`lowest_series`, used for short-side protective stops)."""
    n = len(highs)
    out: List[float] = [0.0] * n
    for i in range(n):
        start = max(0, i - length + 1)
        out[i] = max(highs[start:i + 1])
    return out


def effective_settings(preset: str, ticker: str, params: StrategyParams):
    """Resolve the active preset into concrete thresholds (Pine lines 19-41)."""
    t = (ticker or "").upper()
    is_btc = "BTC" in t or "XBT" in t
    is_eth = "ETH" in t
    auto = "BTC" if is_btc else "ETH" if is_eth else "Crypto"
    eff = auto if preset == "Auto" else preset
    if eff == "Custom":
        return eff, params.cust_os, params.cust_atr, params.cust_vol, params.cust_look
    os_ = 30
    atr_mult = 0.8 if eff == "BTC" else 0.9 if eff == "ETH" else 1.0
    return eff, os_, atr_mult, 1.0, 8


def evaluate(candles, params: StrategyParams, ticker: str = "") -> Optional[StrategySignal]:
    """Replay the dip→green-sequence state machine over CLOSED candles.

    ``candles`` is ``[[ts, open, high, low, close, volume], ...]`` oldest-first,
    all confirmed/closed (the live forming candle must be dropped by the
    caller). Returns a :class:`StrategySignal` if — and only if — the *newest*
    closed candle is a confirmed BUY, else ``None``.
    """
    _dips, signals = _replay(candles, params, ticker)
    if signals and signals[-1].index == len(candles) - 1:
        return signals[-1]
    return None


def evaluate_all(candles, params: StrategyParams, ticker: str = ""):
    """Replay the engine and return *every* annotation for charting.

    Returns ``(dips, signals)`` where ``dips`` is a list of bar indices flagged
    as dip bars (yellow markers) and ``signals`` is a list of
    :class:`StrategySignal` (lime BUY arrows + entry/TP/SL levels) — the same
    visuals the Pine indicator draws, computed by the same logic the bot trades.
    """
    return _replay(candles, params, ticker)


def track_outcomes(candles, signals, track_window: int = 120):
    """Resolve each signal's fate by scanning forward (Pine lines 234-265).

    Returns a list of :class:`SignalOutcome`, one per signal, recording the bar
    where TP1 / TP2 / SL were first reached and the resulting status. Pure and
    deterministic — TP2 takes priority over SL on the same bar, exactly as the
    indicator marks it.
    """
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    n = len(candles)
    outcomes = []
    for sig in signals:
        oc = SignalOutcome(signal=sig)
        tp1_hit = False
        tp2_hit = False
        for j in range(sig.index + 1, n):
            bars_in = j - sig.index
            hit_tp2 = highs[j] >= sig.tp2
            hit_tp1 = highs[j] >= sig.tp1
            hit_sl = sig.sl is not None and lows[j] <= sig.sl
            if hit_tp2 and not tp2_hit:
                tp2_hit = True
                tp1_hit = True
                oc.tp2_index = j
                if oc.tp1_index is None:
                    oc.tp1_index = j
            elif hit_tp1 and not tp1_hit:
                tp1_hit = True
                oc.tp1_index = j

            if tp2_hit:
                oc.status = "win"
                break
            if hit_sl:
                oc.status = "part" if tp1_hit else "loss"
                oc.sl_index = j
                break
            if bars_in >= track_window:
                oc.status = "part" if tp1_hit else "expired"
                break
        outcomes.append(oc)
    return outcomes


def _replay(candles, params: StrategyParams, ticker: str = ""):
    """Core state-machine replay shared by ``evaluate`` / ``evaluate_all``.

    Returns ``(dip_indices, signals)`` over the full candle list. Pure and
    deterministic — see the module docstring for the parity notes.
    """
    n = len(candles)
    need = max(params.rsi_len, params.atr_len, params.vol_len) + 2
    if n < need:
        return [], []

    ts = [int(c[0]) for c in candles]
    o = [float(c[1]) for c in candles]
    h = [float(c[2]) for c in candles]
    low = [float(c[3]) for c in candles]
    cl = [float(c[4]) for c in candles]
    vol = [float(c[5]) for c in candles]

    eff, eff_os, eff_atr, eff_vol, eff_look = effective_settings(params.preset, ticker, params)

    rsi = rsi_series(cl, params.rsi_len)
    atr = atr_series(h, low, cl, params.atr_len)
    vol_avg = sma_series(vol, params.vol_len)
    lowest_n = lowest_series(low, eff_look)
    lowest_five = lowest_series(low, 5)
    lowest_sl = lowest_series(low, params.sl_look)

    # State machine (mirrors Pine lines 176-219).
    dip_active = False
    green_count = 0
    dip_low_val: Optional[float] = None
    bars_since_dip = 0
    last_signal_bar = -1000
    locked_dip_low: Optional[float] = None
    cum_vol = 0.0
    dips: List[int] = []
    signals: List[StrategySignal] = []

    for i in range(n):
        cum_vol += vol[i]
        rng = h[i] - low[i]
        is_red = cl[i] < o[i]
        is_green = cl[i] > o[i]
        body_ratio = abs(cl[i] - o[i]) / rng if rng > 0 else 0.0
        is_quality_green = is_green and (not params.require_body_ratio
                                         or body_ratio >= params.min_body_ratio)

        ready = rsi[i] is not None and atr[i] is not None and vol_avg[i] is not None
        # hasVolume = cum(volume) > 0; volPass = not hasVolume or vol >= avg*mult
        vol_pass = (cum_vol <= 0) or (vol_avg[i] is not None and vol[i] >= vol_avg[i] * eff_vol)
        is_dip_red = (ready and is_red and rsi[i] <= eff_os
                      and rng >= atr[i] * eff_atr
                      and low[i] <= lowest_n[i] and vol_pass)

        if is_dip_red:
            dips.append(i)
            locked_dip_low = low[i]            # Pine's separate lockedDipLow block
            dip_active = True
            green_count = 0
            dip_low_val = low[i]
            bars_since_dip = 0
        elif dip_active:
            bars_since_dip += 1
            if bars_since_dip > params.max_wait:
                dip_active = False
                green_count = 0
            elif is_quality_green:
                green_count += 1
            else:
                green_count = 0
                if dip_low_val is not None and low[i] < dip_low_val:
                    dip_active = False

        in_cooldown = (i - last_signal_bar) < params.cooldown
        raw_signal = dip_active and is_quality_green and green_count >= params.green_n
        signal = raw_signal and not in_cooldown   # candles are confirmed (closed)

        if signal:
            dip_active = False
            green_count = 0
            last_signal_bar = i
            entry = cl[i]
            dip_ref = locked_dip_low if locked_dip_low is not None else lowest_five[i]
            tp_range = max(entry - dip_ref, atr[i])
            tp1 = entry + tp_range * params.rr_tp1
            tp2 = entry + tp_range * params.rr_tp2
            sl = None
            if params.use_sl:
                liq_low = min(lowest_sl[i], dip_ref)
                sl = liq_low * (1.0 - params.sl_buf / 100.0)
            signals.append(StrategySignal(ts=ts[i], entry=entry, tp1=tp1, tp2=tp2,
                                          rsi=float(rsi[i]), sl=sl, index=i))

    return dips, signals


# ---------------------------------------------------------------------------
# Strategy B — "MA": EMA20 + RSI-50 crossover (long + short, dynamic exits).
# ---------------------------------------------------------------------------
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
    need = max(params.ma_len, params.rsi_len, params.ma_swing) + 2
    if n < need:
        return []

    ts = [int(c[0]) for c in candles]
    o = [float(c[1]) for c in candles]
    h = [float(c[2]) for c in candles]
    low = [float(c[3]) for c in candles]
    cl = [float(c[4]) for c in candles]

    ema = ema_series(cl, params.ma_len)
    rsi = rsi_series(cl, params.rsi_len)
    swing_low = lowest_series(low, params.ma_swing)
    swing_high = highest_series(h, params.ma_swing)
    buf = params.ma_sl_buf / 100.0
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
        # Entry needs alignment AND `confirm` candles in the trade direction.
        enter_long = prev_ready and long_aligned and green_run >= confirm
        enter_short = prev_ready and short_aligned and red_run >= confirm

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
                sl = swing_low[i] * (1.0 - buf)
                events.append((i, {"act": "enter", "side": "long",
                                   "entry": cl[i], "sl": sl, "ts": ts[i]}))
                pos, scaled = "long", False
            elif enter_short:
                sl = swing_high[i] * (1.0 + buf)
                events.append((i, {"act": "enter", "side": "short",
                                   "entry": cl[i], "sl": sl, "ts": ts[i]}))
                pos, scaled = "short", False

        prev_ready, prev_long, prev_short = True, long_aligned, short_aligned

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
