"""Licence verification for the built-in strategy engine.

The cloud relay already gates *cloud signals* behind a per-customer token; this
extends the same token to the **built-in strategy** so it, too, requires an
active subscription. The bot calls ``verify`` against the relay's ``verify.php``
endpoint (read-only — it never disturbs cloud-signal delivery) and the strategy
runner refuses to trade without a valid licence.

``verify`` returns a coarse status the runner can act on:

* ``"ok"``        – token is valid (and not expired).
* ``"rejected"``  – the server explicitly refused (missing/invalid/disabled/
  expired token). Hard deny.
* ``"error"``     – the server couldn't be reached (offline / DNS / timeout).
  The runner applies a grace period so a transient outage doesn't strand a
  legitimately-licensed user mid-session.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Tuple


def verify_url_from_relay(relay_url: str) -> str:
    """Derive the ``verify.php`` URL from the configured relay (poll) URL.

    Keeps both endpoints on the same host so self-hosters who change the relay
    URL get verification automatically (``…/poll.php`` -> ``…/verify.php``)."""
    parts = urllib.parse.urlsplit(relay_url.strip())
    path = parts.path or ""
    if "/" in path:
        path = path.rsplit("/", 1)[0] + "/verify.php"
    else:
        path = "/verify.php"
    return urllib.parse.urlunsplit((parts.scheme or "https", parts.netloc, path, "", ""))


def verify(verify_url: str, token: str, timeout: float = 10.0) -> Tuple[str, str, int]:
    """Check ``token`` against ``verify_url``.

    Returns ``(status, message, expires_at)`` where status is one of
    ``"ok"`` / ``"rejected"`` / ``"error"`` and ``expires_at`` is an epoch
    second (0 = never).
    """
    token = (token or "").strip()
    if not token:
        return "rejected", "no licence token", 0
    if not verify_url:
        return "error", "no licence server configured", 0

    url = verify_url + ("&" if "?" in verify_url else "?") + "token=" + urllib.parse.quote(token)
    req = urllib.request.Request(url, headers={"User-Agent": "PrometheusBot"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The server answered (e.g. 403) — try to read its JSON reason; this is
        # an explicit rejection, not a connectivity problem.
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return "rejected", f"licence rejected (HTTP {exc.code})", 0
        return "rejected", str(data.get("error") or f"licence rejected (HTTP {exc.code})"), 0
    except Exception as exc:  # noqa: BLE001 - network/parse problem = unreachable
        return "error", f"licence server unreachable ({exc})", 0

    if data.get("ok"):
        return "ok", "licence valid", int(data.get("expires_at") or 0)
    return "rejected", str(data.get("error") or "licence rejected"), 0
