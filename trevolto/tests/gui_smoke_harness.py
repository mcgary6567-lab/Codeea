"""Headless smoke harness for the canvas-drawing windows.

Run as a subprocess by ``test_gui_smoke.py`` so the tkinter stub it installs
can't leak into the rest of the suite. It swaps tkinter for permissive mocks
(with a numeric ``FakeCanvas``) and then actually *constructs* the Chart,
Backtest and Analytics windows and calls their drawing routines over sample
data — exercising the real ``_redraw`` / ``_draw_equity`` / ``_draw_curve``
code paths without needing a display. Any exception => non-zero exit.
"""

import os
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeCanvas:
    """A canvas with real numeric geometry that records draw calls."""

    def __init__(self, *a, **k):
        self.calls = {}
        self._id = 0

    def winfo_width(self):
        return 940

    def winfo_height(self):
        return 560

    def winfo_reqheight(self):
        return 400

    def winfo_rootx(self):
        return 0

    def winfo_rooty(self):
        return 0

    def update_idletasks(self):
        pass

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls[name] = self.calls.get(name, 0) + 1
            self._id += 1
            return self._id
        return rec


def _install_tk_stub():
    tk = MagicMock(name="tkinter")
    tk.TclError = type("TclError", (Exception,), {})

    class _Var:
        def __init__(self, value=None, **k):
            self._v = value

        def get(self):
            return self._v

        def set(self, v):
            self._v = v

    for n in ("StringVar", "BooleanVar", "IntVar", "DoubleVar"):
        setattr(tk, n, _Var)
    tk.Canvas = FakeCanvas                       # every window's canvas is numeric
    sys.modules["tkinter"] = tk

    ttk = MagicMock(name="ttk")
    ttk.Treeview.return_value.get_children.return_value = []   # so refresh() can iterate
    sys.modules["tkinter.ttk"] = ttk
    for sub in ("filedialog", "messagebox", "font", "scrolledtext"):
        sys.modules[f"tkinter.{sub}"] = MagicMock()
    return tk


def _sample_candles(n=160):
    out = []
    t = 1_700_000_000_000
    px = 60000.0
    for i in range(n):
        o = px
        px *= 1 + 0.0015 * ((-1) ** i)           # gentle zig-zag
        c = px
        out.append([t, o, max(o, c) * 1.001, min(o, c) * 0.999, c, 5.0])
        t += 3_600_000
    return out


def main() -> int:
    _install_tk_stub()
    import strategy
    import chart_window
    import backtest_window
    import analytics_window

    params = strategy.StrategyParams()
    cs = _sample_candles()
    closes = [x[4] for x in cs]

    # --- Chart: candles + overlays, plus the empty-state path ---
    ch = chart_window.ChartWindow(
        MagicMock(), lambda *a, **k: ["BTC/USDT"], lambda: "1h",
        lambda: params, lambda: "binance", lambda: "spot")
    assert isinstance(ch.canvas, FakeCanvas), "chart canvas not stubbed"
    ch._candles = cs
    ch._price_ma = strategy.ema_series(closes, params.ma_len)
    ch._rsi = strategy.rsi_series(closes, params.rsi_len)
    ch._ma_events = []
    ch.view_end = None
    ch._redraw()
    assert ch.canvas.calls.get("create_rectangle", 0) > 0, "no candles drawn"
    assert ch.canvas.calls.get("create_line", 0) > 0, "no lines drawn"
    ch.canvas.calls.clear()
    ch._candles = []
    ch._redraw()
    assert ch.canvas.calls.get("create_text", 0) > 0, "empty-state text missing"

    # --- Backtest: equity curve ---
    bw = backtest_window.BacktestWindow(
        MagicMock(), lambda *a, **k: ["BTC/USDT"], lambda: "1h",
        lambda: params, lambda: "binance", lambda: "spot")
    bw._result = SimpleNamespace(equity=[(i, 1000 + i * 3 - (i % 5)) for i in range(40)])
    bw._draw_equity()
    assert bw.canvas.calls.get("create_line", 0) > 0, "no equity curve drawn"

    # --- Analytics: equity curve (refresh ran in __init__ too) ---
    hist = MagicMock()
    hist.stats.return_value = {
        "total": 5, "filled": 4, "rejected": 1, "closed": 3, "win_rate": 66.6,
        "wins": 2, "losses": 1, "realized_pnl": 12.5, "best": 9.0, "worst": -3.0}
    hist.stats_by_symbol.return_value = []
    hist.fetch_equity.return_value = [(i, 1000 + i * 2) for i in range(12)]
    aw = analytics_window.AnalyticsWindow(MagicMock(), hist)
    aw._draw_curve(hist.fetch_equity.return_value)
    assert aw.canvas.calls.get("create_line", 0) > 0, "no analytics curve drawn"

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
