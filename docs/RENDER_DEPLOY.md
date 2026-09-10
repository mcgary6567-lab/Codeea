# Deploying to Render

Everything Render needs is already in the repository. You push the code to your own GitHub account,
point Render at it once, and it builds itself.

Estimated time: about 10 minutes, most of it waiting for the first build.

---

## What is already prepared

| File | Purpose |
|---|---|
| `render.yaml` | Blueprint defining the web service **and** a PostgreSQL database, with all environment variables |
| `render-build.sh` | Build step: installs dependencies, runs migrations, loads seed data, rotates passwords |
| `runtime.txt` | Pins Python 3.12.7 (the PostgreSQL driver has no 3.14 wheels yet) |
| `deploy_secure.py` | Replaces the development passwords with the values Render generates |
| `migrations/` | Alembic baseline so the schema is created and versioned, not guessed |

The front-end libraries (Tailwind, Alpine, Chart.js, HTMX, Lucide) are committed under
`app/static/vendor/`, so the live site does not depend on any CDN being reachable.

---

## Step 1 — Put the code on GitHub

The repository is already initialised and committed locally on branch `main`.

Create an **empty** repository at https://github.com/new — no README, no .gitignore, no licence.
Call it `online-quran-college`. Private is fine; Render can read private repos once you authorise it.

Then, from the project folder:

```bash
git remote add origin https://github.com/YOUR-USERNAME/online-quran-college.git
git push -u origin main
```

If GitHub asks for a password, use a Personal Access Token (Settings → Developer settings →
Personal access tokens → Fine-grained token, with Contents: Read and write), not your account password.

## Step 2 — Create the Blueprint on Render

1. Go to https://dashboard.render.com
2. **New** → **Blueprint**
3. Connect your GitHub account if prompted, then pick `online-quran-college`
4. Render reads `render.yaml` and shows two resources: the web service `oqc` and the database `oqc-db`
5. Click **Apply**

Render will provision PostgreSQL, install dependencies, run the migrations, load the demo data,
and rotate the passwords. The first build takes roughly 5 to 8 minutes.

## Step 3 — Collect your login details

Render generates the passwords, so read them from the dashboard after the build:

**Service `oqc` → Environment**

| Variable | What it is |
|---|---|
| `ADMIN_PASSWORD` | Password for `admin@oqc.local`, the CEO / super admin account |
| `DEMO_PASSWORD` | Shared password for every other demo account (teacher1@, parent1@, student1@, and the rest) |

Your site is at `https://oqc.onrender.com` (Render shows the exact URL at the top of the service page).

Sign in as `admin@oqc.local` with the generated `ADMIN_PASSWORD`. You will be asked to set your own
password on first sign-in.

---

## Things worth knowing before you show a client

**The free plan sleeps.** A free web service spins down after about 15 minutes of inactivity, and the
next visit takes roughly 30 to 50 seconds to wake. Before a client demo, open the site a minute early,
or upgrade the service to **Starter** so it stays warm. The free PostgreSQL database also expires
after 30 days, so move to a paid database before storing anything you care about.

**Generated files do not survive a deploy.** Render gives each deploy a fresh filesystem, so invoices,
receipts, payslips, certificates and result cards written to `storage/` disappear on redeploy. They
regenerate on demand, which is fine for a demo. For production, attach a Render Disk to the service
and point `storage/` at it, or move file storage to S3-compatible object storage.

**Only one worker.** The start command runs a single worker on purpose. The app schedules its own
background jobs in-process, so a second worker would send every reminder twice, generate invoices
twice, and run backups twice. To scale beyond one worker, move the scheduler into a separate Render
background worker first.

**Integrations stay simulated.** WhatsApp, GoHighLevel, email and the AI provider log their calls
instead of making them until you add credentials. Add them in the service's Environment tab
(the names are commented at the bottom of `render.yaml`); no code change is needed.

**Switching to a clean install.** The blueprint sets `SEED_MODE=demo`. Change it to `core` in the
Environment tab and redeploy to get a bare install with no demo families, students or invoices.
That does not delete data that already exists, so do it before you start using the site for real.

---

## Redeploying after changes

`autoDeploy` is on, so pushing to `main` rebuilds automatically:

```bash
git add -A
git commit -m "describe your change"
git push
```

If you change a model, generate a migration before pushing, or the deploy will not create the
new columns:

```bash
.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe the change"
# read the generated file under migrations/versions/ before committing it
```

## If a deploy fails

Open the service → **Logs**. The usual causes:

| Symptom | Cause and fix |
|---|---|
| `ModuleNotFoundError: psycopg` | `runtime.txt` was not picked up. Confirm the service is on Python 3.12, not 3.13+ |
| `alembic ... target database is not up to date` | A migration is missing. Generate one locally and push it |
| Build succeeds, site returns 502 | Check the start command still binds `0.0.0.0` and `$PORT` |
| Login rejects the password | Read `ADMIN_PASSWORD` from Environment; the local `Admin@12345` is deliberately not used in production |
