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
| `Dockerfile` | For container hosts (Railway/Render/Fly/VPS) | ✅ (Docker path) |
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

## Path C — Push-button host (Railway / Render / Fly.io)

Zero server admin. This folder already has a `Dockerfile`.

1. Create a new project from your repo, root = `app.trevolto`.
2. Set the environment variables from `.env.example` in the host's dashboard.
3. Add a persistent **volume** mounted at `/data` and set
   `TREVOLTO_DATA_DIR=/data` (so the SQLite DB survives redeploys).
4. Deploy, then point `app.trevolto.com` (CNAME) at the URL the host gives you.

---

## DNS: pointing the subdomain

- **cPanel (Path A):** the subdomain is created for you — nothing extra.
- **VPS (Path B):** add an **A record** `app` → your server's IP.
- **Managed host (Path C):** add a **CNAME** `app` → the host's target domain.

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
