"""Create the schema and load seed / demo data.

    python seed.py            # create tables + seed everything (idempotent)
    python seed.py --reset    # drop the SQLite database first and rebuild from scratch
    python seed.py --core     # only roles, users, departments, currencies (production bootstrap)
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

from app.config import settings, BASE_DIR
from app.database import Base, engine, SessionLocal, init_db

# Order matters: later modules depend on earlier ones.
# "academic" builds courses/packages and the curriculum tree; "academic_curriculum" runs again after
# "people" so the per-student academic history (progress, plans, tests, certificates) has students to attach to.
SEED_MODULES = ["core", "academic", "people", "academic_curriculum", "scheduling", "crm", "finance", "hr", "ops"]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = set(sys.argv[1:])
    if "--reset" in args:
        removed = False
        if settings.is_sqlite:
            db_path = BASE_DIR / settings.DATABASE_URL.replace("sqlite:///./", "")
            engine.dispose()
            try:
                for suffix in ("", "-wal", "-shm"):
                    p = Path(str(db_path) + suffix)
                    if p.exists():
                        p.unlink()
                removed = True
                print(f"Removed {db_path}")
            except PermissionError:
                print(f"{db_path} is in use by another process - dropping and recreating tables instead.")
                print("Tip: set DATABASE_URL=sqlite:///./data/oqc_<name>.db to use a private database while developing.")
        if not removed:
            from app import models  # noqa: F401
            Base.metadata.drop_all(bind=engine)
            print("Dropped all tables")
    init_db()
    print(f"Schema ready ({len(Base.metadata.tables)} tables) on {settings.DATABASE_URL}")
    modules = ["core"] if "--core" in args else SEED_MODULES
    db = SessionLocal()
    try:
        for name in modules:
            try:
                mod = importlib.import_module(f"app.seed.{name}")
            except ModuleNotFoundError:
                print(f"  - seed.{name}: not present, skipped")
                continue
            t0 = time.time()
            mod.run(db)
            db.commit()
            print(f"  ✓ seed.{name} ({time.time() - t0:.1f}s)")
    finally:
        db.close()
    print("\nDone. Start the server with:  python run.py")
    print("Sign in at http://127.0.0.1:8000  (admin@oqc.local / Admin@12345)")


if __name__ == "__main__":
    main()
