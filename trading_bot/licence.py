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

import hashlib
import json
import platform
import urllib.parse
import urllib.request
import uuid
from typing import Tuple


def _sibling_endpoint(relay_url: str, name: str) -> str:
    """Swap the final path segment of ``relay_url`` for ``name`` (same host).

    Keeps companion endpoints on the same host so self-hosters who change the
    relay URL get them automatically (``…/poll.php`` -> ``…/verify.php``)."""
    parts = urllib.parse.urlsplit(relay_url.strip())
    path = parts.path or ""
    if "/" in path:
        path = path.rsplit("/", 1)[0] + "/" + name
    else:
        path = "/" + name
    return urllib.parse.urlunsplit((parts.scheme or "https", parts.netloc, path, "", ""))


def verify_url_from_relay(relay_url: str) -> str:
    """Derive the ``verify.php`` URL from the configured relay (poll) URL."""
    return _sibling_endpoint(relay_url, "verify.php")


def trial_url_from_relay(relay_url: str) -> str:
    """Derive the ``trial.php`` URL from the configured relay (poll) URL."""
    return _sibling_endpoint(relay_url, "trial.php")


def reset_url_from_relay(relay_url: str) -> str:
    """Derive the ``reset.php`` (PIN-reset claim) URL from the relay (poll) URL."""
    return _sibling_endpoint(relay_url, "reset.php")


def machine_fingerprint() -> str:
    """Stable, privacy-preserving machine id (sha256 hex) for trial anti-abuse.

    Derived from the MAC-based node id, hostname and platform — hashed, so the
    relay only ever stores an opaque digest, never raw machine identifiers."""
    raw = "|".join([
        str(uuid.getnode()),
        platform.node() or "",
        platform.system() or "",
        platform.machine() or "",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def licence_status(verify_url: str, token: str, timeout: float = 10.0) -> dict:
    """Full licence status as a dict:

    ``{"status", "message", "expires_at", "trial"}`` where status is one of
    ``"ok"`` / ``"rejected"`` / ``"error"`` and ``expires_at`` is an epoch
    second (0 = never)."""
    token = (token or "").strip()
    if not token:
        return {"status": "rejected", "message": "no licence token", "expires_at": 0, "trial": False}
    if not verify_url:
        return {"status": "error", "message": "no licence server configured", "expires_at": 0, "trial": False}

    url = verify_url + ("&" if "?" in verify_url else "?") + "token=" + urllib.parse.quote(token)
    req = urllib.request.Request(url, headers={"User-Agent": "PrometheusBot"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The server answered (e.g. 403) — read its JSON reason; this is an
        # explicit rejection, not a connectivity problem.
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"status": "rejected", "message": f"licence rejected (HTTP {exc.code})",
                    "expires_at": 0, "trial": False}
        return {"status": "rejected",
                "message": str(data.get("error") or f"licence rejected (HTTP {exc.code})"),
                "expires_at": 0, "trial": False}
    except Exception as exc:  # noqa: BLE001 - network/parse problem = unreachable
        return {"status": "error", "message": f"licence server unreachable ({exc})",
                "expires_at": 0, "trial": False}

    if data.get("ok"):
        return {"status": "ok", "message": "licence valid",
                "expires_at": int(data.get("expires_at") or 0), "trial": bool(data.get("trial"))}
    return {"status": "rejected", "message": str(data.get("error") or "licence rejected"),
            "expires_at": 0, "trial": False}


def verify(verify_url: str, token: str, timeout: float = 10.0) -> Tuple[str, str, int]:
    """Back-compat wrapper around :func:`licence_status`.

    Returns ``(status, message, expires_at)``."""
    st = licence_status(verify_url, token, timeout)
    return st["status"], st["message"], st["expires_at"]


def start_trial(trial_url: str, email: str, machine: str,
                timeout: float = 15.0) -> Tuple[str, str, str, int]:
    """Request a self-service free trial from ``trial.php``.

    Returns ``(status, message, token, expires_at)`` where status is one of
    ``"ok"`` / ``"used"`` (trial already taken) / ``"error"``."""
    if not trial_url:
        return "error", "no licence server configured", "", 0
    body = urllib.parse.urlencode({"email": (email or "").strip(), "machine": machine}).encode("utf-8")
    req = urllib.request.Request(trial_url, data=body, headers={"User-Agent": "PrometheusBot"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return "error", f"trial request failed (HTTP {exc.code})", "", 0
        status = "used" if str(data.get("code")) == "used" else "error"
        return status, str(data.get("error") or "trial request rejected"), "", 0
    except Exception as exc:  # noqa: BLE001 - network/parse problem = unreachable
        return "error", f"trial server unreachable ({exc})", "", 0

    if data.get("ok"):
        return "ok", "trial started", str(data.get("token") or ""), int(data.get("expires_at") or 0)
    status = "used" if str(data.get("code")) == "used" else "error"
    return status, str(data.get("error") or "trial request rejected"), "", 0


def request_pin_reset(reset_url: str, email: str, machine: str,
                      timeout: float = 15.0) -> Tuple[str, str, str, int]:
    """Claim an admin-authorised PIN reset from ``reset.php``.

    The admin authorises the reset in the panel; the app then proves identity
    with the registered email and gets the licence token back so it can rebuild
    its vault under a new PIN. Returns ``(status, message, token, expires_at)``
    where status is ``"ok"`` / ``"denied"`` (no/expired authorisation, unknown
    email) / ``"error"`` (server unreachable)."""
    if not reset_url:
        return "error", "no licence server configured", "", 0
    body = urllib.parse.urlencode(
        {"email": (email or "").strip(), "machine": machine}).encode("utf-8")
    req = urllib.request.Request(reset_url, data=body, headers={"User-Agent": "PrometheusBot"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The server answered with a reason (e.g. 403 not authorised) — surface it.
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return "error", f"reset request failed (HTTP {exc.code})", "", 0
        return "denied", str(data.get("error") or "reset not authorised"), "", 0
    except Exception as exc:  # noqa: BLE001 - network/parse problem = unreachable
        return "error", f"reset server unreachable ({exc})", "", 0

    if data.get("ok") and data.get("token"):
        return "ok", "reset authorised", str(data.get("token")), int(data.get("expires_at") or 0)
    return "denied", str(data.get("error") or "reset not authorised"), "", 0
