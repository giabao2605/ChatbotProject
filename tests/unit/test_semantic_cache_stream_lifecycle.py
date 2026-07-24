import pytest

from mech_chatbot.rag import semantic_cache


pytestmark = pytest.mark.unit


def test_pipeline_namespace_isolated_by_feature_and_version():
    baseline_environment = {
        "RAG_PLANNER_VERSION": "planner-v1",
        "RAG_LATE_INDEX_VERSION": "late-v1",
        "RAG_GRAPH_SERVING_EPOCH": "graph-v1",
    }
    baseline = semantic_cache.scope_signature(
        "Technical", ["Technical"], "internal", ["HQ"], ["viewer"],
        pipeline_environment=baseline_environment,
    )
    late = semantic_cache.scope_signature(
        "Technical", ["Technical"], "internal", ["HQ"], ["viewer"],
        pipeline_environment={
            **baseline_environment,
            "RAG_LATE_INTERACTION_ENABLED": "true",
        },
    )
    reindexed = semantic_cache.scope_signature(
        "Technical", ["Technical"], "internal", ["HQ"], ["viewer"],
        pipeline_environment={
            **baseline_environment,
            "RAG_LATE_INDEX_VERSION": "late-v2",
        },
    )

    assert "pipe=" in baseline
    assert baseline != late
    assert late != reindexed


def test_evaluation_disables_semantic_cache_by_default():
    assert semantic_cache.enabled(None, "evaluation") is False
