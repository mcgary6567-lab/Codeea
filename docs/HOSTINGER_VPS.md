# Installing on a Hostinger VPS

Written 12 October 2026, when the free Render database expired. One Hostinger VPS runs everything: the
app, its PostgreSQL database, HTTPS (Caddy with a free Let's Encrypt certificate) and a nightly database
backup. Nothing expires and nothing sleeps.

Time needed: about 20 minutes, most of it waiting.

## 1. Choose the VPS

| Plan | Use |
|---|---|
| KVM 1 (1 vCPU, 4 GB) | Enough to start, up to a few hundred students |
| **KVM 2 (2 vCPU, 8 GB)** | Recommended for daily use by all staff, teachers and parents |

When Hostinger asks for the operating system, choose plain **Ubuntu 24.04** under *Plain OS*. Do **not**
choose a control-panel template (CloudPanel, cPanel, CyberPanel, Plesk) or the **Docker / Docker Manager**
application template: they run their own web server (the Docker template runs Traefik) on ports 80 and 443,
which the app needs for HTTPS. The installer stops with "Ports 80/443 are already in use" if it finds one.
A server that answers `http://<VPS IP>` with a redirect and "404 page not found" before you installed
anything has Traefik on it. If the VPS already has a panel, use
hPanel › VPS › **OS & Panel** › *Operating System* to reinstall it with plain Ubuntu 24.04.

Set a strong root password, or better, add your SSH key, during setup.

## 2. Point a domain at the VPS

Pick the address staff will use, for example `erp.onlinequrancollege.com`.

1. In hPanel › VPS › **Overview**, copy the VPS **IPv4 address**.
2. Where the domain's DNS is managed (hPanel › Domains › **DNS / Nameservers**, or your registrar), add an
   **A record**: name `erp`, points to the VPS IPv4 address, TTL 300.
3. Wait until it works: `ping erp.onlinequrancollege.com` should show the VPS address (usually minutes,
   sometimes up to an hour).

The installer checks this and warns you if the domain does not point at the server yet. HTTPS starts by
itself as soon as it does.

## 3. Open the firewall

If you switched on the Hostinger firewall (hPanel › VPS › **Security** › *Firewall*), allow:

| Port | For |
|---|---|
| 22 (TCP) | SSH |
| 80 (TCP) | HTTP, and the Let's Encrypt check |
| 443 (TCP and UDP) | HTTPS |

If the firewall is off, there is nothing to do. The installer also opens 80 and 443 in `ufw` when `ufw` is on.

## 4. Run the installer

Open the server's terminal: hPanel › VPS › **Browser terminal**, or from your computer
`ssh root@<VPS IP>`. Then run:

```bash
curl -fsSL https://raw.githubusercontent.com/mcgary6567-lab/Codeea/main/install.sh | bash
```

It asks three questions:

1. **Domain**: type `erp.onlinequrancollege.com` (without `https://`).
2. **Admin e-mail**: the owner's real e-mail. It becomes the super-admin sign-in.
3. **Demo data**: answer **N** for real use (a clean system with roles, settings and the admin account),
   or **y** for a training copy full of sample families, teachers and classes.

It then installs Docker, downloads the app to `/opt/oqc`, writes `/opt/oqc/.env` with new random
passwords, builds and starts everything. The first start takes 2–5 minutes. At the end it prints:

```
URL        : https://erp.onlinequrancollege.com
Sign in    : owner@example.com
Password   : <generated>   (you must change it on first sign-in)
```

**Write the password down now.** It is also in `/opt/oqc/.env` (`ADMIN_PASSWORD`).

## 5. First sign-in

1. Open the URL, sign in, and choose your own password when asked.
2. Human Resource › **Users & Access**: create the staff accounts.
3. Configuration › **Settings**: check the college name, currency and time zone.

## 6. E-mail, WhatsApp, GoHighLevel and AI

These stay switched off (simulated) until you fill them in. Edit the settings file:

```bash
nano /opt/oqc/.env
```

| Setting | Value |
|---|---|
| `SMTP_HOST` | `smtp.hostinger.com` (Hostinger e-mail) or your mail provider |
| `SMTP_PORT` | `587` |
| `SMTP_USER` / `SMTP_PASSWORD` | the mailbox, e.g. `noreply@onlinequrancollege.com`, and its password |
| `SMTP_FROM` | the same mailbox |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID` | from Meta WhatsApp Cloud API |
| `GHL_API_KEY` | from GoHighLevel (then turn sync on in Configuration › GoHighLevel Sync) |
| `AI_PROVIDER`, `AI_API_KEY` | only if you use an AI provider |
| `ZOOM_ACCOUNT_ID`, `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET` | a Zoom Server-to-Server OAuth app (scopes `meeting:write:admin`, `recording:read:admin`): Get Meeting URL and SYNC Calls |
| `SMS_GATEWAY_URL` | a local SMS gateway's send URL with `{to}`, `{text}` and `{sender}` in it (then Integrations › SMS: provider `http`) |
| `SMS_TWILIO_AUTH_TOKEN` | Twilio instead (then Integrations › SMS: provider `twilio`, Account SID and sender number) |

Save (Ctrl+O, Enter, Ctrl+X) and apply:

```bash
cd /opt/oqc && docker compose up -d
```

## 7. Backups

Three layers, use all of them:

1. **Nightly database dump** (automatic). Kept for 30 days inside the server. List them:
   `cd /opt/oqc && docker compose exec backup ls -lh /backups`
2. **Hostinger backups.** hPanel › VPS › **Backups & Monitoring**: turn on automatic backups, and take a
   **snapshot** before every upgrade.
3. **A copy off the server**, at least weekly, because 1 and 2 are both at Hostinger. From your own computer:

   ```bash
   scp -r root@<VPS IP>:/var/lib/docker/volumes/oqc_backups/_data ./oqc-backups
   ```

Uploaded files (invoices, receipts, payslips, certificates, recordings) live in the Docker volume
`oqc_storage`; Hostinger backups and snapshots include it.

Restoring a dump is described at the top of `deploy/backup.sh`.

## 8. Upgrading to a new release

Take a Hostinger snapshot first, then:

```bash
/opt/oqc/upgrade.sh
```

It saves a database dump, downloads the new code, rebuilds, runs the database migrations, adds any new
roles and permissions, and tests that the site answers. The rollback steps are at the top of `upgrade.sh`.

## 9. Bringing the old Render data across

The Render database is **suspended**. Its data can only be copied if it is upgraded to a paid plan first
(Render deletes it about 12 days after it expired). If the old data matters:

1. On Render, upgrade **oqc-db** to a paid instance and wait until it shows *Available*.
2. Copy its **External Database URL** (Render › oqc-db › Connections).
3. On the VPS, after step 4 above:

   ```bash
   cd /opt/oqc
   docker compose stop app
   docker compose exec -T db pg_dump "<External Database URL>" --format=custom --no-owner > render.dump
   docker compose exec -T db pg_restore -U oqc -d oqc --clean --if-exists --no-owner < render.dump
   docker compose start app
   ```

4. Sign in with the owner e-mail used on Render (or `admin@oqc.local` if it was never changed) and the
   password the installer printed (the VPS passwords in `.env` are applied
   again when the app starts on the restored data). Staff accounts you created yourself keep their Render
   passwords. When everything is there, delete the Render database and service.

If the Render data was only demo data, skip this and let it expire.

## 10. When something goes wrong

| Problem | What to do |
|---|---|
| The browser says the site is not secure / no HTTPS | The domain does not point at the VPS yet (step 2), or port 80/443 is closed (step 3). Check `docker compose logs caddy`. |
| "Ports 80/443 are already in use" | The VPS has a control panel or another web server. Reinstall with plain Ubuntu 24.04 (step 1). |
| The site does not open at all | `cd /opt/oqc && docker compose ps` (every line should say *running* or *healthy*), then `docker compose logs --tail=100 app`. |
| Forgot the admin password | Change `ADMIN_PASSWORD` in `/opt/oqc/.env`, then `docker compose up -d`. It is applied at start and must be changed at first sign-in. |
| The server is slow | hPanel › VPS › Overview shows CPU and memory. Move up to KVM 2. |

Useful commands, run in `/opt/oqc`:

```bash
docker compose ps                 # what is running
docker compose logs -f app        # live app log (Ctrl+C to stop)
docker compose restart app        # restart the app
docker compose down && docker compose up -d   # restart everything (data is kept)
```

## Security notes

- `/opt/oqc/.env` holds every secret; it is readable by root only. Never copy it into e-mail or chat.
- Prefer SSH keys and switch off password login for root once your key works.
- The database is not reachable from the internet; only ports 80 and 443 are open to the web.
- A seeded demo account can never come back with its published password: `deploy_secure.py` replaces it at
  every start.
