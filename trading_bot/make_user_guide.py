"""Generate the customer Quick Start guide as a multi-page PDF.

Pure reportlab (no cryptography dependency). Run:
    python make_user_guide.py
Outputs ../PrometheusAI_QuickStart.pdf
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
OUT = os.path.join(HERE, "..", "PrometheusAI_QuickStart.pdf")
LOGO = os.path.join(HERE, "logo.png")

ACCENT = colors.HexColor("#e8821e")
DARK = colors.HexColor("#1d2128")
GREY = colors.HexColor("#5b626b")
LINE = colors.HexColor("#d7dbe0")
PANEL = colors.HexColor("#f4f6f8")
GREEN = colors.HexColor("#2e8b40")
RED = colors.HexColor("#c1392b")

SUPPORT = "support@prometheusai.tech"
SITE = "prometheusai.tech"

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
                           f"Prometheus AI Crypto Bot  -  page {canvas.getPageNumber()}")
    canvas.restoreState()


def build():
    doc = SimpleDocTemplate(
        OUT, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=20 * mm,
        title="Prometheus AI Crypto Bot - Quick Start Guide",
        author="Prometheus AI",
    )
    s = []

    # --- Header ---
    head = [[
        Image(LOGO, width=14 * mm, height=14 * mm * 149 / 113) if os.path.exists(LOGO) else "",
        [Paragraph("Prometheus AI Crypto Bot", H1),
         Paragraph("Quick Start Guide for Customers", SUB),
         Paragraph("Auto-trade crypto from TradingView signals - secure, simple, yours.", SMALL)],
    ]]
    ht = Table(head, colWidths=[18 * mm, None], hAlign="LEFT")
    ht.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("LEFTPADDING", (0, 0), (0, 0), 0),
                            ("LEFTPADDING", (1, 0), (1, 0), 6)]))
    s.append(ht)
    s.append(Spacer(1, 4))
    s.append(HRFlowable(width="100%", thickness=1.2, color=ACCENT, spaceAfter=8))

    s.append(Paragraph(
        "The Prometheus AI Crypto Bot runs on your Windows PC and places crypto trades "
        "automatically from your indicator's signals (or manually), with automatic "
        "take-profits, risk guardrails, live analytics, and bank-grade encrypted storage "
        "for your API keys. Works with <b>Binance, Binance.US, Bybit, OKX, KuCoin and "
        "Bitget</b>.", BODY))

    s.append(Paragraph("Get started in 5 steps", H2))
    s.append(steps([
        "Open the app and create a <b>PIN</b> (it encrypts your saved keys).",
        "Create an exchange <b>API key</b> with trading enabled (see page 2).",
        "Paste your <b>API Key + Secret</b>, pick your exchange and <b>Spot</b>, then click "
        "<b>Connect</b> - keep <b>Safe Mode</b> on for your first run.",
        "(Selling model) paste your <b>Licence token</b> under <b>Webhook -&gt; Cloud signals</b> "
        "to receive signals automatically - no setup, no ngrok.",
        "Watch it work in Safe Mode, then turn Safe Mode off and trade a <b>small size</b>.",
    ]))

    s.append(Paragraph("What the screen shows", H2))
    s.append(bullets([
        "<b>Top bar:</b> connection status, exchange, balance and live P&amp;L.",
        "<b>Left:</b> API Settings, your public IP (for whitelisting), Open Positions, "
        "and BUY / SELL.",
        "<b>Right:</b> Trade Settings (4 tabs) and the live Trade Log.",
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
        [Paragraph("<b>Withdrawals</b>", CELL),
         Paragraph("<font color='#c1392b'><b>OFF</b></font>", CELL),
         Paragraph("<font color='#c1392b'><b>OFF</b></font>", CELL),
         Paragraph("Never needed - keep it disabled", CELL)],
        [Paragraph("IP whitelist", CELL), Paragraph("Yes", CELL), Paragraph("Yes", CELL),
         Paragraph("Required once trading is on", CELL)],
    ], [46 * mm, 16 * mm, 18 * mm, None]))
    s.append(Spacer(1, 6))
    s.append(bullets([
        "<b>Disable Withdrawals - always.</b> The bot never withdraws; a leaked key with "
        "withdrawals could drain funds.",
        "<b>IP whitelist:</b> in the app click <b>Show my IP -&gt; Copy</b>, paste it into "
        "your exchange's \"trusted IPs\" box. (Tip: if your home IP changes, the app warns "
        "you to update it.)",
        "<b>Passphrase:</b> OKX, KuCoin and Bitget give you a third secret - paste it into "
        "the app's Passphrase field. Binance and Bybit do not use one.",
        "<b>United States:</b> binance.com is blocked - choose <b>Binance.US</b> in the "
        "exchange list (or Bybit / OKX / KuCoin / Bitget).",
    ]))

    # ---------------- Page 3 ----------------
    s.append(Paragraph("2. Connect (start in Safe Mode)", H2))
    s.append(steps([
        "Select your <b>Exchange</b> and <b>Market</b> (Spot is the safer default).",
        "Paste <b>API Key</b>, <b>Secret</b> (and <b>Passphrase</b> if shown).",
        "Leave <b>Safe Mode</b> ON for the first run - real prices &amp; balance, but fills "
        "are simulated, so you can verify everything risk-free.",
        "Click <b>Connect</b>. The status light turns green and your balance appears.",
        "Happy? Turn Safe Mode off (you'll confirm once) and trade small.",
    ]))

    s.append(Paragraph("3. The settings tabs", H2))
    s.append(bullets([
        "<b>Execution:</b> Trade Size (in the base coin, e.g. 0.006 BTC), Sizing mode "
        "(Fixed lot or Risk % of balance), Auto-place SL/TP with TP1/TP2 scale-out, order "
        "type, and (futures) leverage &amp; margin.",
        "<b>Modes &amp; Risk:</b> Manual trading, <b>Safe Mode</b>, Read-only, and "
        "<b>Guardrails</b> - daily loss/profit auto-halt, max open positions, per-symbol "
        "cooldown, duplicate-signal filter, trailing stop, move-to-breakeven.",
        "<b>Webhook:</b> the TradingView webhook + <b>Strategy filter</b> (acts only on your "
        "indicator), and <b>Cloud signals</b> - paste your Relay URL + Licence token.",
        "<b>Alerts:</b> Sound, Desktop and <b>Telegram</b> notifications (with Test buttons), "
        "an \"important events only\" filter, and an end-of-day <b>P&amp;L summary</b>.",
    ]))

    s.append(Paragraph("4. Trading &amp; analytics", H2))
    s.append(bullets([
        "<b>Manual:</b> pick a Symbol, set the size, press <b>BUY</b> or <b>SELL</b>.",
        "<b>Automatic:</b> signals arrive by webhook or cloud and the bot opens the trade "
        "plus its TP1/TP2 brackets for you.",
        "<b>Open Positions:</b> Close Selected, Set SL/TP, or <b>PANIC: Close All</b> to "
        "flatten everything instantly.",
        "<b>Analytics:</b> win rate, realized P&amp;L and an equity curve, with a date-range "
        "filter, Clear, and Export CSV for your records.",
    ]))

    # ---------------- Page 4 ----------------
    s.append(Paragraph("Fees &amp; sizing - read this", H2))
    s.append(bullets([
        "Spot fees are about <b>0.1% per fill (~0.2% per round trip)</b>. Keep your "
        "take-profit above ~0.25% so wins clear the fee.",
        "<b>More trades is not more profit</b> - frequent trading bleeds capital in fees. "
        "Quality over quantity.",
        "On a $500 balance, a 0.006 BTC lot (~$420) uses most of your funds, so you hold "
        "one position at a time. Lower the size to run more, or add capital.",
        "On Binance, holding <b>BNB</b> lowers fees by ~25%.",
    ]))

    s.append(Paragraph("Troubleshooting", H2))
    s.append(table([
        [Paragraph("Message", CELLB), Paragraph("What it means &amp; the fix", CELLB)],
        [Paragraph("Insufficient balance", CELL),
         Paragraph("Your lot costs more than your balance (size is in coins, not $). "
                   "Lower the Trade Size.", CELL)],
        [Paragraph("Invalid API-key / -2015", CELL),
         Paragraph("Trading not enabled or IP not whitelisted. Enable Spot/Futures and add "
                   "your IP (Show my IP).", CELL)],
        [Paragraph("Restricted location / 451", CELL),
         Paragraph("Exchange blocked in your region. US: use Binance.US. Else: another "
                   "exchange or turn off VPN.", CELL)],
        [Paragraph("Balance shows $0", CELL),
         Paragraph("Funds are in the other wallet. Move USDT to Spot (Market=Spot) or "
                   "Futures (Market=Futures).", CELL)],
        [Paragraph("does not have market symbol", CELL),
         Paragraph("Pair mismatch - use the exchange's pair (e.g. BTCUSDT, not BTCUSD).", CELL)],
    ], [44 * mm, None]))

    s.append(Paragraph("Security checklist", H2))
    s.append(bullets([
        "Withdrawals <b>disabled</b> on the API key.",
        "Your IP added to the key's whitelist.",
        "Tested in <b>Safe Mode</b> before going live.",
        "Trading a <b>small size</b> until you trust it.",
        "Keys live only in the app - encrypted behind your PIN.",
    ]))
    s.append(Spacer(1, 6))
    s.append(HRFlowable(width="100%", thickness=0.8, color=LINE, spaceAfter=6))
    s.append(Paragraph(
        f"Need help? Email <b>{SUPPORT}</b> or visit <b>{SITE}</b>. "
        "Trading places real orders with real money when Safe Mode is off - start small, "
        "keep withdrawals off, and never risk funds you can't afford to lose.", SMALL))

    doc.build(s, onFirstPage=_footer, onLaterPages=_footer)
    print(f"[make_user_guide] wrote {os.path.abspath(OUT)}")


# ---------------------------------------------------------------------------
# One-page cheat sheet (dense quick reference).
# ---------------------------------------------------------------------------
OUT_CHEAT = os.path.join(HERE, "..", "PrometheusAI_CheatSheet.pdf")

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
        title="Prometheus AI Crypto Bot - Cheat Sheet", author="Prometheus AI")
    s = []
    head = [[
        Image(LOGO, width=12 * mm, height=12 * mm * 149 / 113) if os.path.exists(LOGO) else "",
        [Paragraph("Prometheus AI Crypto Bot", ParagraphStyle(
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
            "Paste Key + Secret, pick exchange + <b>Spot</b>.",
            "Click <b>Connect</b> with <b>Safe Mode</b> ON.",
            "Verify, then Safe Mode off + <b>small size</b>.",
        ]),
        Paragraph("API key - get this right", CH_H),
        _cbul([
            "Enable <b>Reading</b> + <b>Spot</b> (or Futures).",
            "<b>Withdrawals OFF</b> - always.",
            "<b>Whitelist your IP</b>: app's <b>Show my IP</b> -&gt; Copy -&gt; paste on exchange.",
            "<b>Passphrase</b> needed: OKX, KuCoin, Bitget.",
            "<b>USA</b>: use <b>Binance.US</b> (not binance.com).",
        ]),
        Paragraph("Cloud signals (licence)", CH_H),
        _cbul([
            "Webhook tab -&gt; paste <b>Relay URL</b> + <b>Licence token</b>.",
            "Set <b>Strategy filter = Prometheus</b>.",
            "No TradingView / ngrok needed.",
        ]),
    ]
    right = [
        Paragraph("Key settings", CH_H),
        _cbul([
            "<b>Trade Size</b> is in coins (0.006 BTC ~ $420), not $.",
            "<b>Sizing</b>: Fixed lot or Risk % of balance.",
            "<b>Auto TP1/TP2</b> scale-out on signals.",
            "<b>Guardrails</b>: daily loss/profit halt, max positions, cooldown, dedupe.",
            "<b>PANIC: Close All</b> flattens everything.",
            "<b>Alerts</b>: Sound / Desktop / Telegram + daily P&amp;L.",
        ]),
        Paragraph("Fees rule of thumb", CH_H),
        _cbul([
            "~<b>0.1% per fill</b> (~0.2% round trip).",
            "Keep <b>TP &gt; 0.25%</b> to beat fees.",
            "Fewer, better trades &gt; many small ones.",
        ]),
        Paragraph("Quick fixes", CH_H),
        _cbul([
            "<b>Insufficient balance</b> -&gt; lower Trade Size.",
            "<b>-2015 / invalid key</b> -&gt; enable trading + whitelist IP.",
            "<b>451 restricted</b> -&gt; Binance.US / other / no VPN.",
            "<b>$0 balance</b> -&gt; funds in wrong wallet (Spot vs Futures).",
            "<b>no market symbol</b> -&gt; use BTCUSDT (not BTCUSD).",
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
        "<b>Safety:</b> withdrawals off - IP whitelisted - test in Safe Mode - trade small. "
        "Real money when Safe Mode is off.", SMALL))
    doc.build(s, onFirstPage=_footer, onLaterPages=_footer)
    print(f"[make_user_guide] wrote {os.path.abspath(OUT_CHEAT)}")


if __name__ == "__main__":
    build()
    build_cheatsheet()

