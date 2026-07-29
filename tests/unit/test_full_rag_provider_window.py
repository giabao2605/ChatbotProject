from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path

import pytest

from mech_chatbot.config.settings import Settings
from scripts.rerank_provider_eval import run_full_rag_window as runner


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "rerank_provider_full_rag_v1" / "eval_manifest.jsonl"
APPROVAL_REF = "codex-thread:019fab5f-2aa9-71d2-9bc1-0ecaf3b6d931"


def test_followup_window_has_a_new_declaration_identity():
    assert runner.WINDOW_ID == "full-rag-provider-019fab5f-v2"
    assert runner.DECLARATION_PATH.name == (
        "full-rag-provider-019fab5f-v2-declaration.json"
    )


def _eval_report(
    *,
    provider: str,
    profile_sha256: str,
    manifest_sha256: str,
    snapshot: str = "fixture-sha",
) -> dict:
    cases = [
        {
            "id": "crag-number-thousands",
            "passed": True,
            "actual_outcome": "full_answer",
            "latency_ms": 100.0,
            "leaked": False,
            "provider_failure": False,
            "provider_retries": 0,
            "correction_count": 0,
            "repair_count": 0,
            "final_generation_count": 1,
        },
        {
            "id": "crag-no-cost-refusal",
            "passed": True,
            "actual_outcome": "insufficient_evidence",
            "latency_ms": 110.0,
            "leaked": False,
            "provider_failure": False,
            "provider_retries": 0,
            "correction_count": 0,
            "repair_count": 0,
            "final_generation_count": 1,
        },
        {
            "id": "crag-restricted-denial",
            "passed": True,
            "actual_outcome": "access_denied",
            "latency_ms": 90.0,
            "leaked": False,
            "provider_failure": False,
            "provider_retries": 0,
            "correction_count": 0,
            "repair_count": 0,
            "final_generation_count": 0,
        },
    ]
    return {
        "schema": "rag-labeled-eval-v4",
        "git_sha": "commit-sha",
        "manifest_sha256s": [manifest_sha256],
        "snapshot_fingerprint": snapshot,
        "provider_configuration_sha256": profile_sha256,
        "governance_scope_sha256": "governance-sha",
        "benchmark_concurrency": 1,
        "collection": "MechChatbot_CRAG_Eval_v1",
        "execution_context": "evaluation",
        "feature_flags": {
            "crag": "false",
            "claim_repair": "false",
            "semantic_cache": "false",
            "evaluation_router_mode": "offline",
            "llm_router": "false",
            "semantic_router": "false",
            "grounded_math": "false",
            "late_interaction": "false",
            "query_decomposition": "false",
            "graph_retrieval": "false",
        },
        "pipeline_configuration": {
            "crag": False,
            "claim_repair": False,
            "grounded_math": False,
            "late_interaction": False,
            "query_decomposition": False,
            "graph_retrieval": False,
            "community_summaries": False,
            "semantic_cache": False,
            "router_mode": "offline",
            "concurrency": 1,
        },
        "run_label": provider,
        "total_cases": 3,
        "passed_cases": 3,
        "latency_p95_ms": 110.0,
        "total_estimated_cost": 0.0,
        "provider_retries": 0,
        "budget_counts": {"correction_count": 0, "repair_count": 0},
        "outcome_confusion": {"wrong_answer": 0, "leakage": 0},
        "ranked_retrieval": {
            "recall_at_5": 1.0,
            "ndcg_at_5": 1.0,
            "recall_at_10": 1.0,
            "ndcg_at_10": 1.0,
            "recall_at_20": 1.0,
            "mrr": 1.0,
        },
        "cases": cases,
    }


def _trace(provider: str, salt: str) -> dict:
    zero = {
        "call_count": 0,
        "success_count": 0,
        "error_count": 0,
        "fallback_count": 0,
        "error_rate": 0.0,
        "fallback_rate": 0.0,
        "status_codes": {},
        "retry_attempt_count": 0,
    }
    active = {
        **zero,
        "call_count": 3,
        "success_count": 3,
        "status_codes": {"200": 3},
    }
    return {
        "schema": "rag-refusal-snapshot-v1",
        "source": {
            "git_sha": "commit-sha",
            "sha256": f"{provider}-{salt}-trace-sha",
        },
        "filters": {
            "execution_contexts": ["evaluation"],
            "excluded_reasons": ["client_cancelled"],
            "exclude_empty_reason": True,
        },
        "parse_errors": 0,
        "rerank_by_provider": {
            "voyage": active if provider == "voyage" else zero,
            "jina": active if provider == "jina" else zero,
        },
        "system_metrics": {
            "query_count": 3,
            "latency_p50_ms": 100.0,
            "latency_p95_ms": 110.0,
            "estimated_cost": 0.0,
            "max_corrections_per_query": 0,
            "max_repairs_per_query": 0,
            "retry_rate": 0.0,
        },
    }


def _fake_arm_runner(events, manifest_sha256):
    def run(label, manifest, output, trace_path, **kwargs):
        provider = kwargs["provider_environment"]["RERANK_PROVIDER"]
        events.append(
            (
                "arm",
                label,
                provider,
                kwargs["started_at"],
                kwargs["provider_configuration_sha256"],
                dict(kwargs["provider_environment"]),
            )
        )
        run_dir = output / label
        run_dir.mkdir(parents=True)
        report = _eval_report(
            provider=provider,
            profile_sha256=kwargs["provider_configuration_sha256"],
            manifest_sha256=manifest_sha256,
        )
        report["started_at"] = kwargs["started_at"]
        (run_dir / "eval.json").write_text(json.dumps(report), encoding="utf-8")
        (run_dir / "preflight.json").write_text(
            json.dumps({"passed": True, "fixture_fingerprint": "fixture-sha"}),
            encoding="utf-8",
        )
        trace_path.write_text("", encoding="utf-8")
        (run_dir / "trace.json").write_text(
            json.dumps(_trace(provider, str(trace_path))),
            encoding="utf-8",
        )
        return {
            "label": label,
            "started_at": kwargs["started_at"],
            "completed_at": kwargs["started_at"],
            "runner_exit": 0,
        }

    return run


def _profiles() -> dict:
    return {
        "voyage": {
            "provider": "voyage",
            "model": "rerank-2.5-lite",
            "profile_sha256": "a" * 64,
            "generation_provider_sha256": "c" * 64,
            "environment": {
                "RERANK_PROVIDER": "wrong",
                "VOYAGE_API_KEY": "VOYAGE_SECRET",
            },
        },
        "jina": {
            "provider": "jina",
            "model": "jina-reranker-v3",
            "profile_sha256": "b" * 64,
            "generation_provider_sha256": "c" * 64,
            "environment": {
                "RERANK_PROVIDER": "wrong",
                "JINA_API_KEY": "JINA_SECRET",
            },
        },
    }


def _smoke(tmp_path: Path) -> Path:
    path = tmp_path / "provider-smoke.json"
    path.write_text(
        json.dumps({"schema": "provider-smoke-v1", "secret": "not-present"}),
        encoding="utf-8",
    )
    return path


def test_manifest_is_exact_locked_three_case_governed_fixture():
    cases = [
        json.loads(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
    ]

    runner.require_approved_manifest(MANIFEST)

    assert [case["id"] for case in cases] == [
        "crag-number-thousands",
        "crag-no-cost-refusal",
        "crag-restricted-denial",
    ]


def test_profile_configuration_projects_pydantic_settings_to_evaluation(
    monkeypatch,
):
    original = Settings(RAG_EXECUTION_CONTEXT="production")
    observed = []
    monkeypatch.setattr(runner, "load_settings", lambda _path: original)
    monkeypatch.setattr(
        runner,
        "configured_repository_runtime",
        lambda settings, **_kwargs: nullcontext(),
    )

    def profiles(settings):
        observed.append(settings.RAG_EXECUTION_CONTEXT)
        return {}, {
            "voyage": {
                "provider": "voyage",
                "model": "rerank-2.5-lite",
                "profile_sha256": "a" * 64,
            },
            "jina": {
                "provider": "jina",
                "model": "jina-reranker-v3",
                "profile_sha256": "b" * 64,
            },
        }

    monkeypatch.setattr(runner, "_real_provider_calls", profiles)
    monkeypatch.setattr(
        runner,
        "provider_environment_for_settings",
        lambda _settings: {},
    )
    monkeypatch.setattr(
        runner,
        "provider_configuration_sha256_for_settings",
        lambda _settings: "c" * 64,
    )

    configurations = runner.load_profile_configurations()

    assert observed == ["evaluation"]
    assert original.RAG_EXECUTION_CONTEXT == "production"
    assert configurations["jina"]["generation_provider_sha256"] == "c" * 64


def test_window_runs_predeclared_orders_paces_voyage_and_writes_authorization(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(runner.LIVE_OPT_IN, "1")
    monkeypatch.setattr(runner, "require_clean_worktree", lambda: None)
    monkeypatch.setattr(runner, "require_source_commit", lambda _sha: None)
    monkeypatch.setattr(runner, "_git_sha", lambda: "commit-sha")
    declaration = tmp_path / "window-declaration.json"
    monkeypatch.setattr(runner, "DECLARATION_PATH", declaration)
    events = []
    now = [0.0]

    def sleeps(seconds):
        events.append(("sleep", seconds))
        now[0] += seconds

    output = tmp_path / "window"
    smoke_calls = []

    def validate_smoke(path, **kwargs):
        smoke_calls.append((path, kwargs))
        return {"schema": "provider-smoke-v1"}

    report = runner.run_window(
        MANIFEST,
        output,
        approval_ref=APPROVAL_REF,
        provider_smoke_artifact=_smoke(tmp_path),
        profile_configurations=_profiles(),
        arm_runner=_fake_arm_runner(events, runner._sha(MANIFEST)),
        smoke_validator=validate_smoke,
        monotonic=lambda: now[0],
        sleeper=sleeps,
        utc_now=iter(
            f"2026-07-29T00:00:{index:02d}Z" for index in range(20)
        ).__next__,
    )

    assert [pair["arm_order"] for pair in report["pairs"]] == [
        "baseline-first",
        "candidate-first",
        "baseline-first",
    ]
    assert [
        (event[1], event[2]) for event in events if event[0] == "arm"
    ] == [
        ("baseline", "voyage"),
        ("candidate", "jina"),
        ("candidate", "jina"),
        ("baseline", "voyage"),
        ("baseline", "voyage"),
        ("candidate", "jina"),
    ]
    assert [event for event in events if event[0] == "sleep"] == [
        ("sleep", 61.0),
        ("sleep", 61.0),
    ]
    arm_configuration_hashes = {
        event[2]: event[4] for event in events if event[0] == "arm"
    }
    assert set(arm_configuration_hashes) == {"voyage", "jina"}
    assert arm_configuration_hashes["voyage"] != arm_configuration_hashes["jina"]
    assert all(
        len(value) == 64
        and value not in {"a" * 64, "b" * 64, "c" * 64}
        for value in arm_configuration_hashes.values()
    )
    for event in (item for item in events if item[0] == "arm"):
        environment = event[5]
        assert environment["RERANK_PROVIDER"] == event[2]
        assert environment["RAG_EVAL_ROUTER_MODE"] == "offline"
        assert environment["SEMANTIC_CACHE_ENABLED"] == "false"
        assert environment["LLM_ROUTER_ENABLED"] == "false"
        assert environment["SEMANTIC_ROUTER_ENABLED"] == "false"
        assert all(
            environment[name] == "false"
            for name in (
                "RAG_CRAG_ENABLED",
                "RAG_CLAIM_REPAIR_ENABLED",
                "RAG_GROUNDED_MATH_ENABLED",
                "RAG_LATE_INTERACTION_ENABLED",
                "RAG_QUERY_DECOMPOSITION_ENABLED",
                "RAG_GRAPH_RETRIEVAL_ENABLED",
                "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
            )
        )
    assert report["passed"] is True
    assert report["release_authorized"] is False
    assert len(smoke_calls) == 6
    assert smoke_calls[-1][1]["expected_provider_sha256"] == "c" * 64
    assert len(smoke_calls[-1][1]["arm_started_at"]) == 6
    authorization = json.loads(
        (output / "authorization.json").read_text(encoding="utf-8")
    )
    assert authorization["approval_ref"] == (
        APPROVAL_REF
    )
    assert authorization["technical_authorized"] is True
    serialized = json.dumps(report)
    assert "VOYAGE_SECRET" not in serialized
    assert "JINA_SECRET" not in serialized
    assert "Giá trị định mức" not in serialized
    assert json.loads(declaration.read_text(encoding="utf-8"))["status"] == (
        "accepted"
    )


def test_window_stops_after_first_failed_pair(monkeypatch, tmp_path):
    monkeypatch.setenv(runner.LIVE_OPT_IN, "1")
    monkeypatch.setattr(runner, "require_clean_worktree", lambda: None)
    monkeypatch.setattr(runner, "require_source_commit", lambda _sha: None)
    monkeypatch.setattr(runner, "_git_sha", lambda: "commit-sha")
    declaration = tmp_path / "window-declaration.json"
    monkeypatch.setattr(runner, "DECLARATION_PATH", declaration)
    events = []
    arm_runner = _fake_arm_runner(events, runner._sha(MANIFEST))

    def failed_arm(*args, **kwargs):
        result = arm_runner(*args, **kwargs)
        if args[0] == "candidate":
            path = args[2] / args[0] / "eval.json"
            report = json.loads(path.read_text(encoding="utf-8"))
            report["cases"][0]["passed"] = False
            report["passed_cases"] = 2
            path.write_text(json.dumps(report), encoding="utf-8")
        return result

    report = runner.run_window(
        MANIFEST,
        tmp_path / "window",
        approval_ref=APPROVAL_REF,
        provider_smoke_artifact=_smoke(tmp_path),
        profile_configurations=_profiles(),
        arm_runner=failed_arm,
        smoke_validator=lambda *_args, **_kwargs: {},
        monotonic=iter([0.0, 1.0]).__next__,
        sleeper=lambda _seconds: None,
        utc_now=iter(
            f"2026-07-29T00:00:{index:02d}Z" for index in range(10)
        ).__next__,
    )

    assert len([event for event in events if event[0] == "arm"]) == 2
    assert report["passed"] is False
    assert report["pair_count"] == 1
    assert (tmp_path / "window" / "run.json").exists()
    assert not (tmp_path / "window" / "authorization.json").exists()
    assert json.loads(declaration.read_text(encoding="utf-8"))["status"] == (
        "rejected"
    )
    with pytest.raises(RuntimeError, match="already declared"):
        runner.run_window(
            MANIFEST,
            tmp_path / "prettier-window",
            approval_ref=APPROVAL_REF,
            provider_smoke_artifact=_smoke(tmp_path),
            profile_configurations=_profiles(),
        )


def test_window_rejects_arm_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setenv(runner.LIVE_OPT_IN, "1")
    monkeypatch.setattr(runner, "require_clean_worktree", lambda: None)
    monkeypatch.setattr(runner, "require_source_commit", lambda _sha: None)
    monkeypatch.setattr(runner, "_git_sha", lambda: "commit-sha")
    declaration = tmp_path / "window-declaration.json"
    monkeypatch.setattr(runner, "DECLARATION_PATH", declaration)
    events = []
    arm_runner = _fake_arm_runner(events, runner._sha(MANIFEST))

    def nonzero_arm(*args, **kwargs):
        result = dict(arm_runner(*args, **kwargs))
        result["runner_exit"] = 1
        return result

    with pytest.raises(RuntimeError, match="non-zero exit"):
        runner.run_window(
            MANIFEST,
            tmp_path / "window",
            approval_ref=APPROVAL_REF,
            provider_smoke_artifact=_smoke(tmp_path),
            profile_configurations=_profiles(),
            arm_runner=nonzero_arm,
            smoke_validator=lambda *_args, **_kwargs: {},
            monotonic=lambda: 0.0,
            sleeper=lambda _seconds: None,
            utc_now=iter(
                f"2026-07-29T00:00:{index:02d}Z" for index in range(10)
            ).__next__,
        )
    assert json.loads(declaration.read_text(encoding="utf-8"))["status"] == (
        "declared"
    )


def test_window_refuses_missing_opt_in_dirty_output_and_manifest_drift(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv(runner.LIVE_OPT_IN, raising=False)
    with pytest.raises(RuntimeError, match=runner.LIVE_OPT_IN):
        runner.run_window(
            MANIFEST,
            tmp_path / "first",
            approval_ref=APPROVAL_REF,
            provider_smoke_artifact=_smoke(tmp_path),
            profile_configurations=_profiles(),
        )

    monkeypatch.setenv(runner.LIVE_OPT_IN, "1")
    monkeypatch.setattr(runner, "require_clean_worktree", lambda: None)
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "existing.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty"):
        runner.run_window(
            MANIFEST,
            dirty,
            approval_ref=APPROVAL_REF,
            provider_smoke_artifact=_smoke(tmp_path),
            profile_configurations=_profiles(),
        )

    changed = tmp_path / "manifest.jsonl"
    changed.write_text(MANIFEST.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="approved manifest"):
        runner.run_window(
            changed,
            tmp_path / "changed",
            approval_ref=APPROVAL_REF,
            provider_smoke_artifact=_smoke(tmp_path),
            profile_configurations=_profiles(),
        )
