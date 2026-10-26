"""A small in-memory rate limiter for the public forms (sign-in, registration, password API).

Per-account lockout (MAX_LOGIN_ATTEMPTS) stops guessing one password; this stops one address trying many accounts
(password spraying) and flooding the public registration form. It lives in the process, which is enough because
the app runs one worker (WEB_CONCURRENCY=1, see docker-compose.yml); a restart clears it.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindow:
    def __init__(self, limit: int, seconds: int):
        self.limit, self.seconds = limit, seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, q: deque, now: float) -> None:
        while q and now - q[0] > self.seconds:
            q.popleft()

    def blocked(self, key: str) -> int:
        """Seconds until ``key`` may try again (0 = allowed now)."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return 0
            self._trim(q, now)
            if len(q) < self.limit:
                return 0
            return max(1, int(self.seconds - (now - q[0])))

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            self._trim(q, now)
            q.append(now)
            if len(self._hits) > 5000:  # keep memory bounded under a scan
                for k in [k for k, v in self._hits.items() if not v][:2500]:
                    self._hits.pop(k, None)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


# failed sign-ins per network address: 30 in 10 minutes
LOGIN_FAILURES = SlidingWindow(limit=30, seconds=600)
# public registrations per network address: 20 an hour
REGISTRATIONS = SlidingWindow(limit=20, seconds=3600)
