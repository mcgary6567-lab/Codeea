#!/usr/bin/env bash
# Online Quran College - Digital Operating System (Linux / macOS launcher)
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

[ -f .env ] || cp .env.example .env

if [ ! -f data/oqc.db ]; then
  echo "Creating database and loading demo data..."
  .venv/bin/python seed.py
fi

cat <<'BANNER'
============================================================
  Online Quran College - Digital Operating System
  http://localhost:8000   or   http://127.0.0.1:8000
  Sign in: admin@oqc.local  /  Admin@12345
============================================================
BANNER
.venv/bin/python run.py
