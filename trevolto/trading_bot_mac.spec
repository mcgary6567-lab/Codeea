# PyInstaller spec for the macOS build of the Trevolto.
# Build on a Mac with:  pyinstaller --noconfirm trading_bot_mac.spec
# Output: dist/Trevolto.app  (windowed .app bundle)
#
# Mirrors the Windows spec's analysis (sync-only ccxt to keep the bundle small)
# but produces a macOS .app via BUNDLE. Unsigned: first launch needs a
# right-click -> Open to clear Gatekeeper (see MAC_BUILD.md).

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("ccxt", "certifi", "cryptography"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    if pkg == "ccxt":
        h = [m for m in h if ".pro" not in m and ".async_support" not in m]
        datas = [t for t in datas
                 if "async_support" not in t[0].replace("\\", "/")
                 and "/pro/" not in t[0].replace("\\", "/")]
    hiddenimports += h

hiddenimports += [
    "_cffi_backend", "cffi",
    # system-tray menu-bar backend on macOS (needs PyObjC at runtime)
    "pystray", "pystray._darwin", "PIL", "PIL.Image",
    # our own flat modules
    "exchange", "backend", "pricefeed", "webhook_server", "guardrails",
    "history", "notifications", "security", "config", "gui", "login",
    "analytics_window", "backtest_window", "chart_window", "theme",
    "relay_client", "autostart", "tray", "single_instance", "strategy",
    "strategy_runner", "updater", "connectivity", "applog", "licence",
]

import os
# Bundle brand assets when present (fetch_logo.py / make_icns.py create them).
datas += [(f, ".") for f in ("logo.png", "icon.icns", "icon.ico") if os.path.exists(f)]
_app_icon = "icon.icns" if os.path.exists("icon.icns") else None

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "ccxt.pro", "ccxt.async_support",
        "matplotlib", "numpy",
        "test", "lib2to3", "pydoc_data", "tkinter.test", "distutils",
    ],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Trevolto",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # windowed (no terminal)
    disable_windowed_traceback=False,
    icon=_app_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Trevolto",
)

app = BUNDLE(
    coll,
    name="Trevolto.app",
    icon=_app_icon,
    bundle_identifier="tech.trevolto.bot",
    info_plist={
        "CFBundleName": "Trevolto",
        "CFBundleDisplayName": "Trevolto",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "10.13",
        # Keep a normal Dock app (the tray/menu-bar is an extra, not the only UI).
        "LSUIElement": False,
    },
)
