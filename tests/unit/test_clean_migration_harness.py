import importlib.util
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrations" / "verify_clean_migration.py"
SPEC = importlib.util.spec_from_file_location("clean_migration_harness", SCRIPT)
clean_migration_harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(clean_migration_harness)


def test_clean_migration_harness_rejects_operational_database():
    with pytest.raises(ValueError):
        clean_migration_harness.validate_test_database("Mech_Chatbot_DB")


def test_clean_migration_harness_requires_explicit_test_name():
    with pytest.raises(ValueError):
        clean_migration_harness.validate_test_database("Mech_Chatbot_Scratch")


def test_clean_migration_harness_renders_only_disposable_copy():
    rendered = clean_migration_harness.render_for_database(
        "USE Mech_Chatbot_DB; CREATE DATABASE Mech_Chatbot_DB;",
        "Mech_Chatbot_Test_Migrations",
    )

    assert "Mech_Chatbot_DB" not in rendered
    assert rendered.count("Mech_Chatbot_Test_Migrations") == 2


def test_clean_migration_harness_knows_all_versioned_migrations():
    versions = clean_migration_harness.expected_versions()

    assert "V0001" in versions
    assert "V0025" in versions
