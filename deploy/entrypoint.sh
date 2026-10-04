#!/usr/bin/env bash
# Container entrypoint. Mirrors render-build.sh: migrate, seed once, rotate passwords when they change, serve.
#   SEED_MODE=core|demo        what to seed on first start (default core)
#   SEED_ON_START=auto|true    auto = seed only when the users table is empty (default)
#   ROTATE_CREDENTIALS=true    force deploy_secure.py to re-apply ADMIN_PASSWORD/DEMO_PASSWORD/ADMIN_EMAIL
#                              (it already re-applies on its own whenever those values change)
#   SKIP_BOOTSTRAP=true        skip migrate/seed (second container sharing the database, e.g. jobs)
#   WEB_CONCURRENCY=1          uvicorn workers; keep 1 unless SCHEDULER_ENABLED=false (see docs/DEPLOYMENT.md)
set -euo pipefail
cd /app
export PORT="${PORT:-8000}"

# Started as root (the default): make the mounted volume writable by oqc, then drop privileges.
if [ "$(id -u)" = "0" ]; then
  chown oqc:oqc /app/storage /app/storage/* /app/data 2>/dev/null || true
  exec gosu oqc "$0" "$@"
fi

if [ "${SKIP_BOOTSTRAP:-false}" != "true" ]; then
  echo "[entrypoint] waiting for the database"
  python deploy/db_ready.py wait

  echo "[entrypoint] alembic upgrade head"
  python -m alembic upgrade head

  seed_now=false
  if [ "${SEED_ON_START:-auto}" = "true" ]; then
    seed_now=true
  elif [ "${SEED_ON_START:-auto}" = "auto" ] && python deploy/db_ready.py empty; then
    seed_now=true
  fi

  if [ "$seed_now" = "true" ]; then
    echo "[entrypoint] first start: seeding (SEED_MODE=${SEED_MODE:-core})"
    if [ "${SEED_MODE:-core}" = "demo" ]; then python seed.py; else python seed.py --core; fi
  fi

  # Applies ADMIN_PASSWORD / DEMO_PASSWORD / ADMIN_EMAIL only when they differ from the last applied
  # set (fingerprint stored in the settings table), so a restart never resets the owner's password.
  echo "[entrypoint] deploy_secure"
  python deploy_secure.py
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec uvicorn app.main:app \
  --host 0.0.0.0 --port "$PORT" \
  --workers "${WEB_CONCURRENCY:-1}" \
  --proxy-headers --forwarded-allow-ips='*' \
  --timeout-keep-alive 30
