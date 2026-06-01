"""Analytics window: trade stats + equity curve (dark, card-style).

Pure Tkinter — the equity curve is drawn on a Canvas, so there are no charting
dependencies. Reads everything from the SQLite history DB.
"""

from __future__ import annotations

import csv
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import theme

BG = theme.BG
PANEL = theme.PANEL
ELEV = theme.ELEV
BORDER = theme.BORDER
TXT = theme.TXT
DIM = theme.TXT_DIM
ACCENT = theme.ACCENT
GREEN = theme.GREEN_HL
RED = theme.RED_HL

# (label, stat-key, colour)  — colour "pnl" means green/red by sign.
TILES = [
    ("Total trades", "total", TXT),
    ("Filled", "filled", GREEN),
    ("Rejected", "rejected", RED),
    ("Closed", "closed", TXT),
    ("Win rate", "win_rate", ACCENT),
    ("Wins", "wins", GREEN),
    ("Losses", "losses", RED),
    ("Realized PnL", "realized_pnl", "pnl"),
    ("Best", "best", "pnl"),
    ("Worst", "worst", "pnl"),
]
COLS = 5


class AnalyticsWindow:
    def __init__(self, root: tk.Tk, history_module) -> None:
        self.history = history_module
        self.win = tk.Toplevel(root)
        self.win.title("Analytics — Prometheus AI Crypto Bot")
        self.win.geometry("780x600")
        self.win.minsize(660, 520)
        self.win.configure(bg=BG)

        self._vals: dict = {}      # key -> (value Label, colour rule)
        self._last_points = []
        self._build()
        self.refresh()

    # -- layout -------------------------------------------------------------
    def _build(self) -> None:
        tk.Label(self.win, text="Performance", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 14)).pack(anchor="w", padx=16, pady=(14, 8))

        grid = tk.Frame(self.win, bg=BG)
        grid.pack(fill="x", padx=11)
        for i in range(COLS):
            grid.columnconfigure(i, weight=1, uniform="t")

        for idx, (label, key, colour) in enumerate(TILES):
            r, c = divmod(idx, COLS)
            cell = tk.Frame(grid, bg=ELEV)
            cell.grid(row=r, column=c, sticky="nsew", padx=5, pady=5)
            tk.Label(cell, text=label.upper(), bg=ELEV, fg=DIM,
                     font=("Segoe UI", 8)).pack(anchor="w", padx=12, pady=(9, 0))
            val = tk.Label(cell, text="—", bg=ELEV,
                           fg=(GREEN if colour == "pnl" else colour),
                           font=("Segoe UI Semibold", 17))
            val.pack(anchor="w", padx=12, pady=(0, 10))
            self._vals[key] = (val, colour)

        tk.Label(self.win, text="Equity curve  (balance over time)", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 13)).pack(anchor="w", padx=16, pady=(14, 6))
        wrap = tk.Frame(self.win, bg=BORDER)   # 1px border around the chart
        wrap.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.canvas = tk.Canvas(wrap, bg=PANEL, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=1, pady=1)
        self.canvas.bind("<Configure>", lambda e: self._draw_curve(self._last_points))

        bar = tk.Frame(self.win, bg=BG)
        bar.pack(fill="x", padx=14, pady=(0, 12))
        tk.Button(bar, text="Refresh", command=self.refresh, bg=ACCENT, fg="#1a1100",
                  relief="flat", bd=0, cursor="hand2", font=("Segoe UI Semibold", 10),
                  activebackground="#ffa057", padx=16, pady=5).pack(side="right")
        tk.Button(bar, text="Export CSV", command=self._export_csv, bg=ELEV, fg=TXT,
                  relief="flat", bd=0, cursor="hand2", font=("Segoe UI Semibold", 10),
                  activebackground=BORDER, padx=16, pady=5).pack(side="right", padx=8)

    def _export_csv(self) -> None:
        rows = self.history.fetch_trades()
        if not rows:
            messagebox.showinfo("Export", "No trades to export yet.", parent=self.win)
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            title="Export trade history (CSV)", parent=self.win)
        if not path:
            return
        cols = ["time", "source", "symbol", "side", "kind", "size", "price",
                "status", "pnl", "message"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in rows:
                t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get("ts", 0)))
                w.writerow([t, r.get("source"), r.get("symbol"), r.get("side"),
                            r.get("kind"), r.get("size"), r.get("price"),
                            r.get("status"), r.get("pnl"), r.get("message")])
        messagebox.showinfo("Export", f"Exported {len(rows)} trades to:\n{path}", parent=self.win)

    # -- data ---------------------------------------------------------------
    def refresh(self) -> None:
        s = self.history.stats()
        for key, (val_lbl, colour) in self._vals.items():
            raw = s.get(key, 0)
            if key == "win_rate":
                val_lbl.config(text=f"{raw:.1f}%")
            elif colour == "pnl":
                val_lbl.config(text=f"{raw:+.2f}", fg=(GREEN if raw >= 0 else RED))
            else:
                val_lbl.config(text=str(raw))
        self._last_points = self.history.fetch_equity()
        self._draw_curve(self._last_points)

    def _draw_curve(self, points) -> None:
        c = self.canvas
        c.delete("all")
        c.update_idletasks()
        w = c.winfo_width() or 720
        h = c.winfo_height() or 280
        pad = 40
        if not points or len(points) < 2:
            c.create_text(w // 2, h // 2,
                          text="No equity data yet — it plots as trades close",
                          fill=DIM, font=("Segoe UI", 10))
            return
        ys = [p[1] for p in points]
        lo, hi = min(ys), max(ys)
        span = (hi - lo) or max(abs(hi), 1.0)
        n = len(points)

        def px(i):
            return pad + (w - 2 * pad) * i / (n - 1)

        def py(v):
            return h - pad - (h - 2 * pad) * (v - lo) / span

        for frac in (0.0, 0.5, 1.0):
            val = lo + span * frac
            y = py(val)
            c.create_line(pad, y, w - pad, y, fill=BORDER)
            c.create_text(pad - 6, y, text=f"{val:,.0f}", anchor="e", fill=DIM,
                          font=("Segoe UI", 8))

        up = ys[-1] >= ys[0]
        line = GREEN if up else RED
        coords = []
        for i, (_, v) in enumerate(points):
            coords += [px(i), py(v)]
        c.create_polygon(coords + [px(n - 1), h - pad, px(0), h - pad],
                         fill=line, outline="", stipple="gray12")
        c.create_line(*coords, fill=line, width=2, smooth=True)
        c.create_oval(px(n - 1) - 3, py(ys[-1]) - 3, px(n - 1) + 3, py(ys[-1]) + 3,
                      fill=line, outline="")
        c.create_text(w - pad, py(ys[-1]) - 12, text=f"{ys[-1]:,.2f}", anchor="e",
                      fill=line, font=("Segoe UI Semibold", 9))
