#!/usr/bin/env bash
# PumpAI one-command installer for Ubuntu 22.04 / 24.04 (or Debian 12) — a fresh VPS or the pump's own PC.
#
#   sudo bash install.sh --domain pump.example.pk --email owner@example.pk
#
# Options
#   --domain NAME        web address for this pump (DNS A record must point to this server). Leave out to use the IP (no HTTPS).
#   --email ADDRESS      for the free SSL certificate (Let's Encrypt) expiry notices
#   --repo URL           git repository to install from (default: the folder this script is in)
#   --branch NAME        git branch (default: main)
#   --vendor-name TEXT   your company name, shown to the pump owner as support (Help → About)
#   --vendor-phone TEXT  your support WhatsApp number
#   --vendor-email TEXT  your support email
#   --port N             internal port (default 4000)
#   --no-ssl             do not ask Let's Encrypt for a certificate
#   --demo               load the demo pump instead of the setup wizard (for showing customers)
set -euo pipefail

DOMAIN="" EMAIL="" REPO="" BRANCH="main" VENDOR_NAME="" VENDOR_PHONE="" VENDOR_EMAIL="" PORT=4000 SSL=1 DEMO=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --email) EMAIL="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --vendor-name) VENDOR_NAME="$2"; shift 2 ;;
    --vendor-phone) VENDOR_PHONE="$2"; shift 2 ;;
    --vendor-email) VENDOR_EMAIL="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --no-ssl) SSL=0; shift ;;
    --demo) DEMO=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)"; exit 1 ;;
  esac
done

APP_DIR=/opt/pumpai
DATA_DIR=/var/lib/pumpai
ENV_FILE=/etc/pumpai.env
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash install.sh ..."
command -v apt-get >/dev/null || die "This installer supports Ubuntu / Debian. For other systems use Docker (see INSTALL.md)."

say "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates git nginx sqlite3 openssl rsync ufw >/dev/null
if [[ $SSL -eq 1 && -n "$DOMAIN" ]]; then apt-get install -y -qq certbot python3-certbot-nginx >/dev/null; fi

if ! command -v node >/dev/null || [[ "$(node -p 'process.versions.node.split(".")[0]')" -lt 22 ]]; then
  say "Installing Node.js 22"
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null
  apt-get install -y -qq nodejs >/dev/null
fi

say "Getting PumpAI into $APP_DIR"
id pumpai >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin pumpai
mkdir -p "$APP_DIR" "$DATA_DIR/backups"
if [[ -n "$REPO" ]]; then
  if [[ -d "$APP_DIR/.git" ]]; then git -C "$APP_DIR" fetch -q origin "$BRANCH" && git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
  else rm -rf "$APP_DIR" && git clone -q --branch "$BRANCH" "$REPO" "$APP_DIR"; fi
elif [[ -f "$SRC_DIR/package.json" && -d "$SRC_DIR/server" ]]; then
  [[ "$SRC_DIR" == "$APP_DIR" ]] || rsync -a --delete --exclude node_modules --exclude 'server/data' --exclude .env "$SRC_DIR/" "$APP_DIR/"
else
  die "Run this script from the PumpAI folder, or give --repo URL"
fi

say "Building (this takes a few minutes)"
cd "$APP_DIR"
npm ci --no-audit --no-fund --loglevel=error
npm run build --silent

if [[ ! -f "$ENV_FILE" ]]; then
  say "Creating settings file $ENV_FILE"
  HOST="${DOMAIN:-$(hostname -I | awk '{print $1}')}"
  SCHEME=$([[ -n "$DOMAIN" && $SSL -eq 1 ]] && echo https || echo http)
  cat > "$ENV_FILE" <<ENV
NODE_ENV=production
PORT=$PORT
DB_PATH=$DATA_DIR/pumpai.db
BACKUP_DIR=$DATA_DIR/backups
WEB_DIST=$APP_DIR/web/dist
PUBLIC_URL=$SCHEME://$HOST
JWT_SECRET=$(openssl rand -hex 32)
SETUP_TOKEN=$(openssl rand -hex 4 | tr a-f A-F)
DEMO_DATA=$DEMO
SUPERVISED=1
TZ_NAME=Asia/Karachi
VENDOR_NAME=$VENDOR_NAME
VENDOR_PHONE=$VENDOR_PHONE
VENDOR_EMAIL=$VENDOR_EMAIL
# Claude AI and WhatsApp keys can be entered later in the app: Settings → Integrations
ENV
  chmod 600 "$ENV_FILE"
fi
chown -R pumpai:pumpai "$DATA_DIR"

say "Starting the PumpAI service"
cat > /etc/systemd/system/pumpai.service <<UNIT
[Unit]
Description=PumpAI petrol pump management
After=network-online.target
Wants=network-online.target

[Service]
User=pumpai
Group=pumpai
WorkingDirectory=$APP_DIR/server
EnvironmentFile=$ENV_FILE
ExecStart=$(command -v node) --disable-warning=ExperimentalWarning dist/index.js
Restart=always
RestartSec=3
# the app closes the database cleanly on stop
TimeoutStopSec=30
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now pumpai >/dev/null
systemctl restart pumpai

say "Setting up the web server (nginx)"
SERVER_NAME="${DOMAIN:-_}"
cat > /etc/nginx/sites-available/pumpai <<NGINX
server {
    listen 80;
    server_name $SERVER_NAME;
    client_max_body_size 12m;
    server_tokens off;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy same-origin always;
    # links carry short read-only tokens; keep them out of the access log anyway
    access_log /var/log/nginx/pumpai.access.log pumpai;
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;           # live inbox (server-sent events)
        proxy_read_timeout 3600s;
    }
}
NGINX
# log requests without their query string (no tokens in log files)
cat > /etc/nginx/conf.d/pumpai-log.conf <<'NGLOG'
log_format pumpai '$remote_addr - [$time_local] "$request_method $uri" $status $body_bytes_sent';
NGLOG
ln -sf /etc/nginx/sites-available/pumpai /etc/nginx/sites-enabled/pumpai
rm -f /etc/nginx/sites-enabled/default
nginx -t -q && systemctl reload nginx

if command -v ufw >/dev/null; then
  # keep the SSH port this server really uses open (not only 22), or the firewall could lock you out
  SSH_PORT=$(sshd -T 2>/dev/null | awk '/^port /{print $2; exit}'); SSH_PORT=${SSH_PORT:-22}
  ufw allow "$SSH_PORT"/tcp >/dev/null; ufw allow 'Nginx Full' >/dev/null; ufw --force enable >/dev/null || true
fi

if [[ -n "$DOMAIN" && $SSL -eq 1 ]]; then
  say "Getting a free SSL certificate for $DOMAIN"
  if [[ -n "$EMAIL" ]]; then MAIL=(-m "$EMAIL"); else MAIL=(--register-unsafely-without-email); fi
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect "${MAIL[@]}" \
    || echo "  SSL failed — check that $DOMAIN points to this server, then run: certbot --nginx -d $DOMAIN"
fi

if [[ -z "$DOMAIN" || $SSL -ne 1 ]]; then
  echo
  echo "  ⚠️  No domain / SSL: sign-in and passwords travel UNENCRYPTED over plain http."
  echo "      Fine for a quick test on the pump's own network — for real use run again with --domain your.domain --email you@mail"
  echo
fi
say "Checking that it is running"
for i in $(seq 1 30); do curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null && break; sleep 1; done
curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null || die "PumpAI did not start. See: journalctl -u pumpai -n 50"

# shellcheck disable=SC1090
source "$ENV_FILE"
cat <<DONE

  ┌──────────────────────────────────────────────────────────┐
  │  PumpAI is installed                                     │
  └──────────────────────────────────────────────────────────┘
   Open:        $PUBLIC_URL
   Setup code:  $SETUP_TOKEN   (the setup wizard asks for it once)

   Settings file:  $ENV_FILE
   Database:       $DATA_DIR/pumpai.db   (backups every night in $DATA_DIR/backups)
   Update later:   sudo bash $APP_DIR/deploy/update.sh
   Logs:           journalctl -u pumpai -f

DONE
