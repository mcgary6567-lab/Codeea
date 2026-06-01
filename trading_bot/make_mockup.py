"""Render a mockup of the redesigned (dark) GUI to PNG.

Preview of the Prometheus AI Crypto Bot window — dark theme, branded header,
tabbed Trade Settings. Run:  python make_mockup.py  ->  gui_mockup.png
"""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from matplotlib.patches import FancyBboxPatch, Rectangle

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "gui_mockup.png")

# Dark palette (matches theme.py)
BG = "#0e1117"
PANEL = "#161b22"
ELEV = "#1c2331"
HEADER = "#0b0e14"
BORDER = "#2a2f3a"
TXT = "#e6edf3"
DIM = "#8b949e"
ACCENT = "#f0883e"
GREEN = "#3fb950"
RED = "#f85149"

fig, ax = plt.subplots(figsize=(13, 8.6))
ax.set_xlim(0, 13)
ax.set_ylim(0, 8.6)
ax.axis("off")
fig.patch.set_facecolor(BG)


def panel(x, y, w, h, title=None):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.05",
                                fc=PANEL, ec=BORDER, lw=1.4, zorder=2))
    if title:
        ax.text(x + 0.18, y + h - 0.04, title, fontsize=9.5, color=ACCENT,
                fontweight="bold", va="center", ha="left", zorder=4)


def field(x, y, w, label, value="", h=0.34, show=False):
    ax.text(x, y + h / 2, label, fontsize=8.5, va="center", ha="right", color=DIM)
    ax.add_patch(Rectangle((x + 0.1, y), w, h, fc=ELEV, ec=BORDER, lw=1, zorder=3))
    ax.text(x + 0.22, y + h / 2, ("•" * 16 if show else value), fontsize=8.5,
            va="center", ha="left", color=TXT, zorder=4)


def button(x, y, w, h, text, fc, tc="white", fs=10):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.05",
                                fc=fc, ec="none", zorder=3))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color=tc,
            fontsize=fs, fontweight="bold", zorder=4)


# window frame
ax.add_patch(Rectangle((0.12, 0.12), 12.76, 8.36, fc=BG, ec="#000000", lw=1.5, zorder=1))

# ===== header =====
ax.add_patch(Rectangle((0.12, 7.7), 12.76, 0.78, fc=HEADER, ec="none", zorder=2))
try:
    logo = plt.imread(os.path.join(HERE, "logo.png"))
    ab = AnnotationBbox(OffsetImage(logo, zoom=0.16), (0.55, 8.09), frameon=False, zorder=5)
    ax.add_artist(ab)
except Exception:
    pass
ax.text(0.95, 8.18, "Prometheus AI Crypto Bot", fontsize=15, fontweight="bold", color=TXT, va="center", zorder=5)
ax.text(0.97, 7.88, "v1.0.0", fontsize=8, color=DIM, va="center", zorder=5)
# live status (right)
ax.text(12.7, 8.09, "feed: live ●", color=GREEN, fontsize=8.5, ha="right", va="center", zorder=5)
ax.text(7.0, 8.09, "● Connected", color=GREEN, fontsize=10, fontweight="bold", va="center", zorder=5)
ax.text(8.5, 8.09, "·  Bybit   ·  $12,504.23   ·  PnL +$320.45", color=TXT, fontsize=9, va="center", zorder=5)

# ===== LEFT: API =====
panel(0.35, 5.5, 6.0, 1.95, title="API Settings")
field(2.0, 6.95, 3.9, "Select Exchange:", "Bybit  ▾")
field(2.0, 6.5, 3.9, "API Key:", show=True)
field(2.0, 6.05, 3.9, "API Secret:", show=True)
button(1.7, 5.6, 1.9, 0.36, "Connect", GREEN)
button(3.9, 5.6, 1.9, 0.36, "Disconnect", RED)

# BUY / SELL
button(0.45, 4.65, 2.85, 0.66, "BUY", GREEN, fs=20)
button(3.45, 4.65, 2.85, 0.66, "SELL", RED, fs=20)

# Open positions
panel(0.35, 0.4, 6.0, 4.05, title="Open Positions")
ax.text(0.55, 4.05, "Manual symbol:", fontsize=8.5, color=DIM, va="center")
ax.add_patch(Rectangle((1.85, 3.9), 1.2, 0.3, fc=ELEV, ec=BORDER, lw=1, zorder=3))
ax.text(1.95, 4.05, "BTC/USDT", fontsize=8, va="center", color=TXT, zorder=4)
ax.text(3.25, 4.05, "Mark: 67,512.0", fontsize=9, fontweight="bold", color=ACCENT, va="center", zorder=4)
button(5.0, 3.9, 1.2, 0.3, "Refresh Now", ELEV, TXT, fs=7.5)
cols = ["Pair", "Type", "Size", "Entry", "Current", "PnL", "Status"]
cx = [0.55, 1.7, 2.5, 3.2, 4.0, 4.9, 5.5]
ax.add_patch(Rectangle((0.45, 3.42), 5.8, 0.3, fc=HEADER, ec=BORDER, lw=1, zorder=3))
for c, x in zip(cols, cx):
    ax.text(x, 3.57, c, fontsize=7.6, fontweight="bold", color=DIM, va="center", zorder=4)
rows = [
    ("BTC/USDT", "Long", "0.001", "67,500", "68,200", "+7.00", "Active", GREEN),
    ("ETH/USDT", "Short", "0.05", "3,200", "3,150", "+2.50", "Active", GREEN),
    ("ADA/USDT", "Long", "100", "0.45", "0.47", "+2.00", "Active", GREEN),
    ("SOL/USDT", "Long", "2", "150", "146", "-8.00", "Active", RED),
]
for i, row in enumerate(rows):
    yy = 3.1 - i * 0.3
    for j, (val, x) in enumerate(zip(row[:7], cx)):
        ax.text(x, yy, val, fontsize=7.4, color=(row[7] if j == 5 else TXT), va="center", zorder=4,
                fontweight="bold" if j == 5 else "normal")
button(0.5, 0.52, 1.5, 0.32, "Close Selected", ELEV, TXT, fs=7.5)
button(2.1, 0.52, 1.9, 0.32, "PANIC: Close All", RED, fs=8)

# ===== RIGHT: Trade Settings (tabs) =====
panel(6.55, 3.95, 6.05, 3.5, title="Trade Settings")
tabs = [("Execution", True), ("Modes & Risk", False), ("Webhook & Alerts", False)]
tx = 6.75
for name, active in tabs:
    w = 0.2 + 0.105 * len(name)
    ax.add_patch(Rectangle((tx, 6.95), w, 0.3, fc=ELEV if active else PANEL, ec=BORDER, lw=1, zorder=3))
    ax.text(tx + w / 2, 7.1, name, fontsize=8, fontweight="bold" if active else "normal",
            color=ACCENT if active else DIM, ha="center", va="center", zorder=4)
    tx += w + 0.12
field(8.1, 6.5, 1.9, "Trade Size:", "0.001")
ax.text(10.25, 6.67, "base (BTC)", fontsize=8, color=DIM, va="center")
field(8.1, 6.05, 3.2, "Sizing mode:", "Risk % per trade (stop-based)  ▾")
ax.text(6.8, 5.73, "Risk %:", fontsize=8.5, color=DIM, va="center")
ax.add_patch(Rectangle((7.35, 5.57), 0.55, 0.3, fc=ELEV, ec=BORDER, lw=1, zorder=3))
ax.text(7.45, 5.73, "1.0", fontsize=8.5, va="center", color=TXT, zorder=4)
ax.text(8.05, 5.73, "☑ Auto-place SL/TP (scale out TP1/TP2)", fontsize=8.5, color=TXT, va="center")
ax.plot([6.8, 12.4], [5.38, 5.38], color=BORDER, lw=1, zorder=3)
field(8.1, 4.95, 1.0, "Order type:", "limit ▾")
ax.text(9.3, 5.12, "Limit px:", fontsize=8.5, color=DIM, va="center")
ax.add_patch(Rectangle((9.95, 4.95), 1.0, 0.3, fc=ELEV, ec=BORDER, lw=1, zorder=3))
ax.text(10.05, 5.12, "67,000", fontsize=8.5, va="center", color=TXT, zorder=4)
field(8.1, 4.5, 0.55, "Leverage:", "5")
ax.text(8.9, 4.67, "x   Margin:", fontsize=8.5, color=DIM, va="center")
ax.add_patch(Rectangle((9.85, 4.5), 1.05, 0.3, fc=ELEV, ec=BORDER, lw=1, zorder=3))
ax.text(9.95, 4.67, "isolated ▾", fontsize=8.5, va="center", color=TXT, zorder=4)
button(6.75, 4.02, 2.7, 0.32, "Save Settings (encrypted)", ELEV, TXT, fs=8)
button(9.6, 4.02, 2.9, 0.32, "Analytics", ACCENT, "#1a1100", fs=8)

# Trade log
panel(6.55, 0.4, 6.05, 3.4, title="Trade Log")
lcols = ["Time", "Signal", "Pair", "Status", "Message"]
lx = [6.75, 7.6, 8.5, 9.5, 10.6]
ax.add_patch(Rectangle((6.65, 3.18), 5.85, 0.3, fc=HEADER, ec=BORDER, lw=1, zorder=3))
for c, x in zip(lcols, lx):
    ax.text(x, 3.33, c, fontsize=7.6, fontweight="bold", color=DIM, va="center", zorder=4)
logs = [
    ("12:45:17", "BUY", "BTC/USDT", "Filled", "LIMIT BUY 0.001 @ 67000", GREEN),
    ("12:45:17", "SL", "BTC/USDT", "OK", "SL set 0.001 @ 66500", TXT),
    ("12:45:17", "TP", "BTC/USDT", "OK", "TP 0.0005 @ 68200 / @ 69000", TXT),
    ("12:42:01", "TP1", "BTC/USDT", "Event", "Stop moved to breakeven @ 67000", ACCENT),
    ("12:40:02", "—", "ETH/USDT", "Blocked", "cooldown active (30s)", RED),
    ("11:30:05", "CLOSE", "ADA/USDT", "Closed", "Realized PnL +4.20", GREEN),
]
for i, row in enumerate(logs):
    yy = 2.9 - i * 0.3
    for j, (val, x) in enumerate(zip(row[:5], lx)):
        ax.text(x, yy, val, fontsize=7.2, color=(row[5] if j in (1, 3) else TXT), va="center", zorder=4)
button(6.75, 0.52, 1.3, 0.32, "Export Log", ELEV, TXT, fs=8)
button(8.2, 0.52, 1.2, 0.32, "Clear Log", ELEV, TXT, fs=8)

fig.savefig(OUT, dpi=130, bbox_inches="tight", facecolor=BG)
print("wrote", OUT)
