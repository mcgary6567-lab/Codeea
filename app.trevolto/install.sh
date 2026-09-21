#!/usr/bin/env bash
# =====================================================================
#  Trevolto — one-click installer for a fresh Ubuntu/Debian VPS
#  Works on Hostinger VPS, GoDaddy VPS, DigitalOcean, Contabo, etc.
#  USAGE:   sudo bash install.sh
#  Re-runnable: keeps your existing secret key & database.
# =====================================================================
set -euo pipefail
BRAND="Trevolto"; PREFIX="TREVOLTO"; SLUG="trevolto"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" = "0" ] || { echo "Please run as root:  sudo bash install.sh"; exit 1; }
echo "==> $BRAND one-click installer"
read -rp "Domain (e.g. app.yourdomain.com) [blank = use server IP]: " DOMAIN
read -rp "Admin email (becomes admin on first signup): " ADMIN_EMAIL

echo "==> Installing system packages..."
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx curl

echo "==> Creating Python environment..."
cd "$APP_DIR"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip -q
./.venv/bin/pip install -r requirements.txt -q

echo "==> Writing configuration..."
ENV_FILE="/etc/$SLUG.env"
IP="$(curl -fsS ifconfig.me 2>/dev/null || echo YOUR_SERVER_IP)"
if [ ! -f "$ENV_FILE" ] || ! grep -q "${PREFIX}_SECRET_KEY" "$ENV_FILE"; then
  SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
  if [ -n "$DOMAIN" ]; then PUBURL="https://$DOMAIN"; else PUBURL="http://$IP"; fi
  cat > "$ENV_FILE" <<CFG
${PREFIX}_SECRET_KEY=$SECRET
${PREFIX}_PUBLIC_URL=$PUBURL
${PREFIX}_DATA_DIR=$APP_DIR/data
${PREFIX}_ADMIN_EMAIL=$ADMIN_EMAIL
CFG
  chmod 600 "$ENV_FILE"
  echo "   -> $ENV_FILE created (secret generated & saved)"
else
  echo "   -> $ENV_FILE already exists — keeping your secret & settings"
fi

echo "==> Installing systemd service (auto-start on boot, auto-restart)..."
cat > /etc/systemd/system/$SLUG.service <<UNIT
[Unit]
Description=$BRAND trading bot
After=network.target
[Service]
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/uvicorn server.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now $SLUG
sleep 2
systemctl is-active --quiet $SLUG && echo "   -> service running" || echo "   -> WARNING: not running (check: journalctl -u $SLUG)"

echo "==> Configuring Nginx reverse proxy (with WebSocket support)..."
cat > /etc/nginx/sites-available/$SLUG <<'NGINX'
server {
  listen 80;
  server_name __SERVER_NAME__;
  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_read_timeout 120s;
  }
}
NGINX
sed -i "s/__SERVER_NAME__/${DOMAIN:-_}/" /etc/nginx/sites-available/$SLUG
ln -sf /etc/nginx/sites-available/$SLUG /etc/nginx/sites-enabled/$SLUG
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

if [ -n "$DOMAIN" ]; then
  echo "==> Getting free HTTPS certificate (Let's Encrypt)..."
  apt-get install -y certbot python3-certbot-nginx
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$ADMIN_EMAIL" --redirect \
    || echo "   -> SSL skipped. Point $DOMAIN's A-record to $IP, then run: certbot --nginx -d $DOMAIN"
fi

echo ""
echo "========================================================================"
echo " DONE — $BRAND is installed and running."
if [ -n "$DOMAIN" ]; then echo "   Open:   https://$DOMAIN"; else echo "   Open:   http://$IP"; fi
echo "   The FIRST account you register becomes the admin."
echo "   Manage: systemctl status|restart|stop $SLUG"
echo "   Update: upload new files (or git pull), then: systemctl restart $SLUG"
echo "========================================================================"
