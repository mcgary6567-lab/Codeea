"""High-impact economic-news calendar + trading blackout.

A tiny, self-contained economic-calendar client used by the built-in
strategy to avoid opening trades around high-impact macro events
(FOMC / rate decisions, CPI, NFP, GDP, PCE ...). Those releases produce
violent, low-liquidity spikes that stop-hunt tight stops and blow through
slippage limits — most professional desks simply stand aside for a window
around them.

Behaviour
---------
* Source: the public ForexFactory weekly calendar JSON feed. We keep only
  ``impact == "High"`` events. USD macro drives crypto the most, but *any*
  high-impact print can whip risk assets, so all high-impact events count.
* A single background refresh (TTL-gated, non-blocking) keeps the calendar
  fresh; snapshot/strategy reads only ever touch the in-memory cache.
* ``blackout(now)`` returns the active event when ``now`` is within the
  ±window (default 60 min) of a high-impact event, else ``None``.
* Fail-open: if the feed can't be reached the cache simply goes stale and
  the bot keeps trading (a halted bot is worse than an unfiltered one).
  ``status()`` reports ``stale`` so the UI can flag it.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
WINDOW_SEC = 60 * 60          # pause 1h before AND 1h after a high-impact event
_TTL_SEC = 3 * 60 * 60        # refresh the calendar at most every 3 hours
_TIMEOUT = 12

_lock = threading.Lock()
_events: list[dict] = []       # [{"ts": epoch, "title": str, "country": str}]
_fetched_at: float = 0.0
_refreshing = False
_ok = False                    # True once we've fetched at least one good calendar


def _parse(raw: bytes) -> list[dict]:
    out = []
    for e in json.loads(raw.decode("utf-8", "replace")):
        if str(e.get("impact", "")).lower() != "high":
            continue
        ds = e.get("date") or ""
        try:
            dt = datetime.fromisoformat(ds)
            ts = dt.timestamp()
        except Exception:
            continue
        out.append({
            "ts": ts,
            "title": str(e.get("title", "Economic event")).strip(),
            "country": str(e.get("country", "")).strip().upper(),
        })
    out.sort(key=lambda x: x["ts"])
    return out


def _do_fetch() -> None:
    global _events, _fetched_at, _refreshing, _ok
    try:
        req = urllib.request.Request(FEED_URL, headers={"User-Agent": "Mozilla/5.0 (news-filter)"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = _parse(r.read())
        with _lock:
            if data:
                _events = data
                _ok = True
            _fetched_at = time.time()
    except Exception:
        with _lock:
            _fetched_at = time.time()   # back off; keep any prior good cache
    finally:
        with _lock:
            _refreshing = False


def _maybe_refresh(now: float) -> None:
    """Kick a background refresh if the cache is older than the TTL. Never blocks."""
    global _refreshing
    with _lock:
        if _refreshing or (now - _fetched_at) < _TTL_SEC:
            return
        _refreshing = True
    threading.Thread(target=_do_fetch, name="news-refresh", daemon=True).start()


def blackout(now: float | None = None, window: int = WINDOW_SEC) -> dict | None:
    """The active high-impact event if ``now`` is inside its ±window, else None.

    Returns ``{"title","country","ts","mins","phase"}`` where ``mins`` is
    signed minutes to the event (>0 before, <0 after) and ``phase`` is
    ``"pre"`` or ``"post"``.
    """
    now = time.time() if now is None else now
    _maybe_refresh(now)
    with _lock:
        evs = list(_events)
    best = None
    for e in evs:
        d = e["ts"] - now
        if abs(d) <= window:
            # prefer the closest event to "now"
            if best is None or abs(d) < abs(best["ts"] - now):
                best = e
    if not best:
        return None
    d = best["ts"] - now
    return {"title": best["title"], "country": best["country"], "ts": best["ts"],
            "mins": int(round(d / 60.0)), "phase": "pre" if d >= 0 else "post"}


def next_event(now: float | None = None) -> dict | None:
    now = time.time() if now is None else now
    _maybe_refresh(now)
    with _lock:
        evs = list(_events)
    for e in evs:
        if e["ts"] > now:
            return {"title": e["title"], "country": e["country"], "ts": e["ts"],
                    "mins": int(round((e["ts"] - now) / 60.0))}
    return None


def status(now: float | None = None, *, protect: bool = False, window: int = WINDOW_SEC) -> dict:
    """UI-facing snapshot of the news filter.

    ``protect`` = the user's News-trading toggle is OFF (i.e. protection ON).
    """
    now = time.time() if now is None else now
    bl = blackout(now, window)
    nx = next_event(now)
    with _lock:
        stale = (not _ok) or ((now - _fetched_at) > _TTL_SEC * 2 and _fetched_at > 0)
    return {
        "protect": bool(protect),
        "blackout": bool(bl and protect),   # only "active" when protection is on
        "in_window": bool(bl),              # inside a window regardless of the toggle
        "event": bl,                        # active event (or None)
        "next": nx,                         # next upcoming high-impact event
        "window_min": window // 60,
        "stale": bool(stale),
    }
