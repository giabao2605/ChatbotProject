"""Serving reconciliation must not consume another collection's SQL rows."""

import re

import pytest
from sqlalchemy import create_engine, event, text

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.db.repositories import publication


@pytest.fixture
def documents():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS dbo"))
        connection.execute(text("""
            CREATE TABLE dbo.TaiLieu (
                DocID INTEGER, SourceSystem TEXT, TrangThaiVector INTEGER,
                Servable INTEGER DEFAULT 1, PublicationState TEXT DEFAULT 'published',
                LifecycleStatus TEXT DEFAULT 'published', ReviewStatus TEXT DEFAULT 'approved',
                IsCurrent INTEGER DEFAULT 1, IsArchived INTEGER DEFAULT 0,
                PublicationVersion INTEGER DEFAULT 1, ServingEpoch INTEGER DEFAULT 1,
                TaxonomyVersion TEXT DEFAULT 'v1', ExternalProcessingPolicy TEXT
            )
        """))
        connection.execute(text("""
            INSERT INTO dbo.TaiLieu (DocID, SourceSystem, TrangThaiVector)
            VALUES (:id, :source, 1)
        """), [
            {"id": 1, "source": "graph-eval-v1"},
            {"id": 2, "source": "grounded-math-eval-v1"},
            {"id": 3, "source": "crag-eval-v1"},
            {"id": 4, "source": "department-decomposition-eval-v1"},
            {"id": 5, "source": "upload"},
            {"id": 6, "source": None},
        ])

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def translate_top(connection, cursor, statement, parameters, context, executemany):
        # SQLite executes the actual WHERE; only SQL Server's TOP syntax differs.
        match = re.search(r"\bTOP \((\d+)\)\s*", statement)
        if match:
            statement = statement[:match.start()] + statement[match.end():]
            statement += " LIMIT " + match[1]
        return statement, parameters

    yield engine
    engine.dispose()


@pytest.mark.parametrize(("collection", "expected"), [
    ("TaiLieuKyThuat_v2", 4),
    ("MechChatbot_Graph_Eval_v1", 1),
    ("MechChatbot_GroundedMath_Eval_v1", 2),
    ("MechChatbot_CRAG_Eval_v1", 3),
])
def test_reconciliation_filters_ownership_before_batch_limit(monkeypatch, documents, collection, expected):
    calls = []
    client = object()

    def update(doc_id, metadata, **kwargs):
        calls.append((doc_id, kwargs))
        return False  # A truly missing point in this collection remains a failure.

    monkeypatch.setattr(publication._r_qdrant, "update_qdrant_metadata", update)
    result = publication.backfill_qdrant_servable(
        limit=1, db_engine=documents, qdrant_client=client, collection_name=collection,
    )
    assert result == {"total": 1, "updated": 0, "failed_doc_ids": [expected]}
    assert calls == [(expected, {
        "require_points": True, "qdrant_client": client, "collection_name": collection,
    })]


def test_bound_collection_keeps_unknown_and_legacy_sources(monkeypatch, documents):
    calls = []
    monkeypatch.setattr(publication._r_qdrant, "update_qdrant_metadata",
                        lambda doc_id, *args, **kwargs: calls.append(doc_id) or True)
    with bind_repository_runtime(qdrant_client=object(), qdrant_collection="TaiLieuKyThuat_v2"):
        result = publication.backfill_qdrant_servable(db_engine=documents)
    assert calls == [4, 5, 6]
    assert result == {"total": 3, "updated": 3, "failed_doc_ids": []}


def test_fixture_constants_share_the_reconciler_collection_contract():
    from mech_chatbot.config.constants import EVALUATION_SOURCE_COLLECTIONS
    from scripts.graph_eval import constants as graph
    from scripts.grounded_math_eval import constants as math
    from scripts.crag_eval import constants as crag

    for fixture in (graph, math, crag):
        assert EVALUATION_SOURCE_COLLECTIONS[fixture.FIXTURE_BATCH] == fixture.FIXTURE_COLLECTION
