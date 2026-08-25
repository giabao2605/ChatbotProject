from types import SimpleNamespace
import threading

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
    docs = context_builders._load_parent_section_chunks(
        PARENT_KEY,
        12,
        selected,
        client=client,
        collection_name="test-knowledge",
    )

    assert [doc.page_content for doc in docs] == ["safe parent evidence"]
    assert len(client.calls) == 1
    assert client.calls[0]["timeout"] == 5
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


def test_parent_hydration_loads_unique_sections_concurrently_and_preserves_order(monkeypatch):
    selected = [
        SimpleNamespace(
            page_content=f"selected {index}",
            metadata=_metadata(
                doc_id=73 + index,
                parent_section=f"Procedure {index:02d}",
            ),
        )
        for index in range(1, 4)
    ]
    barrier = threading.Barrier(len(selected))

    def _parallel_loader(parent_key, _limit, metadata):
        barrier.wait(timeout=2)
        return [
            SimpleNamespace(page_content="first", metadata={**metadata, "chunk_index": 1}),
            SimpleNamespace(page_content="second", metadata={**metadata, "chunk_index": 2}),
        ]

    monkeypatch.setattr(context_builders, "_load_parent_section_chunks", _parallel_loader)

    hydrated = context_builders.hydrate_parent_context(
        selected,
        max_sections=3,
        max_chunks_per_section=2,
        max_workers=3,
    )

    assert [doc.metadata["doc_id"] for doc in hydrated] == [74, 75, 76]
    assert [doc.page_content for doc in hydrated] == [
        "first\n\nsecond",
        "first\n\nsecond",
        "first\n\nsecond",
    ]


def test_parent_hydration_batches_qdrant_reads_and_preserves_order():
    selected = [
        SimpleNamespace(
            page_content=f"selected {index}",
            metadata=_metadata(
                doc_id=90 + index,
                parent_section=f"Procedure {index:02d}",
            ),
        )
        for index in range(1, 4)
    ]

    class _BatchClient:
        def __init__(self):
            self.calls = []

        def query_batch_points(self, **kwargs):
            self.calls.append(kwargs)
            return [
                SimpleNamespace(
                    points=[
                        _point(
                            {**document.metadata, "chunk_index": chunk_index},
                            f"parent {index}.{chunk_index}",
                        )
                        for chunk_index in (1, 2)
                    ]
                )
                for index, document in enumerate(selected, 1)
            ]

        def scroll(self, **_kwargs):
            raise AssertionError("batch-capable clients must not use scroll workers")

    client = _BatchClient()
    hydrated = context_builders.hydrate_parent_context(
        selected,
        max_sections=3,
        max_chunks_per_section=2,
        max_workers=3,
        client=client,
        collection_name="test-knowledge",
        batch_enabled=True,
    )

    assert [doc.metadata["doc_id"] for doc in hydrated] == [91, 92, 93]
    assert [doc.page_content for doc in hydrated] == [
        "parent 1.1\n\nparent 1.2",
        "parent 2.1\n\nparent 2.2",
        "parent 3.1\n\nparent 3.2",
    ]
    assert len(client.calls) == 1
    assert len(client.calls[0]["requests"]) == 3
    assert all(request.query is None for request in client.calls[0]["requests"])
    assert client.calls[0]["timeout"] == 5
    for index, request in enumerate(client.calls[0]["requests"], 1):
        conditions = {
            condition.key: condition.match
            for condition in request.filter.must
        }
        assert conditions["metadata.doc_id"].value == 90 + index
        assert conditions["metadata.parent_section"].value == f"Procedure {index:02d}"
        assert conditions["metadata.site"].value == "HQ"
        assert conditions["metadata.security_level"].value == "internal"
        assert conditions["metadata.phong_ban_quyen"].any == [
            "Technical",
            "CHUNG",
        ]
        assert conditions["metadata.serving_epoch"].value == 18
        assert conditions["metadata.publication_version"].value == 4


def test_parent_hydration_batch_failure_is_terminal_without_scroll_retry():
    selected = SimpleNamespace(
        page_content="selected",
        metadata=_metadata(),
    )

    class _FailureClient:
        def __init__(self):
            self.batch_calls = 0
            self.scroll_calls = 0

        def query_batch_points(self, **_kwargs):
            self.batch_calls += 1
            raise RuntimeError("batch unavailable")

        def scroll(self, **_kwargs):
            self.scroll_calls += 1
            raise AssertionError("failed batches must not be retried with scroll")

    client = _FailureClient()
    with pytest.raises(RuntimeError, match="batch unavailable"):
        context_builders.hydrate_parent_context(
            [selected],
            max_workers=1,
            client=client,
            collection_name="test-knowledge",
            batch_enabled=True,
        )

    assert client.batch_calls == 1
    assert client.scroll_calls == 0


def test_parent_hydration_keeps_legacy_path_when_batch_is_disabled():
    selected = SimpleNamespace(
        page_content="selected",
        metadata=_metadata(),
    )

    class _LegacyClient(_ScrollClient):
        def query_batch_points(self, **_kwargs):
            raise AssertionError("baseline parent hydration must remain unchanged")

    client = _LegacyClient(
        [_point(_metadata(chunk_index=2), "legacy parent")]
    )
    hydrated = context_builders.hydrate_parent_context(
        [selected],
        max_workers=1,
        client=client,
        collection_name="test-knowledge",
        batch_enabled=False,
    )

    assert [document.page_content for document in hydrated] == ["selected"]
    assert len(client.calls) == 1


def test_parent_hydration_legacy_transport_failure_is_terminal_without_retry():
    selected = SimpleNamespace(
        page_content="selected",
        metadata=_metadata(),
    )

    class _FailureClient:
        def __init__(self):
            self.scroll_calls = 0

        def scroll(self, **_kwargs):
            self.scroll_calls += 1
            raise TimeoutError("TLS handshake timed out")

    client = _FailureClient()
    with pytest.raises(TimeoutError, match="TLS handshake timed out"):
        context_builders.hydrate_parent_context(
            [selected],
            max_workers=1,
            client=client,
            collection_name="test-knowledge",
            batch_enabled=False,
        )

    assert client.scroll_calls == 1


def test_parent_hydration_worker_one_is_sequential_rollback(monkeypatch):
    selected = [
        SimpleNamespace(
            page_content=f"selected {index}",
            metadata=_metadata(
                doc_id=80 + index,
                parent_section=f"Procedure {index:02d}",
            ),
        )
        for index in range(2)
    ]
    active = 0
    max_active = 0
    lock = threading.Lock()

    def _loader(_parent_key, _limit, metadata):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        with lock:
            active -= 1
        return [SimpleNamespace(page_content="only", metadata=metadata)]

    monkeypatch.setattr(context_builders, "_load_parent_section_chunks", _loader)

    assert context_builders.hydrate_parent_context(selected, max_workers=1) == selected
    assert max_active == 1
