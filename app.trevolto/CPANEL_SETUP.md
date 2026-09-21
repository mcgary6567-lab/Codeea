# Trevolto — cPanel / Shared Hosting Setup (no VPS, no SSH)

Any cPanel host with **"Setup Python App"** (Hostinger shared, GoDaddy cPanel,
Namecheap, etc.) can run Trevolto through Passenger.

> ⚠️ Passenger does **not** proxy WebSockets, so the dashboard automatically
> falls back to **3-second polling** — everything works, just not sub-second
> live. For true real-time updates, use a VPS with `install.sh` instead.

---

## 1. Upload the app
cPanel → **File Manager** → your **home** folder (keep it *outside* `public_html`).
Upload this app's zip and **Extract** it, e.g. `/home/USER/trevolto`.

## 2. Create the Python app
cPanel → **Setup Python App** → **Create Application**:

| Field | Value |
|---|---|
| Python version | 3.11 (or the highest available) |
| Application root | `trevolto` (the extracted folder) |
| Application URL | your domain or subdomain |
| Application startup file | `passenger_wsgi.py` |
| Application Entry point | `application` |

Click **Create**.

## 3. Install dependencies
On the app's page, under **Configuration files** add `requirements.txt` and click
**Run Pip Install** — or copy the shown `source .../activate` command into cPanel
**Terminal** and run `pip install -r requirements.txt`.

## 4. Environment variables
Same page → **Environment variables** → add:

```
TREVOLTO_SECRET_KEY   = <a long random string, 40+ chars>
TREVOLTO_PUBLIC_URL   = https://yourdomain.com
TREVOLTO_DATA_DIR     = /home/USER/trevolto/data
TREVOLTO_ADMIN_EMAIL  = you@email.com
```

Generate a secret on any PC: `python -c "import secrets;print(secrets.token_urlsafe(48))"`

## 5. Restart & open
Click **Restart**, then open your domain. **The first account you register becomes the admin.**

---

**Update later:** re-upload changed files in File Manager → click **Restart**.
**Prefer real-time + easiest setup?** Use a VPS: `sudo bash install.sh` (see `Trevolto_Guide.pdf`).
