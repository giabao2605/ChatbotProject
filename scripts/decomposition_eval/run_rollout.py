"""Run a frozen query-decomposition baseline/candidate pair."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from scripts.crag_eval.run_rollout import (
    _artifact_reference,
    _sha,
    _utc_now,
    governance_scope_sha256,
    require_source_commit,
)
from scripts.decomposition_eval.constants import (
    FIXTURE_BATCH,
    FIXTURE_COLLECTION,
    LIVE_OPT_IN,
)
from scripts.eval.provider_smoke import (
    provider_configuration_sha256_for_settings,
    provider_environment_for_settings,
    validate_provider_smoke_for_baseline,
)

ROOT = Path(__file__).resolve().parents[2]


def require_clean_worktree():
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        text=True,
    ).strip()
    if status:
        raise RuntimeError("decomposition rollout requires a clean worktree")


def build_evaluation_environment(
    *,
    enabled: bool,
    collection: str = FIXTURE_COLLECTION,
    fixture_batch: str = FIXTURE_BATCH,
):
    environment = os.environ.copy()
    environment.update({
        "RAG_EXECUTION_CONTEXT": "evaluation",
        "EXTERNAL_PROCESSING_POLICY": "all_external",
        "RAG_CRAG_ENABLED": "false", "RAG_CLAIM_REPAIR_ENABLED": "false",
        "RAG_GROUNDED_MATH_ENABLED": "false",
        "RAG_QUERY_DECOMPOSITION_ENABLED": str(enabled).lower(),
        "RAG_LATE_INTERACTION_ENABLED": "false", "RAG_GRAPH_RETRIEVAL_ENABLED": "false",
        "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED": "false",
        "RAG_ACTIVATION_PROFILE": "selective" if enabled else "all_off",
        "RAG_ACTIVATION_SCOPE": "evaluation",
        "SEMANTIC_CACHE_ENABLED": "false", "STRICT_REALTIME_STREAMING": "false",
        "QDRANT_COLLECTION": collection,
        "RAG_EVAL_EXPECTED_COLLECTION": collection,
        "RAG_EVAL_FIXTURE_BATCH": fixture_batch,
        "RAG_EVAL_PREFLIGHT_KIND": "decomposition",
        "RAG_EVAL_ROUTER_MODE": "offline", "LLM_ROUTER_ENABLED": "false",
        "SEMANTIC_ROUTER_ENABLED": "false",
    })
    return environment


def _is_strict_deterministic_local_split(event, expected_trace_ids):
    coverage = event.get("intent_coverage")
    intent_count = event.get("intent_count")
    subquery_count = event.get("subquery_count")
    zero_fields = (
        event.get("planner_count"),
        event.get("input_tokens"),
        event.get("output_tokens"),
        event.get("estimated_cost"),
        event.get("exclusive_estimated_cost"),
    )
    fallback_fields = {
        str(key).casefold()
        for key, value in event.items()
        if "fallback" in str(key).casefold() and bool(value)
    }
    return bool(
        event.get("execution_context") == "evaluation"
        and event.get("event") == "query_decomposition"
        and str(event.get("trace_id") or "") in expected_trace_ids
        and event.get("deterministic_fallback") is True
        and fallback_fields == {"deterministic_fallback"}
        and type(intent_count) is int
        and type(subquery_count) is int
        and 2 <= intent_count <= 3
        and subquery_count == intent_count
        and isinstance(coverage, list)
        and len(coverage) == intent_count
        and all(value is True for value in coverage)
        and event.get("intent_overflow") is False
        and event.get("deadline_exceeded") is False
        and all(type(value) in (int, float) and value == 0 for value in zero_fields)
    )


def _manifest_trace_ids(manifest, label):
    case_ids = []
    for raw in Path(manifest).read_text(encoding="utf-8-sig").splitlines():
        if not raw.strip():
            continue
        try:
            case_id = str(json.loads(raw).get("id") or "")
        except json.JSONDecodeError as exc:
            raise RuntimeError("frozen manifest contains invalid JSON") from exc
        if not case_id:
            raise RuntimeError("frozen manifest contains a case without an id")
        case_ids.append(case_id)
    if not case_ids or len(case_ids) != len(set(case_ids)):
        raise RuntimeError("frozen manifest case identities are invalid")
    return [f"eval:{label}:{case_id}" for case_id in case_ids]


def _validate_trace_snapshot(label, expected_trace_ids, evaluation, snapshot):
    evaluation_trace_ids = [
        str(case.get("trace_id") or "") for case in evaluation.get("cases") or []
    ]
    if (
        int(evaluation.get("total_cases") or 0) != len(expected_trace_ids)
        or Counter(evaluation_trace_ids) != Counter(expected_trace_ids)
    ):
        raise RuntimeError(f"{label} eval artifact does not match frozen manifest")
    query_count = int((snapshot.get("system_metrics") or {}).get("query_count") or 0)
    observed_range = snapshot.get("observed_range") or {}
    if (
        query_count != len(expected_trace_ids)
        or not observed_range.get("first")
        or not observed_range.get("last")
    ):
        raise RuntimeError(f"trace snapshot does not cover every {label} case")
    for field, message in (
        ("parse_errors", "parse errors"),
        ("error_event_count", "error events"),
        ("retry_event_count", "retry events"),
    ):
        if int(snapshot.get(field) or 0):
            raise RuntimeError(f"trace snapshot contains {message} for {label}")


def _read_appended_trace(trace, start_offset, label):
    events = []
    with trace.open("rb") as trace_file:
        trace_file.seek(start_offset)
        for raw in trace_file.read().decode("utf-8").splitlines():
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"appended trace contains invalid JSON for {label}"
                ) from exc
    return events


def _validate_appended_trace(label, expected_trace_ids, events, snapshot, enabled):
    observed_trace_ids = [
        str(event.get("trace_id") or "")
        for event in events
        if event.get("execution_context") == "evaluation"
        and event.get("event") == "rag_end"
    ]
    if Counter(observed_trace_ids) != Counter(expected_trace_ids):
        raise RuntimeError(f"trace identities do not match every {label} case")
    deterministic_splits = [
        event
        for event in events
        if event.get("event") == "query_decomposition"
        and event.get("deterministic_fallback") is True
    ]
    split_trace_ids = [str(event.get("trace_id") or "") for event in deterministic_splits]
    if any(count > 1 for count in Counter(split_trace_ids).values()):
        raise RuntimeError(
            f"appended trace contains duplicate deterministic split identities for {label}"
        )
    expected_trace_id_set = set(expected_trace_ids)
    fallback_count = int(snapshot.get("fallback_event_count") or 0)
    if (
        (deterministic_splits and not enabled)
        or not all(
            _is_strict_deterministic_local_split(event, expected_trace_id_set)
            for event in deterministic_splits
        )
        or fallback_count != len(deterministic_splits)
    ):
        raise RuntimeError(f"trace snapshot contains fallback events for {label}")


def _load_successful_gate(gate_path, gate_result):
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    passed = gate.get("passed")
    expected_exit = 0 if passed is True else 1 if passed is False else None
    if expected_exit is None or gate_result.returncode != expected_exit:
        raise RuntimeError("retrieval gate exit/artifact mismatch")
    return gate


def _invoke_evaluation(manifest, output, label, environment):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.eval.run_eval",
            "--manifest",
            str(manifest),
            "--output-dir",
            str(output),
            "--run-label",
            label,
        ],
        cwd=ROOT,
        env=environment,
        check=False,
    )


def _write_trace_snapshot(trace, started_at, completed_at, run_dir, environment):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.eval.rag_trace_snapshot",
            str(trace),
            "--start",
            started_at,
            "--end",
            completed_at,
            "--context",
            "evaluation",
            "--json-output",
            str(run_dir / "trace.json"),
            "--markdown-output",
            str(run_dir / "trace.md"),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
    )


def _run(
    label,
    manifest,
    output,
    trace,
    *,
    enabled,
    provider_sha,
    governance_sha,
    collection,
    fixture_batch,
    provider_environment=None,
    started_at=None,
):
    expected_trace_ids = _manifest_trace_ids(manifest, label)
    run_dir = output / label
    if run_dir.exists():
        raise ValueError(f"refusing to reuse run directory: {run_dir}")
    environment = build_evaluation_environment(
        enabled=enabled,
        collection=collection,
        fixture_batch=fixture_batch,
    )
    environment.update(provider_environment or {})
    environment.update({
        "RAG_EVAL_PROVIDER_CONFIGURATION_SHA256": provider_sha,
        "RAG_EVAL_GOVERNANCE_SCOPE_SHA256": governance_sha,
        "RAG_EVAL_CONCURRENCY": "1",
        "RAG_TRACE_LOG_FILE": str(trace),
    })
    trace_start_offset = trace.stat().st_size
    started_at = started_at or _utc_now()
    result = _invoke_evaluation(manifest, output, label, environment)
    if result.returncode not in (0, 2):
        raise RuntimeError(f"{label} evaluation exited {result.returncode}")
    completed_at = _utc_now()
    if not (run_dir / "eval.json").exists():
        raise RuntimeError(
            f"{label} failed before writing eval artifacts (exit {result.returncode})"
        )
    snapshot = _write_trace_snapshot(trace, started_at, completed_at, run_dir, environment)
    if snapshot.returncode:
        raise RuntimeError(f"trace snapshot failed for {label}")
    evaluation = json.loads((run_dir / "eval.json").read_text(encoding="utf-8"))
    trace_snapshot = json.loads((run_dir / "trace.json").read_text(encoding="utf-8"))
    _validate_trace_snapshot(label, expected_trace_ids, evaluation, trace_snapshot)
    events = _read_appended_trace(trace, trace_start_offset, label)
    _validate_appended_trace(label, expected_trace_ids, events, trace_snapshot, enabled)
    return {"started_at": started_at, "completed_at": completed_at, "runner_exit": result.returncode}


def _validate_rollback_evidence(path: Path, git_sha: str) -> dict:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if (
        evidence.get("schema") != "rollback-test-evidence-v1"
        or evidence.get("passed") is not True
        or evidence.get("git_sha") != git_sha
        or set(evidence.get("flags") or [])
        != {"RAG_QUERY_DECOMPOSITION_ENABLED"}
    ):
        raise ValueError(
            "rollback evidence must pass for this commit and decomposition flag"
        )
    return _artifact_reference(path)


def validate_formal_pair_inputs(
    manifest,
    output,
    trace,
    *,
    provider_smoke_artifact,
    rollback_test_artifact=None,
    trace_must_exist: bool,
):
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before running live staging evaluation")
    manifest, output, trace = Path(manifest), Path(output), Path(trace)
    provider_smoke_artifact = Path(provider_smoke_artifact)
    rollback_test_artifact = (
        Path(rollback_test_artifact) if rollback_test_artifact else None
    )
    if not manifest.is_file():
        raise ValueError("manifest must be a file")
    if trace_must_exist:
        if not trace.is_file():
            raise ValueError("trace must be a file")
        if trace.stat().st_size != 0 or output.exists():
            raise ValueError(
                "decomposition rollout requires a fresh zero-byte trace "
                "and nonexistent output directory"
            )
    elif trace.exists():
        raise ValueError("pre-dispatch validation requires a nonexistent trace")
    if not trace_must_exist and output.exists():
        raise ValueError("decomposition rollout requires a nonexistent output directory")
    if len(_manifest_trace_ids(manifest, "baseline")) != 13:
        raise ValueError("decomposition rollout requires exactly 13 frozen Query cases")

    require_clean_worktree()
    git_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()
    from mech_chatbot.config.settings import load_settings
    settings = load_settings()
    provider_sha = provider_configuration_sha256_for_settings(settings)
    baseline_started_at = _utc_now()
    validate_provider_smoke_for_baseline(
        provider_smoke_artifact,
        expected_provider_sha256=provider_sha,
        baseline_started_at=baseline_started_at,
    )
    rollback = (
        _validate_rollback_evidence(rollback_test_artifact, git_sha)
        if rollback_test_artifact is not None
        else {}
    )
    return {
        "manifest": manifest,
        "output": output,
        "trace": trace,
        "provider_smoke_artifact": provider_smoke_artifact,
        "git_sha": git_sha,
        "manifest_sha": _sha(manifest),
        "provider_sha": provider_sha,
        "provider_environment": provider_environment_for_settings(settings),
        "governance_sha": governance_scope_sha256(manifest),
        "baseline_started_at": baseline_started_at,
        "rollback": rollback,
    }


def run_rollout(
    manifest,
    output,
    trace,
    *,
    provider_smoke_artifact,
    rollback_test_artifact=None,
    collection=FIXTURE_COLLECTION,
    fixture_batch=FIXTURE_BATCH,
):
    inputs = validate_formal_pair_inputs(
        manifest,
        output,
        trace,
        provider_smoke_artifact=provider_smoke_artifact,
        rollback_test_artifact=rollback_test_artifact,
        trace_must_exist=True,
    )
    manifest = inputs["manifest"]
    output = inputs["output"]
    output.mkdir(exist_ok=False)
    trace = inputs["trace"]
    provider_smoke_artifact = inputs["provider_smoke_artifact"]
    git_sha = inputs["git_sha"]
    manifest_sha = inputs["manifest_sha"]
    provider_sha = inputs["provider_sha"]
    provider_environment = inputs["provider_environment"]
    governance_sha = inputs["governance_sha"]
    baseline_started_at = inputs["baseline_started_at"]
    rollback = inputs["rollback"]
    baseline = _run(
        "baseline",
        manifest,
        output,
        trace,
        enabled=False,
        provider_sha=provider_sha,
        governance_sha=governance_sha,
        collection=collection,
        fixture_batch=fixture_batch,
        provider_environment=provider_environment,
        started_at=baseline_started_at,
    )
    require_clean_worktree()
    if _sha(manifest) != manifest_sha:
        raise RuntimeError("manifest changed after baseline")
    require_source_commit(git_sha)
    candidate = _run(
        "candidate",
        manifest,
        output,
        trace,
        enabled=True,
        provider_sha=provider_sha,
        governance_sha=governance_sha,
        collection=collection,
        fixture_batch=fixture_batch,
        provider_environment=provider_environment,
    )
    require_clean_worktree()
    if _sha(manifest) != manifest_sha:
        raise RuntimeError("manifest changed after candidate")
    require_source_commit(git_sha)
    baseline_preflight = json.loads(
        (output / "baseline" / "preflight.json").read_text(encoding="utf-8")
    )
    candidate_preflight = json.loads(
        (output / "candidate" / "preflight.json").read_text(encoding="utf-8")
    )
    fingerprint = baseline_preflight["fixture_fingerprint"]
    if fingerprint != candidate_preflight["fixture_fingerprint"]:
        raise RuntimeError("fixture snapshot changed between baseline and candidate")
    gate_path = output / "gate.json"
    gate_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.eval.retrieval_intelligence_gate",
            "query_decomposition",
            str(output / "baseline" / "eval.json"),
            str(output / "candidate" / "eval.json"),
            "--baseline-trace",
            str(output / "baseline" / "trace.json"),
            "--candidate-trace",
            str(output / "candidate" / "trace.json"),
            "--output",
            str(gate_path),
        ],
        cwd=ROOT,
        check=False,
    )
    context = {
        "git_sha": git_sha, "manifest_sha256": manifest_sha,
        "snapshot_fingerprint": fingerprint, "provider_configuration_sha256": provider_sha,
        "concurrency": 1, "governance_scope_sha256": governance_sha,
        "collection": collection,
    }
    production_collection = os.getenv(
        "RAG_PRODUCTION_QDRANT_COLLECTION",
        "TaiLieuKyThuat_v2",
    )
    touches_production = collection == production_collection
    pair = {
        "schema": "rollout-evidence-pair-v1",
        "source_commit": git_sha,
        "run_id": output.name,
        "stage": "query_decomposition",
        "evidence_type": "staging_evaluation",
        "provider_smoke": _artifact_reference(Path(provider_smoke_artifact)),
        "baseline": {
            **context,
            **_artifact_reference(output / "baseline" / "eval.json"),
            **_artifact_reference(output / "baseline" / "trace.json", prefix="trace"),
            **baseline,
        },
        "candidate": {
            **context,
            **_artifact_reference(output / "candidate" / "eval.json"),
            **_artifact_reference(output / "candidate" / "trace.json", prefix="trace"),
            **candidate,
        },
        "data_plane": {
            "production_collection": production_collection,
            "mutation_mode": "in_place" if touches_production else "staging",
        },
        "gate": _artifact_reference(gate_path),
        "rollback": {
            "flags": ["RAG_QUERY_DECOMPOSITION_ENABLED"],
            "defaults_disabled": True,
            **rollback,
        },
    }
    pair_path = output / "rollout_pair.json"
    pair_path.write_text(json.dumps(pair, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from mech_chatbot.evaluation.rollout_guardrails import evaluate_rollout_pair
    guardrail = evaluate_rollout_pair(pair)
    gate = _load_successful_gate(gate_path, gate_result)
    technical_guardrails_passed = all(
        passed
        for name, passed in guardrail["checks"].items()
        if not (
            touches_production
            and name == "production_collection_not_mutated"
        )
    )
    technical_passed = bool(gate["passed"]) and technical_guardrails_passed
    report = {
        "schema": "decomposition-rollout-run-v1",
        "git_sha": git_sha,
        "manifest_sha256": manifest_sha,
        "fixture_fingerprint": fingerprint,
        "baseline": baseline,
        "candidate": candidate,
        "gate_exit": gate_result.returncode,
        "passed": technical_passed,
        "technical_eligible": technical_passed,
        "production_eligible": False,
        "decision_status": "pending_human_review",
        "rollout_pair_sha256": _sha(pair_path),
        "guardrail_checks": guardrail["checks"],
    }
    (output / "run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trace", type=Path, default=ROOT / "logs" / "rag_trace.jsonl")
    parser.add_argument("--provider-smoke-artifact", type=Path, required=True)
    parser.add_argument("--rollback-test-artifact", type=Path)
    parser.add_argument("--collection", default=FIXTURE_COLLECTION)
    parser.add_argument("--fixture-batch", default=FIXTURE_BATCH)
    parser.add_argument("--validate-inputs-only", action="store_true")
    args = parser.parse_args()
    if args.validate_inputs_only:
        validate_formal_pair_inputs(
            args.manifest,
            args.output_dir,
            args.trace,
            provider_smoke_artifact=args.provider_smoke_artifact,
            rollback_test_artifact=args.rollback_test_artifact,
            trace_must_exist=False,
        )
        print(json.dumps({"status": "validated"}))
        return 0
    report = run_rollout(
        args.manifest,
        args.output_dir,
        args.trace,
        provider_smoke_artifact=args.provider_smoke_artifact,
        rollback_test_artifact=args.rollback_test_artifact,
        collection=args.collection,
        fixture_batch=args.fixture_batch,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
