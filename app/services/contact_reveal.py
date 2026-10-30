"""Client contacts behind a one-time password, as the ERP keeps them behind its "OTP Page".

A client's email, cell, WhatsApp and extra contact rows show masked. When Configuration › OTP is enabled for the
viewer's role (an empty role list means every staff role), "View" sends a code to the viewer's own email, WhatsApp
or SMS (the channel on that page) and the value opens only once the code is typed. Someone with an authenticator
app may type its code instead, which also covers a server whose email / WhatsApp is not connected yet. Every code
sent, every wrong code and every value opened is written to the audit log.

With reveal_window_minutes above 0 an accepted code keeps further contacts open for that long; 0 asks every time.
With OTP disabled the View link opens the value straight away (still audited), as before.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import mask, verify_totp
from app.models.config_erp import ContactRevealChallenge, OtpConfiguration
from app.models.core import User

log = logging.getLogger("oqc.contact_reveal")

FIELDS = ("email", "phone", "whatsapp")
FIELD_LABELS = {"email": "Primary Email", "phone": "Primary Cell", "whatsapp": "WhatsApp"}


class RevealError(Exception):
    """A message for the dialog: no address to send to, too soon to resend, wrong / expired code."""

    def __init__(self, message: str, challenge: Optional["ContactRevealChallenge"] = None):
        super().__init__(message)
        self.challenge = challenge  # the code still open for this same contact, so the dialog can take it


def config(db: Session) -> Optional[OtpConfiguration]:
    return db.query(OtpConfiguration).order_by(OtpConfiguration.id).first()


def otp_required(db: Session, user: User) -> bool:
    cfg = config(db)
    if not cfg or not cfg.is_enabled:
        return False
    roles = cfg.required_for_roles or []
    return not roles or user.role_slug in roles


def unlocked(db: Session, user: User, now: Optional[datetime] = None) -> bool:
    """True while an accepted code still covers this user (the reveal window on Configuration › OTP)."""
    cfg = config(db)
    minutes = (cfg.reveal_window_minutes or 0) if cfg else 0
    if minutes <= 0:
        return False
    since = (now or datetime.utcnow()) - timedelta(minutes=minutes)
    return db.query(ContactRevealChallenge.id).filter(ContactRevealChallenge.user_id == user.id,
                                                      ContactRevealChallenge.verified_at >= since).first() is not None


def needs_code(db: Session, user: User) -> bool:
    return otp_required(db, user) and not unlocked(db, user)


def _hash(challenge_salt: str, code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"{challenge_salt}:{code.strip()}".encode(), hashlib.sha256).hexdigest()


def new_code(length: int) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(max(4, min(length or 6, 10))))


def _destination(db: Session, cfg: OtpConfiguration, user: User) -> tuple[str, str]:
    """(channel, address) on the viewer's own account: the configured channel when they have a number for it, else email."""
    from app.models.people import Employee
    number = user.phone or db.query(Employee.phone).filter(Employee.user_id == user.id).scalar()
    if cfg.channel in ("whatsapp", "sms") and number:
        return cfg.channel, number
    if user.email:
        return "email", user.email
    raise RevealError("Your account has no email or phone to send a code to. Ask HR to add one, or turn on an authenticator app on your profile.")


def start(db: Session, user: User, client_id: int, field: str, now: Optional[datetime] = None) -> ContactRevealChallenge:
    """Send a fresh code for one contact of one client, unless the last one went out too recently."""
    from app.services import integrations
    now = now or datetime.utcnow()
    cfg = config(db)
    last = (db.query(ContactRevealChallenge).filter(ContactRevealChallenge.user_id == user.id)
            .order_by(ContactRevealChallenge.id.desc()).first())
    wait = (cfg.resend_after_seconds or 0) - int((now - last.created_at).total_seconds()) if last else 0
    if last and wait > 0 and last.verified_at is None:
        same = last.client_id == client_id and last.field == field and now <= last.expires_at
        raise RevealError(f"A code was sent {int((now - last.created_at).total_seconds())} seconds ago. "
                          + ("Type it below" if same else "Use it") + f", or wait {wait} seconds for a new one.", challenge=last if same else None)
    channel, address = _destination(db, cfg, user)
    code = new_code(cfg.code_length)
    salt = secrets.token_hex(8)
    ch = ContactRevealChallenge(user_id=user.id, client_id=client_id, field=field, channel=channel, sent_to=mask(address),
                                code_hash=f"{salt}${_hash(salt, code)}", attempts=0, created_at=now, updated_at=now,
                                expires_at=now + timedelta(minutes=cfg.validity_minutes or 10))
    db.add(ch)
    db.flush()
    body = (f"Your Online Quran College code is {code}. It opens one client contact and expires in "
            f"{cfg.validity_minutes or 10} minutes. If you did not ask for it, tell your manager.")
    try:
        if channel == "email":
            sent = integrations.send_email(db, address, "Your one-time code", body)
        elif channel == "whatsapp":
            sent = integrations.send_whatsapp(db, address, body, purpose="general")
        else:
            sent = integrations.send_sms(db, address, body)
    except Exception as exc:  # a dead gateway is the dialog's message, not a 500
        log.warning("contact code to user %s failed: %s", user.id, exc)
        raise RevealError(f"The code could not be sent by {channel} ({exc.__class__.__name__}). "
                          + ("Type your authenticator code instead." if user.two_factor_enabled else "Try again shortly."))
    if sent.get("simulated"):
        # no gateway connected: the message only reached the server log, so say so instead of waiting on it
        if settings.APP_ENV != "production":
            log.info("[DEV] contact code for user %s: %s", user.id, code)
        ch.channel = f"{channel}-simulated"
    return ch


def verify(db: Session, user: User, challenge_id: int, code: str, client_id: Optional[int] = None, field: Optional[str] = None,
           now: Optional[datetime] = None) -> ContactRevealChallenge:
    """Accept the emailed / WhatsApp code of this challenge, or the user's authenticator code, for the contact it was sent for."""
    now = now or datetime.utcnow()
    cfg = config(db)
    ch = db.get(ContactRevealChallenge, challenge_id)
    if not ch or ch.user_id != user.id or ch.verified_at is not None:
        raise RevealError("This code request is no longer open. Press View again for a new code.")
    if (client_id is not None and ch.client_id != client_id) or (field is not None and ch.field != field):
        raise RevealError("This code was sent for another contact. Press View again.")
    if now > ch.expires_at:
        raise RevealError("The code has expired. Press View again for a new one.")
    if ch.attempts >= (cfg.max_attempts or 5):
        raise RevealError("Too many wrong codes. Press View again for a new one.")
    code = (code or "").strip().replace(" ", "")
    salt, _, digest = ch.code_hash.partition("$")
    ok = bool(code) and (hmac.compare_digest(_hash(salt, code), digest)
                         or (user.two_factor_enabled and bool(user.two_factor_secret) and verify_totp(user.two_factor_secret, code)))
    if not ok:
        ch.attempts += 1
        left = (cfg.max_attempts or 5) - ch.attempts
        raise RevealError("Wrong code." + (f" {left} attempt(s) left." if left > 0 else " Press View again for a new one."))
    ch.verified_at = now
    return ch
