import ast
from pathlib import Path

from tests.architecture.rules import scan_repository


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "mech_chatbot"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


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
