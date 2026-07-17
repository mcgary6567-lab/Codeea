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

TRIAL_DAYS = float(__import__("os").environ.get("PROMETHEUS_TRIAL_DAYS", "10"))
ADMIN_EMAIL = __import__("os").environ.get("PROMETHEUS_ADMIN_EMAIL", "").strip().lower()


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
                created REAL NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                plan TEXT NOT NULL DEFAULT 'trial',
                trial_ends REAL NOT NULL DEFAULT 0,
                licence_until REAL NOT NULL DEFAULT 0,
                last_seen REAL NOT NULL DEFAULT 0,
                first_name TEXT NOT NULL DEFAULT '',
                last_name TEXT NOT NULL DEFAULT ''
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
        # Lightweight migration for DBs created before access-control columns.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        for name, ddl in {
            "is_admin": "INTEGER NOT NULL DEFAULT 0",
            "active": "INTEGER NOT NULL DEFAULT 1",
            "plan": "TEXT NOT NULL DEFAULT 'trial'",
            "trial_ends": "REAL NOT NULL DEFAULT 0",
            "licence_until": "REAL NOT NULL DEFAULT 0",
            "last_seen": "REAL NOT NULL DEFAULT 0",
            "first_name": "TEXT NOT NULL DEFAULT ''",
            "last_name": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in cols:
                c.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")


# --- users ------------------------------------------------------------------
def create_user(email: str, password: str, first_name: str = "", last_name: str = "") -> dict:
    email = email.strip().lower()
    with _LOCK, _conn() as c:
        if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise ValueError("email already registered")
        token = secrets.token_urlsafe(24)
        now = time.time()
        # First-ever user, or the configured admin email, becomes an admin.
        first = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"] == 0
        is_admin = 1 if (first or (ADMIN_EMAIL and email == ADMIN_EMAIL)) else 0
        cur = c.execute(
            "INSERT INTO users(email, pw_hash, webhook_token, created, is_admin, trial_ends,"
            " first_name, last_name) VALUES(?,?,?,?,?,?,?,?)",
            (email, security.hash_password(password), token, now, is_admin,
             now + TRIAL_DAYS * 86400, first_name.strip(), last_name.strip()),
        )
        uid = cur.lastrowid
    # Read after the transaction commits (get_user uses its own connection).
    return get_user(uid)


# --- access control ---------------------------------------------------------
def entitlement(user: dict) -> dict:
    """Whether this user may trade, and why. Admins always may."""
    now = time.time()
    if user.get("is_admin"):
        return {"ok": True, "status": "admin", "days_left": None}
    if not user.get("active", 1):
        return {"ok": False, "status": "suspended", "days_left": 0}
    lic = user.get("licence_until") or 0
    if lic > now:
        return {"ok": True, "status": "licensed", "days_left": int((lic - now) / 86400) + 1}
    trial = user.get("trial_ends") or 0
    if trial > now:
        return {"ok": True, "status": "trial", "days_left": int((trial - now) / 86400) + 1}
    return {"ok": False, "status": "expired", "days_left": 0}


def touch(user_id: int) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET last_seen=? WHERE id=?", (time.time(), user_id))


# --- admin ------------------------------------------------------------------
def list_users() -> list:
    with _LOCK, _conn() as c:
        rows = c.execute(
            "SELECT id,email,is_admin,active,plan,trial_ends,licence_until,created,last_seen,"
            "(keys_blob IS NOT NULL) AS has_keys FROM users ORDER BY id"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["entitlement"] = entitlement(d)
        out.append(d)
    return out


def set_active(user_id: int, active: bool) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, user_id))


def set_admin(user_id: int, is_admin: bool) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if is_admin else 0, user_id))


def grant_licence(user_id: int, days: float) -> None:
    """Extend the licence by ``days`` from max(now, current expiry)."""
    with _LOCK, _conn() as c:
        r = c.execute("SELECT licence_until FROM users WHERE id=?", (user_id,)).fetchone()
        base = max(time.time(), (r["licence_until"] or 0) if r else 0)
        c.execute("UPDATE users SET licence_until=?, plan='paid' WHERE id=?",
                  (base + days * 86400, user_id))


def revoke_licence(user_id: int) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET licence_until=0, plan='trial' WHERE id=?", (user_id,))


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


def update_email(user_id: int, email: str) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET email=? WHERE id=?", (email.strip().lower(), user_id))


def update_password(user_id: int, password: str) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET pw_hash=? WHERE id=?",
                  (security.hash_password(password), user_id))


def update_name(user_id: int, first_name: str, last_name: str) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET first_name=?, last_name=? WHERE id=?",
                  (first_name.strip(), last_name.strip(), user_id))


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


def analytics(user_id: int, since: float | None = None, until: float | None = None) -> dict:
    q = "SELECT symbol,pnl,ts FROM trades WHERE user_id=? AND kind='close'"
    args: list = [user_id]
    if since:
        q += " AND ts>=?"; args.append(since)
    if until:
        q += " AND ts<=?"; args.append(until)
    with _LOCK, _conn() as c:
        rows = c.execute(q, args).fetchall()
    closed = [(r["symbol"] or "?", r["pnl"] or 0.0) for r in rows]
    pnls = [p for _, p in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)
    by: dict = {}
    for sym, p in closed:
        d = by.setdefault(sym, {"symbol": sym, "trades": 0, "pnl": 0.0, "wins": 0})
        d["trades"] += 1
        d["pnl"] = round(d["pnl"] + p, 4)
        d["wins"] += 1 if p > 0 else 0
    for d in by.values():
        d["win_rate"] = round(100 * d["wins"] / d["trades"], 1) if d["trades"] else 0.0
    return {
        "trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(100 * len(wins) / n, 1) if n else 0.0,
        "realized_pnl": round(sum(pnls), 4),
        "best": round(max(pnls), 4) if pnls else 0.0,
        "worst": round(min(pnls), 4) if pnls else 0.0,
        "avg_win": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 4) if losses else 0.0,
        "by_symbol": sorted(by.values(), key=lambda x: -x["pnl"]),
    }
