"""Render a per-exchange setup walkthrough to PNG (matplotlib).

Run:  python make_setup_guide.py  ->  setup_guide.png
"""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup_guide.png")

BLUE = "#2962ff"
GREEN = "#2e8b3d"
ORANGE = "#f39c12"
RED = "#c0392b"
GREY = "#7f8c8d"
DARK = "#2c3e50"

fig, ax = plt.subplots(figsize=(14, 9))
ax.set_xlim(0, 14)
ax.set_ylim(0, 9)
ax.axis("off")
fig.patch.set_facecolor("white")

ax.text(7, 8.65, "Per-Exchange Setup Walkthrough", ha="center", fontsize=19,
        fontweight="bold", color="#222")
ax.text(7, 8.28, "Get from zero to safely auto-trading in 6 steps",
        ha="center", fontsize=11, color="#666", fontstyle="italic")


def step(x, y, w, h, num, title, body, color):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.04,rounding_size=0.08",
                                fc="white", ec=color, lw=2, zorder=2))
    ax.add_patch(plt.Circle((x + 0.4, y + h - 0.4), 0.26, color=color, zorder=3))
    ax.text(x + 0.4, y + h - 0.4, num, ha="center", va="center", color="white",
            fontsize=12, fontweight="bold", zorder=4)
    ax.text(x + 0.8, y + h - 0.4, title, ha="left", va="center", fontsize=11,
            fontweight="bold", color="#222", zorder=4)
    ax.text(x + 0.25, y + h - 0.85, body, ha="left", va="top", fontsize=8.7,
            color="#333", zorder=4, linespacing=1.45)


def arrow(p1, p2, color=GREY):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle="-|>", mutation_scale=18,
                                 lw=2, color=color, zorder=1))


# Top row of steps
sy = 5.5
sw, sh = 4.1, 2.2
step(0.4, sy, sw, sh, "1", "Create API keys", color=BLUE, body=(
    "On the exchange website:\n"
    "• New API key with TRADE / Futures\n"
    "  enabled\n"
    "• DISABLE withdrawals\n"
    "• IP-whitelist your PC if possible"))
step(4.95, sy, sw, sh, "2", "Note the credentials", color=BLUE, body=(
    "Copy:\n"
    "• API Key\n"
    "• API Secret\n"
    "• API Passphrase  (only OKX,\n"
    "  KuCoin, Bitget — see table)"))
step(9.5, sy, sw, sh, "3", "Launch & set a PIN", color=GREEN, body=(
    "• Run run.bat (installs deps)\n"
    "• Create a PIN on first launch\n"
    "  (encrypts your saved keys)\n"
    "• Pick exchange in the dropdown"))

arrow((4.5, sy + sh / 2), (4.95, sy + sh / 2), BLUE)
arrow((9.05, sy + sh / 2), (9.5, sy + sh / 2), GREEN)

# down arrow
arrow((11.55, sy), (11.55, 4.7), GREY)

# Bottom row
by = 2.3
step(9.5, by, sw, sh, "4", "Connect", color=GREEN, body=(
    "• Paste key / secret (+ passphrase)\n"
    "• Click Connect\n"
    "• Dot turns green; balance &\n"
    "  positions start streaming live"))
step(4.95, by, sw, sh, "5", "Dry-run in Safe Mode", color=ORANGE, body=(
    "• Modes & Risk tab → Safe Mode ON\n"
    "• Test BUY/SELL + a webhook alert\n"
    "• Orders are SIMULATED — verify\n"
    "  sizing, SL/TP, log look right"))
step(0.4, by, sw, sh, "6", "Go live (carefully)", color=RED, body=(
    "• Set guardrails: daily loss, max\n"
    "  open, cooldown\n"
    "• Safe Mode OFF, small size first\n"
    "• Start Webhook → point TradingView\n"
    "  alert at http://host:8723/"))

arrow((9.5, by + sh / 2), (9.05, by + sh / 2), ORANGE)
arrow((4.95, by + sh / 2), (4.5, by + sh / 2), RED)

# Passphrase table
ax.add_patch(FancyBboxPatch((0.4, 0.25), 13.2, 1.55, boxstyle="round,pad=0.03,rounding_size=0.05",
                            fc="#f7f9fa", ec="#c9d2d8", lw=1.4, zorder=2))
ax.text(0.65, 1.55, "API passphrase required?", fontsize=10, fontweight="bold", color=DARK, zorder=4)
exs = [
    ("Binance", "No", GREEN), ("Bybit", "No", GREEN), ("OKX", "Yes", ORANGE),
    ("KuCoin", "Yes", ORANGE), ("Bitget", "Yes", ORANGE),
]
colw = 2.55
for i, (name, need, col) in enumerate(exs):
    x = 0.7 + i * colw
    ax.add_patch(FancyBboxPatch((x, 0.5), colw - 0.2, 0.85,
                                boxstyle="round,pad=0.02,rounding_size=0.05",
                                fc="white", ec=col, lw=1.6, zorder=3))
    ax.text(x + (colw - 0.2) / 2, 1.12, name, ha="center", fontsize=10,
            fontweight="bold", color="#222", zorder=4)
    ax.text(x + (colw - 0.2) / 2, 0.75, f"passphrase: {need}", ha="center",
            fontsize=8.5, color=col, zorder=4)

fig.savefig(OUT, dpi=130, bbox_inches="tight", facecolor="white")
print("wrote", OUT)
