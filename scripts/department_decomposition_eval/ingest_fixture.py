"""Ingest department Query Decomposition fixtures into the configured collection."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text

from scripts.demo_wave.ingest_demo_corpus import ingest_corpus
from scripts.department_decomposition_eval.generate_fixture import (
    BATCH_ID,
    DEFAULT_OUTPUT,
    generate_corpus,
)
from scripts.demo_wave.generate_demo_assets import DEPARTMENTS
from mech_chatbot.composition.maintenance_runtime import (
    with_configured_repository_runtime,
)
from mech_chatbot.db.engine import _ensure_engine, engine


def validate_active_departments(active_departments) -> list[str]:
    active = {str(item).strip() for item in active_departments if str(item).strip()}
    expected = set(DEPARTMENTS)
    if active != expected:
        raise RuntimeError(
            "fixture departments must match active departments; "
            f"unexpected={sorted(active - expected)}, "
            f"missing={sorted(expected - active)}"
        )
    return sorted(active)


def _active_departments() -> list[str]:
    _ensure_engine()
    with engine.connect() as connection:
        values = connection.execute(text("""
            SELECT DeptCode FROM dbo.DepartmentKnowledgeGovernance
            WHERE IsActive=1 ORDER BY DeptCode
        """)).scalars().all()
    return validate_active_departments(values)


@with_configured_repository_runtime(include_qdrant=True)
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    _active_departments()
    report = ingest_corpus(
        args.output,
        args.limit,
        batch=BATCH_ID,
        generator=generate_corpus,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
