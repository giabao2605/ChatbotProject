"""Run isolated baseline/candidate evaluations and the CRAG rollout gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from scripts.crag_eval.constants import FIXTURE_COLLECTION, LIVE_OPT_IN
from scripts.eval.provider_smoke import (
    provider_configuration_sha256_for_settings,
    provider_environment_for_settings,
    validate_provider_smoke_for_arms,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
GOVERNANCE_FIELDS = (
    "user_department",
    "user_roles",
    "allowed_departments",
    "allowed_sites",
    "max_security_level",
    "expected_department",
    "expected_site",
    "expected_security_level",
    "expected_version_policy",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_reference(path: Path, *, prefix: str = "artifact") -> dict:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    schema = artifact.get("schema")
    if not schema:
        raise ValueError(f"artifact has no schema: {path}")
    return {
        f"{prefix}_path": str(path.resolve()),
        f"{prefix}_schema": schema,
        f"{prefix}_sha256": _sha(path),
    }


def governance_scope_sha256(manifest: Path) -> str:
    scopes = []
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        case = json.loads(raw)
        scopes.append({
            "id": case.get("id"),
            **{field: case.get(field) for field in GOVERNANCE_FIELDS},
        })
    scopes.sort(key=lambda value: str(value.get("id") or ""))
    return hashlib.sha256(
        json.dumps(scopes, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def build_rollout_pair(
    *,
    run_id: str,
    git_sha: str,
    manifest_sha256: str,
    snapshot_fingerprint: str,
    provider_configuration_sha256: str,
    governance_scope_sha256_value: str,
    baseline_evidence: dict,
    candidate_evidence: dict,
    gate_artifact: Path,
    rollback_test_artifact: Path | None = None,
    arm_order: str = "baseline-first",
) -> dict:
    if arm_order not in {"baseline-first", "candidate-first"}:
        raise ValueError(f"unsupported arm order: {arm_order}")
    rollback_tested = False
    if rollback_test_artifact is not None:
        evidence = json.loads(rollback_test_artifact.read_text(encoding="utf-8"))
        if (
            evidence.get("schema") != "rollback-test-evidence-v1"
            or evidence.get("git_sha") != git_sha
            or evidence.get("passed") is not True
            or set(evidence.get("flags") or [])
            != {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"}
        ):
            raise ValueError("rollback test evidence must pass for the rollout commit")
        rollback_tested = True
    gate_reference = _artifact_reference(gate_artifact)
    context = {
        "git_sha": git_sha,
        "manifest_sha256": manifest_sha256,
        "snapshot_fingerprint": snapshot_fingerprint,
        "provider_configuration_sha256": provider_configuration_sha256,
        "concurrency": 1,
        "governance_scope_sha256": governance_scope_sha256_value,
        "collection": FIXTURE_COLLECTION,
    }
    return {
        "schema": "rollout-evidence-pair-v1",
        "source_commit": git_sha,
        "run_id": run_id,
        "stage": "crag",
        "arm_order": arm_order,
        "evidence_type": "staging_evaluation",
        "baseline": {**context, **baseline_evidence},
        "candidate": {**context, **candidate_evidence},
        "data_plane": {
            "production_collection": os.getenv(
                "RAG_PRODUCTION_QDRANT_COLLECTION", "TaiLieuKyThuat_v2"
            ),
            "mutation_mode": "staging",
        },
        "gate": gate_reference,
        "rollback": {
            "flags": ["RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"],
            "defaults_disabled": True,
            **(
                _artifact_reference(rollback_test_artifact)
                if rollback_tested and rollback_test_artifact is not None
                else {}
            ),
        },
    }


def require_clean_worktree() -> None:
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        text=True,
    ).strip()
    if status:
        raise RuntimeError(
            "CRAG rollout requires a clean worktree so artifact git_sha "
            "identifies the code that actually ran"
        )


def require_source_commit(expected: str) -> None:
    current = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
    ).strip()
    if current != expected:
        raise RuntimeError("commit changed during rollout")


def build_evaluation_environment(*, enabled: bool, router_mode: str) -> dict[str, str]:
    """Build one controlled evaluation environment without mutating the caller.

    Offline mode bypasses both semantic prototypes and the provider-backed L2
    router. CRAG fixtures then use the router's safe technical fallback, so the
    comparison measures CRAG rather than router model or prototype latency.
    """
    if router_mode not in {"offline", "provider"}:
        raise ValueError(f"unsupported router mode: {router_mode}")
    env = {
        **os.environ,
        "RAG_EXECUTION_CONTEXT": "evaluation",
        "RAG_CRAG_ENABLED": str(enabled).lower(),
        "RAG_CLAIM_REPAIR_ENABLED": str(enabled).lower(),
        "SEMANTIC_CACHE_ENABLED": "false",
        "STRICT_REALTIME_STREAMING": "false",
        "QDRANT_COLLECTION": FIXTURE_COLLECTION,
        "RAG_EVAL_ROUTER_MODE": router_mode,
    }
    if router_mode == "offline":
        return {
            **env,
            "LLM_ROUTER_ENABLED": "false",
            "SEMANTIC_ROUTER_ENABLED": "false",
        }
    return env


def _manifest_trace_ids(
    manifest: Path,
    label: str,
    selected_case_id: str | None = None,
) -> list[str]:
    try:
        case_ids = tuple(
            str(json.loads(raw).get("id") or "")
            for raw in manifest.read_text(encoding="utf-8-sig").splitlines()
            if raw.strip()
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError("frozen manifest contains invalid JSON") from exc
    if any(not manifest_case_id for manifest_case_id in case_ids):
        raise RuntimeError("frozen manifest contains a case without an id")
    if not case_ids or len(case_ids) != len(set(case_ids)):
        raise RuntimeError("frozen manifest case identities are invalid")
    if selected_case_id is not None:
        if selected_case_id not in case_ids:
            raise RuntimeError("selected case is absent from frozen manifest")
        case_ids = (selected_case_id,)
    return [f"eval:{label}:{manifest_case_id}" for manifest_case_id in case_ids]


def _validate_arm_artifacts(
    label: str,
    manifest: Path,
    evaluation_path: Path,
    snapshot_path: Path,
    *,
    case_id: str | None = None,
) -> None:
    expected_trace_ids = _manifest_trace_ids(manifest, label, case_id)
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    observed = [
        str(case.get("trace_id") or "")
        for case in evaluation.get("cases") or []
    ]
    if (
        int(evaluation.get("total_cases") or 0) != len(expected_trace_ids)
        or Counter(observed) != Counter(expected_trace_ids)
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
    for field in ("parse_errors", "error_event_count", "retry_event_count"):
        if int(snapshot.get(field) or 0):
            raise RuntimeError(f"trace snapshot contains {field} for {label}")


def _read_appended_trace(trace: Path, start_offset: int, label: str) -> tuple[dict, ...]:
    events = ()
    with trace.open("rb") as trace_file:
        trace_file.seek(start_offset)
        for raw in trace_file.read().decode("utf-8").splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"appended trace contains invalid JSON for {label}"
                ) from exc
            if not isinstance(event, dict):
                raise RuntimeError(
                    f"appended trace contains invalid event for {label}"
                )
            events = (*events, event)
    return events


def _validate_appended_trace_identities(
    label: str,
    manifest: Path,
    events: tuple[dict, ...],
    *,
    case_id: str | None = None,
) -> None:
    observed = [
        str(event.get("trace_id") or "")
        for event in events
        if event.get("execution_context") == "evaluation"
        and event.get("event") == "rag_end"
    ]
    if Counter(observed) != Counter(_manifest_trace_ids(manifest, label, case_id)):
        raise RuntimeError(f"appended trace identities do not match {label} manifest")


def _load_successful_gate(gate_path: Path, gate_result) -> dict:
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    passed = gate.get("passed")
    expected_exit = 0 if passed is True else 1 if passed is False else None
    if expected_exit is None or gate_result.returncode != expected_exit:
        raise RuntimeError("CRAG gate exit/artifact mismatch")
    return gate


def _invoke_evaluation(manifest, output, label, case_id, environment):
    command = [
        sys.executable, "-m", "scripts.eval.run_eval",
        "--manifest", str(manifest), "--output-dir", str(output),
        "--run-label", label,
        *(("--case-id", case_id) if case_id else ()),
    ]
    return subprocess.run(command, cwd=ROOT, env=environment, check=False)


def _write_trace_snapshot(trace, started_at, completed_at, run_dir, environment):
    return subprocess.run([
        sys.executable, "-m", "scripts.eval.rag_trace_snapshot", str(trace),
        "--start", started_at, "--end", completed_at,
        "--context", "evaluation",
        "--json-output", str(run_dir / "trace.json"),
        "--markdown-output", str(run_dir / "trace.md"),
    ], cwd=ROOT, env=environment, check=False)


def _arm_environment(
    *, enabled, router_mode, provider_environment,
    provider_configuration_sha256, governance_scope_sha256_value, trace,
):
    return {
        **build_evaluation_environment(enabled=enabled, router_mode=router_mode),
        **(provider_environment or {}),
        "RAG_EVAL_PROVIDER_CONFIGURATION_SHA256": provider_configuration_sha256,
        "RAG_EVAL_GOVERNANCE_SCOPE_SHA256": governance_scope_sha256_value,
        "RAG_EVAL_CONCURRENCY": "1",
        "RAG_TRACE_LOG_FILE": str(trace),
    }


def _validate_completed_arm(
    *, label, manifest, run_dir, trace, trace_start_offset, case_id,
):
    _validate_arm_artifacts(
        label,
        manifest,
        run_dir / "eval.json",
        run_dir / "trace.json",
        case_id=case_id,
    )
    _validate_appended_trace_identities(
        label,
        manifest,
        _read_appended_trace(trace, trace_start_offset, label),
        case_id=case_id,
    )


def _require_evaluation_artifact(label, run_dir, eval_result):
    if eval_result.returncode not in (0, 2):
        raise RuntimeError(f"{label} evaluation exited {eval_result.returncode}")
    if not (run_dir / "eval.json").exists():
        raise RuntimeError(
            f"{label} failed before writing eval artifacts "
            f"(exit {eval_result.returncode})"
        )


def _run(
    label: str,
    manifest: Path,
    output: Path,
    trace: Path,
    *,
    enabled: bool,
    router_mode: str,
    provider_configuration_sha256: str,
    governance_scope_sha256_value: str,
    provider_environment: dict[str, str] | None = None,
    started_at: str | None = None,
    case_id: str | None = None,
) -> dict:
    run_dir = output / label
    if run_dir.exists():
        raise ValueError(f"refusing to reuse run directory: {run_dir}")
    env = _arm_environment(
        enabled=enabled,
        router_mode=router_mode,
        provider_environment=provider_environment,
        provider_configuration_sha256=provider_configuration_sha256,
        governance_scope_sha256_value=governance_scope_sha256_value,
        trace=trace,
    )
    trace_start_offset = trace.stat().st_size
    started_at = started_at or _utc_now()
    eval_result = _invoke_evaluation(manifest, output, label, case_id, env)
    completed_at = _utc_now()
    _require_evaluation_artifact(label, run_dir, eval_result)
    snapshot_result = _write_trace_snapshot(
        trace, started_at, completed_at, run_dir, env,
    )
    if snapshot_result.returncode:
        raise RuntimeError(f"trace snapshot failed for {label}")
    _validate_completed_arm(
        label=label,
        manifest=manifest,
        run_dir=run_dir,
        trace=trace,
        trace_start_offset=trace_start_offset,
        case_id=case_id,
    )
    return {
        "label": label,
        "started_at": started_at,
        "completed_at": completed_at,
        "runner_exit": eval_result.returncode,
    }


def _validate_rollout_paths(manifest, output, trace, arm_order):
    if not manifest.is_file() or not trace.is_file():
        raise ValueError("manifest and trace files must exist")
    if trace.stat().st_size != 0 or output.exists():
        raise ValueError(
            "CRAG rollout requires a fresh zero-byte trace and nonexistent output directory"
        )
    if arm_order not in {"baseline-first", "candidate-first"}:
        raise ValueError(f"unsupported arm order: {arm_order}")


def _run_arms(
    *, manifest, output, trace, provider_smoke_artifact, router_mode,
    arm_order, git_sha, manifest_sha, provider_config_sha,
    provider_environment, governance_sha,
):
    arm_specs = (
        (("baseline", False), ("candidate", True))
        if arm_order == "baseline-first"
        else (("candidate", True), ("baseline", False))
    )
    results = {}
    starts = ()
    for label, enabled in arm_specs:
        started_at = _utc_now()
        starts = (*starts, started_at)
        validate_provider_smoke_for_arms(
            provider_smoke_artifact,
            expected_provider_sha256=provider_config_sha,
            arm_started_at=starts,
        )
        result = _run(
            label, manifest, output, trace, enabled=enabled,
            router_mode=router_mode,
            provider_configuration_sha256=provider_config_sha,
            governance_scope_sha256_value=governance_sha,
            provider_environment=provider_environment,
            started_at=started_at,
        )
        results = {**results, label: result}
        require_clean_worktree()
        if _sha(manifest) != manifest_sha:
            raise RuntimeError(f"manifest changed after {label}")
        require_source_commit(git_sha)
    return results


def _fixture_fingerprint(output: Path) -> str:
    baseline = json.loads(
        (output / "baseline" / "preflight.json").read_text(encoding="utf-8")
    )
    candidate = json.loads(
        (output / "candidate" / "preflight.json").read_text(encoding="utf-8")
    )
    fingerprint = baseline["fixture_fingerprint"]
    if fingerprint != candidate["fixture_fingerprint"]:
        raise RuntimeError("fixture snapshot changed between baseline and candidate")
    return fingerprint


def _run_gate(output: Path):
    gate_path = output / "gate.json"
    result = subprocess.run([
        sys.executable, "-m", "scripts.eval.crag_rollout_gate",
        str(output / "baseline" / "eval.json"),
        str(output / "candidate" / "eval.json"),
        str(output / "baseline" / "trace.json"),
        str(output / "candidate" / "trace.json"),
        "--output", str(gate_path),
    ], cwd=ROOT, check=False)
    return gate_path, result, _load_successful_gate(gate_path, result)


def _arm_evidence(output: Path, label: str, result: dict) -> dict:
    return {
        **_artifact_reference(output / label / "eval.json"),
        **_artifact_reference(output / label / "trace.json", prefix="trace"),
        "started_at": result["started_at"],
        "completed_at": result["completed_at"],
    }


def _write_pair(
    *, output, git_sha, manifest_sha, fingerprint, provider_config_sha,
    governance_sha, arm_results, gate_path, rollback_test_artifact,
    arm_order, provider_smoke_artifact,
):
    pair = {
        **build_rollout_pair(
            run_id=output.name, git_sha=git_sha,
            manifest_sha256=manifest_sha,
            snapshot_fingerprint=fingerprint,
            provider_configuration_sha256=provider_config_sha,
            governance_scope_sha256_value=governance_sha,
            baseline_evidence=_arm_evidence(
                output, "baseline", arm_results["baseline"],
            ),
            candidate_evidence=_arm_evidence(
                output, "candidate", arm_results["candidate"],
            ),
            gate_artifact=gate_path,
            rollback_test_artifact=rollback_test_artifact,
            arm_order=arm_order,
        ),
        "provider_smoke": _artifact_reference(provider_smoke_artifact),
    }
    path = output / "rollout_pair.json"
    path.write_text(
        json.dumps(pair, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return pair, path


def _write_run_report(
    *, output, git_sha, manifest_sha, provider_config_sha, fingerprint,
    router_mode, arm_order, arm_results, gate_result, gate, pair_path,
    pair_guardrail,
):
    metadata = {
        "schema": "crag-rollout-run-v1",
        "git_sha": git_sha,
        "manifest_sha256": manifest_sha,
        "provider_configuration_sha256": provider_config_sha,
        "concurrency": 1,
        "fixture_fingerprint": fingerprint,
        "router_mode": router_mode,
        "arm_order": arm_order,
        "baseline": arm_results["baseline"],
        "candidate": arm_results["candidate"],
        "gate_exit": gate_result.returncode,
        "passed": bool(gate["passed"])
        and bool(pair_guardrail["production_eligible"]),
        "rollout_pair_sha256": _sha(pair_path),
        "production_eligible": bool(pair_guardrail["production_eligible"]),
        "guardrail_checks": pair_guardrail["checks"],
    }
    (output / "run.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def run_rollout(
    manifest: Path,
    output: Path,
    trace: Path,
    *,
    provider_smoke_artifact: Path,
    router_mode: str = "offline",
    rollback_test_artifact: Path | None = None,
    arm_order: str = "baseline-first",
) -> dict:
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before running live staging evaluation")
    _validate_rollout_paths(manifest, output, trace, arm_order)
    require_clean_worktree()
    git_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
    ).strip()
    manifest_sha = _sha(manifest)
    from mech_chatbot.config.settings import load_settings
    settings = load_settings()
    provider_config_sha = provider_configuration_sha256_for_settings(settings)
    provider_environment = provider_environment_for_settings(settings)
    governance_sha = governance_scope_sha256(manifest)
    arm_results = _run_arms(
        manifest=manifest, output=output, trace=trace,
        provider_smoke_artifact=provider_smoke_artifact,
        router_mode=router_mode, arm_order=arm_order, git_sha=git_sha,
        manifest_sha=manifest_sha, provider_config_sha=provider_config_sha,
        provider_environment=provider_environment, governance_sha=governance_sha,
    )
    fingerprint = _fixture_fingerprint(output)
    gate_path, gate_result, gate = _run_gate(output)
    pair, pair_path = _write_pair(
        output=output, git_sha=git_sha, manifest_sha=manifest_sha,
        fingerprint=fingerprint, provider_config_sha=provider_config_sha,
        governance_sha=governance_sha, arm_results=arm_results,
        gate_path=gate_path, rollback_test_artifact=rollback_test_artifact,
        arm_order=arm_order, provider_smoke_artifact=provider_smoke_artifact,
    )
    from mech_chatbot.evaluation.rollout_guardrails import evaluate_rollout_pair
    pair_guardrail = evaluate_rollout_pair(pair)
    return _write_run_report(
        output=output, git_sha=git_sha, manifest_sha=manifest_sha,
        provider_config_sha=provider_config_sha, fingerprint=fingerprint,
        router_mode=router_mode, arm_order=arm_order,
        arm_results=arm_results, gate_result=gate_result, gate=gate,
        pair_path=pair_path, pair_guardrail=pair_guardrail,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trace", type=Path, default=ROOT / "logs" / "rag_trace.jsonl")
    parser.add_argument("--provider-smoke-artifact", type=Path, required=True)
    parser.add_argument("--router-mode", choices=("offline", "provider"), default="offline")
    parser.add_argument(
        "--arm-order",
        choices=("baseline-first", "candidate-first"),
        default="baseline-first",
    )
    parser.add_argument("--rollback-test-artifact", type=Path)
    args = parser.parse_args()
    report = run_rollout(
        args.manifest,
        args.output_dir,
        args.trace,
        provider_smoke_artifact=args.provider_smoke_artifact,
        router_mode=args.router_mode,
        rollback_test_artifact=args.rollback_test_artifact,
        arm_order=args.arm_order,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
