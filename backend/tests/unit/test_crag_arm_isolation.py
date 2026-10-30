"""CRAG arms cannot inherit unrelated governed features from their host."""

import os

import pytest

from scripts.crag_eval.run_rollout import build_evaluation_environment


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("router_mode", ["offline", "provider"])
@pytest.mark.parametrize("contaminate_provider", [False, True])
def test_crag_arm_uses_exact_feature_set(
    monkeypatch, enabled, router_mode, contaminate_provider,
):
    unrelated = (
        "RAG_GROUNDED_MATH_ENABLED", "RAG_QUERY_DECOMPOSITION_ENABLED",
        "RAG_GRAPH_RETRIEVAL_ENABLED", "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
        "RAG_LATE_INTERACTION_ENABLED",
    )
    for flag in unrelated:
        monkeypatch.setenv(flag, "true")
    monkeypatch.setenv("RAG_CRAG_ENABLED", str(not enabled).lower())
    monkeypatch.setenv("RAG_CLAIM_REPAIR_ENABLED", str(not enabled).lower())
    before = dict(os.environ)

    environment = build_evaluation_environment(
        enabled=enabled, router_mode=router_mode,
        provider_environment=(
            {**{flag: "true" for flag in unrelated},
             "RAG_CRAG_ENABLED": str(not enabled).lower(),
             "RAG_CLAIM_REPAIR_ENABLED": str(not enabled).lower()}
            if contaminate_provider else None
        ),
    )

    assert {flag: environment[flag] for flag in unrelated} == {
        flag: "false" for flag in unrelated
    }
    assert environment["RAG_CRAG_ENABLED"] == str(enabled).lower()
    assert environment["RAG_CLAIM_REPAIR_ENABLED"] == str(enabled).lower()
    assert dict(os.environ) == before
