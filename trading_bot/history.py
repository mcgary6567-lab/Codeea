"""Persistent trade history and analytics (SQLite, stdlib).

Two tables:
  * ``trades``  – one row per order event (entry, SL, TP, close, rejection).
  * ``equity``  – periodic balance + open-PnL snapshots for the equity curve.

All functions are safe to call from the backend worker thread. SQLite is opened
per-call with a short timeout to avoid cross-thread handle issues.
"""

from __future__ import annotations

import sqlite3
import time
from typing import List, Tuple

from config import HISTORY_DB


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(HISTORY_DB, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, source TEXT, symbol TEXT, side TEXT, kind TEXT,
                size REAL, price REAL, status TEXT, pnl REAL, message TEXT
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS equity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, balance REAL, pnl REAL
            )"""
        )


def record_trade(
    source: str, symbol: str, side: str, kind: str,
    size: float, price: float, status: str, pnl: float = 0.0, message: str = "",
) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO trades (ts, source, symbol, side, kind, size, price, status, pnl, message)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (time.time(), source, symbol, side, kind, size, price, status, pnl, message),
        )


def record_equity(balance: float, pnl: float) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO equity (ts, balance, pnl) VALUES (?,?,?)",
            (time.time(), balance, pnl),
        )


def fetch_equity(limit: int = 1000) -> List[Tuple[float, float]]:
    """Return [(ts, balance), ...] oldest-first for the equity curve."""
    with _conn() as c:
        rows = c.execute(
            "SELECT ts, balance FROM (SELECT * FROM equity ORDER BY id DESC LIMIT ?) ORDER BY ts ASC",
            (limit,),
        ).fetchall()
    return [(r["ts"], r["balance"]) for r in rows]


def fetch_trades(limit: int = 5000) -> list:
    """Return recorded trade rows (newest first) for CSV/tax export."""
    with _conn() as c:
        rows = c.execute(
            "SELECT ts, source, symbol, side, kind, size, price, status, pnl, message "
            "FROM trades ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def stats_by_symbol(limit: int = 12) -> list:
    """Per-symbol breakdown: trades, wins, realized PnL (from 'close' rows)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT symbol, COUNT(*) trades, "
            "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) wins, "
            "SUM(pnl) realized "
            "FROM trades WHERE kind='close' GROUP BY symbol "
            "ORDER BY realized DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def pnl_by_day(days: int = 30) -> list:
    """Realized PnL per day (UTC) from closed trades, oldest first."""
    with _conn() as c:
        rows = c.execute(
            "SELECT strftime('%Y-%m-%d', ts, 'unixepoch') day, SUM(pnl) pnl "
            "FROM trades WHERE kind='close' GROUP BY day ORDER BY day DESC LIMIT ?",
            (days,),
        ).fetchall()
    return list(reversed([dict(r) for r in rows]))


def summary_for_day(day: str) -> dict:
    """Closed-trade summary for a local 'YYYY-MM-DD' day (for the daily recap)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT pnl FROM trades WHERE kind='close' "
            "AND strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime')=?",
            (day,),
        ).fetchall()
        entries = c.execute(
            "SELECT COUNT(*) n FROM trades WHERE kind='entry' "
            "AND strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime')=?",
            (day,),
        ).fetchone()["n"]
    pnls = [r["pnl"] for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "day": day,
        "entries": entries,
        "closed": len(pnls),
        "wins": wins,
        "losses": len(pnls) - wins,
        "realized": sum(pnls),
        "best": max(pnls) if pnls else 0.0,
        "worst": min(pnls) if pnls else 0.0,
    }


def stats() -> dict:
    """Aggregate analytics over recorded trades."""
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"]
        filled = c.execute(
            "SELECT COUNT(*) n FROM trades WHERE status IN ('Filled','Simulated')"
        ).fetchone()["n"]
        rejected = c.execute(
            "SELECT COUNT(*) n FROM trades WHERE status='Rejected'"
        ).fetchone()["n"]
        # 'close' rows carry the realized PnL estimate for a position.
        closes = c.execute(
            "SELECT pnl FROM trades WHERE kind='close'"
        ).fetchall()
    pnls = [r["pnl"] for r in closes]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p <= 0)
    realized = sum(pnls)
    closed = len(pnls)
    return {
        "total": total,
        "filled": filled,
        "rejected": rejected,
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / closed * 100.0) if closed else 0.0,
        "realized_pnl": realized,
        "best": max(pnls) if pnls else 0.0,
        "worst": min(pnls) if pnls else 0.0,
    }
