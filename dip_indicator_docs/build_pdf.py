"""
Builds a PDF documenting the Dip Red + 2 Green Pine Script indicator,
with candlestick chart examples and diagrams.
"""
import os
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Rectangle, FancyBboxPatch, FancyArrowPatch
from matplotlib.backends.backend_pdf import PdfPages

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_PATH = os.path.join(OUT_DIR, "Dip2Green_Indicator_Guide.pdf")

# ----------------------------- helpers -----------------------------

def draw_candles(ax, ohlc, highlight=None, labels=None):
    """ohlc: list of (open, high, low, close). highlight: dict idx->color for body fill."""
    for i, (o, h, l, c) in enumerate(ohlc):
        up = c >= o
        body_color = "#26a69a" if up else "#ef5350"
        if highlight and i in highlight:
            body_color = highlight[i]
        # wick
        ax.plot([i, i], [l, h], color="black", linewidth=1, zorder=2)
        # body
        bottom = min(o, c)
        height = max(abs(c - o), 0.02)
        rect = Rectangle((i - 0.3, bottom), 0.6, height,
                         facecolor=body_color, edgecolor="black", linewidth=0.8, zorder=3)
        ax.add_patch(rect)
        if labels and i in labels:
            ax.annotate(labels[i], xy=(i, h), xytext=(i, h + 0.15),
                        ha="center", fontsize=8, fontweight="bold")
    ax.set_xlim(-0.8, len(ohlc) - 0.2)
    lows = [x[2] for x in ohlc]
    highs = [x[1] for x in ohlc]
    pad = (max(highs) - min(lows)) * 0.25
    ax.set_ylim(min(lows) - pad, max(highs) + pad)
    ax.set_xticks(range(len(ohlc)))
    ax.set_xticklabels([f"B{i+1}" for i in range(len(ohlc))], fontsize=8)
    ax.grid(True, alpha=0.3)


def text_page(pdf, title, paragraphs):
    fig, ax = plt.subplots(figsize=(8.5, 11))
    ax.axis("off")
    ax.text(0.05, 0.95, title, fontsize=20, fontweight="bold", transform=ax.transAxes)
    ax.plot([0.05, 0.95], [0.93, 0.93], transform=ax.transAxes, color="black", linewidth=1)
    y = 0.88
    for kind, txt in paragraphs:
        if kind == "h2":
            y -= 0.02
            ax.text(0.05, y, txt, fontsize=13, fontweight="bold",
                    color="#1565c0", transform=ax.transAxes)
            y -= 0.025
        elif kind == "p":
            for line in wrap(txt, 95):
                ax.text(0.05, y, line, fontsize=10, transform=ax.transAxes)
                y -= 0.018
            y -= 0.008
        elif kind == "code":
            for line in txt.split("\n"):
                ax.text(0.07, y, line, fontsize=9, family="monospace",
                        color="#263238", transform=ax.transAxes)
                y -= 0.016
            y -= 0.008
        elif kind == "bullet":
            for line in wrap(txt, 92):
                ax.text(0.07, y, "• " + line if line == wrap(txt, 92)[0] else "  " + line,
                        fontsize=10, transform=ax.transAxes)
                y -= 0.018
            y -= 0.004
    pdf.savefig(fig)
    plt.close(fig)


def wrap(text, width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= width:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


# ============================ PAGES =============================

def page_cover(pdf):
    fig, ax = plt.subplots(figsize=(8.5, 11))
    ax.axis("off")
    ax.add_patch(Rectangle((0, 0.78), 1, 0.22, transform=ax.transAxes,
                           facecolor="#0d47a1"))
    ax.text(0.5, 0.90, "Dip Red + 2 Green", fontsize=32, fontweight="bold",
            color="white", ha="center", transform=ax.transAxes)
    ax.text(0.5, 0.84, "Universal TradingView Indicator — Visual Guide",
            fontsize=14, color="white", ha="center", transform=ax.transAxes)

    # demo mini-chart
    np.random.seed(2)
    ohlc = [(100, 100.4, 99.6, 100.2), (100.2, 100.3, 99.5, 99.6),
            (99.6, 99.8, 98.5, 98.8), (98.8, 98.9, 97.0, 97.2),
            (97.2, 97.3, 95.5, 95.8),  # dip red
            (95.8, 96.8, 95.7, 96.6),  # green 1
            (96.6, 97.5, 96.5, 97.3),  # green 2 - signal
            (97.3, 98.0, 97.1, 97.9)]
    sub = fig.add_axes([0.15, 0.30, 0.7, 0.40])
    draw_candles(sub, ohlc,
                 highlight={4: "#ffeb3b"},
                 labels={4: "DIP", 6: "BUY"})
    sub.set_title("Core idea: oversold red dip → wait for greens → BUY",
                  fontsize=11)
    sub.set_ylabel("Price")

    ax.text(0.5, 0.18, "Core Logic • Improvements • Examples",
            fontsize=12, ha="center", style="italic", transform=ax.transAxes)
    ax.text(0.5, 0.14, "Pine Script v6  |  Multi-Asset Preset Engine",
            fontsize=10, ha="center", color="#555", transform=ax.transAxes)
    ax.text(0.5, 0.06, "Generated with Claude Code", fontsize=9,
            ha="center", color="#888", transform=ax.transAxes)
    pdf.savefig(fig)
    plt.close(fig)


def page_overview(pdf):
    fig, ax = plt.subplots(figsize=(8.5, 11))
    ax.axis("off")
    ax.text(0.5, 0.96, "1. Pipeline Overview", fontsize=18, fontweight="bold",
            ha="center", transform=ax.transAxes)

    boxes = [
        (0.5, 0.86, "1) Asset Preset Resolution",
         "Auto-detect symbol type → pick RSI/ATR/Volume/Lookback thresholds"),
        (0.5, 0.72, "2) Per-Bar Indicators",
         "RSI(14)   ATR(14)   Volume SMA(20)   hasVolume check"),
        (0.5, 0.58, "3) Dip Red Detection (5 ANDed conditions)",
         "isRed AND rsi≤OS AND range≥ATR×k AND low=lowestN AND volPass"),
        (0.5, 0.44, "4) State Machine",
         "DIP → wait for N greens within maxWait bars → arm signal"),
        (0.5, 0.30, "5) Signal & Risk Levels",
         "BUY arrow, SL=dipLow, TP=entry+(entry−SL)×RR"),
        (0.5, 0.16, "6) Alerts & Visuals",
         "alert() / alertcondition / dashed E/SL/TP lines / labels"),
    ]
    colors = ["#e3f2fd", "#e8f5e9", "#fff3e0", "#fce4ec", "#ede7f6", "#f1f8e9"]
    for (cx, cy, title, desc), col in zip(boxes, colors):
        box = FancyBboxPatch((cx - 0.42, cy - 0.05), 0.84, 0.10,
                             boxstyle="round,pad=0.01",
                             linewidth=1.5, edgecolor="#333",
                             facecolor=col, transform=ax.transAxes)
        ax.add_patch(box)
        ax.text(cx, cy + 0.025, title, fontsize=12, fontweight="bold",
                ha="center", transform=ax.transAxes)
        ax.text(cx, cy - 0.022, desc, fontsize=9.5, ha="center",
                transform=ax.transAxes, color="#333")

    for i in range(5):
        y = 0.81 - i * 0.14
        arr = FancyArrowPatch((0.5, y), (0.5, y - 0.04),
                              arrowstyle="->", mutation_scale=20,
                              color="#333", transform=ax.transAxes)
        ax.add_patch(arr)
    pdf.savefig(fig)
    plt.close(fig)


def page_preset(pdf):
    fig = plt.figure(figsize=(8.5, 11))
    fig.suptitle("2. Asset Preset Resolver", fontsize=18, fontweight="bold", y=0.96)

    ax1 = fig.add_axes([0.07, 0.55, 0.86, 0.32])
    ax1.axis("off")
    ax1.text(0.0, 1.0, "Logic flow", fontsize=12, fontweight="bold", color="#1565c0")
    blocks = [
        (0.1, 0.55, "Read syminfo.ticker\n+ syminfo.type"),
        (0.4, 0.55, "Match keywords:\nJPY, XAU, GOLD,\nXAG, OIL, crypto"),
        (0.75, 0.55, "Pick preset:\nForex / JPY / Crypto /\nGold / Indices / Stocks"),
        (0.4, 0.18, "User preset == 'Auto'  →  use auto.\nElse override with chosen preset.")
    ]
    for x, y, t in blocks:
        ax1.add_patch(FancyBboxPatch((x - 0.13, y - 0.12), 0.26, 0.22,
                                     boxstyle="round,pad=0.01",
                                     facecolor="#e3f2fd", edgecolor="#1565c0"))
        ax1.text(x, y - 0.01, t, ha="center", va="center", fontsize=9)
    ax1.annotate("", xy=(0.27, 0.55), xytext=(0.23, 0.55),
                 arrowprops=dict(arrowstyle="->"))
    ax1.annotate("", xy=(0.62, 0.55), xytext=(0.53, 0.55),
                 arrowprops=dict(arrowstyle="->"))
    ax1.annotate("", xy=(0.4, 0.30), xytext=(0.4, 0.43),
                 arrowprops=dict(arrowstyle="->"))
    ax1.set_xlim(0, 1)
    ax1.set_ylim(0, 1)

    ax2 = fig.add_axes([0.07, 0.06, 0.86, 0.42])
    ax2.axis("off")
    ax2.text(0.0, 1.0, "Effective thresholds per preset", fontsize=12,
             fontweight="bold", color="#1565c0")
    headers = ["Preset", "RSI OS", "Range×ATR", "Vol×Avg", "Lookback"]
    rows = [
        ["Forex Major", "33", "0.5", "0.8", "8"],
        ["JPY Pair", "30", "0.7", "0.9", "10"],
        ["Crypto", "30", "0.8", "1.0", "8"],
        ["Gold/Metals", "32", "0.6", "0.8", "8"],
        ["Indices", "35", "0.5", "0.7", "6"],
        ["Stocks", "32", "0.6", "0.9", "8"],
        ["Custom", "user", "user", "user", "user"],
    ]
    table = ax2.table(cellText=rows, colLabels=headers,
                      loc="center", cellLoc="center",
                      bbox=[0.02, 0.05, 0.96, 0.85])
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    for i in range(len(headers)):
        table[(0, i)].set_facecolor("#1565c0")
        table[(0, i)].set_text_props(color="white", fontweight="bold")
    for r in range(1, len(rows) + 1):
        table[(r, 0)].set_facecolor("#f5f5f5")
        table[(r, 0)].set_text_props(fontweight="bold")
    pdf.savefig(fig)
    plt.close(fig)


def page_dip_detection(pdf):
    fig = plt.figure(figsize=(8.5, 11))
    fig.suptitle("3. Dip Red Detection — Five ANDed Conditions",
                 fontsize=18, fontweight="bold", y=0.96)

    # candle example
    ax = fig.add_axes([0.08, 0.55, 0.84, 0.34])
    ohlc = [
        (100, 100.5, 99.7, 100.3),   # neutral
        (100.3, 100.4, 99.5, 99.7),  # slow drop
        (99.7, 99.8, 98.6, 98.9),
        (98.9, 99.0, 97.4, 97.6),
        (97.6, 97.7, 95.4, 95.6),    # *** DIP RED ***
        (95.6, 96.4, 95.5, 96.2),
        (96.2, 96.8, 96.1, 96.7),
    ]
    draw_candles(ax, ohlc, highlight={4: "#ffeb3b"}, labels={4: "DIP candle"})
    ax.set_title("Candle 5 satisfies all 5 conditions → marked yellow", fontsize=11)
    ax.set_ylabel("Price")

    # condition list
    ax2 = fig.add_axes([0.06, 0.05, 0.88, 0.42])
    ax2.axis("off")
    conds = [
        ("isRed", "close < open  → bearish body"),
        ("rsi ≤ effOS", "Oversold momentum (e.g. RSI ≤ 30 for crypto)"),
        ("range ≥ atr × effATR", "Bar is wide enough — real flush, not noise"),
        ("low ≤ lowest(low, effLook)", "New low vs prior N bars — true pivot dip"),
        ("volPass", "volume ≥ avg × effVol  OR  symbol has no volume data"),
    ]
    ax2.text(0.0, 0.97, "All conditions must be TRUE on the same bar:",
             fontsize=11, fontweight="bold", color="#1565c0")
    y = 0.85
    for name, desc in conds:
        ax2.add_patch(FancyBboxPatch((0.02, y - 0.08), 0.26, 0.10,
                                     boxstyle="round,pad=0.01",
                                     facecolor="#fff3e0", edgecolor="#e65100"))
        ax2.text(0.15, y - 0.03, name, ha="center", va="center",
                 fontweight="bold", fontsize=10)
        ax2.text(0.31, y - 0.03, "→  " + desc, fontsize=10, va="center")
        y -= 0.14
    ax2.text(0.0, 0.05,
             "Auto-skip rule: when ta.cum(volume) == 0 (FX brokers without tick volume),\n"
             "the volume filter is bypassed automatically so the indicator still works.",
             fontsize=9, style="italic", color="#555")
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    pdf.savefig(fig)
    plt.close(fig)


def page_state_machine(pdf):
    fig = plt.figure(figsize=(8.5, 11))
    fig.suptitle("4. State Machine — DIP → Wait → Greens → Signal",
                 fontsize=18, fontweight="bold", y=0.96)

    ax = fig.add_axes([0.05, 0.50, 0.9, 0.42])
    ax.axis("off")
    states = [
        (0.15, 0.7, "IDLE\ndipActive=false", "#eceff1"),
        (0.5, 0.7, "DIP ACTIVE\nlockedDipLow=low\ngreenCount=0", "#fff9c4"),
        (0.85, 0.7, "SIGNAL\nFire BUY\nReset state", "#c8e6c9"),
        (0.5, 0.25, "WAITING\nIncrement greenCount on green\nReset on red\nAbort if low<dipLow\nor barsSinceDip>maxWait", "#ffe0b2"),
    ]
    for x, y, t, c in states:
        ax.add_patch(FancyBboxPatch((x - 0.15, y - 0.10), 0.30, 0.20,
                                    boxstyle="round,pad=0.01",
                                    facecolor=c, edgecolor="#333", linewidth=1.5))
        ax.text(x, y, t, ha="center", va="center", fontsize=9.5)
    arrows = [
        (0.30, 0.7, 0.35, 0.7, "isDipRed"),
        (0.50, 0.60, 0.50, 0.35, "next bar"),
        (0.65, 0.30, 0.85, 0.60, "greenCount ≥ greenN"),
        (0.35, 0.30, 0.15, 0.60, "abort / timeout"),
        (0.85, 0.60, 0.30, 0.70, ""),  # signal back to idle
    ]
    for x1, y1, x2, y2, label in arrows:
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="->", color="#1565c0", lw=1.5))
        if label:
            ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.025, label,
                    fontsize=8.5, color="#1565c0", ha="center",
                    bbox=dict(facecolor="white", edgecolor="none", pad=1))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # candles showing 3 scenarios
    ax_c = fig.add_axes([0.06, 0.06, 0.88, 0.36])
    ax_c.axis("off")
    ax_c.text(0.0, 1.0, "Three timeline outcomes", fontsize=12,
              fontweight="bold", color="#1565c0")

    # scenario A: success
    scA = ax_c.inset_axes([0.0, 0.05, 0.31, 0.85])
    o = [(99,99.2,98.7,98.8),(98.8,98.9,97.2,97.3),(97.3,98.1,97.2,98.0),(98.0,98.7,97.9,98.6)]
    draw_candles(scA, o, highlight={1:"#ffeb3b"}, labels={1:"Dip",3:"BUY"})
    scA.set_title("A) Success: 2 greens fire", fontsize=9)

    # scenario B: aborted by new low
    scB = ax_c.inset_axes([0.34, 0.05, 0.31, 0.85])
    o = [(99,99.2,98.7,98.8),(98.8,98.9,97.2,97.3),(97.3,97.8,96.5,96.6),(96.6,96.7,95.8,95.9)]
    draw_candles(scB, o, highlight={1:"#ffeb3b"}, labels={1:"Dip",2:"low<"})
    scB.set_title("B) Aborted: new low < dipLow", fontsize=9)

    # scenario C: timeout
    scC = ax_c.inset_axes([0.68, 0.05, 0.31, 0.85])
    o = [(99,99.2,98.7,98.8),(98.8,98.9,97.2,97.3),
         (97.3,97.4,97.0,97.1),(97.1,97.2,96.9,97.0),
         (97.0,97.1,96.8,96.9),(96.9,97.0,96.7,96.8)]
    draw_candles(scC, o, highlight={1:"#ffeb3b"}, labels={1:"Dip"})
    scC.set_title("C) Timeout: maxWait passed", fontsize=9)
    pdf.savefig(fig)
    plt.close(fig)


def page_signal_sltp(pdf):
    fig = plt.figure(figsize=(8.5, 11))
    fig.suptitle("5. Signal Bar + SL / TP Construction",
                 fontsize=18, fontweight="bold", y=0.96)

    ax = fig.add_axes([0.10, 0.40, 0.80, 0.50])
    ohlc = [
        (100, 100.3, 99.7, 100.1),
        (100.1, 100.2, 99.4, 99.6),
        (99.6, 99.7, 98.5, 98.7),
        (98.7, 98.8, 97.2, 97.4),
        (97.4, 97.5, 95.5, 95.7),    # DIP — low becomes SL
        (95.7, 96.6, 95.6, 96.4),    # green 1
        (96.4, 97.4, 96.3, 97.2),    # green 2 → BUY (entry = close)
        (97.2, 97.8, 97.0, 97.6),
        (97.6, 98.4, 97.5, 98.3),
        (98.3, 99.4, 98.2, 99.3),
    ]
    draw_candles(ax, ohlc, highlight={4: "#ffeb3b", 6: "#a5d6a7"},
                 labels={4: "DIP", 6: "BUY"})
    entry = 97.2
    sl = 95.5
    rr = 2.0
    tp = entry + (entry - sl) * rr  # 100.6
    ax.axhline(entry, color="#1565c0", ls="--", lw=1.2, label=f"Entry {entry}")
    ax.axhline(sl, color="#c62828", ls="--", lw=1.2, label=f"SL {sl}")
    ax.axhline(tp, color="#2e7d32", ls="--", lw=1.2, label=f"TP {tp:.2f} (RR 1:2)")
    ax.fill_between([6, len(ohlc) - 0.5], sl, entry, color="#c62828", alpha=0.10)
    ax.fill_between([6, len(ohlc) - 0.5], entry, tp, color="#2e7d32", alpha=0.10)
    ax.legend(loc="lower right", fontsize=9)
    ax.set_title("Risk zone (red) below entry — Reward zone (green) above", fontsize=11)
    ax.set_ylabel("Price")

    ax2 = fig.add_axes([0.06, 0.04, 0.88, 0.30])
    ax2.axis("off")
    ax2.text(0.0, 0.95, "Formulae", fontsize=12, fontweight="bold", color="#1565c0")
    code = (
        "entry  = close                              // signal bar close\n"
        "SL     = lockedDipLow                        // captured at the dip bar\n"
        "TP     = entry + (entry − SL) × rrTarget     // default rrTarget = 2.0\n"
        "risk_pips   = (entry − SL) / mintick / 10\n"
        "reward_pips = (TP − entry) / mintick / 10"
    )
    for i, line in enumerate(code.split("\n")):
        ax2.text(0.02, 0.78 - i * 0.10, line, family="monospace",
                 fontsize=10, color="#263238")
    ax2.text(0.0, 0.10,
             "Strength: SL anchored to a structural low → invalidation is meaningful.\n"
             "Weakness: a tight wick low can produce a too-tight stop. See Improvement #2 (ATR buffer).",
             fontsize=9.5, style="italic")
    pdf.savefig(fig)
    plt.close(fig)


def page_improvement_htf(pdf):
    fig = plt.figure(figsize=(8.5, 11))
    fig.suptitle("Improvement #1 — Higher-Timeframe Trend Filter",
                 fontsize=16, fontweight="bold", y=0.96)

    ax = fig.add_axes([0.08, 0.50, 0.84, 0.40])
    np.random.seed(7)
    n = 40
    base = np.linspace(100, 110, n) + np.cumsum(np.random.randn(n) * 0.3)
    o = base + np.random.randn(n) * 0.1
    c = base + np.random.randn(n) * 0.1
    h = np.maximum(o, c) + np.abs(np.random.randn(n) * 0.3)
    l = np.minimum(o, c) - np.abs(np.random.randn(n) * 0.3)
    ohlc = list(zip(o, h, l, c))
    draw_candles(ax, ohlc)
    ema = np.convolve(c, np.ones(8) / 8, mode="same")
    ax.plot(range(n), ema, color="#ff9800", lw=2, label="EMA(200) proxy — HTF trend")
    # mark fake dips
    ax.scatter([10, 22, 33], [base[10] - 0.3, base[22] - 0.3, base[33] - 0.3],
               s=120, c=["red", "green", "green"], marker="v",
               edgecolors="black", zorder=5)
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title("Only take dips when price is ABOVE the HTF EMA (green ticks)",
                 fontsize=11)

    ax2 = fig.add_axes([0.06, 0.05, 0.88, 0.35])
    ax2.axis("off")
    ax2.text(0.0, 0.95, "Why it matters", fontsize=12, fontweight="bold", color="#1565c0")
    ax2.text(0.02, 0.85,
             "Mean-reversion buys fail in strong downtrends. Filter dips with a higher-TF\n"
             "trend (e.g. 4H EMA50 while trading 15m).",
             fontsize=10)
    code = (
        "htfTrend = request.security(syminfo.tickerid, \"240\",\n"
        "                            close > ta.ema(close, 50))\n"
        "signal := signal and htfTrend"
    )
    ax2.text(0.0, 0.55, "Pine snippet", fontsize=11, fontweight="bold", color="#1565c0")
    for i, line in enumerate(code.split("\n")):
        ax2.text(0.02, 0.45 - i * 0.07, line, family="monospace",
                 fontsize=10, color="#263238")
    ax2.text(0.0, 0.12,
             "Expected impact: cuts low-quality counter-trend signals dramatically;\n"
             "biggest single accuracy improvement you can make.",
             fontsize=9.5, style="italic", color="#2e7d32")
    pdf.savefig(fig)
    plt.close(fig)


def page_improvement_sl(pdf):
    fig = plt.figure(figsize=(8.5, 11))
    fig.suptitle("Improvement #2 — ATR Buffer on Stop-Loss",
                 fontsize=16, fontweight="bold", y=0.96)

    ax = fig.add_axes([0.10, 0.45, 0.80, 0.45])
    ohlc = [
        (100, 100.3, 99.7, 100.1),
        (100.1, 100.2, 99.4, 99.6),
        (99.6, 99.7, 98.5, 98.7),
        (98.7, 98.8, 97.2, 97.4),
        (97.4, 97.5, 95.5, 95.7),    # dip; wick low 95.5
        (95.7, 96.6, 95.6, 96.4),
        (96.4, 97.4, 96.3, 97.2),
        (97.2, 97.5, 95.45, 97.4),   # wick stop-out hits original SL
        (97.4, 98.4, 97.3, 98.3),
        (98.3, 99.5, 98.2, 99.4),
    ]
    draw_candles(ax, ohlc, highlight={4: "#ffeb3b", 6: "#a5d6a7"},
                 labels={4: "DIP", 6: "BUY", 7: "wick!"})
    ax.axhline(95.5, color="#c62828", ls="--", lw=1.5, label="Naive SL = dipLow (stopped out)")
    ax.axhline(95.0, color="#2e7d32", ls="--", lw=1.5, label="SL with ATR×0.3 buffer (survives)")
    ax.axhline(97.2, color="#1565c0", ls=":", lw=1, label="Entry")
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title("Wicks below dip low cause premature stops — buffer them with ATR",
                 fontsize=11)

    ax2 = fig.add_axes([0.06, 0.05, 0.88, 0.32])
    ax2.axis("off")
    code = (
        "slBuf  = input.float(0.3, \"SL ATR buffer\")\n"
        "slPrice = lockedDipLow - atr * slBuf"
    )
    ax2.text(0.0, 0.95, "Pine snippet", fontsize=12, fontweight="bold", color="#1565c0")
    for i, line in enumerate(code.split("\n")):
        ax2.text(0.02, 0.80 - i * 0.10, line, family="monospace",
                 fontsize=10, color="#263238")
    ax2.text(0.0, 0.45, "Trade-off",
             fontsize=12, fontweight="bold", color="#1565c0")
    ax2.text(0.02, 0.32,
             "• Wider SL → smaller position size for the same risk amount.\n"
             "• Reduces stop-hunts; usually improves win rate by 5–15%.\n"
             "• Combine with a multi-TP exit (Improvement #5) to keep RR healthy.",
             fontsize=10)
    pdf.savefig(fig)
    plt.close(fig)


def page_improvement_quality(pdf):
    fig = plt.figure(figsize=(8.5, 11))
    fig.suptitle("Improvement #3 — Dip Quality Score (0–100)",
                 fontsize=16, fontweight="bold", y=0.96)

    ax = fig.add_axes([0.10, 0.55, 0.80, 0.32])
    components = ["RSI depth", "Range/ATR", "Volume", "Below pivot", "S/R proximity"]
    weights = [25, 20, 20, 20, 15]
    colors = ["#1565c0", "#2e7d32", "#ef6c00", "#6a1b9a", "#00838f"]
    ax.barh(components, weights, color=colors)
    for i, w in enumerate(weights):
        ax.text(w + 1, i, f"{w} pts", va="center", fontsize=10)
    ax.set_xlim(0, 35)
    ax.set_xlabel("Weight contribution")
    ax.set_title("Composite score replaces hard binary thresholds", fontsize=11)
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.3)

    ax2 = fig.add_axes([0.06, 0.05, 0.88, 0.40])
    ax2.axis("off")
    code = (
        "score  = w1*norm(effOS - rsi)        +\n"
        "         w2*norm(range/atr)          +\n"
        "         w3*norm(volume/volAvg)      +\n"
        "         w4*norm(distBelowPivot)     +\n"
        "         w5*srProximityFactor\n"
        "isDipRed := isRed and score >= scoreThreshold"
    )
    ax2.text(0.0, 0.97, "Pine sketch", fontsize=12, fontweight="bold", color="#1565c0")
    for i, line in enumerate(code.split("\n")):
        ax2.text(0.02, 0.85 - i * 0.06, line, family="monospace",
                 fontsize=10, color="#263238")
    ax2.text(0.0, 0.40,
             "Why this is better than hard thresholds:",
             fontsize=12, fontweight="bold", color="#1565c0")
    ax2.text(0.02, 0.30,
             "• One knob (scoreThreshold) tunes overall sensitivity.\n"
             "• Setups can be ranked, sorted, and filtered.\n"
             "• Color the on-chart dip diamond by score → instant visual quality gauge.\n"
             "• Easier to backtest: log score vs outcome, find the sweet spot.",
             fontsize=10)
    pdf.savefig(fig)
    plt.close(fig)


def page_improvement_mtf_tp(pdf):
    fig = plt.figure(figsize=(8.5, 11))
    fig.suptitle("Improvements #4 & #5 — MTF Confluence + Multi-TP Exit",
                 fontsize=15, fontweight="bold", y=0.96)

    # MTF confluence chart
    ax = fig.add_axes([0.08, 0.55, 0.84, 0.32])
    np.random.seed(3)
    n = 30
    htf = np.linspace(60, 35, n) + np.random.randn(n) * 2
    ltf = np.linspace(55, 40, n) + np.random.randn(n) * 4
    ax.plot(range(n), htf, color="#1565c0", lw=2, label="HTF (1H) RSI")
    ax.plot(range(n), ltf, color="#ff9800", lw=2, label="LTF (5m) RSI")
    ax.axhline(50, color="grey", ls="--")
    ax.axhline(30, color="red", ls=":")
    ax.axhspan(0, 30, color="red", alpha=0.07)
    ax.fill_between(range(n), 0, 100, where=(htf < 50) & (ltf < 30),
                    color="green", alpha=0.15, label="Confluence zone")
    ax.set_ylim(20, 70)
    ax.legend(loc="upper right", fontsize=9)
    ax.set_title("Trade only when LTF oversold AND HTF still pulling back",
                 fontsize=11)
    ax.set_ylabel("RSI")
    ax.grid(alpha=0.3)

    # multi-TP visualisation
    ax2 = fig.add_axes([0.10, 0.10, 0.80, 0.35])
    levels = ["Entry", "TP1 (1R) close 50%", "TP2 (2R) close 30%", "Trail (chandelier) 20%"]
    prices = [100, 102, 104, 106.5]
    colors = ["#1565c0", "#43a047", "#2e7d32", "#1b5e20"]
    ax2.barh(levels, [p - 99 for p in prices], color=colors, left=99)
    for i, (lab, p) in enumerate(zip(levels, prices)):
        ax2.text(p + 0.05, i, f"{p}", va="center", fontsize=10, fontweight="bold")
    ax2.set_xlim(99, 108)
    ax2.set_xlabel("Price")
    ax2.invert_yaxis()
    ax2.set_title("Tiered exit: lock in profit early, let runners run", fontsize=11)
    ax2.grid(axis="x", alpha=0.3)
    pdf.savefig(fig)
    plt.close(fig)


def page_summary(pdf):
    text_page(pdf, "Summary & Recommended Order", [
        ("h2", "Core logic recap"),
        ("p", "Detect an oversold red 'flush' candle that prints a fresh low with adequate range and "
              "volume; confirm reversal with N green candles within a max-wait window; then fire a BUY "
              "with SL at the dip's low and TP at a configurable risk-reward multiple."),
        ("h2", "Strengths"),
        ("bullet", "Asset-aware presets remove most parameter tweaking."),
        ("bullet", "State machine prevents stale signals (timeout + new-low abort)."),
        ("bullet", "Auto volume-filter bypass keeps it usable on FX brokers without tick volume."),
        ("h2", "Top weaknesses to fix"),
        ("bullet", "No trend context — fires longs in clear downtrends."),
        ("bullet", "SL exactly at dip low → wick stop-outs are common."),
        ("bullet", "Binary thresholds can't rank setup quality."),
        ("h2", "Recommended improvement order"),
        ("bullet", "1) Add HTF trend filter (biggest accuracy gain, 1 line)."),
        ("bullet", "2) Add ATR buffer to SL (eliminates wick stops)."),
        ("bullet", "3) Convert to strategy() and backtest each preset."),
        ("bullet", "4) Replace hard rules with a 0-100 dip quality score."),
        ("bullet", "5) Add multi-TF RSI confluence + multi-TP exits."),
        ("bullet", "6) Mirror logic for SHORT (Rally Green + 2 Red)."),
        ("h2", "End"),
        ("p", "This document is a visual reference — the indicator's source remains the source of "
              "truth. Backtest every change before going live."),
    ])


# ============================ MAIN =============================

def main():
    plt.rcParams.update({"font.family": "DejaVu Sans"})
    with PdfPages(PDF_PATH) as pdf:
        page_cover(pdf)
        page_overview(pdf)
        page_preset(pdf)
        page_dip_detection(pdf)
        page_state_machine(pdf)
        page_signal_sltp(pdf)
        page_improvement_htf(pdf)
        page_improvement_sl(pdf)
        page_improvement_quality(pdf)
        page_improvement_mtf_tp(pdf)
        page_summary(pdf)
    print(f"PDF written to {PDF_PATH}")


if __name__ == "__main__":
    main()
