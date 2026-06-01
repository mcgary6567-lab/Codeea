# 🔑 API Setup Guide — Prometheus AI Crypto Bot

How to create an exchange API key, give it the **right** permissions (and only
those), and connect it to the bot. **5 minutes.**

> **One golden rule:** never enable **Withdrawals** on a trading bot key. The bot
> never withdraws. If a key with withdrawals leaks, your funds can be drained.

---

## TL;DR

1. Exchange → **API Management** → **Create API key**.
2. Enable: ✅ **Read** + ✅ **Spot Trading** (or ✅ **Futures** if you trade futures).
3. **Disable** ❌ **Withdrawals**. (Recommended: add an **IP whitelist**.)
4. Copy the **Key**, **Secret**, and — on OKX/KuCoin/Bitget — the **Passphrase**.
5. Paste them into the bot, pick **Spot/Futures**, click **Connect** (start in **Safe Mode**).

---

## What permissions the bot needs

| Permission | Spot | Futures | Why |
|---|:--:|:--:|---|
| **Read / Enable Reading** | ✅ | ✅ | See balance & open positions |
| **Spot & Margin Trading** | ✅ | — | Place spot orders |
| **Futures / Derivatives** | — | ✅ | Place futures orders |
| **Withdrawals** | ❌ | ❌ | **Never needed — keep OFF** |
| **IP whitelist** | 👍 | 👍 | Locks the key to your PC (Binance often requires it for trading) |

Under the hood the bot only calls: `fetchBalance`, `fetchPositions`,
`fetchTickers`, `loadMarkets` (Read) and `createOrder`, `cancelOrder` (Trade).

---

## Per-exchange: where to click

### 🟡 Binance
1. Profile → **API Management** → **Create API** → *System generated*.
2. Verify (2FA/email).
3. Edit restrictions:
   - ✅ **Enable Reading**
   - ✅ **Enable Spot & Margin Trading** (or **Enable Futures**)
   - ❌ leave **Enable Withdrawals** OFF
4. **Restrict access to trusted IPs** → add your PC's IP (recommended; often
   required before trading works).
5. Copy **API Key** + **Secret Key**. *(No passphrase on Binance.)*

### 🟠 Bybit
1. Account → **API** → **Create New Key** → *System-generated*.
2. Permissions: ✅ **Orders** + ✅ **Positions** (and **Spot** / **Derivatives**
   as needed). Read is included.
3. **Withdraw** → leave unchecked.
4. Set IP restriction (recommended). Copy **Key** + **Secret**. *(No passphrase.)*

### ⚫ OKX
1. Profile → **API** → **Create V5 API Key**.
2. Permission: choose **Trade** (Read is included). **Not** *Withdraw*.
3. **You set a Passphrase** here — remember it.
4. Copy **API Key** + **Secret Key** + **Passphrase** → all three go in the bot.

### 🟢 KuCoin
1. **API Management** → **Create API** → *API-based*.
2. Permissions: ✅ **General** (read) + ✅ **Spot/Futures Trade**. **No Transfer/Withdraw.**
3. **You set an API Passphrase** — remember it.
4. Copy **Key** + **Secret** + **Passphrase** → all three go in the bot.

### 🔵 Bitget
1. **API Management** → **Create API** → *Self-generated*.
2. Permissions: ✅ **Read-only** + ✅ **Trade** (Spot or Futures). **No Withdraw.**
3. **You set a Passphrase** — remember it.
4. Copy **Key** + **Secret** + **Passphrase** → all three go in the bot.

> **Passphrase needed?** Yes for **OKX · KuCoin · Bitget**. No for **Binance · Bybit**.

---

## Connect in the bot

1. Pick your **Exchange** and **Market** (**Spot** is the safer default).
2. Paste **API Key**, **Secret** (and **Passphrase** if shown).
3. Keep **Safe Mode ON** for the first run — real prices & balance, but fills are
   simulated, so you can verify everything risk-free.
4. Click **Connect**. The status light turns green and your balance appears.
5. Happy? Turn **Safe Mode off** and trade small.

---

## If it doesn't connect / trade — quick fixes

| Symptom | Cause | Fix |
|---|---|---|
| **Invalid API-key, IP, or permissions** (-2015) | Trading not enabled, or IP not whitelisted | Enable Spot/Futures on the key; add your IP |
| **Timestamp error** (-1021) | PC clock out of sync | Auto-handled; if it persists, sync Windows time |
| **Signature / 401** | Wrong secret, or missing passphrase | Re-paste secret; add the passphrase (OKX/KuCoin/Bitget) |
| **Balance shows $0** | Funds in the other wallet | Move USDT to **Spot** wallet (Market=Spot) or **Futures** wallet (Market=Futures) |
| **Order rejected: insufficient balance** | Not enough USDT | Fund the matching wallet |
| **MIN_NOTIONAL / LOT_SIZE** | Order too small | Increase size (most exchanges need ≈ $5–10 minimum) |
| **Order rejected: … reduceOnly** | reduce-only on spot | Spot has no SL/reduce orders — this is expected; use Futures for stop brackets |

The bot also **warns you if a Binance key has withdrawals enabled** — if you see
that, go back and turn withdrawals off.

---

## Security checklist ✅

- [ ] Withdrawals **disabled** on the key
- [ ] IP whitelist set (where supported)
- [ ] Key/secret stored **only** in the bot (it encrypts them behind your PIN)
- [ ] Tested in **Safe Mode** before going live
- [ ] Trading **small size** until you trust it

---

*Trading places real orders with real money when Safe Mode is off. Start small,
disable withdrawals, and never risk funds you can't afford to lose.*
