"""Render vertical 9:16 social reel posters (TikTok + Facebook) to PNG.

1080x1920 promo frames built around the app mockup, with example profit
callouts. Run:  python make_reel.py
Outputs ../Marketing Docs/social/{tiktok,facebook}_reel.png

NOTE: profit figures are ILLUSTRATIVE examples and the frame carries a risk
disclaimer — required for ad-policy / consumer-protection compliance when
marketing a real-money trading product.
"""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from matplotlib.patches import FancyBboxPatch

HERE = os.path.dirname(os.path.abspath(__file__))
SOCIAL = os.path.join(HERE, "..", "Marketing Docs", "social")
os.makedirs(SOCIAL, exist_ok=True)

BG = "#0e1117"
PANEL = "#161b22"
ELEV = "#1c2331"
BORDER = "#2a2f3a"
TXT = "#e6edf3"
DIM = "#8b949e"
ACCENT = "#f0883e"
GREEN = "#3fb950"

W, H = 1080, 1920


def _pill(ax, cx, cy, text, fc, tc, fs, pad=26, h=58, bold=True):
    w = pad * 2 + fs * 0.62 * len(text)
    ax.add_patch(FancyBboxPatch((cx - w / 2, cy - h / 2), w, h,
                                boxstyle="round,pad=2,rounding_size=24",
                                fc=fc, ec="none", zorder=6))
    ax.text(cx, cy, text, ha="center", va="center", color=tc, fontsize=fs,
            fontweight="bold" if bold else "normal", zorder=7)


def build(out, hook):
    fig, ax = plt.subplots(figsize=(10.8, 19.2), dpi=100)
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")
    fig.patch.set_facecolor(BG)
    ax.add_patch(FancyBboxPatch((0, 0), W, H, boxstyle="square,pad=0", fc=BG, ec="none", zorder=0))

    # accent glow top
    glow = plt.Circle((300, 1700), 520, color="#241a12", zorder=0)
    ax.add_patch(glow)

    # --- top hook chip ---
    _pill(ax, W / 2, 1852, "● LIVE · AUTO-TRADING 24/7", ELEV, GREEN, 24)

    # --- logo + brand ---
    try:
        logo = plt.imread(os.path.join(HERE, "logo.png"))
        ax.add_artist(AnnotationBbox(OffsetImage(logo, zoom=0.34), (W / 2, 1752),
                                     frameon=False, zorder=6))
    except Exception:
        pass
    ax.text(W / 2, 1650, "PROMETHEUS AI", ha="center", va="center", color=TXT,
            fontsize=36, fontweight="bold", zorder=6)

    # --- hook headline ---
    ax.text(W / 2, 1584, hook, ha="center", va="center", color=ACCENT,
            fontsize=26, fontweight="bold", zorder=6)

    # --- app mockup card ---
    cy = 1130
    ax.add_patch(FancyBboxPatch((48, cy - 360), W - 96, 720,
                                boxstyle="round,pad=2,rounding_size=28",
                                fc=PANEL, ec=BORDER, lw=2, zorder=3))
    try:
        shot = plt.imread(os.path.join(HERE, "gui_mockup.png"))
        ax.add_artist(AnnotationBbox(OffsetImage(shot, zoom=0.56), (W / 2, cy),
                                     frameon=False, zorder=4))
    except Exception:
        pass


    # --- big claim ---
    ax.text(W / 2, 668, "+$100+", ha="center", va="center", color=GREEN,
            fontsize=108, fontweight="bold", zorder=6)
    ax.text(W / 2, 582, "per trade*", ha="center", va="center", color=TXT,
            fontsize=40, fontweight="bold", zorder=6)
    ax.text(W / 2, 530, "5 coins in profit today  ·  +$755 total*", ha="center",
            va="center", color=GREEN, fontsize=24, fontweight="bold", zorder=6)

    # --- CTA ---
    ax.add_patch(FancyBboxPatch((140, 360), W - 280, 96,
                                boxstyle="round,pad=2,rounding_size=48",
                                fc=ACCENT, ec="none", zorder=6))
    ax.text(W / 2, 408, "↓  DOWNLOAD FREE", ha="center", va="center", color="#1a1100",
            fontsize=38, fontweight="bold", zorder=7)
    ax.text(W / 2, 300, "Windows & macOS · free trial · prometheusai.tech", ha="center",
            va="center", color=TXT, fontsize=26, zorder=6)
    ax.text(W / 2, 250, "Binance · Bybit · OKX · KuCoin · Bitget · Kraken · Coinbase",
            ha="center", va="center", color=DIM, fontsize=20, zorder=6)

    # --- disclaimer ---
    ax.text(W / 2, 120,
            "*Illustrative example, not a profit guarantee. Crypto trading is high-risk —\n"
            "you can lose money. Not financial advice.",
            ha="center", va="center", color=DIM, fontsize=18, zorder=6)

    fig.savefig(out, dpi=100, facecolor=BG, bbox_inches=None, pad_inches=0)
    plt.close(fig)
    print(f"wrote {out}  ({W}x{H})")


if __name__ == "__main__":
    build(os.path.join(SOCIAL, "tiktok_reel.png"),
          "POV: your bot trades crypto while you sleep")
    build(os.path.join(SOCIAL, "facebook_reel.png"),
          "Let AI trade crypto for you — 24/7")
