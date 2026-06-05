"""Strategy chart window — a TradingView-style candlestick view, in pure Tkinter.

Draws candles on a ``tk.Canvas`` (same dependency-free approach as the Analytics
equity curve) and overlays the **built-in strategy's own signals**, computed by
the very engine the bot trades on (:func:`strategy.evaluate_all_crossover`):

* green **BUY** (▲) / red **SELL** (▼) entry arrows
* yellow **⊙ scale-out** markers (RSI 70/30) and grey **✕ exit** markers
* blue **ENTRY** + red **SL** level lines for the most recent trade, the
  **EMA20** overlay, and an RSI pane (70/30 zones, 50 centerline)

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
from config import STRATEGY_CANDLE_LIMIT, STRATEGY_TIMEFRAMES, exchange_label
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

# Overlay palette.
ENTRY_COLOR = "#4a9eff"    # blue entry line
SL_COLOR = "#ff5c5c"       # red SL
MA_COLOR = "#5b8def"       # EMA overlay line
RSI_COLOR = "#c792ea"      # RSI line
LONG_COLOR = "#7CFF6B"     # long entry arrow (BUY, up)
SHORT_COLOR = "#ff5c5c"    # short entry arrow (SELL, down)
SCALE_COLOR = "#ffd54a"    # RSI-extreme scale-out marker
EXIT_COLOR = "#9aa4b2"     # EMA-trail / stop exit marker

REFRESH_MS = 15000         # auto-refresh cadence
DEFAULT_VIEW = 120         # visible candles on open
MA_LEN = 20                # EMA length shown in the legend label


class ChartWindow:
    def __init__(
        self,
        root: tk.Tk,
        get_symbols: Callable[[], List[str]],
        get_timeframe: Callable[[], str],
        get_params: Callable[[], "strategy.StrategyParams"],
        get_exchange: Callable[[], str],
        get_market: Callable[[], str],
    ) -> None:
        # Live getters into the Strategy/Execution tabs — the chart can follow
        # them so it always mirrors what the strategy actually trades.
        self.get_symbols = get_symbols
        self.get_timeframe = get_timeframe
        self.get_params = get_params
        self.get_exchange = get_exchange
        self.get_market = get_market

        syms = self._safe_symbols()
        self.symbol = syms[0] if syms else "BTC/USDT"
        self.timeframe = (self._safe(get_timeframe) or "1h")
        self.exchange_id = self._safe(get_exchange) or "binance"
        self.market_type = self._safe(get_market) or "spot"

        self._client = None
        self._candles: List[list] = []
        self._ma_events: List = []       # (index, event) crossover instructions
        self._rsi: List = []
        self._price_ma: List = []        # EMA20 overlay
        self._geom: Optional[dict] = None
        self.view_count = DEFAULT_VIEW
        self.view_end: Optional[int] = None
        self._drag = None
        self._alive = True
        self._after_id = None
        self._reload_after_id = None

        self.win = tk.Toplevel(root)
        self.win.title("Strategy Chart — Prometheus AI Crypto Bot")
        self.win.geometry("960x600")
        self.win.minsize(720, 460)
        self.win.configure(bg=BG)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        self.follow = tk.BooleanVar(value=True)   # mirror the Strategy tab live
        self._build(syms or [self.symbol])
        self._sync_controls_state()
        self._load()
        self._schedule()

    # -- small safe getters -------------------------------------------------
    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return None

    def _safe_symbols(self) -> List[str]:
        return [s for s in (self._safe(self.get_symbols) or []) if s]

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

        self._follow_chk = tk.Checkbutton(
            bar, text="Follow Strategy tab", variable=self.follow,
            command=self._on_follow_toggle, bg=BG, fg=DIM, selectcolor=ELEV,
            activebackground=BG, activeforeground=TXT, bd=0, highlightthickness=0,
            font=("Segoe UI", 9), cursor="hand2")
        self._follow_chk.pack(side="left", padx=(0, 12))

        tk.Label(bar, text="Symbol", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left")
        self.symbol_var = tk.StringVar(value=self.symbol)
        self._sym_box = ttk.Combobox(bar, textvariable=self.symbol_var, width=14,
                                     values=symbols)
        self._sym_box.pack(side="left", padx=(4, 12))
        self._sym_box.bind("<<ComboboxSelected>>", lambda e: self._on_symbol_change())
        self._sym_box.bind("<Return>", lambda e: self._on_symbol_change())

        tk.Label(bar, text="Timeframe", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left")
        self.tf_var = tk.StringVar(value=self.timeframe)
        self._tf_box = ttk.Combobox(bar, textvariable=self.tf_var, width=6, state="readonly",
                                    values=STRATEGY_TIMEFRAMES)
        self._tf_box.pack(side="left", padx=(4, 12))
        self._tf_box.bind("<<ComboboxSelected>>", lambda e: self._on_tf_change())

        tk.Button(bar, text="Refresh", command=self._load, bg=ACCENT, fg="#1a1100",
                  relief="flat", bd=0, cursor="hand2", font=("Segoe UI Semibold", 9),
                  activebackground="#ffa057", padx=14, pady=4).pack(side="right")
        self.status = tk.Label(bar, text="Loading…", bg=BG, fg=DIM, font=("Segoe UI", 9))
        self.status.pack(side="right", padx=10)

        # "What am I tracking?" banner + legend.
        leg = tk.Frame(self.win, bg=BG)
        leg.pack(fill="x", padx=12, pady=(0, 4))
        self._track_lbl = tk.Label(leg, text="", bg=BG, fg=GREEN,
                                   font=("Segoe UI Semibold", 9))
        self._track_lbl.pack(side="left", padx=(0, 10))
        self._update_track_label()
        tk.Label(leg, text="scroll = zoom · drag = pan", bg=BG, fg=DIM,
                 font=("Segoe UI", 8)).pack(side="right")
        self._legend_items = tk.Frame(leg, bg=BG)
        self._legend_items.pack(side="left")
        self._refresh_legend()

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
            closes = [c[4] for c in candles]
            rsi = strategy.rsi_series(closes, params.rsi_len)
            ma_events = strategy.evaluate_all_crossover(candles, params, ticker=self.symbol)
            price_ma = strategy.ema_series(closes, params.ma_len)
        except Exception as exc:  # noqa: BLE001 - surface, don't crash the window
            self._post(lambda: self.status.config(text=f"Error: {exc}"))
            return

        def apply():
            self._candles = candles
            self._ma_events = ma_events
            self._rsi = rsi
            self._price_ma = price_ma
            if self.view_end is None:
                self.view_end = len(candles) - 1
            count = sum(1 for _, e in ma_events if e["act"] == "enter")
            self.status.config(
                text=f"{len(candles)} candles · {count} entr{'y' if count == 1 else 'ies'}")
            self._update_track_label()
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
        self._pull_settings()       # follow the Strategy tab (no-op if unfollowed)
        self._load()
        self._schedule()

    def _refresh_legend(self) -> None:
        """Build the legend chips for the EMA20+RSI crossover strategy."""
        f = getattr(self, "_legend_items", None)
        if f is None:
            return
        for ch in f.winfo_children():
            ch.destroy()
        items = (("▲ BUY", LONG_COLOR), ("▼ SELL", SHORT_COLOR),
                 ("⊙ scale-out", SCALE_COLOR), ("✕ exit", EXIT_COLOR),
                 ("— entry", ENTRY_COLOR), ("— SL", SL_COLOR),
                 (f"— EMA{MA_LEN}", MA_COLOR))
        for txt, col in items:
            tk.Label(f, text=txt, bg=BG, fg=col, font=("Segoe UI", 8)).pack(side="left", padx=6)

    def _update_track_label(self) -> None:
        """Refresh the banner so it's always obvious what the chart tracks."""
        if not hasattr(self, "_track_lbl"):
            return
        following = self.follow.get()
        dot = "● following" if following else "○ manual"
        feed = f"{exchange_label(self.exchange_id)} {self.market_type}"
        try:
            self._track_lbl.config(
                text=f"{dot} · {self.symbol} · {self.timeframe} · {feed}",
                fg=(GREEN if following else DIM))
        except tk.TclError:
            pass

    # -- follow the Strategy tab -------------------------------------------
    def sync(self) -> None:
        """Called by the GUI when a Strategy/Execution setting changes.

        Debounced so rapid edits (typing) coalesce into one reload."""
        if not self._alive or not self.follow.get():
            return
        self._pull_settings()
        if self._reload_after_id:
            try:
                self.win.after_cancel(self._reload_after_id)
            except tk.TclError:
                pass
        self._reload_after_id = self.win.after(700, self._load)

    def _pull_settings(self) -> None:
        """Adopt the live Strategy/Execution settings (only while following)."""
        if not self.follow.get():
            return
        syms = self._safe_symbols() or [self.symbol]
        tf = self._safe(self.get_timeframe) or self.timeframe
        ex = self._safe(self.get_exchange) or self.exchange_id
        mk = self._safe(self.get_market) or self.market_type
        new_symbol = self.symbol if self.symbol in syms else syms[0]
        feed_changed = (ex != self.exchange_id or mk != self.market_type)
        changed = (new_symbol != self.symbol or tf != self.timeframe or feed_changed)
        self.exchange_id, self.market_type = ex, mk
        if feed_changed:
            self._client = None        # rebuild the ccxt client for the new feed
        self.symbol, self.timeframe = new_symbol, tf
        try:
            self._sym_box.config(values=syms)
            self.symbol_var.set(new_symbol)
            self.tf_var.set(tf)
        except tk.TclError:
            pass
        if changed:
            self.view_end = None
            self.view_count = DEFAULT_VIEW
        self._update_track_label()

    def _on_follow_toggle(self) -> None:
        self._sync_controls_state()
        if self.follow.get():
            self._pull_settings()
            self._load()
        self._update_track_label()

    def _sync_controls_state(self) -> None:
        """Lock the symbol/timeframe pickers while following the tab."""
        try:
            if self.follow.get():
                self._sym_box.config(state="disabled")
                self._tf_box.config(state="disabled")
            else:
                self._sym_box.config(state="normal")
                self._tf_box.config(state="readonly")
        except tk.TclError:
            pass

    # -- input handlers -----------------------------------------------------
    def _on_symbol_change(self) -> None:
        self.symbol = self.symbol_var.get().strip() or self.symbol
        self.view_end = None
        self.view_count = DEFAULT_VIEW
        self._update_track_label()
        self._load()

    def _on_tf_change(self) -> None:
        self.timeframe = self.tf_var.get()
        self.view_end = None
        self.view_count = DEFAULT_VIEW
        self._update_track_label()
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

        # --- price range (include the latest entry's levels so they stay on-screen) ---
        hi = max(x[2] for x in view)
        lo = min(x[3] for x in view)
        ma_in_view = [(i, e) for (i, e) in self._ma_events if start <= i <= end]
        entries = [e for (_, e) in ma_in_view if e["act"] == "enter"]
        if entries:
            for lv in (entries[-1]["entry"], entries[-1].get("sl")):
                if lv:
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

        # Price moving-average overlay.
        ma_pts = []
        for k in range(vc):
            mv = self._price_ma[start + k] if start + k < len(self._price_ma) else None
            if mv is not None:
                ma_pts += [X(k), Yp(mv)]
        if len(ma_pts) >= 4:
            c.create_line(*ma_pts, fill=MA_COLOR, width=1)

        # --- Entry/scale-out/exit overlays: BUY (▲), SELL (▼), ⊙ scale-out, ✕ exit ---
        last_entry = None
        for i, e in ma_in_view:
            k = i - start
            x = X(k)
            act = e["act"]
            if act == "enter":
                last_entry = e
                if e["side"] == "long":
                    y = Yp(view[k][3]) + 14            # below the low
                    c.create_polygon(x, y - 7, x - 6, y + 5, x + 6, y + 5,
                                     fill=LONG_COLOR, outline="")
                    c.create_text(x, y + 13, text="BUY", fill=LONG_COLOR,
                                  font=("Segoe UI", 7, "bold"))
                else:
                    y = Yp(view[k][2]) - 14            # above the high
                    c.create_polygon(x, y + 7, x - 6, y - 5, x + 6, y - 5,
                                     fill=SHORT_COLOR, outline="")
                    c.create_text(x, y - 13, text="SELL", fill=SHORT_COLOR,
                                  font=("Segoe UI", 7, "bold"))
            elif act == "scale_out":
                above = e.get("side") == "long"
                y = Yp(view[k][2]) - 8 if above else Yp(view[k][3]) + 8
                c.create_oval(x - 4, y - 4, x + 4, y + 4, outline=SCALE_COLOR, width=2)
            elif act == "exit":
                y = Yp(view[k][4])                     # at the close
                c.create_line(x - 4, y - 4, x + 4, y + 4, fill=EXIT_COLOR, width=2)
                c.create_line(x - 4, y + 4, x + 4, y - 4, fill=EXIT_COLOR, width=2)
        # Entry + SL level lines for the most recent visible entry.
        if last_entry is not None:
            verb = "BUY" if last_entry["side"] == "long" else "SELL"
            self._level(c, Yp, w, padL, padR, last_entry["entry"], ENTRY_COLOR, (),
                        f"{verb} ENTRY")
            if last_entry.get("sl"):
                self._level(c, Yp, w, padL, padR, last_entry["sl"], SL_COLOR, (2, 2),
                            f"SL {self._pct(last_entry['sl'], last_entry['entry'])}%")

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

        # --- RSI pane ---
        c.create_rectangle(padL, rsi_top, w - padR, rsi_bot, outline=BORDER)
        c.create_text(padL + 2, rsi_top + 6, anchor="w", text="RSI", fill=DIM,
                      font=("Segoe UI", 7))

        def Yr(r):
            return rsi_bot - rsi_h * (max(0.0, min(100.0, r)) / 100.0)

        # 70/30 are the scale-out zones; 50 is the entry centerline.
        c.create_rectangle(padL, rsi_top, w - padR, Yr(70),
                           fill=SCALE_COLOR, outline="", stipple="gray12")
        c.create_rectangle(padL, Yr(30), w - padR, rsi_bot,
                           fill=SCALE_COLOR, outline="", stipple="gray12")
        for lvl in (70, 50, 30):
            y = Yr(lvl)
            col = RSI_COLOR if lvl == 50 else BORDER
            c.create_line(padL, y, w - padR, y, fill=col, dash=(2, 2))
            c.create_text(w - padR + 4, y, anchor="w", text=str(lvl), fill=DIM,
                          font=("Segoe UI", 7))
        rsi_pts = []
        for k in range(vc):
            idx = start + k
            rv = self._rsi[idx] if idx < len(self._rsi) else None
            if rv is not None:
                rsi_pts += [X(k), Yr(rv)]
        if len(rsi_pts) >= 4:
            c.create_line(*rsi_pts, fill=RSI_COLOR, width=1)

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
        for aid in (self._after_id, self._reload_after_id):
            if aid:
                try:
                    self.win.after_cancel(aid)
                except tk.TclError:
                    pass
        self.win.destroy()
