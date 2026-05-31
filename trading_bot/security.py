"""Security layer: PIN-protected, encrypted credential storage.

API key + secret are encrypted with Fernet (AES-128-CBC + HMAC). The Fernet key
is derived from the user's PIN/password via PBKDF2-HMAC-SHA256 with a random,
per-install salt. The PIN itself is never stored; we verify it by attempting to
decrypt a known token.

If the PIN is wrong, decryption raises and we treat it as an auth failure.
"""

import base64
import json
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from config import CREDENTIALS_FILE, SALT_FILE

# A constant we encrypt under the derived key so we can verify a PIN without
# storing it. Decrypting this back to the sentinel proves the PIN is correct.
_VERIFY_SENTINEL = b"trading-bot-pin-ok"
_PBKDF2_ITERATIONS = 390_000


def _get_salt() -> bytes:
    """Return the per-install salt, creating it on first run."""
    if os.path.exists(SALT_FILE):
        with open(SALT_FILE, "rb") as fh:
            return fh.read()
    salt = os.urandom(16)
    with open(SALT_FILE, "wb") as fh:
        fh.write(salt)
    return salt


def _derive_key(pin: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_get_salt(),
        iterations=_PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(pin.encode("utf-8")))


def is_initialised() -> bool:
    """True if credentials have already been saved (i.e. a PIN exists)."""
    return os.path.exists(CREDENTIALS_FILE)


def save_credentials(pin: str, payload: dict) -> None:
    """Encrypt ``payload`` (dict of secrets/settings) under ``pin`` and store it.

    A verification sentinel is bundled in so the PIN can later be checked.
    """
    fernet = Fernet(_derive_key(pin))
    blob = {
        "verify": base64.b64encode(_VERIFY_SENTINEL).decode(),
        "data": payload,
    }
    token = fernet.encrypt(json.dumps(blob).encode("utf-8"))
    # Write atomically to avoid corrupting the store on a crash mid-write.
    tmp = CREDENTIALS_FILE + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(token)
    os.replace(tmp, CREDENTIALS_FILE)


def verify_pin(pin: str) -> bool:
    """Return True if ``pin`` correctly decrypts the stored credentials."""
    try:
        load_credentials(pin)
        return True
    except (InvalidToken, ValueError, KeyError):
        return False


def load_credentials(pin: str) -> dict:
    """Decrypt and return the stored payload. Raises InvalidToken on bad PIN."""
    if not is_initialised():
        return {}
    with open(CREDENTIALS_FILE, "rb") as fh:
        token = fh.read()
    fernet = Fernet(_derive_key(pin))
    blob = json.loads(fernet.decrypt(token).decode("utf-8"))
    if base64.b64decode(blob["verify"]) != _VERIFY_SENTINEL:
        raise InvalidToken("sentinel mismatch")
    return blob.get("data", {})


def change_pin(old_pin: str, new_pin: str) -> bool:
    """Re-encrypt the stored payload under a new PIN. Returns success."""
    try:
        data = load_credentials(old_pin)
    except InvalidToken:
        return False
    save_credentials(new_pin, data)
    return True
