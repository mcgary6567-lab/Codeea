"""Configuration loader for the Dip2Green bridge.

All settings come from environment variables (see .env.example). Keeping this
in one place makes the rest of the code easy to read and test.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


class Config:
    # Webhook security
    WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "").strip()
    PORT = int(os.getenv("PORT", "8080"))

    # Safety switches
    DRY_RUN = _bool("DRY_RUN", True)
    TESTNET = _bool("TESTNET", True)

    # Exchange
    EXCHANGE = os.getenv("EXCHANGE", "binance").strip()
    API_KEY = os.getenv("API_KEY", "").strip()
    API_SECRET = os.getenv("API_SECRET", "").strip()
    API_PASSWORD = os.getenv("API_PASSWORD", "").strip()
    MARKET_TYPE = os.getenv("MARKET_TYPE", "spot").strip()

    # Risk / sizing
    RISK_PCT = _float("RISK_PCT", 0.01)
    MAX_NOTIONAL = _float("MAX_NOTIONAL", 0.0)
    TP1_FRACTION = _float("TP1_FRACTION", 0.5)
    TP2_FRACTION = _float("TP2_FRACTION", 0.5)
    ENTRY_TYPE = os.getenv("ENTRY_TYPE", "market").strip().lower()

    SYMBOL_WHITELIST = [
        s.strip().upper()
        for s in os.getenv("SYMBOL_WHITELIST", "").split(",")
        if s.strip()
    ]

    @classmethod
    def validate(cls) -> None:
        if not cls.WEBHOOK_TOKEN or cls.WEBHOOK_TOKEN == "change-me-to-a-long-random-string":
            raise SystemExit("WEBHOOK_TOKEN is not set. Edit your .env first.")
        if abs((cls.TP1_FRACTION + cls.TP2_FRACTION) - 1.0) > 1e-6:
            raise SystemExit("TP1_FRACTION + TP2_FRACTION must equal 1.0")
        if not cls.DRY_RUN and not (cls.API_KEY and cls.API_SECRET):
            raise SystemExit("Live mode needs API_KEY and API_SECRET in .env")
