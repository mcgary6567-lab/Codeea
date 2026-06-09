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
# Organised subfolders (see Marketing Docs/social/README.md).
ORG_IMG = os.path.join(SOCIAL, "organic", "images")
ORG_VID = os.path.join(SOCIAL, "organic", "video")
PAID_IMG = os.path.join(SOCIAL, "paid-ads-safe", "images")
PAID_VID = os.path.join(SOCIAL, "paid-ads-safe", "video")
for _d in (ORG_IMG, ORG_VID, PAID_IMG, PAID_VID):
    os.makedirs(_d, exist_ok=True)

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


def _scene_card(ax, cfg):
    """Richer copy-driven 1080x1920 frame: hook + caption + multi-coin profit
    line + big number, around the app card. cfg keys: hook, sub, big, label, extra."""
    W, H = 1080, 1920
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    ax.add_patch(FancyBboxPatch((0, 0), W, H, boxstyle="square,pad=0", fc=BG, ec="none", zorder=0))
    ax.add_patch(plt.Circle((300, 1700), 540, color="#241a12", zorder=0))

    _pill(ax, W / 2, 1864, "● LIVE · AUTO-TRADING 24/7", ELEV, GREEN, 23)
    _img(ax, "logo.png", (W / 2, 1788), 0.30, z=6)
    ax.text(W / 2, 1710, "PROMETHEUS AI", ha="center", va="center", color=TXT,
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
            color="#1a1100", fontsize=37, fontweight="bold", zorder=7)
    ax.text(W / 2, 300, "Windows & macOS · free trial · prometheusai.tech",
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
    ax.add_patch(plt.Circle((300, 1700), 540, color="#241a12", zorder=0))

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
    ax.text(W / 2, L["brand"], "PROMETHEUS AI", ha="center", va="center", color=TXT,
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
    ax.text(W / 2, L["cta_t"], "↓  TRY IT FREE", ha="center", va="center", color="#1a1100",
            fontsize=36, fontweight="bold", zorder=7)
    ax.text(W / 2, L["plat"], "Windows & macOS · free trial · prometheusai.tech",
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
    {"color": "#f0883e", "window": "gui_mockup.png", "hook": "Wake up to\ngreen trades", "sub": "The bot traded all night — hands-free.", "big": "+$755", "label": "overnight*", "extra": "BTC +$214 · ETH +$188 · SOL +$152 · XRP +$104"},
    {"color": "#4a9eff", "window": "gui_analytics.png", "hook": "5 coins.\nAll in profit.", "sub": "Your performance, at a glance.", "big": "68%", "label": "win rate*", "extra": "Realized P&L +$842 across 142 trades*"},
    {"color": "#3fb950", "window": "gui_chart.png", "hook": "It catches\nevery move", "sub": "Buy low, sell high — automatically.", "big": "+$214", "label": "this trade*", "extra": "long & short signals, executed for you"},
    {"color": "#c792ea", "window": "gui_backtest.png", "hook": "Backtested before\na single cent", "sub": "See how it performed on real history.", "big": "+84%", "label": "in backtest*", "extra": "68% win · 2.1 profit factor · 142 trades*"},
    {"color": "#ffd54a", "window": "gui_mockup.png", "hook": "Your portfolio\non autopilot", "sub": "Set your risk once. It does the rest.", "big": "+$755", "label": "today*", "extra": "5 coins green: BTC ETH SOL XRP ADA"},
    {"color": "#2dd4bf", "window": "gui_chart.png", "hook": "Signal to profit\nin seconds", "sub": "EMA + RSI crossover, auto-executed.", "big": "+$188", "label": "on ETH*", "extra": "entry · TP1 · TP2 placed automatically"},
    {"color": "#ff6fb5", "window": "gui_analytics.png", "hook": "The numbers\ndon't lie", "sub": "68% win rate. +$842 realized.", "big": "+$842", "label": "realized*", "extra": "142 trades · profit factor 2.1*"},
    {"color": "#38bdf8", "window": "gui_backtest.png", "hook": "Proven,\nnot promised", "sub": "Walk-forward tested before going live.", "big": "2.1", "label": "profit factor*", "extra": "equity +84% over 3 months (backtest)*"},
    {"color": "#f0883e", "window": "gui_mockup.png", "hook": "Profits while\nyou sleep", "sub": "24/7 automation on your own PC.", "big": "+$755", "label": "overnight*", "extra": "BTC +$214 · ETH +$188 · SOL +$152"},
    {"color": "#a3e635", "window": "gui_chart.png", "hook": "Long AND short —\nboth directions", "sub": "Profit in up and down markets.", "big": "+$152", "label": "on SOL*", "extra": "futures: long + short, automatically"},
    {"color": "#4a9eff", "window": "gui_mockup.png", "hook": "Set it. Forget it.\nStack it.", "sub": "One-click presets, then walk away.", "big": "+$100+", "label": "per trade*", "extra": "5 coins green today · +$755 total*"},
    {"color": "#c792ea", "window": "gui_analytics.png", "hook": "Every position\ngreen today", "sub": "Live P&L across your portfolio.", "big": "+$842", "label": "realized*", "extra": "win rate 68% · best +$96 · 142 trades"},
    {"color": "#ffd54a", "window": "gui_backtest.png", "hook": "68% win rate,\nhands-free", "sub": "The backtest the bot actually trades.", "big": "68%", "label": "win rate*", "extra": "net +$842 · profit factor 2.1*"},
    {"color": "#2dd4bf", "window": "gui_chart.png", "hook": "Turn volatility\ninto gains", "sub": "It trades the swings for you.", "big": "+$214", "label": "on BTC*", "extra": "BUY / SELL signals fired automatically"},
    {"color": "#ff6fb5", "window": "gui_mockup.png", "hook": "This is\nautomated crypto", "sub": "Multi-coin. Multi-exchange. 24/7.", "big": "+$755", "label": "today*", "extra": "BTC ETH SOL XRP ADA — all in profit"},
]


# 5 more — the realistic Strategy Chart window, varied colours + captions.
CFGS_CHART = [
    {"color": "#3fb950", "window": "gui_chart.png", "hook": "Watch it\ntrade live", "sub": "BUY ▲ and SELL ▼ — fired automatically.", "big": "+$214", "label": "on BTC today*", "extra": "38 entries today · long + short"},
    {"color": "#4a9eff", "window": "gui_chart.png", "hook": "Catch the swing\nevery time", "sub": "EMA20 + RSI crossover, executed for you.", "big": "+$188", "label": "on ETH*", "extra": "entry · SL · TP1 · TP2 placed for you"},
    {"color": "#ffd54a", "window": "gui_chart.png", "hook": "Up or down —\nit profits", "sub": "Long & short on Bitget futures.", "big": "+$152", "label": "on SOL*", "extra": "BTC +$214 · ETH +$188 · SOL +$152"},
    {"color": "#ff6fb5", "window": "gui_chart.png", "hook": "Never miss\na setup", "sub": "The bot watches the charts 24/7.", "big": "+$755", "label": "today*", "extra": "5 coins green: BTC ETH SOL XRP ADA"},
    {"color": "#38bdf8", "window": "gui_chart.png", "hook": "Pro signals,\nautomated", "sub": "Scale-outs, breakeven & exits — handled.", "big": "+$104", "label": "on XRP*", "extra": "299 candles scanned · 38 entries"},
]


if __name__ == "__main__":
    import make_mockup
    make_mockup.render(os.path.join(HERE, "gui_mockup_safe.png"), safe=True)
    make_mockup.render_chart(); make_mockup.render_backtest(); make_mockup.render_analytics()
    # --- organic (profit-led) ---
    build(os.path.join(ORG_IMG, "tiktok_reel.png"),
          "POV: your bot trades crypto while you sleep")
    build(os.path.join(ORG_IMG, "facebook_reel.png"),
          "Let AI trade crypto for you — 24/7")
    build_square(os.path.join(ORG_IMG, "square_post.png"),
                 "Let AI trade crypto for you — 24/7")
    build_video(os.path.join(ORG_VID, "reel.mp4"),
                "Let AI trade crypto for you — 24/7")
    for i, cfg in enumerate(CFGS, 1):
        build_card(os.path.join(ORG_IMG, f"reel_{i:02d}.png"), cfg)
    for i, cfg in enumerate(CFGS_15, 11):   # reel_11..reel_25 (varied window + color)
        build_card(os.path.join(ORG_IMG, f"reel_{i:02d}.png"), cfg)
    for i, cfg in enumerate(CFGS_CHART, 26):  # reel_26..reel_30 (Strategy Chart)
        build_card(os.path.join(ORG_IMG, f"reel_{i:02d}.png"), cfg)
    # --- paid-ads-safe (feature-led, no profit claims) ---
    for i, cfg in enumerate(CFGS_SAFE, 1):
        build_safe(os.path.join(PAID_IMG, f"reel_safe_{i:02d}.png"), cfg)
    # Ad-safe MP4 reels — safe-zone layout, 9:16 (works for TikTok + FB/IG Reels).
    for i, cfg in enumerate(CFGS_SAFE, 1):
        build_safe_video(os.path.join(PAID_VID, f"reel_safe_{i:02d}.mp4"), cfg)
