"""Render social promo creatives from the app mockup.

Outputs (in ../Marketing Docs/social/):
  * tiktok_reel.png / facebook_reel.png  — 1080x1920 vertical posters
  * square_post.png                       — 1080x1080 feed post
  * reel.mp4                              — 1080x1920 animated reel (profit
                                            total tickers up + pulsing headline)
Run:  python make_reel.py

NOTE: profit figures are ILLUSTRATIVE examples and every frame carries a risk
disclaimer — required for ad-policy / consumer-protection compliance when
marketing a real-money trading product.
"""

import math
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from matplotlib.patches import FancyBboxPatch

# Use the bundled ffmpeg (pip imageio-ffmpeg) if a system one isn't present.
try:
    import imageio_ffmpeg
    plt.rcParams["animation.ffmpeg_path"] = imageio_ffmpeg.get_ffmpeg_exe()
    _HAS_FFMPEG = True
except Exception:  # noqa: BLE001
    _HAS_FFMPEG = animation.writers.is_available("ffmpeg")

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

TOTAL = 755   # illustrative portfolio P&L for the day


def _pill(ax, cx, cy, text, fc, tc, fs, h=58):
    w = 52 + fs * 0.62 * len(text)
    ax.add_patch(FancyBboxPatch((cx - w / 2, cy - h / 2), w, h,
                                boxstyle="round,pad=2,rounding_size=24",
                                fc=fc, ec="none", zorder=6))
    ax.text(cx, cy, text, ha="center", va="center", color=tc, fontsize=fs,
            fontweight="bold", zorder=7)


def _img(ax, name, xy, zoom, z=4):
    try:
        ax.add_artist(AnnotationBbox(OffsetImage(plt.imread(os.path.join(HERE, name)), zoom=zoom),
                                     xy, frameon=False, zorder=z))
    except Exception:  # noqa: BLE001
        pass


def _scene_vertical(ax, hook):
    """Draw the 1080x1920 vertical scene. Returns (big, counter) dynamic texts."""
    W, H = 1080, 1920
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    ax.add_patch(FancyBboxPatch((0, 0), W, H, boxstyle="square,pad=0", fc=BG, ec="none", zorder=0))
    ax.add_patch(plt.Circle((300, 1700), 520, color="#241a12", zorder=0))

    _pill(ax, W / 2, 1852, "● LIVE · AUTO-TRADING 24/7", ELEV, GREEN, 24)
    _img(ax, "logo.png", (W / 2, 1752), 0.34, z=6)
    ax.text(W / 2, 1650, "PROMETHEUS AI", ha="center", va="center", color=TXT,
            fontsize=36, fontweight="bold", zorder=6)
    ax.text(W / 2, 1584, hook, ha="center", va="center", color=ACCENT,
            fontsize=26, fontweight="bold", zorder=6)

    cy = 1130
    ax.add_patch(FancyBboxPatch((48, cy - 360), W - 96, 720,
                                boxstyle="round,pad=2,rounding_size=28",
                                fc=PANEL, ec=BORDER, lw=2, zorder=3))
    _img(ax, "gui_mockup.png", (W / 2, cy), 0.56)

    big = ax.text(W / 2, 668, "+$100+", ha="center", va="center", color=GREEN,
                  fontsize=108, fontweight="bold", zorder=6)
    ax.text(W / 2, 582, "per trade*", ha="center", va="center", color=TXT,
            fontsize=40, fontweight="bold", zorder=6)
    counter = ax.text(W / 2, 530, f"5 coins in profit today  ·  +${TOTAL} total*",
                      ha="center", va="center", color=GREEN, fontsize=24,
                      fontweight="bold", zorder=6)

    ax.add_patch(FancyBboxPatch((140, 360), W - 280, 96,
                                boxstyle="round,pad=2,rounding_size=48", fc=ACCENT, ec="none", zorder=6))
    ax.text(W / 2, 408, "↓  DOWNLOAD FREE", ha="center", va="center", color="#1a1100",
            fontsize=38, fontweight="bold", zorder=7)
    ax.text(W / 2, 300, "Windows & macOS · free trial · prometheusai.tech",
            ha="center", va="center", color=TXT, fontsize=26, zorder=6)
    ax.text(W / 2, 250, "Binance · Bybit · OKX · KuCoin · Bitget · Kraken · Coinbase",
            ha="center", va="center", color=DIM, fontsize=20, zorder=6)
    ax.text(W / 2, 120, "*Illustrative example, not a profit guarantee. Crypto trading is high-risk —\n"
            "you can lose money. Not financial advice.",
            ha="center", va="center", color=DIM, fontsize=18, zorder=6)
    return big, counter


def build(out, hook):
    fig, ax = plt.subplots(figsize=(10.8, 19.2), dpi=100)
    fig.patch.set_facecolor(BG)
    _scene_vertical(ax, hook)
    fig.savefig(out, dpi=100, facecolor=BG, pad_inches=0)
    plt.close(fig)
    print(f"wrote {out}  (1080x1920)")


def build_square(out, hook):
    W = H = 1080
    fig, ax = plt.subplots(figsize=(10.8, 10.8), dpi=100)
    fig.patch.set_facecolor(BG)
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    ax.add_patch(FancyBboxPatch((0, 0), W, H, boxstyle="square,pad=0", fc=BG, ec="none", zorder=0))
    ax.add_patch(plt.Circle((250, 980), 360, color="#241a12", zorder=0))

    _pill(ax, W / 2, 1038, "● LIVE · AUTO-TRADING 24/7", ELEV, GREEN, 20, h=46)
    ax.text(W / 2, 998, "PROMETHEUS AI", ha="center", va="center", color=TXT,
            fontsize=28, fontweight="bold", zorder=6)
    ax.text(W / 2, 950, hook, ha="center", va="center", color=ACCENT,
            fontsize=19, fontweight="bold", zorder=6)

    cy = 615
    ax.add_patch(FancyBboxPatch((90, cy - 235), W - 180, 470,
                                boxstyle="round,pad=2,rounding_size=24",
                                fc=PANEL, ec=BORDER, lw=2, zorder=3))
    _img(ax, "gui_mockup.png", (W / 2, cy), 0.40)

    ax.text(W / 2, 312, "+$100+ per trade*", ha="center", va="center", color=GREEN,
            fontsize=54, fontweight="bold", zorder=6)
    ax.text(W / 2, 262, f"5 coins in profit today  ·  +${TOTAL} total*", ha="center",
            va="center", color=GREEN, fontsize=19, fontweight="bold", zorder=6)
    ax.add_patch(FancyBboxPatch((300, 165), W - 600, 70,
                                boxstyle="round,pad=2,rounding_size=35", fc=ACCENT, ec="none", zorder=6))
    ax.text(W / 2, 200, "↓  DOWNLOAD FREE", ha="center", va="center",
            color="#1a1100", fontsize=25, fontweight="bold", zorder=7)
    ax.text(W / 2, 118, "prometheusai.tech · Windows & macOS · free trial",
            ha="center", va="center", color=TXT, fontsize=18, zorder=6)
    ax.text(W / 2, 64, "*Illustrative example, not a profit guarantee. Crypto trading is high-risk.",
            ha="center", va="center", color=DIM, fontsize=15, zorder=6)
    fig.savefig(out, dpi=100, facecolor=BG, pad_inches=0)
    plt.close(fig)
    print(f"wrote {out}  (1080x1080)")


def build_video(out, hook, seconds=5, fps=30):
    if not _HAS_FFMPEG:
        print("ffmpeg unavailable — skipping mp4")
        return
    fig, ax = plt.subplots(figsize=(10.8, 19.2), dpi=100)
    fig.patch.set_facecolor(BG)
    big, counter = _scene_vertical(ax, hook)
    n = seconds * fps
    ramp = int(n * 0.55)   # count up over the first ~55% then hold

    def update(i):
        t = min(1.0, i / ramp)
        e = 1 - (1 - t) ** 3                       # ease-out
        counter.set_text(f"5 coins in profit today  ·  +${int(TOTAL * e)} total*")
        big.set_fontsize(108 + 8 * math.sin(i / 5.0))   # gentle pulse
        return big, counter

    anim = animation.FuncAnimation(fig, update, frames=n, interval=1000 / fps, blit=False)
    anim.save(out, writer=animation.FFMpegWriter(fps=fps, bitrate=4000), dpi=100)
    plt.close(fig)
    print(f"wrote {out}  (1080x1920 · {seconds}s)")


if __name__ == "__main__":
    build(os.path.join(SOCIAL, "tiktok_reel.png"),
          "POV: your bot trades crypto while you sleep")
    build(os.path.join(SOCIAL, "facebook_reel.png"),
          "Let AI trade crypto for you — 24/7")
    build_square(os.path.join(SOCIAL, "square_post.png"),
                 "Let AI trade crypto for you — 24/7")
    build_video(os.path.join(SOCIAL, "reel.mp4"),
                "Let AI trade crypto for you — 24/7")
