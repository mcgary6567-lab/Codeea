"""Generate the Prometheus brand logo + Windows icon (placeholder).

Network policy blocks fetching the official logo in this environment, so this
draws a clean Prometheus-style flame badge. Replace logo.png / icon.ico with the
official artwork any time — the app just loads whatever is in this folder.

Run:  python make_logo.py   ->  logo.png (256px) + icon.ico
"""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch, Polygon
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PNG = os.path.join(HERE, "logo.png")
ICO = os.path.join(HERE, "icon.ico")

BADGE = "#12161f"
BADGE_EDGE = "#f0883e"


def flame(cx, scale, y0, color, wig=0.0):
    """A symmetric flame polygon centred at cx, rising from y0."""
    ys = np.linspace(0, 1, 60)
    # width tapers to a point at the top, fattest ~35% up
    w = np.sin(np.pi * ys ** 0.62) * scale * (1 - ys * 0.15)
    wobble = wig * np.sin(ys * 9) * (1 - ys)  # subtle licking edge
    left = [(cx - w[i] + wobble[i], y0 + ys[i]) for i in range(len(ys))]
    right = [(cx + w[i] + wobble[i], y0 + ys[i]) for i in range(len(ys) - 1, -1, -1)]
    return Polygon(left + right, closed=True, fc=color, ec="none", zorder=5)


fig, ax = plt.subplots(figsize=(4, 4), dpi=64)
ax.set_xlim(0, 1)
ax.set_ylim(0, 1)
ax.axis("off")

# Rounded dark badge.
ax.add_patch(FancyBboxPatch((0.06, 0.06), 0.88, 0.88,
                            boxstyle="round,pad=0.0,rounding_size=0.18",
                            fc=BADGE, ec=BADGE_EDGE, lw=3, zorder=1))

# Layered flame: deep -> orange -> gold -> white core.
ax.add_patch(flame(0.5, 0.30, 0.20, "#b5360a", wig=0.012))
ax.add_patch(flame(0.5, 0.235, 0.235, "#f0883e", wig=0.010))
ax.add_patch(flame(0.5, 0.155, 0.275, "#ffc94d", wig=0.006))
ax.add_patch(flame(0.5, 0.075, 0.315, "#fff3c4"))

fig.savefig(PNG, dpi=64, transparent=True, bbox_inches="tight", pad_inches=0)
print("wrote", PNG)

img = Image.open(PNG).convert("RGBA")
img.save(ICO, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", ICO)
