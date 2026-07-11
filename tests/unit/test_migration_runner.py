import importlib.util
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrations" / "migrate.py"
SPEC = importlib.util.spec_from_file_location("migration_runner", SCRIPT)
migration_runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration_runner)


def test_split_batches_ignores_go_inside_block_comment():
    sql = """
SELECT 1;
GO
/*
SELECT 2;
GO
*/
SELECT 3;
GO
"""

    batches = migration_runner.split_batches(sql)

    assert len(batches) == 2
    assert batches[0] == "SELECT 1;"
    assert "GO" in batches[1]
    assert "SELECT 3" in batches[1]


def test_split_batches_ignores_go_inside_multiline_string():
    sql = """
SELECT 'line one
GO
line two';
GO
SELECT 2;
"""

    batches = migration_runner.split_batches(sql)

    assert len(batches) == 2
    assert "line two" in batches[0]
    assert batches[1] == "SELECT 2;"


def test_strip_use_statements_keeps_a_clean_test_run_on_its_configured_database():
    sql = """-- Legacy migration comment
USE Mech_Chatbot_DB;
GO
SELECT DB_NAME();
"""

    rendered = migration_runner.strip_use_statements(sql)
    batches = migration_runner.split_batches(rendered)

    assert "USE Mech_Chatbot_DB" not in rendered
    assert all("USE Mech_Chatbot_DB" not in batch for batch in batches)
    assert batches[-1] == "SELECT DB_NAME();"
