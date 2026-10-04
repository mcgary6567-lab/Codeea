#!/bin/sh
# Daily pg_dump into the "backups" volume, from the postgres:16 image so pg_dump matches the server.
# The app's own /admin/backups page only writes a JSON export on PostgreSQL; this is the restorable copy.
#
# Restore (stop the app first):
#   docker compose cp backup:/backups/oqc-<stamp>.dump .
#   docker compose exec -T db pg_restore -U oqc -d oqc --clean --if-exists --no-owner < oqc-<stamp>.dump
set -eu
: "${BACKUP_KEEP_DAYS:=30}"
while true; do
  stamp=$(date +%Y%m%d-%H%M%S)
  if pg_dump -h db -U oqc -d oqc --format=custom --no-owner -f "/backups/oqc-$stamp.dump.part"; then
    mv "/backups/oqc-$stamp.dump.part" "/backups/oqc-$stamp.dump"
    echo "backup oqc-$stamp.dump ok"
  else
    echo "backup FAILED at $stamp" >&2
    rm -f "/backups/oqc-$stamp.dump.part"
  fi
  find /backups -name 'oqc-*.dump' -mtime +"$BACKUP_KEEP_DAYS" -delete
  sleep 86400
done
