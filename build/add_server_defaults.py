"""Give every NOT NULL column a migration adds to an existing table a server default.

Alembic emits ``ALTER TABLE t ADD COLUMN c TYPE NOT NULL`` with no default. SQLite's batch mode hides
the problem by rebuilding the table, but PostgreSQL rejects it outright the moment the table already
holds rows:

    psycopg.errors.NotNullViolation: column "is_public" of relation "books" contains null values

The fix is a server default, and the only correct value is the one the model already declares, so this
reads ``default=`` straight off the SQLAlchemy column rather than guessing.

    .venv/Scripts/python.exe build/add_server_defaults.py migrations/versions/<file>.py
"""
from __future__ import annotations

import io
import re
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models  # noqa: F401  (registers every table on the metadata)
from app.database import Base

BATCH = re.compile(r"op\.batch_alter_table\(\s*'([^']+)'")
ADD = re.compile(r"^(\s*)batch_op\.add_column\(sa\.Column\('([^']+)',\s*(.+?),\s*nullable=False\)\)\s*$")


def render_default(value) -> str | None:
    """SQL literal for a Python-side column default, or None when we cannot express one."""
    if callable(value):
        # SQLAlchemy wraps a plain callable default (default=list) in one that takes an execution
        # context, so try both shapes before giving up.
        for call in (lambda: value(), lambda: value(None)):
            try:
                value = call()
                break
            except TypeError:
                continue
        else:
            return None
    if isinstance(value, bool):
        return "sa.text('true')" if value else "sa.text('false')"
    if isinstance(value, (int, float, Decimal)):
        return f"sa.text('{value}')"
    if isinstance(value, str):
        return "sa.text(\"'" + value.replace("'", "''") + "'\")"
    if isinstance(value, list):
        return "sa.text(\"'[]'\")"
    if isinstance(value, dict):
        return "sa.text(\"'{}'\")"
    return None


def main(path: str) -> int:
    lines = io.open(path, encoding="utf-8").read().splitlines()
    out: list[str] = []
    table = ""
    patched, skipped = 0, []

    for line in lines:
        m = BATCH.search(line)
        if m:
            table = m.group(1)
        a = ADD.match(line)
        if a and "server_default" not in line:
            indent, column, type_sql = a.group(1), a.group(2), a.group(3)
            model_table = Base.metadata.tables.get(table)
            col = model_table.columns.get(column) if model_table is not None else None
            literal = None
            if col is not None and col.default is not None:
                literal = render_default(getattr(col.default, "arg", None))
            if literal is None:
                skipped.append(f"{table}.{column}")
            else:
                line = (f"{indent}batch_op.add_column(sa.Column('{column}', {type_sql}, "
                        f"nullable=False, server_default={literal}))")
                patched += 1
        out.append(line)

    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")
    print(f"added server defaults to {patched} NOT NULL column(s)")
    if skipped:
        print("NO DEFAULT AVAILABLE (make these nullable or set one by hand):")
        for s in skipped:
            print("  ", s)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
