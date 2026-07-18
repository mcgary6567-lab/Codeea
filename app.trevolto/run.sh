#!/usr/bin/env bash
# Trevolto Web launcher (dev). For production see README (uvicorn workers + TLS).
set -e
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements.txt
export TREVOLTO_SECRET_KEY="${TREVOLTO_SECRET_KEY:-$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')}"
export TREVOLTO_PUBLIC_URL="${TREVOLTO_PUBLIC_URL:-http://localhost:8000}"
echo "Starting Trevolto Web on http://localhost:8000"
exec python3 -m uvicorn server.main:app --host 0.0.0.0 --port 8000
