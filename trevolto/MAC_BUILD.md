# Building the macOS app (Trevolto)

The macOS build runs the **same code** as Windows — platform differences are
handled with `sys.platform` branches (storage location, run-at-login,
notifications, sound, single-instance focus). This guide covers building the
`.app` on a Mac.

> You must build **on a Mac** — PyInstaller can't cross-compile a macOS app from
> Windows/Linux.

## 1. Prerequisites

- **Python 3.10+ from [python.org](https://www.python.org/downloads/macos/)**
  (or Homebrew `python-tk`). Do **not** use Apple's `/usr/bin/python3` — it ships
  an old Tk 8.5 that renders the UI poorly and has bugs. You need **Tk 8.6**.
  Verify:
  ```bash
  python3 -c "import tkinter; print(tkinter.TkVersion)"   # want 8.6
  ```
- Xcode Command Line Tools (`xcode-select --install`) for `hdiutil` / build tools.

## 2. Build

```bash
cd trading_bot
chmod +x build_mac.sh
./build_mac.sh
```

This installs deps (incl. PyObjC for the menu-bar tray), builds `icon.icns`,
runs PyInstaller against `trading_bot_mac.spec`, and produces:

- `dist/Trevolto.app`
- `dist/Trevolto.dmg` (if `hdiutil` is available)

## 3. First launch (unsigned build / Gatekeeper)

This build is **not code-signed or notarized**, so macOS Gatekeeper will warn
the first time. Tell customers:

1. **Right-click** (or Control-click) the app → **Open**.
2. In the dialog, click **Open** again.

After that first time it opens normally. (Alternatively:
System Settings → Privacy & Security → "Open Anyway".)

> To remove the warning entirely later, sign with an Apple **Developer ID**
> certificate and notarize:
> ```bash
> codesign --deep --force --options runtime \
>   --sign "Developer ID Application: YOUR NAME (TEAMID)" \
>   "dist/Trevolto.app"
> xcrun notarytool submit dist/Trevolto.dmg \
>   --apple-id you@example.com --team-id TEAMID --password APP_SPECIFIC_PW --wait
> xcrun stapler staple "dist/Trevolto.app"
> ```

## 4. Architecture (Apple Silicon vs Intel)

The CI workflow builds on **`macos-13` = Intel (x86_64)** on purpose: an Intel
build runs **natively on Intel Macs** and **on Apple Silicon via Rosetta 2**, so
a single `.dmg` covers every Mac. The bundle's minimum is **macOS 10.13**.
(If you build locally on an Apple Silicon Mac you'll get an arm64 app that will
*not* run on Intel Macs — use the CI `.dmg`, or build on an Intel Mac, for the
widest compatibility. A native arm64 build is a future optimisation if you want
maximum Apple-Silicon performance.)

> **Very old macOS (10.14 Mojave and earlier):** Apple no longer supports these,
> and even an Intel build may refuse to launch depending on the Python/Tk
> toolchain. macOS **11 (Big Sur) or later is recommended**; 10.13–10.15 may
> work but isn't guaranteed.

## 5. What's macOS-native in this build

- **Storage:** `~/Library/Application Support/TradingBot` (keys, settings, logs).
- **Run at login:** a per-user **LaunchAgent** (`~/Library/LaunchAgents/tech.trevolto.bot.plist`).
- **Notifications:** native banners via `osascript` (allow them in
  System Settings → Notifications); alert beep via the system sound.
- **Tray:** the menu-bar extra (pystray + PyObjC) for "minimize to tray".
- **Single instance:** one copy runs at a time; a second launch activates the
  running one.
- **Updates:** in-app auto-update is Windows-only; on macOS "Check for updates"
  points to the download page (re-download the `.dmg`).
