#!/usr/bin/env bash
# Build the macOS .app (and a .dmg) for the Prometheus AI Crypto Bot.
#
# Run on a Mac (see MAC_BUILD.md for prerequisites — Tk 8.6 Python, etc.):
#     ./build_mac.sh
#
# Produces:
#     dist/Prometheus AI Crypto Bot.app
#     dist/PrometheusAICryptoBot.dmg   (if hdiutil is available)
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing dependencies"
python3 -m pip install -r requirements-mac.txt pyinstaller

echo "==> Fetching brand assets + building icon.icns (best-effort)"
python3 fetch_logo.py || true
python3 make_icns.py  || true

echo "==> Building the .app with PyInstaller"
rm -rf build "dist/Prometheus AI Crypto Bot.app"
pyinstaller --noconfirm trading_bot_mac.spec

APP="dist/Prometheus AI Crypto Bot.app"
if [ ! -d "$APP" ]; then
  echo "Build failed: $APP not found" >&2
  exit 1
fi

echo "==> Packaging a .dmg"
if command -v hdiutil >/dev/null 2>&1; then
  hdiutil create -volname "Prometheus AI Crypto Bot" \
    -srcfolder "$APP" -ov -format UDZO "dist/PrometheusAICryptoBot.dmg"
  echo "    dist/PrometheusAICryptoBot.dmg"
else
  echo "    hdiutil not found — skipping .dmg (ship the .app zipped instead)"
fi

echo "==> Done. App: $APP"
echo "    Unsigned build: first launch = right-click the app -> Open -> Open."
