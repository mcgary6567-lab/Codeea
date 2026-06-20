# Webhook Setup — TradingView → Prometheus AI Crypto Bot

How to connect the **Prometheus AI Crypto Bot** indicator on TradingView to the
Windows app so signals auto-execute.

```
Prometheus indicator → TradingView alert (webhook) → ngrok → app :8723 → exchange
```

> You do **not** need to "publish" the indicator. You just add it to a chart and
> create an **Alert** with a **Webhook URL**. Webhooks require a **paid
> TradingView plan**.

---

## 1. App side (once)

1. Open the app, enter your PIN, **Connect** your exchange.
2. **Webhook & Alerts** tab → it shows **● listening** (the webhook auto-starts
   on port **8723**). Leave **Strategy filter = `Prometheus`**.
3. Keep **Safe Mode ON** (Modes & Risk) for your first test.

## 2. Expose port 8723 to the internet

TradingView is remote and can only POST to a **public URL**; the app listens on
`localhost:8723`. Bridge them with a tunnel:

```bash
ngrok http 8723
```

Copy the `https://xxxx.ngrok-free.app` URL it prints — that's your webhook URL.

> The PC running the app + tunnel must stay **on and online**. A free ngrok URL
> changes on each restart (update the alert); a paid static domain or a VPS
> gives a permanent URL.

## 3. Indicator inputs (TradingView)

On the **Prometheus AI Crypto Bot** settings (Inputs tab):

| Setting | Value |
|--------|-------|
| Alert Mode | **Bar Close** (no repaint) |
| Alert Format | **Webhook JSON** ← required |
| Strategy tag | **`Prometheus`** (must match the app's Strategy filter) |
| Webhook passphrase | blank, **or** the same value as the app's passphrase |
| Alert on TP Hit | ✓ (sends tp1_hit / tp2_hit events) |

## 4. Create the alert (the two gotchas)

Click the **⏰ Alert** button on the chart:

### ✅ Condition — pick "Any alert() function call"
```
Condition:  Prometheus AI Crypto Bot   ▼
            Any alert() function call   ▼   ← THIS
```
**Do NOT pick "Prometheus BUY".** That option is the `alertcondition` and sends
only fixed text (`Prometheus AI Crypto Bot - BUY signal`) — **not JSON** — and
the bot will reject it (`bad json`). *"Any alert() function call"* sends the
real JSON payload (entry **and** TP events). When it's selected, the Message box
greys out — that's expected (the JSON comes from the indicator).

### ✅ Notifications → Webhook URL
- Tick **Webhook URL** and paste your ngrok URL: `https://xxxx.ngrok-free.app/`
- **Replace the `https://example.com/alert-hook` placeholder** — leaving it
  shows *"A Webhook URL is required."*
- Other toggles (Notify in app, Toast, Sound, Plain text) are optional/harmless.

Set **Trigger = Once per bar close**, then **Create**.

## 5. Test before going live

With ngrok running, fire a test from your PC and watch the app's **Trade Log**:

```bat
curl -X POST https://xxxx.ngrok-free.app/ -H "Content-Type: application/json" ^
 -d "{\"action\":\"buy\",\"symbol\":\"BTCUSD\",\"entry\":67500,\"tp1\":68200,\"tp2\":69500,\"comment\":\"Prometheus\"}"
```

Expected: a **BUY (Simulated)** row + **two TP** rows, and the Manual symbol box
auto-fills to `BTCUSD`.

When you're confident, turn **Safe Mode OFF** (confirm the one-time LIVE prompt)
and use small size.

---

## Troubleshooting

| In the Trade Log / TradingView | Cause | Fix |
|---|---|---|
| `bad json` | Alert Condition is **"Prometheus BUY"** | Switch to **Any alert() function call** |
| `ignored (strategy '…' != Prometheus)` | Strategy tag ≠ Strategy filter | Make both `Prometheus` (or blank the filter) |
| `unauthorized` (401) | Passphrase mismatch | Match indicator `whSecret` to the app's Webhook passphrase (or blank both) |
| Nothing arrives at all | URL not public / app not listening / PC asleep | Use the ngrok URL (not localhost), ensure **● listening**, keep PC awake |
| `A Webhook URL is required` | Placeholder URL left in | Paste your ngrok `https://…` URL |
| Order `Rejected: Not connected` | Exchange not connected | Click **Connect** first |

## Payload reference

**Entry** (no stop-loss — Entry + TP1 + TP2 only):
```json
{"action":"buy","symbol":"BTCUSD","passphrase":"","entry":67500,"tp1":68200,"tp2":69500,"comment":"Prometheus","tf":"1","time":"…"}
```
**TP hit event:**
```json
{"event":"tp1_hit","symbol":"BTCUSD","passphrase":"","price":68200,"entry":67500,"tp1":68200,"tp2":69500,"comment":"Prometheus","tf":"1","time":"…"}
```
