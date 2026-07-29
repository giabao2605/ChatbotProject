"""Build a hash-bound RAG activation bundle for one immutable deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from mech_chatbot.governance.artifact_references import build_json_reference
from mech_chatbot.governance.review_governance import review_governance_status
from mech_chatbot.rag.feature_activation import (
    ACTIVATION_PROFILES,
    MILESTONE_FLAGS,
    VERSION_DEFAULTS,
    profile_environment,
    validate_controlled_demo_decision_ledger,
    validate_release_decision_ledger,
)


_LEDGER_SCHEMAS = {
    "controlled_demo": "controlled-demo-decision-ledger-v2",
    "default_rollout": "integrated-release-decisions-v1",
}


def build_activation_bundle(
    *, scope: str, profile: str, source_commit: str,
    decision_ledger: str | Path, output: str | Path,
    root: str | Path = ".", review_governance: str | Path | None = None,
    versions: dict | None = None, graph_fingerprint: str | None = None,
) -> tuple[dict, str]:
    project_root = Path(root)
    scope = str(scope or "").strip()
    if scope not in _LEDGER_SCHEMAS:
        raise ValueError("activation scope must be controlled_demo or default_rollout")
    if profile not in ACTIVATION_PROFILES:
        raise ValueError(f"unknown activation profile: {profile}")
    source_commit = str(source_commit or "").strip()
    if not source_commit:
        raise ValueError("source commit is required")
    resolved_versions = dict(VERSION_DEFAULTS)
    resolved_versions.update(dict(versions or {}))
    if set(resolved_versions) != set(VERSION_DEFAULTS) or not all(
        str(value or "").strip() for value in resolved_versions.values()
    ):
        raise ValueError("all feature versions must be present")
    if profile == "community_summaries" and not str(graph_fingerprint or "").strip():
        raise ValueError("community_summaries requires a graph fingerprint")
    ledger_path = Path(decision_ledger)
    if not ledger_path.is_absolute():
        ledger_path = project_root / ledger_path
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    review_mode = "multi_reviewer"
    governance_reference = None
    if review_governance is not None:
        governance_path = Path(review_governance)
        if not governance_path.is_absolute():
            governance_path = project_root / governance_path
        governance_value = json.loads(governance_path.read_text(encoding="utf-8"))
        governance = review_governance_status(
            governance_value, source_commit=source_commit, scope=scope,
        )
        if not governance.valid:
            raise ValueError(f"review governance is invalid: {governance.reason}")
        review_mode = governance.mode
        governance_reference = build_json_reference(
            governance_path, root=project_root,
            expected_schema="rag-review-governance-v1",
        )
    if scope == "default_rollout":
        if not validate_release_decision_ledger(
            ledger,
            root=project_root,
            source_commit=source_commit,
            expected_enabled=ACTIVATION_PROFILES[profile],
            review_mode=review_mode,
        ):
            raise ValueError(
                "default_rollout requires a verified release decision ledger"
            )
    else:
        active_milestones = {
            milestone
            for milestone, flags in MILESTONE_FLAGS.items()
            if set(flags) & set(ACTIVATION_PROFILES[profile])
        }
        if not validate_controlled_demo_decision_ledger(
            ledger,
            active_milestones=active_milestones,
            root=project_root,
            source_commit=source_commit,
            review_mode=review_mode,
        ):
            raise ValueError(
                "controlled_demo requires a verified controlled-demo decision ledger"
            )
        if profile != "all_off" and governance_reference is None:
            raise ValueError(
                "controlled_demo feature-on activation requires review governance"
            )
    bundle = {
        "schema": "rag-activation-bundle-v1",
        "scope": scope,
        "source_commit": source_commit,
        "activation_profile": profile,
        "feature_flags": {
            name: value == "true"
            for name, value in profile_environment(profile).items()
        },
        "versions": resolved_versions,
        "graph_fingerprint": str(graph_fingerprint or "").strip() or None,
        "decision_ledger": build_json_reference(
            ledger_path, root=project_root,
            expected_schema=_LEDGER_SCHEMAS[scope],
        ),
    }
    if governance_reference is not None:
        bundle["review_governance"] = governance_reference
    output_path = Path(output)
    if not output_path.is_absolute():
        output_path = project_root / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(bundle, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    output_path.write_bytes(raw)
    return bundle, hashlib.sha256(raw).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=tuple(_LEDGER_SCHEMAS), required=True)
    parser.add_argument("--profile", choices=tuple(ACTIVATION_PROFILES), required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--decision-ledger", type=Path, required=True)
    parser.add_argument("--review-governance", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--planner-version", default=VERSION_DEFAULTS["RAG_PLANNER_VERSION"])
    parser.add_argument("--late-index-version", default=VERSION_DEFAULTS["RAG_LATE_INDEX_VERSION"])
    parser.add_argument("--graph-serving-epoch", default=VERSION_DEFAULTS["RAG_GRAPH_SERVING_EPOCH"])
    parser.add_argument("--community-serving-epoch", default=VERSION_DEFAULTS["RAG_COMMUNITY_SERVING_EPOCH"])
    parser.add_argument("--graph-fingerprint")
    args = parser.parse_args(argv)
    bundle, digest = build_activation_bundle(
        scope=args.scope, profile=args.profile,
        source_commit=args.source_commit,
        decision_ledger=args.decision_ledger,
        review_governance=args.review_governance,
        output=args.output, root=args.root,
        versions={
            "RAG_PLANNER_VERSION": args.planner_version,
            "RAG_LATE_INDEX_VERSION": args.late_index_version,
            "RAG_GRAPH_SERVING_EPOCH": args.graph_serving_epoch,
            "RAG_COMMUNITY_SERVING_EPOCH": args.community_serving_epoch,
        },
        graph_fingerprint=args.graph_fingerprint,
    )
    print(json.dumps({
        "path": str(args.output), "sha256": digest,
        "profile": bundle["activation_profile"], "scope": bundle["scope"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
