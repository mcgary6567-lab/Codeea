"""Backtest window — replay the built-in strategy over history, in pure Tkinter.

Fetches candles from a public (keyless) ccxt client (last-N bars *or* a date
range), runs :func:`backtest.run_backtest` with optional fees + funding, and
shows a stats panel + an equity curve drawn on a Canvas, with a CSV export of
every trade. All fetching/computation runs on a worker thread so the UI never
blocks; the authenticated backend client is never touched.
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, List, Optional

try:
    import ccxt

    CCXT_AVAILABLE = True
except ImportError:
    ccxt = None
    CCXT_AVAILABLE = False

import backtest as bt
import strategy
import theme
from config import STRATEGY_TIMEFRAMES, exchange_label
from exchange import normalize_symbol

BG, PANEL, ELEV, BORDER = theme.BG, theme.PANEL, theme.ELEV, theme.BORDER
TXT, DIM, ACCENT = theme.TXT, theme.TXT_DIM, theme.ACCENT
GREEN, RED = theme.GREEN_HL, theme.RED_HL

TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
              "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}
MAX_BARS = 50_000          # safety cap on a fetch

# (label, stat-key, "pnl" colour = green/red by sign)
STAT_CARDS = [
    ("Trades", "trades", DIM), ("Win rate", "win_rate", ACCENT),
    ("Net PnL", "net_pnl", "pnl"), ("Return %", "return_pct", "pnl"),
    ("Profit factor", "profit_factor", ACCENT), ("Avg R", "avg_r", "pnl"),
    ("Max DD %", "max_drawdown", RED), ("Longs / Shorts", "_ls", DIM),
    ("Best", "best", "pnl"), ("Worst", "worst", "pnl"),
    ("Fees", "fees", DIM), ("Funding", "funding", DIM),
]


class BacktestWindow:
    def __init__(
        self,
        root: tk.Tk,
        get_symbols: Callable[[], List[str]],
        get_timeframe: Callable[[], str],
        get_params: Callable[[], "strategy.StrategyParams"],
        get_exchange: Callable[[], str],
        get_market: Callable[[], str],
    ) -> None:
        self.get_params = get_params
        self.get_exchange = get_exchange
        self.get_market = get_market
        syms = [s for s in (self._safe(get_symbols) or []) if s]
        self.symbol = syms[0] if syms else "BTC/USDT"
        self.timeframe = self._safe(get_timeframe) or "1h"
        self.exchange_id = self._safe(get_exchange) or "binance"
        self.market_type = self._safe(get_market) or "spot"

        self._result: Optional[bt.BacktestResult] = None
        self._alive = True

        self.win = tk.Toplevel(root)
        self.win.title("Backtest — Prometheus AI Crypto Bot")
        self.win.geometry("860x640")
        self.win.minsize(720, 560)
        self.win.configure(bg=BG)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build(syms or [self.symbol])

    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return None

    def alive(self) -> bool:
        return self._alive

    def focus(self) -> None:
        try:
            self.win.deiconify(); self.win.lift(); self.win.focus_force()
        except tk.TclError:
            pass

    # -- layout -------------------------------------------------------------
    def _build(self, symbols: List[str]) -> None:
        tk.Label(self.win, text="Backtest", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 13)).pack(anchor="w", padx=12, pady=(10, 4))

        # Row 1: symbol / timeframe.
        r1 = tk.Frame(self.win, bg=BG)
        r1.pack(fill="x", padx=12)
        tk.Label(r1, text="Symbol", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left")
        self.symbol_var = tk.StringVar(value=self.symbol)
        ttk.Combobox(r1, textvariable=self.symbol_var, width=14, values=symbols).pack(
            side="left", padx=(4, 12))
        tk.Label(r1, text="Timeframe", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left")
        self.tf_var = tk.StringVar(value=self.timeframe)
        ttk.Combobox(r1, textvariable=self.tf_var, width=6, state="readonly",
                     values=STRATEGY_TIMEFRAMES).pack(side="left", padx=(4, 12))

        # Row 2: depth mode (last-N OR date range).
        r2 = tk.Frame(self.win, bg=BG)
        r2.pack(fill="x", padx=12, pady=(8, 0))
        self.depth_mode = tk.StringVar(value="bars")
        tk.Radiobutton(r2, text="Last", variable=self.depth_mode, value="bars",
                       bg=BG, fg=TXT, selectcolor=ELEV, activebackground=BG,
                       font=("Segoe UI", 9)).pack(side="left")
        self.bars_var = tk.StringVar(value="2000")
        tk.Entry(r2, textvariable=self.bars_var, width=7, bg=ELEV, fg=TXT,
                 insertbackground=TXT, relief="flat").pack(side="left", padx=4)
        tk.Label(r2, text="bars      ", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left")
        tk.Radiobutton(r2, text="Date range", variable=self.depth_mode, value="range",
                       bg=BG, fg=TXT, selectcolor=ELEV, activebackground=BG,
                       font=("Segoe UI", 9)).pack(side="left")
        self.from_var = tk.StringVar()
        self.to_var = tk.StringVar()
        tk.Entry(r2, textvariable=self.from_var, width=11, bg=ELEV, fg=TXT,
                 insertbackground=TXT, relief="flat").pack(side="left", padx=4)
        tk.Label(r2, text="→", bg=BG, fg=DIM).pack(side="left")
        tk.Entry(r2, textvariable=self.to_var, width=11, bg=ELEV, fg=TXT,
                 insertbackground=TXT, relief="flat").pack(side="left", padx=4)
        tk.Label(r2, text="(YYYY-MM-DD)", bg=BG, fg=DIM, font=("Segoe UI", 8)).pack(side="left")

        # Row 3: costs + sizing.
        r3 = tk.Frame(self.win, bg=BG)
        r3.pack(fill="x", padx=12, pady=(8, 0))
        self.costs_var = tk.BooleanVar(value=True)
        tk.Checkbutton(r3, text="Include fees & funding", variable=self.costs_var,
                       bg=BG, fg=TXT, selectcolor=ELEV, activebackground=BG,
                       font=("Segoe UI", 9)).pack(side="left")
        tk.Label(r3, text="Fee %", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left", padx=(10, 2))
        self.fee_var = tk.StringVar(value="0.05")
        tk.Entry(r3, textvariable=self.fee_var, width=6, bg=ELEV, fg=TXT,
                 insertbackground=TXT, relief="flat").pack(side="left")
        tk.Label(r3, text="Funding %/8h", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left", padx=(10, 2))
        self.fund_var = tk.StringVar(value="0.01")
        tk.Entry(r3, textvariable=self.fund_var, width=6, bg=ELEV, fg=TXT,
                 insertbackground=TXT, relief="flat").pack(side="left")
        tk.Label(r3, text="Equity", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left", padx=(10, 2))
        self.equity_var = tk.StringVar(value="10000")
        tk.Entry(r3, textvariable=self.equity_var, width=8, bg=ELEV, fg=TXT,
                 insertbackground=TXT, relief="flat").pack(side="left")
        tk.Label(r3, text="Size %", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left", padx=(10, 2))
        self.size_var = tk.StringVar(value="100")
        tk.Entry(r3, textvariable=self.size_var, width=6, bg=ELEV, fg=TXT,
                 insertbackground=TXT, relief="flat").pack(side="left")

        # Row 4: run / export / status.
        r4 = tk.Frame(self.win, bg=BG)
        r4.pack(fill="x", padx=12, pady=(10, 4))
        self.run_btn = tk.Button(r4, text="Run backtest", command=self._run,
                                 bg=ACCENT, fg="#1a1100", relief="flat", bd=0,
                                 cursor="hand2", font=("Segoe UI Semibold", 9),
                                 activebackground="#ffa057", padx=16, pady=4)
        self.run_btn.pack(side="left")
        self.export_btn = tk.Button(r4, text="Export CSV", command=self._export_csv,
                                    bg=ELEV, fg=TXT, relief="flat", bd=0, cursor="hand2",
                                    font=("Segoe UI", 9), padx=12, pady=4, state="disabled")
        self.export_btn.pack(side="left", padx=8)
        self.status = tk.Label(r4, text="Set options, then Run.", bg=BG, fg=DIM,
                               font=("Segoe UI", 9))
        self.status.pack(side="left", padx=10)

        # Stats grid.
        self.grid = tk.Frame(self.win, bg=BG)
        self.grid.pack(fill="x", padx=12, pady=(4, 6))

        tk.Label(self.win, text="Equity curve", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 10)).pack(anchor="w", padx=12)
        wrap = tk.Frame(self.win, bg=BORDER)
        wrap.pack(fill="both", expand=True, padx=12, pady=(2, 12))
        self.canvas = tk.Canvas(wrap, bg=PANEL, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=1, pady=1)
        self.canvas.bind("<Configure>", lambda e: self._draw_equity())

    # -- run ---------------------------------------------------------------
    def _run(self) -> None:
        if not CCXT_AVAILABLE:
            self.status.config(text="ccxt not installed — can't fetch candles")
            return
        self.symbol = self.symbol_var.get().strip() or self.symbol
        self.timeframe = self.tf_var.get()
        self.run_btn.config(state="disabled", text="Running…")
        self.status.config(text="Fetching candles…")
        threading.Thread(target=self._run_worker, daemon=True).start()

    def _run_worker(self) -> None:
        try:
            candles = self._fetch()
            if len(candles) < 60:
                raise RuntimeError(f"only {len(candles)} candles — widen the range")
            cfg = bt.BacktestConfig(
                start_equity=float(self.equity_var.get() or 10000),
                size_pct=float(self.size_var.get() or 100),
                fee_pct=float(self.fee_var.get() or 0),
                funding_pct_8h=float(self.fund_var.get() or 0),
                apply_costs=self.costs_var.get(),
                bar_seconds=TF_SECONDS.get(self.timeframe, 3600),
                allow_short=(self.market_type == "futures"),
            )
            result = bt.run_backtest(candles, self.get_params(), cfg)
        except Exception as exc:  # noqa: BLE001
            self._post(lambda: self._done_error(str(exc)))
            return
        self._post(lambda: self._done(result, len(candles)))

    def _done_error(self, msg: str) -> None:
        self.run_btn.config(state="normal", text="Run backtest")
        self.status.config(text=f"Error: {msg}")

    def _done(self, result: bt.BacktestResult, n_candles: int) -> None:
        self._result = result
        self.run_btn.config(state="normal", text="Run backtest")
        self.export_btn.config(state="normal" if result.trades else "disabled")
        s = result.stats
        self.status.config(
            text=f"{n_candles} candles · {s['trades']} trades · "
                 f"{s['win_rate']:.0f}% win · net {s['net_pnl']:+.2f}")
        self._render_stats(s)
        self._draw_equity()

    # -- candle fetch (paged) ----------------------------------------------
    def _client(self):
        return getattr(ccxt, self.exchange_id)({
            "enableRateLimit": True,
            "options": {"defaultType": "swap" if self.market_type == "futures" else "spot"},
        })

    def _market_symbol(self) -> str:
        sym = normalize_symbol(self.symbol)
        if self.market_type != "futures" or ":" in sym:
            return sym
        base, _, quote = sym.partition("/")
        return f"{base}/{quote}:{quote}"

    def _fetch(self) -> List[list]:
        client = self._client()
        sym = self._market_symbol()
        ms = int(TF_SECONDS.get(self.timeframe, 3600) * 1000)
        out: List[list] = []
        if self.depth_mode.get() == "range":
            since = self._parse_date(self.from_var.get())
            until = self._parse_date(self.to_var.get(), end=True)
            if since is None:
                raise RuntimeError("enter a 'from' date (YYYY-MM-DD)")
            cur = since
            until = until or client.milliseconds()
            while cur < until and len(out) < MAX_BARS:
                batch = client.fetch_ohlcv(sym, self.timeframe, since=cur, limit=1000)
                if not batch:
                    break
                out += batch
                cur = batch[-1][0] + ms
                if len(batch) < 1000:
                    break
            out = [c for c in out if c[0] <= until]
        else:
            want = max(60, min(MAX_BARS, int(float(self.bars_var.get() or 2000))))
            cur = client.milliseconds() - (want + 5) * ms
            while len(out) < want:
                batch = client.fetch_ohlcv(sym, self.timeframe, since=cur, limit=1000)
                if not batch:
                    break
                out += batch
                cur = batch[-1][0] + ms
                if len(batch) < 1000:
                    break
            out = out[-(want + 1):]
        # Drop the still-forming candle.
        return out[:-1] if len(out) > 1 else out

    @staticmethod
    def _parse_date(text: str, end: bool = False) -> Optional[int]:
        text = (text or "").strip()
        if not text:
            return None
        try:
            t = time.strptime(text, "%Y-%m-%d")
            secs = time.mktime(t) + (86399 if end else 0)
            return int(secs * 1000)
        except ValueError:
            return None

    # -- stats + equity rendering ------------------------------------------
    def _render_stats(self, s: dict) -> None:
        for ch in self.grid.winfo_children():
            ch.destroy()
        cols = 4
        for idx, (label, key, colour) in enumerate(STAT_CARDS):
            if key == "_ls":
                val = f"{s.get('longs', 0)} / {s.get('shorts', 0)}"
                fg = TXT
            else:
                raw = s.get(key, 0.0)
                if key in ("net_pnl", "best", "worst", "fees", "funding"):
                    val = f"{raw:+.2f}" if key in ("net_pnl", "best", "worst") else f"{raw:.2f}"
                elif key in ("win_rate", "return_pct", "max_drawdown"):
                    val = f"{raw:.1f}%"
                elif key == "profit_factor":
                    val = "∞" if raw == float("inf") else f"{raw:.2f}"
                elif key == "avg_r":
                    val = f"{raw:+.2f}"
                else:
                    val = f"{int(raw)}" if isinstance(raw, (int, float)) else str(raw)
                fg = (GREEN if raw >= 0 else RED) if colour == "pnl" else colour
            card = tk.Frame(self.grid, bg=ELEV)
            card.grid(row=idx // cols, column=idx % cols, sticky="ew", padx=3, pady=3)
            self.grid.columnconfigure(idx % cols, weight=1)
            tk.Label(card, text=label, bg=ELEV, fg=DIM, font=("Segoe UI", 8)).pack(
                anchor="w", padx=8, pady=(5, 0))
            tk.Label(card, text=val, bg=ELEV, fg=fg,
                     font=("Segoe UI Semibold", 13)).pack(anchor="w", padx=8, pady=(0, 5))

    def _draw_equity(self) -> None:
        c = self.canvas
        c.delete("all")
        c.update_idletasks()
        w = c.winfo_width() or 820
        h = c.winfo_height() or 300
        eq = self._result.equity if self._result else []
        if len(eq) < 2:
            c.create_text(w // 2, h // 2, text="Run a backtest to see the equity curve",
                          fill=DIM, font=("Segoe UI", 11))
            return
        padL, padR, padT, padB = 10, 64, 12, 22
        ys = [e for _, e in eq]
        lo, hi = min(ys), max(ys)
        span = (hi - lo) or (abs(hi) * 0.01 or 1.0)
        lo -= span * 0.05
        hi += span * 0.05
        span = hi - lo
        plot_w = w - padL - padR
        plot_h = h - padT - padB

        def X(k):
            return padL + plot_w * (k / (len(eq) - 1))

        def Y(v):
            return padT + plot_h * (hi - v) / span

        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            v = lo + span * frac
            y = Y(v)
            c.create_line(padL, y, w - padR, y, fill=BORDER)
            c.create_text(w - padR + 4, y, anchor="w", text=f"{v:,.0f}", fill=DIM,
                          font=("Segoe UI", 8))
        start_eq = eq[0][1]
        c.create_line(padL, Y(start_eq), w - padR, Y(start_eq), fill=DIM, dash=(2, 2))
        pts = []
        for k, (_, v) in enumerate(eq):
            pts += [X(k), Y(v)]
        end_eq = eq[-1][1]
        c.create_line(*pts, fill=(GREEN if end_eq >= start_eq else RED), width=2)

    # -- CSV export --------------------------------------------------------
    def _export_csv(self) -> None:
        if not self._result or not self._result.trades:
            return
        path = filedialog.asksaveasfilename(
            parent=self.win, defaultextension=".csv",
            initialfile=f"backtest_{normalize_symbol(self.symbol).replace('/', '')}_"
                        f"{self.timeframe}.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                fh.write(bt.trades_to_csv(self._result.trades))
            self.status.config(text=f"Exported {len(self._result.trades)} trades → {path}")
        except Exception as exc:  # noqa: BLE001
            self.status.config(text=f"Export failed: {exc}")

    def _post(self, fn) -> None:
        if self._alive:
            try:
                self.win.after(0, fn)
            except tk.TclError:
                pass

    def _on_close(self) -> None:
        self._alive = False
        self.win.destroy()
