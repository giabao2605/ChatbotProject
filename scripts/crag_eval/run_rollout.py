"""Run isolated baseline/candidate evaluations and the CRAG rollout gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.crag_eval.constants import FIXTURE_COLLECTION, LIVE_OPT_IN

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
) -> dict:
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
        "run_id": run_id,
        "stage": "crag",
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
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        text=True,
    ).strip()
    if status:
        raise RuntimeError(
            "CRAG rollout requires a clean tracked worktree so artifact git_sha "
            "identifies the code that actually ran"
        )


def build_evaluation_environment(*, enabled: bool, router_mode: str) -> dict[str, str]:
    """Build one controlled evaluation environment without mutating the caller.

    Offline mode bypasses both semantic prototypes and the provider-backed L2
    router. CRAG fixtures then use the router's safe technical fallback, so the
    comparison measures CRAG rather than router model or prototype latency.
    """
    if router_mode not in {"offline", "provider"}:
        raise ValueError(f"unsupported router mode: {router_mode}")
    env = os.environ.copy()
    env.update({
        "RAG_EXECUTION_CONTEXT": "evaluation",
        "RAG_CRAG_ENABLED": str(enabled).lower(),
        "RAG_CLAIM_REPAIR_ENABLED": str(enabled).lower(),
        "SEMANTIC_CACHE_ENABLED": "false",
        "STRICT_REALTIME_STREAMING": "false",
        "QDRANT_COLLECTION": FIXTURE_COLLECTION,
        "RAG_EVAL_ROUTER_MODE": router_mode,
    })
    if router_mode == "offline":
        env["LLM_ROUTER_ENABLED"] = "false"
        env["SEMANTIC_ROUTER_ENABLED"] = "false"
    return env


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
) -> dict:
    env = build_evaluation_environment(enabled=enabled, router_mode=router_mode)
    env.update({
        "RAG_EVAL_PROVIDER_CONFIGURATION_SHA256": provider_configuration_sha256,
        "RAG_EVAL_GOVERNANCE_SCOPE_SHA256": governance_scope_sha256_value,
        "RAG_EVAL_CONCURRENCY": "1",
    })
    started_at = _utc_now()
    eval_result = subprocess.run([
        sys.executable, "-m", "scripts.eval.run_eval",
        "--manifest", str(manifest), "--output-dir", str(output), "--run-label", label,
    ], cwd=ROOT, env=env, check=False)
    completed_at = _utc_now()
    run_dir = output / label
    if not (run_dir / "eval.json").exists():
        raise RuntimeError(f"{label} failed before writing eval artifacts (exit {eval_result.returncode})")
    snapshot_result = subprocess.run([
        sys.executable, "-m", "scripts.eval.rag_trace_snapshot", str(trace),
        "--start", started_at, "--end", completed_at, "--context", "evaluation",
        "--json-output", str(run_dir / "trace.json"),
        "--markdown-output", str(run_dir / "trace.md"),
    ], cwd=ROOT, env=env, check=False)
    if snapshot_result.returncode:
        raise RuntimeError(f"trace snapshot failed for {label}")
    return {"label": label, "started_at": started_at, "completed_at": completed_at, "runner_exit": eval_result.returncode}


def run_rollout(
    manifest: Path,
    output: Path,
    trace: Path,
    *,
    router_mode: str = "offline",
    rollback_test_artifact: Path | None = None,
) -> dict:
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before running live staging evaluation")
    if not manifest.is_file() or not trace.is_file():
        raise ValueError("manifest and trace files must exist")
    require_clean_worktree()
    for label in ("baseline", "candidate"):
        run_dir = output / label
        if run_dir.exists() and any(run_dir.iterdir()):
            raise ValueError(f"refusing to overwrite non-empty run directory: {run_dir}")
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest_sha = _sha(manifest)
    provider_config = {
        key: os.getenv(key)
        for key in ("GPT_MODEL_NAME", "OPENAI_BASE_URL", "MAX_CONCURRENT_RAG")
        if os.getenv(key)
    }
    provider_config_sha = hashlib.sha256(
        json.dumps(provider_config, sort_keys=True).encode("utf-8")
    ).hexdigest()
    governance_sha = governance_scope_sha256(manifest)
    baseline = _run(
        "baseline", manifest, output, trace, enabled=False, router_mode=router_mode,
        provider_configuration_sha256=provider_config_sha,
        governance_scope_sha256_value=governance_sha,
    )
    require_clean_worktree()
    if _sha(manifest) != manifest_sha:
        raise RuntimeError("manifest changed after baseline")
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != git_sha:
        raise RuntimeError("commit changed between baseline and candidate")
    candidate = _run(
        "candidate", manifest, output, trace, enabled=True, router_mode=router_mode,
        provider_configuration_sha256=provider_config_sha,
        governance_scope_sha256_value=governance_sha,
    )
    require_clean_worktree()
    if _sha(manifest) != manifest_sha:
        raise RuntimeError("manifest changed after candidate")
    baseline_preflight = json.loads((output / "baseline" / "preflight.json").read_text(encoding="utf-8"))
    candidate_preflight = json.loads((output / "candidate" / "preflight.json").read_text(encoding="utf-8"))
    if baseline_preflight["fixture_fingerprint"] != candidate_preflight["fixture_fingerprint"]:
        raise RuntimeError("fixture snapshot changed between baseline and candidate")
    gate_path = output / "gate.json"
    gate_result = subprocess.run([
        sys.executable, "-m", "scripts.eval.crag_rollout_gate",
        str(output / "baseline" / "eval.json"), str(output / "candidate" / "eval.json"),
        str(output / "baseline" / "trace.json"), str(output / "candidate" / "trace.json"),
        "--output", str(gate_path),
    ], cwd=ROOT, check=False)
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    pair = build_rollout_pair(
        run_id=output.name,
        git_sha=git_sha,
        manifest_sha256=manifest_sha,
        snapshot_fingerprint=baseline_preflight["fixture_fingerprint"],
        provider_configuration_sha256=provider_config_sha,
        governance_scope_sha256_value=governance_sha,
        baseline_evidence={
            **_artifact_reference(output / "baseline" / "eval.json"),
            **_artifact_reference(output / "baseline" / "trace.json", prefix="trace"),
            "started_at": baseline["started_at"],
            "completed_at": baseline["completed_at"],
        },
        candidate_evidence={
            **_artifact_reference(output / "candidate" / "eval.json"),
            **_artifact_reference(output / "candidate" / "trace.json", prefix="trace"),
            "started_at": candidate["started_at"],
            "completed_at": candidate["completed_at"],
        },
        gate_artifact=gate_path,
        rollback_test_artifact=rollback_test_artifact,
    )
    pair_path = output / "rollout_pair.json"
    pair_path.write_text(
        json.dumps(pair, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    from mech_chatbot.evaluation.rollout_guardrails import evaluate_rollout_pair
    pair_guardrail = evaluate_rollout_pair(pair)
    metadata = {
        "schema": "crag-rollout-run-v1", "git_sha": git_sha, "manifest_sha256": manifest_sha,
        "provider_configuration_sha256": provider_config_sha, "concurrency": 1,
        "fixture_fingerprint": baseline_preflight["fixture_fingerprint"],
        "router_mode": router_mode,
        "baseline": baseline, "candidate": candidate, "gate_exit": gate_result.returncode,
        "passed": bool(gate["passed"]),
        "rollout_pair_sha256": _sha(pair_path),
        "production_eligible": bool(pair_guardrail["production_eligible"]),
        "guardrail_checks": pair_guardrail["checks"],
    }
    (output / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trace", type=Path, default=ROOT / "logs" / "rag_trace.jsonl")
    parser.add_argument("--router-mode", choices=("offline", "provider"), default="offline")
    parser.add_argument("--rollback-test-artifact", type=Path)
    args = parser.parse_args()
    report = run_rollout(
        args.manifest,
        args.output_dir,
        args.trace,
        router_mode=args.router_mode,
        rollback_test_artifact=args.rollback_test_artifact,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
