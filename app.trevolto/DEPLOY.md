# Deploying Trevolto Web on `app.trevolto.com`

> **Read this first.** Trevolto Web is a **running Python app**, not static
> files. Your marketing site (plain HTML/PHP) can be dragged into a subdomain
> folder — this **cannot**. It needs a Python process running `uvicorn` (or
> Passenger). Pick one of the three paths below. All end with your subdomain
> serving the app over HTTPS.

---

## What's in this folder

| Path | What it is | Upload it? |
|------|-----------|------------|
| `server/` | The FastAPI backend (Python) | ✅ yes |
| `server/engine/` | The trading engine (reused from the app) | ✅ yes |
| `web/` | The dashboard + admin UI (HTML/CSS/JS) | ✅ yes |
| `requirements.txt` | Python dependencies to install | ✅ yes |
| `passenger_wsgi.py` | Entry point for cPanel "Setup Python App" | ✅ (cPanel path) |
| `Dockerfile` | For container hosts (Fly/Railway/Render/VPS) | ✅ (Docker path) |
| `fly.toml` | Ready Fly.io config (region, volume, always-on) | ✅ (Fly path) |
| `run.sh` | Local/VPS dev launcher | ✅ |
| `.env.example` | The settings you must fill in | ✅ (copy to `.env`) |
| `data/` (created at runtime) | **The database lives here** | ❌ never upload/commit |

### The database
There is **no MySQL to create.** Trevolto Web uses **SQLite** — a single file
`trevolto_web.db` auto-created inside `TREVOLTO_DATA_DIR`, plus a `master.key`.
It holds customers, their **encrypted** API keys, settings, trades and equity.
Keep `TREVOLTO_DATA_DIR` **outside the web root** and back it up — losing
`master.key` makes stored API keys unreadable.

### Settings you must provide (see `.env.example`)
`TREVOLTO_SECRET_KEY` (required), `TREVOLTO_PUBLIC_URL` (your subdomain),
`TREVOLTO_DATA_DIR`, `TREVOLTO_TRIAL_DAYS`, `TREVOLTO_ADMIN_EMAIL`.

---

## Path A — cPanel shared hosting ("Setup Python App")

Best if your subdomain is on the same cPanel as your marketing site. Note:
Passenger doesn't proxy WebSockets, so the dashboard uses **3-second polling**
instead of live push (everything still works).

1. **Create the subdomain**: cPanel → **Domains / Subdomains** →
   `app` . `trevolto.com`. Note the document root it makes
   (e.g. `/home/youruser/app.trevolto.com`).
2. **Upload** the contents of this folder into that document root (File Manager
   → Upload, or Git Version Control → clone this repo and point it there).
3. cPanel → **Setup Python App** → **Create Application**:
   - Python version **3.10+**
   - Application root = the folder you uploaded to
   - Application URL = `app.trevolto.com`
   - Application startup file = `passenger_wsgi.py`
   - Application Entry point = `application`
4. In the same screen, add **Environment variables** from `.env.example`
   (`TREVOLTO_SECRET_KEY`, `TREVOLTO_PUBLIC_URL=https://app.trevolto.com`,
   `TREVOLTO_DATA_DIR=/home/youruser/trevolto-data`, etc.).
5. Click **Run pip install** with `requirements.txt` (or open the venv terminal
   it shows and run `pip install -r requirements.txt`).
6. **Restart** the app. Visit `https://app.trevolto.com` — you should see the
   landing page. Enable **AutoSSL** (cPanel → SSL/TLS Status) for HTTPS.

---

## Path B — VPS (recommended: full real-time + WebSockets)

A $4–6/mo VPS (Hetzner/DigitalOcean/Vultr) or free Oracle Cloud VM. Gives you
live WebSocket updates and full control.

```bash
# on the server
git clone <your-repo> && cd app.trevolto
cp .env.example .env && nano .env          # fill in your values
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# run it (production server):
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Put **Caddy** in front for automatic HTTPS (point `app.trevolto.com`'s DNS
A-record at the VPS IP first):

```
# /etc/caddy/Caddyfile
app.trevolto.com {
    reverse_proxy 127.0.0.1:8000
}
```

Keep it running with systemd (`uvicorn ... --workers 1`) or `pm2`. Use
**one worker** unless you add a shared session store (see README limitations).

---

## Path C — Fly.io (recommended cloud option)

Fly runs the included `Dockerfile`, supports **WebSockets** (full real-time
dashboard), and lets you run **next to your exchange** for low order latency.
This folder ships a ready [`fly.toml`](fly.toml).

```bash
# 1. Install flyctl and log in
curl -L https://fly.io/install.sh | sh
fly auth login

# 2. From inside this folder — create the app (don't deploy yet)
cd app.trevolto
fly launch --no-deploy            # keep the app name unique; it reads fly.toml

# 3. Create the persistent disk for the SQLite DB (same region as the app!)
fly volumes create trevolto_data --size 1 --region nrt

# 4. Set your secrets (NOT in fly.toml)
fly secrets set \
  TREVOLTO_SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  TREVOLTO_ADMIN_EMAIL="you@youremail.com"
#   (edit TREVOLTO_PUBLIC_URL in fly.toml to your real subdomain first)

# 5. Deploy
fly deploy

# 6. Attach your subdomain
fly certs add app.trevolto.com
#   Then add the DNS records Fly prints (an A + AAAA, or a CNAME) at your
#   domain registrar. `fly certs show app.trevolto.com` confirms once issued.
```

**Important Fly settings (already in `fly.toml`):** `auto_stop_machines = false`
and `min_machines_running = 1`. A trading bot must stay awake 24/7 — if Fly
suspends the machine, background trades and the strategy loop stop. Keep **one**
machine (the app holds sessions in memory), and pick `primary_region` closest to
your exchange (`nrt` = Tokyo for Binance/Bybit/OKX).

### Railway / Render (alternative push-button)
Same idea: new project from the repo (root `app.trevolto`), set the env vars,
add a volume at `/data` with `TREVOLTO_DATA_DIR=/data`, keep it always-on
(disable scale-to-zero), then CNAME the subdomain.

---

## How fast does it execute trades?

Fast enough — this is a **signal/swing** bot, not high-frequency. The path is:

```
TradingView alert ──(webhook)──► your Fly app ──(REST)──► exchange
     ~0.5–2 s dispatch              ~5–20 ms proc          ~30–150 ms round-trip
```

- **Your app adds only a few milliseconds** (parse → guardrails → size → send).
- The two things that dominate are **TradingView's webhook dispatch** (typically
  ~1 second, outside anyone's control) and the **exchange API round-trip**.
- You cut the exchange round-trip by running Fly in the **same region as the
  exchange** (`primary_region = "nrt"` for Binance/Bybit/OKX on AWS Tokyo).
- Because the Trevolto strategy fires on **bar close**, a sub-second vs
  two-second fill is not meaningful to its edge — it's not scalping ticks.

If you ever wanted true low-latency (co-located, no TradingView hop), you'd use
the **built-in strategy** (candles → order, no webhook) on a machine in the
exchange's region — which this app already supports.

---

## DNS: pointing the subdomain

- **cPanel (Path A):** the subdomain is created for you — nothing extra.
- **VPS (Path B):** add an **A record** `app` → your server's IP.
- **Fly.io (Path C):** run `fly certs add app.trevolto.com`, then add the
  **A + AAAA** (or CNAME) records Fly gives you at your registrar.
- **Railway/Render:** add a **CNAME** `app` → the host's target domain.

TLS/HTTPS is **required** — TradingView only posts webhooks to `https://` URLs.

---

## First run checklist

1. Open `https://app.trevolto.com` → **Register**. The **first account** (or the
   `TREVOLTO_ADMIN_EMAIL` you set) is the **admin**.
2. Go to `https://app.trevolto.com/admin` — you should see the customer panel.
3. Register a second test account → it gets a **trial**; suspend/licence it from
   the admin panel to confirm gating works.
4. In a customer account: **Trade → Save & Connect** (keep **Safe Mode** on),
   then **Webhook** tab → copy the URL into a TradingView alert.

Your marketing site stays on its own domain/subdomain (e.g. `trevolto.com`) and
just links **"Login / Dashboard"** to `https://app.trevolto.com`.
