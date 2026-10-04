"""Wait for the configured database and report whether it is empty. Used by deploy/entrypoint.sh.

    python deploy/db_ready.py wait    # exit 0 once SELECT 1 works (90 s timeout)
    python deploy/db_ready.py empty   # exit 0 when the users table is missing or has no rows
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# `python deploy/db_ready.py` puts deploy/ on sys.path, not the repository root; the app package lives there.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.database import engine  # noqa: E402


def wait(timeout: int = 90) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # psycopg OperationalError while Postgres boots
            if time.time() > deadline:
                print(f"database not reachable after {timeout}s: {exc}", file=sys.stderr)
                sys.exit(1)
            time.sleep(2)


def is_empty() -> bool:
    with engine.connect() as conn:
        try:
            return (conn.execute(text("SELECT COUNT(*) FROM users")).scalar() or 0) == 0
        except Exception:
            return True


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "wait"
    if cmd == "wait":
        wait()
    elif cmd == "empty":
        sys.exit(0 if is_empty() else 1)
    else:
        sys.exit(f"unknown command {cmd}")
