import json
import os
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.unit


def test_graph_arm_forwards_exact_case_selector(monkeypatch, tmp_path):
    from scripts.graph_eval import run_rollout as rollout

    output = tmp_path / "output"
    trace = tmp_path / "trace.jsonl"
    trace.write_text("", encoding="utf-8")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        run_dir = output / "baseline"
        run_dir.mkdir(parents=True, exist_ok=True)
        if "scripts.eval.run_eval" in command:
            (run_dir / "eval.json").write_text("{}\n", encoding="utf-8")
        else:
            (run_dir / "trace.json").write_text("{}\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(rollout.subprocess, "run", fake_run)

    rollout._run(
        "baseline",
        tmp_path / "manifest.jsonl",
        output,
        trace,
        enabled=False,
        provider_sha="a" * 64,
        governance_sha="b" * 64,
        case_id="graph-case-02",
    )

    assert commands[0][-2:] == ["--case-id", "graph-case-02"]
    assert "--case-id" not in commands[1]


def test_graph_rollout_toggles_only_graph_between_arms(monkeypatch):
    from scripts.graph_eval.run_rollout import build_evaluation_environment

    monkeypatch.setenv("RAG_CRAG_ENABLED", "stale")
    monkeypatch.setenv("RAG_CLAIM_REPAIR_ENABLED", "stale")
    monkeypatch.setenv("RAG_GRAPH_RETRIEVAL_ENABLED", "stale")
    monkeypatch.setenv("EXTERNAL_PROCESSING_POLICY", "internal_only")

    baseline = build_evaluation_environment(enabled=False)
    candidate = build_evaluation_environment(enabled=True)

    fixed_flags = {
        "RAG_CRAG_ENABLED": "false",
        "RAG_CLAIM_REPAIR_ENABLED": "false",
        "RAG_GROUNDED_MATH_ENABLED": "false",
        "RAG_QUERY_DECOMPOSITION_ENABLED": "false",
        "RAG_LATE_INTERACTION_ENABLED": "false",
        "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED": "false",
    }
    assert {key: baseline[key] for key in fixed_flags} == fixed_flags
    assert {key: candidate[key] for key in fixed_flags} == fixed_flags
    assert baseline["RAG_GRAPH_RETRIEVAL_ENABLED"] == "false"
    assert candidate["RAG_GRAPH_RETRIEVAL_ENABLED"] == "true"
    assert baseline["RAG_ACTIVATION_PROFILE"] == "all_off"
    assert candidate["RAG_ACTIVATION_PROFILE"] == "selective"
    assert baseline["RAG_ACTIVATION_SCOPE"] == "evaluation"
    assert candidate["RAG_ACTIVATION_SCOPE"] == "evaluation"
    assert baseline["RAG_EXECUTION_CONTEXT"] == "evaluation"
    assert candidate["RAG_EXECUTION_CONTEXT"] == "evaluation"
    assert baseline["EXTERNAL_PROCESSING_POLICY"] == "all_external"
    assert candidate["EXTERNAL_PROCESSING_POLICY"] == "all_external"
    assert baseline["RAG_ACTIVATION_BUNDLE_PATH"] == ""
    assert candidate["RAG_ACTIVATION_BUNDLE_PATH"] == ""
    assert baseline["RAG_ACTIVATION_BUNDLE_SHA256"] == ""
    assert candidate["RAG_ACTIVATION_BUNDLE_SHA256"] == ""
    assert os.environ["RAG_CRAG_ENABLED"] == "stale"
    assert os.environ["EXTERNAL_PROCESSING_POLICY"] == "internal_only"


def test_graph_fixture_meets_phase_four_relational_floor():
    from scripts.graph_eval.generate_fixture import cases

    relational = [
        case for case in cases() if case["evaluation_group"] == "relational"
    ]
    relation_types = {
        relation["relation_type"]
        for case in relational
        for relation in (
            case.get("expected_relations")
            or [case.get("expected_relation")]
        )
        if relation
    }

    assert len(relational) >= 10
    assert {
        case["expected_department"] for case in relational
    } >= {"Technical", "Production", "Maintenance"}
    assert relation_types >= {
        "HAS_VERSION",
        "SUPERSEDES",
        "CONTAINS_PART",
        "USES_MATERIAL",
        "APPLIES_TO",
    }
    assert all(
        claim.get("positive_relation", {}).get("predicate")
        and claim["positive_relation"].get("target_terms")
        and {"không", "chưa", "chẳng", "chả"} <= set(
            claim["positive_relation"].get("negation_terms") or []
        )
        for case in relational
        for claim in case["expected_claims"]
    )


def _write_provider_smoke(
    path,
    provider_sha,
    *,
    completed_at="2026-07-28T00:00:00Z",
):
    path.write_text(
        json.dumps({
            "schema": "provider-smoke-v1",
            "passed": True,
            "request_count": 5,
            "successful_requests": 5,
            "failed_requests": 0,
            "provider_retries": 0,
            "completed_at": completed_at,
            "provider_configuration_sha256": provider_sha,
            "provider_outcome": {"provider_blocked": False},
        }),
        encoding="utf-8",
    )
    return path


def test_graph_rollout_records_runtime_provider_hash(monkeypatch, tmp_path):
    from mech_chatbot.config import settings as settings_module
    from mech_chatbot.config.settings import Settings
    from scripts.eval.provider_smoke import provider_configuration_sha256_for_settings
    from scripts.graph_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"
    snapshot = Settings.from_env({
        "PROXYLLM_API_KEY": "test-provider-key",
        "PROXYLLM_BASE_URL": "https://provider.example/v1",
        "GPT_MODEL_NAME": "snapshot-model",
        "MAX_CONCURRENT_RAG": "7",
    })
    expected = provider_configuration_sha256_for_settings(snapshot)
    smoke = _write_provider_smoke(
        tmp_path / "provider-smoke.json",
        expected,
    )

    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")
    monkeypatch.setattr(rollout, "_utc_now", lambda: "2026-07-28T00:01:00Z")
    monkeypatch.setattr(settings_module, "load_settings", lambda: snapshot)
    monkeypatch.setattr(rollout, "require_clean_worktree", lambda: None)
    monkeypatch.setattr(
        rollout.subprocess, "check_output", lambda *args, **kwargs: "abc123\n"
    )

    def fake_arm(label, *args, **kwargs):
        run_dir = output / label
        run_dir.mkdir(parents=True)
        for name, payload in (
            ("eval.json", {"schema": "rag-labeled-eval-v4"}),
                ("trace.json", {"schema": "rag-refusal-snapshot-v1"}),
                ("preflight.json", {
                    "fixture_fingerprint": "fixture-sha",
                    "graph_report": {
                        "schema": "graph-readiness-v1",
                        "passed": True,
                    },
                }),
        ):
            (run_dir / name).write_text(json.dumps(payload), encoding="utf-8")
        return {
            "started_at": kwargs.get(
                "started_at",
                "2026-07-28T00:00:00Z",
            ),
            "completed_at": "2026-07-28T00:01:00Z",
            "runner_exit": 0,
        }

    def fake_subprocess_run(command, **kwargs):
        gate_path = output / "gate.json"
        gate_path.write_text(
            json.dumps({
                "schema": "retrieval-intelligence-gate-v1",
                "passed": True,
            }),
            encoding="utf-8",
        )
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(rollout, "_run", fake_arm)
    monkeypatch.setattr(rollout.subprocess, "run", fake_subprocess_run)
    from mech_chatbot.evaluation import rollout_guardrails
    monkeypatch.setattr(
        rollout_guardrails,
        "evaluate_rollout_pair",
        lambda pair: {
            "production_eligible": True,
            "checks": {"rollback_contract_valid": True},
        },
    )

    report = rollout.run_rollout(
        manifest,
        output,
        trace,
        provider_smoke_artifact=smoke,
    )
    pair = json.loads((output / "rollout_pair.json").read_text(encoding="utf-8"))

    assert pair["baseline"]["provider_configuration_sha256"] == expected
    assert pair["candidate"]["provider_configuration_sha256"] == expected
    assert pair["baseline"]["started_at"] == "2026-07-28T00:01:00Z"
    assert pair["baseline"]["trace_schema"] == "rag-refusal-snapshot-v1"
    assert pair["candidate"]["trace_schema"] == "rag-refusal-snapshot-v1"
    assert pair["metadata"]["artifact_schema"] == "graph-readiness-v1"
    assert len(pair["metadata"]["artifact_sha256"]) == 64
    assert report["passed"] is True
    assert report["technical_eligible"] is True
    assert report["production_eligible"] is False
    assert report["decision_status"] == "pending_formal_series"
    assert report["guardrail_checks"] == {"rollback_contract_valid": True}


def test_graph_rollout_rejects_manifest_drift_after_baseline(monkeypatch, tmp_path):
    from mech_chatbot.config import settings as settings_module
    from mech_chatbot.config.settings import Settings
    from scripts.graph_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"
    checks = iter(["before", "after"])
    snapshot = Settings.from_env({})
    from scripts.eval.provider_smoke import provider_configuration_sha256_for_settings
    smoke = _write_provider_smoke(
        tmp_path / "provider-smoke.json",
        provider_configuration_sha256_for_settings(snapshot),
    )

    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")
    monkeypatch.setattr(rollout, "_utc_now", lambda: "2026-07-28T00:01:00Z")
    monkeypatch.setattr(settings_module, "load_settings", lambda: snapshot)
    monkeypatch.setattr(rollout, "require_clean_worktree", lambda: None)
    monkeypatch.setattr(rollout, "_sha", lambda path: next(checks))
    monkeypatch.setattr(
        rollout.subprocess, "check_output", lambda *args, **kwargs: "abc123\n"
    )
    monkeypatch.setattr(
        rollout,
        "_run",
        lambda *args, **kwargs: {
            "started_at": "2026-07-28T00:00:00Z",
            "completed_at": "2026-07-28T00:01:00Z",
            "runner_exit": 0,
        },
    )

    with pytest.raises(RuntimeError, match="manifest changed after baseline"):
        rollout.run_rollout(
            manifest,
            output,
            trace,
            provider_smoke_artifact=smoke,
        )


def test_graph_rollout_rejects_stale_provider_smoke_before_eval(
    monkeypatch,
    tmp_path,
):
    from mech_chatbot.config import settings as settings_module
    from mech_chatbot.config.settings import Settings
    from scripts.eval.provider_smoke import provider_configuration_sha256_for_settings
    from scripts.graph_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    snapshot = Settings.from_env({})
    smoke = _write_provider_smoke(
        tmp_path / "provider-smoke.json",
        provider_configuration_sha256_for_settings(snapshot),
        completed_at="2026-07-28T00:00:00Z",
    )

    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")
    monkeypatch.setattr(settings_module, "load_settings", lambda: snapshot)
    monkeypatch.setattr(rollout, "require_clean_worktree", lambda: None)
    monkeypatch.setattr(rollout, "_utc_now", lambda: "2026-07-28T00:31:00Z")
    monkeypatch.setattr(
        rollout.subprocess,
        "check_output",
        lambda *args, **kwargs: "abc123\n",
    )
    monkeypatch.setattr(
        rollout,
        "_run",
        lambda *args, **kwargs: pytest.fail(
            "evaluation started with stale smoke"
        ),
    )

    with pytest.raises(ValueError, match="older than 30 minutes"):
        rollout.run_rollout(
            manifest,
            tmp_path / "rollout",
            trace,
            provider_smoke_artifact=smoke,
        )
