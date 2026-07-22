"""Built-in strategy engine — H1 9/21 EMA crossover with a 100-EMA trend filter.

Production port of the spec:

  * **Trend filter** — the candle must CLOSE above the EMA100 for longs / close
    below for shorts. A "trend re-arm" also enters when the EMAs are already
    aligned and price reclaims the EMA100 (so a cross during a pullback is not
    missed once price clears the trend).
  * **Trigger** — EMA9 crosses EMA21 on a *closed* candle; entry is taken at the
    OPEN of the next candle (no intra-candle execution).
  * **Stop** — long: the lower of {EMA21 − 0.2%} and the recent swing low;
    short: the higher of {EMA21 + 0.2%} and the recent swing high.
  * **Target** — a 1:2 R:R partial: close ``partial_pct`` (50%) at 2R.
  * **Trail** — after the partial, trail the remainder on the EMA21 (ratchet),
    and close everything on a counter-cross (EMA9 back across EMA21) at close.
  * **Guardrails** — whipsaw filter (>N crosses in a window suspends new entries
    for M hours) and a daily-close time filter (no new entries 23:30–00:30 UTC).

Pure & deterministic over CLOSED candles (non-repaint). ``_replay_crossover`` is
the single source of truth: it simulates the whole lifecycle over OHLCV and emits
``enter`` / ``partial`` / ``exit`` events, which both the backtester and the live
runner consume.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class StrategyParams:
    fast_ema: int = 9              # Fast EMA
    slow_ema: int = 21            # Slow EMA
    trend_ema: int = 100          # Master trend filter EMA
    use_trend_filter: bool = True  # require price completely above/below EMA100
    confirm: int = 2              # confirming candles required after the cross (0 = off)
    min_body: float = 0.4        # min body-to-range ratio of a confirming candle
    sl_ema_buffer_pct: float = 0.2  # stop 0.2% beyond the EMA21
    swing_lookback: int = 10      # bars for the recent swing low/high
    tp_r: float = 1.0             # partial take-profit R multiple
    partial_pct: float = 0.5      # fraction closed at the target (50%)
    whipsaw_max_crosses: int = 2  # > this many crosses in the window suspends entries
    whipsaw_window: int = 5       # candles in the whipsaw window
    whipsaw_suspend_hours: float = 12.0
    avoid_daily_close: bool = True  # no new entries 23:30–00:30 UTC
    post_sl_cooldown_bars: int = 0  # after a stop-out, block new entries for N closed candles (0 = off)


# ---------------------------------------------------------------------------
# Series helpers
# ---------------------------------------------------------------------------
def ema_series(values: List[float], length: int) -> List[Optional[float]]:
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


def crossover_need(params: StrategyParams) -> int:
    return params.trend_ema + 3


def crossover_arrays(candles, params: StrategyParams):
    """OHLC + the three EMAs (fast/slow/trend). Shared by engine, chart, backtest."""
    o = [float(c[1]) for c in candles]
    h = [float(c[2]) for c in candles]
    low = [float(c[3]) for c in candles]
    cl = [float(c[4]) for c in candles]
    fast = ema_series(cl, params.fast_ema)
    slow = ema_series(cl, params.slow_ema)
    trend = ema_series(cl, params.trend_ema)
    return o, h, low, cl, fast, slow, trend


def _in_daily_close_window(ts_ms: int, params: StrategyParams) -> bool:
    if not params.avoid_daily_close:
        return False
    minutes = int(ts_ms // 60000) % 1440
    return minutes >= 23 * 60 + 30 or minutes < 30   # 23:30 .. 00:30 UTC


# ---------------------------------------------------------------------------
# Full lifecycle simulation
# ---------------------------------------------------------------------------
def _replay_crossover(candles, params: StrategyParams, ticker: str = ""):
    """Simulate the strategy over CLOSED candles. Returns ``[(i, event), ...]``:
      * ``{"act":"enter","side","entry","sl","tp","ts"}``   (filled at bar open)
      * ``{"act":"partial","fraction","price","ts"}``       (2R hit, 50% out)
      * ``{"act":"exit","side","reason","price","ts"}``     (sl/trail/counter_cross)
    """
    n = len(candles)
    if n < params.trend_ema + 3:
        return []
    ts = [int(c[0]) for c in candles]
    o, h, low, cl, fast, slow, trend = crossover_arrays(candles, params)
    buf = max(0.0, params.sl_ema_buffer_pct) / 100.0
    look = max(1, int(params.swing_lookback))
    confirm = max(0, int(params.confirm))
    min_body = max(0.0, float(params.min_body))

    events: list = []
    pos: Optional[dict] = None       # open position
    pending: Optional[dict] = None   # {"side","cross_i"} awaiting next-open fill
    cross_bars: list = []            # indices where EMA9/21 crossed
    suspend_until = 0                # ms; whipsaw suspension of new entries
    sl_cd = max(0, int(getattr(params, "post_sl_cooldown_bars", 0) or 0))
    sl_cooldown_until = -1           # bar index; no new entries at/before this bar after a stop-out

    for i in range(1, n):
        if None in (fast[i], slow[i], trend[i], fast[i - 1], slow[i - 1]):
            continue
        cu = fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]
        cd = fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]
        if cu or cd:
            cross_bars.append(i)

        # 1) fill a CONFIRMED pending entry at THIS bar's open
        if pending is not None and pending.get("armed") and pos is None:
            side = pending["side"]; ci = pending["cross_i"]
            entry = o[i]
            if side == "long":
                ema_stop = slow[ci] * (1 - buf)
                swing = min(low[max(0, ci - look):ci + 1])
                sl = min(ema_stop, swing)
                risk = entry - sl
                tp = entry + params.tp_r * risk
            else:
                ema_stop = slow[ci] * (1 + buf)
                swing = max(h[max(0, ci - look):ci + 1])
                sl = max(ema_stop, swing)
                risk = sl - entry
                tp = entry - params.tp_r * risk
            if risk > 0:
                pos = {"side": side, "entry": entry, "sl": sl, "tp": tp,
                       "scaled": False, "trail": None}
                events.append((i, {"act": "enter", "side": side, "entry": entry,
                                   "sl": sl, "tp": tp, "ts": ts[i]}))
            pending = None

        # 2) manage the open position against THIS bar
        if pos is not None:
            side = pos["side"]
            stop = pos["trail"] if pos["trail"] is not None else pos["sl"]
            reason = "trail" if pos["trail"] is not None else "sl"
            if side == "long":
                if low[i] <= stop:
                    events.append((i, {"act": "exit", "side": side, "reason": reason,
                                       "price": stop, "ts": ts[i]})); pos = None
                    sl_cooldown_until = i + sl_cd     # stop-out -> cooldown before re-entry
                else:
                    if not pos["scaled"] and h[i] >= pos["tp"]:
                        events.append((i, {"act": "partial", "fraction": params.partial_pct,
                                           "price": pos["tp"], "ts": ts[i]}))
                        pos["scaled"] = True
                        pos["trail"] = slow[i]
                    if pos is not None and cd:            # counter-cross closes the rest
                        events.append((i, {"act": "exit", "side": side, "reason": "counter_cross",
                                           "price": cl[i], "ts": ts[i]})); pos = None
                    if pos is not None and pos["scaled"] and slow[i] is not None:
                        pos["trail"] = max(pos["trail"], slow[i])
            else:
                if h[i] >= stop:
                    events.append((i, {"act": "exit", "side": side, "reason": reason,
                                       "price": stop, "ts": ts[i]})); pos = None
                    sl_cooldown_until = i + sl_cd     # stop-out -> cooldown before re-entry
                else:
                    if not pos["scaled"] and low[i] <= pos["tp"]:
                        events.append((i, {"act": "partial", "fraction": params.partial_pct,
                                           "price": pos["tp"], "ts": ts[i]}))
                        pos["scaled"] = True
                        pos["trail"] = slow[i]
                    if pos is not None and cu:
                        events.append((i, {"act": "exit", "side": side, "reason": "counter_cross",
                                           "price": cl[i], "ts": ts[i]})); pos = None
                    if pos is not None and pos["scaled"] and slow[i] is not None:
                        pos["trail"] = min(pos["trail"], slow[i])

        # 2b) advance the confirmation window (N green/red candles with a strong body)
        if pending is not None and not pending.get("armed") and pos is None:
            side = pending["side"]
            regime = fast[i] > slow[i] if side == "long" else fast[i] < slow[i]
            if not regime:                      # the cross flipped back before confirming
                pending = None
            else:
                rng = h[i] - low[i]
                body = abs(cl[i] - o[i]) / rng if rng > 0 else 0.0
                dir_ok = (cl[i] > o[i]) if side == "long" else (cl[i] < o[i])
                if dir_ok and body >= min_body:
                    pending["count"] += 1
                    if pending["count"] >= confirm:
                        pending["armed"] = True
                else:
                    pending["count"] = 0        # confirming candles must be consecutive

        # 3) detect a fresh signal at THIS close -> start a pending (confirm then fill)
        if pos is None and pending is None:
            now = ts[i]
            recent = [x for x in cross_bars if x > i - params.whipsaw_window]
            if len(recent) > params.whipsaw_max_crosses:
                suspend_until = now + int(params.whipsaw_suspend_hours * 3600 * 1000)
            elif now >= suspend_until and i > sl_cooldown_until and not _in_daily_close_window(now, params):
                above = (not params.use_trend_filter) or cl[i] > trend[i]
                below = (not params.use_trend_filter) or cl[i] < trend[i]
                # trend re-arm: EMAs already aligned and price just reclaimed the trend EMA
                # (catches entries where the EMA cross happened during a pullback and price
                #  only cleared the EMA100 a few candles later).
                tf = params.use_trend_filter and trend[i - 1] is not None
                reclaim_up = tf and cl[i - 1] <= trend[i - 1] and cl[i] > trend[i]
                reclaim_dn = tf and cl[i - 1] >= trend[i - 1] and cl[i] < trend[i]
                if cu and above:
                    pending = {"side": "long", "cross_i": i, "count": 0, "armed": confirm == 0}
                elif reclaim_up and fast[i] > slow[i]:
                    pending = {"side": "long", "cross_i": i, "count": 0, "armed": confirm == 0}
                elif reclaim_dn and fast[i] < slow[i]:
                    pending = {"side": "short", "cross_i": i, "count": 0, "armed": confirm == 0}
                elif cd and below:
                    pending = {"side": "short", "cross_i": i, "count": 0, "armed": confirm == 0}
    return events


def evaluate_crossover(candles, params: StrategyParams, ticker: str = ""):
    """Events on the NEWEST closed candle only (the live runner acts on these)."""
    last = len(candles) - 1
    return [ev for (i, ev) in _replay_crossover(candles, params, ticker) if i == last]


def evaluate_all_crossover(candles, params: StrategyParams, ticker: str = ""):
    return _replay_crossover(candles, params, ticker)


def entry_block_reason(candles, params: StrategyParams) -> str:
    n = len(candles)
    if n < crossover_need(params):
        return "warming up"
    o, h, low, cl, fast, slow, trend = crossover_arrays(candles, params)
    i = n - 1
    if fast[i] is None or slow[i] is None or trend[i] is None:
        return "EMAs warming up"
    if params.use_trend_filter:
        if fast[i] > slow[i] and cl[i] <= trend[i]:
            return f"BUY held — price still below EMA{params.trend_ema} (trend filter)"
        if fast[i] < slow[i] and cl[i] >= trend[i]:
            return f"SELL held — price still above EMA{params.trend_ema} (trend filter)"
    return ""


def signal_strength(candles, params: StrategyParams) -> dict:
    """A 0-100 'how close is the strategy to firing an entry' meter with a
    plain-English state, plus the likely side (long=BUY / short=SELL).
    Rises as the EMAs converge -> cross -> confirm -> ready to enter."""
    n = len(candles)
    if n < crossover_need(params):
        return {"pct": 0, "state": "Warming up", "side": ""}
    o, h, low, cl, fast, slow, trend = crossover_arrays(candles, params)
    i = n - 1
    if fast[i] is None or slow[i] is None or trend[i] is None:
        return {"pct": 0, "state": "Warming up", "side": ""}
    up = fast[i] >= slow[i]
    side = "long" if up else "short"
    tf_ok = (not params.use_trend_filter) or (cl[i] > trend[i] if up else cl[i] < trend[i])
    confirm = max(0, int(params.confirm))
    look = max(4, int(params.whipsaw_window) + 2)
    rng = sum((h[k] - low[k]) for k in range(max(0, i - look), i + 1)) / (min(look, i) + 1) or 1.0

    # most recent SAME-direction EMA9/21 cross within the lookback (None if none)
    cross_age = None
    for j in range(i, max(1, i - look), -1):
        if None in (fast[j], slow[j], fast[j - 1], slow[j - 1]):
            continue
        cu = fast[j - 1] <= slow[j - 1] and fast[j] > slow[j]
        cd = fast[j - 1] >= slow[j - 1] and fast[j] < slow[j]
        if (up and cu) or (not up and cd):
            cross_age = i - j; break
        if (up and cd) or (not up and cu):
            break  # opposite cross came first -> not a fresh same-side setup

    if cross_age is not None:
        if not tf_ok:
            return {"pct": 55, "state": "Cross fired — awaiting trend confirmation", "side": side}
        cc = 0
        for k in range(i - cross_age, i + 1):
            r = (h[k] - low[k]) or 1.0
            body_ok = (abs(cl[k] - o[k]) / r) >= params.min_body
            dir_ok = (cl[k] > o[k]) if up else (cl[k] < o[k])
            cc = cc + 1 if (body_ok and dir_ok) else 0
        prog = 1.0 if confirm == 0 else min(1.0, cc / confirm)
        pct = int(round(72 + 28 * prog))
        state = "Signal ready — entry imminent" if prog >= 1.0 else f"Confirming — {cc}/{confirm} candles"
        return {"pct": pct, "state": state, "side": side}

    # no fresh cross -> score how close the EMAs are to crossing (convergence)
    gap = abs(fast[i] - slow[i])
    gap_prev = abs(fast[i - 1] - slow[i - 1]) if fast[i - 1] is not None else gap
    converging = gap < gap_prev
    prox = max(0.0, 1.0 - min(1.0, gap / (rng * 1.5)))
    base = prox * (0.7 if converging else 0.35) + (0.12 if tf_ok else 0.0)
    pct = int(round(min(62, base * 62)))
    if pct < 12:
        state = "Idle — no cross brewing"
    elif pct < 35:
        state = "Watching — trend developing"
    else:
        state = "Cross brewing — EMAs converging"
    return {"pct": pct, "state": state, "side": side}
