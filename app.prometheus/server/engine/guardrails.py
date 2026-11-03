"""Risk guardrails — the gate every new entry must pass.

Enforces, in order:
  1. **Daily loss limit** — once the day's realized PnL drops to ``-limit`` the
     guard *trips* and blocks all new entries until reset (or the next day).
  2. **Duplicate-alert dedupe** — drop an identical (symbol, side) signal that
     arrives within ``dedupe_seconds`` of the previous one.
  3. **Cooldown** — enforce a minimum gap between trades on the same symbol.
  4. **Max open positions** — block opening a *new* symbol once the cap is hit.

Pure, deterministic logic (time is passed in) so it is fully unit-testable.
Closing/flattening is never gated — only opening risk is.
"""

from __future__ import annotations

import time
from datetime import date


class Guardrails:
    def __init__(self) -> None:
        self.enabled = True
        self.paused = False             # block all NEW entries; exits still flow
        self.max_exposure = 0.0         # cap combined open notional (quote ccy); 0 = off
        self.max_open_positions = 0
        self.daily_loss_limit = 0.0     # absolute quote-ccy limit (USDT)
        self.daily_loss_pct = 0.0       # % of the day's starting equity (overrides the absolute)
        self.daily_profit_limit = 0.0
        self.daily_profit_pct = 0.0     # % of the day's starting equity (overrides the absolute)
        self.cooldown_seconds = 0
        self.dedupe_seconds = 0
        # Extended risk controls (0 = off).
        self.loss_streak_limit = 0      # N consecutive losses -> pause
        self.streak_cooldown = 0        # seconds to pause after a loss streak
        self.start_hour = 0             # trading-hours window (local). 0==0 -> off
        self.end_hour = 0
        self.max_drawdown_pct = 0.0     # halt if equity falls this % from its peak

        self._day: date | None = None
        self._daily_realized = 0.0
        self._last_trade_ts: dict = {}      # symbol -> ts
        self._recent_signals: dict = {}     # (symbol, side) -> ts
        self.tripped = False                # daily loss/profit limit hit
        self.trip_reason = ""               # "loss" or "profit"
        self._loss_streak = 0
        self._streak_until = 0.0
        self._equity = 0.0
        self._peak_equity = 0.0
        self._day_start_equity = 0.0        # equity at the start of the trading day
        self._dd_tripped = False

    # -- config -------------------------------------------------------------
    def configure(self, max_open, daily_loss, cooldown, dedupe, daily_profit=0.0,
                  loss_streak=0, streak_cooldown=0, start_hour=0, end_hour=0,
                  max_drawdown=0.0, daily_loss_pct=0.0, daily_profit_pct=0.0,
                  paused=False, max_exposure=0.0) -> None:
        self.paused = bool(paused)
        self.max_exposure = float(max_exposure or 0.0)
        self.max_open_positions = int(max_open or 0)
        self.daily_loss_limit = float(daily_loss or 0.0)
        self.daily_loss_pct = float(daily_loss_pct or 0.0)
        self.daily_profit_limit = float(daily_profit or 0.0)
        self.daily_profit_pct = float(daily_profit_pct or 0.0)
        self.cooldown_seconds = int(cooldown or 0)
        self.dedupe_seconds = int(dedupe or 0)
        self.loss_streak_limit = int(loss_streak or 0)
        self.streak_cooldown = int(streak_cooldown or 0)
        self.start_hour = int(start_hour or 0)
        self.end_hour = int(end_hour or 0)
        self.max_drawdown_pct = float(max_drawdown or 0.0)

    # -- daily PnL tracking -------------------------------------------------
    def _roll_day(self, now: float) -> None:
        today = date.fromtimestamp(now)
        if self._day != today:
            self._day = today
            self._daily_realized = 0.0
            self.tripped = False
            self.trip_reason = ""
            self._loss_streak = 0
            self._streak_until = 0.0
            self._dd_tripped = False
            self._peak_equity = self._equity   # fresh peak each day
            self._day_start_equity = self._equity   # base for the % daily-loss limit

    def effective_loss_limit(self) -> float:
        """The active daily-loss limit in quote ccy: a % of the day's starting
        equity when ``daily_loss_pct`` is set, else the absolute amount."""
        if self.daily_loss_pct > 0 and self._day_start_equity > 0:
            return self._day_start_equity * self.daily_loss_pct / 100.0
        return self.daily_loss_limit

    def effective_profit_limit(self) -> float:
        """The active daily-profit target in quote ccy: a % of the day's starting
        equity when ``daily_profit_pct`` is set, else the absolute amount."""
        if self.daily_profit_pct > 0 and self._day_start_equity > 0:
            return self._day_start_equity * self.daily_profit_pct / 100.0
        return self.daily_profit_limit

    def record_realized(self, pnl: float, now: float | None = None) -> bool:
        """Add a realized PnL amount; returns True if this *trips* a daily limit.

        Trips on either the loss limit (PnL <= -loss) or the profit target
        (PnL >= +profit). ``trip_reason`` records which one fired. Also tracks
        the consecutive-loss streak for the loss-streak cooldown.
        """
        now = now if now is not None else time.time()
        self._roll_day(now)
        self._daily_realized += pnl
        # consecutive-loss streak
        if self.loss_streak_limit > 0:
            if pnl <= 0:
                self._loss_streak += 1
                if self._loss_streak >= self.loss_streak_limit and self.streak_cooldown > 0:
                    self._streak_until = now + self.streak_cooldown
            else:
                self._loss_streak = 0
        if self.tripped:
            return False
        loss_limit = self.effective_loss_limit()
        if loss_limit > 0 and self._daily_realized <= -loss_limit:
            self.tripped = True
            self.trip_reason = "loss"
            return True
        profit_limit = self.effective_profit_limit()
        if profit_limit > 0 and self._daily_realized >= profit_limit:
            self.tripped = True
            self.trip_reason = "profit"
            return True
        return False

    def update_equity(self, equity: float, now: float | None = None) -> None:
        """Feed the current account equity; trips the drawdown halt if it falls
        ``max_drawdown_pct`` % below the running peak."""
        now = now if now is not None else time.time()
        self._roll_day(now)
        self._equity = equity
        if self._day_start_equity <= 0 and equity > 0:
            self._day_start_equity = equity     # first valid reading seeds the day's base
        if equity > self._peak_equity:
            self._peak_equity = equity
        if self.max_drawdown_pct > 0 and self._peak_equity > 0:
            dd = (self._peak_equity - equity) / self._peak_equity * 100.0
            if dd >= self.max_drawdown_pct:
                self._dd_tripped = True

    @property
    def daily_realized(self) -> float:
        return self._daily_realized

    def reset_daily(self) -> None:
        self._daily_realized = 0.0
        self.tripped = False
        self.trip_reason = ""
        self._loss_streak = 0
        self._streak_until = 0.0
        self._dd_tripped = False
        self._peak_equity = self._equity
        self._day_start_equity = self._equity

    # -- the entry gate -----------------------------------------------------
    def check_entry(self, symbol: str, side: str, open_pairs: set, now: float | None = None):
        """Return ``(allowed: bool, reason: str)`` for opening this trade."""
        now = now if now is not None else time.time()
        self._roll_day(now)
        if not self.enabled:
            return True, "guardrails off"

        if self.paused:
            return False, "paused — new entries off (exits still run)"

        if self.tripped:
            kind = "profit target" if self.trip_reason == "profit" else "loss limit"
            return False, (
                f"daily {kind} hit ({self._daily_realized:+.2f}) — trading halted"
            )

        if self._dd_tripped:
            return False, f"equity drawdown limit ({self.max_drawdown_pct:g}%) hit — trading halted"

        if self.streak_cooldown > 0 and now < self._streak_until:
            return False, f"loss-streak cooldown ({self._loss_streak} losses) — paused"

        if self.start_hour != self.end_hour:   # 0==0 disables the window
            h = time.localtime(now).tm_hour
            inside = (self.start_hour <= h < self.end_hour) if self.start_hour < self.end_hour \
                else (h >= self.start_hour or h < self.end_hour)
            if not inside:
                return False, (f"outside trading hours "
                               f"({self.start_hour:02d}:00-{self.end_hour:02d}:00)")

        key = (symbol, side)
        if self.dedupe_seconds > 0:
            last = self._recent_signals.get(key)
            if last is not None and (now - last) < self.dedupe_seconds:
                return False, f"duplicate signal within {self.dedupe_seconds}s"

        if self.cooldown_seconds > 0:
            last = self._last_trade_ts.get(symbol)
            if last is not None and (now - last) < self.cooldown_seconds:
                return False, f"cooldown active ({self.cooldown_seconds}s) on {symbol}"

        if (
            self.max_open_positions > 0
            and symbol not in open_pairs
            and len(open_pairs) >= self.max_open_positions
        ):
            return False, f"max open positions reached ({self.max_open_positions})"

        return True, "ok"

    def exposure_ok(self, current_notional: float, new_notional: float = 0.0):
        """Whether opening ``new_notional`` keeps total open notional within the
        cap. Returns ``(allowed, reason)``. 0 = off."""
        if self.max_exposure <= 0:
            return True, "ok"
        total = abs(current_notional) + abs(new_notional)
        if total > self.max_exposure:
            return False, (f"exposure cap reached "
                           f"({total:.0f} > {self.max_exposure:.0f})")
        return True, "ok"

    def record_signal(self, symbol: str, side: str, now: float | None = None) -> None:
        """Mark a signal as seen (for dedupe), even if it's later blocked."""
        now = now if now is not None else time.time()
        self._recent_signals[(symbol, side)] = now

    def record_entry(self, symbol: str, now: float | None = None) -> None:
        """Mark an actual entry (for cooldown)."""
        now = now if now is not None else time.time()
        self._last_trade_ts[symbol] = now
