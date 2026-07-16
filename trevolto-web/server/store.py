"""Multi-tenant SQLite store: users, encrypted keys, settings, trades, equity.

Replaces the desktop app's single-file ``history.db`` + local settings/creds
with per-user rows so one server can host many customers.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from typing import Optional

from .config_web import DB_PATH
from . import security

_LOCK = threading.RLock()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db() -> None:
    with _LOCK, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                pw_hash TEXT NOT NULL,
                webhook_token TEXT NOT NULL,
                settings TEXT NOT NULL DEFAULT '{}',
                keys_blob TEXT,
                created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trades(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ts REAL NOT NULL,
                symbol TEXT, side TEXT, kind TEXT, status TEXT,
                amount REAL, price REAL, pnl REAL, note TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_trades_user ON trades(user_id, ts);
            CREATE TABLE IF NOT EXISTS equity(
                user_id INTEGER NOT NULL,
                ts REAL NOT NULL, balance REAL, pnl REAL
            );
            CREATE INDEX IF NOT EXISTS ix_equity_user ON equity(user_id, ts);
            """
        )


# --- users ------------------------------------------------------------------
def create_user(email: str, password: str) -> dict:
    email = email.strip().lower()
    with _LOCK, _conn() as c:
        if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise ValueError("email already registered")
        token = secrets.token_urlsafe(24)
        cur = c.execute(
            "INSERT INTO users(email, pw_hash, webhook_token, created) VALUES(?,?,?,?)",
            (email, security.hash_password(password), token, time.time()),
        )
        uid = cur.lastrowid
    # Read after the transaction commits (get_user uses its own connection).
    return get_user(uid)


def get_user(user_id: int) -> Optional[dict]:
    with _LOCK, _conn() as c:
        r = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(r) if r else None


def get_user_by_email(email: str) -> Optional[dict]:
    with _LOCK, _conn() as c:
        r = c.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        return dict(r) if r else None


def user_by_webhook(token: str) -> Optional[dict]:
    with _LOCK, _conn() as c:
        r = c.execute("SELECT * FROM users WHERE webhook_token=?", (token,)).fetchone()
        return dict(r) if r else None


def save_settings(user_id: int, settings: dict) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET settings=? WHERE id=?", (json.dumps(settings), user_id))


def load_settings(user_id: int) -> dict:
    u = get_user(user_id)
    return json.loads(u["settings"]) if u and u["settings"] else {}


def save_keys(user_id: int, keys: dict) -> None:
    """keys = {exchange, market_type, api_key, api_secret, password}."""
    blob = security.encrypt_secret(keys)
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET keys_blob=? WHERE id=?", (blob, user_id))


def load_keys(user_id: int) -> Optional[dict]:
    u = get_user(user_id)
    if not u or not u["keys_blob"]:
        return None
    try:
        return security.decrypt_secret(u["keys_blob"])
    except Exception:
        return None


def clear_keys(user_id: int) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET keys_blob=NULL WHERE id=?", (user_id,))


# --- trades / equity --------------------------------------------------------
def record_trade(user_id: int, *, symbol="", side="", kind="", status="",
                 amount=0.0, price=0.0, pnl=0.0, note="") -> None:
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT INTO trades(user_id,ts,symbol,side,kind,status,amount,price,pnl,note)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (user_id, time.time(), symbol, side, kind, status, amount, price, pnl, note),
        )


def record_equity(user_id: int, balance: float, pnl: float) -> None:
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO equity(user_id,ts,balance,pnl) VALUES(?,?,?,?)",
                  (user_id, time.time(), balance, pnl))


def recent_trades(user_id: int, limit: int = 200) -> list:
    with _LOCK, _conn() as c:
        rows = c.execute(
            "SELECT * FROM trades WHERE user_id=? ORDER BY ts DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def equity_curve(user_id: int, limit: int = 1000) -> list:
    with _LOCK, _conn() as c:
        rows = c.execute(
            "SELECT ts,balance,pnl FROM equity WHERE user_id=? ORDER BY ts DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def analytics(user_id: int) -> dict:
    with _LOCK, _conn() as c:
        rows = c.execute(
            "SELECT status,pnl FROM trades WHERE user_id=? AND kind IN ('close','tp','sl')",
            (user_id,),
        ).fetchall()
    closed = [r["pnl"] or 0.0 for r in rows]
    wins = [p for p in closed if p > 0]
    losses = [p for p in closed if p < 0]
    n = len(closed)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100 * len(wins) / n, 1) if n else 0.0,
        "realized_pnl": round(sum(closed), 4),
        "best": round(max(closed), 4) if closed else 0.0,
        "worst": round(min(closed), 4) if closed else 0.0,
        "avg_win": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 4) if losses else 0.0,
    }
