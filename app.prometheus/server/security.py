"""Auth, password hashing, JWT, and API-key encryption for the web app.

Desktop Prometheus protects keys with a local PIN + Fernet. The web version keeps
the same Fernet-at-rest model but the master key lives on the server (set
``PROMETHEUS_SECRET_KEY`` — in production back it with a KMS/secret manager), and
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
    env = os.environ.get("PROMETHEUS_SECRET_KEY")
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
def make_token(user_id: int, email: str) -> str:
    now = int(time.time())
    payload = {"sub": str(user_id), "email": email, "iat": now, "exp": now + _TOKEN_TTL}
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
