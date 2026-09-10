#!/usr/bin/env bash
# Compile Tailwind into a static stylesheet. Run after changing templates or the theme.
#   ./build/build-css.sh
set -e
cd "$(dirname "$0")/.."
BIN="build/tailwindcss.exe"
[ -x "$BIN" ] || BIN="build/tailwindcss"
"$BIN" -c tailwind.config.js -i build/input.css -o app/static/css/tailwind.css --minify
echo "Built app/static/css/tailwind.css"
