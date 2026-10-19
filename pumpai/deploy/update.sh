#!/usr/bin/env bash
# Update PumpAI to the latest version safely:
#   1) back up the database, 2) build the new version in a separate folder (the running app is untouched),
#   3) swap it in and restart, 4) if the app does not come back healthy, put the old version back.
#   sudo bash /opt/pumpai/deploy/update.sh            (from git, if installed with --repo)
#   sudo bash update.sh --from /path/to/new/pumpai     (from a folder / unzipped release)
set -euo pipefail
APP_DIR=/opt/pumpai ENV_FILE=/etc/pumpai.env FROM=""
[[ "${1:-}" == "--from" ]] && FROM="$2"
[[ $EUID -eq 0 ]] || { echo "Run as root: sudo bash update.sh"; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"
STAMP=$(date +%Y%m%d-%H%M%S)
NEW="$APP_DIR.new" OLD="$APP_DIR.old-$STAMP"
health() { for _ in $(seq 1 40); do curl -fs "http://127.0.0.1:${PORT:-4000}/api/health" >/dev/null && return 0; sleep 1; done; return 1; }

echo "==> Backing up the database to $BACKUP_DIR/pre-update-$STAMP.db"
sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/pre-update-$STAMP.db'"
chown pumpai:pumpai "$BACKUP_DIR/pre-update-$STAMP.db" 2>/dev/null || true

echo "==> Getting the new version"
rm -rf "$NEW"
if [[ -n "$FROM" ]]; then rsync -a --exclude node_modules --exclude .git "$FROM/" "$NEW/"
elif [[ -d "$APP_DIR/.git" ]]; then cp -a "$APP_DIR" "$NEW" && git -C "$NEW" pull -q --ff-only
else echo "Nothing to update from: give --from /path/to/new/pumpai"; exit 1; fi

echo "==> Building (the running app keeps working meanwhile)"
( cd "$NEW" && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent ) \
  || { echo "Build FAILED — nothing was changed. The app is still running the old version."; rm -rf "$NEW"; exit 1; }
chown -R pumpai:pumpai "$NEW"

echo "==> Switching to the new version"
mv "$APP_DIR" "$OLD" && mv "$NEW" "$APP_DIR"
systemctl restart pumpai
if health; then
  echo "==> Updated. The previous version is kept in $OLD (delete it when you are happy)."
  ls -1d "$APP_DIR".old-* 2>/dev/null | head -n -2 | xargs -r rm -rf   # keep the last two old versions
else
  echo "!! The new version did not start — putting the old one back"
  journalctl -u pumpai -n 30 --no-pager || true
  mv "$APP_DIR" "$APP_DIR.failed-$STAMP" && mv "$OLD" "$APP_DIR"
  systemctl restart pumpai
  health && echo "==> Old version is running again. Nothing was lost (database backup: $BACKUP_DIR/pre-update-$STAMP.db)." || echo "!! Still not healthy — see journalctl -u pumpai"
  exit 1
fi
