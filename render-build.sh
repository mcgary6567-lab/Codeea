#!/usr/bin/env bash
# Render build step: install dependencies, apply migrations, load seed data.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

# Version-controlled schema. Safe to re-run on every deploy.
python -m alembic upgrade head

# Bootstrap data. Idempotent, so redeploys will not duplicate anything.
#   SEED_MODE=core  -> roles, departments, currencies, settings, admin only
#   SEED_MODE=demo  -> the above plus the full demo dataset
if [ "${SEED_MODE:-core}" = "demo" ]; then
  python seed.py
else
  python seed.py --core
fi

# Replace the shipped development passwords with the generated ones.
python deploy_secure.py
