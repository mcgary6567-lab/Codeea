"""Render a mockup of the current (dark) GUI to PNG.

render(out, safe=False) draws the Trevolto main window. With
safe=True it renders a paid-ads-safe variant: SAFE MODE / demo badge, no profit
figures, neutral positions — for marketing where profit claims aren't allowed.
Run:  python make_mockup.py  ->  gui_mockup.png
"""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from matplotlib.patches import FancyBboxPatch, Rectangle

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "gui_mockup.png")

BG = "#0e1117"; PANEL = "#161b22"; ELEV = "#1c2331"; HEADER = "#0b0e14"
BORDER = "#2a2f3a"; TXT = "#e6edf3"; DIM = "#8b949e"; ACCENT = "#4a9eff"
GREEN = "#3fb950"; RED = "#f85149"


def render(out=OUT, safe=False):
    fig, ax = plt.subplots(figsize=(13, 8.6))
    ax.set_xlim(0, 13); ax.set_ylim(0, 8.6); ax.axis("off")
    fig.patch.set_facecolor(BG)

    def panel(x, y, w, h, title=None):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.05",
                                    fc=PANEL, ec=BORDER, lw=1.4, zorder=2))
        if title:
            ax.text(x + 0.18, y + h - 0.04, title, fontsize=9.5, color=ACCENT,
                    fontweight="bold", va="center", ha="left", zorder=4)

    def field(x, y, w, label, value="", h=0.32, show=False):
        ax.text(x, y + h / 2, label, fontsize=8.3, va="center", ha="right", color=DIM)
        ax.add_patch(Rectangle((x + 0.1, y), w, h, fc=ELEV, ec=BORDER, lw=1, zorder=3))
        ax.text(x + 0.22, y + h / 2, ("•" * 14 if show else value), fontsize=8.3,
                va="center", ha="left", color=TXT, zorder=4)

    def button(x, y, w, h, text, fc, tc="white", fs=10):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.05",
                                    fc=fc, ec="none", zorder=3))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color=tc,
                fontsize=fs, fontweight="bold", zorder=4)

    def chip(x, y, text, fc, tc):
        w = 0.16 + 0.072 * len(text)
        ax.add_patch(FancyBboxPatch((x, y), w, 0.26, boxstyle="round,pad=0.02,rounding_size=0.08",
                                    fc=fc, ec=BORDER, lw=1, zorder=3))
        ax.text(x + w / 2, y + 0.13, text, ha="center", va="center", color=tc,
                fontsize=7.4, fontweight="bold", zorder=4)
        return w

    ax.add_patch(Rectangle((0.12, 0.12), 12.76, 8.36, fc=BG, ec="#000000", lw=1.5, zorder=1))

    # ===== header =====
    ax.add_patch(Rectangle((0.12, 7.78), 12.76, 0.7, fc=HEADER, ec="none", zorder=2))
    try:
        ax.add_artist(AnnotationBbox(OffsetImage(plt.imread(os.path.join(HERE, "logo.png")), zoom=0.15),
                                     (0.5, 8.13), frameon=False, zorder=5))
    except Exception:
        pass
    ax.text(0.88, 8.2, "Trevolto", fontsize=14, fontweight="bold", color=TXT, va="center", zorder=5)
    ax.text(0.9, 7.92, "v1.0.0", fontsize=8, color=DIM, va="center", zorder=5)
    ax.text(6.5, 8.13, "● Connected", color=GREEN, fontsize=9.5, fontweight="bold", va="center", zorder=5)
    if safe:
        button(7.85, 8.0, 1.05, 0.26, "SAFE MODE", ACCENT, "#1a1100", fs=7.5)
        ax.text(9.1, 8.13, "Bitget  ·  demo balance $1,000.00  ·  paper trading",
                color=DIM, fontsize=8.6, va="center", zorder=5)
    else:
        button(7.85, 8.0, 0.62, 0.26, "LIVE", RED, fs=7.5)
        ax.text(8.7, 8.13, "Bitget  ·  $12,504.23  ·  PnL +$755.50", color=GREEN,
                fontsize=8.6, fontweight="bold", va="center", zorder=5)
    ax.text(12.62, 8.13, "⚙", color=DIM, fontsize=14, ha="right", va="center", zorder=5)

    # ===== setup / status strip =====
    ax.add_patch(Rectangle((0.12, 7.42), 12.76, 0.34, fc=BG, ec="none", zorder=2))
    ax.text(0.3, 7.59, "Setup:", fontsize=8.2, color=DIM, va="center", zorder=4)
    ax.text(0.92, 7.59, "✓ Connect", fontsize=8.2, color=GREEN, va="center", zorder=4)
    ax.text(2.05, 7.59, "✓ Activate license", fontsize=8.2, color=GREEN, va="center", zorder=4)
    ax.text(3.7, 7.59, "▶ Start trading", fontsize=8.2, color=ACCENT, fontweight="bold", va="center", zorder=4)
    chips = [("Strategy ●", True), ("Webhook ○", False), ("Cloud ●", True),
             ("Safe Mode ●", True) if safe else ("Paused ○", False)]
    xx = 6.6
    for label, on in chips:
        xx += chip(xx, 7.46, label, ELEV, GREEN if "●" in label else DIM) + 0.12

    # ===== LEFT: API settings =====
    panel(0.35, 4.5, 6.0, 2.78, title="API Settings")
    field(2.0, 6.78, 3.9, "Select Exchange:", "Bitget  ▾")
    field(2.0, 6.42, 3.9, "API Key:", show=True)
    field(2.0, 6.06, 3.9, "API Secret:", show=True)
    field(2.0, 5.70, 3.9, "Passphrase:", show=True)
    field(2.0, 5.34, 1.7, "Market:", "Futures ▾")
    ax.text(4.0, 5.5, "Show my IP", fontsize=8, color=ACCENT, va="center", zorder=4)
    button(1.7, 4.92, 1.9, 0.34, "Connect", GREEN, fs=9.5)
    button(3.75, 4.92, 1.9, 0.34, "Disconnect", RED, fs=9.5)
    button(0.5, 4.56, 1.85, 0.3, "↗ Chart", ACCENT, "#1a1100", fs=8)
    button(2.45, 4.56, 1.85, 0.3, "Backtest", ACCENT, "#1a1100", fs=8)
    button(4.4, 4.56, 1.85, 0.3, "Analytics", ACCENT, "#1a1100", fs=8)

    button(0.7, 3.95, 2.6, 0.42, "▲ BUY", GREEN, fs=13)
    button(3.65, 3.95, 2.6, 0.42, "▼ SELL", RED, fs=13)

    # ===== LEFT: open positions =====
    title = "Open Positions  (Safe Mode — simulated)" if safe else "Open Positions"
    panel(0.35, 0.4, 6.0, 3.35, title=title)
    ax.text(0.55, 3.36, "Symbol:", fontsize=8.3, color=DIM, va="center")
    ax.add_patch(Rectangle((1.25, 3.21), 1.2, 0.3, fc=ELEV, ec=BORDER, lw=1, zorder=3))
    ax.text(1.35, 3.36, "BTC/USDT", fontsize=8, va="center", color=TXT, zorder=4)
    ax.text(2.7, 3.36, "Current: 67,512.0", fontsize=8.6, fontweight="bold", color=ACCENT, va="center", zorder=4)
    button(5.05, 3.21, 1.15, 0.3, "↻ Refresh", ELEV, TXT, fs=7.5)
    cols = ["Pair", "Type", "Size", "Entry", "Current", "PnL", "Status"]
    cx = [0.55, 1.7, 2.5, 3.2, 4.0, 4.9, 5.5]
    ax.add_patch(Rectangle((0.45, 2.78), 5.8, 0.3, fc=HEADER, ec=BORDER, lw=1, zorder=3))
    for c, x in zip(cols, cx):
        ax.text(x, 2.93, c, fontsize=7.6, fontweight="bold", color=DIM, va="center", zorder=4)
    if safe:
        rows = [
            ("BTC/USDT", "Long", "0.001", "67,500", "67,540", "demo", "Active", DIM),
            ("ETH/USDT", "Long", "0.05", "3,200", "3,206", "demo", "Active", DIM),
            ("SOL/USDT", "Long", "2", "150", "149", "demo", "Active", DIM),
        ]
    else:
        rows = [
            ("BTC/USDT", "Long", "0.01", "67,500", "70,640", "+214.00", "Active", GREEN),
            ("ETH/USDT", "Long", "0.6", "3,200", "3,520", "+188.40", "Active", GREEN),
            ("SOL/USDT", "Long", "8", "146", "165", "+152.10", "Active", GREEN),
            ("XRP/USDT", "Long", "900", "0.52", "0.63", "+104.20", "Active", GREEN),
            ("ADA/USDT", "Long", "1,400", "0.45", "0.52", "+96.80", "Active", GREEN),
        ]
    for i, row in enumerate(rows):
        yy = 2.46 - i * 0.3
        for j, (val, x) in enumerate(zip(row[:7], cx)):
            ax.text(x, yy, val, fontsize=7.4, color=(row[7] if j == 5 else TXT), va="center", zorder=4,
                    fontweight="bold" if j == 5 else "normal")
    button(0.5, 0.55, 1.55, 0.32, "Close Selected", ELEV, TXT, fs=7.5)
    button(2.15, 0.55, 1.05, 0.32, "Close %", ELEV, TXT, fs=7.5)
    button(3.3, 0.55, 1.95, 0.32, "PANIC: Close All", RED, fs=8)

    # ===== RIGHT: Trade Settings (5 tabs) =====
    panel(6.55, 4.0, 6.05, 3.28, title="Trade Settings")
    tabs = [("Execution", True), ("Modes & Risk", False), ("Strategy", False),
            ("Connect & License", False), ("Alerts", False)]
    tx = 6.72
    for name, active in tabs:
        w = 0.14 + 0.082 * len(name)
        ax.add_patch(Rectangle((tx, 6.74), w, 0.3, fc=ELEV if active else HEADER, ec=BORDER, lw=1, zorder=3))
        ax.text(tx + w / 2, 6.89, name, fontsize=7.3, fontweight="bold" if active else "normal",
                color=ACCENT if active else DIM, ha="center", va="center", zorder=4)
        tx += w + 0.1
    field(8.05, 6.28, 1.7, "Trade Size:", "0.001")
    ax.text(9.95, 6.44, "base (BTC)", fontsize=8, color=DIM, va="center")
    field(8.05, 5.9, 3.3, "Sizing mode:", "Risk % per trade (stop-based)  ▾")
    ax.text(6.78, 5.57, "Risk %:", fontsize=8.3, color=DIM, va="center")
    ax.add_patch(Rectangle((7.32, 5.42), 0.5, 0.3, fc=ELEV, ec=BORDER, lw=1, zorder=3))
    ax.text(7.42, 5.57, "1.0", fontsize=8.3, va="center", color=TXT, zorder=4)
    ax.text(7.95, 5.57, "☑ Auto-place SL/TP (scale out TP1/TP2)", fontsize=8.3, color=TXT, va="center")
    field(8.05, 5.04, 1.0, "Order type:", "limit ▾")
    ax.text(9.3, 5.2, "Leverage:", fontsize=8.3, color=DIM, va="center")
    ax.add_patch(Rectangle((9.95, 5.04), 0.5, 0.3, fc=ELEV, ec=BORDER, lw=1, zorder=3))
    ax.text(10.05, 5.2, "5", fontsize=8.3, va="center", color=TXT, zorder=4)
    ax.text(10.6, 5.2, "x  · isolated", fontsize=8.3, color=DIM, va="center")
    ax.plot([6.78, 12.4], [4.78, 4.78], color=BORDER, lw=1, zorder=3)
    ax.text(12.4, 4.55, "● unsaved changes", fontsize=7.5, color=ACCENT, ha="right", va="center", zorder=4)
    button(6.75, 4.08, 1.55, 0.32, "Save", ACCENT, "#1a1100", fs=8.5)
    button(8.4, 4.08, 1.45, 0.32, "Backup", ELEV, TXT, fs=8)
    button(9.95, 4.08, 1.45, 0.32, "Restore", ELEV, TXT, fs=8)
    button(11.5, 4.08, 0.95, 0.32, "↻ Reset", RED, fs=8)

    # ===== RIGHT: trade log =====
    panel(6.55, 0.4, 6.05, 3.35, title="Trade Log")
    lcols = ["Time", "Signal", "Pair", "Status", "Message"]
    lx = [6.75, 7.6, 8.5, 9.5, 10.6]
    ax.add_patch(Rectangle((6.65, 3.12), 5.85, 0.3, fc=HEADER, ec=BORDER, lw=1, zorder=3))
    for c, x in zip(lcols, lx):
        ax.text(x, 3.27, c, fontsize=7.6, fontweight="bold", color=DIM, va="center", zorder=4)
    if safe:
        logs = [
            ("12:45:17", "BUY", "BTC/USDT", "Sim", "SAFE MODE — simulated fill (no real order)", DIM),
            ("12:45:17", "SL/TP", "BTC/USDT", "OK", "SL 66500 · TP1 68200 · TP2 69000", TXT),
            ("12:42:01", "TP1", "BTC/USDT", "Event", "stop moved to breakeven @ 67000", ACCENT),
            ("12:41:30", "STRAT", "ETH/USDT", "Signal", "EMA20/RSI cross — entry 3,200", TXT),
            ("12:40:02", "—", "SOL/USDT", "Blocked", "cooldown active (30s)", RED),
            ("11:30:05", "INFO", "—", "OK", "demo mode — fills are simulated", DIM),
        ]
    else:
        logs = [
            ("12:45:17", "BUY", "BTC/USDT", "Filled", "LIMIT BUY 0.001 @ 67000", GREEN),
            ("12:45:17", "SL/TP", "BTC/USDT", "OK", "SL 66500 · TP1 68200 · TP2 69000", TXT),
            ("12:42:01", "TP1", "BTC/USDT", "Event", "stop moved to breakeven @ 67000", ACCENT),
            ("12:41:30", "STRAT", "ETH/USDT", "Short", "EMA20/RSI cross — entry 3,200", GREEN),
            ("12:40:02", "—", "SOL/USDT", "Blocked", "cooldown active (30s)", RED),
            ("11:30:05", "CLOSE", "ADA/USDT", "Closed", "realized PnL +4.20", GREEN),
        ]
    for i, row in enumerate(logs):
        yy = 2.84 - i * 0.32
        for j, (val, x) in enumerate(zip(row[:5], lx)):
            ax.text(x, yy, val, fontsize=7.1, color=(row[5] if j in (1, 3) else TXT), va="center", zorder=4)
    button(6.75, 0.55, 1.3, 0.3, "Export Log", ELEV, TXT, fs=7.5)
    button(8.2, 0.55, 1.2, 0.3, "Clear Log", ELEV, TXT, fs=7.5)

    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"wrote {out}{' (safe)' if safe else ''}")


# ---------------------------------------------------------------------------
# Extra window mockups (Chart / Backtest / Analytics) for marketing variety.
# ---------------------------------------------------------------------------
def _new_ax():
    fig, ax = plt.subplots(figsize=(13, 8.6))
    ax.set_xlim(0, 13); ax.set_ylim(0, 8.6); ax.axis("off")
    fig.patch.set_facecolor(BG)
    ax.add_patch(Rectangle((0.12, 0.12), 12.76, 8.36, fc=BG, ec="#000000", lw=1.5, zorder=1))
    ax.add_patch(Rectangle((0.12, 7.78), 12.76, 0.7, fc=HEADER, ec="none", zorder=2))
    return fig, ax


def _hdr(ax, title, right="● live"):
    try:
        ax.add_artist(AnnotationBbox(OffsetImage(plt.imread(os.path.join(HERE, "logo.png")), zoom=0.15),
                                     (0.5, 8.13), frameon=False, zorder=5))
    except Exception:
        pass
    ax.text(0.9, 8.13, title, fontsize=13, fontweight="bold", color=TXT, va="center", zorder=5)
    ax.text(12.62, 8.13, right, color=GREEN, fontsize=10, fontweight="bold", ha="right", va="center", zorder=5)


def _panel(ax, x, y, w, h, title=None):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.05",
                                fc=PANEL, ec=BORDER, lw=1.4, zorder=2))
    if title:
        ax.text(x + 0.18, y + h - 0.04, title, fontsize=9.5, color=ACCENT, fontweight="bold",
                va="center", ha="left", zorder=4)


def _btn(ax, x, y, w, h, text, fc, tc="white", fs=11):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.05",
                                fc=fc, ec="none", zorder=3))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color=tc, fontsize=fs,
            fontweight="bold", zorder=4)


def render_chart(out=None):
    """Faithful Strategy Chart window: toolbar + legend, candles with EMA20 and
    BUY/SELL markers, entry/SL/TP1/TP2 level lines + right price axis, a volume
    pane, and an RSI pane with shaded overbought/oversold bands."""
    import numpy as np
    out = out or os.path.join(HERE, "gui_chart.png")
    BLUE = "#4a9eff"; EMAB = "#5b8def"; SLC = "#4a9eff"; TPC = "#26d07c"; RSIC = "#c792ea"
    fig, ax = _new_ax()
    _hdr(ax, "Strategy Chart — Trevolto", right="")

    # --- toolbar row ---
    ax.text(0.45, 7.62, "☑ Follow Strategy tab", fontsize=8.3, color=TXT, va="center", zorder=5)
    ax.text(2.9, 7.62, "Symbol", fontsize=8.3, color=DIM, va="center", zorder=5)
    ax.add_patch(Rectangle((3.55, 7.46), 1.3, 0.3, fc=ELEV, ec=BORDER, lw=1, zorder=3))
    ax.text(3.66, 7.62, "BTC/USDT ▾", fontsize=8, color=TXT, va="center", zorder=4)
    ax.text(5.1, 7.62, "Timeframe", fontsize=8.3, color=DIM, va="center", zorder=5)
    ax.add_patch(Rectangle((6.0, 7.46), 0.75, 0.3, fc=ELEV, ec=BORDER, lw=1, zorder=3))
    ax.text(6.1, 7.62, "15m ▾", fontsize=8, color=TXT, va="center", zorder=4)
    ax.text(9.95, 7.62, "299 candles · 38 entries", fontsize=8.2, color=DIM, ha="right", va="center", zorder=5)
    _btn(ax, 10.15, 7.45, 1.05, 0.32, "↻ Refresh", ACCENT, "#1a1100", fs=8.5)

    # --- legend row ---
    ax.text(0.45, 7.22, "● following · BTC/USDT · 15m · Bitget futures",
            fontsize=8, color=GREEN, fontweight="bold", va="center", zorder=5)
    leg = [("▲ BUY", GREEN), ("▼ SELL", RED), ("⊙ scale-out", "#ffd54a"), ("✕ exit", DIM),
           ("— entry", BLUE), ("— SL", SLC), ("— TP1/TP2", TPC), ("— EMA20", EMAB)]
    lx = 4.7
    for t, c in leg:
        ax.text(lx, 7.22, t, fontsize=7.2, color=c, va="center", zorder=5)
        lx += 0.18 + 0.052 * len(t)
    ax.text(12.5, 7.22, "scroll = zoom · drag = pan", fontsize=7.2, color=DIM, ha="right", va="center", zorder=5)

    px0, px1 = 0.35, 11.9        # plot left/right (right margin = axis labels)
    py0, py1 = 4.05, 7.0         # price pane
    vy0, vy1 = 2.95, 3.85        # volume pane
    ry0, ry1 = 0.45, 2.6         # rsi pane

    # --- synth candles: rise, fall, then sideways (like the screenshot) ---
    n = 78
    rng = np.random.default_rng(7)
    seg = np.concatenate([np.linspace(0, 11, 26), np.linspace(11, -2, 28), np.linspace(-2, 0.5, n - 54)])
    base = 62900 + seg * 100 + np.cumsum(rng.normal(0, 28, n))
    op = base + rng.normal(0, 35, n)
    cl = base + rng.normal(0, 45, n)
    hi = np.maximum(op, cl) + np.abs(rng.normal(40, 30, n))
    lo = np.minimum(op, cl) - np.abs(rng.normal(40, 30, n))
    pmin, pmax = 61897, 64288     # match the screenshot's axis

    def X(i): return px0 + (px1 - px0) * i / (n - 1)
    def Yp(p): return py0 + (py1 - py0) * (p - pmin) / (pmax - pmin)

    # price gridlines + right axis labels
    for p in (64287.65, 63690.08, 63092.50, 62494.93, 61897.35):
        ax.plot([px0, px1], [Yp(p)] * 2, color=BORDER, lw=0.6, zorder=2)
        ax.text(px1 + 0.05, Yp(p), f"{p:,.2f}", fontsize=7, color=DIM, va="center", zorder=4)
    bw = (px1 - px0) / n * 0.62
    for i in range(n):
        up = cl[i] >= op[i]; col = GREEN if up else RED
        ax.plot([X(i), X(i)], [Yp(lo[i]), Yp(hi[i])], color=col, lw=0.7, zorder=3)
        y1, y2 = Yp(min(op[i], cl[i])), Yp(max(op[i], cl[i]))
        ax.add_patch(Rectangle((X(i) - bw / 2, y1), bw, max(0.015, y2 - y1),
                               fc=(BG if up else col), ec=col, lw=0.7, zorder=3))
    ema = np.convolve(cl, np.ones(9) / 9, mode="same")
    ax.plot([X(i) for i in range(4, n - 4)], [Yp(ema[i]) for i in range(4, n - 4)], color=EMAB, lw=1.4, zorder=4)

    # level lines + right labels
    for price, col, dash, lab in [(63344, SLC, (0, (4, 3)), "SL 0.71% 63,344"),
                                  (62890, BLUE, None, "SELL ENTRY 62,890"),
                                  (62454, TPC, (0, (4, 3)), "TP1 -0.71% 62,454"),
                                  (62000, TPC, (0, (4, 3)), "TP2 -1.42% 62,000")]:
        ax.plot([px0, px1], [Yp(price)] * 2, color=col, lw=1.1, ls=(dash or "-"), zorder=4)
        ax.text(px1 + 0.05, Yp(price), lab, fontsize=6.6, color=col, va="center", zorder=5)

    # BUY/SELL markers at swing points
    buys = [3, 9, 12, 16, 22, 41, 50, 60, 71]
    sells = [6, 18, 21, 25, 30, 47, 66, 68]
    for i in buys:
        ax.scatter(X(i), Yp(lo[i]) - 0.12, marker="^", s=55, color=GREEN, zorder=5)
        ax.text(X(i), Yp(lo[i]) - 0.27, "BUY", color=GREEN, fontsize=5.6, fontweight="bold", ha="center", zorder=5)
    for i in sells:
        ax.scatter(X(i), Yp(hi[i]) + 0.12, marker="v", s=55, color=RED, zorder=5)
        ax.text(X(i), Yp(hi[i]) + 0.27, "SELL", color=RED, fontsize=5.6, fontweight="bold", ha="center", zorder=5)
    # a scale-out ring near the lows
    ax.scatter(X(36), Yp(lo[36]) - 0.35, marker="o", s=70, facecolors="none", edgecolors="#ffd54a", lw=1.5, zorder=5)

    # --- volume pane ---
    ax.text(px0 + 0.04, vy1 - 0.06, "Vol", fontsize=7, color=DIM, va="center", zorder=4)
    vmax = np.abs(cl - op).max() + 60
    for i in range(n):
        vh = (vy1 - vy0) * (abs(cl[i] - op[i]) + rng.uniform(10, 60)) / vmax
        col = GREEN if cl[i] >= op[i] else RED
        ax.add_patch(Rectangle((X(i) - bw / 2, vy0), bw, vh, fc=col, ec="none", alpha=0.55, zorder=3))

    # --- RSI pane with shaded OB/OS bands ---
    ax.add_patch(Rectangle((px0, ry0), px1 - px0, ry1 - ry0, fc="#12161e", ec=BORDER, lw=0.8, zorder=2))

    def Yr(v): return ry0 + (ry1 - ry0) * np.clip(v, 0, 100) / 100
    ax.add_patch(Rectangle((px0, Yr(70)), px1 - px0, ry1 - Yr(70), fc=SLC, ec="none", alpha=0.18, zorder=2))
    ax.add_patch(Rectangle((px0, ry0), px1 - px0, Yr(30) - ry0, fc=SLC, ec="none", alpha=0.18, zorder=2))
    for lv in (70, 50, 30):
        ax.plot([px0, px1], [Yr(lv)] * 2, color=BORDER, lw=0.6, ls=(0, (3, 3)), zorder=3)
        ax.text(px1 + 0.05, Yr(lv), str(lv), fontsize=7, color=DIM, va="center", zorder=4)
    rsi = 52 + 16 * np.sin(np.linspace(0.5, 7, n)) + rng.normal(0, 3.5, n)
    ax.plot([X(i) for i in range(n)], [Yr(rsi[i]) for i in range(n)], color=RSIC, lw=1.2, zorder=4)

    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor=BG); plt.close(fig)
    print(f"wrote {out}")


def render_backtest(out=None):
    import numpy as np
    out = out or os.path.join(HERE, "gui_backtest.png")
    fig, ax = _new_ax()
    _hdr(ax, "Backtest — BTC/USDT · 1h · Last 3 months", right="142 trades")
    # stat cards
    stats = [("Trades", "142", TXT), ("Win rate", "68%", ACCENT), ("Net PnL", "+$842", GREEN),
             ("Profit factor", "2.1", ACCENT), ("Max DD", "6.4%", RED), ("Best", "+$96", GREEN)]
    for i, (lab, val, col) in enumerate(stats):
        x = 0.4 + i * 2.07
        ax.add_patch(FancyBboxPatch((x, 6.05), 1.95, 1.15, boxstyle="round,pad=0.02,rounding_size=0.05",
                                    fc=ELEV, ec=BORDER, lw=1, zorder=2))
        ax.text(x + 0.18, 6.95, lab.upper(), fontsize=7.5, color=DIM, va="center", zorder=4)
        ax.text(x + 0.18, 6.45, val, fontsize=16, color=col, fontweight="bold", va="center", zorder=4)
    # equity curve
    _panel(ax, 0.4, 0.4, 8.4, 5.3, title="Equity curve")
    ex0, ex1, ey0, ey1 = 0.7, 8.6, 0.8, 5.3
    rng = np.random.default_rng(5)
    eq = 1000 + np.cumsum(np.abs(rng.normal(7, 9, 90)))
    emin, emax = eq.min(), eq.max()
    xs = [ex0 + (ex1 - ex0) * i / (len(eq) - 1) for i in range(len(eq))]
    ys = [ey0 + (ey1 - ey0) * (e - emin) / (emax - emin) for e in eq]
    ax.fill_between(xs, ys, ey0, color=GREEN, alpha=0.12, zorder=2)
    ax.plot(xs, ys, color=GREEN, lw=2, zorder=3)
    ax.text(ex1 - 0.1, ys[-1] + 0.2, "+84.2%", color=GREEN, fontsize=11, fontweight="bold", ha="right", zorder=4)
    # trades table
    _panel(ax, 9.0, 0.4, 3.6, 5.3, title="Trades")
    rows = [("BTC", "long", "+96"), ("ETH", "long", "+72"), ("SOL", "short", "+58"),
            ("XRP", "long", "+41"), ("BTC", "long", "+33"), ("ADA", "long", "-12")]
    for i, (s, side, pnl) in enumerate(rows):
        yy = 4.85 - i * 0.6
        ax.text(9.25, yy, s, fontsize=8.5, color=TXT, va="center", zorder=4)
        ax.text(10.4, yy, side, fontsize=8.5, color=DIM, va="center", zorder=4)
        ax.text(12.4, yy, pnl, fontsize=8.5, color=(GREEN if "+" in pnl else RED),
                fontweight="bold", ha="right", va="center", zorder=4)
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor=BG); plt.close(fig)
    print(f"wrote {out}")


def render_analytics(out=None):
    import numpy as np
    out = out or os.path.join(HERE, "gui_analytics.png")
    fig, ax = _new_ax()
    _hdr(ax, "Performance — Analytics", right="● online")
    tiles = [("Total", "142", TXT), ("Win rate", "68%", ACCENT), ("Wins", "96", GREEN),
             ("Losses", "46", RED), ("Realized PnL", "+$842", GREEN),
             ("Best", "+$96", GREEN), ("Worst", "-$22", RED), ("Profit factor", "2.1", ACCENT)]
    for i, (lab, val, col) in enumerate(tiles):
        x = 0.4 + (i % 4) * 3.12
        y = 5.55 - (i // 4) * 1.35
        ax.add_patch(FancyBboxPatch((x, y), 2.98, 1.15, boxstyle="round,pad=0.02,rounding_size=0.05",
                                    fc=ELEV, ec=BORDER, lw=1, zorder=2))
        ax.text(x + 0.2, y + 0.85, lab.upper(), fontsize=8, color=DIM, va="center", zorder=4)
        ax.text(x + 0.2, y + 0.38, val, fontsize=18, color=col, fontweight="bold", va="center", zorder=4)
    _panel(ax, 0.4, 0.4, 12.2, 3.4, title="Equity curve  (balance over time)")
    ex0, ex1, ey0, ey1 = 0.7, 12.4, 0.8, 3.4
    rng = np.random.default_rng(3)
    eq = 1000 + np.cumsum(np.abs(rng.normal(8, 10, 110)))
    emin, emax = eq.min(), eq.max()
    xs = [ex0 + (ex1 - ex0) * i / (len(eq) - 1) for i in range(len(eq))]
    ys = [ey0 + (ey1 - ey0) * (e - emin) / (emax - emin) for e in eq]
    ax.fill_between(xs, ys, ey0, color=GREEN, alpha=0.12, zorder=2)
    ax.plot(xs, ys, color=GREEN, lw=2, zorder=3)
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor=BG); plt.close(fig)
    print(f"wrote {out}")


if __name__ == "__main__":
    render(OUT, safe=False)
    render(os.path.join(HERE, "gui_mockup_safe.png"), safe=True)
    render_chart()
    render_backtest()
    render_analytics()
