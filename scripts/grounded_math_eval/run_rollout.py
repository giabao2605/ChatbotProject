"""Run an isolated grounded-math baseline/candidate pair and stage gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.crag_eval.run_rollout import (
    _artifact_reference, _sha, _utc_now, governance_scope_sha256,
)
from scripts.grounded_math_eval.constants import FIXTURE_COLLECTION, LIVE_OPT_IN

ROOT = Path(__file__).resolve().parents[2]


def require_clean_worktree() -> None:
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True,
    ).strip()
    if status:
        raise RuntimeError("grounded-math rollout requires a clean tracked worktree")


def build_evaluation_environment(*, enabled: bool, router_mode: str) -> dict[str, str]:
    if router_mode not in {"offline", "provider"}:
        raise ValueError(f"unsupported router mode: {router_mode}")
    environment = os.environ.copy()
    environment.update({
        "RAG_EXECUTION_CONTEXT": "evaluation",
        "RAG_CRAG_ENABLED": "true",
        "RAG_CLAIM_REPAIR_ENABLED": "true",
        "RAG_GROUNDED_MATH_ENABLED": str(enabled).lower(),
        "SEMANTIC_CACHE_ENABLED": "false",
        "STRICT_REALTIME_STREAMING": "false",
        "QDRANT_COLLECTION": FIXTURE_COLLECTION,
        "RAG_EVAL_PREFLIGHT_KIND": "grounded_math",
        "RAG_EVAL_ROUTER_MODE": router_mode,
    })
    if router_mode == "offline":
        environment["LLM_ROUTER_ENABLED"] = "false"
        environment["SEMANTIC_ROUTER_ENABLED"] = "false"
    return environment


def _run(label, manifest, output, trace, *, enabled, router_mode, provider_sha, governance_sha):
    environment = build_evaluation_environment(enabled=enabled, router_mode=router_mode)
    environment.update({
        "RAG_EVAL_PROVIDER_CONFIGURATION_SHA256": provider_sha,
        "RAG_EVAL_GOVERNANCE_SCOPE_SHA256": governance_sha,
        "RAG_EVAL_CONCURRENCY": "1",
    })
    started_at = _utc_now()
    result = subprocess.run([
        sys.executable, "-m", "scripts.eval.run_eval", "--manifest", str(manifest),
        "--output-dir", str(output), "--run-label", label,
    ], cwd=ROOT, env=environment, check=False)
    completed_at = _utc_now()
    run_dir = output / label
    if not (run_dir / "eval.json").exists():
        raise RuntimeError(f"{label} failed before writing eval artifacts (exit {result.returncode})")
    snapshot = subprocess.run([
        sys.executable, "-m", "scripts.eval.rag_trace_snapshot", str(trace),
        "--start", started_at, "--end", completed_at, "--context", "evaluation",
        "--json-output", str(run_dir / "trace.json"),
        "--markdown-output", str(run_dir / "trace.md"),
    ], cwd=ROOT, env=environment, check=False)
    if snapshot.returncode:
        raise RuntimeError(f"trace snapshot failed for {label}")
    return {"started_at": started_at, "completed_at": completed_at, "runner_exit": result.returncode}


def _rollback_reference(path: Path | None, git_sha: str) -> dict:
    if path is None:
        return {}
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if (
        evidence.get("schema") != "rollback-test-evidence-v1"
        or evidence.get("passed") is not True
        or evidence.get("git_sha") != git_sha
        or set(evidence.get("flags") or []) != {"RAG_GROUNDED_MATH_ENABLED"}
    ):
        raise ValueError("rollback evidence must pass for the rollout commit and grounded-math flag")
    return _artifact_reference(path)


def run_rollout(manifest, output, trace, *, router_mode="offline", rollback_test_artifact=None):
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before running live staging evaluation")
    if not Path(manifest).is_file() or not Path(trace).is_file():
        raise ValueError("manifest and trace files must exist")
    require_clean_worktree()
    for label in ("baseline", "candidate"):
        directory = Path(output) / label
        if directory.exists() and any(directory.iterdir()):
            raise ValueError(f"refusing to overwrite non-empty run directory: {directory}")
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest_sha = _sha(Path(manifest))
    provider_config = {
        key: os.getenv(key) for key in ("GPT_MODEL_NAME", "OPENAI_BASE_URL", "MAX_CONCURRENT_RAG")
        if os.getenv(key)
    }
    provider_sha = hashlib.sha256(json.dumps(provider_config, sort_keys=True).encode()).hexdigest()
    governance_sha = governance_scope_sha256(Path(manifest))
    baseline = _run("baseline", Path(manifest), Path(output), Path(trace), enabled=False,
                    router_mode=router_mode, provider_sha=provider_sha, governance_sha=governance_sha)
    require_clean_worktree()
    if _sha(Path(manifest)) != manifest_sha:
        raise RuntimeError("manifest changed after baseline")
    candidate = _run("candidate", Path(manifest), Path(output), Path(trace), enabled=True,
                     router_mode=router_mode, provider_sha=provider_sha, governance_sha=governance_sha)
    require_clean_worktree()
    if _sha(Path(manifest)) != manifest_sha:
        raise RuntimeError("manifest changed after candidate")
    baseline_preflight = json.loads((Path(output) / "baseline" / "preflight.json").read_text(encoding="utf-8"))
    candidate_preflight = json.loads((Path(output) / "candidate" / "preflight.json").read_text(encoding="utf-8"))
    if baseline_preflight["fixture_fingerprint"] != candidate_preflight["fixture_fingerprint"]:
        raise RuntimeError("fixture snapshot changed between baseline and candidate")
    gate_path = Path(output) / "gate.json"
    gate_result = subprocess.run([
        sys.executable, "-m", "scripts.eval.retrieval_intelligence_gate", "grounded_math",
        str(Path(output) / "baseline" / "eval.json"),
        str(Path(output) / "candidate" / "eval.json"),
        "--baseline-trace", str(Path(output) / "baseline" / "trace.json"),
        "--candidate-trace", str(Path(output) / "candidate" / "trace.json"),
        "--output", str(gate_path),
    ], cwd=ROOT, check=False)
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    context = {
        "git_sha": git_sha, "manifest_sha256": manifest_sha,
        "snapshot_fingerprint": baseline_preflight["fixture_fingerprint"],
        "provider_configuration_sha256": provider_sha, "concurrency": 1,
        "governance_scope_sha256": governance_sha, "collection": FIXTURE_COLLECTION,
    }
    rollback_reference = _rollback_reference(rollback_test_artifact, git_sha)
    pair = {
        "schema": "rollout-evidence-pair-v1", "run_id": Path(output).name,
        "stage": "grounded_math", "evidence_type": "staging_evaluation",
        "baseline": {
            **context, **_artifact_reference(Path(output) / "baseline" / "eval.json"),
            **_artifact_reference(Path(output) / "baseline" / "trace.json", prefix="trace"),
            **baseline,
        },
        "candidate": {
            **context, **_artifact_reference(Path(output) / "candidate" / "eval.json"),
            **_artifact_reference(Path(output) / "candidate" / "trace.json", prefix="trace"),
            **candidate,
        },
        "data_plane": {
            "production_collection": os.getenv("RAG_PRODUCTION_QDRANT_COLLECTION", "TaiLieuKyThuat_v2"),
            "mutation_mode": "staging",
        },
        "gate": _artifact_reference(gate_path),
        "rollback": {
            "flags": ["RAG_GROUNDED_MATH_ENABLED"], "defaults_disabled": True,
            **rollback_reference,
        },
    }
    pair_path = Path(output) / "rollout_pair.json"
    pair_path.write_text(json.dumps(pair, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from mech_chatbot.evaluation.rollout_guardrails import evaluate_rollout_pair
    guardrail = evaluate_rollout_pair(pair)
    report = {
        "schema": "grounded-math-rollout-run-v1", "git_sha": git_sha,
        "manifest_sha256": manifest_sha, "fixture_fingerprint": baseline_preflight["fixture_fingerprint"],
        "router_mode": router_mode, "baseline": baseline, "candidate": candidate,
        "gate_exit": gate_result.returncode, "passed": bool(gate["passed"]),
        "rollout_pair_sha256": _sha(pair_path),
        "production_eligible": bool(guardrail["production_eligible"]),
        "guardrail_checks": guardrail["checks"],
    }
    (Path(output) / "run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trace", type=Path, default=ROOT / "logs" / "rag_trace.jsonl")
    parser.add_argument("--router-mode", choices=("offline", "provider"), default="offline")
    parser.add_argument("--rollback-test-artifact", type=Path)
    args = parser.parse_args()
    report = run_rollout(args.manifest, args.output_dir, args.trace,
                         router_mode=args.router_mode,
                         rollback_test_artifact=args.rollback_test_artifact)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
