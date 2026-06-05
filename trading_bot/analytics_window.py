"""Analytics window: trade stats + equity curve (dark, card-style).

Pure Tkinter — the equity curve is drawn on a Canvas, so there are no charting
dependencies. Reads everything from the SQLite history DB.
"""

from __future__ import annotations

import csv
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

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

    # -- time range ---------------------------------------------------------
    def _range_bounds(self):
        """Return (since, until) epoch seconds for the selected range, or None."""
        sel = self.range_var.get()
        now = time.time()
        if sel == "Today":
            t = time.localtime(now)
            start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
            return start, None
        if sel == "Last 7 days":
            return now - 7 * 86400, None
        if sel == "Last 30 days":
            return now - 30 * 86400, None
        if sel == "Custom":
            since = self._parse_date(self.from_var.get(), end=False)
            until = self._parse_date(self.to_var.get(), end=True)
            return since, until
        return None, None  # All

    @staticmethod
    def _parse_date(text: str, end: bool):
        text = (text or "").strip()
        if not text:
            return None
        try:
            t = time.strptime(text, "%Y-%m-%d")
            secs = time.mktime(t)
            return secs + 86400 if end else secs   # 'to' is inclusive of that day
        except ValueError:
            return None

    # -- layout -------------------------------------------------------------
    def _build(self) -> None:
        tk.Label(self.win, text="Performance", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 14)).pack(anchor="w", padx=16, pady=(14, 4))

        # Date-range filter.
        fr = tk.Frame(self.win, bg=BG)
        fr.pack(fill="x", padx=16, pady=(0, 6))
        tk.Label(fr, text="Range:", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left")
        self.range_var = tk.StringVar(value="All")
        rng = ttk.Combobox(fr, textvariable=self.range_var, width=14, state="readonly",
                           values=["All", "Today", "Last 7 days", "Last 30 days", "Custom"])
        rng.pack(side="left", padx=6)
        rng.bind("<<ComboboxSelected>>", lambda e: self._on_range_change())
        self.from_var = tk.StringVar()
        self.to_var = tk.StringVar()
        self._custom = tk.Frame(fr, bg=BG)   # shown only for Custom
        tk.Label(self._custom, text="From", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left")
        fe = ttk.Entry(self._custom, textvariable=self.from_var, width=11)
        fe.pack(side="left", padx=(4, 8))
        tk.Label(self._custom, text="To", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left")
        te = ttk.Entry(self._custom, textvariable=self.to_var, width=11)
        te.pack(side="left", padx=4)
        tk.Label(self._custom, text="(YYYY-MM-DD)", bg=BG, fg=DIM,
                 font=("Segoe UI", 8)).pack(side="left", padx=4)
        for e in (fe, te):
            e.bind("<Return>", lambda ev: self.refresh())

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

        tk.Label(self.win, text="By symbol", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 13)).pack(anchor="w", padx=16, pady=(14, 4))
        symf = tk.Frame(self.win, bg=BORDER)
        symf.pack(fill="x", padx=14)
        cols = ("symbol", "trades", "wins", "realized")
        self.sym_tree = ttk.Treeview(symf, columns=cols, show="headings", height=5)
        for c, t, w in zip(cols, ("Symbol", "Trades", "Wins", "Realized PnL"), (140, 80, 80, 120)):
            self.sym_tree.heading(c, text=t)
            self.sym_tree.column(c, width=w, anchor="center")
        self.sym_tree.tag_configure("pos", foreground=GREEN)
        self.sym_tree.tag_configure("neg", foreground=RED)
        self.sym_tree.pack(fill="x", padx=1, pady=1)

        tk.Label(self.win, text="Equity curve  (balance over time)", bg=BG, fg=ACCENT,
                 font=("Segoe UI Semibold", 13)).pack(anchor="w", padx=16, pady=(14, 6))
        wrap = tk.Frame(self.win, bg=BORDER)   # 1px border around the chart
        wrap.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.canvas = tk.Canvas(wrap, bg=PANEL, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=1, pady=1)
        self.canvas.bind("<Configure>", lambda e: self._draw_curve(self._last_points))

        bar = tk.Frame(self.win, bg=BG)
        bar.pack(fill="x", padx=14, pady=(0, 12))
        tk.Button(bar, text="🔄 Refresh", command=self.refresh, bg=ACCENT, fg="#1a1100",
                  relief="flat", bd=0, cursor="hand2", font=("Segoe UI Semibold", 10),
                  activebackground="#ffa057", padx=16, pady=5).pack(side="right")
        tk.Button(bar, text="📤 Export CSV", command=self._export_csv, bg=ELEV, fg=TXT,
                  relief="flat", bd=0, cursor="hand2", font=("Segoe UI Semibold", 10),
                  activebackground=BORDER, padx=16, pady=5).pack(side="right", padx=8)
        tk.Button(bar, text="🗑 Clear", command=self._clear, bg=ELEV, fg=RED,
                  relief="flat", bd=0, cursor="hand2", font=("Segoe UI Semibold", 10),
                  activebackground=RED, activeforeground="#ffffff", padx=16, pady=5).pack(side="left")

    def _on_range_change(self) -> None:
        if self.range_var.get() == "Custom":
            self._custom.pack(side="left", padx=8)
        else:
            self._custom.pack_forget()
        self.refresh()

    def _clear(self) -> None:
        since, until = self._range_bounds()
        if since is None and until is None:
            scope = "ALL trade history and the equity curve"
        else:
            scope = f"trade history in the selected range ({self.range_var.get()})"
        if not messagebox.askyesno(
            "Clear analytics",
            f"This permanently deletes {scope}.\n\n"
            "Tip: use Export CSV first if you want a copy.\n\nContinue?",
            icon="warning", parent=self.win):
            return
        self.history.clear(since=since, until=until)
        messagebox.showinfo("Cleared", "Analytics history has been cleared.", parent=self.win)
        self.refresh()

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
        since, until = self._range_bounds()
        s = self.history.stats(since=since, until=until)
        for key, (val_lbl, colour) in self._vals.items():
            raw = s.get(key, 0)
            if key == "win_rate":
                val_lbl.config(text=f"{raw:.1f}%")
            elif colour == "pnl":
                val_lbl.config(text=f"{raw:+.2f}", fg=(GREEN if raw >= 0 else RED))
            else:
                val_lbl.config(text=str(raw))
        for item in self.sym_tree.get_children():
            self.sym_tree.delete(item)
        for r in self.history.stats_by_symbol(since=since, until=until):
            realized = r.get("realized") or 0.0
            self.sym_tree.insert(
                "", "end",
                values=(r.get("symbol"), r.get("trades"), r.get("wins") or 0,
                        f"{realized:+.2f}"),
                tags=("pos" if realized >= 0 else "neg",))

        self._last_points = self.history.fetch_equity(since=since, until=until)
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
