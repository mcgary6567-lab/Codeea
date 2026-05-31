# TradingView Trading Bot (Windows)

A standalone Python desktop app that connects to a crypto exchange and executes
trades **manually** (BUY/SELL buttons) or **automatically** from **TradingView
webhook alerts** — designed to be driven by the `Dip2Green_PRO.pine` indicator
in this repo.

Built with **Tkinter** (ships with Python, runs on every Windows version) plus
[`ccxt`](https://github.com/ccxt/ccxt) for exchange connectivity and
`cryptography` for encrypted, PIN-protected key storage.

![layout reference](../dip_indicator_docs)

---

## Features

| Area | What it does |
|------|--------------|
| **Status bar** | Live connection state (green/red), selected exchange, account balance, aggregate PnL |
| **Exchange panel** | Drop-down for **Binance, Bybit, OKX, KuCoin, Bitget**; API key/secret (+ passphrase for OKX/KuCoin/Bitget); Connect/Disconnect |
| **Lot control** | Fixed lot size **or** risk-based sizing (% of balance) |
| **Manual trading** | Big **BUY** / **SELL** buttons with a confirmation popup |
| **Account info** | Balance, PnL, and an Open Positions table (pair, side, size, entry, current, PnL, status) |
| **Trade log** | Timestamped, scrollable log of every signal/execution; **Export** to CSV and **Clear** |
| **Webhook** | Built-in HTTP receiver for TradingView alerts (auto-execution) |
| **Real-time data** | WebSocket price feed (ccxt.pro) with REST fallback; live PnL recompute, green/red coloring, **Refresh Now**, and connection-drop alerts |
| **Security** | Fernet-encrypted API keys, PIN/password gate, optional **Read-only** monitoring mode, and **Safe Mode** (simulate, no real orders) |

---

## Install & run

1. Install **Python 3.8+** from python.org (tick *"Add Python to PATH"*).
2. Double-click **`run.bat`** — it installs dependencies on first run and launches the app.

Or from a terminal:

```bat
cd trading_bot
pip install -r requirements.txt
python main.py
```

On first launch you'll be asked to **create a PIN**. It encrypts your saved
settings; there is no recovery if you forget it (by design).

> Without `ccxt` installed the app still launches in **simulation mode** so you
> can explore the UI; install requirements for real connectivity.

---

## Connecting an exchange

1. Create **API keys** on your exchange. Enable *trading* but **disable
   withdrawals**, and IP-whitelist your machine where possible.
2. Pick the exchange, paste key/secret (+ passphrase for OKX/KuCoin/Bitget).
3. Click **Connect**. The dot turns green and balance/positions start updating.

**Safe Mode** (Trade Settings) simulates fills instead of sending real orders —
keep it on until you've verified everything. **Read-only** blocks all orders
entirely (monitoring only).

---

## Auto-trading from TradingView (`Dip2Green_PRO`)

The flow:

```
Dip2Green alert  →  TradingView webhook  →  this bot's receiver  →  exchange order
```

1. In **Trade Settings**, optionally set a **Webhook passphrase**, then click
   **Start Webhook**. The bot listens on port **8723**.
2. Expose that port to TradingView. TradingView only POSTs to public URLs, so
   either:
   - run a tunnel (e.g. `ngrok http 8723`) and use the public URL, **or**
   - host on a VPS / forward the port on your router.
3. In TradingView, add an alert on the **Dip2Green PRO** indicator:
   - Condition: *Any alert() function call* (the indicator already emits JSON).
   - In the indicator settings, set **"Webhook passphrase (for trading bot)"**
     to the same value you put in the bot.
   - Webhook URL: `http://YOUR_PUBLIC_HOST:8723/`
4. The indicator sends payloads like:

   ```json
   {"action":"BUY","symbol":"BTCUSDT","passphrase":"yoursecret","entry":67500,"sl":67000,"tp1":68200,"tp2":69000}
   ```

   The bot reads `action` + `symbol`, sizes the order from your lot settings,
   and executes (or simulates, in Safe Mode).

> **Note:** TradingView webhooks require a **paid** TradingView plan.

---

## Manual webhook test

With the webhook running you can fire a test signal locally:

```bat
curl -X POST http://127.0.0.1:8723/ -H "Content-Type: application/json" ^
  -d "{\"action\":\"buy\",\"symbol\":\"BTC/USDT\",\"passphrase\":\"\"}"
```

---

## Real-time data workflow

Once connected, the bot keeps the account view live:

- **Positions (authoritative):** a background thread polls the exchange via
  ccxt every few seconds — `fetch_positions` (Binance Futures `positionRisk`,
  Bybit `/v5/position/list`, OKX `/account/positions`, etc.) for symbol, side,
  size, entry, current and unrealized PnL.
- **Prices (real-time):** `pricefeed.py` streams tickers for your open symbols
  over **WebSocket** (`ccxt.pro`, `watch_tickers`) when available, falling back
  to a fast **REST `fetch_tickers` poll** otherwise. Only held symbols are
  subscribed, so it stays within rate limits.
- **PnL** is recomputed locally on every tick — `(current − entry) × size`
  (reversed for shorts) — so the table and status bar move instantly without
  waiting for the next REST poll. Profit rows are green, losses red.
- **Manual symbol mark price:** the symbol in the manual-trade box is streamed
  too — its live **Mark** price shows next to it even when you hold no position
  in it, so you can see the price before clicking BUY/SELL. The feed subscribes
  to the union of held symbols and the manual symbol.
- **Trade log sync:** when a position disappears between refreshes it is logged
  as **Closed**.
- **Resilience:** keys are encrypted at rest; ticker requests use a keyless
  public client; repeated REST failures (dropped link / rate-limit storm)
  raise a one-time **connection alert** and amber status, auto-clearing when the
  feed recovers. A **Refresh Now** button forces an immediate update.

All exchange I/O runs off the UI thread (single worker thread + a price-feed
thread), and the GUI is updated from a thread-safe queue, so the window never
freezes.

> Real-time prices are only fetched in **live** connections. In **Safe Mode**
> the feed is skipped and fills/positions are simulated locally.

## Security notes

- Keys are stored **encrypted** (Fernet/AES) under a key derived from your PIN
  (PBKDF2-HMAC-SHA256, per-install salt). The PIN is never written to disk.
- Local secret/log files are git-ignored.
- This software places **real trades with real money** when Safe Mode is off.
  Start in Safe Mode, use small sizes, and never risk funds you can't lose.
  Provided as-is, no warranty.

---

## File map

| File | Role |
|------|------|
| `main.py` | Entry point: login → GUI |
| `login.py` | PIN create/verify dialog |
| `gui.py` | Tkinter frontend (the mockup layout) |
| `backend.py` | Single worker thread; serializes all exchange I/O |
| `exchange.py` | ccxt wrapper; the only place orders are placed; pure `recompute_pnl` |
| `pricefeed.py` | Real-time price feed (WebSocket + REST fallback) |
| `webhook_server.py` | TradingView alert receiver |
| `security.py` | Encryption + PIN |
| `config.py` | Constants & storage paths |
| `run.bat` | Windows launcher |
