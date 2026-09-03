"""Auth, password hashing, JWT, and API-key encryption for the web app.

Desktop Trevolto protects keys with a local PIN + Fernet. The web version keeps
the same Fernet-at-rest model but the master key lives on the server (set
``TREVOLTO_SECRET_KEY`` — in production back it with a KMS/secret manager), and
user identity is an email + password (PBKDF2) issuing short-lived JWTs.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import jwt
from cryptography.fernet import Fernet

from .config_web import DATA_DIR

_ALG = "HS256"
_TOKEN_TTL = 7 * 24 * 3600  # 7 days


def _load_master_key() -> bytes:
    """Fernet master key from env, else a persisted per-install key (dev)."""
    env = os.environ.get("TREVOLTO_SECRET_KEY")
    if env:
        # Accept either a raw Fernet key or any string (hashed to 32 bytes).
        try:
            Fernet(env.encode())
            return env.encode()
        except Exception:
            digest = hashlib.sha256(env.encode()).digest()
            return base64.urlsafe_b64encode(digest)
    path = os.path.join(DATA_DIR, "master.key")
    if os.path.exists(path):
        return open(path, "rb").read().strip()
    key = Fernet.generate_key()
    with open(path, "wb") as fh:
        fh.write(key)
    os.chmod(path, 0o600)
    return key


_FERNET = Fernet(_load_master_key())
_JWT_SECRET = hashlib.sha256(_load_master_key() + b"jwt").hexdigest()


# --- password hashing -------------------------------------------------------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# --- JWT --------------------------------------------------------------------
def make_token(user_id: int, email: str, token_version: int = 0) -> str:
    now = int(time.time())
    payload = {"sub": str(user_id), "email": email, "tv": int(token_version), "iat": now, "exp": now + _TOKEN_TTL}
    return jwt.encode(payload, _JWT_SECRET, algorithm=_ALG)


def read_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _JWT_SECRET, algorithms=[_ALG])
    except Exception:
        return None


# --- API-key encryption -----------------------------------------------------
def encrypt_secret(obj: dict) -> str:
    return _FERNET.encrypt(json.dumps(obj).encode()).decode()


def decrypt_secret(blob: str) -> dict:
    return json.loads(_FERNET.decrypt(blob.encode()).decode())


# --- password-reset tokens --------------------------------------------------
def new_reset_token() -> tuple:
    """Return (raw_token, sha256_hash). Store the hash, email the raw token."""
    raw = os.urandom(24).hex()
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def hash_token(raw: str) -> str:
    return hashlib.sha256((raw or "").encode()).hexdigest()


# --- TOTP (RFC 6238, SHA1, 6 digits, 30s) -----------------------------------
_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def new_totp_secret(length: int = 20) -> str:
    raw = os.urandom(length)
    bits = "".join(f"{b:08b}" for b in raw)
    out = ""
    for i in range(0, len(bits) - 4, 5):
        out += _B32[int(bits[i:i + 5], 2)]
    return out


def _b32decode(secret: str) -> bytes:
    secret = secret.strip().replace(" ", "").upper()
    bits = "".join(f"{_B32.index(c):05b}" for c in secret if c in _B32)
    return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits) - 7, 8))


def totp_at(secret: str, counter: int) -> str:
    key = _b32decode(secret)
    msg = counter.to_bytes(8, "big")
    h = hmac.new(key, msg, hashlib.sha1).digest()
    off = h[-1] & 0x0F
    code = ((h[off] & 0x7F) << 24) | (h[off + 1] << 16) | (h[off + 2] << 8) | h[off + 3]
    return f"{code % 1_000_000:06d}"


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    if not secret or not code:
        return False
    code = code.strip().replace(" ", "")
    counter = int(time.time()) // 30
    for w in range(-window, window + 1):
        if hmac.compare_digest(totp_at(secret, counter + w), code):
            return True
    return False


def totp_uri(secret: str, email: str, issuer: str = "Trevolto") -> str:
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}"


def totp_qr_data_uri(uri: str) -> str:
    """Render an otpauth:// URI as a scannable QR code (SVG data-URI).

    Returns "" if the QR library isn't installed, so 2FA setup still works via
    the manual text secret and the app never crashes on a missing dependency.
    """
    try:
        import segno
    except Exception:  # noqa: BLE001
        return ""
    try:
        qr = segno.make(uri, error="m")
        return qr.svg_data_uri(scale=5, border=2, dark="#0b1220", light="#ffffff")
    except Exception:  # noqa: BLE001
        return ""
