# Trevolto Web

A **web version of the Trevolto desktop trading app** — same engine, in the
browser, multi-user. It reuses Trevolto's trading core (`exchange.py`,
`strategy.py`, `guardrails.py`, `backtest.py`) unchanged and wraps it in a
FastAPI backend + a dashboard, so one server hosts many users.

```
Browser dashboard ─┐
TradingView webhook ┼─► FastAPI ─► per-user TraderSession ─► ccxt ─► exchange
Built-in strategy  ─┘        (guardrails → sizing → order → SL/TP bracket)
```

> ⚠️ **Trading risk & custody.** This is a full SaaS build: users' exchange API
> keys are stored **encrypted server-side** and the server trades on their
> behalf. That makes the operator a custodian — protect `TREVOLTO_SECRET_KEY`
> with a real secret manager/KMS, require trade-only keys (withdrawals off),
> and keep users in **Safe Mode** until validated. Provided as-is, no warranty.

## Feature parity with the desktop app

| Desktop feature | Web |
|---|---|
| Connect Binance/Bybit/OKX/KuCoin/Bitget/Kraken/Coinbase (spot & futures) | ✅ |
| Encrypted API keys, auth gate | ✅ (Fernet + email/password + JWT) |
| Manual BUY/SELL, market/limit | ✅ |
| TradingView webhook auto-execution | ✅ (`/webhook/{token}` per user) |
| Built-in strategy engine (no TradingView) | ✅ (EMA20+RSI crossover, live candles) |
| Risk sizing (fixed / $ / %balance / stop-based) | ✅ |
| Auto SL + TP1/TP2 scale-out bracket | ✅ |
| Guardrails (max-open / daily loss+profit / cooldown / dedupe) | ✅ |
| Positions table, live PnL, PANIC close-all | ✅ (WebSocket live updates) |
| Backtester | ✅ (`/api/backtest`) |
| Analytics (win rate, realized PnL, equity curve) | ✅ |
| Safe Mode / Read-only | ✅ |
| Telegram/sound notifications | ⏳ not ported (browser notifications TODO) |
| Candlestick chart overlays | ⏳ equity charts only so far |

## Run (dev)

```bash
cd trevolto-web
./run.sh                 # installs deps, generates a dev secret, serves :8000
```

Open <http://localhost:8000>, register, then **Trade → Save & Connect**. With
**Safe Mode** on (default) you can explore and place *simulated* trades without
real keys or funds.

## Run (production)

```bash
docker build -t trevolto-web .
docker run -p 8000:8000 -v trevolto-data:/data \
  -e TREVOLTO_SECRET_KEY="$(your-kms-fetch)" \
  -e TREVOLTO_PUBLIC_URL="https://app.yourdomain.com" \
  trevolto-web
```

Put it behind TLS (Caddy/Nginx) — TradingView webhooks require HTTPS on 443.
Scale with `uvicorn --workers N` **only** behind a shared session store; the
current build keeps `TraderSession`s in-process (one worker) — see Limitations.

## Environment

| Var | Purpose |
|---|---|
| `TREVOLTO_SECRET_KEY` | Master key for Fernet key-encryption + JWT signing. **Back with KMS.** |
| `TREVOLTO_PUBLIC_URL` | Public base URL advertised in each user's webhook URL |
| `TREVOLTO_DATA_DIR` | Where SQLite + master key live (default `./data`) |
| `TREVOLTO_POLL_INTERVAL` | Per-user account refresh cadence (s) |

## Connect TradingView

1. Go to **Webhook** tab → copy your URL (`…/webhook/<token>`).
2. TradingView alert → *Any alert() function call* → **Webhook URL** = that URL,
   Alert format = Webhook JSON. Optionally set a **passphrase**.
3. The bot reads `{action, symbol, entry, sl, tp1, tp2, comment, passphrase}`,
   sizes from your settings, and places entry + SL + TP1/TP2.

## API (JWT via `Authorization: Bearer`)

`POST /api/register` · `POST /api/login` · `GET /api/state` · `POST /api/keys` ·
`POST /api/connect` · `POST /api/disconnect` · `POST /api/trade` · `POST /api/close` ·
`POST /api/close_all` · `POST /api/settings` · `POST /api/strategy` ·
`POST /api/backtest` · `GET /api/analytics` · `POST /webhook/{token}` · `WS /ws`

## Layout

```
server/
  main.py        FastAPI routes + webhook + WebSocket + static
  session.py     per-user TraderSession (connect, trade, webhook, strategy loop)
  store.py       SQLite: users, encrypted keys, settings, trades, equity
  security.py    password hash, JWT, Fernet key encryption
  config_web.py  server settings/paths
  engine/        Trevolto trading core (reused): exchange, strategy, guardrails, backtest, config
web/             index.html · style.css · app.js  (dashboard SPA)
```

## Limitations / next steps

- **Single-process sessions.** `TraderSession`s live in memory, so run one
  uvicorn worker (or add Redis + a shared worker registry to scale out).
- **Custody.** Server-side keys = you are a custodian; consider the desktop app
  or a per-user agent for users who won't trust server-side keys.
- Telegram/sound notifications and the candlestick overlay chart aren't ported
  yet (the desktop app has them).
