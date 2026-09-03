"""Optional Web Push (VAPID) sender. Degrades gracefully if pywebpush is absent.

Keys are auto-generated once with the (already-required) cryptography lib and
persisted in the kv table, so no manual VAPID setup is needed.
"""
from __future__ import annotations

import base64
import json
import threading

from . import store

try:
    from pywebpush import webpush as _webpush, WebPushException
    _HAS = True
except Exception:                       # pywebpush not installed -> push disabled
    _HAS = False

_CLAIM = {"sub": "mailto:admin@trevolto.com"}
_BRAND = "Trevolto"


def available() -> bool:
    return _HAS


def _keys():
    """(private_pem, public_b64url) — generate + persist once."""
    priv = store.kv_get("vapid_private")
    pub = store.kv_get("vapid_public")
    if priv and pub:
        return priv, pub
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        k = ec.generate_private_key(ec.SECP256R1())
        priv = k.private_bytes(serialization.Encoding.PEM,
                               serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption()).decode()
        raw = k.public_key().public_bytes(serialization.Encoding.X962,
                                          serialization.PublicFormat.UncompressedPoint)
        pub = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        store.kv_set("vapid_private", priv)
        store.kv_set("vapid_public", pub)
        return priv, pub
    except Exception:
        return None, None


def public_key() -> str:
    if not _HAS:
        return ""
    _, pub = _keys()
    return pub or ""


def push_to_user(user_id: int, message: str) -> None:
    if not _HAS:
        return
    threading.Thread(target=_send_all, args=(user_id, message), daemon=True).start()


def push_to_all(message: str) -> None:
    if not _HAS:
        return
    threading.Thread(target=_send_broadcast, args=(message,), daemon=True).start()


def _send_broadcast(message: str) -> None:
    priv, _ = _keys()
    if not priv:
        return
    payload = json.dumps({"title": _BRAND, "body": message})
    for row in store.all_push_subs():
        try:
            _webpush(subscription_info=json.loads(row["sub"]), data=payload,
                     vapid_private_key=priv, vapid_claims=dict(_CLAIM))
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            if code in (404, 410):
                store.remove_push_sub(row["endpoint"])
        except Exception:
            pass


def _send_all(user_id: int, message: str) -> None:
    priv, _ = _keys()
    if not priv:
        return
    payload = json.dumps({"title": _BRAND, "body": message})
    for row in store.push_subs_for(user_id):
        try:
            _webpush(subscription_info=json.loads(row["sub"]), data=payload,
                     vapid_private_key=priv, vapid_claims=dict(_CLAIM))
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            if code in (404, 410):
                store.remove_push_sub(row["endpoint"])
        except Exception:
            pass
