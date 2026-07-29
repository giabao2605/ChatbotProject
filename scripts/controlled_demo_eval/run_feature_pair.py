"""Run a main-collection controlled-demo baseline/candidate feature pair."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.controlled_demo_eval.milestones import (
    EXPECTED_GROUP_COUNTS,
    FEATURE_FLAGS,
    MILESTONES,
    PAIR_STAGES,
)
from scripts.crag_eval.run_rollout import (
    _artifact_reference,
    _sha,
    _utc_now,
    governance_scope_sha256,
)
from scripts.eval.provider_smoke import (
    provider_configuration_sha256_for_settings,
    provider_environment_for_settings,
    validate_provider_smoke_artifact,
    validate_provider_smoke_for_baseline,
)


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
CONTROLLED_DEMO_COLLECTION = "TaiLieuKyThuat_v2"

def build_feature_environment(
    stage: str,
    *,
    candidate: bool,
    collection: str,
) -> dict[str, str]:
    config = MILESTONES.get(stage)
    if config is None or not config.pair_enabled:
        raise ValueError(f"unsupported controlled-demo stage: {stage}")
    enabled = set(config.baseline_enabled)
    if candidate:
        enabled.update(config.candidate_additions)

    environment = os.environ.copy()
    environment.update({name: str(name in enabled).lower() for name in FEATURE_FLAGS})
    environment.update({
        "RAG_EXECUTION_CONTEXT": "evaluation",
        "QDRANT_COLLECTION": collection,
        "RAG_PRODUCTION_QDRANT_COLLECTION": collection,
        "RAG_EVAL_PREFLIGHT_KIND": "controlled_demo",
        "RAG_EVAL_ROUTER_MODE": "offline",
        "LLM_ROUTER_ENABLED": "false",
        "SEMANTIC_ROUTER_ENABLED": "false",
        "SEMANTIC_CACHE_ENABLED": "false",
        "STRICT_REALTIME_STREAMING": "false",
    })
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(SRC) + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    return environment


def validate_collection(collection: str) -> str:
    if collection != CONTROLLED_DEMO_COLLECTION:
        raise ValueError(
            f"controlled demo must use {CONTROLLED_DEMO_COLLECTION}; got {collection}"
        )
    return collection


def validate_readiness_artifacts(
    full_preflight_artifact: Path,
    provider_smoke_artifact: Path,
    *,
    expected_provider_sha256: str,
) -> dict:
    full_preflight_artifact = Path(full_preflight_artifact)
    provider_smoke_artifact = Path(provider_smoke_artifact)
    preflight = json.loads(full_preflight_artifact.read_text(encoding="utf-8"))
    validate_provider_smoke_artifact(
        provider_smoke_artifact,
        expected_provider_sha256=expected_provider_sha256,
    )
    preflight_valid = all((
        preflight.get("schema") == "controlled-demo-main-preflight-v1",
        preflight.get("passed") is True,
        preflight.get("collection") == CONTROLLED_DEMO_COLLECTION,
        int(preflight.get("checked_cases") or 0) == 44,
        not preflight.get("failures"),
        bool(preflight.get("fixture_fingerprint")),
    ))
    if not preflight_valid:
        raise ValueError("full controlled-demo preflight artifact is invalid")
    return {
        "snapshot_fingerprint": preflight["fixture_fingerprint"],
        "preflight": _artifact_reference(full_preflight_artifact),
        "provider_smoke": _artifact_reference(provider_smoke_artifact),
    }


def validate_manifest_inventory(
    inventory_artifact: Path,
    stage: str,
    manifest: Path,
) -> dict:
    inventory_artifact = Path(inventory_artifact)
    manifest = Path(manifest)
    inventory = json.loads(inventory_artifact.read_text(encoding="utf-8"))
    groups = inventory.get("groups") or {}
    actual_counts = {
        group: int((groups.get(group) or {}).get("case_count") or 0)
        for group in EXPECTED_GROUP_COUNTS
    }
    milestone = (inventory.get("milestones") or {}).get(stage) or {}
    config = MILESTONES.get(stage)
    manifest_sha256 = _sha(manifest)
    valid = all((
        inventory.get("schema") == "controlled-demo-manifest-inventory-v1",
        int(inventory.get("source_case_count") or 0) == 44,
        actual_counts == EXPECTED_GROUP_COUNTS,
        bool(inventory.get("source_manifests")),
        all(
            bool(item.get("path")) and bool(item.get("sha256"))
            for item in (inventory.get("source_manifests") or [])
        ),
        config is not None and config.pair_enabled,
        int(milestone.get("case_count") or 0)
        == sum(EXPECTED_GROUP_COUNTS[group] for group in config.groups),
        int(milestone.get("minimum_cases") or 0) == config.minimum_cases,
        milestone.get("sha256") == manifest_sha256,
        Path(str(milestone.get("path") or "")).resolve() == manifest.resolve(),
    ))
    if not valid:
        raise ValueError("manifest does not match controlled-demo inventory")
    return {
        "manifest_sha256": manifest_sha256,
        "inventory": _artifact_reference(inventory_artifact),
    }


def require_clean_worktree() -> None:
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        text=True,
    ).strip()
    if status:
        raise RuntimeError("controlled-demo pair requires a clean tracked worktree")


def _run_arm(
    stage: str,
    label: str,
    manifest: Path,
    output_dir: Path,
    trace_path: Path,
    *,
    collection: str,
    provider_sha256: str,
    governance_sha256: str,
    provider_environment: dict[str, str] | None = None,
) -> dict:
    environment = build_feature_environment(
        stage, candidate=label == "candidate", collection=collection,
    )
    environment.update(provider_environment or {})
    environment.update({
        "RAG_EVAL_PROVIDER_CONFIGURATION_SHA256": provider_sha256,
        "RAG_EVAL_GOVERNANCE_SCOPE_SHA256": governance_sha256,
        "RAG_EVAL_CONCURRENCY": "1",
    })
    started_at = _utc_now()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.eval.run_eval",
            "--manifest",
            str(manifest),
            "--output-dir",
            str(output_dir),
            "--run-label",
            label,
        ],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    completed_at = _utc_now()
    run_dir = output_dir / label
    eval_path = run_dir / "eval.json"
    if not eval_path.exists():
        raise RuntimeError(
            f"{stage} {label} stopped before eval artifact (exit {result.returncode})"
        )
    snapshot = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.eval.rag_trace_snapshot",
            str(trace_path),
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
    if snapshot.returncode:
        raise RuntimeError(f"{stage} {label} trace snapshot failed")
    return {
        "label": label,
        "started_at": started_at,
        "completed_at": completed_at,
        "runner_exit": result.returncode,
    }


def _run_gate(stage: str, output_dir: Path) -> tuple[Path, int]:
    gate_path = output_dir / "gate.json"
    baseline_eval = output_dir / "baseline" / "eval.json"
    candidate_eval = output_dir / "candidate" / "eval.json"
    baseline_trace = output_dir / "baseline" / "trace.json"
    candidate_trace = output_dir / "candidate" / "trace.json"
    if stage == "crag":
        command = [
            sys.executable,
            "-m",
            "scripts.eval.crag_rollout_gate",
            str(baseline_eval),
            str(candidate_eval),
            str(baseline_trace),
            str(candidate_trace),
            "--output",
            str(gate_path),
        ]
    else:
        command = [
            sys.executable,
            "-m",
            "scripts.eval.retrieval_intelligence_gate",
            stage,
            str(baseline_eval),
            str(candidate_eval),
            "--baseline-trace",
            str(baseline_trace),
            "--candidate-trace",
            str(candidate_trace),
            "--output",
            str(gate_path),
        ]
    result = subprocess.run(command, cwd=ROOT, check=False)
    if not gate_path.exists():
        raise RuntimeError(f"{stage} gate did not write an artifact")
    return gate_path, result.returncode


def run_feature_pair(
    stage: str,
    manifest: Path,
    output_dir: Path,
    trace_path: Path,
    *,
    collection: str,
    full_preflight_artifact: Path,
    provider_smoke_artifact: Path,
    manifest_inventory_artifact: Path,
) -> dict:
    if os.getenv("CONTROLLED_DEMO_LIVE_OPT_IN") != "1":
        raise RuntimeError("set CONTROLLED_DEMO_LIVE_OPT_IN=1 before live evaluation")
    alias_value = os.getenv("CONTROLLED_DEMO_FIXTURE_ALIASES")
    if not alias_value:
        raise RuntimeError("set CONTROLLED_DEMO_FIXTURE_ALIASES before live evaluation")
    alias_path = Path(alias_value)
    if not alias_path.is_file():
        raise ValueError("CONTROLLED_DEMO_FIXTURE_ALIASES must point to a file")
    config = MILESTONES.get(stage)
    if config is None or not config.pair_enabled:
        raise ValueError(f"unsupported controlled-demo stage: {stage}")
    validate_collection(collection)
    from mech_chatbot.config.settings import load_settings
    settings = load_settings()
    provider_sha256 = provider_configuration_sha256_for_settings(settings)
    provider_environment = provider_environment_for_settings(settings)
    readiness = validate_readiness_artifacts(
        full_preflight_artifact,
        provider_smoke_artifact,
        expected_provider_sha256=provider_sha256,
    )
    manifest = Path(manifest)
    output_dir = Path(output_dir)
    trace_path = Path(trace_path)
    if not manifest.is_file() or not trace_path.is_file():
        raise ValueError("manifest and trace files must exist")
    inventory_binding = validate_manifest_inventory(
        manifest_inventory_artifact, stage, manifest,
    )
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output directory: {output_dir}")
    require_clean_worktree()

    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
    ).strip()
    manifest_sha256 = inventory_binding["manifest_sha256"]
    governance_sha256 = governance_scope_sha256(manifest)

    validate_provider_smoke_for_baseline(
        provider_smoke_artifact,
        expected_provider_sha256=provider_sha256,
        baseline_started_at=_utc_now(),
    )
    baseline = _run_arm(
        stage, "baseline", manifest, output_dir, trace_path,
        collection=collection, provider_sha256=provider_sha256,
        governance_sha256=governance_sha256,
        provider_environment=provider_environment,
    )
    require_clean_worktree()
    if _sha(manifest) != manifest_sha256:
        raise RuntimeError("manifest changed after baseline")
    candidate = _run_arm(
        stage, "candidate", manifest, output_dir, trace_path,
        collection=collection, provider_sha256=provider_sha256,
        governance_sha256=governance_sha256,
        provider_environment=provider_environment,
    )
    require_clean_worktree()
    if _sha(manifest) != manifest_sha256:
        raise RuntimeError("manifest changed after candidate")
    current_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
    ).strip()
    if current_commit != source_commit:
        raise RuntimeError("commit changed between baseline and candidate")

    baseline_preflight = json.loads(
        (output_dir / "baseline" / "preflight.json").read_text(encoding="utf-8")
    )
    candidate_preflight = json.loads(
        (output_dir / "candidate" / "preflight.json").read_text(encoding="utf-8")
    )
    snapshot_fingerprint = baseline_preflight.get("fixture_fingerprint")
    if not snapshot_fingerprint or snapshot_fingerprint != candidate_preflight.get(
        "fixture_fingerprint"
    ):
        raise RuntimeError("corpus snapshot changed between baseline and candidate")
    if snapshot_fingerprint != readiness["snapshot_fingerprint"]:
        raise RuntimeError("corpus snapshot changed after full preflight")

    gate_path, gate_exit = _run_gate(stage, output_dir)
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    eval_report = json.loads(
        (output_dir / "candidate" / "eval.json").read_text(encoding="utf-8")
    )
    case_count = int(eval_report.get("total_cases") or 0)
    minimum_cases = config.minimum_cases
    minimum_met = case_count >= minimum_cases
    pair = {
        "schema": "controlled-demo-feature-pair-v1",
        "scope": "controlled_demo",
        "stage": stage,
        "source_commit": source_commit,
        "collection": collection,
        "manifest_sha256": manifest_sha256,
        "fixture_aliases_sha256": _sha(alias_path),
        "governance_scope_sha256": governance_sha256,
        "provider_configuration_sha256": provider_sha256,
        "snapshot_fingerprint": snapshot_fingerprint,
        "case_count": case_count,
        "minimum_cases": minimum_cases,
        "minimum_met": minimum_met,
        "case_count_ceiling": "accepted" if minimum_met else "inconclusive",
        "decision_status": "pending_human_review",
        "readiness": readiness,
        "manifest_inventory": inventory_binding["inventory"],
        "baseline": {
            **baseline,
            **_artifact_reference(output_dir / "baseline" / "eval.json", prefix="eval"),
            **_artifact_reference(
                output_dir / "baseline" / "preflight.json", prefix="preflight"
            ),
            **_artifact_reference(output_dir / "baseline" / "trace.json", prefix="trace"),
        },
        "candidate": {
            **candidate,
            **_artifact_reference(output_dir / "candidate" / "eval.json", prefix="eval"),
            **_artifact_reference(
                output_dir / "candidate" / "preflight.json", prefix="preflight"
            ),
            **_artifact_reference(output_dir / "candidate" / "trace.json", prefix="trace"),
        },
        "gate": {
            "passed": gate.get("passed") is True,
            "exit_code": gate_exit,
            **_artifact_reference(gate_path),
        },
        "data_plane": {"mode": "main_collection_read_only_evaluation"},
    }
    pair_path = output_dir / "pair.json"
    pair_path.write_text(
        json.dumps(pair, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return pair


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "stage", choices=PAIR_STAGES
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--collection", choices=(CONTROLLED_DEMO_COLLECTION,),
        default=CONTROLLED_DEMO_COLLECTION,
    )
    parser.add_argument("--full-preflight-artifact", type=Path, required=True)
    parser.add_argument("--provider-smoke-artifact", type=Path, required=True)
    parser.add_argument("--manifest-inventory-artifact", type=Path, required=True)
    parser.add_argument(
        "--trace", type=Path, default=ROOT / "logs" / "rag_trace.jsonl"
    )
    args = parser.parse_args(argv)
    report = run_feature_pair(
        args.stage,
        args.manifest,
        args.output_dir,
        args.trace,
        collection=args.collection,
        full_preflight_artifact=args.full_preflight_artifact,
        provider_smoke_artifact=args.provider_smoke_artifact,
        manifest_inventory_artifact=args.manifest_inventory_artifact,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
