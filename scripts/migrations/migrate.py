"""Run versioned SQL Server migrations in filename order.

Examples:
    python scripts/migrations/migrate.py
    python scripts/migrations/migrate.py --target V0019
    python scripts/migrations/migrate.py --dry-run
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

from sqlalchemy import text


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from mech_chatbot.db.engine import _ensure_engine, engine  # noqa: E402


MIGRATION_DIR = PROJECT_ROOT / "database" / "migrations"
MIGRATION_RE = re.compile(r"^(V\d{4})__(.+)\.sql$", re.IGNORECASE)
GO_RE = re.compile(r"^\s*GO(?:\s+\d+)?\s*(?:--.*)?$", re.IGNORECASE)
USE_RE = re.compile(r"^\s*USE\s+[^;]+;?\s*$", re.IGNORECASE)


def split_batches(sql_text: str) -> list[str]:
    batches: list[str] = []
    current: list[str] = []
    in_block_comment = False
    in_string = False
    for line in sql_text.lstrip("\ufeff").splitlines():
        if not in_block_comment and not in_string and GO_RE.match(line):
            batch = "\n".join(current).strip()
            if batch:
                batches.append(batch)
            current = []
            continue

        current.append(line)
        index = 0
        while index < len(line):
            if in_block_comment:
                end = line.find("*/", index)
                if end < 0:
                    break
                in_block_comment = False
                index = end + 2
                continue
            if in_string:
                if line[index] == "'":
                    if index + 1 < len(line) and line[index + 1] == "'":
                        index += 2
                        continue
                    in_string = False
                index += 1
                continue
            if line.startswith("--", index):
                break
            if line.startswith("/*", index):
                in_block_comment = True
                index += 2
                continue
            if line[index] == "'":
                in_string = True
            index += 1
    batch = "\n".join(current).strip()
    if batch:
        batches.append(batch)
    return batches


def strip_use_statements(sql_text: str) -> str:
    """Remove legacy ``USE`` lines before executing through one configured engine.

    A migration frequently starts with comments followed by ``USE
    Mech_Chatbot_DB``.  Skipping only a batch that consists solely of ``USE``
    is unsafe: the comment and ``USE`` can be one batch, silently switching a
    clean-test run back to the operational database.  The SQLAlchemy connection
    already selects ``SQL_DATABASE``, so every standalone USE line is removed
    before batches are split.
    """
    return "\n".join(
        "" if USE_RE.match(line) else line
        for line in sql_text.lstrip("\ufeff").splitlines()
    )


def discover_migrations(target: str | None = None):
    migrations = []
    for path in sorted(MIGRATION_DIR.glob("V*.sql")):
        match = MIGRATION_RE.match(path.name)
        if not match:
            continue
        version = match.group(1).upper()
        if target and version > target.upper():
            continue
        description = match.group(2).replace("_", " ")[:255]
        migrations.append((version, description, path))
    return migrations


def applied_versions() -> set[str]:
    _ensure_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT Version FROM dbo._SchemaVersions")).fetchall()
    return {str(row[0]).upper() for row in rows}


def apply_migration(version: str, description: str, path: Path) -> int:
    batches = split_batches(strip_use_statements(path.read_text(encoding="utf-8-sig")))
    executed = 0
    with engine.begin() as conn:
        for batch in batches:
            conn.exec_driver_sql(batch)
            executed += 1
        conn.execute(
            text(
                """
                IF NOT EXISTS (
                    SELECT 1 FROM dbo._SchemaVersions WHERE Version = :version
                )
                    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
                    VALUES (:version, :description, GETDATE())
                """
            ),
            {"version": version, "description": description},
        )
    return executed


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply pending SQL migrations")
    parser.add_argument("--target", help="Only apply through this version, e.g. V0019")
    parser.add_argument("--dry-run", action="store_true", help="List pending migrations only")
    args = parser.parse_args()

    migrations = discover_migrations(args.target)
    if not migrations:
        print("Khong tim thay migration hop le.")
        return 1

    applied = applied_versions()
    pending = [item for item in migrations if item[0] not in applied]
    if not pending:
        print("Database da o schema moi nhat.")
        return 0

    for version, description, path in pending:
        if args.dry_run:
            print(f"PENDING {version} {path.name}")
            continue
        print(f"APPLY {version} {path.name}")
        try:
            batch_count = apply_migration(version, description, path)
        except Exception as exc:
            print(f"FAILED {version}: {exc}", file=sys.stderr)
            return 1
        print(f"DONE {version} ({batch_count} batches)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
