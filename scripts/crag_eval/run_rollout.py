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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(label: str, manifest: Path, output: Path, trace: Path, *, enabled: bool) -> dict:
    env = os.environ.copy()
    env.update({
        "RAG_EXECUTION_CONTEXT": "evaluation",
        "RAG_CRAG_ENABLED": str(enabled).lower(),
        "RAG_CLAIM_REPAIR_ENABLED": str(enabled).lower(),
        "SEMANTIC_CACHE_ENABLED": "false",
        "STRICT_REALTIME_STREAMING": "false",
        "QDRANT_COLLECTION": FIXTURE_COLLECTION,
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


def run_rollout(manifest: Path, output: Path, trace: Path) -> dict:
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before running live staging evaluation")
    if not manifest.is_file() or not trace.is_file():
        raise ValueError("manifest and trace files must exist")
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
    baseline = _run("baseline", manifest, output, trace, enabled=False)
    if _sha(manifest) != manifest_sha:
        raise RuntimeError("manifest changed after baseline")
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != git_sha:
        raise RuntimeError("commit changed between baseline and candidate")
    candidate = _run("candidate", manifest, output, trace, enabled=True)
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
    metadata = {
        "schema": "crag-rollout-run-v1", "git_sha": git_sha, "manifest_sha256": manifest_sha,
        "provider_configuration_sha256": provider_config_sha, "concurrency": 1,
        "fixture_fingerprint": baseline_preflight["fixture_fingerprint"],
        "baseline": baseline, "candidate": candidate, "gate_exit": gate_result.returncode,
        "passed": bool(gate["passed"]),
    }
    (output / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trace", type=Path, default=ROOT / "logs" / "rag_trace.jsonl")
    args = parser.parse_args()
    report = run_rollout(args.manifest, args.output_dir, args.trace)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
