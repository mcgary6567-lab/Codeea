# Gold Scalpers — Crypto Patch

Three edits to make the **Gold Scalpers** indicator work on crypto pairs and feed
the **Prometheus AI Crypto Bot** cleanly. Apply them in TradingView's Pine editor
(Pine v6). Each is a **find → replace**: paste the indicator, find the first
block, replace it with the second.

---

## 1) Crypto preset, detection, support gate, price precision

Without this, a crypto symbol becomes `"Unsupported"` and **no signal ever
fires**. This adds a `Crypto` preset (auto-detected) and uses high price
precision so small-priced coins aren't truncated to `0` in the JSON.

**FIND:**
```pine
preset      = input.string("Auto", "Asset Preset", options=["Auto","XAUUSD","USDJPY","GBPJPY","Custom"], group="Asset Settings")
tickerStr   = syminfo.ticker
isGold      = str.contains(tickerStr, "XAU") or str.contains(tickerStr, "GOLD")
isUSDJPY    = str.contains(tickerStr, "USDJPY")
isGBPJPY    = str.contains(tickerStr, "GBPJPY")
autoPreset  = isGold ? "XAUUSD" : isGBPJPY ? "GBPJPY" : isUSDJPY ? "USDJPY" : "Unsupported"
eff         = preset == "Auto" ? autoPreset : preset
isSupported = eff == "XAUUSD" or eff == "USDJPY" or eff == "GBPJPY" or eff == "Custom"
priceDec    = eff == "XAUUSD" ? "0.00" : "0.000"
```

**REPLACE:**
```pine
preset      = input.string("Auto", "Asset Preset", options=["Auto","XAUUSD","USDJPY","GBPJPY","Crypto","Custom"], group="Asset Settings")
tickerStr   = syminfo.ticker
isGold      = str.contains(tickerStr, "XAU") or str.contains(tickerStr, "GOLD")
isUSDJPY    = str.contains(tickerStr, "USDJPY")
isGBPJPY    = str.contains(tickerStr, "GBPJPY")
isCrypto    = syminfo.type == "crypto"
autoPreset  = isGold ? "XAUUSD" : isGBPJPY ? "GBPJPY" : isUSDJPY ? "USDJPY" : isCrypto ? "Crypto" : "Unsupported"
eff         = preset == "Auto" ? autoPreset : preset
isSupported = eff == "XAUUSD" or eff == "USDJPY" or eff == "GBPJPY" or eff == "Crypto" or eff == "Custom"
priceDec    = eff == "XAUUSD" ? "0.00" : eff == "Crypto" ? "0.00000000" : "0.000"
```

---

## 2) Effective parameters for the Crypto preset

Gives the `Crypto` preset its own RSI / ATR / volume / lookback values.

**FIND:**
```pine
effOS   = eff == "Custom" ? custOS   : eff == "XAUUSD" ? 32  : eff == "USDJPY" ? 30  : eff == "GBPJPY" ? 30  : 30
effATR  = eff == "Custom" ? custATR  : eff == "XAUUSD" ? 0.6 : eff == "USDJPY" ? 0.7 : eff == "GBPJPY" ? 0.8 : 0.6
effVol  = eff == "Custom" ? custVol  : eff == "XAUUSD" ? 0.8 : eff == "USDJPY" ? 0.9 : eff == "GBPJPY" ? 0.9 : 0.8
effLook = eff == "Custom" ? custLook : eff == "XAUUSD" ? 8   : eff == "USDJPY" ? 10  : eff == "GBPJPY" ? 10  : 8
```

**REPLACE:**
```pine
effOS   = eff == "Custom" ? custOS   : eff == "XAUUSD" ? 32  : eff == "USDJPY" ? 30  : eff == "GBPJPY" ? 30  : eff == "Crypto" ? 30  : 30
effATR  = eff == "Custom" ? custATR  : eff == "XAUUSD" ? 0.6 : eff == "USDJPY" ? 0.7 : eff == "GBPJPY" ? 0.8 : eff == "Crypto" ? 0.8 : 0.6
effVol  = eff == "Custom" ? custVol  : eff == "XAUUSD" ? 0.8 : eff == "USDJPY" ? 0.9 : eff == "GBPJPY" ? 0.9 : eff == "Crypto" ? 1.0 : 0.8
effLook = eff == "Custom" ? custLook : eff == "XAUUSD" ? 8   : eff == "USDJPY" ? 10  : eff == "GBPJPY" ? 10  : eff == "Crypto" ? 8   : 8
```

---

## 3) Webhook passphrase in the JSON payloads

Only needed if you set a **Webhook passphrase** in the bot. Adds a `passphrase`
field to the entry + TP + SL JSON so the bot's auth accepts them.

### 3a) Add the input — FIND:
```pine
eaComment = input.string("GoldScalp",  "Order Comment",                         group=grpEA, tooltip="Label visible in MT4/MT5 terminal.")
```
**REPLACE:**
```pine
eaComment = input.string("GoldScalp",  "Order Comment",                         group=grpEA, tooltip="Label visible in MT4/MT5 terminal.")
whSecret  = input.string("",           "Webhook passphrase (for trading bot)",  group=grpEA, tooltip="Must match the bot's Webhook passphrase. Leave blank if the bot has none.")
```

### 3b) Entry JSON — FIND:
```pine
jsonMsg = '{"action":"buy","symbol":"' + tickerStr + '","entry":' + entryStr +
```
**REPLACE:**
```pine
jsonMsg = '{"action":"buy","symbol":"' + tickerStr + '","passphrase":"' + whSecret + '","entry":' + entryStr +
```

### 3c) TP-hit JSON — FIND:
```pine
                    jsTP   = '{"event":"' + tpEvent + '","symbol":"' + tickerStr +
                              '","price":' + pStr + ',"entry":' + eStr +
```
**REPLACE:**
```pine
                    jsTP   = '{"event":"' + tpEvent + '","symbol":"' + tickerStr +
                              '","passphrase":"' + whSecret +
                              '","price":' + pStr + ',"entry":' + eStr +
```

### 3d) SL-hit JSON — FIND:
```pine
                    jsSL    = '{"event":"' + slEvent + '","symbol":"' + tickerStr +
                               '","price":' + pStr + ',"entry":' + eStr + ',"sl":' + pStr +
```
**REPLACE:**
```pine
                    jsSL    = '{"event":"' + slEvent + '","symbol":"' + tickerStr +
                               '","passphrase":"' + whSecret +
                               '","price":' + pStr + ',"entry":' + eStr + ',"sl":' + pStr +
```

---

## After patching

- Set **Asset Preset = Auto** (it'll detect crypto) or **Crypto**.
- Optional for 24/7 markets: turn **"Block Sydney Open"** OFF (it's an FX-session
  filter that would skip valid crypto signals).
- Alert: **Alert Format = "Webhook JSON"**, condition *Any alert() function call*,
  webhook URL `http://YOUR_HOST:8723/`.
- In the bot: **Strategy filter = `GoldScalp`** (matches the Order Comment) and,
  if used, the same **Webhook passphrase** you set in `whSecret`.

Resulting crypto entry payload:
```json
{"action":"buy","symbol":"BTCUSDT","passphrase":"yoursecret","entry":67500.00000000,"sl":67000.00000000,"tp1":68200.00000000,"tp2":69000.00000000,"comment":"GoldScalp",...}
```
