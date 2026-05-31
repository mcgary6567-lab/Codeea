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
        self.max_open_positions = 0
        self.daily_loss_limit = 0.0
        self.cooldown_seconds = 0
        self.dedupe_seconds = 0

        self._day: date | None = None
        self._daily_realized = 0.0
        self._last_trade_ts: dict = {}      # symbol -> ts
        self._recent_signals: dict = {}     # (symbol, side) -> ts
        self.tripped = False                # daily loss limit hit

    # -- config -------------------------------------------------------------
    def configure(self, max_open, daily_loss, cooldown, dedupe) -> None:
        self.max_open_positions = int(max_open or 0)
        self.daily_loss_limit = float(daily_loss or 0.0)
        self.cooldown_seconds = int(cooldown or 0)
        self.dedupe_seconds = int(dedupe or 0)

    # -- daily PnL tracking -------------------------------------------------
    def _roll_day(self, now: float) -> None:
        today = date.fromtimestamp(now)
        if self._day != today:
            self._day = today
            self._daily_realized = 0.0
            self.tripped = False

    def record_realized(self, pnl: float, now: float | None = None) -> bool:
        """Add a realized PnL amount; returns True if this *trips* the limit."""
        now = now if now is not None else time.time()
        self._roll_day(now)
        self._daily_realized += pnl
        if (
            self.daily_loss_limit > 0
            and not self.tripped
            and self._daily_realized <= -self.daily_loss_limit
        ):
            self.tripped = True
            return True
        return False

    @property
    def daily_realized(self) -> float:
        return self._daily_realized

    def reset_daily(self) -> None:
        self._daily_realized = 0.0
        self.tripped = False

    # -- the entry gate -----------------------------------------------------
    def check_entry(self, symbol: str, side: str, open_pairs: set, now: float | None = None):
        """Return ``(allowed: bool, reason: str)`` for opening this trade."""
        now = now if now is not None else time.time()
        self._roll_day(now)
        if not self.enabled:
            return True, "guardrails off"

        if self.daily_loss_limit > 0 and self.tripped:
            return False, (
                f"daily loss limit hit ({self._daily_realized:+.2f} "
                f"<= -{self.daily_loss_limit:g}) — trading halted"
            )

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

    def record_signal(self, symbol: str, side: str, now: float | None = None) -> None:
        """Mark a signal as seen (for dedupe), even if it's later blocked."""
        now = now if now is not None else time.time()
        self._recent_signals[(symbol, side)] = now

    def record_entry(self, symbol: str, now: float | None = None) -> None:
        """Mark an actual entry (for cooldown)."""
        now = now if now is not None else time.time()
        self._last_trade_ts[symbol] = now
