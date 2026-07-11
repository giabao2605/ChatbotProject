from types import SimpleNamespace

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security]


context_builders = pytest.importorskip("mech_chatbot.rag.context_builders")


PARENT_KEY = (73, "section", "Procedure 01")


def _metadata(**overrides):
    metadata = {
        "doc_id": 73,
        "parent_section": "Procedure 01",
        "parent_page": 3,
        "chunk_index": 1,
        "site": "HQ",
        "phong_ban_quyen": ["Technical", "CHUNG"],
        "security_level": "internal",
        "required_clearance": "internal",
        "servable": True,
        "publication_state": "published",
        "lifecycle_status": "published",
        "review_status": "approved",
        "is_current": True,
        "serving_epoch": 18,
        "publication_version": 4,
    }
    metadata.update(overrides)
    return metadata


@pytest.mark.parametrize(
    "name, overrides",
    [
        ("staging", {"servable": False, "publication_state": "qdrant_synced"}),
        ("old_version", {"is_current": False, "serving_epoch": 17, "publication_version": 3}),
        ("wrong_site", {"site": "DN"}),
        ("wrong_department", {"phong_ban_quyen": ["HR", "CHUNG"]}),
        ("wrong_clearance", {"required_clearance": "confidential"}),
        ("wrong_security_level", {"security_level": "confidential"}),
    ],
)
def test_parent_chunk_safety_rejects_staging_and_scope_mismatches(name, overrides):
    selected = _metadata()
    selected_scope = context_builders._parent_access_scope(selected)

    assert selected_scope is not None
    assert context_builders._parent_chunk_is_safe(selected, PARENT_KEY, selected_scope) is True
    assert context_builders._parent_chunk_is_safe(
        _metadata(**overrides),
        PARENT_KEY,
        selected_scope,
    ) is False, name


class _ScrollClient:
    def __init__(self, points):
        self.points = points
        self.calls = []

    def scroll(self, **kwargs):
        self.calls.append(kwargs)
        return self.points, None


def _point(metadata, content):
    return SimpleNamespace(payload={"metadata": metadata, "page_content": content})


def test_parent_loader_defensively_filters_bad_points_and_carries_scope_filters(monkeypatch):
    from mech_chatbot.db import repository

    selected = _metadata()
    client = _ScrollClient(
        [
            _point(_metadata(chunk_index=2), "safe parent evidence"),
            _point(_metadata(servable=False, publication_state="draft"), "staging"),
            _point(_metadata(is_current=False, serving_epoch=17), "old version"),
            _point(_metadata(site="DN"), "wrong site"),
            _point(_metadata(phong_ban_quyen=["HR"]), "wrong department"),
            _point(_metadata(required_clearance="confidential"), "wrong clearance"),
            _point(_metadata(security_level="confidential"), "wrong security"),
        ]
    )
    monkeypatch.setattr(repository, "_get_qdrant_client", lambda: client)

    docs = context_builders._load_parent_section_chunks(PARENT_KEY, 12, selected)

    assert [doc.page_content for doc in docs] == ["safe parent evidence"]
    assert len(client.calls) == 1
    conditions = {
        condition.key: condition.match
        for condition in client.calls[0]["scroll_filter"].must
    }
    assert conditions["metadata.site"].value == "HQ"
    assert conditions["metadata.security_level"].value == "internal"
    assert conditions["metadata.phong_ban_quyen"].any == ["Technical", "CHUNG"]
    assert conditions["metadata.serving_epoch"].value == 18
    assert conditions["metadata.publication_version"].value == 4
    assert conditions["metadata.required_clearance"].value == "internal"


def test_parent_hydration_passes_selected_metadata_and_preserves_selected(monkeypatch):
    selected = SimpleNamespace(
        page_content="selected evidence",
        metadata={"doc_id": 73, "parent_section": "Procedure 01"},
    )
    called = []

    def _unexpected_loader(*args, **kwargs):
        called.append((args, kwargs))
        return []

    monkeypatch.setattr(context_builders, "_load_parent_section_chunks", _unexpected_loader)

    hydrated = context_builders.hydrate_parent_context(
        [selected],
        max_sections=1,
        max_chunks_per_section=2,
    )

    assert hydrated == [selected]
    assert called == [((PARENT_KEY, 2, selected.metadata), {})]
