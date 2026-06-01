# PyInstaller spec for the Prometheus AI Crypto Bot.
# Build on Windows with:  pyinstaller --noconfirm trading_bot.spec
# Output: dist\PrometheusAICryptoBot.exe  (single-file, windowed)
#
# Size-optimised build: we bundle ccxt's SYNC layer only and drop the
# WebSocket/async layer (ccxt.pro + ccxt.async_support) — that roughly halves
# ccxt's footprint. The price feed degrades to REST polling automatically.

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("ccxt", "certifi", "cryptography"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    if pkg == "ccxt":
        # Drop the async + websocket variants of every exchange (big saving).
        h = [m for m in h if ".pro" not in m and ".async_support" not in m]
        datas = [t for t in datas
                 if "async_support" not in t[0].replace("\\", "/")
                 and "/pro/" not in t[0].replace("\\", "/")]
    hiddenimports += h

hiddenimports += [
    # cryptography uses cffi at runtime — PyInstaller often misses this, which
    # makes the exe crash on launch with "No module named '_cffi_backend'".
    "_cffi_backend", "cffi",
    # our own flat modules
    "exchange", "backend", "pricefeed", "webhook_server", "guardrails",
    "history", "notifications", "security", "config", "gui", "login",
    "analytics_window", "theme", "relay_client",
]

# Bundle brand assets so the frozen exe can show the logo + window icon.
# Bundle brand assets ONLY if present. fetch_logo.py downloads them from the
# official URL before this spec runs; if that fails the build still succeeds and
# the app falls back to no logo.
import os
datas += [(f, ".") for f in ("logo.png", "icon.ico") if os.path.exists(f)]
_exe_icon = "icon.ico" if os.path.exists("icon.ico") else None

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
        # WebSocket/async ccxt layer — pricefeed falls back to REST if absent.
        "ccxt.pro", "ccxt.async_support",
        # Only used by the diagram scripts, never by the app.
        "matplotlib", "numpy", "PIL",
        # Stdlib bloat we never touch.
        "test", "lib2to3", "pydoc_data", "tkinter.test", "distutils",
    ],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PrometheusAICryptoBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX packing often trips AV heuristics; off = friendlier
    runtime_tmpdir=None,
    console=False,        # windowed app (no console window)
    disable_windowed_traceback=False,
    icon=_exe_icon,
    version="version_info.txt",   # embeds publisher/product metadata
)
