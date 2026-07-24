from pathlib import Path

from tests.architecture.rules import (
    ArchitectureViolation,
    format_violations,
    load_allowlist,
    scan_repository,
    stale_allowances,
    unexpected_violations,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "mech_chatbot"
ALLOWLIST_PATH = Path(__file__).with_name("architecture_allowlist.json")


def test_guard_fails_closed_when_source_root_is_missing(tmp_path):
    missing = tmp_path / "missing"

    try:
        scan_repository(missing)
    except FileNotFoundError as exc:
        assert str(missing) in str(exc)
    else:
        raise AssertionError("missing architecture source root must fail closed")


def test_guard_detects_new_api_data_dependency(tmp_path):
    source_root = tmp_path / "mech_chatbot"
    api_dir = source_root / "api"
    api_dir.mkdir(parents=True)
    (api_dir / "new_route.py").write_text(
        "from sqlalchemy import text\n"
        "from mech_chatbot.db.engine import engine\n"
        "QUERY = text('SELECT 1')\n",
        encoding="utf-8",
    )

    violations = scan_repository(source_root)

    assert ArchitectureViolation(
        "api_direct_data_access", "api/new_route.py", "sqlalchemy"
    ) in violations
    assert ArchitectureViolation(
        "api_direct_data_access", "api/new_route.py", "mech_chatbot.db.engine"
    ) in violations
    assert ArchitectureViolation(
        "api_raw_sql", "api/new_route.py", "sqlalchemy.text"
    ) in violations


def test_guard_detects_dependencies_across_every_planned_layer_rule(tmp_path):
    source_root = tmp_path / "mech_chatbot"
    files = {
        "application/use_case.py": (
            "import fastapi\n"
            "from mech_chatbot.adapters import sql\n"
            "from mech_chatbot.rag import serving_state\n"
        ),
        "config/logging.py": (
            "from mech_chatbot.db import repository\n"
            "from mech_chatbot.rag import execution\n"
            "import os\n"
            "VALUE = os.getenv('VALUE')\n"
        ),
        "db/repo.py": "from mech_chatbot.rag.pipeline import execute_pipeline\n",
        "db/registry.py": (
            "import importlib\n"
            "MODULE = 'mech_chatbot.ingestion.registry'\n"
            "def resolve():\n"
            "    return importlib.import_module(MODULE)\n"
        ),
        "ingestion/loader.py": "from mech_chatbot.rag import service\n",
        "rag/feature.py": (
            "from mech_chatbot.evaluation import gate\n"
            "from mech_chatbot.ingestion import material_registry\n"
        ),
        "rag/runtime.py": (
            "from concurrent.futures import ThreadPoolExecutor\n"
            "from mech_chatbot.adapters.pilot_replay import PilotReplayExecutor\n"
            "POOL = ThreadPoolExecutor()\n"
            "REPLAYS = PilotReplayExecutor()\n"
        ),
        "evaluation/check.py": "from mech_chatbot.rag.pipeline import execute_pipeline\n",
        "workers/job.py": "import os\nVALUE = os.getenv('VALUE')\n",
        "api/legacy_ui.py": "import streamlit\n",
        "legacy.py": "from mech_chatbot.rag.pipeline import *\n",
        "services/new_service.py": "__all__ = ['new_flat_export']\n",
        "services/legacy_ui.py": "from mech_chatbot.ui import page\n",
    }
    for relative, source in files.items():
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    violations = set(scan_repository(source_root))

    expected = {
        ArchitectureViolation(
            "application_external_dependency", "application/use_case.py", "fastapi"
        ),
        ArchitectureViolation(
            "application_external_dependency",
            "application/use_case.py",
            "mech_chatbot.adapters",
        ),
        ArchitectureViolation(
            "application_external_dependency",
            "application/use_case.py",
            "mech_chatbot.rag",
        ),
        ArchitectureViolation(
            "config_upward_dependency",
            "config/logging.py",
            "mech_chatbot.db",
        ),
        ArchitectureViolation(
            "config_upward_dependency",
            "config/logging.py",
            "mech_chatbot.rag",
        ),
        ArchitectureViolation("direct_getenv", "config/logging.py", "os.getenv"),
        ArchitectureViolation(
            "db_upward_dependency", "db/repo.py", "mech_chatbot.rag.pipeline"
        ),
        ArchitectureViolation(
            "db_dynamic_import", "db/registry.py", "importlib.import_module"
        ),
        ArchitectureViolation(
            "ingestion_rag_dependency",
            "ingestion/loader.py",
            "mech_chatbot.rag",
        ),
        ArchitectureViolation(
            "rag_evaluation_dependency", "rag/feature.py", "mech_chatbot.evaluation"
        ),
        ArchitectureViolation(
            "rag_ingestion_dependency",
            "rag/feature.py",
            "mech_chatbot.ingestion",
        ),
        ArchitectureViolation(
            "import_time_resource",
            "rag/runtime.py",
            "ThreadPoolExecutor",
        ),
        ArchitectureViolation(
            "import_time_resource",
            "rag/runtime.py",
            "PilotReplayExecutor",
        ),
        ArchitectureViolation(
            "evaluation_private_rag_dependency",
            "evaluation/check.py",
            "mech_chatbot.rag.pipeline",
        ),
        ArchitectureViolation("direct_getenv", "workers/job.py", "os.getenv"),
        ArchitectureViolation("core_ui_dependency", "api/legacy_ui.py", "streamlit"),
        ArchitectureViolation("wildcard_import", "legacy.py", "mech_chatbot.rag.pipeline"),
        ArchitectureViolation(
            "service_flat_export", "services/new_service.py", "new_flat_export"
        ),
        ArchitectureViolation(
            "service_ui_dependency", "services/legacy_ui.py", "mech_chatbot.ui"
        ),
    }
    assert expected <= violations


def test_only_canonical_settings_module_may_read_environment(tmp_path):
    source_root = tmp_path / "mech_chatbot"
    files = {
        "config/settings.py": "import os\nVALUE = os.getenv('VALUE')\n",
        "config/worker_settings.py": "import os\nVALUE = os.getenv('VALUE')\n",
        "ingestion/pdf/config.py": "import os\nVALUE = os.getenv('VALUE')\n",
        "rag/bootstrap.py": "import os\nVALUE = os.getenv('VALUE')\n",
        "rag/route_config.py": "import os\nVALUE = os.getenv('VALUE')\n",
    }
    for relative, source in files.items():
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    violations = scan_repository(source_root)
    direct_reads = {
        item.path for item in violations if item.rule == "direct_getenv"
    }

    assert direct_reads == {
        "config/worker_settings.py",
        "ingestion/pdf/config.py",
        "rag/bootstrap.py",
        "rag/route_config.py",
    }


def test_ratchet_distinguishes_removed_debt_from_new_occurrences():
    violation = ArchitectureViolation("direct_getenv", "rag/example.py", "os.getenv")

    assert unexpected_violations([violation], [violation, violation]) == []
    assert stale_allowances([violation], [violation, violation]) == [violation]
    assert unexpected_violations([violation, violation], [violation]) == [violation]
    assert stale_allowances([violation, violation], [violation]) == []


def test_current_backend_does_not_exceed_reviewed_architecture_debt():
    current = scan_repository(
        SOURCE_ROOT,
        required_packages=("api", "db", "rag", "services"),
    )
    allowed = load_allowlist(ALLOWLIST_PATH)
    unexpected = unexpected_violations(current, allowed)
    stale = stale_allowances(current, allowed)

    assert not unexpected and not stale, (
        "New architecture debt is not allowlisted. Remove the dependency or update the "
        "reviewed baseline intentionally. Stale allowances must be removed in the same "
        "change as the debt.\nUnexpected:\n"
        + format_violations(unexpected)
        + "\nStale:\n"
        + format_violations(stale)
    )
