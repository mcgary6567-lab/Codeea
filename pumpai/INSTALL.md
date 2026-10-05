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
cp deploy/docker.env.example .env      # set DOMAIN, JWT_SECRET, SETUP_TOKEN
docker compose up -d                   # PumpAI + Caddy with automatic HTTPS
```

Data is kept in the `pumpai-data` volume. Works on any OS with Docker (including Windows with Docker Desktop).

## First run: the setup wizard (5 minutes, with the owner)

Open the address. Because the database is empty, PumpAI shows the **setup wizard**:

1. **Business** — setup code, name, **logo**, brand colour, owner name and WhatsApp, phone, email,
   address, city, oil company, NTN / STRN, receipt line.
2. **Owner login** — the Admin (CEO) email, password and optional 4-digit PIN.
3. **Stations & tanks** — one or more stations; each tank's fuel, capacity, current stock and number of nozzles.
4. **Prices** — today's price per litre for each fuel.
5. **Check & finish** — the owner is signed in and the dashboard opens.

Standard expense categories, the daily safety checklist and all automations are switched on.

Then, inside the app:

- **Users & Roles** — add managers, salesmen (with PINs) and the wholesale officer.
- **Settings → Integrations** — paste the Claude API key and the WhatsApp Cloud API details
  (they work immediately, no restart). The WhatsApp webhook address is shown there.
- **Settings → Business profile** — change the logo, colour or details any time; they appear on the
  app, receipts, bills, the customer page, the TV rate board and salary slips.
- On each tablet / phone: open the address and tap **Install app** to put PumpAI on the home screen.

## Looking after an installation

| Task | How |
|---|---|
| Update to a new version | `sudo bash /opt/pumpai/deploy/update.sh` (git) or `sudo bash update.sh --from /path/to/new/pumpai` — the database is backed up first. |
| Backups | Every night to `/var/lib/pumpai/backups` (last 14). Download or restore from **Settings → Backups**; "Restart now" finishes a restore. Copy the folder to Google Drive / a USB disk for an off-site copy. |
| Logs | `journalctl -u pumpai -f` |
| Settings file | `/etc/pumpai.env` (then `sudo systemctl restart pumpai`) |
| Move to a new server | Install on the new server, stop it, copy `pumpai.db` into `/var/lib/pumpai/`, start it. |
| Support contact | `VENDOR_NAME`, `VENDOR_PHONE`, `VENDOR_EMAIL` in the settings file — shown to the owner in Settings → About. |

## Settings file reference (`/etc/pumpai.env`)

| Name | Meaning |
|---|---|
| `PUBLIC_URL` | The address customers open (used in WhatsApp links, bills, receipts, slips). Also editable in the app. |
| `JWT_SECRET` | Random secret for sign-in tokens. Never share; changing it signs everyone out. |
| `SETUP_TOKEN` | Code the setup wizard asks for (only before setup is finished). |
| `DEMO_DATA` | `1` = load the demo pump into an empty database, `0` = show the setup wizard. |
| `DB_PATH`, `BACKUP_DIR` | Where the database and backups are kept. |
| `ANTHROPIC_API_KEY`, `WA_*` | Optional; normally entered in Settings → Integrations instead. |
| `VENDOR_*` | Your company's support details. |
| `SUPERVISED` | `1` when running as a service (allows "Restart now" from the app). |
