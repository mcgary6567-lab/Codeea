"""Generate the customer Quick Start guide + one-page cheat sheet as PDFs.

Pure reportlab (no cryptography dependency). Run:
    python make_user_guide.py
Outputs ../Trevolto_QuickStart.pdf and ../Trevolto_CheatSheet.pdf

This script is the single source of truth for both PDFs — edit the content here
and re-run, rather than hand-editing the binaries.
"""

from __future__ import annotations

import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Image, ListFlowable, ListItem, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle,
)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "Trevolto_QuickStart.pdf")
LOGO = os.path.join(HERE, "logo.png")

ACCENT = colors.HexColor("#e8821e")
DARK = colors.HexColor("#1d2128")
GREY = colors.HexColor("#5b626b")
LINE = colors.HexColor("#d7dbe0")
PANEL = colors.HexColor("#f4f6f8")
GREEN = colors.HexColor("#2e8b40")
RED = colors.HexColor("#c1392b")

SUPPORT = "support@trevolto.com"
SITE = "trevolto.com"

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Title"], fontName="Helvetica-Bold",
                    fontSize=22, textColor=DARK, spaceAfter=2, leading=25)
SUB = ParagraphStyle("SUB", fontName="Helvetica", fontSize=11, textColor=ACCENT,
                     spaceAfter=2)
H2 = ParagraphStyle("H2", fontName="Helvetica-Bold", fontSize=13.5, textColor=ACCENT,
                    spaceBefore=12, spaceAfter=5, leading=16)
BODY = ParagraphStyle("BODY", fontName="Helvetica", fontSize=9.7, textColor=DARK,
                      leading=14, spaceAfter=5)
SMALL = ParagraphStyle("SMALL", fontName="Helvetica", fontSize=8.4, textColor=GREY,
                       leading=11)
BULLET = ParagraphStyle("BULLET", parent=BODY, leftIndent=4, spaceAfter=2.5)
CELL = ParagraphStyle("CELL", fontName="Helvetica", fontSize=8.6, textColor=DARK, leading=11)
CELLB = ParagraphStyle("CELLB", parent=CELL, fontName="Helvetica-Bold")


def bullets(items, style=BULLET):
    return ListFlowable(
        [ListItem(Paragraph(t, style), value="square", leftIndent=12,
                  bulletColor=ACCENT) for t in items],
        bulletType="bullet", start="square",
    )


def steps(items):
    return ListFlowable(
        [ListItem(Paragraph(t, BODY), leftIndent=14) for t in items],
        bulletType="1", bulletFormat="%s.", bulletFontName="Helvetica-Bold",
        bulletColor=ACCENT,
    )


def table(rows, widths):
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.6),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PANEL]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(GREY)
    canvas.drawString(18 * mm, 9 * mm, f"Support: {SUPPORT}   |   Website: {SITE}")
    canvas.setFillColor(ACCENT)
    canvas.drawRightString(A4[0] - 18 * mm, 9 * mm,
                           f"Trevolto  -  page {canvas.getPageNumber()}")
    canvas.restoreState()


def build():
    doc = SimpleDocTemplate(
        OUT, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=20 * mm,
        title="Trevolto - Quick Start Guide",
        author="Trevolto",
    )
    s = []

    # --- Header ---
    head = [[
        Image(LOGO, width=14 * mm, height=14 * mm * 149 / 113) if os.path.exists(LOGO) else "",
        [Paragraph("Trevolto", H1),
         Paragraph("Quick Start Guide for Customers", SUB),
         Paragraph("Auto-trade crypto - built-in strategy or your signals. Secure, simple, yours.", SMALL)],
    ]]
    ht = Table(head, colWidths=[18 * mm, None], hAlign="LEFT")
    ht.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("LEFTPADDING", (0, 0), (0, 0), 0),
                            ("LEFTPADDING", (1, 0), (1, 0), 6)]))
    s.append(ht)
    s.append(Spacer(1, 4))
    s.append(HRFlowable(width="100%", thickness=1.2, color=ACCENT, spaceAfter=8))

    s.append(Paragraph(
        "The Trevolto runs on your Windows PC and trades crypto automatically - "
        "from its built-in EMA + RSI strategy, from your TradingView signals, or manually. It "
        "includes one-click strategy presets, a backtester, a live chart, risk guardrails, "
        "analytics, Telegram alerts, and PIN-encrypted storage for your API keys. Works with "
        "<b>Binance, Binance.US, Bybit, OKX, KuCoin, Bitget, Kraken and Coinbase</b>.", BODY))

    s.append(Paragraph("Get started in 5 steps", H2))
    s.append(steps([
        "Open the app and create a <b>PIN</b> (it encrypts your saved keys).",
        "Create an exchange <b>API key</b> with trading enabled (see page 2).",
        "Paste your <b>API Key + Secret</b>, pick your exchange and <b>Spot/Futures</b>, then click "
        "<b>Connect</b> - keep <b>Safe Mode</b> on for your first run.",
        "Activate your licence: click <b>Start Free Trial</b> (enter your email) or paste a "
        "<b>Licence token</b> under the <b>Connect &amp; License</b> tab.",
        "Pick a strategy preset (<b>Balanced</b>), watch it in Safe Mode, then turn Safe Mode off "
        "and trade a <b>small size</b>.",
    ]))
    s.append(Paragraph(
        "The setup strip under the top bar lights up as you go: "
        "(1) Connect -&gt; (2) Activate license -&gt; (3) Start trading.", BODY))

    s.append(Paragraph("What the screen shows", H2))
    s.append(bullets([
        "<b>Top bar:</b> connection status, a mode badge (LIVE / TESTNET / SAFE / READ-ONLY), "
        "exchange, balance, live P&amp;L, and the gear for Settings.",
        "<b>Status strip:</b> the setup checklist plus live chips - Strategy / Webhook / Cloud / Paused.",
        "<b>Left:</b> API Settings, your public IP (for whitelisting), the Chart / Backtest / "
        "Analytics buttons, Open Positions, and BUY / SELL (with a live order preview).",
        "<b>Right:</b> Trade Settings (5 tabs) and the live Trade Log.",
    ]))

    # ---------------- Page 2 ----------------
    s.append(Paragraph("1. Create your exchange API key", H2))
    s.append(Paragraph(
        "On your exchange go to <b>API Management -&gt; Create API</b>, then set the "
        "permissions below. This is the most important step - most connection problems "
        "come from a missing permission or IP whitelist.", BODY))
    s.append(table([
        [Paragraph("Permission", CELLB), Paragraph("Spot", CELLB),
         Paragraph("Futures", CELLB), Paragraph("Notes", CELLB)],
        [Paragraph("Enable Reading", CELL), Paragraph("Yes", CELL), Paragraph("Yes", CELL),
         Paragraph("Balance &amp; positions", CELL)],
        [Paragraph("Spot &amp; Margin Trading", CELL), Paragraph("Yes", CELL),
         Paragraph("-", CELL), Paragraph("Place spot orders", CELL)],
        [Paragraph("Futures / Derivatives", CELL), Paragraph("-", CELL), Paragraph("Yes", CELL),
         Paragraph("Place futures orders", CELL)],
        [Paragraph("IP whitelist", CELL), Paragraph("Yes", CELL), Paragraph("Yes", CELL),
         Paragraph("Required once trading is on", CELL)],
    ], [46 * mm, 16 * mm, 18 * mm, None]))
    s.append(Spacer(1, 6))
    s.append(bullets([
        "<b>IP whitelist:</b> click <b>Show my IP -&gt; Copy</b> and paste it into your exchange's "
        "trusted-IPs box. The app warns you if your IP changes.",
        "<b>Passphrase:</b> OKX, KuCoin and Bitget give a third secret - paste it into the app's "
        "Passphrase field. Binance and Bybit do not use one.",
        "<b>United States:</b> binance.com is blocked - choose <b>Binance.US</b> "
        "(or Bybit / OKX / KuCoin / Bitget / Kraken / Coinbase).",
    ]))

    # ---------------- Page 2 cont ----------------
    s.append(Paragraph("2. Connect (start in Safe Mode)", H2))
    s.append(steps([
        "Select your <b>Exchange</b> and <b>Market</b> (Spot is the safer default).",
        "Paste <b>API Key</b>, <b>Secret</b> (and <b>Passphrase</b> if shown).",
        "Leave <b>Safe Mode</b> ON for the first run - real prices &amp; balance, but fills are "
        "simulated, so you can verify everything risk-free.",
        "Click <b>Connect</b>. The status light turns green, the mode badge shows your mode, and "
        "your balance appears.",
        "Happy? Turn Safe Mode off (you'll confirm once) and trade small. (Prefer a paper exchange? "
        "Tick <b>Use exchange testnet</b> and connect with testnet keys.)",
    ]))

    s.append(Paragraph("3. The settings tabs", H2))
    s.append(bullets([
        "<b>Execution:</b> Trade Size (coin or USDT $), Sizing mode (Fixed lot / Fixed $ / Risk %), "
        "Auto-place SL/TP with TP1/TP2 scale-out, order type, a <b>Slippage guard</b>, "
        "round-up-to-minimum, and (futures) leverage &amp; margin with a high-leverage warning. "
        "Hover any field for a tooltip.",
        "<b>Modes &amp; Risk</b> (scrollable): Manual trading, Safe Mode, testnet, Pause new entries, "
        "Read-only, and the full <b>Guardrails</b> set - grouped into Position limits, Daily limits, "
        "Timing and Risk halts - plus trailing stop, move-to-breakeven, Run on startup and Minimize to tray.",
        "<b>Strategy:</b> the built-in EMA + RSI strategy with one-click presets and an Advanced section (page 3).",
        "<b>Connect &amp; License:</b> the TradingView webhook + Strategy filter (acts only on your "
        "indicator), a Test Signal button, your Licence token, and Start Free Trial / Get License.",
        "<b>Alerts:</b> Sound, Desktop and Telegram notifications (with Test buttons), an "
        "important-events-only filter, and an end-of-day P&amp;L summary.",
    ]))
    s.append(Paragraph(
        "<b>Saving:</b> most fields apply live; click <b>Save</b> to keep them across restarts. "
        "An \"unsaved changes\" marker appears when something hasn't been saved yet.", BODY))

    s.append(Paragraph("4. The built-in strategy &amp; presets", H2))
    s.append(bullets([
        "On the <b>Strategy</b> tab the bot runs its own port of the indicator - an EMA20 + RSI-50 "
        "crossover that trades both <b>long and short</b> - directly on exchange candles. "
        "No TradingView account needed.",
        "<b>Presets - one click:</b> <b>Conservative</b> (fewer, stricter trades), <b>Balanced</b> "
        "(recommended), <b>Aggressive</b> (more trades, trails the runner). The active preset is "
        "highlighted; hand-edit any field and it switches to Custom.",
        "<b>Advanced</b> (optional): confirmation candles, body filter, Trend EMA filter, ATR stop, "
        "TP1/TP2 targets, Trailing x ATR, and an ADX trend-strength filter.",
        "<b>Symbols &amp; timeframe:</b> trade one or a comma-separated list of pairs on your chosen timeframe.",
        "The strategy only trades when <b>enabled + connected + licensed</b> - and it respects every "
        "guardrail and your sizing settings, exactly like signal trades.",
    ]))

    # ---------------- Page 3 ----------------
    s.append(Paragraph("5. Chart, Backtest &amp; Analytics", H2))
    s.append(Paragraph(
        "Three buttons on the main window open the analysis tools. They follow your live Strategy "
        "settings and pair list.", BODY))
    s.append(bullets([
        "<b>Chart:</b> candlesticks with EMAs, RSI, volume and every BUY / SELL / scale-out / exit "
        "marker, a live last-price line, plus entry, stop and TP lines.",
        "<b>Backtest:</b> replay the strategy on real history. Spot is long-only; <b>Futures runs "
        "long + short</b> (matching what you can actually trade). Includes an optimizer (parameter "
        "sweeps), a walk-forward test to guard against curve-fitting, a buy-&amp;-hold benchmark, an "
        "equity curve, and CSV export.",
        "<b>Analytics:</b> win rate, profit factor, per-symbol stats and an equity curve, with a "
        "date-range filter, Clear and Export CSV.",
    ]))

    s.append(Paragraph("6. Trading &amp; open positions", H2))
    s.append(bullets([
        "<b>Manual:</b> pick a Symbol, set the size, press <b>BUY</b> or <b>SELL</b>. The preview "
        "above the buttons shows what will be sent.",
        "<b>Automatic:</b> signals from the built-in strategy, your webhook or the cloud feed open "
        "the trade plus its TP1/TP2 brackets for you.",
        "<b>Open Positions:</b> Close Selected, <b>Close %</b> (bank part of a winner - 25/33/50/75%), "
        "Set SL/TP, or <b>PANIC: Close All</b> to flatten everything instantly.",
    ]))

    s.append(Paragraph("7. Settings (the gear)", H2))
    s.append(Paragraph("The gear in the top-right opens app preferences - the things you set once.", BODY))
    s.append(bullets([
        "<b>Security:</b> Change PIN, and <b>Lock now</b> (hide the app behind your PIN without quitting). "
        "<b>Forgot your PIN?</b> Ask support to authorise a reset, then click <b>Forgot PIN?</b> on the "
        "unlock screen, enter your email and set a new PIN - your licence is kept (you'll re-enter your "
        "exchange keys). Only one copy of the app runs at a time, so you can't accidentally double-trade.",
        "<b>Appearance:</b> Text size - Small / Normal / Large - for readability on any screen.",
        "<b>Data &amp; backup:</b> Backup / Restore settings, Open data folder, View log file (handy for "
        "support), and Clear history / log.",
        "<b>Startup &amp; updates:</b> run on Windows startup, minimize to tray, start minimized, "
        "auto-connect on launch, and check for updates.",
    ]))

    s.append(Paragraph("8. Fees &amp; sizing - read this", H2))
    s.append(bullets([
        "Spot fees are about <b>0.1% per fill (~0.2% round trip)</b>. Keep take-profits above ~0.25% "
        "so wins clear the fee.",
        "<b>More trades is not more profit</b> - frequent trading bleeds capital in fees. Quality over quantity.",
        "<b>Risk-based sizing</b> (recommended) sizes each trade from your Risk % and the stop distance, "
        "so position size scales with your balance.",
        "On Binance, holding <b>BNB</b> lowers fees by ~25%.",
    ]))

    # ---------------- Page 4 ----------------
    s.append(Paragraph("Troubleshooting", H2))
    s.append(table([
        [Paragraph("Message", CELLB), Paragraph("What it means &amp; the fix", CELLB)],
        [Paragraph("Insufficient balance", CELL),
         Paragraph("Your lot costs more than your balance (size is in coins, not $). Lower the Trade "
                   "Size, or use Risk % sizing.", CELL)],
        [Paragraph("Invalid API-key / -2015", CELL),
         Paragraph("Trading not enabled or IP not whitelisted. Enable Spot/Futures and add your IP "
                   "(Show my IP).", CELL)],
        [Paragraph("Futures rejected (-2015)", CELL),
         Paragraph("Your key/account isn't enabled for Futures, or the IP isn't whitelisted for it. "
                   "Enable Futures trading on the key and whitelist your IP.", CELL)],
        [Paragraph("Restricted location / 451", CELL),
         Paragraph("Exchange blocked in your region. US: use Binance.US. Else: another exchange or "
                   "turn off VPN.", CELL)],
        [Paragraph("Balance shows $0", CELL),
         Paragraph("Funds are in the other wallet. Move USDT to Spot (Market=Spot) or Futures "
                   "(Market=Futures).", CELL)],
        [Paragraph("does not have market symbol", CELL),
         Paragraph("Pair mismatch - pick the pair from the dropdown (the app lists your exchange's "
                   "pairs).", CELL)],
        [Paragraph("Telegram not arriving", CELL),
         Paragraph("Open Alerts, click Test. Message your bot once, check the token &amp; chat id. "
                   "The app falls back automatically behind strict firewalls.", CELL)],
    ], [44 * mm, None]))

    s.append(Paragraph("Running 24/7", H2))
    s.append(bullets([
        "<b>Run automatically when Windows starts</b> + <b>Minimize to tray</b> (Modes &amp; Risk, or "
        "the gear): the bot keeps trading in the background. Combine with Safe Mode off and a saved "
        "licence so it runs unattended.",
        "Leave the PC on (or use a small always-on PC / VPS) and keep your exchange IP whitelist updated.",
    ]))

    s.append(Paragraph("Security checklist", H2))
    s.append(bullets([
        "Your IP added to the key's whitelist.",
        "Tested in <b>Safe Mode</b> before going live.",
        "Trading a <b>small size</b> until you trust it.",
        "Keys live only in the app - encrypted behind your PIN.",
    ]))
    s.append(Spacer(1, 6))
    s.append(HRFlowable(width="100%", thickness=0.8, color=LINE, spaceAfter=6))
    s.append(Paragraph(
        f"Need help? Email <b>{SUPPORT}</b> or visit <b>{SITE}</b>. "
        "Trading places real orders with real money when Safe Mode is off - "
        "start small and never risk funds you can't afford to lose.", SMALL))

    doc.build(s, onFirstPage=_footer, onLaterPages=_footer)
    print(f"[make_user_guide] wrote {os.path.abspath(OUT)}")


# ---------------------------------------------------------------------------
# One-page cheat sheet (dense quick reference).
# ---------------------------------------------------------------------------
OUT_CHEAT = os.path.join(HERE, "..", "Trevolto_CheatSheet.pdf")

CH = ParagraphStyle("CH", fontName="Helvetica", fontSize=8.3, textColor=DARK, leading=11)
CH_H = ParagraphStyle("CH_H", fontName="Helvetica-Bold", fontSize=10.5, textColor=ACCENT,
                      spaceBefore=7, spaceAfter=3, leading=12)


def _cbul(items):
    return ListFlowable(
        [ListItem(Paragraph(t, CH), value="square", leftIndent=10, bulletColor=ACCENT)
         for t in items], bulletType="bullet", start="square")


def _cstep(items):
    return ListFlowable(
        [ListItem(Paragraph(t, CH), leftIndent=12) for t in items],
        bulletType="1", bulletFormat="%s.", bulletFontName="Helvetica-Bold", bulletColor=ACCENT)


def build_cheatsheet():
    doc = SimpleDocTemplate(
        OUT_CHEAT, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=12 * mm, bottomMargin=18 * mm,
        title="Trevolto - Cheat Sheet", author="Trevolto")
    s = []
    head = [[
        Image(LOGO, width=12 * mm, height=12 * mm * 149 / 113) if os.path.exists(LOGO) else "",
        [Paragraph("Trevolto", ParagraphStyle(
            "ct", fontName="Helvetica-Bold", fontSize=17, textColor=DARK, leading=19)),
         Paragraph("One-Page Cheat Sheet", SUB)],
    ]]
    ht = Table(head, colWidths=[16 * mm, None], hAlign="LEFT")
    ht.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("LEFTPADDING", (0, 0), (0, 0), 0)]))
    s.append(ht)
    s.append(HRFlowable(width="100%", thickness=1.1, color=ACCENT, spaceBefore=3, spaceAfter=2))

    left = [
        Paragraph("Connect in 5 steps", CH_H),
        _cstep([
            "Open app, set a <b>PIN</b>.",
            "Create an exchange <b>API key</b> (trading on).",
            "Paste Key + Secret, pick exchange + <b>Spot/Futures</b>.",
            "Connect with <b>Safe Mode</b> ON.",
            "Activate licence (Free Trial or token), pick a preset, go.",
        ]),
        Paragraph("API key - get this right", CH_H),
        _cbul([
            "Enable <b>Reading</b> + <b>Spot</b> (or Futures).",
            "<b>Whitelist your IP</b>: Show my IP -&gt; Copy -&gt; paste on exchange.",
            "<b>Passphrase</b> needed: OKX, KuCoin, Bitget.",
            "<b>USA</b>: use <b>Binance.US</b> (not binance.com).",
        ]),
        Paragraph("Strategy &amp; presets", CH_H),
        _cbul([
            "EMA20 + RSI-50 crossover - trades <b>long + short</b>.",
            "Presets: <b>Conservative / Balanced / Aggressive</b> (one click).",
            "Advanced: trend EMA, ATR stop, TP1/TP2, trailing, ADX.",
            "Set Symbols + Timeframe; trades only when licensed + connected.",
        ]),
        Paragraph("Connect &amp; License", CH_H),
        _cbul([
            "Connect &amp; License tab -&gt; <b>Start Free Trial</b> or paste token.",
            "Optional webhook: Strategy filter = Trevolto.",
            "No TradingView / ngrok needed.",
        ]),
    ]
    right = [
        Paragraph("Chart / Backtest / Analytics", CH_H),
        _cbul([
            "<b>Chart</b>: candles, EMAs, RSI, volume + BUY/SELL markers + last price.",
            "<b>Backtest</b>: Spot = long-only, Futures = long + short; "
            "optimizer + walk-forward; vs buy &amp; hold.",
            "<b>Analytics</b>: win rate, profit factor, equity curve, CSV.",
        ]),
        Paragraph("Key settings", CH_H),
        _cbul([
            "<b>Trade Size</b> in coin or USDT ($); Sizing: Fixed / Fixed $ / Risk %.",
            "<b>Auto TP1/TP2</b> scale-out; live order preview.",
            "<b>Guardrails</b>: daily loss/profit, max positions, exposure cap, "
            "cooldown, dedupe, loss-streak, drawdown, hours.",
            "Pause new entries, trailing stop, slippage guard, &gt;10x warns.",
            "<b>Close %</b> banks a winner; <b>PANIC: Close All</b> flattens.",
            "Alerts: Sound / Desktop / Telegram + daily P&amp;L.",
        ]),
        Paragraph("Gear, test &amp; 24/7", CH_H),
        _cbul([
            "Gear: Change PIN, Lock, text size, open data folder / log.",
            "<b>Forgot PIN?</b> support authorises -&gt; Forgot PIN? on unlock -&gt; set new PIN (licence kept).",
            "Only one copy runs at a time (no double-trading).",
            "<b>Test Signal</b>: fake BUY through the real pipeline (sim in Safe Mode).",
            "Run on startup + Minimize to tray = unattended 24/7.",
        ]),
        Paragraph("Fees &amp; quick fixes", CH_H),
        _cbul([
            "~<b>0.1% per fill</b> (~0.2% round trip); keep <b>TP &gt; 0.25%</b>.",
            "<b>Insufficient balance</b> -&gt; lower size / use Risk %.",
            "<b>-2015 / invalid key</b> -&gt; enable trading + whitelist IP.",
            "<b>451</b> -&gt; Binance.US / other / no VPN.  <b>$0</b> -&gt; wrong wallet.",
            "<b>no market symbol</b> -&gt; pick the pair from the dropdown.",
        ]),
    ]
    cols = Table([[left, right]], colWidths=[(A4[0] - 28 * mm) / 2] * 2, hAlign="LEFT")
    cols.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (0, 0), 0),
                              ("LEFTPADDING", (1, 0), (1, 0), 8)]))
    s.append(cols)
    s.append(Spacer(1, 6))
    s.append(HRFlowable(width="100%", thickness=0.8, color=LINE, spaceAfter=4))
    s.append(Paragraph(
        "<b>Safety:</b> IP whitelisted - test in Safe Mode - trade small. "
        "Real money when Safe Mode is off.", SMALL))
    doc.build(s, onFirstPage=_footer, onLaterPages=_footer)
    print(f"[make_user_guide] wrote {os.path.abspath(OUT_CHEAT)}")


if __name__ == "__main__":
    build()
    build_cheatsheet()
