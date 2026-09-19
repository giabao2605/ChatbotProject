import ast
from pathlib import Path

from tests.architecture.rules import scan_repository


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "mech_chatbot"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(
                f"{node.module}.{alias.name}"
                for alias in node.names
                if alias.name != "*"
            )
    return modules


def test_import_scanner_expands_from_import_aliases(tmp_path):
    fixture = tmp_path / "imports.py"
    fixture.write_text(
        "from mech_chatbot.db import repository\n"
        "from mech_chatbot import services\n",
        encoding="utf-8",
    )

    imports = _imported_modules(fixture)

    assert "mech_chatbot.db.repository" in imports
    assert "mech_chatbot.services" in imports


def test_core_and_service_layers_do_not_import_legacy_ui():
    violations = scan_repository(
        SOURCE_ROOT,
        required_packages=("api", "db", "rag", "services"),
    )
    ui_violations = [
        item
        for item in violations
        if item.rule in {"core_ui_dependency", "service_ui_dependency"}
    ]

    assert ui_violations == []


def test_engine_contract_is_composition_owned_and_legacy_shim_stays_import_safe():
    engine_path = SOURCE_ROOT / "db" / "engine.py"
    repository_path = SOURCE_ROOT / "db" / "repository.py"

    engine_source = engine_path.read_text(encoding="utf-8")
    repository_imports = _imported_modules(repository_path)

    assert "engine = create_db_engine()" not in engine_source
    assert "def build_database_runtime" in engine_source
    assert "def _ensure_engine" in engine_source
    assert "mech_chatbot.db.engine" in repository_imports


def test_auth_service_keeps_core_authentication_compatibility_import():
    imports = _imported_modules(SOURCE_ROOT / "auth" / "service.py")

    assert "mech_chatbot.auth.core" in imports


def test_phase5_registry_and_pipeline_edges_are_removed():
    violations = scan_repository(
        SOURCE_ROOT,
        required_packages=("db", "ingestion", "rag"),
    )
    targeted_paths = {
        "db/registry_ports.py",
        "ingestion/pdf/pipeline_implementation.py",
        "rag/answer_checks.py",
        "rag/glossary_expand.py",
    }
    targeted_rules = {
        "db_dynamic_import",
        "ingestion_rag_dependency",
        "rag_ingestion_dependency",
    }

    blocked = [
        item
        for item in violations
        if item.rule in targeted_rules and item.path in targeted_paths
    ]

    assert blocked == []


def test_phase5_imports_do_not_create_resources_or_mutate_environment():
    violations = scan_repository(
        SOURCE_ROOT,
        required_packages=(
            "api",
            "application",
            "config",
            "db",
            "ingestion",
            "llm",
            "rag",
            "workers",
        ),
    )

    blocked = [
        item
        for item in violations
        if item.rule
        in {
            "import_time_environment_mutation",
            "import_time_resource",
        }
    ]

    assert blocked == []


def test_phase6_internal_source_and_scripts_do_not_import_legacy_facades():
    facade_owners = {
        "mech_chatbot.services": "services/__init__.py",
        "mech_chatbot.db.repository": "db/repository.py",
        "mech_chatbot.rag.service": "rag/service.py",
        "mech_chatbot.ingestion.pdf_processor": (
            "ingestion/pdf_processor.py"
        ),
    }
    violations = []
    scan_roots = (
        ("src", SOURCE_ROOT),
        ("scripts", SCRIPTS_ROOT),
    )
    for root_label, root in scan_roots:
        for path in root.rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            imports = _imported_modules(path)
            for facade, owner in facade_owners.items():
                if root_label == "src" and relative == owner:
                    continue
                if facade in imports:
                    violations.append(f"{root_label}/{relative}: {facade}")

    assert violations == []


def test_phase6_services_compatibility_facade_is_static():
    source = (SOURCE_ROOT / "services" / "__init__.py").read_text(encoding="utf-8")

    assert "_SERVICE_MODULES" not in source
    assert "globals()[" not in source


def test_phase6_repository_package_exports_owner_modules_without_wildcards():
    package_path = SOURCE_ROOT / "db" / "repositories" / "__init__.py"
    violations = scan_repository(SOURCE_ROOT, required_packages=("db",))
    wildcard_paths = {
        item.path for item in violations if item.rule == "wildcard_import"
    }

    assert "db/repositories/__init__.py" not in wildcard_paths
    assert "from ." not in package_path.read_text(encoding="utf-8").replace(
        "from . import", ""
    )


def test_phase6_sql_migration_owns_and_closes_its_database_runtime():
    migration_path = SCRIPTS_ROOT / "migrations" / "alter_ingestionjobs.py"
    imports = _imported_modules(migration_path)
    source = migration_path.read_text(encoding="utf-8")

    assert "mech_chatbot.db.engine" in imports
    assert "build_database_runtime" in source
    assert "from mech_chatbot.db.engine import engine" not in source
    assert "database_runtime.close()" in source
    assert "COL_LENGTH" in source
    assert "except Exception" not in source


def test_phase6_image_summary_diagnostic_owns_and_closes_runtime_resources():
    diagnostic_path = (
        SCRIPTS_ROOT / "diagnostics" / "check_image_summary_coverage.py"
    )
    source = diagnostic_path.read_text(encoding="utf-8")

    assert "build_database_runtime" in source
    assert "build_qdrant_admin_runtime" in source
    assert "from mech_chatbot.db.engine import engine" not in source
    assert "qdrant_runtime.close()" in source
    assert "database_runtime.close()" in source
    assert "Error querying Qdrant" not in source
