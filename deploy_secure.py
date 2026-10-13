"""Apply the deployment credentials to a seeded instance.

Runs at the end of the Render build and in the container entrypoint (deploy/entrypoint.sh). It replaces
the development passwords that ship in the seed with values taken from the environment, so a public
deployment never keeps the documented `Admin@12345` / `Teacher@123` credentials.

    ADMIN_PASSWORD      password for admin@oqc.local (and any other superuser); forces a change at first login
    DEMO_PASSWORD       shared password for every other seeded account
    ADMIN_EMAIL         optional: rename the owner account to a real address
    ROTATE_CREDENTIALS  "true" re-applies the values even when they have not changed

Applied only when the configured values change. A sha256 fingerprint of (ADMIN_PASSWORD, DEMO_PASSWORD,
ADMIN_EMAIL) is stored in the settings table under ``credentials_fingerprint`` together with the time it
was applied. On the next run the fingerprint is compared first: when it matches and ROTATE_CREDENTIALS is
not "true", nothing is touched - no password is rewritten, no session is revoked - so a redeploy never
resets the password the owner chose at first sign-in or logs everyone out. Changing one of the three
variables on the host (Render: Environment tab, then redeploy; Docker: edit .env, restart) is the
rotation path.

Idempotent: re-running only rewrites passwords, never creates or deletes accounts.

Late seed accounts: the seed runs again on every deploy and re-creates a seeded account an administrator
deleted, with its published development password. When the fingerprint is unchanged, accounts created
after the last application that still carry their seed password get the configured password too
(``secure_late_accounts``), so no deploy can bring a documented password back.
It is a no-op when neither password variable is set, so local development is unaffected.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import hash_password, verify_password
from app.database import SessionLocal
from app.models.core import AuditEvent, Setting, User, UserSession
from app.services.system import get_setting, set_setting

# Accounts that must keep working for a client walkthrough, but with a new shared password.
DEMO_PREFIXES = ("teacher", "parent", "student", "supervisor", "manager", "hr", "finance",
                 "academics", "qa", "tech", "marketing", "billing", "leadgen", "closer",
                 "accountant", "qaofficer", "hrofficer", "coordinator", "auditor", "sysadmin")

FINGERPRINT_KEY = "credentials_fingerprint"


def fingerprint(admin_pw: str, demo_pw: str, admin_email: str) -> str:
    """sha256 over the three configured values; stored instead of the values themselves."""
    h = hashlib.sha256()
    for part in (admin_pw, demo_pw, admin_email):
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")  # separator so ("ab", "c") and ("a", "bc") differ
    return h.hexdigest()


def stored_fingerprint(db: Session) -> dict | None:
    """{"fingerprint": sha256, "applied_at": iso timestamp} from the last application, or None."""
    value = get_setting(db, FINGERPRINT_KEY)
    return value if isinstance(value, dict) and value.get("fingerprint") else None


def seed_password(email: str) -> str | None:
    """The published development password the seed gives this address, or None for a non-seed account."""
    from app.seed.core import DEV_ONLY_USERS, USERS
    known = {e: p for e, p, *_ in USERS + DEV_ONLY_USERS}
    email = (email or "").lower()
    if email in known:
        return known[email]
    local, _, domain = email.partition("@")
    if domain != "oqc.local":
        return None
    return {"teacher": "Teacher@123", "parent": "Parent@123", "student": "Student@123"}.get(local.rstrip("0123456789"))


def secure_late_accounts(db: Session, admin_pw: str, demo_pw: str, applied_at: str | None) -> int:
    """Give the configured passwords to seed accounts created since ``applied_at`` that still have their seed password."""
    if not applied_at:
        return 0
    try:
        since = datetime.fromisoformat(applied_at).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return 0
    fixed = 0
    for u in db.query(User).filter(User.created_at >= since - timedelta(minutes=1)):
        default = seed_password(u.email)
        if not default or not verify_password(default, u.hashed_password):
            continue
        new_pw = admin_pw if u.is_superuser else demo_pw
        if new_pw:
            u.hashed_password = hash_password(new_pw)
            u.must_change_password = bool(u.is_superuser)
        else:  # nothing configured for this kind of account: lock it until an administrator resets it
            u.hashed_password = hash_password(secrets.token_urlsafe(24))
            u.must_change_password = True
        fixed += 1
    if fixed:
        db.add(AuditEvent(actor_name="deploy", action="password_change", module="security", severity="critical",
                          is_consequential=True, description=f"{fixed} re-seeded account(s) lost their development password",
                          rationale="The seed re-created accounts after the last credential rotation"))
        db.flush()
    return fixed


def apply(db: Session, admin_pw: str, demo_pw: str, admin_email: str = "", force: bool = False) -> dict:
    """Apply the credentials inside the caller's transaction (flush only, no commit).

    Returns a dict with ``applied`` (bool) and, when applied, the counts ``superusers``, ``demo_accounts``,
    ``sessions_revoked`` and ``applied_at``; when skipped, ``reason`` is "unchanged" or "not configured".
    """
    admin_pw = (admin_pw or "").strip()
    demo_pw = (demo_pw or "").strip()
    admin_email = (admin_email or "").strip().lower()

    if not admin_pw and not demo_pw:
        return {"applied": False, "reason": "not configured"}

    fp = fingerprint(admin_pw, demo_pw, admin_email)
    previous = stored_fingerprint(db)
    if previous and previous.get("fingerprint") == fp and not force:
        late = secure_late_accounts(db, admin_pw, demo_pw, previous.get("applied_at"))
        return {"applied": False, "reason": "unchanged", "applied_at": previous.get("applied_at"), "late_accounts": late}

    changed_admin = changed_demo = 0

    if admin_pw:
        admin_hash = hash_password(admin_pw)   # one bcrypt per password, not per account
        for u in db.query(User).filter(User.is_superuser.is_(True)):
            u.hashed_password = admin_hash
            u.must_change_password = True
            changed_admin += 1
        if admin_email:
            owner = db.query(User).filter(User.email == "admin@oqc.local").first()
            if owner is None:
                print(f"deploy_secure: admin@oqc.local not found (already renamed?); ADMIN_EMAIL={admin_email} not applied")
            elif db.query(User).filter(User.email == admin_email).first():
                print(f"deploy_secure: {admin_email} already exists; admin@oqc.local keeps its address")
            else:
                owner.email = admin_email
                print(f"deploy_secure: owner account renamed to {admin_email}")

    if demo_pw:
        demo_hash = hash_password(demo_pw)
        for u in db.query(User).filter(User.is_superuser.is_(False)):
            local = (u.email or "").split("@")[0]
            if local.rstrip("0123456789") in DEMO_PREFIXES or local in DEMO_PREFIXES:
                u.hashed_password = demo_hash
                u.must_change_password = False  # keep the walkthrough friction-free
                changed_demo += 1

    # Any session minted before the rotation must not survive it.
    revoked = int(db.query(UserSession).filter(UserSession.revoked.is_(False)).update({"revoked": True}) or 0)

    applied_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    db.add(AuditEvent(actor_name="deploy", action="password_change", module="security",
                      severity="critical", is_consequential=True,
                      description=f"Deployment credential rotation on {settings.APP_ENV}",
                      rationale="Configured ADMIN_PASSWORD/DEMO_PASSWORD/ADMIN_EMAIL changed since the last application"
                                if previous else "Development passwords replaced with generated values at deploy time",
                      after_data={"superusers": changed_admin, "demo_accounts": changed_demo,
                                  "sessions_revoked": revoked, "fingerprint": fp[:12]}))
    set_setting(db, FINGERPRINT_KEY, {"fingerprint": fp, "applied_at": applied_at}, group="security",
                description="sha256 of the deployment credentials last applied by deploy_secure.py")
    marker = db.query(Setting).filter(Setting.key == FINGERPRINT_KEY).first()
    if marker is not None:
        marker.is_editable = False
    db.flush()
    return {"applied": True, "superusers": changed_admin, "demo_accounts": changed_demo,
            "sessions_revoked": revoked, "applied_at": applied_at}


def main() -> int:
    admin_pw = os.environ.get("ADMIN_PASSWORD", "")
    demo_pw = os.environ.get("DEMO_PASSWORD", "")
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    force = os.environ.get("ROTATE_CREDENTIALS", "").strip().lower() == "true"

    if not admin_pw.strip() and not demo_pw.strip():
        print("deploy_secure: no ADMIN_PASSWORD/DEMO_PASSWORD set, leaving credentials unchanged.")
        return 0

    db = SessionLocal()
    try:
        result = apply(db, admin_pw, demo_pw, admin_email, force=force)
        if not result["applied"]:
            if result.get("late_accounts"):
                db.commit()
                print(f"deploy_secure: {result['late_accounts']} re-seeded account(s) had a development password; replaced.")
            print(f"deploy_secure: credentials unchanged since {result.get('applied_at') or 'the last run'}, nothing to do"
                  " (set ROTATE_CREDENTIALS=true to re-apply).")
            return 0
        db.commit()
        print(f"deploy_secure: rotated {result['superusers']} superuser and {result['demo_accounts']} demo passwords; "
              f"revoked {result['sessions_revoked']} sessions; fingerprint stored at {result['applied_at']}.")
        return 0
    except Exception as exc:  # never fail the build over this, but make it loud
        db.rollback()
        print(f"deploy_secure: FAILED - {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
