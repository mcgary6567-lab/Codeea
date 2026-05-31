# PyInstaller spec for the TradingView Trading Bot.
# Build on Windows with:  pyinstaller --noconfirm trading_bot.spec
# Output: dist\TradingBot.exe  (single-file, windowed)
#
# ccxt ships hundreds of exchange submodules and bundles data files; certifi
# provides the CA bundle used for HTTPS. We collect both fully so the frozen
# exe can reach every supported exchange.

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
# Fully bundle ccxt (hundreds of exchange modules + data), certifi (CA bundle),
# and cryptography (cffi-backed). collect_all pulls submodules, data, and libs.
for pkg in ("ccxt", "certifi", "cryptography"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# ccxt.pro (WebSocket) submodules + our own modules, to be safe.
hiddenimports += collect_submodules("ccxt")
hiddenimports += [
    # cryptography uses cffi at runtime — PyInstaller often misses this, which
    # makes the exe crash on launch with "No module named '_cffi_backend'".
    "_cffi_backend", "cffi",
    # our own flat modules
    "exchange", "backend", "pricefeed", "webhook_server", "guardrails",
    "history", "notifications", "security", "config", "gui", "login",
    "analytics_window",
]

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "numpy"],  # only used by the diagram scripts, not the app
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
    name="TradingBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,        # windowed app (no console window)
    disable_windowed_traceback=False,
    icon="icon.ico",
)
