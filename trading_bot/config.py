"""Central configuration and constants for the trading bot.

Everything user-tunable that is *not* a secret lives here or in the on-disk
settings file. Secrets (API key/secret) are stored encrypted by ``security.py``.
"""

import os

APP_NAME = "TradingBot"

# ---------------------------------------------------------------------------
# Storage locations. We keep state in %APPDATA%\TradingBot on Windows and in
# ~/.trading_bot elsewhere so the app behaves on every Windows version while
# still being testable on Linux/macOS during development.
# ---------------------------------------------------------------------------
def _data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


DATA_DIR = _data_dir()
CREDENTIALS_FILE = os.path.join(DATA_DIR, "credentials.enc")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
SALT_FILE = os.path.join(DATA_DIR, "salt.bin")
LOG_FILE = os.path.join(DATA_DIR, "trade_log.csv")

# ---------------------------------------------------------------------------
# Exchanges exposed in the drop-down. The value is the ccxt id; if the exchange
# offers a separate futures/swap product line via ccxt we note the default
# market type used for position fetching.
# ---------------------------------------------------------------------------
SUPPORTED_EXCHANGES = ["binance", "bybit", "okx", "kucoin", "bitget"]

# Quote currency used for balance display and risk-based sizing.
QUOTE_CURRENCY = "USDT"

# ---------------------------------------------------------------------------
# Webhook receiver. TradingView alerts POST JSON here. Keep the port high so it
# does not need admin rights to bind on Windows.
# ---------------------------------------------------------------------------
WEBHOOK_HOST = "0.0.0.0"
WEBHOOK_PORT = 8723
# Shared secret expected in the webhook payload ("passphrase"). Empty = disabled,
# but you should set one in Settings before exposing the port to the internet.
DEFAULT_WEBHOOK_PASSPHRASE = ""

# How often (seconds) the backend refreshes balance + open positions (REST).
POLL_INTERVAL = 5

# How often (seconds) the price feed refreshes "current" prices for open
# symbols. WebSocket (ccxt.pro) pushes faster than this; this is the REST
# fallback cadence. Kept short for a near real-time feel without hammering the
# API (only open symbols are queried).
PRICE_POLL_INTERVAL = 2

# Consecutive REST refresh failures before we warn the user the connection
# looks lost (handles dropped connections / rate-limit storms gracefully).
MAX_REFRESH_FAILURES = 3

# Default trade sizing.
DEFAULT_TRADE_SIZE = 0.001          # in base asset, e.g. 0.001 BTC
DEFAULT_RISK_PERCENT = 1.0          # % of balance risked when risk-based sizing

# When True the bot simulates fills instead of sending real orders.
# Per the user's choice the app starts LIVE (Safe Mode off), but the toggle
# remains available in the UI.
DEFAULT_SAFE_MODE = False

APP_VERSION = "1.0.0"
