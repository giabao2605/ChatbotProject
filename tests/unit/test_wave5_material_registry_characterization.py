import re

import pytest

from mech_chatbot.db import repository
from mech_chatbot.ingestion import material_registry


pytestmark = pytest.mark.unit


class _Rows:
    def __init__(self, rows):
        self._rows = tuple(rows)

    def fetchall(self):
        return list(self._rows)


class _Connection:
    def __init__(self, materials=(), synonyms=()):
        self._materials = tuple(materials)
        self._synonyms = tuple(synonyms)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement):
        query = str(statement)
        if "MaterialDictionary" in query:
            return _Rows(self._materials)
        return _Rows(self._synonyms)


class _Engine:
    def __init__(self, materials=(), synonyms=()):
        self._materials = tuple(materials)
        self._synonyms = tuple(synonyms)

    def connect(self):
        return _Connection(self._materials, self._synonyms)


@pytest.fixture(autouse=True)
def isolated_material_cache(monkeypatch):
    monkeypatch.setattr(material_registry, "_cache", {"ts": 0.0, "materials": None})


def test_public_material_registry_reads_active_database_dictionary(monkeypatch):
    monkeypatch.setattr(repository, "_ensure_engine", lambda: None)
    monkeypatch.setattr(
        repository,
        "engine",
        _Engine(
            materials=[(7, "SUS304", "SUS 304", "stainless steel")],
            synonyms=[(7, "inox 304"), (7, None), (999, "ignored")],
        ),
    )

    material_registry.refresh_cache()
    patterns = material_registry.get_material_patterns()

    assert material_registry.get_known_materials() == ["SUS304"]
    assert any(re.search(pattern, "SUS 304", flags=re.IGNORECASE) for pattern in patterns)
    assert any(re.search(pattern, "inox 304", flags=re.IGNORECASE) for pattern in patterns)
    assert material_registry.normalize_material("Inox 304 plate") == "sus 304 plate"
    assert material_registry.normalize_material("SUS304") == "sus 304"


def test_empty_dictionary_uses_safe_defaults_and_cached_result(monkeypatch):
    calls = []

    def ensure_engine():
        calls.append("ensure")

    monkeypatch.setattr(repository, "_ensure_engine", ensure_engine)
    monkeypatch.setattr(repository, "engine", _Engine())

    material_registry.refresh_cache()
    first = material_registry.get_known_materials()
    second = material_registry.get_known_materials()

    assert "SUS304" in first
    assert second == first
    assert calls == ["ensure"]


def test_database_failure_never_breaks_public_material_normalization(monkeypatch):
    monkeypatch.setattr(
        repository,
        "_ensure_engine",
        lambda: (_ for _ in ()).throw(RuntimeError("connection details must stay internal")),
    )

    material_registry.refresh_cache()

    assert "SUS304" in material_registry.get_known_materials()
    assert material_registry.normalize_material("INOX") == "stainless steel"
    assert material_registry.normalize_material(None) is None
    assert material_registry.normalize_material("unlisted polymer") == "unlisted polymer"
