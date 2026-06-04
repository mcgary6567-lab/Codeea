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

# Rotating debug/support log (separate from the human-readable trade-log CSV).
# Capped + auto-rotated so it stays small enough to attach to a support ticket.
DEBUG_LOG = os.path.join(DATA_DIR, "app.log")
LOG_MAX_BYTES = 1_000_000          # 1 MB per file
LOG_BACKUP_COUNT = 5               # keep 5 rotations (~6 MB total ceiling)

# Daily Telegram P&L recap (end-of-day summary pushed via the Telegram bot).
DEFAULT_DAILY_SUMMARY = False
DEFAULT_SUMMARY_HOUR = 23          # local hour (0-23) to send the recap

# When True, only important events (entries, closes+PnL, halts, security) are
# sent to Telegram; routine/noisy events still beep + toast locally.
DEFAULT_TELEGRAM_IMPORTANT_ONLY = True

# Auto-update: a small JSON at this URL like {"version":"1.0.1","url":"..."}
UPDATE_URL = "https://prometheusai.tech/version.json"

# ---------------------------------------------------------------------------
# Exchanges exposed in the drop-down. The value is the ccxt id; if the exchange
# offers a separate futures/swap product line via ccxt we note the default
# market type used for position fetching.
# ---------------------------------------------------------------------------
SUPPORTED_EXCHANGES = ["binance", "binanceus", "bybit", "okx", "kucoin",
                       "bitget", "kraken", "coinbase"]

# Pretty (Title-case) display names. ccxt ids stay lowercase above; the UI shows
# these and maps back to the id when connecting.
EXCHANGE_LABELS = {
    "binance": "Binance",
    "binanceus": "Binance.US",
    "bybit": "Bybit",
    "okx": "OKX",
    "kucoin": "KuCoin",
    "bitget": "Bitget",
    "kraken": "Kraken",
    "coinbase": "Coinbase",
}

# Exchanges that offer spot only (no futures/derivatives product line). If the
# user picks Futures on one of these, the app warns and falls back to Spot.
SPOT_ONLY_EXCHANGES = {"binanceus"}

# Some venues run futures on a SEPARATE ccxt class rather than a defaultType
# switch on the spot class. When Futures is selected we connect to the class
# below instead of the spot id.
FUTURES_EXCHANGE_ID = {
    "kraken": "krakenfutures",            # USD-margined perpetuals
    "coinbase": "coinbaseinternational",  # USDC perps — eligible / non-US accounts only
}

# Margin/quote currency for a venue's linear futures (USDT-M is the default).
# Kraken futures are USD-margined; Coinbase International perps are USDC-margined.
FUTURES_QUOTE = {
    "kraken": "USD",
    "coinbase": "USDC",
}


def futures_ccxt_id(ex_id: str) -> str:
    """ccxt class to use for this venue's futures (may differ from its spot id)."""
    return FUTURES_EXCHANGE_ID.get(ex_id, ex_id)


def futures_quote(ex_id: str) -> str:
    """Margin/quote currency for this venue's linear futures (default USDT)."""
    return FUTURES_QUOTE.get(ex_id, "USDT")


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
RELAY_POLL_INTERVAL = 5.0   # seconds between polls (low enough for fast pickup,
                            # high enough to avoid host/Cloudflare rate-limit 403s)

# Self-service free trial. New users click "Start Free Trial" to get a 10-day
# full-access licence (issued by the relay's trial.php). When it lapses the app
# locks and the "Get License" button opens CHECKOUT_URL.
TRIAL_DAYS = 10
# >>> SET THIS to your real checkout link (Stripe Payment Link / Gumroad / etc.)
CHECKOUT_URL = "https://prometheusai.tech/checkout"

# ---------------------------------------------------------------------------
# Built-in strategy engine. Runs the bot's own port of the indicator over
# exchange candles so it can trade with no TradingView account. It pulls
# candles from a public (keyless) client on its own thread — like the price
# feed — and feeds confirmed BUYs into the same trade pipeline as webhooks.
# ---------------------------------------------------------------------------
STRATEGY_TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"]
DEFAULT_STRATEGY_TIMEFRAME = "5m"
# How often (seconds) the runner checks for a freshly-closed candle. Lower =
# faster pickup after a candle closes; one cheap OHLCV call per symbol per poll.
STRATEGY_POLL_INTERVAL = 15.0
# Candles fetched per evaluation. Plenty of history for the indicators to
# converge (Wilder RSI/ATR warmup) while staying a single light request.
STRATEGY_CANDLE_LIMIT = 300
# Default symbols the built-in strategy watches (comma-separated in the UI).
DEFAULT_STRATEGY_SYMBOLS = "BTC/USDT"

# Licence gate for the built-in strategy. It validates the same relay licence
# token (one subscription per customer) against the relay's verify.php before
# trading, then re-checks on this cadence. A transient server outage is
# tolerated for the grace window so a legit user isn't stranded mid-session.
LICENCE_RECHECK_INTERVAL = 6 * 3600.0      # re-verify a valid licence every 6h
LICENCE_RETRY_INTERVAL = 300.0             # retry sooner after a server error
LICENCE_GRACE_SECONDS = 24 * 3600.0        # keep trading this long through outages

# Default trade sizing.
DEFAULT_TRADE_SIZE = 0.003           # in base asset (e.g. 0.003 BTC)
DEFAULT_RISK_PERCENT = 1.0          # % of balance risked when risk-based sizing

# Skip orders whose notional (size x price) is below this many USDT — avoids a
# cryptic exchange "below minimum" rejection on dust-sized orders.
MIN_NOTIONAL = 5.0

# Sizing modes offered in the UI.
#   fixed        -> use the fixed lot size as-is (in the base coin, e.g. BTC)
#   fixed_quote  -> the size is a USDT amount; buy that many $ worth (size/price)
#   risk_balance -> spend risk% of balance (notional / current price)
#   risk_stop    -> risk risk% of balance over the entry->stop distance
SIZING_MODES = ["fixed", "fixed_quote", "risk_balance", "risk_stop"]
SIZING_MODE_LABELS = {
    "fixed": "Fixed lot (coin)",
    "fixed_quote": "Fixed $ (USDT)",
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
