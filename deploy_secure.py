"""Harden a freshly deployed instance.

Runs at the end of the Render build. It replaces the development passwords that ship in the seed
with values taken from the environment, so a public deployment never keeps the documented
`Admin@12345` / `Teacher@123` credentials.

    ADMIN_PASSWORD   password for admin@oqc.local (and any other superuser)
    DEMO_PASSWORD    shared password for every other seeded account
    ADMIN_EMAIL      optional: rename the owner account to a real address

Idempotent: re-running only rewrites passwords, never creates or deletes accounts.
It is a no-op when neither variable is set, so local development is unaffected.
"""
from __future__ import annotations

import os
import sys

from app.config import settings
from app.core.security import hash_password
from app.database import SessionLocal
from app.models.core import AuditEvent, User, UserSession

# Accounts that must keep working for a client walkthrough, but with a new shared password.
DEMO_PREFIXES = ("teacher", "parent", "student", "supervisor", "manager", "hr", "finance",
                 "academics", "qa", "tech", "marketing", "billing", "leadgen", "closer",
                 "accountant", "qaofficer", "hrofficer", "coordinator", "auditor", "sysadmin")


def main() -> int:
    admin_pw = os.environ.get("ADMIN_PASSWORD", "").strip()
    demo_pw = os.environ.get("DEMO_PASSWORD", "").strip()
    admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()

    if not admin_pw and not demo_pw:
        print("deploy_secure: no ADMIN_PASSWORD/DEMO_PASSWORD set, leaving credentials unchanged.")
        return 0

    db = SessionLocal()
    try:
        changed_admin = changed_demo = 0

        if admin_pw:
            for u in db.query(User).filter(User.is_superuser.is_(True)):
                u.hashed_password = hash_password(admin_pw)
                u.must_change_password = True
                changed_admin += 1
            if admin_email:
                owner = db.query(User).filter(User.email == "admin@oqc.local").first()
                if owner and not db.query(User).filter(User.email == admin_email).first():
                    owner.email = admin_email
                    print(f"deploy_secure: owner account renamed to {admin_email}")

        if demo_pw:
            for u in db.query(User).filter(User.is_superuser.is_(False)):
                local = (u.email or "").split("@")[0]
                if local.rstrip("0123456789") in DEMO_PREFIXES or local in DEMO_PREFIXES:
                    u.hashed_password = hash_password(demo_pw)
                    u.must_change_password = False  # keep the walkthrough friction-free
                    changed_demo += 1

        # Any session minted before the rotation must not survive it.
        revoked = db.query(UserSession).filter(UserSession.revoked.is_(False)).update({"revoked": True})

        db.add(AuditEvent(actor_name="deploy", action="password_change", module="security",
                          severity="critical", is_consequential=True,
                          description=f"Deployment credential rotation on {settings.APP_ENV}",
                          rationale="Development passwords replaced with generated values at deploy time",
                          after_data={"superusers": changed_admin, "demo_accounts": changed_demo,
                                      "sessions_revoked": int(revoked or 0)}))
        db.commit()
        print(f"deploy_secure: rotated {changed_admin} superuser and {changed_demo} demo passwords; "
              f"revoked {revoked or 0} sessions.")
        return 0
    except Exception as exc:  # never fail the build over this, but make it loud
        db.rollback()
        print(f"deploy_secure: FAILED - {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
