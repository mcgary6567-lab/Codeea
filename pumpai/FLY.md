# Deploying PumpAI on Fly.io

Fly.io runs the PumpAI Docker image as **one always-on machine** with a **persistent volume** for the
SQLite database. Custom domain + HTTPS are free. You buy a domain separately (Fly does not sell domains).

> Cost: roughly **$6–8 / month** (1 GB machine + 3 GB volume), plus your domain (~$10–15/year for a .com).

## 0. One-time: install the CLI and sign in

```bash
# macOS/Linux:  curl -L https://fly.io/install.sh | sh      # Windows: iwr https://fly.io/install.ps1 -useb | iex
fly auth signup      # or: fly auth login   (needs a card — Fly has no free tier now)
```

Run the rest from the PumpAI folder (the one with `fly.toml` and `Dockerfile`).

## 1. Create the app (do NOT deploy yet)

```bash
fly launch --no-deploy --copy-config --name <your-app-name> --region bom
```

- Pick a unique `<your-app-name>` (becomes `https://<your-app-name>.fly.dev`).
- It reuses the bundled `fly.toml`. If it asks to overwrite it, say **No**.
- `bom` = Mumbai (closest to Pakistan). `sin` (Singapore) also fine.

## 2. Create the persistent volume (the database lives here)

```bash
fly volumes create pumpai_data --size 3 --region bom   # same region as the app
```

3 GB holds the database and 14 nightly backups for a long time; grow later with `fly volumes extend`.

## 3. Set the secrets

```bash
fly secrets set \
  JWT_SECRET="$(openssl rand -hex 32)" \
  SETUP_TOKEN="$(openssl rand -hex 4)" \
  PUBLIC_URL="https://pump.yourdomain.pk" \
  VENDOR_NAME="Your Company" VENDOR_PHONE="03001234567" VENDOR_EMAIL="support@yourco.pk"
```

- **JWT_SECRET** — sign-in + customer-link secret. Keep it safe; changing it later signs everyone out.
- **SETUP_TOKEN** — the code the setup wizard asks for on first open. Note it down (`fly secrets list` shows names only, not values).
- **PUBLIC_URL** — your final domain (used in WhatsApp links, bills, receipts). Put the real domain even before DNS is live.
- AI / WhatsApp keys are entered **inside the app** later (Settings → Integrations), not here.

## 4. Deploy

```bash
fly deploy
```

First build takes a few minutes. When it finishes, open `https://<your-app-name>.fly.dev` — the **setup wizard** appears (enter the SETUP_TOKEN). You can run the whole wizard on the `.fly.dev` address and add the domain next.

## 5. Add your domain (free SSL)

```bash
fly ips list                       # note the shared v4 (or allocate: fly ips allocate-v4 --shared) and v6
fly certs add pump.yourdomain.pk
fly certs show pump.yourdomain.pk  # shows the exact DNS records to add
```

At your domain provider (GoDaddy / Namecheap / PKNIC), add:
- an **A** record → the IPv4 Fly shows, and an **AAAA** record → the IPv6, **or** a **CNAME** → `<your-app-name>.fly.dev` (for a subdomain like `pump.`).
- plus the small **_acme-challenge** record `fly certs show` lists, so Fly can issue the certificate.

Within minutes `fly certs show` says **Ready** and `https://pump.yourdomain.pk` works. (If you didn't set `PUBLIC_URL` to the final domain in step 3, do it now: `fly secrets set PUBLIC_URL="https://pump.yourdomain.pk"`.)

## 6. First run — the setup wizard (5 minutes, with the owner)

Open the domain → the wizard walks through Business (setup code, name, **logo**, colour, owner WhatsApp, NTN/STRN),
Owner login (CEO email + password), Stations & tanks, Prices → done. Then in the app:
- **Users & Roles** — managers, salesmen (PINs), cashier, wholesale officer.
- **Settings → Integrations** — paste the Gemini/Claude key and WhatsApp Cloud API (work immediately).
- On each phone/tablet: open the domain → **Install app**.

## Day-to-day on Fly

| Task | How |
|---|---|
| New version | `git pull` (or copy the new code), then `fly deploy`. The volume (database) is untouched. |
| Logs | `fly logs` |
| Open a shell | `fly ssh console` (database at `/data/pumpai.db`, backups in `/data/backups`) |
| Download a backup | In the app: **Settings → Backups** → download. Or `fly ssh sftp get /data/backups/<file>`. |
| **Off-site backup (do this!)** | Set `BACKUP_COPY_CMD` as a secret so each nightly backup is copied off Fly (e.g. to Google Drive via `rclone`). The volume is on one machine — keep a copy elsewhere. |
| Owner forgot password | `fly ssh console -C "node /app/server/dist/cli/reset-admin.js owner@mail.com NewPass123"` |
| More RAM / disk | `fly scale memory 2048` · `fly volumes extend <id> --size 5` |

### Important Fly notes
- **Keep it one machine, always-on.** `fly.toml` already sets `min_machines_running = 1` and
  `auto_stop_machines = false`. SQLite needs a single writer, and the nightly reminders / monthly bills /
  backups only run if the machine stays up. **Do not** run `fly scale count 2`.
- The machine and the volume must be in the **same region** (`bom` here).
- A `.fly.dev` URL is always there as a fallback even after you add your own domain.
