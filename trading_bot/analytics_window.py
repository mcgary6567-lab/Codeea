"""Analytics window: trade stats + equity curve.

Pure Tkinter — the equity curve is drawn on a Canvas, so there are no charting
dependencies. Reads everything from the SQLite history DB.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

GREEN = "#2e8b3d"
RED = "#c0392b"


class AnalyticsWindow:
    def __init__(self, root: tk.Tk, history_module) -> None:
        self.history = history_module
        self.win = tk.Toplevel(root)
        self.win.title("Analytics")
        self.win.geometry("640x520")

        self._build_stats()
        self._build_chart()
        self.refresh()

    def _build_stats(self) -> None:
        box = ttk.LabelFrame(self.win, text="Performance", padding=10)
        box.pack(fill="x", padx=10, pady=10)
        self.labels: dict = {}
        rows = [
            ("Total trades", "total"), ("Filled", "filled"), ("Rejected", "rejected"),
            ("Closed", "closed"), ("Wins", "wins"), ("Losses", "losses"),
            ("Win rate", "win_rate"), ("Realized PnL", "realized_pnl"),
            ("Best", "best"), ("Worst", "worst"),
        ]
        for i, (text, key) in enumerate(rows):
            r, c = divmod(i, 2)
            cell = ttk.Frame(box)
            cell.grid(row=r, column=c, sticky="w", padx=10, pady=3)
            ttk.Label(cell, text=text + ":", width=14).pack(side="left")
            lab = tk.Label(cell, text="—", font=("Segoe UI", 10, "bold"))
            lab.pack(side="left")
            self.labels[key] = lab

    def _build_chart(self) -> None:
        box = ttk.LabelFrame(self.win, text="Equity curve (balance over time)", padding=8)
        box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.canvas = tk.Canvas(box, bg="white", height=260, highlightthickness=1, highlightbackground="#ccc")
        self.canvas.pack(fill="both", expand=True)
        ttk.Button(self.win, text="Refresh", command=self.refresh).pack(pady=(0, 10))

    def refresh(self) -> None:
        s = self.history.stats()
        fmt = {
            "win_rate": lambda v: f"{v:.1f}%",
            "realized_pnl": lambda v: f"{v:+.2f}",
            "best": lambda v: f"{v:+.2f}",
            "worst": lambda v: f"{v:+.2f}",
        }
        for key, lab in self.labels.items():
            val = s.get(key, 0)
            lab.config(text=fmt.get(key, lambda v: str(v))(val))
            if key in ("realized_pnl", "best", "worst"):
                lab.config(fg=GREEN if val >= 0 else RED)
        self._draw_curve(self.history.fetch_equity())

    def _draw_curve(self, points) -> None:
        c = self.canvas
        c.delete("all")
        c.update_idletasks()
        w = c.winfo_width() or 600
        h = c.winfo_height() or 260
        pad = 30
        if len(points) < 2:
            c.create_text(w // 2, h // 2, text="Not enough data yet", fill="#999")
            return
        ys = [p[1] for p in points]
        lo, hi = min(ys), max(ys)
        span = (hi - lo) or 1.0
        n = len(points)

        def px(i):
            return pad + (w - 2 * pad) * i / (n - 1)

        def py(val):
            return h - pad - (h - 2 * pad) * (val - lo) / span

        # Axes.
        c.create_line(pad, h - pad, w - pad, h - pad, fill="#ccc")
        c.create_line(pad, pad, pad, h - pad, fill="#ccc")
        c.create_text(pad - 4, py(hi), text=f"{hi:,.0f}", anchor="e", fill="#666", font=("Segoe UI", 8))
        c.create_text(pad - 4, py(lo), text=f"{lo:,.0f}", anchor="e", fill="#666", font=("Segoe UI", 8))

        coords = []
        for i, (_, val) in enumerate(points):
            coords += [px(i), py(val)]
        color = GREEN if ys[-1] >= ys[0] else RED
        c.create_line(*coords, fill=color, width=2, smooth=True)
