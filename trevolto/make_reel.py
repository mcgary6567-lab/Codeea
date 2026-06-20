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
SOCIAL = os.path.join(HERE, "..", "Trevolto Marketing", "creatives")
# Organised subfolders (see Marketing Docs/social/README.md). Organic reels are
# grouped by the app window they show, for easy identification.
# By purpose: reels/ (vertical 9:16 — TikTok + FB/IG Reels) and posts/ (feed).
REELS = os.path.join(SOCIAL, "reels")
PAID_IMG = os.path.join(REELS, "ads-safe", "images")
PAID_VID = os.path.join(REELS, "ads-safe", "video")
ORG = os.path.join(REELS, "organic")
ORG_MAIN = os.path.join(ORG, "main-window")
ORG_CHART = os.path.join(ORG, "strategy-chart")
ORG_BT = os.path.join(ORG, "backtest")
ORG_AN = os.path.join(ORG, "analytics")
ORG_VID = os.path.join(ORG, "video")
POST_SQUARE = os.path.join(SOCIAL, "posts", "square")       # 1080x1080 FB/IG feed
POST_VERT = os.path.join(SOCIAL, "posts", "vertical")       # 1080x1920 image posts
POST_IG = os.path.join(SOCIAL, "posts", "instagram")        # 1080x1350 IG feed
POST_LI = os.path.join(SOCIAL, "posts", "linkedin")         # 1200x1200 LinkedIn
# Which folder a reel lands in, by the embedded app window.
WINDOW_DIR = {"gui_mockup.png": ORG_MAIN, "gui_chart.png": ORG_CHART,
              "gui_backtest.png": ORG_BT, "gui_analytics.png": ORG_AN}
for _d in (ORG_MAIN, ORG_CHART, ORG_BT, ORG_AN, ORG_VID, PAID_IMG, PAID_VID,
           POST_SQUARE, POST_VERT, POST_IG, POST_LI):
    os.makedirs(_d, exist_ok=True)


def slug(text, maxlen=34):
    """Short kebab-case identifier derived from a hook line, for filenames."""
    import re
    s = re.sub(r"[^a-z0-9]+", "-", text.replace("\n", " ").lower()).strip("-")
    return s[:maxlen].strip("-")

BG = "#1b1f24"
PANEL = "#23282e"
ELEV = "#2c323a"
BORDER = "#3a424b"
TXT = "#e6edf3"
DIM = "#9aa4af"
ACCENT = "#2dd4bf"
GREEN = "#4ade80"

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
    ax.add_patch(plt.Circle((300, 1700), 520, color="#0c2b27", zorder=0))

    _pill(ax, W / 2, 1852, "● LIVE · AUTO-TRADING 24/7", ELEV, GREEN, 24)
    _img(ax, "logo.png", (W / 2, 1752), 0.34, z=6)
    ax.text(W / 2, 1650, "TREVOLTO", ha="center", va="center", color=TXT,
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
    ax.text(W / 2, 408, "↓  DOWNLOAD FREE", ha="center", va="center", color="#04231d",
            fontsize=38, fontweight="bold", zorder=7)
    ax.text(W / 2, 300, "Windows & macOS · free trial · trevolto.com",
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
    ax.add_patch(plt.Circle((250, 980), 360, color="#0c2b27", zorder=0))

    _pill(ax, W / 2, 1038, "● LIVE · AUTO-TRADING 24/7", ELEV, GREEN, 20, h=46)
    ax.text(W / 2, 998, "TREVOLTO", ha="center", va="center", color=TXT,
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
            color="#04231d", fontsize=25, fontweight="bold", zorder=7)
    ax.text(W / 2, 118, "trevolto.com · Windows & macOS · free trial",
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


def _scene_card(ax, cfg):
    """Richer copy-driven 1080x1920 frame: hook + caption + multi-coin profit
    line + big number, around the app card. cfg keys: hook, sub, big, label, extra."""
    W, H = 1080, 1920
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    ax.add_patch(FancyBboxPatch((0, 0), W, H, boxstyle="square,pad=0", fc=BG, ec="none", zorder=0))
    ax.add_patch(plt.Circle((300, 1700), 540, color="#0c2b27", zorder=0))

    _pill(ax, W / 2, 1864, "● LIVE · AUTO-TRADING 24/7", ELEV, GREEN, 23)
    _img(ax, "logo.png", (W / 2, 1788), 0.30, z=6)
    ax.text(W / 2, 1710, "TREVOLTO", ha="center", va="center", color=TXT,
            fontsize=30, fontweight="bold", zorder=6)
    ax.text(W / 2, 1612, cfg["hook"], ha="center", va="center", color=cfg.get("color", ACCENT),
            fontsize=31, fontweight="bold", zorder=6, linespacing=1.05)
    ax.text(W / 2, 1505, cfg["sub"], ha="center", va="center", color=TXT,
            fontsize=23, zorder=6)

    cy = 1115
    ax.add_patch(FancyBboxPatch((54, cy - 280), W - 108, 560,
                                boxstyle="round,pad=2,rounding_size=28",
                                fc=PANEL, ec=BORDER, lw=2, zorder=3))
    _img(ax, cfg.get("window", "gui_mockup.png"), (W / 2, cy), 0.46)

    ax.text(W / 2, 792, cfg["extra"], ha="center", va="center", color=GREEN,
            fontsize=22, fontweight="bold", zorder=6)
    ax.text(W / 2, 690, cfg["big"], ha="center", va="center", color=GREEN,
            fontsize=88, fontweight="bold", zorder=6)
    ax.text(W / 2, 612, cfg["label"], ha="center", va="center", color=TXT,
            fontsize=33, fontweight="bold", zorder=6)

    ax.add_patch(FancyBboxPatch((150, 360), W - 300, 96,
                                boxstyle="round,pad=2,rounding_size=48", fc=ACCENT, ec="none", zorder=6))
    ax.text(W / 2, 408, cfg.get("cta", "↓  DOWNLOAD FREE"), ha="center", va="center",
            color="#04231d", fontsize=37, fontweight="bold", zorder=7)
    ax.text(W / 2, 300, "Windows & macOS · free trial · trevolto.com",
            ha="center", va="center", color=TXT, fontsize=25, zorder=6)
    ax.text(W / 2, 252, "Binance · Bybit · OKX · KuCoin · Bitget · Kraken · Coinbase",
            ha="center", va="center", color=DIM, fontsize=19, zorder=6)
    ax.text(W / 2, 120, "*Illustrative example, not a profit guarantee. Crypto trading is high-risk —\n"
            "you can lose money. Not financial advice.",
            ha="center", va="center", color=DIM, fontsize=18, zorder=6)


def build_card(out, cfg):
    fig, ax = plt.subplots(figsize=(10.8, 19.2), dpi=100)
    fig.patch.set_facecolor(BG)
    _scene_card(ax, cfg)
    fig.savefig(out, dpi=100, facecolor=BG, pad_inches=0)
    plt.close(fig)
    print(f"wrote {out}")


# 10 high-converting variants — varied hooks/captions, multi-coin profit, big P&L.
CFGS = [
    {"hook": "Your money should work\nwhile you sleep", "sub": "Set it once — the AI trades 24/7.",
     "big": "+$755", "label": "while you slept*", "extra": "BTC +$214 · ETH +$188 · SOL +$152 · XRP +$104"},
    {"hook": "5 trades today.\n5 in profit.", "sub": "Multi-coin. Hands-free. Fully automated.",
     "big": "+$100+", "label": "per trade*", "extra": "+$755 banked today across 5 coins*"},
    {"hook": "Stop watching charts.\nStart banking gains.", "sub": "The bot executes — you keep your keys.",
     "big": "+$214", "label": "on BTC today*", "extra": "ETH +$188 · SOL +$152 · XRP +$104 · ADA +$96"},
    {"hook": "Start with $100.\nLet the AI do the rest.", "sub": "Beginner-friendly — no experience needed.",
     "big": "+$100+", "label": "per trade*", "extra": "5 coins in profit · +$755 today*"},
    {"hook": "Up AND down markets —\nthe bot profits both.", "sub": "Goes long and short on futures.",
     "big": "+$755", "label": "today*", "extra": "BTC +$214 · ETH +$188 · SOL +$152 · XRP +$104"},
    {"hook": "While you were at work…", "sub": "…your bot closed 5 green trades.",
     "big": "+$755", "label": "today*", "extra": "every position green — BTC · ETH · SOL · XRP · ADA"},
    {"hook": "Crypto on autopilot", "sub": "Backtested. Automated. Running 24/7.",
     "big": "+$100+", "label": "per trade*", "extra": "5 coins running in profit right now*"},
    {"hook": "Your funds never leave\nyour own exchange", "sub": "Non-custodial · PIN-encrypted · you stay in control.",
     "big": "+$755", "label": "today*", "extra": "BTC +$214 · ETH +$188 · SOL +$152 · XRP +$104"},
    {"hook": "This is what a green\nportfolio looks like", "sub": "5 coins. All in profit. One afternoon.",
     "big": "+$755", "label": "total today*", "extra": "BTC +$214 · ETH +$188 · SOL +$152 · XRP +$104"},
    {"hook": "Trade like a pro —\nwithout being one", "sub": "Presets do the setup. The AI does the trading.",
     "big": "+$100+", "label": "per trade*", "extra": "+$755 across 5 coins today*"},
]


def _scene_safe(ax, cfg, sz=False):
    """Paid-ads-SAFE 1080x1920 frame: feature-led, no profit figures, demo
    mockup, strong risk disclaimer. cfg keys: hook, sub, features (3).

    sz=True lifts all critical content (CTA + disclaimer) out of the bottom ~430px
    and keeps side margins clear, so TikTok/IG Reels UI doesn't cover it."""
    W, H = 1080, 1920
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    ax.add_patch(FancyBboxPatch((0, 0), W, H, boxstyle="square,pad=0", fc=BG, ec="none", zorder=0))
    ax.add_patch(plt.Circle((300, 1700), 540, color="#0c2b27", zorder=0))

    # y-coordinate layout: full-frame vs safe-zone (content pulled up).
    L = dict(chip=1864, logo=1788, brand=1710, hook=1612, sub=1505, cy=1120, ch=560,
             zoom=0.46, feat=(792, 720, 648), cta_y=360, cta_h=96, cta_t=408,
             plat=300, exch=252, disc=120, mw=300, fts=24, hks=33)
    if sz:
        L = dict(chip=1764, logo=1692, brand=1624, hook=1534, sub=1436, cy=1082, ch=460,
                 zoom=0.40, feat=(806, 743, 680), cta_y=556, cta_h=92, cta_t=602,
                 plat=510, exch=474, disc=430, mw=340, fts=23, hks=31)

    _pill(ax, W / 2, L["chip"], "● DEMO / SAFE MODE SHOWN", ELEV, ACCENT, 22)
    _img(ax, "logo.png", (W / 2, L["logo"]), 0.30, z=6)
    ax.text(W / 2, L["brand"], "TREVOLTO", ha="center", va="center", color=TXT,
            fontsize=30, fontweight="bold", zorder=6)
    ax.text(W / 2, L["hook"], cfg["hook"], ha="center", va="center", color=ACCENT,
            fontsize=L["hks"], fontweight="bold", zorder=6, linespacing=1.05)
    ax.text(W / 2, L["sub"], cfg["sub"], ha="center", va="center", color=TXT, fontsize=23, zorder=6)

    cy, ch = L["cy"], L["ch"]
    ax.add_patch(FancyBboxPatch((54, cy - ch / 2), W - 108, ch,
                                boxstyle="round,pad=2,rounding_size=28", fc=PANEL, ec=BORDER, lw=2, zorder=3))
    _img(ax, "gui_mockup_safe.png", (W / 2, cy), L["zoom"])

    feats = []
    for f, fy in zip(cfg["features"], L["feat"]):
        chk = ax.text(168, fy, "✓", color=GREEN, fontsize=27, fontweight="bold", va="center", ha="left", zorder=6)
        lbl = ax.text(214, fy, f, color=TXT, fontsize=L["fts"], va="center", ha="left", zorder=6)
        feats.append((chk, lbl))

    margin = 170 if sz else 150
    ax.add_patch(FancyBboxPatch((margin, L["cta_y"]), W - 2 * margin, L["cta_h"],
                                boxstyle="round,pad=2,rounding_size=46", fc=ACCENT, ec="none", zorder=6))
    ax.text(W / 2, L["cta_t"], "↓  TRY IT FREE", ha="center", va="center", color="#04231d",
            fontsize=36, fontweight="bold", zorder=7)
    ax.text(W / 2, L["plat"], "Windows & macOS · free trial · trevolto.com",
            ha="center", va="center", color=TXT, fontsize=24, zorder=6)
    ax.text(W / 2, L["exch"], "Binance · Bybit · OKX · KuCoin · Bitget · Kraken · Coinbase",
            ha="center", va="center", color=DIM, fontsize=18, zorder=6)
    ax.text(W / 2, L["disc"], "Demo / Safe Mode shown. Crypto trading involves risk of loss —\n"
            "results are not guaranteed. Not financial advice.",
            ha="center", va="center", color=DIM, fontsize=17, zorder=6)
    return feats


def build_safe(out, cfg):
    fig, ax = plt.subplots(figsize=(10.8, 19.2), dpi=100)
    fig.patch.set_facecolor(BG)
    _scene_safe(ax, cfg)
    fig.savefig(out, dpi=100, facecolor=BG, pad_inches=0)
    plt.close(fig)
    print(f"wrote {out}")


def build_safe_video(out, cfg, seconds=6, fps=30):
    """Animated ad-safe reel: the ✓ feature lines reveal one-by-one (no profit
    ticker, nothing that implies returns)."""
    if not _HAS_FFMPEG:
        print("ffmpeg unavailable — skipping mp4")
        return
    fig, ax = plt.subplots(figsize=(10.8, 19.2), dpi=100)
    fig.patch.set_facecolor(BG)
    feats = _scene_safe(ax, cfg, sz=True)   # TikTok/IG safe-zone layout
    n = seconds * fps

    def update(i):
        for p, (chk, lbl) in enumerate(feats):
            on = i >= int((0.28 + 0.17 * p) * n)
            chk.set_alpha(1.0 if on else 0.0)
            lbl.set_alpha(1.0 if on else 0.0)
        return [a for pair in feats for a in pair]

    anim = animation.FuncAnimation(fig, update, frames=n, interval=1000 / fps, blit=False)
    anim.save(out, writer=animation.FFMpegWriter(fps=fps, bitrate=4000), dpi=100)
    plt.close(fig)
    print(f"wrote {out}  (1080x1920 · {seconds}s)")


# Paid-ads-safe set: feature/benefit copy, NO profit figures, demo visuals.
CFGS_SAFE = [
    {"hook": "Automate your crypto\nstrategy — 24/7", "sub": "Connect your exchange. The bot handles execution.",
     "features": ["Runs 24/7 while you're away", "One-click strategy presets", "Long & short on futures"]},
    {"hook": "Test before you\nrisk real money", "sub": "Built-in demo mode + historical backtester.",
     "features": ["Paper-trade in Safe Mode", "Backtest on real history", "Optimize before you go live"]},
    {"hook": "Your keys.\nYour exchange.", "sub": "Non-custodial — funds never leave your account.",
     "features": ["Connect via API (no withdrawals)", "Keys encrypted behind a PIN", "You stay in full control"]},
    {"hook": "Built-in risk\nguardrails", "sub": "Protect your account automatically.",
     "features": ["Daily-loss & max-drawdown halts", "Cooldowns & position caps", "Trailing stops + auto SL/TP"]},
    {"hook": "Beginner-friendly\nautomation", "sub": "Guided setup — no experience needed.",
     "features": ["Step-by-step setup", "Learn first in demo mode", "Presets: Conservative → Aggressive"]},
    {"hook": "Backtest. Optimize.\nAutomate.", "sub": "See how a strategy behaved before going live.",
     "features": ["Equity curve & win-rate stats", "Walk-forward testing", "Export results to CSV"]},
    {"hook": "One app,\n8 exchanges", "sub": "Trade your venue of choice.",
     "features": ["Binance · Bybit · OKX · KuCoin", "Bitget · Kraken · Coinbase", "Spot & futures markets"]},
    {"hook": "Set it once.\nRuns 24/7.", "sub": "Hands-free crypto automation on your PC.",
     "features": ["Windows & macOS", "Optional run-at-startup", "Telegram & desktop alerts"]},
]


# 15 more profit-led variants — varied accent color + app window + caption.
# windows: gui_mockup (main) / gui_chart / gui_backtest / gui_analytics.
CFGS_15 = [
    {"color": "#2dd4bf", "window": "gui_mockup.png", "hook": "Wake up to\ngreen trades", "sub": "The bot traded all night — hands-free.", "big": "+$755", "label": "overnight*", "extra": "BTC +$214 · ETH +$188 · SOL +$152 · XRP +$104"},
    {"color": "#2dd4bf", "window": "gui_analytics.png", "hook": "5 coins.\nAll in profit.", "sub": "Your performance, at a glance.", "big": "68%", "label": "win rate*", "extra": "Realized P&L +$842 across 142 trades*"},
    {"color": "#4ade80", "window": "gui_chart.png", "hook": "It catches\nevery move", "sub": "Buy low, sell high — automatically.", "big": "+$214", "label": "this trade*", "extra": "long & short signals, executed for you"},
    {"color": "#22d3ee", "window": "gui_backtest.png", "hook": "Backtested before\na single cent", "sub": "See how it performed on real history.", "big": "+84%", "label": "in backtest*", "extra": "68% win · 2.1 profit factor · 142 trades*"},
    {"color": "#4ade80", "window": "gui_mockup.png", "hook": "Your portfolio\non autopilot", "sub": "Set your risk once. It does the rest.", "big": "+$755", "label": "today*", "extra": "5 coins green: BTC ETH SOL XRP ADA"},
    {"color": "#2dd4bf", "window": "gui_chart.png", "hook": "Signal to profit\nin seconds", "sub": "EMA + RSI crossover, auto-executed.", "big": "+$188", "label": "on ETH*", "extra": "entry · TP1 · TP2 placed automatically"},
    {"color": "#2dd4bf", "window": "gui_analytics.png", "hook": "The numbers\ndon't lie", "sub": "68% win rate. +$842 realized.", "big": "+$842", "label": "realized*", "extra": "142 trades · profit factor 2.1*"},
    {"color": "#22d3ee", "window": "gui_backtest.png", "hook": "Proven,\nnot promised", "sub": "Walk-forward tested before going live.", "big": "2.1", "label": "profit factor*", "extra": "equity +84% over 3 months (backtest)*"},
    {"color": "#2dd4bf", "window": "gui_mockup.png", "hook": "Profits while\nyou sleep", "sub": "24/7 automation on your own PC.", "big": "+$755", "label": "overnight*", "extra": "BTC +$214 · ETH +$188 · SOL +$152"},
    {"color": "#4ade80", "window": "gui_chart.png", "hook": "Long AND short —\nboth directions", "sub": "Profit in up and down markets.", "big": "+$152", "label": "on SOL*", "extra": "futures: long + short, automatically"},
    {"color": "#2dd4bf", "window": "gui_mockup.png", "hook": "Set it. Forget it.\nStack it.", "sub": "One-click presets, then walk away.", "big": "+$100+", "label": "per trade*", "extra": "5 coins green today · +$755 total*"},
    {"color": "#22d3ee", "window": "gui_analytics.png", "hook": "Every position\ngreen today", "sub": "Live P&L across your portfolio.", "big": "+$842", "label": "realized*", "extra": "win rate 68% · best +$96 · 142 trades"},
    {"color": "#4ade80", "window": "gui_backtest.png", "hook": "68% win rate,\nhands-free", "sub": "The backtest the bot actually trades.", "big": "68%", "label": "win rate*", "extra": "net +$842 · profit factor 2.1*"},
    {"color": "#2dd4bf", "window": "gui_chart.png", "hook": "Turn volatility\ninto gains", "sub": "It trades the swings for you.", "big": "+$214", "label": "on BTC*", "extra": "BUY / SELL signals fired automatically"},
    {"color": "#2dd4bf", "window": "gui_mockup.png", "hook": "This is\nautomated crypto", "sub": "Multi-coin. Multi-exchange. 24/7.", "big": "+$755", "label": "today*", "extra": "BTC ETH SOL XRP ADA — all in profit"},
]


# 5 more — the realistic Strategy Chart window, varied colours + captions.
CFGS_CHART = [
    {"color": "#4ade80", "window": "gui_chart.png", "hook": "Watch it\ntrade live", "sub": "BUY ▲ and SELL ▼ — fired automatically.", "big": "+$214", "label": "on BTC today*", "extra": "38 entries today · long + short"},
    {"color": "#2dd4bf", "window": "gui_chart.png", "hook": "Catch the swing\nevery time", "sub": "EMA20 + RSI crossover, executed for you.", "big": "+$188", "label": "on ETH*", "extra": "entry · SL · TP1 · TP2 placed for you"},
    {"color": "#4ade80", "window": "gui_chart.png", "hook": "Up or down —\nit profits", "sub": "Long & short on Bitget futures.", "big": "+$152", "label": "on SOL*", "extra": "BTC +$214 · ETH +$188 · SOL +$152"},
    {"color": "#2dd4bf", "window": "gui_chart.png", "hook": "Never miss\na setup", "sub": "The bot watches the charts 24/7.", "big": "+$755", "label": "today*", "extra": "5 coins green: BTC ETH SOL XRP ADA"},
    {"color": "#22d3ee", "window": "gui_chart.png", "hook": "Pro signals,\nautomated", "sub": "Scale-outs, breakeven & exits — handled.", "big": "+$104", "label": "on XRP*", "extra": "299 candles scanned · 38 entries"},
]


def build_post(out, cfg, shape):
    """Feed post for Instagram (1080x1350 portrait) / LinkedIn (1200x1200 square).

    cfg keys: hook, sub, big, label, extra, color, window.
    shape: "ig" → 1080x1350 portrait, punchy CTA;
           "li" → 1200x1200 square, professional CTA.
    Layout is fully explicit (no formula collisions): the app-window card is
    sized to the embedded screenshot, and the profit block sits clear of the CTA."""
    if shape == "ig":
        # ih = target on-canvas screenshot height in px (see zoom note below).
        L = dict(W=1080, H=1350, chip=1306, logo=1238, brand=1176, hook=1086,
                 hookfs=33, sub=996, cy=735, ih=400, extra=478, big=388, bigfs=76,
                 label=296, cta_y=124, cta_h=86, cta_t=167, foot=80, disc=38,
                 cta=cfg.get("cta", "↓  DOWNLOAD FREE"))
    else:  # li — square, a touch more compact vertically
        L = dict(W=1200, H=1200, chip=1158, logo=1092, brand=1035, hook=946,
                 hookfs=31, sub=854, cy=648, ih=320, extra=424, big=332, bigfs=72,
                 label=244, cta_y=108, cta_h=84, cta_t=150, foot=78, disc=38,
                 cta=cfg.get("cta", "Learn more  →"))
    W, H = L["W"], L["H"]
    color = cfg.get("color", ACCENT)
    fig, ax = plt.subplots(figsize=(W / 100, H / 100), dpi=100)
    fig.patch.set_facecolor(BG)
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    ax.add_patch(FancyBboxPatch((0, 0), W, H, boxstyle="square,pad=0", fc=BG, ec="none", zorder=0))
    ax.add_patch(plt.Circle((W * 0.26, H * 0.84), W * 0.46, color="#0c2b27", zorder=0))

    # --- header ---
    _pill(ax, W / 2, L["chip"], "● LIVE · AUTO-TRADING 24/7", ELEV, GREEN, 22, h=52)
    _img(ax, "logo.png", (W / 2, L["logo"]), 0.26, z=6)
    ax.text(W / 2, L["brand"], "TREVOLTO", ha="center", va="center", color=TXT,
            fontsize=28, fontweight="bold", zorder=6)
    ax.text(W / 2, L["hook"], cfg["hook"], ha="center", va="center", color=color,
            fontsize=L["hookfs"], fontweight="bold", zorder=6, linespacing=1.05)
    ax.text(W / 2, L["sub"], cfg["sub"], ha="center", va="center", color=TXT,
            fontsize=22, zorder=6)

    # --- app-window card (sized to the screenshot) ---
    # OffsetImage zoom renders at native_px * zoom * dpi/72; solve for a target
    # on-canvas height so the card never overlaps the surrounding text.
    z = L["ih"] / (886 * 100 / 72.0)
    iw, ih = 1340 * z * 100 / 72.0, L["ih"]   # on-canvas screenshot size
    cw, ch = iw + 46, ih + 46
    cy = L["cy"]
    ax.add_patch(FancyBboxPatch(((W - cw) / 2, cy - ch / 2), cw, ch,
                                boxstyle="round,pad=2,rounding_size=26",
                                fc=PANEL, ec=BORDER, lw=2, zorder=3))
    _img(ax, cfg.get("window", "gui_mockup.png"), (W / 2, cy), z)

    # --- profit block ---
    ax.text(W / 2, L["extra"], cfg["extra"], ha="center", va="center", color=GREEN,
            fontsize=21, fontweight="bold", zorder=6)
    ax.text(W / 2, L["big"], cfg["big"], ha="center", va="center", color=GREEN,
            fontsize=L["bigfs"], fontweight="bold", zorder=6)
    ax.text(W / 2, L["label"], cfg["label"], ha="center", va="center", color=TXT,
            fontsize=30, fontweight="bold", zorder=6)

    # --- CTA + footer ---
    cta_w = W * 0.60
    ax.add_patch(FancyBboxPatch(((W - cta_w) / 2, L["cta_y"]), cta_w, L["cta_h"],
                                boxstyle="round,pad=2,rounding_size=43", fc=color, ec="none", zorder=6))
    ax.text(W / 2, L["cta_t"], L["cta"], ha="center", va="center", color="#0a0a0a",
            fontsize=30, fontweight="bold", zorder=7)
    ax.text(W / 2, L["foot"], "Windows & macOS · free trial · trevolto.com",
            ha="center", va="center", color=TXT, fontsize=21, zorder=6)
    ax.text(W / 2, L["disc"], "*Illustrative example, not a profit guarantee. Crypto trading is high-risk — not financial advice.",
            ha="center", va="center", color=DIM, fontsize=15, zorder=6)
    fig.savefig(out, dpi=100, facecolor=BG, pad_inches=0)
    plt.close(fig)
    print(f"wrote {out}  ({W}x{H})")


# 10 Instagram posts (1080x1350) — punchy, profit-led, varied font colours,
# alternating main-dashboard / strategy-chart windows, multi-coin profit lines.
CFGS_IG = [
    {"color": "#2dd4bf", "window": "gui_mockup.png", "hook": "5 coins.\nAll in profit.", "sub": "Your portfolio, fully automated.", "big": "+$755", "label": "banked today*", "extra": "BTC +$214 · ETH +$188 · SOL +$152 · XRP +$104"},
    {"color": "#2dd4bf", "window": "gui_chart.png", "hook": "It catches\nevery move", "sub": "BUY ▲ / SELL ▼ — fired automatically.", "big": "+$214", "label": "on BTC today*", "extra": "long & short signals, executed for you"},
    {"color": "#4ade80", "window": "gui_mockup.png", "hook": "Wake up to\ngreen trades", "sub": "The bot traded all night — hands-free.", "big": "+$755", "label": "overnight*", "extra": "5 coins green: BTC ETH SOL XRP ADA"},
    {"color": "#4ade80", "window": "gui_chart.png", "hook": "Stop watching.\nStart banking.", "sub": "It watches the charts 24/7 so you don't.", "big": "+$188", "label": "on ETH*", "extra": "entry · SL · TP1 · TP2 placed for you"},
    {"color": "#2dd4bf", "window": "gui_mockup.png", "hook": "Your money,\nworking 24/7", "sub": "Set your risk once. It does the rest.", "big": "+$100+", "label": "per trade*", "extra": "5 coins in profit · +$755 today*"},
    {"color": "#22d3ee", "window": "gui_chart.png", "hook": "Up OR down —\nit profits both", "sub": "Goes long and short on futures.", "big": "+$152", "label": "on SOL*", "extra": "BTC +$214 · ETH +$188 · SOL +$152"},
    {"color": "#4ade80", "window": "gui_mockup.png", "hook": "This is a green\nportfolio", "sub": "Multi-coin. Multi-exchange. 24/7.", "big": "+$755", "label": "total today*", "extra": "every position green across 5 coins"},
    {"color": "#22d3ee", "window": "gui_chart.png", "hook": "Signal to profit\nin seconds", "sub": "EMA20 + RSI crossover, auto-executed.", "big": "+$214", "label": "this trade*", "extra": "38 entries today · long + short"},
    {"color": "#2dd4bf", "window": "gui_mockup.png", "hook": "Start with $100.\nLet AI do the rest.", "sub": "Beginner-friendly — no experience needed.", "big": "+$100+", "label": "per trade*", "extra": "5 coins running in profit right now*"},
    {"color": "#2dd4bf", "window": "gui_chart.png", "hook": "Never miss\na setup again", "sub": "The bot trades the swings for you.", "big": "+$104", "label": "on XRP*", "extra": "299 candles scanned · 38 entries"},
]


# 10 LinkedIn posts (1200x1200) — professional, credible tone; same windows +
# varied font colours; sample/backtest-framed numbers (no per-trade hype).
CFGS_LI = [
    {"color": "#2dd4bf", "window": "gui_chart.png", "hook": "Automate your\ncrypto strategy", "sub": "Rules-based execution, running 24/7.", "big": "68%", "label": "win rate (sample)*", "extra": "142 trades · profit factor 2.1 · backtested"},
    {"color": "#4ade80", "window": "gui_backtest.png" if os.path.exists(os.path.join(HERE, "gui_backtest.png")) else "gui_chart.png", "hook": "Backtested first,\nthen automated", "sub": "Validate on real history before going live.", "big": "+84%", "label": "equity (backtest)*", "extra": "3-month walk-forward · 142 trades*"},
    {"color": "#22d3ee", "window": "gui_mockup.png", "hook": "Systematic,\nrules-based trading", "sub": "Remove emotion. Follow the strategy.", "big": "24/7", "label": "hands-free execution", "extra": "multi-coin · spot & futures · 8 exchanges"},
    {"color": "#2dd4bf", "window": "gui_chart.png", "hook": "Risk-managed\nby design", "sub": "Daily-loss limits, drawdown halts, auto stops.", "big": "100%", "label": "rules enforced*", "extra": "every trade carries SL · TP1 · TP2"},
    {"color": "#22d3ee", "window": "gui_mockup.png", "hook": "Your keys.\nYour exchange.", "sub": "Non-custodial — connects by API only.", "big": "0", "label": "withdrawal access", "extra": "keys PIN-encrypted · you stay in control"},
    {"color": "#4ade80", "window": "gui_chart.png", "hook": "Discipline,\nautomated", "sub": "EMA + RSI signals executed without hesitation.", "big": "2.1", "label": "profit factor (sample)*", "extra": "68% win rate over 142 backtested trades*"},
    {"color": "#2dd4bf", "window": "gui_mockup.png", "hook": "Test. Optimize.\nThen deploy.", "sub": "A full workflow — not a black box.", "big": "+84%", "label": "in backtest*", "extra": "equity curve · win-rate · exportable stats"},
    {"color": "#4ade80", "window": "gui_chart.png", "hook": "One platform,\neight exchanges", "sub": "Binance · Bybit · OKX · Kraken · Coinbase + more.", "big": "8", "label": "exchanges, one app", "extra": "spot & futures · long and short"},
    {"color": "#2dd4bf", "window": "gui_mockup.png", "hook": "Strategy execution,\nperfected", "sub": "The bot trades exactly what you backtested.", "big": "24/7", "label": "consistent execution", "extra": "no missed entries · no emotional exits"},
    {"color": "#2dd4bf", "window": "gui_chart.png", "hook": "Data before\nyou trade", "sub": "Backtest on real history with full stats.", "big": "142", "label": "trades analyzed*", "extra": "win rate 68% · profit factor 2.1 (sample)*"},
]


if __name__ == "__main__":
    import make_mockup
    make_mockup.render(os.path.join(HERE, "gui_mockup_safe.png"), safe=True)
    make_mockup.render_chart(); make_mockup.render_backtest(); make_mockup.render_analytics()

    # --- posts (feed) ---
    build(os.path.join(POST_VERT, "tiktok-hero.png"),
          "POV: your bot trades crypto while you sleep")
    build(os.path.join(POST_VERT, "facebook-hero.png"),
          "Let AI trade crypto for you — 24/7")
    build_square(os.path.join(POST_SQUARE, "square-post.png"),
                 "Let AI trade crypto for you — 24/7")
    # --- organic reels (profit-led): grouped by app window, named after the hook ---
    build_video(os.path.join(ORG_VID, "profit-reel.mp4"),
                "Let AI trade crypto for you — 24/7")
    counters = {}
    for cfg in (CFGS + CFGS_15 + CFGS_CHART):
        folder = WINDOW_DIR.get(cfg.get("window", "gui_mockup.png"), ORG_MAIN)
        counters[folder] = counters.get(folder, 0) + 1
        build_card(os.path.join(folder, f"{counters[folder]:02d}-{slug(cfg['hook'])}.png"), cfg)

    # --- feed posts: Instagram (1080x1350) + LinkedIn (1200x1200) ---
    for i, cfg in enumerate(CFGS_IG, 1):
        build_post(os.path.join(POST_IG, f"{i:02d}-{slug(cfg['hook'])}.png"), cfg, "ig")
    for i, cfg in enumerate(CFGS_LI, 1):
        build_post(os.path.join(POST_LI, f"{i:02d}-{slug(cfg['hook'])}.png"), cfg, "li")

    # --- paid-ads-safe (feature-led, no profit claims) ---
    for i, cfg in enumerate(CFGS_SAFE, 1):
        name = f"{i:02d}-{slug(cfg['hook'])}"
        build_safe(os.path.join(PAID_IMG, name + ".png"), cfg)
        build_safe_video(os.path.join(PAID_VID, name + ".mp4"), cfg)
