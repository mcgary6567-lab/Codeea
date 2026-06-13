#!/usr/bin/env bash
# Build the macOS .app (and a .dmg) for the Trevolto.
#
# Run on a Mac (see MAC_BUILD.md for prerequisites — Tk 8.6 Python, etc.):
#     ./build_mac.sh
#
# Produces:
#     dist/Trevolto.app
#     dist/Trevolto.dmg   (if hdiutil is available)
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing dependencies"
python3 -m pip install -r requirements-mac.txt pyinstaller

echo "==> Fetching brand assets + building icon.icns (best-effort)"
python3 fetch_logo.py || true
python3 make_icns.py  || true

echo "==> Building the .app with PyInstaller"
rm -rf build "dist/Trevolto.app"
pyinstaller --noconfirm trading_bot_mac.spec

APP="dist/Trevolto.app"
if [ ! -d "$APP" ]; then
  echo "Build failed: $APP not found" >&2
  exit 1
fi

echo "==> Packaging a .dmg"
if command -v hdiutil >/dev/null 2>&1; then
  hdiutil create -volname "Trevolto" \
    -srcfolder "$APP" -ov -format UDZO "dist/Trevolto.dmg"
  echo "    dist/Trevolto.dmg"
else
  echo "    hdiutil not found — skipping .dmg (ship the .app zipped instead)"
fi

echo "==> Done. App: $APP"
echo "    Unsigned build: first launch = right-click the app -> Open -> Open."
