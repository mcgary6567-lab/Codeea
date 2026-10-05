#!/usr/bin/env bash
# Update PumpAI to the latest version: backs up the database first, rebuilds and restarts.
#   sudo bash /opt/pumpai/deploy/update.sh            (from git, if installed with --repo)
#   sudo bash update.sh --from /path/to/new/pumpai     (from a folder / unzipped release)
set -euo pipefail
APP_DIR=/opt/pumpai ENV_FILE=/etc/pumpai.env FROM=""
[[ "${1:-}" == "--from" ]] && FROM="$2"
[[ $EUID -eq 0 ]] || { echo "Run as root: sudo bash update.sh"; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"
STAMP=$(date +%Y%m%d-%H%M%S)
echo "==> Backing up the database to $BACKUP_DIR/pre-update-$STAMP.db"
sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/pre-update-$STAMP.db'"
cd "$APP_DIR"
if [[ -n "$FROM" ]]; then rsync -a --delete --exclude node_modules --exclude .git "$FROM/" "$APP_DIR/"
elif [[ -d .git ]]; then git pull -q --ff-only
else echo "Nothing to update from: give --from /path/to/new/pumpai"; exit 1; fi
echo "==> Building"
npm ci --no-audit --no-fund --loglevel=error
npm run build --silent
systemctl restart pumpai
for i in $(seq 1 30); do curl -fs "http://127.0.0.1:${PORT:-4000}/api/health" && break; sleep 1; done
echo
echo "==> Updated. If anything is wrong, the database before the update is $BACKUP_DIR/pre-update-$STAMP.db"
