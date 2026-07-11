"""Verify a disposable SQL Server database can bootstrap and migrate cleanly.

This is deliberately an operator/CI harness, not a normal migration command.
It renders the repository SQL against an explicitly named test database, runs
baseline + seeds + V0001..V0025 twice, and checks the version ledger.  It never
accepts a production-looking database name.

Example (Windows trusted authentication):
    python scripts/migrations/verify_clean_migration.py \
        --database Mech_Chatbot_Test_Migrations --recreate
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE = PROJECT_ROOT / "database" / "schema" / "01_baseline.sql"
SEEDS = (
    PROJECT_ROOT / "database" / "seed" / "01_roles.sql",
    PROJECT_ROOT / "database" / "seed" / "03_departments.sql",
    PROJECT_ROOT / "database" / "seed" / "02_dev_accounts.sql",
)
MIGRATION_RUNNER = PROJECT_ROOT / "scripts" / "migrations" / "migrate.py"
TEST_DATABASE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,120}$")


def validate_test_database(name: str) -> str:
    """Accept only clearly disposable database names to avoid destructive use."""
    value = str(name or "").strip()
    if not TEST_DATABASE_RE.fullmatch(value):
        raise ValueError("Ten database test chi duoc dung chu, so va dau gach duoi")
    if "test" not in value.lower():
        raise ValueError("Database test phai co chu 'test' trong ten")
    if value.lower() == "mech_chatbot_db":
        raise ValueError("Khong duoc dung database van hanh cho clean migration test")
    return value


def render_for_database(sql_text: str, database: str) -> str:
    """Replace the legacy fixed database name only for an isolated test copy."""
    return sql_text.replace("Mech_Chatbot_DB", database)


def _sqlcmd_base_args(server: str, database: str) -> list[str]:
    args = ["sqlcmd", "-S", server, "-d", database, "-b", "-I"]
    username = os.getenv("SQL_USERNAME", "").strip()
    password = os.getenv("SQL_PASSWORD", "")
    if username and password:
        return args + ["-U", username, "-P", password]
    return args + ["-E"]


def run_rendered_sql(path: Path, *, server: str, database: str) -> None:
    rendered = render_for_database(path.read_text(encoding="utf-8-sig"), database)
    with tempfile.NamedTemporaryFile("w", suffix=".sql", encoding="utf-8", delete=False) as handle:
        handle.write(rendered)
        temporary_path = Path(handle.name)
    try:
        subprocess.run(
            _sqlcmd_base_args(server, "master") + ["-i", str(temporary_path)],
            check=True,
        )
    finally:
        temporary_path.unlink(missing_ok=True)


def recreate_database(*, server: str, database: str) -> None:
    statement = (
        f"IF DB_ID(N'{database}') IS NOT NULL BEGIN "
        f"ALTER DATABASE [{database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
        f"DROP DATABASE [{database}]; END;"
    )
    subprocess.run(_sqlcmd_base_args(server, "master") + ["-Q", statement], check=True)


def apply_all_migrations(*, database: str) -> None:
    environment = dict(os.environ)
    environment["SQL_DATABASE"] = database
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    subprocess.run([sys.executable, str(MIGRATION_RUNNER)], cwd=PROJECT_ROOT, env=environment, check=True)


def expected_versions() -> set[str]:
    return {
        path.name.split("__", 1)[0].upper()
        for path in (PROJECT_ROOT / "database" / "migrations").glob("V????__*.sql")
    }


def assert_version_ledger(*, server: str, database: str) -> None:
    query = "SET NOCOUNT ON; SELECT Version FROM dbo._SchemaVersions ORDER BY Version;"
    result = subprocess.run(
        _sqlcmd_base_args(server, database) + ["-h", "-1", "-W", "-Q", query],
        check=True,
        capture_output=True,
        text=True,
    )
    seen = {line.strip().upper() for line in result.stdout.splitlines() if line.strip()}
    missing = sorted(expected_versions() - seen)
    if missing:
        raise RuntimeError("Thieu migration trong _SchemaVersions: " + ", ".join(missing))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.getenv("SQL_SERVER", r"localhost\SQLEXPRESS"))
    parser.add_argument("--database", required=True, help="Ten disposable test database (bat buoc co 'test')")
    parser.add_argument("--recreate", action="store_true", help="Xoa/recreate database test truoc khi chay")
    args = parser.parse_args()
    database = validate_test_database(args.database)
    if not args.recreate:
        raise SystemExit("Can --recreate de xac nhan thao tac voi database test")

    recreate_database(server=args.server, database=database)
    print("BOOTSTRAP baseline + seeds")
    run_rendered_sql(BASELINE, server=args.server, database=database)
    for seed in SEEDS:
        run_rendered_sql(seed, server=args.server, database=database)

    print("APPLY migrations lan 1")
    apply_all_migrations(database=database)
    print("APPLY migrations lan 2 (idempotency)")
    apply_all_migrations(database=database)
    assert_version_ledger(server=args.server, database=database)
    print(f"PASS clean migration verification: {database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
