import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import text
from mech_chatbot.config.settings import SqlSettings, load_settings
from mech_chatbot.db.engine import build_database_runtime


def main() -> int:
    settings = load_settings(PROJECT_ROOT / ".env")
    database_runtime = build_database_runtime(
        SqlSettings.from_settings(settings)
    )
    try:
        with database_runtime.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    IF COL_LENGTH(
                        'dbo.IngestionJobs',
                        'FailureType'
                    ) IS NULL
                    BEGIN
                        ALTER TABLE dbo.IngestionJobs
                        ADD FailureType NVARCHAR(50) NULL;
                    END
                    """
                )
            )
            print("Ensured FailureType column")
            conn.execute(
                text(
                    """
                    IF COL_LENGTH(
                        'dbo.IngestionJobs',
                        'NextRetryAt'
                    ) IS NULL
                    BEGIN
                        ALTER TABLE dbo.IngestionJobs
                        ADD NextRetryAt DATETIME NULL;
                    END
                    """
                )
            )
            print("Ensured NextRetryAt column")
    finally:
        database_runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
