# Dip2Green PRO → Exchange Bridge

Receives the JSON alerts emitted by the `Dip2Green PRO` TradingView indicator
and places **real bracket orders** (entry + stop-loss + two take-profits) on a
crypto exchange via [CCXT](https://github.com/ccxt/ccxt).

One code path works for **Binance, Bybit, OKX, Kraken, KuCoin, Gate.io, Bitget,
MEXC** and other CCXT-supported exchanges.

```
Dip2Green PRO alert (JSON)  ──webhook──►  this bridge  ──REST API──►  your exchange
```

> ⚠️ **Trading risk.** This software can place live orders that risk real money.
> It ships with `DRY_RUN=true` and `TESTNET=true`. Keep them on until you have
> validated the full flow. Use API keys with **trade-only** permission
> (withdrawals disabled) and IP-whitelist your server. You are responsible for
> every order it sends.

---

## 1. Why this is needed

A TradingView **indicator** (like Dip2Green PRO) cannot talk to an exchange's
order engine. On each signal it can only fire an HTTP POST ("webhook") to a URL
you choose. This bridge is that URL: it authenticates the request, sizes the
position from your risk settings, and submits the orders.

## 2. Install

Requires Python 3.10+.

```bash
cd bridge
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then edit .env
```

Set at minimum in `.env`:
- `WEBHOOK_TOKEN` — a long random string (shared secret).
- `EXCHANGE`, `API_KEY`, `API_SECRET` (and `API_PASSWORD` for OKX/KuCoin/Bitget).
- `MARKET_TYPE` — `spot`, `swap`, or `future`.
- `RISK_PCT` — fraction of quote balance to risk per trade (`0.01` = 1%).

## 3. Run

```bash
# Development / dry-run
python app.py

# Production (behind HTTPS, see step 6)
waitress-serve --port=8080 app:app
```

Check it's alive:

```bash
curl localhost:8080/health
# {"dry_run":true,"exchange":"binance","status":"ok","testnet":true}
```

## 4. Test it end-to-end (no exchange needed while DRY_RUN=true)

```bash
TOKEN=$(grep WEBHOOK_TOKEN .env | cut -d= -f2)
curl -X POST "localhost:8080/webhook?token=$TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"BUY","symbol":"BTCUSDT","tf":"60","entry":68000,"sl":67200,"tp1":68800,"tp2":69600,"rr":2,"score":78.5}'
```

You should see a `"status":"dry_run"` response and a `PLAN ...` line in the logs
showing the size and the SL/TP1/TP2 it *would* place.

## 5. Wire up TradingView

1. You need a **paid TradingView plan** (Essential or higher) for webhooks.
2. Add the indicator to your chart. In its settings, keep **"Emit JSON payload"**
   ON (Alerts group).
3. Right-click the chart → **Add alert**.
   - **Condition:** `Dip2Green PRO` → choose the `LONG` / `SHORT` alert, or use
     "Any alert() function call" to capture both BUY and SELL.
   - **Webhook URL:** `https://your-domain.com/webhook?token=YOUR_WEBHOOK_TOKEN`
   - **Message:** leave as is — the indicator already builds the JSON. (You can
     also add `"secret":"YOUR_WEBHOOK_TOKEN"` inside the JSON instead of the URL
     token.)
4. Create the alert. TradingView only allows webhooks to **port 80/443**, so the
   bridge must sit behind HTTPS (next step).

## 6. Expose it over HTTPS

TradingView will only POST to a public HTTPS URL. Options:
- **Cloudflare Tunnel** (free): `cloudflared tunnel --url http://localhost:8080`
- **ngrok**: `ngrok http 8080`
- **VPS + Caddy/Nginx** with a real domain and TLS (best for 24/7 use).

Keep the `?token=` secret out of screenshots/logs you share.

## 7. Go live

When dry-run looks correct:
1. Fund the exchange **testnet**, set `DRY_RUN=false`, keep `TESTNET=true`, and
   confirm orders actually appear on the testnet.
2. Only then set `TESTNET=false` with small `RISK_PCT` and a `MAX_NOTIONAL` cap.

## Configuration reference

See [`.env.example`](.env.example) — every option is documented inline.

## How sizing works

Position size = `(quote_balance * RISK_PCT) / |entry - stop|`, then capped by
`MAX_NOTIONAL`. The position is split across TP1/TP2 by `TP1_FRACTION` /
`TP2_FRACTION`. The stop-loss covers the full size (reduce-only).

## Limitations / notes

- Stop/TP order params are submitted with CCXT's unified `triggerPrice` /
  `reduceOnly`. A few exchanges need small tweaks — if an order is rejected,
  check that exchange's CCXT page and adjust `broker.py`'s `create_order` params.
- No persistence: if the bridge restarts mid-trade, the exchange-side bracket
  orders remain, but the bridge keeps no local state. That's intentional and
  simple; the exchange manages the open orders.
- One signal = one bracket. It does not pyramid or manage trailing stops
  (the indicator's on-chart trailing is for visualization).
