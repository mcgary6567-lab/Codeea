"""Strategy chart window — a TradingView-style candlestick view, in pure Tkinter.

Draws candles on a ``tk.Canvas`` (same dependency-free approach as the Analytics
equity curve) and overlays the **built-in strategy's own signals**, computed by
the very engine the bot trades on (:func:`strategy.evaluate_all`):

* yellow **dip** diamonds      (``isDipRed`` bars)
* lime **BUY** arrows          (confirmed entries)
* blue **ENTRY**, orange **TP1**, aqua **TP2**, red **SL** level lines
  for the most recent signal in view

So the chart shows exactly what fired on the same exchange candles the strategy
evaluates — not an approximation. Candles come from a public (keyless) ccxt
client on a worker thread, so the UI never blocks and the authenticated backend
client is never touched.

Interactions: hover crosshair + OHLC readout, scroll-wheel zoom, drag to pan,
auto-refresh on a timer plus a manual Refresh.
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional

try:
    import ccxt

    CCXT_AVAILABLE = True
except ImportError:
    ccxt = None
    CCXT_AVAILABLE = False

import strategy
import theme
from config import STRATEGY_CANDLE_LIMIT, STRATEGY_TIMEFRAMES
from exchange import normalize_symbol

BG = theme.BG
PANEL = theme.PANEL
ELEV = theme.ELEV
BORDER = theme.BORDER
TXT = theme.TXT
DIM = theme.TXT_DIM
ACCENT = theme.ACCENT
GREEN = theme.GREEN_HL
RED = theme.RED_HL

# Overlay palette — mirrors the Pine indicator's colours.
DIP_COLOR = "#ffd54a"      # yellow dip diamond
BUY_COLOR = "#7CFF6B"      # lime BUY arrow
ENTRY_COLOR = "#4a9eff"    # blue entry line
TP1_COLOR = "#ffa057"      # orange TP1
TP2_COLOR = "#33d6cf"      # aqua TP2
SL_COLOR = "#ff5c5c"       # red SL

REFRESH_MS = 15000         # auto-refresh cadence
DEFAULT_VIEW = 120         # visible candles on open


class ChartWindow:
    def __init__(
        self,
        root: tk.Tk,
        exchange_id: str,
        market_type: str,
        symbol: str,
        timeframe: str,
        get_params: Callable[[], "strategy.StrategyParams"],
        symbols: Optional[List[str]] = None,
    ) -> None:
        self.exchange_id = exchange_id
        self.market_type = market_type
        self.symbol = symbol or "BTC/USDT"
        self.timeframe = timeframe or "5m"
        self.get_params = get_params

        self._client = None
        self._candles: List[list] = []
        self._dips: List[int] = []
        self._signals: List = []
        self._rsi: List = []
        self._vol_sma: List = []
        self._eff_os: float = 30
        self._geom: Optional[dict] = None
        self.view_count = DEFAULT_VIEW
        self.view_end: Optional[int] = None
        self._drag = None
        self._alive = True
        self._after_id = None

        self.win = tk.Toplevel(root)
        self.win.title("Strategy Chart — Prometheus AI Crypto Bot")
        self.win.geometry("960x600")
        self.win.minsize(720, 460)
        self.win.configure(bg=BG)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build(symbols or [self.symbol])
        self._load()
        self._schedule()

    # -- public helpers (used by the GUI to reuse one window) ---------------
    def alive(self) -> bool:
        return self._alive

    def focus(self) -> None:
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except tk.TclError:
            pass

    # -- layout -------------------------------------------------------------
    def _build(self, symbols: List[str]) -> None:
        bar = tk.Frame(self.win, bg=BG)
        bar.pack(fill="x", padx=12, pady=(10, 6))

        tk.Label(bar, text="Symbol", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left")
        self.symbol_var = tk.StringVar(value=self.symbol)
        sym_box = ttk.Combobox(bar, textvariable=self.symbol_var, width=14,
                               values=symbols)
        sym_box.pack(side="left", padx=(4, 12))
        sym_box.bind("<<ComboboxSelected>>", lambda e: self._on_symbol_change())
        sym_box.bind("<Return>", lambda e: self._on_symbol_change())

        tk.Label(bar, text="Timeframe", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left")
        self.tf_var = tk.StringVar(value=self.timeframe)
        tf_box = ttk.Combobox(bar, textvariable=self.tf_var, width=6, state="readonly",
                              values=STRATEGY_TIMEFRAMES)
        tf_box.pack(side="left", padx=(4, 12))
        tf_box.bind("<<ComboboxSelected>>", lambda e: self._on_tf_change())

        tk.Button(bar, text="Refresh", command=self._load, bg=ACCENT, fg="#1a1100",
                  relief="flat", bd=0, cursor="hand2", font=("Segoe UI Semibold", 9),
                  activebackground="#ffa057", padx=14, pady=4).pack(side="right")
        self.status = tk.Label(bar, text="Loading…", bg=BG, fg=DIM, font=("Segoe UI", 9))
        self.status.pack(side="right", padx=10)

        # Legend.
        leg = tk.Frame(self.win, bg=BG)
        leg.pack(fill="x", padx=12, pady=(0, 4))
        for txt, col in (("◆ dip", DIP_COLOR), ("▲ BUY", BUY_COLOR),
                         ("— entry", ENTRY_COLOR), ("— TP1", TP1_COLOR),
                         ("— TP2", TP2_COLOR), ("— SL", SL_COLOR)):
            tk.Label(leg, text=txt, bg=BG, fg=col, font=("Segoe UI", 8)).pack(side="left", padx=6)
        tk.Label(leg, text="scroll = zoom · drag = pan", bg=BG, fg=DIM,
                 font=("Segoe UI", 8)).pack(side="right")

        wrap = tk.Frame(self.win, bg=BORDER)
        wrap.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.canvas = tk.Canvas(wrap, bg=PANEL, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=1, pady=1)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self.canvas.delete("cross"))
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<MouseWheel>", self._on_wheel)       # Windows / macOS
        self.canvas.bind("<Button-4>", self._on_wheel)         # Linux up
        self.canvas.bind("<Button-5>", self._on_wheel)         # Linux down

    # -- data fetch (worker thread) ----------------------------------------
    def _post(self, fn) -> None:
        if self._alive:
            try:
                self.win.after(0, fn)
            except tk.TclError:
                pass

    def _load(self) -> None:
        if not CCXT_AVAILABLE:
            self.status.config(text="ccxt not installed — no candles")
            return
        self.status.config(text="Loading…")
        threading.Thread(target=self._load_worker, daemon=True).start()

    def _load_worker(self) -> None:
        try:
            candles = self._fetch()
            params = self.get_params()
            dips, signals = strategy.evaluate_all(candles, params, ticker=self.symbol)
            closes = [c[4] for c in candles]
            volumes = [c[5] for c in candles]
            rsi = strategy.rsi_series(closes, params.rsi_len)
            vol_sma = strategy.sma_series(volumes, params.vol_len)
            eff_os = strategy.effective_settings(params.preset, self.symbol, params)[1]
        except Exception as exc:  # noqa: BLE001 - surface, don't crash the window
            self._post(lambda: self.status.config(text=f"Error: {exc}"))
            return

        def apply():
            self._candles = candles
            self._dips = dips
            self._signals = signals
            self._rsi = rsi
            self._vol_sma = vol_sma
            self._eff_os = eff_os
            if self.view_end is None:
                self.view_end = len(candles) - 1
            self.status.config(
                text=f"{self.symbol} · {self.timeframe} · {len(candles)} candles · "
                     f"{len(signals)} signal{'s' if len(signals) != 1 else ''}")
            self._redraw()
        self._post(apply)

    def _client_obj(self):
        if self._client is None:
            self._client = getattr(ccxt, self.exchange_id)({
                "enableRateLimit": True,
                "options": {"defaultType": "swap" if self.market_type == "futures" else "spot"},
            })
        return self._client

    def _market_symbol(self) -> str:
        sym = normalize_symbol(self.symbol)
        if self.market_type != "futures" or ":" in sym:
            return sym
        base, _, quote = sym.partition("/")
        return f"{base}/{quote}:{quote}"

    def _fetch(self) -> List[list]:
        raw = self._client_obj().fetch_ohlcv(self._market_symbol(), self.timeframe,
                                             limit=STRATEGY_CANDLE_LIMIT)
        # Drop the still-forming candle so the chart matches what the engine sees.
        return raw[:-1] if len(raw) > 1 else raw

    def _schedule(self) -> None:
        if self._alive:
            self._after_id = self.win.after(REFRESH_MS, self._tick)

    def _tick(self) -> None:
        if not self._alive:
            return
        self._load()
        self._schedule()

    # -- input handlers -----------------------------------------------------
    def _on_symbol_change(self) -> None:
        self.symbol = self.symbol_var.get().strip() or self.symbol
        self.view_end = None
        self.view_count = DEFAULT_VIEW
        self._load()

    def _on_tf_change(self) -> None:
        self.timeframe = self.tf_var.get()
        self.view_end = None
        self.view_count = DEFAULT_VIEW
        self._load()

    def _on_wheel(self, e) -> None:
        delta = getattr(e, "delta", 0)
        up = delta > 0 or getattr(e, "num", 0) == 4
        if up:
            self.view_count = max(20, int(self.view_count * 0.85))
        else:
            self.view_count = min(max(len(self._candles), 20), int(self.view_count * 1.18) + 1)
        self._redraw()

    def _on_press(self, e) -> None:
        self._drag = (e.x, self.view_end if self.view_end is not None else len(self._candles) - 1)

    def _on_drag(self, e) -> None:
        if not self._drag or not self._geom:
            return
        dx = e.x - self._drag[0]
        steps = int(-dx / self._geom["cw"]) if self._geom["cw"] else 0
        n = len(self._candles)
        ve = self._drag[1] + steps
        self.view_end = max(self.view_count - 1, min(n - 1, ve))
        self._redraw()

    # -- formatting ---------------------------------------------------------
    @staticmethod
    def _fmt(p: float) -> str:
        if p >= 100:
            return f"{p:,.2f}"
        if p >= 1:
            return f"{p:.4f}"
        return f"{p:.6f}"

    # -- drawing ------------------------------------------------------------
    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        c.update_idletasks()
        w = c.winfo_width() or 940
        h = c.winfo_height() or 520
        if not self._candles:
            c.create_text(w // 2, h // 2, text="No candles yet — Refresh to load",
                          fill=DIM, font=("Segoe UI", 11))
            return

        padL, padR, padT, padB = 8, 72, 10, 22
        n = len(self._candles)
        vc = max(5, min(self.view_count, n))
        end = self.view_end if self.view_end is not None else n - 1
        end = max(vc - 1, min(end, n - 1))
        start = end - vc + 1
        view = self._candles[start:end + 1]

        # Vertical layout: price pane (top), volume pane, RSI pane (bottom).
        total_h = h - padT - padB
        gap = 8
        rsi_h = max(54.0, total_h * 0.20)
        vol_h = max(36.0, total_h * 0.16)
        price_h = max(80.0, total_h - rsi_h - vol_h - 2 * gap)
        price_top = padT
        price_bot = price_top + price_h
        vol_bot = price_bot + gap + vol_h
        vol_top = vol_bot - vol_h
        rsi_top = vol_bot + gap
        rsi_bot = rsi_top + rsi_h

        plot_w = w - padL - padR
        cw = plot_w / vc

        def X(k):
            return padL + cw * (k + 0.5)

        # --- price range (include the visible signal's levels so they stay on-screen) ---
        hi = max(x[2] for x in view)
        lo = min(x[3] for x in view)
        vis_sigs = [s for s in self._signals if start <= s.index <= end]
        if vis_sigs:
            s = vis_sigs[-1]
            for lv in [s.entry, s.tp1, s.tp2] + ([s.sl] if s.sl else []):
                hi = max(hi, lv)
                lo = min(lo, lv)
        span = (hi - lo) or (abs(hi) * 0.01 or 1.0)
        hi += span * 0.05
        lo -= span * 0.05
        span = hi - lo

        def Yp(p):
            return price_top + price_h * (hi - p) / span

        # Price gridlines + right-edge labels.
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            p = lo + span * frac
            y = Yp(p)
            c.create_line(padL, y, w - padR, y, fill=BORDER)
            c.create_text(w - padR + 4, y, anchor="w", text=self._fmt(p), fill=DIM,
                          font=("Segoe UI", 8))

        # Time labels along the very bottom.
        stepk = max(1, vc // 6)
        for k in range(0, vc, stepk):
            ts = view[k][0] / 1000.0
            c.create_text(X(k), h - padB + 11,
                          text=time.strftime("%m-%d %H:%M", time.localtime(ts)),
                          fill=DIM, font=("Segoe UI", 7))

        # Candles.
        bw = max(1.0, cw * 0.62)
        for k, cd in enumerate(view):
            o, hh, ll, cc = cd[1], cd[2], cd[3], cd[4]
            x = X(k)
            col = GREEN if cc >= o else RED
            c.create_line(x, Yp(hh), x, Yp(ll), fill=col)
            y1, y2 = Yp(max(o, cc)), Yp(min(o, cc))
            if y2 - y1 < 1:
                y2 = y1 + 1
            c.create_rectangle(x - bw / 2, y1, x + bw / 2, y2, fill=col, outline=col)

        # Dip diamonds (below the bar).
        for di in self._dips:
            if start <= di <= end:
                k = di - start
                x, y = X(k), Yp(view[k][3]) + 9
                c.create_polygon(x, y - 4, x - 4, y, x, y + 4, x + 4, y,
                                 fill=DIP_COLOR, outline="")

        # BUY arrows.
        for s in vis_sigs:
            k = s.index - start
            x, y = X(k), Yp(view[k][3]) + 20
            c.create_polygon(x, y - 7, x - 6, y + 5, x + 6, y + 5,
                             fill=BUY_COLOR, outline="")
            c.create_text(x, y + 13, text="BUY", fill=BUY_COLOR, font=("Segoe UI", 7))

        # Level lines for the most recent visible signal.
        if vis_sigs:
            s = vis_sigs[-1]
            self._level(c, Yp, w, padL, padR, s.entry, ENTRY_COLOR, (), "ENTRY")
            self._level(c, Yp, w, padL, padR, s.tp1, TP1_COLOR, (4, 2),
                        f"TP1 +{self._pct(s.tp1, s.entry)}%")
            self._level(c, Yp, w, padL, padR, s.tp2, TP2_COLOR, (4, 2),
                        f"TP2 +{self._pct(s.tp2, s.entry)}%")
            if s.sl:
                self._level(c, Yp, w, padL, padR, s.sl, SL_COLOR, (2, 2),
                            f"SL {self._pct(s.sl, s.entry)}%")

        # --- volume pane ---
        maxv = max((x[5] for x in view), default=0.0) or 1.0
        c.create_line(padL, vol_bot, w - padR, vol_bot, fill=BORDER)
        c.create_text(padL + 2, vol_top + 6, anchor="w", text="Vol", fill=DIM,
                      font=("Segoe UI", 7))
        for k, cd in enumerate(view):
            vh = vol_h * (cd[5] / maxv)
            x = X(k)
            col = GREEN if cd[4] >= cd[1] else RED
            c.create_rectangle(x - bw / 2, vol_bot - vh, x + bw / 2, vol_bot,
                               fill=col, outline="", stipple="gray50")
        # Volume SMA — the average the strategy's volume filter compares against.
        sma_pts = []
        for k in range(vc):
            sv = self._vol_sma[start + k] if start + k < len(self._vol_sma) else None
            if sv is not None:
                sma_pts += [X(k), vol_bot - vol_h * min(sv / maxv, 1.0)]
        if len(sma_pts) >= 4:
            c.create_line(*sma_pts, fill=ACCENT, width=1)

        # --- RSI pane ---
        c.create_rectangle(padL, rsi_top, w - padR, rsi_bot, outline=BORDER)
        c.create_text(padL + 2, rsi_top + 6, anchor="w", text="RSI", fill=DIM,
                      font=("Segoe UI", 7))

        def Yr(r):
            return rsi_bot - rsi_h * (max(0.0, min(100.0, r)) / 100.0)

        for lvl in (70, 50, 30):
            y = Yr(lvl)
            c.create_line(padL, y, w - padR, y, fill=BORDER, dash=(2, 2))
            c.create_text(w - padR + 4, y, anchor="w", text=str(lvl), fill=DIM,
                          font=("Segoe UI", 7))
        # The strategy's effective oversold threshold (the dip trigger), highlighted.
        yos = Yr(self._eff_os)
        c.create_line(padL, yos, w - padR, yos, fill=DIP_COLOR, dash=(3, 2))
        c.create_text(w - padR + 4, yos, anchor="w", text=f"OS {self._eff_os:g}",
                      fill=DIP_COLOR, font=("Segoe UI", 7))
        rsi_pts = []
        for k in range(vc):
            idx = start + k
            rv = self._rsi[idx] if idx < len(self._rsi) else None
            if rv is not None:
                rsi_pts += [X(k), Yr(rv)]
        if len(rsi_pts) >= 4:
            c.create_line(*rsi_pts, fill="#c792ea", width=1)

        self._geom = dict(padL=padL, padR=padR, padT=padT, padB=padB, w=w, h=h,
                          cw=cw, vc=vc, hi=hi, span=span,
                          price_top=price_top, price_h=price_h,
                          view=view, start=start)

    @staticmethod
    def _pct(target: float, base: float) -> str:
        if not base:
            return "0.00"
        return f"{(target - base) / base * 100.0:.2f}"

    @staticmethod
    def _vfmt(v: float) -> str:
        if v >= 1e9:
            return f"{v / 1e9:.2f}B"
        if v >= 1e6:
            return f"{v / 1e6:.2f}M"
        if v >= 1e3:
            return f"{v / 1e3:.1f}K"
        return f"{v:.0f}"

    def _level(self, c, Y, w, padL, padR, price, color, dash, label) -> None:
        y = Y(price)
        if dash:
            c.create_line(padL, y, w - padR, y, fill=color, dash=dash)
        else:
            c.create_line(padL, y, w - padR, y, fill=color)   # solid (no dash arg)
        c.create_text(w - padR + 4, y, anchor="w",
                      text=f"{label} {self._fmt(price)}", fill=color, font=("Segoe UI", 7))

    # -- crosshair + OHLC tooltip ------------------------------------------
    def _on_motion(self, e) -> None:
        c = self.canvas
        c.delete("cross")
        g = self._geom
        if not g or e.x < g["padL"] or e.x > g["w"] - g["padR"]:
            return
        k = int((e.x - g["padL"]) / g["cw"]) if g["cw"] else 0
        k = max(0, min(g["vc"] - 1, k))
        cd = g["view"][k]
        cx = g["padL"] + g["cw"] * (k + 0.5)
        # Vertical line spans all three panes.
        c.create_line(cx, g["padT"], cx, g["h"] - g["padB"], fill=DIM, dash=(2, 2), tags="cross")
        # Horizontal line + price readout only within the price pane.
        pt, ph = g["price_top"], g["price_h"]
        if pt <= e.y <= pt + ph:
            c.create_line(g["padL"], e.y, g["w"] - g["padR"], e.y, fill=DIM, dash=(2, 2), tags="cross")
            price = g["hi"] - (e.y - pt) / ph * g["span"]
            c.create_text(g["w"] - g["padR"] + 4, e.y, anchor="w", text=self._fmt(price),
                          fill=TXT, font=("Segoe UI", 8), tags="cross")
        idx = g["start"] + k
        rv = self._rsi[idx] if idx < len(self._rsi) else None
        rtxt = f"  RSI {rv:.1f}" if rv is not None else ""
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(cd[0] / 1000.0))
        info = (f"{when}   O {self._fmt(cd[1])}  H {self._fmt(cd[2])}  "
                f"L {self._fmt(cd[3])}  C {self._fmt(cd[4])}  V {self._vfmt(cd[5])}{rtxt}")
        c.create_rectangle(g["padL"] + 2, g["padT"] + 2, g["padL"] + 8 + len(info) * 6.0,
                           g["padT"] + 18, fill=ELEV, outline="", tags="cross")
        c.create_text(g["padL"] + 6, g["padT"] + 10, anchor="w", text=info,
                      fill=TXT, font=("Segoe UI", 8), tags="cross")

    # -- lifecycle ----------------------------------------------------------
    def _on_close(self) -> None:
        self._alive = False
        if self._after_id:
            try:
                self.win.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self.win.destroy()
