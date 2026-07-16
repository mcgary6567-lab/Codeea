# Deploying Prometheus Web on `app.prometheus.com`

> **Read this first.** Prometheus Web is a **running Python app**, not static
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
There is **no MySQL to create.** Prometheus Web uses **SQLite** — a single file
`prometheus_web.db` auto-created inside `PROMETHEUS_DATA_DIR`, plus a `master.key`.
It holds customers, their **encrypted** API keys, settings, trades and equity.
Keep `PROMETHEUS_DATA_DIR` **outside the web root** and back it up — losing
`master.key` makes stored API keys unreadable.

### Settings you must provide (see `.env.example`)
`PROMETHEUS_SECRET_KEY` (required), `PROMETHEUS_PUBLIC_URL` (your subdomain),
`PROMETHEUS_DATA_DIR`, `PROMETHEUS_TRIAL_DAYS`, `PROMETHEUS_ADMIN_EMAIL`.

---

## Path A — cPanel shared hosting ("Setup Python App")

Best if your subdomain is on the same cPanel as your marketing site. Note:
Passenger doesn't proxy WebSockets, so the dashboard uses **3-second polling**
instead of live push (everything still works).

1. **Create the subdomain**: cPanel → **Domains / Subdomains** →
   `app` . `prometheus.com`. Note the document root it makes
   (e.g. `/home/youruser/app.prometheus.com`).
2. **Upload** the contents of this folder into that document root (File Manager
   → Upload, or Git Version Control → clone this repo and point it there).
3. cPanel → **Setup Python App** → **Create Application**:
   - Python version **3.10+**
   - Application root = the folder you uploaded to
   - Application URL = `app.prometheus.com`
   - Application startup file = `passenger_wsgi.py`
   - Application Entry point = `application`
4. In the same screen, add **Environment variables** from `.env.example`
   (`PROMETHEUS_SECRET_KEY`, `PROMETHEUS_PUBLIC_URL=https://app.prometheus.com`,
   `PROMETHEUS_DATA_DIR=/home/youruser/prometheus-data`, etc.).
5. Click **Run pip install** with `requirements.txt` (or open the venv terminal
   it shows and run `pip install -r requirements.txt`).
6. **Restart** the app. Visit `https://app.prometheus.com` — you should see the
   landing page. Enable **AutoSSL** (cPanel → SSL/TLS Status) for HTTPS.

---

## Path B — VPS (recommended: full real-time + WebSockets)

A $4–6/mo VPS (Hetzner/DigitalOcean/Vultr) or free Oracle Cloud VM. Gives you
live WebSocket updates and full control.

```bash
# on the server
git clone <your-repo> && cd app.prometheus
cp .env.example .env && nano .env          # fill in your values
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# run it (production server):
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Put **Caddy** in front for automatic HTTPS (point `app.prometheus.com`'s DNS
A-record at the VPS IP first):

```
# /etc/caddy/Caddyfile
app.prometheus.com {
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
cd app.prometheus
fly launch --no-deploy            # keep the app name unique; it reads fly.toml

# 3. Create the persistent disk for the SQLite DB (same region as the app!)
fly volumes create prometheus_data --size 1 --region nrt

# 4. Set your secrets (NOT in fly.toml)
fly secrets set \
  PROMETHEUS_SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  PROMETHEUS_ADMIN_EMAIL="you@youremail.com"
#   (edit PROMETHEUS_PUBLIC_URL in fly.toml to your real subdomain first)

# 5. Deploy
fly deploy

# 6. Attach your subdomain
fly certs add app.prometheus.com
#   Then add the DNS records Fly prints (an A + AAAA, or a CNAME) at your
#   domain registrar. `fly certs show app.prometheus.com` confirms once issued.
```

**Important Fly settings (already in `fly.toml`):** `auto_stop_machines = false`
and `min_machines_running = 1`. A trading bot must stay awake 24/7 — if Fly
suspends the machine, background trades and the strategy loop stop. Keep **one**
machine (the app holds sessions in memory), and pick `primary_region` closest to
your exchange (`nrt` = Tokyo for Binance/Bybit/OKX).

### Auto-deploy from GitHub — NO terminal needed (browser only)

The workflow at `.github/workflows/deploy-fly.yml` is **self-bootstrapping**: on
GitHub's runners (which *can* reach Fly) it creates the app, creates the data
volume, sets the secrets, and deploys. You only add repo secrets in the browser.

**Steps (all in the browser):**

1. **Pick a globally-unique app name.** Fly app names are shared across all Fly
   users, so `prometheus-web` may be taken. Edit `app.prometheus/fly.toml` line
   `app = "…"` to something unique (e.g. `prometheus-web-yourname`). Commit it.
2. **Create a Fly deploy token:** Fly dashboard → **Tokens** (or
   <https://fly.io/user/personal_access_tokens>) → create a token → copy it.
3. **Add GitHub repo secrets:** your repo → **Settings → Secrets and variables →
   Actions → New repository secret**. Add:
   - `FLY_API_TOKEN` = the token from step 2
   - `PROMETHEUS_SECRET_KEY` = a long random string (e.g. from
     <https://generate-secret.vercel.app/32> or any password generator)
   - `PROMETHEUS_ADMIN_EMAIL` = your email *(optional — makes you admin)*
4. **Run it:** repo → **Actions → Deploy Prometheus Web to Fly.io → Run
   workflow**. Watch it create everything and deploy. (It also runs on every
   push to `main` that touches the app.)
5. **Attach your subdomain** in the Fly dashboard (Certificates) or once, via
   `fly certs add app.prometheus.com`, then add the DNS records Fly shows.

The token is revocable any time from the Fly dashboard. The `--ha=false` deploy
keeps it to a single always-on machine (correct for this app).

### Railway / Render (alternative push-button)
Same idea: new project from the repo (root `app.prometheus`), set the env vars,
add a volume at `/data` with `PROMETHEUS_DATA_DIR=/data`, keep it always-on
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
- Because the Prometheus strategy fires on **bar close**, a sub-second vs
  two-second fill is not meaningful to its edge — it's not scalping ticks.

If you ever wanted true low-latency (co-located, no TradingView hop), you'd use
the **built-in strategy** (candles → order, no webhook) on a machine in the
exchange's region — which this app already supports.

---

## DNS: pointing the subdomain

- **cPanel (Path A):** the subdomain is created for you — nothing extra.
- **VPS (Path B):** add an **A record** `app` → your server's IP.
- **Fly.io (Path C):** run `fly certs add app.prometheus.com`, then add the
  **A + AAAA** (or CNAME) records Fly gives you at your registrar.
- **Railway/Render:** add a **CNAME** `app` → the host's target domain.

TLS/HTTPS is **required** — TradingView only posts webhooks to `https://` URLs.

---

## First run checklist

1. Open `https://app.prometheus.com` → **Register**. The **first account** (or the
   `PROMETHEUS_ADMIN_EMAIL` you set) is the **admin**.
2. Go to `https://app.prometheus.com/admin` — you should see the customer panel.
3. Register a second test account → it gets a **trial**; suspend/licence it from
   the admin panel to confirm gating works.
4. In a customer account: **Trade → Save & Connect** (keep **Safe Mode** on),
   then **Webhook** tab → copy the URL into a TradingView alert.

Your marketing site stays on its own domain/subdomain (e.g. `prometheus.com`) and
just links **"Login / Dashboard"** to `https://app.prometheus.com`.
