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


def test_engine_contract_remains_in_extracted_module_and_legacy_shim():
    engine_path = SOURCE_ROOT / "db" / "engine.py"
    repository_path = SOURCE_ROOT / "db" / "repository.py"

    engine_source = engine_path.read_text(encoding="utf-8")
    repository_imports = _imported_modules(repository_path)

    assert "engine = create_db_engine()" in engine_source
    assert "def _ensure_engine" in engine_source
    assert "mech_chatbot.db.engine" in repository_imports


def test_auth_service_keeps_core_authentication_compatibility_import():
    imports = _imported_modules(SOURCE_ROOT / "auth" / "service.py")

    assert "mech_chatbot.auth.core" in imports
