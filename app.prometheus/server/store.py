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
                amount REAL, price REAL, pnl REAL, note TEXT,
                mode TEXT NOT NULL DEFAULT 'live'
            );
            CREATE INDEX IF NOT EXISTS ix_trades_user ON trades(user_id, ts);
            CREATE TABLE IF NOT EXISTS equity(
                user_id INTEGER NOT NULL,
                ts REAL NOT NULL, balance REAL, pnl REAL
            );
            CREATE INDEX IF NOT EXISTS ix_equity_user ON equity(user_id, ts);
            CREATE TABLE IF NOT EXISTS resets(
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS logins(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, ts REAL NOT NULL,
                ip TEXT NOT NULL DEFAULT '', ua TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS ix_logins_user ON logins(user_id, ts);
            CREATE TABLE IF NOT EXISTS push_subs(
                endpoint TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL, sub TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS broadcasts(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
                message TEXT NOT NULL, level TEXT NOT NULL DEFAULT 'info', active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS sales(
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, ts REAL NOT NULL,
                amount REAL NOT NULL DEFAULT 0, method TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS ix_sales_ts ON sales(ts);
            CREATE TABLE IF NOT EXISTS audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
                admin_email TEXT NOT NULL DEFAULT '', action TEXT NOT NULL DEFAULT '',
                target_id INTEGER, detail TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit(ts);
            """
        )
        for name, ddl in {
            "totp_secret": "TEXT NOT NULL DEFAULT ''",
            "totp_enabled": "INTEGER NOT NULL DEFAULT 0",
            "last_summary": "TEXT NOT NULL DEFAULT ''",
            "token_version": "INTEGER NOT NULL DEFAULT 0",
            "admin_note": "TEXT NOT NULL DEFAULT ''",
            "allow_custom": "INTEGER NOT NULL DEFAULT 0",
            "custom_requested": "REAL NOT NULL DEFAULT 0",
            "custom_reason": "TEXT NOT NULL DEFAULT ''",
        }.items():
            ucols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
            if name not in ucols:
                c.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
        # Lightweight migration for DBs created before access-control columns.
        tcols = {r["name"] for r in c.execute("PRAGMA table_info(trades)")}
        if "mode" not in tcols:
            c.execute("ALTER TABLE trades ADD COLUMN mode TEXT NOT NULL DEFAULT 'live'")
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
# A lifetime licence is stored as a far-future expiry (year ~3000). Any licence
# expiring past year 2100 is treated as "never expires" — this needs no extra
# column, so it is fully backward-compatible with existing databases.
LIFETIME_TS = 32503680000.0        # ~ 3000-01-01, the sentinel we write
LIFETIME_THRESHOLD = 4102444800.0  # ~ 2100-01-01, anything beyond = lifetime


def entitlement(user: dict) -> dict:
    """Whether this user may trade, and why. Admins always may."""
    now = time.time()
    if user.get("is_admin"):
        return {"ok": True, "status": "admin", "days_left": None, "lifetime": False}
    if not user.get("active", 1):
        return {"ok": False, "status": "suspended", "days_left": 0, "lifetime": False}
    lic = user.get("licence_until") or 0
    if lic > now:
        if lic >= LIFETIME_THRESHOLD:
            return {"ok": True, "status": "licensed", "days_left": None, "lifetime": True}
        return {"ok": True, "status": "licensed",
                "days_left": int((lic - now) / 86400) + 1, "lifetime": False}
    trial = user.get("trial_ends") or 0
    if trial > now:
        return {"ok": True, "status": "trial",
                "days_left": int((trial - now) / 86400) + 1, "lifetime": False}
    return {"ok": False, "status": "expired", "days_left": 0, "lifetime": False}


def touch(user_id: int) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET last_seen=? WHERE id=?", (time.time(), user_id))


# --- admin ------------------------------------------------------------------
def list_users() -> list:
    with _LOCK, _conn() as c:
        rows = c.execute(
            "SELECT id,email,first_name,last_name,is_admin,active,plan,trial_ends,licence_until,"
            "created,last_seen,(keys_blob IS NOT NULL) AS has_keys FROM users ORDER BY id"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["entitlement"] = entitlement(d)
        out.append(d)
    return out


def platform_stats(licence_price: float = 0.0) -> dict:
    """Aggregate business + trading metrics across all customers.

    Read-only, single connection, no schema dependency beyond existing columns.
    All money figures for MRR are *estimates* derived from ``licence_price``.
    """
    now = time.time()
    day = 86400.0
    with _LOCK, _conn() as c:
        users = [dict(r) for r in c.execute(
            "SELECT id,is_admin,active,plan,trial_ends,licence_until,created,last_seen,"
            "(keys_blob IS NOT NULL) AS has_keys FROM users"
        ).fetchall()]
        trows = c.execute(
            "SELECT mode,pnl FROM trades WHERE kind='close'"
        ).fetchall()
        topen = c.execute("SELECT COUNT(*) n FROM trades WHERE kind!='close'").fetchone()["n"]

    total = admins = trial = licensed = expired = suspended = with_keys = 0
    new_today = new_7d = new_30d = 0
    ever_paid = 0
    # 30-day daily signup series (index 0 = 29 days ago … 29 = today)
    series = [0] * 30
    for u in users:
        total += 1
        if u["is_admin"]:
            admins += 1
        if u["has_keys"]:
            with_keys += 1
        ent = entitlement(u)
        st = ent["status"]
        if st == "trial":
            trial += 1
        elif st == "licensed":
            licensed += 1
        elif st == "expired":
            expired += 1
        elif st == "suspended":
            suspended += 1
        if u["plan"] in ("paid", "licensed", "lifetime") or (u["licence_until"] or 0) > 0:
            ever_paid += 1
        created = u["created"] or 0
        age = now - created
        if age < day:
            new_today += 1
        if age < 7 * day:
            new_7d += 1
        if age < 30 * day:
            new_30d += 1
        didx = int((created - (now - 30 * day)) / day)
        if 0 <= didx < 30:
            series[didx] += 1

    # trial→paid conversion: share of non-admin customers who ever bought
    customers = total - admins
    conversion = round(100 * ever_paid / customers, 1) if customers else 0.0

    live_pnl = paper_pnl = 0.0
    live_n = paper_n = live_wins = 0
    for r in trows:
        p = r["pnl"] or 0.0
        if (r["mode"] or "live") == "paper":
            paper_pnl += p
            paper_n += 1
        else:
            live_pnl += p
            live_n += 1
            if p > 0:
                live_wins += 1

    return {
        "users": {
            "total": total, "customers": customers, "admins": admins,
            "trial": trial, "licensed": licensed, "expired": expired,
            "suspended": suspended, "with_keys": with_keys,
        },
        "growth": {
            "new_today": new_today, "new_7d": new_7d, "new_30d": new_30d,
            "conversion": conversion, "signup_series": series,
        },
        "revenue": {
            "licensed": licensed,
            "est_mrr": round(licensed * licence_price, 2),
            "licence_price": licence_price,
        },
        "trading": {
            "closed_trades": live_n + paper_n,
            "open_trades": topen,
            "live_trades": live_n, "paper_trades": paper_n,
            "live_pnl": round(live_pnl, 2), "paper_pnl": round(paper_pnl, 2),
            "live_win_rate": round(100 * live_wins / live_n, 1) if live_n else 0.0,
        },
    }


def user_detail(user_id: int) -> Optional[dict]:
    """Full per-customer profile for the admin drawer (read-only)."""
    u = get_user(user_id)
    if not u:
        return None
    safe = {k: v for k, v in u.items()
            if k not in ("pw_hash", "keys_blob", "webhook_token", "totp_secret")}
    safe["has_keys"] = bool(u.get("keys_blob"))
    safe["entitlement"] = entitlement(u)
    safe["analytics"] = analytics(user_id)
    safe["pnl_modes"] = pnl_by_mode(user_id)
    safe["recent_trades"] = recent_trades(user_id, 20)
    return safe


def set_active(user_id: int, active: bool) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, user_id))


def set_admin(user_id: int, is_admin: bool) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if is_admin else 0, user_id))


def grant_licence(user_id: int, days: float = 0.0, lifetime: bool = False) -> None:
    """Grant or extend a licence.

    ``lifetime=True`` (the admin default) grants a permanent, never-expiring
    licence. Otherwise the licence is extended by ``days`` from
    max(now, current expiry).
    """
    with _LOCK, _conn() as c:
        if lifetime:
            c.execute("UPDATE users SET licence_until=?, plan='lifetime' WHERE id=?",
                      (LIFETIME_TS, user_id))
            return
        r = c.execute("SELECT licence_until FROM users WHERE id=?", (user_id,)).fetchone()
        cur = (r["licence_until"] or 0) if r else 0
        # If they already hold a lifetime licence, a day-grant must not shorten it.
        if cur >= LIFETIME_THRESHOLD:
            return
        base = max(time.time(), cur)
        c.execute("UPDATE users SET licence_until=?, plan='paid' WHERE id=?",
                  (base + days * 86400, user_id))


def revoke_licence(user_id: int) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET licence_until=0, plan='trial' WHERE id=?", (user_id,))


def extend_trial(user_id: int, days: float) -> None:
    """Extend the free trial by ``days`` from max(now, current trial end)."""
    with _LOCK, _conn() as c:
        r = c.execute("SELECT trial_ends FROM users WHERE id=?", (user_id,)).fetchone()
        base = max(time.time(), (r["trial_ends"] or 0) if r else 0)
        c.execute("UPDATE users SET trial_ends=? WHERE id=?", (base + days * 86400, user_id))


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


# --- password reset ---------------------------------------------------------
def create_reset(user_id: int, ttl_seconds: int = 3600) -> str:
    raw, h = security.new_reset_token()
    with _LOCK, _conn() as c:
        c.execute("DELETE FROM resets WHERE user_id=?", (user_id,))
        c.execute("INSERT INTO resets(token_hash,user_id,expires) VALUES(?,?,?)",
                  (h, user_id, time.time() + ttl_seconds))
    return raw


def consume_reset(raw_token: str) -> int | None:
    h = security.hash_token(raw_token)
    with _LOCK, _conn() as c:
        r = c.execute("SELECT user_id,expires FROM resets WHERE token_hash=?", (h,)).fetchone()
        if not r:
            return None
        c.execute("DELETE FROM resets WHERE token_hash=?", (h,))
        if r["expires"] < time.time():
            return None
        return r["user_id"]


# --- 2FA (TOTP) -------------------------------------------------------------
def set_totp_secret(user_id: int, secret: str) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET totp_secret=? WHERE id=?", (secret, user_id))


def set_totp_enabled(user_id: int, enabled: bool) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET totp_enabled=? WHERE id=?", (1 if enabled else 0, user_id))


# --- daily summary bookkeeping ----------------------------------------------
def all_users() -> list:
    with _LOCK, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM users").fetchall()]


def mark_summary(user_id: int, day: str) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET last_summary=? WHERE id=?", (day, user_id))


def realized_pnl_since(user_id: int, since_ts: float) -> tuple:
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT pnl FROM trades WHERE user_id=? AND kind='close' AND ts>=?",
                         (user_id, since_ts)).fetchall()
    pnls = [r["pnl"] or 0.0 for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    return round(sum(pnls), 4), len(pnls), wins


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
                 amount=0.0, price=0.0, pnl=0.0, note="", mode="live") -> None:
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT INTO trades(user_id,ts,symbol,side,kind,status,amount,price,pnl,note,mode)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, time.time(), symbol, side, kind, status, amount, price, pnl, note, mode),
        )


def pnl_by_mode(user_id: int) -> dict:
    """Realized PnL + trade/win counts split by paper vs live (closed trades)."""
    out = {m: {"trades": 0, "wins": 0, "pnl": 0.0} for m in ("live", "paper")}
    with _LOCK, _conn() as c:
        rows = c.execute(
            "SELECT mode, pnl FROM trades WHERE user_id=? AND kind='close'", (user_id,)
        ).fetchall()
    for r in rows:
        m = r["mode"] if r["mode"] in out else "live"
        p = r["pnl"] or 0.0
        out[m]["trades"] += 1
        out[m]["wins"] += 1 if p > 0 else 0
        out[m]["pnl"] = round(out[m]["pnl"] + p, 4)
    for m in out:
        t = out[m]["trades"]
        out[m]["win_rate"] = round(100 * out[m]["wins"] / t, 1) if t else 0.0
    return out


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


# --- security: token versioning, login history ------------------------------
def bump_token_version(user_id: int) -> int:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET token_version = token_version + 1 WHERE id=?", (user_id,))
        r = c.execute("SELECT token_version FROM users WHERE id=?", (user_id,)).fetchone()
        return int(r["token_version"]) if r else 0


def record_login(user_id: int, ip: str = "", ua: str = "") -> None:
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO logins(user_id, ts, ip, ua) VALUES(?,?,?,?)",
                  (user_id, time.time(), (ip or "")[:64], (ua or "")[:300]))
        c.execute("DELETE FROM logins WHERE user_id=? AND id NOT IN "
                  "(SELECT id FROM logins WHERE user_id=? ORDER BY ts DESC LIMIT 30)",
                  (user_id, user_id))


def recent_logins(user_id: int, limit: int = 10) -> list:
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT ts, ip, ua FROM logins WHERE user_id=? ORDER BY ts DESC LIMIT ?",
                         (user_id, limit)).fetchall()
        return [dict(r) for r in rows]


# --- web-push subscriptions + kv store --------------------------------------
def add_push_sub(user_id: int, endpoint: str, sub_json: str) -> None:
    with _LOCK, _conn() as c:
        c.execute("INSERT OR REPLACE INTO push_subs(endpoint, user_id, sub, created) VALUES(?,?,?,?)",
                  (endpoint, user_id, sub_json, time.time()))


def push_subs_for(user_id: int) -> list:
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT endpoint, sub FROM push_subs WHERE user_id=?", (user_id,)).fetchall()
        return [dict(r) for r in rows]


def remove_push_sub(endpoint: str) -> None:
    with _LOCK, _conn() as c:
        c.execute("DELETE FROM push_subs WHERE endpoint=?", (endpoint,))


def kv_get(key: str):
    with _LOCK, _conn() as c:
        r = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return r["v"] if r else None


def kv_set(key: str, value: str) -> None:
    with _LOCK, _conn() as c:
        c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?,?)", (key, value))


# --- admin: broadcasts, sales/revenue, audit, customer mgmt ------------------
def add_broadcast(message: str, level: str = "info") -> int:
    with _LOCK, _conn() as c:
        cur = c.execute("INSERT INTO broadcasts(ts, message, level, active) VALUES(?,?,?,1)",
                        (time.time(), message.strip(), level))
        return cur.lastrowid


def active_broadcast() -> Optional[dict]:
    with _LOCK, _conn() as c:
        r = c.execute("SELECT id, ts, message, level FROM broadcasts WHERE active=1 "
                      "ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None


def recent_broadcasts(limit: int = 15) -> list:
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT id, ts, message, level, active FROM broadcasts "
                         "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def deactivate_broadcasts() -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE broadcasts SET active=0 WHERE active=1")


def record_sale(user_id: int, amount: float, method: str = "", note: str = "") -> None:
    if amount <= 0:
        return
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO sales(user_id, ts, amount, method, note) VALUES(?,?,?,?,?)",
                  (user_id, time.time(), float(amount), method[:40], note[:200]))


def revenue_stats() -> dict:
    now = time.time(); day = 86400.0
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT ts, amount FROM sales").fetchall()
    total = m30 = m7 = 0.0
    n = 0
    series = [0.0] * 30
    for r in rows:
        a = r["amount"] or 0.0; total += a; n += 1
        age = now - (r["ts"] or 0)
        if age < 30 * day: m30 += a
        if age < 7 * day: m7 += a
        idx = int((r["ts"] - (now - 30 * day)) / day)
        if 0 <= idx < 30:
            series[idx] += a
    return {"total": round(total, 2), "last_30d": round(m30, 2), "last_7d": round(m7, 2),
            "sales": n, "series": [round(x, 2) for x in series]}


def record_audit(admin_email: str, action: str, target_id=None, detail: str = "") -> None:
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO audit(ts, admin_email, action, target_id, detail) VALUES(?,?,?,?,?)",
                  (time.time(), (admin_email or "")[:120], action[:40], target_id, (detail or "")[:200]))


def recent_audit(limit: int = 30) -> list:
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT ts, admin_email, action, target_id, detail FROM audit "
                         "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def set_admin_note(user_id: int, note: str) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET admin_note=? WHERE id=?", ((note or "")[:1000], user_id))


def set_licence_until(user_id: int, ts: float) -> None:
    with _LOCK, _conn() as c:
        plan = "paid" if ts > time.time() else "trial"
        c.execute("UPDATE users SET licence_until=?, plan=? WHERE id=?", (float(ts), plan, user_id))


def admin_update_user(user_id: int, email=None, first_name=None, last_name=None) -> None:
    with _LOCK, _conn() as c:
        if email:
            c.execute("UPDATE users SET email=? WHERE id=?", (email.strip().lower(), user_id))
        if first_name is not None or last_name is not None:
            c.execute("UPDATE users SET first_name=COALESCE(?, first_name), last_name=COALESCE(?, last_name) WHERE id=?",
                      (first_name, last_name, user_id))


def delete_user(user_id: int) -> None:
    with _LOCK, _conn() as c:
        for t in ("trades", "equity", "resets", "logins", "push_subs", "sales"):
            c.execute(f"DELETE FROM {t} WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM users WHERE id=?", (user_id,))


def all_telegram_targets() -> list:
    """(token, chat) pairs for every user who configured Telegram — for broadcasts."""
    out = []
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT settings FROM users WHERE settings LIKE '%telegram_token%'").fetchall()
    for r in rows:
        try:
            st = json.loads(r["settings"] or "{}")
            tok = (st.get("telegram_token") or "").strip()
            chat = str(st.get("telegram_chat") or "").strip()
            if tok and chat:
                out.append((tok, chat))
        except Exception:
            continue
    return out


def all_push_subs() -> list:
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT endpoint, sub FROM push_subs").fetchall()
        return [dict(r) for r in rows]


# --- admin-managed global bot strategy + per-customer unlock -----------------
def get_global_strategy() -> dict:
    raw = kv_get("global_strategy")
    if raw:
        try:
            g = json.loads(raw)
            g.setdefault("params", {}); g.setdefault("timeframe", "15m")
            g.setdefault("symbols", "BTC/USDT"); g.setdefault("version", 0)
            return g
        except Exception:
            pass
    return {"params": {}, "timeframe": "15m", "symbols": "BTC/USDT", "version": 0, "updated": 0}


def set_global_strategy(params: dict, timeframe: str, symbols: str) -> dict:
    cur = get_global_strategy()
    g = {"params": params or {}, "timeframe": timeframe or "15m",
         "symbols": symbols or "BTC/USDT", "version": int(cur.get("version", 0)) + 1,
         "updated": time.time()}
    kv_set("global_strategy", json.dumps(g))
    return g


def set_allow_custom(user_id: int, allow: bool) -> None:
    with _LOCK, _conn() as c:
        if allow:
            c.execute("UPDATE users SET allow_custom=1, custom_requested=0 WHERE id=?", (user_id,))
        else:
            c.execute("UPDATE users SET allow_custom=0 WHERE id=?", (user_id,))


def request_custom(user_id: int, reason: str = "") -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET custom_requested=?, custom_reason=? WHERE id=?",
                  (time.time(), (reason or "")[:300], user_id))


def clear_custom_request(user_id: int) -> None:
    with _LOCK, _conn() as c:
        c.execute("UPDATE users SET custom_requested=0 WHERE id=?", (user_id,))


def pending_custom_requests() -> list:
    with _LOCK, _conn() as c:
        rows = c.execute(
            "SELECT id, email, first_name, last_name, custom_requested, custom_reason "
            "FROM users WHERE custom_requested>0 AND allow_custom=0 ORDER BY custom_requested DESC"
        ).fetchall()
        return [dict(r) for r in rows]
