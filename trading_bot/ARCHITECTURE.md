# How the Trading Bot Works

A visual reference. The PNG (`architecture.png`, rendered by `make_diagram.py`)
shows the whole system at a glance; the Mermaid diagrams below render directly
on GitHub and zoom into each flow.

---

## 1. System overview / data flow

```mermaid
flowchart TB
    subgraph SIGNALS["Signal sources"]
        IND["Dip2Green PRO<br/>indicator (Pine)"]
        ALERT["TradingView alert<br/>JSON webhook"]
        MANUAL["Manual BUY / SELL<br/>GUI buttons"]
        IND --> ALERT
    end

    WH["Webhook server :8723<br/>(passphrase check)"]
    ALERT -->|POST JSON| WH

    subgraph BACKEND["Backend worker — single thread, serialized exchange I/O"]
        direction LR
        G["1· Guardrails<br/>loss / cooldown / dedupe / max-open"]
        S["2· Risk sizing<br/>fixed / %bal / stop-based"]
        LM["3· Leverage<br/>& margin"]
        O["4· Order<br/>market / limit"]
        B["5· Auto SL/TP<br/>scale-out"]
        G --> S --> LM --> O --> B
    end

    WH -->|trade cmd| G
    MANUAL -->|trade cmd| G

    EX["Exchange via ccxt<br/>Binance · Bybit · OKX · KuCoin · Bitget"]
    PF["Price feed<br/>WebSocket (ccxt.pro) → REST fallback"]
    O -->|orders| EX
    B -->|reduce-only SL/TP| EX
    EX -->|fills / balance / positions| BACKEND
    PF -->|live ticks| BACKEND

    STORE[("Encrypted keys + PIN<br/>history.db (SQLite)")]
    BACKEND <-->|read keys / write trades & equity| STORE

    NOTE["Notifier: sound + Telegram"]
    B --> NOTE

    GUI["GUI (Tkinter)<br/>status · positions · log · analytics · mark price"]
    BACKEND -->|ui_queue: status / positions / log / alerts| GUI
    GUI -->|settings & commands| BACKEND
```

---

## 2. Trade decision lifecycle (every entry)

```mermaid
flowchart TD
    START([Signal arrives:<br/>webhook or manual]) --> DEDUPE{Duplicate within<br/>dedupe window?}
    DEDUPE -- yes --> BLOCK[Log 'Blocked' + notify]
    DEDUPE -- no --> HALT{Daily loss<br/>limit tripped?}
    HALT -- yes --> BLOCK
    HALT -- no --> COOL{Symbol in<br/>cooldown?}
    COOL -- yes --> BLOCK
    COOL -- no --> MAX{Max open<br/>positions hit?}
    MAX -- yes --> BLOCK
    MAX -- no --> SIZE[Size order:<br/>fixed / %balance / risk-per-trade from stop]
    SIZE --> LEV[Apply leverage<br/>& margin mode]
    LEV --> SAFE{Safe Mode?}
    SAFE -- yes --> SIM[Simulate fill locally]
    SAFE -- no --> SEND[Send market/limit order via ccxt]
    SIM --> BRACKET
    SEND --> OK{Filled?}
    OK -- no --> REJ[Log 'Rejected' + notify]
    OK -- yes --> BRACKET[Place reduce-only SL +<br/>TP1/TP2 scale-out]
    BRACKET --> REC[Record to history.db<br/>+ notify + refresh UI]
```

---

## 3. Threading model (why the UI never freezes)

```mermaid
flowchart LR
    subgraph MAIN["Main thread (Tk event loop)"]
        UI["GUI widgets"]
        DRAIN["root.after → drain ui_queue"]
    end
    subgraph WORK["Backend worker thread"]
        LOOP["command loop +<br/>periodic REST refresh"]
    end
    subgraph FEED["Price-feed thread"]
        WS["WebSocket / REST tickers"]
    end
    subgraph WHT["Webhook server thread(s)"]
        HTTP["HTTP handler"]
    end

    UI -->|command_queue| LOOP
    HTTP -->|command_queue| LOOP
    WS -->|on_prices| LOOP
    LOOP -->|ui_queue| DRAIN
    DRAIN --> UI
```

All exchange calls happen **only** on the worker thread, so the ccxt client is
never touched concurrently. Other threads communicate via thread-safe queues;
the GUI applies updates on the main thread from `ui_queue`.

---

## 4. Real-time price + PnL loop

```mermaid
sequenceDiagram
    participant F as Price feed
    participant B as Backend
    participant G as GUI
    loop every tick (WS) / 2s (REST)
        F->>B: on_prices({symbol: price})
        B->>B: recompute PnL = (price-entry)*size
        B->>G: ui_queue → account (positions + PnL)
        B->>G: ui_queue → ticker (manual mark price)
    end
    loop every 5s
        B->>B: REST fetch balance + positions (authoritative)
        B->>B: detect closed positions → realized PnL
        B->>G: ui_queue → account / log / equity snapshot
    end
```

---

To regenerate the PNG: `python make_diagram.py` (needs `matplotlib`).
