"""Encryption for credentials typed on Configuration › Integrations (Zoom client secret, SMS gateway URL, ...).

Values are sealed with Fernet (AES-128-CBC + HMAC-SHA256) under a key derived from SECRET_KEY, so a copy of the
database alone does not reveal them. If SECRET_KEY changes, sealed values can no longer be opened: ``unseal`` returns
None and the screen asks for them again.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

PREFIX = "fernet:"


def _fernet() -> Fernet:
    digest = hashlib.sha256(("oqc-secret-box:" + settings.SECRET_KEY).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def seal(value: str) -> str:
    return PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def unseal(token: Optional[str]) -> Optional[str]:
    if not token or not str(token).startswith(PREFIX):
        return None
    try:
        return _fernet().decrypt(str(token)[len(PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
