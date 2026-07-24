import ast
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

SRC_ROOT = Path(__file__).parents[2] / "src" / "mech_chatbot"


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_registry_policy_is_owned_by_neutral_domain():
    from mech_chatbot.domain import registry_policy

    assert registry_policy.resolve_domain_fallback("Technical") == "mechanical"
    assert registry_policy.resolve_security_fallback("HR") == "confidential"
    assert registry_policy.resolve_site_fallback("Production") == "XUONG_CO_KHI"
    assert registry_policy.canonical_label("ban ve ky thuat") == "Bản vẽ kỹ thuật"
    assert registry_policy.normalize_material(
        "Inox 304 plate",
        [
            {
                "code": "SUS304",
                "display": "SUS 304",
                "category": "stainless steel",
                "synonyms": ["inox 304"],
            }
        ],
    ) == "sus 304 plate"


def test_db_registry_is_static_and_has_no_callback_registry():
    from mech_chatbot.db import registry_ports

    assert not hasattr(registry_ports, "register")
    assert not hasattr(registry_ports, "_resolve")
    assert not hasattr(registry_ports, "_IMPLS")
    assert not hasattr(registry_ports, "_SOURCES")
    assert "importlib" not in _imports(SRC_ROOT / "db" / "registry_ports.py")


@pytest.mark.parametrize("module_name", ["answer_checks.py", "glossary_expand.py"])
def test_rag_registry_consumers_do_not_import_ingestion(module_name):
    imported = _imports(SRC_ROOT / "rag" / module_name)

    assert not any(name.startswith("mech_chatbot.ingestion") for name in imported)


def test_public_ingestion_registry_modules_remain_compatible():
    from mech_chatbot.ingestion import (
        doc_type_registry,
        domain_registry,
        material_registry,
        site_registry,
    )

    assert domain_registry.resolve_domain_by_department("Technical") == "mechanical"
    assert domain_registry.resolve_security_by_department("HR") == "confidential"
    assert site_registry.resolve_site_by_department("Production") == "XUONG_CO_KHI"
    assert doc_type_registry.normalize_doc_type("bản vẽ kỹ thuật") == "technical_drawing"
    assert "SUS304" in material_registry.get_known_materials()


class _DepartmentResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _DepartmentConnection:
    def __init__(self, row=None, failure=None):
        self._row = row
        self._failure = failure

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _statement, _parameters):
        if self._failure:
            raise self._failure
        query = str(_statement)
        if "SELECT Site" in query:
            return _DepartmentResult((self._row[2],))
        return _DepartmentResult(self._row[:2])


class _DepartmentEngine:
    def __init__(self, connection):
        self._connection = connection

    def connect(self):
        return self._connection


class _LegacyDepartmentConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, _parameters):
        if "DefaultSecurity" in str(statement):
            raise RuntimeError("column is not available")
        return _DepartmentResult(("tabular", None))


def test_database_department_values_win_over_static_fallbacks(monkeypatch):
    from mech_chatbot.db import registry_ports, repository

    monkeypatch.setattr(repository, "_ensure_engine", lambda: None)
    monkeypatch.setattr(
        repository,
        "engine",
        _DepartmentEngine(
            _DepartmentConnection(("tabular", "restricted", "FACTORY_2"))
        ),
    )

    assert registry_ports.resolve_domain_by_department("Technical") == "tabular"
    assert (
        registry_ports.resolve_security_by_department("Technical")
        == "restricted"
    )
    assert registry_ports.resolve_site_by_department("Technical") == "FACTORY_2"


def test_database_failure_uses_existing_department_fallbacks(monkeypatch):
    from mech_chatbot.db import registry_ports, repository

    monkeypatch.setattr(repository, "_ensure_engine", lambda: None)
    monkeypatch.setattr(
        repository,
        "engine",
        _DepartmentEngine(_DepartmentConnection(failure=RuntimeError("offline"))),
    )

    assert registry_ports.resolve_domain_by_department("Technical") == "mechanical"
    assert registry_ports.resolve_security_by_department("HR") == "confidential"
    assert registry_ports.resolve_site_by_department("Production") == "XUONG_CO_KHI"


def test_legacy_department_schema_keeps_domain_lookup_working(monkeypatch):
    from mech_chatbot.db import registry_ports, repository

    monkeypatch.setattr(repository, "_ensure_engine", lambda: None)
    monkeypatch.setattr(
        repository,
        "engine",
        _DepartmentEngine(_LegacyDepartmentConnection()),
    )

    assert registry_ports.resolve_domain_by_department("LegacyDept") == "tabular"
    assert registry_ports.resolve_security_by_department("LegacyDept") == "internal"


def test_empty_material_value_does_not_touch_database(monkeypatch):
    from mech_chatbot.db import registry_ports

    monkeypatch.setattr(
        registry_ports,
        "_get_materials",
        lambda *_args, **_kwargs: pytest.fail("database should not be read"),
    )

    assert registry_ports.normalize_material(None) is None
