"""Render an architecture / data-flow diagram for the trading bot to PNG.

Pure matplotlib (no graphviz needed). Run:  python make_diagram.py
Outputs architecture.png next to this file.
"""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "architecture.png")

# Palette
C_TV = "#2962ff"      # TradingView / blue
C_EXCH = "#f39c12"    # exchange / orange
C_GUI = "#16a085"     # GUI / teal
C_CORE = "#8e44ad"    # backend core / purple
C_SAFE = "#c0392b"    # safety / red
C_DATA = "#2c3e50"    # storage / dark
WHITE = "white"


def box(ax, x, y, w, h, text, color, fc=None, fs=10, bold=True):
    fc = fc or color
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.06",
            linewidth=1.6, edgecolor=color, facecolor=fc, alpha=1.0, zorder=2,
        )
    )
    ax.text(
        x + w / 2, y + h / 2, text, ha="center", va="center",
        fontsize=fs, color=WHITE if fc == color else "#222",
        fontweight="bold" if bold else "normal", zorder=3, wrap=True,
    )


def arrow(ax, p1, p2, color="#555", style="-|>", lw=1.8, ls="-", rad=0.0):
    ax.add_patch(
        FancyArrowPatch(
            p1, p2, arrowstyle=style, mutation_scale=16, lw=lw,
            color=color, linestyle=ls, zorder=1,
            connectionstyle=f"arc3,rad={rad}",
        )
    )


def label(ax, x, y, text, color="#555", fs=8.5, style="italic"):
    ax.text(x, y, text, ha="center", va="center", fontsize=fs, color=color,
            fontstyle=style, zorder=4,
            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.85))


fig, ax = plt.subplots(figsize=(15, 9.5))
ax.set_xlim(0, 15)
ax.set_ylim(0, 9.5)
ax.axis("off")

ax.text(7.5, 9.15, "TradingView Trading Bot — How It Works",
        ha="center", fontsize=19, fontweight="bold", color="#222")
ax.text(7.5, 8.75, "Signal → Webhook → Guardrails → Sizing → Order → Bracket → Live monitoring",
        ha="center", fontsize=11, color="#666", fontstyle="italic")

# ---- Row 1: signal sources ----
box(ax, 0.4, 7.4, 3.0, 1.0, "Dip2Green PRO\nindicator (Pine)", C_TV)
box(ax, 0.4, 6.1, 3.0, 0.95, "TradingView Alert\n(JSON webhook)", C_TV, fc="#bbd0ff")
box(ax, 11.6, 7.4, 3.0, 1.0, "Manual BUY / SELL\n(GUI buttons)", C_GUI)

# ---- Webhook receiver ----
box(ax, 4.2, 6.25, 2.7, 1.05, "Webhook Server\n:8723  (passphrase)", C_TV, fc="#dbe7ff")

# ---- Backend worker (center) ----
box(ax, 4.0, 3.55, 7.0, 2.15, "", C_CORE, fc="#f3e9fb")
ax.text(7.5, 5.5, "Backend Worker  (single thread — all exchange I/O serialized)",
        ha="center", fontsize=10.5, fontweight="bold", color=C_CORE, zorder=3)
box(ax, 4.25, 4.5, 1.95, 0.7, "1 · Guardrails\n(loss/cooldown/dedupe)", C_SAFE, fc="#f6d6d2", fs=8)
box(ax, 6.35, 4.5, 1.55, 0.7, "2 · Risk sizing\n(stop-based)", C_CORE, fs=8)
box(ax, 8.05, 4.5, 1.45, 0.7, "3 · Lev/Margin", C_CORE, fc="#e8d6f3", fs=8)
box(ax, 9.65, 4.5, 1.15, 0.7, "4 · Order\nmkt/limit", C_CORE, fs=8)
box(ax, 4.25, 3.7, 2.6, 0.62, "5 · Auto SL / TP  (scale-out)", C_SAFE, fc="#f6d6d2", fs=8.5)
box(ax, 7.0, 3.7, 1.9, 0.62, "Close / PANIC all", C_SAFE, fs=8.5)
box(ax, 9.05, 3.7, 1.75, 0.62, "Notifier\nsound/Telegram", C_GUI, fs=8)

# ---- Exchange ----
box(ax, 11.7, 4.45, 2.9, 1.25,
    "Exchange  (ccxt)\nBinance · Bybit · OKX\nKuCoin · Bitget", C_EXCH, fs=9.5)

# ---- Price feed ----
box(ax, 11.7, 2.55, 2.9, 1.15,
    "Price Feed\nWebSocket (ccxt.pro)\n→ REST fallback", C_EXCH, fc="#fde3bf", fs=9)

# ---- GUI (bottom) ----
box(ax, 0.4, 0.5, 14.2, 1.5, "", C_GUI, fc="#e3f6f1")
ax.text(7.5, 1.72, "GUI  (Tkinter — updated from a thread-safe queue, never blocks)",
        ha="center", fontsize=10.5, fontweight="bold", color=C_GUI, zorder=3)
box(ax, 0.7, 0.7, 2.4, 0.7, "Status bar\nconn · bal · PnL", C_GUI, fc="white", fs=8)
box(ax, 3.3, 0.7, 2.4, 0.7, "Open Positions\n(green/red, live)", C_GUI, fc="white", fs=8)
box(ax, 5.9, 0.7, 2.2, 0.7, "Trade Log", C_GUI, fc="white", fs=8)
box(ax, 8.3, 0.7, 2.3, 0.7, "Analytics\nwin% · equity", C_GUI, fc="white", fs=8)
box(ax, 10.8, 0.7, 1.7, 0.7, "Mark price\n(manual sym)", C_GUI, fc="white", fs=8)
box(ax, 12.7, 0.7, 1.7, 0.7, "Settings\n(encrypted)", C_GUI, fc="white", fs=8)

# ---- Storage (left of backend) ----
box(ax, 0.4, 3.9, 3.0, 1.4, "", C_DATA, fc="#e8ecf0")
ax.text(1.9, 5.05, "Local storage", ha="center", fontsize=9.5, fontweight="bold", color=C_DATA, zorder=3)
box(ax, 0.6, 4.5, 2.6, 0.45, "Encrypted keys + PIN", C_DATA, fc="white", fs=8)
box(ax, 0.6, 3.98, 2.6, 0.45, "history.db (SQLite)", C_DATA, fc="white", fs=8)

# ============ arrows ============
arrow(ax, (1.9, 7.4), (1.9, 7.05), C_TV)                       # indicator -> alert
arrow(ax, (3.4, 6.6), (4.2, 6.7), C_TV)                        # alert -> webhook
label(ax, 3.75, 6.95, "POST JSON")
arrow(ax, (5.55, 6.25), (6.2, 5.7), C_TV, rad=-0.1)            # webhook -> backend
arrow(ax, (13.1, 7.4), (9.5, 5.7), C_GUI, rad=0.15)           # manual -> backend
label(ax, 11.0, 6.7, "trade cmd")

# pipeline internal arrows
arrow(ax, (6.2, 4.85), (6.35, 4.85), "#7d5a9c", lw=1.4)
arrow(ax, (7.9, 4.85), (8.05, 4.85), "#7d5a9c", lw=1.4)
arrow(ax, (9.5, 4.85), (9.65, 4.85), "#7d5a9c", lw=1.4)

# backend <-> exchange
arrow(ax, (10.8, 4.95), (11.7, 5.0), C_EXCH)
label(ax, 11.25, 5.25, "orders")
arrow(ax, (11.7, 4.75), (10.8, 4.2), C_EXCH, rad=0.1)
label(ax, 11.2, 4.25, "fills / balance / positions")

# price feed -> backend
arrow(ax, (11.7, 3.0), (11.0, 3.7), C_EXCH, ls="--")
arrow(ax, (14.6, 4.45), (14.6, 3.7), "#d68910", ls="--", rad=0)
label(ax, 13.9, 3.95, "ticks")

# backend -> GUI (queue) and storage
arrow(ax, (7.5, 3.55), (7.5, 2.0), C_GUI, lw=2.0)
label(ax, 8.35, 2.75, "ui_queue: status/positions/log/alerts")
arrow(ax, (4.0, 4.6), (3.4, 4.6), C_DATA, ls="--")
label(ax, 3.7, 4.85, "read keys / write trades")

# price feed -> GUI mark + positions (implied via backend) ; add legend
legend = [
    mpatches.Patch(color=C_TV, label="TradingView / signals"),
    mpatches.Patch(color=C_CORE, label="Backend pipeline"),
    mpatches.Patch(color=C_SAFE, label="Safety / risk control"),
    mpatches.Patch(color=C_EXCH, label="Exchange (ccxt)"),
    mpatches.Patch(color=C_GUI, label="GUI / user"),
    mpatches.Patch(color=C_DATA, label="Local storage"),
]
ax.legend(handles=legend, loc="upper center", bbox_to_anchor=(0.5, 0.045),
          ncol=6, fontsize=9, frameon=False)

plt.tight_layout()
fig.savefig(OUT, dpi=130, bbox_inches="tight", facecolor="white")
print("wrote", OUT)
