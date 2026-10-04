#!/usr/bin/env bash
# Upgrade an installation in place: dump the database, pull, rebuild, restart (the entrypoint runs
# alembic upgrade head), smoke-test. Works for the Docker install (/opt/oqc) and for the bare
# systemd install described in docs/DEPLOYMENT.md (/opt/oqc/app with .venv).
#
# Rollback (Docker):  git checkout $(cat .last-good-sha) && docker compose up -d --build app
#   if the release carried a migration, first: docker compose run --rm app python -m alembic downgrade -1
#   or restore the pre-upgrade dump (see deploy/backup.sh).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
STAMP="$(date +%Y%m%d-%H%M)"
git rev-parse HEAD > .last-good-sha
echo "rollback point: $(cat .last-good-sha)"

if [ -f docker-compose.yml ] && command -v docker >/dev/null 2>&1; then
  echo "==> pre-upgrade dump -> backups volume"
  docker compose run --rm --no-deps --entrypoint sh backup -c \
    "pg_dump -h db -U oqc -d oqc --format=custom --no-owner -f /backups/pre-upgrade-$STAMP.dump" || echo "WARNING: dump failed, continuing"
  echo "==> pull"
  git pull --ff-only
  echo "==> build + restart (entrypoint migrates)"
  GIT_COMMIT="$(git rev-parse --short HEAD)" docker compose build app
  docker compose up -d app
  for _ in $(seq 1 60); do
    docker compose exec -T app curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 && break
    sleep 5
  done
  docker image prune -f >/dev/null
  URL="$(grep -E '^BASE_URL=' .env | cut -d= -f2-)"
else
  echo "==> pre-upgrade dump"
  set -a; . ./.env; set +a
  mkdir -p storage/backups
  # pg_dump does not understand the "+psycopg" driver suffix the app uses in DATABASE_URL.
  PG_URL="postgresql://${DATABASE_URL#postgresql+psycopg://}"
  pg_dump --format=custom --no-owner "$PG_URL" > "storage/backups/pre-upgrade-$STAMP.dump" || echo "WARNING: dump failed, continuing"
  echo "==> pull + install"
  git pull --ff-only
  .venv/bin/pip install -q -r requirements.txt
  echo "==> migrate + bootstrap data"
  .venv/bin/python -m alembic upgrade head
  .venv/bin/python seed.py --core
  .venv/bin/python deploy_secure.py
  .venv/bin/python -c "from app.main import app; print('import ok')"
  sudo systemctl restart oqc
  URL="${BASE_URL:-http://127.0.0.1:8000}"
fi

echo "==> smoke test"
./deploy/smoke.sh "$URL"
echo "upgrade complete: $(git rev-parse --short HEAD)"
