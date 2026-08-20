import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PREPARATION = (
    ROOT
    / "data"
    / "integrated_hardening_v1"
    / "evidence"
    / "query-crag-offline-preparation.json"
)
RUNBOOK = ROOT / "docs" / "query-crag-offline-next-window-runbook.md"
GOVERNED_FLAGS = {
    "RAG_CRAG_ENABLED",
    "RAG_CLAIM_REPAIR_ENABLED",
    "RAG_GROUNDED_MATH_ENABLED",
    "RAG_LATE_INTERACTION_ENABLED",
    "RAG_QUERY_DECOMPOSITION_ENABLED",
    "RAG_GRAPH_RETRIEVAL_ENABLED",
    "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
}
EXECUTION_BINDINGS = {
    "source_commit",
    "manifest_sha256",
    "fixture_preflight_sha256",
    "provider_configuration_sha256",
    "fresh_provider_smoke_sha256",
    "rollback_evidence_sha256",
    "governance_scope_sha256",
    "runner_sha256",
    "release_decisions_sha256",
    "owner_declaration_sha256",
}
DYNAMIC_BINDINGS = {
    "source_commit",
    "fixture_preflight_sha256",
    "provider_configuration_sha256",
    "fresh_provider_smoke_sha256",
    "rollback_evidence_sha256",
    "governance_scope_sha256",
    "owner_declaration_sha256",
}
SOURCE_COMMIT = "67265a0bd6135f9f205521e99bd51870a955b014"
RELEASE_DECISIONS_SHA256 = (
    "0e41b33f87b0f82be66453f105bd956380cfd67c89927aa9914539dfda971208"
)
ACTIVATION_BUNDLE_SHA256 = (
    "d2b146bb36ba66e3ec6319391fccf3228776f34287b18a0ff490befd588ba660"
)
CRAG_PROVIDER_SMOKE_CONTRACT = {
    "required_passed": 5,
    "required_total": 5,
    "maximum_provider_retries": 0,
    "maximum_age_minutes_at_arm_start": 30,
    "same_provider_configuration_required": True,
}


def _load() -> dict:
    return json.loads(PREPARATION.read_text(encoding="utf-8"))


def test_query_preparation_is_offline_and_fail_closed():
    preparation = _load()

    assert preparation["schema"] == "query-crag-offline-preparation-v1"
    assert preparation["status"] == "predeclared_unexecuted"
    assert preparation["prepared_from_commit"] == SOURCE_COMMIT
    assert set(preparation["authorization"].values()) == {False}
    assert preparation["math_campaign_dependency"] == {
        "campaign_id": "19aacefbe67b1aa3907a490c",
        "must_be_terminal_before_provider_traffic": True,
        "completion_marker_required": True,
        "artifacts_are_read_only": True,
    }
    assert preparation["math_default_rollout_dependency"] == {
        "source_commit": SOURCE_COMMIT,
        "release_decisions_sha256": RELEASE_DECISIONS_SHA256,
        "activation_bundle_sha256": ACTIVATION_BUNDLE_SHA256,
        "accepted_features": ["RAG_GROUNDED_MATH_ENABLED"],
        "required_off_features": [
            "RAG_CRAG_ENABLED",
            "RAG_CLAIM_REPAIR_ENABLED",
            "RAG_LATE_INTERACTION_ENABLED",
            "RAG_QUERY_DECOMPOSITION_ENABLED",
            "RAG_GRAPH_RETRIEVAL_ENABLED",
            "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
        ],
        "artifacts_are_read_only": True,
    }
    assert preparation["carry_forward"] == {
        "requests": 0,
        "elapsed_time_seconds": 0,
        "traces": 0,
        "artifacts": [],
    }

    query = preparation["capabilities"]["query_decomposition"]
    assert query["status"] == "off_pending_fresh_declared_window"
    assert query["manifest_reference"] == {
        "path": "data/decomposition_eval_v1/eval_manifest.jsonl",
        "case_count": 13,
        "prepared_sha256": (
            "6976cbbe4c9500b7c0755c5944775e326106a780bb2910bfa71167787a1d0bf8"
        ),
    }
    assert set(query["baseline_feature_flags"]) == GOVERNED_FLAGS
    assert not any(query["baseline_feature_flags"].values())
    assert set(query["candidate_feature_flags"]) == GOVERNED_FLAGS
    assert {
        name for name, enabled in query["candidate_feature_flags"].items() if enabled
    } == {"RAG_QUERY_DECOMPOSITION_ENABLED"}
    assert set(query["execution_bindings"]) == EXECUTION_BINDINGS
    assert query["execution_bindings"]["manifest_sha256"] == query[
        "manifest_reference"
    ]["prepared_sha256"]
    assert query["execution_bindings"]["runner_sha256"] == (
        "1f9e451140c90bce00352da1a7f6ef26c5a602a4d2aab086a038467aa53888a8"
    )
    assert query["execution_bindings"]["release_decisions_sha256"] == (
        RELEASE_DECISIONS_SHA256
    )
    assert {query["execution_bindings"][name] for name in DYNAMIC_BINDINGS} == {
        None
    }
    assert query["provider_smoke_contract"] == {
        "required_passed": 5,
        "required_total": 5,
        "maximum_provider_retries": 0,
        "maximum_age_minutes_at_baseline_start": 30,
        "same_provider_configuration_required": True,
    }
    assert query["thresholds"] == {
        "minimum_complex_answer_gain": 0.10,
        "maximum_latency_ratio": 1.5,
        "maximum_cost_ratio": 1.5,
        "maximum_subqueries": 3,
        "maximum_corrections": 1,
        "maximum_final_generations": 1,
        "maximum_terminal_answer_violations": 0,
        "required_branch_accuracy": 1.0,
        "required_branch_citation_accuracy": 1.0,
    }


def test_crag_preparation_is_offline_and_isolates_both_candidate_flags():
    crag = _load()["capabilities"]["crag_claim_repair"]

    assert crag["status"] == "off_pending_provider_recovery_and_fresh_window"
    assert crag["manifest_reference"] == {
        "path": "data/crag_eval_v1/eval_manifest.jsonl",
        "case_count": 9,
        "prepared_sha256": (
            "beac3aac28b59ac57930b2c7099997efa7bdfda2a76bf65e3f1620d4b0fb897b"
        ),
    }
    assert set(crag["baseline_feature_flags"]) == GOVERNED_FLAGS
    assert not any(crag["baseline_feature_flags"].values())
    assert set(crag["candidate_feature_flags"]) == GOVERNED_FLAGS
    assert {
        name for name, enabled in crag["candidate_feature_flags"].items() if enabled
    } == {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"}
    assert set(crag["execution_bindings"]) == EXECUTION_BINDINGS
    assert crag["execution_bindings"]["manifest_sha256"] == crag[
        "manifest_reference"
    ]["prepared_sha256"]
    assert crag["execution_bindings"]["runner_sha256"] == (
        "62b333ae70e34b13b45c95049fad096327b457b779de4e928a7ffe45d3086955"
    )
    assert crag["execution_bindings"]["release_decisions_sha256"] == (
        RELEASE_DECISIONS_SHA256
    )
    assert {crag["execution_bindings"][name] for name in DYNAMIC_BINDINGS} == {
        None
    }
    assert crag["provider_smoke_contract"] == CRAG_PROVIDER_SMOKE_CONTRACT
    assert crag["thresholds"] == {
        "maximum_latency_ratio": 1.25,
        "maximum_cost_ratio": 1.5,
        "maximum_corrections_per_query": 1,
        "maximum_repairs_per_query": 1,
        "maximum_provider_retries": 0,
        "candidate_cases_required_pass_rate": 1.0,
        "maximum_leakage": 0,
        "wrong_answers_may_increase": False,
    }


def test_each_provider_smoke_command_has_a_mechanical_math_terminal_guard():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    powershell_blocks = runbook.split("```powershell\n")[1:]
    smoke_blocks = [
        block.split("```", 1)[0]
        for block in powershell_blocks
        if "scripts.eval.provider_smoke" in block
    ]

    assert len(smoke_blocks) == 2
    assert all(
        block.index("Assert-MathCampaignTerminal")
        < block.index("scripts.eval.provider_smoke")
        for block in smoke_blocks
    )
    assert all(
        block.index("Assert-MathDefaultRollout")
        < block.index("scripts.eval.provider_smoke")
        for block in smoke_blocks
    )
    assert 'if (!(Test-Path -LiteralPath "$CampaignRoot\\stop.marker"))' in runbook
    assert "math_campaign_completion_marker_missing" in runbook
    assert 'if (Test-Path -LiteralPath "$CampaignRoot\\stop.marker")' not in runbook
    assert "function Assert-MathDefaultRollout" in runbook
    assert RELEASE_DECISIONS_SHA256 in runbook
    assert ACTIVATION_BUNDLE_SHA256 in runbook
    assert "operator-gate.json" in runbook
    assert "base-gate.json" in runbook
    assert "attempt_completed" in runbook
    assert "'Disabled'" in runbook
