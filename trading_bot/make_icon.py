"""Generate the app icon (icon.ico + icon.png) with matplotlib + Pillow.

A simple dark badge with a green up-candle and an up arrow. Run:
    python make_icon.py
"""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Polygon, Rectangle
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PNG = os.path.join(HERE, "icon.png")
ICO = os.path.join(HERE, "icon.ico")

GREEN = "#2ecc71"
DARK = "#1b2733"
RED = "#e74c3c"

fig, ax = plt.subplots(figsize=(4, 4), dpi=64)
ax.set_xlim(0, 10)
ax.set_ylim(0, 10)
ax.axis("off")

# Rounded dark background.
ax.add_patch(FancyBboxPatch((0.4, 0.4), 9.2, 9.2, boxstyle="round,pad=0.02,rounding_size=1.6",
                            fc=DARK, ec="#0d141b", lw=2, zorder=1))

# Two small candles + one big green candle.
def candle(x, body_lo, body_hi, wick_lo, wick_hi, color, w=0.9):
    ax.add_patch(Rectangle((x - 0.06, wick_lo), 0.12, wick_hi - wick_lo, fc=color, ec="none", zorder=2))
    ax.add_patch(Rectangle((x - w / 2, body_lo), w, body_hi - body_lo, fc=color, ec="none", zorder=3))

candle(2.4, 2.2, 3.4, 1.7, 3.9, RED)
candle(3.9, 3.2, 4.6, 2.8, 5.0, GREEN)
candle(5.4, 4.4, 6.4, 3.9, 6.9, GREEN)

# Up arrow.
ax.add_patch(Polygon([[6.6, 4.0], [8.2, 4.0], [8.2, 6.2], [9.0, 6.2],
                      [7.4, 8.4], [5.8, 6.2], [6.6, 6.2]], closed=True,
                     fc=GREEN, ec="none", zorder=4))

fig.savefig(PNG, dpi=64, transparent=True, bbox_inches="tight", pad_inches=0)
print("wrote", PNG)

# Multi-resolution .ico for Windows.
img = Image.open(PNG).convert("RGBA")
img.save(ICO, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", ICO)
