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
| Telegram notifications | ✅ (per-user bot token + chat id; entry/close/connect) |
| Candlestick chart + strategy overlays | ✅ (Chart tab: candles, EMA, RSI sub-pane, BUY/SELL markers + SL) |
| Sound notifications | ⏳ browser notifications TODO |

## Customer management (SaaS access control)

The web build is a **standalone product** — no link to the desktop/Mac apps.
Customers sign up, enter **their own** exchange API keys, and TradingView hooks
hit their per-user webhook URL. Access is gated:

- **Free trial** on signup (default 10 days, `TREVOLTO_TRIAL_DAYS`). Trial users
  can connect and trade immediately.
- When the trial ends, connecting/trading/webhooks are blocked (HTTP 402) until
  you grant a **licence**.
- **Admin panel** at `/admin` (admin-only): view every customer, their status,
  whether they've added keys, last-seen; **suspend/activate**, **grant/revoke
  licences** (extend N days). Suspending stops a customer's trading instantly —
  including any running strategy loop and incoming webhooks.
- The **first registered user** becomes admin automatically; or set
  `TREVOLTO_ADMIN_EMAIL` so a specific email is admin on signup.

| Env | Purpose |
|---|---|
| `TREVOLTO_TRIAL_DAYS` | Free-trial length in days (default 10) |
| `TREVOLTO_ADMIN_EMAIL` | Email that becomes admin on registration |

## Run (dev)

```bash
cd app.trevolto
./run.sh                 # installs deps, generates a dev secret, serves :8000
```

Open <http://localhost:8000>, register, then **Trade → Save & Connect**. With
**Safe Mode** on (default) you can explore and place *simulated* trades without
real keys or funds.

## Run (production)

**Full subdomain setup (cPanel / VPS / Railway) → see [`DEPLOY.md`](DEPLOY.md).**
Quick Docker version:

```bash
docker build -t trevolto-web .
docker run -p 8000:8000 -v trevolto-data:/data \
  -e TREVOLTO_SECRET_KEY="$(your-kms-fetch)" \
  -e TREVOLTO_PUBLIC_URL="https://app.trevolto.com" \
  -e TREVOLTO_DATA_DIR=/data \
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
`POST /api/backtest` · `GET /api/analytics` · `GET /api/candles` · `POST /webhook/{token}` · `WS /ws`
Admin (admin JWT): `GET /api/admin/users` · `POST /api/admin/action` (suspend/activate/grant/revoke/make_admin)

## Layout

```
server/
  main.py        FastAPI routes + webhook + WebSocket + static
  session.py     per-user TraderSession (connect, trade, webhook, strategy loop)
  store.py       SQLite: users, encrypted keys, settings, trades, equity
  security.py    password hash, JWT, Fernet key encryption
  config_web.py  server settings/paths
  engine/        Trevolto trading core (reused): exchange, strategy, guardrails, backtest, config
web/             index.html (landing+dashboard) · admin.html · style.css · app.js · admin.js
```

## Limitations / next steps

- **Single-process sessions.** `TraderSession`s live in memory, so run one
  uvicorn worker (or add Redis + a shared worker registry to scale out).
- **Custody.** Server-side keys = you are a custodian; consider the desktop app
  or a per-user agent for users who won't trust server-side keys.
- **Billing** is not automated — the admin grants licences manually. Wire a
  Stripe/crypto checkout to `grant_licence()` for self-serve upgrades.
- Sound notifications aren't ported (Telegram is).
