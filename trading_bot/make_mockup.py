"""Render a mockup of the actual GUI window to PNG (matplotlib).

Mirrors the real Tkinter layout: status bar, API panel, BUY/SELL, positions,
the tabbed Trade Settings (Execution tab shown), and the trade log.
Run:  python make_mockup.py  ->  gui_mockup.png
"""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_mockup.png")

GREEN = "#2e8b3d"
RED = "#c0392b"
TEAL = "#16a085"
BAR = "#ecf0f1"
PANEL = "#f7f9fa"
LINE = "#c9d2d8"

fig, ax = plt.subplots(figsize=(13, 8.6))
ax.set_xlim(0, 13)
ax.set_ylim(0, 8.6)
ax.axis("off")
fig.patch.set_facecolor("#dfe6ea")


def panel(x, y, w, h, fc=PANEL, ec=LINE, lw=1.4, title=None, r=0.05):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0.02,rounding_size={r}",
                                fc=fc, ec=ec, lw=lw, zorder=2))
    if title:
        ax.text(x + 0.12, y + h - 0.02, f" {title} ", fontsize=9.5, color="#555",
                fontweight="bold", va="center", ha="left",
                bbox=dict(boxstyle="round,pad=0.15", fc=fc, ec="none"), zorder=4)


def field(x, y, w, label, value="", h=0.34, show=False):
    ax.text(x, y + h / 2, label, fontsize=8.5, va="center", ha="right", color="#444")
    ax.add_patch(Rectangle((x + 0.1, y), w, h, fc="white", ec=LINE, lw=1, zorder=3))
    txt = "•" * 16 if show else value
    ax.text(x + 0.2, y + h / 2, txt, fontsize=8.5, va="center", ha="left", color="#222", zorder=4)


def button(x, y, w, h, text, fc, tc="white", fs=10):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.05",
                                fc=fc, ec="none", zorder=3))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color=tc,
            fontsize=fs, fontweight="bold", zorder=4)


# ===== window frame =====
ax.add_patch(FancyBboxPatch((0.15, 0.15), 12.7, 8.3, boxstyle="round,pad=0.02,rounding_size=0.08",
                            fc="#eef2f4", ec="#b6c0c6", lw=1.6, zorder=1))

# ===== title / status bar =====
ax.add_patch(Rectangle((0.15, 7.85), 12.7, 0.6, fc=BAR, ec="none", zorder=2))
ax.text(0.45, 8.15, "●", color=GREEN, fontsize=15, va="center", zorder=3)
ax.text(0.7, 8.15, "Connected", color="#222", fontsize=10, fontweight="bold", va="center", zorder=3)
ax.text(2.1, 8.15, "|  Exchange: BYBIT", color="#333", fontsize=9.5, va="center", zorder=3)
ax.text(4.4, 8.15, "|  Balance: $12,504.23", color="#333", fontsize=9.5, va="center", zorder=3)
ax.text(7.0, 8.15, "|  PnL: +$320.45", color=GREEN, fontsize=9.5, fontweight="bold", va="center", zorder=3)
ax.text(12.6, 8.15, "feed: live ●", color=GREEN, fontsize=8.5, va="center", ha="right", zorder=3)

# ===== LEFT COLUMN =====
# API settings
panel(0.35, 5.55, 6.0, 2.1, title="API Settings")
field(2.0, 7.05, 3.9, "Select Exchange:", "Bybit  ▾")
field(2.0, 6.6, 3.9, "API Key:", show=True)
field(2.0, 6.15, 3.9, "API Secret:", show=True)
button(1.6, 5.65, 2.0, 0.42, "Connect", GREEN)
button(3.8, 5.65, 2.0, 0.42, "Disconnect", RED)

# BUY / SELL
button(0.45, 4.7, 2.85, 0.7, "BUY", GREEN, fs=20)
button(3.45, 4.7, 2.85, 0.7, "SELL", RED, fs=20)

# Open positions
panel(0.35, 0.4, 6.0, 4.1, title="Open Positions")
ax.text(0.55, 4.18, "Manual symbol:", fontsize=8.5, color="#444", va="center")
ax.add_patch(Rectangle((1.85, 4.02), 1.3, 0.32, fc="white", ec=LINE, lw=1, zorder=3))
ax.text(1.95, 4.18, "BTC/USDT", fontsize=8, va="center", zorder=4)
ax.text(3.4, 4.18, "Mark: 67,512.0", fontsize=9, fontweight="bold", color="#222", va="center")
button(5.0, 4.04, 1.2, 0.3, "Refresh Now", "#7f8c8d", fs=7.5)

# table header
cols = ["Pair", "Type", "Size", "Entry", "Current", "PnL", "Status"]
cx = [0.55, 1.7, 2.5, 3.2, 4.0, 4.9, 5.5]
ax.add_patch(Rectangle((0.45, 3.5), 5.8, 0.32, fc="#e4eaed", ec=LINE, lw=1, zorder=3))
for c, x in zip(cols, cx):
    ax.text(x, 3.66, c, fontsize=7.8, fontweight="bold", color="#333", va="center", zorder=4)
rows = [
    ("BTC/USDT", "Long", "0.001", "67,500", "68,200", "+$7.00", "Active", GREEN),
    ("ETH/USDT", "Short", "0.05", "3,200", "3,150", "+$2.50", "Active", GREEN),
    ("ADA/USDT", "Long", "100", "0.45", "0.47", "+$2.00", "Active", GREEN),
    ("SOL/USDT", "Long", "2", "150", "146", "-$8.00", "Active", RED),
]
for i, row in enumerate(rows):
    yy = 3.18 - i * 0.32
    for j, (val, x) in enumerate(zip(row[:7], cx)):
        color = row[7] if j in (5,) else "#222"
        ax.text(x, yy, val, fontsize=7.6, color=color, va="center", zorder=4,
                fontweight="bold" if j == 5 else "normal")
button(0.5, 0.55, 1.6, 0.34, "Close Selected", "#7f8c8d", fs=7.5)
button(2.2, 0.55, 2.0, 0.34, "PANIC: Close All", RED, fs=8)

# ===== RIGHT COLUMN =====
# Trade settings (tabbed)
panel(6.55, 4.0, 6.1, 3.65, title="Trade Settings")
# tabs
tabs = [("Execution", True), ("Modes & Risk", False), ("Webhook & Alerts", False)]
tx = 6.75
for name, active in tabs:
    w = 0.18 + 0.105 * len(name)
    ax.add_patch(Rectangle((tx, 7.05), w, 0.32, fc="white" if active else "#dde4e7",
                           ec=LINE, lw=1, zorder=3))
    ax.text(tx + w / 2, 7.21, name, fontsize=8, fontweight="bold" if active else "normal",
            color="#222" if active else "#666", ha="center", va="center", zorder=4)
    tx += w + 0.12

# Execution tab content
field(8.1, 6.55, 2.0, "Trade Size:", "0.001")
ax.text(10.3, 6.72, "base (BTC)", fontsize=8, color="#777", va="center")
field(8.1, 6.1, 3.3, "Sizing mode:", "Risk % per trade (stop-based)  ▾")
ax.text(6.8, 5.78, "Risk %:", fontsize=8.5, color="#444", va="center")
ax.add_patch(Rectangle((7.35, 5.62), 0.6, 0.3, fc="white", ec=LINE, lw=1, zorder=3))
ax.text(7.45, 5.78, "1.0", fontsize=8.5, va="center", zorder=4)
ax.text(8.15, 5.78, "☑ Auto-place SL/TP (scale out TP1/TP2)", fontsize=8.5, color="#222", va="center")
ax.plot([6.8, 12.4], [5.4, 5.4], color=LINE, lw=1, zorder=3)
field(8.1, 4.95, 1.0, "Order type:", "limit ▾")
ax.text(9.3, 5.12, "Limit px:", fontsize=8.5, color="#444", va="center")
ax.add_patch(Rectangle((9.95, 4.95), 1.0, 0.3, fc="white", ec=LINE, lw=1, zorder=3))
ax.text(10.05, 5.12, "67,000", fontsize=8.5, va="center", zorder=4)
field(8.1, 4.5, 0.6, "Leverage:", "5")
ax.text(9.0, 4.67, "x   Margin:", fontsize=8.5, color="#444", va="center")
ax.add_patch(Rectangle((9.9, 4.5), 1.1, 0.3, fc="white", ec=LINE, lw=1, zorder=3))
ax.text(10.0, 4.67, "isolated ▾", fontsize=8.5, va="center", zorder=4)
button(6.75, 4.06, 2.7, 0.32, "Save Settings (encrypted)", "#34495e", fs=8)
button(9.6, 4.06, 2.9, 0.32, "Analytics", TEAL, fs=8)

# Trade log
panel(6.55, 0.4, 6.1, 3.45, title="Trade Log")
lcols = ["Time", "Signal", "Pair", "Status", "Message"]
lx = [6.75, 7.6, 8.5, 9.5, 10.6]
ax.add_patch(Rectangle((6.65, 3.35), 5.9, 0.3, fc="#e4eaed", ec=LINE, lw=1, zorder=3))
for c, x in zip(lcols, lx):
    ax.text(x, 3.5, c, fontsize=7.8, fontweight="bold", color="#333", va="center", zorder=4)
logs = [
    ("12:45:17", "BUY", "BTC/USDT", "Filled", "LIMIT BUY 0.001 @ 67000", GREEN),
    ("12:45:17", "SL", "BTC/USDT", "OK", "SL set 0.001 @ 66500", "#444"),
    ("12:45:17", "TP", "BTC/USDT", "OK", "TP 0.0005 @ 68200 / @ 69000", "#444"),
    ("12:40:02", "—", "ETH/USDT", "Blocked", "cooldown active (30s)", RED),
    ("11:30:05", "CLOSE", "ADA/USDT", "Closed", "Realized PnL +4.20", GREEN),
    ("11:08:29", "SELL", "SOL/USDT", "Simulated", "SIMULATED SELL 2 SOL/USDT", "#888"),
]
for i, row in enumerate(logs):
    yy = 3.05 - i * 0.3
    for j, (val, x) in enumerate(zip(row[:5], lx)):
        color = row[5] if j in (1, 3) else "#222"
        ax.text(x, yy, val, fontsize=7.3, color=color, va="center", zorder=4)
button(6.75, 0.55, 1.3, 0.32, "Export Log", "#7f8c8d", fs=8)
button(8.2, 0.55, 1.2, 0.32, "Clear Log", "#7f8c8d", fs=8)

ax.text(6.5, 8.62, "TradingView Trading Bot  v1.0.0", fontsize=12, fontweight="bold",
        ha="center", color="#222", zorder=5)

fig.savefig(OUT, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
print("wrote", OUT)
