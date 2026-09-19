from __future__ import annotations

from copy import deepcopy
import json

import pytest

from mech_chatbot.evaluation.full_rag_provider import (
    build_provider_technical_authorization,
    compare_full_rag_provider_reports,
)


pytestmark = pytest.mark.unit

CASE_IDS = [
    "crag-number-thousands",
    "crag-no-cost-refusal",
    "crag-restricted-denial",
]


def _eval(provider: str) -> dict:
    return {
        "schema": "rag-labeled-eval-v4",
        "git_sha": "a" * 40,
        "manifest_sha256s": ["b" * 64],
        "snapshot_fingerprint": "c" * 64,
        "provider_configuration_sha256": (
            "d" * 64 if provider == "voyage" else "e" * 64
        ),
        "governance_scope_sha256": "f" * 64,
        "benchmark_concurrency": 1,
        "collection": "MechChatbot_CRAG_Eval_v1",
        "execution_context": "evaluation",
        "feature_flags": {
            "crag": "false",
            "claim_repair": "false",
            "semantic_cache": "false",
            "evaluation_router_mode": "deterministic",
            "llm_router": "false",
            "semantic_router": "false",
            "grounded_math": "false",
            "late_interaction": "false",
            "query_decomposition": "false",
            "graph_retrieval": "false",
        },
        "pipeline_configuration": {
            "flags": {
                "RAG_CRAG_ENABLED": False,
                "RAG_CLAIM_REPAIR_ENABLED": False,
                "RAG_GROUNDED_MATH_ENABLED": False,
                "RAG_LATE_INTERACTION_ENABLED": False,
                "RAG_QUERY_DECOMPOSITION_ENABLED": False,
                "RAG_GRAPH_RETRIEVAL_ENABLED": False,
                "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED": False,
            },
            "versions": {
                "RAG_PLANNER_VERSION": "planner-v1",
                "RAG_LATE_INDEX_VERSION": "late-v2",
                "RAG_GRAPH_SERVING_EPOCH": "graph-v1",
                "RAG_COMMUNITY_SERVING_EPOCH": "community-v1",
            },
        },
        "total_cases": 3,
        "passed_cases": 3,
        "outcome_confusion": {
            "correct_answer": 1,
            "correct_refusal": 2,
        },
        "ranked_retrieval": {
            "recall_at_5": 1.0,
            "ndcg_at_5": 1.0,
            "recall_at_10": 1.0,
            "ndcg_at_10": 1.0,
            "recall_at_20": 1.0,
            "mrr": 1.0,
        },
        "latency_p95_ms": 1000.0 if provider == "voyage" else 1100.0,
        "total_estimated_cost": 0.01,
        "provider_retries": 0,
        "budget_counts": {
            "correction_count": 0,
            "repair_count": 0,
        },
        "cases": [
            {
                "id": case_id,
                "passed": True,
                "leaked": False,
                "provider_failure": False,
                "provider_retries": 0,
                "correction_count": 0,
                "repair_count": 0,
            }
            for case_id in CASE_IDS
        ],
    }


def _trace(provider: str) -> dict:
    empty = {
        "call_count": 0,
        "success_count": 0,
        "error_count": 0,
        "fallback_count": 0,
        "error_rate": 0.0,
        "fallback_rate": 0.0,
        "status_codes": {},
        "retry_attempt_count": 0,
    }
    used = {
        **empty,
        "call_count": 3,
        "success_count": 3,
        "status_codes": {"200": 3},
    }
    return {
        "schema": "rag-refusal-snapshot-v1",
        "source": {
            "path": f"C:/private/{provider}.jsonl",
            "git_sha": "a" * 40,
            "sha256": ("1" if provider == "voyage" else "2") * 64,
        },
        "filters": {
            "start": (
                "2026-07-29T00:00:00Z"
                if provider == "voyage"
                else "2026-07-29T00:05:00Z"
            ),
            "end": (
                "2026-07-29T00:04:59Z"
                if provider == "voyage"
                else "2026-07-29T00:09:59Z"
            ),
            "execution_contexts": ["evaluation"],
            "excluded_reasons": ["client_cancelled"],
            "exclude_empty_reason": True,
        },
        "parse_errors": 0,
        "system_metrics": {
            "query_count": 3,
            "latency_p95_ms": 1000.0 if provider == "voyage" else 1100.0,
            "estimated_cost": 0.01,
        },
        "rerank_by_provider": {
            "voyage": used if provider == "voyage" else empty,
            "jina": used if provider == "jina" else empty,
        },
    }


def _gate() -> dict:
    return compare_full_rag_provider_reports(
        _eval("voyage"),
        _eval("jina"),
        _trace("voyage"),
        _trace("jina"),
    )


def test_full_rag_gate_passes_only_isolated_identical_three_case_window():
    gate = _gate()

    assert gate["schema"] == "full-rag-rerank-provider-gate-v1"
    assert gate["passed"] is True
    assert gate["release_authorized"] is False
    assert gate["checks"]["quality_non_inferior"] is True
    assert gate["checks"]["baseline_voyage_isolated"] is True
    assert gate["checks"]["candidate_jina_isolated"] is True
    assert gate["latency_ratio"] == pytest.approx(1.1)
    assert gate["cost_ratio"] == 1.0
    assert gate["identity"] == {
        "source_commit": "a" * 40,
        "manifest_sha256": "b" * 64,
        "snapshot_fingerprint": "c" * 64,
        "governance_scope_sha256": "f" * 64,
        "collection": "MechChatbot_CRAG_Eval_v1",
        "benchmark_concurrency": 1,
        "case_ids": CASE_IDS,
        "baseline_provider_configuration_sha256": "d" * 64,
        "candidate_provider_configuration_sha256": "e" * 64,
    }


@pytest.mark.parametrize(
    ("mutate", "failed_check"),
    [
        (
            lambda b, c, bt, ct: c.update(git_sha="z" * 40),
            "identical_evidence_identity",
        ),
        (
            lambda b, c, bt, ct: c["cases"].pop(),
            "exact_case_set",
        ),
        (
            lambda b, c, bt, ct: c["cases"][0].update(passed=False),
            "all_cases_passed",
        ),
        (
            lambda b, c, bt, ct: c["outcome_confusion"].update(leakage=1),
            "no_leakage",
        ),
        (
            lambda b, c, bt, ct: c["outcome_confusion"].update(wrong_answer=1),
            "wrong_answers_not_increased",
        ),
        (
            lambda b, c, bt, ct: c["ranked_retrieval"].update(mrr=0.5),
            "quality_non_inferior",
        ),
        (
            lambda b, c, bt, ct: c["cases"][0].update(
                correction_count=2
            ),
            "correction_repair_bounded",
        ),
        (
            lambda b, c, bt, ct: c["budget_counts"].update(
                repair_count=4
            ),
            "correction_repair_bounded",
        ),
        (
            lambda b, c, bt, ct: c.update(latency_p95_ms=1251.0),
            "latency_within_limit",
        ),
        (
            lambda b, c, bt, ct: c.update(total_estimated_cost=0.016),
            "cost_within_limit",
        ),
        (
            lambda b, c, bt, ct: ct.update(parse_errors=1),
            "trace_contract",
        ),
        (
            lambda b, c, bt, ct: ct["rerank_by_provider"]["voyage"].update(
                call_count=1,
                success_count=1,
            ),
            "candidate_jina_isolated",
        ),
        (
            lambda b, c, bt, ct: ct["rerank_by_provider"]["jina"].update(
                retry_attempt_count=1,
            ),
            "candidate_jina_isolated",
        ),
        (
            lambda b, c, bt, ct: ct["rerank_by_provider"]["jina"].update(
                fallback_rate=0.5,
            ),
            "candidate_jina_isolated",
        ),
    ],
)
def test_full_rag_gate_fails_closed(mutate, failed_check):
    baseline = _eval("voyage")
    candidate = _eval("jina")
    baseline_trace = _trace("voyage")
    candidate_trace = _trace("jina")
    mutate(baseline, candidate, baseline_trace, candidate_trace)

    gate = compare_full_rag_provider_reports(
        baseline,
        candidate,
        baseline_trace,
        candidate_trace,
    )

    assert gate["passed"] is False
    assert gate["release_authorized"] is False
    assert failed_check in gate["failed_checks"]


def test_gate_contains_hashes_and_metadata_only():
    sentinel = "RAW_QUERY_OR_DOCUMENT_MUST_NOT_LEAK"
    baseline = _eval("voyage")
    baseline["cases"][0]["question"] = sentinel
    candidate = _eval("jina")
    candidate["cases"][0]["answer"] = sentinel

    serialized = json.dumps(
        compare_full_rag_provider_reports(
            baseline,
            candidate,
            _trace("voyage"),
            _trace("jina"),
        )
    )

    assert sentinel not in serialized
    assert "baseline_eval_sha256" in serialized
    assert "candidate_trace_sha256" in serialized


def _pair(index: int, order: str) -> dict:
    gate = _gate()
    gate["artifact_sha256s"] = {
        name: f"{index:x}" * 64
        for name in gate["artifact_sha256s"]
    }
    return {
        "schema": "rerank-provider-full-rag-pair-v1",
        "pair_index": index,
        "arm_order": order,
        "source_commit": gate["identity"]["source_commit"],
        "manifest_sha256": gate["identity"]["manifest_sha256"],
        "snapshot_fingerprint": gate["identity"]["snapshot_fingerprint"],
        "profile_sha256s": {
            "baseline": "3" * 64,
            "candidate": "4" * 64,
        },
        "gate": gate,
        "release_authorized": False,
    }


def test_three_passed_pairs_build_technical_not_release_authorization():
    pairs = [
        _pair(1, "baseline-first"),
        _pair(2, "candidate-first"),
        _pair(3, "baseline-first"),
    ]

    artifact = build_provider_technical_authorization(
        pairs,
        "codex-thread:019fab5f-2aa9-71d2-9bc1-0ecaf3b6d931",
    )

    assert artifact["schema"] == "rerank-provider-technical-authorization-v1"
    assert artifact["decision"] == "accepted"
    assert artifact["technical_authorized"] is True
    assert artifact["pilot_eligible"] is True
    assert artifact["production_authorized"] is False
    assert artifact["release_authorized"] is False
    assert len(set(artifact["pair_sha256s"])) == 3


@pytest.mark.parametrize(
    "mutate,approval_ref",
    [
        (lambda pairs: pairs[1].update(arm_order="baseline-first"), "codex-thread:ok"),
        (lambda pairs: pairs[1]["gate"].update(passed=False), "codex-thread:ok"),
        (
            lambda pairs: pairs[1].update(snapshot_fingerprint="9" * 64),
            "codex-thread:ok",
        ),
        (
            lambda pairs: pairs[1]["gate"]["identity"].update(
                manifest_sha256="9" * 64
            ),
            "codex-thread:ok",
        ),
        (
            lambda pairs: pairs[1]["gate"].update(
                artifact_sha256s=deepcopy(
                    pairs[0]["gate"]["artifact_sha256s"]
                )
            ),
            "codex-thread:ok",
        ),
        (lambda pairs: pairs[1].update(release_authorized=True), "codex-thread:ok"),
        (lambda pairs: pairs.append(deepcopy(pairs[0])), "codex-thread:ok"),
        (lambda pairs: None, "manual-approval"),
    ],
)
def test_technical_authorization_rejects_incomplete_or_drifted_series(
    mutate,
    approval_ref,
):
    pairs = [
        _pair(1, "baseline-first"),
        _pair(2, "candidate-first"),
        _pair(3, "baseline-first"),
    ]
    mutate(pairs)

    artifact = build_provider_technical_authorization(pairs, approval_ref)

    assert artifact["decision"] == "rejected"
    assert artifact["technical_authorized"] is False
    assert artifact["pilot_eligible"] is False
    assert artifact["production_authorized"] is False
    assert artifact["release_authorized"] is False
