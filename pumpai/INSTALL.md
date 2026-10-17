# Installing PumpAI for a pump owner

PumpAI is sold as a one-time product: every pump owner gets **their own copy on their own server** with
their own domain, name and logo. There is no monthly fee and no shared server — the owner's data stays
with the owner. This guide is for you (the seller / installer).

## What each customer needs

| | |
|---|---|
| **Server** | Any Ubuntu 22.04 / 24.04 (or Debian 12) machine: a small VPS (1 vCPU, 1–2 GB RAM, 20 GB disk is enough for one pump group), or a PC in the pump office with Ubuntu. |
| **Domain** | e.g. `pump.almadina.pk` or `almadinapetroleum.com`. Point an **A record** to the server's IP. HTTPS is set up for free by the installer. |
| **Optional** | Claude API key (AI features) and a WhatsApp Cloud API number — both are entered later inside the app. |

> The camera (meter / QR scanning), phone alerts and "Install app" need **HTTPS**, so use a domain.
> Without a domain the app still works on `http://<server-ip>` for office computers on the same network.

## Option 1 — one command (recommended)

Copy the PumpAI folder to the server (or use your private git repository), then:

```bash
sudo bash deploy/install.sh \
  --domain pump.almadina.pk --email owner@almadina.pk \
  --vendor-name "Your Company" --vendor-phone 0300xxxxxxx
```

Or straight from your git repository:

```bash
curl -fsSL https://raw.githubusercontent.com/<you>/PumpAI/main/deploy/install.sh | \
  sudo bash -s -- --repo https://github.com/<you>/PumpAI.git --domain pump.almadina.pk --email owner@almadina.pk
```

The installer:

1. installs Node.js 22, nginx and the SSL tools;
2. builds PumpAI in `/opt/pumpai`;
3. writes the settings file `/etc/pumpai.env` (a random secret and a **setup code**);
4. runs PumpAI as a service that starts with the computer and restarts by itself;
5. puts nginx in front of it and gets a free Let's Encrypt certificate;
6. prints the address and the **setup code**.

Add `--demo` to install with the demo pump instead (for showing the system to a customer).

## Option 2 — Docker

```bash
cp deploy/docker.env.example .env      # set DOMAIN (leave JWT_SECRET / SETUP_TOKEN empty: they are made for you)
docker compose up -d                   # PumpAI + Caddy with automatic HTTPS
docker compose logs app | grep "SETUP CODE"   # the code the setup wizard asks for
```

Data (database, backups and the sign-in secret) is kept in the `pumpai-data` volume. The app runs as a
normal user, has a health check and stops cleanly. Works on any OS with Docker (including Windows with Docker Desktop).

## Option 3 — Vercel (demo / showroom only)

The repository is ready for Vercel: in Vercel choose **Add New → Project → Import** `mcgary6567-lab/pumpai`
and press **Deploy** (no settings needed; optionally add `JWT_SECRET`). It builds the app and a demo pump
database and serves everything from one serverless function.

> Vercel has no permanent disk and no always-on server, so this is **only for showing PumpAI to customers**:
> the demo data is reset whenever Vercel restarts the function (and each instance has its own copy),
> nightly automations, WhatsApp reminders and backups do not run there. Real pumps use Option 1 or 2.

## Option 4 — Fly.io (managed, Docker, free SSL + custom domain)

Runs the Docker image as one always-on machine with a persistent volume for the database; ~$6–8/month plus
your own domain. No server to maintain. Full step-by-step in **`FLY.md`** (`fly launch --no-deploy` →
`fly volumes create pumpai_data` → `fly secrets set …` → `fly deploy` → `fly certs add <domain>`).

## First run: the setup wizard (5 minutes, with the owner)

Open the address. Because the database is empty, PumpAI shows the **setup wizard**:

1. **Business** — setup code, name, **logo**, brand colour, owner name and WhatsApp, phone, email,
   address, city, oil company, NTN / STRN, receipt line.
2. **Owner login** — the Admin (CEO) email and password (8+ characters). The owner always signs in with the
   password; 4-digit PINs are for staff on the shared tablet.
3. **Stations & tanks** — one or more stations; each tank's fuel, capacity, current stock and number of nozzles.
4. **Prices** — today's price per litre for each fuel.
5. **Check & finish** — the owner is signed in and the dashboard opens.

Standard expense categories, the daily safety checklist and the internal automations (stock, shifts, backups,
daily brief…) are switched on. Automations that **message customers or change their khata** (reminders, monthly
bills, overdue hold, late fee, win-back offers, staff coaching) start **off** — turn them on in AI Automations
once the owner has agreed the wording.

Then, inside the app:

- **Users & Roles** — add managers, salesmen (with PINs) and the wholesale officer.
- **Settings → Integrations** — paste the Claude API key and the WhatsApp Cloud API details
  (they work immediately, no restart). The WhatsApp webhook address is shown there. The **App Secret is required**
  with a WhatsApp token: without it nobody could tell real WhatsApp messages from fake ones.
- **Payment link** (optional) — if the pump has an online payment page (bank / JazzCash / Easypaisa merchant link),
  set `PAYMENT_LINK_BASE`; otherwise messages simply ask customers to pay at the pump.
- **Settings → Business profile** — change the logo, colour or details any time; they appear on the
  app, receipts, bills, the customer page, the TV rate board and salary slips.
- On each tablet / phone: open the address and tap **Install app** to put PumpAI on the home screen.

## Looking after an installation

| Task | How |
|---|---|
| Update to a new version | `sudo bash /opt/pumpai/deploy/update.sh` (git) or `sudo bash update.sh --from /path/to/new/pumpai` — the database is backed up first. |
| Backups | Every night to `/var/lib/pumpai/backups` (last 14). Download or restore from **Settings → Backups**; "Restart now" finishes a restore. **Off-site copy (do this!):** set `BACKUP_COPY_CMD`, e.g. `rclone copy "$BACKUP_FILE" gdrive:pumpai` — each nightly backup is then copied off the server and the result shows in Settings → Backups. |
| Owner forgot the password | `sudo -u pumpai bash -c 'set -a; . /etc/pumpai.env; cd /opt/pumpai/server && node dist/cli/reset-admin.js owner@mail.com NewPass123'` (Docker: `docker compose exec app node dist/cli/reset-admin.js owner@mail.com NewPass123`). |
| Lost phone / tablet | Users & Roles → "Sign out everywhere" for that person, or "Unlink all tablets". |
| Logs | `journalctl -u pumpai -f` |
| Settings file | `/etc/pumpai.env` (then `sudo systemctl restart pumpai`) |
| Move to a new server | Install on the new server and stop it. Copy the newest file from Settings → Backups (or `sqlite3 pumpai.db ".backup copy.db"`) to `/var/lib/pumpai/pumpai.db`, **and copy `JWT_SECRET` from the old `/etc/pumpai.env`** (otherwise customers' bill / khata links and PINs stop working). Start it. |
| Update safely | `update.sh` builds the new version beside the running one, switches over, and puts the old version back by itself if the new one does not start. |
| Support contact | `VENDOR_NAME`, `VENDOR_PHONE`, `VENDOR_EMAIL` in the settings file — shown to the owner in Settings → About. |

## Settings file reference (`/etc/pumpai.env`)

| Name | Meaning |
|---|---|
| `PUBLIC_URL` | The address customers open (used in WhatsApp links, bills, receipts, slips). Also editable in the app. |
| `JWT_SECRET` | Random secret for sign-in tokens and customer links (made by the installer; if empty, one is made and kept next to the database). Never share; changing it signs everyone out and breaks old customer links. A short / example value stops the app from starting. |
| `SETUP_TOKEN` | Code the setup wizard asks for (only before setup is finished). If empty, one is made and printed in the log. |
| `PAYMENT_LINK_BASE` | Optional: the pump's own online payment page. Empty = no "pay online" line in WhatsApp messages. |
| `BACKUP_COPY_CMD` | Optional but strongly advised: command that copies each nightly backup off the server (`$BACKUP_FILE` is the file). |
| `DEMO_DATA` | `1` = load the demo pump into an empty database, `0` = show the setup wizard. |
| `DB_PATH`, `BACKUP_DIR` | Where the database and backups are kept. |
| `ANTHROPIC_API_KEY`, `WA_*` | Optional; normally entered in Settings → Integrations instead. |
| `VENDOR_*` | Your company's support details. |
| `SUPERVISED` | `1` when running as a service (allows "Restart now" from the app). |
