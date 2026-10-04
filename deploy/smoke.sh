#!/usr/bin/env bash
# Smoke test: health (with a database round-trip), the versioned stylesheet actually served, storage guard in place.
#   deploy/smoke.sh https://os.example.com      (default http://127.0.0.1:8000)
set -euo pipefail
URL="${1:-http://127.0.0.1:8000}"; URL="${URL%/}"
cd "$(dirname "$(readlink -f "$0")")/.."

echo "health:"
curl -fsS "$URL/health?db=1" | tee /dev/stderr | grep -q '"status":"ok"'; echo

V="$(curl -fsS "$URL/login" | grep -o 'tailwind.css?v=[0-9a-f]*' | head -1)"
[ -n "$V" ] || { echo "FAIL: /login carries no versioned stylesheet link"; exit 1; }
echo "stylesheet link: /static/css/$V"
curl -fsSI "$URL/static/css/$V" | grep -qi '^content-type: text/css' || { echo "FAIL: stylesheet not served as text/css"; exit 1; }

REMOTE="$(curl -fsS "$URL/static/css/$V" | md5sum | cut -d' ' -f1)"
LOCAL="$(md5sum app/static/css/tailwind.css | cut -d' ' -f1)"
if [ "$REMOTE" = "$LOCAL" ]; then echo "stylesheet matches the local build ($LOCAL)"; else echo "WARNING: deployed stylesheet $REMOTE != local $LOCAL (old release still live?)"; fi

CODE="$(curl -s -o /dev/null -w '%{http_code}' "$URL/storage/agent_screenshots/probe.png")"
[ "$CODE" = "404" ] && echo "agent screenshot guard: 404 ok" || { echo "FAIL: /storage/agent_screenshots/ answered $CODE"; exit 1; }
echo "smoke ok"
