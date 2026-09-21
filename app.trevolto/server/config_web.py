"""Web-app configuration (paths + server defaults).

The trading *engine* keeps its own ``engine/config.py`` (constants + exchange
maps, imported as top-level ``config`` once the engine dir is on ``sys.path``).
This module is only the web server's own settings.
"""
import os

DATA_DIR = os.environ.get("TREVOLTO_DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
DATA_DIR = os.path.abspath(DATA_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

# Point the engine's file-based storage at the same dir (harmless; the web app
# uses its own SQLite store for trades/equity, but the engine reads this env).
os.environ.setdefault("TREVOLTO_DATA_DIR", DATA_DIR)

DB_PATH = os.path.join(DATA_DIR, "trevolto_web.db")

# Public base URL the app advertises for TradingView webhooks (behind your TLS).
PUBLIC_URL = os.environ.get("TREVOLTO_PUBLIC_URL", "").rstrip("/")

# Background refresh cadence (seconds) for the per-user poll loop.
POLL_INTERVAL = float(os.environ.get("TREVOLTO_POLL_INTERVAL", "3"))

# Assumed licence price (per active licence, per period) used only to *estimate*
# MRR on the admin dashboard. Purely informational — the app does not bill.
LICENCE_PRICE = float(os.environ.get("TREVOLTO_LICENCE_PRICE", "49"))
