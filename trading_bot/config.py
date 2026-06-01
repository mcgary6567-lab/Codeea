"""Central configuration and constants for the trading bot.

Everything user-tunable that is *not* a secret lives here or in the on-disk
settings file. Secrets (API key/secret) are stored encrypted by ``security.py``.
"""

import os
import sys

APP_NAME = "TradingBot"            # storage key (kept stable so saved PINs persist)
APP_TITLE = "Prometheus AI Crypto Bot"
WEBSITE_URL = "https://prometheusai.tech/"
SUPPORT_EMAIL = "support@prometheusai.tech"


def resource_path(name: str) -> str:
    """Path to a bundled resource (logo/icon), works in dev and in a frozen exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)

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
HISTORY_DB = os.path.join(DATA_DIR, "history.db")
STATE_FILE = os.path.join(DATA_DIR, "state.json")   # crash-recovery state

# Auto-update: a small JSON at this URL like {"version":"1.0.1","url":"..."}
UPDATE_URL = "https://prometheusai.tech/version.json"

# ---------------------------------------------------------------------------
# Exchanges exposed in the drop-down. The value is the ccxt id; if the exchange
# offers a separate futures/swap product line via ccxt we note the default
# market type used for position fetching.
# ---------------------------------------------------------------------------
SUPPORTED_EXCHANGES = ["binance", "bybit", "okx", "kucoin", "bitget"]

# Pretty (Title-case) display names. ccxt ids stay lowercase above; the UI shows
# these and maps back to the id when connecting.
EXCHANGE_LABELS = {
    "binance": "Binance",
    "bybit": "Bybit",
    "okx": "OKX",
    "kucoin": "KuCoin",
    "bitget": "Bitget",
}


def exchange_label(ex_id: str) -> str:
    return EXCHANGE_LABELS.get(ex_id, ex_id.title())


def exchange_id(label: str) -> str:
    for eid, lab in EXCHANGE_LABELS.items():
        if lab == label:
            return eid
    return label.lower()

# Quote currency used for balance display and risk-based sizing.
QUOTE_CURRENCY = "USDT"

# Most-traded crypto pairs offered in the Symbol dropdown (BTC & ETH first).
TOP_PAIRS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "MATIC/USDT",
]

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
# symbols. WebSocket (ccxt.pro) pushes faster; this is the REST fallback
# cadence. Short for a near real-time feel without hammering the API.
PRICE_POLL_INTERVAL = 2

# Consecutive REST refresh failures before we warn the user the connection
# looks lost (handles dropped connections / rate-limit storms gracefully).
MAX_REFRESH_FAILURES = 3

# ---------------------------------------------------------------------------
# Cloud relay (optional). Your TradingView account posts to your relay; each
# customer's app polls it for new signals — no ngrok / port-forwarding needed.
# ---------------------------------------------------------------------------
DEFAULT_RELAY_URL = "https://hooks.prometheusai.tech/poll.php"
RELAY_POLL_INTERVAL = 1.0   # seconds between polls (lower = faster pickup, more requests)

# Default trade sizing.
DEFAULT_TRADE_SIZE = 0.03           # in base asset (e.g. 0.03 BTC)
DEFAULT_RISK_PERCENT = 1.0          # % of balance risked when risk-based sizing

# Sizing modes offered in the UI.
#   fixed        -> use the fixed lot size as-is
#   risk_balance -> spend risk% of balance (notional / current price)
#   risk_stop    -> risk risk% of balance over the entry->stop distance
SIZING_MODES = ["fixed", "risk_balance", "risk_stop"]
SIZING_MODE_LABELS = {
    "fixed": "Fixed lot",
    "risk_balance": "Risk % of balance",
    "risk_stop": "Risk % per trade (stop-based)",
}

# When True, place reduce-only SL/TP orders from alert payloads after entry.
DEFAULT_AUTO_BRACKET = True
# Fraction of the position closed at TP1 when both TP1 and TP2 are provided.
TP1_SCALE_OUT = 0.5

# ---------------------------------------------------------------------------
# Execution: order type / leverage / margin.
# ---------------------------------------------------------------------------
ORDER_TYPES = ["market", "limit"]
MARGIN_MODES = ["", "cross", "isolated"]      # "" = leave exchange default
DEFAULT_ORDER_TYPE = "market"
DEFAULT_LEVERAGE = 0                            # 0 = don't change exchange setting

# ---------------------------------------------------------------------------
# Risk guardrails. 0 disables the individual limit.
# ---------------------------------------------------------------------------
DEFAULT_MAX_OPEN_POSITIONS = 0                  # 0 = unlimited
DEFAULT_DAILY_LOSS_LIMIT = 0.0                  # quote ccy; trip & block at -X
DEFAULT_COOLDOWN_SECONDS = 0                    # min seconds between trades / symbol
DEFAULT_DEDUPE_SECONDS = 0                      # drop identical signals within window

# When True the bot simulates fills instead of sending real orders.
# Per the user's choice the app starts LIVE (Safe Mode off), but the toggle
# remains available in the UI.
DEFAULT_SAFE_MODE = False

APP_VERSION = "1.0.0"
