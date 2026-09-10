"""Password hashing, JWT tokens, session cookies, API keys, 2FA (TOTP)."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt

from app.config import settings

SESSION_COOKIE = "oqc_session"
ALGORITHM = "HS256"


# ----------------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def password_strength_errors(password: str) -> list[str]:
    errors = []
    if len(password) < 8:
        errors.append("At least 8 characters")
    if not any(c.isdigit() for c in password):
        errors.append("At least one digit")
    if not any(c.isalpha() for c in password):
        errors.append("At least one letter")
    return errors


# ----------------------------------------------------------------------------- JWT
def create_access_token(user_id: int, jti: Optional[str] = None, minutes: Optional[int] = None, extra: Optional[dict] = None) -> str:
    now = int(time.time())  # epoch seconds (timezone-safe)
    payload = {
        "sub": str(user_id),
        "jti": jti or secrets.token_hex(16),
        "iat": now,
        "exp": now + 60 * (minutes or settings.ACCESS_TOKEN_MINUTES),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


# ----------------------------------------------------------------------------- API keys
def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, hash)."""
    raw = "oqc_" + secrets.token_urlsafe(32)
    return raw, raw[:12], hashlib.sha256(raw.encode()).hexdigest()


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ----------------------------------------------------------------------------- signed tokens (email links, survey links)
def sign_value(value: str, purpose: str = "generic") -> str:
    sig = hmac.new(settings.SECRET_KEY.encode(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{value}.{sig}"


def verify_signed(signed: str, purpose: str = "generic") -> Optional[str]:
    if "." not in signed:
        return None
    value, sig = signed.rsplit(".", 1)
    expected = hmac.new(settings.SECRET_KEY.encode(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()[:32]
    return value if hmac.compare_digest(sig, expected) else None


# ----------------------------------------------------------------------------- TOTP (2FA) – RFC 6238, no external deps
def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("utf-8").rstrip("=")


def _totp_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = (struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_now(secret: str) -> str:
    return _totp_at(secret, int(time.time()) // 30)


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    counter = int(time.time()) // 30
    code = (code or "").strip().replace(" ", "")
    return any(hmac.compare_digest(_totp_at(secret, counter + i), code) for i in range(-window, window + 1))


def totp_uri(secret: str, account: str, issuer: str = "OnlineQuranCollege") -> str:
    return f"otpauth://totp/{issuer}:{account}?secret={secret}&issuer={issuer}&digits=6&period=30"


def mask(value: Optional[str], keep: int = 3) -> str:
    """Institutional masking for sensitive data (phones, CNIC, emails)."""
    if not value:
        return "—"
    if "@" in value:
        name, _, domain = value.partition("@")
        return (name[:2] + "***@" + domain) if len(name) > 2 else "***@" + domain
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]
