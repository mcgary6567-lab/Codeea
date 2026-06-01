# 🔥 Prometheus AI Crypto Bot

A Windows desktop app that executes crypto trades from **TradingView indicator
alerts** (or manually), with auto take-profits, risk guardrails, analytics, and
an encrypted, PIN-protected vault for your API keys.

**Signal → TradingView webhook → bot → exchange order.**

![architecture](trading_bot/architecture.png)
![gui](trading_bot/gui_mockup.png)

---

## What's in this repo

| Path | What it is |
|------|------------|
| **`trading_bot/`** | The Windows app (Python/Tkinter + ccxt). Full docs: [`trading_bot/README.md`](trading_bot/README.md) · diagrams: [`trading_bot/ARCHITECTURE.md`](trading_bot/ARCHITECTURE.md) |
| **`Prometheus_AI_Crypto_Bot.pine`** | The current indicator — crypto, **Entry + TP1 + TP2** (no stop-loss), `Webhook JSON` alerts tagged `Prometheus` |
| `Gold_Scalpers.pine` | Earlier Gold Scalpers indicator with the crypto patch applied |
| `Gold_Scalpers_crypto_patch.md` | Find/replace edits to run Gold Scalpers on crypto |
| `Dip2Green_PRO.pine` + `dip_indicator_docs/` | The original Dip2Green indicator + PDF guide |

## Quick start

1. **Get the app:** download `PrometheusAICryptoBot.exe` from the repo's
   **Actions ▸ Build Windows EXE** artifact, or build locally with
   `trading_bot/build_exe.bat`. (A Windows `.exe` can only be built on Windows.)
2. **Run it**, create a PIN, **connect** your exchange (start in **Safe Mode**).
3. **Add `Prometheus_AI_Crypto_Bot.pine`** to a crypto chart in TradingView,
   alert **Format = "Webhook JSON"**, point it at `http://YOUR_HOST:8723/`
   (use `ngrok http 8723` to expose the port).
4. In the app set **Strategy filter = `Prometheus`** so it only acts on this
   indicator. Verify in Safe Mode, then go live with small size.

## Features (app)

Multi-exchange (**Binance · Bybit · OKX · KuCoin · Bitget** via ccxt) · manual
BUY/SELL · webhook auto-execution · auto **TP1/TP2 scale-out** · market/limit
orders · leverage & margin mode · risk-based sizing · **guardrails** (daily-loss
auto-halt, max positions, cooldown, duplicate-alert dedupe) · Close / **PANIC**
flatten · real-time prices & PnL · **analytics** (win rate, realized PnL, equity
curve) · sound + **Telegram** alerts · encrypted keys + PIN + Read-only & Safe
Mode.

---

## Recent updates

**Rebrand & UI**
- Renamed to **Prometheus AI Crypto Bot**; modern **dark / sleek** theme.
- Branded header **logo** + window/taskbar icon (auto-fetched at build time).
- **Title-case** exchange names (Binance, Bybit, OKX, KuCoin, Bitget).
- Smaller **BUY/SELL** buttons with 5px rounded corners.

**New crypto indicator** (`Prometheus_AI_Crypto_Bot.pine`)
- Crypto-only (BTC/ETH/auto presets), 24/7 — no forex session filter.
- **Stop-loss removed** — outputs **Entry + TP1 + TP2** only.
- Crypto-price-friendly: per-symbol precision (no truncation), **% targets**
  instead of forex pips.

**Indicator ↔ bot wiring**
- **Strategy filter** — the bot acts only on alerts whose `comment` tag matches
  (default `Prometheus`); others are ignored.
- Handles post-entry **lifecycle events** (`tp1_hit`/`tp2_hit`) incl. optional
  **move-stop-to-breakeven on TP1**.
- Verified end-to-end: a Prometheus entry produces an entry fill + TP1 + TP2
  (50/50 split) and **no SL**.

**Build & distribution**
- Windows **exe build** via GitHub Actions and `build_exe.bat`.
- **SmartScreen/AV-friendlier**: embedded publisher metadata, no UPX packing,
  and **optional auto code-signing** (add `WIN_CERT_BASE64` + `WIN_CERT_PASSWORD`
  secrets).
- **Size-optimised** (~22–26MB): dropped ccxt's WebSocket/async layer (price
  feed uses REST polling) + stdlib excludes.

**Core trading engine** (earlier)
- ccxt multi-exchange execution, auto SL/TP brackets, stop-based risk sizing,
  guardrails, analytics + SQLite history, notifications, limit/leverage/margin.

---

## ⚠️ Disclaimer

This software places **real trades with real money** when Safe Mode is off. It is
provided **as-is, with no warranty**. Crypto trading is high-risk — start in
**Safe Mode**, disable withdrawals on your API keys, use small size, and never
risk funds you can't afford to lose. You are responsible for your own trades.
