# Building the macOS app (Prometheus AI Crypto Bot)

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

- `dist/Prometheus AI Crypto Bot.app`
- `dist/PrometheusAICryptoBot.dmg` (if `hdiutil` is available)

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
>   "dist/Prometheus AI Crypto Bot.app"
> xcrun notarytool submit dist/PrometheusAICryptoBot.dmg \
>   --apple-id you@example.com --team-id TEAMID --password APP_SPECIFIC_PW --wait
> xcrun stapler staple "dist/Prometheus AI Crypto Bot.app"
> ```

## 4. Architecture (Apple Silicon vs Intel)

PyInstaller builds for the Mac it runs on (arm64 on Apple Silicon, x86_64 on
Intel). For a single app that runs on both, build on Apple Silicon with a
`universal2` Python, or ship two builds. Most users on modern Macs are arm64.

## 5. What's macOS-native in this build

- **Storage:** `~/Library/Application Support/TradingBot` (keys, settings, logs).
- **Run at login:** a per-user **LaunchAgent** (`~/Library/LaunchAgents/tech.prometheusai.bot.plist`).
- **Notifications:** native banners via `osascript` (allow them in
  System Settings → Notifications); alert beep via the system sound.
- **Tray:** the menu-bar extra (pystray + PyObjC) for "minimize to tray".
- **Single instance:** one copy runs at a time; a second launch activates the
  running one.
- **Updates:** in-app auto-update is Windows-only; on macOS "Check for updates"
  points to the download page (re-download the `.dmg`).
