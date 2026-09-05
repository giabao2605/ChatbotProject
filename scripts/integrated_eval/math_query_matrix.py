"""Offline-only draft for the three-row Math/Query isolation contract.

No evaluation, preflight, credential loading, or dispatch entrypoint exists here.
Source identity is informational until a clean candidate is frozen and reviewed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mech_chatbot.governance.feature_activation import FEATURE_FLAGS

ROWS = (
    ("math_only", "data/grounded_math_eval_v1/eval_manifest.jsonl", 16,
     "650af531b2b2f349c8fef85dc8a6681592c3d4af523f3060f66259cc469af0c3",
     frozenset({"RAG_GROUNDED_MATH_ENABLED"})),
    ("query_only", "data/decomposition_eval_v1/eval_manifest.jsonl", 13,
     "6976cbbe4c9500b7c0755c5944775e326106a780bb2910bfa71167787a1d0bf8",
     frozenset({"RAG_QUERY_DECOMPOSITION_ENABLED"})),
    ("math_query", "data/decomposition_eval_v1/math_query_interaction_manifest.jsonl", 3,
     "d21495e86faca22c455f745a7b9fd7f249643f31e76f36e086f8af4467b0932b",
     frozenset({"RAG_GROUNDED_MATH_ENABLED", "RAG_QUERY_DECOMPOSITION_ENABLED"})),
)


def build_draft(source_root: Path) -> dict:
    root = source_root.resolve()
    rows = []
    for name, relative, count, digest, enabled in ROWS:
        raw = (root / relative).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("matrix_manifest_drift")
        rows.append({
            "row": name, "case_count": count,
            "manifest": {"path": relative, "sha256": digest},
            "baseline_flags": {flag: False for flag in FEATURE_FLAGS},
            "candidate_flags": {flag: flag in enabled for flag in FEATURE_FLAGS},
        })
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, text=True).strip())
    return {
        "schema": "math-query-isolation-draft-v1", "status": "draft",
        "source_root": str(root), "observed_commit": commit, "source_dirty": dirty,
        "preparer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dispatch_authorized": False, "default_rollout_authorized": False,
        "execution_ready": False, "concurrency": 1, "provider_retries": 0,
        "replacement_requests": 0, "catch_up_requests": 0, "rows": rows,
        "pending": ["clean_source_freeze", "reviewed_runner", "runtime_identity",
                    "fresh_authorization", "preflight_rollback_smoke", "row_evidence"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source-root", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--source-root", type=Path, required=True)
    validate.add_argument("--draft", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        draft = build_draft(args.source_root)
        if args.command == "prepare":
            with args.output.open("x", encoding="utf-8") as output:
                output.write(json.dumps(draft, indent=2) + "\n")
        else:
            supplied = json.loads(args.draft.read_bytes())
            # Canonical JSON comparison is type-strict: false cannot become 0.
            if json.dumps(supplied, sort_keys=True) != json.dumps(draft, sort_keys=True):
                raise ValueError("matrix_draft_drift")
    except (OSError, ValueError, subprocess.CalledProcessError):
        print(json.dumps({"status": "rejected", "dispatch_authorized": False}))
        return 1
    print(json.dumps({"status": "validated_draft" if args.command == "validate" else "draft",
                      "dispatch_authorized": False, "execution_ready": False}))
    return 0


def build_arm_plan(source_root: Path, run_root: Path) -> dict:
    """Construct evaluator invocations without creating roots or executing them.

    Environment is a controlled overlay, not a copy of ambient credentials.
    A future authorized dispatcher must merge provider settings BEFORE this overlay.
    """
    from scripts.grounded_math_eval.constants import FIXTURE_COLLECTION as math_collection
    from scripts.crag_eval.constants import FIXTURE_COLLECTION as query_collection

    draft = build_draft(source_root)
    root, output = source_root.resolve(), run_root.resolve()
    if output.exists() or output.is_symlink():
        raise ValueError("matrix_run_root_not_fresh")
    arms = []
    for row in draft["rows"]:
        name = row["row"]
        collection = math_collection if name == "math_only" else query_collection
        for label in ("baseline", "candidate"):
            flags = row[label + "_flags"]
            environment = {
                **{key: str(value).lower() for key, value in flags.items()},
                "RAG_EXECUTION_CONTEXT": "evaluation", "RAG_ACTIVATION_SCOPE": "evaluation",
                "RAG_ACTIVATION_PROFILE": "selective" if any(flags.values()) else "all_off",
                "SEMANTIC_CACHE_ENABLED": "false", "STRICT_REALTIME_STREAMING": "false",
                "LLM_ROUTER_ENABLED": "false", "SEMANTIC_ROUTER_ENABLED": "false",
                "RAG_EVAL_ROUTER_MODE": "offline", "RAG_EVAL_CONCURRENCY": "1",
                "QDRANT_COLLECTION": collection, "RAG_EVAL_EXPECTED_COLLECTION": collection,
                "RAG_EVAL_PREFLIGHT_KIND": "grounded_math" if name == "math_only" else "decomposition",
                "RAG_TRACE_LOG_FILE": str(output / name / "rag-traces" / (label + ".jsonl")),
            }
            arms.append({"row": name, "label": label, "environment": environment,
                "command": [sys.executable, "-m", "scripts.eval.run_eval", "--manifest",
                    str(root / row["manifest"]["path"]), "--output-dir", str(output / name),
                    "--run-label", label, "--maximum-provider-retries", "0",
                    "--stop-on-provider-failure"]})
    return {"schema": "math-query-arm-plan-v1", "dispatch_authorized": False,
            "execution_ready": False, "draft": draft, "arms": arms}


if __name__ == "__main__":
    raise SystemExit(main())
